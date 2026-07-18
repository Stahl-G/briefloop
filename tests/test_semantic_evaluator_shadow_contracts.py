from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from multi_agent_brief.semantic_evaluator.adapter import (
    ExternalTextObservation,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256
from multi_agent_brief.semantic_evaluator.shadow_contracts import (
    PROVIDER_ATTEMPT_SCHEMA_ID,
    ProviderAttemptRecordV4,
    ProviderBoundaryFactsRecordV4,
)


EXPECTED_MODEL = "gpt-test-2026-07-18"


def _text(value: str):
    return capture_external_text_v4((ExternalTextObservation(True, value),))


def _runtime_facts():
    return make_provider_boundary_facts_v4(
        envelope=capture_response_envelope_v4(b'{"status":"completed"}', present=True),
        status=_text("completed"),
        response_id=_text("resp_public"),
        provider_identity=_text("openai_responses"),
        model_identity=_text(EXPECTED_MODEL),
        output=_text('{"findings":[]}'),
        http_status=capture_http_status_v4(None, present=False),
        transport_kind="response",
    )


def _attempt_payload() -> dict[str, object]:
    facts = _runtime_facts()
    outcome = classify_provider_outcome_v4(
        facts, expected_model_version_utf8=EXPECTED_MODEL.encode()
    )
    payload: dict[str, object] = {
        "schema_version": PROVIDER_ATTEMPT_SCHEMA_ID,
        "attempt_ref": "attempt:dimension-1:1",
        "trial_id": "trial-public",
        "dimension_id": "dimension-1",
        "attempt_ordinal": 1,
        "prompt_request_sha256": "1" * 64,
        "adapter_id": "synthetic_fixture_v4",
        "provider_id": "synthetic",
        "requested_model_id": "synthetic-v4",
        "expected_model_version_utf8_hex": EXPECTED_MODEL.encode().hex(),
        "facts": ProviderBoundaryFactsRecordV4.from_runtime(facts).model_dump(
            mode="json"
        ),
        "attempt_status": outcome.attempt_status,
        "shadow_reason": outcome.shadow_reason,
        "kernel_reason": outcome.kernel_reason,
        "retry_eligible": outcome.retry_eligible,
        "output_eligible": outcome.output_eligible,
        "request_projection_sha256": "2" * 64,
        "raw_transport_response_sha256": facts.envelope.raw_sha256,
        "extracted_output_sha256": "3" * 64,
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "started_at": "2026-07-18T00:00:00Z",
        "completed_at": "2026-07-18T00:00:01Z",
    }
    payload["attempt_record_sha256"] = canonical_sha256(payload)
    return payload


def test_se2r_01_v4_attempt_accepts_exact_classifier_projection() -> None:
    record = ProviderAttemptRecordV4.model_validate(_attempt_payload())
    assert record.attempt_status == "completed"
    assert record.output_eligible is True
    assert record.retry_eligible is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_status", "failed"),
        ("shadow_reason", "provider_retryable_failure"),
        ("kernel_reason", "provider_failed"),
        ("retry_eligible", True),
        ("output_eligible", False),
    ],
)
def test_se2r_10_attempt_cannot_disagree_with_classifier(
    field: str, value: object
) -> None:
    payload = _attempt_payload()
    payload[field] = value
    payload["attempt_record_sha256"] = canonical_sha256(
        {key: item for key, item in payload.items() if key != "attempt_record_sha256"}
    )
    with pytest.raises(ValidationError):
        ProviderAttemptRecordV4.model_validate(payload)


def test_se2r_10_attempt_rejects_boundary_fact_hash_rewrite() -> None:
    payload = _attempt_payload()
    facts = deepcopy(payload["facts"])
    assert isinstance(facts, dict)
    facts["boundary_facts_sha256"] = "0" * 64
    payload["facts"] = facts
    payload["attempt_record_sha256"] = canonical_sha256(
        {key: item for key, item in payload.items() if key != "attempt_record_sha256"}
    )
    with pytest.raises(ValidationError):
        ProviderAttemptRecordV4.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("attempt_ordinal", True), ("retry_eligible", 1), ("input_tokens", False)],
)
def test_v4_attempt_contract_rejects_coercion(field: str, value: object) -> None:
    payload = _attempt_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        ProviderAttemptRecordV4.model_validate(payload)


def test_v4_attempt_contract_rejects_extra_fields() -> None:
    payload = _attempt_payload()
    payload["authority"] = "accepted"
    with pytest.raises(ValidationError):
        ProviderAttemptRecordV4.model_validate(payload)
