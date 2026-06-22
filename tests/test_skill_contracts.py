from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = ROOT / ".agents" / "skills"
HERMES_SKILL = ROOT / ".agents" / "hermes-skills" / "multi-agent-brief-hermes"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n")
    end = text.find("\n---\n", 4)
    assert end != -1
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip()
    return data


def test_agents_directory_scope_wall_exists():
    text = _read(ROOT / ".agents" / "AGENTS.md")
    assert "runtime skill contracts" in text
    assert "capability contracts" in text
    assert "delegate_task" in text


def test_skill_folders_are_kebab_case_and_match_names():
    for skill_dir in SKILL_ROOT.iterdir():
        if not skill_dir.is_dir():
            continue
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill_dir.name)
        skill = skill_dir / "SKILL.md"
        assert skill.exists()
        fm = _frontmatter(_read(skill))
        assert fm["name"] == skill_dir.name


def test_skill_descriptions_are_routing_descriptions():
    for skill in SKILL_ROOT.glob("*/SKILL.md"):
        fm = _frontmatter(_read(skill))
        description = fm.get("description", "")
        assert len(description) <= 1024
        assert "Use " in description or "Use when" in description
        assert "<" not in description and ">" not in description


def test_skills_use_contract_structure():
    required = ["## Scope", "## Purpose", "## Use When", "## Inputs", "## Outputs", "## Work", "## Handoff"]
    for skill in SKILL_ROOT.glob("*/SKILL.md"):
        text = _read(skill)
        for heading in required:
            assert heading in text, f"{skill} missing {heading}"


def test_skills_do_not_restore_old_generic_contracts():
    forbidden = [
        "Expected Inputs",
        "Expected Outputs",
        "Subagent workflow Context",
        "preparation artifacts",
        "draft_brief.md",
        "source_map.md",
        "Structured artifacts conforming to the workflow contract",
    ]
    for skill in SKILL_ROOT.glob("*/SKILL.md"):
        text = _read(skill)
        for phrase in forbidden:
            assert phrase not in text, f"{skill} contains old generic phrase: {phrase}"


def test_source_writer_skills_lock_source_metadata_fields():
    scout = _read(SKILL_ROOT / "scout" / "SKILL.md")
    claim_ledger = _read(SKILL_ROOT / "claim-ledger" / "SKILL.md")

    assert "source_url` for HTTP(S) web sources" in scout
    assert "source_path` for local/package sources" in scout
    assert "Never put a title, search query" in scout
    assert "`source_type` is provider/storage type" in scout
    assert "`source_category` is reader-facing evidence category" in scout

    assert "`source_url` is only for HTTP(S) URLs" in claim_ledger
    assert "Do not put titles, source names, source IDs, search queries, or local paths" in claim_ledger
    assert "`source_path` plus `source_title` or `source_name` and `source_category`" in claim_ledger


def test_hermes_skill_uses_progressive_disclosure():
    skill = HERMES_SKILL / "SKILL.md"
    text = _read(skill)
    assert len(text.splitlines()) <= 140
    assert "references/delegate-task-sequence.md" in text
    assert "references/source-cache-contract.md" in text
    assert "references/cron-patterns.md" in text
    assert (HERMES_SKILL / "references" / "delegate-task-sequence.md").exists()
    assert (HERMES_SKILL / "references" / "source-cache-contract.md").exists()
    assert (HERMES_SKILL / "references" / "cron-patterns.md").exists()


def test_hermes_frontmatter_is_routable():
    fm = _frontmatter(_read(HERMES_SKILL / "SKILL.md"))
    assert fm["name"] == "multi-agent-brief-hermes"
    assert "Use when" in fm["description"]
    assert len(fm["description"]) <= 1024
    assert "delegate_task" in fm["description"]
