# Data Model: 主线强势股选股信号

**Date**: 2026-07-08　**Source**: spec.md Key Entities + research.md 数据实证

## 输入实体（数据湖，只读）

### DailyBar（`data/normalized/daily_bars_all_a.parquet`）

| 字段 | 类型 | 说明 |
|---|---|---|
| datetime | date | 交易日 |
| vt_symbol | str | 如 `000001.SZ` |
| open/high/low/close | f64 | 未复权 OHLC |
| volume | f64 | 成交量 |
| turnover | f64 | 成交额（元），FR-009 的量价因子用此列 |

约束：范围 2021-03-05 ~ 2026-07-02，5206 只；`turnover <= 0` 视为停牌日（与 pit_universe 口径一致）。

### SectorMember（`data/sector/sector_members.parquet`）

| 字段 | 类型 | 说明 |
|---|---|---|
| sector | str | 仅取 `GICS1*` 前缀的 11 个一级行业（R1） |
| vt_symbol | str | 成员股 |
| snapshot_at | datetime | 时点快照标记，写入产物局限性声明（R2） |

派生：`industry_map: vt_symbol -> industry`；一只股票理论上仅属一个 GICS1 行业，若出现多归属取字典序第一个并记数据质量日志。

### ExecutionUniverse（`data/universe/execution_universe.parquet`）

| 字段 | 类型 | 说明 |
|---|---|---|
| datetime / vt_symbol | date / str | 主键 |
| in_execution | bool | FR-006 可交易性判据 |
| execution_reason | str | 不可交易原因（st/suspended/limit 等） |

### TradingDate（`data/calendar/trading_dates.parquet`）

`market == "SH"` 的 trade_date 序列 → 派生**调仓日历**：每自然月最后一个交易日。

## 配置实体

### MainlineConfig（frozen dataclass，包内唯一的类，FR-013）

| 字段 | 默认 | 对应需求 |
|---|---|---|
| mom_window / confirm_window | 60 / 20 | FR-001 |
| breadth_window / breadth_min | 60 / 0.60 | FR-002/003 |
| industry_rank_gate / industry_top_n | 5 / 3（N=11 校准值，见 R1） | FR-003 |
| ma_windows / ma_slope_lag | (20,60,120) / 5 | FR-004 |
| bias_max | 0.25 | FR-005 |
| min_listed_bars | 252 | FR-006 |
| nh_window / nh_min | 252 / 0.80 | FR-008 |
| vol_short / vol_long / vol_cap | 20 / 60 / 3.0 | FR-009 |
| score_weights | (0.40, 0.35, 0.25)，支持等权对照 | FR-010 |
| stocks_per_industry | (6, 10) | FR-011 |
| universe | "all_a"（预留 "leaders"） | spec Assumptions |

## 输出实体（`outputs/mainline/<run_id>/`，全部逐调仓日留档）

### IndustrySnapshot（`industry_signals.parquet`）— FR-014

| 字段 | 类型 | 说明 |
|---|---|---|
| rebalance_date | date | 调仓日 |
| industry | str | GICS1 行业 |
| mom_60 / mom_20 | f64 | 双窗口动量 |
| rank_60 / rank_20 | i32 | 双窗口排名（1 为最强） |
| breadth_60 | f64 | 上涨广度 [0,1] |
| member_count | i32 | 参与计算的成分股数 |
| selected | bool | 是否入选主线 |
| reject_reason | str? | 未入选原因（rank_gate / breadth / member_count / null=入选） |

状态规则：`selected = (rank_60 <= gate) & (rank_20 <= gate) & (breadth_60 >= breadth_min)`，入选者按 mom_60 取前 industry_top_n。

### StockSnapshot（`stock_signals.parquet`）— FR-014

| 字段 | 类型 | 说明 |
|---|---|---|
| rebalance_date / vt_symbol / industry | 主键 | 仅主线行业内个股 |
| filter_ma_align / filter_bias / filter_nh / filter_tradable / filter_history | bool | 各过滤项通过情况（FR-004~006/008） |
| reject_reason | str? | 首个未通过的过滤项；null=存活进入打分 |
| rs_60 / nh_252 / vol_ratio | f64? | 三因子原始值（被过滤者为 null） |
| rank_rs / rank_nh / rank_vol | f64? | 行业池内百分位排名 [0,1]（VOL 按 R7 规则） |
| score | f64? | 加权总分 |
| industry_rank | i32? | 行业内名次 |
| selected | bool | 是否入选 |

### SelectionList（`selection.parquet`）— 最终清单

| 字段 | 说明 |
|---|---|
| rebalance_date / vt_symbol / industry | 入选记录 |
| score / industry_rank | 复核用 |

不变式：每 (rebalance_date, industry) 分组行数 ≤ stocks_per_industry 上限；全表按 (rebalance_date, industry, industry_rank) 排序保证输出确定性（SC-004）。

### RunManifest（`config.json` + `run_summary.json`）— FR-013

参数快照（config 全字段 + 数据文件路径与行数 + generated_at）、逐调仓日统计（主线行业数、候选数、过滤剔除数、入选数）、数据质量日志（无行业归属、疑似除权异常、窗口不足）。结构为包内自定义的扁平 JSON，不复用既有 run_summary 模块。

## 实体关系

```text
TradingDate ──派生──> 调仓日历
                        │ (逐调仓日)
DailyBar ──行业聚合──> IndustrySnapshot ──主线行业──┐
   │                                                ▼
   └──个股窗口计算──────────────────────────> StockSnapshot ──前N──> SelectionList
ExecutionUniverse ──可交易过滤──────────────────┘
SectorMember ──industry_map──（行业聚合与 RS 因子共用）
```
