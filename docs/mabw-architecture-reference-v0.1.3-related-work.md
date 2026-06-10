# MABW Architecture Reference v0.1.3 — Related Work (Standalone Chapter)

**Snapshot**: v0.6.9 | **Branch**: `main` | **Commit**: `ca4b952` | **Date**: 2026-06-10

> This chapter integrates five new papers (June 6–10, 2026) and revises the LIFE-HARNESS comparison per Mythos architectural review. It replaces the Related Work section in the v0.1.2 report and will be merged into the main reference document.

---

## Related Work

MABW sits at the intersection of five research lines. This section is organized by **architectural relevance to MABW**, not by publication order. Each subsection identifies the precise claim the cited work supports, the boundary between that work and MABW, and the citation strategy (independent convergence / informed attribution / distinguished from).

> **Design thesis.** MABW should not optimize for fluent reports; it should maintain an auditable workspace where grounded claims, required coverage, process-level gaps, repair plans, harness changes, and human overrides are all first-class state.

---

### 1. Harness Adaptation: Adapt the Interface, Not the Model

The core thesis of MABW—that agent improvement can be achieved by evolving the runtime interface rather than retraining model weights—has been independently validated by two parallel research programs in June 2026.

#### 1.1 LIFE-HARNESS (Xu et al., May 2026)

LIFE-HARNESS evolves a structured runtime harness from training trajectories and applies it to frozen LLMs, improving 116 out of 126 model–environment settings across 18 backbones (GPT, Claude, Llama, Qwen families) with an average relative gain of 88.5%. Harnesses evolved only from Qwen3-4B-Instruct transfer to 17 other models without modification.

**Convergence and divergence.** LIFE-HARNESS and MABW arrived at the same thesis—adapt the interface, not the model—from different directions: LIFE-HARNESS from controlled laboratory experiments on deterministic agent benchmarks; MABW from the operational needs of enterprise briefing. Their four-layer harness (Environment Contracts, Procedural Skills, Action Realization, Trajectory Regulation) is organized by **lifecycle interception points** (when in the agent loop the intervention applies), while MABW's four contracts are organized by **governance domain** (what kind of quality boundary the contract enforces). These are complementary decompositions of the same problem space, not structurally equivalent mappings.

**Trajectory Regulation: a missing layer in MABW.** LIFE-HARNESS's ablation study shows Trajectory Regulation is load-bearing—removing it causes an 86.5% performance drop in ALFWorld and 36.2% in Telecom. MABW's event log records every decision but does not yet enforce trajectory-level constraints. This is more urgent than the "v0.8 design item" framing suggests: an operator waiting an hour for a 15th repair-loop iteration while the control plane never alarms is a real failure scenario. v0.7 addresses this with zero-code doc-level constraints added to handoff usage rules: a recommended repair round limit (exceeding N rounds should trigger `request_human_review`), and a scope-narrowing rule (a repair that rewrites more than N sections should narrow scope or request human review). These are constraints a compliant Orchestrator respects. Code-level enforcement (retry counters, decision narrowing) remains v0.8.

**Experimental protocol for v0.8.** LIFE-HARNESS's evaluation methodology—frozen-after-evolution assessment, leave-one-surface-out ablation, cross-model transfer, and prompt-only evolving baseline—is the template for MABW's v0.8 baseline comparison protocol.

**Evidence asymmetry.** LIFE-HARNESS validates harness adaptation with 126 controlled model–environment experiments. MABW has 1,051 deterministic control-plane tests and zero LLM-execution-plane measurements. MABW has not yet demonstrated that human-designed contracts + human feedback achieve improvement rates comparable to trajectory-driven harness evolution. This is the central question for the v0.8 baseline comparison (080-2).

**Citation strategy:** The core thesis is independently convergent and merits parallel citation with separated timelines (LIFE-HARNESS arXiv v1 May 21, v2 May 27, 2026; MABW development began June 4, 2026, without knowledge of the paper). The trajectory-regulation gap analysis and experimental-protocol borrowing are informed/derived work with direct attribution.

#### 1.2 Self-Harness (Zhang et al., June 2026)

Self-Harness introduces a three-stage loop in which an LLM-based agent improves its own harness without human engineering: Weakness Mining identifies model-specific failure patterns from execution traces; Harness Proposal generates minimal, failure-tied harness modifications; Proposal Validation accepts candidate edits only after regression testing. Across three models from diverse families, held-out pass rates improved from 40.5%→61.9%, 23.8%→38.1%, and 42.9%→57.1%, respectively.

**Boundary between Self-Harness and MABW.** Self-Harness targets **deterministic reward domains** (Terminal-Bench), where pass/fail signals are machine-verifiable. MABW targets **open-domain briefing**, where the reward signal is human judgment and cannot be automated. Self-Harness proves that when a reward signal exists, agents can autonomously evolve their harness; MABW studies what happens when the reward signal is a person, requiring structured human feedback (FeedbackIssue → RepairPlan) and a human-gated approval path (Improvement Ledger). These are complementary settings within the same paradigm, not competing approaches.

**Model-specific harnesses: a design question with a near-term action.** Self-Harness emphasizes that weaknesses are model-specific—different base models exhibit distinct failure patterns, and harness modifications effective for one model may not transfer. This directly challenges MABW's invariant that five runtimes share an identical contract surface. If contract tuning proves model-specific, the Improvement Ledger schema and contract execution model would need revision (per-runtime contract variants). v0.7 mitigates this with a zero-cost preemptive action: the `source_evidence` frozen copy in ledger entries captures an optional `origin_runtime` field from the manifest at propose time, making the runtime provenance transparent without adding scoping/filtering behavior—which remains deferred to v0.8 cross-runtime transfer experiments.

**FrictionStore blueprint.** Self-Harness's three-stage loop (mine failures → propose minimal fix → regression-test before acceptance) is the direct architectural blueprint for MABW's FrictionStore + agent-drafted proposals in v0.8+. The eval-case suite (1051 deterministic tests) is MABW's regression-test infrastructure for this loop.

**Citation strategy:** Informed/derived work. Self-Harness postdates MABW's core design but provides the blueprint for FrictionStore automation. Direct attribution for the three-stage loop; explicit boundary statement on deterministic reward vs. human reward.

---

### 2. Multi-Turn Improvement Under Feedback

#### DRA Multi-Turn Feedback (Sabharwal et al., ICML 2026)

This paper evaluates deep research agents (DRAs) under two feedback settings—self-reflection (agent revises without external diagnostic signal) and process-level feedback (agent receives guidance targeting research-strategy gaps). Three findings directly validate MABW's architectural decisions:

**Finding 1: Self-reflection yields negligible net improvement.** Under self-reflection, agents incorporate and regress on rubric criteria at nearly equal rates. This is empirical evidence for MABW's refusal to rely on LLM self-critique as a quality mechanism—and validation of the design choice to use **deterministic external gates** (artifact validation, quality gate reports) rather than asking the model to check its own work.

**Finding 2: Process-level feedback produces substantial single-round gains.** A single round of process-level feedback yields an 8–15 point improvement and a 35–40% incorporation rate. MABW's FeedbackIssue → RepairPlan path operates on exactly this principle: feedback targets specific process gaps (which contract dimension, which stage, which artifact) rather than asking the agent to "write a better brief."

**Finding 3: Gains do not compound over subsequent turns.** When rewriting the full report to address remaining gaps, agents regress on up to 24% of previously satisfied criteria. This is the strongest empirical support yet for three MABW design choices:

- **Per-stage targeted repair** (RepairPlan's `target_stage`/`target_artifacts`) rather than full-report rewriting. If rewriting the whole report causes regression, repair must be scoped to the specific artifact and stage that failed.
- **Frozen per-run snapshots with human approval** (the improvement ledger's approve-then-materialize model) rather than multi-turn conversational revision. v0.7's "approve only appends a record; materialization occurs at next run start" directly addresses the regression problem.
- **Trajectory regulation**: repair-loop detection and retry-budget narrowing become empirically motivated, not just architecturally clean.

**Protocol borrowing for v0.8 — with MABW-specific metrics.** The incorporation/regression measurement framework is structurally useful, but DRA measured absorption of one-shot revision instructions within a single multi-turn report. MABW's mechanism is different: approved guidance is frozen into per-run snapshots and appears in every subsequent run's prompt surface—a persistent context model, not a one-shot revision instruction. MABW's v0.8 protocol must define its own two metrics: **guidance manifestation rate** (the fraction of approved ledger entries observably reflected in subsequent run outputs) and **guidance regression rate** (the fraction of previously manifested entries no longer reflected after later runs). The vocabulary is borrowed from DRA; the numbers must be earned independently.

**Citation strategy:** Independent convergence on the core finding (external structure outperforms self-reflection); informed attribution for the incorporation/regression measurement framework and experimental protocol.

---

### 3. Protocols for Auditable Human-Agent Collaboration

#### CHAP: Collaborative Human-Agent Protocol (Shahid et al., June 2026)

CHAP defines an open protocol for structured multi-human, multi-agent collaboration. Its core abstraction—workspaces, participants, tasks, artefacts, and an append-only evidence log—maps structurally to MABW's control surface:

| CHAP Concept | MABW Equivalent |
|-------------|-----------------|
| Workspaces with structured tasks and artefacts | Workspace directory with `config.yaml`, stage specs, artifact contracts |
| Append-only evidence log | `event_log.jsonl` |
| Human override → structured event with diff, rationale, content hash | `request_human_review` decision + RepairPlan with `reason` and `source_evidence` |
| Handoff between shifts → portable envelope | Runtime handoff protocol (handoff.md + handoff.json) |
| Human approval → non-repudiable signed decision | `approve --by <approver>` with event log record |

**Protocol and instance.** CHAP is a protocol specification—it defines message formats and interaction flows, not agent internals. MABW is a workflow engine—it defines stage pipelines, contract enforcement, and state file schemas for a specific domain (enterprise briefing). CHAP standardizes the inter-agent/inter-human communication layer; MABW implements the intra-workflow governance layer. Post-v1.0, MABW could evaluate CHAP-compatible evidence log output. No public commitment is made before v1.0.

**Citation strategy:** Independent convergence (CHAP and MABW developed without mutual knowledge). Direct attribution for protocol design concepts that map to MABW's control surface.

---

### 4. Evaluation Methodology for Grounded Generation

#### Precision Is Not Faithfulness (Santillana, June 2026)

This paper demonstrates that reference-free faithfulness metrics measure only precision (are stated claims supported?) and therefore reward abstention—a model can score near-perfect faithfulness by saying almost nothing. On a multilingual benchmark across 150 races, the most precise frontier model (precision 0.89) covered only 0.46 of relevant facts and ranked last by F1.

**Impact on MABW's quality gates.** MABW's three existing gates (Policy Material Terms, Market Quote Freshness, Target-Entity Relevance) are all **precision-side** checks—they verify that what is written is correct, not that everything relevant was covered. The paper provides theoretical grounding for adding a **coverage-side** complement:

- Precision gate: "Is every claim in the brief supported?"
- Coverage gate: "Are all screened-relevant facts present in the brief?"

MABW has a unique structural advantage for coverage checking: the screener stage produces `screened_candidates`—the set of facts judged relevant to the briefing task. The gap between `screened_candidates` and what appears in the final brief is **deterministically computable** by comparing candidate IDs in the claim ledger against the audited brief's citation references. What the gate actually guarantees is **no silent loss after screening**—that facts which survived screening are not dropped during analyst drafting or editor refinement. This is a mechanical comparison of two artifact surfaces, not a semantic completeness claim. It does not guarantee that the screener recalled all relevant facts; screener recall remains an open NLP problem. But DRA's 24% regression data shows that analyst/editor rewriting is itself a high-frequency loss path—and the gate catches exactly that.

**Coverage cannot be solved by better prompts.** The paper's prompt ablation found that explicitly instructing models to be comprehensive did not close the precision-coverage gap. This reinforces a MABW design decision: coverage is a gate problem, not a guidance problem. It belongs in deterministic checks, not in audience_profile.md guidelines.

**Anti-Goodhart design principle (elevated).** The paper empirically demonstrates Goodhart's law applied to evaluation: the most precise frontier model (grok-4.3, precision 0.89) covered only 0.46 of relevant facts and ranked last by F1. For MABW's gate design, this means: **every blocking precision gate must answer, before deployment, what the cheapest passing strategy is for an optimizing agent. If the cheapest strategy is content deletion, the gate must be paired with a mechanical coverage-side check.** This is now a gate-design rule, not an observation. A partial mitigation exists in production: a human-triggered finalize gate is the last coverage defense (a three-sentence brief won't pass a manager who needs to submit it). But pressure inside the agent loop still requires mechanical pairing—the principle stands.

**Citation strategy:** Informed attribution. The coverage-blindness finding provides theoretical grounding for MABW's planned coverage gate; the anti-Goodhart principle is adopted as a gate-design rule.

---

### 5. Multi-Agent Frameworks and Workflow Systems

MABW is distinguished from multi-agent conversation frameworks (AutoGen, CAMEL, MetaGPT) by its contract-governed, file-state blackboard coordination model. Roles are governed by contracts, not prompt negotiation; shared context is structured state files, not chat history; cross-stage decisions belong to the Orchestrator, not agent consensus.

MABW instantiates workflow patterns (van der Aalst et al.) and blackboard architecture (Nii) in an LLM-agent context. It aligns with Anthropic's *Building Effective Agents* workflow pattern: clear inputs and outputs at every stage boundary.

MABW extends Design by Contract (Meyer) from code verification to agent behavior governance, and aligns its contract categories with NIST AI RMF and ISO/IEC 42001 governance frameworks.

---

### 6. Provenance, Evidence Grounding, and Self-Improvement

MABW's provenance schema is W3C PROV-compatible, designed to operationalize FActScore and ALCE verification patterns. v0.6.5 provides optional deterministic provenance projection for audit/debug review.

MABW's controlled self-improvement loop is distinguished from Self-Refine and Reflexion by structured, file-system-persisted, human-gated feedback routing. The DRA multi-turn findings (§2) now provide the empirical evidence that self-reflection loops (Self-Refine, Reflexion) suffer from regression rates that offset incorporation rates—and that process-level feedback routed through a contract-anchored repair plan (MABW's approach) is the structurally superior alternative.

---

### 7. Memory and Preference Systems

MABW's audience profile runtime surface draws from the Hermes agent's USER.md pattern—a plain-text, human-editable, agent-readable preference file frozen into a per-run snapshot. Unlike Hermes's agent-managed memory, MABW's profile is human-edited; the Orchestrator reads and summarizes it, but does not autonomously modify it.

---

## Citation Table

| # | Paper | arXiv / Venue | Date | Relevance | Citation Strategy |
|---|-------|-------------|------|-----------|-------------------|
| 1 | LIFE-HARNESS (Xu et al.) | 2605.22166v2 | May 21/27, 2026 | Core thesis validation; trajectory regulation gap | Independent convergence + informed for protocol |
| 2 | Self-Harness (Zhang et al.) | 2606.09498v1 | Jun 8, 2026 | FrictionStore blueprint; model-specific harness question | Informed/derived; explicit boundary |
| 3 | DRA Multi-Turn (Sabharwal et al.) | 2606.09748, ICML 2026 | Jun 8, 2026 | Empirical validation of gates, targeted repair, snapshot model | Independent convergence + informed for protocol |
| 4 | CHAP (Shahid et al.) | 2606.09751v1 | Jun 8, 2026 | Protocol standard for auditable human-agent collaboration | Independent convergence; moat correction noted |
| 5 | Precision Is Not Faithfulness (Santillana) | 2606.09376 | Jun 2026 | Coverage-gate grounding; anti-Goodhart gate principle | Informed attribution |
| 6 | AutoGen (Wu et al.) | 2308.08155 | 2023 | Multi-agent conversation baseline | Distinguished from |
| 7 | CAMEL (Li et al.) | 2303.17760 | 2023 | Role-playing agent baseline | Distinguished from |
| 8 | MetaGPT (Hong et al.) | 2308.00352 | 2023 | SOP-encoded workflow baseline | Distinguished from |
| 9 | Workflow Patterns (van der Aalst et al.) | — | 2003 | Stage transition semantics | Engaged with |
| 10 | Blackboard Architecture (Nii) | — | 1986 | File-state blackboard lineage | Engaged with |
| 11 | Building Effective Agents (Anthropic) | — | 2024 | Workflow vs. agent distinction | Engaged with |
| 12 | NIST AI RMF 1.0 | — | 2023 | Governance framework alignment | Engaged with |
| 13 | ISO/IEC 42001:2023 | — | 2023 | AI management system alignment | Engaged with |
| 14 | W3C PROV | — | 2013 | Provenance data model | Engaged with |
| 15 | FActScore (Min et al.) | 2305.14251 | 2023 | Atomic claim verification | Engaged with |
| 16 | ALCE (Gao et al.) | 2305.14627 | 2023 | Citation evaluation | Engaged with |
| 17 | Self-Refine (Madaan et al.) | 2303.17651 | 2023 | Self-improvement baseline | Distinguished from |
| 18 | Hermes Agent (Nous Research) | — | 2025–2026 | Memory architecture inspiration | Engaged with |

---

## Action Items

1. **Evidence (§1.1)**: 080-2 baseline comparison is MABW's ticket from design document to system paper. No further revision needed—only execution.

2. **DRA metrics (§2)**: Define MABW-own metrics for 080 protocol: guidance manifestation rate and guidance regression rate. Do not cite DRA's 35–40% incorporation rate—mechanisms differ (persistent frozen context vs. one-shot revision).

3. **`origin_runtime` field**: Add optional `origin_runtime` to `source_evidence` frozen copy in Improvement Ledger entries, captured from manifest at propose time. v0.7 only does transparent labeling, no scoping/filtering. Do this before v0.7.0 ships—schema is not yet frozen.

4. **Coverage gate claim (§4)**: Reword from "complete coverage" to "no silent loss after screening." Add screener-recall limitation to docs. Cite Precision paper's prompt ablation as evidence that coverage cannot be solved by better prompts—it's a gate problem, not a guidance problem.

5. **Goodhart principle (§4)**: Elevate to gate-design rule: every blocking precision gate must answer what the cheapest passing strategy is; if content deletion, must pair with mechanical coverage check.

6. **CHAP (§3)**: Remove schema-standardization from moat inventory entirely. Public docs make no compatibility claim. Roadmap retains one-line post-1.0 note only.

7. **Trajectory regulation (§1.1)**: Add two doc-level constraints to PR3 handoff usage rules: repair round limit (N rounds → `request_human_review`) and scope-narrowing rule (rewrite > N sections → narrow scope or human review). Code enforcement stays v0.8.

8. **Capacity limit (memory)**: Document active entry ceiling (~20). Clarify that full injection is not a simplification—it is an auditability requirement (`applied_entry_ids` is deterministic; retrieval-based injection would destroy this property). Retrieval deferred to post-1.0; must first answer "how to audit a retrieval."

9. **Five honesty sentences adopted**: Evidence asymmetry (§1.1), model-specificity challenge (§1.2), DRA metric incomparability (§2), screener-recall limitation (§4), CHAP reality constraint (§3).

---

*MABW Architecture Reference v0.1.3 Related Work. 2026-06-10.*
