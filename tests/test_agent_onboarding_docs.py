"""Tests for agent-facing onboarding docs: safety, completeness, and structure."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_agent_onboarding_docs_public_safe():
    """Agent docs must not contain private-looking sentinel examples."""
    forbidden = [
        "ACME Corp Internal",  # not a real private name, just a sentinel
        "SECRET_PROJECT_X",
        "DO_NOT_SHIP_REAL_NAME",
    ]
    files = [
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / ".agents" / "skills" / "brief-onboarding" / "SKILL.md",
        ROOT / ".claude" / "commands" / "init-brief.md",
        ROOT / "docs" / "onboarding.md",
    ]
    for fpath in files:
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8")
        for sentinel in forbidden:
            assert sentinel not in text, (
                f"Private sentinel '{sentinel}' found in {fpath}"
            )


def test_agent_onboarding_docs_do_not_expose_internal_fields_as_user_questions():
    """Docs may mention internal fields in 'do not expose' lists,
    but must not present them as user-facing questions."""
    user_question_patterns = [
        "What source_profile",
        "What selector_max_items",
        "What retrieval_provider",
        "What output_formats",
        "Please choose source_profile",
        "Please select selector_max_items",
    ]
    files = [
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / ".agents" / "skills" / "brief-onboarding" / "SKILL.md",
        ROOT / ".claude" / "commands" / "init-brief.md",
    ]
    for fpath in files:
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8")
        for pattern in user_question_patterns:
            assert pattern not in text, (
                f"Internal field exposed as user question in {fpath}: '{pattern}'"
            )


def test_codex_skill_exists():
    skill_path = ROOT / ".agents" / "skills" / "brief-onboarding" / "SKILL.md"
    assert skill_path.exists(), f"Skill not found: {skill_path}"
    text = skill_path.read_text(encoding="utf-8")
    for keyword in ("initialize", "start", "configure", "set up", "onboarding.json"):
        assert keyword in text, (
            f"Skill missing keyword '{keyword}': {skill_path}"
        )


def test_claude_policy_disallows_required_free_text_askuserquestion():
    claude_path = ROOT / "CLAUDE.md"
    assert claude_path.exists(), "CLAUDE.md not found"
    text = claude_path.read_text(encoding="utf-8")
    assert "Do not use AskUserQuestion as the primary onboarding path" in text
    assert "Never use AskUserQuestion for required free-text fields" in text
