"""Focused runner rows for MU-LAJ-1's v4 provider boundary."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from multi_agent_brief.semantic_evaluator.adapter import (
    ExternalTextObservation,
    RawProviderAttemptV4,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
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
from multi_agent_brief.semantic_evaluator.prompt_sizer import (
    CLIPROXY_PROMPT_SIZER_ID,
    CLIPROXY_PROMPT_SIZER_VERSION,
)
from multi_agent_brief.semantic_evaluator.runner import (
    PROFILE_ID,
    PreparedShadowRun,
    execute_prepared_shadow_run,
    prepare_shadow_run,
    run_shadow,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes


FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"
FIXED_TIME = "2026-07-18T00:00:00Z"


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
