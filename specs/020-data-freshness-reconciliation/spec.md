# 功能规格：数据新鲜度与复权价差修复

**Feature Branch**: `020-data-freshness-reconciliation`

**Created**: 2026-07-14

**Status**: Draft

**Input**: 用户要求先修两件事：交易日历、未复权日线、执行股票池统一更新到同一交易日；解决 QMT/AKShare 后复权价差，确认复权口径、单位、除权处理是否一致。

## Clarifications

- 本规格不改动 QMT 凭证文件，也不处理凭证硬编码问题。
- 本规格目标是把本地数据从“能阻断实盘”推进到“具备进入 paper/live 前置检查的候选状态”；不包含真实下单、订单账本和资金持仓账本。
- 以 `data/silver/*` 和 `data/gold/*` 为策略读取入口，旧目录只作为源或回退，不作为最终验收入口。

## 用户场景与测试

### User Story 1 - 同一交易日数据截面（Priority: P1）

作为准备实盘的使用者，我需要交易日历、未复权日线、后复权日线和执行股票池都停在同一个可信交易日，这样信号生成不会混用不同日期的数据。

**为什么是 P1**：当前 live gate 的阻断项之一是交易日历只覆盖到 `2026-07-02`，而后复权与 AKShare 数据已经到 `2026-07-08/2026-07-09`。不同步的数据截面会直接污染实盘信号。

**独立测试**：更新数据后运行 `python3 scripts/check_data_gate.py --data-root data --mode live --as-of <评估日期>`，不再出现 `trading_calendar_coverage`，且 manifest 的 `latest_trade_date` 等于所有核心数据集共同可用的最新交易日。

**验收场景**：

1. **Given** 交易日历、未复权日线、后复权日线、执行股票池已更新，**When** 运行 live gate，**Then** `trading_calendar_coverage`、`latest_trade_date_freshness`、`execution_universe_latest_date` 均通过。
2. **Given** 任一核心数据集落后于共同交易日，**When** 运行 live gate，**Then** gate 失败，并指出落后的数据集和日期。
3. **Given** 更新过程中部分 QMT 拉取失败，**When** 构建分层数据，**Then** 失败证据进入 `data/quarantine/`，不得进入 `silver/gold`。

---

### User Story 2 - QMT/AKShare 后复权价差闭环（Priority: P2）

作为策略研究者，我需要知道 QMT 后复权价与 AKShare 后复权价差来自口径、单位、除权处理还是真实异常，并在未解释前阻断实盘。

**为什么是 P2**：当前 live gate 报告 `qmt_akshare_price_diff` 失败，近期 `70177` 行超过 2% 差异，占比 `5.4079%`。如果复权价口径不一致，回测收益和实盘信号可能不可比。

**独立测试**：生成一份复权价差诊断报告，按原因分桶展示样本、比例和代表股票，并让 data gate 只在差异被解释或低于阈值后通过。

**验收场景**：

1. **Given** QMT 与 AKShare 后复权价差超过阈值，**When** 运行价差诊断，**Then** 报告必须输出覆盖率、差异比例、最大差异样本、按股票/日期聚合的异常分布。
2. **Given** 差异来自单位或复权口径不一致，**When** 应用统一口径后的对照数据，**Then** `qmt_akshare_price_diff` 通过或报告剩余不可解释样本。
3. **Given** 差异来自个别股票异常，**When** 生成质量报告，**Then** 这些股票不得静默进入 live-ready 数据版本。

---

### User Story 3 - 修复结果可追溯（Priority: P3）

作为后续排查者，我需要知道这次更新使用了哪些源、覆盖到哪天、哪些异常被隔离、哪些口径被确认。

**为什么是 P3**：数据修复不是一次性动作，实盘前需要留痕，避免“今天能跑、明天不知道为什么变了”。

**独立测试**：查看 `data/manifest/versions/<version_id>.json`、`data/quality/` 报告和 `state/quant_meta.sqlite`，能追踪本次数据版本、质量结果和价差诊断结果。

**验收场景**：

1. **Given** 数据刷新完成，**When** 生成 manifest，**Then** 不可变版本副本存在且记录核心数据集日期范围、hash 和行数。
2. **Given** data gate 通过，**When** 生成报告，**Then** 报告明确写出用于实盘候选的共同交易日。

## 边界情况

- QMT 网关不可用或超时：必须失败并隔离证据，不得沿用半更新数据覆盖 silver/gold。
- AKShare 当日数据晚于 QMT：不得用更晚的 AKShare 日期抬高共同交易日。
- 后复权算法口径不同：必须在报告中标注口径差异，不得简单调高阈值掩盖。
- 某些股票停牌、退市或缺失：必须在覆盖率中体现，并区分合理缺失与异常缺失。
- 交易日历存在非交易日或缺交易日：live gate 必须阻断。

## 功能需求

- **FR-001**: 系统必须能刷新并校验交易日历，使其覆盖评估日期。
- **FR-002**: 系统必须能刷新未复权日线、后复权日线和执行股票池，并计算共同最新交易日。
- **FR-003**: 系统必须在核心数据集不同步时阻断 live gate，并输出落后数据集和日期。
- **FR-004**: 系统必须对 QMT 后复权价与 AKShare 后复权价做近期窗口覆盖率和价差校验。
- **FR-005**: 系统必须生成复权价差诊断报告，包含异常样本、异常比例、代表股票和可能原因分类。
- **FR-006**: 系统必须把失败拉取或失败校验证据写入 `data/quarantine/`。
- **FR-007**: 系统必须刷新 manifest、SQLite 元数据和 query catalog，并保留不可变 manifest 版本副本。
- **FR-008**: 系统必须让 `scripts/run_mainline_signals.py --data-gate-mode live` 在数据不合格时失败退出。

## 关键实体

- **核心交易截面**：交易日历、未复权日线、后复权日线、执行股票池共同支持的最新交易日。
- **复权价差样本**：某个 `datetime + vt_symbol` 上 QMT 后复权收盘价与 AKShare 后复权价的比较结果。
- **价差诊断报告**：记录覆盖率、差异分布、最大异常和原因分桶的人读报告。
- **隔离记录**：失败源、数据集、失败原因、证据文件和隔离目录。
- **live-ready 数据版本**：通过 live gate 且 manifest 固化的数据版本。

## 成功标准

- **SC-001**: 修复后 live gate 不再出现 `trading_calendar_coverage` 阻断。
- **SC-002**: 核心数据集共同最新交易日一致，manifest `latest_trade_date` 等于该共同日期。
- **SC-003**: QMT/AKShare 近期窗口 key 覆盖率不低于 `99%`，或低于阈值时必须阻断。
- **SC-004**: QMT/AKShare 后复权价差超过 `2%` 的样本比例为 `0`，或所有剩余异常均在报告中解释并被隔离/降级。
- **SC-005**: `--data-gate-mode live` 在任一阻断项存在时不产生新的实盘候选信号。
- **SC-006**: 数据刷新和诊断流程有可重复命令，且输出 JSON/Markdown 报告。

## 假设

- QMT 是实盘行情、交易日历和执行股票池主源；AKShare 是复权价、流通股本和基本面校验源。
- 当前 100G 本地空间足够支持增量刷新、诊断报告和隔离证据。
- 后复权主口径优先采用 QMT `back_ratio`，AKShare 作为交叉校验；若 AKShare 口径不可对齐，应报告而不是强行覆盖 QMT。
- 本规格不新建传统行式行情数据库，继续沿用 Parquet 数据湖 + SQLite 元数据。

## 验收记录

**执行时间**：2026-07-14

**已落地能力**：

- 新增核心数据新鲜度检查和报告：`data/quality/freshness_report.md`。
- 新增 QMT/AKShare 后复权价差诊断和报告：`data/quality/price_reconciliation.md`。
- 新增 live 数据刷新编排脚本：`scripts/refresh_live_data_foundation.py`。
- 新增通用 QMT 全 A 日线下载脚本：`scripts/download_qmt_daily_bars_full.py`。
- data gate 已复用价差诊断结果，并保留 live 阻断语义。
- freshness 与 price reconciliation 结果已写入 `state/quant_meta.sqlite:quality_check_results`。

**真实数据验收结果**：

- `freshness.status=pass`，共同交易日为 `2026-07-14`。
- `data_gate.status=pass`，live 模式阻断项 `0`、警告项 `0`。
- 交易日历、未复权日线、后复权日线、执行股票池均覆盖 `2026-07-14`。
- QMT 标准化行情清理后保留 `5194` 只、`14751901` 行；最新交易日可用股票数 `5190`。
- 执行股票池最新交易日为 `2026-07-14`，最新日可执行股票数 `4606`。
- `price_reconciliation.status=pass`，最近 252 个交易日覆盖率 `0.996473`，按股票尺度归一化后超阈值行数 `0`。
- 绝对后复权价格仍有 `69177` 行差异，占比 `0.053463`，主要来自供应商后复权基准/单位尺度不同；该差异已保留在报告中，不作为 live 阻断。
- 已隔离 QMT 非正 OHLC 股票：`000004.SZ`、`002808.SZ`、`300029.SZ`、`600193.SH`、`600608.SH`、`605081.SH`。
- 已隔离 QMT/AKShare 复权归一化异常股票：`000656.SZ`、`000691.SZ`、`000793.SZ`、`002742.SZ`、`300159.SZ`、`301039.SZ`、`600165.SH`。

**剩余风险**：

- 本次 QMT 健康检查返回 `connected=False`、`dry_run=True`，说明数据网关可读但交易连接状态仍需在真正下单前单独确认。
- 13 只隔离股票暂不进入当前实盘候选链路，需要后续定位 QMT 返回 0 价和复权路径不一致的根因。
- PIT 表仍包含近似口径：历史 ST、停复牌、涨跌停和公司行动明细后续应替换为官方逐日数据。
- AKShare 下载过程中 `689009.SH` 曾卡住，当前覆盖率达标，但后续应补一个按缺失 symbol 精准重试的下载入口。
