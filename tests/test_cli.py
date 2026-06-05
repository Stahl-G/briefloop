"""Tests for CLI commands. Pipeline tests use BriefPipeline directly."""
from __future__ import annotations

from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.core.config import build_run_settings, load_config
from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext


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


def _run_pipeline(workspace: Path, output_dir: str | None = None):
    """Helper: run BriefPipeline directly (formerly tested via CLI run)."""
    config_path = workspace / "config.yaml"
    config = load_config(str(config_path)) if config_path.exists() else None
    input_dir = str(workspace / "input") if (workspace / "input").exists() else None
    settings = build_run_settings(
        config=config,
        input_dir=input_dir,
        output_dir=output_dir,
        name=None,
        language=None,
        audience=None,
    )
    context = PipelineContext(**settings)
    BriefPipeline().run(context)
    return context


def test_cli_init_and_run(tmp_path):
    workspace = tmp_path / "ws"

    assert main(complete_init_args(workspace)) == 0
    assert (workspace / "config.yaml").exists()
    assert (workspace / "sources.yaml").exists()

    # Add a source file
    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "input" / "news.md").write_text("- Test signal for weekly brief.\n", encoding="utf-8")

    _run_pipeline(workspace)
    assert (workspace / "output" / "brief.md").exists()
    assert (workspace / "output" / "intermediate" / "draft_brief.md").exists()
    assert (workspace / "output" / "intermediate" / "claim_ledger.json").exists()


def test_cli_run_with_industry(tmp_path):
    workspace = tmp_path / "ws"
    main(complete_init_args(workspace))

    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "input" / "data.md").write_text("- Financial earnings report shows growth.\n", encoding="utf-8")

    _run_pipeline(workspace)
    assert (workspace / "output" / "brief.md").exists()


def test_cli_run_accepts_workspace_directory_with_config(tmp_path):
    workspace = tmp_path / "ws"
    main(complete_init_args(workspace))
    (workspace / "input" / "news.md").write_text(
        "- Workspace directory invocation should load this reportable source from input.\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "out"
    _run_pipeline(workspace, output_dir=str(output_dir))
    assert (output_dir / "brief.md").exists()


def test_cli_audit_existing_brief(tmp_path):
    workspace = tmp_path / "ws"
    main(complete_init_args(workspace))
    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "input" / "news.md").write_text("- Test signal for audit.\n", encoding="utf-8")
    _run_pipeline(workspace)

    audit_output = tmp_path / "audit.json"
    exit_code = main(
        [
            "audit",
            str(workspace / "output" / "brief.md"),
            "--ledger",
            str(workspace / "output" / "intermediate" / "claim_ledger.json"),
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
    assert '"audit_status": "pass"' in audit_output.read_text(encoding="utf-8")


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
    assert "has been replaced by" in captured.out
    assert "/generate-brief" in captured.out
