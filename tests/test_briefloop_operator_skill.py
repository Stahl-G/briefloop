from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / ".agents" / "skills" / "briefloop"
CLAUDE_WRAPPER = ROOT / ".claude" / "skills" / "briefloop" / "SKILL.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_briefloop_skill_uses_repo_skill_contract_structure() -> None:
    text = _read(CANONICAL / "SKILL.md")
    for heading in ["## Scope", "## Purpose", "## Use When", "## Inputs", "## Outputs", "## Work", "## Handoff"]:
        assert heading in text


def test_briefloop_skill_references_exist() -> None:
    text = _read(CANONICAL / "SKILL.md")
    references = sorted(re.findall(r"references/[a-z0-9-]+\.md", text))
    assert references
    for reference in references:
        assert (CANONICAL / reference).exists(), reference


def test_briefloop_skill_classifies_core_modes() -> None:
    text = _read(CANONICAL / "SKILL.md")
    for mode in ["runtime-workspace", "experiment-080-090", "repo-development", "public-claims"]:
        assert mode in text


def test_claude_projection_is_thin_wrapper() -> None:
    text = _read(CLAUDE_WRAPPER)
    assert ".agents/skills/briefloop/SKILL.md" in text
    assert "canonical" in text.lower()
    assert len(text.splitlines()) <= 24


def test_version_matrix_tracks_current_surface_without_planned_overclaim() -> None:
    text = _read(CANONICAL / "references" / "version-matrix.md")
    assert "v0.10.7" in text
    assert "multi-agent-brief" in text
    assert "Shell CLI alias: `briefloop`" in text
    assert "Claude writer command: `/briefloop`" in text
    assert "/mabw" in text
    assert "BriefLoop skill is an agent protocol surface" in text
    assert "not the `/briefloop` slash" in text
    assert "auditable_brief" in text
    assert "delivery_brief" in text
    assert "Planned / Not Yet Authoritative" in text
    assert "Atomic Claim Graph" in text
    assert "Claim-Support Matrix" in text
    assert "Semantic Assessment Report" in text
    assert "proposal-only Claim-Support Matrix delta projection" in text
    assert "MABW-080 experiment operations" in text
    assert "MABW-080 / BriefLoop-090 experiment operations" not in text
    assert "BriefLoop-090 is a future readiness/fresh-rerun label" in text
    assert "not a current CLI namespace" in text


def test_experiment_reference_separates_targets_and_stops_finalize() -> None:
    text = _read(CANONICAL / "references" / "experiment-080-090.md")
    assert "auditable_brief" in text
    assert "delivery_brief" in text
    assert "do not run finalize or delivery" in text
    assert "not management-ready delivery" in text
    assert "BriefLoop-090 is a future experiment/readiness label" in text
    assert "MABW-080 remains the shipped experiment command namespace" in text


def test_experiment_reference_uses_formal_blind_command_loop() -> None:
    text = _read(CANONICAL / "references" / "experiment-080-090.md")
    assert "validate-case --case" not in text
    assert "multi-agent-brief experiments 080 validate-case <case_dir>" in text
    assert "--blind-pack <blind_pack_dir>/blind_pack.json" in text
    assert "--reveal-mapping <blind_pack_dir>/reveal_mapping.json" in text
    assert "--scorecard <baseline_scorecard.json>" in text
    assert "--scorecard <memory_scorecard.json>" in text
    assert "--scorecard <prompt_only_scorecard.json>" in text


def test_repair_reference_requires_transaction_path() -> None:
    text = _read(CANONICAL / "references" / "repair-protocol.md")
    assert "multi-agent-brief repair route" in text
    assert "multi-agent-brief repair start" in text
    assert "multi-agent-brief repair complete" in text
    assert "allowed_artifacts" in text
    assert "does not make a contaminated run clean" in text


def test_public_claims_and_red_lines_forbid_overclaims() -> None:
    public_claims = _read(CANONICAL / "references" / "public-claims.md")
    red_lines = _read(CANONICAL / "references" / "red-lines.md")
    for phrase in [
        "Do not say:",
        "BriefLoop proves truth",
        "BriefLoop eliminates hallucinations",
        "automatically ready to send",
        "Improvement Memory improves output quality",
    ]:
        assert phrase in public_claims
    assert "Do not edit frozen artifacts in place." in red_lines
    assert "Do not edit control files" in red_lines
