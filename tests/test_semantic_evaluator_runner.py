"""Focused runner rows for MU-LAJ-1's v4 provider boundary."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

from multi_agent_brief.semantic_evaluator.adapter import (
    ExternalTextObservation,
    RawProviderAttemptV4,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
)
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_API_KEY_SETTING,
    ANTHROPIC_ENDPOINT_SETTING,
    ANTHROPIC_PROVIDER_ID,
    AnthropicMessagesAdapterV1,
    synthetic_anthropic_message_bytes_v1,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    SYNTHETIC_PROVIDER_ID,
    SyntheticFixtureAdapterV4,
    project_synthetic_response_bytes_v4,
    _rubric_from_prompt,
)
from multi_agent_brief.semantic_evaluator.contracts import DIMENSION_RESPONSE_SCHEMA_ID
from multi_agent_brief.semantic_evaluator.prompt_sizer import (
    ANTHROPIC_PROMPT_SIZER_ID,
    ANTHROPIC_PROMPT_SIZER_VERSION,
)
from multi_agent_brief.semantic_evaluator.runner import (
    PROFILE_ID,
    run_shadow,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    sha256_bytes,
)


FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"
FIXED_TIME = "2026-07-18T00:00:00Z"
ANTHROPIC_TEST_MODEL = "public-nonclaude-model-v1"
ANTHROPIC_TEST_ENDPOINT = "https://messages.example.test/v1"


def _invocation(tmp_path: Path, *, max_attempts: int = 1) -> dict[str, object]:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name in ("report.md", "bounded_context.json", "instrument.json"):
        shutil.copyfile(FIXTURES / name, inputs / name)
    instrument = inputs / "instrument.json"
    payload = json.loads(instrument.read_text(encoding="utf-8"))
    payload["retry_policy"] = {
        "max_attempts": max_attempts,
        "retryable_reason_codes": (
            ["provider_retryable_failure"] if max_attempts > 1 else []
        ),
        "backoff_schedule_ms": [17] * (max_attempts - 1),
    }
    instrument.write_bytes(canonical_json_bytes(payload))
    return {
        "report": inputs / "report.md",
        "bounded_context": inputs / "bounded_context.json",
        "profile": PROFILE_ID,
        "instrument": instrument,
        "trial_id": "trial-runner-v4",
        "archive_root": (tmp_path / "archives").resolve(),
        "clock": lambda: FIXED_TIME,
    }


def _anthropic_invocation(
    tmp_path: Path,
    monkeypatch,
    *,
    endpoint: str = ANTHROPIC_TEST_ENDPOINT,
    max_attempts: int = 1,
) -> dict[str, object]:
    monkeypatch.setenv(ANTHROPIC_ENDPOINT_SETTING, endpoint)
    invocation = _invocation(tmp_path, max_attempts=max_attempts)
    instrument = Path(invocation["instrument"])
    payload = json.loads(instrument.read_text())
    payload.update(
        {
            "instrument_config_id": "anthropic-shadow-instrument-v1",
            "provider_id": ANTHROPIC_PROVIDER_ID,
            "model_id": ANTHROPIC_TEST_MODEL,
            "model_version": ANTHROPIC_TEST_MODEL,
            "decoding": {
                "max_output_tokens": 4096,
                "seed": None,
                "temperature": 1.0,
                "top_p": 1.0,
            },
            "prompt_sizer": {
                "max_context_tokens": 200000,
                "reserved_output_tokens": 4096,
                "sizer_id": ANTHROPIC_PROMPT_SIZER_ID,
                "sizer_version": ANTHROPIC_PROMPT_SIZER_VERSION,
            },
        }
    )
    instrument.write_bytes(canonical_json_bytes(payload))
    invocation["trial_id"] = "trial-anthropic-runner-v1"
    return invocation


def _absent_text():
    return capture_external_text_v4((ExternalTextObservation(False),))


def _retryable_attempt(request) -> RawProviderAttemptV4:
    absent = _absent_text()
    provider = capture_external_text_v4(
        (
            ExternalTextObservation(True, request.provider_id),
            ExternalTextObservation(True, SYNTHETIC_PROVIDER_ID),
        )
    )
    facts = make_provider_boundary_facts_v4(
        envelope=capture_response_envelope_v4(None, present=False),
        status=absent,
        response_id=absent,
        provider_identity=provider,
        model_identity=absent,
        output=absent,
        http_status=capture_http_status_v4(None, present=False),
        transport_kind="timeout",
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
    )


def _incomplete_attempt(request) -> RawProviderAttemptV4:
    completed = SyntheticFixtureAdapterV4().invoke(request)
    payload = json.loads(completed.raw_transport_response or b"{}")
    payload["status"] = "incomplete"
    raw = canonical_json_bytes(payload)
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
        request_projection_bytes=request.projection_bytes(),
        raw_transport_response=raw,
        extracted_output=None,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )


class _ScriptedAdapter:
    def __init__(self, execution, mode: str) -> None:
        self.adapter_id = execution.adapter_id
        self.adapter_version = execution.adapter_version
        self.provider_sdk_name = execution.provider_sdk_name
        self.provider_sdk_version = execution.provider_sdk_version
        self.qualification_eligible = execution.qualification_eligible
        self.mode = mode
        self.calls: list[tuple[str, int]] = []
        self.delegate = SyntheticFixtureAdapterV4()

    def invoke(self, request):
        self.calls.append((request.dimension_id, request.attempt_ordinal))
        if self.mode == "retry_then_success" and len(self.calls) == 1:
            return _retryable_attempt(request)
        if self.mode == "incomplete" and len(self.calls) == 1:
            return _incomplete_attempt(request)
        if self.mode == "raise" and len(self.calls) == 1:
            raise RuntimeError("private provider detail")
        return self.delegate.invoke(request)


def _factory(mode: str, captures: list[_ScriptedAdapter]):
    def create(execution):
        adapter = _ScriptedAdapter(execution, mode)
        captures.append(adapter)
        return adapter

    return create


class _AnthropicFixtureAdapter:
    def __init__(self, execution, *, endpoint: str = ANTHROPIC_TEST_ENDPOINT) -> None:
        self.adapter_id = execution.adapter_id
        self.adapter_version = execution.adapter_version
        self.provider_sdk_name = execution.provider_sdk_name
        self.provider_sdk_version = execution.provider_sdk_version
        self.qualification_eligible = execution.qualification_eligible
        self.base_url = endpoint
        self._delegate = object.__new__(AnthropicMessagesAdapterV1)
        self.calls = 0

    def invoke(self, request):
        self.calls += 1
        rubric = _rubric_from_prompt(request.user_text)
        output = canonical_json_bytes(
            {
                "dimension_id": request.dimension_id,
                "schema_version": DIMENSION_RESPONSE_SCHEMA_ID,
                "trial_id": request.trial_id,
                "unit_results": [
                    {
                        "assessment_unit_id": item["assessment_unit_id"],
                        "disposition": "no_finding",
                    }
                    for item in rubric["assessment_units"]
                ],
            }
        )
        raw = synthetic_anthropic_message_bytes_v1(
            stop_reason="end_turn",
            response_id=f"msg-public-{self.calls}",
            model=request.expected_model_version,
            content=[
                {
                    "type": "thinking",
                    "thinking": "synthetic non-output reasoning",
                    "signature": "",
                },
                {"type": "text", "text": output.decode("utf-8")},
            ],
        )
        return self._delegate._attempt_from_response(
            request=request,
            raw=raw,
            sdk_response=None,
        )


def test_anthropic_run_is_distinct_and_replay_precedes_key_sdk_and_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    monkeypatch.setattr(runner.metadata, "version", lambda _name: "0.104.1")
    captured: list[_AnthropicFixtureAdapter] = []

    def factory(execution):
        adapter = _AnthropicFixtureAdapter(execution)
        captured.append(adapter)
        return adapter

    invocation = _anthropic_invocation(tmp_path, monkeypatch)
    result = run_shadow(**invocation, adapter_factory=factory)
    assert result.ok is True
    assert result.execution_origin == "messages_endpoint"
    assert result.qualification_class == "messages_compatible_experimental"
    assert result.qualification_eligible is False
    assert captured[0].calls == 9
    archive = Path(result.archive_path or "")
    execution = json.loads((archive / "execution_manifest.json").read_bytes())
    assert execution["provider_endpoint_sha256"] == sha256_bytes(
        ANTHROPIC_TEST_ENDPOINT.encode("ascii")
    )
    assert (
        b"synthetic non-output reasoning"
        in next(archive.glob("attempts/*/1/response.body")).read_bytes()
    )
    assert (
        b"synthetic non-output reasoning"
        not in next(archive.glob("attempts/*/1/output.txt")).read_bytes()
    )

    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "generic-key-must-not-be-read")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://generic-env.invalid")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    metadata_calls = 0

    def forbidden_metadata(_name):
        nonlocal metadata_calls
        metadata_calls += 1
        raise AssertionError("replay touched distribution metadata")

    monkeypatch.setattr(runner.metadata, "version", forbidden_metadata)
    monkeypatch.setattr(
        runner,
        "prepare_archive_root",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("replay touched archive preparation")
        ),
    )
    replay = run_shadow(
        **invocation,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("replay touched Anthropic adapter")
        ),
    )
    assert replay.ok is True
    assert replay.replayed is True
    assert replay.receipt_id == result.receipt_id
    assert metadata_calls == 0


def test_se2r_02_incomplete_is_terminal_no_retry_no_output_or_advice(
    tmp_path: Path,
) -> None:
    captures: list[_ScriptedAdapter] = []
    sleeps: list[float] = []
    result = run_shadow(
        **_invocation(tmp_path, max_attempts=2),
        sleep=sleeps.append,
        adapter_factory=_factory("incomplete", captures),
    )
    assert result.ok is False
    assert result.archive_complete is True
    assert sleeps == []
    assert len(captures[0].calls) == 9
    archive = Path(result.archive_path or "")
    first = next(
        item
        for item in archive.glob("attempts/*/1/transport.json")
        if json.loads(item.read_bytes())["shadow_reason"] == "provider_incomplete"
    )
    prefix = first.parent
    record = json.loads(first.read_bytes())
    assert record["retry_eligible"] is False
    assert record["output_eligible"] is False
    assert not (prefix / "output.txt").exists()
    assert json.loads((archive / "run.json").read_bytes())["findings"] == []
    presentation = json.loads((archive / "presentation_actual.json").read_bytes())
    assert presentation["additional_semantic_findings"] == []
    assert presentation["finding_count"] == 0
    assert presentation["withheld_finding_count"] == 0
