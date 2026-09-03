from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = ROOT / ".agents" / "skills"


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


def test_skill_folders_are_kebab_case_and_match_names():
    for skill_dir in SKILL_ROOT.iterdir():
        if not skill_dir.is_dir():
            continue
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill_dir.name)
        skill = skill_dir / "SKILL.md"
        assert skill.exists()
        fm = _frontmatter(_read(skill))
        assert fm["name"] == skill_dir.name


def test_briefloop_skill_locks_explicit_successor_guidance_boundary():
    canonical = SKILL_ROOT / "briefloop"
    packaged = (
        ROOT
        / "src"
        / "multi_agent_brief"
        / "runtime_kits"
        / "codex"
        / "skills"
        / "briefloop"
    )
    corpora = [
        _read(canonical / "SKILL.md")
        + "\n"
        + _read(canonical / "references" / "codex-controlstore-v2.md"),
        _read(packaged / "SKILL.md")
        + "\n"
        + _read(packaged / "references" / "controlstore-v2.md"),
    ]
    for corpus in corpora:
        normalized = " ".join(corpus.split())
        for phrase in (
            "briefloop runtime successor-start",
            "--include-approved-guidance",
            "FrozenGuidanceContext",
            "audience fit, structure, style, and expression",
            "Current `RunDirection` and evidence govern",
            "review-open` remains current-head-only",
        ):
            assert phrase in normalized
        assert re.search(
            r"16(?:-item| items).*65,536(?:-byte|[^.]*UTF-8 bytes)",
            normalized,
        )
        assert (
            "not a sixth `CoreRunNextAction` kind" in normalized
            or "not a `CoreRunNextAction` kind" in normalized
        )

    assert (canonical / "references" / "codex-controlstore-v2.md").read_bytes() == (
        packaged / "references" / "controlstore-v2.md"
    ).read_bytes()
