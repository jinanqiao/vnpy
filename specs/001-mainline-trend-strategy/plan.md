# Implementation Plan: 主线强势股选股信号（行业筛选 + 个股打分）

**Branch**: `001-mainline-trend-strategy` | **Date**: 2026-07-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-mainline-trend-strategy/spec.md`

## Summary

实现一条月度信号生成流水线：给定月末调仓日，先以"行业动量（60/20 双窗口）+ 上涨广度"选出主线行业，再在主线行业内执行硬过滤（均线多头排列、乖离率、距新高、可交易性）与三因子打分（相对强度 40% + 距 52 周新高 35% + 量价配合 25%），产出"主线行业 + 入选个股"清单及全部中间留档。

**技术上独立成套（用户明确要求）**：不 import 复用 `turtle_*` / `pit_universe` / `data_check` / `run_summary` 等任何既有研究模块，只与它们共享同一个数据湖（只读）。代码以 **Python 初学者可读** 为第一设计目标：自包含子包、扁平函数、单一配置类、中文 docstring 讲清每个函数的金融含义，不追求工程复用。

## Technical Context

**Language/Version**: Python 3.12（项目现行环境，pyproject 管理）

**Primary Dependencies**: polars（项目现有依赖，parquet 原生读取，不引入 pandas/pyarrow 新依赖；代码上不 import 任何既有研究模块）；标准库 dataclasses/argparse/json

**Storage**: 输入为 `data/` 下现有 parquet 数据湖（只读）；输出为 `outputs/mainline/<run_id>/` 下的 parquet + JSON + Markdown 文件，无数据库

**Testing**: pytest（沿用 `tests/alpha/research/` 现有约定与 `scripts/validate_alpha.py` 质量门禁）

**Target Platform**: 本地 macOS/Linux 研究环境，命令行运行

**Project Type**: 单体研究库内的新模块组（library + CLI 薄封装）

**Performance Goals**: 全历史（约 2021-03 ~ 2026-07，5200 只股票 × 约 1300 交易日）逐月信号一次性生成 ≤ 5 分钟；单调仓日 ≤ 10 秒

**Constraints**: 信号计算零未来函数（全部窗口右端点 = 调仓日）；相同输入输出可复现（无随机性、固定排序）；中间结果全留档；**可读性约束**：单文件 ≤ 300 行、只使用"函数 + 一个 frozen dataclass 配置类"两种构造（不用继承、装饰器、生成器、运算符重载等初学者不友好的语法）、每个函数配中文 docstring 说明输入输出和金融含义

**Scale/Scope**: 约 52 个月度调仓日 × 11 个 GICS 一级行业 × 约 5200 只股票；新增 1 个自包含子包（5 个源文件）+ 1 个脚本 + 3 个测试文件

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` 仍为未批准的模板（全部为占位符，无已生效原则），无强制性关卡。采用项目事实约定作为替代检查：

- [x] 新代码自包含于独立子包，不修改、不 import 任何既有研究模块（用户要求的隔离性）
- [x] 不新增第三方依赖（仅 polars，已在项目依赖中）
- [x] 新模块随附 pytest 测试，纳入 `scripts/validate_alpha.py` 门禁
- [x] 数据湖只读，不修改既有数据文件与既有模块行为
- [x] 可读性约束落实（见 Technical Context Constraints）

Phase 1 设计后复查：无违规，无需 Complexity Tracking。

## Project Structure

### Documentation (this feature)

```text
specs/001-mainline-trend-strategy/
├── plan.md              # 本文件
├── research.md          # Phase 0 输出（已生成）
├── data-model.md        # Phase 1 输出（已生成）
├── quickstart.md        # Phase 1 输出（已生成）
├── contracts/
│   ├── cli-contract.md        # 脚本入口契约
│   └── artifacts-schema.md    # 输出产物 schema 契约
└── tasks.md             # Phase 2 输出（/speckit-tasks 生成，本命令不创建）
```

### Source Code (repository root)

```text
vnpy/alpha/research/mainline/   # 自包含子包：零依赖于兄弟模块，可整体拷走单独阅读
├── __init__.py                 # 只导出 run_mainline_signals 与 MainlineConfig
├── config.py                   # MainlineConfig（唯一的类）：全部参数 + 默认值（FR-013）
├── data_loader.py              # 读数据湖 4 张表、字段校验、除权异常侦测（自带实现，不复用 data_check）
├── industry.py                 # 行业层：行业净值、IND_MOM_60/20、IND_BREADTH_60、主线筛选（FR-001~003）
├── stocks.py                   # 个股层：MA_ALIGN/BIAS_20/NH_252 过滤 + RS_60/NH_252/VOL_RATIO 打分（FR-004~011）
└── pipeline.py                 # 编排：逐调仓日执行两层漏斗，写全部产物与参数快照（FR-012~014）

scripts/
└── run_mainline_signals.py     # CLI 薄入口（契约见 contracts/cli-contract.md）

outputs/mainline/<run_id>/      # 运行产物（schema 见 contracts/artifacts-schema.md）

tests/alpha/research/
├── test_mainline_industry.py   # 行业动量/广度/双窗口+广度筛选规则
├── test_mainline_stock.py      # 过滤条款逐条测试 + 打分合成复算
└── test_mainline_pipeline.py   # 端到端：合成小数据集跑单调仓日，验证留档完整性与可复现性
```

**Structure Decision**: 独立子包 `vnpy/alpha/research/mainline/`，与 `turtle_*` 家族并列但**零代码耦合**——不 import 任何兄弟模块，数据校验、产物写盘等基础能力在包内自带最简实现。文件按"配置 → 数据 → 行业层 → 个股层 → 编排"的阅读顺序组织，与 spec 的两层漏斗一一对应；行业层与个股层可分别测试（US1/US2 独立可测）。阅读入口是 `pipeline.py` 的 `run_mainline_signals()`，从头到尾一条直线，无回调、无多态。

## Complexity Tracking

无 Constitution 违规，本节为空。
