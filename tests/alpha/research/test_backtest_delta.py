"""009 差量调仓测试：连任持仓保留不动，只交易名单差集。"""

from datetime import date
from pathlib import Path

import polars as pl

from vnpy.alpha.research.mainline_backtest.config import BacktestConfig
from vnpy.alpha.research.mainline_backtest.pipeline import run_mainline_backtest
from backtest_fixtures import (
    make_benchmark,
    make_business_dates,
    make_execution_universe,
    make_flat_dual_bars,
    make_selection,
    write_backtest_lake,
)

# 两期信号：HOLD.SZ 连任两期，OUT.SZ 第 2 期出局，NEW.SZ 第 2 期新进
DATES = make_business_dates(date(2025, 1, 1), 65)
PERIOD_1, PERIOD_2 = DATES[22], DATES[42]   # 1 月末、2 月末
SELECTION_ROWS = [
    (PERIOD_1, "HOLD.SZ", "GICS1A"),
    (PERIOD_1, "OUT.SZ", "GICS1A"),
    (PERIOD_2, "HOLD.SZ", "GICS1A"),
    (PERIOD_2, "NEW.SZ", "GICS1A"),
]
SYMBOLS = ["HOLD.SZ", "OUT.SZ", "NEW.SZ"]


def build_lake(tmp_path: Path, **config_overrides) -> BacktestConfig:
    adjusted, unadjusted = [], []
    for vt_symbol in SYMBOLS:
        adj, raw = make_flat_dual_bars(vt_symbol, DATES, 10.0)
        adjusted.append(adj)
        unadjusted.append(raw)
    selection_path = write_backtest_lake(
        tmp_path / "data",
        pl.concat(adjusted), pl.concat(unadjusted),
        make_selection(SELECTION_ROWS),
        make_execution_universe(DATES, SYMBOLS),
        make_benchmark(DATES),
        DATES,
    )
    return BacktestConfig(
        selection_path=str(selection_path),
        data_dir=str(tmp_path / "data"),
        output_dir=str(tmp_path / "outputs"),
        name="delta_test",
        **config_overrides,
    )


def test_delta_rebalance_trades_only_the_diff(tmp_path: Path) -> None:
    """差量模式：第 2 期只卖 OUT、只买 NEW，连任的 HOLD 无任何交易记录。"""
    config = build_lake(tmp_path, delta_rebalance_enabled=True)
    result = run_mainline_backtest(config)
    trades = result["trades"]

    second = trades.filter(pl.col("signal_date") == PERIOD_2)
    assert set(second.get_column("vt_symbol").to_list()) == {"NEW.SZ"}
    assert second.filter((pl.col("side") == "buy") & (pl.col("status") == "filled")).height == 1

    # OUT.SZ 的卖出记录挂在第 1 期 signal_date 上（卖的是第 1 期建的仓）
    out_sells = trades.filter((pl.col("vt_symbol") == "OUT.SZ") & (pl.col("side") == "sell"))
    assert out_sells.height == 1
    assert out_sells.row(0, named=True)["executed_date"] == next(d for d in DATES if d > PERIOD_2)

    # HOLD.SZ 全程只有第 1 期一笔买入，没有"卖了再买回"
    hold_trades = trades.filter(pl.col("vt_symbol") == "HOLD.SZ")
    assert hold_trades.height == 1
    assert hold_trades.row(0, named=True)["side"] == "buy"

    # 第 2 期执行日之后 HOLD 与 NEW 都在持仓里
    exec_day_2 = next(d for d in DATES if d > PERIOD_2)
    held = result["positions"].filter(pl.col("datetime") == exec_day_2)
    assert set(held.get_column("vt_symbol").to_list()) == {"HOLD.SZ", "NEW.SZ"}


def test_delta_rebalance_saves_costs(tmp_path: Path) -> None:
    """同一份信号，差量模式的累计交易成本严格低于基线全卖全买。"""
    baseline = run_mainline_backtest(build_lake(tmp_path / "a"))
    delta = run_mainline_backtest(build_lake(tmp_path / "b", delta_rebalance_enabled=True))

    def total_costs(trades: pl.DataFrame) -> float:
        filled = trades.filter(pl.col("status").is_in(["filled", "forced"]))
        return float(
            (filled.get_column("commission") + filled.get_column("stamp_tax")
             + filled.get_column("slippage_cost")).sum()
        )

    assert total_costs(delta["trades"]) < total_costs(baseline["trades"])
    # 价格恒定的合成市场里，成本省下来的钱直接体现为更高净值
    assert delta["metrics"]["total_return"] > baseline["metrics"]["total_return"]


def test_delta_off_matches_previous_behavior(tmp_path: Path) -> None:
    """回归保护：差量开关关闭时与基线逐字节一致。"""
    run_a = run_mainline_backtest(build_lake(tmp_path / "a"))
    run_b = run_mainline_backtest(build_lake(tmp_path / "b", delta_rebalance_enabled=False))
    for filename in ["nav.parquet", "positions.parquet", "trades.parquet"]:
        assert (run_a["output_dir"] / filename).read_bytes() == (run_b["output_dir"] / filename).read_bytes()
