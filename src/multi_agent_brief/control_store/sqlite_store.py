"""Typed SQLite ControlStore substrate with no current runtime authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import stat
import threading
from typing import TYPE_CHECKING, Callable, Iterable, TypeVar, cast
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from multi_agent_brief.contracts.v2 import (
    AcceptedProposalRecord,
    AcceptedSourceRecord,
    Approval,
    ArtifactRecord,
    ArtifactRevision,
    ArtifactRevisionReference,
    ContractId,
    Delivery,
    EventEnvelope,
    Invocation,
    ProposalSourceBinding,
    RunIdentity,
    StageState,
    StrictModel,
    TransactionReceipt,
    WorkspaceRunHead,
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
_PR3_RECORD_MODELS = (
    WorkspaceRunHead,
    AcceptedSourceRecord,
    AcceptedProposalRecord,
    ProposalSourceBinding,
)


def _canonical_record_text(record: StrictModel) -> str:
    if type(record) not in _PR3_RECORD_MODELS:
        return canonical_model_text(record)
    payload = record.model_dump(mode="json", exclude_unset=False)
    return canonical_json_bytes(payload).decode("utf-8")


def _decode_record(model_type: type[_ModelT], payload_text: str) -> _ModelT:
    if model_type not in _PR3_RECORD_MODELS:
        return decode_model(model_type, payload_text)
    try:
        model = model_type.model_validate_json(payload_text, strict=True)
    except (ValidationError, ValueError) as exc:
        raise ControlStoreIntegrityError("stored_payload_invalid") from exc
    if _canonical_record_text(model) != payload_text:
        raise ControlStoreIntegrityError("stored_payload_not_canonical")
    return model


def _validate_contract_id(value: object, error_code: str) -> str:
    """Reuse the PR-1 ContractId vocabulary without copying its grammar."""

    try:
        return _CONTRACT_ID_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise ControlStoreIntegrityError(error_code) from exc


def _validate_blob_topology(
    blob_root: Path,
    *,
    error_code: str,
    blob_path: Path | None = None,
    allow_missing_directories: bool = False,
    require_blob: bool = False,
    missing_blob_error_code: str | None = None,
) -> tuple[Path, ...]:
    """Validate one lexical, non-symlink blob tree without following links."""

    def fail(exc: BaseException | None = None) -> None:
        if exc is None:
            raise ControlStoreIntegrityError(error_code)
        raise ControlStoreIntegrityError(error_code) from exc

    def require_real_directory(path: Path, *, allow_missing: bool = False) -> bool:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            if allow_missing:
                return False
            if require_blob and blob_path is not None:
                raise ControlStoreIntegrityError(
                    missing_blob_error_code or error_code
                )
            fail()
        except OSError as exc:
            fail(exc)
        if not stat.S_ISDIR(mode):
            fail()
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            fail(exc)
        if not resolved.is_relative_to(root_resolved):
            fail()
        return True

    try:
        root_mode = blob_root.lstat().st_mode
    except OSError as exc:
        fail(exc)
    if not stat.S_ISDIR(root_mode):
        fail()
    try:
        root_resolved = blob_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        fail(exc)
    if os.path.normcase(str(root_resolved)) != os.path.normcase(str(blob_root)):
        fail()

    hash_root = blob_root / "sha256"
    if blob_path is not None:
        try:
            relative = blob_path.relative_to(blob_root)
        except ValueError:
            fail()
        parts = relative.parts
        if (
            len(parts) != 3
            or parts[0] != "sha256"
            or len(parts[1]) != 2
            or len(parts[2]) != 64
            or parts[1] != parts[2][:2]
            or any(char not in "0123456789abcdef" for char in parts[2])
        ):
            fail()
        if not require_real_directory(
            hash_root,
            allow_missing=allow_missing_directories,
        ):
            return ()
        prefix = hash_root / parts[1]
        if not require_real_directory(
            prefix,
            allow_missing=allow_missing_directories,
        ):
            return ()
        try:
            mode = blob_path.lstat().st_mode
        except FileNotFoundError:
            if require_blob:
                raise ControlStoreIntegrityError(
                    missing_blob_error_code or error_code
                )
            return ()
        except OSError as exc:
            fail(exc)
        if not stat.S_ISREG(mode):
            fail()
        try:
            resolved_blob = blob_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            fail(exc)
        if not resolved_blob.is_relative_to(root_resolved):
            fail()
        return (blob_path,)

    files: list[Path] = []
    try:
        with os.scandir(blob_root) as root_entries:
            for root_entry in root_entries:
                if root_entry.name == "sha256":
                    continue
                if root_entry.is_symlink() or not root_entry.is_file(
                    follow_symlinks=False
                ):
                    fail()
                files.append(Path(root_entry.path))
    except OSError as exc:
        fail(exc)
    if not require_real_directory(hash_root, allow_missing=True):
        return tuple(sorted(files, key=lambda path: path.as_posix()))
    try:
        with os.scandir(hash_root) as prefixes:
            for prefix_entry in prefixes:
                if prefix_entry.is_symlink() or not prefix_entry.is_dir(
                    follow_symlinks=False
                ):
                    fail()
                prefix = Path(prefix_entry.path)
                require_real_directory(prefix)
                with os.scandir(prefix) as blobs:
                    for blob_entry in blobs:
                        if blob_entry.is_symlink() or not blob_entry.is_file(
                            follow_symlinks=False
                        ):
                            fail()
                        path = Path(blob_entry.path)
                        try:
                            resolved_blob = path.resolve(strict=True)
                        except (OSError, RuntimeError) as exc:
                            fail(exc)
                        if not resolved_blob.is_relative_to(root_resolved):
                            fail()
                        files.append(path)
    except OSError as exc:
        fail(exc)
    return tuple(sorted(files, key=lambda path: path.as_posix()))


@dataclass(frozen=True)
class ControlStoreSnapshot:
    """One immutable typed view of a run at the store's current revision."""

    workspace_id: str
    store_revision: int
    run: RunIdentity
    workspace_run_head: WorkspaceRunHead | None
    stage_states: tuple[StageState, ...]
    invocations: tuple[Invocation, ...]
    artifacts: tuple[ArtifactRecord, ...]
    artifact_revisions: tuple[ArtifactRevision, ...]
    events: tuple[EventEnvelope, ...]
    approvals: tuple[Approval, ...]
    deliveries: tuple[Delivery, ...]
    sources: tuple[AcceptedSourceRecord, ...]
    accepted_proposals: tuple[AcceptedProposalRecord, ...]
    proposal_source_bindings: tuple[ProposalSourceBinding, ...]
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
            _validate_blob_topology(
                blobs,
                error_code="blob_topology_invalid",
            )
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
            _validate_blob_topology(
                blobs,
                error_code="blob_topology_invalid",
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
        try:
            lexical_root = Path(blob_root).expanduser()
            if lexical_root.is_symlink():
                raise ControlStoreStateError("blob_root_invalid")
        except ControlStoreError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ControlStoreStateError("blob_root_invalid") from exc
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
        receipt = _decode_record(TransactionReceipt, str(row[1]))
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
            # Exact replay and new work both require a complete trusted
            # baseline. This read transaction finishes before any new blob is
            # written, so pre-existing ledger corruption cannot create another
            # orphan or be mistaken for a successful replay.
            prior = self._verify_baseline_and_existing_receipt(
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
            self._preflight_artifact_subgraph(uow, run_id)
            self._preflight_intake_subgraph(uow, run_id)
            self._inject("before_blob_write")
            for position, item in enumerate(uow._artifact_revisions, start=1):
                self._inject(f"before_blob_write:{position}")
                self._write_blob(item.record, item.content)
                self._inject(f"after_blob_write:{position}")
            self._inject("after_blob_write")
            receipt: TransactionReceipt | None = None
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._inject("after_begin")
                # Another connection may have committed after the first read
                # snapshot and before this write transaction. Recheck the
                # accepted baseline before an exact replay can return.
                verify_schema(self._connection)
                self._verify_committed_blob_bindings()
                self._verify_workspace_ledger_graph()
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
                self._upsert_workspace_run_head(uow._workspace_run_head)
                self._upsert_stage_states(uow._stage_states.values())
                self._upsert_invocations(uow._invocations.values())
                self._upsert_artifacts(uow._artifacts.values())
                self._insert_artifact_revisions(uow._artifact_revisions)
                self._insert_events(uow._events)
                self._insert_approvals(uow._approvals.values())
                self._upsert_deliveries(uow._deliveries.values())
                self._insert_sources(uow._sources.values())
                self._insert_accepted_proposals(uow._accepted_proposals.values())
                self._insert_proposal_source_bindings(
                    uow._proposal_source_bindings.values()
                )
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
                # Validate the proposed graph while all inserted rows and the
                # workspace revision remain rollback-capable in this same
                # SQLite write transaction.
                self._verify_workspace_ledger_graph()
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

    def _preflight_artifact_subgraph(
        self,
        uow: "ControlUnitOfWork",
        run_id: str,
    ) -> None:
        """Reject deterministically unbound blob records before file writes."""

        staged_artifact_ids = set(uow._artifacts)
        staged_revision_keys = {
            (item.record.artifact_id, item.record.revision)
            for item in uow._artifact_revisions
        }
        existing_artifact_ids = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT artifact_id FROM artifacts WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        for artifact_id, revision in staged_revision_keys:
            if artifact_id not in staged_artifact_ids | existing_artifact_ids:
                raise ControlStoreConflict("relational_integrity_conflict")
            if self._connection.execute(
                """
                SELECT 1 FROM artifact_revisions
                WHERE run_id = ? AND artifact_id = ? AND revision = ?
                """,
                (run_id, artifact_id, revision),
            ).fetchone() is not None:
                # Exact transaction replay returned before this preflight. Any
                # remaining revision-key collision belongs to different intent.
                raise ControlStoreConflict("relational_integrity_conflict")
        for record in uow._artifacts.values():
            if record.current_revision == 0:
                continue
            key = (record.artifact_id, record.current_revision)
            if key in staged_revision_keys:
                continue
            if self._connection.execute(
                """
                SELECT 1 FROM artifact_revisions
                WHERE run_id = ? AND artifact_id = ? AND revision = ?
                """,
                (run_id, record.artifact_id, record.current_revision),
            ).fetchone() is None:
                raise ControlStoreConflict("relational_integrity_conflict")

    def _workspace_revision_in_transaction(self) -> int:
        row = self._connection.execute(
            "SELECT revision FROM workspaces WHERE workspace_id = ?",
            (self.workspace_id,),
        ).fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0:
            raise ControlStoreIntegrityError("workspace_revision_invalid")
        return int(row[0])

    def _preflight_intake_subgraph(
        self,
        uow: "ControlUnitOfWork",
        run_id: str,
    ) -> None:
        """Reject known missing intake relations before any blob promotion."""

        staged_invocations = set(uow._invocations)
        existing_invocations = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT invocation_id FROM agent_invocations WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        staged_events = {event.event_id for event in uow._events}
        staged_revisions = {
            (item.record.artifact_id, item.record.revision)
            for item in uow._artifact_revisions
        }
        existing_revisions = {
            (str(row[0]), int(row[1]))
            for row in self._connection.execute(
                """
                SELECT artifact_id, revision FROM artifact_revisions
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()
        }
        staged_sources = set(uow._sources)
        existing_sources = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT source_id FROM sources WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        staged_proposals = set(uow._accepted_proposals)
        existing_proposals = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT proposal_id FROM accepted_proposals WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        available_invocations = staged_invocations | existing_invocations
        available_revisions = staged_revisions | existing_revisions
        available_sources = staged_sources | existing_sources
        available_proposals = staged_proposals | existing_proposals

        for source in uow._sources.values():
            required_revisions = {
                (source.content_artifact_id, source.content_artifact_revision)
            }
            if source.raw_payload_artifact_id is not None:
                required_revisions.add(
                    (
                        source.raw_payload_artifact_id,
                        source.raw_payload_artifact_revision,
                    )
                )
            if (
                source.invocation_id not in available_invocations
                or source.acquisition_event_id not in staged_events
                or source.accepted_transaction_id != uow.transaction_id
                or not required_revisions <= available_revisions
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        for proposal in uow._accepted_proposals.values():
            if (
                proposal.invocation_id not in available_invocations
                or proposal.accepted_event_id not in staged_events
                or proposal.accepted_transaction_id != uow.transaction_id
                or (proposal.artifact_id, proposal.artifact_revision)
                not in available_revisions
                or (
                    proposal.parent_proposal_id is not None
                    and proposal.parent_proposal_id not in available_proposals
                )
                or (
                    proposal.target_artifact_id is not None
                    and (
                        proposal.target_artifact_id,
                        proposal.target_artifact_revision,
                    )
                    not in available_revisions
                )
                or not set(proposal.source_ids) <= available_sources
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        binding_keys = {
            (record.proposal_id, record.source_id)
            for record in uow._proposal_source_bindings.values()
        }
        expected_binding_keys = {
            (proposal.proposal_id, source_id)
            for proposal in uow._accepted_proposals.values()
            for source_id in proposal.source_ids
        }
        if binding_keys != expected_binding_keys:
            raise ControlStoreConflict("relational_integrity_conflict")
        if any(
            proposal_id not in available_proposals or source_id not in available_sources
            for proposal_id, source_id in binding_keys
        ):
            raise ControlStoreConflict("relational_integrity_conflict")

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
                    "source_ids": list(uow._sources),
                    "proposal_ids": list(uow._accepted_proposals),
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

    def _upsert_workspace_run_head(
        self,
        record: WorkspaceRunHead | None,
    ) -> None:
        if record is None:
            return
        self._connection.execute(
            """
            INSERT INTO workspace_run_heads(
                workspace_id, schema_version, current_run_id, updated_at, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                current_run_id=excluded.current_run_id,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                record.workspace_id,
                record.schema_version,
                record.current_run_id,
                record.updated_at,
                _canonical_record_text(record),
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
                    started_at, completed_at, failure_reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, invocation_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    role_id=excluded.role_id,
                    runtime=excluded.runtime,
                    status=excluded.status,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    failure_reason=excluded.failure_reason,
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
                    record.failure_reason,
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

    def _insert_sources(self, records: Iterable[AcceptedSourceRecord]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO sources(
                    run_id, source_id, schema_version, origin_type,
                    acquisition_method, material_kind, provider, locator_json,
                    title, publisher, published_at, retrieved_at, source_category,
                    retrieval_source_type, underlying_evidence_type,
                    raw_underlying_evidence_type, content_sha256,
                    content_size_bytes, content_media_type, content_blob_path,
                    content_artifact_id, content_artifact_revision,
                    raw_payload_sha256, raw_payload_size_bytes,
                    raw_payload_media_type, raw_payload_blob_path,
                    raw_payload_artifact_id, raw_payload_artifact_revision,
                    claims_eligible, eligibility_reason, invocation_id,
                    acquisition_event_id, accepted_transaction_id,
                    request_fingerprint, created_at, payload_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    record.run_id,
                    record.source_id,
                    record.schema_version,
                    record.origin_type,
                    record.acquisition_method,
                    record.material_kind,
                    record.provider,
                    canonical_json_bytes(record.locator.model_dump(mode="json")).decode(
                        "utf-8"
                    ),
                    record.title,
                    record.publisher,
                    record.published_at,
                    record.retrieved_at,
                    record.source_category,
                    record.retrieval_source_type,
                    record.underlying_evidence_type,
                    record.raw_underlying_evidence_type,
                    record.content_sha256,
                    record.content_size_bytes,
                    record.content_media_type,
                    record.content_blob_path,
                    record.content_artifact_id,
                    record.content_artifact_revision,
                    record.raw_payload_sha256,
                    record.raw_payload_size_bytes,
                    record.raw_payload_media_type,
                    record.raw_payload_blob_path,
                    record.raw_payload_artifact_id,
                    record.raw_payload_artifact_revision,
                    int(record.claims_eligible),
                    record.eligibility_reason,
                    record.invocation_id,
                    record.acquisition_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    record.created_at,
                    _canonical_record_text(record),
                ),
            )

    def _insert_accepted_proposals(
        self,
        records: Iterable[AcceptedProposalRecord],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO accepted_proposals(
                    run_id, proposal_id, schema_version, proposal_kind, artifact_id,
                    artifact_revision, proposal_sha256, invocation_id,
                    owner_stage_id, owner_role_id, parent_proposal_id,
                    target_artifact_id, target_artifact_revision, source_ids_json,
                    accepted_event_id, accepted_transaction_id,
                    request_fingerprint, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.proposal_id,
                    record.schema_version,
                    record.proposal_kind,
                    record.artifact_id,
                    record.artifact_revision,
                    record.proposal_sha256,
                    record.invocation_id,
                    record.owner_stage_id,
                    record.owner_role_id,
                    record.parent_proposal_id,
                    record.target_artifact_id,
                    record.target_artifact_revision,
                    canonical_json_bytes(record.source_ids).decode("utf-8"),
                    record.accepted_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    record.created_at,
                    _canonical_record_text(record),
                ),
            )

    def _insert_proposal_source_bindings(
        self,
        records: Iterable[ProposalSourceBinding],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO proposal_source_bindings(
                    run_id, proposal_id, source_id, schema_version, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.proposal_id,
                    record.source_id,
                    record.schema_version,
                    _canonical_record_text(record),
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
        for position, source_id in enumerate(receipt.source_ids):
            self._connection.execute(
                """
                INSERT INTO transaction_sources(
                    run_id, transaction_id, position, source_id
                ) VALUES (?, ?, ?, ?)
                """,
                (receipt.run_id, receipt.transaction_id, position, source_id),
            )
        for position, proposal_id in enumerate(receipt.proposal_ids):
            self._connection.execute(
                """
                INSERT INTO transaction_proposals(
                    run_id, transaction_id, position, proposal_id
                ) VALUES (?, ?, ?, ?)
                """,
                (receipt.run_id, receipt.transaction_id, position, proposal_id),
            )

    def _blob_relpath(self, sha256: str) -> str:
        return f"sha256/{sha256[:2]}/{sha256}"

    def _blob_path(self, sha256: str) -> Path:
        return self.blob_root.joinpath(*self._blob_relpath(sha256).split("/"))

    def _workspace_blob_path(self, sha256: str) -> str:
        # PR-3's fresh workspace contract fixes the logical accepted-byte path.
        # Backup/restore may use a different physical blob root while retaining
        # the same immutable workspace-relative record.
        return f"briefloop.db.blobs/{self._blob_relpath(sha256)}"

    def _write_blob(self, record: ArtifactRevision, content: bytes) -> None:
        destination = self._blob_path(record.sha256)
        existing = _validate_blob_topology(
            self.blob_root,
            error_code="blob_topology_invalid",
            blob_path=destination,
            allow_missing_directories=True,
        )
        if existing:
            self._verify_blob(record, destination)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        _validate_blob_topology(
            self.blob_root,
            error_code="blob_topology_invalid",
            blob_path=destination,
        )
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
        _validate_blob_topology(
            self.blob_root,
            error_code="blob_topology_invalid",
            blob_path=path,
            require_blob=True,
            missing_blob_error_code="committed_blob_missing",
        )
        try:
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

    def _verify_baseline_and_existing_receipt(
        self,
        run_id: str,
        transaction_id: str,
        fingerprint: str,
    ) -> TransactionReceipt | None:
        try:
            self._connection.execute("BEGIN")
            self._verify_all_payloads_in_transaction()
            receipt = self._existing_receipt(run_id, transaction_id, fingerprint)
            self._connection.commit()
            return receipt
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise ControlStoreIntegrityError("sqlite_read_failed") from exc
        except Exception:
            self._connection.rollback()
            raise

    def _verify_all_payloads(self) -> None:
        try:
            self._connection.execute("BEGIN")
            self._verify_all_payloads_in_transaction()
            self._connection.commit()
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise ControlStoreIntegrityError("sqlite_read_failed") from exc
        except Exception:
            self._connection.rollback()
            raise

    def _verify_all_payloads_in_transaction(self) -> None:
        verify_schema(self._connection)
        self._verify_committed_blob_bindings()
        self._verify_workspace_ledger_graph()
        run_ids = [
            str(row[0])
            for row in self._connection.execute(
                "SELECT run_id FROM runs ORDER BY run_id"
            ).fetchall()
        ]
        for run_id in run_ids:
            self._load_snapshot_in_transaction(run_id)

    def load_snapshot(self, run_id: str) -> ControlStoreSnapshot:
        with self._lock:
            self._require_open()
            if type(run_id) is not str or not run_id:
                raise ControlStoreIntegrityError("run_id_invalid")
            try:
                self._connection.execute("BEGIN")
                verify_schema(self._connection)
                self._verify_committed_blob_bindings()
                self._verify_workspace_ledger_graph()
                snapshot = self._load_snapshot_in_transaction(run_id)
                self._connection.commit()
                return snapshot
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def load_workspace_run_head(self) -> WorkspaceRunHead | None:
        """Return the explicit workspace head after full Store verification."""

        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN")
                self._verify_all_payloads_in_transaction()
                head = self._load_workspace_run_head_in_transaction()
                self._connection.commit()
                return head
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def load_transaction_receipt(
        self,
        run_id: str,
        transaction_id: str,
    ) -> TransactionReceipt | None:
        """Load one receipt without inferring a current run or replay intent."""

        run_id = _validate_contract_id(run_id, "transaction_identity_invalid")
        transaction_id = _validate_contract_id(
            transaction_id,
            "transaction_identity_invalid",
        )
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN")
                self._verify_all_payloads_in_transaction()
                row = self._connection.execute(
                    """
                    SELECT * FROM transactions
                    WHERE run_id = ? AND transaction_id = ?
                    """,
                    (run_id, transaction_id),
                ).fetchone()
                receipt = None if row is None else self._decode_transaction_row(row)
                if receipt is not None:
                    self._verify_transaction_relations(receipt)
                    self._verify_receipt_blobs(receipt)
                self._connection.commit()
                return receipt
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def find_invocation_run_ids(self, invocation_id: str) -> tuple[str, ...]:
        """Return exact run bindings for one invocation after Store verification."""

        invocation_id = _validate_contract_id(
            invocation_id,
            "invocation_identity_invalid",
        )
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN")
                self._verify_all_payloads_in_transaction()
                rows = self._connection.execute(
                    """
                    SELECT run_id FROM agent_invocations
                    WHERE invocation_id = ? ORDER BY run_id
                    """,
                    (invocation_id,),
                ).fetchall()
                run_ids = tuple(str(row[0]) for row in rows)
                self._connection.commit()
                return run_ids
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def read_artifact_revision_bytes(
        self,
        run_id: str,
        artifact_id: str,
        revision: int,
    ) -> bytes:
        """Read bytes only through one verified artifact-revision binding."""

        run_id = _validate_contract_id(run_id, "artifact_identity_invalid")
        artifact_id = _validate_contract_id(
            artifact_id,
            "artifact_identity_invalid",
        )
        if type(revision) is not int or revision <= 0:
            raise ControlStoreIntegrityError("artifact_identity_invalid")
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN")
                self._verify_all_payloads_in_transaction()
                row = self._connection.execute(
                    """
                    SELECT * FROM artifact_revisions
                    WHERE run_id = ? AND artifact_id = ? AND revision = ?
                    """,
                    (run_id, artifact_id, revision),
                ).fetchone()
                if row is None:
                    raise ControlStoreStateError("artifact_revision_not_found")
                record = self._decode_checked(
                    ArtifactRevision,
                    row,
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
                )
                path = self._blob_path(record.sha256)
                self._verify_blob(record, path)
                try:
                    content = path.read_bytes()
                except OSError as exc:
                    raise ControlStoreIntegrityError("blob_read_failed") from exc
                self._connection.commit()
                return content
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def _load_snapshot_in_transaction(self, run_id: str) -> ControlStoreSnapshot:
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
            workspace_run_head=self._load_workspace_run_head_in_transaction(),
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
                    "failure_reason": "failure_reason",
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
            sources=self._load_for_run(
                AcceptedSourceRecord,
                "sources",
                run_id,
                "source_id",
                {
                    "run_id": "run_id",
                    "source_id": "source_id",
                    "schema_version": "schema_version",
                    "origin_type": "origin_type",
                    "acquisition_method": "acquisition_method",
                    "material_kind": "material_kind",
                    "provider": "provider",
                    "title": "title",
                    "publisher": "publisher",
                    "published_at": "published_at",
                    "retrieved_at": "retrieved_at",
                    "source_category": "source_category",
                    "retrieval_source_type": "retrieval_source_type",
                    "underlying_evidence_type": "underlying_evidence_type",
                    "raw_underlying_evidence_type": (
                        "raw_underlying_evidence_type"
                    ),
                    "content_sha256": "content_sha256",
                    "content_size_bytes": "content_size_bytes",
                    "content_media_type": "content_media_type",
                    "content_blob_path": "content_blob_path",
                    "content_artifact_id": "content_artifact_id",
                    "content_artifact_revision": "content_artifact_revision",
                    "raw_payload_sha256": "raw_payload_sha256",
                    "raw_payload_size_bytes": "raw_payload_size_bytes",
                    "raw_payload_media_type": "raw_payload_media_type",
                    "raw_payload_blob_path": "raw_payload_blob_path",
                    "raw_payload_artifact_id": "raw_payload_artifact_id",
                    "raw_payload_artifact_revision": (
                        "raw_payload_artifact_revision"
                    ),
                    "claims_eligible": "claims_eligible",
                    "eligibility_reason": "eligibility_reason",
                    "invocation_id": "invocation_id",
                    "acquisition_event_id": "acquisition_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                    "created_at": "created_at",
                },
            ),
            accepted_proposals=self._load_for_run(
                AcceptedProposalRecord,
                "accepted_proposals",
                run_id,
                "proposal_id",
                {
                    "run_id": "run_id",
                    "proposal_id": "proposal_id",
                    "schema_version": "schema_version",
                    "proposal_kind": "proposal_kind",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "proposal_sha256": "proposal_sha256",
                    "invocation_id": "invocation_id",
                    "owner_stage_id": "owner_stage_id",
                    "owner_role_id": "owner_role_id",
                    "parent_proposal_id": "parent_proposal_id",
                    "target_artifact_id": "target_artifact_id",
                    "target_artifact_revision": "target_artifact_revision",
                    "accepted_event_id": "accepted_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                    "created_at": "created_at",
                },
            ),
            proposal_source_bindings=self._load_for_run(
                ProposalSourceBinding,
                "proposal_source_bindings",
                run_id,
                "proposal_id, source_id",
                {
                    "run_id": "run_id",
                    "proposal_id": "proposal_id",
                    "source_id": "source_id",
                    "schema_version": "schema_version",
                },
            ),
            transactions=self._load_transactions(run_id),
        )

    def _load_workspace_run_head_in_transaction(self) -> WorkspaceRunHead | None:
        rows = self._connection.execute(
            "SELECT * FROM workspace_run_heads WHERE workspace_id = ?",
            (self.workspace_id,),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ControlStoreIntegrityError("workspace_run_head_invalid")
        return self._decode_checked(
            WorkspaceRunHead,
            rows[0],
            {
                "workspace_id": "workspace_id",
                "schema_version": "schema_version",
                "current_run_id": "current_run_id",
                "updated_at": "updated_at",
            },
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
        model = _decode_record(model_type, str(row["payload_json"]))
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
        elif model_type is AcceptedSourceRecord:
            locator_text = canonical_json_bytes(
                model.locator.model_dump(mode="json")
            ).decode("utf-8")
            if row["locator_json"] != locator_text:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        elif model_type is AcceptedProposalRecord:
            source_ids_text = canonical_json_bytes(model.source_ids).decode("utf-8")
            if row["source_ids_json"] != source_ids_text:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        return model

    def _decode_source_row(self, row: sqlite3.Row) -> AcceptedSourceRecord:
        return self._decode_checked(
            AcceptedSourceRecord,
            row,
            {
                "run_id": "run_id",
                "source_id": "source_id",
                "schema_version": "schema_version",
                "origin_type": "origin_type",
                "acquisition_method": "acquisition_method",
                "material_kind": "material_kind",
                "provider": "provider",
                "title": "title",
                "publisher": "publisher",
                "published_at": "published_at",
                "retrieved_at": "retrieved_at",
                "source_category": "source_category",
                "retrieval_source_type": "retrieval_source_type",
                "underlying_evidence_type": "underlying_evidence_type",
                "raw_underlying_evidence_type": "raw_underlying_evidence_type",
                "content_sha256": "content_sha256",
                "content_size_bytes": "content_size_bytes",
                "content_media_type": "content_media_type",
                "content_blob_path": "content_blob_path",
                "content_artifact_id": "content_artifact_id",
                "content_artifact_revision": "content_artifact_revision",
                "raw_payload_sha256": "raw_payload_sha256",
                "raw_payload_size_bytes": "raw_payload_size_bytes",
                "raw_payload_media_type": "raw_payload_media_type",
                "raw_payload_blob_path": "raw_payload_blob_path",
                "raw_payload_artifact_id": "raw_payload_artifact_id",
                "raw_payload_artifact_revision": "raw_payload_artifact_revision",
                "claims_eligible": "claims_eligible",
                "eligibility_reason": "eligibility_reason",
                "invocation_id": "invocation_id",
                "acquisition_event_id": "acquisition_event_id",
                "accepted_transaction_id": "accepted_transaction_id",
                "request_fingerprint": "request_fingerprint",
                "created_at": "created_at",
            },
        )

    def _decode_proposal_row(self, row: sqlite3.Row) -> AcceptedProposalRecord:
        return self._decode_checked(
            AcceptedProposalRecord,
            row,
            {
                "run_id": "run_id",
                "proposal_id": "proposal_id",
                "schema_version": "schema_version",
                "proposal_kind": "proposal_kind",
                "artifact_id": "artifact_id",
                "artifact_revision": "artifact_revision",
                "proposal_sha256": "proposal_sha256",
                "invocation_id": "invocation_id",
                "owner_stage_id": "owner_stage_id",
                "owner_role_id": "owner_role_id",
                "parent_proposal_id": "parent_proposal_id",
                "target_artifact_id": "target_artifact_id",
                "target_artifact_revision": "target_artifact_revision",
                "accepted_event_id": "accepted_event_id",
                "accepted_transaction_id": "accepted_transaction_id",
                "request_fingerprint": "request_fingerprint",
                "created_at": "created_at",
            },
        )

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
            receipt = self._decode_transaction_row(row)
            self._verify_transaction_relations(receipt)
            receipts.append(receipt)
        return tuple(receipts)

    def _decode_transaction_row(self, row: sqlite3.Row) -> TransactionReceipt:
        return self._decode_checked(
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

    def _transaction_relation_values(
        self,
        receipt: TransactionReceipt,
    ) -> tuple[
        tuple[str, ...],
        tuple[ArtifactRevisionReference, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        event_rows = self._connection.execute(
            """
            SELECT position, event_id FROM transaction_events
            WHERE run_id = ? AND transaction_id = ? ORDER BY position
            """,
            (receipt.run_id, receipt.transaction_id),
        ).fetchall()
        revision_rows = self._connection.execute(
            """
            SELECT position, artifact_id, revision
            FROM transaction_artifact_revisions
            WHERE run_id = ? AND transaction_id = ? ORDER BY position
            """,
            (receipt.run_id, receipt.transaction_id),
        ).fetchall()
        source_rows = self._connection.execute(
            """
            SELECT position, source_id FROM transaction_sources
            WHERE run_id = ? AND transaction_id = ? ORDER BY position
            """,
            (receipt.run_id, receipt.transaction_id),
        ).fetchall()
        proposal_rows = self._connection.execute(
            """
            SELECT position, proposal_id FROM transaction_proposals
            WHERE run_id = ? AND transaction_id = ? ORDER BY position
            """,
            (receipt.run_id, receipt.transaction_id),
        ).fetchall()
        if [row[0] for row in event_rows] != list(range(len(event_rows))) or [
            row[0] for row in revision_rows
        ] != list(range(len(revision_rows))) or [
            row[0] for row in source_rows
        ] != list(range(len(source_rows))) or [
            row[0] for row in proposal_rows
        ] != list(range(len(proposal_rows))):
            raise ControlStoreIntegrityError("transaction_relation_mismatch")
        event_ids = tuple(str(row[1]) for row in event_rows)
        try:
            revision_refs = tuple(
                ArtifactRevisionReference.model_validate(
                    {"artifact_id": row[1], "revision": row[2]}
                )
                for row in revision_rows
            )
        except ValidationError as exc:
            raise ControlStoreIntegrityError("transaction_relation_mismatch") from exc
        source_ids = tuple(str(row[1]) for row in source_rows)
        proposal_ids = tuple(str(row[1]) for row in proposal_rows)
        return event_ids, revision_refs, source_ids, proposal_ids

    def _verify_transaction_relations(self, receipt: TransactionReceipt) -> None:
        event_ids, revision_refs, source_ids, proposal_ids = (
            self._transaction_relation_values(receipt)
        )
        if (
            list(event_ids) != receipt.event_ids
            or list(revision_refs) != receipt.artifact_revisions
            or list(source_ids) != receipt.source_ids
            or list(proposal_ids) != receipt.proposal_ids
        ):
            raise ControlStoreIntegrityError("transaction_relation_mismatch")

    def _verify_workspace_ledger_graph(self) -> None:
        """Verify one complete workspace transaction graph in this SQL snapshot."""

        def invalid() -> None:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

        workspace_revision = self._workspace_revision_in_transaction()
        transaction_rows = self._connection.execute(
            """
            SELECT * FROM transactions
            ORDER BY committed_revision, run_id, transaction_id
            """
        ).fetchall()
        if len(transaction_rows) != workspace_revision:
            invalid()

        event_owners: dict[tuple[str, str], str] = {}
        revision_owners: dict[tuple[str, str, int], str] = {}
        source_owners: dict[tuple[str, str], str] = {}
        proposal_owners: dict[tuple[str, str], str] = {}
        for expected_revision, row in enumerate(transaction_rows, start=1):
            receipt = self._decode_transaction_row(row)
            if (
                row["workspace_id"] != self.workspace_id
                or receipt.prior_revision != expected_revision - 1
                or receipt.committed_revision != expected_revision
            ):
                invalid()
            event_ids, revision_refs, source_ids, proposal_ids = (
                self._transaction_relation_values(receipt)
            )
            if (
                list(event_ids) != receipt.event_ids
                or list(revision_refs) != receipt.artifact_revisions
                or list(source_ids) != receipt.source_ids
                or list(proposal_ids) != receipt.proposal_ids
            ):
                raise ControlStoreIntegrityError("transaction_relation_mismatch")
            for event_id in event_ids:
                key = (receipt.run_id, event_id)
                if key in event_owners:
                    invalid()
                event_owners[key] = receipt.transaction_id
            for reference in revision_refs:
                key = (receipt.run_id, reference.artifact_id, reference.revision)
                if key in revision_owners:
                    invalid()
                revision_owners[key] = receipt.transaction_id
            for source_id in source_ids:
                key = (receipt.run_id, source_id)
                if key in source_owners:
                    invalid()
                source_owners[key] = receipt.transaction_id
            for proposal_id in proposal_ids:
                key = (receipt.run_id, proposal_id)
                if key in proposal_owners:
                    invalid()
                proposal_owners[key] = receipt.transaction_id

        event_keys: set[tuple[str, str]] = set()
        for row in self._connection.execute(
            "SELECT * FROM events ORDER BY run_id, event_id"
        ).fetchall():
            event = self._decode_checked(
                EventEnvelope,
                row,
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
            )
            key = (event.run_id, event.event_id)
            owner = event_owners.get(key)
            if owner is None or key in event_keys:
                invalid()
            if event.transaction_id is not None and event.transaction_id != owner:
                invalid()
            event_keys.add(key)
        if event_keys != set(event_owners):
            invalid()

        revision_keys: set[tuple[str, str, int]] = set()
        for row in self._connection.execute(
            """
            SELECT * FROM artifact_revisions
            ORDER BY run_id, artifact_id, revision
            """
        ).fetchall():
            revision = self._decode_checked(
                ArtifactRevision,
                row,
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
            )
            key = (revision.run_id, revision.artifact_id, revision.revision)
            if key not in revision_owners or key in revision_keys:
                invalid()
            revision_keys.add(key)
        if revision_keys != set(revision_owners):
            invalid()

        source_keys: set[tuple[str, str]] = set()
        for row in self._connection.execute(
            "SELECT * FROM sources ORDER BY run_id, source_id"
        ).fetchall():
            source = self._decode_source_row(row)
            key = (source.run_id, source.source_id)
            owner = source_owners.get(key)
            if (
                owner is None
                or owner != source.accepted_transaction_id
                or key in source_keys
            ):
                invalid()
            self._verify_source_graph_record(source)
            source_keys.add(key)
        if source_keys != set(source_owners):
            invalid()

        proposal_keys: set[tuple[str, str]] = set()
        proposal_source_ids: dict[tuple[str, str], set[str]] = {}
        for row in self._connection.execute(
            "SELECT * FROM accepted_proposals ORDER BY run_id, proposal_id"
        ).fetchall():
            proposal = self._decode_proposal_row(row)
            key = (proposal.run_id, proposal.proposal_id)
            owner = proposal_owners.get(key)
            if (
                owner is None
                or owner != proposal.accepted_transaction_id
                or key in proposal_keys
            ):
                invalid()
            self._verify_proposal_graph_record(proposal)
            proposal_keys.add(key)
            proposal_source_ids[key] = set(proposal.source_ids)
        if proposal_keys != set(proposal_owners):
            invalid()

        binding_source_ids: dict[tuple[str, str], set[str]] = {}
        for row in self._connection.execute(
            """
            SELECT * FROM proposal_source_bindings
            ORDER BY run_id, proposal_id, source_id
            """
        ).fetchall():
            binding = self._decode_checked(
                ProposalSourceBinding,
                row,
                {
                    "run_id": "run_id",
                    "proposal_id": "proposal_id",
                    "source_id": "source_id",
                    "schema_version": "schema_version",
                },
            )
            key = (binding.run_id, binding.proposal_id)
            binding_source_ids.setdefault(key, set()).add(binding.source_id)
        for key, expected in proposal_source_ids.items():
            if binding_source_ids.get(key, set()) != expected:
                invalid()
        if set(binding_source_ids) - set(proposal_source_ids):
            invalid()

    def _verify_source_graph_record(self, source: AcceptedSourceRecord) -> None:
        content_revision = self._artifact_revision_for(
            source.run_id,
            source.content_artifact_id,
            source.content_artifact_revision,
        )
        content_artifact = self._artifact_for(
            source.run_id,
            source.content_artifact_id,
        )
        expected_content_path = self._workspace_blob_path(source.content_sha256)
        if (
            content_revision.sha256 != source.content_sha256
            or content_revision.size_bytes != source.content_size_bytes
            or content_revision.path != source.content_blob_path
            or source.content_blob_path != expected_content_path
            or content_artifact.current_revision != source.content_artifact_revision
            or content_artifact.path != expected_content_path
        ):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        if source.raw_payload_artifact_id is not None:
            raw_revision = self._artifact_revision_for(
                source.run_id,
                source.raw_payload_artifact_id,
                cast(int, source.raw_payload_artifact_revision),
            )
            raw_artifact = self._artifact_for(
                source.run_id,
                source.raw_payload_artifact_id,
            )
            expected_raw_path = self._workspace_blob_path(
                cast(str, source.raw_payload_sha256)
            )
            if (
                raw_revision.sha256 != source.raw_payload_sha256
                or raw_revision.size_bytes != source.raw_payload_size_bytes
                or raw_revision.path != source.raw_payload_blob_path
                or source.raw_payload_blob_path != expected_raw_path
                or raw_artifact.current_revision
                != source.raw_payload_artifact_revision
                or raw_artifact.path != expected_raw_path
            ):
                raise ControlStoreIntegrityError(
                    "transaction_ledger_integrity_invalid"
                )
        event = self._event_for(source.run_id, source.acquisition_event_id)
        binding = event.intake_binding
        if (
            event.event_type != "source_evidence_committed"
            or event.transaction_id != source.accepted_transaction_id
            or event.artifact_id != source.content_artifact_id
            or binding is None
            or binding.outcome != "committed"
            or binding.request_id != source.accepted_transaction_id
            or binding.request_fingerprint != source.request_fingerprint
            or binding.invocation_id != source.invocation_id
            or binding.source_id != source.source_id
            or binding.proposal_id is not None
        ):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

    def _verify_proposal_graph_record(
        self,
        proposal: AcceptedProposalRecord,
    ) -> None:
        revision = self._artifact_revision_for(
            proposal.run_id,
            proposal.artifact_id,
            proposal.artifact_revision,
        )
        self._artifact_for(proposal.run_id, proposal.artifact_id)
        expected_path = self._workspace_blob_path(proposal.proposal_sha256)
        event = self._event_for(proposal.run_id, proposal.accepted_event_id)
        binding = event.intake_binding
        if (
            revision.sha256 != proposal.proposal_sha256
            or revision.path != expected_path
            or event.event_type != "role_proposal_committed"
            or event.transaction_id != proposal.accepted_transaction_id
            or event.artifact_id != proposal.artifact_id
            or binding is None
            or binding.outcome != "committed"
            or binding.request_id != proposal.accepted_transaction_id
            or binding.request_fingerprint != proposal.request_fingerprint
            or binding.invocation_id != proposal.invocation_id
            or binding.proposal_id != proposal.proposal_id
            or binding.source_id is not None
        ):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

    def _artifact_for(self, run_id: str, artifact_id: str) -> ArtifactRecord:
        row = self._connection.execute(
            "SELECT * FROM artifacts WHERE run_id = ? AND artifact_id = ?",
            (run_id, artifact_id),
        ).fetchone()
        if row is None:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        return self._decode_checked(
            ArtifactRecord,
            row,
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
        )

    def _artifact_revision_for(
        self,
        run_id: str,
        artifact_id: str,
        revision: int,
    ) -> ArtifactRevision:
        row = self._connection.execute(
            """
            SELECT * FROM artifact_revisions
            WHERE run_id = ? AND artifact_id = ? AND revision = ?
            """,
            (run_id, artifact_id, revision),
        ).fetchone()
        if row is None:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        return self._decode_checked(
            ArtifactRevision,
            row,
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
        )

    def _event_for(self, run_id: str, event_id: str) -> EventEnvelope:
        row = self._connection.execute(
            "SELECT * FROM events WHERE run_id = ? AND event_id = ?",
            (run_id, event_id),
        ).fetchone()
        if row is None:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        return self._decode_checked(
            EventEnvelope,
            row,
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
        )

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
                for path in _validate_blob_topology(
                    self.blob_root,
                    error_code="blob_topology_invalid",
                ):
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
            except ControlStoreError:
                raise
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
