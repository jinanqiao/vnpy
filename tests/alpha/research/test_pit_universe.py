from datetime import date, datetime

import polars as pl

from vnpy.alpha.research.pit_universe import (
    PitUniverseConfig,
    build_daily_status,
    build_daily_universe,
    build_universe_layers,
)


def test_build_daily_universe_respects_list_and_delist_dates() -> None:
    symbols = pl.DataFrame(
        {
            "vt_symbol": ["AAA.SZ", "BBB.SZ"],
            "name": ["Alpha", "Beta"],
            "board": ["main", "main"],
            "exchange": ["SZ", "SZ"],
            "list_date": ["20240102", "20240103"],
            "delist_date": ["99999999", "20240103"],
        }
    )
    calendar = pl.DataFrame({"market": ["SZ"] * 3, "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]})
    bars = pl.DataFrame(
        {
            "datetime": [datetime(2024, 1, 2), datetime(2024, 1, 3), datetime(2024, 1, 4)],
            "vt_symbol": ["AAA.SZ", "AAA.SZ", "AAA.SZ"],
        }
    )

    result = build_daily_universe(symbols, calendar, bars)

    assert result.select(["datetime", "vt_symbol"]).to_dicts() == [
        {"datetime": datetime(2024, 1, 2), "vt_symbol": "AAA.SZ"},
        {"datetime": datetime(2024, 1, 3), "vt_symbol": "AAA.SZ"},
        {"datetime": datetime(2024, 1, 3), "vt_symbol": "BBB.SZ"},
        {"datetime": datetime(2024, 1, 4), "vt_symbol": "AAA.SZ"},
    ]


def test_build_daily_status_and_layers() -> None:
    daily_universe = pl.DataFrame(
        {
            "datetime": [datetime(2024, 1, 1), datetime(2024, 1, 2)],
            "vt_symbol": ["AAA.SZ", "AAA.SZ"],
            "name": ["Alpha", "Alpha"],
            "board": ["main", "main"],
            "exchange": ["SZ", "SZ"],
            "list_date": ["20200101", "20200101"],
            "delist_date": ["99999999", "99999999"],
            "effective_list_date": [datetime(2020, 1, 1), datetime(2020, 1, 1)],
            "effective_delist_date": [None, None],
            "in_universe": [True, True],
        }
    )
    bars = pl.DataFrame(
        {
            "datetime": [datetime(2024, 1, 1), datetime(2024, 1, 2)],
            "vt_symbol": ["AAA.SZ", "AAA.SZ"],
            "open": [10.0, 10.5],
            "high": [11.0, 11.0],
            "low": [9.0, 10.0],
            "close": [10.5, 10.8],
            "volume": [1000.0, 1200.0],
            "turnover": [30_000_000.0, 32_000_000.0],
        }
    )
    config = PitUniverseConfig(min_listed_days=1)

    status = build_daily_status(daily_universe, bars, config)
    layers = build_universe_layers(status, config)

    assert status["tradable"].to_list() == [True, True]
    assert layers["research_universe"].height == 2
    assert layers["tradable_universe"].filter(pl.col("in_tradable")).height == 2
    assert layers["execution_universe"].filter(pl.col("in_execution")).height == 2
