# Artifacts Schema Contract: `outputs/mainline/<run_id>/`

**Date**: 2026-07-08　对应 FR-013/014、SC-002/003/006

## 文件清单（每次运行必须全部产出）

| 文件 | 格式 | 内容 |
|---|---|---|
| `config.json` | JSON | MainlineConfig 全字段快照 + universe + 数据文件路径与行数 + generated_at |
| `industry_signals.parquet` | parquet | 行业信号快照，逐调仓日 × 全部 11 个 GICS1 行业 |
| `stock_signals.parquet` | parquet | 个股信号快照，逐调仓日 × 主线行业内全部个股（含被过滤者） |
| `selection.parquet` | parquet | 最终入选清单 |
| `data_quality.json` | JSON | 数据质量日志：无行业归属、窗口不足、疑似除权异常（R3 防护）、行业成员不足 |
| `report.md` | Markdown | 人读摘要：逐期主线行业、入选数、空清单期原因、固定的局限性声明（快照行业成分 + 未复权价格） |

## 列级 schema

三个 parquet 的列名、类型、可空性与排序键以 [data-model.md](../data-model.md) 的 IndustrySnapshot / StockSnapshot / SelectionList 定义为准，实现不得增删列名或改变语义；新增列必须先修订 data-model.md。

## 不变式（验收抽查依据）

1. `selection` 中每条记录必在 `stock_signals` 中存在对应行且 `selected = true`、全部 `filter_* = true`（SC-002）。
2. `stock_signals` 中 `reject_reason` 非空 ⇔ `score` 为空；`reject_reason` 取值枚举：`ma_align` / `bias` / `nh_min` / `not_tradable` / `insufficient_history` / `missing_factor`（SC-003/006）。
3. `industry_signals` 中 `selected = true` 的行业数 ≤ industry_top_n；`reject_reason` 枚举：`rank_gate` / `breadth` / `member_count`。
4. 每 (rebalance_date, industry) 的 selection 行数 ∈ [1, stocks_per_industry 上限]，不足下限时 data_quality.json 必有对应记录。
5. `score` 复算：`score = w1*rank_rs + w2*rank_nh + w3*rank_vol`，误差为 0（SC-003）。
6. 全部 parquet 按固定键排序：industry_signals (rebalance_date, industry)；stock_signals (rebalance_date, industry, vt_symbol)；selection (rebalance_date, industry, industry_rank)（SC-004）。
