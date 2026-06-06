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
        name=f"MABW daily scout - {summary['name']}",
        schedule=daily_schedule,
        workdir=str(repo_path),
        profile=profile,
        deliver=deliver,
        purpose="Collect daily source signals into the workspace cache for later weekly/monthly synthesis.",
        prompt=(
            "Use the multi-agent-brief-hermes skill.\n"
            f"{prompt_context}\n\n"
            "Task: collect today's public, citable signals relevant to this brief. "
            "Write one JSON file under the daily cache directory named YYYY-MM-DD.json. "
            "The JSON must be either an array of source items or an object with an items array. "
            "Each item should include title, content or snippet, url, published_at when available, "
            "source_name, reliability, and metadata. Do not write a final brief in this job. "
            "End with a short summary of how many usable items were saved."
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
            purpose="Run the audited weekly brief workflow from the accumulated daily cache.",
            prompt=(
                "Use the multi-agent-brief-hermes skill.\n"
                f"{prompt_context}\n\n"
                "Task: prepare this week's brief using cached daily source packages plus any configured workspace sources. "
                "First ensure sources.yaml enables cached_package for input/hermes_cache. "
                "Run doctor. Then execute the subagent-first workflow: invoke scout, screener, claim-ledger, "
                "analyst, editor, and auditor subagents. After audited_brief.md exists, run finalize. "
                "If any audit gate fails, report the blocking findings and do not mark the brief delivery-ready."
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
            purpose="Run the audited monthly brief workflow from the accumulated daily cache.",
            prompt=(
                "Use the multi-agent-brief-hermes skill.\n"
                f"{prompt_context}\n\n"
                "Task: prepare this month's brief using cached daily source packages plus any configured workspace sources. "
                "Favor month-level patterns over daily noise. Run doctor. Then execute the subagent-first workflow: "
                "invoke scout, screener, claim-ledger, analyst, editor, and auditor subagents. "
                "After audited_brief.md exists, run finalize. "
                "If any audit gate fails, report the blocking findings and do not mark the brief delivery-ready."
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


def render_hermes_skill() -> str:
    return """---
name: multi-agent-brief-hermes
description: Run Multi-Agent Brief Workflow workspaces from Hermes cron, collecting daily source packages and triggering audited weekly/monthly briefs.
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
---

# Multi-Agent Brief Workflow for Hermes

Use this skill when a Hermes cron job needs to collect daily signals for a MABW workspace or trigger an audited weekly/monthly brief.

## Hard Rules

- Treat Hermes cron as a fresh session. Rely only on this skill, the cron prompt, `AGENTS.md`, and files in the configured workdir/workspace.
- Do not invent internal company facts. Use only public or user-provided source evidence.
- Do not paste API keys, tokens, raw private logs, or credentials into files or messages.
- Daily scout jobs collect source packages only. They must not write final management briefs.
- Weekly/monthly jobs must run `doctor`, then execute the subagent-first workflow.
- If audit gates fail, report the blocking findings and do not mark the output delivery-ready.
- Reader-facing artifacts must not contain `[src:CLAIM_ID]`; internal audited markdown may retain citations.

## Daily Scout Workflow

1. Read the cron prompt for the absolute workspace path and cache directory.
2. Collect public, citable source signals relevant to the configured company, industry/theme, audience, and report language.
3. Write one JSON file under the cache directory named `YYYY-MM-DD.json`.
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

5. End with a short count of saved usable items and any source gaps.

## Weekly / Monthly Brief Workflow

1. Confirm the workspace has `config.yaml`, `sources.yaml`, and `user.md`.
2. Ensure `sources.yaml` enables any required source providers, including `cached_package` for `input/hermes_cache` when daily Hermes scout cache is used.
3. Run doctor:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
```

4. Generate the brief through the subagent workflow:
   - If Claude Code is available, the recommended path is `/generate-brief <workspace>`.
   - Otherwise invoke subagents in order: scout, screener, claim-ledger, analyst, editor, auditor.
5. After `audited_brief.md` exists, run finalize:

```bash
multi-agent-brief finalize --config <workspace>/config.yaml
```

6. Report artifact paths for:
   - `output/brief.md`
   - named Markdown if configured
   - `output/brief.docx` if configured
   - `output/intermediate/audited_brief.md`
   - `output/intermediate/claim_ledger.json`
   - `output/intermediate/audit_report.json`

## Source Cache Contract

The MABW `cached_package` provider can read JSON, Markdown, and text files from the configured cache directory. Prefer JSON arrays or objects with an `items` array. Each item should preserve URL, publication date, source name, and reliability where available.

## Hermes Cron Notes

- Attach this skill to each cron job with `--skill multi-agent-brief-hermes`.
- Use `--workdir <repo-root>` so Hermes loads repository instructions and runs commands from the project.
- Pin `--profile <name>` only when the Hermes profile already exists.
- Do not call `send_message` for the normal cron destination; Hermes delivers the final response automatically.
"""


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
