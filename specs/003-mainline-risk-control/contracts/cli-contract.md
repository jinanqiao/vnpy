# CLI Contract: 主线策略风控层

## 1. `scripts/run_mainline_backtest.py` 参数扩展（既有契约保持不变）

新增参数（与 BacktestConfig 新字段一一对应）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--timing` | 关 | 打开大盘择时开关 |
| `--timing-ma-window N` | 60 | 择时均线窗口 |
| `--no-signal-exit` | 关 | 打开无信号月清仓开关 |
| `--stop-loss` | 关 | 打开个股止损开关 |
| `--stop-loss-rate X` | 0.15 | 止损阈值（0 < X < 1） |

行为：
- 不带任何新参数时行为与 002 完全一致（FR-008）
- `--config-json` 同样可覆盖这 5 个新字段；未知字段报错的既有语义不变
- 非法值（stop-loss-rate ≤ 0、ma-window < 2）→ stderr 明确报错，退出码 1

示例：

```bash
# 仅择时
python scripts/run_mainline_backtest.py --selection <path> --timing --name timing_only

# 三条全开 + 自定义止损线
python scripts/run_mainline_backtest.py --selection <path> \
    --timing --no-signal-exit --stop-loss --stop-loss-rate 0.20 --name all_on
```

## 2. `scripts/run_risk_experiments.py`（新增，FR-009）

```bash
python scripts/run_risk_experiments.py --selection <path> [--data-dir data] \
    [--output-dir outputs/mainline_backtest] [--name risk_comparison]
```

行为契约：
- 串行执行四组固定组合（baseline / timing / timing_ns / all_on，见 data-model §4），每组产物写各自 run 目录
- 全部完成后生成汇总目录 `<output-dir>/<时间戳>_<name>/comparison.md`：四组指标对照表 + 边际贡献段 + 各组产物目录路径
- 任一组回测失败 → stderr 报错，退出码 1，已完成组的产物保留
- 成功退出码 0；日志逐组打印组名与核心指标
