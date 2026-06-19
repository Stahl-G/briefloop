"""Experiment assessment target definitions and read-only projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ALLOWED_ASSESSMENT_TARGETS = {"auditable_brief", "delivery_brief"}
DEFAULT_ASSESSMENT_TARGET = "delivery_brief"
EXPERIMENT_080_CONDITION_PATH = Path("experiment/080/condition.json")

AUDITABLE_TARGET_ARTIFACTS = {
    "audited_brief": "output/intermediate/audited_brief.md",
    "audit_report": "output/intermediate/audit_report.json",
    "auditor_quality_gate_report": "output/intermediate/gates/auditor_quality_gate_report.json",
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
                "auditor_quality_gates_pass",
                "run_integrity_clean",
                "no_active_repair",
            ],
            "included_control_scope": ASSESSMENT_TARGET_INCLUDED_CONTROLS[target],
            "claim_scope": ASSESSMENT_TARGET_CLAIM_SCOPE[target],
            "excluded_claim_scope": ASSESSMENT_TARGET_EXCLUDED_CLAIM_SCOPE[target],
            "audit_binding_status": "pending_pr1_not_checked",
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
) -> list[str]:
    reasons: list[str] = []
    workflow = workflow_state if isinstance(workflow_state, dict) else {}
    registry = artifact_registry if isinstance(artifact_registry, dict) else {}
    artifacts = registry.get("artifacts") if isinstance(registry.get("artifacts"), dict) else {}

    current_stage = workflow.get("current_stage")
    if current_stage not in {"finalize", None}:
        reasons.append(f"workflow current_stage is {current_stage or '<missing>'}, expected finalize or terminal")
    if isinstance(workflow.get("active_repair"), dict):
        reasons.append("active repair is open")

    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    for stage_id in ("analyst", "editor", "auditor"):
        stage_status = statuses.get(stage_id) if isinstance(statuses.get(stage_id), dict) else {}
        if stage_status.get("status") != "complete":
            reasons.append(f"{stage_id} stage is not complete")

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

    gate = auditor_gate_report if isinstance(auditor_gate_report, dict) else {}
    if gate.get("status") != "pass":
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
