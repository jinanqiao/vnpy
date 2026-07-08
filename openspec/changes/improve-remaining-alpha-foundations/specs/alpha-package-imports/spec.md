## ADDED Requirements

### Requirement: Alpha scripts use package imports
Alpha scripts SHALL import reusable code through package paths instead of mutating `sys.path`.

#### Scenario: Script is imported by tests
- **WHEN** a test imports an alpha script module
- **THEN** the import succeeds without inserting research directories into `sys.path`

#### Scenario: Script is run from repository root
- **WHEN** a user runs an alpha script from the repository root
- **THEN** package imports resolve normally and CLI behavior remains compatible

### Requirement: Alpha research tests use normal imports where practical
Alpha research tests SHALL prefer package imports over custom file loaders or path mutation once modules are package-safe.

#### Scenario: Test imports turtle pipeline
- **WHEN** a research test imports turtle pipeline functions
- **THEN** it imports from `vnpy.alpha.research` rather than a test-only loader where practical

#### Scenario: Direct module loading is still needed
- **WHEN** a test truly requires direct file loading for compatibility coverage
- **THEN** the reason is isolated to that test and does not become the default import path

### Requirement: Package exports remain intentional
Alpha package `__init__` files SHALL expose stable public helpers intentionally and avoid importing heavy optional modules at package import time.

#### Scenario: Import alpha research package
- **WHEN** a user imports `vnpy.alpha.research`
- **THEN** the import succeeds quickly and does not trigger optional model, plotting, or gateway work
