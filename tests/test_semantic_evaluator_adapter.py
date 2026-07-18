from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from multi_agent_brief.semantic_evaluator.adapter import (
    FrozenProviderRequestV4,
    PROVIDER_BOUNDARY_FACTS_SCHEMA_ID,
    ExternalTextObservation,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
)
from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
    OPENAI_ADAPTER_ID,
    OPENAI_PROVIDER_ID,
    OpenAIResponsesAdapterV4,
    project_openai_response_bytes_v4,
    synthetic_openai_response_bytes_v4,
)


EXPECTED_MODEL = b"gpt-test-2026-07-18"
KNOWN_STATUSES = frozenset(
    {"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}
)


def _present(value: object, *, allowed: frozenset[str] | None = None):
    return capture_external_text_v4(
        (ExternalTextObservation(True, value),), allowed_values=allowed
    )


def _absent():
    return capture_external_text_v4((ExternalTextObservation(False),))


def _facts(
    *,
    status: object = "completed",
    status_present: bool = True,
    envelope_present: bool = True,
    output: object = '{"findings":[]}',
    output_present: bool = True,
    transport_kind: str = "response",
    http_status: object = None,
    http_present: bool = False,
    model: object = "gpt-test-2026-07-18",
):
    status_fact = (
        _present(status, allowed=KNOWN_STATUSES) if status_present else _absent()
    )
    return make_provider_boundary_facts_v4(
        envelope=capture_response_envelope_v4(
            b'{"status":"completed"}' if envelope_present else None,
            present=envelope_present,
        ),
        status=status_fact,
        response_id=_present("resp_public") if envelope_present else _absent(),
        provider_identity=_present("openai_responses")
        if envelope_present
        else _absent(),
        model_identity=_present(model) if envelope_present else _absent(),
        output=_present(output) if output_present else _absent(),
        http_status=capture_http_status_v4(http_status, present=http_present),
        transport_kind=transport_kind,  # type: ignore[arg-type]
    )


def _openai_request() -> FrozenProviderRequestV4:
    return FrozenProviderRequestV4(
        trial_id="trial-public",
        dimension_id="dimension-1",
        attempt_ordinal=1,
        system_text="system",
        user_text="user",
        prompt_request_sha256="1" * 64,
        adapter_id=OPENAI_ADAPTER_ID,
        provider_id=OPENAI_PROVIDER_ID,
        model_id="gpt-test",
        expected_model_version=EXPECTED_MODEL.decode(),
        temperature=0.0,
        top_p=1.0,
        max_output_tokens=100,
        seed=None,
        timeout_seconds=60,
    )


def test_se2r_01_completed_exact_response_is_output_eligible() -> None:
    facts = _facts()
    outcome = classify_provider_outcome_v4(
        facts, expected_model_version_utf8=EXPECTED_MODEL
    )
    assert facts.schema_version == PROVIDER_BOUNDARY_FACTS_SCHEMA_ID
    assert outcome.attempt_status == "completed"
    assert outcome.shadow_reason is None
    assert outcome.retry_eligible is False
    assert outcome.output_eligible is True


@pytest.mark.parametrize("output_present", [False, True])
def test_se2r_02_incomplete_is_terminal_without_output_or_retry(
    output_present: bool,
) -> None:
    outcome = classify_provider_outcome_v4(
        _facts(status="incomplete", output_present=output_present),
        expected_model_version_utf8=EXPECTED_MODEL,
    )
    assert outcome.attempt_status == "failed"
    assert outcome.shadow_reason == "provider_incomplete"
    assert outcome.kernel_reason == "provider_failed"
    assert outcome.retry_eligible is False
    assert outcome.output_eligible is False


def test_se2r_03_present_envelope_with_absent_status_is_terminal() -> None:
    outcome = classify_provider_outcome_v4(
        _facts(
            status_present=False,
            transport_kind="http_error",
            http_status=503,
            http_present=True,
        ),
        expected_model_version_utf8=EXPECTED_MODEL,
    )
    assert outcome.shadow_reason == "provider_boundary_invalid"
    assert outcome.retry_eligible is False


@pytest.mark.parametrize("status", ["future_status", 3, None, True])
def test_se2r_04_invalid_status_cannot_be_laundered_by_http_5xx(
    status: object,
) -> None:
    outcome = classify_provider_outcome_v4(
        _facts(
            status=status,
            transport_kind="http_error",
            http_status=503,
            http_present=True,
        ),
        expected_model_version_utf8=EXPECTED_MODEL,
    )
    assert outcome.shadow_reason == "provider_boundary_invalid"
    assert outcome.retry_eligible is False


@pytest.mark.parametrize(
    ("kind", "http_status", "http_present"),
    [
        ("timeout", None, False),
        ("connection", None, False),
        ("http_error", 408, True),
        ("http_error", 409, True),
        ("http_error", 429, True),
        ("http_error", 500, True),
        ("http_error", 599, True),
    ],
)
def test_se2r_05_only_absent_envelope_retryable_transport_can_retry(
    kind: str, http_status: int | None, http_present: bool
) -> None:
    outcome = classify_provider_outcome_v4(
        _facts(
            envelope_present=False,
            status_present=False,
            output_present=False,
            transport_kind=kind,
            http_status=http_status,
            http_present=http_present,
        ),
        expected_model_version_utf8=EXPECTED_MODEL,
    )
    assert outcome.shadow_reason == "provider_retryable_failure"
    assert outcome.kernel_reason == "provider_retryable_failure"
    assert outcome.retry_eligible is True
    assert outcome.output_eligible is False


@pytest.mark.parametrize("value", [True, False, 99, 600, "503", 503.0, object()])
def test_se2r_06_invalid_http_status_is_typed_and_terminal(value: object) -> None:
    facts = _facts(
        envelope_present=False,
        status_present=False,
        output_present=False,
        transport_kind="http_error",
        http_status=value,
        http_present=True,
    )
    assert facts.http_status.state == "present_invalid"
    outcome = classify_provider_outcome_v4(
        facts, expected_model_version_utf8=EXPECTED_MODEL
    )
    assert outcome.shadow_reason == "provider_boundary_invalid"
    assert outcome.retry_eligible is False


@pytest.mark.parametrize("field", ["status", "model", "output"])
def test_se2r_07_lone_surrogate_is_value_free_terminal(field: str) -> None:
    kwargs = {field: "bad\ud800value"}
    facts = _facts(**kwargs)
    outcome = classify_provider_outcome_v4(
        facts, expected_model_version_utf8=EXPECTED_MODEL
    )
    assert outcome.shadow_reason == "provider_boundary_invalid"
    assert outcome.retry_eligible is False
    assert "bad" not in repr(outcome)


def test_se2r_07_lone_surrogate_in_expected_identity_is_terminal() -> None:
    outcome = classify_provider_outcome_v4(
        _facts(), expected_model_version_utf8=b"\xff"
    )
    assert outcome.shadow_reason == "provider_boundary_invalid"


def test_se2r_02_openai_raw_incomplete_is_terminal_even_with_valid_output() -> None:
    raw = synthetic_openai_response_bytes_v4(
        status="incomplete",
        response_id="resp-public",
        model=EXPECTED_MODEL.decode(),
        output_text='{"findings":[]}',
    )
    adapter = object.__new__(OpenAIResponsesAdapterV4)
    attempt = adapter._attempt_from_response(
        request=_openai_request(), raw=raw, sdk_response=None
    )
    assert attempt.outcome.shadow_reason == "provider_incomplete"
    assert attempt.outcome.retry_eligible is False
    assert attempt.extracted_output is None
    assert attempt.sdk_projection_bytes is not None


def test_se2r_02_openai_status_error_body_cannot_complete() -> None:
    raw = synthetic_openai_response_bytes_v4(
        status="completed",
        response_id="resp-public",
        model=EXPECTED_MODEL.decode(),
        output_text='{"findings":[]}',
    )
    attempt = object.__new__(OpenAIResponsesAdapterV4)._attempt_from_response(
        request=_openai_request(),
        raw=raw,
        sdk_response=None,
        transport_kind="http_error",
    )
    assert attempt.outcome.shadow_reason == "provider_failed"
    assert attempt.outcome.retry_eligible is False
    assert attempt.extracted_output is None


def test_se2r_04_openai_sdk_parse_failure_cannot_fall_back_to_raw_success() -> None:
    from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
        _SDK_READ_FAILED,
    )

    raw = synthetic_openai_response_bytes_v4(
        status="completed",
        response_id="resp-public",
        model=EXPECTED_MODEL.decode(),
        output_text='{"findings":[]}',
    )
    attempt = object.__new__(OpenAIResponsesAdapterV4)._attempt_from_response(
        request=_openai_request(), raw=raw, sdk_response=_SDK_READ_FAILED
    )
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.extracted_output is None


def test_se2r_04_completed_rejects_unknown_output_item_type() -> None:
    raw = (
        b'{"id":"resp-public","model":"gpt-test-2026-07-18","output":['
        b'{"content":[{"text":"ignored","type":"output_text"}],"type":"future_item"},'
        b'{"content":[{"text":"accepted","type":"output_text"}],"type":"message"}'
        b'],"status":"completed"}'
    )
    projection = project_openai_response_bytes_v4(raw)
    assert projection.output.state == "present_invalid"
    assert projection.output.invalid_code == "external_text_unknown"


@pytest.mark.parametrize(
    ("raw", "status_code", "expected_reason", "retry_eligible"),
    [
        (b"", 429, "provider_retryable_failure", True),
        (
            synthetic_openai_response_bytes_v4(
                status="completed",
                response_id="resp-public",
                model=EXPECTED_MODEL.decode(),
                output_text='{"findings":[]}',
            ),
            500,
            "provider_failed",
            False,
        ),
    ],
)
def test_se2r_03_status_error_body_presence_controls_retry_without_output(
    raw: bytes,
    status_code: int,
    expected_reason: str,
    retry_eligible: bool,
) -> None:
    class FakeStatusError(Exception):
        pass

    error = FakeStatusError()
    error.status_code = status_code  # type: ignore[attr-defined]
    error.response = SimpleNamespace(content=raw)  # type: ignore[attr-defined]

    class Create:
        def create(self, **_kwargs):
            raise error

    adapter = object.__new__(OpenAIResponsesAdapterV4)
    adapter._openai = SimpleNamespace(
        APITimeoutError=type("FakeTimeout", (Exception,), {}),
        APIConnectionError=type("FakeConnection", (Exception,), {}),
        APIStatusError=FakeStatusError,
    )
    adapter._client = SimpleNamespace(
        responses=SimpleNamespace(with_raw_response=Create())
    )
    attempt = adapter.invoke(_openai_request())
    assert attempt.outcome.shadow_reason == expected_reason
    assert attempt.outcome.retry_eligible is retry_eligible
    assert attempt.extracted_output is None


def test_se2r_04_openai_raw_duplicate_status_is_terminal() -> None:
    raw = (
        b'{"id":"resp-public","model":"gpt-test-2026-07-18",'
        b'"output":[],"status":"completed","status":"incomplete"}'
    )
    projection = project_openai_response_bytes_v4(raw)
    assert projection.envelope_valid is False
    assert projection.envelope_invalid_code == "envelope_duplicate_member"


def test_se2r_07_openai_output_surrogate_escape_is_terminal() -> None:
    raw = (
        b'{"id":"resp-public","model":"gpt-test-2026-07-18",'
        b'"output":[{"content":[{"text":"\\ud800","type":"output_text"}]}],'
        b'"status":"completed"}'
    )
    attempt = object.__new__(OpenAIResponsesAdapterV4)._attempt_from_response(
        request=_openai_request(), raw=raw, sdk_response=None
    )
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.extracted_output is None


def test_se2r_10_classifier_is_total_over_malformed_fact_objects() -> None:
    for value in (None, {}, object(), True, "facts"):
        outcome = classify_provider_outcome_v4(
            value, expected_model_version_utf8=EXPECTED_MODEL
        )
        assert outcome.shadow_reason == "provider_boundary_invalid"
        assert outcome.retry_eligible is False


def test_boundary_fact_self_hash_rejects_mutation() -> None:
    facts = _facts()
    with pytest.raises(TypeError, match="shadow_adapter_unavailable"):
        replace(facts, boundary_facts_sha256="0" * 64)


def test_external_text_observations_require_exact_corroboration() -> None:
    mismatch = capture_external_text_v4(
        (
            ExternalTextObservation(True, "one"),
            ExternalTextObservation(True, "two"),
        )
    )
    mixed = capture_external_text_v4(
        (
            ExternalTextObservation(True, "one"),
            ExternalTextObservation(False),
        )
    )
    assert mismatch.invalid_code == "external_text_projection_mismatch"
    assert mixed.invalid_code == "external_text_projection_mismatch"
