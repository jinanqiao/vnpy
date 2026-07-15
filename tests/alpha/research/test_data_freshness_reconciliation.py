from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3

import polars as pl

from vnpy.alpha.research.data_foundation.freshness import evaluate_common_trade_date
from vnpy.alpha.research.data_foundation.metadata import record_quality_check_result
from vnpy.alpha.research.data_foundation.quarantine import quarantine_failed_source_pull


def test_freshness_blocks_when_calendar_does_not_cover_as_of(tmp_path: Path) -> None:
    _write_core_data(tmp_path, calendar_dates=[date(2026, 7, 13)], data_dates=[date(2026, 7, 13)])

    result = evaluate_common_trade_date(tmp_path, as_of=date(2026, 7, 14))

    assert result.status == "fail"
    assert "trading_calendar" in result.blocking_datasets


def test_freshness_blocks_lagging_core_dataset(tmp_path: Path) -> None:
    _write_core_data(
        tmp_path,
        calendar_dates=[date(2026, 7, 13), date(2026, 7, 14)],
        data_dates=[date(2026, 7, 13)],
        adjusted_dates=[date(2026, 7, 14)],
        universe_dates=[date(2026, 7, 14)],
    )

    result = evaluate_common_trade_date(tmp_path, as_of=date(2026, 7, 14))

    assert result.status == "fail"
    assert "daily_bars_raw_price" in result.blocking_datasets


def test_freshness_passes_when_core_datasets_cover_expected_trade_date(tmp_path: Path) -> None:
    _write_core_data(
        tmp_path,
        calendar_dates=[date(2026, 7, 13), date(2026, 7, 14)],
        data_dates=[date(2026, 7, 14)],
    )

    result = evaluate_common_trade_date(tmp_path, as_of=date(2026, 7, 14))

    assert result.status == "pass"
    assert result.common_trade_date == "2026-07-14"


def test_quarantine_keeps_failed_source_outside_promoted_layers(tmp_path: Path) -> None:
    evidence = tmp_path / "partial.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(evidence)

    result = quarantine_failed_source_pull(
        tmp_path,
        source="qmt",
        dataset="daily_bars_raw_price",
        reason="download failed",
        files=[evidence],
    )

    assert Path(result.metadata_path).exists()
    assert "quarantine" in result.quarantine_dir
    assert not (tmp_path / "silver" / "partial.parquet").exists()


def test_quality_check_result_is_recorded_in_sqlite(tmp_path: Path) -> None:
    check_id = record_quality_check_result(
        dataset_name="core_datasets",
        check_name="freshness",
        mode="live",
        severity="blocking",
        status="fail",
        details={"blocking": ["trading_calendar"]},
        state_dir=tmp_path / "state",
    )

    with sqlite3.connect(tmp_path / "state" / "quant_meta.sqlite") as conn:
        row = conn.execute(
            "SELECT check_id, status FROM quality_check_results WHERE check_id = ?",
            (check_id,),
        ).fetchone()

    assert row == (check_id, "fail")


def _write_core_data(
    root: Path,
    *,
    calendar_dates: list[date],
    data_dates: list[date],
    adjusted_dates: list[date] | None = None,
    universe_dates: list[date] | None = None,
) -> None:
    silver = root / "silver"
    gold = root / "gold"
    silver.mkdir(parents=True, exist_ok=True)
    gold.mkdir(parents=True, exist_ok=True)
    adjusted_dates = adjusted_dates or data_dates
    universe_dates = universe_dates or data_dates

    pl.DataFrame({"market": ["SH"] * len(calendar_dates), "trade_date": calendar_dates}).write_parquet(silver / "trading_calendar.parquet")
    _bars(data_dates).write_parquet(silver / "daily_bars_raw_price.parquet")
    _bars(adjusted_dates).write_parquet(silver / "daily_bars_adjusted.parquet")
    pl.DataFrame(
        {
            "datetime": universe_dates,
            "vt_symbol": ["000001.SZ"] * len(universe_dates),
            "in_execution": [True] * len(universe_dates),
        }
    ).write_parquet(gold / "execution_universe.parquet")


def _bars(dates: list[date]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "datetime": dates,
            "vt_symbol": ["000001.SZ"] * len(dates),
            "open": [10.0] * len(dates),
            "high": [10.5] * len(dates),
            "low": [9.8] * len(dates),
            "close": [10.2] * len(dates),
            "volume": [100.0] * len(dates),
            "turnover": [1000.0] * len(dates),
        }
    )
