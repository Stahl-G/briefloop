"""Tests for v0.6.6 audience profile runtime surface."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from multi_agent_brief.audience_memory import (
    AUDIENCE_MEMORY_FILES,
    build_default_audience_profile,
    create_audience_profile_snapshot,
    ensure_audience_profile,
)
from multi_agent_brief.cli.main import main
from multi_agent_brief.inputs.classifier import classify_input_dir
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state
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


def _workspace_bytes(ws: Path) -> dict[str, str]:
    return {
        path.relative_to(ws).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }


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
    state = initialize_runtime_state(
        runtime="operator", workspace=ws, repo_workdir=ROOT
    )
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
    assert "UNIQUE_TASTE_MARKER_AFTER_SNAPSHOT" not in second.path.read_text(
        encoding="utf-8"
    )


def test_snapshot_rebuilds_when_metadata_missing_or_malformed(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(
        runtime="operator", workspace=ws, repo_workdir=ROOT
    )
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
    assert snapshot.path.read_text(encoding="utf-8").startswith(
        "<!-- mabw:audience-profile-snapshot"
    )


@pytest.mark.parametrize(
    ("command", "expected_error"),
    [
        (
            ["run", "--runtime", "operator", "--skip-doctor"],
            "[run] runtime_adapter_unsupported",
        ),
        (["state", "init", "--runtime", "operator"], "runtime_command_unsupported"),
        (["state", "check"], "runtime_command_unsupported"),
        (
            ["state", "init", "--runtime", "operator", "--reset-state"],
            "runtime_command_unsupported",
        ),
    ],
)
def test_legacy_audience_runtime_commands_fail_closed_without_writes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
    expected_error: str,
) -> None:
    ws = _write_workspace(tmp_path)
    custom_profile = "# Audience Profile\n\nCUSTOM_TASTE_MARKER_DO_NOT_OVERWRITE\n"
    (ws / "audience_profile.md").write_text(custom_profile, encoding="utf-8")
    before = _workspace_bytes(ws)

    rc = main([*command, "--workspace", str(ws), "--repo-workdir", str(ROOT)])

    assert rc == 1
    assert capsys.readouterr().out.strip() == expected_error
    assert _workspace_bytes(ws) == before
    assert (ws / "audience_profile.md").read_text(encoding="utf-8") == custom_profile
    assert not (ws / AUDIENCE_MEMORY_FILES["audience_profile_snapshot"]).exists()
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()


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

    classification = classify_input_dir(ws / "input")
    all_paths = json.dumps(classification, ensure_ascii=False)
    assert "source.md" in all_paths
    assert "audience_profile.md" not in all_paths
    assert marker not in all_paths
    assert not (ws / "output" / "input_classification.json").exists()
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()
