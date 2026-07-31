"""Store-native PF-LAJ Human disposition and guidance lifecycle."""

from __future__ import annotations

from dataclasses import replace
import json
import shutil
import sqlite3

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import (
    ControlStoreIntegrityError,
    SQLiteControlStore,
)
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
from multi_agent_brief.product.projection_platform import (
    supports_retained_directory_publication,
)
from multi_agent_brief.control_store.serialization import canonical_json_bytes
from multi_agent_brief.product.post_final_assessment import _record_fingerprint
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_API_KEY_SETTING,
)
import multi_agent_brief.semantic_evaluator.runner as runner_module
from tests.test_finalized_local_review_facts import _finalized_local_workspace
from tests.test_post_final_assessment import (
    _fixture_service,
    _policy_payload,
    _schema9_finalized_local_workspace_upgraded,
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
    review = PostFinalReviewService(
        workspace,
        str(outcome["assessment_result_id"]),
        str(outcome["assessment_result_fingerprint"]),
    )
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
    deactivated = review.deactivate_guidance(
        {
            **current_status_payload,
            "human_request_id": "human-guidance-deactivate-2",
        }
    )
    assert deactivated["replayed"] is False

    later_rejection = review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="human-disposition-reject-after-guidance",
            decision="reject",
        )
    )
    assert later_rejection["replayed"] is False
    assert review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="human-disposition-accept-1",
            decision="accept",
        )
    ) == {**accepted, "replayed": True}
    assert review.append_guidance_draft(draft_payload) == {
        **draft,
        "replayed": True,
    }
    assert review.approve_guidance(approve_payload) == {
        **approved,
        "replayed": True,
    }
    assert review.approve_guidance(
        {
            **current_status_payload,
            "human_request_id": "human-guidance-approve-2",
        }
    ) == {**approval_2, "replayed": True}
    assert review.deactivate_guidance(
        {
            **current_status_payload,
            "human_request_id": "human-guidance-deactivate-2",
        }
    ) == {**deactivated, "replayed": True}
    changed_draft_replay = dict(draft_payload)
    changed_draft_replay["guidance_text"] = "Changed replay bytes."
    with pytest.raises(
        PostFinalReviewError,
        match="post_final_review_request_conflict",
    ):
        review.append_guidance_draft(changed_draft_replay)
    changed_status_replay = dict(approve_payload)
    changed_status_replay["draft_revision"] = 2
    with pytest.raises(
        PostFinalReviewError,
        match="post_final_review_request_conflict",
    ):
        review.approve_guidance(changed_status_replay)
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
    assert len(final_status["guidance_statuses"]) == 3
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


def test_guidance_status_transition_table_and_ui_actions_fail_closed(
    tmp_path,
    monkeypatch,
) -> None:
    workspace, run_id, _calls, review, status, finding = _qualified_review(
        tmp_path,
        monkeypatch,
    )
    accepted = review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="transition-disposition-accept",
            decision="accept",
        )
    )
    draft = review.append_guidance_draft(
        {
            "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-1",
            "human_request_id": "transition-guidance-draft",
            "assessment_result_id": status["assessment_result_id"],
            "finding_id": finding["finding_id"],
            "disposition_id": accepted["disposition_id"],
            "guidance_text": "Keep the recommendation within the evidence boundary.",
        }
    )
    current = review.review_status()
    draft_row = next(
        item
        for item in current["guidance_drafts"]
        if item["guidance_id"] == draft["guidance_id"]
        and item["draft_revision"] == draft["draft_revision"]
    )
    assert draft_row["legal_actions"] == ["approve"]

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_invalid = store.current_revision
    for action_name, action in (
        ("deactivate", review.deactivate_guidance),
        ("revert", review.revert_guidance),
        ("supersede", review.supersede_guidance),
    ):
        with pytest.raises(
            PostFinalReviewError,
            match="post_final_guidance_transition_invalid",
        ):
            action(
                {
                    "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
                    "human_actor_id": "human-reviewer-1",
                    "human_request_id": f"transition-{action_name}-unapproved",
                    "guidance_id": draft["guidance_id"],
                    "draft_revision": draft["draft_revision"],
                }
            )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_invalid

    rejected = review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="transition-disposition-reject-before-approval",
            decision="reject",
        )
    )
    current = review.review_status()
    draft_row = next(
        item
        for item in current["guidance_drafts"]
        if item["guidance_id"] == draft["guidance_id"]
    )
    assert draft_row["legal_actions"] == []
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_rejected_approval = store.current_revision
    with pytest.raises(PostFinalReviewError, match="post_final_guidance_stale"):
        review.approve_guidance(
            {
                "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
                "human_actor_id": "human-reviewer-1",
                "human_request_id": "transition-approve-after-reject",
                "guidance_id": draft["guidance_id"],
                "draft_revision": draft["draft_revision"],
            }
        )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_rejected_approval

    reaccepted = review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="transition-disposition-reaccept",
            decision="accept",
        )
    )
    draft = review.append_guidance_draft(
        {
            "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-1",
            "human_request_id": "transition-guidance-draft-current-accept",
            "assessment_result_id": status["assessment_result_id"],
            "finding_id": finding["finding_id"],
            "disposition_id": reaccepted["disposition_id"],
            "guidance_text": (
                "Keep the recommendation within the current accepted evidence boundary."
            ),
        }
    )
    current = review.review_status()
    draft_row = next(
        item
        for item in current["guidance_drafts"]
        if item["guidance_id"] == draft["guidance_id"]
        and item["draft_revision"] == draft["draft_revision"]
    )
    assert draft_row["legal_actions"] == ["approve"]

    approve_payload = {
        "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
        "human_actor_id": "human-reviewer-1",
        "human_request_id": "transition-approve",
        "guidance_id": draft["guidance_id"],
        "draft_revision": draft["draft_revision"],
    }
    approved = review.approve_guidance(approve_payload)
    current = review.review_status()
    draft_row = next(
        item
        for item in current["guidance_drafts"]
        if item["guidance_id"] == draft["guidance_id"]
        and item["draft_revision"] == draft["draft_revision"]
    )
    assert draft_row["legal_actions"] == [
        "deactivate",
        "revert",
        "supersede",
    ]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        approved_snapshot = store.load_snapshot(run_id)
    approved_status = next(
        item
        for item in approved_snapshot.post_final_guidance_statuses
        if item.status_revision_id == approved["status_revision_id"]
    )
    for forged_status in ("deactivated", "reverted", "superseded"):
        forged_workspace = tmp_path / f"forged-{forged_status}"
        shutil.copytree(workspace, forged_workspace)
        payload = approved_status.model_dump(mode="json", exclude_unset=False)
        payload["status"] = forged_status
        payload["status_fingerprint"] = _record_fingerprint(
            payload,
            "status_fingerprint",
        )
        connection = sqlite3.connect(forged_workspace / "briefloop.db")
        try:
            connection.execute("DROP TRIGGER post_final_guidance_statuses_no_update")
            connection.execute(
                "UPDATE post_final_guidance_statuses "
                "SET status=?,status_fingerprint=?,payload_json=? "
                "WHERE run_id=? AND status_revision_id=?",
                (
                    forged_status,
                    payload["status_fingerprint"],
                    canonical_json_bytes(payload).decode("utf-8"),
                    run_id,
                    approved_status.status_revision_id,
                ),
            )
            connection.execute(
                "CREATE TRIGGER post_final_guidance_statuses_no_update "
                "BEFORE UPDATE ON post_final_guidance_statuses "
                "BEGIN SELECT RAISE(ABORT,'append_only'); END"
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(
            ControlStoreIntegrityError,
            match="control_store_integrity_invalid",
        ):
            with SQLiteControlStore.open(
                forged_workspace / "briefloop.db"
            ) as forged_store:
                forged_store.load_snapshot(run_id)

    later_rejection = review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="transition-disposition-reject-after-approval",
            decision="reject",
        )
    )
    current = review.review_status()
    draft_row = next(
        item
        for item in current["guidance_drafts"]
        if item["guidance_id"] == draft["guidance_id"]
        and item["draft_revision"] == draft["draft_revision"]
    )
    assert draft_row["legal_actions"] == [
        "deactivate",
        "revert",
        "supersede",
    ]

    forged_workspace = tmp_path / "forged-reject-before-approval"
    shutil.copytree(workspace, forged_workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        chronology_snapshot = store.load_snapshot(run_id)
    approval_receipt = next(
        item
        for item in chronology_snapshot.transactions
        if item.transaction_id == approved["receipt_id"]
    )
    rejection_receipt = next(
        item
        for item in chronology_snapshot.transactions
        if item.transaction_id == later_rejection["receipt_id"]
    )
    connection = sqlite3.connect(forged_workspace / "briefloop.db")
    try:
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='trigger' AND name='transactions_no_update'"
        ).fetchone()
        if trigger_sql is None:
            raise AssertionError("transactions update trigger missing")
        connection.execute("DROP TRIGGER transactions_no_update")
        temporary_prior = (
            int(connection.execute("SELECT revision FROM workspaces").fetchone()[0])
            + 10
        )
        temporary_approval = approval_receipt.model_copy(
            update={
                "prior_revision": temporary_prior,
                "committed_revision": temporary_prior + 1,
            }
        )
        forged_rejection = rejection_receipt.model_copy(
            update={
                "prior_revision": approval_receipt.prior_revision,
                "committed_revision": approval_receipt.committed_revision,
            }
        )
        forged_approval = approval_receipt.model_copy(
            update={
                "prior_revision": rejection_receipt.prior_revision,
                "committed_revision": rejection_receipt.committed_revision,
            }
        )
        for receipt in (
            temporary_approval,
            forged_rejection,
            forged_approval,
        ):
            connection.execute(
                "UPDATE transactions "
                "SET prior_revision=?,committed_revision=?,payload_json=? "
                "WHERE run_id=? AND transaction_id=?",
                (
                    receipt.prior_revision,
                    receipt.committed_revision,
                    canonical_json_bytes(
                        receipt.model_dump(mode="json", exclude_unset=False)
                    ).decode("utf-8"),
                    receipt.run_id,
                    receipt.transaction_id,
                ),
            )
        connection.execute(str(trigger_sql[0]))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(
        ControlStoreIntegrityError,
        match="control_store_integrity_invalid",
    ):
        with SQLiteControlStore.open(forged_workspace / "briefloop.db") as forged_store:
            forged_store.load_snapshot(run_id)

    deactivated = review.deactivate_guidance(
        {
            **approve_payload,
            "human_request_id": "transition-deactivate",
        }
    )
    assert deactivated["replayed"] is False
    current = review.review_status()
    draft_row = next(
        item
        for item in current["guidance_drafts"]
        if item["guidance_id"] == draft["guidance_id"]
    )
    assert draft_row["legal_actions"] == []
    for index, (terminal_status, action) in enumerate(
        (
            ("reverted", review.revert_guidance),
            ("superseded", review.supersede_guidance),
        ),
        start=1,
    ):
        other_finding = status["dispositions"][index]
        other_disposition = review.record_disposition(
            _disposition_payload(
                status,
                other_finding,
                request_id=f"transition-{terminal_status}-accept",
                decision="accept",
            )
        )
        other_draft = review.append_guidance_draft(
            {
                "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
                "human_actor_id": "human-reviewer-1",
                "human_request_id": f"transition-{terminal_status}-draft",
                "assessment_result_id": status["assessment_result_id"],
                "finding_id": other_finding["finding_id"],
                "disposition_id": other_disposition["disposition_id"],
                "guidance_text": (
                    f"Preserve the exact {terminal_status} lifecycle evidence."
                ),
            }
        )
        other_status_payload = {
            "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-1",
            "human_request_id": f"transition-{terminal_status}-approve",
            "guidance_id": other_draft["guidance_id"],
            "draft_revision": other_draft["draft_revision"],
        }
        review.approve_guidance(other_status_payload)
        action(
            {
                **other_status_payload,
                "human_request_id": f"transition-{terminal_status}",
            }
        )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_terminal = store.current_revision
    for action_name, action in (
        ("revert", review.revert_guidance),
        ("supersede", review.supersede_guidance),
    ):
        with pytest.raises(
            PostFinalReviewError,
            match="post_final_guidance_transition_invalid",
        ):
            action(
                {
                    **approve_payload,
                    "human_request_id": f"transition-{action_name}-after-deactivate",
                }
            )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_terminal
        snapshot = store.load_snapshot(run_id)
    assert {item.status for item in snapshot.post_final_guidance_statuses} == {
        "approved",
        "deactivated",
        "reverted",
        "superseded",
    }
    assert approved["status_revision_id"] != deactivated["status_revision_id"]


@pytest.mark.explicit_e2e
@pytest.mark.timeout(900)
@pytest.mark.skipif(
    not supports_retained_directory_publication(),
    reason="successful finalized-local Human review is unavailable on this platform",
)
def test_schema9_finalized_local_upgrade_runs_full_laj_human_loop(
    tmp_path,
    monkeypatch,
) -> None:
    workspace, run_id, historical_receipts = (
        _schema9_finalized_local_workspace_upgraded(tmp_path, monkeypatch)
    )
    calls: list[tuple[str, int]] = []
    assessment = _fixture_service(workspace, calls, terminal_mode="finding")
    assert assessment.policy_set(_policy_payload())["ok"] is True
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    assert assessment.assess()["status"] == "available"
    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
    result = snapshot.post_final_assessment_results[0]
    review = PostFinalReviewService(
        workspace,
        result.assessment_result_id,
        result.result_fingerprint,
    )
    status = review.review_status()
    finding = status["dispositions"][0]
    disposition = review.record_disposition(
        _disposition_payload(
            status,
            finding,
            request_id="schema9-upgrade-accept",
            decision="accept",
        )
    )
    draft = review.append_guidance_draft(
        {
            "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-1",
            "human_request_id": "schema9-upgrade-draft",
            "assessment_result_id": status["assessment_result_id"],
            "finding_id": finding["finding_id"],
            "disposition_id": disposition["disposition_id"],
            "guidance_text": "Keep conclusions aligned with the frozen report.",
        }
    )
    review.approve_guidance(
        {
            "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
            "human_actor_id": "human-reviewer-1",
            "human_request_id": "schema9-upgrade-approve",
            "guidance_id": draft["guidance_id"],
            "draft_revision": draft["draft_revision"],
        }
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        stored_receipts = {
            str(row[0]): str(row[1]).encode("utf-8")
            for row in store._connection.execute(
                "SELECT transaction_id,payload_json FROM transactions"
            ).fetchall()
            if str(row[0]) in historical_receipts
        }
    assert stored_receipts == historical_receipts
    CoreRunDomainVerifier().verify_history(history)
    assert len(calls) == 9


def test_tampered_or_cross_bound_finding_is_zero_write(tmp_path, monkeypatch) -> None:
    workspace, run_id, provider_calls, review, status, finding = _qualified_review(
        tmp_path, monkeypatch
    )
    database_before = (workspace / "briefloop.db").read_bytes()
    with pytest.raises(
        PostFinalReviewError,
        match="post_final_review_",
    ):
        PostFinalReviewService(
            workspace,
            status["assessment_result_id"],
            "0" * 64,
        ).review_status()
    assert (workspace / "briefloop.db").read_bytes() == database_before
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
                "--assessment-result-id",
                status["assessment_result_id"],
                "--assessment-result-fingerprint",
                status["assessment_result_fingerprint"],
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
                "--assessment-result-id",
                status["assessment_result_id"],
                "--assessment-result-fingerprint",
                status["assessment_result_fingerprint"],
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
