"""Typed SQLite ControlStore substrate with no current runtime authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import threading
from typing import TYPE_CHECKING, Callable, Iterable, TypeVar
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from multi_agent_brief.contracts.v2 import (
    Approval,
    ArtifactRecord,
    ArtifactRevision,
    ArtifactRevisionReference,
    ContractId,
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
    ControlStoreError,
    ControlStoreIntegrityError,
    ControlStoreStateError,
)
from multi_agent_brief.control_store.schema import (
    configure_connection,
    initialize_schema,
    verify_schema,
)
from multi_agent_brief.control_store.serialization import (
    canonical_json_bytes,
    canonical_model_text,
    decode_model,
    sha256_hex,
)


_ModelT = TypeVar("_ModelT", bound=StrictModel)
_FailureHook = Callable[[str], None]
_CONTRACT_ID_ADAPTER = TypeAdapter(ContractId)


def _validate_contract_id(value: object, error_code: str) -> str:
    """Reuse the PR-1 ContractId vocabulary without copying its grammar."""

    try:
        return _CONTRACT_ID_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise ControlStoreIntegrityError(error_code) from exc


@dataclass(frozen=True)
class ControlStoreSnapshot:
    """One immutable typed view of a run at the store's current revision."""

    workspace_id: str
    store_revision: int
    run: RunIdentity
    stage_states: tuple[StageState, ...]
    invocations: tuple[Invocation, ...]
    artifacts: tuple[ArtifactRecord, ...]
    artifact_revisions: tuple[ArtifactRevision, ...]
    events: tuple[EventEnvelope, ...]
    approvals: tuple[Approval, ...]
    deliveries: tuple[Delivery, ...]
    transactions: tuple[TransactionReceipt, ...]


@dataclass(frozen=True)
class OrphanBlobScan:
    """Report-only blob inventory; it never accepts or removes a blob."""

    orphan_hashes: tuple[str, ...]
    malformed_paths: tuple[str, ...]


class SQLiteControlStore:
    """Persist typed v2 DTOs without replacing any current JSON authority."""

    def __init__(
        self,
        *,
        path: Path,
        blob_root: Path,
        connection: sqlite3.Connection,
        workspace_id: str,
        clock: Callable[[], datetime] | None = None,
        failure_hook: _FailureHook | None = None,
    ) -> None:
        self.path = path
        self.blob_root = blob_root
        self.workspace_id = workspace_id
        self._connection = connection
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._failure_hook = failure_hook
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        *,
        workspace_id: str,
        blob_root: str | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
        _failure_hook: _FailureHook | None = None,
    ) -> "SQLiteControlStore":
        workspace_id = _validate_contract_id(workspace_id, "workspace_id_invalid")
        database_path = cls._normalize_path(path, "database_path_invalid")
        blobs = cls._blob_root_for(database_path, blob_root)
        cls._validate_database_blob_separation(database_path, blobs)
        if database_path.exists() or database_path.is_symlink():
            raise ControlStoreStateError("database_already_exists")
        blob_root_preexisting = blobs.exists()
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            if blobs.is_symlink() or (blobs.exists() and not blobs.is_dir()):
                raise ControlStoreStateError("blob_root_invalid")
            if blobs.exists() and any(blobs.iterdir()):
                raise ControlStoreStateError("blob_root_not_empty")
            blobs.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                database_path,
                isolation_level=None,
                check_same_thread=False,
            )
        except ControlStoreError:
            raise
        except (OSError, sqlite3.Error) as exc:
            cls._remove_database_files(database_path)
            if not blob_root_preexisting:
                try:
                    blobs.rmdir()
                except OSError:
                    pass
            raise ControlStoreStateError("store_path_unavailable") from exc
        try:
            configure_connection(connection)
            initialize_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO workspaces(workspace_id, revision) VALUES (?, 0)",
                (workspace_id,),
            )
            connection.commit()
        except Exception:
            connection.close()
            cls._remove_database_files(database_path)
            if not blob_root_preexisting:
                try:
                    blobs.rmdir()
                except OSError:
                    pass
            raise
        return cls(
            path=database_path,
            blob_root=blobs,
            connection=connection,
            workspace_id=workspace_id,
            clock=clock,
            failure_hook=_failure_hook,
        )

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        *,
        blob_root: str | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
        _failure_hook: _FailureHook | None = None,
    ) -> "SQLiteControlStore":
        database_path = cls._normalize_path(path, "database_path_invalid")
        if not database_path.is_file():
            raise ControlStoreStateError("database_not_found")
        blobs = cls._blob_root_for(database_path, blob_root)
        cls._validate_database_blob_separation(database_path, blobs)
        if blobs.is_symlink() or (blobs.exists() and not blobs.is_dir()):
            raise ControlStoreStateError("blob_root_invalid")
        try:
            connection = sqlite3.connect(
                database_path,
                isolation_level=None,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise ControlStoreStateError("database_open_failed") from exc
        try:
            configure_connection(connection)
            verify_schema(connection)
            workspace_rows = connection.execute(
                "SELECT workspace_id FROM workspaces ORDER BY workspace_id"
            ).fetchall()
            if len(workspace_rows) != 1:
                raise ControlStoreIntegrityError("workspace_binding_invalid")
            workspace_id = _validate_contract_id(
                workspace_rows[0][0],
                "workspace_id_invalid",
            )
            store = cls(
                path=database_path,
                blob_root=blobs,
                connection=connection,
                workspace_id=workspace_id,
                clock=clock,
                failure_hook=_failure_hook,
            )
            store._verify_all_payloads()
            return store
        except Exception:
            connection.close()
            raise

    @staticmethod
    def _normalize_path(
        path: str | os.PathLike[str],
        error_code: str,
    ) -> Path:
        try:
            value = Path(path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ControlStoreStateError(error_code) from exc
        return value

    @classmethod
    def _blob_root_for(
        cls,
        database_path: Path,
        blob_root: str | os.PathLike[str] | None,
    ) -> Path:
        if blob_root is None:
            return database_path.with_name(f"{database_path.name}.blobs")
        return cls._normalize_path(blob_root, "blob_root_invalid")

    @staticmethod
    def _validate_database_blob_separation(
        database_path: Path,
        blob_root: Path,
    ) -> None:
        if database_path == blob_root or database_path.is_relative_to(blob_root):
            raise ControlStoreStateError("database_blob_paths_overlap")

    @staticmethod
    def _remove_database_files(database_path: Path) -> None:
        for path in (
            database_path,
            database_path.with_name(f"{database_path.name}-wal"),
            database_path.with_name(f"{database_path.name}-shm"),
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def __enter__(self) -> "SQLiteControlStore":
        self._require_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise ControlStoreStateError("store_closed")

    def _inject(self, stage: str) -> None:
        if self._failure_hook is not None:
            self._failure_hook(stage)

    @property
    def current_revision(self) -> int:
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT revision FROM workspaces WHERE workspace_id = ?",
                (self.workspace_id,),
            ).fetchone()
            if row is None or type(row[0]) is not int or row[0] < 0:
                raise ControlStoreIntegrityError("workspace_revision_invalid")
            return int(row[0])

    def begin(
        self,
        run_id: str,
        transaction_id: str,
        transaction_type: str,
        expected_revision: int,
    ) -> "ControlUnitOfWork":
        self._require_open()
        run_id = _validate_contract_id(run_id, "transaction_identity_invalid")
        transaction_id = _validate_contract_id(
            transaction_id,
            "transaction_identity_invalid",
        )
        transaction_type = _validate_contract_id(
            transaction_type,
            "transaction_identity_invalid",
        )
        if type(expected_revision) is not int or expected_revision < 0:
            raise ControlStoreIntegrityError("expected_revision_invalid")
        from multi_agent_brief.control_store.uow import ControlUnitOfWork

        return ControlUnitOfWork(
            self,
            run_id=run_id,
            transaction_id=transaction_id,
            transaction_type=transaction_type,
            expected_revision=expected_revision,
        )

    def _existing_receipt(
        self,
        run_id: str,
        transaction_id: str,
        fingerprint: str,
    ) -> TransactionReceipt | None:
        row = self._connection.execute(
            """
            SELECT fingerprint, payload_json
            FROM transactions
            WHERE run_id = ? AND transaction_id = ?
            """,
            (run_id, transaction_id),
        ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise ControlStoreConflict("transaction_replay_conflict")
        receipt = decode_model(TransactionReceipt, str(row[1]))
        self._verify_transaction_relations(receipt)
        self._verify_receipt_blobs(receipt)
        return receipt

    def _commit_unit_of_work(self, uow: "ControlUnitOfWork") -> TransactionReceipt:
        # Freeze the validated identity at the commit linearization point. Every
        # replay lookup, fingerprint, receipt, and revision check below uses this
        # immutable snapshot rather than rereading caller-visible UoW state.
        identity = uow._identity_snapshot()
        run_id = _validate_contract_id(
            identity.run_id,
            "transaction_identity_invalid",
        )
        transaction_id = _validate_contract_id(
            identity.transaction_id,
            "transaction_identity_invalid",
        )
        transaction_type = _validate_contract_id(
            identity.transaction_type,
            "transaction_identity_invalid",
        )
        expected_revision = identity.expected_revision
        if type(expected_revision) is not int or expected_revision < 0:
            raise ControlStoreIntegrityError("expected_revision_invalid")
        fingerprint = uow._fingerprint(identity)
        with self._lock:
            self._require_open()
            prior = self._existing_receipt(
                run_id,
                transaction_id,
                fingerprint,
            )
            if prior is not None:
                return prior
            if self.current_revision != expected_revision:
                raise ControlStoreConflict("store_revision_conflict")
            if uow._run is None:
                existing_run = self._connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if existing_run is None:
                    raise ControlStoreConflict("run_not_found")
            self._inject("before_blob_write")
            for item in uow._artifact_revisions:
                self._write_blob(item.record, item.content)
            self._inject("after_blob_write")
            receipt: TransactionReceipt | None = None
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._inject("after_begin")
                replay = self._existing_receipt(
                    run_id,
                    transaction_id,
                    fingerprint,
                )
                if replay is not None:
                    self._connection.rollback()
                    return replay
                locked_revision = self._workspace_revision_in_transaction()
                if locked_revision != expected_revision:
                    raise ControlStoreConflict("store_revision_conflict")
                committed_revision = locked_revision + 1
                receipt = self._build_receipt(
                    uow,
                    identity,
                    committed_revision,
                )
                self._insert_run(uow._run)
                self._insert_transaction(receipt, self.workspace_id, fingerprint)
                self._upsert_stage_states(uow._stage_states.values())
                self._upsert_invocations(uow._invocations.values())
                self._upsert_artifacts(uow._artifacts.values())
                self._insert_artifact_revisions(uow._artifact_revisions)
                self._insert_events(uow._events)
                self._insert_approvals(uow._approvals.values())
                self._upsert_deliveries(uow._deliveries.values())
                self._insert_transaction_relations(receipt)
                self._inject("after_records")
                self._inject("before_commit")
                for item in uow._artifact_revisions:
                    self._verify_blob(item.record, self._blob_path(item.record.sha256))
                updated = self._connection.execute(
                    """
                    UPDATE workspaces SET revision = ?
                    WHERE workspace_id = ? AND revision = ?
                    """,
                    (committed_revision, self.workspace_id, locked_revision),
                )
                if updated.rowcount != 1:
                    raise ControlStoreConflict("store_revision_conflict")
                self._connection.commit()
            except sqlite3.IntegrityError as exc:
                self._connection.rollback()
                raise ControlStoreConflict("relational_integrity_conflict") from exc
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_write_failed") from exc
            except Exception:
                self._connection.rollback()
                raise
            if receipt is None:
                raise ControlStoreIntegrityError("transaction_receipt_missing")
            # Private test-only boundary for a real process exit after the durable
            # commit but before the caller observes the receipt.
            self._inject("after_commit")
            self._verify_committed_blob_bindings(run_id=run_id)
            return receipt

    def _workspace_revision_in_transaction(self) -> int:
        row = self._connection.execute(
            "SELECT revision FROM workspaces WHERE workspace_id = ?",
            (self.workspace_id,),
        ).fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0:
            raise ControlStoreIntegrityError("workspace_revision_invalid")
        return int(row[0])

    def _build_receipt(
        self,
        uow: "ControlUnitOfWork",
        identity: "_TransactionIdentity",
        committed_revision: int,
    ) -> TransactionReceipt:
        timestamp = self._clock()
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise ControlStoreStateError("store_clock_invalid")
        committed_at = timestamp.isoformat().replace("+00:00", "Z")
        try:
            return TransactionReceipt.model_validate(
                {
                    "schema_version": TransactionReceipt.schema_id,
                    "transaction_id": identity.transaction_id,
                    "run_id": identity.run_id,
                    "transaction_type": identity.transaction_type,
                    "prior_revision": identity.expected_revision,
                    "committed_revision": committed_revision,
                    "committed_at": committed_at,
                    "projection_status": "stale",
                    "event_ids": [event.event_id for event in uow._events],
                    "artifact_revisions": [
                        {
                            "artifact_id": item.record.artifact_id,
                            "revision": item.record.revision,
                        }
                        for item in uow._artifact_revisions
                    ],
                }
            )
        except ValueError as exc:
            raise ControlStoreIntegrityError("transaction_identity_invalid") from exc

    def _insert_run(self, record: RunIdentity | None) -> None:
        if record is None:
            return
        self._connection.execute(
            """
            INSERT INTO runs(
                run_id, workspace_id, schema_version, runtime, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.workspace_id,
                record.schema_version,
                record.runtime,
                record.created_at,
                canonical_model_text(record),
            ),
        )

    def _insert_transaction(
        self,
        receipt: TransactionReceipt,
        workspace_id: str,
        fingerprint: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO transactions(
                run_id, transaction_id, workspace_id, schema_version,
                transaction_type, prior_revision, committed_revision, committed_at,
                projection_status, fingerprint, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.run_id,
                receipt.transaction_id,
                workspace_id,
                receipt.schema_version,
                receipt.transaction_type,
                receipt.prior_revision,
                receipt.committed_revision,
                receipt.committed_at,
                receipt.projection_status,
                fingerprint,
                canonical_model_text(receipt),
            ),
        )

    def _upsert_stage_states(self, records: Iterable[StageState]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO stage_states(
                    run_id, stage_id, schema_version, status, revision,
                    updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, stage_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    status=excluded.status,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    record.run_id,
                    record.stage_id,
                    record.schema_version,
                    record.status,
                    record.revision,
                    record.updated_at,
                    canonical_model_text(record),
                ),
            )

    def _upsert_invocations(self, records: Iterable[Invocation]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO agent_invocations(
                    run_id, invocation_id, schema_version, role_id, runtime, status,
                    started_at, completed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, invocation_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    role_id=excluded.role_id,
                    runtime=excluded.runtime,
                    status=excluded.status,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    payload_json=excluded.payload_json
                """,
                (
                    record.run_id,
                    record.invocation_id,
                    record.schema_version,
                    record.role_id,
                    record.runtime,
                    record.status,
                    record.started_at,
                    record.completed_at,
                    canonical_model_text(record),
                ),
            )

    def _upsert_artifacts(self, records: Iterable[ArtifactRecord]) -> None:
        for record in records:
            revision_ref = record.current_revision or None
            self._connection.execute(
                """
                INSERT INTO artifacts(
                    run_id, artifact_id, schema_version, current_revision,
                    current_revision_ref, status, required, path, format, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, artifact_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    current_revision=excluded.current_revision,
                    current_revision_ref=excluded.current_revision_ref,
                    status=excluded.status,
                    required=excluded.required,
                    path=excluded.path,
                    format=excluded.format,
                    payload_json=excluded.payload_json
                """,
                (
                    record.run_id,
                    record.artifact_id,
                    record.schema_version,
                    record.current_revision,
                    revision_ref,
                    record.status,
                    int(record.required),
                    record.path,
                    record.format,
                    canonical_model_text(record),
                ),
            )

    def _insert_artifact_revisions(
        self,
        records: Iterable["_StagedArtifactRevision"],
    ) -> None:
        for item in records:
            record = item.record
            self._connection.execute(
                """
                INSERT INTO artifact_revisions(
                    run_id, artifact_id, revision, schema_version, path, sha256,
                    size_bytes, frozen, producer_kind, producer_id, created_at,
                    blob_relpath, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.artifact_id,
                    record.revision,
                    record.schema_version,
                    record.path,
                    record.sha256,
                    record.size_bytes,
                    int(record.frozen),
                    record.producer_kind,
                    record.producer_id,
                    record.created_at,
                    self._blob_relpath(record.sha256),
                    canonical_model_text(record),
                ),
            )

    def _insert_events(self, records: Iterable[EventEnvelope]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO events(
                    event_id, run_id, schema_version, event_type, created_at, actor,
                    transaction_id, stage_id, artifact_id, decision, reason,
                    metadata_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    record.run_id,
                    record.schema_version,
                    record.event_type,
                    record.created_at,
                    record.actor,
                    record.transaction_id,
                    record.stage_id,
                    record.artifact_id,
                    record.decision,
                    record.reason,
                    canonical_json_bytes(record.metadata).decode("utf-8"),
                    canonical_model_text(record),
                ),
            )

    def _insert_approvals(self, records: Iterable[Approval]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO approvals(
                    run_id, approval_id, schema_version, mode, role, decision,
                    reason, actor_id, recorded_at, boundary, event_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.approval_id,
                    record.schema_version,
                    record.mode,
                    record.role,
                    record.decision,
                    record.reason,
                    record.actor_id,
                    record.recorded_at,
                    record.boundary,
                    record.event_id,
                    canonical_model_text(record),
                ),
            )

    def _upsert_deliveries(self, records: Iterable[Delivery]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO deliveries(
                    run_id, delivery_id, schema_version, artifact_id,
                    artifact_revision, approval_id, status, target, channel,
                    created_at, completed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, delivery_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    artifact_id=excluded.artifact_id,
                    artifact_revision=excluded.artifact_revision,
                    approval_id=excluded.approval_id,
                    status=excluded.status,
                    target=excluded.target,
                    channel=excluded.channel,
                    created_at=excluded.created_at,
                    completed_at=excluded.completed_at,
                    payload_json=excluded.payload_json
                """,
                (
                    record.run_id,
                    record.delivery_id,
                    record.schema_version,
                    record.artifact_id,
                    record.artifact_revision,
                    record.approval_id,
                    record.status,
                    record.target,
                    record.channel,
                    record.created_at,
                    record.completed_at,
                    canonical_model_text(record),
                ),
            )

    def _insert_transaction_relations(self, receipt: TransactionReceipt) -> None:
        for position, event_id in enumerate(receipt.event_ids):
            self._connection.execute(
                """
                INSERT INTO transaction_events(
                    run_id, transaction_id, position, event_id
                ) VALUES (?, ?, ?, ?)
                """,
                (receipt.run_id, receipt.transaction_id, position, event_id),
            )
        for position, reference in enumerate(receipt.artifact_revisions):
            self._connection.execute(
                """
                INSERT INTO transaction_artifact_revisions(
                    run_id, transaction_id, position, artifact_id, revision
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.artifact_id,
                    reference.revision,
                ),
            )

    def _blob_relpath(self, sha256: str) -> str:
        return f"sha256/{sha256[:2]}/{sha256}"

    def _blob_path(self, sha256: str) -> Path:
        return self.blob_root.joinpath(*self._blob_relpath(sha256).split("/"))

    def _write_blob(self, record: ArtifactRevision, content: bytes) -> None:
        destination = self._blob_path(record.sha256)
        if destination.exists():
            self._verify_blob(record, destination)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if sha256_hex(temporary.read_bytes()) != record.sha256:
                raise ControlStoreIntegrityError("artifact_blob_hash_mismatch")
            os.replace(temporary, destination)
            if os.name != "nt":
                directory_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except ControlStoreError:
            raise
        except OSError as exc:
            raise ControlStoreIntegrityError("artifact_blob_write_failed") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        self._verify_blob(record, destination)

    def _verify_blob(self, record: ArtifactRevision, path: Path) -> None:
        try:
            if not path.is_file() or path.is_symlink():
                raise ControlStoreIntegrityError("committed_blob_missing")
            content = path.read_bytes()
        except OSError as exc:
            raise ControlStoreIntegrityError("committed_blob_unreadable") from exc
        if len(content) != record.size_bytes:
            raise ControlStoreIntegrityError("committed_blob_size_mismatch")
        if sha256_hex(content) != record.sha256:
            raise ControlStoreIntegrityError("committed_blob_hash_mismatch")

    def _verify_committed_blob_bindings(self, run_id: str | None = None) -> None:
        sql = (
            "SELECT run_id, sha256, blob_relpath, payload_json FROM artifact_revisions"
        )
        parameters: tuple[object, ...] = ()
        if run_id is not None:
            sql += " WHERE run_id = ?"
            parameters = (run_id,)
        sql += " ORDER BY run_id, artifact_id, revision"
        for row in self._connection.execute(sql, parameters).fetchall():
            record = decode_model(ArtifactRevision, str(row[3]))
            if row[0] != record.run_id or row[1] != record.sha256:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
            expected = self._blob_relpath(record.sha256)
            if row[2] != expected:
                raise ControlStoreIntegrityError("blob_binding_invalid")
            self._verify_blob(record, self._blob_path(record.sha256))

    def _verify_receipt_blobs(self, receipt: TransactionReceipt) -> None:
        for reference in receipt.artifact_revisions:
            row = self._connection.execute(
                """
                SELECT payload_json FROM artifact_revisions
                WHERE run_id = ? AND artifact_id = ? AND revision = ?
                """,
                (receipt.run_id, reference.artifact_id, reference.revision),
            ).fetchone()
            if row is None:
                raise ControlStoreIntegrityError("transaction_relation_mismatch")
            record = decode_model(ArtifactRevision, str(row[0]))
            self._verify_blob(record, self._blob_path(record.sha256))

    def _verify_all_payloads(self) -> None:
        verify_schema(self._connection)
        self._verify_committed_blob_bindings()
        run_ids = [
            str(row[0])
            for row in self._connection.execute(
                "SELECT run_id FROM runs ORDER BY run_id"
            ).fetchall()
        ]
        if not run_ids:
            _ = self.current_revision
            return
        for run_id in run_ids:
            self.load_snapshot(run_id)

    def load_snapshot(self, run_id: str) -> ControlStoreSnapshot:
        with self._lock:
            self._require_open()
            if type(run_id) is not str or not run_id:
                raise ControlStoreIntegrityError("run_id_invalid")
            try:
                self._connection.execute("BEGIN")
                snapshot = self._load_snapshot_in_transaction(run_id)
                self._connection.commit()
                return snapshot
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def _load_snapshot_in_transaction(self, run_id: str) -> ControlStoreSnapshot:
        verify_schema(self._connection)
        self._verify_committed_blob_bindings(run_id=run_id)
        run_rows = self._connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        if len(run_rows) != 1:
            raise ControlStoreStateError("run_not_found")
        run = self._decode_checked(
            RunIdentity,
            run_rows[0],
            {
                "run_id": "run_id",
                "workspace_id": "workspace_id",
                "schema_version": "schema_version",
                "runtime": "runtime",
                "created_at": "created_at",
            },
        )
        return ControlStoreSnapshot(
            workspace_id=self.workspace_id,
            store_revision=self.current_revision,
            run=run,
            stage_states=self._load_for_run(
                StageState,
                "stage_states",
                run_id,
                "stage_id",
                {
                    "run_id": "run_id",
                    "stage_id": "stage_id",
                    "schema_version": "schema_version",
                    "status": "status",
                    "revision": "revision",
                    "updated_at": "updated_at",
                },
            ),
            invocations=self._load_for_run(
                Invocation,
                "agent_invocations",
                run_id,
                "invocation_id",
                {
                    "run_id": "run_id",
                    "invocation_id": "invocation_id",
                    "schema_version": "schema_version",
                    "role_id": "role_id",
                    "runtime": "runtime",
                    "status": "status",
                    "started_at": "started_at",
                    "completed_at": "completed_at",
                },
            ),
            artifacts=self._load_for_run(
                ArtifactRecord,
                "artifacts",
                run_id,
                "artifact_id",
                {
                    "run_id": "run_id",
                    "artifact_id": "artifact_id",
                    "schema_version": "schema_version",
                    "current_revision": "current_revision",
                    "status": "status",
                    "required": "required",
                    "path": "path",
                    "format": "format",
                },
            ),
            artifact_revisions=self._load_for_run(
                ArtifactRevision,
                "artifact_revisions",
                run_id,
                "artifact_id, revision",
                {
                    "run_id": "run_id",
                    "artifact_id": "artifact_id",
                    "revision": "revision",
                    "schema_version": "schema_version",
                    "path": "path",
                    "sha256": "sha256",
                    "size_bytes": "size_bytes",
                    "frozen": "frozen",
                    "producer_kind": "producer_kind",
                    "producer_id": "producer_id",
                    "created_at": "created_at",
                },
            ),
            events=self._load_for_run(
                EventEnvelope,
                "events",
                run_id,
                "created_at, event_id",
                {
                    "run_id": "run_id",
                    "event_id": "event_id",
                    "schema_version": "schema_version",
                    "event_type": "event_type",
                    "created_at": "created_at",
                    "actor": "actor",
                    "transaction_id": "transaction_id",
                    "stage_id": "stage_id",
                    "artifact_id": "artifact_id",
                    "decision": "decision",
                    "reason": "reason",
                },
            ),
            approvals=self._load_for_run(
                Approval,
                "approvals",
                run_id,
                "recorded_at, approval_id",
                {
                    "run_id": "run_id",
                    "approval_id": "approval_id",
                    "schema_version": "schema_version",
                    "mode": "mode",
                    "role": "role",
                    "decision": "decision",
                    "reason": "reason",
                    "actor_id": "actor_id",
                    "recorded_at": "recorded_at",
                    "boundary": "boundary",
                    "event_id": "event_id",
                },
            ),
            deliveries=self._load_for_run(
                Delivery,
                "deliveries",
                run_id,
                "delivery_id",
                {
                    "run_id": "run_id",
                    "delivery_id": "delivery_id",
                    "schema_version": "schema_version",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "approval_id": "approval_id",
                    "status": "status",
                    "target": "target",
                    "channel": "channel",
                    "created_at": "created_at",
                    "completed_at": "completed_at",
                },
            ),
            transactions=self._load_transactions(run_id),
        )

    def _load_for_run(
        self,
        model_type: type[_ModelT],
        table: str,
        run_id: str,
        order_by: str,
        columns: dict[str, str],
    ) -> tuple[_ModelT, ...]:
        # Table and ordering values are closed internal constants above.
        rows = self._connection.execute(
            f"SELECT * FROM {table} WHERE run_id = ? ORDER BY {order_by}",
            (run_id,),
        ).fetchall()
        return tuple(self._decode_checked(model_type, row, columns) for row in rows)

    def _decode_checked(
        self,
        model_type: type[_ModelT],
        row: sqlite3.Row,
        columns: dict[str, str],
    ) -> _ModelT:
        model = decode_model(model_type, str(row["payload_json"]))
        for column, attribute in columns.items():
            stored = row[column]
            expected = getattr(model, attribute)
            if type(expected) is bool:
                expected = int(expected)
            if stored != expected:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        if model_type is EventEnvelope:
            metadata_text = canonical_json_bytes(model.metadata).decode("utf-8")
            if row["metadata_json"] != metadata_text:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        return model

    def _load_transactions(self, run_id: str) -> tuple[TransactionReceipt, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM transactions
            WHERE run_id = ? ORDER BY committed_revision, transaction_id
            """,
            (run_id,),
        ).fetchall()
        receipts: list[TransactionReceipt] = []
        for row in rows:
            receipt = self._decode_checked(
                TransactionReceipt,
                row,
                {
                    "run_id": "run_id",
                    "transaction_id": "transaction_id",
                    "schema_version": "schema_version",
                    "transaction_type": "transaction_type",
                    "prior_revision": "prior_revision",
                    "committed_revision": "committed_revision",
                    "committed_at": "committed_at",
                    "projection_status": "projection_status",
                },
            )
            self._verify_transaction_relations(receipt)
            receipts.append(receipt)
        return tuple(receipts)

    def _verify_transaction_relations(self, receipt: TransactionReceipt) -> None:
        event_ids = [
            str(row[0])
            for row in self._connection.execute(
                """
                SELECT event_id FROM transaction_events
                WHERE run_id = ? AND transaction_id = ? ORDER BY position
                """,
                (receipt.run_id, receipt.transaction_id),
            ).fetchall()
        ]
        revision_refs = [
            ArtifactRevisionReference.model_validate(
                {"artifact_id": row[0], "revision": row[1]}
            )
            for row in self._connection.execute(
                """
                SELECT artifact_id, revision FROM transaction_artifact_revisions
                WHERE run_id = ? AND transaction_id = ? ORDER BY position
                """,
                (receipt.run_id, receipt.transaction_id),
            ).fetchall()
        ]
        if (
            event_ids != receipt.event_ids
            or revision_refs != receipt.artifact_revisions
        ):
            raise ControlStoreIntegrityError("transaction_relation_mismatch")

    def scan_orphans(self) -> OrphanBlobScan:
        with self._lock:
            self._require_open()
            referenced = {
                str(row[0])
                for row in self._connection.execute(
                    "SELECT sha256 FROM artifact_revisions"
                ).fetchall()
            }
            found: set[str] = set()
            malformed: list[str] = []
            try:
                if self.blob_root.exists():
                    for path in sorted(self.blob_root.rglob("*")):
                        if not path.is_file():
                            continue
                        relative = path.relative_to(self.blob_root).as_posix()
                        parts = relative.split("/")
                        if (
                            len(parts) == 3
                            and parts[0] == "sha256"
                            and len(parts[1]) == 2
                            and len(parts[2]) == 64
                            and parts[1] == parts[2][:2]
                            and all(char in "0123456789abcdef" for char in parts[2])
                        ):
                            found.add(parts[2])
                        else:
                            malformed.append(relative)
            except OSError as exc:
                raise ControlStoreIntegrityError("orphan_scan_failed") from exc
            return OrphanBlobScan(
                orphan_hashes=tuple(sorted(found - referenced)),
                malformed_paths=tuple(malformed),
            )

    def backup_to(self, destination: str | os.PathLike[str]) -> Path:
        from multi_agent_brief.control_store.backup import backup_store

        with self._lock:
            self._require_open()
            return backup_store(self, destination)

    @classmethod
    def restore_to_new_path(
        cls,
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        blob_root: str | os.PathLike[str] | None = None,
    ) -> "SQLiteControlStore":
        from multi_agent_brief.control_store.backup import restore_store

        return restore_store(cls, source, destination, blob_root=blob_root)


if TYPE_CHECKING:
    from multi_agent_brief.control_store.uow import (
        ControlUnitOfWork,
        _StagedArtifactRevision,
        _TransactionIdentity,
    )


__all__ = ["ControlStoreSnapshot", "OrphanBlobScan", "SQLiteControlStore"]
