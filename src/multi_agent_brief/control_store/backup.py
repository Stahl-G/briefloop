"""WAL-safe backup and restore helpers for SQLite ControlStore."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
from typing import TYPE_CHECKING
from uuid import uuid4

from multi_agent_brief.control_store.errors import (
    ControlStoreError,
    ControlStoreIntegrityError,
    ControlStoreStateError,
)
from multi_agent_brief.control_store.schema import verify_schema
from multi_agent_brief.control_store.sqlite_store import _validate_blob_topology

if TYPE_CHECKING:
    from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore


BACKUP_DATABASE_NAME = "control.db"
BACKUP_BLOB_DIRECTORY = "blobs"


def backup_store(
    store: "SQLiteControlStore",
    destination: str | os.PathLike[str],
) -> Path:
    target = store._normalize_path(destination, "backup_destination_invalid")
    if target.exists() or target.is_symlink():
        raise ControlStoreStateError("backup_destination_exists")
    if target.is_relative_to(store.blob_root):
        raise ControlStoreStateError("backup_destination_overlaps_store")
    _validate_blob_topology(
        store.blob_root,
        error_code="blob_topology_invalid",
    )
    store._verify_all_payloads()
    staging = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        backup_database = staging / BACKUP_DATABASE_NAME
        destination_connection = sqlite3.connect(
            backup_database,
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            store._connection.backup(destination_connection)
            verify_schema(destination_connection)
        finally:
            destination_connection.close()
        backup_blobs = staging / BACKUP_BLOB_DIRECTORY
        shutil.copytree(store.blob_root, backup_blobs, symlinks=True)
        _validate_blob_topology(
            backup_blobs,
            error_code="blob_topology_invalid",
        )
        _fsync_tree(staging)
        os.replace(staging, target)
        _fsync_directory(target.parent)
    except ControlStoreError:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(target, ignore_errors=True)
        raise
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(target, ignore_errors=True)
        raise ControlStoreIntegrityError("backup_failed") from exc
    return target


def restore_store(
    store_type: type["SQLiteControlStore"],
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    blob_root: str | os.PathLike[str] | None,
) -> "SQLiteControlStore":
    backup = store_type._normalize_path(source, "backup_source_invalid")
    database_path = store_type._normalize_path(
        destination,
        "restore_destination_invalid",
    )
    blobs = store_type._blob_root_for(database_path, blob_root)
    store_type._validate_database_blob_separation(database_path, blobs)
    if database_path.is_relative_to(backup) or blobs.is_relative_to(backup):
        raise ControlStoreStateError("restore_destination_overlaps_backup")
    source_database = backup / BACKUP_DATABASE_NAME
    source_blobs = backup / BACKUP_BLOB_DIRECTORY
    if (
        not source_database.is_file()
        or source_database.is_symlink()
        or not source_blobs.exists()
    ):
        raise ControlStoreStateError("backup_incomplete")
    _validate_blob_topology(
        source_blobs,
        error_code="blob_topology_invalid",
    )
    if database_path.exists() or database_path.is_symlink():
        raise ControlStoreStateError("restore_destination_exists")
    if blobs.exists() or blobs.is_symlink():
        raise ControlStoreStateError("restore_blob_root_exists")
    temporary_database = database_path.with_name(
        f".{database_path.name}.{uuid4().hex}.tmp"
    )
    temporary_blobs = blobs.with_name(f".{blobs.name}.{uuid4().hex}.tmp")
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        blobs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_database, temporary_database)
        shutil.copytree(source_blobs, temporary_blobs, symlinks=True)
        _validate_blob_topology(
            temporary_blobs,
            error_code="blob_topology_invalid",
        )
        _fsync_file(temporary_database)
        _fsync_tree(temporary_blobs)
        os.replace(temporary_database, database_path)
        os.replace(temporary_blobs, blobs)
        _fsync_directory(database_path.parent)
        if blobs.parent != database_path.parent:
            _fsync_directory(blobs.parent)
        return store_type.open(database_path, blob_root=blobs)
    except Exception as exc:
        try:
            temporary_database.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        shutil.rmtree(temporary_blobs, ignore_errors=True)
        # A failed validation must not leave a partially accepted restore.
        store_type._remove_database_files(database_path)
        shutil.rmtree(blobs, ignore_errors=True)
        if isinstance(exc, ControlStoreError):
            raise
        if isinstance(exc, (OSError, sqlite3.Error)):
            raise ControlStoreIntegrityError("restore_failed") from exc
        raise


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
    # Windows' CRT rejects fsync on the read-only descriptor produced by ``rb``.
    # These are newly written store/backup files, so open a write-capable handle
    # there. POSIX retains its existing read-only file sync behavior.
    flags = os.O_RDWR if os.name == "nt" else os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ControlStoreIntegrityError("file_sync_failed") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise ControlStoreIntegrityError("file_sync_failed") from exc
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # Python exposes no portable Windows directory handle that os.fsync can
        # accept. File handles are flushed before each atomic replacement; POSIX
        # additionally fsyncs the containing directories below.
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise ControlStoreIntegrityError("directory_sync_failed") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise ControlStoreIntegrityError("directory_sync_failed") from exc
    finally:
        os.close(descriptor)


__all__ = ["BACKUP_BLOB_DIRECTORY", "BACKUP_DATABASE_NAME"]
