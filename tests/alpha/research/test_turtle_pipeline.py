from datetime import datetime, timedelta
import json
from pathlib import Path

import polars as pl

from vnpy.alpha.research.turtle_experiment import TurtleExperimentConfig
from vnpy.alpha.research.turtle_pipeline import (
    calculate_turtle_strategy_frames,
    prepare_turtle_data,
    run_turtle_pipeline,
    run_turtle_pipeline_result,
)


def test_run_turtle_pipeline_saves_core_artifacts(tmp_path: Path) -> None:
    data_path = tmp_path / "prices.csv"
    make_pipeline_input().write_csv(data_path)
    config = TurtleExperimentConfig(
        name="unit_test",
        data_path=str(data_path),
        entry_window=2,
        exit_window=2,
        atr_window=2,
        max_positions=2,
        cost_rate=0.0,
    )

    experiment_dir = run_turtle_pipeline(config, output_base_dir=tmp_path / "outputs", save_figures=False)

    assert (experiment_dir / "config.json").exists()
    assert (experiment_dir / "data_quality.json").exists()
    assert (experiment_dir / "signals.csv").exists()
    assert (experiment_dir / "equity.csv").exists()
    assert (experiment_dir / "metrics.json").exists()
    assert (experiment_dir / "report.md").exists()


def test_turtle_pipeline_stages_return_structured_outputs_without_writing_full_experiment(tmp_path: Path) -> None:
    data_path = tmp_path / "prices.csv"
    make_pipeline_input().write_csv(data_path)
    config = TurtleExperimentConfig(
        name="unit_test",
        data_path=str(data_path),
        entry_window=2,
        exit_window=2,
        atr_window=2,
        max_positions=2,
        cost_rate=0.0,
    )

    price_df, data_summary = prepare_turtle_data(config)
    indicator_df, signal_df, daily_df, metrics = calculate_turtle_strategy_frames(config, price_df)

    assert price_df.height == 12
    assert data_summary.row_count == 12
    assert {"entry_channel", "exit_channel", "atr"}.issubset(indicator_df.columns)
    assert {"entry_signal", "exit_signal", "target_position"}.issubset(signal_df.columns)
    assert {"datetime", "net_equity", "net_return", "turnover"}.issubset(daily_df.columns)
    assert isinstance(metrics.total_return, float)


def test_run_turtle_pipeline_result_preserves_directory_return_and_exposes_frames(tmp_path: Path) -> None:
    data_path = tmp_path / "prices.csv"
    make_pipeline_input().write_csv(data_path)
    config = TurtleExperimentConfig(
        name="unit_test",
        data_path=str(data_path),
        entry_window=2,
        exit_window=2,
        atr_window=2,
        max_positions=2,
        cost_rate=0.0,
    )

    result = run_turtle_pipeline_result(config, output_base_dir=tmp_path / "outputs", save_figures=False)

    assert result.experiment_dir.exists()
    assert result.price_df.height == 12
    assert result.signal_df.height == 12
    assert result.figure_paths == []
    assert result.run_summary.job_name == "turtle_pipeline"
    assert result.run_summary.counts["price_rows"] == 12
    assert "signals" in result.run_summary.outputs
    assert result.run_summary.assumptions
    assert (result.experiment_dir / "signals.csv").exists()


def test_turtle_pipeline_run_summary_writes_json(tmp_path: Path) -> None:
    data_path = tmp_path / "prices.csv"
    make_pipeline_input().write_csv(data_path)
    config = TurtleExperimentConfig(
        name="unit_test",
        data_path=str(data_path),
        entry_window=2,
        exit_window=2,
        atr_window=2,
        max_positions=2,
        cost_rate=0.0,
    )
    result = run_turtle_pipeline_result(config, output_base_dir=tmp_path / "outputs", save_figures=False)

    summary_path = result.run_summary.write_json(tmp_path / "summary.json")
    data = json.loads(summary_path.read_text(encoding="utf-8"))

    assert data["job_name"] == "turtle_pipeline"
    assert data["counts"]["signal_rows"] == 12
    assert data["outputs"]["experiment_dir"]


def make_pipeline_input() -> pl.DataFrame:
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(6)]
    return pl.DataFrame(
        {
            "datetime": dates * 2,
            "vt_symbol": ["AAA.SSE"] * 6 + ["BBB.SSE"] * 6,
            "open": [10.0, 10.5, 11.0, 12.0, 11.5, 11.0, 20.0, 20.5, 21.0, 22.0, 21.5, 21.0],
            "high": [10.5, 11.0, 12.0, 12.5, 12.0, 11.5, 20.5, 21.0, 22.0, 22.5, 22.0, 21.5],
            "low": [9.5, 10.0, 10.5, 11.5, 11.0, 10.5, 19.5, 20.0, 20.5, 21.5, 21.0, 20.5],
            "close": [10.0, 10.7, 11.8, 12.2, 11.2, 10.8, 20.0, 20.7, 21.8, 22.2, 21.2, 20.8],
            "volume": [1000.0] * 12,
        }
    )
