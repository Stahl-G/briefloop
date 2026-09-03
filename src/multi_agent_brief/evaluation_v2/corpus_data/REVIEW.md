# Review: 24 legacy fixtures -> 16 generator specs

Source of truth (read-only, never modified):
`src/multi_agent_brief/evaluation_cases/fixtures/manifest.yaml`
(schema `multi-agent-brief-evaluation-cases/v1`, 24 cases, all synthetic).

Scope note (reviewed redefinition of the plan's original Task 6): the original
task -- port annotations and fill locators by hand -- was rejected because the
legacy fixtures contain almost no source documents to point locators at. The
16 kept scenarios therefore became **generator spec inputs** under
`corpus_data/specs/legacy_<case_id>.yaml`: scenario narratives plus
defect/blocking truth, and nothing else. No locators. No case porting. The
legacy fixtures are read-only. The 80-case corpus is produced later by the
Phase-2 generator, which also constructs locator ground truth (see the final
note below).

## Step 1: full 24-case tabulation (spread before any writing)

Columns: initial stage; `findings_any` entries as `type@level`; finding types
in `findings_absent`; `workflow_state.blocked` (`--` means the case declares
no `workflow_state` at all).

| # | case_id | case_type | initial_stage | findings_any (type@level) | findings_absent types | blocked |
|---|---------|-----------|---------------|---------------------------|-----------------------|---------|
| 1 | unsupported_material_fact | workspace | auditor | number_without_source@blocking | -- | true |
| 2 | stale_current_claim | workspace | auditor | stale_source@blocking | -- | true |
| 3 | reader_facing_target_relevance | workspace | finalize | target_relevance_gap@blocking | target_priority_claim_missing_from_summary, number_without_source | true |
| 4 | feedback_triage_required | workspace | doctor | (empty) | -- | false |
| 5 | planned_blocking_issue_cannot_continue | workspace | analyst | (empty) | -- | true |
| 6 | provenance_projection_minimal | workspace | auditor | (empty) | -- | -- |
| 7 | control_switchboard_selection_is_not_execution | workspace | doctor | (empty) | -- | false |
| 8 | reader_facing_source_appendix | workspace | finalize | (empty) | -- | -- |
| 9 | final_abstract_quality_warning_surface | workspace | finalize | final_scope_title_mismatch@warning, final_missing_comparison_basis@warning, final_missing_limitation_section@warning, final_incomplete_key_case_fields@warning, final_unsupported_superlative@warning | (non-type clauses only) | false |
| 10 | reader_clean_failed_no_delivery_promotion | workspace | finalize | (empty) | -- | -- |
| 11 | static_hermes_no_skip_finalize | static_contract | (none) | (empty) | -- | -- |
| 12 | source_evidence_pack_blocks_non_evidence_file | workspace | scout | (empty) | -- | -- |
| 13 | release_readiness_forged_event_blocker | workspace | finalize | (empty) | -- | -- |
| 14 | unauthorized_institution_branding_blocks_release | workspace | finalize | (empty) | -- | -- |
| 15 | mixed_metric_scope_support_blocker | workspace | auditor | claim_support_matrix_blocking_support@blocking | -- | true |
| 16 | media_only_legal_policy_blocks_research_review | workspace | auditor | claim_support_matrix_blocking_support@blocking | -- | true |
| 17 | company_event_missing_latest_official_check | workspace | auditor | claim_support_matrix_blocking_support@blocking | -- | true |
| 18 | third_party_price_snapshot_formal_block | workspace | auditor | claim_support_matrix_blocking_support@blocking | -- | true |
| 19 | formal_release_missing_human_approval | workspace | finalize | (empty) | -- | -- |
| 20 | trajectory_retry_budget_exhausted | workspace | source-discovery | (empty) | -- | false |
| 21 | same_evidence_reader_quality_regression | workspace | finalize | (empty) | -- | false |
| 22 | unapproved_entry_not_materialized | workspace | doctor | (empty) | -- | -- |
| 23 | approved_guidance_materialized | workspace | doctor | (empty) | -- | -- |
| 24 | reverted_entry_removed_from_next_snapshot | workspace | doctor | (empty) | -- | -- |

Tabulation outcome vs. the reviewed expectation: **confirmed exactly**. 24
cases; 8 control-plane exclusions by initial stage (doctor x5, analyst x1,
scout x1, source-discovery x1), all 8 with empty `findings_any`; the remaining
16 split 7 blocking / 1 warning-only / 8 no-defect; finding-type coverage
across the 16 is `claim_support_matrix_blocking_support` x4 plus 8 other types
x1 each, with `target_priority_claim_missing_from_summary` at x0 in
`findings_any` (it appears only in a `findings_absent` clause).

## Excluded: 8 control-plane cases (no spec written)

These exercise control-plane behavior (state machine, control selection,
feedback triage, evidence-pack manifest validation, retry budgets, improvement
memory), not defect detection; they carry no gate-finding semantics that a
role rollout could be scored on.

| case_id | stage | Reason for exclusion |
|---------|-------|----------------------|
| feedback_triage_required | doctor | Human-feedback triage routing; asserts `issues_any`, no gate findings. |
| control_switchboard_selection_is_not_execution | doctor | Control selection records intent without executing; control-plane switchboard behavior. |
| unapproved_entry_not_materialized | doctor | Improvement-memory materialization control (proposed guidance stays unmaterialized). |
| approved_guidance_materialized | doctor | Improvement-memory snapshot freezing control for approved guidance. |
| reverted_entry_removed_from_next_snapshot | doctor | Improvement-memory revert/regeneration control. |
| planned_blocking_issue_cannot_continue | analyst | State-machine bypass prevention; its `blocked: true` is state-machine blocking, not a defect finding. |
| source_evidence_pack_blocks_non_evidence_file | scout | Evidence-pack manifest validation (placeholder file rejected as evidence). |
| trajectory_retry_budget_exhausted | source-discovery | Retry-budget decision narrowing to human-review or block. |

## Classification: the 16 ported scenarios

| case_id | class | role | seeded finding types (level) | must_not_report |
|---------|-------|------|------------------------------|-----------------|
| unsupported_material_fact | blocking | auditor | number_without_source (blocking) | -- |
| stale_current_claim | blocking | auditor | stale_source (blocking) | -- |
| reader_facing_target_relevance | blocking | editor | target_relevance_gap (blocking) | target_priority_claim_missing_from_summary, number_without_source |
| mixed_metric_scope_support_blocker | blocking | auditor | claim_support_matrix_blocking_support (blocking) | -- |
| media_only_legal_policy_blocks_research_review | blocking | auditor | claim_support_matrix_blocking_support (blocking) | -- |
| company_event_missing_latest_official_check | blocking | auditor | claim_support_matrix_blocking_support (blocking) | -- |
| third_party_price_snapshot_formal_block | blocking | auditor | claim_support_matrix_blocking_support (blocking) | -- |
| final_abstract_quality_warning_surface | warning-only | editor | final_scope_title_mismatch, final_missing_comparison_basis, final_missing_limitation_section, final_incomplete_key_case_fields, final_unsupported_superlative (all warning) | -- |
| provenance_projection_minimal | no-defect | auditor | (none) | -- |
| reader_facing_source_appendix | no-defect | editor | (none) | -- |
| reader_clean_failed_no_delivery_promotion | no-defect | editor | (none) | -- |
| static_hermes_no_skip_finalize | no-defect | editor | (none) | -- |
| release_readiness_forged_event_blocker | no-defect | editor | (none) | -- |
| unauthorized_institution_branding_blocks_release | no-defect | editor | (none) | -- |
| formal_release_missing_human_approval | no-defect | editor | (none) | -- |
| same_evidence_reader_quality_regression | no-defect | editor | (none) | -- |

Counts: blocking 7, warning-only 1, no-defect 8. The warning-only case is
`final_abstract_quality_warning_surface` (`blocked: false` in the fixture with
five warning-level findings).

`must_not_report` derivation: strictly from `findings_absent` clauses that
name a `finding_type`. Only `reader_facing_target_relevance` yields entries.

## Role-inference rule as applied

- Specs with seeded defects: role = the role owning the finding's legacy
  `gate_stage_id`. The four auditor-stage types (`number_without_source`,
  `stale_source`, `claim_support_matrix_blocking_support`,
  `target_priority_claim_missing_from_summary`) all carry
  `gate_stage_id: auditor` in the fixture -> `auditor`. The six finalize-stage
  types (`target_relevance_gap` plus the five `final_*` quality types) all
  carry `gate_stage_id: finalize` -> `editor`, the role that owns finalize.
  Note: the plan text said "the 6 `final_*` types", but in the data the
  finalize-gated set of six includes `target_relevance_gap`, which is not
  `final_`-prefixed; the applied rule is stage-based, not prefix-based.
- No-defect specs: role derived from each case's own stage/subject matter:
  - `provenance_projection_minimal`: initial_stage `auditor`, commands are
    audit-stage provenance checks -> **auditor**.
  - `reader_facing_source_appendix`, `reader_clean_failed_no_delivery_promotion`,
    `release_readiness_forged_event_blocker`,
    `unauthorized_institution_branding_blocks_release`,
    `formal_release_missing_human_approval`,
    `same_evidence_reader_quality_regression`: initial_stage `finalize` ->
    **editor**.
  - `static_hermes_no_skip_finalize`: a `static_contract` case with no
    initial_stage; its subject is the gate-before-finalize ordering at
    finalize -> **editor** (weakest port; see anomalies).

The guard test `tests/test_evaluation_v2_specs.py` pins both the rule for
defect-bearing specs and the exact per-case roles for the no-defect specs, so
this table cannot drift from the data.

## Data anomalies found

1. `workflow_state` (and with it `blocked`) is entirely absent for 11 of the
   24 cases, including 7 of the 16 kept ones (every kept case except the 7
   blocking ones and the 2 that explicitly say `false`). Absent is treated as
   non-blocking, consistent with every case that does declare it.
2. `static_hermes_no_skip_finalize` is the only `static_contract` case (the
   other 23 are `workspace`) and has no `initial_stage`. It falls into the
   kept 16 because it is not in the stage-based exclusion set, but it carries
   no workspace semantics at all; its spec is a pure clean-side (true-negative
   narrative) input and the generator should not try to reconstruct a
   control-plane assertion from it.
3. `target_priority_claim_missing_from_summary` never appears in
   `findings_any` anywhere in the fixture set; it exists only as a
   `findings_absent` clause of `reader_facing_target_relevance`. It therefore
   enters these specs only through `must_not_report`, and the Phase-2
   generator must author new positive coverage for it (it is one of the 10
   canonical `FINDING_TYPES` requiring >= 4 corpus cases).
4. `final_abstract_quality_warning_surface` has `findings_absent` clauses on
   `blocking_level` and `repair_owner` rather than `finding_type`. They encode
   "warnings must not escalate to blocking and must not be repaired into the
   delivered brief"; that truth is expressed in its spec through
   `expected_blocking_level: warning` on all five seeded defects, not through
   `must_not_report`.
5. `planned_blocking_issue_cannot_continue` (excluded) shows that `blocked:
   true` alone is not evidence of defect semantics; classification keys off
   `findings_any`, not `blocked`.

## Locator ground truth is not portable

The legacy fixtures assert findings against deterministically seeded Python
state (`gates.check`, `synthetic.seed_claim_support_case`, ...) and contain
almost no source documents, so no locator ground truth can be carried over.
The specs here are deliberately locator-free: `seeded_defects` entries carry
only `defect_id`, `finding_type`, and `expected_blocking_level`. Locator truth
for the 80-case corpus is constructed by the Phase-2 generator when it
materializes source packs from these specs, at which point the strict
`EvaluationCase` contract (which requires a real `locator` and rejects
`TO_BE_ANNOTATED`) takes over.

## Packaging note

`corpus_data/specs/` is build-time input only. The package-data list in
`pyproject.toml` pins exactly `corpus_data/manifest.yaml`,
`corpus_data/cases/*.yaml`, and `corpus_data/REVIEW.md`; no pattern matches
`specs/`, so the spec files stay out of runtime package data. Both
`tests/test_evaluation_v2_corpus.py` (exact pattern-set pin) and
`tests/test_evaluation_v2_specs.py` (specs-stay-out pin) enforce this.

## P2-T0 locator anchor observations (2026-09-03, 2 real codex exec rollouts)

Measured on staged demo workspaces with the packaged kit role instructions
(auditor on a seeded defective brief; editor writing its own brief, scored by
the offline deterministic gate evaluation). Raw payloads archived under the
repo's ignored planning directory.

Observed anchor shapes:

| Defect anchor | Canonical finding shape | Locator rule |
|---|---|---|
| Seeded source/claim (`claim_ledger`) | `stale_source` with `claim_id=CL-XXXX`, no line | locator = `<claim_id>` |
| Seeded brief (`audited_brief`, auditor role) | gate finding `artifact_id=audited_brief` + `line_number` in the seeded file | locator = `<artifact>#L<line>` |
| Rollout-written brief (editor role) | `target_relevance_gap` and `final_*` anchored on `audited_brief` with the line inside agent-written prose | locator = `<artifact>` only; the line is not knowable before rollout |

Verdict for the generator: an anchor whose artifact is seeded before the
rollout may carry a line number; an anchor whose artifact the rollout writes
must stay anchor-only. `validate_corpus` will enforce this by finding-family
plus `rollout.role`, together with "at most one seeded defect per finding_type
per case" (anchor-level locators cannot disambiguate same-type siblings).

Agent-written `audit_report.json` findings are free-typed and carry no anchor
fields (observed: `unsupported_fact_missing_citation` instead of
`number_without_source`, positions only in prose). Scoring therefore reads
canonical Python gate findings; if an eval envelope wants agent-reported
findings, the envelope's task instructions must constrain the reporting
vocabulary and anchor fields — the role contract itself is not edited.

Corpus construction constraints observed the hard way: `config.yaml` must
agree with seeded content (a company-name mismatch produced a whole extra
finding), claim-count minimums must match corpus scale (`min_items` noise),
and source dates must be constructed relative to the case `report_date`
(the freshness gate blocks anything outside the window).

Follow-up smoke (constrained envelope, same case content): with the
harness-owned reporting contract injected into the envelope, the real auditor
produced exactly two findings — `stale_source` anchored `related_claim_id:
CL-0001` and `number_without_source` anchored `line_number: 7` (byte-verified
against the seeded brief line). Constraint produces compliance; the agent
dropped an inexpressible out-of-scope finding it had reported when
unconstrained, which is precisely why format compliance is reported alongside
the reward. The rollout writes its report under the envelope scratch
directory, so the adapter harvests there.
