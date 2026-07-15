# Research: 主线新鲜度规则

**Date**: 2026-07-09

## R1 月龄状态的计算位置

- **Decision**: 在 `mainline/pipeline.py` 的逐期循环里维护 `prev_streak: dict[行业, 月龄]`，每期调用 `industry.py` 新增的纯函数 `apply_freshness(snapshot, prev_streak, config)` 完成"标注 + 过滤 + 状态推进"。
- **Rationale**: 月龄是跨期状态，天然属于编排层；`calc_industry_snapshot` 保持"单期纯函数"不变，逐期循环已经按时间顺序执行，状态传递零成本。
- **Alternatives**: 循环结束后对全量快照做窗口函数事后标注——但过滤会影响个股层的当期计算，必须在循环内完成，事后标注做不到。

## R2 相邻期与月龄重置口径

- **Decision**: "上一个信号评估月" = 上一个产出行业快照的调仓日。行业在相邻两期都是原始主线（三道门槛 + TopN，未计新鲜度过滤）则月龄 +1，否则重置为 1；无主线月（快照存在但无 selected）会清空所有行业的月龄。
- **Rationale**: 001 每个月末都会评估（除历史窗口不足的开头几期），相邻期即相邻月末；无主线月意味着资金链断裂，行情重启后按"新主线"对待，与实证分析口径一致。
- **Alternatives**: 按自然月对齐——需要额外日历逻辑且与 001 调仓日历重复，放弃。

## R3 月龄口径与过滤解耦

- **Decision**: 月龄按"原始主线口径"计数：行业只要三道门槛 + TopN 达标就推进月龄，即使当期因 stale 被过滤未建仓。快照中被过滤行业记 `selected=False, reject_reason="stale"`，`industry_streak` 保留真实月龄。
- **Rationale**: 若月龄跟着过滤后的 selected 走，行业会"隔月复活"（第 2 月被过滤 → 第 3 月 streak 误判为 1 又可买），违背"老主线不追"的本意。
- **审计恢复**: 原始主线 = `selected OR reject_reason == "stale"`。

## R4 快照与入选清单的列扩展

- **Decision**: `SNAPSHOT_COLUMNS` 新增 `industry_streak`（Int32，非主线行业为 null）；`selection.parquet` 通过与行业快照按 (rebalance_date, industry) join 带上该列，`stocks.py` 完全不动。
- **Rationale**: 个股层与月龄无关，join 一次即可；002 回测的 `load_selection` 只校验必需列存在，多一列无感知（已核实 `_read_parquet` 只查缺列）。

## R5 对照实验设计

- **Decision**: 新脚本 `scripts/run_freshness_experiments.py`：同参数跑两组信号（baseline: max=0；fresh: max=1），各自用推荐风控（择时 + 无信号清仓，止损关）回测，产出 comparison.md。
- **Rationale**: 003 验收显示择时 + 无信号清仓是最优组合，新鲜度实验应站在这个基线上量化边际贡献；两次信号生成全历史各约 1~2 分钟，可接受。
- **报告要素**: 总收益 / 年化 / 最大回撤 / 夏普 / 月度胜率 / 年均换手 / 成本拖累 / 有信号月数，外加固定的样本内声明。

## R6 配置与 CLI

- **Decision**: `MainlineConfig` 新增 `max_industry_streak: int = 0` 并加 `__post_init__` 校验非负；`run_mainline_signals.py` 新增 `--max-industry-streak`。
- **Rationale**: 与 003 的 BacktestConfig 校验风格一致；默认 0 保证所有既有调用零改动。

## R7 数据窥探声明

- **Decision**: 001 的 `LIMITATIONS_NOTE` 增加一条：新鲜度规则源于同一段样本的事后分析（样本内），正式结论以对照回测为准；comparison.md 也固定携带该声明。
- **Rationale**: 规则直接来自对回测样本的挖掘，必须显式提示过拟合风险。
