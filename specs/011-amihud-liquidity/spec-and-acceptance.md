# 011 Amihud 非流动性因子（T2b）

## 目标

给个股打分层加一个可选的 Amihud 非流动性因子（第五个打分因子），
测试它作为"与现有 rs/nh/vol 三因子低相关的独立信号"能否显著提升样本内表现。

## 背景（前置检验）

- 检验脚本：`scripts/validate_lottery_liquidity_factors.py`
- 检验报告：`outputs/factor_checks/lottery_liquidity_gics1.md`
- 定义：`amihud_20 = 近 20 日 mean(|日收益| / 成交额)`（成交额≤0 记 null 跳过）
- 关键数字（2021-03 ~ 2026-07 样本内）：

| 池 | IC均值 | ICIR | t值 | Q1 | Q3 | Q5 | 顶底价差年化 | 单调 |
|---|---|---|---|---|---|---|---|---|
| weekly/all | +0.046 | +0.28 | +4.34 | +0.06% | +0.19% | +0.51% | +23.3% | 升 |
| monthly/all | +0.077 | +0.46 | +3.44 | +0.53% | +1.00% | +1.98% | +17.4% | 升 |

- **独立性（handoff T2 闸门）**：与 rs/nh/vol 三因子的截面秩相关绝对值均 ≤ 0.09，
  远低于 0.5 的闸门（对比：现有 rs/vol 相关 +0.40，都在往同一"近期热度"方向堆）。
- **同批检验的 MAX 因子未通过闸门**：t 值虽显著（-6~-8），但与 vol_ratio 相关 +0.46、
  weekly/filtered 与 rs 相关 +0.49，加入等于给现有反向 rs/vol 加杠杆，不接入。

## 实现

- 新增配置：`MainlineConfig.illiq_window: int = 20`（滚动窗口）+
  `illiq_weight: float = 0.0`（默认 0 关闭，逐字节回归 008/009 后行为）。
- `stocks.py::calc_price_features`：新增每股 `illiq` 列（近 20 日 mean(|pct_change|/turnover)）。
- `stocks.py::calc_stock_factors`：`illiq_weight != 0` 时参与 `missing_factor` 判定，
  避免用默认值补位；关闭时不参与，保持旧行为逐字节兼容。
- `stocks.py::rank_and_score`：新增 `rank_illiq` 百分位排名（**正权重** = 越不流动分越高），
  在存活候选池内 rank，合成分累加 `illiq_weight * rank_illiq`。
- `STOCK_SNAPSHOT_COLUMNS` 追加 `illiq` / `rank_illiq` 两列，快照产物同步更新。
- CLI：`scripts/run_mainline_signals.py` 新增 `--illiq-weight` 参数。

## 测试

`tests/alpha/research/test_mainline_stock.py` 新增 2 项：
- `test_illiq_factor_weight_and_off_switch`：off 时 `rank_illiq` 全空 + 总分不变；
  on(+0.25) 时 THIN vs LIQ 分数差 = +0.25 * (1.0 - 0.5) = +0.125 手算对照。
- `test_illiq_missing_only_gates_when_enabled`：`illiq_weight=0` 时 illiq 缺失不报
  missing_factor（回归保护）；`illiq_weight=0.25` 时缺失才拦。

全量 `pytest tests/alpha/research/ -q` = **134 passed**（132 老 + 2 新 illiq）。

## 三档回测（2026-07-09）

- 输入选股：`--industry-mode none --industry-source gics1 --trend-filters off`
  `--score-weights=-0.40,0.35,-0.25 --rebalance-freq monthly` + `mom=60/conf=20`
- 回测风控：`--timing --timing-daily --timing-reentry --no-signal-exit`
  `--stop-loss --delta-rebalance`（生产组合，见 CLAUDE.md）
- 区间：2022-05-31 → 2026-07-01（50 期，5 年）

### 主结果

| illiq_w | 年化 | 最大回撤 | Sharpe | Gross年化 | 换手率 | 成本拖累 | 输出目录 |
|---------|------|----------|--------|-----------|--------|----------|----------|
| **0（基线）** | **15.49%** | **-19.21%** | **0.985** | 18.04% | 13.5x | 2.55% | `outputs/mainline_backtest/20260709_225902_t2b_illiq0_bt` |
| **+0.15** | **19.85%** | **-19.25%** | **1.123** | 22.54% | 15.9x | 2.69% | `20260709_225907_t2b_illiq15_bt` |
| +0.25 | 17.99% | -19.29% | 1.013 | 20.66% | 15.1x | 2.67% | `20260709_225911_t2b_illiq25_bt` |

### 分年度对照（净收益）

| 年 | 基准300 | illiq0 | illiq15 | illiq25 |
|----|---------|--------|---------|---------|
| 2022 | -5.18% | -5.31% | **+1.26%** | +1.81% |
| 2023 | -11.75% | -3.20% | -2.33% | -4.41% |
| 2024 | +16.20% | +31.20% | **+50.59%** | +46.47% |
| 2025 | +21.19% | +24.92% | +26.75% | +26.48% |
| 2026 H1 | +2.00% | +14.82% | +6.55% | +4.11% |

### 与三档 selection 的重叠度

| 对比 | 入选票交集 |
|---|---|
| illiq0 vs illiq15 | 54.1% |
| illiq0 vs illiq25 | 40.3% |
| illiq15 vs illiq25 | 82.3% |

### 容量风险（入选池平均成交额分位，越低越偏小盘）

| illiq_w | 均值 | 最低 | 最高 |
|---------|------|------|------|
| 0（基线） | 0.517 | 0.166 | 0.844 |
| +0.15 | **0.306** | 0.141 | 0.501 |
| +0.25 | **0.244** | 0.128 | 0.418 |

**关键警告**：+0.15 权重把入选池平均成交额分位从 52% → 31%，
+0.25 更严重（24%）。多出来的年化不是"免费午餐"，是把持仓推向小市值低流动性股票，
**实盘容量会显著打折**（回测按后复权收盘价成交，不建模流动性冲击）。

## 结论（初次三档对照）

- **+0.15 权重打赢无过滤基线**（年化 +4.36pp、Sharpe +0.14、回撤基本不变），
  三档中最优；+0.25 收益已回吐（过拟合权重上限），且更极端偏小盘。
- 收益来源诊断：Gross 年化 18.04% → 22.54%（+4.5pp），成本拖累仅 +0.14pp，
  换手率 13.5→15.9x——收益不是"用换手成本换的"，选股 Alpha 确实提升。
- **但**：入选池成交额分位从 0.52 塌到 0.31，需要一轮"叠加成交额下限过滤"的
  鲁棒性对照来判断这个 Alpha 是否只是"小盘规模溢价"。见下节。

## 容量鲁棒性对照（B 方案，2026-07-09）

固定 `illiq_weight=0.15`，叠加 `min_turnover_avg20` 硬过滤（近 20 日均成交额下限），
三档阈值：0.3 亿 / 0.6 亿 / 1.0 亿（约对应 2024 年全市场分位 p10/p50/p70）。

### 全对照

| 组合 | 年化 | 回撤 | Sharpe | Gross 年化 | 换手 | 成交额分位均值 |
|---|---|---|---|---|---|---|
| illiq0（生产基线） | 15.49% | -19.21% | 0.985 | 18.04% | 13.5x | 0.517 |
| **illiq15 无过滤** | 19.85% | -19.25% | 1.123 | 22.54% | 15.9x | **0.306** |
| illiq15 + min=0.3亿 | 17.34% | -20.25% | 1.018 | 20.04% | 15.0x | 0.359 |
| illiq15 + min=0.6亿 | **14.70%** | -17.28% | 0.889 | 17.42% | 14.2x | 0.517 |
| illiq15 + min=1.0亿 | 6.65% | -17.85% | 0.472 | 9.47% | 11.9x | 0.648 |

### 分年度净收益（三档鲁棒方案）

| 年 | min0.3 | min0.6 | min1.0 |
|---|---|---|---|
| 2022 | -2.16% | -5.51% | -5.11% |
| 2023 | -2.20% | -2.11% | -5.48% |
| 2024 | +43.49% | +42.00% | +23.49% |
| 2025 | +25.05% | +19.23% | +13.02% |
| 2026 H1 | +7.41% | +7.68% | +1.37% |

### 关键发现（**决定性证据**）

- **Amihud 的超额收益几乎全部来自小盘偏移**——一旦把成交额分位从 0.31 拉回 0.52
  （min=0.6 亿），年化就从 19.85% 塌回 14.70%，**反而不如生产基线 15.49%**。
- 拉到 0.65（min=1 亿）年化只有 6.65%，几乎归零。
- min=0.3 亿是"折中"：留住大部分小盘、年化 17.34% / Sharpe 1.02，
  但比生产基线只多 1.85pp 年化，回撤反而恶化 1pp（-19.21% → -20.25%），**性价比很弱**。

## 最终结论（不建议改生产默认）

- **`illiq_weight = 0.0` 保持默认**：Amihud 因子的 Alpha 在样本内**不是独立信号**，
  而是"小盘规模溢价"的另一种编码。回测的 4pp 年化在实盘不可获得
  （小盘容量打折 + 冲击成本 + 流动性风险）。
- **`min_turnover_avg20 = 0` 保持默认**：作为独立开关保留，
  实盘上线时可与其他策略组合按客户容量偏好开启。
- **两个开关都不改生产**，代码完成、开关就位、有测试保护、有 spec 记录，
  等下一轮"引入真正独立的正交信号（如财务质量、非价量）"再重新考虑组合方式。

## 已知踩坑

- 检验期发现 turnover<=0（停牌）时 `|ret|/turnover` 会爆炸/生成 inf，
  必须先把 turnover<=0 位记 null 让 rolling_mean 跳过。测试用手工构造特征表调
  `calc_stock_factors`，可能没 `illiq` 列——添加缺列的兼容分支（已加）。
- `illiq_weight != 0` 才让 illiq 缺失触发 missing_factor，避免关闭时因为窗口
  短一天误剔股票破坏逐字节回归。
- **重大坑（本轮教训）**：因子 Rank IC 显著 + 分层单调 + 与现有因子低相关 ≠ 好因子。
  Amihud 检验时看着完美（t=+4.34、Q1→Q5 单调、相关 ≤ 0.09），但根本原因是它
  把打分推向小盘股，收益溢价来自小盘规模效应而非非流动性溢价本身。
  **加"叠加流动性过滤后的鲁棒性对照"到 T2b 的标准闸门中**（handoff 应更新）。

## 已知踩坑

- 检验期发现 turnover<=0（停牌）时 `|ret|/turnover` 会爆炸/生成 inf，
  必须先把 turnover<=0 位记 null 让 rolling_mean 跳过。测试用手工构造特征表调
  `calc_stock_factors`，可能没 `illiq` 列——添加缺列的兼容分支（已加）。
- `illiq_weight != 0` 才让 illiq 缺失触发 missing_factor，避免关闭时因为窗口
  短一天误剔股票破坏逐字节回归。

## 复现命令

```bash
# 初次三档 selection（不同 illiq 权重）
for W in 0 15 25; do
  arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    scripts/run_mainline_signals.py \
    --industry-source gics1 --industry-mode none --trend-filters off \
    --score-weights='-0.40,0.35,-0.25' --rebalance-freq monthly \
    --config-json specs/011-amihud-liquidity/illiq${W}.json \
    --name t2b_illiq${W}
done

# 鲁棒性三档：固定 illiq=0.15，加不同流动性下限
for MIN in 3 6 10; do
  arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    scripts/run_mainline_signals.py \
    --industry-source gics1 --industry-mode none --trend-filters off \
    --score-weights='-0.40,0.35,-0.25' --rebalance-freq monthly \
    --config-json specs/011-amihud-liquidity/robust_illiq15_min${MIN}.json \
    --name t2b_robust_illiq15_min${MIN}
done

# 六档 backtest（生产风控组合）
for TAG in t2b_illiq0 t2b_illiq15 t2b_illiq25 \
           t2b_robust_illiq15_min3 t2b_robust_illiq15_min6 t2b_robust_illiq15_min10; do
  SEL=$(ls -td outputs/mainline/*_${TAG} | head -1)/selection.parquet
  arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    scripts/run_mainline_backtest.py --selection "$SEL" \
    --timing --timing-daily --timing-reentry --no-signal-exit \
    --stop-loss --delta-rebalance --name ${TAG}_bt
done
```
