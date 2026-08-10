"""Read-only workspace status summary for writer-facing product commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

_STALE_MARKER_CANDIDATES = (
    "config.yaml",
    "sources.yaml",
    "report_spec.yaml",
)


def build_workspace_status(workspace: str | Path) -> dict[str, Any]:
    """Return a read-only status summary without refreshing runtime state.

    SQLite workspaces project from the ControlStore. A workspace without a
    Store has no runtime authority; status reports the workspace-file product
    projections only. The legacy JSON control plane is removed, so no
    workflow_state / artifact_registry / event_log / runtime_manifest /
    finalize_report reads happen here.
    """

    ws = Path(workspace).expanduser().resolve()
    if (ws / "briefloop.db").exists() or (ws / "briefloop.db").is_symlink():
        from multi_agent_brief.runtime_host_v2.projections import (
            build_store_status_projection,
        )

        return build_store_status_projection(ws)
    return _workspace_file_status(ws)


def _workspace_file_status(ws: Path) -> dict[str, Any]:
    ok = ws.exists() and ws.is_dir()
    policy_profile = project_workspace_policy_profile(ws)
    report_template = project_workspace_report_template(ws)
    template_conformance = project_workspace_report_template_conformance(ws)
    template_render_plan = project_workspace_report_template_render_plan(ws)
    trajectory = project_workspace_trajectory_regulation(ws)
    materiality = project_workspace_materiality_selection(
        ws,
        policy_profile=policy_profile,
    )
    closeout = quality_panel_closeout_projection(workspace=ws)
    atomic_projection = _atomic_reader_projection_summary(ws)
    stale = [
        f"{name} missing"
        for name in _STALE_MARKER_CANDIDATES
        if not (ws / name).exists()
    ]
    suggested = (
        f"briefloop run --workspace {ws} --runtime codex"
        if ok
        else f"briefloop init {ws} --demo"
    )
    return {
        "ok": ok,
        "workspace": str(ws),
        "read_only": True,
        "authority": "fresh",
        "policy_profile": policy_profile,
        "report_template": report_template,
        "report_template_conformance": template_conformance,
        "report_template_render_plan": template_render_plan,
        "trajectory_regulation": trajectory,
        "materiality_selection": materiality,
        "quality_panel_closeout": closeout,
        "atomic_reader_projection": atomic_projection,
        "stale_or_unknown": stale,
        "suggested_next_command": suggested,
        "progress": _progress_summary(ok=ok, suggested=suggested),
    }


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

    policy_profile = status.get("policy_profile") or {}
    if isinstance(policy_profile, dict) and policy_profile.get("status") not in {
        None,
        "not_available",
    }:
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
    report_template = status.get("report_template") or {}
    if isinstance(report_template, dict) and report_template.get("status") not in {
        None,
        "not_available",
    }:
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
    template_conformance = status.get("report_template_conformance") or {}
    if isinstance(template_conformance, dict) and template_conformance.get(
        "status"
    ) not in {None, "not_available"}:
        counts = template_conformance.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] report_template_conformance: "
            f"{template_conformance.get('status')} "
            f"present_targets={counts.get('present_target_count', 0)} "
            f"warnings={counts.get('warning_target_count', 0)} "
            f"missing_sections={counts.get('missing_section_count', 0)} "
            f"out_of_order={counts.get('out_of_order_section_count', 0)} "
            f"extra_headings={counts.get('extra_heading_count', 0)} "
            f"reader_contract_warnings={counts.get('reader_block_warning_count', 0)} "
            "boundary=projection_only "
            "runtime_effect=none"
        )
    render_plan = status.get("report_template_render_plan") or {}
    if isinstance(render_plan, dict) and render_plan.get("status") not in {
        None,
        "not_available",
    }:
        counts = render_plan.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        selected_source = render_plan.get("selected_source_artifact") or "none"
        lines.append(
            "[status] report_template_render_plan: "
            f"{render_plan.get('status')} "
            f"source={selected_source} "
            f"sections={counts.get('section_count', 0)} "
            f"unresolved={counts.get('unresolved_section_count', 0)} "
            f"targets={counts.get('planned_delivery_target_count', 0)} "
            "boundary=projection_only "
            "runtime_effect=none"
        )
    trajectory = status.get("trajectory_regulation") or {}
    if isinstance(trajectory, dict) and trajectory.get("status") not in {
        None,
        "not_available",
    }:
        counts = trajectory.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        actions = trajectory.get("recommended_actions")
        actions = actions if isinstance(actions, list) else []
        lines.append(
            "[status] trajectory_regulation: "
            f"{trajectory.get('status')} "
            f"retry_events={counts.get('retry_stage_count', 0)} "
            f"repair_starts={counts.get('repair_started_count', 0)} "
            f"actions={len(actions)} "
            "boundary=projection_only "
            "runtime_effect=none"
        )
    materiality = status.get("materiality_selection") or {}
    if isinstance(materiality, dict) and materiality.get("status") not in {
        None,
        "not_available",
    }:
        counts = materiality.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] materiality_selection: "
            f"{materiality.get('status')} "
            f"findings={counts.get('finding_count', 0)} "
            f"human_review={counts.get('human_review_recommended_count', 0)} "
            "boundary=projection_only "
            "runtime_effect=none"
        )
    atomic = status.get("atomic_reader_projection") or {}
    audited_atomic = atomic.get("audited_brief") if isinstance(atomic, dict) else None
    if isinstance(audited_atomic, dict) and audited_atomic.get("status") not in {
        None,
        "not_available",
    }:
        counts = audited_atomic.get("summary_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines.append(
            "[status] atomic_reader_projection: "
            f"{audited_atomic.get('status')} "
            f"atom_residue={counts.get('atom_residue_count', 0)} "
            f"process_residue={counts.get('process_residue_count', 0)}"
        )
    closeout = status.get("quality_panel_closeout") or {}
    if isinstance(closeout, dict):
        lines.append(
            "[status] quality_panel_closeout: "
            f"{closeout.get('status') or 'unknown'} "
            f"command={closeout.get('command') or ''}"
        )
    for marker in status.get("stale_or_unknown") or []:
        lines.append(f"[status] stale_or_unknown: {marker}")
    lines.append(f"[status] suggested_next: {status.get('suggested_next_command')}")
    return "\n".join(lines)


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
        from multi_agent_brief.outputs.atomic_reader_projection import (
            project_atomic_reader_text_from_workspace,
        )

        summary[key] = project_atomic_reader_text_from_workspace(
            workspace=workspace,
            target_text=text,
            target_artifact=artifact,
        )
    return summary


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _progress_summary(*, ok: bool, suggested: str) -> dict[str, Any]:
    if not ok:
        return {
            "schema_version": "briefloop.status_progress.v1",
            "runtime_effect": "read_only",
            "source": "workspace_status_projection",
            "current_stage": None,
            "current_work": "create workspace",
            "next_command": suggested,
            "status": "workspace_missing",
            "message": "Workspace folder was not found; create or choose a workspace before running BriefLoop.",
        }
    return {
        "schema_version": "briefloop.status_progress.v1",
        "runtime_effect": "read_only",
        "source": "workspace_status_projection",
        "current_stage": None,
        "current_work": "create handoff",
        "next_command": suggested,
        "status": "not_started",
        "message": "This workspace has no SQLite ControlStore. Run `briefloop run --workspace <ws> --runtime codex` to bootstrap it.",
    }
