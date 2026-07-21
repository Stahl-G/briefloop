"""Tests for the read-only writer-facing status command."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from multi_agent_brief import status as status_module
from multi_agent_brief.cli.main import main
from tests.helpers import sha256_file as _sha256_file
from tests.helpers import write_minimal_workspace


def _minimal_workspace(path: Path) -> Path:
    return write_minimal_workspace(
        path,
        project_name="status-test",
        user_text="# Status test\n",
    )




































































def test_status_command_reports_corrupt_event_log_without_writing(tmp_path):
    ws = _minimal_workspace(tmp_path / "ws")
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    event_log.parent.mkdir(parents=True)
    event_log.write_text("{bad json}\n", encoding="utf-8")
    before = event_log.read_bytes()
    before_mtime = event_log.stat().st_mtime_ns

    payload = status_module.build_workspace_status(ws)
    assert payload["events"]["corrupt_count"] == 1
    assert payload["progress"]["status"] == "needs_operator_action"
    assert payload["progress"]["current_work"] == "check run record"
    assert payload["timing"]["status"] == "invalid_event_log"
    assert "event_log contains unreadable records" in payload["stale_or_unknown"]
    assert event_log.read_bytes() == before
    assert event_log.stat().st_mtime_ns == before_mtime


def test_status_command_reports_invalid_utf8_event_log_without_writing(tmp_path):
    ws = _minimal_workspace(tmp_path / "ws")
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    event_log.parent.mkdir(parents=True)
    event_log.write_bytes(b"\xff\xfe\x00")
    before = event_log.read_bytes()
    before_mtime = event_log.stat().st_mtime_ns

    payload = status_module.build_workspace_status(ws)
    assert payload["events"]["corrupt_count"] == 1
    assert payload["progress"]["status"] == "needs_operator_action"
    assert payload["progress"]["current_work"] == "check run record"
    assert payload["timing"]["status"] == "invalid_event_log"
    assert "event_log contains unreadable records" in payload["stale_or_unknown"]
    assert event_log.read_bytes() == before
    assert event_log.stat().st_mtime_ns == before_mtime


def test_status_command_reports_malformed_quality_gate_as_unknown(tmp_path):
    ws = _minimal_workspace(tmp_path / "ws")
    quality_gate = ws / "output" / "intermediate" / "quality_gate_report.json"
    quality_gate.parent.mkdir(parents=True)
    quality_gate.write_text(
        json.dumps(
            {
                "metadata": "bad",
                "findings": [],
                "status": "pass",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    before = quality_gate.read_bytes()

    payload = status_module.build_workspace_status(ws)
    assert payload["quality_gate"]["present"] is True
    assert payload["quality_gate"]["status"] == "unknown"
    assert payload["quality_gate"]["raw_status"] == "pass"
    assert payload["quality_gate"]["schema_warnings"] == ["metadata is not an object"]
    assert "quality_gate_report schema warning: metadata is not an object" in payload["stale_or_unknown"]
    assert quality_gate.read_bytes() == before
