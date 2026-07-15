# CLI Contract: 主线新鲜度规则

## 1. scripts/run_mainline_signals.py（扩展）

新增参数：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| --max-industry-streak | int | 0 | 主线月龄上限；0=不限制；1=只买新晋主线 |

行为：

- 负值 → 退出码 1，stderr 提示"max_industry_streak 不能为负"
- `--config-json` 中的 `max_industry_streak` 可被命令行覆盖（与既有参数优先级一致：命令行最后生效）
- 未显式传入时保持 0，输出与 001 现状行为一致（新增 industry_streak 列除外）

示例：

```bash
# 只买新晋主线的全历史信号
python scripts/run_mainline_signals.py --max-industry-streak 1 --name fresh_only
```

## 2. scripts/run_freshness_experiments.py（新增）

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| --data-dir | str | data | 数据湖根目录 |
| --signal-output-dir | str | outputs/mainline | 两组信号产物根目录 |
| --backtest-output-dir | str | outputs/mainline_backtest | 两组回测与汇总产物根目录 |
| --name | str | freshness_comparison | 汇总目录名后缀 |
| --start / --end | str | ""（全历史） | 信号与回测共用的日期区间 |

行为：

1. 跑两组信号：baseline（max=0）、fresh（max=1），其余参数全默认
2. 每组信号各跑一次回测：timing_enabled=True，no_signal_exit_enabled=True，stop_loss_enabled=False
3. 汇总目录写 comparison.md（指标对照 + 差异 + 样本内声明 + 四个产物路径）
4. 任一步骤失败 → 退出码 1，stderr 给出失败环节
5. 成功 → 退出码 0

示例：

```bash
python scripts/run_freshness_experiments.py --name freshness_v1
```
