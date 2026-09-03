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
