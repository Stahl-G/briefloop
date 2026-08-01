"""Focused backup-first schema v11 -> v12 upgrade contract."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
import sqlite3

import pytest

from multi_agent_brief.control_store.errors import (
    ControlStoreError,
    ControlStoreSchemaError,
)
from multi_agent_brief.control_store.schema import verify_schema
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore
from multi_agent_brief.control_store.upgrade import upgrade_store


def _v11_workspace(tmp_path: Path, workspace_id: str = "WS-UPGRADE-TEST") -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "briefloop.db.blobs").mkdir()
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        for version in range(1, 12):
            migration = resources.files("multi_agent_brief.control_store").joinpath(
                "migrations", f"{version:04d}.sql"
            )
            connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO workspaces(workspace_id, revision) VALUES (?, 0)",
            (workspace_id,),
        )
        connection.execute(
            """
            INSERT INTO transaction_receipt_compatibility_boundaries(
                workspace_id, boundary_id, legacy_receipt_max_committed_revision
            ) VALUES (?, ?, 0)
            """,
            (workspace_id, "briefloop.transaction_receipt_relation_compatibility.v1"),
        )
        connection.execute(
            """
            INSERT INTO source_acquisition_attempt_compatibility_boundaries(
                workspace_id, boundary_id, legacy_receipt_max_committed_revision
            ) VALUES (?, ?, 0)
            """,
            (workspace_id, "briefloop.source_acquisition_attempt_compatibility.v1"),
        )
        connection.commit()
    finally:
        connection.close()
    return workspace


def test_upgrade_is_backup_first_and_replayable(tmp_path: Path) -> None:
    workspace = _v11_workspace(tmp_path)
    backup = tmp_path / "backup"

    result = upgrade_store(workspace, backup)

    assert result["ok"] is True
    assert result["status"] == "upgraded"
    assert result["schema_version"] == 12
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == 0
    connection = sqlite3.connect(backup / "control.db")
    try:
        verify_schema(connection, expected_version=11)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 11
    finally:
        connection.close()
    assert (backup / "blobs").is_dir()

    retry = upgrade_store(workspace, tmp_path / "should-not-be-created")
    assert retry["status"] == "already_current"
    assert not (tmp_path / "should-not-be-created").exists()


@pytest.mark.parametrize(
    "stage",
    ["backup_published", "migration_start", "migration_committed", "post_verify"],
)
def test_upgrade_failure_boundaries_preserve_recovery(
    tmp_path: Path,
    stage: str,
) -> None:
    workspace = _v11_workspace(tmp_path, workspace_id=f"WS-UPGRADE-{stage}")
    backup = tmp_path / f"backup-{stage}"

    def fail(observed: str) -> None:
        if observed == stage:
            raise RuntimeError("synthetic failure")

    with pytest.raises(ControlStoreError) as error:
        upgrade_store(workspace, backup, failure_hook=fail)
    assert error.value.code == "store_upgrade_failed"
    assert (workspace / "briefloop.db").is_file()
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        verify_schema(connection, expected_version=11)
    finally:
        connection.close()
    assert backup.is_dir()


def test_upgrade_rejects_unsupported_schema_before_backup(tmp_path: Path) -> None:
    workspace = _v11_workspace(tmp_path)
    connection = sqlite3.connect(workspace / "briefloop.db")
    connection.execute("PRAGMA user_version = 10")
    connection.commit()
    connection.close()

    with pytest.raises(ControlStoreSchemaError) as error:
        upgrade_store(workspace, tmp_path / "backup")
    assert error.value.code == "unsupported_schema_version"
    assert not (tmp_path / "backup").exists()


def test_current_v12_is_noop_even_when_backup_name_exists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SQLiteControlStore.create(
        workspace / "briefloop.db",
        workspace_id="WS-UPGRADE-CURRENT",
    )
    store.close()
    existing = tmp_path / "existing-backup"
    existing.mkdir()
    result = upgrade_store(workspace, existing)
    assert result["status"] == "already_current"
    assert existing.is_dir()
