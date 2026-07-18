"""Packaged hermetic adapter for public-safe Semantic Evaluator E2E evidence."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import json

from multi_agent_brief.semantic_evaluator.adapter import (
    ExternalTextFactV4,
    ExternalTextObservation,
    FrozenProviderRequestV4,
    RawProviderAttemptV4,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
)
from multi_agent_brief.semantic_evaluator.contracts import DIMENSION_RESPONSE_SCHEMA_ID
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    sha256_bytes,
)


SYNTHETIC_ADAPTER_ID = "synthetic_fixture_v4"
SYNTHETIC_PROVIDER_ID = "synthetic_fixture"
SYNTHETIC_ADAPTER_VERSION = "synthetic_fixture_adapter_v4"
_FIXTURE_PACKAGE = "multi_agent_brief.semantic_evaluator"
_FIXTURE_NAME = "manifest.json"
_STATUS_VALUES = frozenset(
    {"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}
)


class _DuplicateMember(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateMember
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise ValueError


@dataclass(frozen=True)
class SyntheticRawProjectionV4:
    envelope_valid: bool
    envelope_invalid_code: str | None
    status: ExternalTextFactV4
    response_id: ExternalTextFactV4
    provider_identity: ExternalTextFactV4
    model_identity: ExternalTextFactV4
    output: ExternalTextFactV4


def _absent():
    return capture_external_text_v4((ExternalTextObservation(False),))


def _invalid(code: str):
    from multi_agent_brief.semantic_evaluator.adapter import (
        invalid_external_text_fact_v4,
    )

    return invalid_external_text_fact_v4(code)  # type: ignore[arg-type]


def project_synthetic_response_bytes_v4(raw: bytes) -> SyntheticRawProjectionV4:
    absent = _absent()
    if type(raw) is not bytes:
        return SyntheticRawProjectionV4(
            False, "envelope_wrong_type", absent, absent, absent, absent, absent
        )
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError:
        return SyntheticRawProjectionV4(
            False, "envelope_utf8_invalid", absent, absent, absent, absent, absent
        )
    except _DuplicateMember:
        return SyntheticRawProjectionV4(
            False, "envelope_duplicate_member", absent, absent, absent, absent, absent
        )
    except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
        return SyntheticRawProjectionV4(
            False, "envelope_json_invalid", absent, absent, absent, absent, absent
        )
    if type(value) is not dict:
        return SyntheticRawProjectionV4(
            False, "envelope_not_object", absent, absent, absent, absent, absent
        )
    observe = lambda name: ExternalTextObservation(name in value, value.get(name))
    return SyntheticRawProjectionV4(
        True,
        None,
        capture_external_text_v4((observe("status"),), allowed_values=_STATUS_VALUES),
        capture_external_text_v4((observe("id"),)),
        capture_external_text_v4((observe("provider"),)),
        capture_external_text_v4((observe("model"),)),
        capture_external_text_v4((observe("output_text"),)),
    )


def _load_fixture_manifest() -> str:
    try:
        raw = (
            resources.files(_FIXTURE_PACKAGE)
            .joinpath("fixtures", "synthetic_shadow_v1", _FIXTURE_NAME)
            .read_bytes()
        )
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except Exception:
        raise SemanticEvaluatorError("shadow_adapter_unavailable") from None
    if payload != {
        "adapter_version": SYNTHETIC_ADAPTER_VERSION,
        "response_mode": "all_no_finding",
        "schema_version": "briefloop.semantic_evaluator.synthetic_fixture.v4",
    }:
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    return f"{SYNTHETIC_ADAPTER_VERSION}:{sha256_bytes(raw)[:12]}"


def _rubric_from_prompt(user_text: str) -> dict[str, object]:
    start_marker = "<CURRENT_RUBRIC>\n"
    end_marker = "\n</CURRENT_RUBRIC>"
    if (
        type(user_text) is not str
        or user_text.count(start_marker) != 1
        or user_text.count(end_marker) != 1
    ):
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    start = user_text.index(start_marker) + len(start_marker)
    end = user_text.index(end_marker, start)
    try:
        payload = json.loads(user_text[start:end])
    except (json.JSONDecodeError, TypeError, ValueError):
        raise SemanticEvaluatorError("shadow_adapter_unavailable") from None
    if type(payload) is not dict:
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    return payload


class SyntheticFixtureAdapterV4:
    adapter_id = SYNTHETIC_ADAPTER_ID
    provider_sdk_name = "synthetic"
    provider_sdk_version = "synthetic-v4"
    qualification_eligible = False

    def __init__(self) -> None:
        self.adapter_version = _load_fixture_manifest()

    def invoke(self, request: FrozenProviderRequestV4) -> RawProviderAttemptV4:
        if (
            request.adapter_id != self.adapter_id
            or request.provider_id != SYNTHETIC_PROVIDER_ID
        ):
            raise SemanticEvaluatorError("shadow_adapter_unavailable")
        rubric = _rubric_from_prompt(request.user_text)
        dimension = rubric.get("dimension")
        units = rubric.get("assessment_units")
        if type(dimension) is not dict or type(units) is not list:
            raise SemanticEvaluatorError("shadow_adapter_unavailable")
        if (
            dimension.get("dimension_id") != request.dimension_id
            or rubric.get("trial_id") != request.trial_id
        ):
            raise SemanticEvaluatorError("shadow_adapter_unavailable")
        unit_results: list[dict[str, str]] = []
        for item in units:
            if (
                type(item) is not dict
                or type(item.get("assessment_unit_id")) is not str
            ):
                raise SemanticEvaluatorError("shadow_adapter_unavailable")
            unit_results.append(
                {
                    "assessment_unit_id": item["assessment_unit_id"],
                    "disposition": "no_finding",
                }
            )
        output = canonical_json_bytes(
            {
                "dimension_id": request.dimension_id,
                "schema_version": DIMENSION_RESPONSE_SCHEMA_ID,
                "trial_id": request.trial_id,
                "unit_results": unit_results,
            }
        )
        projection = request.projection_bytes()
        raw = canonical_json_bytes(
            {
                "id": f"synthetic-{sha256_bytes(projection)[:16]}",
                "model": request.expected_model_version,
                "output_text": output.decode("utf-8"),
                "provider": SYNTHETIC_PROVIDER_ID,
                "status": "completed",
            }
        )
        projected = project_synthetic_response_bytes_v4(raw)
        provider = capture_external_text_v4(
            (
                ExternalTextObservation(True, request.provider_id),
                ExternalTextObservation(True, SYNTHETIC_PROVIDER_ID),
                ExternalTextObservation(
                    True,
                    (projected.provider_identity.utf8_bytes or b"").decode("utf-8"),
                ),
            )
        )
        facts = make_provider_boundary_facts_v4(
            envelope=capture_response_envelope_v4(raw, present=True),
            status=projected.status,
            response_id=projected.response_id,
            provider_identity=provider,
            model_identity=projected.model_identity,
            output=projected.output,
            http_status=capture_http_status_v4(None, present=False),
            transport_kind="response",
        )
        outcome = classify_provider_outcome_v4(
            facts,
            expected_model_version_utf8=request.expected_model_version.encode("utf-8"),
        )
        return RawProviderAttemptV4(
            facts=facts,
            outcome=outcome,
            request_projection_bytes=projection,
            raw_transport_response=raw,
            extracted_output=output if outcome.output_eligible else None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )


__all__ = [
    "SYNTHETIC_ADAPTER_ID",
    "SYNTHETIC_ADAPTER_VERSION",
    "SYNTHETIC_PROVIDER_ID",
    "SyntheticFixtureAdapterV4",
    "SyntheticRawProjectionV4",
    "project_synthetic_response_bytes_v4",
]
