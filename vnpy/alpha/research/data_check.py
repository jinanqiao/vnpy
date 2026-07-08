from dataclasses import dataclass
from datetime import datetime
from typing import cast

import polars as pl


REQUIRED_PRICE_COLUMNS: tuple[str, ...] = (
    "datetime",
    "vt_symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
)

PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")


@dataclass(frozen=True)
class DataQualityReport:
    """Data quality summary for daily or minute OHLCV data."""

    row_count: int
    symbol_count: int
    start_datetime: datetime | None
    end_datetime: datetime | None
    is_sorted: bool
    missing_columns: list[str]
    duplicate_rows: int
    null_counts: dict[str, int]
    nan_counts: dict[str, int]
    non_positive_counts: dict[str, int]
    price_relation_errors: dict[str, int]
    symbol_ranges: dict[str, tuple[datetime, datetime]]
    warnings: list[str]
    errors: list[str]

    @property
    def passed(self) -> bool:
        """Return True when no blocking data problem was found."""
        return not self.errors


def check_price_data(
    df: pl.DataFrame,
    required_columns: tuple[str, ...] = REQUIRED_PRICE_COLUMNS,
) -> DataQualityReport:
    """
    Inspect OHLCV data before feature calculation or backtesting.

    The function does not modify input data. It only returns a report that can
    be printed in notebooks, stored with experiments, or used by validation.
    """
    missing_columns: list[str] = [column for column in required_columns if column not in df.columns]
    errors: list[str] = []
    warnings: list[str] = []

    for column in missing_columns:
        errors.append(f"Missing required column: {column}")

    row_count: int = df.height
    symbol_count: int = _count_symbols(df)
    start_datetime, end_datetime = _get_datetime_range(df)

    is_sorted: bool = _is_sorted_by_symbol_and_time(df)
    if not is_sorted:
        warnings.append("Rows are not sorted by vt_symbol and datetime.")

    duplicate_rows: int = _count_duplicate_symbol_datetimes(df)
    if duplicate_rows:
        errors.append(f"Found {duplicate_rows} duplicate vt_symbol/datetime rows.")

    null_counts: dict[str, int] = _count_nulls(df)
    for column, count in null_counts.items():
        if column in required_columns and count:
            errors.append(f"Column {column} has {count} null values.")

    nan_counts: dict[str, int] = _count_nans(df)
    for column, count in nan_counts.items():
        if column in required_columns and count:
            errors.append(f"Column {column} has {count} NaN values.")

    non_positive_counts: dict[str, int] = _count_non_positive_values(df)
    for column, count in non_positive_counts.items():
        if count:
            errors.append(f"Column {column} has {count} non-positive values.")

    price_relation_errors: dict[str, int] = _count_price_relation_errors(df)
    for name, count in price_relation_errors.items():
        if count:
            errors.append(f"Price relation check failed for {name}: {count} rows.")

    symbol_ranges: dict[str, tuple[datetime, datetime]] = _get_symbol_ranges(df)

    if row_count == 0:
        errors.append("DataFrame is empty.")

    return DataQualityReport(
        row_count=row_count,
        symbol_count=symbol_count,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        is_sorted=is_sorted,
        missing_columns=missing_columns,
        duplicate_rows=duplicate_rows,
        null_counts=null_counts,
        nan_counts=nan_counts,
        non_positive_counts=non_positive_counts,
        price_relation_errors=price_relation_errors,
        symbol_ranges=symbol_ranges,
        warnings=warnings,
        errors=errors,
    )


def validate_price_data(df: pl.DataFrame) -> DataQualityReport:
    """
    Return a data quality report, or raise ValueError for blocking problems.

    Use this at the beginning of a repeatable research or backtest pipeline.
    """
    report: DataQualityReport = check_price_data(df)
    if not report.passed:
        message: str = "Price data validation failed:\n" + "\n".join(f"- {error}" for error in report.errors)
        raise ValueError(message)

    return report


def _count_symbols(df: pl.DataFrame) -> int:
    if "vt_symbol" not in df.columns or df.is_empty():
        return 0
    return cast(int, df.select(pl.col("vt_symbol").n_unique()).item())


def _get_datetime_range(df: pl.DataFrame) -> tuple[datetime | None, datetime | None]:
    if "datetime" not in df.columns or df.is_empty():
        return None, None

    start = cast(datetime | None, df.select(pl.col("datetime").min()).item())
    end = cast(datetime | None, df.select(pl.col("datetime").max()).item())
    return start, end


def _is_sorted_by_symbol_and_time(df: pl.DataFrame) -> bool:
    if not {"vt_symbol", "datetime"}.issubset(df.columns) or df.is_empty():
        return True

    current: pl.DataFrame = df.select(["vt_symbol", "datetime"])
    expected: pl.DataFrame = df.sort(["vt_symbol", "datetime"]).select(["vt_symbol", "datetime"])
    return bool(current.equals(expected))


def _count_duplicate_symbol_datetimes(df: pl.DataFrame) -> int:
    if not {"vt_symbol", "datetime"}.issubset(df.columns) or df.is_empty():
        return 0

    duplicates: pl.DataFrame = (
        df.group_by(["vt_symbol", "datetime"])
        .len()
        .filter(pl.col("len") > 1)
    )
    return int(duplicates.height)


def _count_nulls(df: pl.DataFrame) -> dict[str, int]:
    if df.is_empty():
        return {column: 0 for column in df.columns}

    row: dict[str, int] = cast(dict[str, int], df.null_count().row(0, named=True))
    return row


def _count_nans(df: pl.DataFrame) -> dict[str, int]:
    result: dict[str, int] = {}

    for column, dtype in df.schema.items():
        if dtype.is_float():
            count: int = df.select(pl.col(column).is_nan().sum()).item()
            result[column] = count
        else:
            result[column] = 0

    return result


def _count_non_positive_values(df: pl.DataFrame) -> dict[str, int]:
    result: dict[str, int] = {}

    for column in [*PRICE_COLUMNS, "volume"]:
        if column not in df.columns:
            continue

        count: int = df.filter(pl.col(column) <= 0).height
        result[column] = count

    return result


def _count_price_relation_errors(df: pl.DataFrame) -> dict[str, int]:
    result: dict[str, int] = {}
    needed_columns: set[str] = {"open", "high", "low", "close"}

    if not needed_columns.issubset(df.columns):
        return result

    result["high_less_than_low"] = df.filter(pl.col("high") < pl.col("low")).height
    result["open_outside_high_low"] = df.filter(
        (pl.col("open") > pl.col("high")) | (pl.col("open") < pl.col("low"))
    ).height
    result["close_outside_high_low"] = df.filter(
        (pl.col("close") > pl.col("high")) | (pl.col("close") < pl.col("low"))
    ).height

    return result


def _get_symbol_ranges(df: pl.DataFrame) -> dict[str, tuple[datetime, datetime]]:
    if not {"vt_symbol", "datetime"}.issubset(df.columns) or df.is_empty():
        return {}

    ranges: dict[str, tuple[datetime, datetime]] = {}
    range_df: pl.DataFrame = (
        df.group_by("vt_symbol")
        .agg(
            pl.col("datetime").min().alias("start"),
            pl.col("datetime").max().alias("end"),
        )
        .sort("vt_symbol")
    )

    for row in range_df.iter_rows(named=True):
        symbol: str = cast(str, row["vt_symbol"])
        start: datetime = cast(datetime, row["start"])
        end: datetime = cast(datetime, row["end"])
        ranges[symbol] = (start, end)

    return ranges
