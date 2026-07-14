"""Release-mode approval ledger and readiness checks.

This product-layer surface records human approvals for internal review modes.
It does not authorize public release, publish externally, or bypass existing
delivery gates.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.core.config import load_config
from multi_agent_brief.orchestrator.runtime_state._io import (
    _read_json_if_exists,
    _read_state_bytes,
    _restore_state_bytes,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    append_event,
    read_event_log_records_strict,
)
from multi_agent_brief.orchestrator.runtime_state.identity import _validate_runtime_run_id, utc_now
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.paths import _require_workspace, runtime_state_paths
from multi_agent_brief.orchestrator_contract import require_canonical_runtime

HUMAN_APPROVAL_LEDGER_SCHEMA = "briefloop.human_approval_ledger.v1"
RELEASE_READINESS_REPORT_SCHEMA = "briefloop.release_readiness_report.v1"
APPROVAL_BOUNDARY = "internal_review_approval_records_only_not_public_release_authorization"
RELEASE_CHECK_BOUNDARY = "release_readiness_check_not_public_release_authorization"

VALID_APPROVAL_DECISIONS = {"approve", "reject", "request_changes"}
VALID_APPROVAL_ROLES = {
    "content_owner",
    "evidence_reviewer",
    "ir_owner",
    "legal_or_compliance_reviewer",
}
APPROVED_BRANDING_AUTHORIZATION_VALUES = {"approved", "authorized"}


RELEASE_MODES: dict[str, dict[str, Any]] = {
    "internal_draft": {
        "approval_required": False,
        "required_roles": [],
        "description": "Internal draft readiness. No human approval is required.",
    },
    "internal_management_review": {
        "approval_required": True,
        "required_roles": ["content_owner"],
        "description": "Ready for internal management review when content owner approval is present.",
    },
    "research_review": {
        "approval_required": True,
        "required_roles": ["content_owner", "evidence_reviewer"],
        "description": "Ready for research review when content and evidence approvals are present.",
    },
    "ir_draft": {
        "approval_required": True,
        "required_roles": ["ir_owner", "evidence_reviewer", "legal_or_compliance_reviewer"],
        "description": "Ready for IR draft review when owner, evidence, and legal/compliance approvals are present.",
    },
    "formal_release_candidate": {
        "approval_required": True,
        "required_roles": ["content_owner", "evidence_reviewer", "legal_or_compliance_reviewer"],
        "description": "Ready for formal release-candidate review when required internal approvals are present.",
    },
}


class ReleaseApprovalError(RuntimeError):
    """Raised when release approval state cannot be recorded or checked."""


@dataclass(frozen=True)
class ApprovalRecordResult:
    payload: dict[str, Any]
    event: dict[str, Any] | None = None


def approval_ledger_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / "output" / "intermediate" / "human_approval_ledger.json"


def release_readiness_report_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / "output" / "intermediate" / "release_readiness_report.json"


def release_modes_payload() -> dict[str, Any]:
    return {
        "schema_version": "briefloop.release_modes.v1",
        "boundary": RELEASE_CHECK_BOUNDARY,
        "modes": {
            mode: {
                "approval_required": bool(config["approval_required"]),
                "required_roles": list(config["required_roles"]),
                "description": str(config["description"]),
            }
            for mode, config in sorted(RELEASE_MODES.items())
        },
    }


def initialize_approval_ledger(
    *,
    workspace: str | Path,
    mode: str,
    actor: str = "human",
) -> ApprovalRecordResult:
    ws, run_id = _workspace_and_run_id(workspace)
    normalized_mode = _require_release_mode(mode)
    ledger_path = approval_ledger_path(ws)
    report_path = release_readiness_report_path(ws)
    paths = runtime_state_paths(ws)
    snapshots = _snapshot_files([ledger_path, report_path, paths["event_log"]])
    archived_stale_paths: list[Path] = []
    try:
        ledger = _load_or_new_ledger(ws)
        link_reason = validate_human_approval_ledger_event_links(ledger, workspace=ws)
        if link_reason:
            if _ledger_has_current_run_entries(ledger, run_id):
                raise ReleaseApprovalError(f"human_approval_ledger invalid: {link_reason}")
            archived_stale_paths.extend(
                _archive_stale_approval_artifacts(
                    workspace=ws,
                    ledger=ledger,
                    current_run_id=run_id,
                )
            )
            ledger = _new_ledger()
        now = utc_now()
        event = append_event(
            workspace=ws,
            run_id=run_id,
            event_type="human_approval_ledger_initialized",
            actor="cli",
            reason=f"Initialized human approval ledger for {normalized_mode}.",
            metadata={
                "mode": normalized_mode,
                "approval_required": RELEASE_MODES[normalized_mode]["approval_required"],
                "required_roles": list(RELEASE_MODES[normalized_mode]["required_roles"]),
                "actor_id_present": bool(str(actor or "").strip()),
                "boundary": APPROVAL_BOUNDARY,
            },
        )
        modes = ledger.setdefault("initialized_modes", {})
        modes[normalized_mode] = {
            "mode": normalized_mode,
            "run_id": run_id,
            "initialized_at": now,
            "event_id": event["event_id"],
            "approval_required": RELEASE_MODES[normalized_mode]["approval_required"],
            "required_roles": list(RELEASE_MODES[normalized_mode]["required_roles"]),
        }
        ledger["updated_at"] = now
        _write_json_atomic(ledger_path, ledger)
        return ApprovalRecordResult(payload=ledger, event=event)
    except Exception:
        _restore_files(snapshots)
        _remove_archived_stale_paths(archived_stale_paths)
        raise


def record_human_approval(
    *,
    workspace: str | Path,
    role: str,
    decision: str,
    reason: str,
    mode: str | None = None,
    actor_id: str = "human",
) -> ApprovalRecordResult:
    ws, run_id = _workspace_and_run_id(workspace)
    ledger = _load_or_new_ledger(ws)
    normalized_mode = _resolve_mode_for_record(ledger, mode)
    normalized_role = _require_role_for_mode(normalized_mode, role)
    normalized_decision = _require_decision(decision)
    clean_reason = _require_reason(reason)
    clean_actor = _clean_text(actor_id) or "human"
    now = utc_now()
    initialized_mode = _initialized_mode_entry(ledger, normalized_mode)
    if initialized_mode is None:
        raise ReleaseApprovalError(
            f"approval mode {normalized_mode} must be initialized with approval init before recording decisions."
        )
    if _clean_text(initialized_mode.get("run_id")) != run_id:
        raise ReleaseApprovalError(
            f"approval mode {normalized_mode} was initialized for a different run; initialize it for the current run."
        )
    event_index = _event_index_for_workspace(ws)
    link_reason = validate_human_approval_ledger_event_links(ledger, workspace=ws)
    if link_reason:
        raise ReleaseApprovalError(f"human_approval_ledger invalid: {link_reason}")
    init_reason = _initialized_mode_event_error(
        initialized_mode,
        mode=normalized_mode,
        event_index=event_index,
    )
    if init_reason:
        raise ReleaseApprovalError(f"human_approval_ledger invalid: {init_reason}")
    record = {
        "approval_id": f"APR-{uuid.uuid4().hex[:12]}",
        "run_id": run_id,
        "mode": normalized_mode,
        "role": normalized_role,
        "decision": normalized_decision,
        "reason": clean_reason,
        "actor_id": clean_actor,
        "recorded_at": now,
        "boundary": APPROVAL_BOUNDARY,
    }
    ledger_path = approval_ledger_path(ws)
    paths = runtime_state_paths(ws)
    snapshots = _snapshot_files([ledger_path, paths["event_log"]])
    try:
        event = append_event(
            workspace=ws,
            run_id=run_id,
            event_type="human_approval_recorded",
            actor="cli",
            reason=f"Recorded {normalized_decision} approval decision for {normalized_role}.",
            metadata={
                "mode": normalized_mode,
                "role": normalized_role,
                "decision": normalized_decision,
                "approval_id": record["approval_id"],
                "actor_id_present": bool(clean_actor),
                "reason_present": bool(clean_reason),
                "boundary": APPROVAL_BOUNDARY,
            },
        )
        record["event_id"] = event["event_id"]
        ledger.setdefault("records", []).append(record)
        ledger["updated_at"] = now
        _write_json_atomic(ledger_path, ledger)
        return ApprovalRecordResult(payload=ledger, event=event)
    except Exception:
        _restore_files(snapshots)
        raise


def check_release_readiness(
    *,
    workspace: str | Path,
    mode: str,
) -> ApprovalRecordResult:
    ws, run_id = _workspace_and_run_id(workspace)
    normalized_mode = _require_release_mode(mode)
    ledger = _read_json_if_exists(approval_ledger_path(ws))
    if isinstance(ledger, dict):
        link_reason = validate_human_approval_ledger_event_links(ledger, workspace=ws)
        if link_reason:
            raise ReleaseApprovalError(f"human_approval_ledger invalid: {link_reason}")
    event_index = _event_index_for_workspace(ws)
    ledger_records = _ledger_records(ledger)
    latest = _latest_records_for_mode(
        ledger_records,
        normalized_mode,
        run_id,
        event_index=event_index,
    )
    required_roles = list(RELEASE_MODES[normalized_mode]["required_roles"])
    approval_required = bool(RELEASE_MODES[normalized_mode]["approval_required"])
    approved_roles = [
        role for role in required_roles
        if latest.get(role, {}).get("decision") == "approve"
    ]
    rejected_roles = [
        role for role in required_roles
        if latest.get(role, {}).get("decision") in {"reject", "request_changes"}
    ]
    missing_roles = [
        role for role in required_roles
        if role not in latest
    ]
    blockers: list[str] = []
    if approval_required:
        blockers.extend(f"missing_required_approval:{role}" for role in missing_roles)
        blockers.extend(f"approval_not_approved:{role}" for role in rejected_roles)
    branding_context = _release_branding_context(ws)
    blockers.extend(branding_context["blockers"])
    status = "pass" if not blockers else "blocked"
    now = utc_now()
    report = {
        "schema_version": RELEASE_READINESS_REPORT_SCHEMA,
        "run_id": run_id,
        "mode": normalized_mode,
        "status": status,
        "approval_required": approval_required,
        "required_roles": required_roles,
        "approved_roles": approved_roles,
        "missing_roles": missing_roles,
        "rejected_or_changes_requested_roles": rejected_roles,
        "blockers": blockers,
        "records_considered": [
            _public_record(latest[role])
            for role in sorted(latest)
            if role in required_roles
        ],
        "branding_context": branding_context,
        "ledger_path": "output/intermediate/human_approval_ledger.json"
        if approval_ledger_path(ws).exists()
        else "",
        "generated_at": now,
        "boundary": RELEASE_CHECK_BOUNDARY,
        "authorization": "not_authorized_for_public_release",
        "next_step": _next_step(status=status, mode=normalized_mode, blockers=blockers),
    }
    report_path = release_readiness_report_path(ws)
    paths = runtime_state_paths(ws)
    snapshots = _snapshot_files([report_path, paths["event_log"]])
    try:
        event = append_event(
            workspace=ws,
            run_id=run_id,
            event_type="release_readiness_checked",
            actor="cli",
            reason=f"Checked release readiness for {normalized_mode}: {status}.",
            metadata={
                "mode": normalized_mode,
                "status": status,
                "approval_required": approval_required,
                "missing_roles": missing_roles,
                "branding_status": branding_context["status"],
                "branding_blocked": bool(branding_context["blockers"]),
                "branding_blockers": list(branding_context["blockers"]),
                "blocked": bool(blockers),
                "boundary": RELEASE_CHECK_BOUNDARY,
            },
        )
        report["event_id"] = event["event_id"]
        _write_json_atomic(report_path, report)
        return ApprovalRecordResult(payload=report, event=event)
    except Exception:
        _restore_files(snapshots)
        raise


def validate_human_approval_ledger_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "human_approval_ledger_schema_error:not_object"
    if payload.get("schema_version") != HUMAN_APPROVAL_LEDGER_SCHEMA:
        return "human_approval_ledger_schema_error:schema_version"
    if payload.get("boundary") != APPROVAL_BOUNDARY:
        return "human_approval_ledger_schema_error:boundary"
    records = payload.get("records")
    if not isinstance(records, list):
        return "human_approval_ledger_schema_error:records"
    initialized_modes = payload.get("initialized_modes")
    if not isinstance(initialized_modes, dict):
        return "human_approval_ledger_schema_error:initialized_modes"
    for mode_key, entry in sorted(initialized_modes.items()):
        if mode_key not in RELEASE_MODES:
            return f"human_approval_ledger_schema_error:initialized_modes.{mode_key}"
        if not isinstance(entry, dict):
            return f"human_approval_ledger_schema_error:initialized_modes.{mode_key}"
        if _clean_text(entry.get("mode")) != mode_key:
            return f"human_approval_ledger_schema_error:initialized_modes.{mode_key}.mode"
        if not _clean_text(entry.get("run_id")):
            return f"human_approval_ledger_schema_error:initialized_modes.{mode_key}.run_id"
        if not _clean_text(entry.get("event_id")):
            return f"human_approval_ledger_schema_error:initialized_modes.{mode_key}.event_id"
        if not _clean_text(entry.get("initialized_at")):
            return f"human_approval_ledger_schema_error:initialized_modes.{mode_key}.initialized_at"
        if entry.get("required_roles") != list(RELEASE_MODES[mode_key]["required_roles"]):
            return f"human_approval_ledger_schema_error:initialized_modes.{mode_key}.required_roles"
    seen_ids: set[str] = set()
    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            return f"human_approval_ledger_schema_error:records[{idx}]"
        approval_id = _clean_text(record.get("approval_id"))
        if not approval_id:
            return f"human_approval_ledger_schema_error:records[{idx}].approval_id"
        if approval_id in seen_ids:
            return f"human_approval_ledger_schema_error:duplicate_approval_id:{approval_id}"
        seen_ids.add(approval_id)
        if not _clean_text(record.get("run_id")):
            return f"human_approval_ledger_schema_error:records[{idx}].run_id"
        if not _clean_text(record.get("event_id")):
            return f"human_approval_ledger_schema_error:records[{idx}].event_id"
        mode = _clean_text(record.get("mode"))
        if mode not in RELEASE_MODES:
            return f"human_approval_ledger_schema_error:records[{idx}].mode"
        role = _clean_text(record.get("role"))
        if role not in RELEASE_MODES[mode]["required_roles"]:
            return f"human_approval_ledger_schema_error:records[{idx}].role"
        if _clean_text(record.get("decision")) not in VALID_APPROVAL_DECISIONS:
            return f"human_approval_ledger_schema_error:records[{idx}].decision"
        if not _clean_text(record.get("reason")):
            return f"human_approval_ledger_schema_error:records[{idx}].reason"
        if not _clean_text(record.get("recorded_at")):
            return f"human_approval_ledger_schema_error:records[{idx}].recorded_at"
    return None


def validate_release_readiness_report_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "release_readiness_report_schema_error:not_object"
    if payload.get("schema_version") != RELEASE_READINESS_REPORT_SCHEMA:
        return "release_readiness_report_schema_error:schema_version"
    if not _clean_text(payload.get("run_id")):
        return "release_readiness_report_schema_error:run_id"
    if not _clean_text(payload.get("event_id")):
        return "release_readiness_report_schema_error:event_id"
    mode = _clean_text(payload.get("mode"))
    if mode not in RELEASE_MODES:
        return "release_readiness_report_schema_error:mode"
    if payload.get("boundary") != RELEASE_CHECK_BOUNDARY:
        return "release_readiness_report_schema_error:boundary"
    if payload.get("authorization") != "not_authorized_for_public_release":
        return "release_readiness_report_schema_error:authorization"
    if payload.get("status") not in {"pass", "blocked"}:
        return "release_readiness_report_schema_error:status"
    for field in ("required_roles", "approved_roles", "missing_roles", "blockers"):
        if not isinstance(payload.get(field), list):
            return f"release_readiness_report_schema_error:{field}"
    branding_context = payload.get("branding_context")
    if not isinstance(branding_context, dict):
        return "release_readiness_report_schema_error:branding_context"
    if branding_context.get("status") not in {"not_required", "complete", "missing", "blocked"}:
        return "release_readiness_report_schema_error:branding_context.status"
    for field in ("missing_fields", "blockers"):
        if not isinstance(branding_context.get(field), list):
            return f"release_readiness_report_schema_error:branding_context.{field}"
    required_roles = list(RELEASE_MODES[mode]["required_roles"])
    if payload.get("required_roles") != required_roles:
        return "release_readiness_report_schema_error:required_roles"
    blockers = payload.get("blockers")
    for blocker in branding_context.get("blockers", []):
        if blocker not in blockers:
            return "release_readiness_report_schema_error:branding_context.blockers"
    if payload.get("status") == "blocked" and not blockers:
        return "release_readiness_report_schema_error:blockers"
    if payload.get("status") == "pass" and blockers:
        return "release_readiness_report_schema_error:blockers"
    return None


def validate_human_approval_ledger_event_links(
    payload: Any,
    *,
    workspace: str | Path,
) -> str | None:
    """Validate approval ledger event IDs against the workspace event log."""

    shape_reason = validate_human_approval_ledger_payload(payload)
    if shape_reason:
        return shape_reason
    if not isinstance(payload, dict):
        return "human_approval_ledger_event_link_error:not_object"
    initialized_modes = payload.get("initialized_modes")
    records = payload.get("records")
    needs_event_log = bool(initialized_modes) or bool(records)
    if not needs_event_log:
        return None
    event_index_or_reason = _event_index_or_reason(workspace)
    if isinstance(event_index_or_reason, str):
        return f"human_approval_ledger_event_link_error:{event_index_or_reason}"
    event_index = event_index_or_reason
    if isinstance(initialized_modes, dict):
        for mode_key, entry in sorted(initialized_modes.items()):
            if not isinstance(entry, dict):
                continue
            reason = _initialized_mode_event_error(
                entry,
                mode=mode_key,
                event_index=event_index,
            )
            if reason:
                return f"human_approval_ledger_event_link_error:{reason}"
    if isinstance(records, list):
        for idx, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            reason = _approval_record_initialized_mode_error(
                record,
                initialized_modes=initialized_modes if isinstance(initialized_modes, dict) else {},
                index=idx,
            )
            if reason:
                return f"human_approval_ledger_event_link_error:{reason}"
            reason = _approval_record_event_error(
                record,
                event_index=event_index,
                index=idx,
            )
            if reason:
                return f"human_approval_ledger_event_link_error:{reason}"
    return None


def validate_release_readiness_report_event_link(
    payload: Any,
    *,
    workspace: str | Path,
) -> str | None:
    """Validate release-readiness report event ID against the workspace event log."""

    shape_reason = validate_release_readiness_report_payload(payload)
    if shape_reason:
        return shape_reason
    if not isinstance(payload, dict):
        return "release_readiness_report_event_link_error:not_object"
    event_index_or_reason = _event_index_or_reason(workspace)
    if isinstance(event_index_or_reason, str):
        return f"release_readiness_report_event_link_error:{event_index_or_reason}"
    event_index = event_index_or_reason
    event_id = _clean_text(payload.get("event_id"))
    event = event_index.get(event_id)
    if event is None:
        return "release_readiness_report_event_link_error:event_missing"
    if event.get("event_type") != "release_readiness_checked":
        return "release_readiness_report_event_link_error:event_type"
    if _clean_text(event.get("run_id")) != _clean_text(payload.get("run_id")):
        return "release_readiness_report_event_link_error:event_run_id"
    metadata = _event_metadata(event)
    if _clean_text(metadata.get("mode")) != _clean_text(payload.get("mode")):
        return "release_readiness_report_event_link_error:event_metadata_mismatch"
    if _clean_text(metadata.get("status")) != _clean_text(payload.get("status")):
        return "release_readiness_report_event_link_error:event_metadata_mismatch"
    if bool(metadata.get("approval_required")) != bool(payload.get("approval_required")):
        return "release_readiness_report_event_link_error:event_metadata_mismatch"
    branding_context = payload.get("branding_context")
    if not isinstance(branding_context, Mapping):
        return "release_readiness_report_event_link_error:branding_context"
    if _clean_text(metadata.get("branding_status")) != _clean_text(branding_context.get("status")):
        return "release_readiness_report_event_link_error:event_metadata_mismatch"
    if bool(metadata.get("branding_blocked")) != bool(branding_context.get("blockers")):
        return "release_readiness_report_event_link_error:event_metadata_mismatch"
    if _clean_text_list(metadata.get("branding_blockers")) != _clean_text_list(branding_context.get("blockers")):
        return "release_readiness_report_event_link_error:event_metadata_mismatch"
    return None


def _workspace_and_run_id(workspace: str | Path) -> tuple[Path, str]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    manifest = _read_json_if_exists(paths["runtime_manifest"])
    if not isinstance(manifest, dict):
        raise ReleaseApprovalError(
            "runtime_manifest.json is required before recording release approvals."
        )
    if manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise ReleaseApprovalError("runtime_manifest.json has an unsupported schema.")
    try:
        require_canonical_runtime(manifest.get("runtime"))
        run_id = _validate_runtime_run_id(manifest.get("run_id"), path=paths["runtime_manifest"])
    except (RuntimeStateError, ValueError) as exc:
        raise ReleaseApprovalError(str(exc)) from exc
    _require_current_run_event_chain(paths["event_log"], run_id)
    return ws, run_id


def _require_current_run_event_chain(event_log_path: Path, run_id: str) -> None:
    if not event_log_path.exists():
        raise ReleaseApprovalError(
            "event_log.jsonl is required before recording release approvals."
        )
    try:
        records = read_event_log_records_strict(event_log_path)
    except RuntimeStateError as exc:
        raise ReleaseApprovalError(str(exc)) from exc
    has_current_run_start = any(
        _clean_text(record.get("run_id")) == run_id
        and record.get("event_type") in {"run_initialized", "run_reset"}
        for record in records
    )
    if not has_current_run_start:
        raise ReleaseApprovalError(
            "event_log.jsonl is missing a current-run initialization event."
        )


def _event_index_for_workspace(workspace: str | Path) -> dict[str, dict[str, Any]]:
    event_index_or_reason = _event_index_or_reason(workspace)
    if isinstance(event_index_or_reason, str):
        raise ReleaseApprovalError(f"event_log invalid: {event_index_or_reason}")
    return event_index_or_reason


def _event_index_or_reason(workspace: str | Path) -> dict[str, dict[str, Any]] | str:
    ws = Path(workspace).expanduser().resolve()
    event_log_path = runtime_state_paths(ws)["event_log"]
    if not event_log_path.exists():
        return "event_log_missing"
    try:
        records = read_event_log_records_strict(event_log_path)
    except RuntimeStateError:
        return "event_log_invalid"
    event_index: dict[str, dict[str, Any]] = {}
    for record in records:
        event_id = _clean_text(record.get("event_id"))
        if not event_id:
            continue
        if event_id in event_index:
            return f"duplicate_event_id:{event_id}"
        event_index[event_id] = record
    return event_index


def _initialized_mode_event_error(
    entry: Mapping[str, Any],
    *,
    mode: str,
    event_index: Mapping[str, Mapping[str, Any]],
) -> str | None:
    event_id = _clean_text(entry.get("event_id"))
    event = event_index.get(event_id)
    if event is None:
        return f"initialized_modes.{mode}.event_missing"
    if event.get("event_type") != "human_approval_ledger_initialized":
        return f"initialized_modes.{mode}.event_type"
    if _clean_text(event.get("run_id")) != _clean_text(entry.get("run_id")):
        return f"initialized_modes.{mode}.event_run_id"
    metadata = _event_metadata(event)
    if _clean_text(metadata.get("mode")) != mode:
        return f"initialized_modes.{mode}.event_metadata_mismatch"
    return None


def _approval_record_event_error(
    record: Mapping[str, Any],
    *,
    event_index: Mapping[str, Mapping[str, Any]],
    index: int | None = None,
) -> str | None:
    field = f"records[{index}]" if index is not None else "record"
    event_id = _clean_text(record.get("event_id"))
    event = event_index.get(event_id)
    if event is None:
        return f"{field}.event_missing"
    if event.get("event_type") != "human_approval_recorded":
        return f"{field}.event_type"
    if _clean_text(event.get("run_id")) != _clean_text(record.get("run_id")):
        return f"{field}.event_run_id"
    metadata = _event_metadata(event)
    for key in ("approval_id", "mode", "role", "decision"):
        if _clean_text(metadata.get(key)) != _clean_text(record.get(key)):
            return f"{field}.event_metadata_mismatch"
    return None


def _approval_record_initialized_mode_error(
    record: Mapping[str, Any],
    *,
    initialized_modes: Mapping[str, Any],
    index: int,
) -> str | None:
    field = f"records[{index}]"
    mode = _clean_text(record.get("mode"))
    entry = initialized_modes.get(mode)
    if not isinstance(entry, Mapping):
        return f"{field}.mode_not_initialized"
    if _clean_text(entry.get("run_id")) != _clean_text(record.get("run_id")):
        return f"{field}.initialized_mode_run_id"
    if _clean_text(entry.get("mode")) != mode:
        return f"{field}.initialized_mode_mismatch"
    return None


def _event_metadata(event: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = event.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _snapshot_files(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: _read_state_bytes(path) for path in paths}


def _restore_files(snapshots: Mapping[Path, bytes | None]) -> None:
    rollback_errors: list[str] = []
    for path, data in snapshots.items():
        try:
            _restore_state_bytes(path, data)
        except RuntimeStateError as exc:
            rollback_errors.append(str(exc))
    if rollback_errors:
        raise ReleaseApprovalError(
            "Release approval transaction rollback failed: " + "; ".join(rollback_errors)
        )


def _load_or_new_ledger(workspace: Path) -> dict[str, Any]:
    path = approval_ledger_path(workspace)
    try:
        payload = _read_json_if_exists(path)
    except RuntimeStateError as exc:
        raise ReleaseApprovalError(str(exc)) from exc
    if payload is None:
        return _new_ledger()
    reason = validate_human_approval_ledger_payload(payload)
    if reason:
        raise ReleaseApprovalError(f"human_approval_ledger invalid: {reason}")
    return payload


def _new_ledger() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": HUMAN_APPROVAL_LEDGER_SCHEMA,
        "boundary": APPROVAL_BOUNDARY,
        "created_at": now,
        "updated_at": now,
        "initialized_modes": {},
        "records": [],
    }


def _ledger_has_current_run_entries(ledger: Mapping[str, Any], run_id: str) -> bool:
    initialized = ledger.get("initialized_modes")
    if isinstance(initialized, Mapping):
        for entry in initialized.values():
            if isinstance(entry, Mapping) and _clean_text(entry.get("run_id")) == run_id:
                return True
    records = ledger.get("records")
    if isinstance(records, list):
        for record in records:
            if isinstance(record, Mapping) and _clean_text(record.get("run_id")) == run_id:
                return True
    return False


def _archive_stale_approval_artifacts(
    *,
    workspace: Path,
    ledger: Mapping[str, Any],
    current_run_id: str,
) -> list[Path]:
    run_token = _stale_ledger_archive_run_id(ledger, current_run_id)
    archived_paths: list[Path] = []
    for path in (approval_ledger_path(workspace), release_readiness_report_path(workspace)):
        archived = _archive_stale_approval_artifact(path, run_token=run_token)
        if archived is not None:
            archived_paths.append(archived)
    return archived_paths


def _archive_stale_approval_artifact(path: Path, *, run_token: str) -> Path | None:
    if not path.exists():
        return None
    archive = path.with_name(f"{path.stem}.{run_token}{path.suffix}")
    if archive.exists():
        archive = path.with_name(f"{path.stem}.{run_token}.{uuid.uuid4().hex[:8]}{path.suffix}")
    try:
        os.replace(path, archive)
    except OSError as exc:
        raise ReleaseApprovalError(
            f"Failed to archive stale approval artifact {path}: {exc}"
        ) from exc
    return archive


def _remove_archived_stale_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ReleaseApprovalError(
                f"Failed to remove stale approval archive after rollback: {path}: {exc}"
            ) from exc


def _stale_ledger_archive_run_id(ledger: Mapping[str, Any], current_run_id: str) -> str:
    candidates: list[str] = []
    initialized = ledger.get("initialized_modes")
    if isinstance(initialized, Mapping):
        for entry in initialized.values():
            if isinstance(entry, Mapping):
                candidates.append(_clean_text(entry.get("run_id")))
    records = ledger.get("records")
    if isinstance(records, list):
        for record in records:
            if isinstance(record, Mapping):
                candidates.append(_clean_text(record.get("run_id")))
    for candidate in candidates:
        if candidate and candidate != current_run_id:
            return _safe_archive_token(candidate)
    return "stale"


def _safe_archive_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return token or "stale"


def _initialized_mode_entry(ledger: Mapping[str, Any], mode: str) -> dict[str, Any] | None:
    initialized = ledger.get("initialized_modes")
    if not isinstance(initialized, dict):
        return None
    entry = initialized.get(mode)
    return entry if isinstance(entry, dict) else None


def _require_release_mode(value: str | None) -> str:
    mode = _clean_text(value)
    if mode not in RELEASE_MODES:
        available = ", ".join(sorted(RELEASE_MODES))
        raise ReleaseApprovalError(f"unknown release mode: {value}. Available modes: {available}")
    return mode


def _resolve_mode_for_record(ledger: Mapping[str, Any], mode: str | None) -> str:
    if mode:
        return _require_release_mode(mode)
    initialized = ledger.get("initialized_modes")
    modes = sorted(initialized) if isinstance(initialized, dict) else []
    if len(modes) == 1:
        return _require_release_mode(modes[0])
    if not modes:
        raise ReleaseApprovalError("approval record requires --mode before any ledger mode is initialized.")
    raise ReleaseApprovalError("approval record requires --mode when multiple modes are initialized.")


def _require_role_for_mode(mode: str, role: str | None) -> str:
    text = _clean_text(role)
    required = RELEASE_MODES[mode]["required_roles"]
    if text not in required:
        raise ReleaseApprovalError(
            f"role {role!r} is not required for release mode {mode}."
        )
    return text


def _require_decision(value: str | None) -> str:
    text = _clean_text(value)
    if text not in VALID_APPROVAL_DECISIONS:
        raise ReleaseApprovalError(
            "approval decision must be one of: approve, reject, request_changes."
        )
    return text


def _require_reason(value: str | None) -> str:
    text = _clean_text(value)
    if not text:
        raise ReleaseApprovalError("approval reason is required.")
    if len(text) > 1000:
        raise ReleaseApprovalError("approval reason is too long.")
    return text


def _ledger_records(ledger: Any) -> list[dict[str, Any]]:
    if not isinstance(ledger, dict):
        return []
    reason = validate_human_approval_ledger_payload(ledger)
    if reason:
        raise ReleaseApprovalError(f"human_approval_ledger invalid: {reason}")
    return [record for record in ledger.get("records", []) if isinstance(record, dict)]


def _latest_records_for_mode(
    records: list[dict[str, Any]],
    mode: str,
    run_id: str,
    *,
    event_index: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("mode") != mode:
            continue
        if _clean_text(record.get("run_id")) != run_id:
            continue
        if not _clean_text(record.get("event_id")):
            continue
        if _approval_record_event_error(record, event_index=event_index) is not None:
            continue
        role = _clean_text(record.get("role"))
        if role:
            latest[role] = record
    return latest


def _release_branding_context(workspace: Path) -> dict[str, Any]:
    config_path = workspace / "config.yaml"
    if not config_path.exists():
        return _empty_branding_context(required=False, status="not_required")
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise ReleaseApprovalError(
            f"config.yaml invalid for release branding context: {exc}"
        ) from exc
    release_config = config.get("release")
    release_config = release_config if isinstance(release_config, dict) else {}
    branding = release_config.get("branding")
    if not isinstance(branding, dict):
        return _empty_branding_context(required=False, status="not_required")

    required = _as_bool(branding.get("required"), default=False)
    institution_name = _first_text(
        branding.get("institution_name"),
        branding.get("organization_name"),
        branding.get("brand_owner"),
    )
    authorization_status = _first_text(
        branding.get("institution_use_authorization"),
        branding.get("authorization_status"),
    ).lower()
    authorization_reference = _first_text(
        branding.get("authorization_reference"),
        branding.get("authorization_ref"),
        branding.get("approval_reference"),
    )
    required_fields = [
        "institution_name",
        "institution_use_authorization",
        "authorization_reference",
    ] if required else []
    missing_fields: list[str] = []
    blockers: list[str] = []
    if required and not institution_name:
        missing_fields.append("institution_name")
        blockers.append("missing_branding_metadata:institution_name")
    if required and not authorization_status:
        missing_fields.append("institution_use_authorization")
        blockers.append("missing_branding_metadata:institution_use_authorization")
    if (
        required
        and authorization_status in APPROVED_BRANDING_AUTHORIZATION_VALUES
        and not authorization_reference
    ):
        missing_fields.append("authorization_reference")
        blockers.append("missing_branding_metadata:authorization_reference")
    if (
        required
        and authorization_status
        and authorization_status not in APPROVED_BRANDING_AUTHORIZATION_VALUES
    ):
        blockers.append(f"institution_branding_not_authorized:{authorization_status}")
    if not required:
        status = "not_required"
    elif missing_fields:
        status = "missing"
    elif blockers:
        status = "blocked"
    else:
        status = "complete"
    return {
        "required": required,
        "status": status,
        "required_fields": required_fields,
        "present_fields": [
            field
            for field, present in (
                ("institution_name", bool(institution_name)),
                ("institution_use_authorization", bool(authorization_status)),
                ("authorization_reference", bool(authorization_reference)),
            )
            if present
        ],
        "missing_fields": missing_fields,
        "institution_name_present": bool(institution_name),
        "institution_use_authorization": authorization_status,
        "authorization_reference_present": bool(authorization_reference),
        "blockers": blockers,
        "boundary": "branding_context_metadata_not_public_release_authorization",
    }


def _empty_branding_context(*, required: bool, status: str) -> dict[str, Any]:
    return {
        "required": required,
        "status": status,
        "required_fields": [],
        "present_fields": [],
        "missing_fields": [],
        "institution_name_present": False,
        "institution_use_authorization": "",
        "authorization_reference_present": False,
        "blockers": [],
        "boundary": "branding_context_metadata_not_public_release_authorization",
    }


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _public_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "approval_id": _clean_text(record.get("approval_id")),
        "run_id": _clean_text(record.get("run_id")),
        "mode": _clean_text(record.get("mode")),
        "role": _clean_text(record.get("role")),
        "decision": _clean_text(record.get("decision")),
        "recorded_at": _clean_text(record.get("recorded_at")),
        "event_id": _clean_text(record.get("event_id")),
        "reason_present": bool(_clean_text(record.get("reason"))),
        "actor_id_present": bool(_clean_text(record.get("actor_id"))),
    }


def _next_step(*, status: str, mode: str, blockers: list[str]) -> str:
    if status == "pass":
        return f"Ready for {mode} internal review; not authorized for public release."
    return "Record missing approvals or resolve rejected/requested-change decisions; do not publish externally."


def _clean_text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _clean_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean_text(item) for item in value if _clean_text(item)]


def payload_to_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
