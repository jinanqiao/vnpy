## Why

The alpha system has already separated storage, expression safety, model, backtesting, research pipeline, and CLI boundaries, but the next bottleneck is operational trust: optional dependencies fail late, research scripts still hide runtime state, tests depend on local import tricks, and quality gates are not yet focused on the alpha surface. Addressing these foundations now will make future strategy work faster, safer, and easier to verify before live or paper-trading integration.

## What Changes

- Add explicit optional-dependency guards for alpha model and research features so missing `torch`, `lightgbm`, `plotly`, or gateway-only components produce actionable messages instead of import-time surprises.
- Convert remaining alpha research and data-lake scripts from ad hoc progress printing to consistent logging, structured run summaries, and explicit failure reports.
- Replace test-only research import helpers with normal package imports and package-level exports where practical.
- Add alpha-focused quality commands for linting, import checks, compile checks, and deterministic pytest subsets so future changes can be validated consistently.
- Add stronger research data validation contracts for schema, sortedness, duplicate keys, null policy, and point-in-time assumptions before indicators, signals, and backtests run.
- No **BREAKING** public API changes are intended; existing CLI entrypoints and package imports should remain available.

## Capabilities

### New Capabilities
- `alpha-optional-dependencies`: Defines graceful behavior when optional alpha model, plotting, and gateway dependencies are unavailable.
- `alpha-observability`: Defines logging, run summaries, and failure-report behavior for alpha scripts and research jobs.
- `alpha-quality-gates`: Defines repeatable alpha validation commands covering tests, imports, compile checks, and lint/type-check subsets where feasible.
- `alpha-research-data-contracts`: Defines preflight validation for research datasets, point-in-time assumptions, and backtest-ready frames.
- `alpha-package-imports`: Defines package import hygiene for alpha research modules, tests, and scripts without path mutation.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `vnpy/alpha/model/models/*`
  - `vnpy/alpha/research/*`
  - `vnpy/alpha/dataset/*`
  - `scripts/*.py`
  - `tests/alpha/**`
  - `pyproject.toml` or lightweight validation scripts if needed
- Public APIs:
  - Existing model classes, pipeline functions, and CLI scripts should remain compatible.
  - New helper functions may be exposed for optional dependency checks, validation, and quality commands.
- Dependencies:
  - No new required runtime dependency is expected.
  - Optional alpha dependencies should remain optional at import time where the feature is not used.
- Systems:
  - This change improves development and research reliability rather than adding a new trading strategy.
  - Implementation should proceed in small phases with focused tests after each phase.
