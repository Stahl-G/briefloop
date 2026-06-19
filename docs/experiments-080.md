# MABW-080 Experiment Guide

MABW-080 is an experimental metadata harness for comparing approved guidance
manifestation under the same frozen fact layer.

It is not a production workflow gate, not an LLM judge, and not a proof that
output quality improved. It helps operators prepare comparable condition
workspaces, register completed runs, build deterministic scorecard drafts,
import external assessment, and summarize the resulting metadata.

## Status

MABW-080 commands are **Experimental**. They are intended for controlled
experiment setup and analysis, not for normal brief delivery.

The harness currently supports this command loop:

```text
validate-case
-> scaffold-condition
-> run each condition workspace
-> register-run
-> score-run
-> import-assessment
-> summarize
```

Python fills deterministic control fields and validates imported assessment
metadata. Python does not decide whether guidance manifested, whether prose got
better, whether a reader would prefer one output, or whether source support is
semantically complete.

## What 080 Measures

080 is for an approved-guidance manifestation question:

```text
Given the same frozen fact layer, did a specific guidance entry manifest in
the selected assessment target for each condition?
```

The current standard conditions are:

| Condition | Meaning |
|---|---|
| `baseline` | Same frozen fact layer, no Improvement Memory and no prompt-only guidance treatment. |
| `memory` | Same frozen fact layer, with operator-prepared approved Improvement Memory matching the guidance set. |
| `prompt_only` | Same frozen fact layer, with the guidance text injected as prompt-only treatment and no Improvement Memory. |

The harness is designed so source-discovery, input governance, Scout, Screener,
and Claim Ledger can be held constant through a frozen fact layer. Downstream
writing and audit still run through normal runtime handoff and completion
transactions.

## Assessment Targets

MABW-080 separates content-level experiment assessment from delivery-level
checks. The default target is `delivery_brief`, so existing cases keep their
current finalize, reader-clean, and archive semantics unless
`case_manifest.json` opts into another target.

| Target | Meaning | Required control surface |
|---|---|---|
| `delivery_brief` | Full reader-delivery target. Guidance is assessed in the finalized reader delivery. | Terminal workflow, run archive, finalize pass, reader-clean delivery, timing, clean/reference-eligible run, same frozen fact layer, and imported assessment. |
| `auditable_brief` | Content-level target for guidance manifestation in `output/intermediate/audited_brief.md`. | Analyst/Editor/Auditor complete, no active repair, clean/reference-eligible run, frozen `audited_brief`, `audit_report`, and auditor gate report hashes, auditor gates pass, timing, same frozen fact layer, and imported assessment. |

Use `auditable_brief` for the minimum A-controlled memory-manifestation
experiment when finalize formatting, DOCX generation, reader-clean delivery, or
delivery archive creation are not the claim being assessed. This target does
not claim management-ready delivery, reader-clean output, DOCX/PDF quality, or
finalize transform correctness.

Example:

```json
{
  "assessment_target": "auditable_brief"
}
```

## What 080 Does Not Prove

Do not use 080 outputs to claim any of the following unless later evidence
actually supports that stronger claim:

- Improvement Memory improves output quality.
- Python judged guidance manifestation.
- An LLM-only assessment is A-grade causal evidence.
- A public-safe skeleton is an A/B result.
- Invalid or contaminated runs count in A-grade denominators.
- A passing scorecard proves semantic source support.
- A faster or cleaner trace proves model performance.

Allowed wording is narrower:

- "These runs used the same frozen fact layer."
- "This scorecard records deterministic control metadata."
- "This assessment was imported from a human or LLM-assisted human review."
- "In this public-safe case, the observed count was `k` out of `n` interpretable scorecards."
- "Invalid runs were excluded from the interpretable metric denominator and retained as failure evidence."

## Case Layout

A case directory contains the experiment contract:

```text
experiments/080/cases/<case_id>/
  case_manifest.json
  frozen_fact_layer.json
  guidance_set.json
  assessments/
    assessment.template.json
```

Common generated outputs may live under the case directory or be passed
explicitly to commands:

```text
run_records/<condition>/<run_id>.run_record.json
scorecards/<condition>/<run_id>.scorecard.json
scorecards/<condition>/<run_id>.assessed_scorecard.json
case_summary.json
```

The exact output layout is operator controlled. `summarize` discovers
scorecards under the case directory and also accepts repeated `--scorecard`
arguments for files written elsewhere.

## Public Pilot Skeleton

The repository includes a public-safe setup fixture:

```text
experiments/080/cases/solar_public_001/
```

It contains a synthetic frozen fact layer seed archive under:

```text
experiments/080/cases/solar_public_001/seed_archive/
```

It is useful for exercising the command loop and public-safety checks. It is
not a completed baseline, memory, or prompt-only condition run, and it does not
contain A/B evidence.

## End-To-End Flow

The examples below use:

```bash
CASE=experiments/080/cases/solar_public_001
ARCHIVE=$CASE/seed_archive/mabw-20260618T000000Z-solarseed0001/manifest.json
```

### 1. Validate The Case

```bash
multi-agent-brief experiments 080 validate-case "$CASE"
```

This is read-only. It validates `case_manifest.json`,
`frozen_fact_layer.json`, `guidance_set.json`, and public-safe case boundaries.

### 2. Prepare Initialized Condition Workspaces

`scaffold-condition` requires an initialized workspace. It does not create
generic `config.yaml`, `sources.yaml`, `user.md`, or `audience_profile.md`,
because those files are run direction and must remain controlled across
conditions.

Use a seed/template workspace with the same report objective, audience, source
policy, report date, freshness window, and output style for all conditions:

```bash
multi-agent-brief init ./080-baseline --from-onboarding onboarding.json
multi-agent-brief init ./080-memory --from-onboarding onboarding.json
multi-agent-brief init ./080-prompt-only --from-onboarding onboarding.json
```

For `baseline` and `prompt_only`, the workspace must not contain Improvement
Memory files, snapshots, or runtime handoff residues. For `memory`, the
operator must prepare approved Improvement Memory before running fast-rerun.

### 3. Scaffold Each Condition

```bash
multi-agent-brief experiments 080 scaffold-condition \
  --case "$CASE" \
  --condition baseline \
  --workspace ./080-baseline \
  --archive "$ARCHIVE"

multi-agent-brief experiments 080 scaffold-condition \
  --case "$CASE" \
  --condition memory \
  --workspace ./080-memory \
  --archive "$ARCHIVE"

multi-agent-brief experiments 080 scaffold-condition \
  --case "$CASE" \
  --condition prompt_only \
  --workspace ./080-prompt-only \
  --archive "$ARCHIVE"
```

This imports the frozen fact layer through the deterministic fast-rerun
transaction and writes:

```text
experiment/080/condition.json
experiment/080/operator_instructions.md
```

It does not run subagents, gates, finalize, register, score, assess, or
summarize.

### 4. Run Each Condition Workspace

After reading `experiment/080/operator_instructions.md`, run each condition
from Analyst onward:

```bash
multi-agent-brief run --workspace ./080-baseline --recipe fast-rerun --skip-doctor
multi-agent-brief run --workspace ./080-memory --recipe fast-rerun --skip-doctor
multi-agent-brief run --workspace ./080-prompt-only --recipe fast-rerun --skip-doctor
```

Complete the normal downstream workflow for each condition. Source-discovery,
input governance, Scout, Screener, and Claim Ledger are satisfied by import and
must not be replayed for the condition workspace.

### 5. Register Runs

For the default `delivery_brief` target, run registration requires a terminal
completed workspace and an archive under `output/runs/<run_id>/`.

For `auditable_brief`, registration may stop after Auditor completes and the
workflow is ready for Finalize. The run must still be clean/reference-eligible,
have no active repair, preserve the same frozen fact layer, and have valid
frozen hashes for:

```text
output/intermediate/audited_brief.md
output/intermediate/audit_report.json
output/intermediate/gates/auditor_quality_gate_report.json
```

```bash
multi-agent-brief experiments 080 register-run \
  --case "$CASE" \
  --condition baseline \
  --workspace ./080-baseline \
  --output "$CASE/run_records/baseline.run_record.json"
```

Repeat for `memory` and `prompt_only`.

Registration writes only the requested `run_record.json`. It does not mutate
the workspace, the case, the archive, the Claim Ledger, or delivery artifacts.
Contaminated completed runs can be registered as failure evidence, but remain
non-reference-eligible.

### 6. Build Deterministic Scorecard Drafts

```bash
multi-agent-brief experiments 080 score-run \
  --case "$CASE" \
  --run-record "$CASE/run_records/baseline.run_record.json" \
  --output "$CASE/scorecards/baseline.scorecard.json"
```

Repeat for each condition.

`score-run` projects deterministic fields from the registered run and target:

- control integrity
- frozen fact-layer match
- reader-clean and gate/finalize/archive status when required by target
- timing summary
- coverage-delta status when available
- required guidance entry IDs
- `assessment_status: needs_assessment`

For `auditable_brief`, `reader_clean` and finalize delivery fields are marked
`not_required_for_target`, and the control projection uses the frozen audited
brief, audit report, and auditor gate report recorded by `register-run`.

It does not fill guidance manifestation scores.

### 7. Import External Assessment

Copy the assessment template and replace placeholders with the completed run
identity and reviewer assessment:

```text
experiments/080/cases/solar_public_001/assessments/assessment.template.json
```

Then import it:

```bash
multi-agent-brief experiments 080 import-assessment \
  --scorecard "$CASE/scorecards/baseline.scorecard.json" \
  --assessment "$CASE/assessments/baseline.assessment.json" \
  --output "$CASE/scorecards/baseline.assessed_scorecard.json"
```

Assessment files may use:

```text
human
llm_assisted_human_review
llm_only
```

`llm_only` assessment can support `B_integration` metadata, but it cannot
promote relevant guidance scores to `A_controlled`.

Guidance scores use:

| Score | Meaning |
|---|---|
| `0` | not observed |
| `1` | partially observed or weakly manifested |
| `2` | manifested |
| `3` | overapplied |

`overapplication` must be `true` if and only if `manifestation_score` is `3`.

### 8. Summarize The Case

```bash
multi-agent-brief experiments 080 summarize \
  --case "$CASE" \
  --scorecard "$CASE/scorecards/baseline.assessed_scorecard.json" \
  --scorecard "$CASE/scorecards/memory.assessed_scorecard.json" \
  --scorecard "$CASE/scorecards/prompt_only.assessed_scorecard.json" \
  --output "$CASE/case_summary.json"
```

`summarize` aggregates existing scorecards only. It does not rerun workflow
stages, score prose, import assessments, or judge quality.

## Interpreting Validity Classes

| Validity class | Interpretation |
|---|---|
| `A_controlled` | Clean, same-fact-layer, reference-eligible run with required control projections and human or LLM-assisted human assessment for relevant guidance. |
| `B_integration` | Interpretable integration observation, often including `llm_only` assessment or non-A-grade control surface. |
| `invalid_contaminated` | Run is useful failure evidence but not reference-eligible. |
| `invalid_incomplete` | Required run, archive, scorecard, or assessment material is missing or incomplete. |
| `invalid_fact_layer_mismatch` | The run did not use the case frozen fact layer. |

Summary metrics separate:

- `a_grade_count`: `A_controlled` only.
- `interpretable_run_denominator`: `A_controlled + B_integration`.
- `invalid_excluded_count`: invalid scorecards retained as failure evidence but excluded from interpretable manifestation, reader-clean, coverage, and timing metrics.

## Common Failure Modes

### `scaffold-condition` Rejects The Workspace

The workspace must already be initialized and must preserve the intended run
direction. Missing `config.yaml`, `sources.yaml`, `user.md`, or
`audience_profile.md` blocks scaffold.

For `baseline` and `prompt_only`, Improvement Memory residues also block
scaffold. Use a clean condition workspace rather than trying to reuse an old
memory-treated workspace.

### `scaffold-condition` Rejects The Archive

The archive fact layer must match `frozen_fact_layer.json`. This prevents a
condition workspace from silently using different evidence.

### `register-run` Rejects The Workspace

Registration requires a terminal completed run with a matching archive manifest
under `output/runs/<run_id>/`. Non-terminal workspaces and run-ID mismatches are
rejected.

### `score-run` Writes `needs_assessment`

That is expected. Scorecard drafts are deterministic metadata. Manifestation
scores enter only through `import-assessment`.

### `summarize` Shows Zero Scorecards

Place scorecards under the case directory or pass each external scorecard with
`--scorecard`. If two external scorecards would collapse to the same display
path, summarize fails closed so the summary remains auditable.

## Public Wording Checklist

Use these patterns:

- "The run was registered under MABW-080."
- "The scorecard reports deterministic control metadata."
- "The assessment was imported from external review."
- "The summary observed `k/n` interpretable scorecards with score 2 manifestation."
- "Invalid runs were excluded from the interpretable denominator."

Avoid these patterns:

- "MABW proved the guidance improved quality."
- "MABW proved Improvement Memory works."
- "A-controlled effect validated."
- "Memory improves output quality."
- "Prompt-only is worse."
- "Python judged the guidance manifested."
- "The skeleton fixture demonstrates a win."
- "The model performed better."
- "Invalid runs were counted as controlled evidence."

## Current Completion Boundary

As of v0.8.5, MABW has the 080 command loop and pilot-level observation. The
pilot can be interpreted as effect observed, not effect proven.

Formal A-controlled rerun requires v0.8.6 hardening and a fresh 090 experiment.
