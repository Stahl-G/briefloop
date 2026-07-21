"""Canonical read-only state machine for contaminated-run recovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence, Union

from multi_agent_brief.orchestrator.run_integrity import (
    RUN_INTEGRITY_CLEAN,
    RUN_INTEGRITY_CONTAMINATED_REPAIRED,
    interpret_run_integrity,
    project_for_read,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_REGISTRY_SCHEMA,
)
from multi_agent_brief.contracts.runtime_contracts import (
    _stage_ids,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.control_context import (
    _WorkspaceControlReadSession,
    _open_workspace_control_read_session,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    parse_event_log_records_strict,
)
from multi_agent_brief.orchestrator.runtime_state.manifest import (
    RUNTIME_MANIFEST_SCHEMA,
)
from multi_agent_brief.orchestrator.runtime_state.paths import RUNTIME_STATE_FILES
from multi_agent_brief.orchestrator.runtime_state.workflow import WORKFLOW_STATE_SCHEMA
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


RECOVERY_STATE_SCHEMA = "briefloop.recovery_state.v1"
OWNER_REVISION_SCHEMA = "briefloop.owner_revision.v1"

RECOVERY_NOT_APPLICABLE = "not_applicable"
RECOVERY_AWAITING = "awaiting_recovery"
RECOVERY_IN_PROGRESS = "repair_in_progress"
RECOVERY_RERUN_PENDING = "downstream_rerun_pending"
RECOVERY_FINALIZE_RENDER_REQUIRED = "finalize_render_required"
RECOVERY_FINALIZE_COMPLETION_PENDING = "finalize_completion_pending"
RECOVERY_COMPLETED_NON_REFERENCE = "completed_non_reference"
RECOVERY_INVALID = "invalid_recovery_state"

RECOVERY_STATUSES = {
    RECOVERY_NOT_APPLICABLE,
    RECOVERY_AWAITING,
    RECOVERY_IN_PROGRESS,
    RECOVERY_RERUN_PENDING,
    RECOVERY_FINALIZE_RENDER_REQUIRED,
    RECOVERY_FINALIZE_COMPLETION_PENDING,
    RECOVERY_COMPLETED_NON_REFERENCE,
    RECOVERY_INVALID,
}

ACTION_NONE = "none"
ACTION_REQUEST_DECISION = "request_recovery_decision"
ACTION_COMPLETE_ACTIVE_REPAIR = "complete_active_repair"
ACTION_RERUN_FROM_STAGE = "rerun_from_stage"
ACTION_RUN_FINALIZE = "run_finalize"
ACTION_RUN_FINALIZE_COMPLETE = "run_finalize_gate_or_finalize_complete"
ACTION_INSPECT_INVALID = "inspect_invalid_recovery"
ACTION_START_NEW_RUN = "start_new_run"
ACTION_INSPECT_DELIVERY = "inspect_delivery_truth"


RECOVERY_CONTROL_INPUT_FILES = (
    ("runtime_manifest", RUNTIME_STATE_FILES["runtime_manifest"]),
    ("workflow_state", RUNTIME_STATE_FILES["workflow_state"]),
    ("artifact_registry", RUNTIME_STATE_FILES["artifact_registry"]),
    ("event_log", RUNTIME_STATE_FILES["event_log"]),
    ("finalize_report", "output/intermediate/finalize_report.json"),
)

_REQUIRED_RECOVERY_CONTROL_INPUTS = (
    "runtime_manifest",
    "workflow_state",
    "event_log",
)
_MISSING_WINDOWS_PATH_ERRORS = {2, 3}


@dataclass(frozen=True)
class RecoveryControlPaths:
    """Canonical lexical bindings for every recovery control input."""

    workspace: Path
    runtime_manifest: Path
    workflow_state: Path
    artifact_registry: Path
    event_log: Path
    finalize_report: Path


@dataclass(frozen=True)
class RecoveryContext:
    run_id: str
    runtime_manifest: Mapping[str, Any]
    workflow: Mapping[str, Any]
    event_records: Sequence[Mapping[str, Any]]
    stage_ids: Sequence[str]
    artifact_registry: Mapping[str, Any] | None
    finalize_report: Mapping[str, Any] | None


@dataclass(frozen=True)
class RecoveryContextNotMaterialized:
    """A safely absent five-input inventory carrying no control values."""

    kind: Literal["not_materialized"] = "not_materialized"
    reason_code: Literal["recovery_context_not_materialized"] = (
        "recovery_context_not_materialized"
    )


RecoveryContextLoadVerdict = Union[
    RecoveryContext,
    RecoveryContextNotMaterialized,
]


@dataclass(frozen=True)
class _OwnerRevisionRecord:
    status: str
    schema_version: str
    event_id: str
    event_type: str
    event_index: int
    transaction_id: str
    repair_start_transaction_id: str
    repair_started_event_id: str
    contamination_event_id: str
    owner_stage: str
    artifact_id: str
    rerun_start_stage: str
    stale_artifact_baselines: Mapping[str, Any]


def evaluate_recovery_state(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    """Load current control records and return the canonical recovery state."""

    ws = _recovery_workspace_path(workspace)
    try:
        control_paths = resolve_recovery_control_paths(ws)
        context = load_recovery_context(
            workspace=ws,
            repo_workdir=repo_workdir,
            control_paths=control_paths,
        )
        return interpret_recovery_state(context)
    except RuntimeStateError as exc:
        return _state(
            status=RECOVERY_INVALID,
            reason_code="control_context_invalid",
            reason=str(exc),
            details={"error_code": exc.error_code, **exc.details},
        )


def interpret_recovery_state(context: RecoveryContext) -> dict[str, Any]:
    """Interpret validated records without reading or writing the workspace."""

    control_reason_code, control_error = _control_record_run_id_error(context)
    if control_error:
        return _invalid(context, control_reason_code, control_error)

    event_error = _event_identity_error(context.event_records)
    if event_error:
        return _invalid(context, "event_identity_invalid", event_error)

    integrity = project_for_read(
        interpret_run_integrity(
            context.workflow.get("run_integrity"),
            field_present="run_integrity" in context.workflow,
        )
    )
    if integrity.get("status") == "unknown":
        return _invalid(context, "run_integrity_invalid", "run_integrity is invalid.")

    current_events = [event for event in context.event_records if _text(event.get("run_id")) == context.run_id]
    owner_revisions, owner_revision_error = _normalize_owner_revisions(
        current_events,
        run_id=context.run_id,
        stage_ids=context.stage_ids,
        workflow=context.workflow,
    )
    if owner_revision_error:
        return _invalid(context, "owner_revision_binding_invalid", owner_revision_error)
    latest_owner_revision = _owner_revision_projection(
        owner_revisions[-1] if owner_revisions else None
    )
    contaminations = [
        event for event in current_events if event.get("event_type") == "run_integrity_contaminated"
    ]
    if not contaminations:
        if integrity.get("status") == RUN_INTEGRITY_CLEAN:
            return _state(
                status=RECOVERY_NOT_APPLICABLE,
                reason_code="no_current_contamination",
                reason="No current-run contamination event exists.",
                run_id=context.run_id,
                current_stage=_text(context.workflow.get("current_stage")),
                reference_eligible=True,
                owner_revision=latest_owner_revision,
            )
        reason_code = (
            "legacy_recovery_unbound"
            if integrity.get("status") == RUN_INTEGRITY_CONTAMINATED_REPAIRED
            else "contamination_event_missing"
        )
        return _invalid(
            context,
            reason_code,
            "Non-clean run_integrity has no current-run contamination event.",
        )

    if context.artifact_registry is None:
        return _invalid(
            context,
            "artifact_registry_missing_for_recovery",
            "artifact_registry.json is required for contaminated recovery.",
        )

    latest_contamination = contaminations[-1]
    contamination_event_id = _text(latest_contamination.get("event_id"))
    contamination_index = current_events.index(latest_contamination)
    recovery_revisions = [
        revision
        for revision in owner_revisions
        if revision.event_index > contamination_index
    ]
    owner_revision = _owner_revision_projection(
        recovery_revisions[-1] if recovery_revisions else None
    )
    for revision in recovery_revisions:
        if revision.contamination_event_id != contamination_event_id:
            return _invalid(
                context,
                "recovery_event_binding_invalid",
                "Recovery event is not bound to the latest contamination event.",
            )
    active_repair = context.workflow.get("active_repair")
    if isinstance(active_repair, Mapping):
        active_error = _active_repair_binding_error(
            active_repair=active_repair,
            event_records=context.event_records,
            run_id=context.run_id,
            contamination_event_id=contamination_event_id,
        )
        if active_error:
            return _invalid(context, "active_repair_binding_invalid", active_error)
        return _state(
            status=RECOVERY_IN_PROGRESS,
            reason_code="active_repair_bound",
            reason="A current-run repair transaction is active.",
            run_id=context.run_id,
            contamination_event_id=contamination_event_id,
            repair_start_transaction_id=_text(active_repair.get("repair_start_transaction_id")),
            owner_stage=_text(active_repair.get("repair_owner")),
            artifact_id=_active_repair_artifact_id(active_repair),
            rerun_start_stage=_text(active_repair.get("must_rerun_from")),
            current_stage=_text(context.workflow.get("current_stage")),
            recommended_recovery_action=ACTION_COMPLETE_ACTIVE_REPAIR,
            owner_revision=owner_revision,
        )

    if not recovery_revisions:
        finalized = context.workflow.get("current_stage") is None
        return _state(
            status=RECOVERY_AWAITING,
            reason_code=(
                "finalized_run_contaminated_new_run_required"
                if finalized
                else "contamination_unrecovered"
            ),
            reason=(
                "A finalized run was contaminated; start a new run."
                if finalized
                else "Current-run contamination has no bound recovery transaction."
            ),
            run_id=context.run_id,
            contamination_event_id=contamination_event_id,
            current_stage=_text(context.workflow.get("current_stage")),
            recommended_recovery_action=(
                ACTION_START_NEW_RUN if finalized else ACTION_REQUEST_DECISION
            ),
            owner_revision=owner_revision,
        )

    recovery_revision = recovery_revisions[-1]
    pointer_error = _repair_pointer_error(
        context.workflow.get("last_repair_transaction"),
        revision=recovery_revision,
        run_id=context.run_id,
    )
    if pointer_error:
        return _invalid(context, "repair_pointer_invalid", pointer_error)

    rerun_start_stage = recovery_revision.rerun_start_stage
    current_stage = _text(context.workflow.get("current_stage"))
    if current_stage:
        if current_stage not in context.stage_ids:
            return _invalid(context, "current_stage_invalid", "Current stage is not canonical.")
        if context.stage_ids.index(current_stage) < context.stage_ids.index(rerun_start_stage):
            return _invalid(
                context,
                "current_stage_precedes_recovery_rerun",
                "Current stage precedes the bound recovery rerun start stage.",
            )
        if current_stage != "finalize":
            return _bound_recovery_state(
                context=context,
                revision=recovery_revision,
                contamination_event_id=contamination_event_id,
                status=RECOVERY_RERUN_PENDING,
                reason_code="downstream_rerun_required",
                reason="Downstream stages must rerun from the recorded stage.",
                action=ACTION_RERUN_FROM_STAGE,
            )

    report_state = _finalize_report_state(
        report=context.finalize_report,
        run_id=context.run_id,
        contamination_event_id=contamination_event_id,
        recovery_transaction_id=recovery_revision.transaction_id,
        rerun_start_stage=rerun_start_stage,
    )
    if current_stage == "finalize":
        if report_state[0] != "current_pass":
            return _bound_recovery_state(
                context=context,
                revision=recovery_revision,
                contamination_event_id=contamination_event_id,
                status=RECOVERY_FINALIZE_RENDER_REQUIRED,
                reason_code=report_state[1],
                reason=report_state[2],
                action=ACTION_RUN_FINALIZE,
            )
        return _bound_recovery_state(
            context=context,
            revision=recovery_revision,
            contamination_event_id=contamination_event_id,
            status=RECOVERY_FINALIZE_COMPLETION_PENDING,
            reason_code="finalize_completion_required",
            reason="Current recovery-bound finalize output must pass gate and finalize-complete.",
            action=ACTION_RUN_FINALIZE_COMPLETE,
            render_transaction_id=_text((context.finalize_report or {}).get("finalize_transaction_id")),
        )

    if current_stage:
        return _invalid(context, "terminal_stage_invalid", "Unexpected terminal recovery stage.")
    if report_state[0] != "current_pass":
        return _invalid(context, "terminal_finalize_report_invalid", report_state[2])
    completion = context.workflow.get("last_completion_transaction")
    completion_error = _finalize_completion_binding_error(
        completion,
        event_records=context.event_records,
        recovery_event_id=recovery_revision.event_id,
        run_id=context.run_id,
        contamination_event_id=contamination_event_id,
        recovery_transaction_id=recovery_revision.transaction_id,
        render_transaction_id=_text((context.finalize_report or {}).get("finalize_transaction_id")),
    )
    if completion_error:
        return _invalid(context, "finalize_completion_binding_invalid", completion_error)
    return _bound_recovery_state(
        context=context,
        revision=recovery_revision,
        contamination_event_id=contamination_event_id,
        status=RECOVERY_COMPLETED_NON_REFERENCE,
        reason_code="recovery_completed_non_reference",
        reason="Recovery reached a bound terminal finalize without restoring reference eligibility.",
        action=ACTION_INSPECT_DELIVERY,
        render_transaction_id=_text((context.finalize_report or {}).get("finalize_transaction_id")),
        finalize_completion_transaction_id=_text(completion.get("transaction_id")) if isinstance(completion, Mapping) else "",
    )


def recovery_stale_artifact_baselines(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return current event-derived stale baselines from a recovery projection."""

    owner_revision = state.get("owner_revision")
    values = (
        owner_revision.get("stale_artifact_baselines")
        if isinstance(owner_revision, Mapping)
        else state.get("stale_artifact_baselines")
    )
    if not isinstance(values, Mapping):
        return {}
    return {
        str(artifact_id): dict(record)
        for artifact_id, record in values.items()
        if isinstance(record, Mapping)
    }


def finalize_recovery_binding(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return lineage for a finalize render in the current recovery cycle."""

    if state.get("status") != RECOVERY_FINALIZE_RENDER_REQUIRED:
        return {}
    return {
        "status": "bound_non_reference_recovery",
        "run_id": _text(state.get("run_id")),
        "contamination_event_id": _text(state.get("contamination_event_id")),
        "recovery_transaction_id": _text(state.get("recovery_transaction_id")),
        "rerun_start_stage": _text(state.get("rerun_start_stage")),
        "reference_eligible": False,
    }


def resolve_recovery_control_paths(
    workspace: str | Path,
) -> RecoveryControlPaths:
    """Return one canonical lexical inventory token for recovery reads."""

    ws = _recovery_workspace_path(workspace)
    paths = _expected_recovery_control_paths(ws)
    return _validate_recovery_control_paths(workspace=ws, control_paths=paths)


def load_recovery_context(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None,
    control_paths: RecoveryControlPaths | None = None,
) -> RecoveryContext:
    """Load a materialized Recovery context through the typed read boundary."""

    verdict = load_recovery_context_verdict(
        workspace=workspace,
        repo_workdir=repo_workdir,
        control_paths=control_paths,
    )
    if isinstance(verdict, RecoveryContextNotMaterialized):
        raise RuntimeStateError(
            "Recovery control context is not materialized.",
            details={"reason_code": verdict.reason_code},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return verdict


def load_recovery_context_verdict(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None,
    control_paths: RecoveryControlPaths | None = None,
) -> RecoveryContextLoadVerdict:
    """Return one typed verdict from the canonical five-input inventory."""

    ws = _recovery_workspace_path(workspace)
    # Stage contracts are a separate semantic input, not a sixth workspace
    # control file. Bind and interpret their authority before acquiring the
    # workspace session, without using the mutable workspace pathname as a
    # repository-discovery fallback. The resulting immutable IDs cannot be
    # replaced after the five-file session has been acquired.
    repo = resolve_repo_workdir(repo_workdir)
    stage_ids = tuple(_stage_ids(load_stage_specs(repo)))
    paths = (
        resolve_recovery_control_paths(ws)
        if control_paths is None
        else control_paths
    )
    # A caller-supplied path set is only a lexical token. Recovery acquires its
    # own workspace-root capability before validating or preflighting the
    # inventory, then keeps that same capability for every load. Callers can
    # neither inject a session nor cause a later absolute-path reopen.
    with _open_workspace_control_read_session(ws) as session:
        canonical_paths = _validate_recovery_control_paths(
            workspace=ws,
            control_paths=paths,
        )
        path_map = _recovery_control_path_mapping(canonical_paths)
        relative_path_map = _recovery_control_relative_path_mapping(canonical_paths)
        presence = _preflight_recovery_control_inputs(
            session=session,
            control_paths=canonical_paths,
            relative_path_map=relative_path_map,
        )
        if not any(presence.values()):
            return RecoveryContextNotMaterialized()
        for key in _REQUIRED_RECOVERY_CONTROL_INPUTS:
            if not presence[key]:
                raise _required_recovery_control_missing_error(
                    control_input=key,
                    path=path_map[key],
                )
        manifest = session.load_object(
            relative_path_map["runtime_manifest"],
            expected_schema=RUNTIME_MANIFEST_SCHEMA,
        )
        workflow = session.load_object(
            relative_path_map["workflow_state"],
            expected_schema=WORKFLOW_STATE_SCHEMA,
        )
        registry = session.load_object(
            relative_path_map["artifact_registry"],
            expected_schema=ARTIFACT_REGISTRY_SCHEMA,
            required=False,
        )
        report = session.load_object(
            relative_path_map["finalize_report"],
            required=False,
        )
        event_raw = session.read_bytes(relative_path_map["event_log"])
    if event_raw is None:
        raise RuntimeStateError(
            f"Required control file is missing: {path_map['event_log']}",
            details={
                "path": str(path_map["event_log"]),
                "reason_code": "control_file_missing",
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    event_records = parse_event_log_records_strict(
        event_raw,
        path=path_map["event_log"],
    )
    run_id = _text((manifest or {}).get("run_id"))
    if not run_id:
        raise RuntimeStateError("runtime_manifest.json run_id is required.")
    return RecoveryContext(
        run_id=run_id,
        runtime_manifest=manifest or {},
        workflow=workflow or {},
        event_records=event_records,
        stage_ids=stage_ids,
        artifact_registry=registry,
        finalize_report=report,
    )


def _expected_recovery_control_paths(workspace: Path) -> RecoveryControlPaths:
    values = {
        key: workspace / relative_path
        for key, relative_path in RECOVERY_CONTROL_INPUT_FILES
    }
    return RecoveryControlPaths(workspace=workspace, **values)


def _recovery_workspace_path(workspace: str | Path) -> Path:
    """Bind a caller workspace name to one physical recovery identity."""

    return Path(workspace).expanduser().resolve()


def _validate_recovery_control_paths(
    *,
    workspace: Path,
    control_paths: RecoveryControlPaths,
) -> RecoveryControlPaths:
    # The supplied binding is data, not a polymorphic loader capability. An
    # exact-type check prevents subclasses from overriding attribute/mapping
    # behavior after their inherited dataclass fields have been validated.
    if type(control_paths) is not RecoveryControlPaths:
        raise _recovery_control_path_error(
            reason_code="recovery_control_paths_invalid",
            control_input="",
            path=None,
        )
    expected = _expected_recovery_control_paths(workspace)
    supplied_workspace = control_paths.workspace
    if (
        type(supplied_workspace) is not type(expected.workspace)
        or supplied_workspace != expected.workspace
    ):
        raise _recovery_control_path_error(
            reason_code="recovery_control_workspace_mismatch",
            control_input="",
            path=(
                supplied_workspace
                if type(supplied_workspace) is type(expected.workspace)
                else None
            ),
        )
    for key, _relative_path in RECOVERY_CONTROL_INPUT_FILES:
        path = getattr(control_paths, key, None)
        expected_path = getattr(expected, key)
        # Check concrete type before invoking equality or any path method. A
        # Path subclass can otherwise impersonate the canonical identity while
        # retaining foreign internal path state for the eventual I/O call.
        if type(path) is not type(expected_path) or path != expected_path:
            raise _recovery_control_path_error(
                reason_code="recovery_control_path_binding_invalid",
                control_input=key,
                path=path if type(path) is type(expected_path) else None,
            )
    return expected


def _recovery_control_path_mapping(
    control_paths: RecoveryControlPaths,
) -> Mapping[str, Path]:
    """Derive all read paths from the exact validated dataclass fields."""

    return MappingProxyType(
        {
            key: getattr(control_paths, key)
            for key, _relative_path in RECOVERY_CONTROL_INPUT_FILES
        }
    )


def _recovery_control_relative_path_mapping(
    control_paths: RecoveryControlPaths,
) -> Mapping[str, str]:
    """Derive descriptor-bound read selectors from the validated path binding."""

    return MappingProxyType(
        {
            key: getattr(control_paths, key)
            .relative_to(control_paths.workspace)
            .as_posix()
            for key, _relative_path in RECOVERY_CONTROL_INPUT_FILES
        }
    )


def _preflight_recovery_control_inputs(
    *,
    session: _WorkspaceControlReadSession,
    control_paths: RecoveryControlPaths,
    relative_path_map: Mapping[str, str],
) -> Mapping[str, bool]:
    """Preflight the complete inventory through the retained workspace root."""

    presence: dict[str, bool] = {}
    for key, _relative_path in RECOVERY_CONTROL_INPUT_FILES:
        try:
            presence[key] = session.preflight(
                relative_path_map[key],
                required=False,
            )
        except RuntimeStateError as exc:
            if _control_input_is_safely_absent(exc):
                presence[key] = False
                continue
            if exc.details.get("reason_code") != "control_file_path_unsafe":
                raise
            raise _recovery_control_path_error(
                reason_code="recovery_control_path_unsafe",
                control_input=key,
                path=getattr(control_paths, key),
            ) from exc
    return MappingProxyType(presence)


def _control_input_is_safely_absent(exc: RuntimeStateError) -> bool:
    """Distinguish a missing ancestor from an unsafe path-chain failure."""

    if exc.details.get("reason_code") != "control_file_ancestor_unsafe":
        return False
    cause = exc.__cause__
    return isinstance(cause, FileNotFoundError) or getattr(
        cause,
        "winerror",
        None,
    ) in _MISSING_WINDOWS_PATH_ERRORS


def _required_recovery_control_missing_error(
    *,
    control_input: str,
    path: Path,
) -> RuntimeStateError:
    return RuntimeStateError(
        f"Required control file is missing: {path}",
        details={
            "path": str(path),
            "reason_code": "control_file_missing",
            "control_input": control_input,
        },
        error_code=E_TRANSACTION_INTEGRITY,
    )


def _recovery_control_path_error(
    *,
    reason_code: str,
    control_input: str,
    path: Path | None,
) -> RuntimeStateError:
    details: dict[str, Any] = {"reason_code": reason_code}
    if control_input:
        details["control_input"] = control_input
    if path is not None:
        details["path"] = str(path)
    return RuntimeStateError(
        "Recovery control path binding is invalid.",
        details=details,
        error_code=E_TRANSACTION_INTEGRITY,
    )


def _event_identity_error(records: Sequence[Mapping[str, Any]]) -> str:
    seen: set[str] = set()
    for event in records:
        event_id = _text(event.get("event_id"))
        if not event_id:
            return "event_log contains an event without event_id."
        if event_id in seen:
            return f"event_log contains duplicate event_id: {event_id}."
        seen.add(event_id)
    return ""


def _control_record_run_id_error(context: RecoveryContext) -> tuple[str, str]:
    records = (
        ("workflow_state", context.workflow, "workflow_run_id_mismatch"),
        (
            "artifact_registry",
            context.artifact_registry,
            "artifact_registry_run_id_mismatch",
        ),
    )
    for label, payload, reason_code in records:
        if payload is None:
            continue
        if _text(payload.get("run_id")) != context.run_id:
            return (
                reason_code,
                f"{label}.run_id does not match runtime_manifest.run_id.",
            )
    return "", ""


def _active_repair_binding_error(
    *,
    active_repair: Mapping[str, Any],
    event_records: Sequence[Mapping[str, Any]],
    run_id: str,
    contamination_event_id: str,
) -> str:
    required = {
        "run_id": run_id,
        "contamination_event_id": contamination_event_id,
    }
    for key, expected in required.items():
        if _text(active_repair.get(key)) != expected:
            return f"active_repair.{key} is not bound to the current recovery cycle."
    owner_stage = _text(active_repair.get("repair_owner"))
    if not owner_stage:
        return "active_repair repair_owner is required."
    return _repair_start_lineage_error(
        event_records=event_records,
        run_id=run_id,
        contamination_event_id=contamination_event_id,
        owner_stage=owner_stage,
        repair_start_transaction_id=_text(
            active_repair.get("repair_start_transaction_id")
        ),
        repair_started_event_id=_text(active_repair.get("repair_started_event_id")),
    )


def _repair_start_lineage_error(
    *,
    event_records: Sequence[Mapping[str, Any]],
    run_id: str,
    contamination_event_id: str,
    owner_stage: str,
    repair_start_transaction_id: str,
    repair_started_event_id: str,
    completion_event_id: str = "",
) -> str:
    if not repair_start_transaction_id or not repair_started_event_id:
        return "Repair start transaction/event identity is required."
    started_index, started_event = next(
        (
            (index, item)
            for index, item in enumerate(event_records)
            if _text(item.get("event_id")) == repair_started_event_id
        ),
        (-1, None),
    )
    if (
        started_event is None
        or started_event.get("event_type") != "repair_started"
        or _text(started_event.get("run_id")) != run_id
    ):
        return "Bound repair_started event is missing or belongs to another run."
    started_metadata = _metadata(started_event)
    if _text(started_metadata.get("transaction_id")) != repair_start_transaction_id:
        return "Repair start transaction does not match repair_started event."
    if _text(started_metadata.get("contamination_event_id")) != contamination_event_id:
        return "repair_started event is not bound to the current contamination event."
    if (
        _text(started_event.get("stage_id")) != owner_stage
        or _text(started_metadata.get("repair_owner")) != owner_stage
    ):
        return "repair_started event is not bound to the recovery owner stage."
    if completion_event_id:
        completion_index = next(
            (
                index
                for index, item in enumerate(event_records)
                if _text(item.get("event_id")) == completion_event_id
            ),
            -1,
        )
        if completion_index < 0 or started_index >= completion_index:
            return "repair_started event must precede repair_completed."
    return ""


def _owner_revision_binding_error(
    event: Mapping[str, Any],
    *,
    event_records: Sequence[Mapping[str, Any]],
    run_id: str,
    stage_ids: Sequence[str],
) -> str:
    metadata = _metadata(event)
    if _text(metadata.get("owner_revision_schema_version")) != OWNER_REVISION_SCHEMA:
        return "Owner revision schema_version is required and must be current."
    if _text(event.get("run_id")) != run_id:
        return "Owner revision event belongs to another run."
    transaction_id = _text(metadata.get("transaction_id"))
    if not transaction_id:
        return "Owner revision transaction_id is required."
    owner_stage = _text(metadata.get("owner_stage"))
    rerun_stage = _text(metadata.get("rerun_start_stage"))
    if owner_stage not in stage_ids:
        return "Owner revision owner_stage is not canonical."
    if rerun_stage not in stage_ids or stage_ids.index(rerun_stage) <= stage_ids.index(
        owner_stage
    ):
        return "Owner revision rerun_start_stage is not canonical."
    if not isinstance(metadata.get("stale_artifact_baselines"), Mapping):
        return "Owner revision stale_artifact_baselines must be an object."
    event_type = _text(event.get("event_type"))
    if event_type == "repair_completed":
        return _repair_start_lineage_error(
            event_records=event_records,
            run_id=run_id,
            contamination_event_id=_text(metadata.get("contamination_event_id")),
            owner_stage=owner_stage,
            repair_start_transaction_id=_text(
                metadata.get("repair_start_transaction_id")
            ),
            repair_started_event_id=_text(metadata.get("repair_started_event_id")),
            completion_event_id=_text(event.get("event_id")),
        )
    if event_type == "repair_stage_superseded":
        if _text(metadata.get("repair_start_transaction_id")) != transaction_id:
            return (
                "Supersede repair_start_transaction_id must equal its accepted "
                "transaction_id."
            )
        return ""
    return "Owner revision event_type is invalid."


def _repair_pointer_error(
    pointer: Any,
    *,
    revision: _OwnerRevisionRecord,
    run_id: str,
) -> str:
    if not isinstance(pointer, Mapping):
        return "workflow.last_repair_transaction is required."
    if revision.status == "legacy_migrated":
        return _legacy_repair_pointer_error(
            pointer,
            event_type=revision.event_type,
            transaction_id=revision.transaction_id,
            owner_stage=revision.owner_stage,
        )
    expected = {
        "transaction_id": revision.transaction_id,
        "run_id": run_id,
        "contamination_event_id": revision.contamination_event_id,
        "owner_stage": revision.owner_stage,
        "artifact_id": revision.artifact_id,
        "rerun_start_stage": revision.rerun_start_stage,
    }
    for key, value in expected.items():
        if _text(pointer.get(key)) != _text(value):
            return f"workflow.last_repair_transaction.{key} does not match recovery event."
    return ""


def _finalize_report_state(
    *,
    report: Mapping[str, Any] | None,
    run_id: str,
    contamination_event_id: str,
    recovery_transaction_id: str,
    rerun_start_stage: str,
) -> tuple[str, str, str]:
    if report is None:
        return "missing", "finalize_report_missing", "A recovery-bound finalize report is required."
    binding = report.get("recovery_binding")
    expected = {
        "status": "bound_non_reference_recovery",
        "run_id": run_id,
        "contamination_event_id": contamination_event_id,
        "recovery_transaction_id": recovery_transaction_id,
        "rerun_start_stage": rerun_start_stage,
    }
    if not isinstance(binding, Mapping) or any(
        _text(binding.get(key)) != value for key, value in expected.items()
    ) or binding.get("reference_eligible") is not False:
        return "stale", "finalize_report_recovery_unbound", "Finalize report is not bound to the current recovery."
    reader_clean = report.get("reader_clean")
    if (
        report.get("status") != "pass"
        or not isinstance(reader_clean, Mapping)
        or reader_clean.get("status") != "pass"
        or report.get("delivery_promotion") != "promoted"
    ):
        return "failed", "finalize_report_failed", "Current recovery-bound finalize report did not pass."
    if not _text(report.get("finalize_transaction_id")):
        return "failed", "render_transaction_missing", "Finalize report transaction ID is required."
    return "current_pass", "finalize_report_current", "Current recovery-bound finalize report passed."


def _finalize_completion_binding_error(
    pointer: Any,
    *,
    event_records: Sequence[Mapping[str, Any]],
    recovery_event_id: str,
    run_id: str,
    contamination_event_id: str,
    recovery_transaction_id: str,
    render_transaction_id: str,
) -> str:
    if not isinstance(pointer, Mapping):
        return "workflow.last_completion_transaction is required."
    completion_id = _text(pointer.get("transaction_id"))
    expected = {
        "run_id": run_id,
        "stage_id": "finalize",
        "decision": "finalize",
        "render_transaction_id": render_transaction_id,
        "recovery_transaction_id": recovery_transaction_id,
        "contamination_event_id": contamination_event_id,
    }
    if not completion_id:
        return "Finalize completion transaction ID is required."
    for key, value in expected.items():
        if _text(pointer.get(key)) != value:
            return f"workflow.last_completion_transaction.{key} is not bound."
    recovery_index = next(
        (
            index
            for index, item in enumerate(event_records)
            if _text(item.get("event_id")) == recovery_event_id
        ),
        -1,
    )
    event_index, event = next(
        (
            (index, item)
            for index, item in enumerate(event_records)
            if item.get("event_type") == "decision_recorded"
            and _text(item.get("run_id")) == run_id
            and _text(_metadata(item).get("transaction_id")) == completion_id
        ),
        (-1, None),
    )
    if event is None or _text(event.get("stage_id")) != "finalize" or _text(event.get("decision")) != "finalize":
        return "Bound finalize completion event is missing."
    if recovery_index < 0 or event_index <= recovery_index:
        return "Bound finalize completion event must follow the current recovery event."
    metadata = _metadata(event)
    for key in ("render_transaction_id", "recovery_transaction_id", "contamination_event_id"):
        if _text(metadata.get(key)) != expected[key]:
            return f"Finalize completion event {key} is not bound."
    return ""


def _bound_recovery_state(
    *,
    context: RecoveryContext,
    revision: _OwnerRevisionRecord,
    contamination_event_id: str,
    status: str,
    reason_code: str,
    reason: str,
    action: str,
    render_transaction_id: str = "",
    finalize_completion_transaction_id: str = "",
) -> dict[str, Any]:
    return _state(
        status=status,
        reason_code=reason_code,
        reason=reason,
        run_id=context.run_id,
        contamination_event_id=contamination_event_id,
        recovery_transaction_id=revision.transaction_id,
        recovery_event_type=revision.event_type,
        repair_start_transaction_id=revision.repair_start_transaction_id,
        owner_stage=revision.owner_stage,
        artifact_id=revision.artifact_id,
        rerun_start_stage=revision.rerun_start_stage,
        current_stage=_text(context.workflow.get("current_stage")),
        render_transaction_id=render_transaction_id,
        finalize_completion_transaction_id=finalize_completion_transaction_id,
        recommended_recovery_action=action,
        stale_artifact_baselines=revision.stale_artifact_baselines,
        owner_revision=_owner_revision_projection(revision),
    )


def _state(
    *,
    status: str,
    reason_code: str,
    reason: str,
    run_id: str = "",
    contamination_event_id: str = "",
    recovery_transaction_id: str = "",
    recovery_event_type: str = "",
    repair_start_transaction_id: str = "",
    owner_stage: str = "",
    artifact_id: str = "",
    rerun_start_stage: str = "",
    current_stage: str = "",
    render_transaction_id: str = "",
    finalize_completion_transaction_id: str = "",
    recommended_recovery_action: str = ACTION_INSPECT_INVALID,
    stale_artifact_baselines: Any = None,
    reference_eligible: bool = False,
    details: Mapping[str, Any] | None = None,
    owner_revision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    blocks = {
        RECOVERY_NOT_APPLICABLE: (False, False, False),
        RECOVERY_AWAITING: (True, True, True),
        RECOVERY_IN_PROGRESS: (True, True, True),
        RECOVERY_RERUN_PENDING: (True, True, True),
        RECOVERY_FINALIZE_RENDER_REQUIRED: (False, True, True),
        RECOVERY_FINALIZE_COMPLETION_PENDING: (True, False, True),
        RECOVERY_COMPLETED_NON_REFERENCE: (True, True, False),
        RECOVERY_INVALID: (True, True, True),
    }[status]
    return {
        "schema_version": RECOVERY_STATE_SCHEMA,
        "runtime_effect": "read_only_recovery_projection",
        "status": status,
        "reason_code": reason_code,
        "reason": reason,
        "run_id": run_id,
        "contamination_event_id": contamination_event_id,
        "recovery_transaction_id": recovery_transaction_id,
        "recovery_event_type": recovery_event_type,
        "repair_start_transaction_id": repair_start_transaction_id,
        "owner_stage": owner_stage,
        "artifact_id": artifact_id,
        "rerun_start_stage": rerun_start_stage,
        "current_stage": current_stage,
        "render_transaction_id": render_transaction_id,
        "finalize_completion_transaction_id": finalize_completion_transaction_id,
        "recovery_blocks_finalize": blocks[0],
        "recovery_blocks_finalize_complete": blocks[1],
        "recovery_blocks_delivery": blocks[2],
        "recommended_recovery_action": recommended_recovery_action,
        "reference_eligible": reference_eligible,
        "stale_artifact_baselines": (
            dict(stale_artifact_baselines)
            if isinstance(stale_artifact_baselines, Mapping)
            else {}
        ),
        "owner_revision": dict(owner_revision or _empty_owner_revision()),
        "details": dict(details or {}),
    }


def _invalid(context: RecoveryContext, reason_code: str, reason: str) -> dict[str, Any]:
    return _state(
        status=RECOVERY_INVALID,
        reason_code=reason_code,
        reason=reason,
        run_id=context.run_id,
        current_stage=_text(context.workflow.get("current_stage")),
    )


def _metadata(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _normalize_owner_revisions(
    events: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    stage_ids: Sequence[str],
    workflow: Mapping[str, Any],
) -> tuple[list[_OwnerRevisionRecord], str]:
    revisions: list[_OwnerRevisionRecord] = []
    for event_index, event in enumerate(events):
        if event.get("event_type") not in {"repair_completed", "repair_stage_superseded"}:
            continue
        schema_version = _text(_metadata(event).get("owner_revision_schema_version"))
        if not schema_version:
            legacy, legacy_error = _legacy_owner_revision_record(
                event,
                event_index=event_index,
                is_latest_revision_event=not any(
                    later.get("event_type")
                    in {"repair_completed", "repair_stage_superseded"}
                    for later in events[event_index + 1 :]
                ),
                events=events,
                run_id=run_id,
                stage_ids=stage_ids,
                workflow=workflow,
            )
            if legacy_error:
                return [], legacy_error
            if legacy is not None:
                revisions.append(legacy)
            continue
        if schema_version != OWNER_REVISION_SCHEMA:
            return [], "Owner revision schema_version is unsupported."
        binding_error = _owner_revision_binding_error(
            event,
            event_records=events,
            run_id=run_id,
            stage_ids=stage_ids,
        )
        if binding_error:
            return [], binding_error
        metadata = _metadata(event)
        revisions.append(
            _OwnerRevisionRecord(
                status="present",
                schema_version=OWNER_REVISION_SCHEMA,
                event_id=_text(event.get("event_id")),
                event_type=_text(event.get("event_type")),
                event_index=event_index,
                transaction_id=_text(metadata.get("transaction_id")),
                repair_start_transaction_id=_text(
                    metadata.get("repair_start_transaction_id")
                ),
                repair_started_event_id=_text(
                    metadata.get("repair_started_event_id")
                ),
                contamination_event_id=_text(
                    metadata.get("contamination_event_id")
                ),
                owner_stage=_text(metadata.get("owner_stage")),
                artifact_id=_text(metadata.get("artifact_id")),
                rerun_start_stage=_text(metadata.get("rerun_start_stage")),
                stale_artifact_baselines=dict(
                    metadata.get("stale_artifact_baselines") or {}
                ),
            )
        )
    return revisions, ""


def _legacy_owner_revision_record(
    event: Mapping[str, Any],
    *,
    event_index: int,
    is_latest_revision_event: bool,
    events: Sequence[Mapping[str, Any]],
    run_id: str,
    stage_ids: Sequence[str],
    workflow: Mapping[str, Any],
) -> tuple[_OwnerRevisionRecord | None, str]:
    metadata = _metadata(event)
    transaction_id = _text(metadata.get("transaction_id"))
    if not transaction_id:
        return None, ""
    event_type = _text(event.get("event_type"))
    owner_stage = (
        _text(metadata.get("repair_owner"))
        or _text(metadata.get("stage_id"))
        or _text(event.get("stage_id"))
    )
    rerun_stage = _text(metadata.get("must_rerun_from")) or _text(
        metadata.get("next_stage")
    )
    artifact_id = _legacy_owner_revision_artifact_id(event)
    artifact_path = _text(metadata.get("artifact_path"))
    baselines, stale_matches, stale_error = _legacy_stale_evidence(
        workflow=workflow,
        event_type=event_type,
        transaction_id=transaction_id,
        owner_stage=owner_stage,
        artifact_path=artifact_path,
    )
    if stale_error:
        return None, stale_error
    contaminations = [
        (index, item)
        for index, item in enumerate(events[:event_index])
        if item.get("event_type") == "run_integrity_contaminated"
        and _text(item.get("run_id")) == run_id
    ]
    if not contaminations and stale_matches == 0:
        return None, ""
    pointer = workflow.get("last_repair_transaction")
    pointer_matches = (
        isinstance(pointer, Mapping)
        and _text(pointer.get("transaction_id")) == transaction_id
    )
    if not pointer_matches and stale_matches == 0 and not is_latest_revision_event:
        return None, ""
    if owner_stage not in stage_ids:
        return None, "Legacy owner revision owner_stage is not canonical."
    if rerun_stage not in stage_ids or stage_ids.index(rerun_stage) <= stage_ids.index(
        owner_stage
    ):
        return None, "Legacy owner revision rerun_start_stage is not canonical."
    if event_type == "repair_completed" and _text(event.get("decision")) != "repair_complete":
        return None, "Legacy repair completion decision is invalid."
    if event_type == "repair_stage_superseded" and (
        not artifact_id
        or not artifact_path
        or not _text(metadata.get("old_registered_sha256"))
        or not _text(metadata.get("current_bytes_sha256"))
    ):
        return None, "Legacy supersede artifact/hash identity is incomplete."

    pointer_error = _legacy_repair_pointer_error(
        pointer,
        event_type=event_type,
        transaction_id=transaction_id,
        owner_stage=owner_stage,
    )
    if pointer_error:
        return None, pointer_error

    contamination_event_id = ""
    repair_start_transaction_id = ""
    repair_started_event_id = ""
    if contaminations:
        contamination_index, contamination = contaminations[-1]
        contamination_event_id = _text(contamination.get("event_id"))
        if not _legacy_revision_is_non_reference(event):
            return None, "Legacy contaminated owner revision lacks non-reference posture."
        if stale_matches == 0 and not _legacy_rerun_has_advanced(
            workflow=workflow,
            rerun_stage=rerun_stage,
            stage_ids=stage_ids,
        ):
            return None, "Legacy contaminated owner revision has no rerun proof."
        if event_type == "repair_completed":
            (
                repair_start_transaction_id,
                repair_started_event_id,
                start_error,
            ) = _legacy_repair_start_lineage(
                event=event,
                event_index=event_index,
                events=events,
                run_id=run_id,
                owner_stage=owner_stage,
                rerun_stage=rerun_stage,
                contamination=contamination,
                contamination_index=contamination_index,
            )
            if start_error:
                return None, start_error
        elif event_type == "repair_stage_superseded":
            count = metadata.get("contamination_event_count")
            try:
                recorded_count = int(count)
            except (TypeError, ValueError):
                return None, "Legacy supersede contamination_event_count is invalid."
            if recorded_count != len(contaminations):
                return None, "Legacy supersede contamination count does not bind."
            repair_start_transaction_id = transaction_id
        else:
            return None, "Legacy owner revision event_type is invalid."
    elif event_type == "repair_completed":
        (
            repair_start_transaction_id,
            repair_started_event_id,
            start_error,
        ) = _legacy_repair_start_lineage(
            event=event,
            event_index=event_index,
            events=events,
            run_id=run_id,
            owner_stage=owner_stage,
            rerun_stage=rerun_stage,
        )
        if start_error:
            return None, start_error
    else:
        return None, "Legacy supersede without contamination is invalid."

    return (
        _OwnerRevisionRecord(
            status="legacy_migrated",
            schema_version="legacy_unversioned",
            event_id=_text(event.get("event_id")),
            event_type=event_type,
            event_index=event_index,
            transaction_id=transaction_id,
            repair_start_transaction_id=repair_start_transaction_id,
            repair_started_event_id=repair_started_event_id,
            contamination_event_id=contamination_event_id,
            owner_stage=owner_stage,
            artifact_id=artifact_id,
            rerun_start_stage=rerun_stage,
            stale_artifact_baselines=baselines,
        ),
        "",
    )


def _legacy_owner_revision_artifact_id(event: Mapping[str, Any]) -> str:
    metadata = _metadata(event)
    source = metadata.get("source")
    return (
        _text(metadata.get("artifact_id"))
        or _text(event.get("artifact_id"))
        or (
            _text(source.get("artifact_id"))
            if isinstance(source, Mapping)
            else ""
        )
    )


def _legacy_repair_pointer_error(
    pointer: Any,
    *,
    event_type: str,
    transaction_id: str,
    owner_stage: str,
) -> str:
    if not isinstance(pointer, Mapping):
        return "Legacy owner revision requires workflow.last_repair_transaction."
    expected_decision = (
        "repair_complete"
        if event_type == "repair_completed"
        else "supersede_stage"
    )
    if _text(pointer.get("transaction_id")) != transaction_id:
        return "Legacy owner revision pointer transaction does not match event."
    if _text(pointer.get("stage_id")) != owner_stage:
        return "Legacy owner revision pointer owner does not match event."
    if _text(pointer.get("decision")) != expected_decision:
        return "Legacy owner revision pointer decision does not match event."
    return ""


def _legacy_revision_is_non_reference(event: Mapping[str, Any]) -> bool:
    metadata = _metadata(event)
    effect = metadata.get("run_integrity_effect")
    return metadata.get("reference_eligible") is False or (
        isinstance(effect, Mapping) and effect.get("reference_eligible") is False
    )


def _legacy_rerun_has_advanced(
    *,
    workflow: Mapping[str, Any],
    rerun_stage: str,
    stage_ids: Sequence[str],
) -> bool:
    current_stage = _text(workflow.get("current_stage"))
    if current_stage and current_stage not in stage_ids:
        return False
    rerun_index = stage_ids.index(rerun_stage)
    end_index = stage_ids.index(current_stage) if current_stage else len(stage_ids)
    if end_index <= rerun_index:
        return False
    statuses = workflow.get("stage_statuses")
    if not isinstance(statuses, Mapping):
        return False
    for stage_id in stage_ids[rerun_index:end_index]:
        entry = statuses.get(stage_id)
        if not isinstance(entry, Mapping) or entry.get("status") not in {
            "complete",
            "skipped",
        }:
            return False
    return True


def _legacy_repair_start_lineage(
    *,
    event: Mapping[str, Any],
    event_index: int,
    events: Sequence[Mapping[str, Any]],
    run_id: str,
    owner_stage: str,
    rerun_stage: str,
    contamination: Mapping[str, Any] | None = None,
    contamination_index: int = -1,
) -> tuple[str, str, str]:
    completion_source = _metadata(event).get("source")
    if not isinstance(completion_source, Mapping):
        return "", "", "Legacy repair completion source identity is required."
    contamination_start_id = ""
    if contamination is not None:
        details = _metadata(contamination).get("details")
        if isinstance(details, Mapping):
            contamination_start_id = _text(details.get("repair_transaction_id"))
    candidates: list[Mapping[str, Any]] = []
    for index, candidate in enumerate(events[:event_index]):
        if (
            candidate.get("event_type") != "repair_started"
            or _text(candidate.get("run_id")) != run_id
            or _text(candidate.get("stage_id")) != owner_stage
        ):
            continue
        candidate_metadata = _metadata(candidate)
        if (
            _text(candidate_metadata.get("repair_owner")) != owner_stage
            or _text(candidate_metadata.get("must_rerun_from")) != rerun_stage
        ):
            continue
        candidate_source = candidate_metadata.get("source")
        if not isinstance(candidate_source, Mapping) or dict(
            completion_source
        ) != dict(candidate_source):
            continue
        start_transaction_id = _text(candidate_metadata.get("transaction_id"))
        if not start_transaction_id:
            continue
        if contamination is not None and index <= contamination_index:
            if start_transaction_id != contamination_start_id:
                continue
        candidates.append(candidate)
    if not candidates:
        return "", "", "Legacy repair completion has no provable start event."
    if len(candidates) != 1:
        return "", "", "Legacy repair completion has ambiguous start events."
    started = candidates[0]
    return (
        _text(_metadata(started).get("transaction_id")),
        _text(started.get("event_id")),
        "",
    )


def _legacy_stale_evidence(
    *,
    workflow: Mapping[str, Any],
    event_type: str,
    transaction_id: str,
    owner_stage: str,
    artifact_path: str,
) -> tuple[dict[str, Any], int, str]:
    baselines: dict[str, Any] = {}
    matches = 0
    statuses = workflow.get("stage_statuses")
    if isinstance(statuses, Mapping):
        for entry in statuses.values():
            stage_metadata = (
                entry.get("metadata")
                if isinstance(entry, Mapping) and isinstance(entry.get("metadata"), Mapping)
                else {}
            )
            repair_match = (
                event_type == "repair_completed"
                and stage_metadata.get("stale_after_repair") is True
                and _text(stage_metadata.get("repair_transaction_id")) == transaction_id
            )
            supersede_match = (
                event_type == "repair_stage_superseded"
                and stage_metadata.get("stale_after_supersede") is True
                and _text(stage_metadata.get("supersede_transaction_id"))
                == transaction_id
            )
            if not repair_match and not supersede_match:
                continue
            matches += 1
            if repair_match and _text(stage_metadata.get("repair_owner")) != owner_stage:
                return {}, 0, "Legacy repair stale owner conflicts with event."
            if supersede_match and (
                _text(stage_metadata.get("supersede_stage")) != owner_stage
                or (
                    artifact_path
                    and _text(stage_metadata.get("supersede_artifact"))
                    != artifact_path
                )
            ):
                return {}, 0, "Legacy supersede stale metadata conflicts with event."
            stage_baselines = stage_metadata.get("stale_artifact_baselines")
            if stage_baselines is None:
                continue
            if not isinstance(stage_baselines, Mapping):
                return {}, 0, "Legacy owner revision stale baselines are invalid."
            for artifact_id, baseline in stage_baselines.items():
                key = _text(artifact_id)
                if not key or not isinstance(baseline, Mapping):
                    return {}, 0, "Legacy owner revision stale baselines are invalid."
                if key in baselines and baselines[key] != baseline:
                    return {}, 0, "Legacy owner revision stale baselines conflict."
                baselines[key] = dict(baseline)
    return baselines, matches, ""


def _owner_revision_projection(
    revision: _OwnerRevisionRecord | None,
) -> dict[str, Any]:
    if revision is None:
        return _empty_owner_revision()
    return {
        "status": revision.status,
        "schema_version": revision.schema_version,
        "event_id": revision.event_id,
        "event_type": revision.event_type,
        "transaction_id": revision.transaction_id,
        "owner_stage": revision.owner_stage,
        "artifact_id": revision.artifact_id,
        "rerun_start_stage": revision.rerun_start_stage,
        "stale_artifact_baselines": dict(revision.stale_artifact_baselines),
    }


def _empty_owner_revision() -> dict[str, Any]:
    return {
        "status": "none",
        "schema_version": "",
        "event_id": "",
        "event_type": "",
        "transaction_id": "",
        "owner_stage": "",
        "artifact_id": "",
        "rerun_start_stage": "",
        "stale_artifact_baselines": {},
    }


def _active_repair_artifact_id(active_repair: Mapping[str, Any]) -> str:
    source = active_repair.get("source")
    return _text(source.get("artifact_id")) if isinstance(source, Mapping) else ""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
