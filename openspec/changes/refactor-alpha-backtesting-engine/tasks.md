## 1. Characterize Current Behavior

- [x] 1.1 Add compact pytest fixtures for `BarData`, `OrderData`, `TradeData`, contract settings, and a minimal engine/strategy double under `tests/alpha/strategy/`
- [x] 1.2 Add tests for current long and short limit-order crossing behavior, including fill price selection
- [x] 1.3 Add tests for limit-up blocking of long fills and limit-down blocking of short fills
- [x] 1.4 Add tests for long and short cash movement with contract size and side-specific commission
- [x] 1.5 Add tests for contract daily PnL and portfolio daily aggregation
- [x] 1.6 Add tests for `calculate_statistics` return keys and daily DataFrame columns

## 2. Extract Order Matching

- [x] 2.1 Create an internal order-matching module under `vnpy/alpha/strategy/`
- [x] 2.2 Move limit-order crossing and fill-price decisions into the matcher while preserving current semantics
- [x] 2.3 Update `BacktestingEngine.cross_order` to delegate matching and continue publishing order/trade callbacks through the existing engine flow
- [x] 2.4 Run the order matching and cash-accounting tests

## 3. Extract Portfolio Accounting

- [x] 3.1 Create an internal portfolio/accounting helper for cash updates from filled trades
- [x] 3.2 Move turnover, commission, and cash delta calculations out of `cross_order`
- [x] 3.3 Keep `BacktestingEngine.get_cash_available` behavior compatible
- [x] 3.4 Run the cash-accounting and order matching tests

## 4. Extract Daily Settlement

- [x] 4.1 Move `ContractDailyResult` and `PortfolioDailyResult` logic into a settlement-focused module
- [x] 4.2 Re-export compatibility names from `vnpy/alpha/strategy/backtesting.py` if external imports could rely on them
- [x] 4.3 Update `BacktestingEngine.calculate_result` to delegate daily PnL aggregation without changing its return DataFrame schema
- [x] 4.4 Run the daily settlement tests

## 5. Extract Statistics Calculation

- [x] 5.1 Create a statistics helper that calculates balance, returns, drawdown, Sharpe, and summary fields from daily results
- [x] 5.2 Update `BacktestingEngine.calculate_statistics` to delegate calculations while preserving returned field names and logging behavior
- [x] 5.3 Keep `self.daily_df` updated with balance, return, high watermark, drawdown, and drawdown-percent columns
- [x] 5.4 Run the statistics tests

## 6. Validate Integration

- [x] 6.1 Run the focused alpha strategy test suite
- [x] 6.2 Run existing alpha research tests that may import alpha strategy modules
- [x] 6.3 Run `python3 -m py_compile` on touched alpha strategy modules
- [x] 6.4 Review imports and public names to ensure existing strategy code remains compatible
- [x] 6.5 Update task checkboxes as implementation work completes
