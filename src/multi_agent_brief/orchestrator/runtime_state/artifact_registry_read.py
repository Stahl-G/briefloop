"""One typed, fail-closed interpretation of ``artifact_registry.json``."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Union, cast

from multi_agent_brief.orchestrator.runtime_state.artifact_paths import (
    artifact_paths_from_contracts,
    validate_workspace_relative_artifact_path,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_EXPECTED,
    ARTIFACT_MISSING,
    ARTIFACT_REGISTRY_SCHEMA,
    _build_artifact_registry,
)
from multi_agent_brief.orchestrator.recovery_state import (
    RECOVERY_INVALID,
    evaluate_recovery_state,
)
from multi_agent_brief.orchestrator.runtime_state.control_context import (
    load_control_object,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    load_artifact_contracts,
)
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.identity import (
    _validate_runtime_run_id,
)
from multi_agent_brief.orchestrator.runtime_state.manifest import (
    RUNTIME_MANIFEST_SCHEMA,
)
from multi_agent_brief.orchestrator.runtime_state.paths import runtime_state_paths
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    WORKFLOW_STATE_SCHEMA,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


RegistryReadKind = Literal[
    "canonical",
    "not_materialized",
    "degradation",
    "snapshot_drift",
]

_ABSENT_STATUSES = {ARTIFACT_EXPECTED, ARTIFACT_MISSING}
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
_SNAPSHOT_DERIVED_FIELDS = {
    "status",
    "validation_result",
    "blocking_reason",
    "size_bytes",
    "mtime",
    "sha256",
    "stale_baseline_sha256",
    "intake_projection",
}
_CONTROL_PATH_REASON_CODES = (
    ("artifact_registry", "artifact_registry_control_path_unsafe"),
    ("runtime_manifest", "artifact_registry_manifest_path_unsafe"),
    ("workflow_state", "artifact_registry_workflow_path_unsafe"),
    ("event_log", "artifact_registry_event_log_path_unsafe"),
)


@dataclass(frozen=True)
class RegistryNotMaterialized:
    """The legal pre-projection state; it carries no Registry values."""

    kind: Literal["not_materialized"] = "not_materialized"
    reason_code: Literal["artifact_registry_not_materialized"] = (
        "artifact_registry_not_materialized"
    )


@dataclass(frozen=True)
class RegistryDegradation:
    """A malformed or unbound Registry; raw values are deliberately absent."""

    reason_code: str
    kind: Literal["degradation"] = "degradation"


@dataclass(frozen=True)
class RegistrySnapshotDrift:
    """A structurally bound Registry whose persisted file snapshot has drifted."""

    reason_code: str
    kind: Literal["snapshot_drift"] = "snapshot_drift"


@dataclass(frozen=True)
class CanonicalRegistryView:
    """The only read result allowed to expose Registry-derived control values."""

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


def interpret_artifact_registry(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> RegistryReadVerdict:
    """Verify the persisted Registry against its unique producer without writing."""

    ws = Path(workspace).expanduser().resolve()
    state_paths = runtime_state_paths(ws)
    control_path_reason = _control_path_chain_reason(
        workspace=ws,
        state_paths=state_paths,
    )
    if control_path_reason is not None:
        return RegistryDegradation(control_path_reason)
    registry_path = state_paths["artifact_registry"]
    if not registry_path.exists():
        return RegistryNotMaterialized()

    registry = _load_control_record(
        registry_path,
        schema=ARTIFACT_REGISTRY_SCHEMA,
        unreadable_reason="artifact_registry_unreadable",
        root_reason="artifact_registry_root_invalid",
        schema_reason="artifact_registry_schema_unsupported",
        missing_reason="artifact_registry_unreadable",
    )
    if isinstance(registry, RegistryDegradation):
        return registry

    manifest_path = state_paths["runtime_manifest"]
    manifest = _load_control_record(
        manifest_path,
        schema=RUNTIME_MANIFEST_SCHEMA,
        unreadable_reason="artifact_registry_manifest_unreadable",
        root_reason="artifact_registry_manifest_root_invalid",
        schema_reason="artifact_registry_manifest_schema_unsupported",
        missing_reason="artifact_registry_manifest_missing",
    )
    if isinstance(manifest, RegistryDegradation):
        return manifest

    if set(registry) != _TOP_LEVEL_FIELDS:
        return RegistryDegradation("artifact_registry_root_fields_invalid")
    updated_at = registry.get("updated_at")
    if not _valid_timestamp(updated_at):
        return RegistryDegradation("artifact_registry_updated_at_invalid")

    manifest_run_id = _validated_run_id(manifest.get("run_id"))
    if manifest_run_id is None:
        return RegistryDegradation("artifact_registry_manifest_run_id_invalid")
    registry_run_id = _validated_run_id(registry.get("run_id"))
    if registry_run_id is None:
        return RegistryDegradation("artifact_registry_run_id_invalid")
    if registry_run_id != manifest_run_id:
        return RegistryDegradation("artifact_registry_run_id_mismatch")

    workflow_path = state_paths["workflow_state"]
    workflow = _load_control_record(
        workflow_path,
        schema=WORKFLOW_STATE_SCHEMA,
        unreadable_reason="artifact_registry_workflow_unreadable",
        root_reason="artifact_registry_workflow_root_invalid",
        schema_reason="artifact_registry_workflow_schema_unsupported",
        missing_reason="artifact_registry_workflow_missing",
    )
    if isinstance(workflow, RegistryDegradation):
        return workflow
    workflow_run_id = _validated_run_id(workflow.get("run_id"))
    if workflow_run_id is None:
        return RegistryDegradation("artifact_registry_workflow_run_id_invalid")
    if workflow_run_id != registry_run_id:
        return RegistryDegradation("artifact_registry_workflow_run_id_mismatch")

    try:
        repo = resolve_repo_workdir(repo_workdir, workspace=ws)
        artifacts = load_artifact_contracts(repo)
    except (RuntimeStateError, ValueError):
        return RegistryDegradation("artifact_registry_contract_context_invalid")
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
    if manifest.get("expected_artifacts") != expected_manifest_artifacts:
        return RegistryDegradation("artifact_registry_manifest_contract_mismatch")
    try:
        resolved_paths = artifact_paths_from_contracts(ws, artifacts_by_id)
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
        if any(record.get(field) != contract.get(field) for field in _CONTRACT_BOUND_RECORD_FIELDS):
            return RegistryDegradation("artifact_registry_record_contract_mismatch")
        required = record.get("required")
        if not isinstance(required, bool) or required != bool(contract.get("required", False)):
            return RegistryDegradation("artifact_registry_record_contract_mismatch")

    try:
        recovery_state = evaluate_recovery_state(
            workspace=ws,
            repo_workdir=repo,
        )
    except Exception:  # fail closed at the trusted-read boundary
        return RegistryDegradation("artifact_registry_recovery_context_invalid")
    if (
        recovery_state.get("status") == RECOVERY_INVALID
        or _validated_run_id(recovery_state.get("run_id")) != registry_run_id
    ):
        return RegistryDegradation("artifact_registry_recovery_context_invalid")

    try:
        producer_replay = _build_artifact_registry(
            workspace=ws,
            run_id=registry_run_id,
            artifacts=artifacts,
            workflow=workflow,
            updated_at=cast(str, updated_at),
            recovery_state=recovery_state,
        )
    except Exception:  # producer failures never release persisted values
        return RegistryDegradation("artifact_registry_producer_replay_failed")
    if producer_replay != registry:
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


def _load_control_record(
    path: Path,
    *,
    schema: str,
    unreadable_reason: str,
    root_reason: str,
    schema_reason: str,
    missing_reason: str,
) -> dict[str, Any] | RegistryDegradation:
    try:
        payload = load_control_object(path, expected_schema=schema)
    except RuntimeStateError as exc:
        details = exc.details
        if details.get("reason_code") == "control_file_missing":
            return RegistryDegradation(missing_reason)
        if details.get("reason_code") == "control_file_not_object":
            return RegistryDegradation(root_reason)
        if details.get("expected_schema") == schema:
            return RegistryDegradation(schema_reason)
        return RegistryDegradation(unreadable_reason)
    if payload is None:  # pragma: no cover - required=True contract
        return RegistryDegradation(missing_reason)
    return payload


def _control_path_chain_reason(
    *,
    workspace: Path,
    state_paths: Mapping[str, Path],
) -> str | None:
    """Reject every symlink or identity change in the consumed control path chain."""

    for key, reason_code in _CONTROL_PATH_REASON_CODES:
        path = state_paths.get(key)
        if path is None or _control_path_chain_is_unsafe(
            workspace=workspace,
            path=path,
        ):
            return reason_code
    return None


def _control_path_chain_is_unsafe(*, workspace: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        return True

    current = workspace
    try:
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return True
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return True
    try:
        resolved.relative_to(workspace)
    except ValueError:
        return True
    return resolved != path


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
        persisted.get(field) != expected.get(field)
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
        if persisted_record == expected_record:
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
    changed_fields = {
        field
        for field in set(persisted_record) | set(expected_record)
        if persisted_record.get(field) != expected_record.get(field)
    }
    if not changed_fields or not changed_fields.issubset(_SNAPSHOT_DERIVED_FIELDS):
        return None
    persisted_snapshot = tuple(
        persisted_record.get(field) for field in ("size_bytes", "mtime", "sha256")
    )
    expected_snapshot = tuple(
        expected_record.get(field) for field in ("size_bytes", "mtime", "sha256")
    )
    if persisted_snapshot == expected_snapshot:
        return None

    persisted_absent = persisted_record.get("status") in _ABSENT_STATUSES
    expected_absent = expected_record.get("status") in _ABSENT_STATUSES
    if persisted_absent != expected_absent:
        return "artifact_registry_snapshot_presence_drift"
    persisted_size, persisted_mtime, persisted_sha = persisted_snapshot
    expected_size, expected_mtime, expected_sha = expected_snapshot
    if (
        persisted_mtime is not None
        and expected_mtime is not None
        and ((persisted_size is None) != (expected_size is None))
        and ((persisted_sha is None) != (expected_sha is None))
    ):
        return "artifact_registry_snapshot_file_type_drift"
    if persisted_mtime != expected_mtime:
        return "artifact_registry_snapshot_mtime_drift"
    if persisted_size != expected_size:
        return "artifact_registry_snapshot_size_drift"
    if persisted_sha != expected_sha:
        return "artifact_registry_snapshot_sha256_drift"
    return "artifact_registry_snapshot_metadata_drift"


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
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
