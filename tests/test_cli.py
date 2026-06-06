"""Tests for CLI toolbox commands."""
from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main


def complete_init_args(workspace, *, language="zh-CN", industry="finance", extra=None):
    args = [
        "init",
        str(workspace),
        "--language",
        language,
        "--company",
        "Test Company",
        "--industry",
        industry,
        "--title",
        "Weekly Brief",
        "--audience",
        "management",
        "--cadence",
        "weekly",
        "--source-profile",
        "research",
    ]
    if extra:
        args.extend(extra)
    return args


def test_cli_init_creates_workspace(tmp_path):
    workspace = tmp_path / "ws"

    assert main(complete_init_args(workspace)) == 0
    assert (workspace / "config.yaml").exists()
    assert (workspace / "sources.yaml").exists()
    assert (workspace / "input").exists()


def test_cli_audit_existing_brief(tmp_path):
    brief = tmp_path / "brief.md"
    ledger = tmp_path / "claim_ledger.json"
    brief.write_text("Revenue grew 5%. [src:CLAIM_TEST_001]\n", encoding="utf-8")
    ledger.write_text(
        json.dumps([
            {
                "claim_id": "CLAIM_TEST_001",
                "statement": "Revenue grew 5%.",
                "source_id": "SRC001",
                "evidence_text": "Revenue grew 5%.",
                "source_url": "https://example.com/report",
                "source_type": "manual",
                "claim_type": "fact",
                "confidence": "high",
            }
        ]),
        encoding="utf-8",
    )

    audit_output = tmp_path / "audit.json"
    exit_code = main(
        [
            "audit",
            str(brief),
            "--ledger",
            str(ledger),
            "--output",
            str(audit_output),
            "--report-date",
            "2026-06-02",
            "--max-source-age-days",
            "14",
            "--fail-on-stale-source",
        ]
    )

    assert exit_code == 0
    assert '"audit_status": "warning"' in audit_output.read_text(encoding="utf-8")


def test_cli_version(capsys):
    assert main(["version"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip()


def test_cli_run_command_prints_error_and_redirects(capsys):
    """run command must reject calls and point users to subagent workflow."""
    import tempfile
    d = tempfile.mkdtemp()
    config = Path(d) / "config.yaml"
    config.write_text("project:\n  name: test\n", encoding="utf-8")
    exit_code = main(["run", "--config", str(config)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "no longer runs the brief workflow" in captured.out
    assert "/generate-brief" in captured.out


def test_cli_prepare_is_deprecated_and_does_not_generate_outputs(tmp_path: Path, capsys):
    """prepare must not run the removed Python brief pipeline."""
    ws = tmp_path / "ws"
    assert main(complete_init_args(ws, extra=["--source-profile", "conservative"])) == 0

    result = main(["prepare", "--config", str(ws / "config.yaml")])
    captured = capsys.readouterr()

    assert result == 1
    assert "prepare no longer runs the brief workflow" in captured.out
    assert "/generate-brief <workspace>" in captured.out
    assert not (ws / "output" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()


def test_core_brief_pipeline_is_removed():
    assert not Path("src/multi_agent_brief/core/pipeline.py").exists()
