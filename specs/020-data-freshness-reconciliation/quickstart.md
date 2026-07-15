# Quickstart：验证数据新鲜度与复权价差修复

## 1. 查看当前 live gate

```bash
python3 scripts/check_data_gate.py --data-root data --mode live --as-of 2026-07-14 --json
```

当前预期仍会失败，阻断项为交易日历覆盖不足和 QMT/AKShare 后复权价差。

## 2. 刷新核心数据

```bash
python3 scripts/refresh_live_data_foundation.py \
  --data-root data \
  --as-of 2026-07-14 \
  --mode live
```

验收点：

- `silver/trading_calendar.parquet` 覆盖 `2026-07-14`。
- `silver/daily_bars_raw_price.parquet`、`silver/daily_bars_adjusted.parquet`、`gold/execution_universe.parquet` 的共同日期一致。
- `data/manifest/versions/` 生成新的不可变版本文件。

## 3. 诊断复权价差

```bash
python3 scripts/diagnose_price_reconciliation.py \
  --data-root data \
  --recent-days 252 \
  --price-diff-max 0.02 \
  --coverage-min 0.99
```

验收点：

- 报告输出覆盖率、异常比例、最大异常、top symbols。
- 若失败，报告说明疑似原因，不得让 live gate 通过。

## 4. 再跑 live gate

```bash
python3 scripts/check_data_gate.py --data-root data --mode live --as-of 2026-07-14 --json
```

验收点：

- 无 `trading_calendar_coverage`。
- 无未解释的 `qmt_akshare_price_diff`。
- `status=pass` 后，才允许进入 paper/live 候选流程。

## 5. 验证信号入口阻断

```bash
python3 scripts/run_mainline_signals.py --data-gate-mode live --end 2026-07-14
```

验收点：

- gate 失败时不产生新信号产物。
- gate 通过时产物 `config.json` 记录 data version。
