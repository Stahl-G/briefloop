"""Read-only WorkBuddy diagnostic Run Card projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.orchestrator.runtime_state import build_completion_projection


DIAGNOSE_SCHEMA_VERSION = "briefloop.workbuddy_diagnose.v1"


def build_workbuddy_diagnosis(*, workspace: str | Path) -> dict[str, Any]:
    """Build a read-only WorkBuddy diagnosis payload for a workspace."""

    ws = Path(workspace).expanduser().resolve()
    completion = build_completion_projection(workspace=ws)
    doctor_payload = _doctor_payload(config_exists=(ws / "config.yaml").exists())
    secret_risk = _secret_risk_payload(ws)
    run_card = _run_card_from_completion(
        completion,
        doctor=doctor_payload,
        secret_risk=secret_risk,
    )

    return {
        "ok": True,
        "schema_version": DIAGNOSE_SCHEMA_VERSION,
        "runtime_effect": "read_only_diagnostic",
        "workspace": str(ws),
        "run_card": run_card,
        "doctor": doctor_payload,
        "completion_projection": completion,
        "runtime": _runtime_payload_from_completion(completion),
        "assessment_target": _mapping(completion.get("assessment_target")),
        "workflow": _workflow_payload_from_completion(completion),
        "control_files": _mapping(completion.get("control_files")),
        "artifacts": _mapping(completion.get("artifacts")),
        "finalize": _finalize_payload_from_completion(completion),
        "delivery": _delivery_payload_from_completion(completion),
        "recovery_truth": _mapping(completion.get("recovery_truth")),
        "delivery_truth": _mapping(completion.get("delivery_truth")),
        "event_truth": _mapping(completion.get("event_truth")),
        "secret_risk": secret_risk,
        "boundary": (
            "read_only_workbuddy_run_card_formats_completion_projection_with_workbuddy_safety_overlay; "
            "not_gate_delivery_release_or_semantic_proof"
        ),
    }


def format_workbuddy_diagnosis(payload: Mapping[str, Any]) -> str:
    """Format a diagnosis payload without exposing secret values."""

    run_card = _mapping(payload.get("run_card"))
    lines = ["WorkBuddy diagnosis", "", "Run Card:"]
    for field in (
        "runtime",
        "current_stage",
        "assessment_target",
        "assessment_target_status",
        "run_integrity",
        "recovery_truth",
        "blocked",
        "latest_gate_status",
        "finalize_report",
        "delivery_truth",
        "finalize_event",
        "delivery_event",
        "share_workspace_zip_allowed",
        "next_allowed_action",
    ):
        lines.append(f"  {field}: {run_card.get(field, 'unknown')}")
    doctor = _mapping(payload.get("doctor"))
    lines.extend([
        "",
        f"Doctor: {doctor.get('status', 'unknown')}",
        f"Workspace: {payload.get('workspace', '')}",
    ])
    artifacts = _mapping(payload.get("artifacts"))
    invalid = artifacts.get("invalid_or_stale") if isinstance(artifacts.get("invalid_or_stale"), list) else []
    if invalid:
        lines.append("Invalid/stale artifacts:")
        for item in invalid:
            if isinstance(item, Mapping):
                lines.append(
                    f"  - {item.get('artifact_id')}: {item.get('status')} ({item.get('validation_result')})"
                )
    secret = _mapping(payload.get("secret_risk"))
    if secret.get("env_present") or secret.get("nonempty_env_keys"):
        lines.append("")
        lines.append("Secret risk: .env is present; values are not displayed.")
    lines.append("")
    lines.append(str(payload.get("boundary", "")))
    return "\n".join(lines)


def _run_card_from_completion(
    completion: Mapping[str, Any],
    *,
    doctor: Mapping[str, Any],
    secret_risk: Mapping[str, Any],
) -> dict[str, Any]:
    workflow = _mapping(completion.get("workflow"))
    runtime = _mapping(completion.get("runtime"))
    gate_truth = _mapping(completion.get("gate_truth"))
    finalize_truth = _mapping(completion.get("finalize_truth"))
    delivery_truth = _mapping(completion.get("delivery_truth"))
    event_truth = _mapping(completion.get("event_truth"))
    assessment_target = _mapping(completion.get("assessment_target"))
    run_integrity = _mapping(completion.get("run_integrity"))
    recovery_truth = _mapping(completion.get("recovery_truth"))
    return {
        "runtime": _clean_text(runtime.get("runtime")) or "unknown",
        "current_stage": _clean_text(workflow.get("current_stage")) or "unknown",
        "assessment_target": _clean_text(assessment_target.get("assessment_target")) or "not_applicable",
        "assessment_target_status": _clean_text(assessment_target.get("status")) or "not_applicable",
        "run_integrity": _clean_text(run_integrity.get("status")) or "unknown",
        "recovery_truth": _clean_text(recovery_truth.get("status")) or "none",
        "recovery_finalize_allowed": recovery_truth.get("finalize_allowed") is True,
        "recovery_delivery_allowed": recovery_truth.get("delivery_allowed") is True,
        "blocked": bool(workflow.get("blocked")),
        "latest_gate_status": _gate_status_text(gate_truth),
        "finalize_report": _clean_text(finalize_truth.get("status")) or "unknown",
        "delivery_truth": _clean_text(delivery_truth.get("status")) or "unknown",
        "delivery_valid": delivery_truth.get("valid") is True,
        "finalize_event": "present" if event_truth.get("finalize_event_present") is True else "missing",
        "delivery_event": "present" if event_truth.get("delivery_event_present") is True else "missing",
        "share_workspace_zip_allowed": False,
        "next_allowed_action": _workbuddy_next_allowed_action(
            completion_next_allowed_action=_clean_text(completion.get("next_allowed_action")) or "unknown",
            doctor=doctor,
            secret_risk=secret_risk,
        ),
        "secret_risk_present": bool(secret_risk.get("env_present") or secret_risk.get("nonempty_env_keys")),
    }


def _workbuddy_next_allowed_action(
    *,
    completion_next_allowed_action: str,
    doctor: Mapping[str, Any],
    secret_risk: Mapping[str, Any],
) -> str:
    """Apply WorkBuddy-only safety stops around canonical completion action.

    Completion projection owns control/finalize/delivery truth. WorkBuddy adds
    only outer operational stops that the completion projection intentionally
    does not know about: doctor errors and workspace-sharing secret risk.
    """

    if _clean_text(doctor.get("status")) == "error":
        return "stop_show_full_doctor_output"
    if (
        completion_next_allowed_action == "inspect_status_before_delivery_or_quality"
        and (secret_risk.get("env_present") or secret_risk.get("nonempty_env_keys"))
    ):
        return "do_not_share_workspace_zip_secret_risk"
    return completion_next_allowed_action


def _runtime_payload_from_completion(completion: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _mapping(completion.get("runtime"))
    control = _mapping(completion.get("control_files"))
    return {
        "runtime": _clean_text(runtime.get("runtime")) or "unknown",
        "manifest_present": control.get("runtime_manifest") == "present",
        "manifest_status": _clean_text(runtime.get("status")) or _clean_text(control.get("runtime_manifest")),
        "runtime_capabilities": runtime.get("runtime_capabilities"),
    }


def _workflow_payload_from_completion(completion: Mapping[str, Any]) -> dict[str, Any]:
    workflow = _mapping(completion.get("workflow"))
    control = _mapping(completion.get("control_files"))
    return {
        "workflow_state_present": control.get("workflow_state") == "present",
        "workflow_state_status": _clean_text(control.get("workflow_state")) or "unknown",
        "current_stage": _clean_text(workflow.get("current_stage")) or "unknown",
        "blocked": bool(workflow.get("blocked")),
        "blocking_reason": _clean_text(workflow.get("blocking_reason")),
        "active_repair_present": bool(workflow.get("active_repair_present")),
        "run_integrity": _mapping(completion.get("run_integrity")),
    }


def _finalize_payload_from_completion(completion: Mapping[str, Any]) -> dict[str, Any]:
    truth = _mapping(completion.get("finalize_truth"))
    event_truth = _mapping(completion.get("event_truth"))
    return {
        "path": truth.get("path") or "output/intermediate/finalize_report.json",
        "exists": truth.get("status") == "present",
        "event_present": event_truth.get("finalize_event_present") is True,
        "truth": truth,
    }


def _delivery_payload_from_completion(completion: Mapping[str, Any]) -> dict[str, Any]:
    truth = _mapping(completion.get("delivery_truth"))
    event_truth = _mapping(completion.get("event_truth"))
    return {
        "path": "output/delivery",
        "exists": truth.get("valid") is True,
        "valid": truth.get("valid") is True,
        "event_present": event_truth.get("delivery_event_present") is True,
        "truth": truth,
    }


def _gate_status_text(gate_truth: Mapping[str, Any]) -> str:
    artifact_id = _clean_text(gate_truth.get("artifact_id"))
    if not artifact_id:
        return "unknown"
    status = _clean_text(gate_truth.get("status")) or "unknown"
    blocking_count = gate_truth.get("blocking_count")
    if not isinstance(blocking_count, int):
        blocking_count = 0
    return f"{artifact_id}:{status}:blocking_findings={blocking_count}"


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


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
