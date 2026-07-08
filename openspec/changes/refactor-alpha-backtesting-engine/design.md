## Context

The current alpha backtesting implementation lives primarily in `vnpy/alpha/strategy/backtesting.py`. `BacktestingEngine` orchestrates historical replay, strategy callbacks, order creation/cancellation, limit-order matching, cash updates, daily close tracking, daily PnL settlement, statistics, and charting. `ContractDailyResult` and `PortfolioDailyResult` are also defined in the same module.

This makes calculation semantics difficult to isolate. A small bug fix in matching or settlement can accidentally alter cash, PnL, or statistics behavior without a focused regression test catching it. The refactor should improve internal boundaries while keeping the existing alpha strategy workflow stable.

## Goals / Non-Goals

**Goals:**

- Preserve the current strategy-facing `BacktestingEngine` API while moving internal responsibilities into focused modules.
- Make order matching deterministic and testable without running a full strategy replay.
- Make portfolio cash/accounting updates testable independently of order matching.
- Make daily settlement and performance statistics testable with compact fixtures.
- Add regression tests for edge cases that directly affect research trustworthiness.
- Keep implementation changes incremental so behavior differences are intentional and visible.

**Non-Goals:**

- Do not redesign the strategy template API.
- Do not replace `AlphaLab` or its data loading behavior.
- Do not change stock universe, signal generation, or research pipeline modules.
- Do not add benchmark-relative analytics or new report/chart output in this change.
- Do not introduce a new backtesting dependency.

## Decisions

### Decision 1: Use Internal Components Behind the Existing Engine

Keep `BacktestingEngine` as the public orchestration facade, but extract internal helpers:

- `OrderMatcher`: decides whether active limit orders cross a bar and produces filled trades plus order status updates.
- `PortfolioAccount`: tracks cash and applies trade turnover/commission.
- `DailySettlement`: owns `ContractDailyResult` and `PortfolioDailyResult`-style daily PnL calculation.
- `BacktestStatistics`: calculates balance, returns, drawdown, Sharpe, and related summary fields from daily results.

Rationale: Existing strategies depend on the engine object methods, so replacing the facade would create unnecessary migration work. Extracting components gives tests smaller surfaces while keeping users on the same workflow.

Alternative considered: Rewrite the engine around a new event-driven simulator. Rejected for this change because it would blur behavior-preservation with feature redesign.

### Decision 2: Preserve Current Matching Semantics Before Improving Them

The first implementation should preserve the existing fill rules:

- Long limit orders fill when order price is at or above bar low and the bar is not treated as full-day limit-up.
- Short limit orders fill when order price is at or below bar high and the bar is not treated as full-day limit-down.
- Long fill price is `min(order.price, bar.open_price)`.
- Short fill price is `max(order.price, bar.open_price)`.

Rationale: The current behavior may be simplified, but changing it during a structural refactor would make regressions hard to diagnose.

Alternative considered: Add realistic slippage, partial fills, and configurable price-limit rules. Rejected as a later feature change that deserves its own spec.

### Decision 3: Add Tests Before Moving Calculation Logic

Create characterization tests around the current behavior before extracting code. Tests should construct small `BarData`, `OrderData`, and `TradeData` fixtures directly and avoid local market data.

Rationale: The goal is safer refactoring. Tests written after extraction may accidentally encode the new implementation rather than the behavior we intended to preserve.

Alternative considered: Refactor first, then test. Rejected because it offers weaker protection for calculation semantics.

### Decision 4: Keep Plotting Outside the Initial Extraction Path

`show_chart` and `show_performance` can remain on the facade during this change unless a minimal move is required for import hygiene.

Rationale: Charting is not the highest-risk calculation surface. The urgent reliability risk is matching, accounting, settlement, and statistics.

Alternative considered: Split charting immediately into a report module. Deferred to avoid widening the change.

## Risks / Trade-offs

- [Risk] Characterization tests may lock in simplified or imperfect existing behavior. → Mitigation: Name tests as current-contract tests and document deferred behavior improvements in comments or follow-up specs.
- [Risk] Moving classes across modules can break imports for users who import `ContractDailyResult` or `PortfolioDailyResult` from `backtesting.py`. → Mitigation: Re-export compatibility names from `backtesting.py`.
- [Risk] Splitting components can introduce circular imports with `AlphaStrategy` or vn.py trader objects. → Mitigation: Keep component modules dependent on trader objects and simple data structures, not on strategy templates.
- [Risk] Statistics calculations may shift due to DataFrame ordering changes. → Mitigation: Sort daily results by date before building statistics fixtures and assert exact core fields.
- [Risk] The refactor may become a rewrite. → Mitigation: Keep tasks ordered as characterize, extract one boundary, run tests, then repeat.

## Migration Plan

1. Add focused tests for current behavior.
2. Extract matching logic with compatibility preserved through `BacktestingEngine.cross_order`.
3. Extract portfolio accounting with `BacktestingEngine` delegating cash updates.
4. Move daily result classes or settlement functions behind a compatibility import layer.
5. Extract statistics calculation while keeping `BacktestingEngine.calculate_statistics` return shape stable.
6. Run the relevant pytest suite after each extraction.

Rollback is straightforward: because the public facade remains, a problematic extraction can be reverted without changing strategy code.

## Open Questions

- Should future price-limit behavior support exchange-specific rules and ST/security-specific limits?
- Should future fills support partial volume and available-cash constraints?
- Should `calculate_statistics` eventually return a typed dataclass instead of a loose `dict`?
- Should charting/report output be moved to a separate alpha reporting module in a later change?
