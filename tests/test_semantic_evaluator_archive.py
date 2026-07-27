"""Immutable v4 shadow archive replay and reachability tests."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from multi_agent_brief.semantic_evaluator import archive as archive_module
from multi_agent_brief.semantic_evaluator import runner as runner_module
from multi_agent_brief.semantic_evaluator.adapter import FrozenProviderRequestV4
from multi_agent_brief.semantic_evaluator.archive import (
    _recomputed_facts,
    _validate_attempt_reachability,
    verify_shadow_archive,
)
from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
    OPENAI_ADAPTER_ID,
    OPENAI_PROVIDER_ID,
    OpenAIResponsesAdapterV4,
    synthetic_openai_response_bytes_v4,
)
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_ADAPTER_ID,
    ANTHROPIC_ENDPOINT_SETTING,
    ANTHROPIC_PROVIDER_ID,
    AnthropicMessagesAdapterV1,
    synthetic_anthropic_message_bytes_v1,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.contracts import (
    DIMENSION_RESPONSE_SCHEMA_ID,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    _rubric_from_prompt,
)
from multi_agent_brief.semantic_evaluator.prompt_sizer import (
    ANTHROPIC_PROMPT_SIZER_ID,
    ANTHROPIC_PROMPT_SIZER_VERSION,
)
from multi_agent_brief.semantic_evaluator.runner import _attempt_record, run_shadow
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_bytes,
)
from multi_agent_brief.semantic_evaluator.shadow_contracts import (
    ProviderAttemptRecordV5,
    ProviderBoundaryFactsRecordV4,
)


_FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"
_ANTHROPIC_TEST_MODEL = "public-nonclaude-model-v1"


def _run(tmp_path: Path, *, trial_id: str = "trial-archive-v4"):
    archive_root = tmp_path / "archives"
    archive_root.mkdir()
    result = run_shadow(
        report=_FIXTURES / "report.md",
        bounded_context=_FIXTURES / "bounded_context.json",
        profile="research_design_report_zh_v1",
        instrument=_FIXTURES / "instrument.json",
        trial_id=trial_id,
        archive_root=archive_root,
        clock=lambda: "2027-07-18T00:00:00Z",
    )
    assert result.ok, result
    assert result.archive_path is not None
    return Path(result.archive_path), archive_root


def _strict_load(path: Path) -> dict:
    value = json.loads(path.read_bytes().decode("utf-8"))
    assert type(value) is dict
    return value


def _rehash_outer(archive: Path, changed_member: str) -> None:
    """Rebuild manifest/receipt/COMPLETE so inner replay does the rejecting."""

    manifest_path = archive / "archive_manifest.json"
    manifest = _strict_load(manifest_path)
    members = manifest["payload_members"]
    for member in members:
        if member["path"] == changed_member:
            raw = (archive / changed_member).read_bytes()
            member["size_bytes"] = len(raw)
            member["sha256"] = sha256_bytes(raw)
            break
    else:
        raise AssertionError(changed_member)
    manifest["aggregate_payload_sha256"] = canonical_sha256(members)
    manifest["archive_id"] = (
        "archive-"
        + canonical_sha256(
            [
                manifest["shadow_request_sha256"],
                manifest["aggregate_payload_sha256"],
            ]
        )[:16]
    )
    manifest["archive_manifest_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "archive_manifest_sha256"
        }
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    receipt_path = archive / "receipt.json"
    receipt = _strict_load(receipt_path)
    receipt["archive_id"] = manifest["archive_id"]
    receipt["archive_manifest_sha256"] = manifest["archive_manifest_sha256"]
    receipt["receipt_id"] = (
        "receipt-"
        + canonical_sha256([receipt["archive_manifest_sha256"], receipt["run_id"]])[:16]
    )
    receipt["receipt_sha256"] = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )
    receipt_raw = canonical_json_bytes(receipt)
    receipt_path.write_bytes(receipt_raw)
    (archive / "COMPLETE").write_bytes(
        (sha256_bytes(receipt_raw) + "\n").encode("ascii")
    )


def _anthropic_archive(
    tmp_path: Path,
    monkeypatch,
    *,
    raw_transform: Callable[[bytes], bytes] | None = None,
    sdk_mismatch: bool = False,
) -> tuple[Path, dict[str, object]]:
    endpoint = "https://messages.example.test/v1"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    report = inputs / "report.md"
    context = inputs / "bounded_context.json"
    instrument = inputs / "instrument.json"
    report.write_bytes((_FIXTURES / "report.md").read_bytes())
    context.write_bytes((_FIXTURES / "bounded_context.json").read_bytes())
    instrument_payload = json.loads((_FIXTURES / "instrument.json").read_bytes())
    instrument_payload.update(
        {
            "instrument_config_id": "anthropic-archive-instrument-v1",
            "provider_id": ANTHROPIC_PROVIDER_ID,
            "model_id": _ANTHROPIC_TEST_MODEL,
            "model_version": _ANTHROPIC_TEST_MODEL,
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
    instrument.write_bytes(canonical_json_bytes(instrument_payload))
    monkeypatch.setenv(ANTHROPIC_ENDPOINT_SETTING, endpoint)
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")

    class Adapter:
        def __init__(self, execution) -> None:
            self.adapter_id = execution.adapter_id
            self.adapter_version = execution.adapter_version
            self.provider_sdk_name = execution.provider_sdk_name
            self.provider_sdk_version = execution.provider_sdk_version
            self.qualification_eligible = execution.qualification_eligible
            self.base_url = endpoint
            self.delegate = object.__new__(AnthropicMessagesAdapterV1)

        def invoke(self, request):
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
                response_id=f"msg-{request.dimension_id}",
                model=request.expected_model_version,
                content=[{"type": "text", "text": output.decode("utf-8")}],
            )
            if raw_transform is not None:
                raw = raw_transform(raw)
            sdk_response = (
                SimpleNamespace(
                    id="sdk-unattested-id",
                    model=request.expected_model_version,
                    stop_reason="end_turn",
                    content=[SimpleNamespace(type="text", text=output.decode("utf-8"))],
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                )
                if sdk_mismatch
                else None
            )
            return self.delegate._attempt_from_response(
                request=request,
                raw=raw,
                sdk_response=sdk_response,
            )

    invocation = {
        "report": report,
        "bounded_context": context,
        "profile": "research_design_report_zh_v1",
        "instrument": instrument,
        "trial_id": "trial-anthropic-status-tamper",
        "archive_root": (tmp_path / "archives").resolve(),
        "clock": lambda: "2027-07-18T00:00:00Z",
        "sleep": lambda _seconds: None,
    }
    result = run_shadow(
        **invocation,
        adapter_factory=lambda execution: Adapter(execution),
    )
    assert result.archive_path is not None
    if raw_transform is None and not sdk_mismatch:
        assert result.ok
    else:
        assert result.ok is False
        assert result.reason_codes == ("provider_failed",)
    return Path(result.archive_path), invocation


def _anthropic_raw_transform(mode: str) -> Callable[[bytes], bytes]:
    def transform(raw: bytes) -> bytes:
        payload = json.loads(raw)
        if mode == "missing_stop":
            payload.pop("stop_reason")
        elif mode == "unknown_stop":
            payload["stop_reason"] = "future_stop"
        elif mode == "missing_id":
            payload.pop("id")
        elif mode == "missing_model":
            payload.pop("model")
        elif mode == "stop_sequence_surrogate":
            payload["stop_sequence"] = "\ud800"
        else:
            raise AssertionError(mode)
        return json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

    return transform


def _text_fact(value: str) -> dict[str, object]:
    raw = value.encode("utf-8", errors="strict")
    return {
        "invalid_code": None,
        "state": "present_valid",
        "utf8_hex": raw.hex(),
        "utf8_sha256": sha256_bytes(raw),
    }


def test_se2r_12_complete_archive_replays_before_adapter_access(
    tmp_path: Path,
) -> None:
    archive, archive_root = _run(tmp_path)

    def forbidden_adapter(_execution):
        pytest.fail("matching archive replay reached adapter construction")

    replay = run_shadow(
        report=_FIXTURES / "report.md",
        bounded_context=_FIXTURES / "bounded_context.json",
        profile="research_design_report_zh_v1",
        instrument=_FIXTURES / "instrument.json",
        trial_id="trial-archive-v4",
        archive_root=archive_root,
        adapter_factory=forbidden_adapter,
        clock=lambda: "2027-07-18T00:00:00Z",
    )
    assert replay.ok and replay.replayed
    assert Path(replay.archive_path or "") == archive


def test_archive_publication_failure_before_atomic_commit_is_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_root = tmp_path / "archives"
    archive_root.mkdir()
    invocation = {
        "report": _FIXTURES / "report.md",
        "bounded_context": _FIXTURES / "bounded_context.json",
        "profile": "research_design_report_zh_v1",
        "instrument": _FIXTURES / "instrument.json",
        "trial_id": "trial-atomic-retry-v4",
        "archive_root": archive_root,
        "clock": lambda: "2027-07-18T00:00:00Z",
    }
    real_rename = archive_module.os.rename

    def fail_commit(_source, _destination):
        raise OSError("injected")

    monkeypatch.setattr(archive_module.os, "rename", fail_commit)
    failed = run_shadow(**invocation)
    assert failed.reason_codes == ("shadow_archive_publish_failed",)
    assert not list(
        (archive_root / "semantic-evaluator" / "v0.1" / "trials").glob("trial-*")
    )

    monkeypatch.setattr(archive_module.os, "rename", real_rename)
    retry = run_shadow(**invocation)
    assert retry.ok is True
    assert retry.archive_complete is True


def test_atomic_publish_accepts_same_request_cooperative_winner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    real_rename = archive_module.os.rename

    def winner(source, destination):
        real_rename(source, destination)
        raise FileExistsError

    monkeypatch.setattr(archive_module.os, "rename", winner)
    archive, _root = _run(tmp_path, trial_id="trial-cooperative-winner-v4")
    assert (archive / "COMPLETE").is_file()


def test_se2r_10_rehashed_raw_tamper_fails_inner_fact_recomputation(
    tmp_path: Path,
) -> None:
    archive, _archive_root = _run(tmp_path, trial_id="trial-raw-tamper-v4")
    response = next(archive.glob("attempts/*/*/response.body"))
    response.write_bytes(response.read_bytes().replace(b'"completed"', b'"incomplete"'))
    relative = response.relative_to(archive).as_posix()
    _rehash_outer(archive, relative)

    with pytest.raises(SemanticEvaluatorError) as caught:
        verify_shadow_archive(archive)
    assert caught.value.reason_code == "shadow_archive_invalid"


def test_predecessor_request_schema_is_not_migrated(tmp_path: Path) -> None:
    archive, _archive_root = _run(tmp_path, trial_id="trial-old-schema-v4")
    request_path = archive / "request.json"
    request = _strict_load(request_path)
    request["schema_version"] = "briefloop.semantic_evaluator.shadow_run_request.v3"
    request_path.write_bytes(canonical_json_bytes(request))
    _rehash_outer(archive, "request.json")

    with pytest.raises(SemanticEvaluatorError) as caught:
        verify_shadow_archive(archive)
    assert caught.value.reason_code == "shadow_archive_invalid"


def test_se2r_08_terminal_attempt_cannot_reach_later_success() -> None:
    terminal = SimpleNamespace(
        attempt_status="failed",
        shadow_reason="provider_incomplete",
        kernel_reason="provider_failed",
        retry_eligible=False,
    )
    success = SimpleNamespace(
        attempt_status="completed",
        shadow_reason=None,
        kernel_reason=None,
        retry_eligible=False,
    )
    attempts = {
        "dimension-a": [
            (SimpleNamespace(attempt_ordinal=1), terminal),
            (SimpleNamespace(attempt_ordinal=2), success),
        ]
    }
    with pytest.raises(SemanticEvaluatorError) as caught:
        _validate_attempt_reachability(
            attempts,
            max_attempts=2,
        )
    assert caught.value.reason_code == "shadow_archive_invalid"


def test_se2r_09_retryable_attempt_can_reach_contiguous_success() -> None:
    retryable = SimpleNamespace(
        attempt_status="failed",
        shadow_reason="provider_retryable_failure",
        kernel_reason="provider_retryable_failure",
        retry_eligible=True,
    )
    success = SimpleNamespace(
        attempt_status="completed",
        shadow_reason=None,
        kernel_reason=None,
        retry_eligible=False,
    )
    attempts = {
        "dimension-a": [
            (SimpleNamespace(attempt_ordinal=1), retryable),
            (SimpleNamespace(attempt_ordinal=2), success),
        ]
    }
    assert (
        _validate_attempt_reachability(
            attempts,
            max_attempts=2,
        )
        == ()
    )


def test_se2r_10_typed_transport_cannot_override_retained_provenance() -> None:
    request = FrozenProviderRequestV4(
        trial_id="trial-public",
        dimension_id="dimension-1",
        attempt_ordinal=1,
        system_text="system",
        user_text="user",
        prompt_request_sha256="1" * 64,
        adapter_id=OPENAI_ADAPTER_ID,
        provider_id=OPENAI_PROVIDER_ID,
        model_id="gpt-test",
        expected_model_version="gpt-test-2026-07-18",
        temperature=0.0,
        top_p=1.0,
        max_output_tokens=100,
        seed=None,
        timeout_seconds=60,
    )
    raw = synthetic_openai_response_bytes_v4(
        status="completed",
        response_id="resp-public",
        model=request.expected_model_version,
        output_text='{"findings":[]}',
    )
    attempt = object.__new__(OpenAIResponsesAdapterV4)._attempt_from_response(
        request=request,
        raw=raw,
        sdk_response=None,
        transport_kind="http_error",
        transport_http_status=500,
        transport_http_present=True,
    )
    record = _attempt_record(
        provider_request=request,
        attempt_ref="attempt:dimension-1:1",
        raw=attempt,
        started_at="2027-07-18T00:00:00Z",
        completed_at="2027-07-18T00:00:01Z",
    )
    forged = record.model_dump(mode="json", warnings="error")
    facts = forged["facts"]
    assert isinstance(facts, dict)
    facts["transport_kind"] = "response"
    facts["boundary_facts_sha256"] = canonical_sha256(
        {key: value for key, value in facts.items() if key != "boundary_facts_sha256"}
    )
    forged.update(
        {
            "attempt_status": "completed",
            "shadow_reason": None,
            "kernel_reason": None,
            "retry_eligible": False,
            "output_eligible": True,
            "extracted_output_sha256": sha256_bytes(b'{"findings":[]}'),
        }
    )
    forged["attempt_record_sha256"] = canonical_sha256(
        {key: value for key, value in forged.items() if key != "attempt_record_sha256"}
    )
    forged_record = ProviderAttemptRecordV5.model_validate(forged)
    recomputed = _recomputed_facts(
        record=forged_record,
        response_raw=raw,
        sdk_projection_raw=attempt.sdk_projection_bytes,
    )
    assert recomputed.transport_kind == "http_error"
    assert ProviderBoundaryFactsRecordV4.from_runtime(recomputed) != forged_record.facts


def test_anthropic_archive_recomputes_terminal_status_from_raw_bytes() -> None:
    request = FrozenProviderRequestV4(
        trial_id="trial-anthropic-archive",
        dimension_id="dimension-1",
        attempt_ordinal=1,
        system_text="system",
        user_text="user",
        prompt_request_sha256="1" * 64,
        adapter_id=ANTHROPIC_ADAPTER_ID,
        provider_id=ANTHROPIC_PROVIDER_ID,
        model_id=_ANTHROPIC_TEST_MODEL,
        expected_model_version=_ANTHROPIC_TEST_MODEL,
        temperature=1.0,
        top_p=1.0,
        max_output_tokens=100,
        seed=None,
        timeout_seconds=60,
    )
    accepted_raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="end_turn",
        response_id="msg-archive-public",
        model=_ANTHROPIC_TEST_MODEL,
        content=[
            {"type": "thinking", "thinking": "non-output", "signature": "sig"},
            {"type": "text", "text": '{"findings":[]}'},
        ],
        input_tokens=10,
        output_tokens=5,
    )
    attempt = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=request,
        raw=accepted_raw,
        sdk_response=None,
    )
    record = _attempt_record(
        provider_request=request,
        attempt_ref="attempt:dimension-1:1",
        raw=attempt,
        started_at="2027-07-18T00:00:00Z",
        completed_at="2027-07-18T00:00:01Z",
    )
    recomputed = _recomputed_facts(
        record=record,
        response_raw=accepted_raw,
        sdk_projection_raw=attempt.sdk_projection_bytes,
    )
    assert ProviderBoundaryFactsRecordV4.from_runtime(recomputed) == record.facts

    present_error = object.__new__(AnthropicMessagesAdapterV1)._attempt_from_response(
        request=request,
        raw=accepted_raw,
        sdk_response=None,
        transport_kind="http_error",
        transport_http_status=503,
        transport_http_present=True,
    )
    present_error_record = _attempt_record(
        provider_request=request,
        attempt_ref="attempt:dimension-1:1",
        raw=present_error,
        started_at="2027-07-18T00:00:00Z",
        completed_at="2027-07-18T00:00:01Z",
    )
    recomputed_present_error = _recomputed_facts(
        record=present_error_record,
        response_raw=accepted_raw,
        sdk_projection_raw=present_error.sdk_projection_bytes,
    )
    assert (
        ProviderBoundaryFactsRecordV4.from_runtime(recomputed_present_error)
        == present_error_record.facts
    )

    refused_raw = synthetic_anthropic_message_bytes_v1(
        stop_reason="refusal",
        response_id="msg-archive-public",
        model=_ANTHROPIC_TEST_MODEL,
        content=[{"type": "text", "text": '{"findings":[]}'}],
        input_tokens=10,
        output_tokens=5,
    )
    with pytest.raises(SemanticEvaluatorError) as exc_info:
        _recomputed_facts(
            record=record,
            response_raw=refused_raw,
            sdk_projection_raw=attempt.sdk_projection_bytes,
        )
    assert exc_info.value.reason_code == "shadow_archive_invalid"


@pytest.mark.parametrize(
    "mode",
    [
        "missing_stop",
        "unknown_stop",
        "missing_id",
        "missing_model",
        "stop_sequence_surrogate",
    ],
)
def test_anthropic_malformed_present_response_is_replayable_negative_evidence(
    tmp_path: Path,
    monkeypatch,
    mode: str,
) -> None:
    archive, invocation = _anthropic_archive(
        tmp_path,
        monkeypatch,
        raw_transform=_anthropic_raw_transform(mode),
    )
    verified = verify_shadow_archive(archive)
    assert verified.ok is False
    assert verified.reason_codes == ("provider_failed",)
    records = [
        _strict_load(path)
        for path in sorted(archive.glob("attempts/*/*/transport.json"))
    ]
    assert records
    assert all(
        record["shadow_reason"] == "provider_boundary_invalid"
        and record["retry_eligible"] is False
        and record["output_eligible"] is False
        for record in records
    )

    metadata_calls = 0

    def forbidden_metadata(_name):
        nonlocal metadata_calls
        metadata_calls += 1
        raise AssertionError("negative replay touched distribution metadata")

    monkeypatch.delenv("BRIEFLOOP_LAJ_MESSAGES_API_KEY", raising=False)
    monkeypatch.setattr(runner_module.metadata, "version", forbidden_metadata)
    replay = run_shadow(
        **invocation,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("negative replay touched adapter")
        ),
    )
    assert replay.replayed is True
    assert replay.ok is False
    assert replay.reason_codes == ("provider_failed",)
    assert "shadow_archive_invalid" not in replay.reason_codes
    assert metadata_calls == 0


def test_anthropic_sdk_mismatch_is_replayable_negative_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive, invocation = _anthropic_archive(
        tmp_path,
        monkeypatch,
        sdk_mismatch=True,
    )
    verified = verify_shadow_archive(archive)
    assert verified.ok is False
    assert verified.reason_codes == ("provider_failed",)
    records = [
        _strict_load(path)
        for path in sorted(archive.glob("attempts/*/*/transport.json"))
    ]
    assert records
    assert all(
        record["shadow_reason"] == "provider_boundary_invalid"
        and record["retry_eligible"] is False
        and record["output_eligible"] is False
        for record in records
    )
    sdk_members = sorted(archive.glob("attempts/*/*/sdk_projection.json"))
    assert sdk_members
    assert all(b"sdk-unattested-id" not in path.read_bytes() for path in sdk_members)

    metadata_calls = 0

    def forbidden_metadata(_name):
        nonlocal metadata_calls
        metadata_calls += 1
        raise AssertionError("SDK mismatch replay touched distribution metadata")

    monkeypatch.delenv("BRIEFLOOP_LAJ_MESSAGES_API_KEY", raising=False)
    monkeypatch.setattr(runner_module.metadata, "version", forbidden_metadata)
    replay = run_shadow(
        **invocation,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("SDK mismatch replay touched adapter")
        ),
    )
    assert replay.replayed is True
    assert replay.ok is False
    assert replay.reason_codes == ("provider_failed",)
    assert metadata_calls == 0


@pytest.mark.parametrize(
    "field",
    [
        "stop_reason",
        "status",
        "response_id",
        "model_identity",
        "output",
        "usage",
        "body_state",
        "transport_kind",
        "http_status",
    ],
)
def test_anthropic_rehashed_sdk_member_tamper_is_intrinsically_invalid(
    tmp_path: Path,
    monkeypatch,
    field: str,
) -> None:
    archive, invocation = _anthropic_archive(tmp_path, monkeypatch)
    sdk_path = next(archive.glob("attempts/*/1/sdk_projection.json"))
    sdk_projection = _strict_load(sdk_path)
    if field == "stop_reason":
        sdk_projection[field] = _text_fact("refusal")
    elif field == "status":
        sdk_projection[field] = _text_fact("refused")
    elif field == "response_id":
        sdk_projection[field] = _text_fact("msg-rehashed-tamper")
    elif field == "model_identity":
        sdk_projection[field] = _text_fact("other-public-model")
    elif field == "output":
        sdk_projection[field] = _text_fact('{"tampered":true}')
    elif field == "usage":
        sdk_projection["input_tokens"] += 1
        sdk_projection["total_tokens"] += 1
    elif field == "body_state":
        sdk_projection[field] = "invalid"
    elif field == "transport_kind":
        sdk_projection[field] = "timeout"
    elif field == "http_status":
        sdk_projection[field] = {
            "invalid_code": None,
            "state": "present_valid",
            "value": 200,
        }
    else:
        raise AssertionError(field)
    sdk_path.write_bytes(canonical_json_bytes(sdk_projection))
    _rehash_outer(archive, sdk_path.relative_to(archive).as_posix())

    metadata_calls = 0

    def forbidden_metadata(_name):
        nonlocal metadata_calls
        metadata_calls += 1
        raise AssertionError("SDK tamper touched distribution metadata")

    monkeypatch.setattr(runner_module.metadata, "version", forbidden_metadata)
    replay = run_shadow(
        **invocation,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("SDK tamper touched adapter")
        ),
    )
    assert replay.reason_codes == ("shadow_archive_invalid",)
    assert metadata_calls == 0


def test_anthropic_invalid_raw_rehashed_sdk_id_tamper_is_invalid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive, invocation = _anthropic_archive(
        tmp_path,
        monkeypatch,
        raw_transform=_anthropic_raw_transform("missing_id"),
    )
    sdk_path = next(archive.glob("attempts/*/1/sdk_projection.json"))
    sdk_projection = _strict_load(sdk_path)
    assert sdk_projection["response_id"]["state"] == "absent"
    sdk_projection["response_id"] = _text_fact("msg-rehashed-tamper")
    sdk_path.write_bytes(canonical_json_bytes(sdk_projection))
    _rehash_outer(archive, sdk_path.relative_to(archive).as_posix())

    metadata_calls = 0

    def forbidden_metadata(_name):
        nonlocal metadata_calls
        metadata_calls += 1
        raise AssertionError("invalid-raw SDK tamper touched metadata")

    monkeypatch.setattr(runner_module.metadata, "version", forbidden_metadata)
    replay = run_shadow(
        **invocation,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("invalid-raw SDK tamper touched adapter")
        ),
    )
    assert replay.reason_codes == ("shadow_archive_invalid",)
    assert metadata_calls == 0


@pytest.mark.parametrize(
    "http_status",
    [
        {"invalid_code": None, "state": "present_valid", "value": 200},
        {
            "invalid_code": "http_status_wrong_type",
            "state": "present_invalid",
            "value": None,
        },
    ],
)
def test_anthropic_rehashed_sdk_status_tamper_is_invalid_before_metadata(
    tmp_path: Path,
    monkeypatch,
    http_status: dict[str, object],
) -> None:
    archive, invocation = _anthropic_archive(tmp_path, monkeypatch)
    sdk_path = next(archive.glob("attempts/*/1/sdk_projection.json"))
    sdk_projection = _strict_load(sdk_path)
    assert sdk_projection["transport_kind"] == "response"
    assert sdk_projection["http_status"]["state"] == "absent"
    sdk_projection["http_status"] = http_status
    sdk_path.write_bytes(canonical_json_bytes(sdk_projection))
    _rehash_outer(
        archive,
        sdk_path.relative_to(archive).as_posix(),
    )
    metadata_calls = 0

    def forbidden_metadata(_name):
        nonlocal metadata_calls
        metadata_calls += 1
        raise AssertionError("tampered replay touched distribution metadata")

    monkeypatch.setattr(runner_module.metadata, "version", forbidden_metadata)
    replay = run_shadow(
        **invocation,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("tampered replay touched adapter")
        ),
    )
    assert replay.reason_codes == ("shadow_archive_invalid",)
    assert metadata_calls == 0
