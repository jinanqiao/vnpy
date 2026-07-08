from datetime import datetime

import polars as pl
import pytest

from vnpy.alpha.research.data_check import check_price_data, validate_price_data


def make_valid_price_data() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "datetime": [
                datetime(2024, 1, 1),
                datetime(2024, 1, 2),
                datetime(2024, 1, 1),
                datetime(2024, 1, 2),
            ],
            "vt_symbol": ["AAA.SSE", "AAA.SSE", "BBB.SSE", "BBB.SSE"],
            "open": [10.0, 10.5, 20.0, 20.5],
            "high": [11.0, 11.0, 21.0, 21.0],
            "low": [9.5, 10.0, 19.5, 20.0],
            "close": [10.5, 10.8, 20.5, 20.8],
            "volume": [1000.0, 1200.0, 2000.0, 2200.0],
        }
    )


def test_check_price_data_accepts_valid_data() -> None:
    report = check_price_data(make_valid_price_data())

    assert report.passed
    assert report.row_count == 4
    assert report.symbol_count == 2
    assert report.duplicate_rows == 0
    assert report.errors == []
    assert report.symbol_ranges["AAA.SSE"] == (datetime(2024, 1, 1), datetime(2024, 1, 2))


def test_check_price_data_reports_missing_required_column() -> None:
    df = make_valid_price_data().drop("volume")

    report = check_price_data(df)

    assert not report.passed
    assert report.missing_columns == ["volume"]
    assert "Missing required column: volume" in report.errors


def test_check_price_data_reports_duplicate_symbol_datetime() -> None:
    df = pl.concat([make_valid_price_data(), make_valid_price_data().slice(0, 1)])

    report = check_price_data(df)

    assert not report.passed
    assert report.duplicate_rows == 1
    assert "Found 1 duplicate vt_symbol/datetime rows." in report.errors


def test_check_price_data_reports_invalid_prices() -> None:
    df = make_valid_price_data().with_columns(
        pl.when(pl.col("vt_symbol") == "AAA.SSE")
        .then(8.0)
        .otherwise(pl.col("high"))
        .alias("high")
    )

    report = check_price_data(df)

    assert not report.passed
    assert report.price_relation_errors["high_less_than_low"] == 2
    assert report.price_relation_errors["open_outside_high_low"] == 2
    assert report.price_relation_errors["close_outside_high_low"] == 2


def test_validate_price_data_raises_clear_error() -> None:
    df = make_valid_price_data().with_columns(pl.lit(0.0).alias("volume"))

    with pytest.raises(ValueError, match="Column volume has 4 non-positive values"):
        validate_price_data(df)
