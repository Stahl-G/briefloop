"""MU15-B ordinary-user Human observation transport and projection affordance."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from multi_agent_brief.product.review_session.contracts import (
    ReviewSessionCommand,
)


def _observation_payload() -> dict[str, object]:
    return {
        "schema_version": "briefloop.post_final_human_observation_input.v1",
        "human_actor_id": "local-human-reviewer",
        "human_request_id": "human-observation-1",
        "observation_text": "The recommendation omits the stated decision dependency.",
    }


def test_session_commands_validate_observation_actions_before_service() -> None:
    command = ReviewSessionCommand.model_validate(
        {
            "schema_version": "briefloop.post_final_review.command.v1",
            "action": "append_observation",
            "payload": _observation_payload(),
        },
        strict=True,
    )
    assert command.action == "append_observation"

    malformed = _observation_payload()
    malformed["assessment_result_id"] = "only-half-bound"
    with pytest.raises(ValidationError):
        ReviewSessionCommand.model_validate(
            {
                "schema_version": "briefloop.post_final_review.command.v1",
                "action": "append_observation",
                "payload": malformed,
            },
            strict=True,
        )


def test_static_export_contains_human_observation_copy_but_no_write_transport() -> None:
    app = Path("src/multi_agent_brief/product/brief_html/static/app.js").read_text(
        encoding="utf-8"
    )
    assert 'sendReviewCommand("append_observation"' in app
    assert 'sendReviewCommand("supersede_observation"' in app
    assert "origin=Human" in app
    assert "local-human-reviewer" in app
    assert "session_reopen" in app
    assert "session_disconnected" in app
    assert "response.text()" in app
    assert "pendingRequestId" in app
    assert 'human_request_id: form.requestId || requestId("human-observation")' in app
    # Static exports have no session token and therefore never enable these
    # command controls; the canonical app only sends over the secured route.
    assert '"/api/v1/command?session_id="' in app
    assert "AI 第二意见" in app
    assert "AI Second Opinion" in app
    assert 'sendReviewCommand("start_successor"' in app
    assert "include_approved_guidance" in app
