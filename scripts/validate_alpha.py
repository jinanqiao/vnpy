"""Run focused alpha subsystem validation checks."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass


PYTEST_TARGETS: tuple[str, ...] = (
    "tests/alpha/test_lab_storage.py",
    "tests/alpha/test_dataproxy.py",
    "tests/test_alpha101.py",
    "tests/alpha/model/test_model_boundaries.py",
    "tests/alpha/strategy",
    "tests/alpha/research",
)

PY_COMPILE_TARGETS: tuple[str, ...] = (
    "vnpy/alpha/optional.py",
    "vnpy/alpha/storage.py",
    "vnpy/alpha/lab.py",
    "vnpy/alpha/dataset/utility.py",
    "vnpy/alpha/model/models/lgb_model.py",
    "vnpy/alpha/model/models/mlp_components.py",
    "vnpy/alpha/model/models/mlp_model.py",
    "vnpy/alpha/research/__init__.py",
    "vnpy/alpha/research/contracts.py",
    "vnpy/alpha/research/run_summary.py",
    "vnpy/alpha/research/turtle_backtest.py",
    "vnpy/alpha/research/turtle_data.py",
    "vnpy/alpha/research/turtle_indicators.py",
    "vnpy/alpha/research/turtle_pipeline.py",
    "vnpy/alpha/research/turtle_signals.py",
    "vnpy/alpha/strategy/backtesting.py",
    "vnpy/alpha/strategy/replay.py",
    "vnpy/alpha/strategy/reporting.py",
    "scripts/run_turtle_pipeline.py",
    "scripts/download_qmt_turtle_data.py",
    "scripts/build_turtle_data_lake.py",
    "scripts/validate_alpha.py",
)


@dataclass(frozen=True)
class ValidationStep:
    name: str
    command: tuple[str, ...]
    optional: bool = False
    skip_reason: str | None = None


def build_validation_steps(*, include_lint: bool = False) -> list[ValidationStep]:
    steps = [
        ValidationStep(
            name="import-check",
            command=(
                sys.executable,
                "-c",
                "import vnpy.alpha; import vnpy.alpha.research; "
                "from vnpy.alpha.model.models import lgb_model, mlp_model; "
                "print('alpha imports ok')",
            ),
        ),
        ValidationStep(
            name="pytest-alpha",
            command=(sys.executable, "-m", "pytest", *PYTEST_TARGETS, "-q"),
        ),
        ValidationStep(
            name="py-compile-alpha",
            command=(sys.executable, "-m", "py_compile", *PY_COMPILE_TARGETS),
        ),
    ]

    if include_lint and shutil.which("ruff"):
        steps.append(
            ValidationStep(
                name="ruff-alpha",
                command=("ruff", "check", "vnpy/alpha", "tests/alpha", "scripts/validate_alpha.py"),
                optional=True,
            )
        )
    else:
        steps.append(
            ValidationStep(
                name="ruff-alpha",
                command=("ruff", "check", "vnpy/alpha", "tests/alpha", "scripts/validate_alpha.py"),
                optional=True,
                skip_reason="pass --lint and install ruff to run scoped lint checks",
            )
        )

    return steps


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run focused alpha validation checks.")
    parser.add_argument("--lint", action="store_true", help="Also run scoped ruff checks when ruff is installed.")
    parser.add_argument("--list", action="store_true", help="Print validation commands without running them.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    steps = build_validation_steps(include_lint=args.lint)

    for step in steps:
        command_text = " ".join(step.command)
        if args.list:
            status = "SKIP" if step.skip_reason else "RUN"
            print(f"[{status}] {step.name}: {command_text}")
            continue

        if step.skip_reason:
            print(f"[SKIP] {step.name}: {step.skip_reason}")
            continue

        print(f"[RUN] {step.name}: {command_text}")
        result = subprocess.run(step.command, check=False)
        if result.returncode:
            print(f"[FAIL] {step.name}: rerun with `{command_text}`")
            return result.returncode
        print(f"[OK] {step.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
