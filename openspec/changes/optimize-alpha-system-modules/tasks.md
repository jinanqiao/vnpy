## 1. AlphaLab Storage Boundaries

- [x] 1.1 Add characterization tests for `AlphaLab` bar, contract, component, dataset, model, and signal persistence using `tmp_path`
- [x] 1.2 Create focused repository/store modules for bars, contracts, components, datasets, models, and signals
- [x] 1.3 Update `AlphaLab` to delegate to the new repositories while preserving existing method signatures
- [x] 1.4 Keep existing parquet, JSON, shelve, and pickle layout compatibility unless a separate migration is specified
- [x] 1.5 Run focused `AlphaLab` tests and adjacent alpha tests

## 2. Factor Expression Safety

- [x] 2.1 Add compatibility tests for existing valid `DataProxy` arithmetic, comparison, registered-function, time-series, and cross-sectional expressions
- [x] 2.2 Add rejection tests for unsafe syntax such as imports, attribute access, unknown names, lambdas, comprehensions, and assignment
- [x] 2.3 Implement a validated expression layer using structured parsing and explicit allowlists
- [x] 2.4 Update `calculate_by_expression` to use validation before execution while preserving valid expression outputs
- [x] 2.5 Run dataset expression tests and alpha101-related tests

## 3. Alpha Model Training Boundaries

- [x] 3.1 Add compact tests for MLP dataset adaptation, prediction shape, seed behavior, and detail output boundaries
- [x] 3.2 Extract Polars/Pandas-to-tensor dataset adaptation from `MlpModel`
- [x] 3.3 Extract training loop, evaluation step, and prediction batching helpers from `MlpModel`
- [x] 3.4 Isolate feature-detail calculation behind a replaceable helper
- [x] 3.5 Keep `MlpModel.fit`, `MlpModel.predict`, and `MlpModel.detail` compatible
- [x] 3.6 Run model tests and import checks for lasso/lgb/mlp modules

## 4. Backtesting Workflow Phase Two

- [x] 4.1 Add tests around replay error behavior, replay callback ordering, and chart/performance facade availability
- [x] 4.2 Extract historical replay orchestration from `BacktestingEngine`
- [x] 4.3 Extract charting and benchmark performance rendering from core backtest calculation
- [x] 4.4 Preserve `BacktestingEngine.run_backtesting`, `show_chart`, and `show_performance` compatibility
- [x] 4.5 Run `tests/alpha/strategy` and alpha research tests

## 5. Research Pipeline Staging

- [x] 5.1 Add tests for turtle pipeline stages that do not require writing full experiment output folders
- [x] 5.2 Introduce structured experiment result and stage objects/functions for load, validate, indicators, signals, backtest, metrics, plots, and report
- [x] 5.3 Extract artifact writing for configs, summaries, signals, equity, metrics, plots, and reports
- [x] 5.4 Preserve `run_turtle_pipeline` behavior and output artifact names
- [x] 5.5 Run turtle pipeline and research tests

## 6. CLI Script Engineering

- [x] 6.1 Add parser/main smoke tests for existing alpha scripts where practical
- [x] 6.2 Remove `sys.path.insert` usage from scripts by switching to normal package imports
- [x] 6.3 Move reusable script logic into package modules under `vnpy/alpha/research` or other appropriate alpha packages
- [x] 6.4 Replace ad hoc `print`/broad exception patterns with consistent logging or explicit raised errors where safe
- [x] 6.5 Run script import/compile checks and targeted CLI tests

## 7. Final Validation

- [x] 7.1 Run all focused tests added by this change
- [x] 7.2 Run existing alpha strategy, research, dataset, and model tests that are available in the environment
- [x] 7.3 Run `python3 -m py_compile` on touched `vnpy/alpha` modules and scripts
- [x] 7.4 Review public imports and facade method names for compatibility
- [x] 7.5 Update task checkboxes as each implementation group completes
