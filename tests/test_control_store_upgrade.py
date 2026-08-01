"""Focused backup-first schema v11 -> v12 upgrade contract."""

from __future__ import annotations

import json
import hashlib
from importlib import resources
import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from multi_agent_brief.control_store.errors import (
    ControlStoreError,
    ControlStoreSchemaError,
)
from multi_agent_brief.control_store.schema import verify_schema
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore
import multi_agent_brief.control_store.upgrade as upgrade_module
from multi_agent_brief.control_store.upgrade import upgrade_store
from multi_agent_brief.control_store.serialization import canonical_json_bytes
from tests.test_control_store import BLOB, _records, _stage_all


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


def _nonempty_v11_workspace(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    """Build v11 with one real receipt, artifact revision, and committed blob."""

    source_root = tmp_path / "source-v12"
    source_root.mkdir()
    source_store = SQLiteControlStore.create(
        source_root / "briefloop.db",
        workspace_id="WS-UPGRADE-HISTORY",
    )
    records = _records(
        run_id="RUN-UPGRADE-HISTORY",
        workspace_id="WS-UPGRADE-HISTORY",
        transaction_id="TX-UPGRADE-HISTORY",
    )
    receipt = _stage_all(source_store, records).commit()
    source_store.close()

    v11_root = tmp_path / "v11"
    v11_root.mkdir()
    workspace = _v11_workspace(v11_root, "WS-UPGRADE-HISTORY")
    source_connection = sqlite3.connect(source_root / "briefloop.db")
    target_connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        target_connection.execute("PRAGMA foreign_keys = OFF")
        for table in (
            "runs",
            "stage_states",
            "agent_invocations",
            "artifacts",
            "artifact_identities",
            "artifact_revisions",
            "events",
            "approvals",
            "deliveries",
            "transactions",
            "transaction_events",
            "transaction_artifact_revisions",
            "transaction_artifact_identities",
        ):
            target_connection.execute(f'DELETE FROM "{table}"')
        target_connection.execute(
            "ATTACH DATABASE ? AS source_store",
            (str(source_root / "briefloop.db"),),
        )
        for table, columns in (
            (
                "runs",
                "run_id,workspace_id,schema_version,runtime,created_at,payload_json",
            ),
            (
                "stage_states",
                "run_id,stage_id,schema_version,status,revision,updated_at,payload_json",
            ),
            (
                "agent_invocations",
                "run_id,invocation_id,schema_version,role_id,runtime,status,started_at,completed_at,failure_reason,payload_json",
            ),
            (
                "artifacts",
                "run_id,artifact_id,schema_version,current_revision,current_revision_ref,status,required,path,format,payload_json",
            ),
            (
                "artifact_identities",
                "run_id,artifact_id,schema_version,required,initial_path,format,accepted_transaction_id,payload_json",
            ),
            (
                "artifact_revisions",
                "run_id,artifact_id,revision,schema_version,path,sha256,size_bytes,frozen,producer_kind,producer_id,created_at,blob_relpath,payload_json",
            ),
            (
                "events",
                "event_id,run_id,schema_version,event_type,created_at,actor,transaction_id,stage_id,artifact_id,decision,reason,metadata_json,payload_json",
            ),
            (
                "approvals",
                "run_id,approval_id,schema_version,mode,role,decision,reason,actor_id,recorded_at,boundary,event_id,payload_json",
            ),
            (
                "deliveries",
                "run_id,delivery_id,schema_version,artifact_id,artifact_revision,approval_id,status,target,channel,created_at,completed_at,payload_json",
            ),
            (
                "transaction_events",
                "run_id,transaction_id,position,event_id",
            ),
            (
                "transaction_artifact_revisions",
                "run_id,transaction_id,position,artifact_id,revision",
            ),
            (
                "transaction_artifact_identities",
                "run_id,transaction_id,position,artifact_id",
            ),
            (
                "transaction_approvals",
                "run_id,transaction_id,position,approval_id",
            ),
        ):
            target_connection.execute(
                f'INSERT INTO "{table}" ({columns}) '
                f'SELECT {columns} FROM source_store."{table}"'
            )
        transaction = source_connection.execute(
            "SELECT run_id,transaction_id,workspace_id,schema_version,"
            "transaction_type,prior_revision,committed_revision,committed_at,"
            "projection_status,fingerprint,payload_json FROM transactions"
        ).fetchone()
        assert transaction is not None
        payload = json.loads(transaction[-1])
        payload.pop("post_final_assessment_abandonments", None)
        target_connection.execute(
            "INSERT INTO transactions("
            "run_id,transaction_id,workspace_id,schema_version,transaction_type,"
            "prior_revision,committed_revision,committed_at,projection_status,"
            "fingerprint,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (*transaction[:-1], canonical_json_bytes(payload).decode("utf-8")),
        )
        target_connection.commit()
        target_connection.execute("DETACH DATABASE source_store")
        target_connection.execute(
            "UPDATE workspaces SET revision=? WHERE workspace_id=?",
            (1, "WS-UPGRADE-HISTORY"),
        )
        target_connection.commit()
    finally:
        source_connection.close()
        target_connection.close()
    shutil.copytree(
        source_root / "briefloop.db.blobs",
        workspace / "briefloop.db.blobs",
        dirs_exist_ok=True,
    )
    database = sqlite3.connect(workspace / "briefloop.db")
    try:
        receipt_payload = database.execute(
            "SELECT transaction_id,payload_json FROM transactions"
        ).fetchone()
        artifact_row = database.execute(
            "SELECT artifact_id,revision,sha256 FROM artifact_revisions"
        ).fetchone()
        assert receipt_payload is not None and artifact_row is not None
        evidence = {
            "receipt": str(receipt_payload[1]).encode("utf-8"),
            "artifact": f"{artifact_row[0]}:{artifact_row[1]}:{artifact_row[2]}".encode(
                "utf-8"
            ),
            "blob": BLOB,
        }
    finally:
        database.close()
    assert receipt.transaction_id == "TX-UPGRADE-HISTORY"
    return workspace, evidence


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


def _database_sidecar_bytes(workspace: Path) -> dict[str, bytes | None]:
    values: dict[str, bytes | None] = {}
    for suffix in ("", "-wal", "-shm"):
        path = workspace / f"briefloop.db{suffix}"
        values[suffix] = path.read_bytes() if path.exists() else None
    return values


def test_upgrade_rejects_backup_topology_before_wal_or_database_effect(
    tmp_path: Path,
) -> None:
    workspace = _v11_workspace(tmp_path)
    before_database = (workspace / "briefloop.db").read_bytes()
    before_sidecars = _database_sidecar_bytes(workspace)
    loop = tmp_path / "backup-loop"
    try:
        loop.symlink_to(loop, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(ControlStoreError) as error:
        upgrade_store(workspace, loop / "nested")
    assert error.value.code == "backup_destination_invalid"
    assert (workspace / "briefloop.db").read_bytes() == before_database
    assert _database_sidecar_bytes(workspace) == before_sidecars


def test_current_v12_noop_does_not_touch_database_or_journal(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SQLiteControlStore.create(
        workspace / "briefloop.db",
        workspace_id="WS-UPGRADE-NOOP-BYTES",
    )
    store.close()
    before_database = (workspace / "briefloop.db").read_bytes()
    before_sidecars = _database_sidecar_bytes(workspace)
    existing = tmp_path / "existing-backup"
    existing.mkdir()

    result = upgrade_store(workspace, existing)

    assert result["status"] == "already_current"
    assert (workspace / "briefloop.db").read_bytes() == before_database
    assert _database_sidecar_bytes(workspace) == before_sidecars


def test_upgrade_rollback_replace_failure_keeps_verified_v11_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _v11_workspace(tmp_path, workspace_id="WS-UPGRADE-ROLLBACK-REPLACE")
    backup = tmp_path / "rollback-backup"
    before_blobs = sorted(
        (
            path.relative_to(workspace / "briefloop.db.blobs").as_posix(),
            path.read_bytes(),
        )
        for path in (workspace / "briefloop.db.blobs").rglob("*")
        if path.is_file()
    )
    original_replace = upgrade_module.os.replace
    failed = False

    def fail_restore_replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        nonlocal failed
        if not failed and Path(destination) == workspace / "briefloop.db":
            failed = True
            raise OSError("synthetic restore replace failure")
        original_replace(source, destination)

    monkeypatch.setattr(upgrade_module.os, "replace", fail_restore_replace)

    def fail_after_commit(stage: str) -> None:
        if stage == "migration_committed":
            raise RuntimeError("synthetic post-commit failure")

    with pytest.raises(ControlStoreError) as error:
        upgrade_store(workspace, backup, failure_hook=fail_after_commit)
    assert error.value.code == "store_upgrade_failed"
    assert failed is True
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        verify_schema(connection, expected_version=11)
    finally:
        connection.close()
    backup_connection = sqlite3.connect(backup / "control.db")
    try:
        verify_schema(backup_connection, expected_version=11)
    finally:
        backup_connection.close()
    after_blobs = sorted(
        (
            path.relative_to(workspace / "briefloop.db.blobs").as_posix(),
            path.read_bytes(),
        )
        for path in (workspace / "briefloop.db.blobs").rglob("*")
        if path.is_file()
    )
    assert after_blobs == before_blobs


def test_upgrade_preserves_nonempty_generation_one_history_and_hashes(
    tmp_path: Path,
) -> None:
    workspace, evidence = _nonempty_v11_workspace(tmp_path)
    backup = tmp_path / "history-backup"
    before_database = (workspace / "briefloop.db").read_bytes()

    result = upgrade_store(workspace, backup)

    assert result["ok"] is True
    assert result["status"] == "upgraded"
    backup_connection = sqlite3.connect(backup / "control.db")
    try:
        verify_schema(backup_connection, expected_version=11)
        assert (
            backup_connection.execute(
                "SELECT transaction_id,payload_json FROM transactions"
            )
            .fetchone()[1]
            .encode("utf-8")
            == evidence["receipt"]
        )
    finally:
        backup_connection.close()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        snapshot = history.snapshots[0]
        assert snapshot.transactions[0].transaction_id == "TX-UPGRADE-HISTORY"
        assert snapshot.artifact_revisions[0].sha256 == hashlib.sha256(BLOB).hexdigest()
        assert snapshot.artifact_revisions[0].size_bytes == len(BLOB)
        sha256 = snapshot.artifact_revisions[0].sha256
        blob_path = store.blob_root / "sha256" / sha256[:2] / sha256
        assert blob_path.read_bytes() == evidence["blob"]
    assert (
        evidence["artifact"].decode("utf-8").endswith(hashlib.sha256(BLOB).hexdigest())
    )
    assert (workspace / "briefloop.db").read_bytes() != before_database
    assert (workspace / "briefloop.db.blobs" / "sha256").is_dir()


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
