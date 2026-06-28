"""Tests for internal release modes and human approval ledger."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state.event_log import append_event
from multi_agent_brief.product.release_approval import (
    check_release_readiness,
    record_human_approval,
    validate_human_approval_ledger_payload,
    validate_release_readiness_report_payload,
)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(
        "project:\n  name: Release Approval Test\n",
        encoding="utf-8",
    )
    assert main(["state", "init", "--workspace", str(ws)]) == 0
    return ws


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _event_records(ws: Path) -> list[dict]:
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    return [
        json.loads(line)
        for line in event_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _event_types(ws: Path) -> list[str]:
    return [record["event_type"] for record in _event_records(ws)]


def test_approval_ledger_records_human_decision_and_event(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    assert main(["approval", "init", "--workspace", str(ws), "--mode", "research_review", "--json"]) == 0
    assert main([
        "approval",
        "record",
        "--workspace",
        str(ws),
        "--role",
        "content_owner",
        "--decision",
        "approve",
        "--reason",
        "Reviewed for internal research use.",
        "--json",
    ]) == 0

    ledger = _json(ws / "output" / "intermediate" / "human_approval_ledger.json")
    assert validate_human_approval_ledger_payload(ledger) is None
    assert ledger["records"][0]["mode"] == "research_review"
    assert ledger["records"][0]["run_id"]
    assert ledger["records"][0]["role"] == "content_owner"
    assert ledger["records"][0]["decision"] == "approve"
    assert ledger["records"][0]["event_id"]
    assert ledger["initialized_modes"]["research_review"]["run_id"] == ledger["records"][0]["run_id"]
    assert ledger["initialized_modes"]["research_review"]["event_id"]
    assert "human_approval_ledger_initialized" in _event_types(ws)
    assert "human_approval_recorded" in _event_types(ws)
    assert "not_public_release_authorization" in json.dumps(ledger)
    captured = capsys.readouterr()
    assert "not_public_release_authorization" in captured.out


def test_approval_requires_initialized_runtime_state(tmp_path: Path, capsys) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text("project:\n  name: Missing Runtime\n", encoding="utf-8")

    assert main(["approval", "init", "--workspace", str(ws), "--mode", "internal_management_review"]) == 1

    assert not (ws / "output" / "intermediate" / "human_approval_ledger.json").exists()
    captured = capsys.readouterr()
    assert "runtime_manifest.json is required" in captured.out


def test_approval_requires_current_run_event_log(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    (ws / "output" / "intermediate" / "event_log.jsonl").unlink()

    assert main(["approval", "init", "--workspace", str(ws), "--mode", "research_review"]) == 1

    assert not (ws / "output" / "intermediate" / "human_approval_ledger.json").exists()
    captured = capsys.readouterr()
    assert "event_log.jsonl is required" in captured.out


def test_approval_record_requires_explicit_mode_initialization(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    assert main([
        "approval",
        "record",
        "--workspace",
        str(ws),
        "--mode",
        "research_review",
        "--role",
        "content_owner",
        "--decision",
        "approve",
        "--reason",
        "Reviewed.",
    ]) == 1

    assert not (ws / "output" / "intermediate" / "human_approval_ledger.json").exists()
    captured = capsys.readouterr()
    assert "must be initialized with approval init" in captured.out


def test_release_check_blocks_missing_required_approval_without_public_authorization(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "research_review"]) == 0
    assert main([
        "approval",
        "record",
        "--workspace",
        str(ws),
        "--role",
        "content_owner",
        "--decision",
        "approve",
        "--reason",
        "Content owner approved.",
    ]) == 0

    assert main(["release", "check", "--workspace", str(ws), "--mode", "research_review", "--json"]) == 1

    report = _json(ws / "output" / "intermediate" / "release_readiness_report.json")
    assert validate_release_readiness_report_payload(report) is None
    assert report["run_id"]
    assert report["event_id"]
    assert report["status"] == "blocked"
    assert report["missing_roles"] == ["evidence_reviewer"]
    assert "missing_required_approval:evidence_reviewer" in report["blockers"]
    assert report["authorization"] == "not_authorized_for_public_release"
    assert "release_readiness_checked" in _event_types(ws)
    captured = capsys.readouterr()
    assert "not_authorized_for_public_release" in captured.out


def test_release_check_passes_internal_mode_after_required_approvals(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "research_review"]) == 0
    for role in ("content_owner", "evidence_reviewer"):
        assert main([
            "approval",
            "record",
            "--workspace",
            str(ws),
            "--role",
            role,
            "--decision",
            "approve",
            "--reason",
            f"{role} approved for internal review.",
        ]) == 0

    assert main(["release", "check", "--workspace", str(ws), "--mode", "research_review"]) == 0

    report = _json(ws / "output" / "intermediate" / "release_readiness_report.json")
    assert report["status"] == "pass"
    assert report["run_id"]
    assert report["event_id"]
    assert report["approved_roles"] == ["content_owner", "evidence_reviewer"]
    assert report["blockers"] == []
    assert report["authorization"] == "not_authorized_for_public_release"
    assert "Ready for research_review internal review" in report["next_step"]


def test_release_check_rejection_overrides_previous_approval(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "internal_management_review"]) == 0
    assert main([
        "approval",
        "record",
        "--workspace",
        str(ws),
        "--role",
        "content_owner",
        "--decision",
        "approve",
        "--reason",
        "Initial approval.",
    ]) == 0
    assert main([
        "approval",
        "record",
        "--workspace",
        str(ws),
        "--role",
        "content_owner",
        "--decision",
        "request_changes",
        "--reason",
        "Needs wording downgrade.",
    ]) == 0

    assert main(["release", "check", "--workspace", str(ws), "--mode", "internal_management_review"]) == 1

    report = _json(ws / "output" / "intermediate" / "release_readiness_report.json")
    assert report["status"] == "blocked"
    assert report["rejected_or_changes_requested_roles"] == ["content_owner"]
    assert report["blockers"] == ["approval_not_approved:content_owner"]


def test_release_check_ignores_prior_run_approvals(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "research_review"]) == 0
    for role in ("content_owner", "evidence_reviewer"):
        assert main([
            "approval",
            "record",
            "--workspace",
            str(ws),
            "--role",
            role,
            "--decision",
            "approve",
            "--reason",
            f"{role} approved the old run.",
        ]) == 0

    manifest_path = ws / "output" / "intermediate" / "runtime_manifest.json"
    manifest = _json(manifest_path)
    manifest["run_id"] = "mabw-run-new"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_event(
        workspace=ws,
        run_id="mabw-run-new",
        event_type="run_initialized",
        actor="cli",
        reason="Initialized a new run for approval scoping.",
        metadata={},
    )

    assert main(["release", "check", "--workspace", str(ws), "--mode", "research_review"]) == 1

    report = _json(ws / "output" / "intermediate" / "release_readiness_report.json")
    assert report["run_id"] == "mabw-run-new"
    assert report["status"] == "blocked"
    assert report["approved_roles"] == []
    assert report["missing_roles"] == ["content_owner", "evidence_reviewer"]
    assert report["records_considered"] == []


def test_release_check_rejects_forged_approval_event_id(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "research_review"]) == 0
    for role in ("content_owner", "evidence_reviewer"):
        assert main([
            "approval",
            "record",
            "--workspace",
            str(ws),
            "--role",
            role,
            "--decision",
            "approve",
            "--reason",
            f"{role} approved for internal review.",
        ]) == 0

    ledger_path = ws / "output" / "intermediate" / "human_approval_ledger.json"
    ledger = _json(ledger_path)
    for record in ledger["records"]:
        record["event_id"] = "evt-forged-does-not-exist"
    _write_json(ledger_path, ledger)

    assert main(["release", "check", "--workspace", str(ws), "--mode", "research_review", "--json"]) == 1

    captured = capsys.readouterr()
    assert "human_approval_ledger_event_link_error:records[0].event_missing" in captured.out
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    ledger_record = registry["artifacts"]["human_approval_ledger"]
    assert ledger_record["status"] == "invalid"
    assert ledger_record["validation_result"] == (
        "human_approval_ledger_event_link_error:records[0].event_missing"
    )


def test_release_check_rejects_approval_event_metadata_mismatch(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "internal_management_review"]) == 0
    assert main([
        "approval",
        "record",
        "--workspace",
        str(ws),
        "--role",
        "content_owner",
        "--decision",
        "request_changes",
        "--reason",
        "Needs another review.",
    ]) == 0

    ledger_path = ws / "output" / "intermediate" / "human_approval_ledger.json"
    ledger = _json(ledger_path)
    ledger["records"][0]["decision"] = "approve"
    _write_json(ledger_path, ledger)

    assert main([
        "release",
        "check",
        "--workspace",
        str(ws),
        "--mode",
        "internal_management_review",
        "--json",
    ]) == 1

    captured = capsys.readouterr()
    assert "human_approval_ledger_event_link_error:records[0].event_metadata_mismatch" in captured.out


def test_approval_record_rejects_forged_initialized_mode_event_id(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "research_review"]) == 0

    ledger_path = ws / "output" / "intermediate" / "human_approval_ledger.json"
    ledger = _json(ledger_path)
    ledger["initialized_modes"]["research_review"]["event_id"] = "evt-forged-init"
    _write_json(ledger_path, ledger)

    assert main([
        "approval",
        "record",
        "--workspace",
        str(ws),
        "--mode",
        "research_review",
        "--role",
        "content_owner",
        "--decision",
        "approve",
        "--reason",
        "Should not record with forged init event.",
    ]) == 1

    captured = capsys.readouterr()
    assert "initialized_modes.research_review.event_missing" in captured.out
    after = _json(ledger_path)
    assert after["records"] == []


def test_release_readiness_report_forged_event_id_is_invalid_in_state_check(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "internal_draft"]) == 0
    assert main(["release", "check", "--workspace", str(ws), "--mode", "internal_draft"]) == 0

    report_path = ws / "output" / "intermediate" / "release_readiness_report.json"
    report = _json(report_path)
    report["event_id"] = "evt-forged-report"
    _write_json(report_path, report)

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    report_record = registry["artifacts"]["release_readiness_report"]
    assert report_record["status"] == "invalid"
    assert report_record["validation_result"] == "release_readiness_report_event_link_error:event_missing"


def test_approval_ledger_missing_event_id_is_invalid(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "research_review"]) == 0
    assert main([
        "approval",
        "record",
        "--workspace",
        str(ws),
        "--role",
        "content_owner",
        "--decision",
        "approve",
        "--reason",
        "Content owner approved.",
    ]) == 0

    ledger_path = ws / "output" / "intermediate" / "human_approval_ledger.json"
    ledger = _json(ledger_path)
    ledger["records"][0].pop("event_id")
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert validate_human_approval_ledger_payload(ledger) == (
        "human_approval_ledger_schema_error:records[0].event_id"
    )


def test_release_readiness_report_missing_event_id_is_invalid(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "internal_draft"]) == 0
    assert main(["release", "check", "--workspace", str(ws), "--mode", "internal_draft"]) == 0

    report = _json(ws / "output" / "intermediate" / "release_readiness_report.json")
    report.pop("event_id")

    assert validate_release_readiness_report_payload(report) == (
        "release_readiness_report_schema_error:event_id"
    )


def test_approval_record_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "research_review"]) == 0
    ledger_path = ws / "output" / "intermediate" / "human_approval_ledger.json"
    before_ledger = ledger_path.read_bytes()
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    before_event_log = event_log.read_bytes()

    def fail_append_event(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("forced append failure")

    monkeypatch.setattr(
        "multi_agent_brief.product.release_approval.append_event",
        fail_append_event,
    )

    try:
        record_human_approval(
            workspace=ws,
            mode="research_review",
            role="content_owner",
            decision="approve",
            reason="Should rollback.",
        )
    except RuntimeError as exc:
        assert "forced append failure" in str(exc)
    else:
        raise AssertionError("record_human_approval should have failed")

    assert ledger_path.read_bytes() == before_ledger
    assert event_log.read_bytes() == before_event_log


def test_release_check_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    assert main(["approval", "init", "--workspace", str(ws), "--mode", "internal_draft"]) == 0
    report_path = ws / "output" / "intermediate" / "release_readiness_report.json"
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    before_event_log = event_log.read_bytes()

    def fail_append_event(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("forced append failure")

    monkeypatch.setattr(
        "multi_agent_brief.product.release_approval.append_event",
        fail_append_event,
    )

    try:
        check_release_readiness(workspace=ws, mode="internal_draft")
    except RuntimeError as exc:
        assert "forced append failure" in str(exc)
    else:
        raise AssertionError("check_release_readiness should have failed")

    assert not report_path.exists()
    assert event_log.read_bytes() == before_event_log


def test_release_approval_artifacts_are_optional_and_validated_by_state_check(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    state_before = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    assert state_before["artifacts"]["human_approval_ledger"]["status"] == "expected"
    assert state_before["artifacts"]["human_approval_ledger"]["required"] is False
    assert state_before["artifacts"]["release_readiness_report"]["status"] == "expected"
    assert state_before["artifacts"]["release_readiness_report"]["required"] is False

    assert main(["approval", "init", "--workspace", str(ws), "--mode", "internal_draft"]) == 0
    assert main(["release", "check", "--workspace", str(ws), "--mode", "internal_draft"]) == 0
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    state_after = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    ledger_record = state_after["artifacts"]["human_approval_ledger"]
    report_record = state_after["artifacts"]["release_readiness_report"]
    assert ledger_record["status"] == "valid"
    assert ledger_record["validation_result"] == "experimental_human_approval_ledger"
    assert report_record["status"] == "valid"
    assert report_record["validation_result"] == "experimental_release_readiness_report"
