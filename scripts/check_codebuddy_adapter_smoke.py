#!/usr/bin/env python3
"""Deterministic smoke check for the source-clone CodeBuddy adapter.

This check inventories retained local assets and proves that the inactive
CodeBuddy runtime fails closed without creating runtime authority.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CODEBUDDY_SKILL = ROOT / ".codebuddy" / "skills" / "briefloop" / "SKILL.md"
CODEBUDDY_AGENT_ROOT = ROOT / ".codebuddy" / "agents"
ROLE_AGENTS = [
    "briefloop-scout",
    "briefloop-screener",
    "briefloop-claim-ledger",
    "briefloop-analyst",
    "briefloop-editor",
    "briefloop-auditor",
    "briefloop-formatter",
]
EXPECTED_ROLE_AGENT_TOOLS = {
    "briefloop-scout": ["Read", "Write", "Grep", "Glob"],
    "briefloop-screener": ["Read", "Write", "Grep", "Glob"],
    "briefloop-claim-ledger": ["Read", "Write", "Grep", "Glob"],
    "briefloop-analyst": ["Read", "Write", "Grep", "Glob"],
    "briefloop-editor": ["Read", "Write", "Grep", "Glob"],
    "briefloop-auditor": ["Read", "Write", "Grep", "Glob"],
    "briefloop-formatter": ["Read", "Grep", "Glob"],
}
FORBIDDEN_HANDOFF_SNIPPETS = [
    "main agent manually writes JSON",
    "main session manually writes JSON",
    "role agents run CLI transactions",
    "role agents may run CLI transactions",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )
    args = parser.parse_args()

    checks: list[dict[str, str]] = []
    _check_source_assets(checks)
    if all(item["status"] == "pass" for item in checks):
        _check_codebuddy_runtime_fail_closed(checks)

    ok = all(item["status"] == "pass" for item in checks)
    payload = {
        "ok": ok,
        "schema_version": "briefloop.codebuddy_adapter_smoke.v1",
        "runtime_effect": "readiness_check_only",
        "non_goals": [
            "codebuddy_process_launch",
            "delegated_runtime_proof",
            "stage_execution",
            "gate_authority",
            "delivery_approval",
            "release_authority",
            "semantic_truth_proof",
        ],
        "checks": checks,
    }
    _print(payload, json_mode=args.json)
    return 0 if ok else 1


def _check_source_assets(checks: list[dict[str, str]]) -> None:
    if not CODEBUDDY_SKILL.exists():
        _append_check(
            checks,
            "codebuddy.skill.exists",
            False,
            "missing .codebuddy/skills/briefloop/SKILL.md",
        )
        return
    skill_text = CODEBUDDY_SKILL.read_text(encoding="utf-8")
    frontmatter = _frontmatter(skill_text)
    required_skill_phrases = [
        "name: briefloop",
        "main CodeBuddy session",
        "Do not add `context: fork`",
        ".agents/skills/briefloop-workbuddy/SKILL.md",
        ".codebuddy/agents/briefloop-scout.md",
        "The main CodeBuddy session owns deterministic CLI transactions",
    ]
    missing_skill = [
        phrase for phrase in required_skill_phrases if phrase not in skill_text
    ]
    if _frontmatter_has_key(frontmatter, "context"):
        missing_skill.append("frontmatter must not contain context")
    for role in ROLE_AGENTS:
        if role not in skill_text:
            missing_skill.append(role)
    _append_check(
        checks,
        "codebuddy.skill.contract",
        not missing_skill,
        f"missing={missing_skill}",
    )

    missing_agents: list[str] = []
    invalid_agents: list[str] = []
    for role in ROLE_AGENTS:
        path = CODEBUDDY_AGENT_ROOT / f"{role}.md"
        if not path.exists():
            missing_agents.append(path.relative_to(ROOT).as_posix())
            continue
        text = path.read_text(encoding="utf-8")
        invalid_agents.extend(_role_agent_contract_errors(role, text))
    _append_check(
        checks,
        "codebuddy.role_agents.contract",
        not missing_agents and not invalid_agents,
        f"missing={missing_agents}; invalid={invalid_agents}",
    )


def _role_agent_contract_errors(role: str, text: str) -> list[str]:
    errors: list[str] = []
    frontmatter = _frontmatter(text)
    name = _frontmatter_value(frontmatter, "name")
    tools = _frontmatter_tools(frontmatter)
    expected_tools = EXPECTED_ROLE_AGENT_TOOLS.get(role)
    if name != role:
        errors.append(f"{role}: frontmatter name must be {role}")
    if expected_tools is None:
        errors.append(f"{role}: unknown CodeBuddy role agent")
    elif tools != expected_tools:
        errors.append(f"{role}: tools must be {', '.join(expected_tools)}")
    if "Bash" in tools:
        errors.append(f"{role}: Bash must not be granted")
    if "MUST BE USED" in text:
        errors.append(f"{role}: description must not force automatic delegation")
    if _frontmatter_has_key(frontmatter, "context"):
        errors.append(f"{role}: role agent must not be a forked skill")
    if "Do not run `briefloop` or `multi-agent-brief` CLI commands" not in text:
        errors.append(f"{role}: missing no-CLI boundary")
    if "workflow_state.json" not in text or "event_log.jsonl" not in text:
        errors.append(f"{role}: missing control-file boundary")
    return errors


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    if len(parts) < 3:
        return ""
    return parts[1]


def _frontmatter_value(frontmatter: str, key: str) -> str | None:
    prefix = f"{key}:"
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return None


def _frontmatter_has_key(frontmatter: str, key: str) -> bool:
    return _frontmatter_value(frontmatter, key) is not None


def _frontmatter_tools(frontmatter: str) -> list[str]:
    value = _frontmatter_value(frontmatter, "tools")
    if not value:
        return []
    return [tool.strip() for tool in value.split(",") if tool.strip()]


def _check_codebuddy_runtime_fail_closed(checks: list[dict[str, str]]) -> None:
    with tempfile.TemporaryDirectory(prefix="briefloop-codebuddy-smoke-") as temp:
        workspace = Path(temp) / "ws"
        new_result = _run_cli(
            [
                "new",
                "industry-weekly",
                str(workspace),
                "--web-search-mode",
                "disabled",
            ]
        )
        if new_result.returncode != 0:
            _append_check(
                checks,
                "codebuddy.handoff.workspace",
                False,
                _command_detail(new_result),
            )
            return
        run_result = _run_cli(
            [
                "run",
                "--workspace",
                str(workspace),
                "--runtime",
                "codebuddy",
            ]
        )
        handoff_json = workspace / "output" / "intermediate" / "agent_handoff.json"
        handoff_md = workspace / "output" / "intermediate" / "agent_handoff.md"
        database = workspace / "briefloop.db"
        combined_output = f"{run_result.stdout}\n{run_result.stderr}"
        legacy_controls = [
            workspace / "output" / "intermediate" / name
            for name in (
                "runtime_manifest.json",
                "workflow_state.json",
                "artifact_registry.json",
                "event_log.jsonl",
            )
        ]

    errors: list[str] = []
    if run_result.returncode == 0:
        errors.append("inactive CodeBuddy runtime returned success")
    if "runtime_adapter_unsupported" not in combined_output:
        errors.append("missing runtime_adapter_unsupported diagnostic")
    if database.exists():
        errors.append("ControlStore was written for inactive CodeBuddy runtime")
    if handoff_json.exists() or handoff_md.exists():
        errors.append("handoff files were written for inactive CodeBuddy runtime")
    if any(path.exists() for path in legacy_controls):
        errors.append(
            "legacy control files were written for inactive CodeBuddy runtime"
        )
    _append_check(
        checks,
        "codebuddy.runtime.fail_closed",
        not errors,
        f"errors={errors}",
    )


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{SRC}{os.pathsep}{current_pythonpath}" if current_pythonpath else str(SRC)
    )
    return subprocess.run(
        [sys.executable, "-m", "multi_agent_brief.cli.main", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _command_detail(result: subprocess.CompletedProcess[str]) -> str:
    stdout = (result.stdout or "").strip().splitlines()[-5:]
    stderr = (result.stderr or "").strip().splitlines()[-5:]
    return f"returncode={result.returncode}; stdout_tail={stdout}; stderr_tail={stderr}"


def _append_check(
    checks: list[dict[str, str]], check_id: str, ok: bool, detail: str
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "pass" if ok else "fail",
            "detail": detail,
        }
    )


def _print(payload: dict[str, Any], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print("CodeBuddy Adapter Smoke")
    print("=" * 32)
    for item in payload["checks"]:
        status = "OK" if item["status"] == "pass" else "FAIL"
        print(f"  [{status}] {item['id']}: {item['detail']}")
    print()
    if payload["ok"]:
        print("ALL CHECKS PASSED.")
    else:
        print("FAILED.")


if __name__ == "__main__":
    raise SystemExit(main())
