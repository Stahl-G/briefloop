from __future__ import annotations

from dataclasses import replace

import pytest

from multi_agent_brief.semantic_evaluator.adapter import (
    PROVIDER_BOUNDARY_FACTS_SCHEMA_ID,
    ExternalTextObservation,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
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
