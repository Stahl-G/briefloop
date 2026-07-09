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
    assert "archived MABW-080 / BriefLoop-090 experiment tooling" in text
    assert "future 090 readiness" not in text
    assert len(text.splitlines()) <= 24


def test_version_matrix_tracks_current_surface_without_planned_overclaim() -> None:
    text = _read(CANONICAL / "references" / "version-matrix.md")
    assert "briefloop-operator-skill-v0.2.0" in text
    assert f"v{_read(ROOT / 'VERSION').strip()}" in text
    assert "v1.0 RC Landed Surfaces" in text
    assert "Pending Before v1.0" in text
    assert "single delivery-truth record" in text
    assert "briefloop workbuddy diagnose --workspace <workspace>" in text
    assert "repair supersede-stage" in text
    assert "not_satisfied" in text
    assert "not yet landed" in text
    assert "Public CLI: `briefloop`" in text
    assert "Compatibility CLI: `multi-agent-brief`" in text
    assert "Claude writer command: `/briefloop`" in text
    assert "/mabw" in text
    assert "`--runtime operator`: host-agnostic compact operator workflow" in text
    assert "`--runtime manual`: legacy compatibility alias resolved to `operator`" in text
    assert "WorkBuddy Skill source bundle" in text
    assert ".agents/skills/briefloop-workbuddy/" in text
    assert "canonical path" in text
    assert "integrations/workbuddy/briefloop/" in text
    assert "legacy mirror" in text
    assert "source-clone-only" in text
    assert "workbuddy pack-skill" in text
    assert "not a WorkBuddy Marketplace publication" in text
    assert "CodeBuddy project Skill adapter" in text
    assert ".codebuddy/skills/briefloop/" in text
    assert "must not use `context: fork`" in text
    assert "used by `--runtime codebuddy` handoff" in text
    assert "CodeBuddy project role agents" in text
    assert ".codebuddy/agents/briefloop-*.md" in text
    assert "main CodeBuddy session remains responsible for deterministic transactions" in text
    assert "CodeBuddy runtime handoff" in text
    assert "`--runtime codebuddy`: experimental handoff" in text
    assert "runtime_capabilities.delegation_supported" in text
    assert "runtime_capabilities.nested_subagents_supported" in text
    assert "runtime_capabilities.role_agents_run_cli_transactions" in text
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
    assert "Archived MABW-080 experiment operations" in text
    assert "MABW-080 / BriefLoop-090 experiment operations" not in text
    assert "BriefLoop-090 is an archived experiment/readiness label" in text
    assert "not a current CLI namespace" in text
    assert "no stage execution from Product OS commands" in text


def test_experiment_reference_separates_targets_and_stops_finalize() -> None:
    text = _read(CANONICAL / "references" / "experiment-080-090.md")
    assert "auditable_brief" in text
    assert "delivery_brief" in text
    assert "do not run finalize or delivery" in text
    assert "not management-ready delivery" in text
    assert "BriefLoop-090 is the archived/readiness label" in text
    assert "MABW-080 remains the shipped experiment" in text
    assert "command namespace" in text
    assert "not ordinary brief-delivery commands" in text


def test_experiment_reference_uses_formal_blind_command_loop() -> None:
    text = _read(CANONICAL / "references" / "experiment-080-090.md")
    assert "validate-case --case" not in text
    assert "briefloop experiments 080 validate-case <case_dir>" in text
    assert "--blind-pack <blind_pack_dir>/blind_pack.json" in text
    assert "--reveal-mapping <blind_pack_dir>/reveal_mapping.json" in text
    assert "--scorecard <baseline_scorecard.json>" in text
    assert "--scorecard <memory_scorecard.json>" in text
    assert "--scorecard <prompt_only_scorecard.json>" in text


def test_repair_reference_requires_transaction_path() -> None:
    text = _read(CANONICAL / "references" / "repair-protocol.md")
    assert "briefloop gates show --workspace <workspace> --json" in text
    assert "briefloop repair route --workspace <workspace> --json" in text
    assert "--gate-stage" in text
    assert "--gate-artifact" in text
    assert "--finding-id <finding_id>" in text
    assert "--route-index <route_index>" in text
    assert "do not use unscoped repair" in text
    assert "Do not use bare" in text
    assert "briefloop repair complete" in text
    assert "briefloop repair supersede-stage --workspace <workspace>" in text
    assert "old registered hash, current bytes hash, and reason" in text
    assert "original contamination event" in text
    assert "allowed_artifacts" in text
    assert "does not make a contaminated run clean" in text
    assert "downstream artifacts are marked stale" in text
    assert "cannot be superseded without routing" in text


def test_public_claims_and_red_lines_forbid_overclaims() -> None:
    public_claims = _read(CANONICAL / "references" / "public-claims.md")
    red_lines = _read(CANONICAL / "references" / "red-lines.md")
    for phrase in [
        "Do not say:",
        "BriefLoop proves truth",
        "BriefLoop eliminates hallucinations",
        "automatically ready to send",
        "Improvement Memory improves output quality",
        "RC-Phase Wording",
        "not_satisfied",
        "BriefLoop v1.0 is ready.",
        "BriefLoop can safely recover contaminated runs.",
    ]:
        assert phrase in public_claims
    assert "Do not edit frozen artifacts in place." in red_lines
    assert "Do not edit control files" in red_lines


def test_finalize_guidance_is_promotion_gated_everywhere() -> None:
    """No guidance surface may teach unconditional post-finalize completion.

    The stale wording pattern told operators to run the finalize gate and
    `state finalize-complete` "after finalize writes" artifacts, ignoring the
    transactional promotion result. Every writer/adapter/skill surface must
    gate those steps on `delivery_promotion: "promoted"` instead.
    """
    stale = "After finalize " + "writes"
    roots = [
        "configs",
        ".agents",
        ".claude",
        ".codex",
        ".opencode",
        ".codebuddy",
        "integrations",
        "src/multi_agent_brief",
        "docs/agents",
    ]
    suffixes = {".md", ".py", ".toml", ".yaml", ".yml", ".json"}
    offenders: list[str] = []
    for root in roots:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if stale in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"unconditional finalize guidance found in: {offenders}"


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
    assert "briefloop run --workspace <workspace> --runtime operator" in runtime
    assert "briefloop run --workspace <workspace> --runtime codebuddy" in runtime
    assert "Legacy `--runtime manual` remains a compatibility alias only." in runtime
    assert ".codebuddy/skills/briefloop/" in runtime
    assert ".codebuddy/agents/briefloop-*.md" in runtime
    assert "deterministic CLI transactions to the main session" in runtime
    assert "`agent_owned_drafts`, `cli_owned_outputs`, `read_only_diagnostics`" in runtime
    assert "`forbidden_direct_edits`" in runtime
    assert "not a gate runner" in runtime
    assert "Approval ledger records must be scoped to the current run" in status
    assert "branding_context" in status
    assert "SHA-256 binding" in status
    assert "Completion And Delivery Truth" in status
    assert "delivery_truth.valid=true" in status
    assert "leaves any prior delivery bundle unchanged" in status
    assert "delivery_truth.valid=true" in runtime
    assert "Agent Artifact Intake" in runtime
    assert "Python assigns" in runtime
    assert "Use the owning CLI transaction instead." in control
    assert "agent draft surfaces" in control
    control_normalized = " ".join(control.split())
    assert "single delivery-truth record" in control_normalized
    assert "There is no separate delivery manifest" in control_normalized


def test_repo_development_reference_includes_product_baseline_and_review_checklist() -> None:
    text = _read(CANONICAL / "references" / "repo-development.md")

    assert "v1.0 RC Readiness Gate" in text
    assert "docs/v1-pilot-evidence.md" in text
    assert "check_v1_rc_readiness.py" in text
    assert "--require-satisfied" in text
    assert "python3 scripts/check_product_baseline.py" in text
    assert "direct import smoke" in text
    assert "hand-edited artifact smoke" in text
    assert "invalid optional artifacts must not become authority" in text
    assert "projection artifacts must not create gate, release, or delivery authority" in text
    assert "README_en.md` compatibility-pointer shape" in text


def test_naming_reference_tracks_readme_pointer_and_product_aliases() -> None:
    text = _read(CANONICAL / "references" / "naming-and-compatibility.md")
    normalized = " ".join(text.split())

    assert "`README.md` is the canonical English README" in text
    assert "`README.zh-CN.md` is the canonical Chinese README" in text
    assert "`README_en.md` is only a short compatibility pointer" in text
    assert "`industry-weekly` -> internal/canonical `market_weekly`" in text
    assert "`management-monthly` -> internal/canonical `management_monthly`" in text
    assert "`document-review` -> internal/canonical `evidence_extract`" in text
    assert "`solar-periodic` -> internal/canonical `solar_industry_periodic`" in text
    assert "Do not write product-facing aliases into control artifacts" in text
    assert "Do not present `/generate-brief` as a recommended first-user writer path" in text
    assert "supported Claude delegated stage-workflow command" in text
    assert "does not execute specialists or complete stages" in normalized
    assert "legacy direct-delegation/debug command" not in text


def test_public_naming_doc_aligns_generate_brief_with_claude_handoff() -> None:
    text = _read(ROOT / "docs" / "briefloop-naming.md")

    assert "Claude delegated stage workflow" in text
    assert "Supported when following generated Claude handoff" in text
    assert "not a first-user writer path" in text
    assert "/briefloop run <workspace>` only creates or refreshes handoff files" in text
    assert "Compatibility/debug only" not in text
