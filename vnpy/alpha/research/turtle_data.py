from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

try:
    from .data_check import DataQualityReport, check_price_data, validate_price_data
    from .contracts import validate_turtle_price_frame
except ImportError:  # pragma: no cover - allows direct file loading in tests
    from data_check import DataQualityReport, check_price_data, validate_price_data  # type: ignore
    from contracts import validate_turtle_price_frame  # type: ignore


COLUMN_ALIASES: dict[str, str] = {
    "date": "datetime",
    "time": "datetime",
    "symbol": "vt_symbol",
    "code": "vt_symbol",
    "ts_code": "vt_symbol",
    "open_price": "open",
    "high_price": "high",
    "low_price": "low",
    "close_price": "close",
    "vol": "volume",
    "amount": "turnover",
}


@dataclass(frozen=True)
class TurtleDataSummary:
    """Summary saved before trusting a turtle backtest."""

    row_count: int
    symbol_count: int
    start_datetime: str | None
    end_datetime: str | None
    min_rows_per_symbol: int
    max_rows_per_symbol: int
    data_quality_passed: bool
    data_quality_errors: list[str]
    data_quality_warnings: list[str]


def load_price_data(path: str | Path) -> pl.DataFrame:
    """Load OHLCV data from a file or folder of CSV/parquet files."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Data path does not exist: {source}")

    if source.is_dir():
        files = sorted([*source.glob("*.parquet"), *source.glob("*.csv")])
        if not files:
            raise FileNotFoundError(f"No parquet/csv files found in: {source}")
        frames = [_read_one_file(file) for file in files]
        df = pl.concat(frames, how="diagonal_relaxed")
    else:
        df = _read_one_file(source)

    result = normalize_price_data(df)
    validate_turtle_price_frame(result).raise_if_failed()
    return result


def normalize_price_data(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize common OHLCV column names and sort rows."""
    rename_map: dict[str, str] = {
        column: COLUMN_ALIASES[column]
        for column in df.columns
        if column in COLUMN_ALIASES and COLUMN_ALIASES[column] not in df.columns
    }

    result = df.rename(rename_map) if rename_map else df

    if "datetime" in result.columns and result.schema["datetime"] == pl.String:
        result = result.with_columns(pl.col("datetime").str.to_datetime(strict=False))
    elif "datetime" in result.columns:
        result = result.with_columns(pl.col("datetime").cast(pl.Datetime, strict=False))

    return result.sort(["vt_symbol", "datetime"]) if {"vt_symbol", "datetime"}.issubset(result.columns) else result


def summarize_price_data(df: pl.DataFrame) -> TurtleDataSummary:
    """Validate price data and return a compact summary."""
    report: DataQualityReport
    errors: list[str] = []
    warnings: list[str] = []

    try:
        report = validate_price_data(df)
    except ValueError:
        report = check_price_data(df)
        errors = [error for error in report.errors if not error.startswith("Column volume has")]
        warnings = [*report.warnings, *[error for error in report.errors if error.startswith("Column volume has")]]

    counts: pl.DataFrame = (
        df.group_by("vt_symbol").len().select(
            pl.col("len").min().alias("min_rows"),
            pl.col("len").max().alias("max_rows"),
        )
        if "vt_symbol" in df.columns and not df.is_empty()
        else pl.DataFrame({"min_rows": [0], "max_rows": [0]})
    )

    return TurtleDataSummary(
        row_count=df.height,
        symbol_count=report.symbol_count,
        start_datetime=str(report.start_datetime) if report.start_datetime else None,
        end_datetime=str(report.end_datetime) if report.end_datetime else None,
        min_rows_per_symbol=int(counts["min_rows"][0]),
        max_rows_per_symbol=int(counts["max_rows"][0]),
        data_quality_passed=not errors,
        data_quality_errors=errors,
        data_quality_warnings=warnings,
    )


def save_data_summary(summary: TurtleDataSummary, output_path: str | Path) -> Path:
    """Save data summary as JSON."""
    import json

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _read_one_file(path: Path) -> pl.DataFrame:
    if path.suffix == ".parquet":
        return pl.read_parquet(path)
    if path.suffix == ".csv":
        return pl.read_csv(path, try_parse_dates=True)
    raise ValueError(f"Unsupported data file type: {path}")
