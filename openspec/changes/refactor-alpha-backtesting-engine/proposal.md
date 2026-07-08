## Why

The alpha backtesting engine is the core trust boundary for strategy research, but it currently combines data replay, order matching, cash accounting, daily settlement, statistics, and charting in one large module. Refactoring it now will reduce regression risk before more strategy features depend on its behavior.

## What Changes

- Split the current `BacktestingEngine` responsibilities into clearer internal components while preserving the public strategy-facing API.
- Extract deterministic order matching and cash/accounting behavior behind testable boundaries.
- Extract daily settlement and performance-statistics calculation from the engine orchestration path.
- Add regression tests for core alpha backtest semantics: order fill price, limit-up/limit-down blocking, cash movement, commission, daily PnL, and statistics outputs.
- Keep the existing `vnpy.alpha.strategy` external workflow working unless a later spec explicitly changes it.
- Do not change data loading, stock universe construction, strategy signal generation, or chart appearance as part of this change.

## Capabilities

### New Capabilities
- `alpha-backtesting-engine`: Defines the behavior and refactor constraints for alpha strategy replay, order matching, portfolio accounting, daily settlement, and backtest statistics.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `vnpy/alpha/strategy/backtesting.py`
  - Potential new internal modules under `vnpy/alpha/strategy/`
  - Focused tests under `tests/alpha/strategy/`
- Public APIs:
  - `BacktestingEngine.set_parameters`
  - `BacktestingEngine.add_strategy`
  - `BacktestingEngine.load_data`
  - `BacktestingEngine.run_backtesting`
  - `BacktestingEngine.calculate_result`
  - `BacktestingEngine.calculate_statistics`
  - Strategy-facing order, signal, cash, and holding-value methods
- Dependencies:
  - No new runtime dependency is expected.
  - Tests should use compact deterministic fixtures and avoid local market data files.
- Systems:
  - This change only targets the alpha backtesting engine; research data, stock pool, and turtle pipeline behavior remain out of scope.
