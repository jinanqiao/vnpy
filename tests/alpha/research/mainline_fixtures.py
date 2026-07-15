"""mainline 测试共用的合成数据工具：走势完全可控，期望结果可以手算。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl


def make_business_dates(start: date, count: int) -> list[date]:
    """从 start 开始生成 count 个工作日（跳过周六周日，够用即可）。"""
    dates: list[date] = []
    current = start
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def make_stock_bars(
    vt_symbol: str,
    dates: list[date],
    daily_return: float,
    start_price: float = 10.0,
    turnover: float = 1_000_000.0,
) -> pl.DataFrame:
    """生成一只股票的等比走势 K 线：每天固定涨（或跌）daily_return。"""
    closes: list[float] = []
    price = start_price
    for _ in dates:
        price = price * (1 + daily_return)
        closes.append(price)
    return pl.DataFrame(
        {
            "datetime": dates,
            "vt_symbol": [vt_symbol] * len(dates),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "turnover": [turnover] * len(dates),
        }
    )


def make_industry_map(members: dict[str, list[str]]) -> pl.DataFrame:
    """行业归属表：{"GICS1XX": ["000001.SZ", ...]} -> (vt_symbol, industry)。"""
    rows = [
        {"vt_symbol": symbol, "industry": industry}
        for industry, symbols in members.items()
        for symbol in symbols
    ]
    return pl.DataFrame(rows)


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


def write_data_lake(
    data_dir: Path,
    bars: pl.DataFrame,
    sector_members: pl.DataFrame,
    execution_universe: pl.DataFrame,
    trade_dates: list[date],
) -> None:
    """把合成数据按数据湖的目录结构写成 parquet，供流水线端到端测试。"""
    (data_dir / "normalized").mkdir(parents=True)
    (data_dir / "sector").mkdir(parents=True)
    (data_dir / "universe").mkdir(parents=True)
    (data_dir / "calendar").mkdir(parents=True)

    bars.write_parquet(data_dir / "normalized" / "daily_bars_all_a.parquet")
    sector_members.write_parquet(data_dir / "sector" / "sector_members.parquet")
    execution_universe.write_parquet(data_dir / "universe" / "execution_universe.parquet")
    pl.DataFrame(
        {"market": ["SH"] * len(trade_dates), "trade_date": trade_dates}
    ).write_parquet(data_dir / "calendar" / "trading_dates.parquet")
