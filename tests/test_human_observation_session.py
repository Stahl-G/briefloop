"""MU15-B ordinary-user Human observation transport and projection affordance."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from multi_agent_brief.product.review_session.contracts import (
    HumanGuidanceDraftInput,
    HumanObservationInput,
    HumanObservationSupersedeInput,
    ReviewSessionCommand,
    SuccessorStartInput,
)
from multi_agent_brief.contracts.v2 import RunDirection
from multi_agent_brief.product.brief_html.builder import (
    _improvement_page,
    build_brief_pages_data,
)
from multi_agent_brief.product.post_final_review import (
    POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
    PostFinalReviewService,
)
from multi_agent_brief.product.post_final_assessment_projection import (
    PostFinalAssessmentProjection,
    _build_human_observation_status,
)
from multi_agent_brief.semantic_evaluator.reader import build_empty_laj_reader_view


def _observation_payload() -> dict[str, object]:
    return {
        "schema_version": "briefloop.post_final_human_observation_input.v1",
        "human_actor_id": "local-human-reviewer",
        "human_request_id": "human-observation-1",
        "observation_text": "The recommendation omits the stated decision dependency.",
    }


def test_report_bound_observation_is_valid_without_assessment_result() -> None:
    observation = HumanObservationInput.model_validate(
        _observation_payload(), strict=True
    )
    assert observation.assessment_result_id is None
    assert observation.scope_class is None


def test_observation_requires_total_result_and_dimension_bindings() -> None:
    partial_result = _observation_payload()
    partial_result["assessment_result_id"] = "assessment-result-1"
    with pytest.raises(ValidationError):
        HumanObservationInput.model_validate(partial_result, strict=True)

    partial_dimension = _observation_payload()
    partial_dimension["scope_class"] = "O2"
    with pytest.raises(ValidationError):
        HumanObservationInput.model_validate(partial_dimension, strict=True)


def test_observation_span_is_exact_and_strict() -> None:
    payload = _observation_payload()
    payload["report_span"] = {
        "schema_version": "briefloop.post_final_human_observation_report_span.v1",
        "report_sha256": "a" * 64,
        "block_id": "B000001",
        "start_char": 4,
        "end_char": 15,
        "excerpt_sha256": "b" * 64,
    }
    payload["scope_class"] = "O1"
    payload["dimension_id"] = "uncertainty_calibration"
    observed = HumanObservationInput.model_validate(payload, strict=True)
    assert observed.report_span is not None
    assert observed.report_span.block_id == "B000001"

    malformed = dict(payload)
    malformed["unexpected_dom_authority"] = "not-accepted"
    with pytest.raises(ValidationError):
        HumanObservationInput.model_validate(malformed, strict=True)


def test_supersede_is_a_new_text_revision_bound_to_predecessor() -> None:
    payload = {
        **_observation_payload(),
        "schema_version": "briefloop.post_final_human_observation_supersede_input.v1",
        "observation_text": "A replacement Human observation.",
        "previous_observation_id": "pf-human-observation-1",
        "previous_observation_fingerprint": "c" * 64,
    }
    supersede = HumanObservationSupersedeInput.model_validate(payload, strict=True)
    assert supersede.previous_observation_id == "pf-human-observation-1"


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


def test_successor_command_requires_explicit_guidance_choice_and_direction() -> None:
    direction = {
        "schema_version": "briefloop.run_direction.v2",
        "subject_name": "Example Co",
        "industry_or_theme": "Technology",
        "brief_title": "Monthly management brief",
        "report_type": "management_monthly",
        "task_objective": "Explain the current operating picture.",
        "audience": "Management",
        "audience_profile": "Executive decision makers",
        "output_language": "en",
        "source_handling": "Public sources only",
        "cadence": "monthly",
        "focus_areas": ["Operations"],
        "excluded_topics": ["Personnel"],
        "forbidden_sources": [],
        "source_profile": "public_web",
        "web_search_mode": "disabled",
        "output_formats": ["markdown"],
        "report_date": "2026-08-08",
        "target_terms": ["revenue"],
    }
    payload = {
        "schema_version": "briefloop.post_final_successor_start_input.v1",
        "successor_run_id": "successor-20260808-management-1",
        "run_direction": direction,
        "include_approved_guidance": False,
    }
    successor = SuccessorStartInput.model_validate(payload, strict=True)
    assert successor.include_approved_guidance is False
    command = ReviewSessionCommand.model_validate(
        {
            "schema_version": "briefloop.post_final_review.command.v1",
            "action": "start_successor",
            "payload": payload,
        },
        strict=True,
    )
    assert command.action == "start_successor"

    missing_choice = dict(payload)
    missing_choice.pop("include_approved_guidance")
    with pytest.raises(ValidationError):
        SuccessorStartInput.model_validate(missing_choice, strict=True)


def test_guidance_provenance_union_is_complete_and_disjoint() -> None:
    model_payload = {
        "schema_version": "briefloop.post_final_guidance_draft_input.v1",
        "human_actor_id": "local-human-reviewer",
        "human_request_id": "guidance-model-1",
        "provenance_kind": "accepted_model_finding",
        "assessment_result_id": "assessment-result-1",
        "assessment_result_fingerprint": "a" * 64,
        "finding_id": "finding-1",
        "finding_fingerprint": "b" * 64,
        "disposition_id": "disposition-1",
        "disposition_fingerprint": "c" * 64,
        "guidance_text": "Align the recommendation with the stated dependency.",
    }
    assert HumanGuidanceDraftInput.model_validate(model_payload, strict=True)
    missing_result_fingerprint = dict(model_payload)
    missing_result_fingerprint.pop("assessment_result_fingerprint")
    with pytest.raises(ValidationError):
        HumanGuidanceDraftInput.model_validate(missing_result_fingerprint, strict=True)

    observation_payload = {
        "schema_version": "briefloop.post_final_guidance_draft_input.v1",
        "human_actor_id": "local-human-reviewer",
        "human_request_id": "guidance-observation-1",
        "provenance_kind": "human_observation",
        "observation_id": "observation-1",
        "observation_fingerprint": "d" * 64,
        "guidance_text": "Make the dependency explicit in the recommendation.",
    }
    assert HumanGuidanceDraftInput.model_validate(observation_payload, strict=True)
    partial_result = dict(observation_payload)
    partial_result["assessment_result_id"] = "assessment-result-1"
    with pytest.raises(ValidationError):
        HumanGuidanceDraftInput.model_validate(partial_result, strict=True)
    mixed_provenance = dict(observation_payload)
    mixed_provenance["finding_id"] = "finding-1"
    with pytest.raises(ValidationError):
        HumanGuidanceDraftInput.model_validate(mixed_provenance, strict=True)


def test_finalized_report_keeps_observation_affordance_without_review_result() -> None:
    local = SimpleNamespace(
        view_state="finalized",
        run_id="run-1",
        reader_brief=SimpleNamespace(
            state="available",
            artifact_id="reader_brief",
            revision=3,
            sha256="a" * 64,
        ),
    )
    qualified = PostFinalAssessmentProjection(
        lifecycle_present=False,
        status="not_requested",
        reason_code="laj_not_run",
        view=build_empty_laj_reader_view(
            status="not_available", reason_code="laj_not_run"
        ),
        user_status="not_assessed",
        compatible_result_options=(),
        requirement_labels=(),
        selected_result_id=None,
        selected_result_fingerprint=None,
        review_status=None,
        request_template=None,
        next_run_consumption="explicit_opt_in_successor_only",
        run_action_available=False,
        selection_required=False,
    )
    page = _improvement_page(local, qualified)
    assert page["status"] == "available"
    assert page["observation_allowed"] is True
    assert page["observation_binding_mode"] == "report_bound"
    assert page["human_observations"] == []


def test_report_only_observation_guidance_is_visible_with_approval_action() -> None:
    class _Dumpable:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

        def model_dump(self, *, mode: str, exclude_unset: bool) -> dict[str, object]:
            del mode, exclude_unset
            return dict(self.__dict__)

    lineage = "a" * 64
    observation = _Dumpable(
        run_id="run-1",
        finalized_lineage_fingerprint=lineage,
        observation_id="observation-1",
        observation_revision=1,
        accepted_transaction_id="observation-tx-1",
        observation_fingerprint="b" * 64,
        previous_observation_id=None,
        origin="human",
        observation_text="The report leaves a dependency implicit.",
    )
    guidance = _Dumpable(
        run_id="run-1",
        finalized_lineage_fingerprint=lineage,
        provenance_kind="human_observation",
        guidance_id="guidance-1",
        draft_revision=1,
        accepted_transaction_id="guidance-tx-1",
        observation_id=observation.observation_id,
        observation_fingerprint=observation.observation_fingerprint,
        guidance_scope="observation_only",
        guidance_text="State the dependency beside the recommendation.",
    )
    snapshot = SimpleNamespace(
        transactions=(
            _Dumpable(transaction_id="observation-tx-1", committed_revision=1),
            _Dumpable(transaction_id="guidance-tx-1", committed_revision=2),
        ),
        post_final_human_observations=(observation,),
        post_final_guidance_drafts=(guidance,),
        post_final_guidance_statuses=(),
    )
    review_status = _build_human_observation_status(
        snapshot=snapshot,
        run_id="run-1",
        finalized_lineage=lineage,
    )
    assert review_status["guidance_drafts"][0]["legal_actions"] == ["approve"]

    local = SimpleNamespace(
        view_state="finalized",
        run_id="run-1",
        reader_brief=SimpleNamespace(
            state="available",
            artifact_id="reader_brief",
            revision=3,
            sha256="c" * 64,
        ),
    )
    qualified = PostFinalAssessmentProjection(
        lifecycle_present=False,
        status="not_requested",
        reason_code="laj_not_run",
        view=build_empty_laj_reader_view(
            status="not_available", reason_code="laj_not_run"
        ),
        user_status="not_assessed",
        compatible_result_options=(),
        requirement_labels=(),
        selected_result_id=None,
        selected_result_fingerprint=None,
        review_status=review_status,
        request_template=None,
        next_run_consumption="explicit_opt_in_successor_only",
        run_action_available=False,
        selection_required=False,
    )
    page = _improvement_page(local, qualified)
    assert page["recorded"][0]["legal_actions"] == ["approve"]
    assert len(
        {(row["guidance_id"], row["draft_revision"]) for row in page["recorded"]}
    ) == len(page["recorded"])


def test_not_run_report_page_data_shows_human_guidance_approval_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_reader_review_backend import _reader_workspace

    workspace, _run_id = _reader_workspace(tmp_path, monkeypatch)
    review = PostFinalReviewService(workspace)
    observation = review.record_human_observation(
        {
            "schema_version": "briefloop.post_final_human_observation_input.v1",
            "human_actor_id": "local-human-reviewer",
            "human_request_id": "page-observation-1",
            "observation_text": "The report leaves a dependency implicit.",
        }
    )
    review.append_guidance_draft(
        {
            "schema_version": POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA,
            "human_actor_id": "local-human-reviewer",
            "human_request_id": "page-guidance-1",
            "provenance_kind": "human_observation",
            "observation_id": observation["observation_id"],
            "observation_fingerprint": observation["observation_fingerprint"],
            "guidance_text": "State the dependency beside the recommendation.",
        }
    )
    page_data = build_brief_pages_data(workspace)
    rows = page_data["improvement"]["recorded"]
    assert len(rows) == 1
    assert rows[0]["provenance_kind"] == "human_observation"
    assert rows[0]["guidance_scope"] == "observation_only"
    assert rows[0]["legal_actions"] == ["approve"]


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


def test_successor_handler_uses_frozen_direction_and_no_post_commit_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    direction = RunDirection.model_validate(
        {
            "schema_version": "briefloop.run_direction.v2",
            "subject_name": "Example Co",
            "brief_title": "Monthly management brief",
            "task_objective": "Explain the current operating picture.",
            "audience": "Management",
            "audience_profile": "Executive decision makers",
            "output_language": "en",
            "source_handling": "Public sources only",
            "cadence": "monthly",
            "focus_areas": ["Operations"],
            "excluded_topics": ["Personnel"],
            "forbidden_sources": [],
            "source_profile": "public_web",
            "web_search_mode": "disabled",
            "output_formats": ["markdown"],
            "report_date": "2026-08-08",
            "target_terms": ["revenue"],
        },
        strict=True,
    )
    page_data = {
        "schema_version": "briefloop.brief_pages.data.v2",
        "workspace": {"run_id": "run-1"},
        "semantic": {
            "selected_result_id": None,
            "selected_result_fingerprint": None,
            "status": "not_run",
            "compatible_result_options": [],
        },
        "improvement": {
            "next_run_consumption": "explicit_opt_in_successor_only",
        },
        "successor": {
            "available": True,
            "predecessor_run_id": "run-1",
            "run_direction": direction.model_dump(mode="json", exclude_unset=False),
            "approved_guidance": [],
            "include_default": False,
        },
    }
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.builder.build_brief_pages_data",
        lambda *_args, **_kwargs: page_data,
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.render.render_brief_pages_html",
        lambda _data: (
            b"<html><head><style>x</style></head><body><script>0</script></body></html>"
        ),
    )

    class _Snapshot:
        workspace_run_head = SimpleNamespace(current_run_id="run-1")
        run_contract_bindings = (SimpleNamespace(run_direction=direction),)

    class _History:
        snapshots = (_Snapshot(),)
        store_revision = 1

        def snapshot_at_revision(self, _run_id: str, _revision: int):
            return self.snapshots[0]

    class _Store:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def load_history(self):
            return _History()

    monkeypatch.setattr(
        "multi_agent_brief.control_store.SQLiteControlStore.open",
        lambda *_args, **_kwargs: _Store(),
    )
    calls: list[bool] = []

    class _Result:
        status = "committed"
        error_code = None

        def to_dict(self):
            return {"status": self.status, "successor_run_id": "successor-1"}

    class _Runtime:
        def __init__(self, *_args, **_kwargs):
            pass

        def start_successor(self, **kwargs):
            calls.append(kwargs["include_approved_guidance"])
            return _Result()

    monkeypatch.setattr(
        "multi_agent_brief.runtime_host_v2.codex.workspace_codex_adapter_loader",
        lambda _workspace: object(),
    )
    monkeypatch.setattr(
        "multi_agent_brief.runtime_host_v2.service.RuntimeHostService", _Runtime
    )
    captured: dict[str, object] = {}

    class _Server:
        url = "http://127.0.0.1:9"

        def start(self):
            return None

    def server_factory(*_args, **kwargs):
        captured["handler"] = kwargs["command_handler"]
        return _Server()

    monkeypatch.setattr(
        "multi_agent_brief.product.review_session.launcher.create_review_session_server",
        server_factory,
    )
    launched = __import__(
        "multi_agent_brief.product.review_session.launcher",
        fromlist=["launch_actionable_review_session"],
    ).launch_actionable_review_session(tmp_path, open_browser=False)
    assert launched.url == "http://127.0.0.1:9"
    handler = captured["handler"]
    for index, include in enumerate((False, True), start=1):
        command = ReviewSessionCommand.model_validate(
            {
                "schema_version": "briefloop.post_final_review.command.v1",
                "action": "start_successor",
                "payload": {
                    "schema_version": "briefloop.post_final_successor_start_input.v1",
                    "successor_run_id": f"successor-{index}",
                    "run_direction": direction.model_dump(
                        mode="json", exclude_unset=False
                    ),
                    "include_approved_guidance": include,
                },
            },
            strict=True,
        )
        response = handler(command)
        assert response["ok"] is True
        assert "page_data" not in response
    assert calls == [False, True]

    class _FailedRuntime(_Runtime):
        def start_successor(self, **_kwargs):
            result = _Result()
            result.status = "failed"
            result.error_code = "successor_start_failed"
            return result

    monkeypatch.setattr(
        "multi_agent_brief.runtime_host_v2.service.RuntimeHostService", _FailedRuntime
    )
    failed = handler(
        ReviewSessionCommand.model_validate(
            {
                "schema_version": "briefloop.post_final_review.command.v1",
                "action": "start_successor",
                "payload": {
                    "schema_version": "briefloop.post_final_successor_start_input.v1",
                    "successor_run_id": "successor-failed",
                    "run_direction": direction.model_dump(
                        mode="json", exclude_unset=False
                    ),
                    "include_approved_guidance": False,
                },
            },
            strict=True,
        )
    )
    assert failed["ok"] is False
    assert failed["reason_code"] == "successor_start_failed"
