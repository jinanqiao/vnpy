from dataclasses import dataclass
from math import sqrt
from typing import cast

import polars as pl


@dataclass(frozen=True)
class PerformanceMetrics:
    """Core performance metrics for a beginner-friendly strategy report."""

    total_return: float
    annual_return: float
    annual_volatility: float
    sharpe_ratio: float
    max_drawdown: float
    calmar_ratio: float
    win_rate: float
    average_turnover: float


def calculate_performance_metrics(
    daily_df: pl.DataFrame,
    return_column: str = "net_return",
    turnover_column: str = "turnover",
    annual_days: int = 252,
) -> PerformanceMetrics:
    """Calculate core metrics from a daily backtest result."""
    if annual_days <= 0:
        raise ValueError("annual_days must be positive")
    _require_columns(daily_df, {"datetime", return_column, turnover_column})

    returns: pl.Series = daily_df[return_column].fill_null(0)
    total_days: int = len(returns)
    if total_days == 0:
        raise ValueError("daily_df is empty")

    equity: pl.Series = (returns + 1).cum_prod()
    total_return: float = cast(float, equity[-1] - 1)
    annual_return: float = (1 + total_return) ** (annual_days / total_days) - 1

    daily_std: float = cast(float, returns.std())
    annual_volatility: float = daily_std * sqrt(annual_days)
    sharpe_ratio: float = annual_return / annual_volatility if annual_volatility else 0.0

    drawdown: pl.Series = equity / equity.cum_max() - 1
    max_drawdown: float = cast(float, drawdown.min())
    calmar_ratio: float = annual_return / abs(max_drawdown) if max_drawdown else 0.0

    win_rate: float = cast(float, (returns > 0).sum()) / total_days
    average_turnover: float = cast(float, daily_df[turnover_column].fill_null(0).mean())

    return PerformanceMetrics(
        total_return=total_return,
        annual_return=annual_return,
        annual_volatility=annual_volatility,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        calmar_ratio=calmar_ratio,
        win_rate=win_rate,
        average_turnover=average_turnover,
    )


def _require_columns(df: pl.DataFrame, columns: set[str]) -> None:
    missing: set[str] = columns - set(df.columns)
    if missing:
        missing_text: str = ", ".join(sorted(missing))
        raise ValueError(f"Missing required columns: {missing_text}")
