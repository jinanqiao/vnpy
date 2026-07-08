## ADDED Requirements

### Requirement: Safe Expression Validation
The system SHALL validate factor expressions before execution using an explicit allowlist of names, functions, operators, and call patterns.

#### Scenario: Valid expression executes
- **WHEN** an expression uses allowed columns, registered functions, and supported operators
- **THEN** the expression executes and returns the same `DataProxy` result shape as the current expression layer

#### Scenario: Unsafe expression is rejected
- **WHEN** an expression attempts attribute access, imports, lambdas, comprehensions, assignment, or unknown function calls
- **THEN** the expression is rejected before execution with a clear validation error

### Requirement: Expression Diagnostics
The system SHALL provide actionable error messages for invalid expressions.

#### Scenario: Unknown column is reported
- **WHEN** an expression references a name that is not an input column or allowed function
- **THEN** the error identifies the unknown name

#### Scenario: Unsupported operator is reported
- **WHEN** an expression uses an unsupported operator
- **THEN** the error identifies the unsupported operator class

### Requirement: Compatibility Coverage
The system MUST include tests for existing arithmetic, comparison, time-series, cross-sectional, and custom registered function expressions.

#### Scenario: Current valid expressions remain valid
- **WHEN** the test suite evaluates representative existing alpha expressions
- **THEN** the validated expression layer produces compatible results
