"""Tests for the read-only writer-facing status command."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    initialize_runtime_state,
    runtime_state_paths,
)


def _minimal_workspace(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "config.yaml").write_text("project:\n  name: status-test\n", encoding="utf-8")
    (path / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (path / "user.md").write_text("# Status test\n", encoding="utf-8")
    return path


def test_status_command_is_read_only_for_existing_runtime_state(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    paths = runtime_state_paths(ws)
    paths["artifact_registry"].write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-artifact-registry/v1",
                "run_id": "run-test",
                "artifacts": {
                    "candidate_claims": {
                        "artifact_id": "candidate_claims",
                        "path": "output/intermediate/candidate_claims.json",
                        "status": "expected",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    watched = [path for path in paths.values() if path.exists()]
    before_bytes = {path: path.read_bytes() for path in watched}
    before_mtime = {path: path.stat().st_mtime_ns for path in watched}
    before_event_count = len(paths["event_log"].read_text(encoding="utf-8").splitlines())

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert payload["runtime"]["runtime"] == "claude"
    assert payload["workflow"]["current_stage"] == "doctor"
    assert payload["artifacts"]["expected_count"] == 1
    assert payload["events"]["event_count"] == before_event_count
    assert "stage-complete" not in payload["suggested_next_command"]
    assert payload["suggested_next_command"] == f"/generate-brief {ws}"

    for path in watched:
        assert path.read_bytes() == before_bytes[path]
        assert path.stat().st_mtime_ns == before_mtime[path]
    assert len(paths["event_log"].read_text(encoding="utf-8").splitlines()) == before_event_count


def test_status_command_does_not_initialize_missing_runtime_state(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    paths = runtime_state_paths(ws)

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert payload["runtime"]["present"] is False
    assert "runtime_manifest missing" in payload["stale_or_unknown"]
    for path in paths.values():
        assert not path.exists()


def test_status_command_reports_corrupt_event_log_without_writing(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    event_log.parent.mkdir(parents=True)
    event_log.write_text("{bad json}\n", encoding="utf-8")
    before = event_log.read_bytes()
    before_mtime = event_log.stat().st_mtime_ns

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["events"]["corrupt_count"] == 1
    assert "event_log contains unreadable records" in payload["stale_or_unknown"]
    assert event_log.read_bytes() == before
    assert event_log.stat().st_mtime_ns == before_mtime


def test_status_command_reports_malformed_quality_gate_as_unknown(tmp_path, capsys):
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

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quality_gate"]["present"] is True
    assert payload["quality_gate"]["status"] == "unknown"
    assert payload["quality_gate"]["raw_status"] == "pass"
    assert payload["quality_gate"]["schema_warnings"] == ["metadata is not an object"]
    assert "quality_gate_report schema warning: metadata is not an object" in payload["stale_or_unknown"]
    assert quality_gate.read_bytes() == before
