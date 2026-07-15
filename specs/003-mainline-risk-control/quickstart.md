# Quickstart: 主线策略风控层 — 验证指南

**Date**: 2026-07-09　按本指南跑通即证明特性端到端可用（对应 spec Success Criteria）。

## 前置条件

- 002 已交付：`data/normalized/daily_bars_all_a_adjusted.parquet` 就绪，全历史回测可跑
- 001 信号产物：`outputs/mainline/20260708_180215_all_history_final/selection.parquet`

## 场景 1：回归保护（SC-001）

```bash
python scripts/run_mainline_backtest.py --selection <path> --name regression_check
```

预期：不带风控参数运行，nav/positions/trades 三个 parquet 与 002 的 repro 基线
（`outputs/mainline_backtest/20260708_200231_repro_a/`）SHA-256 一致；
trades 中 timing_exit/no_signal_exit/stop_loss 出现 0 次。

## 场景 2：仅择时（SC-002）

```bash
python scripts/run_mainline_backtest.py --selection <path> --timing --name timing_only
```

预期：最大回撤较基线（-52.8%）显著收窄；data_quality.json 含 timing_skip 事件；
择时空仓时段持仓明细为空、净值平直。

## 场景 3：四组对照一键跑（SC-003/006）

```bash
python scripts/run_risk_experiments.py --selection <path>
```

预期：总耗时 ≤ 5 分钟；comparison.md 呈现四组指标对照与三段边际贡献；
每组 config.json 开关状态与组名一致。

## 场景 4：止损追溯（SC-004）

从 all_on 组的 trades.parquet 抽全部 reason=stop_loss 的卖出：每笔在 data_quality.json
有对应 stop_loss_trigger 事件（同股票、触发日早于执行日）；抽 1 笔核对回撤幅度确实超过阈值。

## 场景 5：可复现性（SC-005）

同参数（--timing --no-signal-exit --stop-loss）跑两次，三个 parquet SHA-256 一致。

## 自动化回归

```bash
python -m pytest tests/alpha/research/test_backtest_risk.py \
                 tests/alpha/research/test_backtest_pipeline.py -v
python scripts/validate_alpha.py
```
