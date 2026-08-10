"""Focused MU15-B Store/service coverage for report-bound Human observations."""

from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.product.post_final_review import (
    POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
    POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
    POST_FINAL_HUMAN_OBSERVATION_INPUT_SCHEMA,
    PostFinalReviewError,
    PostFinalReviewService,
)
from multi_agent_brief.semantic_evaluator.normalization import (
    make_span_locator,
    normalize_markdown,
)
from multi_agent_brief.semantic_evaluator.profile import load_profile
from tests.test_reader_review_backend import _reader_workspace
from multi_agent_brief.runtime_host_v2.projections import (
    build_finalized_local_review_projection,
)


def _observation_payload(
    *,
    request_id: str,
    text: str,
    span: dict[str, object] | None = None,
    scope_class: str | None = None,
    dimension_id: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": POST_FINAL_HUMAN_OBSERVATION_INPUT_SCHEMA,
        "human_actor_id": "human-observer-1",
        "human_request_id": request_id,
        "observation_text": text,
        "report_span": span,
        "scope_class": scope_class,
        "dimension_id": dimension_id,
    }


def _span_payload(workspace: Path, *, tamper: bool = False) -> dict[str, object]:
    projection = build_finalized_local_review_projection(workspace)
    report = projection.facts.report
    normalized = normalize_markdown(
        report.markdown_utf8,
        artifact_id=report.artifact_id,
        language="en",
    )
    block = normalized.artifact.blocks[0]
    span = make_span_locator(
        normalized.artifact,
        block_id=block.block_id,
        start_char=0,
        end_char=min(12, len(block.text)),
    )
    payload = span.model_dump(mode="json")
    if tamper:
        payload["excerpt_sha256"] = "0" * 64
    payload["schema_version"] = "briefloop.post_final_human_observation_report_span.v1"
    return payload


def test_report_bound_observations_are_append_only_and_exactly_replayable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id = _reader_workspace(tmp_path, monkeypatch)
    review = PostFinalReviewService(workspace)
    first_payload = _observation_payload(
        request_id="human-observation-independent-1",
        text="The finalized report leaves a decision dependency implicit.",
        span=_span_payload(workspace),
        scope_class="O1",
        dimension_id=load_profile("management_brief_en_v1")
        .profile.dimensions[0]
        .dimension_id,
    )
    first = review.record_human_observation(first_payload)
    assert first["origin"] == "human"
    assert first["observation_revision"] == 1
    assert review.record_human_observation(first_payload) == {
        **first,
        "replayed": True,
    }

    second_payload = _observation_payload(
        request_id="human-observation-independent-2",
        text="The risk boundary is not stated next to the recommendation.",
    )
    second = review.record_human_observation(second_payload)
    assert second["observation_revision"] == 1
    assert second["observation_id"] != first["observation_id"]

    supersede_payload = {
        **_observation_payload(
            request_id="human-observation-supersede-1",
            text="The finalized report should state the decision dependency explicitly.",
        ),
        "schema_version": "briefloop.post_final_human_observation_supersede_input.v1",
        "previous_observation_id": first["observation_id"],
        "previous_observation_fingerprint": first["observation_fingerprint"],
    }
    superseded = review.supersede_human_observation(supersede_payload)
    assert superseded["observation_revision"] == 2
    assert review.supersede_human_observation(supersede_payload) == {
        **superseded,
        "replayed": True,
    }
    changed = dict(supersede_payload)
    changed["observation_text"] = "Changed semantic text."
    with pytest.raises(
        PostFinalReviewError,
        match="post_final_review_request_conflict",
    ):
        review.supersede_human_observation(changed)

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(_run_id)
    assert len(snapshot.post_final_human_observations) == 3
    assert {
        item.observation_revision for item in snapshot.post_final_human_observations
    } == {1, 2}
    assert (
        len(
            [
                item
                for item in snapshot.post_final_human_observations
                if item.observation_revision == 1
            ]
        )
        == 2
    )


def test_report_span_replay_and_zero_result_guidance_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id = _reader_workspace(tmp_path, monkeypatch)
    review = PostFinalReviewService(workspace)
    invalid_span = _observation_payload(
        request_id="human-observation-tampered-span",
        text="The span excerpt was changed.",
        span=_span_payload(workspace, tamper=True),
    )
    with pytest.raises(
        PostFinalReviewError,
        match="post_final_review_report_span_invalid",
    ):
        review.record_human_observation(invalid_span)

    forged_requirement = _observation_payload(
        request_id="human-observation-forged-requirement",
        text="A forged requirement reference must not be stored.",
    )
    forged_requirement["requirement_id"] = "pf-laj-not-a-real-requirement"
    with pytest.raises(
        PostFinalReviewError,
        match="post_final_review_reference_invalid",
    ):
        review.record_human_observation(forged_requirement)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.load_snapshot(_run_id).post_final_human_observations == ()

    observation = review.record_human_observation(
        _observation_payload(
            request_id="human-observation-report-only-guidance",
            text="The report-only observation must remain attributable to the report.",
            scope_class="O1",
            dimension_id=load_profile("management_brief_en_v1")
            .profile.dimensions[0]
            .dimension_id,
        )
    )
    # The requirement inventory is derived from the frozen RunDirection, so a
    # report-only observation can bind a real requirement without a Reader
    # result or result payload.
    valid_requirement = review.record_human_observation(
        _observation_payload(
            request_id="human-observation-report-only-requirement",
            text="The objective requirement is explicit in the report context.",
        )
        | {"requirement_id": "pf-laj-objective"}
    )
    assert valid_requirement["origin"] == "human"
    draft_payload = {
        "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
        "human_actor_id": "human-observer-1",
        "human_request_id": "human-guidance-observation-1",
        "provenance_kind": "human_observation",
        "observation_id": observation["observation_id"],
        "observation_fingerprint": observation["observation_fingerprint"],
        "guidance_text": "Keep the report-only observation visible to the editor.",
    }
    draft = review.append_guidance_draft(draft_payload)
    approved = review.approve_guidance(
        {
            "schema_version": POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA,
            "human_actor_id": "human-observer-1",
            "human_request_id": "human-guidance-observation-approve-1",
            "guidance_id": draft["guidance_id"],
            "draft_revision": draft["draft_revision"],
        }
    )
    assert approved["replayed"] is False
    status = review.review_status()
    assert status["assessment_result_id"] is None
    assert status["reader_view_sha256"] is None
    assert status["human_observations"][0]["origin"] == "human"
    guidance = next(
        item
        for item in status["guidance_drafts"]
        if item["guidance_id"] == draft["guidance_id"]
    )
    assert guidance["provenance_kind"] == "human_observation"
    assert guidance["guidance_scope"] == "observation_only"
