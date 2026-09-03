"""Tests for briefloop start / handoff launcher."""

from __future__ import annotations

from types import SimpleNamespace
from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.cli.init_commands import _init_web_wizard
from tests.helpers import write_workspace_files_under


class _InitWebServerDouble:
    def __init__(self, *, outcome=None, interrupt: bool = False) -> None:
        self.url = "http://127.0.0.1:12345/#token=test"
        self.outcome = outcome
        self._interrupt = interrupt
        self.closed = False

    def serve_forever(self) -> None:
        if self._interrupt:
            raise KeyboardInterrupt

    def close(self) -> None:
        self.closed = True


def test_init_web_handoff_prints_exact_browser_selected_target(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    selected = tmp_path / "human-selected"
    server = _InitWebServerDouble(
        outcome=SimpleNamespace(
            status="committed",
            workspace=str(selected),
            run_id="RUN-SELECTED",
            transaction_id="TX-SELECTED",
            execution_authorized=True,
        )
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.init_web.create_init_web_server",
        lambda *_args, **_kwargs: server,
    )
    monkeypatch.setattr("webbrowser.open", lambda _url: True)

    assert _init_web_wizard(SimpleNamespace(port=0)) == 0

    output = capsys.readouterr().out
    assert f"briefloop runtime continue --workspace {selected}" in output
    assert "RUN-SELECTED" in output and "TX-SELECTED" in output
    assert server.closed is True


_write_workspace = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "Test Brief"
  company: "TestCo"
  industry: "testing"
  language: "en"
  audience: "management"
report:
  cadence: "weekly"
input:
  path: "input"
output:
  path: "output"
""".strip(),
    user_text="# Test User Profile\n\nCompany: TestCo\n",
    sources_text="""
source_strategy:
  profile: "conservative"
  enabled_providers:
    - "manual"
manual:
  enabled: true
  sources: []
""".strip(),
    include_input_dir=True,
)


def _snapshot_workspace_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


def test_retired_launcher_public_paths_reject_without_writes(
    tmp_path, monkeypatch, capsys
):
    """Bounded rejection matrix for the retired run/start/handoff launcher surface."""
    venv = str(tmp_path / ".venv" / "bin" / "activate")

    def assert_rejected(ws: Path, argv: list[str], expected: str) -> None:
        before = _snapshot_workspace_bytes(ws)
        assert main(argv) == 1
        assert capsys.readouterr().out == expected
        assert _snapshot_workspace_bytes(ws) == before

    # retired public `start` launcher (explicit --workspace).
    ws_start = _write_workspace(tmp_path / "start-flag")
    assert_rejected(
        ws_start,
        [
            "start",
            "--runtime",
            "operator",
            "--workspace",
            str(ws_start),
            "--skip-doctor",
            "--venv",
            venv,
        ],
        "runtime_command_unsupported\n",
    )
    # retired `start` CWD workspace auto-detection.
    ws_start_cwd = _write_workspace(tmp_path / "start-cwd")
    monkeypatch.chdir(ws_start_cwd)
    assert_rejected(
        ws_start_cwd,
        ["start", "--runtime", "operator", "--skip-doctor", "--venv", venv],
        "[start] runtime_command_unsupported\n",
    )
    # retired non-codex `run` runtime adapters (operator/claude).
    ws_run = _write_workspace(tmp_path / "run-operator")
    assert_rejected(
        ws_run,
        [
            "run",
            "--runtime",
            "operator",
            "--workspace",
            str(ws_run),
            "--skip-doctor",
            "--venv",
            venv,
        ],
        "[run] runtime_adapter_unsupported\n",
    )
    ws_rerun = _write_workspace(tmp_path / "run-fast-rerun")
    assert_rejected(
        ws_rerun,
        [
            "run",
            "--runtime",
            "claude",
            "--recipe",
            "fast-rerun",
            "--workspace",
            str(ws_rerun),
            "--skip-doctor",
            "--venv",
            venv,
        ],
        "[run] runtime_adapter_unsupported\n",
    )
    # retired `run --skip-doctor` launcher path for the codex runtime.
    ws_codex = _write_workspace(tmp_path / "run-codex")
    assert_rejected(
        ws_codex,
        [
            "run",
            "--runtime",
            "codex",
            "--workspace",
            str(ws_codex),
            "--skip-doctor",
            "--venv",
            venv,
        ],
        "[run] runtime_command_unsupported\n",
    )
    # retired public `handoff` generator command.
    ws_handoff = _write_workspace(tmp_path / "handoff")
    assert_rejected(
        ws_handoff,
        [
            "handoff",
            "--config",
            str(ws_handoff / "config.yaml"),
            "--runtime",
            "hermes",
            "--skip-doctor",
            "--venv",
            venv,
        ],
        "runtime_command_unsupported\n",
    )
    # non-codex runtimes are refused on every workspace (SQLite-only runtime).
    ws_fresh = _write_workspace(tmp_path / "fresh")
    assert_rejected(
        ws_fresh,
        ["run", "--runtime", "claude", "--workspace", str(ws_fresh), "--skip-doctor", "--venv", venv],
        "[run] runtime_adapter_unsupported\n",
    )
