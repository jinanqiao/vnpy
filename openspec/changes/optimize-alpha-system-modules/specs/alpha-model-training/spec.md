## ADDED Requirements

### Requirement: Model Training Boundaries
The system SHALL separate alpha model data adaptation, training loop, prediction, network structure, and model-detail responsibilities.

#### Scenario: MlpModel remains the public facade
- **WHEN** existing code constructs and uses `MlpModel.fit`, `MlpModel.predict`, or `MlpModel.detail`
- **THEN** those methods remain available while delegating to focused internals

#### Scenario: Dataset adaptation is testable
- **WHEN** a compact alpha dataset fixture is passed to the model adapter
- **THEN** it returns deterministic feature, label, and feature-name structures without running a full training loop

### Requirement: Reproducible Training Behavior
The system SHALL keep seed-controlled training behavior explicit.

#### Scenario: Seed configures random generators
- **WHEN** a model is constructed with a seed
- **THEN** numpy and torch random behavior used by training is initialized consistently with the existing behavior

### Requirement: Model Detail Semantics
The system SHALL avoid presenting random-input feature importance as authoritative model explanation without clear isolation.

#### Scenario: Feature detail is requested
- **WHEN** `MlpModel.detail` calculates feature details
- **THEN** the implementation is isolated behind a detail helper and can be replaced or tested independently
