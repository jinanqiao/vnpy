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
    "vnpy/alpha/research/qmt_gateway_trade.py",
    "vnpy/alpha/research/mainline/__init__.py",
    "vnpy/alpha/research/mainline/config.py",
    "vnpy/alpha/research/mainline/data_loader.py",
    "vnpy/alpha/research/mainline/industry.py",
    "vnpy/alpha/research/mainline/stocks.py",
    "vnpy/alpha/research/mainline/regime.py",
    "vnpy/alpha/research/mainline/pipeline.py",
    "vnpy/alpha/research/mainline_backtest/__init__.py",
    "vnpy/alpha/research/mainline_backtest/config.py",
    "vnpy/alpha/research/mainline_backtest/data_loader.py",
    "vnpy/alpha/research/mainline_backtest/execution.py",
    "vnpy/alpha/research/mainline_backtest/portfolio.py",
    "vnpy/alpha/research/mainline_backtest/risk.py",
    "vnpy/alpha/research/mainline_backtest/metrics.py",
    "vnpy/alpha/research/mainline_backtest/analytics.py",
    "vnpy/alpha/research/mainline_backtest/pipeline.py",
    "vnpy/alpha/research/mainline_backtest/report.py",
    "vnpy/alpha/research/data_foundation/__init__.py",
    "vnpy/alpha/research/data_foundation/context.py",
    "vnpy/alpha/research/data_foundation/data_gate.py",
    "vnpy/alpha/research/data_foundation/freshness.py",
    "vnpy/alpha/research/data_foundation/layout.py",
    "vnpy/alpha/research/data_foundation/manifest.py",
    "vnpy/alpha/research/data_foundation/metadata.py",
    "vnpy/alpha/research/data_foundation/pit_tables.py",
    "vnpy/alpha/research/data_foundation/price_reconciliation.py",
    "vnpy/alpha/research/data_foundation/quarantine.py",
    "vnpy/alpha/research/data_foundation/query_catalog.py",
    "vnpy/alpha/strategy/backtesting.py",
    "vnpy/alpha/strategy/replay.py",
    "vnpy/alpha/strategy/reporting.py",
    "scripts/run_turtle_pipeline.py",
    "scripts/run_mainline_signals.py",
    "scripts/run_mainline_backtest.py",
    "scripts/validate_technical_factors.py",
    "scripts/run_risk_experiments.py",
    "scripts/run_freshness_experiments.py",
    "scripts/run_weekly_experiments.py",
    "scripts/run_industry_experiments.py",
    "scripts/download_adjusted_bars.py",
    "scripts/download_sw1_members.py",
    "scripts/validate_industry_momentum.py",
    "scripts/validate_stock_factors.py",
    "scripts/validate_lottery_liquidity_factors.py",
    "scripts/check_factor_health.py",
    "scripts/run_rolling_oos_backtest.py",
    "scripts/download_akshare_daily.py",
    "scripts/validate_size_liquidity_factors.py",
    "scripts/download_akshare_financial.py",
    "scripts/validate_fundamental_factors.py",
    "scripts/download_qmt_turtle_data.py",
    "scripts/download_qmt_daily_bars_full.py",
    "scripts/build_turtle_data_lake.py",
    "scripts/build_data_foundation.py",
    "scripts/build_pit_tables.py",
    "scripts/check_data_gate.py",
    "scripts/diagnose_price_reconciliation.py",
    "scripts/apply_symbol_quarantine.py",
    "scripts/refresh_live_data_foundation.py",
    "scripts/refresh_auxiliary_data_foundation.py",
    "scripts/sync_metadata_to_timescale.py",
    "scripts/validate_alpha.py",
    "infra/qmt_gateway/qmt_gateway.py",
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
