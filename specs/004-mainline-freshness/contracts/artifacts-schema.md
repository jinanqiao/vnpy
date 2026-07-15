# Artifacts Schema: 主线新鲜度规则

在 001 的 artifacts-schema 基础上追加，未提及者不变。

## 1. industry_signals.parquet

- 新增列 `industry_streak`（Int32，可空）
- 不变量 I-14: `industry_streak` 非空 ⇔ (`selected` 为真 OR `reject_reason == "stale"`)
- 不变量 I-15: `reject_reason == "stale"` 只在 `max_industry_streak > 0` 的运行中出现
- 不变量 I-16: 同一行业相邻快照期均为原始主线时，后一期 `industry_streak` = 前一期 + 1；否则为 1

## 2. selection.parquet

- 新增列 `industry_streak`（Int32），值与该期行业快照一致
- 不变量 I-17: `max_industry_streak > 0` 时，所有行 `industry_streak <= max_industry_streak`
- 不变量 I-18: `max_industry_streak = 0` 时，除新增列外，行集合与既有列取值与 001 现状逐行一致（回归保护）

## 3. data_quality.json

- 新事件 `stale_industry_filtered`：{type, datetime, industry, streak, detail}

## 4. report.md（001 信号报告）

- 逐期表格的主线行业列在被过滤行业后标注"（第 N 月，已过滤）"或在表格下方列出过滤记录
- LIMITATIONS_NOTE 增加样本内声明：新鲜度规则源自同一样本的事后分析，正式结论以对照回测为准

## 5. comparison.md（新，对照实验汇总）

必含四部分：

1. 两组配置说明（baseline / fresh 的信号参数与共用风控开关）
2. 指标对照表：总收益、年化、最大回撤、夏普、月度胜率、年均换手、成本拖累、有信号月数
3. 差异行（fresh − baseline）
4. 样本内声明（固定文案）

## 6. 可复现性

- 同参数两次运行，selection / industry_signals / stock_signals 逐字节一致（沿用 001 约定）
