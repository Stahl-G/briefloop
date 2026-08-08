"""MU 15-A Reader Review backend and deterministic projection rows."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.test_finalized_local_review_facts import _finalized_local_workspace

from multi_agent_brief.contracts.v2 import (
    CoreRunInitializeRequest,
    ReaderReviewAssessmentInput,
    derive_reader_review_result_status,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.product.post_final_assessment import (
    POST_FINAL_ASSESSMENT_POLICY_SCHEMA,
    READER_REVIEW_LANGUAGE,
    READER_REVIEW_MAX_OUTPUT_TOKENS_PER_CALL,
    READER_REVIEW_MAX_PROVIDER_CALLS,
    READER_REVIEW_MAX_TOTAL_INPUT_TOKENS,
    READER_REVIEW_MAX_TOTAL_OUTPUT_TOKENS,
    READER_REVIEW_REPORT_TYPE,
    PostFinalAssessmentError,
    PostFinalAssessmentService,
)
from multi_agent_brief.product.post_final_assessment_projection import (
    build_post_final_assessment_projection,
)
import multi_agent_brief.product.post_final_assessment as assessment_module
import multi_agent_brief.product.post_final_assessment_projection as projection_module
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_API_KEY_SETTING,
    AnthropicMessagesAdapterV1,
    synthetic_anthropic_message_bytes_v1,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    _rubric_from_prompt,
)
from multi_agent_brief.semantic_evaluator.archive import trial_archive_path
from multi_agent_brief.semantic_evaluator.contracts import (
    DIMENSION_RESPONSE_SCHEMA_ID,
    BoundedRequirement,
    DimensionResponse,
    FindingDraft,
    FindingEmittedResult,
    NoFindingResult,
    O2RequirementAssessment,
)
from multi_agent_brief.semantic_evaluator.normalization import (
    freeze_bounded_context,
    make_span_locator,
)
from multi_agent_brief.semantic_evaluator.profile import load_profile
from multi_agent_brief.semantic_evaluator.runner import (
    PreparedShadowRun,
    prepare_shadow_run_from_bytes,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes
from multi_agent_brief.semantic_evaluator.validator import (
    validate_dimension_response,
)
import multi_agent_brief.semantic_evaluator.runner as runner_module


_ENDPOINT = "https://messages.example.test/v1"
_MODEL = "public-reader-review-model-v1"


def _reader_input(human_request_id: str) -> dict[str, object]:
    return {
        "schema_version": ReaderReviewAssessmentInput.schema_id,
        "human_actor_id": "human-reader-review-1",
        "human_request_id": human_request_id,
        "disclosure_confirmed": True,
        "messages_endpoint": _ENDPOINT,
        "requested_model_id": _MODEL,
        "model_version": _MODEL,
        "expected_model_identity": _MODEL,
        "public_safe_egress_attested": True,
        "cost_status": "not_measured",
    }


def _reader_policy_payload(
    service: PostFinalAssessmentService,
    human_request_id: str,
) -> dict[str, object]:
    request = ReaderReviewAssessmentInput.model_validate(
        _reader_input(f"{human_request_id}-authorization"),
        strict=True,
    )
    config = service._reader_review_instrument(request)
    return {
        "schema_version": POST_FINAL_ASSESSMENT_POLICY_SCHEMA,
        "human_actor_id": request.human_actor_id,
        "human_request_id": human_request_id,
        "enabled": True,
        "auto_run": False,
        "auto_open": False,
        "messages_endpoint": request.messages_endpoint,
        "requested_model_id": request.requested_model_id,
        "model_version": request.model_version,
        "expected_model_identity": request.expected_model_identity,
        "instrument_config": config.model_dump(mode="json"),
        "max_provider_calls": READER_REVIEW_MAX_PROVIDER_CALLS,
        "max_total_input_tokens": READER_REVIEW_MAX_TOTAL_INPUT_TOKENS,
        "max_total_output_tokens": READER_REVIEW_MAX_TOTAL_OUTPUT_TOKENS,
        "max_output_tokens_per_call": READER_REVIEW_MAX_OUTPUT_TOKENS_PER_CALL,
        "public_safe_egress_attested": True,
        "assessment_kind": "reader_review",
        "report_type": READER_REVIEW_REPORT_TYPE,
        "language": READER_REVIEW_LANGUAGE,
        "disclosure_confirmed": True,
        "cost_status": "not_measured",
    }


def _tagged_json(text: str, tag: str) -> dict[str, object]:
    start_marker = f"<{tag}>\n"
    end_marker = f"\n</{tag}>"
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker, start)
    value = json.loads(text[start:end])
    assert isinstance(value, dict)
    return value


class _ReaderMessagesAdapter:
    """Two-call public fixture for the Messages-compatible protocol."""

    def __init__(
        self,
        execution,
        calls: list[tuple[str, int]],
        *,
        terminal_mode: str = "no_finding",
    ) -> None:
        self.adapter_id = execution.adapter_id
        self.adapter_version = execution.adapter_version
        self.provider_sdk_name = execution.provider_sdk_name
        self.provider_sdk_version = execution.provider_sdk_version
        self.qualification_eligible = execution.qualification_eligible
        self.base_url = _ENDPOINT
        self._delegate = object.__new__(AnthropicMessagesAdapterV1)
        self._calls = calls
        self._terminal_mode = terminal_mode

    def invoke(self, request):
        self._calls.append((request.dimension_id, request.attempt_ordinal))
        rubric = _rubric_from_prompt(request.user_text)
        context = _tagged_json(request.user_text, "BOUNDED_CONTEXT_DATA")
        units = rubric["assessment_units"]
        assert isinstance(units, list)
        requirements = context["requirements"]
        assert isinstance(requirements, list)
        unit_results = [
            {
                "assessment_unit_id": unit["assessment_unit_id"],
                "disposition": "no_finding",
            }
            for unit in units
        ]
        if (
            self._terminal_mode == "finding"
            and rubric["dimension"]["scope_class"] == "O1"
        ):
            report = _tagged_json(request.user_text, "REPORT_DATA")
            span = report["span_locator_contract"]["full_block_candidates"][0]
            for index, unit in enumerate(units[:3]):
                unit_results[index] = {
                    "assessment_unit_id": unit["assessment_unit_id"],
                    "disposition": "finding_emitted",
                    "findings": [
                        {
                            "assessment_unit_id": unit["assessment_unit_id"],
                            "scope_class": unit["scope_class"],
                            "dimension_id": unit["dimension_id"],
                            "severity": "major",
                            "impact_scope": "key_conclusion",
                            "report_spans": [span],
                            "context_requirement_ids": [],
                            "observation": (
                                "The executive conclusion conflicts with the report "
                                f"body for review unit {index + 1}."
                            ),
                            "rationale": (
                                "The two statements cannot both be true within the "
                                "report."
                            ),
                            "severity_basis": (
                                "The conflict could mislead a management decision."
                            ),
                            "confidence_basis": "direct_cross_span_conflict",
                            "external_premise_disclosure": "none",
                            "recommended_human_action": "reconcile_status_language",
                            "suggested_rewrite": None,
                        }
                    ],
                }
        requirement_assessments = [
            {
                "assessment_unit_id": unit["assessment_unit_id"],
                "requirement_id": requirement["requirement_id"],
                "state": "fulfilled",
                "attention_status": "none",
                "report_spans": [],
                "rationale": "The frozen requirement is explicitly covered.",
            }
            for unit in units
            for requirement in requirements
            if requirement["type"] in unit["eligible_requirement_types"]
        ]
        output = canonical_json_bytes(
            {
                "schema_version": DIMENSION_RESPONSE_SCHEMA_ID,
                "trial_id": request.trial_id,
                "dimension_id": request.dimension_id,
                "unit_results": unit_results,
                "requirement_assessments": requirement_assessments,
            }
        )
        raw = synthetic_anthropic_message_bytes_v1(
            stop_reason="end_turn",
            response_id=f"msg-reader-review-{len(self._calls)}",
            model=request.expected_model_version,
            content=[{"type": "text", "text": output.decode("utf-8")}],
        )
        return self._delegate._attempt_from_response(
            request=request,
            raw=raw,
            sdk_response=None,
        )


def _reader_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str]:
    direction = CoreRunInitializeRequest.minimal_example["run_direction"]
    assert isinstance(direction, dict)
    monkeypatch.setitem(direction, "report_type", "management_monthly")
    monkeypatch.setitem(direction, "output_language", "en")
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    return workspace, run_id


def _unsupported_reader_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> tuple[Path, str]:
    direction = CoreRunInitializeRequest.minimal_example["run_direction"]
    assert isinstance(direction, dict)
    monkeypatch.setitem(direction, field, value)
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    return workspace, run_id


def _reader_service(
    workspace: Path,
    calls: list[tuple[str, int]],
    *,
    terminal_mode: str = "no_finding",
) -> PostFinalAssessmentService:
    return PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda execution: _ReaderMessagesAdapter(
            execution,
            calls,
            terminal_mode=terminal_mode,
        ),
    )


def test_reader_review_input_profile_and_result_vocabulary_are_exact() -> None:
    payload = _reader_input("reader-review-request-contract-1")
    validated = ReaderReviewAssessmentInput.model_validate(payload, strict=True)
    assert validated.model_dump(mode="json") == payload
    with pytest.raises(ValidationError):
        ReaderReviewAssessmentInput.model_validate({**payload, "extra": "forbidden"})
    with pytest.raises(ValidationError):
        ReaderReviewAssessmentInput.model_validate(
            {**payload, "disclosure_confirmed": False}, strict=True
        )
    with pytest.raises(ValidationError):
        ReaderReviewAssessmentInput.model_validate(
            {**payload, "cost_status": "estimated"}, strict=True
        )

    profile = load_profile("management_brief_en_v1")
    assert (profile.profile.report_type, profile.profile.language) == (
        "management_monthly",
        "en",
    )
    assert [item.scope_class for item in profile.profile.dimensions] == ["O1", "O2"]
    assert len(profile.profile.dimensions[0].sub_aspects) == 5
    assert {
        item.eligible_requirement_types[0]
        for item in profile.profile.dimensions[1].sub_aspects
    } == {
        "must_answer",
        "must_include",
        "must_not_claim",
        "audience_need",
        "decision_use",
        "scope_included",
        "scope_excluded",
    }
    assert (
        derive_reader_review_result_status(
            terminal_evidence_class="available",
            assessed_unit_count=12,
            finding_count=0,
            withheld_finding_count=0,
            abstention_count=0,
            requirement_states=("fulfilled",),
        )
        == "no_finding_returned_in_completed_supported_checks"
    )
    assert (
        derive_reader_review_result_status(
            terminal_evidence_class="available",
            assessed_unit_count=12,
            finding_count=0,
            withheld_finding_count=1,
            abstention_count=0,
            requirement_states=("fulfilled",),
        )
        == "partially_assessed"
    )
    assert (
        derive_reader_review_result_status(
            terminal_evidence_class="provider_failed",
            assessed_unit_count=0,
            finding_count=0,
            withheld_finding_count=0,
            abstention_count=0,
            requirement_states=(),
        )
        == "unable_to_assess"
    )


@pytest.mark.parametrize(
    ("direction_field", "unsupported_value"),
    (("report_type", "weekly"), ("output_language", "zh-CN")),
)
def test_reader_review_v3_entrypoints_stop_on_unsupported_frozen_direction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    direction_field: str,
    unsupported_value: str,
) -> None:
    """Direct v3 policy/assessment commands fail before any evaluator effect."""

    unsupported, _run_id = _unsupported_reader_workspace(
        tmp_path / "unsupported-policy",
        monkeypatch,
        direction_field,
        unsupported_value,
    )
    unsupported_service = PostFinalAssessmentService(unsupported)
    before_policy = (unsupported / "briefloop.db").read_bytes()
    with pytest.raises(PostFinalAssessmentError, match="reader_review_not_supported"):
        unsupported_service.policy_set(
            _reader_policy_payload(
                unsupported_service,
                "reader-review-v3-unsupported-policy",
            )
        )
    assert (unsupported / "briefloop.db").read_bytes() == before_policy

    workspace, _run_id = _reader_workspace(tmp_path / "unsupported-run", monkeypatch)
    service = PostFinalAssessmentService(workspace)
    policy = service.policy_set(
        _reader_policy_payload(service, "reader-review-v3-supported-policy")
    )
    assert policy["ok"] is True
    loaded = service._load()
    facts, snapshot, binding, workspace_id, history, action = loaded
    series = service._series_for_facts(history, snapshot, facts, action)
    policy_record = next(
        item
        for item in snapshot.post_final_assessment_policy_revisions
        if item.policy_revision_id == policy["policy_revision_id"]
    )
    command = assessment_module._build_next_assessment_command(
        facts=facts,
        action=action,
        policy=policy_record,
        series=series,
        results={
            item.assessment_request_id: item
            for item in snapshot.post_final_assessment_results
        },
        abandonments={
            item.assessment_request_id: item
            for item in snapshot.post_final_assessment_abandonments
        },
        human_actor_id="human-reader-review-1",
        human_request_id="reader-review-v3-unsupported-run",
        assessment_purpose="post_final_review",
        abandon_predecessor=False,
        reader_review_authorization_fingerprint="a" * 64,
    ).model_dump(mode="json")
    unsupported_binding = binding.model_copy(
        update={
            "run_direction": binding.run_direction.model_copy(
                update={direction_field: unsupported_value}
            )
        }
    )
    monkeypatch.setattr(
        service,
        "_load",
        lambda: (
            facts,
            snapshot,
            unsupported_binding,
            workspace_id,
            history,
            action,
        ),
    )
    monkeypatch.setattr(
        assessment_module,
        "prepare_shadow_run_from_bytes",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported Reader Review prepared evaluator")
        ),
    )
    monkeypatch.setattr(
        assessment_module,
        "execute_prepared_shadow_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported Reader Review executed provider")
        ),
    )
    before_run = (workspace / "briefloop.db").read_bytes()
    next_result = service.assessment_next(
        policy_revision_id=str(policy["policy_revision_id"]),
        human_actor_id="human-reader-review-1",
        human_request_id="reader-review-v3-unsupported-next",
        assessment_purpose="post_final_review",
    )
    assert next_result == {
        "ok": False,
        "status": "unsupported",
        "reason_code": "reader_review_not_supported",
    }
    run_result = service.assessment_run(command)
    assert run_result == {
        "ok": False,
        "status": "unsupported",
        "reason_code": "reader_review_not_supported",
    }
    assert (workspace / "briefloop.db").read_bytes() == before_run


def test_reader_review_assessment_next_rebuilds_authorization_after_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outcome-unknown v3 predecessor can be explicitly abandoned read-only."""

    workspace, _run_id = _reader_workspace(tmp_path / "unknown", monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _reader_service(workspace, calls)
    policy = service.policy_set(
        _reader_policy_payload(service, "reader-review-v3-unknown-policy")
    )
    assert policy["ok"] is True

    first = service.assessment_next(
        policy_revision_id=str(policy["policy_revision_id"]),
        human_actor_id="human-reader-review-next",
        human_request_id="reader-review-v3-unknown-first",
        assessment_purpose="post_final_review",
    )
    assert first["ok"] is True
    first_request = first["request"]
    assert isinstance(first_request, dict)
    authorization = first_request["reader_review_authorization_fingerprint"]
    assert isinstance(authorization, str) and len(authorization) == 64

    facts, snapshot, _binding, _workspace_id, history, action = service._load()
    policy_record = next(
        item
        for item in snapshot.post_final_assessment_policy_revisions
        if item.policy_revision_id == policy["policy_revision_id"]
    )
    reconstructed = service._reader_review_input_from_policy(
        policy_record,
        human_actor_id="human-reader-review-next",
        human_request_id="reader-review-v3-unknown-first",
    )
    assert authorization == service._reader_review_authorization_fingerprint(
        reconstructed,
        facts=facts,
        action=action,
    )

    class _ProcessStop(BaseException):
        pass

    original_execute = assessment_module.execute_prepared_shadow_run
    monkeypatch.setattr(
        assessment_module,
        "execute_prepared_shadow_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_ProcessStop()),
    )
    with pytest.raises(_ProcessStop):
        service.assessment_run(first_request)
    monkeypatch.setattr(
        assessment_module,
        "execute_prepared_shadow_run",
        original_execute,
    )

    before_preview = (workspace / "briefloop.db").read_bytes()
    second = service.assessment_next(
        policy_revision_id=str(policy["policy_revision_id"]),
        human_actor_id="human-reader-review-next",
        human_request_id="reader-review-v3-unknown-abandon",
        assessment_purpose="post_final_review",
        abandon_predecessor=True,
    )
    assert second["ok"] is True
    second_request = second["request"]
    assert isinstance(second_request, dict)
    assert second_request["abandon_predecessor"] is True
    assert isinstance(second_request["reader_review_authorization_fingerprint"], str)
    assert calls == []
    assert (workspace / "briefloop.db").read_bytes() == before_preview

    assert (
        service.assessment_next(
            policy_revision_id=str(policy["policy_revision_id"]),
            human_actor_id="human-reader-review-next",
            human_request_id="reader-review-v3-unknown-abandon",
            assessment_purpose="post_final_review",
            abandon_predecessor=True,
        )
        == second
    )


def test_reader_review_status_follows_latest_assessment_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status must report the verified series head, not an abandoned predecessor."""

    workspace, _run_id = _reader_workspace(tmp_path / "status-series", monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _reader_service(workspace, calls)
    policy = service.policy_set(
        _reader_policy_payload(service, "reader-review-status-series-policy")
    )
    assert policy["ok"] is True
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")

    class _ProcessStop(BaseException):
        pass

    def run_unknown(request: dict[str, object]) -> None:
        original_execute = assessment_module.execute_prepared_shadow_run
        original_retry = service.retry
        original_archive_probe = service._archive_recovery_available
        monkeypatch.setattr(
            assessment_module,
            "execute_prepared_shadow_run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(_ProcessStop()),
        )
        monkeypatch.setattr(
            service,
            "retry",
            lambda assessment_request_id: {
                "ok": False,
                "status": "pending",
                "assessment_request_id": assessment_request_id,
            },
        )
        monkeypatch.setattr(
            service,
            "_archive_recovery_available",
            lambda *_args, **_kwargs: False,
        )
        try:
            with pytest.raises(_ProcessStop):
                service.assessment_run(request)
        finally:
            monkeypatch.setattr(
                assessment_module,
                "execute_prepared_shadow_run",
                original_execute,
            )
            monkeypatch.setattr(service, "retry", original_retry)
            monkeypatch.setattr(
                service,
                "_archive_recovery_available",
                original_archive_probe,
            )

    first = service.assessment_next(
        policy_revision_id=str(policy["policy_revision_id"]),
        human_actor_id="human-reader-review-status-series",
        human_request_id="reader-review-status-series-first",
        assessment_purpose="post_final_review",
    )
    assert first["ok"] is True
    first_request = first["request"]
    assert isinstance(first_request, dict)
    run_unknown(first_request)

    second = service.assessment_next(
        policy_revision_id=str(policy["policy_revision_id"]),
        human_actor_id="human-reader-review-status-series",
        human_request_id="reader-review-status-series-second",
        assessment_purpose="post_final_review",
        abandon_predecessor=True,
    )
    assert second["ok"] is True
    second_request = second["request"]
    assert isinstance(second_request, dict)
    run_unknown(second_request)

    third = service.assessment_next(
        policy_revision_id=str(policy["policy_revision_id"]),
        human_actor_id="human-reader-review-status-series",
        human_request_id="reader-review-status-series-third",
        assessment_purpose="post_final_review",
        abandon_predecessor=True,
    )
    assert third["ok"] is True
    third_request = third["request"]
    assert isinstance(third_request, dict)
    third_result = service.assessment_run(third_request)
    assert third_result["ok"] is True
    assert third_result["status"] == "available"

    listing = service.assessment_list()
    assert [item["terminal_evidence_class"] for item in listing["assessments"]] == [
        "abandoned",
        "abandoned",
        "available",
    ]
    status = service.status()
    assert status["status"] == "available"
    assert (
        status["assessment_request_id"]
        == listing["assessments"][-1]["assessment_request_id"]
    )
    assert (
        status["assessment_result_id"]
        == listing["assessments"][-1]["assessment_result_id"]
    )


def test_reader_review_assessment_run_preflights_sdk_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing live SDK identity cannot create a claimed request."""

    workspace, run_id = _reader_workspace(tmp_path / "sdk-preflight", monkeypatch)
    factory_calls: list[object] = []

    def forbidden_factory(execution: object) -> object:
        factory_calls.append(execution)
        raise AssertionError("SDK preflight reached provider factory")

    service = PostFinalAssessmentService(
        workspace,
        adapter_factory=forbidden_factory,
    )
    policy = service.policy_set(
        _reader_policy_payload(service, "reader-review-sdk-preflight-policy")
    )
    assert policy["ok"] is True
    preview = service.assessment_next(
        policy_revision_id=str(policy["policy_revision_id"]),
        human_actor_id="human-reader-review-preflight",
        human_request_id="reader-review-sdk-preflight-run",
        assessment_purpose="post_final_review",
    )
    assert preview["ok"] is True
    request = preview["request"]
    assert isinstance(request, dict)

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision
        before_snapshot = store.load_snapshot(run_id)
        assert before_snapshot.post_final_assessment_requests == ()

    metadata_calls: list[str] = []

    def unavailable_metadata(name: str) -> str:
        metadata_calls.append(name)
        raise RuntimeError("optional SDK is not installed")

    monkeypatch.setattr(runner_module.metadata, "version", unavailable_metadata)
    before_db = (workspace / "briefloop.db").read_bytes()
    result = service.assessment_run(request)

    assert result == {
        "ok": False,
        "status": "unavailable",
        "reason_code": "preflight_invalid",
    }
    assert metadata_calls == ["anthropic"]
    assert factory_calls == []
    assert (workspace / "briefloop.db").read_bytes() == before_db
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
        assert store.current_revision == before_revision
        assert snapshot.post_final_assessment_requests == ()
        assert snapshot.post_final_assessment_results == ()


def test_o2_four_state_contract_and_unable_finding_conflict_are_deterministic(
    tmp_path: Path,
) -> None:
    report = b"# Monthly brief\n\nThe requested operating status is covered.\n"
    context = freeze_bounded_context(
        context_id="reader-review-validator-context",
        data_class="synthetic",
        language="en",
        requirements=[
            BoundedRequirement(
                requirement_id="reader-review-must-answer",
                type="must_answer",
                text="State the current operating status.",
                source_locator="run_direction.objective",
            )
        ],
    )
    request = ReaderReviewAssessmentInput.model_validate(
        _reader_input("reader-review-validator-request"), strict=True
    )
    prepared = prepare_shadow_run_from_bytes(
        report_bytes=report,
        bounded_context=context,
        instrument_config=PostFinalAssessmentService._reader_review_instrument(request),
        trial_id="reader-review-validator-trial",
        archive_root=tmp_path / "archive",
        workspace_root=tmp_path / "workspace",
        messages_endpoint=_ENDPOINT,
        profile_id="management_brief_en_v1",
    )
    assert isinstance(prepared, PreparedShadowRun)
    admission = prepared.admission
    o2_units = [
        item
        for item in admission.assessment_plan.units
        if item.dimension_id == "brief_requirement_coverage"
    ]
    target = next(
        item for item in o2_units if item.eligible_requirement_types == ["must_answer"]
    )
    block = admission.reader.artifact.blocks[0]
    span = make_span_locator(
        admission.reader.artifact,
        block_id=block.block_id,
        start_char=0,
        end_char=len(block.text),
    )
    finding = FindingDraft(
        assessment_unit_id=target.assessment_unit_id,
        scope_class="O2",
        dimension_id=target.dimension_id,
        severity="major",
        impact_scope="decision",
        report_spans=[span],
        context_requirement_ids=["reader-review-must-answer"],
        observation="The required operating status is not reliably resolved.",
        rationale="The report does not provide a decision-usable status.",
        severity_basis="Management could act on an unresolved status.",
        confidence_basis="explicit_requirement_mismatch",
        external_premise_disclosure="none",
        recommended_human_action="address_requirement",
        suggested_rewrite=None,
    )
    unit_results = [
        (
            FindingEmittedResult(
                assessment_unit_id=item.assessment_unit_id,
                disposition="finding_emitted",
                findings=[finding],
            )
            if item == target
            else NoFindingResult(
                assessment_unit_id=item.assessment_unit_id,
                disposition="no_finding",
            )
        )
        for item in o2_units
    ]
    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=admission.assessment_plan.trial_id,
        dimension_id="brief_requirement_coverage",
        unit_results=unit_results,
        requirement_assessments=[
            O2RequirementAssessment(
                assessment_unit_id=target.assessment_unit_id,
                requirement_id="reader-review-must-answer",
                state="unable_to_assess",
                attention_status="unable_to_assess",
                report_spans=[],
                rationale="The available report text cannot settle fulfillment.",
            )
        ],
    )
    loaded_profile = admission._instrument_snapshot.resources.loaded_profile
    result = validate_dimension_response(
        response,
        raw_object=response.model_dump(mode="json", warnings="error"),
        expected_dimension_id="brief_requirement_coverage",
        plan=admission.assessment_plan,
        reader_artifact=admission.reader.artifact,
        bounded_context=context,
        attempt_ref="reader-review-validator-attempt",
        _loaded_profile=loaded_profile,
    )
    assert "unable_requirement_finding_conflict" in result.reason_codes

    with pytest.raises(ValidationError):
        O2RequirementAssessment(
            assessment_unit_id=target.assessment_unit_id,
            requirement_id="reader-review-must-answer",
            state="unfulfilled_transparent",
            attention_status="none",
            report_spans=[span],
            rationale="The gap is disclosed.",
        )
    transparent = O2RequirementAssessment(
        assessment_unit_id=target.assessment_unit_id,
        requirement_id="reader-review-must-answer",
        state="unfulfilled_transparent",
        attention_status="attention_needed",
        report_spans=[span],
        rationale="The gap is disclosed.",
    )
    assert transparent.attention_status == "attention_needed"


def test_reader_review_claim_recovery_replay_and_selection_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, run_id = _reader_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    supported = build_post_final_assessment_projection(workspace)
    assert (
        supported.lifecycle_present,
        supported.status,
        supported.reason_code,
        supported.user_status,
        supported.run_action_available,
    ) == (
        False,
        "not_requested",
        "laj_not_run",
        "not_assessed",
        True,
    )
    assert supported.request_template is not None
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    service = _reader_service(workspace, calls)
    first_request = _reader_input("reader-review-human-request-1")

    original_qualify = service._qualify_archive
    monkeypatch.setattr(
        service,
        "_qualify_archive",
        lambda _facts, request, _archive_path: {
            "ok": False,
            "status": "pending",
            "assessment_request_id": request.assessment_request_id,
        },
    )
    initial = service.run_reader_review(first_request)
    assert initial["status"] == "pending"
    assert initial["user_status"] == "unable_to_assess"
    assert len(calls) == 2
    before_projection = (workspace / "briefloop.db").read_bytes()
    pending = build_post_final_assessment_projection(workspace)
    assert (pending.status, pending.user_status, pending.reason_code) == (
        "pending",
        "unable_to_assess",
        "post_final_assessment_outcome_unknown",
    )
    assert pending.run_action_available is False
    assert pending.view.findings == []
    assert pending.view.requirement_assessments == []
    assert (workspace / "briefloop.db").read_bytes() == before_projection

    monkeypatch.setattr(service, "_qualify_archive", original_qualify)
    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    monkeypatch.setattr(
        runner_module.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("Reader Review replay touched SDK metadata")
        ),
    )
    recovery = PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("Reader Review replay touched adapter")
        ),
    ).run_reader_review(first_request)
    assert recovery["ok"] is True
    assert recovery["user_status"] == (
        "no_finding_returned_in_completed_supported_checks"
    )
    assert len(calls) == 2

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
    first_result = snapshot.post_final_assessment_results[0]
    persisted_view = first_result.reader_view_payload
    assert persisted_view is not None
    assert persisted_view["requirement_assessments"]
    assert all(
        item["state"] == "fulfilled"
        for item in persisted_view["requirement_assessments"]
    )
    persisted_text = json.dumps(persisted_view, sort_keys=True)
    assert "public-synthetic-key" not in persisted_text
    assert _ENDPOINT not in persisted_text

    database_before_direct_replay = (workspace / "briefloop.db").read_bytes()
    direct_replay = PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("stored result replay touched adapter")
        ),
    ).run_reader_review(first_request)
    assert direct_replay["assessment_result_id"] == first_result.assessment_result_id
    assert direct_replay["replayed"] is True
    assert (workspace / "briefloop.db").read_bytes() == database_before_direct_replay

    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    second = _reader_service(workspace, calls).run_reader_review(
        _reader_input("reader-review-human-request-2")
    )
    assert second["ok"] is True
    assert len(calls) == 4

    projection_bytes = (workspace / "briefloop.db").read_bytes()
    selection = build_post_final_assessment_projection(workspace)
    assert selection.user_status == "selection_required"
    assert selection.selection_required is True
    assert selection.run_action_available is False
    assert len(selection.compatible_result_options) == 2
    assert (workspace / "briefloop.db").read_bytes() == projection_bytes

    selected = build_post_final_assessment_projection(
        workspace,
        assessment_result_id=first_result.assessment_result_id,
        assessment_result_fingerprint=first_result.result_fingerprint,
    )
    assert selected.selected_result_id == first_result.assessment_result_id
    assert selected.user_status == ("no_finding_returned_in_completed_supported_checks")
    assert "does not mean the report is correct" in selected.view.disclaimer
    expected_labels = [
        (
            item["requirement_id"],
            item["type"],
            item["text"],
            item["source_locator"],
        )
        for item in snapshot.post_final_assessment_policy_revisions[0].bounded_context[
            "requirements"
        ]
    ]
    assert [
        (
            item.requirement_id,
            item.requirement_type,
            item.text,
            item.source_locator,
        )
        for item in selected.requirement_labels
    ] == expected_labels
    incompatible = build_post_final_assessment_projection(
        workspace,
        assessment_result_id="historical-reader-review-result",
        assessment_result_fingerprint="0" * 64,
    )
    assert incompatible.reason_code == "reader_review_selection_incompatible"
    assert incompatible.user_status == "not_assessed"
    assert (workspace / "briefloop.db").read_bytes() == projection_bytes


def test_explicit_selection_binds_current_head_before_historical_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_result = SimpleNamespace(
        assessment_result_id="historical-reader-review-result",
        result_fingerprint="1" * 64,
        run_id="run-historical",
    )
    historical_snapshot = SimpleNamespace(
        workspace_run_head=SimpleNamespace(current_run_id="run-current"),
        post_final_assessment_results=(old_result,),
    )
    current_snapshot = SimpleNamespace(
        store_revision=42,
        post_final_assessment_results=(),
    )

    class _History:
        snapshots = (historical_snapshot,)
        store_revision = 42

        @staticmethod
        def snapshot_at_revision(run_id: str, revision: int):
            assert (run_id, revision) == ("run-current", 42)
            return current_snapshot

    observed: list[tuple[str, bool]] = []

    def current_facts(_root, _history, *, run_id: str, require_current_head: bool):
        observed.append((run_id, require_current_head))
        action_fingerprint = "2" * 64
        return SimpleNamespace(
            facts=SimpleNamespace(
                workspace_id="workspace-current",
                run_id="run-current",
                store_revision=42,
                terminal_state="finalized_local",
                terminal_action_fingerprint=action_fingerprint,
                finalization_id="finalization-current",
                finalization_receipt_id="receipt-current",
                finalize_gate_batch_id="gate-current",
                gate_bindings=(),
                report=SimpleNamespace(
                    artifact_id="artifact-current",
                    artifact_revision=1,
                    sha256="3" * 64,
                    size_bytes=10,
                ),
            )
        )

    monkeypatch.setattr(
        projection_module,
        "build_finalized_local_review_projection_from_history",
        current_facts,
    )
    monkeypatch.setattr(
        projection_module.CoreRunDomainVerifier,
        "verify_loaded_history",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        projection_module,
        "classify_core_run_next_action",
        lambda _verified: SimpleNamespace(
            run_id="run-current",
            store_revision=42,
            action_kind="complete",
            effect_kind="finalized_local",
            reason_code="local_finalization_complete",
            stage_id=None,
            role_id=None,
            source_route_id=None,
            source_provider_id=None,
            request_schema_id=None,
            action_fingerprint="2" * 64,
        ),
    )
    before = list(tmp_path.iterdir())
    projected = build_post_final_assessment_projection(
        tmp_path,
        assessment_result_id=old_result.assessment_result_id,
        assessment_result_fingerprint=old_result.result_fingerprint,
        loaded_history=_History(),  # type: ignore[arg-type]
    )
    assert observed == [("run-current", True)]
    assert projected.reason_code == "reader_review_selection_incompatible"
    assert list(tmp_path.iterdir()) == before


def test_reader_review_result_replay_and_projection_require_exact_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero-advice Reader Review replay remains archive-bound and provider-free."""

    workspace, run_id = _reader_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    service = _reader_service(workspace, calls)
    command = _reader_input("reader-review-archive-bound-request")
    first = service.run_reader_review(command)
    assert first["ok"] is True, first
    assert first["user_status"] == ("no_finding_returned_in_completed_supported_checks")
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
    request = snapshot.post_final_assessment_requests[0]
    result = snapshot.post_final_assessment_results[0]
    archive_path = trial_archive_path(service._archive_root, request.trial_id)
    assert archive_path.is_dir()
    archive_backup = tmp_path / "reader-review-archive-backup"
    shutil.copytree(archive_path, archive_backup)
    database_before = (workspace / "briefloop.db").read_bytes()
    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    monkeypatch.setattr(
        runner_module.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("Reader Review replay touched SDK metadata")
        ),
    )
    replay = PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("Reader Review replay touched adapter")
        ),
    )

    def assert_archive_rejected() -> None:
        replayed = replay.run_reader_review(command)
        assert replayed == {
            "ok": False,
            "status": "invalid",
            "reason_code": "archive_verification_failed",
        }
        retried = replay.retry(request.assessment_request_id)
        assert retried == {
            "ok": False,
            "status": "invalid",
            "reason_code": "archive_verification_failed",
        }
        projected = build_post_final_assessment_projection(workspace)
        assert projected.status == "invalid"
        assert projected.reason_code == "post_final_assessment_archive_invalid"
        assert projected.selected_result_id is None
        assert projected.review_status is not None
        assert projected.review_status["ok"] is True
        assert projected.review_status["run_id"] == run_id
        assert len(projected.review_status["finalized_lineage_fingerprint"]) == 64
        assert projected.review_status["assessment_result_id"] is None
        assert projected.review_status["human_observations"] == []
        assert projected.review_status["guidance_drafts"] == []
        assert projected.review_status["provider_calls"] == 0
        assert projected.view.findings == []
        assert projected.view.requirement_assessments == []
        assert (workspace / "briefloop.db").read_bytes() == database_before

    shutil.rmtree(archive_path)
    assert not archive_path.exists()
    assert_archive_rejected()

    shutil.copytree(archive_backup, archive_path)
    archive_member = archive_path / "presentation_actual.json"
    archive_member.write_bytes(archive_member.read_bytes() + b" ")
    assert_archive_rejected()
    assert len(calls) == 2
    assert result.finding_count == result.withheld_finding_count == 0
