# BriefLoop: Open-Source Loop Engineering for Auditable Business Briefings

## Architecture Reference v0.6.1: Dynamic Knowledge Conflicts, Evidence Governance, and Release Authority

| Field | Value |
|---|---|
| Report edition | v0.6.1 |
| Product baseline | v0.12.1, including merged changes #513–#515 after the `v0.12.1` tag (`c2a09157`) |
| Code snapshot | `main@47ae439d0206a852a2a223db4051d28f39b54c38` |
| Branch | `main` |
| Report date | 2026-07-19 |
| Evaluation status | Planned; not executed or frozen |
| v1.0 first-user evidence | `not_satisfied` |
| Formal reference set | 42 entries; 2 new 2026 preprints in this edition |

> **Version boundary.** This report describes the implementation state at `main@47ae439d0206a852a2a223db4051d28f39b54c38`. The version file is v0.12.1, but this exact head is later than the `v0.12.1` tag: it additionally includes a unified internal-citation parser, citation-span boundary repairs, and closure of the WorkBuddy pilot contract. The report therefore distinguishes release-tag capabilities from current-`main` capabilities and does not back-port post-tag repairs as evidence for the tag. `run` remains a handoff launcher for an external runtime, not a Python briefing generator. Test count describes regression surface area; it does not prove prose quality, semantic truth, or first-user success. `docs/architecture-status.md` and `docs/support-matrix.md` remain authoritative for stability. `docs/v1-pilot-evidence.md` still records `not_satisfied`; approaching 1.0 does not prove output quality, first-user usability, or management readiness.

## Abstract

Periodic enterprise briefings are time-bounded release judgments made where stale parametric memory, newly retrieved evidence, and mutually inconsistent sources may disagree. BriefLoop is an open-source control system for this setting. It treats AI-assisted reporting not as one-shot document generation but as governed production of claims, evidence, failures, repairs, and release decisions that can be inspected, traced, and reviewed.

The system separates agentic interpretation from authority. Agents may discover sources and propose claims; deterministic transactions validate schemas, preserve frozen artifacts, record state transitions, apply gates, and expose unresolved gaps; humans adjudicate semantic conflicts and authorize delivery. The current snapshot includes runtime state, claim and evidence surfaces, quality gates, recovery, auditable delivery artifacts, and product-oriented report packs. Experimental support-sufficiency surfaces can record claim-evidence proposals and human decisions, but they do not prove semantic truth or grant release authority.

This edition frames dynamic knowledge conflict as a structural risk for recurring briefings. Prior work shows that recent context may fail to override parametric memory and that retrieved sources may conflict. BriefLoop does not claim automatic conflict detection, temporal truth adjudication, or measured content-quality gains. It provides traceability, limited temporal checks, deterministic control for mechanically decidable conditions, and explicit escalation for unresolved cases. The planned evaluation has not been executed or frozen; v1.0 first-user evidence remains `not_satisfied`.

## Revision in This Edition

v0.6.1 is a research-quality revision of the frozen v0.6.0 report. It retains the same code snapshot and does not rewrite the historical edition. This edition:

- retains the 12 peer-reviewed knowledge-conflict papers added in v0.6.0 and adds two high-relevance 2026 preprints: deterministic freshness aggregation [P34](#ref-p34) and ConflictRAG [P35](#ref-p35), expanding the formal set from 40 to 42 references;
- upgrades P12, P14, P16, and P17 from older preprint metadata to their formal EMNLP 2023 or NeurIPS 2023 records;
- narrows the future-study abstract, conflict-precedence language, and evaluation tense so that an unexecuted protocol is not presented as a completed experiment; and
- adds vanilla RAG, deterministic freshness, and conflict-aware RAG to the planned baselines while preserving human adjudication and release-authority boundaries.

The [single reference index](tech-report-v0.6.1/reference-index.md) records inclusion rationale, permitted support, and prohibited extrapolations. The [reference-screening record](tech-report-v0.6.1/reference-screening.md) records candidate decisions and exclusions.

> **Naming convention.** BriefLoop is the project's only current name. Historical literals may remain in old commands, schemas, archive filenames, and experiment IDs for compatibility or reproducibility. They are not alternate product names. See `docs/briefloop-naming.md`.

## Future Study Abstract (Planned; Not Executed)

> Large language models can fail to reconstruct recent change when parametric memory, retrieved evidence, and the timestamps of publication, event occurrence, availability, and adjudication disagree. For enterprise intelligence workflows, this is a recurring operating risk rather than a universal failure mode. A model may retrieve a recent document while retaining an older world state, mistake a follow-up announcement for an initial launch, merge distinct product states, or repeat a superseded claim with greater confidence than the available evidence permits. Many vanilla prompt-only and retrieval-augmented pipelines leave these conflicts to implicit model judgment.
> 
> BriefLoop treats temporal knowledge conflict as a systems-governance problem. It operates over frozen source evidence, separates agentic interpretation from authoritative state changes, binds material claims to identifiable evidence, records conflicts, failures, and revisions, and subjects delivery to deterministic controls and human adjudication. This report defines a protocol intended for preregistration that compares direct prompting, skill or agent workflows, vanilla RAG, a deterministic-freshness baseline, conflict-aware RAG, and BriefLoop. The planned source packs cover publication-event mismatches, stale status, preview-to-GA transitions, vendor-reported metrics, and similar-entity collisions.
> 
> BriefLoop does not claim that architecture makes generated claims inherently true. It provides an enterprise control layer in which evidence, conflicts, decisions, unresolved uncertainty, and subsequent repairs can be inspected, challenged, and governed. The protocol may be called preregistered only after its task pack, decision rules, and hashes are frozen; comparative findings may be stated only after execution.

## 1. Core Insights

### 1.1 Architecture Charters

The following principles were extracted from failures in real runs. They are engineering constraints, not slogans.

1. **Smart components have no authority; authoritative components are deterministic; effective changes pass through people; exceptional actions leave a trace.** LLMs and agents may interpret, recommend, decompose, and draft. They may not directly write authoritative state, advance a stage, freeze evidence, pass a gate, or approve delivery. Effects must be executed by the deterministic control plane, confirmed where required by a human, and recorded.
2. **What machines can enforce should not be delegated to memory.** Rules that can be checked through schemas, validators, gates, transactions, events, or tests must not exist only in prompts or handoff prose.
3. **Every field has one writer.** Python writes state, ledgers, events, hashes, gates, and archives. LLMs write content drafts. Humans approve preferences and delivery decisions. Derived projections may not overwrite authoritative records.
4. **A source is not support; traceability is not proof.** Retrieval plans, source candidates, search summaries, and model summaries are discovery material. Whether a source supports a claim must be recorded separately by support strength, source tier, scope, and freshness.
5. **Frozen artifacts cannot be silently rewritten, and gaps cannot be hidden.** Legitimate change must create a new revision, artifact, event, or explicit supersession, revert, or contamination record. Failed gates, missing evidence, rejected claims, and unresolved human decisions must remain queryable.
6. **Mechanically resolvable conflicts follow declared precedence rather than model persuasion; semantically unresolved conflicts remain explicit and require human adjudication.** Fact contracts and deterministic gates outrank style preferences; repairs for the current run outrank cross-run taste memory; changes to objective, audience, time window, source policy, or delivery standard require explicit configuration or a new run. Declared precedence cannot by itself determine which competing source is true in the world.
7. **Cross-module invariants must close structurally.** When a rule spans transactions, state recomputation, registries, gates, projections, and runtime adapters, it needs one authoritative record and coverage over every writer, recomputer, and reader. The system cannot rely on each path independently remembering the same fact.

### 1.2 Operating Disciplines

- **Product spine: acceleration must not remove accountability.** BriefLoop may reuse frozen evidence, avoid repeated reasoning, and parallelize independent work. It may not remove ledgers, gates, approvals, events, snapshots, or archives to run faster.
- **Public claims: do not say more than the artifacts can support.** Unmeasured capabilities must be labeled unmeasured. Traceability must not be described as semantic proof or demonstrated quality improvement.
- **Data boundary: private facts cannot validate a public mechanism.** Real business workflows may contribute failure categories and test shapes, but private business facts, customer materials, employer data, and non-public information must not enter the public repository, fixtures, or demos.

### 1.3 Why Coding Agents Improve Faster

The progress of coding agents comes not only from model capability but also from the closed feedback loops already established in software engineering. Anthropic's distinction between workflows and agents provides an engineering coordinate: fixed, decomposable tasks are better served by explicit inputs, outputs, and programmatic checks, while open-ended paths warrant greater autonomy.[E21](#ref-e21)

| Software-engineering mechanism | Improvement signal |
|---|---|
| Test suite | Explicit pass or fail |
| Git history | Author, rationale, and diff for each change |
| Defect-to-commit trace | The change that introduced a failure can be located |
| Continuous integration | Automated validation before merge |
| Code review | Important changes receive human approval |

Models provide capability; infrastructure provides repeatable feedback. Business briefing rarely has equivalent machinery. Quality defects are difficult to turn into tests, stale data is difficult to locate in the retrieval chain, verbal feedback does not accumulate, and “this section feels wrong” rarely becomes a reusable engineering task. Improvement therefore depends on individual craft and remains difficult to transfer.

### 1.4 The BriefLoop Thesis

BriefLoop does not attempt to make one model intrinsically smarter. It brings software-engineering accountability infrastructure to business briefing:

| Software-engineering mechanism | BriefLoop counterpart |
|---|---|
| Test suite | Artifact validation and stage quality gates |
| Git history | Decisions, timestamps, actors, and reasons in `event_log.jsonl` |
| Defect trace | Producer stage, role, and artifact relationships in `artifact_registry.json` |
| Continuous integration | Orchestrator control loop and stage-completion transactions |
| Code review | `request_human_review`, RepairPlan, human adjudication, and the Improvement Ledger |

The v0.7.1 reference run exposed a decisive failure: the agent completed the content pipeline while almost entirely skipping the control pipeline. It produced eight content artifacts but invoked no decision transactions, ran no gates, and left `workflow_state.json` at its initial stage. In its postmortem, the agent acknowledged that it had treated the Orchestrator contract as background information rather than an API it was required to execute.

That failure drove BriefLoop to move critical bookkeeping from prompts into transactions. Agents still propose what to do and explain why. The Python control plane determines whether a decision has taken legal effect under the required conditions and artifacts. Important rules must live in schemas, validators, gates, transactions, events, or tests rather than instructions alone.

### 1.5 Independent Convergence with Harness Engineering

Three 2026 studies of harness optimization and one research synthesis provide external coordinates that are comparable with BriefLoop from different directions, but are not interchangeable with it.

- **LIFE-HARNESS** treats the runtime interface outside a frozen model as an optimization target, but its findings come from deterministic environments with computable rewards and cannot be extrapolated into gains in open-domain briefing quality.[P01](#ref-p01)
- **Self-Harness** uses failure mining, bounded modification, and held-in/held-out regression. It supports the proposition that a harness can be improved through engineering; it does not support allowing a candidate modification to approve its own activation.[P02](#ref-p02)
- **Meta-Harness** further places models, tools, and environments within an end-to-end optimization scope. It strengthens both the case for evaluating the entire operating system and the requirement to keep permissions and acceptance protocols outside the editable loop.[P05](#ref-p05)
- **Weng (2026)** synthesizes harness components including workflows, context lifecycle, persistent state, tools, subagents, permissions, and evaluation, and systematically discusses evaluator, reward-hacking, and human-oversight risks. It is a research synthesis and technical essay, not experimental evidence for BriefLoop.[E19](#ref-e19)

The chronology also matters. BriefLoop's human-gated Improvement Ledger and per-run snapshots entered v0.7.0 on 2026-06-10, and its Python-owned claim-freeze transaction entered v0.8.3 on 2026-06-16, both before Weng's synthesis. The defensible description is therefore **retrospective independent convergence**: Weng provides a unifying research language and risk boundary, while BriefLoop's repository history shows an early engineering instance of these principles in open-domain business briefing. Chronology does not substitute for evaluation and does not establish priority or performance superiority.

### 1.6 From Agent Engineering to Loop Engineering

Loop Engineering is concerned not with writing a better prompt once, but with designing a system that continuously discovers tasks, assigns work, checks outcomes, records state, and decides what happens next. This report borrows the term and attributes the paradigm; it does not treat the technical essay as experimental evidence.[E20](#ref-e20) BriefLoop applies this method to recurring business briefings: the control units are no longer code diffs, but material claims, evidence spans, support records, `FeedbackIssue` records, repair tasks, and delivery decisions.

| Loop-engineering element | Coding context | BriefLoop context |
|---|---|---|
| Scheduled discovery | Periodic issue scanning | Weekly reports, monthly reports, recurring research |
| Isolated workspace | Git worktree | Independent run workspace |
| Skills | Repository-level `SKILL.md` | Audience profile, policy profile, role contracts |
| Connectors | Issue tracker, database, API | Source providers and delivery connectors |
| Subagents | Producer and checker separation | Author and auditor separation |
| Persistent memory | Files and commit history | Improvement Ledger, Claim Ledger, Event Log |
| Validation | Unit and regression tests | Quality gates and same-evidence regression |
| Human review | Pull Request review | Human adjudication and delivery approval |

## 2. Design Philosophy

### 2.1 A Three-Layer Quality Model

BriefLoop separates quality into three layers so that process compliance, clean delivery, and analytical excellence are not conflated.

1. **Law:** Machine-checkable requirements such as citation presence, source freshness, numerical consistency with the ledger, and absence of internal identifiers in the reader edition. Hashes, events, and gate reports can verify this layer, but only for formalizable defects.
2. **Honesty:** Whether the reader artifact is clean, readable, and free of internal workflow residue or blank citations. This measures delivery discipline, not analytical depth.
3. **Wisdom:** Whether the briefing identifies what truly matters, provides insight, and outperforms a single-model baseline. This remains **NOT MEASURED**. Claim-layer artifacts differ across runs, so causal attribution is not yet valid.

The correct sequence is to stabilize Law first, then Honesty, and only then measure Wisdom against controlled baselines. Content-quality comparisons are heavily confounded while delivery itself remains unstable.

### 2.2 Correctness, Taste, and Evidence

| Dimension | Concern | Governance mechanism | Authoritative writer |
|---|---|---|---|
| Correctness | Factual errors, stale data, attribution mismatch, structural violations | Schemas, stage validation, deterministic gates | Python control plane |
| Taste | Department preferences, cultural norms, implicit audience expectations | Audience Profile and human-approved Improvement Ledger | Human; model interprets and applies |
| Evidence | Source-claim binding, support strength, freshness, authority tier | Claim drafts, frozen ledger, evidence spans, source appendix | Model drafts; Python freezes and validates |

Correctness can be partly mechanized. Taste must remain human-editable. Evidence lies between them. Models may discover and draft claims, but claim IDs, freeze records, hashes, and support metadata must be owned by the deterministic control plane.

### 2.3 Governance Domains and Control Surfaces

Four contract categories answer **what is governed**:

| Contract category | Governance scope |
|---|---|
| Behavior | Authority boundaries for the Orchestrator and specialist roles |
| Process / Artifact | Stage readiness and expected artifacts |
| Fact-Grounding / Evidence | Whether material claims trace to registered evidence |
| Quality / Audience | Whether delivery meets reader and quality requirements |

Control surfaces answer **who writes, when the result freezes, how it is validated, and what happens on failure**. Their theoretical lineage can be understood through workflow patterns, blackboard architectures, and Design by Contract: the first describes control-flow expressiveness, the shared blackboard describes specialized roles collaborating around common state, and contract-based design emphasizes preconditions, postconditions, and invariants.[T01](#ref-t01) [T02](#ref-t02) [T03](#ref-t03) BriefLoop's provenance relations also borrow the W3C PROV vocabulary of entities, activities, agents, and derivation, but the project does not claim full compatibility or conformance testing.[T06](#ref-t06) Contract categories and control surfaces are not competing architectures: the former describes governance content, while the latter realizes that content as files, single writers, transactions, and failure states. See `docs/control-surfaces.md` for the complete current inventory.

Northstar is a product-governance and prioritization advisory surface, not a runtime role, architecture authority, Merge Governor, or workspace control plane. It may recommend building, deferring, or rejecting scope based on user evidence, but it cannot write runtime state, execute gates or finalization, approve delivery, merge code, or approve public release. Humans retain authority over subjective tradeoffs, commercial commitments, pilot participation, outcome acceptance, and product decisions that take effect. A change to the current run's objective, reader, time window, source policy, or delivery standard must become an explicit configuration change or a new run.

### 2.4 The Single-Writer Principle

- Python writes control state, ledgers, events, hashes, gates, transactions, and archives.
- Runtime agents write candidate claims, screening results, claim drafts, brief content, and semantic audit opinions.
- Humans write approvals, audience guidance, delivery decisions, and explicit run direction.

The separation between `claim_drafts.json` and `claim_ledger.json` exists to enforce this rule. The model may write drafts without authoritative IDs. Python assigns stable IDs and freezes the ledger. Neither side may opportunistically rewrite the other's artifact.

### 2.5 The Speed Principle

Speed may come from reusing frozen artifacts, reducing repeated reasoning, and parallelizing independent work. It may not come from weaker records, fewer gates, fewer approvals, or weaker archives. A fast rerun imports and validates an existing fact layer, then resumes from analysis. It still performs writing, audit, gates, finalization, and human delivery. Acceleration comes from reuse, not omission.

## 3. Architecture: Five Control Spines

![BriefLoop's auditable closed-loop architecture from source discovery to human delivery](assets/briefloop-architecture-v0.5.0.en.svg)

*Figure 1. Agents perform content work; the deterministic control plane owns state, freezing, gates, and archives; humans retain critical authorization and final delivery.*

### 3.1 Runtime-State Spine

```text
runtime_manifest.json
→ workflow_state.json
→ artifact_registry.json
→ event_log.jsonl
```

The Python control plane is the sole authoritative writer. v0.12 binds recovery reads for the Manifest, Workflow, Registry, Event Log, and Finalize Report to the same opened workspace session. POSIX uses descriptor-bound, no-follow reads; Windows uses handle-bound reads with reparse-point handling. Legitimate optional absence remains a typed absence, while unsafe, stale, or non-canonical Registry state projects no value. This design realizes Architecture Charter 7 through a shared fail-closed read boundary, preventing different consumers from reopening paths independently and interpreting the same control file in different ways.

### 3.2 Evidence-and-Claim Spine

```text
source evidence
→ persistent source evidence
→ input classification
→ candidate_claims.json
→ screened_candidates.json
→ claim_drafts.json
→ freeze transaction
→ claim_ledger.json
→ audited_brief.md
→ audit_report.json
→ source_appendix.md
```

Runtime agents produce candidates, screening results, claim drafts, and briefing content. Python validates and freezes the boundary between `claim_drafts.json` and `claim_ledger.json`. A provenance projection can connect claims, sources, artifacts, decisions, and gate findings, but traceable relations do not establish that a source semantically supports a claim, nor do they establish full W3C PROV compatibility.[T06](#ref-t06)

The claim-freeze transaction proceeds as follows:

1. The claim role writes `claim_drafts.json` without `claim_id`. A preassigned ID at any nesting level is rejected.
2. `briefloop state freeze-claim-ledger` reads validated drafts, assigns `CL-####` in deterministic order, writes the authoritative `claim_ledger.json`, records hashes and freeze metadata, and appends a `claim_ledger_frozen` event.
3. `briefloop state stage-complete --stage claim-ledger` requires a matching freeze record. Hash drift, missing freeze metadata, or stale ledger bytes fail closed.
4. Analyst and auditor roles read only the frozen ledger. They may not treat the draft as authoritative input or modify the ledger.

### 3.3 Gate Spine

```text
CompositeAuditAgent
├── DeterministicAuditAgent
├── QualityHarnessAuditAgent
└── NoOpSemanticAuditAgent
    → gates/auditor_quality_gate_report.json
    → gates/finalize_quality_gate_report.json
```

The first two audit components are Python implementations and do not call an LLM. The semantic-audit slot remains a placeholder. The runtime Auditor is expected to examine whether wording matches support strength, but the project does not ship a model-based semantic auditor with release authority. Auditor and finalize stages consume their own stage-scoped gate reports. The legacy `quality_gate_report.json` is a compatibility projection, not frozen authority.

### 3.4 Memory-and-Improvement Spine

```text
audience_profile.md
→ audience_profile_snapshot.md

improvement/ledger.jsonl
→ improvement/memory.md
→ improvement_memory_snapshot.md
```

Humans maintain the Audience Profile and approve improvement guidance. Python materializes memory from the ledger, freezes the per-run snapshot, and records effective entries and their SHA-256 in the Runtime Manifest. An approval or revert during a run affects only future runs; it cannot alter already-frozen input for the current run. Each ledger revision links to its predecessor hash.

### 3.5 Delivery-and-Archive Spine

```text
output/intermediate/finalize_report.json   # single delivery truth
output/delivery/brief.md
output/delivery/<name>.docx
output/source_appendix.md
output/runs/<run_id>/
```

Finalization first renders reader artifacts in a candidate location and runs reader-clean checks; only a passing candidate is promoted to `output/brief.md` and `output/delivery/`. A failure writes a failed `finalize_report.json` while preserving the previous delivery bundle. A success report records the delivery artifacts bound to the current run and their SHA-256 hashes. Delivery eligibility is not delivery success: the latter also requires a delivery-outcome event bound to the current run. File presence alone is not delivery truth. The archive continues to retain delivery artifacts, intermediate artifacts, control records, and a hash manifest; a historical run may not be overwritten in place.

### 3.6 Product Layer and Support-Sufficiency Experimental Stack

```text
report_spec.yaml
→ ReportPack / ReportTemplate / PolicyProfile
→ atomic_claim_graph.json
→ evidence_span_registry.json
→ claim_support_matrix.json
→ semantic_assessment_report.json
→ semantic_support_acceptance_ledger.json
→ quality_panel.json / quality_summary.md / quality_panel.html
→ delivery_bundle.zip / audit_bundle.zip
```

Authority is bounded as follows:

- Specialist roles may draft Atomic Claim Graphs, evidence spans, Claim-Support Matrix rows, and semantic-assessment proposals.
- Python validates only schemas, reference integrity, hash bindings, required-row coverage, and adjudication-record format.
- `semantic-support adjudicate` records human acceptance or rejection, but the adjudication record does not rewrite the Claim-Support Matrix automatically.
- `briefloop new`, `packs bundle`, `quality summarize`, `extract`, and `sources materialize-pack` write only workspace structure or projections. They do not run specialist roles, approve delivery, or prove semantic correctness.
- The experimental WorkBuddy/CodeBuddy path uses two-stage permissions: a checked-in role agent drafts only the artifacts named in the handoff; a command-capable main session must reread the handoff before executing the permitted CLI transaction. Seeing an artifact or a generic-helper narrative cannot substitute for a host-visible record of the exact role invocation and return.

The supported product entrypoints are:

| User command | Internal ReportPack | Purpose |
|---|---|---|
| `briefloop new industry-weekly` | `market_weekly` | Industry weekly report |
| `briefloop new management-monthly` | `management_monthly` | Management monthly report |
| `briefloop new document-review` | `evidence_extract` | Document evidence-extraction workspace |

## 4. Control Transactions

### 4.1 Stage-Completion Transactions

`stage-complete`, `finalize`, and `finalize-complete` move stage bookkeeping, candidate promotion, and completion decisions from prompt obligations into deterministic execution. A transaction:

- checks that expected artifacts are registered and valid in the trusted interpretation of the Registry;
- updates stage status in `workflow_state.json` and appends an event to `event_log.jsonl`;
- enforces stage-specific preconditions, such as a matching freeze record for Claim Ledger completion;
- during finalization, checks the candidate reader artifact before atomic promotion and writes delivery-artifact hashes to `finalize_report.json`;
- distinguishes eligibility, finalization success, and delivery outcome in completion and delivery projections, rather than inferring success from file presence.

The Orchestrator decides what action to take and why. Python records whether that decision has legally taken effect under the required conditions.

### 4.2 Claim-Ledger Freeze Transaction

| Operation | Authoritative writer | Artifact or result |
|---|---|---|
| Draft claims | Claim role | `claim_drafts.json`, without `claim_id` |
| Validate drafts | Python | Reject preassigned IDs and invalid structure |
| Assign IDs | Python | Stable `CL-####` identifiers |
| Freeze ledger | Python | `claim_ledger.json`, freeze metadata, and event |
| Complete stage | Python | Refuse completion without a matching freeze record |

After freezing, analyst and auditor roles may only read the ledger. Any change requires a new run or an explicit contamination, supersession, and repair record.

### 4.3 Run Integrity and Contamination

`workflow_state.json.run_integrity` records whether a run remains usable as clean reference evidence. Resetting an executed run, replaying a stage against stale state, or modifying a frozen artifact writes a contamination event and reason. The v0.12 recovery context must read five classes of control input through the same trusted workspace session; supersede marks downstream artifacts stale until they are regenerated. A contaminated run may continue to a constrained delivery, but it may not be presented as an A-grade controlled experiment. Missing, stale, and unsafe inputs must remain visible.

### 4.4 Immutable Archives

The archive at `output/runs/<run_id>/` preserves:

- delivery artifacts such as Markdown and DOCX;
- intermediate artifacts such as the Claim Ledger, gate reports, and audit report;
- control records such as workflow state, Event Log, and Runtime Manifest;
- SHA-256 values for every artifact in the manifest.

The archive is append-only and cannot be rewritten in place.

### 4.5 Fast-Rerun Import

`briefloop state import-fact-layer` can copy archived source evidence, input classification, candidate claims, screening results, and the Claim Ledger into a new workspace. The transaction copies original bytes, verifies hashes, records the import relationship, and marks satisfied upstream stages as completed by import. `briefloop run --recipe fast-rerun` begins at analysis. Finalization still reevaluates freshness against the new workspace's time. A fast rerun reuses the fact layer; it does not reuse the previous brief, audit result, finalization record, or delivery approval.

## 5. Evidence and Claim Governance

### 5.1 From Source to Claim

```text
source discovery
→ persistent source evidence
→ input classification
→ candidate claims
→ screening results
→ claim drafts
→ deterministic freeze
→ Claim Ledger
```

Only materialized source files and supported entries in source configuration can serve as evidence. `source_candidates.yaml` is for planning and review. It cannot replace `sources.yaml`, and its presence does not establish that source discovery is complete. Retrieval plans, search summaries, and model summaries are discovery material, not evidence.

Current source records require at least a source ID, name, type, title, and content. Evidence spans, retrieval time, source tier, and excerpt hashes belong to the support-sufficiency direction. They strengthen traceability but still do not prove automatically that a source semantically supports a claim.

### 5.2 Claim-Draft Contract

`claim_drafts.json` is the input to the claim-freeze transaction. Neither a draft entry nor its metadata may contain a preassigned `claim_id`. The `sorted_sequential_v1` algorithm sorts by stable keys and assigns `CL-####`. Identical freeze input produces identical IDs, but the system does not promise ID stability across freezes when claims are added, removed, or reordered.

This design prevents the model from fabricating authoritative identity. The model owns claim content; the system owns claim identity and freeze status.

### 5.3 Support-Strength Calibration

The v0.7.4 failure study exposed five recurring problems:

1. **Support inflation:** a source reports regulatory discussion, while the brief describes formal approval.
2. **Authority inflation:** a conference item or media report is presented as a government plan or official fact.
3. **Claim conflation:** a supported core fact and an unverified implication appear in the same sentence.
4. **Attribution mismatch:** one source is made to carry several conclusions it does not independently support.
5. **Forecast-as-evidence:** a secondary-market forecast or commentary is used as the basis for a core fact.

These are not missing-source failures. They are calibration failures between evidence and language. The Auditor must examine overstatement, support strength, confidence, evidence relationships, and limitations. Experimental support records may use labels such as `explicitly_supported`, `partially_supported`, `supportive_but_overextended`, `attribution_mismatch`, `needs_primary_source`, and `unsupported`. The labels still require human adjudication and cannot become release authority on their own.

Dynamic knowledge adds four adjacent but distinct calibration risks:

6. **Temporal-validity inflation:** a fact that was historically true is written as if it remains true at the current freeze point.
7. **Version conflation:** an original item, correction, update, and republication are treated as independent and equally current evidence.
8. **Repetition-as-corroboration:** multiple copies of an older report create a numerical majority over one newer primary source.
9. **Loss of source fidelity:** the model uses its own knowledge to “correct” the input, producing text that may be more factually accurate in the world but is no longer faithful to the source it purports to summarize.[P29](#ref-p29)

Temporal-knowledge research shows that facts may have validity periods, while credibility-aware generation shows that source credibility can be made an explicit signal. Neither result means that a date or a high source tier automatically determines truth.[P30](#ref-p30) [P33](#ref-p33) The current exact head can record source `published_at` and `retrieved_at` values and apply deterministic freshness checks; the policy-regulatory module also has a local `effective_date`. It does not provide a general `valid_time` or `as_of` model for claims, source-correction, retraction, or supersession relations, or a general conflict finding and resolution state. These four items are therefore architecture requirements and planned evaluations in this edition, not shipped conflict-governance capabilities.

### 5.4 Boundary of the Source Appendix

During finalization, `output/source_appendix.md` is generated from claims actually cited by the reader brief. It is embedded into Markdown and DOCX delivery artifacts while an audit copy is retained. The Source Appendix gives readers a route for follow-up. It is not a certificate of factual correctness. It establishes where a claim can be traced, not that the claim has been proven true.

## 6. Gates and Repair

### 6.1 Stage-Scoped Gates

| Gate report | Constrained stage | Primary checks |
|---|---|---|
| `gates/auditor_quality_gate_report.json` | Auditor completion | Material facts, freshness, target relevance, coverage omissions |
| `gates/finalize_quality_gate_report.json` | Finalize completion | Reader residue, internal IDs, process language, delivery hygiene |

Stage-scoped reports are authoritative. The legacy `quality_gate_report.json` is retained only as a compatibility projection. Completion transactions have no `--force` path around the gates.

### 6.2 Deterministic Audit Stack

```text
runtime Auditor role
→ CompositeAuditAgent
→ DeterministicAuditAgent
→ QualityHarnessAuditAgent
→ NoOpSemanticAuditAgent
→ audit_report.json
```

The deterministic audit checks sources, freshness, numbers, dates, safe wording, process residue, and redaction. The Quality Harness audit checks rules around material facts, target relevance, and reader residue. The semantic-audit slot remains a placeholder. A future model-based semantic evaluator still may not overwrite deterministic findings or decide support truth or delivery eligibility by itself.

### 6.3 Repair Routing

`briefloop repair route` is a read-only diagnostic command. It maps gate, audit, registry, and workflow findings to the responsible stage and allowed artifact class. It tells the Orchestrator who should repair an issue and what may be changed. It does not create prose, execute repair, or replace a RepairPlan.

### 6.4 Anti-Goodhart Principle

*Precision Is Not Faithfulness* shows that optimizing precision alone may reward a system for deleting important but difficult-to-verify content.[P08](#ref-p08) Before a blocking precision gate is introduced, BriefLoop must ask: **What is the cheapest way for the system to pass?** If omission is the cheapest strategy, a coverage or omission check must accompany the gate so that silence cannot earn a high score.

### 6.5 Coverage and Omission Continuity

The current supported gate checks whether high-priority items in `screened_candidates.json` disappear silently before the Claim Ledger or cited brief. It captures the path in which an item passes screening but is omitted during analysis or editing. It does not provide complete recall over all relevant facts and does not prove that the report has sufficient overall coverage.

## 7. Controlled Memory and Improvement

### 7.1 Audience Profile

`audience_profile.md` is a human-editable workspace file for structural preferences, departmental vocabulary, tone, and durable feedback. A run reads only its frozen `audience_profile_snapshot.md`. Changes to the live profile during a run affect future runs only. The profile is semantic guidance, not evidence, and has no gate authority.

### 7.2 Improvement Ledger

`improvement/ledger.jsonl` is an append-only, revision-chained workspace ledger that requires human approval. Its lifecycle is:

```text
propose
→ human approve
→ Python rebuilds improvement/memory.md
→ next run freezes improvement_memory_snapshot.md
→ revert when necessary
```

Key invariants are:

- a proposed entry affects no run;
- approval appends state and does not alter the current run;
- materialization occurs at the beginning of the next run;
- reverted entries disappear from the next memory and snapshot;
- `materialized_entry_ids` and the hash in `runtime_manifest.json` record exactly which guidance the run consumed.

### 7.3 Whether Guidance Was Manifested

The experimental `guidance_manifestation_report.json` can record observable status for approved guidance in an output: explicitly manifested, partially manifested, contradicted, or not observable. Python validates labels and counts them. It does not decide whether a label is semantically correct, modify improvement memory, or block finalization.

The archived BriefLoop-090 experiment can import manifestation ratings from an external evaluator. That measurement is outside the ordinary product path and does not establish improved output quality.

### 7.4 Memory Surfaces Not Yet Shipped

| Planned artifact | Status | Purpose |
|---|---|---|
| `improvement/intake.jsonl` | Deferred | Receive raw feedback with source relationships |
| `improvement/candidates.jsonl` | Deferred | Stage unapproved rule or preference candidates |
| `reference_samples/manifest.jsonl` | Planned | Preserve human-accepted examples of taste |

These surfaces should be introduced only after the core propose-approve-materialize-freeze-revert lifecycle is stable.

### 7.5 Controlled Harness-Improvement Protocol (Proposed)

The current exact head has event traces, gate findings, `FeedbackIssue`, `RepairPlan`, evaluation fixtures, the Improvement Ledger, run snapshots, trusted control reads, and transactional delivery records, but it does not yet form an end-to-end self-improving harness. A future protocol must preserve these authority boundaries:

| Phase | Permitted action | Authority constraint |
|---|---|---|
| Observe weaknesses | Form structured candidates from events, gates, audits, and human feedback | Observation does not become modification automatically |
| Propose a bounded change | An agent proposes a narrow modification for a repeated, localizable issue and declares editable scope and behavior to preserve | The agent cannot write the active harness |
| Regression validation | Held-in cases confirm the target issue is fixed; held-out cases and same-evidence reruns check side effects | Evaluators and permission controls stay outside the editable loop |
| Authorization | A human accepts or rejects; a deterministic transaction records inputs, version, outcome, and decision | Only an approval transaction may create a candidate new version |
| Activation | The new version affects future runs only; rejected proposals and negative outcomes remain recorded | No writeback into the current run or frozen historical runs |

“Improvement” in BriefLoop therefore does not mean an agent rewriting its own control plane. It means converting production failures into engineering changes that are localizable, proposable, regression-tested, approvable, and reversible. Materiality, analytical taste, and management value still require human judgment. Deterministic gates provide only local, auditable weak-reward surfaces.

## 8. v0.12.1 / Post-Tag `main` Implementation Baseline

### 8.1 Version Evolution

| Version | Theme | Core capability boundary |
|---|---|---|
| v0.8.3 | Claim-freeze transaction | Python assigns stable IDs to draft claims and freezes them |
| v0.9.x | Experimental support-sufficiency core | Atomic Claim Graph, Evidence Span Registry, Claim-Support Matrix, semantic proposals, and human adjudication records |
| v0.10.x | Product layer and delivery hardening | Report specification, bundle projections, finalization transaction, and the five-step authoring path |
| v0.11.x | Product baseline | Three product entrypoints, policy/template diagnostics, and runtime/operator surfaces |
| v0.12.0 | Trusted reads and delivery truth | Descriptor/handle-bound control reads, same-session recovery, candidate-before-promotion, and `finalize_report.json` as the single delivery truth |
| v0.12.1 tag | Delegation and product-governance boundaries | Two-stage WorkBuddy/CodeBuddy permissions, exact delegation evidence, the Northstar advisory boundary, and the historical v0.4 report |
| post-tag `main` | Citation parsing and pilot-contract closure | #513–#515; not back-ported as v0.12.1-tag capability |
| v1.0.0 (target) | Product freeze | Freeze commitments and satisfy the first-user evidence gate rather than expand scope |

### 8.2 Supported

- Default, strict, and `human_assisted` role topologies, plus Delivery Editor.
- Hermes, Claude Code, and OpenCode runtimes; `run` generates a handoff and does not replace external role execution.
- Runtime state, a trusted Registry, the Event Log, claim freezing, stage completion, transactional finalization, contamination/supersede records, and immutable archives.
- Audit and finalization stage gates, coverage-and-omission continuity checks, feedback and repair plans, and the Orchestrator control switchboard.
- The Improvement Ledger, audience snapshots, per-run improvement snapshots, and provenance projection.
- Reader delivery bundles under `output/delivery/`, audit copies of the Source Appendix, delivery truth in `finalize_report.json`, and a current-run delivery outcome.
- Four-way input governance and market-competition, policy, and regulatory-analysis modules.
- Twenty-five public-safe evaluation cases; 3,710 pytest items are locally collectable at this snapshot.
- The `industry-weekly`, `management-monthly`, and `document-review` product entrypoints.

### 8.3 Experimental

- Atomic Claim Graph, Evidence Span Registry, Claim-Support Matrix, semantic assessment, and human adjudication.
- Broader Product OS extensions: template rendering, policy-profile gate adaptation, the Quality Panel, and materiality and support-wording diagnostics.
- UTF-8 text evidence-span seeding, persistent source evidence packs, SourceHub Lite, and fast-rerun fact-layer import.
- Paths involving Codex custom agents, source-clone WorkBuddy/CodeBuddy role assets, Feishu, Gmail, PDF, and MinerU.
- Archived experiment tooling for BriefLoop-090 and its frozen historical ID `MABW-080`.

### 8.4 Not Shipped

- An end-to-end Issue Candidate system and an approval transaction that safely promotes a candidate into a new harness version.
- A complete semantic-coverage gate, regression harness, and release-eligibility summary with release-blocking authority.
- Demonstrated cross-model output-quality gains, complete fact-checking, private commercial benchmarks, and autonomous learning.
- External runtime evidence that establishes actual WorkBuddy/CodeBuddy role delegation and result quality.
- A primary `pipx install briefloop` path smoke-tested against a real package-index artifact.
- First-user pilot evidence satisfying the v1.0 requirement.

### 8.5 v1.0 Evidence Gate

The freeze inventory in `docs/control-surfaces.md` identifies surfaces eligible for backward-compatibility commitments. v1.0 also requires at least one publicly reproducible first-user evidence record, such as an external fresh clone, first use through WorkBuddy, a pilot checklist, or recurring weekly-report dogfood. At the current exact head, `docs/v1-pilot-evidence.md` still records `Status: not_satisfied`. It is a release-readiness evidence ledger, not semantic proof, proof of output quality, delivery approval, or release authority.

BriefLoop-090 completed one synthetic `auditable_brief` pilot. It can support the narrow statement that guidance patterns differed in one case. It cannot support generalized claims about output quality, management readiness, or delivery quality.

## 9. Reference Evidence and Failure Studies

### 9.1 v0.7.2 Public Solar Integration Run

Two runs over public materials demonstrated that the following mechanisms can close end to end:

- approved guidance can be materialized into frozen per-run improvement memory;
- quality gates blocked stage progression three times and passed after repair;
- the Orchestrator reads a frozen snapshot rather than live mutable workspace memory;
- `runtime_manifest.json` records effective entry IDs and their hashes.

The run does not prove improved output quality or a causal effect from guidance. Candidate claims, screening results, and Claim Ledger hashes differed between the two runs, so guidance was not the only variable. The run is appropriate as B+ integration evidence, not as an A-grade controlled experiment.

### 9.2 v0.7.4 Failure Study

An industry-research run produced a complete, readable briefing and preserved the full workflow artifact chain. External review still found support inflation, authority-tier mismatch, claim conflation, attribution mismatch, and forecasts presented as facts.

This did not show that BriefLoop produced a better report. It demonstrated a narrower but important capability. In direct model drafting, an error often survives only in the final text. In BriefLoop, the error can be traced through source summary, candidate claim, screening result, Claim Ledger, audited brief, and reader artifact. The failure was not repaired automatically, but its propagation path was preserved.

### 9.3 Decoupling Content and Control

The v0.7.1 run showed that a model can complete all content work while skipping almost every control obligation. That failure directly motivated stage-completion, finalization-completion, and claim-freeze transactions. It supports one conclusion: **a prompt obligation is not an execution guarantee.** An important rule needs a machine-verifiable execution path.

### 9.4 Evidence Boundaries

| Evidence | Supports | Does not support |
|---|---|---|
| Solar B+ run | Gates executed; Improvement Memory chain closed | Improved output quality; causal guidance effect |
| Failure study | Error-propagation path preserved; failures classifiable | BriefLoop outperforms a single-model baseline |
| Content/control decoupling | Models are poor owners of low-level authoritative bookkeeping | Every model will fail in the same way |
| BriefLoop-090 | Observable guidance-pattern differences under one frozen fact layer | Generalized quality, management readiness, or DOCX quality |

### 9.5 Synthetic Pilot and Product-Layer Reference Bundle

BriefLoop-090 uses one public-safe synthetic case with three conditions: `baseline`, `memory`, and `prompt-only`. It applies blind assessment and hash-bound import. The observation matched the intended pattern: the baseline did not manifest the target guidance, the memory condition was more stable, and prompt-only over-applied the same guidance. This result is specific to one case.

The v0.11.3 product-layer reference bundle demonstrates deterministic `same_evidence_reader_quality_regression`: the reader artifact contains no internal claim markers, the audit bundle preserves trace records, and the Quality Panel summarizes materiality, template, wording, and trajectory diagnostics. The bundle does not call a model, so it cannot establish model-output quality or delivery approval.

## 10. Related Research and Industry Practice

### 10.1 Harness Adaptation and Optimization

LIFE-HARNESS, Self-Harness, and Meta-Harness jointly show that the harness between a model and its environment can be an independent optimization target.[P01](#ref-p01) [P02](#ref-p02) [P05](#ref-p05) Their tasks, rewards, and acceptance mechanisms, however, cannot be extrapolated directly to open-domain business briefings. BriefLoop therefore adopts only the methodology that failures can be structured, modifications should be bounded, and regression must cover held-out behavior. The active control plane, evaluators, permissions, and approval transactions must remain outside candidate modifications.

Weng's synthesis expands the harness into an operating system composed of workflows, context lifecycle, persistent state, tools, subagents, permissions, and evaluation, and emphasizes that recursive structures, vague evaluation, reward hacking, and long-horizon maintenance objectives remain risks.[E19](#ref-e19) This report uses it for domain definition and risk synthesis; mechanism and performance claims still return to primary papers, and the synthesis is not presented as a peer-reviewed experiment.

BriefLoop has a supported subset of trajectory regulation. When retries, repair loops, or repeated blocks exceed budget, Python narrows the legal decisions for the current stage to `request_human_review` and `block_run` and records the change in the Event Log. This is control-state narrowing, not automated repair.

### 10.2 Multi-Turn Feedback and Regression

DRA Multi-Turn finds that process-level feedback can produce meaningful single-turn gains, but later revision can regress constraints that were previously satisfied.[P06](#ref-p06) This supports BriefLoop's targeted repair, frozen snapshots, and external gates. It also explains why same-evidence regression must ask both whether the target defect disappeared and whether previously passing behavior was preserved. The study does not provide an estimate of BriefLoop's own quality improvement.

### 10.3 Auditable Human-Agent Collaboration

CHAP describes human–multi-agent collaboration through workspaces, tasks, artifacts, and append-only evidence logs.[P07](#ref-p07) CHAP focuses on a communication protocol; BriefLoop focuses on governance and release accountability inside business briefing. Both require collaborative results to land in inspectable artifacts rather than exist only in transient conversation. This report does not claim protocol or schema compatibility.

### 10.4 Evaluation Method

FActScore decomposes long-form text into atomic facts, while ALCE separates citation quality from general fluency. Together they show why “has a citation” and “is fully supported” must be evaluated separately.[P12](#ref-p12) [P13](#ref-p13) *Precision Is Not Faithfulness* further warns that a single precision metric may reward a system for saying less, so a precision gate needs a continuity constraint.[P08](#ref-p08) ResearchLoop provides an adjacent-system comparison for an externalized evidence gate and persistent claim binding.[P20](#ref-p20) BriefLoop keeps freezing, hashing, coverage checks, stage state, and delivery hygiene in a non-model control plane while leaving semantic judgment as a proposal or an input to human adjudication. It does not claim that atomization or provenance itself establishes truth.

### 10.5 Multi-Agent Frameworks

AutoGen, CAMEL, and MetaGPT respectively represent conversational agents, role-playing collaboration, and SOP-encoded pipelines.[P09](#ref-p09) [P10](#ref-p10) [P11](#ref-p11) EvoMAS frames multi-agent-system generation as execution-feedback-driven evolution over a structured configuration space and reports performance and executability results on its benchmark.[P21](#ref-p21) These works study how agents can be organized; BriefLoop's distinction is who has authority to make a result take effect. In this report, EvoMAS supports only the proposition that candidate topologies or configurations can be searched. It does not support a claim that BriefLoop has implemented automatic architecture evolution. In engineering practice, multiple agents are warranted only when context isolation, parallel exploration, or specialization provides a real benefit.[E10](#ref-e10)

### 10.6 Memory and Preference

Self-Refine and Reflexion show how model self-feedback, linguistic feedback, and episodic memory can affect later outputs or trials.[P16](#ref-p16) [P17](#ref-p17) Hermes Agent's persistent-memory documentation provides a human-readable `USER.md`/`MEMORY.md` file surface with optional write approval.[E22](#ref-e22) BriefLoop borrows the readable file surface, not its authority model: audience guidance also passes through a workspace ledger, human approval, per-run freezing, hash chaining, and a manifest of effective entries. Live memory cannot silently alter the current run, preference cannot override fact gates, and projections cannot write back to the source ledger.

### 10.7 Enterprise Knowledge-Work Agents

This section retains only first-party engineering materials that support a specific architectural argument: the workflow/agent distinction, the boundary for when multi-agent systems are useful, attribution of the Loop Engineering term, a persistent-memory surface, and the Tax AI production loop. Other product launches, aggregation pages, and duplicate cases are excluded from the formal bibliography. Engineering articles can describe patterns of practice; they cannot establish that BriefLoop is implemented correctly, produces higher quality, or is production-ready.

#### 10.7.1 From Conversation to Deliverables

Recurring briefing contains two kinds of work. The parts with explicit objectives, stages, and delivery formats suit predictable workflows; source exploration, materiality judgment, and interpretation of conflicts still require an agent with tools and context. Anthropic's engineering article explicitly distinguishes workflows that follow predefined code paths from agents that dynamically direct their own process.[E21](#ref-e21) BriefLoop accordingly assigns content work to external runtime roles and state transitions, freezing, and delivery transactions to the Python control plane.

#### 10.7.2 Enterprise Analysis Is First a Dynamic-Knowledge, Conflict, and Validation Problem

The first problem in business analysis is not making paragraphs look more like a report, but keeping entities, time, scope, source tier, and support relationships clear at a declared freeze point. FActScore and ALCE illustrate the importance of intermediate objects through atomic facts and citation quality, respectively.[P12](#ref-p12) [P13](#ref-p13) For periodic briefing, this is also a dynamic-knowledge problem: one model can simultaneously face stale training-time knowledge, newly retrieved material from the current week, and inconsistent announcements, republications, and corrections.

The knowledge-conflict literature commonly distinguishes three cases: `context-memory conflict` between current context and parametric knowledge, `inter-context conflict` among external contexts, and `intra-memory conflict` where parametric memory encodes competing answers.[P22](#ref-p22) Briefing and summarization require a fourth distinction between factual correctness and source fidelity. A model can produce a statement that is correct in the world while silently “correcting” its input and breaking the claim-evidence relationship.[P29](#ref-p29)

Research on news streams shows that expanding the retrieval space with new articles can support rapid adaptation, yet a system with an outdated underlying language model still underperforms one whose parametric model is also updated.[P23](#ref-p23) DYNAMICQA further finds that dynamic facts exhibit more internal conflict and that facts with such conflict are harder to update from context.[P24](#ref-p24) This is consistent with the foundational observation in temporal-knowledge research that many facts expire while most language models are trained on data snapshots.[P30](#ref-p30)

Retrieval can introduce conflicts of its own. Controlled experiments show that some retrieval-augmented models persist with faulty internal memory even when given correct evidence and can be influenced by evidence counts and confirmation bias.[P25](#ref-p25) In the QACC study's open-domain Google Search setting, as many as roughly one quarter of unambiguous questions retrieved conflicting contexts. That proportion must not be generalized into a conflict rate for news briefings or enterprise data.[P26](#ref-p26)

Prompting a model to “consider the latest facts” is not reliable control. In a temporal-conflict benchmark, explicitly prompting for fact mutability increased references to temporal change but did not improve factual accuracy in that setting. Verbalized temporal awareness is not equivalent to correct final prediction.[P27](#ref-p27) When available evidence is insufficient for adjudication, exposing the disagreement rather than letting a model choose silently is the more appropriate behavior for an auditable system.[P28](#ref-p28)

A 2026 preprint further separates current-value conflict handling into semantic candidate extraction and deterministic aggregation. On the single-hop MemoryAgentBench FactConsolidation task with explicit version serials, the candidate-extraction plus Python `max(serial)` pipeline exceeded free-text LLM judgment by 10.8 percentage points, with a 21-point gap at 262K context. The reported gain is a pipeline-level effect: prompt, output format, temperature, and resolver changed together, and a `max(timestamp)` pipeline did not outperform LLM judgment on 45 LongMemEval knowledge-update samples. The result supports a deterministic microbaseline for safely total-ordered current-value conflicts; it does not make the newest source a general truth rule.[P34](#ref-p34)

The ConflictRAG preprint proposes a `detect → classify → resolve → generate` pipeline that distinguishes inter-document from parametric-contextual conflicts before generation and preserves source attribution and conflict annotations. It is a useful adjacent method and planned conflict-aware RAG baseline, not an enterprise release-authority model: it defers to retrieved evidence for parametric-contextual disagreement, ranks temporal conflict by recency, relies on LLM-extracted source criteria, and acknowledges that CARS structurally favors systems with explicit conflict modules.[P35](#ref-p35)

These results do not imply that retrieval augmentation lacks value. FreshLLMs shows that organized search context can improve question answering over rapidly changing knowledge; Astute RAG and credibility-aware generation show that conflict-aware and source-aware post-retrieval methods are active, useful research directions.[P31](#ref-p31) [P32](#ref-p32) [P33](#ref-p33) Benchmark gains, however, do not automatically establish the latest source, complete a supersession decision, or grant enterprise release authority.

BriefLoop's source packs, Claim Ledger, evidence spans, and support records are engineering representations of these intermediate objects. The current system can preserve source times, claim relationships, support proposals, and human-adjudication records. It does not claim to detect every knowledge conflict, determine which source is true, recognize every correction or supersession relation, or independently fact-check dynamic claims.

Models therefore discover, draft, propose conflict labels, and challenge; Python freezes, validates, records, and executes deterministic gates; humans own semantic disagreement that cannot be deterministically resolved, run direction, and final delivery. This is a division of authority, not a ranking of capability.

#### 10.7.3 From Prompts to Operating Harnesses

Loop Engineering moves attention from a single prompt to a system that continuously discovers, assigns, checks, and remembers; Building Effective Agents emphasizes simple, composable patterns and clear stage boundaries.[E20](#ref-e20) [E21](#ref-e21) BriefLoop's contracts, control plane, gates, frozen artifacts, Event Log, and human approvals are concrete implementation choices for briefing. The two technical articles are not test evidence for those choices.

#### 10.7.4 When Multiple Agents Are Appropriate

Multi-agent systems are most valuable in three cases: isolating subtasks that would pollute the main context, exploring a large search space in parallel, and assigning specialized tools and context to distinct responsibilities. Outside these cases, coordination cost often exceeds the benefit.[E10](#ref-e10) BriefLoop therefore does not market “more agents” as the contribution. Role topology may change; artifact contracts, single writers, and control responsibilities do not.

#### 10.7.5 From Quality Governance to Security Governance

v0.5 does not generalize quality controls into a complete security proof. What can currently be stated is that control reads use a fail-closed descriptor/handle boundary; experimental roles have no CLI transaction authority; and human approvals, the Event Log, frozen snapshots, and delivery records remain auditable. The project has not published a complete threat model and does not claim conformance with zero-trust architecture, organizational security standards, or regulatory requirements.

#### 10.7.6 Production Traceability and Controlled Improvement

OpenAI and Thrive Holdings' Tax AI case organizes a production-improvement loop around corrections from expert practitioners, product tracing from source materials through final filings, and conversion of repeated issues into custom evaluations and bounded engineering tasks.[E01](#ref-e01) The article also states that ambiguous cases or cases that cannot be automated safely return to the product team, while engineers remain responsible for architecture, product decisions, and deployment. It therefore supports a controlled loop, not unbounded autonomous improvement.

BriefLoop takes a corresponding position: human edits, audit findings, citation mismatches, and insufficient support first become structured issues. Only repeated, localizable, testable issues become repair candidates. Self-improvement is not a model reflecting alone; it is a production system converting failures into engineering changes that are verifiable, approvable, reversible, and effective only for future runs. The project has not yet completed this end-to-end improvement path.

| Tax AI practice | BriefLoop counterpart |
|---|---|
| Source document | Source pack |
| Field extraction | Atomic claim extraction |
| Field citation | Evidence span |
| Expert correction | Human adjudication and FeedbackIssue |
| Field-level review row | Support record and `FeedbackIssue` |
| Repeated correction pattern | Evaluation target |
| Bounded code repair | Scoped workflow repair |
| Regression evaluation | Semantic regression and same-evidence rerun |
| Filed result | Human-approved briefing delivery |

## 11. Limitations and Future Work

### 11.1 Known Boundaries

The current exact head does not claim to provide:

- automatic proof of semantic truth or complete fact-checking;
- automatic detection or adjudication of all `context-memory`, `inter-context`, and `intra-memory` knowledge conflicts, including dynamic facts, source corrections, version supersession, retractions, disputed facts, and source-fidelity conflicts;
- demonstrated improvement in briefing content quality, management value, or cross-model stability;
- autonomous repair execution, autonomous modification of the active harness, or self-approval of policy for future runs;
- management-ready deliverables by default;
- proof of actual delegation from file presence, a generic-helper narrative, or the presence of role assets alone;
- stable production readiness for WorkBuddy/CodeBuddy, Codex, Gmail, Feishu, PDF, or PyPI paths;
- satisfaction of the v1.0 first-user evidence gate;
- an end-to-end self-improving harness.

The precise capability boundary is narrower. Material claims can be connected to registered sources, artifacts, and control records. Optional support records and semantic-assessment proposals can be observed and human-adjudicated. Sources can carry publication and retrieval times and undergo freshness checks. These surfaces provide traceability, limited temporal signals, and support-sufficiency records; they do not provide general valid time, source-supersession relations, complete conflict detection, or truth proof.

The current version does not automatically cluster validator-confirmed weaknesses, does not ship an approval/promotion transaction for harness candidates, and has not shown that a modified harness avoids regression on held-out briefing cases. The Improvement Ledger manages human-approved reader guidance. It does not authorize agents to change code, contracts, gates, policies, or control state consumed by future runs. The v0.12 advance is to close the boundaries around reads, recovery, finalization, and delegation more reliably, not to grant models more authority.

### 11.2 v1.0 Focus

The priority for v1.0 should be to freeze the product promise and obtain first-user evidence, not expand the experimental surface. The product layer already provides three workspace entrypoints, report specification, a five-step authoring path, state projections, reader and audit bundles, and the Quality Panel, but `docs/v1-pilot-evidence.md` still records `not_satisfied`. The next requirement is for a non-maintainer to complete a reproducible run in a fresh environment and leave evidence of success, confusion, failure, repair, and remaining limitations, rather than for the maintainer to infer usability on the user's behalf.

### 11.3 Support-Sufficiency Direction

Existing experimental surfaces include the Atomic Claim Graph, Evidence Span Registry, Claim-Support Matrix, semantic-assessment proposals, human adjudication, persistent source evidence packs, and the Quality Panel. The remaining path is:

```text
blocking coverage gate
→ held-in/held-out regression acceptance protocol
→ regression harness with release authority
→ release-eligibility summary
→ Issue Candidate system
```

A semantic model may propose support labels, conflict types, candidate supersession relations, source differences, and uncertainty explanations. It may not directly determine which dynamic fact is true, silently discard competing sources, or authorize release merely from source count, model confidence, or agreement with parametric memory. It also may not decide repair ownership, archive grade, future-run policy, or release eligibility. Release authority remains in schemas, hashes, policy, human adjudication, and deterministic blocking rules.

### 11.4 From Failure to Improvement

A future Issue Candidate system should follow this path:

```text
report failure
→ claim-level trace
→ structured issue
→ evaluation target
→ scoped repair
→ same-evidence regression
→ human review
→ updated release eligibility
```

Not every correction should enter the engineering queue automatically. Further harness modification requires the editable scope to be declared in advance, evaluators and permission control to remain outside the editable loop, held-in cases to confirm the target fix, held-out cases to test side effects, and an approved version to affect future runs only. EvoMAS shows that a multi-agent topology can be searched within a structured configuration space using execution traces.[P21](#ref-p21) For BriefLoop, the only acceptable future path is “generate a candidate configuration → deterministic schema and invariant validation → held-in and held-out regression → human approval → recorded activation as a new version,” not an in-run agent directly rewriting its own authority.

### 11.5 Non-Goals

BriefLoop does not use one global semantic score as release authority, let a model reviewer decide final support truth, ask Python to pretend it has semantic judgment, or weaken ledgers, Event Logs, archives, human delivery, and frozen-artifact rules in the name of speed.

The most accurate public statement is:

> BriefLoop puts the claims, evidence, and delivery decisions in business briefings into an auditable engineering loop. It does not prove truth or eliminate hallucination.

## Appendix A: Contract Categories

`configs/orchestrator_contract.yaml` defines four contract categories:

| Category | Meaning |
|---|---|
| `behavior` | Role authority and behavioral boundaries |
| `process_artifact` | Stage order, readiness, and expected artifacts |
| `fact_grounding_evidence` | Relationships among claims, sources, evidence, and support records |
| `quality_audience` | Quality requirements, reader requirements, and delivery boundaries |

## Appendix B: Decision Vocabulary

The Orchestrator's legal decisions are:

| Decision | Meaning |
|---|---|
| `continue` | Current-stage requirements are satisfied; proceed |
| `retry_stage` | Re-execute the current stage |
| `delegate_repair` | Delegate a bounded repair to the responsible role |
| `request_human_review` | Request human judgment or approval |
| `block_run` | Prevent the run from advancing |
| `finalize` | Enter finalization after audit and gates are satisfied |

Allowed decisions by stage are defined in `configs/stage_specs.yaml`.

## Appendix C: Control-Surface Index

Authoritative control surfaces fall into four groups:

- runtime state: Runtime Manifest, Workflow State, Artifact Registry, and Event Log;
- evidence and correctness: source evidence, Claim Ledger, gate reports, Audit Report, and support records;
- taste and improvement: Audience Profile, Improvement Ledger, frozen snapshots, and manifestation diagnostics;
- delivery and archive: Finalize Report, reader delivery bundle, audit bundle, and immutable run archive.

Field-level authority is defined in `docs/control-surfaces.md` and `src/multi_agent_brief/orchestrator/runtime_state/`.

## Appendix D: Roles and Stages

```text
doctor (Python)
→ source-discovery
→ input-governance (Python)
→ scout
→ screener
→ claim-ledger
→ analyst
→ delivery-editor
→ auditor
→ finalize (Python)
```

The default topology allows Scout to perform both discovery and screening. Strict topology uses an independent Screener. `human_assisted` topology can introduce people at designated points. Topology changes role assignment, not artifact contracts or control responsibility.

## Appendix E: Evaluation Framework

The repository currently packages 25 public-safe evaluation cases. At the exact head, `pytest --collect-only` collects 3,710 test items. The former are control-plane behavior fixtures and the latter is a test-discovery count; neither is a model-prose quality score. The evaluation design follows the orientation of *AI Agents That Matter*: measure real system behavior, cost, reproducibility, and failure modes rather than comparing only an accuracy score.[P19](#ref-p19) G-Eval and MT-Bench show that LLM judging can be a scalable auxiliary measurement, but position, verbosity, self-preference, and model bias mean it cannot be release authority.[P14](#ref-p14) [P15](#ref-p15)

**Deterministic layers currently covered:** quality gates, feedback classification, runtime blocking, trajectory budgets, provenance projection, source evidence packs, improvement memory, reader residue, delivery truth, forged events, and Hermes static invariants.

**Measurement layers required after v0.5, but not claimed as complete in this report:**

- Claim-level support and citation completeness: check atomic facts and citation quality separately, retaining human adjudication.[P12](#ref-p12) [P13](#ref-p13)
- Same-evidence comparison: freeze the source pack, claim candidates, cutoff time, and model conditions, then compare direct prompting, a skill or agent workflow, vanilla RAG, deterministic freshness, conflict-aware RAG, and BriefLoop.
- Blinded reader quality: use at least two independent human reviewers, predeclare the rubric, and measure materiality, clarity, calibration, and actionability separately.
- LLM-judge sensitivity: use only as a secondary analysis, swap answer positions, control length, use multiple judges, and report disagreement.
- Cost and time: record tokens, model calls, human minutes, rework cycles, and failure-recovery time.
- First-user path: fresh clone, install/doctor, actual role delegation, understanding of blocks, delivery judgment, and points of confusion.
- Dynamic knowledge and conflict: hold questions and source packs fixed while constructing stale parametric knowledge against a new official source, multiple old republications against one new primary source, corrections or retractions that supersede earlier versions, two credible sources that cannot be adjudicated automatically, silent model correction of a source, and same-name entity ambiguity. Planned metrics include `conflict_detection_recall`, `stale_fact_adoption_rate`, `unsupported_resolution_rate`, `transparent_conflict_disclosure_rate`, `source_fidelity_error_rate`, `supersession_resolution_accuracy`, `correct_human_escalation_rate`, `current_value_resolution_accuracy`, `as_of_state_reconstruction_accuracy`, `temporal_operator_selection_accuracy`, `deterministic_resolvability_coverage`, and `unsafe_automatic_resolution_rate`, together with human-adjudication time, model-call cost, and rework cycles.[P22](#ref-p22) [P23](#ref-p23) [P26](#ref-p26) [P28](#ref-p28) [P29](#ref-p29) [P34](#ref-p34) [P35](#ref-p35)

**Planned comparison conditions:**

| Condition | Question answered |
|---|---|
| Direct prompt | What can one model call accomplish? |
| Skill or agent workflow | Are longer instructions and role decomposition sufficient? |
| Vanilla RAG | Is external retrieval alone sufficient? |
| Deterministic-freshness baseline | What can simple version aggregation do for safely total-ordered current-value cases? |
| Conflict-aware RAG | What does explicit conflict detection and resolution improve? |
| BriefLoop | Do freezing, transparent disclosure, human escalation, and release controls add value? |

**Deterministic-resolution eligibility (planned protocol, not a current capability):**

```text
same normalized claim scope
AND explicit version or supersession marker
AND a valid total ordering
AND a current-value question
AND no source-authority conflict
AND no disclosure-policy conflict
→ eligible for deterministic-resolution candidacy

otherwise
→ remain unresolved / require human adjudication
```

All dynamic-knowledge metrics above are planned evaluations; they have not been executed or frozen. Every measurement must distinguish mechanism regression, output quality, runtime delegation, and user usability. Passing one layer cannot stand as evidence for another.

Historical experiment commands use the frozen identifier `MABW-080`. Their status is Archived Experimental, and they are not part of the product path.

## Appendix F: Terminology

| Term | Definition in this report |
|---|---|
| Harness | The operating system around the model: workflow, context, tools, state, artifacts, permissions, and evaluation |
| control surface | A governed field or artifact with a declared writer, freeze point, validation rule, and failure behavior |
| artifact | A persisted workflow output or control record |
| claim | A factual or analytical statement tracked by the workflow |
| Claim Ledger | The Python-frozen authoritative list of claims |
| evidence span | A bounded excerpt from source evidence |
| Claim-Support Matrix | Records connecting atomic claims to evidence spans and proposed support status |
| stage-completion transaction | Deterministic transaction that validates and records stage completion |
| claim-freeze transaction | Deterministic transaction that assigns IDs and freezes claim drafts |
| run integrity | Whether a run remains usable as clean reference evidence |
| immutable archive | Append-only run archive with artifact hashes |
| support calibration | Alignment between wording strength and evidence strength |
| guidance manifestation | Observable presence or absence of approved guidance in output |
| bounded harness proposal | A proposed harness modification with declared scope and preserved behavior |
| held-in / held-out regression | Target-case verification paired with unseen-case no-regression checks |
| same-evidence rerun | Comparison in which the frozen fact layer is held constant |
| release eligibility | Whether declared release conditions have been satisfied |
| parametric knowledge | Knowledge encoded in model parameters during training |
| contextual knowledge | Knowledge supplied in the inference context |
| context-memory conflict | Conflict between contextual and parametric knowledge |
| inter-context conflict | Conflict among external contexts or sources |
| intra-memory conflict | Competing answers encoded within parametric memory |
| temporal knowledge conflict | Conflict caused by facts changing across time |
| source fidelity | Faithfulness of an output to the source it claims to represent |
| supersession | An explicit relation by which a later source or record replaces an earlier one |

## Appendix G: Research and Industry-Practice Reference Matrix

This edition retains v0.6.0's 61 candidates and 40 formal references, then adds and screens two 2026 preprints, producing 63 candidates and 42 formal references. The [single reference index](tech-report-v0.6.1/reference-index.md) governs author, version, type, `supports`, `does_not_support`, and `used_in` metadata; screening and exclusion rationales are recorded in the [reference-screening record](tech-report-v0.6.1/reference-screening.md). P34 and P35 are preprints and do not count as peer-reviewed evidence.

| ID | Full reference | Type |
|---|---|---|
| <a id="ref-p01"></a>P01 | Xu, T., Wen, H., & Li, M. (2026). [*Adapting the Interface, Not the Model: Runtime Harness Adaptation for Deterministic LLM Agents*](https://arxiv.org/abs/2605.22166). arXiv:2605.22166v2. | Preprint |
| <a id="ref-p02"></a>P02 | Zhang, H., Zhang, S., Li, K., Zhang, C., Chen, Y., Zhang, Y., Bai, L., & Hu, S. (2026). [*Self-Harness: Harnesses That Improve Themselves*](https://arxiv.org/abs/2606.09498). arXiv:2606.09498v1. | Preprint |
| <a id="ref-p05"></a>P05 | Lee, Y., Nair, R., Zhang, Q., Lee, K., Khattab, O., & Finn, C. (2026). [*Meta-Harness: End-to-End Optimization of Model Harnesses*](https://arxiv.org/abs/2603.28052). arXiv:2603.28052v1. | Preprint |
| <a id="ref-p06"></a>P06 | Sabharwal, R., Wang, H., Storkey, A., & Pan, J. Z. (2026). [*Multi-Turn Evaluation of Deep Research Agents Under Process-Level Feedback*](https://arxiv.org/abs/2606.09748). SCALE-ICML 2026 workshop paper; arXiv:2606.09748v1. | Workshop paper / preprint |
| <a id="ref-p07"></a>P07 | Shahid, A., Suttie, G., & Black, P. (2026). [*Collaborative Human-Agent Protocol (CHAP)*](https://arxiv.org/abs/2606.09751). arXiv:2606.09751v2. | Preprint |
| <a id="ref-p08"></a>P08 | Santillana, J. S. (2026). [*Precision Is Not Faithfulness: Coverage-Aware Evaluation of Grounded Generation with a Complete Oracle*](https://arxiv.org/abs/2606.09376). arXiv:2606.09376v2. | Preprint |
| <a id="ref-p09"></a>P09 | Wu, Q., et al. (2023). [*AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation*](https://arxiv.org/abs/2308.08155). arXiv:2308.08155v2. | Preprint |
| <a id="ref-p10"></a>P10 | Li, G., Hammoud, H. A. A. K., Itani, H., Khizbullin, D., & Ghanem, B. (2023). [*CAMEL: Communicative Agents for “Mind” Exploration of Large Language Model Society*](https://arxiv.org/abs/2303.17760). NeurIPS 2023; arXiv:2303.17760v2. | Peer-reviewed paper |
| <a id="ref-p11"></a>P11 | Hong, S., et al. (2024). [*MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework*](https://arxiv.org/abs/2308.00352). arXiv:2308.00352v7. | Academic paper / preprint |
| <a id="ref-p12"></a>P12 | Min, S., Krishna, K., Lyu, X., et al. (2023). [*FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation*](https://aclanthology.org/2023.emnlp-main.741/). EMNLP 2023, 12076–12100. DOI: 10.18653/v1/2023.emnlp-main.741. | Peer-reviewed paper |
| <a id="ref-p13"></a>P13 | Gao, T., Yen, H., Yu, J., & Chen, D. (2023). [*Enabling Large Language Models to Generate Text with Citations*](https://arxiv.org/abs/2305.14627). EMNLP 2023; arXiv:2305.14627v2. | Peer-reviewed paper |
| <a id="ref-p14"></a>P14 | Liu, Y., Iter, D., Xu, Y., Wang, S., Xu, R., & Zhu, C. (2023). [*G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment*](https://aclanthology.org/2023.emnlp-main.153/). EMNLP 2023, 2511–2522. DOI: 10.18653/v1/2023.emnlp-main.153. | Peer-reviewed paper |
| <a id="ref-p15"></a>P15 | Zheng, L., et al. (2023). [*Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*](https://arxiv.org/abs/2306.05685). NeurIPS 2023 Datasets and Benchmarks; arXiv:2306.05685v4. | Peer-reviewed paper |
| <a id="ref-p16"></a>P16 | Madaan, A., Tandon, N., Gupta, P., et al. (2023). [*Self-Refine: Iterative Refinement with Self-Feedback*](https://proceedings.neurips.cc/paper_files/paper/2023/hash/91edff07232fb1b55a505a9e9f6c0ff3-Abstract-Conference.html). NeurIPS 2023, Main Conference Track. | Peer-reviewed paper |
| <a id="ref-p17"></a>P17 | Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., & Yao, S. (2023). [*Reflexion: Language Agents with Verbal Reinforcement Learning*](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html). NeurIPS 2023, Main Conference Track. | Peer-reviewed paper |
| <a id="ref-p19"></a>P19 | Kapoor, S., Stroebl, B., Siegel, Z. S., Nadgir, N., & Narayanan, A. (2024). [*AI Agents That Matter*](https://arxiv.org/abs/2407.01502). arXiv:2407.01502v1. | Preprint |
| <a id="ref-p20"></a>P20 | Xia, Y., & Wang, T. (2026). [*ResearchLoop: An Evidence-Gated Control Plane for AI-Assisted Research*](https://arxiv.org/abs/2605.28282). arXiv:2605.28282v1. | Technical report / preprint |
| <a id="ref-p21"></a>P21 | Hu, Y., Zhang, Y., Trager, M., Zhang, Y., Yang, S., Xia, W., & Soatto, S. (2026). [*EvoMAS: Evolutionary Generation of Multi-Agent Systems*](https://arxiv.org/abs/2602.06511). ICML 2026; arXiv:2602.06511v4. | Peer-reviewed paper |
| <a id="ref-p22"></a>P22 | Xu, R., Qi, Z., Guo, Z., Wang, C., Wang, H., Zhang, Y., & Xu, W. (2024). [*Knowledge Conflicts for LLMs: A Survey*](https://aclanthology.org/2024.emnlp-main.486/). EMNLP 2024, 8541–8565. DOI: 10.18653/v1/2024.emnlp-main.486. | Peer-reviewed paper |
| <a id="ref-p23"></a>P23 | Liska, A., Kocisky, T., Gribovskaya, E., et al. (2022). [*StreamingQA: A Benchmark for Adaptation to New Knowledge over Time in Question Answering Models*](https://proceedings.mlr.press/v162/liska22a.html). ICML 2022, PMLR 162, 13604–13622. | Peer-reviewed paper |
| <a id="ref-p24"></a>P24 | Marjanovic, S. V., Yu, H., Atanasova, P., Maistro, M., Lioma, C., & Augenstein, I. (2024). [*DYNAMICQA: Tracing Internal Knowledge Conflicts in Language Models*](https://aclanthology.org/2024.findings-emnlp.838/). Findings of EMNLP 2024, 14346–14360. DOI: 10.18653/v1/2024.findings-emnlp.838. | Peer-reviewed paper |
| <a id="ref-p25"></a>P25 | Jin, Z., Cao, P., Chen, Y., et al. (2024). [*Tug-of-War between Knowledge: Exploring and Resolving Knowledge Conflicts in Retrieval-Augmented Language Models*](https://aclanthology.org/2024.lrec-main.1466/). LREC-COLING 2024, 16867–16878. | Peer-reviewed paper |
| <a id="ref-p26"></a>P26 | Liu, S., Ning, Q., Halder, K., et al. (2025). [*Open Domain Question Answering with Conflicting Contexts*](https://aclanthology.org/2025.findings-naacl.99/). Findings of NAACL 2025, 1838–1854. DOI: 10.18653/v1/2025.findings-naacl.99. | Peer-reviewed paper |
| <a id="ref-p27"></a>P27 | Wallat, J., Nejdl, W., & Sikdar, S. (2026). [*When Facts Change: Temporal Knowledge Conflict Resolution in LLMs*](https://aclanthology.org/2026.findings-acl.103/). Findings of ACL 2026, 2154–2184. DOI: 10.18653/v1/2026.findings-acl.103. | Peer-reviewed paper |
| <a id="ref-p28"></a>P28 | Pham, Q. H., Ngo, H., Luu, A. T., & Nguyen, D. Q. (2024). [*Who’s Who: Large Language Models Meet Knowledge Conflicts in Practice*](https://aclanthology.org/2024.findings-emnlp.593/). Findings of EMNLP 2024, 10142–10151. DOI: 10.18653/v1/2024.findings-emnlp.593. | Peer-reviewed paper |
| <a id="ref-p29"></a>P29 | Li, M., Zhang, H., Fan, H., Ding, J., & Feng, Y. (2026). [*Harmful Factuality: LLMs Correcting What They Shouldn’t*](https://aclanthology.org/2026.findings-eacl.46/). Findings of EACL 2026, 896–912. DOI: 10.18653/v1/2026.findings-eacl.46. | Peer-reviewed paper |
| <a id="ref-p30"></a>P30 | Dhingra, B., Cole, J. R., Eisenschlos, J. M., Gillick, D., Eisenstein, J., & Cohen, W. W. (2022). [*Time-Aware Language Models as Temporal Knowledge Bases*](https://aclanthology.org/2022.tacl-1.15/). TACL 10, 257–273. DOI: 10.1162/tacl_a_00459. | Peer-reviewed journal paper |
| <a id="ref-p31"></a>P31 | Vu, T., Iyyer, M., Wang, X., et al. (2024). [*FreshLLMs: Refreshing Large Language Models with Search Engine Augmentation*](https://aclanthology.org/2024.findings-acl.813/). Findings of ACL 2024, 13697–13720. DOI: 10.18653/v1/2024.findings-acl.813. | Peer-reviewed paper |
| <a id="ref-p32"></a>P32 | Wang, F., Wan, X., Sun, R., Chen, J., & Arik, S. O. (2025). [*Astute RAG: Overcoming Imperfect Retrieval Augmentation and Knowledge Conflicts for Large Language Models*](https://aclanthology.org/2025.acl-long.1476/). ACL 2025, 30553–30571. DOI: 10.18653/v1/2025.acl-long.1476. | Peer-reviewed paper |
| <a id="ref-p33"></a>P33 | Pan, R., Cao, B., Lin, H., et al. (2024). [*Not All Contexts Are Equal: Teaching LLMs Credibility-aware Generation*](https://aclanthology.org/2024.emnlp-main.1109/). EMNLP 2024, 19844–19863. DOI: 10.18653/v1/2024.emnlp-main.1109. | Peer-reviewed paper |
| <a id="ref-p34"></a>P34 | Reddy, V., & Challaram, S. (2026). [*Don’t Ask the LLM to Track Freshness: A Deterministic Recipe for Memory Conflict Resolution*](https://arxiv.org/abs/2606.01435). arXiv:2606.01435v1. | Preprint; no peer-reviewed status stated |
| <a id="ref-p35"></a>P35 | Wang, C., Li, Y., Liu, Y., & Shu, Y. (2026). [*ConflictRAG: Detecting and Resolving Knowledge Conflicts in Retrieval-Augmented Generation*](https://arxiv.org/abs/2605.17301). arXiv:2605.17301v2; submitted to IEEE SMC 2026. | Preprint; acceptance not stated |
| <a id="ref-t01"></a>T01 | van der Aalst, W. M. P., ter Hofstede, A. H. M., Kiepuszewski, B., & Barros, A. P. (2003). [*Workflow Patterns*](https://doi.org/10.1023/A:1022883727209). *Distributed and Parallel Databases*, 14, 5–51. | Peer-reviewed journal article |
| <a id="ref-t02"></a>T02 | Nii, H. P. (1986). [*The Blackboard Model of Problem Solving and the Evolution of Blackboard Architectures*](https://doi.org/10.1609/aimag.v7i2.537). *AI Magazine*, 7(2). | Peer-reviewed journal article |
| <a id="ref-t03"></a>T03 | Meyer, B. (1992). [*Applying “Design by Contract”*](https://ieeexplore.ieee.org/document/161279/). *Computer*, 25(10). | Peer-reviewed journal article |
| <a id="ref-t06"></a>T06 | Moreau, L., & Missier, P. (Eds.). (2013). [*PROV-DM: The PROV Data Model*](https://www.w3.org/TR/prov-dm/). W3C Recommendation, 30 April 2013. | Technical standard |
| <a id="ref-e01"></a>E01 | Srinivasan, A., Shamdasani, S., Araujo, A. F., & de Wasseige, J.; OpenAI & Thrive Holdings. (2026, May 27). [*Building Self-Improving Tax Agents with Codex*](https://openai.com/index/building-self-improving-tax-agents-with-codex/). | First-party engineering case study |
| <a id="ref-e10"></a>E10 | Anthropic. (2026, January 23). [*Building Multi-Agent Systems: When and How to Use Them*](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them). | First-party engineering article |
| <a id="ref-e19"></a>E19 | Weng, L. (2026, July 4). [*Harness Engineering for Self-Improvement*](https://lilianweng.github.io/posts/2026-07-04-harness/). | Research synthesis / technical essay |
| <a id="ref-e20"></a>E20 | Osmani, A. (2026, June 8). [*Loop Engineering*](https://addyo.substack.com/p/loop-engineering). | Engineering article |
| <a id="ref-e21"></a>E21 | Anthropic. (2024, December 19). [*Building Effective Agents*](https://www.anthropic.com/engineering/building-effective-agents). | First-party engineering article |
| <a id="ref-e22"></a>E22 | Nous Research. (n.d.). [*Persistent Memory — Hermes Agent*](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory). Accessed 2026-07-14. | Project documentation |

## Appendix H: Issue Candidate Boundary (Not Shipped)

The Issue Candidate system has not shipped. This report retains only the product boundary: if implemented later, the mechanism must follow the existing principles of deterministic control, frozen artifacts, single writers, structural closure, and human adjudication, and it must not grant agents self-approval or release authority.

This report does not define its fields, schema, categories, state machine, migration, or failure taxonomy. Any concrete contract must be established with its authoritative owner, validators, tests, and current documentation when implementation actually begins.

## Appendix I: Legacy-Identifier Quarantine

BriefLoop is the only current project name. Old commands, module paths, workspace schemas, archived experiment IDs, and historical filenames remain only where compatibility or reproducibility requires them. `docs/briefloop-naming.md` defines the exact literals and their allowed placement.

These literals may not appear in current titles, project descriptions, architecture names, recommended commands, or public branding, and may not imply that two parallel project names exist. Frozen archives and historical IDs must not be rewritten in place. A future migration of technical identifiers requires a compatibility layer and migration tests.

---

*BriefLoop Architecture Reference v0.6.1. Code snapshot `main@47ae439d0206a852a2a223db4051d28f39b54c38` (version file v0.12.1; includes post-tag changes #513–#515), 2026-07-19.*
