"""Native Anthropic Messages adapter for isolated LAJ shadow evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata
import ipaddress
import json
import re
from typing import Any
from urllib.parse import urlsplit

from multi_agent_brief.semantic_evaluator.adapter import (
    ExternalTextFactV4,
    ExternalTextObservation,
    FrozenProviderRequestV4,
    RawProviderAttemptV4,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    invalid_external_text_fact_v4,
    make_provider_boundary_facts_v4,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes


ANTHROPIC_ADAPTER_ID = "anthropic_messages_v1"
ANTHROPIC_PROVIDER_ID = "anthropic_messages"
ANTHROPIC_ADAPTER_VERSION = "anthropic_messages_adapter_v1"
ANTHROPIC_ENDPOINT_SETTING = "BRIEFLOOP_LAJ_MESSAGES_ENDPOINT"
ANTHROPIC_API_KEY_SETTING = "BRIEFLOOP_LAJ_MESSAGES_API_KEY"
_STOP_REASONS = frozenset(
    {
        "end_turn",
        "max_tokens",
        "model_context_window_exceeded",
        "pause_turn",
        "refusal",
        "stop_sequence",
        "tool_use",
    }
)
_SDK_READ_FAILED = object()
_MISSING = object()


class _DuplicateMember(ValueError):
    pass


def is_supported_anthropic_sdk_version_v1(value: object) -> bool:
    """Accept only the frozen first-party SDK minor line."""

    return type(value) is str and (value == "0.104" or value.startswith("0.104."))


def canonical_messages_endpoint_v1(value: object) -> str:
    """Validate an exact canonical HTTPS endpoint without rewriting it."""

    if type(value) is not str or not value:
        raise TypeError("shadow_request_invalid")
    try:
        value.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        raise TypeError("shadow_request_invalid") from None
    if (
        "\\" in value
        or "%" in value
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise TypeError("shadow_request_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, UnicodeError):
        raise TypeError("shadow_request_invalid") from None
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 443
        or host != host.lower()
        or host.endswith(".")
    ):
        raise TypeError("shadow_request_invalid")
    if ":" in host:
        try:
            address = ipaddress.IPv6Address(host)
        except ipaddress.AddressValueError:
            raise TypeError("shadow_request_invalid") from None
        if address.compressed != host:
            raise TypeError("shadow_request_invalid")
    elif re.fullmatch(r"[0-9.]+", host):
        try:
            address = ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError:
            raise TypeError("shadow_request_invalid") from None
        if str(address) != host:
            raise TypeError("shadow_request_invalid")
    else:
        if len(host) > 253:
            raise TypeError("shadow_request_invalid")
        labels = host.split(".")
        if any(
            not label
            or len(label) > 63
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
            for label in labels
        ):
            raise TypeError("shadow_request_invalid")
    if port == 0:
        raise TypeError("shadow_request_invalid")
    canonical_host = f"[{host}]" if ":" in host else host
    canonical_netloc = canonical_host if port is None else f"{canonical_host}:{port}"
    path = parsed.path
    if (
        parsed.netloc != canonical_netloc
        or path == "/"
        or path.endswith("/")
        or "//" in path
        or any(part in {".", ".."} for part in path.split("/"))
        or not re.fullmatch(r"(?:/[A-Za-z0-9._~!$&'()*+,;=:@-]+)*", path)
    ):
        raise TypeError("shadow_request_invalid")
    canonical = f"https://{canonical_netloc}{path}"
    if value != canonical:
        raise TypeError("shadow_request_invalid")
    return value


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateMember
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise ValueError


def _absent_text() -> ExternalTextFactV4:
    return capture_external_text_v4((ExternalTextObservation(False),))


def _invalid_text(code: str) -> ExternalTextFactV4:
    return invalid_external_text_fact_v4(code)  # type: ignore[arg-type]


def _member(value: dict[str, object], name: str) -> ExternalTextObservation:
    return ExternalTextObservation(name in value, value.get(name))


def normalize_anthropic_stop_reason_v1(
    stop_reason: ExternalTextFactV4,
) -> ExternalTextFactV4:
    """Map a strict Messages stop reason into the sole classifier vocabulary."""

    if stop_reason.state != "present_valid":
        return (
            _absent_text()
            if stop_reason.state == "absent"
            else _invalid_text(stop_reason.invalid_code or "external_text_unknown")
        )
    value = stop_reason.utf8_bytes
    if value == b"end_turn":
        normalized = "completed"
    elif value in {b"max_tokens", b"model_context_window_exceeded"}:
        normalized = "incomplete"
    elif value == b"refusal":
        normalized = "refused"
    else:
        return _invalid_text("external_text_unknown")
    return capture_external_text_v4((ExternalTextObservation(True, normalized),))


def _valid_nonempty_text(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return True


def _project_content(
    value: dict[str, object], *, status: ExternalTextFactV4
) -> ExternalTextFactV4:
    content = value.get("content")
    if type(content) is not list:
        return _invalid_text("external_text_invalid_container")
    chunks: list[str] = []
    try:
        for block in content:
            if type(block) is not dict:
                return _invalid_text("external_text_invalid_container")
            block_type = block.get("type")
            if block_type == "text":
                if set(block) != {"text", "type"} or not _valid_nonempty_text(
                    block.get("text")
                ):
                    return _invalid_text("external_text_projection_mismatch")
                chunks.append(block["text"])  # type: ignore[arg-type]
            elif block_type == "thinking":
                if set(block) != {"signature", "thinking", "type"} or not all(
                    _valid_nonempty_text(block.get(name))
                    for name in ("thinking", "signature")
                ):
                    return _invalid_text("external_text_projection_mismatch")
            elif block_type == "redacted_thinking":
                if set(block) != {"data", "type"} or not _valid_nonempty_text(
                    block.get("data")
                ):
                    return _invalid_text("external_text_projection_mismatch")
            else:
                return _invalid_text("external_text_unknown")
        if not chunks:
            return (
                _invalid_text("external_text_empty")
                if status.utf8_bytes == b"completed"
                else _absent_text()
            )
        return capture_external_text_v4(
            (ExternalTextObservation(True, "".join(chunks)),)
        )
    except Exception:
        return _invalid_text("external_text_read_failed")


def _usage_integer(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


@dataclass(frozen=True)
class AnthropicRawProjectionV1:
    envelope_valid: bool
    envelope_invalid_code: str | None
    stop_reason: ExternalTextFactV4
    status: ExternalTextFactV4
    response_id: ExternalTextFactV4
    model_identity: ExternalTextFactV4
    output: ExternalTextFactV4
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


def project_anthropic_message_bytes_v1(raw: bytes) -> AnthropicRawProjectionV1:
    """Pure strict Messages projector shared by live capture and replay."""

    absent = _absent_text()
    failed = AnthropicRawProjectionV1(
        False,
        "envelope_wrong_type",
        absent,
        absent,
        absent,
        absent,
        absent,
        None,
        None,
        None,
    )
    if type(raw) is not bytes:
        return failed
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return AnthropicRawProjectionV1(
            False,
            "envelope_utf8_invalid",
            absent,
            absent,
            absent,
            absent,
            absent,
            None,
            None,
            None,
        )
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateMember:
        return AnthropicRawProjectionV1(
            False,
            "envelope_duplicate_member",
            absent,
            absent,
            absent,
            absent,
            absent,
            None,
            None,
            None,
        )
    except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
        return AnthropicRawProjectionV1(
            False,
            "envelope_json_invalid",
            absent,
            absent,
            absent,
            absent,
            absent,
            None,
            None,
            None,
        )
    if type(value) is not dict:
        return AnthropicRawProjectionV1(
            False,
            "envelope_not_object",
            absent,
            absent,
            absent,
            absent,
            absent,
            None,
            None,
            None,
        )
    if value.get("type") != "message" or value.get("role") != "assistant":
        return AnthropicRawProjectionV1(
            False,
            "envelope_projection_failed",
            absent,
            absent,
            absent,
            absent,
            absent,
            None,
            None,
            None,
        )
    stop_reason = capture_external_text_v4(
        (_member(value, "stop_reason"),), allowed_values=_STOP_REASONS
    )
    status = normalize_anthropic_stop_reason_v1(stop_reason)
    usage = value.get("usage")
    input_tokens: int | None = None
    output_tokens: int | None = None
    if usage is not None:
        if type(usage) is not dict:
            return AnthropicRawProjectionV1(
                False,
                "envelope_projection_failed",
                stop_reason,
                status,
                absent,
                absent,
                absent,
                None,
                None,
                None,
            )
        input_tokens = _usage_integer(usage.get("input_tokens"))
        output_tokens = _usage_integer(usage.get("output_tokens"))
        if input_tokens is None or output_tokens is None:
            return AnthropicRawProjectionV1(
                False,
                "envelope_projection_failed",
                stop_reason,
                status,
                absent,
                absent,
                absent,
                None,
                None,
                None,
            )
    output = _project_content(value, status=status)
    return AnthropicRawProjectionV1(
        True,
        None,
        stop_reason,
        status,
        capture_external_text_v4((_member(value, "id"),)),
        capture_external_text_v4((_member(value, "model"),)),
        output,
        input_tokens,
        output_tokens,
        (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        ),
    )


def _safe_attr(value: object, name: str) -> ExternalTextObservation:
    if value is _SDK_READ_FAILED:
        return ExternalTextObservation(True, object())
    try:
        return ExternalTextObservation(hasattr(value, name), getattr(value, name, None))
    except Exception:
        return ExternalTextObservation(True, object())


def _sdk_output(value: object | None) -> ExternalTextObservation:
    if value is None:
        return ExternalTextObservation(False)
    try:
        content = getattr(value, "content")
        if type(content) is not list:
            return ExternalTextObservation(True, object())
        chunks: list[str] = []
        for block in content:
            block_type = getattr(block, "type")
            if block_type == "text":
                text = getattr(block, "text")
                if not _valid_nonempty_text(text):
                    return ExternalTextObservation(True, object())
                chunks.append(text)
            elif block_type == "thinking":
                if not all(
                    _valid_nonempty_text(getattr(block, name))
                    for name in ("thinking", "signature")
                ):
                    return ExternalTextObservation(True, object())
            elif block_type == "redacted_thinking":
                if not _valid_nonempty_text(getattr(block, "data")):
                    return ExternalTextObservation(True, object())
            else:
                return ExternalTextObservation(True, object())
        return (
            ExternalTextObservation(True, "".join(chunks))
            if chunks
            else ExternalTextObservation(False)
        )
    except Exception:
        return ExternalTextObservation(True, object())


def _sdk_usage(value: object | None, name: str) -> int | None:
    try:
        item = getattr(getattr(value, "usage"), name)
    except Exception:
        return None
    return _usage_integer(item)


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
        response = getattr(value, "response", _MISSING)
        if response is _MISSING or response is None:
            response = getattr(value, "http_response", _MISSING)
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


def _sdk_projection_bytes(
    value: object | None,
    *,
    transport_kind: str,
    http_status: object = None,
    http_present: bool = False,
    body_state: str,
) -> bytes:
    stop_reason = capture_external_text_v4(
        (
            _safe_attr(value, "stop_reason")
            if value is not None
            else ExternalTextObservation(False),
        ),
        allowed_values=_STOP_REASONS,
    )
    status = normalize_anthropic_stop_reason_v1(stop_reason)
    input_tokens = _sdk_usage(value, "input_tokens")
    output_tokens = _sdk_usage(value, "output_tokens")
    return canonical_json_bytes(
        {
            "body_state": body_state,
            "http_status": asdict(
                capture_http_status_v4(http_status, present=http_present)
            ),
            "input_tokens": input_tokens,
            "model_identity": asdict(
                capture_external_text_v4(
                    (
                        _safe_attr(value, "model")
                        if value is not None
                        else ExternalTextObservation(False),
                    )
                )
            ),
            "output": asdict(capture_external_text_v4((_sdk_output(value),))),
            "output_tokens": output_tokens,
            "response_id": asdict(
                capture_external_text_v4(
                    (
                        _safe_attr(value, "id")
                        if value is not None
                        else ExternalTextObservation(False),
                    )
                )
            ),
            "schema_version": (
                "briefloop.semantic_evaluator.anthropic_sdk_projection.v1"
            ),
            "status": asdict(status),
            "stop_reason": asdict(stop_reason),
            "total_tokens": (
                input_tokens + output_tokens
                if input_tokens is not None and output_tokens is not None
                else None
            ),
            "transport_kind": transport_kind,
        }
    )


class AnthropicMessagesAdapterV1:
    adapter_id = ANTHROPIC_ADAPTER_ID
    adapter_version = ANTHROPIC_ADAPTER_VERSION
    provider_id = ANTHROPIC_PROVIDER_ID
    provider_sdk_name = "anthropic"
    qualification_eligible = False

    def __init__(self, *, api_key: str, endpoint: str) -> None:
        if type(api_key) is not str or not api_key:
            raise TypeError("shadow_adapter_unavailable")
        try:
            api_key.encode("utf-8", errors="strict")
            endpoint = canonical_messages_endpoint_v1(endpoint)
            import anthropic  # type: ignore[import-not-found]

            version = metadata.version("anthropic")
            if not is_supported_anthropic_sdk_version_v1(version):
                raise ValueError
            client = anthropic.Anthropic(
                api_key=api_key,
                base_url=endpoint,
                max_retries=0,
            )
        except Exception:
            raise TypeError("shadow_adapter_unavailable") from None
        self._anthropic = anthropic
        self._client = client
        self.base_url = endpoint
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
        projection = project_anthropic_message_bytes_v1(raw)
        status = projection.status
        response_id = projection.response_id
        model = projection.model_identity
        output = projection.output
        if sdk_response is not None and projection.envelope_valid:
            sdk_stop = capture_external_text_v4(
                (_safe_attr(sdk_response, "stop_reason"),),
                allowed_values=_STOP_REASONS,
            )
            raw_stop_value = (
                (projection.stop_reason.utf8_bytes or b"").decode("utf-8")
                if projection.stop_reason.state == "present_valid"
                else object()
            )
            stop_reason = capture_external_text_v4(
                (
                    ExternalTextObservation(
                        projection.stop_reason.state != "absent", raw_stop_value
                    ),
                    _safe_attr(sdk_response, "stop_reason"),
                ),
                allowed_values=_STOP_REASONS,
            )
            if sdk_stop.state != "present_valid":
                stop_reason = sdk_stop
            status = normalize_anthropic_stop_reason_v1(stop_reason)
            response_id = capture_external_text_v4(
                (
                    ExternalTextObservation(
                        projection.response_id.state != "absent",
                        (
                            (projection.response_id.utf8_bytes or b"").decode("utf-8")
                            if projection.response_id.state == "present_valid"
                            else object()
                        ),
                    ),
                    _safe_attr(sdk_response, "id"),
                )
            )
            model = capture_external_text_v4(
                (
                    ExternalTextObservation(
                        projection.model_identity.state != "absent",
                        (
                            (projection.model_identity.utf8_bytes or b"").decode(
                                "utf-8"
                            )
                            if projection.model_identity.state == "present_valid"
                            else object()
                        ),
                    ),
                    _safe_attr(sdk_response, "model"),
                )
            )
            if projection.output.state == "present_valid":
                output = capture_external_text_v4(
                    (
                        ExternalTextObservation(
                            True,
                            (projection.output.utf8_bytes or b"").decode("utf-8"),
                        ),
                        _sdk_output(sdk_response),
                    )
                )
        provider = capture_external_text_v4(
            (
                ExternalTextObservation(True, request.provider_id),
                ExternalTextObservation(True, self.provider_id),
            )
        )
        facts = make_provider_boundary_facts_v4(
            envelope=capture_response_envelope_v4(
                raw,
                present=True,
                invalid_code=projection.envelope_invalid_code,  # type: ignore[arg-type]
            ),
            status=status,
            response_id=response_id,
            provider_identity=provider,
            model_identity=model,
            output=output,
            http_status=capture_http_status_v4(
                transport_http_status,
                present=transport_http_present,
            ),
            transport_kind=transport_kind,  # type: ignore[arg-type]
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
            extracted_output=output.utf8_bytes if outcome.output_eligible else None,
            input_tokens=projection.input_tokens,
            output_tokens=projection.output_tokens,
            total_tokens=projection.total_tokens,
            sdk_projection_bytes=_sdk_projection_bytes(
                sdk_response,
                transport_kind=transport_kind,
                http_status=transport_http_status,
                http_present=transport_http_present,
                body_state="present",
            ),
        )

    def _transport_attempt(
        self,
        *,
        request: FrozenProviderRequestV4,
        kind: str,
        http_status: object = None,
        http_present: bool = False,
        body_state: str = "absent",
        raw: bytes | None = None,
    ) -> RawProviderAttemptV4:
        absent = _absent_text()
        envelope = capture_response_envelope_v4(
            raw,
            present=raw is not None,
            invalid_code=("envelope_projection_failed" if raw is not None else None),
        )
        facts = make_provider_boundary_facts_v4(
            envelope=envelope,
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
            http_status=capture_http_status_v4(
                http_status,
                present=http_present,
            ),
            transport_kind=kind,  # type: ignore[arg-type]
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
                transport_kind=kind,
                http_status=http_status,
                http_present=http_present,
                body_state=body_state,
            ),
        )

    def invoke(self, request: FrozenProviderRequestV4) -> RawProviderAttemptV4:
        if (
            request.adapter_id != self.adapter_id
            or request.provider_id != self.provider_id
            or request.temperature != 1.0
            or request.top_p != 1.0
            or request.seed is not None
        ):
            return self._transport_attempt(request=request, kind="adapter_error")
        try:
            raw_response = self._client.messages.with_raw_response.create(
                model=request.model_id,
                max_tokens=request.max_output_tokens,
                system=request.system_text,
                messages=[{"role": "user", "content": request.user_text}],
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
        except self._anthropic.APITimeoutError:
            return self._transport_attempt(request=request, kind="timeout")
        except self._anthropic.APIConnectionError:
            return self._transport_attempt(request=request, kind="connection")
        except self._anthropic.APIStatusError as error:
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
                return self._transport_attempt(
                    request=request,
                    kind="http_error",
                    http_status=status,
                    http_present=True,
                    body_state="invalid",
                    raw=b"",
                )
            return self._transport_attempt(
                request=request,
                kind="http_error",
                http_status=status,
                http_present=True,
            )
        except Exception:
            return self._transport_attempt(request=request, kind="adapter_error")


def synthetic_anthropic_message_bytes_v1(
    *,
    stop_reason: str,
    response_id: str,
    model: str,
    content: list[dict[str, object]],
    input_tokens: int = 1,
    output_tokens: int = 1,
) -> bytes:
    return canonical_json_bytes(
        {
            "content": content,
            "id": response_id,
            "model": model,
            "role": "assistant",
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "type": "message",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }
    )


__all__ = [
    "ANTHROPIC_ADAPTER_ID",
    "ANTHROPIC_ADAPTER_VERSION",
    "ANTHROPIC_API_KEY_SETTING",
    "ANTHROPIC_ENDPOINT_SETTING",
    "ANTHROPIC_PROVIDER_ID",
    "AnthropicMessagesAdapterV1",
    "AnthropicRawProjectionV1",
    "canonical_messages_endpoint_v1",
    "is_supported_anthropic_sdk_version_v1",
    "normalize_anthropic_stop_reason_v1",
    "project_anthropic_message_bytes_v1",
    "synthetic_anthropic_message_bytes_v1",
]
