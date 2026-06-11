# Architecture Memo: Recursive Dogfooding — MABW's Contract Model Applied to Its Own Development

**Date**: 2026-06-11
**Context**: Six days of multi-agent collaboration (June 4–10, 2026) across one human Orchestrator, five specialized agent roles, and four worktrees
**Status**: Observation memo — not a design specification, but a record of what the development process itself revealed about the architecture

---

## The Observation

MABW is a contract-governed multi-agent workflow for enterprise briefing. Its development is also a contract-governed multi-agent workflow — for architecture design, literature analysis, code implementation, and adversarial review.

The same architectural pattern that MABW applies to briefing is being applied to MABW's own development. The pattern held. This memo documents what that revealed.

---

## 1. The Development Team as a Multi-Agent System

Over six days, the following agent roles emerged organically. None were pre-planned. Each acquired a distinct contract surface through role definition, not through prompt iteration.

| Role | Agent | Contract Surface | Primary Output |
|------|-------|-----------------|----------------|
| **Orchestrator** | Human (author) | Domain knowledge, taste judgment, cross-stage decisions, final approval on all design changes | Direction, prioritization, go/no-go on rulings |
| **Auditor** | Mythos (Claude, harsh-reviewer persona) | Read entire repo + all design docs; find architectural gaps; issue binding rulings with version targets and action items; reject designs that violate invariants | 35+ rulings across 3 rounds; each with version mapping and action items |
| **Analyst/Editor** | Researcher agent (Claude, technical-writer persona) | Digest rulings; write architecture reports, design notes, Related Work chapters; adversarial-review rulings before adopting them; never write code | 9 documents (~25,000 words); 18-paper bibliography; 3 architecture memos |
| **Specialist (Implementation)** | Coding agent (Claude, 07x worktree) | Implement Improvement Ledger per Mythos rulings; write tests; stay within contract boundaries | ~1,600 LOC; 35 passing tests; entry-revision model with SHA-256 chaining |
| **Design Ruling Agent** | Preference-taste design agent (Claude) | Read PROSE paper; issue 10-point binding ruling with version targets; classify every design element into correct layer; reject schema expansions that violate invariants | 10-point ruling with version slicing; candidate schema; route taxonomy; vocabulary separation table |
| **Screener/Compiler Agent** | Earlier design agent (Claude) | Read research papers; rank by architectural relevance; issue per-paper rulings on citation strategy and MABW boundary | Paper ranking and citation-strategy rulings for 5 papers |

**Key property**: No agent communicated directly with another agent. All coordination passed through the Orchestrator. All intermediate artifacts (memos, rulings, design notes) were written to the workspace as structured files. The audit trail of every design decision is fully reconstructible from the document history.

---

## 2. What Held: Contract Surfaces

The single most important factor in agent output quality was **role definition specificity**.

| Role Definition | Output Quality |
|----------------|----------------|
| "Harshest product-architecture reviewer. Find what's wrong. Rulings must be binding, versioned, with action items." | Mythos produced 35+ rulings with zero drift into implementation, zero softening into "suggestions," zero unversioned recommendations |
| "Technical writer. Write architecture reports and design notes. Never write code. Adopt rulings after adversarial review." | Analyst produced 9 documents with consistent tone, no code, and explicit ruling-to-text traceability |
| "Implement Improvement Ledger per these specific contract specs. 35 tests. Entry-revision model. Append-only." | Coding agent produced 1,600 LOC with 35 passing tests in one worktree session |

**When role boundaries were vague, quality degraded.** The coding agent, when asked to also handle the content pipeline during the reference run, skipped all control-plane bookkeeping — it treated the Orchestrator contract as background documentation. This is the Content/Control Decoupling failure (see `architecture-memo-content-control-decoupling-2026-06-11.md`). The fix was not a better prompt. The fix was a clearer contract boundary: "Python does transactions; LLM decides what and why." The `stage complete` command is that boundary made executable.

**What this teaches about MABW's Behavior Contract**: Role boundaries in an agent workflow are not cosmetic. They are the primary determinant of whether the system produces auditable work or plausible-looking drift. A Scout with a fuzzy contract surface will produce candidate claims that look right but cannot be traced. An Auditor with a fuzzy contract surface will produce self-review that sounds thorough but has no machine-verifiable evidence. The reference run's Content PASS / Instrumentation FAIL is the canonical demonstration.

---

## 3. What Held: Adversarial Review vs. Collaborative Review

The Mythos role was explicitly adversarial: "harshest product-architecture reviewer." This produced qualitatively different output than a collaborative reviewer role would have:

| Dimension | Adversarial (Mythos) | Hypothetical Collaborative |
|-----------|---------------------|---------------------------|
| Finds problems or confirms strengths? | Finds problems. Assumes the design is wrong until proven right. | Confirms strengths. Assumes the design is right unless obviously wrong. |
| Output format | Binding rulings with version targets and action items. "Rejected." "Must fix before v0.7.0 ships." | Suggestions. "Consider..." "You might want to..." |
| Ruling specificity | "Add `origin_runtime` field now. Schema is not frozen. v0.7.0 release would lock this in as a v2 migration." | "It might be useful to track which runtime learned a preference." |
| What happened to soft claims | All surfaced and rejected. "Evidence asymmetry" paragraph added. "CHAP moat" deleted. "Four-layer structural equivalence" corrected to "complementary decomposition." | Would have been noted in passing, not corrected. |
| Audit trail | Every ruling maps to a document change, a version, and an action item. | Would map to a conversation, not a document. |

**What this teaches about MABW's Auditor role**: The Auditor stage in the briefing workflow must be adversarial, not collaborative. An Auditor that confirms the Analyst's work is not an Auditor — it is an echo. The reference run's self-diagnosis ("I treated Auditor self-review as equivalent to deterministic gates check") is the briefing-domain instance of the same failure mode. The fix is the same in both domains: the Auditor does not write content; it produces an audit report with machine-verifiable findings; the Orchestrator decides based on that report.

---

## 4. What Held: Rulings Must Have Enforcement Tracking

A human code reviewer typically does not track whether their suggestions were implemented. In this collaboration, every Mythos ruling carried: (a) a version target, (b) an action item, and (c) a document location where the change would land. The Analyst/Editor then explicitly tagged each adopted ruling in the document edits. This created a closed loop:

```
Mythos ruling → Analyst adoption → Document change → Mythos re-review → Confirmation or revision
```

After three rounds, 35+ rulings had been tracked to completion. Zero were lost. Zero were "noted but not acted on."

**What this teaches about MABW's Improvement Ledger**: The approve → materialize → manifest-cite → re-evaluate loop that v0.7 implements for briefing guidance is the same pattern. A human reviewer's feedback must not evaporate into conversation. It must land as a structured record (FeedbackIssue → RepairPlan → Improvement Ledger entry), be tracked to its effect (manifest applied_entry_ids), and be re-evaluable (manifestation report). The development process proved this loop works at human scale. The reference run will prove whether it works at agent scale.

---

## 5. What Nearly Broke: Design Expansion Pressure

Every round of testing triggered proposals for new subsystems: Compiler, PROSE engine, precedence table, candidate parking lot, manifestation report, intake pipeline. The Mythos rulings consistently caged these into their correct versions. But the pressure was real: six days of intense design work produced more specification than could be implemented in two minor versions.

The discipline that held was: **issue count does not equal version count. Discoveries default to the 0.8 train. Boarding a 0.7.x train requires passing the gate: "without this fix, the reference run claim cannot stand."**

Three issues passed that gate. Seven were assigned to v0.8. Zero new version numbers were invented.

**What this teaches about MABW**: Contract-governed workflows need a scope-discipline mechanism. In MABW's briefing workflow, this is the Orchestrator's `block_run` and `request_human_review` decisions — they prevent scope creep within a run. At the development-process level, the equivalent mechanism is the Mythos role's version-gating function: "this is a real finding, but it rides the 0.8 train, not the 0.7.x train." Without this function, every discovery becomes an emergency, and the version map collapses into a single infinite release.

---

## 6. What This Means for MABW's Thesis

MABW's thesis is: **the same infrastructure that made coding agents improvable — test suites, git history, CI/CD, code review gates — can be built for enterprise briefing.**

The development process documented in this memo provides an intermediate data point:

**The same infrastructure — contract-governed role boundaries, adversarial review, structured feedback tracking, version-gated scope discipline — improved MABW's own architecture design process.**

This is not a controlled experiment. It is an existence proof: a multi-agent collaboration using contract governance produced architecture-quality output that survived adversarial review, with a fully reconstructible audit trail, across six days and five specialized agent roles.

The gap that remains — and that the reference run must close — is whether the same infrastructure works when the domain is enterprise briefing rather than architecture design, and when the content-producer agents are LLMs rather than a human Orchestrator with domain expertise.

---

## 7. Three Transferable Lessons

1. **Contract granularity must match role scope.** Mythos needed "find architectural gaps; issue binding rulings; reject violations." The coding agent needed "implement this specific module; follow these schema constraints; 35 tests." The same contract template applied to both would have failed both. MABW's stage_specs design — per-stage contract surfaces with stage-specific allowed_decisions — is the correct abstraction.

2. **Adversarial review produces qualitatively different output than collaborative review.** The difference is not a matter of degree. An agent asked to "find what's wrong" produces output that an agent asked to "give helpful feedback" structurally cannot produce. MABW's Auditor role should be designed as adversarial, not collaborative.

3. **Rulings without enforcement tracking are conversation, not governance.** The closed loop of ruling → adoption → re-review is what made Mythos's output binding rather than advisory. MABW's Improvement Ledger (approve → materialize → manifest-cite → manifestation report) is the same pattern applied to briefing guidance.

---

*Architecture Memo 2026-06-11. This memo is an observation, not a specification. Its claims about the development process are supported by the document history in `docs/` and the Mythos ruling record in `Documents/Mythos_对话_MABW_v0.7_产品架构_20260610.md`.*
