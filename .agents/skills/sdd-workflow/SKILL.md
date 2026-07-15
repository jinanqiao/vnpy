---
name: sdd-workflow
description: 在本仓库按 Spec-Driven Development（规格驱动开发）推进功能：先创建并确认规格，再设计、拆分任务、实现与验收。用户提出新功能、要求按 SDD/SpecKit 推进，或要求生成规格、计划、任务时使用。
---

# SDD 工作流

本仓库已初始化 SpecKit，所有工作流产物以 `.specify/`、`specs/` 和仓库根目录
`CLAUDE.md` 为准。开始前先阅读 `CLAUDE.md`，它定义量化研究模块的强制约束。

## 何时使用

- 用户要求“按 SDD / 规格驱动开发”推进新功能；
- 用户需要规格、设计计划、任务拆分或一致性检查；
- 一项改动范围较大、需求尚未明确，不能直接安全实现。

简单且边界清楚的修复不必强制创建新规格。

## 标准顺序

1. **规格**：读取 `.specify/templates/spec-template.md`，在 `specs/` 创建下一个
   有序编号的功能目录及 `spec.md`。描述“做什么、为什么、怎么验收”，避免写实现细节。
   同时创建 `checklists/requirements.md`，检查规格是否完整、可测且无歧义。
2. **澄清**：只对会影响范围、数据、风险或验收方式的问题向用户提问；最多 5 个。
   将已确认答案写回 `spec.md` 的 Clarifications 区域。
3. **计划**：基于 `.specify/templates/plan-template.md` 创建 `plan.md`，
   并产出需要的 `research.md`、`data-model.md`、`contracts/`、`quickstart.md`。
   计划必须逐项检查并遵守 `CLAUDE.md` 的约束。
4. **任务**：基于 `.specify/templates/tasks-template.md` 创建 `tasks.md`。
   每个任务格式为 `- [ ] T001 [可选 P] [可选 US#] 动作及文件路径`；
   按依赖顺序分阶段，先测试后实现，并使每个用户故事可以独立验收。
5. **分析**：实现前交叉检查 `spec.md`、`plan.md`、`tasks.md`，
   报告需求覆盖缺口、冲突、含糊项与违背项目约束的问题。分析阶段不得改文件。
6. **实现与验收**：严格按 `tasks.md` 分阶段实施；完成一项才标记 `[X]`。
   运行计划规定的测试和项目必要检查。完成后将实验数字和验收结果写入相应规格文档。
7. **收敛**：将现有代码与规格、计划、任务对照；只把剩余工作追加为新的任务，
   不得重写既有任务或擅自扩大范围。

## 本仓库的额外质量门

- 涉及 `vnpy/alpha/research/mainline*` 的新因子，必须先完成离线 Rank IC 与分层检验；
  t 值不显著不得接入策略。
- 新功能必须默认关闭，并增加关闭时逐字节回归测试。
- 数据处理用 polars；涉及浮点聚合时先显式排序，再
  `group_by(maintain_order=True)`，保证逐字节可复现。
- 每个完成的特性须在对应 `specs/00X-*` 中记录 spec 和验收结果；
  因子检验报告写入 `outputs/factor_checks/`。
- 新 Python 文件须加入 `scripts/validate_alpha.py` 的 `PY_COMPILE_TARGETS`，
  并执行 `python3 -m pytest tests/alpha/research/ -q`。

## 现有工具

优先复用 `.specify/scripts/bash/` 中的 `check-prerequisites.sh`、
`setup-plan.sh` 和 `setup-tasks.sh` 来定位当前功能和生成文件。
使用它们前先查看 `--help` 或现有调用方式，避免猜测参数。
