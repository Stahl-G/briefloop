"""Typed Unit of Work for the non-authoritative SQLite substrate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from multi_agent_brief.contracts.v2 import (
    Approval,
    ArtifactRecord,
    ArtifactRevision,
    Delivery,
    EventEnvelope,
    Invocation,
    RunIdentity,
    StageState,
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

    def put_run(self, record: RunIdentity) -> None:
        self._require_run(record)
        if type(record) is not RunIdentity:
            raise ControlStoreIntegrityError("unsupported_control_record")
        if record.workspace_id != self._store.workspace_id:
            raise ControlStoreConflict("control_record_workspace_mismatch")
        if self._run is not None:
            raise ControlStoreConflict("duplicate_staged_record")
        self._run = record

    def put_stage_state(self, record: StageState) -> None:
        self._require_run(record)
        if type(record) is not StageState:
            raise ControlStoreIntegrityError("unsupported_control_record")
        self._put_unique(self._stage_states, record.stage_id, record)

    def put_invocation(self, record: Invocation) -> None:
        self._require_run(record)
        if type(record) is not Invocation:
            raise ControlStoreIntegrityError("unsupported_control_record")
        self._put_unique(self._invocations, record.invocation_id, record)

    def put_artifact(self, record: ArtifactRecord) -> None:
        self._require_run(record)
        if type(record) is not ArtifactRecord:
            raise ControlStoreIntegrityError("unsupported_control_record")
        self._put_unique(self._artifacts, record.artifact_id, record)

    def put_artifact_revision(
        self,
        record: ArtifactRevision,
        content: bytes,
    ) -> None:
        self._require_run(record)
        if type(record) is not ArtifactRevision:
            raise ControlStoreIntegrityError("unsupported_control_record")
        if type(content) is not bytes:
            raise ControlStoreIntegrityError("artifact_blob_bytes_required")
        key = (record.artifact_id, record.revision)
        if key in self._artifact_revision_keys:
            raise ControlStoreConflict("duplicate_staged_record")
        if len(content) != record.size_bytes:
            raise ControlStoreIntegrityError("artifact_blob_size_mismatch")
        if sha256_hex(content) != record.sha256:
            raise ControlStoreIntegrityError("artifact_blob_hash_mismatch")
        self._artifact_revision_keys.add(key)
        self._artifact_revisions.append(
            _StagedArtifactRevision(record=record, content=content)
        )

    def append_event(self, record: EventEnvelope) -> None:
        self._require_run(record)
        if type(record) is not EventEnvelope:
            raise ControlStoreIntegrityError("unsupported_control_record")
        if (
            record.transaction_id is not None
            and record.transaction_id != self.transaction_id
        ):
            raise ControlStoreConflict("control_record_transaction_mismatch")
        if record.event_id in self._event_ids:
            raise ControlStoreConflict("duplicate_staged_record")
        self._event_ids.add(record.event_id)
        self._events.append(record)

    def put_approval(self, record: Approval) -> None:
        self._require_run(record)
        if type(record) is not Approval:
            raise ControlStoreIntegrityError("unsupported_control_record")
        self._put_unique(self._approvals, record.approval_id, record)

    def put_delivery(self, record: Delivery) -> None:
        self._require_run(record)
        if type(record) is not Delivery:
            raise ControlStoreIntegrityError("unsupported_control_record")
        self._put_unique(self._deliveries, record.delivery_id, record)

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
