"""Packaged, hermetic Semantic Evaluator adapter for synthetic E2E evidence."""

from __future__ import annotations

import json
from importlib import resources

from multi_agent_brief.semantic_evaluator.adapter import (
    FrozenProviderRequest,
    RawProviderAttempt,
)
from multi_agent_brief.semantic_evaluator.contracts import DIMENSION_RESPONSE_SCHEMA_ID
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    sha256_bytes,
)


SYNTHETIC_ADAPTER_ID = "synthetic_fixture_v1"
SYNTHETIC_PROVIDER_ID = "synthetic_fixture"
_FIXTURE_PACKAGE = "multi_agent_brief.semantic_evaluator"
_FIXTURE_NAME = "manifest.json"


def _load_fixture_manifest() -> tuple[str, bytes]:
    try:
        raw = (
            resources.files(_FIXTURE_PACKAGE)
            .joinpath("fixtures", "synthetic_shadow_v1", _FIXTURE_NAME)
            .read_bytes()
        )
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise SemanticEvaluatorError("shadow_adapter_unavailable") from None
    if payload != {
        "adapter_version": "synthetic_fixture_adapter_v1",
        "response_mode": "all_no_finding",
        "schema_version": "briefloop.semantic_evaluator.synthetic_fixture.v1",
    }:
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    return f"synthetic_fixture_adapter_v1:{sha256_bytes(raw)[:12]}", raw


def _rubric_from_prompt(user_text: str) -> dict[str, object]:
    start_marker = "<CURRENT_RUBRIC>\n"
    end_marker = "\n</CURRENT_RUBRIC>"
    if user_text.count(start_marker) != 1 or user_text.count(end_marker) != 1:
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


class SyntheticFixtureAdapterV1:
    adapter_id = SYNTHETIC_ADAPTER_ID
    provider_sdk_name = "synthetic"
    provider_sdk_version = "synthetic-v1"
    qualification_eligible = False

    def __init__(self) -> None:
        self.adapter_version, _raw = _load_fixture_manifest()

    def invoke(self, request: FrozenProviderRequest) -> RawProviderAttempt:
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
        dimension_id = dimension.get("dimension_id")
        if (
            dimension_id != request.dimension_id
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
        transport = canonical_json_bytes(
            {
                "id": f"synthetic-{sha256_bytes(projection)[:16]}",
                "model": request.expected_model_version,
                "output_text": output.decode("utf-8"),
                "provider": SYNTHETIC_PROVIDER_ID,
            }
        )
        return RawProviderAttempt(
            status="completed",
            reason_code=None,
            provider_request_id=f"synthetic-{sha256_bytes(projection)[:16]}",
            observed_model_version=request.expected_model_version,
            request_projection_bytes=projection,
            raw_transport_response=transport,
            extracted_output=output,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )


__all__ = [
    "SYNTHETIC_ADAPTER_ID",
    "SYNTHETIC_PROVIDER_ID",
    "SyntheticFixtureAdapterV1",
]
