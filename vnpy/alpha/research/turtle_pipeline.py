from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

try:
    from .turtle_backtest import run_long_only_backtest
    from .turtle_data import load_price_data, save_data_summary, summarize_price_data
    from .turtle_experiment import (
        TurtleExperimentConfig,
        create_experiment_dir,
        save_experiment_config,
        save_json,
    )
    from .turtle_indicators import add_turtle_indicators
    from .turtle_metrics import calculate_performance_metrics
    from .turtle_plots import save_drawdown_chart, save_equity_chart, save_signal_chart
    from .turtle_report import save_turtle_report
    from .turtle_signals import generate_turtle_signals
    from .run_summary import RunSummary
    from .contracts import validate_turtle_price_frame, validate_turtle_signal_frame
except ImportError:  # pragma: no cover - allows direct file loading in tests
    from turtle_backtest import run_long_only_backtest  # type: ignore
    from turtle_data import load_price_data, save_data_summary, summarize_price_data  # type: ignore
    from turtle_experiment import TurtleExperimentConfig, create_experiment_dir, save_experiment_config, save_json  # type: ignore
    from turtle_indicators import add_turtle_indicators  # type: ignore
    from turtle_metrics import calculate_performance_metrics  # type: ignore
    from turtle_plots import save_drawdown_chart, save_equity_chart, save_signal_chart  # type: ignore
    from turtle_report import save_turtle_report  # type: ignore
    from turtle_signals import generate_turtle_signals  # type: ignore
    from run_summary import RunSummary  # type: ignore
    from contracts import validate_turtle_price_frame, validate_turtle_signal_frame  # type: ignore


@dataclass
class TurtlePipelineResult:
    """Structured output produced by the turtle research pipeline."""

    experiment_dir: Path
    price_df: pl.DataFrame
    data_summary: dict
    indicator_df: pl.DataFrame
    signal_df: pl.DataFrame
    daily_df: pl.DataFrame
    metrics: object
    figure_paths: list[str | Path]
    run_summary: RunSummary


def run_turtle_pipeline(
    config: TurtleExperimentConfig,
    output_base_dir: str | Path = "outputs/turtle",
    save_figures: bool = True,
) -> Path:
    """Run the full local turtle research pipeline and save artifacts."""
    result = run_turtle_pipeline_result(config, output_base_dir, save_figures)
    return result.experiment_dir


def run_turtle_pipeline_result(
    config: TurtleExperimentConfig,
    output_base_dir: str | Path = "outputs/turtle",
    save_figures: bool = True,
) -> TurtlePipelineResult:
    """Run the full local turtle research pipeline and return structured outputs."""
    experiment_dir = create_experiment_dir(output_base_dir, config.name)
    price_df, data_summary = prepare_turtle_data(config)
    indicator_df, signal_df, daily_df, metrics = calculate_turtle_strategy_frames(config, price_df)
    figure_paths = write_turtle_artifacts(
        config,
        experiment_dir,
        data_summary,
        signal_df,
        daily_df,
        metrics,
        save_figures,
    )
    artifact_paths = {
        "config": experiment_dir / "config.json",
        "data_quality": experiment_dir / "data_quality.json",
        "signals": experiment_dir / "signals.csv",
        "equity": experiment_dir / "equity.csv",
        "metrics": experiment_dir / "metrics.json",
        "report": experiment_dir / "report.md",
        "figures": figure_paths,
    }
    run_summary = build_turtle_run_summary(
        config,
        experiment_dir,
        artifact_paths,
        price_df,
        signal_df,
        daily_df,
        metrics,
    )

    return TurtlePipelineResult(
        experiment_dir=experiment_dir,
        price_df=price_df,
        data_summary=data_summary,
        indicator_df=indicator_df,
        signal_df=signal_df,
        daily_df=daily_df,
        metrics=metrics,
        figure_paths=figure_paths,
        run_summary=run_summary,
    )


def prepare_turtle_data(config: TurtleExperimentConfig) -> tuple[pl.DataFrame, dict]:
    """Load and summarize turtle source data without writing artifacts."""
    price_df: pl.DataFrame = load_price_data(config.data_path)
    validate_turtle_price_frame(price_df).raise_if_failed()
    data_summary = summarize_price_data(price_df)
    return price_df, data_summary


def calculate_turtle_strategy_frames(
    config: TurtleExperimentConfig,
    price_df: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, object]:
    """Calculate indicators, signals, equity curve, and metrics."""
    indicator_df = add_turtle_indicators(
        price_df,
        entry_window=config.entry_window,
        exit_window=config.exit_window,
        atr_window=config.atr_window,
    )
    signal_df = generate_turtle_signals(indicator_df)
    validate_turtle_signal_frame(signal_df).raise_if_failed()
    daily_df = run_long_only_backtest(signal_df, max_positions=config.max_positions, cost_rate=config.cost_rate)
    metrics = calculate_performance_metrics(daily_df, annual_days=config.annual_days)
    return indicator_df, signal_df, daily_df, metrics


def write_turtle_artifacts(
    config: TurtleExperimentConfig,
    experiment_dir: Path,
    data_summary: dict,
    signal_df: pl.DataFrame,
    daily_df: pl.DataFrame,
    metrics: object,
    save_figures: bool = True,
) -> list[str | Path]:
    """Write turtle pipeline artifacts while preserving historical filenames."""
    save_experiment_config(config, experiment_dir / "config.json")
    save_data_summary(data_summary, experiment_dir / "data_quality.json")
    signal_df.write_csv(experiment_dir / "signals.csv")
    daily_df.write_csv(experiment_dir / "equity.csv")
    save_json(asdict(metrics), experiment_dir / "metrics.json")

    figure_paths: list[str | Path] = []
    if save_figures:
        figure_paths.append(save_equity_chart(daily_df, experiment_dir / "figures" / "equity.html"))
        figure_paths.append(save_drawdown_chart(daily_df, experiment_dir / "figures" / "drawdown.html"))
        first_symbol = _first_symbol(signal_df)
        if first_symbol:
            figure_paths.append(
                save_signal_chart(signal_df, first_symbol, experiment_dir / "figures" / f"signal_{first_symbol}.html")
            )

    save_turtle_report(
        experiment_dir / "report.md",
        metrics,
        assumptions={
            "策略": "股票版 long-only 海龟策略",
            "入场": f"收盘价突破过去 {config.entry_window} 日最高价",
            "离场": f"收盘价跌破过去 {config.exit_window} 日最低价",
            "执行": "信号次日持仓，日频简化回测",
            "成本": f"单边简化成本率 {config.cost_rate:.4f}",
        },
        figure_paths=figure_paths,
    )

    return figure_paths


def build_turtle_run_summary(
    config: TurtleExperimentConfig,
    experiment_dir: Path,
    artifact_paths: dict[str, object],
    price_df: pl.DataFrame,
    signal_df: pl.DataFrame,
    daily_df: pl.DataFrame,
    metrics: object,
) -> RunSummary:
    summary = RunSummary(
        job_name="turtle_pipeline",
        inputs={
            "data_path": config.data_path,
            "entry_window": config.entry_window,
            "exit_window": config.exit_window,
            "atr_window": config.atr_window,
            "max_positions": config.max_positions,
            "cost_rate": config.cost_rate,
            "annual_days": config.annual_days,
        },
        outputs={
            "experiment_dir": experiment_dir,
            **artifact_paths,
        },
        counts={
            "price_rows": price_df.height,
            "signal_rows": signal_df.height,
            "daily_rows": daily_df.height,
        },
        metrics=asdict(metrics),
        assumptions=[
            "股票版 long-only 海龟策略",
            "信号次日持仓，日频简化回测",
            "如使用当前股票池或指数成分数据，应视为当前快照而非历史 PIT 成分",
        ],
    )
    return summary.finish()


def _first_symbol(df: pl.DataFrame) -> str | None:
    if "vt_symbol" not in df.columns or df.is_empty():
        return None
    return str(df["vt_symbol"][0])
