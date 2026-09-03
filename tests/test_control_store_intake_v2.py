from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from multi_agent_brief.contracts.v2 import (
    Invocation,
    RunIdentity,
    StageState,
    SourceProposal,
    WorkspaceRunHead,
)
from multi_agent_brief.control_store import (
    ControlStoreIntegrityError,
    SQLiteControlStore,
)
from multi_agent_brief.intake_v2.errors import IntakeResult
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.intake_v2.policy import (
    SourcePolicyError,
    evaluate_source_eligibility,
)


RUN_ID = "RUN-PR3-001"
WORKSPACE_ID = "WS-PR3-001"
NOW = "2026-07-15T12:00:00Z"
CLOCK = lambda: datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _record(model_type, **values):
    return model_type.model_validate(
        {"schema_version": model_type.schema_id, **values},
        strict=True,
    )


def _write_json(path: Path, payload: dict[str, object]) -> bytes:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _seed_workspace(workspace: Path, *, include_head: bool = True) -> None:
    workspace.mkdir()
    with SQLiteControlStore.create(
        workspace / "briefloop.db",
        workspace_id=WORKSPACE_ID,
        clock=CLOCK,
    ) as store:
        unit = store.begin(RUN_ID, "TX-SEED-001", "private_test_seed", 0)
        unit.put_run(
            _record(
                RunIdentity,
                run_id=RUN_ID,
                workspace_id=WORKSPACE_ID,
                runtime="operator",
                created_at=NOW,
            )
        )
        if include_head:
            unit.put_workspace_run_head(
                _record(
                    WorkspaceRunHead,
                    workspace_id=WORKSPACE_ID,
                    current_run_id=RUN_ID,
                    updated_at=NOW,
                )
            )
        for stage_id in (
            "source-discovery",
            "scout",
            "screener",
            "claim-ledger",
            "auditor",
        ):
            unit.put_stage_state(
                _record(
                    StageState,
                    run_id=RUN_ID,
                    stage_id=stage_id,
                    status="ready",
                    revision=0,
                    updated_at=NOW,
                )
            )
        for invocation_id, role_id in (
            ("INV-SOURCE-001", "source-provider"),
            ("INV-SCOUT-001", "scout"),
            ("INV-SCREEN-001", "scout"),
            ("INV-SCREENER-001", "screener"),
            ("INV-DRAFTS-001", "claim-ledger"),
            ("INV-AUDIT-001", "auditor"),
        ):
            unit.put_invocation(
                _record(
                    Invocation,
                    invocation_id=invocation_id,
                    run_id=RUN_ID,
                    role_id=role_id,
                    runtime="operator",
                    status="active",
                    started_at=NOW,
                )
            )
        unit.commit()


def _source_request(workspace: Path, *, expected_revision: int = 1) -> Path:
    scratch = workspace / "scratch" / "INV-SOURCE-001"
    content = b"Synthetic public filing bytes.\n"
    content_path = scratch / "source_content.pdf"
    content_path.parent.mkdir(parents=True, exist_ok=True)
    content_path.write_bytes(content)
    _write_json(
        scratch / "source_proposal.json",
        {
            "schema_version": "briefloop.source_proposal.v2",
            "proposal_id": "PROP-SOURCE-001",
            "run_id": RUN_ID,
            "source_id": "SRC-001",
            "origin_type": "uploaded_file",
            "acquisition_method": "manual_upload",
            "material_kind": "uploaded_file",
            "provider": None,
            "locator": {
                "kind": "file",
                "path": "scratch/INV-SOURCE-001/source_content.pdf",
            },
            "title": "Synthetic public filing",
            "publisher": None,
            "published_at": None,
            "retrieved_at": NOW,
            "source_category": "regulator",
            "retrieval_source_type": "local_file",
            "underlying_evidence_type": "filing",
            "raw_underlying_evidence_type": None,
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_media_type": "application/pdf",
            "raw_payload_sha256": None,
            "raw_payload_media_type": None,
        },
    )
    request = scratch / "submit_request.json"
    _write_json(
        request,
        {
            "schema_version": "briefloop.source_commit_request.v2",
            "request_id": "REQ-SOURCE-001",
            "run_id": RUN_ID,
            "invocation_id": "INV-SOURCE-001",
            "proposal_path": "scratch/INV-SOURCE-001/source_proposal.json",
            "content_path": "scratch/INV-SOURCE-001/source_content.pdf",
            "raw_payload_path": None,
            "expected_store_revision": expected_revision,
        },
    )
    return request


def _replace_with_external_hardlink(path: Path, *, outside: Path) -> bytes:
    """Replace one workspace leaf with a distinct external hardlink alias."""

    original = path.read_bytes()
    outside.write_bytes(original)
    path.unlink()
    try:
        os.link(outside, path)
    except OSError as exc:
        pytest.skip(f"test filesystem does not support hardlinks: {exc}")
    outside_info = outside.stat()
    path_info = path.stat()
    assert (path_info.st_dev, path_info.st_ino) == (
        outside_info.st_dev,
        outside_info.st_ino,
    )
    assert path_info.st_nlink > 1
    return original


def _candidate_request(workspace: Path, *, expected_revision: int = 2) -> Path:
    scratch = workspace / "scratch" / "INV-SCOUT-001"
    _write_json(
        scratch / "candidate_claims.json",
        {
            "schema_version": "briefloop.candidate_claims_proposal.v2",
            "proposal_id": "PROP-CANDIDATES-001",
            "run_id": RUN_ID,
            "created_at": NOW,
            "candidates": [
                {
                    "candidate_id": "CAND-001",
                    "source_id": "SRC-001",
                    "statement": "A synthetic public filing was supplied.",
                    "evidence_text": "Synthetic public filing bytes.",
                    "topic": "operations",
                    "claim_type": "fact",
                    "confidence": "high",
                }
            ],
        },
    )
    request = scratch / "submit_request.json"
    _write_json(
        request,
        {
            "schema_version": "briefloop.artifact_submit_request.v2",
            "request_id": "REQ-CANDIDATE-001",
            "run_id": RUN_ID,
            "artifact_id": "candidate_claims",
            "invocation_id": "INV-SCOUT-001",
            "input_path": "scratch/INV-SCOUT-001/candidate_claims.json",
            "expected_store_revision": expected_revision,
            "expected_artifact_revision": 0,
        },
    )
    return request


def _snippet_source_request(workspace: Path, *, expected_revision: int = 1) -> Path:
    scratch = workspace / "scratch" / "INV-SOURCE-001"
    content = b"Discovery snippet only."
    raw = b'{"results":[]}'
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "source_content.txt").write_bytes(content)
    (scratch / "source_raw.json").write_bytes(raw)
    _write_json(
        scratch / "source_proposal.json",
        {
            "schema_version": "briefloop.source_proposal.v2",
            "proposal_id": "PROP-SOURCE-SNIPPET",
            "run_id": RUN_ID,
            "source_id": "SRC-SNIPPET",
            "origin_type": "provider_response",
            "acquisition_method": "provider_search",
            "material_kind": "search_snippet",
            "provider": "synthetic-provider",
            "locator": {"kind": "web", "url": "https://example.com/source"},
            "title": "Synthetic discovery snippet",
            "publisher": None,
            "published_at": None,
            "retrieved_at": NOW,
            "source_category": "other",
            "retrieval_source_type": "other",
            "underlying_evidence_type": "unknown",
            "raw_underlying_evidence_type": "provider-search-response",
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_media_type": "text/plain",
            "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_payload_media_type": "application/json",
        },
    )
    request = scratch / "submit_request.json"
    _write_json(
        request,
        {
            "schema_version": "briefloop.source_commit_request.v2",
            "request_id": "REQ-SOURCE-SNIPPET",
            "run_id": RUN_ID,
            "invocation_id": "INV-SOURCE-001",
            "proposal_path": "scratch/INV-SOURCE-001/source_proposal.json",
            "content_path": "scratch/INV-SOURCE-001/source_content.txt",
            "raw_payload_path": "scratch/INV-SOURCE-001/source_raw.json",
            "expected_store_revision": expected_revision,
        },
    )
    return request


def _proposal_request(
    workspace: Path,
    *,
    invocation_id: str,
    request_id: str,
    artifact_id: str,
    payload: dict[str, object],
    expected_store_revision: int,
    expected_artifact_revision: int,
) -> Path:
    scratch = workspace / "scratch" / invocation_id
    proposal_path = scratch / f"{artifact_id}.json"
    _write_json(proposal_path, payload)
    request = scratch / "submit_request.json"
    _write_json(
        request,
        {
            "schema_version": "briefloop.artifact_submit_request.v2",
            "request_id": request_id,
            "run_id": RUN_ID,
            "artifact_id": artifact_id,
            "invocation_id": invocation_id,
            "input_path": proposal_path.relative_to(workspace).as_posix(),
            "expected_store_revision": expected_store_revision,
            "expected_artifact_revision": expected_artifact_revision,
        },
    )
    return request


def test_source_and_candidate_commit_form_first_class_receipt_graph(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    service = IntakeService(workspace, clock=CLOCK)

    source = service.submit_source(
        _source_request(workspace).relative_to(workspace).as_posix()
    )
    candidate = service.submit_proposal(
        "candidate",
        _candidate_request(workspace).relative_to(workspace).as_posix(),
    )

    assert source.status == "committed"
    assert source.receipt is not None
    assert source.receipt.source_ids == ["SRC-001"]
    assert candidate.status == "committed"
    assert candidate.receipt is not None
    assert candidate.receipt.proposal_ids == ["PROP-CANDIDATES-001"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
        assert snapshot.workspace_run_head is not None
        assert snapshot.workspace_run_head.current_run_id == RUN_ID
        assert [item.source_id for item in snapshot.sources] == ["SRC-001"]
        assert [item.proposal_id for item in snapshot.accepted_proposals] == [
            "PROP-CANDIDATES-001"
        ]
        assert [
            (item.proposal_id, item.source_id)
            for item in snapshot.proposal_source_bindings
        ] == [("PROP-CANDIDATES-001", "SRC-001")]
        assert snapshot.store_revision == 3
        backup = store.backup_to(tmp_path / "backup")
    with SQLiteControlStore.open(
        backup / "control.db",
        blob_root=backup / "blobs",
    ) as restored:
        restored_snapshot = restored.load_snapshot(RUN_ID)
        assert restored_snapshot.sources == snapshot.sources
        assert restored_snapshot.accepted_proposals == snapshot.accepted_proposals


def test_discovery_only_source_commits_but_cannot_back_claim_candidate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    service = IntakeService(workspace, clock=CLOCK)

    source = service.submit_source(
        _snippet_source_request(workspace).relative_to(workspace).as_posix()
    )
    candidate_request = _candidate_request(workspace)
    candidate_payload_path = (
        workspace / "scratch" / "INV-SCOUT-001" / "candidate_claims.json"
    )
    candidate_payload = json.loads(candidate_payload_path.read_text(encoding="utf-8"))
    candidate_payload["candidates"][0]["source_id"] = "SRC-SNIPPET"
    _write_json(candidate_payload_path, candidate_payload)
    candidate = service.submit_proposal(
        "candidate",
        candidate_request.relative_to(workspace).as_posix(),
    )

    assert source.status == "committed"
    assert candidate.status == "rejected_recorded"
    assert candidate.error_code == "source_not_claims_eligible"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
        assert snapshot.sources[0].claims_eligible is False
        assert snapshot.sources[0].eligibility_reason == "ineligible_search_snippet"
        assert snapshot.accepted_proposals == ()


def test_new_authority_rows_and_relations_are_append_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    service = IntakeService(workspace, clock=CLOCK)
    service.submit_source(_source_request(workspace).relative_to(workspace).as_posix())
    service.submit_proposal(
        "candidate",
        _candidate_request(workspace).relative_to(workspace).as_posix(),
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        for statement in (
            "UPDATE sources SET title = 'changed'",
            "DELETE FROM accepted_proposals",
            "UPDATE proposal_source_bindings SET source_id = 'OTHER'",
            "DELETE FROM transaction_sources",
            "UPDATE transaction_proposals SET proposal_id = 'OTHER'",
            "DELETE FROM workspace_run_heads",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append_only"):
                store._connection.execute(statement)


def test_exact_replay_returns_original_receipt_without_new_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    service = IntakeService(workspace, clock=CLOCK)
    request = _source_request(workspace).relative_to(workspace).as_posix()

    committed = service.submit_source(request)
    replayed = service.submit_source(request)

    assert committed.status == "committed"
    assert replayed.status == "replayed"
    assert replayed.receipt == committed.receipt
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == 2


def test_failed_request_exactly_replays_and_changed_bytes_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    request_path = _candidate_request(workspace, expected_revision=1)
    relative = request_path.relative_to(workspace).as_posix()
    service = IntakeService(workspace, clock=CLOCK)

    rejected = service.submit_proposal("candidate", relative)
    replayed = service.submit_proposal("candidate", relative)
    proposal_path = workspace / "scratch" / "INV-SCOUT-001" / "candidate_claims.json"
    proposal_path.write_bytes(proposal_path.read_bytes() + b" ")
    conflict = service.submit_proposal("candidate", relative)

    assert rejected.status == "rejected_recorded"
    assert replayed.status == "rejected_recorded"
    assert replayed.receipt == rejected.receipt
    assert conflict.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "submission_replay_conflict",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == 2


def test_missing_explicit_run_head_is_zero_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace, include_head=False)
    request = _source_request(workspace).relative_to(workspace).as_posix()
    before = (workspace / "briefloop.db").read_bytes()

    result = IntakeService(workspace, clock=CLOCK).submit_source(request)

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "current_run_binding_missing",
    }
    assert (workspace / "briefloop.db").read_bytes() == before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == 1


@pytest.mark.parametrize(
    "failure_stage",
    ["before_blob_write", "after_blob_write:1", "after_records"],
)
def test_intake_commit_failure_keeps_invocation_active_and_db_unaccepted(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    workspace = tmp_path / failure_stage.replace(":", "-")
    _seed_workspace(workspace)
    request = _source_request(workspace).relative_to(workspace).as_posix()

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise ControlStoreIntegrityError("injected_intake_failure")

    result = IntakeService(
        workspace,
        clock=CLOCK,
        _store_failure_hook=fail,
    ).submit_source(request)

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "intake_commit_failed",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
        assert store.current_revision == 1
        assert snapshot.sources == ()
        assert snapshot.artifacts == ()
        invocation = next(
            item
            for item in snapshot.invocations
            if item.invocation_id == "INV-SOURCE-001"
        )
        assert invocation.status == "active"
        expected_orphans = 0 if failure_stage == "before_blob_write" else 1
        assert len(store.scan_orphans().orphan_hashes) == expected_orphans


def test_commit_outcome_unknown_intake_result_is_strictly_value_free() -> None:
    result = IntakeResult(
        status="commit_outcome_unknown",
        error_code="commit_outcome_unknown",
    )
    assert result.exit_code == 1
    assert result.to_dict() == {
        "status": "commit_outcome_unknown",
        "error_code": "commit_outcome_unknown",
    }
    with pytest.raises(ValueError, match="invalid intake result shape"):
        IntakeResult(
            status="commit_outcome_unknown",
            error_code="commit_outcome_unknown",
            source_id="must-not-leak",
        )


def test_stale_store_revision_and_unsafe_scratch_are_zero_write(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    stale_request = _source_request(workspace, expected_revision=0)
    service = IntakeService(workspace, clock=CLOCK)

    stale = service.submit_source(stale_request.relative_to(workspace).as_posix())
    assert stale.error_code == "expected_store_revision_conflict"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == 1

    content = workspace / "scratch" / "INV-SOURCE-001" / "source_content.pdf"
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(content.read_bytes())
    content.unlink()
    content.symlink_to(outside)
    unsafe = service.submit_source(stale_request.relative_to(workspace).as_posix())
    assert unsafe.error_code == "scratch_entry_unsafe"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == 1


@pytest.mark.parametrize(
    ("leaf_name", "force_absolute_fallback"),
    [
        ("submit_request.json", False),
        ("source_proposal.json", False),
        ("source_content.pdf", False),
        ("source_raw.json", False),
        ("submit_request.json", True),
        ("source_proposal.json", True),
        ("source_content.pdf", True),
        ("source_raw.json", True),
    ],
)
def test_hardlinked_source_intake_leaves_are_uncommitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    leaf_name: str,
    force_absolute_fallback: bool,
) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    request_path = (
        _snippet_source_request(workspace)
        if leaf_name == "source_raw.json"
        else _source_request(workspace)
    )
    target = request_path.parent / leaf_name
    original = _replace_with_external_hardlink(
        target,
        outside=tmp_path / f"outside-{leaf_name}",
    )
    if force_absolute_fallback:
        monkeypatch.setattr(os, "supports_dir_fd", frozenset())
    database = workspace / "briefloop.db"
    before_bytes = database.read_bytes()
    result = IntakeService(workspace, clock=CLOCK).submit_source(
        request_path.relative_to(workspace).as_posix()
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "scratch_entry_unsafe",
    }
    assert database.read_bytes() == before_bytes
    assert target.read_bytes() == original
    with SQLiteControlStore.open(database) as store:
        assert store.current_revision == 1
        snapshot = store.load_snapshot(RUN_ID)
    assert snapshot.sources == ()
    assert snapshot.artifact_revisions == ()
    assert snapshot.events == ()


def test_all_five_lanes_and_both_screening_owners_commit_without_stage_advance(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    service = IntakeService(workspace, clock=CLOCK)
    before_stages = None
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_stages = store.load_snapshot(RUN_ID).stage_states

    assert service.submit_source(
        _source_request(workspace).relative_to(workspace).as_posix()
    ).status == "committed"
    assert service.submit_proposal(
        "candidate",
        _candidate_request(workspace).relative_to(workspace).as_posix(),
    ).status == "committed"

    screened_default = _proposal_request(
        workspace,
        invocation_id="INV-SCREEN-001",
        request_id="REQ-SCREEN-001",
        artifact_id="screened_candidates",
        expected_store_revision=3,
        expected_artifact_revision=0,
        payload={
            "schema_version": "briefloop.screened_candidates_proposal.v2",
            "proposal_id": "PROP-SCREENED-001",
            "run_id": RUN_ID,
            "candidate_claims_proposal_id": "PROP-CANDIDATES-001",
            "created_at": NOW,
            "decisions": [
                {
                    "candidate_id": "CAND-001",
                    "decision": "selected",
                    "reason_code": None,
                    "explanation": None,
                }
            ],
        },
    )
    assert service.submit_proposal(
        "screened",
        screened_default.relative_to(workspace).as_posix(),
    ).status == "committed"

    screened_strict = _proposal_request(
        workspace,
        invocation_id="INV-SCREENER-001",
        request_id="REQ-SCREEN-STRICT-001",
        artifact_id="screened_candidates",
        expected_store_revision=4,
        expected_artifact_revision=1,
        payload={
            "schema_version": "briefloop.screened_candidates_proposal.v2",
            "proposal_id": "PROP-SCREENED-STRICT-001",
            "run_id": RUN_ID,
            "candidate_claims_proposal_id": "PROP-CANDIDATES-001",
            "created_at": NOW,
            "decisions": [
                {
                    "candidate_id": "CAND-001",
                    "decision": "selected",
                    "reason_code": None,
                    "explanation": None,
                }
            ],
        },
    )
    assert service.submit_proposal(
        "screened",
        screened_strict.relative_to(workspace).as_posix(),
    ).status == "committed"

    drafts = _proposal_request(
        workspace,
        invocation_id="INV-DRAFTS-001",
        request_id="REQ-DRAFTS-001",
        artifact_id="claim_drafts",
        expected_store_revision=5,
        expected_artifact_revision=0,
        payload={
            "schema_version": "briefloop.claim_drafts_proposal.v2",
            "proposal_id": "PROP-DRAFTS-001",
            "run_id": RUN_ID,
            "screened_candidates_proposal_id": "PROP-SCREENED-STRICT-001",
            "created_at": NOW,
            "drafts": [
                {
                    "draft_id": "DRAFT-001",
                    "statement": "A synthetic public filing was supplied.",
                    "evidence_text": "Synthetic public filing bytes.",
                    "source_ids": ["SRC-001"],
                    "claim_type": "fact",
                }
            ],
        },
    )
    assert service.submit_proposal(
        "claim-drafts",
        drafts.relative_to(workspace).as_posix(),
    ).status == "committed"

    audit = _proposal_request(
        workspace,
        invocation_id="INV-AUDIT-001",
        request_id="REQ-AUDIT-001",
        artifact_id="audit_proposal",
        expected_store_revision=6,
        expected_artifact_revision=0,
        payload={
            "schema_version": "briefloop.audit_proposal.v2",
            "proposal_id": "PROP-AUDIT-001",
            "run_id": RUN_ID,
            "artifact_id": "candidate_claims",
            "artifact_revision": 1,
            "decision": "pass",
            "created_at": NOW,
            "findings": [],
        },
    )
    assert service.submit_proposal(
        "audit",
        audit.relative_to(workspace).as_posix(),
    ).status == "committed"

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
        assert snapshot.stage_states == before_stages
        assert snapshot.store_revision == 7
        assert [item.proposal_kind for item in snapshot.accepted_proposals] == [
            "audit",
            "candidate",
            "claim_drafts",
            "screened",
            "screened",
        ]
        assert snapshot.approvals == ()
        assert snapshot.deliveries == ()


def test_audit_requires_current_frozen_same_run_target_revision(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    request = _proposal_request(
        workspace,
        invocation_id="INV-AUDIT-001",
        request_id="REQ-AUDIT-MISSING-TARGET",
        artifact_id="audit_proposal",
        expected_store_revision=1,
        expected_artifact_revision=0,
        payload={
            "schema_version": "briefloop.audit_proposal.v2",
            "proposal_id": "PROP-AUDIT-MISSING-TARGET",
            "run_id": RUN_ID,
            "artifact_id": "candidate_claims",
            "artifact_revision": 1,
            "decision": "pass",
            "created_at": NOW,
            "findings": [],
        },
    )

    result = IntakeService(workspace, clock=CLOCK).submit_proposal(
        "audit",
        request.relative_to(workspace).as_posix(),
    )

    assert result.status == "rejected_recorded"
    assert result.error_code == "audit_target_invalid"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
        assert snapshot.accepted_proposals == ()
        assert snapshot.artifacts == ()


@pytest.mark.parametrize(
    (
        "origin",
        "method",
        "material",
        "provider",
        "raw",
        "eligible",
        "reason",
    ),
    [
        (
            "uploaded_file",
            "manual_upload",
            "uploaded_file",
            None,
            False,
            True,
            "eligible_durable_source_content",
        ),
        (
            "uploaded_file",
            "manual_upload",
            "full_content",
            None,
            False,
            True,
            "eligible_durable_source_content",
        ),
        (
            "manual_evidence",
            "manual_evidence",
            "partial_extract",
            None,
            False,
            True,
            "eligible_durable_source_content",
        ),
        (
            "provider_response",
            "provider_search",
            "search_result",
            "provider",
            True,
            False,
            "ineligible_search_result",
        ),
        (
            "provider_response",
            "provider_extract",
            "full_content",
            "provider",
            True,
            True,
            "eligible_durable_source_content",
        ),
        (
            "authorized_web_fetch",
            "authorized_web_fetch",
            "partial_extract",
            None,
            False,
            True,
            "eligible_durable_source_content",
        ),
        (
            "cached_provider_response",
            "cached_provider_response",
            "search_snippet",
            "provider",
            True,
            False,
            "ineligible_search_snippet",
        ),
        (
            "claim_ledger_derivative",
            "downstream_derivative",
            "downstream_derivative",
            None,
            False,
            False,
            "ineligible_downstream_derivative",
        ),
        (
            "model_summary_derivative",
            "model_generated",
            "model_synthesis",
            None,
            False,
            False,
            "ineligible_model_synthesis",
        ),
        (
            "search_snippet_only",
            "provider_search",
            "search_snippet",
            "provider",
            True,
            False,
            "ineligible_search_snippet",
        ),
        (
            "unknown",
            "unknown",
            "unknown",
            None,
            False,
            False,
            "ineligible_unknown_origin",
        ),
    ],
)
def test_source_eligibility_matrix_is_literal_and_deterministic(
    origin: str,
    method: str,
    material: str,
    provider: str | None,
    raw: bool,
    eligible: bool,
    reason: str,
) -> None:
    proposal = SourceProposal.model_validate(
        {
            "schema_version": SourceProposal.schema_id,
            "proposal_id": "PROP-SOURCE-POLICY",
            "run_id": RUN_ID,
            "source_id": "SRC-POLICY",
            "origin_type": origin,
            "acquisition_method": method,
            "material_kind": material,
            "provider": provider,
            "locator": {
                "kind": "file",
                "path": "scratch/INV-SOURCE-001/source_content.pdf",
            },
            "title": "Synthetic public source",
            "publisher": None,
            "published_at": None,
            "retrieved_at": NOW,
            "source_category": "regulator",
            "retrieval_source_type": "local_file",
            "underlying_evidence_type": "filing",
            "raw_underlying_evidence_type": None,
            "content_sha256": "a" * 64,
            "content_media_type": "application/pdf",
            "raw_payload_sha256": "b" * 64 if raw else None,
            "raw_payload_media_type": "application/json" if raw else None,
        },
        strict=True,
    )
    assert evaluate_source_eligibility(
        proposal,
        raw_payload_present=raw,
    ) == (eligible, reason)


def test_source_policy_rejects_impossible_combination_instead_of_downgrading() -> (
    None
):
    proposal = SourceProposal.model_validate(
        {
            **SourceProposal.minimal_example,
            "origin_type": "uploaded_file",
            "acquisition_method": "provider_search",
            "material_kind": "search_result",
            "provider": "provider",
            "raw_payload_sha256": "b" * 64,
            "raw_payload_media_type": "application/json",
        },
        strict=True,
    )
    with pytest.raises(SourcePolicyError, match="^source_origin_policy_invalid$"):
        evaluate_source_eligibility(proposal, raw_payload_present=True)
