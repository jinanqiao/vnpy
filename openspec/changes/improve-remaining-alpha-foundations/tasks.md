## 1. Optional Dependency Boundaries

- [x] 1.1 Add characterization tests for importing alpha package, model modules, and research modules when `torch`, `lightgbm`, or plotting extras are unavailable
- [x] 1.2 Create an alpha optional-dependency helper that returns actionable errors naming the missing package and install guidance
- [x] 1.3 Refactor MLP and LightGBM model imports so package/module discovery does not require heavy optional dependencies until feature use
- [x] 1.4 Refactor plotting or gateway-adjacent research imports where feasible so non-plot and non-gateway paths remain importable
- [x] 1.5 Run model import tests and existing alpha model boundary tests

## 2. Alpha Observability

- [x] 2.1 Add tests for script logging/run-summary behavior using deterministic temporary outputs
- [x] 2.2 Introduce reusable research run summary structures for inputs, outputs, counts, failures, timestamps, and metrics
- [x] 2.3 Convert remaining alpha script progress prints to logging where safe while preserving CLI exit behavior
- [x] 2.4 Add optional summary output support for long-running research/data-lake scripts where practical
- [x] 2.5 Run alpha script smoke tests and targeted research pipeline tests

## 3. Research Data Contracts

- [x] 3.1 Add tests for missing columns, duplicate `datetime`/`vt_symbol` keys, unsorted frames, null numeric columns, and point-in-time assumption reporting
- [x] 3.2 Create reusable research dataframe validation helpers and structured validation result objects
- [x] 3.3 Integrate validation into turtle data, indicator, signal, backtest, and pipeline stages without changing valid outputs
- [x] 3.4 Ensure validation summaries record known limitations such as current-snapshot universe membership
- [x] 3.5 Run alpha research tests and turtle pipeline tests

## 4. Package Import Hygiene

- [x] 4.1 Add tests that import research modules and scripts through normal package/module imports without test-only path mutation
- [x] 4.2 Replace default use of `tests/alpha/research/module_loader.py` with package imports where practical
- [x] 4.3 Keep any remaining direct-file-loading compatibility tests isolated and documented by test name
- [x] 4.4 Review `vnpy/alpha/research/__init__.py` and related package exports so imports stay intentional and lightweight
- [x] 4.5 Run alpha research and script import tests

## 5. Alpha Quality Gates

- [x] 5.1 Add a repeatable alpha validation command or script for focused pytest, import checks, and compile checks
- [x] 5.2 Add tests or smoke checks for the validation command composition
- [x] 5.3 Add scoped ruff and type-check command documentation or wrappers for alpha modules where feasible
- [x] 5.4 Ensure the quality gate reports skipped optional dependency checks distinctly from failures
- [x] 5.5 Run the new alpha validation command locally

## 6. Final Validation

- [x] 6.1 Run all focused tests added by this change
- [x] 6.2 Run existing available alpha dataset, model, strategy, research, and script tests
- [x] 6.3 Run `python3 -m py_compile` on touched `vnpy/alpha` modules and scripts
- [x] 6.4 Run `openspec validate improve-remaining-alpha-foundations --strict`
- [x] 6.5 Update task checkboxes as each implementation group completes
