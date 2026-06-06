from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERMES_SKILL_NAME = "multi-agent-brief-hermes"
DEFAULT_DAILY_SCHEDULE = "0 7 * * *"
DEFAULT_WEEKLY_SCHEDULE = "0 9 * * 1"
DEFAULT_MONTHLY_SCHEDULE = "30 8 1 * *"


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
                "Run a Hermes-native delegated MABW brief workflow.\n\n"
                f"{prompt_context}\n\n"
                "Use the multi-agent-brief-hermes skill.\n"
                "Run doctor, then use Hermes delegate_task children for:\n"
                "scout -> screener -> claim-ledger -> analyst -> editor -> auditor.\n"
                "After the audit artifact is ready, run finalize and report artifact paths."
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
                "Run a Hermes-native delegated MABW brief workflow.\n\n"
                f"{prompt_context}\n\n"
                "Use the multi-agent-brief-hermes skill.\n"
                "Favor month-level patterns over daily noise.\n"
                "Run doctor, then use Hermes delegate_task children for:\n"
                "scout -> screener -> claim-ledger -> analyst -> editor -> auditor.\n"
                "After the audit artifact is ready, run finalize and report artifact paths."
            ),
        ))

    notes = [
        "Hermes cron sessions are fresh sessions; every job attaches the MABW skill and sets an absolute workdir.",
        "The daily job is intentionally source-only so weekly/monthly jobs can synthesize from a stable cache.",
        "For low-cost frequent polling, convert the daily job to a wakeAgent/script gate in Hermes after the source pattern stabilizes.",
    ]
    return HermesCronPlan(
        version="v0.5.5",
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
version: 0.5.5
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

Hermes is a native MABW runtime. The Hermes parent agent manages orchestration, artifact handoff, and gate checks. Hermes `delegate_task` children run scout, screener, claim-ledger, analyst, editor, and auditor tasks as isolated subagents. Python CLI tools handle init, doctor, sources decide, inputs classify, audit, finalize, and rendering support. Cron jobs provide durable scheduling; `delegate_task` provides child task dispatch within each run.

Brief generation follows the MABW subagent workflow:

```text
scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> finalize
```

## Setup Workflow

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

I can continue generating the brief inside Hermes. The next step uses Hermes delegate_task children for:
scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> finalize.
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

The Hermes parent agent manages the full pipeline:

1. Read workspace files:
   - `config.yaml`
   - `sources.yaml`
   - `user.md`
   - `input/`
   - `input/hermes_cache/` when present

2. Run doctor:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
```

3. If source discovery is configured:

```bash
multi-agent-brief sources decide --config <workspace>/config.yaml
```

Review and merge according to workspace policy.

4. If input governance is available:

```bash
multi-agent-brief inputs classify --config <workspace>/config.yaml
```

5. Create `output/intermediate/` if it does not exist.

6. Delegate child tasks with complete context and explicit artifact paths. Use `delegate_task` for each step.

7. After each child returns, verify the expected artifact exists and is non-empty before continuing to the next child.

8. When all children have completed and `audited_brief.md` exists, finalize:

```bash
multi-agent-brief finalize --config <workspace>/config.yaml
```

9. Report artifact paths and audit status.

### Delegation Sequence

#### 1. Scout child

Use `delegate_task` to extract candidate reportable items:

```python
delegate_task(
    goal="Extract candidate reportable items for a MABW brief",
    context="""
Workspace: <workspace>
Read approved evidence inputs, cached source packages, local source files, and source config.
Write: <workspace>/output/intermediate/candidate_claims.json

Output candidate reportable items only.
Each item should preserve source path or URL, source date if available, evidence text, topic, claim type, and confidence.
Return a summary with item count and source gaps.
""",
    toolsets=["file", "terminal", "web"]
)
```

For independent source clusters, the parent may use batch delegation with up to 3 scout children, then merge their outputs into one `candidate_claims.json`.

#### 2. Screener child

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
Write: output/intermediate/claim_ledger.json

Create stable claim IDs and source-grounded claim entries.
Preserve evidence text, source URL/path, publication date, retrieved date, topic, claim type, and confidence.
Return claim count and schema issues found.
""",
    toolsets=["file", "terminal"]
)
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
- output/intermediate/audited_brief.md

Write a management-ready brief in the workspace language.
Use Claim Ledger evidence for factual statements.
Preserve valid [src:CLAIM_ID] citations.
Include source dates where useful.
Return a section summary and any source limitations.
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
Input: output/intermediate/audited_brief.md
Write: output/intermediate/audited_brief.md

Improve readability, structure, and executive tone.
Preserve factual scope, uncertainty, and valid [src:CLAIM_ID] citations.
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

Then reports:

- `output/brief.md`
- configured named Markdown copy if enabled
- `output/brief.docx` if configured
- `output/intermediate/audited_brief.md`
- `output/intermediate/claim_ledger.json`
- `output/intermediate/audit_report.json`

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
