"""Retry, terminal-failure, and admission-order tests for the shadow runner."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil

import multi_agent_brief.semantic_evaluator.runner as runner_module
from multi_agent_brief.semantic_evaluator.adapter import RawProviderAttempt
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    SyntheticFixtureAdapterV1,
)
from multi_agent_brief.semantic_evaluator.runner import PROFILE_ID, run_shadow


FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"
FIXED_TIME = "2026-07-17T00:00:00Z"


def _invocation(tmp_path: Path, *, max_attempts: int = 1):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name in ("report.md", "bounded_context.json", "instrument.json"):
        shutil.copyfile(FIXTURES / name, inputs / name)
    instrument_path = inputs / "instrument.json"
    config = json.loads(instrument_path.read_text(encoding="utf-8"))
    config["retry_policy"] = {
        "max_attempts": max_attempts,
        "retryable_reason_codes": (
            ["provider_retryable_failure"] if max_attempts > 1 else []
        ),
        "backoff_schedule_ms": [17] * (max_attempts - 1),
    }
    instrument_path.write_text(
        json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return {
        "report": inputs / "report.md",
        "bounded_context": inputs / "bounded_context.json",
        "profile": PROFILE_ID,
        "instrument": instrument_path,
        "trial_id": "trial-runner-v1",
        "archive_root": tmp_path / "archives",
        "clock": lambda: FIXED_TIME,
    }


class _ScriptedAdapter:
    def __init__(self, execution, mode: str) -> None:
        self.adapter_id = execution.adapter_id
        self.adapter_version = execution.adapter_version
        self.provider_sdk_name = execution.provider_sdk_name
        self.provider_sdk_version = execution.provider_sdk_version
        self.qualification_eligible = execution.qualification_eligible
        self.mode = mode
        self.calls = []
        self._delegate = SyntheticFixtureAdapterV1()

    def _failed(self, request, reason="provider_failed"):
        return RawProviderAttempt(
            status="failed",
            reason_code=reason,
            provider_request_id=None,
            observed_model_version=None,
            request_projection_bytes=request.projection_bytes(),
            raw_transport_response=b'{"synthetic":"failure"}',
            extracted_output=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )

    def invoke(self, request):
        self.calls.append((request.dimension_id, request.attempt_ordinal))
        first_dimension = "cross_section_consistency"
        if self.mode == "retry_then_success" and len(self.calls) == 1:
            return self._failed(request, "provider_retryable_failure")
        if self.mode == "retry_exhausted":
            return self._failed(request, "provider_retryable_failure")
        if (
            self.mode == "one_terminal_failure"
            and request.dimension_id == first_dimension
        ):
            return self._failed(request)
        if self.mode == "malformed" and request.dimension_id == first_dimension:
            return RawProviderAttempt(
                status="completed",
                reason_code=None,
                provider_request_id="synthetic-malformed-v1",
                observed_model_version=request.expected_model_version,
                request_projection_bytes=request.projection_bytes(),
                raw_transport_response=b'{"output":"malformed"}',
                extracted_output=b"not-json",
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
            )
        if self.mode == "security" and request.dimension_id == first_dimension:
            match = re.search(r"BLSE_CANARY_V1_[0-9a-f]{64}", request.system_text)
            assert match is not None
            canary = match.group(0)
            return RawProviderAttempt(
                status="completed",
                reason_code=None,
                provider_request_id="synthetic-security-v1",
                observed_model_version=request.expected_model_version,
                request_projection_bytes=request.projection_bytes(),
                raw_transport_response=b'{"output":"security"}',
                extracted_output=(f'{{"leak":"{canary}"}}').encode("utf-8"),
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
            )
        if self.mode == "identity_drift" and request.dimension_id == first_dimension:
            completed = self._delegate.invoke(request)
            return RawProviderAttempt(
                status="completed",
                reason_code=None,
                provider_request_id=completed.provider_request_id,
                observed_model_version="different-model-version",
                request_projection_bytes=completed.request_projection_bytes,
                raw_transport_response=completed.raw_transport_response,
                extracted_output=completed.extracted_output,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
            )
        return self._delegate.invoke(request)


def _factory(mode: str, capture: list[_ScriptedAdapter]):
    def create(execution):
        adapter = _ScriptedAdapter(execution, mode)
        capture.append(adapter)
        return adapter

    return create


def test_retry_sequence_is_owned_by_runner_and_all_attempts_are_archived(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path, max_attempts=2)
    sleeps: list[float] = []
    adapters: list[_ScriptedAdapter] = []
    result = run_shadow(
        **invocation,
        sleep=sleeps.append,
        adapter_factory=_factory("retry_then_success", adapters),
    )
    assert result.ok is True
    assert sleeps == [0.017]
    assert adapters[0].calls[:2] == [
        ("cross_section_consistency", 1),
        ("cross_section_consistency", 2),
    ]
    archive = Path(result.archive_path or "")
    assert len(list((archive / "attempts").glob("*/*/transport.json"))) == 10
    first = json.loads(
        (archive / "attempts/cross_section_consistency/1/transport.json").read_text(
            encoding="utf-8"
        )
    )
    assert first["reason_code"] == "provider_retryable_failure"


def test_retry_exhaustion_is_complete_failure_evidence_not_semantic_success(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path, max_attempts=2)
    adapters: list[_ScriptedAdapter] = []
    result = run_shadow(
        **invocation,
        sleep=lambda _seconds: None,
        adapter_factory=_factory("retry_exhausted", adapters),
    )
    assert result.ok is False
    assert result.archive_complete is True
    assert result.run_status == "provider_failed"
    assert result.validation_status == "incomplete"
    assert result.reason_codes == ("provider_retryable_failure",)
    assert len(adapters[0].calls) == 18


def test_one_terminal_dimension_failure_is_linked_and_withholds_all_advice(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    adapters: list[_ScriptedAdapter] = []
    result = run_shadow(
        **invocation,
        sleep=lambda _seconds: None,
        adapter_factory=_factory("one_terminal_failure", adapters),
    )
    assert result.ok is False
    assert result.archive_complete is True
    assert result.run_status == "incomplete"
    assert result.validation_status == "incomplete"
    presentation = json.loads(
        Path(result.archive_path or "", "presentation_actual.json").read_text(
            encoding="utf-8"
        )
    )
    assert presentation["additional_semantic_findings"] == []
    assert presentation["failure_count"] == 1


def test_parser_failure_is_not_retried_or_repaired(tmp_path: Path) -> None:
    invocation = _invocation(tmp_path, max_attempts=2)
    adapters: list[_ScriptedAdapter] = []
    sleeps: list[float] = []
    result = run_shadow(
        **invocation,
        sleep=sleeps.append,
        adapter_factory=_factory("malformed", adapters),
    )
    assert result.ok is False
    assert result.run_status == "parser_failed"
    assert result.validation_status == "rejected"
    assert result.reason_codes == ("parser_invalid_json",)
    assert sleeps == []
    assert adapters[0].calls[0] == ("cross_section_consistency", 1)
    assert adapters[0].calls[1][0] != "cross_section_consistency"


def test_security_failure_is_not_retried_and_displays_no_advice(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path, max_attempts=2)
    adapters: list[_ScriptedAdapter] = []
    sleeps: list[float] = []
    result = run_shadow(
        **invocation,
        sleep=sleeps.append,
        adapter_factory=_factory("security", adapters),
    )
    assert result.ok is False
    assert result.run_status == "security_failed"
    assert result.validation_status == "rejected"
    assert result.reason_codes == ("tool_or_canary_output_forbidden",)
    assert sleeps == []
    presentation = json.loads(
        Path(result.archive_path or "", "presentation_actual.json").read_text(
            encoding="utf-8"
        )
    )
    assert presentation["additional_semantic_findings"] == []


def test_model_identity_drift_archives_terminal_reason_without_parsing_output(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    adapters: list[_ScriptedAdapter] = []
    result = run_shadow(
        **invocation,
        sleep=lambda _seconds: None,
        adapter_factory=_factory("identity_drift", adapters),
    )
    assert result.ok is False
    assert result.run_status == "incomplete"
    assert result.reason_codes == ("provider_identity_mismatch",)
    record = json.loads(
        Path(
            result.archive_path or "",
            "attempts/cross_section_consistency/1/transport.json",
        ).read_text(encoding="utf-8")
    )
    assert record["reason_code"] == "provider_identity_mismatch"
    assert not Path(
        result.archive_path or "",
        "attempts/cross_section_consistency/1/output.txt",
    ).exists()


def test_adapter_unavailable_and_prompt_sizer_failure_write_no_final_archive(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)

    def unavailable(_execution):
        raise RuntimeError("hidden detail")

    result = run_shadow(
        **invocation,
        sleep=lambda _seconds: None,
        adapter_factory=unavailable,
    )
    assert result.reason_codes == ("shadow_adapter_unavailable",)
    assert not Path(invocation["archive_root"]).exists()

    config_path = Path(invocation["instrument"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["prompt_sizer"]["sizer_version"] = "wrong-version"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    blocked = run_shadow(**invocation, sleep=lambda _seconds: None)
    assert blocked.reason_codes == ("prompt_sizer_unavailable",)
    assert not Path(invocation["archive_root"]).exists()


def test_hash_and_policy_failures_block_before_prompt_or_provider(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    context_path = Path(invocation["bounded_context"])
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["context_sha256"] = "0" * 64
    context_path.write_text(json.dumps(context), encoding="utf-8")
    touched = False

    def forbidden(_execution):
        nonlocal touched
        touched = True
        raise AssertionError

    mismatch = run_shadow(**invocation, adapter_factory=forbidden)
    assert mismatch.reason_codes == ("input_sha_mismatch",)
    assert touched is False
    assert not Path(invocation["archive_root"]).exists()

    context["data_class"] = "private"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    private = run_shadow(**invocation, adapter_factory=forbidden)
    assert private.reason_codes == ("shadow_request_invalid",)
    assert touched is False


def test_execution_identity_rotates_with_behavior_source(monkeypatch) -> None:
    sizer = runner_module.SyntheticFixturePromptSizerV1()
    policy = runner_module._policy("synthetic_fixture_v1")
    first = runner_module._execution_manifest(
        instrument_sha256="0" * 64,
        policy=policy,
        prompt_sizer=sizer,
    )
    original = runner_module.source_sha256_for_module

    def changed(module_name: str) -> str:
        if module_name == "multi_agent_brief.semantic_evaluator.prompt_sizer":
            return "f" * 64
        return original(module_name)

    monkeypatch.setattr(runner_module, "source_sha256_for_module", changed)
    second = runner_module._execution_manifest(
        instrument_sha256="0" * 64,
        policy=policy,
        prompt_sizer=sizer,
    )
    assert first.execution_sha256 != second.execution_sha256
    assert first.runner_source_sha256 != second.runner_source_sha256


def test_policy_records_retention_without_automatic_deletion() -> None:
    policy = runner_module._policy("synthetic_fixture_v1")
    assert policy.raw_retention_days == 30
    assert policy.local_filesystem_only is True
    assert not hasattr(runner_module, "delete_expired_archives")
