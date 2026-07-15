"""Typed Unit of Work for the non-authoritative SQLite substrate."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar, cast

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    Approval,
    ArtifactRecord,
    ArtifactRevision,
    Delivery,
    EventEnvelope,
    Invocation,
    RunIdentity,
    StageState,
    StrictModel,
    TransactionReceipt,
)
from multi_agent_brief.control_store.errors import (
    ControlStoreConflict,
    ControlStoreIntegrityError,
    ControlStoreStateError,
)
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_model_payload,
    sha256_hex,
)

if TYPE_CHECKING:
    from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore


_RecordT = TypeVar("_RecordT", bound=StrictModel)


@dataclass(frozen=True)
class _StagedArtifactRevision:
    record: ArtifactRevision
    content: bytes


@dataclass(frozen=True)
class _TransactionIdentity:
    run_id: str
    transaction_id: str
    transaction_type: str
    expected_revision: int


class ControlUnitOfWork:
    """Collect one exact-revision transaction before its atomic DB commit."""

    def __init__(
        self,
        store: "SQLiteControlStore",
        *,
        run_id: str,
        transaction_id: str,
        transaction_type: str,
        expected_revision: int,
    ) -> None:
        self._store = store
        self._identity = _TransactionIdentity(
            run_id=run_id,
            transaction_id=transaction_id,
            transaction_type=transaction_type,
            expected_revision=expected_revision,
        )
        self._run: RunIdentity | None = None
        self._stage_states: dict[str, StageState] = {}
        self._invocations: dict[str, Invocation] = {}
        self._artifacts: dict[str, ArtifactRecord] = {}
        self._artifact_revisions: list[_StagedArtifactRevision] = []
        self._artifact_revision_keys: set[tuple[str, int]] = set()
        self._events: list[EventEnvelope] = []
        self._event_ids: set[str] = set()
        self._approvals: dict[str, Approval] = {}
        self._deliveries: dict[str, Delivery] = {}
        self._state = "active"

    @property
    def run_id(self) -> str:
        return self._identity.run_id

    @property
    def transaction_id(self) -> str:
        return self._identity.transaction_id

    @property
    def transaction_type(self) -> str:
        return self._identity.transaction_type

    @property
    def expected_revision(self) -> int:
        return self._identity.expected_revision

    def __enter__(self) -> "ControlUnitOfWork":
        self._require_active()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._state == "active":
            self.rollback()

    def _require_active(self) -> None:
        if self._state != "active":
            raise ControlStoreStateError("unit_of_work_not_active")

    def _require_run(self, model: object) -> None:
        self._require_active()
        if getattr(model, "run_id", None) != self.run_id:
            raise ControlStoreConflict("control_record_run_mismatch")

    def _snapshot_record(
        self,
        record: object,
        expected_type: type[_RecordT],
    ) -> _RecordT:
        """Revalidate and detach caller-owned DTO state at the staging boundary."""

        self._require_active()
        if type(record) is not expected_type:
            raise ControlStoreIntegrityError("unsupported_control_record")
        typed_record = cast(StrictModel, record)
        try:
            payload = {
                name: deepcopy(getattr(typed_record, name))
                for name in expected_type.model_fields
            }
            return expected_type.model_validate(payload, strict=True)
        except (AttributeError, ValidationError) as exc:
            raise ControlStoreIntegrityError("control_record_invalid") from exc

    def put_run(self, record: RunIdentity) -> None:
        snapshot = self._snapshot_record(record, RunIdentity)
        self._require_run(snapshot)
        if snapshot.workspace_id != self._store.workspace_id:
            raise ControlStoreConflict("control_record_workspace_mismatch")
        if self._run is not None:
            raise ControlStoreConflict("duplicate_staged_record")
        self._run = snapshot

    def put_stage_state(self, record: StageState) -> None:
        snapshot = self._snapshot_record(record, StageState)
        self._require_run(snapshot)
        self._put_unique(self._stage_states, snapshot.stage_id, snapshot)

    def put_invocation(self, record: Invocation) -> None:
        snapshot = self._snapshot_record(record, Invocation)
        self._require_run(snapshot)
        self._put_unique(self._invocations, snapshot.invocation_id, snapshot)

    def put_artifact(self, record: ArtifactRecord) -> None:
        snapshot = self._snapshot_record(record, ArtifactRecord)
        self._require_run(snapshot)
        self._put_unique(self._artifacts, snapshot.artifact_id, snapshot)

    def put_artifact_revision(
        self,
        record: ArtifactRevision,
        content: bytes,
    ) -> None:
        snapshot = self._snapshot_record(record, ArtifactRevision)
        self._require_run(snapshot)
        if type(content) is not bytes:
            raise ControlStoreIntegrityError("artifact_blob_bytes_required")
        key = (snapshot.artifact_id, snapshot.revision)
        if key in self._artifact_revision_keys:
            raise ControlStoreConflict("duplicate_staged_record")
        if len(content) != snapshot.size_bytes:
            raise ControlStoreIntegrityError("artifact_blob_size_mismatch")
        if sha256_hex(content) != snapshot.sha256:
            raise ControlStoreIntegrityError("artifact_blob_hash_mismatch")
        self._artifact_revision_keys.add(key)
        self._artifact_revisions.append(
            _StagedArtifactRevision(record=snapshot, content=content)
        )

    def append_event(self, record: EventEnvelope) -> None:
        snapshot = self._snapshot_record(record, EventEnvelope)
        self._require_run(snapshot)
        if (
            snapshot.transaction_id is not None
            and snapshot.transaction_id != self.transaction_id
        ):
            raise ControlStoreConflict("control_record_transaction_mismatch")
        if snapshot.event_id in self._event_ids:
            raise ControlStoreConflict("duplicate_staged_record")
        self._event_ids.add(snapshot.event_id)
        self._events.append(snapshot)

    def put_approval(self, record: Approval) -> None:
        snapshot = self._snapshot_record(record, Approval)
        self._require_run(snapshot)
        self._put_unique(self._approvals, snapshot.approval_id, snapshot)

    def put_delivery(self, record: Delivery) -> None:
        snapshot = self._snapshot_record(record, Delivery)
        self._require_run(snapshot)
        self._put_unique(self._deliveries, snapshot.delivery_id, snapshot)

    def _put_unique(
        self, collection: dict[str, object], key: str, value: object
    ) -> None:
        if key in collection:
            raise ControlStoreConflict("duplicate_staged_record")
        collection[key] = value

    def _identity_snapshot(self) -> _TransactionIdentity:
        self._require_active()
        return self._identity

    def _fingerprint(self, identity: _TransactionIdentity) -> str:
        """Fingerprint caller intent, excluding the store-generated receipt."""

        payload = {
            "run_id": identity.run_id,
            "transaction_id": identity.transaction_id,
            "transaction_type": identity.transaction_type,
            "expected_revision": identity.expected_revision,
            "run": (
                canonical_model_payload(self._run) if self._run is not None else None
            ),
            "stage_states": [
                canonical_model_payload(self._stage_states[key])
                for key in sorted(self._stage_states)
            ],
            "invocations": [
                canonical_model_payload(self._invocations[key])
                for key in sorted(self._invocations)
            ],
            "artifacts": [
                canonical_model_payload(self._artifacts[key])
                for key in sorted(self._artifacts)
            ],
            "artifact_revisions": [
                canonical_model_payload(item.record)
                for item in self._artifact_revisions
            ],
            "events": [canonical_model_payload(item) for item in self._events],
            "approvals": [
                canonical_model_payload(self._approvals[key])
                for key in sorted(self._approvals)
            ],
            "deliveries": [
                canonical_model_payload(self._deliveries[key])
                for key in sorted(self._deliveries)
            ],
        }
        return canonical_fingerprint(payload)

    def commit(self) -> TransactionReceipt:
        self._require_active()
        try:
            receipt = self._store._commit_unit_of_work(self)
        except Exception:
            self._state = "rolled_back"
            raise
        self._state = "committed"
        return receipt

    def rollback(self) -> None:
        self._require_active()
        self._state = "rolled_back"


__all__ = ["ControlUnitOfWork"]
