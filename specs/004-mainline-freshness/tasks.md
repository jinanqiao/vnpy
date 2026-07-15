# Tasks: 主线新鲜度规则

**Input**: specs/004-mainline-freshness/（spec / plan / research / data-model / contracts / quickstart）
**Tests**: TDD——每个用户故事先写测试再实现

## Phase 1: Setup

- [x] T001 `mainline/config.py`: 新增 `max_industry_streak: int = 0` 字段（含注释）与 `__post_init__` 非负校验
- [x] T002 [P] 测试骨架 `tests/alpha/research/test_mainline_freshness.py`: 配置校验用例（负值报错、默认 0、config-json 覆盖）

## Phase 2: US1 主线月龄标注 (P1)

- [x] T003 测试：合成数据验证月龄计数——连任 1/2/3、断档重置、无主线月清空、非主线行业 streak 为 null（不变量 I-14/I-16）
- [x] T004 `mainline/industry.py`: SNAPSHOT_COLUMNS 新增 `industry_streak`；新增纯函数 `apply_freshness(snapshot, prev_streak, config)` → (标注后快照, 新 prev_streak, stale 行业列表)
- [x] T005 `mainline/pipeline.py`: 循环内维护 `prev_streak` 并调用 apply_freshness；selection 与行业快照 join 带上 industry_streak 列
- [x] T006 回归保护测试：max=0 时无 "stale"、selection 除新增列外与既有 pipeline 测试期望一致；既有 001 测试全绿

## Phase 3: US2 新鲜度过滤开关 (P1)

- [x] T007 测试：max=1 时连任第 2 月行业整期不入选（reject_reason="stale"、月龄保留）；月龄不受过滤影响（连任第 3 月仍 streak=3）；全 stale 期产出空清单；质量日志含 stale_industry_filtered
- [x] T008 实现过滤：apply_freshness 内 stale 标记 + selected=False；pipeline 记录质量日志事件；report.md 逐期表格标注被过滤行业
- [x] T009 `mainline/pipeline.py`: LIMITATIONS_NOTE 增加样本内声明
- [x] T010 `scripts/run_mainline_signals.py`: 新增 --max-industry-streak（负值退出码 1）；对应 CLI 测试

## Phase 4: US3 对照实验 (P2)

- [x] T011 测试：合成数据跑通对照脚本核心函数（两组信号 → 两组回测 → comparison.md 含指标对照 + 差异 + 样本内声明）
- [x] T012 新增 `scripts/run_freshness_experiments.py`（≤300 行；baseline max=0 / fresh max=1；回测用 timing+no_signal；汇总 comparison.md）

## Phase 5: Polish & 验收

- [x] T013 `scripts/validate_alpha.py`: PY_COMPILE_TARGETS 纳入新脚本与新测试文件；`mainline/__init__.py` 阅读指南补月龄说明
- [x] T014 全量测试 + 门禁：pytest tests/alpha/research -q 与 validate_alpha 全绿
- [x] T015 真实数据验收：quickstart 场景 1~5（回归对比、月龄抽查、fresh 运行、一键对照实验、可复现性），写 acceptance-run.md
