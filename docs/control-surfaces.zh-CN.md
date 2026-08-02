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
| 延后至 v0.7.3+ | 已接受的方向，但不属于 v0.7.2 release。 |
| 计划 v0.8 | 已接受的方向，延后到度量 / 推断 / 角色拓扑阶段。 |
| 投影 | 从其他 source surface 派生，不是真理源。 |

## Run 级过程控制

这些 surface 描述一次具体运行的状态。它们位于 `output/intermediate/`，可以随 workspace runtime state 被归档或重置。

| Surface | 作用 | 写者 | 状态 | 冻结 / 重置规则 |
|---|---|---|---|---|
| `runtime_manifest.json` | legacy/projection run identity 与 path metadata。 | Python | SQLite 下仅为 projection/legacy | 不拥有 runtime authority；以 Store Receipt 与 relation 为准。 |
| `runtime_manifest.json.improvement` | 旧 file-ledger/memory snapshot metadata。 | 无 | 已退役 (LD2-3) | inert；Store-native successor snapshot 是独立 SQLite record。 |
| `workflow_state.json` | 当前 stage、stage status、last decision、next allowed decisions，以及当前 stage 预算耗尽时的 trajectory decision narrowing。 | Python，经 state commands | 已实现 | 通过 runtime state commands 更新；agent 不应手改。 |
| `event_log.jsonl` | append-only runtime/control events。 | Python | 已实现 | 只追加；记录控制决策、状态迁移和 trajectory narrowing。 |
| `artifact_registry.json` | 记录 workflow artifacts 的观测和基础验证状态。 | Python | 已实现 | 由 state check 和 artifact observation 更新。 |
| `orchestrator_control_switchboard.json` | 给 Orchestrator 的确定性控制建议。 | Python | 已实现 | 从当前 workspace state/config 重建。 |
| `control_selections.json` | Orchestrator 对推荐控制的 enable/defer/reject 选择记录。 | Python CLI，由 Orchestrator/human 显式选择触发 | 已实现 | selection 是记录，不是执行。 |
| `agent_handoff.md` / `agent_handoff.json` | 当前 run 的 runtime-facing contract surface。 | Python | 已实现；v0.7.1 硬化中 | handoff 时重建；只应暴露 frozen runtime context。 |
| `stage complete` / `finalize complete` transactions | stage/finalize transition 的确定性完成记录。 | Orchestrator 调用的 Python CLI | 已实现 | 验证 artifacts、更新 registry/state、追加 transaction events；不执行 stage。 |

## Run 级证据与正确性

这些 surface 将内容与证据分开。LLM 可以写内容 artifact，但控制状态由确定性工具验证和记录。

| Surface | 作用 | 写者 | 状态 | 边界 |
|---|---|---|---|---|
| `candidate_claims.json` | 从 sources 中提取的候选事实 claim。 | specialist runtime 输出，再验证 | 已实现 | 内容 artifact；本身不是最终证明。 |
| `screened_candidates.json` | 经筛选后应保留或明确排除的 claims。 | specialist runtime 输出，再验证 | 已实现 | 后续 brief generation 的 coverage anchor。 |
| `claim_ledger.json` | 下游 brief writing 和 audit 使用的 claim-level source support。 | specialist runtime 输出，再验证 | 已实现 | source/evidence surface，不是 taste memory。 |
| `gates/auditor_quality_gate_report.json`, `gates/finalize_quality_gate_report.json` | material-fact、freshness、target-relevance 等确定性 gate findings。`quality_gate_report.json` 保留为 latest/legacy projection。 | Python | 已实现 | stage-scoped reports 可阻断不安全的 auditor completion 和 finalize completion。 |
| `audit_report.json` | Auditor role 的语义审计发现。 | Auditor runtime role | 已实现 | 语义 review，不等同于 deterministic gate report。 |
| `feedback_issues.json` | 结构化 human/audit feedback issues。 | Python CLI，来自 human/audit input | 已实现 | repair 或未来 proposal 的 evidence，本身不是 guidance。 |
| `repair_plan.json` | 当前 feedback issues 的有界 repair plan。 | Python CLI | 已实现 | 不自动执行 repair。 |
| `delta_audit_report.json` | repair delta 的可选审计报告。 | Auditor/runtime 输出，再验证 | repair path 使用时已实现 | run-scoped，不是长期 memory surface。 |
| `source_appendix.md` | 已追加进交付 Markdown/DOCX 的来源附录审计/控制副本。 | Python finalize | 已实现 | reader projection copy；不是 source evidence 本身，也不是单独交付文件。 |
| `provenance_graph.json` | 从现有 control files 派生的 workspace-local audit/debug projection。 | Python | 已实现投影 | 不 fetch sources、不 replay runtime、不证明语义真实。 |

## Workspace 级品味与已批准 guidance

当前 guidance lifecycle 是 Store-native。只有显式 Human disposition、draft、
approval/status，再加上独立 successor opt-in，才能产生持久 effect；agent 与
projection 都不写权威状态。

| Surface | 作用 | 写者 | 状态 | 边界 |
|---|---|---|---|---|
| `audience_profile.md` | human-editable workspace-local audience profile。 | Human / init defaults | 已实现 | 只作为 taste context；不是 source evidence 或 correctness contract。 |
| `output/intermediate/audience_profile_snapshot.md` | 当前 run 的 frozen audience context。 | Python | 已实现投影 | 中途编辑 `audience_profile.md` 只影响后续 run。 |
| legacy `improvement/ledger.jsonl`、`improvement/memory.md`、`output/intermediate/improvement_memory_snapshot.md` | 旧 file-based ledger、projection 与 run snapshot。 | 无 | 已退役 (LD2-3) | inert；无 reader、writer、migration 或 fallback。 |
| Post-final finding disposition、guidance draft 与 guidance status records | 与一个已验证 assessment result 和历史 source run 绑定的 append-only Human review lifecycle。 | `PostFinalReviewService`，接受 strict Human request | development main 实验性 | accept/reject/defer、Human-edited text 与独立 approval/status；approval 本身不产生后续 run effect。 |
| `RunGuidanceSnapshotRecord`、selection decisions 与 selected snapshot items | successor run 对完整兼容 active-approved 集合的不可变副本。 | Core successor transaction | development main 实验性 | 与正常 successor 原子写入；exact replay 幂等，conflict/limit failure 零写入，后续 live status 不能改写。 |
| `RoleTaskEnvelope.frozen_guidance_context` | Analyst 与 Editor 共用的同一份有序不可变 snapshot context。 | RuntimeHost 从 Store snapshot bytes 投影 | development main 实验性 | 其他角色收到 `None`；当前 direction/evidence 优先，不拥有 Claim Ledger、Gate、repair、finalize、delivery 或 Core 权限。 |
| `improvement/intake.jsonl` / `improvement/candidates.jsonl` | 旧 file-memory 扩展。 | 无 | 已退役/未交付 | 无当前 promotion 或 runtime 路径。 |
| `reference_samples/manifest.jsonl` | accepted samples 作为 taste evidence 的 manifest。 | Python / human workspace management | 计划 v0.8 | non-evidence；不得作为 source material 被扫描。 |

## Run 级偏好评估

Guidance 效用与 manifestation 均为 NOT MEASURED。已退役 manifestation projection
不是 delivery Gate，successor snapshot 也不会打分或证明模型遵循了 guidance、输出得到改善。

| Surface | 作用 | 写者 | 状态 | 边界 |
|---|---|---|---|---|

## Repo 级元治理

这些 surface 属于 repository，通过版本化开发变化，不随 workspace run 变化。

| Surface | 作用 | 写者 | 状态 |
|---|---|---|---|
| `configs/orchestrator_contract.yaml` | Orchestrator authority、decisions 和 contract categories。 | Maintainers | 已实现 |
| `configs/stage_specs.yaml` | Stage order 和 stage expectations。 | Maintainers | 已实现 |
| `configs/artifact_contracts.yaml` | Expected artifact contracts。 | Maintainers | 已实现 |
| `configs/policy_packs/*.yaml` | Public-safe policy defaults 和 boundary metadata。 | Maintainers | 已实现 |
| `eval-cases/` packaged cases | Control-surface behavior 的确定性回归用例。 | Maintainers | 已退役(LD2-3);fixture 数据保留供 EF-1/EF-2 复用 |
| `docs/support-matrix.md` | Public capability/status map。 | Maintainers | 已实现 |
| `docs/architecture-status.md` | 当前实现状态与 roadmap goals 的区分。 | Maintainers | 已实现 |
| `docs/red-lines-and-anti-patterns.md` | Public red lines 和 misuse patterns。 | Maintainers | 已实现 |

## 历史 v0.11.0 冻结清单

本表仅保留 v0.11 的历史规划记录，不代表当前 capability 或 compatibility；
当前事实以上方 Store-native 与已退役条目为准。在原规划中，“冻结”意味着 schema
或命令族将获得向后兼容承诺，并由 CI 看守。

| Surface | v0.11.0 冻结前提 |
|---|---|
| `event_log.jsonl` schema 和 event types | v0.7.2 completion transaction events 和 trajectory narrowing events 必须在冻结前保持稳定。 |
| `workflow_state.json` 和 decision vocabulary | `stage-complete` / `finalize-complete` 语义已纳入；topology-satisfied stages 会作为显式 workflow / event 记录，而不是隐藏跳过。 |
| `runtime_manifest.json` | 历史 JSON projection 规划；它不是当前 Store authority，旧 `improvement` / `recipe` 字段也不是 compatibility surface。 |
| `artifact_registry.json` | default / strict topology 下 artifact 名称保持稳定：Scout 可以满足 Screener，但 `candidate_claims.json` 和 `screened_candidates.json` 仍是两个独立 artifacts。 |
| `stage_specs.yaml` / stage order | 已实现 topology satisfaction：default topology 可由 Scout 满足 Screener；strict topology 仍保留独立 Screener。 |
| `artifact_contracts.yaml` | artifact contract 集合跨 topology 模式保持不变；candidate/screened coverage anchor 仍是显式迁移前的不变量。 |
| `orchestrator_contract.yaml` | Completion transaction 语义必须进入冻结的 decision table。 |
| Gate report schema 和 gate ids | Reader-final / process-residue gates 以及 coverage-side gates 需先稳定。 |
| Policy pack schema | 至少需要第二个 pack 证明泛化能力。Pack 内容不冻结，它是调参层。 |
| `feedback_issues.json` / `repair_plan.json` | repair path 回归覆盖稳定后可进入冻结候选。 |
| Improvement Ledger schema | 已随 legacy file-state stack 退役；无冻结或兼容承诺。 |
| `origin_runtime` | 已实现为 audit/rendering metadata；不参与 filtering、routing 或 materialization。 |
| `improvement/intake.jsonl` / `improvement/candidates.jsonl` | 已退役的 legacy 规划面；无当前 reader、writer 或冻结承诺。 |
| `improvement/memory.md` / improvement snapshot 渲染 | 已退役的 legacy file surface；Store-native successor snapshot 是独立的 fresh-schema-only 记录。 |
| Runtime handoff 格式 | 最终 usage rules 和 v0.8 precedence table 定稿后再冻结。 |
| 五动词 writer entrypoint 与核心 CLI families | 仅为历史规划；`improve` 命令族已退役，不属于当前 CLI compatibility 承诺。 |
| Eval-case schema 和 runner actions | 需覆盖最终 v0.7.2 control actions 和 v0.8 evaluation-only surfaces。 |
| `audience_profile.md` 格式 | 格式可冻结；profile 内容由人编辑，永不冻结。 |
| Reference sample manifest | 计划 v0.8；至少一个真实使用周期前保持 experimental。 |
| Manifestation report | 已退役的 diagnostic projection；无当前 reader、writer 或 runtime authority。 |
| Mode registry / role topology | 已实现 supported default / strict role-topology contract。default topology 只有在 candidate 和 screened artifacts 都存在时，才允许 Scout 满足 Screener；strict topology 保留独立 Screener。这是 workflow shape control，不是速度或输出质量声明。 |
| Support matrix | 它定义冻结承诺范围；每个冻结 surface 都必须同步更新。 |

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
