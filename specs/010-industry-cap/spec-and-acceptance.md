# 010 行业上限约束（T1）

## 目标

在 `industry_mode=none` 全市场选股模式下加一个行业占比上限开关
`max_per_industry`，缓解 008 版偶发的"19/30 只同行业"极端集中风险。

## 实现

- 新增配置 `MainlineConfig.max_per_industry: int = 0`（0=不限制，默认与 008 后
  行为逐字节一致；>0 时启用）。
- 逻辑（`vnpy/alpha/research/mainline/regime.py::select_stocks_global`）：
  先在各行业内按 score 降序取前 `max_per_industry` 只作候选池，再从候选池按
  score 降序取前 `regime_top_n` 只。用 `cum_sum` 而非 `rank` 编行业内序号，
  避免 rank 把非候选行占用名次导致膨胀。
- 顺延语义：某行业内候选被上限截断后，名额自动流向次强行业的下一名。
- 兼容测试：`tests/alpha/research/test_mainline_industry_cap.py` 7 项，含
  cap=0 与大 cap 的 parquet 逐字节回归。全量 `pytest tests/alpha/research/ -q`
  = 132 passed。

## 三档回测（2026-07-09）

- 输入选股：`--industry-mode none --industry-source gics1 --trend-filters off`
  `--score-weights=-0.40,0.35,-0.25 --rebalance-freq monthly` + `mom=60/conf=20`
- 回测风控：`--timing --timing-daily --timing-reentry --no-signal-exit`
  `--stop-loss --delta-rebalance`（生产组合，见 CLAUDE.md）
- 区间：2022-05-31 → 2026-07-01（50 期，含月末非交易日调整后 5 年）

### 主结果

| cap | 年化 | 最大回撤 | Sharpe | 2026 年化 | 每期最大行业占比（均值/最坏） | 输出目录 |
|-----|------|----------|--------|-----------|----------------|----------|
| **0（基线）** | **15.49%** | **-19.21%** | **0.985** | 34.67% | 10.12 / 19（33.7% / 63.3%） | `outputs/mainline_backtest/20260709_195300_t1_cap0_bt` |
| 6 | 15.31% | -17.95% | 0.987 | 37.59% | 6.00 / 6（20.0% / 20.0%） | `outputs/mainline_backtest/20260709_195312_t1_cap6_bt` |
| 8 | 15.90% | -19.87% | 1.003 | 35.38% | 7.78 / 8（25.9% / 26.7%） | `outputs/mainline_backtest/20260709_195322_t1_cap8_bt` |

### 分年度对照（净收益）

| 年 | 基准 300 | cap0 | cap6 | cap8 |
|----|---------|------|------|------|
| 2022 | -5.18% | -9.04% | -6.80% | -8.84% |
| 2023 | -11.75% | -3.34% | -2.67% | -2.52% |
| 2024 | +16.20% | +32.84% | +31.45% | +31.60% |
| 2025 | +21.19% | +26.07% | +22.58% | +27.38% |
| 2026 H1 | +2.00% | +34.67% | +37.59% | +35.38% |

## 结论

- **`max_per_industry=0` 保持默认（生产不变）**。cap6 在年化 (-0.18pp) 与
  Sharpe (+0.003) 上打平基线，回撤改善 1.27pp；cap8 年化 +0.41pp 但回撤反而
  多恶化 0.66pp——三档结果差异都在噪声量级，没有一个"明确打赢基线"。
- cap6 是防御型选择：50 期内每期最大行业占比严格封顶在 6 只（20%），代价
  是 2025 年少赚 3.49pp（cap0 在信息技术等主线里堆重仓时期赚得更多）。
- cap8 是折中型：均值最大占比 7.78 只（26%），2026 H1 相对基线并没有明显优
  势，最大回撤反而略高——说明 T1 只压"极端月份"，对回撤中枢无帮助。
- **开关保留但默认关闭**：给未来实盘上线时用户可选（如觉得 19/30 集中度
  监管报表不好看），本轮离线不改生产。基线数字与 CLAUDE.md 记录逐字节一致
  （15.49% ≈ 15.5% / -19.21% ≈ -19.2% / 0.985 ≈ 0.98）✓。

## 已知踩坑

- `run_mainline_signals.py` 的 CLI 默认参数（如 `--rebalance-freq weekly`）
  会硬覆盖 `--config-json` 里的值，跑 monthly 必须在命令行显式指定，光靠
  config-json 无效。首次跑 cap0 就误成了 weekly，已删除重跑。
- argparse 对负数参数：`--score-weights "-0.40,..."` 被识别为 flag，必须
  用等号形式 `--score-weights='-0.40,0.35,-0.25'`。

## 复现命令

```bash
# 三档 selection（monthly 必须显式给，config-json 覆盖不了 CLI 默认值）
for CAP in 0 6 8; do
  arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    scripts/run_mainline_signals.py \
    --industry-source gics1 --industry-mode none --trend-filters off \
    --score-weights='-0.40,0.35,-0.25' --rebalance-freq monthly \
    --config-json specs/010-industry-cap/cap${CAP}.json \
    --name t1_cap${CAP}
done

# 三档 backtest（生产风控组合）
for SEL in <三个 selection 路径>; do
  arch -arm64 ... scripts/run_mainline_backtest.py --selection $SEL \
    --timing --timing-daily --timing-reentry --no-signal-exit \
    --stop-loss --delta-rebalance --name t1_capX_bt
done
```
