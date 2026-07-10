"""Stage and finalize completion transactions.

Validates completion targets, applies topology satisfaction, appends
transaction events, snapshots the analyst draft, and owns the
auditable-target downstream guard.
"""

from __future__ import annotations

import os
import shlex
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.contracts.target_contract import (
    AUDIT_BINDING_SCHEMA,
    auditable_gate_has_only_final_abstract_advisory_warnings,
    load_experiment_080_condition_metadata,
    project_assessment_target_status,
    repair_history_transaction_ids_for_artifact,
)
from multi_agent_brief.feedback.feedback_contract import current_stage_feedback_blocking_reasons
from multi_agent_brief.quality_gates.contract import current_stage_quality_gate_blocking_reasons
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.orchestrator.runtime_state._io import (
    _read_json,
    _read_json_if_exists,
    _restore_state_files,
    _sha256_file,
    _snapshot_state_files,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state._transactions import (
    _archive_finalized_state_if_needed,
    _load_manifest_and_workflow,
    _persist_run_contamination,
    _preflight_transaction_files,
    _restore_file_paths,
    _snapshot_file_paths,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    _artifact_registry_path,
    _artifact_registry_sha,
    _build_artifact_registry,
    _changed_artifact_events,
    interpret_frozen_artifact_integrity,
    require_frozen_artifact_integrity_pass,
)
from multi_agent_brief.orchestrator.runtime_state.claim_ledger_freeze import _claim_ledger_freeze_reasons
from multi_agent_brief.orchestrator.runtime_state.completion_gates import (
    _completion_artifact_gate_reasons,
    _fast_rerun_finalize_freshness_snapshot,
    _finalize_completion_reasons,
    _quality_gate_pass_reasons,
    _raise_completion_reasons,
    _role_topology_from_policy_pack,
    _source_discovery_evidence_reasons,
    _topology_satisfaction_artifact_reasons,
    _topology_satisfaction_rules,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    _artifact_map,
    _stage_ids,
    load_artifact_contracts,
    load_default_policy_pack,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ARTIFACT_INVALID,
    E_ASSESSMENT_TARGET_COMPLETE,
    E_COMPLETION_TRANSACTION_REQUIRED,
    E_ILLEGAL_TRANSITION,
    E_QUALITY_GATE_REQUIRED,
    E_READER_FINAL_GATE_FAILED,
    E_REQUIRED_ARTIFACT_MISSING,
    E_STAGE_ALREADY_COMPLETED,
    E_STAGE_MISMATCH,
    E_TRANSACTION_INTEGRITY,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
    _wrap_archive_error,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    append_event,
    read_event_log_records_strict,
)
from multi_agent_brief.orchestrator.runtime_state.identity import utc_now
from multi_agent_brief.orchestrator.runtime_state.lifecycle import show_runtime_state
from multi_agent_brief.orchestrator.runtime_state.manifest import (
    _assert_manifest_extensions_preserved,
    _preserved_manifest_extensions,
)
from multi_agent_brief.orchestrator.runtime_state.paths import (
    _require_workspace,
    _workspace_relative,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.repair import (
    raise_if_active_repair_open,
)
from multi_agent_brief.orchestrator.runtime_state.trajectory import (
    _raise_if_trajectory_narrows_success_path,
    _workflow_with_trajectory_decision_narrowing,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    STAGE_BLOCKED,
    STAGE_COMPLETE,
    STAGE_PENDING,
    STAGE_READY,
    STAGE_SKIPPED,
    _allowed_decisions_for_stage,
    _next_stage_id,
    _stage_status,
    _status_entry,
    _workflow_after_completion,
)
from multi_agent_brief.orchestrator.run_archive import (
    RunArchiveError,
    preflight_finalized_run_archive,
)
from multi_agent_brief.orchestrator.run_integrity import finalize_run_integrity as _finalize_run_integrity


ANALYST_DRAFT_SNAPSHOT_PATH = Path("output/intermediate/analyst_draft_snapshot.md")


def _manifest_with_fast_rerun_freshness_at_finalize(
    manifest: dict[str, Any],
    freshness_at_finalize: dict[str, Any] | None,
) -> dict[str, Any]:
    record = (
        manifest.get("fact_layer_import")
        if isinstance(manifest.get("fact_layer_import"), dict)
        else None
    )
    if not record or not freshness_at_finalize:
        return manifest
    next_manifest = dict(manifest)
    next_record = dict(record)
    next_record["freshness_at_finalize"] = freshness_at_finalize
    next_manifest["fact_layer_import"] = next_record
    return next_manifest


def _older_stage_replay_message(
    *,
    stage_id: str,
    current_stage: str | None,
    stages: list[dict[str, Any]],
    workflow: dict[str, Any],
) -> str:
    if current_stage is None or stage_id == current_stage:
        return ""
    stage_ids = _stage_ids(stages)
    if stage_id not in stage_ids or current_stage not in stage_ids:
        return ""
    if stage_ids.index(stage_id) >= stage_ids.index(current_stage):
        return ""
    statuses = (
        workflow.get("stage_statuses")
        if isinstance(workflow.get("stage_statuses"), dict)
        else {}
    )
    downstream_ids = stage_ids[stage_ids.index(stage_id) + 1 :]
    downstream_touched = [
        item
        for item in downstream_ids
        if ((statuses.get(item) or {}).get("status") or "")
        in {STAGE_COMPLETE, STAGE_READY, STAGE_BLOCKED, STAGE_SKIPPED}
    ]
    if not downstream_touched:
        return ""
    return (
        f"Stage-complete was attempted for older stage '{stage_id}' after downstream "
        f"stage '{downstream_touched[0]}' already existed."
    )


def _validate_completion_target(
    *,
    stage_id: str,
    workflow: dict[str, Any],
    stage_by_id: dict[str, dict[str, Any]],
    finalize: bool,
) -> dict[str, Any]:
    if stage_id not in stage_by_id:
        raise RuntimeStateError(
            f"Unknown stage: {stage_id}",
            details={"stage_id": stage_id, "known_stages": list(stage_by_id)},
            error_code=E_ILLEGAL_TRANSITION,
        )
    current_stage = workflow.get("current_stage")
    if current_stage is None and _stage_status(workflow, stage_id) == STAGE_COMPLETE:
        raise RuntimeStateError(
            f"Stage '{stage_id}' is already complete.",
            details={"stage_id": stage_id},
            error_code=E_STAGE_ALREADY_COMPLETED,
        )
    if stage_id != current_stage:
        if _stage_status(workflow, stage_id) == STAGE_COMPLETE:
            raise RuntimeStateError(
                f"Stage '{stage_id}' is already complete.",
                details={"stage_id": stage_id, "current_stage": current_stage},
                error_code=E_STAGE_ALREADY_COMPLETED,
            )
        raise RuntimeStateError(
            f"Completion stage '{stage_id}' does not match current stage '{current_stage}'.",
            details={"stage_id": stage_id, "current_stage": current_stage},
            error_code=E_STAGE_MISMATCH,
        )
    if finalize and stage_id != "finalize":
        raise RuntimeStateError(
            "finalize-complete can only complete the finalize stage.",
            details={"stage_id": stage_id},
            error_code=E_ILLEGAL_TRANSITION,
        )
    if not finalize and stage_id == "finalize":
        raise RuntimeStateError(
            "stage-complete cannot complete the finalize stage; use finalize-complete.",
            details={"stage_id": stage_id},
            error_code=E_ILLEGAL_TRANSITION,
        )
    stage = stage_by_id[stage_id]
    decision = "finalize" if finalize else "continue"
    allowed = [str(item) for item in (stage.get("allowed_decisions") or [])]
    if decision not in allowed:
        raise RuntimeStateError(
            f"Decision '{decision}' is not allowed for stage '{stage_id}'.",
            details={
                "stage_id": stage_id,
                "decision": decision,
                "stage_allowed_decisions": allowed,
            },
            error_code=E_ILLEGAL_TRANSITION,
        )
    return stage


def _auditor_completion_metadata(
    *,
    workspace: Path,
    registry: dict[str, Any],
    event_records: list[dict[str, Any]],
    transaction_id: str,
) -> dict[str, Any]:
    ledger_sha = _artifact_registry_sha(registry, "claim_ledger")
    audited_brief_sha = _artifact_registry_sha(registry, "audited_brief")
    audit_sha = _artifact_registry_sha(registry, "audit_report")
    ledger_path = workspace / _artifact_registry_path(
        registry,
        "claim_ledger",
        "output/intermediate/claim_ledger.json",
    )
    audited_brief_path = workspace / _artifact_registry_path(
        registry,
        "audited_brief",
        "output/intermediate/audited_brief.md",
    )
    audit_path = workspace / _artifact_registry_path(
        registry,
        "audit_report",
        "output/intermediate/audit_report.json",
    )
    if _sha256_file(ledger_path) != ledger_sha:
        raise RuntimeStateError(
            "Claim Ledger changed before auditor completion could bind it.",
            details={
                "artifact_id": "claim_ledger",
                "path": str(ledger_path),
                "registry_sha256": ledger_sha,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    if _sha256_file(audited_brief_path) != audited_brief_sha:
        raise RuntimeStateError(
            "Audited brief changed before auditor completion could bind it.",
            details={
                "artifact_id": "audited_brief",
                "path": str(audited_brief_path),
                "registry_sha256": audited_brief_sha,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    if _sha256_file(audit_path) != audit_sha:
        raise RuntimeStateError(
            "Audit report changed before auditor completion could bind it.",
            details={
                "artifact_id": "audit_report",
                "path": str(audit_path),
                "registry_sha256": audit_sha,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    relevant_repair_transaction_ids = _repair_transaction_ids_for_artifact(
        event_records,
        artifact_path="output/intermediate/audited_brief.md",
    )
    audit_binding = {
        "schema_version": AUDIT_BINDING_SCHEMA,
        "source": "auditor_stage_complete",
        "claim_ledger_sha256": ledger_sha,
        "audited_brief_sha256": audited_brief_sha,
        "audit_report_sha256": audit_sha,
        "relevant_repair_transaction_ids": relevant_repair_transaction_ids,
        "auditor_stage_transaction_id": transaction_id,
        "stage_completion_event": {
            "event_type": "decision_recorded",
            "transaction_id": transaction_id,
            "event_id": None,
            "sequence": None,
            "availability": "not_available_until_event_append",
        },
    }
    return {
        "upstream_artifact_sha256": {
            "claim_ledger": ledger_sha,
            "audited_brief": audited_brief_sha,
        },
        "produced_artifact_sha256": {
            "audit_report": audit_sha,
        },
        "audit_binding": audit_binding,
    }


def _repair_transaction_ids_for_artifact(
    event_records: list[dict[str, Any]],
    *,
    artifact_path: str,
) -> list[str]:
    return repair_history_transaction_ids_for_artifact(
        event_records,
        artifact_id="audited_brief",
        artifact_path=artifact_path,
    )


def _stage_runtime_provenance(
    *,
    runtime: str | None,
    model: str | None,
    actor: str,
) -> dict[str, Any] | None:
    data: dict[str, Any] = {
        "schema_version": "mabw.stage_runtime_provenance.v1",
        "source": "stage_completion_args",
        "recorded_by_actor": actor,
        "provenance_only": True,
        "quality_claim": False,
    }
    if runtime is not None and str(runtime).strip():
        data["runtime"] = str(runtime).strip()
    if model is not None and str(model).strip():
        data["model"] = str(model).strip()
    return data if "runtime" in data or "model" in data else None


def _topology_satisfier_aliases(*, stage_id: str, topology: str) -> set[str]:
    aliases = {stage_id}
    if topology == "human_assisted" and stage_id in {"analyst", "editor", "writer"}:
        aliases.add("writer")
    return aliases


def _topology_satisfaction_targets_for_completion(
    *,
    stages: list[dict[str, Any]],
    policy_pack: dict[str, Any],
    stage_id: str,
) -> list[tuple[str, dict[str, Any]]]:
    try:
        topology = _role_topology_from_policy_pack(policy_pack)
        rules = _topology_satisfaction_rules(stages=stages, policy_pack=policy_pack)
    except ValueError as exc:
        raise RuntimeStateError(
            "policy.role_topology is invalid for stage satisfaction.",
            details={"reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc

    satisfiers = _topology_satisfier_aliases(stage_id=stage_id, topology=topology)
    targets: list[tuple[str, dict[str, Any]]] = []
    current = _next_stage_id(stages, stage_id)
    while current:
        rule = rules.get(current)
        if not rule:
            break
        if str(rule.get("satisfied_by") or "") not in satisfiers:
            break
        targets.append((current, rule))
        current = _next_stage_id(stages, current)
    return targets


def _topology_satisfaction_required_reasons(
    *,
    workspace: Path,
    targets: list[tuple[str, dict[str, Any]]],
    artifacts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for target_stage_id, rule in targets:
        reasons.extend(
            _topology_satisfaction_artifact_reasons(
                workspace=workspace,
                stage_id=target_stage_id,
                rule=rule,
                artifacts_by_id=artifacts_by_id,
            )
        )
    return reasons


def _topology_satisfaction_target_blocking_reasons(
    *,
    workspace: Path,
    targets: list[tuple[str, dict[str, Any]]],
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for target_stage_id, _rule in targets:
        reasons.extend(
            current_stage_feedback_blocking_reasons(
                workspace=workspace,
                current_stage=target_stage_id,
                stages=stages,
                artifacts=artifacts,
            )
        )
        reasons.extend(
            current_stage_quality_gate_blocking_reasons(
                workspace=workspace,
                current_stage=target_stage_id,
                stages=stages,
                artifacts=artifacts,
            )
        )
    return reasons


def _workflow_with_topology_satisfaction(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    targets: list[tuple[str, dict[str, Any]]],
    trigger_stage_id: str,
    now: str,
    transaction_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not targets:
        return workflow, []

    updated = dict(workflow)
    statuses = dict(updated.get("stage_statuses") or {})
    topology_events: list[dict[str, Any]] = []
    current_stage = updated.get("current_stage")

    for target_stage_id, rule in targets:
        if current_stage != target_stage_id:
            raise RuntimeStateError(
                "Topology satisfaction target does not match the current workflow stage.",
                details={
                    "target_stage_id": target_stage_id,
                    "current_stage": current_stage,
                    "trigger_stage_id": trigger_stage_id,
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
        topology = str(rule.get("topology") or "")
        satisfied_by = str(rule.get("satisfied_by") or "")
        required_artifacts = [
            str(item) for item in (rule.get("required_artifacts") or []) if item
        ]
        reason = f"Stage satisfied by {satisfied_by} under {topology} role topology."
        metadata = {
            "satisfied_by_topology": True,
            "topology": topology,
            "satisfied_by": satisfied_by,
            "satisfied_by_stage": trigger_stage_id,
            "required_artifacts": required_artifacts,
            "transaction_id": transaction_id,
        }
        statuses[target_stage_id] = _status_entry(
            STAGE_COMPLETE,
            reason,
            now,
            metadata=metadata,
        )
        topology_events.append(
            {
                "event_type": "stage_satisfied_by_topology",
                "stage_id": target_stage_id,
                "reason": reason,
                "metadata": metadata,
            }
        )
        current_stage = _next_stage_id(stages, target_stage_id)
        if current_stage:
            statuses[current_stage] = _status_entry(
                STAGE_READY,
                "",
                now,
            )

    updated["current_stage"] = current_stage
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(
        stages, current_stage
    )
    return updated, topology_events


def _append_transaction_events(
    *,
    workspace: Path,
    run_id: str,
    actor: str,
    transaction_id: str,
    stage_id: str,
    decision: str,
    reason: str,
    next_stage: str | None,
    artifact_events: list[dict[str, Any]],
    topology_events: list[dict[str, Any]] | None = None,
    runtime_provenance: dict[str, Any] | None = None,
) -> None:
    try:
        for event in [*artifact_events, *(topology_events or [])]:
            metadata = dict(event.get("metadata") or {})
            metadata["transaction_id"] = transaction_id
            append_event(
                workspace=workspace,
                run_id=run_id,
                event_type=str(event["event_type"]),
                actor=actor,
                stage_id=event.get("stage_id"),
                artifact_id=event.get("artifact_id"),
                reason=str(event.get("reason") or ""),
                metadata=metadata,
            )
        decision_metadata = {"next_stage": next_stage, "transaction_id": transaction_id}
        if runtime_provenance:
            decision_metadata["runtime_provenance"] = runtime_provenance
        append_event(
            workspace=workspace,
            run_id=run_id,
            event_type="decision_recorded",
            actor=actor,
            stage_id=stage_id,
            decision=decision,
            reason=reason,
            metadata=decision_metadata,
        )
    except RuntimeStateError as exc:
        raise RuntimeStateError(
            "Completion transaction partially wrote state but failed to append event.",
            details={
                "transaction_id": transaction_id,
                "stage_id": stage_id,
                "decision": decision,
                "event_error": str(exc),
                "event_details": exc.details,
            },
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc


def raise_if_auditable_target_complete_blocks_downstream(
    *,
    workspace: Path,
    workflow: dict[str, Any],
    command: str,
) -> None:
    condition_metadata = load_experiment_080_condition_metadata(workspace)
    if (
        not isinstance(condition_metadata, dict)
        or condition_metadata.get("assessment_target") != "auditable_brief"
    ):
        return
    paths = runtime_state_paths(workspace)
    registry = _read_json_if_exists(paths["artifact_registry"])
    auditor_gate = _read_json_if_exists(
        workspace
        / "output"
        / "intermediate"
        / "gates"
        / "auditor_quality_gate_report.json"
    )
    event_records = (
        read_event_log_records_strict(paths["event_log"])
        if paths["event_log"].exists()
        else []
    )
    projection = project_assessment_target_status(
        condition_metadata=condition_metadata,
        workflow_state=workflow,
        artifact_registry=registry,
        auditor_gate_report=auditor_gate,
        event_records=event_records,
    )
    workspace_arg = shlex.quote(str(workspace))
    target_complete = projection.get("target_complete") is True
    if target_complete:
        message = (
            "TARGET COMPLETE: auditable_brief. This 080 workspace has reached the auditable-brief "
            "assessment target; finalize/delivery actions are outside this target."
        )
        next_allowed_commands = [
            (
                "multi-agent-brief experiments 080 register-run --case <case_dir> "
                f"--condition {projection.get('condition') or '<condition>'} "
                f"--workspace {workspace_arg} --output <run_record.json>"
            ),
            (
                "multi-agent-brief experiments 080 score-run --case <case_dir> "
                "--run-record <run_record.json> --output <scorecard.json>"
            ),
        ]
    else:
        message = (
            "TARGET INCOMPLETE: auditable_brief. This 080 workspace uses the auditable-brief "
            "assessment target; finalize/delivery actions are blocked until target controls pass."
        )
        next_allowed_commands = [
            f"multi-agent-brief status --workspace {workspace_arg} --json",
            f"multi-agent-brief state check --workspace {workspace_arg} --strict --json",
            f"multi-agent-brief repair route --workspace {workspace_arg} --json",
        ]
    raise RuntimeStateError(
        message,
        details={
            "assessment_target": "auditable_brief",
            "command": command,
            "target_complete": target_complete,
            "reasons": projection.get("reasons") or [],
            "next_allowed_commands": next_allowed_commands,
            "forbidden_downstream_actions": [
                "multi-agent-brief finalize",
                "multi-agent-brief state finalize-complete",
                "multi-agent-brief deliver",
            ],
            "projection": projection,
        },
        error_code=E_ASSESSMENT_TARGET_COMPLETE,
    )


def _auditable_target_auditor_gate_pass_reasons(
    *,
    workspace: Path,
    stage_id: str,
) -> list[str]:
    if stage_id != "auditor":
        return []
    condition_metadata = load_experiment_080_condition_metadata(workspace)
    if (
        not isinstance(condition_metadata, dict)
        or condition_metadata.get("assessment_target") != "auditable_brief"
    ):
        return []
    gate_path = (
        workspace
        / "output"
        / "intermediate"
        / "gates"
        / "auditor_quality_gate_report.json"
    )
    payload = _read_json_if_exists(gate_path)
    if payload is None:
        return [
            "080 auditable_brief target requires output/intermediate/gates/auditor_quality_gate_report.json before auditor stage-complete."
        ]
    status = str(payload.get("status") or "")
    if (
        status != "pass"
        and not auditable_gate_has_only_final_abstract_advisory_warnings(payload)
    ):
        return [
            "080 auditable_brief target requires auditor quality gate report status pass before auditor stage-complete; "
            f"got {status or '<missing>'}. Repair warnings before completing auditor."
        ]
    return []


def _stale_expected_artifact_refresh_reasons(
    *,
    workspace: Path,
    workflow: dict[str, Any],
    stage: dict[str, Any],
    artifacts_by_id: dict[str, dict[str, Any]],
    old_registry: dict[str, Any],
) -> list[str]:
    if not isinstance(old_registry, dict):
        return []
    registry_artifacts = (
        old_registry.get("artifacts")
        if isinstance(old_registry.get("artifacts"), dict)
        else {}
    )
    from multi_agent_brief.orchestrator.recovery_state import (
        evaluate_recovery_state,
        recovery_stale_artifact_baselines,
    )

    recovery_state = evaluate_recovery_state(workspace=workspace)
    recovery_baselines = recovery_stale_artifact_baselines(recovery_state)
    reasons: list[str] = []
    for artifact_id in [str(item) for item in (stage.get("expected_artifacts") or [])]:
        record = registry_artifacts.get(artifact_id)
        if not isinstance(record, dict):
            continue
        if (
            record.get("status") != "stale"
            and record.get("validation_result") != "stale_after_repair"
        ):
            continue
        contract = artifacts_by_id.get(artifact_id)
        if not isinstance(contract, dict):
            continue
        rel_path = str(contract.get("path") or record.get("path") or "")
        if not rel_path:
            continue
        path = workspace / rel_path
        if not path.is_file():
            continue
        stale_sha = _stale_artifact_baseline_sha(
            stale_artifact_baselines=recovery_baselines,
            artifact_id=artifact_id,
            record=record,
        )
        current_sha = _sha256_file(path)
        if isinstance(stale_sha, str) and stale_sha == current_sha:
            stale_kind = "owner-stage revision"
            if record.get("validation_result") == "stale_after_repair":
                stale_kind = "repair"
            elif record.get("validation_result") == "stale_after_supersede":
                stale_kind = "supersede"
            reasons.append(
                f"Expected artifact '{artifact_id}' at '{rel_path}' is stale after {stale_kind} "
                "and still has the stale hash; rerun the producer stage and refresh the artifact before stage-complete."
            )
    return reasons


def _stale_artifact_baseline_sha(
    *,
    stale_artifact_baselines: dict[str, dict[str, Any]],
    artifact_id: str,
    record: dict[str, Any],
) -> str | None:
    baseline = (
        stale_artifact_baselines.get(artifact_id)
        if isinstance(stale_artifact_baselines.get(artifact_id), dict)
        else {}
    )
    baseline_sha = baseline.get("sha256")
    if isinstance(baseline_sha, str) and baseline_sha:
        return baseline_sha
    record_baseline_sha = record.get("stale_baseline_sha256")
    if isinstance(record_baseline_sha, str) and record_baseline_sha:
        return record_baseline_sha
    return None


def _complete_stage_transaction(
    *,
    workspace: str | Path,
    stage_id: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
    finalize: bool = False,
    stage_runtime: str | None = None,
    stage_model: str | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    event_records = _preflight_transaction_files(paths)
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    raise_if_active_repair_open(workspace=ws, workflow=workflow)
    if finalize:
        raise_if_auditable_target_complete_blocks_downstream(
            workspace=ws,
            workflow=workflow,
            command="state finalize-complete",
        )
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    policy_pack = load_default_policy_pack(repo)
    artifacts_by_id = _artifact_map(artifacts)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    stage = _validate_completion_target(
        stage_id=stage_id,
        workflow=workflow,
        stage_by_id=stage_by_id,
        finalize=finalize,
    )
    run_id = str(manifest["run_id"])
    decision = "finalize" if finalize else "continue"
    _raise_if_trajectory_narrows_success_path(
        workspace=ws,
        workflow=workflow,
        event_records=event_records,
        run_id=run_id,
        stage_id=stage_id,
        decision=decision,
    )
    replay_message = _older_stage_replay_message(
        stage_id=stage_id,
        current_stage=workflow.get("current_stage"),
        stages=stages,
        workflow=workflow,
    )
    if replay_message:
        workflow = _persist_run_contamination(
            workspace=ws,
            paths=paths,
            run_id=str(manifest["run_id"]),
            workflow=workflow,
            reason_code="older_stage_replay",
            message=replay_message,
            actor=actor,
            stage_id=stage_id,
        )

    transaction_id = uuid.uuid4().hex
    now = utc_now()
    runtime_provenance = _stage_runtime_provenance(
        runtime=stage_runtime,
        model=stage_model,
        actor=actor,
    )
    analyst_snapshot_before: dict[Path, bytes | None] | None = None
    if stage_id == "analyst":
        analyst_snapshot_before = _snapshot_file_paths(
            [ws / ANALYST_DRAFT_SNAPSHOT_PATH]
        )
        _snapshot_analyst_draft(ws)
    try:
        artifact_reasons = _completion_artifact_gate_reasons(
            workspace=ws,
            stage=stage,
            artifacts_by_id=artifacts_by_id,
        )
        old_registry_for_stale_check = _read_json_if_exists(paths["artifact_registry"])
        stale_artifact_reasons = _stale_expected_artifact_refresh_reasons(
            workspace=ws,
            workflow=workflow,
            stage=stage,
            artifacts_by_id=artifacts_by_id,
            old_registry=old_registry_for_stale_check,
        )
        if stale_artifact_reasons:
            _raise_completion_reasons(
                message=f"Cannot complete stage '{stage_id}' because stale downstream artifacts were not refreshed",
                reasons=stale_artifact_reasons,
                error_code=E_TRANSACTION_INTEGRITY,
                details={"stage_id": stage_id},
            )
        topology_targets = _topology_satisfaction_targets_for_completion(
            stages=stages,
            policy_pack=policy_pack,
            stage_id=stage_id,
        )
        artifact_reasons.extend(
            _topology_satisfaction_required_reasons(
                workspace=ws,
                targets=topology_targets,
                artifacts_by_id=artifacts_by_id,
            )
        )
        if stage_id == "source-discovery":
            artifact_reasons.extend(_source_discovery_evidence_reasons(ws))
        if artifact_reasons:
            code = E_REQUIRED_ARTIFACT_MISSING
            if any("invalid" in item.lower() for item in artifact_reasons):
                code = E_ARTIFACT_INVALID
            _raise_completion_reasons(
                message=f"Cannot complete stage '{stage_id}'",
                reasons=artifact_reasons,
                error_code=code,
                details={"stage_id": stage_id},
            )
        if stage_id == "claim-ledger":
            freeze_reasons = _claim_ledger_freeze_reasons(
                workspace=ws, manifest=manifest
            )
            if freeze_reasons:
                _raise_completion_reasons(
                    message="Cannot complete stage 'claim-ledger' before Claim Ledger freeze",
                    reasons=freeze_reasons,
                    error_code=E_COMPLETION_TRANSACTION_REQUIRED,
                    details={"stage_id": stage_id},
                )

        topology_target_reasons = _topology_satisfaction_target_blocking_reasons(
            workspace=ws,
            targets=topology_targets,
            stages=stages,
            artifacts=artifacts,
        )
        for target_stage_id, _rule in topology_targets:
            target_stage = stage_by_id.get(target_stage_id)
            if not isinstance(target_stage, dict):
                continue
            topology_target_reasons.extend(
                _stale_expected_artifact_refresh_reasons(
                    workspace=ws,
                    workflow=workflow,
                    stage=target_stage,
                    artifacts_by_id=artifacts_by_id,
                    old_registry=old_registry_for_stale_check,
                )
            )
        if topology_target_reasons:
            _raise_completion_reasons(
                message=f"Cannot complete stage '{stage_id}' because a topology-satisfied downstream stage is blocked",
                reasons=topology_target_reasons,
                error_code=E_ILLEGAL_TRANSITION,
                details={
                    "stage_id": stage_id,
                    "topology_target_stages": [
                        target for target, _rule in topology_targets
                    ],
                },
            )

        feedback_reasons = current_stage_feedback_blocking_reasons(
            workspace=ws,
            current_stage=stage_id,
            stages=stages,
            artifacts=artifacts,
        )
        if feedback_reasons:
            _raise_completion_reasons(
                message=f"Cannot complete stage '{stage_id}'",
                reasons=feedback_reasons,
                error_code=E_ILLEGAL_TRANSITION,
                details={"stage_id": stage_id},
            )

        quality_reasons = current_stage_quality_gate_blocking_reasons(
            workspace=ws,
            current_stage=stage_id,
            stages=stages,
            artifacts=artifacts,
        )
        if stage_id == "auditor":
            quality_reasons.extend(
                _quality_gate_pass_reasons(
                    workspace=ws, stages=stages, artifacts=artifacts
                )
            )
            quality_reasons.extend(
                _auditable_target_auditor_gate_pass_reasons(
                    workspace=ws,
                    stage_id=stage_id,
                )
            )
        if quality_reasons:
            _raise_completion_reasons(
                message=f"Cannot complete stage '{stage_id}'",
                reasons=quality_reasons,
                error_code=E_QUALITY_GATE_REQUIRED,
                details={"stage_id": stage_id},
            )

        fast_rerun_freshness_at_finalize: dict[str, Any] | None = None
        manifest_for_completion = manifest
        if finalize:
            fast_rerun_freshness_at_finalize = _fast_rerun_finalize_freshness_snapshot(
                ws,
                manifest,
                checked_at=utc_now(),
            )
            manifest_for_completion = _manifest_with_fast_rerun_freshness_at_finalize(
                manifest,
                fast_rerun_freshness_at_finalize,
            )
            finalize_reasons = _finalize_completion_reasons(
                ws,
                stages=stages,
                artifacts=artifacts,
                runtime_manifest=manifest_for_completion,
                fast_rerun_freshness_at_finalize=fast_rerun_freshness_at_finalize,
            )
            if finalize_reasons:
                _raise_completion_reasons(
                    message="Cannot complete finalize stage",
                    reasons=finalize_reasons,
                    error_code=E_READER_FINAL_GATE_FAILED,
                    details={"stage_id": stage_id},
                )

        preserved_extensions = _preserved_manifest_extensions(manifest_for_completion)
        next_workflow = _workflow_after_completion(
            workflow=workflow,
            stages=stages,
            stage_id=stage_id,
            reason=reason,
            now=now,
            transaction_id=transaction_id,
            finalize=finalize,
            runtime_provenance=runtime_provenance,
        )
        if finalize:
            next_workflow = _finalize_run_integrity(next_workflow)
        next_workflow, topology_events = _workflow_with_topology_satisfaction(
            workflow=next_workflow,
            stages=stages,
            targets=topology_targets,
            trigger_stage_id=stage_id,
            now=now,
            transaction_id=transaction_id,
        )
        next_workflow = _workflow_with_trajectory_decision_narrowing(
            workspace=ws,
            workflow=next_workflow,
            stages=stages,
            event_records=event_records,
            run_id=run_id,
        )
        old_registry = _read_json_if_exists(paths["artifact_registry"])
        registry = _build_artifact_registry(
            workspace=ws,
            run_id=run_id,
            artifacts=artifacts,
            workflow=next_workflow,
            updated_at=now,
        )
        frozen_verdict = interpret_frozen_artifact_integrity(
            old_registry=old_registry,
            registry=registry,
            workflow=workflow,
            artifacts=artifacts,
            stages=stages,
            mutating_stage=stage_id,
        )
        frozen_reasons = require_frozen_artifact_integrity_pass(frozen_verdict)
        if frozen_reasons:
            if frozen_verdict.contaminates_run:
                workflow = _persist_run_contamination(
                    workspace=ws,
                    paths=paths,
                    run_id=run_id,
                    workflow=workflow,
                    reason_code="frozen_artifact_changed",
                    message=" ".join(frozen_reasons),
                    actor=actor,
                    stage_id=stage_id,
                    metadata={"blocking_reasons": frozen_reasons},
                )
            _raise_completion_reasons(
                message=(
                    "Completion transaction cannot proceed because a frozen upstream artifact changed"
                    if frozen_verdict.contaminates_run
                    else "Completion transaction cannot proceed because frozen artifact integrity could not be verified"
                ),
                reasons=frozen_reasons,
                error_code=E_TRANSACTION_INTEGRITY,
                details={"stage_id": stage_id},
            )
        if stage_id == "auditor":
            statuses = dict(next_workflow.get("stage_statuses") or {})
            auditor_status = dict(statuses.get("auditor") or {})
            auditor_metadata = dict(auditor_status.get("metadata") or {})
            auditor_metadata.update(
                _auditor_completion_metadata(
                    workspace=ws,
                    registry=registry,
                    event_records=event_records,
                    transaction_id=transaction_id,
                )
            )
            auditor_status["metadata"] = auditor_metadata
            statuses["auditor"] = auditor_status
            next_workflow["stage_statuses"] = statuses
        finalize_report: dict[str, Any] | None = None
        if finalize:
            finalize_report = _read_json(
                paths["runtime_manifest"].parent / "finalize_report.json"
            )
            try:
                preflight_finalized_run_archive(
                    workspace=ws,
                    run_id=run_id,
                    manifest=manifest_for_completion,
                    workflow=next_workflow,
                    artifact_registry=registry,
                    finalize_report=finalize_report,
                    fast_rerun_freshness_at_finalize=fast_rerun_freshness_at_finalize,
                )
            except RunArchiveError as exc:
                raise _wrap_archive_error(exc) from exc
        artifact_events = _changed_artifact_events(
            old_registry=old_registry, registry=registry
        )
    except RuntimeStateError:
        if analyst_snapshot_before is not None:
            _restore_file_paths(
                analyst_snapshot_before,
                rollback_message="Stage completion rollback failed after Analyst snapshot write.",
            )
        raise

    state_written = False
    state_snapshots = _snapshot_state_files(
        paths, ("runtime_manifest", "artifact_registry", "workflow_state")
    )
    try:
        if manifest_for_completion != manifest:
            _write_json_atomic(paths["runtime_manifest"], manifest_for_completion)
            state_written = True
        _write_json_atomic(paths["artifact_registry"], registry)
        state_written = True
        _write_json_atomic(paths["workflow_state"], next_workflow)
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
            if analyst_snapshot_before is not None:
                _restore_file_paths(
                    analyst_snapshot_before,
                    rollback_message="Stage completion rollback failed after state write failure.",
                )
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Completion transaction partially wrote files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": stage_id,
                    "state_error": str(exc),
                    "state_details": exc.details,
                    "rollback_error": str(rollback_exc),
                    "rollback_details": rollback_exc.details,
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        code = E_TRANSACTION_PARTIAL_WRITE if state_written else exc.error_code
        raise RuntimeStateError(
            "Completion transaction failed while writing state files; control files were restored.",
            details={
                "transaction_id": transaction_id,
                "stage_id": stage_id,
                "state_error": str(exc),
                "state_details": exc.details,
                "restored": True,
            },
            error_code=code,
        ) from exc

    _append_transaction_events(
        workspace=ws,
        run_id=run_id,
        actor=actor,
        transaction_id=transaction_id,
        stage_id=stage_id,
        decision="finalize" if finalize else "continue",
        reason=reason,
        next_stage=next_workflow.get("current_stage"),
        artifact_events=artifact_events,
        topology_events=topology_events,
        runtime_provenance=runtime_provenance,
    )

    current_manifest = _read_json(paths["runtime_manifest"])
    _assert_manifest_extensions_preserved(
        before=preserved_extensions, after=current_manifest
    )
    archive_result: dict[str, Any] | None = None
    if finalize:
        archive_result = _archive_finalized_state_if_needed(
            workspace=ws,
            manifest=current_manifest,
            workflow=next_workflow,
            artifact_registry=registry,
            finalize_report=finalize_report
            or _read_json(paths["runtime_manifest"].parent / "finalize_report.json"),
            fast_rerun_freshness_at_finalize=fast_rerun_freshness_at_finalize,
        )
        try:
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type="run_archived",
                actor=actor,
                stage_id=stage_id,
                reason="Finalized run archived.",
                metadata={
                    "archive_path": _workspace_relative(
                        ws, Path(str(archive_result["archive_path"]))
                    ),
                    "archive_manifest": _workspace_relative(
                        ws, Path(str(archive_result["archive_manifest"]))
                    ),
                    "archive_manifest_sha256": archive_result[
                        "archive_manifest_sha256"
                    ],
                    "file_count": archive_result["file_count"],
                    "event_log_includes_run_archived": False,
                    "transaction_id": transaction_id,
                },
            )
        except RuntimeStateError as exc:
            raise RuntimeStateError(
                "Completion transaction archived the run but failed to append archive event.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": stage_id,
                    "archive_path": archive_result.get("archive_path"),
                    "event_error": str(exc),
                    "event_details": exc.details,
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from exc
    state = show_runtime_state(workspace=ws)
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": stage_id,
        "decision": "finalize" if finalize else "continue",
    }
    if runtime_provenance:
        state["transaction"]["runtime_provenance"] = runtime_provenance
    if archive_result is not None:
        state["run_archive"] = archive_result
    return state


def _snapshot_analyst_draft(workspace: Path) -> None:
    source = workspace / "output/intermediate/audited_brief.md"
    target = workspace / ANALYST_DRAFT_SNAPSHOT_PATH
    if not source.exists():
        raise RuntimeStateError(
            "Cannot snapshot Analyst draft because output/intermediate/audited_brief.md is missing. "
            "The Analyst role must write audited_brief.md as the working draft; "
            "state stage-complete --stage analyst is the only writer that freezes "
            "output/intermediate/analyst_draft_snapshot.md.",
            details={"path": _workspace_relative(workspace, source)},
            error_code=E_REQUIRED_ARTIFACT_MISSING,
        )
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise RuntimeStateError(
            "Cannot read Analyst draft for snapshot.",
            details={
                "path": _workspace_relative(workspace, source),
                "reason": str(exc),
            },
        ) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeStateError(
            "Cannot write Analyst draft snapshot.",
            details={
                "path": _workspace_relative(workspace, target),
                "reason": str(exc),
            },
        ) from exc


def complete_stage_transaction(
    *,
    workspace: str | Path,
    stage_id: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
    runtime: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    return _complete_stage_transaction(
        workspace=workspace,
        stage_id=stage_id,
        reason=reason,
        repo_workdir=repo_workdir,
        actor=actor,
        finalize=False,
        stage_runtime=runtime,
        stage_model=model,
    )


def complete_finalize_transaction(
    *,
    workspace: str | Path,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
    runtime: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    return _complete_stage_transaction(
        workspace=workspace,
        stage_id="finalize",
        reason=reason,
        repo_workdir=repo_workdir,
        actor=actor,
        finalize=True,
        stage_runtime=runtime,
        stage_model=model,
    )
