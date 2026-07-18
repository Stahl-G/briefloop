"""Lossless provider boundary for private Semantic Evaluator shadow runs.

This module owns the only provider-outcome classifier.  Adapters capture facts;
the runner and archive consume the classifier result and never reinterpret a
provider status or transport error themselves.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Protocol, cast, get_args

from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_sha256,
    sha256_bytes,
)


ADAPTER_PROTOCOL_VERSION = "semantic_evaluator_adapter_v4"
PROVIDER_BOUNDARY_FACTS_SCHEMA_ID = (
    "briefloop.semantic_evaluator.provider_boundary_facts.v4"
)

BoundaryFactState = Literal["absent", "present_valid", "present_invalid"]
ExternalTextInvalidCode = Literal[
    "external_text_wrong_type",
    "external_text_empty",
    "external_text_utf8_unencodable",
    "external_text_unknown",
    "external_text_duplicate",
    "external_text_invalid_container",
    "external_text_projection_mismatch",
    "external_text_read_failed",
]
EnvelopeInvalidCode = Literal[
    "envelope_wrong_type",
    "envelope_utf8_invalid",
    "envelope_json_invalid",
    "envelope_not_object",
    "envelope_duplicate_member",
    "envelope_projection_failed",
]
HttpStatusInvalidCode = Literal[
    "http_status_wrong_type",
    "http_status_out_of_range",
    "http_status_read_failed",
]
ProviderTransportKind = Literal[
    "response",
    "timeout",
    "connection",
    "http_error",
    "adapter_error",
]
ProviderAttemptStatus = Literal["completed", "failed"]
ProviderShadowReason = Literal[
    "provider_retryable_failure",
    "provider_failed",
    "provider_incomplete",
    "provider_identity_mismatch",
    "provider_boundary_invalid",
]
KernelAttemptFailureReason = Literal[
    "provider_retryable_failure",
    "provider_failed",
]

_BOUNDARY_STATES = frozenset(get_args(BoundaryFactState))
_EXTERNAL_TEXT_INVALID_CODES = frozenset(get_args(ExternalTextInvalidCode))
_ENVELOPE_INVALID_CODES = frozenset(get_args(EnvelopeInvalidCode))
_HTTP_INVALID_CODES = frozenset(get_args(HttpStatusInvalidCode))
_TRANSPORT_KINDS = frozenset(get_args(ProviderTransportKind))
_SHADOW_REASONS = frozenset(get_args(ProviderShadowReason))
_KERNEL_REASONS = frozenset(get_args(KernelAttemptFailureReason))
_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 409, 429})
_KNOWN_RESPONSE_STATUSES = frozenset(
    {"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}
)


@dataclass(frozen=True)
class ExternalTextObservation:
    """One corroborating observation without coercing its external value."""

    present: bool
    value: object = None

    def __post_init__(self) -> None:
        if type(self.present) is not bool:
            raise TypeError("shadow_adapter_unavailable")


@dataclass(frozen=True)
class ExternalTextFactV4:
    state: BoundaryFactState
    utf8_hex: str | None
    utf8_sha256: str | None
    invalid_code: ExternalTextInvalidCode | None

    def __post_init__(self) -> None:
        valid = (
            self.state == "present_valid"
            and type(self.utf8_hex) is str
            and bool(self.utf8_hex)
            and len(self.utf8_hex) % 2 == 0
            and type(self.utf8_sha256) is str
            and len(self.utf8_sha256) == 64
            and self.invalid_code is None
        )
        absent = (
            self.state == "absent"
            and self.utf8_hex is None
            and self.utf8_sha256 is None
            and self.invalid_code is None
        )
        invalid = (
            self.state == "present_invalid"
            and self.utf8_hex is None
            and self.utf8_sha256 is None
            and type(self.invalid_code) is str
            and self.invalid_code in _EXTERNAL_TEXT_INVALID_CODES
        )
        if self.state not in _BOUNDARY_STATES or not (valid or absent or invalid):
            raise TypeError("shadow_adapter_unavailable")
        if valid:
            try:
                raw = bytes.fromhex(cast(str, self.utf8_hex))
                raw.decode("utf-8", errors="strict")
            except (ValueError, UnicodeDecodeError):
                raise TypeError("shadow_adapter_unavailable") from None
            if sha256_bytes(raw) != self.utf8_sha256:
                raise TypeError("shadow_adapter_unavailable")

    @property
    def utf8_bytes(self) -> bytes | None:
        if self.state != "present_valid":
            return None
        return bytes.fromhex(cast(str, self.utf8_hex))


@dataclass(frozen=True)
class ResponseEnvelopeFactV4:
    state: BoundaryFactState
    raw_size_bytes: int | None
    raw_sha256: str | None
    invalid_code: EnvelopeInvalidCode | None

    def __post_init__(self) -> None:
        present_base = (
            type(self.raw_size_bytes) is int
            and cast(int, self.raw_size_bytes) >= 0
            and type(self.raw_sha256) is str
            and len(cast(str, self.raw_sha256)) == 64
        )
        valid = (
            self.state == "present_valid" and present_base and self.invalid_code is None
        )
        invalid = (
            self.state == "present_invalid"
            and present_base
            and type(self.invalid_code) is str
            and self.invalid_code in _ENVELOPE_INVALID_CODES
        )
        absent = (
            self.state == "absent"
            and self.raw_size_bytes is None
            and self.raw_sha256 is None
            and self.invalid_code is None
        )
        if self.state not in _BOUNDARY_STATES or not (valid or invalid or absent):
            raise TypeError("shadow_adapter_unavailable")


@dataclass(frozen=True)
class HttpStatusFactV4:
    state: BoundaryFactState
    value: int | None
    invalid_code: HttpStatusInvalidCode | None

    def __post_init__(self) -> None:
        valid = (
            self.state == "present_valid"
            and type(self.value) is int
            and 100 <= cast(int, self.value) <= 599
            and self.invalid_code is None
        )
        absent = (
            self.state == "absent" and self.value is None and self.invalid_code is None
        )
        invalid = (
            self.state == "present_invalid"
            and self.value is None
            and type(self.invalid_code) is str
            and self.invalid_code in _HTTP_INVALID_CODES
        )
        if self.state not in _BOUNDARY_STATES or not (valid or absent or invalid):
            raise TypeError("shadow_adapter_unavailable")


@dataclass(frozen=True)
class ProviderBoundaryFactsV4:
    schema_version: Literal["briefloop.semantic_evaluator.provider_boundary_facts.v4"]
    envelope: ResponseEnvelopeFactV4
    status: ExternalTextFactV4
    response_id: ExternalTextFactV4
    provider_identity: ExternalTextFactV4
    model_identity: ExternalTextFactV4
    output: ExternalTextFactV4
    http_status: HttpStatusFactV4
    transport_kind: ProviderTransportKind
    boundary_facts_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != PROVIDER_BOUNDARY_FACTS_SCHEMA_ID:
            raise TypeError("shadow_adapter_unavailable")
        if (
            type(self.transport_kind) is not str
            or self.transport_kind not in _TRANSPORT_KINDS
        ):
            raise TypeError("shadow_adapter_unavailable")
        if (
            type(self.boundary_facts_sha256) is not str
            or len(self.boundary_facts_sha256) != 64
        ):
            raise TypeError("shadow_adapter_unavailable")
        payload = asdict(self)
        payload.pop("boundary_facts_sha256")
        if canonical_sha256(payload) != self.boundary_facts_sha256:
            raise TypeError("shadow_adapter_unavailable")


@dataclass(frozen=True)
class ProviderOutcomeV4:
    attempt_status: ProviderAttemptStatus
    shadow_reason: ProviderShadowReason | None
    kernel_reason: KernelAttemptFailureReason | None
    retry_eligible: bool
    output_eligible: bool

    def __post_init__(self) -> None:
        completed = (
            self.attempt_status == "completed"
            and self.shadow_reason is None
            and self.kernel_reason is None
            and self.retry_eligible is False
            and self.output_eligible is True
        )
        failed = (
            self.attempt_status == "failed"
            and type(self.shadow_reason) is str
            and self.shadow_reason in _SHADOW_REASONS
            and type(self.kernel_reason) is str
            and self.kernel_reason in _KERNEL_REASONS
            and type(self.retry_eligible) is bool
            and self.retry_eligible
            == (self.shadow_reason == "provider_retryable_failure")
            and self.output_eligible is False
        )
        if not (completed or failed):
            raise TypeError("shadow_adapter_unavailable")


def absent_external_text_fact_v4() -> ExternalTextFactV4:
    return ExternalTextFactV4("absent", None, None, None)


def invalid_external_text_fact_v4(code: ExternalTextInvalidCode) -> ExternalTextFactV4:
    return ExternalTextFactV4("present_invalid", None, None, code)


def capture_external_text_v4(
    observations: tuple[ExternalTextObservation, ...],
    *,
    allow_empty: Literal[False] = False,
    allowed_values: frozenset[str] | None = None,
) -> ExternalTextFactV4:
    """Totally capture corroborating external text without value-bearing errors."""

    try:
        if type(observations) is not tuple or not observations:
            return invalid_external_text_fact_v4("external_text_invalid_container")
        if allow_empty is not False:
            return invalid_external_text_fact_v4("external_text_invalid_container")
        if allowed_values is not None and type(allowed_values) is not frozenset:
            return invalid_external_text_fact_v4("external_text_invalid_container")
        if any(type(item) is not ExternalTextObservation for item in observations):
            return invalid_external_text_fact_v4("external_text_invalid_container")
        present = tuple(item.present for item in observations)
        if not any(present):
            return absent_external_text_fact_v4()
        if not all(present):
            return invalid_external_text_fact_v4("external_text_projection_mismatch")
        encoded: list[bytes] = []
        for observation in observations:
            value = observation.value
            if type(value) is not str:
                return invalid_external_text_fact_v4("external_text_wrong_type")
            if not value:
                return invalid_external_text_fact_v4("external_text_empty")
            try:
                raw = value.encode("utf-8", errors="strict")
            except (UnicodeEncodeError, UnicodeError):
                return invalid_external_text_fact_v4("external_text_utf8_unencodable")
            if allowed_values is not None and value not in allowed_values:
                return invalid_external_text_fact_v4("external_text_unknown")
            encoded.append(raw)
        if any(raw != encoded[0] for raw in encoded[1:]):
            return invalid_external_text_fact_v4("external_text_projection_mismatch")
        raw = encoded[0]
        return ExternalTextFactV4("present_valid", raw.hex(), sha256_bytes(raw), None)
    except Exception:
        return invalid_external_text_fact_v4("external_text_read_failed")


def capture_response_envelope_v4(
    raw: object,
    *,
    present: bool,
    invalid_code: EnvelopeInvalidCode | None = None,
) -> ResponseEnvelopeFactV4:
    """Capture exact envelope bytes; explicit null is invalid, never absent."""

    if type(present) is not bool:
        raise TypeError("shadow_adapter_unavailable")
    if not present:
        return ResponseEnvelopeFactV4("absent", None, None, None)
    if type(raw) is not bytes:
        return ResponseEnvelopeFactV4(
            "present_invalid", 0, sha256_bytes(b""), "envelope_wrong_type"
        )
    code = invalid_code
    return ResponseEnvelopeFactV4(
        "present_invalid" if code is not None else "present_valid",
        len(raw),
        sha256_bytes(raw),
        code,
    )


def capture_http_status_v4(value: object, *, present: bool) -> HttpStatusFactV4:
    if type(present) is not bool:
        return HttpStatusFactV4("present_invalid", None, "http_status_read_failed")
    if not present:
        return HttpStatusFactV4("absent", None, None)
    if type(value) is not int:
        return HttpStatusFactV4("present_invalid", None, "http_status_wrong_type")
    if not 100 <= value <= 599:
        return HttpStatusFactV4("present_invalid", None, "http_status_out_of_range")
    return HttpStatusFactV4("present_valid", value, None)


def make_provider_boundary_facts_v4(
    *,
    envelope: ResponseEnvelopeFactV4,
    status: ExternalTextFactV4,
    response_id: ExternalTextFactV4,
    provider_identity: ExternalTextFactV4,
    model_identity: ExternalTextFactV4,
    output: ExternalTextFactV4,
    http_status: HttpStatusFactV4,
    transport_kind: ProviderTransportKind,
) -> ProviderBoundaryFactsV4:
    payload = {
        "schema_version": PROVIDER_BOUNDARY_FACTS_SCHEMA_ID,
        "envelope": asdict(envelope),
        "status": asdict(status),
        "response_id": asdict(response_id),
        "provider_identity": asdict(provider_identity),
        "model_identity": asdict(model_identity),
        "output": asdict(output),
        "http_status": asdict(http_status),
        "transport_kind": transport_kind,
    }
    return ProviderBoundaryFactsV4(
        schema_version=PROVIDER_BOUNDARY_FACTS_SCHEMA_ID,
        envelope=envelope,
        status=status,
        response_id=response_id,
        provider_identity=provider_identity,
        model_identity=model_identity,
        output=output,
        http_status=http_status,
        transport_kind=transport_kind,
        boundary_facts_sha256=canonical_sha256(payload),
    )


def _failed_outcome(reason: ProviderShadowReason) -> ProviderOutcomeV4:
    kernel: KernelAttemptFailureReason = (
        "provider_retryable_failure"
        if reason == "provider_retryable_failure"
        else "provider_failed"
    )
    return ProviderOutcomeV4(
        "failed",
        reason,
        kernel,
        reason == "provider_retryable_failure",
        False,
    )


def classify_provider_outcome_v4(
    facts: object,
    *,
    expected_model_version_utf8: bytes,
) -> ProviderOutcomeV4:
    """Total classifier and sole status/retry/output/kernel authority."""

    if type(facts) is not ProviderBoundaryFactsV4:
        return _failed_outcome("provider_boundary_invalid")
    if (
        type(expected_model_version_utf8) is not bytes
        or not expected_model_version_utf8
    ):
        return _failed_outcome("provider_boundary_invalid")
    try:
        expected_model_version_utf8.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _failed_outcome("provider_boundary_invalid")

    typed_facts = cast(ProviderBoundaryFactsV4, facts)
    text_facts = (
        typed_facts.status,
        typed_facts.response_id,
        typed_facts.provider_identity,
        typed_facts.model_identity,
        typed_facts.output,
    )
    if (
        typed_facts.envelope.state == "present_invalid"
        or typed_facts.http_status.state == "present_invalid"
        or any(fact.state == "present_invalid" for fact in text_facts)
    ):
        return _failed_outcome("provider_boundary_invalid")

    if typed_facts.envelope.state == "present_valid":
        required = (
            typed_facts.status,
            typed_facts.response_id,
            typed_facts.provider_identity,
            typed_facts.model_identity,
        )
        if any(fact.state != "present_valid" for fact in required):
            return _failed_outcome("provider_boundary_invalid")
        if typed_facts.http_status.state != "absent":
            return _failed_outcome("provider_boundary_invalid")
        status_bytes = typed_facts.status.utf8_bytes
        if status_bytes == b"incomplete":
            return _failed_outcome("provider_incomplete")
        if status_bytes in {b"failed", b"cancelled", b"queued", b"in_progress"}:
            return _failed_outcome("provider_failed")
        if status_bytes != b"completed":
            return _failed_outcome("provider_boundary_invalid")
        if typed_facts.transport_kind != "response":
            return _failed_outcome("provider_failed")
        if typed_facts.output.state != "present_valid":
            return _failed_outcome("provider_boundary_invalid")
        if typed_facts.model_identity.utf8_bytes != expected_model_version_utf8:
            return _failed_outcome("provider_identity_mismatch")
        return ProviderOutcomeV4("completed", None, None, False, True)

    absent_response_facts = (
        typed_facts.status,
        typed_facts.response_id,
        typed_facts.model_identity,
        typed_facts.output,
    )
    if any(fact.state != "absent" for fact in absent_response_facts):
        return _failed_outcome("provider_boundary_invalid")
    if typed_facts.provider_identity.state not in {"absent", "present_valid"}:
        return _failed_outcome("provider_boundary_invalid")
    if (
        typed_facts.transport_kind != "http_error"
        and typed_facts.http_status.state != "absent"
    ):
        return _failed_outcome("provider_boundary_invalid")
    if typed_facts.transport_kind in {"timeout", "connection"}:
        return _failed_outcome("provider_retryable_failure")
    if typed_facts.transport_kind == "http_error":
        if typed_facts.http_status.state != "present_valid":
            return _failed_outcome("provider_failed")
        status = cast(int, typed_facts.http_status.value)
        if status in _RETRYABLE_HTTP_STATUS_CODES or 500 <= status <= 599:
            return _failed_outcome("provider_retryable_failure")
    return _failed_outcome("provider_failed")


@dataclass(frozen=True)
class FrozenProviderRequestV4:
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
        try:
            for value in text_values:
                value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise TypeError("shadow_request_invalid") from None
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
        from multi_agent_brief.semantic_evaluator.serialization import (
            canonical_json_bytes,
        )

        return canonical_json_bytes(asdict(self))


@dataclass(frozen=True)
class RawProviderAttemptV4:
    facts: ProviderBoundaryFactsV4
    outcome: ProviderOutcomeV4
    request_projection_bytes: bytes
    raw_transport_response: bytes | None
    extracted_output: bytes | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    sdk_projection_bytes: bytes | None = None

    def __post_init__(self) -> None:
        if type(self.request_projection_bytes) is not bytes:
            raise TypeError("shadow_adapter_unavailable")
        if (
            self.sdk_projection_bytes is not None
            and type(self.sdk_projection_bytes) is not bytes
        ):
            raise TypeError("shadow_adapter_unavailable")
        if self.outcome.output_eligible != (type(self.extracted_output) is bytes):
            raise TypeError("shadow_adapter_unavailable")
        if (
            self.facts.envelope.state == "absent"
            and self.raw_transport_response is not None
        ):
            raise TypeError("shadow_adapter_unavailable")
        if (
            self.facts.envelope.state != "absent"
            and type(self.raw_transport_response) is not bytes
        ):
            raise TypeError("shadow_adapter_unavailable")
        if type(self.raw_transport_response) is bytes and (
            len(self.raw_transport_response) != self.facts.envelope.raw_size_bytes
            or sha256_bytes(self.raw_transport_response)
            != self.facts.envelope.raw_sha256
        ):
            raise TypeError("shadow_adapter_unavailable")
        for value in (self.input_tokens, self.output_tokens, self.total_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise TypeError("shadow_adapter_unavailable")
        if self.total_tokens is not None and (
            self.input_tokens is None
            or self.output_tokens is None
            or self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise TypeError("shadow_adapter_unavailable")


class SemanticEvaluatorAdapterV4(Protocol):
    adapter_id: str
    adapter_version: str
    provider_sdk_name: str
    provider_sdk_version: str
    qualification_eligible: bool

    def invoke(self, request: FrozenProviderRequestV4) -> RawProviderAttemptV4: ...


def _validate_taxonomy_totality() -> None:
    if _BOUNDARY_STATES != frozenset({"absent", "present_valid", "present_invalid"}):
        raise RuntimeError("shadow_adapter_unavailable")
    if _TRANSPORT_KINDS != frozenset(
        {"response", "timeout", "connection", "http_error", "adapter_error"}
    ):
        raise RuntimeError("shadow_adapter_unavailable")
    if _SHADOW_REASONS != frozenset(
        {
            "provider_retryable_failure",
            "provider_failed",
            "provider_incomplete",
            "provider_identity_mismatch",
            "provider_boundary_invalid",
        }
    ):
        raise RuntimeError("shadow_adapter_unavailable")
    if _KERNEL_REASONS != frozenset({"provider_retryable_failure", "provider_failed"}):
        raise RuntimeError("shadow_adapter_unavailable")
    for reason in cast(
        tuple[ProviderShadowReason, ...], get_args(ProviderShadowReason)
    ):
        outcome = _failed_outcome(reason)
        if outcome.retry_eligible != (reason == "provider_retryable_failure"):
            raise RuntimeError("shadow_adapter_unavailable")
        if outcome.output_eligible:
            raise RuntimeError("shadow_adapter_unavailable")
    if _KNOWN_RESPONSE_STATUSES != frozenset(
        {"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}
    ):
        raise RuntimeError("shadow_adapter_unavailable")


_validate_taxonomy_totality()

# Internal lifecycle names intentionally point at the single v4 authority.
FrozenProviderRequest = FrozenProviderRequestV4
RawProviderAttempt = RawProviderAttemptV4
SemanticEvaluatorAdapter = SemanticEvaluatorAdapterV4


__all__ = [
    "ADAPTER_PROTOCOL_VERSION",
    "PROVIDER_BOUNDARY_FACTS_SCHEMA_ID",
    "BoundaryFactState",
    "EnvelopeInvalidCode",
    "ExternalTextFactV4",
    "ExternalTextInvalidCode",
    "ExternalTextObservation",
    "FrozenProviderRequestV4",
    "FrozenProviderRequest",
    "HttpStatusFactV4",
    "HttpStatusInvalidCode",
    "KernelAttemptFailureReason",
    "ProviderBoundaryFactsV4",
    "ProviderOutcomeV4",
    "ProviderShadowReason",
    "ProviderTransportKind",
    "RawProviderAttemptV4",
    "RawProviderAttempt",
    "ResponseEnvelopeFactV4",
    "SemanticEvaluatorAdapterV4",
    "SemanticEvaluatorAdapter",
    "absent_external_text_fact_v4",
    "capture_external_text_v4",
    "capture_http_status_v4",
    "capture_response_envelope_v4",
    "classify_provider_outcome_v4",
    "invalid_external_text_fact_v4",
    "make_provider_boundary_facts_v4",
]
