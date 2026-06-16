"""Tests for agent config generation from agent_roles.yaml manifest."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "configs" / "agent_roles.yaml"
GENERATOR = ROOT / "scripts" / "generate_agent_configs.py"

# Import generator functions directly
sys.path.insert(0, str(ROOT / "scripts"))
from generate_agent_configs import (
    load_manifest,
    validate_manifest,
    render_codex_config,
    render_codex_agent,
    render_claude_agent,
    render_docs,
    render_opencode_agent,
    render_opencode_agents,
    render_opencode_command_generate_brief,
    render_opencode_jsonc,
    write_or_check,
    _sensitive_check,
    PIPELINE_TEXT,
    HARNESS_TEXT,
)

PIPELINE_ROLES = ["scout", "screener", "claim-ledger", "analyst", "editor", "auditor", "formatter"]
HARNESS_ROLES = ["draft-audit-harness", "final-quality-harness", "rendered-output-harness"]
ALL_ROLES = PIPELINE_ROLES + HARNESS_ROLES + ["orchestrator"]
REQUIRED_ROLE_FIELDS = ["stage", "tool_profile", "description", "trigger", "responsibilities", "hard_rules"]


@pytest.fixture
def manifest():
    return load_manifest(MANIFEST_PATH)


# --- Manifest validation ---

def test_manifest_loads():
    manifest = load_manifest(MANIFEST_PATH)
    assert "schema_version" in manifest
    assert "roles" in manifest


def test_manifest_has_complete_pipeline(manifest):
    pipeline = manifest["project"]["pipeline"]
    assert pipeline == PIPELINE_ROLES


def test_manifest_has_harness_roles(manifest):
    roles = manifest["roles"]
    for name in HARNESS_ROLES:
        assert name in roles, f"Missing harness role: {name}"


def test_manifest_roles_have_required_fields(manifest):
    for name, role in manifest["roles"].items():
        for field in REQUIRED_ROLE_FIELDS:
            assert field in role, f"Role '{name}' missing field: {field}"


def test_manifest_roles_use_valid_tool_profiles(manifest):
    profiles = manifest["tool_profiles"]
    for name, role in manifest["roles"].items():
        assert role["tool_profile"] in profiles, f"Role '{name}' uses unknown profile: {role['tool_profile']}"


def test_manifest_validate_passes(manifest):
    validate_manifest(manifest)  # should not raise


# --- Read-only agents must not have edit tools ---

def test_read_only_agents_no_edit_tools(manifest):
    profiles = manifest["tool_profiles"]
    for name, role in manifest["roles"].items():
        tp = profiles[role["tool_profile"]]
        if not tp["may_edit"]:
            tools = tp["claude_tools"]
            assert "Edit" not in tools, f"Read-only role '{name}' has Edit tool"
            assert "MultiEdit" not in tools, f"Read-only role '{name}' has MultiEdit tool"
            assert "Write" not in tools, f"Read-only role '{name}' has Write tool"


# --- Generated content checks ---

def test_codex_config_valid_toml(manifest):
    content = render_codex_config(manifest)
    parsed = tomllib.loads(content)
    assert "max_threads" in content
    assert "max_depth" in content
    assert parsed["agents"]["max_depth"] == 1


def test_codex_agents_have_required_fields(manifest):
    for name, role in manifest["roles"].items():
        content = render_codex_agent(name, role, manifest)
        parsed = tomllib.loads(content)
        assert f'name = "{name}"' in content
        assert "description =" in content
        assert "developer_instructions" in content
        assert parsed["name"] == name
        for key in ("name", "description", "developer_instructions"):
            assert key in parsed, (name, key)
        assert parsed["developer_instructions"].strip()


def test_checked_in_codex_agents_are_valid_toml():
    for path in sorted((ROOT / ".codex" / "agents").glob("*.toml")):
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        for key in ("name", "description", "developer_instructions"):
            assert key in parsed, (path, key)
        assert parsed["developer_instructions"].strip()


def test_claude_agents_have_frontmatter(manifest):
    for name, role in manifest["roles"].items():
        content = render_claude_agent(name, role, manifest)
        assert content.startswith("---")
        assert f"name: {name}" in content
        assert "description:" in content
        assert "tools:" in content
        assert "model:" in content


def test_generated_orchestrator_is_main_agent(manifest):
    role = manifest["roles"]["orchestrator"]
    codex = render_codex_agent("orchestrator", role, manifest)
    claude = render_claude_agent("orchestrator", role, manifest)
    opencode = render_opencode_agent("orchestrator", role, manifest)

    for content in (codex, claude, opencode):
        assert "Orchestrator main agent" in content
        assert "Orchestrator control loop" in content
        assert "contract references" in content.lower() or "configs/orchestrator_contract.yaml" in content
        assert "retry_stage" in content
        assert "request_human_review" in content
        assert "block_run" in content

    assert "You are the Orchestrator subagent" not in claude


def test_generated_source_planner_stays_lightweight_plan_only(manifest):
    role = manifest["roles"]["source-planner"]
    rendered = "\n".join([
        render_codex_agent("source-planner", role, manifest),
        render_claude_agent("source-planner", role, manifest),
        render_opencode_agent("source-planner", role, manifest),
    ])

    assert "lightweight" in rendered
    assert "source plan, not evidence" in rendered
    assert "Do not read full input/sources/* files" in rendered
    assert "Do not decide whether source-discovery is complete" in rendered
    assert "Do not judge claim support" in rendered
    assert "Orchestrator/source-provider" in rendered
    assert "Ensures all sources are public, citable, and timestamped" not in rendered


def test_generated_scout_and_screener_are_topology_aware(manifest):
    scout_role = manifest["roles"]["scout"]
    screener_role = manifest["roles"]["screener"]
    scout_rendered = "\n".join([
        render_codex_agent("scout", scout_role, manifest),
        render_claude_agent("scout", scout_role, manifest),
        render_opencode_agent("scout", scout_role, manifest),
    ])
    screener_rendered = "\n".join([
        render_codex_agent("screener", screener_role, manifest),
        render_claude_agent("screener", screener_role, manifest),
        render_opencode_agent("screener", screener_role, manifest),
    ])

    assert "In default topology, produce both candidate_claims.json and screened_candidates.json" in scout_rendered
    assert "also output screened_candidates.json before Scout stage completion" in scout_rendered
    assert "Strict: Scout -> Screener" in scout_rendered
    assert "Use only when role_topology is strict" in screener_rendered
    assert "Default topology uses Scout to perform screening" in screener_rendered
    assert "Do not rediscover source material" in screener_rendered


def test_generated_editor_uses_delivery_editor_alias(manifest):
    role = manifest["roles"]["editor"]
    rendered = "\n".join([
        render_codex_agent("editor", role, manifest),
        render_claude_agent("editor", role, manifest),
        render_opencode_agent("editor", role, manifest),
    ])

    assert "Delivery Editor alias" in rendered
    assert "stage id remains editor" in rendered
    assert "Own the final auditable output/intermediate/audited_brief.md" in rendered
    assert "output/intermediate/analyst_draft_snapshot.md as the factual boundary" in rendered
    assert "Do not add new facts, numbers, named entities, dates, causal claims, or citations" in rendered


def test_generated_analyst_and_editor_explain_audited_brief_ownership(manifest):
    analyst = manifest["roles"]["analyst"]
    editor = manifest["roles"]["editor"]
    rendered = "\n".join([
        render_codex_agent("analyst", analyst, manifest),
        render_claude_agent("analyst", analyst, manifest),
        render_opencode_agent("analyst", analyst, manifest),
        render_codex_agent("editor", editor, manifest),
        render_claude_agent("editor", editor, manifest),
        render_opencode_agent("editor", editor, manifest),
    ])

    assert "Analyst working draft" in rendered
    assert "Python freezes it into output/intermediate/analyst_draft_snapshot.md" in rendered
    assert "Do not edit output/intermediate/analyst_draft_snapshot.md" in rendered
    assert "Own the final auditable output/intermediate/audited_brief.md" in rendered


def test_generated_assets_scope_claim_freeze_to_claim_ledger_runtime_protocol(manifest):
    rendered_parts: list[str] = [render_opencode_command_generate_brief(manifest)]
    for name, role in manifest["roles"].items():
        rendered_parts.extend([
            render_codex_agent(name, role, manifest),
            render_claude_agent(name, role, manifest),
            render_opencode_agent(name, role, manifest),
        ])
    rendered_parts.extend(render_docs(manifest).values())
    rendered = "\n".join(rendered_parts)

    assert "Claim Freeze is implemented" not in rendered
    assert "claim_drafts.json" in rendered
    assert "freeze-claim-ledger" in rendered

    analyst_auditor = "\n".join([
        render_codex_agent("analyst", manifest["roles"]["analyst"], manifest),
        render_claude_agent("analyst", manifest["roles"]["analyst"], manifest),
        render_opencode_agent("analyst", manifest["roles"]["analyst"], manifest),
        render_codex_agent("auditor", manifest["roles"]["auditor"], manifest),
        render_claude_agent("auditor", manifest["roles"]["auditor"], manifest),
        render_opencode_agent("auditor", manifest["roles"]["auditor"], manifest),
    ])
    assert "claim_drafts.json" not in analyst_auditor
    assert "freeze-claim-ledger" not in analyst_auditor


def test_generated_orchestrator_separates_runtime_and_repo_development_modes(manifest):
    role = manifest["roles"]["orchestrator"]
    rendered = "\n".join([
        render_codex_agent("orchestrator", role, manifest),
        render_claude_agent("orchestrator", role, manifest),
        render_opencode_agent("orchestrator", role, manifest),
    ])

    assert "Determine the active mode before acting" in rendered
    assert "Brief-runtime mode coordinates one workspace run" in rendered
    assert "repo-development mode changes contracts" in rendered
    assert "In brief-runtime mode, do not edit repository files" in rendered
    assert "run repo validation commands unless the user explicitly switches" in rendered
    assert "only in repo-development mode" in rendered


def test_generated_auditor_does_not_treat_audit_report_as_input_or_coordinator(manifest):
    role = manifest["roles"]["auditor"]
    rendered = "\n".join([
        render_codex_agent("auditor", role, manifest),
        render_claude_agent("auditor", role, manifest),
        render_opencode_agent("auditor", role, manifest),
    ])

    assert "Review output/intermediate/audited_brief.md against claim_ledger.json" in rendered
    assert "current audit_report.json is this stage's output, not input" in rendered
    assert "Do not coordinate other agents" in rendered
    assert "Report audit readiness only" in rendered
    assert "Review final brief against claim_ledger.json and audit_report.json" not in rendered
    assert "Coordinate draft and final harness agents" not in rendered
    assert "Mark reports distribution-ready" not in rendered


def test_claude_read_only_agents_no_edit_tools(manifest):
    profiles = manifest["tool_profiles"]
    for name, role in manifest["roles"].items():
        tp = profiles[role["tool_profile"]]
        if not tp["may_edit"]:
            content = render_claude_agent(name, role, manifest)
            # Extract tools line from frontmatter
            for line in content.splitlines():
                if line.startswith("tools:"):
                    tools_str = line.split(":", 1)[1].strip()
                    tools = [t.strip() for t in tools_str.split(",")]
                    assert "Edit" not in tools, f"Read-only Claude agent '{name}' has Edit"
                    assert "MultiEdit" not in tools, f"Read-only Claude agent '{name}' has MultiEdit"
                    assert "Write" not in tools, f"Read-only Claude agent '{name}' has Write"
                    break


# --- Harness docs ---

def test_harness_docs_contain_delivery_contract(manifest):
    docs = render_docs(manifest)
    harness_content = docs["docs/agents/harness-subagents.md"]
    assert HARNESS_TEXT in harness_content
    assert "draft-audit-harness" in harness_content
    assert "final-quality-harness" in harness_content
    assert "rendered-output-harness" in harness_content


def test_docs_contain_pipeline(manifest):
    docs = render_docs(manifest)
    for key in ["docs/agents/README.md", "docs/agents/manifest.md"]:
        assert PIPELINE_TEXT in docs[key], f"Doc {key} missing pipeline text"


# --- Sensitivity checks ---

def _check_no_sensitive(text: str, context: str):
    hits = _sensitive_check(text, context)
    assert not hits, f"Sensitive content found: {hits}"


def test_no_sensitive_content_in_generated_files(manifest):
    _check_no_sensitive(render_codex_config(manifest), "codex config")
    for name, role in manifest["roles"].items():
        _check_no_sensitive(render_codex_agent(name, role, manifest), f"codex/{name}.toml")
        _check_no_sensitive(render_claude_agent(name, role, manifest), f"claude/{name}.md")
        _check_no_sensitive(render_opencode_agent(name, role, manifest), f"opencode/{name}.md")
    _check_no_sensitive(render_opencode_command_generate_brief(manifest), "opencode/generate-brief.md")
    _check_no_sensitive(render_opencode_jsonc(), "opencode.jsonc")
    for key, content in render_docs(manifest).items():
        _check_no_sensitive(content, key)


# --- write_or_check ---

def test_write_or_check_write_mode(tmp_path):
    path = tmp_path / "test.txt"
    assert write_or_check(path, "hello", check=False)
    assert path.read_text() == "hello"


def test_write_or_check_check_mode_passes(tmp_path):
    path = tmp_path / "test.txt"
    path.write_text("hello")
    assert write_or_check(path, "hello", check=True)


def test_write_or_check_check_mode_fails_on_missing(tmp_path):
    path = tmp_path / "missing.txt"
    assert not write_or_check(path, "hello", check=True)


def test_write_or_check_check_mode_fails_on_stale(tmp_path):
    path = tmp_path / "test.txt"
    path.write_text("old")
    assert not write_or_check(path, "new", check=True)


# --- CLI ---

def test_generate_check_passes_after_write():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0, f"--check failed: {result.stdout}\n{result.stderr}"


def test_generate_write_then_check_roundtrip():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--write"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0


def test_generate_target_codex_only():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--write", "--target", "codex"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0
    assert "Generated" in result.stdout


# --- OpenCode ---

def test_opencode_agent_has_frontmatter(manifest):
    for name, role in manifest["roles"].items():
        content = render_opencode_agent(name, role, manifest)
        assert content.startswith("---")
        assert "description:" in content
        assert "mode:" in content
        assert "permission:" in content


def test_opencode_orchestrator_is_primary(manifest):
    role = manifest["roles"]["orchestrator"]
    content = render_opencode_agent("orchestrator", role, manifest)
    assert "mode: primary" in content


def test_opencode_agents_all_have_brief_prefix(manifest):
    agents = render_opencode_agents(manifest)
    for rel_path in agents:
        assert "brief-" in rel_path, f"Missing brief- prefix: {rel_path}"


def test_opencode_command_has_correct_agent(manifest):
    content = render_opencode_command_generate_brief(manifest)
    assert "agent: brief-orchestrator" in content
    assert "subtask: false" in content
    assert "$ARGUMENTS" in content
    assert "Orchestrator main agent" in content
    assert "configs/orchestrator_contract.yaml" in content
    assert "Check the expected artifact before continuing" in content
    assert "multi-agent-brief run --workspace $ARGUMENTS --runtime opencode --skip-doctor" in content
    assert "output/intermediate/audience_profile_snapshot.md" in content
    assert "output/intermediate/orchestrator_control_switchboard.json" in content
    assert "multi-agent-brief controls select" in content
    assert "selection is not execution" in content
    assert "Do not treat `audience_profile.md` as source evidence" in content
    assert "retry_stage" in content
    assert "Analyst working draft" in content
    assert "analyst_draft_snapshot.md" in content
    assert "Editor-owned final auditable brief" in content


def test_opencode_jsonc_is_valid(manifest):
    content = render_opencode_jsonc()
    assert "$schema" in content
    assert "opencode.ai/config.json" in content


def test_generate_target_opencode_only():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--write", "--target", "opencode"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0
    assert "Generated" in result.stdout


def test_opencode_agents_have_permission_block(manifest):
    for name, role in manifest["roles"].items():
        content = render_opencode_agent(name, role, manifest)
        assert "permission:" in content, f"OpenCode agent '{name}' missing permission block"
        if name == "orchestrator":
            assert "brief-*" in content, "Orchestrator should allow brief-* tasks"
