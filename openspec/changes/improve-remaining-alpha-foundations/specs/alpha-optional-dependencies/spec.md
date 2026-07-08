## ADDED Requirements

### Requirement: Optional alpha imports remain lightweight
The system SHALL allow importing alpha packages and non-dependent alpha modules without requiring optional model, plotting, or gateway-only dependencies.

#### Scenario: Import alpha package without model extras
- **WHEN** a user imports `vnpy.alpha` or non-model alpha modules in an environment without `torch` or `lightgbm`
- **THEN** the import succeeds unless the user directly constructs or uses a feature requiring those dependencies

#### Scenario: Import research pipeline without plotting extra
- **WHEN** a user imports research pipeline modules without invoking plot generation
- **THEN** missing plotting dependencies do not prevent import or non-plot execution

### Requirement: Missing optional dependency errors are actionable
The system SHALL raise errors that name the missing package, the alpha feature requiring it, and the relevant optional install group or package name.

#### Scenario: Use MLP model without torch
- **WHEN** a user constructs or trains an MLP model and `torch` is unavailable
- **THEN** the system raises an actionable dependency error mentioning `torch` and the alpha optional dependency group

#### Scenario: Use LightGBM model without lightgbm
- **WHEN** a user constructs or trains a LightGBM model and `lightgbm` is unavailable
- **THEN** the system raises an actionable dependency error mentioning `lightgbm` and the alpha optional dependency group

### Requirement: Optional dependency behavior is covered by tests
The system SHALL include focused tests for both available and unavailable optional dependency paths without forcing heavy dependencies to be installed.

#### Scenario: Dependency unavailable in test environment
- **WHEN** an optional dependency is unavailable during tests
- **THEN** tests verify the actionable error path or skip only the dependency-specific execution path
