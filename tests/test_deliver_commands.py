from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.delivery.base import DeliveryResult
from multi_agent_brief.delivery.gws import GwsGmailDeliveryConnector
from multi_agent_brief.orchestrator.runtime_state import RuntimeStateError, initialize_runtime_state, runtime_state_paths
from tests.helpers import sha256_file as _sha256_file
from tests.helpers import write_minimal_workspace


ROOT = Path(__file__).resolve().parents[1]


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
    manifest_path = intermediate / "delivery_manifest.json"
    manifest = {
        "schema_version": "briefloop.delivery_manifest.v1",
        "status": "promoted",
        "finalize_transaction_id": "test-finalize-transaction",
        "reader_clean_status": reader_clean_status,
        "delivery_dir": "output/delivery",
        "artifacts": [
            {
                "path": Path(artifact).resolve().relative_to(ws).as_posix()
                if Path(artifact).resolve().is_relative_to(ws)
                else artifact,
                "sha256": artifact_hashes.get(artifact, ""),
                "kind": "reader_docx" if str(artifact).endswith(".docx") else "reader_markdown",
            }
            for artifact in artifact_paths
            if Path(artifact).exists()
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "status": "pass",
        "reader_clean": {"status": reader_clean_status, "sample_findings": []},
        "delivery_artifacts": artifact_paths,
        "delivery_artifact_sha256": artifact_hashes,
        "delivery_manifest": "output/intermediate/delivery_manifest.json",
        "delivery_manifest_sha256": _sha256_file(manifest_path),
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


def test_deliver_local_blocks_contaminated_run_without_events(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)
    _mark_run_contaminated(ws)

    rc = main(["deliver", "--workspace", str(ws), "--target", "local", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_DELIVERY_RUN_INTEGRITY_BLOCKED"
    assert payload["run_integrity"]["status"] == "contaminated"
    assert payload["run_integrity"]["reference_eligible"] is False
    assert "run integrity is not clean" in payload["message"]
    assert _delivery_events(ws) == []

    rc = main(["deliver", "--workspace", str(ws), "--target", "local"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "Delivery bundle ready" not in captured.out
    assert "run integrity is not clean" in captured.err


def test_deliver_refreshes_run_integrity_before_recording_events(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    capsys.readouterr()
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["stage_statuses"]["claim-ledger"]["status"] = "complete"
    workflow["stage_statuses"]["claim-ledger"]["reason"] = "claim ledger frozen"
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger = ws / "output" / "intermediate" / "claim_ledger.json"
    ledger.write_text('[{"claim_id":"CL-TAMPERED"}]\n', encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--target", "local", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_RUN_INTEGRITY_BLOCKED"
    assert payload["runtime_error_code"] == "E_TRANSACTION_INTEGRITY"
    assert payload["run_integrity"]["status"] == "contaminated"
    assert payload["run_integrity"]["reference_eligible"] is False
    assert payload["run_integrity"]["reasons"][0]["reason_code"] == "frozen_artifact_changed"
    assert _delivery_events(ws) == []


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


def test_deliver_json_blocks_malformed_run_integrity_as_unknown(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["run_integrity"] = "bad"
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--target", "local", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_RUN_INTEGRITY_BLOCKED"
    assert payload["run_integrity"]["status"] == "unknown"
    assert payload["run_integrity"]["reference_eligible"] is False
    assert payload["run_integrity"]["reasons"][0]["reason_code"] == "run_integrity_malformed"
    assert _delivery_events(ws) == []


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


def test_deliver_rejects_missing_delivery_manifest(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.pop("delivery_manifest")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_BUNDLE_MISSING"
    assert "delivery_manifest" in payload["message"]
    assert _delivery_events(ws) == []


def test_deliver_rejects_tampered_delivery_manifest(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)
    manifest_path = ws / "output" / "intermediate" / "delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_ARTIFACT_MISMATCH"
    assert "delivery_manifest.json has changed" in payload["message"]
    assert _delivery_events(ws) == []


def test_deliver_rejects_invalid_utf8_delivery_manifest_without_traceback(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)
    manifest_path = ws / "output" / "intermediate" / "delivery_manifest.json"
    manifest_path.write_bytes(b"\xff\xfe{not-json")
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_manifest_sha256"] = _sha256_file(manifest_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    rc = main(["deliver", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_BUNDLE_MISSING"
    assert "delivery_manifest.json is not valid UTF-8 JSON" in payload["message"]
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


def test_deliver_gmail_draft_creates_draft_without_email_leak(tmp_path: Path, capsys, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    markdown, docx = _write_bundle(ws)
    markdown.write_text(
        "# Final Brief\n\n"
        "Reader-facing summary.\n\n"
        "# Source Appendix\n\n"
        "Appendix detail should not appear in the email body.\n",
        encoding="utf-8",
    )
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_artifact_sha256"][str(markdown)] = _sha256_file(markdown)
    manifest_path = ws / "output" / "intermediate" / "delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["sha256"] = _sha256_file(markdown)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["delivery_manifest_sha256"] = _sha256_file(manifest_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    calls: list[tuple[str, str, str, dict[str, object]]] = []

    def fake_deliver(self, artifact, target):
        calls.append((artifact.path, target.channel, target.recipient, dict(target.metadata)))
        return DeliveryResult("gmail", True, "Gmail draft created", {"draft_id_present": True})

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.GwsGmailDeliveryConnector.deliver", fake_deliver)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "gmail",
        "--channel",
        "draft",
        "--recipient",
        "recipient@example.com",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["target"] == "gmail"
    assert payload["channel"] == "draft"
    assert payload["delivered"] is False
    assert payload["draft_created"] is True
    assert payload["artifact"].endswith(".docx")
    assert calls == [
        (
            str(docx),
            "draft",
            "recipient@example.com",
            {
                "subject": "BriefLoop delivery: Final Brief",
                "body": (
                    "Please review the attached BriefLoop delivery.\n\n"
                    "Attachment: output/delivery/Weekly_Brief_2026-06-12.docx\n\n"
                    "Brief excerpt:\n"
                    "# Final Brief\n"
                    "Reader-facing summary.\n\n"
                    "Audit/control files are not attached."
                ),
                "attachments": [str(docx)],
                "markdown": str(ws / "output" / "delivery" / "brief.md"),
            },
        )
    ]
    events = _delivery_events(ws)
    assert [event["event_type"] for event in events] == ["delivery_attempted", "delivery_draft_created"]
    assert events[1]["metadata"]["draft_id_present"] is True
    event_blob = json.dumps(events, ensure_ascii=False)
    assert "recipient@example.com" not in event_blob
    assert "BriefLoop delivery" not in event_blob
    assert "Appendix detail" not in calls[0][3]["body"]
    assert '"recipient_present": true' in event_blob


def test_deliver_gmail_draft_event_failure_returns_error_without_retry_signal(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)

    def fake_deliver(self, artifact, target):
        return DeliveryResult("gmail", True, "Gmail draft created", {"draft_id_present": True})

    real_append_event = __import__(
        "multi_agent_brief.cli.deliver_commands",
        fromlist=["append_event"],
    ).append_event

    def flaky_append_event(**kwargs):
        if kwargs.get("event_type") == "delivery_draft_created":
            raise RuntimeStateError("event write failed")
        return real_append_event(**kwargs)

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.GwsGmailDeliveryConnector.deliver", fake_deliver)
    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.append_event", flaky_append_event)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "gmail",
        "--channel",
        "draft",
        "--recipient",
        "recipient@example.com",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_DELIVERY_EVENT_FAILED"
    assert payload["target"] == "gmail"
    assert payload["channel"] == "draft"
    assert payload["draft_created"] is True
    assert payload["event_recorded"] is False
    assert "event write failed" in payload["event_error"]
    assert "Gmail draft was created" in payload["message"]
    assert "Inspect Gmail Drafts" in payload["message"]
    assert "do not retry blindly" in payload["message"]
    assert [event["event_type"] for event in _delivery_events(ws)] == ["delivery_attempted"]


def test_deliver_gmail_send_sends_message_without_email_leak(tmp_path: Path, capsys, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    _markdown, docx = _write_bundle(ws)
    calls: list[tuple[str, str, str, dict[str, object]]] = []

    def fake_deliver(self, artifact, target):
        calls.append((artifact.path, target.channel, target.recipient, dict(target.metadata)))
        return DeliveryResult("gmail", True, "Gmail message sent", {"sent_message_present": True})

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.GwsGmailDeliveryConnector.deliver", fake_deliver)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "gmail",
        "--channel",
        "send",
        "--recipient",
        "recipient@example.com",
        "--subject",
        "Secret Q3 Board Plan",
        "--body",
        "Confidential launch narrative",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["target"] == "gmail"
    assert payload["channel"] == "send"
    assert payload["delivered"] is True
    assert payload["sent"] is True
    assert payload["draft_created"] is False
    assert payload["artifact"].endswith(".docx")
    assert calls == [
        (
            str(docx),
            "send",
            "recipient@example.com",
            {
                "subject": "Secret Q3 Board Plan",
                "body": "Confidential launch narrative",
                "attachments": [str(docx)],
                "markdown": str(ws / "output" / "delivery" / "brief.md"),
            },
        )
    ]
    events = _delivery_events(ws)
    assert [event["event_type"] for event in events] == ["delivery_attempted", "delivery_succeeded"]
    assert events[1]["metadata"]["sent_message_present"] is True
    event_blob = json.dumps(events, ensure_ascii=False)
    for secret in ("recipient@example.com", "Secret Q3 Board Plan", "Confidential launch narrative"):
        assert secret not in event_blob


def test_deliver_gmail_send_event_failure_returns_error_without_retry_signal(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws)

    def fake_deliver(self, artifact, target):
        return DeliveryResult("gmail", True, "Gmail message sent", {"sent_message_present": True})

    real_append_event = __import__(
        "multi_agent_brief.cli.deliver_commands",
        fromlist=["append_event"],
    ).append_event

    def flaky_append_event(**kwargs):
        if kwargs.get("event_type") == "delivery_succeeded":
            raise RuntimeStateError("event write failed")
        return real_append_event(**kwargs)

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.GwsGmailDeliveryConnector.deliver", fake_deliver)
    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.append_event", flaky_append_event)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "gmail",
        "--channel",
        "send",
        "--recipient",
        "recipient@example.com",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_DELIVERY_EVENT_FAILED"
    assert payload["target"] == "gmail"
    assert payload["channel"] == "send"
    assert payload["sent"] is True
    assert payload["draft_created"] is False
    assert payload["event_recorded"] is False
    assert "event write failed" in payload["event_error"]
    assert "Gmail message was sent" in payload["message"]
    assert "Inspect Gmail Sent Mail" in payload["message"]
    assert "do not retry blindly" in payload["message"]
    assert [event["event_type"] for event in _delivery_events(ws)] == ["delivery_attempted"]


def test_deliver_gmail_draft_uses_markdown_when_docx_missing(tmp_path: Path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    markdown, _docx = _write_bundle(ws, include_docx=False)
    calls: list[str] = []

    def fake_deliver(self, artifact, target):
        calls.append(artifact.path)
        return DeliveryResult("gmail", True, "Gmail draft created")

    monkeypatch.setattr("multi_agent_brief.cli.deliver_commands.GwsGmailDeliveryConnector.deliver", fake_deliver)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "gmail",
        "--channel",
        "draft",
        "--recipient",
        "recipient@example.com",
        "--json",
    ])

    assert rc == 0
    assert calls == [str(markdown)]


def test_deliver_gmail_draft_failure_scrubs_gws_subject_body_and_recipient(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)
    recipient = "recipient@example.com"
    subject = "Secret Q3 Board Plan"
    body = "Confidential launch narrative"

    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"oauth"}', "stderr": ""})()
        leaked = f"failed command contained {recipient} {subject} {body}"
        return type("Completed", (), {"returncode": 2, "stdout": leaked, "stderr": leaked})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "gmail",
        "--channel",
        "draft",
        "--recipient",
        recipient,
        "--subject",
        subject,
        "--body",
        body,
        "--json",
    ])

    assert rc == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_DELIVERY_FAILED"
    assert "gws draft creation failed" in payload["message"]
    output_blob = captured.out + captured.err
    event_blob = json.dumps(_delivery_events(ws), ensure_ascii=False)
    for secret in (recipient, subject, body):
        assert secret not in output_blob
        assert secret not in event_blob
    assert [event["event_type"] for event in _delivery_events(ws)] == ["delivery_attempted", "delivery_failed"]


def test_deliver_gmail_timeout_is_reported_as_unknown_outcome(tmp_path: Path, capsys, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)
    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"oauth"}', "stderr": ""})()
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "gmail",
        "--channel",
        "send",
        "--recipient",
        "recipient@example.com",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_DELIVERY_FAILED"
    assert payload["outcome_unknown"] is True
    assert payload["inspect_target"] == "Gmail Sent Mail"
    assert payload["sent"] is False
    assert "timed out" in payload["message"]
    assert "Inspect Gmail Sent Mail before retrying" in payload["message"]
    events = _delivery_events(ws)
    assert [event["event_type"] for event in events] == ["delivery_attempted", "delivery_failed"]
    assert events[1]["metadata"]["outcome_unknown"] is True
    assert events[1]["metadata"]["timeout"] is True
    assert events[1]["metadata"]["inspect_target"] == "Gmail Sent Mail"


def test_deliver_gmail_rejects_unknown_channel(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _write_bundle(ws, include_docx=False)

    rc = main([
        "deliver",
        "--workspace",
        str(ws),
        "--target",
        "gmail",
        "--channel",
        "chat",
        "--recipient",
        "recipient@example.com",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_DELIVERY_TARGET_INVALID"
    assert "draft|send" in payload["message"]
    assert _delivery_events(ws) == []


def test_gws_gmail_connector_creates_draft_with_attachment(monkeypatch, tmp_path: Path) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        calls.append((cmd, cwd))
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"oauth"}', "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": '{"id":"draft-123"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type(
            "Target",
            (),
            {
                "channel": "draft",
                "recipient": "recipient@example.com",
                "metadata": {
                    "subject": "Subject",
                    "body": "Body",
                    "attachments": [str(artifact)],
                },
            },
        )(),
    )

    assert result.delivered is True
    assert result.metadata == {"draft_id_present": True}
    send_cmd, send_cwd = calls[1]
    assert send_cwd == str(tmp_path)
    assert send_cmd[:7] == [
        "gws",
        "gmail",
        "+send",
        "--to",
        "recipient@example.com",
        "--subject",
        "Subject",
    ]
    assert "--body" in send_cmd
    assert "Body" in send_cmd
    assert "--draft" in send_cmd
    assert "--attach" in send_cmd
    assert str(artifact) not in send_cmd
    assert artifact.name in send_cmd


def test_gws_gmail_connector_sends_message_with_attachment(monkeypatch, tmp_path: Path) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        calls.append((cmd, cwd))
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"oauth"}', "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": '{"id":"msg-123"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type(
            "Target",
            (),
            {
                "channel": "send",
                "recipient": "recipient@example.com",
                "metadata": {
                    "subject": "Subject",
                    "body": "Body",
                    "attachments": [str(artifact)],
                },
            },
        )(),
    )

    assert result.delivered is True
    assert result.metadata == {"sent_message_present": True}
    send_cmd, send_cwd = calls[1]
    assert send_cwd == str(tmp_path)
    assert send_cmd[:7] == [
        "gws",
        "gmail",
        "+send",
        "--to",
        "recipient@example.com",
        "--subject",
        "Subject",
    ]
    assert "--body" in send_cmd
    assert "Body" in send_cmd
    assert "--draft" not in send_cmd
    assert "--attach" in send_cmd
    assert str(artifact) not in send_cmd
    assert artifact.name in send_cmd


def test_gws_gmail_connector_fails_without_send_confirmation(monkeypatch, tmp_path: Path) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"oauth"}', "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": '{"status":"ok"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type("Target", (), {"channel": "send", "recipient": "recipient@example.com", "metadata": {}})(),
    )

    assert result.delivered is False
    assert "did not confirm Gmail send" in result.message
    assert "Inspect Gmail Sent Mail" in result.message
    assert "do not retry blindly" in result.message


def test_gws_gmail_connector_fails_without_draft_confirmation(monkeypatch, tmp_path: Path) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"oauth"}', "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": '{"status":"ok"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type("Target", (), {"channel": "draft", "recipient": "recipient@example.com", "metadata": {}})(),
    )

    assert result.delivered is False
    assert "did not confirm Gmail draft creation" in result.message
    assert "do not retry blindly" in result.message


def test_gws_gmail_connector_allows_keyring_auth_status_prefix(monkeypatch, tmp_path: Path) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        if cmd == ["gws", "auth", "status"]:
            stdout = 'Using keyring backend: keyring\n{"auth_method":"oauth"}\n'
            return type("Completed", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": '{"id":"draft-123"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type("Target", (), {"channel": "draft", "recipient": "recipient@example.com", "metadata": {}})(),
    )

    assert result.delivered is True
    assert result.metadata == {"draft_id_present": True}


def test_gws_gmail_connector_allows_env_token_when_auth_status_reports_none(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLI_TOKEN", "example-env-auth")

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"none"}', "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": '{"id":"draft-123"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type("Target", (), {"channel": "draft", "recipient": "recipient@example.com", "metadata": {}})(),
    )

    assert result.delivered is True
    assert result.metadata == {"draft_id_present": True}


def test_gws_gmail_connector_allows_adc_env_when_auth_status_reports_none(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    adc = tmp_path / "adc.json"
    adc.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc))

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"none"}', "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": '{"id":"draft-123"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type("Target", (), {"channel": "draft", "recipient": "recipient@example.com", "metadata": {}})(),
    )

    assert result.delivered is True
    assert result.metadata == {"draft_id_present": True}


def test_gws_gmail_connector_allows_well_known_adc_when_auth_status_reports_none(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    adc_dir = tmp_path / "gcloud"
    adc_dir.mkdir()
    adc = adc_dir / "application_default_credentials.json"
    adc.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")
    monkeypatch.delenv("GOOGLE_WORKSPACE_CLI_TOKEN", raising=False)
    monkeypatch.delenv("GWS_TOKEN", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr("multi_agent_brief.delivery.gws._well_known_adc_paths", lambda: [adc])

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"none"}', "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": '{"id":"draft-123"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type("Target", (), {"channel": "draft", "recipient": "recipient@example.com", "metadata": {}})(),
    )

    assert result.delivered is True
    assert result.metadata == {"draft_id_present": True}


def test_gws_gmail_connector_lets_unparseable_auth_status_fall_through(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        if cmd == ["gws", "auth", "status"]:
            return type("Completed", (), {"returncode": 0, "stdout": "logged in as someone", "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": '{"id":"draft-123"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type("Target", (), {"channel": "draft", "recipient": "recipient@example.com", "metadata": {}})(),
    )

    assert result.delivered is True
    assert result.metadata == {"draft_id_present": True}


def test_gws_gmail_connector_fails_closed_without_auth(monkeypatch, tmp_path: Path) -> None:
    artifact = tmp_path / "brief.md"
    artifact.write_text("# Brief\n", encoding="utf-8")
    monkeypatch.setattr("multi_agent_brief.delivery.gws.shutil.which", lambda name: "/usr/local/bin/gws")
    monkeypatch.delenv("GOOGLE_WORKSPACE_CLI_TOKEN", raising=False)
    monkeypatch.delenv("GWS_TOKEN", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr("multi_agent_brief.delivery.gws._well_known_adc_paths", lambda: [])

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        return type("Completed", (), {"returncode": 0, "stdout": '{"auth_method":"none"}', "stderr": ""})()

    monkeypatch.setattr("multi_agent_brief.delivery.gws.subprocess.run", fake_run)

    result = GwsGmailDeliveryConnector().deliver(
        artifact=type("Artifact", (), {"path": str(artifact), "title": "Weekly"})(),
        target=type("Target", (), {"channel": "draft", "recipient": "recipient@example.com", "metadata": {}})(),
    )

    assert result.delivered is False
    assert "gws is not authenticated" in result.message
    assert "gws auth setup" in result.message


def test_mabw_deliver_guidance_uses_delivery_command() -> None:
    text = (ROOT / ".claude" / "commands" / "mabw.md").read_text(encoding="utf-8")
    assert "$BRIEFLOOP_CLI deliver --workspace <workspace> --target local" in text
    assert "$BRIEFLOOP_CLI deliver --workspace <workspace> --target feishu" in text
    assert "delivery_artifacts" in text
    assert "do not send audit/control records" in text
    assert "`doctor` is not a writer verb" in text


def test_deliver_help_mentions_recipient_hash(capsys) -> None:
    try:
        main(["deliver", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "recipient_sha256" in capsys.readouterr().out
