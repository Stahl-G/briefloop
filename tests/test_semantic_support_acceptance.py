"""Tests for human adjudication records for semantic support proposals."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state.event_log import read_event_log_records_strict
from multi_agent_brief.orchestrator.runtime_state.semantic_support_acceptance import (
    SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY,
    semantic_support_acceptance_ledger_path,
)
from tests.test_quality_panel import _workspace, _write_semantic_support_artifacts


def _artifact_status(ws: Path, capsys) -> dict:
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    state = json.loads(capsys.readouterr().out)
    return state["artifact_registry"]["artifacts"]["semantic_support_acceptance_ledger"]


def _record_acceptance(ws: Path, capsys, *, decision: str = "accept") -> None:
    assert (
        main(
            [
                "semantic-support",
                "adjudicate",
                "--workspace",
                str(ws),
                "--proposal-id",
                "SAR-0001",
                "--decision",
                decision,
                "--reason",
                "Human reviewer adjudicated this proposal.",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()


def _adjudicate_rc(ws: Path, capsys) -> tuple[int, dict]:
    rc = main(
        [
            "semantic-support",
            "adjudicate",
            "--workspace",
            str(ws),
            "--proposal-id",
            "SAR-0001",
            "--decision",
            "accept",
            "--reason",
            "Human reviewer adjudicated this proposal.",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    return rc, payload


def test_semantic_support_adjudicate_records_acceptance_without_authority(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()

    rc = main(
        [
            "semantic-support",
            "adjudicate",
            "--workspace",
            str(ws),
            "--proposal-id",
            "SAR-0001",
            "--decision",
            "accept",
            "--reason",
            "Human reviewer agrees this is an overstatement risk.",
            "--by",
            "evidence_reviewer",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["proposal_id"] == "SAR-0001"
    assert payload["decision"] == "accept"
    assert payload["boundary"] == SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY
    assert all(value is False for value in payload["authority_effects"].values())

    ledger = json.loads(semantic_support_acceptance_ledger_path(ws).read_text(encoding="utf-8"))
    assert ledger["boundary"] == SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY
    assert len(ledger["records"]) == 1
    record = ledger["records"][0]
    assert record["proposal_id"] == "SAR-0001"
    assert record["claim_id"] == "CL-0001"
    assert record["atom_id"] == "AC-0001-01"
    assert record["decision"] == "accept"
    assert all(value is False for value in record["authority_effects"].values())
    assert not (ws / "output" / "intermediate" / "claim_support_matrix.json").exists()
    assert not (ws / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json").exists()
    assert not (ws / "output" / "delivery").exists()

    events = read_event_log_records_strict(ws / "output" / "intermediate" / "event_log.jsonl")
    event = events[-1]
    assert event["event_type"] == "semantic_support_finding_adjudicated"
    assert event["decision"] == "accept"
    assert event["metadata"]["proposal_id"] == "SAR-0001"
    assert event["metadata"]["boundary"] == SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY

    artifact = _artifact_status(ws, capsys)
    assert artifact["status"] == "valid"
    assert artifact["validation_result"] == "experimental_semantic_support_acceptance_ledger"


def test_semantic_support_adjudicate_rejects_unknown_proposal_without_writes(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    event_path = ws / "output" / "intermediate" / "event_log.jsonl"
    before_events = event_path.read_text(encoding="utf-8") if event_path.exists() else ""

    rc = main(
        [
            "semantic-support",
            "adjudicate",
            "--workspace",
            str(ws),
            "--proposal-id",
            "SAR-MISSING",
            "--decision",
            "accept",
            "--reason",
            "wrong proposal",
            "--json",
        ]
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_ARTIFACT_INVALID"
    assert "Semantic support proposal not found" in payload["error"]
    assert not semantic_support_acceptance_ledger_path(ws).exists()
    assert (event_path.read_text(encoding="utf-8") if event_path.exists() else "") == before_events


def test_semantic_support_adjudicate_rejects_missing_event_log_without_writes(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    event_path = ws / "output" / "intermediate" / "event_log.jsonl"
    event_path.unlink()

    rc, payload = _adjudicate_rc(ws, capsys)

    assert rc == 1
    assert payload["error_code"] == "E_TRANSACTION_INTEGRITY"
    assert "event_log.jsonl is required" in payload["error"]
    assert not semantic_support_acceptance_ledger_path(ws).exists()
    assert not event_path.exists()


def test_semantic_support_adjudicate_rejects_invalid_event_log_without_writes(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    event_path = ws / "output" / "intermediate" / "event_log.jsonl"
    before = "{bad json}\n"
    event_path.write_text(before, encoding="utf-8")

    rc, payload = _adjudicate_rc(ws, capsys)

    assert rc == 1
    assert payload["error_code"] == "E_TRANSACTION_INTEGRITY"
    assert "Invalid JSON event log line" in payload["error"]
    assert not semantic_support_acceptance_ledger_path(ws).exists()
    assert event_path.read_text(encoding="utf-8") == before


def test_semantic_support_adjudicate_rejects_non_newline_event_log_without_writes(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    event_path = ws / "output" / "intermediate" / "event_log.jsonl"
    before = event_path.read_text(encoding="utf-8").rstrip("\n")
    event_path.write_text(before, encoding="utf-8")

    rc, payload = _adjudicate_rc(ws, capsys)

    assert rc == 1
    assert payload["error_code"] == "E_TRANSACTION_INTEGRITY"
    assert "Event log is not newline-terminated" in payload["error"]
    assert not semantic_support_acceptance_ledger_path(ws).exists()
    assert event_path.read_text(encoding="utf-8") == before


def test_semantic_support_acceptance_ledger_rejects_authority_forgery(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys, decision="reject")
    path = semantic_support_acceptance_ledger_path(ws)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["accepted_support_truth"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    artifact = _artifact_status(ws, capsys)
    assert artifact["status"] == "invalid"
    assert (
        artifact["validation_result"]
        == "semantic_support_acceptance_ledger_schema_error:forbidden_authority_key:accepted_support_truth"
    )


def test_semantic_support_acceptance_ledger_rejects_record_authority_forgery(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys)
    path = semantic_support_acceptance_ledger_path(ws)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["accepted_support_truth"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    artifact = _artifact_status(ws, capsys)

    assert artifact["status"] == "invalid"
    assert (
        artifact["validation_result"]
        == "semantic_support_acceptance_ledger_schema_error:records[0].forbidden_authority_key:accepted_support_truth"
    )


def test_semantic_support_acceptance_ledger_rejects_nested_authority_forgery(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys)
    path = semantic_support_acceptance_ledger_path(ws)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["authority_effects"]["release_authority"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    artifact = _artifact_status(ws, capsys)

    assert artifact["status"] == "invalid"
    assert (
        artifact["validation_result"]
        == "semantic_support_acceptance_ledger_schema_error:records[0].authority_effects.unknown_key:release_authority"
    )


def test_semantic_support_acceptance_ledger_rejects_fake_event_id(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys)
    path = semantic_support_acceptance_ledger_path(ws)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["event_id"] = "evt-forged-does-not-exist"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    artifact = _artifact_status(ws, capsys)

    assert artifact["status"] == "invalid"
    assert (
        artifact["validation_result"]
        == "semantic_support_acceptance_ledger_schema_error:records[0].event_missing:evt-forged-does-not-exist"
    )


def test_semantic_support_acceptance_ledger_rejects_event_metadata_mismatch(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys)
    event_path = ws / "output" / "intermediate" / "event_log.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    events[-1]["metadata"]["proposal_id"] = "SAR-FORGED"
    event_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )

    artifact = _artifact_status(ws, capsys)

    assert artifact["status"] == "invalid"
    assert (
        artifact["validation_result"]
        == "semantic_support_acceptance_ledger_schema_error:records[0].event_metadata_mismatch:proposal_id"
    )


def test_semantic_support_acceptance_ledger_rejects_edited_decision(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys, decision="accept")
    path = semantic_support_acceptance_ledger_path(ws)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["decision"] = "reject"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    artifact = _artifact_status(ws, capsys)

    assert artifact["status"] == "invalid"
    assert (
        artifact["validation_result"]
        == "semantic_support_acceptance_ledger_schema_error:records[0].event_decision_mismatch"
    )
