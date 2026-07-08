from datetime import datetime, timedelta

import polars as pl

from vnpy.alpha.research.turtle_backtest import run_long_only_backtest


def make_signal_data() -> pl.DataFrame:
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(4)]
    return pl.DataFrame(
        {
            "datetime": dates,
            "vt_symbol": ["AAA.SSE"] * 4,
            "close": [10.0, 11.0, 12.0, 13.0],
            "target_position": [0, 1, 1, 0],
        }
    )


def test_backtest_shifts_position_before_earning_return() -> None:
    result = run_long_only_backtest(make_signal_data(), cost_rate=0.0)

    assert result["gross_return"].to_list() == [0.0, 0.0, 12.0 / 11.0 - 1, 13.0 / 12.0 - 1]


def test_backtest_cost_reduces_net_equity() -> None:
    no_cost = run_long_only_backtest(make_signal_data(), cost_rate=0.0)
    with_cost = run_long_only_backtest(make_signal_data(), cost_rate=0.01)

    assert with_cost["net_equity"][-1] < no_cost["net_equity"][-1]
    assert with_cost["cost"].sum() > 0
