"""Lifecycle coverage for normal successors and frozen Human guidance."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.control_store.schema import SCHEMA_VERSION
from multi_agent_brief.contracts.v2 import RunSuccessorStartRequest
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.policy import derived_id
from multi_agent_brief.core_run_v2.successor import (
    CoreRunSuccessorService,
    build_run_guidance_snapshot,
)
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.product.post_final_assessment_projection import (
    build_post_final_assessment_projection,
)
from multi_agent_brief.product.post_final_review import (
    POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
    POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
    POST_FINAL_HUMAN_OBSERVATION_INPUT_SCHEMA,
    PostFinalReviewError,
    PostFinalReviewService,
)
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.service import RuntimeHostService
from tests.test_finalized_local_review_facts import _finalized_local_workspace
from tests.test_post_final_human_review import (
    _disposition_payload,
    _qualified_review,
)


def _verified(workspace: Path, run_id: str):
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        return CoreRunDomainVerifier().verify(store, run_id)


def _stored_adapter_loader(workspace: Path):
    """Keep these Core tests independent of any installed runtime kit."""

    def load(run_id: str):
        return _verified(workspace, run_id).runtime_adapter

    return load


def _start_successor(
    workspace: Path,
    *,
    successor_run_id: str,
    run_direction,
    include_approved_guidance: bool,
):
    return RuntimeHostService(
        workspace,
        adapter_loader=_stored_adapter_loader(workspace),
    ).start_successor(
        successor_run_id=successor_run_id,
        run_direction=run_direction,
        include_approved_guidance=include_approved_guidance,
    )


def _approve_one_guidance(review, status, finding, *, text: str):
    disposition = review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="successor-guidance-accept",
            decision="accept",
        )
    )
    draft = review.append_guidance_draft(
        {
            "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-1",
            "human_request_id": "successor-guidance-draft",
            "assessment_result_id": status["assessment_result_id"],
            "finding_id": finding["finding_id"],
            "disposition_id": disposition["disposition_id"],
            "guidance_text": text,
        }
    )
    approved = review.approve_guidance(
        {
            "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-1",
            "human_request_id": "successor-guidance-approve",
            "guidance_id": draft["guidance_id"],
            "draft_revision": draft["draft_revision"],
        }
    )
    return draft, approved


def _start_active_role_invocation(workspace: Path):
    service = RuntimeHostService(
        workspace,
        adapter_loader=_stored_adapter_loader(workspace),
    )
    for _ in range(8):
        action = service.next_action()
        if action.action_kind == "delegate":
            return service, service.start_current_invocation(action)
        if action.action_kind != "deterministic":
            raise AssertionError(action.model_dump(mode="json"))
        service.apply_current(action, presentation_hook=False)
    raise AssertionError("successor did not reach one delegated role")


class _CapturedSuccessorRequest(Exception):
    def __init__(self, request: RunSuccessorStartRequest) -> None:
        self.request = request


def _capture_successor_request(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    successor_run_id: str,
    run_direction,
    include_approved_guidance: bool,
) -> RunSuccessorStartRequest:
    def capture(_service, request):
        raise _CapturedSuccessorRequest(request)

    with monkeypatch.context() as scoped:
        scoped.setattr(CoreRunSuccessorService, "start_successor", capture)
        with pytest.raises(_CapturedSuccessorRequest) as captured:
            _start_successor(
                workspace,
                successor_run_id=successor_run_id,
                run_direction=run_direction,
                include_approved_guidance=include_approved_guidance,
            )
    return captured.value.request


def _request_with(request: RunSuccessorStartRequest, **updates):
    payload = request.model_dump(mode="json", exclude_unset=False)
    payload.update(updates)
    payload.pop("request_fingerprint", None)
    payload["request_fingerprint"] = canonical_fingerprint(payload)
    return RunSuccessorStartRequest.model_validate(payload, strict=True)


def _workspace_topology(workspace: Path) -> dict[str, tuple[str, object]]:
    observed: dict[str, tuple[str, object]] = {}
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace).as_posix()
        if path.is_symlink():
            observed[relative] = ("symlink", path.readlink().as_posix())
        elif path.is_dir():
            observed[relative] = ("directory", None)
        elif path.is_file():
            observed[relative] = ("blob", path.read_bytes())
        else:
            observed[relative] = ("other", None)
    return observed


def test_normal_successor_freezes_empty_snapshot_without_inheriting_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, predecessor_run_id, _clock = _finalized_local_workspace(
        tmp_path,
        monkeypatch,
    )
    predecessor = _verified(workspace, predecessor_run_id)
    direction = predecessor.binding.run_direction
    successor_run_id = "RUN-GUIDANCE-EMPTY-002"

    with sqlite3.connect(workspace / "briefloop.db") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 13
    assert SCHEMA_VERSION == 13

    committed = _start_successor(
        workspace,
        successor_run_id=successor_run_id,
        run_direction=direction,
        include_approved_guidance=False,
    )
    assert committed.status == "committed"

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision_after_commit = store.current_revision
        history = store.load_history()
        successor = store.load_snapshot(successor_run_id)
    CoreRunDomainVerifier().verify_history(history)
    assert successor.workspace_run_head.current_run_id == successor_run_id
    assert len(successor.run_contract_bindings) == 1
    assert successor.run_contract_bindings[0].run_direction == direction
    assert len(successor.run_guidance_snapshots) == 1
    guidance = successor.run_guidance_snapshots[0]
    assert guidance.reuse_requested is False
    assert guidance.selected_count == guidance.omitted_count == 0
    assert guidance.selected_item_ids == guidance.decision_ids == []
    assert successor.run_guidance_selection_decisions == ()
    assert successor.run_guidance_snapshot_items == ()
    transition = successor.run_head_transitions[0]
    assert (
        transition.predecessor_run_id,
        transition.successor_run_id,
        transition.reason_code,
        transition.successor_disposition,
    ) == (
        predecessor_run_id,
        successor_run_id,
        "human_started_successor",
        "reference",
    )
    assert {
        "run_initialized",
        "run_successor_started",
        "run_guidance_snapshot_frozen",
    }.issubset({item.event_type for item in successor.events})

    # A normal successor receives direction and contracts, never the prior
    # run's execution, discovery, acquisition, provider, or evidence effects.
    assert successor.run_execution_authorizations == ()
    assert successor.run_source_discovery_authorizations == ()
    assert successor.run_source_acquisition_attempt_authorizations == ()
    assert successor.sources == ()
    assert successor.invocations == ()
    assert successor.accepted_proposals == ()

    replayed = _start_successor(
        workspace,
        successor_run_id=successor_run_id,
        run_direction=direction,
        include_approved_guidance=False,
    )
    assert replayed.status == "replayed"
    assert replayed.receipt == committed.receipt
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision_after_commit
        assert len(store.load_history().snapshots) == 2

    changed_direction = direction.model_copy(
        update={"brief_title": "A conflicting successor direction"}
    )
    with pytest.raises(RuntimeHostError, match="submission_replay_conflict"):
        _start_successor(
            workspace,
            successor_run_id=successor_run_id,
            run_direction=changed_direction,
            include_approved_guidance=False,
        )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision_after_commit

    old_schema = tmp_path / "old-schema"
    shutil.copytree(workspace, old_schema)
    with sqlite3.connect(old_schema / "briefloop.db") as connection:
        connection.execute("PRAGMA user_version=12")
    with pytest.raises(RuntimeHostError, match="control_store_integrity_invalid"):
        _start_successor(
            old_schema,
            successor_run_id="RUN-OLD-SCHEMA-003",
            run_direction=direction,
            include_approved_guidance=False,
        )


def test_successor_rejects_tampered_finalized_checkout_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, predecessor_run_id, _clock = _finalized_local_workspace(
        tmp_path,
        monkeypatch,
    )
    direction = _verified(workspace, predecessor_run_id).binding.run_direction
    request = _capture_successor_request(
        workspace,
        monkeypatch,
        successor_run_id="RUN-TAMPERED-CHECKOUT-002",
        run_direction=direction,
        include_approved_guidance=False,
    )
    database = workspace / "briefloop.db"
    with SQLiteControlStore.open(database) as store:
        predecessor = store.load_snapshot(predecessor_run_id)
        committed_revisions = {
            item.transaction_id: item.committed_revision
            for item in predecessor.transactions
        }
        checkout_binding = max(
            predecessor.receipt_checkout_bindings,
            key=lambda item: committed_revisions[item.transaction_id],
        )
        checkout_members = tuple(
            item
            for item in predecessor.checkout_revision_members
            if item.checkout_revision_id == checkout_binding.post_checkout_revision_id
        )
    target_member = next(
        item
        for item in sorted(checkout_members, key=lambda item: item.canonical_path)
        if (workspace / item.canonical_path).is_file()
    )
    target = workspace / target_member.canonical_path
    assert hashlib.sha256(target.read_bytes()).hexdigest() == target_member.blob_sha256
    tampered_bytes = b"tampered finalized predecessor checkout\n"
    target.write_bytes(tampered_bytes)

    database_before = database.read_bytes()
    topology_before = _workspace_topology(workspace)
    with SQLiteControlStore.open(database) as store:
        revision_before = store.current_revision
        history_before = store.load_history()
        head_before = store.load_workspace_run_head()
        predecessor_before = store.load_snapshot(predecessor_run_id)

    result = CoreRunSuccessorService(workspace).start_successor(request)

    assert (result.status, result.error_code) == (
        "failed_uncommitted",
        "checkout_projection_preimage_restore_required",
    )
    assert database.read_bytes() == database_before
    assert _workspace_topology(workspace) == topology_before
    assert target.read_bytes() == tampered_bytes
    with SQLiteControlStore.open(database) as store:
        assert store.current_revision == revision_before
        assert store.load_workspace_run_head() == head_before
        assert store.load_snapshot(predecessor_run_id) == predecessor_before
        assert store.load_history() == history_before
        assert all(
            item.run.run_id != request.successor_run_id
            for item in store.load_history().snapshots
        )


def test_approved_guidance_is_scope_selected_frozen_and_role_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (
        workspace,
        predecessor_run_id,
        provider_calls,
        review,
        status,
        finding,
    ) = _qualified_review(tmp_path, monkeypatch)
    guidance_text = (
        "Keep the recommendation concise; never turn this guidance into evidence."
    )
    draft, _approved = _approve_one_guidance(
        review,
        status,
        finding,
        text=guidance_text,
    )
    direction = _verified(workspace, predecessor_run_id).binding.run_direction
    provider_call_count = len(provider_calls)
    matching_workspace = workspace
    mismatch_workspace = tmp_path / "scope-mismatch"
    shutil.copytree(workspace, mismatch_workspace)

    successor_run_id = "RUN-GUIDANCE-SELECTED-002"
    result = _start_successor(
        matching_workspace,
        successor_run_id=successor_run_id,
        run_direction=direction,
        include_approved_guidance=True,
    )
    assert result.status == "committed"
    assert len(provider_calls) == provider_call_count

    verified = _verified(matching_workspace, successor_run_id)
    successor = verified.snapshot
    frozen_snapshot = successor.run_guidance_snapshots[0]
    assert (frozen_snapshot.selected_count, frozen_snapshot.omitted_count) == (1, 0)
    decision = successor.run_guidance_selection_decisions[0]
    item = successor.run_guidance_snapshot_items[0]
    assert (decision.selected, decision.reason_code) == (
        True,
        "approved_scope_match",
    )
    assert item.guidance_text == guidance_text
    assert item.guidance_id == draft["guidance_id"]
    assert item.source_run_id == predecessor_run_id
    assert verified.binding.run_direction == direction
    assert successor.run_execution_authorizations == ()
    assert successor.run_source_discovery_authorizations == ()
    assert successor.run_source_acquisition_attempt_authorizations == ()

    analyst = RuntimeHostService._frozen_guidance_context(
        verified,
        role_id="analyst",
    )
    editor = RuntimeHostService._frozen_guidance_context(
        verified,
        role_id="editor",
    )
    assert analyst is not None and analyst == editor
    assert analyst.snapshot_id == frozen_snapshot.snapshot_id
    assert [entry.guidance_text for entry in analyst.items] == [guidance_text]
    for role_id in (
        "source-planner",
        "source-provider",
        "scout",
        "screener",
        "claim-ledger",
        "auditor",
        "formatter",
    ):
        assert (
            RuntimeHostService._frozen_guidance_context(
                verified,
                role_id=role_id,
            )
            is None
        )

    # The explicit CLI lifecycle can still select run A after run B becomes
    # current. The default product service remains current-head-only.
    with pytest.raises(
        PostFinalReviewError,
        match="post_final_review_unavailable",
    ):
        PostFinalReviewService(
            matching_workspace,
            status["assessment_result_id"],
            status["assessment_result_fingerprint"],
        ).review_status()
    historical_direct = PostFinalReviewService(
        matching_workspace,
        status["assessment_result_id"],
        status["assessment_result_fingerprint"],
        allow_historical=True,
    ).review_status()
    assert historical_direct["assessment_result_id"] == status["assessment_result_id"]
    assert (
        main(
            [
                "quality",
                "laj",
                "review-status",
                "--workspace",
                str(matching_workspace),
                "--assessment-result-id",
                status["assessment_result_id"],
                "--assessment-result-fingerprint",
                status["assessment_result_fingerprint"],
                "--json",
            ]
        )
        == 0
    )
    historical_status = json.loads(capsys.readouterr().out)
    assert historical_status["assessment_result_id"] == status["assessment_result_id"]
    assert historical_status["provider_calls"] == 0

    frozen_item_before_status_change = item.model_dump(
        mode="json",
        exclude_unset=False,
    )
    deactivate = {
        "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
        "human_actor_id": "human-reviewer-1",
        "human_request_id": "historical-guidance-deactivate",
        "guidance_id": draft["guidance_id"],
        "draft_revision": draft["draft_revision"],
    }
    assert (
        main(
            [
                "quality",
                "laj",
                "deactivate",
                "--workspace",
                str(matching_workspace),
                "--assessment-result-id",
                status["assessment_result_id"],
                "--assessment-result-fingerprint",
                status["assessment_result_fingerprint"],
                "--request-json",
                json.dumps(deactivate, sort_keys=True),
                "--json",
            ]
        )
        == 0
    )
    deactivated = json.loads(capsys.readouterr().out)
    assert deactivated["replayed"] is False
    assert deactivated["status_revision_id"]
    later_rejection = PostFinalReviewService(
        matching_workspace,
        status["assessment_result_id"],
        status["assessment_result_fingerprint"],
        allow_historical=True,
    ).record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="historical-disposition-after-successor",
            decision="reject",
        )
    )
    assert later_rejection["replayed"] is False
    after_status_change = _verified(matching_workspace, successor_run_id)
    assert (
        after_status_change.snapshot.run_guidance_snapshot_items[0].model_dump(
            mode="json", exclude_unset=False
        )
        == frozen_item_before_status_change
    )
    assert (
        RuntimeHostService._frozen_guidance_context(
            after_status_change,
            role_id="analyst",
        )
        == analyst
    )
    assert len(provider_calls) == provider_call_count

    mismatched_direction = direction.model_copy(update={"audience": "board"})
    mismatch_result = _start_successor(
        mismatch_workspace,
        successor_run_id="RUN-GUIDANCE-MISMATCH-002",
        run_direction=mismatched_direction,
        include_approved_guidance=True,
    )
    assert mismatch_result.status == "committed"
    mismatch = _verified(
        mismatch_workspace,
        "RUN-GUIDANCE-MISMATCH-002",
    ).snapshot
    assert mismatch.run_guidance_snapshot_items == ()
    assert (
        mismatch.run_guidance_snapshots[0].selected_count,
        mismatch.run_guidance_snapshots[0].omitted_count,
    ) == (0, 1)
    assert [
        (entry.selected, entry.reason_code)
        for entry in mismatch.run_guidance_selection_decisions
    ] == [(False, "guidance_scope_mismatch")]
    assert len(provider_calls) == provider_call_count


def test_human_observation_guidance_freezes_tagged_provenance_without_finding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, predecessor_run_id, _calls, review, status, _finding = _qualified_review(
        tmp_path,
        monkeypatch,
    )
    observed = review.record_human_observation(
        {
            "schema_version": POST_FINAL_HUMAN_OBSERVATION_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-observer",
            "human_request_id": "human-observation-successor-1",
            "observation_text": "The management conclusion omits the stated downside condition.",
            "assessment_result_id": status["assessment_result_id"],
            "assessment_result_fingerprint": status["assessment_result_fingerprint"],
            "reader_view_sha256": status["reader_view_sha256"],
        }
    )
    draft = review.append_guidance_draft(
        {
            "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-observer",
            "human_request_id": "human-observation-guidance-1",
            "provenance_kind": "human_observation",
            "assessment_result_id": status["assessment_result_id"],
            "assessment_result_fingerprint": status["assessment_result_fingerprint"],
            "observation_id": observed["observation_id"],
            "observation_fingerprint": observed["observation_fingerprint"],
            "guidance_text": "Check the downside condition before drafting the recommendation.",
        }
    )
    review.approve_guidance(
        {
            "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-observer",
            "human_request_id": "human-observation-guidance-approve-1",
            "guidance_id": draft["guidance_id"],
            "draft_revision": draft["draft_revision"],
        }
    )

    direction = _verified(workspace, predecessor_run_id).binding.run_direction
    successor_run_id = "RUN-HUMAN-OBSERVATION-GUIDANCE-002"
    assert (
        _start_successor(
            workspace,
            successor_run_id=successor_run_id,
            run_direction=direction,
            include_approved_guidance=True,
        ).status
        == "committed"
    )
    successor = _verified(workspace, successor_run_id).snapshot
    decision = successor.run_guidance_selection_decisions[0]
    item = successor.run_guidance_snapshot_items[0]
    assert decision.provenance_kind == item.provenance_kind == "human_observation"
    assert decision.observation_id == item.observation_id == observed["observation_id"]
    assert (
        decision.observation_fingerprint
        == item.observation_fingerprint
        == observed["observation_fingerprint"]
    )
    assert (
        decision.assessment_result_id
        == item.assessment_result_id
        == status["assessment_result_id"]
    )
    assert decision.finding_id is None
    assert decision.finding_fingerprint is None
    assert decision.disposition_id is None
    assert decision.disposition_fingerprint is None
    assert item.finding_id is None
    assert item.finding_fingerprint is None
    assert item.disposition_id is None
    assert item.disposition_fingerprint is None

    verified = _verified(workspace, successor_run_id)
    context = RuntimeHostService._frozen_guidance_context(
        verified,
        role_id="analyst",
    )
    assert context is not None
    assert context.items[0].provenance_kind == "human_observation"
    assert context.items[0].observation_id == observed["observation_id"]
    assert context.items[0].finding_id is None
    assert context.items[0].disposition_id is None


@pytest.mark.parametrize("decision", ["reject", "defer"])
def test_current_nonaccept_disposition_omits_previously_approved_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    workspace, predecessor_run_id, _calls, review, status, finding = _qualified_review(
        tmp_path, monkeypatch
    )
    _approve_one_guidance(
        review,
        status,
        finding,
        text="Use only guidance whose acceptance is still current.",
    )
    review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id=f"successor-guidance-{decision}-head",
            decision=decision,
        )
    )
    successor_run_id = f"RUN-GUIDANCE-{decision.upper()}-002"
    assert (
        _start_successor(
            workspace,
            successor_run_id=successor_run_id,
            run_direction=_verified(
                workspace, predecessor_run_id
            ).binding.run_direction,
            include_approved_guidance=True,
        ).status
        == "committed"
    )
    successor = _verified(workspace, successor_run_id).snapshot
    assert successor.run_guidance_snapshot_items == ()
    assert [
        (entry.selected, entry.reason_code)
        for entry in successor.run_guidance_selection_decisions
    ] == [(False, "guidance_unapproved")]


def test_active_successor_blocks_new_historical_review_writes_but_allows_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, predecessor_run_id, _calls, review, status, finding = _qualified_review(
        tmp_path, monkeypatch
    )
    accept_payload = _disposition_payload(
        status,
        finding,
        request_id="active-guard-accept",
        decision="accept",
    )
    accepted = review.record_disposition(accept_payload)
    draft_payload = {
        "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
        "human_actor_id": "human-reviewer-1",
        "human_request_id": "active-guard-draft",
        "assessment_result_id": status["assessment_result_id"],
        "finding_id": finding["finding_id"],
        "disposition_id": accepted["disposition_id"],
        "guidance_text": "Preserve the active invocation envelope exactly.",
    }
    draft = review.append_guidance_draft(draft_payload)
    approve_payload = {
        "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
        "human_actor_id": "human-reviewer-1",
        "human_request_id": "active-guard-approve",
        "guidance_id": draft["guidance_id"],
        "draft_revision": draft["draft_revision"],
    }
    review.approve_guidance(approve_payload)

    second_finding = status["dispositions"][1]
    second_accept = review.record_disposition(
        _disposition_payload(
            status,
            second_finding,
            request_id="active-guard-second-accept",
            decision="accept",
        )
    )
    second_draft = review.append_guidance_draft(
        {
            "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-1",
            "human_request_id": "active-guard-second-draft",
            "assessment_result_id": status["assessment_result_id"],
            "finding_id": second_finding["finding_id"],
            "disposition_id": second_accept["disposition_id"],
            "guidance_text": "Keep this second draft pending Human approval.",
        }
    )

    direction = _verified(workspace, predecessor_run_id).binding.run_direction
    successor_run_id = "RUN-ACTIVE-GUARD-002"
    assert (
        _start_successor(
            workspace,
            successor_run_id=successor_run_id,
            run_direction=direction,
            include_approved_guidance=True,
        ).status
        == "committed"
    )
    runtime, dispatch = _start_active_role_invocation(workspace)
    historical = PostFinalReviewService(
        workspace,
        status["assessment_result_id"],
        status["assessment_result_fingerprint"],
        allow_historical=True,
    )
    database = workspace / "briefloop.db"
    before = database.read_bytes()
    with SQLiteControlStore.open(database) as store:
        before_revision = store.current_revision

    assert historical.record_disposition(accept_payload)["replayed"] is True
    assert historical.append_guidance_draft(draft_payload)["replayed"] is True
    assert historical.approve_guidance(approve_payload)["replayed"] is True
    assert database.read_bytes() == before

    blocked_calls = [
        lambda: historical.record_disposition(
            _disposition_payload(
                status,
                finding,
                request_id="active-guard-new-reject",
                decision="reject",
            )
        ),
        lambda: historical.append_guidance_draft(
            {
                **draft_payload,
                "human_request_id": "active-guard-new-draft",
                "guidance_text": "This revision must wait for the active role.",
            }
        ),
        lambda: historical.approve_guidance(
            {
                "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
                "human_actor_id": "human-reviewer-1",
                "human_request_id": "active-guard-new-approve",
                "guidance_id": second_draft["guidance_id"],
                "draft_revision": second_draft["draft_revision"],
            }
        ),
    ]
    for action_name, action in (
        ("deactivate", historical.deactivate_guidance),
        ("revert", historical.revert_guidance),
        ("supersede", historical.supersede_guidance),
    ):
        blocked_calls.append(
            lambda action=action, action_name=action_name: action(
                {
                    **approve_payload,
                    "human_request_id": f"active-guard-new-{action_name}",
                }
            )
        )
    for call in blocked_calls:
        with pytest.raises(
            PostFinalReviewError,
            match="post_final_review_request_conflict",
        ):
            call()
        assert database.read_bytes() == before
    with SQLiteControlStore.open(database) as store:
        assert store.current_revision == before_revision
        active = [
            item
            for item in store.load_snapshot(successor_run_id).invocations
            if item.status == "active"
        ]
    assert [item.invocation_id for item in active] == [dispatch.envelope.invocation_id]

    failed = runtime.fail_invocation(
        dispatch.envelope.invocation_id,
        reason_code="child_failed",
    )
    assert failed.status == "rejected_recorded"
    assert (
        historical.record_disposition(
            _disposition_payload(
                status,
                finding,
                request_id="active-guard-new-reject",
                decision="reject",
            )
        )["replayed"]
        is False
    )


def test_tampered_and_ambiguous_guidance_selection_fail_closed_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, run_id, _calls, review, status, finding = _qualified_review(
        tmp_path,
        monkeypatch,
    )
    draft, _approved = _approve_one_guidance(
        review,
        status,
        finding,
        text="Preserve the exact evidence boundary.",
    )
    direction = _verified(workspace, run_id).binding.run_direction
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
    source = next(item for item in history.snapshots if item.run.run_id == run_id)
    result = source.post_final_assessment_results[0]
    ambiguous = replace(
        history,
        snapshots=tuple(
            replace(
                item,
                post_final_assessment_results=(
                    *item.post_final_assessment_results,
                    result,
                ),
            )
            if item.run.run_id == run_id
            else item
            for item in history.snapshots
        ),
    )
    projection = build_post_final_assessment_projection(
        workspace,
        assessment_result_id=result.assessment_result_id,
        assessment_result_fingerprint=result.result_fingerprint,
        loaded_history=ambiguous,
    )
    assert (projection.status, projection.reason_code) == (
        "invalid",
        "post_final_assessment_selection_invalid",
    )

    tampered = tmp_path / "tampered-guidance"
    shutil.copytree(workspace, tampered)
    database = tampered / "briefloop.db"
    with sqlite3.connect(database) as connection:
        before = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='post_final_guidance_drafts_no_update'"
        ).fetchone()
        assert trigger is not None
        payload_row = connection.execute(
            "SELECT payload_json FROM post_final_guidance_drafts "
            "WHERE guidance_id=? AND draft_revision=?",
            (draft["guidance_id"], draft["draft_revision"]),
        ).fetchone()
        assert payload_row is not None
        payload = json.loads(payload_row[0])
        payload["guidance_text"] = "tampered after approval"
        connection.execute("DROP TRIGGER post_final_guidance_drafts_no_update")
        connection.execute(
            "UPDATE post_final_guidance_drafts SET payload_json=? "
            "WHERE guidance_id=? AND draft_revision=?",
            (
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                draft["guidance_id"],
                draft["draft_revision"],
            ),
        )
        connection.execute(str(trigger[0]))
        connection.commit()

    with pytest.raises(RuntimeHostError, match="control_store_integrity_invalid"):
        _start_successor(
            tampered,
            successor_run_id="RUN-GUIDANCE-TAMPERED-002",
            run_direction=direction,
            include_approved_guidance=True,
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == before
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM runs WHERE run_id='RUN-GUIDANCE-TAMPERED-002'"
            ).fetchone()[0]
            == 0
        )


def test_approved_guidance_opt_in_and_active_status_are_both_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, run_id, _calls, _review, status, finding = _qualified_review(
        tmp_path,
        monkeypatch,
    )
    review = PostFinalReviewService(
        workspace,
        status["assessment_result_id"],
        status["assessment_result_fingerprint"],
    )
    draft, _approved = _approve_one_guidance(
        review,
        status,
        finding,
        text="Use one concise conclusion that respects the current evidence.",
    )
    direction = _verified(workspace, run_id).binding.run_direction
    no_opt_in = tmp_path / "guidance-no-opt-in"
    inactive = tmp_path / "guidance-inactive"
    shutil.copytree(workspace, no_opt_in)

    no_opt_result = _start_successor(
        no_opt_in,
        successor_run_id="RUN-GUIDANCE-NO-OPT-IN-002",
        run_direction=direction,
        include_approved_guidance=False,
    )
    assert no_opt_result.status == "committed"
    no_opt_snapshot = _verified(no_opt_in, "RUN-GUIDANCE-NO-OPT-IN-002").snapshot
    assert no_opt_snapshot.run_guidance_snapshot_items == ()
    assert [
        (item.selected, item.reason_code)
        for item in no_opt_snapshot.run_guidance_selection_decisions
    ] == [(False, "reuse_not_requested")]

    with SQLiteControlStore.open(no_opt_in / "briefloop.db") as store:
        revision = store.current_revision
    with pytest.raises(RuntimeHostError, match="submission_replay_conflict"):
        _start_successor(
            no_opt_in,
            successor_run_id="RUN-GUIDANCE-NO-OPT-IN-002",
            run_direction=direction,
            include_approved_guidance=True,
        )
    with SQLiteControlStore.open(no_opt_in / "briefloop.db") as store:
        assert store.current_revision == revision

    # Mutate through the still-canonical archive-bound source workspace, then
    # copy the resulting Store state for the independent successor row.
    review.deactivate_guidance(
        {
            "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-1",
            "human_request_id": "successor-guidance-deactivate",
            "guidance_id": draft["guidance_id"],
            "draft_revision": draft["draft_revision"],
        }
    )
    shutil.copytree(workspace, inactive)
    inactive_result = _start_successor(
        inactive,
        successor_run_id="RUN-GUIDANCE-INACTIVE-002",
        run_direction=direction,
        include_approved_guidance=True,
    )
    assert inactive_result.status == "committed"
    inactive_snapshot = _verified(inactive, "RUN-GUIDANCE-INACTIVE-002").snapshot
    assert inactive_snapshot.run_guidance_snapshot_items == ()
    assert [
        (item.selected, item.reason_code)
        for item in inactive_snapshot.run_guidance_selection_decisions
    ] == [(False, "guidance_inactive")]


def test_direct_core_successor_rejects_forged_inherited_authority_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    direction = _verified(workspace, run_id).binding.run_direction
    request = _capture_successor_request(
        workspace,
        monkeypatch,
        successor_run_id="RUN-GUIDANCE-INHERITED-AUTHORITY-002",
        run_direction=direction,
        include_approved_guidance=False,
    )
    topologies = ("single_session", "default", "strict", "human_assisted")
    alternate_topology = next(
        item for item in topologies if item != request.role_topology
    )
    changed_gates = dict(request.gate_strictness)
    gate_id = next(iter(changed_gates))
    changed_gates[gate_id] = not changed_gates[gate_id]
    forged_fields = (
        {"role_topology": alternate_topology},
        {"gate_strictness": changed_gates},
        {"input_governance_required": not request.input_governance_required},
    )
    for position, update in enumerate(forged_fields):
        candidate = tmp_path / f"forged-successor-authority-{position}"
        shutil.copytree(workspace, candidate)
        database = candidate / "briefloop.db"
        before = database.read_bytes()
        result = CoreRunSuccessorService(candidate).start_successor(
            _request_with(request, **update)
        )
        assert (result.status, result.error_code) == (
            "failed_uncommitted",
            "successor_history_invalid",
        )
        assert database.read_bytes() == before
        with SQLiteControlStore.open(database) as store:
            assert all(
                item.run.run_id != request.successor_run_id
                for item in store.load_history().snapshots
            )

    unchanged = CoreRunSuccessorService(workspace).start_successor(request)
    assert unchanged.status == "committed"


def test_guidance_context_count_and_utf8_byte_bounds_are_exact_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, run_id, _calls, review, status, finding = _qualified_review(
        tmp_path,
        monkeypatch,
    )
    _approve_one_guidance(
        review,
        status,
        finding,
        text="bounded guidance",
    )
    verified = _verified(workspace, run_id)
    direction = verified.binding.run_direction
    request = _capture_successor_request(
        workspace,
        monkeypatch,
        successor_run_id="RUN-GUIDANCE-BOUNDS-002",
        run_direction=direction,
        include_approved_guidance=True,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
    database_before = (workspace / "briefloop.db").read_bytes()
    source = next(item for item in history.snapshots if item.run.run_id == run_id)
    base_draft = source.post_final_guidance_drafts[0]
    base_status = source.post_final_guidance_statuses[0]

    def history_with_count(count: int):
        drafts = tuple(
            base_draft.model_copy(
                update={"guidance_id": f"pf-laj-guidance-bound-{index:02d}"}
            )
            for index in range(count)
        )
        statuses = tuple(
            base_status.model_copy(
                update={
                    "guidance_id": draft.guidance_id,
                    "status_revision_id": f"pf-laj-status-bound-{index:02d}",
                }
            )
            for index, draft in enumerate(drafts)
        )
        return replace(
            history,
            snapshots=tuple(
                replace(
                    item,
                    post_final_guidance_drafts=drafts,
                    post_final_guidance_statuses=statuses,
                )
                if item.run.run_id == run_id
                else item
                for item in history.snapshots
            ),
        )

    exact_snapshot, _decisions, exact_items = build_run_guidance_snapshot(
        history=history_with_count(16),
        successor_contract=verified.binding,
        request=request,
        snapshot_id="GUIDANCE-SNAPSHOT-BOUND-16",
        snapshot_event_id="EVT-GUIDANCE-SNAPSHOT-BOUND-16",
        derived_id=derived_id,
    )
    assert exact_snapshot.selected_count == len(exact_items) == 16
    with pytest.raises(CoreRunError, match="approved_guidance_context_limit_exceeded"):
        build_run_guidance_snapshot(
            history=history_with_count(17),
            successor_contract=verified.binding,
            request=request,
            snapshot_id="GUIDANCE-SNAPSHOT-BOUND-17",
            snapshot_event_id="EVT-GUIDANCE-SNAPSHOT-BOUND-17",
            derived_id=derived_id,
        )

    def history_with_text(text: str):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        draft = base_draft.model_copy(
            update={"guidance_text": text, "guidance_sha256": digest}
        )
        guidance_status = base_status.model_copy(update={"guidance_sha256": digest})
        return replace(
            history,
            snapshots=tuple(
                replace(
                    item,
                    post_final_guidance_drafts=(draft,),
                    post_final_guidance_statuses=(guidance_status,),
                )
                if item.run.run_id == run_id
                else item
                for item in history.snapshots
            ),
        )

    exact_bytes_snapshot, _decisions, exact_byte_items = build_run_guidance_snapshot(
        history=history_with_text("x" * 65_536),
        successor_contract=verified.binding,
        request=request,
        snapshot_id="GUIDANCE-SNAPSHOT-BYTES-65536",
        snapshot_event_id="EVT-GUIDANCE-SNAPSHOT-BYTES-65536",
        derived_id=derived_id,
    )
    assert exact_bytes_snapshot.selected_count == len(exact_byte_items) == 1
    with pytest.raises(CoreRunError, match="approved_guidance_context_limit_exceeded"):
        build_run_guidance_snapshot(
            history=history_with_text("x" * 65_537),
            successor_contract=verified.binding,
            request=request,
            snapshot_id="GUIDANCE-SNAPSHOT-BYTES-65537",
            snapshot_event_id="EVT-GUIDANCE-SNAPSHOT-BYTES-65537",
            derived_id=derived_id,
        )
    assert (workspace / "briefloop.db").read_bytes() == database_before


def test_windows_successor_boundary_is_typed_and_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, run_id, provider_calls, review, status, finding = _qualified_review(
        tmp_path,
        monkeypatch,
    )
    _approve_one_guidance(
        review,
        status,
        finding,
        text="Keep the later brief concise and evidence-bound.",
    )
    direction = _verified(workspace, run_id).binding.run_direction
    request = _capture_successor_request(
        workspace,
        monkeypatch,
        successor_run_id="RUN-GUIDANCE-WINDOWS-002",
        run_direction=direction,
        include_approved_guidance=True,
    )
    database = workspace / "briefloop.db"
    database_before = database.read_bytes()
    provider_calls_before = len(provider_calls)
    with SQLiteControlStore.open(database) as store:
        history_before = store.load_history()
        revision_before = store.current_revision
        predecessor_before = store.load_snapshot(run_id)

    import multi_agent_brief.core_run_v2.checkout as checkout_module

    monkeypatch.setattr(checkout_module.sys, "platform", "win32")
    result = CoreRunSuccessorService(workspace).start_successor(request)
    assert (result.status, result.error_code) == (
        "failed_uncommitted",
        "checkout_publication_unsupported",
    )
    assert database.read_bytes() == database_before
    assert len(provider_calls) == provider_calls_before
    with SQLiteControlStore.open(database) as store:
        history_after = store.load_history()
        assert store.current_revision == revision_before
        assert store.load_snapshot(run_id) == predecessor_before
    assert history_after == history_before
    assert all(
        item.run.run_id != request.successor_run_id for item in history_after.snapshots
    )
