"""Tests for v0.6.6 audience profile runtime surface."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.audience_memory import build_default_audience_profile
from multi_agent_brief.inputs.classifier import classify_input_dir
from tests.helpers import write_workspace_files_under


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
