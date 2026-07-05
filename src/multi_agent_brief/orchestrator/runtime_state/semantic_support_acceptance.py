"""Human adjudication records for Semantic Support Auditor proposals."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.orchestrator.runtime_state._io import (
    _read_json_if_exists,
    _read_state_bytes,
    _restore_state_bytes,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ARTIFACT_INVALID,
    E_RUNTIME_STATE_NOT_INITIALIZED,
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import append_event, read_event_log_records_strict
from multi_agent_brief.orchestrator.runtime_state.identity import _validate_runtime_run_id, utc_now
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.paths import runtime_state_paths
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    build_semantic_assessment_checked_inputs,
    project_semantic_assessment_report_from_workspace,
)


SEMANTIC_SUPPORT_ACCEPTANCE_LEDGER_SCHEMA = "briefloop.semantic_support_acceptance_ledger.v1"
SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY = (
    "human_semantic_support_adjudication_records_only_not_gate_or_release_authority"
)
SEMANTIC_SUPPORT_ACCEPTANCE_DECISIONS = {"accept", "reject"}
SEMANTIC_SUPPORT_ACCEPTANCE_FORBIDDEN_AUTHORITY_KEYS = {
    "accepted_support_truth",
    "approve_delivery",
    "block_delivery",
    "block_release",
    "delivery_approval",
    "gate_decision",
    "repair_route",
    "release_authority",
    "semantic_truth_proof",
    "writes_claim_support_matrix",
    "writes_gate_report",
    "writes_workflow_state",
}


def semantic_support_acceptance_ledger_path(workspace: str | Path) -> Path:
    return (
        Path(workspace).expanduser().resolve()
        / "output"
        / "intermediate"
        / "semantic_support_acceptance_ledger.json"
    )


def record_semantic_support_adjudication(
    *,
    workspace: str | Path,
    proposal_id: str,
    decision: str,
    reason: str,
    actor_id: str = "human",
) -> dict[str, Any]:
    """Record a human accept/reject decision for one semantic support proposal.

    The transaction writes only ``semantic_support_acceptance_ledger.json`` and
    an event-log record. It does not mutate the Semantic Assessment Report,
    Claim-Support Matrix, gate reports, workflow state, repair routing,
    delivery state, or release state.
    """

    ws = Path(workspace).expanduser().resolve()
    run_id = _current_run_id(ws)
    clean_proposal_id = _require_text(proposal_id, field="proposal_id")
    clean_decision = _require_decision(decision)
    clean_reason = _require_text(reason, field="reason")
    clean_actor = _clean_text(actor_id) or "human"
    projection = project_semantic_assessment_report_from_workspace(ws)
    ledger_path = semantic_support_acceptance_ledger_path(ws)
    paths = runtime_state_paths(ws)
    report_path = ws / "output" / "intermediate" / "semantic_assessment_report.json"
    snapshots = {
        report_path: _read_state_bytes(report_path),
        ledger_path: _read_state_bytes(ledger_path),
        paths["event_log"]: _read_state_bytes(paths["event_log"]),
    }
    try:
        projection = _ensure_checked_inputs_for_adjudication(
            ws,
            projection=projection,
            report_path=report_path,
        )
        result = _record_semantic_support_adjudication_with_projection(
            ws=ws,
            run_id=run_id,
            projection=projection,
            proposal_id=clean_proposal_id,
            decision=clean_decision,
            reason=clean_reason,
            actor_id=clean_actor,
            ledger_path=ledger_path,
        )
        return result
    except Exception:
        _restore_paths(snapshots)
        raise


def _record_semantic_support_adjudication_with_projection(
    *,
    ws: Path,
    run_id: str,
    projection: Mapping[str, Any],
    proposal_id: str,
    decision: str,
    reason: str,
    actor_id: str,
    ledger_path: Path,
) -> dict[str, Any]:
    if projection.get("status") != "valid" or projection.get("checked_inputs_status") != "fresh":
        raise RuntimeStateError(
            "semantic_assessment_report.json must be present, valid, and checked-input fresh before human adjudication.",
            details={
                "status": projection.get("status"),
                "reason": projection.get("reason"),
                "checked_inputs_status": projection.get("checked_inputs_status"),
            },
            error_code=E_ARTIFACT_INVALID,
        )
    proposal = _proposal_from_projection(projection, proposal_id)
    report_sha256 = _require_text(projection.get("report_sha256"), field="semantic_assessment_report_sha256")
    checked_inputs_digest = _require_text(projection.get("checked_inputs_digest"), field="checked_inputs_digest")
    _validated_event_log_for_current_run(ws, run_id=run_id)
    ledger = _load_or_new_ledger(ledger_path)
    validation_reason = validate_semantic_support_acceptance_ledger_for_workspace(ledger, artifact_path=ledger_path)
    if validation_reason:
        raise RuntimeStateError(
            f"semantic_support_acceptance_ledger invalid: {validation_reason}",
            details={"path": str(ledger_path), "reason": validation_reason},
            error_code=E_ARTIFACT_INVALID,
        )
    now = utc_now()
    acceptance_id = f"SSA-{uuid.uuid4().hex[:12]}"
    event = append_event(
        workspace=ws,
        run_id=run_id,
        event_type="semantic_support_finding_adjudicated",
        actor="cli",
        artifact_id="semantic_support_acceptance_ledger",
        decision=decision,
        reason=f"Recorded human {decision} decision for semantic proposal {proposal_id}.",
        metadata={
            "acceptance_id": acceptance_id,
            "proposal_id": proposal_id,
            "source_row_id": _clean_text(proposal.get("source_row_id")),
            "claim_id": _clean_text(proposal.get("claim_id")),
            "atom_id": _clean_text(proposal.get("atom_id")),
            "calibration_label": _clean_text(proposal.get("calibration_label")),
            "actor_id_present": bool(actor_id),
            "reason_present": bool(reason),
            "boundary": SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY,
            "semantic_assessment_report_sha256": report_sha256,
            "checked_inputs_digest": checked_inputs_digest,
        },
    )
    record = _acceptance_record(
        acceptance_id=acceptance_id,
        run_id=run_id,
        proposal=proposal,
        semantic_assessment_report_sha256=report_sha256,
        checked_inputs_digest=checked_inputs_digest,
        decision=decision,
        reason=reason,
        actor_id=actor_id,
        recorded_at=now,
        event_id=str(event["event_id"]),
    )
    ledger.setdefault("records", []).append(record)
    ledger["updated_at"] = now
    _write_json_atomic(ledger_path, ledger)
    return {
        "ok": True,
        "schema_version": "briefloop.semantic_support_adjudication_result.v1",
        "boundary": SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY,
        "ledger_path": "output/intermediate/semantic_support_acceptance_ledger.json",
        "acceptance_id": acceptance_id,
        "proposal_id": proposal_id,
        "decision": decision,
        "event_id": event["event_id"],
        "authority_effects": _no_authority_effects(),
    }


def _ensure_checked_inputs_for_adjudication(
    workspace: Path,
    *,
    projection: Mapping[str, Any],
    report_path: Path,
) -> Mapping[str, Any]:
    """Bind legacy auditor-produced SARs to current checked inputs.

    Auditor-produced reports that omit ``checked_inputs`` remain valid advisory
    artifacts. Human adjudication needs an exact input binding, so the
    deterministic transaction adds the binding only when the field is absent.
    Declared but stale/incomplete bindings are never overwritten here.
    """

    if projection.get("status") == "valid" and projection.get("checked_inputs_status") == "fresh":
        return projection
    if projection.get("status") != "valid" or projection.get("checked_inputs_status") != "missing_checked_inputs":
        return projection
    report = _read_json_if_exists(report_path)
    if not isinstance(report, dict):
        return projection
    if "checked_inputs" in report and report.get("checked_inputs") is not None:
        return projection
    try:
        report["checked_inputs"] = build_semantic_assessment_checked_inputs(workspace)
    except FileNotFoundError as exc:
        raise RuntimeStateError(
            "semantic_assessment_report.json cannot be bound to checked inputs because a required input is missing.",
            details={"missing_input": str(exc)},
            error_code=E_ARTIFACT_INVALID,
        ) from exc
    _write_json_atomic(report_path, report)
    return project_semantic_assessment_report_from_workspace(workspace)


def validate_semantic_support_acceptance_ledger_payload(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return "not_object"
    if payload.get("schema_version") != SEMANTIC_SUPPORT_ACCEPTANCE_LEDGER_SCHEMA:
        return "schema_version"
    if payload.get("boundary") != SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY:
        return "boundary"
    for key in SEMANTIC_SUPPORT_ACCEPTANCE_FORBIDDEN_AUTHORITY_KEYS:
        if payload.get(key) not in (None, False, [], {}, ""):
            return f"forbidden_authority_key:{key}"
    effects_reason = _authority_effects_error(payload.get("authority_effects"))
    if effects_reason:
        return f"authority_effects.{effects_reason}"
    records = payload.get("records")
    if not isinstance(records, list):
        return "records"
    for idx, record in enumerate(records):
        reason = _validate_acceptance_record(record)
        if reason:
            return f"records[{idx}].{reason}"
    return None


def validate_semantic_support_acceptance_ledger_for_workspace(
    payload: Any,
    *,
    artifact_path: str | Path,
) -> str | None:
    reason = validate_semantic_support_acceptance_ledger_payload(payload)
    if reason:
        return reason
    assert isinstance(payload, Mapping)
    artifact = Path(artifact_path).expanduser().resolve()
    workspace = _workspace_from_ledger_path(artifact)
    proposal_ids, reason = _workspace_proposal_ids(workspace)
    if reason:
        return reason
    try:
        events = read_event_log_records_strict(runtime_state_paths(workspace)["event_log"])
    except RuntimeStateError as exc:
        return f"event_log_invalid:{exc.error_code or 'read_error'}"
    event_index = {
        _clean_text(event.get("event_id")): event
        for event in events
        if _clean_text(event.get("event_id"))
    }
    records = payload.get("records")
    for idx, record in enumerate(records if isinstance(records, list) else []):
        if not isinstance(record, Mapping):
            return f"records[{idx}].not_object"
        proposal_id = _clean_text(record.get("proposal_id"))
        if proposal_id not in proposal_ids:
            return f"records[{idx}].proposal_missing:{proposal_id}"
        reason = _event_link_error(record, event_index)
        if reason:
            return f"records[{idx}].{reason}"
    return None


def semantic_support_acceptance_record_current_effectiveness(
    record: Mapping[str, Any],
    *,
    workspace: str | Path,
) -> dict[str, Any]:
    """Return whether a historical adjudication still matches current SAR inputs."""

    ws = Path(workspace).expanduser().resolve()
    projection = project_semantic_assessment_report_from_workspace(ws)
    if projection.get("status") != "valid" or projection.get("checked_inputs_status") != "fresh":
        return {
            "current_effective": False,
            "reason": _clean_text(projection.get("reason"))
            or _clean_text(projection.get("checked_inputs_status"))
            or _clean_text(projection.get("status"))
            or "semantic_assessment_report_not_current",
        }
    record_report_sha = _clean_text(record.get("semantic_assessment_report_sha256"))
    if not record_report_sha:
        return {"current_effective": False, "reason": "record_missing_semantic_assessment_report_sha256"}
    if record_report_sha != _clean_text(projection.get("report_sha256")):
        return {"current_effective": False, "reason": "semantic_assessment_report_sha256_changed"}
    record_digest = _clean_text(record.get("checked_inputs_digest"))
    if not record_digest:
        return {"current_effective": False, "reason": "record_missing_checked_inputs_digest"}
    if record_digest != _clean_text(projection.get("checked_inputs_digest")):
        return {"current_effective": False, "reason": "checked_inputs_digest_changed"}
    proposals = projection.get("proposed_claim_support_rows")
    proposal_ids: set[str] = set()
    for proposal in proposals if isinstance(proposals, list) else []:
        if isinstance(proposal, Mapping):
            proposal_ids.add(_clean_text(proposal.get("proposal_id")))
    proposal_id = _clean_text(record.get("proposal_id"))
    if proposal_id not in proposal_ids:
        return {"current_effective": False, "reason": f"proposal_missing:{proposal_id}"}
    return {"current_effective": True, "reason": None}


def _validate_acceptance_record(record: Any) -> str | None:
    if not isinstance(record, Mapping):
        return "not_object"
    for key in SEMANTIC_SUPPORT_ACCEPTANCE_FORBIDDEN_AUTHORITY_KEYS:
        if record.get(key) not in (None, False, [], {}, ""):
            return f"forbidden_authority_key:{key}"
    for field in (
        "acceptance_id",
        "run_id",
        "proposal_id",
        "decision",
        "reason",
        "actor_id",
        "recorded_at",
        "event_id",
    ):
        if not _clean_text(record.get(field)):
            return field
    for field in ("semantic_assessment_report_sha256", "checked_inputs_digest"):
        if field in record and not _clean_text(record.get(field)):
            return field
    if record.get("decision") not in SEMANTIC_SUPPORT_ACCEPTANCE_DECISIONS:
        return "decision"
    if record.get("boundary") != SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY:
        return "boundary"
    effects = record.get("authority_effects")
    effects_reason = _authority_effects_error(effects)
    if effects_reason:
        return f"authority_effects.{effects_reason}"
    return None


def _authority_effects_error(effects: Any) -> str | None:
    if not isinstance(effects, Mapping):
        return "not_object"
    allowed = set(_no_authority_effects())
    for key, value in effects.items():
        if key not in allowed:
            return f"unknown_key:{key}"
        if value is not False:
            return str(key)
    for key in allowed:
        if effects.get(key) is not False:
            return str(key)
    return None


def _validated_event_log_for_current_run(workspace: Path, *, run_id: str) -> list[Mapping[str, Any]]:
    event_path = runtime_state_paths(workspace)["event_log"]
    if not event_path.exists():
        raise RuntimeStateError(
            "event_log.jsonl is required before semantic support adjudication.",
            details={"path": str(event_path)},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    events = read_event_log_records_strict(event_path)
    if not any(
        _clean_text(event.get("run_id")) == run_id
        and _clean_text(event.get("event_type")) in {"run_initialized", "run_reset"}
        for event in events
    ):
        raise RuntimeStateError(
            "event_log.jsonl does not contain a current-run initialization event.",
            details={"path": str(event_path), "run_id": run_id},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return events


def _workspace_from_ledger_path(path: Path) -> Path:
    # Expected path: <workspace>/output/intermediate/semantic_support_acceptance_ledger.json
    if path.parent.name == "intermediate" and path.parent.parent.name == "output":
        return path.parent.parent.parent
    return path.parent


def _workspace_proposal_ids(workspace: Path) -> tuple[set[str], str | None]:
    projection = project_semantic_assessment_report_from_workspace(workspace)
    if projection.get("status") in {"not_available", "invalid_report"}:
        reason = _clean_text(projection.get("reason")) or _clean_text(projection.get("status")) or "invalid_report"
        return set(), f"semantic_assessment_report_invalid:{reason}"
    proposals = projection.get("proposed_claim_support_rows")
    proposal_ids: set[str] = set()
    for proposal in proposals if isinstance(proposals, list) else []:
        if not isinstance(proposal, Mapping):
            continue
        proposal_id = _clean_text(proposal.get("proposal_id"))
        if proposal_id:
            proposal_ids.add(proposal_id)
    return proposal_ids, None


def _event_link_error(record: Mapping[str, Any], event_index: Mapping[str, Mapping[str, Any]]) -> str | None:
    event_id = _clean_text(record.get("event_id"))
    event = event_index.get(event_id)
    if event is None:
        return f"event_missing:{event_id}"
    if _clean_text(event.get("event_type")) != "semantic_support_finding_adjudicated":
        return "event_type_mismatch"
    if _clean_text(event.get("artifact_id")) != "semantic_support_acceptance_ledger":
        return "event_artifact_mismatch"
    if _clean_text(event.get("run_id")) != _clean_text(record.get("run_id")):
        return "event_run_id_mismatch"
    if _clean_text(event.get("decision")) != _clean_text(record.get("decision")):
        return "event_decision_mismatch"
    metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
    for field in ("acceptance_id", "proposal_id", "boundary"):
        if _clean_text(metadata.get(field)) != _clean_text(record.get(field)):
            return f"event_metadata_mismatch:{field}"
    for field in ("semantic_assessment_report_sha256", "checked_inputs_digest"):
        record_value = _clean_text(record.get(field))
        metadata_value = _clean_text(metadata.get(field))
        if (record_value or metadata_value) and metadata_value != record_value:
            return f"event_metadata_mismatch:{field}"
    return None


def _current_run_id(workspace: Path) -> str:
    paths = runtime_state_paths(workspace)
    manifest = _read_json_if_exists(paths["runtime_manifest"])
    workflow = _read_json_if_exists(paths["workflow_state"])
    if manifest is None or workflow is None:
        raise RuntimeStateError(
            "Runtime state is not initialized. Run `multi-agent-brief state init --workspace <workspace>` first.",
            details={"workspace": str(workspace)},
            error_code=E_RUNTIME_STATE_NOT_INITIALIZED,
        )
    if manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise RuntimeStateError(
            "runtime_manifest.json has an unsupported schema.",
            details={"path": str(paths["runtime_manifest"]), "schema_version": manifest.get("schema_version")},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    run_id = _validate_runtime_run_id(manifest.get("run_id"), path=paths["runtime_manifest"])
    if workflow.get("run_id") and _clean_text(workflow.get("run_id")) != run_id:
        raise RuntimeStateError(
            "workflow_state.json run_id does not match runtime_manifest.json.",
            details={"manifest_run_id": run_id, "workflow_run_id": workflow.get("run_id")},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return run_id


def _proposal_for_workspace(workspace: Path, proposal_id: str) -> Mapping[str, Any]:
    projection = project_semantic_assessment_report_from_workspace(workspace)
    if projection.get("status") != "valid":
        raise RuntimeStateError(
            "semantic_assessment_report.json must be present and valid before human adjudication.",
            details={
                "status": projection.get("status"),
                "reason": projection.get("reason"),
            },
            error_code=E_ARTIFACT_INVALID,
        )
    return _proposal_from_projection(projection, proposal_id)


def _proposal_from_projection(projection: Mapping[str, Any], proposal_id: str) -> Mapping[str, Any]:
    proposals = projection.get("proposed_claim_support_rows")
    for proposal in proposals if isinstance(proposals, list) else []:
        if isinstance(proposal, Mapping) and _clean_text(proposal.get("proposal_id")) == proposal_id:
            return proposal
    raise RuntimeStateError(
        f"Semantic support proposal not found: {proposal_id}",
        details={"proposal_id": proposal_id},
        error_code=E_ARTIFACT_INVALID,
    )


def _load_or_new_ledger(path: Path) -> dict[str, Any]:
    payload = _read_json_if_exists(path)
    if payload is None:
        return _new_ledger()
    return payload


def _new_ledger() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": SEMANTIC_SUPPORT_ACCEPTANCE_LEDGER_SCHEMA,
        "boundary": SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY,
        "created_at": now,
        "updated_at": now,
        "records": [],
        "authority_effects": _no_authority_effects(),
    }


def _acceptance_record(
    *,
    acceptance_id: str,
    run_id: str,
    proposal: Mapping[str, Any],
    semantic_assessment_report_sha256: str,
    checked_inputs_digest: str,
    decision: str,
    reason: str,
    actor_id: str,
    recorded_at: str,
    event_id: str,
) -> dict[str, Any]:
    return {
        "acceptance_id": acceptance_id,
        "run_id": run_id,
        "proposal_id": _clean_text(proposal.get("proposal_id")),
        "source_row_id": _clean_text(proposal.get("source_row_id")),
        "claim_id": _clean_text(proposal.get("claim_id")),
        "atom_id": _clean_text(proposal.get("atom_id")),
        "evidence_span_id": _clean_text(proposal.get("evidence_span_id")),
        "calibration_label": _clean_text(proposal.get("calibration_label")),
        "proposed_support_label": _clean_text(proposal.get("proposed_support_label")),
        "semantic_assessment_report_sha256": semantic_assessment_report_sha256,
        "checked_inputs_digest": checked_inputs_digest,
        "decision": decision,
        "reason": reason,
        "actor_id": actor_id,
        "recorded_at": recorded_at,
        "event_id": event_id,
        "boundary": SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY,
        "authority_effects": _no_authority_effects(),
    }


def _no_authority_effects() -> dict[str, bool]:
    return {
        "writes_claim_support_matrix": False,
        "writes_gate_report": False,
        "writes_workflow_state": False,
        "creates_repair_route": False,
        "approves_delivery": False,
        "authorizes_release": False,
    }


def _restore_paths(snapshots: Mapping[Path, bytes | None]) -> None:
    rollback_errors: list[dict[str, str]] = []
    for path, data in snapshots.items():
        try:
            _restore_state_bytes(path, data)
        except RuntimeStateError as exc:
            rollback_errors.append({"path": str(path), "reason": str(exc)})
    if rollback_errors:
        raise RuntimeStateError(
            "Semantic support adjudication rollback failed after partial write.",
            details={"rollback_errors": rollback_errors},
            error_code=E_TRANSACTION_INTEGRITY,
        )


def _require_decision(value: Any) -> str:
    text = _require_text(value, field="decision")
    if text not in SEMANTIC_SUPPORT_ACCEPTANCE_DECISIONS:
        raise RuntimeStateError(
            f"Unsupported semantic support adjudication decision: {text}",
            details={"decision": text, "allowed_values": sorted(SEMANTIC_SUPPORT_ACCEPTANCE_DECISIONS)},
            error_code=E_ARTIFACT_INVALID,
        )
    return text


def _require_text(value: Any, *, field: str) -> str:
    text = _clean_text(value)
    if not text:
        raise RuntimeStateError(
            f"semantic support adjudication requires {field}.",
            details={"field": field},
            error_code=E_ARTIFACT_INVALID,
        )
    return text


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
