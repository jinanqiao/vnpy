# Quickstart: 主线新鲜度规则验收

## 场景 0 自动化测试

```bash
python3 -m pytest tests/alpha/research/test_mainline_freshness.py tests/alpha/research/test_mainline_pipeline.py -q
```

预期：全部通过；既有 001/002/003 测试无回归。

## 场景 1 回归保护（真实数据，max=0）

```bash
python scripts/run_mainline_signals.py --name regression_004
```

预期：selection.parquet 去掉 industry_streak 列后，与 004 之前的基线运行逐行一致。

## 场景 2 月龄标注检查

对场景 1 产物：

- 任选一个连任行业，核对相邻期 industry_streak 递增
- 无主线月后的第一个主线月，所有行业 streak=1
- reject_reason 无 "stale"（因为 max=0）

## 场景 3 新鲜度过滤（真实数据，max=1）

```bash
python scripts/run_mainline_signals.py --max-industry-streak 1 --name fresh_only_004
```

预期：

- selection 所有行 industry_streak = 1
- industry_signals 中存在 reject_reason="stale" 且 industry_streak >= 2 的行
- data_quality.json 出现 stale_industry_filtered 事件

## 场景 4 一键对照实验（真实数据）

```bash
python scripts/run_freshness_experiments.py --name freshness_v1
```

预期：两组信号 + 两组回测 + comparison.md；指标对照与差异行完整；含样本内声明。

## 场景 5 可复现性

同参数连跑两次场景 3，selection/industry_signals/stock_signals 逐字节一致。

## 场景 6 门禁

```bash
python scripts/validate_alpha.py
```

预期：编译检查覆盖新脚本与新测试文件，全部通过。
