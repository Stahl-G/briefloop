"""Read-only workspace status summary for writer-facing product commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.experiments.target_contract import (
    load_experiment_080_condition_metadata,
    project_assessment_target_status,
)
from multi_agent_brief.orchestrator.fact_layer_import import summarize_fact_layer_import
from multi_agent_brief.orchestrator.run_integrity import (
    interpret_run_integrity,
    project_for_read,
    workflow_with_sticky_contamination_events,
)
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.claim_support_matrix import (
    project_claim_support_matrix_from_workspace,
)
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    project_semantic_assessment_report_from_workspace,
)
from multi_agent_brief.orchestrator.timing import derive_control_timing_from_path
from multi_agent_brief.outputs.atomic_reader_projection import project_atomic_reader_text_from_workspace
from multi_agent_brief.product.guidance_manifestation import project_workspace_guidance_manifestation
from multi_agent_brief.product.materiality_selection import project_workspace_materiality_selection
from multi_agent_brief.product.policy_projection import project_workspace_policy_profile
from multi_agent_brief.product.quality_closeout import quality_panel_closeout_projection
from multi_agent_brief.product.support_wording import project_workspace_support_wording
from multi_agent_brief.product.template_conformance import project_workspace_report_template_conformance
from multi_agent_brief.product.template_projection import project_workspace_report_template
from multi_agent_brief.product.template_render_plan import project_workspace_report_template_render_plan
from multi_agent_brief.product.trajectory_regulation import project_workspace_trajectory_regulation


INTERMEDIATE_DIR = Path("output/intermediate")


def build_workspace_status(workspace: str | Path) -> dict[str, Any]:
    """Return a read-only status summary without refreshing runtime state.

    This helper deliberately avoids orchestrator runtime helpers such as
    ``state check`` or ``initialize_runtime_state``. It only reads existing
    workspace files and reports missing/corrupt surfaces as stale or unknown.
    """

    ws = Path(workspace).expanduser().resolve()
    payload: dict[str, Any] = {
        "ok": ws.exists() and ws.is_dir(),
        "workspace": str(ws),
        "read_only": True,
        "runtime": {},
        "workflow": {},
        "artifacts": {},
        "events": {},
        "quality_gate": {},
        "reader_clean": {},
        "improvement": {},
        "feedback": {},
        "experiment_080": {},
        "fact_layer_import": {},
        "atomic_reader_projection": {},
        "claim_support_matrix": {},
        "semantic_assessment_report": {},
        "policy_profile": {},
        "report_template": {},
        "report_template_conformance": {},
        "report_template_render_plan": {},
        "trajectory_regulation": {},
        "guidance_manifestation": {},
        "materiality_selection": {},
        "support_wording": {},
        "quality_panel_closeout": {},
        "timing": {},
        "stale_or_unknown": [],
        "suggested_next_command": None,
    }
    if not payload["ok"]:
        payload["error"] = f"Workspace directory not found: {ws}"
        payload["suggested_next_command"] = "multi-agent-brief init <workspace> --demo"
        return payload

    manifest = _read_json(ws / INTERMEDIATE_DIR / "runtime_manifest.json")
    workflow = _read_json(ws / INTERMEDIATE_DIR / "workflow_state.json")
    registry = _read_json(ws / INTERMEDIATE_DIR / "artifact_registry.json")
    quality_gate = _read_json(ws / INTERMEDIATE_DIR / "quality_gate_report.json")
    auditor_quality_gate = _read_json(ws / INTERMEDIATE_DIR / "gates" / "auditor_quality_gate_report.json")
    finalize_quality_gate = _read_json(ws / INTERMEDIATE_DIR / "gates" / "finalize_quality_gate_report.json")
    finalize_report = _read_json(ws / INTERMEDIATE_DIR / "finalize_report.json")
    feedback_issues = _read_json(ws / INTERMEDIATE_DIR / "feedback_issues.json")
    repair_plan = _read_json(ws / INTERMEDIATE_DIR / "repair_plan.json")

    event_log_path = ws / INTERMEDIATE_DIR / "event_log.jsonl"
    event_records = _event_records_best_effort(event_log_path)
    workflow_payload = workflow.get("payload") if workflow.get("status") == "present" else None
    if isinstance(workflow_payload, dict):
        workflow = dict(workflow)
        try:
            workflow["payload"] = workflow_with_sticky_contamination_events(workflow_payload, event_records)
        except RuntimeStateError:
            workflow["payload"] = workflow_payload
    workflow_payload = workflow.get("payload") if workflow.get("status") == "present" else None

    payload["runtime"] = _runtime_summary(manifest)
    payload["workflow"] = _workflow_summary(workflow)
    payload["artifacts"] = _artifact_summary(registry)
    payload["events"] = _event_summary(event_log_path)
    payload["quality_gate"] = _quality_gate_summary(
        _select_quality_gate_result(
            workflow=payload["workflow"],
            legacy=quality_gate,
            auditor=auditor_quality_gate,
            finalize=finalize_quality_gate,
        )
    )
    payload["reader_clean"] = _reader_clean_summary(finalize_report)
    payload["quality_panel_closeout"] = quality_panel_closeout_projection(
        workspace=ws,
        finalize_report=finalize_report.get("payload") if finalize_report.get("status") == "present" else None,
        artifact_registry=registry.get("payload") if registry.get("status") == "present" else None,
    )
    payload["improvement"] = _improvement_summary(ws, manifest)
    payload["feedback"] = _feedback_summary(feedback_issues, repair_plan)
    payload["experiment_080"] = project_assessment_target_status(
        condition_metadata=load_experiment_080_condition_metadata(ws),
        workflow_state=workflow_payload if isinstance(workflow_payload, dict) else None,
        artifact_registry=registry.get("payload") if registry.get("status") == "present" else None,
        auditor_gate_report=auditor_quality_gate.get("payload")
        if auditor_quality_gate.get("status") == "present"
        else None,
        event_records=event_records,
    )
    manifest_payload = manifest.get("payload") if manifest.get("status") == "present" else None
    payload["fact_layer_import"] = summarize_fact_layer_import(
        manifest_payload if isinstance(manifest_payload, dict) else None,
        workflow_payload if isinstance(workflow_payload, dict) else None,
        workspace=ws,
    )
    payload["atomic_reader_projection"] = _atomic_reader_projection_summary(ws)
    payload["claim_support_matrix"] = project_claim_support_matrix_from_workspace(ws)
    payload["semantic_assessment_report"] = project_semantic_assessment_report_from_workspace(ws)
    payload["policy_profile"] = project_workspace_policy_profile(ws)
    payload["materiality_selection"] = project_workspace_materiality_selection(
        ws,
        policy_profile=payload["policy_profile"],
    )
    payload["support_wording"] = project_workspace_support_wording(
        ws,
        claim_support_matrix=payload["claim_support_matrix"],
    )
    payload["report_template"] = project_workspace_report_template(ws)
    payload["report_template_conformance"] = project_workspace_report_template_conformance(ws)
    payload["report_template_render_plan"] = project_workspace_report_template_render_plan(ws)
    payload["trajectory_regulation"] = project_workspace_trajectory_regulation(
        ws,
        workflow_state=workflow_payload if isinstance(workflow_payload, dict) else None,
        event_records=event_records,
        event_log_present=event_log_path.exists(),
        event_log_corrupt_count=int(payload["events"].get("corrupt_count") or 0),
        run_id=(manifest_payload or {}).get("run_id") if isinstance(manifest_payload, dict) else None,
    )
    payload["guidance_manifestation"] = project_workspace_guidance_manifestation(
        ws,
        runtime_manifest=manifest_payload if isinstance(manifest_payload, dict) else None,
    )
    payload["timing"] = derive_control_timing_from_path(
        event_log_path,
        workflow_state=workflow_payload if isinstance(workflow_payload, dict) else None,
        expected_run_id=(manifest_payload or {}).get("run_id") if isinstance(manifest_payload, dict) else None,
    )

    stale = payload["stale_or_unknown"]
    for label, result in (
        ("runtime_manifest", manifest),
        ("workflow_state", workflow),
        ("artifact_registry", registry),
        ("quality_gate_report", quality_gate),
        ("auditor_quality_gate_report", auditor_quality_gate),
        ("finalize_quality_gate_report", finalize_quality_gate),
        ("finalize_report", finalize_report),
        ("feedback_issues", feedback_issues),
        ("repair_plan", repair_plan),
    ):
        if result["status"] == "missing":
            stale.append(f"{label} missing")
        elif result["status"] == "error":
            stale.append(f"{label} unreadable: {result['error']}")
    if payload["events"].get("corrupt_count"):
        stale.append("event_log contains unreadable records")
    for warning in payload["quality_gate"].get("schema_warnings") or []:
        stale.append(f"quality_gate_report schema warning: {warning}")

    payload["suggested_next_command"] = _suggested_next_command(ws, payload)
    return payload


def format_workspace_status(status: dict[str, Any]) -> str:
    """Format a concise human-readable status report."""

    lines = [
        f"[status] workspace: {status.get('workspace')}",
        f"[status] read_only: {status.get('read_only')}",
    ]
    if not status.get("ok"):
        lines.append(f"[status] error: {status.get('error')}")
        lines.append(f"[status] suggested_next: {status.get('suggested_next_command')}")
        return "\n".join(lines)

    runtime = status.get("runtime") or {}
    workflow = status.get("workflow") or {}
    artifacts = status.get("artifacts") or {}
    gate = status.get("quality_gate") or {}
    reader = status.get("reader_clean") or {}
    feedback = status.get("feedback") or {}
    fact_layer_import = status.get("fact_layer_import") or {}
    improvement = status.get("improvement") or {}
    experiment_080 = status.get("experiment_080") or {}
    atomic_projection = status.get("atomic_reader_projection") or {}
    claim_support_matrix = status.get("claim_support_matrix") or {}
    semantic_assessment_report = status.get("semantic_assessment_report") or {}
    policy_profile = status.get("policy_profile") or {}
    report_template = status.get("report_template") or {}
    report_template_conformance = status.get("report_template_conformance") or {}
    report_template_render_plan = status.get("report_template_render_plan") or {}
    trajectory_regulation = status.get("trajectory_regulation") or {}
    guidance_manifestation = status.get("guidance_manifestation") or {}
    materiality_selection = status.get("materiality_selection") or {}
    support_wording = status.get("support_wording") or {}
    quality_panel_closeout = status.get("quality_panel_closeout") or {}
    events = status.get("events") or {}
    timing = status.get("timing") or {}
    run_integrity = workflow.get("run_integrity") if isinstance(workflow.get("run_integrity"), dict) else {}

    lines.extend(
        [
            f"[status] run_id: {runtime.get('run_id') or 'unknown'}",
            f"[status] runtime: {runtime.get('runtime') or 'unknown'}",
            f"[status] recipe: {runtime.get('recipe') or 'unknown'}",
            f"[status] current_stage: {workflow.get('current_stage') or 'unknown'}",
            f"[status] blocked: {workflow.get('blocked')}",
            f"[status] blocking_reason: {workflow.get('blocking_reason') or ''}",
            (
                "[status] run_integrity: "
                f"{run_integrity.get('status') or 'unknown'} "
                f"reference_eligible={run_integrity.get('reference_eligible')}"
            ),
            (
                "[status] artifacts: "
                f"valid={artifacts.get('valid_count', 0)} "
                f"invalid={artifacts.get('invalid_count', 0)} "
                f"missing={artifacts.get('missing_count', 0)} "
                f"expected={artifacts.get('expected_count', 0)}"
            ),
            f"[status] events: count={events.get('event_count', 0)} corrupt={events.get('corrupt_count', 0)}",
            _format_fact_layer_import_line(fact_layer_import),
            _format_timing_line(timing),
            *_format_topology_satisfaction_lines(timing),
            *_format_experiment_080_lines(experiment_080),
            f"[status] quality_gate: {gate.get('status') or 'unknown'}",
            f"[status] reader_clean: {reader.get('status') or 'unknown'}",
            (
                "[status] quality_panel_closeout: "
                f"{quality_panel_closeout.get('status') or 'unknown'} "
                f"command={quality_panel_closeout.get('command') or ''}"
            ),
            (
                "[status] improvement: "
                f"ledger={improvement.get('ledger_present')} "
                f"snapshot={improvement.get('snapshot_present')} "
                f"materialized={len(improvement.get('materialized_entry_ids') or [])}"
            ),
            (
                "[status] feedback: "
                f"issues={feedback.get('issue_count', 0)} "
                f"open={feedback.get('open_count', 0)} "
                f"repair_plans={feedback.get('repair_plan_count', 0)}"
            ),
        ]
    )
    audited_projection = (
        atomic_projection.get("audited_brief")
        if isinstance(atomic_projection.get("audited_brief"), dict)
        else {}
    )
    if audited_projection.get("status") not in {None, "not_available"}:
        counts = audited_projection.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] atomic_reader_projection: "
            f"{audited_projection.get('status')} "
            f"atom_residue={counts.get('atom_residue_count', 0)} "
            f"process_residue={counts.get('process_residue_count', 0)}"
        )
    if claim_support_matrix.get("status") not in {None, "not_available"}:
        counts = claim_support_matrix.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] claim_support_matrix: "
            f"{claim_support_matrix.get('status')} "
            f"blocking_atoms={counts.get('blocking_atom_count', 0)} "
            f"weak_atoms={counts.get('weak_atom_count', 0)} "
            f"adjudication_atoms={counts.get('adjudication_required_atom_count', 0)}"
        )
    if semantic_assessment_report.get("status") not in {None, "not_available"}:
        counts = semantic_assessment_report.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] semantic_assessment_report: "
            f"{semantic_assessment_report.get('status')} "
            "boundary=proposal_only "
            f"proposals={counts.get('proposal_row_count', 0)} "
            f"llm_only={counts.get('llm_only_count', 0)} "
            f"high_uncertainty={counts.get('high_uncertainty_count', 0)} "
            f"high_disagreement={counts.get('high_disagreement_count', 0)} "
            f"adjudication={counts.get('requires_human_adjudication_count', 0)}"
        )
    if policy_profile.get("status") not in {None, "not_available"}:
        errors = policy_profile.get("errors") if isinstance(policy_profile.get("errors"), list) else []
        lines.append(
            "[status] policy_profile: "
            f"{policy_profile.get('status')} "
            f"id={policy_profile.get('resolved_policy_profile') or policy_profile.get('policy_profile') or 'unknown'} "
            f"source={policy_profile.get('source') or 'unknown'} "
            "boundary=projection_only "
            "runtime_effect=none "
            f"errors={len(errors)}"
        )
    if report_template.get("status") not in {None, "not_available"}:
        errors = report_template.get("errors") if isinstance(report_template.get("errors"), list) else []
        lines.append(
            "[status] report_template: "
            f"{report_template.get('status')} "
            f"id={report_template.get('template_id') or 'unknown'} "
            f"report_type={report_template.get('report_type') or 'unknown'} "
            f"sections={report_template.get('section_count') or 0} "
            "boundary=projection_only "
            "runtime_effect=none "
            f"errors={len(errors)}"
        )
    if report_template_conformance.get("status") not in {None, "not_available"}:
        counts = report_template_conformance.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] report_template_conformance: "
            f"{report_template_conformance.get('status')} "
            f"present_targets={counts.get('present_target_count', 0)} "
            f"warnings={counts.get('warning_target_count', 0)} "
            f"missing_sections={counts.get('missing_section_count', 0)} "
            f"out_of_order={counts.get('out_of_order_section_count', 0)} "
            f"extra_headings={counts.get('extra_heading_count', 0)} "
            f"reader_contract_warnings={counts.get('reader_block_warning_count', 0)} "
            "boundary=projection_only "
            "runtime_effect=none"
        )
    if report_template_render_plan.get("status") not in {None, "not_available"}:
        counts = report_template_render_plan.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        selected_source = report_template_render_plan.get("selected_source_artifact") or "none"
        lines.append(
            "[status] report_template_render_plan: "
            f"{report_template_render_plan.get('status')} "
            f"source={selected_source} "
            f"sections={counts.get('section_count', 0)} "
            f"unresolved={counts.get('unresolved_section_count', 0)} "
            f"targets={counts.get('planned_delivery_target_count', 0)} "
            "boundary=projection_only "
            "runtime_effect=none"
        )
    if trajectory_regulation.get("status") not in {None, "not_available"}:
        counts = trajectory_regulation.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        actions = trajectory_regulation.get("recommended_actions")
        actions = actions if isinstance(actions, list) else []
        lines.append(
            "[status] trajectory_regulation: "
            f"{trajectory_regulation.get('status')} "
            f"retry_events={counts.get('retry_stage_count', 0)} "
            f"repair_starts={counts.get('repair_started_count', 0)} "
            f"actions={len(actions)} "
            "boundary=projection_only "
            "runtime_effect=none"
        )
    if guidance_manifestation.get("status") not in {None, "not_available", "no_materialized_guidance"}:
        counts = guidance_manifestation.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] guidance_manifestation: "
            f"{guidance_manifestation.get('status')} "
            f"assessed={counts.get('assessed_entry_count', 0)} "
            f"not_observable={counts.get('not_observable_count', 0)} "
            f"contradicted={counts.get('contradicted_count', 0)} "
            "boundary=diagnostic_only "
            "runtime_effect=none"
        )
    if materiality_selection.get("status") not in {None, "not_available"}:
        counts = materiality_selection.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] materiality_selection: "
            f"{materiality_selection.get('status')} "
            f"findings={counts.get('finding_count', 0)} "
            f"human_review={counts.get('human_review_recommended_count', 0)} "
            "boundary=projection_only "
            "runtime_effect=none"
        )
    if support_wording.get("status") not in {None, "not_available"}:
        counts = support_wording.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] support_wording: "
            f"{support_wording.get('status')} "
            f"findings={counts.get('finding_count', 0)} "
            f"unsupported_reader={counts.get('unsupported_reader_claim_count', 0)} "
            f"weak_strong={counts.get('weak_support_strong_wording_count', 0)} "
            "boundary=projection_only "
            "runtime_effect=none"
        )
    for marker in status.get("stale_or_unknown") or []:
        lines.append(f"[status] stale_or_unknown: {marker}")
    lines.append(f"[status] suggested_next: {status.get('suggested_next_command')}")
    return "\n".join(lines)


def _format_topology_satisfaction_lines(timing: dict[str, Any]) -> list[str]:
    stages = timing.get("stages") if isinstance(timing.get("stages"), list) else []
    lines: list[str] = []
    for stage in stages:
        if not isinstance(stage, dict) or stage.get("status") != "satisfied_by_topology":
            continue
        stage_id = str(stage.get("stage_id") or "unknown")
        satisfied_by = str(stage.get("satisfied_by") or stage.get("satisfied_by_stage") or "unknown")
        topology = str(stage.get("topology") or "unknown")
        required = stage.get("required_artifacts")
        required_ids = [str(item) for item in required if item] if isinstance(required, list) else []
        required_text = ",".join(required_ids) if required_ids else "unknown"
        lines.append(
            f"[status] topology: {stage_id} complete via {satisfied_by} "
            f"({topology}; required={required_text})"
        )
    return lines


def _format_experiment_080_lines(experiment: dict[str, Any]) -> list[str]:
    if not experiment.get("present"):
        return []
    lines = [
        (
            "[status] experiment_080: "
            f"case={experiment.get('case_id') or 'unknown'} "
            f"condition={experiment.get('condition') or 'unknown'} "
            f"assessment_target={experiment.get('assessment_target') or 'unknown'}"
        )
    ]
    if experiment.get("assessment_target") == "auditable_brief":
        if experiment.get("target_complete"):
            lines.append("[status] target_complete: auditable_brief")
            lines.append(
                "[status] target_next: experiments 080 register-run; score-run; "
                "do not finalize for this target"
            )
        else:
            reasons = experiment.get("reasons") if isinstance(experiment.get("reasons"), list) else []
            first_reason = str(reasons[0]) if reasons else "target contract not yet satisfied"
            lines.append(f"[status] target_incomplete: auditable_brief reason={first_reason}")
    return lines


def _format_timing_line(timing: dict[str, Any]) -> str:
    status = timing.get("status") or "unknown"
    if status == "available":
        elapsed = timing.get("total_elapsed_seconds")
        stages = timing.get("stages") if isinstance(timing.get("stages"), list) else []
        finalized = timing.get("finalize") if isinstance(timing.get("finalize"), dict) else None
        stage_count = len(stages) + (1 if finalized else 0)
        return f"[status] timing: available total_elapsed={elapsed}s stages={stage_count}"
    if status == "contaminated":
        return "[status] timing: contaminated; elapsed buckets are not clean evidence"
    return f"[status] timing: {status}"


def _format_fact_layer_import_line(summary: dict[str, Any]) -> str:
    if summary.get("status") == "valid":
        freshness = summary.get("freshness_at_import") if isinstance(summary.get("freshness_at_import"), dict) else {}
        freshness_status = freshness.get("status") or "unknown"
        return (
            "[status] fact_layer_import: valid "
            f"source_run={summary.get('source_run_id') or 'unknown'} "
            f"fact_layer_sha256={(summary.get('fact_layer_sha256') or '')[:12]} "
            f"freshness_at_import={freshness_status} "
            f"next={summary.get('next_stage') or 'analyst'} "
            "satisfied=complete via import"
        )
    if summary.get("present"):
        return (
            "[status] fact_layer_import: invalid "
            f"errors={len(summary.get('errors') or [])}"
        )
    return "[status] fact_layer_import: missing"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path), "payload": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "error", "path": str(path), "payload": None, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"status": "error", "path": str(path), "payload": None, "error": "JSON root is not an object"}
    return {"status": "present", "path": str(path), "payload": payload}


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _atomic_reader_projection_summary(workspace: Path) -> dict[str, Any]:
    graph_present = (workspace / INTERMEDIATE_DIR / "atomic_claim_graph.json").exists()
    targets = {
        "audited_brief": (workspace / INTERMEDIATE_DIR / "audited_brief.md", "output/intermediate/audited_brief.md"),
        "reader_brief": (workspace / "output" / "brief.md", "output/brief.md"),
    }
    summary: dict[str, Any] = {}
    for key, (path, artifact) in targets.items():
        text = _read_optional_text(path)
        if text is None or not text.strip():
            summary[key] = {
                "status": "not_available",
                "target_artifact": artifact,
                "graph_present": graph_present,
                "reason": f"{artifact}:missing",
                "summary_counts": {},
            }
            continue
        summary[key] = project_atomic_reader_text_from_workspace(
            workspace=workspace,
            target_text=text,
            target_artifact=artifact,
        )
    return summary


def _runtime_summary(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload") if result.get("status") == "present" else None
    if not isinstance(payload, dict):
        return {"present": False, "run_id": None, "runtime": None, "recipe": None}
    return {
        "present": True,
        "run_id": payload.get("run_id"),
        "runtime": payload.get("runtime"),
        "recipe": payload.get("recipe"),
        "schema_version": payload.get("schema_version"),
    }


def _workflow_summary(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload") if result.get("status") == "present" else None
    if not isinstance(payload, dict):
        return {
            "present": False,
            "current_stage": None,
            "blocked": None,
            "blocking_reason": None,
        }
    return {
        "present": True,
        "current_stage": payload.get("current_stage"),
        "blocked": payload.get("blocked"),
        "blocking_reason": payload.get("blocking_reason"),
        "next_allowed_decisions": payload.get("next_allowed_decisions") or [],
        "run_integrity": _run_integrity_summary(payload),
    }


def _run_integrity_summary(workflow: dict[str, Any]) -> dict[str, Any]:
    return project_for_read(
        interpret_run_integrity(
            workflow.get("run_integrity"),
            field_present="run_integrity" in workflow,
        )
    )


def _artifact_summary(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload") if result.get("status") == "present" else None
    records = (payload or {}).get("artifacts") if isinstance(payload, dict) else None
    if isinstance(records, dict):
        iterable = list(records.values())
    elif isinstance(records, list):
        iterable = records
    else:
        iterable = []
    counts = {
        "present": bool(isinstance(payload, dict)),
        "artifact_count": len(iterable),
        "valid_count": 0,
        "invalid_count": 0,
        "missing_count": 0,
        "expected_count": 0,
        "ready_count": 0,
        "stale_count": 0,
    }
    for record in iterable:
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "")
        if status == "valid":
            counts["valid_count"] += 1
        elif status == "invalid":
            counts["invalid_count"] += 1
        elif status == "missing":
            counts["missing_count"] += 1
        elif status == "expected":
            counts["expected_count"] += 1
        elif status == "stale":
            counts["stale_count"] += 1
        elif status in {"present", "ready"}:
            counts["ready_count"] += 1
    return counts


def _event_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False, "event_count": 0, "corrupt_count": 0, "recent_events": []}
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        return {
            "present": True,
            "event_count": 0,
            "corrupt_count": 1,
            "recent_events": [],
            "error": str(exc),
        }
    corrupt = 0
    recent: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if not isinstance(event, dict):
            corrupt += 1
            continue
        recent.append({
            "event_type": event.get("event_type"),
            "stage_id": event.get("stage_id"),
            "artifact_id": event.get("artifact_id"),
            "decision": event.get("decision"),
            "created_at": event.get("created_at"),
        })
    return {
        "present": True,
        "event_count": len(lines),
        "corrupt_count": corrupt,
        "recent_events": recent[-5:],
    }


def _event_records_best_effort(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            records.append(event)
    return records


def _quality_gate_summary(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload") if result.get("status") == "present" else None
    if not isinstance(payload, dict):
        return {"present": False, "status": None, "blocking_findings": 0, "schema_warnings": []}
    warnings: list[str] = []
    findings = payload.get("findings") or []
    if not isinstance(payload.get("findings", []), list):
        warnings.append("findings is not a list")
        findings = []
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        warnings.append("metadata is not an object")
        metadata = {}
    blocking = [
        finding
        for finding in findings
        if isinstance(finding, dict)
        and (finding.get("blocking") is True or finding.get("blocking_level") == "blocking")
    ]
    return {
        "present": True,
        "status": "unknown" if warnings else payload.get("status"),
        "raw_status": payload.get("status"),
        "gate_stage_id": metadata.get("gate_stage_id"),
        "blocking_findings": len(blocking),
        "schema_warnings": warnings,
    }


def _select_quality_gate_result(
    *,
    workflow: dict[str, Any],
    legacy: dict[str, Any],
    auditor: dict[str, Any],
    finalize: dict[str, Any],
) -> dict[str, Any]:
    current_stage = workflow.get("current_stage")
    if current_stage == "finalize" and finalize.get("status") == "present":
        return finalize
    if current_stage == "auditor" and auditor.get("status") == "present":
        return auditor
    if auditor.get("status") == "present":
        return auditor
    if finalize.get("status") == "present":
        return finalize
    return legacy


def _reader_clean_summary(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload") if result.get("status") == "present" else None
    if not isinstance(payload, dict):
        return {"present": False, "status": None, "finding_count": 0}
    reader_clean = payload.get("reader_clean")
    if not isinstance(reader_clean, dict):
        return {"present": True, "status": "unknown", "finding_count": 0}
    findings = reader_clean.get("sample_findings") or []
    return {
        "present": True,
        "status": reader_clean.get("status"),
        "finding_count": len(findings) if isinstance(findings, list) else 0,
    }


def _improvement_summary(workspace: Path, manifest_result: dict[str, Any]) -> dict[str, Any]:
    manifest = manifest_result.get("payload") if manifest_result.get("status") == "present" else {}
    improvement = manifest.get("improvement") if isinstance(manifest, dict) else {}
    if not isinstance(improvement, dict):
        improvement = {}
    return {
        "ledger_present": (workspace / "improvement" / "ledger.jsonl").exists(),
        "memory_present": (workspace / "improvement" / "memory.md").exists(),
        "snapshot_present": (workspace / INTERMEDIATE_DIR / "improvement_memory_snapshot.md").exists(),
        "ledger_sha256": improvement.get("ledger_sha256"),
        "memory_sha256": improvement.get("memory_sha256"),
        "snapshot_sha256": improvement.get("snapshot_sha256"),
        "snapshot_path": improvement.get("snapshot_path"),
        "materialized_entry_ids": improvement.get("materialized_entry_ids") or [],
    }


def _feedback_summary(issues_result: dict[str, Any], plan_result: dict[str, Any]) -> dict[str, Any]:
    issues_payload = issues_result.get("payload") if issues_result.get("status") == "present" else {}
    plan_payload = plan_result.get("payload") if plan_result.get("status") == "present" else {}
    issues = issues_payload.get("issues") if isinstance(issues_payload, dict) else []
    plans = plan_payload.get("repair_plans") if isinstance(plan_payload, dict) else []
    if not isinstance(issues, list):
        issues = []
    if not isinstance(plans, list):
        plans = []
    open_statuses = {"open", "planned", "in_progress", "blocked", "triage"}
    blocking_severities = {"blocking"}
    return {
        "issues_present": issues_result.get("status") == "present",
        "issue_count": len(issues),
        "open_count": sum(1 for item in issues if isinstance(item, dict) and item.get("status") in open_statuses),
        "blocking_count": sum(
            1
            for item in issues
            if isinstance(item, dict)
            and (
                item.get("severity") in blocking_severities
                or item.get("blocking_level") in blocking_severities
            )
        ),
        "triage_count": sum(1 for item in issues if isinstance(item, dict) and item.get("status") == "triage"),
        "repair_plan_present": plan_result.get("status") == "present",
        "repair_plan_count": len(plans),
    }


def _suggested_next_command(workspace: Path, status: dict[str, Any]) -> str:
    workflow = status.get("workflow") or {}
    gate = status.get("quality_gate") or {}
    fact_layer_import = status.get("fact_layer_import") or {}
    experiment_080 = status.get("experiment_080") or {}
    if not (status.get("runtime") or {}).get("present"):
        return f"multi-agent-brief run --workspace {workspace} --runtime claude"
    if workflow.get("blocked"):
        return f"multi-agent-brief state show --workspace {workspace} --json"
    if (
        experiment_080.get("assessment_target") == "auditable_brief"
        and experiment_080.get("target_complete") is True
    ):
        condition = experiment_080.get("condition") or "<condition>"
        return (
            "multi-agent-brief experiments 080 register-run "
            f"--case <case_dir> --condition {condition} --workspace {workspace} "
            "--output <run_record.json>"
        )
    if experiment_080.get("assessment_target") == "auditable_brief":
        return f"multi-agent-brief status --workspace {workspace} --json"
    current_stage = workflow.get("current_stage")
    if fact_layer_import.get("status") == "valid" and current_stage == "analyst":
        return f"multi-agent-brief run --workspace {workspace} --recipe fast-rerun --skip-doctor"
    if current_stage == "finalize":
        return f"/mabw deliver {workspace}"
    quality_closeout = status.get("quality_panel_closeout") or {}
    if quality_closeout.get("status") in {"recommended", "stale_or_invalid"}:
        return f"briefloop quality summarize --workspace {workspace}"
    if current_stage == "auditor" and gate.get("status") != "pass":
        return f"multi-agent-brief gates check --workspace {workspace} --stage auditor"
    if current_stage:
        return f"/generate-brief {workspace}"
    return f"multi-agent-brief run --workspace {workspace} --runtime claude --skip-doctor"
