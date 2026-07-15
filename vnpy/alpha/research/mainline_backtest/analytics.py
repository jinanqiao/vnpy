"""净值曲线的深度分析：滚动收益、滚动夏普、强弱市分段表现。

这个文件回答一个问题：除了总收益和年化，这条净值曲线在"过程中"表现如何——
任意一年买入的体验（滚动收益）、风险调整后的稳定性（滚动夏普）、
以及牛市/弱市里各自的表现（场景适应性）。

全部是"输入 nav DataFrame、输出 dict/DataFrame"的纯函数，供对比实验脚本调用。
nav DataFrame 需要的列：datetime, nav, benchmark_nav（pipeline 的 nav.parquet 自带）。
"""

from __future__ import annotations

import math

import polars as pl


def rolling_window_returns(nav_frame: pl.DataFrame, window: int = 252, annualize: bool = True) -> list[float]:
    """滚动 window 个交易日的收益序列（每个交易日回看一个窗口）。

    例如 window=252 就是"任意一天往前看一年，这一年的收益是多少"，
    用来回答"如果我是在最差/最好的时点开始跟这个策略，体验差多少"。
    annualize=False 时返回窗口原始收益（短窗口年化会夸大波动，建议不年化）。
    """
    nav = nav_frame.get_column("nav").to_list()
    if len(nav) <= window:
        return []
    returns = []
    for index in range(window, len(nav)):
        total = nav[index] / nav[index - window]
        returns.append(total ** (252 / window) - 1 if annualize else total - 1)
    return returns


def rolling_sharpe(nav_frame: pl.DataFrame, window: int = 252) -> list[float]:
    """滚动 window 个交易日的夏普比率序列（无风险利率取 0）。"""
    nav = nav_frame.get_column("nav").to_list()
    if len(nav) <= window:
        return []
    daily = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    sharpes = []
    for index in range(window, len(daily) + 1):
        segment = daily[index - window: index]
        mean = sum(segment) / window
        variance = sum((r - mean) ** 2 for r in segment) / window
        std = math.sqrt(variance)
        sharpes.append(mean / std * math.sqrt(252) if std > 0 else 0.0)
    return sharpes


def distribution_stats(values: list[float]) -> dict:
    """一组数的分布统计：最差 / 四分位 / 中位 / 最好 / 为正的占比。"""
    if not values:
        return {"min": None, "p25": None, "median": None, "p75": None, "max": None, "positive_share": None}
    ordered = sorted(values)
    n = len(ordered)

    def pct(q: float) -> float:
        return ordered[min(int(q * n), n - 1)]

    return {
        "min": ordered[0],
        "p25": pct(0.25),
        "median": pct(0.50),
        "p75": pct(0.75),
        "max": ordered[-1],
        "positive_share": sum(1 for v in values if v > 0) / n,
    }


def regime_performance(nav_frame: pl.DataFrame, ma_window: int = 60) -> dict:
    """按"基准是否在 ma_window 日均线上方"把交易日分成强/弱市，分段算表现。

    返回 {"strong": {...}, "weak": {...}}，每段含天数、策略年化、基准年化——
    用来回答"这个策略靠什么赚钱：强市跟涨能力还是弱市躲跌能力"。
    均线窗口不足的开头几天不参与统计。
    """
    frame = nav_frame.sort("datetime").with_columns(
        pl.col("benchmark_nav").rolling_mean(ma_window).alias("bench_ma"),
        (pl.col("nav") / pl.col("nav").shift(1) - 1).alias("ret"),
        (pl.col("benchmark_nav") / pl.col("benchmark_nav").shift(1) - 1).alias("bench_ret"),
    ).drop_nulls(["bench_ma", "ret"])

    result = {}
    for label, condition in [
        ("strong", pl.col("benchmark_nav") >= pl.col("bench_ma")),
        ("weak", pl.col("benchmark_nav") < pl.col("bench_ma")),
    ]:
        segment = frame.filter(condition)
        days = segment.height
        if days == 0:
            result[label] = {"days": 0, "annual_return": None, "benchmark_annual_return": None}
            continue
        mean = float(segment.get_column("ret").mean())
        bench_mean = float(segment.get_column("bench_ret").mean())
        result[label] = {
            "days": days,
            "annual_return": mean * 252,               # 算术年化，段内对比够用
            "benchmark_annual_return": bench_mean * 252,
        }
    return result


def analyze_nav(nav_frame: pl.DataFrame) -> dict:
    """一条净值曲线的完整深度分析包（滚动 12 个月 + 滚动 3 个月 + 强弱市）。"""
    return {
        "rolling_1y_return": distribution_stats(rolling_window_returns(nav_frame, 252, annualize=True)),
        "rolling_3m_return": distribution_stats(rolling_window_returns(nav_frame, 60, annualize=False)),
        "rolling_1y_sharpe": distribution_stats(rolling_sharpe(nav_frame, 252)),
        "regime": regime_performance(nav_frame, 60),
    }
