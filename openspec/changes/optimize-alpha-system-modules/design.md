## Context

The alpha system has several modules that work, but their boundaries are still too broad for a long-lived quantitative research system. `AlphaLab` mixes storage and domain operations, the factor expression layer uses direct `eval`, the MLP model combines model structure and training orchestration, the backtesting facade still contains replay/reporting concerns, research pipelines write artifacts directly from orchestration functions, and scripts import research files through `sys.path` mutation.

The previous `refactor-alpha-backtesting-engine` change extracted matching, accounting, settlement, and statistics from the first part of the backtesting engine. This change continues the same direction across the rest of the alpha system.

## Goals / Non-Goals

**Goals:**

- Improve module boundaries without changing user-facing workflows unless compatibility wrappers are provided.
- Make storage, expression evaluation, model training, replay, reporting, experiment execution, and CLI behavior independently testable.
- Remove unsafe or brittle implementation patterns such as direct factor-expression `eval` and script-level `sys.path` mutation.
- Keep each implementation phase small enough to validate with focused pytest coverage.
- Preserve existing research outputs and public method names where practical.

**Non-Goals:**

- Do not rewrite the entire alpha framework in one pass.
- Do not change trading or strategy semantics unless a spec explicitly requires it.
- Do not migrate existing local data files.
- Do not introduce new model algorithms or strategy logic.
- Do not optimize runtime performance before correctness and boundaries are covered by tests.

## Decisions

### Decision 1: Implement by Capability Phase

Implementation should proceed in this order:

1. `alpha-lab-storage`
2. `alpha-factor-expression`
3. `alpha-model-training`
4. `alpha-backtesting-workflow`
5. `alpha-research-pipeline`
6. `alpha-cli-tools`

Rationale: This order starts with storage boundaries, then secures factor calculation, then improves model/research ergonomics. Backtesting has already had a first extraction, so it can be second-phase work rather than the first risk.

Alternative considered: Apply all modules in parallel. Rejected because cross-module changes would make regressions harder to isolate.

### Decision 2: Keep Compatibility Facades

Large existing classes such as `AlphaLab`, `MlpModel`, and `BacktestingEngine` should remain as public facades while delegating to new internal modules.

Rationale: The repo already has examples, notebooks, and user habits around these names. Facades preserve workflow while allowing internals to become testable.

Alternative considered: Replace public APIs immediately. Rejected because this is an optimization change, not a breaking redesign.

### Decision 3: Use Structured Validation for Expressions

The factor expression layer should parse expressions with Python AST or an equivalent structured parser, validate all names/operators/calls against allowlists, and only then execute through existing `DataProxy` functions.

Rationale: Direct `eval` is unsafe and gives poor diagnostics. A structured layer can reject unsafe syntax and produce useful errors without changing the factor function library.

Alternative considered: Replace expressions with pure Polars DSL only. Deferred because existing alpha expressions likely rely on the current function-style syntax.

### Decision 4: Separate Artifact Writing From Pipeline Execution

Research pipelines should return structured results and delegate files/reports/plots to artifact writers.

Rationale: This makes tests fast and deterministic: core calculations can be tested without touching output folders, while artifact writing can be tested separately.

Alternative considered: Keep pipeline as one orchestration function. Rejected because adding benchmarks, grids, and reproducibility metadata would bloat the function.

### Decision 5: Scripts Become Thin Entrypoints

Scripts should import package modules normally and only handle argument parsing, logging setup, and exit codes.

Rationale: Reusable logic belongs under `vnpy/alpha`, not in scripts that mutate `sys.path`.

Alternative considered: Keep scripts as experimental one-offs. Rejected because these scripts now build core research artifacts.

## Risks / Trade-offs

- [Risk] A broad optimization change may become too large to finish safely. → Mitigation: Keep tasks grouped by capability and run tests after each group.
- [Risk] Compatibility facades can hide old design problems. → Mitigation: Make delegation explicit and add tests for both facade and new internal modules.
- [Risk] Expression validation may reject previously accepted but unsafe expressions. → Mitigation: Add compatibility tests for known valid expressions and clear error messages for rejected expressions.
- [Risk] Model refactor may alter training reproducibility. → Mitigation: Add seed and prediction-shape tests before changing training internals.
- [Risk] CLI cleanup can break user commands. → Mitigation: Preserve arguments and add CLI smoke tests around parser/main functions.

## Migration Plan

1. Add tests around current behavior for the target module.
2. Introduce internal module or helper with the same behavior.
3. Delegate from the existing public facade.
4. Run targeted tests plus adjacent alpha tests.
5. Mark tasks complete and continue to the next capability.

Rollback can happen capability by capability because each public facade remains available.

## Open Questions

- Which existing notebooks or downstream scripts import `AlphaLab` internals directly?
- Should persisted dataset/model artifacts eventually include version metadata?
- Should expression validation support user-registered functions beyond the current registry?
- Should CLI commands eventually be exposed as package console scripts in `pyproject.toml`?
