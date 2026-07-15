# 实盘数据库说明

本项目采用三层存储：

- `Parquet`：历史行情、复权行情、股票池、PIT 过渡表和因子缓存。
- `DuckDB`：本地研究查询 Parquet，不作为实盘状态库。
- `PostgreSQL + TimescaleDB`：实盘运行状态、信号、订单、成交、持仓、资金、风控事件、数据版本追溯。

## 安装方式

本仓库使用 Docker Compose 启动本地 TimescaleDB：

```bash
bash scripts/db_timescale.sh up
```

第一次运行会自动生成本地 `.env`，其中包含数据库密码。`.env` 已被 `.gitignore` 忽略，不进入 Git。

检查数据库：

```bash
bash scripts/db_timescale.sh check
```

进入 psql：

```bash
bash scripts/db_timescale.sh psql
```

停止服务：

```bash
bash scripts/db_timescale.sh down
```

## 数据库边界

### 继续留在 Parquet

- `data/silver/daily_bars_raw_price.parquet`
- `data/silver/daily_bars_adjusted.parquet`
- `data/gold/execution_universe.parquet`
- `data/silver/adjust_factors.parquet`
- 财务、行业、因子和回测大表

原因：这些是大规模历史数据，按日期和股票批量扫描，Parquet 更合适。

### 进入 PostgreSQL/TimescaleDB

- `ops.data_versions`：数据版本。
- `ops.dataset_snapshots`：每个版本的数据集快照。
- `ops.quality_check_results`：数据门禁和质量检查结果。
- `ops.run_metadata`：策略运行、信号生成、回测和实盘运行追溯。
- `live.signals`：实盘候选信号。
- `live.orders`：委托。
- `live.trades`：成交。
- `live.positions`：持仓快照。
- `live.account_snapshots`：账户资金快照。
- `risk.events`：风控事件和阻断记录。

## 增量数据流程

每日流程应固定为：

```text
QMT/AKShare 拉取
  -> 写 raw/normalized 临时区
  -> 数据校验
  -> 重建 silver/gold
  -> 刷新 manifest
  -> 写入 ops 数据版本和质量结果
  -> live data gate 通过
  -> 写 live.signals
  -> 下单、成交、持仓、资金写 live/risk 表
```

实盘要求：任何 live gate 阻断项存在时，不允许写入新的实盘候选信号。

## 默认连接信息

连接信息来自本地 `.env`：

```text
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=vnpy_quant
POSTGRES_USER=vnpy
POSTGRES_PASSWORD=<本地随机生成>
```
