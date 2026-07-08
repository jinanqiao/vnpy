## ADDED Requirements

### Requirement: Alpha scripts emit consistent logs
Alpha scripts SHALL use consistent logging for progress, warnings, and completion messages instead of ad hoc progress printing where safe.

#### Scenario: Script starts a long-running job
- **WHEN** an alpha script begins downloading, validating, or building research artifacts
- **THEN** it logs the job name, input parameters, output location, and major stage transitions

#### Scenario: Script completes successfully
- **WHEN** an alpha script finishes successfully
- **THEN** it logs a concise completion summary including key row counts and artifact paths

### Requirement: Alpha jobs produce structured run summaries
Long-running alpha research or data-lake jobs SHALL be able to produce a structured summary containing inputs, outputs, counts, failures, and timestamps.

#### Scenario: Data-lake build has partial failures
- **WHEN** a gateway-backed data-lake build succeeds with failed symbols or indexes
- **THEN** the run summary records the failed identifiers and the successful artifact paths

#### Scenario: Research pipeline writes artifacts
- **WHEN** a research pipeline writes config, data quality, signals, equity, metrics, figures, and report artifacts
- **THEN** the run summary records those artifact paths and key metrics

### Requirement: Failures preserve diagnostic context
Alpha scripts SHALL catch only expected external-service or data-quality failures and MUST preserve enough context for diagnosis.

#### Scenario: Gateway request fails for one symbol
- **WHEN** a gateway request fails for one symbol during a batch download
- **THEN** the job records the symbol, exception text, and continues only when partial failure is explicitly supported

#### Scenario: Required input is invalid
- **WHEN** required local input data is missing or invalid
- **THEN** the script fails fast with a clear error rather than silently producing incomplete artifacts
