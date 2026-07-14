"""Tests for v0.6.6 audience profile runtime surface."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.audience_memory import (
    AUDIENCE_MEMORY_FILES,
    build_default_audience_profile,
    create_audience_profile_snapshot,
    ensure_audience_profile,
)
from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import RUNTIME_STATE_FILES, initialize_runtime_state
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent


def _write_workspace(tmp_path: Path) -> Path:
    ws = write_workspace_files_under(
        tmp_path,
        config_text="""
project:
  name: "Audience Memory Test"
  company: "TestCo"
  industry: "testing"
  audience: "management"
language:
  output: "en-US"
report:
  cadence: "weekly"
input:
  path: "input"
output:
  path: "output"
""".strip(),
        user_text="# User\n",
        sources_text="""
manual:
  enabled: true
  sources:
    - name: "Local evidence"
      path: "input/sources/"
""".strip(),
        include_input_dir=True,
    )
    (ws / "input" / "sources").mkdir(parents=True, exist_ok=True)
    return ws


def _event_types(ws: Path) -> list[str]:
    event_log = ws / RUNTIME_STATE_FILES["event_log"]
    return [
        json.loads(line)["event_type"]
        for line in event_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_default_audience_profile_is_plain_markdown():
    text = build_default_audience_profile(
        {
            "company": "ExampleCo",
            "industry_text": "Industrial robotics",
            "audience": "strategy team",
            "task_objective": "Track competitor moves.",
            "focus_areas": ["pricing", "capacity"],
        }
    )

    assert text.startswith("# Audience Profile")
    assert "ExampleCo" in text
    assert "Track competitor moves." in text
    assert "not source evidence" in text


def test_snapshot_created_once_per_run_id_and_ignores_mid_run_profile_edits(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    run_id = state["manifest"]["run_id"]
    ensure_audience_profile(ws, {"company": "TestCo"})

    first = create_audience_profile_snapshot(workspace=ws, run_id=run_id)
    first_text = first.path.read_text(encoding="utf-8")
    assert first.created is True
    assert "<!-- mabw:audience-profile-snapshot" in first_text
    assert f"run_id: {run_id}" in first_text
    assert "Captured Body SHA256" in first_text
    assert "Snapshot SHA256" not in first_text

    (ws / "audience_profile.md").write_text(
        "# Audience Profile\n\nUNIQUE_TASTE_MARKER_AFTER_SNAPSHOT\n",
        encoding="utf-8",
    )
    second = create_audience_profile_snapshot(workspace=ws, run_id=run_id)

    assert second.created is False
    assert second.path.read_text(encoding="utf-8") == first_text
    assert "UNIQUE_TASTE_MARKER_AFTER_SNAPSHOT" not in second.path.read_text(encoding="utf-8")


def test_snapshot_rebuilds_when_metadata_missing_or_malformed(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    run_id = state["manifest"]["run_id"]
    ensure_audience_profile(ws, {"company": "TestCo"})
    snapshot_path = ws / AUDIENCE_MEMORY_FILES["audience_profile_snapshot"]

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        "# Audience Profile Snapshot\n\nrun_id: not-a-real-header\n",
        encoding="utf-8",
    )

    snapshot = create_audience_profile_snapshot(workspace=ws, run_id=run_id)

    assert snapshot.created is True
    assert snapshot.stale_rebuilt is True
    assert snapshot.path.read_text(encoding="utf-8").startswith("<!-- mabw:audience-profile-snapshot")


def test_run_creates_profile_snapshot_event_and_handoff_refs(tmp_path):
    ws = _write_workspace(tmp_path)

    rc = main([
        "run", "--runtime", "operator",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--skip-doctor",
    ])

    assert rc == 0
    assert (ws / "audience_profile.md").exists()
    assert (ws / AUDIENCE_MEMORY_FILES["audience_profile_snapshot"]).exists()
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert data["audience_memory_files"] == AUDIENCE_MEMORY_FILES
    assert "audience_profile.md" not in data["expected_artifacts"]
    assert AUDIENCE_MEMORY_FILES["audience_profile_snapshot"] not in data["expected_artifacts"]

    events = [
        json.loads(line)
        for line in (ws / RUNTIME_STATE_FILES["event_log"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    created_events = [
        event for event in events
        if event["event_type"] == "audience_profile_snapshot_created"
    ]
    assert len(created_events) == 1
    metadata = created_events[0]["metadata"]
    assert metadata["audience_profile"] == "audience_profile.md"
    assert metadata["audience_profile_snapshot"] == AUDIENCE_MEMORY_FILES["audience_profile_snapshot"]
    assert metadata["profile_missing"] is True
    assert metadata["profile_created"] is True
    assert metadata["source_sha256"]
    assert metadata["snapshot_sha256"]


def test_run_backfills_missing_profile_from_workspace_config(tmp_path):
    ws = _write_workspace(tmp_path)
    assert not (ws / "audience_profile.md").exists()

    rc = main([
        "run", "--runtime", "operator",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--skip-doctor",
    ])

    assert rc == 0
    profile = (ws / "audience_profile.md").read_text(encoding="utf-8")
    assert "TestCo" in profile
    assert "testing" in profile
    assert "management" in profile
    assert "weekly" in profile
    assert "Unknown organization" not in profile
    assert "Unknown industry/theme" not in profile


def test_runtime_state_commands_do_not_overwrite_existing_audience_profile(tmp_path):
    ws = _write_workspace(tmp_path)
    custom_profile = "# Audience Profile\n\nCUSTOM_TASTE_MARKER_DO_NOT_OVERWRITE\n"
    (ws / "audience_profile.md").write_text(custom_profile, encoding="utf-8")

    assert main(["run", "--runtime", "operator", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--skip-doctor"]) == 0
    assert (ws / "audience_profile.md").read_text(encoding="utf-8") == custom_profile

    assert main(["state", "init", "--runtime", "operator", "--workspace", str(ws), "--repo-workdir", str(ROOT)]) == 0
    assert (ws / "audience_profile.md").read_text(encoding="utf-8") == custom_profile

    assert main(["state", "check", "--workspace", str(ws), "--repo-workdir", str(ROOT)]) == 0
    assert (ws / "audience_profile.md").read_text(encoding="utf-8") == custom_profile

    assert main(["state", "init", "--runtime", "operator", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--reset-state"]) == 0
    assert (ws / "audience_profile.md").read_text(encoding="utf-8") == custom_profile


def test_rerun_same_run_id_does_not_duplicate_snapshot_created_event(tmp_path):
    ws = _write_workspace(tmp_path)
    for _ in range(2):
        rc = main([
            "run", "--runtime", "operator",
            "--workspace",
            str(ws),
            "--repo-workdir",
            str(ROOT),
            "--skip-doctor",
        ])
        assert rc == 0

    assert _event_types(ws).count("audience_profile_snapshot_created") == 1


def test_reset_new_run_refreshes_fixed_snapshot_path(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main(["run", "--runtime", "operator", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--skip-doctor"])
    assert rc == 0
    first = (ws / AUDIENCE_MEMORY_FILES["audience_profile_snapshot"]).read_text(encoding="utf-8")
    first_run_id = json.loads((ws / RUNTIME_STATE_FILES["runtime_manifest"]).read_text(encoding="utf-8"))["run_id"]

    rc = main(["state", "init", "--runtime", "operator", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--reset-state"])
    assert rc == 0
    rc = main(["run", "--runtime", "operator", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--skip-doctor"])
    assert rc == 0

    second = (ws / AUDIENCE_MEMORY_FILES["audience_profile_snapshot"]).read_text(encoding="utf-8")
    second_run_id = json.loads((ws / RUNTIME_STATE_FILES["runtime_manifest"]).read_text(encoding="utf-8"))["run_id"]
    assert second_run_id != first_run_id
    assert second != first
    assert f"run_id: {second_run_id}" in second
    assert not (ws / "output" / "intermediate" / f"audience_profile_snapshot_{first_run_id}.md").exists()


def test_audience_profile_is_not_input_evidence_or_claim_ledger_source(tmp_path):
    ws = _write_workspace(tmp_path)
    marker = "UNIQUE_TASTE_MARKER_NOT_EVIDENCE"
    (ws / "audience_profile.md").write_text(
        f"# Audience Profile\n\n{marker}\n",
        encoding="utf-8",
    )
    (ws / "input" / "sources" / "source.md").write_text(
        "# Source\n\nEvidence item from input sources.\n",
        encoding="utf-8",
    )

    rc = main(["inputs", "classify", "--config", str(ws / "config.yaml"), "--quiet"])
    assert rc == 0
    classification = json.loads((ws / "output" / "input_classification.json").read_text(encoding="utf-8"))
    all_paths = json.dumps(classification, ensure_ascii=False)
    assert "source.md" in all_paths
    assert "audience_profile.md" not in all_paths
    assert marker not in all_paths
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()
