## ADDED Requirements

### Requirement: Repository Boundaries
The system SHALL separate alpha storage responsibilities into focused repositories or stores while preserving the `AlphaLab` facade.

#### Scenario: AlphaLab delegates bar operations
- **WHEN** a caller saves or loads bar data through `AlphaLab`
- **THEN** `AlphaLab` delegates storage work to a bar repository without changing the caller-facing method signature

#### Scenario: AlphaLab delegates artifact operations
- **WHEN** a caller saves or loads datasets, models, or signals through `AlphaLab`
- **THEN** `AlphaLab` delegates artifact persistence to focused stores

### Requirement: Storage Compatibility
The system SHALL preserve existing file layout compatibility unless a migration is explicitly specified.

#### Scenario: Existing parquet bars remain readable
- **WHEN** existing daily or minute parquet files are present under the current lab directory layout
- **THEN** the new bar repository reads them through the existing `AlphaLab` workflow

#### Scenario: Missing files remain explicit
- **WHEN** requested storage artifacts do not exist
- **THEN** the facade returns the existing compatible empty value or `None` and records a clear log message

### Requirement: Store Tests
The system MUST include deterministic tests for each extracted storage boundary.

#### Scenario: Tests use temporary directories
- **WHEN** storage tests write files
- **THEN** they write only to pytest temporary directories and do not require local market data
