# Tasks: 主线强势股策略回测

**Input**: Design documents from `/specs/002-mainline-backtest/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: 包含测试任务（延续 001 约定：新模块随附 pytest 测试并纳入 `scripts/validate_alpha.py` 门禁，TDD 先测后码）。

**Organization**: 按 user story 分组。US4（数据升级）与 US1（回测引擎）都是 P1：引擎全程用合成数据开发测试，**不依赖真实下载完成**，两条线可并行；只有最后的真实数据验收需要 US4 先完成。

**全局约束（写代码时每个任务都要遵守）**：polars + 标准库；单文件 ≤ 300 行；只用"函数 + BacktestConfig 一个 frozen dataclass"；不 import `mainline/`、turtle_* 等策略模块（`qmt_gateway_data` 属数据湖基础设施，仅下载脚本可用）；每个函数配中文 docstring 说明输入输出与金融含义；固定排序保证输出可复现。

## Format: `[ID] [P?] [Story?] Description`

---

## Phase 1: Setup

**Purpose**: 建立自包含子包骨架

- [X] T001 创建子包骨架：`vnpy/alpha/research/mainline_backtest/__init__.py`（暂为空导出）及空模块文件 `config.py`、`data_loader.py`、`execution.py`、`portfolio.py`、`metrics.py`、`pipeline.py`，每个文件头部写中文模块说明（这个文件在回测中负责什么）
- [X] T002 [P] 创建测试骨架：`tests/alpha/research/test_backtest_execution.py`、`test_backtest_portfolio.py`、`test_backtest_pipeline.py`（空壳 + 模块说明），合成数据工具规划在 `tests/alpha/research/backtest_fixtures.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 配置与数据加载——所有 user story 共同依赖

**⚠️ CRITICAL**: 本阶段完成前不得开始任何 user story

- [X] T003 实现 `BacktestConfig` frozen dataclass 于 `vnpy/alpha/research/mainline_backtest/config.py`：data-model.md §1 全部字段与默认值（selection_path 必填、initial_capital=1_000_000、commission_rate=0.00025、stamp_tax_rate=0.0005、slippage_rate=0.001、lot_size=100、suspend_freeze_days=20、数据路径组），附 `to_dict()` 与 `load_config_from_json()`（未知字段报错，FR-013）
- [X] T004 实现数据加载于 `vnpy/alpha/research/mainline_backtest/data_loader.py`：读 selection.parquet（校验 001 契约五列）、后复权/未复权日线、基准指数（过滤 benchmark_symbol）、执行池、交易日历，列名类型校验，统一 datetime→Date；实现 `verify_adjusted_bars()` 三项一致性校验（覆盖率 ≥99%、成交额一致、复权比例阶梯性，research R8，供下载脚本与测试复用）；实现"信号日→次一交易日"执行日推算（research R2）
- [X] T005 [P] 构建合成数据工具于 `tests/alpha/research/backtest_fixtures.py`：可控生成 (a) 迷你 selection 清单（2~3 个调仓期 × 每期 3~5 只）、(b) 配套后复权+未复权双行情（可注入除权跳空、停牌缺行、一字板 high==low）、(c) 执行池与日历、(d) 基准指数走势、(e) `write_backtest_lake()` 按数据湖目录落盘，供三个测试文件共用
- [X] T006 为 T004 编写数据加载测试于 `tests/alpha/research/test_backtest_pipeline.py`：selection 缺列报错、后复权文件缺失时报错并提示先运行下载脚本、执行日推算正确（月末信号→次月首个交易日）、`verify_adjusted_bars()` 三项校验的通过与失败路径（注入成交额不一致/覆盖率不足的坏数据）

**Checkpoint**: 配置与数据层就绪，US4 与 US1 可并行开始

---

## Phase 3: User Story 4 - 数据湖升级：后复权行情 (Priority: P1) — 可与 Phase 4 并行

**Goal**: 从阿里云 QMT（IP `8.141.119.179` 已验证连通）下载全 A 后复权日线，校验后原子写入数据湖

**Independent Test**: 独立运行下载命令，产物覆盖率 ≥ 99%、抽样个股除权日附近复权价连续；网关断连时退出码 2 且不写最终文件

### Implementation for User Story 4

- [X] T007 [US4] 实现下载 CLI 于 `scripts/download_adjusted_bars.py`：契约严格对照 contracts/cli-contract.md §1——健康检查先行（失败退出码 2 + 排查提示）、按 `all_a_symbols.parquet` 分批下载 `adjust="back"`（每批 200 只写分片到 `data/raw/qmt/adjusted/`）、单只失败重试 1 次后记入 `data/quality/failed_symbols_adjusted.json`（网关冷启动首请求会超时，research R1）、`--resume` 跳过已有分片、全部完成后合并 → 调用 `verify_adjusted_bars()` → 通过才原子写 `--output-path`（失败退出码 3）、校验报告写 `data/quality/adjusted_bars_verification.md`
- [X] T008 [P] [US4] 为下载脚本编写离线测试于 `tests/alpha/research/test_backtest_pipeline.py`：分片合并逻辑、resume 跳过逻辑、校验失败不写最终文件（全部用本地合成数据，不连网关）
- [X] T009 [US4] 真实执行数据下载：运行 `python scripts/download_adjusted_bars.py --resume`（预计 30~60 分钟，建议后台运行），完成后核对校验报告与失败清单量级（失败 ≤ 1% 可接受），结果记入验收记录

**Checkpoint**: 数据湖具备后复权行情，真实数据验收解锁

---

## Phase 4: User Story 1 - 全历史回测跑出成绩单 (Priority: P1) 🎯 MVP — 可与 Phase 3 并行

**Goal**: 逐调仓期"目标等权持仓 → 次日开盘执行 → 逐日估值"，产出净值、指标、基准对比六件套

**Independent Test**: 合成数据（走势可手算）跑 2~3 个调仓期，净值逐日可手工复算、记账恒等式成立、产物契约逐条满足

### Tests for User Story 1

- [X] T010 [P] [US1] 编写交易执行测试于 `tests/alpha/research/test_backtest_execution.py`：等权目标金额分配（期初净值/入选数）、未复权价整手向下取整 + 零钱留现金、买卖滑点方向正确（买加卖减）、佣金/印花税各自计费口径、执行日停牌买入 abandoned + reason=suspended、一字涨停 abandoned + reason=limit_up、一字跌停卖出 deferred 顺延到下一可交易日 + defer_days 正确、退市个股 forced 强平——先写测试并确认失败
- [X] T011 [P] [US1] 编写记账估值测试于 `tests/alpha/research/test_backtest_portfolio.py`：份额演化模型手算对照（投入金额 × 后复权价比，research R3）、除权跳空日净值连续（注入除权数据验证不产生假亏损）、记账恒等式 nav×capital = cash + position_value、空仓期净值平直、停牌持仓估值冻结 is_frozen 标记、零成本影子净值 nav_gross ≥ nav——先写测试并确认失败

### Implementation for User Story 1

- [X] T012 [US1] 实现交易模拟于 `vnpy/alpha/research/mainline_backtest/execution.py`：`build_target_positions()` 等权目标金额 → `plan_orders()` 与现有持仓求差（全卖全买，月度轮动语义）→ `execute_orders()` 逐单处理成交/延迟/放弃/强平并计成本分项，产出 TradeRecord 表（schema 对照 data-model.md §3，FR-002~006）
- [X] T013 [US1] 实现账户记账于 `vnpy/alpha/research/mainline_backtest/portfolio.py`：持仓三元组（真实股数/投入金额/买入日后复权价）、`value_positions()` 逐日份额演化估值 + 停牌冻结、现金账户收支、`build_nav_series()` 含成本与零成本影子两条净值 + 基准归一对齐（FR-007、SC-002）
- [X] T014 [US1] 实现指标计算于 `vnpy/alpha/research/mainline_backtest/metrics.py`：research R6 六项指标（口径写死在 docstring）+ 分年度分段 + 基准同期指标（FR-008~010），输入 nav 序列输出 MetricsSummary 字典
- [X] T015 [US1] 实现编排于 `vnpy/alpha/research/mainline_backtest/pipeline.py`：`run_mainline_backtest(config)` 逐调仓期推进（执行日交易 → 持有到下一期）、聚合三张表、写六件套产物（含 report.md 固定局限性声明四条，contracts/artifacts-schema.md）、INFO 日志逐期打印成交/放弃/延迟/期末净值（FR-012）
- [X] T016 [US1] 更新 `vnpy/alpha/research/mainline_backtest/__init__.py` 导出 `BacktestConfig` 与 `run_mainline_backtest`；实现 CLI 于 `scripts/run_mainline_backtest.py`（契约对照 contracts/cli-contract.md §2，argparse 直译）
- [X] T017 [US1] 运行并通过 T010/T011 全部测试；编写端到端测试于 `tests/alpha/research/test_backtest_pipeline.py`：合成数据跑 2 个调仓期 → 六件套齐备、artifacts-schema.md 八条不变式逐条断言、同输入重跑 parquet 逐字节一致（SC-004）
- [X] T018 [US1] 真实数据验收（依赖 T009 完成）：用 001 全历史 selection 跑完整回测（quickstart 场景 1/2/5），核对净值复算、耗时 ≤ 5 分钟、report.md 含基准对比与分年度表，结果记入验收记录

**Checkpoint**: MVP 完成——能回答"策略历史上赚不赚钱"

---

## Phase 5: User Story 2 - 每笔交易可追溯 (Priority: P2)

**Goal**: 交易流水完整可审计，异常处理透明

**Independent Test**: 抽查任意交易能还原信号来源与成本分项；异常场景（停牌/一字板/退市）各有明确记录

- [X] T019 [P] [US2] 补充交易追溯测试于 `tests/alpha/research/test_backtest_execution.py`：TradeRecord 全字段完整性（filled 行价格股数成本齐全、abandoned 行金额字段全 null）、status/reason 枚举封闭性、signal_date < planned_date ≤ executed_date 无未来信息断言（不变式 2/3）
- [X] T020 [US2] 在 `vnpy/alpha/research/mainline_backtest/pipeline.py` 的 report.md 生成中加入"异常交易汇总"段：放弃买入/延迟卖出/强平的笔数与明细表、长期停牌（> suspend_freeze_days）单列清单（对照 data_quality.json 同步记录）
- [X] T021 [US2] 真实数据抽查（quickstart 场景 3）：从真实回测 trades.parquet 抽 10 笔核对追溯链，抽 abandoned/deferred 各 1 笔核对 reason，结果记入验收记录

**Checkpoint**: 回测结果可信度可独立审计

---

## Phase 6: User Story 3 - 参数对照实验 (Priority: P3)

**Goal**: 成本假设等参数可覆盖，对照实验可归因

**Independent Test**: 不同参数两次运行，config.json 反映差异、指标差异方向符合直觉

- [X] T022 [P] [US3] 编写参数对照测试于 `tests/alpha/research/test_backtest_pipeline.py`：`--zero-cost` 时 nav == nav_gross、config-json 覆盖 slippage_rate 生效且未知字段报错、成本越高年化越低的方向性断言（合成数据）
- [X] T023 [US3] 真实数据对照实验（quickstart 场景 6）：零成本 vs 默认成本 vs 3 倍滑点三组运行，对比 metrics.json 的 cost_drag，结果记入验收记录

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T024 [P] 将三个测试文件与新模块纳入 `scripts/validate_alpha.py` 质量门禁（PYTEST_TARGETS 已含 tests/alpha/research 目录则只需追加 PY_COMPILE_TARGETS：子包 6 个文件 + 2 个脚本）
- [X] T025 [P] 可读性自查（对照 plan Constraints）：单文件 ≤ 300 行、无继承/装饰器/生成器、每个公开函数中文 docstring、`pipeline.py` 线性可读；超标处拆分
- [X] T026 执行 quickstart 场景 4（可复现性 SHA-256 比对），确认契约成立
- [X] T027 [P] 在 `vnpy/alpha/research/mainline_backtest/__init__.py` 写"阅读指南"：6 个文件阅读顺序、每个文件回答什么问题、与 spec FR 编号对照表
- [X] T028 汇总验收记录到 `specs/002-mainline-backtest/acceptance-run.md`（T009/T018/T021/T023/T026 的证据）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 → Phase 2**：骨架先行
- **Phase 2 BLOCKS 所有 user story**：config 与 data_loader 是公共输入
- **Phase 3（US4）与 Phase 4（US1）可并行**：引擎全程用合成数据开发；建议 T009 真实下载在后台跑，同时推进 Phase 4
- **T018（真实验收）依赖 T009**：这是两条线唯一的交汇点
- **Phase 5（US2）依赖 Phase 4**：追溯测试建立在交易流水实现之上
- **Phase 6（US3）依赖 Phase 4**：对照实验需要引擎完整
- **Phase 7 依赖全部**

### Parallel Opportunities

- T002 与 T001 后半、T005 与 T003/T004 可并行
- **T009（真实下载 30~60 分钟）启动后立即转入 Phase 4 开发**，下载在后台进行
- T010 与 T011 可并行编写；T019/T022 可并行
- T024/T025/T027 互不冲突可并行

---

## Implementation Strategy

**MVP = Phase 1~4**（数据 + 引擎 + 真实全历史回测）：此时已能回答"策略历史上赚不赚钱、风险多大"。随后 Phase 5 补审计能力、Phase 6 补对照实验、Phase 7 收尾。关键并行点：T009 下载耗时长，应尽早启动并转入引擎开发。每个 Checkpoint 停下来跑对应测试再前进。

## Notes

- 测试先行（T010/T011 明确要求先失败再实现），端到端不变式以 contracts/artifacts-schema.md 八条为唯一判据
- 真实数据验收不修改既有数据湖文件；后复权数据只新增 `daily_bars_all_a_adjusted.parquet`
- QMT 网关 IP 若再变化：设 `QMT_GATEWAY_URL` 环境变量或找用户确认
