"""Run the beginner-friendly stock turtle research pipeline."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence


from vnpy.alpha.research.turtle_experiment import TurtleExperimentConfig
from vnpy.alpha.research.turtle_pipeline import run_turtle_pipeline_result

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stock turtle strategy research pipeline.")
    parser.add_argument("--data-path", required=True, help="CSV/parquet file or folder containing OHLCV data.")
    parser.add_argument("--output-dir", default="outputs/turtle", help="Folder for experiment artifacts.")
    parser.add_argument("--name", default="turtle_long_only", help="Experiment name.")
    parser.add_argument("--entry-window", type=int, default=20, help="Donchian breakout window.")
    parser.add_argument("--exit-window", type=int, default=10, help="Donchian exit window.")
    parser.add_argument("--atr-window", type=int, default=20, help="ATR window.")
    parser.add_argument("--max-positions", type=int, default=10, help="Maximum number of holdings.")
    parser.add_argument("--cost-rate", type=float, default=0.0013, help="Simplified one-way transaction cost rate.")
    parser.add_argument("--annual-days", type=int, default=252, help="Trading days per year.")
    parser.add_argument("--no-figures", action="store_true", help="Skip Plotly HTML figures.")
    parser.add_argument("--summary-path", help="Optional JSON path for a structured run summary.")
    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    config = TurtleExperimentConfig(
        name=args.name,
        data_path=args.data_path,
        entry_window=args.entry_window,
        exit_window=args.exit_window,
        atr_window=args.atr_window,
        max_positions=args.max_positions,
        cost_rate=args.cost_rate,
        annual_days=args.annual_days,
    )

    logger.info(
        "Starting turtle pipeline: data_path=%s output_dir=%s name=%s",
        args.data_path,
        args.output_dir,
        args.name,
    )
    result = run_turtle_pipeline_result(
        config,
        output_base_dir=args.output_dir,
        save_figures=not args.no_figures,
    )
    if args.summary_path:
        result.run_summary.write_json(args.summary_path)
        logger.info("Run summary saved to: %s", args.summary_path)

    logger.info(
        "Turtle experiment saved to: %s price_rows=%s daily_rows=%s",
        result.experiment_dir,
        result.run_summary.counts["price_rows"],
        result.run_summary.counts["daily_rows"],
    )


if __name__ == "__main__":
    main()
