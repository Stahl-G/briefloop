"""Runtime state and artifact registry support for the Orchestrator."""

from __future__ import annotations

import fnmatch
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
)
from multi_agent_brief.feedback.feedback_contract import (
    current_stage_feedback_blocking_reasons,
)
from multi_agent_brief.quality_gates.contract import (
    current_stage_quality_gate_blocking_reasons,
)
from multi_agent_brief.orchestrator_contract import (
    DECISION_VOCABULARY,
    resolve_repo_workdir,
)
from multi_agent_brief.orchestrator.runtime_state._io import (
    _read_json,
    _read_json_if_exists,
    _restore_state_files,
    _sha256_file,
    _snapshot_state_files,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    _artifact_map,
    _stage_ids,
    load_default_policy_pack,
    load_artifact_contracts,
    load_stage_specs,
)
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
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ARTIFACT_INVALID,
    E_ACTIVE_REPAIR_OPEN,
    E_ASSESSMENT_TARGET_COMPLETE,
    E_CLAIM_DRAFT_CONTRACT_INVALID,
    E_COMPLETION_TRANSACTION_REQUIRED,
    E_FACT_LAYER_IMPORT_INVALID,
    E_ILLEGAL_TRANSITION,
    E_QUALITY_GATE_REQUIRED,
    E_READER_FINAL_GATE_FAILED,
    E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN,
    E_REPAIR_NO_LEGAL_ROUTE,
    E_REPAIR_TRANSACTION_REQUIRED,
    E_REQUIRED_ARTIFACT_MISSING,
    E_RUNTIME_STATE_NOT_INITIALIZED,
    E_STAGE_ALREADY_COMPLETED,
    E_STAGE_MISMATCH,
    E_TRANSACTION_INTEGRITY,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
    _wrap_archive_error,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    _artifact_registry_path,
    _artifact_registry_sha,
    _build_artifact_registry,
    _changed_artifact_events,
    interpret_frozen_artifact_integrity,
    require_frozen_artifact_integrity_pass,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    EVENT_LOG_SCHEMA,
    _read_event_log_records,
    append_event,
    read_event_log_records_strict,
    record_handoff_written,
)
from multi_agent_brief.orchestrator.runtime_state.identity import (
    new_run_id,
    utc_now,
)
from multi_agent_brief.orchestrator.runtime_state.manifest import (
    RUNTIME_MANIFEST_SCHEMA,
    _assert_manifest_extensions_preserved,
    _preserved_manifest_extensions,
)
from multi_agent_brief.orchestrator.runtime_state.paths import (
    RUNTIME_STATE_FILES,
    _require_workspace,
    _workspace_relative,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state._transactions import (  # noqa: F401
    _archive_finalized_state_if_needed,
    _load_manifest_and_workflow,
    _persist_run_contamination,
    _preflight_transaction_files,
    _restore_file_paths,
    _snapshot_file_paths,
)
from multi_agent_brief.orchestrator.runtime_state.trajectory import (  # noqa: F401
    TRAJECTORY_DECISION_NARROWING_STATUS,
    TRAJECTORY_NARROWED_DECISIONS,
    _raise_if_trajectory_narrows_repair_route,
    _raise_if_trajectory_narrows_success_path,
    _trajectory_decision_narrowing,
    _trajectory_narrowing_changed,
    _workflow_with_trajectory_decision_narrowing,
)
from multi_agent_brief.orchestrator.runtime_state.fact_layer import (  # noqa: F401
    FACT_LAYER_IMPORT_FORBIDDEN_ARTIFACT_IDS,
    FACT_LAYER_IMPORT_REQUIRED_ARTIFACT_IDS,
    FACT_LAYER_IMPORT_SCHEMA,
    FACT_LAYER_IMPORT_SINGLETON_PATHS,
    FACT_LAYER_IMPORT_SOURCE_PACK_ARTIFACT_ID,
    _archive_fact_layer_path_for,
    _copy_import_files,
    _imported_required_artifact_reasons,
    _path_text_is_unsafe,
    _read_fact_layer_import_plan,
    _reject_duplicate_fact_layer_import_targets,
    _reject_existing_fact_layer_import_leftovers,
    _reject_existing_fact_layer_import_targets,
    _reject_source_plan_fact_layer_record,
    _require_fact_layer_file_record,
    _resolve_fact_layer_archive_manifest,
    _source_archive_path,
    _target_workspace_path,
    _validate_fact_layer_import_record_scope,
    import_fact_layer_transaction,
)
from multi_agent_brief.orchestrator.runtime_state.claim_ledger_freeze import (  # noqa: F401
    CLAIM_DRAFTS_PATH,
    CLAIM_DRAFT_PROVENANCE_METADATA_FIELDS,
    CLAIM_LEDGER_FREEZE_ID_STRATEGY,
    CLAIM_LEDGER_FREEZE_SCHEMA,
    CLAIM_LEDGER_PATH,
    _canonical_claims_from_drafts,
    _claim_draft_sort_key,
    _claim_draft_source_type,
    _claim_draft_warnings,
    _claim_ledger_bytes,
    _claim_ledger_freeze_manifest,
    _claim_ledger_freeze_reasons,
    _normalize_claim_text,
    _read_claim_drafts_for_freeze,
    freeze_claim_ledger_transaction,
)
from multi_agent_brief.orchestrator.runtime_state.claim_metadata_enrichment import (  # noqa: F401
    CLAIM_LEDGER_METADATA_ENRICHMENT_SCHEMA,
    CLAIM_METADATA_ENRICHMENT_ALLOWED_FIELDS,
    CLAIM_METADATA_ENRICHMENT_FORBIDDEN_FIELDS,
    CLAIM_METADATA_REPLACEABLE_DEFAULTS,
    _claim_ledger_enrichment_authority,
    _claims_with_enriched_metadata,
    _imported_claim_ledger_record,
    _imported_source_evidence_authority,
    _normalize_source_evidence_taxonomy,
    _source_evidence_ids,
    _source_evidence_metadata_from_file,
    _source_evidence_metadata_from_markdown,
    _sync_enriched_claim_source_fields,
    _valid_imported_claim_ledger_derivation,
    _workflow_allows_claim_metadata_enrichment,
    _workflow_with_enriched_claim_ledger_hash,
    enrich_claim_metadata_transaction,
)
from multi_agent_brief.orchestrator.runtime_state.lifecycle import (  # noqa: F401
    _archive_reset_run_scoped_control_artifact,
    _recompute_stage_state,
    _remove_reset_archive_copy,
    _reset_run_scoped_control_artifact_paths,
    _restore_reset_control_artifacts,
    check_runtime_state,
    initialize_runtime_state,
    show_runtime_state,
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
    _workflow_is_finalized,
)
from multi_agent_brief.orchestrator.run_archive import (
    E_RUN_ARCHIVE_CONFLICT,
    RunArchiveError,
    preflight_finalized_run_archive,
)
from multi_agent_brief.orchestrator.run_integrity import (
    RUN_INTEGRITY_CLEAN,
    contamination_event_metadata as _run_integrity_contamination_event_metadata,
    contaminate_run_integrity_with_event_flag as _contaminate_run_integrity_with_event_flag,
    finalize_run_integrity as _finalize_run_integrity,
)

__all__ = [
    "E_FACT_LAYER_IMPORT_INVALID",
    "E_RUNTIME_STATE_NOT_INITIALIZED",
    "E_STAGE_MISMATCH",
    "RUNTIME_MANIFEST_SCHEMA",
    "RUNTIME_STATE_FILES",
    "RuntimeStateError",
    "append_event",
    "check_runtime_state",
    "complete_finalize_transaction",
    "complete_stage_transaction",
    "enrich_claim_metadata_transaction",
    "freeze_claim_ledger_transaction",
    "import_fact_layer_transaction",
    "initialize_runtime_state",
    "load_artifact_contracts",
    "load_stage_specs",
    "new_run_id",
    "read_event_log_records_strict",
    "record_decision",
    "record_handoff_written",
    "runtime_state_paths",
    "show_runtime_state",
    "utc_now",
]

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


def _contaminate_run_integrity(
    workflow: dict[str, Any],
    *,
    reason_code: str,
    message: str,
    created_at: str,
    event_type: str | None = None,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contaminated, _reason_added = _contaminate_run_integrity_with_event_flag(
        workflow,
        reason_code=reason_code,
        message=message,
        created_at=created_at,
        event_type=event_type,
        stage_id=stage_id,
        artifact_id=artifact_id,
        metadata=metadata,
    )
    return contaminated


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
    ids: list[str] = []
    for event in event_records:
        if event.get("event_type") != "repair_completed":
            continue
        metadata = (
            event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        )
        allowed = [str(item) for item in metadata.get("allowed_artifacts") or []]
        if not _artifact_allowed(artifact_path, allowed):
            continue
        transaction_id = metadata.get("transaction_id") or metadata.get(
            "repair_transaction_id"
        )
        if (
            isinstance(transaction_id, str)
            and transaction_id
            and transaction_id not in ids
        ):
            ids.append(transaction_id)
    return ids


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
            statuses[current_stage] = _status_entry(STAGE_READY, "", now)

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
    if isinstance(workflow.get("active_repair"), dict):
        raise _active_repair_blocking_error(workspace, workflow)


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
            workflow=workflow,
            stage_id=str(stage.get("stage_id") or ""),
            artifact_id=artifact_id,
            record=record,
        )
        current_sha = _sha256_file(path)
        if isinstance(stale_sha, str) and stale_sha == current_sha:
            reasons.append(
                f"Expected artifact '{artifact_id}' at '{rel_path}' is stale after repair "
                "and still has the stale hash; rerun the producer stage and refresh the artifact before stage-complete."
            )
    return reasons


def _stale_artifact_baseline_sha(
    *,
    workflow: dict[str, Any],
    stage_id: str,
    artifact_id: str,
    record: dict[str, Any],
) -> str | None:
    statuses = (
        workflow.get("stage_statuses")
        if isinstance(workflow.get("stage_statuses"), dict)
        else {}
    )
    stage_status = (
        statuses.get(stage_id) if isinstance(statuses.get(stage_id), dict) else {}
    )
    metadata = (
        stage_status.get("metadata")
        if isinstance(stage_status.get("metadata"), dict)
        else {}
    )
    baselines = (
        metadata.get("stale_artifact_baselines")
        if isinstance(metadata.get("stale_artifact_baselines"), dict)
        else {}
    )
    baseline = (
        baselines.get(artifact_id)
        if isinstance(baselines.get(artifact_id), dict)
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
    *, workspace: Path, stage_id: str, decision: str
) -> RuntimeStateError:
    try:
        from multi_agent_brief.repair.router import route_repair

        repair_route = route_repair(workspace=workspace)
    except Exception as exc:  # pragma: no cover - defensive best-effort diagnostics
        repair_route = {"ok": False, "error": str(exc)}
    return RuntimeStateError(
        (
            "Decision 'delegate_repair' requires `multi-agent-brief repair start`; "
            "`state decide` cannot authorize owner-stage artifact edits."
        ),
        details={
            "stage_id": stage_id,
            "decision": decision,
            "required_commands": [
                f"multi-agent-brief repair route --workspace {workspace}",
                f"multi-agent-brief repair start --workspace {workspace}",
                f'multi-agent-brief repair complete --workspace {workspace} --reason "<reason>"',
            ],
            "repair_steps": [
                "Run repair start to open an owner-stage repair transaction.",
                "Delegate only the reported repair_owner role.",
                "Allow edits only to repair_route.allowed_artifacts.",
                "Run repair complete after the owner edits.",
            ],
            "fallback_decisions": ["request_human_review", "block_run"],
            "repair_route": repair_route,
        },
        error_code=E_REPAIR_TRANSACTION_REQUIRED,
    )


def _repair_event_metadata(active_repair: dict[str, Any]) -> dict[str, Any]:
    return {
        "transaction_id": active_repair.get("transaction_id"),
        "repair_owner": active_repair.get("repair_owner"),
        "allowed_artifacts": list(active_repair.get("allowed_artifacts") or []),
        "blocked_direct_edits": list(active_repair.get("blocked_direct_edits") or []),
        "source": active_repair.get("source") or {},
        "must_rerun_from": active_repair.get("must_rerun_from"),
        "recommended_action": active_repair.get("recommended_action"),
        "run_integrity_effect": active_repair.get("run_integrity_effect"),
    }


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
) -> dict[str, Any]:
    """Start an explicit owner-stage repair transaction from the deterministic route."""

    ws = _require_workspace(workspace)
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
    if isinstance(workflow.get("active_repair"), dict):
        raise RuntimeStateError(
            "A repair transaction is already active.",
            details={"active_repair": workflow.get("active_repair")},
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

    from multi_agent_brief.repair.router import route_repair

    route = route_repair(workspace=ws, route_index=route_index, finding_id=finding_id)
    if not route.get("ok"):
        raise _repair_route_error(route)
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
    active_repair = {
        "schema_version": "mabw.active_repair.v1",
        "transaction_id": transaction_id,
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
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    baseline_records = (
        active_repair.get("artifact_baseline")
        if isinstance(active_repair.get("artifact_baseline"), dict)
        else {}
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
        stale_artifact_baselines = _stale_artifact_baselines_for_stage(
            stage=stage_by_id.get(stage_id) or {},
            baseline_records=baseline_records,
        )
        if stage_id == rerun_stage:
            statuses[stage_id] = _status_entry(
                STAGE_READY,
                "Ready after owner-stage repair completion.",
                now,
                metadata={
                    "stale_after_repair": True,
                    "repair_transaction_id": transaction_id,
                    "repair_owner": owner,
                    "stale_artifact_baselines": stale_artifact_baselines,
                },
            )
        else:
            statuses[stage_id] = _status_entry(
                STAGE_PENDING,
                "Pending rerun after owner-stage repair completion.",
                now,
                metadata={
                    "stale_after_repair": True,
                    "repair_transaction_id": transaction_id,
                    "repair_owner": owner,
                    "stale_artifact_baselines": stale_artifact_baselines,
                },
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


def record_decision(
    *,
    workspace: str | Path,
    stage_id: str,
    decision: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    if not paths["runtime_manifest"].exists() or not paths["workflow_state"].exists():
        initialize_runtime_state(workspace=ws, repo_workdir=repo_workdir, actor=actor)

    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    workflow_before_decision = dict(workflow)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    if stage_id not in stage_by_id:
        raise RuntimeStateError(
            f"Unknown stage: {stage_id}",
            details={"stage_id": stage_id, "known_stages": list(stage_by_id)},
        )
    if decision not in DECISION_VOCABULARY:
        raise RuntimeStateError(
            f"Unknown Orchestrator decision: {decision}",
            details={
                "decision": decision,
                "allowed_decisions": list(DECISION_VOCABULARY),
            },
        )
    stage_allowed = [
        str(item) for item in (stage_by_id[stage_id].get("allowed_decisions") or [])
    ]
    if decision not in stage_allowed:
        raise RuntimeStateError(
            f"Decision '{decision}' is not allowed for stage '{stage_id}'.",
            details={
                "stage_id": stage_id,
                "decision": decision,
                "stage_allowed_decisions": stage_allowed,
            },
        )
    current_stage_before = workflow.get("current_stage")
    if current_stage_before is None:
        raise RuntimeStateError(
            "Cannot record a decision because the workflow has no current stage.",
            details={"stage_id": stage_id, "decision": decision},
        )
    if stage_id != current_stage_before:
        raise RuntimeStateError(
            f"Decision stage '{stage_id}' does not match current stage '{current_stage_before}'.",
            details={
                "stage_id": stage_id,
                "current_stage": current_stage_before,
                "decision": decision,
            },
        )

    run_id = str(manifest["run_id"])
    event_records = _read_event_log_records(paths["event_log"])
    existing_narrowing = _trajectory_decision_narrowing(
        workspace=ws,
        workflow=workflow,
        event_records=event_records,
        run_id=run_id,
    )
    if existing_narrowing and decision not in TRAJECTORY_NARROWED_DECISIONS:
        raise RuntimeStateError(
            f"Decision '{decision}' is blocked because trajectory regulation narrowed current-stage decisions.",
            details={
                "stage_id": stage_id,
                "decision": decision,
                "allowed_decisions": list(TRAJECTORY_NARROWED_DECISIONS),
                "trajectory_regulation": existing_narrowing,
            },
            error_code=E_ILLEGAL_TRANSITION,
        )

    if decision == "delegate_repair":
        raise _delegate_repair_transaction_required_error(
            workspace=ws,
            stage_id=stage_id,
            decision=decision,
        )

    if decision in {"continue", "finalize"}:
        command = "finalize-complete" if decision == "finalize" else "stage-complete"
        raise RuntimeStateError(
            (
                f"Decision '{decision}' must be recorded with `multi-agent-brief state {command}`. "
                "`state decide` is reserved for retry_stage, delegate_repair, request_human_review, and block_run."
            ),
            details={
                "stage_id": stage_id,
                "decision": decision,
                "required_command": command,
            },
            error_code=E_COMPLETION_TRANSACTION_REQUIRED,
        )

    now = utc_now()
    statuses = dict(workflow.get("stage_statuses") or {})
    blocked = False
    blocking_reason = ""
    current_stage: str | None = stage_id

    if decision in {"continue", "finalize"}:
        statuses[stage_id] = _status_entry(STAGE_COMPLETE, reason, now)
        next_stage = _next_stage_id(stages, stage_id)
        if next_stage and decision != "finalize":
            statuses[next_stage] = _status_entry(STAGE_READY, "", now)
            current_stage = next_stage
        else:
            current_stage = None
    elif decision == "retry_stage":
        statuses[stage_id] = _status_entry(STAGE_READY, reason, now)
    elif decision in {"request_human_review", "block_run"}:
        statuses[stage_id] = _status_entry(STAGE_BLOCKED, reason, now)
        blocked = True
        blocking_reason = reason

    workflow["updated_at"] = now
    workflow["current_stage"] = current_stage
    workflow["blocked"] = blocked
    workflow["blocking_reason"] = blocking_reason
    workflow["stage_statuses"] = statuses
    workflow["last_decision"] = {
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    decision_metadata = {"next_stage": current_stage}
    post_decision_events = [
        *event_records,
        {
            "run_id": run_id,
            "event_type": "decision_recorded",
            "stage_id": stage_id,
            "decision": decision,
            "reason": reason,
            "metadata": decision_metadata,
        },
    ]
    workflow = _workflow_with_trajectory_decision_narrowing(
        workspace=ws,
        workflow=workflow,
        stages=stages,
        event_records=post_decision_events,
        run_id=run_id,
    )
    trajectory_narrowing_changed = _trajectory_narrowing_changed(
        workflow_before_decision,
        workflow,
    )

    append_event(
        workspace=ws,
        run_id=run_id,
        event_type="decision_recorded",
        actor=actor,
        stage_id=stage_id,
        decision=decision,
        reason=reason,
        metadata=decision_metadata,
    )
    narrowing = workflow.get("trajectory_regulation")
    if (
        trajectory_narrowing_changed
        and isinstance(narrowing, dict)
        and narrowing.get("status") == TRAJECTORY_DECISION_NARROWING_STATUS
    ):
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="trajectory_decision_narrowed",
            actor=actor,
            stage_id=str(narrowing.get("stage_id") or stage_id),
            reason=", ".join(str(item) for item in narrowing.get("reasons") or []),
            metadata={
                "allowed_decisions": list(narrowing.get("allowed_decisions") or []),
                "recommended_actions": list(narrowing.get("recommended_actions") or []),
            },
        )
    _write_json_atomic(paths["workflow_state"], workflow)
    return show_runtime_state(workspace=ws)
