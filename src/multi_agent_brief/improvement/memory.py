"""Deterministic Improvement Ledger projection and runtime snapshot helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from multi_agent_brief.improvement.contract import (
    LEDGER_RELATIVE_PATH,
    read_ledger_text,
    supersedes_id,
)
from multi_agent_brief.improvement.product_definition import (
    ProductDefinitionDecision,
    classify_ledger_entry_materialization,
)
from multi_agent_brief.improvement.state import ImprovementLedgerError


IMPROVEMENT_MEMORY_SCHEMA = "multi-agent-brief-improvement-memory/v1"
IMPROVEMENT_MEMORY_FILE = "improvement/memory.md"
IMPROVEMENT_MEMORY_SNAPSHOT_FILE = "output/intermediate/improvement_memory_snapshot.md"
MAX_INLINE_IDENTIFIER_LENGTH = 200
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WINDOWS_ABSOLUTE_RE = re.compile(r"\b[A-Za-z]:[\\/]")
_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
]
_FORBIDDEN_PATH_FRAGMENTS = ("/Users/", "/home/", "/var/", "file://")
_INJECTION_PHRASES = ("system:", "developer:", "assistant:", "ignore previous", "ignore all previous")


@dataclass(frozen=True)
class SkippedImprovementEntry:
    entry_id: str
    reason_code: str
    message: str


@dataclass(frozen=True)
class ImprovementMemoryProjection:
    workspace: Path
    ledger_path: Path
    memory_path: Path
    ledger_sha256: str | None
    memory_sha256: str
    selected_entry_ids: list[str]
    eligible_count: int
    skipped_entries: list[SkippedImprovementEntry]
    warnings: list[SkippedImprovementEntry]
    memory_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "workspace": str(self.workspace),
            "ledger_path": str(self.ledger_path),
            "memory_path": IMPROVEMENT_MEMORY_FILE,
            "ledger_sha256": self.ledger_sha256,
            "memory_sha256": self.memory_sha256,
            "selected_entry_ids": list(self.selected_entry_ids),
            "eligible_count": self.eligible_count,
            "skipped_entries": [asdict(item) for item in self.skipped_entries],
            "warnings": [asdict(item) for item in self.warnings],
        }


@dataclass(frozen=True)
class ImprovementMemoryFreezeResult:
    projection: ImprovementMemoryProjection
    manifest_improvement: dict[str, Any]
    snapshot_path: str | None
    snapshot_sha256: str | None
    snapshot_created_or_changed: bool

    def to_dict(self) -> dict[str, Any]:
        payload = self.projection.to_dict()
        payload.update({
            "manifest_improvement": dict(self.manifest_improvement),
            "snapshot_path": self.snapshot_path,
            "snapshot_sha256": self.snapshot_sha256,
            "snapshot_created_or_changed": self.snapshot_created_or_changed,
        })
        return payload


def improvement_memory_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / IMPROVEMENT_MEMORY_FILE


def improvement_memory_snapshot_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / IMPROVEMENT_MEMORY_SNAPSHOT_FILE


def rebuild_improvement_memory(*, workspace: str | Path) -> dict[str, Any]:
    """Project the ledger into improvement/memory.md only."""
    ws = _require_workspace(workspace)
    projection = _build_projection(ws)
    _write_text_if_changed(projection.memory_path, projection.memory_text)
    return projection.to_dict()


def freeze_improvement_memory_for_run(
    *,
    workspace: str | Path,
    run_id: str,
) -> ImprovementMemoryFreezeResult:
    """Recompute memory and freeze it for the current runtime run."""
    ws = _require_workspace(workspace)
    _validate_runtime_identifier(run_id, label="run_id")
    projection = _build_projection(ws)
    _write_text_if_changed(projection.memory_path, projection.memory_text)

    snapshot_path = improvement_memory_snapshot_path(ws)
    snapshot_rel_path: str | None = None
    snapshot_sha256: str | None = None
    changed = False

    if projection.selected_entry_ids:
        snapshot_text = _snapshot_text(projection=projection, run_id=run_id)
        new_sha = sha256_text(snapshot_text)
        old_sha = sha256_file(snapshot_path) if snapshot_path.exists() else None
        if old_sha != new_sha:
            _write_text_atomic(snapshot_path, snapshot_text)
            changed = True
        snapshot_rel_path = IMPROVEMENT_MEMORY_SNAPSHOT_FILE
        snapshot_sha256 = new_sha
    else:
        if snapshot_path.exists():
            snapshot_path.unlink()

    manifest_block = {
        "ledger_sha256": projection.ledger_sha256,
        "memory_sha256": projection.memory_sha256,
        "snapshot_path": snapshot_rel_path,
        "snapshot_sha256": snapshot_sha256,
        "materialized_entry_ids": list(projection.selected_entry_ids),
    }
    _update_runtime_manifest_improvement(ws, manifest_block)
    return ImprovementMemoryFreezeResult(
        projection=projection,
        manifest_improvement=manifest_block,
        snapshot_path=snapshot_rel_path,
        snapshot_sha256=snapshot_sha256,
        snapshot_created_or_changed=changed,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(_text_bytes(text)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_projection(workspace: Path) -> ImprovementMemoryProjection:
    ledger_path = workspace / LEDGER_RELATIVE_PATH
    ledger_sha = sha256_file(ledger_path) if ledger_path.exists() else None
    text = ledger_path.read_text(encoding="utf-8") if ledger_path.exists() else ""
    read_result = read_ledger_text(text)
    if read_result.diagnostics:
        raise ImprovementLedgerError(
            "Improvement ledger is not valid for memory projection.",
            details={"diagnostics": [asdict(item) for item in read_result.diagnostics]},
        )

    selected: list[dict[str, Any]] = []
    skipped: list[SkippedImprovementEntry] = []
    warnings: list[SkippedImprovementEntry] = []
    superseded_by = _superseded_by_map(read_result.current_entries)
    for entry_id, entry in sorted(read_result.current_entries.items()):
        if entry.get("status") != "approved":
            continue
        superseders = superseded_by.get(entry_id) or []
        if superseders:
            skipped.append(SkippedImprovementEntry(
                entry_id=entry_id,
                reason_code="superseded_by_active_guidance",
                message=f"Entry is superseded by {', '.join(superseders)}.",
            ))
            continue
        decision = classify_ledger_entry_materialization(entry)
        if decision.materializable:
            selected.append(entry)
        else:
            skipped.append(_skipped(entry_id=entry_id, decision=decision))
        target_id = supersedes_id(entry)
        if target_id is not None and not decision.materializable:
            warnings.append(SkippedImprovementEntry(
                entry_id=entry_id,
                reason_code="non_materializable_superseder_suppresses_target",
                message=f"{entry_id} suppresses {target_id} but is not materializable.",
            ))

    memory_text = _memory_text(ledger_sha256=ledger_sha, entries=selected)
    return ImprovementMemoryProjection(
        workspace=workspace,
        ledger_path=ledger_path,
        memory_path=improvement_memory_path(workspace),
        ledger_sha256=ledger_sha,
        memory_sha256=sha256_text(memory_text),
        selected_entry_ids=[str(entry["entry_id"]) for entry in selected],
        eligible_count=len(selected),
        skipped_entries=skipped,
        warnings=warnings,
        memory_text=memory_text,
    )


def _memory_text(*, ledger_sha256: str | None, entries: list[dict[str, Any]]) -> str:
    selected_ids = [str(entry["entry_id"]) for entry in entries]
    selected_text = ", ".join(selected_ids) if selected_ids else "none"
    lines = [
        "<!-- mabw:improvement-memory",
        f"schema: {IMPROVEMENT_MEMORY_SCHEMA}",
        f"ledger_sha256: {ledger_sha256 or 'null'}",
        f"entry_count: {len(entries)}",
        f"selected_entry_ids: {selected_text}",
        "-->",
        "",
        "# Improvement Memory",
        "",
        "- Runtime use: audience/taste guidance only.",
        "- Not evidence, not a source, not Claim Ledger input, and not a repair instruction.",
        "- Must not alter material facts, claims, citations, or source support.",
        "",
        "## Entries",
        "",
    ]
    if not entries:
        lines.extend(["No approved materializable improvement entries.", ""])
        return "\n".join(lines)

    for entry in entries:
        change = entry.get("change") if isinstance(entry.get("change"), dict) else {}
        lines.extend([
            f"### {entry.get('entry_id')}",
            "",
            f"- Category: {change.get('category')}",
            f"- Scope: {change.get('scope')}",
            f"- Guidance: {change.get('guidance_text')}",
            f"- {_source_line(entry)}",
            "",
        ])
    return "\n".join(lines)


def _snapshot_text(*, projection: ImprovementMemoryProjection, run_id: str) -> str:
    safe_run_id = _inline_identifier(run_id)
    selected_text = ", ".join(projection.selected_entry_ids)
    return "\n".join([
        "<!-- mabw:improvement-memory-snapshot",
        f"schema: {IMPROVEMENT_MEMORY_SCHEMA}",
        f"run_id: {safe_run_id}",
        f"ledger_sha256: {projection.ledger_sha256 or 'null'}",
        f"memory_sha256: {projection.memory_sha256}",
        f"selected_entry_ids: {selected_text}",
        "-->",
        "",
        "# Improvement Memory Snapshot",
        "",
        f"- Run ID: {safe_run_id}",
        f"- Source: {IMPROVEMENT_MEMORY_FILE}",
        f"- Ledger SHA256: {projection.ledger_sha256 or 'null'}",
        f"- Memory SHA256: {projection.memory_sha256}",
        "- Runtime use: read this frozen snapshot only.",
        "- Taste/audience guidance only; not evidence, not source, not Claim Ledger, and not repair instruction.",
        "- Must not alter material facts, claims, citations, or source support.",
        "- Mid-run ledger or memory edits apply to later runs only.",
        "",
        "## Captured Improvement Memory",
        "",
        projection.memory_text.rstrip(),
        "",
    ])


def _source_line(entry: dict[str, Any]) -> str:
    approved_by = str(entry.get("approved_by") or "unknown")
    approved_at = str(entry.get("approved_at") or "")
    approved_date = approved_at.split("T", 1)[0] if approved_at else "unknown"
    evidence = entry.get("source_evidence") or []
    source_parts: list[str] = []
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            if item.get("source_type") == "feedback_issue":
                safe_run_id = _inline_identifier(item.get("run_id"))
                safe_issue_id = _inline_identifier(item.get("issue_id"))
                source_parts.append(_source_ref_with_runtime(
                    item,
                    base=f"run {safe_run_id} / issue {safe_issue_id}",
                ))
            elif item.get("source_type") == "human_feedback":
                source_parts.append(_source_ref_with_runtime(item, base="human_feedback"))
    source_text = ", ".join(sorted(set(source_parts))) or "unknown"
    return f"approved_by: {approved_by} ({approved_date}) · source: {source_text}"


def _source_ref_with_runtime(item: dict[str, Any], *, base: str) -> str:
    origin = item.get("origin") if isinstance(item.get("origin"), dict) else {}
    runtime = origin.get("origin_runtime") if isinstance(origin, dict) else None
    if runtime is None:
        return base
    return f"{base} / runtime: {_inline_identifier(runtime)}"


def _validate_runtime_identifier(value: str, *, label: str) -> None:
    text = str(value or "").strip()
    if not text:
        raise ImprovementLedgerError(
            f"{label} is required before freezing improvement memory.",
            details={"label": label},
        )
    if _unsafe_inline_identifier(text):
        raise ImprovementLedgerError(
            f"{label} is not safe for improvement memory snapshot metadata.",
            details={"label": label},
        )


def _inline_identifier(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    if _unsafe_inline_identifier(text):
        return "unsafe"
    return text


def _unsafe_inline_identifier(text: str) -> bool:
    lower = text.lower()
    return (
        len(text) > MAX_INLINE_IDENTIFIER_LENGTH
        or "\n" in text
        or "\r" in text
        or text.lstrip().startswith("#")
        or "```" in text
        or "~~~" in text
        or "<!--" in text
        or "-->" in text
        or bool(_CONTROL_CHAR_RE.search(text))
        or bool(_WINDOWS_ABSOLUTE_RE.search(text))
        or any(fragment.lower() in lower for fragment in _FORBIDDEN_PATH_FRAGMENTS)
        or any(pattern.search(text) for pattern in _TOKEN_PATTERNS)
        or any(phrase in lower for phrase in _INJECTION_PHRASES)
    )


def _skipped(*, entry_id: str, decision: ProductDefinitionDecision) -> SkippedImprovementEntry:
    return SkippedImprovementEntry(
        entry_id=entry_id,
        reason_code=decision.reason_code,
        message=decision.message,
    )


def _superseded_by_map(current_entries: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    superseded: dict[str, list[str]] = {}
    for entry_id, entry in sorted(current_entries.items()):
        if entry.get("status") != "approved":
            continue
        target_id = supersedes_id(entry)
        if target_id is None:
            continue
        superseded.setdefault(target_id, []).append(entry_id)
    return {key: sorted(value) for key, value in superseded.items()}


def _update_runtime_manifest_improvement(workspace: Path, improvement: dict[str, Any]) -> None:
    manifest_path = workspace / "output" / "intermediate" / "runtime_manifest.json"
    if not manifest_path.exists():
        raise ImprovementLedgerError(
            "runtime_manifest.json is required before freezing improvement memory.",
            details={"path": str(manifest_path)},
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ImprovementLedgerError(
            "runtime_manifest.json is not valid JSON.",
            details={"path": str(manifest_path), "error": str(exc)},
        ) from exc
    if not isinstance(manifest, dict):
        raise ImprovementLedgerError(
            "runtime_manifest.json must contain an object.",
            details={"path": str(manifest_path)},
        )
    manifest["improvement"] = improvement
    _write_json_atomic(manifest_path, manifest)


def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not ws.exists():
        raise ImprovementLedgerError(
            f"Workspace does not exist: {ws}",
            details={"workspace": str(ws)},
        )
    if not (ws / "config.yaml").exists():
        raise ImprovementLedgerError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            details={"workspace": str(ws)},
        )
    return ws


def _write_text_if_changed(path: Path, text: str) -> bool:
    data = _text_bytes(text)
    if path.exists() and path.read_bytes() == data:
        return False
    _write_text_atomic(path, text)
    return True


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(_text_bytes(text))
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _text_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_text_atomic(path, text)
