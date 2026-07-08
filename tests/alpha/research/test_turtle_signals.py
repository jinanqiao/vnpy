from datetime import datetime, timedelta

import polars as pl

from vnpy.alpha.research.turtle_signals import generate_turtle_signals


def test_turtle_signals_enter_and_exit_position() -> None:
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(5)]
    df = pl.DataFrame(
        {
            "datetime": dates,
            "vt_symbol": ["AAA.SSE"] * 5,
            "close": [10.0, 11.0, 13.0, 12.0, 8.0],
            "entry_channel": [None, None, 12.0, 14.0, 14.0],
            "exit_channel": [None, None, 9.0, 9.0, 9.0],
        }
    )

    result = generate_turtle_signals(df)

    assert result["entry_signal"].to_list() == [False, False, True, False, False]
    assert result["exit_signal"].to_list() == [False, False, False, False, True]
    assert result["target_position"].to_list() == [0, 0, 1, 1, 0]
