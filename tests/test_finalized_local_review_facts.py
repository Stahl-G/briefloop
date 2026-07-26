from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.test_core_run_v2_terminal import (
    _authorized_finalize_ready_workspace,
    _commit_finalize_gate,
    _commit_finalize_render,
    _finalize_ready_workspace,
)

from multi_agent_brief.contracts.v2 import FinalizeCompleteRequest
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.terminal import CoreRunTerminalService
from multi_agent_brief.runtime_host_v2 import (
    FinalizedLocalReviewFacts,
    FinalizedLocalReviewProjection,
    RuntimeHostError,
    build_finalized_local_review_projection,
)
import multi_agent_brief.runtime_host_v2.projections as projections


def _finalized_local_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, object]:
    workspace, run_id, clock = _authorized_finalize_ready_workspace(
        tmp_path, monkeypatch
    )
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace, run_id, clock
    )
    _gate_receipt, _gate_fingerprint, evaluations = _commit_finalize_gate(
        workspace, run_id, clock, render
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        before = store.load_snapshot(run_id)
        finalize_stage = next(
            item for item in before.stage_states if item.stage_id == "finalize"
        )
    request = FinalizeCompleteRequest.model_validate(
        {
            "schema_version": FinalizeCompleteRequest.schema_id,
            "request_id": "REQ-FINALIZED-LOCAL-REVIEW-FACTS-001",
            "run_id": run_id,
            "render_id": render.render_id,
            "expected_finalize_stage_revision": finalize_stage.revision,
            "gate_evaluation_ids": sorted(item.evaluation_id for item in evaluations),
            "recovery_id": None,
            "expected_store_revision": before.store_revision,
        },
        strict=True,
    )
    result = CoreRunTerminalService(workspace, clock=clock).complete_finalize(request)
    assert (result.status, result.error_code) == ("committed", None)
    return workspace, run_id, clock


def _package_ready_workspace(tmp_path: Path) -> tuple[Path, str, object]:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace, run_id, clock
    )
    _gate_receipt, _gate_fingerprint, evaluations = _commit_finalize_gate(
        workspace, run_id, clock, render
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        before = store.load_snapshot(run_id)
        finalize_stage = next(
            item for item in before.stage_states if item.stage_id == "finalize"
        )
    request = FinalizeCompleteRequest.model_validate(
        {
            "schema_version": FinalizeCompleteRequest.schema_id,
            "request_id": "REQ-PACKAGE-READY-REVIEW-FACTS-001",
            "run_id": run_id,
            "render_id": render.render_id,
            "expected_finalize_stage_revision": finalize_stage.revision,
            "gate_evaluation_ids": sorted(item.evaluation_id for item in evaluations),
            "recovery_id": None,
            "expected_store_revision": before.store_revision,
        },
        strict=True,
    )
    result = CoreRunTerminalService(workspace, clock=clock).complete_finalize(request)
    assert (result.status, result.error_code) == ("committed", None)
    return workspace, run_id, clock


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _context_with_snapshot(context, snapshot, *, artifact_contents=None):
    return context._replace(
        history=replace(
            context.history,
            snapshots=(snapshot,),
            artifact_contents=(
                context.history.artifact_contents
                if artifact_contents is None
                else artifact_contents
            ),
        ),
        verified=replace(context.verified, snapshot=snapshot),
    )


def test_finalized_local_review_projection_binds_exact_history_once_and_is_pure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    before_files = _file_bytes(workspace)
    original = SQLiteControlStore.load_history
    calls = 0

    def counted(store: SQLiteControlStore):
        nonlocal calls
        calls += 1
        return original(store)

    monkeypatch.setattr(SQLiteControlStore, "load_history", counted)
    monkeypatch.setattr(
        SQLiteControlStore,
        "load_workspace_run_head",
        lambda _store: (_ for _ in ()).throw(AssertionError("head reopened")),
    )

    projection = build_finalized_local_review_projection(workspace)

    assert calls == 1
    assert projection.schema_version == FinalizedLocalReviewProjection.schema_id
    assert projection.facts.schema_version == FinalizedLocalReviewFacts.schema_id
    assert projection.facts.workspace_id == "WS-CORE-V2-001"
    assert projection.facts.run_id == run_id
    assert projection.facts.terminal_state == "finalized_local"
    assert projection.facts.report.artifact_id == "reader_brief"
    assert projection.facts.report.markdown_utf8
    assert (
        projection.facts.report.sha256
        == hashlib.sha256(projection.facts.report.markdown_utf8).hexdigest()
    )
    assert projection.local_run.view_state == "finalized"
    assert (
        projection.local_run.reader_brief.markdown_utf8
        == projection.facts.report.markdown_utf8
    )
    assert (
        projection.local_run.reader_brief.revision
        == projection.facts.report.artifact_revision
    )
    assert _file_bytes(workspace) == before_files


def test_finalized_local_review_projection_ignores_legacy_files_and_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    first = build_finalized_local_review_projection(workspace)
    (workspace / "output" / "brief.md").write_text("forged", encoding="utf-8")
    intermediate = workspace / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "quality_gate_report.json").write_text(
        '{"forged":true}', encoding="utf-8"
    )

    second = build_finalized_local_review_projection(workspace)

    assert second == first


def test_finalized_local_review_projection_rejects_nonlocal_terminal(
    tmp_path: Path,
) -> None:
    workspace, _run_id, _clock = _finalize_ready_workspace(tmp_path)

    with pytest.raises(RuntimeHostError, match="run_not_finalized_local"):
        build_finalized_local_review_projection(workspace)


def test_finalized_local_review_projection_rejects_later_package_terminal(
    tmp_path: Path,
) -> None:
    workspace, _run_id, _clock = _package_ready_workspace(tmp_path)

    with pytest.raises(RuntimeHostError, match="run_not_finalized_local"):
        build_finalized_local_review_projection(workspace)


def test_finalized_local_review_contracts_reject_tampered_facts_and_presentation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    projection = build_finalized_local_review_projection(workspace)
    facts_payload = projection.facts.model_dump(mode="python")
    facts_payload["terminal_action_fingerprint"] = "0" * 64

    with pytest.raises(ValidationError):
        FinalizedLocalReviewFacts.model_validate(facts_payload, strict=True)

    projection_payload = projection.model_dump(mode="python")
    projection_payload["local_run"]["view_state"] = "running"
    with pytest.raises(ValidationError):
        FinalizedLocalReviewProjection.model_validate(projection_payload, strict=True)

    duplicate_python = projection.facts.model_dump(mode="python")
    duplicate_json = projection.facts.model_dump(mode="json")
    duplicate_python["gate_bindings"].append(dict(duplicate_python["gate_bindings"][0]))
    duplicate_json["gate_bindings"].append(dict(duplicate_json["gate_bindings"][0]))
    duplicate_python["facts_fingerprint"] = FinalizedLocalReviewFacts.fingerprint_for(
        duplicate_json
    )
    with pytest.raises(ValidationError):
        FinalizedLocalReviewFacts.model_validate(duplicate_python, strict=True)


def test_finalized_local_review_projection_maps_bad_current_reader_to_report_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    context = projections._load_presentation_context(workspace)
    reader = next(
        item
        for item in context.verified.snapshot.artifacts
        if item.artifact_id == "reader_brief"
    )
    bad_snapshot = replace(
        context.verified.snapshot,
        artifacts=tuple(
            item.model_copy(update={"current_revision": item.current_revision + 1})
            if item == reader
            else item
            for item in context.verified.snapshot.artifacts
        ),
    )
    bad_context = _context_with_snapshot(context, bad_snapshot)
    monkeypatch.setattr(
        projections, "_load_presentation_context", lambda _workspace: bad_context
    )

    with pytest.raises(RuntimeHostError, match="final_report_revision_invalid"):
        build_finalized_local_review_projection(workspace)


def test_finalized_local_review_projection_rejects_every_selected_reader_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    context = projections._load_presentation_context(workspace)
    snapshot = context.verified.snapshot
    reader_record = next(
        item for item in snapshot.artifacts if item.artifact_id == "reader_brief"
    )
    reader_revision = next(
        item
        for item in snapshot.artifact_revisions
        if item.artifact_id == "reader_brief"
        and item.revision == reader_record.current_revision
    )

    unfrozen_snapshot = replace(
        snapshot,
        artifact_revisions=tuple(
            item.model_copy(update={"frozen": False})
            if item == reader_revision
            else item
            for item in snapshot.artifact_revisions
        ),
    )
    monkeypatch.setattr(
        projections,
        "_load_presentation_context",
        lambda _workspace: _context_with_snapshot(context, unfrozen_snapshot),
    )
    with pytest.raises(RuntimeHostError, match="final_report_revision_invalid"):
        build_finalized_local_review_projection(workspace)

    path_mismatch_snapshot = replace(
        snapshot,
        artifacts=tuple(
            item.model_copy(update={"path": "output/other-reader.md"})
            if item == reader_record
            else item
            for item in snapshot.artifacts
        ),
    )
    monkeypatch.setattr(
        projections,
        "_load_presentation_context",
        lambda _workspace: _context_with_snapshot(context, path_mismatch_snapshot),
    )
    with pytest.raises(RuntimeHostError, match="final_report_revision_invalid"):
        build_finalized_local_review_projection(workspace)

    corrupted_contents = dict(context.history.artifact_contents)
    corrupted_contents[
        (
            snapshot.run.run_id,
            reader_revision.artifact_id,
            reader_revision.revision,
        )
    ] = b"\xff"
    monkeypatch.setattr(
        projections,
        "_load_presentation_context",
        lambda _workspace: _context_with_snapshot(
            context,
            snapshot,
            artifact_contents=corrupted_contents,
        ),
    )
    with pytest.raises(RuntimeHostError, match="final_report_revision_invalid"):
        build_finalized_local_review_projection(workspace)


def test_finalized_local_review_projection_rejects_receipt_render_and_gate_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    context = projections._load_presentation_context(workspace)
    snapshot = context.verified.snapshot
    finalization = snapshot.finalizations[0]

    wrong_receipt_snapshot = replace(
        snapshot,
        finalizations=(
            finalization.model_copy(
                update={"accepted_transaction_id": "REQ-MISSING-FINALIZATION"}
            ),
        ),
    )
    monkeypatch.setattr(
        projections,
        "_load_presentation_context",
        lambda _workspace: _context_with_snapshot(context, wrong_receipt_snapshot),
    )
    with pytest.raises(RuntimeHostError, match="finalized_local_lineage_invalid"):
        build_finalized_local_review_projection(workspace)

    missing_render_snapshot = replace(snapshot, finalize_renders=())
    monkeypatch.setattr(
        projections,
        "_load_presentation_context",
        lambda _workspace: _context_with_snapshot(context, missing_render_snapshot),
    )
    with pytest.raises(RuntimeHostError, match="finalized_local_lineage_invalid"):
        build_finalized_local_review_projection(workspace)

    declared_gate_ids = set(finalization.finalize_gate_evaluation_ids)
    wrong_batch_snapshot = replace(
        snapshot,
        gate_evaluations=tuple(
            item.model_copy(update={"gate_batch_id": "GATE-BATCH-WRONG"})
            if item.evaluation_id in declared_gate_ids
            else item
            for item in snapshot.gate_evaluations
        ),
    )
    monkeypatch.setattr(
        projections,
        "_load_presentation_context",
        lambda _workspace: _context_with_snapshot(context, wrong_batch_snapshot),
    )
    with pytest.raises(RuntimeHostError, match="finalized_local_lineage_invalid"):
        build_finalized_local_review_projection(workspace)


def test_finalized_local_review_projection_prioritizes_action_terminal_integrity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    context = projections._load_presentation_context(workspace)
    finalization = context.verified.snapshot.finalizations[0]
    malformed_snapshot = replace(
        context.verified.snapshot,
        finalizations=(
            finalization.model_copy(
                update={"accepted_transaction_id": "REQ-MISSING-FINALIZATION"}
            ),
        ),
    )
    monkeypatch.setattr(
        projections,
        "classify_terminal_legality",
        lambda _snapshot: SimpleNamespace(
            package_state="finalized_local",
            terminal_state="rendered",
        ),
    )
    monkeypatch.setattr(
        projections,
        "_load_presentation_context",
        lambda _workspace: _context_with_snapshot(context, malformed_snapshot),
    )

    with pytest.raises(RuntimeHostError, match="control_store_integrity_invalid"):
        build_finalized_local_review_projection(workspace)
