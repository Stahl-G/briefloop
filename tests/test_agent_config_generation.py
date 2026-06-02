"""Tests for agent config generation from agent_roles.yaml manifest."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "configs" / "agent_roles.yaml"
GENERATOR = ROOT / "scripts" / "generate_agent_configs.py"

# Import generator functions directly
sys.path.insert(0, str(ROOT / "scripts"))
from generate_agent_configs import (
    load_manifest,
    validate_manifest,
    render_agents_md,
    render_codex_config,
    render_codex_agent,
    render_skill,
    render_claude_agent,
    render_docs,
    write_or_check,
    _sensitive_check,
    PIPELINE_TEXT,
    HARNESS_TEXT,
)

PIPELINE_ROLES = ["scout", "screener", "claim-ledger", "analyst", "auditor", "editor", "formatter"]
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

def test_agents_md_contains_pipeline(manifest):
    content = render_agents_md(manifest)
    assert PIPELINE_TEXT in content


def test_agents_md_contains_harness_contract(manifest):
    content = render_agents_md(manifest)
    assert HARNESS_TEXT in content


def test_agents_md_lists_all_roles(manifest):
    content = render_agents_md(manifest)
    for name in ALL_ROLES:
        assert f"### {name}" in content


def test_codex_config_valid_toml(manifest):
    content = render_codex_config(manifest)
    assert "max_threads" in content
    assert "max_depth" in content


def test_codex_agents_have_required_fields(manifest):
    for name, role in manifest["roles"].items():
        content = render_codex_agent(name, role, manifest)
        assert f'name = "{name}"' in content
        assert "description =" in content
        assert "developer_instructions" in content


def test_skills_have_frontmatter(manifest):
    for name, role in manifest["roles"].items():
        content = render_skill(name, role, manifest)
        assert content.startswith("---")
        assert f"name: {name}" in content
        assert "description:" in content


def test_claude_agents_have_frontmatter(manifest):
    for name, role in manifest["roles"].items():
        content = render_claude_agent(name, role, manifest)
        assert content.startswith("---")
        assert f"name: {name}" in content
        assert "description:" in content
        assert "tools:" in content
        assert "model:" in content


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
    _check_no_sensitive(render_agents_md(manifest), "AGENTS.md")
    _check_no_sensitive(render_codex_config(manifest), "codex config")
    for name, role in manifest["roles"].items():
        _check_no_sensitive(render_codex_agent(name, role, manifest), f"codex/{name}.toml")
        _check_no_sensitive(render_skill(name, role, manifest), f"skills/{name}/SKILL.md")
        _check_no_sensitive(render_claude_agent(name, role, manifest), f"claude/{name}.md")
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
