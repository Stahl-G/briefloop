from __future__ import annotations

from dataclasses import replace
import json
import sys
from types import SimpleNamespace

import pytest

from multi_agent_brief.semantic_evaluator.adapter import (
    FrozenProviderRequestV4,
    PROVIDER_BOUNDARY_FACTS_SCHEMA_ID,
    ExternalTextObservation,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
)
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_ADAPTER_ID,
    ANTHROPIC_API_KEY_SETTING,
    ANTHROPIC_ENDPOINT_SETTING,
    ANTHROPIC_PROVIDER_ID,
    AnthropicMessagesAdapterV1,
    canonical_messages_endpoint_v1,
    is_supported_anthropic_sdk_version_v1,
    project_anthropic_message_bytes_v1,
    synthetic_anthropic_message_bytes_v1,
)
from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
    OPENAI_ADAPTER_ID,
    OPENAI_BASE_URL,
    OPENAI_PROVIDER_ID,
    OpenAIResponsesAdapterV4,
    project_openai_response_bytes_v4,
    synthetic_openai_response_bytes_v4,
)
from multi_agent_brief.semantic_evaluator.adapters.local_proxy_responses import (
    CLIPROXY_ADAPTER_ID,
    CLIPROXY_BASE_URL,
    CLIPROXY_PROVIDER_ID,
    CLIProxyResponsesAdapterV1,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    project_synthetic_response_bytes_v4,
)
from multi_agent_brief.semantic_evaluator.prompt_sizer import (
    AnthropicUtf8BytePromptSizerV1,
    CLIProxyUtf8BytePromptSizerV1,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    sha256_bytes,
)


EXPECTED_MODEL = b"gpt-test-2026-07-18"
ANTHROPIC_TEST_MODEL = "public-nonclaude-model-v1"
ANTHROPIC_TEST_ENDPOINT = "https://messages.example.test/v1"
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


def _openai_request() -> FrozenProviderRequestV4:
    return FrozenProviderRequestV4(
        trial_id="trial-public",
        dimension_id="dimension-1",
        attempt_ordinal=1,
        system_text="system",
        user_text="user",
        prompt_request_sha256="1" * 64,
        adapter_id=OPENAI_ADAPTER_ID,
        provider_id=OPENAI_PROVIDER_ID,
        model_id="gpt-test",
        expected_model_version=EXPECTED_MODEL.decode(),
        temperature=0.0,
        top_p=1.0,
        max_output_tokens=100,
        seed=None,
        timeout_seconds=60,
    )


def _anthropic_request() -> FrozenProviderRequestV4:
    return FrozenProviderRequestV4(
        trial_id="trial-anthropic-public",
        dimension_id="dimension-1",
        attempt_ordinal=1,
        system_text="system",
        user_text="user",
        prompt_request_sha256="2" * 64,
        adapter_id=ANTHROPIC_ADAPTER_ID,
        provider_id=ANTHROPIC_PROVIDER_ID,
        model_id=ANTHROPIC_TEST_MODEL,
        expected_model_version=ANTHROPIC_TEST_MODEL,
        temperature=1.0,
        top_p=1.0,
        max_output_tokens=100,
        seed=None,
        timeout_seconds=60,
    )


def _anthropic_raw_payload() -> dict[str, object]:
    return json.loads(
        synthetic_anthropic_message_bytes_v1(
            stop_reason="end_turn",
            response_id="msg-public",
            model=ANTHROPIC_TEST_MODEL,
            content=[{"type": "text", "text": '{"findings":[]}'}],
        )
    )


class _AnthropicTimeoutError(Exception):
    pass


class _AnthropicConnectionError(Exception):
    pass


class _AnthropicStatusError(Exception):
    pass


def _anthropic_invoke_adapter(create):
    adapter = object.__new__(AnthropicMessagesAdapterV1)
    adapter._client = SimpleNamespace(
        messages=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=create),
        )
    )
    adapter._anthropic = SimpleNamespace(
        APITimeoutError=_AnthropicTimeoutError,
        APIConnectionError=_AnthropicConnectionError,
        APIStatusError=_AnthropicStatusError,
    )
    return adapter


def _invoke_anthropic_response(
    raw: bytes,
    *,
    sdk_response: object | None = None,
    parse_error: str | None = None,
):
    calls = {"parse": 0, "provider": 0}

    def parse():
        calls["parse"] += 1
        if parse_error is not None:
            raise RuntimeError(parse_error)
        return sdk_response

    def create(**_kwargs):
        calls["provider"] += 1
        return SimpleNamespace(
            http_response=SimpleNamespace(content=raw),
            parse=parse,
        )

    attempt = _anthropic_invoke_adapter(create).invoke(_anthropic_request())
    return attempt, calls


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


def test_se2r_02_openai_raw_incomplete_is_terminal_even_with_valid_output() -> None:
    raw = synthetic_openai_response_bytes_v4(
        status="incomplete",
        response_id="resp-public",
        model=EXPECTED_MODEL.decode(),
        output_text='{"findings":[]}',
    )
    adapter = object.__new__(OpenAIResponsesAdapterV4)
    attempt = adapter._attempt_from_response(
        request=_openai_request(), raw=raw, sdk_response=None
    )
    assert attempt.outcome.shadow_reason == "provider_incomplete"
    assert attempt.outcome.retry_eligible is False
    assert attempt.extracted_output is None
    assert attempt.sdk_projection_bytes is not None


def test_cliproxy_reuses_openai_projector_with_distinct_provider_identity() -> None:
    request = replace(
        _openai_request(),
        adapter_id=CLIPROXY_ADAPTER_ID,
        provider_id=CLIPROXY_PROVIDER_ID,
    )
    raw = synthetic_openai_response_bytes_v4(
        status="completed",
        response_id="resp-public",
        model=EXPECTED_MODEL.decode(),
        output_text='{"findings":[]}',
    )
    attempt = object.__new__(CLIProxyResponsesAdapterV1)._attempt_from_response(
        request=request,
        raw=raw,
        sdk_response=None,
    )
    assert attempt.outcome.attempt_status == "completed"
    assert attempt.facts.provider_identity.utf8_bytes == CLIPROXY_PROVIDER_ID.encode()
    assert attempt.extracted_output == b'{"findings":[]}'


def test_cliproxy_constructor_freezes_loopback_endpoint_and_disables_sdk_retry(
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator.adapters import openai_responses

    captured: dict[str, object] = {}

    def make_client(**kwargs):
        captured.update(kwargs)
        return object()

    direct_transport = object()
    transport_arguments: dict[str, object] = {}

    def make_http_client(**kwargs):
        transport_arguments.update(kwargs)
        return direct_transport

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(
            DefaultHttpxClient=make_http_client,
            OpenAI=make_client,
        ),
    )
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setattr(openai_responses.metadata, "version", lambda _name: "2.46.0")
    adapter = CLIProxyResponsesAdapterV1(api_key="test")
    assert adapter.base_url == CLIPROXY_BASE_URL
    assert adapter.qualification_eligible is False
    assert captured == {
        "api_key": "test",
        "base_url": CLIPROXY_BASE_URL,
        "http_client": direct_transport,
        "max_retries": 0,
    }
    assert transport_arguments == {
        "follow_redirects": False,
        "trust_env": False,
    }


def test_direct_openai_constructor_ignores_environment_base_url(monkeypatch) -> None:
    from multi_agent_brief.semantic_evaluator.adapters import openai_responses

    captured: dict[str, object] = {}

    def make_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("OPENAI_BASE_URL", "http://proxy.invalid/v1")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=make_client))
    monkeypatch.setattr(openai_responses.metadata, "version", lambda _name: "2.46.0")

    adapter = OpenAIResponsesAdapterV4(api_key="test")

    assert adapter.base_url == OPENAI_BASE_URL
    assert captured == {
        "api_key": "test",
        "base_url": OPENAI_BASE_URL,
        "max_retries": 0,
    }


def test_cliproxy_prompt_sizer_is_strict_utf8_and_conservative() -> None:
    sizer = CLIProxyUtf8BytePromptSizerV1()
    assert sizer.count_tokens(system_text="A", user_text="中文") == 15
    with pytest.raises(Exception) as exc_info:
        sizer.count_tokens(system_text="bad\ud800", user_text="ok")
    assert getattr(exc_info.value, "reason_code", None) == "prompt_sizer_unavailable"


def test_anthropic_projector_keeps_thinking_out_of_final_text() -> None:
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[
            {"type": "thinking", "thinking": "private reasoning", "signature": "sig"},
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "text", "text": '{"findings":[]}'},
        ],
        input_tokens=11,
        output_tokens=7,
    )
    projection = project_anthropic_message_bytes_v1(raw)
    assert projection.envelope_valid is True
    assert projection.status.utf8_bytes == b"completed"
    assert projection.output.utf8_bytes == b'{"findings":[]}'
    assert b"private reasoning" not in (projection.output.utf8_bytes or b"")
    assert (
        projection.input_tokens,
        projection.output_tokens,
        projection.total_tokens,
    ) == (
        11,
        7,
        18,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("stop_reason", None, id="missing-stop"),
        ("stop_reason", "future-stop"),
        pytest.param("id", None, id="missing-id"),
        ("id", ""),
        ("id", 7),
        ("id", "\ud800"),
        pytest.param("model", None, id="missing-model"),
        ("model", ""),
        ("model", 7),
        ("model", "\ud800"),
    ],
)
def test_anthropic_required_envelope_facts_are_replayable_negative_evidence(
    field: str,
    value: object,
) -> None:
    payload = _anthropic_raw_payload()
    if value is None:
        payload.pop(field)
    else:
        payload[field] = value
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=None,
    )
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.retry_eligible is False
    assert attempt.outcome.output_eligible is False
    assert attempt.extracted_output is None
    assert attempt.sdk_projection_bytes is not None


@pytest.mark.parametrize(
    ("stop_sequence", "valid"),
    [
        pytest.param("__absent__", True, id="absent"),
        (None, True),
        ("ordinary", False),
        ("", False),
        (7, False),
        ({"value": "x"}, False),
        (["x"], False),
        (True, False),
        ("\ud800", False),
    ],
)
def test_anthropic_stop_sequence_is_only_absent_or_null(
    stop_sequence: object,
    valid: bool,
) -> None:
    payload = _anthropic_raw_payload()
    if stop_sequence == "__absent__":
        payload.pop("stop_sequence")
    else:
        payload["stop_sequence"] = stop_sequence
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=None,
    )
    assert attempt.facts.envelope.state == (
        "present_valid" if valid else "present_invalid"
    )
    assert attempt.outcome.shadow_reason == (
        None if valid else "provider_boundary_invalid"
    )
    assert attempt.outcome.output_eligible is valid
    assert attempt.outcome.retry_eligible is False
    assert attempt.extracted_output == (b'{"findings":[]}' if valid else None)
    assert b"ordinary" not in (attempt.sdk_projection_bytes or b"")


@pytest.mark.parametrize(
    "field",
    ["stop_reason", "id", "model", "output", "usage"],
)
def test_anthropic_sdk_mismatch_is_value_free_terminal_evidence(field: str) -> None:
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )
    sentinel = f"sdk-mismatch-{field}"
    sdk_response = SimpleNamespace(
        id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text='{"findings":[]}')],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    if field == "stop_reason":
        sdk_response.stop_reason = sentinel
    elif field == "id":
        sdk_response.id = sentinel
    elif field == "model":
        sdk_response.model = sentinel
    elif field == "output":
        sdk_response.content = [SimpleNamespace(type="text", text=sentinel)]
    elif field == "usage":
        sdk_response.usage = SimpleNamespace(input_tokens=2, output_tokens=1)
    else:
        raise AssertionError(field)
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=sdk_response,
    )
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.retry_eligible is False
    assert attempt.outcome.output_eligible is False
    assert attempt.extracted_output is None
    assert sentinel.encode("utf-8") not in (attempt.sdk_projection_bytes or b"")


def test_anthropic_invalid_raw_sdk_fact_cannot_rescue_completion() -> None:
    payload = _anthropic_raw_payload()
    payload.pop("id")
    raw = canonical_json_bytes(payload)
    sentinel = "sdk-unattested-id"
    sdk_response = SimpleNamespace(
        id=sentinel,
        model=ANTHROPIC_TEST_MODEL,
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text='{"findings":[]}')],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=sdk_response,
    )
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.facts.response_id.state == "present_invalid"
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.extracted_output is None
    assert sentinel.encode("utf-8") not in (attempt.sdk_projection_bytes or b"")


@pytest.mark.parametrize(
    ("stop_reason", "reason"),
    [
        ("max_tokens", "provider_incomplete"),
        ("model_context_window_exceeded", "provider_incomplete"),
        ("refusal", "provider_refused"),
    ],
)
def test_anthropic_terminal_stop_reasons_never_expose_output(
    stop_reason: str, reason: str
) -> None:
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason=stop_reason,
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=None,
    )
    assert attempt.outcome.shadow_reason == reason
    assert attempt.outcome.retry_eligible is False
    assert attempt.extracted_output is None


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "tool_use", "id": "tool-1"}],
        [{"type": "thinking", "thinking": "x"}],
        [{"type": "redacted_thinking", "data": ""}],
        [{"type": "text", "text": ""}],
    ],
)
def test_anthropic_unknown_or_malformed_blocks_fail_closed(
    content: list[dict[str, object]],
) -> None:
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=content,
    )
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=None,
    )
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.extracted_output is None


@pytest.mark.parametrize(
    "stop_reason",
    ["pause_turn", "stop_sequence", "tool_use", "future_stop_reason"],
)
def test_anthropic_unexpected_stop_reason_is_terminal_invalid(
    stop_reason: str,
) -> None:
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason=stop_reason,
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=None,
    )
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.retry_eligible is False
    assert attempt.extracted_output is None


def test_anthropic_model_drift_is_terminal_identity_mismatch() -> None:
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-public",
        model="public-nonclaude-model-v1-drifted",
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=None,
    )
    assert attempt.outcome.shadow_reason == "provider_identity_mismatch"
    assert attempt.extracted_output is None


def test_anthropic_present_http_error_binds_status_and_stays_terminal() -> None:
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-http-error-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )
    adapter = object.__new__(AnthropicMessagesAdapterV1)
    normal = adapter._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=None,
    )
    assert normal.facts.http_status.state == "absent"

    present_error = adapter._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=None,
        transport_kind="http_error",
        transport_http_status=503,
        transport_http_present=True,
    )
    assert present_error.facts.envelope.state == "present_valid"
    assert present_error.facts.http_status.state == "present_valid"
    assert present_error.facts.http_status.value == 503
    assert present_error.outcome.retry_eligible is False
    assert present_error.outcome.output_eligible is False
    assert present_error.outcome.shadow_reason == "provider_boundary_invalid"
    sdk_projection = json.loads(present_error.sdk_projection_bytes or b"")
    assert sdk_projection["http_status"] == {
        "invalid_code": None,
        "state": "present_valid",
        "value": 503,
    }


def test_anthropic_sdk_parse_failure_cannot_fall_back_to_raw_success() -> None:
    from multi_agent_brief.semantic_evaluator.adapters import anthropic_messages

    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=_anthropic_request(),
        raw=raw,
        sdk_response=anthropic_messages._SDK_READ_FAILED,
    )
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.extracted_output is None


def test_anthropic_invoke_retains_invalid_utf8_when_sdk_parse_fails() -> None:
    sentinel = "parse-secret-sentinel"
    raw = b"\xffcaptured-response"
    attempt, calls = _invoke_anthropic_response(
        raw,
        parse_error=sentinel,
    )
    assert calls == {"parse": 1, "provider": 1}
    assert attempt.facts.transport_kind == "response"
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.facts.envelope.raw_sha256 == sha256_bytes(raw)
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is False
    assert attempt.raw_transport_response == raw
    assert attempt.extracted_output is None
    assert sentinel.encode() not in (attempt.sdk_projection_bytes or b"")


def _anthropic_parse_failure_cases() -> list[tuple[str, bytes]]:
    valid = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )
    wrong_type = _anthropic_raw_payload()
    wrong_type["type"] = "future_message"
    wrong_role = _anthropic_raw_payload()
    wrong_role["role"] = "user"
    return [
        ("invalid_json", b'{"type":"message"'),
        (
            "duplicate_member",
            b'{"role":"assistant","type":"message","type":"message"}',
        ),
        ("non_object_json", b'["message"]'),
        ("wrong_messages_type", canonical_json_bytes(wrong_type)),
        ("wrong_messages_role", canonical_json_bytes(wrong_role)),
        ("valid_messages_body", valid),
    ]


@pytest.mark.parametrize(
    ("_case_id", "raw"),
    _anthropic_parse_failure_cases(),
    ids=[item[0] for item in _anthropic_parse_failure_cases()],
)
def test_anthropic_invoke_parse_failure_retains_exact_response(
    _case_id: str,
    raw: bytes,
) -> None:
    sentinel = "parse-provider-diagnostic-must-not-survive"
    attempt, calls = _invoke_anthropic_response(raw, parse_error=sentinel)

    assert calls == {"parse": 1, "provider": 1}
    assert attempt.facts.transport_kind == "response"
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.facts.envelope.raw_sha256 == sha256_bytes(raw)
    assert attempt.raw_transport_response == raw
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is False
    assert attempt.extracted_output is None
    sdk_projection = json.loads(attempt.sdk_projection_bytes or b"")
    assert sdk_projection["body_state"] == "invalid"
    for field in ("model_identity", "output", "response_id", "status", "stop_reason"):
        assert sdk_projection[field] == {
            "invalid_code": "external_text_read_failed",
            "state": "present_invalid",
            "utf8_hex": None,
            "utf8_sha256": None,
        }
    assert sentinel.encode() not in (attempt.sdk_projection_bytes or b"")


@pytest.mark.parametrize(
    ("raising_field", "sdk_fact_field"),
    [
        ("id", "response_id"),
        ("model", "model_identity"),
        ("stop_reason", "stop_reason"),
        ("content", "output"),
        ("usage", "output"),
    ],
)
def test_anthropic_invoke_sdk_getter_failure_retains_exact_response(
    raising_field: str,
    sdk_fact_field: str,
) -> None:
    sentinel = f"getter-secret-{raising_field}"
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )

    class SdkResponse:
        def _value(self, name: str, value: object) -> object:
            if raising_field == name:
                raise RuntimeError(sentinel)
            return value

        @property
        def id(self):
            return self._value("id", "msg-public")

        @property
        def model(self):
            return self._value("model", ANTHROPIC_TEST_MODEL)

        @property
        def stop_reason(self):
            return self._value("stop_reason", "end_turn")

        @property
        def content(self):
            return self._value(
                "content",
                [SimpleNamespace(type="text", text='{"findings":[]}')],
            )

        @property
        def usage(self):
            return self._value(
                "usage",
                SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    attempt, calls = _invoke_anthropic_response(raw, sdk_response=SdkResponse())

    assert calls == {"parse": 1, "provider": 1}
    assert attempt.facts.transport_kind == "response"
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.facts.envelope.raw_sha256 == sha256_bytes(raw)
    assert attempt.raw_transport_response == raw
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is False
    sdk_projection = json.loads(attempt.sdk_projection_bytes or b"")
    assert sdk_projection[sdk_fact_field]["state"] == "present_invalid"
    assert sentinel.encode() not in (attempt.sdk_projection_bytes or b"")


def test_anthropic_invoke_projector_failure_cannot_discard_captured_raw() -> None:
    sentinel = "projector-secret-sentinel"
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )
    calls = {"parse": 0, "provider": 0}

    def parse():
        calls["parse"] += 1
        return None

    def create(**_kwargs):
        calls["provider"] += 1
        return SimpleNamespace(
            http_response=SimpleNamespace(content=raw),
            parse=parse,
        )

    adapter = _anthropic_invoke_adapter(create)

    def fail_projection(**_kwargs):
        raise RuntimeError(sentinel)

    adapter._attempt_from_response = fail_projection
    attempt = adapter.invoke(_anthropic_request())

    assert calls == {"parse": 1, "provider": 1}
    assert attempt.facts.transport_kind == "response"
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.facts.envelope.raw_sha256 == sha256_bytes(raw)
    assert attempt.raw_transport_response == raw
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is False
    assert sentinel.encode() not in (attempt.sdk_projection_bytes or b"")


@pytest.mark.parametrize(
    ("exception_type", "transport_kind"),
    [
        (_AnthropicTimeoutError, "timeout"),
        (_AnthropicConnectionError, "connection"),
        (RuntimeError, "adapter_error"),
    ],
)
def test_anthropic_pre_body_transport_failure_remains_bodyless(
    exception_type: type[Exception],
    transport_kind: str,
) -> None:
    sentinel = f"transport-secret-{transport_kind}"
    provider_calls = 0

    def create(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise exception_type(sentinel)

    attempt = _anthropic_invoke_adapter(create).invoke(_anthropic_request())

    assert provider_calls == 1
    assert attempt.facts.transport_kind == transport_kind
    assert attempt.facts.envelope.state == "absent"
    assert attempt.facts.envelope.raw_sha256 is None
    assert attempt.raw_transport_response is None
    assert attempt.outcome.output_eligible is False
    assert sentinel.encode() not in (attempt.sdk_projection_bytes or b"")


@pytest.mark.parametrize(
    ("raw", "expected_envelope_state"),
    [
        (b"", "present_invalid"),
        (b"\xffhttp-error-body", "present_invalid"),
        (
            synthetic_anthropic_message_bytes_v1(
                stop_reason="end_turn",
                response_id="msg-http-error-public",
                model=ANTHROPIC_TEST_MODEL,
                content=[{"type": "text", "text": '{"findings":[]}'}],
            ),
            "present_valid",
        ),
    ],
)
def test_anthropic_http_status_retains_exact_present_body(
    raw: bytes,
    expected_envelope_state: str,
) -> None:
    provider_calls = 0
    error = _AnthropicStatusError("status-secret-sentinel")
    error.status_code = 503  # type: ignore[attr-defined]
    error.response = SimpleNamespace(content=raw)  # type: ignore[attr-defined]

    def create(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise error

    attempt = _anthropic_invoke_adapter(create).invoke(_anthropic_request())

    assert provider_calls == 1
    assert attempt.facts.transport_kind == "http_error"
    assert attempt.facts.http_status.state == "present_valid"
    assert attempt.facts.http_status.value == 503
    assert attempt.facts.envelope.state == expected_envelope_state
    assert attempt.facts.envelope.raw_sha256 == sha256_bytes(raw)
    assert attempt.raw_transport_response == raw
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is False
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert b"status-secret-sentinel" not in (attempt.sdk_projection_bytes or b"")


@pytest.mark.parametrize(
    ("body_mode", "status_code"),
    [
        ("missing", 503),
        ("none", 408),
        ("none", 409),
        ("none", 429),
        ("none", 500),
        ("text", 503),
        ("object", 503),
        ("raising", 503),
    ],
)
def test_anthropic_http_unreadable_body_has_no_fabricated_raw(
    body_mode: str,
    status_code: int,
) -> None:
    sentinel = "content-secret-sentinel"
    if body_mode == "missing":
        response = SimpleNamespace()
    elif body_mode == "none":
        response = SimpleNamespace(content=None)
    elif body_mode == "text":
        response = SimpleNamespace(content="not-exact-bytes")
    elif body_mode == "object":
        response = SimpleNamespace(content=object())
    elif body_mode == "raising":

        class RaisingResponse:
            @property
            def content(self):
                raise RuntimeError(sentinel)

        response = RaisingResponse()
    else:
        raise AssertionError(body_mode)
    error = _AnthropicStatusError("status-secret-sentinel")
    error.status_code = status_code  # type: ignore[attr-defined]
    error.response = response  # type: ignore[attr-defined]

    def create(**_kwargs):
        raise error

    attempt = _anthropic_invoke_adapter(create).invoke(_anthropic_request())

    assert attempt.facts.transport_kind == "http_error"
    assert attempt.facts.http_status.state == "present_valid"
    assert attempt.facts.http_status.value == status_code
    assert attempt.facts.envelope.state == "absent"
    assert attempt.facts.envelope.raw_size_bytes is None
    assert attempt.facts.envelope.raw_sha256 is None
    assert attempt.raw_transport_response is None
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is False
    sdk_projection = json.loads(attempt.sdk_projection_bytes or b"")
    assert sdk_projection["body_state"] == "invalid"
    assert sdk_projection["output"] == {
        "invalid_code": "external_text_read_failed",
        "state": "present_invalid",
        "utf8_hex": None,
        "utf8_sha256": None,
    }
    assert sha256_bytes(b"") not in (attempt.sdk_projection_bytes or b"").decode()
    assert sentinel.encode() not in (attempt.sdk_projection_bytes or b"")


def test_anthropic_http_status_getter_failure_retains_exact_body() -> None:
    sentinel = "status-getter-secret-sentinel"
    raw = b"\xffcaptured-http-error"

    class RaisingStatusError(_AnthropicStatusError):
        @property
        def status_code(self):
            raise RuntimeError(sentinel)

    error = RaisingStatusError("status-secret-sentinel")
    error.response = SimpleNamespace(content=raw)  # type: ignore[attr-defined]

    def create(**_kwargs):
        raise error

    attempt = _anthropic_invoke_adapter(create).invoke(_anthropic_request())

    assert attempt.facts.transport_kind == "http_error"
    assert attempt.facts.http_status.state == "present_invalid"
    assert attempt.facts.http_status.invalid_code == "http_status_read_failed"
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.facts.envelope.raw_size_bytes == len(raw)
    assert attempt.facts.envelope.raw_sha256 == sha256_bytes(raw)
    assert attempt.raw_transport_response == raw
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is False
    assert sentinel.encode() not in (attempt.sdk_projection_bytes or b"")


@pytest.mark.parametrize("status_mode", ["valid", "raising"])
def test_anthropic_http_projection_failure_retains_exact_body(
    status_mode: str,
) -> None:
    sentinel = f"http-projector-secret-{status_mode}"
    raw = b"\xffcaptured-http-projector"

    if status_mode == "raising":

        class StatusError(_AnthropicStatusError):
            @property
            def status_code(self):
                raise RuntimeError(sentinel)

        error = StatusError("status-secret-sentinel")
    else:
        error = _AnthropicStatusError("status-secret-sentinel")
        error.status_code = 503  # type: ignore[attr-defined]
    error.response = SimpleNamespace(content=raw)  # type: ignore[attr-defined]

    def create(**_kwargs):
        raise error

    adapter = _anthropic_invoke_adapter(create)

    def fail_projection(**_kwargs):
        raise RuntimeError(sentinel)

    adapter._attempt_from_response = fail_projection
    attempt = adapter.invoke(_anthropic_request())

    assert attempt.facts.transport_kind == "http_error"
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.facts.envelope.raw_size_bytes == len(raw)
    assert attempt.facts.envelope.raw_sha256 == sha256_bytes(raw)
    assert attempt.raw_transport_response == raw
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is False
    if status_mode == "raising":
        assert attempt.facts.http_status.state == "present_invalid"
        assert attempt.facts.http_status.invalid_code == "http_status_read_failed"
    else:
        assert attempt.facts.http_status.state == "present_valid"
        assert attempt.facts.http_status.value == 503
    assert sentinel.encode() not in (attempt.sdk_projection_bytes or b"")


@pytest.mark.parametrize(
    ("status_code", "expected_reason", "expected_retry"),
    [
        (408, "provider_retryable_failure", True),
        (409, "provider_retryable_failure", True),
        (429, "provider_retryable_failure", True),
        (500, "provider_retryable_failure", True),
        (599, "provider_retryable_failure", True),
        (400, "provider_failed", False),
        (404, "provider_failed", False),
    ],
)
def test_anthropic_http_absent_body_preserves_retry_table(
    status_code: int,
    expected_reason: str,
    expected_retry: bool,
) -> None:
    error = _AnthropicStatusError("status-secret-sentinel")
    error.status_code = status_code  # type: ignore[attr-defined]
    error.response = None  # type: ignore[attr-defined]

    def create(**_kwargs):
        raise error

    attempt = _anthropic_invoke_adapter(create).invoke(_anthropic_request())

    assert attempt.facts.transport_kind == "http_error"
    assert attempt.facts.http_status.state == "present_valid"
    assert attempt.facts.http_status.value == status_code
    assert attempt.facts.envelope.state == "absent"
    assert attempt.facts.envelope.raw_sha256 is None
    assert attempt.raw_transport_response is None
    assert attempt.outcome.shadow_reason == expected_reason
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is expected_retry
    sdk_projection = json.loads(attempt.sdk_projection_bytes or b"")
    assert sdk_projection["body_state"] == "absent"


def test_anthropic_http_status_without_body_does_not_fabricate_raw() -> None:
    error = _AnthropicStatusError("status-secret-sentinel")
    error.status_code = 503  # type: ignore[attr-defined]
    error.response = None  # type: ignore[attr-defined]

    def create(**_kwargs):
        raise error

    attempt = _anthropic_invoke_adapter(create).invoke(_anthropic_request())

    assert attempt.facts.transport_kind == "http_error"
    assert attempt.facts.http_status.state == "present_valid"
    assert attempt.facts.http_status.value == 503
    assert attempt.facts.envelope.state == "absent"
    assert attempt.facts.envelope.raw_sha256 is None
    assert attempt.raw_transport_response is None
    assert attempt.outcome.output_eligible is False
    assert attempt.outcome.retry_eligible is True
    assert attempt.outcome.shadow_reason == "provider_retryable_failure"


def test_anthropic_adapter_error_is_value_free_and_never_calls_fallback() -> None:
    sentinel = "secret-like-provider-diagnostic"

    class TimeoutError(Exception):
        pass

    class ConnectionError(Exception):
        pass

    class StatusError(Exception):
        pass

    def explode(**_kwargs):
        raise RuntimeError(sentinel)

    adapter = object.__new__(AnthropicMessagesAdapterV1)
    adapter._client = SimpleNamespace(
        messages=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=explode),
        )
    )
    adapter._anthropic = SimpleNamespace(
        APITimeoutError=TimeoutError,
        APIConnectionError=ConnectionError,
        APIStatusError=StatusError,
    )
    attempt = adapter.invoke(_anthropic_request())
    assert attempt.outcome.shadow_reason == "provider_failed"
    assert attempt.raw_transport_response is None
    assert sentinel.encode() not in (attempt.sdk_projection_bytes or b"")


def test_anthropic_adapter_rejects_noncanonical_request_before_provider() -> None:
    calls = 0

    def create(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("noncanonical request reached provider")

    adapter = object.__new__(AnthropicMessagesAdapterV1)
    adapter._client = SimpleNamespace(
        messages=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=create),
        )
    )
    request = replace(_anthropic_request(), temperature=0.0)
    attempt = adapter.invoke(request)
    assert calls == 0
    assert attempt.outcome.shadow_reason == "provider_failed"


def test_anthropic_constructor_and_wire_profile_are_fixed(monkeypatch) -> None:
    from multi_agent_brief.semantic_evaluator.adapters import anthropic_messages

    constructor: dict[str, object] = {}
    request_arguments: dict[str, object] = {}
    raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
    )
    sdk_response = SimpleNamespace(
        id="msg-public",
        model=ANTHROPIC_TEST_MODEL,
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text='{"findings":[]}')],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    raw_response = SimpleNamespace(
        http_response=SimpleNamespace(content=raw),
        parse=lambda: sdk_response,
    )

    def create(**kwargs):
        request_arguments.update(kwargs)
        return raw_response

    client = SimpleNamespace(
        messages=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=create),
        )
    )

    def make_client(**kwargs):
        constructor.update(kwargs)
        return client

    class TimeoutError(Exception):
        pass

    class ConnectionError(Exception):
        pass

    class StatusError(Exception):
        pass

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://generic-env.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "generic-key-must-not-be-read")
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(
            Anthropic=make_client,
            APITimeoutError=TimeoutError,
            APIConnectionError=ConnectionError,
            APIStatusError=StatusError,
        ),
    )
    monkeypatch.setattr(anthropic_messages.metadata, "version", lambda _name: "0.104.1")
    adapter = AnthropicMessagesAdapterV1(
        api_key="test-key",
        endpoint=ANTHROPIC_TEST_ENDPOINT,
    )
    attempt = adapter.invoke(_anthropic_request())

    assert constructor == {
        "api_key": "test-key",
        "base_url": ANTHROPIC_TEST_ENDPOINT,
        "max_retries": 0,
    }
    assert request_arguments == {
        "model": ANTHROPIC_TEST_MODEL,
        "max_tokens": 100,
        "system": "system",
        "messages": [{"role": "user", "content": "user"}],
        "timeout": 60,
    }
    assert attempt.outcome.attempt_status == "completed"
    assert "temperature" not in request_arguments
    assert "top_p" not in request_arguments
    assert "thinking" not in request_arguments
    assert "output_config" not in request_arguments
    assert "tools" not in request_arguments
    assert ANTHROPIC_ENDPOINT_SETTING == "BRIEFLOOP_LAJ_MESSAGES_ENDPOINT"
    assert ANTHROPIC_API_KEY_SETTING == "BRIEFLOOP_LAJ_MESSAGES_API_KEY"


def test_anthropic_prompt_sizer_is_strict_local_upper_bound() -> None:
    sizer = AnthropicUtf8BytePromptSizerV1()
    assert sizer.count_tokens(system_text="A", user_text="中文") == 15
    with pytest.raises(Exception) as exc_info:
        sizer.count_tokens(system_text="bad\ud800", user_text="ok")
    assert getattr(exc_info.value, "reason_code", None) == "prompt_sizer_unavailable"


@pytest.mark.parametrize("version", ["0.103.9", "0.105.0", "", None, True])
def test_anthropic_sdk_version_is_frozen_to_0104(version: object) -> None:
    assert is_supported_anthropic_sdk_version_v1(version) is False
    assert is_supported_anthropic_sdk_version_v1("0.104.1") is True


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.anthropic.com",
        ANTHROPIC_TEST_ENDPOINT,
        "https://messages.example.test:8443/api",
        "https://localhost:8443/v1",
        "https://127.0.0.1:8443/v1",
        "https://[2001:db8::1]:8443/v1",
    ],
)
def test_messages_endpoint_accepts_only_exact_canonical_https(
    endpoint: str,
) -> None:
    assert canonical_messages_endpoint_v1(endpoint) == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        None,
        "",
        "http://messages.example.test",
        "https://user@messages.example.test",
        "https://messages.example.test?x=1",
        "https://messages.example.test#fragment",
        "https://messages.example.test/.",
        "https://messages.example.test/a/../b",
        "https://messages.example.test//v1",
        "https://messages.example.test/%76%31",
        "https:\\messages.example.test",
        "https://MESSAGES.example.test",
        "https://messages.example.test/",
        "https://messages.example.test:443",
        "https:// ",
        "https:// /v1",
        "https://messages.example.test/\tbad",
        "https://example..test",
        "https://-example.test",
        "https://example-.test",
        "https://127.000.0.1",
        "https://127.1",
        "https://[2001:0db8::1]",
        "https://[fe80::1%25en0]",
    ],
)
def test_messages_endpoint_rejects_ambiguous_or_noncanonical_forms(
    endpoint: object,
) -> None:
    with pytest.raises(TypeError) as caught:
        canonical_messages_endpoint_v1(endpoint)
    assert str(caught.value) == "shadow_request_invalid"


def test_se2r_02_openai_status_error_body_cannot_complete() -> None:
    raw = synthetic_openai_response_bytes_v4(
        status="completed",
        response_id="resp-public",
        model=EXPECTED_MODEL.decode(),
        output_text='{"findings":[]}',
    )
    attempt = object.__new__(OpenAIResponsesAdapterV4)._attempt_from_response(
        request=_openai_request(),
        raw=raw,
        sdk_response=None,
        transport_kind="http_error",
    )
    assert attempt.outcome.shadow_reason == "provider_failed"
    assert attempt.outcome.retry_eligible is False
    assert attempt.extracted_output is None


def test_se2r_04_openai_sdk_parse_failure_cannot_fall_back_to_raw_success() -> None:
    from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
        _SDK_READ_FAILED,
    )

    raw = synthetic_openai_response_bytes_v4(
        status="completed",
        response_id="resp-public",
        model=EXPECTED_MODEL.decode(),
        output_text='{"findings":[]}',
    )
    attempt = object.__new__(OpenAIResponsesAdapterV4)._attempt_from_response(
        request=_openai_request(), raw=raw, sdk_response=_SDK_READ_FAILED
    )
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.extracted_output is None


def test_se2r_04_completed_rejects_unknown_output_item_type() -> None:
    raw = (
        b'{"id":"resp-public","model":"gpt-test-2026-07-18","output":['
        b'{"content":[{"text":"ignored","type":"output_text"}],"type":"future_item"},'
        b'{"content":[{"text":"accepted","type":"output_text"}],"type":"message"}'
        b'],"status":"completed"}'
    )
    projection = project_openai_response_bytes_v4(raw)
    assert projection.output.state == "present_invalid"
    assert projection.output.invalid_code == "external_text_unknown"


def test_cliproxy_completed_accepts_known_reasoning_item_before_message() -> None:
    raw = (
        b'{"id":"resp-public","model":"gpt-test-2026-07-18","output":['
        b'{"id":"reasoning-public","summary":[],"type":"reasoning"},'
        b'{"content":[{"text":"{\\"findings\\":[]}","type":"output_text"}],'
        b'"type":"message"}],"status":"completed"}'
    )
    projection = project_openai_response_bytes_v4(raw)
    assert projection.envelope_valid is True
    assert projection.output.utf8_bytes == b'{"findings":[]}'


@pytest.mark.parametrize(
    ("raw", "status_code", "expected_reason", "retry_eligible"),
    [
        (b"", 429, "provider_boundary_invalid", False),
        (
            synthetic_openai_response_bytes_v4(
                status="completed",
                response_id="resp-public",
                model=EXPECTED_MODEL.decode(),
                output_text='{"findings":[]}',
            ),
            500,
            "provider_failed",
            False,
        ),
    ],
)
def test_se2r_03_status_error_body_presence_controls_retry_without_output(
    raw: bytes,
    status_code: int,
    expected_reason: str,
    retry_eligible: bool,
) -> None:
    class FakeStatusError(Exception):
        pass

    error = FakeStatusError()
    error.status_code = status_code  # type: ignore[attr-defined]
    error.response = SimpleNamespace(content=raw)  # type: ignore[attr-defined]

    class Create:
        def create(self, **_kwargs):
            raise error

    adapter = object.__new__(OpenAIResponsesAdapterV4)
    adapter._openai = SimpleNamespace(
        APITimeoutError=type("FakeTimeout", (Exception,), {}),
        APIConnectionError=type("FakeConnection", (Exception,), {}),
        APIStatusError=FakeStatusError,
    )
    adapter._client = SimpleNamespace(
        responses=SimpleNamespace(with_raw_response=Create())
    )
    attempt = adapter.invoke(_openai_request())
    assert attempt.outcome.shadow_reason == expected_reason
    assert attempt.outcome.retry_eligible is retry_eligible
    assert attempt.extracted_output is None
    assert attempt.facts.envelope.state != "absent"


@pytest.mark.parametrize("content", [None, "", 500, object()])
def test_se2r_03_status_error_unreadable_body_is_terminal(content: object) -> None:
    class FakeStatusError(Exception):
        pass

    error = FakeStatusError()
    error.status_code = 500  # type: ignore[attr-defined]
    error.response = SimpleNamespace(content=content)  # type: ignore[attr-defined]

    class Create:
        def create(self, **_kwargs):
            raise error

    adapter = object.__new__(OpenAIResponsesAdapterV4)
    adapter._openai = SimpleNamespace(
        APITimeoutError=type("FakeTimeout", (Exception,), {}),
        APIConnectionError=type("FakeConnection", (Exception,), {}),
        APIStatusError=FakeStatusError,
    )
    adapter._client = SimpleNamespace(
        responses=SimpleNamespace(with_raw_response=Create())
    )
    attempt = adapter.invoke(_openai_request())
    assert attempt.facts.envelope.state == "present_invalid"
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.outcome.retry_eligible is False


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_se2r_04_nonfinite_json_constants_are_invalid(constant: bytes) -> None:
    openai_raw = (
        b'{"id":"resp-public","ignored":'
        + constant
        + b',"model":"gpt-test-2026-07-18","output":['
        b'{"content":[{"text":"ok","type":"output_text"}],"type":"message"}],'
        b'"status":"completed"}'
    )
    synthetic_raw = (
        b'{"id":"resp-public","ignored":'
        + constant
        + b',"model":"synthetic-fixture-v4","output_text":"{}",'
        b'"provider":"synthetic_fixture","status":"completed"}'
    )
    openai_projection = project_openai_response_bytes_v4(openai_raw)
    synthetic_projection = project_synthetic_response_bytes_v4(synthetic_raw)
    assert openai_projection.envelope_invalid_code == "envelope_json_invalid"
    assert synthetic_projection.envelope_invalid_code == "envelope_json_invalid"


def test_se2r_04_openai_raw_duplicate_status_is_terminal() -> None:
    raw = (
        b'{"id":"resp-public","model":"gpt-test-2026-07-18",'
        b'"output":[],"status":"completed","status":"incomplete"}'
    )
    projection = project_openai_response_bytes_v4(raw)
    assert projection.envelope_valid is False
    assert projection.envelope_invalid_code == "envelope_duplicate_member"


def test_se2r_07_openai_output_surrogate_escape_is_terminal() -> None:
    raw = (
        b'{"id":"resp-public","model":"gpt-test-2026-07-18",'
        b'"output":[{"content":[{"text":"\\ud800","type":"output_text"}]}],'
        b'"status":"completed"}'
    )
    attempt = object.__new__(OpenAIResponsesAdapterV4)._attempt_from_response(
        request=_openai_request(), raw=raw, sdk_response=None
    )
    assert attempt.outcome.shadow_reason == "provider_boundary_invalid"
    assert attempt.extracted_output is None


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
