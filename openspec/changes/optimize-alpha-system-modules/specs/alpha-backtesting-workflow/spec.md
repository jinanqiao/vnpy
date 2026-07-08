## ADDED Requirements

### Requirement: Replay Orchestration Boundary
The system SHALL separate historical replay orchestration from order matching, accounting, settlement, statistics, and reporting.

#### Scenario: BacktestingEngine runs replay through a coordinator
- **WHEN** `BacktestingEngine.run_backtesting` replays bars
- **THEN** replay flow is isolated behind a coordinator or helper while preserving strategy callbacks

### Requirement: Reporting Boundary
The system SHALL separate charting and benchmark performance rendering from core backtest calculation.

#### Scenario: Chart methods remain compatible
- **WHEN** existing callers invoke `show_chart` or `show_performance`
- **THEN** those methods remain available and delegate rendering work outside the core engine

### Requirement: Error Propagation Policy
The system SHALL make replay errors visible without silently hiding calculation failures.

#### Scenario: Strategy replay raises an exception
- **WHEN** replay encounters an unexpected exception
- **THEN** the engine logs diagnostic context and follows an explicit error policy covered by tests
