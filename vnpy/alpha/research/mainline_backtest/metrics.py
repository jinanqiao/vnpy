"""风险收益指标计算。

这个文件回答一个问题：这条净值曲线到底好不好。

指标口径（全部写死，避免争议，对应 research R6）：
    总收益     期末净值 - 1
    年化收益   期末净值 ^ (252 / 交易日数) - 1
    最大回撤   min(净值 / 历史峰值 - 1)，负数，越接近 0 越好
    夏普比率   日收益均值 / 日收益标准差 × sqrt(252)，无风险利率取 0
    月度胜率   调仓期间组合收益 > 0 的期数占比；另算跑赢基准的期数占比
    年均换手率 sum(每期买入金额) / 初始资金，再按年折算
    成本拖累   零成本年化 - 含成本年化（下界近似，见 portfolio 模块说明）
"""

from __future__ import annotations

import math

import polars as pl


def basic_metrics(nav: list[float]) -> dict:
    """对一条净值序列算 总收益/年化/最大回撤/夏普 四件套。"""
    if len(nav) < 2:
        return {"total_return": 0.0, "annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}

    total_return = nav[-1] / nav[0] - 1
    annual_return = (nav[-1] / nav[0]) ** (252 / (len(nav) - 1)) - 1

    peak = nav[0]
    max_drawdown = 0.0
    for value in nav:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)

    daily_returns = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean) ** 2 for r in daily_returns) / len(daily_returns)
    std = math.sqrt(variance)
    sharpe = mean / std * math.sqrt(252) if std > 0 else 0.0

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
    }


def period_win_rates(nav_frame: pl.DataFrame, rebalance_dates: list) -> dict:
    """按调仓期切段，算组合的月度胜率和跑赢基准的胜率。

    第 i 期收益 = 第 i 个调仓日到第 i+1 个调仓日之间的净值变化
    （最后一期到数据末尾）。没有完整期时返回 0。
    """
    boundaries = sorted(rebalance_dates)
    if not boundaries or nav_frame.is_empty():
        return {"monthly_win_rate": 0.0, "win_vs_benchmark_rate": 0.0, "n_periods": 0}

    wins = 0
    beats = 0
    count = 0
    for index, start_date in enumerate(boundaries):
        end_date = boundaries[index + 1] if index + 1 < len(boundaries) else None
        segment = nav_frame.filter(pl.col("datetime") >= start_date)
        if end_date is not None:
            segment = segment.filter(pl.col("datetime") < end_date)
        if segment.height < 2:
            continue
        nav_return = segment["nav"][-1] / segment["nav"][0] - 1
        benchmark_return = segment["benchmark_nav"][-1] / segment["benchmark_nav"][0] - 1
        count += 1
        wins += 1 if nav_return > 0 else 0
        beats += 1 if nav_return > benchmark_return else 0

    return {
        "monthly_win_rate": wins / count if count else 0.0,
        "win_vs_benchmark_rate": beats / count if count else 0.0,
        "n_periods": count,
    }


def turnover_metrics(trades: pl.DataFrame, initial_capital: float, n_days: int) -> dict:
    """年均换手率 = 累计买入金额 / 初始资金，按 252 交易日折算成年。"""
    if trades.is_empty() or n_days < 1:
        return {"annual_turnover": 0.0, "total_buy_amount": 0.0}
    buys = trades.filter((pl.col("side") == "buy") & pl.col("gross_amount").is_not_null())
    total_buy = float(buys.get_column("gross_amount").sum() or 0.0)
    return {
        "annual_turnover": total_buy / initial_capital * (252 / n_days),
        "total_buy_amount": total_buy,
    }


def build_metrics_summary(
    nav_frame: pl.DataFrame,
    trades: pl.DataFrame,
    rebalance_dates: list,
    initial_capital: float,
) -> dict:
    """汇总全部指标：总体 + 零成本对照 + 基准 + 分年度（MetricsSummary）。"""
    nav = nav_frame.get_column("nav").to_list()
    nav_gross = nav_frame.get_column("nav_gross").to_list()
    benchmark = nav_frame.get_column("benchmark_nav").to_list()

    overall = basic_metrics(nav)
    gross = basic_metrics(nav_gross)
    overall["cost_drag"] = gross["annual_return"] - overall["annual_return"]
    overall.update(period_win_rates(nav_frame, rebalance_dates))
    overall.update(turnover_metrics(trades, initial_capital, len(nav) - 1))

    by_year = []
    years = nav_frame.with_columns(pl.col("datetime").dt.year().alias("year"))
    for year in sorted(years.get_column("year").unique().to_list()):
        segment = years.filter(pl.col("year") == year)
        year_metrics = basic_metrics(segment.get_column("nav").to_list())
        year_benchmark = basic_metrics(segment.get_column("benchmark_nav").to_list())
        by_year.append(
            {
                "year": int(year),
                **year_metrics,
                "benchmark_total_return": year_benchmark["total_return"],
            }
        )

    return {
        **overall,
        "gross": gross,
        "benchmark": basic_metrics(benchmark),
        "by_year": by_year,
    }
