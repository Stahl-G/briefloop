#!/usr/bin/env python3
"""Quick launch smoke for a fresh source checkout.

This guard validates that the public setup/demo path still reaches a working
demo workspace handoff from the current checkout. It does not install package
dependencies, call an LLM, access the network, run subagents, or prove output
quality.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = (
    "Launch smoke verifies source-checkout setup/demo mechanics only: import, "
    "CLI version, demo init, doctor, and runtime handoff. It is not semantic "
    "truth proof, output-quality improvement proof, delivery approval, or "
    "release authorization."
)


def _tail(text: str, *, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(ROOT / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not existing else f"{src}{os.pathsep}{existing}"
    return env


def _run_step(
    *,
    step_id: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int = 60,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "id": step_id,
            "ok": False,
            "command": command,
            "returncode": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": f"timeout after {timeout}s",
            "stdout_tail": _tail(exc.stdout or ""),
            "stderr_tail": _tail(exc.stderr or ""),
        }
    return {
        "id": step_id,
        "ok": result.returncode == 0,
        "command": command,
        "returncode": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": _tail(result.stdout),
        "stderr_tail": _tail(result.stderr),
    }


def _check_repo_layout() -> dict[str, Any]:
    required = [
        "pyproject.toml",
        "scripts/setup.sh",
        "scripts/demo.sh",
        "src/multi_agent_brief/cli/main.py",
    ]
    missing = [rel for rel in required if not (ROOT / rel).exists()]
    return {
        "id": "repo_layout",
        "ok": not missing,
        "required_paths": required,
        "missing": missing,
        "error": f"missing required checkout files: {missing}" if missing else "",
    }


def _check_artifacts(workspace: Path) -> dict[str, Any]:
    required = [
        workspace / "config.yaml",
        workspace / "sources.yaml",
        workspace / "user.md",
        workspace / "output" / "intermediate" / "agent_handoff.md",
        workspace / "output" / "intermediate" / "agent_handoff.json",
        workspace / "output" / "intermediate" / "runtime_manifest.json",
        workspace / "output" / "intermediate" / "workflow_state.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    return {
        "id": "handoff_artifacts",
        "ok": not missing,
        "missing": missing,
        "error": f"missing expected handoff artifacts: {missing}" if missing else "",
    }


def run_launch_smoke() -> dict[str, Any]:
    env = _base_env()
    steps: list[dict[str, Any]] = []
    tmp_root = ""
    workspace_path = ""

    layout = _check_repo_layout()
    steps.append(layout)
    if not layout["ok"]:
        return _payload(False, steps, tmp_root=tmp_root, workspace_path=workspace_path)

    with tempfile.TemporaryDirectory(prefix="briefloop-launch-smoke-") as tmp:
        tmp_dir = Path(tmp).resolve()
        workspace = tmp_dir / "demo-workspace"
        tmp_root = str(tmp_dir)
        workspace_path = str(workspace)
        commands = [
            (
                "source_import",
                [sys.executable, "-c", "import multi_agent_brief; print('import-ok')"],
            ),
            (
                "cli_version",
                [sys.executable, "-m", "multi_agent_brief.cli.main", "version"],
            ),
            (
                "demo_init",
                [
                    sys.executable,
                    "-m",
                    "multi_agent_brief.cli.main",
                    "init",
                    str(workspace),
                    "--demo",
                    "--force",
                ],
            ),
            (
                "demo_doctor",
                [
                    sys.executable,
                    "-m",
                    "multi_agent_brief.cli.main",
                    "doctor",
                    "--config",
                    str(workspace / "config.yaml"),
                ],
            ),
            (
                "demo_runtime_handoff",
                [
                    sys.executable,
                    "-m",
                    "multi_agent_brief.cli.main",
                    "run",
                    "--workspace",
                    str(workspace),
                    "--runtime",
                    "manual",
                ],
            ),
        ]
        for step_id, command in commands:
            result = _run_step(
                step_id=step_id,
                command=command,
                cwd=tmp_dir,
                env=env,
                timeout=90,
            )
            steps.append(result)
            if not result["ok"]:
                return _payload(False, steps, tmp_root=tmp_root, workspace_path=workspace_path)
        steps.append(_check_artifacts(workspace))
        ok = all(step.get("ok") is True for step in steps)
        return _payload(ok, steps, tmp_root=tmp_root, workspace_path=workspace_path)


def _payload(
    ok: bool,
    steps: list[dict[str, Any]],
    *,
    tmp_root: str,
    workspace_path: str,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "check": "launch_demo_smoke",
        "repo_root": str(ROOT),
        "tmp_root": tmp_root,
        "workspace_path": workspace_path,
        "boundary": BOUNDARY,
        "steps": steps,
    }


def _print_human(payload: dict[str, Any]) -> None:
    print("Launch Demo Smoke")
    print("=" * 40)
    for step in payload["steps"]:
        status = "PASS" if step.get("ok") else "FAIL"
        print(f"  [{status}] {step.get('id')}")
        if not step.get("ok"):
            if step.get("error"):
                print(f"         error: {step['error']}")
            if step.get("command"):
                print(f"         command: {' '.join(step['command'])}")
            if step.get("returncode") is not None:
                print(f"         returncode: {step['returncode']}")
            stdout = str(step.get("stdout_tail") or "").strip()
            stderr = str(step.get("stderr_tail") or "").strip()
            if stdout:
                print("         stdout tail:")
                for line in stdout.splitlines():
                    print(f"           {line}")
            if stderr:
                print("         stderr tail:")
                for line in stderr.splitlines():
                    print(f"           {line}")
    print()
    print(payload["boundary"])
    print()
    print("ALL CHECKS PASSED." if payload["ok"] else "FAILED.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    payload = run_launch_smoke()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(payload)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
