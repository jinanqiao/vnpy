from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"


def load_script(name: str) -> ModuleType:
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_run_turtle_pipeline_parser_smoke() -> None:
    module = load_script("run_turtle_pipeline.py")

    args = module.parse_args(["--data-path", "prices.csv", "--no-figures", "--summary-path", "summary.json"])

    assert args.data_path == "prices.csv"
    assert args.no_figures is True
    assert args.summary_path == "summary.json"


def test_build_point_in_time_universe_parser_smoke() -> None:
    module = load_script("build_point_in_time_universe.py")

    args = module.parse_args(["--data-root", "data", "--min-listed-days", "60"])

    assert args.data_root == "data"
    assert args.min_listed_days == 60


def test_download_qmt_turtle_data_parser_smoke() -> None:
    module = load_script("download_qmt_turtle_data.py")

    args = module.parse_args(["--symbols", "000001.SZ", "600000.SH", "--count", "10", "--summary-path", "out.json"])

    assert args.symbols == ["000001.SZ", "600000.SH"]
    assert args.count == 10
    assert args.summary_path == "out.json"


def test_build_turtle_data_lake_parser_smoke() -> None:
    module = load_script("build_turtle_data_lake.py")

    args = module.parse_args(["--universe", "sample20", "--bar-count", "30", "--summary-path", "lake.json"])

    assert args.universe == "sample20"
    assert args.bar_count == 30
    assert args.summary_path == "lake.json"


def test_build_quant_data_completeness_parser_smoke() -> None:
    module = load_script("build_quant_data_completeness.py")

    args = module.parse_args(["--trading-date-count", "100"])

    assert args.trading_date_count == 100


def test_validate_alpha_command_composition() -> None:
    module = load_script("validate_alpha.py")

    args = module.parse_args(["--list"])
    steps = module.build_validation_steps(include_lint=False)

    assert args.list is True
    assert [step.name for step in steps] == [
        "import-check",
        "pytest-alpha",
        "py-compile-alpha",
        "ruff-alpha",
    ]
    assert steps[-1].skip_reason


def test_alpha_research_package_exports_lightweight_helpers() -> None:
    import vnpy.alpha.research as research

    assert research.RunSummary.__name__ == "RunSummary"
    assert research.TurtlePipelineResult.__name__ == "TurtlePipelineResult"
    assert callable(research.validate_research_frame)
