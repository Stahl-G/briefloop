---
name: multi-agent-brief-hermes
description: Runs Multi-Agent Brief Workflow workspaces inside Hermes using delegate_task child agents, source cache, cron scheduling, and final rendering. Use when the user asks Hermes to generate, schedule, continue, or inspect a MABW brief from a workspace.
license: MIT
compatibility: Requires Hermes with delegate_task support plus terminal and file access to a workspace with the multi-agent-brief CLI installed.
metadata:
  author: multi-agent-brief-workflow
  version: 0.8.3
  tags:
    - hermes
    - cron
    - brief
    - research
    - delegate-task
---

# Multi-Agent Brief Workflow for Hermes

## Scope

This skill is the Hermes runtime contract for MABW. It applies when Hermes is asked to set up, schedule, or run a MABW workspace.

The Hermes parent agent is the Orchestrator main agent. It reads shared contract references and runtime state files, controls delegated stages, checks expected artifacts, and selects the next workflow decision.

Contract references: `configs/orchestrator_contract.yaml`, `configs/stage_specs.yaml`, `configs/artifact_contracts.yaml`, and `configs/policy_packs/default.yaml`.

Runtime state files: `output/intermediate/runtime_manifest.json`, `output/intermediate/workflow_state.json`, `output/intermediate/artifact_registry.json`, and `output/intermediate/event_log.jsonl`.

Audience memory files: `audience_profile.md` and `output/intermediate/audience_profile_snapshot.md`. Read the snapshot at run start, summarize relevant taste guidance for delegated roles, and do not treat `audience_profile.md` as source evidence or a correctness contract. Mid-run profile edits apply to the next run.

Control switchboard files: `output/intermediate/orchestrator_control_switchboard.json` lists available/recommended controls, and `output/intermediate/control_selections.json` records Orchestrator enable/defer/reject choices. Selection is not execution.

Control files are produced by explicit commands: feedback uses `output/intermediate/feedback_issues.json`, `output/intermediate/repair_plan.json`, and `output/intermediate/delta_audit_report.json`; gates check creates stage-scoped reports under `output/intermediate/gates/` and also refreshes `output/intermediate/quality_gate_report.json` as a legacy/latest projection; provenance build creates `output/intermediate/provenance_graph.json` as an audit/debug projection, not semantic proof. Before finalize, `gates check --stage auditor` is required; after finalize, `gates check --stage finalize --brief <workspace>/output/brief.md` is required before `finalize-complete`.

Orchestrator control loop:

```text
Read workspace context -> read contract references -> identify the next stage -> delegate a specialist or Python tool -> check the expected artifact -> decide continue / retry_stage / delegate_repair / request_human_review / block_run / finalize.
```

## Use When

Use this skill when the user asks Hermes to create or continue a MABW workspace, generate a management/market/policy/competitor/research brief, or schedule source cache and brief generation.

## Preferred Path: Hermes Plugin

Install and enable the MABW Hermes plugin, then use the plugin tools in Hermes:

```text
/mabw <workspace>
→ mabw_create_onboarding
→ mabw_init_workspace
→ mabw_run_handoff
→ read agent_handoff.md
→ continue delegated workflow
```

Install:

```bash
cp -R integrations/hermes-plugin/mabw ~/.hermes/plugins/mabw
hermes plugins enable mabw
```

The plugin's `references/onboarding-json.md` has the detailed JSON shape and field notes.

## Fallback Path: chat-to-JSON Onboarding

When the plugin is not available, run onboarding as a chat-to-JSON workflow:

1. Collect brief profile in chat — ask for company, industry, task objective, audience, language, cadence, source style, output style, must-watch topics, excluded sources, and source/search mode. Accept natural-language answers and confirm defaults.
2. Write `onboarding.json` from the collected answers.
3. Validate: `multi-agent-brief onboard --validate onboarding.json`
4. Create the workspace: `multi-agent-brief init <workspace> --from-onboarding onboarding.json`
5. Create runtime handoff: `multi-agent-brief run --workspace <workspace>`
6. Read `agent_handoff.md`, `workflow_state.json`, `artifact_registry.json`, and optional feedback state references, then continue with the delegated workflow below.

## Existing Workspace Path

For a workspace that already has `config.yaml`:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
multi-agent-brief hermes prompt --config <workspace>/config.yaml
```

## Delegated Brief Run

```text
doctor → source discovery → input governance
→ scout (default: discovery+screening; strict: discovery only)
→ screener (strict or repair/review only) → claim-ledger → analyst
→ editor / Delivery Editor → auditor
→ gates check + state check + state stage-complete → finalize → finalize-complete
```

Read `configs/policy_packs/default.yaml` before delegating. Default topology:
Scout writes `candidate_claims.json` and `screened_candidates.json`, then `stage-complete --stage scout` satisfies Screener. Strict topology: Scout writes only `candidate_claims.json`; independent Screener writes `screened_candidates.json`.
If the Hermes parent splits Scout across chunks/children, child outputs are scratch/intermediate runtime material, not workflow artifacts.
The parent must join chunks deterministically before writing `candidate_claims.json`, using source identity, source path or URL, source date, topic, and evidence text rather than child completion order.
Do not append to `candidate_claims.json` from chunk workers, and do not silently drop chunk-level duplicates or near-duplicates.

If runtime WebSearch reports `Did 0 searches`, or every query returns an empty result set, stop and request human review. Do not switch to source-planner or continue with stale sources.

Before finalize, run this explicit success path:

```bash
multi-agent-brief gates check --workspace <workspace> --stage auditor
multi-agent-brief state check --workspace <workspace> --strict
multi-agent-brief state stage-complete --workspace <workspace> --stage auditor --reason "Audit and quality gates passed."
multi-agent-brief finalize --config <workspace>/config.yaml
multi-agent-brief gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md
multi-agent-brief state finalize-complete --workspace <workspace> --reason "Reader-facing artifacts passed finalize checks."
```

`finalize` is not a quality-gate executor. Blocking gate findings must route to feedback/repair, `request_human_review`, or `block_run`; do not finalize through a blocking gate.
At run start, read `output/intermediate/audience_profile_snapshot.md` for taste context and pass a concise summary to delegated roles. Do not treat `audience_profile.md` as evidence.
Read `output/intermediate/orchestrator_control_switchboard.json`, then use `multi-agent-brief controls select` to record selected controls before explicitly running their CLI/subagent/human action. Selection is not execution.
Use `multi-agent-brief feedback ingest`, `feedback plan`, `feedback resolve`, `feedback show --json`, and `feedback validate` only when audit findings or human feedback exist. These commands structure and record issues but do not execute repair.
Repair guidance is bounded runtime guidance, not an automatic trajectory regulator: if the same stage has already needed roughly three retry/repair rounds, prefer `request_human_review` or `block_run`; if a repair would touch more than two sections, narrow the scope before delegating or request human review.
Use `multi-agent-brief provenance build`, `provenance show --json`, and `provenance validate` only as optional audit/debug projection commands after runtime state exists. Provenance projection does not prove semantic support, execute repair, or gate finalize by default.

Read `references/delegate-task-sequence.md` before creating child tasks.

## Daily Source Cache

Daily cache mode collects source signals and writes cache files without drafting a final brief.

Read `references/source-cache-contract.md` before writing cache files.

## Cron Scheduling

Use cron for durable scheduling and `delegate_task` for per-run child work.

Read `references/cron-patterns.md` before creating or editing Hermes cron jobs.

## Reporting

After a delegated run, report delivery files from `output/delivery/` (`brief.md` and the configured DOCX when present). When source appendix output is configured, it is already appended inside those delivery files. Treat `output/intermediate/audited_brief.md`, `output/intermediate/claim_ledger.json`, `output/intermediate/audit_report.json`, the standalone `output/source_appendix.md` audit/control copy, and `output/intermediate/audience_profile_snapshot.md` as runtime/audit/control records, not user delivery files.
