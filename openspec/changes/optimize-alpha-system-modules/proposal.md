## Why

The alpha system now has working research and backtesting pieces, but several core modules still mix storage, execution, modeling, expression parsing, reporting, and CLI concerns. Optimizing these boundaries now will make future strategy research safer, easier to test, and easier to extend without weakening backtest trustworthiness.

## What Changes

- Split `AlphaLab` into focused repository/store components for bars, contracts, components, datasets, models, and signals.
- Replace unsafe factor expression execution with a validated expression layer that supports explicit allowlists and actionable errors.
- Refactor the MLP model implementation into separable dataset adaptation, training, prediction, model structure, and model-detail responsibilities.
- Continue the alpha backtesting engine cleanup after the first extraction by separating replay orchestration and reporting/charting concerns.
- Turn the turtle/research pipeline into a staged, reproducible experiment runner with explicit artifact boundaries.
- Convert ad hoc scripts into thin CLI entrypoints that delegate reusable logic into package modules.
- Add focused tests for each boundary using deterministic in-memory fixtures instead of local market data files.
- No **BREAKING** public API changes are intended; compatibility wrappers should remain where existing users may import old names.

## Capabilities

### New Capabilities
- `alpha-lab-storage`: Defines repository and artifact-store boundaries for alpha data, contract settings, index components, datasets, models, and signals.
- `alpha-factor-expression`: Defines safe factor expression parsing, validation, execution, and error-reporting behavior.
- `alpha-model-training`: Defines model training, prediction, evaluation, and model-detail boundaries for alpha models, especially MLP.
- `alpha-backtesting-workflow`: Defines second-phase backtesting workflow cleanup, including replay orchestration and reporting separation.
- `alpha-research-pipeline`: Defines staged, reproducible research experiment execution and artifact writing.
- `alpha-cli-tools`: Defines command-line script behavior, logging, import hygiene, and delegation to package modules.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `vnpy/alpha/lab.py`
  - `vnpy/alpha/dataset/utility.py`
  - `vnpy/alpha/dataset/*_function.py`
  - `vnpy/alpha/model/models/mlp_model.py`
  - `vnpy/alpha/strategy/backtesting.py`
  - `vnpy/alpha/strategy/*`
  - `vnpy/alpha/research/turtle_pipeline.py`
  - `vnpy/alpha/research/turtle_*`
  - `scripts/*.py`
  - `tests/alpha/**`
- Public APIs:
  - Existing entrypoints should remain available unless a later change explicitly marks a breaking migration.
  - New internal modules may be introduced under `vnpy/alpha/` to separate responsibilities.
- Dependencies:
  - No new runtime dependency is expected for the storage, expression, backtesting, pipeline, or CLI work.
  - Model-training refactors should use existing dependencies already declared in `pyproject.toml`.
- Systems:
  - This change coordinates multiple alpha-system optimizations. Implementation should proceed in small phases, with tests passing after each phase.
  - Existing data files and generated outputs are not part of this change.
