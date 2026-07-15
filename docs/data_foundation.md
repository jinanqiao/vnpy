# 数据存储底座改造说明

本文对应 `specs/019-data-storage-foundation`，说明当前本地量化数据如何分层、如何检查、如何追溯。

## 当前结论

本项目采用“Parquet 数据湖 + SQLite 本地元数据 + DuckDB 查询 + PostgreSQL/TimescaleDB 实盘状态库”的本地架构：

- 大规模行情、财务、股票池继续用 parquet。
- `state/quant_meta.sqlite` 保存数据版本、数据集快照、查询目录和后续运行元数据。
- `state/query_catalog.sql` 提供可给 DuckDB 执行的 parquet 视图定义。
- PostgreSQL/TimescaleDB 保存实盘信号、订单、成交、持仓、资金、风控事件和可追溯数据版本。
- 实盘前必须先跑 data gate，失败时不得生成实盘订单候选。

## 目录分层

```text
data/
  raw/        原始拉取，不覆盖
  bronze/     字段标准化后的数据，后续扩展使用
  silver/     清洗、去重、复权、PIT 标记后的数据
  gold/       策略可直接读取的数据
  manifest/   数据版本清单
  quality/    数据质量报告
  dictionary/ 数据字典

state/
  quant_meta.sqlite  元数据 SQLite
  query_catalog.sql  DuckDB 风格查询视图

infra/postgres/
  compose.yaml       TimescaleDB 本地服务
  init/              初始化扩展、schema 和实盘表
```

## Manifest 固化

`data/manifest/data_foundation_manifest.json` 是当前版本指针，会随刷新更新。每次构建还会同时写入不可覆盖版本副本：

```text
data/manifest/versions/<version_id>.json
```

版本副本一旦存在会拒绝覆盖，用来保证后续信号、回测、实盘运行能追溯到当时的数据快照。

## 失败数据隔离

失败源数据不进入 `raw/bronze/silver/gold`。统一隔离目录为：

```text
data/quarantine/<source>/<dataset>/<timestamp>/
```

每次隔离都会写 `quarantine.json`，记录数据源、数据集、失败原因和拷贝的证据文件。后续 QMT/AKShare 下载脚本接入时，应在拉取失败或校验失败时调用该机制。

## 常用命令

构建或刷新分层目录：

```bash
python3 scripts/build_data_foundation.py --data-root data
```

构建 PIT 过渡表，并刷新 manifest / metadata / query catalog：

```bash
python3 scripts/build_pit_tables.py --data-root data --refresh-manifest
```

刷新 live 数据底座并生成新鲜度/价差报告：

```bash
python3 scripts/refresh_live_data_foundation.py --data-root data --as-of 2026-07-14 --mode live
```

需要实际调用 QMT 下载时再显式加 `--run-downloads`：

```bash
python3 scripts/refresh_live_data_foundation.py --data-root data --as-of 2026-07-14 --mode live --run-downloads
```

单独诊断 QMT/AKShare 后复权价差：

```bash
python3 scripts/diagnose_price_reconciliation.py --data-root data --recent-days 252
```

运行实盘数据门禁：

```bash
python3 scripts/check_data_gate.py --data-root data --mode live --as-of 2026-07-14
```

信号生成前强制运行数据门禁：

```bash
python3 scripts/run_mainline_signals.py --data-gate-mode live --end 2026-07-14
```

启动 PostgreSQL/TimescaleDB：

```bash
bash scripts/db_timescale.sh up
```

检查 PostgreSQL/TimescaleDB：

```bash
bash scripts/db_timescale.sh check
```

同步当前数据版本和质量记录到 PostgreSQL/TimescaleDB：

```bash
python3 scripts/sync_metadata_to_timescale.py --data-root data --state-dir state --env-file .env
```

输出 JSON 和 Markdown 报告：

```bash
python3 scripts/check_data_gate.py \
  --data-root data \
  --mode live \
  --as-of 2026-07-14 \
  --output-json data/quality/data_gate_live_20260714.json \
  --output-md data/quality/data_gate_live_20260714.md
```

## 数据源职责

| 数据类别 | 主源 | 备注 |
|---|---|---|
| 日线 OHLCV | QMT | AKShare 做交叉校验 |
| 后复权日线 | QMT | AKShare 做交叉校验 |
| 交易日历 | QMT | 必须覆盖评估日期 |
| 实盘行情和执行状态 | QMT | AKShare 不作为实盘执行主源 |
| 财务报表 | AKShare | QMT 财务可用时再做校验 |
| 流通股本/换手率 | AKShare | 先作为研究补充 |

data gate 会使用 `silver/outstanding_share_turnover.parquet` 中的 AKShare `close_hfq` 与
`silver/daily_bars_adjusted.parquet` 中的 QMT 后复权收盘价做近期窗口交叉校验：

- `qmt_akshare_key_coverage`：最近窗口内 AKShare 对照价格覆盖率。
- `qmt_akshare_price_diff`：QMT/AKShare 后复权价格按股票尺度归一化后的相对差异是否超过阈值。

说明：不同供应商的后复权绝对价格可能使用不同锚点，绝对价格差异会继续写入报告用于排查；
live gate 阻断依据是归一化后的价格路径差异，避免把稳定倍数差异误判为行情错误。

价差诊断报告会写到：

- `data/quality/price_reconciliation.json`
- `data/quality/price_reconciliation.md`

核心数据新鲜度报告会写到：

- `data/quality/freshness_report.md`

## PIT 过渡表

当前已先落地一批可管理、可追溯的 PIT 过渡表：

| 表 | 路径 | 当前口径 | 实盘前要求 |
|---|---|---|---|
| 复权因子 | `data/silver/adjust_factors.parquet` | 由未复权和后复权收盘价相除推导 | 后续与 QMT/AKShare 官方复权因子交叉校验 |
| 公司行动候选 | `data/silver/corporate_actions_candidates.parquet` | 由复权因子跳变识别候选事件 | 后续替换/补充官方分红送转配股明细 |
| 涨跌停状态 | `data/silver/limit_status_daily.parquet` | 使用当前证券快照字段近似 | 后续接入逐日涨跌停价历史 |
| 停复牌状态 | `data/silver/suspension_daily.parquet` | 优先 `daily_status`，否则用成交量/成交额为 0 近似 | 后续接入官方停复牌历史 |
| ST 状态 | `data/silver/st_status_daily.parquet` | 优先 `daily_status`，否则用当前名称快照近似 | 后续接入历史 ST 变更 |

这里的关键点是：过渡表可以先让策略、回测、查询目录和 manifest 形成统一入口，但 `pit_status` 会保留“推导/近似/快照”的风险标记。实盘前不能把这些近似表当成已经完全 PIT 安全。

## 100G 空间预算

| 类别 | 预算 |
|---|---:|
| raw 原始分片 | 35G |
| silver 行情和复权数据 | 20G |
| PIT 股票池/行业/涨跌停/停复牌 | 10G |
| 财务和基本面 | 10G |
| gold 策略输入和因子缓存 | 15G |
| manifest/quality/state 及预留 | 10G |

## 当前 live-ready 状态

截至 `2026-07-14` 本次刷新验收：

- `data_gate.status=pass`，live 模式阻断项 `0`、警告项 `0`。
- 交易日历、未复权日线、后复权日线、执行股票池共同覆盖到 `2026-07-14`。
- QMT 标准化行情清理后保留 `5194` 只、`14751901` 行，最新交易日可用股票数 `5190`。
- 执行股票池最新交易日可执行股票数 `4606`。
- QMT/AKShare 最近 252 个交易日 key 覆盖率 `0.996473`。
- QMT/AKShare 后复权价格按股票尺度归一化后超阈值行数 `0`。

本次隔离了两类不适合进入实盘候选链路的股票：

- QMT 非正 OHLC：`000004.SZ`、`002808.SZ`、`300029.SZ`、`600193.SH`、`600608.SH`、`605081.SH`。
- QMT/AKShare 复权归一化异常：`000656.SZ`、`000691.SZ`、`000793.SZ`、`002742.SZ`、`300159.SZ`、`301039.SZ`、`600165.SH`。

隔离证据：

- `data/quality/qmt_non_positive_ohlc_20260714.json`
- `data/quality/qmt_akshare_reconciliation_exclusions_20260714.json`

剩余注意事项：本次 QMT 健康检查可读，但返回过 `connected=False`、`dry_run=True`；这不影响行情刷新验收，但真正下单前还必须单独确认 QMT 交易连接和非 dry-run 状态。
