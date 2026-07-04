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
    assert "briefloop-operator-skill-v0.1.2" in text
    assert f"v{_read(ROOT / 'VERSION').strip()}" in text
    assert "multi-agent-brief" in text
    assert "Shell CLI alias: `briefloop`" in text
    assert "Claude writer command: `/briefloop`" in text
    assert "/mabw" in text
    assert "`--runtime operator`: host-agnostic compact operator workflow" in text
    assert "`--runtime manual`: legacy compatibility alias resolved to `operator`" in text
    assert "BriefLoop skill is an agent protocol surface" in text
    assert "not the `/briefloop` slash" in text
    assert "auditable_brief" in text
    assert "delivery_brief" in text
    assert "Planned / Not Yet Authoritative" in text
    assert "Atomic Claim Graph" in text
    assert "Claim-Support Matrix" in text
    assert "Semantic Assessment Report" in text
    assert "proposal-only Claim-Support Matrix delta projection" in text
    assert "briefloop quality summarize --workspace <workspace>" in text
    assert "quality_panel.json" in text
    assert "quality_summary.md" in text
    assert "quality_panel.html" in text
    assert "approval init" in text
    assert "approval record" in text
    assert "release check" in text
    assert "release_readiness_report.json" in text
    assert "event-log linkage is required" in text
    assert "`industry-weekly` -> canonical ReportPack `market_weekly`" in text
    assert "`document-review` -> canonical ReportPack `evidence_extract`" in text
    assert "scripts/check_product_baseline.py" in text
    assert "MABW-080 experiment operations" in text
    assert "MABW-080 / BriefLoop-090 experiment operations" not in text
    assert "BriefLoop-090 is a future readiness/fresh-rerun label" in text
    assert "not a current CLI namespace" in text
    assert "no stage execution from Product OS commands" in text


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


def test_runtime_status_and_control_references_track_quality_and_release_surfaces() -> None:
    runtime = _read(CANONICAL / "references" / "runtime-workspace.md")
    status = _read(CANONICAL / "references" / "status-and-gates.md")
    control = _read(CANONICAL / "references" / "control-record-map.md")

    for text in [runtime, status, control]:
        assert "quality_panel.json" in text
        assert "quality_summary.md" in text
        assert "quality_panel.html" in text
        assert "human_approval_ledger.json" in text
        assert "release_readiness_report.json" in text

    assert "briefloop quality summarize --workspace <workspace>" in runtime
    assert "multi-agent-brief run --workspace <workspace> --runtime operator" in runtime
    assert "Legacy `--runtime manual` remains a compatibility alias only." in runtime
    assert "not a gate runner" in runtime
    assert "Approval ledger records must be scoped to the current run" in status
    assert "branding_context" in status
    assert "SHA-256 binding" in status
    assert "Use the owning CLI transaction instead." in control
    assert "agent draft surfaces" in control


def test_repo_development_reference_includes_product_baseline_and_review_checklist() -> None:
    text = _read(CANONICAL / "references" / "repo-development.md")

    assert "python3 scripts/check_product_baseline.py" in text
    assert "direct import smoke" in text
    assert "hand-edited artifact smoke" in text
    assert "invalid optional artifacts must not become authority" in text
    assert "projection artifacts must not create gate, release, or delivery authority" in text
    assert "README_en.md` compatibility-pointer shape" in text


def test_naming_reference_tracks_readme_pointer_and_product_aliases() -> None:
    text = _read(CANONICAL / "references" / "naming-and-compatibility.md")

    assert "`README.md` is the canonical English README" in text
    assert "`README.zh-CN.md` is the canonical Chinese README" in text
    assert "`README_en.md` is only a short compatibility pointer" in text
    assert "`industry-weekly` -> internal/canonical `market_weekly`" in text
    assert "`management-monthly` -> internal/canonical `management_monthly`" in text
    assert "`document-review` -> internal/canonical `evidence_extract`" in text
    assert "`solar-periodic` -> internal/canonical `solar_industry_periodic`" in text
    assert "Do not write product-facing aliases into control artifacts" in text
