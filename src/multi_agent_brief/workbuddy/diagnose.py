"""Read-only WorkBuddy diagnostic Run Card projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.sources.doctor import format_doctor_report, run_doctor


DIAGNOSE_SCHEMA_VERSION = "briefloop.workbuddy_diagnose.v1"


def build_workbuddy_diagnosis(*, workspace: str | Path) -> dict[str, Any]:
    """Build a read-only WorkBuddy diagnosis payload for a workspace."""

    ws = Path(workspace).expanduser().resolve()
    config_path = ws / "config.yaml"
    intermediate = ws / "output" / "intermediate"
    workflow = _read_json(intermediate / "workflow_state.json")
    manifest = _read_json(intermediate / "runtime_manifest.json")
    registry = _read_json(intermediate / "artifact_registry.json")

    doctor_results = run_doctor(config_path=config_path) if config_path.exists() else []
    doctor_payload = _doctor_payload(doctor_results, config_exists=config_path.exists())
    artifact_payload = _artifact_payload(registry)
    finalize_path = intermediate / "finalize_report.json"
    delivery_dir = ws / "output" / "delivery"
    secret_risk = _secret_risk_payload(ws)
    run_integrity = workflow.get("run_integrity") if isinstance(workflow, Mapping) else None
    active_repair = workflow.get("active_repair") if isinstance(workflow, Mapping) else None
    blocked = bool(workflow.get("blocked")) if isinstance(workflow, Mapping) else False
    current_stage = _clean_text(workflow.get("current_stage")) if isinstance(workflow, Mapping) else "unknown"
    runtime = _clean_text(manifest.get("runtime")) if isinstance(manifest, Mapping) else "unknown"
    latest_gate_status = _latest_gate_status(registry)

    run_card = {
        "runtime": runtime or "unknown",
        "current_stage": current_stage or "unknown",
        "run_integrity": _run_integrity_status(run_integrity),
        "blocked": blocked,
        "latest_gate_status": latest_gate_status,
        "finalize_report": "present" if finalize_path.exists() else "missing",
        "delivery_dir": "present" if delivery_dir.is_dir() else "missing",
        "next_allowed_action": _next_safe_action(
            doctor=doctor_payload,
            run_integrity=run_integrity,
            active_repair=active_repair,
            invalid_or_stale_artifacts=artifact_payload["invalid_or_stale"],
            finalize_report_exists=finalize_path.exists(),
            delivery_dir_exists=delivery_dir.is_dir(),
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
            "runtime_capabilities": manifest.get("runtime_capabilities") if isinstance(manifest, Mapping) else None,
        },
        "workflow": {
            "workflow_state_present": isinstance(workflow, Mapping),
            "current_stage": current_stage,
            "blocked": blocked,
            "active_repair_present": bool(active_repair),
            "run_integrity": run_integrity,
        },
        "artifacts": artifact_payload,
        "finalize": {
            "path": "output/intermediate/finalize_report.json",
            "exists": finalize_path.exists(),
        },
        "delivery": {
            "path": "output/delivery",
            "exists": delivery_dir.is_dir(),
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


def _doctor_payload(results: list[Any], *, config_exists: bool) -> dict[str, Any]:
    errors = [result.message for result in results if getattr(result, "status", "") == "ERROR"]
    warnings = [result.message for result in results if getattr(result, "status", "") == "WARN"]
    if not config_exists:
        status = "error"
        errors = ["config.yaml missing"]
        report = "config.yaml missing"
    elif errors:
        status = "error"
        report = format_doctor_report(results)
    elif warnings:
        status = "warning"
        report = format_doctor_report(results)
    else:
        status = "pass"
        report = format_doctor_report(results)
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


def _latest_gate_status(registry: Any) -> str:
    artifacts = registry.get("artifacts") if isinstance(registry, Mapping) else {}
    for artifact_id in (
        "finalize_quality_gate_report",
        "auditor_quality_gate_report",
        "quality_gate_report",
    ):
        entry = artifacts.get(artifact_id) if isinstance(artifacts, Mapping) else None
        if not isinstance(entry, Mapping):
            continue
        status = _clean_text(entry.get("status"))
        validation = _clean_text(entry.get("validation_result"))
        if status and status != "expected":
            return f"{artifact_id}:{status}:{validation or 'unknown'}"
    return "unknown"


def _next_safe_action(
    *,
    doctor: Mapping[str, Any],
    run_integrity: Any,
    active_repair: Any,
    invalid_or_stale_artifacts: list[Mapping[str, str]],
    finalize_report_exists: bool,
    delivery_dir_exists: bool,
) -> str:
    if doctor.get("status") == "error":
        return "stop_show_full_doctor_output"
    if active_repair:
        return "stop_complete_or_inspect_active_repair"
    integrity = _run_integrity_status(run_integrity)
    if integrity not in {"clean", "pass", "ok"}:
        return "stop_run_integrity_not_clean"
    if invalid_or_stale_artifacts:
        return "inspect_invalid_or_stale_artifacts"
    if not finalize_report_exists or not delivery_dir_exists:
        return "draft_only_run_finalize_when_allowed"
    return "inspect_status_before_delivery_or_quality"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
