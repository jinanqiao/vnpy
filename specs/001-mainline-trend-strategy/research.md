# Phase 0 Research: 主线强势股选股信号

**Date**: 2026-07-08

本阶段对数据湖实际形态做了实证核查（读取真实 parquet 文件确认 schema 与覆盖范围），解决 plan 技术上下文中的全部未知项。

## R1. 行业分类数据的实际形态与"行业"的定义

**Decision**: 使用 `data/sector/sector_members.parquet` 中 `GICS1` 前缀的 11 个 GICS 一级行业作为行业层分组；`上证A股/创业板/沪深A股` 等宽基板块不参与行业排名。

**Rationale**: 实测该文件含 18 个 sector，其中 11 个为 GICS 一级行业（信息技术 1608 只、工业 2103 只、金融 509 只等），其余是交易所宽基板块（与"行业"语义不符，且成员互相包含）。GICS1 覆盖约 5200 只 A 股，与日线数据的 5206 只基本对齐。

**Alternatives considered**: 申万一级行业（数据湖中不存在，QMT 板块接口未提供）；用全部 18 个 sector（宽基板块会污染行业排名，否决）。

**连带影响（重要）**: spec 的"排名前 8 取前 5"默认参数是按约 30 个行业的假设拟定的；实际行业数 N=11 时，"前 8"门槛过松（73% 的行业都算候选）。处理：参数保持可配置（FR-013 已要求），**默认值按 N=11 调整为"双窗口排名均进前 5、按 60 日动量取前 3"**，原 8/5 值保留为配置项注释。该调整幅度需在回测阶段做敏感性检验，spec 的参数表同步更新由 tasks 阶段落实。

## R2. 行业成分是时点快照，非逐日历史

**Decision**: 接受 `snapshot_at = 2026-07-02` 的当前时点行业归属，按 spec Assumptions 的既定决策执行，产物说明中固定输出该局限性声明。

**Rationale**: 文件仅有单一 snapshot_at；行业归属变动（改行业、借壳）频率低，对 60 日窗口的行业动量影响有限；spec 已将此列为可接受偏差。

**Alternatives considered**: 引入 Tushare 等外部 PIT 行业历史（违反"不新增外部数据源"的 spec 假设，留待数据湖升级）。

## R3. 日线价格未复权——本特性最大的数据风险

**Decision**: 沿用现有未复权日线（`qmt_gateway_data.py` 拉取时 `adjust="none"`，数据湖无复权因子表，`data_gap_report.md` 已记录此缺口），但在 `mainline_data.py` 中加一道**除权异常防护**：对单日收益低于 -15%（非跌停所致的价格跳空，可配置）的样本记入数据质量日志，并在产物说明中声明该局限。

**Rationale**: 复权因子不在数据湖中，本次范围不含数据补采。未复权对本策略的影响集中在分红除权日：会使动量/均线/新高信号短暂失真。多数 A 股分红率低，且 NH_252、MA_ALIGN 等多信号叠加降低了单一除权事件的误判概率，风险可接受但必须留痕。

**Alternatives considered**: 重新用 `adjust="front"` 拉全 A 日线（工作量大、依赖 QMT 环境在线，且属于数据湖升级职责，另行立项）；在信号层做除权侦测并修正（等价于自建复权，复杂且易错，否决）。

## R4. 可交易性过滤的数据接口

**Decision**: 使用 `data/universe/execution_universe.parquet`（列：datetime, vt_symbol, in_execution, execution_reason）作为 FR-006 可交易性过滤的唯一判据；"上市满 252 日"不依赖该文件的 listed_days 规则，由 `mainline_data.py` 按"该股在日线中调仓日前的 bar 数量 ≥ 252"独立计算。

**Rationale**: execution_universe 已覆盖停牌/ST/涨跌停/低流动性（实测 reason 分布：ok 439 万、listed_days 52 万、st 26 万、low_turnover 23 万、suspended 13 万、low_price 4 万），但其 listed_days 门槛与本 spec 的 252 日要求未必一致，独立计算保证 FR-006 的窗口完整性语义准确。

**Alternatives considered**: 全部自行计算（重复造轮子，与 PIT universe 模块的职责冲突）。

## R5. 基准与调仓日历

**Decision**: 调仓日 = `data/calendar/trading_dates.parquet`（SH 市场）中每个自然月的最后一个交易日；基准指数使用 `data/benchmark/index_daily.parquet` 中的 `000300.SH`（本次仅用于产物中的市场环境参考信息，不参与信号）。

**Rationale**: 交易日历覆盖 2005 起；日线实际范围 2021-03-05 ~ 2026-07-02。首个可用调仓日受 252 日窗口约束，约为 2022-03 月末起，共约 52 个调仓日。

**Alternatives considered**: 用日线数据自身的日期集合推导调仓日（在个别全市场停牌日边界上不如官方日历可靠）。

## R6. 技术栈与模块组织

**Decision**: polars 实现全部计算；代码组织为**独立自包含子包** `vnpy/alpha/research/mainline/`，不 import 任何既有研究模块（turtle_*/pit_universe/data_check/run_summary），数据校验与产物写盘在包内自带最简实现。代码风格以 Python 初学者可读为第一目标：只用"函数 + 一个 frozen dataclass 配置类"，单文件 ≤ 300 行，每个函数配中文 docstring 说明金融含义，不使用继承、装饰器、生成器等构造。

**Rationale**: 用户明确要求不复用、单独成套、简单清洁，以便后续自己阅读学习。polars 保留的原因是实用主义：项目环境无 pyarrow/pandas parquet 引擎（实测 pandas 读 parquet 直接报错），polars 是项目现有依赖、零成本读 parquet 且滚动窗口计算天然高效；换 pandas 需新增依赖且当前项目依赖解析存在问题（scipy-stubs 冲突）。

**Alternatives considered**: 复用 turtle 基础模块（代码更少但增加阅读时的跳转成本，且与用户要求相悖，否决）；pandas + pyarrow（初学者教程更多，但需新增依赖且项目依赖解析当前有冲突，否决——polars 语法同样直观，测试与 docstring 会补足学习成本）。

## R7. 量价配合因子的"中位数 0.5 计分"实现语义

**Decision**: FR-009 的"否则该项按中位数 0.5 计"实现为：近 20 日累计收益 ≤ 0 的个股，其 VOL_RATIO 百分位排名直接置为 0.5，不参与该因子的截面排名（排名仅在收益为正的个股间计算后再回填）。

**Rationale**: 若先排名再替换会让被替换个股仍占据排名位次，扭曲其余个股的百分位；先剔除再回填保证"放量上涨才加分、其余中性"的语义精确成立。

**Alternatives considered**: 原始值置 1.0（量比中性值）再统一排名（对分布形态敏感，语义不如直接置 0.5 分位干净）。

## 未解决事项

无。全部 NEEDS CLARIFICATION 已消除；R1 的默认参数调整与 R3 的未复权声明需要在实现与 spec 参数表中落实（进入 tasks）。
