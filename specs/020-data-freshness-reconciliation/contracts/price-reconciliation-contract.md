# 合同：QMT/AKShare 后复权价差诊断

## CLI 合同

计划命令：

```bash
python3 scripts/diagnose_price_reconciliation.py \
  --data-root data \
  --recent-days 252 \
  --price-diff-max 0.02 \
  --coverage-min 0.99 \
  --output-json data/quality/price_reconciliation.json \
  --output-md data/quality/price_reconciliation.md
```

## 输入数据

| 来源 | 路径 | 字段 |
|---|---|---|
| QMT 后复权 | `silver/daily_bars_adjusted.parquet` | `datetime`, `vt_symbol`, `close` |
| AKShare 后复权 | `silver/outstanding_share_turnover.parquet` | `datetime`, `vt_symbol`, `close_hfq` |

## 输出 JSON

```json
{
  "status": "fail",
  "recent_days": 252,
  "coverage": 0.997706,
  "coverage_min": 0.99,
  "diff_rows": 70177,
  "diff_ratio": 0.054079,
  "price_diff_max": 0.02,
  "date_range": {
    "start": "2025-07-01",
    "end": "2026-07-09"
  },
  "top_symbols": [
    {
      "vt_symbol": "000001.SZ",
      "diff_rows": 10,
      "max_relative_diff": 0.123
    }
  ],
  "suspected_reasons": {
    "adjustment_base": 1000,
    "unit_mismatch": 0,
    "unknown": 69177
  }
}
```

## 通过规则

- key 覆盖率必须 `>= coverage_min`。
- 超过 `price_diff_max` 的样本必须为 0，或被明确降级并不得进入 live-ready。
- 诊断报告必须列出异常最多的股票和最大相对差异样本。

## 失败规则

- 任一输入表缺失：fail。
- 必需字段缺失：fail。
- 覆盖率低于阈值：fail。
- 价差超过阈值且未解释：fail。
