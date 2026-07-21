from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from multi_agent_brief.cli.deliver_commands import DeliverCommandError, deliver_workspace
from multi_agent_brief.cli.main import main
from multi_agent_brief.delivery.base import DeliveryResult
from multi_agent_brief.delivery.gws import GwsGmailDeliveryConnector
from tests.helpers import write_legacy_control_files, initialize_workspace
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
        write_legacy_control_files(ws)
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
        "finalize_transaction_id": "render-deliver-test-001",
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








def _deliver(
    ws: Path,
    *,
    target: str = "local",
    channel: str | None = None,
    recipient: str = "",
    subject: str = "",
    body: str = "",
) -> tuple[int, dict[str, Any]]:
    """Drive delivery through the direct deterministic seam.

    Mirrors deliver_commands.handle: rc 0 with the success payload, or rc 1
    with the typed DeliverCommandError payload.
    """
    resolved_channel = channel or ("local" if target == "local" else "")
    try:
        return 0, deliver_workspace(
            workspace=ws,
            target=target,
            channel=resolved_channel,
            recipient=recipient,
            subject=subject,
            body=body,
        )
    except DeliverCommandError as exc:
        payload: dict[str, Any] = exc.to_payload()
        payload["target"] = payload.get("target") or target
        payload["channel"] = payload.get("channel") or resolved_channel
        return 1, payload


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }


def test_deliver_public_cli_is_retired_with_zero_writes(tmp_path: Path, capsys) -> None:
    # retired public `deliver` CLI surface; delivery semantics are
    # only reachable through the direct deterministic seam (deliver_workspace).
    cases: list[tuple[Path, str, list[str]]] = []

    legacy_ws = _workspace(tmp_path / "legacy")
    _write_bundle(legacy_ws)
    cases.append((
        legacy_ws,
        "legacy_workspace_unsupported",
        ["deliver", "--workspace", str(legacy_ws), "--target", "local", "--json"],
    ))

    fresh_ws = _workspace(tmp_path / "fresh")
    cases.append((
        fresh_ws,
        "runtime_command_unsupported",
        ["deliver", "--workspace", str(fresh_ws), "--json"],
    ))

    sqlite_ws = initialize_workspace(tmp_path / "sqlite")
    cases.append((
        sqlite_ws,
        "runtime_command_unsupported",
        ["deliver", "--workspace", str(sqlite_ws), "--target", "local"],
    ))
    capsys.readouterr()

    for ws, token, argv in cases:
        before = _workspace_file_bytes(ws)
        rc = main(argv)
        out = capsys.readouterr().out
        assert rc == 1
        assert out.strip() == token
        assert _workspace_file_bytes(ws) == before














def test_deliver_missing_bundle_returns_typed_error(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)

    rc, payload = _deliver(ws)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error_code"] == "E_DELIVERY_BUNDLE_MISSING"
    assert "finalize" in payload["message"]
















































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
