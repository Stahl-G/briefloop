"""Audience profile runtime surface helpers.

This module owns workspace-local taste context files. It deliberately does not
import CLI profile types so runtime code can use it without a CLI dependency.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml


AUDIENCE_MEMORY_FILES = {
    "audience_profile": "audience_profile.md",
    "audience_profile_snapshot": "output/intermediate/audience_profile_snapshot.md",
}

_SNAPSHOT_MARKER = "<!-- mabw:audience-profile-snapshot"
_SNAPSHOT_END = "-->"


@dataclass(frozen=True)
class AudienceProfileResult:
    path: Path
    relative_path: str
    created: bool
    missing: bool
    sha256: str


@dataclass(frozen=True)
class AudienceSnapshotResult:
    path: Path
    relative_path: str
    created: bool
    stale_rebuilt: bool
    run_id: str
    source_sha256: str
    captured_body_sha256: str
    snapshot_sha256: str
    profile_created: bool
    profile_missing: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_path(workspace: str | Path, rel_path: str) -> Path:
    ws = Path(workspace).expanduser().resolve()
    path = (ws / rel_path).resolve()
    try:
        path.relative_to(ws)
    except ValueError as exc:
        raise ValueError(f"Audience memory path escapes workspace: {rel_path}") from exc
    return path


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


def profile_data_from_object(profile: Any) -> dict[str, Any]:
    """Convert an arbitrary profile-like object into plain profile data."""
    fields = [
        "company",
        "industry",
        "industry_text",
        "role",
        "audience",
        "audience_profile",
        "brief_title",
        "task_objective",
        "interface_language",
        "output_language",
        "cadence",
        "source_profile",
        "focus_areas",
        "forbidden_sources",
        "output_formats",
    ]
    return {field: getattr(profile, field, "") for field in fields}


def profile_data_from_workspace_config(workspace: str | Path) -> dict[str, Any]:
    """Extract audience profile defaults from an existing workspace config."""
    config_path = Path(workspace).expanduser().resolve() / "config.yaml"
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}

    project = data.get("project") or {}
    audience_profile = data.get("audience_profile") or {}
    language = data.get("language") or {}
    report = data.get("report") or {}
    source = data.get("source") or {}
    source_strategy = data.get("source_strategy") or {}
    output = data.get("output") or {}
    task = data.get("task") or {}

    formats = output.get("formats") if isinstance(output, dict) else []
    if not isinstance(formats, list):
        formats = []

    return {
        "company": project.get("company") or project.get("organization") or project.get("name") or "",
        "industry": project.get("industry") or "",
        "industry_text": project.get("industry") or "",
        "role": project.get("role") or "",
        "audience": project.get("audience") or "",
        "audience_profile": audience_profile.get("id") or "",
        "brief_title": project.get("name") or "",
        "task_objective": task.get("objective") or "",
        "interface_language": language.get("interface") or "",
        "output_language": language.get("output") or "",
        "cadence": report.get("cadence") or "",
        "source_profile": source.get("mode") or source_strategy.get("profile") or "",
        "output_formats": formats,
    }


def build_default_audience_profile(profile_data: Mapping[str, Any] | None = None) -> str:
    """Build a human-editable audience profile Markdown template."""
    data = dict(profile_data or {})
    company = _as_text(data.get("company"), "Unknown organization")
    industry = _as_text(data.get("industry_text") or data.get("industry"), "Unknown industry/theme")
    audience = _as_text(data.get("audience"), "management")
    profile_id = _as_text(data.get("audience_profile"), "default")
    title = _as_text(data.get("brief_title"), "Workspace brief")
    objective = _as_text(data.get("task_objective"), "Summarize material updates for the target reader.")
    language = _as_text(data.get("output_language") or data.get("interface_language"), "en-US")
    cadence = _as_text(data.get("cadence"), "weekly")
    role = _as_text(data.get("role"), "strategy_office")
    source_profile = _as_text(data.get("source_profile"), "llm_decide")
    focus_areas = _as_list(data.get("focus_areas"))
    forbidden_sources = _as_list(data.get("forbidden_sources"))
    output_formats = _as_list(data.get("output_formats"))

    focus_lines = "\n".join(f"- {item}" for item in focus_areas) or "- Add durable audience priorities here."
    avoid_lines = "\n".join(f"- {item}" for item in forbidden_sources) or "- Do not let taste preferences override evidence or audit constraints."
    output_lines = "\n".join(f"- {item}" for item in output_formats) or "- Markdown\n- DOCX"

    return (
        "# Audience Profile\n\n"
        "This workspace-local file records reader taste, department preferences, and recurring editorial guidance.\n"
        "It is not source evidence, not a correctness contract, and not a stage gate.\n"
        "Human edits are allowed. Mid-run edits apply to the next run after a new snapshot is created.\n\n"
        "## Reader Context\n\n"
        f"- Organization: {company}\n"
        f"- Industry/theme: {industry}\n"
        f"- Reader/audience: {audience}\n"
        f"- Audience profile id: {profile_id}\n"
        f"- Internal role/use context: {role}\n"
        f"- Brief title: {title}\n"
        f"- Cadence: {cadence}\n"
        f"- Output language: {language}\n"
        f"- Source mode: {source_profile}\n\n"
        "## Decision Use\n\n"
        f"{objective}\n\n"
        "## Preferred Brief Style\n\n"
        "- Start with the decision-useful conclusion when evidence supports it.\n"
        "- Keep claims bounded by source quality, freshness, and uncertainty.\n"
        "- Prefer concise, concrete implications over generic narrative.\n\n"
        "## Tone And Structure Preferences\n\n"
        "- Write for the stated reader, not for a generic public audience.\n"
        "- Preserve auditability during drafting; reader-facing cleanup happens after audit.\n"
        "- Surface risks, caveats, and missing evidence clearly when relevant.\n\n"
        "## Focus Areas\n\n"
        f"{focus_lines}\n\n"
        "## Output Expectations\n\n"
        f"{output_lines}\n\n"
        "## Recurring Feedback\n\n"
        "- Add durable feedback patterns here after human review.\n\n"
        "## Avoid\n\n"
        f"{avoid_lines}\n\n"
        "## Human Notes\n\n"
        "- Add workspace-specific taste notes here.\n"
    )


def ensure_audience_profile(
    workspace: str | Path,
    profile_data: Mapping[str, Any] | None = None,
) -> AudienceProfileResult:
    """Ensure the workspace has an audience profile."""
    path = _workspace_path(workspace, AUDIENCE_MEMORY_FILES["audience_profile"])
    missing = not path.exists()
    created = False
    if missing:
        _write_text_atomic(path, build_default_audience_profile(profile_data))
        created = True
    return AudienceProfileResult(
        path=path,
        relative_path=AUDIENCE_MEMORY_FILES["audience_profile"],
        created=created,
        missing=missing,
        sha256=sha256_file(path),
    )


def _parse_snapshot_metadata(text: str) -> dict[str, str] | None:
    """Parse only the strict top-of-file audience snapshot metadata block."""
    if not text.startswith(_SNAPSHOT_MARKER):
        return None
    end = text.find(_SNAPSHOT_END)
    if end == -1:
        return None
    block = text[len(_SNAPSHOT_MARKER):end].strip()
    metadata: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            return None
        metadata[key] = value
    required = {"run_id", "source", "created_at", "source_sha256", "captured_body_sha256"}
    if not required.issubset(metadata):
        return None
    if metadata.get("source") != AUDIENCE_MEMORY_FILES["audience_profile"]:
        return None
    return metadata


def _snapshot_text(
    *,
    run_id: str,
    created_at: str,
    source_sha256: str,
    captured_body_sha256: str,
    profile_body: str,
) -> str:
    body = profile_body.rstrip() + "\n"
    return (
        "<!-- mabw:audience-profile-snapshot\n"
        f"run_id: {run_id}\n"
        f"source: {AUDIENCE_MEMORY_FILES['audience_profile']}\n"
        f"created_at: {created_at}\n"
        f"source_sha256: {source_sha256}\n"
        f"captured_body_sha256: {captured_body_sha256}\n"
        "-->\n\n"
        "# Audience Profile Snapshot\n\n"
        f"- Run ID: {run_id}\n"
        f"- Source: {AUDIENCE_MEMORY_FILES['audience_profile']}\n"
        f"- Created at: {created_at}\n"
        f"- Source SHA256: {source_sha256}\n"
        f"- Captured Body SHA256: {captured_body_sha256}\n"
        "- Run behavior: use this snapshot for the current run.\n"
        f"- Mid-run edits to {AUDIENCE_MEMORY_FILES['audience_profile']} apply to later runs only.\n\n"
        "## Captured Audience Profile\n\n"
        f"{body}"
    )


def create_audience_profile_snapshot(
    workspace: str | Path,
    *,
    run_id: str,
    profile_data: Mapping[str, Any] | None = None,
) -> AudienceSnapshotResult:
    """Create or reuse the active run audience profile snapshot."""
    profile = ensure_audience_profile(workspace, profile_data)
    snapshot_path = _workspace_path(workspace, AUDIENCE_MEMORY_FILES["audience_profile_snapshot"])

    if snapshot_path.exists():
        existing_text = snapshot_path.read_text(encoding="utf-8")
        metadata = _parse_snapshot_metadata(existing_text)
        if metadata and metadata.get("run_id") == run_id:
            return AudienceSnapshotResult(
                path=snapshot_path,
                relative_path=AUDIENCE_MEMORY_FILES["audience_profile_snapshot"],
                created=False,
                stale_rebuilt=False,
                run_id=run_id,
                source_sha256=metadata["source_sha256"],
                captured_body_sha256=metadata["captured_body_sha256"],
                snapshot_sha256=sha256_file(snapshot_path),
                profile_created=profile.created,
                profile_missing=profile.missing,
            )

    profile_body = profile.path.read_text(encoding="utf-8")
    source_sha256 = sha256_text(profile_body)
    captured_body_sha256 = source_sha256
    text = _snapshot_text(
        run_id=run_id,
        created_at=utc_now(),
        source_sha256=source_sha256,
        captured_body_sha256=captured_body_sha256,
        profile_body=profile_body,
    )
    stale_rebuilt = snapshot_path.exists()
    _write_text_atomic(snapshot_path, text)
    return AudienceSnapshotResult(
        path=snapshot_path,
        relative_path=AUDIENCE_MEMORY_FILES["audience_profile_snapshot"],
        created=True,
        stale_rebuilt=stale_rebuilt,
        run_id=run_id,
        source_sha256=source_sha256,
        captured_body_sha256=captured_body_sha256,
        snapshot_sha256=sha256_file(snapshot_path),
        profile_created=profile.created,
        profile_missing=profile.missing,
    )
