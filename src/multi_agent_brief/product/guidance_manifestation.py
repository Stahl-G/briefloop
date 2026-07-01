"""Read-only guidance manifestation diagnostic projection.

This module validates and summarizes externally assessed manifestation labels
for approved Improvement Memory entries. It does not judge prose quality,
mutate Improvement Memory, approve guidance, run gates, or decide release.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping


GUIDANCE_MANIFESTATION_REPORT_SCHEMA_VERSION = "briefloop.guidance_manifestation_report.v1"
GUIDANCE_MANIFESTATION_PROJECTION_SCHEMA_VERSION = "briefloop.guidance_manifestation_projection.v1"
GUIDANCE_MANIFESTATION_BOUNDARY = (
    "guidance_manifestation_diagnostic_only_not_memory_mutation_gate_or_release_authority"
)
GUIDANCE_MANIFESTATION_RUNTIME_EFFECT = "none"
GUIDANCE_MANIFESTATION_LABELS = (
    "explicitly_reflected",
    "partially_reflected",
    "contradicted",
    "not_observable",
)
GUIDANCE_MANIFESTATION_LABEL_SET = set(GUIDANCE_MANIFESTATION_LABELS)
GUIDANCE_MANIFESTATION_ASSESSMENT_SOURCES = {
    "human",
    "assisted_human",
    "imported",
}
GUIDANCE_MANIFESTATION_ASSESSMENT_METHODS = {
    "human_review",
    "assisted_human_review",
    "imported_external_review",
    "operator_review",
    "synthetic_eval_human_label",
}
GUIDANCE_MANIFESTATION_PROJECTION_STATUSES = {
    "not_available",
    "no_materialized_guidance",
    "missing_report",
    "invalid_report",
    "incomplete",
    "present",
}
GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS = {
    "memory_mutation",
    "guidance_approval",
    "quality_score",
    "gate_decision",
    "release_authority",
    "delivery_approval",
}

_INTERMEDIATE = Path("output/intermediate")
_FORBIDDEN_AUTHORITY_KEYS = {
    "approve_delivery",
    "approved_for_delivery",
    "delivery_approval",
    "gate_decision",
    "guidance_approved",
    "memory_mutation",
    "mutate_memory",
    "quality_score",
    "release_authority",
    "state_transition",
    "write_memory",
}


def guidance_manifestation_report_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / _INTERMEDIATE / "guidance_manifestation_report.json"


def project_workspace_guidance_manifestation(
    workspace: str | Path,
    *,
    runtime_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project guidance manifestation labels without writing workspace state."""

    ws = Path(workspace).expanduser().resolve()
    if runtime_manifest is None:
        manifest, manifest_reason = _read_runtime_manifest(ws / _INTERMEDIATE / "runtime_manifest.json")
    else:
        manifest = dict(runtime_manifest) if isinstance(runtime_manifest, Mapping) else None
        manifest_reason = None if manifest is not None else "runtime_manifest_missing"
    if not isinstance(manifest, dict):
        return _projection(
            status="not_available",
            run_id="unknown",
            materialized_entry_ids=[],
            reason=manifest_reason or "runtime_manifest_missing",
        )

    run_id = _text(manifest.get("run_id")) or "unknown"
    if run_id == "unknown":
        return _projection(
            status="not_available",
            run_id="unknown",
            materialized_entry_ids=[],
            reason="runtime_manifest_missing",
        )
    improvement = manifest.get("improvement") if isinstance(manifest.get("improvement"), Mapping) else {}
    materialized_entry_ids = _string_list(improvement.get("materialized_entry_ids"))
    report_path = guidance_manifestation_report_path(ws)
    snapshot = {
        "snapshot_path": _text(improvement.get("snapshot_path")) or None,
        "snapshot_sha256": _text(improvement.get("snapshot_sha256")) or None,
        "ledger_sha256": _text(improvement.get("ledger_sha256")) or None,
        "memory_sha256": _text(improvement.get("memory_sha256")) or None,
    }

    if not report_path.exists():
        if not materialized_entry_ids:
            return _projection(
                status="no_materialized_guidance",
                run_id=run_id,
                materialized_entry_ids=[],
                snapshot=snapshot,
                reason="no_materialized_guidance",
                report_present=False,
            )
        return _projection(
            status="missing_report",
            run_id=run_id,
            materialized_entry_ids=materialized_entry_ids,
            snapshot=snapshot,
            reason="guidance_manifestation_report_missing",
            report_present=False,
        )

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _projection(
            status="invalid_report",
            run_id=run_id,
            materialized_entry_ids=materialized_entry_ids,
            snapshot=snapshot,
            reason="guidance_manifestation_report_unreadable",
            report_present=True,
        )

    reason = validate_guidance_manifestation_report_payload(
        report,
        current_run_id=run_id,
        materialized_entry_ids=materialized_entry_ids,
    )
    if reason:
        return _projection(
            status="invalid_report",
            run_id=run_id,
            materialized_entry_ids=materialized_entry_ids,
            snapshot=snapshot,
            reason=reason,
            report_present=True,
        )

    entries = [
        dict(entry)
        for entry in report.get("entries", [])
        if isinstance(entry, Mapping)
    ]
    materialized_set = set(materialized_entry_ids)
    assessed_ids = {_text(entry.get("entry_id")) for entry in entries}
    assessed_ids.discard("")
    missing_ids = [entry_id for entry_id in materialized_entry_ids if entry_id not in assessed_ids]
    extra_ids = sorted(entry_id for entry_id in assessed_ids if entry_id not in materialized_set)
    status = "present"
    if missing_ids:
        status = "incomplete"
    if not materialized_entry_ids:
        status = "no_materialized_guidance"
    return _projection(
        status=status,
        run_id=run_id,
        materialized_entry_ids=materialized_entry_ids,
        entries=entries,
        snapshot=snapshot,
        report_present=True,
        report_path="output/intermediate/guidance_manifestation_report.json",
        missing_entry_ids=missing_ids,
        extra_entry_ids=extra_ids,
        assessment_method=_text(report.get("assessment_method")) or "unknown",
        generated_at=_text(report.get("generated_at")) or None,
    )


def validate_guidance_manifestation_report_payload(
    payload: Any,
    *,
    current_run_id: str | None = None,
    materialized_entry_ids: list[str] | None = None,
) -> str | None:
    if not isinstance(payload, dict):
        return "guidance_manifestation_report_schema_error:not_object"
    if _contains_forbidden_authority_key(payload):
        return "guidance_manifestation_report_schema_error:authority_field"
    if payload.get("schema_version") != GUIDANCE_MANIFESTATION_REPORT_SCHEMA_VERSION:
        return "guidance_manifestation_report_schema_error:schema_version"
    if payload.get("workspace") != ".":
        return "guidance_manifestation_report_schema_error:workspace"
    run_id = _text(payload.get("run_id"))
    if not run_id:
        return "guidance_manifestation_report_schema_error:run_id"
    if current_run_id and run_id != current_run_id:
        return "guidance_manifestation_report_schema_error:run_id_mismatch"
    if payload.get("read_only") is not True:
        return "guidance_manifestation_report_schema_error:read_only"
    if payload.get("runtime_effect") != GUIDANCE_MANIFESTATION_RUNTIME_EFFECT:
        return "guidance_manifestation_report_schema_error:runtime_effect"
    if payload.get("boundary") != GUIDANCE_MANIFESTATION_BOUNDARY:
        return "guidance_manifestation_report_schema_error:boundary"
    if not _text(payload.get("generated_at")):
        return "guidance_manifestation_report_schema_error:generated_at"
    if _text(payload.get("assessment_method")) not in GUIDANCE_MANIFESTATION_ASSESSMENT_METHODS:
        return "guidance_manifestation_report_schema_error:assessment_method"
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return "guidance_manifestation_report_schema_error:entries"
    seen: set[str] = set()
    materialized_set = set(_string_list(materialized_entry_ids)) if materialized_entry_ids is not None else None
    for idx, entry in enumerate(entries):
        reason = _validate_report_entry(entry)
        if reason:
            return f"guidance_manifestation_report_schema_error:entries[{idx}].{reason}"
        entry_id = _text(entry.get("entry_id"))
        if entry_id in seen:
            return f"guidance_manifestation_report_schema_error:entries[{idx}].duplicate_entry_id"
        if materialized_set is not None and entry_id not in materialized_set:
            return f"guidance_manifestation_report_schema_error:entries[{idx}].entry_id_not_materialized"
        seen.add(entry_id)
    non_goals = payload.get("non_goals")
    if not isinstance(non_goals, list):
        return "guidance_manifestation_report_schema_error:non_goals"
    if not GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS.issubset({str(item) for item in non_goals}):
        return "guidance_manifestation_report_schema_error:non_goals"
    return None


def validate_guidance_manifestation_projection_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "guidance_manifestation_projection_schema_error:not_object"
    if payload.get("schema_version") != GUIDANCE_MANIFESTATION_PROJECTION_SCHEMA_VERSION:
        return "guidance_manifestation_projection_schema_error:schema_version"
    if payload.get("runtime_effect") != GUIDANCE_MANIFESTATION_RUNTIME_EFFECT:
        return "guidance_manifestation_projection_schema_error:runtime_effect"
    if payload.get("boundary") != GUIDANCE_MANIFESTATION_BOUNDARY:
        return "guidance_manifestation_projection_schema_error:boundary"
    if payload.get("status") not in GUIDANCE_MANIFESTATION_PROJECTION_STATUSES:
        return "guidance_manifestation_projection_schema_error:status"
    if payload.get("python_judged_manifestation") is not False:
        return "guidance_manifestation_projection_schema_error:python_judged_manifestation"
    for field in ("summary_counts", "snapshot"):
        if not isinstance(payload.get(field), dict):
            return f"guidance_manifestation_projection_schema_error:{field}"
    for field in ("materialized_entry_ids", "entries", "missing_entry_ids", "extra_entry_ids", "non_goals"):
        if not isinstance(payload.get(field), list):
            return f"guidance_manifestation_projection_schema_error:{field}"
    for entry in payload.get("entries", []):
        if not isinstance(entry, dict):
            return "guidance_manifestation_projection_schema_error:entries"
        if _text(entry.get("status")) not in GUIDANCE_MANIFESTATION_LABEL_SET:
            return "guidance_manifestation_projection_schema_error:entries.status"
    if not GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS.issubset({str(item) for item in payload.get("non_goals", [])}):
        return "guidance_manifestation_projection_schema_error:non_goals"
    return None


def _projection(
    *,
    status: str,
    run_id: str,
    materialized_entry_ids: list[str],
    entries: list[dict[str, Any]] | None = None,
    snapshot: Mapping[str, Any] | None = None,
    reason: str | None = None,
    report_present: bool = False,
    report_path: str | None = None,
    missing_entry_ids: list[str] | None = None,
    extra_entry_ids: list[str] | None = None,
    assessment_method: str = "unknown",
    generated_at: str | None = None,
) -> dict[str, Any]:
    entry_list = entries or []
    counts = Counter(_text(entry.get("status")) for entry in entry_list if isinstance(entry, Mapping))
    counts = Counter({label: counts.get(label, 0) for label in GUIDANCE_MANIFESTATION_LABELS})
    missing = list(missing_entry_ids) if missing_entry_ids is not None else list(materialized_entry_ids)
    extra = list(extra_entry_ids) if extra_entry_ids is not None else []
    payload: dict[str, Any] = {
        "schema_version": GUIDANCE_MANIFESTATION_PROJECTION_SCHEMA_VERSION,
        "status": status,
        "read_only": True,
        "runtime_effect": GUIDANCE_MANIFESTATION_RUNTIME_EFFECT,
        "boundary": GUIDANCE_MANIFESTATION_BOUNDARY,
        "run_id": run_id or "unknown",
        "report_present": bool(report_present),
        "report_path": report_path,
        "python_judged_manifestation": False,
        "assessment_method": assessment_method,
        "generated_at": generated_at,
        "materialized_entry_ids": list(materialized_entry_ids),
        "entries": entry_list,
        "missing_entry_ids": missing,
        "extra_entry_ids": extra,
        "summary_counts": {
            "materialized_entry_count": len(materialized_entry_ids),
            "assessed_entry_count": len(entry_list),
            "explicitly_reflected_count": counts.get("explicitly_reflected", 0),
            "partially_reflected_count": counts.get("partially_reflected", 0),
            "contradicted_count": counts.get("contradicted", 0),
            "not_observable_count": counts.get("not_observable", 0),
            "unassessed_entry_count": len(missing),
            "extra_entry_count": len(extra),
        },
        "snapshot": dict(snapshot or {}),
        "non_goals": sorted(GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS),
    }
    if reason:
        payload["reason"] = reason
    return payload


def _validate_report_entry(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return "not_object"
    if _contains_forbidden_authority_key(entry):
        return "authority_field"
    if not _text(entry.get("entry_id")):
        return "entry_id"
    if _text(entry.get("status")) not in GUIDANCE_MANIFESTATION_LABEL_SET:
        return "status"
    if _text(entry.get("assessment_source")) not in GUIDANCE_MANIFESTATION_ASSESSMENT_SOURCES:
        return "assessment_source"
    if "notes" in entry and not isinstance(entry.get("notes"), str):
        return "notes"
    refs = entry.get("artifact_refs", [])
    if refs is None:
        refs = []
    if not isinstance(refs, list):
        return "artifact_refs"
    for idx, ref in enumerate(refs):
        if not isinstance(ref, dict):
            return f"artifact_refs[{idx}]"
        path = ref.get("path")
        if path is not None and (not isinstance(path, str) or _path_is_unsafe(path)):
            return f"artifact_refs[{idx}].path"
    return None


def _read_json_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_runtime_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "runtime_manifest_missing"
    except (OSError, UnicodeDecodeError):
        return None, "runtime_manifest_unreadable"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None, "runtime_manifest_unreadable"
    if not isinstance(payload, dict):
        return None, "runtime_manifest_unreadable"
    return payload, None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        text = _text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _path_is_unsafe(value: str) -> bool:
    raw = value.strip()
    if not raw or raw.startswith("~"):
        return True
    posix = PurePosixPath(raw.replace("\\", "/"))
    windows = PureWindowsPath(raw)
    if ".." in posix.parts or ".." in windows.parts:
        return True
    if Path(raw).is_absolute() or windows.drive:
        return True
    return False


def _contains_forbidden_authority_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in _FORBIDDEN_AUTHORITY_KEYS:
                return True
            if _contains_forbidden_authority_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_authority_key(item) for item in value)
    return False
