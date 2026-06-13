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
    assert "multi-agent-brief run --workspace $ARGUMENTS --runtime claude --skip-doctor" in text
    assert "output/intermediate/audience_profile_snapshot.md" in text
    assert "output/intermediate/orchestrator_control_switchboard.json" in text
    assert "multi-agent-brief controls select" in text
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


def test_claude_generate_brief_requires_source_discovery_transaction_for_all_profiles():
    text = _read(".claude/commands/generate-brief.md")
    source_section = text.split("**Source discovery transaction (all source profiles):**", 1)[1].split(
        "**Input governance gate", 1
    )[0]
    assert "Source discovery is a workflow stage for every run, not only for `llm_decide`." in source_section
    assert "Complete the `source-discovery` transaction before invoking Scout." in source_section
    assert "configured non-`llm_decide` source profile" in source_section
    assert (
        "multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage source-discovery"
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


def test_claude_mabw_command_is_five_verb_writer_surface():
    text = _read(".claude/commands/mabw.md")
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


def test_claude_mabw_new_forbids_private_company_inference():
    text = _read(".claude/commands/mabw.md")
    new_section = text.split("## `new`", 1)[1].split("## `run <workspace>`", 1)[0]
    assert "must come only from the user's explicit answer" in new_section
    assert "Do not infer company" in new_section
    assert "previous workspaces" in new_section
    assert "chat memory" in new_section
    assert "Never silently fill a real company name" in new_section
    assert "Before writing onboarding.json" in new_section


def test_claude_mabw_status_is_read_only_and_feedback_is_bounded():
    text = _read(".claude/commands/mabw.md")
    status_section = text.split("## `status <workspace>`", 1)[1].split(
        "## `feedback <workspace> [text-or-file]`", 1
    )[0]
    assert "status is strictly read-only" in status_section
    assert "multi-agent-brief status --workspace <workspace> --json" in status_section
    assert "do not run `multi-agent-brief state check`" in status_section
    assert "do not initialize runtime state" in status_section
    assert "do not refresh artifact registry" in status_section
    assert "do not append event log entries" in status_section

    feedback_section = text.split("## `feedback <workspace> [text-or-file]`", 1)[1].split(
        "## `deliver <workspace>`", 1
    )[0]
    assert "multi-agent-brief feedback ingest" in feedback_section
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
    assert "multi-agent-brief gates check --workspace <workspace>" in deliver_section
    assert "multi-agent-brief state check --workspace <workspace> --strict" in deliver_section
    assert "multi-agent-brief state stage-complete --workspace <workspace> --stage auditor" in deliver_section
    assert "multi-agent-brief finalize --config <workspace>/config.yaml" in deliver_section
    assert "multi-agent-brief state finalize-complete --workspace <workspace>" in deliver_section
    assert "finalize` as a quality-gate executor" in deliver_section
    assert "state decide --decision finalize" in deliver_section
    assert "state decide --decision continue" not in deliver_section


def test_opencode_generate_brief_command_uses_audience_snapshot_context():
    text = _read(".opencode/commands/generate-brief.md")
    assert "Orchestrator main agent" in text
    assert "multi-agent-brief run --workspace $ARGUMENTS --runtime opencode --skip-doctor" in text
    assert "output/intermediate/audience_profile_snapshot.md" in text
    assert "output/intermediate/orchestrator_control_switchboard.json" in text
    assert "multi-agent-brief controls select" in text
    assert "selection is not execution" in text
    assert "Do not treat `audience_profile.md` as source evidence" in text
    assert "Check the expected artifact" in text
