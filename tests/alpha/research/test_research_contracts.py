from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from vnpy.alpha.research.contracts import (
    validate_research_frame,
    validate_turtle_price_frame,
    validate_turtle_signal_frame,
)


def make_price_frame() -> pl.DataFrame:
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(2)]
    return pl.DataFrame(
        {
            "datetime": dates,
            "vt_symbol": ["AAA.SSE", "AAA.SSE"],
            "open": [10.0, 11.0],
            "high": [11.0, 12.0],
            "low": [9.0, 10.0],
            "close": [10.5, 11.5],
        }
    )


def test_research_contract_reports_missing_columns() -> None:
    result = validate_research_frame(pl.DataFrame({"datetime": [datetime(2024, 1, 1)]}), required_columns={"close"})

    assert not result.passed
    assert result.errors == ["Missing required columns: close"]
    with pytest.raises(ValueError, match="Missing required columns"):
        result.raise_if_failed()


def test_turtle_price_contract_rejects_duplicate_symbol_datetime_keys() -> None:
    frame = pl.concat([make_price_frame(), make_price_frame().head(1)])

    result = validate_turtle_price_frame(frame)

    assert not result.passed
    assert "Duplicate keys" in result.errors[0]


def test_research_contract_reports_unsorted_frames_without_failing_when_allowed() -> None:
    frame = make_price_frame().sort("datetime", descending=True)

    result = validate_turtle_price_frame(frame, fail_on_unsorted=False)

    assert result.passed
    assert result.warnings == ["Frame is not sorted by vt_symbol, datetime"]


def test_research_contract_rejects_null_critical_numeric_columns() -> None:
    frame = make_price_frame().with_columns(pl.lit(None).cast(pl.Float64).alias("close"))

    result = validate_turtle_price_frame(frame)

    assert not result.passed
    assert result.errors == ["Column close has 2 null values"]


def test_signal_contract_records_shift_assumption() -> None:
    frame = make_price_frame().with_columns(pl.lit(1).alias("target_position"))

    result = validate_turtle_signal_frame(frame)

    assert result.passed
    assert result.assumptions == ["Backtest applies a one-bar shift to target_position before earning returns."]
