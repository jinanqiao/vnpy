# Implementation Plan: 主线新鲜度规则

**Branch**: `004-mainline-freshness` | **Date**: 2026-07-09 | **Spec**: specs/004-mainline-freshness/spec.md

## Summary

给 001 信号层加"主线月龄"标注与可选的新鲜度过滤（只买连续入选第 1 个月的新晋主线），并提供 baseline vs fresh 的一键对照回测实验。002 回测引擎零改动。

## Technical Context

- **语言/依赖**: Python 3.10+，polars（沿用 001/002/003 栈，不新增依赖）
- **改动面**:
  - `vnpy/alpha/research/mainline/config.py`: +max_industry_streak 字段与校验
  - `vnpy/alpha/research/mainline/industry.py`: +industry_streak 列、apply_freshness() 纯函数
  - `vnpy/alpha/research/mainline/pipeline.py`: 循环内维护月龄状态、selection join 月龄列、报告与质量日志扩展
  - `scripts/run_mainline_signals.py`: +--max-industry-streak
  - `scripts/run_freshness_experiments.py`: 新增对照实验脚本
  - `tests/alpha/research/test_mainline_freshness.py`: 新增测试
  - `scripts/validate_alpha.py`: 门禁纳入新文件
- **不改动**: stocks.py、mainline_backtest 子包全部文件
- **约束**: 单文件 ≤ 300 行；不用类（除既有 dataclass）；中文 docstring；同参数逐字节可复现

## Constitution Check

项目 constitution 为空，沿用既有工程约定（TDD、可复现、beginner-friendly 代码风格）。PASS。

## Project Structure

```
specs/004-mainline-freshness/
├── spec.md / plan.md / research.md / data-model.md / quickstart.md
├── checklists/requirements.md
└── contracts/{cli-contract.md, artifacts-schema.md}
```

## 实现要点（对照 research.md）

1. **月龄状态机在 pipeline 循环里**（R1/R2）：`prev_streak` 字典逐期推进；无主线月清空；窗口不足期不推进。
2. **月龄与过滤解耦**（R3）：先按原始主线口径算月龄，再做 stale 过滤；被过滤行业 selected=False + reject_reason="stale"，月龄保留。
3. **selection 带月龄列**（R4）：与行业快照 join，stocks.py 不动。
4. **对照实验站在 003 最优风控上**（R5）：两组信号 → 各自 timing+no_signal 回测 → comparison.md。
5. **样本内声明**（R7）：LIMITATIONS_NOTE 与 comparison.md 固定携带。

## 已知风险

- 行数约束：industry.py 现 208 行 + apply_freshness 约 40 行，仍在 300 内；pipeline.py 现 205 行 + 约 40 行，仍在 300 内。超限时把报告函数拆到独立文件。
- 回归保护依赖磁盘上的 004 前基线运行产物做逐行对比；若缺失则以既有 pipeline 测试 + 不变量 I-18 的合成数据测试兜底。
