"""Doctor: checks source configuration health."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from multi_agent_brief.sources.base import SourceConfig
from multi_agent_brief.sources.registry import load_sources_config, validate_all_providers


class CheckResult:
    def __init__(self, status: str, message: str) -> None:
        self.status = status  # OK, WARN, ERROR
        self.message = message

    def __str__(self) -> str:
        icon = {"OK": "[OK]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(self.status, "[?]")
        return f"  {icon} {self.message}"


def run_doctor(
    config_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
) -> list[CheckResult]:
    """Run all doctor checks and return results."""
    results: list[CheckResult] = []

    # 1. Config file existence
    if config_path:
        p = Path(config_path)
        if not p.exists():
            results.append(CheckResult("ERROR", f"Config file not found: {p}"))
            return results
        results.append(CheckResult("OK", f"Config file exists: {p.name}"))
    else:
        results.append(CheckResult("WARN", "No config path provided, skipping config checks"))
        return results

    # 2. Load sources config
    # sources.yaml is usually alongside config.yaml
    sources_path = p.parent / "sources.yaml"
    if not sources_path.exists():
        results.append(CheckResult("WARN", "sources.yaml not found, using defaults"))
        source_config = SourceConfig()
    else:
        results.append(CheckResult("OK", "sources.yaml found"))
        try:
            source_config = load_sources_config(sources_path)
            results.append(CheckResult("OK", f"sources.yaml parsed successfully"))
        except Exception as e:
            results.append(CheckResult("ERROR", f"sources.yaml parse error: {e}"))
            return results

    # 3. Profile
    results.append(CheckResult("OK", f"Source profile: {source_config.profile}"))

    # 4. Enabled providers
    results.append(CheckResult("OK", f"Enabled providers: {', '.join(source_config.enabled_providers) or 'none'}"))

    # 5. Manual sources
    manual_sources = source_config.manual.get("sources", [])
    if source_config.manual.get("enabled", True):
        results.append(CheckResult("OK", f"Manual sources configured: {len(manual_sources)}"))
    elif manual_sources:
        results.append(CheckResult("WARN", "Manual sources configured but disabled"))

    # 6. RSS feeds
    rss_feeds = source_config.rss.get("feeds", [])
    if source_config.rss.get("enabled"):
        results.append(CheckResult("OK", f"RSS feeds configured: {len(rss_feeds)}"))
    elif rss_feeds:
        results.append(CheckResult("WARN", "RSS feeds configured but disabled"))

    # 7. Web search
    if source_config.web_search.get("enabled"):
        backend_name = source_config.web_search.get("backend", "mock")
        if backend_name == "mock":
            results.append(CheckResult("WARN", "web_search uses mock backend; suitable for tests/demo only"))
        else:
            results.append(CheckResult("OK", f"web_search enabled with backend: {backend_name}"))

    # 8. API providers
    api_providers = source_config.api.get("providers", [])
    if source_config.api.get("enabled"):
        for provider in api_providers:
            env_key = provider.get("api_key_env", "")
            if env_key:
                if os.environ.get(env_key):
                    results.append(CheckResult("OK", f"API key {env_key} is set"))
                else:
                    results.append(CheckResult("WARN", f"API key {env_key} not found, {provider.get('name', '?')} disabled"))
            else:
                results.append(CheckResult("WARN", f"API provider '{provider.get('name', '?')}' has no api_key_env"))
    elif api_providers:
        results.append(CheckResult("WARN", "API providers configured but disabled"))

    # 9. MCP servers
    mcp_servers = source_config.mcp.get("servers", [])
    if source_config.mcp.get("enabled"):
        results.append(CheckResult("WARN", f"MCP enabled with {len(mcp_servers)} servers (Phase 1 stub)"))
    elif mcp_servers:
        results.append(CheckResult("WARN", "MCP servers configured but disabled"))

    # 10. Provider config validation
    errors = validate_all_providers(source_config)
    for err in errors:
        results.append(CheckResult("ERROR", err))
    if not errors:
        results.append(CheckResult("OK", "Provider config validation passed"))

    # 11. Output directory
    output_dir = p.parent / "output"
    if output_dir.exists():
        try:
            test_file = output_dir / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            results.append(CheckResult("OK", "Output directory writable"))
        except (PermissionError, OSError):
            results.append(CheckResult("ERROR", "Output directory not writable"))
    else:
        results.append(CheckResult("OK", "Output directory does not exist yet (will be created)"))

    return results


def format_doctor_report(results: list[CheckResult]) -> str:
    """Format doctor results into a readable report."""
    lines = ["Source configuration check", ""]
    for r in results:
        lines.append(str(r))
    lines.append("")

    errors = sum(1 for r in results if r.status == "ERROR")
    warns = sum(1 for r in results if r.status == "WARN")
    oks = sum(1 for r in results if r.status == "OK")

    if errors:
        lines.append(f"Result: {errors} error(s), {warns} warning(s), {oks} OK")
    elif warns:
        lines.append(f"Result: {warns} warning(s), {oks} OK — review warnings above")
    else:
        lines.append(f"Result: all {oks} checks passed")

    return "\n".join(lines)
