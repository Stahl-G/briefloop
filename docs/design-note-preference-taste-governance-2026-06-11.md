# Design Note: Preference & Taste Governance System

**Date**: 2026-06-11
**Status**: Design specification — v0.7.2 foundations, v0.8 engine
**Inputs**: PROSE paper analysis (Apple/Colorado, ICML 2025), 10-point agent ruling, PR2.5 intake design
**Relationship**: Complements `docs/mabw-architecture-reference-v0.1.3-related-work.md` §7; v0.2 paper §"Preference Inference vs. Preference Governance"

---

## North Star

> The system never learns anything the user has not approved.

MABW may use LLMs to help users discover, decompose, interpret, and candidate preferences. It must never allow an LLM to directly change the behavior of future runs. All taste, preference, and structure guidance that affects future output must pass through an auditable record, a deterministic validation check, human adoption, and a per-run frozen snapshot.

---

## 1. The Parent Pattern

Every component in the Preference & Taste System obeys one invariant:

| Layer | Is Smart? | Has Power? | Leaves a Trace? |
|-------|-----------|------------|-----------------|
| Candidate Engine (LLM) | Yes | No | Proposes candidates only; no effect on future runs |
| Validator (Python) | No | Yes (reject) | Deterministic pass/fail + warnings |
| Human adoption | Yes | Yes (approve) | `approved_by` + `approved_at` in ledger |
| Improvement Ledger | No | Yes (materialize) | Append-only, revision-chained, SHA-256 verified |
| Per-run snapshot | No | No | Frozen at run start; manifest-cited hash |
| Manifestation Report (LLM) | Yes | No | Evaluates; does not block; does not write back |

No single component is simultaneously smart, authoritative, and untraceable.

---

## 2. Layered Architecture

```
Natural feedback / accepted samples
  → Intake Log (improvement/intake.jsonl)
  → Agent-side compiler (PROSE-inspired inference, v0.8)
  → Candidate Parking Lot (improvement/candidates.jsonl)
  → Deterministic validation (Python)
  → Human adoption (improve approve / adopt)
  → Improvement Ledger (improvement/ledger.jsonl)
  → Next-run frozen snapshot (output/intermediate/improvement_memory_snapshot.md)
  → Manifestation evaluation (output/intermediate/guidance_manifestation_report.json, v0.8)
```

### File Map

| File | Role | Version |
|------|------|---------|
| `improvement/intake.jsonl` | Raw feedback entry; no lifecycle | v0.7.2 |
| `improvement/candidates.jsonl` | Preference/rule candidate parking lot; immutable records + tombstones | v0.7.2–0.7.3 |
| `improvement/ledger.jsonl` | Human-adopted audience guidance; append-only audit ledger | v0.7.0 |
| `improvement/memory.md` | Deterministic projection from ledger; injected into handoff | v0.7.0 |
| `output/intermediate/improvement_memory_snapshot.md` | Per-run frozen snapshot; manifest-cited SHA-256 | v0.7.0 |
| `reference_samples/manifest.jsonl` | Accepted report samples as taste evidence; non-evidence to content pipeline | v0.8 |
| `output/intermediate/guidance_manifestation_report.json` | Per-run evaluation of guidance manifestation; read-only | v0.8 |

---

## 3. Component Boundaries

### 3.1 Intake Log

**Does**: save raw user feedback with source reference, hash, and derived object references.  
**Does not**: approve, reject, or assign lifecycle status.  
**File**: `improvement/intake.jsonl` — append-only, workspace-local.

### 3.2 Candidate Parking Lot

**Does**: store immutable preference/rule/fact-review candidates with deterministic validator conclusions.  
**Does not**: use the words `approved`, `applied`, `materialized`, `verified`, or `rejected`.  
**File**: `improvement/candidates.jsonl` — append-only, workspace-local. Tombstone records mark discarded candidates. Display states (`pending`, `promoted`, `discarded`) are computed at read time: `promoted` derives from ledger `source_evidence` back-references to `candidate_id`; `discarded` derives from tombstone records.

**Schema** (per record, immutable):

```json
{
  "candidate_id": "PC-0001",
  "created_at": "2026-06-11T00:00:00Z",
  "route": "memory_guidance",
  "provenance": "machine_proposed",
  "derived_from": {
    "intake_ids": ["FI-0001"],
    "sample_ids": []
  },
  "user_visible_summary": "以后每条新闻先说明对公司的影响。",
  "candidate_text": "Lead each major news item with its company-specific implication before background context.",
  "risk_flags": [],
  "validator": {
    "result": "valid",
    "warnings": []
  }
}
```

**Forbidden vocabulary in candidates**: `approved`, `applied`, `materialized`, `verified`, `rejected`. These words belong to the ledger and manifest (applied/materialized/approved) or to the human (rejected). Candidates are proposals, not decisions.

### 3.3 Improvement Ledger

**Does**: store human-adopted audience guidance with full audit trail.  
**Does not**: store fact corrections, gate rules, contract parameters, or repair plans.  
**Provenance field** (added v0.7.2): `human_authored` or `machine_proposed`. Machine-proposed entries require: full per-entry display (no batch approval), human confirmation gesture per entry, and the option to edit—which flips provenance to `human_authored`. Machine-proposed entries materialize no earlier than v0.8.

### 3.4 Reference Samples

**Does**: preserve accepted weekly reports as taste evidence for preference inference.  
**Does not**: enter the content pipeline as sources, appear in source discovery scans, or enter the Claim Ledger.  
**Location**: `reference_samples/accepted/*.md` — workspace-local, never in repo, never in public fixtures.  
**Hard boundary**: `reference_samples/` is marked `non_evidence` in its manifest. The only permitted reader is the preference inference skill (agent-side, temporary read). Candidates must not quote sample text verbatim—only reference `sample_id` and hash.

### 3.5 Manifestation Report

**Does**: evaluate whether approved guidance is observably present in run output, using per-component match (PROSE-inspired PPCM).  
**Does not**: block finalize, gate progression, or write back to ledger state.  
**File**: `output/intermediate/guidance_manifestation_report.json` — run-scoped evaluation artifact. Results are shown to the human operator. `contradicted` findings are routed to human decision (supersede/revert), not to automatic state transitions.

---

## 4. Route Taxonomy

Five routing destinations for compiler output:

| Route | Target | Example |
|-------|--------|---------|
| `memory_guidance` | Improvement Ledger (audience_guidance) | "Lead with business impact before policy context" |
| `checkable_rule_candidate` | Rule candidate → staging → promotion track | "Structure: implication → fact → uncertainty per news item" |
| `fact_review` | FeedbackIssue → RepairPlan → Fact-Grounding contract | "This number contradicts last week's reported figure" |
| `already_enforced` | No action — inform user of existing guarantee | "Source appendix is already required by finalize gate" |
| `out_of_scope` | No action | "Learn my personal writing style over time" |

`already_enforced` is load-bearing: it prevents the system from "learning" what it already guarantees, which would produce false confidence in self-improvement.

---

## 5. Vocabulary Separation

Three lexicons, one iron rule:

| Domain | Allowed Terms | Forbidden Terms |
|--------|--------------|-----------------|
| Deterministic machine (Python) | `valid`, `validated`, `verified` | — |
| LLM judgment (agent) | `assessed`, `sample_supported` (with n/k count) | `verified`, `validated` |
| Human | `approved`, `rejected` | — |

LLM judge output must never be labeled `verified`. This goes into schema comments and documentation. The first person to mix these terms will not be the author—it will be a contributor six months from now.

---

## 6. Precedence Table

Written into handoff templates and architecture documentation. Controls style/taste conflicts only:

```
contracts / policy / source-supported facts
> quality gates
> current-run repair plan
> current-run explicit request (user.md, brief_request)
> improvement_memory_snapshot
> audience_profile_snapshot
> model default style
```

**Critical caveat**: control-plane obligations (decision recording, gates, bookkeeping, event log, manifest) are not in this table. They are unconditional, non-overridable, and must never be "deprioritized" by any taste or style layer. If the Orchestrator ever cites this table to justify skipping bookkeeping, the table has been misused.

---

## 7. Version Slicing

### v0.7.2 — Deterministic Foundation

- Transaction layer (`stage complete` / `stage block` / `finalize complete`): P0
- Product path: P1
- `improvement/intake.jsonl` skeleton
- `improvement/candidates.jsonl` schema + validator (if capacity allows; may slip to v0.7.3)
- Ledger `provenance` field (`human_authored` / `machine_proposed`)
- Route includes `already_enforced`
- Documentation: non-evidence samples boundary; no Python LLM calls

### v0.7.3 — Candidate CLI

- Candidate list / show / discard commands
- Promote candidate → `improve propose` pathway
- Basic agent handoff for feedback compiler

### v0.8 — Inference Engine + Evaluation

- `reference_samples/` manifest
- PROSE-inspired agent skill (refine → breakdown → consistency verification)
- Cross-sample consistency assessment
- `guidance_manifestation_report.json`
- PPCM-like per-component evaluation
- Precedence table in runtime handoff
- Machine-proposed entries eligible for materialization

---

## 8. Relationship to PROSE

PROSE (Aroca-Ouellette et al., ICML 2025) solves preference **inference** from user writing samples through iterative refinement and cross-sample consistency verification. MABW's Preference & Taste System solves preference **governance**—who approves, when it takes effect, how it is audited, and how it is reverted.

PROSE's algorithmic structure (refine → breakdown → verify across samples) is adopted as the design blueprint for MABW's v0.8 inference engine. No Apple code is ported. Prompts are written clean-room from the paper's algorithmic description. The paper is cited in v0.2 as the inference-end counterpart to MABW's governance-end design.

Together with the Hermes memory analysis (§7 of the v0.1.3 Related Work), PROSE and MABW form a three-point spectrum:

| System | Solves | Mechanism |
|--------|--------|-----------|
| PROSE | Preference inference | Iterative refinement + cross-sample consistency from user writing |
| Hermes Memory | Preference memory | Agent-managed USER.md; tool-driven self-curation |
| MABW | Preference governance | Human-adopted, validator-checked, ledger-recorded, per-run-frozen, manifest-cited audit trail |

---

## 9. Product Story

What the user sees:

> You gave feedback. The system broke it into three confirmable interpretations:
> 1. A writing habit (tone/structure preference)
> 2. A format rule candidate (checkable structure)
> 3. A fact/source check
>
> After you confirm, only what you confirmed affects the next run.

What the system does:

```
feedback → intake → candidates → validation → human adoption
→ Improvement Ledger / rule candidate / FeedbackIssue
→ next-run frozen snapshot
→ manifestation report (evaluation only)
```

One-sentence positioning:

> The system never learns anything you have not approved.

A black-box "auto-learns your style" system cannot answer four questions: what was learned, who approved it, when it took effect, and where the evidence is. MABW answers all four with file-level evidence.

---

*Design Note 2026-06-11. Complements architecture memo `docs/architecture-memo-content-control-decoupling-2026-06-11.md`. Specifications in this note are binding for v0.7.2+ development.*

---

## References

1. **PROSE — Aligning LLMs by Predicting Preferences from User Writing Samples.** Stéphane Aroca-Ouellette, Natalie Mackraz, Barry-John Theobald, Katherine Metcalf. ICML 2025 (poster). arXiv/ICML 2025. Apple/University of Colorado. Code: `https://github.com/apple/ml-predict`.
   - Core contribution: iterative refinement + cross-sample consistency verification for preference inference from user writing demonstrations. 33% improvement over CIPHER. Prompt ablation shows 11% performance drop when preferences are reordered rather than kept in the LLM's own phrasing.
   - MABW relationship: PROSE solves preference *inference* from writing samples. MABW solves preference *governance*—who approves, when it takes effect, how it is audited. Algorithmic structure (refine → breakdown → consistency verification) adopted as design blueprint for v0.8 inference engine. No code ported; clean-room prompt design. Cited in v0.2 paper §"Preference Inference vs. Preference Governance."

2. **Hermes Agent Memory.** Nous Research. `https://hermes-agent.nousresearch.com/docs/user-guide/features/memory`.
   - Core contribution: dual-file memory (USER.md + MEMORY.md), tool-driven self-curation, frozen snapshot injection at session start, capacity-aware consolidation.
   - MABW relationship: MABW borrows the surface pattern (plain-text, human-editable preference file). MABW builds its own governance infrastructure (append-only ledger, revision chaining, human-gated approval, per-run freezing, manifest citation) because Hermes memory is a storage mechanism, not an auditable governance surface. See v0.1.3 Related Work §7 for the detailed analysis of five architectural boundaries that direct Hermes-memory adoption would break.
