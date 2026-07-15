# Quickstart: 主线强势股选股信号 — 验证指南

**Date**: 2026-07-08　按本指南跑通即证明特性端到端可用（对应 spec Success Criteria）。

## 前置条件

- 项目环境可运行 `python`，polars 可用（现有 turtle 流水线能跑即满足）
- 数据湖文件齐备：`data/normalized/daily_bars_all_a.parquet`、`data/sector/sector_members.parquet`、`data/universe/execution_universe.parquet`、`data/calendar/trading_dates.parquet`

## 场景 1：单调仓日冒烟（SC-001，约 10 秒）

```bash
python scripts/run_mainline_signals.py --start 2026-05-25 --end 2026-06-05 --name smoke
```

预期：退出码 0；`outputs/mainline/<run_id>_smoke/` 下 6 个产物文件齐备（见 [contracts/artifacts-schema.md](./contracts/artifacts-schema.md)）；`report.md` 显示 2026-05/06 月末各一期信号、主线行业 ≤ 3 个、每行业入选 6~10 只。

## 场景 2：主线行业人工评审（SC-005，对应 US1）

打开 `industry_signals.parquet`（或 report.md 的行业段落），核对最近一期：入选行业的 mom_60 排名、mom_20 排名均 ≤ 5 且 breadth_60 ≥ 0.60；入选行业方向与该时点市场公认热点一致；每个未入选行业的 reject_reason 属于 {rank_gate, breadth, member_count}。

## 场景 3：个股信号复核（SC-002/003，对应 US2）

从 `selection.parquet` 抽最近一期得分前 10 的个股，逐一确认对应 `stock_signals` 行：全部 `filter_* = true`；`score = 0.40*rank_rs + 0.35*rank_nh + 0.25*rank_vol` 手工复算误差为 0；再抽任意 3 只被剔除个股，`reject_reason` 唯一且属于既定枚举。

## 场景 4：可复现性（SC-004）

```bash
python scripts/run_mainline_signals.py --start 2025-01-01 --end 2025-06-30 --name repro_a
python scripts/run_mainline_signals.py --start 2025-01-01 --end 2025-06-30 --name repro_b
# 对比两次产物（排除含时间戳的 config.json generated_at 与 run_id）
```

预期：三个 parquet 文件内容逐字节一致（可用文件哈希对比脚本或 polars frame_equal）。

## 场景 5：全历史运行（性能约束）

```bash
time python scripts/run_mainline_signals.py --name all_history
```

预期：覆盖约 2022-03 起全部月末调仓日（约 52 期），总耗时 ≤ 5 分钟；`data_quality.json` 中窗口不足/无行业归属的记录数量级合理（数千条级，集中在次新股）。

## 场景 6：等权对照与参数覆盖（FR-010/013）

```bash
python scripts/run_mainline_signals.py --equal-weights --name eq_weights
echo '{"industry_top_n": 5}' > /tmp/cfg.json
python scripts/run_mainline_signals.py --config-json /tmp/cfg.json --name top5
```

预期：两次运行的 `config.json` 分别反映等权权重与 industry_top_n=5；产物结构不变。

## 自动化回归

```bash
python -m pytest tests/alpha/research/test_mainline_industry.py \
                 tests/alpha/research/test_mainline_stock.py \
                 tests/alpha/research/test_mainline_pipeline.py -v
python scripts/validate_alpha.py   # 纳入既有质量门禁后
```
