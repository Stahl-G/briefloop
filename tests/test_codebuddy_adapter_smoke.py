from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_codebuddy_adapter_smoke.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location(
        "check_codebuddy_adapter_smoke", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codebuddy_adapter_smoke_json_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["runtime_effect"] == "readiness_check_only"
    assert "delegated_runtime_proof" in payload["non_goals"]
    check_ids = {item["id"] for item in payload["checks"]}
    assert {
        "codebuddy.skill.contract",
        "codebuddy.role_agents.contract",
        "codebuddy.runtime.fail_closed",
    } <= check_ids


def test_codebuddy_adapter_smoke_human_output_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CodeBuddy Adapter Smoke" in result.stdout
    assert "ALL CHECKS PASSED" in result.stdout


def test_codebuddy_adapter_frontmatter_parser_anchors_tool_and_context_keys() -> None:
    smoke = _load_smoke_module()
    text = """---
name: briefloop-test
description: Mentions context and says never use Bash in prose.
tools: Read, Write, Grep, Glob
---

Never use Bash to run CLI commands.
"""
    frontmatter = smoke._frontmatter(text)
    assert smoke._frontmatter_tools(frontmatter) == ["Read", "Write", "Grep", "Glob"]
    assert not smoke._frontmatter_has_key(frontmatter, "context")
    assert "Bash" not in smoke._frontmatter_tools(frontmatter)

    unsafe = """---
name: briefloop-test
tools: Read, Bash
context: fork
---
"""
    unsafe_frontmatter = smoke._frontmatter(unsafe)
    assert "Bash" in smoke._frontmatter_tools(unsafe_frontmatter)
    assert smoke._frontmatter_has_key(unsafe_frontmatter, "context")


def test_codebuddy_adapter_smoke_rejects_formatter_write_tool() -> None:
    smoke = _load_smoke_module()
    formatter_with_write = """---
name: briefloop-formatter
description: Formatter readiness review.
tools: Read, Write, Grep, Glob
model: inherit
permissionMode: default
---

Do not run `briefloop` or `multi-agent-brief` CLI commands.
Never edit workflow_state.json or event_log.jsonl.
"""
    errors = smoke._role_agent_contract_errors(
        "briefloop-formatter", formatter_with_write
    )
    assert "briefloop-formatter: tools must be Read, Grep, Glob" in errors


def test_codebuddy_adapter_smoke_rejects_role_agent_name_mismatch() -> None:
    smoke = _load_smoke_module()
    scout_with_wrong_name = """---
name: briefloop-scout-typo
description: Scout role.
tools: Read, Write, Grep, Glob
model: inherit
permissionMode: default
---

Do not run `briefloop` or `multi-agent-brief` CLI commands.
Never edit workflow_state.json or event_log.jsonl.
"""
    errors = smoke._role_agent_contract_errors("briefloop-scout", scout_with_wrong_name)
    assert "briefloop-scout: frontmatter name must be briefloop-scout" in errors
