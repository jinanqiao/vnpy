"""Build practical PIT universe layers from the local quant data foundation."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from collections.abc import Sequence


import polars as pl

from vnpy.alpha.research.pit_universe import PitUniverseConfig, build_pit_universe

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build PIT universe artifacts.")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-dir", default="data/universe")
    parser.add_argument("--bars", default="data/silver/daily_bars_raw_price.parquet", help="用于构建股票池的未复权日线")
    parser.add_argument("--min-listed-days", type=int, default=120)
    parser.add_argument("--min-price", type=float, default=2.0)
    parser.add_argument("--min-avg-turnover", type=float, default=20_000_000.0)
    parser.add_argument("--turnover-window", type=int, default=20)
    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    data_root = Path(args.data_root)
    config = PitUniverseConfig(
        min_listed_days=args.min_listed_days,
        min_price=args.min_price,
        min_avg_turnover=args.min_avg_turnover,
        turnover_window=args.turnover_window,
    )

    summary = build_pit_universe(
        symbols_df=pl.read_parquet(data_root / "universe" / "all_a_symbols.parquet"),
        trading_dates_df=pl.read_parquet(data_root / "calendar" / "trading_dates.parquet"),
        daily_bars=pl.read_parquet(args.bars),
        output_dir=args.output_dir,
        config=config,
    )
    logger.info("PIT universe generated: %s", summary)


if __name__ == "__main__":
    main()
