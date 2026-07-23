"""Focused M3 authorized runtime-continuation State x Path rows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.contracts import SchemaRegistry
from multi_agent_brief.contracts.v2 import CoreRunNextAction, IntegrityCheckRequest
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.core_run_v2.errors import CoreRunError, CoreRunResult
from multi_agent_brief.core_run_v2.integrity import (
    RunIntegrityService,
    protected_revision_keys,
    workspace_observation_revision_keys,
)
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.product.init_web.submit import InitWebSubmitter
from multi_agent_brief.runtime_host_v2.codex import workspace_codex_adapter_loader
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.service import RuntimeHostService


def _body(*, authorized: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "workspace_target": "workspace",
        "selections": {
            "company": "ExampleCo",
            "industry_or_theme": "manufacturing",
            "task_objective": "Prepare a public-safe manufacturing brief.",
            "brief_title": "ExampleCo brief",
            "audience": "management",
            "interface_language": "en",
            "output_language": "en",
            "cadence": "weekly",
            "focus_areas": ["operations"],
            "output_formats": ["markdown"],
            "forbidden_sources": [],
            "web_search_mode": "disabled",
            "output_extent": "balanced",
        },
        "human_confirmation": True,
    }
    return {
        "schema_version": "briefloop.init_web.submission.v1",
        "request_id": "REQ-CONTINUE-001" if authorized else "REQ-MANUAL-001",
        "payload": payload,
    }


def _authorized_workspace(tmp_path: Path) -> Path:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    content = b"public durable source\n"
    staged = submitter.stage_upload(
        session_id="init-session",
        filename="source.txt",
        stream=BytesIO(content),
        declared_length=len(content),
    )
    body = _body(authorized=True)
    payload = body["payload"]
    assert isinstance(payload, dict)
    metadata = {
        "source_id": "SRC-INIT-001",
        "expected_content_sha256": staged["sha256"],
        "origin_type": "uploaded_file",
        "acquisition_method": "manual_upload",
        "material_kind": "uploaded_file",
        "provider": None,
        "original_url": None,
        "title": "Public durable source",
        "publisher": "Example publisher",
        "published_at": "2026-07-22",
        "retrieved_at": "2026-07-23T00:00:00Z",
        "source_category": "other",
        "retrieval_source_type": "local_file",
        "underlying_evidence_type": "unknown",
        "raw_underlying_evidence_type": None,
        "document_kind": None,
        "opened_at": None,
        "resolved_at": None,
    }
    bindings = [{"metadata_index": 0, "upload_handle": staged["upload_handle"]}]
    preview = submitter.preview_source_manifest(
        session_id="init-session",
        body={
            "source_manifest_mode": "imported",
            "source_metadata": [metadata],
            "upload_bindings": bindings,
        },
    )
    payload.update(
        {
            "completion_target": "finalized_local",
            "repair_budget": 1,
            "source_manifest_mode": "imported",
            "source_metadata": preview["source_metadata"],
            "source_manifest": preview["source_manifest"],
            "upload_session_id": "init-session",
            "upload_bindings": preview["routing_bindings"],
        }
    )
    status, response = submitter.submit(body)
    assert status == 200 and response["status"] == "committed"
    return tmp_path / "workspace"


def _revision(workspace: Path) -> int:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        return store.load_snapshot(head.current_run_id).store_revision


def _service(workspace: Path) -> RuntimeHostService:
    return RuntimeHostService(
        workspace,
        adapter_loader=workspace_codex_adapter_loader(workspace),
    )


def test_unauthorized_run_returns_typed_zero_write_attention(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    status, _response = submitter.submit(_body(authorized=False))
    assert status == 200
    workspace = tmp_path / "workspace"
    revision = _revision(workspace)

    result = _service(workspace).continue_authorized()

    assert result.status == "needs_human"
    assert result.reason_code == "runtime_continuation_unsupported"
    assert _revision(workspace) == revision


def test_authorized_continue_commits_pack_and_returns_exact_role_work(
    tmp_path: Path,
) -> None:
    workspace = _authorized_workspace(tmp_path)

    result = _service(workspace).continue_authorized()

    assert result.status == "role_work_required"
    assert result.reason_code == "role_work_required"
    assert result.current_stage == "scout"
    assert result.completed_stages >= 2
    assert result.trace.next_action.effect_kind == "invocation_accept_or_fail"
    assert result.trace.envelope_path is not None
    assert (workspace / result.trace.envelope_path).is_file()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.run_execution_authorizations) == 1
    assert len(snapshot.sources) == 1


def test_missing_and_invalid_proposal_are_zero_write(tmp_path: Path) -> None:
    workspace = _authorized_workspace(tmp_path)
    first = _service(workspace).continue_authorized()
    revision = _revision(workspace)

    missing = _service(workspace).continue_authorized()
    assert missing.status == "role_work_required"
    assert _revision(workspace) == revision

    assert first.trace.envelope_path is not None
    envelope = json.loads((workspace / first.trace.envelope_path).read_text())
    scratch = workspace / envelope["scratch_directory"]
    (scratch / "candidate_claims.json").write_text("{}\n", encoding="utf-8")
    invalid = _service(workspace).continue_authorized()

    assert invalid.status == "proposal_invalid"
    assert invalid.reason_code == "runtime_proposal_invalid"
    assert invalid.violations
    assert _revision(workspace) == revision


def test_continue_cli_hides_trace_unless_explicit(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _authorized_workspace(tmp_path)

    assert main(["runtime", "continue", "--workspace", str(workspace)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "role_work_required"
    assert "trace" not in payload

    assert main(
        ["runtime", "continue", "--workspace", str(workspace), "--trace"]
    ) == 0
    traced = json.loads(capsys.readouterr().out)
    assert traced["trace"]["next_action"]["effect_kind"] == "invocation_accept_or_fail"


def test_progress_ceiling_is_zero_write_attention(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    revision = _revision(workspace)
    monkeypatch.setattr(
        service,
        "apply_current",
        lambda _action, **_kwargs: SimpleNamespace(receipt=None),
    )

    result = service.continue_authorized(maximum_progress_attempts=2)

    assert result.status == "needs_attention"
    assert result.reason_code == "runtime_progress_stalled"
    assert _revision(workspace) == revision


def test_stale_deterministic_action_refreshes_without_spending_progress_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    original = service.apply_current
    calls = 0

    def _stale_once(action, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeHostError("runtime_action_stale")
        return original(action, **kwargs)

    monkeypatch.setattr(service, "apply_current", _stale_once)

    result = service.continue_authorized(maximum_progress_attempts=8)

    assert calls >= 2
    assert result.status == "role_work_required"
    assert result.current_stage == "scout"


def test_persistent_invocation_reservation_unknown_returns_typed_attention(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _authorized_workspace(tmp_path)
    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.service.CoreRunService.start_invocation",
        lambda *_args, **_kwargs: CoreRunResult(
            status="commit_outcome_unknown",
            error_code="commit_outcome_unknown",
        ),
    )

    result = _service(workspace).continue_authorized()

    assert result.status == "needs_attention"
    assert result.reason_code == "commit_outcome_unknown"


def test_persistent_proposal_accept_unknown_returns_typed_attention(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    required = service.continue_authorized()
    assert required.status == "role_work_required"
    _write_current_role_proposal(workspace, required)
    monkeypatch.setattr(
        IntakeService,
        "submit_proposal",
        lambda *_args, **_kwargs: SimpleNamespace(status="commit_outcome_unknown"),
    )

    result = service.continue_authorized()

    assert result.status == "needs_attention"
    assert result.reason_code == "commit_outcome_unknown"


def test_committed_proposal_accept_unknown_replays_identity_before_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    required = service.continue_authorized()
    assert required.status == "role_work_required"
    _write_current_role_proposal(workspace, required)
    original = IntakeService.submit_proposal
    calls: list[tuple[str, str]] = []
    committed_transaction_id: str | None = None

    def _commit_then_unknown(instance, lane, request_path):
        nonlocal committed_transaction_id
        calls.append((lane, request_path))
        result = original(instance, lane, request_path)
        if len(calls) == 1:
            assert result.receipt is not None
            committed_transaction_id = result.receipt.transaction_id
            return SimpleNamespace(status="commit_outcome_unknown")
        return result

    monkeypatch.setattr(IntakeService, "submit_proposal", _commit_then_unknown)

    result = service.continue_authorized()

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert committed_transaction_id is not None
    assert committed_transaction_id in result.trace.transaction_ids
    assert result.status == "role_work_required"


def test_finalize_continuation_uses_core_effect_without_reader_html_hook(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def _action(*, kind: str, effect: str, reason: str, revision: int):
        payload: dict[str, object] = {
            "schema_version": "briefloop.core_run_next_action.v2",
            "run_id": "RUN-TEST",
            "store_revision": revision,
            "action_kind": kind,
            "effect_kind": effect,
            "stage_id": None,
            "role_id": None,
            "source_route_id": None,
            "source_provider_id": None,
            "reason_code": reason,
            "input_artifacts": [],
            "request_schema_id": None,
            "adapter_binding_fingerprint": "a" * 64,
            "source_plan_fingerprint": "b" * 64,
        }
        payload["action_fingerprint"] = canonical_fingerprint(payload)
        return CoreRunNextAction.model_validate(payload, strict=True)

    def _current(action, revision: int):
        snapshot = SimpleNamespace(
            run=SimpleNamespace(run_id="RUN-TEST"),
            store_revision=revision,
            stage_states=[],
            run_execution_authorizations=[object()],
        )
        return SimpleNamespace(
            action=action,
            verified=SimpleNamespace(snapshot=snapshot),
        )

    first_action = _action(
        kind="deterministic",
        effect="finalize_complete",
        reason="finalize_completion_required",
        revision=8,
    )
    complete_action = _action(
        kind="complete",
        effect="finalized_local",
        reason="local_finalization_complete",
        revision=9,
    )
    currents = iter((_current(first_action, 8), _current(complete_action, 9)))
    monkeypatch.setattr(
        "multi_agent_brief.runtime_host_v2.service.initialize_or_open_runtime",
        lambda *_args, **_kwargs: next(currents),
    )
    service = RuntimeHostService(workspace, adapter_loader=lambda _runtime: None)
    presentation_flags: list[bool] = []
    monkeypatch.setattr(
        service,
        "apply_current",
        lambda _action, *, presentation_hook=True: (
            presentation_flags.append(presentation_hook)
            or SimpleNamespace(receipt=SimpleNamespace(transaction_id="TX-FINAL"))
        ),
    )

    result = service.continue_authorized()

    assert result.status == "finalized_local"
    assert result.reason_code == "local_finalization_complete"
    assert result.trace.transaction_ids == ["TX-FINAL"]
    assert presentation_flags == [False]


def _write_current_role_proposal(
    workspace: Path,
    result,
    *,
    initial_editor_repetitions: int = 210,
    repair_editor_repetitions: int = 210,
) -> None:
    assert result.trace.envelope_path is not None
    envelope_path = workspace / result.trace.envelope_path
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    scratch = workspace / envelope["scratch_directory"]
    role_id = envelope["role_id"]
    run_id = envelope["run_id"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
    if role_id == "scout":
        payload = SchemaRegistry.example(
            "briefloop.candidate_claims_proposal.v2", "minimal"
        )
        payload["run_id"] = run_id
        payload["proposal_id"] = "PROP-M3-CANDIDATES"
        payload["candidates"][0].update(
            source_id=snapshot.sources[0].source_id,
            statement="ExampleCo opened a public pilot facility.",
            evidence_text="public durable source",
        )
        filename = "candidate_claims.json"
    elif role_id == "screener":
        candidate = next(
            item
            for item in snapshot.accepted_proposals
            if item.proposal_kind == "candidate"
        )
        payload = SchemaRegistry.example(
            "briefloop.screened_candidates_proposal.v2", "minimal"
        )
        payload.update(
            run_id=run_id,
            proposal_id="PROP-M3-SCREENED",
            candidate_claims_proposal_id=candidate.proposal_id,
        )
        payload["decisions"][0]["candidate_id"] = "CAND-001"
        filename = "screened_candidates.json"
    elif role_id == "claim-ledger":
        screened = next(
            item
            for item in snapshot.accepted_proposals
            if item.proposal_kind == "screened"
        )
        payload = SchemaRegistry.example(
            "briefloop.claim_drafts_proposal.v2", "minimal"
        )
        payload.update(
            run_id=run_id,
            proposal_id="PROP-M3-DRAFTS",
            screened_candidates_proposal_id=screened.proposal_id,
        )
        payload["drafts"][0]["source_ids"] = [snapshot.sources[0].source_id]
        filename = "claim_drafts.json"
    elif role_id in {"analyst", "editor"}:
        repairing = role_id == "editor" and bool(snapshot.gate_repair_cycles)
        repetitions = (
            repair_editor_repetitions if repairing else initial_editor_repetitions
        )
        body = (
            "# ExampleCo public brief\n\n## Executive Summary\n\n"
            + " ".join(["ExampleCo operations context"] * repetitions)
            + " ExampleCo opened a public pilot facility. [src:CL-0001]\n"
        )
        (scratch / ("analyst_draft.md" if role_id == "analyst" else "audited_brief.md")).write_text(
            body,
            encoding="utf-8",
        )
        return
    elif role_id == "auditor":
        payload = deepcopy(
            SchemaRegistry.example("briefloop.audit_proposal.v2", "minimal")
        )
        payload.update(
            run_id=run_id,
            proposal_id=(
                "PROP-M4-AUDIT-REPAIR"
                if snapshot.gate_repair_cycles
                else "PROP-M3-AUDIT"
            ),
            artifact_id="audited_brief",
            artifact_revision=next(
                item.current_revision
                for item in snapshot.artifacts
                if item.artifact_id == "audited_brief"
            ),
            decision="pass",
            findings=[],
        )
        filename = "audit_proposal.json"
    else:  # pragma: no cover - the frozen single-session topology is exhaustive.
        raise AssertionError(role_id)
    (scratch / filename).write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )


def test_authorized_current_session_reaches_truthful_finalized_local(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    sequence: list[tuple[str, str, str]] = []

    for _ in range(8):
        result = service.continue_authorized()
        sequence.append(
            (
                result.status,
                result.reason_code,
                result.trace.next_action.action_fingerprint,
            )
        )
        if result.status == "finalized_local":
            break
        assert result.status == "role_work_required", (
            result.reason_code,
            result.trace.next_action.action_kind,
            result.trace.next_action.effect_kind,
            result.trace.next_action.reason_code,
            result.trace.transaction_ids,
        )
        _write_current_role_proposal(workspace, result)
    else:
        raise AssertionError("authorized current-session run did not terminate")

    assert result.reason_code == "local_finalization_complete"
    assert [item[0] for item in sequence] == [
        "role_work_required",
        "role_work_required",
        "role_work_required",
        "role_work_required",
        "role_work_required",
        "role_work_required",
        "finalized_local",
    ]
    assert all(len(item[2]) == 64 for item in sequence)
    assert result.trace.next_action.action_kind == "complete"
    assert result.trace.next_action.effect_kind == "finalized_local"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert not snapshot.package_ready_records
    assert not snapshot.approvals
    assert not snapshot.delivery_authorizations
    assert not snapshot.delivery_attempts
    assert not snapshot.delivery_results


def test_authorized_editor_gate_repair_runs_once_then_finalizes_local(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    role_sequence: list[str] = []

    for _ in range(12):
        result = service.continue_authorized()
        if result.status == "finalized_local":
            break
        assert result.status == "role_work_required", (
            result.reason_code,
            result.trace.next_action.action_kind,
            result.trace.next_action.effect_kind,
            result.trace.next_action.reason_code,
            result.trace.transaction_ids,
        )
        assert result.trace.envelope_path is not None
        envelope = json.loads(
            (workspace / result.trace.envelope_path).read_text(encoding="utf-8")
        )
        role_sequence.append(envelope["role_id"])
        _write_current_role_proposal(
            workspace,
            result,
            initial_editor_repetitions=20,
            repair_editor_repetitions=210,
        )
    else:
        raise AssertionError("authorized Gate repair did not terminate")

    assert role_sequence == [
        "scout",
        "screener",
        "claim-ledger",
        "analyst",
        "editor",
        "auditor",
        "editor",
        "auditor",
    ]
    assert result.reason_code == "local_finalization_complete"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.gate_repair_cycles) == 1
    assert len(snapshot.gate_repair_artifact_bindings) == 1
    assert len(snapshot.gate_repair_outcomes) == 1
    cycle = snapshot.gate_repair_cycles[0]
    binding = snapshot.gate_repair_artifact_bindings[0]
    outcome = snapshot.gate_repair_outcomes[0]
    assert cycle.repair_ordinal == 1
    assert binding.prior_artifact.revision == 1
    assert binding.successor_artifact.revision == 2
    assert outcome.disposition == "passed"
    assert not snapshot.repair_cycles
    assert not snapshot.artifact_supersessions
    assert not snapshot.repair_completions
    assert not snapshot.recovery_completions
    assert not snapshot.package_ready_records
    assert not snapshot.approvals
    assert not snapshot.delivery_authorizations
    assert not snapshot.delivery_attempts
    assert not snapshot.delivery_results

    editor_completions = sorted(
        (
            item
            for item in snapshot.stage_transitions
            if item.stage_id == "editor" and item.transition_kind == "complete"
        ),
        key=lambda item: item.result_revision,
    )
    assert len(editor_completions) == 2

    def editor_binding_signature(transition_id: str):
        return {
            (item.artifact_id, item.artifact_revision, item.usage)
            for item in snapshot.stage_artifact_bindings
            if item.transition_id == transition_id
        }

    assert editor_binding_signature(editor_completions[0].transition_id) == {
        ("analyst_draft_snapshot", 1, "consumed"),
        ("audited_brief", 1, "produced"),
    }
    assert editor_binding_signature(editor_completions[1].transition_id) == {
        ("audited_brief", 1, "consumed"),
        ("audited_brief", 2, "produced"),
    }

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, snapshot.run.run_id)
        history = store.load_history()
    revisions = {
        (item.artifact_id, item.revision): item for item in snapshot.artifact_revisions
    }
    prior = revisions[("audited_brief", 1)]
    successor = revisions[("audited_brief", 2)]
    prior_key = ("audited_brief", 1)
    successor_key = ("audited_brief", 2)
    analyst = revisions[("analyst_draft_snapshot", 1)]

    repair_consumed = next(
        item
        for item in snapshot.stage_artifact_bindings
        if item.transition_id == editor_completions[1].transition_id
        and item.usage == "consumed"
    )
    ordinary_consumed = next(
        item
        for item in snapshot.stage_artifact_bindings
        if item.transition_id == editor_completions[0].transition_id
        and item.usage == "consumed"
    )
    submission = next(
        item
        for item in snapshot.owned_artifact_submissions
        if item.submission_id == binding.owned_artifact_submission_id
    )
    lineage_forgeries = (
        replace(snapshot, gate_repair_artifact_bindings=()),
        replace(
            snapshot,
            gate_repair_artifact_bindings=(
                binding.model_copy(
                    update={
                        "gate_repair_id": "GATE-REPAIR-CROSS",
                    }
                ),
            ),
        ),
        replace(
            snapshot,
            gate_repair_artifact_bindings=(
                binding.model_copy(
                    update={
                        "prior_artifact": binding.prior_artifact.model_copy(
                            update={"revision": 2}
                        ),
                    }
                ),
            ),
        ),
        replace(
            snapshot,
            gate_repair_artifact_bindings=(
                binding.model_copy(
                    update={
                        "successor_artifact": binding.successor_artifact.model_copy(
                            update={"revision": 1}
                        ),
                    }
                ),
            ),
        ),
        replace(
            snapshot,
            owned_artifact_submissions=tuple(
                item.model_copy(update={"parent_artifact": binding.successor_artifact})
                if item.submission_id == submission.submission_id
                else item
                for item in snapshot.owned_artifact_submissions
            ),
        ),
        replace(
            snapshot,
            stage_artifact_bindings=tuple(
                item.model_copy(
                    update={
                        "artifact_id": analyst.artifact_id,
                        "artifact_revision": analyst.revision,
                        "artifact_sha256": analyst.sha256,
                    }
                )
                if (
                    item.transition_id == repair_consumed.transition_id
                    and item.position == repair_consumed.position
                )
                else item
                for item in snapshot.stage_artifact_bindings
            ),
        ),
        replace(
            snapshot,
            stage_artifact_bindings=tuple(
                item.model_copy(
                    update={
                        "artifact_id": prior.artifact_id,
                        "artifact_revision": prior.revision,
                        "artifact_sha256": prior.sha256,
                    }
                )
                if (
                    item.transition_id == ordinary_consumed.transition_id
                    and item.position == ordinary_consumed.position
                )
                else item
                for item in snapshot.stage_artifact_bindings
            ),
        ),
    )
    for forged_snapshot in lineage_forgeries:
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            CoreRunDomainVerifier()._verify_snapshot(history, forged_snapshot)

    integrity = RunIntegrityService(workspace)
    assert prior_key in protected_revision_keys(verified)
    observed = workspace_observation_revision_keys(
        verified,
        additional_revisions=(prior,),
    )
    assert prior_key in observed
    assert successor_key in observed
    hard_mismatch = integrity.first_mismatch(
        verified,
        additional_revisions=(prior,),
    )
    assert hard_mismatch is not None
    assert (hard_mismatch[0].artifact_id, hard_mismatch[0].revision) == prior_key

    lineage_observed = workspace_observation_revision_keys(
        verified,
        completion_lineage_revisions=(prior, successor),
    )
    assert prior_key not in lineage_observed
    assert successor_key in lineage_observed
    assert (
        integrity.first_mismatch(
            verified,
            completion_lineage_revisions=(prior, successor),
        )
        is None
    )

    forged_snapshots = (
        replace(snapshot, gate_repair_artifact_bindings=()),
        replace(
            snapshot,
            artifacts=tuple(
                item.model_copy(update={"current_revision": 1})
                if item.artifact_id == "audited_brief"
                else item
                for item in snapshot.artifacts
            ),
        ),
        replace(
            snapshot,
            artifact_revisions=tuple(
                item.model_copy(update={"path": "output/intermediate/other.md"})
                if (item.artifact_id, item.revision) == successor_key
                else item
                for item in snapshot.artifact_revisions
            ),
        ),
        replace(
            snapshot,
            gate_repair_artifact_bindings=(
                binding.model_copy(update={"gate_repair_id": "GATE-REPAIR-CROSS"}),
            ),
        ),
        replace(
            snapshot,
            checkout_revision_members=tuple(
                item.model_copy(
                    update={
                        "artifact_revision": 1,
                        "blob_sha256": prior.sha256,
                        "byte_size": prior.size_bytes,
                    }
                )
                if (item.artifact_id == "audited_brief" and item.artifact_revision == 2)
                else item
                for item in snapshot.checkout_revision_members
            ),
        ),
    )
    for forged_snapshot in forged_snapshots:
        forged = replace(verified, snapshot=forged_snapshot)
        assert prior_key in workspace_observation_revision_keys(forged)
        mismatch = integrity.first_mismatch(forged)
        assert mismatch is not None

    repaired_path = workspace / successor.path
    repaired_path.write_text("tampered repaired brief\n", encoding="utf-8")
    mismatch = integrity.first_mismatch(verified)
    assert mismatch is not None
    assert (mismatch[0].artifact_id, mismatch[0].revision) == successor_key


def test_active_gate_repair_contamination_is_zero_write_human_block(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)

    for _ in range(10):
        result = service.continue_authorized()
        assert result.status == "role_work_required"
        assert result.trace.envelope_path is not None
        envelope = json.loads(
            (workspace / result.trace.envelope_path).read_text(encoding="utf-8")
        )
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            snapshot = store.load_snapshot(envelope["run_id"])
        _write_current_role_proposal(
            workspace,
            result,
            initial_editor_repetitions=20,
            repair_editor_repetitions=210,
        )
        if envelope["role_id"] == "editor" and snapshot.gate_repair_cycles:
            break
    else:
        raise AssertionError("authorized Gate repair was not reserved")

    audited = next(
        item for item in snapshot.artifacts if item.artifact_id == "audited_brief"
    )
    audited_revision = next(
        item
        for item in snapshot.artifact_revisions
        if item.artifact_id == audited.artifact_id
        and item.revision == audited.current_revision
    )
    (workspace / audited_revision.path).write_text(
        "tampered while Gate repair is active\n",
        encoding="utf-8",
    )

    contamination = RunIntegrityService(workspace).inspect(
        IntegrityCheckRequest.model_validate(
            {
                **IntegrityCheckRequest.minimal_example,
                "request_id": "REQ-GATE-REPAIR-CONTAMINATION-001",
                "run_id": envelope["run_id"],
                "expected_store_revision": snapshot.store_revision,
            },
            strict=True,
        )
    )
    assert contamination["status"] == "blocked"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        blocked_revision = store.current_revision

    blocked = service.continue_authorized()
    assert (
        blocked.status,
        blocked.reason_code,
        blocked.trace.next_action.action_kind,
        blocked.trace.next_action.effect_kind,
    ) == (
        "needs_human",
        "gate_repair_failed_after_attempt",
        "human_decision",
        "gate_repair_human_review",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after = store.load_snapshot(envelope["run_id"])
        assert store.current_revision == blocked_revision
    assert after.run_integrity_records[-1].status == "contaminated"
    assert not after.repair_cycles
    assert not after.artifact_supersessions
    assert not after.repair_completions
    assert not after.recovery_completions

    replay = service.continue_authorized()
    assert replay == blocked
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == blocked_revision
