"""Immutable v4 shadow archive replay and reachability tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from multi_agent_brief.semantic_evaluator import archive as archive_module
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
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
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
