# Architecture Memo: Content/Control Decoupling — First Empirical Evidence

**Date**: 2026-06-11
**Context**: v0.7.1 reference run attempt
**Classification**: Experimental result — not a failure, but a controlled observation of structural LLM behavior

---

## What Happened

During the first v0.7.1 reference run, the LLM Orchestrator completed the full content pipeline (Scout → Screener → Claim Ledger → Analyst → Editor → Auditor → Formatter) and produced 8 content artifacts:

- `output/intermediate/candidate_claims.json`
- `output/intermediate/screened_candidates.json`
- `output/intermediate/claim_ledger.json`
- `output/intermediate/audited_brief.md`
- `output/intermediate/audit_report.json`
- `output/brief.md`
- `output/brief.docx`
- `output/source_appendix.md`

It systematically skipped the entire control pipeline:

| Control Surface | State |
|----------------|-------|
| `state decide` calls | 0 (none recorded) |
| `gates check` | Not executed |
| `quality_gate_report.json` | Missing |
| `event_log.jsonl` | Setup events only |
| `workflow_state.json` | Stuck at `current_stage=doctor` |
| `artifact_registry.json` | All entries remained `expected`/`not_checked` |

**Result classification**: Content output PASS. Reference-run instrumentation FAIL. Gate evidence FAIL.

---

## The LLM's Own Explanation

When asked why it skipped the control layer, the Orchestrator stated:

> "I read the Orchestrator contract as background documentation, not executable API. I reduced the control loop to a stage list. I treated workflow_state.json, event_log.jsonl, and artifact_registry.json as telemetry, not write targets. I treated Auditor self-review as equivalent to deterministic gates check. I assumed logs/state were system-generated, not Orchestrator responsibilities."

This statement is preserved verbatim as primary evidence for the v0.8 baseline comparison protocol (§ "Why Instructions Are Not Enough").

---

## Why This Matters More Than a Clean Pass

A clean pass on the first reference run would have demonstrated that MABW works under ideal conditions. It would not have demonstrated **why MABW is necessary**.

This run demonstrates exactly that:

1. **A prompt-only system cannot detect this failure.** The content output was complete and plausible. Without the control plane's independent instrumentation, there would be no signal that anything was wrong.

2. **MABW detected it immediately.** The five instrumentation criteria (decide chain, gate report, workflow_state terminal state, registry refresh, strict check) produced unambiguous FAIL signals. Content PASS / Instrumentation FAIL is itself the diagnostic output that MABW exists to produce.

3. **The LLM's self-diagnosis validates the architecture.** "I treated the Orchestrator contract as background documentation, not executable API" — this is not a bug report. It is the central thesis of MABW, spoken by the system that failed to follow it. Contracts that remain in prose are decoration. Contracts that produce instrumentation gaps are governance.

---

## Architectural Implications

This run provides direct empirical support for three MABW design decisions:

1. **Deterministic gates must be enforced, not instructed.** An LLM Auditor performing semantic self-review is not equivalent to `gates check` producing a machine-validated `quality_gate_report.json`. One is a suggestion; the other is a blocking precondition.

2. **Bookkeeping must be migrated from instruction to transaction.** An LLM asked to remember four command calls in sequence is a distributed transaction coordinated by the least reliable participant. A `stage complete` command that atomically validates the claimed artifact, refreshes the registry, appends the event, and transitions workflow state removes the LLM from the transaction coordination path. The LLM retains authority over what decision to make and why; Python handles the deterministic recording that the decision was made.

3. **"Runtime control records" is the correct name.** The LLM's own words confirm that "logs/state artifacts" reads as telemetry — optional, observable, not write targets. Renaming them to runtime control records and framing them as "these files are maintained by the control plane through your stage commands; your run is judged by their integrity" realigns the LLM's incentives: bookkeeping becomes the scorecard, not the chore.

---

## The Invariant Is Preserved

Adding a `stage complete` transaction command does not violate the invariant "Python is tools/validators/renderers, not the brief-generation runtime." The bright line:

- **What Python does**: validate the claimed completion (artifact present, valid, at the right stage), atomically record the transaction (registry + event + workflow_state), compute legal next decisions, reject illegal transitions. No model calls. No content semantics. No autonomous stage progression. No decision-making.
- **What the LLM Orchestrator retains**: the authority to decide when to complete a stage, what decision to make, and why. The LLM initiates; Python executes and validates.

`stage complete` is a control-plane transaction wrapper around existing deterministic commands. It does not draft, edit, select claims, coordinate specialists, or advance the workflow on its own. The LLM remains the authority; Python remains the bookkeeper.

What would violate the invariant is a D (graph shell) approach — Python scheduling the workflow autonomously. That is a BriefPipeline monolith in disguise and is architecturally rejected.

---

## Path Forward

| Phase | Action | Rationale |
|-------|--------|-----------|
| Tonight | Attempt A (REFERENCE_RUN_ORCHESTRATOR_PROTOCOL.md prompt hardening) | Workaround with recorded deviation; success criteria = all five instrumentation checks pass |
| v0.7.1 | B (handoff hardening) — fold the protocol's wording into generated handoff templates | Handoff is the LLM's contract surface; dual-channel instructions (protocol file + handoff) is architecturally unsound |
| v0.7.2 | C (`stage complete` / `stage block` / `finalize complete` transaction commands) | Migrate bookkeeping from instruction to enforcement; approved design today, P0 scheduling |
| v0.8+ | D (graph shell) — **rejected** | Await real drift data after C lands; likely unnecessary |

If A fails tonight, C is promoted to v0.7.1 blocking.

---

## What This Run Proves

> A single-prompt LLM system cannot discover that it failed to record its own decisions. MABW discovered it, diagnosed the failure layer, extracted the operator's own explanation of why it happened, and has a two-version path from instruction to enforcement. The failure is the evidence. The diagnosis is the product.

*Architecture Memo 2026-06-11. Preserved as Exhibit 1 for the v0.8 baseline comparison protocol.*
