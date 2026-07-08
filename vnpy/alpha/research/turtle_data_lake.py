from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import json

import polars as pl

try:
    from .data_check import check_price_data
    from .qmt_gateway_data import QmtGatewayConfig, fetch_qmt_daily_bars
except ImportError:  # pragma: no cover - allows direct file loading in scripts/tests
    from data_check import check_price_data  # type: ignore
    from qmt_gateway_data import QmtGatewayConfig, fetch_qmt_daily_bars  # type: ignore


INDEX_SYMBOLS: tuple[str, ...] = (
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "399006.SZ",
    "000001.SH",
)


@dataclass(frozen=True)
class DataLakeSummary:
    """A compact audit summary for one data lake build."""

    created_at: str
    data_root: str
    stock_count: int
    daily_bar_rows: int
    benchmark_rows: int
    universe_rows: int
    start_datetime: str | None
    end_datetime: str | None
    data_quality_passed: bool
    data_quality_errors: list[str]
    data_quality_warnings: list[str]
    failed_symbols: list[str]


def symbols_to_frame(symbols: list[dict[str, Any]], universe_name: str = "all_a") -> pl.DataFrame:
    """Normalize QMT symbol metadata into a flat stock universe table."""
    rows: list[dict[str, Any]] = []
    for item in symbols:
        rows.append(
            {
                "vt_symbol": str(item.get("symbol") or ""),
                "code": str(item.get("code") or ""),
                "exchange": str(item.get("exchange") or ""),
                "name": str(item.get("name") or ""),
                "board": str(item.get("board") or ""),
                "market": str(item.get("market") or ""),
                "status": str(item.get("status") or ""),
                "list_date": str(item.get("list_date") or ""),
                "delist_date": str(item.get("delist_date") or ""),
                "is_trading_now": bool(item.get("is_trading")),
                "pre_close": _float_or_none(item.get("pre_close")),
                "limit_up": _float_or_none(item.get("limit_up")),
                "limit_down": _float_or_none(item.get("limit_down")),
                "float_volume": _float_or_none(item.get("float_volume")),
                "total_volume": _float_or_none(item.get("total_volume")),
                "universe_name": universe_name,
                "source": "qmt_gateway",
                "snapshot_at": datetime.now(),
            }
        )
    return pl.DataFrame(rows).filter(pl.col("vt_symbol") != "").sort("vt_symbol")


def build_daily_tradable_calendar(daily_bars: pl.DataFrame, symbols_df: pl.DataFrame) -> pl.DataFrame:
    """Build a daily tradability table from bars and current QMT instrument metadata."""
    metadata = symbols_df.select(
        [
            "vt_symbol",
            "name",
            "list_date",
            "delist_date",
            "board",
            "limit_up",
            "limit_down",
        ]
    )

    return (
        daily_bars
        .sort(["vt_symbol", "datetime"])
        .with_columns(
            pl.cum_count("datetime").over("vt_symbol").alias("listed_days"),
            (pl.col("turnover") <= 0).alias("is_suspended"),
            pl.col("turnover").rolling_mean(window_size=20, min_samples=1).over("vt_symbol").alias("avg_turnover_20"),
        )
        .join(metadata, on="vt_symbol", how="left")
        .with_columns(
            pl.col("name").str.contains("ST").fill_null(False).alias("is_st"),
            (pl.col("close") >= pl.col("limit_up")).fill_null(False).alias("is_limit_up"),
            (pl.col("close") <= pl.col("limit_down")).fill_null(False).alias("is_limit_down"),
        )
        .with_columns(
            (
                pl.col("is_st").not_()
                & pl.col("is_suspended").not_()
                & (pl.col("listed_days") >= 120)
                & (pl.col("close") >= 2.0)
                & (pl.col("avg_turnover_20") >= 20_000_000.0)
            ).alias("tradable")
        )
        .with_columns(_tradable_reason_expr().alias("reason"))
        .select(
            [
                "datetime",
                "vt_symbol",
                "name",
                "board",
                "listed_days",
                "is_st",
                "is_suspended",
                "is_limit_up",
                "is_limit_down",
                "turnover",
                "avg_turnover_20",
                "tradable",
                "reason",
            ]
        )
    )


def download_bars_for_symbols(
    config: QmtGatewayConfig,
    symbols: list[str],
    count: int,
) -> tuple[pl.DataFrame, list[str]]:
    """Download daily bars symbol by symbol and keep going on failures."""
    frames: list[pl.DataFrame] = []
    failed: list[str] = []
    total = len(symbols)

    for index, symbol in enumerate(symbols, start=1):
        try:
            frame = fetch_qmt_daily_bars(config, symbol, count=count)
        except Exception as exc:
            failed.append(f"{symbol}: {exc}")
            print(f"FAIL {index}/{total} {symbol}: {exc}", flush=True)
            continue
        if frame.is_empty():
            failed.append(f"{symbol}: empty")
            print(f"EMPTY {index}/{total} {symbol}", flush=True)
            continue

        frames.append(frame)
        if index == 1 or index % 50 == 0 or index == total:
            print(f"OK {index}/{total} {symbol}: {frame.height} rows", flush=True)

    if not frames:
        return _empty_daily_bars(), failed

    return pl.concat(frames, how="vertical").sort(["vt_symbol", "datetime"]), failed


def write_data_lake_summary(
    data_root: str | Path,
    symbols_df: pl.DataFrame,
    daily_bars: pl.DataFrame,
    benchmark_df: pl.DataFrame,
    tradable_df: pl.DataFrame,
    failed_symbols: list[str],
) -> Path:
    """Write the current build summary and data quality report."""
    report = check_price_data(daily_bars)
    errors = [error for error in report.errors if not error.startswith("Column volume has")]
    warnings = [*report.warnings, *[error for error in report.errors if error.startswith("Column volume has")]]
    summary = DataLakeSummary(
        created_at=datetime.now().isoformat(timespec="seconds"),
        data_root=str(data_root),
        stock_count=symbols_df.height,
        daily_bar_rows=daily_bars.height,
        benchmark_rows=benchmark_df.height,
        universe_rows=tradable_df.height,
        start_datetime=str(report.start_datetime) if report.start_datetime else None,
        end_datetime=str(report.end_datetime) if report.end_datetime else None,
        data_quality_passed=not errors,
        data_quality_errors=errors,
        data_quality_warnings=warnings,
        failed_symbols=failed_symbols,
    )

    path = Path(data_root) / "quality" / "data_lake_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _tradable_reason_expr() -> pl.Expr:
    return (
        pl.when(pl.col("is_st"))
        .then(pl.lit("st"))
        .when(pl.col("is_suspended"))
        .then(pl.lit("suspended"))
        .when(pl.col("listed_days") < 120)
        .then(pl.lit("listed_days"))
        .when(pl.col("close") < 2.0)
        .then(pl.lit("low_price"))
        .when(pl.col("avg_turnover_20") < 20_000_000.0)
        .then(pl.lit("low_turnover"))
        .otherwise(pl.lit("ok"))
    )


def _empty_daily_bars() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "datetime": pl.Datetime,
            "vt_symbol": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "turnover": pl.Float64,
        }
    )


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
