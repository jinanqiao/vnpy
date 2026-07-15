---
description: "数据存储底座改造任务清单"
---

# 任务清单：数据存储底座改造

**输入**：`/specs/019-data-storage-foundation/` 下的设计文档

**前置文档**：plan.md、spec.md、research.md、data-model.md、contracts/

**测试说明**：本 feature 会影响实盘前数据可用性判断，因此包含测试任务。

**组织方式**：按用户故事分组，保证每个用户故事可以独立实现和验证。

## Phase 1：准备工作（共享基础）

**目的**：在实现前确认范围、迁移盘点和文件边界。

- [x] T001 和用户复核 `specs/019-data-storage-foundation/spec.md`，确认 QMT 凭证改造不属于本 scope
- [x] T002 [P] 盘点 `data/normalized`、`data/universe`、`data/sector`、`data/benchmark`、`data/akshare`、`data/manifest`、`data/quality` 下现有文件的迁移目标
- [x] T003 [P] 确认 `data/raw`、`data/bronze`、`data/silver`、`data/gold`、`data/manifest`、`data/quality`、`state` 的最终目录语义
- [x] T004 [P] 确认 QMT 和 AKShare 在各数据类别上的主源/校验源角色

---

## Phase 2：基础设施设计（阻断所有用户故事）

**目的**：定义所有用户故事共同依赖的 schema、合同和门禁规则。

**关键要求**：本阶段完成前，不应开始任何用户故事实现。

- [x] T005 定义日线、后复权日线、交易日历、证券主数据、可执行股票池、研究股票池的规范 schema
- [x] T006 定义数据版本、数据集快照、质量检查结果、数据源覆盖结果、查询目录条目、运行元数据的元数据 schema
- [x] T007 在 `specs/019-data-storage-foundation/contracts/data-readiness-contract.md` 中确认各运行模式的阻断/警告规则
- [x] T008 在 `specs/019-data-storage-foundation/contracts/data-readiness-contract.md` 中确认 freshness、coverage、价格差异、复权一致性的默认阈值
- [x] T009 在 `specs/019-data-storage-foundation/contracts/manifest-contract.md` 中确认 MVP 必需 manifest 数据集
- [x] T010 在 `specs/019-data-storage-foundation/contracts/query-catalog-contract.md` 中确认 MVP 查询目录条目
- [x] T011 在 `specs/019-data-storage-foundation/data-model.md` 中确认 P0/P1/P2 PIT 表优先级和字段边界

**检查点**：基础设计完成后，用户故事可以并行推进。

---

## Phase 3：用户故事 1 - 生成信号前确认数据可信（P1，MVP）

**目标**：定义并实现可区分研究、回测、模拟和实盘的数据就绪门禁。

**独立验证**：对现有 `data/` 目录运行未来的门禁流程，确认实盘模式会阻断过期或不合格数据。

### 用户故事 1 的测试任务

- [x] T012 [P] [US1] 为必需文件缺失场景设计合同测试：`tests/alpha/research/test_data_gate_contract.py`
- [x] T013 [P] [US1] 为最新交易日滞后场景设计集成测试：`tests/alpha/research/test_data_gate_integration.py`
- [x] T014 [P] [US1] 为后复权/未复权一致性场景设计集成测试：`tests/alpha/research/test_data_gate_integration.py`
- [x] T015 [P] [US1] 为 QMT/AKShare 覆盖率和价格差异场景设计集成测试：`tests/alpha/research/test_data_gate_integration.py`

### 用户故事 1 的实现任务

- [x] T016 [US1] 按 `data-readiness-contract.md` 设计 `scripts/check_data_gate.py` 的 CLI 行为
- [x] T017 [US1] 设计 `vnpy/alpha/research/data_foundation/data_gate.py` 中可复用的检查边界
- [x] T018 [US1] 设计 `data/quality/` 下的数据门禁报告落盘格式
- [x] T019 [US1] 设计实盘模式阻断传播规则，确保后续信号生成不能忽略 blocking 结果

**检查点**：US1 可以独立验证，且构成 MVP。

---

## Phase 4：用户故事 2 - 每次信号都能追溯到数据版本（P2）

**目标**：让未来每次信号、回测、模拟或实盘运行都能绑定一个不可变数据版本。

**独立验证**：创建或检查一个数据版本 manifest，并确认一次规划中的信号运行能引用它。

### 用户故事 2 的测试任务

- [x] T020 [P] [US2] 为 manifest 结构设计合同测试：`tests/alpha/research/test_data_manifest_contract.py`
- [x] T021 [P] [US2] 为运行元数据的数据版本引用设计测试：`tests/alpha/research/test_run_metadata_traceability.py`
- [x] T022 [P] [US2] 为 manifest 哈希不匹配场景设计失败测试：`tests/alpha/research/test_data_manifest_contract.py`

### 用户故事 2 的实现任务

- [x] T023 [US2] 设计 `vnpy/alpha/research/data_foundation/manifest.py` 的 manifest 构建行为
- [x] T024 [US2] 设计 `state/quant_meta.sqlite` 的元数据存储边界
- [x] T025 [US2] 设计数据版本固化规则，确保 manifest 创建后不可变
- [x] T026 [US2] 设计 `vnpy/alpha/research/mainline/pipeline.py` 和 `vnpy/alpha/research/mainline_backtest/pipeline.py` 的运行元数据接入点

**检查点**：US2 可以独立验证，并为回测/信号追溯提供基础。

---

## Phase 5：用户故事 3 - 不搬迁全量大数据也能像数据库一样查询（P3）

**目标**：通过查询目录暴露 parquet 和元数据，让研究者可用 SQL 风格访问数据。

**独立验证**：查询目录能暴露日线、可执行股票池、数据版本和质量结果。

### 用户故事 3 的测试任务

- [x] T027 [P] [US3] 为查询目录结构设计合同测试：`tests/alpha/research/test_query_catalog_contract.py`
- [x] T028 [P] [US3] 为 parquet-backed 查询设计冒烟测试：`tests/alpha/research/test_query_catalog_integration.py`
- [x] T029 [P] [US3] 为大表推荐过滤条件设计测试：`tests/alpha/research/test_query_catalog_contract.py`

### 用户故事 3 的实现任务

- [x] T030 [US3] 设计 `vnpy/alpha/research/data_foundation/query_catalog.py` 的查询目录注册行为
- [x] T031 [US3] 设计 `daily_bars_raw_price`、`daily_bars_adjusted`、`execution_universe`、`trading_calendar` 的 DuckDB 风格视图名和 schema
- [x] T032 [US3] 设计 `data_versions`、`quality_check_results`、`source_coverage_results`、`run_metadata` 的元数据查询视图
- [x] T033 [US3] 设计大 parquet 扫描保护规则和推荐过滤条件

**检查点**：US3 可以独立验证，且不要求把大数据导入行式数据库。

---

## Phase 6：用户故事 4 - 区分源数据、清洗数据和策略可用数据（P4）

**目标**：建立 raw/bronze/silver/gold 生命周期，并记录现有文件如何迁移。

**独立验证**：现有文件能映射到规划层级，gold 层策略输入有明确血缘。

### 用户故事 4 的测试任务

- [x] T034 [P] [US4] 为现有文件迁移映射设计测试：`tests/alpha/research/test_data_layout_migration.py`
- [x] T035 [P] [US4] 为数据血缘校验设计测试：`tests/alpha/research/test_dataset_lineage.py`
- [x] T036 [P] [US4] 为隔离数据不得进入 gold 层设计测试：`tests/alpha/research/test_dataset_lineage.py`

### 用户故事 4 的实现任务

- [x] T037 [US4] 设计现有 `data/` 文件到 raw/bronze/silver/gold 的增量迁移流程
- [x] T038 [US4] 设计失败源数据拉取的 quarantine 行为
- [x] T039 [US4] 设计公司行动、复权因子、涨跌停、停复牌、ST、指数成分、行业成分、财报可用日期的 PIT 表提升规则
- [x] T040 [US4] 设计 `data/dictionary/data_dictionary.md` 的数据字典更新方式

**检查点**：US4 可以独立验证，且现有数据不会被删除或覆盖。

---

## Phase 7：收尾与横切关注点

**目的**：补齐文档、容量预算和运行风险说明。

- [x] T041 [P] 在 `docs/` 中补充 100G 本地数据空间预算说明
- [x] T042 [P] 在 `docs/` 中补充 QMT/AKShare 数据源限制和降级策略
- [x] T043 复核 `specs/019-data-storage-foundation/quickstart.md` 中所有验证场景
- [x] T044 复核非 PIT 数据风险，并确认 manifest 中的 `pit_status` 标记规则
- [x] T045 复核 SQLite 升级 PostgreSQL 的触发条件
- [x] T046 在开始任何实现前做最终 SDD review

---

## 依赖与执行顺序

### 阶段依赖

- **Phase 1 准备工作**：无依赖，可以立即开始。
- **Phase 2 基础设施设计**：依赖 Phase 1，阻断所有用户故事。
- **Phase 3+ 用户故事**：依赖 Phase 2。
- **Phase 7 收尾**：依赖已选择用户故事完成。

### 用户故事依赖

- **US1（P1）**：Phase 2 后即可开始，是 MVP。
- **US2（P2）**：Phase 2 后即可开始，会引用 US1 的质量结果。
- **US3（P3）**：Phase 2 后即可开始，可与 US2 并行。
- **US4（P4）**：Phase 2 后即可开始，但会使用 US2 的 manifest 和血缘规则。

### 可并行机会

- T002、T003、T004 可并行。
- T012、T013、T014、T015 可并行。
- T020、T021、T022 可并行。
- T027、T028、T029 可并行。
- T034、T035、T036 可并行。
- US2 和 US3 在基础 schema 稳定后可并行推进。

## 并行示例：用户故事 1

```text
任务："为必需文件缺失场景设计合同测试 tests/alpha/research/test_data_gate_contract.py"
任务："为最新交易日滞后场景设计集成测试 tests/alpha/research/test_data_gate_integration.py"
任务："为后复权/未复权一致性场景设计集成测试 tests/alpha/research/test_data_gate_integration.py"
任务："为 QMT/AKShare 覆盖率和价格差异场景设计集成测试 tests/alpha/research/test_data_gate_integration.py"
```

## 实施策略

### MVP 优先：只做用户故事 1

1. 完成 Phase 1 准备工作。
2. 完成 Phase 2 基础 schema 和数据门禁合同。
3. 完成 Phase 3 数据就绪门禁。
4. 停下来，用现有 `data/` 状态验证 US1。

### 增量交付

1. 先交付数据就绪门禁。
2. 再交付 manifest 和数据版本追溯。
3. 再交付 parquet + 元数据查询目录。
4. 最后交付分层迁移和 PIT 目标表提升。

### 任务总数

46 个任务。

### 各用户故事任务数

- US1：8 个任务
- US2：7 个任务
- US3：7 个任务
- US4：7 个任务
