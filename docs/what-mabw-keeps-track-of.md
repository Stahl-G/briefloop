# What MABW Keeps Track Of

Chinese version: `docs/what-mabw-keeps-track-of.zh-CN.md`.

MABW is not only a tool for drafting briefs. It is a workflow that keeps track of the process behind a brief.

For writers and business users, the useful mental model is not a list of control files. It is the weekly loop:

```text
Where the brief stands.
Where each number came from.
What approved guidance was explicitly carried.
What is guarding delivery.
```

## The Promise

BriefLoop can observe and suggest, but it does not learn automatically. Human
review records approved guidance in SQLite, and a later run uses it only when a
Human explicitly opts in while starting a normal same-workspace successor.

In shorter form:

> Approval is necessary, but reuse still requires a separate explicit successor request.

Observation is not authority. The system may suggest a preference, format rule,
or fact check, but only compatible, active Human-approved guidance can reach a
successor, and only after `--include-approved-guidance` opt-in.

In plain terms:

> AI can draft; deterministic services record effects; only a Human can approve and explicitly carry guidance.

## 1. Where The Brief Stands

What it records:

- which stage is current;
- which stages are complete, pending, or blocked;
- which artifacts are expected;
- which decisions were recorded;
- what the Orchestrator is allowed to do next.

Where to look:

- `output/intermediate/workflow_state.json`
- `output/intermediate/event_log.jsonl`
- `output/intermediate/artifact_registry.json`
- `output/intermediate/runtime_manifest.json`
- `output/intermediate/agent_handoff.md`

How it protects you when things go wrong:

> A brief is not "done" because an agent says it is done. MABW can tell you where the run is, what is missing, and why it can or cannot move forward.

## 2. Where Each Number Came From

What it records:

- sources and support for claims, numbers, dates, company facts, policies, prices, capacity, customers, and project status;
- audit and quality-gate findings tied to those claims;
- source appendix entries appended to the delivery `brief.md` / DOCX.

Where to look:

- `output/intermediate/claim_ledger.json`
- `output/intermediate/gates/auditor_quality_gate_report.json`
- `output/intermediate/gates/finalize_quality_gate_report.json`
- `output/intermediate/quality_gate_report.json` (latest/legacy projection)
- `output/intermediate/audit_report.json`
- `output/delivery/brief.md`
- `output/delivery/<named>.docx`
- `output/source_appendix.md`
- `output/intermediate/provenance_graph.json`

How it protects you when things go wrong:

> Pick a number in the final brief and ask "where did this come from?" The workflow should point back to the claim, source, date, and checks instead of relying on memory or model confidence.

## 3. What Approved Guidance Was Explicitly Carried

What it records:

- lead with business impact before background;
- avoid making decisions on behalf of management;
- use a more concise executive tone;
- explain uncertainty before giving a recommendation.

These are not facts. They are preferences about how the brief should be written for a reader.

Where to look:

- Store-native post-final review status for the exact assessment result;
- the Receipt from `briefloop runtime successor-start`; and
- the successor's immutable `RoleTaskEnvelope.frozen_guidance_context` for
  Analyst or Editor.

The former Improvement JSON/JSONL ledger, memory projection, and Markdown
snapshot are retired and inert.

How it protects you when things go wrong:

> Unapproved, inactive, reverted, superseded, malformed, or scope-mismatched guidance is not consumed. Approval alone does nothing to a later run; the Human must explicitly opt in when starting the successor. Once frozen, that successor snapshot does not change when the live guidance lifecycle changes.

## 4. What Is Guarding Delivery

What it records:

- contracts, gates, policies, and delivery checks;
- required artifacts that must exist before a stage can be considered complete;
- final reader-clean checks such as local paths, internal claim IDs, and blank source rows.

Where to look:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`
- `output/intermediate/gates/auditor_quality_gate_report.json`
- `output/intermediate/gates/finalize_quality_gate_report.json`
- `output/intermediate/quality_gate_report.json` (latest/legacy projection)
- `output/intermediate/repair_plan.json`

How it protects you when things go wrong:

> Some requirements are not preferences. They are delivery checks that protect you before the brief is finalized, and they should block delivery rather than become soft memory.

## When Feedback Is Routed Somewhere Else

Users should not have to decide whether feedback is a taste preference, a structure rule, a fact correction, or a delivery gate. MABW should translate natural feedback into the right route.

Example feedback:

> "Next time, lead every news item with the impact on our company, then give background, and do not make decisions for management."

MABW may split that into:

| User-facing bucket | Internal route | Example |
|---|---|---|
| Writing preference | `approved_guidance_candidate` | Lead with company impact. |
| Fixed format candidate | `checkable_rule_candidate` | Use implication -> fact -> uncertainty for each item. |
| Style boundary | `approved_guidance_candidate` or future checklist | Do not decide for management. |
| Fact or source check | `fact_review` | Correct a price, date, source, or company status. |
| Already covered | `already_enforced` | Source appendix is already checked before delivery. |

The user should see the system's interpretation before anything persistent changes.

## Suggested User-Facing Responses

### Already Enforced

Avoid saying only "supported" or "already handled." Show the mechanism and where the user can verify it.

Suggested wording:

> This is already enforced as a delivery standard. Each run checks for the source appendix before final delivery; if it is missing, the run should not finalize. You can see the result in the delivery check record.

### Fact Or Source Review

Do not make this sound like a rejection from memory. It is a stronger route.

Suggested wording:

> Understood. This involves a concrete fact or source, so it is more important than remembering it as a writing habit. I have routed it to this run's fact/source review.

### Fixed Format Candidate

Make the upgrade clear: a checkable rule should not remain a soft memory forever.

Suggested wording:

> Understood. This is a fixed format requirement that should be enforced every time. Writing it as a preference would be weak; I recommend promoting it to a template or delivery rule after review.

### Writing Preference

Show that approval controls future effect.

Suggested wording:

> I can remember this as a writing preference for future runs. It will not affect future output unless you approve it.

## Candidate Suggestions Should Be Visible

If MABW proposes a preference or rule, that proposal should be visible by default. Hidden suggestions reduce trust.

A future candidate view should group suggestions like this:

```text
Writing preferences awaiting your confirmation
Suggested fixed-format rules
Facts or sources that need review
Items already enforced by the system
```

Users should be able to confirm, edit, or dismiss suggestions. A candidate parking lot should also be easy to clear; a full inbox of old suggestions is worse than no suggestions.

## Bulk Confirmation

Not all confirmations have the same risk.

If a user gives one feedback sentence and MABW splits it into several pieces, the UI may show all pieces and allow one submit action after the user has seen them.

If the system inferred preferences from past accepted samples, the user did not explicitly say those things. Those machine-proposed preferences should be reviewed one by one before adoption.

Rule of thumb:

```text
User-said feedback: grouped review is acceptable.
Machine-inferred preference: one-by-one adoption.
```

## What MABW Should Not Claim

MABW should not claim that it automatically improves output quality just because a control surface exists.

Current control surfaces can prove things like:

- an approved guidance entry was recorded;
- a snapshot was frozen;
- an explicitly started successor referenced that snapshot;
- a gate report was written;
- a claim had a cited source;
- a feedback issue was structured.

They do not by themselves prove:

- the model fully followed the guidance;
- the final prose improved;
- no useful structure regressed;
- all relevant facts were covered.

Guidance utility is NOT MEASURED. The snapshot is presentation context for
Analyst/Editor only; it is not evidence, a Gate rule, a repair command, or
finalize/delivery/Core authority.

Those require separate evaluation, reference runs, and Human review; this
mechanism makes no such quality claim.

## The One-Minute Demo

For non-agent audiences such as IR, compliance, or business reviewers, do not start with "multi-agent workflow."

Start with a finished brief.

Point to one number and ask:

> Where did this number come from?

Then trace:

```text
final sentence
-> claim ledger entry
-> source and date
-> gate or audit finding
-> source appendix
-> any approved reader guidance that affected wording
```

That is the user-level meaning of process-level accountability.

## Related

- `docs/control-surfaces.md`
- `docs/architecture-status.md`
- `docs/support-matrix.md`
- `docs/modules/improvement.md`
- `docs/archive/design-note-preference-taste-governance-2026-06-11.md`
