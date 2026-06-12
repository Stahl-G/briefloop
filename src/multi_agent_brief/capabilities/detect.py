"""Readiness detectors — check env vars, CLI tools, and workspace files."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from multi_agent_brief.capabilities.catalog import get_capability
from multi_agent_brief.capabilities.models import (
    CapabilityStatus,
    RequirementResult,
)


def check_env_var(var_name: str) -> RequirementResult:
    """Check if an environment variable is set and non-empty."""
    value = os.environ.get(var_name, "")
    if value:
        return RequirementResult(requirement=var_name, status="OK", message=f"{var_name} is set")
    return RequirementResult(requirement=var_name, status="ERROR", message=f"{var_name} not set")


def check_cli_tool(tool_name: str) -> RequirementResult:
    """Check if a CLI tool is available on PATH."""
    if shutil.which(tool_name):
        return RequirementResult(requirement=tool_name, status="OK", message=f"{tool_name} found on PATH")
    if tool_name == "python":
        fallback = sys.executable or shutil.which("python3")
        if fallback:
            return RequirementResult(requirement=tool_name, status="OK", message=f"python available via {fallback}")
    return RequirementResult(requirement=tool_name, status="WARN", message=f"{tool_name} not found on PATH")


def check_file_exists(path: str | Path) -> RequirementResult:
    """Check if a file or directory exists."""
    p = Path(path)
    if p.exists():
        return RequirementResult(requirement=str(p), status="OK", message=f"{p} exists")
    return RequirementResult(requirement=str(p), status="WARN", message=f"{p} not found")


def detect_readiness(
    capability_id: str,
    workspace_dir: str | Path | None = None,
) -> list[RequirementResult]:
    """Detect readiness for a capability by checking its requirements.

    Returns a list of RequirementResult for each check performed.
    """
    cap = get_capability(capability_id)
    if cap is None:
        return [RequirementResult(
            requirement=capability_id,
            status="ERROR",
            message=f"Unknown capability: {capability_id}",
        )]

    results: list[RequirementResult] = []

    if capability_id == "web_search":
        if workspace_dir:
            sources_path = Path(workspace_dir) / "sources.yaml"
            if sources_path.exists():
                try:
                    import yaml
                    data = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
                    ws_config = data.get("web_search", {}) or {}
                    if ws_config.get("enabled") and ws_config.get("mode") == "runtime_tool":
                        results.append(RequirementResult(
                            requirement="runtime_web_search",
                            status="OK",
                            message=(
                                "runtime_tool mode uses the current runtime's built-in search; "
                                "no external API key is required"
                            ),
                        ))
                        return results
                except Exception as exc:
                    results.append(RequirementResult(
                        requirement="sources.yaml",
                        status="ERROR",
                        message=f"Failed to parse sources.yaml: {exc}",
                    ))
                    return results

        backends = {
            "tavily": "TAVILY_API_KEY",
            "exa": "EXA_API_KEY",
            "brave": "BRAVE_SEARCH_API_KEY",
            "firecrawl": "FIRECRAWL_API_KEY",
            "serper": "SERPER_API_KEY",
        }
        any_key = False
        for backend, env_var in backends.items():
            r = check_env_var(env_var)
            if r.status == "OK":
                any_key = True
        if not any_key:
            results.append(RequirementResult(
                requirement="search_backend",
                status="ERROR",
                message="No search backend API key configured. Set one in .env.",
            ))

    elif capability_id == "api_news":
        results.append(check_env_var("NEWSAPI_API_KEY"))

    elif capability_id == "mineru":
        results.append(check_env_var("MINERU_API_TOKEN"))

    elif capability_id == "feishu":
        results.append(check_cli_tool("lark-cli"))

    elif capability_id == "opencli":
        results.append(check_cli_tool("opencli"))

    elif capability_id == "filing_resolver":
        if workspace_dir:
            sources_path = Path(workspace_dir) / "sources.yaml"
            if sources_path.exists():
                try:
                    import yaml
                    data = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
                    fr_config = data.get("filing_resolver", {})
                    tickers = fr_config.get("tickers", [])
                    if tickers:
                        results.append(RequirementResult(
                            requirement="tickers",
                            status="OK",
                            message=f"Tickers configured: {', '.join(str(t) for t in tickers)}",
                        ))
                    else:
                        results.append(RequirementResult(
                            requirement="tickers",
                            status="WARN",
                            message="filing_resolver has no tickers configured",
                        ))
                except Exception as e:
                    results.append(RequirementResult(
                        requirement="sources.yaml",
                        status="ERROR",
                        message=f"Failed to parse sources.yaml: {e}",
                    ))
            else:
                results.append(RequirementResult(
                    requirement="sources.yaml",
                    status="WARN",
                    message="sources.yaml not found, cannot check tickers",
                ))

    return results


def assess_capability(
    capability_id: str,
    workspace_dir: str | Path | None = None,
    enabled_providers: set[str] | None = None,
) -> CapabilityStatus:
    """Assess the full status of a capability."""
    cap = get_capability(capability_id)
    if cap is None:
        return CapabilityStatus(
            capability_id=capability_id,
            state="UNAVAILABLE",
            notes=f"Unknown capability: {capability_id}",
        )

    is_enabled = enabled_providers is not None and cap.provider_name in enabled_providers
    reqs = detect_readiness(capability_id, workspace_dir)
    has_errors = any(r.status == "ERROR" for r in reqs)

    if is_enabled and not has_errors:
        state = "ENABLED_READY"
    elif is_enabled and has_errors:
        state = "ENABLED_NEEDS_SETUP"
    elif not is_enabled:
        state = "AVAILABLE"
    else:
        state = "UNAVAILABLE"

    notes = "; ".join(r.message for r in reqs if r.status != "OK") or ""

    return CapabilityStatus(
        capability_id=capability_id,
        state=state,
        notes=notes,
    )
