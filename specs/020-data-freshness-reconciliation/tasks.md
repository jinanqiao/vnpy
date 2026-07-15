# 任务清单：数据新鲜度与复权价差修复

**输入**：`/specs/020-data-freshness-reconciliation/` 下的规格和计划文档

**前置文档**：spec.md、plan.md、research.md、data-model.md、contracts/

**测试说明**：本 feature 直接影响 live gate，因此测试任务必须先做。

## Phase 1：准备工作

**目的**：确认当前阻断状态和文件边界。

- [x] T001 记录当前 live gate 阻断基线到 `data/quality/data_gate_live_20260714.json`
- [x] T002 [P] 盘点核心数据集日期范围：交易日历、未复权日线、后复权日线、执行股票池
- [x] T003 [P] 盘点 QMT/AKShare 后复权价差当前基线：覆盖率、diff_rows、diff_ratio
- [x] T004 确认本 feature 不改 `vnpy/alpha/research/qmt_gateway_data.py` 凭证默认值

---

## Phase 2：基础设施

**目的**：先定义可复用诊断边界，再进入刷新和修复。

- [x] T005 [P] 为核心数据共同交易日设计测试：`tests/alpha/research/test_data_freshness_reconciliation.py`
- [x] T006 [P] 为 QMT/AKShare 价差诊断设计测试：`tests/alpha/research/test_price_reconciliation.py`
- [x] T007 [P] 为 quarantine 在刷新失败时不进入 silver/gold 设计测试：`tests/alpha/research/test_data_freshness_reconciliation.py`
- [x] T008 设计 `vnpy/alpha/research/data_foundation/freshness.py` 的核心数据新鲜度模型和检查函数
- [x] T009 设计 `vnpy/alpha/research/data_foundation/price_reconciliation.py` 的价差诊断模型和检查函数
- [x] T010 扩展 `scripts/validate_alpha.py` 编译目标，纳入新增脚本和模块

---

## Phase 3：用户故事 1 - 同一交易日数据截面（P1）

**目标**：交易日历、未复权日线、后复权日线、执行股票池统一到共同最新交易日。

**独立验证**：live gate 不再出现 `trading_calendar_coverage`，且共同日期一致。

### 测试任务

- [x] T011 [P] [US1] 测试交易日历未覆盖 as_of 时必须阻断
- [x] T012 [P] [US1] 测试核心数据集日期不同步时必须阻断并指出落后数据集
- [x] T013 [P] [US1] 测试刷新失败证据进入 `data/quarantine/`

### 实现任务

- [x] T014 [US1] 实现 `freshness.py`：读取核心数据日期、行数、最新日股票数
- [x] T015 [US1] 实现共同交易日计算，不允许 AKShare 晚日期抬高 live-ready 日期
- [x] T016 [US1] 实现 `scripts/refresh_live_data_foundation.py` 的 CLI 骨架和报告输出
- [x] T017 [US1] 接入现有 QMT/AKShare 下载脚本或分阶段调用，刷新后重建 `silver/gold`
- [x] T018 [US1] 刷新 manifest、SQLite 元数据和 query catalog
- [x] T019 [US1] 更新 `docs/data_foundation.md` 的刷新命令和验收口径

---

## Phase 4：用户故事 2 - QMT/AKShare 后复权价差闭环（P2）

**目标**：识别价差来源，不能解释的价差阻断 live-ready。

**独立验证**：运行诊断 CLI 可输出覆盖率、异常比例、top symbols 和最大异常样本。

### 测试任务

- [x] T020 [P] [US2] 测试价差覆盖率低于阈值时失败
- [x] T021 [P] [US2] 测试价差超过阈值时失败并输出异常样本
- [x] T022 [P] [US2] 测试无价差时通过并生成 JSON/Markdown 报告

### 实现任务

- [x] T023 [US2] 实现 `price_reconciliation.py` 的近期窗口裁剪和 key 覆盖率计算
- [x] T024 [US2] 实现价差样本输出、top symbols、最大异常样本
- [x] T025 [US2] 实现疑似原因分桶：单位差异、复权基准差异、公司行动日、缺失数据、unknown
- [x] T026 [US2] 实现 `scripts/diagnose_price_reconciliation.py`
- [x] T027 [US2] 将诊断结果接入 `data_gate.py`，保留 live 阻断语义
- [x] T028 [US2] 更新 `data/dictionary/data_dictionary.md` 和 `docs/data_foundation.md`

---

## Phase 5：用户故事 3 - 修复结果可追溯（P3）

**目标**：数据刷新、价差诊断和 gate 结果都可追溯到 manifest 版本。

### 测试任务

- [x] T029 [P] [US3] 测试刷新后不可变 manifest 版本副本存在
- [x] T030 [P] [US3] 测试质量报告记录 common_trade_date 和价差诊断路径

### 实现任务

- [x] T031 [US3] 在刷新报告中写入 manifest version、common_trade_date、阻断项
- [x] T032 [US3] 在 SQLite 质量结果表中记录 freshness 与 price reconciliation 检查结果
- [x] T033 [US3] 在 quickstart 中补充完整验证命令和预期输出

---

## Phase 6：验收与收尾

- [x] T034 运行 `python3 -m pytest tests/alpha/research/test_data_freshness_reconciliation.py tests/alpha/research/test_price_reconciliation.py tests/alpha/research/test_data_foundation.py -q`
- [x] T035 运行 `python3 -m compileall -q vnpy/alpha scripts/refresh_live_data_foundation.py scripts/diagnose_price_reconciliation.py`
- [x] T036 运行真实数据 live gate，并把结果写入 `data/quality/data_gate_live_20260714.json`
- [x] T037 复核 `specs/020-data-freshness-reconciliation/checklists/requirements.md`
- [x] T038 在本规格中记录最终验收结果和剩余风险

## 依赖与顺序

- Phase 1 → Phase 2 → US1/US2/US3 → Phase 6。
- US1 是 MVP，先修共同交易日；US2 可并行开发诊断，但 live-ready 必须等 US1+US2 都通过。
- US3 依赖 US1/US2 的输出报告。

## 实施策略

1. 先让 freshness 检查能清楚指出“哪个核心数据落后”。
2. 再刷新交易日历、未复权、后复权和执行股票池。
3. 再做价差诊断，不靠调阈值糊过去。
4. 最后才允许 `--data-gate-mode live` 进入候选信号流程。
