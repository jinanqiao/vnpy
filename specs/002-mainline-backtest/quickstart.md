# Quickstart: 主线策略回测 — 验证指南

**Date**: 2026-07-08　按本指南跑通即证明特性端到端可用（对应 spec Success Criteria）。

## 前置条件

- 001 已产出信号：`outputs/mainline/<run_id>/selection.parquet`（全历史运行）
- 阿里云 QMT 实例运行中，网关 8710 端口可达（`curl http://<IP>:8710/health` 返回 200）

## 场景 0：数据湖升级（US4，一次性，约 30~60 分钟）

```bash
python scripts/download_adjusted_bars.py --resume
```

预期：退出码 0；生成 `data/normalized/daily_bars_all_a_adjusted.parquet` 与校验报告 `data/quality/adjusted_bars_verification.md`（覆盖率 ≥ 99%、成交额一致、复权比例阶梯合理）。网关不可达时退出码 2 并给出排查提示——此时联系用户协助。

## 场景 1：全历史回测冒烟（SC-001，约 1 分钟）

```bash
python scripts/run_mainline_backtest.py \
    --selection outputs/mainline/<run_id>/selection.parquet --name full_history
```

预期：退出码 0；`outputs/mainline_backtest/<run_id>_full_history/` 六件套齐备；日志逐期打印成交/放弃/延迟统计。

## 场景 2：净值复算抽查（SC-002）

从 `positions.parquet` 抽任意一天：`sum(market_value) + cash == nav × initial_capital`（误差 < 0.01%）。

## 场景 3：交易可追溯抽查（SC-003）

从 `trades.parquet` 抽 10 笔：每笔能看到 signal_date（对应 selection 里的调仓期）、执行日、执行价、成本三分项；抽 abandoned/deferred 各 1 笔核对 reason 与 defer_days。

## 场景 4：可复现性（SC-004）

同参数跑两次，对比 nav/positions/trades 三个 parquet 的 SHA-256 一致。

## 场景 5：报告可读性（SC-005/006）

打开 report.md：5 分钟内能回答"总收益多少、最大回撤多深、跑没跑赢沪深300、成本吃掉多少年化"。核对含成本与零成本两条净值同时呈现。

## 场景 6：参数对照（US3）

```bash
python scripts/run_mainline_backtest.py --selection <path> --zero-cost --name no_cost
echo '{"slippage_rate": 0.003}' > /tmp/bt.json
python scripts/run_mainline_backtest.py --selection <path> --config-json /tmp/bt.json --name high_slip
```

预期：两次运行 config.json 反映各自参数；指标差异方向符合直觉（成本越高年化越低）。

## 自动化回归

```bash
python -m pytest tests/alpha/research/test_backtest_execution.py \
                 tests/alpha/research/test_backtest_portfolio.py \
                 tests/alpha/research/test_backtest_pipeline.py -v
python scripts/validate_alpha.py
```
