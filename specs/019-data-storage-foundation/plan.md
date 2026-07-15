# 实施计划：数据存储底座改造

**分支**：`019-data-storage-foundation` | **日期**：2026-07-14 | **规格**：[spec.md](./spec.md)

**输入**：来自 `/specs/019-data-storage-foundation/spec.md` 的功能规格

**说明**：本文件是 SDD 规划产物。本轮只写规格和计划，不创建实现代码。

## 摘要

将现有本地 `data/` 目录升级为可支撑实盘前数据检查的量化数据底座。目标架构是：大规模行情和研究数据继续保存在 parquet 数据湖中；DuckDB 或同类嵌入式分析查询层负责 SQL 风格查询；SQLite 先负责数据版本、质量检查、数据源覆盖和运行元数据；当出现多进程写入、远程看板、多人协作或服务化部署时，再评估升级到 PostgreSQL。

QMT 继续作为交易和执行相关数据的主源；AKShare 作为研究补充源和交叉校验源。第一版 MVP 聚焦 data gate、manifest、query catalog、元数据边界和现有文件迁移映射，不要求一次性补齐所有 PIT 数据。

## 技术上下文

**语言/版本**：Python 3.x，延续现有仓库脚本和 `vnpy/alpha` 模块风格

**主要依赖**：现有 Polars/parquet 工作流；规划引入 DuckDB 风格嵌入式查询层；规划使用 SQLite 作为第一阶段元数据存储

**存储**：

- Parquet：raw/bronze/silver/gold 大数据表
- DuckDB 或同类查询层：直接查询 parquet 和元数据视图
- SQLite：数据版本、质量检查、数据源覆盖、查询目录、运行元数据
- PostgreSQL：后续多人/多进程/服务化时的升级路径

**测试**：后续实现沿用 pytest；本 SDD 只定义验证场景、合同和任务

**目标平台**：本地 macOS 开发环境，配合 Windows/远程 QMT 网关，当前本地空间预算 100G

**项目类型**：现有 Python 量化研究/实盘系统中的数据基础设施改造

**性能目标**：

- 数据门禁适合每日盘前/盘后执行
- 常见按股票/日期范围查询不复制全量 parquet
- 元数据和质量结果查询具备交互式响应体验

**约束**：

- 不修改 QMT 硬编码凭证处理
- 不删除或重写现有数据文件
- 不在本 SDD 轮次写实现代码
- 不把大规模行情数据整体搬入传统行式数据库
- 实盘模式必须 fail closed，也就是检查不过就阻断

**规模/范围**：当前本地数据约 2.4G，包含约 600 万行全 A 日线；本地可用空间预算约 100G

## 宪法检查

*门禁：Phase 0 research 前必须通过；Phase 1 design 后重新检查。*

项目 constitution 当前仍是占位模板，没有可执行约束。本计划采用以下仓库内约束和用户约束：

- QMT 凭证硬编码处理明确不在本 feature 范围内。
- 本轮 SDD 不创建实现代码。
- 现有 raw/normalized/akshare/universe 等数据文件不能被删除或重写。
- 设计必须支持研究复现和实盘前阻断。
- 大数据继续 parquet 化，小而关键的状态进入元数据库。

门禁状态：通过。

## 项目结构

### 本 feature 文档

```text
specs/019-data-storage-foundation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── data-readiness-contract.md
│   ├── manifest-contract.md
│   └── query-catalog-contract.md
└── tasks.md
```

### 规划中的数据和代码区域

```text
data/
├── raw/
│   ├── qmt/
│   └── akshare/
├── bronze/
├── silver/
├── gold/
├── manifest/
├── quality/
└── dictionary/

state/
├── quant_data.duckdb
├── quant_meta.sqlite
└── live_trading.sqlite

vnpy/alpha/research/
├── data_check.py
├── qmt_gateway_data.py
├── pit_universe.py
└── data_foundation/        # 后续实现时再创建

scripts/
├── build_quant_data_completeness.py
├── build_point_in_time_universe.py
├── download_adjusted_bars.py
├── download_akshare_daily.py
├── download_akshare_financial.py
└── check_data_gate.py      # 后续实现时再创建
```

**结构决策**：大数据仍放 `data/`，本地数据库文件放 `state/`。`vnpy/alpha/research/data_foundation/` 只作为后续实现区域规划，本 SDD 不创建代码目录。现有 `data/normalized`、`data/universe`、`data/sector`、`data/benchmark`、`data/akshare` 等文件都是迁移输入，不是可随意删除的临时产物。

## 数据分层准入/准出规则

| 层级 | 准入 | 准出 |
|---|---|---|
| raw | QMT/AKShare 原始拉取结果，带来源、时间、参数 | 不修改、不覆盖，可被 bronze 引用 |
| bronze | raw 数据经过字段名、类型、日期格式标准化 | schema 检查通过，可进入 silver |
| silver | 清洗、去重、复权、PIT 对齐或标注近似状态 | 质量检查通过，可生成 gold 或 manifest |
| gold | 策略直接读取的数据，如 execution universe、策略输入宽表 | data gate 对目标模式通过，才允许被信号/回测引用 |

## MVP 切分

### MVP 必做

- 数据门禁合同和阈值。
- 数据版本 manifest 合同。
- 查询目录合同。
- SQLite 元数据边界。
- 现有数据文件迁移映射。
- P0/P1/P2 PIT 目标表优先级。

### MVP 不做

- 真实订单/成交/持仓/资金账本。
- ClickHouse 等服务端数据库。
- 所有 PIT 数据一次性补齐。
- QMT 凭证安全改造。

## 复杂度跟踪

| 复杂点 | 为什么需要 | 为什么不用更简单方案 |
|---|---|---|
| Parquet + 查询层 + 元数据库的混合架构 | 大规模行情和小规模审计状态访问模式完全不同 | 全塞 SQLite/PostgreSQL 会复制大数据并降低列式扫描效率 |
| 显式 PIT 目标表 | 实盘级回测必须知道数据在当时是否可见 | 继续用当前快照倒推历史会保留明显未来函数风险 |
| 多运行模式 data gate | 研究、回测、模拟和实盘容错程度不同 | 单一 pass/fail 无法表达“研究可用但实盘不可用” |

## Phase 0：研究结论

见 [research.md](./research.md)。

## Phase 1：设计与合同

见 [data-model.md](./data-model.md)、[quickstart.md](./quickstart.md) 和 [contracts/](./contracts/)。

## 设计后宪法检查

门禁状态：通过。

- 设计保留现有数据并采用增量迁移。
- 设计没有修改 QMT 凭证处理。
- 设计区分 parquet 大数据和 SQLite 元数据。
- 设计包含实盘前数据阻断和数据版本追溯。
