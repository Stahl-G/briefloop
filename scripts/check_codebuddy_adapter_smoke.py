#!/usr/bin/env python3
"""Deterministic smoke check for the source-clone CodeBuddy adapter.

This check validates local repository assets and generated `--runtime codebuddy`
handoff content. It does not launch CodeBuddy and is not delegated-runtime proof.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    checks: list[dict[str, str]] = []
    _check_source_assets(checks)
    if all(item["status"] == "pass" for item in checks):
        _check_codebuddy_handoff(checks)
        _check_missing_codebuddy_assets_fail_closed(checks)

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
        _append_check(checks, "codebuddy.skill.exists", False, "missing .codebuddy/skills/briefloop/SKILL.md")
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
    missing_skill = [phrase for phrase in required_skill_phrases if phrase not in skill_text]
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
    tools = _frontmatter_tools(frontmatter)
    expected_tools = EXPECTED_ROLE_AGENT_TOOLS.get(role)
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
            return stripped[len(prefix):].strip()
    return None


def _frontmatter_has_key(frontmatter: str, key: str) -> bool:
    return _frontmatter_value(frontmatter, key) is not None


def _frontmatter_tools(frontmatter: str) -> list[str]:
    value = _frontmatter_value(frontmatter, "tools")
    if not value:
        return []
    return [tool.strip() for tool in value.split(",") if tool.strip()]


def _check_codebuddy_handoff(checks: list[dict[str, str]]) -> None:
    with tempfile.TemporaryDirectory(prefix="briefloop-codebuddy-smoke-") as temp:
        workspace = Path(temp) / "ws"
        new_result = _run_cli(["new", "industry-weekly", str(workspace)])
        if new_result.returncode != 0:
            _append_check(checks, "codebuddy.handoff.workspace", False, _command_detail(new_result))
            return
        run_result = _run_cli(["run", "--workspace", str(workspace), "--runtime", "codebuddy", "--skip-doctor"])
        if run_result.returncode != 0:
            _append_check(checks, "codebuddy.handoff.run", False, _command_detail(run_result))
            return

        handoff_json = workspace / "output" / "intermediate" / "agent_handoff.json"
        handoff_md = workspace / "output" / "intermediate" / "agent_handoff.md"
        if not handoff_json.exists() or not handoff_md.exists():
            _append_check(checks, "codebuddy.handoff.files", False, "handoff files missing")
            return
        try:
            data = json.loads(handoff_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _append_check(checks, "codebuddy.handoff.json", False, f"invalid json: {exc}")
            return
        prompt = str(data.get("prompt") or "")
        capabilities = data.get("runtime_capabilities")
        if not isinstance(capabilities, dict):
            _append_check(checks, "codebuddy.handoff.capabilities", False, "runtime_capabilities missing")
            return

    errors: list[str] = []
    if data.get("runtime") != "codebuddy":
        errors.append(f"runtime={data.get('runtime')!r}")
    expected_capabilities: dict[str, Any] = {
        "runtime": "codebuddy",
        "host": "codebuddy",
        "delegation_supported": True,
        "nested_subagents_supported": False,
        "main_session_runs_cli_transactions": True,
        "role_agents_run_cli_transactions": False,
        "must_not_claim_uninvoked_subagents_ran": True,
        "skill_path": ".codebuddy/skills/briefloop/SKILL.md",
        "role_agent_path_glob": ".codebuddy/agents/briefloop-*.md",
    }
    for key, expected in expected_capabilities.items():
        if capabilities.get(key) != expected:
            errors.append(f"{key}={capabilities.get(key)!r}")
    if capabilities.get("subagent_names") != ROLE_AGENTS:
        errors.append(f"subagent_names={capabilities.get('subagent_names')!r}")
    for phrase in [
        ".codebuddy/skills/briefloop/SKILL.md",
        "The main CodeBuddy session is the Orchestrator main agent",
        "Role sub-agents must not run `briefloop` or `multi-agent-brief` CLI commands",
        "CodeBuddy sub-agents cannot spawn other sub-agents",
    ]:
        if phrase not in prompt:
            errors.append(f"prompt missing {phrase!r}")
    for role in ROLE_AGENTS:
        if role not in prompt:
            errors.append(f"prompt missing {role}")
    for snippet in FORBIDDEN_HANDOFF_SNIPPETS:
        if snippet in prompt:
            errors.append(f"forbidden prompt snippet: {snippet}")
    _append_check(
        checks,
        "codebuddy.handoff.contract",
        not errors,
        f"errors={errors}",
    )


def _check_missing_codebuddy_assets_fail_closed(checks: list[dict[str, str]]) -> None:
    with tempfile.TemporaryDirectory(prefix="briefloop-codebuddy-missing-assets-") as temp:
        temp_path = Path(temp)
        workspace = temp_path / "ws"
        fake_repo = temp_path / "repo_without_codebuddy"
        _copy_contract_refs(fake_repo)

        new_result = _run_cli(["new", "industry-weekly", str(workspace)])
        if new_result.returncode != 0:
            _append_check(checks, "codebuddy.missing_assets.workspace", False, _command_detail(new_result))
            return
        run_result = _run_cli([
            "run",
            "--workspace", str(workspace),
            "--runtime", "codebuddy",
            "--repo-workdir", str(fake_repo),
            "--skip-doctor",
        ])
        handoff_json = workspace / "output" / "intermediate" / "agent_handoff.json"
        combined_output = f"{run_result.stdout}\n{run_result.stderr}"
        handoff_written = handoff_json.exists()

    errors: list[str] = []
    if run_result.returncode == 0:
        errors.append("missing CodeBuddy assets run returned success")
    if "CodeBuddy runtime is source-clone-only" not in combined_output:
        errors.append("missing source-clone-only diagnostic")
    if ".codebuddy/skills/briefloop/SKILL.md" not in combined_output:
        errors.append("missing skill path diagnostic")
    if handoff_written:
        errors.append("handoff JSON was written despite missing CodeBuddy assets")
    _append_check(
        checks,
        "codebuddy.missing_assets.fail_closed",
        not errors,
        f"errors={errors}",
    )


def _copy_contract_refs(target_repo: Path) -> None:
    target_repo.mkdir(parents=True, exist_ok=True)
    (target_repo / "__init__.py").write_text("", encoding="utf-8")
    rel_paths = [
        Path("configs") / "orchestrator_contract.yaml",
        Path("configs") / "stage_specs.yaml",
        Path("configs") / "artifact_contracts.yaml",
        Path("configs") / "policy_packs" / "default.yaml",
    ]
    for rel_path in rel_paths:
        source = ROOT / rel_path
        target = target_repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{SRC}{os.pathsep}{current_pythonpath}" if current_pythonpath else str(SRC)
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


def _append_check(checks: list[dict[str, str]], check_id: str, ok: bool, detail: str) -> None:
    checks.append({
        "id": check_id,
        "status": "pass" if ok else "fail",
        "detail": detail,
    })


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
