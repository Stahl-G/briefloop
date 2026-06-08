# 路线图

本路线图覆盖 v0.5.8 后的新基线。项目下一阶段不再把质量提升、provenance、FrictionStore 分散推进，而是统一收敛到一个主线：

```text
Explicit Orchestrator Contract
→ Runtime State
→ Artifact Contract
→ Evidence Provenance
→ Execution Provenance
→ Unified Provenance Graph
→ Quality And Repair Loop
→ Golden Evaluation And FrictionStore
→ Distribution And Reference Workflows
→ v1.0 Stable Baseline
```

## 核心定义

### Orchestrator 是 main agent，不是 Python pipeline

本项目的 Orchestrator 指 runtime main agent。它可以是 Hermes parent agent、Claude Code command context、Codex main agent、OpenCode primary agent，或 manual fallback 操作者。

Orchestrator 的职责是控制流程、委派子 agent、检查 artifact、记录决策、阻断失败和生成改进信号。Python 只提供工具、schema、validator、manifest、event log、renderer 和 audit harness。

```text
Orchestrator main agent
  reads contracts
  selects policy pack
  plans stages
  delegates subagents
  validates outputs
  records decisions
  blocks or repairs
  finalizes only after gates pass
```

不得把 Orchestrator 实现成新的 `BriefPipeline.run()`。不得让 Python 自动执行 Scout、Screener、Analyst、Editor、Auditor 全链路生成 brief。

### 0.6 的目标

0.6 的目标不是先提升 DOCX 文笔，而是让弱模型也能明确意识到：

```text
I am the orchestrator.
I manage stage state.
I delegate specialist agents.
I validate each artifact.
I decide continue / retry / block / human review.
I record provenance and improvement signals.
```

换句话说，0.6 要把 main agent 从“照着 prompt 顺序跑”升级为“受 harness 管理的 workflow controller”。

## 已完成基线

### v0.5.7

- `multi-agent-brief run` 已从 Python brief generator 改成 runtime handoff launcher。
- 主路径是 external subagent workflow：scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> formatter。
- Hermes 成为 primary runtime path，支持 `delegate_task`、cron、daily source cache 和 cached_package。
- Claude Code、OpenCode、Codex 平台适配器由 `configs/agent_roles.yaml` 生成。
- `inputs classify` 与 ManualProvider hard gate 已阻止 feedback / instruction / context 污染 Claim Ledger。
- deterministic audit、editorial governance、final quality、limitation hygiene 已形成质量门基础。

### v0.5.8

- 清理旧 `prepare` / Python pipeline 叙事。
- 建立 support matrix。
- 建立 `VERSION` 单一真源和 release consistency 脚本。
- 明确 clone/source install 与 CLI-only install 边界。

## 工程分层

后续 agent 修改代码时，先按这一层级定位问题：

| 层 | 目的 | 主要文件 |
|---|---|---|
| Orchestrator Contract | 定义 main agent 应如何管理 workflow | `configs/orchestrator_contract.yaml`, `.agents/skills/orchestrator/SKILL.md`, `configs/agent_roles.yaml` |
| Stage Spec | 定义 stage 依赖、输入、输出、validator、failure action | `configs/stage_specs.yaml`, `src/multi_agent_brief/orchestrator/stage_spec.py` |
| Artifact Contract | 定义 artifact schema、producer、consumer、hash、status | `configs/artifact_contracts.yaml`, `src/multi_agent_brief/artifacts/` |
| Runtime State | 记录当前 run 的状态、决策和 gate 结果 | `workflow_state.json`, `run_manifest.json`, `event_log.jsonl` |
| Evidence Provenance | 记录 source -> evidence -> claim -> draft -> final | `source_registry.json`, `evidence_pack.json`, `claim_ledger.json`, `citation_audit.json` |
| Execution Provenance | 记录 agent / tool / handoff / artifact lineage | `agent_task_log.jsonl`, `tool_call_log.jsonl`, `handoff_log.jsonl` |
| Unified Provenance Graph | 用 typed relations 连接事实链、执行链、审计和改进信号 | `provenance_graph.json`, `src/multi_agent_brief/provenance/graph.py` |
| Quality Harness | 记录 relevance、delivery、repair、rendering、eval 结果 | `relevance_report.json`, `delivery_report.json`, `quality_score.json` |
| Improvement Loop | 把失败转成受控改进项 | `friction_store.jsonl`, `improvement_signals.json`, `improvement_proposal.md` |

## v0.6.0: Explicit Orchestrator Contract

目标：把 Orchestrator 从“普通协调 agent”改成 main agent 的正式工程契约。

### 必须做

1. 新增 Orchestrator 架构文档：

```text
docs/orchestrator-architecture.zh-CN.md
docs/orchestrator-architecture.md
```

文档必须说明：

- Orchestrator 是 main agent，不是 Python pipeline。
- Orchestrator 如何读取 workspace、policy、stage spec、artifact contract。
- Orchestrator 如何委派 subagents。
- Orchestrator 可以做哪些 decision。
- Orchestrator 何时必须 block、retry、request human review。
- Python CLI 在该架构中只提供 tools 和 validators。

2. 新增 contract 配置源：

```text
configs/orchestrator_contract.yaml
configs/stage_specs.yaml
configs/artifact_contracts.yaml
configs/policy_packs/default.yaml
```

3. 重写 Orchestrator role source：

```text
configs/agent_roles.yaml
.agents/skills/orchestrator/SKILL.md
```

然后运行：

```bash
python scripts/generate_agent_configs.py
python scripts/check_agent_configs.py
```

生成文件包括：

```text
.claude/agents/orchestrator.md
.codex/agents/orchestrator.toml
.opencode/agents/brief-orchestrator.md
docs/agents/
```

4. 改写 runtime command 主路径：

```text
.claude/commands/generate-brief.md
.opencode/commands/generate-brief.md
.agents/hermes-skills/multi-agent-brief-hermes/SKILL.md
src/multi_agent_brief/hermes/adapter.py
src/multi_agent_brief/cli/start_commands.py
```

这些入口必须明确：

- 当前 main agent 是 Orchestrator。
- Scout / Screener / Claim Ledger / Analyst / Editor / Auditor / Formatter 是被 Orchestrator 调度的 subagents。
- 每个 stage 后 Orchestrator 必须验证 artifact 再进入下一步。

### Orchestrator decision schema

0.6.0 至少要在文档和 config 中定义这些 decision：

```text
continue
retry_stage
delegate_repair
request_human_review
block_run
finalize
```

每个 decision 必须包含：

```text
decision_id
stage_id
decision
reason_summary
input_artifacts
output_artifacts
validation_result
next_allowed_actions
created_at
```

### 测试

新增或更新：

```text
tests/test_orchestrator_contract_docs.py
tests/test_agent_config_generation.py
tests/test_start_commands.py
tests/test_no_python_pipeline_regression.py
```

测试重点：

- Orchestrator 角色文件包含 main agent / controller / decision / validation / block 语义。
- Orchestrator 角色文件不把自己描述成普通 pipeline stage。
- generate-brief command 不再只是线性步骤清单，必须要求 Orchestrator 验证每个 stage。
- 不允许重新出现 `BriefPipeline().run` 或 Python fake agent 主路径。

### 完成标准

- 后续 agent 打开 roadmap 后知道先改 `configs/orchestrator_contract.yaml` 和 `configs/stage_specs.yaml`。
- Hermes / Claude / Codex / OpenCode 的入口都能告诉 main agent 它是 Orchestrator。
- 角色生成检查通过。
- pytest 通过。

## v0.6.1: Runtime State And Handoff Initialization

目标：让 `multi-agent-brief run` 初始化 Orchestrator 可读的 run state，而不是只写 `agent_handoff.md/json`。

### 必须做

新增模块：

```text
src/multi_agent_brief/orchestrator/
  __init__.py
  workflow_state.py
  stage_spec.py
  decision.py
  event_log.py
  policy_loader.py
```

复用并迁移旧能力：

```text
src/multi_agent_brief/core/manifest.py
```

不要平行创造第二套 manifest。应把现有 manifest 改造成 runtime handoff manifest，而不是 pipeline manifest。

`multi-agent-brief run/start/handoff` 生成：

```text
output/intermediate/agent_handoff.md
output/intermediate/agent_handoff.json
output/intermediate/workflow_state.json
output/intermediate/run_manifest.json
output/intermediate/event_log.jsonl
```

### workflow_state.json 最小字段

```json
{
  "schema_version": "workflow_state/v1",
  "run_id": "RUN_...",
  "workspace": "...",
  "runtime": "hermes",
  "orchestrator_role": "main_agent",
  "policy_pack": "default",
  "current_stage": "doctor",
  "stages": [],
  "decisions": [],
  "blocked": false,
  "block_reason": null
}
```

### event_log.jsonl 最小事件

```text
run_initialized
handoff_written
stage_ready
artifact_expected
validation_result
orchestrator_decision
run_blocked
run_finalized
```

不要记录原始 chain-of-thought。允许记录短 `reason_summary`、tool observation summary 和 validation summary。

### CLI

新增：

```bash
multi-agent-brief validate run --workspace <workspace>
multi-agent-brief validate state --workspace <workspace>
```

`validate state` 先只检查 JSON 结构、stage id、runtime、required fields。

### 测试

```text
tests/test_workflow_state.py
tests/test_event_log.py
tests/test_validate_commands.py
tests/test_start_commands.py
```

完成标准：

- `run --workspace` 不生成 brief，但生成 Orchestrator state。
- `validate state` 可以在无 LLM 环境下运行。
- event log 至少记录 run 初始化和 handoff 写入。

## v0.6.2: Artifact Registry And Process Contract

目标：防止 fake completion。Orchestrator 不能只相信子 agent 说“完成了”，必须检查 artifact。

### 必须做

新增：

```text
src/multi_agent_brief/artifacts/
  __init__.py
  models.py
  registry.py
  validators.py
  hashing.py
```

生成：

```text
output/intermediate/artifact_registry.json
```

Artifact registry entry：

```json
{
  "artifact_id": "claim_ledger",
  "path": "output/intermediate/claim_ledger.json",
  "producer_stage": "claim_ledger",
  "consumer_stages": ["analyst", "auditor"],
  "schema_id": "claim_ledger/v1",
  "required": true,
  "status": "missing|present|valid|invalid|stale",
  "content_hash": "",
  "created_at": "",
  "validation_result": {}
}
```

### Validators

新增：

```bash
multi-agent-brief validate artifact --workspace <workspace> --artifact claim_ledger
multi-agent-brief validate stage --workspace <workspace> --stage claim_ledger
multi-agent-brief validate handoff --workspace <workspace>
multi-agent-brief validate run --workspace <workspace>
```

Validator 必须检查：

- required artifact 是否存在。
- 文件是否非空。
- JSON 是否可解析。
- schema_version 是否匹配。
- producer_stage 是否符合 stage spec。
- 上游 artifact 是否满足依赖。

### 与 Orchestrator 的关系

Orchestrator 每次子 agent 返回后必须执行：

```text
validate stage
update artifact_registry
write event_log validation_result
write orchestrator_decision
continue or block
```

### 测试

```text
tests/test_artifact_registry.py
tests/test_artifact_validators.py
tests/test_validate_commands.py
tests/test_runtime_parity_contract.py
```

完成标准：

- 缺失 `claim_ledger.json` 时 Analyst stage 不能被判定为 ready。
- 缺失 `audit_report.json` 时 finalize stage 不能被判定为 ready。
- Hermes / Claude / Codex / OpenCode 的 expected artifacts 来自同一 artifact contract。

## v0.6.3: Evidence Provenance Contract

目标：把事实可信度从“文稿里有引用”升级为 source -> evidence -> claim 的结构化链路。

### 必须做

新增或升级：

```text
source_registry.json
evidence_pack.json
claim_ledger.json
citation_audit.json
```

建议模块：

```text
src/multi_agent_brief/provenance/
  __init__.py
  source_registry.py
  evidence_pack.py
  citation_audit.py
  evidence_graph.py
```

### Claim Ledger 升级

Claim 必须逐步支持：

```text
atomic_statement
support_status
evidence_refs
contradicting_evidence_refs
source_quality_snapshot
linked_entities
usage
limitations
```

兼容旧字段 `statement`、`source_id`、`evidence_text`，不要一次性破坏现有 tests。

### Evidence unit

```json
{
  "evidence_id": "EVD_001",
  "source_id": "SRC_001",
  "locator": {"type": "paragraph", "value": "section 2"},
  "evidence_text": "short excerpt or paraphrase",
  "evidence_hash": "...",
  "extracted_by": "scout",
  "language": "en"
}
```

### Citation audit

检查：

- final cited draft 中的 `[src:CLAIM_ID]` 是否存在。
- claim 是否有 evidence_refs。
- evidence 是否能回到 source_registry。
- unsupported / partially_supported / contradicted claim 是否被过度表述。
- Editor 是否删除了 draft audit 所需引用。

### Evidence relation types

v0.6.3 要先定义事实链 relation，供 v0.6.4 unified graph 复用：

```text
DERIVED_FROM: evidence -> source
SUPPORTS: evidence -> claim
PARTIALLY_SUPPORTS: evidence -> claim
CONTRADICTS: evidence -> claim
USES_CLAIM: draft/final artifact -> claim
INVALIDATES: citation_audit finding -> claim or citation marker
```

这些 relation 不是展示用标签，必须能被 validator 使用。例如 `support_status=unsupported` 的 claim 不应有 `SUPPORTS` edge；正文中使用 `CONTRADICTS` 关系的 claim 必须带 limitation 或 uncertainty wording。

完成标准：

- 重要正文 claim 不能只靠 URL 或裸 source_id。
- claim 能追溯到 evidence，evidence 能追溯到 source。
- citation audit 失败会阻止 finalize。

## v0.6.4: Execution Provenance Contract

目标：解释哪个 agent、哪个 tool、哪个 handoff、哪个 validation 产生了当前状态。

### 必须做

新增：

```text
agent_task_log.jsonl
tool_call_log.jsonl
handoff_log.jsonl
orchestrator_report.json
provenance_graph.json
```

建议模块：

```text
src/multi_agent_brief/provenance/
  execution_log.py
  agent_task_log.py
  tool_call_log.py
  handoff_log.py
  graph.py
  relation_schema.py
```

### agent_task_log 事件

```json
{
  "event_type": "agent_task_completed",
  "run_id": "RUN_...",
  "stage_id": "scout",
  "agent_role": "scout",
  "input_artifacts": ["source_registry"],
  "output_artifacts": ["candidate_claims"],
  "status": "completed|failed|blocked",
  "summary": "short observable summary",
  "created_at": "..."
}
```

### tool_call_log 边界

不要保存敏感原始日志、API key、完整 prompt 或原始 chain-of-thought。保存：

```text
tool_name
purpose
parameters_summary
observation_summary
artifact_updates
status
```

### Unified provenance graph

v0.6.4 必须显式引入 `provenance_graph.json`，把 evidence provenance 和 execution provenance 连成同一张 typed relation 网络。

Graph node 类型：

```text
run
stage
agent_task
tool_call
artifact
source
evidence
claim
citation
audit_finding
orchestrator_decision
repair_plan
friction_item
```

Graph relation 类型：

```text
DERIVED_FROM
DEPENDS_ON
GENERATED_BY
USED_BY
VERIFIED_BY
INVALIDATED_BY
SUPPORTS
PARTIALLY_SUPPORTS
CONTRADICTS
TRIGGERED
UPDATED
BLOCKED_BY
PROPOSED_FIX_FOR
```

最小 graph schema：

```json
{
  "schema_version": "provenance_graph/v1",
  "run_id": "RUN_...",
  "nodes": [
    {"id": "CLM_001", "type": "claim", "ref": "claim_ledger.json#CLM_001"}
  ],
  "edges": [
    {"from": "EVD_001", "to": "CLM_001", "relation": "SUPPORTS"}
  ]
}
```

v0.6.4 不需要完整图查询引擎，但必须能从现有 artifact 生成 graph，并能校验 orphan node、unknown relation、broken ref。

完成标准：

- 每个 required artifact 都有 producer。
- 每个 stage 的成功、失败、阻断有可读事件。
- Orchestrator report 能说明最终 brief 通过了哪些 gate，哪些 limitation 留存。
- `provenance_graph.json` 能连接 source/evidence/claim 与 agent_task/tool_call/artifact。
- typed relation validator 能发现 unknown relation、broken ref 和缺失 producer。

## v0.6.5: Orchestrator Quality And Repair Loop

目标：把质量提升纳入 Orchestrator control loop，而不是依赖强模型一次写好。

### 必须做

新增或正式化：

```text
output/intermediate/relevance_report.json
output/intermediate/delivery_report.json
output/intermediate/repair_plan.json
```

建议模块：

```text
src/multi_agent_brief/relevance/
  schemas.py
  scorer.py
  report.py

src/multi_agent_brief/delivery_gate/
  schemas.py
  checker.py
  report.py

src/multi_agent_brief/repair/
  repair_plan.py
  bounded_refine.py
```

### RelevanceGate

每条 claim 进入正文前必须有：

```text
topic_relevance
audience_relevance
target_entity_relevance
time_relevance
actionability
recommended_use: include | appendix | drop | to_verify
reason
```

硬规则：

- `recommended_use=drop` 的 claim 被正文引用，audit fail。
- 不能解释“目标读者为什么要看”的 claim 不能进入 executive summary。
- 缺少本期时间口径的 claim 默认只能作 background 或 appendix。

### DeliveryGate

检查：

- 语言是否匹配。
- 读者层级是否匹配。
- 是否出现通用模板栏目泄漏。
- 是否缺 executive summary。
- 是否缺 risk / limitation / next watch。
- 是否有英文泄漏。
- reader-facing 输出是否残留 `[src:CLAIM_ID]`。

### Bounded repair

Orchestrator 允许有限修复：

```text
max_repair_rounds: 2
repair_scope: structure | citation | wording | rendering
fact_change_requires: claim-ledger update + citation audit
```

事实修复必须回到 source / evidence / claim 层，不能让 Editor 自行补事实。

完成标准：

- 弱模型只做局部受控任务。
- Draft / final 的质量失败会生成 repair_plan，而不是直接交付。
- reader-facing brief 不包含 `[src:CLAIM_ID]`，但 audited draft 保留引用供审计。

## v0.7.0: Golden Evaluation Harness

目标：把“质量有没有变好”变成可回归测试。

### 必须做

新增：

```text
golden_cases/
  normal_weekly/
  quiet_week/
  sparse_evidence/
  conflicting_sources/
  feedback_contamination/
  citation_removed_by_editor/
  unsupported_recommendation/
```

新增模块：

```text
src/multi_agent_brief/eval/
  rubric.py
  golden_case.py
  scorer.py
  compare.py
  report.py
```

CLI：

```bash
multi-agent-brief eval run --case golden_cases/normal_weekly
multi-agent-brief eval score --workspace <workspace>
multi-agent-brief eval compare --baseline runs/A --candidate runs/B
```

完成标准：

- CI 可跑 public-safe golden smoke。
- 评估不要求 LLM 输出完全一致，只要求 contract compliance 和 minimum quality。
- 每个 PR 能看到 artifact、provenance、quality 的回归信号。

## v0.7.1: FrictionStore And Improvement Proposals

目标：把失败、人审反馈、audit findings 转成结构化改进项，不把原始反馈直接塞回 prompt。

### 必须做

新增：

```text
friction_store.jsonl
improvement_signals.json
improvement_proposal.md
patch_plan.md
regression_plan.json
```

建议模块：

```text
src/multi_agent_brief/improve/
  friction_store.py
  failure_miner.py
  proposal.py
  patch_plan.py
  regression.py
```

Friction item：

```json
{
  "friction_id": "FRIC_001",
  "source_type": "human_feedback|audit_finding|regression_failure",
  "failure_type": "unsupported_claim",
  "severity": "high",
  "bad_example": "short sanitized example",
  "preferred_fix": "rewrite as evidence-bound observation",
  "applies_to": ["analyst", "editor"],
  "policy_scope": ["manufacturing_executive"],
  "status": "active",
  "expires_at": null
}
```

### Friction provenance

Friction item 必须能回溯到触发它的 audit finding、claim、artifact、tool call 或 orchestrator decision。

新增字段：

```text
source_refs
related_claim_ids
related_artifact_ids
related_event_ids
provenance_edges
```

示例：

```json
{
  "friction_id": "FRIC_001",
  "source_refs": ["audit_report.json#AUDIT_014"],
  "related_claim_ids": ["CLM_023"],
  "related_artifact_ids": ["audited_brief"],
  "related_event_ids": ["EVT_20260608_001"],
  "provenance_edges": [
    {"from": "AUDIT_014", "to": "FRIC_001", "relation": "TRIGGERED"},
    {"from": "FRIC_001", "to": "CLM_023", "relation": "PROPOSED_FIX_FOR"}
  ]
}
```

没有 provenance 的 friction item 只能作为 draft suggestion，不能进入 future run injection。

### Self-improvement safety rules

必须明确禁止：

- 自动修改 main branch。
- 删除或放宽 failing tests 来提升分数。
- 静默降低 quality threshold。
- 把原始用户反馈、完整 prompt、原始日志直接注入 skill 或 agent prompt。
- 让模型自行批准事实修复。
- 在没有 regression plan 的情况下把 friction item 标为 active。

完成标准：

- Orchestrator 可以把失败写成 improvement signal。
- FrictionStore 不保存敏感原始日志、完整 prompt 或私有材料。
- 自改进只产生 proposal、patch plan、validator 或 golden case 建议，不自动改 main。
- 每个 active friction item 都能追溯到 audit finding、event、artifact 或人工确认记录。

## v0.8.0: Policy Packs And Runtime Parity

目标：让 Orchestrator 根据行业、受众和任务类型选择不同 rule set。

### 必须做

新增 policy packs：

```text
configs/policy_packs/default.yaml
configs/policy_packs/manufacturing_executive.yaml
configs/policy_packs/finance_research.yaml
configs/policy_packs/internet_pm.yaml
```

每个 policy pack 至少定义：

```text
stage_overrides
source_rules
claim_rules
delivery_rules
quality_weights
human_review_gates
repair_limits
```

Runtime parity：

- Hermes parent prompt。
- Claude `/generate-brief`。
- Codex orchestrator agent。
- OpenCode primary agent。
- manual fallback。

完成标准：

- 同一 workspace 在不同 runtime 下产生同一套 expected artifact contract。
- policy pack 不改变事实链 schema，只改变 gates、weights、stage options。
- runtime adapter 差异不泄漏到业务 artifact schema。

## v0.9.0: Distribution And Reference Workflows

目标：让新用户不需要 clone repo 内部结构也能安装 runtime assets 并跑通 reference workflow。

### 必须做

```text
multi-agent-brief assets install --profile hermes|claude|opencode|codex
multi-agent-brief assets doctor
scripts/install.sh
scripts/install.ps1
Homebrew formula
importlib.resources package assets
```

Reference workflows：

```text
examples/reference_workflows/manufacturing_executive_weekly
examples/reference_workflows/finance_research_brief
examples/reference_workflows/internet_pm_competitor_scan
```

完成标准：

- fresh install 后能安装 agent assets。
- `assets doctor` 能发现版本不匹配、缺文件、runtime 未配置。
- reference workflow 使用 public-safe 数据。

## v1.0.0: Stable Orchestrated Brief Workflow

v1.0 不是完整分布式 MAS runtime。v1.0 应冻结一个 local-first、file-state-driven、contract-governed、provenance-aware、self-improving 的 briefing workflow baseline。

v1.0 必须包括：

- Explicit Orchestrator Contract。
- Runtime state、run manifest、event log。
- Artifact registry 和 process validators。
- Evidence provenance。
- Execution provenance。
- Unified provenance graph 和 typed relation schema。
- RelevanceGate、DeliveryGate、bounded repair。
- Golden evaluation。
- FrictionStore 和 improvement proposal。
- Hermes / Claude / Codex / OpenCode / manual runtime parity。
- package assets install 和 doctor。

完成标准：

- 从 fresh install 能跑通 supported reference workflow。
- 所有正式输出都有 artifact、evidence、execution、audit 记录。
- 弱模型不能绕过 Orchestrator contract 直接生成 reader-facing brief。
- v1.0 可作为 v2 MAS Runtime 的对照组和回退基线。

## v2.0: MAS Runtime Research Track

v2.0 推迟到 v1.0 后。它可以探索真正的 runtime 层，而不是继续扩写 handoff contract。

候选方向：

```text
Shared World / SQLite Event Store
Typed AgentMessage envelope
TaskBoard and leases
Agent inbox cursor
Capability registry
ClaimProposal state machine
Deterministic ClaimReducer
Run replay
Task tree / DAG control flow
```

不在 v2 初期做：

- 多服务器、Kafka、Redis。
- 企业多租户权限。
- 完整 RAG memory。
- 自动 main branch 自修改。
- 一次性迁移所有 connector 和 analysis module。

## Agent Implementation Guide

## Validation Coverage Strategy

每个版本任务都要配套验证覆盖，不能只写 schema 或 prompt。

| 层 | 最低测试覆盖 |
|---|---|
| Orchestrator Contract | docs grep、role config generation、runtime entry parity、禁止 Python pipeline 回归 |
| Runtime State | state schema roundtrip、missing field failure、event log append、run id consistency |
| Artifact Contract | missing artifact、empty artifact、malformed JSON、wrong producer、upstream dependency failure |
| Evidence Provenance | source/evidence/claim ref integrity、support_status 与 relation consistency、citation audit failure |
| Execution Provenance | required artifact producer、stage event order、tool_call summary redaction、handoff lineage |
| Unified Graph | unknown relation、broken node ref、orphan artifact、claim without evidence edge、friction without trigger edge |
| Quality Gates | relevance threshold、delivery leakage、bounded repair max rounds、reader-facing citation stripping |
| FrictionStore | no raw prompt/log injection、expiry handling、scope filtering、proposal requires provenance |

建议测试文件：

```text
tests/test_orchestrator_contract_docs.py
tests/test_workflow_state.py
tests/test_artifact_registry.py
tests/test_provenance_graph.py
tests/test_relation_schema.py
tests/test_friction_store.py
tests/test_validate_commands.py
tests/test_runtime_parity_contract.py
```

每个 PR 至少要回答：

```text
What artifact/schema changed?
What validator enforces it?
What runtime entry reads it?
What regression test prevents rollback?
What failure mode becomes visible to the Orchestrator?
```

后续 agent 接到任务时按这个顺序判断：

1. 如果任务涉及主流程、runtime、handoff、subagent sequencing，先看 `docs/orchestrator-architecture.zh-CN.md` 和 `configs/orchestrator_contract.yaml`。
2. 如果任务涉及某个 stage 的输入输出，先看 `configs/stage_specs.yaml`。
3. 如果任务涉及文件是否存在、是否有效、谁生成，先看 `configs/artifact_contracts.yaml` 和 `src/multi_agent_brief/artifacts/`。
4. 如果任务涉及事实支撑，先看 provenance 和 claim ledger，不要先改 prompt。
5. 如果任务涉及文稿质量，先看 RelevanceGate、DeliveryGate、analysis_blocks 和 final quality harness。
6. 如果任务涉及自改进，先写 improvement proposal 和 golden case，不要直接把反馈塞进 skill。

每个 PR 应尽量只做一个 contract slice：

```text
one schema
one validator
one CLI surface
one runtime adapter update
one focused test group
```

避免把 Orchestrator、provenance、quality、packaging 混在一个 PR 里。

## 暂缓事项

v1.0 前不要把重心放在：

- 更多搜索后端。
- 更多交付渠道。
- 完整模型路由。
- 完整 RAG / 长期记忆。
- 企业多租户。
- 分布式 MAS runtime。
- 大量行业专题模块。

未稳定能力必须在 README、support matrix、CLI 输出中标为 Experimental、Interface Only 或 CLI-only。
