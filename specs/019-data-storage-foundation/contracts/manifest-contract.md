# 合同：数据版本 Manifest

## 目的

定义研究、回测、模拟交易和实盘信号运行可复现所需的 manifest 结构。

## Manifest 结构

```json
{
  "version_id": "YYYYMMDD-HHMMSS-label",
  "created_at": "ISO-8601",
  "latest_trade_date": "YYYY-MM-DD",
  "storage_layout_version": "1",
  "datasets": [
    {
      "name": "daily_bars_adjusted",
      "layer": "silver",
      "source_role": "primary",
      "source": "qmt",
      "path": "data/silver/daily_bars_adjusted.parquet",
      "sha256": "hex",
      "rows": 0,
      "symbol_count": 0,
      "date_min": "YYYY-MM-DD",
      "date_max": "YYYY-MM-DD",
      "schema_version": "1",
      "pit_status": "pit_safe | snapshot_based | approximation"
    }
  ],
  "quality": {
    "status_by_mode": {
      "research": "pass",
      "backtest": "pass",
      "paper": "warning",
      "live": "fail"
    },
    "quality_report_path": "data/quality/data_gate_YYYYMMDD.json"
  },
  "lineage": [
    {
      "output_dataset": "daily_bars_adjusted",
      "upstream_datasets": ["raw_qmt_adjusted_shards"],
      "transformation": "normalize-and-verify-adjusted-bars"
    }
  ],
  "notes": []
}
```

## 规则

- manifest 固化后不可变。
- manifest 必须包含 gold 层策略输入所依赖的全部数据集。
- 哈希必须基于文件内容计算。
- 每次信号、回测、模拟或实盘运行必须且只能引用一个 `version_id`。
- 如果某个数据集不是 PIT 安全数据，manifest 必须标注为 `snapshot_based` 或 `approximation`。
- 如果 manifest 中任一必需文件当前哈希与记录不一致，该数据版本不能用于新的实盘运行。

## MVP 必需数据集条目

- `daily_bars_raw_price`
- `daily_bars_adjusted`
- `benchmark_index_daily`
- `trading_calendar`
- `instrument_master_snapshot`
- `execution_universe`
- `research_universe`
- `sector_members_snapshot` 或 `sw1_members_snapshot`
- `data_quality_summary`

## 后续扩展条目

- `corporate_actions`
- `adjust_factors`
- `limit_status_daily`
- `suspension_daily`
- `st_status_daily`
- `index_members_pit`
- `industry_members_pit`
- `financial_reports_pit`
