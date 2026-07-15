# 合同：核心数据新鲜度

## CLI 合同

计划命令：

```bash
python3 scripts/refresh_live_data_foundation.py \
  --data-root data \
  --as-of 2026-07-14 \
  --mode live \
  --output-json data/quality/live_refresh_20260714.json \
  --output-md data/quality/live_refresh_20260714.md
```

## 输入

| 参数 | 必需 | 说明 |
|---|---|---|
| `--data-root` | 否 | 默认 `data` |
| `--as-of` | 是 | 评估日期 |
| `--mode` | 否 | 默认 `live` |
| `--output-json` | 否 | JSON 报告路径 |
| `--output-md` | 否 | Markdown 报告路径 |

## 必需核心数据集

| 数据集 | 路径 | 日期字段 | live 要求 |
|---|---|---|---|
| 交易日历 | `silver/trading_calendar.parquet` | `trade_date` | 覆盖 `as_of` |
| 未复权日线 | `silver/daily_bars_raw_price.parquet` | `datetime` | 覆盖共同交易日 |
| 后复权日线 | `silver/daily_bars_adjusted.parquet` | `datetime` | 覆盖共同交易日 |
| 执行股票池 | `gold/execution_universe.parquet` | `datetime` | 覆盖共同交易日 |

## 输出 JSON

```json
{
  "status": "pass",
  "as_of": "2026-07-14",
  "common_trade_date": "2026-07-14",
  "datasets": [
    {
      "dataset_name": "daily_bars_raw_price",
      "date_max": "2026-07-14",
      "rows": 123,
      "symbol_count_latest": 5200,
      "status": "pass"
    }
  ],
  "blocking_checks": [],
  "quarantine_records": []
}
```

## 阻断规则

- 交易日历未覆盖 `as_of`：live 阻断。
- 任一核心数据集最新日期早于共同交易日：live 阻断。
- 更新失败但产生了临时文件：必须写入 quarantine，live 阻断。
- manifest 未刷新或版本副本缺失：live 阻断。
