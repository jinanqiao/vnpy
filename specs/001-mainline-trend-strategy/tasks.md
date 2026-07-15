# Tasks: 主线强势股选股信号（行业筛选 + 个股打分）

**Input**: Design documents from `/specs/001-mainline-trend-strategy/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: 包含测试任务（plan 的 Constitution Check 明确要求新模块随附 pytest 测试并纳入 `scripts/validate_alpha.py` 门禁）。

**Organization**: 按 user story 分组；US1（选行业）与 US2（选股）都是 P1，US1 先行（US2 的输入依赖主线行业结果，但通过合成数据可独立测试）。

**全局约束（写代码时每个任务都要遵守）**：polars + 标准库；单文件 ≤ 300 行；只用"函数 + MainlineConfig 一个 frozen dataclass"；不 import 任何既有研究模块（turtle_*/pit_universe/data_check/run_summary）；每个函数配中文 docstring 说明输入输出与金融含义；固定排序保证输出可复现。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

**Purpose**: 建立自包含子包骨架

- [X] T001 创建子包骨架：`vnpy/alpha/research/mainline/__init__.py`（暂为空导出）及空模块文件 `config.py`、`data_loader.py`、`industry.py`、`stocks.py`、`pipeline.py`，每个文件头部写一段中文模块说明（这个文件在流水线中负责什么）
- [X] T002 [P] 创建测试目录占位：`tests/alpha/research/test_mainline_industry.py`、`test_mainline_stock.py`、`test_mainline_pipeline.py`（空壳 + 模块说明）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 配置与数据加载——两个 user story 共同依赖

**⚠️ CRITICAL**: 本阶段完成前不得开始任何 user story

- [X] T003 实现 `MainlineConfig` frozen dataclass 于 `vnpy/alpha/research/mainline/config.py`：data-model.md 配置实体的全部字段与默认值（mom_window=60、confirm_window=20、breadth_window=60、breadth_min=0.60、industry_rank_gate=5、industry_top_n=3、ma_windows=(20,60,120)、ma_slope_lag=5、bias_max=0.25、min_listed_bars=252、nh_window=252、nh_min=0.80、vol_short=20、vol_long=60、vol_cap=3.0、score_weights=(0.40,0.35,0.25)、stocks_per_industry=(6,10)、universe="all_a"、数据路径），附 `from_json_file()` 与 `to_dict()` 两个纯函数（FR-013）
- [X] T004 实现数据加载于 `vnpy/alpha/research/mainline/data_loader.py`：读取 4 张 parquet（日线/行业成分/执行池/交易日历），列名与类型校验（对照 data-model.md 输入实体），GICS1 行业过滤与 `industry_map` 构建（多归属取字典序第一并记日志，R1/R2），派生"每自然月最后一个交易日"调仓日历（R5），除权异常侦测（单日收益 < -15% 记数据质量日志，R3），返回结构统一为 polars DataFrame + 数据质量记录 list
- [X] T005 [P] 构建测试用合成数据工具函数于 `tests/alpha/research/test_mainline_pipeline.py` 顶部（或独立的 `_mainline_fixtures.py`）：生成 3 个虚拟行业 × 每行业 8 只股票 × 400 交易日的可控日线/成分/执行池/日历小数据集，走势可参数化（指定哪个行业强势、哪只股票破位），供三个测试文件共用
- [X] T006 为 T004 编写数据加载测试于 `tests/alpha/research/test_mainline_pipeline.py`：字段校验报错路径、GICS1 过滤、调仓日历正确性（用合成日历验证月末日）、除权异常侦测触发

**Checkpoint**: 配置与数据层就绪，US1/US2 可开始

---

## Phase 3: User Story 1 - 选出当期主线行业 (Priority: P1) 🎯 MVP

**Goal**: 给定调仓日，产出全部行业的动量/广度/排名/入选标记（IndustrySnapshot）

**Independent Test**: 合成数据中指定"行业A 强且普涨、行业B 强但独涨、行业C 弱"，验证只有行业A 入选且 reject_reason 正确；真实数据最近一期人工核对热点（SC-005）

### Tests for User Story 1

- [X] T007 [P] [US1] 编写行业层测试于 `tests/alpha/research/test_mainline_industry.py`：行业净值等权复合正确性（手工算例对照）、IND_MOM_60/20 数值复算、IND_BREADTH_60 数值复算、双窗口门槛拒绝（AS-2）、广度拒绝且原因为"广度不足"（AS-3）、满足者不足 N 不硬凑（AS-4）、成员 < 5 的行业不参与排名（Edge Case）、0 个满足时输出空清单（Edge Case）——先写测试并确认失败

### Implementation for User Story 1

- [X] T008 [US1] 实现行业层于 `vnpy/alpha/research/mainline/industry.py`（FR-001~003）：`build_industry_nav()` 行业等权净值 → `calc_industry_momentum()` 双窗口动量与排名 → `calc_industry_breadth()` 60 日上涨广度 → `select_mainline_industries()` 门槛+广度筛选并生成含 reject_reason 的 IndustrySnapshot（列结构严格对照 data-model.md），全部为接收/返回 polars DataFrame 的纯函数
- [X] T009 [US1] 运行并通过 T007 全部测试；用真实数据对最近一个调仓日做一次人工冒烟（打印入选行业，核对市场常识，SC-005 的证据记入 PR 描述或注释）

**Checkpoint**: 行业层独立可用、可测

---

## Phase 4: User Story 2 - 主线行业内选出强势个股 (Priority: P1)

**Goal**: 给定主线行业与调仓日，产出含过滤明细与三因子得分的 StockSnapshot 和入选清单 SelectionList

**Independent Test**: 用合成数据直接指定主线行业（不依赖 US1 代码），验证每条过滤条款单独触发、得分可手工复算、行业内取前 N 规则正确

### Tests for User Story 2

- [X] T010 [P] [US2] 编写个股层测试于 `tests/alpha/research/test_mainline_stock.py`：MA_ALIGN 四种破坏形态逐一拒绝（FR-004）、乖离率 >25% 拒绝且原因正确（AS-1）、NH_252 < 0.80 拒绝（FR-008）、可交易性/上市天数拒绝（FR-006）、RS_60/NH_252/VOL_RATIO 原始值手工算例复算（FR-007~009）、VOL_RATIO 截断为 3 与"近 20 日收益 ≤ 0 置 0.5 分位"（R7 语义：先剔除再回填）、总分加权复算误差为 0（AS-2/SC-003）、等权对照配置生效（FR-010）、行业内不足 6 只不硬凑（AS-3）、因子缺失个股剔除（Edge Case）——先写测试并确认失败

### Implementation for User Story 2

- [X] T011 [US2] 实现个股过滤于 `vnpy/alpha/research/mainline/stocks.py` 前半：`apply_stock_filters()` 计算 filter_ma_align / filter_bias / filter_nh / filter_tradable / filter_history 五个布尔列与首个 reject_reason（枚举对照 contracts/artifacts-schema.md 不变式 2）（FR-004~006、FR-008 过滤部分）
- [X] T012 [US2] 实现个股打分于 `vnpy/alpha/research/mainline/stocks.py` 后半：`calc_stock_factors()` 三因子原始值 → `rank_and_score()` 行业池内百分位排名（VOL 按 R7 先剔除再回填）与加权总分 → `select_stocks()` 每行业取前 6~10 只生成 StockSnapshot 与 SelectionList（FR-007~011），固定排序键 (rebalance_date, industry, industry_rank)
- [X] T013 [US2] 运行并通过 T010 全部测试

**Checkpoint**: 两层漏斗的核心逻辑全部独立可用

---

## Phase 5: Integration — 流水线编排与 CLI（服务 US1+US2 的完整交付）

**Purpose**: 把两层串成一条命令，产出全部留档（SC-001/004/006）

- [X] T014 实现编排于 `vnpy/alpha/research/mainline/pipeline.py`：`run_mainline_signals(config)` 逐调仓日执行 数据加载 → 行业层 → 个股层，聚合三张结果表，写 `outputs/mainline/<run_id>/` 六件套产物（config.json / industry_signals.parquet / stock_signals.parquet / selection.parquet / data_quality.json / report.md，schema 与不变式严格对照 contracts/artifacts-schema.md，report.md 含固定局限性声明：快照行业成分 + 未复权价格），INFO 日志逐期进度（FR-012~014）
- [X] T015 更新 `vnpy/alpha/research/mainline/__init__.py` 导出 `MainlineConfig` 与 `run_mainline_signals`
- [X] T016 实现 CLI 于 `scripts/run_mainline_signals.py`：参数与行为严格对照 contracts/cli-contract.md（--start/--end/--data-dir/--output-dir/--name/--universe/--config-json/--equal-weights，退出码约定），argparse 直译、不加抽象
- [X] T017 编写端到端测试于 `tests/alpha/research/test_mainline_pipeline.py`：合成数据集跑 2 个调仓日 → 六件套产物齐备、artifacts-schema.md 六条不变式逐条断言（selection⊂stock_signals、reject_reason 枚举、行业数上限、每行业数量区间、score 复算、固定排序）、同输入跑两次 parquet 逐字节一致（SC-004）
- [X] T018 真实数据验收：跑 quickstart.md 场景 1（单期冒烟）与场景 5（全历史 ≤ 5 分钟），核对场景 2/3 的人工抽查项，结果记入 `specs/001-mainline-trend-strategy/` 下的运行记录或 PR 描述

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T019 [P] 将三个测试文件纳入 `scripts/validate_alpha.py` 质量门禁（仅追加路径，不改既有逻辑）
- [X] T020 [P] 可读性自查（对照 plan Constraints）：单文件 ≤ 300 行、无继承/装饰器/生成器、每个公开函数有中文 docstring、`pipeline.py` 从上到下可线性阅读；超标处拆分或简化
- [X] T021 执行 quickstart.md 场景 4（可复现性）与场景 6（等权对照 + 参数覆盖），确认契约成立
- [X] T022 [P] 在 `vnpy/alpha/research/mainline/__init__.py` 或包级 docstring 写一份"阅读指南"：5 个文件的阅读顺序、每个文件回答什么问题、与 spec FR 编号的对照表

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 → Phase 2**：骨架先行
- **Phase 2 BLOCKS US1/US2**：config 与 data_loader 是两个故事的共同输入
- **Phase 3（US1）与 Phase 4（US2）可并行**：US2 测试用合成数据直接指定主线行业，不依赖 US1 代码
- **Phase 5 依赖 Phase 3+4**：编排层消费两层的函数
- **Phase 6 依赖 Phase 5**

### Parallel Opportunities

- T002 与 T001 后半可并行；T005 与 T003/T004 可并行
- T007（US1 测试）与 T010（US2 测试）可并行编写
- Phase 3 与 Phase 4 整体可由两条线并行推进
- T019/T020/T022 互不冲突可并行

---

## Implementation Strategy

**MVP = Phase 1~3**（数据层 + 行业层）：此时已能回答"本月主线行业是谁"，可独立演示与人工评审（SC-005）。随后 Phase 4 补齐选股、Phase 5 一条命令交付全量产物、Phase 6 收尾。每个 Checkpoint 停下来跑一次对应测试再前进；每完成一个任务或逻辑组提交一次 commit。

## Notes

- 测试先行（T007/T010 明确要求先失败再实现），端到端不变式以 contracts/artifacts-schema.md 为唯一判据
- 所有真实数据验收不修改数据湖，产物只写 `outputs/mainline/`
