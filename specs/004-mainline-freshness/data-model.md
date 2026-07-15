# Data Model: 主线新鲜度规则

**Date**: 2026-07-09

## 1. MainlineConfig 新增字段

| 字段 | 类型 | 默认 | 校验 | 说明 |
|---|---|---|---|---|
| max_industry_streak | int | 0 | >= 0 | 主线月龄上限；0=不限制（现状）；1=只买连续入选第 1 个月的新晋主线 |

`__post_init__` 校验非负，负值直接 `ValueError`。

## 2. 行业信号快照（industry_signals.parquet）列扩展

| 列 | 类型 | 取值 | 说明 |
|---|---|---|---|
| industry_streak | Int32 | >=1 或 null | 原始主线行业的连续入选月数；非主线行业为 null |

- **原始主线口径**: 三道门槛（member_count / rank_gate / breadth）+ TopN 达标，不含新鲜度过滤。
- **reject_reason 枚举扩展**: 新增 `"stale"`——行业本是原始主线，但月龄 > max_industry_streak 被过滤，`selected=False`。
- **不变量**: `industry_streak is not null` ⇔ (`selected` OR `reject_reason == "stale"`)。

## 3. 月龄状态机（pipeline 循环内）

```
prev_streak: dict[industry, int]   # 上一个快照期的原始主线月龄

每期：
  raw_mainline = 本期原始主线行业列表
  streak[i] = prev_streak.get(i, 0) + 1        # 上期也是原始主线则连任 +1，否则 1
  若 max > 0 且 streak[i] > max: 标记 stale，selected=False
  prev_streak = {i: streak[i] for i in raw_mainline}   # 落选行业月龄清零（下次重置为 1）

窗口不足被跳过的期：不产生快照，也不推进/清空状态（仅出现在历史开头）。
无主线期：快照存在但 raw_mainline 为空 → prev_streak 清空。
```

## 4. selection.parquet 列扩展

| 列 | 类型 | 说明 |
|---|---|---|
| industry_streak | Int32 | 该行个股所属行业的当期月龄（来自行业快照 join） |

列顺序: rebalance_date, vt_symbol, industry, score, industry_rank, industry_streak。
002 回测 `load_selection` 只校验必需五列存在，新增列透明。

## 5. data_quality.json 新事件

| type | 触发 | 字段 |
|---|---|---|
| stale_industry_filtered | 过滤开启且某行业因月龄被整期排除 | datetime, industry, streak, detail |

## 6. 对照实验产物（run_freshness_experiments.py）

```
outputs/mainline/<ts>_fresh_baseline/          # max=0 信号
outputs/mainline/<ts>_fresh_only/              # max=1 信号
outputs/mainline_backtest/<ts>_fresh_baseline/ # baseline 回测（择时+无信号清仓）
outputs/mainline_backtest/<ts>_fresh_only/     # fresh 回测（同风控）
outputs/mainline_backtest/<ts>_freshness_comparison/comparison.md
```

comparison.md 必含：两组指标对照表（总收益/年化/最大回撤/夏普/月度胜率/年均换手/成本拖累/有信号月数）、差异行、样本内声明。
