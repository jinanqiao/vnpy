"""Build a practical QMT-backed data lake for turtle strategy research."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Sequence


import polars as pl
import requests

from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig, _request
from vnpy.alpha.research.run_summary import RunSummary
from vnpy.alpha.research.turtle_data_lake import (
    INDEX_SYMBOLS,
    build_daily_tradable_calendar,
    download_bars_for_symbols,
    symbols_to_frame,
    write_data_lake_summary,
)

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build turtle strategy data lake from QMT Gateway.")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--bar-count", type=int, default=1250)
    parser.add_argument("--symbol-limit", type=int, default=6000)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--universe", choices=["sample20", "hs300", "all_a"], default="all_a")
    parser.add_argument("--summary-path", help="Optional JSON path for a structured run summary.")
    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    data_root = Path(args.data_root)
    config = QmtGatewayConfig(timeout=args.timeout)
    build_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    for folder in ["raw/qmt", "normalized", "universe", "benchmark", "quality"]:
        (data_root / folder).mkdir(parents=True, exist_ok=True)

    summary = RunSummary(
        job_name="build_turtle_data_lake",
        inputs={
            "data_root": args.data_root,
            "bar_count": args.bar_count,
            "symbol_limit": args.symbol_limit,
            "timeout": args.timeout,
            "universe": args.universe,
        },
    )

    logger.info("Fetching all A symbols...")
    all_symbols = _request(
        config,
        "GET",
        "/market/symbols",
        params={"sector": "沪深A股", "limit": args.symbol_limit, "detail": "true"},
    ).get("symbols") or []
    symbols_df = symbols_to_frame(list(all_symbols), "all_a")
    symbols_df.write_parquet(data_root / "raw/qmt" / f"all_a_symbols_{build_id}.parquet")
    symbols_df.write_parquet(data_root / "universe" / "all_a_symbols.parquet")
    logger.info("All A symbols: %s", symbols_df.height)

    logger.info("Fetching index weights...")
    index_members = _download_index_members(config)
    index_members.write_parquet(data_root / "universe" / "index_members.parquet")
    _write_named_universe_files(data_root, index_members)

    symbols = _select_symbols(args.universe, symbols_df, index_members)
    logger.info("Selected universe=%s, symbols=%s", args.universe, len(symbols))

    daily_bars, failed = download_bars_for_symbols(config, symbols, count=args.bar_count)
    raw_daily_path = data_root / "raw/qmt" / f"daily_bars_{args.universe}_{build_id}.parquet"
    normalized_daily_path = data_root / "normalized" / f"daily_bars_{args.universe}.parquet"
    daily_bars.write_parquet(raw_daily_path)
    daily_bars.write_parquet(normalized_daily_path)
    if args.universe == "all_a":
        daily_bars.write_parquet(data_root / "normalized" / "daily_bars.parquet")
    logger.info("Daily bars rows: %s, failed symbols: %s", daily_bars.height, len(failed))

    benchmark_df, benchmark_failed = download_bars_for_symbols(config, list(INDEX_SYMBOLS), count=args.bar_count)
    benchmark_df = benchmark_df.rename({"vt_symbol": "index_symbol"})
    benchmark_df.write_parquet(data_root / "benchmark" / "index_daily.parquet")
    logger.info("Benchmark rows: %s, failed indexes: %s", benchmark_df.height, len(benchmark_failed))

    tradable_df = build_daily_tradable_calendar(daily_bars, symbols_df)
    tradable_df.write_parquet(data_root / "universe" / f"tradable_calendar_{args.universe}.parquet")
    if args.universe == "all_a":
        tradable_df.write_parquet(data_root / "universe" / "tradable_calendar.parquet")

    failures = failed + [f"benchmark {item}" for item in benchmark_failed]
    (data_root / "quality" / f"failed_symbols_{args.universe}.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_path = write_data_lake_summary(data_root, symbols_df, daily_bars, benchmark_df, tradable_df, failures)
    summary.outputs = {
        "raw_daily_path": raw_daily_path,
        "normalized_daily_path": normalized_daily_path,
        "benchmark_path": data_root / "benchmark" / "index_daily.parquet",
        "tradable_calendar": data_root / "universe" / f"tradable_calendar_{args.universe}.parquet",
        "data_lake_summary": summary_path,
    }
    summary.counts = {
        "all_symbols": symbols_df.height,
        "selected_symbols": len(symbols),
        "daily_rows": daily_bars.height,
        "benchmark_rows": benchmark_df.height,
        "tradable_rows": tradable_df.height,
    }
    summary.failures = failures
    summary.assumptions = [
        "Index and sector memberships are current gateway snapshots unless a PIT source is provided."
    ]
    summary.finish()
    if args.summary_path:
        summary.write_json(args.summary_path)
    logger.info("Summary saved to: %s", summary_path)


def _download_index_members(config: QmtGatewayConfig) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for index_code, universe_name in [
        ("000300.SH", "hs300"),
        ("000905.SH", "zz500"),
        ("000852.SH", "zz1000"),
    ]:
        try:
            response = _request(config, "GET", f"/market/index-weight/{index_code}")
        except (RuntimeError, requests.RequestException) as exc:
            logger.warning("Index weight failed %s: %s", index_code, exc)
            continue
        weights = response.get("weights") or {}
        for symbol, weight in weights.items():
            rows.append(
                {
                    "datetime": datetime.now().date(),
                    "vt_symbol": str(symbol),
                    "index_code": index_code,
                    "universe_name": universe_name,
                    "weight": float(weight),
                    "source": "qmt_gateway",
                }
            )
    return pl.DataFrame(rows).sort(["universe_name", "vt_symbol"]) if rows else pl.DataFrame()


def _write_named_universe_files(data_root: Path, index_members: pl.DataFrame) -> None:
    if index_members.is_empty():
        return
    for name in index_members["universe_name"].unique().to_list():
        index_members.filter(pl.col("universe_name") == name).write_parquet(data_root / "universe" / f"{name}_members.parquet")


def _select_symbols(universe: str, symbols_df: pl.DataFrame, index_members: pl.DataFrame) -> list[str]:
    if universe == "sample20":
        from vnpy.alpha.research.turtle_universe import SAMPLE_20_SYMBOLS

        return list(SAMPLE_20_SYMBOLS)
    if universe == "hs300" and not index_members.is_empty():
        return [str(symbol) for symbol in index_members.filter(pl.col("universe_name") == "hs300")["vt_symbol"].to_list()]
    return [str(symbol) for symbol in symbols_df["vt_symbol"].to_list()]


if __name__ == "__main__":
    main()
