# MABW Agent 开发指南

本文是 coding agent 和 contributor 快速理解 MABW 源码仓库的开发入口。它不是 end-user brief workspace 的执行合同，也不是某一次 brief run 的 handoff。真实运行时合同以 `multi-agent-brief run --workspace <workspace>` 生成的 handoff artifacts、runtime state files、role skills 和 workspace 配置为准。

## 项目目标

Multi-Agent Brief Workflow 是一个面向企业简报的 contract-governed、subagent-first、可审计、可反馈修复的工作流工具包。

它不是让一个 LLM 直接写完整报告，而是由 Orchestrator 作为 runtime main agent 控制流程，委派 specialist subagents 完成单阶段任务，并通过 runtime state、artifact registry、event log 和 contracts 管理整个过程。

核心目标：

- 让企业简报生成过程可控、可检查、可阻塞、可修复。
- 让每个阶段的输入、输出、状态和决策都能被人类和 agent 读取。
- 让错误能够被结构化为反馈、修复计划和后续改进资产。
- 支持未来跨行业、跨 runtime、跨 policy pack 的复用。

## 架构骨架

```text
Contract Registry
  ├─ configs/orchestrator_contract.yaml
  ├─ configs/stage_specs.yaml
  ├─ configs/artifact_contracts.yaml
  └─ configs/policy_packs/default.yaml
        │
        ▼
Orchestrator Control Loop
  read state → identify stage → delegate → check artifact → decide
        │
        ├─ External LLM Specialists
        │    scout / screener / claim-ledger / analyst / editor / auditor
        │
        ├─ Runtime State
        │    runtime_manifest.json
        │    workflow_state.json
        │    artifact_registry.json
        │    event_log.jsonl
        │
        └─ Python Tools / Validators / Renderer
             init / state / doctor / sources / inputs / audit / finalize
```

关键原则：

- Orchestrator 是 runtime main agent，负责流程、委派、验证和决策。
- Specialists 执行单阶段任务，不跨阶段协调。
- Python 是工具、验证器和渲染器，不是标准简报生成 runtime。
- 所有协调通过 state files、artifacts 和 Orchestrator 决策完成。

## 四类 Contracts

| Contract | Purpose | Implementation Status |
|---|---|---|
| Behavior | 定义 Orchestrator 与 specialist 角色边界 | 以当前代码、tests、support matrix 为准 |
| Process / Artifact | 定义阶段就绪、artifact 输入输出和阻塞规则 | 以当前代码、tests、support matrix 为准 |
| Fact-Grounding / Evidence | 保证重要表述可追溯到 claim / evidence | 以当前代码、tests、support matrix 为准 |
| Quality / Audience | 保证输出符合读者、场景和企业价值要求 | 以当前代码、tests、support matrix 为准 |

Contracts 不是建议，也不是普通文档。Contracts 定义 stage boundary 上的可执行规则。Roadmap 中的目标不等于已实现能力；判断当前状态时先读 `docs/architecture-status.md` 和 `docs/support-matrix.md`。

## Runtime State 与 Artifact 管理

Runtime control files 通常位于 workspace 的 `output/intermediate/`：

- `runtime_manifest.json`：记录 run id、workspace、runtime、MABW version、contract references、stage order 和 expected artifacts。
- `workflow_state.json`：记录当前 stage、stage status、blocked 状态、blocking reason、last decision 和 next allowed decisions。
- `artifact_registry.json`：记录每个 artifact 的 path、required、producer、consumers、status、validation result 和 blocking reason。
- `event_log.jsonl`：append-only 记录 runtime/control events、artifact validation、stage status changes 和 Orchestrator decisions。

Artifact blocking 必须是 stage-scoped 和 consumer-scoped。不要因为未来阶段的 required artifact 尚未产生，就全局阻塞当前 run。一个 artifact 通常只有在其 producer stage 已完成、artifact 已激活，并且它影响当前或下游 consumer stage 时，才可能成为阻塞项。

## Stage Pipeline

标准 workflow stage source of truth 是 `configs/stage_specs.yaml`。不要把本文列出的顺序当作唯一事实来源。

典型 workflow：

```text
doctor
→ source-discovery
→ input-governance
→ scout
→ screener
→ claim-ledger
→ analyst
→ editor
→ auditor
→ finalize
```

只有 Orchestrator 可以推进 stage transition。每个 transition 必须基于 contract references、runtime state、artifact validation 和合法 decision vocabulary。

## Orchestrator Decision Vocabulary

Orchestrator 的合法决策由 `configs/orchestrator_contract.yaml` 定义。当前公共控制面围绕这些决策：

```text
continue
retry_stage
delegate_repair
request_human_review
block_run
finalize
```

任何新决策必须先进入 contract source、runtime validation 和 tests。不要在 prompt、handoff 或代码里临时发明 decision。

## 开发优先级

开发 agent 修改项目时，优先遵循：

- 先读 `docs/agent-dev-guide.zh-CN.md`、`docs/architecture-status.md`、`docs/MIGRATION.md`、`docs/orchestrator-contracts.md` 和 `docs/support-matrix.md`。
- 再读 `configs/orchestrator_contract.yaml`、`configs/stage_specs.yaml`、`configs/artifact_contracts.yaml`。
- 不要先改 prompt；能用 schema、validator、state 或 tests 约束的规则，不要只写进自然语言。
- 每个 PR 尽量只实现一个 contract slice：一个 schema、一个 validator、一个 CLI surface、一个 runtime adapter update 或一组 focused tests。
- 不要在一个 PR 里同时改 Orchestrator、provenance、quality、packaging 和 agent prompts。
- 任何 self-improvement 能力必须是 proposal-only，不能自动改 main branch、不能自动放宽规则、不能删除 failing cases。

## ARS / Hermes 设计启发

MABW 可以吸收 academic research workflow 和 Hermes-style agent design 的工程模式，但不要直接复制外部项目的 prompts、skills 或专有实现。

### Checkpointed Stage Gates

Stage boundary 应区分不同 gate 类型：

- machine-only：artifact existence、schema、parseability、contract status。
- human-in-the-loop：需要用户确认方向、范围、修复意图或交付边界。
- mixed：机器先给出 findings，Orchestrator 决定是否 request human review。

这类 gate 应落到 stage specs、runtime state、event log 和 tests 中，而不是只写在 prompt 里。

### Multi-Mode Entry

同一套 specialist roles 可以支持不同入口，而不是每次都跑完整 pipeline。未来 mode registry 可以覆盖：

- full brief run。
- source readiness / source review。
- audit-only。
- repair-planning-only。
- audience-profile update。
- final-render-only。

Mode 是 Orchestrator 的调度方式，不是新的 Python pipeline。

### Anti-Patterns With Rationale

Red lines 应说明三件事：

- prohibited behavior。
- why it fails。
- correct behavior。

这样能降低后续 agent 把 contract 当作建议、把 repair 当作自动改稿、或把 runtime-specific 便捷路径写成业务 schema fork 的风险。

### Workspace Memory

MABW 的记忆层应分清三类：

- `audience_profile.md`：taste、偏好、部门风格和读者期待。
- FeedbackIssue / RepairPlan / FrictionStore：错误、修复和 recurring failure memory。
- Runtime state：一次 run 的控制状态，不是长期记忆。

借鉴 Hermes 的关键点是：memory update 应该是 agent-proposed、human-approved。一个 run 开始时应读取 frozen snapshot；同一个 run 内新提出的 memory 更新默认下次 run 才生效，避免中途改变执行标准。

## Red Lines

| Red Line | Why | Correct Behavior |
|---|---|---|
| 不恢复 Python full brief pipeline | 会让后续 agent 误以为 Python 是 workflow runtime | `run` 只做 runtime handoff，brief stages 由 external subagents 执行 |
| 不重新引入 Python fake Agent classes | 会模糊 subagent-first 架构边界 | Python 保持 tools、validators、renderers |
| 不绕过 Orchestrator 推进 stage | 会丢失 decision record 和 blocking 依据 | 所有 stage transition 记录到 runtime state / event log |
| 不把 contracts 当建议 | 会让 artifact validity 和 safety gates 失效 | contract violation 必须进入 validator、state 或 audit findings |
| 不跳过 artifact validation | 会把坏 artifact 传给 downstream stage | missing / invalid artifact 只在 stage-scoped 语义下 block 或 request review |
| 不自动执行 repair | 会把 feedback loop 变成未经授权的改稿流水线 | RepairPlan 只是 proposal，执行由 Orchestrator 委派并经 human gate 控制 |
| 不把 taste 写进 contract YAML | taste 会频繁变化，不适合机械 enforcement | taste 写入 `audience_profile.md`，由 Orchestrator 语义总结 |
| 不把 private planning 泄漏进公开文档 | 会暴露未稳定 schema、prompt 和商业策略 | 详细计划放入 ignored private planning paths |

## 常用验证

按改动范围运行 focused tests。常用命令：

```bash
python3 -m pytest -q
python3 scripts/generate_agent_configs.py --check
python3 scripts/check_version_consistency.py
python3 scripts/check_release_consistency.py --no-tag
python3 scripts/check_capabilities.py
git diff --check
```

涉及 CLI entrypoints、runtime handoff、contract references、generated assets、installers 或 package data 时，还应做 non-editable install smoke。

## 总结

MABW v0.6+ 的目标不是增加更多 agent，而是建立 Orchestrator Control Plane：

```text
一次 run 可控
artifact 可检查
错误可阻塞
反馈可结构化
修复可计划
质量可 gate
失败可沉淀
未来可跨场景复用
```

最终目标是一个配置驱动、Orchestrator 管理、artifact 可审计、feedback 可修复、memory 可沉淀、policy pack 可迁移的企业简报多 Agent 工作流框架。
