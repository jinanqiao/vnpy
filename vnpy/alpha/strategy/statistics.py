from dataclasses import dataclass
from datetime import date
from math import sqrt
from typing import cast

import numpy as np
import polars as pl


@dataclass(frozen=True)
class BacktestStatisticsResult:
    """Calculated backtest statistics and enriched daily data."""

    daily_df: pl.DataFrame
    statistics: dict
    positive_balance: bool


def calculate_backtest_statistics(
    daily_df: pl.DataFrame,
    capital: float,
    risk_free: float,
    annual_days: int,
) -> BacktestStatisticsResult:
    """Calculate alpha backtest statistics with the existing return schema."""
    start_date: str = ""
    end_date: str = ""
    total_days: int = 0
    profit_days: int = 0
    loss_days: int = 0
    end_balance: float = 0
    max_drawdown: float = 0
    max_ddpercent: float = 0
    max_drawdown_duration: int = 0
    total_net_pnl: float = 0
    daily_net_pnl: float = 0
    total_commission: float = 0
    daily_commission: float = 0
    total_turnover: float = 0
    daily_turnover: float = 0
    total_trade_count: int = 0
    daily_trade_count: float = 0
    total_return: float = 0
    annual_return: float = 0
    daily_return: float = 0
    return_std: float = 0
    sharpe_ratio: float = 0
    return_drawdown_ratio: float = 0

    df: pl.DataFrame = daily_df.with_columns(
        balance=pl.col("net_pnl").cum_sum() + capital
    ).with_columns(
        pl.col("balance").pct_change().fill_null(0).alias("return"),
        highlevel=pl.col("balance").cum_max(),
    ).with_columns(
        drawdown=pl.col("balance") - pl.col("highlevel"),
        ddpercent=(pl.col("balance") / pl.col("highlevel") - 1) * 100,
    )

    positive_balance: bool = bool((df["balance"] > 0).all())

    if positive_balance:
        start_date = df["date"][0]
        end_date = df["date"][-1]

        total_days = len(df)
        profit_days = df.filter(pl.col("net_pnl") > 0).height
        loss_days = df.filter(pl.col("net_pnl") < 0).height

        end_balance = df["balance"][-1]
        max_drawdown = cast(float, df["drawdown"].min())
        max_ddpercent = cast(float, df["ddpercent"].min())

        max_drawdown_end_idx = cast(int, df["drawdown"].arg_min())
        max_drawdown_end = df["date"][max_drawdown_end_idx]

        if isinstance(max_drawdown_end, date):
            max_drawdown_start_idx = cast(int, df.slice(0, max_drawdown_end_idx + 1)["balance"].arg_max())
            max_drawdown_start = df["date"][max_drawdown_start_idx]
            max_drawdown_duration = (max_drawdown_end - max_drawdown_start).days
        else:
            max_drawdown_duration = 0

        total_net_pnl = cast(float, df["net_pnl"].sum())
        daily_net_pnl = total_net_pnl / total_days

        total_commission = cast(float, df["commission"].sum())
        daily_commission = total_commission / total_days

        total_turnover = cast(float, df["turnover"].sum())
        daily_turnover = total_turnover / total_days

        total_trade_count = cast(int, df["trade_count"].sum())
        daily_trade_count = total_trade_count / total_days

        total_return = (end_balance / capital - 1) * 100
        annual_return = total_return / total_days * annual_days
        daily_return = cast(float, df["return"].mean()) * 100
        return_std = cast(float, df["return"].std()) * 100

        if return_std:
            daily_risk_free = risk_free / sqrt(annual_days)
            sharpe_ratio = (daily_return - daily_risk_free) / return_std * sqrt(annual_days)
        else:
            sharpe_ratio = 0

        return_drawdown_ratio = -total_net_pnl / max_drawdown

    statistics: dict = {
        "start_date": start_date,
        "end_date": end_date,
        "total_days": total_days,
        "profit_days": profit_days,
        "loss_days": loss_days,
        "capital": capital,
        "end_balance": end_balance,
        "max_drawdown": max_drawdown,
        "max_ddpercent": max_ddpercent,
        "max_drawdown_duration": max_drawdown_duration,
        "total_net_pnl": total_net_pnl,
        "daily_net_pnl": daily_net_pnl,
        "total_commission": total_commission,
        "daily_commission": daily_commission,
        "total_turnover": total_turnover,
        "daily_turnover": daily_turnover,
        "total_trade_count": total_trade_count,
        "daily_trade_count": daily_trade_count,
        "total_return": total_return,
        "annual_return": annual_return,
        "daily_return": daily_return,
        "return_std": return_std,
        "sharpe_ratio": sharpe_ratio,
        "return_drawdown_ratio": return_drawdown_ratio,
    }

    for key, value in statistics.items():
        if value in (np.inf, -np.inf):
            value = 0
        statistics[key] = np.nan_to_num(value)

    return BacktestStatisticsResult(df, statistics, positive_balance)
