## Context

The alpha subsystem now has clearer internal boundaries, but several operational details still make day-to-day research fragile. Some optional features import heavy packages at module import time, scripts print progress without reusable summaries, tests still contain local research module loading helpers, and validation relies on manually remembering which pytest or compile commands to run.

The project already declares alpha optional dependencies in `pyproject.toml`, uses pytest, and prefers deterministic in-memory fixtures. The next optimization should strengthen reliability without changing trading behavior or adding a new strategy.

## Goals / Non-Goals

**Goals:**
- Make optional alpha features fail with actionable messages only when the feature is used.
- Add consistent logging and structured summaries for research and data-lake scripts.
- Define reusable preflight data contracts for research frames before indicators, signals, and backtests.
- Remove remaining path-mutation import patterns from alpha research tests and scripts where practical.
- Provide one or more repeatable alpha quality commands for local validation.

**Non-Goals:**
- Do not change strategy logic, factor formulas, or portfolio accounting semantics.
- Do not require `torch`, `lightgbm`, or gateway services for ordinary package imports.
- Do not migrate historical local data files or generated outputs.
- Do not enforce full-repo strict typing as part of this change.

## Decisions

1. Use small internal helper modules instead of new required dependencies.

   Rationale: Optional dependency checks, logging setup, validation summaries, and command wrappers can be implemented with the standard library plus existing project dependencies. This keeps alpha usable in lightweight research environments.

   Alternative considered: Add a CLI framework or validation library. Rejected because the current scripts are simple and the main need is consistency, not a new framework.

2. Defer optional imports until feature use where feasible.

   Rationale: Importing `vnpy.alpha` or a research module should not fail because the user has not installed a model-specific or plotting-specific dependency. Model construction, plotting, or gateway calls are the correct points to raise actionable dependency errors.

   Alternative considered: Keep top-level imports and rely on optional test skips. Rejected because import-time failure blocks discovery and makes Cursor/Claude/Codex navigation less reliable.

3. Add research data preflight contracts as reusable functions returning structured results.

   Rationale: Schema, duplicate key, sortedness, null policy, and point-in-time assumptions are cross-cutting concerns. Reusable validators make the turtle pipeline and future pipelines safer without embedding one-off checks in every stage.

   Alternative considered: Put validation directly inside each pipeline function. Rejected because future research pipelines would duplicate the same checks.

4. Treat alpha quality gates as developer commands rather than production runtime behavior.

   Rationale: Fast focused checks help iteration, while broad full-repo type/lint enforcement may create noisy unrelated failures. The quality gate should start with deterministic alpha tests, import checks, compile checks, and limited lint/type checks where practical.

   Alternative considered: Enable strict CI for the whole repository immediately. Rejected because this change should stay scoped to the alpha subsystem.

## Risks / Trade-offs

- Optional import deferral can hide dependency errors until later execution. → Mitigate with explicit tests for missing dependency messages and documented quality checks.
- Logging changes can alter script stdout that users may rely on informally. → Preserve CLI exit behavior and core messages while adding structured summaries.
- Data contracts can initially reject messy but usable research data. → Make validation policies explicit and allow pipeline-specific null or duplicate policies where justified.
- Quality gates can become stale if implemented as documentation only. → Add executable scripts or command definitions and test the command composition where feasible.

## Migration Plan

1. Add tests for current optional import failures, script parser/import behavior, and research frame validation expectations.
2. Introduce helper modules for optional dependency checks, logging/run summaries, and research data contracts.
3. Refactor scripts and modules in small batches while preserving entrypoints.
4. Add alpha quality commands and verify they run in the current environment.
5. Keep rollback simple by preserving old CLI names and public model/pipeline APIs.

## Open Questions

- Should alpha quality gates become project-level CI later, or remain a local developer command for now?
- Should gateway-backed scripts output JSON summaries by default or only when an explicit `--summary-path` argument is provided?
- Which optional model dependencies should be imported lazily first: `lightgbm`, `torch`, or both in the same implementation phase?
