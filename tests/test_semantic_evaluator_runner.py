"""Focused runner rows for MU-LAJ-1's v4 provider boundary."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

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
    ANTHROPIC_ADAPTER_ID,
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
from multi_agent_brief.semantic_evaluator.adapters.local_proxy_responses import (
    CLIPROXY_PROVIDER_ID,
    CLIProxyResponsesAdapterV1,
)
from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
    synthetic_openai_response_bytes_v4,
)
from multi_agent_brief.semantic_evaluator.contracts import DIMENSION_RESPONSE_SCHEMA_ID
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.archive import (
    resolve_existing_execution_evidence,
)
from multi_agent_brief.semantic_evaluator.prompt_sizer import (
    ANTHROPIC_PROMPT_SIZER_ID,
    ANTHROPIC_PROMPT_SIZER_VERSION,
    CLIPROXY_PROMPT_SIZER_ID,
    CLIPROXY_PROMPT_SIZER_VERSION,
)
from multi_agent_brief.semantic_evaluator.runner import (
    PROFILE_ID,
    PreparedShadowRun,
    derive_existing_execution_evidence,
    execute_prepared_shadow_run,
    prepare_shadow_run,
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


def _cliproxy_invocation(tmp_path: Path) -> dict[str, object]:
    invocation = _invocation(tmp_path)
    instrument = Path(invocation["instrument"])
    payload = json.loads(instrument.read_bytes())
    payload.update(
        {
            "instrument_config_id": "local-proxy-shadow-instrument-v1",
            "provider_id": CLIPROXY_PROVIDER_ID,
            "model_id": "gpt-5.6-sol",
            "model_version": "gpt-5.6-sol",
            "prompt_sizer": {
                "max_context_tokens": 200000,
                "reserved_output_tokens": 4096,
                "sizer_id": CLIPROXY_PROMPT_SIZER_ID,
                "sizer_version": CLIPROXY_PROMPT_SIZER_VERSION,
            },
        }
    )
    instrument.write_bytes(canonical_json_bytes(payload))
    invocation["trial_id"] = "trial-local-proxy-runner-v1"
    return invocation


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
    payload = json.loads(instrument.read_bytes())
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


class _CLIProxyFixtureAdapter:
    def __init__(self, execution) -> None:
        self.adapter_id = execution.adapter_id
        self.adapter_version = execution.adapter_version
        self.provider_sdk_name = execution.provider_sdk_name
        self.provider_sdk_version = execution.provider_sdk_version
        self.qualification_eligible = execution.qualification_eligible
        self._delegate = object.__new__(CLIProxyResponsesAdapterV1)
        self.calls = 0

    def invoke(self, request):
        self.calls += 1
        rubric = _rubric_from_prompt(request.user_text)
        units = rubric["assessment_units"]
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
                    for item in units
                ],
            }
        )
        raw = synthetic_openai_response_bytes_v4(
            status="completed",
            response_id=f"resp-public-{self.calls}",
            model=request.expected_model_version,
            output_text=output.decode("utf-8"),
        )
        return self._delegate._attempt_from_response(
            request=request,
            raw=raw,
            sdk_response=None,
        )


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


class _AnthropicRaisingAdapter(_AnthropicFixtureAdapter):
    def invoke(self, request):
        self.calls += 1
        raise RuntimeError("private transport detail")


def test_se2r_01_synthetic_run_preserves_exact_25_unit_accounting(
    tmp_path: Path,
) -> None:
    result = run_shadow(**_invocation(tmp_path), sleep=lambda _seconds: None)
    assert result.ok is True
    assert result.archive_complete is True
    assert result.validation_status == "accepted"
    archive = Path(result.archive_path or "")
    assessment_plan = json.loads((archive / "assessment_plan.json").read_bytes())
    run = json.loads((archive / "run.json").read_bytes())
    assert len(assessment_plan["units"]) == 25
    assert len(run["assessment_units"]) == 25
    assert {item["disposition"] for item in run["assessment_units"]} == {"no_finding"}
    assert len(list((archive / "attempts").glob("*/*/transport.json"))) == 9
    assert (
        archive.parent.parent.parent.parent
        / "executions"
        / "trial-runner-v4"
        / "COMPLETE"
    ).is_file()


def test_provider_evidence_survives_local_derivation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A composition failure must not erase provider request/response evidence."""

    from multi_agent_brief.semantic_evaluator import runner

    invocation = _invocation(tmp_path)
    original_composition = runner.compose_matched_non_llm

    def fail_composition(**_kwargs):
        raise SemanticEvaluatorError("composition_record_mismatch")

    monkeypatch.setattr(runner, "compose_matched_non_llm", fail_composition)
    result = run_shadow(**invocation, sleep=lambda _seconds: None)

    assert result.ok is False
    assert result.reason_codes == ("composition_record_mismatch",)
    evidence = Path(result.execution_archive_path or "")
    assert evidence.is_dir()
    assert (evidence / "COMPLETE").is_file()
    manifest = json.loads((evidence / "evidence_manifest.json").read_bytes())
    assert manifest["status"] == "complete"
    assert manifest["payload_file_count"] > 0
    assert (evidence / "attempts").is_dir()
    assert list(evidence.glob("attempts/*/*/response.body"))
    reopened = resolve_existing_execution_evidence(
        archive_root=Path(result.execution_archive_path).parents[1],
        trial_id="trial-runner-v4",
    )
    assert reopened is not None
    assert "laj_composition_witness.json" in reopened.payloads
    assert not result.archive_complete
    monkeypatch.setattr(runner, "compose_matched_non_llm", original_composition)
    derived = derive_existing_execution_evidence(
        archive_root=Path(invocation["archive_root"]),
        trial_id="trial-runner-v4",
    )
    assert derived.ok is True
    assert derived.archive_complete is True


def test_provider_evidence_survives_run_assembly_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    monkeypatch.setattr(
        runner,
        "assemble_semantic_assessment_run",
        lambda **_kwargs: (_ for _ in ()).throw(
            SemanticEvaluatorError("composition_witness_mismatch")
        ),
    )
    result = run_shadow(**_invocation(tmp_path), sleep=lambda _seconds: None)
    assert result.reason_codes == ("composition_witness_mismatch",)
    evidence = Path(result.execution_archive_path or "")
    assert evidence.is_dir()
    assert json.loads((evidence / "evidence_manifest.json").read_bytes())["status"] == (
        "local_derivation_failed"
    )
    assert list(evidence.glob("attempts/*/*/response.body"))


def test_cliproxy_run_is_distinct_nonqualifying_and_replay_is_credential_free(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    monkeypatch.setattr(runner.metadata, "version", lambda _name: "2.46.0")
    captured: list[_CLIProxyFixtureAdapter] = []

    def factory(execution):
        adapter = _CLIProxyFixtureAdapter(execution)
        captured.append(adapter)
        return adapter

    invocation = _cliproxy_invocation(tmp_path)
    result = run_shadow(**invocation, adapter_factory=factory)
    assert result.ok is True
    assert result.execution_origin == "local_cliproxy"
    assert result.qualification_class == "local_proxy_experimental"
    assert result.qualification_eligible is False
    assert captured[0].calls == 9
    archive = Path(result.archive_path or "")
    receipt = json.loads((archive / "receipt.json").read_bytes())
    assert receipt["execution_origin"] == "local_cliproxy"
    assert receipt["qualification_class"] == "local_proxy_experimental"
    monkeypatch.delenv("CLIPROXY_API_KEY", raising=False)
    replay = run_shadow(
        **invocation,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("replay touched CLIProxy adapter")
        ),
    )
    assert replay.ok is True
    assert replay.replayed is True
    assert replay.receipt_id == result.receipt_id


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


def test_anthropic_generic_key_never_substitutes_for_dedicated_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner
    from multi_agent_brief.semantic_evaluator.adapters import anthropic_messages

    monkeypatch.setattr(runner.metadata, "version", lambda _name: "0.104.1")
    invocation = _anthropic_invocation(tmp_path, monkeypatch)
    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "generic-key-must-not-be-read")
    constructor_calls = 0

    class ForbiddenAdapter:
        def __init__(self, **_kwargs):
            nonlocal constructor_calls
            constructor_calls += 1

    monkeypatch.setattr(
        anthropic_messages,
        "AnthropicMessagesAdapterV1",
        ForbiddenAdapter,
    )
    result = run_shadow(**invocation)
    assert result.reason_codes == ("shadow_adapter_unavailable",)
    assert constructor_calls == 0


def test_execution_failure_is_not_misreported_as_archive_publish_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A pre-publication execution exception keeps its own stable boundary."""

    from multi_agent_brief.semantic_evaluator import runner

    invocation = _invocation(tmp_path)
    prepared = prepare_shadow_run(
        report=invocation["report"],
        bounded_context=invocation["bounded_context"],
        profile=invocation["profile"],
        instrument=invocation["instrument"],
        trial_id=invocation["trial_id"],
        archive_root=invocation["archive_root"],
    )
    assert isinstance(prepared, PreparedShadowRun)
    monkeypatch.setattr(
        runner,
        "_execute_dimensions",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )

    result = execute_prepared_shadow_run(
        prepared,
        adapter_factory=lambda _execution: SyntheticFixtureAdapterV4(),
    )

    assert result.archive_complete is False
    assert result.reason_codes == ("shadow_execution_failed",)


def test_anthropic_generic_endpoint_never_substitutes_for_dedicated_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    invocation = _anthropic_invocation(tmp_path, monkeypatch)
    monkeypatch.delenv(ANTHROPIC_ENDPOINT_SETTING, raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://generic-env.invalid")
    metadata_calls: list[str] = []
    monkeypatch.setattr(
        runner.metadata,
        "version",
        lambda name: metadata_calls.append(name),
    )
    result = run_shadow(**invocation)
    assert result.reason_codes == ("shadow_request_invalid",)
    assert metadata_calls == []
    assert not Path(invocation["archive_root"]).exists()


def test_anthropic_replay_only_miss_precedes_distribution_and_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    invocation = _anthropic_invocation(tmp_path, monkeypatch)
    prepared = prepare_shadow_run(
        **{
            key: value
            for key, value in invocation.items()
            if key not in {"clock", "sleep"}
        }
    )
    assert isinstance(prepared, PreparedShadowRun)
    metadata_calls = 0

    def forbidden_metadata(_name):
        nonlocal metadata_calls
        metadata_calls += 1
        raise AssertionError("replay-only miss touched distribution metadata")

    monkeypatch.setattr(runner.metadata, "version", forbidden_metadata)
    result = execute_prepared_shadow_run(
        prepared,
        replay_only=True,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("replay-only miss touched adapter")
        ),
    )
    assert result.reason_codes == ("shadow_archive_incomplete",)
    assert metadata_calls == 0
    assert not Path(invocation["archive_root"]).exists()


def test_anthropic_first_party_endpoint_is_explicit_and_retained_after_prepare(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    first_party = "https://api.anthropic.com"
    monkeypatch.setattr(runner.metadata, "version", lambda _name: "0.104.1")
    invocation = _anthropic_invocation(
        tmp_path,
        monkeypatch,
        endpoint=first_party,
    )
    endpoint_reads = 0
    real_endpoint_reader = runner._messages_endpoint_for

    def counted_endpoint_reader(adapter_id):
        nonlocal endpoint_reads
        endpoint_reads += 1
        return real_endpoint_reader(adapter_id)

    monkeypatch.setattr(runner, "_messages_endpoint_for", counted_endpoint_reader)
    prepared = prepare_shadow_run(
        **{
            key: value
            for key, value in invocation.items()
            if key not in {"clock", "sleep"}
        }
    )
    assert isinstance(prepared, PreparedShadowRun)
    assert endpoint_reads == 1
    assert prepared.messages_endpoint == first_party
    monkeypatch.setenv(
        ANTHROPIC_ENDPOINT_SETTING,
        "https://changed-after-prepare.example.test",
    )
    result = execute_prepared_shadow_run(
        prepared,
        adapter_factory=lambda execution: _AnthropicFixtureAdapter(
            execution,
            endpoint=first_party,
        ),
    )
    assert result.ok is True
    execution = json.loads(
        (Path(result.archive_path or "") / "execution_manifest.json").read_bytes()
    )
    assert execution["provider_endpoint_sha256"] == sha256_bytes(
        first_party.encode("ascii")
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://messages.example.test",
        "https://user@messages.example.test",
        "https://messages.example.test/path?query=1",
        "https://messages.example.test/a/../b",
        "https://messages.example.test//v1",
        "https://messages.example.test/%76%31",
    ],
)
def test_anthropic_invalid_endpoint_fails_before_sdk_key_archive_or_provider(
    tmp_path: Path,
    monkeypatch,
    endpoint: str,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    invocation = _anthropic_invocation(tmp_path, monkeypatch, endpoint=endpoint)
    metadata_calls: list[str] = []
    monkeypatch.setattr(
        runner.metadata,
        "version",
        lambda name: metadata_calls.append(name),
    )
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "must-not-be-read")
    result = run_shadow(**invocation)
    assert result.reason_codes == ("shadow_request_invalid",)
    assert metadata_calls == []
    assert not Path(invocation["archive_root"]).exists()


def test_anthropic_transport_failure_publishes_verifiable_archive_without_sdk_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    monkeypatch.setattr(runner.metadata, "version", lambda _name: "0.104.1")
    invocation = _anthropic_invocation(tmp_path, monkeypatch)
    captures: list[_AnthropicRaisingAdapter] = []

    def factory(execution):
        adapter = _AnthropicRaisingAdapter(execution)
        captures.append(adapter)
        return adapter

    result = run_shadow(**invocation, adapter_factory=factory)
    assert result.ok is False
    assert result.archive_complete is True
    assert result.reason_codes == ("provider_failed",)
    assert len(captures) == 1
    archive = Path(result.archive_path or "")
    transport = next(archive.glob("attempts/*/*/transport.json"))
    prefix = transport.parent
    assert not (prefix / "sdk_projection.json").exists()
    assert json.loads(transport.read_bytes())["facts"]["envelope"]["state"] == "absent"


def test_anthropic_exact_opaque_model_change_creates_distinct_request_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    monkeypatch.setattr(runner.metadata, "version", lambda _name: "0.104.1")
    first_invocation = _anthropic_invocation(tmp_path, monkeypatch)
    first = run_shadow(
        **first_invocation,
        adapter_factory=lambda execution: _AnthropicFixtureAdapter(execution),
    )
    assert first.ok is True
    first_archive = Path(first.archive_path or "")
    first_request = (first_archive / "request.json").read_bytes()

    instrument = Path(first_invocation["instrument"])
    payload = json.loads(instrument.read_bytes())
    payload["model_id"] = "another-public-model-id"
    payload["model_version"] = "another-public-model-id"
    instrument.write_bytes(canonical_json_bytes(payload))
    second_invocation = {
        **first_invocation,
        "trial_id": "trial-anthropic-runner-model-change-v1",
    }
    second = run_shadow(
        **second_invocation,
        adapter_factory=lambda execution: _AnthropicFixtureAdapter(execution),
    )
    assert second.ok is True
    assert second.archive_path != first.archive_path
    assert (
        Path(second.archive_path or "") / "request.json"
    ).read_bytes() != first_request
    assert (first_archive / "request.json").read_bytes() == first_request


def test_anthropic_endpoint_change_creates_distinct_execution_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from multi_agent_brief.semantic_evaluator import runner

    monkeypatch.setattr(runner.metadata, "version", lambda _name: "0.104.1")
    invocation = _anthropic_invocation(tmp_path, monkeypatch)
    first = run_shadow(
        **invocation,
        adapter_factory=lambda execution: _AnthropicFixtureAdapter(execution),
    )
    assert first.ok is True
    first_archive = Path(first.archive_path or "")
    first_request = (first_archive / "request.json").read_bytes()

    second_endpoint = "https://other-messages.example.test/api"
    monkeypatch.setenv(ANTHROPIC_ENDPOINT_SETTING, second_endpoint)
    metadata_calls = 0

    def forbidden_metadata(_name):
        nonlocal metadata_calls
        metadata_calls += 1
        raise AssertionError("archive conflict touched distribution metadata")

    monkeypatch.setattr(runner.metadata, "version", forbidden_metadata)
    conflict = run_shadow(
        **invocation,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("endpoint conflict touched adapter")
        ),
    )
    assert conflict.reason_codes == ("shadow_request_conflict",)
    assert metadata_calls == 0
    assert (first_archive / "request.json").read_bytes() == first_request

    monkeypatch.setattr(runner.metadata, "version", lambda _name: "0.104.1")
    second = run_shadow(
        **{
            **invocation,
            "trial_id": "trial-anthropic-endpoint-change-v1",
        },
        adapter_factory=lambda execution: _AnthropicFixtureAdapter(
            execution,
            endpoint=second_endpoint,
        ),
    )
    assert second.ok is True
    assert second.archive_path != first.archive_path
    assert (
        Path(second.archive_path or "") / "request.json"
    ).read_bytes() != first_request
    assert (first_archive / "request.json").read_bytes() == first_request


def test_anthropic_nondefault_sampling_fails_before_sdk_or_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    invocation = _anthropic_invocation(tmp_path, monkeypatch)
    instrument = Path(invocation["instrument"])
    payload = json.loads(instrument.read_bytes())
    payload["decoding"]["temperature"] = 0.0
    instrument.write_bytes(canonical_json_bytes(payload))
    metadata_calls: list[str] = []
    monkeypatch.setattr(
        "multi_agent_brief.semantic_evaluator.runner.metadata.version",
        lambda name: metadata_calls.append(name),
    )
    result = run_shadow(**invocation)
    assert result.ok is False
    assert result.reason_codes == ("shadow_request_invalid",)
    assert metadata_calls == []
    assert not Path(invocation["archive_root"]).exists()


def test_se2r_05_multi_attempt_policy_requires_classifier_retry_reason(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path, max_attempts=2)
    instrument = Path(invocation["instrument"])
    payload = json.loads(instrument.read_bytes())
    payload["retry_policy"]["retryable_reason_codes"] = []
    instrument.write_bytes(canonical_json_bytes(payload))
    result = run_shadow(**invocation, sleep=lambda _seconds: None)
    assert result.ok is False
    assert result.reason_codes == ("shadow_request_invalid",)


def test_se2r_05_and_09_retryable_first_then_success_uses_one_frozen_backoff(
    tmp_path: Path,
) -> None:
    captures: list[_ScriptedAdapter] = []
    sleeps: list[float] = []
    result = run_shadow(
        **_invocation(tmp_path, max_attempts=2),
        sleep=sleeps.append,
        adapter_factory=_factory("retry_then_success", captures),
    )
    assert result.ok is True
    assert sleeps == [0.017]
    assert captures[0].calls[0][1] == 1
    assert captures[0].calls[1][1] == 2
    archive = Path(result.archive_path or "")
    records = sorted((archive / "attempts").glob("*/*/transport.json"))
    assert len(records) == 10
    outcomes = [json.loads(path.read_bytes()) for path in records]
    retryable = [item for item in outcomes if item["retry_eligible"]]
    assert len(retryable) == 1
    assert retryable[0]["shadow_reason"] == "provider_retryable_failure"


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


def test_adapter_exception_is_typed_terminal_and_value_free(tmp_path: Path) -> None:
    captures: list[_ScriptedAdapter] = []
    result = run_shadow(
        **_invocation(tmp_path, max_attempts=2),
        sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("no retry")),
        adapter_factory=_factory("raise", captures),
    )
    assert result.ok is False
    assert result.archive_complete is True
    assert "private provider detail" not in json.dumps(result.to_dict())
    archive = Path(result.archive_path or "")
    records = [
        json.loads(path.read_bytes())
        for path in archive.glob("attempts/*/*/transport.json")
    ]
    failed = [item for item in records if item["shadow_reason"] == "provider_failed"]
    assert len(failed) == 1
    assert failed[0]["facts"]["transport_kind"] == "adapter_error"
    assert failed[0]["retry_eligible"] is False
    assert len(captures[0].calls) == 9


def test_shared_prepare_execute_matches_ordinary_runner_lifecycle(
    tmp_path: Path,
) -> None:
    (tmp_path / "prepared").mkdir()
    (tmp_path / "ordinary").mkdir()
    prepared_invocation = _invocation(tmp_path / "prepared")
    prepared_invocation.pop("clock")
    prepared = prepare_shadow_run(**prepared_invocation)
    assert isinstance(prepared, PreparedShadowRun)
    assert len(prepared.admission.prompts) == 9
    assert not Path(prepared_invocation["archive_root"]).exists()

    prepared_result = execute_prepared_shadow_run(prepared)
    ordinary_result = run_shadow(**_invocation(tmp_path / "ordinary"))
    assert prepared_result.ok is ordinary_result.ok is True
    assert prepared_result.run_status == ordinary_result.run_status == "completed"
    assert (
        prepared_result.validation_status
        == ordinary_result.validation_status
        == "accepted"
    )
