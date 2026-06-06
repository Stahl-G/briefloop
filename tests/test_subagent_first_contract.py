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


def test_agents_md_states_python_commands_are_tools_not_runtime():
    text = _read("AGENTS.md")
    assert "Python commands are tools, not the workflow runtime." in text
    assert "There is no Python brief-generation pipeline." in text
