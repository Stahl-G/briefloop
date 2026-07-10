"""Repair transactions: start, validate, and complete bounded repairs.

Owns active-repair workflow state, repair artifact baselines/allowlists,
and the raise_if_active_repair_open guard used by gates and delivery.
"""

from __future__ import annotations

import fnmatch
import shlex
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.active_repair import active_repair_is_open
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.feedback.feedback_contract import current_stage_feedback_blocking_reasons
from multi_agent_brief.orchestrator.runtime_state._io import (
    _read_json_if_exists,
    _restore_state_files,
    _sha256_file,
    _snapshot_state_files,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state._transactions import (
    _load_manifest_and_workflow,
    _preflight_transaction_files,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_VALID,
    _build_artifact_registry,
    _changed_artifact_events,
    interpret_frozen_artifact_integrity,
    require_frozen_artifact_integrity_pass,
)
from multi_agent_brief.orchestrator.runtime_state.completion_gates import (
    _completion_artifact_gate_reasons,
    _raise_completion_reasons,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    _artifact_map,
    _stage_ids,
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ACTIVE_REPAIR_OPEN,
    E_ARTIFACT_INVALID,
    E_ILLEGAL_TRANSITION,
    E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN,
    E_REPAIR_NO_LEGAL_ROUTE,
    E_REPAIR_TRANSACTION_REQUIRED,
    E_REQUIRED_ARTIFACT_MISSING,
    E_STAGE_MISMATCH,
    E_TRANSACTION_INTEGRITY,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    _read_event_log_records,
    append_event,
)
from multi_agent_brief.orchestrator.runtime_state.identity import utc_now
from multi_agent_brief.orchestrator.runtime_state.lifecycle import show_runtime_state
from multi_agent_brief.orchestrator.runtime_state.paths import (
    _require_workspace,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.trajectory import (
    TRAJECTORY_NARROWED_DECISIONS,
    _raise_if_trajectory_narrows_repair_route,
    _trajectory_decision_narrowing,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    STAGE_BLOCKED,
    STAGE_COMPLETE,
    STAGE_PENDING,
    STAGE_READY,
    _allowed_decisions_for_stage,
    _next_stage_id,
    _status_entry,
    _workflow_is_finalized,
)
from multi_agent_brief.orchestrator.run_integrity import (
    RUN_INTEGRITY_CLEAN,
    contamination_event_metadata as _run_integrity_contamination_event_metadata,
    contaminate_run_integrity_with_event_flag as _contaminate_run_integrity_with_event_flag,
)
from multi_agent_brief.quality_gates.contract import quality_gate_report_key_for_stage

QUALITY_GATE_ROUTE_SOURCES = {
    "auditor_quality_gate_report",
    "finalize_quality_gate_report",
    "quality_gate_report",
}


def _owner_revision_schema_version() -> str:
    # Keep this local because recovery_state imports runtime-state modules.
    from multi_agent_brief.orchestrator.recovery_state import OWNER_REVISION_SCHEMA

    return OWNER_REVISION_SCHEMA


GATE_SCOPED_STAGES = {"auditor", "finalize"}
NON_GATE_ROUTE_SOURCES = {
    "audit_report",
    "finalize_report",
    "artifact_registry",
    "transaction_integrity",
}


def _active_repair_blocking_error(
    workspace: Path, workflow: dict[str, Any]
) -> RuntimeStateError:
    active = (
        workflow.get("active_repair")
        if isinstance(workflow.get("active_repair"), dict)
        else {}
    )
    owner = active.get("repair_owner")
    transaction_id = active.get("transaction_id")
    workspace_arg = shlex.quote(str(workspace))
    return RuntimeStateError(
        "An owner-stage repair transaction is active. Complete it before advancing workflow state.\n\n"
        "Run:\n"
        f'  multi-agent-brief repair complete --workspace {workspace_arg} --reason "<reason>"\n\n'
        "Or inspect:\n"
        f"  multi-agent-brief repair route --workspace {workspace_arg} --json\n"
        f"  multi-agent-brief state check --workspace {workspace_arg} --strict",
        details={
            "active_repair": active,
            "repair_owner": owner,
            "transaction_id": transaction_id,
            "allowed_commands": [
                f"multi-agent-brief repair route --workspace {workspace_arg} --json",
                f'multi-agent-brief repair complete --workspace {workspace_arg} --reason "<reason>" --json',
                f"multi-agent-brief state check --workspace {workspace_arg} --strict --json",
            ],
            "blocked_commands": [
                "state stage-complete",
                "state finalize-complete",
                "gates check",
                "deliver",
            ],
        },
        error_code=E_ACTIVE_REPAIR_OPEN,
    )


def raise_if_active_repair_open(*, workspace: Path, workflow: dict[str, Any]) -> None:
    if active_repair_is_open(workflow):
        raise _active_repair_blocking_error(workspace, workflow)


def _repair_route_error(payload: dict[str, Any]) -> RuntimeStateError:
    return RuntimeStateError(
        str(
            payload.get("message")
            or payload.get("reason")
            or payload.get("error")
            or "No deterministic repair route found."
        ),
        details=payload,
        error_code=str(payload.get("error_code") or E_ILLEGAL_TRANSITION),
    )


def _delegate_repair_transaction_required_error(
    *,
    workspace: Path,
    stage_id: str,
    decision: str,
    repo_workdir: str | Path | None = None,
) -> RuntimeStateError:
    gate_artifact_id = _gate_scoped_artifact_id_for_stage(stage_id)
    if gate_artifact_id is not None:
        try:
            from multi_agent_brief.repair.router import route_repair_for_gate

            scoped_route = route_repair_for_gate(
                workspace=workspace,
                gate_stage_id=stage_id,
                gate_artifact_id=gate_artifact_id,
                repo_workdir=repo_workdir,
            )
        except Exception as exc:  # pragma: no cover - defensive best-effort diagnostics
            scoped_route = {"ok": False, "error": str(exc)}
    else:
        scoped_route = {
            "ok": True,
            "route_kind": "none",
            "repair_owner": "none",
            "reason": "Current workflow stage has no scoped quality-gate repair route.",
        }

    if _is_owner_stage_repair_route(scoped_route):
        return _repair_transaction_required_error(
            workspace=workspace,
            stage_id=stage_id,
            decision=decision,
            repair_route=scoped_route,
            required_commands=[
                f"multi-agent-brief gates show --workspace {workspace} --json",
                (
                    f"multi-agent-brief repair start --workspace {workspace} "
                    f"--gate-stage {stage_id} --gate-artifact {gate_artifact_id} --json"
                ),
                f'multi-agent-brief repair complete --workspace {workspace} --reason "<reason>" --json',
            ],
            repair_steps=[
                "Current gate has an owner-stage repair route.",
                "Use scoped repair start; do not use workspace-wide bare repair start.",
                "Delegate only the reported repair_owner role.",
                "Allow edits only to repair_route.allowed_artifacts.",
                "Run repair complete after the owner edits.",
            ],
        )

    if scoped_route.get("ok") and scoped_route.get("route_kind") == "none":
        workspace_route = _non_gate_workspace_repair_route(workspace)
        if workspace_route is not None:
            selector = _repair_start_selector_for_route(workspace_route)
            return _repair_transaction_required_error(
                workspace=workspace,
                stage_id=stage_id,
                decision=decision,
                repair_route=workspace_route,
                required_commands=[
                    f"multi-agent-brief repair route --workspace {workspace} --json",
                    f"multi-agent-brief repair start --workspace {workspace} {selector} --json",
                    f'multi-agent-brief repair complete --workspace {workspace} --reason "<reason>" --json',
                ],
                repair_steps=[
                    "Workspace-wide non-gate repair route is available.",
                    "Start the selected route with --route-index from repair route output.",
                    "Do not use bare repair start.",
                    "Delegate only the reported repair_owner role.",
                    "Allow edits only to repair_route.allowed_artifacts.",
                    "Run repair complete after the owner edits.",
                ],
            )

    repair_route = scoped_route if scoped_route.get("ok") else {"ok": False, **scoped_route}
    return _repair_transaction_required_error(
        workspace=workspace,
        stage_id=stage_id,
        decision=decision,
        repair_route=repair_route,
        required_commands=[
            f"multi-agent-brief gates show --workspace {workspace} --json",
        ],
        repair_steps=[
            "delegate_repair cannot be recorded through state decide.",
            "Use gates show for current-gate blockers, or repair route for non-gate blockers.",
            "Do not use bare repair start.",
        ],
    )


def _repair_transaction_required_error(
    *,
    workspace: Path,
    stage_id: str,
    decision: str,
    repair_route: dict[str, Any],
    required_commands: list[str],
    repair_steps: list[str],
) -> RuntimeStateError:
    return RuntimeStateError(
        (
            "Decision 'delegate_repair' requires `multi-agent-brief repair start`; "
            "`state decide` cannot authorize owner-stage artifact edits."
        ),
        details={
            "stage_id": stage_id,
            "decision": decision,
            "required_commands": required_commands,
            "repair_steps": repair_steps,
            "fallback_decisions": ["request_human_review", "block_run"],
            "repair_route": repair_route,
        },
        error_code=E_REPAIR_TRANSACTION_REQUIRED,
    )


def _is_owner_stage_repair_route(route: dict[str, Any]) -> bool:
    return bool(
        route.get("ok", True)
        and route.get("route_kind") == "owner_stage_repair"
        and route.get("repair_owner") not in {None, "", "none", "human"}
        and route.get("allowed_artifacts")
        and route.get("must_rerun_from")
    )


def _non_gate_workspace_repair_route(workspace: Path) -> dict[str, Any] | None:
    try:
        from multi_agent_brief.repair.router import route_repair

        payload = route_repair(workspace=workspace)
    except Exception:  # pragma: no cover - defensive best-effort diagnostics
        return None
    candidates = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    for fallback_index, route in enumerate(candidates):
        if not isinstance(route, dict) or not _is_owner_stage_repair_route(route):
            continue
        source = route.get("source") if isinstance(route.get("source"), dict) else {}
        source_kind = str(source.get("kind") or "")
        if source_kind in QUALITY_GATE_ROUTE_SOURCES:
            continue
        if source_kind not in NON_GATE_ROUTE_SOURCES:
            continue
        if route.get("is_imported_fact_layer_forbidden") is True:
            continue
        return {
            "ok": True,
            "workspace": str(workspace),
            **route,
            "selected_route_index": route.get("route_rank", fallback_index),
            "routes": candidates,
            "finding_count": payload.get("finding_count"),
        }
    return None


def _repair_start_selector_for_route(route: dict[str, Any]) -> str:
    selected_route_index = route.get("selected_route_index")
    if selected_route_index is not None:
        try:
            return f"--route-index {int(selected_route_index)}"
        except (TypeError, ValueError):
            pass
    route_rank = route.get("route_rank")
    if route_rank is not None:
        try:
            return f"--route-index {int(route_rank)}"
        except (TypeError, ValueError):
            pass
    return "--route-index 0"


def _gate_scoped_artifact_id_for_stage(stage_id: str | None) -> str | None:
    stage = str(stage_id or "")
    if stage not in GATE_SCOPED_STAGES:
        return None
    return quality_gate_report_key_for_stage(stage)


def _repair_event_metadata(active_repair: dict[str, Any]) -> dict[str, Any]:
    return {
        "transaction_id": active_repair.get("transaction_id"),
        "repair_start_transaction_id": active_repair.get("repair_start_transaction_id"),
        "repair_started_event_id": active_repair.get("repair_started_event_id"),
        "run_id": active_repair.get("run_id"),
        "contamination_event_id": active_repair.get("contamination_event_id"),
        "repair_owner": active_repair.get("repair_owner"),
        "allowed_artifacts": list(active_repair.get("allowed_artifacts") or []),
        "blocked_direct_edits": list(active_repair.get("blocked_direct_edits") or []),
        "source": active_repair.get("source") or {},
        "must_rerun_from": active_repair.get("must_rerun_from"),
        "recommended_action": active_repair.get("recommended_action"),
        "run_integrity_effect": active_repair.get("run_integrity_effect"),
    }


def _latest_contamination_event_id(
    event_records: list[dict[str, Any]], *, run_id: str
) -> str:
    for event in reversed(event_records):
        if (
            event.get("event_type") == "run_integrity_contaminated"
            and str(event.get("run_id") or "") == run_id
        ):
            return str(event.get("event_id") or "")
    return ""


def _repair_primary_artifact_id(active_repair: dict[str, Any]) -> str:
    source = active_repair.get("source")
    if isinstance(source, dict) and source.get("artifact_id"):
        return str(source["artifact_id"])
    return ""


def _recovery_stale_artifact_baselines(
    *,
    stages: list[dict[str, Any]],
    owner_stage: str,
    baseline_records: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    stage_ids = _stage_ids(stages)
    if owner_stage not in stage_ids:
        return {}
    downstream_stages = set(stage_ids[stage_ids.index(owner_stage) + 1 :])
    baselines: dict[str, dict[str, Any]] = {}
    for artifact_id, record in baseline_records.items():
        if not isinstance(record, dict):
            continue
        if str(record.get("producer_stage") or "") not in downstream_stages:
            continue
        baselines[str(artifact_id)] = {
            "path": record.get("path"),
            "status": record.get("status"),
            "validation_result": record.get("validation_result"),
            "sha256": record.get("sha256"),
        }
    return baselines


def _workflow_with_repair_run_integrity_effect(
    *,
    workflow: dict[str, Any],
    active_repair: dict[str, Any],
    now: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    effect = active_repair.get("run_integrity_effect")
    if not isinstance(effect, dict) or effect.get("reference_eligible") is not False:
        return workflow, None
    current_integrity = (
        workflow.get("run_integrity")
        if isinstance(workflow.get("run_integrity"), dict)
        else {}
    )
    if (
        current_integrity.get("status") != RUN_INTEGRITY_CLEAN
        or current_integrity.get("reference_eligible", True) is not True
    ):
        return workflow, None

    source = (
        active_repair.get("source")
        if isinstance(active_repair.get("source"), dict)
        else {}
    )
    reason_code = str(
        source.get("finding_type")
        or effect.get("reason_code")
        or "repair_non_reference"
    )
    message = str(
        effect.get("reason")
        or active_repair.get("reason")
        or "Repair route marked this run non-reference-eligible."
    )
    stage_id = source.get("stage_id") or active_repair.get("repair_owner")
    artifact_id = source.get("artifact_id")
    metadata = {
        "repair_transaction_id": active_repair.get("transaction_id"),
        "repair_owner": active_repair.get("repair_owner"),
        "source": source,
        "recommended_action": active_repair.get("recommended_action"),
        "run_integrity_effect": effect,
    }
    contaminated, reason_added = _contaminate_run_integrity_with_event_flag(
        workflow,
        reason_code=reason_code,
        message=message,
        created_at=now,
        event_type="repair_started",
        stage_id=str(stage_id) if stage_id else None,
        artifact_id=str(artifact_id) if artifact_id else None,
        metadata=metadata,
    )
    if not reason_added:
        return contaminated, None
    reasons = (contaminated.get("run_integrity") or {}).get("reasons")
    reason = (
        reasons[-1]
        if isinstance(reasons, list) and reasons and isinstance(reasons[-1], dict)
        else {}
    )
    return contaminated, reason


def _source_stage_for_repair_route(route: dict[str, Any]) -> str:
    source = route.get("source") if isinstance(route.get("source"), dict) else {}
    stage_id = str(source.get("stage_id") or "")
    if stage_id:
        return stage_id
    kind = str(source.get("kind") or "")
    if kind == "auditor_quality_gate_report":
        return "auditor"
    if kind == "finalize_quality_gate_report":
        return "finalize"
    if kind == "audit_report":
        return "auditor"
    return ""


def _repair_artifact_baseline(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = registry.get("artifacts")
    if not isinstance(records, dict):
        return {}
    baseline: dict[str, dict[str, Any]] = {}
    for artifact_id, record in records.items():
        if not isinstance(record, dict):
            continue
        baseline[str(artifact_id)] = {
            "path": record.get("path"),
            "producer_stage": record.get("producer_stage"),
            "status": record.get("status"),
            "validation_result": record.get("validation_result"),
            "sha256": record.get("sha256"),
        }
    return baseline


def _workflow_with_active_repair(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    active_repair: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    owner = str(active_repair.get("repair_owner") or "")
    if owner not in _stage_ids(stages):
        raise RuntimeStateError(
            f"Repair owner '{owner}' is not a workflow stage.",
            details={"repair_owner": owner, "known_stages": _stage_ids(stages)},
            error_code=E_ILLEGAL_TRANSITION,
        )
    updated = dict(workflow)
    statuses = dict(updated.get("stage_statuses") or {})
    statuses[owner] = _status_entry(
        STAGE_READY,
        f"Repair started: {active_repair.get('reason') or ''}".strip(),
        now,
        metadata={
            "active_repair": True,
            "repair_transaction_id": active_repair.get("transaction_id"),
            "allowed_artifacts": list(active_repair.get("allowed_artifacts") or []),
            "must_rerun_from": active_repair.get("must_rerun_from"),
        },
    )
    updated["updated_at"] = now
    updated["current_stage"] = owner
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["active_repair"] = active_repair
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, owner)
    return updated


def start_repair_transaction(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
    route_index: int | None = None,
    finding_id: str | None = None,
    gate_stage_id: str | None = None,
    gate_artifact_id: str | None = None,
) -> dict[str, Any]:
    """Start an explicit owner-stage repair transaction from the deterministic route."""

    ws = _require_workspace(workspace)
    scoped_gate_requested = gate_stage_id is not None or gate_artifact_id is not None
    if scoped_gate_requested and not (gate_stage_id and gate_artifact_id):
        raise RuntimeStateError(
            "Scoped repair start requires both gate_stage_id and gate_artifact_id.",
            details={
                "gate_stage_id": gate_stage_id,
                "gate_artifact_id": gate_artifact_id,
            },
            error_code=E_ILLEGAL_TRANSITION,
        )
    if scoped_gate_requested and (route_index is not None or finding_id is not None):
        raise RuntimeStateError(
            "Use either scoped gate selection or route_index/finding_id, not both.",
            details={
                "gate_stage_id": gate_stage_id,
                "gate_artifact_id": gate_artifact_id,
                "route_index": route_index,
                "finding_id": finding_id,
            },
            error_code=E_ILLEGAL_TRANSITION,
        )
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    if not paths["event_log"].exists():
        raise RuntimeStateError(
            "Repair start requires an existing event_log.jsonl control trace.",
            details={"path": str(paths["event_log"])},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    if _workflow_is_finalized(workflow) or workflow.get("current_stage") is None:
        raise RuntimeStateError(
            "Cannot start repair for a finalized workflow; create a new run or use an explicit supersede/revision path.",
            details={"current_stage": workflow.get("current_stage")},
            error_code=E_ILLEGAL_TRANSITION,
        )
    if active_repair_is_open(workflow):
        raise RuntimeStateError(
            "A repair transaction is already active.",
            details={"active_repair": workflow.get("active_repair")},
            error_code=E_ILLEGAL_TRANSITION,
        )
    if scoped_gate_requested:
        current_gate_stage_id = str(workflow.get("current_stage") or "")
        current_gate_artifact_id = _gate_scoped_artifact_id_for_stage(current_gate_stage_id)
        if current_gate_artifact_id is None:
            raise RuntimeStateError(
                "Scoped repair start is only valid for auditor/finalize quality-gate stages.",
                details={
                    "requested_gate_stage_id": gate_stage_id,
                    "requested_gate_artifact_id": gate_artifact_id,
                    "current_stage": current_gate_stage_id,
                    "gate_scoped_stages": sorted(GATE_SCOPED_STAGES),
                },
                error_code=E_ILLEGAL_TRANSITION,
            )
        if gate_stage_id != current_gate_stage_id or gate_artifact_id != current_gate_artifact_id:
            raise RuntimeStateError(
                "Scoped repair start gate must match the current workflow stage.",
                details={
                    "requested_gate_stage_id": gate_stage_id,
                    "requested_gate_artifact_id": gate_artifact_id,
                    "current_stage": current_gate_stage_id,
                    "expected_gate_artifact_id": current_gate_artifact_id,
                },
                error_code=E_ILLEGAL_TRANSITION,
            )
    run_id = str(manifest["run_id"])
    event_records = _read_event_log_records(paths["event_log"])
    existing_narrowing = _trajectory_decision_narrowing(
        workspace=ws,
        workflow=workflow,
        event_records=event_records,
        run_id=run_id,
    )
    if existing_narrowing:
        raise RuntimeStateError(
            "Repair start is blocked because trajectory regulation narrowed current-stage decisions.",
            details={
                "stage_id": workflow.get("current_stage"),
                "decision": "delegate_repair",
                "allowed_decisions": list(TRAJECTORY_NARROWED_DECISIONS),
                "trajectory_regulation": existing_narrowing,
            },
            error_code=E_ILLEGAL_TRANSITION,
        )

    from multi_agent_brief.repair.router import route_repair, route_repair_for_gate

    if scoped_gate_requested:
        route = route_repair_for_gate(
            workspace=ws,
            gate_stage_id=gate_stage_id,
            gate_artifact_id=gate_artifact_id,
            repo_workdir=repo_workdir,
        )
    else:
        route = route_repair(workspace=ws, route_index=route_index, finding_id=finding_id)
    if not route.get("ok"):
        raise _repair_route_error(route)
    if route.get("route_kind") == "human_review":
        raise RuntimeStateError(
            "Repair start is blocked because the selected route requires human review.",
            details=route,
            error_code=E_ILLEGAL_TRANSITION,
        )
    if route.get("is_imported_fact_layer_forbidden") is True:
        raise RuntimeStateError(
            (
                "This route targets imported frozen fact-layer artifacts. Start a fresh condition workspace "
                "or use human review; do not repair imported fact layer artifacts in place."
            ),
            details={
                "selected_route": route,
                "allowed_artifacts": list(route.get("allowed_artifacts") or []),
                "workspace": str(ws),
            },
            error_code=E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN,
        )
    if route.get("repair_owner") in {None, "", "none"}:
        raise RuntimeStateError(
            "No legal deterministic repair route found."
            if route.get("no_legal_route")
            else "No deterministic repair route found.",
            details=route,
            error_code=E_REPAIR_NO_LEGAL_ROUTE
            if route.get("no_legal_route")
            else E_ILLEGAL_TRANSITION,
        )
    if not route.get("allowed_artifacts"):
        raise RuntimeStateError(
            "Deterministic repair route has no allowed artifacts.",
            details=route,
            error_code=E_ILLEGAL_TRANSITION,
        )
    _raise_if_trajectory_narrows_repair_route(
        workspace=ws,
        workflow=workflow,
        event_records=event_records,
        run_id=run_id,
        route=route,
    )

    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    transaction_id = uuid.uuid4().hex
    repair_started_event_id = uuid.uuid4().hex
    now = utc_now()
    route_stage = _source_stage_for_repair_route(route)
    current_stage = str(workflow.get("current_stage") or "")
    if route_stage and route_stage != current_stage:
        raise RuntimeStateError(
            "Repair route source stage does not match the current workflow stage.",
            details={
                "route_stage_id": route_stage,
                "current_stage": current_stage,
                "source": route.get("source") or {},
            },
            error_code=E_ILLEGAL_TRANSITION,
        )
    baseline_registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=workflow,
        updated_at=now,
    )
    current_integrity = (
        workflow.get("run_integrity")
        if isinstance(workflow.get("run_integrity"), dict)
        else {}
    )
    effect = route.get("run_integrity_effect")
    creates_contamination = (
        isinstance(effect, dict)
        and effect.get("reference_eligible") is False
        and current_integrity.get("status") == RUN_INTEGRITY_CLEAN
        and current_integrity.get("reference_eligible", True) is True
    )
    contamination_event_id = _latest_contamination_event_id(
        event_records, run_id=run_id
    )
    if creates_contamination:
        contamination_event_id = uuid.uuid4().hex
    elif current_integrity.get("status") != RUN_INTEGRITY_CLEAN and not contamination_event_id:
        raise RuntimeStateError(
            "Non-clean repair start requires a current-run contamination event.",
            details={"run_id": run_id, "run_integrity": current_integrity},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    active_repair = {
        "schema_version": "mabw.active_repair.v2",
        "transaction_id": transaction_id,
        "repair_start_transaction_id": transaction_id,
        "repair_started_event_id": repair_started_event_id,
        "run_id": run_id,
        "contamination_event_id": contamination_event_id,
        "repair_owner": route.get("repair_owner"),
        "allowed_artifacts": list(route.get("allowed_artifacts") or []),
        "blocked_direct_edits": list(route.get("blocked_direct_edits") or []),
        "source": route.get("source") or {},
        "source_report_path": (route.get("source") or {}).get("file"),
        "must_rerun_from": route.get("must_rerun_from") or "",
        "reason": route.get("reason") or "",
        "recommended_action": route.get("recommended_action"),
        "run_integrity_effect": route.get("run_integrity_effect"),
        "started_at": now,
        "artifact_baseline": _repair_artifact_baseline(baseline_registry),
    }
    next_workflow = _workflow_with_active_repair(
        workflow=workflow,
        stages=stages,
        active_repair=active_repair,
        now=now,
    )
    next_workflow, contamination_reason = _workflow_with_repair_run_integrity_effect(
        workflow=next_workflow,
        active_repair=active_repair,
        now=now,
    )

    state_snapshots = _snapshot_state_files(paths, ("workflow_state", "event_log"))
    _write_json_atomic(paths["workflow_state"], next_workflow)
    try:
        append_event(
            workspace=ws,
            run_id=str(manifest["run_id"]),
            event_type="repair_started",
            event_id=repair_started_event_id,
            actor=actor,
            stage_id=str(active_repair["repair_owner"]),
            reason=str(active_repair.get("reason") or "Repair transaction started."),
            metadata=_repair_event_metadata(active_repair),
        )
        if contamination_reason is not None:
            append_event(
                workspace=ws,
                run_id=str(manifest["run_id"]),
                event_type="run_integrity_contaminated",
                event_id=contamination_event_id,
                actor=actor,
                stage_id=contamination_reason.get("stage_id"),
                artifact_id=contamination_reason.get("artifact_id"),
                reason=str(
                    contamination_reason.get("message")
                    or "Repair start contaminated run integrity."
                ),
                metadata=_run_integrity_contamination_event_metadata(
                    contamination_reason
                ),
            )
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Repair start partially wrote control files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "event_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        raise RuntimeStateError(
            "Repair start event append failed; control files were restored.",
            details={
                "transaction_id": transaction_id,
                "event_error": str(exc),
                "event_details": exc.details,
            },
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc

    state = show_runtime_state(workspace=ws)
    state["repair"] = active_repair
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": active_repair["repair_owner"],
        "decision": "repair_start",
    }
    return state


def supersede_stage_artifact_transaction(
    *,
    workspace: str | Path,
    stage_id: str,
    artifact: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    """Record a contaminated owner-stage artifact revision without restoring clean status."""

    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    event_records = _preflight_transaction_files(paths)
    if not paths["event_log"].exists():
        raise RuntimeStateError(
            "Stage supersede requires an existing event_log.jsonl control trace.",
            details={"path": str(paths["event_log"])},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    if _workflow_is_finalized(workflow) or workflow.get("current_stage") is None:
        raise RuntimeStateError(
            "Cannot supersede a finalized workflow; create a new run instead.",
            details={"current_stage": workflow.get("current_stage")},
            error_code=E_ILLEGAL_TRANSITION,
        )
    if active_repair_is_open(workflow):
        raise RuntimeStateError(
            "Cannot supersede while a repair transaction is already active.",
            details={"active_repair": workflow.get("active_repair")},
            error_code=E_ILLEGAL_TRANSITION,
        )
    integrity = workflow.get("run_integrity") if isinstance(workflow.get("run_integrity"), dict) else {}
    if integrity.get("status") == RUN_INTEGRITY_CLEAN:
        raise RuntimeStateError(
            "Stage supersede is only allowed after run integrity contamination has been recorded.",
            details={"run_integrity": integrity},
            error_code=E_ILLEGAL_TRANSITION,
        )
    run_id = str(manifest["run_id"])
    contamination_events = _run_integrity_contamination_events(
        event_records, run_id=run_id
    )
    if not contamination_events:
        raise RuntimeStateError(
            "Stage supersede requires a recorded run_integrity_contaminated event.",
            details={"run_integrity": integrity},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    contamination_event_id = str(contamination_events[-1].get("event_id") or "")
    if not contamination_event_id:
        raise RuntimeStateError(
            "Stage supersede requires a contamination event identity.",
            details={"run_id": run_id},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    stage_ids = _stage_ids(stages)
    if stage_id not in stage_ids:
        raise RuntimeStateError(
            f"Unknown supersede stage: {stage_id}",
            details={"stage_id": stage_id, "known_stages": stage_ids},
            error_code=E_ILLEGAL_TRANSITION,
        )
    if not _stage_status_is_complete(workflow, stage_id):
        raise RuntimeStateError(
            "Stage supersede requires the owner stage to be complete.",
            details={"stage_id": stage_id},
            error_code=E_ILLEGAL_TRANSITION,
        )

    artifacts = load_artifact_contracts(repo)
    artifact_ref = _normalize_artifact_reference(ws, artifact)
    artifact_contract = _artifact_contract_for_supersede(
        artifacts=artifacts,
        stage_id=stage_id,
        artifact_ref=artifact_ref,
    )
    artifact_id = str(artifact_contract.get("artifact_id") or "")
    artifact_path = str(artifact_contract.get("path") or artifact_ref)
    blocked_anchor = _forbidden_supersede_control_anchor_reason(
        stage_id=stage_id,
        artifact_id=artifact_id,
    )
    if blocked_anchor:
        reason, recommended_action = blocked_anchor
        raise RuntimeStateError(
            f"Stage supersede cannot accept {artifact_id} because {reason}; "
            f"use {recommended_action} instead.",
            details={
                "stage_id": stage_id,
                "artifact_id": artifact_id,
                "artifact": artifact_path,
                "recommended_action": recommended_action,
            },
            error_code=E_ILLEGAL_TRANSITION,
        )
    target_path = ws / artifact_path
    if not target_path.exists() or not target_path.is_file():
        raise RuntimeStateError(
            "Stage supersede artifact is missing.",
            details={"stage_id": stage_id, "artifact": artifact_path},
            error_code=E_REQUIRED_ARTIFACT_MISSING,
        )

    old_registry = _read_json_if_exists(paths["artifact_registry"])
    old_records = (old_registry or {}).get("artifacts") if isinstance(old_registry, dict) else None
    old_record = old_records.get(artifact_id) if isinstance(old_records, dict) else None
    if not isinstance(old_record, dict) or not old_record.get("sha256"):
        raise RuntimeStateError(
            "Stage supersede requires a frozen artifact hash in artifact_registry.json.",
            details={"stage_id": stage_id, "artifact_id": artifact_id, "artifact": artifact_path},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    old_registered_sha256 = str(old_record["sha256"])
    current_bytes_sha256 = _sha256_file(target_path)
    if current_bytes_sha256 == old_registered_sha256:
        raise RuntimeStateError(
            "Stage supersede artifact bytes match the registered frozen hash.",
            details={
                "stage_id": stage_id,
                "artifact_id": artifact_id,
                "artifact": artifact_path,
                "registered_sha256": old_registered_sha256,
                "current_sha256": current_bytes_sha256,
            },
            error_code=E_ILLEGAL_TRANSITION,
        )

    transaction_id = uuid.uuid4().hex
    now = utc_now()
    next_workflow = _workflow_after_stage_supersede(
        workflow=workflow,
        stages=stages,
        stage_id=stage_id,
        artifact_id=artifact_id,
        artifact_path=artifact_path,
        reason=reason,
        now=now,
        transaction_id=transaction_id,
        run_id=run_id,
        contamination_event_id=contamination_event_id,
        old_registered_sha256=old_registered_sha256,
        current_bytes_sha256=current_bytes_sha256,
        baseline_records=old_records,
    )
    registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=next_workflow,
        updated_at=now,
        recovery_state={
            "recovery_event_type": "repair_stage_superseded",
            "recovery_transaction_id": transaction_id,
            "owner_stage": stage_id,
            "stale_artifact_baselines": (
                (next_workflow.get("last_repair_transaction") or {}).get(
                    "stale_artifact_baselines"
                )
                or {}
            ),
        },
    )
    frozen_verdict = interpret_frozen_artifact_integrity(
        old_registry=old_registry,
        registry=registry,
        workflow=workflow,
        artifacts=artifacts,
        stages=stages,
        mutating_stage=stage_id,
        exempt_artifact_ids={artifact_id},
    )
    frozen_reasons = require_frozen_artifact_integrity_pass(frozen_verdict)
    if frozen_reasons:
        raise RuntimeStateError(
            "Stage supersede cannot proceed because frozen artifact integrity could not be verified.",
            details={"stage_id": stage_id, "reasons": frozen_reasons},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    new_record = ((registry.get("artifacts") or {}).get(artifact_id) or {})
    if new_record.get("sha256") != current_bytes_sha256:
        raise RuntimeStateError(
            "Stage supersede registry did not bind the current artifact bytes.",
            details={
                "stage_id": stage_id,
                "artifact_id": artifact_id,
                "artifact": artifact_path,
                "expected_sha256": current_bytes_sha256,
                "actual_sha256": new_record.get("sha256"),
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    if new_record.get("status") != ARTIFACT_VALID:
        raise RuntimeStateError(
            "Stage supersede artifact bytes are not valid for the target artifact contract.",
            details={
                "stage_id": stage_id,
                "artifact_id": artifact_id,
                "artifact": artifact_path,
                "artifact_status": new_record.get("status"),
                "validation_result": new_record.get("validation_result"),
                "current_sha256": current_bytes_sha256,
            },
            error_code=E_ARTIFACT_INVALID,
        )
    artifact_events = _changed_artifact_events(
        old_registry=old_registry,
        registry=registry,
    )

    state_snapshots = _snapshot_state_files(
        paths, ("artifact_registry", "workflow_state", "event_log")
    )
    state_written = False
    try:
        _write_json_atomic(paths["artifact_registry"], registry)
        state_written = True
        _write_json_atomic(paths["workflow_state"], next_workflow)
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Stage supersede partially wrote files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": stage_id,
                    "state_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        code = E_TRANSACTION_PARTIAL_WRITE if state_written else exc.error_code
        raise RuntimeStateError(
            "Stage supersede failed while writing state files; control files were restored.",
            details={
                "transaction_id": transaction_id,
                "stage_id": stage_id,
                "state_error": str(exc),
                "state_details": exc.details,
                "restored": True,
            },
            error_code=code,
        ) from exc

    try:
        for event in artifact_events:
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type=str(event["event_type"]),
                actor=actor,
                artifact_id=event.get("artifact_id"),
                reason=str(event.get("reason") or ""),
                metadata={
                    **(event.get("metadata") or {}),
                    "transaction_id": transaction_id,
                    "supersede_stage": stage_id,
                },
            )
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="repair_stage_superseded",
            actor=actor,
            stage_id=stage_id,
            artifact_id=artifact_id,
            reason=reason,
            metadata={
                "owner_revision_schema_version": _owner_revision_schema_version(),
                "transaction_id": transaction_id,
                "repair_start_transaction_id": transaction_id,
                "run_id": run_id,
                "contamination_event_id": contamination_event_id,
                "owner_stage": stage_id,
                "stage_id": stage_id,
                "artifact_id": artifact_id,
                "artifact_path": artifact_path,
                "old_registered_sha256": old_registered_sha256,
                "current_bytes_sha256": current_bytes_sha256,
                "rerun_start_stage": next_workflow.get("current_stage"),
                "next_stage": next_workflow.get("current_stage"),
                "stale_artifact_baselines": (
                    (next_workflow.get("last_repair_transaction") or {}).get(
                        "stale_artifact_baselines"
                    )
                    or {}
                ),
                "reference_eligible": False,
                "run_integrity_status": (next_workflow.get("run_integrity") or {}).get("status"),
                "contamination_event_count": len(contamination_events),
            },
        )
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Stage supersede partially wrote files and failed rollback after event append failure.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": stage_id,
                    "event_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        raise RuntimeStateError(
            "Stage supersede event append failed; control files were restored.",
            details={
                "transaction_id": transaction_id,
                "stage_id": stage_id,
                "event_error": str(exc),
                "event_details": exc.details,
            },
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc

    state = show_runtime_state(workspace=ws)
    state["repair"] = {
        "superseded": True,
        "stage_id": stage_id,
        "artifact_id": artifact_id,
        "artifact": artifact_path,
        "old_registered_sha256": old_registered_sha256,
        "current_bytes_sha256": current_bytes_sha256,
        "next_stage": next_workflow.get("current_stage"),
        "reference_eligible": False,
    }
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": stage_id,
        "decision": "supersede_stage",
    }
    return state


def _run_integrity_contamination_events(
    event_records: list[dict[str, Any]], *, run_id: str
) -> list[dict[str, Any]]:
    return [
        event
        for event in event_records
        if isinstance(event, dict)
        and event.get("event_type") == "run_integrity_contaminated"
        and str(event.get("run_id") or "") == run_id
    ]


def _stage_status_is_complete(workflow: dict[str, Any], stage_id: str) -> bool:
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    status = statuses.get(stage_id) if isinstance(statuses.get(stage_id), dict) else {}
    return status.get("status") == STAGE_COMPLETE


def _normalize_artifact_reference(workspace: Path, artifact: str) -> str:
    raw = str(artifact or "").strip()
    if not raw:
        raise RuntimeStateError(
            "Stage supersede requires an artifact path.",
            details={"artifact": artifact},
            error_code=E_ILLEGAL_TRANSITION,
        )
    path = Path(raw).expanduser()
    if path.is_absolute():
        try:
            return path.resolve().relative_to(workspace).as_posix()
        except ValueError as exc:
            raise RuntimeStateError(
                "Stage supersede artifact must be inside the workspace.",
                details={"artifact": raw, "workspace": str(workspace)},
                error_code=E_ILLEGAL_TRANSITION,
            ) from exc
    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    target = (workspace / normalized).resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise RuntimeStateError(
            "Stage supersede artifact must be inside the workspace.",
            details={"artifact": raw, "workspace": str(workspace)},
            error_code=E_ILLEGAL_TRANSITION,
        ) from exc
    return normalized


def _artifact_contract_for_supersede(
    *,
    artifacts: list[dict[str, Any]],
    stage_id: str,
    artifact_ref: str,
) -> dict[str, Any]:
    for item in artifacts:
        artifact_id = str(item.get("artifact_id") or "")
        path = str(item.get("path") or "")
        if artifact_ref not in {artifact_id, path}:
            continue
        producer_stage = str(item.get("producer_stage") or "")
        if producer_stage != stage_id:
            raise RuntimeStateError(
                "Stage supersede artifact is not produced by the requested stage.",
                details={
                    "stage_id": stage_id,
                    "artifact": artifact_ref,
                    "artifact_id": artifact_id,
                    "producer_stage": producer_stage,
                },
                error_code=E_ILLEGAL_TRANSITION,
            )
        return item
    raise RuntimeStateError(
        "Stage supersede artifact is not a known workflow artifact.",
        details={"stage_id": stage_id, "artifact": artifact_ref},
        error_code=E_ILLEGAL_TRANSITION,
    )


def _forbidden_supersede_control_anchor_reason(
    *,
    stage_id: str,
    artifact_id: str,
) -> tuple[str, str] | None:
    if stage_id == "auditor" and artifact_id == "audit_report":
        return (
            "audit_report is bound by Python-owned audit_binding metadata",
            "rerun_auditor",
        )
    if stage_id == "claim-ledger" and artifact_id == "claim_ledger":
        return (
            "claim_ledger is bound by Python-owned claim_ledger_freeze metadata",
            "rerun_claim_ledger_freeze",
        )
    return None


def _workflow_after_stage_supersede(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    stage_id: str,
    artifact_id: str,
    artifact_path: str,
    reason: str,
    now: str,
    transaction_id: str,
    run_id: str,
    contamination_event_id: str,
    old_registered_sha256: str,
    current_bytes_sha256: str,
    baseline_records: dict[str, Any],
) -> dict[str, Any]:
    stage_ids = _stage_ids(stages)
    stage_index = stage_ids.index(stage_id)
    rerun_stage = _next_stage_id(stages, stage_id)
    if rerun_stage is None:
        raise RuntimeStateError(
            "Stage supersede requires a downstream stage to rerun.",
            details={"stage_id": stage_id},
            error_code=E_ILLEGAL_TRANSITION,
        )
    recovery_baselines = _recovery_stale_artifact_baselines(
        stages=stages,
        owner_stage=stage_id,
        baseline_records=baseline_records,
    )
    statuses = dict(workflow.get("stage_statuses") or {})
    statuses[stage_id] = _status_entry(
        STAGE_COMPLETE,
        reason,
        now,
        metadata={
            "superseded": True,
            "supersede_transaction_id": transaction_id,
            "artifact_id": artifact_id,
            "artifact_path": artifact_path,
            "old_registered_sha256": old_registered_sha256,
            "current_bytes_sha256": current_bytes_sha256,
        },
    )
    for downstream_stage_id in stage_ids[stage_index + 1 :]:
        if downstream_stage_id == rerun_stage:
            statuses[downstream_stage_id] = _status_entry(
                STAGE_READY,
                "Ready after owner-stage supersede.",
                now,
            )
        else:
            statuses[downstream_stage_id] = _status_entry(
                STAGE_PENDING,
                "Pending rerun after owner-stage supersede.",
                now,
            )
    updated = dict(workflow)
    updated.pop("active_repair", None)
    updated["updated_at"] = now
    updated["current_stage"] = rerun_stage
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["last_decision"] = {
        "stage_id": stage_id,
        "decision": "supersede_stage",
        "reason": reason,
        "created_at": now,
    }
    updated["last_repair_transaction"] = {
        "transaction_id": transaction_id,
        "repair_start_transaction_id": transaction_id,
        "run_id": run_id,
        "contamination_event_id": contamination_event_id,
        "owner_stage": stage_id,
        "artifact_id": artifact_id,
        "rerun_start_stage": rerun_stage,
        "stale_artifact_baselines": recovery_baselines,
        "stage_id": stage_id,
        "decision": "supersede_stage",
        "reason": reason,
        "created_at": now,
    }
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(
        stages, rerun_stage
    )
    return updated


def _artifact_path_matches(pattern: str, path: str) -> bool:
    normalized_pattern = pattern.strip()
    normalized_path = path.strip()
    return bool(
        normalized_pattern
        and (
            normalized_path == normalized_pattern
            or fnmatch.fnmatch(normalized_path, normalized_pattern)
        )
    )


def _artifact_allowed(path: str, patterns: list[str]) -> bool:
    return any(_artifact_path_matches(pattern, path) for pattern in patterns)


def _repair_changed_artifact_reasons(
    *,
    baseline_records: dict[str, Any],
    registry: dict[str, Any],
    allowed_artifacts: list[str],
    blocked_direct_edits: list[str],
) -> tuple[list[str], bool]:
    new_records = registry.get("artifacts")
    if not isinstance(baseline_records, dict) or not isinstance(new_records, dict):
        return [
            "Repair completion requires a valid artifact baseline and artifact_registry.json."
        ], False

    reasons: list[str] = []
    allowed_changed = False
    for artifact_id in sorted({*baseline_records.keys(), *new_records.keys()}):
        old_record_raw = baseline_records.get(artifact_id) or {}
        new_record = new_records.get(artifact_id) or {}
        if not isinstance(old_record_raw, dict):
            old_record_raw = {}
        if not isinstance(new_record, dict):
            new_record = {}
        path = str(new_record.get("path") or old_record_raw.get("path") or artifact_id)
        old_state = (
            old_record_raw.get("status"),
            old_record_raw.get("validation_result"),
            old_record_raw.get("sha256"),
        )
        new_state = (
            new_record.get("status"),
            new_record.get("validation_result"),
            new_record.get("sha256"),
        )
        if old_state == new_state:
            continue
        if _artifact_allowed(path, allowed_artifacts):
            allowed_changed = True
            continue
        if _artifact_allowed(path, blocked_direct_edits):
            reasons.append(
                f"Blocked repair artifact changed without ownership: {path}."
            )
        else:
            reasons.append(f"Repair changed non-allowed frozen artifact: {path}.")
    return reasons, allowed_changed


def _stale_artifact_baselines_for_stage(
    *,
    stage: dict[str, Any],
    baseline_records: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    baselines: dict[str, dict[str, Any]] = {}
    for artifact_id in [str(item) for item in (stage.get("expected_artifacts") or [])]:
        record = (
            baseline_records.get(artifact_id)
            if isinstance(baseline_records, dict)
            else None
        )
        if not isinstance(record, dict):
            continue
        baselines[artifact_id] = {
            "path": record.get("path"),
            "status": record.get("status"),
            "validation_result": record.get("validation_result"),
            "sha256": record.get("sha256"),
        }
    return baselines


def _workflow_after_repair_completion(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    active_repair: dict[str, Any],
    reason: str,
    now: str,
    transaction_id: str,
) -> dict[str, Any]:
    owner = str(active_repair.get("repair_owner") or "")
    stage_ids = _stage_ids(stages)
    if owner not in stage_ids:
        raise RuntimeStateError(
            f"Repair owner '{owner}' is not a workflow stage.",
            details={"repair_owner": owner, "known_stages": stage_ids},
            error_code=E_ILLEGAL_TRANSITION,
        )
    owner_index = stage_ids.index(owner)
    baseline_records = (
        active_repair.get("artifact_baseline")
        if isinstance(active_repair.get("artifact_baseline"), dict)
        else {}
    )
    recovery_baselines = _recovery_stale_artifact_baselines(
        stages=stages,
        owner_stage=owner,
        baseline_records=baseline_records,
    )
    requested_rerun = str(active_repair.get("must_rerun_from") or "")
    rerun_stage = (
        requested_rerun
        if requested_rerun in stage_ids
        else _next_stage_id(stages, owner)
    )
    statuses = dict(workflow.get("stage_statuses") or {})
    statuses[owner] = _status_entry(
        STAGE_COMPLETE,
        reason,
        now,
        metadata={
            "repaired": True,
            "repair_transaction_id": transaction_id,
            "allowed_artifacts": list(active_repair.get("allowed_artifacts") or []),
        },
    )
    for stage_id in stage_ids[owner_index + 1 :]:
        if stage_id == rerun_stage:
            statuses[stage_id] = _status_entry(
                STAGE_READY,
                "Ready after owner-stage repair completion.",
                now,
            )
        else:
            statuses[stage_id] = _status_entry(
                STAGE_PENDING,
                "Pending rerun after owner-stage repair completion.",
                now,
            )
    updated = dict(workflow)
    updated.pop("active_repair", None)
    updated["updated_at"] = now
    updated["current_stage"] = rerun_stage
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["last_decision"] = {
        "stage_id": owner,
        "decision": "repair_complete",
        "reason": reason,
        "created_at": now,
    }
    updated["last_repair_transaction"] = {
        "transaction_id": transaction_id,
        "repair_start_transaction_id": active_repair.get("repair_start_transaction_id"),
        "run_id": active_repair.get("run_id"),
        "contamination_event_id": active_repair.get("contamination_event_id"),
        "owner_stage": owner,
        "artifact_id": _repair_primary_artifact_id(active_repair),
        "rerun_start_stage": rerun_stage,
        "stale_artifact_baselines": recovery_baselines,
        "stage_id": owner,
        "decision": "repair_complete",
        "reason": reason,
        "created_at": now,
    }
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(
        stages, rerun_stage
    )
    return updated


def complete_repair_transaction(
    *,
    workspace: str | Path,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    """Complete the active owner-stage repair transaction."""

    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    if not paths["event_log"].exists():
        raise RuntimeStateError(
            "Repair completion requires an existing event_log.jsonl control trace.",
            details={"path": str(paths["event_log"])},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    if _workflow_is_finalized(workflow) or workflow.get("current_stage") is None:
        raise RuntimeStateError(
            "Cannot complete repair for a finalized workflow; create a new run or use an explicit supersede/revision path.",
            details={"current_stage": workflow.get("current_stage")},
            error_code=E_ILLEGAL_TRANSITION,
        )
    active_repair = workflow.get("active_repair")
    if not isinstance(active_repair, dict):
        raise RuntimeStateError(
            "No active repair transaction exists.",
            details={"workspace": str(ws)},
            error_code=E_ILLEGAL_TRANSITION,
        )
    owner = str(active_repair.get("repair_owner") or "")
    if workflow.get("current_stage") != owner:
        raise RuntimeStateError(
            "Active repair owner does not match current workflow stage.",
            details={
                "repair_owner": owner,
                "current_stage": workflow.get("current_stage"),
            },
            error_code=E_STAGE_MISMATCH,
        )

    allowed_artifacts = [
        str(item) for item in active_repair.get("allowed_artifacts") or []
    ]
    blocked_direct_edits = [
        str(item) for item in active_repair.get("blocked_direct_edits") or []
    ]
    if not allowed_artifacts:
        raise RuntimeStateError(
            "Active repair has no allowed artifacts.",
            details={"active_repair": active_repair},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    stage = stage_by_id.get(owner)
    if stage is None:
        raise RuntimeStateError(
            f"Unknown repair owner stage: {owner}",
            details={"repair_owner": owner, "known_stages": list(stage_by_id)},
            error_code=E_ILLEGAL_TRANSITION,
        )
    artifacts_by_id = _artifact_map(artifacts)
    artifact_reasons = _completion_artifact_gate_reasons(
        workspace=ws,
        stage=stage,
        artifacts_by_id=artifacts_by_id,
    )
    if artifact_reasons:
        code = E_REQUIRED_ARTIFACT_MISSING
        if any("invalid" in item.lower() for item in artifact_reasons):
            code = E_ARTIFACT_INVALID
        _raise_completion_reasons(
            message=f"Cannot complete repair for stage '{owner}'",
            reasons=artifact_reasons,
            error_code=code,
            details={"stage_id": owner},
        )
    feedback_reasons = current_stage_feedback_blocking_reasons(
        workspace=ws,
        current_stage=owner,
        stages=stages,
        artifacts=artifacts,
    )
    if feedback_reasons:
        _raise_completion_reasons(
            message=f"Cannot complete repair for stage '{owner}'",
            reasons=feedback_reasons,
            error_code=E_ILLEGAL_TRANSITION,
            details={"stage_id": owner},
        )
    transaction_id = uuid.uuid4().hex
    now = utc_now()
    run_id = str(manifest["run_id"])
    old_registry = _read_json_if_exists(paths["artifact_registry"])
    registry_for_change_check = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=workflow,
        updated_at=now,
    )
    baseline_records = active_repair.get("artifact_baseline")
    if not isinstance(baseline_records, dict):
        raise RuntimeStateError(
            "Active repair is missing its artifact baseline.",
            details={"active_repair": active_repair},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    changed_reasons, allowed_changed = _repair_changed_artifact_reasons(
        baseline_records=baseline_records,
        registry=registry_for_change_check,
        allowed_artifacts=allowed_artifacts,
        blocked_direct_edits=blocked_direct_edits,
    )
    if changed_reasons:
        _raise_completion_reasons(
            message="Repair completion changed artifacts outside the deterministic repair route",
            reasons=changed_reasons,
            error_code=E_TRANSACTION_INTEGRITY,
            details={"stage_id": owner, "allowed_artifacts": allowed_artifacts},
        )
    if not allowed_changed:
        raise RuntimeStateError(
            "Repair completion did not modify any allowed artifact.",
            details={"stage_id": owner, "allowed_artifacts": allowed_artifacts},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    next_workflow = _workflow_after_repair_completion(
        workflow=workflow,
        stages=stages,
        active_repair=active_repair,
        reason=reason,
        now=now,
        transaction_id=transaction_id,
    )
    registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=next_workflow,
        updated_at=now,
        recovery_state={
            "recovery_event_type": "repair_completed",
            "recovery_transaction_id": transaction_id,
            "owner_stage": owner,
            "stale_artifact_baselines": (
                (next_workflow.get("last_repair_transaction") or {}).get(
                    "stale_artifact_baselines"
                )
                or {}
            ),
        },
    )
    frozen_verdict = interpret_frozen_artifact_integrity(
        old_registry=old_registry,
        registry=registry,
        workflow=workflow,
        artifacts=artifacts,
        stages=stages,
        mutating_stage=owner,
    )
    frozen_reasons = require_frozen_artifact_integrity_pass(frozen_verdict)
    if frozen_reasons:
        _raise_completion_reasons(
            message="Repair completion cannot proceed because frozen artifact integrity could not be verified",
            reasons=frozen_reasons,
            error_code=E_TRANSACTION_INTEGRITY,
            details={"stage_id": owner},
        )
    artifact_events = _changed_artifact_events(
        old_registry=old_registry, registry=registry
    )
    recovery_baselines = _recovery_stale_artifact_baselines(
        stages=stages,
        owner_stage=owner,
        baseline_records=baseline_records,
    )

    state_snapshots = _snapshot_state_files(
        paths, ("artifact_registry", "workflow_state", "event_log")
    )
    state_written = False
    try:
        _write_json_atomic(paths["artifact_registry"], registry)
        state_written = True
        _write_json_atomic(paths["workflow_state"], next_workflow)
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Repair completion partially wrote files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": owner,
                    "state_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        code = E_TRANSACTION_PARTIAL_WRITE if state_written else exc.error_code
        raise RuntimeStateError(
            "Repair completion failed while writing state files; control files were restored.",
            details={
                "transaction_id": transaction_id,
                "stage_id": owner,
                "state_error": str(exc),
                "state_details": exc.details,
                "restored": True,
            },
            error_code=code,
        ) from exc

    try:
        for event in artifact_events:
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type=str(event["event_type"]),
                actor=actor,
                artifact_id=event.get("artifact_id"),
                reason=str(event.get("reason") or ""),
                metadata={
                    **(event.get("metadata") or {}),
                    "transaction_id": transaction_id,
                },
            )
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="repair_completed",
            actor=actor,
            stage_id=owner,
            decision="repair_complete",
            reason=reason,
            metadata={
                **_repair_event_metadata(
                    {**active_repair, "transaction_id": transaction_id}
                ),
                "owner_revision_schema_version": _owner_revision_schema_version(),
                "run_id": run_id,
                "contamination_event_id": active_repair.get("contamination_event_id"),
                "owner_stage": owner,
                "artifact_id": _repair_primary_artifact_id(active_repair),
                "rerun_start_stage": next_workflow.get("current_stage"),
                "stale_artifact_baselines": recovery_baselines,
                "next_stage": next_workflow.get("current_stage"),
            },
        )
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Repair completion partially wrote files and failed rollback after event append failure.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": owner,
                    "event_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        raise RuntimeStateError(
            "Repair completion event append failed; control files were restored.",
            details={
                "transaction_id": transaction_id,
                "event_error": str(exc),
                "event_details": exc.details,
            },
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc

    state = show_runtime_state(workspace=ws)
    state["repair"] = {
        "completed": True,
        "repair_owner": owner,
        "allowed_artifacts": allowed_artifacts,
        "must_rerun_from": active_repair.get("must_rerun_from"),
        "next_stage": next_workflow.get("current_stage"),
    }
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": owner,
        "decision": "repair_complete",
    }
    return state
