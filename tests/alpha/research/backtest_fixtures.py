"""mainline_backtest 测试共用的合成数据工具：走势可控，期望结果可手算。

核心思路：测试直接给定每只股票的"后复权收盘价序列"和"复权因子序列"，
未复权价 = 后复权价 / 复权因子。因子恒为 1 代表从不分红（两套价格相同）；
因子在某天从 1 跳到 2 代表当天除权（未复权价腰斩、后复权价连续）。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl


def make_business_dates(start: date, count: int) -> list[date]:
    """从 start 开始生成 count 个工作日（跳过周六周日）。"""
    dates: list[date] = []
    current = start
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def make_dual_bars(
    vt_symbol: str,
    dates: list[date],
    adj_closes: list[float],
    adj_factors: list[float] | None = None,
    turnover: float = 1_000_000.0,
    one_word_days: dict[date, str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """生成一只股票的 (后复权, 未复权) 两套日线。

    参数:
        adj_closes    后复权收盘价序列（与 dates 等长）
        adj_factors   复权因子序列（未复权价 = 后复权价 / 因子），默认全 1
        one_word_days 一字板日期 -> "up"/"down"：当天 开=高=低=收，
                      "up" 表示一字涨停（收盘比前一天高）、"down" 一字跌停
    开盘价约定：默认开盘 = 前一天收盘（跳空由测试通过收盘序列控制）；
    一字板日开=高=低=收=当日收盘。
    """
    factors = adj_factors or [1.0] * len(dates)
    one_word_days = one_word_days or {}
    assert len(adj_closes) == len(dates) and len(factors) == len(dates)

    rows_adj: list[dict] = []
    rows_raw: list[dict] = []
    for index, day in enumerate(dates):
        close_adj = adj_closes[index]
        open_adj = adj_closes[index - 1] if index > 0 else close_adj
        if day in one_word_days:
            open_adj = close_adj

        high_adj = max(open_adj, close_adj)
        low_adj = min(open_adj, close_adj)
        if day in one_word_days:
            high_adj = low_adj = open_adj = close_adj

        factor = factors[index]
        rows_adj.append(
            {
                "datetime": day, "vt_symbol": vt_symbol,
                "open": open_adj, "high": high_adj, "low": low_adj, "close": close_adj,
                "turnover": turnover,
            }
        )
        rows_raw.append(
            {
                "datetime": day, "vt_symbol": vt_symbol,
                "open": open_adj / factor, "high": high_adj / factor,
                "low": low_adj / factor, "close": close_adj / factor,
                "turnover": turnover,
            }
        )
    return pl.DataFrame(rows_adj), pl.DataFrame(rows_raw)


def make_flat_dual_bars(
    vt_symbol: str, dates: list[date], price: float = 10.0
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """价格恒定不动的股票（净值贡献恒为 0，方便隔离验证其他逻辑）。"""
    return make_dual_bars(vt_symbol, dates, [price] * len(dates))


def make_selection(rows: list[tuple[date, str, str]]) -> pl.DataFrame:
    """入选清单：[(调仓日, 股票, 行业), ...]，score/名次自动按顺序编。"""
    records = []
    rank_counter: dict[tuple[date, str], int] = {}
    for rebalance_date, vt_symbol, industry in rows:
        key = (rebalance_date, industry)
        rank_counter[key] = rank_counter.get(key, 0) + 1
        records.append(
            {
                "rebalance_date": rebalance_date,
                "vt_symbol": vt_symbol,
                "industry": industry,
                "score": 1.0 - 0.01 * rank_counter[key],
                "industry_rank": rank_counter[key],
            }
        )
    return pl.DataFrame(records)


def make_execution_universe(
    dates: list[date],
    symbols: list[str],
    blocked: set[tuple[date, str]] | None = None,
) -> pl.DataFrame:
    """可交易名单：默认全部可交易，blocked 里的 (日期, 代码) 记为不可交易。"""
    blocked = blocked or set()
    rows = [
        {"datetime": d, "vt_symbol": s, "in_execution": (d, s) not in blocked}
        for d in dates
        for s in symbols
    ]
    return pl.DataFrame(rows)


def make_step_closes(steps: list[tuple[int, float]]) -> list[float]:
    """分段常数价格序列：[(天数, 价格), ...] 展开成收盘价列表。

    用于构造可控的基准走势，例如 [(20, 3000), (20, 1000), (20, 3000)]
    = 高位 20 天 → 跌破均线 20 天 → 收复 20 天（择时场景三段式）。
    """
    closes: list[float] = []
    for days, price in steps:
        closes.extend([price] * days)
    return closes


def make_benchmark(dates: list[date], closes: list[float] | None = None) -> pl.DataFrame:
    """基准指数日线（默认恒定 3000 点）。"""
    closes = closes or [3000.0] * len(dates)
    return pl.DataFrame(
        {
            "datetime": dates,
            "index_symbol": ["000300.SH"] * len(dates),
            "close": closes,
        }
    )


def write_backtest_lake(
    data_dir: Path,
    adjusted_bars: pl.DataFrame,
    unadjusted_bars: pl.DataFrame,
    selection: pl.DataFrame,
    execution_universe: pl.DataFrame,
    benchmark: pl.DataFrame,
    trade_dates: list[date],
) -> Path:
    """按数据湖目录结构写全部输入文件，返回 selection.parquet 的路径。

    同时写新旧两套布局：新布局（silver/gold）是当前生产入口读取的规范路径，
    旧布局（normalized/benchmark/…）保留给仍直接按老路径读文件的历史测试。
    行情文件（后复权/未复权）在加载器里没有旧→新回退，必须显式写到 silver/。
    """
    (data_dir / "normalized").mkdir(parents=True)
    (data_dir / "benchmark").mkdir(parents=True)
    (data_dir / "universe").mkdir(parents=True)
    (data_dir / "calendar").mkdir(parents=True)
    (data_dir / "silver").mkdir(parents=True)

    # 新布局：生产入口默认读 silver/ 下的规范文件名
    adjusted_bars.write_parquet(data_dir / "silver" / "daily_bars_adjusted.parquet")
    unadjusted_bars.write_parquet(data_dir / "silver" / "daily_bars_raw_price.parquet")

    # 旧布局：保留给按 normalized/ 老路径直接读文件的历史测试
    adjusted_bars.write_parquet(data_dir / "normalized" / "daily_bars_all_a_adjusted.parquet")
    unadjusted_bars.write_parquet(data_dir / "normalized" / "daily_bars_all_a.parquet")
    benchmark.write_parquet(data_dir / "benchmark" / "index_daily.parquet")
    execution_universe.write_parquet(data_dir / "universe" / "execution_universe.parquet")
    pl.DataFrame(
        {"market": ["SH"] * len(trade_dates), "trade_date": trade_dates}
    ).write_parquet(data_dir / "calendar" / "trading_dates.parquet")

    selection_path = data_dir / "selection.parquet"
    selection.write_parquet(selection_path)
    return selection_path
