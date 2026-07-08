## ADDED Requirements

### Requirement: Package Import Hygiene
Alpha command-line scripts SHALL import reusable logic from package modules without mutating `sys.path`.

#### Scenario: CLI script starts
- **WHEN** a script is invoked from the project root
- **THEN** it imports `vnpy.alpha` modules through normal package imports

### Requirement: Thin CLI Entrypoints
Scripts SHALL be thin entrypoints responsible for argument parsing, logging setup, and calling package functions.

#### Scenario: CLI delegates work
- **WHEN** a script command executes
- **THEN** core data-building or pipeline logic runs in a package module that can be tested separately

### Requirement: CLI Feedback And Errors
Scripts SHALL provide consistent user-facing status and error handling.

#### Scenario: Command succeeds
- **WHEN** a script completes successfully
- **THEN** it reports the key output path or summary through a consistent logging mechanism

#### Scenario: Command fails
- **WHEN** a script encounters an expected recoverable error
- **THEN** it exits with a non-zero status or raises a clear exception rather than silently continuing
