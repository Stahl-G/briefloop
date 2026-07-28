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
    HttpStatusFactV4,
    ProviderBoundaryFactsV4,
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
_TRANSPORT_KINDS = frozenset(
    {"response", "timeout", "connection", "http_error", "adapter_error"}
)
_BODY_STATES = frozenset({"absent", "present", "invalid"})


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
    usage_present: bool
    usage_valid: bool


def _failed_raw_projection(code: str) -> AnthropicRawProjectionV1:
    absent = _absent_text()
    return AnthropicRawProjectionV1(
        False,
        code,
        absent,
        absent,
        absent,
        absent,
        absent,
        None,
        None,
        None,
        False,
        False,
    )


def project_anthropic_message_bytes_v1(raw: bytes) -> AnthropicRawProjectionV1:
    """Pure strict Messages projector shared by live capture and replay."""

    if type(raw) is not bytes:
        return _failed_raw_projection("envelope_wrong_type")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _failed_raw_projection("envelope_utf8_invalid")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateMember:
        return _failed_raw_projection("envelope_duplicate_member")
    except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
        return _failed_raw_projection("envelope_json_invalid")
    if type(value) is not dict:
        return _failed_raw_projection("envelope_not_object")
    if value.get("type") != "message" or value.get("role") != "assistant":
        return _failed_raw_projection("envelope_projection_failed")
    stop_reason = capture_external_text_v4(
        (_member(value, "stop_reason"),), allowed_values=_STOP_REASONS
    )
    status = normalize_anthropic_stop_reason_v1(stop_reason)
    response_id = capture_external_text_v4((_member(value, "id"),))
    model_identity = capture_external_text_v4((_member(value, "model"),))
    output = _project_content(value, status=status)
    usage_present = "usage" in value
    usage = value.get("usage")
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage_valid = not usage_present
    if usage_present and type(usage) is dict:
        input_tokens = _usage_integer(usage.get("input_tokens"))
        output_tokens = _usage_integer(usage.get("output_tokens"))
        usage_valid = input_tokens is not None and output_tokens is not None
        if not usage_valid:
            input_tokens = None
            output_tokens = None
    stop_sequence_valid = (
        "stop_sequence" not in value or value.get("stop_sequence") is None
    )
    envelope_valid = (
        response_id.state == "present_valid"
        and model_identity.state == "present_valid"
        and stop_reason.state == "present_valid"
        and status.state == "present_valid"
        and output.state != "present_invalid"
        and (status.utf8_bytes != b"completed" or output.state == "present_valid")
        and usage_valid
        and stop_sequence_valid
    )
    return AnthropicRawProjectionV1(
        envelope_valid,
        None if envelope_valid else "envelope_projection_failed",
        stop_reason,
        status,
        response_id,
        model_identity,
        output,
        input_tokens,
        output_tokens,
        (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        ),
        usage_present,
        usage_valid,
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


def _sdk_usage(value: object, name: str) -> object:
    try:
        item = getattr(getattr(value, "usage"), name)
    except Exception:
        return _MISSING
    return item


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


def _parse_fact_payload(value: object) -> ExternalTextFactV4:
    if type(value) is not dict or set(value) != {
        "invalid_code",
        "state",
        "utf8_hex",
        "utf8_sha256",
    }:
        raise TypeError("shadow_adapter_unavailable")
    try:
        return ExternalTextFactV4(
            state=value["state"],  # type: ignore[arg-type]
            utf8_hex=value["utf8_hex"],  # type: ignore[arg-type]
            utf8_sha256=value["utf8_sha256"],  # type: ignore[arg-type]
            invalid_code=value["invalid_code"],  # type: ignore[arg-type]
        )
    except Exception:
        raise TypeError("shadow_adapter_unavailable") from None


def _parse_http_status_payload(value: object) -> HttpStatusFactV4:
    if type(value) is not dict or set(value) != {
        "invalid_code",
        "state",
        "value",
    }:
        raise TypeError("shadow_adapter_unavailable")
    try:
        return HttpStatusFactV4(
            state=value["state"],  # type: ignore[arg-type]
            value=value["value"],  # type: ignore[arg-type]
            invalid_code=value["invalid_code"],  # type: ignore[arg-type]
        )
    except Exception:
        raise TypeError("shadow_adapter_unavailable") from None


@dataclass(frozen=True)
class AnthropicSdkProjectionV1:
    stop_reason: ExternalTextFactV4
    status: ExternalTextFactV4
    response_id: ExternalTextFactV4
    model_identity: ExternalTextFactV4
    output: ExternalTextFactV4
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    transport_kind: str
    http_status: HttpStatusFactV4
    body_state: str

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "body_state": self.body_state,
                "http_status": asdict(self.http_status),
                "input_tokens": self.input_tokens,
                "model_identity": asdict(self.model_identity),
                "output": asdict(self.output),
                "output_tokens": self.output_tokens,
                "response_id": asdict(self.response_id),
                "schema_version": (
                    "briefloop.semantic_evaluator.anthropic_sdk_projection.v1"
                ),
                "status": asdict(self.status),
                "stop_reason": asdict(self.stop_reason),
                "total_tokens": self.total_tokens,
                "transport_kind": self.transport_kind,
            }
        )


def parse_anthropic_sdk_projection_v1(raw: bytes) -> AnthropicSdkProjectionV1:
    """Parse one exact canonical SDK fact projection without provider text."""

    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (
        AttributeError,
        UnicodeDecodeError,
        _DuplicateMember,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        RecursionError,
    ):
        raise TypeError("shadow_adapter_unavailable") from None
    required = {
        "body_state",
        "http_status",
        "input_tokens",
        "model_identity",
        "output",
        "output_tokens",
        "response_id",
        "schema_version",
        "status",
        "stop_reason",
        "total_tokens",
        "transport_kind",
    }
    if type(value) is not dict or set(value) != required:
        raise TypeError("shadow_adapter_unavailable")
    try:
        if (
            value["schema_version"]
            != "briefloop.semantic_evaluator.anthropic_sdk_projection.v1"
            or value["transport_kind"] not in _TRANSPORT_KINDS
            or value["body_state"] not in _BODY_STATES
        ):
            raise ValueError
        projection = AnthropicSdkProjectionV1(
            stop_reason=_parse_fact_payload(value["stop_reason"]),
            status=_parse_fact_payload(value["status"]),
            response_id=_parse_fact_payload(value["response_id"]),
            model_identity=_parse_fact_payload(value["model_identity"]),
            output=_parse_fact_payload(value["output"]),
            input_tokens=value["input_tokens"],
            output_tokens=value["output_tokens"],
            total_tokens=value["total_tokens"],
            transport_kind=value["transport_kind"],
            http_status=_parse_http_status_payload(value["http_status"]),
            body_state=value["body_state"],
        )
        usage = (
            projection.input_tokens,
            projection.output_tokens,
            projection.total_tokens,
        )
        if any(
            item is not None and (type(item) is not int or item < 0) for item in usage
        ):
            raise ValueError
        if any(item is not None for item in usage) and (
            projection.input_tokens is None
            or projection.output_tokens is None
            or projection.total_tokens
            != projection.input_tokens + projection.output_tokens
        ):
            raise ValueError
        text_absent = all(
            item.state == "absent"
            for item in (
                projection.stop_reason,
                projection.status,
                projection.response_id,
                projection.model_identity,
                projection.output,
            )
        )
        usage_absent = all(item is None for item in usage)
        if projection.transport_kind == "response":
            valid_shape = (
                projection.body_state in {"present", "invalid"}
                and projection.http_status.state == "absent"
            )
        elif projection.transport_kind == "http_error":
            valid_shape = projection.http_status.state != "absent" and (
                projection.body_state in {"present", "invalid"}
                or (projection.body_state == "absent" and text_absent and usage_absent)
            )
        else:
            valid_shape = (
                projection.body_state == "absent"
                and projection.http_status.state == "absent"
                and text_absent
                and usage_absent
            )
        if not valid_shape or projection.canonical_bytes() != raw:
            raise ValueError
    except Exception:
        raise TypeError("shadow_adapter_unavailable") from None
    return projection


def _reconcile_fact(
    raw_fact: ExternalTextFactV4,
    sdk_fact: ExternalTextFactV4,
) -> ExternalTextFactV4:
    if sdk_fact.state == "absent":
        return raw_fact
    if (
        raw_fact.state == "present_valid"
        and sdk_fact.state == "present_valid"
        and raw_fact.utf8_bytes == sdk_fact.utf8_bytes
    ):
        return raw_fact
    if (
        raw_fact.state == "present_invalid"
        and sdk_fact.state == "present_invalid"
        and raw_fact.invalid_code == sdk_fact.invalid_code
    ):
        return raw_fact
    return _invalid_text("external_text_projection_mismatch")


def _sdk_fact_is_canonical_for_raw(
    raw_fact: ExternalTextFactV4,
    sdk_fact: ExternalTextFactV4,
) -> bool:
    if sdk_fact == raw_fact:
        return True
    return (
        sdk_fact.state == "present_invalid"
        and sdk_fact.invalid_code == "external_text_projection_mismatch"
    )


def _canonical_sdk_fact(
    raw_fact: ExternalTextFactV4,
    observation: ExternalTextObservation,
    *,
    allowed_values: frozenset[str] | None = None,
) -> ExternalTextFactV4:
    sdk_fact = capture_external_text_v4(
        (observation,),
        allowed_values=allowed_values,
    )
    if raw_fact.state != "absent" and sdk_fact.state == "absent":
        return _invalid_text("external_text_projection_mismatch")
    return _reconcile_fact(raw_fact, sdk_fact)


def _sdk_projection_bytes(
    value: object | None,
    *,
    raw_projection: AnthropicRawProjectionV1 | None,
    transport_kind: str,
    http_status: object = None,
    http_present: bool = False,
    body_state: str,
) -> bytes:
    absent = _absent_text()
    if raw_projection is None:
        stop_reason = absent
        response_id = absent
        model_identity = absent
        output = absent
        input_tokens = None
        output_tokens = None
        total_tokens = None
    elif value is None:
        stop_reason = raw_projection.stop_reason
        response_id = raw_projection.response_id
        model_identity = raw_projection.model_identity
        output = raw_projection.output
        if raw_projection.usage_valid:
            input_tokens = raw_projection.input_tokens
            output_tokens = raw_projection.output_tokens
            total_tokens = raw_projection.total_tokens
        else:
            input_tokens = None
            output_tokens = None
            total_tokens = None
            body_state = "invalid"
    else:
        stop_reason = _canonical_sdk_fact(
            raw_projection.stop_reason,
            _safe_attr(value, "stop_reason"),
            allowed_values=_STOP_REASONS,
        )
        response_id = _canonical_sdk_fact(
            raw_projection.response_id,
            _safe_attr(value, "id"),
        )
        model_identity = _canonical_sdk_fact(
            raw_projection.model_identity,
            _safe_attr(value, "model"),
        )
        output = _canonical_sdk_fact(
            raw_projection.output,
            _sdk_output(value),
        )
        observed_input = _sdk_usage(value, "input_tokens")
        observed_output = _sdk_usage(value, "output_tokens")
        if (
            raw_projection.usage_present
            and raw_projection.usage_valid
            and observed_input == raw_projection.input_tokens
            and observed_output == raw_projection.output_tokens
        ):
            input_tokens = raw_projection.input_tokens
            output_tokens = raw_projection.output_tokens
            total_tokens = raw_projection.total_tokens
        elif (
            not raw_projection.usage_present
            and observed_input is _MISSING
            and observed_output is _MISSING
        ):
            input_tokens = None
            output_tokens = None
            total_tokens = None
        else:
            input_tokens = None
            output_tokens = None
            total_tokens = None
            body_state = "invalid"
    if body_state == "invalid":
        stop_reason = _invalid_text("external_text_read_failed")
        response_id = _invalid_text("external_text_read_failed")
        model_identity = _invalid_text("external_text_read_failed")
        output = _invalid_text("external_text_read_failed")
        input_tokens = None
        output_tokens = None
        total_tokens = None
    status = normalize_anthropic_stop_reason_v1(stop_reason)
    projection = AnthropicSdkProjectionV1(
        stop_reason=stop_reason,
        status=status,
        response_id=response_id,
        model_identity=model_identity,
        output=output,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        transport_kind=transport_kind,
        http_status=capture_http_status_v4(http_status, present=http_present),
        body_state=body_state,
    )
    return projection.canonical_bytes()


@dataclass(frozen=True)
class AnthropicAttemptProjectionV1:
    facts: ProviderBoundaryFactsV4
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    sdk_projection_bytes: bytes


def project_anthropic_attempt_v1(
    *,
    raw: bytes | None,
    sdk_projection_raw: bytes,
    provider_id: str,
) -> AnthropicAttemptProjectionV1:
    """Reconcile exact raw and canonical SDK facts for live capture and replay."""

    sdk = parse_anthropic_sdk_projection_v1(sdk_projection_raw)
    absent = _absent_text()
    provider = capture_external_text_v4(
        (
            ExternalTextObservation(True, provider_id),
            ExternalTextObservation(True, ANTHROPIC_PROVIDER_ID),
        )
    )
    if raw is None:
        if (
            sdk.body_state != "absent"
            or any(
                item.state != "absent"
                for item in (
                    sdk.stop_reason,
                    sdk.status,
                    sdk.response_id,
                    sdk.model_identity,
                    sdk.output,
                )
            )
            or any(
                item is not None
                for item in (
                    sdk.input_tokens,
                    sdk.output_tokens,
                    sdk.total_tokens,
                )
            )
        ):
            raise TypeError("shadow_adapter_unavailable")
        facts = make_provider_boundary_facts_v4(
            envelope=capture_response_envelope_v4(None, present=False),
            status=absent,
            response_id=absent,
            provider_identity=provider,
            model_identity=absent,
            output=absent,
            http_status=sdk.http_status,
            transport_kind=sdk.transport_kind,  # type: ignore[arg-type]
        )
        return AnthropicAttemptProjectionV1(
            facts,
            None,
            None,
            None,
            sdk_projection_raw,
        )

    raw_projection = project_anthropic_message_bytes_v1(raw)
    if sdk.body_state not in {"present", "invalid"}:
        raise TypeError("shadow_adapter_unavailable")
    raw_status = normalize_anthropic_stop_reason_v1(raw_projection.stop_reason)
    if sdk.body_state == "invalid":
        if any(
            item.state != "present_invalid"
            or item.invalid_code != "external_text_read_failed"
            for item in (
                sdk.stop_reason,
                sdk.status,
                sdk.response_id,
                sdk.model_identity,
                sdk.output,
            )
        ) or any(
            item is not None
            for item in (
                sdk.input_tokens,
                sdk.output_tokens,
                sdk.total_tokens,
            )
        ):
            raise TypeError("shadow_adapter_unavailable")
    else:
        for raw_fact, sdk_fact in (
            (raw_projection.stop_reason, sdk.stop_reason),
            (raw_status, sdk.status),
            (raw_projection.response_id, sdk.response_id),
            (raw_projection.model_identity, sdk.model_identity),
            (raw_projection.output, sdk.output),
        ):
            if not _sdk_fact_is_canonical_for_raw(raw_fact, sdk_fact):
                raise TypeError("shadow_adapter_unavailable")
        raw_usage = (
            raw_projection.input_tokens,
            raw_projection.output_tokens,
            raw_projection.total_tokens,
        )
        sdk_usage = (sdk.input_tokens, sdk.output_tokens, sdk.total_tokens)
        if not raw_projection.usage_valid or sdk_usage != raw_usage:
            raise TypeError("shadow_adapter_unavailable")
    if sdk.body_state == "invalid":
        stop_reason = sdk.stop_reason
        status = sdk.status
        response_id = sdk.response_id
        model_identity = sdk.model_identity
        output = sdk.output
    else:
        stop_reason = _reconcile_fact(raw_projection.stop_reason, sdk.stop_reason)
        status = _reconcile_fact(
            normalize_anthropic_stop_reason_v1(stop_reason),
            sdk.status,
        )
        response_id = _reconcile_fact(raw_projection.response_id, sdk.response_id)
        model_identity = _reconcile_fact(
            raw_projection.model_identity,
            sdk.model_identity,
        )
        output = _reconcile_fact(raw_projection.output, sdk.output)
    raw_usage = (
        raw_projection.input_tokens,
        raw_projection.output_tokens,
        raw_projection.total_tokens,
    )
    if sdk.body_state == "invalid":
        input_tokens = None
        output_tokens = None
        total_tokens = None
    else:
        input_tokens, output_tokens, total_tokens = raw_usage
    envelope_invalid_code = raw_projection.envelope_invalid_code
    sdk_projection_mismatch = any(
        item.state == "present_invalid"
        and item.invalid_code == "external_text_projection_mismatch"
        for item in (
            sdk.stop_reason,
            sdk.status,
            sdk.response_id,
            sdk.model_identity,
            sdk.output,
        )
    )
    if sdk.body_state == "invalid" or sdk_projection_mismatch:
        envelope_invalid_code = "envelope_projection_failed"
    facts = make_provider_boundary_facts_v4(
        envelope=capture_response_envelope_v4(
            raw,
            present=True,
            invalid_code=envelope_invalid_code,  # type: ignore[arg-type]
        ),
        status=status,
        response_id=response_id,
        provider_identity=provider,
        model_identity=model_identity,
        output=output,
        http_status=sdk.http_status,
        transport_kind=sdk.transport_kind,  # type: ignore[arg-type]
    )
    return AnthropicAttemptProjectionV1(
        facts,
        input_tokens,
        output_tokens,
        total_tokens,
        sdk_projection_raw,
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
        raw_projection = project_anthropic_message_bytes_v1(raw)
        sdk_projection_bytes = _sdk_projection_bytes(
            sdk_response,
            raw_projection=raw_projection,
            transport_kind=transport_kind,
            http_status=transport_http_status,
            http_present=transport_http_present,
            body_state=(
                "invalid" if sdk_response is _SDK_READ_FAILED else "present"
            ),
        )
        shared = project_anthropic_attempt_v1(
            raw=raw,
            sdk_projection_raw=sdk_projection_bytes,
            provider_id=request.provider_id,
        )
        outcome = classify_provider_outcome_v4(
            shared.facts,
            expected_model_version_utf8=request.expected_model_version.encode(
                "utf-8", errors="strict"
            ),
        )
        return RawProviderAttemptV4(
            facts=shared.facts,
            outcome=outcome,
            request_projection_bytes=request.projection_bytes(),
            raw_transport_response=raw,
            extracted_output=(
                shared.facts.output.utf8_bytes if outcome.output_eligible else None
            ),
            input_tokens=shared.input_tokens,
            output_tokens=shared.output_tokens,
            total_tokens=shared.total_tokens,
            sdk_projection_bytes=shared.sdk_projection_bytes,
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
        raw_projection = (
            project_anthropic_message_bytes_v1(raw) if raw is not None else None
        )
        sdk_projection_bytes = _sdk_projection_bytes(
            None,
            raw_projection=raw_projection,
            transport_kind=kind,  # type: ignore[arg-type]
            http_status=http_status,
            http_present=http_present,
            body_state=body_state,
        )
        shared = project_anthropic_attempt_v1(
            raw=raw,
            sdk_projection_raw=sdk_projection_bytes,
            provider_id=request.provider_id,
        )
        outcome = classify_provider_outcome_v4(
            shared.facts,
            expected_model_version_utf8=request.expected_model_version.encode(
                "utf-8", errors="strict"
            ),
        )
        return RawProviderAttemptV4(
            facts=shared.facts,
            outcome=outcome,
            request_projection_bytes=request.projection_bytes(),
            raw_transport_response=raw,
            extracted_output=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            sdk_projection_bytes=shared.sdk_projection_bytes,
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
        raw = _raw_bytes(raw_response)
        if raw is None:
            return self._transport_attempt(request=request, kind="adapter_error")
        try:
            sdk_response = raw_response.parse()
        except Exception:
            sdk_response = _SDK_READ_FAILED
        try:
            return self._attempt_from_response(
                request=request, raw=raw, sdk_response=sdk_response
            )
        except Exception:
            return self._transport_attempt(
                request=request,
                kind="response",
                body_state="invalid",
                raw=raw,
            )


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
    "AnthropicAttemptProjectionV1",
    "AnthropicRawProjectionV1",
    "AnthropicSdkProjectionV1",
    "canonical_messages_endpoint_v1",
    "is_supported_anthropic_sdk_version_v1",
    "normalize_anthropic_stop_reason_v1",
    "parse_anthropic_sdk_projection_v1",
    "project_anthropic_attempt_v1",
    "project_anthropic_message_bytes_v1",
    "synthetic_anthropic_message_bytes_v1",
]
