# Implementation Plan: 主线策略风控层

**Branch**: `003-mainline-risk-control` | **Date**: 2026-07-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-mainline-risk-control/spec.md`

## Summary

在 002 回测引擎的"信号 → 执行"链路上插入三条独立可开关的风控规则——大盘择时（沪深300 跌破 60 日线则清仓空仓）、无信号月清仓（信号层选不出主线时不再死拿旧仓）、个股月中止损（较买入价回撤超阈值次日卖出）——并提供一键四组对照实验，量化每条规则对收益/回撤/换手的边际贡献。风控只减仓、默认全关、全关时与 002 结果逐字节一致。

**实现方式是 002 子包的增量扩展**（新增 `risk.py` 规则模块 + 配置字段 + 主循环 3~4 个调用点），不新建子包；溯源复用 trades.reason 列（不加列，保证字节级回归），详见 research.md R2/R8。

## Technical Context

**Language/Version**: Python 3.12（项目现行环境）

**Primary Dependencies**: polars + 标准库（零新增依赖）

**Storage**: 输入不变（002 数据湖 + 001 selection）；输出沿用 `outputs/mainline_backtest/<run_id>/` 六件套，新增对照实验汇总目录（comparison.md）

**Testing**: pytest（新增 `tests/alpha/research/test_backtest_risk.py`，纳入 `scripts/validate_alpha.py` 门禁）

**Target Platform**: 本地 macOS/Linux 研究环境，命令行运行

**Project Type**: 既有研究子包的增量模块 + 1 个新 CLI 脚本

**Performance Goals**: 单次全历史回测仍 ≤ 5 分钟（002 实测 3 秒，风控逐日扫描持仓 ≤ 30 只，开销可忽略）；四组对照 ≤ 5 分钟

**Constraints**: 零未来函数（择时用执行日前一日收盘、止损收盘触发次日执行）；全开关关闭时 nav/positions/trades 与 002 逐字节一致（回归保护）；可读性约束沿用（单文件 ≤ 300 行、函数 + frozen dataclass、中文 docstring）；`pipeline.py` 现 295 行，需先把 `_build_periods()` 迁往 `data_loader.py` 腾空间（research R8）

**Scale/Scope**: 1 个新源文件（risk.py）+ 2 个既有文件扩展（config/pipeline）+ 1 个新脚本 + 1 个新测试文件；约 400 行增量

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` 仍为未批准模板，沿用 001/002 的项目事实约定：

- [x] 不 import `mainline/` 与 turtle 家族模块（风控只依赖回测子包内部与 selection 文件契约）
- [x] 零新增第三方依赖
- [x] 新逻辑随附 pytest 测试并纳入 `scripts/validate_alpha.py` 门禁
- [x] 数据湖只读；产物只写 `outputs/`
- [x] 可读性约束落实（见 Technical Context Constraints）
- [x] 002 既有产物契约与八条不变式全部保持（增量不变式 9~13 见 contracts/artifacts-schema.md）

Phase 1 设计后复查：无违规，无需 Complexity Tracking。

## Project Structure

### Documentation (this feature)

```text
specs/003-mainline-risk-control/
├── plan.md              # 本文件
├── research.md          # Phase 0 输出（已生成，R1~R8 八项决策）
├── data-model.md        # Phase 1 输出（已生成，配置/枚举/日志/对照表增量）
├── quickstart.md        # Phase 1 输出（已生成，5 个验证场景）
├── contracts/
│   ├── cli-contract.md        # 回测 CLI 参数扩展 + 对照实验脚本契约
│   └── artifacts-schema.md    # 不变式 9~13 + comparison.md 契约
└── tasks.md             # Phase 2 输出（/speckit-tasks 生成）
```

### Source Code (repository root)

```text
vnpy/alpha/research/mainline_backtest/
├── config.py            # [扩展] 新增 5 个风控字段 + 非法值校验（data-model §1）
├── data_loader.py       # [扩展] 接收 pipeline 迁入的 _build_periods（腾行数）+ 真实月末计算复用
├── risk.py              # [新增] 三条规则的纯函数：均线预计算/择时判定/无信号月末/止损扫描（research R1/R4/R5）
├── pipeline.py          # [扩展] 主循环插入风控调用点：择时跳买+清仓、无信号清仓日、止损队列（research R3）
├── report.py            # [扩展] report.md 增加"风控动作汇总"段
└── __init__.py          # [扩展] 阅读指南补 risk.py（第 8 个文件）

scripts/
├── run_mainline_backtest.py   # [扩展] 5 个新参数（contracts/cli-contract.md §1）
└── run_risk_experiments.py    # [新增] 四组对照一键跑 + comparison.md（contracts/cli-contract.md §2）

tests/alpha/research/
├── test_backtest_risk.py      # [新增] 三条规则单测 + 回归保护 + 对照实验端到端（合成数据）
└── backtest_fixtures.py       # [扩展] 基准走势可控注入（跌破/站上均线的场景构造）
```

**Structure Decision**: 风控作为回测子包的内部模块而非独立子包：三条规则深度耦合回测主循环的持仓状态与交易执行路径（清仓复用卖出机制、止损复用 pending_sells 顺延），拆包只会制造人为接口。规则本体（何时触发）集中在 `risk.py` 纯函数中可独立单测，编排（触发后做什么）留在 `pipeline.py` 主循环，保持"从上到下一条线"的可读性。溯源不加列、默认全关，把 002 的行为锁为回归基线。

## Complexity Tracking

无 Constitution 违规，本节为空。
