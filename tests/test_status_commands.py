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


def _mark_fact_layer_imported(ws: Path) -> None:
    paths = runtime_state_paths(ws)
    manifest = json.loads(paths["runtime_manifest"].read_text(encoding="utf-8"))
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    fact_layer_sha256 = "a" * 64
    manifest["recipe"] = "fast-rerun"
    manifest["fact_layer_import"] = {
        "schema_version": "mabw.fact_layer_import.v1",
        "source_run_id": "mabw-20260614T000000Z-source",
        "source_archive_manifest": "output/runs/mabw-20260614T000000Z-source/manifest.json",
        "source_archive_manifest_sha256": "b" * 64,
        "fact_layer_sha256": fact_layer_sha256,
        "imported_file_count": 5,
        "satisfied_stage_ids": [
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
        ],
    }
    statuses = dict(workflow.get("stage_statuses") or {})
    for stage_id in manifest["fact_layer_import"]["satisfied_stage_ids"]:
        statuses[stage_id] = {
            "status": "complete",
            "reason": "Satisfied by frozen fact layer import.",
            "updated_at": "2026-06-14T00:00:00+00:00",
            "metadata": {
                "satisfied_by_import": True,
                "fact_layer_import_sha256": fact_layer_sha256,
                "source_run_id": manifest["fact_layer_import"]["source_run_id"],
            },
        }
    statuses["analyst"] = {
        "status": "ready",
        "reason": "",
        "updated_at": "2026-06-14T00:00:00+00:00",
    }
    workflow["current_stage"] = "analyst"
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow["stage_statuses"] = statuses
    paths["runtime_manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    assert payload["workflow"]["run_integrity"]["status"] == "clean"
    assert payload["workflow"]["run_integrity"]["reference_eligible"] is True
    assert payload["timing"]["schema_version"] == "mabw.control_timing.v1"
    assert payload["timing"]["source"] == "event_log"
    assert payload["timing"]["precision"] == "control_trace_bucket"
    assert payload["timing"]["status"] == "unknown"
    assert payload["artifacts"]["expected_count"] == 1
    assert payload["events"]["event_count"] == before_event_count
    assert "stage-complete" not in payload["suggested_next_command"]
    assert payload["suggested_next_command"] == f"/generate-brief {ws}"

    for path in watched:
        assert path.read_bytes() == before_bytes[path]
        assert path.stat().st_mtime_ns == before_mtime[path]
    assert len(paths["event_log"].read_text(encoding="utf-8").splitlines()) == before_event_count


def test_status_command_reports_contaminated_run_integrity(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
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

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[status] run_integrity: contaminated reference_eligible=False" in out
    assert "[status] timing: contaminated; elapsed buckets are not clean evidence" in out


def test_status_command_reports_fact_layer_import_summary(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _mark_fact_layer_imported(ws)

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    summary = payload["fact_layer_import"]
    assert summary["status"] == "valid"
    assert summary["source_run_id"] == "mabw-20260614T000000Z-source"
    assert summary["fact_layer_sha256"] == "a" * 64
    assert summary["next_stage"] == "analyst"
    assert all(stage["display_status"] == "complete via import" for stage in summary["imported_stages"])
    assert payload["suggested_next_command"] == f"multi-agent-brief run --workspace {ws} --recipe fast-rerun --skip-doctor"


def test_status_command_human_output_reports_fact_layer_import(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _mark_fact_layer_imported(ws)

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[status] fact_layer_import: valid" in out
    assert "source_run=mabw-20260614T000000Z-source" in out
    assert "satisfied=complete via import" in out


def test_status_command_reports_malformed_run_integrity_as_unknown(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["run_integrity"] = "bad"
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"]["run_integrity"]["status"] == "unknown"
    assert payload["workflow"]["run_integrity"]["reference_eligible"] is False
    assert payload["workflow"]["run_integrity"]["reasons"][0]["reason_code"] == "run_integrity_malformed"
    assert payload["timing"]["status"] == "unknown"
    assert payload["timing"]["run_integrity"]["reference_eligible"] is False
    assert "run_integrity_unknown" in payload["timing"]["warnings"]


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


def test_status_timing_is_unknown_when_workflow_state_missing_even_with_event_log(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    paths = runtime_state_paths(ws)
    paths["event_log"].parent.mkdir(parents=True, exist_ok=True)
    paths["event_log"].write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema_version": "multi-agent-brief-event-log/v1",
                        "event_id": "e0",
                        "run_id": "run-test",
                        "created_at": "2026-06-14T00:00:00Z",
                        "event_type": "run_initialized",
                        "actor": "cli",
                        "stage_id": None,
                        "artifact_id": None,
                        "decision": None,
                        "reason": "",
                        "metadata": {},
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "schema_version": "multi-agent-brief-event-log/v1",
                        "event_id": "e1",
                        "run_id": "run-test",
                        "created_at": "2026-06-14T00:01:00Z",
                        "event_type": "decision_recorded",
                        "actor": "cli",
                        "stage_id": "doctor",
                        "artifact_id": None,
                        "decision": "continue",
                        "reason": "complete",
                        "metadata": {"transaction_id": "tx-e1"},
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"]["present"] is False
    assert payload["timing"]["status"] == "unknown"
    assert payload["timing"]["run_integrity"]["status"] == "unknown"
    assert payload["timing"]["run_integrity"]["reference_eligible"] is False
    assert "run_integrity_unknown" in payload["timing"]["warnings"]


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
    assert payload["timing"]["status"] == "invalid_event_log"
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
