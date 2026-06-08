# MABW: A Contract-Governed Multi-Agent Workflow for Enterprise Briefing

## Architecture Reference v0.1.1 — Technical Report

**Snapshot**: v0.6.1 working state
**Branch**: `062`
**Commit**: `eff8bbc`
**Date**: 2026-06-08

---

## Abstract

Enterprise briefing relies on a craft that takes new analysts months to acquire: writing briefs that are not only factually correct, but also aligned with an invisible set of departmental tastes, cultural taboos, and unstated editorial preferences. Single-LLM approaches fail here twice — they cannot guarantee factual correctness (no audit trail, no repairability), and they cannot learn taste (each run starts fresh).

MABW (Multi-Agent Brief Workflow) addresses both gaps through a **contract-governed multi-agent workflow with a persistent memory layer**. Correctness is governed by four structured contracts enforced at stage boundaries through a file-state blackboard. Taste is carried by `audience_profile.md` — a plain-text, human-editable memory file that grows with use, analogous to the Hermes agent's USER.md. The Orchestrator reads this file at each run, produces a semantic summary, and injects it into stage handoffs. Contracts guarantee the brief is not wrong; taste guides it toward being right.

This report documents the v0.6.1 architecture, the v0.1.1 design update that adds the memory-and-taste layer, and the core design philosophy: **bringing the improvement-loop infrastructure that made coding agents successful — auditability, traceability, structured feedback, and human-gated repair — into real business workflows.**

---

> **About This Report.** This is a living architecture reference. v0.1.1 adds three major design elements absent from v0.1: (1) the memory-and-taste layer (`audience_profile.md` + Orchestrator semantic summary), (2) the formal separation between contract-governed correctness and profile-governed taste, and (3) explicit red lines that coding agents must never violate. Sections marked "Deferred" or "Planned" indicate design intent not yet implemented in v0.6.1. Chapter 8 provides the authoritative implementation snapshot.

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

---

## 2. Design Philosophy & Core Contributions

### 2.1 Two Kinds of Quality: Correctness and Taste

Enterprise briefing quality has two orthogonal dimensions:

| Dimension | What It Means | Examples | How It's Enforced |
|-----------|--------------|----------|-------------------|
| **Correctness** | The brief contains no factual errors, stale data, misattributed claims, or structural violations | Wrong stock price, missing source, claim without evidence, wrong entity name | **Contract enforcement** — structured YAML, schema-validated, mechanically checked at stage boundaries |
| **Taste** | The brief matches the department's editorial preferences, cultural norms, taboo topics, and unwritten expectations | "Don't give strategic advice without data," "use product-philosophy framing," "lead with risk disclaimers first" | **audience_profile.md** — plain natural language, human-edited, semantically interpreted by the Orchestrator |

Correctness can be mechanized. Taste cannot — it is learned through months of feedback cycles, passed down through implicit cultural norms, and varies between companies, departments, and individual managers. MABW's architecture treats these two dimensions as separate concerns with separate governance mechanisms.

### 2.2 The Memory-and-Taste Layer

v0.1.1 introduces `audience_profile.md` — a plain-text file in the workspace, analogous to the Hermes agent's USER.md. It carries:

- Editorial preferences ("lead with the key number, not background context")
- Cultural taboos ("never suggest strategic pivots without explicit data support")
- Structural conventions ("market data section goes before policy analysis")
- Departmental vocabulary and framing preferences
- Accumulated feedback patterns ("recurring issue: briefs are too long for Monday morning reads")

**Lifecycle:**

1. **Initial state**: A few lines of natural language written by the department head or senior analyst. No schema, no YAML, no structured fields required.
2. **Per-run**: The Orchestrator reads `audience_profile.md` at the start of each run, alongside contract references and state files. It produces a semantic summary of relevant taste guidelines for the current briefing task.
3. **Injected into handoffs**: The summary is included in the handoff context for every specialist role that generates or edits content (scout through auditor).
4. **Growth over time**: After each run, the human operator can add notes to `audience_profile.md` based on what went well or poorly. FeedbackIssues that reveal a taste-level problem (not a correctness problem) can be summarized into the profile. The file grows organically, like institutional memory — not like a database schema.
5. **No mechanical enforcement**: Unlike contracts, `audience_profile.md` has no schema validation, no blocking semantics, no pass/fail gate. It is semantically interpreted by the Orchestrator and the specialist roles. Drift is accepted — different runs may interpret the same profile slightly differently, just as different human analysts interpret the same manager's preferences differently.

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

**Contracts govern correctness. They do not govern taste.** Taste lives in `audience_profile.md` (§2.2). This separation is load-bearing: it means contracts change rarely (only when the workflow structure changes), while taste can be updated after every run by anyone who can write a sentence.

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

5. **Contracts govern correctness; audience_profile.md governs taste.** They use different formats (structured YAML vs. plain text), different enforcement mechanisms (mechanical validation vs. semantic interpretation), and different edit frequencies (rarely vs. after every run). They do not contaminate each other.

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
│  1. Read workspace (contracts + state files + audience_profile)│
│  2. Summarize taste guidelines from audience_profile.md        │
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
│ │scout/     │ │  │ .md               │
│ │screener/  │ │  │ (taste/memory)    │
│ │claim-     │ │  └──────────────────┘
│ │ledger/    │ │
│ │analyst/   │ │
│ │editor/    │ │
│ │auditor    │ │
│ └───────────┘ │
└──────────────┘
```
*Python tools execute locally via CLI and are not LLM-powered. Specialist roles execute on external LLM runtimes via the handoff protocol. `audience_profile.md` is read by the Orchestrator at the start of each run; a semantic summary of taste guidelines is injected into each specialist role handoff. Contracts govern correctness; the profile governs taste.*

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

Append-only JSON Lines file. Each event contains: `event_id`, `run_id`, `created_at`, `event_type` (one of 13 types), `actor`, `stage_id`, `artifact_id`, `decision`, `reason`, `metadata`. The event log is a control trace, not a full provenance graph (provenance deferred to v0.6.5).

### 3.4 Orchestrator Control Loop

#### 3.4.1 Decision Vocabulary

Six shared terms: `continue`, `retry_stage`, `delegate_repair`, `request_human_review`, `block_run`, `finalize`.

#### 3.4.2 Runtime Loop (v0.1.1 — updated with taste step)

1. Read workspace context: `config.yaml`, `sources.yaml`, `user.md`, **`audience_profile.md`**, inputs, handoff artifacts, and runtime state files
2. **Produce a semantic summary of taste guidelines from `audience_profile.md` relevant to the current briefing task**
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

Specialist roles (`scout` through `auditor`) are LLM-powered, contract-governed, and executed via external runtime handoff. Each role receives the Orchestrator's taste summary alongside its contract surface in the handoff context. Autonomous in-Python agent execution is intentionally deferred.

### 3.7 Python Tool / Validator / Renderer Layer

Three categories: **workspace lifecycle** (`init`, `state init`, `state check`), **stage-bound tools** (`doctor`, `sources decide`, `inputs classify`, `audit`, `finalize`), and **feedback/repair lifecycle** (deferred to v0.6.2: `feedback ingest`, `feedback plan`, `feedback resolve`, `feedback show`, `feedback validate`).

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

> **Status: Planned (v0.6.3).** Not implemented in v0.6.1.

Three planned gates: **Policy Material Terms** (regulatory/compliance accuracy), **Market Quote Freshness** (data timeliness and attribution), **Target-Entity Relevance** (entity accuracy and scope). These are workflow-internal operational controls, not evaluation benchmarks — distinct from G-Eval, MT-Bench, and other LLM-as-judge frameworks.

---

## 6. Controlled Self-Improvement Loop

> **Status: Deferred (v0.6.1).** Feedback/repair entirely deferred. v0.6.2 targets minimum controls.

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
| **R9** | NEVER put taste rules in contract YAML | Taste lives in `audience_profile.md`. Contracts govern correctness. Conflating them makes both unmaintainable |
| **R10** | NEVER require the human operator to read YAML or code | Non-AI-native users interact with CLI summaries, state files, and `audience_profile.md` natural language. Contracts are for the Orchestrator and the coding agent, not the end user |

An agent suggestion that violates any of these red lines is an architecture-level drift, not a minor adjustment. If a coding agent proposes "let analyst directly modify scout's artifact to save time," it simultaneously violates R1, R2, and R7.

---

## 8. Implementation Snapshot

> **Snapshot**: v0.6.1 working state | **Branch**: `062` | **Commit**: `eff8bbc` | **Date**: 2026-06-08

### 8.1 Component Status

**Stable — v0.6.1 implements:**

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
| **audience_profile.md support** | **New in v0.1.1 — read by Orchestrator, summarized, injected into handoffs** |

**Deferred — v0.6.1 defers:**

| Component | Target |
|-----------|--------|
| Feedback/repair loop | v0.6.2 (plan only, no auto-edit/execute) |
| Material-fact/freshness/relevance gates | v0.6.3 |
| Evidence execution graph | v0.6.5 |
| Public golden cases | Future |
| Fact-Grounding/Evidence Contract enforcement | v0.6.3 |
| Quality/Audience Contract enforcement | v0.6.3 |
| Full policy packs (domain-specific) | Future |
| FrictionStore (long-term failure memory) | v0.7+ |
| Autonomous in-Python agent execution | Intentionally deferred |

### 8.2 Phase Roadmap

| Milestone | Scope |
|-----------|-------|
| **v0.6.1** (current) | Runtime state files + minimum artifact registry + stage blocking + decision event log + runtime parity + role handoff + audience_profile.md Orchestrator integration |
| **v0.6.2** (next) | Minimum feedback issue schema + bounded repair planning controls (plan only) |
| **v0.6.3** | Quality gates + Fact-Grounding/Evidence contract enforcement |
| **v0.6.5** | Evidence execution graph + provenance (W3C PROV-aligned) |
| **v0.7** | FrictionStore + full controlled self-improvement loop |
| **v0.2** | System paper — experimental evaluation complete |

---

## 9. Evaluation Framework

### 9.1 Evaluation Paradigm

Following Kapoor et al. (2024) *AI Agents That Matter*, MABW evaluates **agentic system behavior, not model output quality.** The evaluation dimensions are: failure localization, state recoverability, artifact validity, and operator auditability.

### 9.2 Verification Checks v0.1

State file correctness, artifact validation completeness, event log traceability, run ID consistency, and CLI output behavior — all executable against the current v0.6.1 implementation without experimental baseline data.

### 9.3 Single-Agent Baseline Comparison

**Designed, not yet executed.** Three testable hypotheses compare MABW against a prompt-only single-LLM baseline on failure localization, state recoverability, and operator auditability — not brief quality scores.

### 9.4 Threats to Validity

No real enterprise workload experiments; runtime parity verified at contract/handoff level only; baseline comparison not executed; single-operator evaluation.

---

## 10. Related Work

MABW engages four research lines. It is distinguished from multi-agent conversation frameworks (AutoGen, CAMEL, MetaGPT) by its contract-governed, file-state blackboard coordination model. It instantiates workflow patterns (van der Aalst et al.) and blackboard architecture (Nii) in an LLM-agent context. It extends Design by Contract from code verification to agent behavior governance, and aligns with NIST AI RMF and ISO/IEC 42001 governance frameworks. Its provenance schema is W3C PROV-compatible, designed to operationalize FActScore and ALCE verification patterns. Its controlled self-improvement loop is distinguished from Self-Refine and Reflexion by structured, file-system-persisted, human-gated feedback routing. MABW's memory-and-taste layer draws from the Hermes agent's USER.md pattern — a plain-text, human-editable, agent-readable preference file that grows with use.

---

## 11. Limitations & Future Work

**Known limitations v0.6.1:** No evaluation data; specialist roles are contract-defined but not autonomously executing; two of four contract categories are reference-only; self-improvement loop entirely deferred; event log is control trace, not provenance graph; no scale validation.

**Design boundary:** MABW is a controlled self-improving system, not fully autonomous. Contracts govern correctness; `audience_profile.md` governs taste — the contract architecture generalizes across domains; the profile is per-team, per-department, per-company. Generalization to non-briefing workflows requires domain-specific policy pack adaptation.

---

## Appendix A: Contract Schema Definitions

Per `configs/orchestrator_contract.yaml`. Four categories: Behavior (role boundaries), Process/Artifact (stage readiness, artifact expectations), Fact-Grounding/Evidence (claim traceability), Quality/Audience (reader-aligned delivery). Full YAML blocks in v0.1 report §A.

## Appendix B: Decision Vocabulary + Decision Record Schema

Six-term vocabulary (`continue`/`retry_stage`/`delegate_repair`/`request_human_review`/`block_run`/`finalize`) plus `decision_record_fields`. Stage-scoped legality table in `configs/stage_specs.yaml`.

## Appendix C: Runtime State File Schemas

Full field tables for `runtime_manifest.json`, `workflow_state.json`, `artifact_registry.json`, and `event_log.jsonl` — sourced from `src/multi_agent_brief/orchestrator/runtime_state.py`. Event types (13) per `EVENT_TYPES` constant.

## Appendix D: Specialist Role Cards

10-stage pipeline table from `configs/stage_specs.yaml` with stage_id, owner, category, consumes, produces, and expected_artifacts.

## Appendix E: Implementation Evidence Map

Each architectural claim mapped to its source file in the repository.

## Appendix F: Glossary

Bilingual (English–中文) terminology. Key terms: contract-backed control surface, file-state blackboard, workflow control, decision vocabulary, runtime parity, controlled self-improvement, provenance schema, audience profile, taste layer.

---

*MABW Architecture Reference v0.1.1. Snapshot: v0.6.1, commit `eff8bbc`, branch `062`. 2026-06-08.*
