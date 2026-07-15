"""交易模拟：把"目标持仓清单"变成一笔笔可审计的交易记录。

这个文件回答一个问题：调仓日那天，钱是怎么换成股票的（以及为什么有的换不成）。

核心约定（对应 spec FR-002 ~ FR-006）：
    执行价   = 执行日开盘价，买入上浮滑点、卖出下压滑点；
    股数     = 用未复权价按 100 股整手向下取整（真实交易约束）；
    收益演化 = 用后复权价（分红除权不产生假亏损，隐含分红再投资）；
    买不进   = 停牌/一字涨停/不在可交易名单 → 放弃并记录原因；
    卖不出   = 停牌/一字跌停 → 顺延到下一个可交易日；
    退市     = 行情永久消失的持仓按最后价格强制清仓。

持仓用一个普通字典表示（整个子包只有 BacktestConfig 一个类）：
    {vt_symbol, industry, shares, basis_amount, buy_open_adj, last_close_adj,
     cost_amount, signal_date, frozen_days}
其中 basis_amount = 股数 × 买入日未复权开盘价（不含滑点），
市值演化公式 = basis_amount × 当日后复权收盘 / 买入日后复权开盘。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from .config import BacktestConfig

# 交易状态与 reason 的固定枚举（对照 contracts/artifacts-schema.md）
# 前五个是"为什么没正常成交"，后三个是风控卖出的触发来源（003 特性）
TRADE_STATUSES = ["filled", "deferred", "abandoned", "forced"]
BLOCK_REASONS = [
    "suspended", "limit_up", "limit_down", "delisted", "insufficient_cash",
    "timing_exit", "no_signal_exit", "stop_loss",
]

# trades.parquet 的固定列类型（对照 data-model.md 的 TradeRecord）
TRADES_SCHEMA = {
    "signal_date": pl.Date, "planned_date": pl.Date, "executed_date": pl.Date,
    "vt_symbol": pl.Utf8, "industry": pl.Utf8, "side": pl.Utf8,
    "status": pl.Utf8, "reason": pl.Utf8,
    "exec_price_raw": pl.Float64, "exec_price_adj": pl.Float64,
    "shares": pl.Int64, "gross_amount": pl.Float64,
    "commission": pl.Float64, "stamp_tax": pl.Float64, "slippage_cost": pl.Float64,
    "defer_days": pl.Int32,
}


def build_price_lookup(
    adjusted_bars: pl.DataFrame,
    unadjusted_bars: pl.DataFrame,
    symbols: list[str],
) -> dict:
    """把回测用得到的股票行情装进字典，按 lookup[股票][日期] 直查。

    每个日期节点是一个小字典：open_adj/close_adj（后复权开收）、
    open_raw/close_raw/high_raw/low_raw（未复权开收高低）、prev_close_raw（未复权前收）。
    只装 selection 里出现过的股票，避免全市场 5000 只白白占内存。
    """
    adj = (
        adjusted_bars.filter(pl.col("vt_symbol").is_in(symbols))
        .select("vt_symbol", "datetime", pl.col("open").alias("open_adj"), pl.col("close").alias("close_adj"))
    )
    raw = (
        unadjusted_bars.filter(pl.col("vt_symbol").is_in(symbols))
        .sort(["vt_symbol", "datetime"])
        .with_columns(pl.col("close").shift(1).over("vt_symbol").alias("prev_close_raw"))
        .select(
            "vt_symbol", "datetime",
            pl.col("open").alias("open_raw"), pl.col("close").alias("close_raw"),
            pl.col("high").alias("high_raw"), pl.col("low").alias("low_raw"),
            "prev_close_raw",
        )
    )
    merged = adj.join(raw, on=["vt_symbol", "datetime"], how="inner")

    lookup: dict = {}
    for row in merged.iter_rows(named=True):
        lookup.setdefault(row["vt_symbol"], {})[row["datetime"]] = row
    return lookup


def find_last_bar_dates(adjusted_bars: pl.DataFrame, symbols: list[str]) -> dict:
    """每只股票最后一根 K 线的日期，用于识别退市（此后行情永久消失）。"""
    last = (
        adjusted_bars.filter(pl.col("vt_symbol").is_in(symbols))
        .group_by("vt_symbol")
        .agg(pl.col("datetime").max().alias("last_date"))
    )
    return {row["vt_symbol"]: row["last_date"] for row in last.iter_rows(named=True)}


def check_buy_blocked(lookup: dict, tradable_set: set, vt_symbol: str, day: date) -> str | None:
    """买入前检查，返回阻塞原因（None = 可以买）。

    依次检查：当天没有 K 线（停牌）→ 不在可交易名单（ST/流动性不足等）→
    一字涨停（开=高=低=收且比前收高，挂单也买不到）。
    """
    bar = lookup.get(vt_symbol, {}).get(day)
    if bar is None:
        return "suspended"
    if (day, vt_symbol) not in tradable_set:
        return "suspended"
    one_word = bar["high_raw"] == bar["low_raw"]
    if one_word and bar["prev_close_raw"] is not None and bar["close_raw"] > bar["prev_close_raw"]:
        return "limit_up"
    return None


def check_sell_blocked(lookup: dict, vt_symbol: str, day: date) -> str | None:
    """卖出前检查，返回阻塞原因（None = 可以卖）。

    卖出只受两种情况阻塞：停牌（没 K 线）和一字跌停（挂单也卖不掉）。
    ST 等状态不阻止卖出——已经持有的股票总是允许离场。
    """
    bar = lookup.get(vt_symbol, {}).get(day)
    if bar is None:
        return "suspended"
    one_word = bar["high_raw"] == bar["low_raw"]
    if one_word and bar["prev_close_raw"] is not None and bar["close_raw"] < bar["prev_close_raw"]:
        return "limit_down"
    return None


def buy_stock(
    lookup: dict,
    vt_symbol: str,
    industry: str,
    target_amount: float,
    available_cash: float,
    day: date,
    signal_date: date,
    config: BacktestConfig,
) -> tuple[dict, dict | None, float]:
    """按目标金额买入一只股票，返回 (交易记录, 新持仓或 None, 实际花掉的现金)。

    股数 = min(目标金额, 可用现金) 能买得起的最大整手数；
    连一手都买不起时放弃（reason=insufficient_cash）。
    """
    bar = lookup[vt_symbol][day]
    exec_price_raw = bar["open_raw"] * (1 + config.slippage_rate)

    budget = min(target_amount, available_cash)
    lot_cost = exec_price_raw * config.lot_size * (1 + config.commission_rate)
    lots = int(budget / lot_cost)
    if lots <= 0:
        trade = trade_row(signal_date, day, vt_symbol, industry, "buy", "abandoned", "insufficient_cash")
        return trade, None, 0.0

    shares = lots * config.lot_size
    gross_amount = shares * exec_price_raw
    commission = gross_amount * config.commission_rate
    slippage_cost = shares * bar["open_raw"] * config.slippage_rate
    cash_out = gross_amount + commission

    position = {
        "vt_symbol": vt_symbol,
        "industry": industry,
        "shares": shares,
        "basis_amount": shares * bar["open_raw"],   # 不含滑点的本金基准（用于后复权演化）
        "buy_open_adj": bar["open_adj"],
        "last_close_adj": bar["close_adj"],
        "cost_amount": cash_out,
        "signal_date": signal_date,
        "frozen_days": 0,
    }
    trade = trade_row(
        signal_date, day, vt_symbol, industry, "buy", "filled", None,
        executed_date=day, exec_price_raw=exec_price_raw, exec_price_adj=bar["open_adj"],
        shares=shares, gross_amount=gross_amount,
        commission=commission, stamp_tax=0.0, slippage_cost=slippage_cost,
    )
    return trade, position, cash_out


def sell_position(
    lookup: dict,
    position: dict,
    day: date,
    planned_date: date,
    config: BacktestConfig,
    forced: bool = False,
    reason: str | None = None,
) -> tuple[dict, float]:
    """卖出一笔持仓，返回 (交易记录, 收回的现金)。

    卖出金额 = 本金基准按后复权开盘价演化到当天，再扣滑点/印花税/佣金。
    forced=True 是退市强平：按最后可得收盘价演化，不收滑点（无真实盘口）。
    reason 标注卖出的触发来源（风控卖出用 timing_exit/no_signal_exit/stop_loss，
    普通调仓卖出保持 None）。
    """
    if forced:
        market_gross = position["basis_amount"] * position["last_close_adj"] / position["buy_open_adj"]
        exec_price_adj = position["last_close_adj"]
        exec_price_raw = None
        slippage_cost = 0.0
    else:
        bar = lookup[position["vt_symbol"]][day]
        market_open = position["basis_amount"] * bar["open_adj"] / position["buy_open_adj"]
        slippage_cost = market_open * config.slippage_rate
        market_gross = market_open - slippage_cost
        exec_price_adj = bar["open_adj"]
        exec_price_raw = bar["open_raw"] * (1 - config.slippage_rate)

    stamp_tax = market_gross * config.stamp_tax_rate
    commission = market_gross * config.commission_rate
    cash_in = market_gross - stamp_tax - commission

    defer_days = (day - planned_date).days if day > planned_date else None
    status = "forced" if forced else ("deferred" if defer_days else "filled")
    if forced:
        reason = "delisted"

    trade = trade_row(
        position["signal_date"], planned_date, position["vt_symbol"], position["industry"],
        "sell", status, reason,
        executed_date=day, exec_price_raw=exec_price_raw, exec_price_adj=exec_price_adj,
        shares=position["shares"], gross_amount=market_gross,
        commission=commission, stamp_tax=stamp_tax, slippage_cost=slippage_cost,
        defer_days=defer_days,
    )
    return trade, cash_in


def sell_symbols(
    lookup: dict,
    positions: dict,
    pending_sells: dict,
    symbols: list[str],
    day: date,
    config: BacktestConfig,
    cash: float,
    costs_paid: float,
    reason: str | None = None,
) -> tuple[list[dict], float, float]:
    """卖出指定的一批持仓，返回 (交易记录列表, 更新后现金, 更新后累计成本)。

    可卖的立即按当日开盘卖出；停牌/一字跌停的挂入 pending_sells 等待补卖
    （值为 (计划卖出日, reason)，补卖时沿用同一 reason）。
    现金和成本在函数内逐笔更新（而不是求和后一次性加回），保证浮点运算
    顺序与逐笔记账完全一致——这是"同输入逐字节可复现"的前提。
    调用方必须传入排好序的 symbols（同理，保证逐笔顺序固定）。
    """
    trades: list[dict] = []
    for vt_symbol in symbols:
        if vt_symbol in pending_sells:
            continue
        if check_sell_blocked(lookup, vt_symbol, day) is None:
            trade, cash_in = sell_position(
                lookup, positions.pop(vt_symbol), day, day, config, reason=reason
            )
            trades.append(trade)
            cash += cash_in
            costs_paid += (trade["commission"] or 0.0) + (trade["stamp_tax"] or 0.0) + (trade["slippage_cost"] or 0.0)
        else:
            pending_sells[vt_symbol] = (day, reason)
    return trades, cash, costs_paid


def sell_all_positions(
    lookup: dict,
    positions: dict,
    pending_sells: dict,
    day: date,
    config: BacktestConfig,
    cash: float,
    costs_paid: float,
    reason: str | None = None,
) -> tuple[list[dict], float, float]:
    """清仓全部持仓（月度全卖、择时清仓、无信号清仓共用，仅 reason 不同）。"""
    return sell_symbols(
        lookup, positions, pending_sells, sorted(list(positions.keys())),
        day, config, cash, costs_paid, reason=reason,
    )


def trade_row(
    signal_date: date,
    planned_date: date,
    vt_symbol: str,
    industry: str,
    side: str,
    status: str,
    reason: str | None,
    executed_date: date | None = None,
    exec_price_raw: float | None = None,
    exec_price_adj: float | None = None,
    shares: int | None = None,
    gross_amount: float | None = None,
    commission: float | None = None,
    stamp_tax: float | None = None,
    slippage_cost: float | None = None,
    defer_days: int | None = None,
) -> dict:
    """组装一行交易记录（schema 对照 data-model.md 的 TradeRecord）。"""
    return {
        "signal_date": signal_date,
        "planned_date": planned_date,
        "executed_date": executed_date,
        "vt_symbol": vt_symbol,
        "industry": industry,
        "side": side,
        "status": status,
        "reason": reason,
        "exec_price_raw": exec_price_raw,
        "exec_price_adj": exec_price_adj,
        "shares": shares,
        "gross_amount": gross_amount,
        "commission": commission,
        "stamp_tax": stamp_tax,
        "slippage_cost": slippage_cost,
        "defer_days": defer_days,
    }
