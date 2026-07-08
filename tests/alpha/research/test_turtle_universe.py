from datetime import datetime, timedelta
from typing import Any

import polars as pl

from vnpy.alpha.research.turtle_universe import (
    UniverseFilterConfig,
    apply_basic_universe_filters,
    build_sample_universe,
    get_tradable_symbols,
)


def make_universe_input() -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for i in range(3):
        dt = datetime(2024, 1, 1) + timedelta(days=i)
        rows.extend(
            [
                {
                    "datetime": dt,
                    "vt_symbol": "AAA.SSE",
                    "close": 10.0 + i,
                    "volume": 3_000_000.0,
                    "turnover": 30_000_000.0,
                    "is_st": False,
                    "is_suspended": False,
                },
                {
                    "datetime": dt,
                    "vt_symbol": "BBB.SSE",
                    "close": 1.5,
                    "volume": 3_000_000.0,
                    "turnover": 30_000_000.0,
                    "is_st": False,
                    "is_suspended": False,
                },
            ]
        )
    return pl.DataFrame(rows)


def test_basic_universe_filters_mark_tradable_symbols() -> None:
    config = UniverseFilterConfig(min_listed_days=2, min_price=2.0, min_avg_turnover=20_000_000.0, turnover_window=2)

    result = apply_basic_universe_filters(make_universe_input(), config)

    aaa_reasons = result.filter(pl.col("vt_symbol") == "AAA.SSE")["reason"].to_list()
    bbb_reasons = result.filter(pl.col("vt_symbol") == "BBB.SSE")["reason"].to_list()
    assert aaa_reasons == ["listed_days", "ok", "ok"]
    assert bbb_reasons == ["listed_days", "low_price", "low_price"]
    assert get_tradable_symbols(result, datetime(2024, 1, 3)) == ["AAA.SSE"]


def test_build_sample_universe_flags_symbols() -> None:
    df = pl.DataFrame(
        {
            "datetime": [datetime(2024, 1, 1), datetime(2024, 1, 1)],
            "vt_symbol": ["000001.SZ", "UNKNOWN.SZ"],
        }
    )

    result = build_sample_universe(df)

    assert result.sort("vt_symbol")["in_universe"].to_list() == [True, False]
