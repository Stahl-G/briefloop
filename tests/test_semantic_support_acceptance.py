"""Tests for human adjudication records for semantic support proposals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main as cli_main
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    check_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import read_event_log_records_strict
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    SEMANTIC_ASSESSMENT_CHECKED_INPUTS,
    build_semantic_assessment_checked_inputs,
    project_semantic_assessment_report_from_workspace,
)
from multi_agent_brief.orchestrator.runtime_state.semantic_support_acceptance import (
    SEMANTIC_SUPPORT_ACCEPTANCE_BOUNDARY,
    bind_semantic_assessment_checked_inputs_transaction,
    record_semantic_support_adjudication,
    semantic_support_acceptance_ledger_path,
    semantic_support_acceptance_record_current_effectiveness,
)
from tests.test_quality_panel import _workspace, _write_semantic_support_artifacts


def _arg(argv: list[str], name: str, *, default: str | None = None) -> str | None:
    try:
        return argv[argv.index(name) + 1]
    except ValueError:
        return default


def main(argv: list[str]) -> int:
    """Exercise retired semantic-support modules directly, never the public CLI guard.

    LEGACY-DELETE removes this bridge with the legacy semantic-support
    acceptance module tests.  Until then it preserves their deterministic
    invariants without claiming that JSON control state remains a supported
    public authority.
    """

    workspace = _arg(argv, "--workspace")
    assert workspace is not None
    try:
        if argv[:2] == ["semantic-support", "bind"]:
            payload = bind_semantic_assessment_checked_inputs_transaction(
                workspace=workspace,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if argv[:2] == ["semantic-support", "adjudicate"]:
            payload = record_semantic_support_adjudication(
                workspace=workspace,
                proposal_id=str(_arg(argv, "--proposal-id")),
                decision=str(_arg(argv, "--decision")),
                reason=str(_arg(argv, "--reason")),
                actor_id=str(_arg(argv, "--by") or "human"),
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if argv[:2] == ["state", "check"]:
            state = check_runtime_state(workspace=workspace)
            print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    except (RuntimeStateError, OSError, json.JSONDecodeError) as exc:
        payload = (
            exc.to_dict()
            if isinstance(exc, RuntimeStateError)
            else {"ok": False, "error": str(exc)}
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    raise AssertionError(f"unsupported direct legacy module call: {argv[:2]}")


def _snapshot_workspace_files(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }


def _artifact_status(ws: Path, capsys) -> dict:
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    state = json.loads(capsys.readouterr().out)
    return state["artifact_registry"]["artifacts"]["semantic_support_acceptance_ledger"]


def _write_fresh_semantic_support_artifacts(ws: Path) -> None:
    _write_semantic_support_artifacts(ws)
    intermediate = ws / "output" / "intermediate"
    (intermediate / "audited_brief.md").write_text("# Audited Brief\n\nTargetCo opened a demo facility.\n", encoding="utf-8")
    report_path = intermediate / "semantic_assessment_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["checked_inputs"] = build_semantic_assessment_checked_inputs(ws)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_unbound_semantic_support_artifacts(ws: Path, *, checked_inputs_value: object = ...):
    _write_fresh_semantic_support_artifacts(ws)
    report_path = ws / "output" / "intermediate" / "semantic_assessment_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if checked_inputs_value is ...:
        report.pop("checked_inputs", None)
    else:
        report["checked_inputs"] = checked_inputs_value
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


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


def _bind_rc(ws: Path, capsys) -> tuple[int, dict]:
    rc = main(
        [
            "semantic-support",
            "bind",
            "--workspace",
            str(ws),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    return rc, payload


def test_semantic_support_adjudicate_records_acceptance_without_authority(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_fresh_semantic_support_artifacts(ws)
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
    assert record["semantic_assessment_report_sha256"]
    assert record["checked_inputs_digest"]
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
    assert event["metadata"]["semantic_assessment_report_sha256"] == record["semantic_assessment_report_sha256"]
    assert event["metadata"]["checked_inputs_digest"] == record["checked_inputs_digest"]

    artifact = _artifact_status(ws, capsys)
    assert artifact["status"] == "valid"
    assert artifact["validation_result"] == "experimental_semantic_support_acceptance_ledger"


def test_semantic_support_bind_binds_legacy_auditor_report_before_acceptance(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    report_path = _write_unbound_semantic_support_artifacts(ws)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "checked_inputs" not in report
    capsys.readouterr()

    rc, payload = _bind_rc(ws, capsys)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["bound"] is True
    bound_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(bound_report["checked_inputs"]) == set(SEMANTIC_ASSESSMENT_CHECKED_INPUTS)
    projection = project_semantic_assessment_report_from_workspace(ws)
    assert projection["status"] == "valid"
    assert projection["checked_inputs_status"] == "fresh"
    assert payload["semantic_assessment_report_sha256"] == projection["report_sha256"]
    assert payload["checked_inputs_digest"] == projection["checked_inputs_digest"]
    events = read_event_log_records_strict(ws / "output" / "intermediate" / "event_log.jsonl")
    assert events[-1]["event_type"] == "semantic_assessment_checked_inputs_bound"
    assert events[-1]["metadata"]["checked_inputs_digest"] == projection["checked_inputs_digest"]

    rc2, payload2 = _bind_rc(ws, capsys)
    assert rc2 == 0
    assert payload2["bound"] is False
    assert payload2["reason"] == "already_bound"

    rc, payload = _adjudicate_rc(ws, capsys)
    assert rc == 0
    assert payload["ok"] is True
    ledger = json.loads(semantic_support_acceptance_ledger_path(ws).read_text(encoding="utf-8"))
    record = ledger["records"][0]
    assert record["semantic_assessment_report_sha256"] == projection["report_sha256"]
    assert record["checked_inputs_digest"] == projection["checked_inputs_digest"]


def test_semantic_support_bind_binds_null_checked_inputs_before_acceptance(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    report_path = _write_unbound_semantic_support_artifacts(ws, checked_inputs_value=None)
    capsys.readouterr()

    rc, payload = _bind_rc(ws, capsys)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["bound"] is True
    bound_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert isinstance(bound_report["checked_inputs"], dict)
    assert set(bound_report["checked_inputs"]) == set(SEMANTIC_ASSESSMENT_CHECKED_INPUTS)
    projection = project_semantic_assessment_report_from_workspace(ws)
    assert projection["checked_inputs_status"] == "fresh"


def test_semantic_support_bind_captures_snapshot_before_later_repair(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    report_path = _write_unbound_semantic_support_artifacts(ws)
    capsys.readouterr()

    rc, payload = _bind_rc(ws, capsys)
    assert rc == 0
    assert payload["bound"] is True
    sealed = json.loads(report_path.read_text(encoding="utf-8"))
    sealed_digest = payload["checked_inputs_digest"]

    (ws / "output" / "intermediate" / "audited_brief.md").write_text("# Repaired after audit\n", encoding="utf-8")

    projection = project_semantic_assessment_report_from_workspace(ws)
    assert projection["status"] == "stale"
    assert projection["checked_inputs_status"] == "stale"
    assert projection["checked_inputs_digest"] == sealed_digest
    assert json.loads(report_path.read_text(encoding="utf-8")) == sealed


def test_semantic_support_adjudicate_rejects_unbound_report_without_writes(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    report_path = _write_unbound_semantic_support_artifacts(ws)
    before_report = report_path.read_bytes()
    event_path = ws / "output" / "intermediate" / "event_log.jsonl"
    before_events = event_path.read_bytes()
    capsys.readouterr()

    rc, payload = _adjudicate_rc(ws, capsys)

    assert rc == 1
    assert payload["error_code"] == "E_ARTIFACT_INVALID"
    assert payload["details"]["checked_inputs_status"] == "missing_checked_inputs"
    assert report_path.read_bytes() == before_report
    assert event_path.read_bytes() == before_events
    assert not semantic_support_acceptance_ledger_path(ws).exists()


def test_semantic_support_acceptance_record_current_effective_tracks_sar_and_inputs(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    _write_fresh_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys)
    path = semantic_support_acceptance_ledger_path(ws)
    ledger = json.loads(path.read_text(encoding="utf-8"))
    record = ledger["records"][0]

    assert semantic_support_acceptance_record_current_effectiveness(record, workspace=ws) == {
        "current_effective": True,
        "reason": None,
    }

    (ws / "output" / "intermediate" / "audited_brief.md").write_text("# Edited after adjudication\n", encoding="utf-8")

    effectiveness = semantic_support_acceptance_record_current_effectiveness(record, workspace=ws)
    assert effectiveness["current_effective"] is False
    assert effectiveness["reason"] == "checked_input_stale:audited_brief"


def test_semantic_support_acceptance_record_current_effective_tracks_report_sha(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    _write_fresh_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys)
    path = semantic_support_acceptance_ledger_path(ws)
    ledger = json.loads(path.read_text(encoding="utf-8"))
    record = ledger["records"][0]
    report_path = ws / "output" / "intermediate" / "semantic_assessment_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.setdefault("metadata", {})["post_adjudication_edit"] = True
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    effectiveness = semantic_support_acceptance_record_current_effectiveness(record, workspace=ws)

    assert effectiveness["current_effective"] is False
    assert effectiveness["reason"] == "semantic_assessment_report_sha256_changed"


def test_semantic_support_adjudicate_rejects_unknown_proposal_without_writes(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_fresh_semantic_support_artifacts(ws)
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
    _write_fresh_semantic_support_artifacts(ws)
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
    _write_fresh_semantic_support_artifacts(ws)
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
    _write_fresh_semantic_support_artifacts(ws)
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
    _write_fresh_semantic_support_artifacts(ws)
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
    _write_fresh_semantic_support_artifacts(ws)
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
    _write_fresh_semantic_support_artifacts(ws)
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
    _write_fresh_semantic_support_artifacts(ws)
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
    _write_fresh_semantic_support_artifacts(ws)
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
    _write_fresh_semantic_support_artifacts(ws)
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


@pytest.mark.parametrize(
    "field",
    ["semantic_assessment_report_sha256", "checked_inputs_digest"],
)
def test_semantic_support_acceptance_ledger_rejects_stripped_event_linkage_field(
    tmp_path: Path,
    capsys,
    field: str,
) -> None:
    ws = _workspace(tmp_path / field)
    _write_fresh_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys)
    path = semantic_support_acceptance_ledger_path(ws)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["records"][0][field]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    artifact = _artifact_status(ws, capsys)

    assert artifact["status"] == "invalid"
    assert (
        artifact["validation_result"]
        == f"semantic_support_acceptance_ledger_schema_error:records[0].event_metadata_mismatch:{field}"
    )


def test_semantic_support_acceptance_ledger_requires_current_bound_sar_for_linked_records(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    _write_fresh_semantic_support_artifacts(ws)
    capsys.readouterr()
    _record_acceptance(ws, capsys)
    report_path = ws / "output" / "intermediate" / "semantic_assessment_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.pop("checked_inputs")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    artifact = _artifact_status(ws, capsys)

    assert artifact["status"] == "invalid"
    assert artifact["validation_result"] == (
        "semantic_support_acceptance_ledger_schema_error:"
        "records[0].current_effectiveness:missing_checked_inputs"
    )


def test_semantic_support_public_cli_retired_rejects_typed_without_writes(
    tmp_path: Path,
    capsys,
) -> None:
    # LEGACY-DELETE: retired public `semantic-support` CLI surface; the
    # authority guard answers a typed token and performs zero writes.
    ws = _workspace(tmp_path)
    _write_fresh_semantic_support_artifacts(ws)
    capsys.readouterr()

    for argv in (
        ["semantic-support", "bind", "--workspace", str(ws), "--json"],
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
        ],
    ):
        before = _snapshot_workspace_files(ws)
        rc = cli_main(argv)
        assert rc == 1
        assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
        assert _snapshot_workspace_files(ws) == before
        assert not semantic_support_acceptance_ledger_path(ws).exists()
