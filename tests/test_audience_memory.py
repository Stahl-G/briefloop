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
from multi_agent_brief.inputs.classifier import classify_input_dir
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




def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
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
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()


def test_retired_operator_public_surfaces_reject_without_writes(tmp_path, capsys):
    """Bounded rejection matrix for the retired public surfaces of this file."""
    cases = [
        (
            lambda ws: [
                "run", "--runtime", "operator",
                "--workspace", str(ws),
                "--repo-workdir", str(ROOT),
                "--skip-doctor",
            ],
            "[run] runtime_adapter_unsupported\n",
        ),
        (
            lambda ws: [
                "state", "init", "--runtime", "operator",
                "--workspace", str(ws),
                "--repo-workdir", str(ROOT),
            ],
            "runtime_command_unsupported\n",
        ),
        (
            lambda ws: ["state", "check", "--workspace", str(ws), "--repo-workdir", str(ROOT)],
            "runtime_command_unsupported\n",
        ),
        (
            lambda ws: [
                "state", "init", "--runtime", "operator",
                "--workspace", str(ws),
                "--repo-workdir", str(ROOT),
                "--reset-state",
            ],
            "runtime_command_unsupported\n",
        ),
        (
            lambda ws: ["inputs", "classify", "--config", str(ws / "config.yaml"), "--quiet"],
            "runtime_command_unsupported\n",
        ),
    ]
    for index, (argv_for, token) in enumerate(cases):
        ws = _write_workspace(tmp_path / f"case-{index}")
        before_files = _workspace_file_bytes(ws)

        rc = main(argv_for(ws))

        # retired public `run --runtime operator`, `state`, and
        # `inputs classify` surfaces; the Codex SQLite ControlStore runtime is
        # the sole runtime authority.
        assert rc == 1
        assert capsys.readouterr().out == token
        assert _workspace_file_bytes(ws) == before_files
