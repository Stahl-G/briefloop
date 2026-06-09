"""Build, record, and validate Orchestrator control switchboards."""

from __future__ import annotations

import json
import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.controls.contract import (
    CONTROL_IDS,
    CONTROL_SELECTIONS_SCHEMA,
    CONTROL_SWITCHBOARD_SCHEMA,
    EXECUTION_TYPES,
    HUMAN_APPROVAL_CONTROLS,
    RECOMMENDATIONS,
    SELECTIONS,
    ControlSwitchboardError,
    control_switchboard_paths,
    ensure_safe_relative_path,
)
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    append_event,
    initialize_runtime_state,
    runtime_state_paths,
    utc_now,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


TEXT_TRIGGERS = {
    "executive": {"board", "management", "executive", "investor", "董事会", "管理层", "高管", "投关"},
    "case": {"case", "comparable", "benchmark", "precedent", "lesson", "案例", "对标", "可比", "先例"},
    "limitation": {"forecast", "hypothesis", "estimate", "analog", "uncertainty", "risk", "caveat", "预测", "假设", "估计", "类比", "不确定", "风险", "限制"},
    "local_signal": {"local market", "local-language", "consumer sentiment", "social signal", "市场信号", "本地", "社媒", "舆情", "消费者"},
    "pain_point": {"pain point", "complaint", "review mining", "customer feedback", "用户痛点", "消费者痛点", "投诉", "评价", "反馈"},
}


def build_control_switchboard(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "cli",
) -> dict[str, Any]:
    """Build the deterministic switchboard and append a lifecycle event."""
    ws = _require_workspace(workspace)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    state_paths = runtime_state_paths(ws)
    existing_manifest = _read_json_object_if_exists(
        state_paths["runtime_manifest"],
        label="runtime_manifest.json",
    )
    existing_runtime = (
        str(existing_manifest.get("runtime") or "")
        if isinstance(existing_manifest, dict)
        else ""
    )
    state = initialize_runtime_state(
        workspace=ws,
        repo_workdir=repo,
        runtime=existing_runtime or "controls",
        actor=actor,
    )
    manifest = state.get("manifest") or {}
    run_id = str(manifest.get("run_id") or "")
    paths = control_switchboard_paths(ws)
    now = utc_now()

    context = _load_context(ws)
    controls = _build_controls(ws, context)
    payload = {
        "schema_version": CONTROL_SWITCHBOARD_SCHEMA,
        "run_id": run_id,
        "workspace": ".",
        "created_at": now,
        "context_signature": _context_signature(ws, context=context),
        "inputs": _switchboard_inputs(ws),
        "controls": controls,
        "warnings": context["warnings"],
    }
    errors = validate_switchboard_payload(payload)
    if errors:
        raise ControlSwitchboardError(
            "Generated control switchboard is invalid.",
            details={"errors": errors},
        )

    archived_selections = _archive_selections_for_other_run(
        paths=paths,
        current_run_id=run_id,
    )
    archived_stale_selections = _archive_selections_for_other_context(
        paths=paths,
        current_run_id=run_id,
        current_context_signature=str(payload.get("context_signature") or ""),
    )
    _write_json_atomic(paths["orchestrator_control_switchboard"], payload)
    append_event(
        workspace=ws,
        run_id=run_id,
        event_type="control_switchboard_built",
        actor=actor,
        reason="Orchestrator control switchboard built.",
        metadata={
            "orchestrator_control_switchboard": "output/intermediate/orchestrator_control_switchboard.json",
            "control_count": len(controls),
            "required_count": sum(1 for item in controls if item.get("recommendation") == "required"),
            "human_approval_count": sum(1 for item in controls if item.get("requires_human_approval")),
            "archived_control_selections": archived_selections,
            "archived_stale_control_selections": archived_stale_selections,
        },
    )
    return _state_payload(ws, switchboard=payload)


def show_control_switchboard(*, workspace: str | Path) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = control_switchboard_paths(ws)
    switchboard = _load_switchboard(paths=paths)
    selections = _read_json_object_if_exists(
        paths["control_selections"],
        label="control_selections.json",
    )
    return _state_payload(ws, switchboard=switchboard, selections=selections)


def select_control(
    *,
    workspace: str | Path,
    control_id: str,
    selection: str,
    reason: str,
    approved_by_human: bool = False,
    human_approval_ref: str | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    """Record an Orchestrator selection without executing the control."""
    ws = _require_workspace(workspace)
    if control_id not in CONTROL_IDS:
        raise ControlSwitchboardError(
            f"Unknown control id: {control_id}",
            details={"control_id": control_id, "known_controls": sorted(CONTROL_IDS)},
        )
    if selection not in SELECTIONS:
        raise ControlSwitchboardError(
            f"Invalid control selection: {selection}",
            details={"selection": selection, "allowed_selections": sorted(SELECTIONS)},
        )
    if not reason.strip():
        raise ControlSwitchboardError("Control selection reason is required.")

    paths = control_switchboard_paths(ws)
    switchboard = _load_switchboard(paths=paths)
    if switchboard is None:
        raise ControlSwitchboardError(
            "Control switchboard is missing. Run controls build-switchboard first.",
            details={"path": str(paths["orchestrator_control_switchboard"])},
        )
    switchboard_errors = validate_switchboard_payload(switchboard)
    switchboard_errors.extend(_switchboard_stale_reasons(ws, switchboard))
    if switchboard_errors:
        raise ControlSwitchboardError(
            "Control switchboard is invalid.",
            details={"errors": switchboard_errors},
        )
    control = _control_by_id(switchboard, control_id)
    if control is None:
        raise ControlSwitchboardError(
            f"Control id is not present in switchboard: {control_id}",
            details={"control_id": control_id},
        )

    run_id = str(switchboard.get("run_id") or _current_run_id(ws) or "")
    now = utc_now()
    existing = _read_json_object_if_exists(
        paths["control_selections"],
        label="control_selections.json",
    )
    switchboard_context_signature = str(switchboard.get("context_signature") or "")
    archived_stale_selections = None
    if existing is None:
        payload = {
            "schema_version": CONTROL_SELECTIONS_SCHEMA,
            "run_id": run_id,
            "switchboard_context_signature": switchboard_context_signature,
            "created_at": now,
            "updated_at": now,
            "selections": [],
        }
    else:
        payload = existing
        if payload.get("run_id") != run_id:
            raise ControlSwitchboardError(
                "control_selections.json run_id does not match switchboard run_id.",
                details={"switchboard_run_id": run_id, "selections_run_id": payload.get("run_id")},
            )
        if payload.get("switchboard_context_signature") != switchboard_context_signature:
            archived_stale_selections = _archive_control_selections_path(
                paths=paths,
                existing=payload,
                suffix="stale",
            )
            payload = {
                "schema_version": CONTROL_SELECTIONS_SCHEMA,
                "run_id": run_id,
                "switchboard_context_signature": switchboard_context_signature,
                "created_at": now,
                "updated_at": now,
                "selections": [],
            }
        else:
            payload["updated_at"] = now

    requires_human = bool(control.get("requires_human_approval"))
    clean_human_approval_ref = human_approval_ref.strip() if human_approval_ref else None
    if (
        requires_human
        and selection == "enable"
        and approved_by_human
        and not clean_human_approval_ref
    ):
        raise ControlSwitchboardError(
            "Human approval reference is required when enabling a human-approval control.",
            details={"control_id": control_id},
        )
    execution_ready = (
        selection == "enable"
        and (not requires_human or bool(approved_by_human))
    )
    entry = {
        "selection_id": f"sel_{uuid.uuid4().hex[:12]}",
        "control_id": control_id,
        "selection": selection,
        "selected_at": now,
        "selected_by": actor,
        "reason": reason.strip(),
        "approved_by_human": bool(approved_by_human),
        "human_approval_ref": clean_human_approval_ref,
        "switchboard_context_signature": switchboard_context_signature,
        "execution_ready": bool(execution_ready),
        "executed": False,
        "execution_ref": None,
        "metadata": {},
    }
    selections = [item for item in payload.get("selections", []) if item.get("control_id") != control_id]
    selections.append(entry)
    payload["selections"] = selections

    selection_errors = validate_selections_payload(payload, switchboard=switchboard, strict=False)
    if selection_errors:
        raise ControlSwitchboardError(
            "Control selection payload is invalid.",
            details={"errors": selection_errors},
        )

    _write_json_atomic(paths["control_selections"], payload)
    append_event(
        workspace=ws,
        run_id=run_id,
        event_type="control_selection_recorded",
        actor=actor,
        reason=reason.strip(),
        metadata={
            "control_selections": "output/intermediate/control_selections.json",
            "control_id": control_id,
            "selection": selection,
            "approved_by_human": bool(approved_by_human),
            "execution_ready": bool(execution_ready),
            "switchboard_context_signature": switchboard_context_signature,
            "archived_stale_control_selections": archived_stale_selections,
        },
    )
    return _state_payload(ws, switchboard=switchboard, selections=payload)


def validate_control_switchboard(
    *,
    workspace: str | Path,
    strict: bool = False,
    actor: str = "cli",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = control_switchboard_paths(ws)
    switchboard = _load_switchboard(paths=paths)
    selections = _read_json_object_if_exists(
        paths["control_selections"],
        label="control_selections.json",
    )
    errors: list[str] = []
    if switchboard is None:
        errors.append("orchestrator_control_switchboard.json is missing.")
    else:
        errors.extend(validate_switchboard_payload(switchboard))
        errors.extend(_switchboard_stale_reasons(ws, switchboard))
        errors.extend(validate_selections_payload(selections, switchboard=switchboard, strict=strict))

    result = {
        "ok": not errors,
        "switchboard_present": switchboard is not None,
        "selection_present": selections is not None,
        "control_count": len((switchboard or {}).get("controls") or []),
        "selection_count": len((selections or {}).get("selections") or []),
        "strict": strict,
        "errors": errors,
    }
    if switchboard is not None:
        run_id = str(_current_run_id(ws) or switchboard.get("run_id") or "")
        try:
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type="control_selection_validated",
                actor=actor,
                reason="Control switchboard validation completed.",
                metadata={
                    "ok": result["ok"],
                    "strict": strict,
                    "error_count": len(errors),
                },
            )
        except RuntimeStateError:
            if result["ok"]:
                raise
    return result


def validate_switchboard_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != CONTROL_SWITCHBOARD_SCHEMA:
        errors.append("orchestrator_control_switchboard.json has an unsupported schema_version.")
    if not payload.get("run_id"):
        errors.append("orchestrator_control_switchboard.json run_id is required.")
    if "context_signature" in payload and not isinstance(payload.get("context_signature"), str):
        errors.append("orchestrator_control_switchboard.json context_signature must be a string.")
    controls = payload.get("controls")
    if not isinstance(controls, list):
        errors.append("orchestrator_control_switchboard.json controls must be a list.")
        return errors
    seen: set[str] = set()
    for idx, control in enumerate(controls):
        prefix = f"controls[{idx}]"
        if not isinstance(control, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        control_id = str(control.get("control_id") or "")
        if control_id not in CONTROL_IDS:
            errors.append(f"{prefix}.control_id is unknown: {control_id}.")
        if control_id in seen:
            errors.append(f"{prefix}.control_id is duplicated: {control_id}.")
        seen.add(control_id)
        if control.get("recommendation") not in RECOMMENDATIONS:
            errors.append(f"{prefix}.recommendation must be one of {sorted(RECOMMENDATIONS)}.")
        if control.get("execution_type") not in EXECUTION_TYPES:
            errors.append(f"{prefix}.execution_type must be one of {sorted(EXECUTION_TYPES)}.")
        if not isinstance(control.get("requires_human_approval"), bool):
            errors.append(f"{prefix}.requires_human_approval must be a boolean.")
        if control_id in HUMAN_APPROVAL_CONTROLS and control.get("requires_human_approval") is not True:
            errors.append(f"{prefix}.requires_human_approval must be true for privacy-sensitive controls.")
        for path_key in ("inputs", "outputs"):
            values = control.get(path_key) or []
            if not isinstance(values, list):
                errors.append(f"{prefix}.{path_key} must be a list.")
                continue
            for path_idx, value in enumerate(values):
                if not isinstance(value, str):
                    errors.append(f"{prefix}.{path_key}[{path_idx}] must be a string.")
                    continue
                try:
                    ensure_safe_relative_path(value, label=f"{prefix}.{path_key}[{path_idx}]")
                except ControlSwitchboardError as exc:
                    errors.append(str(exc))
    return errors


def validate_selections_payload(
    payload: dict[str, Any] | None,
    *,
    switchboard: dict[str, Any],
    strict: bool,
) -> list[str]:
    errors: list[str] = []
    controls = switchboard.get("controls") or []
    by_id = {str(item.get("control_id")): item for item in controls if isinstance(item, dict)}
    if payload is None:
        if strict:
            required = sorted(
                control_id
                for control_id, control in by_id.items()
                if control.get("recommendation") == "required"
            )
            if required:
                errors.append(f"control_selections.json is missing required selections: {', '.join(required)}.")
        return errors
    if payload.get("schema_version") != CONTROL_SELECTIONS_SCHEMA:
        errors.append("control_selections.json has an unsupported schema_version.")
    if payload.get("run_id") != switchboard.get("run_id"):
        errors.append("control_selections.json run_id must match switchboard run_id.")
    switchboard_context_signature = str(switchboard.get("context_signature") or "")
    if payload.get("switchboard_context_signature") != switchboard_context_signature:
        errors.append("control_selections.json switchboard_context_signature must match the current switchboard context_signature.")
    selections = payload.get("selections")
    if not isinstance(selections, list):
        errors.append("control_selections.json selections must be a list.")
        return errors
    seen: set[str] = set()
    for idx, item in enumerate(selections):
        prefix = f"selections[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        control_id = str(item.get("control_id") or "")
        if control_id not in by_id:
            errors.append(f"{prefix}.control_id is unknown or not present in switchboard: {control_id}.")
        if control_id in seen:
            errors.append(f"{prefix}.control_id has more than one effective selection: {control_id}.")
        seen.add(control_id)
        if item.get("selection") not in SELECTIONS:
            errors.append(f"{prefix}.selection must be one of {sorted(SELECTIONS)}.")
        if item.get("switchboard_context_signature") != switchboard_context_signature:
            errors.append(f"{prefix}.switchboard_context_signature must match the current switchboard context_signature.")
        if not str(item.get("reason") or "").strip():
            errors.append(f"{prefix}.reason is required.")
        if not isinstance(item.get("approved_by_human"), bool):
            errors.append(f"{prefix}.approved_by_human must be a boolean.")
        if item.get("executed") is not False:
            errors.append(f"{prefix}.executed must remain false; selection is not execution.")
        control = by_id.get(control_id) or {}
        if (
            control.get("requires_human_approval")
            and item.get("selection") == "enable"
            and item.get("approved_by_human") is True
            and not str(item.get("human_approval_ref") or "").strip()
        ):
            errors.append(f"{prefix}.human_approval_ref is required for approved human-approval controls.")
        expected_ready = (
            item.get("selection") == "enable"
            and (not control.get("requires_human_approval") or item.get("approved_by_human") is True)
        )
        if item.get("execution_ready") != expected_ready:
            errors.append(f"{prefix}.execution_ready is inconsistent with selection and human approval.")
    if strict:
        missing_required = sorted(
            control_id
            for control_id, control in by_id.items()
            if control.get("recommendation") == "required" and control_id not in seen
        )
        if missing_required:
            errors.append(f"Missing required control selections: {', '.join(missing_required)}.")
    return errors


def _build_controls(ws: Path, context: dict[str, Any]) -> list[dict[str, Any]]:
    text = context["search_text"]
    current_stage = context["current_stage"]
    files = context["files"]
    controls = [
        _control_quality_gates(current_stage=current_stage, files=files),
        _control_feedback(files=files),
        _control_analysis_blocks(text=text, files=files),
        _control_case_applicability(text=text, claim_text=context["claim_text"]),
        _control_limitation_hygiene(text=text, claim_text=context["claim_text"]),
        _control_local_signal(text=text),
        _control_consumer_pain_point(text=text),
        _control_provenance(current_stage=current_stage, files=files),
    ]
    return controls


def _base_control(
    *,
    control_id: str,
    title: str,
    recommendation: str,
    requires_human_approval: bool,
    reason: str,
    execution_type: str,
    execution_hint: str,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    selection_required: bool = False,
    blocking_semantics: str = "",
) -> dict[str, Any]:
    return {
        "control_id": control_id,
        "title": title,
        "recommendation": recommendation,
        "requires_human_approval": requires_human_approval,
        "reason": reason,
        "execution_type": execution_type,
        "execution_hint": execution_hint,
        "inputs": inputs or [],
        "outputs": outputs or [],
        "blocking_semantics": blocking_semantics,
        "selection_required": selection_required,
        "metadata": {},
    }


def _control_quality_gates(*, current_stage: str | None, files: dict[str, bool]) -> dict[str, Any]:
    required = current_stage in {"auditor", "finalize"} or files["audited_brief"] or files["brief"]
    recommendation = "required" if required else "recommended"
    reason = (
        "Auditor/finalize path should run material-fact, freshness, and target-relevance gates before delivery."
        if required
        else "Quality gates are available when auditable draft and claim ledger artifacts exist."
    )
    return _base_control(
        control_id="quality_gates",
        title="Deterministic quality gates",
        recommendation=recommendation,
        requires_human_approval=False,
        reason=reason,
        execution_type="cli",
        execution_hint="multi-agent-brief gates check --workspace <workspace>",
        inputs=["output/intermediate/audited_brief.md", "output/intermediate/claim_ledger.json"],
        outputs=["output/intermediate/quality_gate_report.json"],
        selection_required=required,
        blocking_semantics="Existing quality gate runtime blockers apply only after gates check creates a report or policy enables gates.",
    )


def _control_feedback(*, files: dict[str, bool]) -> dict[str, Any]:
    if files["feedback_issues"] and not files["repair_plan"]:
        recommendation = "required"
        reason = "Feedback issues exist and no repair plan is present."
    elif files["feedback_issues"]:
        recommendation = "recommended"
        reason = "Feedback issue state exists; Orchestrator should inspect repair status before continuing."
    else:
        recommendation = "suggested"
        reason = "Feedback controls are available if audit findings or human feedback need repair planning."
    return _base_control(
        control_id="feedback_repair_plan",
        title="Feedback issue and repair-plan controls",
        recommendation=recommendation,
        requires_human_approval=False,
        reason=reason,
        execution_type="cli",
        execution_hint="multi-agent-brief feedback show --workspace <workspace> --json",
        inputs=["output/intermediate/feedback_issues.json"],
        outputs=["output/intermediate/repair_plan.json"],
        selection_required=recommendation == "required",
        blocking_semantics="Existing feedback runtime blockers apply only from feedback_issues.json and repair_plan.json.",
    )


def _control_analysis_blocks(*, text: str, files: dict[str, bool]) -> dict[str, Any]:
    hit = _has_any(text, TEXT_TRIGGERS["executive"]) or files["claim_ledger"]
    return _base_control(
        control_id="analysis_blocks",
        title="Analysis block grouping",
        recommendation="recommended" if hit else "suggested",
        requires_human_approval=False,
        reason=(
            "Executive or claim-ledger context benefits from grouped analysis blocks."
            if hit
            else "Analysis blocks can be used when the claim ledger is ready."
        ),
        execution_type="cli",
        execution_hint="multi-agent-brief analysis-blocks --ledger <workspace>/output/intermediate/claim_ledger.json --output <workspace>/output/intermediate/analysis_blocks.json",
        inputs=["output/intermediate/claim_ledger.json"],
        outputs=["output/intermediate/analysis_blocks.json"],
    )


def _control_case_applicability(*, text: str, claim_text: str) -> dict[str, Any]:
    hit = _has_any(f"{text}\n{claim_text}", TEXT_TRIGGERS["case"])
    return _base_control(
        control_id="case_applicability",
        title="Case applicability checks",
        recommendation="recommended" if hit else "not_applicable",
        requires_human_approval=False,
        reason=(
            "Case/comparable language is present; auditor should apply applicability checks."
            if hit
            else "No explicit case/comparable trigger detected."
        ),
        execution_type="audit_rule",
        execution_hint="Ask auditor to apply case applicability checks to case/comparable claims.",
        inputs=["output/intermediate/claim_ledger.json"],
        outputs=[],
    )


def _control_limitation_hygiene(*, text: str, claim_text: str) -> dict[str, Any]:
    hit = _has_any(f"{text}\n{claim_text}", TEXT_TRIGGERS["limitation"])
    return _base_control(
        control_id="limitation_hygiene",
        title="Limitation hygiene checks",
        recommendation="recommended" if hit else "suggested",
        requires_human_approval=False,
        reason=(
            "Forecast, hypothesis, analogy, risk, or caveat language is present."
            if hit
            else "Limitation hygiene is available for uncertainty-heavy briefs."
        ),
        execution_type="cli",
        execution_hint="multi-agent-brief limitation-hygiene --ledger <workspace>/output/intermediate/claim_ledger.json --output <workspace>/output/intermediate/limitation_hygiene_report.json",
        inputs=["output/intermediate/claim_ledger.json"],
        outputs=["output/intermediate/limitation_hygiene_report.json"],
    )


def _control_local_signal(*, text: str) -> dict[str, Any]:
    hit = _has_any(text, TEXT_TRIGGERS["local_signal"])
    return _base_control(
        control_id="local_signal_discovery",
        title="Local signal discovery",
        recommendation="recommended" if hit else "suggested",
        requires_human_approval=True,
        reason=(
            "Local market or social-signal context is present; human approval is required before collection."
            if hit
            else "Local signal discovery is available but requires human approval before any collection."
        ),
        execution_type="config_review",
        execution_hint="Review and configure sources.yaml local signal discovery; do not collect without explicit approval.",
        inputs=["sources.yaml"],
        outputs=[],
    )


def _control_consumer_pain_point(*, text: str) -> dict[str, Any]:
    hit = _has_any(text, TEXT_TRIGGERS["pain_point"])
    return _base_control(
        control_id="consumer_pain_point_discovery",
        title="Consumer pain-point discovery",
        recommendation="recommended" if hit else "suggested",
        requires_human_approval=True,
        reason=(
            "Consumer pain-point or review-mining context is present; human approval is required before collection."
            if hit
            else "Consumer pain-point discovery is available but requires human approval before any collection."
        ),
        execution_type="config_review",
        execution_hint="Review consumer pain-point discovery scope with a human before configuring source collection.",
        inputs=["sources.yaml"],
        outputs=[],
    )


def _control_provenance(*, current_stage: str | None, files: dict[str, bool]) -> dict[str, Any]:
    ready = files["claim_ledger"] and (files["audited_brief"] or files["brief"] or current_stage in {None, "finalize"})
    return _base_control(
        control_id="provenance_projection",
        title="Deterministic provenance projection",
        recommendation="recommended" if ready else "suggested",
        requires_human_approval=False,
        reason=(
            "Audit/final artifacts exist; provenance projection can summarize existing control records."
            if ready
            else "Provenance projection is available after runtime and audit/control files exist."
        ),
        execution_type="cli",
        execution_hint="multi-agent-brief provenance build --workspace <workspace>",
        inputs=[
            "output/intermediate/runtime_manifest.json",
            "output/intermediate/artifact_registry.json",
            "output/intermediate/event_log.jsonl",
        ],
        outputs=["output/intermediate/provenance_graph.json"],
        blocking_semantics="Provenance remains audit/debug tooling and is not a finalize gate by default.",
    )


def _state_payload(
    ws: Path,
    *,
    switchboard: dict[str, Any] | None,
    selections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = control_switchboard_paths(ws)
    if selections is None:
        selections = _read_json_object_if_exists(paths["control_selections"], label="control_selections.json")
    validation_errors = validate_switchboard_payload(switchboard) if switchboard is not None else []
    if switchboard is not None:
        validation_errors.extend(_switchboard_stale_reasons(ws, switchboard))
    if switchboard is not None:
        validation_errors.extend(validate_selections_payload(selections, switchboard=switchboard, strict=False))
    return {
        "ok": switchboard is not None and not validation_errors,
        "control_switchboard_path": str(paths["orchestrator_control_switchboard"]),
        "control_selections_path": str(paths["control_selections"]),
        "orchestrator_control_switchboard": switchboard,
        "control_selections": selections,
        "validation": {
            "ok": not validation_errors,
            "errors": validation_errors,
            "control_count": len((switchboard or {}).get("controls") or []),
            "selection_count": len((selections or {}).get("selections") or []),
        },
    }


def _switchboard_inputs(ws: Path) -> dict[str, str]:
    candidates = {
        "config": "config.yaml",
        "sources": "sources.yaml",
        "user": "user.md",
        "audience_profile_snapshot": "output/intermediate/audience_profile_snapshot.md",
        "runtime_manifest": "output/intermediate/runtime_manifest.json",
        "workflow_state": "output/intermediate/workflow_state.json",
        "artifact_registry": "output/intermediate/artifact_registry.json",
    }
    return {key: rel for key, rel in candidates.items() if (ws / rel).exists()}


def _load_switchboard(*, paths: dict[str, Path]) -> dict[str, Any] | None:
    return _read_json_object_if_exists(
        paths["orchestrator_control_switchboard"],
        label="orchestrator_control_switchboard.json",
    )


def refresh_control_switchboard_if_stale(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "cli",
) -> dict[str, Any] | None:
    """Refresh an existing switchboard if runtime context changed."""
    ws = _require_workspace(workspace)
    paths = control_switchboard_paths(ws)
    switchboard = _read_json_object_if_exists(
        paths["orchestrator_control_switchboard"],
        label="orchestrator_control_switchboard.json",
    )
    if switchboard is None:
        return None
    if not _switchboard_stale_reasons(ws, switchboard):
        return _state_payload(ws, switchboard=switchboard)
    return build_control_switchboard(workspace=ws, repo_workdir=repo_workdir, actor=actor)


def _switchboard_stale_reasons(ws: Path, switchboard: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    current_run_id = _current_run_id(ws) or ""
    if current_run_id and switchboard.get("run_id") != current_run_id:
        reasons.append(
            "orchestrator_control_switchboard.json run_id does not match current runtime run_id; "
            "run controls build-switchboard to rebuild it."
        )
    expected_signature = _context_signature(ws)
    if switchboard.get("context_signature") != expected_signature:
        reasons.append(
            "orchestrator_control_switchboard.json is stale for the current workspace context; "
            "run controls build-switchboard to rebuild it."
        )
    return reasons


def _context_signature(ws: Path, *, context: dict[str, Any] | None = None) -> str:
    if context is None:
        context = _load_context(ws)
    payload = {
        "run_id": _current_run_id(ws) or "",
        "current_stage": context.get("current_stage") or "",
        "files": context.get("files") or {},
        "inputs": {
            rel_path: _file_fingerprint(ws / rel_path)
            for rel_path in (
                "config.yaml",
                "sources.yaml",
                "user.md",
                "output/intermediate/audience_profile_snapshot.md",
                "output/intermediate/claim_ledger.json",
                "output/intermediate/audited_brief.md",
                "output/brief.md",
                "output/intermediate/feedback_issues.json",
                "output/intermediate/repair_plan.json",
                "output/intermediate/quality_gate_report.json",
            )
        },
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {"exists": False}
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return {"exists": True, "readable": False}
    return {
        "exists": True,
        "size": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def _archive_selections_for_other_run(
    *,
    paths: dict[str, Path],
    current_run_id: str,
) -> str | None:
    selections_path = paths["control_selections"]
    existing = _read_json_object_if_exists(selections_path, label="control_selections.json")
    if existing is None or existing.get("run_id") == current_run_id:
        return None
    return _archive_control_selections_path(
        paths=paths,
        existing=existing,
        suffix=None,
    )


def _archive_selections_for_other_context(
    *,
    paths: dict[str, Path],
    current_run_id: str,
    current_context_signature: str,
) -> str | None:
    selections_path = paths["control_selections"]
    existing = _read_json_object_if_exists(selections_path, label="control_selections.json")
    if existing is None:
        return None
    if existing.get("run_id") != current_run_id:
        return None
    if existing.get("switchboard_context_signature") == current_context_signature:
        return None
    return _archive_control_selections_path(
        paths=paths,
        existing=existing,
        suffix="stale",
    )


def _archive_control_selections_path(
    *,
    paths: dict[str, Path],
    existing: dict[str, Any],
    suffix: str | None,
) -> str:
    selections_path = paths["control_selections"]
    old_run_id = str(existing.get("run_id") or "unknown")
    suffix_part = f".{suffix}" if suffix else ""
    archive = selections_path.with_name(f"control_selections.{old_run_id}{suffix_part}.json")
    if archive.exists():
        archive = selections_path.with_name(
            f"control_selections.{old_run_id}{suffix_part}.{uuid.uuid4().hex[:8]}.json"
        )
    os.replace(selections_path, archive)
    return archive.name


def _load_context(ws: Path) -> dict[str, Any]:
    warnings: list[str] = []
    config = _read_yaml(ws / "config.yaml", warnings=warnings)
    sources = _read_yaml(ws / "sources.yaml", warnings=warnings)
    user = _read_text_if_exists(ws / "user.md")
    audience_snapshot = _read_text_if_exists(ws / "output" / "intermediate" / "audience_profile_snapshot.md")
    workflow = _read_json_object_if_exists(runtime_state_paths(ws)["workflow_state"], label="workflow_state.json") or {}
    claim_ledger = _read_json_value_if_exists(ws / "output" / "intermediate" / "claim_ledger.json", label="claim_ledger.json")
    search_text = "\n".join(
        _redact_long_text(part)
        for part in (
            _dump_public_context(config),
            _dump_public_context(sources),
            user,
            audience_snapshot,
        )
        if part
    ).lower()
    files = {
        "audited_brief": (ws / "output" / "intermediate" / "audited_brief.md").exists(),
        "brief": (ws / "output" / "brief.md").exists(),
        "claim_ledger": claim_ledger is not None,
        "feedback_issues": (ws / "output" / "intermediate" / "feedback_issues.json").exists(),
        "repair_plan": (ws / "output" / "intermediate" / "repair_plan.json").exists(),
        "quality_gate_report": (ws / "output" / "intermediate" / "quality_gate_report.json").exists(),
    }
    return {
        "warnings": warnings,
        "search_text": search_text,
        "claim_text": _claim_metadata_text(claim_ledger).lower(),
        "current_stage": workflow.get("current_stage"),
        "files": files,
    }


def _read_yaml(path: Path, *, warnings: list[str]) -> Any:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        warnings.append(f"Failed to read {path.name}: {exc}")
        return {}


def _dump_public_context(value: Any) -> str:
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            if str(key).lower() in {"api_key", "token", "secret", "password"}:
                continue
            parts.append(f"{key}: {_dump_public_context(item)}")
        return "\n".join(parts)
    if isinstance(value, list):
        return "\n".join(_dump_public_context(item) for item in value)
    return str(value)


def _claim_metadata_text(payload: Any) -> str:
    if not payload:
        return ""
    claims = payload.get("claims") if isinstance(payload, dict) and isinstance(payload.get("claims"), list) else payload
    if not isinstance(claims, list):
        return ""
    parts: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        metadata = claim.get("metadata") if isinstance(claim.get("metadata"), dict) else {}
        for key in ("claim_type", "evidence_relation", "confidence"):
            if claim.get(key):
                parts.append(str(claim.get(key)))
        for key in ("claim_type", "evidence_relation", "confidence", "limitations", "applicability_reason"):
            if metadata.get(key):
                parts.append(str(metadata.get(key)))
    return "\n".join(parts)


def _redact_long_text(text: str) -> str:
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", r"\1: [redacted]", text)
    return text[:12000]


def _has_any(text: str, needles: set[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _control_by_id(switchboard: dict[str, Any], control_id: str) -> dict[str, Any] | None:
    for item in switchboard.get("controls") or []:
        if isinstance(item, dict) and item.get("control_id") == control_id:
            return item
    return None


def _current_run_id(ws: Path) -> str | None:
    manifest = _read_json_object_if_exists(runtime_state_paths(ws)["runtime_manifest"], label="runtime_manifest.json")
    if manifest:
        return str(manifest.get("run_id") or "")
    return None


def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not (ws / "config.yaml").exists():
        raise ControlSwitchboardError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            details={"workspace": str(ws)},
        )
    return ws


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json_object_if_exists(path: Path, *, label: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ControlSwitchboardError(
            f"Invalid JSON {label}: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise ControlSwitchboardError(
            f"Failed to read {label}: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise ControlSwitchboardError(
            f"{label} must contain a JSON object: {path}",
            details={"path": str(path)},
        )
    return data


def _read_json_value_if_exists(path: Path, *, label: str) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ControlSwitchboardError(
            f"Invalid JSON {label}: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise ControlSwitchboardError(
            f"Failed to read {label}: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ControlSwitchboardError(
            f"Failed to write control switchboard file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
