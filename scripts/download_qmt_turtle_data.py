"""Download daily bars from the hardcoded QMT Gateway for turtle research."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from collections.abc import Sequence


import polars as pl

from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig, fetch_qmt_daily_bars
from vnpy.alpha.research.run_summary import RunSummary
from vnpy.alpha.research.turtle_universe import SAMPLE_20_SYMBOLS

logger = logging.getLogger(__name__)

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download stock daily bars from hardcoded QMT Gateway.")
    parser.add_argument("--output-path", default="data/turtle/qmt_daily.parquet")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--symbols", nargs="*", default=list(SAMPLE_20_SYMBOLS))
    parser.add_argument("--summary-path", help="Optional JSON path for a structured run summary.")
    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    config = QmtGatewayConfig(timeout=args.timeout)
    frames: list[pl.DataFrame] = []
    failed: list[str] = []

    summary = RunSummary(
        job_name="download_qmt_turtle_data",
        inputs={"symbols": list(args.symbols), "count": args.count, "timeout": args.timeout},
        outputs={"output_path": args.output_path},
    )

    for symbol in args.symbols:
        try:
            frame = fetch_qmt_daily_bars(config, symbol, count=args.count)
        except Exception as exc:
            failed.append(f"{symbol}: {exc}")
            continue
        if frame.is_empty():
            failed.append(f"{symbol}: empty")
            continue
        frames.append(frame)
        logger.info("OK %s: %s rows", symbol, frame.height)

    if not frames:
        raise SystemExit("No data downloaded from QMT Gateway.")

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pl.concat(frames, how="vertical").write_parquet(output_path)
    summary.counts["symbols_saved"] = len(frames)
    summary.failures = failed
    summary.finish()
    if args.summary_path:
        summary.write_json(args.summary_path)
    logger.info("Saved %s symbols to %s", len(frames), output_path)

    if failed:
        logger.warning("Failed symbols:")
        for item in failed:
            logger.warning("- %s", item)


if __name__ == "__main__":
    main()
