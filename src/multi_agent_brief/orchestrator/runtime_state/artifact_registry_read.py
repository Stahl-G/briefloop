"""One typed, fail-closed interpretation of ``artifact_registry.json``."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Union, cast

from multi_agent_brief.orchestrator.recovery_state import (
    RECOVERY_INVALID,
    RECOVERY_NOT_APPLICABLE,
    RecoveryContext,
    RecoveryContextNotMaterialized,
    interpret_recovery_state,
    load_recovery_context_verdict,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_paths import (
    artifact_paths_from_contracts,
    validate_workspace_relative_artifact_path,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    _build_artifact_registry,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    load_artifact_contracts,
)
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.identity import (
    _validate_runtime_run_id,
)
from multi_agent_brief.orchestrator.runtime_state.paths import RUNTIME_STATE_FILES
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    workflow_with_persistable_stage_completions,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


RegistryReadKind = Literal[
    "canonical",
    "not_materialized",
    "degradation",
    "snapshot_drift",
]

_TOP_LEVEL_FIELDS = {"schema_version", "run_id", "updated_at", "artifacts"}
_CONTRACT_BOUND_RECORD_FIELDS = (
    "path",
    "format",
    "producer_stage",
    "producer_role",
    "consumer_stages",
    "allowed_decisions",
    "retry_or_human_review_decision",
)
_PHYSICAL_SNAPSHOT_FIELDS = frozenset({"size_bytes", "mtime", "sha256"})


@dataclass(frozen=True)
class RegistryNotMaterialized:
    """A legal observed pre-projection state carrying no Registry values."""

    kind: Literal["not_materialized"] = "not_materialized"
    reason_code: Literal["artifact_registry_not_materialized"] = (
        "artifact_registry_not_materialized"
    )


@dataclass(frozen=True)
class RegistryDegradation:
    """An invalid or unbound Registry context with no raw values."""

    reason_code: str
    kind: Literal["degradation"] = "degradation"


@dataclass(frozen=True)
class RegistrySnapshotDrift:
    """A structurally bound Registry whose producer-observed snapshot drifted."""

    reason_code: str
    kind: Literal["snapshot_drift"] = "snapshot_drift"


@dataclass(frozen=True)
class CanonicalRegistryView:
    """The only read result allowed to expose Registry-derived values."""

    run_id: str
    updated_at: str
    records: Mapping[str, Mapping[str, Any]]
    resolved_paths: Mapping[str, Path]
    kind: Literal["canonical"] = "canonical"

    @property
    def artifact_count(self) -> int:
        return len(self.records)

    @property
    def status_counts(self) -> Mapping[str, int]:
        return MappingProxyType(
            dict(Counter(str(record["status"]) for record in self.records.values()))
        )


RegistryReadVerdict = Union[
    CanonicalRegistryView,
    RegistryNotMaterialized,
    RegistryDegradation,
    RegistrySnapshotDrift,
]


@dataclass(frozen=True)
class _RegistryPrerequisites:
    """Private validated inputs required before Registry presence is classified."""

    workspace: Path
    artifacts: tuple[dict[str, Any], ...]
    artifacts_by_id: Mapping[str, dict[str, Any]]
    context: RecoveryContext
    run_id: str
    workflow: dict[str, Any]
    recovery_state: dict[str, Any]


def interpret_artifact_registry(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> RegistryReadVerdict:
    """Return one total typed verdict without writing or exposing bad inputs."""

    try:
        return _interpret_artifact_registry(
            workspace=workspace,
            repo_workdir=repo_workdir,
        )
    except Exception:
        # The public reader is a fail-closed total function. Specific invalid
        # caller/input boundaries below retain stable reason codes; this final
        # guard prevents unforeseen malformed inputs from releasing values.
        return RegistryDegradation("artifact_registry_interpretation_failed")


def _interpret_artifact_registry(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None,
) -> RegistryReadVerdict:
    try:
        ws = Path(workspace).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return RegistryDegradation("artifact_registry_workspace_invalid")

    try:
        repo = resolve_repo_workdir(repo_workdir)
        artifacts = load_artifact_contracts(repo)
    except (RuntimeStateError, OSError, RuntimeError, TypeError, ValueError):
        return RegistryDegradation("artifact_registry_contract_context_invalid")

    try:
        recovery_verdict = load_recovery_context_verdict(
            workspace=ws,
            repo_workdir=repo,
        )
    except (RuntimeStateError, OSError, RuntimeError, TypeError, ValueError):
        return RegistryDegradation("artifact_registry_recovery_context_invalid")

    if isinstance(recovery_verdict, RecoveryContextNotMaterialized):
        return RegistryNotMaterialized()

    prerequisites = _validate_registry_prerequisites(
        workspace=ws,
        artifacts=artifacts,
        context=recovery_verdict,
    )
    if isinstance(prerequisites, RegistryDegradation):
        return prerequisites

    context = prerequisites.context
    registry = context.artifact_registry
    if registry is None:
        if context.finalize_report is not None:
            return RegistryDegradation("artifact_registry_recovery_context_invalid")
        if (
            prerequisites.recovery_state.get("status") == RECOVERY_NOT_APPLICABLE
            and prerequisites.recovery_state.get("reason_code")
            == "no_current_contamination"
        ):
            return RegistryNotMaterialized()
        return RegistryDegradation("artifact_registry_recovery_context_invalid")

    return _interpret_materialized_registry(
        prerequisites=prerequisites,
        registry=registry,
    )


def _validate_registry_prerequisites(
    *,
    workspace: Path,
    artifacts: list[dict[str, Any]],
    context: RecoveryContext,
) -> _RegistryPrerequisites | RegistryDegradation:
    """Validate loaded controls independently of Registry presence."""

    manifest = context.runtime_manifest
    workflow_payload = context.workflow

    manifest_run_id = _validated_run_id(manifest.get("run_id"))
    if manifest_run_id is None or context.run_id != manifest_run_id:
        return RegistryDegradation("artifact_registry_manifest_run_id_invalid")

    workflow_run_id = _validated_run_id(workflow_payload.get("run_id"))
    if workflow_run_id is None:
        return RegistryDegradation("artifact_registry_workflow_run_id_invalid")
    if workflow_run_id != manifest_run_id:
        return RegistryDegradation("artifact_registry_workflow_run_id_mismatch")

    stage_statuses = workflow_payload.get("stage_statuses")
    if not isinstance(stage_statuses, Mapping) or set(stage_statuses) != set(
        context.stage_ids
    ):
        return RegistryDegradation("artifact_registry_workflow_stage_status_invalid")
    stages = [{"stage_id": stage_id} for stage_id in context.stage_ids]
    try:
        workflow = workflow_with_persistable_stage_completions(
            dict(workflow_payload),
            stages=stages,
            path=workspace / RUNTIME_STATE_FILES["workflow_state"],
        )
    except RuntimeStateError:
        return RegistryDegradation("artifact_registry_workflow_stage_status_invalid")

    artifacts_by_id = {
        str(artifact["artifact_id"]): artifact
        for artifact in artifacts
    }
    expected_manifest_artifacts = [
        {
            "artifact_id": artifact.get("artifact_id", ""),
            "path": artifact.get("path", ""),
            "required": bool(artifact.get("required", False)),
            "producer_stage": artifact.get("producer_stage", ""),
            "consumer_stages": artifact.get("consumer_stages", []),
        }
        for artifact in artifacts
    ]
    if not _json_values_equal(
        manifest.get("expected_artifacts"),
        expected_manifest_artifacts,
    ):
        return RegistryDegradation("artifact_registry_manifest_contract_mismatch")

    recovery_state = interpret_recovery_state(context)
    if (
        recovery_state.get("status") == RECOVERY_INVALID
        or _validated_run_id(recovery_state.get("run_id")) != manifest_run_id
    ):
        return RegistryDegradation("artifact_registry_recovery_context_invalid")

    return _RegistryPrerequisites(
        workspace=workspace,
        artifacts=tuple(artifacts),
        artifacts_by_id=MappingProxyType(artifacts_by_id),
        context=context,
        run_id=manifest_run_id,
        workflow=workflow,
        recovery_state=recovery_state,
    )


def _interpret_materialized_registry(
    *,
    prerequisites: _RegistryPrerequisites,
    registry: Mapping[str, Any],
) -> RegistryReadVerdict:
    if set(registry) != _TOP_LEVEL_FIELDS:
        return RegistryDegradation("artifact_registry_root_fields_invalid")
    updated_at = registry.get("updated_at")
    if not _valid_timestamp(updated_at):
        return RegistryDegradation("artifact_registry_updated_at_invalid")

    registry_run_id = _validated_run_id(registry.get("run_id"))
    if registry_run_id is None:
        return RegistryDegradation("artifact_registry_run_id_invalid")
    if registry_run_id != prerequisites.run_id:
        return RegistryDegradation("artifact_registry_run_id_mismatch")

    artifacts_by_id = prerequisites.artifacts_by_id
    try:
        resolved_paths = artifact_paths_from_contracts(
            prerequisites.workspace,
            artifacts_by_id,
        )
    except RuntimeStateError:
        return RegistryDegradation("artifact_registry_path_context_invalid")

    records = registry.get("artifacts")
    if not isinstance(records, dict):
        return RegistryDegradation("artifact_registry_artifacts_invalid")
    contract_ids = set(artifacts_by_id)
    if set(records) != contract_ids:
        return RegistryDegradation("artifact_registry_artifact_universe_mismatch")

    seen_record_ids: set[str] = set()
    for artifact_id in sorted(contract_ids):
        record = records.get(artifact_id)
        if not isinstance(record, dict):
            return RegistryDegradation("artifact_registry_record_not_object")
        record_id = record.get("artifact_id")
        if isinstance(record_id, str) and record_id in seen_record_ids:
            return RegistryDegradation("artifact_registry_record_identity_duplicate")
        if isinstance(record_id, str):
            seen_record_ids.add(record_id)
        if record_id != artifact_id:
            return RegistryDegradation("artifact_registry_record_identity_mismatch")

        contract = artifacts_by_id[artifact_id]
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return RegistryDegradation("artifact_registry_record_path_invalid")
        try:
            canonical_path = validate_workspace_relative_artifact_path(
                raw_path,
                artifact_id=artifact_id,
                binding_source="artifact_registry",
            )
        except RuntimeStateError:
            return RegistryDegradation("artifact_registry_record_path_invalid")
        if canonical_path != raw_path:
            return RegistryDegradation("artifact_registry_record_path_invalid")
        if any(
            not _json_values_equal(record.get(field), contract.get(field))
            for field in _CONTRACT_BOUND_RECORD_FIELDS
        ):
            return RegistryDegradation("artifact_registry_record_contract_mismatch")
        required = record.get("required")
        if not isinstance(required, bool) or required != bool(
            contract.get("required", False)
        ):
            return RegistryDegradation("artifact_registry_record_contract_mismatch")

    try:
        producer_replay = _build_artifact_registry(
            workspace=prerequisites.workspace,
            run_id=registry_run_id,
            artifacts=list(prerequisites.artifacts),
            workflow=prerequisites.workflow,
            updated_at=cast(str, updated_at),
            recovery_state=prerequisites.recovery_state,
        )
    except Exception:
        return RegistryDegradation("artifact_registry_producer_replay_failed")
    if not _json_values_equal(producer_replay, registry):
        return _producer_replay_mismatch_verdict(
            persisted=registry,
            expected=producer_replay,
        )

    canonical_records = {
        artifact_id: cast(
            Mapping[str, Any],
            _freeze_json(cast(dict[str, Any], records[artifact_id])),
        )
        for artifact_id in sorted(contract_ids)
    }
    return CanonicalRegistryView(
        run_id=registry_run_id,
        updated_at=cast(str, updated_at),
        records=MappingProxyType(canonical_records),
        resolved_paths=MappingProxyType(dict(resolved_paths)),
    )


def _validated_run_id(value: Any) -> str | None:
    try:
        return _validate_runtime_run_id(value)
    except RuntimeStateError:
        return None


def _producer_replay_mismatch_verdict(
    *,
    persisted: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> RegistryDegradation | RegistrySnapshotDrift:
    """Classify producer-derived snapshot drift without exposing either payload."""

    if any(
        not _json_values_equal(persisted.get(field), expected.get(field))
        for field in _TOP_LEVEL_FIELDS - {"artifacts"}
    ):
        return RegistryDegradation("artifact_registry_producer_replay_mismatch")
    persisted_records = persisted.get("artifacts")
    expected_records = expected.get("artifacts")
    if not isinstance(persisted_records, Mapping) or not isinstance(
        expected_records, Mapping
    ):
        return RegistryDegradation("artifact_registry_producer_replay_mismatch")
    if set(persisted_records) != set(expected_records):
        return RegistryDegradation("artifact_registry_producer_replay_mismatch")

    drift_reasons: list[str] = []
    for artifact_id in sorted(persisted_records):
        persisted_record = persisted_records[artifact_id]
        expected_record = expected_records[artifact_id]
        if _json_values_equal(persisted_record, expected_record):
            continue
        reason = _producer_snapshot_drift_reason(
            persisted_record=persisted_record,
            expected_record=expected_record,
        )
        if reason is None:
            return RegistryDegradation("artifact_registry_producer_replay_mismatch")
        drift_reasons.append(reason)
    if drift_reasons:
        return RegistrySnapshotDrift(drift_reasons[0])
    return RegistryDegradation("artifact_registry_producer_replay_mismatch")


def _producer_snapshot_drift_reason(
    *,
    persisted_record: Any,
    expected_record: Any,
) -> str | None:
    if not isinstance(persisted_record, Mapping) or not isinstance(
        expected_record, Mapping
    ):
        return None
    if set(persisted_record) != set(expected_record):
        # Snapshot drift is reserved for a structurally complete persisted
        # writer record. Missing or additional fields are Registry corruption,
        # even when the current artifact also changed.
        return None
    if any(
        not _json_values_equal(persisted_record[field], expected_record[field])
        for field in set(persisted_record) - _PHYSICAL_SNAPSHOT_FIELDS
    ):
        # Status, validation, staleness, intake, and every other non-physical
        # value are producer truth, not evidence of physical snapshot drift.
        return None
    persisted_snapshot = tuple(
        persisted_record.get(field) for field in ("size_bytes", "mtime", "sha256")
    )
    expected_snapshot = tuple(
        expected_record.get(field) for field in ("size_bytes", "mtime", "sha256")
    )
    if not _valid_regular_file_snapshot(persisted_snapshot) or not (
        _valid_regular_file_snapshot(expected_snapshot)
    ):
        return None
    if _json_values_equal(persisted_snapshot, expected_snapshot):
        return None
    persisted_size, persisted_mtime, persisted_sha = persisted_snapshot
    expected_size, expected_mtime, expected_sha = expected_snapshot
    if not _json_values_equal(persisted_mtime, expected_mtime):
        return "artifact_registry_snapshot_mtime_drift"
    if not _json_values_equal(persisted_size, expected_size):
        return "artifact_registry_snapshot_size_drift"
    if not _json_values_equal(persisted_sha, expected_sha):
        return "artifact_registry_snapshot_sha256_drift"
    return None


def _valid_regular_file_snapshot(snapshot: tuple[Any, Any, Any]) -> bool:
    size_bytes, mtime, sha256 = snapshot
    if type(size_bytes) is not int or size_bytes < 0:
        return False
    if not _valid_writer_file_mtime(mtime):
        return False
    return (
        isinstance(sha256, str)
        and len(sha256) == 64
        and all(character in "0123456789abcdef" for character in sha256)
    )


def _valid_writer_file_mtime(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.utcoffset().total_seconds() == 0
        and parsed.microsecond == 0
        and value == parsed.isoformat()
    )


def _json_values_equal(left: Any, right: Any) -> bool:
    """Compare JSON values recursively without Python numeric type aliases."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _json_values_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            _json_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return left == right


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None
