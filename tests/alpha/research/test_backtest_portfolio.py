"""记账估值单元测试：后复权演化、除权日连续性、停牌冻结、会计恒等式（FR-007）。"""

from datetime import date

import polars as pl

from vnpy.alpha.research.mainline_backtest.config import BacktestConfig
from vnpy.alpha.research.mainline_backtest.execution import build_price_lookup
from vnpy.alpha.research.mainline_backtest.portfolio import snapshot_day, value_position
from backtest_fixtures import make_business_dates, make_dual_bars

CONFIG = BacktestConfig(initial_capital=1_000_000.0)
DATES = make_business_dates(date(2025, 3, 3), 6)


def make_position(vt_symbol: str, basis: float, buy_open_adj: float) -> dict:
    return {
        "vt_symbol": vt_symbol, "industry": "GICS1X", "shares": 1000,
        "basis_amount": basis, "buy_open_adj": buy_open_adj, "last_close_adj": buy_open_adj,
        "cost_amount": basis * 1.001, "signal_date": DATES[0], "frozen_days": 0,
    }


def build_lookup(closes: list[float], factors: list[float] | None = None) -> dict:
    adj, raw = make_dual_bars("A.SZ", DATES, closes, factors)
    return build_price_lookup(adj, raw, ["A.SZ"])


def test_valuation_follows_adjusted_close() -> None:
    """市值 = 本金基准 × 当日后复权收盘 / 买入日后复权开盘。"""
    lookup = build_lookup([10.0, 11.0, 12.0, 12.0, 12.0, 12.0])
    position = make_position("A.SZ", basis=100_000.0, buy_open_adj=10.0)

    value, frozen = value_position(lookup, position, DATES[2])
    assert abs(value - 100_000.0 * 12.0 / 10.0) < 1e-6
    assert not frozen


def test_ex_dividend_day_no_fake_loss() -> None:
    """除权日未复权价腰斩，但后复权连续 → 市值无假性暴跌。"""
    closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0]          # 后复权价稳定
    factors = [1.0, 1.0, 1.0, 2.0, 2.0, 2.0]               # 第 4 天除权（未复权腰斩）
    lookup = build_lookup(closes, factors)
    assert lookup["A.SZ"][DATES[3]]["close_raw"] == 5.0    # 确认未复权确实腰斩

    position = make_position("A.SZ", basis=100_000.0, buy_open_adj=10.0)
    value_before, _ = value_position(lookup, position, DATES[2])
    value_after, _ = value_position(lookup, position, DATES[3])
    assert abs(value_after - value_before) < 1e-6          # 市值连续，没有 -50%


def test_suspended_position_frozen_at_last_close() -> None:
    """停牌日（无K线）沿用最近有效收盘价估值并标记冻结。"""
    lookup = build_lookup([10.0, 11.0, 12.0, 12.0, 12.0, 12.0])
    position = make_position("A.SZ", basis=100_000.0, buy_open_adj=10.0)

    value_position(lookup, position, DATES[2])             # 先正常估值到 12 元
    missing_day = date(2030, 1, 1)                         # lookup 里不存在的日期
    value, frozen = value_position(lookup, position, missing_day)

    assert frozen and position["frozen_days"] == 1
    assert abs(value - 100_000.0 * 12.0 / 10.0) < 1e-6     # 冻结在最近的 12 元


def test_snapshot_accounting_identity_and_gross_nav() -> None:
    """恒等式: 净值 = (现金 + Σ持仓市值) / 初始资金；影子净值 ≥ 净值。"""
    lookup = build_lookup([10.0, 11.0, 12.0, 12.0, 12.0, 12.0])
    positions = {"A.SZ": make_position("A.SZ", basis=100_000.0, buy_open_adj=10.0)}

    nav_row, position_rows = snapshot_day(
        lookup, positions, cash=880_000.0, total_costs_paid=1_500.0,
        day=DATES[2], config=CONFIG,
    )

    total_value = sum(row["market_value"] for row in position_rows)
    assert abs(nav_row["nav"] - (880_000.0 + total_value) / 1_000_000.0) < 1e-12
    assert abs(nav_row["nav_gross"] - (880_000.0 + 1_500.0 + total_value) / 1_000_000.0) < 1e-12
    assert nav_row["nav_gross"] >= nav_row["nav"]
    assert nav_row["n_holdings"] == 1
    assert abs(sum(row["weight"] for row in position_rows)
               - total_value / (880_000.0 + total_value)) < 1e-12


def test_empty_portfolio_snapshot_is_flat() -> None:
    """空仓：净值 = 现金/初始资金，持仓明细为空。"""
    nav_row, position_rows = snapshot_day(
        {}, {}, cash=1_000_000.0, total_costs_paid=0.0, day=DATES[0], config=CONFIG
    )
    assert nav_row["nav"] == 1.0 and nav_row["n_holdings"] == 0
    assert position_rows == []
