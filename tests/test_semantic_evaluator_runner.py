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
)
from multi_agent_brief.semantic_evaluator.runner import PROFILE_ID, run_shadow
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
