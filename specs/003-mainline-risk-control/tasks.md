# Tasks: 主线策略风控层

**Input**: Design documents from `/specs/003-mainline-risk-control/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: 包含测试任务（延续 001/002 约定：TDD 先测后码，纳入 `scripts/validate_alpha.py` 门禁）。

**Organization**: 按 user story 分组。US1（择时）/US2（无信号清仓）/US3（止损）共享 Foundational 的配置与规则模块骨架；US4（对照实验）依赖前三者完成。

**全局约束**：polars + 标准库；单文件 ≤ 300 行；风控溯源复用 trades.reason 列（不加列，research R2）；全开关关闭时 nav/positions/trades 与 002 逐字节一致；每个函数中文 docstring。

## Format: `[ID] [P?] [Story?] Description`

---

## Phase 1: Setup

**Purpose**: 为增量腾空间，锁定回归基线

- [X] T001 把 `_build_periods()` 从 `vnpy/alpha/research/mainline_backtest/pipeline.py` 迁移到 `data_loader.py`（更名 `build_periods`，本就是数据整形逻辑），pipeline 改为 import 调用；跑既有三个回测测试文件确认无回归（research R8 腾行数）
- [X] T002 [P] 扩展 `tests/alpha/research/backtest_fixtures.py`：`make_benchmark()` 支持传入可控走势构造"跌破/站上 60 日线"场景（前 N 天高位、之后跌破、再回升的三段式走势工具函数）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 配置字段与规则模块骨架——三条规则共同依赖

**⚠️ CRITICAL**: 本阶段完成前不得开始任何 user story

- [X] T003 扩展 `vnpy/alpha/research/mainline_backtest/config.py`：新增 5 个风控字段（data-model §1，默认全关），`__post_init__` 校验 stop_loss_rate > 0 且 timing_ma_window ≥ 2（frozen dataclass 用 object.__setattr__ 规避不了就在 load/CLI 侧校验，保持无继承约束）
- [X] T004 创建 `vnpy/alpha/research/mainline_backtest/risk.py` 骨架：模块级中文说明（这个文件回答"什么时候该减仓"）+ 四个纯函数签名：`build_benchmark_ma()`（均线预计算）、`is_weak_market()`（择时判定，含窗口不足视为通过）、`find_no_signal_month_ends()`（无信号月末集合）、`scan_stop_loss()`（止损扫描）
- [X] T005 [P] 创建 `tests/alpha/research/test_backtest_risk.py` 骨架 + 数据加载层规则单测：均线预计算手算对照、窗口不足视为通过并记日志、无信号月末计算（有信号月/无信号月/日历末尾不完整月）——先写测试确认失败，随 T004 实现转绿

**Checkpoint**: 规则函数就绪且单测通过，US1/US2/US3 可开始

---

## Phase 3: User Story 1 - 大盘择时开关 (Priority: P1) 🎯 MVP

**Goal**: 弱市调仓日清仓空仓，恢复日正常建仓；开关关闭时行为与 002 逐字节一致

**Independent Test**: 合成数据构造"基准跌破均线"时段，验证空仓、净值平直、恢复建仓；关闭开关字节级回归

### Tests for User Story 1

- [X] T006 [US1] 编写择时测试于 `tests/alpha/research/test_backtest_risk.py`：弱市执行日跳过全部买入且清仓（卖出 reason=timing_exit）、该期入选股不产生 abandoned 记录、空仓期净值平直、恢复日正常等权买入、择时判定只用执行日前一日数据（把执行日当天基准拉高不改变判定）、timing_skip 记入数据质量日志——先写测试确认失败

### Implementation for User Story 1

- [X] T007 [US1] 实现 `risk.py` 的 `build_benchmark_ma()` 与 `is_weak_market()`（research R1：前一交易日收盘 vs 均线，窗口不足返回"通过"+日志标记）
- [X] T008 [US1] 在 `vnpy/alpha/research/mainline_backtest/pipeline.py` 调仓分支插入择时调用点（research R3）：弱市 → 照常全卖（reason 标 timing_exit）+ 跳过全买 + quality_logs 记 timing_skip；`sell_position()` 增加 reason 透传参数（`execution.py` 小改，默认 None 不影响 002 路径）
- [X] T009 [US1] 运行 T006 全部转绿；补回归保护断言：开关全关时合成数据端到端产物与关闭前字节一致、trades 中新增三种 reason 出现 0 次（不变式 10）

**Checkpoint**: MVP 完成——预期贡献最大的规则可独立交付

---

## Phase 4: User Story 2 - 无信号月清仓 (Priority: P1)

**Goal**: 信号层空月时清仓持币，连续空月只清一次

**Independent Test**: 合成数据"有信号月 → 无信号月 → 有信号月"序列验证清仓与恢复

### Tests for User Story 2

- [X] T010 [P] [US2] 编写无信号清仓测试于 `tests/alpha/research/test_backtest_risk.py`：无信号月末次一交易日清仓（reason=no_signal_exit）、连续无信号月只有一次清仓交易、开关关闭时保持旧持仓（002 现状）、清仓日停牌股顺延（沿用 pending_sells）——先写测试确认失败

### Implementation for User Story 2

- [X] T011 [US2] 实现 `risk.py` 的 `find_no_signal_month_ends()`（research R4：日历真实月末 − 信号月末，限定范围）；在 `pipeline.py` 主循环加"无信号清仓日"分支（清仓动作复用择时清仓路径，reason 换为 no_signal_exit）
- [X] T012 [US2] 运行 T010 全部转绿；确认与 US1 组合场景（择时空仓期间遇到无信号月末 → 持仓已空自动无动作）

---

## Phase 5: User Story 3 - 个股月中止损 (Priority: P2)

**Goal**: 收盘回撤超阈值次日卖出，复用卖出受阻顺延机制

**Independent Test**: 合成数据单边下跌股验证触发与执行，未触发股不受影响

### Tests for User Story 3

- [X] T013 [P] [US3] 编写止损测试于 `tests/alpha/research/test_backtest_risk.py`：跌破 15% 次日开盘卖出（reason=stop_loss、data_quality 有 trigger 事件且触发日早于执行日）、恰好 15% 不触发（严格大于）、触发日停牌次日顺延、止损后同股下期再入选照常买入、调仓日撞车时调仓卖出优先不重复、stop_loss_rate ≤ 0 配置报错——先写测试确认失败

### Implementation for User Story 3

- [X] T014 [US3] 实现 `risk.py` 的 `scan_stop_loss()`（research R5：收盘 vs 买入执行价的后复权演化）；`pipeline.py` 估值后扫描持仓 → 触发者进 pending_sells（携带 reason=stop_loss）+ quality_logs 记 stop_loss_trigger；pending_sells 值扩展为 (planned_date, reason) 结构（既有补卖路径同步适配）
- [X] T015 [US3] 运行 T013 全部转绿；跑全部既有回测测试确认无回归

---

## Phase 6: User Story 4 - 风控对照实验 (Priority: P1，依赖 US1~US3)

**Goal**: 一键四组对照，量化每条规则的边际贡献

**Independent Test**: 四组真实回测完成，comparison.md 呈现对照表与边际贡献

- [X] T016 [US4] 扩展 `scripts/run_mainline_backtest.py`：5 个新参数（contracts/cli-contract.md §1），非法值报错退出码 1
- [X] T017 [US4] 实现 `scripts/run_risk_experiments.py`（contracts/cli-contract.md §2）：串行四组固定组合 → 各组产物 + comparison.md（对照表 + 三段边际贡献 + 产物路径）
- [X] T018 [P] [US4] 编写对照实验测试于 `tests/alpha/research/test_backtest_risk.py`：合成数据跑四组 → comparison.md 生成、每组 config 快照开关状态正确、baseline 组与 002 现状一致（不变式 10 端到端版）
- [X] T019 [US4] 真实数据验收（quickstart 场景 1~5）：回归保护 SHA-256 比对 002 基线、仅择时组回撤收窄核对、四组对照 ≤ 5 分钟、止损追溯抽查、可复现性两次运行比对，结果记入验收记录

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T020 [P] `vnpy/alpha/research/mainline_backtest/report.py` 增加"风控动作汇总"段（择时跳过期数/清仓次数/止损笔数，未启用时显示"未启用"）；`__init__.py` 阅读指南补 risk.py 条目
- [X] T021 [P] 将 `risk.py` 与 `scripts/run_risk_experiments.py` 追加进 `scripts/validate_alpha.py` 的 PY_COMPILE_TARGETS（测试目录已被 PYTEST_TARGETS 覆盖）
- [X] T022 可读性自查：全部改动文件 ≤ 300 行（重点 pipeline.py）、无继承/装饰器/生成器、公开函数中文 docstring；超标处拆分
- [X] T023 汇总验收记录到 `specs/003-mainline-risk-control/acceptance-run.md`（T019 全部场景证据 + 四组对照表）

---

## Dependencies & Execution Order

- **Phase 1 → Phase 2 → user stories**：T001 腾行数是 pipeline 改动的前置；T003/T004 是三条规则的公共骨架
- **US1（Phase 3）→ US2（Phase 4）**：清仓路径（reason 透传）由 US1 建立，US2 复用
- **US3（Phase 5）可与 US2 并行**（改动点不同：US2 在调仓分支、US3 在估值后扫描），但都依赖 US1 的 reason 透传
- **US4（Phase 6）依赖 US1~US3 全部完成**
- **Phase 7 依赖全部**

### Parallel Opportunities

- T002 与 T001 并行；T005 与 T003/T004 并行
- T010 与 T013 的测试编写可并行
- T018 与 T016/T017 完成后即可写；T020/T021 互不冲突可并行

---

## Implementation Strategy

**MVP = Phase 1~3**（择时开关，预期贡献最大的单条规则）。随后 US2/US3 按序补齐，US4 出最终对照答案。每个 Checkpoint 跑全部回测测试（含 002 既有三个文件）防回归；字节级回归保护（不变式 10）从 T009 起持续生效。
