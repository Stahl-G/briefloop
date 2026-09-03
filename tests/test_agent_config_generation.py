"""Tests for agent config generation from agent_roles.yaml manifest."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "configs" / "agent_roles.yaml"
PACKAGED_CODEX_ROOT = ROOT / "src" / "multi_agent_brief" / "runtime_kits" / "codex"

# Import generator functions directly
sys.path.insert(0, str(ROOT / "scripts"))
from generate_agent_configs import (
    load_manifest,
    render_codex_config,
    render_packaged_codex_agent,
    render_docs,
    _sensitive_check,
    PACKAGED_CODEX_ROLE_IDS,
)

PIPELINE_ROLES = ["scout", "screener", "claim-ledger", "analyst", "editor", "auditor", "formatter"]


@pytest.fixture
def manifest():
    return load_manifest(MANIFEST_PATH)


def test_manifest_has_complete_pipeline(manifest):
    pipeline = manifest["project"]["pipeline"]
    assert pipeline == PIPELINE_ROLES


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


# --- Packaged Codex runtime kit content ---

def test_checked_in_packaged_codex_config_matches_generator(manifest):
    generated = render_codex_config(manifest)
    assert (PACKAGED_CODEX_ROOT / "config.toml").read_text(encoding="utf-8") == generated


def test_packaged_kit_has_briefloop_skill():
    assert (PACKAGED_CODEX_ROOT / "skills" / "briefloop" / "SKILL.md").exists()
    assert (
        PACKAGED_CODEX_ROOT / "skills" / "briefloop" / "references" / "controlstore-v2.md"
    ).exists()


# --- Sensitivity checks ---

def _check_no_sensitive(text: str, context: str):
    hits = _sensitive_check(text, context)
    assert not hits, f"Sensitive content found: {hits}"


def test_no_sensitive_content_in_generated_files(manifest):
    _check_no_sensitive(render_codex_config(manifest), "codex config")
    for name in PACKAGED_CODEX_ROLE_IDS:
        role = manifest["roles"][name]
        _check_no_sensitive(
            render_packaged_codex_agent(name, role), f"packaged_codex/{name}.toml"
        )
    for key, content in render_docs(manifest).items():
        _check_no_sensitive(content, key)
