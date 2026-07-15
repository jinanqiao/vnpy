from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3

import polars as pl
import pytest

from vnpy.alpha.research.data_foundation import (
    DataContextError,
    apply_symbol_quarantine_to_execution_universe,
    build_data_foundation_layout,
    load_data_context,
    quarantine_failed_source_pull,
    run_data_gate,
    validate_data_context,
)
from vnpy.alpha.research.mainline.config import MainlineConfig
from vnpy.alpha.research.mainline_backtest.config import BacktestConfig


def test_build_data_foundation_layout_copies_existing_files(tmp_path: Path) -> None:
    _write_legacy_data(tmp_path)

    result = build_data_foundation_layout(tmp_path, state_dir=tmp_path / "state")

    assert (tmp_path / "silver" / "daily_bars_raw_price.parquet").exists()
    assert (tmp_path / "silver" / "daily_bars_adjusted.parquet").exists()
    assert (tmp_path / "gold" / "execution_universe.parquet").exists()
    assert (tmp_path / "manifest" / "data_foundation_manifest.json").exists()
    assert "silver/daily_bars_raw_price.parquet" in result.copied
    assert Path(result.metadata_path).exists()
    assert Path(result.query_catalog_path).exists()


def test_data_gate_passes_for_fresh_layered_data(tmp_path: Path) -> None:
    _write_legacy_data(tmp_path)
    build_data_foundation_layout(tmp_path, state_dir=tmp_path / "state")

    result = run_data_gate(tmp_path, mode="live", as_of=date(2026, 7, 14))

    assert result.status == "pass"
    assert result.latest_trade_date == "2026-07-14"
    assert not result.blocking_checks


def test_data_gate_blocks_when_layered_files_missing(tmp_path: Path) -> None:
    result = run_data_gate(tmp_path, mode="live", as_of=date(2026, 7, 14))

    assert result.status == "fail"
    assert any(item.name == "required_file_exists" for item in result.blocking_checks)


def test_metadata_store_contains_manifest_and_query_catalog(tmp_path: Path) -> None:
    _write_legacy_data(tmp_path)
    result = build_data_foundation_layout(tmp_path, state_dir=tmp_path / "state")

    with sqlite3.connect(result.metadata_path) as conn:
        version_count = conn.execute("SELECT COUNT(*) FROM data_versions").fetchone()[0]
        dataset_count = conn.execute("SELECT COUNT(*) FROM dataset_snapshots").fetchone()[0]
        query_count = conn.execute("SELECT COUNT(*) FROM query_catalog").fetchone()[0]

    assert version_count == 1
    assert dataset_count >= 4
    assert query_count >= 5


def test_data_context_loads_manifest_and_resolves_core_paths(tmp_path: Path) -> None:
    _write_legacy_data(tmp_path)
    build_data_foundation_layout(tmp_path, state_dir=tmp_path / "state")

    context = load_data_context(tmp_path)

    assert context.version_id
    assert context.latest_trade_date == "2026-07-14"
    assert context.dataset_path("daily_bars_raw_price") == tmp_path / "silver" / "daily_bars_raw_price.parquet"


def test_data_context_blocks_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(DataContextError, match="缺少数据 manifest"):
        load_data_context(tmp_path)


def test_validate_data_context_runs_gate(tmp_path: Path) -> None:
    _write_legacy_data(tmp_path)
    build_data_foundation_layout(tmp_path, state_dir=tmp_path / "state")

    context, gate = validate_data_context(tmp_path, mode="live", as_of=date(2026, 7, 14))

    assert context.latest_trade_date == "2026-07-14"
    assert gate.status == "pass"


def test_data_gate_blocks_when_qmt_akshare_price_diff_exceeds_threshold(tmp_path: Path) -> None:
    _write_legacy_data(tmp_path)
    build_data_foundation_layout(tmp_path, state_dir=tmp_path / "state")
    akshare_path = tmp_path / "silver" / "outstanding_share_turnover.parquet"
    (
        pl.read_parquet(akshare_path)
        .with_columns(
            pl.when(pl.col("datetime") == date(2026, 7, 14))
            .then(pl.col("close_hfq") * 1.10)
            .otherwise(pl.col("close_hfq"))
            .alias("close_hfq")
        )
        .write_parquet(akshare_path)
    )

    result = run_data_gate(tmp_path, mode="live", as_of=date(2026, 7, 14))

    assert result.status == "fail"
    assert any(item.name == "qmt_akshare_price_diff" for item in result.blocking_checks)


def test_unknown_reconciliation_symbols_are_blocked_until_quarantined(tmp_path: Path) -> None:
    _write_legacy_data(tmp_path)
    build_data_foundation_layout(tmp_path, state_dir=tmp_path / "state")
    akshare_path = tmp_path / "silver" / "outstanding_share_turnover.parquet"
    (
        pl.read_parquet(akshare_path)
        .with_columns(
            pl.when((pl.col("datetime") == date(2026, 7, 14)) & (pl.col("vt_symbol") == "000001.SZ"))
            .then(pl.col("close_hfq") * 1.10)
            .otherwise(pl.col("close_hfq"))
            .alias("close_hfq")
        )
        .write_parquet(akshare_path)
    )

    blocked = run_data_gate(tmp_path, mode="live", as_of=date(2026, 7, 14))

    assert any(item.name == "qmt_akshare_unknown_quarantine" for item in blocked.blocking_checks)

    quarantine = apply_symbol_quarantine_to_execution_universe(tmp_path)
    allowed = run_data_gate(tmp_path, mode="live", as_of=date(2026, 7, 14))

    assert quarantine["removed_symbols"] == 1
    assert not any(item.name == "qmt_akshare_unknown_quarantine" for item in allowed.blocking_checks)


def test_manifest_version_file_is_immutable(tmp_path: Path) -> None:
    _write_legacy_data(tmp_path)
    result = build_data_foundation_layout(tmp_path, state_dir=tmp_path / "state")
    version_path = Path(result.manifest_version_path)

    assert version_path.exists()
    with pytest.raises(FileExistsError):
        from vnpy.alpha.research.data_foundation.manifest import build_manifest
        from vnpy.alpha.research.data_foundation.layout import mapping_specs

        manifest = build_manifest(tmp_path, mapping_specs(), version_label="data-foundation")
        object.__setattr__(manifest, "version_id", version_path.stem)
        manifest.write_version_json(version_path)


def test_quarantine_failed_source_pull_writes_metadata_and_copies_files(tmp_path: Path) -> None:
    evidence = tmp_path / "failed.json"
    evidence.write_text('{"error": "timeout"}', encoding="utf-8")

    result = quarantine_failed_source_pull(
        tmp_path,
        source="akshare",
        dataset="daily bars",
        reason="timeout while downloading",
        files=[evidence],
    )

    assert Path(result.metadata_path).exists()
    assert len(result.copied_files) == 1
    assert "daily_bars" in result.quarantine_dir


def test_default_configs_use_layered_paths() -> None:
    mainline = MainlineConfig()
    backtest = BacktestConfig()

    assert mainline.daily_bars_file == "silver/daily_bars_raw_price.parquet"
    assert mainline.execution_universe_file == "gold/execution_universe.parquet"
    assert backtest.adjusted_bars_file == "silver/daily_bars_adjusted.parquet"
    assert backtest.trading_dates_file == "silver/trading_calendar.parquet"


def _write_legacy_data(root: Path) -> None:
    for folder in ["normalized", "benchmark", "calendar", "universe", "sector", "akshare"]:
        (root / folder).mkdir(parents=True, exist_ok=True)

    dates = [date(2026, 7, 13), date(2026, 7, 14)]
    bars = pl.DataFrame(
        {
            "datetime": dates * 2,
            "vt_symbol": ["000001.SZ", "000001.SZ", "600000.SH", "600000.SH"],
            "open": [10.0, 10.1, 20.0, 20.1],
            "high": [10.5, 10.6, 20.5, 20.6],
            "low": [9.8, 9.9, 19.8, 19.9],
            "close": [10.2, 10.3, 20.2, 20.3],
            "volume": [100.0, 101.0, 200.0, 201.0],
            "turnover": [1000.0, 1010.0, 4000.0, 4010.0],
        }
    )
    bars.write_parquet(root / "normalized" / "daily_bars_all_a.parquet")
    bars.write_parquet(root / "normalized" / "daily_bars_all_a_adjusted.parquet")

    pl.DataFrame(
        {
            "datetime": dates,
            "index_symbol": ["000300.SH", "000300.SH"],
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
        }
    ).write_parquet(root / "benchmark" / "index_daily.parquet")
    pl.DataFrame(
        {
            "market": ["SH", "SH"],
            "trade_date": dates,
            "source": ["test", "test"],
        }
    ).write_parquet(root / "calendar" / "trading_dates.parquet")
    pl.DataFrame(
        {
            "datetime": dates * 2,
            "vt_symbol": ["000001.SZ", "000001.SZ", "600000.SH", "600000.SH"],
            "in_execution": [True, True, True, True],
        }
    ).write_parquet(root / "universe" / "execution_universe.parquet")
    pl.DataFrame(
        {
            "datetime": dates * 2,
            "vt_symbol": ["000001.SZ", "000001.SZ", "600000.SH", "600000.SH"],
            "in_research": [True, True, True, True],
            "research_reason": ["ok", "ok", "ok", "ok"],
        }
    ).write_parquet(root / "universe" / "research_universe.parquet")
    pl.DataFrame(
        {
            "vt_symbol": ["000001.SZ", "600000.SH"],
            "name": ["A", "B"],
            "snapshot_at": [date(2026, 7, 14), date(2026, 7, 14)],
        }
    ).write_parquet(root / "universe" / "all_a_symbols.parquet")
    pl.DataFrame(
        {
            "sector": ["GICS1S", "GICS1S"],
            "vt_symbol": ["000001.SZ", "600000.SH"],
            "snapshot_at": [date(2026, 7, 14), date(2026, 7, 14)],
        }
    ).write_parquet(root / "sector" / "sector_members.parquet")
    pl.DataFrame(
        {
            "sector": ["SW1S"],
            "vt_symbol": ["000001.SZ"],
            "snapshot_at": [date(2026, 7, 14)],
        }
    ).write_parquet(root / "sector" / "sw1_members.parquet")
    pl.DataFrame(
        {
            "datetime": dates * 2,
            "vt_symbol": ["000001.SZ", "000001.SZ", "600000.SH", "600000.SH"],
            "close_hfq": [10.2, 10.3, 20.2, 20.3],
        }
    ).write_parquet(root / "akshare" / "daily_bars_outstanding.parquet")
    pl.DataFrame({"vt_symbol": ["000001.SZ"], "report_date": ["2026-06-30"], "announce_date": ["2026-07-10"]}).write_parquet(root / "akshare" / "financial_reports.parquet")
    pl.DataFrame({"vt_symbol": ["000001.SZ"], "report_date": ["2026-06-30"]}).write_parquet(root / "akshare" / "financial_indicators.parquet")
