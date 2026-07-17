"""Optional OpenAI Responses API adapter for public/synthetic shadow trials."""

from __future__ import annotations

from importlib import metadata
import os
from typing import Any

from multi_agent_brief.semantic_evaluator.adapter import (
    FrozenProviderRequest,
    RawProviderAttempt,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError


OPENAI_ADAPTER_ID = "openai_responses_v1"
OPENAI_PROVIDER_ID = "openai_responses"
OPENAI_ADAPTER_VERSION = "openai_responses_adapter_v1"


def _response_bytes(value: Any) -> bytes | None:
    content = getattr(value, "content", None)
    if type(content) is bytes:
        return content
    text = getattr(value, "text", None)
    if type(text) is str:
        return text.encode("utf-8")
    return None


def _raw_error_body(error: BaseException) -> bytes | None:
    response = getattr(error, "response", None)
    return _response_bytes(response) if response is not None else None


def _transport_reason(error: BaseException) -> str:
    status = getattr(error, "status_code", None)
    if type(status) is int:
        if status in {408, 409, 429} or status >= 500:
            return "provider_retryable_failure"
        return "provider_failed"
    name = type(error).__name__
    if name in {"APITimeoutError", "APIConnectionError"}:
        return "provider_retryable_failure"
    return "provider_failed"


def _usage_int(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None) if usage is not None else None
    return value if type(value) is int and value >= 0 else None


class OpenAIResponsesAdapterV1:
    adapter_id = OPENAI_ADAPTER_ID
    adapter_version = OPENAI_ADAPTER_VERSION
    provider_sdk_name = "openai"
    qualification_eligible = True

    def __init__(self) -> None:
        key = os.environ.get("OPENAI_API_KEY")
        if type(key) is not str or not key:
            raise SemanticEvaluatorError("shadow_adapter_unavailable")
        try:
            from openai import OpenAI  # type: ignore[import-not-found]

            version = metadata.version("openai")
            client = OpenAI(api_key=key, max_retries=0)
        except Exception:
            raise SemanticEvaluatorError("shadow_adapter_unavailable") from None
        if type(version) is not str or not version:
            raise SemanticEvaluatorError("shadow_adapter_unavailable")
        self.provider_sdk_version = version
        self._client: Any = client

    def invoke(self, request: FrozenProviderRequest) -> RawProviderAttempt:
        if (
            request.adapter_id != self.adapter_id
            or request.provider_id != OPENAI_PROVIDER_ID
        ):
            raise SemanticEvaluatorError("shadow_adapter_unavailable")
        projection = request.projection_bytes()
        try:
            raw = self._client.responses.with_raw_response.create(
                model=request.model_id,
                instructions=request.system_text,
                input=request.user_text,
                temperature=request.temperature,
                top_p=request.top_p,
                max_output_tokens=request.max_output_tokens,
                store=False,
                timeout=request.timeout_seconds,
            )
            http_response = getattr(raw, "http_response", None)
            transport = _response_bytes(http_response)
            response = raw.parse()
            request_id = getattr(response, "id", None)
            observed_model = getattr(response, "model", None)
            output_text = getattr(response, "output_text", None)
            if type(request_id) is not str or not request_id:
                request_id = None
            if type(observed_model) is not str or not observed_model:
                observed_model = None
            if observed_model != request.expected_model_version:
                return RawProviderAttempt(
                    status="failed",
                    reason_code="provider_identity_mismatch",
                    provider_request_id=request_id,
                    observed_model_version=observed_model,
                    request_projection_bytes=projection,
                    raw_transport_response=transport,
                    extracted_output=None,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                )
            if type(output_text) is not str:
                return RawProviderAttempt(
                    status="failed",
                    reason_code="provider_failed",
                    provider_request_id=request_id,
                    observed_model_version=observed_model,
                    request_projection_bytes=projection,
                    raw_transport_response=transport,
                    extracted_output=None,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                )
            usage = getattr(response, "usage", None)
            input_tokens = _usage_int(usage, "input_tokens")
            output_tokens = _usage_int(usage, "output_tokens")
            total_tokens = _usage_int(usage, "total_tokens")
            if (
                input_tokens is None
                or output_tokens is None
                or total_tokens != input_tokens + output_tokens
            ):
                input_tokens = output_tokens = total_tokens = None
            return RawProviderAttempt(
                status="completed",
                reason_code=None,
                provider_request_id=request_id,
                observed_model_version=observed_model,
                request_projection_bytes=projection,
                raw_transport_response=transport,
                extracted_output=output_text.encode("utf-8"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )
        except Exception as exc:
            return RawProviderAttempt(
                status="failed",
                reason_code=_transport_reason(exc),
                provider_request_id=None,
                observed_model_version=None,
                request_projection_bytes=projection,
                raw_transport_response=_raw_error_body(exc),
                extracted_output=None,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
            )


__all__ = [
    "OPENAI_ADAPTER_ID",
    "OPENAI_ADAPTER_VERSION",
    "OPENAI_PROVIDER_ID",
    "OpenAIResponsesAdapterV1",
]
