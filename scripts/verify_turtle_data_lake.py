"""Verify generated turtle data lake files and write a readable report."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl


DATA_ROOT = Path("data")
REPORT_PATH = DATA_ROOT / "quality" / "data_lake_verification.md"


def main() -> None:
    sections: list[str] = ["# Turtle Data Lake Verification", ""]

    daily_path = _first_existing(
        DATA_ROOT / "normalized" / "daily_bars_all_a.parquet",
        DATA_ROOT / "normalized" / "daily_bars_hs300.parquet",
        DATA_ROOT / "normalized" / "daily_bars.parquet",
    )
    benchmark_path = DATA_ROOT / "benchmark" / "index_daily.parquet"
    symbols_path = DATA_ROOT / "universe" / "all_a_symbols.parquet"
    tradable_path = _first_existing(
        DATA_ROOT / "universe" / "tradable_calendar_all_a.parquet",
        DATA_ROOT / "universe" / "tradable_calendar_hs300.parquet",
        DATA_ROOT / "universe" / "tradable_calendar.parquet",
    )
    index_members_path = DATA_ROOT / "universe" / "index_members.parquet"
    summary_path = DATA_ROOT / "quality" / "data_lake_summary.json"

    if symbols_path.exists():
        symbols = pl.read_parquet(symbols_path)
        sections += _symbol_section(symbols_path, symbols)
    if index_members_path.exists():
        members = pl.read_parquet(index_members_path)
        sections += _index_member_section(index_members_path, members)
    if daily_path and daily_path.exists():
        daily = pl.read_parquet(daily_path)
        sections += _daily_section(daily_path, daily)
    if tradable_path and tradable_path.exists():
        tradable = pl.read_parquet(tradable_path)
        sections += _tradable_section(tradable_path, tradable)
    if benchmark_path.exists():
        benchmark = pl.read_parquet(benchmark_path)
        sections += _benchmark_section(benchmark_path, benchmark)
    if summary_path.exists():
        sections += ["## Build Summary", "", "```json", summary_path.read_text(encoding="utf-8"), "```", ""]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(sections), encoding="utf-8")
    print(f"Verification report saved to {REPORT_PATH}")


def _symbol_section(path: Path, df: pl.DataFrame) -> list[str]:
    st_count = df.filter(pl.col("name").str.contains("ST")).height if "name" in df.columns else 0
    return [
        "## Stock Universe",
        "",
        f"- File: `{path}`",
        f"- Rows: `{df.height}`",
        f"- Unique symbols: `{df.select(pl.col('vt_symbol').n_unique()).item()}`",
        f"- ST-like names: `{st_count}`",
        "",
    ]


def _index_member_section(path: Path, df: pl.DataFrame) -> list[str]:
    by_name = df.group_by("universe_name").len().sort("universe_name").to_dicts() if not df.is_empty() else []
    return [
        "## Index Members",
        "",
        f"- File: `{path}`",
        f"- Rows: `{df.height}`",
        f"- Counts: `{json.dumps(by_name, ensure_ascii=False)}`",
        "",
    ]


def _daily_section(path: Path, df: pl.DataFrame) -> list[str]:
    date_range = df.select(pl.col("datetime").min().alias("start"), pl.col("datetime").max().alias("end")).to_dicts()[0]
    zero_volume = df.filter(pl.col("volume") <= 0).height
    duplicate_rows = df.group_by(["vt_symbol", "datetime"]).len().filter(pl.col("len") > 1).height
    return [
        "## Daily Bars",
        "",
        f"- File: `{path}`",
        f"- Rows: `{df.height}`",
        f"- Unique symbols: `{df.select(pl.col('vt_symbol').n_unique()).item()}`",
        f"- Date range: `{date_range['start']}` to `{date_range['end']}`",
        f"- Duplicate symbol/date rows: `{duplicate_rows}`",
        f"- Zero-volume rows: `{zero_volume}`",
        "",
    ]


def _tradable_section(path: Path, df: pl.DataFrame) -> list[str]:
    reason_counts = df.group_by("reason").len().sort("reason").to_dicts() if not df.is_empty() else []
    return [
        "## Tradable Calendar",
        "",
        f"- File: `{path}`",
        f"- Rows: `{df.height}`",
        f"- Tradable rows: `{df.filter(pl.col('tradable')).height}`",
        f"- Reason counts: `{json.dumps(reason_counts, ensure_ascii=False)}`",
        "",
    ]


def _benchmark_section(path: Path, df: pl.DataFrame) -> list[str]:
    date_range = df.select(pl.col("datetime").min().alias("start"), pl.col("datetime").max().alias("end")).to_dicts()[0]
    return [
        "## Benchmark",
        "",
        f"- File: `{path}`",
        f"- Rows: `{df.height}`",
        f"- Index count: `{df.select(pl.col('index_symbol').n_unique()).item()}`",
        f"- Date range: `{date_range['start']}` to `{date_range['end']}`",
        "",
    ]


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


if __name__ == "__main__":
    main()
