# Research: 主线策略风控层

**Date**: 2026-07-09　Phase 0 输出。所有决策基于 002 已交付的回测引擎现状与本会话的归因分析。

## R1: 择时信号的计算口径

**Decision**: 沪深300 收盘价的 60 日简单均线（窗口可配 `timing_ma_window`）。每个调仓执行日，取**执行日前一交易日**的基准收盘与均线比较：收盘 < 均线 → 该期"弱市"。窗口不足 60 个交易日的早期时段视为"通过"（不空仓），记入数据质量日志。

**Rationale**: 与归因分析口径一致（60 日线上方 +36% / 下方 -31%）；用前一日收盘保证执行日开盘时信息已完整可得，零未来函数。简单均线而非 EMA：可手算、可解释，适合第一版风控。

**Alternatives considered**: 20 日线（太敏感，月度调仓粒度下反复打脸）；双均线金叉死叉（引入第二个参数，过度设计）；成交量加权（数据依赖更重，收益不明确）。

## R2: 风控卖出的溯源标注——复用 reason 列而不加新列

**Decision**: 不给 trades 表加新列。风控触发的卖出复用现有 `reason` 字段标注来源，枚举扩展为：
`suspended / limit_up / limit_down / delisted / insufficient_cash / timing_exit / no_signal_exit / stop_loss`。
普通调仓卖出 reason 保持 null。

**Rationale**: FR-008/SC-001 要求全开关关闭时数值产物与 002 **逐字节一致**——加列会改变 parquet 字节流，哪怕值全为 null。复用 reason 列时，开关全关 → 不产生任何风控卖出 → reason 取值分布与 002 完全相同 → 字节一致自然成立。语义上也自洽：reason 本来就是"这笔交易为什么长这样"。

**Alternatives considered**: 新增 `trigger` 列（破坏字节一致，需要把 002 基线也重跑，回归保护失去锚点）；写进 data_quality.json 而不进 trades（审计时要跨文件对账，违背"每笔交易可追溯"）。

## R3: 择时清仓与月度调仓的动作合并

**Decision**: 择时判定发生在调仓执行日的交易动作之前。弱市时：照常执行"卖出全部旧持仓"（002 的全卖全买语义中的"全卖"），但跳过"全买"；卖出交易的 reason 标注 `timing_exit`；该期在数据质量日志记录 `timing_skip`。该期入选个股**不产生 abandoned 记录**（该期根本没有买入计划）。恢复期（基准回到均线上方的调仓日）按当期信号正常建仓。

**Rationale**: 复用既有的卖出路径（滑点/成本/停牌顺延全部继承），风控层只是把"买"这一半关掉，改动面最小。abandoned 语义保持"想买而不能买"，不被择时污染。

## R4: 无信号月的识别与清仓时点

**Decision**: 用交易日历重算全部"真实月末"（复用 001 的月末定义：日历中每个自然月的最后一个交易日，排除日历末尾不完整月），与 selection 的信号月对比得到"无信号月末"集合。范围限定在 [第一个信号月, 行情最后日期]。开关打开时，无信号月末的次一交易日清仓全部持仓（reason=`no_signal_exit`），连续无信号月只在第一个月清仓（其后本来就是空仓）。若该日同时是择时弱市判定日——不可能，无信号月末不是调仓执行日，两者天然错开；但清仓交易若因择时清仓已发生则跳过（持仓为空自动无动作）。

**Rationale**: "连续无信号月只清一次"无需特殊逻辑——第二个月持仓已空。清仓日=次一交易日与调仓执行日的口径一致（信号月末收盘后才知道"这个月没信号"）。

## R5: 个股止损的触发与执行

**Decision**: 每个交易日估值后检查每笔持仓：`当日后复权收盘 / 买入日后复权执行价 - 1 < -stop_loss_rate`（买入执行价=开盘×(1+滑点)，即实际成交口径的后复权演化）→ 加入待卖队列，次一交易日按 002 卖出规则执行（开盘价、滑点、成本、停牌/一字跌停顺延），reason=`stop_loss`。触发后不拉黑：同一股票下期再入选照常买入。`stop_loss_rate <= 0` 在配置构建时直接报错。调仓执行日与止损卖出撞车时调仓卖出优先（先处理调仓，止损队列里已不在持仓中的条目自动作废）。

**Rationale**: 收盘触发、次日执行是日线数据下唯一无未来函数的止损实现（盘中触价需要分钟数据）。基准价用实际成交口径，和账户真实亏损一致。

**Alternatives considered**: 移动止损/最高价回落（参数多一个、路径依赖更强，spec 已声明留待后续）；当日收盘价成交（用了当日收盘信息决策当日成交，未来函数）。

## R6: 对照实验 runner

**Decision**: 新增薄脚本 `scripts/run_risk_experiments.py`：串行跑四组固定组合（baseline / timing / timing+no_signal / all_on），每组产物写各自 run 目录，最后汇总一张对照表写 `outputs/mainline_backtest/<run_id>_risk_comparison/comparison.md`（含每组开关状态 + 总收益/年化/最大回撤/夏普/换手/成本拖累 + 相邻组的边际差异）。逐组调用 `run_mainline_backtest()`，不搞并行（全历史单次 3 秒，四组 ≤ 15 秒）。

**Rationale**: 四组共享同一 selection 与行情，串行足够快且日志清晰。汇总表独立成文件，SC-006 的"5 分钟看懂每条规则值多少"由它承担。

## R7: 配置扩展与回归保护

**Decision**: `BacktestConfig` 新增 5 个字段（默认值即 002 现状行为）：
`timing_enabled=False`、`timing_ma_window=60`、`no_signal_exit_enabled=False`、`stop_loss_enabled=False`、`stop_loss_rate=0.15`。
逐字节一致的范围 = nav/positions/trades 三个 parquet（SC-001 口径）；config.json 因新增字段必然变化，不在字节一致范围内（002 契约中 config.json 本就含时间戳，从未参与字节比对）。回归测试：合成数据下全开关关闭 vs 002 代码路径的三个 parquet 字节比对（用既有端到端 fixture 跑一次留档对照即可，实现上直接断言"关闭时不触发任何风控分支"+ 复用既有可复现性测试）。

## R8: 代码落位与可读性

**Decision**: 新增 `vnpy/alpha/research/mainline_backtest/risk.py` 集中三条规则的纯函数（均线序列预计算、择时判定、无信号月末计算、止损扫描），`pipeline.py` 的主循环只插入 3~4 个调用点。`pipeline.py` 当前 295 行、逼近 300 上限：把 `_build_periods()` 迁至 `data_loader.py`（本就是数据整形逻辑），腾出空间。阅读指南新增 risk.py 条目（第 8 个文件）。

**Rationale**: 规则与编排分离，风控逻辑可单测；pipeline 主循环保持"从上到下一条线"的可读性。
