# 合同：查询目录

## 目的

定义 SQL 风格访问 parquet 数据集和元数据记录时使用的命名查询/视图目录。

## 目录条目结构

```json
{
  "entry_name": "daily_bars_adjusted",
  "description": "用于收益计算的全 A 后复权日线",
  "layer": "silver",
  "backing_type": "parquet",
  "backing_location": "data/silver/daily_bars_adjusted.parquet",
  "schema_version": "1",
  "recommended_filters": ["datetime", "vt_symbol"],
  "owner": "data-foundation"
}
```

## MVP 必需条目

| 条目名 | 底层来源 | 用途 |
|---|---|---|
| `daily_bars_raw_price` | parquet | 原始价格日线，用于执行股数和原始行情检查 |
| `daily_bars_adjusted` | parquet | 后复权日线，用于收益计算 |
| `execution_universe` | parquet | 每日可执行股票池 |
| `research_universe` | parquet | 研究股票池 |
| `trading_calendar` | parquet | 交易日查询 |
| `data_versions` | 元数据表 | 数据版本血缘 |
| `quality_check_results` | 元数据表 | 数据就绪和质量历史 |
| `source_coverage_results` | 元数据表 | QMT/AKShare 对比结果 |
| `run_metadata` | 元数据表 | 策略运行追溯 |

## 查询语义

- 查询视图不得修改源 parquet 文件。
- gold 层条目只能指向通过验证的数据。
- 大表视图必须记录推荐过滤条件，避免误触发全量扫描。
- 元数据条目必须支持按 `version_id` 和 `run_id` 查询。
- 查询层不负责自动修复数据，只负责暴露可审计视图。
