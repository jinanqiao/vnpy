from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from vnpy.alpha.research.data_foundation.price_reconciliation import diagnose_price_reconciliation


def test_price_reconciliation_passes_when_prices_match(tmp_path: Path) -> None:
    _write_price_inputs(tmp_path, qmt_close=[10.0, 11.0], akshare_close=[10.0, 11.0])

    result = diagnose_price_reconciliation(tmp_path, recent_days=2, coverage_min=1.0)

    assert result.status == "pass"
    assert result.coverage == 1.0
    assert result.diff_rows == 0
    assert result.normalized_diff_rows == 0


def test_price_reconciliation_allows_constant_adjustment_scale(tmp_path: Path) -> None:
    _write_price_inputs(tmp_path, qmt_close=[10.0, 11.0], akshare_close=[1000.0, 1100.0])

    result = diagnose_price_reconciliation(tmp_path, recent_days=2, coverage_min=1.0)

    assert result.status == "pass"
    assert result.diff_rows == 2
    assert result.normalized_diff_rows == 0
    assert result.unknown_symbol_count == 0
    assert result.classified_path
    assert result.unknown_symbols_path


def test_price_reconciliation_fails_when_coverage_is_low(tmp_path: Path) -> None:
    _write_price_inputs(tmp_path, qmt_close=[10.0, 11.0], akshare_close=[10.0], akshare_dates=[date(2026, 7, 13)])

    result = diagnose_price_reconciliation(tmp_path, recent_days=2, coverage_min=1.0)

    assert result.status == "fail"
    assert result.coverage == 0.5


def test_price_reconciliation_fails_and_outputs_samples_when_diff_exceeds_threshold(tmp_path: Path) -> None:
    _write_price_inputs(tmp_path, qmt_close=[10.0, 11.0], akshare_close=[10.0, 20.0])

    result = diagnose_price_reconciliation(tmp_path, recent_days=2, price_diff_max=0.02)

    assert result.status == "fail"
    assert result.normalized_diff_rows > 0
    assert result.unknown_symbol_count == 1
    assert result.max_samples[0]["vt_symbol"] == "000001.SZ"
    assert result.top_symbols[0]["diff_rows"] == 1
    assert Path(result.classified_path or "").exists()
    assert Path(result.unknown_symbols_path or "").exists()
    assert pl.read_parquet(result.unknown_symbols_path or "").get_column("vt_symbol").to_list() == ["000001.SZ"]


def _write_price_inputs(
    root: Path,
    *,
    qmt_close: list[float],
    akshare_close: list[float],
    akshare_dates: list[date] | None = None,
) -> None:
    silver = root / "silver"
    silver.mkdir(parents=True, exist_ok=True)
    dates = [date(2026, 7, 13), date(2026, 7, 14)]
    pl.DataFrame(
        {
            "datetime": dates[: len(qmt_close)],
            "vt_symbol": ["000001.SZ"] * len(qmt_close),
            "close": qmt_close,
        }
    ).write_parquet(silver / "daily_bars_adjusted.parquet")
    pl.DataFrame(
        {
            "datetime": akshare_dates or dates[: len(akshare_close)],
            "vt_symbol": ["000001.SZ"] * len(akshare_close),
            "close_hfq": akshare_close,
        }
    ).write_parquet(silver / "outstanding_share_turnover.parquet")
