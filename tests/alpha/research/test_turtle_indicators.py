from datetime import datetime, timedelta

import polars as pl

from vnpy.alpha.research.turtle_indicators import calculate_atr, calculate_donchian_channels


def make_price_data() -> pl.DataFrame:
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(5)]
    return pl.DataFrame(
        {
            "datetime": dates,
            "vt_symbol": ["AAA.SSE"] * 5,
            "open": [10.0, 11.0, 12.0, 13.0, 14.0],
            "high": [11.0, 12.0, 13.0, 14.0, 15.0],
            "low": [9.0, 10.0, 11.0, 12.0, 13.0],
            "close": [10.0, 11.0, 12.0, 13.0, 14.0],
            "volume": [1000.0] * 5,
        }
    )


def test_donchian_channels_use_past_prices_only() -> None:
    result = calculate_donchian_channels(make_price_data(), entry_window=2, exit_window=2)

    assert result["entry_channel"].to_list() == [None, None, 12.0, 13.0, 14.0]
    assert result["exit_channel"].to_list() == [None, None, 9.0, 10.0, 11.0]


def test_atr_uses_true_range() -> None:
    result = calculate_atr(make_price_data(), atr_window=2)

    assert result["true_range"].to_list() == [2.0, 2.0, 2.0, 2.0, 2.0]
    assert result["atr"].to_list() == [None, 2.0, 2.0, 2.0, 2.0]
