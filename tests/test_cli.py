"""Tests for CLI toolbox commands."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import build_parser, main


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
    parser = build_parser()
    subcommands = next(
        action.choices
        for action in parser._actions
        if getattr(action, "choices", None)
    )
    init_options = {
        option
        for action in subcommands["init"]._actions
        for option in action.option_strings
    }
    if "--task-objective" in init_options:
        # retain only the strict SQLite initialization contract.
        args.extend(["--task-objective", "Track material finance developments."])
    if extra:
        args.extend(extra)
    return args


def test_cli_init_creates_workspace(tmp_path, capsys):
    workspace = tmp_path / "ws"

    assert main(complete_init_args(workspace)) == 0
    output = capsys.readouterr().out
    assert (workspace / "config.yaml").exists()
    assert (workspace / "sources.yaml").exists()
    assert (workspace / "input").exists()
    input_readme = (workspace / "input" / "README.md").read_text(encoding="utf-8")
    context_readme = (workspace / "input" / "context" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "input/context" in input_readme
    assert "简报示例 Markdown" in input_readme
    assert "previous_weekly_reference.md" in context_readme
    assert "input/context" in output
    assert "简报示例 Markdown" in output
    assert "Claim Ledger" in output


def test_cli_init_can_configure_initial_news_backfill(tmp_path):
    workspace = tmp_path / "ws"

    rc = main(
        complete_init_args(
            workspace,
            language="en-US",
            industry="manufacturing",
            extra=[
                "--source-profile",
                "llm_decide",
                "--web-search-mode",
                "external_api",
                "--search-backend",
                "tavily",
                "--initial-news-backfill",
                "--preferred-news-domains",
                "reuters.com, bloomberg.com",
                "--excluded-news-domains",
                "spam.example.com",
            ],
        )
    )

    assert rc == 0
    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    backfill = sources["web_search"]["initial_news_backfill"]
    assert backfill["enabled"] is True
    assert backfill["days"] == 7
    assert backfill["daily_max_results"] == 20
    customization = sources["source_discovery"]["search_customization"]
    assert "task_objective" in customization["derive_queries_from"]
    assert customization["daily_backfill_uses_user_need_terms"] is True
    source_selection = sources["source_discovery"]["news_source_selection"]
    assert source_selection["preferred_domains"] == ["reuters.com", "bloomberg.com"]
    assert source_selection["excluded_domains"] == ["spam.example.com"]
    assert source_selection["do_not_use_fixed_personal_domain_list"] is True
    domain_config = sources["web_search"]["news_source_domains"]
    assert domain_config["preferred_domains"] == ["reuters.com", "bloomberg.com"]
    assert domain_config["excluded_domains"] == ["spam.example.com"]


def test_cli_init_rejects_initial_news_backfill_without_llm_decide(tmp_path, capsys):
    workspace = tmp_path / "ws"

    rc = main(
        complete_init_args(
            workspace,
            language="en-US",
            industry="manufacturing",
            extra=[
                "--web-search-mode",
                "external_api",
                "--search-backend",
                "tavily",
                "--initial-news-backfill",
            ],
        )
    )

    assert rc == 1
    assert (
        "--initial-news-backfill requires --source-profile llm_decide"
        in capsys.readouterr().out
    )
    assert not (workspace / "sources.yaml").exists()


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


def test_cli_audit_accepts_wrapped_ledger_and_hyphenated_claim_id(tmp_path):
    brief = tmp_path / "brief.md"
    ledger = tmp_path / "claim_ledger.json"
    brief.write_text("Revenue grew 5%. [src:CLM-001]\n", encoding="utf-8")
    ledger.write_text(
        json.dumps(
            {
                "metadata": {"generated_by": "synthetic fixture"},
                "claims": [
                    {
                        "claim_id": "CLM-001",
                        "statement": "Revenue grew 5%.",
                        "source_id": "SRC001",
                        "evidence_text": "Revenue grew 5%.",
                        "source_url": "https://example.com/report",
                        "source_type": "manual",
                        "claim_type": "fact",
                        "confidence": "high",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    audit_output = tmp_path / "audit.json"
    exit_code = main(["audit", str(brief), "--ledger", str(ledger), "--output", str(audit_output)])

    assert exit_code == 0
    report = json.loads(audit_output.read_text(encoding="utf-8"))
    assert report["audit_status"] == "pass"
    assert report["findings"] == []


def test_cli_version(capsys):
    assert main(["version"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip()


def test_pyproject_exposes_briefloop_shell_alias():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    entrypoint = '"multi_agent_brief.cli.main:main"'
    assert f"multi-agent-brief = {entrypoint}" in text
    assert f"briefloop = {entrypoint}" in text


def test_claude_install_writes_user_command_and_agents(tmp_path, capsys):
    repo = tmp_path / "repo"
    command_dir = repo / ".claude" / "commands"
    agents_dir = repo / ".claude" / "agents"
    command_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (command_dir / "generate-brief.md").write_text(
        "---\ndescription: test\n---\n\n"
        "You are the Orchestrator main agent generating a real user-facing brief for workspace: $ARGUMENTS.\n\n"
        "Read shared contract references before delegation:\n\n"
        "- `configs/orchestrator_contract.yaml`\n"
        "- `configs/stage_specs.yaml`\n"
        "- `configs/artifact_contracts.yaml`\n"
        "- `configs/policy_packs/default.yaml`\n",
        encoding="utf-8",
    )
    (command_dir / "mabw.md").write_text(
        "---\ndescription: test mabw\n---\n\n"
        "First-Screen Writer Help\n\n"
        "/mabw new\n/mabw run <workspace>\n/mabw status <workspace>\n"
        "/mabw feedback <workspace> [text-or-file]\n/mabw deliver <workspace>\n",
        encoding="utf-8",
    )
    (command_dir / "briefloop.md").write_text(
        "---\ndescription: test briefloop\n---\n\n"
        "First-Screen Writer Help\n\n"
        "/briefloop new\n/briefloop run <workspace>\n/briefloop status <workspace>\n"
        "/briefloop feedback <workspace> [text-or-file]\n/briefloop deliver <workspace>\n",
        encoding="utf-8",
    )
    (command_dir / "capability.md").write_text("# capability\n", encoding="utf-8")
    (command_dir / "init-brief.md").write_text("# init\n", encoding="utf-8")
    (command_dir / "propose-competitors.md").write_text("# competitors\n", encoding="utf-8")
    (agents_dir / "scout.md").write_text("---\nname: scout\n---\n\nScout.\n", encoding="utf-8")

    target = tmp_path / "claude"
    rc = main(["claude", "install", "--repo-workdir", str(repo), "--target", str(target)])

    assert rc == 0
    output = capsys.readouterr().out
    assert "Installed /briefloop and compatibility Claude Code assets" in output
    installed_briefloop_command = target / "commands" / "briefloop.md"
    assert installed_briefloop_command.exists()
    assert installed_briefloop_command.read_text(encoding="utf-8").startswith("---\n")
    assert "Generated by briefloop claude install" in installed_briefloop_command.read_text(encoding="utf-8")
    installed_mabw_command = target / "commands" / "mabw.md"
    assert installed_mabw_command.exists()
    assert installed_mabw_command.read_text(encoding="utf-8").startswith("---\n")
    assert "Generated by briefloop claude install" in installed_mabw_command.read_text(encoding="utf-8")
    installed_command = target / "commands" / "generate-brief.md"
    assert installed_command.exists()
    text = installed_command.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "Generated by briefloop claude install" in text
    assert f"{repo.as_posix()}/configs/orchestrator_contract.yaml" in text
    assert "If $ARGUMENTS is a relative path" in text
    assert not (target / "commands" / "capability.md").exists()
    assert not (target / "commands" / "init-brief.md").exists()
    assert not (target / "commands" / "propose-competitors.md").exists()
    installed_agent = target / "agents" / "mabw" / "scout.md"
    assert installed_agent.exists()
    assert installed_agent.read_text(encoding="utf-8").startswith("---\n")
    assert "Generated by briefloop claude install" in installed_agent.read_text(encoding="utf-8")


def test_claude_install_briefloop_command_is_self_contained(tmp_path, capsys):
    repo = Path(__file__).resolve().parents[1]
    target = tmp_path / "claude"

    rc = main(["claude", "install", "--repo-workdir", str(repo), "--target", str(target)])

    assert rc == 0
    capsys.readouterr()
    text = (target / "commands" / "briefloop.md").read_text(encoding="utf-8")
    assert "Generated by briefloop claude install" in text
    assert ".claude/commands/mabw.md" not in text
    assert "## `new`" in text
    assert "## `run <workspace>`" in text
    assert "## `status <workspace>`" in text
    assert "## `feedback <workspace> [text-or-file]`" in text
    assert "## `deliver <workspace>`" in text
    assert "status is strictly read-only" in text
    assert "briefloop deliver --workspace <workspace> --target local" in text
    assert "do not send audit/control records" in text


def test_claude_install_refuses_existing_non_mabw_file_without_force(tmp_path, capsys):
    repo = tmp_path / "repo"
    command_dir = repo / ".claude" / "commands"
    agents_dir = repo / ".claude" / "agents"
    command_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (command_dir / "generate-brief.md").write_text("# command\n", encoding="utf-8")
    (command_dir / "briefloop.md").write_text("# briefloop\n", encoding="utf-8")
    (command_dir / "mabw.md").write_text("# mabw\n", encoding="utf-8")
    (command_dir / "capability.md").write_text("# capability\n", encoding="utf-8")
    (agents_dir / "scout.md").write_text("# scout\n", encoding="utf-8")
    target = tmp_path / "claude"
    (target / "commands").mkdir(parents=True)
    (target / "commands" / "capability.md").write_text("# user capability\n", encoding="utf-8")
    (target / "commands" / "generate-brief.md").write_text("# existing\n", encoding="utf-8")

    rc = main(["claude", "install", "--repo-workdir", str(repo), "--target", str(target)])

    assert rc == 1
    assert "Refusing to overwrite existing non-generated file without --force" in capsys.readouterr().out


def test_cli_run_command_creates_handoff(capsys):
    """run --runtime operator is retired: typed rejection with zero workspace writes."""
    import tempfile
    d = Path(tempfile.mkdtemp())
    config = d / "config.yaml"
    config.write_text("project:\n  name: test\noutput:\n  path: output\n", encoding="utf-8")
    (d / "user.md").write_text("# test\n", encoding="utf-8")
    before_files = {
        path.relative_to(d).as_posix(): path.read_bytes()
        for path in d.rglob("*")
        if path.is_file()
    }
    exit_code = main(["run", "--runtime", "operator", "--config", str(config), "--skip-doctor"])
    captured = capsys.readouterr()
    # retired public `run --runtime operator` handoff surface and
    # its output/intermediate control artifacts; the Codex SQLite ControlStore
    # runtime is the sole runtime authority.
    assert exit_code == 1
    assert captured.out == "[run] runtime_adapter_unsupported\n"
    assert "/generate-brief" not in captured.out
    after_files = {
        path.relative_to(d).as_posix(): path.read_bytes()
        for path in d.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
    assert not (d / "output").exists()


def test_cli_prepare_is_deprecated_and_does_not_generate_outputs(tmp_path: Path, capsys):
    """prepare is retired: typed rejection and it must not generate any outputs."""
    ws = tmp_path / "ws"
    assert main(complete_init_args(ws, extra=["--source-profile", "conservative"])) == 0
    capsys.readouterr()
    before_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }

    result = main(["prepare", "--config", str(ws / "config.yaml")])
    captured = capsys.readouterr()

    # retired public `prepare` surface and its deprecation
    # message; the workspace authority guard rejects it before dispatch.
    assert result == 1
    assert captured.out == "runtime_command_unsupported\n"
    assert "/generate-brief" not in captured.out
    after_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
    assert not (ws / "output" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "intermediate" / "candidate_claims.json").exists()
    assert not (ws / "output" / "intermediate" / "screened_candidates.json").exists()
    assert not (ws / "output" / "intermediate" / "audited_brief.md").exists()
    assert not (ws / "output" / "intermediate" / "audit_report.json").exists()


def test_core_brief_pipeline_is_removed():
    assert not Path("src/multi_agent_brief/core/pipeline.py").exists()


def test_onboard_template_writes_json(tmp_path, capsys):
    """onboard --template writes a template onboarding.json."""
    out = tmp_path / "onboarding.json"
    exit_code = main(["onboard", "--template", "--output", str(out)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "company_or_org" in data
    assert "task_objective" in data
    assert "audience_plain" in data


def test_onboard_validate_accepts_valid_file(tmp_path, capsys):
    """onboard --validate accepts a complete onboarding.json."""
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({
        "company_or_org": "阿特斯",
        "industry_or_theme": "光伏",
        "task_objective": "行业简报",
    }), encoding="utf-8")
    exit_code = main(["onboard", "--validate", str(valid)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Required fields: OK" in captured.out


def test_onboard_validate_rejects_missing_fields(tmp_path, capsys):
    """onboard --validate returns 1 when required fields are missing."""
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({
        "audience_plain": "management",
    }), encoding="utf-8")
    exit_code = main(["onboard", "--validate", str(incomplete)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Missing required fields" in captured.out


def test_onboard_validate_rejects_invalid_json(tmp_path, capsys):
    """onboard --validate returns 1 for invalid JSON."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    exit_code = main(["onboard", "--validate", str(bad)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Invalid JSON" in captured.out


def test_onboard_validate_rejects_missing_file(tmp_path, capsys):
    """onboard --validate returns 1 for nonexistent file."""
    exit_code = main(["onboard", "--validate", str(tmp_path / "nope.json")])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not found" in captured.out
