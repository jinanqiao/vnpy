## ADDED Requirements

### Requirement: Research frames support preflight validation
The system SHALL provide reusable validation for research dataframes before they are used for indicators, signals, backtests, or reports.

#### Scenario: Required columns missing
- **WHEN** a research dataframe lacks required columns for a stage
- **THEN** validation fails with the missing column names

#### Scenario: Duplicate symbol-date keys exist
- **WHEN** a price or signal dataframe contains duplicate `datetime` and `vt_symbol` keys where uniqueness is required
- **THEN** validation fails or returns a structured failure according to the stage policy

### Requirement: Research validation covers ordering and null policy
The system SHALL validate sortedness, null policy, and numeric column sanity for backtest-ready frames.

#### Scenario: Frame is unsorted
- **WHEN** a research stage requires sorted `vt_symbol` and `datetime` data
- **THEN** validation either returns a sorted frame explicitly or reports that input is unsorted

#### Scenario: Critical numeric column contains nulls
- **WHEN** a critical numeric column contains nulls after the allowed warm-up period
- **THEN** validation reports the column and affected row count

### Requirement: Point-in-time assumptions are explicit
The system SHALL make point-in-time assumptions visible in validation or pipeline summaries for universe, indicators, signals, and backtest inputs.

#### Scenario: Pipeline uses current snapshot universe
- **WHEN** a research pipeline uses current snapshot universe membership instead of historical point-in-time membership
- **THEN** the summary or validation result explicitly records that limitation

#### Scenario: Signal frame enters backtest
- **WHEN** a signal frame is passed to a backtest stage
- **THEN** validation confirms that target positions are shifted or that the backtest stage applies the shift before returns are earned
