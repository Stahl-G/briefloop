from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from multi_agent_brief.contracts.v2 import (
    Approval,
    ArtifactRecord,
    ArtifactRevision,
    Delivery,
    EventEnvelope,
    Invocation,
    RunIdentity,
    SourceProposal,
    StageState,
    TransactionReceipt,
)
from multi_agent_brief.control_store import (
    ControlStoreConflict,
    ControlStoreIntegrityError,
    ControlStoreSchemaError,
    ControlStoreStateError,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.schema import migration_sql
from multi_agent_brief.control_store.serialization import canonical_model_text


RUN_ID = "RUN-20260715-001"
WORKSPACE_ID = "WS-CONTROLSTORE-TEST"
TRANSACTION_ID = "TX-CONTROLSTORE-001"
NOW = "2026-07-15T09:00:00+00:00"
COMMITTED_AT = datetime(2026, 7, 15, 9, 0, 1, tzinfo=timezone.utc)
BLOB = b"BriefLoop SQLite substrate test artifact.\n"
BLOB_SHA256 = hashlib.sha256(BLOB).hexdigest()
CRASH_TRANSACTION_ID = "TX-CRASH-BOUNDARY-001"


_CRASH_SUBPROCESS = r"""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys

from multi_agent_brief.contracts.v2 import (
    ArtifactRecord,
    ArtifactRevision,
    RunIdentity,
)
from multi_agent_brief.control_store import SQLiteControlStore

database = Path(sys.argv[1])
failure_stage = sys.argv[2]
content = b"BriefLoop SQLite substrate test artifact.\n"
digest = hashlib.sha256(content).hexdigest()


def crash(stage: str) -> None:
    if stage == failure_stage:
        os._exit(73)


store = SQLiteControlStore.open(
    database,
    clock=lambda: datetime(2026, 7, 15, 9, 0, 1, tzinfo=timezone.utc),
    _failure_hook=crash,
)
run = RunIdentity.model_validate(
    {
        "schema_version": RunIdentity.schema_id,
        "run_id": "RUN-20260715-001",
        "workspace_id": "WS-CONTROLSTORE-TEST",
        "runtime": "operator",
        "created_at": "2026-07-15T09:00:00+00:00",
    }
)
artifact = ArtifactRecord.model_validate(
    {
        "schema_version": ArtifactRecord.schema_id,
        "run_id": run.run_id,
        "artifact_id": "brief",
        "current_revision": 1,
        "status": "valid",
        "required": True,
        "path": "output/brief.md",
        "format": "markdown",
    }
)
revision = ArtifactRevision.model_validate(
    {
        "schema_version": ArtifactRevision.schema_id,
        "run_id": run.run_id,
        "artifact_id": artifact.artifact_id,
        "revision": 1,
        "path": f"output/artifacts/{digest}/brief.md",
        "sha256": digest,
        "size_bytes": len(content),
        "frozen": True,
        "producer_kind": "workflow_stage",
        "producer_id": "scout",
        "created_at": "2026-07-15T09:00:00+00:00",
    }
)
unit = store.begin(
    run.run_id,
    "TX-CRASH-BOUNDARY-001",
    "crash_boundary",
    0,
)
unit.put_run(run)
unit.put_artifact(artifact)
unit.put_artifact_revision(revision, content)
unit.commit()
store.close()
raise SystemExit(0)
"""


@dataclass(frozen=True)
class Records:
    run: RunIdentity
    stage: StageState
    invocation: Invocation
    artifact: ArtifactRecord
    revision: ArtifactRevision
    event: EventEnvelope
    approval: Approval
    delivery: Delivery


def _record(model_type, **values):
    return model_type.model_validate({"schema_version": model_type.schema_id, **values})


def _records(
    *,
    run_id: str = RUN_ID,
    workspace_id: str = WORKSPACE_ID,
    transaction_id: str = TRANSACTION_ID,
) -> Records:
    return Records(
        run=_record(
            RunIdentity,
            run_id=run_id,
            workspace_id=workspace_id,
            runtime="operator",
            created_at=NOW,
        ),
        stage=_record(
            StageState,
            run_id=run_id,
            stage_id="scout",
            status="complete",
            revision=1,
            updated_at=NOW,
        ),
        invocation=_record(
            Invocation,
            invocation_id="INV-SCOUT-001",
            run_id=run_id,
            role_id="scout",
            runtime="operator",
            status="completed",
            started_at=NOW,
            completed_at=NOW,
        ),
        artifact=_record(
            ArtifactRecord,
            run_id=run_id,
            artifact_id="brief",
            current_revision=1,
            status="valid",
            required=True,
            path="output/brief.md",
            format="markdown",
        ),
        revision=_record(
            ArtifactRevision,
            run_id=run_id,
            artifact_id="brief",
            revision=1,
            path=f"output/artifacts/{BLOB_SHA256}/brief.md",
            sha256=BLOB_SHA256,
            size_bytes=len(BLOB),
            frozen=True,
            producer_kind="workflow_stage",
            producer_id="scout",
            created_at=NOW,
        ),
        event=_record(
            EventEnvelope,
            event_id="EVT-CONTROLSTORE-001",
            run_id=run_id,
            event_type="stage_status_changed",
            created_at=NOW,
            actor="cli",
            transaction_id=transaction_id,
            stage_id="scout",
            artifact_id="brief",
            decision="continue",
            reason="The typed test transaction completed.",
            metadata={"z": 2, "a": {"finite": 1.25, "valid": True}},
        ),
        approval=_record(
            Approval,
            approval_id="APR-CONTROLSTORE-001",
            run_id=run_id,
            mode="internal_management_review",
            role="content_owner",
            decision="approve",
            reason="Synthetic control-store fixture approved.",
            actor_id="human-test-operator",
            recorded_at=NOW,
            boundary=(
                "internal_review_approval_records_only_not_public_release_authorization"
            ),
            event_id="EVT-CONTROLSTORE-001",
        ),
        delivery=_record(
            Delivery,
            delivery_id="DEL-CONTROLSTORE-001",
            run_id=run_id,
            artifact_id="brief",
            artifact_revision=1,
            approval_id="APR-CONTROLSTORE-001",
            status="succeeded",
            target="local",
            channel="local-test",
            created_at=NOW,
            completed_at=NOW,
        ),
    )


def _create_store(
    tmp_path: Path,
    *,
    failure_hook=None,
) -> SQLiteControlStore:
    return SQLiteControlStore.create(
        tmp_path / "control.db",
        workspace_id=WORKSPACE_ID,
        clock=lambda: COMMITTED_AT,
        _failure_hook=failure_hook,
    )


def _stage_all(store: SQLiteControlStore, records: Records | None = None):
    records = records or _records()
    unit = store.begin(
        run_id=records.run.run_id,
        transaction_id=records.event.transaction_id,
        transaction_type="control_store_bootstrap",
        expected_revision=0,
    )
    unit.put_run(records.run)
    unit.put_stage_state(records.stage)
    unit.put_invocation(records.invocation)
    unit.put_artifact(records.artifact)
    unit.put_artifact_revision(records.revision, BLOB)
    unit.append_event(records.event)
    unit.put_approval(records.approval)
    unit.put_delivery(records.delivery)
    return unit


def _table_count(store: SQLiteControlStore, table: str) -> int:
    return int(store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _symlink_directory(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")


def _replace_blob_prefix_with_symlink(blob_path: Path, outside: Path) -> Path:
    prefix = blob_path.parent
    prefix.rename(outside)
    _symlink_directory(prefix, outside)
    return prefix


def _corrupt_delivery_foreign_key(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "UPDATE deliveries SET artifact_revision = 999 WHERE run_id = ?",
            (RUN_ID,),
        )
        connection.commit()
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is not None
    finally:
        connection.close()


def _mutate_schema(database: Path, script: str) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.executescript(script)
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    finally:
        connection.close()


def _forged_receipt(
    base: TransactionReceipt,
    *,
    transaction_id: str,
    prior_revision: int,
    committed_revision: int,
    event_ids: tuple[str, ...] = (),
    artifact_revisions: tuple[tuple[str, int], ...] = (),
) -> TransactionReceipt:
    values = base.model_dump(mode="python")
    values.update(
        {
            "transaction_id": transaction_id,
            "prior_revision": prior_revision,
            "committed_revision": committed_revision,
            "event_ids": list(event_ids),
            "artifact_revisions": [
                {"artifact_id": artifact_id, "revision": revision}
                for artifact_id, revision in artifact_revisions
            ],
        }
    )
    return TransactionReceipt.model_validate(values)


def _insert_receipt_row(
    connection: sqlite3.Connection,
    receipt: TransactionReceipt,
) -> None:
    connection.execute(
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
            WORKSPACE_ID,
            receipt.schema_version,
            receipt.transaction_type,
            receipt.prior_revision,
            receipt.committed_revision,
            receipt.committed_at,
            receipt.projection_status,
            "0" * 64,
            canonical_model_text(receipt),
        ),
    )
    for position, event_id in enumerate(receipt.event_ids):
        connection.execute(
            """
            INSERT INTO transaction_events(
                run_id, transaction_id, position, event_id
            ) VALUES (?, ?, ?, ?)
            """,
            (receipt.run_id, receipt.transaction_id, position, event_id),
        )
    for position, reference in enumerate(receipt.artifact_revisions):
        connection.execute(
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


def _insert_artifact_revision_row(
    connection: sqlite3.Connection,
    record: ArtifactRevision,
) -> None:
    connection.execute(
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
            f"sha256/{record.sha256[:2]}/{record.sha256}",
            canonical_model_text(record),
        ),
    )


def _stage_crash_boundary_unit(store: SQLiteControlStore):
    records = _records(transaction_id=CRASH_TRANSACTION_ID)
    unit = store.begin(
        RUN_ID,
        CRASH_TRANSACTION_ID,
        "crash_boundary",
        0,
    )
    unit.put_run(records.run)
    unit.put_artifact(records.artifact)
    unit.put_artifact_revision(records.revision, BLOB)
    return unit


def _run_crash_subprocess(database: Path, failure_stage: str) -> None:
    repo = Path(__file__).parents[1]
    environment = os.environ.copy()
    source_path = str(repo / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path
        if not existing_pythonpath
        else source_path + os.pathsep + existing_pythonpath
    )
    command = [sys.executable]
    if sys.flags.optimize:
        command.append("-O")
    command.extend(["-c", _CRASH_SUBPROCESS, str(database), failure_stage])
    result = subprocess.run(
        command,
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 73, result.stderr


@pytest.mark.parametrize(
    "workspace_id",
    ["workspace id with spaces", "工作区", "", 7],
)
def test_create_rejects_invalid_workspace_id_before_any_path_write(
    tmp_path: Path,
    workspace_id: object,
) -> None:
    store_root = tmp_path / "not-created"
    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.create(
            store_root / "control.db",
            workspace_id=workspace_id,  # type: ignore[arg-type]
        )
    assert error.value.code == "workspace_id_invalid"
    assert str(error.value) == "workspace_id_invalid"
    assert not store_root.exists()


def test_control_store_round_trips_exact_nine_control_dtos(tmp_path: Path) -> None:
    records = _records()
    with _create_store(tmp_path) as store:
        receipt = _stage_all(store, records).commit()

        assert receipt.schema_version == "briefloop.transaction_receipt.v2"
        assert receipt.prior_revision == 0
        assert receipt.committed_revision == 1
        assert receipt.projection_status == "stale"
        assert receipt.event_ids == [records.event.event_id]
        assert [
            (item.artifact_id, item.revision) for item in receipt.artifact_revisions
        ] == [(records.revision.artifact_id, records.revision.revision)]

        snapshot = store.load_snapshot(RUN_ID)
        assert snapshot.workspace_id == WORKSPACE_ID
        assert snapshot.store_revision == 1
        assert snapshot.run == records.run
        assert snapshot.stage_states == (records.stage,)
        assert snapshot.invocations == (records.invocation,)
        assert snapshot.artifacts == (records.artifact,)
        assert snapshot.artifact_revisions == (records.revision,)
        assert snapshot.events == (records.event,)
        assert snapshot.approvals == (records.approval,)
        assert snapshot.deliveries == (records.delivery,)
        assert snapshot.transactions == (receipt,)
        assert snapshot.run.created_at == NOW

        event_row = store._connection.execute(
            "SELECT metadata_json, payload_json FROM events WHERE event_id = ?",
            (records.event.event_id,),
        ).fetchone()
        assert event_row[0] == '{"a":{"finite":1.25,"valid":true},"z":2}'
        assert event_row[1] == canonical_model_text(records.event)


@pytest.mark.parametrize(
    "corruption",
    [
        "revision_zero_with_transaction",
        "missing_terminal_transaction",
        "noncontiguous_transaction",
        "transaction_beyond_revision",
    ],
)
def test_open_rejects_noncontiguous_workspace_transaction_ledger(
    tmp_path: Path,
    corruption: str,
) -> None:
    store = _create_store(tmp_path)
    first = _stage_all(store).commit()
    if corruption == "revision_zero_with_transaction":
        store._connection.execute(
            "UPDATE workspaces SET revision = 0 WHERE workspace_id = ?",
            (WORKSPACE_ID,),
        )
    elif corruption == "missing_terminal_transaction":
        store._connection.execute(
            "UPDATE workspaces SET revision = 2 WHERE workspace_id = ?",
            (WORKSPACE_ID,),
        )
    elif corruption == "noncontiguous_transaction":
        _insert_receipt_row(
            store._connection,
            _forged_receipt(
                first,
                transaction_id="TX-FORGED-GAP-008",
                prior_revision=7,
                committed_revision=8,
            ),
        )
        store._connection.execute(
            "UPDATE workspaces SET revision = 8 WHERE workspace_id = ?",
            (WORKSPACE_ID,),
        )
    else:
        _insert_receipt_row(
            store._connection,
            _forged_receipt(
                first,
                transaction_id="TX-FORGED-BEYOND-002",
                prior_revision=1,
                committed_revision=2,
            ),
        )
    store.close()

    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.open(tmp_path / "control.db")
    assert error.value.code == "transaction_ledger_integrity_invalid"
    assert str(error.value) == "transaction_ledger_integrity_invalid"


def test_load_rejects_event_without_reverse_receipt_coverage(tmp_path: Path) -> None:
    with _create_store(tmp_path) as store:
        first = _stage_all(store).commit()
        uncovered = _records().event.model_copy(
            update={
                "event_id": "EV-UNCOVERED-002",
                "transaction_id": first.transaction_id,
            }
        )
        store._insert_events((uncovered,))

        with pytest.raises(ControlStoreIntegrityError) as error:
            store.load_snapshot(RUN_ID)
        assert error.value.code == "transaction_ledger_integrity_invalid"
        assert store.current_revision == 1
        assert _table_count(store, "transactions") == 1


def test_open_rejects_artifact_revision_without_reverse_receipt_coverage(
    tmp_path: Path,
) -> None:
    store = _create_store(tmp_path)
    _stage_all(store).commit()
    uncovered = _records().revision.model_copy(
        update={
            "revision": 2,
            "path": f"output/artifacts/{BLOB_SHA256}/brief-v2.md",
        }
    )
    _insert_artifact_revision_row(store._connection, uncovered)
    store.close()

    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.open(tmp_path / "control.db")
    assert error.value.code == "transaction_ledger_integrity_invalid"


@pytest.mark.parametrize("covered_kind", ["event", "artifact_revision"])
def test_open_rejects_rows_covered_by_two_transaction_receipts(
    tmp_path: Path,
    covered_kind: str,
) -> None:
    store = _create_store(tmp_path)
    first = _stage_all(store).commit()
    second = _forged_receipt(
        first,
        transaction_id=f"TX-DUPLICATE-{covered_kind.upper()}-002",
        prior_revision=1,
        committed_revision=2,
        event_ids=(first.event_ids[0],) if covered_kind == "event" else (),
        artifact_revisions=(
            (
                first.artifact_revisions[0].artifact_id,
                first.artifact_revisions[0].revision,
            ),
        )
        if covered_kind == "artifact_revision"
        else (),
    )
    _insert_receipt_row(store._connection, second)
    store._connection.execute(
        "UPDATE workspaces SET revision = 2 WHERE workspace_id = ?",
        (WORKSPACE_ID,),
    )
    store.close()

    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.open(tmp_path / "control.db")
    assert error.value.code == "transaction_ledger_integrity_invalid"


def test_open_rejects_event_transaction_id_that_differs_from_receipt_owner(
    tmp_path: Path,
) -> None:
    store = _create_store(tmp_path)
    first = _stage_all(store).commit()
    event_id = "EV-CROSS-OWNER-002"
    cross_owned = _records().event.model_copy(
        update={"event_id": event_id, "transaction_id": first.transaction_id}
    )
    store._insert_events((cross_owned,))
    second = _forged_receipt(
        first,
        transaction_id="TX-ACTUAL-OWNER-002",
        prior_revision=1,
        committed_revision=2,
        event_ids=(event_id,),
    )
    _insert_receipt_row(store._connection, second)
    store._connection.execute(
        "UPDATE workspaces SET revision = 2 WHERE workspace_id = ?",
        (WORKSPACE_ID,),
    )
    store.close()

    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.open(tmp_path / "control.db")
    assert error.value.code == "transaction_ledger_integrity_invalid"


def test_forward_receipt_relation_mismatch_keeps_existing_error(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        first = _stage_all(store).commit()
        extra = _records().event.model_copy(
            update={
                "event_id": "EV-EXTRA-RELATION-002",
                "transaction_id": first.transaction_id,
            }
        )
        store._insert_events((extra,))
        store._connection.execute(
            """
            INSERT INTO transaction_events(
                run_id, transaction_id, position, event_id
            ) VALUES (?, ?, 1, ?)
            """,
            (RUN_ID, first.transaction_id, extra.event_id),
        )

        with pytest.raises(ControlStoreIntegrityError) as error:
            store.load_snapshot(RUN_ID)
        assert error.value.code == "transaction_relation_mismatch"


def test_exact_replay_rejects_preexisting_corrupt_ledger(tmp_path: Path) -> None:
    with _create_store(tmp_path) as store:
        first = _stage_all(store).commit()
        uncovered = _records().event.model_copy(
            update={
                "event_id": "EV-REPLAY-UNCOVERED-002",
                "transaction_id": first.transaction_id,
            }
        )
        store._insert_events((uncovered,))

        with pytest.raises(ControlStoreIntegrityError) as error:
            _stage_all(store).commit()
        assert error.value.code == "transaction_ledger_integrity_invalid"
        assert store.current_revision == 1
        assert _table_count(store, "transactions") == 1


def test_commit_rechecks_second_connection_damage_before_new_blob(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        first = _stage_all(store).commit()
        content = b"new artifact that must not be staged\n"
        digest = hashlib.sha256(content).hexdigest()
        unit = store.begin(
            RUN_ID,
            "TX-BLOCKED-BY-LEDGER-002",
            "artifact_update",
            1,
        )
        unit.put_artifact(
            _record(
                ArtifactRecord,
                run_id=RUN_ID,
                artifact_id="new-artifact",
                current_revision=1,
                status="valid",
                required=False,
                path="output/new-artifact.md",
                format="markdown",
            )
        )
        unit.put_artifact_revision(
            _record(
                ArtifactRevision,
                run_id=RUN_ID,
                artifact_id="new-artifact",
                revision=1,
                path=f"output/artifacts/{digest}/new-artifact.md",
                sha256=digest,
                size_bytes=len(content),
                frozen=True,
                producer_kind="control_tool",
                producer_id="control-store-test",
                created_at=NOW,
            ),
            content,
        )
        second_connection = sqlite3.connect(store.path, isolation_level=None)
        try:
            uncovered = _records().event.model_copy(
                update={
                    "event_id": "EV-EXTERNAL-UNCOVERED-002",
                    "transaction_id": first.transaction_id,
                }
            )
            second_connection.execute(
                """
                INSERT INTO events(
                    event_id, run_id, schema_version, event_type, created_at,
                    actor, transaction_id, stage_id, artifact_id, decision,
                    reason, metadata_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uncovered.event_id,
                    uncovered.run_id,
                    uncovered.schema_version,
                    uncovered.event_type,
                    uncovered.created_at,
                    uncovered.actor,
                    uncovered.transaction_id,
                    uncovered.stage_id,
                    uncovered.artifact_id,
                    uncovered.decision,
                    uncovered.reason,
                    '{"a":{"finite":1.25,"valid":true},"z":2}',
                    canonical_model_text(uncovered),
                ),
            )
        finally:
            second_connection.close()

        with pytest.raises(ControlStoreIntegrityError) as error:
            unit.commit()
        assert error.value.code == "transaction_ledger_integrity_invalid"
        assert not store._blob_path(digest).exists()
        assert store.current_revision == 1
        assert _table_count(store, "artifact_revisions") == 1


def test_proposed_graph_is_verified_before_sqlite_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _create_store(tmp_path) as store:
        original = store._verify_workspace_ledger_graph
        observations: list[tuple[bool, int]] = []

        def reject_proposed_graph() -> None:
            original()
            revision = store._workspace_revision_in_transaction()
            observations.append((store._connection.in_transaction, revision))
            if revision == 1:
                raise ControlStoreIntegrityError(
                    "transaction_ledger_integrity_invalid"
                )

        monkeypatch.setattr(
            store,
            "_verify_workspace_ledger_graph",
            reject_proposed_graph,
        )
        with pytest.raises(ControlStoreIntegrityError) as error:
            _stage_all(store).commit()
        assert error.value.code == "transaction_ledger_integrity_invalid"
        assert (True, 1) in observations
        assert store.current_revision == 0
        assert _table_count(store, "transactions") == 0
        assert _table_count(store, "events") == 0
        assert _table_count(store, "artifact_revisions") == 0
        assert store._blob_path(BLOB_SHA256).is_file()


def test_multi_run_transactions_share_one_workspace_revision_chain(
    tmp_path: Path,
) -> None:
    second_run_id = "RUN-20260715-002"
    second_transaction_id = "TX-SECOND-RUN-002"
    with _create_store(tmp_path) as store:
        first = _stage_all(store).commit()
        second_run = _record(
            RunIdentity,
            run_id=second_run_id,
            workspace_id=WORKSPACE_ID,
            runtime="operator",
            created_at=NOW,
        )
        second_event = _record(
            EventEnvelope,
            event_id="EV-SECOND-RUN-002",
            run_id=second_run_id,
            event_type="run_initialized",
            created_at=NOW,
            actor="cli",
            transaction_id=second_transaction_id,
            stage_id=None,
            artifact_id=None,
            decision="continue",
            reason="Synthetic second run.",
            metadata={},
        )
        unit = store.begin(
            second_run_id,
            second_transaction_id,
            "run_initialize",
            1,
        )
        unit.put_run(second_run)
        unit.append_event(second_event)
        second = unit.commit()

        assert (first.prior_revision, first.committed_revision) == (0, 1)
        assert (second.prior_revision, second.committed_revision) == (1, 2)
        assert store.load_snapshot(RUN_ID).store_revision == 2
        second_snapshot = store.load_snapshot(second_run_id)
        assert second_snapshot.store_revision == 2
        assert second_snapshot.transactions == (second,)


def test_backup_and_restore_reject_corrupt_workspace_ledger(
    tmp_path: Path,
) -> None:
    backup_destination = tmp_path / "blocked-backup"
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        store._connection.execute(
            "UPDATE workspaces SET revision = 2 WHERE workspace_id = ?",
            (WORKSPACE_ID,),
        )
        with pytest.raises(ControlStoreIntegrityError) as error:
            store.backup_to(backup_destination)
        assert error.value.code == "transaction_ledger_integrity_invalid"
        assert not backup_destination.exists()

    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    with _create_store(valid_root) as valid_store:
        first = _stage_all(valid_store).commit()
        backup = valid_store.backup_to(tmp_path / "restore-source")
    connection = sqlite3.connect(backup / "control.db", isolation_level=None)
    try:
        _insert_receipt_row(
            connection,
            _forged_receipt(
                first,
                transaction_id="TX-RESTORE-GAP-008",
                prior_revision=7,
                committed_revision=8,
            ),
        )
        connection.execute(
            "UPDATE workspaces SET revision = 8 WHERE workspace_id = ?",
            (WORKSPACE_ID,),
        )
    finally:
        connection.close()
    destination = tmp_path / "rejected-restore.db"
    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.restore_to_new_path(backup, destination)
    assert error.value.code == "transaction_ledger_integrity_invalid"
    assert not destination.exists()
    assert not destination.with_name(f"{destination.name}.blobs").exists()


def test_backup_validates_copied_ledger_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "ledger-race-backup"
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        verify_source = store._verify_all_payloads

        def corrupt_after_source_verification() -> None:
            verify_source()
            connection = sqlite3.connect(store.path, isolation_level=None)
            try:
                connection.execute(
                    "UPDATE workspaces SET revision = 2 WHERE workspace_id = ?",
                    (WORKSPACE_ID,),
                )
            finally:
                connection.close()

        monkeypatch.setattr(
            store,
            "_verify_all_payloads",
            corrupt_after_source_verification,
        )
        with pytest.raises(ControlStoreIntegrityError) as error:
            store.backup_to(destination)

    assert error.value.code == "transaction_ledger_integrity_invalid"
    assert not destination.exists()
    assert not tuple(tmp_path.glob(f".{destination.name}.*.tmp"))


def test_backup_validates_copied_blob_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "blob-race-backup"
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        verify_source = store._verify_all_payloads

        def corrupt_after_source_verification() -> None:
            verify_source()
            store._blob_path(BLOB_SHA256).write_bytes(b"X" * len(BLOB))

        monkeypatch.setattr(
            store,
            "_verify_all_payloads",
            corrupt_after_source_verification,
        )
        with pytest.raises(ControlStoreIntegrityError) as error:
            store.backup_to(destination)

    assert error.value.code == "committed_blob_hash_mismatch"
    assert not destination.exists()
    assert not tuple(tmp_path.glob(f".{destination.name}.*.tmp"))


def test_schema_settings_and_exact_table_universe(tmp_path: Path) -> None:
    allowed_tables = {
        "schema_migrations",
        "workspaces",
        "runs",
        "stage_states",
        "agent_invocations",
        "transactions",
        "transaction_events",
        "transaction_artifact_revisions",
        "events",
        "artifacts",
        "artifact_revisions",
        "approvals",
        "deliveries",
    }
    with _create_store(tmp_path) as store:
        assert store._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store._connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert store._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert store._connection.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {
            row[0]
            for row in store._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert tables == allowed_tables
        assert (
            not {
                "stage_transitions",
                "sources",
                "claims",
                "claim_source_bindings",
                "repair_transactions",
                "projection_receipts",
            }
            & tables
        )


def test_transaction_receipt_preserves_event_and_artifact_revision_order(
    tmp_path: Path,
) -> None:
    transaction_id = "TX-ORDERED-LINKS-001"
    contents = {"artifact-b": b"B\n", "artifact-a": b"A\n"}
    records = _records(transaction_id=transaction_id)
    with _create_store(tmp_path) as store:
        unit = store.begin(RUN_ID, transaction_id, "ordered_links", 0)
        unit.put_run(records.run)
        expected_revisions: list[tuple[str, int]] = []
        for artifact_id in ("artifact-b", "artifact-a"):
            content = contents[artifact_id]
            digest = hashlib.sha256(content).hexdigest()
            unit.put_artifact(
                _record(
                    ArtifactRecord,
                    run_id=RUN_ID,
                    artifact_id=artifact_id,
                    current_revision=1,
                    status="valid",
                    required=True,
                    path=f"output/{artifact_id}.md",
                    format="markdown",
                )
            )
            revision = _record(
                ArtifactRevision,
                run_id=RUN_ID,
                artifact_id=artifact_id,
                revision=1,
                path=f"output/artifacts/{digest}/{artifact_id}.md",
                sha256=digest,
                size_bytes=len(content),
                frozen=True,
                producer_kind="workflow_stage",
                producer_id="scout",
                created_at=NOW,
            )
            unit.put_artifact_revision(revision, content)
            expected_revisions.append((artifact_id, 1))
        expected_events = ["EVT-ORDER-B", "EVT-ORDER-A"]
        for event_id, event_type in zip(
            expected_events,
            ("artifact_observed", "artifact_validated"),
        ):
            unit.append_event(
                _record(
                    EventEnvelope,
                    event_id=event_id,
                    run_id=RUN_ID,
                    event_type=event_type,
                    created_at=NOW,
                    actor="system",
                    transaction_id=transaction_id,
                )
            )
        receipt = unit.commit()
        assert receipt.event_ids == expected_events
        assert [
            (reference.artifact_id, reference.revision)
            for reference in receipt.artifact_revisions
        ] == expected_revisions
        assert store.load_snapshot(RUN_ID).transactions == (receipt,)


def test_transaction_exact_replay_is_idempotent_and_conflict_is_value_free(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        first = _stage_all(store).commit()
        replay = _stage_all(store).commit()
        assert replay == first
        assert store.current_revision == 1
        assert _table_count(store, "transactions") == 1

        changed = _records()
        changed_stage = changed.stage.model_copy(update={"status": "blocked"})
        unit = store.begin(
            run_id=RUN_ID,
            transaction_id=TRANSACTION_ID,
            transaction_type="control_store_bootstrap",
            expected_revision=0,
        )
        unit.put_run(changed.run)
        unit.put_stage_state(changed_stage)
        with pytest.raises(ControlStoreConflict) as error:
            unit.commit()
        assert error.value.code == "transaction_replay_conflict"
        assert str(error.value) == "transaction_replay_conflict"
        assert store.current_revision == 1
        assert store.load_snapshot(RUN_ID).stage_states == (changed.stage,)


def test_exact_replay_cannot_return_success_when_its_committed_blob_is_missing(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        store._blob_path(BLOB_SHA256).unlink()
        with pytest.raises(ControlStoreIntegrityError) as error:
            _stage_all(store).commit()
        assert error.value.code == "committed_blob_missing"
        assert store.current_revision == 1
        assert _table_count(store, "transactions") == 1


def test_optimistic_revision_conflict_happens_before_blob_write(tmp_path: Path) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        unit = store.begin(
            run_id=RUN_ID,
            transaction_id="TX-STALE-002",
            transaction_type="stale_write",
            expected_revision=0,
        )
        unit.put_stage_state(
            _records().stage.model_copy(update={"status": "blocked", "revision": 2})
        )
        with pytest.raises(ControlStoreConflict) as error:
            unit.commit()
        assert error.value.code == "store_revision_conflict"
        assert store.current_revision == 1
        assert _table_count(store, "transactions") == 1


def test_dual_connection_late_conflict_leaves_only_non_authoritative_orphan(
    tmp_path: Path,
) -> None:
    primary = _create_store(tmp_path)
    _stage_all(primary).commit()
    winner = SQLiteControlStore.open(tmp_path / "control.db", clock=lambda: COMMITTED_AT)
    loser_content = b"Late conflicting content-addressed blob.\n"
    loser_sha256 = hashlib.sha256(loser_content).hexdigest()
    loser_artifact_id = "late-conflict-brief"
    loser_transaction_id = "TX-LATE-CONFLICT-002"
    loser = primary.begin(
        RUN_ID,
        loser_transaction_id,
        "late_conflict",
        1,
    )
    loser.put_artifact(
        _record(
            ArtifactRecord,
            run_id=RUN_ID,
            artifact_id=loser_artifact_id,
            current_revision=1,
            status="valid",
            required=False,
            path=f"output/{loser_artifact_id}.md",
            format="markdown",
        )
    )
    loser.put_artifact_revision(
        _record(
            ArtifactRevision,
            run_id=RUN_ID,
            artifact_id=loser_artifact_id,
            revision=1,
            path=f"output/artifacts/{loser_sha256}/{loser_artifact_id}.md",
            sha256=loser_sha256,
            size_bytes=len(loser_content),
            frozen=True,
            producer_kind="workflow_stage",
            producer_id="scout",
            created_at=NOW,
        ),
        loser_content,
    )
    winner_committed = False

    def commit_winner(stage: str) -> None:
        nonlocal winner_committed
        if stage != "before_blob_write" or winner_committed:
            return
        winner_committed = True
        unit = winner.begin(
            RUN_ID,
            "TX-CONCURRENT-WINNER-002",
            "stage_state_update",
            1,
        )
        unit.put_stage_state(
            _records().stage.model_copy(update={"status": "blocked", "revision": 2})
        )
        unit.commit()

    primary._failure_hook = commit_winner
    try:
        with pytest.raises(ControlStoreConflict) as error:
            loser.commit()
        assert error.value.code == "store_revision_conflict"
        assert winner_committed is True
        assert primary.current_revision == 2
        assert primary._blob_path(loser_sha256).read_bytes() == loser_content
        assert primary.scan_orphans().orphan_hashes == (loser_sha256,)
        assert (
            primary._connection.execute(
                "SELECT 1 FROM artifacts WHERE run_id = ? AND artifact_id = ?",
                (RUN_ID, loser_artifact_id),
            ).fetchone()
            is None
        )
        assert (
            primary._connection.execute(
                "SELECT 1 FROM transactions WHERE run_id = ? AND transaction_id = ?",
                (RUN_ID, loser_transaction_id),
            ).fetchone()
            is None
        )
    finally:
        winner.close()
        primary.close()

    with SQLiteControlStore.open(tmp_path / "control.db") as reopened:
        snapshot = reopened.load_snapshot(RUN_ID)
        assert snapshot.store_revision == 2
        assert {item.artifact_id for item in snapshot.artifacts} == {"brief"}
        assert {item.transaction_id for item in snapshot.transactions} == {
            TRANSACTION_ID,
            "TX-CONCURRENT-WINNER-002",
        }
        assert reopened.scan_orphans().orphan_hashes == (loser_sha256,)


@pytest.mark.parametrize(
    ("transaction_id", "transaction_type"),
    [
        ("transaction id with spaces", "update"),
        ("TX-VALID-001", "type/with/slashes"),
        ("交易", "update"),
        ("TX-VALID-001", ""),
    ],
)
def test_invalid_transaction_identity_is_rejected_before_uow_or_blob_write(
    tmp_path: Path,
    transaction_id: str,
    transaction_type: str,
) -> None:
    with _create_store(tmp_path) as store:
        with pytest.raises(ControlStoreIntegrityError) as error:
            store.begin(RUN_ID, transaction_id, transaction_type, 0)
        assert error.value.code == "transaction_identity_invalid"
        assert str(error.value) == "transaction_identity_invalid"
        assert store.current_revision == 0
        assert _table_count(store, "transactions") == 0
        assert _table_count(store, "artifact_revisions") == 0
        assert list(store.blob_root.rglob("*")) == []


def test_uow_transaction_identity_is_read_only_after_begin(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        unit = _stage_all(store)
        for field_name, value in (
            ("run_id", "RUN-CHANGED-VALID"),
            ("transaction_id", "TX-CHANGED-VALID"),
            ("transaction_type", "changed_valid_type"),
            ("expected_revision", 9),
        ):
            with pytest.raises(AttributeError):
                setattr(unit, field_name, value)
        unit.rollback()
        assert store.current_revision == 0
        assert _table_count(store, "transactions") == 0
        assert _table_count(store, "artifact_revisions") == 0
        assert list(store.blob_root.rglob("*")) == []


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [
        ("run_id", "RUN-CHANGED-VALID"),
        ("transaction_id", "invalid transaction id"),
        ("transaction_type", "changed_valid_type"),
        ("expected_revision", 99),
    ],
)
def test_commit_uses_one_frozen_identity_across_blob_and_sql_boundaries(
    tmp_path: Path,
    field_name: str,
    changed_value: object,
) -> None:
    store = _create_store(tmp_path)
    unit = _stage_all(store)

    def replace_private_identity(stage: str) -> None:
        if stage == "before_blob_write":
            unit._identity = replace(
                unit._identity,
                **{field_name: changed_value},
            )

    store._failure_hook = replace_private_identity
    try:
        receipt = unit.commit()
        assert receipt.run_id == RUN_ID
        assert receipt.transaction_id == TRANSACTION_ID
        assert receipt.transaction_type == "control_store_bootstrap"
        assert receipt.prior_revision == 0
        assert store.current_revision == 1
        assert _table_count(store, "transactions") == 1
        assert _table_count(store, "artifact_revisions") == 1
        assert store.scan_orphans().orphan_hashes == ()
    finally:
        store.close()


def test_wrong_workspace_and_cross_run_records_fail_without_writes(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        wrong_workspace = _records(workspace_id="WS-OTHER")
        unit = store.begin(
            run_id=RUN_ID,
            transaction_id="TX-WRONG-WORKSPACE",
            transaction_type="bootstrap",
            expected_revision=0,
        )
        with pytest.raises(ControlStoreConflict) as error:
            unit.put_run(wrong_workspace.run)
        assert error.value.code == "control_record_workspace_mismatch"

        wrong_run_stage = _records(run_id="RUN-OTHER").stage
        with pytest.raises(ControlStoreConflict) as error:
            unit.put_stage_state(wrong_run_stage)
        assert error.value.code == "control_record_run_mismatch"
        unit.rollback()
        assert store.current_revision == 0
        assert _table_count(store, "runs") == 0


def test_event_transaction_ownership_is_exact_or_explicitly_unbound(
    tmp_path: Path,
) -> None:
    records = _records()
    with _create_store(tmp_path) as store:
        rejected = store.begin(
            run_id=RUN_ID,
            transaction_id="TX-CURRENT-EVENT-001",
            transaction_type="event_ownership_rejection",
            expected_revision=0,
        )
        mismatched_event = records.event.model_copy(
            update={"transaction_id": "TX-ALREADY-COMMITTED-001"}
        )
        with pytest.raises(ControlStoreConflict) as error:
            rejected.append_event(mismatched_event)
        assert error.value.code == "control_record_transaction_mismatch"
        assert str(error.value) == "control_record_transaction_mismatch"
        assert rejected._events == []
        assert rejected._event_ids == set()
        rejected.rollback()
        assert store.current_revision == 0
        assert _table_count(store, "events") == 0
        assert _table_count(store, "transactions") == 0

        exact = store.begin(
            run_id=RUN_ID,
            transaction_id=records.event.transaction_id,
            transaction_type="event_ownership_exact",
            expected_revision=0,
        )
        exact.put_run(records.run)
        exact.append_event(records.event)
        exact_receipt = exact.commit()
        assert exact_receipt.event_ids == [records.event.event_id]

        unbound_event = records.event.model_copy(
            update={
                "event_id": "EVT-CONTROLSTORE-UNBOUND-002",
                "transaction_id": None,
            }
        )
        unbound = store.begin(
            run_id=RUN_ID,
            transaction_id="TX-UNBOUND-EVENT-002",
            transaction_type="event_ownership_unbound",
            expected_revision=1,
        )
        unbound.append_event(unbound_event)
        unbound_receipt = unbound.commit()
        assert unbound_receipt.event_ids == [unbound_event.event_id]
        assert store.load_snapshot(RUN_ID).events == (
            records.event,
            unbound_event,
        )


def test_uow_stages_detached_snapshots_for_every_typed_record(
    tmp_path: Path,
) -> None:
    records = _records()
    expected = _records()
    with _create_store(tmp_path) as store:
        unit = _stage_all(store, records)
        staged_fingerprint = unit._fingerprint(unit._identity_snapshot())

        assert unit._run is not records.run
        assert unit._stage_states[records.stage.stage_id] is not records.stage
        assert (
            unit._invocations[records.invocation.invocation_id]
            is not records.invocation
        )
        assert unit._artifacts[records.artifact.artifact_id] is not records.artifact
        assert unit._artifact_revisions[0].record is not records.revision
        assert unit._events[0] is not records.event
        assert unit._approvals[records.approval.approval_id] is not records.approval
        assert unit._deliveries[records.delivery.delivery_id] is not records.delivery

        records.run.runtime = "claude"
        records.stage.status = "blocked"
        records.invocation.status = "failed"
        records.artifact.current_revision = 99
        records.revision.sha256 = "0" * 64
        records.event.transaction_id = "TX-FOREIGN-MUTATION-001"
        nested_metadata = records.event.metadata["a"]
        assert isinstance(nested_metadata, dict)
        nested_metadata["finite"] = 99.0
        records.approval.decision = "reject"
        records.delivery.status = "failed"

        assert unit._fingerprint(unit._identity_snapshot()) == staged_fingerprint
        receipt = unit.commit()
        snapshot = store.load_snapshot(RUN_ID)

        assert receipt.event_ids == [expected.event.event_id]
        assert snapshot.run == expected.run
        assert snapshot.stage_states == (expected.stage,)
        assert snapshot.invocations == (expected.invocation,)
        assert snapshot.artifacts == (expected.artifact,)
        assert snapshot.artifact_revisions == (expected.revision,)
        assert snapshot.events == (expected.event,)
        assert snapshot.approvals == (expected.approval,)
        assert snapshot.deliveries == (expected.delivery,)


def test_staged_event_snapshot_cannot_be_rebound_before_commit(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        transaction_id = "TX-EVENT-SNAPSHOT-002"
        event = _records(transaction_id=transaction_id).event.model_copy(
            update={"event_id": "EVT-EVENT-SNAPSHOT-002"},
            deep=True,
        )
        expected_event = event.model_copy(deep=True)
        unit = store.begin(
            run_id=RUN_ID,
            transaction_id=transaction_id,
            transaction_type="event_snapshot_ownership",
            expected_revision=1,
        )
        unit.append_event(event)
        staged_fingerprint = unit._fingerprint(unit._identity_snapshot())

        event.transaction_id = TRANSACTION_ID
        nested_metadata = event.metadata["a"]
        assert isinstance(nested_metadata, dict)
        nested_metadata["finite"] = 99.0

        assert unit._fingerprint(unit._identity_snapshot()) == staged_fingerprint
        receipt = unit.commit()
        persisted_event = store.load_snapshot(RUN_ID).events[-1]
        event_owner = store._connection.execute(
            "SELECT transaction_id FROM events WHERE event_id = ?",
            (expected_event.event_id,),
        ).fetchone()
        receipt_owner = store._connection.execute(
            "SELECT transaction_id FROM transaction_events WHERE event_id = ?",
            (expected_event.event_id,),
        ).fetchone()

        assert receipt.transaction_id == transaction_id
        assert receipt.event_ids == [expected_event.event_id]
        assert persisted_event == expected_event
        assert tuple(event_owner) == (transaction_id,)
        assert tuple(receipt_owner) == (transaction_id,)


def test_illegally_mutated_model_is_revalidated_before_staging(
    tmp_path: Path,
) -> None:
    event = _records().event
    event.transaction_id = "invalid transaction id"
    with _create_store(tmp_path) as store:
        unit = store.begin(
            run_id=RUN_ID,
            transaction_id=TRANSACTION_ID,
            transaction_type="invalid_mutated_record",
            expected_revision=0,
        )
        with pytest.raises(ControlStoreIntegrityError) as error:
            unit.append_event(event)
        assert error.value.code == "control_record_invalid"
        assert str(error.value) == "control_record_invalid"
        assert unit._events == []
        assert unit._event_ids == set()
        unit.rollback()
        assert store.current_revision == 0
        assert _table_count(store, "events") == 0
        assert _table_count(store, "transactions") == 0


def test_relational_cross_run_binding_rolls_back_entire_transaction(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        other_run_id = "RUN-20260715-OTHER"
        other = _records(
            run_id=other_run_id,
            transaction_id="TX-CROSS-RUN-002",
        )
        unit = store.begin(
            run_id=other_run_id,
            transaction_id="TX-CROSS-RUN-002",
            transaction_type="invalid_cross_run_delivery",
            expected_revision=1,
        )
        unit.put_run(other.run)
        unit.put_delivery(
            other.delivery.model_copy(
                update={"approval_id": None, "artifact_id": "brief"}
            )
        )
        with pytest.raises(ControlStoreConflict) as error:
            unit.commit()
        assert error.value.code == "relational_integrity_conflict"
        assert store.current_revision == 1
        assert (
            store._connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (other_run_id,)
            ).fetchone()
            is None
        )


def test_artifact_current_revision_requires_exact_committed_revision(
    tmp_path: Path,
) -> None:
    records = _records()
    with _create_store(tmp_path) as store:
        unit = store.begin(
            run_id=RUN_ID,
            transaction_id="TX-MISSING-BLOB-ROW",
            transaction_type="invalid_artifact_binding",
            expected_revision=0,
        )
        unit.put_run(records.run)
        unit.put_artifact(records.artifact)
        with pytest.raises(ControlStoreConflict) as error:
            unit.commit()
        assert error.value.code == "relational_integrity_conflict"
        assert store.current_revision == 0
        assert _table_count(store, "runs") == 0
        assert _table_count(store, "artifacts") == 0


def test_unbound_artifact_revision_is_rejected_before_blob_write(
    tmp_path: Path,
) -> None:
    records = _records()
    with _create_store(tmp_path) as store:
        unit = store.begin(
            run_id=RUN_ID,
            transaction_id="TX-UNBOUND-REVISION-001",
            transaction_type="invalid_artifact_binding",
            expected_revision=0,
        )
        unit.put_run(records.run)
        unit.put_artifact_revision(records.revision, BLOB)
        with pytest.raises(ControlStoreConflict) as error:
            unit.commit()
        assert error.value.code == "relational_integrity_conflict"
        assert str(error.value) == "relational_integrity_conflict"
        assert store.current_revision == 0
        assert _table_count(store, "runs") == 0
        assert _table_count(store, "artifact_revisions") == 0
        assert list(store.blob_root.rglob("*")) == []


def test_artifact_subgraph_preflight_is_independent_of_staging_order(
    tmp_path: Path,
) -> None:
    records = _records(transaction_id="TX-REVISION-FIRST-001")
    with _create_store(tmp_path) as store:
        unit = store.begin(
            run_id=RUN_ID,
            transaction_id=records.event.transaction_id,
            transaction_type="revision_first",
            expected_revision=0,
        )
        unit.put_run(records.run)
        unit.put_artifact_revision(records.revision, BLOB)
        unit.put_artifact(records.artifact)
        receipt = unit.commit()
        snapshot = store.load_snapshot(RUN_ID)
        assert receipt.artifact_revisions[0].artifact_id == "brief"
        assert snapshot.artifacts == (records.artifact,)
        assert snapshot.artifact_revisions == (records.revision,)
        assert store.scan_orphans().orphan_hashes == ()


def test_existing_revision_key_conflict_is_rejected_before_new_blob_write(
    tmp_path: Path,
) -> None:
    conflicting_content = b"Conflicting bytes for an existing revision key.\n"
    conflicting_sha256 = hashlib.sha256(conflicting_content).hexdigest()
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        unit = store.begin(
            RUN_ID,
            "TX-DUPLICATE-REVISION-002",
            "duplicate_revision",
            1,
        )
        unit.put_artifact_revision(
            _records().revision.model_copy(
                update={
                    "path": f"output/artifacts/{conflicting_sha256}/brief.md",
                    "sha256": conflicting_sha256,
                    "size_bytes": len(conflicting_content),
                }
            ),
            conflicting_content,
        )
        with pytest.raises(ControlStoreConflict) as error:
            unit.commit()
        assert error.value.code == "relational_integrity_conflict"
        assert store.current_revision == 1
        assert not store._blob_path(conflicting_sha256).exists()
        assert store.scan_orphans().orphan_hashes == ()


@pytest.mark.parametrize(
    "failure_stage",
    [
        "before_blob_write",
        "after_blob_write",
        "after_begin",
        "after_records",
        "before_commit",
    ],
)
def test_failure_injection_rolls_back_sql_and_only_allows_blob_orphan(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    class InjectedFailure(RuntimeError):
        pass

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise InjectedFailure(stage)

    store = _create_store(tmp_path, failure_hook=fail)
    try:
        with pytest.raises(InjectedFailure):
            _stage_all(store).commit()
        assert store.current_revision == 0
        assert _table_count(store, "runs") == 0
        assert _table_count(store, "transactions") == 0
        assert _table_count(store, "artifact_revisions") == 0
        scan = store.scan_orphans()
        expected = () if failure_stage == "before_blob_write" else (BLOB_SHA256,)
        assert scan.orphan_hashes == expected
        assert not list(tmp_path.glob("*.json"))
    finally:
        store.close()
    reopened = SQLiteControlStore.open(tmp_path / "control.db")
    assert reopened.current_revision == 0
    reopened.close()


def test_blob_deleted_before_sql_commit_cannot_receive_committed_row(
    tmp_path: Path,
) -> None:
    store = _create_store(tmp_path)

    def remove_blob(stage: str) -> None:
        if stage == "before_commit":
            store._blob_path(BLOB_SHA256).unlink()

    store._failure_hook = remove_blob
    try:
        with pytest.raises(ControlStoreIntegrityError) as error:
            _stage_all(store).commit()
        assert error.value.code == "committed_blob_missing"
        assert store.current_revision == 0
        assert _table_count(store, "artifact_revisions") == 0
        assert _table_count(store, "transactions") == 0
    finally:
        store.close()


def test_orphan_scan_is_report_only_and_never_accepts_or_deletes(
    tmp_path: Path,
) -> None:
    class InjectedFailure(RuntimeError):
        pass

    def fail(stage: str) -> None:
        if stage == "after_blob_write":
            raise InjectedFailure(stage)

    with _create_store(tmp_path, failure_hook=fail) as store:
        with pytest.raises(InjectedFailure):
            _stage_all(store).commit()
        malformed = store.blob_root / "unowned.bin"
        malformed.write_bytes(b"unowned")
        first = store.scan_orphans()
        second = store.scan_orphans()
        assert first == second
        assert first.orphan_hashes == (BLOB_SHA256,)
        assert first.malformed_paths == ("unowned.bin",)
        assert store._blob_path(BLOB_SHA256).read_bytes() == BLOB
        assert malformed.read_bytes() == b"unowned"
        assert _table_count(store, "artifact_revisions") == 0


@pytest.mark.parametrize(
    ("failure_stage", "expected_orphans"),
    [
        ("before_blob_write", ()),
        ("after_blob_write", (BLOB_SHA256,)),
    ],
)
def test_real_process_exit_before_db_commit_never_creates_committed_records(
    tmp_path: Path,
    failure_stage: str,
    expected_orphans: tuple[str, ...],
) -> None:
    store = _create_store(tmp_path)
    database = store.path
    store.close()

    _run_crash_subprocess(database, failure_stage)

    with SQLiteControlStore.open(database) as reopened:
        assert reopened.current_revision == 0
        assert _table_count(reopened, "runs") == 0
        assert _table_count(reopened, "transactions") == 0
        assert _table_count(reopened, "artifact_revisions") == 0
        assert reopened.scan_orphans().orphan_hashes == expected_orphans
        assert reopened._connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        with pytest.raises(ControlStoreStateError) as error:
            reopened.load_snapshot(RUN_ID)
        assert error.value.code == "run_not_found"


def test_real_process_exit_after_commit_reopens_and_exactly_replays_receipt(
    tmp_path: Path,
) -> None:
    store = _create_store(tmp_path)
    database = store.path
    store.close()

    _run_crash_subprocess(database, "after_commit")

    with SQLiteControlStore.open(database, clock=lambda: COMMITTED_AT) as reopened:
        snapshot = reopened.load_snapshot(RUN_ID)
        assert snapshot.store_revision == 1
        assert len(snapshot.transactions) == 1
        receipt = snapshot.transactions[0]
        assert receipt.transaction_id == CRASH_TRANSACTION_ID
        assert reopened._blob_path(BLOB_SHA256).read_bytes() == BLOB
        assert reopened._connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert _stage_crash_boundary_unit(reopened).commit() == receipt
        assert reopened.current_revision == 1
        assert _table_count(reopened, "transactions") == 1


def test_event_revision_and_approval_rows_are_append_only(tmp_path: Path) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        connection = sqlite3.connect(store.path)
        try:
            for statement in (
                "UPDATE events SET reason = 'changed'",
                "DELETE FROM artifact_revisions",
                "UPDATE approvals SET decision = 'reject'",
            ):
                with pytest.raises(sqlite3.IntegrityError, match="append_only"):
                    connection.execute(statement)
                connection.rollback()
        finally:
            connection.close()
        snapshot = store.load_snapshot(RUN_ID)
        assert snapshot.events == (_records().event,)
        assert snapshot.artifact_revisions == (_records().revision,)
        assert snapshot.approvals == (_records().approval,)


def test_duplicate_immutable_identity_rolls_back_without_new_revision(
    tmp_path: Path,
) -> None:
    records = _records()
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        duplicate = store.begin(
            run_id=RUN_ID,
            transaction_id="TX-DUPLICATE-EVENT-002",
            transaction_type="duplicate_event",
            expected_revision=1,
        )
        duplicate.append_event(
            records.event.model_copy(
                update={"transaction_id": "TX-DUPLICATE-EVENT-002"}
            )
        )
        with pytest.raises(ControlStoreConflict) as error:
            duplicate.commit()
        assert error.value.code == "relational_integrity_conflict"
        assert store.current_revision == 1
        assert _table_count(store, "events") == 1
        assert _table_count(store, "transactions") == 1


def test_mutable_row_payload_corruption_is_rejected_on_load(tmp_path: Path) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        store._connection.execute(
            "UPDATE stage_states SET payload_json = '{}' WHERE run_id = ?",
            (RUN_ID,),
        )
        with pytest.raises(ControlStoreIntegrityError) as error:
            store.load_snapshot(RUN_ID)
        assert error.value.code == "stored_payload_invalid"


def test_reopen_rejects_missing_or_changed_committed_blob(tmp_path: Path) -> None:
    store = _create_store(tmp_path)
    _stage_all(store).commit()
    blob_path = store._blob_path(BLOB_SHA256)
    store.close()
    blob_path.write_bytes(b"changed")
    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.open(tmp_path / "control.db")
    assert error.value.code == "committed_blob_size_mismatch"


@pytest.mark.parametrize("symlink_level", ["blob_root", "sha256", "prefix"])
def test_symlinked_blob_directory_rejects_write_before_database_binding(
    tmp_path: Path,
    symlink_level: str,
) -> None:
    with _create_store(tmp_path) as store:
        outside = tmp_path / f"outside-{symlink_level}"
        if symlink_level == "blob_root":
            store.blob_root.rename(outside)
            _symlink_directory(store.blob_root, outside)
        else:
            outside.mkdir()
            hash_root = store.blob_root / "sha256"
            if symlink_level == "sha256":
                _symlink_directory(hash_root, outside)
            else:
                hash_root.mkdir()
                _symlink_directory(hash_root / BLOB_SHA256[:2], outside)

        with pytest.raises(ControlStoreIntegrityError) as error:
            _stage_all(store).commit()
        assert error.value.code == "blob_topology_invalid"
        assert str(error.value) == "blob_topology_invalid"
        assert store.current_revision == 0
        assert _table_count(store, "runs") == 0
        assert _table_count(store, "artifact_revisions") == 0
        assert _table_count(store, "transactions") == 0
        assert list(outside.iterdir()) == []


def test_committed_prefix_symlink_blocks_load_and_reopen(
    tmp_path: Path,
) -> None:
    store = _create_store(tmp_path)
    _stage_all(store).commit()
    database = store.path
    blob_path = store._blob_path(BLOB_SHA256)
    outside = tmp_path / "outside-committed-prefix"
    prefix = _replace_blob_prefix_with_symlink(blob_path, outside)

    with pytest.raises(ControlStoreIntegrityError) as error:
        store.load_snapshot(RUN_ID)
    assert error.value.code == "blob_topology_invalid"
    assert prefix.is_symlink()
    assert (outside / BLOB_SHA256).read_bytes() == BLOB
    store.close()

    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.open(database)
    assert error.value.code == "blob_topology_invalid"
    assert (outside / BLOB_SHA256).read_bytes() == BLOB


def test_orphan_scan_rejects_prefix_symlink_without_traversing_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-orphans"
    outside.mkdir()
    external_blob = outside / BLOB_SHA256
    external_blob.write_bytes(BLOB)
    with _create_store(tmp_path) as store:
        hash_root = store.blob_root / "sha256"
        hash_root.mkdir()
        _symlink_directory(hash_root / BLOB_SHA256[:2], outside)

        with pytest.raises(ControlStoreIntegrityError) as error:
            store.scan_orphans()
        assert error.value.code == "blob_topology_invalid"
        assert external_blob.read_bytes() == BLOB
        assert _table_count(store, "artifact_revisions") == 0


def test_successful_store_reopens_with_typed_snapshot_and_exact_revision(
    tmp_path: Path,
) -> None:
    store = _create_store(tmp_path)
    receipt = _stage_all(store).commit()
    store.close()

    reopened = SQLiteControlStore.open(tmp_path / "control.db")
    try:
        snapshot = reopened.load_snapshot(RUN_ID)
        assert snapshot.store_revision == 1
        assert snapshot.run == _records().run
        assert snapshot.transactions == (receipt,)
        assert reopened._blob_path(BLOB_SHA256).read_bytes() == BLOB
        assert _stage_all(reopened).commit() == receipt
        assert reopened.current_revision == 1
    finally:
        reopened.close()


def test_load_snapshot_keeps_one_sqlite_read_revision_across_external_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _create_store(tmp_path)
    _stage_all(primary).commit()
    secondary = SQLiteControlStore.open(tmp_path / "control.db")
    original_loader = primary._load_for_run
    committed = False

    def load_and_commit(model_type, table, run_id, order_by, columns):
        nonlocal committed
        values = original_loader(model_type, table, run_id, order_by, columns)
        if model_type is StageState and not committed:
            committed = True
            update = secondary.begin(
                RUN_ID,
                "TX-CONCURRENT-STAGE-002",
                "stage_state_update",
                1,
            )
            update.put_stage_state(
                _records().stage.model_copy(update={"status": "blocked", "revision": 2})
            )
            update.commit()
        return values

    monkeypatch.setattr(primary, "_load_for_run", load_and_commit)
    try:
        snapshot = primary.load_snapshot(RUN_ID)
        assert committed is True
        assert snapshot.store_revision == 1
        assert snapshot.stage_states == (_records().stage,)
        assert len(snapshot.transactions) == 1
        assert primary.current_revision == 2
    finally:
        secondary.close()
        primary.close()


def test_blob_hash_mismatch_is_rejected_before_any_file_or_db_write(
    tmp_path: Path,
) -> None:
    records = _records()
    bad_revision = records.revision.model_copy(update={"sha256": "0" * 64})
    with _create_store(tmp_path) as store:
        unit = store.begin(RUN_ID, "TX-BAD-BLOB", "bad_blob", 0)
        unit.put_run(records.run)
        with pytest.raises(ControlStoreIntegrityError) as error:
            unit.put_artifact_revision(bad_revision, BLOB)
        assert error.value.code == "artifact_blob_hash_mismatch"
        unit.rollback()
        assert store.current_revision == 0
        assert list(store.blob_root.rglob("*")) == []
        assert _table_count(store, "runs") == 0


def test_database_and_blob_paths_must_be_separate(tmp_path: Path) -> None:
    blob_root = tmp_path / "blob-root"
    blob_root.mkdir()
    with pytest.raises(ControlStoreStateError) as error:
        SQLiteControlStore.create(
            blob_root / "control.db",
            workspace_id=WORKSPACE_ID,
            blob_root=blob_root,
        )
    assert error.value.code == "database_blob_paths_overlap"
    assert list(blob_root.iterdir()) == []


def test_invalid_sqlite_file_fails_with_typed_schema_error(tmp_path: Path) -> None:
    path = tmp_path / "invalid.db"
    path.write_bytes(b"not a SQLite database")
    with pytest.raises(ControlStoreSchemaError) as error:
        SQLiteControlStore.open(path)
    assert error.value.code == "connection_configuration_failed"


def test_reopen_rejects_foreign_key_corruption_that_quick_check_misses(
    tmp_path: Path,
) -> None:
    store = _create_store(tmp_path)
    _stage_all(store).commit()
    store.close()
    _corrupt_delivery_foreign_key(tmp_path / "control.db")

    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.open(tmp_path / "control.db")
    assert error.value.code == "database_foreign_key_check_failed"
    assert str(error.value) == "database_foreign_key_check_failed"


def test_reopen_rejects_missing_append_only_trigger_definition(
    tmp_path: Path,
) -> None:
    store = _create_store(tmp_path)
    _stage_all(store).commit()
    store.close()
    _mutate_schema(tmp_path / "control.db", "DROP TRIGGER events_no_update;")

    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.open(tmp_path / "control.db")
    assert error.value.code == "database_schema_definition_mismatch"
    assert str(error.value) == "database_schema_definition_mismatch"


def test_reopen_rejects_noninternal_sqlitex_schema_object(
    tmp_path: Path,
) -> None:
    store = _create_store(tmp_path)
    _stage_all(store).commit()
    store.close()
    _mutate_schema(
        tmp_path / "control.db",
        """
        CREATE TRIGGER sqliteX_extra
        BEFORE UPDATE ON events BEGIN SELECT 1; END;
        """,
    )

    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.open(tmp_path / "control.db")
    assert error.value.code == "database_schema_definition_mismatch"
    assert str(error.value) == "database_schema_definition_mismatch"


def test_future_schema_fails_closed(tmp_path: Path) -> None:
    store = _create_store(tmp_path)
    store.close()
    connection = sqlite3.connect(tmp_path / "control.db")
    connection.execute("PRAGMA user_version = 2")
    connection.close()
    with pytest.raises(ControlStoreSchemaError) as error:
        SQLiteControlStore.open(tmp_path / "control.db")
    assert error.value.code == "future_schema_version"


def test_wal_backup_restore_preserves_latest_revision_and_blob_integrity(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        store._connection.execute("PRAGMA wal_autocheckpoint = 0")
        _stage_all(store).commit()
        updated_stage = _records().stage.model_copy(
            update={"status": "blocked", "revision": 2}
        )
        second = store.begin(
            run_id=RUN_ID,
            transaction_id="TX-STAGE-UPDATE-002",
            transaction_type="stage_state_update",
            expected_revision=1,
        )
        second.put_stage_state(updated_stage)
        second.commit()
        wal_path = store.path.with_name(f"{store.path.name}-wal")
        assert wal_path.is_file()
        assert wal_path.stat().st_size > 0
        backup = store.backup_to(tmp_path / "backup")
        assert backup == tmp_path / "backup"
        assert (backup / "control.db").is_file()
        assert (backup / "blobs").is_dir()
        with SQLiteControlStore.open(
            backup / "control.db",
            blob_root=backup / "blobs",
        ) as verified_backup:
            assert verified_backup.load_snapshot(RUN_ID).store_revision == 2

    restored = SQLiteControlStore.restore_to_new_path(
        tmp_path / "backup",
        tmp_path / "restored.db",
    )
    try:
        snapshot = restored.load_snapshot(RUN_ID)
        assert snapshot.store_revision == 2
        assert snapshot.stage_states == (updated_stage,)
        assert len(snapshot.transactions) == 2
        assert restored._blob_path(BLOB_SHA256).read_bytes() == BLOB
        assert restored._connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        restored.close()


def test_backup_rejects_blob_prefix_symlink_without_copying_target(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "symlink-backup"
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        outside = tmp_path / "outside-backup-prefix"
        _replace_blob_prefix_with_symlink(
            store._blob_path(BLOB_SHA256),
            outside,
        )

        with pytest.raises(ControlStoreIntegrityError) as error:
            store.backup_to(destination)
        assert error.value.code == "blob_topology_invalid"
        assert (outside / BLOB_SHA256).read_bytes() == BLOB
    assert not destination.exists()


def test_backup_rejects_foreign_key_corruption_without_destination(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "invalid-backup"
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        _corrupt_delivery_foreign_key(store.path)
        with pytest.raises(ControlStoreIntegrityError) as error:
            store.backup_to(destination)
        assert error.value.code == "database_foreign_key_check_failed"
        assert str(error.value) == "database_foreign_key_check_failed"
    assert not destination.exists()


def test_backup_rejects_replaced_append_only_trigger_without_destination(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "schema-drift-backup"
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        _mutate_schema(
            store.path,
            """
            DROP TRIGGER events_no_update;
            CREATE TRIGGER events_no_update
            BEFORE UPDATE ON events BEGIN SELECT 1; END;
            """,
        )
        with pytest.raises(ControlStoreIntegrityError) as error:
            store.backup_to(destination)
        assert error.value.code == "database_schema_definition_mismatch"
        assert str(error.value) == "database_schema_definition_mismatch"
    assert not destination.exists()


def test_restore_rejects_foreign_key_corruption_and_cleans_destination(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        backup = store.backup_to(tmp_path / "backup-with-invalid-foreign-key")
    _corrupt_delivery_foreign_key(backup / "control.db")

    destination = tmp_path / "restored-invalid-foreign-key.db"
    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.restore_to_new_path(backup, destination)
    assert error.value.code == "database_foreign_key_check_failed"
    assert str(error.value) == "database_foreign_key_check_failed"
    assert not destination.exists()
    assert not destination.with_name(f"{destination.name}.blobs").exists()


def test_restore_rejects_table_definition_drift_and_cleans_destination(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        backup = store.backup_to(tmp_path / "backup-with-schema-drift")
    _mutate_schema(
        backup / "control.db",
        "ALTER TABLE stage_states ADD COLUMN unexpected_extension TEXT;",
    )

    destination = tmp_path / "restored-schema-drift.db"
    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.restore_to_new_path(backup, destination)
    assert error.value.code == "database_schema_definition_mismatch"
    assert str(error.value) == "database_schema_definition_mismatch"
    assert not destination.exists()
    assert not destination.with_name(f"{destination.name}.blobs").exists()


def test_restore_rejects_incomplete_blob_backup_and_cleans_destination(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        backup = store.backup_to(tmp_path / "backup")
    shutil.rmtree(backup / "blobs")
    (backup / "blobs").mkdir()

    destination = tmp_path / "restored.db"
    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.restore_to_new_path(backup, destination)
    assert error.value.code == "committed_blob_missing"
    assert not destination.exists()
    assert not destination.with_name("restored.db.blobs").exists()


def test_restore_rejects_symlinked_backup_blob_prefix_and_cleans_destination(
    tmp_path: Path,
) -> None:
    with _create_store(tmp_path) as store:
        _stage_all(store).commit()
        backup = store.backup_to(tmp_path / "backup-with-symlink")
    backup_blob = backup / "blobs" / "sha256" / BLOB_SHA256[:2] / BLOB_SHA256
    outside = tmp_path / "outside-restore-prefix"
    _replace_blob_prefix_with_symlink(backup_blob, outside)

    destination = tmp_path / "restored-symlink.db"
    with pytest.raises(ControlStoreIntegrityError) as error:
        SQLiteControlStore.restore_to_new_path(backup, destination)
    assert error.value.code == "blob_topology_invalid"
    assert (outside / BLOB_SHA256).read_bytes() == BLOB
    assert not destination.exists()
    assert not destination.with_name(f"{destination.name}.blobs").exists()


def test_explicit_rollback_closes_uow_without_writing(tmp_path: Path) -> None:
    with _create_store(tmp_path) as store:
        unit = _stage_all(store)
        unit.rollback()
        with pytest.raises(ControlStoreStateError) as error:
            unit.commit()
        assert error.value.code == "unit_of_work_not_active"
        assert store.current_revision == 0
        assert _table_count(store, "runs") == 0


def test_only_merged_control_dtos_are_serializable() -> None:
    proposal = SourceProposal.model_validate(SourceProposal.minimal_example)
    with pytest.raises(ControlStoreIntegrityError) as error:
        canonical_model_text(proposal)
    assert error.value.code == "unsupported_control_record"


def test_migration_resource_matches_packaged_source_text() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "multi_agent_brief"
        / "control_store"
        / "migrations"
        / "0001.sql"
    )
    # Git may materialize the Windows checkout with CRLF, while Python text
    # reads (including importlib.resources) apply universal-newline semantics.
    # The executable migration contract is the exact decoded SQL text, not the
    # platform-specific working-tree newline representation.
    assert migration_sql() == source.read_text(encoding="utf-8")


def test_no_current_production_module_imports_control_store() -> None:
    package_root = Path(__file__).parents[1] / "src" / "multi_agent_brief"
    findings: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        if "control_store" in path.relative_to(package_root).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(
                name == "multi_agent_brief.control_store"
                or name.startswith("multi_agent_brief.control_store.")
                for name in names
            ):
                findings.append(f"{path.relative_to(package_root)}:{node.lineno}")
    assert findings == []


def test_closed_store_rejects_reads_and_new_uow(tmp_path: Path) -> None:
    store = _create_store(tmp_path)
    store.close()
    with pytest.raises(ControlStoreStateError) as error:
        _ = store.current_revision
    assert error.value.code == "store_closed"
    with pytest.raises(ControlStoreStateError) as error:
        store.begin(
            run_id=RUN_ID,
            transaction_id="TX-CLOSED",
            transaction_type="closed",
            expected_revision=0,
        )
    assert error.value.code == "store_closed"


def test_public_store_api_exposes_no_raw_sql_mutation_surface(tmp_path: Path) -> None:
    with _create_store(tmp_path) as store:
        assert not hasattr(store, "execute")
        assert not hasattr(store, "executemany")
        assert not hasattr(store, "cursor")
        assert not hasattr(store, "delete_orphans")
        assert not hasattr(store, "read_run_truth")
        assert not hasattr(store, "export_projection")
