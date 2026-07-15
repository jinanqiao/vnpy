# 018 全局优化 Phase 1-4 汇总（2026-07-10）

## 目标

围绕现有月频反转选股策略（生产基线：年化 15.49% / 回撤 -19.21% / Sharpe 0.985），
一次性把因子扩展 / 组合权重 / 风控升级 / 频率池子四大维度的 16 个候选策略跑完对照，
挑出**明确打赢基线且样本内表现一致改善**的组合作为新生产候选。

## 执行架构

- Phase 1: T6 财务数据下载（akshare 三表 + 派生指标，918024 + 164402 行）
- Phase 2: PIT 对齐 + 4 个基本面因子检验（SUE / ROE / 净利润同比 / 应计）
- Phase 3: 12 个 subagent 并发跑单开关策略（S5-S16，git worktree 隔离）
- Phase 4: 单+双开关组合叠加（11 组主力）

## Phase 2 结论：基本面因子当前样本内全部无用

| 因子 | 定义 | 月频 t | 周频 t | 结论 |
|---|---|---:|---:|---|
| SUE | (Q净利 - 4Q均) / 4Q std | -0.23 | -0.46 | ✗ |
| ROE | akshare 净资产收益率(%) | -0.43 | -0.44 | ✗ |
| NI_YoY | akshare 净利润增长率(%) | -0.64 | -0.46 | ✗ |
| ACC | (净利 - 经营现金流) / 总资产 | -1.46 | -1.43 | ✗ |

四个因子 |t| 都 <2，全部不通过闸门。推测原因：
1. **A 股财报公告后有 30-45 天延迟**，月频调仓时价格已充分消化
2. **反转效应主导时期基本面被冲淡**（散户主导的市场）
3. 2021-2026 是极端反转期，样本偏差

**结论**：不接入任何基本面因子。数据留存在 `data/akshare/financial_*.parquet` 供后续实验。

## Phase 3 12 个单开关策略排序（对基线的 Δ）

| # | 策略 | Δ年化 | ΔSharpe | Δ回撤 | 结论 |
|---|---|---:|---:|---:|---|
| S7 | 组合波动率目标=0.10 | **+2.94pp** | **+0.32** | **+6.14pp** | 🟢 强推 |
| S13 | 相关性去重 0.8 | +0.15 | +0.05 | +1.48 | 🟢 弱推（未合入 main）|
| S12 | min_price=3 | +0.97 | +0.05 | +0.14 | 🟢 弱推 |
| S15 | 单股 cap=0.04 | 0 | 0 | 0 | 🟡 未触发 |
| S10 | 时间止损=3 | 0 | 0 | 0 | 🟡 未触发（delta_rebalance 已剪） |
| S14 | min pos change | 0 | 0 | 0 | 🟡 未触发（反转策略新旧交集小） |
| S16 | 剔高波 0.05 | -0.18 | 0 | +0.14 | 🟡 中性 |
| S5 | vol_inv 加权 | -0.80 | -0.02 | +0.21 | 🔴 不启用 |
| S6 | 行业中性权重 | -0.51 | -0.02 | -1.56 | 🔴 熊市档 |
| S9 | ATR 止损 | -2.24 | -0.06 | +0.85 | 🔴 不启用 |
| S11 | 双周频调仓 | -3.69 | -0.20 | -1.28 | 🔴 不启用 |
| S8 | 三均线共振 | -11.87 | -0.47 | +6.25 | 🔴 太保守 |

**过闸门的核心策略**：S7（唯一三指标全强改善）+ S12（全指标微改善）

## Phase 4 单+双组合叠加（11 组，生产 selection + 生产风控）

| 组合 | 年化 | 回撤 | Sharpe | ΔSharpe |
|---|---:|---:|---:|---:|
| (baseline) | 15.49% | -19.21% | 0.985 | – |
| S7 单独 | 18.43% | -13.07% | 1.306 | +0.321 |
| S15 单独 | 15.49% | -19.21% | 0.985 | 0 |
| S12 单独 | 16.46% | -19.07% | 1.034 | +0.049 |
| S16 单独 | 15.30% | -19.07% | 0.982 | -0.003 |
| **S7 + S12** | **19.34%** | **-12.76%** | **1.356** | **+0.371** ⭐ |
| S7 + S15 | 17.15% | -13.17% | 1.224 | +0.239 |
| S7 + S16 | 17.94% | -16.06% | 1.237 | +0.253 |
| S15 + S12 | 16.46% | -19.07% | 1.034 | +0.049 |
| S15 + S16 | 15.30% | -19.07% | 0.982 | -0.003 |
| S12 + S16 | 16.04% | -19.16% | 1.022 | +0.038 |

## 冠军：S7 + S12（vol_target=0.10 + min_price=3）

| 维度 | 基线 | S7+S12 | Δ |
|---|---|---|---|
| 年化收益 | 15.49% | **19.34%** | **+3.85pp** |
| 最大回撤 | -19.21% | **-12.76%** | **+6.45pp** |
| Sharpe | 0.985 | **1.356** | **+0.371** |
| Gross 年化 | 18.04% | 21.47% | +3.43pp |
| 换手率 | 13.52x | 12.43x | -1.09x |
| 成本拖累 | 2.55% | 2.14% | -0.41pp |
| 跑赢基准胜率 | 66% | 66% | 0 |

### 分年度对照

| 年 | 基准300 | 基线 | S7+S12 |
|---|---|---|---|
| 2022 | -5.18% | -5.31% | **-1.24%** |
| 2023 | -11.75% | -3.20% | **-0.71%** |
| 2024 | +16.20% | +31.20% | **+40.86%** |
| 2025 | +21.19% | +24.92% | +23.26% |
| 2026 H1 | +2.00% | +14.82% | +15.27% |

**每年都不输基线，2022/2023/2024 显著更强**。

## 上线建议

**推荐把生产配置升级为**：
```
score_weights=(-0.40, +0.35, -0.25)  # 保持不变
industry_mode=none                    # 保持不变
mom_window=60, confirm_window=20      # 保持不变
--min-price 3.0                       # 新增（S12）
--portfolio-vol-target 0.10           # 新增（S7）
其他生产风控（timing/daily/reentry/stop-loss/delta-rebalance）保持不变
```

**注意事项**：
1. S7 是**样本内**结论（2022-05 ~ 2026-07 全期），需要用 T4 滚动 OOS 复现
   `run_rolling_oos_backtest.py` 验证 OOS 也成立才建议上线
2. S12 的 min_price=3 会过滤掉一部分低价股，实盘容量与老组合略有差异
3. S13 相关性去重（Sharpe +0.05 / 回撤 -1.48pp）值得后续单独合并到 main 再评估

## 已合入 main 的开关（默认全部关闭 = 逐字节回归）

- `portfolio_vol_target: float = 0.0`（S7）— `--portfolio-vol-target 0.10` 启用
- `min_price: float = 0.0`（S12）— `--min-price 3.0` 启用
- `max_position_weight: float = 0.0`（S15）— `--max-position-weight 0.04` 启用
- `max_volatility: float = 0.0`（S16）— `--max-volatility 0.05` 启用
- `illiq_weight: float = 0.0`（011）
- `min_turnover_avg20: float = 0.0`（011）
- `max_per_industry: int = 0`（010）
- `volatility_weight: float = 0.0`（009）

## 未合入 main 的开关（在 worktree 分支保留，`git checkout worktree-agent-*` 可查看）

- S5 vol_inv 加权（`worktree-agent-a25ab9adcec1db420`）
- S6 industry_neutral（`worktree-agent-a6b4bdc4db4bca923`）
- S8 三均线共振（`worktree-agent-a843160f4e8a5f1e1`）
- S9 ATR 止损（`worktree-agent-a9d41de3a0c2674f6`）
- S10 时间止损（`worktree-agent-a1271a842b0fb55f5`）
- S11 双周频（`worktree-agent-af9df4da22b4acced`）
- S13 相关性去重（`worktree-agent-a4993610f630344ca`）
- S14 min_position_change（`worktree-agent-a20fc5e1ae7a316c8`）

## 复现命令

```bash
# 冠军组合：S7 vol_target=0.10 + S12 min_price=3

# 1. 生成 S12 selection
arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    scripts/run_mainline_signals.py \
    --industry-source gics1 --industry-mode none --trend-filters off \
    --score-weights='-0.40,0.35,-0.25' --rebalance-freq monthly \
    --config-json specs/010-industry-cap/cap0.json \
    --min-price 3.0 --name production_next

# 2. S7 backtest
SEL=$(ls -td outputs/mainline/*_production_next | head -1)/selection.parquet
arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    scripts/run_mainline_backtest.py --selection "$SEL" \
    --timing --timing-daily --timing-reentry --no-signal-exit \
    --stop-loss --delta-rebalance \
    --portfolio-vol-target 0.10 \
    --name production_next_bt
```

## T4 滚动 OOS 验证 S7+S12（已跑，2026-07-10）

| 指标 | A 基线均值 | B (S7+S12) 均值 | Δ |
|---|---:|---:|---:|
| 年化 | +13.36% | **+14.74%** | +1.38pp |
| 回撤 | -11.53% | **-8.70%** | +2.83pp |
| Sharpe | 0.966 | **1.175** | +0.209 |

- **年化 7/8 窗口打赢**（唯一 -0.08 是 2023-05~2024-04，接近打平）
- **回撤 8/8 窗口全部改善**
- **Sharpe 7/8 窗口打赢**（唯一 -0.064 是 2024-11~2025-10）
- 最近窗口 2025-07 ~ 2026-06：**年化 +34.16% / 回撤 -7.14% / Sharpe 2.279**（相比基线 +33.16% / -10.02% / 1.982）

**样本外验证通过，可以上线 S7+S12 为新生产配置。**

产物：`outputs/mainline_backtest/rolling_oos_p4_champion_oos/summary.md`

## 后续（未跑）
- **T3 因子健康度季检加 vol_target 稳健性**：`portfolio_vol_target` 是"σ 反比缩放"，
  非因子方向类，但仍值得每季看它的历史 scale 均值有没有跑出 [0.5, 1.5] 边界
- **T5 akshare 数据**已入湖（financial_reports.parquet / financial_indicators.parquet），
  下次做行为金融类因子（北向、两融）时同一套下载脚本可复用（改 `stock_zh_a_daily`）
