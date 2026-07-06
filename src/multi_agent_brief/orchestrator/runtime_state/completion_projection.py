"""Read-only completion truth projection.

This module is intentionally core runtime-state code, not a WorkBuddy adapter.
Runtime adapters may format the projection, but they must not re-infer delivery
truth from file existence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.contracts.target_contract import (
    ALLOWED_ASSESSMENT_TARGETS,
    EXPERIMENT_080_CONDITION_PATH,
    project_assessment_target_status,
)
from multi_agent_brief.orchestrator.run_integrity import (
    interpret_run_integrity,
    project_for_read,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import ARTIFACT_REGISTRY_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.event_log import read_event_log_records_strict
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.paths import runtime_state_paths
from multi_agent_brief.orchestrator.runtime_state.workflow import WORKFLOW_STATE_SCHEMA
from multi_agent_brief.quality_gates.contract import validate_quality_gate_report_payload


COMPLETION_PROJECTION_SCHEMA_VERSION = "briefloop.completion_projection.v1"
DELIVERY_MANIFEST_SCHEMA_VERSION = "briefloop.delivery_manifest.v1"
INTERMEDIATE_DIR = Path("output/intermediate")
DELIVERY_DIR = Path("output/delivery")


def build_completion_projection(*, workspace: str | Path) -> dict[str, Any]:
    """Build the canonical read-only completion projection for a workspace."""

    ws = Path(workspace).expanduser().resolve()
    intermediate = ws / INTERMEDIATE_DIR
    paths = runtime_state_paths(ws)
    workflow, workflow_status = _read_json_with_schema(
        paths["workflow_state"],
        expected_schema=WORKFLOW_STATE_SCHEMA,
    )
    manifest, manifest_status = _read_json_with_schema(
        paths["runtime_manifest"],
        expected_schema=RUNTIME_MANIFEST_SCHEMA,
    )
    registry, registry_status = _read_json_with_schema(
        paths["artifact_registry"],
        expected_schema=ARTIFACT_REGISTRY_SCHEMA,
    )
    event_records, event_log_status = _read_event_log(paths["event_log"])
    control_files = {
        "workflow_state": workflow_status,
        "runtime_manifest": manifest_status,
        "artifact_registry": registry_status,
        "event_log": event_log_status,
    }

    current_stage = _clean_text(workflow.get("current_stage")) if isinstance(workflow, Mapping) else "unknown"
    blocked = bool(workflow.get("blocked")) if isinstance(workflow, Mapping) else False
    active_repair = workflow.get("active_repair") if isinstance(workflow, Mapping) else None
    run_integrity = _run_integrity_projection(workflow, workflow_status)
    artifact_truth = _artifact_truth(registry, registry_status=registry_status)
    gate_truth = _latest_gate_truth(intermediate, current_stage=current_stage)
    assessment_target = _assessment_target_projection(
        workspace=ws,
        workflow=workflow,
        registry=registry,
        event_records=event_records,
        intermediate=intermediate,
    )
    finalize_truth = _finalize_truth(ws)
    delivery_truth = _delivery_truth(
        ws,
        finalize_truth=finalize_truth,
        artifact_truth=artifact_truth,
    )
    event_truth = {
        "event_log_status": event_log_status,
        "finalize_event_present": _has_finalize_event(event_records),
        "delivery_event_present": _has_event(
            event_records,
            {"delivery_completed", "delivery_recorded", "delivery_draft_created", "delivery_succeeded"},
        ),
    }
    workflow_truth = {
        "current_stage": current_stage or "unknown",
        "blocked": blocked,
        "blocking_reason": _clean_text(workflow.get("blocking_reason")) if isinstance(workflow, Mapping) else "",
        "active_repair": bool(active_repair),
        "active_repair_record": active_repair if isinstance(active_repair, Mapping) else None,
        "runtime": _clean_text(manifest.get("runtime")) if isinstance(manifest, Mapping) else "unknown",
    }

    return {
        "ok": True,
        "schema_version": COMPLETION_PROJECTION_SCHEMA_VERSION,
        "runtime_effect": "read_only_completion_projection",
        "workspace": str(ws),
        "control_files": control_files,
        "run_integrity": run_integrity,
        "workflow": workflow_truth,
        "artifacts": artifact_truth,
        "gate_truth": gate_truth,
        "assessment_target": assessment_target,
        "finalize_truth": finalize_truth,
        "delivery_truth": delivery_truth,
        "event_truth": event_truth,
        "next_allowed_action": _next_allowed_action(
            control_file_status=control_files,
            workflow=workflow_truth,
            run_integrity=run_integrity,
            gate_truth=gate_truth,
            artifact_truth=artifact_truth,
            assessment_target=assessment_target,
            finalize_truth=finalize_truth,
            delivery_truth=delivery_truth,
        ),
        "boundary": "completion_projection_not_gate_delivery_release_or_semantic_proof",
    }


def _run_integrity_projection(workflow: Any, workflow_status: str) -> dict[str, Any]:
    if not isinstance(workflow, Mapping):
        return project_for_read(
            interpret_run_integrity(
                None,
                field_present=False,
                unavailable_reason={
                    "reason_code": f"workflow_state_{workflow_status}",
                    "message": "workflow_state.json is unavailable for completion projection.",
                },
            )
        )
    return project_for_read(
        interpret_run_integrity(
            workflow.get("run_integrity"),
            field_present="run_integrity" in workflow,
        )
    )


def _artifact_truth(registry: Any, *, registry_status: str) -> dict[str, Any]:
    if registry_status != "present" or not isinstance(registry, Mapping):
        return {
            "artifact_registry_present": False,
            "artifact_registry_status": registry_status,
            "artifact_registry_valid": False,
            "invalid_or_stale": [],
            "expected": [],
            "findings": [f"artifact_registry_{registry_status}"],
        }
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
        "artifact_registry_status": registry_status,
        "artifact_registry_valid": True,
        "invalid_or_stale": invalid_or_stale,
        "expected": expected,
        "findings": [],
    }


def _latest_gate_truth(intermediate: Path, *, current_stage: str) -> dict[str, Any]:
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
                "status": "unreadable",
                "control_status": status,
                "blocking_count": 1,
                "blocking": True,
                "findings": [f"{artifact_id}:{status}"],
            }
        errors = validate_quality_gate_report_payload(
            dict(payload),
            stages=_gate_validation_stages(),
            artifacts=_gate_validation_artifacts(),
        )
        if errors:
            return {
                "artifact_id": artifact_id,
                "status": "invalid",
                "control_status": "present",
                "blocking_count": 1,
                "blocking": True,
                "validation_errors": errors,
                "findings": ["gate_report_invalid"],
            }
        gate_status = _clean_text(payload.get("status")) or "unknown"
        blocking_count = _blocking_gate_result_count(payload)
        findings = payload.get("findings")
        if isinstance(findings, list):
            blocking_count += sum(1 for finding in findings if _is_blocking_gate_finding(finding))
        return {
            "artifact_id": artifact_id,
            "status": gate_status,
            "control_status": "present",
            "blocking_count": blocking_count,
            "blocking": gate_status == "fail" or blocking_count > 0,
            "findings": [],
        }
    return {
        "artifact_id": "",
        "status": "missing",
        "control_status": "missing",
        "blocking_count": 0,
        "blocking": False,
        "findings": [],
    }


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
    auditor_gate, auditor_gate_status = _read_json(
        intermediate / "gates" / "auditor_quality_gate_report.json"
    )
    projection = project_assessment_target_status(
        condition_metadata=dict(condition),
        workflow_state=dict(workflow) if isinstance(workflow, Mapping) else None,
        artifact_registry=dict(registry) if isinstance(registry, Mapping) else None,
        auditor_gate_report=dict(auditor_gate) if isinstance(auditor_gate, Mapping) else None,
        event_records=[dict(record) for record in event_records if isinstance(record, Mapping)],
    )
    projection["condition_status"] = condition_status
    projection["auditor_gate_status"] = auditor_gate_status
    return projection


def _finalize_truth(workspace: Path) -> dict[str, Any]:
    path = workspace / INTERMEDIATE_DIR / "finalize_report.json"
    payload, control_status = _read_json(path)
    truth: dict[str, Any] = {
        "path": "output/intermediate/finalize_report.json",
        "control_status": control_status,
        "exists": path.exists(),
        "status": "missing" if control_status == "missing" else "invalid_control",
        "reader_clean_status": "unknown",
        "delivery_manifest": "",
        "delivery_manifest_sha256": "",
        "findings": [],
    }
    if control_status != "present" or not isinstance(payload, Mapping):
        if control_status != "missing":
            truth["findings"].append(f"finalize_report_{control_status}")
        return truth

    status = _clean_text(payload.get("status")) or "unknown"
    reader_clean = payload.get("reader_clean") if isinstance(payload.get("reader_clean"), Mapping) else {}
    reader_clean_status = _clean_text(reader_clean.get("status")) or "unknown"
    truth.update({
        "status": status,
        "reader_clean_status": reader_clean_status,
        "delivery_manifest": _clean_text(payload.get("delivery_manifest")),
        "delivery_manifest_sha256": _clean_text(payload.get("delivery_manifest_sha256")),
        "delivery_artifact_count": len(payload.get("delivery_artifacts") or [])
        if isinstance(payload.get("delivery_artifacts"), list)
        else 0,
    })
    if status != "pass":
        truth["findings"].append(f"finalize_status_{status}")
    if reader_clean_status != "pass":
        truth["findings"].append(f"reader_clean_{reader_clean_status}")
    return truth


def _delivery_truth(
    workspace: Path,
    *,
    finalize_truth: Mapping[str, Any],
    artifact_truth: Mapping[str, Any],
) -> dict[str, Any]:
    delivery_dir = workspace / DELIVERY_DIR
    truth: dict[str, Any] = {
        "valid": False,
        "status": "invalid",
        "delivery_dir_status": "present" if delivery_dir.is_dir() else "missing",
        "manifest_status": "not_checked",
        "paths_current": False,
        "hash_bound": False,
        "artifact_count": 0,
        "findings": [],
    }
    if artifact_truth.get("artifact_registry_valid") is not True:
        status = _clean_text(artifact_truth.get("artifact_registry_status")) or "unknown"
        truth["status"] = "invalid_control"
        truth["manifest_status"] = "not_checked"
        truth["findings"].append(f"artifact_registry_{status}")
        return truth
    report_path = workspace / INTERMEDIATE_DIR / "finalize_report.json"
    report, report_status = _read_json(report_path)
    if report_status != "present" or not isinstance(report, Mapping):
        truth["status"] = "not_available" if report_status == "missing" else "invalid"
        truth["manifest_status"] = "not_available"
        if delivery_dir.is_dir():
            truth["findings"].append("delivery_dir_exists_without_current_finalize_report")
        return truth
    if finalize_truth.get("status") != "pass" or finalize_truth.get("reader_clean_status") != "pass":
        truth["status"] = "not_current"
        truth["manifest_status"] = "not_current"
        if delivery_dir.is_dir():
            truth["findings"].append("delivery_dir_not_current_after_failed_finalize")
        return truth

    manifest_path = _resolve_workspace_path(workspace, report.get("delivery_manifest"))
    if manifest_path is None:
        truth["manifest_status"] = "missing_path"
        truth["findings"].append("delivery_manifest_path_missing")
        return truth
    intermediate_root = (workspace / INTERMEDIATE_DIR).resolve()
    try:
        manifest_path.relative_to(intermediate_root)
    except ValueError:
        truth["manifest_status"] = "outside_intermediate"
        truth["findings"].append("delivery_manifest_outside_intermediate")
        return truth
    expected_sha = report.get("delivery_manifest_sha256")
    if not isinstance(expected_sha, str) or not expected_sha.strip():
        truth["manifest_status"] = "missing_sha256"
        truth["findings"].append("delivery_manifest_sha256_missing")
        return truth
    if not manifest_path.exists():
        truth["manifest_status"] = "missing"
        truth["findings"].append("delivery_manifest_missing")
        return truth
    if not manifest_path.is_file():
        truth["manifest_status"] = "not_file"
        truth["findings"].append("delivery_manifest_not_file")
        return truth
    try:
        actual_sha = _sha256_file(manifest_path)
    except OSError as exc:
        truth["manifest_status"] = "unreadable"
        truth["findings"].append(f"delivery_manifest_unreadable:{exc}")
        return truth
    if actual_sha != expected_sha.strip():
        truth["manifest_status"] = "hash_mismatch"
        truth["findings"].append("delivery_manifest_hash_mismatch")
        return truth
    manifest, manifest_status = _read_json(manifest_path)
    if manifest_status != "present" or not isinstance(manifest, Mapping):
        truth["manifest_status"] = manifest_status
        truth["findings"].append(f"delivery_manifest_{manifest_status}")
        return truth
    truth["manifest_status"] = "present"
    manifest_artifacts = manifest.get("artifacts")
    if manifest.get("schema_version") != DELIVERY_MANIFEST_SCHEMA_VERSION:
        truth["findings"].append("delivery_manifest_schema_version_unsupported")
    if manifest.get("status") != "promoted":
        truth["findings"].append("delivery_manifest_status_not_promoted")
    if manifest.get("reader_clean_status") != "pass":
        truth["findings"].append("delivery_manifest_reader_clean_not_pass")
    if not isinstance(manifest_artifacts, list) or not manifest_artifacts:
        truth["findings"].append("delivery_manifest_artifacts_missing")
        return truth

    report_hashes = report.get("delivery_artifact_sha256")
    if not isinstance(report_hashes, Mapping):
        truth["findings"].append("finalize_report_delivery_artifact_sha256_missing")
        return truth
    delivery_root = (workspace / DELIVERY_DIR).resolve()
    report_artifacts = report.get("delivery_artifacts")
    if not isinstance(report_artifacts, list) or not report_artifacts:
        truth["findings"].append("finalize_report_delivery_artifacts_missing")
        return truth
    report_artifact_rels: set[str] = set()
    for item in report_artifacts:
        report_artifact_path = _resolve_workspace_path(workspace, item)
        if report_artifact_path is None:
            truth["findings"].append("finalize_report_delivery_artifact_path_invalid")
            continue
        try:
            rel = report_artifact_path.relative_to(workspace.resolve()).as_posix()
            report_artifact_path.relative_to(delivery_root)
        except ValueError:
            truth["findings"].append(f"finalize_report_delivery_artifact_outside_delivery:{item}")
            continue
        report_artifact_rels.add(rel)
    artifact_count = 0
    manifest_artifact_rels: set[str] = set()
    for artifact in manifest_artifacts:
        artifact_count += 1
        if not isinstance(artifact, Mapping):
            truth["findings"].append("delivery_manifest_artifact_invalid")
            continue
        raw_path = artifact.get("path")
        sha256 = artifact.get("sha256")
        kind = artifact.get("kind")
        if not isinstance(raw_path, str) or not raw_path.strip():
            truth["findings"].append("delivery_manifest_artifact_path_missing")
            continue
        if not isinstance(sha256, str) or not sha256.strip():
            truth["findings"].append(f"delivery_manifest_artifact_sha256_missing:{raw_path}")
            continue
        if not isinstance(kind, str) or not kind.strip():
            truth["findings"].append(f"delivery_manifest_artifact_kind_missing:{raw_path}")
        artifact_path = _resolve_workspace_path(workspace, raw_path)
        if artifact_path is None:
            truth["findings"].append("delivery_manifest_artifact_path_invalid")
            continue
        try:
            rel = artifact_path.relative_to(workspace.resolve()).as_posix()
            artifact_path.relative_to(delivery_root)
        except ValueError:
            truth["findings"].append(f"delivery_manifest_artifact_outside_delivery:{raw_path}")
            continue
        manifest_artifact_rels.add(rel)
        if not artifact_path.exists():
            truth["findings"].append(f"delivery_artifact_missing:{rel}")
            continue
        if not artifact_path.is_file():
            truth["findings"].append(f"delivery_artifact_not_file:{rel}")
            continue
        try:
            actual_artifact_sha = _sha256_file(artifact_path)
        except OSError as exc:
            truth["findings"].append(f"delivery_artifact_unreadable:{rel}:{exc}")
            continue
        report_hash = report_hashes.get(raw_path) or report_hashes.get(rel) or report_hashes.get(str(artifact_path))
        if not isinstance(report_hash, str) or not report_hash.strip():
            truth["findings"].append(f"finalize_report_delivery_artifact_sha256_missing:{rel}")
        elif report_hash.strip() != sha256.strip():
            truth["findings"].append(f"delivery_manifest_hash_mismatch:{rel}")
        elif actual_artifact_sha != sha256.strip():
            truth["findings"].append(f"delivery_artifact_hash_mismatch:{rel}")
    for rel in sorted(report_artifact_rels - manifest_artifact_rels):
        truth["findings"].append(f"delivery_manifest_missing_delivery_artifact:{rel}")
    for rel in sorted(manifest_artifact_rels - report_artifact_rels):
        truth["findings"].append(f"delivery_manifest_extra_delivery_artifact:{rel}")
    truth["artifact_count"] = artifact_count
    if truth["findings"]:
        return truth
    truth.update({
        "valid": True,
        "status": "valid",
        "paths_current": True,
        "hash_bound": True,
    })
    return truth


def _next_allowed_action(
    *,
    control_file_status: Mapping[str, str],
    workflow: Mapping[str, Any],
    run_integrity: Mapping[str, Any],
    gate_truth: Mapping[str, Any],
    artifact_truth: Mapping[str, Any],
    assessment_target: Mapping[str, Any],
    finalize_truth: Mapping[str, Any],
    delivery_truth: Mapping[str, Any],
) -> str:
    if any(
        status in {"unreadable_utf8", "invalid_json", "invalid_json_shape", "invalid_schema", "unreadable"}
        for status in control_file_status.values()
    ):
        return "inspect_unreadable_or_missing_control_files"
    if (
        control_file_status.get("artifact_registry") == "missing"
        and _completion_depends_on_artifact_registry(
            workflow=workflow,
            finalize_truth=finalize_truth,
            delivery_truth=delivery_truth,
        )
    ):
        return "inspect_unreadable_or_missing_control_files"
    if workflow.get("blocked") is True:
        return "stop_workflow_blocked_human_review_required"
    if workflow.get("active_repair") is True:
        return "stop_complete_or_inspect_active_repair"
    if _clean_text(run_integrity.get("status")) not in {"clean", "pass", "ok"}:
        return "stop_run_integrity_not_clean"
    if gate_truth.get("blocking") is True:
        return "stop_resolve_blocking_gate_report"
    if assessment_target.get("status") == "invalid_condition":
        return "inspect_invalid_experiment_condition"
    invalid_or_stale = artifact_truth.get("invalid_or_stale")
    if isinstance(invalid_or_stale, list) and invalid_or_stale:
        return "inspect_invalid_or_stale_artifacts"
    if assessment_target.get("assessment_target") == "auditable_brief":
        if assessment_target.get("target_complete") is True:
            return "register_auditable_brief_run"
        if _clean_text(workflow.get("current_stage")) in {"finalize", "delivery", "delivered"}:
            return "inspect_auditable_brief_target_status"
        return "continue_current_stage_or_handoff_workflow"
    if finalize_truth.get("status") not in {"missing", "pass"}:
        return "stop_finalize_failed_no_valid_delivery"
    if finalize_truth.get("status") == "pass" and finalize_truth.get("reader_clean_status") != "pass":
        return "stop_finalize_failed_no_valid_delivery"
    if delivery_truth.get("valid") is True:
        return "inspect_status_before_delivery_or_quality"
    if _clean_text(workflow.get("current_stage")) not in {"finalize", "delivery", "delivered"}:
        return "continue_current_stage_or_handoff_workflow"
    if finalize_truth.get("status") == "missing":
        return "run_finalize_when_allowed"
    return "inspect_invalid_delivery_truth"


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
            and (
                _clean_text(record.get("decision")) == "finalize_complete"
                or (
                    _clean_text(record.get("stage_id")) == "finalize"
                    and _clean_text(record.get("decision")) == "finalize"
                )
            )
        ):
            return True
    return False


def _completion_depends_on_artifact_registry(
    *,
    workflow: Mapping[str, Any],
    finalize_truth: Mapping[str, Any],
    delivery_truth: Mapping[str, Any],
) -> bool:
    if _clean_text(workflow.get("current_stage")) in {"finalize", "delivery", "delivered"}:
        return True
    if finalize_truth.get("status") != "missing":
        return True
    if delivery_truth.get("delivery_dir_status") == "present":
        return True
    return _clean_text(delivery_truth.get("manifest_status")) not in {
        "not_available",
        "not_checked",
    }


def _resolve_workspace_path(workspace: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def _gate_validation_stages() -> list[dict[str, Any]]:
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


def _gate_validation_artifacts() -> list[dict[str, Any]]:
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


def _clean_text(value: Any) -> str:
    return str(value or "").strip()
