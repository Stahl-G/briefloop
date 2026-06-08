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


def _find_repo_root() -> Path | None:
    """Walk upward from cwd and common locations to find the MABW source repo."""
    candidates = [Path.cwd().resolve()]
    # Also check common clone locations
    for d in ("~", "~/Developer", "~/dev", "~/projects", "~/workspace"):
        expanded = Path(d).expanduser()
        if expanded.is_dir():
            for child in sorted(expanded.iterdir()):
                if child.is_dir() and "multi-agent-brief" in child.name.lower():
                    candidates.append(child.resolve())
    seen = set()
    for start in candidates:
        for parent in [start] + list(start.parents):
            if parent in seen:
                continue
            seen.add(parent)
            if (parent / "pyproject.toml").exists() and (parent / "CLAUDE.md").exists():
                return parent
            # Allow single-marker match for shallow clones
            if (parent / "pyproject.toml").exists() and (parent / "src" / "multi_agent_brief").exists():
                return parent
    return None


def _find_workspace_dirs(repo_root: Path | None) -> list[Path]:
    """Discover plausible MABW workspace directories."""
    found: list[Path] = []
    seen: set[str] = set()
    # Always scan cwd and home-adjacent paths
    search_roots = [Path.cwd().resolve(), Path.home()]
    if repo_root is not None:
        search_roots.append(repo_root)
    for base in search_roots:
        for depth, candidate in enumerate([base] + list(base.parents)):
            if depth > 4:
                break
            key = str(candidate.resolve())
            if key in seen:
                continue
            seen.add(key)
            config = candidate / "config.yaml"
            sources = candidate / "sources.yaml"
            if config.exists() and sources.exists():
                found.append(candidate)
    return found


def env_doctor(_args: dict, **kwargs) -> str:
    """Check MABW environment and return a diagnostic report."""
    del _args, kwargs

    report: dict[str, Any] = {
        "repo_found": False,
        "plugin_enabled": False,
        "mabw_bin": _mabw_bin(),
        "version": "unknown",
        "venv_found": False,
        "workspace_found": False,
        "workspaces": [],
        "next_action": "collect_onboarding",
    }

    # 1. Repo detection
    repo_root = _find_repo_root()
    if repo_root is not None:
        report["repo_found"] = True
        report["repo_root"] = str(repo_root)

    # 2. Plugin presence
    plugin_dir = Path.home() / ".hermes" / "plugins" / "mabw"
    report["plugin_enabled"] = plugin_dir.is_dir()

    # 3. Binary and version
    mabw = _mabw_bin()
    found_bin = mabw if shutil.which(mabw) else None
    if found_bin is None and repo_root is not None:
        # Try venv path
        for bindir in (repo_root / ".venv" / "bin", repo_root / ".venv" / "Scripts"):
            candidate = bindir / "multi-agent-brief"
            if candidate.exists():
                found_bin = str(candidate)
                report["mabw_bin"] = found_bin
                break
    elif found_bin:
        report["mabw_bin"] = found_bin

    if found_bin:
        version_result = _run([found_bin, "version"])
        if version_result["ok"]:
            version_line = version_result["stdout"].strip().split("\n")[0]
            report["version"] = version_line

    # 4. Venv
    if repo_root is not None:
        for venv_dir in (repo_root / ".venv", repo_root / "venv"):
            activate = venv_dir / "bin" / "activate" if not _is_windows() else venv_dir / "Scripts" / "activate"
            if activate.exists():
                report["venv_found"] = True
                report["venv_path"] = str(venv_dir)
                break

    # 5. Workspaces
    workspaces = _find_workspace_dirs(repo_root)
    report["workspaces"] = [str(w) for w in workspaces]
    report["workspace_found"] = len(report["workspaces"]) > 0

    # 6. Next action
    if not report["repo_found"]:
        report["next_action"] = "clone_repo"
        report["hint"] = "Clone https://github.com/Stahl-G/multi-agent-brief-workflow.git first."
    elif not report["venv_found"]:
        report["next_action"] = "run_setup"
        report["hint"] = "Run bash scripts/setup.sh from the repo root."
    elif not found_bin:
        report["next_action"] = "activate_venv"
        report["hint"] = "Activate the venv: source .venv/bin/activate (or Scripts\\activate on Windows)."
    elif not report["plugin_enabled"]:
        report["next_action"] = "install_plugin"
        report["hint"] = "Run multi-agent-brief hermes install-plugin."
    elif report["workspace_found"]:
        report["next_action"] = "run_existing_workspace"
        report["hint"] = "Use mabw_run_handoff with the first existing workspace."
    else:
        report["next_action"] = "collect_onboarding"
        report["hint"] = "Ask the user for brief profile fields, then call mabw_create_onboarding."

    return _json(report)


def _is_windows() -> bool:
    import platform
    return platform.system() == "Windows"


def run_handoff(args: dict, **kwargs) -> str:
    """Run MABW runtime handoff for an initialized workspace."""
    del kwargs
    try:
        workspace = _resolve_workspace(args["workspace"])
        runtime = args.get("runtime") or "hermes"
        repo_root = _find_repo_root()

        cmd = [_mabw_bin(), "run", "--workspace", str(workspace), "--runtime", runtime]
        if repo_root is not None:
            cmd.extend(["--repo-workdir", str(repo_root)])
        result = _run(cmd, cwd=str(repo_root) if repo_root is not None else None)
        handoff_md = workspace / "output" / "intermediate" / "agent_handoff.md"
        handoff_json = workspace / "output" / "intermediate" / "agent_handoff.json"
        runtime_state_files = {
            "runtime_manifest": workspace / "output" / "intermediate" / "runtime_manifest.json",
            "workflow_state": workspace / "output" / "intermediate" / "workflow_state.json",
            "artifact_registry": workspace / "output" / "intermediate" / "artifact_registry.json",
            "event_log": workspace / "output" / "intermediate" / "event_log.jsonl",
        }
        feedback_state_files = {
            "feedback_issues": workspace / "output" / "intermediate" / "feedback_issues.json",
            "repair_plan": workspace / "output" / "intermediate" / "repair_plan.json",
            "delta_audit_report": workspace / "output" / "intermediate" / "delta_audit_report.json",
        }

        result.update({
            "workspace": str(workspace),
            "runtime": runtime,
            "repo_root": str(repo_root) if repo_root is not None else "",
            "handoff_md": str(handoff_md),
            "handoff_json": str(handoff_json),
            "handoff_md_exists": handoff_md.exists(),
            "handoff_json_exists": handoff_json.exists(),
            "runtime_state_files": {key: str(path) for key, path in runtime_state_files.items()},
            "runtime_state_files_exist": {key: path.exists() for key, path in runtime_state_files.items()},
            "feedback_state_files": {key: str(path) for key, path in feedback_state_files.items()},
            "feedback_state_files_exist": {key: path.exists() for key, path in feedback_state_files.items()},
            "next": (
                "Read agent_handoff.md and continue in Hermes as the Orchestrator main agent. "
                "Read configs/orchestrator_contract.yaml, configs/stage_specs.yaml, "
                "configs/artifact_contracts.yaml, configs/policy_packs/default.yaml, "
                "workflow_state.json, artifact_registry.json, and optional feedback state references before delegation."
            ),
        })

        if shutil.which(_mabw_bin()) is None and _mabw_bin() == "multi-agent-brief":
            result["hint"] = "multi-agent-brief is not on PATH. Install MABW or set MABW_BIN."

        return _json(result)
    except Exception as exc:
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
