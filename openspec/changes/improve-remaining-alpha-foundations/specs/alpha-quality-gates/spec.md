## ADDED Requirements

### Requirement: Alpha validation command is repeatable
The system SHALL provide a repeatable alpha-focused validation command or documented script that runs deterministic alpha checks in the current repository.

#### Scenario: Developer runs alpha validation
- **WHEN** a developer runs the alpha validation command
- **THEN** it executes focused alpha pytest subsets, import checks, and compile checks without requiring local market data files

#### Scenario: Optional dependencies are missing
- **WHEN** optional alpha dependencies such as `torch` or `lightgbm` are missing
- **THEN** the validation command reports skips or actionable optional dependency checks rather than failing unrelated tests

### Requirement: Alpha lint/type checks are scoped
The system SHALL define a scoped lint/type-check approach for alpha code that avoids noisy unrelated full-repo failures.

#### Scenario: Lint alpha files
- **WHEN** the alpha quality gate runs linting
- **THEN** it targets alpha modules, alpha tests, and alpha scripts touched by the change

#### Scenario: Type checking is not fully clean yet
- **WHEN** strict type checking has known legacy noise
- **THEN** the quality gate documents or isolates the feasible alpha subset rather than blocking on unrelated modules

### Requirement: Quality gate output is actionable
The alpha validation command SHALL report which checks ran, which checks were skipped, and what command failed.

#### Scenario: A test subset fails
- **WHEN** an alpha pytest subset fails
- **THEN** the validation output identifies the failing command so the developer can rerun it directly
