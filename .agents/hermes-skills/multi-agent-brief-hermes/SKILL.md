---
name: multi-agent-brief-hermes
description: Use this skill to run BriefLoop workspaces inside Hermes using Hermes delegate_task subagents, source cache, cron scheduling, and final rendering tools.
version: 0.11.12
author: multi-agent-brief-workflow
license: MIT
platforms:
  - linux
  - macos
  - windows
tags:
  - hermes
  - cron
  - brief
  - research
  - workflow
  - delegate_task
---

# BriefLoop for Hermes

Use this skill to run BriefLoop workspaces inside Hermes using Hermes delegate_task subagents, source cache, cron scheduling, and final rendering tools.

## Operating Model

Hermes is a native BriefLoop runtime. The Hermes parent agent is the Orchestrator main agent: it reads shared contract references and runtime state files, manages artifact handoff, checks expected artifacts, and selects the next workflow decision. Hermes `delegate_task` children run scout, screener, claim-ledger, analyst, editor, and auditor tasks as isolated subagents. Python CLI tools handle init, doctor, sources decide, input extraction/classification, state checks, feedback ingest/plan/resolve/show/validate, gates check/show/validate, provenance build/show/validate, audit, finalize, and rendering support. Cron jobs provide durable scheduling; `delegate_task` provides child task dispatch within each run.

Contract references:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

Runtime state files:

- `output/intermediate/runtime_manifest.json`
- `output/intermediate/workflow_state.json`
- `output/intermediate/artifact_registry.json`
- `output/intermediate/event_log.jsonl`

Audience memory files:

- `audience_profile.md`
- `output/intermediate/audience_profile_snapshot.md`

Read the snapshot at run start, summarize relevant taste guidance for delegated roles, and do not treat `audience_profile.md` as source evidence or a correctness contract. Do not treat `audience_profile.md` as evidence. Mid-run profile edits apply to the next run.

Control switchboard files:

- `output/intermediate/orchestrator_control_switchboard.json`
- `output/intermediate/control_selections.json`

Read the switchboard after handoff, record enable/defer/reject choices with `briefloop controls select`, and then explicitly run the selected CLI/subagent/human action. Selection is not execution.

Optional feedback state files:

- `output/intermediate/feedback_issues.json`
- `output/intermediate/repair_plan.json`
- `output/intermediate/delta_audit_report.json`

Optional quality gate state files:

- `output/intermediate/gates/auditor_quality_gate_report.json`
- `output/intermediate/gates/finalize_quality_gate_report.json`
- `output/intermediate/quality_gate_report.json` (legacy/latest projection)

Optional provenance projection files:

- `output/intermediate/provenance_graph.json`

Orchestrator control loop:

```text
Read workspace context -> read contract references -> identify the next stage -> delegate a specialist or Python tool -> check the expected artifact -> decide continue / retry_stage / delegate_repair / request_human_review / block_run / finalize.
```

Brief generation follows the BriefLoop subagent workflow:

```text
default: scout(discovery+screening) -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor -> finalize
strict: scout -> screener -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor -> finalize
```

Success path after `audit_report.json`: gates check + state check + state stage-complete, then finalize and finalize-complete.

## Setup Workflow

### Preferred Path: Hermes Plugin

Use the BriefLoop Hermes plugin when it is installed:

```text
/mabw <workspace>
→ mabw_create_onboarding (if workspace is new)
→ mabw_init_workspace
→ mabw_run_handoff
→ read agent_handoff.md
→ continue delegated workflow
```

Install from the repository:

```bash
cp -R integrations/hermes-plugin/mabw ~/.hermes/plugins/mabw
hermes plugins enable mabw
```

### Fallback: chat-to-JSON onboarding

If the plugin is unavailable, use fallback onboarding: Collect brief profile in chat. Write `onboarding.json`, validate it with `briefloop onboard --validate onboarding.json`, initialize with `briefloop init <workspace> --from-onboarding onboarding.json`, then create the handoff with `briefloop run --workspace <workspace>`. Do not call `briefloop run` again mid-pipeline to refresh handoff or state; use status, state, gates, and repair commands instead.

1. Clone or open the repository.
2. Create and activate the Python virtual environment.
3. Install BriefLoop.
4. Initialize the requested workspace.
5. Run doctor:

```bash
briefloop doctor --config <workspace>/config.yaml
```

6. Report the repo path, venv path, workspace path, version, and doctor status.
7. Offer to continue with a Hermes-native delegated brief run.

After a successful setup, present the result like this:

```
Project is cloned and ready.

Repository: <repo>
Virtual environment: <venv>
Workspace: <workspace>
Version: <version>
Doctor: passed

I can continue generating the brief inside Hermes. The next step uses the Hermes Orchestrator main agent with delegate_task children for:
default topology uses scout(discovery+screening) -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor -> finalize.
strict topology uses scout -> screener -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor -> finalize.
```

## Daily Source Cache Workflow

1. Read workspace `config.yaml`, `sources.yaml`, and `user.md`.
2. Collect public, citable source signals.
3. Write JSON cache to `input/hermes_cache/YYYY-MM-DD.json`.
4. Use this item shape when possible:

```json
{
  "source_id": "HERMES_YYYYMMDD_001",
  "source_name": "Source name",
  "source_type": "hermes_daily_cache",
  "title": "Short source title",
  "content": "Concise factual summary with enough context for claim extraction.",
  "url": "https://example.com/source",
  "published_at": "YYYY-MM-DD",
  "reliability": "high",
  "metadata": {
    "collected_by": "hermes",
    "collection_cadence": "daily"
  }
}
```

5. Report saved item count, source gaps, and cache file path.
6. Daily cache mode ends after source cache reporting.

## Hermes-native Delegated Brief Workflow

### Parent Orchestration

The Hermes parent agent is the Orchestrator main agent for the full pipeline:

1. Read contract references:
   - `configs/orchestrator_contract.yaml`
   - `configs/stage_specs.yaml`
   - `configs/artifact_contracts.yaml`
   - `configs/policy_packs/default.yaml`

2. Read workspace files:
   - `config.yaml`
   - `sources.yaml`
   - `user.md`
   - `output/intermediate/audience_profile_snapshot.md`
   - `output/intermediate/orchestrator_control_switchboard.json`
   - `input/`
   - `input/hermes_cache/` when present

3. Summarize relevant taste guidance from `output/intermediate/audience_profile_snapshot.md` for delegated roles. Do not treat the profile as source evidence.

4. Read the Orchestrator control switchboard and record control selections with `briefloop controls select`. Selection is not execution.

5. Run doctor:

```bash
briefloop doctor --config <workspace>/config.yaml
```

6. If source discovery is configured:

```bash
briefloop sources decide --config <workspace>/config.yaml
```

Review and merge according to workspace policy.
If runtime WebSearch reports `Did 0 searches`, or every query returns an empty result set, stop and request human review. Do not switch to source-planner or continue with stale sources.

6. Extract non-text input files when present:

```bash
briefloop inputs extract --config <workspace>/config.yaml
```

This converts PDF/DOCX/image inputs to adjacent `.mineru.md` files before classification. Directory role still controls claim eligibility: eligible evidence files under `input/sources/` count as evidence; binary inputs require extracted Markdown before use. Extracted files under `input/context/`, `input/instructions/`, and `input/feedback/` are not evidence.

7. Classify input files:

```bash
briefloop inputs classify --config <workspace>/config.yaml
```

8. Create `output/intermediate/` if it does not exist.

9. Delegate child tasks with complete context and explicit artifact paths. Use `delegate_task` for each step.

10. After each child returns, verify the expected artifact exists and is non-empty before selecting the next decision.

11. If audit findings or human feedback exist, use `briefloop feedback ingest`, `feedback plan`, `feedback resolve`, `feedback show --json`, and `feedback validate`; these commands structure and record issues but do not execute repair.

12. Repair guidance is bounded runtime guidance, not an automatic trajectory regulator. If the same stage has already needed roughly three retry/repair rounds, prefer `request_human_review` or `block_run`; if a repair would touch more than two sections, narrow the scope before delegating or request human review.

13. After `audit_report.json` exists, run deterministic quality gates and refresh runtime state:

```bash
briefloop gates check --workspace <workspace> --stage auditor
briefloop state check --workspace <workspace> --strict
```

14. If state is not blocked, record the auditor decision:

```bash
briefloop state stage-complete --workspace <workspace> --stage auditor --reason "Audit and quality gates passed."
```

If state is blocked by owner-stage artifact repair, run `briefloop repair route --workspace <workspace>` and `briefloop repair start --workspace <workspace>`; otherwise choose `request_human_review` or `block_run`. Audit warnings, overstatement findings, support-calibration findings, and quality-gate findings do not authorize direct edits to frozen artifacts. Do not finalize.

15. Run finalize only after the gates/state completion path passes. `finalize` is not a quality-gate executor:

```bash
briefloop finalize --config <workspace>/config.yaml
```

16. After finalize writes reader-facing artifacts, verify completion:

```bash
briefloop gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md
briefloop state finalize-complete --workspace <workspace> --reason "Reader-facing artifacts passed finalize checks."
```

17. Optional audit/debug provenance projection after runtime state exists:

```bash
briefloop provenance build --workspace <workspace>
briefloop provenance show --workspace <workspace> --json
briefloop provenance validate --workspace <workspace>
```

Provenance projection is not semantic proof and is not required before finalize.

15. Report artifact paths, audit status, quality gate status, and optional provenance graph path when created.

### Delegation Sequence

#### 1. Scout child

Use `delegate_task` to extract candidate reportable items. In default topology,
the same Scout child also screens those candidates and writes
`screened_candidates.json`; in strict topology, Scout stops after discovery.

```python
delegate_task(
    goal="Extract candidate reportable items for a BriefLoop brief",
    context="""
Workspace: <workspace>
Read approved evidence inputs, cached source packages, local source files, and source config.
Write:
- <workspace>/output/intermediate/candidate_claims.json
- default topology only: <workspace>/output/intermediate/screened_candidates.json

Discovery output must capture the found universe before screening.
Each item should preserve source identity, source date if available, evidence text, topic, claim type, and confidence.
Use source_url only for HTTP(S) URLs. Use source_path for local/package sources.
Preserve source_title/source_name, publisher when known, source_category, and provider source_type.
In default topology, also rank, dedupe, freshness-check, capacity-cap, and write selected/excluded candidates with reasons plus a screening_policy snapshot.
Return a summary with candidate count, selected count, excluded count, and source gaps.
""",
    toolsets=["file", "terminal", "web"]
)
```

For independent source clusters, the parent may use batch delegation with up to
3 scout children. Those child outputs are scratch/intermediate runtime material,
not workflow artifacts. The parent must join chunk outputs deterministically
before writing `candidate_claims.json`: stable ordering must use source identity,
source path or URL, source date, topic, and evidence text, not child completion
order. Duplicates or near-duplicates must be represented or excluded with
reasons; do not silently drop chunk-level outputs. Only the final joined
`candidate_claims.json` and, in default topology, `screened_candidates.json`
count for stage completion.

#### 2. Screener child (strict topology or explicit repair/review)

In default topology, do not delegate Screener and do not call
`state stage-complete --stage screener`; Scout writes `screened_candidates.json`
and the Screener stage is satisfied by topology after Scout completion.

```python
delegate_task(
    goal="Screen and rank BriefLoop candidate claims",
    context="""
Workspace: <workspace>
Input: output/intermediate/candidate_claims.json
Write: output/intermediate/screened_candidates.json

Rank, dedupe, freshness-check, and capacity-cap candidate items.
Preserve source identity and evidence fields.
Return included count, excluded count, and main exclusion categories.
""",
    toolsets=["file", "terminal"]
)
```

#### 3. Claim-ledger child

```python
delegate_task(
    goal="Build the BriefLoop Claim Ledger",
    context="""
Workspace: <workspace>
Input: output/intermediate/screened_candidates.json
Write: output/intermediate/claim_drafts.json

Create source-grounded claim drafts without claim_id fields.
Preserve evidence text, source URL/path, source title/name, publisher, source_category, provider source_type, publication date, retrieved date, topic, claim type, and confidence.
source_url is only for HTTP(S) URLs; never put titles, source names, source IDs, search queries, or local paths in source_url.
Return claim count and schema issues found.
""",
    toolsets=["file", "terminal"]
)
```

After `claim_drafts.json` exists, freeze the Claim Ledger and record the
Claim Ledger stage completion before delegating Analyst:

```bash
briefloop state freeze-claim-ledger --workspace <workspace>
briefloop state stage-complete --workspace <workspace> --stage claim-ledger --reason "Claim Ledger frozen from claim drafts."
```

#### 4. Analyst child

```python
delegate_task(
    goal="Draft the audited BriefLoop brief",
    context="""
Workspace: <workspace>
Inputs:
- user.md
- output/intermediate/claim_ledger.json

Write:
- output/intermediate/audited_brief.md as the Analyst working draft

Write a management-ready brief in the workspace language.
Use Claim Ledger evidence for factual statements.
If output/intermediate/atomic_claim_graph.json is present and valid, use it only as an optional experimental structural decomposition aid for frozen Claim Ledger claims; it is not source evidence or proof of support.
Preserve valid [src:<claim_id>] citations that use real Claim Ledger IDs.
Do not cite atom IDs in reader-facing prose.
Do not introduce material atoms absent from the frozen Claim Ledger and, when present and valid, atomic_claim_graph.json.
Do not create, edit, rewrite, repair, or extend atomic_claim_graph.json; if it is absent or invalid, do not repair it.
Include source dates where useful.
Return a section summary and any source limitations.
Do not write analyst_draft_snapshot.md; Python freezes that control artifact during analyst stage-complete.
""",
    toolsets=["file", "terminal"]
)
```

#### 5. Editor child

```python
delegate_task(
    goal="Polish the audited BriefLoop brief",
    context="""
Workspace: <workspace>
Inputs:
- output/intermediate/analyst_draft_snapshot.md
- output/intermediate/audited_brief.md
Write:
- output/intermediate/audited_brief.md as the Editor-owned final auditable brief

Improve readability, structure, and executive tone.
Preserve factual scope, uncertainty, and valid [src:<claim_id>] citations that use real Claim Ledger IDs.
If output/intermediate/atomic_claim_graph.json is present and valid, use it only as an optional experimental structural decomposition aid; if it is absent or invalid, do not repair it.
Do not create, edit, rewrite, repair, or extend atomic_claim_graph.json.
Do not introduce material atoms absent from the frozen Claim Ledger and, when present and valid, atomic_claim_graph.json.
Do not cite atom IDs in reader-facing prose.
Return edits made and any unresolved issues.
""",
    toolsets=["file", "terminal"]
)
```

#### 6. Auditor child

```python
delegate_task(
    goal="Audit the BriefLoop brief against the Claim Ledger",
    context="""
Workspace: <workspace>
Inputs:
- output/intermediate/audited_brief.md
- output/intermediate/claim_ledger.json

Write:
- output/intermediate/audit_report.json

Check source support, orphan citations, unsupported numbers, missing dates, stale framing, process residue, and delivery readiness.
Return audit status, blocking findings, and recommended fixes.
""",
    toolsets=["file", "terminal"]
)
```

#### 7. Finalize

Parent runs:

```bash
briefloop finalize --config <workspace>/config.yaml
```

Formatter/finalize reads `output/intermediate/audited_brief.md` as frozen input.
It may write reader delivery artifacts and finalize control records only. If
reader-clean requires wording changes in the audited brief, route repair to Editor;
do not patch `audited_brief.md`, `audit_report.json`, artifact registry, or
workflow state.

Then reports delivery artifacts:

- `output/delivery/brief.md`
- `output/delivery/<named>.docx` if configured

Internal audit/control records remain available:

- `output/intermediate/audited_brief.md`
- `output/intermediate/claim_ledger.json`
- `output/intermediate/audit_report.json`
- `output/intermediate/finalize_report.json`
- `output/source_appendix.md` when configured

## Source Cache Contract

The BriefLoop `cached_package` provider can read JSON, Markdown, and text files from the configured cache directory. Prefer JSON arrays or objects with an `items` array. Each item should preserve URL, publication date, source name, and reliability where available.

## Hermes Cron Notes

- Attach this skill to each cron job with `--skill multi-agent-brief-hermes`.
- Use `--workdir <repo-root>` so Hermes loads repository instructions and runs commands from the project.
- Pin `--profile <name>` when the Hermes profile already exists.
- Hermes delivers the final response through the configured cron destination.
