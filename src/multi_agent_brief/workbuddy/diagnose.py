"""Read-only WorkBuddy diagnostic Run Card projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.orchestrator.run_integrity import interpret_run_integrity, project_for_read
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.event_log import read_event_log_records_strict
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.workflow import WORKFLOW_STATE_SCHEMA
from multi_agent_brief.quality_gates.contract import validate_quality_gate_report_payload


DIAGNOSE_SCHEMA_VERSION = "briefloop.workbuddy_diagnose.v1"


def build_workbuddy_diagnosis(*, workspace: str | Path) -> dict[str, Any]:
    """Build a read-only WorkBuddy diagnosis payload for a workspace."""

    ws = Path(workspace).expanduser().resolve()
    config_path = ws / "config.yaml"
    intermediate = ws / "output" / "intermediate"
    workflow, workflow_status = _read_json_with_schema(
        intermediate / "workflow_state.json",
        expected_schema=WORKFLOW_STATE_SCHEMA,
    )
    manifest, manifest_status = _read_json_with_schema(
        intermediate / "runtime_manifest.json",
        expected_schema=RUNTIME_MANIFEST_SCHEMA,
    )
    registry, registry_status = _read_json(intermediate / "artifact_registry.json")
    event_records, event_log_status = _read_event_log(intermediate / "event_log.jsonl")

    doctor_payload = _doctor_payload(config_exists=config_path.exists())
    artifact_payload = _artifact_payload(registry)
    finalize_path = intermediate / "finalize_report.json"
    delivery_dir = ws / "output" / "delivery"
    control_file_status = {
        "workflow_state": workflow_status,
        "runtime_manifest": manifest_status,
        "artifact_registry": registry_status,
        "event_log": event_log_status,
    }
    finalize_event = _has_finalize_event(event_records)
    delivery_event = _has_event(
        event_records,
        {"delivery_completed", "delivery_recorded", "delivery_draft_created", "delivery_succeeded"},
    )
    secret_risk = _secret_risk_payload(ws)
    run_integrity = _run_integrity_projection(workflow, workflow_status)
    active_repair = workflow.get("active_repair") if isinstance(workflow, Mapping) else None
    blocked = bool(workflow.get("blocked")) if isinstance(workflow, Mapping) else False
    blocking_reason = _clean_text(workflow.get("blocking_reason")) if isinstance(workflow, Mapping) else ""
    current_stage = _clean_text(workflow.get("current_stage")) if isinstance(workflow, Mapping) else "unknown"
    runtime = _clean_text(manifest.get("runtime")) if isinstance(manifest, Mapping) else "unknown"
    latest_gate = _latest_gate_projection(intermediate, current_stage=current_stage)
    latest_gate_status = latest_gate["status_text"]

    run_card = {
        "runtime": runtime or "unknown",
        "current_stage": current_stage or "unknown",
        "run_integrity": _run_integrity_status(run_integrity),
        "blocked": blocked,
        "latest_gate_status": latest_gate_status,
        "finalize_report": "present" if finalize_path.exists() else "missing",
        "delivery_dir": "present" if delivery_dir.is_dir() else "missing",
        "finalize_event": "present" if finalize_event else "missing",
        "delivery_event": "present" if delivery_event else "missing",
        "share_workspace_zip_allowed": False,
        "next_allowed_action": _next_safe_action(
            doctor=doctor_payload,
            control_file_status=control_file_status,
            current_stage=current_stage,
            blocked=blocked,
            run_integrity=run_integrity,
            active_repair=active_repair,
            latest_gate=latest_gate,
            invalid_or_stale_artifacts=artifact_payload["invalid_or_stale"],
            finalize_report_exists=finalize_path.exists(),
            delivery_dir_exists=delivery_dir.is_dir(),
            finalize_event_exists=finalize_event,
            delivery_event_exists=delivery_event,
            secret_risk=secret_risk,
        ),
    }

    return {
        "ok": True,
        "schema_version": DIAGNOSE_SCHEMA_VERSION,
        "runtime_effect": "read_only_diagnostic",
        "workspace": str(ws),
        "run_card": run_card,
        "doctor": doctor_payload,
        "runtime": {
            "runtime": run_card["runtime"],
            "manifest_present": isinstance(manifest, Mapping),
            "manifest_status": manifest_status,
            "runtime_capabilities": manifest.get("runtime_capabilities") if isinstance(manifest, Mapping) else None,
        },
        "workflow": {
            "workflow_state_present": isinstance(workflow, Mapping),
            "workflow_state_status": workflow_status,
            "current_stage": current_stage,
            "blocked": blocked,
            "blocking_reason": blocking_reason,
            "active_repair_present": bool(active_repair),
            "run_integrity": run_integrity,
        },
        "control_files": control_file_status,
        "artifacts": artifact_payload,
        "finalize": {
            "path": "output/intermediate/finalize_report.json",
            "exists": finalize_path.exists(),
            "event_present": finalize_event,
        },
        "delivery": {
            "path": "output/delivery",
            "exists": delivery_dir.is_dir(),
            "event_present": delivery_event,
        },
        "secret_risk": secret_risk,
        "boundary": (
            "read_only_workbuddy_run_card_not_gate_delivery_release_or_semantic_proof"
        ),
    }


def format_workbuddy_diagnosis(payload: Mapping[str, Any]) -> str:
    """Format a diagnosis payload without exposing secret values."""

    run_card = payload.get("run_card") if isinstance(payload.get("run_card"), Mapping) else {}
    lines = ["WorkBuddy diagnosis", "", "Run Card:"]
    for field in (
        "runtime",
        "current_stage",
        "run_integrity",
        "blocked",
        "latest_gate_status",
        "finalize_report",
        "delivery_dir",
        "finalize_event",
        "delivery_event",
        "share_workspace_zip_allowed",
        "next_allowed_action",
    ):
        lines.append(f"  {field}: {run_card.get(field, 'unknown')}")
    doctor = payload.get("doctor") if isinstance(payload.get("doctor"), Mapping) else {}
    lines.extend([
        "",
        f"Doctor: {doctor.get('status', 'unknown')}",
        f"Workspace: {payload.get('workspace', '')}",
    ])
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), Mapping) else {}
    invalid = artifacts.get("invalid_or_stale") if isinstance(artifacts.get("invalid_or_stale"), list) else []
    if invalid:
        lines.append("Invalid/stale artifacts:")
        for item in invalid:
            if isinstance(item, Mapping):
                lines.append(
                    f"  - {item.get('artifact_id')}: {item.get('status')} ({item.get('validation_result')})"
                )
    secret = payload.get("secret_risk") if isinstance(payload.get("secret_risk"), Mapping) else {}
    if secret.get("env_present") or secret.get("nonempty_env_keys"):
        lines.append("")
        lines.append("Secret risk: .env is present; values are not displayed.")
    lines.append("")
    lines.append(str(payload.get("boundary", "")))
    return "\n".join(lines)


def _doctor_payload(*, config_exists: bool) -> dict[str, Any]:
    if not config_exists:
        status = "error"
        errors = ["config.yaml missing"]
        warnings: list[str] = []
        report = "config.yaml missing"
    else:
        status = "not_run_read_only"
        errors = []
        warnings = [
            "briefloop workbuddy diagnose does not run doctor because it is a read-only diagnostic.",
        ]
        report = (
            "Doctor not run by WorkBuddy diagnosis. This command is read-only and "
            "does not perform output writability checks. Run `briefloop doctor "
            "--config <workspace>/config.yaml` separately when write-check evidence is needed."
        )
    return {
        "status": status,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "full_output": report,
    }


def _artifact_payload(registry: Any) -> dict[str, Any]:
    artifacts = registry.get("artifacts") if isinstance(registry, Mapping) else {}
    invalid_or_stale: list[dict[str, str]] = []
    expected: list[dict[str, str]] = []
    for artifact_id, entry in sorted(artifacts.items()) if isinstance(artifacts, Mapping) else []:
        if not isinstance(entry, Mapping):
            continue
        status = _clean_text(entry.get("status")) or "unknown"
        row = {
            "artifact_id": str(artifact_id),
            "status": status,
            "validation_result": _clean_text(entry.get("validation_result")),
        }
        if status in {"invalid", "stale", "missing", "blocked"}:
            invalid_or_stale.append(row)
        elif status == "expected":
            expected.append(row)
    return {
        "artifact_registry_present": isinstance(registry, Mapping),
        "invalid_or_stale": invalid_or_stale,
        "expected": expected,
    }


def _secret_risk_payload(workspace: Path) -> dict[str, Any]:
    env_path = workspace / ".env"
    nonempty_keys: list[str] = []
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if value.strip():
                    nonempty_keys.append(key.strip())
        except OSError:
            nonempty_keys.append("unreadable_env")
    return {
        "env_present": env_path.exists(),
        "nonempty_env_keys": sorted(set(nonempty_keys)),
        "secret_values_reported": False,
        "share_workspace_zip_allowed": False,
        "recommend_key_rotation_if_shared": bool(nonempty_keys),
    }


def _run_integrity_status(run_integrity: Any) -> str:
    if not isinstance(run_integrity, Mapping):
        return "unknown"
    return _clean_text(run_integrity.get("status")) or "unknown"


def _run_integrity_projection(workflow: Any, workflow_status: str) -> dict[str, Any]:
    if not isinstance(workflow, Mapping):
        return project_for_read(
            interpret_run_integrity(
                None,
                field_present=False,
                unavailable_reason={
                    "reason_code": f"workflow_state_{workflow_status}",
                    "message": "workflow_state.json is unavailable for WorkBuddy diagnosis.",
                },
            )
        )
    return project_for_read(
        interpret_run_integrity(
            workflow.get("run_integrity"),
            field_present="run_integrity" in workflow,
        )
    )


def _latest_gate_projection(intermediate: Path, *, current_stage: str) -> dict[str, Any]:
    scoped_reports = {
        "auditor_quality_gate_report": intermediate / "gates" / "auditor_quality_gate_report.json",
        "finalize_quality_gate_report": intermediate / "gates" / "finalize_quality_gate_report.json",
        "quality_gate_report": intermediate / "quality_gate_report.json",
    }
    if _clean_text(current_stage) in {"finalize", "delivery", "delivered"}:
        scan_order = (
            "finalize_quality_gate_report",
            "auditor_quality_gate_report",
            "quality_gate_report",
        )
    else:
        scan_order = (
            "auditor_quality_gate_report",
            "quality_gate_report",
            "finalize_quality_gate_report",
        )
    for artifact_id in scan_order:
        path = scoped_reports[artifact_id]
        payload, status = _read_json(path)
        if status == "missing":
            continue
        if status != "present" or not isinstance(payload, Mapping):
            return {
                "artifact_id": artifact_id,
                "gate_status": "unreadable",
                "blocking_count": 0,
                "status_text": f"{artifact_id}:unreadable:{status}",
            }
        errors = validate_quality_gate_report_payload(
            dict(payload),
            stages=_diagnose_gate_validation_stages(),
            artifacts=_diagnose_gate_validation_artifacts(),
        )
        if errors:
            return {
                "artifact_id": artifact_id,
                "gate_status": "invalid",
                "blocking_count": 1,
                "status_text": f"{artifact_id}:invalid:gate_report_invalid",
                "validation_errors": errors,
            }
        gate_status = _clean_text(payload.get("status")) or "unknown"
        findings = payload.get("findings")
        blocking_count = _blocking_gate_result_count(payload)
        if isinstance(findings, list):
            blocking_count += sum(1 for finding in findings if _is_blocking_gate_finding(finding))
        return {
            "artifact_id": artifact_id,
            "gate_status": gate_status,
            "blocking_count": blocking_count,
            "status_text": f"{artifact_id}:{gate_status}:blocking_findings={blocking_count}",
        }
    return {
        "artifact_id": "",
        "gate_status": "unknown",
        "blocking_count": 0,
        "status_text": "unknown",
    }


def _is_blocking_gate_finding(finding: Any) -> bool:
    if not isinstance(finding, Mapping):
        return False
    return finding.get("blocking") is True or _clean_text(finding.get("blocking_level")) == "blocking"


def _blocking_gate_result_count(payload: Mapping[str, Any]) -> int:
    gate_results = payload.get("gate_results")
    if not isinstance(gate_results, list):
        return 0
    return sum(
        1
        for result in gate_results
        if isinstance(result, Mapping)
        and (result.get("blocking") is True or _clean_text(result.get("status")) == "fail")
    )


def _diagnose_gate_validation_stages() -> list[dict[str, Any]]:
    return [
        {"stage_id": stage}
        for stage in (
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
            "auditor",
            "finalize",
        )
    ]


def _diagnose_gate_validation_artifacts() -> list[dict[str, Any]]:
    return [
        {"artifact_id": artifact}
        for artifact in (
            "candidate_claims",
            "screened_candidates",
            "claim_drafts",
            "claim_ledger",
            "analyst_draft_snapshot",
            "audited_brief",
            "audit_report",
            "reader_brief",
            "auditor_quality_gate_report",
            "finalize_quality_gate_report",
            "quality_gate_report",
        )
    ]


def _next_safe_action(
    *,
    doctor: Mapping[str, Any],
    control_file_status: Mapping[str, str],
    current_stage: str,
    blocked: bool,
    run_integrity: Any,
    active_repair: Any,
    latest_gate: Mapping[str, Any],
    invalid_or_stale_artifacts: list[Mapping[str, str]],
    finalize_report_exists: bool,
    delivery_dir_exists: bool,
    finalize_event_exists: bool,
    delivery_event_exists: bool,
    secret_risk: Mapping[str, Any],
) -> str:
    if doctor.get("status") == "error":
        return "stop_show_full_doctor_output"
    if any(
        status in {"unreadable_utf8", "invalid_json", "invalid_schema", "unreadable"}
        for status in control_file_status.values()
    ):
        return "inspect_unreadable_or_missing_control_files"
    if blocked:
        return "stop_workflow_blocked_human_review_required"
    if active_repair:
        return "stop_complete_or_inspect_active_repair"
    integrity = _run_integrity_status(run_integrity)
    if integrity not in {"clean", "pass", "ok"}:
        return "stop_run_integrity_not_clean"
    if int(latest_gate.get("blocking_count") or 0) > 0 or _clean_text(latest_gate.get("gate_status")) == "fail":
        return "stop_resolve_blocking_gate_report"
    if invalid_or_stale_artifacts:
        return "inspect_invalid_or_stale_artifacts"
    if not finalize_report_exists or not delivery_dir_exists:
        if _clean_text(current_stage) not in {"finalize", "delivery", "delivered"}:
            return "continue_current_stage_or_handoff_workflow"
        return "draft_only_run_finalize_when_allowed"
    if not finalize_event_exists or not delivery_event_exists:
        return "inspect_finalize_delivery_event_gap"
    if secret_risk.get("nonempty_env_keys") or secret_risk.get("env_present"):
        return "do_not_share_workspace_zip_secret_risk"
    return "inspect_status_before_delivery_or_quality"


def _read_json(path: Path) -> tuple[Any, str]:
    if not path.exists():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), "present"
    except UnicodeDecodeError:
        return None, "unreadable_utf8"
    except json.JSONDecodeError:
        return None, "invalid_json"
    except OSError:
        return None, "unreadable"


def _read_json_with_schema(path: Path, *, expected_schema: str) -> tuple[Any, str]:
    payload, status = _read_json(path)
    if status != "present" or not isinstance(payload, Mapping):
        return payload, status
    if payload.get("schema_version") != expected_schema:
        return payload, "invalid_schema"
    return payload, status


def _read_event_log(path: Path) -> tuple[list[Mapping[str, Any]], str]:
    if not path.exists():
        return [], "missing"
    try:
        records = read_event_log_records_strict(path)
    except UnicodeDecodeError:
        return [], "unreadable_utf8"
    except json.JSONDecodeError:
        return [], "invalid_json"
    except RuntimeStateError:
        return [], "invalid_json"
    except OSError:
        return [], "unreadable"
    return records, "present"


def _has_event(records: list[Mapping[str, Any]], event_types: set[str]) -> bool:
    for record in records:
        event_type = _clean_text(record.get("event_type"))
        if event_type in event_types:
            return True
    return False


def _has_finalize_event(records: list[Mapping[str, Any]]) -> bool:
    for record in records:
        event_type = _clean_text(record.get("event_type"))
        if event_type in {"finalize_completed", "finalize_complete"}:
            return True
        if (
            event_type == "decision_recorded"
            and _clean_text(record.get("decision")) == "finalize"
            and _clean_text(record.get("stage_id")) == "finalize"
        ):
            return True
    return False


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
