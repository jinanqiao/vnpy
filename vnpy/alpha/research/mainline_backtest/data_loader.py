"""从数据湖读取回测需要的数据，并做基础校验。

这个文件回答一个问题：回测的原材料从哪里来、可不可信。

五个入口函数：
    load_selection()           001 产出的调仓信号清单（回测唯一的选股依据）
    load_bars()                日线行情（后复权算收益 / 未复权算股数，两份 schema 相同）
    load_benchmark()           沪深300 基准收盘价
    load_execution_universe()  每天哪些股票真正可以交易
    next_trading_day()         信号日 → 执行日（次一交易日）的推算

外加一个数据质量工具 verify_adjusted_bars()：校验后复权数据与未复权数据
是否互相印证（下载脚本和测试都用它）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from .config import BacktestConfig

SELECTION_COLUMNS = ["rebalance_date", "vt_symbol", "industry", "score", "industry_rank"]
BAR_COLUMNS = ["datetime", "vt_symbol", "open", "high", "low", "close", "turnover"]

LAYERED_PATH_FALLBACKS: dict[str, str] = {
    "silver/benchmark_index_daily.parquet": "benchmark/index_daily.parquet",
    "gold/execution_universe.parquet": "universe/execution_universe.parquet",
    "silver/trading_calendar.parquet": "calendar/trading_dates.parquet",
}


def _read_parquet(path: Path, required_columns: list[str], hint: str = "") -> pl.DataFrame:
    """读取 parquet 并检查必需列，缺文件/缺列直接报错（带修复提示）。"""
    resolved_path = _resolve_data_path(path)
    if not resolved_path.exists():
        message = f"数据文件不存在: {path}"
        if hint:
            message += f"。{hint}"
        raise FileNotFoundError(message)

    frame = pl.read_parquet(resolved_path)
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{resolved_path} 缺少必需列: {missing}，实际列: {frame.columns}")
    return frame


def _resolve_data_path(path: Path) -> Path:
    if path.exists():
        return path
    path_text = path.as_posix()
    for layered, fallback in LAYERED_PATH_FALLBACKS.items():
        if path_text.endswith(layered):
            data_root = Path(path_text[: -len(layered)].rstrip("/"))
            fallback_path = data_root / fallback
            if fallback_path.exists():
                return fallback_path
    return path


def load_selection(config: BacktestConfig) -> pl.DataFrame:
    """读取 001 的入选清单，返回按 (调仓日, 行业, 名次) 排序的五列表。"""
    selection = _read_parquet(
        Path(config.selection_path),
        SELECTION_COLUMNS,
        hint="请先运行 001 的 scripts/run_mainline_signals.py 生成信号",
    )
    selection = selection.with_columns(pl.col("rebalance_date").cast(pl.Date))

    if config.start:
        selection = selection.filter(pl.col("rebalance_date") >= date.fromisoformat(config.start))
    if config.end:
        selection = selection.filter(pl.col("rebalance_date") <= date.fromisoformat(config.end))
    return selection.sort(["rebalance_date", "industry", "industry_rank"])


def load_bars(config: BacktestConfig, adjusted: bool) -> pl.DataFrame:
    """读取日线行情。adjusted=True 读后复权（算收益），False 读未复权（算整手股数）。"""
    if adjusted:
        path = Path(config.data_dir) / config.adjusted_bars_file
        hint = "请先运行 scripts/download_adjusted_bars.py 下载后复权行情"
    else:
        path = Path(config.data_dir) / config.unadjusted_bars_file
        hint = ""

    bars = _read_parquet(path, BAR_COLUMNS, hint=hint)
    return bars.with_columns(pl.col("datetime").cast(pl.Date)).sort(["vt_symbol", "datetime"])


def load_benchmark(config: BacktestConfig) -> pl.DataFrame:
    """读取基准指数日线，返回两列: datetime, benchmark_close（只保留 benchmark_symbol）。"""
    path = Path(config.data_dir) / config.benchmark_file
    frame = _read_parquet(path, ["datetime", "index_symbol", "close"])
    benchmark = (
        frame.filter(pl.col("index_symbol") == config.benchmark_symbol)
        .with_columns(pl.col("datetime").cast(pl.Date))
        .select("datetime", pl.col("close").alias("benchmark_close"))
        .sort("datetime")
    )
    if benchmark.is_empty():
        raise ValueError(f"{path} 中找不到基准指数 {config.benchmark_symbol}")
    return benchmark


def load_execution_universe(config: BacktestConfig) -> pl.DataFrame:
    """读取逐日可交易名单，返回列: datetime, vt_symbol, in_execution。"""
    path = Path(config.data_dir) / config.execution_universe_file
    universe = _read_parquet(path, ["datetime", "vt_symbol", "in_execution"])
    return universe.with_columns(pl.col("datetime").cast(pl.Date))


def load_trading_days(config: BacktestConfig) -> list[date]:
    """读取上交所交易日列表（升序），用于执行日推算与逐日估值循环。"""
    path = Path(config.data_dir) / config.trading_dates_file
    calendar = _read_parquet(path, ["market", "trade_date"])
    return (
        calendar.filter(pl.col("market") == "SH")
        .with_columns(pl.col("trade_date").cast(pl.Date))
        .get_column("trade_date")
        .unique()
        .sort()
        .to_list()
    )


def next_trading_day(trading_days: list[date], signal_date: date) -> date | None:
    """信号日的次一交易日 = 执行日（严格大于信号日的第一个交易日）。

    信号在月末收盘后才算出来，最早能行动的时点就是下一个交易日的开盘，
    这是"无未来信息"的最紧执行假设。日历走到头返回 None（该期无法执行）。
    """
    for day in trading_days:
        if day > signal_date:
            return day
    return None


def build_periods(
    selection: pl.DataFrame, trading_days: list[date], adjusted_bars: pl.DataFrame
) -> tuple[list[dict], list[dict]]:
    """把信号清单整理成调仓期列表：{signal_date, exec_day, stocks:[(代码, 行业)]}。

    信号日之后找不到可执行行情的期直接跳过并记入质量日志（返回值第二项）。
    """
    last_bar_day = adjusted_bars.get_column("datetime").max()
    periods: list[dict] = []
    quality_logs: list[dict] = []

    for signal_date in selection.get_column("rebalance_date").unique().sort().to_list():
        exec_day = next_trading_day(trading_days, signal_date)
        if exec_day is None or exec_day > last_bar_day:
            quality_logs.append(
                {
                    "type": "period_skipped",
                    "signal_date": str(signal_date),
                    "detail": "信号日之后没有可用的执行日行情，跳过该期",
                }
            )
            continue
        rows = selection.filter(pl.col("rebalance_date") == signal_date)
        stocks = [(row["vt_symbol"], row["industry"]) for row in rows.iter_rows(named=True)]
        periods.append({"signal_date": signal_date, "exec_day": exec_day, "stocks": stocks})
    return periods, quality_logs


def verify_adjusted_bars(adjusted: pl.DataFrame, unadjusted: pl.DataFrame) -> dict:
    """校验后复权与未复权行情互相印证，返回三项校验结果的字典。

    三项校验（依据 research R8）：
        1. 覆盖率：后复权的 (股票, 日期) 组合应覆盖未复权的 99% 以上；
        2. 成交额一致：复权只改价格不改成交金额，同一天的 turnover 应几乎相等；
        3. 复权比例阶梯性：后复权价/未复权价的比例只会在除权日上台阶（只升不降）。
    任何一项不通过，下载脚本都不会把数据写入数据湖。
    """
    joined = adjusted.join(unadjusted, on=["vt_symbol", "datetime"], how="inner", suffix="_raw")

    coverage = joined.height / max(unadjusted.height, 1)

    turnover_diff = (
        (pl.col("turnover") - pl.col("turnover_raw")).abs()
        / pl.col("turnover_raw").abs().clip(lower_bound=1.0)
    )
    turnover_bad = joined.filter(turnover_diff > 0.001).height

    # 复权比例 = 后复权收盘 / 未复权收盘；后复权特性：比例随时间只升不降
    ratio = joined.with_columns((pl.col("close") / pl.col("close_raw")).alias("adj_ratio"))
    ratio_drop = ratio.filter(
        (pl.col("adj_ratio") / pl.col("adj_ratio").shift(1).over("vt_symbol") - 1) < -0.001
    ).height

    passed = coverage >= 0.99 and turnover_bad == 0 and ratio_drop == 0
    return {
        "passed": passed,
        "coverage": round(coverage, 6),
        "coverage_ok": coverage >= 0.99,
        "turnover_mismatch_rows": turnover_bad,
        "turnover_ok": turnover_bad == 0,
        "ratio_drop_rows": ratio_drop,
        "ratio_ok": ratio_drop == 0,
    }
