"""S15 单股最大权重上限测试：默认关闭时逐字节回归，打开时目标金额被 cap。

钉的是 S15 spec：
- max_position_weight=0（默认）→ 等权 equity/N，与 002 基线逐字节一致
- max_position_weight>0 且 equity/N > cap → 目标金额被 clip 到 cap*equity；
  剩余现金保留（不重新分摊到其他股票）
"""

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


# ---- 场景：一期信号选 30 只等权股票；价格恒定 10 元 ----
# equity=1_000_000，N=30 → 每股等权目标 33_333.33 元
# 打开 cap=0.02 → 每股上限 20_000 元（少 13_333 元现金留下不动）
DATES = make_business_dates(date(2025, 1, 1), 40)
SIGNAL_DATE = DATES[19]                            # 第 20 个交易日为信号日
SYMBOLS = [f"S{index:02d}.SZ" for index in range(30)]
SELECTION_ROWS = [(SIGNAL_DATE, vt, "GICS1A") for vt in SYMBOLS]


def build_lake(tmp_path: Path, **overrides) -> BacktestConfig:
    """把 30 只价格恒定的股票和 1 期等权信号写进临时数据湖。"""
    adjusted_dfs, unadjusted_dfs = [], []
    for vt in SYMBOLS:
        adj, raw = make_flat_dual_bars(vt, DATES, 10.0)
        adjusted_dfs.append(adj)
        unadjusted_dfs.append(raw)
    selection_path = write_backtest_lake(
        tmp_path / "data",
        pl.concat(adjusted_dfs), pl.concat(unadjusted_dfs),
        make_selection(SELECTION_ROWS),
        make_execution_universe(DATES, SYMBOLS),
        make_benchmark(DATES),
        DATES,
    )
    return BacktestConfig(
        selection_path=str(selection_path),
        data_dir=str(tmp_path / "data"),
        output_dir=str(tmp_path / "outputs"),
        name="s15_test",
        **overrides,
    )


def test_max_position_weight_off_matches_baseline(tmp_path: Path) -> None:
    """回归保护：max_position_weight=0（默认）与显式传 0 逐字节一致。"""
    run_a = run_mainline_backtest(build_lake(tmp_path / "a"))
    run_b = run_mainline_backtest(build_lake(tmp_path / "b", max_position_weight=0.0))
    for filename in ["nav.parquet", "positions.parquet", "trades.parquet"]:
        assert (run_a["output_dir"] / filename).read_bytes() == (run_b["output_dir"] / filename).read_bytes()


def test_max_position_weight_caps_target_amount(tmp_path: Path) -> None:
    """打开 cap=0.02：30 只等权理论 3.33% → 目标金额被 clip 到 2%（=20_000 元）。

    验证方法：cap 打开后单笔买入的实际现金流出 <= cap*equity=20_000，
    且 filled=30 的成交都成功执行；未 cap 时 filled 的每笔应接近 33_333。
    """
    equity = 1_000_000.0
    capped = run_mainline_backtest(build_lake(
        tmp_path / "cap",
        initial_capital=equity,
        max_position_weight=0.02,
    ))
    baseline = run_mainline_backtest(build_lake(
        tmp_path / "base",
        initial_capital=equity,
    ))

    def filled_buy_amounts(trades: pl.DataFrame) -> list[float]:
        rows = trades.filter(
            (pl.col("signal_date") == SIGNAL_DATE)
            & (pl.col("side") == "buy") & (pl.col("status") == "filled")
        )
        return rows.get_column("gross_amount").to_list()

    capped_amounts = filled_buy_amounts(capped["trades"])
    baseline_amounts = filled_buy_amounts(baseline["trades"])
    assert len(capped_amounts) == 30
    assert len(baseline_amounts) == 30

    # 交易细节：price=10 元、slippage=0.001 → exec_price=10.01；佣金 0.00025；lot=100
    # 基线目标 33_333 → 每笔可买 33 手 = 3300 股 → gross=33_033
    # cap 目标 20_000 → 每笔可买 19 手 = 1900 股 → gross=19_019
    for amount in baseline_amounts:
        assert 30_000 <= amount <= 34_000, f"基线每笔 gross 应约 33_033，实际 {amount}"
    for amount in capped_amounts:
        # cap 后每笔 gross <= 20_000 * (1 + slippage)
        assert amount <= 20_020.0 + 1e-6, f"cap 后每笔应 <= 20_020，实际 {amount}"
        assert 18_000 <= amount, f"cap 后每笔仍应正常买入（gross≈19_019），实际 {amount}"

    # cap 后现金留存更多（每股少花约 14_000 → 30 只共约 40 万现金余下）
    capped_end_cash = capped["nav"].get_column("cash").item(-1)
    baseline_end_cash = baseline["nav"].get_column("cash").item(-1)
    assert capped_end_cash > baseline_end_cash + 300_000


def test_max_position_weight_validation() -> None:
    """非法值直接报错（负数或超过 1）。"""
    import pytest

    with pytest.raises(ValueError, match="max_position_weight"):
        BacktestConfig(max_position_weight=-0.01)
    with pytest.raises(ValueError, match="max_position_weight"):
        BacktestConfig(max_position_weight=1.5)
    # 边界 0 和 1 合法
    BacktestConfig(max_position_weight=0.0)
    BacktestConfig(max_position_weight=1.0)
