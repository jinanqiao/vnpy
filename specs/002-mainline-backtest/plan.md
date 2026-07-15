# Implementation Plan: 主线强势股策略回测

**Branch**: `002-mainline-backtest` | **Date**: 2026-07-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-mainline-backtest/spec.md`

## Summary

把 001 产出的月度信号清单（`selection.parquet`）变成可度量的历史业绩：先升级数据湖（从阿里云 QMT 重新下载全 A 后复权日线，消除分红除权对收益的扭曲），再实现一个逐日事件推进的组合回测——次一交易日开盘价成交、全部入选个股等权、显式成本模型（佣金/印花税/滑点）、停牌延迟卖出与放弃买入全记录——产出净值曲线、核心指标、沪深300 对比和图文报告。

**延续 001 的代码风格约定（用户已确认的可读性优先）**：回测独立成子包 `vnpy/alpha/research/mainline_backtest/`，与信号子包 `mainline/` 零代码耦合（只通过 `selection.parquet` 文件契约衔接），扁平函数 + 单一配置类 + 中文 docstring。

## Technical Context

**Language/Version**: Python 3.12（项目现行环境）

**Primary Dependencies**: polars（现有依赖）+ 标准库；数据下载脚本复用现有 `vnpy/alpha/research/qmt_gateway_data.py`（数据湖基础设施，非策略代码，不违反隔离约定）

**Storage**: 输入为 `data/` 数据湖（新增后复权日线文件 `data/normalized/daily_bars_all_a_adjusted.parquet`）+ 001 的 `selection.parquet`；输出为 `outputs/mainline_backtest/<run_id>/` 产物文件，无数据库

**Testing**: pytest（`tests/alpha/research/` 现有约定 + `scripts/validate_alpha.py` 门禁）

**Target Platform**: 本地 macOS/Linux 研究环境，命令行运行

**Project Type**: 单体研究库内的新模块组（library + 2 个 CLI 薄入口）

**Performance Goals**: 全历史回测（50+ 调仓期 × 每期 ≤ 30 只持仓 × 约 1300 交易日估值）≤ 5 分钟；数据下载全 A 约 5200 只受网关吞吐限制，允许分批与断点续传

**Constraints**: 零未来函数（信号日次一交易日才执行）；同输入结果逐字节可复现；每笔交易可追溯；可读性约束与 001 相同（单文件 ≤ 300 行、函数 + frozen dataclass、无继承/装饰器/生成器、中文 docstring）

**Scale/Scope**: 1 个自包含子包（约 7 个源文件）+ 2 个脚本 + 3~4 个测试文件；1 个数据湖新文件

**已知风险（Phase 0 实测，已解决）**: 阿里云公网 IP 已更换为 `8.141.119.179`（用户确认），新 IP 健康检查与后复权行情拉取均验证通过，`qmt_gateway_data.py` 默认 IP 已更新。残余风险：IP 未来可能再变（用 `QMT_GATEWAY_URL` 环境变量覆盖）；网关冷启动时首个请求可能超时（下载 CLI 内置重试）。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` 仍为未批准的模板，无强制关卡。沿用 001 的项目事实约定：

- [x] 回测代码自包含于独立子包，不 import `mainline/` 及 turtle 家族任何模块（文件契约衔接）
- [x] 不新增第三方依赖（polars + requests 均为现有依赖）
- [x] 新模块随附 pytest 测试，纳入 `scripts/validate_alpha.py` 门禁
- [x] 数据湖既有文件只读；新增文件写入 `data/normalized/`，不覆盖未复权数据
- [x] 可读性约束落实（见 Technical Context Constraints）

Phase 1 设计后复查：无违规，无需 Complexity Tracking。

## Project Structure

### Documentation (this feature)

```text
specs/002-mainline-backtest/
├── plan.md              # 本文件
├── research.md          # Phase 0 输出（已生成）
├── data-model.md        # Phase 1 输出（已生成）
├── quickstart.md        # Phase 1 输出（已生成）
├── contracts/
│   ├── cli-contract.md        # 两个脚本入口契约（下载 + 回测）
│   └── artifacts-schema.md    # 回测产物 schema 契约
└── tasks.md             # Phase 2 输出（/speckit-tasks 生成）
```

### Source Code (repository root)

```text
vnpy/alpha/research/mainline_backtest/   # 自包含子包：与 mainline/ 零代码耦合
├── __init__.py                # 阅读指南 + 导出 BacktestConfig 与 run_mainline_backtest
├── config.py                  # BacktestConfig（唯一的类）：资金/成本/执行/路径全部参数（FR-013）
├── data_loader.py             # 读 selection + 后复权行情 + 基准 + 执行池 + 日历；新旧行情一致性校验（FR-011a）
├── execution.py               # 调仓模拟：目标等权持仓 → 订单 → 成交/延迟/放弃 + 成本分项（FR-002~006）
├── portfolio.py               # 账户记账与逐日估值：持仓、现金、净值、长期停牌冻结（FR-007、SC-002）
├── metrics.py                 # 指标：年化/回撤/夏普/月度胜率/换手/成本拖累 + 分年度（FR-008~010）
└── pipeline.py                # 编排：逐调仓期推进 + 逐日估值 + 写全部产物（FR-012）

scripts/
├── download_adjusted_bars.py  # 数据湖升级 CLI：全 A 后复权日线下载 + 校验（US4，复用 qmt_gateway_data）
└── run_mainline_backtest.py   # 回测 CLI 薄入口（契约见 contracts/cli-contract.md）

data/normalized/
└── daily_bars_all_a_adjusted.parquet   # 新增：全 A 后复权日线（schema 同现有未复权文件）

outputs/mainline_backtest/<run_id>/     # 回测产物（schema 见 contracts/artifacts-schema.md）

tests/alpha/research/
├── test_backtest_execution.py  # 交易模拟：成交/延迟/放弃、成本分项、整手取整
├── test_backtest_portfolio.py  # 估值记账：净值复算、空仓期、停牌估值冻结
└── test_backtest_pipeline.py   # 端到端：合成数据全流程、产物契约、可复现性；含下载校验函数的离线测试
```

**Structure Decision**: 回测独立成 `mainline_backtest/` 子包而不是塞进 `mainline/`，理由：(1) 001 包已按"5 个文件的阅读顺序"定稿，追加 7 个文件会破坏其自包含叙事；(2) 两者唯一的衔接点是 `selection.parquet` 文件（FR-001 明确回测不得重算信号），文件契约比代码 import 更松耦合、更符合"信号与回测可独立演进"的边界；(3) 下载脚本归属数据湖基础设施，复用现有 `qmt_gateway_data.py` 网关封装（与 turtle 数据下载脚本同一模式），不属于策略代码隔离范围。阅读入口是 `pipeline.py` 的 `run_mainline_backtest()`：逐调仓期"算目标持仓 → 执行交易 → 逐日估值到下一期"，一条直线推进。

## Complexity Tracking

无 Constitution 违规，本节为空。
