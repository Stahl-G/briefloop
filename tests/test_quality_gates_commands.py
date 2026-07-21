"""Tests for v0.6.3 deterministic quality gate controls."""

from __future__ import annotations

import json
import hashlib
import re
import shutil
from functools import partial
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.quality_gates.contract import (
    GATE_IDS,
    interpret_quality_gate_binding,
    quality_gate_report_path_for_stage,
    require_quality_gate_binding_pass,
)
from tests.helpers import write_legacy_control_files, write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent


_write_workspace_files = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "TargetCo"
output:
  path: "output"
input:
  path: "input"
""".strip(),
    user_text="# User\nTarget: TargetCo\n",
    include_input_dir=True,
)


def _write_workspace(tmp_path: Path) -> Path:
    ws = _write_workspace_files(tmp_path)
    write_legacy_control_files(ws)
    return ws




def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path








def _write_ledger(ws: Path, claims: list[dict]) -> None:
    (_intermediate(ws) / "claim_ledger.json").write_text(
        json.dumps(claims, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_audited_brief(ws: Path, text: str) -> None:
    (_intermediate(ws) / "audited_brief.md").write_text(text, encoding="utf-8")
















































@pytest.mark.parametrize(
    "argv",
    [
        ["gates", "check", "--json"],
        ["gates", "show", "--json"],
        ["gates", "validate", "--json"],
        [
            "state",
            "decide",
            "--stage",
            "auditor",
            "--decision",
            "continue",
            "--reason",
            "skip quality gates",
            "--json",
        ],
        [
            "feedback",
            "ingest",
            "--feedback",
            "output/intermediate/quality_gate_report.json",
            "--source",
            "audit",
            "--json",
        ],
    ],
    ids=["gates-check", "gates-show", "gates-validate", "state-decide", "feedback-ingest"],
)
def test_retired_public_gate_command_surfaces_fail_closed_on_legacy_workspace(tmp_path, capsys, argv):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(ws, "## Executive Summary\nTargetCo update.\n")
    # retired public gates/state/feedback CLI surfaces on legacy JSON
    # workspaces; the SQLite ControlStore runtime is the sole command authority.
    before_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }

    rc = main([argv[0], argv[1], "--workspace", str(ws), "--repo-workdir", str(ROOT), *argv[2:]])

    assert rc == 1
    assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
    after_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
















def test_runtime_repair_instructions_use_scoped_current_gate_start() -> None:
    instruction_files = [
        ROOT / "src/multi_agent_brief/hermes/adapter.py",
        ROOT / "configs/agent_roles.yaml",
        ROOT / "scripts/generate_agent_configs.py",
        ROOT / ".agents/skills/orchestrator/SKILL.md",
    ]
    for directory in (
        ROOT / ".agents/skills",
        ROOT / ".agents/hermes-skills",
        ROOT / ".codex",
        ROOT / ".claude",
        ROOT / ".opencode",
        ROOT / "docs/agents",
        ROOT / "integrations/hermes-plugin",
        ROOT / "integrations/workbuddy",
    ):
        if directory.exists():
            instruction_files.extend(
                path
                for path in directory.rglob("*")
                if path.is_file() and path.suffix in {".md", ".toml", ".yaml", ".yml", ".py"}
            )
    instruction_files = sorted(set(instruction_files))
    bare_start = re.compile(
        r"(?:briefloop|multi-agent-brief)\s+repair\s+start\s+--workspace\s+"
        r"(?:<workspace>|\{workspace\}|\$ARGUMENTS)"
        r"(?![^`\n]*(?:--gate-stage|--finding-id|--route-index))"
    )
    combined_text = ""

    for path in instruction_files:
        text = path.read_text(encoding="utf-8")
        combined_text += f"\n# {path}\n{text}"
        offenders = [
            line
            for line in text.splitlines()
            if bare_start.search(line) and "do not use bare" not in line and "Do not use bare" not in line
        ]
        assert offenders == [], path

    assert "gates show --workspace" in combined_text
    assert "repair route --workspace" in combined_text
    assert "--gate-stage" in combined_text
    assert "--gate-artifact" in combined_text
    assert "--finding-id" in combined_text
    assert "--route-index" in combined_text
    assert "do not use unscoped repair start for current-gate blockers" in combined_text




















































































































