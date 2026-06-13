# Orchestrator Contract Model

v0.6 is organized around four public contract categories. This page defines them at an abstract level only. Detailed schema drafts, private prompt notes, evaluation cases, and commercial policy rules are kept out of the public repository until they are stable.

## Behavior Contract

Defines role boundaries: what the main Orchestrator may coordinate, what specialist subagents are responsible for, and which actions should be blocked or escalated.

v0.6.7 control switchboards expose deterministic control recommendations and
record explicit Orchestrator selections. A selection is runtime intent only; it
does not execute a CLI command, subagent, repair action, source collection, or
human approval step.

## Process / Artifact Contract

Defines whether the workflow has progressed through required stages and whether expected artifacts exist before downstream work proceeds.

`orchestrator_control_switchboard.json` and `control_selections.json` are
runtime control context files. They are not expected workflow artifacts, Claim
Ledger evidence, reader-facing output, or default finalize gates.

`source_candidates.yaml` is a source plan, not evidence. It can describe where
the workflow intends to look for sources, but it cannot support a claim. Scout
must extract candidate claims from actual source content or runtime search
results.

After `state stage-complete` succeeds, artifacts produced by that stage are
frozen for downstream stages. If a later stage finds schema mismatch or invalid
content in a frozen upstream artifact, the repair path goes back to the owner
stage; downstream stages must not rewrite the artifact in place.

## Fact-Grounding / Evidence Contract

Defines the expectation that important statements remain traceable to source-grounded evidence, and that unsupported or uncertain claims are not overstated.

v0.6.5 provenance projection can expose citation and control relationships for audit/debug review, but it does not assert semantic truth or replace human evidence review.

`provenance_graph.json` is produced by a Python control tool, not by a formal workflow stage. Artifact contracts mark this with `producer_kind: control_tool`; `producer_stage: provenance` is a control-tool pseudo-producer label, not a `stage_specs.yaml` stage.

For provenance graph edges, `artifact_derived_from` is directional: `from` is the derived/output artifact and `to` is the source/input artifact.

## Quality / Audience Contract

Defines whether the delivered brief is useful for the intended reader, matches the task context, and meets delivery-readiness expectations.

v0.6.8 source appendices are reader-facing delivery artifacts generated from
claims actually cited in the audited brief and resolved through the Claim
Ledger. They must not expose raw claim IDs, source IDs, evidence text, local
paths, or `file://` URLs, and they are not semantic proof that claims are true.

## Public Boundary

This public model intentionally does not publish full schema drafts, exact validation rules, private golden cases, industry-specific policy packs, or agent prompt details.
