# 实施计划：数据新鲜度与复权价差修复

**Branch**: `020-data-freshness-reconciliation` | **Date**: 2026-07-14 | **Spec**: `specs/020-data-freshness-reconciliation/spec.md`

**Input**: `/specs/020-data-freshness-reconciliation/spec.md`

## 概要

本 feature 解决两个实盘前阻断问题：第一，交易日历、未复权日线、后复权日线、执行股票池必须统一到同一可信交易日；第二，QMT 与 AKShare 后复权价差必须诊断、解释并纳入 data gate。实现上继续沿用 Parquet 数据湖 + SQLite 元数据，新增刷新编排、价差诊断和更严格的 live gate 报告。

## 技术上下文

**Language/Version**: Python 3，遵循仓库现有运行环境。

**Primary Dependencies**: polars、标准库、现有 QMT 网关封装、现有 AKShare 下载脚本。

**Storage**: `data/raw`、`data/silver`、`data/gold`、`data/quality`、`data/quarantine`、`data/manifest`、`state/quant_meta.sqlite`。

**Testing**: pytest，重点在 `tests/alpha/research/`。

**Target Platform**: 本地 macOS/类 Unix 开发环境，后续可迁移到实盘主机。

**Project Type**: 本地量化数据底座 + CLI。

**Performance Goals**: 全 A 最近窗口诊断应在本地可接受时间内完成；大表扫描必须按日期窗口裁剪。

**Constraints**:

- 不改动 QMT 凭证硬编码文件。
- 新功能默认不自动下单。
- 数据处理只用 polars。
- 任何进入 live-ready 的数据必须通过 data gate。
- 失败数据不得进入 silver/gold。

**Scale/Scope**: 全 A 股票，日频行情，近期窗口默认 252 个交易日；历史全量可作为诊断扩展。

## Constitution Check

- 单文件 ≤ 300 行：计划通过小模块拆分满足。
- polars 数据处理：满足。
- 默认关闭：新增诊断/刷新 CLI 不改变既有研究默认流程；live 强制通过 `--data-gate-mode live` 显式启用。
- 逐字节可复现：诊断输出排序固定，聚合前显式排序。
- 新 py 文件需加入 `scripts/validate_alpha.py` 编译目标：实现阶段执行。

## 项目结构

### Documentation

```text
specs/020-data-freshness-reconciliation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── freshness-contract.md
│   └── price-reconciliation-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
vnpy/alpha/research/data_foundation/
├── data_gate.py              # 扩展共同交易日、源覆盖和价差阻断
├── freshness.py              # 规划新增：核心数据集新鲜度诊断
├── price_reconciliation.py   # 规划新增：QMT/AKShare 复权价差诊断
└── quarantine.py             # 复用失败隔离

scripts/
├── refresh_live_data_foundation.py       # 规划新增：刷新并重建数据底座
├── diagnose_price_reconciliation.py      # 规划新增：输出价差诊断报告
└── check_data_gate.py                    # 复用/扩展

tests/alpha/research/
├── test_data_freshness_reconciliation.py
├── test_price_reconciliation.py
└── test_data_foundation.py
```

**Structure Decision**: 本 feature 属于数据底座，不进入 `mainline` 或 `mainline_backtest` 策略内部；策略只通过 data gate 和分层文件消费结果。

## 复杂度跟踪

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| 无 | 无 | 无 |
