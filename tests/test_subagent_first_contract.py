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
        "docs/features.md",
        "docs/features.zh-CN.md",
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
    assert (
        "Python CLI commands provide onboarding, workspace setup, runtime handoff"
        in text
    )
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
    assert "briefloop onboard" in text
    assert "briefloop init <workspace> --from-onboarding onboarding.json" in text
    assert "briefloop run --workspace <workspace>" in text


def test_readme_first_screen_uses_briefloop_as_writer_command():
    readme_paths = ["README.md", "README.zh-CN.md"]
    for path in readme_paths:
        text = _read(path)
        first_screen = "\n".join(text.splitlines()[:32])
        assert "/briefloop" in first_screen
        assert "/mabw" not in first_screen
        assert "multi-agent-brief" not in first_screen
        assert "MABW" not in first_screen
        assert "BriefLoop" in first_screen
        assert "/generate-brief" not in first_screen

    readme_en = _read("README_en.md")
    assert "English README has moved to [README.md](README.md)." in readme_en
