"""Single OpenAI Responses adapter for private offline-shadow evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata
import json
from typing import Any

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
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes


OPENAI_ADAPTER_ID = "openai_responses_v4"
OPENAI_PROVIDER_ID = "openai_responses"
OPENAI_ADAPTER_VERSION = "openai_responses_adapter_v4"
_STATUS_VALUES = frozenset(
    {"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}
)
_SDK_READ_FAILED = object()
_MISSING = object()


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
class OpenAIRawProjectionV4:
    envelope_valid: bool
    envelope_invalid_code: str | None
    status: ExternalTextFactV4
    response_id: ExternalTextFactV4
    model_identity: ExternalTextFactV4
    output: ExternalTextFactV4


def _absent_text() -> ExternalTextFactV4:
    return capture_external_text_v4((ExternalTextObservation(False),))


def _invalid_text(code: str) -> ExternalTextFactV4:
    from multi_agent_brief.semantic_evaluator.adapter import (
        invalid_external_text_fact_v4,
    )

    return invalid_external_text_fact_v4(code)  # type: ignore[arg-type]


def _member(value: dict[str, object], name: str) -> ExternalTextObservation:
    return ExternalTextObservation(name in value, value.get(name))


def _project_output(
    value: dict[str, object], *, status: ExternalTextFactV4
) -> ExternalTextFactV4:
    raw_output = value.get("output")
    completed = status.utf8_bytes == b"completed"
    if type(raw_output) is not list:
        if completed:
            return _invalid_text("external_text_invalid_container")
        return _absent_text()
    chunks: list[ExternalTextObservation] = []
    try:
        for item in raw_output:
            if type(item) is not dict:
                if completed:
                    return _invalid_text("external_text_invalid_container")
                continue
            item_type = capture_external_text_v4(
                (_member(item, "type"),),
                allowed_values=frozenset({"message", "reasoning"}),
            )
            if item_type.state != "present_valid":
                if completed:
                    return item_type
                continue
            if item_type.utf8_bytes == b"reasoning":
                continue
            content = item.get("content")
            if type(content) is not list:
                if completed:
                    return _invalid_text("external_text_invalid_container")
                continue
            for part in content:
                if type(part) is not dict:
                    if completed:
                        return _invalid_text("external_text_invalid_container")
                    continue
                part_type = capture_external_text_v4(
                    (_member(part, "type"),),
                    allowed_values=frozenset({"output_text"}),
                )
                if part_type.state != "present_valid":
                    if completed:
                        return part_type
                    continue
                chunks.append(ExternalTextObservation("text" in part, part.get("text")))
        if not chunks:
            return _invalid_text("external_text_empty") if completed else _absent_text()
        captured = [capture_external_text_v4((chunk,)) for chunk in chunks]
        if any(fact.state != "present_valid" for fact in captured):
            return next(fact for fact in captured if fact.state != "present_valid")
        text = b"".join(fact.utf8_bytes or b"" for fact in captured).decode("utf-8")
        return capture_external_text_v4((ExternalTextObservation(True, text),))
    except Exception:
        return _invalid_text("external_text_read_failed")


def project_openai_response_bytes_v4(raw: bytes) -> OpenAIRawProjectionV4:
    """Pure strict projector used by both live capture and archive replay."""

    absent = _absent_text()
    if type(raw) is not bytes:
        return OpenAIRawProjectionV4(
            False, "envelope_wrong_type", absent, absent, absent, absent
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return OpenAIRawProjectionV4(
            False, "envelope_utf8_invalid", absent, absent, absent, absent
        )
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateMember:
        return OpenAIRawProjectionV4(
            False, "envelope_duplicate_member", absent, absent, absent, absent
        )
    except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
        return OpenAIRawProjectionV4(
            False, "envelope_json_invalid", absent, absent, absent, absent
        )
    if type(value) is not dict:
        return OpenAIRawProjectionV4(
            False, "envelope_not_object", absent, absent, absent, absent
        )
    status = capture_external_text_v4(
        (_member(value, "status"),), allowed_values=_STATUS_VALUES
    )
    return OpenAIRawProjectionV4(
        True,
        None,
        status,
        capture_external_text_v4((_member(value, "id"),)),
        capture_external_text_v4((_member(value, "model"),)),
        _project_output(value, status=status),
    )


def _safe_attr(value: object, name: str) -> ExternalTextObservation:
    if value is _SDK_READ_FAILED:
        return ExternalTextObservation(True, object())
    try:
        return ExternalTextObservation(hasattr(value, name), getattr(value, name, None))
    except Exception:
        return ExternalTextObservation(True, object())


def _raw_bytes(value: object) -> bytes | None:
    try:
        response = getattr(value, "http_response", None)
        if response is None:
            response = getattr(value, "response", None)
        content = getattr(response, "content", None)
    except Exception:
        return None
    return content if type(content) is bytes else None


@dataclass(frozen=True)
class _StatusErrorBody:
    state: str
    raw: bytes | None


def _status_error_body(value: object) -> _StatusErrorBody:
    try:
        response = getattr(value, "http_response", _MISSING)
        if response is _MISSING or response is None:
            response = getattr(value, "response", _MISSING)
    except Exception:
        return _StatusErrorBody("invalid", None)
    if response is _MISSING or response is None:
        return _StatusErrorBody("absent", None)
    try:
        content = getattr(response, "content", _MISSING)
    except Exception:
        return _StatusErrorBody("invalid", None)
    if type(content) is not bytes:
        return _StatusErrorBody("invalid", None)
    return _StatusErrorBody("present", content)


def _usage(value: object, name: str) -> int | None:
    try:
        usage = getattr(value, "usage", None)
        item = getattr(usage, name, None)
    except Exception:
        return None
    return item if type(item) is int and item >= 0 else None


def _sdk_projection_bytes(
    value: object | None,
    *,
    transport_kind: str,
    http_status: object = None,
    http_present: bool = False,
    body_state: str,
) -> bytes:
    fields = {
        "status": capture_external_text_v4(
            (_safe_attr(value, "status"),)
            if value is not None
            else (ExternalTextObservation(False),),
            allowed_values=_STATUS_VALUES,
        ),
        "response_id": capture_external_text_v4(
            (_safe_attr(value, "id"),)
            if value is not None
            else (ExternalTextObservation(False),)
        ),
        "model_identity": capture_external_text_v4(
            (_safe_attr(value, "model"),)
            if value is not None
            else (ExternalTextObservation(False),)
        ),
        "output": capture_external_text_v4(
            (_safe_attr(value, "output_text"),)
            if value is not None
            else (ExternalTextObservation(False),)
        ),
    }
    return canonical_json_bytes(
        {
            "body_state": body_state,
            "http_status": asdict(
                capture_http_status_v4(http_status, present=http_present)
            ),
            **{name: asdict(fact) for name, fact in fields.items()},
            "schema_version": "briefloop.semantic_evaluator.openai_sdk_projection.v4",
            "transport_kind": transport_kind,
        }
    )


class OpenAIResponsesAdapterV4:
    adapter_id = OPENAI_ADAPTER_ID
    adapter_version = OPENAI_ADAPTER_VERSION
    provider_id = OPENAI_PROVIDER_ID
    provider_sdk_name = "openai"
    qualification_eligible = True
    base_url: str | None = None

    def __init__(self, *, api_key: str) -> None:
        if type(api_key) is not str or not api_key:
            raise TypeError("shadow_adapter_unavailable")
        try:
            api_key.encode("utf-8", errors="strict")
            import openai  # type: ignore[import-not-found]

            version = metadata.version("openai")
            sdk_arguments: dict[str, object] = {
                "api_key": api_key,
                "max_retries": 0,
            }
            if self.base_url is not None:
                sdk_arguments["base_url"] = self.base_url
            client = openai.OpenAI(**sdk_arguments)
        except Exception:
            raise TypeError("shadow_adapter_unavailable") from None
        self._openai = openai
        self._client = client
        self.provider_sdk_version = version

    def _attempt_from_response(
        self,
        *,
        request: FrozenProviderRequestV4,
        raw: bytes,
        sdk_response: object | None,
        transport_kind: str = "response",
        transport_http_status: object = None,
        transport_http_present: bool = False,
    ) -> RawProviderAttemptV4:
        projection = project_openai_response_bytes_v4(raw)
        envelope = capture_response_envelope_v4(
            raw,
            present=True,
            invalid_code=projection.envelope_invalid_code,  # type: ignore[arg-type]
        )
        status = projection.status
        response_id = projection.response_id
        model = projection.model_identity
        output = projection.output
        if sdk_response is not None and projection.envelope_valid:
            status = capture_external_text_v4(
                (
                    ExternalTextObservation(
                        True, (projection.status.utf8_bytes or b"").decode("utf-8")
                    ),
                    _safe_attr(sdk_response, "status"),
                ),
                allowed_values=_STATUS_VALUES,
            )
            response_id = capture_external_text_v4(
                (
                    ExternalTextObservation(
                        True, (projection.response_id.utf8_bytes or b"").decode("utf-8")
                    ),
                    _safe_attr(sdk_response, "id"),
                )
            )
            model = capture_external_text_v4(
                (
                    ExternalTextObservation(
                        True,
                        (projection.model_identity.utf8_bytes or b"").decode("utf-8"),
                    ),
                    _safe_attr(sdk_response, "model"),
                )
            )
            if projection.output.state == "present_valid":
                output = capture_external_text_v4(
                    (
                        ExternalTextObservation(
                            True, (projection.output.utf8_bytes or b"").decode("utf-8")
                        ),
                        _safe_attr(sdk_response, "output_text"),
                    )
                )
        provider = capture_external_text_v4(
            (
                ExternalTextObservation(True, request.provider_id),
                ExternalTextObservation(True, self.provider_id),
            )
        )
        facts = make_provider_boundary_facts_v4(
            envelope=envelope,
            status=status,
            response_id=response_id,
            provider_identity=provider,
            model_identity=model,
            output=output,
            http_status=capture_http_status_v4(None, present=False),
            transport_kind=transport_kind,  # type: ignore[arg-type]
        )
        expected = request.expected_model_version.encode("utf-8", errors="strict")
        outcome = classify_provider_outcome_v4(
            facts, expected_model_version_utf8=expected
        )
        extracted = output.utf8_bytes if outcome.output_eligible else None
        return RawProviderAttemptV4(
            facts=facts,
            outcome=outcome,
            request_projection_bytes=request.projection_bytes(),
            raw_transport_response=raw,
            extracted_output=extracted,
            input_tokens=_usage(sdk_response, "input_tokens")
            if sdk_response is not None
            else None,
            output_tokens=_usage(sdk_response, "output_tokens")
            if sdk_response is not None
            else None,
            total_tokens=_usage(sdk_response, "total_tokens")
            if sdk_response is not None
            else None,
            sdk_projection_bytes=_sdk_projection_bytes(
                sdk_response,
                transport_kind=transport_kind,
                http_status=transport_http_status,
                http_present=transport_http_present,
                body_state="present",
            ),
        )

    def _invalid_status_error_attempt(
        self,
        *,
        request: FrozenProviderRequestV4,
        http_status: object,
    ) -> RawProviderAttemptV4:
        absent = _absent_text()
        raw = b""
        facts = make_provider_boundary_facts_v4(
            envelope=capture_response_envelope_v4(
                raw,
                present=True,
                invalid_code="envelope_projection_failed",
            ),
            status=absent,
            response_id=absent,
            provider_identity=capture_external_text_v4(
                (
                    ExternalTextObservation(True, request.provider_id),
                    ExternalTextObservation(True, self.provider_id),
                )
            ),
            model_identity=absent,
            output=absent,
            http_status=capture_http_status_v4(None, present=False),
            transport_kind="http_error",
        )
        outcome = classify_provider_outcome_v4(
            facts,
            expected_model_version_utf8=request.expected_model_version.encode(
                "utf-8", errors="strict"
            ),
        )
        return RawProviderAttemptV4(
            facts=facts,
            outcome=outcome,
            request_projection_bytes=request.projection_bytes(),
            raw_transport_response=raw,
            extracted_output=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            sdk_projection_bytes=_sdk_projection_bytes(
                None,
                transport_kind="http_error",
                http_status=http_status,
                http_present=True,
                body_state="invalid",
            ),
        )

    def _transport_attempt(
        self,
        *,
        request: FrozenProviderRequestV4,
        kind: str,
        http_status: object = None,
        http_present: bool = False,
    ) -> RawProviderAttemptV4:
        absent = _absent_text()
        provider = capture_external_text_v4(
            (
                ExternalTextObservation(True, request.provider_id),
                ExternalTextObservation(True, self.provider_id),
            )
        )
        facts = make_provider_boundary_facts_v4(
            envelope=capture_response_envelope_v4(None, present=False),
            status=absent,
            response_id=absent,
            provider_identity=provider,
            model_identity=absent,
            output=absent,
            http_status=capture_http_status_v4(http_status, present=http_present),
            transport_kind=kind,  # type: ignore[arg-type]
        )
        outcome = classify_provider_outcome_v4(
            facts,
            expected_model_version_utf8=request.expected_model_version.encode("utf-8"),
        )
        return RawProviderAttemptV4(
            facts=facts,
            outcome=outcome,
            request_projection_bytes=request.projection_bytes(),
            raw_transport_response=None,
            extracted_output=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            sdk_projection_bytes=_sdk_projection_bytes(
                None,
                transport_kind=kind,
                http_status=http_status,
                http_present=http_present,
                body_state="absent",
            ),
        )

    def invoke(self, request: FrozenProviderRequestV4) -> RawProviderAttemptV4:
        try:
            raw_response = self._client.responses.with_raw_response.create(
                model=request.model_id,
                instructions=request.system_text,
                input=request.user_text,
                temperature=request.temperature,
                top_p=request.top_p,
                max_output_tokens=request.max_output_tokens,
                store=False,
                timeout=request.timeout_seconds,
            )
            raw = _raw_bytes(raw_response)
            if raw is None:
                return self._transport_attempt(request=request, kind="adapter_error")
            try:
                sdk_response = raw_response.parse()
            except Exception:
                sdk_response = _SDK_READ_FAILED
            return self._attempt_from_response(
                request=request, raw=raw, sdk_response=sdk_response
            )
        except self._openai.APITimeoutError:
            return self._transport_attempt(request=request, kind="timeout")
        except self._openai.APIConnectionError:
            return self._transport_attempt(request=request, kind="connection")
        except self._openai.APIStatusError as error:
            body = _status_error_body(error)
            status = getattr(error, "status_code", None)
            if body.state == "present":
                return self._attempt_from_response(
                    request=request,
                    raw=body.raw or b"",
                    sdk_response=None,
                    transport_kind="http_error",
                    transport_http_status=status,
                    transport_http_present=True,
                )
            if body.state == "invalid":
                return self._invalid_status_error_attempt(
                    request=request,
                    http_status=status,
                )
            return self._transport_attempt(
                request=request,
                kind="http_error",
                http_status=status,
                http_present=True,
            )
        except Exception:
            return self._transport_attempt(request=request, kind="adapter_error")


def synthetic_openai_response_bytes_v4(
    *, status: str, response_id: str, model: str, output_text: str
) -> bytes:
    return canonical_json_bytes(
        {
            "id": response_id,
            "model": model,
            "output": [
                {
                    "content": [{"text": output_text, "type": "output_text"}],
                    "type": "message",
                }
            ],
            "status": status,
        }
    )


__all__ = [
    "OPENAI_ADAPTER_ID",
    "OPENAI_ADAPTER_VERSION",
    "OPENAI_PROVIDER_ID",
    "OpenAIRawProjectionV4",
    "OpenAIResponsesAdapterV4",
    "project_openai_response_bytes_v4",
    "synthetic_openai_response_bytes_v4",
]
