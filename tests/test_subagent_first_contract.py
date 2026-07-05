from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_scout_chunk_contract(path: str) -> None:
    text = _read(path)
    normalized = " ".join(text.replace("`", "").split())

    assert "scratch/intermediate runtime material" in normalized, path
    assert "not workflow artifacts" in normalized, path
    assert "deterministically" in normalized, path
    assert "source identity" in normalized, path
    assert "source path or URL" in normalized, path
    assert "source date" in normalized, path
    assert "topic" in normalized, path
    assert "evidence text" in normalized, path
    assert "completion order" in normalized, path
    assert (
        "append to candidate_claims.json from chunk workers" in normalized
        or "join chunk outputs deterministically before writing candidate_claims.json" in normalized
        or "join chunks deterministically before writing candidate_claims.json" in normalized
    ), path
    assert "silently drop" in normalized, path


def _assert_source_metadata_contract(path: str) -> None:
    text = _read(path)
    normalized = " ".join(text.replace("`", "").split())

    assert "source_url only for HTTP(S) URLs" in normalized, path
    assert "source_path for local/package sources" in normalized, path
    assert "source_category" in normalized, path
    assert "source_type" in normalized, path
    assert "Never put titles" in normalized, path


def _assert_generate_brief_repair_transaction(path: str) -> None:
    text = _read(path)
    normalized = " ".join(text.replace("`", "").split())

    assert "Do not call briefloop run again mid-pipeline" in normalized, path
    assert "briefloop repair route --workspace $ARGUMENTS --json" in normalized, path
    assert "briefloop repair start --workspace $ARGUMENTS --json" in normalized, path
    assert "briefloop repair complete --workspace $ARGUMENTS --reason" in normalized, path
    assert "do not edit artifacts directly" in normalized.lower(), path
    assert "Do not edit frozen artifacts directly" in normalized, path
    assert "Direct edits will mark the run contaminated and non-reference-eligible" in normalized, path
    assert "reported repair_owner role" in normalized, path
    assert "allowed_artifacts" in normalized, path
    assert "blocked_direct_edits" in normalized, path
    assert "must_rerun_from" in normalized, path
    assert "Never use state decide delegate_repair to authorize artifact edits" in normalized, path
    assert "Never manually update artifact_registry.json or frozen hashes" in normalized, path


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
        ".claude/commands/mabw.md",
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


def test_agents_md_states_python_commands_are_support_tools():
    text = _read("AGENTS.md")
    assert "Python CLI commands provide onboarding, workspace setup, runtime handoff" in text
    assert "subagent-first" in text


def test_agents_md_stays_bounded_and_actionable():
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 220
    assert "Environment Separation" in text
    assert "Version And Release Semantics" in text
    assert "Packaging And Install Paths" in text
    assert "Common Validation" in text


def test_generate_brief_commands_lock_source_metadata_contract():
    for path in [
        ".claude/commands/generate-brief.md",
        ".opencode/commands/generate-brief.md",
    ]:
        _assert_source_metadata_contract(path)


def test_agents_md_uses_standard_entry_path():
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "multi-agent-brief onboard" in text
    assert "multi-agent-brief init <workspace> --from-onboarding onboarding.json" in text
    assert "multi-agent-brief run --workspace <workspace>" in text


def test_claude_generate_brief_command_uses_orchestrator_contract():
    text = _read(".claude/commands/generate-brief.md")
    assert "Orchestrator main agent" in text
    assert "configs/orchestrator_contract.yaml" in text
    assert "configs/stage_specs.yaml" in text
    assert "configs/artifact_contracts.yaml" in text
    assert "briefloop run --workspace $ARGUMENTS --runtime claude --skip-doctor" in text
    assert "output/intermediate/audience_profile_snapshot.md" in text
    assert "output/intermediate/orchestrator_control_switchboard.json" in text
    assert "briefloop controls select" in text
    assert "selection is not execution" in text
    assert "Do not treat `audience_profile.md` as source evidence" in text
    assert "retry_stage" in text
    assert "request_human_review" in text
    assert "block_run" in text
    assert "Check the expected artifact" in text


def test_claude_generate_brief_requires_stage_complete_before_next_specialist():
    text = _read(".claude/commands/generate-brief.md")
    assert "A stage is not complete when its artifact is written." in text
    assert "Only after `stage-complete` succeeds may you dispatch the next specialist" in text
    assert "Do not call the next specialist" in text
    assert "Never treat `state stage-complete` as after-the-fact bookkeeping" in text
    assert "output artifacts are frozen for downstream stages" in text
    assert "route repair back to the owner stage" in text


def test_generate_brief_commands_use_repair_transaction_for_blocking_gates():
    for path in [
        ".claude/commands/generate-brief.md",
        ".opencode/commands/generate-brief.md",
    ]:
        _assert_generate_brief_repair_transaction(path)


def test_claude_generate_brief_is_topology_aware_for_scout_and_screener():
    text = _read(".claude/commands/generate-brief.md")
    assert "With `role_topology=default`, Scout writes both `candidate_claims.json`" in text
    assert "`screened_candidates.json` before `stage-complete --stage scout`" in text
    assert "Do not delegate Screener in default topology" in text
    assert "do not call `state stage-complete --stage screener`" in text
    assert "Do not replay Screener delegation or `stage-complete --stage screener`" in text
    assert "Strict topology only: invoke the **screener** subagent" in text
    assert "With `role_topology=strict`, Scout writes only `candidate_claims.json`" in text
    assert "strict topology delegates Screener separately after Scout completion" in text


def test_checked_in_hermes_skill_is_topology_aware_for_scout_and_screener():
    for path in [
        ".agents/hermes-skills/multi-agent-brief-hermes/SKILL.md",
        ".agents/hermes-skills/multi-agent-brief-hermes/references/delegate-task-sequence.md",
    ]:
        text = _read(path)
        normalized = " ".join(text.lower().split())
        assert "default topology" in text, path
        assert "screened_candidates.json" in text, path
        assert "do not delegate screener" in normalized, path
        assert "do not call `state stage-complete --stage screener`" in normalized, path
        assert "strict" in normalized and "screener" in normalized, path


def test_scout_contract_treats_chunk_outputs_as_scratch_only():
    paths = [
        "configs/agent_roles.yaml",
        ".agents/skills/scout/SKILL.md",
        ".agents/hermes-skills/multi-agent-brief-hermes/SKILL.md",
        ".agents/hermes-skills/multi-agent-brief-hermes/references/delegate-task-sequence.md",
        ".claude/commands/generate-brief.md",
        ".claude/agents/scout.md",
        ".codex/agents/scout.toml",
        ".opencode/commands/generate-brief.md",
        ".opencode/agents/brief-scout.md",
    ]
    for path in paths:
        _assert_scout_chunk_contract(path)


def test_claim_ledger_skill_accepts_screened_candidates_from_any_topology():
    text = _read(".agents/skills/claim-ledger/SKILL.md")
    assert "after screener writes" not in text.lower()
    assert "after screener has written" not in text.lower()
    assert "screened_candidates.json exists" in text
    assert "default Scout" in text
    assert "strict Screener" in text


def test_claude_generate_brief_runs_claim_ledger_freeze_transaction():
    text = _read(".claude/commands/generate-brief.md")
    claim_ledger_section = text.split("8. Invoke the **claim-ledger** subagent:", 1)[1].split(
        "9. **Market & Competitor Module", 1
    )[0]
    assert "claim_drafts.json" in claim_ledger_section
    assert "without `claim_id` fields" in claim_ledger_section
    assert "state freeze-claim-ledger" in claim_ledger_section
    assert "Confirm freeze produced `$ARGUMENTS/output/intermediate/claim_ledger.json`" in claim_ledger_section
    assert "Write `$ARGUMENTS/output/intermediate/claim_ledger.json`" not in claim_ledger_section


def test_analyst_prompts_use_frozen_ledger_only():
    prompt_paths = [
        "configs/agent_roles.yaml",
        ".agents/skills/analyst/SKILL.md",
        ".agents/hermes-skills/multi-agent-brief-hermes/references/delegate-task-sequence.md",
        ".claude/commands/generate-brief.md",
        ".claude/agents/analyst.md",
        ".codex/agents/analyst.toml",
        ".opencode/agents/brief-analyst.md",
    ]
    for path in prompt_paths:
        text = _read(path)
        assert "claim_drafts.json" in text, f"claim_drafts boundary missing in {path}"
        assert "Do not read" in text, f"claim_drafts read prohibition missing in {path}"
        assert "frozen" in text.lower(), f"frozen ledger wording missing in {path}"
        assert "Do not create, edit, rewrite, or repair" in text, (
            f"ledger mutation prohibition missing in {path}"
        )


def test_auditor_prompts_check_overstatement_and_support_calibration():
    prompt_paths = [
        "configs/agent_roles.yaml",
        ".agents/skills/auditor/SKILL.md",
        ".agents/hermes-skills/multi-agent-brief-hermes/references/delegate-task-sequence.md",
        ".claude/commands/generate-brief.md",
        ".claude/agents/auditor.md",
        ".codex/agents/auditor.toml",
        ".opencode/agents/brief-auditor.md",
    ]
    for path in prompt_paths:
        text = _read(path)
        assert "overstatement" in text, f"overstatement check missing in {path}"
        assert "support-strength calibration" in text, f"support calibration missing in {path}"
        assert "confidence mismatch" in text, f"confidence mismatch missing in {path}"
        assert "Do not read" in text and "claim_drafts.json" in text, (
            f"claim_drafts read prohibition missing in {path}"
        )
        assert "Do not create, edit, rewrite, or repair" in text, (
            f"ledger mutation prohibition missing in {path}"
        )


def test_semantic_support_auditor_is_proposal_only():
    prompt_paths = [
        "configs/agent_roles.yaml",
        ".agents/skills/auditor/SKILL.md",
        ".claude/agents/auditor.md",
        ".codex/agents/auditor.toml",
        ".opencode/agents/brief-auditor.md",
    ]
    for path in prompt_paths:
        text = _read(path)
        lowered = text.lower()
        assert "semantic support auditor" in lowered, f"semantic support auditor missing in {path}"
        assert "semantic_assessment_report.json" in text, (
            f"semantic proposal artifact missing in {path}"
        )
        assert "proposal" in lowered, f"proposal-only framing missing in {path}"
        # Proposals must never be described as gating finalize/delivery/release.
        assert "do not gate finalize" in lowered, f"gate boundary missing in {path}"
        # The semantic auditor must not be told to write authoritative control files.
        assert "must not" in lowered and "audit_report.json" in text, (
            f"authoritative-write prohibition missing in {path}"
        )
        assert "records adjudication only" in lowered, (
            f"human adjudication boundary missing in {path}"
        )
        assert "does not create support truth" in lowered, (
            f"support-truth boundary missing in {path}"
        )
        forbidden = [
            "until a human accepts",
            "unless a human accepts",
            "becomes authoritative",
            "become authoritative",
            "human accept makes",
            "human acceptance makes",
        ]
        for phrase in forbidden:
            assert phrase not in lowered, f"semantic support authority leak in {path}: {phrase}"


def test_claude_generate_brief_requires_source_discovery_transaction_for_all_profiles():
    text = _read(".claude/commands/generate-brief.md")
    source_section = text.split("**Source discovery transaction (all source profiles):**", 1)[1].split(
        "**Input governance gate", 1
    )[0]
    assert "Source discovery is a workflow stage for every run, not only for `llm_decide`." in source_section
    assert "Complete the `source-discovery` transaction before invoking Scout." in source_section
    assert "configured non-`llm_decide` source profile" in source_section
    assert (
        "briefloop state stage-complete --workspace $ARGUMENTS --stage source-discovery"
        in source_section
    )
    assert "after the `source-discovery` transaction succeeds" in source_section
    assert "source plan only, not source evidence" in source_section


def test_orchestrator_agent_treats_stage_completion_as_transaction_defined():
    text = _read(".claude/agents/orchestrator.md")
    assert "Stage completion is transaction-defined, not artifact-defined." in text
    assert "not allowed to call the next specialist agent or tool" in text
    assert "If `state stage-complete` fails, stop" in text
    assert "output artifacts are frozen for downstream stages" in text
    assert "route repair back to the owner stage" in text


def test_claude_generate_brief_does_not_weaken_config_freshness():
    text = _read(".claude/commands/generate-brief.md")
    assert "Configuration is authoritative." in text
    assert "Do not weaken or override `config.yaml` constraints" in text
    assert "Do not tell Screener that older sources may be retained" in text
    assert "stop and report the mismatch instead of relaxing the rule" in text


def test_claude_generate_brief_blocks_zero_runtime_websearch():
    text = _read(".claude/commands/generate-brief.md")
    assert "Did 0 searches" in text
    assert "every query returns an empty result set" in text
    assert "stop and request human review" in text
    assert "Do not switch to source-planner" in text


def test_orchestrator_agent_does_not_turn_config_into_guidance():
    text = _read(".claude/agents/orchestrator.md")
    assert "Configuration is authoritative." in text
    assert "must not weaken it through specialist prompts" in text
    assert "Do not convert hard config settings into soft guidance" in text
    assert "max_source_age_days" in text
    assert "fail_on_stale_source" in text


def test_orchestrator_agent_blocks_zero_runtime_websearch():
    text = _read(".claude/agents/orchestrator.md")
    assert "Did 0 searches" in text
    assert "every query returns an empty result set" in text
    assert "request human review" in text
    assert "Do not switch to source-planner" in text
    assert "source plan, not source evidence" in text


def test_codex_orchestrator_has_writer_flow_protocol():
    text = _read(".codex/agents/orchestrator.toml")
    assert "Codex writer flow protocol" in text
    assert "Workspace Card" in text
    assert "Trust status is one Workspace Card line, not the main answer" in text
    assert "Do not launch the interactive terminal onboarding wizard inside Codex chat" in text
    assert "show the values to be written" in text
    assert "Before initializing into an existing directory" in text
    assert "output/intermediate/runtime_manifest.json" in text
    assert "Source Mode Card" in text
    assert "runtime WebSearch enabled/disabled" in text
    assert "input/sources/" in text
    assert "URL, source title/name" in text
    assert "raw excerpt/snippet" in text
    assert "Do not call sources decide --search unless web_search.mode is external_api" in text
    assert "Do not call sources decide --merge on source_plan_only artifacts" in text
    assert "source_candidates.yaml is planning/review only, not evidence" in text
    assert "report progress after every successful stage-complete transaction" in text
    assert "[stage] produced <artifact> -> stage-complete passed -> next <stage>" in text


def test_screener_role_treats_freshness_config_as_authoritative():
    text = _read(".agents/skills/screener/SKILL.md")
    assert "Treat workspace config freshness settings as authoritative" in text
    assert "Do not silently relax the threshold" in text


def test_auditor_prompt_does_not_require_audit_binding_metadata():
    skill_text = _read(".agents/skills/auditor/SKILL.md")
    role_text = _read("configs/agent_roles.yaml")
    for text in (skill_text, role_text):
        assert "metadata.audit_binding" not in text
        assert "claim_ledger_mtime" not in text
        assert "Do not write audit binding metadata" in text
        assert "Python control-plane" in text
        assert "state stage-complete --stage auditor" in text


def test_auditor_prompts_require_current_audit_report_contract_fields():
    prompt_paths = [
        "configs/agent_roles.yaml",
        ".agents/skills/auditor/SKILL.md",
        ".claude/agents/auditor.md",
        ".codex/agents/auditor.toml",
        ".opencode/agents/brief-auditor.md",
    ]
    for path in prompt_paths:
        text = _read(path)
        assert "audit_status" in text, f"audit_status missing in {path}"
        assert "audit_score" in text, f"audit_score missing in {path}"
        assert "findings" in text, f"findings missing in {path}"
        assert "metadata" in text, f"metadata missing in {path}"
        assert "never replace" in text, f"compatibility warning missing in {path}"


def test_runtime_prompts_do_not_use_literal_claim_id_placeholder():
    prompt_paths = [
        "configs/agent_roles.yaml",
        ".agents/skills/analyst/SKILL.md",
        ".agents/skills/editor/SKILL.md",
        ".agents/skills/formatter/SKILL.md",
        ".agents/hermes-skills/multi-agent-brief-hermes/references/delegate-task-sequence.md",
        ".claude/commands/generate-brief.md",
        ".claude/agents/analyst.md",
        ".claude/agents/auditor.md",
        ".claude/agents/editor.md",
        ".claude/agents/formatter.md",
        ".codex/agents/analyst.toml",
        ".codex/agents/auditor.toml",
        ".codex/agents/editor.toml",
        ".codex/agents/formatter.toml",
        ".opencode/commands/generate-brief.md",
        ".opencode/agents/brief-analyst.md",
        ".opencode/agents/brief-auditor.md",
        ".opencode/agents/brief-editor.md",
        ".opencode/agents/brief-formatter.md",
        "docs/agents/claude-code.md",
        "docs/agents/codex.md",
        "docs/agents/opencode.md",
    ]
    for path in prompt_paths:
        text = _read(path)
        assert "[src:CLAIM_ID]" not in text, f"literal placeholder leaked in {path}"


def test_analyst_editor_contracts_use_atomic_graph_as_optional_no_new_atom_aid():
    prompt_paths = [
        "configs/agent_roles.yaml",
        ".agents/skills/analyst/SKILL.md",
        ".agents/skills/editor/SKILL.md",
        ".agents/hermes-skills/multi-agent-brief-hermes/references/delegate-task-sequence.md",
        ".claude/commands/generate-brief.md",
        ".claude/agents/analyst.md",
        ".claude/agents/editor.md",
        ".codex/agents/analyst.toml",
        ".codex/agents/editor.toml",
        ".opencode/commands/generate-brief.md",
        ".opencode/agents/brief-analyst.md",
        ".opencode/agents/brief-editor.md",
    ]
    for path in prompt_paths:
        text = _read(path)
        normalized = " ".join(text.replace("`", "").split())
        assert "atomic_claim_graph.json" in normalized, f"atomic graph boundary missing in {path}"
        assert "optional experimental structural decomposition aid" in normalized, (
            f"optional aid wording missing in {path}"
        )
        assert "not source evidence" in normalized, f"source-evidence boundary missing in {path}"
        assert "proof of support" in normalized, f"support-proof boundary missing in {path}"
        assert "create, edit, rewrite, repair, or extend" in normalized, (
            f"graph mutation ban missing in {path}"
        )
        assert "absent or invalid" in normalized, f"absent/invalid graph boundary missing in {path}"
        assert "Do not cite atom IDs in reader-facing prose" in normalized, (
            f"atom citation ban missing in {path}"
        )
        assert "material atoms absent" in normalized, f"no-new-atom boundary missing in {path}"


def test_stage_protocol_does_not_require_atomic_claim_graph():
    text = _read(".claude/commands/generate-brief.md")
    assert "MUST produce: atomic_claim_graph" not in text
    assert "required input artifact: atomic_claim_graph" not in text.lower()


def test_formatter_contract_forbids_patching_frozen_audited_brief():
    prompt_paths = [
        "configs/agent_roles.yaml",
        ".agents/skills/formatter/SKILL.md",
        ".agents/hermes-skills/multi-agent-brief-hermes/SKILL.md",
        ".agents/hermes-skills/multi-agent-brief-hermes/references/delegate-task-sequence.md",
        ".claude/commands/mabw.md",
        ".claude/agents/formatter.md",
        ".codex/agents/formatter.toml",
        ".opencode/agents/brief-formatter.md",
    ]
    for path in prompt_paths:
        text = _read(path)
        assert "audited_brief.md" in text, f"audited brief boundary missing in {path}"
        assert "frozen input" in text, f"frozen input boundary missing in {path}"
        assert "repair" in text, f"repair route guidance missing in {path}"
        assert "workflow state" in text or "workflow_state" in text, f"workflow-state edit ban missing in {path}"


def test_claude_mabw_command_is_five_verb_writer_surface():
    text = _read(".claude/commands/mabw.md")
    assert "BriefLoop writer command" in text
    assert "The command name `/mabw` is retained for compatibility" in text
    assert "`/briefloop` is also supported as a writer command alias" in text
    assert "Claude Code is the first-class writer / five-verb path." in text
    assert "Hermes remains a supported delegated/scheduled runtime path." in text
    assert "Do not mirror this five-verb command into Hermes, OpenCode, Codex" in text

    first_screen = text.split("## First-Screen Writer Help", 1)[1].split(
        "Do not put", 1
    )[0]
    expected = [
        "/mabw new",
        "/mabw run <workspace>",
        "/mabw status <workspace>",
        "/mabw feedback <workspace> [text-or-file]",
        "/mabw deliver <workspace>",
    ]
    for verb in expected:
        assert verb in first_screen
    assert "/mabw doctor" not in first_screen
    assert "eval-cases" not in first_screen
    assert "runtime install" not in first_screen


def test_claude_mabw_command_resolves_cli_once_for_compatibility():
    text = _read(".claude/commands/mabw.md")
    routing_section = text.split("## Routing", 1)[1].split("## `new`", 1)[0]
    new_section = text.split("## `new`", 1)[1].split("## `run <workspace>`", 1)[0]

    assert "BRIEFLOOP_CLI=briefloop" in routing_section
    assert "BRIEFLOOP_CLI=multi-agent-brief" in routing_section
    assert "Use `$BRIEFLOOP_CLI` for every command below" in routing_section
    assert "Do not check one CLI binary" in routing_section
    assert "- check whether `multi-agent-brief` is available;" not in text
    assert "briefloop init <workspace>" not in new_section
    assert "$BRIEFLOOP_CLI init <workspace> --from-onboarding <onboarding.json>" in new_section
    assert "$BRIEFLOOP_CLI run --workspace <workspace> --runtime claude --skip-doctor" in new_section


def test_claude_briefloop_command_is_five_verb_writer_surface():
    text = _read(".claude/commands/briefloop.md")
    assert "BriefLoop writer command" in text
    assert "`/briefloop` is the public five-verb writer surface" in text
    assert "self-contained after `briefloop claude" in text
    assert "Do not mirror this five-verb command into Hermes, OpenCode, Codex" in text
    assert ".claude/commands/mabw.md" not in text
    assert "/mabw" not in text
    assert "multi-agent-brief" not in text
    assert "MABW" not in text

    first_screen = text.split("## First-Screen Writer Help", 1)[1].split(
        "Do not put", 1
    )[0]
    expected = [
        "/briefloop new",
        "/briefloop run <workspace>",
        "/briefloop status <workspace>",
        "/briefloop feedback <workspace> [text-or-file]",
        "/briefloop deliver <workspace>",
    ]
    for verb in expected:
        assert verb in first_screen
    assert "/briefloop doctor" not in first_screen
    assert "eval-cases" not in first_screen
    assert "runtime install" not in first_screen
    assert "## `new`" in text
    assert "## `run <workspace>`" in text
    assert "## `status <workspace>`" in text
    assert "## `feedback <workspace> [text-or-file]`" in text
    assert "## `deliver <workspace>`" in text
    assert "status is strictly read-only" in text
    assert "briefloop deliver --workspace <workspace> --target local" in text
    assert "do not send audit/control records" in text


def test_briefloop_skill_is_not_slash_command_implementation():
    assert Path(".claude/commands/briefloop.md").exists()
    naming = _read("docs/briefloop-naming.md")
    assert "`/briefloop` Claude writer command" in naming
    assert "BriefLoop skill is an agent operator protocol surface" in naming
    assert "not the slash-command" in naming


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


def test_claude_mabw_new_forbids_private_company_inference():
    text = _read(".claude/commands/mabw.md")
    new_section = text.split("## `new`", 1)[1].split("## `run <workspace>`", 1)[0]
    assert "must come only from the user's explicit answer" in new_section
    assert "Do not infer company" in new_section
    assert "previous workspaces" in new_section
    assert "chat memory" in new_section
    assert "Never silently fill a real company name" in new_section
    assert "Before writing onboarding.json" in new_section


def test_claude_mabw_new_points_to_status_after_handoff_setup():
    text = _read(".claude/commands/mabw.md")
    new_section = text.split("## `new`", 1)[1].split("## `run <workspace>`", 1)[0]
    normalized = " ".join(new_section.split())
    assert "workspace handoff has already been created" in normalized
    assert "The next writer command is:" in normalized
    assert "/briefloop status <workspace>" in normalized
    assert "/generate-brief <workspace>" not in normalized
    assert "`/mabw status <workspace>` first" in normalized
    assert "`/mabw run <workspace>` refreshes handoff" in normalized
    assert "does not execute specialists" in normalized
    assert "`/mabw run <workspace>` is the normal next writer command" not in normalized


def test_claude_mabw_status_is_read_only_and_feedback_is_bounded():
    text = _read(".claude/commands/mabw.md")
    status_section = text.split("## `status <workspace>`", 1)[1].split(
        "## `feedback <workspace> [text-or-file]`", 1
    )[0]
    assert "status is strictly read-only" in status_section
    assert "$BRIEFLOOP_CLI status --workspace <workspace> --json" in status_section
    assert "do not run `$BRIEFLOOP_CLI state check`" in status_section
    assert "do not initialize runtime state" in status_section
    assert "do not refresh artifact registry" in status_section
    assert "do not append event log entries" in status_section

    feedback_section = text.split("## `feedback <workspace> [text-or-file]`", 1)[1].split(
        "## `deliver <workspace>`", 1
    )[0]
    assert "$BRIEFLOOP_CLI feedback ingest" in feedback_section
    assert "Downstream actions require explicit user confirmation" in feedback_section
    assert "do not execute repair" in feedback_section
    assert "do not auto-resolve" in feedback_section
    assert "do not automatically create Improvement Ledger entries" in feedback_section
    assert "do not approve, reject, or revert improvement entries" in feedback_section


def test_claude_mabw_deliver_uses_completion_transactions():
    text = _read(".claude/commands/mabw.md")
    deliver_section = text.split("## `deliver <workspace>`", 1)[1].split(
        "## Diagnostic And Maintainer Commands", 1
    )[0]
    assert "$BRIEFLOOP_CLI gates check --workspace <workspace> --stage auditor" in deliver_section
    assert "$BRIEFLOOP_CLI gates check --workspace <workspace> --stage finalize" in deliver_section
    assert "$BRIEFLOOP_CLI state check --workspace <workspace> --strict" in deliver_section
    assert "$BRIEFLOOP_CLI state stage-complete --workspace <workspace> --stage auditor" in deliver_section
    assert "$BRIEFLOOP_CLI finalize --config <workspace>/config.yaml" in deliver_section
    assert "$BRIEFLOOP_CLI state finalize-complete --workspace <workspace>" in deliver_section
    assert "finalize` as a quality-gate executor" in deliver_section
    assert "state decide --decision finalize" in deliver_section
    assert "state decide --decision continue" not in deliver_section


def test_opencode_generate_brief_command_uses_audience_snapshot_context():
    text = _read(".opencode/commands/generate-brief.md")
    assert "Orchestrator main agent" in text
    assert "briefloop run --workspace $ARGUMENTS --runtime opencode --skip-doctor" in text
    assert "output/intermediate/audience_profile_snapshot.md" in text
    assert "output/intermediate/orchestrator_control_switchboard.json" in text
    assert "briefloop controls select" in text
    assert "selection is not execution" in text
    assert "Do not treat `audience_profile.md` as source evidence" in text
    assert "Check the expected artifact" in text
