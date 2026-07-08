## ADDED Requirements

### Requirement: Engine Public Workflow Compatibility
The system SHALL preserve the existing alpha backtesting workflow exposed by `BacktestingEngine` while refactoring its internal implementation.

#### Scenario: Existing strategy workflow remains available
- **WHEN** a caller configures parameters, adds a strategy, loads data, runs backtesting, calculates results, and calculates statistics through `BacktestingEngine`
- **THEN** those public methods remain available with compatible argument names and return shapes

#### Scenario: Strategy-facing order helpers remain available
- **WHEN** a strategy uses the engine to send orders, cancel orders, read signals, read cash, read holding value, or inspect orders/trades
- **THEN** the engine exposes compatible methods for those interactions

### Requirement: Deterministic Limit Order Matching
The system SHALL match alpha backtest limit orders deterministically according to the current bar, order side, order price, previous close, and price tick.

#### Scenario: Long limit order crosses the bar
- **WHEN** a long limit order price is greater than or equal to the bar low and the bar is not blocked by the limit-up rule
- **THEN** the order is marked fully traded and a trade is created at the lesser of the order price and the bar open price

#### Scenario: Short limit order crosses the bar
- **WHEN** a short limit order price is less than or equal to the bar high and the bar is not blocked by the limit-down rule
- **THEN** the order is marked fully traded and a trade is created at the greater of the order price and the bar open price

#### Scenario: Limit-up bar blocks long fills
- **WHEN** the bar low is greater than or equal to the rounded previous close multiplied by 1.1
- **THEN** a long limit order is not filled even if its price is otherwise crossable

#### Scenario: Limit-down bar blocks short fills
- **WHEN** the bar high is less than or equal to the rounded previous close multiplied by 0.9
- **THEN** a short limit order is not filled even if its price is otherwise crossable

### Requirement: Portfolio Cash Accounting
The system SHALL update cash from filled trades using contract size, trade price, volume, direction, and side-specific commission rates.

#### Scenario: Long trade reduces cash
- **WHEN** a long trade is filled
- **THEN** cash decreases by trade price multiplied by volume multiplied by contract size plus long-side commission

#### Scenario: Short trade increases cash net of commission
- **WHEN** a short trade is filled
- **THEN** cash increases by trade price multiplied by volume multiplied by contract size minus short-side commission

### Requirement: Daily Settlement Semantics
The system SHALL calculate daily contract and portfolio PnL from previous closes, start positions, close prices, trades, contract sizes, and side-specific commission rates.

#### Scenario: Holding PnL uses start position
- **WHEN** a contract has a start position and a previous close
- **THEN** holding PnL equals start position multiplied by close price minus previous close multiplied by contract size

#### Scenario: Trading PnL uses trade direction and close price
- **WHEN** a contract has same-day trades
- **THEN** trading PnL reflects each position change multiplied by close price minus trade price multiplied by contract size

#### Scenario: Portfolio daily result aggregates contract results
- **WHEN** multiple contract daily results are calculated for the same date
- **THEN** portfolio trade count, turnover, commission, trading PnL, holding PnL, total PnL, and net PnL equal the sum of their contract results

### Requirement: Backtest Statistics Compatibility
The system SHALL calculate the existing statistics fields from daily net PnL and capital without changing the returned field names.

#### Scenario: Statistics include existing keys
- **WHEN** daily results are available and balances remain positive
- **THEN** statistics include the existing start date, end date, trading-day counts, balance, PnL, commission, turnover, return, drawdown, Sharpe, and return-drawdown fields

#### Scenario: Statistics update daily DataFrame
- **WHEN** statistics are calculated
- **THEN** the engine daily DataFrame includes balance, return, high watermark, drawdown, and drawdown-percent columns

### Requirement: Regression Tests for Refactor Boundaries
The system MUST include focused regression tests for matching, cash accounting, settlement, and statistics behavior before or alongside extraction.

#### Scenario: Tests use deterministic in-memory fixtures
- **WHEN** regression tests construct backtest inputs
- **THEN** they use compact in-memory fixtures rather than local market data files

#### Scenario: Refactor keeps tests passing
- **WHEN** internal matching, accounting, settlement, or statistics code is moved to new modules
- **THEN** the focused regression tests continue to pass without requiring strategy notebook or local data execution
