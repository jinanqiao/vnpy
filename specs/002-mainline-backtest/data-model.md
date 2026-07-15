# Data Model: 主线强势股策略回测

**Date**: 2026-07-08　实体分四组：配置、输入、过程记录、输出。全部表用 polars DataFrame 表达，落盘为 parquet/JSON。

## 1. 配置实体

### BacktestConfig（唯一的类，frozen dataclass）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| selection_path | str | （必填） | 001 产物 selection.parquet 路径 |
| data_dir | str | "data" | 数据湖根目录 |
| adjusted_bars_file | str | "normalized/daily_bars_all_a_adjusted.parquet" | 后复权日线 |
| unadjusted_bars_file | str | "normalized/daily_bars_all_a.parquet" | 未复权日线（整手股数计算） |
| benchmark_file | str | "benchmark/index_daily.parquet" | 基准指数 |
| benchmark_symbol | str | "000300.SH" | 沪深300 |
| execution_universe_file | str | "universe/execution_universe.parquet" | 可交易性 |
| trading_dates_file | str | "calendar/trading_dates.parquet" | 交易日历 |
| initial_capital | float | 1_000_000.0 | 初始资金（元） |
| commission_rate | float | 0.00025 | 佣金（双边） |
| stamp_tax_rate | float | 0.0005 | 印花税（卖出） |
| slippage_rate | float | 0.001 | 滑点（单边，调整执行价） |
| lot_size | int | 100 | 整手股数 |
| suspend_freeze_days | int | 20 | 停牌超过此天数在报告单列 |
| start / end | str | "" | 只回测此区间内的调仓期（空=全部） |
| output_dir | str | "outputs/mainline_backtest" | 产物根目录 |
| name | str | "mainline_backtest" | 实验名 |

约束：`weighting` 本版只有等权（不设参数，避免未用配置）；成本任一项可置 0。

## 2. 输入实体（数据湖，只读）

- **SelectionList**（001 契约）: rebalance_date, vt_symbol, industry, score, industry_rank
- **AdjustedBars**（新增文件）: datetime, vt_symbol, open, high, low, close, volume, turnover（后复权价格）
- **UnadjustedBars**（现有）: 同上 schema（未复权，仅用于整手股数计算与校验）
- **BenchmarkBars**（现有）: datetime, index_symbol, open, high, low, close, volume, turnover
- **ExecutionUniverse / TradingDates**（现有）: 同 001

## 3. 过程记录实体（回测产物核心）

### TradeRecord（交易流水，trades.parquet 一行一笔）

| 字段 | 类型 | 说明 |
|---|---|---|
| signal_date | date | 信号来源调仓日 |
| planned_date | date | 计划执行日（信号日次一交易日） |
| executed_date | date? | 实际执行日（延迟卖出时 > planned_date；放弃时 null） |
| vt_symbol / industry | str | 标的 |
| side | str | buy / sell |
| status | str | filled / deferred（延迟成交）/ abandoned（放弃）/ forced（退市强平） |
| reason | str? | 未正常成交原因: suspended / limit_up / limit_down / delisted / insufficient_cash（连一手都买不起）, 正常成交为 null |
| exec_price_raw | f64? | 执行日未复权开盘价（真实成交价口径） |
| exec_price_adj | f64? | 执行日后复权开盘价（收益演化口径） |
| shares | i64? | 成交股数（100 的整数倍） |
| gross_amount | f64? | 成交金额（未复权价 × 股数） |
| commission / stamp_tax / slippage_cost | f64? | 成本分项（元） |
| defer_days | i32? | 延迟天数（deferred 时有值） |

### PositionSnapshot（逐日持仓，positions.parquet 一行一持仓一日）

| 字段 | 类型 | 说明 |
|---|---|---|
| datetime | date | 交易日 |
| vt_symbol / industry | str | 标的 |
| shares | i64 | 持有股数 |
| cost_amount | f64 | 买入投入金额（含成本） |
| market_value | f64 | 当日市值（份额演化模型，见 research R3） |
| weight | f64 | 占组合净值比例 |
| is_frozen | bool | 当日停牌按最后价冻结估值 |

### NavSeries（净值序列，nav.parquet 一行一交易日）

| 字段 | 类型 | 说明 |
|---|---|---|
| datetime | date | 交易日 |
| nav | f64 | 组合净值（归一为 1 起步） |
| nav_gross | f64 | 零成本影子净值 |
| cash | f64 | 现金余额（元） |
| position_value | f64 | 持仓总市值（元） |
| benchmark_nav | f64 | 基准净值（同起点归一） |
| excess_nav | f64 | nav / benchmark_nav |
| n_holdings | i32 | 持仓只数（0 = 空仓期） |

### 不变式

1. `nav[t] × initial_capital = cash[t] + position_value[t]`（记账恒等式，SC-002）
2. trades 中每笔 filled/deferred/forced 的成本分项 ≥ 0，abandoned 的金额字段全 null
3. 每个调仓期：目标持仓 = 该期 selection 全体（等权），偏差只能来自 abandoned/deferred/整手取整
4. 固定排序：trades 按 (signal_date, vt_symbol, side)；positions/nav 按 (datetime, vt_symbol)
5. 同输入重跑，三个 parquet 逐字节一致（SC-004）

## 4. 输出汇总实体

### MetricsSummary（metrics.json）

顶层：total_return / annual_return / max_drawdown / sharpe / monthly_win_rate / win_vs_benchmark_rate / annual_turnover / cost_drag（口径见 research R6）；`by_year` 数组按年度重复同组指标；`benchmark` 段含基准同期指标。

### 数据质量与校验（data_quality.json）

下载校验三项结果（research R8）+ 回测过程记录（放弃买入笔数、延迟卖出笔数、长期停牌单列、强制清仓清单）。
