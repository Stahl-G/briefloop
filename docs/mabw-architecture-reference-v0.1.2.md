# MABW: A Contract-Governed Multi-Agent Workflow for Enterprise Briefing

## Architecture Reference v0.1.2 — Technical Report

**Snapshot**: v0.6.9 working state
**Branch**: `codex/069-install-runtime-asset-parity`
**Commit**: `8a36803`
**Date**: 2026-06-10

---

> **Current Release Boundary.** This report documents the v0.1.2 design framing and maps it to the v0.6.9 implementation baseline. Key capabilities landed across releases: feedback/repair lifecycle (v0.6.2+), deterministic quality gate reports (v0.6.3+), public-safe evaluation cases (v0.6.4+), optional provenance projection (v0.6.5+), audience profile runtime surface (v0.6.6+), Orchestrator control switchboard (v0.6.7+), reader-facing source appendix (v0.6.8+), and runtime asset installer (v0.6.9). `audience_profile.md` is the human-editable source file; `output/intermediate/audience_profile_snapshot.md` is the frozen per-run file read through handoff. Mid-run profile edits apply to the next run. Feedback issues and repair plans are structured, validated, and recorded — selection is not execution. Quality gate reports are deterministic checks; they do not perform semantic fact verification, source retrieval, or automatic content correction. These surfaces are not a long-term memory system, semantic proof, or autonomous repair.

---

## Abstract

Enterprise briefing relies on a craft that takes new analysts months to acquire: writing briefs that are not only factually correct, but also aligned with an invisible set of departmental tastes, cultural taboos, and unstated editorial preferences. Single-LLM approaches fail here twice — they cannot guarantee factual correctness (no audit trail, no repairability), and they cannot learn taste (each run starts fresh).

MABW (Multi-Agent Brief Workflow) addresses both gaps through a **contract-governed multi-agent workflow with an audience profile runtime surface**. Correctness is governed by four structured contracts enforced at stage boundaries through a file-state blackboard. Taste is written in `audience_profile.md` — a plain-text, human-editable workspace file — and frozen into `output/intermediate/audience_profile_snapshot.md` for each run. The Orchestrator reads the snapshot, produces a semantic summary, and injects it into stage handoffs. Contracts bound correctness; taste context guides reader fit without becoming a contract.

This report documents the v0.6.9 architecture lineage, the v0.1.2 design update that adds the audience profile runtime surface and the LIFE-HARNESS runtime-interface comparison, and the core design philosophy: **bringing the improvement-loop infrastructure that made coding agents successful — auditability, traceability, structured feedback, and human-gated repair — into real business workflows.**

---

> **About This Report.** This is a living architecture reference. v0.1.2 adds: (1) the audience profile runtime surface (`audience_profile.md` + frozen per-run `audience_profile_snapshot.md` + Orchestrator semantic summary), (2) the formal separation between contract-governed correctness and profile-governed taste, (3) explicit red lines that coding agents must never violate, (4) the LIFE-HARNESS runtime-interface comparison (§1.4, §10.6), and (5) a full status refresh mapping the v0.1.x design framing to the v0.6.9 implementation baseline. For current release status, use `docs/architecture-status.md` and `docs/support-matrix.md`.

---

## 1. The Core Insight

### 1.1 Why Coding Agents Improved So Fast

Coding agents (Claude Code, Codex, Cursor) didn't improve because models got dramatically smarter. They improved because coding as a practice already had a **closed improvement loop**:

| Mechanism | What It Does |
|-----------|--------------|
| **Test suite** | Binary pass/fail signal — no ambiguity about whether code works |
| **Git history** | Every change attributed to a commit, an author, a reason |
| **Bug → commit traceability** | A failing test can be traced to the specific change that broke it |
| **CI/CD pipeline** | Changes are validated automatically before merge |
| **Code review gate** | Every change requires human approval |

These five mechanisms form a complete cycle: write code → test → fail → trace cause → fix → human review → pass → never regress on the same bug. The model provides the raw capability; **the infrastructure provides the improvement signal.**

### 1.2 What Business Workflows Lack

Enterprise briefing has none of these mechanisms:

- No binary correctness signal: a brief is "not quite right" but nobody can point to a failing test
- No traceability: a stale market figure in a brief cannot be traced to which source retrieval step failed
- No accumulated memory: a junior analyst's mistake is corrected verbally and forgotten; the next new hire repeats it
- No structured feedback: "this section doesn't feel right" evaporates after the meeting

The result: business workflows cannot improve systematically. They improve only through individual human learning, which is slow, non-transferable, and lost when people leave.

### 1.3 MABW's Thesis

**The same infrastructure that made coding agents improvable can be built for enterprise briefing — but it requires designing for auditability, traceability, and structured feedback from the beginning, not bolting it on afterward.**

MABW does not try to make a single LLM smarter. It builds the five mechanisms:

| Coding Mechanism | MABW Equivalent |
|------------------|-----------------|
| Test suite | Artifact validation (`valid`/`invalid`) + Quality gates (`pass`/`block`) |
| Git history | `event_log.jsonl` — every decision recorded with timestamp, actor, reason |
| Bug → commit traceability | `artifact_registry.json` — every artifact has `producer_stage`, `producer_role`, `consumer_stages` |
| CI/CD pipeline | Orchestrator control loop — contract enforcement at every stage boundary |
| Code review gate | `request_human_review` decision + RepairPlan human approval |

The bet: if you build the infrastructure, the improvement will follow — not because the model learns, but because the system accumulates feedback-to-improvement cycles the way a test suite accumulates regression checks.

### 1.4 Runtime-Interface Comparison: LIFE-HARNESS

In May 2026, Xu et al. released *Adapting the Interface, Not the Model: Runtime Harness Adaptation for Deterministic LLM Agents* (arXiv:2605.22166, v1 May 21, v2 May 27, 2026). The paper demonstrates that a structured runtime harness, evolved from training trajectories and applied to frozen LLM agents, improves 116 out of 126 model–environment settings across 18 open model backbones including Qwen, Llama, and xLAM families, with an average relative gain of 88.5%. The harnesses were evolved only from Qwen3-4B-Instruct trajectories and transferred to 17 other models without modification.

The useful comparison is not a four-layer-to-four-contract isomorphism. LIFE-HARNESS layers are lifecycle interception points: before interaction, during task conditioning, before execution, and after execution. MABW contract categories are governance domains: behavior, process/artifact, fact-grounding/evidence, and quality/audience. These are different axes.

The convergence is at the thesis level: **adapt the interface, not the model.** LIFE-HARNESS shows that recurring failures in deterministic agent environments can be localized to the model-environment boundary and converted into structured, auditable, frozen harness interventions. MABW applies the same interface-first intuition to enterprise briefing, but uses human review, contract checks, state files, and bounded repair proposals rather than automatic harness evolution. Their differences are as instructive as their similarities (see §10 for a detailed comparison).

---

## 2. Design Philosophy & Core Contributions

### 2.1 Two Kinds of Quality: Correctness and Taste

Enterprise briefing quality has two orthogonal dimensions:

| Dimension | What It Means | Examples | How It's Enforced |
|-----------|--------------|----------|-------------------|
| **Correctness** | The brief contains no factual errors, stale data, misattributed claims, or structural violations | Wrong stock price, missing source, claim without evidence, wrong entity name | **Contract enforcement** — structured YAML, schema-validated, mechanically checked at stage boundaries |
| **Taste** | The brief matches the department's editorial preferences, cultural norms, taboo topics, and unwritten expectations | "Don't give strategic advice without data," "use product-philosophy framing," "lead with risk disclaimers first" | **audience_profile.md** source + **audience_profile_snapshot.md** runtime context — plain natural language, human-edited, semantically interpreted by the Orchestrator |

Correctness can be mechanized. Taste cannot — it is learned through months of feedback cycles, passed down through implicit cultural norms, and varies between companies, departments, and individual managers. MABW's architecture treats these two dimensions as separate concerns with separate governance mechanisms.

### 2.2 The Audience Profile Runtime Surface

v0.1.1 introduces `audience_profile.md` — a plain-text file in the workspace, analogous to the Hermes agent's USER.md. v0.6.6 freezes that live profile into `output/intermediate/audience_profile_snapshot.md` for the active run. v0.6.7 adds `output/intermediate/orchestrator_control_switchboard.json` as a separate runtime control surface; it may read the snapshot as context, but it does not modify taste memory or execute controls. v0.6.8 adds `output/source_appendix.md` as a reader-facing source list produced during finalize, not as part of the taste or control-routing layer. The profile/snapshot pair carries:

- Editorial preferences ("lead with the key number, not background context")
- Cultural taboos ("never suggest strategic pivots without explicit data support")
- Structural conventions ("market data section goes before policy analysis")
- Departmental vocabulary and framing preferences
- Accumulated feedback patterns ("recurring issue: briefs are too long for Monday morning reads")

**Lifecycle:**

1. **Initial state**: A few lines of natural language written by the department head or senior analyst. No schema, no YAML, no structured fields required.
2. **Per-run**: `run`, `start`, and `handoff` create or reuse a frozen `audience_profile_snapshot.md` for the current `run_id`. The Orchestrator reads that snapshot at the start of the run, alongside contract references and state files. It produces a semantic summary of relevant taste guidelines for the current briefing task.
3. **Injected into handoffs**: The summary is included in the handoff context for every specialist role that generates or edits content (scout through auditor).
4. **Growth over time**: After each run, the human operator can add notes to `audience_profile.md` based on what went well or poorly. Those edits are captured only by a later run snapshot; mid-run edits do not change the active run. v0.6.8 does not automatically update the profile.
5. **No mechanical enforcement**: Unlike contracts, `audience_profile.md` and its snapshot have no schema validation for taste content, no blocking semantics, and no pass/fail gate. The snapshot is semantically interpreted by the Orchestrator and the specialist roles. Drift is accepted between runs, not within a frozen run.

### 2.3 Contract-Backed Control Surface

The central design decision in MABW is that **contracts are not prompts, and they are not post-hoc documentation**. A contract is a multi-dimensional governance interface that defines what a stage must consume, what artifact it must produce, which decisions are legal at that stage, and what happens on violation.

MABW defines four contract categories (per `configs/orchestrator_contract.yaml`):

| Category | Purpose | v0.6.1 Status |
|----------|---------|---------------|
| Behavior | Define Orchestrator and specialist role boundaries | Implemented (v0.6.0) |
| Process/Artifact | Define stage readiness and expected artifact categories | Reference-only (v0.6.0); minimum registry checks in v0.6.1 |
| Fact-Grounding/Evidence | Keep material statements traceable to supported claims | Reference-only; enforcement deferred to v0.6.3 |
| Quality/Audience | Keep delivery decisions aligned with reader context | Reference-only; enforcement deferred to v0.6.3 |

In v0.6.1, contracts function as a **reference/config + state CLI hybrid control layer**. The Orchestrator reads contract references, inspects runtime state files, validates artifact status, and applies the decision vocabulary — but enforcement is not yet a full runtime API (→ v0.6.3+ for full contract enforcement). Contracts serve as the **precondition for subagent existence**: a specialist role cannot be defined without specifying its contract surface.

**Contracts govern correctness. They do not govern taste.** Taste lives in `audience_profile.md` and the per-run `audience_profile_snapshot.md` (§2.2). This separation is load-bearing: it means contracts change rarely (only when the workflow structure changes), while taste can be updated between runs by anyone who can write a sentence.

### 2.4 Workflow Control Before Agent Autonomy

MABW is closer to a workflow/control system than an autonomous-agent benchmark. Stage transition, blocking, retry, and human review handoff are workflow semantics — and in MABW, they belong to the Orchestrator, not to the agents.

The Orchestrator's decision vocabulary (`continue`, `retry_stage`, `delegate_repair`, `request_human_review`, `block_run`, `finalize`) directly instantiates workflow control primitives described in van der Aalst et al.'s Workflow Patterns. Each stage's `allowed_decisions` (per `configs/stage_specs.yaml`) constrains which decisions are legal at that point — the Orchestrator has authority, but not unbounded authority.

Agent autonomy is deliberately constrained to **single-stage scope**. A specialist role executes its assigned stage, produces its expected artifact, and returns control to the Orchestrator. Cross-stage decisions — whether to proceed, retry, repair, escalate, or block — are Orchestrator decisions, not agent negotiations. This aligns with Anthropic's guidance in *Building Effective Agents*: "chaining steps where each has clear inputs and outputs" is the workflow pattern most appropriate for tasks requiring predictability and auditability.

### 2.5 File-State as Blackboard, not Chat Memory

MABW's shared context is not agent chat history. It is a set of **verifiable state files**: `runtime_manifest.json`, `workflow_state.json`, `artifact_registry.json`, and `event_log.jsonl`. Each specialist role reads from and writes to these structured files — it does not participate in a free-form multi-agent conversation.

This directly instantiates the **blackboard architecture** pattern (Nii, 1986): a shared data structure (state files), independent knowledge sources (specialist roles), and a control component (the Orchestrator) that decides which knowledge source to activate next based on the current state of the blackboard. The contrast with conversation-based multi-agent frameworks (AutoGen, CAMEL) is structural: coordination happens through structured state transitions, not agent-to-agent messages.

### 2.6 Architecture Invariants

The following invariants define MABW's architectural boundary:

1. **Python is tools/validators/renderers.** Python provides workspace setup, source handling, deterministic checks, validation helpers, audit support, and final rendering. It is not the standard brief-generation runtime.

2. **External runtime is main-agent/subagent execution surface.** Specialist roles run on one of five supported runtimes (Hermes, Claude, Codex, OpenCode, Manual). The Orchestrator delegates stages but does not execute them in-process.

3. **No BriefPipeline monolith.** The workflow is an Orchestrator loop with stage-by-stage delegation. Each stage is independently inspectable at its state-file boundary.

4. **Public docs stay high-level.** The authoritative specification is the contract YAML files in `configs/`. Prose documentation explains design rationale; contract files define runtime behavior.

5. **Contracts govern correctness; audience profile snapshot governs taste context.** They use different formats (structured YAML vs. plain text), different enforcement mechanisms (mechanical validation vs. semantic interpretation), and different edit frequencies (rarely vs. between-run profile edits). They do not contaminate each other.

6. **Human remains accountable.** All RepairPlan proposals operate within a human-accountable framework. No model-initiated contract modification occurs without an audit trail.

---

## 3. System Architecture

### 3.1 Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     CONTRACT REGISTRY                          │
│   orchestrator_contract.yaml  stage_specs.yaml                 │
│   artifact_contracts.yaml     policy_packs/default.yaml        │
└──────────────────────┬───────────────────────────────────────┘
                       │ reads
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                  ORCHESTRATOR CONTROL LOOP                     │
│  1. Read workspace (contracts + state + audience snapshot)     │
│  2. Summarize taste guidelines from frozen snapshot            │
│  3. Identify current stage → delegate → check artifact         │
│  4. Decide (continue/retry/repair/review/block/finalize)       │
└──┬─────────────────────┬──────────────────────┬──────────────┘
   │ delegates (handoff)  │ writes               │ invokes (CLI)
   │  + taste summary     │                      │
   ▼                      ▼                      ▼
┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ EXTERNAL LLM  │  │ RUNTIME STATE    │  │  PYTHON TOOLS     │
│ RUNTIMES      │  │ LAYER            │  │  (local CLI)      │
│ Hermes/Claude/│  │ runtime_manifest │  │  init/state       │
│ Codex/OpenCode│  │ workflow_state   │  │  doctor/sources   │
│ /Manual       │  │ artifact_registry│  │  inputs/audit     │
│               │  │ event_log        │  │  finalize         │
│ ┌───────────┐ │  ├──────────────────┤  └──────────────────┘
│ │Specialist │ │  │ WORKSPACE FILES   │
│ │Roles (LLM)│ │  │ audience_profile  │
│ │scout/     │ │  │ snapshot.md       │
│ │screener/  │ │  │ (taste context)   │
│ │claim-     │ │  └──────────────────┘
│ │ledger/    │ │
│ │analyst/   │ │
│ │editor/    │ │
│ │auditor    │ │
│ └───────────┘ │
└──────────────┘
```
*Python tools execute locally via CLI and are not LLM-powered. Specialist roles execute on external LLM runtimes via the handoff protocol. `audience_profile.md` is frozen into `output/intermediate/audience_profile_snapshot.md`; the Orchestrator reads the snapshot at the start of each run and injects a semantic taste summary into specialist role handoffs. Contracts govern correctness; the snapshot provides taste context.*

### 3.2 Contract Registry

The contract registry (per `configs/orchestrator_contract.yaml`) defines four categories:

**Behavior Contract** (`first_landing: v0.6.0`). Defines the Orchestrator's role boundaries and the boundary between the Orchestrator and each specialist role.

**Process/Artifact Contract** (`first_landing: v0.6.0_reference_only`). Defines stage readiness conditions and expected artifact categories. In v0.6.1, artifact status checking is operational; full enforcement is deferred.

**Fact-Grounding/Evidence Contract** (`first_landing: v0.6.0_reference_only`). Establishes that material statements must be traceable to supported claims. Enforcement deferred to v0.6.3.

**Quality/Audience Contract** (`first_landing: v0.6.0_reference_only`). Keeps delivery decisions aligned with reader context. Enforcement deferred to v0.6.3.

See §8.1 for the authoritative v0.6.1 component status, sourced from `orchestrator_contract.yaml:v061_boundaries`.

### 3.3 Runtime State Layer

#### 3.3.1 `runtime_manifest.json`

Run-level metadata file. Key fields: `run_id`, `runtime`, `mabw_version`, `contract_references`, `stage_order`, `expected_artifacts`. Run ID is preserved across non-reset `init` calls.

#### 3.3.2 `workflow_state.json`

Stage-level state machine. Key fields: `current_stage`, `blocked` (boolean), `blocking_reason`, `stage_statuses` (per-stage map with status values `pending`/`ready`/`complete`/`blocked`/`skipped`), `last_decision`, `next_allowed_decisions`.

#### 3.3.3 `artifact_registry.json`

Tracks every artifact's lifecycle. Artifact status values: `expected`, `missing`, `present`, `valid`, `invalid`. A required artifact transitions from `expected` to `missing` only after its producer stage has completed — a fresh workspace shows `expected`, not `missing`, and is not globally blocked. Blocking is current-stage-scoped and consumer-stage-scoped.

#### 3.3.4 `event_log.jsonl`

Append-only JSON Lines file. Each event contains: `event_id`, `run_id`, `created_at`, `event_type`, `actor`, `stage_id`, `artifact_id`, `decision`, `reason`, `metadata`. The event log is a control trace. In v0.6.5, `provenance_graph.json` is a separate optional projection for audit/debug review, not semantic proof or runtime replay.

### 3.4 Orchestrator Control Loop

#### 3.4.1 Decision Vocabulary

Six shared terms: `continue`, `retry_stage`, `delegate_repair`, `request_human_review`, `block_run`, `finalize`.

#### 3.4.2 Runtime Loop (v0.1.1 — updated with taste step)

1. Read workspace context: `config.yaml`, `sources.yaml`, `user.md`, **`output/intermediate/audience_profile_snapshot.md`**, `output/intermediate/orchestrator_control_switchboard.json`, inputs, handoff artifacts, and runtime state files
2. **Produce a semantic summary of taste guidelines from the frozen audience snapshot relevant to the current briefing task**
3. Read contract references from the handoff
4. Identify the current stage and expected artifact from `configs/stage_specs.yaml`
5. Delegate the stage to the appropriate specialist role or Python tool, **including the taste summary in the handoff context**
6. Check that the expected artifact is present and suitable for the next stage
7. [Deferred to v0.6.2] When audit findings or human feedback exist, structure issues and repair plans without executing repair
8. Decide using the six-term vocabulary
9. `finalize` only after audit readiness

#### 3.4.3 Stage-Scoped Decision Legality

Not all decisions are legal at all stages. Each stage declares `allowed_decisions`: `doctor` allows only `continue`, `request_human_review`, `block_run`; `analyst` allows the full vocabulary including `delegate_repair`; `finalize` allows only `finalize`, `request_human_review`, `block_run`.

### 3.5 External Runtime Surfaces

Five runtimes: Hermes, Claude, Codex, OpenCode, Manual. All share the same contract references, decision vocabulary, handoff protocol, and state file schemas. Runtime parity guarantees control model consistency, not semantic decision equivalence.

### 3.6 Specialist Roles

The 10-stage pipeline: `doctor` (Python) → `source-discovery` → `input-governance` (Python) → `scout` → `screener` → `claim-ledger` → `analyst` → `editor` → `auditor` → `finalize` (Python).

Specialist roles (`scout` through `auditor`) are LLM-powered, contract-governed, and executed via external runtime handoff. Each role receives the Orchestrator's taste summary from the frozen audience snapshot alongside its contract surface in the handoff context. Autonomous in-Python agent execution is intentionally deferred.

### 3.7 Python Tool / Validator / Renderer Layer

Three categories: **workspace lifecycle** (`init`, `state init`, `state check`, `runtime install`), **stage-bound tools** (`doctor`, `sources decide`, `inputs classify`, `audit`, `finalize`), and **feedback/repair lifecycle** (`feedback ingest`, `feedback plan`, `feedback resolve`, `feedback show`, `feedback validate` — structured, validated, recorded; no automatic execution).

---

## 4. Contract Enforcement Cycle

Each stage transition is a contract compliance checkpoint:

```
Contract Read → State Inspect → Artifact Validate → Decision → Event Record
```

**Artifact Status Lifecycle:**

```
expected → (producer stage completes) → missing → (file written) → present → valid
                                                                      └── invalid
```

Required artifacts trigger downstream stage blocking when missing or invalid. Optional artifacts do not block on their own. Blocking is always current-stage-scoped.

**Decision Recording:** When the Orchestrator calls `record_decision()`, `workflow_state.json` is updated and a `decision_recorded` event is appended to `event_log.jsonl`. The `orchestrator_contract.yaml` also defines `decision_record_fields` as a declarative contract-level specification.

---

## 5. Quality Gates

> **Status: Implemented (v0.6.3+).** Deterministic material-fact, freshness, and target-relevance gates write a quality gate report from existing artifacts without fetching sources or rewriting briefs. Gate reports inform Orchestrator decisions; they do not perform semantic fact verification or automatic content correction.

Three planned gates: **Policy Material Terms** (regulatory/compliance accuracy), **Market Quote Freshness** (data timeliness and attribution), **Target-Entity Relevance** (entity accuracy and scope). These are workflow-internal operational controls, not evaluation benchmarks — distinct from G-Eval, MT-Bench, and other LLM-as-judge frameworks.

---

## 6. Controlled Self-Improvement Loop

> **Status: Implemented (v0.6.2+).** Feedback issues and bounded repair plans can be structured, validated, and recorded via `multi-agent-brief feedback ingest/plan/resolve/show/validate` CLI commands. Per the architecture-status.md boundary: the system does not automatically edit brief artifacts, execute repair, or build a provenance graph. RepairPlan is a proposal — selection is recorded, execution is not automatic. The human gate remains in place.

**FeedbackIssue** (v0.6.2): Structured issue recording — human feedback linked to contract dimension, stage context, and severity. **RepairPlan** (v0.6.2): Bounded repair planning — plan only, no automatic edit/execute. **FrictionStore** (v0.7+): Long-term failure memory for cross-run pattern detection.

The loop is **controlled, not autonomous**: stages that allow `delegate_repair` route plans through the Orchestrator; stages restricted to `request_human_review` or `block_run` require explicit human approval. This design is distinguished from Self-Refine and Reflexion: MABW externalizes feedback as file-state artifacts and routes it through the same contract enforcement cycle as every other workflow decision.

---

## 7. Red Lines

The following architectural boundaries must never be violated by any coding agent, contributor, or automated tool. They are listed in order of priority.

| # | Red Line | Why |
|---|----------|-----|
| **R1** | Specialists NEVER communicate directly with each other | All coordination goes through state files + Orchestrator. Direct communication bypasses auditability |
| **R2** | NEVER modify another stage's artifact | Only the producer stage writes its own output. Cross-stage writes destroy traceability |
| **R3** | NEVER skip artifact validation | Required artifact missing/invalid → block, don't continue. Skipping validation makes contracts decorative |
| **R4** | NEVER turn contracts into "suggestions" or "guidelines" | Contracts are enforced at stage boundaries. Downgrading them to suggestions removes the governance surface |
| **R5** | NEVER invent a new decision outside the 6-term vocabulary | If a new decision is needed, it goes in `configs/orchestrator_contract.yaml` first, then code — not the reverse |
| **R6** | NEVER automatically edit brief content under "repair" | RepairPlan is a proposal. Human approves before execution. Autonomous content modification is prohibited |
| **R7** | NEVER bypass the Orchestrator | No stage transition without an Orchestrator decision recorded in `event_log.jsonl` |
| **R8** | NEVER remove or weaken the human gate on self-improvement | The system proposes; the human decides. This is architectural, not temporary |
| **R9** | NEVER put taste rules in contract YAML | Taste lives in `audience_profile.md` and the frozen runtime snapshot. Contracts govern correctness. Conflating them makes both unmaintainable |
| **R10** | NEVER require the human operator to read YAML or code | Non-AI-native users interact with CLI summaries, state files, and `audience_profile.md` natural language. The Orchestrator reads the snapshot; contracts are for the Orchestrator and the coding agent, not the end user |

An agent suggestion that violates any of these red lines is an architecture-level drift, not a minor adjustment. If a coding agent proposes "let analyst directly modify scout's artifact to save time," it simultaneously violates R1, R2, and R7.

---

## 8. Implementation Snapshot

> **Historical Snapshot**: v0.6.1 working state | **Branch**: `062` | **Commit**: `eff8bbc` | **Date**: 2026-06-08. Current release status has advanced; see `docs/architecture-status.md`.

### 8.1 Component Status

**Stable baseline plus current additions through v0.6.9:**

| Component | Evidence |
|-----------|----------|
| Shared Orchestrator authority + decision vocabulary | `configs/orchestrator_contract.yaml` |
| Contract references (4 categories) | `configs/orchestrator_contract.yaml` |
| Runtime role parity (5 surfaces) | `configs/orchestrator_contract.yaml` |
| Persisted runtime state control files | `runtime_manifest.json`, `workflow_state.json`, `artifact_registry.json`, `event_log.jsonl` |
| Minimum artifact registry status check | `configs/artifact_contracts.yaml` — 6 status values, 11 artifact definitions |
| Stage-scoped blocking summary | CLI output |
| Orchestrator decision event entrypoint | `event_log.jsonl` |
| Stage specifications (10 stages) | `configs/stage_specs.yaml` |
| Python tool layer | Workspace lifecycle + stage-bound tools |
| Role contracts + runtime handoff | `configs/stage_specs.yaml` |
| Feedback/repair lifecycle | `multi-agent-brief feedback ingest/plan/resolve/show/validate` — structured, validated, recorded; no automatic execution |
| Deterministic quality gates | Material-fact, freshness, target-relevance gate reports; no automatic source fetching or brief rewriting |
| Packaged public-safe evaluation cases | CI-validated gates, feedback, runtime blocker, Hermes path regressions |
| Optional provenance projection | Workspace-local audit/debug provenance graph from existing control files |
| **Audience profile runtime surface** | `audience_profile.md` source + frozen `output/intermediate/audience_profile_snapshot.md` |
| **Orchestrator control switchboard** | `output/intermediate/orchestrator_control_switchboard.json` + `control_selections.json`; selection is not execution |
| **Reader-facing source appendix** | `output/source_appendix.md` generated during finalize; mode: `append` appends to final Markdown/DOCX |
| **Runtime asset installer** | `multi-agent-brief runtime install --workspace <ws> --runtime opencode\|claude\|codex\|all` — copies project commands, agents, workspace skill, and Experimental Codex custom-agent assets |
| **Runtime asset inventory** | `docs/runtime-asset-inventory.md` + `scripts/check_runtime_asset_parity.py` — distinguishes packaged contract data from source-clone-only runtime assets |

**Deferred / out of scope for v0.6.9:**

| Component | Target |
|-----------|--------|
| Automatic repair execution | Future |
| Semantic repair verification | Future |
| Source support semantic proof graph | Future |
| Private/commercial benchmark suites | Future |
| Full policy packs (domain-specific) | Future |
| FrictionStore (long-term failure memory) | v0.7+ |
| Automatic taste learning or profile updates | Future |
| Autonomous in-Python agent execution | Intentionally deferred |

### 8.2 Phase Roadmap

| Milestone | Scope |
|-----------|-------|
| **v0.6.1** | Runtime state files + minimum artifact registry + stage blocking + decision event log + runtime parity + role handoff |
| **v0.6.2** | Minimum feedback issue schema + bounded repair planning controls (plan only) |
| **v0.6.3** | Deterministic quality gate reports |
| **v0.6.4** | Packaged public-safe evaluation cases for CI |
| **v0.6.5** | Optional deterministic provenance projection for audit/debug |
| **v0.6.6** | Workspace-local audience profile + frozen per-run snapshot |
| **v0.6.7** | Orchestrator control switchboard + explicit control selections |
| **v0.6.8** | Reader-facing source appendix during finalize |
| **v0.6.9** (current) | Runtime asset installer + asset inventory + install smoke hardening |
| **v0.7** | FrictionStore + full controlled self-improvement loop |
| **v0.2** | System paper — experimental evaluation complete |

---

## 9. Evaluation Framework

### 9.1 Evaluation Paradigm

Following Kapoor et al. (2024) *AI Agents That Matter*, MABW evaluates **agentic system behavior, not model output quality.** The evaluation dimensions are: failure localization, state recoverability, artifact validity, and operator auditability.

### 9.2 Verification Checks v0.1

State file correctness, artifact validation completeness, event log traceability, run ID consistency, handoff context exposure, control switchboard selection behavior, source appendix safety, and CLI output behavior — all executable against the v0.6.9 implementation baseline without experimental evaluation data.

### 9.3 Experimental Protocol Design

**Designed, not yet executed.** The initial comparison should not stop at "MABW versus a bare single LLM." A stronger protocol evaluates the control surface itself:

1. **Frozen-surface evaluation.** Accumulate feedback, gates, profile snapshots, and harness rules on one task set; freeze the surface; then evaluate on a held-out task set. This mirrors the LIFE-HARNESS protocol of evolving on training trajectories and evaluating a fixed harness on unseen tasks.
2. **Leave-one-surface-out ablation.** Run the same task set with selected surfaces disabled: quality gates, feedback/repair controls, audience snapshot, provenance projection, and control switchboard. This tests which control surface produces measurable system-behavior value.
3. **Cross-runtime transfer.** Run the same contract, handoff, state, and validation surface across Hermes, Claude Code, Codex, OpenCode, and manual fallback where practical. Runtime parity should be measured as transfer of control semantics, not identical model outputs.
4. **Prompt-optimized single-agent baseline.** Compare not only against a bare prompt, but also against a carefully optimized single-agent prompt. Otherwise the evaluation risks measuring prompt engineering effort rather than architecture.
5. **Failure diagnosis before investment.** Before adding new control surfaces, manually classify failed reference runs by earliest dominant bottleneck: contract exposure, procedural guidance, action/artifact validation, trajectory regulation, evidence grounding, audience fit, or model reasoning. The distribution should drive the next engineering investment.

The hypotheses remain system-behavior hypotheses: failure localization, state recoverability, artifact validity, cross-runtime control transfer, and operator auditability — not raw brief prose quality.

### 9.4 Threats to Validity

No real enterprise workload experiments; runtime parity verified at contract/handoff level only; baseline and ablation comparisons not executed; single-operator evaluation; deterministic gates cover only slices of the briefing problem; open-ended briefing lacks the binary reward signal available in deterministic agent benchmarks.

---

## 10. Related Work

MABW engages five research lines.

### 10.1 Multi-Agent Conversation Frameworks

MABW is distinguished from multi-agent conversation frameworks (AutoGen, CAMEL, MetaGPT) by its contract-governed, file-state blackboard coordination model. Roles are governed by contracts, not prompt negotiation; shared context is structured state files, not chat history; cross-stage decisions belong to the Orchestrator, not agent consensus.

### 10.2 Workflow and Control-State Systems

MABW instantiates workflow patterns (van der Aalst et al.) and blackboard architecture (Nii) in an LLM-agent context. It aligns with Anthropic's *Building Effective Agents* workflow pattern: clear inputs and outputs at every stage boundary.

### 10.3 Contract and Policy-Governed Software

MABW extends Design by Contract (Meyer) from code verification to agent behavior governance, and aligns with NIST AI RMF and ISO/IEC 42001 governance frameworks.

### 10.4 Provenance and Evidence Grounding

MABW's provenance schema is W3C PROV-compatible, designed to operationalize FActScore and ALCE verification patterns. v0.6.5 provides optional deterministic provenance projection for audit/debug review from existing control files.

### 10.5 Self-Improvement Systems

MABW's controlled self-improvement loop is distinguished from Self-Refine and Reflexion by structured, file-system-persisted, human-gated feedback routing. Feedback issues and repair plans are recorded as artifacts; RepairPlan is a proposal, not an automatic action.

### 10.6 LIFE-HARNESS: Runtime Harness Adaptation

Xu et al. (2026) released *Adapting the Interface, Not the Model: Runtime Harness Adaptation for Deterministic LLM Agents* in May 2026 (arXiv:2605.22166, v1 May 21, v2 May 27). LIFE-HARNESS evolves a structured runtime harness from training trajectories and applies it to frozen LLM agents, improving 116 out of 126 model–environment settings across 18 open model backbones, with an average relative gain of 88.5%. Harnesses evolved only from Qwen3-4B-Instruct trajectories and transferred to 17 other models without modification.

The architectural comparison is strongest when the axes are kept separate:

| Dimension | LIFE-HARNESS | MABW |
|-----------|-------------|------|
| Core thesis | Adapt the interface, not the model | Contract-governed, not model-dependent |
| Primary axis | Lifecycle interception points: before interaction, task conditioning, before execution, after execution | Governance domains: behavior, process/artifact, fact-grounding/evidence, quality/audience |
| Environment Contract | Tool descriptions, action constraints, policy rules exposed before interaction | Contracts, stage specs, artifact contracts, policy packs, handoff references |
| Procedural Skill | Retrieved procedures distilled from training trajectories | Audience/profile context, role skills, recipes, and future FrictionStore patterns |
| Action Realization | Validate, canonicalize, or block model actions before execution | Decision legality checks, artifact validation, CLI/source/action boundary checks |
| Trajectory Regulation | Detect loops, stagnation, repeated invalid retries, and budget exhaustion after execution | Partially absent: event log records decisions, but v0.6.9 has no first-class retry counters, repair-loop detection, or budget-driven narrowing of allowed decisions |
| Evolution mechanism | Offline trajectory-driven harness evolution from training data | Online human feedback → FeedbackIssue → RepairPlan → FrictionStore |
| Applicability | Deterministic agent benchmarks (ALFWorld, WebShop, OS, DB, τ-bench) | Enterprise briefing (policy-driven, compliance-sensitive, human-in-the-loop) |
| Reward signal | Binary task success in deterministic environments | Human review plus deterministic gates over limited slices of the task |
| Human role | Evaluator and reviewer of fixed-harness results | Operator and reviewer of feedback, repair plans, and improvement proposals |
| Model scope | 18 backbones, cross-model transfer demonstrated | Runtime parity: 5 runtimes sharing one contract surface |
| Validation | 126 controlled experiments, leave-one-layer ablation, prompt-evolving comparison | Architecture reference, public-safe evaluation cases, CI-validated regressions; ablation protocol not yet executed |

The differences are as instructive as the similarities. LIFE-HARNESS demonstrates that interface adaptation works when the environment is deterministic and the reward signal is clear. MABW studies the harder business-workflow setting where the reward signal is sparse, human, and often tacit. Deterministic gates therefore have strategic value beyond quality checking: each gate converts a small part of briefing from "no reward signal" into a weak, auditable reward surface that can support controlled improvement.

LIFE-HARNESS also exposes a missing MABW layer: trajectory regulation. MABW has legal decisions such as `retry_stage` and `delegate_repair`, and it records those decisions in `event_log.jsonl`, but v0.6.9 does not yet detect repeated repair loops, count retries per stage, or narrow `next_allowed_decisions` when a stage exhausts its attempt budget. A future control-surface patch should add stage-scoped retry counters, repair-loop detection, and budget-driven escalation to `request_human_review` or `block_run` without turning Python into the workflow executor.

### 10.7 Memory and Preference Systems

MABW's audience profile runtime surface draws from the Hermes agent's USER.md pattern — a plain-text, human-editable, agent-readable preference file frozen into a per-run snapshot. Unlike Hermes's agent-managed memory, MABW's profile is human-edited; the Orchestrator reads and summarizes it, but does not autonomously modify it.

---

## 11. Limitations & Future Work

**Known limitations v0.6.9:** No private/commercial evaluation data; specialist roles are contract-defined but not autonomously executing in Python; automatic repair execution, semantic fact verification, and full provenance proof remain deferred; event log is control trace; provenance graph is optional audit/debug projection (v0.6.5); source appendix is a reader-facing source list, not semantic proof; audience profile is context only — not automatically learned, updated, enforced, or routed; control selections record intent without executing selected controls; first-class trajectory regulation is not implemented yet (no stage retry counters, repair-loop detection, or attempt-budget-driven decision narrowing); FrictionStore and cross-run pattern detection deferred to v0.7+; no scale validation on real enterprise workloads.

**Design boundary:** MABW is a controlled self-improving system, not fully autonomous. Contracts govern correctness; `audience_profile.md` and its frozen per-run snapshot provide taste context — the contract architecture generalizes across domains; the profile is per-team, per-department, per-company. Generalization to non-briefing workflows requires domain-specific policy pack adaptation.

---

## Appendix A: Contract Schema Definitions

Per `configs/orchestrator_contract.yaml`. Four categories: Behavior (role boundaries), Process/Artifact (stage readiness, artifact expectations), Fact-Grounding/Evidence (claim traceability), Quality/Audience (reader-aligned delivery). Full YAML blocks in v0.1 report §A.

## Appendix B: Decision Vocabulary + Decision Record Schema

Six-term vocabulary (`continue`/`retry_stage`/`delegate_repair`/`request_human_review`/`block_run`/`finalize`) plus `decision_record_fields`. Stage-scoped legality table in `configs/stage_specs.yaml`.

## Appendix C: Runtime State File Schemas

Full field tables for `runtime_manifest.json`, `workflow_state.json`, `artifact_registry.json`, and `event_log.jsonl` — sourced from `src/multi_agent_brief/orchestrator/runtime_state.py`. Event types are defined by the `EVENT_TYPES` constant.

## Appendix D: Specialist Role Cards

10-stage pipeline table from `configs/stage_specs.yaml` with stage_id, owner, category, consumes, produces, and expected_artifacts.

## Appendix E: Implementation Evidence Map

Each architectural claim mapped to its source file in the repository.

## Appendix F: Glossary

Bilingual (English–中文) terminology. Key terms: contract-backed control surface, file-state blackboard, workflow control, decision vocabulary, runtime parity, controlled self-improvement, provenance schema, audience profile, taste layer.

---

*MABW Architecture Reference v0.1.2. Snapshot: v0.6.9, commit `8a36803`, branch `codex/069-install-runtime-asset-parity`. 2026-06-10.*
