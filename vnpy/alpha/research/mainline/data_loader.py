"""从数据湖读取策略需要的四张表，并做基础校验。

这个文件回答一个问题：策略的原材料（行情、行业归属、可交易名单、交易日历）
从哪里来、长什么样、有没有质量问题。

四个入口函数：
    load_daily_bars()          日线行情（开高低收、成交额）
    load_industry_map()        每只股票属于哪个 GICS 一级行业
    load_execution_universe()  每天哪些股票真正可以交易
    build_rebalance_dates()    每个自然月的最后一个交易日（调仓日）

数据质量问题（缺行业归属、疑似除权跳空等）不会中断运行，
而是收集成 quality_logs 列表，最终写进产物目录的 data_quality.json。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from .config import MainlineConfig

# 行业成分文件里既有 GICS 一级行业，也有"上证A股"这类宽基板块；
# 只有对应前缀的才是真正的行业分类。gics1 = 11 个粗行业，sw1 = 31 个申万一级行业。
GICS1_PREFIX = "GICS1"
SW1_PREFIX = "SW1"

LAYERED_PATH_FALLBACKS: dict[str, str] = {
    "silver/sector_members_snapshot.parquet": "sector/sector_members.parquet",
    "silver/sw1_members_snapshot.parquet": "sector/sw1_members.parquet",
    "gold/execution_universe.parquet": "universe/execution_universe.parquet",
    "silver/trading_calendar.parquet": "calendar/trading_dates.parquet",
}


def _read_parquet(config: MainlineConfig, relative_path: str, required_columns: list[str]) -> pl.DataFrame:
    """读取一个 parquet 文件并检查必需列是否齐全，缺列直接报错。"""
    path = _resolve_data_path(config.data_dir, relative_path)
    if not path.exists():
        raise FileNotFoundError(f"数据文件不存在: {path}")

    frame = pl.read_parquet(path)
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{path} 缺少必需列: {missing}，实际列: {frame.columns}")
    return frame


def _resolve_data_path(data_dir: str, relative_path: str) -> Path:
    path = Path(data_dir) / relative_path
    if path.exists():
        return path
    fallback = LAYERED_PATH_FALLBACKS.get(relative_path)
    if fallback:
        fallback_path = Path(data_dir) / fallback
        if fallback_path.exists():
            return fallback_path
    return path


def load_daily_bars(config: MainlineConfig, quality_logs: list[dict]) -> pl.DataFrame:
    """读取全 A 日线行情，返回列: datetime, vt_symbol, open, high, low, close, turnover。

    额外做一件事：侦测"疑似除权跳空"。数据湖的价格没有复权，
    分红除权那天价格会突然大跌（不是真跌），这里把单日跌幅超过阈值的
    样本记进质量日志，供事后核查（不修改数据本身）。
    """
    bars = _read_parquet(
        config,
        config.daily_bars_file,
        required_columns=["datetime", "vt_symbol", "open", "high", "low", "close", "turnover"],
    )
    bars = bars.with_columns(pl.col("datetime").cast(pl.Date))
    bars = bars.sort(["vt_symbol", "datetime"])

    if config.universe == "leaders":
        bars = bars.filter(pl.col("vt_symbol").is_in(load_leaders_symbols(config)))
    elif config.universe != "all_a":
        raise ValueError(f"不认识的股票池: {config.universe}，只支持 all_a / leaders")

    # 疑似除权侦测：同一只股票相邻两天收盘价的跌幅超过阈值
    daily_return = pl.col("close") / pl.col("close").shift(1).over("vt_symbol") - 1
    suspects = bars.filter(daily_return < config.suspect_drop_threshold)
    for row in suspects.select(["datetime", "vt_symbol", "close"]).head(500).iter_rows(named=True):
        quality_logs.append(
            {
                "type": "suspect_ex_dividend_drop",
                "datetime": str(row["datetime"]),
                "vt_symbol": row["vt_symbol"],
                "detail": f"单日跌幅超过 {config.suspect_drop_threshold:.0%}，疑似未复权的除权跳空",
            }
        )
    return bars


def load_leaders_symbols(config: MainlineConfig) -> list[str]:
    """读取龙头池成分：沪深300 + 中证500 成分股代码（去重排序）。"""
    hs300 = _read_parquet(config, config.hs300_members_file, required_columns=["vt_symbol"])
    zz500 = _read_parquet(config, config.zz500_members_file, required_columns=["vt_symbol"])
    symbols = set(hs300["vt_symbol"].to_list()) | set(zz500["vt_symbol"].to_list())
    return sorted(symbols)


def load_industry_map(config: MainlineConfig, quality_logs: list[dict]) -> pl.DataFrame:
    """读取行业成分，返回两列: vt_symbol, industry（每只股票只属于一个行业）。

    config.industry_source 决定口径：gics1 读 sector_members_file（11 个行业），
    sw1 读 sw1_members_file（31 个申万一级行业，更细）。
    行业成分文件是当前时点的快照（不是历史逐日成分），这是已知局限，
    会写进产物报告。若一只股票出现在多个行业里（数据异常），
    取行业名字典序第一个，并记质量日志。
    """
    if config.industry_source == "sw1":
        members_file, prefix = config.sw1_members_file, SW1_PREFIX
    else:
        members_file, prefix = config.sector_members_file, GICS1_PREFIX

    members = _read_parquet(config, members_file, required_columns=["sector", "vt_symbol"])
    industries = members.filter(pl.col("sector").str.starts_with(prefix))
    industries = industries.select(pl.col("vt_symbol"), pl.col("sector").alias("industry")).unique()

    duplicated = (
        industries.group_by("vt_symbol").len().filter(pl.col("len") > 1).get_column("vt_symbol").to_list()
    )
    for symbol in sorted(duplicated)[:200]:
        quality_logs.append(
            {
                "type": "multiple_industry_membership",
                "vt_symbol": symbol,
                "detail": f"同时出现在多个 {prefix} 行业，取字典序第一个",
            }
        )

    industry_map = industries.sort(["vt_symbol", "industry"]).group_by("vt_symbol", maintain_order=True).first()
    return industry_map


def load_execution_universe(config: MainlineConfig) -> pl.DataFrame:
    """读取逐日可交易名单，返回列: datetime, vt_symbol, in_execution。

    in_execution 为 True 表示当天非停牌、非 ST、非一字涨跌停、流动性达标，
    是"调仓日真的买得进"的判据（由数据湖的 PIT universe 流水线预先算好）。
    """
    universe = _read_parquet(
        config,
        config.execution_universe_file,
        required_columns=["datetime", "vt_symbol", "in_execution"],
    )
    return universe.with_columns(pl.col("datetime").cast(pl.Date))


def build_rebalance_dates(config: MainlineConfig, bars: pl.DataFrame) -> list[date]:
    """生成调仓日列表：每个周期（周或月）的最后一个交易日（以上交所日历为准）。

    config.rebalance_freq = "weekly" 时按 ISO 周分组（周五或该周最后一个交易日），
    "monthly" 时按自然月分组。只保留日线数据实际覆盖、且满足 config.start/end
    范围的调仓日。注意：数据起始后的第一年内，因子窗口（最长 252 日）不足的
    调仓日也会返回，由下游按"窗口不足"规则处理个股，不在这里砍掉。
    """
    calendar = _read_parquet(config, config.trading_dates_file, required_columns=["market", "trade_date"])
    trade_dates = (
        calendar.filter(pl.col("market") == "SH")
        .with_columns(pl.col("trade_date").cast(pl.Date))
        .get_column("trade_date")
    )

    bars_min = bars.get_column("datetime").min()
    bars_max = bars.get_column("datetime").max()

    # 周期键：weekly 用 ISO 年-周（跨年周不会被拆错），monthly 用 年-月
    if config.rebalance_freq == "weekly":
        period_key = (
            pl.col("trade_date").dt.iso_year().cast(pl.Utf8)
            + "-W" + pl.col("trade_date").dt.week().cast(pl.Utf8).str.zfill(2)
        )
    else:
        period_key = pl.col("trade_date").dt.strftime("%Y-%m")

    # 在完整日历上找每个周期的最后一个交易日，再过滤到日线数据覆盖的范围。
    # 日历的最后一个周期要丢掉：日历更新到周期中间时，"该周期最后一个交易日"
    # 其实还没到，只有日历里出现了更晚周期的日期，才能确认这个周期已经收官。
    grouped = (
        pl.DataFrame({"trade_date": trade_dates})
        .with_columns(period_key.alias("period"))
        .group_by("period")
        .agg(pl.col("trade_date").max().alias("rebalance_date"))
        .sort("rebalance_date")
    )
    calendar_last_period = grouped.get_column("period")[-1]
    rebalance_dates = (
        grouped.filter(pl.col("period") != calendar_last_period)
        .filter((pl.col("rebalance_date") >= bars_min) & (pl.col("rebalance_date") <= bars_max))
        .get_column("rebalance_date")
        .to_list()
    )

    if config.start:
        start_date = date.fromisoformat(config.start)
        rebalance_dates = [d for d in rebalance_dates if d >= start_date]
    if config.end:
        end_date = date.fromisoformat(config.end)
        rebalance_dates = [d for d in rebalance_dates if d <= end_date]
    return rebalance_dates
