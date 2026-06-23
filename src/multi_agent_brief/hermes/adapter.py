from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator_contract import ORCHESTRATOR_LOOP, contract_reference_bullets

HERMES_SKILL_NAME = "multi-agent-brief-hermes"
DEFAULT_DAILY_SCHEDULE = "0 7 * * *"
DEFAULT_WEEKLY_SCHEDULE = "0 9 * * 1"
DEFAULT_MONTHLY_SCHEDULE = "30 8 1 * *"
REPAIR_GUIDANCE_NOTE = (
    "Repair guidance is bounded runtime guidance, not an automatic trajectory regulator: "
    "if the same stage has already needed roughly three retry/repair rounds, prefer "
    "request_human_review or block_run; if a repair would touch more than two sections, "
    "narrow the scope before delegating or request human review. Audit warnings, "
    "overstatement findings, support-calibration findings, and quality-gate findings "
    "do not authorize direct edits to frozen artifacts. For owner-stage artifact "
    "repair, run `multi-agent-brief repair route --workspace <workspace>` and "
    "`multi-agent-brief repair start --workspace <workspace>` before delegation; after "
    "the owner edits only allowed_artifacts, run `multi-agent-brief repair complete "
    "--workspace <workspace> --reason \"<reason>\"` and rerun downstream stages from "
    "must_rerun_from."
)


@dataclass
class HermesCronJob:
    name: str
    schedule: str
    prompt: str
    skills: list[str] = field(default_factory=lambda: [HERMES_SKILL_NAME])
    workdir: str = ""
    profile: str = ""
    deliver: str = "local"
    context_from: list[str] = field(default_factory=list)
    enabled_toolsets: list[str] = field(default_factory=lambda: ["web", "file", "terminal"])
    purpose: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HermesCronPlan:
    version: str
    workspace: str
    project_name: str
    cadences: list[str]
    cache_dir: str
    jobs: list[HermesCronJob]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["jobs"] = [job.to_dict() for job in self.jobs]
        return data


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, tuple):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


def _project_summary(config: dict[str, Any]) -> dict[str, str]:
    project = config.get("project", {}) or {}
    report = config.get("report", {}) or {}
    language = config.get("language", {}) or {}
    return {
        "name": str(project.get("name") or project.get("title") or "MABW Brief"),
        "company": str(project.get("company") or ""),
        "industry": str(project.get("industry") or ""),
        "audience": str(project.get("audience") or "management"),
        "language": str(language.get("output") or project.get("language") or "zh-CN"),
        "cadence": str(report.get("cadence") or project.get("cadence") or ""),
    }


def _resolve_cadences(config: dict[str, Any], requested: list[str] | None) -> list[str]:
    explicit = [c.lower().replace("-", "_") for c in (requested or [])]
    if explicit:
        return [c for c in explicit if c in {"daily", "weekly", "monthly"}]

    summary = _project_summary(config)
    raw = summary.get("cadence") or "weekly"
    cadences = [c.lower().replace("-", "_") for c in _as_list(raw)]
    if not cadences:
        cadences = ["weekly"]
    return [c for c in cadences if c in {"daily", "weekly", "monthly"}] or ["weekly"]


def _prompt_context(summary: dict[str, str], workspace: Path, cache_dir: Path) -> str:
    bits = [
        f"Workspace: {workspace}",
        f"Project: {summary['name']}",
        f"Audience: {summary['audience']}",
        f"Language: {summary['language']}",
    ]
    if summary["company"]:
        bits.append(f"Company: {summary['company']}")
    if summary["industry"]:
        bits.append(f"Industry/theme: {summary['industry']}")
    bits.append(f"Daily cache directory: {cache_dir}")
    return "\n".join(bits)


def build_hermes_cron_plan(
    *,
    config: dict[str, Any],
    workspace: str | Path,
    repo_workdir: str | Path,
    cadences: list[str] | None = None,
    deliver: str = "local",
    profile: str = "",
    daily_schedule: str = DEFAULT_DAILY_SCHEDULE,
    weekly_schedule: str = DEFAULT_WEEKLY_SCHEDULE,
    monthly_schedule: str = DEFAULT_MONTHLY_SCHEDULE,
) -> HermesCronPlan:
    workspace_path = Path(workspace).resolve()
    repo_path = Path(repo_workdir).resolve()
    summary = _project_summary(config)
    resolved_cadences = _resolve_cadences(config, cadences)
    cache_dir = workspace_path / "input" / "hermes_cache"
    prompt_context = _prompt_context(summary, workspace_path, cache_dir)

    jobs: list[HermesCronJob] = []
    daily_job = HermesCronJob(
        name=f"MABW daily cache - {summary['name']}",
        schedule=daily_schedule,
        workdir=str(repo_path),
        profile=profile,
        deliver=deliver,
        purpose="Collect daily source signals into the workspace cache for later weekly/monthly synthesis.",
        prompt=(
            "Run a Hermes daily source cache collection for this MABW workspace.\n\n"
            f"{prompt_context}\n\n"
            "Use the multi-agent-brief-hermes skill.\n"
            "Collect source signals and write YYYY-MM-DD.json.\n"
            "Report saved item count and source gaps."
        ),
    )
    jobs.append(daily_job)

    if "weekly" in resolved_cadences:
        jobs.append(HermesCronJob(
            name=f"MABW weekly brief - {summary['name']}",
            schedule=weekly_schedule,
            workdir=str(repo_path),
            profile=profile,
            deliver=deliver,
            context_from=[daily_job.name],
            purpose="Run the audited weekly brief workflow using Hermes delegate_task children.",
            prompt=(
                "Run a Hermes-native delegated MABW brief workflow as the Orchestrator main agent.\n\n"
                f"{prompt_context}\n\n"
                "Use the multi-agent-brief-hermes skill.\n"
                "Read contract references before delegation:\n"
                f"{contract_reference_bullets()}\n\n"
                "Read runtime state files before selecting the next stage:\n"
                "- output/intermediate/runtime_manifest.json\n"
                "- output/intermediate/workflow_state.json\n"
                "- output/intermediate/artifact_registry.json\n"
                "- output/intermediate/event_log.jsonl\n\n"
                "Read audience memory snapshot for this run:\n"
                "- output/intermediate/audience_profile_snapshot.md\n"
                "Summarize relevant taste guidance for delegated roles. Do not treat audience_profile.md as source evidence, and do not use mid-run profile edits until the next run.\n\n"
                "Read the Orchestrator control switchboard:\n"
                "- output/intermediate/orchestrator_control_switchboard.json\n"
                "Record control selections with multi-agent-brief controls select. Selection is not execution.\n\n"
                f"{REPAIR_GUIDANCE_NOTE}\n\n"
                "Optional feedback state files are created only by feedback commands:\n"
                "- output/intermediate/feedback_issues.json\n"
                "- output/intermediate/repair_plan.json\n"
                "- output/intermediate/delta_audit_report.json\n\n"
                "Optional quality gate state files are created only by gates commands:\n"
                "- output/intermediate/gates/auditor_quality_gate_report.json\n"
                "- output/intermediate/gates/finalize_quality_gate_report.json\n"
                "- output/intermediate/quality_gate_report.json (legacy/latest projection)\n\n"
                "Optional provenance projection files are created only by provenance commands:\n"
                "- output/intermediate/provenance_graph.json\n\n"
                f"Orchestrator loop: {ORCHESTRATOR_LOOP}\n"
                "Run doctor, then use Hermes delegate_task children for:\n"
                "default topology: scout(discovery+screening) -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor.\n"
                "strict topology: scout -> screener -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor.\n"
                "After audit_report.json exists, run:\n"
                f"multi-agent-brief controls select --workspace {workspace_path} --control quality_gates --selection enable --reason \"Use quality gates before finalize.\"\n"
                f"multi-agent-brief gates check --workspace {workspace_path} --stage auditor\n"
                f"multi-agent-brief state check --workspace {workspace_path} --strict\n"
                f"multi-agent-brief state stage-complete --workspace {workspace_path} --stage auditor --reason \"Audit and quality gates passed.\"\n"
                f"Then run multi-agent-brief finalize --config {workspace_path}/config.yaml.\n"
                f"After finalize writes reader-facing artifacts, run multi-agent-brief gates check --workspace {workspace_path} --stage finalize --brief {workspace_path}/output/brief.md, then run multi-agent-brief state finalize-complete --workspace {workspace_path} --reason \"Reader-facing artifacts passed finalize checks.\"\n"
                "finalize is not a quality-gate executor.\n"
                "Optionally run multi-agent-brief provenance build/show/validate after runtime state exists for an audit/debug projection; it is not semantic proof."
            ),
        ))

    if "monthly" in resolved_cadences:
        jobs.append(HermesCronJob(
            name=f"MABW monthly brief - {summary['name']}",
            schedule=monthly_schedule,
            workdir=str(repo_path),
            profile=profile,
            deliver=deliver,
            context_from=[daily_job.name],
            purpose="Run the audited monthly brief workflow using Hermes delegate_task children.",
            prompt=(
                "Run a Hermes-native delegated MABW brief workflow as the Orchestrator main agent.\n\n"
                f"{prompt_context}\n\n"
                "Use the multi-agent-brief-hermes skill.\n"
                "Read contract references before delegation:\n"
                f"{contract_reference_bullets()}\n\n"
                "Read runtime state files before selecting the next stage:\n"
                "- output/intermediate/runtime_manifest.json\n"
                "- output/intermediate/workflow_state.json\n"
                "- output/intermediate/artifact_registry.json\n"
                "- output/intermediate/event_log.jsonl\n\n"
                "Read audience memory snapshot for this run:\n"
                "- output/intermediate/audience_profile_snapshot.md\n"
                "Summarize relevant taste guidance for delegated roles. Do not treat audience_profile.md as source evidence, and do not use mid-run profile edits until the next run.\n\n"
                "Read the Orchestrator control switchboard:\n"
                "- output/intermediate/orchestrator_control_switchboard.json\n"
                "Record control selections with multi-agent-brief controls select. Selection is not execution.\n\n"
                f"{REPAIR_GUIDANCE_NOTE}\n\n"
                "Optional feedback state files are created only by feedback commands:\n"
                "- output/intermediate/feedback_issues.json\n"
                "- output/intermediate/repair_plan.json\n"
                "- output/intermediate/delta_audit_report.json\n\n"
                "Optional quality gate state files are created only by gates commands:\n"
                "- output/intermediate/gates/auditor_quality_gate_report.json\n"
                "- output/intermediate/gates/finalize_quality_gate_report.json\n"
                "- output/intermediate/quality_gate_report.json (legacy/latest projection)\n\n"
                "Optional provenance projection files are created only by provenance commands:\n"
                "- output/intermediate/provenance_graph.json\n\n"
                f"Orchestrator loop: {ORCHESTRATOR_LOOP}\n"
                "Favor month-level patterns over daily noise.\n"
                "Run doctor, then use Hermes delegate_task children for:\n"
                "default topology: scout(discovery+screening) -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor.\n"
                "strict topology: scout -> screener -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor.\n"
                "After audit_report.json exists, run:\n"
                f"multi-agent-brief controls select --workspace {workspace_path} --control quality_gates --selection enable --reason \"Use quality gates before finalize.\"\n"
                f"multi-agent-brief gates check --workspace {workspace_path} --stage auditor\n"
                f"multi-agent-brief state check --workspace {workspace_path} --strict\n"
                f"multi-agent-brief state stage-complete --workspace {workspace_path} --stage auditor --reason \"Audit and quality gates passed.\"\n"
                f"Then run multi-agent-brief finalize --config {workspace_path}/config.yaml.\n"
                f"After finalize writes reader-facing artifacts, run multi-agent-brief gates check --workspace {workspace_path} --stage finalize --brief {workspace_path}/output/brief.md, then run multi-agent-brief state finalize-complete --workspace {workspace_path} --reason \"Reader-facing artifacts passed finalize checks.\"\n"
                "finalize is not a quality-gate executor.\n"
                "Optionally run multi-agent-brief provenance build/show/validate after runtime state exists for an audit/debug projection; it is not semantic proof."
            ),
        ))

    notes = [
        "Hermes cron sessions are fresh sessions; every job attaches the MABW skill and sets an absolute workdir.",
        "The daily job is intentionally source-only so weekly/monthly jobs can synthesize from a stable cache.",
        "For low-cost frequent polling, convert the daily job to a wakeAgent/script gate in Hermes after the source pattern stabilizes.",
    ]
    return HermesCronPlan(
        version="v0.10.1",
        workspace=str(workspace_path),
        project_name=summary["name"],
        cadences=resolved_cadences,
        cache_dir=str(cache_dir),
        jobs=jobs,
        notes=notes,
    )


def render_hermes_cron_commands(plan: HermesCronPlan) -> str:
    lines: list[str] = []
    for job in plan.jobs:
        parts = [
            "hermes",
            "cron",
            "create",
            job.schedule,
            job.prompt,
        ]
        for skill in job.skills:
            parts.extend(["--skill", skill])
        parts.extend(["--workdir", job.workdir])
        parts.extend(["--name", job.name])
        if job.profile:
            parts.extend(["--profile", job.profile])
        if job.deliver and job.deliver != "local":
            parts.extend(["--deliver", job.deliver])
        lines.append(" ".join(shlex.quote(part) for part in parts))
    return "\n\n".join(lines) + "\n"


def render_hermes_cron_markdown(plan: HermesCronPlan) -> str:
    lines = [
        "# Hermes Cron Plan",
        "",
        f"- Version: {plan.version}",
        f"- Workspace: `{plan.workspace}`",
        f"- Project: {plan.project_name}",
        f"- Cadences: {', '.join(plan.cadences)}",
        f"- Cache directory: `{plan.cache_dir}`",
        "",
        "## Jobs",
        "",
    ]
    for job in plan.jobs:
        lines.extend([
            f"### {job.name}",
            "",
            f"- Schedule: `{job.schedule}`",
            f"- Purpose: {job.purpose}",
            f"- Workdir: `{job.workdir}`",
            f"- Deliver: `{job.deliver}`",
            f"- Skills: {', '.join(job.skills)}",
            f"- Context from: {', '.join(job.context_from) if job.context_from else 'none'}",
            "",
            "Prompt:",
            "",
            "```text",
            job.prompt,
            "```",
            "",
        ])
    lines.extend(["## Notes", ""])
    for note in plan.notes:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


# The SKILL_MD template uses triple-single-quote delimiter so that
# the inner delegate_task code examples with triple-double-quotes
# render as clean Python without backslash escapes.
_SKILL_MD_TEMPLATE = '''---
name: multi-agent-brief-hermes
description: Use this skill to run Multi-Agent Brief Workflow workspaces inside Hermes using Hermes delegate_task subagents, source cache, cron scheduling, and final rendering tools.
version: 0.10.1
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

# Multi-Agent Brief Workflow for Hermes

Use this skill to run Multi-Agent Brief Workflow workspaces inside Hermes using Hermes delegate_task subagents, source cache, cron scheduling, and final rendering tools.

## Operating Model

Hermes is a native MABW runtime. The Hermes parent agent is the Orchestrator main agent: it reads shared contract references and runtime state files, manages artifact handoff, checks expected artifacts, and selects the next workflow decision. Hermes `delegate_task` children run scout, screener, claim-ledger, analyst, editor, and auditor tasks as isolated subagents. Python CLI tools handle init, doctor, sources decide, input extraction/classification, state checks, feedback ingest/plan/resolve/show/validate, gates check/show/validate, provenance build/show/validate, audit, finalize, and rendering support. Cron jobs provide durable scheduling; `delegate_task` provides child task dispatch within each run.

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

Read the snapshot at run start, summarize relevant taste guidance for delegated roles, and do not treat `audience_profile.md` as source evidence or a correctness contract. Mid-run profile edits apply to the next run.

Control switchboard files:

- `output/intermediate/orchestrator_control_switchboard.json`
- `output/intermediate/control_selections.json`

Read the switchboard after handoff, record enable/defer/reject choices with `multi-agent-brief controls select`, and then explicitly run the selected CLI/subagent/human action. Selection is not execution.

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

Brief generation follows the MABW subagent workflow:

```text
default: scout(discovery+screening) -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor -> finalize
strict: scout -> screener -> claim-ledger -> analyst -> editor/Delivery Editor -> auditor -> finalize
```

## Setup Workflow

### Preferred Path: Hermes Plugin

Use the MABW Hermes plugin when it is installed:

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

If the plugin is unavailable, use fallback onboarding: Collect brief profile in chat. Write `onboarding.json`, validate it with `multi-agent-brief onboard --validate onboarding.json`, initialize with `multi-agent-brief init <workspace> --from-onboarding onboarding.json`, then create the handoff with `multi-agent-brief run --workspace <workspace>`.

1. Clone or open the repository.
2. Create and activate the Python virtual environment.
3. Install MABW.
4. Initialize the requested workspace.
5. Run doctor:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
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

4. Read the Orchestrator control switchboard and record control selections with `multi-agent-brief controls select`. Selection is not execution.

5. Run doctor:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
```

6. If source discovery is configured:

```bash
multi-agent-brief sources decide --config <workspace>/config.yaml
```

Review and merge according to workspace policy.
If runtime WebSearch reports `Did 0 searches`, or every query returns an empty result set, stop and request human review. Do not switch to source-planner or continue with stale sources.

6. Extract non-text input files when present:

```bash
multi-agent-brief inputs extract --config <workspace>/config.yaml
```

This converts PDF/DOCX/image inputs to adjacent `.mineru.md` files before classification. Directory role still controls claim eligibility: extracted files under `input/sources/` are evidence; extracted files under `input/context/`, `input/instructions/`, and `input/feedback/` are not evidence.

7. Classify input files:

```bash
multi-agent-brief inputs classify --config <workspace>/config.yaml
```

8. Create `output/intermediate/` if it does not exist.

9. Delegate child tasks with complete context and explicit artifact paths. Use `delegate_task` for each step.

10. After each child returns, verify the expected artifact exists and is non-empty before selecting the next decision.

11. If audit findings or human feedback exist, use `multi-agent-brief feedback ingest`, `feedback plan`, `feedback resolve`, `feedback show --json`, and `feedback validate`; these commands structure and record issues but do not execute repair.

12. Repair guidance is bounded runtime guidance, not an automatic trajectory regulator. If the same stage has already needed roughly three retry/repair rounds, prefer `request_human_review` or `block_run`; if a repair would touch more than two sections, narrow the scope before delegating or request human review.

13. After `audit_report.json` exists, run deterministic quality gates and refresh runtime state:

```bash
multi-agent-brief gates check --workspace <workspace> --stage auditor
multi-agent-brief state check --workspace <workspace> --strict
```

14. If state is not blocked, record the auditor decision:

```bash
multi-agent-brief state stage-complete --workspace <workspace> --stage auditor --reason "Audit and quality gates passed."
```

If state is blocked by owner-stage artifact repair, run `multi-agent-brief repair route --workspace <workspace>` and `multi-agent-brief repair start --workspace <workspace>`; otherwise choose `request_human_review` or `block_run`. Audit warnings, overstatement findings, support-calibration findings, and quality-gate findings do not authorize direct edits to frozen artifacts. Do not finalize.

15. Run finalize only after the gates/state completion path passes. `finalize` is not a quality-gate executor:

```bash
multi-agent-brief finalize --config <workspace>/config.yaml
```

16. After finalize writes reader-facing artifacts, verify completion:

```bash
multi-agent-brief gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md
multi-agent-brief state finalize-complete --workspace <workspace> --reason "Reader-facing artifacts passed finalize checks."
```

17. Optional audit/debug provenance projection after runtime state exists:

```bash
multi-agent-brief provenance build --workspace <workspace>
multi-agent-brief provenance show --workspace <workspace> --json
multi-agent-brief provenance validate --workspace <workspace>
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
    goal="Extract candidate reportable items for a MABW brief",
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

```python
delegate_task(
    goal="Screen and rank MABW candidate claims",
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
    goal="Build the MABW Claim Ledger",
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
multi-agent-brief state freeze-claim-ledger --workspace <workspace>
multi-agent-brief state stage-complete --workspace <workspace> --stage claim-ledger --reason "Claim Ledger frozen from claim drafts."
```

#### 4. Analyst child

```python
delegate_task(
    goal="Draft the audited MABW brief",
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
    goal="Polish the audited MABW brief",
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
    goal="Audit the MABW brief against the Claim Ledger",
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
multi-agent-brief finalize --config <workspace>/config.yaml
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

The MABW `cached_package` provider can read JSON, Markdown, and text files from the configured cache directory. Prefer JSON arrays or objects with an `items` array. Each item should preserve URL, publication date, source name, and reliability where available.

## Hermes Cron Notes

- Attach this skill to each cron job with `--skill multi-agent-brief-hermes`.
- Use `--workdir <repo-root>` so Hermes loads repository instructions and runs commands from the project.
- Pin `--profile <name>` when the Hermes profile already exists.
- Hermes delivers the final response through the configured cron destination.
'''


def render_hermes_skill() -> str:
    return _SKILL_MD_TEMPLATE


def render_hermes_setup_success(
    *,
    repo: str | Path,
    venv: str | Path,
    workspace: str | Path,
    version: str = "v0.10.1",
    doctor_status: str = "passed",
) -> str:
    return f"""Project is cloned and ready.

Repository: {repo}
Virtual environment: {venv}
Workspace: {workspace}
Version: {version}
Doctor: {doctor_status}

I can continue generating the brief inside Hermes with the Orchestrator main agent. Recommended next steps:

  multi-agent-brief hermes install-skill
  multi-agent-brief hermes prompt --config {workspace}/config.yaml

Then use the generated prompt in Hermes to run the delegated brief workflow.
"""


def render_hermes_prompt(
    *,
    workspace: str | Path,
    repo_workdir: str | Path,
    venv_path: str | Path,
) -> str:
    workspace = str(Path(workspace).resolve())
    repo = str(Path(repo_workdir).resolve())
    venv = str(Path(venv_path).resolve())
    contract_refs = contract_reference_bullets()
    return f"""Use the multi-agent-brief-hermes skill to run a Hermes-native delegated brief workflow for this workspace.

Repository: {repo}
Workspace: {workspace}
Venv activate: source {venv}

You are the Hermes Orchestrator main agent. Read shared contract references, identify the next stage, delegate specialist child tasks or Python tools, check expected artifacts, and decide continue / retry_stage / delegate_repair / request_human_review / block_run / finalize.

Contract references:
{contract_refs}

Runtime state files:
- output/intermediate/runtime_manifest.json
- output/intermediate/workflow_state.json
- output/intermediate/artifact_registry.json
- output/intermediate/event_log.jsonl

Audience memory snapshot:
- output/intermediate/audience_profile_snapshot.md

Read the snapshot at run start, summarize relevant taste guidance for delegated roles, and use that summary as runtime context. Do not treat audience_profile.md as source evidence, and do not use mid-run profile edits until the next run.

Control switchboard files:
- output/intermediate/orchestrator_control_switchboard.json
- output/intermediate/control_selections.json

Read the switchboard after handoff and record enable/defer/reject choices with multi-agent-brief controls select. Selection is not execution; explicitly run selected controls afterward.

Optional feedback state files:
- output/intermediate/feedback_issues.json
- output/intermediate/repair_plan.json
- output/intermediate/delta_audit_report.json

Optional quality gate state files:
- output/intermediate/gates/auditor_quality_gate_report.json
- output/intermediate/gates/finalize_quality_gate_report.json
- output/intermediate/quality_gate_report.json (legacy/latest projection)

Optional provenance projection files:
- output/intermediate/provenance_graph.json

Orchestrator loop: {ORCHESTRATOR_LOOP}

## Preferred: Hermes Plugin

If the MABW Hermes plugin is installed and enabled, use the plugin path:

```text
/mabw {workspace}
→ mabw_create_onboarding (if workspace is new)
→ mabw_init_workspace
→ mabw_run_handoff
→ read agent_handoff.md
→ continue delegated workflow
```

Install from the MABW repo:

```bash
cp -R integrations/hermes-plugin/mabw ~/.hermes/plugins/mabw
hermes plugins enable mabw
```

## Fallback: chat-to-JSON onboarding

If the plugin is not available and this workspace does not yet have config.yaml:

1. Collect brief profile in chat — ask for company, industry, task objective, audience, language, cadence, source style, output style, must-watch topics, excluded sources, and source/search mode. Accept natural-language answers and confirm defaults.
2. Write onboarding.json from the collected answers.
3. Validate with: multi-agent-brief onboard --validate onboarding.json
4. Create the workspace: multi-agent-brief init <workspace> --from-onboarding onboarding.json
5. Create runtime handoff: multi-agent-brief run --workspace <workspace>
6. Read agent_handoff.md and continue with the delegated workflow below.

## Existing workspace: delegated brief run

As the Hermes Orchestrator main agent, execute:

1. Read contract references:
   - configs/orchestrator_contract.yaml
   - configs/stage_specs.yaml
   - configs/artifact_contracts.yaml
   - configs/policy_packs/default.yaml

2. Read runtime state files:
   - output/intermediate/runtime_manifest.json
   - output/intermediate/workflow_state.json
   - output/intermediate/artifact_registry.json
   - output/intermediate/event_log.jsonl

3. Read audience memory snapshot:
   - output/intermediate/audience_profile_snapshot.md
   Summarize relevant taste guidance for delegated roles. Do not treat the profile as source evidence or as a correctness contract.

4. Read the Orchestrator control switchboard:
   - output/intermediate/orchestrator_control_switchboard.json
   Record control choices with multi-agent-brief controls select. Selection is not execution.

5. Run doctor:
   multi-agent-brief doctor --config {workspace}/config.yaml

6. If source discovery is configured:
   multi-agent-brief sources decide --config {workspace}/config.yaml
   If runtime WebSearch reports `Did 0 searches`, or every query returns an empty result set, stop and request human review. Do not switch to source-planner or continue with stale sources.

7. If non-text input files are present:
   multi-agent-brief inputs extract --config {workspace}/config.yaml

8. If input governance is available:
   multi-agent-brief inputs classify --config {workspace}/config.yaml

9. Refresh runtime state without running stages:
   multi-agent-brief state check --workspace {workspace}

10. If audit findings or human feedback exist, structure them without running repair:
   multi-agent-brief feedback ingest --workspace {workspace} --feedback <path> --source human|audit
   multi-agent-brief feedback plan --workspace {workspace}
   multi-agent-brief feedback resolve --workspace {workspace} --issue-id <id> --repair-plan-id <id> --reason <reason>
   multi-agent-brief feedback show --workspace {workspace} --json
   multi-agent-brief feedback validate --workspace {workspace}

11. Repair guidance is bounded runtime guidance, not an automatic trajectory regulator. If the same stage has already needed roughly three retry/repair rounds, prefer request_human_review or block_run; if a repair would touch more than two sections, narrow the scope before delegating or request human review.

12. Delegate scout child via delegate_task:
   Goal: "Extract candidate reportable items for a MABW brief; in default topology, screen them in the same Scout stage"
   Write: output/intermediate/candidate_claims.json
   Default topology also writes: output/intermediate/screened_candidates.json
   Source identity: source_url is only for HTTP(S) URLs; use source_path for local/package sources. Preserve source_title/source_name, publisher, source_category, source_type, source dates, and evidence text.
   toolsets: ["file", "terminal", "web"]

13. If role_topology is `strict`, after candidate_claims.json exists and is non-empty, delegate screener child. If role_topology is `default`, Scout must already have written screened_candidates.json and the screener stage is satisfied by topology:
   Goal: "Screen and rank MABW candidate claims"
   Input: output/intermediate/candidate_claims.json
   Write: output/intermediate/screened_candidates.json
   toolsets: ["file", "terminal"]

14. After screened_candidates.json exists, delegate claim-ledger child:
   Goal: "Build the MABW Claim Ledger"
   Input: output/intermediate/screened_candidates.json
   Write: output/intermediate/claim_drafts.json
   Preserve source_url/source_path, source_title/source_name, publisher, source_category, source_type, published_at/retrieved_at, and evidence text. Never put titles, source names, source IDs, search queries, or local paths in source_url.
   toolsets: ["file", "terminal"]

15. After claim_drafts.json exists, freeze the Claim Ledger, confirm claim_ledger.json exists, and record claim-ledger completion before delegating Analyst:
   multi-agent-brief state freeze-claim-ledger --workspace {workspace}
   multi-agent-brief state stage-complete --workspace {workspace} --stage claim-ledger --reason "Claim Ledger frozen from claim drafts."

16. Then delegate analyst child:
   Goal: "Draft the audited MABW brief"
   Inputs: user.md and output/intermediate/claim_ledger.json
   Write: output/intermediate/audited_brief.md as the Analyst working draft
   Optional atomic graph boundary: if output/intermediate/atomic_claim_graph.json is present and valid, use it only as an optional experimental structural decomposition aid for frozen Claim Ledger claims; it is not source evidence or proof of support. Do not cite atom IDs, create/edit/repair/extend the graph, or introduce material atoms absent from the frozen Claim Ledger and valid graph.
   toolsets: ["file", "terminal"]

17. After analyst stage-complete freezes analyst_draft_snapshot.md, delegate editor / Delivery Editor child:
   Goal: "Polish the audited MABW brief without adding facts"
   Inputs: output/intermediate/analyst_draft_snapshot.md and output/intermediate/audited_brief.md
   Write: output/intermediate/audited_brief.md as the Editor-owned final auditable brief
   Optional atomic graph boundary: if output/intermediate/atomic_claim_graph.json is present and valid, use it only as an optional experimental structural decomposition aid; if it is absent or invalid, do not repair it. Do not create/edit/repair/extend the graph, cite atom IDs, or introduce material atoms absent from the frozen Claim Ledger and valid graph.
   toolsets: ["file", "terminal"]

18. After editor completes, delegate auditor child:
    Goal: "Audit the MABW brief against the Claim Ledger"
    Inputs: output/intermediate/audited_brief.md and output/intermediate/claim_ledger.json
    Write: output/intermediate/audit_report.json
    toolsets: ["file", "terminal"]

19. After audit_report.json exists, select and run deterministic quality gates, then refresh runtime state:
    multi-agent-brief controls select --workspace {workspace} --control quality_gates --selection enable --reason "Use quality gates before finalize."
    multi-agent-brief gates check --workspace {workspace} --stage auditor
    multi-agent-brief state check --workspace {workspace} --strict

20. If state is not blocked, record the auditor completion:
    multi-agent-brief state stage-complete --workspace {workspace} --stage auditor --reason "Audit and quality gates passed."

21. If state is blocked by owner-stage artifact repair, run `multi-agent-brief repair route --workspace {workspace}` and `multi-agent-brief repair start --workspace {workspace}`. Delegate only the repair_owner role and allow edits only to allowed_artifacts, then run `multi-agent-brief repair complete --workspace {workspace} --reason "<reason>"` and rerun downstream stages from must_rerun_from. Otherwise choose request_human_review or block_run. Audit warnings, overstatement findings, support-calibration findings, and quality-gate findings do not authorize direct edits to frozen artifacts. Do not finalize.

22. Run finalize only after the gates/state completion path passes. finalize is not a quality-gate executor:
    multi-agent-brief finalize --config {workspace}/config.yaml

23. After finalize writes delivery artifacts under output/delivery/, verify completion:
    multi-agent-brief gates check --workspace {workspace} --stage finalize --brief {workspace}/output/brief.md
    multi-agent-brief state finalize-complete --workspace {workspace} --reason "Reader-facing artifacts passed finalize checks."

24. Optional audit/debug projection after runtime state exists:
    multi-agent-brief provenance build --workspace {workspace}
    multi-agent-brief provenance show --workspace {workspace} --json
    multi-agent-brief provenance validate --workspace {workspace}
    Provenance projection is not semantic proof and is not required to finalize.

25. Report artifact paths, audit status, quality gate status, switchboard selections, and optional provenance_graph.json when created.

For each delegate_task call, write complete goal and context with the workspace path, input paths, and output paths fully specified. After each child returns, verify the expected artifact exists and is non-empty before selecting continue, retry_stage, delegate_repair, request_human_review, block_run, or finalize.

Expected artifacts:
- {workspace}/output/intermediate/candidate_claims.json
- {workspace}/output/intermediate/screened_candidates.json
- {workspace}/output/intermediate/claim_ledger.json
- {workspace}/output/intermediate/audited_brief.md
- {workspace}/output/intermediate/audit_report.json
- {workspace}/output/delivery/brief.md
"""


def _find_hermes_skill_dirs() -> list[Path]:
    home = Path.home()
    candidates = [
        home / ".hermes" / "skills",
        home / ".config" / "hermes" / "skills",
        home / "hermes" / "skills",
    ]
    return [d for d in candidates if d.exists()]


def install_hermes_skill(target_dir: str | Path | None = None) -> dict[str, Any]:
    skill_content = render_hermes_skill()
    target = Path(target_dir) if target_dir else None

    if target is None:
        dirs = _find_hermes_skill_dirs()
        if dirs:
            target = dirs[0] / "multi-agent-brief-hermes"
        else:
            target = Path(".agents/hermes-skills/multi-agent-brief-hermes")

    target.mkdir(parents=True, exist_ok=True)
    skill_path = target / "SKILL.md"
    skill_path.write_text(skill_content, encoding="utf-8")

    return {
        "installed": True,
        "skill_path": str(skill_path.resolve()),
        "skill_dir": str(target.resolve()),
        "auto_detected": target_dir is None and bool(_find_hermes_skill_dirs()),
        "hint": (
            "Copy this skill into ~/.hermes/skills/ or configure Hermes skills.external_dirs"
            if target_dir is None and not _find_hermes_skill_dirs()
            else ""
        ),
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_cached_package_source(
    *,
    sources_path: str | Path,
    cache_dir: str = "input/hermes_cache",
    dry_run: bool = False,
) -> dict[str, Any]:
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to update sources.yaml") from exc

    path = Path(sources_path)
    if not path.exists():
        raise FileNotFoundError(f"sources.yaml not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"sources.yaml must be a mapping: {path}")

    strategy = data.setdefault("source_strategy", {})
    enabled = strategy.setdefault("enabled_providers", [])
    if isinstance(enabled, str):
        enabled = [enabled]
    if not isinstance(enabled, list):
        raise ValueError("source_strategy.enabled_providers must be a list")

    changed = False
    if "cached_package" not in enabled:
        enabled.append("cached_package")
        strategy["enabled_providers"] = enabled
        changed = True

    cached = data.setdefault("cached_package", {})
    if cached.get("enabled") is not True:
        cached["enabled"] = True
        changed = True

    paths = cached.setdefault("paths", [])
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list):
        raise ValueError("cached_package.paths must be a list")
    if cache_dir not in paths:
        paths.append(cache_dir)
        changed = True
    cached["paths"] = paths

    formats = cached.setdefault("formats", ["json", "md", "txt"])
    if isinstance(formats, str):
        formats = [formats]
    for fmt in ["json", "md", "txt"]:
        if fmt not in formats:
            formats.append(fmt)
            changed = True
    cached["formats"] = formats

    if changed and not dry_run:
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    return {
        "changed": changed,
        "sources_path": str(path),
        "enabled_providers": enabled,
        "cache_dir": cache_dir,
        "formats": formats,
        "dry_run": dry_run,
    }
