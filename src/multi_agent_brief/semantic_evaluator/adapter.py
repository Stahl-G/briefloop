"""Package-private provider boundary for Semantic Evaluator shadow execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes


ADAPTER_PROTOCOL_VERSION = "semantic_evaluator_adapter_v1"


@dataclass(frozen=True)
class FrozenProviderRequest:
    """Detached request projection containing no workflow or baseline state."""

    trial_id: str
    dimension_id: str
    attempt_ordinal: int
    system_text: str
    user_text: str
    prompt_request_sha256: str
    adapter_id: str
    provider_id: str
    model_id: str
    expected_model_version: str
    temperature: float
    top_p: float
    max_output_tokens: int
    seed: None
    timeout_seconds: int

    def __post_init__(self) -> None:
        text_values = (
            self.trial_id,
            self.dimension_id,
            self.system_text,
            self.user_text,
            self.prompt_request_sha256,
            self.adapter_id,
            self.provider_id,
            self.model_id,
            self.expected_model_version,
        )
        if any(type(value) is not str or not value for value in text_values):
            raise TypeError("shadow_request_invalid")
        if type(self.attempt_ordinal) is not int or self.attempt_ordinal < 1:
            raise TypeError("shadow_request_invalid")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 1:
            raise TypeError("shadow_request_invalid")
        if (
            type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 300
        ):
            raise TypeError("shadow_request_invalid")
        if type(self.temperature) is not float or type(self.top_p) is not float:
            raise TypeError("shadow_request_invalid")
        if self.seed is not None:
            raise TypeError("shadow_request_invalid")

    def projection_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "adapter_id": self.adapter_id,
                "attempt_ordinal": self.attempt_ordinal,
                "dimension_id": self.dimension_id,
                "expected_model_version": self.expected_model_version,
                "max_output_tokens": self.max_output_tokens,
                "model_id": self.model_id,
                "prompt_request_sha256": self.prompt_request_sha256,
                "provider_id": self.provider_id,
                "seed": None,
                "system_text": self.system_text,
                "temperature": self.temperature,
                "timeout_seconds": self.timeout_seconds,
                "top_p": self.top_p,
                "trial_id": self.trial_id,
                "user_text": self.user_text,
            }
        )


@dataclass(frozen=True)
class RawProviderAttempt:
    status: Literal["completed", "failed"]
    reason_code: Optional[
        Literal[
            "provider_retryable_failure",
            "provider_failed",
            "provider_identity_mismatch",
        ]
    ]
    provider_request_id: Optional[str]
    observed_model_version: Optional[str]
    request_projection_bytes: bytes
    raw_transport_response: Optional[bytes]
    extracted_output: Optional[bytes]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]

    def __post_init__(self) -> None:
        if type(self.request_projection_bytes) is not bytes:
            raise TypeError("shadow_adapter_unavailable")
        if self.status == "completed":
            if self.reason_code is not None or type(self.extracted_output) is not bytes:
                raise TypeError("shadow_adapter_unavailable")
        elif self.reason_code is None or self.extracted_output is not None:
            raise TypeError("shadow_adapter_unavailable")
        for value in (self.raw_transport_response, self.extracted_output):
            if value is not None and type(value) is not bytes:
                raise TypeError("shadow_adapter_unavailable")
        for value in (self.provider_request_id, self.observed_model_version):
            if value is not None and (type(value) is not str or not value):
                raise TypeError("shadow_adapter_unavailable")
        for value in (self.input_tokens, self.output_tokens, self.total_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise TypeError("shadow_adapter_unavailable")
        if self.total_tokens is not None:
            if self.input_tokens is None or self.output_tokens is None:
                raise TypeError("shadow_adapter_unavailable")
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise TypeError("shadow_adapter_unavailable")


class SemanticEvaluatorAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    provider_sdk_name: str
    provider_sdk_version: str
    qualification_eligible: bool

    def invoke(self, request: FrozenProviderRequest) -> RawProviderAttempt: ...


__all__ = [
    "ADAPTER_PROTOCOL_VERSION",
    "FrozenProviderRequest",
    "RawProviderAttempt",
    "SemanticEvaluatorAdapter",
]
