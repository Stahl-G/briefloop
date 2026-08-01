"""Backup-first, narrowly scoped schema v11 to v12 upgrade."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
from typing import Callable
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

    root = Path(workspace).expanduser().resolve(strict=False)
    database = root / "briefloop.db"
    blobs = root / "briefloop.db.blobs"
    target = Path(backup).expanduser().resolve(strict=False)
    if not database.is_file() or database.is_symlink():
        raise ControlStoreStateError("database_not_found")

    connection = _connect(database)
    committed = False
    published = False
    staging: Path | None = None
    try:
        version = _schema_version(connection)
        if version == 12:
            connection.close()
            with SQLiteControlStore.open(database, blob_root=blobs) as store:
                return {
                    "ok": True,
                    "status": "already_current",
                    "schema_version": 12,
                    "workspace_id": store.workspace_id,
                    "store_revision": store.current_revision,
                    "backup": None,
                }
        if target.exists() or target.is_symlink():
            raise ControlStoreStateError("backup_destination_exists")
        if target == root or target.is_relative_to(root):
            raise ControlStoreStateError("backup_destination_overlaps_store")
        if root.is_relative_to(target):
            raise ControlStoreStateError("backup_destination_overlaps_store")
        if version != 11:
            raise ControlStoreSchemaError("unsupported_schema_version")
        _validate_v11_snapshot(connection, blobs)

        # Hold the writer reservation before making the recoverable backup or
        # changing any schema bytes. A competing writer therefore fails here.
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        _validate_v11_snapshot(connection, blobs)
        _validate_migrated_snapshot(database, blobs)

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

        with SQLiteControlStore.open(database, blob_root=blobs) as store:
            store.load_history()
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
    """Prove every v11 payload opens after the exact packaged migration."""

    staging = database.parent / f".{database.name}.{uuid4().hex}.validate"
    staging.mkdir(parents=False, exist_ok=False)
    try:
        _copy_snapshot(database, blobs, staging)
        connection = sqlite3.connect(
            staging / "control.db",
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.executescript(_load_migration_sql("0012"))
            connection.execute("PRAGMA foreign_keys = ON")
        finally:
            connection.close()
        with SQLiteControlStore.open(
            staging / "control.db",
            blob_root=staging / "blobs",
        ) as store:
            store.load_history()
    except ControlStoreError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise ControlStoreIntegrityError("store_upgrade_preflight_failed") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
    temporary_blobs = blobs.with_name(f".{blobs.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source_database, temporary_database)
        shutil.copytree(source_blobs, temporary_blobs, symlinks=True)
        _validate_blob_topology(temporary_blobs, error_code="blob_topology_invalid")
        for suffix in ("", "-wal", "-shm"):
            try:
                database.with_name(f"{database.name}{suffix}").unlink()
            except FileNotFoundError:
                pass
        if blobs.exists() or blobs.is_symlink():
            shutil.rmtree(blobs)
        os.replace(temporary_database, database)
        os.replace(temporary_blobs, blobs)
        _fsync_directory(database.parent)
    except Exception as exc:
        temporary_database.unlink(missing_ok=True)
        shutil.rmtree(temporary_blobs, ignore_errors=True)
        if isinstance(exc, ControlStoreError):
            raise
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
