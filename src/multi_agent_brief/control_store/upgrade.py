"""Backup-first, narrowly scoped schema v11 to v12 upgrade."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import stat
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

from multi_agent_brief.control_store.errors import (
    ControlStoreError,
    ControlStoreIntegrityError,
    ControlStoreSchemaError,
    ControlStoreStateError,
)
from multi_agent_brief.control_store.schema import (
    _load_migration_sql,
    configure_connection,
    verify_schema,
)
from multi_agent_brief.control_store.sqlite_store import (
    SQLiteControlStore,
    _validate_blob_topology,
)


UpgradeHook = Callable[[str], None]


def upgrade_store(
    workspace: str | os.PathLike[str],
    backup: str | os.PathLike[str],
    *,
    failure_hook: UpgradeHook | None = None,
) -> dict[str, object]:
    """Upgrade one workspace from schema v11 to v12, or report no-op v12.

    The operation deliberately owns the only filesystem/schema effect in this
    unit. A private validation copy proves that the packaged 0012 resource can
    open the logical v11 snapshot before the public backup is published.
    """

    root = _normalize_path(workspace, "workspace_path_invalid")
    database = root / "briefloop.db"
    blobs = root / "briefloop.db.blobs"
    raw_target = Path(backup).expanduser()
    if raw_target.is_symlink():
        raise ControlStoreStateError("backup_destination_exists")
    _validate_backup_lexical_ancestors(raw_target)
    target = _normalize_path(backup, "backup_destination_invalid")
    if not _is_regular_file_without_link(database):
        raise ControlStoreStateError("database_not_found")

    version = _read_schema_version(database)
    if version == 12:
        return _read_current_metadata(database, blobs)

    _validate_backup_destination(root, target)
    if version != 11:
        raise ControlStoreSchemaError("unsupported_schema_version")

    # All checks which can reject the request run on a read-only connection.
    # In particular, do not switch an existing database into WAL before an
    # invalid backup path or malformed v11 snapshot has been rejected.
    readonly = _open_read_only_connection(
        database,
        immutable=_can_open_immutable(database),
    )
    try:
        _validate_v11_snapshot(readonly, blobs)
    finally:
        readonly.close()

    connection = _connect(database)
    committed = False
    published = False
    staging: Path | None = None
    try:
        # Hold the writer reservation before making the recoverable backup or
        # changing any schema bytes. A competing writer therefore fails here.
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        _validate_v11_snapshot(connection, blobs)
        target.parent.mkdir(parents=True, exist_ok=True)
        _validate_backup_destination(root, target)

        staging = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        staging.mkdir(parents=True, exist_ok=False)
        _copy_snapshot(database, blobs, staging)
        _validate_v11_snapshot(
            sqlite3.connect(staging / "control.db"),
            staging / "blobs",
            close_connection=True,
        )
        _fsync_tree(staging)
        os.replace(staging, target)
        staging = None
        _fsync_directory(target.parent)
        published = True
        _hook(failure_hook, "backup_published")

        _hook(failure_hook, "migration_start")
        migration = _load_migration_sql("0012")
        _execute_migration_in_transaction(connection, migration)
        verify_schema(connection)
        connection.commit()
        committed = True
        connection.execute("PRAGMA foreign_keys = ON")
        fk = connection.execute("PRAGMA foreign_keys").fetchone()
        if fk is None or fk[0] != 1:
            raise ControlStoreIntegrityError("foreign_keys_unavailable")
        _hook(failure_hook, "migration_committed")
        connection.close()
        connection = None  # type: ignore[assignment]

        _validate_current_snapshot(database, blobs)
        with _open_read_only_store(database, blobs) as store:
            result = {
                "ok": True,
                "status": "upgraded",
                "schema_version": 12,
                "workspace_id": store.workspace_id,
                "store_revision": store.current_revision,
                "backup": str(target),
            }
        _hook(failure_hook, "post_verify")
        return result
    except ControlStoreError:
        if connection is not None and not committed:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
        if connection is not None:
            connection.close()
        if published and committed:
            _restore_v11_snapshot(database, blobs, target)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        # A backup published before a failed migration is intentionally kept;
        # it is the user's recoverable evidence. Pre-publication staging is not.
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        if connection is not None and not committed:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
        if connection is not None:
            connection.close()
        if published and committed:
            _restore_v11_snapshot(database, blobs, target)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise ControlStoreIntegrityError("store_upgrade_failed") from exc


def _normalize_path(path: str | os.PathLike[str], error_code: str) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ControlStoreStateError(error_code) from exc


def _is_regular_file_without_link(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ControlStoreStateError("database_path_invalid") from exc
    return stat.S_ISREG(mode)


def _validate_backup_destination(root: Path, target: Path) -> None:
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        mode = None
    except OSError as exc:
        raise ControlStoreStateError("backup_destination_invalid") from exc
    if mode is not None:
        raise ControlStoreStateError("backup_destination_exists")
    if target == root or target.is_relative_to(root) or root.is_relative_to(target):
        raise ControlStoreStateError("backup_destination_overlaps_store")

    # Validate every existing ancestor without following a symlink. Missing
    # ancestors are allowed and are created only after all Store checks pass.
    current = target.parent
    while True:
        try:
            ancestor_mode = current.lstat().st_mode
        except FileNotFoundError:
            parent = current.parent
            if parent == current:
                raise ControlStoreStateError("backup_destination_invalid")
            current = parent
            continue
        except OSError as exc:
            raise ControlStoreStateError("backup_destination_invalid") from exc
        if stat.S_ISLNK(ancestor_mode) or not stat.S_ISDIR(ancestor_mode):
            raise ControlStoreStateError("backup_destination_invalid")
        break


def _validate_backup_lexical_ancestors(path: Path) -> None:
    """Reject a backup path that would cross a symlink before normalization."""

    current = path.parent
    while True:
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            parent = current.parent
            if parent == current:
                raise ControlStoreStateError("backup_destination_invalid")
            current = parent
            continue
        except OSError as exc:
            raise ControlStoreStateError("backup_destination_invalid") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ControlStoreStateError("backup_destination_invalid")
        break


def _open_read_only_connection(
    database: Path,
    *,
    immutable: bool = False,
) -> sqlite3.Connection:
    try:
        uri = f"file:{quote(str(database), safe='/')}?mode=ro"
        if immutable:
            uri += "&immutable=1"
        connection = sqlite3.connect(
            uri,
            uri=True,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        if not immutable:
            connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except sqlite3.Error as exc:
        raise ControlStoreStateError("database_open_failed") from exc


def _read_schema_version(database: Path) -> int:
    connection = _open_read_only_connection(
        database,
        immutable=_can_open_immutable(database),
    )
    try:
        return _schema_version(connection)
    finally:
        connection.close()


def _read_current_metadata(database: Path, blobs: Path) -> dict[str, object]:
    connection = _open_read_only_connection(
        database,
        immutable=_can_open_immutable(database),
    )
    try:
        verify_schema(connection, expected_version=12)
        rows = connection.execute(
            "SELECT workspace_id, revision FROM workspaces ORDER BY workspace_id"
        ).fetchall()
        if (
            len(rows) != 1
            or type(rows[0][0]) is not str
            or not rows[0][0]
            or type(rows[0][1]) is not int
            or rows[0][1] < 0
        ):
            raise ControlStoreIntegrityError("workspace_binding_invalid")
        _validate_blob_topology(blobs, error_code="blob_topology_invalid")
        return {
            "ok": True,
            "status": "already_current",
            "schema_version": 12,
            "workspace_id": str(rows[0][0]),
            "store_revision": int(rows[0][1]),
            "backup": None,
        }
    finally:
        connection.close()


def _can_open_immutable(database: Path) -> bool:
    return not any(
        database.with_name(f"{database.name}{suffix}").exists()
        for suffix in ("-wal", "-shm")
    )


def _open_read_only_store(database: Path, blobs: Path) -> SQLiteControlStore:
    immutable = _can_open_immutable(database)
    connection = _open_read_only_connection(database, immutable=immutable)
    try:
        rows = connection.execute(
            "SELECT workspace_id FROM workspaces ORDER BY workspace_id"
        ).fetchall()
        if len(rows) != 1 or type(rows[0][0]) is not str or not rows[0][0]:
            raise ControlStoreIntegrityError("workspace_binding_invalid")
        _validate_blob_topology(blobs, error_code="blob_topology_invalid")
        store = SQLiteControlStore(
            path=database,
            blob_root=blobs,
            connection=connection,
            workspace_id=str(rows[0][0]),
        )
        store._verify_all_payloads()
        return store
    except Exception:
        connection.close()
        raise


def _connect(database: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            database,
            isolation_level=None,
            check_same_thread=False,
        )
        configure_connection(connection)
        return connection
    except sqlite3.Error as exc:
        raise ControlStoreStateError("database_open_failed") from exc


def _schema_version(connection: sqlite3.Connection) -> int:
    try:
        value = connection.execute("PRAGMA user_version").fetchone()
        if value is None or type(value[0]) is not int:
            raise ControlStoreSchemaError("schema_version_invalid")
        return int(value[0])
    except ControlStoreError:
        raise
    except sqlite3.Error as exc:
        raise ControlStoreSchemaError("schema_version_invalid") from exc


def _validate_v11_snapshot(
    connection: sqlite3.Connection,
    blobs: Path,
    *,
    close_connection: bool = False,
) -> None:
    try:
        verify_schema(connection, expected_version=11)
        rows = connection.execute(
            "SELECT workspace_id FROM workspaces ORDER BY workspace_id"
        ).fetchall()
        if len(rows) != 1 or type(rows[0][0]) is not str or not rows[0][0]:
            raise ControlStoreIntegrityError("workspace_binding_invalid")
        _validate_blob_topology(blobs, error_code="blob_topology_invalid")
        _validate_payload_columns(connection)
        _validate_artifact_bytes(connection, blobs)
    except ControlStoreError:
        raise
    except sqlite3.Error as exc:
        raise ControlStoreIntegrityError("store_upgrade_preflight_failed") from exc
    finally:
        if close_connection:
            connection.close()


def _validate_payload_columns(connection: sqlite3.Connection) -> None:
    tables = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for row in tables:
        table = str(row[0])
        columns = {
            str(column[1])
            for column in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if "payload_json" not in columns:
            continue
        for payload in connection.execute(
            f'SELECT payload_json FROM "{table}" ORDER BY rowid'
        ).fetchall():
            if type(payload[0]) is not str:
                raise ControlStoreIntegrityError("stored_payload_invalid")
            try:
                import json

                decoded = json.loads(payload[0])
            except (TypeError, ValueError) as exc:
                raise ControlStoreIntegrityError("stored_payload_invalid") from exc
            if not isinstance(decoded, dict):
                raise ControlStoreIntegrityError("stored_payload_invalid")


def _validate_artifact_bytes(connection: sqlite3.Connection, blobs: Path) -> None:
    from multi_agent_brief.control_store.serialization import sha256_hex

    for row in connection.execute(
        "SELECT sha256, size_bytes, blob_relpath FROM artifact_revisions "
        "ORDER BY run_id, artifact_id, revision"
    ).fetchall():
        sha, size, relative = row
        if type(sha) is not str or relative != f"sha256/{sha[:2]}/{sha}":
            raise ControlStoreIntegrityError("blob_binding_invalid")
        path = blobs / str(relative)
        _validate_blob_topology(
            blobs,
            error_code="blob_topology_invalid",
            blob_path=path,
            require_blob=True,
            missing_blob_error_code="committed_blob_missing",
        )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ControlStoreIntegrityError("committed_blob_unreadable") from exc
        if type(size) is not int or len(content) != size or sha256_hex(content) != sha:
            raise ControlStoreIntegrityError("committed_blob_hash_mismatch")


def _copy_snapshot(
    database: Path,
    blobs: Path,
    destination: Path,
) -> None:
    output_database = destination / "control.db"
    output = sqlite3.connect(
        output_database,
        isolation_level=None,
        check_same_thread=False,
    )
    reader = sqlite3.connect(
        database,
        isolation_level=None,
        check_same_thread=False,
    )
    try:
        # The write connection holds BEGIN IMMEDIATE.  SQLite's backup API can
        # wait forever when asked to copy from that same active writer, so use
        # a read snapshot while the reservation is held.
        reader.backup(output)
    finally:
        reader.close()
        output.close()
    shutil.copytree(blobs, destination / "blobs", symlinks=True)
    _validate_blob_topology(destination / "blobs", error_code="blob_topology_invalid")


def _validate_migrated_snapshot(database: Path, blobs: Path) -> None:
    """Prove a v11 payload after migration, including the Core boundary."""

    staging = database.parent / f".{database.name}.{uuid4().hex}.validate"
    staging.mkdir(parents=False, exist_ok=False)
    try:
        _copy_workspace_snapshot(database, blobs, staging)
        connection = sqlite3.connect(
            staging / "briefloop.db",
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.executescript(_load_migration_sql("0012"))
            connection.execute("PRAGMA foreign_keys = ON")
        finally:
            connection.close()
        _validate_migrated_workspace(staging)
    except ControlStoreError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise ControlStoreIntegrityError("store_upgrade_preflight_failed") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _validate_current_snapshot(database: Path, blobs: Path) -> None:
    """Validate the committed v12 copy before publishing upgrade success."""

    staging = database.parent / f".{database.name}.{uuid4().hex}.current"
    staging.mkdir(parents=False, exist_ok=False)
    try:
        _copy_workspace_snapshot(database, blobs, staging)
        _validate_migrated_workspace(staging)
    except ControlStoreError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise ControlStoreIntegrityError("store_upgrade_post_verify_failed") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _copy_workspace_snapshot(
    database: Path,
    blobs: Path,
    destination: Path,
) -> None:
    destination_database = destination / "briefloop.db"
    output = sqlite3.connect(
        destination_database,
        isolation_level=None,
        check_same_thread=False,
    )
    reader = _open_read_only_connection(database)
    try:
        reader.backup(output)
    finally:
        reader.close()
        output.close()
    shutil.copytree(blobs, destination / "briefloop.db.blobs", symlinks=True)
    _validate_blob_topology(
        destination / "briefloop.db.blobs",
        error_code="blob_topology_invalid",
    )


def _validate_migrated_workspace(workspace: Path) -> None:
    """Run Store, Core and finalized-local projections on a private copy."""

    database = workspace / "briefloop.db"
    blobs = workspace / "briefloop.db.blobs"
    with SQLiteControlStore.open(database, blob_root=blobs) as store:
        history = store.load_history()
    run_ids = {
        snapshot.workspace_run_head.current_run_id
        for snapshot in history.snapshots
        if snapshot.workspace_run_head is not None
    }
    if len(run_ids) != 1:
        return
    run_id = next(iter(run_ids))
    snapshot = next(
        (item for item in history.snapshots if item.run.run_id == run_id),
        None,
    )
    # A deliberately minimal v11 store may only contain bootstrap records.
    # Once a Core run contract exists, however, the entire immutable Core
    # verifier and the finalized-local projection are mandatory upgrade gates.
    if snapshot is None or not snapshot.run_contract_bindings:
        return
    from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
    from multi_agent_brief.core_run_v2.errors import CoreRunError
    from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier

    try:
        verified = CoreRunDomainVerifier().verify_loaded_history(history, run_id)
    except CoreRunError as exc:
        raise ControlStoreIntegrityError(exc.code) from exc
    action = classify_core_run_next_action(verified)
    if action.effect_kind != "finalized_local":
        return
    from multi_agent_brief.runtime_host_v2.projections import (
        build_finalized_local_review_projection,
    )

    try:
        projection = build_finalized_local_review_projection(workspace)
    except Exception as exc:
        # RuntimeHost projections are read-only consumers here; translate any
        # projection failure into the Store's fixed value-free error boundary.
        raise ControlStoreIntegrityError("finalized_local_projection_invalid") from exc
    if projection.facts.run_id != run_id:
        raise ControlStoreIntegrityError("finalized_local_projection_invalid")

    _validate_post_final_assessment_snapshot(
        workspace,
        history=history,
        snapshot=history.snapshot_at_revision(run_id, history.store_revision),
        facts=projection.facts,
        action=action,
    )


def _validate_post_final_assessment_snapshot(
    workspace: Path,
    *,
    history: Any,
    snapshot: Any,
    facts: Any,
    action: Any,
) -> None:
    """Re-verify any existing PF-LAJ result without creating advisory state."""

    from multi_agent_brief.product.post_final_assessment import (
        PostFinalAssessmentError,
        PostFinalAssessmentService,
        post_final_assessment_archive_root,
        resolve_current_post_final_assessment_result,
        resolve_post_final_assessment_series,
    )
    from multi_agent_brief.semantic_evaluator.archive import (
        trial_archive_path,
        verify_shadow_archive,
    )
    from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
    from multi_agent_brief.semantic_evaluator.reader import build_laj_reader_view

    try:
        series = resolve_post_final_assessment_series(
            history,
            snapshot,
            facts,
            action,
        )
        for request in series:
            result = resolve_current_post_final_assessment_result(snapshot, request)
            if result is None:
                continue
            # Zero-advice terminal records intentionally do not require an archive
            # to remain readable; nonzero evidence must be fully replay-verifiable
            # before an upgrade can claim success.
            if result.finding_count == 0 and result.withheld_finding_count == 0:
                continue
            archive = verify_shadow_archive(
                trial_archive_path(
                    post_final_assessment_archive_root(workspace), request.trial_id
                )
            )
            view = build_laj_reader_view(
                archive.path,
                expected_report_sha256=facts.report.sha256,
            )
            if not PostFinalAssessmentService._result_matches_verified_evidence(
                result,
                request,
                archive,
                view,
            ):
                raise ControlStoreIntegrityError(
                    "post_final_assessment_binding_invalid"
                )
    except (
        PostFinalAssessmentError,
        SemanticEvaluatorError,
        OSError,
        ValueError,
    ) as exc:
        raise ControlStoreIntegrityError("post_final_assessment_invalid") from exc


def _execute_migration_in_transaction(
    connection: sqlite3.Connection,
    migration: str,
) -> None:
    text = migration.strip()
    begin = "BEGIN IMMEDIATE;"
    commit = "COMMIT;"
    if not text.startswith(begin) or not text.endswith(commit):
        raise ControlStoreSchemaError("migration_resource_invalid")
    body = text[len(begin) : -len(commit)]
    statement = ""
    for character in body:
        statement += character
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            statement = ""
            if sql:
                connection.execute(sql)
    if statement.strip():
        raise ControlStoreSchemaError("migration_resource_invalid")


def _restore_v11_snapshot(database: Path, blobs: Path, backup: Path) -> None:
    source_database = backup / "control.db"
    source_blobs = backup / "blobs"
    temporary_database = database.with_name(f".{database.name}.{uuid4().hex}.tmp")
    try:
        source_connection = _open_read_only_connection(source_database)
        try:
            _validate_v11_snapshot(source_connection, source_blobs)
            _validate_v11_snapshot(source_connection, blobs)
        finally:
            source_connection.close()
        _validate_blob_topology(blobs, error_code="blob_topology_invalid")
        shutil.copy2(source_database, temporary_database)
        staged_connection = _open_read_only_connection(temporary_database)
        try:
            _validate_v11_snapshot(staged_connection, source_blobs)
        finally:
            staged_connection.close()
        _remove_database_sidecars(database)
        try:
            os.replace(temporary_database, database)
        except OSError:
            # A single injected atomic-replace failure must not return with a
            # v12 database. The verified backup is the authoritative fallback;
            # copy2 is intentionally after the failed replace so the normal
            # path remains atomic.
            shutil.copy2(source_database, database)
            _fsync_file(database)
            temporary_database.unlink(missing_ok=True)
        _fsync_directory(database.parent)
        restored = _open_read_only_connection(database)
        try:
            _validate_v11_snapshot(restored, blobs)
        finally:
            restored.close()
    except Exception as exc:
        temporary_database.unlink(missing_ok=True)
        if isinstance(exc, ControlStoreError):
            raise
        raise ControlStoreIntegrityError("store_upgrade_rollback_failed") from exc


def _remove_database_sidecars(database: Path) -> None:
    for suffix in ("-wal", "-shm"):
        try:
            database.with_name(f"{database.name}{suffix}").unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ControlStoreIntegrityError("store_upgrade_rollback_failed") from exc


def _hook(hook: UpgradeHook | None, stage: str) -> None:
    if hook is None:
        return
    try:
        hook(stage)
    except ControlStoreError:
        raise
    except Exception as exc:
        raise ControlStoreIntegrityError("store_upgrade_failed") from exc


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            _fsync_file(path)
    for path in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        _fsync_directory(path)
    _fsync_directory(root)


def _fsync_file(path: Path) -> None:
    flags = os.O_RDWR if os.name == "nt" else os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError as exc:
        raise ControlStoreIntegrityError("file_sync_failed") from exc
    finally:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError as exc:
        raise ControlStoreIntegrityError("directory_sync_failed") from exc
    finally:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass


__all__ = ["upgrade_store"]
