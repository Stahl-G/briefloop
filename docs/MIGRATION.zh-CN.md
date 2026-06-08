# 迁移说明

本页说明公开架构如何从旧 Python-pipeline 叙事迁移到当前 Orchestrator-first 叙事。

| 旧叙事 | 当前叙事 |
|---|---|
| Python 拥有完整 brief workflow | Runtime main agent 协调 delegated subagents |
| `prepare` 是主要生成路径 | `run` 是 runtime handoff launcher |
| Python class 充当 workflow agent | 外部 runtime role 充当 subagent |
| 只靠 prompt 控制流程 | 通过 contract-governed handoff 和 validation 控制 |
| 质量只是后期编辑问题 | 质量进入 evaluation 和 feedback loops |
| private feedback 混入 context | feedback 被治理，并与 evidence 分离 |

## 迁移规则

- 不要恢复 Python full-pipeline 作为标准生成路径。
- 不要把 roadmap 目标当成已实现模块。
- validator 或 audit check 应该执行的硬约束，不要塞进 user notes。
- runtime-specific adapter 不应改变公开 artifact expectations。
