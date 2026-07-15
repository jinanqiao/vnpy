# 数据模型：数据新鲜度与复权价差修复

## CoreDatasetFreshness

描述核心数据集的新鲜度。

| 字段 | 类型 | 说明 |
|---|---|---|
| dataset_name | string | 数据集名，如 `daily_bars_raw_price` |
| path | string | 分层路径 |
| date_column | string | 日期字段 |
| date_min | date | 最早日期 |
| date_max | date | 最新日期 |
| rows | integer | 行数 |
| symbol_count_latest | integer | 最新日期股票数 |
| required_for_live | bool | 是否 live 必需 |
| status | string | pass/warning/fail |

## CommonTradeDate

描述 live-ready 的共同交易日。

| 字段 | 类型 | 说明 |
|---|---|---|
| as_of | date | 评估日期 |
| common_trade_date | date/null | 核心数据共同最新交易日 |
| blocking_datasets | list | 阻断数据集 |
| source_dates | object | 各核心数据集最新日期 |
| status | string | pass/fail |

## PriceReconciliationSample

描述 QMT/AKShare 复权价差样本。

| 字段 | 类型 | 说明 |
|---|---|---|
| datetime | date | 交易日 |
| vt_symbol | string | 股票代码 |
| qmt_close_adjusted | float | QMT 后复权收盘价 |
| akshare_close_hfq | float | AKShare 后复权收盘价 |
| relative_diff | float | 相对差异 |
| abs_diff | float | 绝对差异 |
| diff_status | string | pass/fail |
| suspected_reason | string | unit_mismatch / adjustment_base / corporate_action / missing_data / unknown |

## PriceReconciliationSummary

描述价差诊断汇总。

| 字段 | 类型 | 说明 |
|---|---|---|
| checked_rows | integer | 检查行数 |
| joined_rows | integer | 成功连接行数 |
| coverage | float | key 覆盖率 |
| diff_rows | integer | 超阈值行数 |
| diff_ratio | float | 超阈值比例 |
| max_relative_diff | float | 最大相对差异 |
| top_symbols | list | 异常最多股票 |
| date_range | object | 检查日期范围 |

## QuarantineRecord

复用 `data_foundation.quarantine`。

| 字段 | 类型 | 说明 |
|---|---|---|
| source | string | qmt/akshare |
| dataset | string | 数据集名 |
| reason | string | 失败原因 |
| quarantine_dir | string | 隔离目录 |
| metadata_path | string | quarantine.json |
| copied_files | list | 证据文件 |
