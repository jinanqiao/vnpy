# Artifacts Schema: 风控层产物契约（002 契约的增量）

002 的六件套文件清单与八条不变式全部继续成立。本文件只定义增量。

## 不变式增补（端到端测试逐条断言）

9. **枚举封闭扩展**: trades.reason ∈ {suspended, limit_up, limit_down, delisted, insufficient_cash, timing_exit, no_signal_exit, stop_loss}；新增三值只出现在 side=sell 行
10. **回归保护**: 全部风控开关关闭时，nav/positions/trades 三个 parquet 与 002 现状逐字节一致；trades 中新增三种 reason 出现 0 次
11. **择时空仓**: timing_skip 时段（数据质量日志可查）内 positions.parquet 无该时段持仓行（除停牌挂起补卖的遗留持仓），nav 变化仅来自遗留持仓与现金
12. **止损可追溯**: 每笔 reason=stop_loss 的卖出，在 data_quality.json 中存在同一 vt_symbol 的 stop_loss_trigger 事件且触发日早于执行日
13. **风控只减仓**: 任何交易日的持仓数量变化中，风控触发的方向恒为减少；无信号月与择时空仓期无任何买入

## report.md 增补段落

- "风控动作汇总"：择时跳过期数、无信号清仓次数、止损触发笔数（开关关闭时显示"未启用"）

## comparison.md（run_risk_experiments.py 产物，人读）

- 四组指标对照表（组名、开关、总收益、年化、最大回撤、夏普、月度胜率、换手、成本拖累）
- 边际贡献段：timing−baseline / timing_ns−timing / all_on−timing_ns 的回撤与年化差值
- 各组产物目录路径（可追溯到完整六件套）
