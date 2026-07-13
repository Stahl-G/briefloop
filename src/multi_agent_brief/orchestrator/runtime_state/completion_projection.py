"""Read-only completion projection for finalize and delivery truth.

This module is intentionally a projection layer.  It consumes runtime control
records, including ``finalize_report.json`` as the single delivery truth record,
and does not write or repair any workspace state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.contracts.target_contract import (
    ALLOWED_ASSESSMENT_TARGETS,
    EXPERIMENT_080_CONDITION_PATH,
    project_assessment_target_status,
)
from multi_agent_brief.orchestrator.active_repair import active_repair_is_open
from multi_agent_brief.orchestrator.run_integrity import (
    interpret_run_integrity,
    project_for_read,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.event_log import read_event_log_records_strict
from multi_agent_brief.orchestrator.runtime_state.completion_gates import (
    _finalize_completion_reasons,
)
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.paths import runtime_state_paths
from multi_agent_brief.orchestrator.runtime_state.workflow import WORKFLOW_STATE_SCHEMA
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.quality_gates.contract import (
    interpret_quality_gate_binding,
    quality_gate_report_key_for_stage,
    require_quality_gate_binding_pass,
)


COMPLETION_PROJECTION_SCHEMA_VERSION = "briefloop.completion_projection.v1"

_CONTROL_STOP_STATUSES = {
    "degradation",
    "missing",
    "not_materialized",
    "snapshot_drift",
    "unreadable_utf8",
    "invalid_json",
    "invalid_json_shape",
    "invalid_schema",
    "unreadable",
}


def build_completion_projection(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    """Build the read-only completion projection for a workspace."""

    # Keep this import at the runtime chokepoint. ``recovery_state`` imports the
    # runtime-state facade, which exports this module; importing the Registry
    # interpreter at module load time would re-enter partially initialized
    # Recovery modules.
    from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import (
        CanonicalRegistryView,
        interpret_artifact_registry,
    )

    ws = Path(workspace).expanduser().resolve()
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    intermediate = ws / "output" / "intermediate"

    workflow, workflow_status = _read_json_with_schema(
        intermediate / "workflow_state.json",
        expected_schema=WORKFLOW_STATE_SCHEMA,
    )
    manifest, manifest_status = _read_json_with_schema(
        intermediate / "runtime_manifest.json",
        expected_schema=RUNTIME_MANIFEST_SCHEMA,
    )
    registry_verdict = interpret_artifact_registry(
        workspace=ws,
        repo_workdir=repo,
    )
    registry = _canonical_registry_payload(
        registry_verdict,
        canonical_view_type=CanonicalRegistryView,
    )
    registry_status = _registry_control_status(
        registry_verdict,
        canonical_view_type=CanonicalRegistryView,
    )
    event_records, event_log_status = _read_event_log(runtime_state_paths(ws)["event_log"])

    control_file_status = {
        "workflow_state": workflow_status,
        "runtime_manifest": manifest_status,
        "artifact_registry": registry_status,
        "event_log": event_log_status,
    }
    current_stage = _clean_text(workflow.get("current_stage")) if isinstance(workflow, Mapping) else ""
    current_stage = current_stage or "unknown"
    run_integrity = _run_integrity_projection(workflow, workflow_status)
    workflow_truth = _workflow_truth(workflow, workflow_status)
    artifact_truth = _artifact_truth(
        registry_verdict,
        canonical_view_type=CanonicalRegistryView,
    )
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    gate_truth = _stage_gate_truth(
        workspace=ws,
        current_stage=current_stage,
        stages=stages,
        artifacts=artifacts,
    )
    finalize_report, finalize_report_status = _read_json(intermediate / "finalize_report.json")
    finalize_truth = _finalize_truth(finalize_report, finalize_report_status)
    # Keep this local because recovery_state imports runtime-state modules.
    from multi_agent_brief.orchestrator.recovery_state import evaluate_recovery_state

    recovery_state = evaluate_recovery_state(workspace=ws, repo_workdir=repo)
    finalize_completion_reasons = _finalize_completion_blocking_reasons(
        workspace=ws,
        finalize_report_status=finalize_report_status,
        stages=stages,
        artifacts=artifacts,
        runtime_manifest=manifest if isinstance(manifest, dict) else None,
    )
    event_truth = _event_truth(
        event_records=event_records,
        event_log_status=event_log_status,
        finalize_truth=finalize_truth,
        recovery_state=recovery_state,
    )
    assessment_target = _assessment_target_projection(
        workspace=ws,
        workflow=workflow,
        registry=registry,
        event_records=event_records,
        intermediate=intermediate,
    )
    delivery_truth = _delivery_truth(
        finalize_truth=finalize_truth,
        finalize_completion_reasons=finalize_completion_reasons,
        gate_truth=gate_truth,
        event_truth=event_truth,
        artifact_truth=artifact_truth,
        recovery_state=recovery_state,
    )

    next_allowed_action = _next_allowed_action(
        control_file_status=control_file_status,
        workflow_truth=workflow_truth,
        recovery_state=recovery_state,
        artifact_truth=artifact_truth,
        gate_truth=gate_truth,
        finalize_truth=finalize_truth,
        delivery_truth=delivery_truth,
        event_truth=event_truth,
        assessment_target=assessment_target,
        current_stage=current_stage,
    )

    return {
        "ok": True,
        "schema_version": COMPLETION_PROJECTION_SCHEMA_VERSION,
        "runtime_effect": "read_only_completion_projection",
        "workspace": str(ws),
        "control_files": control_file_status,
        "workflow": workflow_truth,
        "runtime": {
            "status": manifest_status,
            "runtime": _clean_text(manifest.get("runtime")) if isinstance(manifest, Mapping) else "unknown",
            "run_id": _clean_text(manifest.get("run_id")) if isinstance(manifest, Mapping) else "",
        },
        "run_integrity": run_integrity,
        "recovery_state": recovery_state,
        "artifacts": artifact_truth,
        "gate_truth": gate_truth,
        "finalize_truth": finalize_truth,
        "delivery_truth": delivery_truth,
        "event_truth": event_truth,
        "assessment_target": assessment_target,
        "next_allowed_action": next_allowed_action,
        "boundary": (
            "read_only_projection_not_gate_delivery_release_or_semantic_proof; "
            "delivery_truth_source=finalize_report"
        ),
    }


def _workflow_truth(workflow: Any, status: str) -> dict[str, Any]:
    if status != "present" or not isinstance(workflow, Mapping):
        return {
            "status": status,
            "current_stage": "unknown",
            "blocked": False,
            "active_repair_present": False,
            "finalize_stage_complete": False,
        }
    stage_statuses = workflow.get("stage_statuses")
    finalize_status = {}
    if isinstance(stage_statuses, Mapping):
        value = stage_statuses.get("finalize")
        if isinstance(value, Mapping):
            finalize_status = dict(value)
    return {
        "status": "present",
        "current_stage": _clean_text(workflow.get("current_stage")) or "unknown",
        "blocked": bool(workflow.get("blocked")),
        "blocking_reason": _clean_text(workflow.get("blocking_reason")),
        "active_repair_present": active_repair_is_open(workflow),
        "finalize_stage_complete": _clean_text(finalize_status.get("status")) == "complete",
    }


def _artifact_truth(
    verdict: Any,
    *,
    canonical_view_type: type[Any],
) -> dict[str, Any]:
    invalid_or_stale: list[dict[str, str]] = []
    if isinstance(verdict, canonical_view_type):
        for artifact_id, record in verdict.records.items():
            artifact_status = _clean_text(record.get("status"))
            if artifact_status in {"invalid", "stale"}:
                invalid_or_stale.append({
                    "artifact_id": str(artifact_id),
                    "status": artifact_status,
                    "validation_result": _clean_text(record.get("validation_result")) or "unknown",
                })
        return {
            "status": "present",
            "trust_kind": verdict.kind,
            "reason_code": "",
            "invalid_or_stale": invalid_or_stale,
        }
    return {
        "status": verdict.kind,
        "trust_kind": verdict.kind,
        "reason_code": verdict.reason_code,
        "invalid_or_stale": [],
    }


def _canonical_registry_payload(
    verdict: Any,
    *,
    canonical_view_type: type[Any],
) -> dict[str, Any] | None:
    if not isinstance(verdict, canonical_view_type):
        return None
    return {
        "run_id": verdict.run_id,
        "artifacts": _thaw_registry_value(verdict.records),
    }


def _thaw_registry_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_registry_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_registry_value(item) for item in value]
    return value


def _registry_control_status(
    verdict: Any,
    *,
    canonical_view_type: type[Any],
) -> str:
    return "present" if isinstance(verdict, canonical_view_type) else verdict.kind


def _stage_gate_truth(
    *,
    workspace: Path,
    current_stage: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    stage = "finalize" if current_stage in {"finalize", "delivery", "delivered", "unknown"} else current_stage
    artifact_id = quality_gate_report_key_for_stage(stage)
    path = (
        workspace
        / "output"
        / "intermediate"
        / "gates"
        / ("finalize_quality_gate_report.json" if artifact_id == "finalize_quality_gate_report" else "auditor_quality_gate_report.json")
    )
    payload, status = _read_json(path)
    if status == "missing":
        return {
            "status": "missing",
            "artifact_id": artifact_id,
            "path": _workspace_relative(workspace, path),
            "blocking": False,
            "blocking_count": 0,
            "validation_errors": [],
        }
    if status != "present" or not isinstance(payload, Mapping):
        return {
            "status": "invalid",
            "artifact_id": artifact_id,
            "path": _workspace_relative(workspace, path),
            "blocking": True,
            "blocking_count": 1,
            "validation_errors": [f"gate report is {status}"],
        }
    expected_brief, expected_ledger = _expected_quality_gate_binding_artifacts(stage)
    verdict = interpret_quality_gate_binding(
        workspace=workspace,
        stage_id=stage,
        expected_brief=expected_brief,
        expected_ledger=expected_ledger,
        stages=stages,
        artifacts=artifacts,
    )
    errors = require_quality_gate_binding_pass(verdict)
    if errors:
        return {
            "status": "invalid",
            "artifact_id": artifact_id,
            "path": _workspace_relative(workspace, path),
            "blocking": True,
            "blocking_count": 1,
            "validation_errors": errors,
        }
    gate_status = _clean_text(payload.get("status")) or "unknown"
    blocking_count = _blocking_gate_result_count(payload) + _blocking_finding_count(payload)
    return {
        "status": gate_status,
        "artifact_id": artifact_id,
        "path": _workspace_relative(workspace, path),
        "blocking": gate_status == "fail" or blocking_count > 0,
        "blocking_count": blocking_count,
        "validation_errors": [],
    }


def _expected_quality_gate_binding_artifacts(stage: str) -> tuple[str, str]:
    if stage == "finalize":
        return "output/brief.md", "output/intermediate/claim_ledger.json"
    return "output/intermediate/audited_brief.md", "output/intermediate/claim_ledger.json"


def _finalize_truth(payload: Any, status: str) -> dict[str, Any]:
    base = {
        "status": status,
        "path": "output/intermediate/finalize_report.json",
        "report_status": "",
        "reader_clean_status": "",
        "delivery_promotion": "",
        "delivery_latest_dir": "",
        "delivery_artifact_count": 0,
        "delivery_artifact_hash_count": 0,
        "record_complete": False,
        "render_transaction_id": "",
    }
    if status != "present" or not isinstance(payload, Mapping):
        return base

    artifacts = payload.get("delivery_artifacts")
    hashes = payload.get("delivery_artifact_sha256")
    artifact_count = len(artifacts) if isinstance(artifacts, list) else 0
    hash_count = len(hashes) if isinstance(hashes, Mapping) else 0
    report_status = _clean_text(payload.get("status"))
    reader_clean = payload.get("reader_clean")
    reader_clean_status = _clean_text(reader_clean.get("status")) if isinstance(reader_clean, Mapping) else ""
    delivery_promotion = _clean_text(payload.get("delivery_promotion"))
    record_complete = (
        report_status == "pass"
        and reader_clean_status == "pass"
        and delivery_promotion == "promoted"
        and artifact_count > 0
        and hash_count >= artifact_count
        and bool(_clean_text(payload.get("delivery_latest_dir")))
    )
    return {
        **base,
        "status": "present",
        "report_status": report_status or "unknown",
        "reader_clean_status": reader_clean_status or "unknown",
        "delivery_promotion": delivery_promotion or "unknown",
        "delivery_latest_dir": _clean_text(payload.get("delivery_latest_dir")),
        "delivery_artifact_count": artifact_count,
        "delivery_artifact_hash_count": hash_count,
        "record_complete": record_complete,
        "render_transaction_id": _clean_text(payload.get("finalize_transaction_id")),
    }


def _delivery_truth(
    *,
    finalize_truth: Mapping[str, Any],
    finalize_completion_reasons: list[str],
    gate_truth: Mapping[str, Any],
    event_truth: Mapping[str, Any],
    artifact_truth: Mapping[str, Any],
    recovery_state: Mapping[str, Any],
) -> dict[str, Any]:
    findings: list[str] = []
    findings.extend(f"finalize_completion_blocker:{reason}" for reason in finalize_completion_reasons)
    if event_truth.get("finalize_event_present") is not True:
        findings.append("finalize_event_missing")
    if artifact_truth.get("status") != "present":
        findings.append(
            "artifact_registry_untrusted:"
            f"{_clean_text(artifact_truth.get('trust_kind')) or 'unknown'}:"
            f"{_clean_text(artifact_truth.get('reason_code')) or 'unknown'}"
        )
    elif artifact_truth.get("invalid_or_stale"):
        findings.append("artifact_registry_invalid_or_stale")
    if recovery_state.get("recovery_blocks_delivery") is True:
        findings.append(
            f"recovery_blocks_delivery:{_clean_text(recovery_state.get('reason_code')) or 'unknown'}"
        )
    return {
        "valid": not findings,
        "status": "valid" if not findings else "not_valid",
        "source": "finalize_report",
        "validation_scope": "finalize_completion_verdict_event_and_registry_status",
        "findings": findings,
    }


def _finalize_completion_blocking_reasons(
    *,
    workspace: Path,
    finalize_report_status: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    runtime_manifest: dict[str, Any] | None,
) -> list[str]:
    if finalize_report_status == "unreadable_utf8":
        return ["finalize_report.json is not valid UTF-8."]
    if finalize_report_status == "unreadable":
        return ["finalize_report.json could not be read."]
    return _finalize_completion_reasons(
        workspace,
        stages=stages,
        artifacts=artifacts,
        runtime_manifest=runtime_manifest,
    )


def _event_truth(
    *,
    event_records: list[Mapping[str, Any]],
    event_log_status: str,
    finalize_truth: Mapping[str, Any],
    recovery_state: Mapping[str, Any],
) -> dict[str, Any]:
    current_run_id = _clean_text(recovery_state.get("run_id"))
    current_events = [
        event
        for event in event_records
        if current_run_id and _clean_text(event.get("run_id")) == current_run_id
    ]
    outcome_types = {
        "delivery_bundle_prepared",
        "delivery_draft_created",
        "delivery_succeeded",
        "delivery_failed",
    }
    bound_outcomes = [
        event
        for event in current_events
        if event.get("event_type") in outcome_types
        and _delivery_event_is_current(
            event,
            finalize_truth=finalize_truth,
            recovery_state=recovery_state,
        )
    ]
    latest_outcome = _clean_text(bound_outcomes[-1].get("event_type")) if bound_outcomes else ""
    bound_attempts = [
        event
        for event in current_events
        if event.get("event_type") == "delivery_attempted"
        and _delivery_event_is_current(
            event,
            finalize_truth=finalize_truth,
            recovery_state=recovery_state,
        )
    ]
    return {
        "status": event_log_status,
        "finalize_event_present": _has_finalize_event(current_events),
        "delivery_attempt_present": bool(bound_attempts),
        "delivery_event_present": bool(bound_outcomes),
        "delivery_outcome": latest_outcome or "missing",
        "delivery_bundle_prepared": latest_outcome == "delivery_bundle_prepared",
        "delivery_draft_created": latest_outcome == "delivery_draft_created",
        "delivery_succeeded": latest_outcome == "delivery_succeeded",
        "delivery_failed": latest_outcome == "delivery_failed",
    }


def _delivery_event_is_current(
    event: Mapping[str, Any],
    *,
    finalize_truth: Mapping[str, Any],
    recovery_state: Mapping[str, Any],
) -> bool:
    current_run_id = _clean_text(recovery_state.get("run_id"))
    if not current_run_id or _clean_text(event.get("run_id")) != current_run_id:
        return False
    metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
    render_transaction_id = _clean_text(finalize_truth.get("render_transaction_id"))
    if not render_transaction_id or _clean_text(metadata.get("render_transaction_id")) != render_transaction_id:
        return False
    recovery_status = recovery_state.get("status")
    if recovery_status == "not_applicable":
        return True
    if recovery_status != "completed_non_reference":
        return False
    return (
        _clean_text(metadata.get("recovery_transaction_id"))
        == _clean_text(recovery_state.get("recovery_transaction_id"))
        and _clean_text(metadata.get("contamination_event_id"))
        == _clean_text(recovery_state.get("contamination_event_id"))
    )


def _assessment_target_projection(
    *,
    workspace: Path,
    workflow: Any,
    registry: Any,
    event_records: list[Mapping[str, Any]],
    intermediate: Path,
) -> dict[str, Any]:
    condition, condition_status = _read_json(workspace / EXPERIMENT_080_CONDITION_PATH)
    if condition_status == "missing":
        return {"present": False, "status": "not_applicable"}
    if condition_status != "present" or not isinstance(condition, Mapping):
        return {
            "present": True,
            "status": "invalid_condition",
            "condition_status": condition_status,
        }
    assessment_target = condition.get("assessment_target")
    if "assessment_target" in condition and (
        not isinstance(assessment_target, str)
        or assessment_target not in ALLOWED_ASSESSMENT_TARGETS
    ):
        return {
            "present": True,
            "status": "invalid_condition",
            "condition_status": condition_status,
            "reason": "unsupported_assessment_target",
        }
    auditor_gate, _ = _read_json(intermediate / "gates" / "auditor_quality_gate_report.json")
    projection = project_assessment_target_status(
        condition_metadata=dict(condition),
        workflow_state=dict(workflow) if isinstance(workflow, Mapping) else None,
        artifact_registry=dict(registry) if isinstance(registry, Mapping) else None,
        auditor_gate_report=dict(auditor_gate) if isinstance(auditor_gate, Mapping) else None,
        event_records=[dict(record) for record in event_records if isinstance(record, Mapping)],
    )
    projection["condition_status"] = condition_status
    return projection


def _next_allowed_action(
    *,
    control_file_status: Mapping[str, str],
    workflow_truth: Mapping[str, Any],
    recovery_state: Mapping[str, Any],
    artifact_truth: Mapping[str, Any],
    gate_truth: Mapping[str, Any],
    finalize_truth: Mapping[str, Any],
    delivery_truth: Mapping[str, Any],
    event_truth: Mapping[str, Any],
    assessment_target: Mapping[str, Any],
    current_stage: str,
) -> str:
    if any(
        status in _CONTROL_STOP_STATUSES
        for control_id, status in control_file_status.items()
        if control_id != "artifact_registry"
    ):
        return "inspect_unreadable_or_missing_control_files"
    if recovery_state.get("status") == "invalid_recovery_state":
        return "inspect_invalid_recovery"
    if control_file_status.get("artifact_registry") in _CONTROL_STOP_STATUSES:
        return "inspect_unreadable_or_missing_control_files"
    if workflow_truth.get("active_repair_present"):
        return "stop_complete_or_inspect_active_repair"
    if workflow_truth.get("blocked"):
        return "stop_workflow_blocked_human_review_required"
    if assessment_target.get("status") == "invalid_condition":
        return "inspect_invalid_experiment_condition"
    if assessment_target.get("assessment_target") == "auditable_brief":
        if assessment_target.get("target_complete") is True:
            return "register_auditable_brief_run"
        if _clean_text(current_stage) in {"finalize", "delivery", "delivered"}:
            return "inspect_auditable_brief_target_status"
        return "continue_current_stage_or_handoff_workflow"
    if gate_truth.get("blocking") is True or gate_truth.get("status") == "fail":
        return "stop_resolve_blocking_gate_report"
    if recovery_state.get("status") not in {"not_applicable", "completed_non_reference"}:
        return _clean_text(recovery_state.get("recommended_recovery_action")) or "inspect_invalid_recovery"
    if artifact_truth.get("invalid_or_stale"):
        return "inspect_invalid_or_stale_artifacts"
    if finalize_truth.get("status") != "present":
        if _clean_text(current_stage) in {"finalize", "delivery", "delivered"}:
            return "run_finalize_when_allowed"
        return "continue_current_stage_or_handoff_workflow"
    if finalize_truth.get("report_status") != "pass" or finalize_truth.get("reader_clean_status") != "pass":
        return "stop_finalize_failed_no_valid_delivery"
    if gate_truth.get("status") == "missing" or event_truth.get("finalize_event_present") is not True:
        return "run_finalize_gate_or_finalize_complete"
    if delivery_truth.get("valid") is True:
        return "inspect_status_before_delivery_or_quality"
    return "inspect_invalid_or_incomplete_finalize_report_delivery_truth"

def _run_integrity_projection(workflow: Any, workflow_status: str) -> dict[str, Any]:
    if workflow_status != "present" or not isinstance(workflow, Mapping):
        return project_for_read(
            interpret_run_integrity(
                None,
                field_present=False,
                unavailable_reason={
                    "reason_code": f"workflow_state_{workflow_status}",
                    "message": "workflow_state.run_integrity is unavailable.",
                },
            )
        )
    return project_for_read(
        interpret_run_integrity(
            workflow.get("run_integrity"),
            field_present="run_integrity" in workflow,
        )
    )


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


def _blocking_finding_count(payload: Mapping[str, Any]) -> int:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return 0
    return sum(
        1
        for finding in findings
        if isinstance(finding, Mapping)
        and (finding.get("blocking") is True or _clean_text(finding.get("blocking_level")) == "blocking")
    )


def _read_json(path: Path) -> tuple[Any, str]:
    if not path.exists():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return None, "unreadable_utf8"
    except json.JSONDecodeError:
        return None, "invalid_json"
    except OSError:
        return None, "unreadable"
    if not isinstance(payload, Mapping):
        return payload, "invalid_json_shape"
    return payload, "present"


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
    return any(_clean_text(record.get("event_type")) in event_types for record in records)


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


def _workspace_relative(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
