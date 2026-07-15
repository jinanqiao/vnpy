# Codex 项目指引

开始任务前阅读并遵守 `CLAUDE.md`，其中定义了本仓库主线量化研究的代码、
可复现性、因子检验和测试约束。

本仓库已启用 Spec-Driven Development。涉及新功能、较大改动或用户明确要求
SDD 时，使用 `sdd-workflow` skill，依次完成规格、澄清、计划、任务、分析、
实现、验收和收敛。

SpecKit 工件位于 `.specify/` 和 `specs/`。不要将 `.codex/skills` 视为 Codex
的项目级发现目录；仓库级 Codex skills 放在 `.agents/skills/`。
