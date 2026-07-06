"""Assessment target definitions and read-only projections."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.active_repair import active_repair_is_open


ALLOWED_ASSESSMENT_TARGETS = {"auditable_brief", "delivery_brief"}
DEFAULT_ASSESSMENT_TARGET = "delivery_brief"
EXPERIMENT_080_CONDITION_PATH = Path("experiment/080/condition.json")
AUDIT_BINDING_SCHEMA = "mabw.auditable_audit_binding.v1"
AUDITABLE_TARGET_DOWNSTREAM_STAGES = ("analyst", "editor", "auditor")

AUDITABLE_TARGET_ARTIFACTS = {
    "audited_brief": "output/intermediate/audited_brief.md",
    "audit_report": "output/intermediate/audit_report.json",
    "auditor_quality_gate_report": "output/intermediate/gates/auditor_quality_gate_report.json",
}

FINAL_ABSTRACT_QUALITY_WARNING_TYPES = {
    "final_scope_title_mismatch",
    "final_missing_comparison_basis",
    "final_missing_limitation_section",
    "final_incomplete_key_case_fields",
    "final_unsupported_superlative",
}

ASSESSMENT_TARGET_CLAIM_SCOPE = {
    "auditable_brief": [
        "guidance_manifestation_in_audited_brief",
        "evidence_use_under_frozen_fact_layer",
        "auditor_gate_passage",
    ],
    "delivery_brief": [
        "guidance_manifestation_in_reader_delivery",
        "reader_clean_delivery",
        "finalize_transform_included",
    ],
}

ASSESSMENT_TARGET_EXCLUDED_CLAIM_SCOPE = {
    "auditable_brief": [
        "reader_clean_delivery",
        "finalize_transform_correctness",
        "management_ready_output",
        "docx_pdf_delivery_quality",
    ],
    "delivery_brief": [],
}

ASSESSMENT_TARGET_INCLUDED_CONTROLS = {
    "auditable_brief": [
        "analyst complete",
        "editor complete",
        "auditor complete",
        "auditor gates pass",
        "audit report artifact present",
        "auditor gate report artifact present",
        "run integrity clean",
    ],
    "delivery_brief": [
        "finalize complete",
        "reader-clean delivery pass",
        "delivery artifacts present",
        "run archive present",
    ],
}

AUDITABLE_TARGET_NEXT_ALLOWED = [
    "multi-agent-brief experiments 080 register-run --case <case_dir> --condition <condition> --workspace <workspace> --output <run_record.json>",
    "multi-agent-brief experiments 080 score-run --case <case_dir> --run-record <run_record.json> --output <scorecard.json>",
]

AUDITABLE_TARGET_FORBIDDEN = [
    "finalize",
    "finalize-complete",
    "deliver",
    "DOCX/PDF delivery quality claims",
    "reader-clean delivery claims",
]


def assessment_target(container: dict[str, Any]) -> str:
    value = container.get("assessment_target")
    if isinstance(value, str) and value in ALLOWED_ASSESSMENT_TARGETS:
        return value
    return DEFAULT_ASSESSMENT_TARGET


def assessment_target_manifest(target: str) -> dict[str, Any]:
    target = target if target in ALLOWED_ASSESSMENT_TARGETS else DEFAULT_ASSESSMENT_TARGET
    if target == "auditable_brief":
        return {
            "assessment_target": target,
            "target_status_semantics": "auditor_ready_internal_auditable_draft",
            "target_artifact": "output/intermediate/audited_brief.md",
            "target_complete_when": [
                "analyst_complete",
                "editor_complete",
                "auditor_complete",
                "analyst_editor_auditor_stage_completion_events_bound",
                "auditor_quality_gates_pass",
                "run_integrity_clean",
                "no_active_repair",
            ],
            "included_control_scope": ASSESSMENT_TARGET_INCLUDED_CONTROLS[target],
            "claim_scope": ASSESSMENT_TARGET_CLAIM_SCOPE[target],
            "excluded_claim_scope": ASSESSMENT_TARGET_EXCLUDED_CLAIM_SCOPE[target],
            "audit_binding_status": "required_python_owned",
            "timing_semantics": "diagnostic_only",
            "reader_clean_required": False,
            "delivery_archive_required": False,
            "next_allowed_commands": list(AUDITABLE_TARGET_NEXT_ALLOWED),
            "forbidden_downstream_actions": list(AUDITABLE_TARGET_FORBIDDEN),
        }
    return {
        "assessment_target": target,
        "target_status_semantics": "reader_delivery_brief",
        "target_artifact": "output/delivery/brief.md",
        "target_complete_when": [
            "finalize_complete",
            "reader_clean_pass",
            "delivery_artifacts_present",
            "run_archive_present",
        ],
        "included_control_scope": ASSESSMENT_TARGET_INCLUDED_CONTROLS[target],
        "claim_scope": ASSESSMENT_TARGET_CLAIM_SCOPE[target],
        "excluded_claim_scope": ASSESSMENT_TARGET_EXCLUDED_CLAIM_SCOPE[target],
        "audit_binding_status": "not_applicable",
        "timing_semantics": "required_control_for_a_controlled",
        "reader_clean_required": True,
        "delivery_archive_required": True,
        "next_allowed_commands": [],
        "forbidden_downstream_actions": [],
    }


def load_experiment_080_condition_metadata(workspace: str | Path) -> dict[str, Any] | None:
    path = Path(workspace).expanduser().resolve() / EXPERIMENT_080_CONDITION_PATH
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def project_assessment_target_status(
    *,
    condition_metadata: dict[str, Any] | None,
    workflow_state: dict[str, Any] | None,
    artifact_registry: dict[str, Any] | None,
    auditor_gate_report: dict[str, Any] | None,
    event_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(condition_metadata, dict):
        return {"present": False}
    target = assessment_target(condition_metadata)
    manifest = assessment_target_manifest(target)
    projection: dict[str, Any] = {
        "present": True,
        "experiment_id": condition_metadata.get("experiment_id"),
        "case_id": condition_metadata.get("case_id"),
        "condition": condition_metadata.get("condition"),
        "assessment_target": target,
        "assessment_target_manifest": manifest,
        "target_complete": False,
        "status": "not_applicable" if target != "auditable_brief" else "incomplete",
        "audit_binding_status": manifest.get("audit_binding_status"),
        "reasons": [],
    }
    if target != "auditable_brief":
        return projection

    reasons = _auditable_target_incomplete_reasons(
        workflow_state=workflow_state,
        artifact_registry=artifact_registry,
        auditor_gate_report=auditor_gate_report,
        event_records=event_records,
    )
    projection["target_complete"] = not reasons
    projection["status"] = "complete" if not reasons else "incomplete"
    projection["reasons"] = reasons
    return projection


def _auditable_target_incomplete_reasons(
    *,
    workflow_state: dict[str, Any] | None,
    artifact_registry: dict[str, Any] | None,
    auditor_gate_report: dict[str, Any] | None,
    event_records: list[dict[str, Any]] | None,
) -> list[str]:
    reasons: list[str] = []
    workflow = workflow_state if isinstance(workflow_state, dict) else {}
    registry = artifact_registry if isinstance(artifact_registry, dict) else {}
    artifacts = registry.get("artifacts") if isinstance(registry.get("artifacts"), dict) else {}

    current_stage = workflow.get("current_stage")
    if current_stage not in {"finalize", None}:
        reasons.append(f"workflow current_stage is {current_stage or '<missing>'}, expected finalize or terminal")
    if active_repair_is_open(workflow):
        reasons.append("active repair is open")

    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    for stage_id in AUDITABLE_TARGET_DOWNSTREAM_STAGES:
        stage_status = statuses.get(stage_id) if isinstance(statuses.get(stage_id), dict) else {}
        if stage_status.get("status") != "complete":
            reasons.append(f"{stage_id} stage is not complete")
    reasons.extend(
        auditable_stage_completion_event_reasons(
            workflow_state=workflow,
            event_records=event_records,
        )
    )

    integrity = workflow.get("run_integrity") if isinstance(workflow.get("run_integrity"), dict) else {}
    if integrity.get("status") != "clean":
        reasons.append("run_integrity is not clean")
    if integrity.get("reference_eligible") is not True:
        reasons.append("run_integrity.reference_eligible is not true")

    for artifact_id, expected_path in AUDITABLE_TARGET_ARTIFACTS.items():
        record = artifacts.get(artifact_id) if isinstance(artifacts.get(artifact_id), dict) else {}
        if record.get("path") != expected_path:
            reasons.append(f"{artifact_id} path is not {expected_path}")
        if record.get("status") != "valid":
            reasons.append(f"{artifact_id} is not valid in artifact_registry")
        if not isinstance(record.get("sha256"), str) or len(str(record.get("sha256"))) != 64:
            reasons.append(f"{artifact_id} has no frozen sha256")

    _extend_audit_binding_reasons(
        reasons,
        workflow=workflow,
        artifacts=artifacts,
        event_records=event_records,
    )

    gate = auditor_gate_report if isinstance(auditor_gate_report, dict) else {}
    if gate.get("status") != "pass" and not auditable_gate_has_only_final_abstract_advisory_warnings(gate):
        reasons.append("auditor quality gate report status is not pass")
    for result in gate.get("gate_results") or []:
        if isinstance(result, dict) and (result.get("status") == "fail" or result.get("blocking") is True):
            reasons.append("auditor quality gate report contains blocking gate_results")
            break
    for finding in gate.get("findings") or []:
        if isinstance(finding, dict) and (
            finding.get("blocking") is True or finding.get("blocking_level") == "blocking"
        ):
            reasons.append("auditor quality gate report contains blocking findings")
            break

    return reasons


def auditable_gate_has_only_final_abstract_advisory_warnings(payload: dict[str, Any]) -> bool:
    if payload.get("status") != "warning":
        return False
    gate_results = payload.get("gate_results")
    findings = payload.get("findings")
    if not isinstance(gate_results, list) or not isinstance(findings, list):
        return False
    advisory_finding_ids: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            return False
        if finding.get("gate_id") != "final_abstract_quality":
            return False
        if finding.get("finding_type") not in FINAL_ABSTRACT_QUALITY_WARNING_TYPES:
            return False
        if finding.get("blocking") is True or finding.get("blocking_level") == "blocking":
            return False
        finding_id = str(finding.get("finding_id") or "")
        if finding_id:
            advisory_finding_ids.add(finding_id)

    saw_advisory_warning = False
    for result in gate_results:
        if not isinstance(result, dict):
            return False
        gate_id = result.get("gate_id")
        status = result.get("status")
        if status == "pass":
            continue
        if gate_id != "final_abstract_quality" or status != "warning" or result.get("blocking") is True:
            return False
        finding_ids = result.get("finding_ids")
        if not isinstance(finding_ids, list):
            return False
        if any(str(finding_id) not in advisory_finding_ids for finding_id in finding_ids):
            return False
        saw_advisory_warning = True
    return saw_advisory_warning


def auditable_gate_projection_has_only_final_abstract_advisory_warnings(gate: dict[str, Any]) -> bool:
    if gate.get("report_status") != "warning" or gate.get("no_blocking") is not True:
        return False
    warning_types = gate.get("warning_finding_types")
    if not isinstance(warning_types, list) or not warning_types:
        return False
    normalized = {str(item) for item in warning_types if isinstance(item, str) and item}
    return bool(normalized) and normalized <= FINAL_ABSTRACT_QUALITY_WARNING_TYPES


def auditable_stage_completion_event_reasons(
    *,
    workflow_state: dict[str, Any] | None,
    event_records: list[dict[str, Any]] | None,
) -> list[str]:
    workflow = workflow_state if isinstance(workflow_state, dict) else {}
    if event_records is None:
        return ["stage completion event log is unavailable"]
    run_id = workflow.get("run_id")
    run_id_text = run_id.strip() if isinstance(run_id, str) and run_id.strip() else ""
    if not run_id_text:
        return ["stage completion events cannot verify workflow run_id"]
    reasons: list[str] = []
    for stage_id in AUDITABLE_TARGET_DOWNSTREAM_STAGES:
        if _auditable_stage_completion_event(event_records, run_id=run_id_text, stage_id=stage_id) is None:
            reasons.append(f"{stage_id} stage completion decision_recorded event is missing")
    return reasons


def _extend_audit_binding_reasons(
    reasons: list[str],
    *,
    workflow: dict[str, Any],
    artifacts: dict[str, Any],
    event_records: list[dict[str, Any]] | None,
) -> None:
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    auditor_status = statuses.get("auditor") if isinstance(statuses.get("auditor"), dict) else {}
    auditor_metadata = (
        auditor_status.get("metadata")
        if isinstance(auditor_status.get("metadata"), dict)
        else {}
    )
    binding = (
        auditor_metadata.get("audit_binding")
        if isinstance(auditor_metadata.get("audit_binding"), dict)
        else {}
    )
    if binding.get("schema_version") != AUDIT_BINDING_SCHEMA:
        reasons.append("auditor audit_binding is missing or has unsupported schema")
        return
    expected = {
        "claim_ledger_sha256": _artifact_sha(artifacts, "claim_ledger"),
        "audited_brief_sha256": _artifact_sha(artifacts, "audited_brief"),
        "audit_report_sha256": _artifact_sha(artifacts, "audit_report"),
    }
    for field, expected_sha in expected.items():
        actual = binding.get(field)
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            reasons.append(f"audit binding cannot verify {field.removesuffix('_sha256')}")
        elif actual != expected_sha:
            reasons.append(f"audit binding {field} does not match artifact_registry")
    run_id = workflow.get("run_id")
    run_id_text = run_id.strip() if isinstance(run_id, str) and run_id.strip() else ""
    repair_ids = binding.get("relevant_repair_transaction_ids")
    if not isinstance(repair_ids, list) or any(not isinstance(item, str) or not item for item in repair_ids):
        reasons.append("audit binding relevant_repair_transaction_ids is invalid")
    elif event_records is not None:
        if not run_id_text:
            reasons.append("audit binding repair history cannot verify workflow run_id")
        else:
            expected_repair_ids = _auditable_brief_repair_transaction_ids(
                event_records,
                run_id=run_id_text,
            )
            if list(repair_ids) != expected_repair_ids:
                reasons.append("audit binding relevant_repair_transaction_ids does not match event_log")
    auditor_stage_transaction_id = binding.get("auditor_stage_transaction_id")
    if not isinstance(auditor_stage_transaction_id, str) or not auditor_stage_transaction_id:
        reasons.append("audit binding auditor_stage_transaction_id is missing")
    elif event_records is not None:
        if not run_id_text:
            reasons.append("audit binding auditor_stage_transaction_id cannot verify workflow run_id")
        elif _auditable_auditor_completion_event(
            event_records,
            run_id=run_id_text,
            transaction_id=auditor_stage_transaction_id,
        ) is None:
            reasons.append("audit binding auditor_stage_transaction_id does not match event_log")


def _artifact_sha(artifacts: dict[str, Any], artifact_id: str) -> str | None:
    record = artifacts.get(artifact_id) if isinstance(artifacts.get(artifact_id), dict) else {}
    sha = record.get("sha256")
    return sha if isinstance(sha, str) else None


def _auditable_brief_repair_transaction_ids(
    records: list[dict[str, Any]],
    *,
    run_id: str,
) -> list[str]:
    ids: list[str] = []
    artifact_path = "output/intermediate/audited_brief.md"
    for event in records:
        if event.get("run_id") != run_id:
            continue
        if event.get("event_type") != "repair_completed":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        allowed = [str(item) for item in metadata.get("allowed_artifacts") or []]
        if not any(_artifact_path_matches(pattern, artifact_path) for pattern in allowed):
            continue
        transaction_id = metadata.get("transaction_id") or metadata.get("repair_transaction_id")
        if isinstance(transaction_id, str) and transaction_id and transaction_id not in ids:
            ids.append(transaction_id)
    return ids


def _artifact_path_matches(pattern: str, path: str) -> bool:
    candidate = pattern.strip()
    return bool(candidate and (path == candidate or fnmatch.fnmatch(path, candidate)))


def _auditable_auditor_completion_event(
    records: list[dict[str, Any]],
    *,
    run_id: str,
    transaction_id: str,
) -> dict[str, Any] | None:
    for event in records:
        if event.get("run_id") != run_id:
            continue
        if event.get("event_type") != "decision_recorded":
            continue
        if event.get("stage_id") != "auditor" or event.get("decision") != "continue":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if metadata.get("transaction_id") == transaction_id:
            return event
    return None


def _auditable_stage_completion_event(
    records: list[dict[str, Any]],
    *,
    run_id: str,
    stage_id: str,
) -> dict[str, Any] | None:
    for event in records:
        if event.get("run_id") != run_id:
            continue
        if event.get("event_type") != "decision_recorded":
            continue
        if event.get("stage_id") != stage_id or event.get("decision") != "continue":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        transaction_id = metadata.get("transaction_id")
        if isinstance(transaction_id, str) and transaction_id:
            return event
    return None
