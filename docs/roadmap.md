# Roadmap

This roadmap replaces the previous post-v0.5.8 plan. The next phase is no longer a set of separate quality, provenance, and evaluation tracks. It is organized around one main line:

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

## Core Definition

### The Orchestrator Is The Main Agent, Not A Python Pipeline

In this project, the Orchestrator is the runtime main agent. It may be the Hermes parent agent, the Claude Code command context, the Codex main agent, the OpenCode primary agent, or a manual fallback operator.

The Orchestrator controls the workflow, delegates subagents, validates artifacts, records decisions, blocks failed stages, and generates improvement signals. Python provides tools, schemas, validators, manifests, event logs, renderers, and audit harnesses.

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

Do not implement this as a new `BriefPipeline.run()`. Do not let Python automatically execute Scout, Screener, Analyst, Editor, and Auditor as the main brief-generation path.

### v0.6 Goal

v0.6 should make even a weaker model understand:

```text
I am the orchestrator.
I manage stage state.
I delegate specialist agents.
I validate each artifact.
I decide continue / retry / block / human review.
I record provenance and improvement signals.
```

In other words, v0.6 upgrades the main agent from "follow these prompt steps" to "workflow controller governed by contracts and harnesses".

## Completed Baseline

### v0.5.7

- `multi-agent-brief run` became a runtime handoff launcher, not a Python brief generator.
- The main path is an external subagent workflow: scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> formatter.
- Hermes became the primary runtime path, with `delegate_task`, cron, daily source cache, and cached_package support.
- Claude Code, OpenCode, and Codex agent assets are generated from `configs/agent_roles.yaml`.
- `inputs classify` and ManualProvider gates prevent feedback / instruction / context contamination of the Claim Ledger.
- Deterministic audit, editorial governance, final quality, and limitation hygiene provide the initial quality harness.

### v0.5.8

- Removed old `prepare` / Python pipeline narratives.
- Added the support matrix.
- Added `VERSION` as the single version source plus release consistency scripts.
- Clarified clone/source install versus CLI-only install boundaries.

## Engineering Layers

When modifying the repository, locate the work in this order:

| Layer | Purpose | Main files |
|---|---|---|
| Orchestrator Contract | Define how the main agent controls the workflow | `configs/orchestrator_contract.yaml`, `.agents/skills/orchestrator/SKILL.md`, `configs/agent_roles.yaml` |
| Stage Spec | Define stage dependencies, inputs, outputs, validators, and failure actions | `configs/stage_specs.yaml`, `src/multi_agent_brief/orchestrator/stage_spec.py` |
| Artifact Contract | Define artifact schema, producer, consumer, hash, and status | `configs/artifact_contracts.yaml`, `src/multi_agent_brief/artifacts/` |
| Runtime State | Record run state, decisions, and gate results | `workflow_state.json`, `run_manifest.json`, `event_log.jsonl` |
| Evidence Provenance | Track source -> evidence -> claim -> draft -> final | `source_registry.json`, `evidence_pack.json`, `claim_ledger.json`, `citation_audit.json` |
| Execution Provenance | Track agent / tool / handoff / artifact lineage | `agent_task_log.jsonl`, `tool_call_log.jsonl`, `handoff_log.jsonl` |
| Unified Provenance Graph | Connect factual chain, execution chain, audit, and improvement signals with typed relations | `provenance_graph.json`, `src/multi_agent_brief/provenance/graph.py` |
| Quality Harness | Track relevance, delivery, repair, rendering, and eval results | `relevance_report.json`, `delivery_report.json`, `quality_score.json` |
| Improvement Loop | Convert failures into controlled improvement items | `friction_store.jsonl`, `improvement_signals.json`, `improvement_proposal.md` |

## v0.6.0: Explicit Orchestrator Contract

Goal: turn the Orchestrator from a generic coordinating agent into the formal main-agent contract.

### Must Do

1. Add Orchestrator architecture docs:

```text
docs/orchestrator-architecture.md
docs/orchestrator-architecture.zh-CN.md
```

These docs must define:

- The Orchestrator is the main agent, not a Python pipeline.
- How it reads workspace config, policy packs, stage specs, and artifact contracts.
- How it delegates subagents.
- Which decisions it may make.
- When it must block, retry, or request human review.
- Python CLI commands are tools and validators only.

2. Add contract sources:

```text
configs/orchestrator_contract.yaml
configs/stage_specs.yaml
configs/artifact_contracts.yaml
configs/policy_packs/default.yaml
```

3. Rewrite the Orchestrator role source:

```text
configs/agent_roles.yaml
.agents/skills/orchestrator/SKILL.md
```

Then run:

```bash
python scripts/generate_agent_configs.py
python scripts/check_agent_configs.py
```

Generated files include:

```text
.claude/agents/orchestrator.md
.codex/agents/orchestrator.toml
.opencode/agents/brief-orchestrator.md
docs/agents/
```

4. Update runtime command paths:

```text
.claude/commands/generate-brief.md
.opencode/commands/generate-brief.md
.agents/hermes-skills/multi-agent-brief-hermes/SKILL.md
src/multi_agent_brief/hermes/adapter.py
src/multi_agent_brief/cli/start_commands.py
```

Each entry point must say:

- The current main agent is the Orchestrator.
- Scout / Screener / Claim Ledger / Analyst / Editor / Auditor / Formatter are delegated subagents.
- The Orchestrator validates the artifact after each stage before continuing.

### Orchestrator Decision Schema

v0.6.0 must define these decisions in docs and config:

```text
continue
retry_stage
delegate_repair
request_human_review
block_run
finalize
```

Each decision must include:

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

### Tests

Add or update:

```text
tests/test_orchestrator_contract_docs.py
tests/test_agent_config_generation.py
tests/test_start_commands.py
tests/test_no_python_pipeline_regression.py
```

Test focus:

- Orchestrator role files contain main-agent / controller / decision / validation / block semantics.
- Orchestrator role files do not describe it as an ordinary pipeline stage.
- generate-brief commands require Orchestrator validation after each stage.
- `BriefPipeline().run` and Python fake-agent runtime paths cannot return.

### Done When

- Future agents know to start with `configs/orchestrator_contract.yaml` and `configs/stage_specs.yaml`.
- Hermes / Claude / Codex / OpenCode entry points tell the main agent it is the Orchestrator.
- Agent config generation checks pass.
- pytest passes.

## v0.6.1: Runtime State And Handoff Initialization

Goal: make `multi-agent-brief run` initialize Orchestrator-readable run state, not just `agent_handoff.md/json`.

### Must Do

Add:

```text
src/multi_agent_brief/orchestrator/
  __init__.py
  workflow_state.py
  stage_spec.py
  decision.py
  event_log.py
  policy_loader.py
```

Reuse and migrate:

```text
src/multi_agent_brief/core/manifest.py
```

Do not create a parallel manifest. Convert the existing manifest concept into a runtime handoff manifest, not a pipeline manifest.

`multi-agent-brief run/start/handoff` should generate:

```text
output/intermediate/agent_handoff.md
output/intermediate/agent_handoff.json
output/intermediate/workflow_state.json
output/intermediate/run_manifest.json
output/intermediate/event_log.jsonl
```

### Minimal workflow_state.json

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

### Minimal event_log.jsonl Events

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

Do not store raw chain-of-thought. Store short `reason_summary`, tool observation summaries, and validation summaries.

### CLI

Add:

```bash
multi-agent-brief validate run --workspace <workspace>
multi-agent-brief validate state --workspace <workspace>
```

`validate state` should initially check JSON structure, stage IDs, runtime, and required fields.

### Tests

```text
tests/test_workflow_state.py
tests/test_event_log.py
tests/test_validate_commands.py
tests/test_start_commands.py
```

Done when:

- `run --workspace` does not generate a brief, but does generate Orchestrator state.
- `validate state` runs without an LLM.
- event log records run initialization and handoff creation.

## v0.6.2: Artifact Registry And Process Contract

Goal: prevent fake completion. The Orchestrator cannot trust a subagent saying "done"; it must verify artifacts.

### Must Do

Add:

```text
src/multi_agent_brief/artifacts/
  __init__.py
  models.py
  registry.py
  validators.py
  hashing.py
```

Generate:

```text
output/intermediate/artifact_registry.json
```

Artifact registry entry:

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

Add:

```bash
multi-agent-brief validate artifact --workspace <workspace> --artifact claim_ledger
multi-agent-brief validate stage --workspace <workspace> --stage claim_ledger
multi-agent-brief validate handoff --workspace <workspace>
multi-agent-brief validate run --workspace <workspace>
```

Validators must check:

- Required artifact exists.
- File is non-empty.
- JSON parses when applicable.
- `schema_version` matches.
- `producer_stage` matches the stage spec.
- Upstream dependencies pass.

### Orchestrator Loop

After every delegated subagent returns, the Orchestrator must:

```text
validate stage
update artifact_registry
write event_log validation_result
write orchestrator_decision
continue or block
```

### Tests

```text
tests/test_artifact_registry.py
tests/test_artifact_validators.py
tests/test_validate_commands.py
tests/test_runtime_parity_contract.py
```

Done when:

- Analyst is not ready if `claim_ledger.json` is missing.
- Finalize is not ready if `audit_report.json` is missing.
- Hermes / Claude / Codex / OpenCode expected artifacts come from the same artifact contract.

## v0.6.3: Evidence Provenance Contract

Goal: upgrade factual trust from "the draft has citations" to a structured source -> evidence -> claim chain.

### Must Do

Add or upgrade:

```text
source_registry.json
evidence_pack.json
claim_ledger.json
citation_audit.json
```

Recommended module:

```text
src/multi_agent_brief/provenance/
  __init__.py
  source_registry.py
  evidence_pack.py
  citation_audit.py
  evidence_graph.py
```

### Claim Ledger Upgrade

Claims should gradually support:

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

Keep compatibility with existing `statement`, `source_id`, and `evidence_text` fields. Do not break existing tests in one sweep.

### Evidence Unit

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

### Citation Audit

Check:

- `[src:CLAIM_ID]` references exist.
- Claims have `evidence_refs`.
- Evidence links back to `source_registry`.
- unsupported / partially_supported / contradicted claims are not overstated.
- Editor did not remove draft citations needed for audit.

### Evidence Relation Types

v0.6.3 should define factual relation types first so v0.6.4 can reuse them in the unified graph:

```text
DERIVED_FROM: evidence -> source
SUPPORTS: evidence -> claim
PARTIALLY_SUPPORTS: evidence -> claim
CONTRADICTS: evidence -> claim
USES_CLAIM: draft/final artifact -> claim
INVALIDATES: citation_audit finding -> claim or citation marker
```

These relations are not display labels. Validators must be able to use them. For example, a `support_status=unsupported` claim should not have a `SUPPORTS` edge; a body claim connected through `CONTRADICTS` must carry limitation or uncertainty wording.

Done when:

- Important body claims cannot rely only on a URL or raw `source_id`.
- Claim links to evidence, and evidence links to source.
- citation audit failure blocks finalize.

## v0.6.4: Execution Provenance Contract

Goal: explain which agent, tool, handoff, and validation produced the current state.

### Must Do

Add:

```text
agent_task_log.jsonl
tool_call_log.jsonl
handoff_log.jsonl
orchestrator_report.json
provenance_graph.json
```

Recommended module:

```text
src/multi_agent_brief/provenance/
  execution_log.py
  agent_task_log.py
  tool_call_log.py
  handoff_log.py
  graph.py
  relation_schema.py
```

### agent_task_log Event

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

### tool_call_log Boundary

Do not store sensitive raw logs, API keys, full prompts, or raw chain-of-thought. Store:

```text
tool_name
purpose
parameters_summary
observation_summary
artifact_updates
status
```

### Unified Provenance Graph

v0.6.4 must explicitly introduce `provenance_graph.json`, connecting evidence provenance and execution provenance into one typed relation network.

Graph node types:

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

Graph relation types:

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

Minimal graph schema:

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

v0.6.4 does not need a full graph query engine, but it must generate the graph from existing artifacts and validate orphan nodes, unknown relations, and broken refs.

Done when:

- Every required artifact has a producer.
- Every stage success, failure, or block has a readable event.
- Orchestrator report explains which gates passed and which limitations remain.
- `provenance_graph.json` connects source/evidence/claim with agent_task/tool_call/artifact.
- Typed relation validators catch unknown relations, broken refs, and missing producers.

## v0.6.5: Orchestrator Quality And Repair Loop

Goal: put quality improvement inside the Orchestrator control loop instead of relying on a strong model to write a perfect draft.

### Must Do

Add or formalize:

```text
output/intermediate/relevance_report.json
output/intermediate/delivery_report.json
output/intermediate/repair_plan.json
```

Recommended modules:

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

Every claim must receive:

```text
topic_relevance
audience_relevance
target_entity_relevance
time_relevance
actionability
recommended_use: include | appendix | drop | to_verify
reason
```

Hard rules:

- If `recommended_use=drop` appears in the body, audit fails.
- Claims that cannot explain why the target reader should care cannot enter the executive summary.
- Claims without current-period framing default to background or appendix.

### DeliveryGate

Check:

- language match
- audience match
- generic template leakage
- missing executive summary
- missing risk / limitation / next-watch sections
- English leakage
- reader-facing `[src:CLAIM_ID]` leakage

### Bounded Repair

The Orchestrator may run limited repairs:

```text
max_repair_rounds: 2
repair_scope: structure | citation | wording | rendering
fact_change_requires: claim-ledger update + citation audit
```

Fact repair must return to source / evidence / claim layers. Editor must not invent replacement facts.

Done when:

- Weaker models perform constrained local work.
- Draft / final quality failures produce `repair_plan.json`, not delivery.
- reader-facing brief strips `[src:CLAIM_ID]`, while audited draft preserves citations.

## v0.7.0: Golden Evaluation Harness

Goal: make quality regression testable.

### Must Do

Add:

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

Add:

```text
src/multi_agent_brief/eval/
  rubric.py
  golden_case.py
  scorer.py
  compare.py
  report.py
```

CLI:

```bash
multi-agent-brief eval run --case golden_cases/normal_weekly
multi-agent-brief eval score --workspace <workspace>
multi-agent-brief eval compare --baseline runs/A --candidate runs/B
```

Done when:

- CI runs public-safe golden smoke tests.
- Evaluation does not require identical LLM output, only contract compliance and minimum quality.
- Every PR gets artifact, provenance, and quality regression signals.

## v0.7.1: FrictionStore And Improvement Proposals

Goal: convert failures, human feedback, and audit findings into structured improvement items without injecting raw feedback into prompts.

### Must Do

Add:

```text
friction_store.jsonl
improvement_signals.json
improvement_proposal.md
patch_plan.md
regression_plan.json
```

Recommended module:

```text
src/multi_agent_brief/improve/
  friction_store.py
  failure_miner.py
  proposal.py
  patch_plan.py
  regression.py
```

Friction item:

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

### Friction Provenance

Each friction item must trace back to the audit finding, claim, artifact, tool call, or orchestrator decision that triggered it.

Add fields:

```text
source_refs
related_claim_ids
related_artifact_ids
related_event_ids
provenance_edges
```

Example:

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

A friction item without provenance can only be a draft suggestion. It cannot enter future-run injection.

### Self-Improvement Safety Rules

Explicitly forbid:

- automatic modification of the main branch
- deleting or relaxing failing tests to improve scores
- silently lowering quality thresholds
- injecting raw user feedback, full prompts, or raw logs into skills or agent prompts
- letting the model approve its own factual repairs
- marking a friction item active without a regression plan

Done when:

- Orchestrator can write failures as improvement signals.
- FrictionStore does not store sensitive raw logs, full prompts, or private materials.
- Self-improvement only creates proposals, patch plans, validators, or golden case suggestions. It does not modify main automatically.
- Every active friction item traces to an audit finding, event, artifact, or human-confirmed record.

## v0.8.0: Policy Packs And Runtime Parity

Goal: let the Orchestrator select rule sets by industry, audience, and task type.

### Must Do

Add policy packs:

```text
configs/policy_packs/default.yaml
configs/policy_packs/manufacturing_executive.yaml
configs/policy_packs/finance_research.yaml
configs/policy_packs/internet_pm.yaml
```

Each pack defines:

```text
stage_overrides
source_rules
claim_rules
delivery_rules
quality_weights
human_review_gates
repair_limits
```

Runtime parity:

- Hermes parent prompt
- Claude `/generate-brief`
- Codex orchestrator agent
- OpenCode primary agent
- manual fallback

Done when:

- The same workspace has the same expected artifact contract across runtimes.
- Policy packs change gates, weights, and stage options, not factual schema.
- Runtime adapter differences do not leak into business artifact schema.

## v0.9.0: Distribution And Reference Workflows

Goal: let a new user install runtime assets and run a reference workflow without depending on repository internals.

### Must Do

```text
multi-agent-brief assets install --profile hermes|claude|opencode|codex
multi-agent-brief assets doctor
scripts/install.sh
scripts/install.ps1
Homebrew formula
importlib.resources package assets
```

Reference workflows:

```text
examples/reference_workflows/manufacturing_executive_weekly
examples/reference_workflows/finance_research_brief
examples/reference_workflows/internet_pm_competitor_scan
```

Done when:

- A fresh install can install agent assets.
- `assets doctor` catches version mismatch, missing files, and runtime setup gaps.
- Reference workflows use public-safe data.

## v1.0.0: Stable Orchestrated Brief Workflow

v1.0 is not a full distributed MAS runtime. It freezes a local-first, file-state-driven, contract-governed, provenance-aware, self-improving briefing workflow baseline.

v1.0 must include:

- Explicit Orchestrator Contract
- Runtime state, run manifest, event log
- Artifact registry and process validators
- Evidence provenance
- Execution provenance
- Unified provenance graph and typed relation schema
- RelevanceGate, DeliveryGate, bounded repair
- Golden evaluation
- FrictionStore and improvement proposals
- Hermes / Claude / Codex / OpenCode / manual runtime parity
- package assets install and doctor

Done when:

- A supported reference workflow runs from a fresh install.
- Every formal output has artifact, evidence, execution, and audit records.
- A weak model cannot bypass the Orchestrator contract and directly produce a reader-facing brief.
- v1.0 is a stable comparison and fallback baseline for future MAS Runtime work.

## v2.0: MAS Runtime Research Track

v2.0 is deferred until after v1.0. It may explore a real runtime layer instead of expanding the handoff contract.

Candidates:

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

Not in early v2:

- multi-server, Kafka, Redis
- enterprise multi-tenant permissions
- full RAG memory
- automatic main branch self-modification
- one-shot migration of all connectors and analysis modules

## Agent Implementation Guide

## Validation Coverage Strategy

Every version task needs validation coverage. Do not add schemas or prompts without tests.

| Layer | Minimum test coverage |
|---|---|
| Orchestrator Contract | docs grep, role config generation, runtime entry parity, Python pipeline regression ban |
| Runtime State | state schema roundtrip, missing field failure, event log append, run id consistency |
| Artifact Contract | missing artifact, empty artifact, malformed JSON, wrong producer, upstream dependency failure |
| Evidence Provenance | source/evidence/claim ref integrity, support_status and relation consistency, citation audit failure |
| Execution Provenance | required artifact producer, stage event order, tool_call summary redaction, handoff lineage |
| Unified Graph | unknown relation, broken node ref, orphan artifact, claim without evidence edge, friction without trigger edge |
| Quality Gates | relevance threshold, delivery leakage, bounded repair max rounds, reader-facing citation stripping |
| FrictionStore | no raw prompt/log injection, expiry handling, scope filtering, proposal requires provenance |

Recommended test files:

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

Every PR should answer:

```text
What artifact/schema changed?
What validator enforces it?
What runtime entry reads it?
What regression test prevents rollback?
What failure mode becomes visible to the Orchestrator?
```

Future agents should use this order:

1. For main workflow, runtime, handoff, or subagent sequencing, start with `docs/orchestrator-architecture.md` and `configs/orchestrator_contract.yaml`.
2. For stage inputs and outputs, start with `configs/stage_specs.yaml`.
3. For artifact existence, validity, producer, or consumer logic, start with `configs/artifact_contracts.yaml` and `src/multi_agent_brief/artifacts/`.
4. For factual support, start with provenance and claim ledger. Do not start by editing prompts.
5. For report quality, start with RelevanceGate, DeliveryGate, analysis_blocks, and final quality harness.
6. For self-improvement, write improvement proposals and golden cases. Do not inject raw feedback into skills.

Each PR should implement one contract slice:

```text
one schema
one validator
one CLI surface
one runtime adapter update
one focused test group
```

Avoid mixing Orchestrator, provenance, quality, and packaging in one PR.

## Deferred

Before v1.0, do not prioritize:

- more search backends
- more delivery channels
- full model routing
- full RAG / long-term memory
- enterprise multi-tenancy
- distributed MAS runtime
- many new industry modules

Unstable capabilities must be labeled Experimental, Interface Only, or CLI-only in README, support matrix, and CLI output.
