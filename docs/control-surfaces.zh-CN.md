# 控制面总账

英文版：`docs/control-surfaces.md`。

这份文档是 MABW 的控制面总账，用来回答每个控制面的三个问题：

- 它记录或约束什么；
- 谁有权写它；
- 它什么时候重置、冻结或晋升。

这份文档面向 maintainer、auditor 和架构审阅者。面向写作者/业务用户的解释见 `docs/what-mabw-keeps-track-of.zh-CN.md`。

## 计数粒度

MABW 的控制面可以按不同粒度统计：

| 粒度 | 数量 | 含义 |
|---|---:|---|
| 门禁族 | 约 3 个 | 高层交付与质量门禁。 |
| 子系统 | 约 12 个 | runtime state、evidence、feedback、memory、governance、delivery 等组。 |
| 文件/表面级 | 约 28 个 | 实际执行“谁能写什么”的治理粒度。 |

本文件采用文件/表面级，因为它对应核心治理原则：

> 一个字段只能有一个写者。

## 状态标签

| 状态 | 含义 |
|---|---|
| 已实现 | 当前代码中已有该 surface，并有确定性 CLI / 测试覆盖。 |
| 计划 v0.7.2 | 已接受的设计，但尚未发布。 |
| 计划 v0.8 | 已接受的方向，延后到度量 / 推断 / 角色拓扑阶段。 |
| 投影 | 从其他 source surface 派生，不是真理源。 |

## Run 级过程控制

这些 surface 描述一次具体运行的状态。它们位于 `output/intermediate/`，可以随 workspace runtime state 被归档或重置。

| Surface | 作用 | 写者 | 状态 | 冻结 / 重置规则 |
|---|---|---|---|---|
| `runtime_manifest.json` | 记录 run identity、runtime、路径，以及 improvement snapshot 等 manifest 指针。 | Python | 已实现 | 由 runtime state 初始化和 handoff flow 重算。 |
| `runtime_manifest.json.improvement` | 记录本 run 的 ledger hash、memory projection hash、snapshot hash 和 `materialized_entry_ids`。 | Python | 已实现 | 对本 run 冻结；后续 ledger 变化不使旧 snapshot 失效。 |
| `workflow_state.json` | 当前 stage、stage status、last decision 和 next allowed decisions。 | Python，经 state commands | 已实现 | 通过 runtime state commands 更新；agent 不应手改。 |
| `event_log.jsonl` | append-only runtime/control events。 | Python | 已实现 | 只追加；记录控制决策和状态迁移。 |
| `artifact_registry.json` | 记录 workflow artifacts 的观测和基础验证状态。 | Python | 已实现 | 由 state check 和 artifact observation 更新。 |
| `orchestrator_control_switchboard.json` | 给 Orchestrator 的确定性控制建议。 | Python | 已实现 | 从当前 workspace state/config 重建。 |
| `control_selections.json` | Orchestrator 对推荐控制的 enable/defer/reject 选择记录。 | Python CLI，由 Orchestrator/human 显式选择触发 | 已实现 | selection 是记录，不是执行。 |
| `agent_handoff.md` / `agent_handoff.json` | 当前 run 的 runtime-facing contract surface。 | Python | 已实现；v0.7.1 硬化中 | handoff 时重建；只应暴露 frozen runtime context。 |
| `stage complete` / `finalize complete` transactions | stage/finalize transition 的原子完成记录。 | Orchestrator 调用的 Python CLI | 计划 v0.7.2 | 必须原子化验证 artifacts、更新 registry/state、追加 events。 |

## Run 级证据与正确性

这些 surface 将内容与证据分开。LLM 可以写内容 artifact，但控制状态由确定性工具验证和记录。

| Surface | 作用 | 写者 | 状态 | 边界 |
|---|---|---|---|---|
| `candidate_claims.json` | 从 sources 中提取的候选事实 claim。 | specialist runtime 输出，再验证 | 已实现 | 内容 artifact；本身不是最终证明。 |
| `screened_candidates.json` | 经筛选后应保留或明确排除的 claims。 | specialist runtime 输出，再验证 | 已实现 | 后续 brief generation 的 coverage anchor。 |
| `claim_ledger.json` | 下游 brief writing 和 audit 使用的 claim-level source support。 | specialist runtime 输出，再验证 | 已实现 | source/evidence surface，不是 taste memory。 |
| `quality_gate_report.json` | material-fact、freshness、target-relevance 等确定性 gate findings。 | Python | 已实现 | 可阻断不安全的 current-stage continue/finalize decision。 |
| `audit_report.json` | Auditor role 的语义审计发现。 | Auditor runtime role | 已实现 | 语义 review，不等同于 deterministic gate report。 |
| `feedback_issues.json` | 结构化 human/audit feedback issues。 | Python CLI，来自 human/audit input | 已实现 | repair 或未来 proposal 的 evidence，本身不是 guidance。 |
| `repair_plan.json` | 当前 feedback issues 的有界 repair plan。 | Python CLI | 已实现 | 不自动执行 repair。 |
| `delta_audit_report.json` | repair delta 的可选审计报告。 | Auditor/runtime 输出，再验证 | repair path 使用时已实现 | run-scoped，不是长期 memory surface。 |
| `source_appendix.md` | 从 cited sources 生成的 reader-facing source appendix。 | Python finalize | 已实现 | reader projection，不是 source evidence 本身。 |
| `provenance_graph.json` | 从现有 control files 派生的 workspace-local audit/debug projection。 | Python | 已实现投影 | 不 fetch sources、不 replay runtime、不证明语义真实。 |

## Workspace 级品味与记忆

这些 surface 跨 run 存续。它们只能通过显式 projection 和 per-run freezing 影响后续 run。

| Surface | 作用 | 写者 | 状态 | 边界 |
|---|---|---|---|---|
| `audience_profile.md` | human-editable workspace-local audience profile。 | Human / init defaults | 已实现 | 只作为 taste context；不是 source evidence 或 correctness contract。 |
| `output/intermediate/audience_profile_snapshot.md` | 当前 run 的 frozen audience context。 | Python | 已实现投影 | 中途编辑 `audience_profile.md` 只影响后续 run。 |
| `improvement/ledger.jsonl` | append-only human-governed reader guidance ledger。 | Python CLI，来自 human approval | 已实现 | 记录 governance lifecycle，不证明 runtime effect 或 output quality。 |
| `improvement/memory.md` | 从 approved materializable guidance 派生的 deterministic projection。 | Python | 已实现投影 | 从 ledger 派生，不是手写真理源。 |
| `output/intermediate/improvement_memory_snapshot.md` | 当前 run 的 frozen improvement memory。 | Python | 已实现投影 | Runtime 读取 snapshot，不读取 live `improvement/memory.md`。 |
| `improvement/intake.jsonl` | 原始 feedback intake 和 derivation links。 | Python | 计划 v0.7.2 | 无 lifecycle state；不是第二本 ledger。 |
| `improvement/candidates.jsonl` | preference/rule/fact review routes 的 candidate parking lot。 | Python validator，来自 agent/human proposals | 计划 v0.7.2/v0.7.3 | candidate 不影响 runtime，除非下游 promote 并 approve。 |
| `reference_samples/manifest.jsonl` | accepted samples 作为 taste evidence 的 manifest。 | Python / human workspace management | 计划 v0.8 | non-evidence；不得作为 source material 被扫描。 |

## Run 级偏好评估

这个未来 surface 用来衡量 approved guidance 是否在输出中体现。它不是交付 gate。

| Surface | 作用 | 写者 | 状态 | 边界 |
|---|---|---|---|---|
| `guidance_manifestation_report.json` | 观察 materialized guidance 是否被体现、被违反或不适用。 | agent/human evaluation，经 Python schema 暴露 | 计划 v0.8 | 只报告，不阻断 finalize，也不写回 ledger state。 |

## Repo 级元治理

这些 surface 属于 repository，通过版本化开发变化，不随 workspace run 变化。

| Surface | 作用 | 写者 | 状态 |
|---|---|---|---|
| `configs/orchestrator_contract.yaml` | Orchestrator authority、decisions 和 contract categories。 | Maintainers | 已实现 |
| `configs/stage_specs.yaml` | Stage order 和 stage expectations。 | Maintainers | 已实现 |
| `configs/artifact_contracts.yaml` | Expected artifact contracts。 | Maintainers | 已实现 |
| `configs/policy_packs/*.yaml` | Public-safe policy defaults 和 boundary metadata。 | Maintainers | 已实现 |
| `eval-cases/` packaged cases | Control-surface behavior 的确定性回归用例。 | Maintainers | 已实现 |
| `docs/support-matrix.md` | Public capability/status map。 | Maintainers | 已实现 |
| `docs/architecture-status.md` | 当前实现状态与 roadmap goals 的区分。 | Maintainers | 已实现 |
| `docs/red-lines-and-anti-patterns.md` | Public red lines 和 misuse patterns。 | Maintainers | 已实现 |

## 分配原则

### 1. 按质量维度分

Correctness 归 contracts、ledgers、evidence 和 gates。

Taste 归 audience 和 improvement surfaces。

Process 归 runtime state、events、registry 和 handoff。

如果一项要求可以被机器检查，不应长期只放在 memory 里。

### 2. 按写者分

Python 写 control records。

LLM/runtime roles 写 content artifacts。

Humans 写 approvals、reader guidance 和 explicit run requests。

一个字段只能有一个写者。混合写者会制造模糊权力和薄弱审计。

### 3. 按权力分

聪明的组件可以 propose，但不应直接有权生效。

有权的组件应是确定性的。

持久行为变化应经过 human。

Human-approved changes 必须留下可追踪记录。

### 4. 按作用域分

Run-scoped surfaces 位于 `output/intermediate/`，可归档/重置。

Workspace-scoped surfaces 跨 run 存续，不应被升级静默覆盖。

Repo-scoped surfaces 随 release version 冻结。

### 5. 按真理源与投影分

Ledgers 和 manifests 是 source/control records。

Memory files、snapshots、source appendices、provenance graphs 和 display states 是 projections。

Display state 尽可能现算，不存成可变真理。

## 产品翻译

面向用户，不应把这些控制面解释成文件清单。写作者看到的版本是：

```text
本期写到哪了。
每个数字哪来的。
它学到了什么。
什么在替你把关。
```

见 `docs/what-mabw-keeps-track-of.zh-CN.md`。
