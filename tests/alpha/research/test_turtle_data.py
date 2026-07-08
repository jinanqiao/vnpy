from datetime import datetime
from pathlib import Path

import polars as pl

from vnpy.alpha.research.turtle_data import load_price_data, normalize_price_data, summarize_price_data


def test_normalize_price_data_renames_common_columns() -> None:
    raw = pl.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-01"],
            "symbol": ["AAA.SSE", "AAA.SSE"],
            "open_price": [11.0, 10.0],
            "high_price": [12.0, 11.0],
            "low_price": [10.0, 9.0],
            "close_price": [11.5, 10.5],
            "vol": [1200.0, 1000.0],
        }
    )

    result = normalize_price_data(raw)

    assert result.columns == ["datetime", "vt_symbol", "open", "high", "low", "close", "volume"]
    assert result["datetime"].to_list() == [datetime(2024, 1, 1), datetime(2024, 1, 2)]


def test_summarize_price_data_keeps_errors_without_raising() -> None:
    invalid = pl.DataFrame(
        {
            "datetime": [datetime(2024, 1, 1)],
            "vt_symbol": ["AAA.SSE"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [0.0],
        }
    )

    summary = summarize_price_data(invalid)

    assert summary.data_quality_passed
    assert summary.row_count == 1
    assert summary.symbol_count == 1
    assert summary.data_quality_errors == []
    assert summary.data_quality_warnings == ["Column volume has 1 non-positive values."]


def test_load_price_data_reads_folder_of_csv_files(tmp_path: Path) -> None:
    first = tmp_path / "part1.csv"
    second = tmp_path / "part2.csv"
    first.write_text(
        "date,symbol,open,high,low,close,volume\n"
        "2024-01-02,AAA.SSE,11,12,10,11.5,1200\n",
        encoding="utf-8",
    )
    second.write_text(
        "date,symbol,open,high,low,close,volume\n"
        "2024-01-01,AAA.SSE,10,11,9,10.5,1000\n",
        encoding="utf-8",
    )

    result = load_price_data(tmp_path)

    assert result.height == 2
    assert result["close"].to_list() == [10.5, 11.5]
