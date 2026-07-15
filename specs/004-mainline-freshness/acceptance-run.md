# Acceptance Run: 主线新鲜度规则

**Date**: 2026-07-09
**结论**: 全部场景通过；对照实验结果与前期粗略反事实存在重要差异（见分析）。

## 自动化测试（场景 0）

- `tests/alpha/research/test_mainline_freshness.py`: 11 passed（配置校验、月龄计数/重置/清空、I-14 不变量、stale 过滤、月龄与过滤解耦、e2e 两个方向、CLI、对照实验脚本）
- 全量 `tests/alpha/research`: 102 passed，001/002/003 无回归
- `python scripts/validate_alpha.py`: import-check / pytest-alpha(234 passed, 1 skipped) / py-compile-alpha 全部 OK

## 场景 1 回归保护（真实数据，max=0）

- 运行 `--name regression_004`，selection 去掉新增 `industry_streak` 列后与 004 前基线
  `outputs/mainline/20260708_180215_all_history_final/selection.parquet` **逐行一致**（`equals` = True）。

## 场景 2 月龄标注抽查

- GICS1信息技术：2023-02（streak=1）→ 2023-03（streak=2）递增正确；断档后 2024-09 重置为 1
- GICS1房地产：2024-09（1）→ 2024-10（2）递增正确
- max=0 运行中 reject_reason 无 "stale"

## 场景 3 新鲜度过滤（真实数据，max=1）

- selection 所有行 industry_streak = 1；industry_signals 存在 reject_reason="stale" 且月龄 ≥ 2 的行
- data_quality.json 出现 stale_industry_filtered 事件；有信号月 28 → 26（两个"全为老主线"的月份变为空仓）

## 场景 4 一键对照实验（真实数据，推荐风控：择时 + 无信号清仓）

产物：`outputs/mainline_backtest/20260709_120058_freshness_v1/comparison.md`

| 组 | 总收益 | 年化 | 最大回撤 | 夏普 | 月度胜率 | 有信号月 |
|---|---|---|---|---|---|---|
| fresh_baseline（不限制） | 1.95% | 0.52% | -22.81% | 0.12 | 35.7% | 28 |
| fresh_only（月龄≤1） | 2.27% | 0.60% | -27.81% | 0.12 | 30.8% | 26 |

补充对照（风控全关）：

| 组 | 总收益 | 年化 | 最大回撤 | 夏普 |
|---|---|---|---|---|
| baseline 无风控 | -31.37% | -9.52% | -52.81% | -0.18 |
| fresh 无风控 | -16.04% | -4.54% | -45.00% | +0.00 |

## 场景 5 可复现性（发现并修复一个 bug）

- 初次校验 industry_signals/stock_signals 出现字节级 MISMATCH（mom_60/rs_60 存在 1e-16 量级抖动）。
  根因：`industry_momentum()` 里 join 输出行序不稳定，浮点求和顺序随运行漂移——**004 之前就潜伏**，
  旧基线恰好未触发；004 的 selection join 未受影响（selection 一直逐字节稳定）。
- 修复：join 后先 `sort(["industry", "datetime", "vt_symbol"])` 再 `group_by(maintain_order=True)`，
  固定浮点求和顺序。修复后三个 parquet 两次运行**逐字节一致**，且修复不改变数值结论
  （回归对比对旧基线仍逐行一致）。

## 场景 6 门禁

- validate_alpha 纳入 `scripts/run_freshness_experiments.py`，全部通过。

## 结果分析（必读）

1. **无风控口径下新鲜度规则显著有效**：总收益 -31.4% → -16.0%，最大回撤 -52.8% → -45.0%。
   与前期实证（老主线下期收益转负）方向一致。
2. **叠加推荐风控后边际收益很小**：年化仅 +0.08pp，最大回撤反而差 5pp。原因是大盘择时已经
   把"老主线衰竭期"的大部分暴露切掉了——老主线转弱的月份多与弱市重叠，两条规则高度重叠。
   fresh 组回撤更差是因为持仓月更少、更集中，个别重仓月的下跌没有被分散。
3. **前期粗略反事实（+29.3%）不可复现为真实结论**：那是零成本、信号日收盘对收盘的口径，
   未计次日开盘执行、滑点成本与择时重叠效应。这正是"必须过真实回测引擎"的价值所在。
4. **建议**：默认保持 max_industry_streak=0。若希望进一步降回撤，方向应是仓位分散度
   （空仓月的资金利用）而非叠加新鲜度过滤；规则保留为可选开关供研究使用。
