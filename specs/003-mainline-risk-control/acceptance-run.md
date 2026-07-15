# Acceptance Run: 主线策略风控层

**Date**: 2026-07-09　真实数据验收记录（quickstart.md 场景 1~5 + 自动化门禁）。

## 自动化测试

- `tests/alpha/research/test_backtest_risk.py`：15 个测试全部通过（规则单测 8 + 择时 3 + 无信号 2 + 止损 3 中含组合场景 + 对照实验 1，按文件内分组计）
- 002 既有回测测试（execution/portfolio/pipeline 共 22 个）无回归，合计 37 passed
- `scripts/validate_alpha.py` 门禁：223 passed, 1 skipped（risk.py 与 run_risk_experiments.py 已纳入编译检查）

## 场景 1：回归保护（SC-001）✅

不带任何风控参数运行（`20260709_104809_regression_check2`），与 002 基线
`20260708_200231_repro_a` 的 SHA-256 比对：

| 文件 | 结果 |
|---|---|
| nav.parquet | 一致 |
| positions.parquet | 一致 |
| trades.parquet | 一致 |

实现注记：清仓路径重构为共用的 `sell_all_positions()` 时，现金/成本必须**逐笔更新**
而不是求和后一次性加回——浮点加法顺序不同会造成第 24 天起 1e-16 量级的净值漂移，
首次比对 nav/positions 因此 MISMATCH，改回逐笔记账后字节一致。

## 场景 2：仅择时（SC-002）✅

`20260709_104836_risk_timing`：最大回撤 -52.81% → **-26.62%**（收窄 26 个百分点），
年化 -9.52% → -1.75%。data_quality.json 含 11 个 timing_skip 事件；
report.md 风控段正确显示"跳过 11 个调仓期"，其余两条规则显示"未启用"。

## 场景 3：四组对照一键跑（SC-003/006）✅

`python3 scripts/run_risk_experiments.py --selection <all_history_final>` 总耗时 **7.3 秒**（≤ 5 分钟）。

| 组 | 择时 | 无信号清仓 | 止损 | 总收益 | 年化 | 最大回撤 | 夏普 | 月度胜率 | 年均换手 |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 关 | 关 | 关 | -31.37% | -9.52% | -52.81% | -0.18 | 42.9% | 5.4 |
| timing | 开 | 关 | 关 | -6.41% | -1.75% | -26.62% | 0.01 | 42.9% | 4.2 |
| timing_ns | 开 | 开 | 关 | +1.95% | +0.52% | -22.81% | 0.12 | 35.7% | 4.3 |
| all_on | 开 | 开 | 开 | -6.01% | -1.63% | -22.49% | -0.02 | 28.6% | 4.0 |

边际贡献：择时 年化 +7.78% / 回撤 +26.18pp；无信号清仓 年化 +2.26% / 回撤 +3.82pp；
个股止损 年化 **-2.15%** / 回撤 +0.31pp（15% 止损线在本组信号上是负贡献，符合"止损
容易卖在恐慌底"的已知现象，默认关闭是合理默认）。
每组 config.json 快照开关状态与组名一致；baseline 组三个 parquet 与 002 基线字节一致。

## 场景 4：止损追溯（SC-004）✅

all_on 组 100 笔 reason=stop_loss 卖出（全部 filled），100/100 在 data_quality.json
中有同股票、更早日期的 stop_loss_trigger 事件。抽查 `600325.SH`：触发日 2022-12-22
（回撤 -16.48% > 15% 阈值）→ 次一交易日 2022-12-23 执行，链路完整。

## 场景 5：可复现性（SC-005）✅

`--timing --no-signal-exit --stop-loss` 连续运行两次，nav/positions/trades 三个
parquet SHA-256 逐一比对一致。

## 附加验证

- 非法参数：`--stop-loss-rate -0.1` → stderr "stop_loss_rate 必须 > 0"，退出码 1 ✅
- 可读性自查：子包全部文件 ≤ 300 行（最大 execution.py 297 行）；无继承/装饰器/生成器；
  公开函数中文 docstring；阅读指南更新为 8 个文件 ✅
