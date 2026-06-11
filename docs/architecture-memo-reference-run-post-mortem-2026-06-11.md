# Architecture Memo: Reference Run Post-Mortem — Issue Triage & Version Mapping

**Date**: 2026-06-11
**Context**: v0.7.1 reference run completed. Content pipeline PASS. Control pipeline FAIL. Ten issues identified.
**Status**: Triage complete. All fixes mapped to existing version vehicles. No new version numbers created.

---

## Core Finding

> The parts written as machines did not break. The parts entrusted to instructions all broke.

The ledger state machine, SHA-256 hash chain, frozen snapshot semantics, six-ring evidence chain, and hygiene injection defenses all survived their first real run intact. Every failure occurred in territory that was not yet enforced — only instructed. This distribution is itself the architectural argument: the system's deterministic infrastructure held; the gaps were precisely where enforcement had not yet replaced instruction.

---

## Issue Triage

### #1 Agent Role Design — Confirmed Design Debt, Not Test Defect

**What happened**: Seven specialist roles with overlapping responsibilities (Scout/Screener, Analyst/Editor) created unnecessary complexity during the run.
**Correction**: This is not a newly discovered problem. The ruling was already issued — compress to B-form (Scout absorbs Screener, Editor → Delivery Editor, Ledger slimmed). Implementation lands in v0.8 alongside the mode registry, before the v1.0 schema freeze.
**Why not 0.7.x**: The reference run validates the improvement loop and audit trail. Mixing role topology changes with control-plane hardening contaminates attribution. Opening a 0.7.x patch for this is precisely the scope drift to prevent.

### #2 Execution Strictness — Genuine P0, The Test's Most Valuable Finding

**What happened**: The LLM Orchestrator treated the contract as background documentation rather than executable API. It skipped all control-plane bookkeeping despite having instructions to perform it. The self-diagnosis — "I reduced the control loop to a stage list. I treated state files as telemetry, not write targets" — is preserved as primary evidence.
**Correction path** (already ruled):
- **0.7.1**: Handoff hardening B — mandatory wording in generated handoff templates + protocol verification
- **0.7.2**: Transaction layer C — `stage complete` / `finalize complete` atomic transactions that migrate bookkeeping from instruction to machine enforcement
**Classification**: This is the one genuine P0 among the three. LLMs are unreliable low-level transaction executors; instructions do not constitute execution guarantees. This is the reference run's most valuable empirical finding.

### #3 Preference Reliability — Half Defect, Half Misdiagnosis

**What happened**: Duplicate entries, no supersede mechanism, and operator error (forgot to approve) were observed. Natural-language guidance produced inconsistent effects across runs.
**Correction**:
- **Defect half (fix in 0.7.2)**: Add `supersedes` field to ledger entries. Add duplicate-entry warning at propose time. Add `provenance` field. Add hygiene discipline.
- **Misdiagnosed half (not a bug)**: Unstable natural-language guidance execution is not a defect — it is the layer's physics. "Soft" is its value as a cheap experimentation surface. "Stable, precise, verifiable" is the responsibility of templates, contracts, and gates. Fixing this by tightening memory would produce a memory that pretends to be a contract.
- **Deep half (v0.8)**: For checkable preferences, provide an escalation path (checkable_rule_candidate → promotion track). For uncheckable preferences, provide measurement (manifestation report). Do not conflate the two.

---

## Issues Not Initially Identified (Test Also Exposed)

### #4 De-identification Reversibility — Process Red Line

**What happened**: DemoCo facts create a fingerprint uniquely pointing to the author's employer. Public materials must use public-domain subject matter.
**Fix**: 0.7.1 — replace with solar-pack public-domain content before any public release.
**Classification**: Process red line, not technical debt.

### #5 Cross-Run Fact Drift — Unwatched

**What happened**: USTR dates were inconsistent across two runs. Within a run, "number → source" attribution is the auditor's and gates' responsibility. But cross-run consistency currently has no machine owner. The history module checks only duplication/novelty.
**Fix**: Record as known boundary. Write into limitations. Do not fix in 0.7.x.
**Classification**: Known limitation. v0.8+ consideration.

### #6 Guidance-Induced Regression — Live DRA Specimen

**What happened**: A useful "impact assessment" label was overwritten by a newly approved guidance entry. This is a wild instance of the exact regression behavior that DRA Multi-Turn (Sabharwal et al., ICML 2026) documented — previously satisfied criteria regressing after new feedback is applied.
**Fix**: Preserve as sample for 080 baseline comparison. Future argument for a format gate (structural-element preservation check).
**Classification**: Research artifact. No fix. Valuable evidence.

### #7 Runtime/Model Differences — Unrecorded

**What happened**: Sonnet stalled. Model identity was not recorded in the run manifest. Runtime-specific behavior differences are invisible to post-hoc analysis.
**Fix**: 0.7.1 — record model identity in run summary. Schema field for manifest deferred to v0.8.
**Classification**: Observability gap. Light fix now; schema work later.

### #8 Operational Process Gap — Human Error Surface

**What happened**: Operator forgot to run `improve approve`. No checklist existed.
**Fix**: 0.7.2 — operator checklist integrated into tutorial and quickstart.
**Classification**: Documentation debt.

### #9 Product Definition Debt — Pre-existing, Not Test-Induced

**What happened**: The author cannot describe how to use their own product. This was identified by Mythos before the reference run and remains unresolved.
**Fix**: 0.7.2 — product path (deferred from 0.6.10).
**Classification**: Pre-existing. Blocking for v1.0 usability claims.

### #10 (Meta) Design Expansion Pressure — On the Author, Not the System

**What happened**: Every round of testing triggers design expansion proposals (Compiler, PROSE engine, precedence table, candidate parking lot). The rulings consistently cage these into appropriate versions, but 0.7.x carries expansion pressure.
**Discipline**: Issue count does not equal version count. Discoveries are discoveries; releases are releases. New discoveries default to the 0.8 train. Boarding a 0.7.x train requires passing the gate: "without this fix, the reference run claim cannot stand."
**Status**: The gate held. Three issues passed. Seven were assigned to their correct vehicles.

---

## Version Mapping (No New Version Numbers)

| Version | Load | Issues Covered |
|---------|------|----------------|
| **0.7.1** | Reference run (solar-pack public content) + Handoff hardening B + Demo suite + Model identity in summary | #2 (instruction-level), #4 (de-identification), #7 (light fix) |
| **0.7.2** | Transaction layer C (P0) + Product path (deferred 0.6.10) + Preference hygiene pack (supersede, dup warning, provenance field, intake skeleton) | #2 (enforcement-level), #3 (defect half), #8, #9 |
| **0.8** | Role convergence (B-form) + Mode/pack + Coverage gate + Manifestation metrics + 080 baseline experiments | #1, #3 (deep half), #5 (limitation), #6 (sample) |

One sentence for the roadmap: three problems, two 0.7.x trains, one 0.8 train, zero new version numbers invented.

---

*Architecture Memo 2026-06-11. Complements `docs/architecture-memo-content-control-decoupling-2026-06-11.md` (the initial failure analysis) and `docs/design-note-preference-taste-governance-2026-06-11.md` (the preference governance specification).*
