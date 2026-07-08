# PIT 股票池改造计划

目标：把当前“当前快照股票池”升级成“按交易日展开的近似 point-in-time 股票池”，并形成研究池、可交易池、执行池三层结构。

## 1. 数据输入

- `data/universe/all_a_symbols.parquet`：股票主数据快照。
- `data/calendar/trading_dates.parquet`：交易日历。
- `data/normalized/daily_bars.parquet`：全 A 日 K。

## 2. 输出表

- `daily_universe.parquet`：每日股票是否存在于研究范围。
- `daily_status.parquet`：每日状态、流动性、上市天数、停牌近似。
- `research_universe.parquet`：研究股票池。
- `tradable_universe.parquet`：可交易股票池。
- `execution_universe.parquet`：执行股票池。
- `universe_manifest.json`：构建参数、行数和已知限制。
- `universe_verification.md`：验证报告。

## 3. 三层含义

- 研究池：历史上当天应被纳入研究范围的股票。
- 可交易池：过滤 ST、停牌/缺行情、新股、低价、低成交额。
- 执行池：在可交易基础上保留可下单执行的股票。

## 4. 当前限制

- 历史 ST 用当前名称近似。
- 停牌用零成交量、零成交额或缺行情近似。
- 历史涨跌停和公司行动 PIT 表暂未接入。
- 股票主数据仍来自当前 QMT 快照，不是供应商级历史主数据。
