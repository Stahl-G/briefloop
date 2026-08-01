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
    ControlStoreIntegrityError,
    ControlStoreSchemaError,
)
from multi_agent_brief.control_store.schema import verify_schema
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore
import multi_agent_brief.control_store.upgrade as upgrade_module
from multi_agent_brief.control_store.serialization import canonical_json_bytes
import multi_agent_brief.product.post_final_assessment as assessment_module
from multi_agent_brief.product.post_final_assessment import (
    upgrade_post_final_assessment_store as upgrade_store,
)
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
    workspace, _ = _nonempty_v11_workspace(tmp_path)
    backup = tmp_path / "rollback-backup"
    before_blobs = sorted(
        (
            path.relative_to(workspace / "briefloop.db.blobs").as_posix(),
            path.read_bytes(),
        )
        for path in (workspace / "briefloop.db.blobs").rglob("*")
        if path.is_file()
    )
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        relative = connection.execute(
            "SELECT blob_relpath FROM artifact_revisions ORDER BY artifact_id,revision"
        ).fetchone()[0]
    finally:
        connection.close()
    deleted_blob = workspace / "briefloop.db.blobs" / str(relative)
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
            deleted_blob.unlink()
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
    assert deleted_blob.read_bytes() == BLOB


def test_rollback_rebuilds_deleted_live_blob_from_backup(
    tmp_path: Path,
) -> None:
    workspace, evidence = _nonempty_v11_workspace(
        tmp_path,
    )
    backup = tmp_path / "deleted-live-blob-backup"
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        relative = connection.execute(
            "SELECT blob_relpath FROM artifact_revisions ORDER BY artifact_id,revision"
        ).fetchone()[0]
    finally:
        connection.close()
    live_blob = workspace / "briefloop.db.blobs" / str(relative)

    def fail_after_commit(stage: str) -> None:
        if stage == "migration_committed":
            live_blob.unlink()
            raise RuntimeError("synthetic post-commit failure after blob loss")

    with pytest.raises(ControlStoreError) as error:
        upgrade_store(workspace, backup, failure_hook=fail_after_commit)
    assert error.value.code == "store_upgrade_failed"
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        verify_schema(connection, expected_version=11)
    finally:
        connection.close()
    assert live_blob.read_bytes() == evidence["blob"]
    assert (backup / "blobs" / str(relative)).read_bytes() == evidence["blob"]


def test_blob_restore_publication_failure_still_returns_verified_v11(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, evidence = _nonempty_v11_workspace(tmp_path)
    backup = tmp_path / "blob-publication-failure-backup"
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        relative = connection.execute(
            "SELECT blob_relpath FROM artifact_revisions ORDER BY artifact_id,revision"
        ).fetchone()[0]
    finally:
        connection.close()
    live_blob = workspace / "briefloop.db.blobs" / str(relative)
    original_replace = upgrade_module.os.replace
    failed = False

    def fail_blob_publication(source, destination) -> None:
        nonlocal failed
        if not failed and Path(destination) == workspace / "briefloop.db.blobs":
            failed = True
            raise OSError("synthetic blob publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(upgrade_module.os, "replace", fail_blob_publication)

    def fail_after_commit(stage: str) -> None:
        if stage == "migration_committed":
            live_blob.unlink()
            raise RuntimeError("synthetic post-commit failure")

    with pytest.raises(ControlStoreError) as error:
        upgrade_store(workspace, backup, failure_hook=fail_after_commit)
    assert failed is True
    assert error.value.code == "store_upgrade_failed"
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        verify_schema(connection, expected_version=11)
    finally:
        connection.close()
    assert live_blob.read_bytes() == evidence["blob"]


def test_blob_swap_marker_failure_repairs_from_verified_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, evidence = _nonempty_v11_workspace(tmp_path)
    backup = tmp_path / "blob-swap-marker-failure-backup"
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        relative = connection.execute(
            "SELECT blob_relpath FROM artifact_revisions ORDER BY artifact_id,revision"
        ).fetchone()[0]
    finally:
        connection.close()
    live_blob = workspace / "briefloop.db.blobs" / str(relative)
    original_replace = upgrade_module.os.replace
    failed = False

    def fail_swap_marker(source, destination) -> None:
        nonlocal failed
        if not failed and Path(destination).name.endswith(".previous"):
            failed = True
            raise OSError("synthetic swap-marker failure")
        original_replace(source, destination)

    monkeypatch.setattr(upgrade_module.os, "replace", fail_swap_marker)

    def fail_after_commit(stage: str) -> None:
        if stage == "migration_committed":
            live_blob.unlink()
            raise RuntimeError("synthetic post-commit failure")

    with pytest.raises(ControlStoreError) as error:
        upgrade_store(workspace, backup, failure_hook=fail_after_commit)
    assert failed is True
    assert error.value.code == "store_upgrade_failed"
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        verify_schema(connection, expected_version=11)
    finally:
        connection.close()
    assert live_blob.read_bytes() == evidence["blob"]


def test_migrated_clone_validates_before_live_writer_and_binds_source_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _v11_workspace(tmp_path)
    backup = tmp_path / "clone-order-backup"
    events: list[tuple[str, Path | None]] = []
    original_validate = (
        assessment_module._validate_post_final_assessment_upgrade_snapshot
    )
    original_connect = upgrade_module._connect

    def validate(workspace: Path, archive_workspace: Path) -> None:
        events.append(("clone", archive_workspace))
        original_validate(workspace, archive_workspace)

    def connect(*args, **kwargs):
        events.append(("connect", None))
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(
        assessment_module,
        "_validate_post_final_assessment_upgrade_snapshot",
        validate,
    )
    monkeypatch.setattr(upgrade_module, "_connect", connect)

    def stop_before_migration(stage: str) -> None:
        if stage == "migration_start":
            events.append(("migration", None))
            raise RuntimeError("stop after clone order proof")

    with pytest.raises(ControlStoreError):
        upgrade_store(workspace, backup, failure_hook=stop_before_migration)
    assert [name for name, _ in events] == ["clone", "connect", "migration"]
    assert events[0][1] == workspace


def test_low_level_upgrade_requires_product_validator_before_any_effect(
    tmp_path: Path,
) -> None:
    workspace = _v11_workspace(tmp_path)
    backup = tmp_path / "missing-validator-backup"
    before = (workspace / "briefloop.db").read_bytes()

    with pytest.raises(TypeError):
        upgrade_module.upgrade_store(workspace, backup)  # type: ignore[call-arg]

    assert (workspace / "briefloop.db").read_bytes() == before
    assert not backup.exists()


def test_product_validator_postcommit_failure_restores_verified_v11_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _v11_workspace(tmp_path)
    backup = tmp_path / "validator-rollback-backup"
    original_validate = (
        assessment_module._validate_post_final_assessment_upgrade_snapshot
    )
    calls = 0

    def validate(staging: Path, archive_workspace: Path) -> None:
        nonlocal calls
        original_validate(staging, archive_workspace)
        calls += 1
        if calls == 2:
            raise ControlStoreIntegrityError("synthetic_product_validator_failure")

    monkeypatch.setattr(
        assessment_module,
        "_validate_post_final_assessment_upgrade_snapshot",
        validate,
    )

    with pytest.raises(ControlStoreError) as error:
        upgrade_store(workspace, backup)

    assert error.value.code == "synthetic_product_validator_failure"
    assert calls == 2
    assert backup.is_dir()
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        verify_schema(connection, expected_version=11)
    finally:
        connection.close()


def _downgrade_0012_preserving_v2_assessment_request(root: Path) -> None:
    """Make a realistic v11 copy while retaining the generation-one request."""

    database = root / "briefloop.db"
    connection = sqlite3.connect(database)
    expected = sqlite3.connect(":memory:")
    try:
        for version in range(1, 12):
            migration = resources.files("multi_agent_brief.control_store").joinpath(
                "migrations", f"{version:04d}.sql"
            )
            expected.executescript(migration.read_text(encoding="utf-8"))
        request_sql = expected.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='post_final_assessment_requests'"
        ).fetchone()[0]
        request_triggers = [
            expected.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                (name,),
            ).fetchone()[0]
            for name in (
                "post_final_assessment_requests_no_update",
                "post_final_assessment_requests_no_delete",
                "schema_migrations_no_delete",
            )
        ]
        request_columns = (
            "run_id,assessment_request_id,schema_version,"
            "finalized_facts_fingerprint,finalized_lineage_fingerprint,"
            "policy_revision_id,trial_id,archive_identity_sha256,"
            "request_fingerprint,claimed_at,request_event_id,"
            "accepted_transaction_id,payload_json"
        )
        request_rows = connection.execute(
            f"SELECT {request_columns} FROM post_final_assessment_requests"
        ).fetchall()
        connection.execute("PRAGMA foreign_keys = OFF")
        for trigger in (
            "post_final_assessment_requests_no_update",
            "post_final_assessment_requests_no_delete",
            "post_final_assessment_abandonments_no_update",
            "post_final_assessment_abandonments_no_delete",
            "transaction_post_final_assessment_abandonments_no_update",
            "transaction_post_final_assessment_abandonments_no_delete",
            "post_final_assessment_abandonment_compatibility_boundaries_no_update",
            "post_final_assessment_abandonment_compatibility_boundaries_no_delete",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for table in (
            "transaction_post_final_assessment_abandonments",
            "post_final_assessment_abandonments",
            "post_final_assessment_abandonment_compatibility_boundaries",
            "post_final_assessment_requests",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute(request_sql)
        connection.executemany(
            f"INSERT INTO post_final_assessment_requests({request_columns}) "
            f"VALUES ({','.join('?' for _ in request_columns.split(','))})",
            request_rows,
        )
        connection.execute("DROP TRIGGER IF EXISTS schema_migrations_no_delete")
        connection.execute("DELETE FROM schema_migrations WHERE version=12")
        for trigger_sql in request_triggers:
            connection.executescript(trigger_sql)
        connection.execute("PRAGMA user_version = 11")
        connection.commit()
    finally:
        expected.close()
        connection.close()


@pytest.mark.skipif(
    os.name == "nt",
    reason="the finalized-local fixture requires the retained POSIX directory boundary",
)
def test_upgrade_validates_original_nonzero_pf_archive_before_live_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_agent_brief.product.post_final_assessment import (
        post_final_assessment_archive_root,
    )
    from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
        ANTHROPIC_API_KEY_SETTING,
    )
    from multi_agent_brief.semantic_evaluator.archive import trial_archive_path
    import multi_agent_brief.semantic_evaluator.runner as runner_module
    from tests.test_core_run_v2_packaging import _real_finalized_local_workspace
    from tests.test_post_final_assessment import _fixture_service, _policy_payload

    workspace = _real_finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls, terminal_mode="finding")
    policy = _policy_payload()
    policy["auto_run"] = True
    assert service.policy_set(policy)["ok"] is True
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    assessed = service.observe_finalized_local()
    assert assessed["ok"] is True, assessed
    assert assessed["status"] == "available"
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        trial_id = connection.execute(
            "SELECT trial_id FROM post_final_assessment_requests"
        ).fetchone()[0]
        result_payload = connection.execute(
            "SELECT payload_json FROM post_final_assessment_results"
        ).fetchone()[0]
    finally:
        connection.close()
    assert json.loads(result_payload)["finding_count"] > 0
    archive = trial_archive_path(
        post_final_assessment_archive_root(workspace), trial_id
    )
    assert (archive / "archive_manifest.json").is_file()
    _downgrade_0012_preserving_v2_assessment_request(workspace)
    backup = tmp_path / "pf-history-backup"
    result = upgrade_store(workspace, backup)
    assert result["status"] == "upgraded"
    assert (archive / "archive_manifest.json").is_file()


def test_upgrade_rejects_tampered_original_pf_archive_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_agent_brief.product.post_final_assessment import (
        post_final_assessment_archive_root,
    )
    from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
        ANTHROPIC_API_KEY_SETTING,
    )
    from multi_agent_brief.semantic_evaluator.archive import trial_archive_path
    import multi_agent_brief.semantic_evaluator.runner as runner_module
    from tests.test_finalized_local_review_facts import _finalized_local_workspace
    from tests.test_post_final_assessment import _fixture_service, _policy_payload

    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    service = _fixture_service(workspace, [], terminal_mode="finding")
    policy = _policy_payload()
    policy["auto_run"] = True
    assert service.policy_set(policy)["ok"] is True
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    assessed = service.observe_finalized_local()
    assert assessed["ok"] is True, assessed
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        trial_id = connection.execute(
            "SELECT trial_id FROM post_final_assessment_requests"
        ).fetchone()[0]
    finally:
        connection.close()
    archive = trial_archive_path(
        post_final_assessment_archive_root(workspace), trial_id
    )
    manifest = archive / "archive_manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b"tamper")
    _downgrade_0012_preserving_v2_assessment_request(workspace)
    before = (workspace / "briefloop.db").read_bytes()
    with pytest.raises(ControlStoreError) as error:
        upgrade_store(workspace, tmp_path / "tampered-pf-backup")
    assert error.value.code == "post_final_assessment_invalid"
    assert (workspace / "briefloop.db").read_bytes() == before
    assert not (tmp_path / "tampered-pf-backup").exists()


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
