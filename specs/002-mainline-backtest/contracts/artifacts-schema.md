# Artifacts Schema: 回测产物契约

每次回测运行在 `outputs/mainline_backtest/<run_id>/` 产出六件套。`run_id = <YYYYMMDD_HHMMSS>_<name>`。

## 文件清单

| 文件 | 格式 | 内容 |
|---|---|---|
| config.json | JSON | BacktestConfig 全量快照 + generated_at + 输入文件行数与日期范围 + selection 文件的路径与哈希 |
| nav.parquet | parquet | NavSeries（schema 见 data-model.md §3） |
| positions.parquet | parquet | PositionSnapshot 逐日持仓 |
| trades.parquet | parquet | TradeRecord 交易流水（含放弃/延迟/强平） |
| metrics.json | JSON | MetricsSummary（总体 + 分年度 + 基准段） |
| report.md | Markdown | 人读报告：核心指标表、净值/回撤描述、分年度表、成本拖累、异常交易汇总、固定局限性声明 |

## 不变式（端到端测试逐条断言）

1. **记账恒等**: 任意交易日 `nav × initial_capital = cash + position_value`（误差 < 1e-6 相对值）
2. **交易状态枚举**: trades.status ∈ {filled, deferred, abandoned, forced}；abandoned 行的价格/股数/成本全 null；deferred 行 defer_days ≥ 1
3. **无未来信息**: 任意 trades 行 `planned_date > signal_date`；executed_date ≥ planned_date
4. **等权偏差可归因**: 每调仓期每只正常成交个股的目标金额 = 期初净值 / 该期入选数；实际投入与目标的偏差 ≤ 1 手股价 + 成本
5. **净值连续**: nav 序列无 null、无跳变到非正数；空仓期 nav 变化 = 0（现金无息）
6. **影子净值**: nav_gross ≥ nav 恒成立（成本只会拖累）；--zero-cost 运行时两者相等
7. **可复现**: 同输入重跑，三个 parquet 逐字节一致
8. **固定排序**: trades (signal_date, vt_symbol, side)；positions/nav (datetime, vt_symbol)

## report.md 固定局限性声明

1. 分红按后复权收益隐含"自动再投资"处理，未建模现金分红到账时滞
2. 一字板用日线 `high==low` 近似识别，盘中打开的涨跌停无法识别（结果略保守）
3. 基准为价格指数（不含分红），组合含分红再投资，超额收益对组合略有利
4. 研究用途，不构成投资建议
