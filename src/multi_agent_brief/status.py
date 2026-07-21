"""Read-only workspace status summary for writer-facing product commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.contracts.agent_artifact_intake import (
    AGENT_ARTIFACT_IDS,
    validate_registry_intake_context,
    validate_workspace_intake_consumption_context,
)
from multi_agent_brief.contracts.target_contract import (
    load_experiment_080_condition_metadata,
    project_assessment_target_status,
)
from multi_agent_brief.orchestrator.fact_layer_import import summarize_fact_layer_import
from multi_agent_brief.orchestrator_contract import (
    HISTORICAL_READ_ONLY_RUNTIMES,
    VALID_RUNTIMES,
)
from multi_agent_brief.outputs.atomic_reader_projection import (
    project_atomic_reader_text_from_workspace,
)
from multi_agent_brief.product.materiality_selection import (
    project_workspace_materiality_selection,
)
from multi_agent_brief.product.policy_projection import project_workspace_policy_profile
from multi_agent_brief.product.quality_closeout import quality_panel_closeout_projection
from multi_agent_brief.product.template_conformance import (
    project_workspace_report_template_conformance,
)
from multi_agent_brief.product.template_projection import (
    project_workspace_report_template,
)
from multi_agent_brief.product.template_render_plan import (
    project_workspace_report_template_render_plan,
)
from multi_agent_brief.product.trajectory_regulation import (
    project_workspace_trajectory_regulation,
)


INTERMEDIATE_DIR = Path("output/intermediate")

_STAGE_PROGRESS_LABELS = {
    "doctor": "prepare sources",
    "source-discovery": "prepare sources",
    "input-governance": "prepare sources",
    "scout": "select claims",
    "screener": "select claims",
    "claim-ledger": "select claims",
    "analyst": "draft brief",
    "editor": "edit brief",
    "auditor": "audit brief",
    "finalize": "finalize delivery",
}

def build_workspace_status(workspace: str | Path) -> dict[str, Any]:
    """Return a read-only status summary without refreshing runtime state.

    This helper deliberately avoids orchestrator runtime helpers such as
    ``state check`` or ``initialize_runtime_state``. It only reads existing
    workspace files and reports missing/corrupt surfaces as stale or unknown.
    """

    ws = Path(workspace).expanduser().resolve()
    if (ws / "briefloop.db").exists() or (ws / "briefloop.db").is_symlink():
        from multi_agent_brief.runtime_host_v2.projections import (
            build_store_status_projection,
        )

        return build_store_status_projection(ws)
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
        "policy_profile": {},
        "report_template": {},
        "report_template_conformance": {},
        "report_template_render_plan": {},
        "trajectory_regulation": {},
        "materiality_selection": {},
        "quality_panel_closeout": {},
        "progress": {},
        "stale_or_unknown": [],
        "suggested_next_command": None,
    }
    if not payload["ok"]:
        payload["error"] = f"Workspace directory not found: {ws}"
        payload["suggested_next_command"] = "briefloop init <workspace> --demo"
        payload["progress"] = _progress_summary(payload)
        return payload

    manifest = _read_json(ws / INTERMEDIATE_DIR / "runtime_manifest.json")
    workflow = _read_json(ws / INTERMEDIATE_DIR / "workflow_state.json")
    quality_gate = _read_json(ws / INTERMEDIATE_DIR / "quality_gate_report.json")
    auditor_quality_gate = _read_json(
        ws / INTERMEDIATE_DIR / "gates" / "auditor_quality_gate_report.json"
    )
    finalize_quality_gate = _read_json(
        ws / INTERMEDIATE_DIR / "gates" / "finalize_quality_gate_report.json"
    )
    finalize_report = _read_json(ws / INTERMEDIATE_DIR / "finalize_report.json")
    feedback_issues = _read_json(ws / INTERMEDIATE_DIR / "feedback_issues.json")
    repair_plan = _read_json(ws / INTERMEDIATE_DIR / "repair_plan.json")

    event_log_path = ws / INTERMEDIATE_DIR / "event_log.jsonl"
    event_records = _event_records_best_effort(event_log_path)
    workflow_payload = (
        workflow.get("payload") if workflow.get("status") == "present" else None
    )

    manifest_payload = (
        manifest.get("payload") if manifest.get("status") == "present" else None
    )
    # The legacy artifact-registry stack is retired; without it no run identity
    # can be trusted, so registry-bound projections see an absent registry.
    expected_run_id = ""
    payload["runtime"] = _runtime_summary(manifest)
    payload["workflow"] = _workflow_summary(workflow)
    payload["artifacts"] = _artifact_summary()
    registry_payload = None
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
        finalize_report=finalize_report.get("payload")
        if finalize_report.get("status") == "present"
        else None,
        artifact_registry=registry_payload,
    )
    payload["improvement"] = _improvement_summary(ws, manifest)
    payload["feedback"] = _feedback_summary(feedback_issues, repair_plan)
    payload["experiment_080"] = project_assessment_target_status(
        condition_metadata=load_experiment_080_condition_metadata(ws),
        workflow_state=workflow_payload if isinstance(workflow_payload, dict) else None,
        artifact_registry=registry_payload,
        auditor_gate_report=auditor_quality_gate.get("payload")
        if auditor_quality_gate.get("status") == "present"
        else None,
        event_records=event_records,
    )
    payload["fact_layer_import"] = summarize_fact_layer_import(
        manifest_payload if isinstance(manifest_payload, dict) else None,
        workflow_payload if isinstance(workflow_payload, dict) else None,
        workspace=ws,
    )
    payload["atomic_reader_projection"] = _atomic_reader_projection_summary(ws)
    payload["policy_profile"] = project_workspace_policy_profile(ws)
    payload["materiality_selection"] = project_workspace_materiality_selection(
        ws,
        policy_profile=payload["policy_profile"],
        artifact_registry=registry_payload,
        expected_run_id=expected_run_id,
    )
    payload["report_template"] = project_workspace_report_template(ws)
    payload["report_template_conformance"] = (
        project_workspace_report_template_conformance(ws)
    )
    payload["report_template_render_plan"] = (
        project_workspace_report_template_render_plan(ws)
    )
    payload["trajectory_regulation"] = project_workspace_trajectory_regulation(
        ws,
        workflow_state=workflow_payload if isinstance(workflow_payload, dict) else None,
        event_records=event_records,
        event_log_present=event_log_path.exists(),
        event_log_corrupt_count=int(payload["events"].get("corrupt_count") or 0),
        run_id=(manifest_payload or {}).get("run_id")
        if isinstance(manifest_payload, dict)
        else None,
    )

    stale = payload["stale_or_unknown"]
    artifact_summary = payload["artifacts"]
    if artifact_summary.get("registry_status") != "valid":
        stale.append(
            f"artifact_registry {artifact_summary.get('registry_status') or 'unavailable'}: "
            f"{artifact_summary.get('registry_reason_code') or 'invalid_control_context'}"
        )
    for label, result in (
        ("runtime_manifest", manifest),
        ("workflow_state", workflow),
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
    payload["progress"] = _progress_summary(payload)
    return payload


def format_workspace_status(status: dict[str, Any]) -> str:
    """Format a concise human-readable status report."""

    if status.get("authority") == "sqlite_control_store":
        action = status.get("next_action") or {}
        return "\n".join(
            [
                f"[status] workspace: {status.get('workspace')}",
                "[status] authority: sqlite_control_store",
                f"[status] run_id: {status.get('run_id')}",
                f"[status] runtime: {status.get('runtime')}",
                f"[status] store_revision: {status.get('store_revision')}",
                f"[status] current_stage: {status.get('current_stage') or 'none'}",
                f"[status] terminal_state: {status.get('terminal_state')}",
                f"[status] package_ready: {status.get('package_ready')}",
                f"[status] delivered: {status.get('delivered')}",
                (
                    "[status] next_action: "
                    f"{action.get('action_kind')}/{action.get('effect_kind')}"
                ),
            ]
        )

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
    policy_profile = status.get("policy_profile") or {}
    report_template = status.get("report_template") or {}
    report_template_conformance = status.get("report_template_conformance") or {}
    report_template_render_plan = status.get("report_template_render_plan") or {}
    trajectory_regulation = status.get("trajectory_regulation") or {}
    materiality_selection = status.get("materiality_selection") or {}
    quality_panel_closeout = status.get("quality_panel_closeout") or {}
    events = status.get("events") or {}
    progress = status.get("progress") or {}

    lines.extend(
        [
            f"[status] run_id: {runtime.get('run_id') or 'unknown'}",
            f"[status] runtime: {runtime.get('runtime') or 'unknown'}",
            f"[status] runtime_identity: {runtime.get('identity_status') or 'unknown'}",
            f"[status] recipe: {runtime.get('recipe') or 'unknown'}",
            f"[status] current_stage: {workflow.get('current_stage') or 'unknown'}",
            f"[status] blocked: {workflow.get('blocked')}",
            f"[status] blocking_reason: {workflow.get('blocking_reason') or ''}",
            _format_progress_line(progress),
            _format_trajectory_decision_narrowing_line(workflow),
            (
                "[status] artifacts: "
                f"registry_status={artifacts.get('registry_status') or 'unavailable'} "
                f"registry_reason={artifacts.get('registry_reason_code') or 'none'} "
                f"valid={artifacts.get('valid_count', 0)} "
                f"invalid={artifacts.get('invalid_count', 0)} "
                f"missing={artifacts.get('missing_count', 0)} "
                f"expected={artifacts.get('expected_count', 0)} "
                f"stale={artifacts.get('stale_count', 0)}"
            ),
            _format_intake_projection_line(artifacts.get("intake")),
            f"[status] events: count={events.get('event_count', 0)} corrupt={events.get('corrupt_count', 0)}",
            _format_fact_layer_import_line(fact_layer_import),
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
    if policy_profile.get("status") not in {None, "not_available"}:
        errors = (
            policy_profile.get("errors")
            if isinstance(policy_profile.get("errors"), list)
            else []
        )
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
        errors = (
            report_template.get("errors")
            if isinstance(report_template.get("errors"), list)
            else []
        )
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
        selected_source = (
            report_template_render_plan.get("selected_source_artifact") or "none"
        )
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
    for marker in status.get("stale_or_unknown") or []:
        lines.append(f"[status] stale_or_unknown: {marker}")
    lines.append(f"[status] suggested_next: {status.get('suggested_next_command')}")
    return "\n".join(lines)


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
            reasons = (
                experiment.get("reasons")
                if isinstance(experiment.get("reasons"), list)
                else []
            )
            first_reason = (
                str(reasons[0]) if reasons else "target contract not yet satisfied"
            )
            lines.append(
                f"[status] target_incomplete: auditable_brief reason={first_reason}"
            )
    return lines


def _format_progress_line(progress: dict[str, Any]) -> str:
    return (
        "[status] progress: "
        f"{progress.get('status') or 'unknown'} "
        f'current_work="{progress.get("current_work") or "check workspace"}" '
        f'message="{progress.get("message") or ""}" '
        f'next="{progress.get("next_command") or ""}"'
    )


def _format_fact_layer_import_line(summary: dict[str, Any]) -> str:
    if summary.get("status") == "valid":
        freshness = (
            summary.get("freshness_at_import")
            if isinstance(summary.get("freshness_at_import"), dict)
            else {}
        )
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
        return {
            "status": "error",
            "path": str(path),
            "payload": None,
            "error": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            "status": "error",
            "path": str(path),
            "payload": None,
            "error": "JSON root is not an object",
        }
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
        "audited_brief": (
            workspace / INTERMEDIATE_DIR / "audited_brief.md",
            "output/intermediate/audited_brief.md",
        ),
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
        return {
            "present": False,
            "run_id": None,
            "runtime": None,
            "recipe": None,
            "identity_status": "not_initialized",
            "runtime_choices": list(VALID_RUNTIMES),
            "requires_reset": False,
        }
    runtime = payload.get("runtime")
    if runtime in VALID_RUNTIMES:
        identity_status = "canonical"
    elif runtime in HISTORICAL_READ_ONLY_RUNTIMES:
        identity_status = "historical_read_only"
    else:
        identity_status = "invalid"
    return {
        "present": True,
        "run_id": payload.get("run_id"),
        "runtime": runtime,
        "recipe": payload.get("recipe"),
        "schema_version": payload.get("schema_version"),
        "identity_status": identity_status,
        "runtime_choices": list(VALID_RUNTIMES),
        "requires_reset": identity_status != "canonical",
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
        "trajectory_regulation": payload.get("trajectory_regulation")
        if isinstance(payload.get("trajectory_regulation"), dict)
        else {},
    }


def _format_trajectory_decision_narrowing_line(workflow: dict[str, Any]) -> str:
    narrowing = workflow.get("trajectory_regulation")
    if (
        not isinstance(narrowing, dict)
        or narrowing.get("status") != "decision_narrowed"
    ):
        return "[status] trajectory_decision_narrowing: none"
    reasons = (
        narrowing.get("reasons") if isinstance(narrowing.get("reasons"), list) else []
    )
    allowed = workflow.get("next_allowed_decisions")
    allowed = (
        allowed if isinstance(allowed, list) else narrowing.get("allowed_decisions")
    )
    allowed = allowed if isinstance(allowed, list) else []
    return (
        "[status] trajectory_decision_narrowing: "
        f"decision_narrowed stage={narrowing.get('stage_id') or 'unknown'} "
        f"allowed={','.join(str(item) for item in allowed)} "
        f"reasons={','.join(str(item) for item in reasons)}"
    )


def _artifact_summary() -> dict[str, Any]:
    """Legacy artifact-registry interpretation is retired with the runtime-state
    stack; the registry is reported as never materialized."""

    return {
        "present": False,
        "registry_status": "missing",
        "registry_reason_code": "artifact_registry_not_materialized",
        "registry_reason": "The artifact registry has not been materialized.",
        "artifact_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "missing_count": 0,
        "expected_count": 0,
        "ready_count": 0,
        "stale_count": 0,
        "intake": _intake_projection_summary(None, expected_run_id=""),
    }


def _intake_projection_summary(
    registry: dict[str, Any] | None,
    *,
    expected_run_id: str,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "present": False,
        "valid": None,
        "projection_count": 0,
        "normalized_artifact_count": 0,
        "normalization_count": 0,
        "fatal_finding_count": 0,
        "invalid_projection_count": 0,
        "stale_projection_count": 0,
        "consumable": None,
        "artifacts": [],
        "reasons": [],
    }
    if not isinstance(registry, dict):
        return summary
    records = registry.get("artifacts")
    if not isinstance(records, dict):
        return summary

    artifacts: list[dict[str, Any]] = []
    context_reasons: list[str] = []
    for artifact_id in sorted(AGENT_ARTIFACT_IDS):
        record = records.get(artifact_id)
        if not isinstance(record, dict) or "intake_projection" not in record:
            continue
        projection = record.get("intake_projection")
        reasons = (
            validate_registry_intake_context(
                registry,
                expected_run_id=expected_run_id,
                artifact_id=artifact_id,
            )
            if expected_run_id
            else [
                "runtime_manifest run_id is unavailable for intake projection binding"
            ]
        )
        consumption_reasons = (
            validate_workspace_intake_consumption_context(
                registry,
                expected_run_id=expected_run_id,
                bundle=None,
                artifact_id=artifact_id,
            )
            if expected_run_id
            else [
                "runtime_manifest run_id is unavailable for intake consumption binding"
            ]
        )
        normalization_count = (
            projection.get("normalization_count") if isinstance(projection, dict) else 0
        )
        fatal_finding_count = (
            projection.get("fatal_finding_count") if isinstance(projection, dict) else 0
        )
        normalization_count = (
            normalization_count if isinstance(normalization_count, int) else 0
        )
        fatal_finding_count = (
            fatal_finding_count if isinstance(fatal_finding_count, int) else 0
        )
        findings = projection.get("findings") if isinstance(projection, dict) else []
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "projection_valid": not reasons,
                "consumable": not consumption_reasons,
                "artifact_status": record.get("status"),
                "validation_result": record.get("validation_result"),
                "transform_version": projection.get("transform_version")
                if isinstance(projection, dict)
                else None,
                "normalization_count": normalization_count,
                "fatal_finding_count": fatal_finding_count,
                "findings": findings if isinstance(findings, list) else [],
                "reasons": reasons,
                "consumption_reasons": consumption_reasons,
            }
        )
        summary["normalization_count"] += normalization_count
        summary["fatal_finding_count"] += fatal_finding_count
        if normalization_count > 0:
            summary["normalized_artifact_count"] += 1
        if reasons:
            summary["invalid_projection_count"] += 1
            context_reasons.extend(reasons)
        if record.get("status") == "stale":
            summary["stale_projection_count"] += 1

    summary["artifacts"] = artifacts
    summary["projection_count"] = len(artifacts)
    summary["reasons"] = list(dict.fromkeys(context_reasons))
    if artifacts:
        summary["present"] = True
        summary["valid"] = not context_reasons
        summary["consumable"] = all(
            artifact.get("consumable") is True for artifact in artifacts
        )
    return summary


def _format_intake_projection_line(value: Any) -> str:
    intake = value if isinstance(value, dict) else {}
    state = (
        "not_available"
        if intake.get("present") is not True
        else "available"
        if intake.get("valid") is True and intake.get("consumable") is True
        else "stale"
        if intake.get("valid") is True and intake.get("stale_projection_count", 0) > 0
        else "invalid"
    )
    return (
        "[status] intake: "
        f"{state} "
        f"projections={intake.get('projection_count', 0)} "
        f"normalized={intake.get('normalized_artifact_count', 0)} "
        f"normalizations={intake.get('normalization_count', 0)} "
        f"fatal={intake.get('fatal_finding_count', 0)} "
        f"invalid_projections={intake.get('invalid_projection_count', 0)} "
        f"stale_projections={intake.get('stale_projection_count', 0)}"
    )


def _event_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "present": False,
            "event_count": 0,
            "corrupt_count": 0,
            "recent_events": [],
        }
    try:
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError) as exc:
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
        recent.append(
            {
                "event_type": event.get("event_type"),
                "stage_id": event.get("stage_id"),
                "artifact_id": event.get("artifact_id"),
                "decision": event.get("decision"),
                "created_at": event.get("created_at"),
            }
        )
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
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError):
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
        return {
            "present": False,
            "status": None,
            "blocking_findings": 0,
            "schema_warnings": [],
        }
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
        and (
            finding.get("blocking") is True
            or finding.get("blocking_level") == "blocking"
        )
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


def _improvement_summary(
    workspace: Path, manifest_result: dict[str, Any]
) -> dict[str, Any]:
    manifest = (
        manifest_result.get("payload")
        if manifest_result.get("status") == "present"
        else {}
    )
    improvement = manifest.get("improvement") if isinstance(manifest, dict) else {}
    if not isinstance(improvement, dict):
        improvement = {}
    return {
        "ledger_present": (workspace / "improvement" / "ledger.jsonl").exists(),
        "memory_present": (workspace / "improvement" / "memory.md").exists(),
        "snapshot_present": (
            workspace / INTERMEDIATE_DIR / "improvement_memory_snapshot.md"
        ).exists(),
        "ledger_sha256": improvement.get("ledger_sha256"),
        "memory_sha256": improvement.get("memory_sha256"),
        "snapshot_sha256": improvement.get("snapshot_sha256"),
        "snapshot_path": improvement.get("snapshot_path"),
        "materialized_entry_ids": improvement.get("materialized_entry_ids") or [],
    }


def _feedback_summary(
    issues_result: dict[str, Any], plan_result: dict[str, Any]
) -> dict[str, Any]:
    issues_payload = (
        issues_result.get("payload") if issues_result.get("status") == "present" else {}
    )
    plan_payload = (
        plan_result.get("payload") if plan_result.get("status") == "present" else {}
    )
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
        "open_count": sum(
            1
            for item in issues
            if isinstance(item, dict) and item.get("status") in open_statuses
        ),
        "blocking_count": sum(
            1
            for item in issues
            if isinstance(item, dict)
            and (
                item.get("severity") in blocking_severities
                or item.get("blocking_level") in blocking_severities
            )
        ),
        "triage_count": sum(
            1
            for item in issues
            if isinstance(item, dict) and item.get("status") == "triage"
        ),
        "repair_plan_present": plan_result.get("status") == "present",
        "repair_plan_count": len(plans),
    }


def _suggested_next_command(workspace: Path, status: dict[str, Any]) -> str:
    workflow = status.get("workflow") or {}
    gate = status.get("quality_gate") or {}
    fact_layer_import = status.get("fact_layer_import") or {}
    experiment_080 = status.get("experiment_080") or {}
    runtime = status.get("runtime") or {}
    runtime_identity = runtime.get("identity_status")
    runtime_value = runtime.get("runtime")
    runtime_choices = "|".join(VALID_RUNTIMES)
    if runtime_identity == "historical_read_only":
        return (
            f"briefloop state init --workspace {workspace} --reset-state "
            f"--runtime <{runtime_choices}>"
        )
    if runtime_identity == "invalid":
        return f"briefloop state show --workspace {workspace} --json"
    if not runtime.get("present"):
        return f"briefloop run --workspace {workspace} --runtime <{runtime_choices}>"
    if workflow.get("blocked"):
        return f"briefloop state show --workspace {workspace} --json"
    if (
        experiment_080.get("assessment_target") == "auditable_brief"
        and experiment_080.get("target_complete") is True
    ):
        condition = experiment_080.get("condition") or "<condition>"
        return (
            "briefloop experiments 080 register-run "
            f"--case <case_dir> --condition {condition} --workspace {workspace} "
            "--output <run_record.json>"
        )
    if experiment_080.get("assessment_target") == "auditable_brief":
        return f"briefloop status --workspace {workspace} --json"
    current_stage = workflow.get("current_stage")
    if fact_layer_import.get("status") == "valid" and current_stage == "analyst":
        return (
            f"briefloop run --workspace {workspace} --runtime {runtime_value} "
            "--recipe fast-rerun --skip-doctor"
        )
    quality_closeout = status.get("quality_panel_closeout") or {}
    if quality_closeout.get("status") in {"recommended", "stale_or_invalid"}:
        return f"briefloop quality summarize --workspace {workspace}"
    if current_stage == "finalize":
        return f"/briefloop deliver {workspace}"
    if current_stage == "auditor" and gate.get("status") != "pass":
        return f"briefloop gates check --workspace {workspace} --stage auditor"
    if current_stage:
        return f"briefloop run --workspace {workspace} --runtime {runtime_value}"
    return (
        f"briefloop run --workspace {workspace} --runtime {runtime_value} --skip-doctor"
    )


def _progress_summary(status: dict[str, Any]) -> dict[str, Any]:
    workflow = (
        status.get("workflow") if isinstance(status.get("workflow"), dict) else {}
    )
    runtime = status.get("runtime") if isinstance(status.get("runtime"), dict) else {}
    events = status.get("events") if isinstance(status.get("events"), dict) else {}
    quality_closeout = (
        status.get("quality_panel_closeout")
        if isinstance(status.get("quality_panel_closeout"), dict)
        else {}
    )
    suggested_next = str(status.get("suggested_next_command") or "")
    current_stage = _text(workflow.get("current_stage"))
    current_work = _stage_progress_label(current_stage)
    base = {
        "schema_version": "briefloop.status_progress.v1",
        "runtime_effect": "read_only",
        "source": "workspace_status_projection",
        "current_stage": current_stage or None,
        "current_work": current_work,
        "next_command": suggested_next,
    }
    if not status.get("ok"):
        return {
            **base,
            "status": "workspace_missing",
            "current_work": "create workspace",
            "message": "Workspace folder was not found; create or choose a workspace before running BriefLoop.",
        }
    if int(events.get("corrupt_count") or 0) > 0:
        return {
            **base,
            "status": "needs_operator_action",
            "current_work": "check run record",
            "message": "The event log has unreadable records; inspect JSON status or state before continuing.",
        }
    if not runtime.get("present"):
        return {
            **base,
            "status": "not_started",
            "current_work": "create handoff",
            "message": "Create or refresh the BriefLoop handoff before stage work.",
        }
    narrowing = (
        workflow.get("trajectory_regulation")
        if isinstance(workflow.get("trajectory_regulation"), dict)
        else {}
    )
    if narrowing.get("status") == "decision_narrowed":
        return {
            **base,
            "status": "human_review_needed",
            "message": "Retry or repair budget is exhausted; request human review or block the run.",
        }
    if workflow.get("blocked"):
        return {
            **base,
            "status": "blocked",
            "message": "The run is blocked; inspect state and repair guidance before continuing.",
        }
    if quality_closeout.get("status") in {"recommended", "stale_or_invalid"}:
        return {
            **base,
            "status": "needs_quality_package",
            "current_work": "build quality package",
            "message": "Generate or refresh the Quality Panel and summary before closeout.",
        }
    if current_stage:
        return {
            **base,
            "status": "ready_for_operator",
            "message": f"Continue the {current_work} step through the suggested command or handoff.",
        }
    return {
        **base,
        "status": "unknown",
        "message": "Runtime state is incomplete; inspect status JSON or create a handoff before continuing.",
    }


def _stage_progress_label(stage_id: str) -> str:
    return _STAGE_PROGRESS_LABELS.get(stage_id, "check workspace")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""
