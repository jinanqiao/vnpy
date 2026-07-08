from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import json


@dataclass(frozen=True)
class TurtleExperimentConfig:
    """Configuration for a reproducible turtle strategy run."""

    name: str = "turtle_long_only"
    data_path: str = ""
    universe_name: str = "all_loaded_symbols"
    entry_window: int = 20
    exit_window: int = 10
    atr_window: int = 20
    max_positions: int = 10
    cost_rate: float = 0.0013
    annual_days: int = 252


def create_experiment_dir(base_dir: str | Path, name: str) -> Path:
    """Create a timestamped experiment directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(base_dir) / f"{timestamp}_{name}"
    path.mkdir(parents=True, exist_ok=False)
    (path / "figures").mkdir()
    return path


def save_experiment_config(config: TurtleExperimentConfig, output_path: str | Path) -> Path:
    """Save experiment config as JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_json(data: dict[str, Any], output_path: str | Path) -> Path:
    """Save any JSON-serializable dictionary."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
