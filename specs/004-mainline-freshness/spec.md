# Feature Specification: 主线新鲜度规则

**Feature Branch**: `004-mainline-freshness`
**Created**: 2026-07-09
**Status**: Implemented & Accepted (2026-07-09)
**Input**: 用户确认的实证发现：主线行业连续入选第 1 个月的个股平均下期收益 +1.67%，第 2 个月起转负（-1.09%）；粗略反事实显示"只买新晋主线"可把同信号累计收益从 -14.9% 提升到 +29.3%（零成本口径）。用户决定："想做，搞吧"。

## 背景与动机

001 的信号用 60 日动量锁定主线行业，天生偏爱"已经涨了两三个月"的行业；行业连续入选到第 2、3 个月时动量已衰竭，继续按当期信号建仓等于吃趋势尾巴。本特性给信号层增加"主线月龄"标注和可选的新鲜度过滤：只有连续入选第 1 个月的新晋主线才建仓，老主线不再买入。

数据窥探声明：该规则源于对同一段样本的事后分析，正式结论必须经真实回测引擎（002+003）对照实验确认，并在报告中声明样本内属性。

## User Scenarios & Testing

### User Story 1 - 主线月龄标注（Priority: P1）

研究者希望每期信号都标注每个主线行业"连续入选到第几个月"，以便审计新鲜度过滤的每一次生效。

**Why this priority**: 月龄是过滤的前提，且单独就有观察价值（不开过滤也能看到主线老化过程）。

**Independent Test**: 合成数据构造"入选-连任-中断-再入选"序列，验证月龄计数与重置。

**Acceptance Scenarios**:

1. Given 行业 A 连续三个信号月入选主线, When 生成信号, Then 三期的 industry_streak 分别为 1、2、3
2. Given 行业 A 入选一期后中断一期再入选, When 生成信号, Then 再入选期 streak 重置为 1
3. Given 某个信号月所有行业都不达标（无主线月）, When 下月行业 A 再达标, Then streak = 1（无主线月打断连任）
4. Given 月龄标注功能上线且过滤关闭, When 用旧参数重跑, Then selection 的原有列内容与 001 现状逐行一致（新增 industry_streak 列不改变既有列取值与行集合）

### User Story 2 - 新鲜度过滤开关（Priority: P1)

研究者希望用一个配置开关（默认关闭）启用"只买新晋主线"：连续入选第 2 个月及以上的行业整体不建仓。

**Why this priority**: 本特性的核心动作，直接对应实证发现。

**Independent Test**: 合成数据行业连任两期，开关打开时第二期该行业个股不出现在入选清单。

**Acceptance Scenarios**:

1. Given 过滤开启（max_industry_streak=1）且行业 A 连任第 2 个月, When 生成信号, Then A 行业的个股不入选，且被过滤的行业与月龄记录在运行摘要中
2. Given 过滤开启且当期全部主线都是老主线, When 生成信号, Then 该期入选清单为空（等同无主线月，与 003 无信号清仓协同）
3. Given 过滤关闭（max_industry_streak=0，默认）, When 生成信号, Then 行为与 001 现状一致
4. Given 月龄计数, When 过滤开启, Then 月龄按"原始主线口径"（三道门槛 + TopN，不含新鲜度过滤）计数，不受过滤影响（行业连任第 3 个月仍是 streak=3，不因前两月被过滤未建仓而重置）

### User Story 3 - 新鲜度对照实验（Priority: P2）

研究者希望一键对比"现状信号 vs 新鲜度过滤信号"在推荐风控配置（择时+无信号清仓）下的真实回测差异。

**Why this priority**: 把粗略反事实升级为含成本、含执行约束的正式结论。

**Independent Test**: 合成数据跑通对照脚本，产出对比报告。

**Acceptance Scenarios**:

1. Given 同一数据湖, When 运行对照脚本, Then 产出 baseline（不过滤）与 fresh（只买新晋）两组信号 + 各自回测产物 + comparison.md（含核心指标对照与空仓月对比）
2. Given 对照报告, Then 明确标注"规则源于样本内分析"的声明

## Requirements

### Functional Requirements

- **FR-001**: 信号层 MUST 为每期每个入选主线行业计算 industry_streak（连续入选月数）：上一个信号评估月同为原始主线（三道门槛 + TopN，不含新鲜度过滤）则 +1，否则重置为 1；月龄计数不受新鲜度过滤本身影响
- **FR-002**: industry_streak MUST 写入行业信号快照与最终入选清单（selection 新增列），审计时可逐期核对
- **FR-003**: MainlineConfig MUST 新增 max_industry_streak 参数（int，默认 0=不限制）；>0 时月龄超过该值的行业整期不建仓
- **FR-004**: 过滤生效的行业 MUST 记入数据质量日志与行业快照（reject_reason="stale"，并保留月龄），审计时可还原每次过滤
- **FR-005**: 过滤后当期无任何行业时 MUST 照常产出空清单期（口径与"无主线月"一致），下游 003 无信号清仓可正常协同
- **FR-006**: max_industry_streak=0 时 MUST 与 001 现状行为一致：入选行集合与既有列取值逐行相同（回归保护）
- **FR-007**: CLI（run_mainline_signals.py）MUST 暴露 --max-industry-streak 参数并校验非负
- **FR-008**: MUST 提供一键对照脚本：baseline 与 fresh 两组信号 → 各自通过推荐风控配置回测 → comparison.md 汇总（总收益/年化/最大回撤/夏普/换手/空仓月数 + 样本内声明）

### Key Entities

- **industry_streak**: 行业连续入选月数（≥1），selection.parquet 与 industry snapshot 新增列
- **MainlineConfig.max_industry_streak**: 新鲜度上限开关（0=关）
- **freshness 对照产物**: 两组信号目录 + 两组回测目录 + comparison.md

## Success Criteria

- **SC-001**: max_industry_streak=0 重跑全历史，selection 的既有列与 001 基线逐行一致（新增列除外）
- **SC-002**: 月龄标注通过合成数据的连任/中断/无主线月三类场景验证
- **SC-003**: 对照实验在真实数据上跑通：fresh 组相对 baseline 组的年化/回撤差异被量化并写入报告
- **SC-004**: 全部既有测试（001/002/003）无回归，新逻辑纳入 validate_alpha 门禁
- **SC-005**: 同参数两次运行产物逐字节一致（可复现性沿用既有约定）

## Assumptions

- 月龄的"上一个信号评估月"= 001 调仓日历中的前一个月末（001 每个月末都评估，即使当月无行业达标）
- 新鲜度过滤只作用于建仓（信号层输出），已持仓的老主线由 002/003 的月度轮动与风控负责退出，本特性不加持仓端规则
- 002 回测引擎无需改动：selection 新增列不影响其必需列校验
