## ADDED Requirements

### Requirement: Staged Experiment Execution
The system SHALL represent research pipeline execution as explicit stages for loading, validation, indicators, signals, backtest, metrics, plots, and report generation.

#### Scenario: Pipeline returns structured result
- **WHEN** the turtle pipeline runs
- **THEN** it returns a structured experiment result containing key intermediate outputs or artifact paths

#### Scenario: Stages can be tested independently
- **WHEN** a test invokes a stage with compact fixture data
- **THEN** the stage can run without writing a full experiment folder

### Requirement: Artifact Writing Boundary
The system SHALL separate artifact writing from core calculation stages.

#### Scenario: Artifacts are written by a writer
- **WHEN** signals, equity curves, metrics, plots, reports, or configs are persisted
- **THEN** a dedicated artifact writer handles file paths and serialization

### Requirement: Reproducibility Metadata
The system SHALL record configuration and run metadata for experiment outputs.

#### Scenario: Experiment is saved
- **WHEN** a pipeline run writes artifacts
- **THEN** the output includes configuration and enough metadata to identify the run inputs and parameters
