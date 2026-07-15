# Specification Quality Checklist: 主线新鲜度规则

**Purpose**: 交付 plan 之前校验规格完整性
**Created**: 2026-07-09

## Content Quality

- [x] CQ-001 无实现细节泄漏（spec 只讲行为，polars/文件组织留给 plan）
- [x] CQ-002 每条 FR 可测试（月龄计数、过滤、回归保护、CLI、实验脚本均有验收场景）
- [x] CQ-003 优先级独立可交付（US1 标注单独有价值；US2 依赖 US1；US3 依赖 US2）

## Requirement Completeness

- [x] RC-001 月龄计数口径明确（原始主线口径 = 三道门槛 + TopN，不含新鲜度过滤；无主线月打断连任）
- [x] RC-002 默认值明确（max_industry_streak=0 = 关闭 = 现状回归）
- [x] RC-003 空清单期口径与 001 现状一致，003 无信号清仓可协同
- [x] RC-004 数据窥探风险已声明（规则源于样本内分析，报告必须携带声明）
- [x] RC-005 下游兼容性确认（selection 新增列不破坏 002 必需列校验）

## Ambiguities Resolved

- [x] AR-001 过滤作用范围 = 仅建仓端（信号层），持仓退出仍由 002/003 负责
- [x] AR-002 行业快照中被过滤行业的表达 = selected=False + reject_reason="stale"，月龄保留可审计
- [x] AR-003 对照实验推荐风控配置 = 择时 + 无信号清仓（003 验收结论的最优组合）

## Notes

- 全部项通过，可进入 /speckit-plan。
