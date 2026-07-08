from datetime import datetime, timedelta

import polars as pl

from vnpy.alpha.research.turtle_metrics import calculate_performance_metrics


def test_performance_metrics_calculate_core_values() -> None:
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(4)]
    df = pl.DataFrame(
        {
            "datetime": dates,
            "net_return": [0.01, -0.02, 0.03, 0.01],
            "turnover": [0.0, 0.5, 0.0, 0.2],
        }
    )

    metrics = calculate_performance_metrics(df, annual_days=252)

    assert metrics.total_return > 0
    assert metrics.max_drawdown < 0
    assert metrics.win_rate == 0.75
    assert metrics.average_turnover == 0.175
