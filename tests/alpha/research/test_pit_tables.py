from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from vnpy.alpha.research.data_foundation import build_data_foundation_layout, build_pit_tables


def test_build_pit_tables_derives_adjust_factors_and_action_candidates(tmp_path: Path) -> None:
    _write_layered_inputs(tmp_path)

    result = build_pit_tables(tmp_path, factor_change_threshold=0.01)

    assert str(tmp_path / "silver" / "adjust_factors.parquet") in result.written
    assert str(tmp_path / "silver" / "corporate_actions_candidates.parquet") in result.written

    factors = pl.read_parquet(tmp_path / "silver" / "adjust_factors.parquet")
    actions = pl.read_parquet(tmp_path / "silver" / "corporate_actions_candidates.parquet")

    assert factors.select(pl.col("adjust_factor").max()).item() == 2.0
    assert actions.height == 1
    assert actions.select("pit_status").item() == "candidate_from_factor_change"


def test_build_pit_tables_prefers_daily_status_for_suspension_and_st(tmp_path: Path) -> None:
    _write_layered_inputs(tmp_path)
    (tmp_path / "universe").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "datetime": [date(2026, 7, 13), date(2026, 7, 14)],
            "vt_symbol": ["000001.SZ", "000001.SZ"],
            "is_suspended": [False, True],
            "is_st": [False, True],
            "name": ["平安银行", "*ST 平安"],
        }
    ).write_parquet(tmp_path / "universe" / "daily_status.parquet")

    result = build_pit_tables(tmp_path)

    assert not any("daily_status" in warning for warning in result.warnings)
    suspension = pl.read_parquet(tmp_path / "silver" / "suspension_daily.parquet")
    st_status = pl.read_parquet(tmp_path / "silver" / "st_status_daily.parquet")

    assert suspension.filter(pl.col("datetime") == date(2026, 7, 14)).select("is_suspended").item() is True
    assert st_status.filter(pl.col("datetime") == date(2026, 7, 14)).select("is_st").item() is True
    assert st_status.select("pit_status").unique().to_series().to_list() == ["derived_from_daily_status"]


def test_build_pit_tables_can_refresh_manifest_with_generated_tables(tmp_path: Path) -> None:
    _write_layered_inputs(tmp_path)
    build_pit_tables(tmp_path)
    build_data_foundation_layout(tmp_path, state_dir=tmp_path / "state")

    manifest = (tmp_path / "manifest" / "data_foundation_manifest.json").read_text(encoding="utf-8")

    assert "adjust_factors" in manifest
    assert "st_status_daily" in manifest


def _write_layered_inputs(root: Path) -> None:
    silver = root / "silver"
    silver.mkdir(parents=True, exist_ok=True)
    dates = [date(2026, 7, 13), date(2026, 7, 14)]
    raw = pl.DataFrame(
        {
            "datetime": dates,
            "vt_symbol": ["000001.SZ", "000001.SZ"],
            "open": [10.0, 10.0],
            "high": [11.0, 11.0],
            "low": [9.0, 9.0],
            "close": [10.0, 10.0],
            "volume": [100.0, 0.0],
            "turnover": [1000.0, 0.0],
        }
    )
    adjusted = raw.with_columns(pl.Series("close", [10.0, 20.0]))
    raw.write_parquet(silver / "daily_bars_raw_price.parquet")
    adjusted.write_parquet(silver / "daily_bars_adjusted.parquet")
    pl.DataFrame(
        {
            "vt_symbol": ["000001.SZ"],
            "name": ["*ST 平安"],
            "limit_up": [11.0],
            "limit_down": [9.0],
        }
    ).write_parquet(silver / "instrument_master_snapshot.parquet")
