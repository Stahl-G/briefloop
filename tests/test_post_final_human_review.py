"""Store-native PF-LAJ Human disposition and guidance lifecycle."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.product.post_final_review import (
    POST_FINAL_DISPOSITION_INPUT_SCHEMA,
    POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
    POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
    PostFinalReviewError,
    PostFinalReviewService,
)
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_API_KEY_SETTING,
)
import multi_agent_brief.semantic_evaluator.runner as runner_module
from tests.test_finalized_local_review_facts import _finalized_local_workspace
from tests.test_post_final_assessment import (
    _fixture_service,
    _policy_payload,
)


def _qualified_review(tmp_path, monkeypatch):
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    provider_calls: list[tuple[str, int]] = []
    assessment = _fixture_service(
        workspace,
        provider_calls,
        terminal_mode="finding",
    )
    assert assessment.policy_set(_policy_payload())["ok"] is True
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    outcome = assessment.assess()
    assert outcome["ok"] is True, outcome
    assert outcome["status"] == "available"
    assert outcome["finding_count"] >= 1
    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    review = PostFinalReviewService(workspace)
    status = review.review_status()
    finding = status["dispositions"][0]
    return workspace, run_id, provider_calls, review, status, finding


def _disposition_payload(status, finding, *, request_id: str, decision: str):
    return {
        "schema_version": POST_FINAL_DISPOSITION_INPUT_SCHEMA,
        "human_actor_id": "human-reviewer-1",
        "human_request_id": request_id,
        "assessment_result_id": status["assessment_result_id"],
        "reader_view_sha256": status["reader_view_sha256"],
        "finding_id": finding["finding_id"],
        "finding_fingerprint": finding["finding_fingerprint"],
        "decision": decision,
        "human_note": f"Human chose {decision}.",
    }


def test_disposition_guidance_and_separate_approval_are_append_only(
    tmp_path, monkeypatch
) -> None:
    workspace, run_id, provider_calls, review, status, finding = _qualified_review(
        tmp_path, monkeypatch
    )
    assert len(status["dispositions"]) >= 3
    rejected_finding = status["dispositions"][1]
    deferred_finding = status["dispositions"][2]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.load_history()
        before_action = classify_core_run_next_action(
            CoreRunDomainVerifier().verify_loaded_history(before, run_id)
        )

    rejected_payload = _disposition_payload(
        status,
        rejected_finding,
        request_id="human-disposition-reject-1",
        decision="reject",
    )
    rejected = review.record_disposition(rejected_payload)
    assert rejected["replayed"] is False
    assert review.record_disposition(rejected_payload) == {
        **rejected,
        "replayed": True,
    }
    changed = dict(rejected_payload)
    changed["decision"] = "defer"
    with pytest.raises(
        PostFinalReviewError, match="post_final_review_request_conflict"
    ):
        review.record_disposition(changed)
    deferred = review.record_disposition(
        _disposition_payload(
            status,
            deferred_finding,
            request_id="human-disposition-defer-1",
            decision="defer",
        )
    )
    assert deferred["replayed"] is False
    with pytest.raises(PostFinalReviewError, match="post_final_guidance_not_accepted"):
        review.append_guidance_draft(
            {
                "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
                "human_actor_id": "human-reviewer-1",
                "human_request_id": "guidance-rejected-1",
                "assessment_result_id": status["assessment_result_id"],
                "finding_id": rejected_finding["finding_id"],
                "disposition_id": rejected["disposition_id"],
                "guidance_text": "This must not be accepted.",
            }
        )

    accepted = review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="human-disposition-accept-1",
            decision="accept",
        )
    )
    draft_payload = {
        "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
        "human_actor_id": "human-reviewer-1",
        "human_request_id": "human-guidance-draft-1",
        "assessment_result_id": status["assessment_result_id"],
        "finding_id": finding["finding_id"],
        "disposition_id": accepted["disposition_id"],
        "guidance_text": "Require the conclusion to match the report constraints.",
    }
    draft = review.append_guidance_draft(draft_payload)
    assert draft["draft_revision"] == 1
    assert review.append_guidance_draft(draft_payload) == {
        **draft,
        "replayed": True,
    }
    approve_payload = {
        "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
        "human_actor_id": "human-reviewer-1",
        "human_request_id": "human-guidance-approve-1",
        "guidance_id": draft["guidance_id"],
        "draft_revision": 1,
    }
    approved = review.approve_guidance(approve_payload)
    assert approved["replayed"] is False
    assert review.approve_guidance(approve_payload) == {
        **approved,
        "replayed": True,
    }

    revised_payload = dict(draft_payload)
    revised_payload["human_request_id"] = "human-guidance-draft-2"
    revised_payload["guidance_text"] = (
        "Require the conclusion and recommendation to match report constraints."
    )
    revised = review.append_guidance_draft(revised_payload)
    assert revised["draft_revision"] == 2
    with pytest.raises(PostFinalReviewError, match="post_final_guidance_stale"):
        review.deactivate_guidance(
            {
                **approve_payload,
                "human_request_id": "human-guidance-stale-status",
            }
        )
    current_status_payload = {
        **approve_payload,
        "draft_revision": 2,
    }
    approval_2 = review.approve_guidance(
        {
            **current_status_payload,
            "human_request_id": "human-guidance-approve-2",
        }
    )
    assert approval_2["replayed"] is False
    for action, request_id in (
        (review.deactivate_guidance, "human-guidance-deactivate-2"),
        (review.revert_guidance, "human-guidance-revert-2"),
        (review.supersede_guidance, "human-guidance-supersede-2"),
    ):
        outcome = action(
            {
                **current_status_payload,
                "human_request_id": request_id,
            }
        )
        assert outcome["replayed"] is False

    later_rejection = review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="human-disposition-reject-after-guidance",
            decision="reject",
        )
    )
    assert later_rejection["replayed"] is False
    with pytest.raises(PostFinalReviewError, match="post_final_guidance_stale"):
        review.approve_guidance(
            {
                **current_status_payload,
                "human_request_id": "human-guidance-approve-after-reject",
            }
        )
    with pytest.raises(PostFinalReviewError, match="post_final_guidance_not_accepted"):
        review.append_guidance_draft(
            {
                **revised_payload,
                "human_request_id": "human-guidance-draft-after-reject",
                "disposition_id": later_rejection["disposition_id"],
            }
        )

    final_status = review.review_status()
    assert final_status["provider_calls"] == 0
    assert final_status["next_run_consumption"] == "not_shipped"
    assert len(final_status["guidance_drafts"]) == 2
    assert len(final_status["guidance_statuses"]) == 5
    assert final_status["guidance_statuses"][0]["draft_revision"] == 1
    assert len(provider_calls) == 9
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after = store.load_history()
        snapshot = store.load_snapshot(run_id)
    CoreRunDomainVerifier().verify_history(after)
    for receipt in snapshot.transactions:
        CoreRunDomainVerifier().verify_history(
            after, through_revision=receipt.committed_revision
        )
    after_action = classify_core_run_next_action(
        CoreRunDomainVerifier().verify_loaded_history(after, run_id)
    )
    assert (
        before_action.action_kind,
        before_action.effect_kind,
        before_action.stage_id,
        before_action.role_id,
        before_action.reason_code,
    ) == (
        after_action.action_kind,
        after_action.effect_kind,
        after_action.stage_id,
        after_action.role_id,
        after_action.reason_code,
    )


def test_tampered_or_cross_bound_finding_is_zero_write(tmp_path, monkeypatch) -> None:
    workspace, run_id, provider_calls, review, status, finding = _qualified_review(
        tmp_path, monkeypatch
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.current_revision
    payload = _disposition_payload(
        status,
        finding,
        request_id="human-disposition-tampered-1",
        decision="accept",
    )
    payload["finding_fingerprint"] = "0" * 64
    with pytest.raises(PostFinalReviewError, match="post_final_review_binding_invalid"):
        review.record_disposition(payload)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before
        snapshot = store.load_snapshot(run_id)
    assert snapshot.post_final_finding_dispositions == ()
    assert len(provider_calls) == 9


def test_advisory_receipt_relation_smuggling_and_unknown_family_fail_closed(
    tmp_path, monkeypatch
) -> None:
    workspace, run_id, _provider_calls, review, status, finding = _qualified_review(
        tmp_path, monkeypatch
    )
    review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="human-disposition-integrity-1",
            decision="defer",
        )
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
    snapshot = next(item for item in history.snapshots if item.run.run_id == run_id)
    receipt = next(
        item
        for item in snapshot.transactions
        if item.transaction_type == "post_final_finding_disposition"
    )
    artifact_reference = next(
        reference
        for item in snapshot.transactions
        for reference in item.artifact_revisions
    )

    def forged_history(**updates):
        forged_receipt = receipt.model_copy(update=updates)
        forged_snapshot = replace(
            snapshot,
            transactions=tuple(
                forged_receipt
                if item.transaction_id == receipt.transaction_id
                else item
                for item in snapshot.transactions
            ),
        )
        return replace(
            history,
            snapshots=tuple(
                forged_snapshot if item.run.run_id == run_id else item
                for item in history.snapshots
            ),
        )

    with pytest.raises(CoreRunError):
        CoreRunDomainVerifier().verify_history(
            forged_history(artifact_revisions=[artifact_reference])
        )
    with pytest.raises(CoreRunError):
        CoreRunDomainVerifier().verify_history(
            forged_history(transaction_type="post_final_unknown")
        )


def test_headless_cli_uses_the_same_store_review_service(
    tmp_path, monkeypatch, capsys
) -> None:
    workspace, _run_id, provider_calls, _review, status, finding = _qualified_review(
        tmp_path, monkeypatch
    )
    assert (
        main(
            [
                "quality",
                "laj",
                "review-status",
                "--workspace",
                str(workspace),
                "--json",
            ]
        )
        == 0
    )
    readback = json.loads(capsys.readouterr().out)
    assert readback["assessment_result_id"] == status["assessment_result_id"]
    assert readback["provider_calls"] == 0

    command = _disposition_payload(
        status,
        finding,
        request_id="headless-human-defer-1",
        decision="defer",
    )
    assert (
        main(
            [
                "quality",
                "laj",
                "disposition",
                "--workspace",
                str(workspace),
                "--request-json",
                json.dumps(command),
                "--json",
            ]
        )
        == 0
    )
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["ok"] is True
    assert recorded["replayed"] is False
    assert len(provider_calls) == 9
