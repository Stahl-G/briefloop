# Control Surfaces

Chinese version: `docs/control-surfaces.zh-CN.md`.

This document is a control-surface ledger for MABW. It answers three questions for each surface:

- what it records or governs;
- who is allowed to write it;
- when it is reset, frozen, or promoted.

It is intended for maintainers, auditors, and architecture reviewers. For a writer-facing explanation, see `docs/what-mabw-keeps-track-of.md`.

## Counting Grain

MABW control surfaces can be counted at several grains:

| Grain | Count | Meaning |
|---|---:|---|
| Gate families | ~3 | High-level delivery and quality gates. |
| Subsystems | ~12 | Runtime state, evidence, feedback, memory, governance, and delivery groups. |
| File/surface level | ~28 | The practical ownership unit for "who can write what". |

This document uses the file/surface level because it matches the core governance rule:

> One field should have one writer.

## Status Labels

| Status | Meaning |
|---|---|
| Implemented | The surface exists in current code and is covered by deterministic CLI/tests. |
| Deferred v0.7.3+ | Accepted direction, not part of the v0.7.2 release. |
| Planned v0.8 | Accepted direction, deferred to the measurement / inference / role-topology phase. |
| Projection | Derived from source surfaces. It is not the source of truth. |

## Version Distribution

This ledger is also a release-freeze aid. The approximate distribution is:

| Version band | Surfaces | Interpretation |
|---|---:|---|
| v0.6.x | ~17 | Runtime state, artifact tracking, evidence, gates, feedback/repair, audience snapshots, provenance, and support/status docs. |
| v0.7.0 | ~5 | Improvement Ledger, deterministic memory projection, frozen improvement snapshots, manifest improvement metadata, and packaged improvement eval cases. |
| v0.7.2 | ~4 | Reader-final gate, stage/finalize completion transactions, Claude five-verb entrypoint, and Improvement Ledger supersession hygiene. |
| v0.11.x | ~4 | Reference samples, manifestation/regression reporting, coverage-side gates, and trajectory-regulation decision narrowing without repair execution. |

The exact count may change as related files are merged or split. The useful
unit is not the number itself, but whether each surface has a clear writer,
scope, source-of-truth status, and freeze/reset rule.

## Run-Scoped Process Control

These surfaces describe the state of a specific run. They live under `output/intermediate/` and can be archived or reset with the workspace run state.

| Surface | Role | Writer | Status | Freeze / Reset Rule |
|---|---|---|---|---|
| `runtime_manifest.json` | Run identity, runtime, paths, and manifest-level pointers such as improvement snapshot metadata. | Python | Implemented | Recomputed by runtime state initialization and handoff flows. |
| `runtime_manifest.json.improvement` | Ledger hash, memory projection hash, snapshot hash, and `materialized_entry_ids` for this run. | Python | Implemented | Frozen for the run; later ledger changes do not invalidate prior snapshots. |
| `workflow_state.json` | Current stage, stage statuses, last decision, next allowed decisions, and current-stage trajectory decision narrowing when budgets are exhausted. | Python via state commands | Implemented | Updated through runtime state commands; should not be hand-edited by agents. |
| `event_log.jsonl` | Append-only runtime/control events. | Python | Implemented | Append-only; records control decisions, transitions, and trajectory narrowing. |
| `artifact_registry.json` | Observed workflow artifacts and basic validation state. | Python | Implemented | Rebuilt/updated by state checks and artifact observation. |
| `orchestrator_control_switchboard.json` | Deterministic control recommendations for the Orchestrator. | Python | Implemented | Rebuilt from current workspace state and config. |
| `control_selections.json` | Orchestrator enable/defer/reject selections for recommended controls. | Python CLI from explicit Orchestrator/human selection | Implemented | Selection is a record, not execution. |
| `agent_handoff.md` / `agent_handoff.json` | Runtime-facing contract surface for the current run. | Python | Implemented; v0.7.1 hardening | Regenerated at handoff; should expose only frozen runtime context. |
| `stage complete` / `finalize complete` transactions | Deterministic completion records for stage/finalize transitions. | Python CLI invoked by Orchestrator | Implemented | Validates artifacts, updates registry/state, and appends transaction events; does not execute stages. |

## Run-Scoped Evidence And Correctness

These surfaces separate content from evidence. LLMs may draft content artifacts, but deterministic tools validate and record control state.

| Surface | Role | Writer | Status | Boundary |
|---|---|---|---|---|
| `candidate_claims.json` | Candidate factual claims extracted from sources. | Specialist runtime output, then validated | Implemented | Content artifact; not final proof by itself. |
| `screened_candidates.json` | Screened claims that should be preserved or intentionally excluded. | Specialist runtime output, then validated | Implemented | Coverage anchor for later brief generation. |
| `claim_ledger.json` | Claim-level source support used by downstream brief writing and audit. | Specialist runtime output, then validated | Implemented | Source/evidence surface, not taste memory. |
| `gates/auditor_quality_gate_report.json`, `gates/finalize_quality_gate_report.json` | Deterministic material-fact, freshness, target-relevance, and related gate findings. `quality_gate_report.json` remains a latest/legacy projection. | Python | Implemented | Stage-scoped reports can block unsafe auditor completion and finalize completion. |
| `audit_report.json` | Semantic audit findings from the Auditor role. | Auditor runtime role | Implemented | Semantic review; not a deterministic gate report. |
| `feedback_issues.json` | Structured human/audit feedback issues. | Python CLI from human/audit input | Implemented | Evidence for repair or future proposals; not guidance by itself. |
| `repair_plan.json` | Bounded repair plan for current feedback issues. | Python CLI | Implemented | Does not execute repair automatically. |
| `delta_audit_report.json` | Optional audit of repair delta. | Auditor/runtime output, then validated | Implemented when repair path is used | Run-scoped; not a long-term memory surface. |
| `source_appendix.md` | Audit/control copy of the source appendix appended into delivery Markdown/DOCX. | Python finalize | Implemented | Reader projection copy; not source evidence itself or a separate delivery file. |
| `provenance_graph.json` | Workspace-local audit/debug projection from existing control files. | Python | Implemented projection | Does not fetch sources, replay runtime, or prove semantic truth. |

## Workspace-Scoped Taste And Memory

These surfaces persist across runs. They can influence later runs only through explicit projection and per-run freezing.

| Surface | Role | Writer | Status | Boundary |
|---|---|---|---|---|
| `audience_profile.md` | Human-editable workspace-local audience profile. | Human / init defaults | Implemented | Taste context only; not source evidence or a correctness contract. |
| `output/intermediate/audience_profile_snapshot.md` | Frozen audience context for the current run. | Python | Implemented projection | Mid-run edits to `audience_profile.md` apply to later runs only. |
| `improvement/ledger.jsonl` | Append-only human-governed reader guidance ledger. | Python CLI from human approval | Implemented | Stores governance lifecycle, not runtime effect or output quality proof. |
| `improvement/memory.md` | Deterministic projection of approved materializable guidance. | Python | Implemented projection | Projection from ledger; not hand-authored source of truth. |
| `output/intermediate/improvement_memory_snapshot.md` | Frozen improvement memory for the current run. | Python | Implemented projection | Runtime reads this snapshot, not live `improvement/memory.md`. |
| `improvement/intake.jsonl` | Raw feedback intake and derivation links. | Python | Deferred | No lifecycle state; not a second ledger. |
| `improvement/candidates.jsonl` | Candidate parking lot for preferences/rules/fact review routes. | Python validator from agent/human proposals | Deferred v0.7.3+ | Candidates do not affect runtime until promoted and approved downstream. |
| `reference_samples/manifest.jsonl` | Manifest for accepted samples used as taste evidence. | Python / human workspace management | Planned v0.8 | Non-evidence; must not be scanned as source material. |

## Run-Scoped Preference Evaluation

This future surface measures whether approved guidance manifested in output. It is not a delivery gate.

| Surface | Role | Writer | Status | Boundary |
|---|---|---|---|---|

## Repo-Scoped Governance

These surfaces belong to the repository and change through versioned development, not workspace runs.

| Surface | Role | Writer | Status |
|---|---|---|---|
| `configs/orchestrator_contract.yaml` | Orchestrator authority, decisions, and contract categories. | Maintainers | Implemented |
| `configs/stage_specs.yaml` | Stage order and stage expectations. | Maintainers | Implemented |
| `configs/artifact_contracts.yaml` | Expected artifact contracts. | Maintainers | Implemented |
| `configs/policy_packs/*.yaml` | Public-safe policy defaults and boundary metadata. | Maintainers | Implemented |
| `eval-cases/` packaged cases | Deterministic regression cases for control-surface behavior. | Maintainers | Implemented |
| `docs/support-matrix.md` | Public capability/status map. | Maintainers | Implemented |
| `docs/architecture-status.md` | Current implementation state versus roadmap goals. | Maintainers | Implemented |
| `docs/red-lines-and-anti-patterns.md` | Public red lines and misuse patterns. | Maintainers | Implemented |

## v0.11.0 Freeze List

Freeze means the schema or command family receives a backwards-compatibility
promise and CI guards. Surfaces can remain implemented without being frozen.

| Surface | v0.11.0 freeze prerequisite |
|---|---|
| `event_log.jsonl` schema and event types | v0.7.2 completion transaction events and trajectory narrowing events must remain stable before freeze. |
| `workflow_state.json` and decision vocabulary | `stage-complete` / `finalize-complete` semantics are included; topology-satisfied stages are recorded as explicit workflow/event records, not hidden skips. |
| `runtime_manifest.json` | Single-writer preservation for `improvement` and `recipe` must stay covered. `operator_reported_model` is deferred to v0.7.3 / v0.8; reference-run summaries record model identity manually until then. |
| `artifact_registry.json` | Artifact names remain stable across default and strict topology: Scout may satisfy Screener, but `candidate_claims.json` and `screened_candidates.json` stay distinct artifacts. |
| `stage_specs.yaml` / stage order | Implemented topology satisfaction lets default topology mark Screener satisfied by Scout while strict topology keeps Screener independent. |
| `artifact_contracts.yaml` | The artifact contract set is preserved across topology modes; the candidate/screened coverage anchor remains an invariant unless migrated explicitly. |
| `orchestrator_contract.yaml` | Completion transaction semantics must be part of the frozen decision table. |
| Gate report schema and gate ids | Reader-final / process-residue gates and coverage-side gates must be settled before freeze. |
| Policy pack schema | Needs at least a second pack to prove generality. Pack contents are not frozen; they are a tuning layer. |
| `feedback_issues.json` / `repair_plan.json` | Current schema is eligible for freeze once repair-path regression coverage is stable. |
| Improvement Ledger schema | v0.7.2 schema hygiene is implemented: `supersedes_id`, duplicate warning, approved supersession fork rejection, and revert re-expose warning. Generic ledger provenance fields are deferred with `intake.jsonl` / `candidates.jsonl` to v0.7.3+. |
| `origin_runtime` | Implemented as audit/rendering metadata only. It is not filtering, routing, or materialization logic. |
| `improvement/intake.jsonl` / `improvement/candidates.jsonl` | Deferred to v0.7.3+ and too young for v0.11.0 freeze. |
| `improvement/memory.md` / improvement snapshot rendering | Can freeze only after the ledger schema is stable. |
| Runtime handoff format | Must include final usage rules and any v0.8 precedence table before freeze. |
| Five-verb writer entrypoint and core CLI families | Five writer verbs, completion transactions, gates, finalize, feedback, and improve command families must be reflected in support matrix and help before freeze. |
| Eval-case schema and runner actions | Needs final v0.7.2 control actions plus v0.8 evaluation-only surfaces before freeze. |
| `audience_profile.md` format | Format may freeze, but profile content remains human-editable and never freezes. |
| Reference sample manifest | Planned v0.8; experimental until at least one real usage cycle. |
| Manifestation report | Planned v0.8 evaluation-only. It must never become a runtime blocker. |
| Mode registry / role topology | Implemented for the supported default/strict role-topology contract. The default topology lets Scout satisfy Screener only when both candidate and screened artifacts exist; strict topology keeps Screener independent. This is workflow-shape control, not a speed or output-quality claim. |
| Support matrix | It defines the freeze promise and must be updated with every frozen surface. |

## Allocation Principles

### 1. Split By Quality Dimension

Correctness belongs to contracts, ledgers, evidence, and gates.

Taste belongs to audience and improvement surfaces.

Process belongs to runtime state, events, registry, and handoff.

If a requirement is machine-checkable, do not leave it only in memory.

### 2. Split By Writer

Python writes control records.

LLM/runtime roles write content artifacts.

Humans write approvals, reader guidance, and explicit run requests.

One field should have one writer. Mixed writers create ambiguous authority and weak audits.

### 3. Split By Authority

Smart components may propose but should not have direct authority.

Authoritative components should be deterministic.

Effective persistent changes should pass through humans.

Human-approved changes should leave traceable records.

### 4. Split By Scope

Run-scoped surfaces live under `output/intermediate/` and can be archived/reset.

Workspace-scoped surfaces persist across runs and must not be silently overwritten by upgrades.

Repo-scoped surfaces freeze with released versions.

### 5. Split Source From Projection

Ledgers and manifests are source/control records.

Memory files, snapshots, source appendices, provenance graphs, and display states are projections.

Display state should be computed when possible, not stored as mutable truth.

## Product Translation

For users, the same control surfaces should not be explained as a file inventory. The writer-facing version is:

```text
Where the brief stands.
Where each number came from.
What the system has learned with approval.
What is guarding delivery.
```

See `docs/what-mabw-keeps-track-of.md`.
