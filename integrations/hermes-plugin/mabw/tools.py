"""Tool handlers for the Hermes MABW plugin."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_PROFILE = {
    "audience": "management team",
    "language": "English",
    "cadence": "weekly",
    "source_style": "reliable research",
    "output_style": "executive brief, conclusion-first",
    "must_watch": [],
    "forbidden_sources": [],
    "web_search_mode": "configure_later",
}

REQUIRED_PROFILE_FIELDS = ("company_or_org", "industry_or_theme", "task_objective")


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _mabw_bin() -> str:
    return os.environ.get("MABW_BIN") or os.environ.get("MULTI_AGENT_BRIEF_BIN") or "multi-agent-brief"


def _resolve_workspace(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(DEFAULT_PROFILE)
    normalized.update(profile or {})
    for key in ("must_watch", "forbidden_sources"):
        value = normalized.get(key)
        if value in (None, ""):
            normalized[key] = []
        elif isinstance(value, str):
            normalized[key] = [item.strip() for item in value.split(",") if item.strip()]
    return normalized


def _validate_profile(profile: dict[str, Any]) -> list[str]:
    missing = []
    for field in REQUIRED_PROFILE_FIELDS:
        value = profile.get(field)
        if value is None or str(value).strip() == "":
            missing.append(field)
    return missing


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 300) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command": cmd,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "returncode": 127,
            "stdout": "",
            "stderr": f"Command not found: {cmd[0]}. Install MABW or set MABW_BIN.",
            "command": cmd,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": f"Command timed out after {timeout}s: {' '.join(cmd)}",
            "command": cmd,
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "command": cmd,
        }


def create_onboarding(args: dict, **kwargs) -> str:
    """Create onboarding.json from chat-collected answers."""
    del kwargs
    try:
        workspace = _resolve_workspace(args["workspace"])
        workspace.mkdir(parents=True, exist_ok=True)

        profile = _normalize_profile(args.get("profile", {}))
        missing = _validate_profile(profile)
        if missing:
            return _json({
                "ok": False,
                "error": "Missing required brief profile fields.",
                "missing": missing,
                "required": list(REQUIRED_PROFILE_FIELDS),
            })

        filename = args.get("onboarding_filename") or "onboarding.json"
        if Path(filename).name != filename:
            return _json({"ok": False, "error": "onboarding_filename must be a filename, not a path."})

        onboarding_path = workspace / filename
        onboarding_path.write_text(_json(profile) + "\n", encoding="utf-8")

        return _json({
            "ok": True,
            "workspace": str(workspace),
            "onboarding_path": str(onboarding_path),
            "profile": profile,
            "next_tool": "mabw_init_workspace",
            "next_args": {
                "workspace": str(workspace),
                "onboarding_path": str(onboarding_path),
            },
        })
    except Exception as exc:
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def init_workspace(args: dict, **kwargs) -> str:
    """Initialize a MABW workspace from onboarding.json."""
    del kwargs
    try:
        workspace = _resolve_workspace(args["workspace"])
        onboarding_path = Path(args["onboarding_path"]).expanduser().resolve()

        cmd = [
            _mabw_bin(),
            "init",
            str(workspace),
            "--from-onboarding",
            str(onboarding_path),
        ]
        result = _run(cmd)
        result["workspace"] = str(workspace)
        result["onboarding_path"] = str(onboarding_path)
        result["next_tool"] = "mabw_run_handoff"
        result["next_args"] = {"workspace": str(workspace), "runtime": "hermes"}
        return _json(result)
    except Exception as exc:
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def run_handoff(args: dict, **kwargs) -> str:
    """Run MABW runtime handoff for an initialized workspace."""
    del kwargs
    try:
        workspace = _resolve_workspace(args["workspace"])
        runtime = args.get("runtime") or "hermes"

        cmd = [_mabw_bin(), "run", "--workspace", str(workspace), "--runtime", runtime]
        result = _run(cmd)
        handoff_md = workspace / "output" / "intermediate" / "agent_handoff.md"
        handoff_json = workspace / "output" / "intermediate" / "agent_handoff.json"

        result.update({
            "workspace": str(workspace),
            "runtime": runtime,
            "handoff_md": str(handoff_md),
            "handoff_json": str(handoff_json),
            "handoff_md_exists": handoff_md.exists(),
            "handoff_json_exists": handoff_json.exists(),
            "next": "Read agent_handoff.md and continue the delegated workflow in Hermes.",
        })

        if shutil.which(_mabw_bin()) is None and _mabw_bin() == "multi-agent-brief":
            result["hint"] = "multi-agent-brief is not on PATH. Install MABW or set MABW_BIN."

        return _json(result)
    except Exception as exc:
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
