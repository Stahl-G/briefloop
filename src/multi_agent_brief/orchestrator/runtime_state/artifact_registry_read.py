"""One typed, fail-closed interpretation of ``artifact_registry.json``."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Union, cast

from multi_agent_brief.contracts.agent_artifact_intake import (
    AGENT_ARTIFACT_IDS,
    AgentArtifactId,
    IntakeResult,
    evaluate_workspace_agent_artifact_intakes,
    validate_registry_intake_context,
)
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file
from multi_agent_brief.orchestrator.runtime_state.artifact_paths import (
    artifact_paths_from_contracts,
    validate_workspace_relative_artifact_path,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_EXPECTED,
    ARTIFACT_INVALID,
    ARTIFACT_MISSING,
    ARTIFACT_PRESENT,
    ARTIFACT_REGISTRY_SCHEMA,
    ARTIFACT_STALE,
    ARTIFACT_VALID,
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
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


RegistryReadKind = Literal[
    "canonical",
    "not_materialized",
    "degradation",
    "snapshot_drift",
]

_PERSISTED_STATUSES = {
    ARTIFACT_EXPECTED,
    ARTIFACT_MISSING,
    ARTIFACT_PRESENT,
    ARTIFACT_VALID,
    ARTIFACT_INVALID,
    ARTIFACT_STALE,
}
_ABSENT_STATUSES = {ARTIFACT_EXPECTED, ARTIFACT_MISSING}
_OBSERVED_STATUSES = {
    ARTIFACT_PRESENT,
    ARTIFACT_VALID,
    ARTIFACT_INVALID,
    ARTIFACT_STALE,
}
_TOP_LEVEL_FIELDS = {"schema_version", "run_id", "updated_at", "artifacts"}
_REQUIRED_RECORD_FIELDS = {
    "artifact_id",
    "path",
    "format",
    "required",
    "producer_stage",
    "producer_role",
    "consumer_stages",
    "status",
    "validation_result",
    "blocking_reason",
    "allowed_decisions",
    "retry_or_human_review_decision",
    "size_bytes",
    "mtime",
    "sha256",
}
_OPTIONAL_RECORD_FIELDS = {"stale_baseline_sha256", "intake_projection"}
_CONTRACT_BOUND_RECORD_FIELDS = (
    "path",
    "format",
    "producer_stage",
    "producer_role",
    "consumer_stages",
    "allowed_decisions",
    "retry_or_human_review_decision",
)
_INTAKE_PROJECTION_FIELDS = {
    "schema_version",
    "artifact_id",
    "transform_version",
    "raw_sha256",
    "normalized_sha256",
    "normalization_count",
    "fatal_finding_count",
    "normalizations",
    "findings",
}
_STALE_VALIDATION_RESULTS = {"stale_after_repair", "stale_after_supersede"}


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
    """Interpret the current Registry without writing or recomputing its truth."""

    ws = Path(workspace).expanduser().resolve()
    state_paths = runtime_state_paths(ws)
    registry_path = state_paths["artifact_registry"]
    if registry_path.is_symlink():
        return RegistryDegradation("artifact_registry_control_path_unsafe")
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
    if manifest_path.is_symlink():
        return RegistryDegradation("artifact_registry_manifest_path_unsafe")
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
    intake_bundle = evaluate_workspace_agent_artifact_intakes(
        ws,
        artifact_paths={
            cast(AgentArtifactId, artifact_id): resolved_paths[artifact_id]
            for artifact_id in AGENT_ARTIFACT_IDS
            if artifact_id in resolved_paths
        },
    )

    seen_record_ids: set[str] = set()
    canonical_records: dict[str, Mapping[str, Any]] = {}
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

        actual_fields = set(record)
        if not _REQUIRED_RECORD_FIELDS.issubset(actual_fields):
            return RegistryDegradation("artifact_registry_record_fields_invalid")
        if actual_fields - (_REQUIRED_RECORD_FIELDS | _OPTIONAL_RECORD_FIELDS):
            return RegistryDegradation("artifact_registry_record_fields_invalid")

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

        status = record.get("status")
        if status not in _PERSISTED_STATUSES:
            return RegistryDegradation("artifact_registry_record_status_invalid")
        validation_result = record.get("validation_result")
        blocking_reason = record.get("blocking_reason")
        if not _nonempty_text(validation_result) or not isinstance(blocking_reason, str):
            return RegistryDegradation("artifact_registry_record_status_shape_invalid")
        status_shape_reason = _status_shape_reason(record)
        if status_shape_reason is not None:
            return RegistryDegradation(status_shape_reason)

        intake_reason = _intake_projection_reason(
            registry=registry,
            record=record,
            artifact_id=artifact_id,
            expected_run_id=registry_run_id,
            result=(
                intake_bundle.get(cast(AgentArtifactId, artifact_id))
                if artifact_id in AGENT_ARTIFACT_IDS
                else None
            ),
        )
        if intake_reason is not None:
            return RegistryDegradation(intake_reason)

        snapshot_reason = _snapshot_drift_reason(
            record=record,
            path=resolved_paths[artifact_id],
        )
        if snapshot_reason is not None:
            return RegistrySnapshotDrift(snapshot_reason)
        canonical_records[artifact_id] = cast(
            Mapping[str, Any],
            _freeze_json(record),
        )

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


def _validated_run_id(value: Any) -> str | None:
    try:
        return _validate_runtime_run_id(value)
    except RuntimeStateError:
        return None


def _status_shape_reason(record: Mapping[str, Any]) -> str | None:
    status = record["status"]
    validation_result = record["validation_result"]
    blocking_reason = record["blocking_reason"]
    if status == ARTIFACT_EXPECTED:
        if validation_result != "not_checked" or blocking_reason:
            return "artifact_registry_record_status_shape_invalid"
    elif status == ARTIFACT_MISSING:
        if validation_result != "missing" or not blocking_reason:
            return "artifact_registry_record_status_shape_invalid"
    elif status in {ARTIFACT_PRESENT, ARTIFACT_VALID}:
        if blocking_reason:
            return "artifact_registry_record_status_shape_invalid"
    elif status == ARTIFACT_INVALID:
        if not blocking_reason:
            return "artifact_registry_record_status_shape_invalid"
    elif status == ARTIFACT_STALE:
        stale_sha = record.get("stale_baseline_sha256")
        if (
            validation_result not in _STALE_VALIDATION_RESULTS
            or not blocking_reason
            or not _valid_sha256(stale_sha)
            or stale_sha != record.get("sha256")
        ):
            return "artifact_registry_record_status_shape_invalid"
    if status != ARTIFACT_STALE and "stale_baseline_sha256" in record:
        return "artifact_registry_record_fields_invalid"
    return None


def _intake_projection_reason(
    *,
    registry: Mapping[str, Any],
    record: Mapping[str, Any],
    artifact_id: str,
    expected_run_id: str,
    result: IntakeResult | None,
) -> str | None:
    projection_present = "intake_projection" in record
    if artifact_id not in AGENT_ARTIFACT_IDS:
        return "artifact_registry_intake_projection_invalid" if projection_present else None
    if record["status"] in _ABSENT_STATUSES:
        return "artifact_registry_intake_projection_invalid" if projection_present else None
    if record["status"] == ARTIFACT_PRESENT:
        return "artifact_registry_record_status_shape_invalid"
    projection = record.get("intake_projection")
    if not isinstance(projection, dict) or set(projection) != _INTAKE_PROJECTION_FIELDS:
        return "artifact_registry_intake_projection_invalid"
    reasons = validate_registry_intake_context(
        registry,
        expected_run_id=expected_run_id,
        artifact_id=cast(AgentArtifactId, artifact_id),
        result=result,
    )
    return "artifact_registry_intake_projection_invalid" if reasons else None


def _snapshot_drift_reason(
    *,
    record: Mapping[str, Any],
    path: Path,
) -> str | None:
    status = record["status"]
    try:
        exists = path.exists()
    except OSError:
        return "artifact_registry_snapshot_unreadable"
    if status in _ABSENT_STATUSES:
        if exists:
            return "artifact_registry_snapshot_presence_drift"
        if any(record.get(field) is not None for field in ("size_bytes", "mtime", "sha256")):
            return "artifact_registry_snapshot_metadata_drift"
        return None
    if status not in _OBSERVED_STATUSES:  # pragma: no cover - guarded above
        return "artifact_registry_record_status_invalid"
    if not exists:
        return "artifact_registry_snapshot_presence_drift"

    size_bytes = record.get("size_bytes")
    mtime = record.get("mtime")
    sha256 = record.get("sha256")
    try:
        before = path.stat()
        is_file = path.is_file()
    except OSError:
        return "artifact_registry_snapshot_unreadable"
    if not _valid_timestamp(mtime):
        return "artifact_registry_snapshot_metadata_invalid"
    observed_mtime = _mtime_string(before.st_mtime)
    if mtime != observed_mtime:
        return "artifact_registry_snapshot_mtime_drift"
    if not is_file:
        if status != ARTIFACT_INVALID or size_bytes is not None or sha256 is not None:
            return "artifact_registry_snapshot_file_type_drift"
        return None
    if not _nonnegative_int(size_bytes) or size_bytes != before.st_size:
        return "artifact_registry_snapshot_size_drift"
    if not _valid_sha256(sha256):
        return "artifact_registry_snapshot_metadata_invalid"
    try:
        observed_sha256 = _sha256_file(path)
        after = path.stat()
    except OSError:
        return "artifact_registry_snapshot_unreadable"
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return "artifact_registry_snapshot_changed_during_read"
    if sha256 != observed_sha256:
        return "artifact_registry_snapshot_sha256_drift"
    return None


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _mtime_string(value: float) -> str:
    return (
        datetime.fromtimestamp(value, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
