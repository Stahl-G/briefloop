from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.delivery.base import DeliveryResult
from multi_agent_brief.orchestrator.runtime_state import RuntimeStateError, initialize_runtime_state, runtime_state_paths
from tests.helpers import sha256_file as _sha256_file
from tests.helpers import write_minimal_workspace


def _workspace(tmp_path: Path) -> Path:
    return write_minimal_workspace(
        tmp_path / "ws",
        project_name="Deliver Test",
        user_text="# Deliver test\n",
    )


def _write_bundle(
    ws: Path,
    *,
    reader_clean_status: str = "pass",
    include_docx: bool = True,
    delivery_artifacts: list[str] | None = None,
    init_runtime: bool = True,
) -> tuple[Path, Path | None]:
    if init_runtime:
        initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    delivery = ws / "output" / "delivery"
    intermediate = ws / "output" / "intermediate"
    delivery.mkdir(parents=True, exist_ok=True)
    intermediate.mkdir(parents=True, exist_ok=True)
    markdown = delivery / "brief.md"
    markdown.write_text("# Final Brief\n\nSource Appendix\n", encoding="utf-8")
    docx = delivery / "Weekly_Brief_2026-06-12.docx"
    if include_docx:
        docx_module = pytest.importorskip("docx", reason="python-docx not installed")
        document = docx_module.Document()
        document.add_paragraph("Final Brief")
        document.add_paragraph("Source Appendix")
        document.save(str(docx))
    else:
        docx = None
    artifact_paths = delivery_artifacts
    if artifact_paths is None:
        artifact_paths = [str(markdown)]
        if docx is not None:
            artifact_paths.append(str(docx))
    artifact_hashes = {
        artifact: _sha256_file(Path(artifact))
        for artifact in artifact_paths
        if Path(artifact).exists()
    }
    report = {
        "status": "pass",
        "reader_clean": {"status": reader_clean_status, "sample_findings": []},
        "delivery_artifacts": artifact_paths,
        "delivery_artifact_sha256": artifact_hashes,
    }
    (intermediate / "finalize_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (ws / "output" / "source_appendix.md").write_text("# Audit copy\n", encoding="utf-8")
    (intermediate / "claim_ledger.json").write_text("[]\n", encoding="utf-8")
    (intermediate / "audit_report.json").write_text("{}\n", encoding="utf-8")
    return markdown, docx


def _delivery_events(ws: Path) -> list[dict[str, object]]:
    path = ws / "output" / "intermediate" / "event_log.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event_type", "").startswith("delivery_")
    ]


def _mark_run_contaminated(ws: Path) -> None:
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [
            {
                "reason_code": "run_reset",
                "message": "run_reset occurred; this run is not clean single-shot reference evidence.",
                "created_at": "2026-06-13T00:00:00+00:00",
            }
        ],
    }
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mark_active_repair(ws: Path) -> None:
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["active_repair"] = {
        "schema_version": "mabw.active_repair.v1",
        "transaction_id": "repair-test-001",
        "repair_owner": "editor",
        "allowed_artifacts": ["output/intermediate/audited_brief.md"],
        "blocked_direct_edits": ["output/intermediate/claim_ledger.json"],
        "must_rerun_from": "auditor",
    }
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_deliver_local_lists_only_delivery_bundle(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)

    rc = main(["deliver", "--workspace", str(ws), "--target", "local"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "output/delivery/brief.md" in out
    assert "output/delivery/Weekly_Brief_2026-06-12.docx" in out
    assert "source_appendix.md" not in out
    assert "claim_ledger.json" not in out
    assert "audit_report.json" not in out
    events = _delivery_events(ws)
    assert [event["event_type"] for event in events] == ["delivery_attempted", "delivery_succeeded"]
    assert events[0]["metadata"]["artifact"] == "output/delivery/brief.md"


def test_deliver_local_fails_without_events_when_active_repair_open(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)
    _mark_active_repair(ws)

    rc = main(["deliver", "--workspace", str(ws), "--target", "local", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_ACTIVE_REPAIR_OPEN"
    assert payload["runtime_error_code"] == "E_ACTIVE_REPAIR_OPEN"
    assert "repair complete" in payload["message"]
    assert _delivery_events(ws) == []


def test_deliver_local_allows_contaminated_run_but_reports_integrity(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)
    _mark_run_contaminated(ws)

    rc = main(["deliver", "--workspace", str(ws), "--target", "local", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["run_integrity"]["status"] == "contaminated"
    assert payload["run_integrity"]["reference_eligible"] is False

    rc = main(["deliver", "--workspace", str(ws), "--target", "local"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "Delivery bundle ready" in captured.out
    assert "Run integrity: contaminated" in captured.err
    assert "Reference eligible: no" in captured.err


def test_deliver_json_returns_typed_error_for_corrupt_workflow_state(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)
    runtime_state_paths(ws)["workflow_state"].write_text("{broken", encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--target", "local", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_DELIVERY_EVENT_FAILED"
    assert "workflow_state.json is unreadable" in payload["message"]


def test_deliver_json_reports_malformed_run_integrity_as_unknown(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["run_integrity"] = "bad"
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--target", "local", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_integrity"]["status"] == "unknown"
    assert payload["run_integrity"]["reference_eligible"] is False
    assert payload["run_integrity"]["reasons"][0]["reason_code"] == "run_integrity_malformed"


def test_deliver_missing_bundle_returns_typed_error(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main(["deliver", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_DELIVERY_BUNDLE_MISSING"
    assert "finalize" in payload["message"]


def test_deliver_rejects_dirty_finalize_report(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, reader_clean_status="fail", init_runtime=False)

    rc = main(["deliver", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_NOT_CLEAN"
    assert not (ws / "output" / "intermediate" / "event_log.jsonl").exists()


def test_deliver_rejects_dirty_current_delivery_artifact(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    markdown, _docx = _write_bundle(ws, include_docx=False)
    markdown.write_text("# Final Brief\n\nLeaked marker [src:CLAIM-001]\n", encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_ARTIFACT_MISMATCH"
    assert _delivery_events(ws) == []


def test_deliver_rejects_clean_markdown_changed_after_finalize(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    markdown, _docx = _write_bundle(ws, include_docx=False)
    markdown.write_text("# Different Clean Brief\n\nSource Appendix\n", encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--target", "local", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_ARTIFACT_MISMATCH"
    assert payload["artifact"] == "output/delivery/brief.md"
    assert "Run finalize again" in payload["message"]
    assert _delivery_events(ws) == []


def test_deliver_rejects_clean_docx_changed_after_finalize(tmp_path: Path, capsys) -> None:
    docx_module = pytest.importorskip("docx", reason="python-docx not installed")
    ws = _workspace(tmp_path)
    _markdown, docx = _write_bundle(ws, include_docx=True)
    assert docx is not None
    document = docx_module.Document()
    document.add_paragraph("Different clean DOCX")
    document.add_paragraph("Source Appendix")
    document.save(str(docx))

    rc = main(["deliver", "--workspace", str(ws), "--target", "local", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_ARTIFACT_MISMATCH"
    assert payload["artifact"].endswith(".docx")
    assert "Run finalize again" in payload["message"]
    assert _delivery_events(ws) == []


def test_deliver_rejects_missing_delivery_hashes(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.pop("delivery_artifact_sha256")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_BUNDLE_MISSING"
    assert "delivery_artifact_sha256" in payload["message"]
    assert _delivery_events(ws) == []


def test_deliver_requires_existing_runtime_state(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False, init_runtime=False)

    rc = main(["deliver", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_RUNTIME_MISSING"
    assert payload["runtime_error_code"] == "E_RUNTIME_STATE_NOT_INITIALIZED"
    assert not (ws / "output" / "intermediate" / "event_log.jsonl").exists()


def test_deliver_rejects_non_delivery_artifact_in_report(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    bad_path = ws / "output" / "source_appendix.md"
    _write_bundle(ws, delivery_artifacts=[str(bad_path)])

    rc = main(["deliver", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_BUNDLE_MISSING"


def test_deliver_feishu_doc_sends_delivery_markdown_and_sanitizes_events(tmp_path: Path, capsys, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    markdown, _docx = _write_bundle(ws)
    calls: list[tuple[str, str, str]] = []

    def fake_deliver(self, artifact, target):
        calls.append((artifact.path, target.channel, target.recipient))
        return DeliveryResult("feishu", True, "Doc created", {"url": "https://example.com/doc"})

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.FeishuDeliveryConnector.deliver", fake_deliver)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "feishu",
        "--channel",
        "doc",
        "--recipient",
        "folder_secret_token",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["artifact"] == "output/delivery/brief.md"
    assert payload["url"] == "https://example.com/doc"
    assert calls == [(str(markdown), "doc", "folder_secret_token")]
    event_blob = json.dumps(_delivery_events(ws), ensure_ascii=False)
    assert "folder_secret_token" not in event_blob
    assert '"recipient_present": true' in event_blob


def test_deliver_feishu_drive_prefers_named_docx(tmp_path: Path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    _markdown, docx = _write_bundle(ws)
    calls: list[str] = []

    def fake_deliver(self, artifact, target):
        calls.append(artifact.path)
        return DeliveryResult("feishu", True, "Uploaded")

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.FeishuDeliveryConnector.deliver", fake_deliver)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "feishu",
        "--channel",
        "drive",
        "--recipient",
        "folder_secret_token",
        "--json",
    ])

    assert rc == 0
    assert calls == [str(docx)]


def test_deliver_feishu_failure_records_failed_event(tmp_path: Path, capsys, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)

    def fake_deliver(self, artifact, target):
        return DeliveryResult(
            "feishu",
            False,
            "feishu failed for oc_secret_chat and folder token abcdefghijklmnopqrstuvwxyz123456",
        )

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.FeishuDeliveryConnector.deliver", fake_deliver)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "feishu",
        "--channel",
        "chat",
        "--recipient",
        "oc_secret_chat",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_FAILED"
    assert "oc_secret_chat" not in payload["message"]
    assert "abcdefghijklmnopqrstuvwxyz123456" not in payload["message"]
    assert "[recipient]" in payload["message"]
    assert "[token]" in payload["message"]
    events = _delivery_events(ws)
    assert [event["event_type"] for event in events] == ["delivery_attempted", "delivery_failed"]
    assert "oc_secret_chat" not in json.dumps(events, ensure_ascii=False)


def test_deliver_feishu_success_with_success_event_failure_reports_delivered(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)

    def fake_deliver(self, artifact, target):
        return DeliveryResult("feishu", True, "Doc created", {"url": "https://example.com/doc"})

    real_append_event = __import__(
        "multi_agent_brief.cli.deliver_commands",
        fromlist=["append_event"],
    ).append_event

    def flaky_append_event(**kwargs):
        if kwargs.get("event_type") == "delivery_succeeded":
            raise RuntimeStateError("event write failed")
        return real_append_event(**kwargs)

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.FeishuDeliveryConnector.deliver", fake_deliver)
    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.append_event", flaky_append_event)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "feishu",
        "--channel",
        "doc",
        "--recipient",
        "folder_secret_token",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["delivered"] is True
    assert payload["event_recorded"] is False
    assert "event write failed" in payload["event_error"]
    assert [event["event_type"] for event in _delivery_events(ws)] == ["delivery_attempted"]


def test_deliver_feishu_success_event_failure_warns_in_text_output(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)

    def fake_deliver(self, artifact, target):
        return DeliveryResult("feishu", True, "Doc created", {"url": "https://example.com/doc"})

    real_append_event = __import__(
        "multi_agent_brief.cli.deliver_commands",
        fromlist=["append_event"],
    ).append_event

    def flaky_append_event(**kwargs):
        if kwargs.get("event_type") == "delivery_succeeded":
            raise RuntimeStateError("event write failed")
        return real_append_event(**kwargs)

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.FeishuDeliveryConnector.deliver", fake_deliver)
    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.append_event", flaky_append_event)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "feishu",
        "--channel",
        "doc",
        "--recipient",
        "folder_secret_token",
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "Delivered to feishu doc: https://example.com/doc" in captured.out
    assert "delivery_succeeded event was not recorded" in captured.err
    assert "do not retry blindly" in captured.err


def test_mabw_deliver_guidance_uses_delivery_command() -> None:
    text = Path(".claude/commands/mabw.md").read_text(encoding="utf-8")
    assert "multi-agent-brief deliver --workspace <workspace> --target local" in text
    assert "multi-agent-brief deliver --workspace <workspace> --target feishu" in text
    assert "delivery_artifacts" in text
    assert "do not send audit/control records" in text
    assert "`doctor` is not a writer verb" in text


def test_deliver_help_mentions_recipient_hash(capsys) -> None:
    try:
        main(["deliver", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "recipient_sha256" in capsys.readouterr().out
