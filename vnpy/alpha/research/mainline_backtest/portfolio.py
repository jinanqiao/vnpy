"""账户记账与逐日估值。

这个文件回答一个问题：每天收盘后，这个组合值多少钱。

估值规则（对应 spec FR-007、research R3）：
    每笔持仓市值 = 本金基准 × 当日后复权收盘 / 买入日后复权开盘；
    当日停牌（没 K 线）→ 沿用最近一次有效收盘价，标记 is_frozen；
    组合净值 = (现金 + 全部持仓市值) / 初始资金，空仓期净值平直；
    零成本影子净值 = (现金 + 累计已付成本 + 持仓市值) / 初始资金——
    把付出去的成本"补回来"，两条净值的差距就是成本拖累（下界近似）。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from .config import BacktestConfig

# positions.parquet 的固定列类型（对照 data-model.md 的 PositionSnapshot）
POSITIONS_SCHEMA = {
    "datetime": pl.Date, "vt_symbol": pl.Utf8, "industry": pl.Utf8,
    "shares": pl.Int64, "cost_amount": pl.Float64, "market_value": pl.Float64,
    "weight": pl.Float64, "is_frozen": pl.Boolean,
}


def value_position(lookup: dict, position: dict, day: date) -> tuple[float, bool]:
    """一笔持仓在某天的市值，返回 (市值, 是否停牌冻结)。

    有 K 线：按后复权收盘演化，并更新持仓里的 last_close_adj；
    没 K 线（停牌）：按最近一次有效收盘价冻结估值，累计冻结天数。
    """
    bar = lookup.get(position["vt_symbol"], {}).get(day)
    if bar is not None:
        position["last_close_adj"] = bar["close_adj"]
        position["frozen_days"] = 0
        frozen = False
    else:
        position["frozen_days"] += 1
        frozen = True

    market_value = position["basis_amount"] * position["last_close_adj"] / position["buy_open_adj"]
    return market_value, frozen


def snapshot_day(
    lookup: dict,
    positions: dict,
    cash: float,
    total_costs_paid: float,
    day: date,
    config: BacktestConfig,
) -> tuple[dict, list[dict]]:
    """给一天收盘做快照，返回 (净值行, 持仓明细行列表)。

    净值行对应 data-model 的 NavSeries（基准列由流水线事后拼上），
    持仓明细行对应 PositionSnapshot。
    """
    position_rows: list[dict] = []
    total_value = 0.0
    for vt_symbol in sorted(positions.keys()):
        position = positions[vt_symbol]
        market_value, frozen = value_position(lookup, position, day)
        total_value += market_value
        position_rows.append(
            {
                "datetime": day,
                "vt_symbol": vt_symbol,
                "industry": position["industry"],
                "shares": position["shares"],
                "cost_amount": position["cost_amount"],
                "market_value": market_value,
                "weight": 0.0,  # 占比要等组合总值算完才知道，下面统一回填
                "is_frozen": frozen,
            }
        )

    equity = cash + total_value
    for row in position_rows:
        row["weight"] = row["market_value"] / equity if equity > 0 else 0.0

    nav_row = {
        "datetime": day,
        "nav": equity / config.initial_capital,
        "nav_gross": (equity + total_costs_paid) / config.initial_capital,
        "cash": cash,
        "position_value": total_value,
        "n_holdings": len(position_rows),
    }
    return nav_row, position_rows


def attach_benchmark(nav_rows: list[dict], benchmark: pl.DataFrame) -> pl.DataFrame:
    """把基准净值（同起点归一）拼到净值序列上，并算超额净值 nav/benchmark_nav。"""
    nav_frame = pl.DataFrame(
        nav_rows,
        schema={
            "datetime": pl.Date, "nav": pl.Float64, "nav_gross": pl.Float64,
            "cash": pl.Float64, "position_value": pl.Float64, "n_holdings": pl.Int32,
        },
    )
    if nav_frame.is_empty():
        return nav_frame.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("benchmark_nav"),
            pl.lit(None, dtype=pl.Float64).alias("excess_nav"),
        )

    joined = nav_frame.join(benchmark, on="datetime", how="left")
    joined = joined.with_columns(pl.col("benchmark_close").fill_null(strategy="forward"))
    first_close = joined.get_column("benchmark_close")[0]
    joined = joined.with_columns((pl.col("benchmark_close") / first_close).alias("benchmark_nav"))
    joined = joined.with_columns((pl.col("nav") / pl.col("benchmark_nav")).alias("excess_nav"))
    return joined.drop("benchmark_close").sort("datetime")


def trade_costs(trade: dict) -> float:
    """一笔交易的成本合计（佣金 + 印花税 + 滑点），未成交的交易成本为 0。"""
    return (trade["commission"] or 0.0) + (trade["stamp_tax"] or 0.0) + (trade["slippage_cost"] or 0.0)
