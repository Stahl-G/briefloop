from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_python_agent_package_removed_from_runtime_source():
    assert not (ROOT / "src" / "multi_agent_brief" / "agents").exists()


def test_role_agent_class_names_do_not_reappear_in_src():
    forbidden = [
        "class ScoutAgent",
        "class ScreenerAgent",
        "class AnalystAgent",
        "class EditorAgent",
        "class AuditorAgent",
        "class FormatterAgent",
        "from multi_agent_brief.agents",
        "multi_agent_brief.agents.",
    ]
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token!r} found in {path.relative_to(ROOT)}"


def test_user_facing_docs_do_not_present_prepare_as_workflow_runtime():
    docs = [
        "README.md",
        "README_en.md",
        "AGENTS.md",
        ".claude/commands/generate-brief.md",
        ".opencode/commands/generate-brief.md",
        "docs/features.md",
        "docs/features.zh-CN.md",
        "docs/claude-code-quickstart.md",
        "docs/claude-code-workflow.md",
    ]
    forbidden = [
        "Run deterministic pipeline",
        "运行确定性管线",
        "Python CLI prepares deterministic",
        "multi-agent-brief prepare --config",
        "deterministic Python pipeline",
        "Python 确定性管线",
    ]
    for doc in docs:
        text = _read(doc)
        for token in forbidden:
            assert token not in text, f"{token!r} found in {doc}"


def test_agents_md_states_python_commands_are_support_tools():
    text = _read("AGENTS.md")
    assert "Python CLI commands provide onboarding, workspace setup, runtime handoff" in text
    assert "subagent-first" in text


def test_agents_md_stays_bounded_and_actionable():
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 220
    assert "Environment Separation" in text
    assert "Version And Release Semantics" in text
    assert "Packaging And Install Paths" in text
    assert "Common Validation" in text


def test_agents_md_uses_standard_entry_path():
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "multi-agent-brief onboard" in text
    assert "multi-agent-brief init <workspace> --from-onboarding onboarding.json" in text
    assert "multi-agent-brief run --workspace <workspace>" in text


def test_claude_generate_brief_command_uses_orchestrator_contract():
    text = _read(".claude/commands/generate-brief.md")
    assert "Orchestrator main agent" in text
    assert "configs/orchestrator_contract.yaml" in text
    assert "configs/stage_specs.yaml" in text
    assert "configs/artifact_contracts.yaml" in text
    assert "multi-agent-brief run --workspace $ARGUMENTS --runtime claude --skip-doctor" in text
    assert "output/intermediate/audience_profile_snapshot.md" in text
    assert "Do not treat `audience_profile.md` as source evidence" in text
    assert "retry_stage" in text
    assert "request_human_review" in text
    assert "block_run" in text
    assert "Check the expected artifact" in text


def test_opencode_generate_brief_command_uses_audience_snapshot_context():
    text = _read(".opencode/commands/generate-brief.md")
    assert "Orchestrator main agent" in text
    assert "multi-agent-brief run --workspace $ARGUMENTS --runtime opencode --skip-doctor" in text
    assert "output/intermediate/audience_profile_snapshot.md" in text
    assert "Do not treat `audience_profile.md` as source evidence" in text
    assert "Check the expected artifact" in text
