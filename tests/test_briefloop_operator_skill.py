from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / ".agents" / "skills" / "briefloop"
CLAUDE_WRAPPER = ROOT / ".claude" / "skills" / "briefloop" / "SKILL.md"
HERMES_MIRROR = ROOT / "integrations" / "hermes-plugin" / "mabw" / "skills" / "briefloop"


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
    assert "historical `auto` / `manual` manifests are read-only diagnostics" in text
    assert "WorkBuddy Skill source bundle" in text
    assert ".agents/skills/briefloop-workbuddy/" in text
    assert "canonical path" in text
    assert "integrations/workbuddy/briefloop/" in text
    assert "legacy mirror" in text
    assert "source-clone-only" in text
    assert "briefloop run --workspace <workspace> --runtime codebuddy" in text
    assert "`--runtime operator` is an explicit user-approved fallback only" in text
    assert "uses `--runtime operator`" not in text
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

    The stale wording family told operators to run the finalize gate and
    `state finalize-complete` after finalize "writes" artifacts, ignoring the
    transactional promotion result, or to report success from audit status
    alone. Every writer/adapter/skill surface must gate those steps on
    `delivery_promotion: "promoted"` and delivery truth instead.
    """
    stale_patterns = [
        re.compile(r"after (the )?finalize( tool)? writes", re.IGNORECASE),
        re.compile(r"success when audit status " + "supports delivery", re.IGNORECASE),
    ]
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
    scan_paths: list[Path] = []
    for root in roots:
        base = ROOT / root
        if not base.exists():
            continue
        scan_paths.extend(base.rglob("*"))
    # Repo-root guidance files (e.g. HERMES.md, AGENTS.md, README*.md) also ship
    # finalize guidance and must not drift back to the ungated wording family.
    scan_paths.extend(p for p in ROOT.glob("*") if p.is_file())
    offenders: list[str] = []
    for path in scan_paths:
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in stale_patterns:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert offenders == [], f"unconditional finalize guidance found in: {offenders}"


def test_finalize_complete_guidance_carries_promotion_gate() -> None:
    """Positive invariant, file-level: any prose guidance surface that teaches
    `state finalize-complete` must also carry the `delivery_promotion` gate.

    Blocklist scans catch known stale phrasings; this check catches novel
    paraphrases and omitted gate steps. Implementation modules and tests are
    enforced behaviorally and are out of scope here.
    """
    prose_roots = [
        "configs",
        ".agents",
        ".claude",
        ".codex",
        ".opencode",
        ".codebuddy",
        "integrations",
        "docs/agents",
    ]
    guidance_emitters = [
        "src/multi_agent_brief/orchestrator/handoff.py",
        "src/multi_agent_brief/hermes/adapter.py",
        "integrations/hermes-plugin/mabw/__init__.py",
        "scripts/generate_agent_configs.py",
    ]
    root_docs = [
        "HERMES.md",
        "AGENTS.md",
        "CLAUDE.md",
        "docs/claude-code-quickstart.md",
        "docs/claude-code-workflow.md",
        "docs/weekly-loop.md",
        "docs/golden-path.md",
    ]
    suffixes = {".md", ".toml", ".yaml", ".yml"}
    files: list[Path] = []
    for root in prose_roots:
        base = ROOT / root
        if not base.exists():
            continue
        files.extend(
            path
            for path in base.rglob("*")
            if path.is_file()
            and path.suffix in suffixes
            and "__pycache__" not in path.parts
            and "tests" not in path.parts
        )
    files.extend(ROOT / rel for rel in guidance_emitters)
    files.extend(ROOT / rel for rel in root_docs if (ROOT / rel).exists())

    offenders: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "state finalize-complete" in text and "delivery_promotion" not in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], (
        f"finalize-complete taught without the delivery_promotion gate in: {offenders}"
    )


def test_stage_completion_protocol_ships_promotion_and_delivery_truth_gate() -> None:
    """Positive gate: the finalize guidance embedded in every handoff must carry
    the transactional promotion + delivery-truth contract, not just avoid stale
    wording. This closes the coverage hole where a rule could drop the gate
    without matching any stale pattern.
    """
    from multi_agent_brief.orchestrator.handoff import (
        FINALIZE_GATE_NOTE,
        STAGE_COMPLETION_PROTOCOL_RULES,
    )

    protocol_text = "\n".join(STAGE_COMPLETION_PROTOCOL_RULES)
    assert 'delivery_promotion "promoted"' in protocol_text
    assert "delivery_truth.valid=true" in protocol_text
    assert 'delivery_promotion "promoted"' in FINALIZE_GATE_NOTE


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
    assert "Historical `auto` / `manual` manifests are read-only" in runtime
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
    assert "successful CLI `finalize-complete`" in status
    assert "automatically materializes" in status
    assert "explicit repair" in status
    assert "It is not the unique normal writer" in status
    assert "leaves any prior delivery bundle unchanged" in status
    assert "delivery_truth.valid=true" in runtime
    assert "Agent Artifact Intake" in runtime
    assert "Python assigns" in runtime
    assert "Use the owning CLI transaction instead." in control
    assert "agent draft surfaces" in control
    control_normalized = " ".join(control.split())
    assert "single delivery-truth record" in control_normalized
    assert "There is no separate delivery manifest" in control_normalized


def test_quality_panel_auto_materialization_contract_matches_hermes_mirror() -> None:
    for filename in ("status-and-gates.md", "version-matrix.md"):
        canonical = _read(CANONICAL / "references" / filename)
        mirror = _read(HERMES_MIRROR / "references" / filename)
        assert mirror == canonical
        assert "successful cli `finalize-complete`" in canonical.lower()
        assert "Artifact Registry" in canonical
        assert "repair" in canonical
        assert "not the unique normal writer" in canonical


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
