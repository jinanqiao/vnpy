"""交易执行单元测试：整手取整、滑点方向、成本口径、买卖阻塞、追溯字段（FR-002~006）。"""

from datetime import date, timedelta

import polars as pl

from vnpy.alpha.research.mainline_backtest.config import BacktestConfig
from vnpy.alpha.research.mainline_backtest.execution import (
    BLOCK_REASONS,
    TRADE_STATUSES,
    build_price_lookup,
    buy_stock,
    check_buy_blocked,
    check_sell_blocked,
    sell_position,
)
from backtest_fixtures import make_business_dates, make_dual_bars

CONFIG = BacktestConfig()
DATES = make_business_dates(date(2025, 1, 1), 5)
TODAY = DATES[2]


def make_lookup(**stocks) -> dict:
    """快速构造 lookup：make_lookup(A_SZ=(closes, factors, one_word_days))。"""
    adj_frames = []
    raw_frames = []
    for key, spec in stocks.items():
        vt_symbol = key.replace("_", ".")
        closes, factors, one_word = spec
        adj, raw = make_dual_bars(vt_symbol, DATES, closes, factors, one_word_days=one_word)
        adj_frames.append(adj)
        raw_frames.append(raw)
    symbols = [k.replace("_", ".") for k in stocks]
    return build_price_lookup(pl.concat(adj_frames), pl.concat(raw_frames), symbols)


def flat(price: float) -> tuple:
    return ([price] * len(DATES), None, None)


def test_buy_lot_rounding_and_cost_breakdown() -> None:
    """整手向下取整 + 佣金/滑点口径逐项手算对照。"""
    lookup = make_lookup(A_SZ=flat(10.0))
    target = 500_000.0

    trade, position, cash_out = buy_stock(
        lookup, "A.SZ", "GICS1X", target, available_cash=1_000_000.0,
        day=TODAY, signal_date=DATES[0], config=CONFIG,
    )

    exec_price = 10.0 * 1.001                       # 开盘价上浮 0.1% 滑点
    lot_cost = exec_price * 100 * 1.00025           # 一手的钱（含佣金）
    expected_shares = int(target / lot_cost) * 100

    assert trade["status"] == "filled"
    assert trade["shares"] == expected_shares and expected_shares % 100 == 0
    assert abs(trade["exec_price_raw"] - exec_price) < 1e-12
    assert abs(trade["gross_amount"] - expected_shares * exec_price) < 1e-6
    assert abs(trade["commission"] - trade["gross_amount"] * 0.00025) < 1e-6
    assert abs(trade["slippage_cost"] - expected_shares * 10.0 * 0.001) < 1e-6
    assert trade["stamp_tax"] == 0.0                # 买入不收印花税
    assert abs(cash_out - (trade["gross_amount"] + trade["commission"])) < 1e-6
    assert position["basis_amount"] == expected_shares * 10.0  # 本金基准不含滑点


def test_buy_insufficient_cash_abandoned() -> None:
    """连一手都买不起 → 放弃并记录 insufficient_cash，不产生持仓。"""
    lookup = make_lookup(A_SZ=flat(10.0))

    trade, position, cash_out = buy_stock(
        lookup, "A.SZ", "GICS1X", target_amount=500.0, available_cash=500.0,
        day=TODAY, signal_date=DATES[0], config=CONFIG,
    )

    assert trade["status"] == "abandoned"
    assert trade["reason"] == "insufficient_cash"
    assert position is None and cash_out == 0.0
    assert trade["shares"] is None and trade["gross_amount"] is None


def test_sell_cost_breakdown_and_slippage_direction() -> None:
    """卖出金额 = 本金按后复权演化 × (1-滑点)，再扣印花税和佣金。"""
    lookup = make_lookup(A_SZ=([10.0, 10.0, 12.0, 12.0, 12.0], None, None))
    position = {
        "vt_symbol": "A.SZ", "industry": "GICS1X", "shares": 1000,
        "basis_amount": 10_000.0, "buy_open_adj": 10.0, "last_close_adj": 10.0,
        "cost_amount": 10_012.5, "signal_date": DATES[0], "frozen_days": 0,
    }
    # DATES[3] 的开盘 = DATES[2] 的收盘 = 12.0
    trade, cash_in = sell_position(lookup, position, DATES[3], DATES[3], CONFIG)

    market_open = 10_000.0 * 12.0 / 10.0            # 本金演化到卖出日开盘
    gross = market_open * (1 - 0.001)               # 滑点往下压价
    assert abs(trade["gross_amount"] - gross) < 1e-6
    assert abs(trade["stamp_tax"] - gross * 0.0005) < 1e-6
    assert abs(trade["commission"] - gross * 0.00025) < 1e-6
    assert abs(cash_in - (gross - trade["stamp_tax"] - trade["commission"])) < 1e-6
    assert trade["status"] == "filled" and trade["defer_days"] is None
    assert trade["exec_price_raw"] < 12.0           # 卖出成交价低于开盘价


def test_deferred_sell_records_delay() -> None:
    """晚于计划日卖出 → status=deferred 且 defer_days 记录延迟。"""
    lookup = make_lookup(A_SZ=flat(10.0))
    position = {
        "vt_symbol": "A.SZ", "industry": "GICS1X", "shares": 1000,
        "basis_amount": 10_000.0, "buy_open_adj": 10.0, "last_close_adj": 10.0,
        "cost_amount": 10_012.5, "signal_date": DATES[0], "frozen_days": 0,
    }
    trade, _ = sell_position(lookup, position, DATES[3], planned_date=DATES[1], config=CONFIG)

    assert trade["status"] == "deferred"
    assert trade["defer_days"] == (DATES[3] - DATES[1]).days
    assert trade["planned_date"] == DATES[1] and trade["executed_date"] == DATES[3]


def test_forced_close_uses_last_price_without_slippage() -> None:
    """退市强平：按最后价格演化清仓，不收滑点，reason=delisted。"""
    lookup = make_lookup(A_SZ=flat(10.0))
    position = {
        "vt_symbol": "A.SZ", "industry": "GICS1X", "shares": 1000,
        "basis_amount": 10_000.0, "buy_open_adj": 10.0, "last_close_adj": 8.0,
        "cost_amount": 10_012.5, "signal_date": DATES[0], "frozen_days": 30,
    }
    trade, cash_in = sell_position(lookup, position, DATES[4], DATES[4], CONFIG, forced=True)

    market = 10_000.0 * 8.0 / 10.0                  # 按最后可得价 8 元演化
    assert trade["status"] == "forced" and trade["reason"] == "delisted"
    assert trade["slippage_cost"] == 0.0
    assert abs(trade["gross_amount"] - market) < 1e-6
    assert abs(cash_in - market * (1 - 0.0005 - 0.00025)) < 1e-6


def test_buy_blocked_reasons() -> None:
    """买入阻塞三种形态：停牌（无K线）、不在可交易名单、一字涨停。"""
    lookup = make_lookup(
        OK_SZ=flat(10.0),
        UP_SZ=([10.0, 10.0, 11.1, 11.1, 11.1], None, {DATES[2]: "up"}),
    )
    tradable = {(TODAY, "OK.SZ"), (TODAY, "UP.SZ")}

    assert check_buy_blocked(lookup, tradable, "OK.SZ", TODAY) is None
    assert check_buy_blocked(lookup, tradable, "GONE.SZ", TODAY) == "suspended"   # 没 K 线
    assert check_buy_blocked(lookup, set(), "OK.SZ", TODAY) == "suspended"        # 不在名单
    assert check_buy_blocked(lookup, tradable, "UP.SZ", TODAY) == "limit_up"      # 一字涨停


def test_sell_blocked_reasons() -> None:
    """卖出只被停牌和一字跌停阻塞；一字涨停不影响卖出。"""
    lookup = make_lookup(
        DOWN_SZ=([10.0, 10.0, 9.0, 9.0, 9.0], None, {DATES[2]: "down"}),
        UP_SZ=([10.0, 10.0, 11.1, 11.1, 11.1], None, {DATES[2]: "up"}),
    )

    assert check_sell_blocked(lookup, "GONE.SZ", TODAY) == "suspended"
    assert check_sell_blocked(lookup, "DOWN.SZ", TODAY) == "limit_down"
    assert check_sell_blocked(lookup, "UP.SZ", TODAY) is None


def test_trade_record_traceability_fields() -> None:
    """T019: 成交记录字段齐全可追溯；无未来信息（计划日晚于信号日）。"""
    lookup = make_lookup(A_SZ=flat(10.0))
    trade, _, _ = buy_stock(
        lookup, "A.SZ", "GICS1X", 100_000.0, 100_000.0, TODAY, DATES[0], CONFIG
    )

    required_fields = [
        "signal_date", "planned_date", "executed_date", "vt_symbol", "industry",
        "side", "status", "reason", "exec_price_raw", "exec_price_adj",
        "shares", "gross_amount", "commission", "stamp_tax", "slippage_cost", "defer_days",
    ]
    assert all(field in trade for field in required_fields)
    assert trade["status"] in TRADE_STATUSES
    assert trade["reason"] is None or trade["reason"] in BLOCK_REASONS
    assert trade["planned_date"] > trade["signal_date"]        # 无未来信息
    assert trade["executed_date"] >= trade["planned_date"]
