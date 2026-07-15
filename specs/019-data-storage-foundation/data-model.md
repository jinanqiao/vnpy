# 数据模型：数据存储底座改造

## 实体：DataSource（数据源）

表示 QMT、AKShare 或未来数据提供方。

**字段**：

- `source_id`：稳定标识，例如 `qmt`、`akshare`
- `display_name`：可读名称
- `role`：`primary`、`secondary`、`cross_check`、`experimental`
- `dataset_category`：该角色适用的数据类别
- `reliability_notes`：数据源限制和已知风险

**校验规则**：

- 每个数据类别最多只能有一个主数据源。
- AKShare 来源的实盘执行数据不得标为主源，除非后续 spec 明确批准。

## 实体：DatasetSnapshot（数据集快照）

表示一次源数据拉取或一次转换产物。raw 层快照必须不可变。

**字段**：

- `snapshot_id`
- `source_id`
- `layer`：`raw`、`bronze`、`silver`、`gold`
- `dataset_name`
- `snapshot_at`
- `trade_date_min`
- `trade_date_max`
- `symbol_count`
- `row_count`
- `file_paths`
- `content_hashes`
- `extract_params_json`
- `upstream_snapshot_ids`
- `status`：`created`、`validated`、`quarantined`、`retired`
- `pit_status`：`pit_safe`、`snapshot_based`、`approximation`

**校验规则**：

- raw 快照不可修改、不可覆盖。
- 每个 silver/gold 快照必须引用上游快照。
- 被隔离的快照不能提升到 gold。
- 非 PIT 安全的数据必须在 `pit_status` 标记。

## 实体：DataVersion（数据版本）

表示一个不可变 manifest，即一组可复现的数据状态。

**字段**：

- `version_id`
- `created_at`
- `latest_trade_date`
- `mode_status_json`：按研究、回测、模拟、实盘记录就绪状态
- `manifest_path`
- `manifest_sha256`
- `dataset_snapshot_ids`
- `quality_run_ids`
- `notes`

**校验规则**：

- 数据版本固化后不可修改。
- 只要存在实盘阻断级质量失败，该数据版本就不能标记为 live-ready。
- 信号或回测运行必须且只能引用一个数据版本。

## 实体：QualityCheckResult（质量检查结果）

表示一条校验规则在某个数据集、某个运行模式下的结果。

**字段**：

- `check_id`
- `version_id`
- `dataset_name`
- `check_name`
- `mode`：`research`、`backtest`、`paper`、`live`
- `severity`：`info`、`warning`、`blocking`
- `status`：`pass`、`fail`、`not_applicable`
- `observed_value`
- `expected_value`
- `details_json`
- `created_at`

**校验规则**：

- blocking 失败会阻断对应模式。
- 每个失败检查都必须在 `details_json` 中提供可读原因。

## 实体：SourceCoverageResult（数据源覆盖结果）

表示跨数据源覆盖率和差异结果。

**字段**：

- `coverage_id`
- `version_id`
- `primary_source_id`
- `comparison_source_id`
- `dataset_name`
- `trade_date_min`
- `trade_date_max`
- `symbol_count_primary`
- `symbol_count_comparison`
- `matched_symbol_count`
- `matched_row_count`
- `mismatch_count`
- `tolerance_rule`
- `examples_json`

**校验规则**：

- 必须区分“对比源缺失”和“主源失败”。
- 差异示例必须包含足以复现的股票、日期、字段和值。

## 实体：QueryCatalogEntry（查询目录条目）

表示一个对研究者或校验流程可见的命名查询/视图。

**字段**：

- `entry_name`
- `description`
- `layer`
- `backing_type`：`parquet`、`metadata_table`、`view`
- `backing_location`
- `schema_version`
- `recommended_filters`
- `owner`

**校验规则**：

- gold 层查询条目只能指向已验证数据。
- 大表查询必须记录推荐过滤条件，例如日期范围和股票代码。

## 实体：RunMetadata（运行元数据）

表示回测、信号、模拟交易或实盘运行记录。

**字段**：

- `run_id`
- `run_type`：`backtest`、`signal`、`paper`、`live`
- `strategy_name`
- `data_version_id`
- `started_at`
- `finished_at`
- `status`
- `output_paths`
- `config_hash`
- `summary_json`

**校验规则**：

- `data_version_id` 必填。
- live run 不能引用非 live-ready 的数据版本。

## 实体：PitAvailabilityRecord（PIT 可用性记录）

表示会引发未来函数风险的数据在什么时候可见。

**字段**：

- `record_id`
- `dataset_name`
- `vt_symbol`
- `effective_date`
- `announce_date`
- `available_date`
- `source_id`
- `payload_ref`

**校验规则**：

- 策略只能使用 `available_date <= signal_date` 的记录。
- 如果只有当前快照数据，必须在数据集元数据中标记为近似或快照回填。

## 计划数据类别

| 数据类别 | 主数据源 | 交叉校验源 | 目标层 |
|---|---|---|---|
| 日线 OHLCV | QMT | AKShare | silver |
| 后复权日线 | QMT | AKShare | silver |
| 交易日历 | QMT | AKShare | silver |
| 可执行股票池 | QMT 派生 | 无 | gold |
| 流通股本和换手率 | AKShare | QMT 可用时校验 | silver |
| 财务报表 | AKShare | QMT 可用时校验 | silver |
| 公司行动和复权因子 | QMT/AKShare，后续确认主源 | 另一来源 | silver |
| 涨跌停和停复牌状态 | QMT 优先 | AKShare | silver/gold |
| 指数成分 PIT | QMT 若可取历史，否则补充源 | AKShare 可用部分 | silver |
| 行业成分 PIT | QMT 快照先行，PIT 源后补 | AKShare 可用部分 | silver |

## 现有文件迁移目标

| 现有路径 | 规划角色 |
|---|---|
| `data/normalized/daily_bars_all_a.parquet` | `data/silver/daily_bars_raw_price.parquet` 的种子 |
| `data/normalized/daily_bars_all_a_adjusted.parquet` | `data/silver/daily_bars_adjusted.parquet` 的种子 |
| `data/benchmark/index_daily.parquet` | `data/silver/benchmark_index_daily.parquet` 的种子 |
| `data/calendar/trading_dates.parquet` | `data/silver/trading_calendar.parquet` 的种子 |
| `data/universe/all_a_symbols.parquet` | `data/silver/instrument_master_snapshot.parquet` 的种子 |
| `data/universe/execution_universe.parquet` | `data/gold/execution_universe.parquet` 的种子 |
| `data/universe/research_universe.parquet` | `data/gold/research_universe.parquet` 的种子 |
| `data/sector/sector_members.parquet` | `data/silver/sector_members_snapshot.parquet` 的种子 |
| `data/sector/sw1_members.parquet` | `data/silver/sw1_members_snapshot.parquet` 的种子 |
| `data/akshare/daily_bars_outstanding.parquet` | `data/silver/outstanding_share_turnover.parquet` 的种子 |
| `data/akshare/financial_reports.parquet` | 通过公告日校验后作为 `data/silver/financial_reports_pit.parquet` 的种子 |
| `data/akshare/financial_indicators.parquet` | `data/silver/financial_indicators.parquet` 的种子 |
| `data/manifest/manifest.json` | `data/manifest/` 版本历史的种子 |
| `data/quality/*.json`、`data/quality/*.md` | 质量历史和报告的种子 |
