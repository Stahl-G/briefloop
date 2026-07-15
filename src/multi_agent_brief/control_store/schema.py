"""SQLite schema loading and fail-closed version checks."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
import sqlite3

from multi_agent_brief.control_store.errors import (
    ControlStoreIntegrityError,
    ControlStoreSchemaError,
)


SCHEMA_VERSION = 1
MIGRATION_NAME = "0001"
_SCHEMA_OBJECT_TYPES = ("index", "table", "trigger", "view")


def _schema_inventory(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str | None], ...]:
    try:
        rows = connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_schema
            WHERE type IN ('index', 'table', 'trigger', 'view')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise ControlStoreIntegrityError(
            "database_schema_definition_mismatch"
        ) from exc
    inventory: list[tuple[str, str, str, str | None]] = []
    for row in rows:
        object_type, name, table_name, definition = row
        if object_type not in _SCHEMA_OBJECT_TYPES:
            raise ControlStoreIntegrityError("database_schema_definition_mismatch")
        inventory.append(
            (
                str(object_type),
                str(name),
                str(table_name),
                None if definition is None else str(definition),
            )
        )
    return tuple(inventory)


@lru_cache(maxsize=1)
def _expected_schema_inventory() -> tuple[tuple[str, str, str, str | None], ...]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(migration_sql())
        return _schema_inventory(connection)
    except ControlStoreIntegrityError:
        raise
    except sqlite3.Error as exc:
        raise ControlStoreSchemaError("migration_resource_invalid") from exc
    finally:
        connection.close()


def migration_sql() -> str:
    """Load the packaged schema resource in source clones and wheels."""

    resource = resources.files("multi_agent_brief.control_store").joinpath(
        "migrations",
        "0001.sql",
    )
    try:
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        raise ControlStoreSchemaError("migration_resource_unavailable") from exc


def configure_connection(connection: sqlite3.Connection) -> None:
    """Apply the fixed durability and FK settings to one writable connection."""

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or foreign_keys[0] != 1:
            raise ControlStoreSchemaError("foreign_keys_unavailable")
        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
        if journal_mode is None or str(journal_mode[0]).lower() != "wal":
            raise ControlStoreSchemaError("wal_unavailable")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.row_factory = sqlite3.Row
    except ControlStoreSchemaError:
        raise
    except sqlite3.Error as exc:
        raise ControlStoreSchemaError("connection_configuration_failed") from exc


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Install schema v1 into a newly created empty database."""

    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != 0:
            raise ControlStoreSchemaError("database_not_empty")
        connection.executescript(migration_sql())
    except ControlStoreSchemaError:
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.rollback()
        raise ControlStoreSchemaError("schema_install_failed") from exc
    verify_schema(connection)


def verify_schema(connection: sqlite3.Connection) -> None:
    """Reject missing, corrupt, or future schemas without migrating them."""

    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise ControlStoreSchemaError("schema_version_invalid") from exc
    if version > SCHEMA_VERSION:
        raise ControlStoreSchemaError("future_schema_version")
    if version != SCHEMA_VERSION:
        raise ControlStoreSchemaError("unsupported_schema_version")
    try:
        row = connection.execute(
            "SELECT name FROM schema_migrations WHERE version = ?",
            (SCHEMA_VERSION,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ControlStoreSchemaError("schema_metadata_invalid") from exc
    if row is None or row[0] != MIGRATION_NAME:
        raise ControlStoreSchemaError("schema_metadata_invalid")
    if _schema_inventory(connection) != _expected_schema_inventory():
        raise ControlStoreIntegrityError("database_schema_definition_mismatch")
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise ControlStoreIntegrityError("database_integrity_check_failed") from exc
    if result is None or result[0] != "ok":
        raise ControlStoreIntegrityError("database_integrity_check_failed")
    try:
        foreign_key_violation = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchone()
    except sqlite3.Error as exc:
        raise ControlStoreIntegrityError(
            "database_foreign_key_check_failed"
        ) from exc
    if foreign_key_violation is not None:
        raise ControlStoreIntegrityError("database_foreign_key_check_failed")


__all__ = [
    "MIGRATION_NAME",
    "SCHEMA_VERSION",
    "configure_connection",
    "initialize_schema",
    "migration_sql",
    "verify_schema",
]
