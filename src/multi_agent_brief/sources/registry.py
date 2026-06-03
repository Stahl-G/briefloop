"""Source registry: loads config, instantiates providers, collects sources."""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None  # type: ignore[assignment]

from multi_agent_brief.sources.base import SourceConfig, SourceItem, SourceProvider, SourceQuery
from multi_agent_brief.sources.manual import ManualProvider
from multi_agent_brief.sources.rss import RssProvider
from multi_agent_brief.sources.web_search import WebSearchProvider
from multi_agent_brief.sources.api_news import NewsApiProvider
from multi_agent_brief.sources.api_filings import FilingsProvider
from multi_agent_brief.sources.mcp_provider import McpProvider
from multi_agent_brief.sources.cli_provider import CliProvider
from multi_agent_brief.sources.cached_package import CachedPackageProvider
from multi_agent_brief.sources.normalizer import normalize_source_item, dedupe_sources, filter_by_recency


# Provider registry
PROVIDER_CLASSES: dict[str, type[SourceProvider]] = {
    "manual": ManualProvider,
    "rss": RssProvider,
    "web_search": WebSearchProvider,
    "api": NewsApiProvider,
    "filings": FilingsProvider,
    "mcp": McpProvider,
    "cli": CliProvider,
    "cached_package": CachedPackageProvider,
}


def load_sources_config(path: str | Path) -> SourceConfig:
    """Load and parse sources.yaml into a SourceConfig."""
    p = Path(path)
    if not p.exists():
        return SourceConfig()
    text = p.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text) or {}
    else:
        data = _minimal_yaml_load(text)
    return SourceConfig.from_dict(data)


def get_providers(source_config: SourceConfig) -> dict[str, SourceProvider]:
    """Instantiate enabled providers based on source config."""
    providers: dict[str, SourceProvider] = {}
    for name in source_config.enabled_providers:
        cls = PROVIDER_CLASSES.get(name)
        if cls is None:
            continue
        providers[name] = cls()
    return providers


def collect_all_sources(
    source_config: SourceConfig,
    query: SourceQuery | None = None,
) -> tuple[list[SourceItem], list[dict[str, str]]]:
    """Collect sources from all enabled providers, normalize, and dedupe.

    Returns:
        Tuple of (deduplicated source items, list of error dicts with keys
        provider, error_type, message).
    """
    if query is None:
        query = SourceQuery()

    providers = get_providers(source_config)
    all_items: list[SourceItem] = []
    errors: list[dict[str, str]] = []

    # Surface unknown providers as errors
    for name in source_config.enabled_providers:
        if name not in PROVIDER_CLASSES:
            errors.append({
                "provider": name,
                "error_type": "UnknownProvider",
                "message": f"Unknown provider '{name}' is not registered. "
                f"Available: {', '.join(sorted(PROVIDER_CLASSES))}",
            })

    config_map = {
        "manual": source_config.manual,
        "rss": source_config.rss,
        "web_search": source_config.web_search,
        "api": source_config.api,
        "filings": source_config.api,  # filings share the api config section
        "mcp": source_config.mcp,
        "cli": source_config.mcp,  # cli shares the mcp config section
        "cached_package": source_config.cached_package,
    }

    for name, provider in providers.items():
        config = config_map.get(name, {})
        try:
            items = provider.collect(query, config)
            all_items.extend(items)
        except Exception as exc:
            # Provider failures are non-fatal, but record the error
            errors.append({
                "provider": name,
                "error_type": type(exc).__name__,
                "message": str(exc)[:200],
            })

    # Normalize, filter, dedupe
    normalized = [normalize_source_item(item) for item in all_items]

    recency = query.recency_days if query.recency_days > 0 else 14
    filtered = filter_by_recency(normalized, recency)

    return dedupe_sources(filtered), errors


def validate_all_providers(source_config: SourceConfig) -> list[str]:
    """Validate all enabled provider configs. Return list of error messages."""
    providers = get_providers(source_config)
    errors: list[str] = []

    # Surface unknown providers as errors
    for name in source_config.enabled_providers:
        if name not in PROVIDER_CLASSES:
            errors.append(
                f"Unknown provider '{name}' is not registered. "
                f"Available: {', '.join(sorted(PROVIDER_CLASSES))}"
            )

    config_map = {
        "manual": source_config.manual,
        "rss": source_config.rss,
        "web_search": source_config.web_search,
        "api": source_config.api,
        "filings": source_config.api,
        "mcp": source_config.mcp,
        "cli": source_config.mcp,
        "cached_package": source_config.cached_package,
    }

    for name, provider in providers.items():
        config = config_map.get(name, {})
        try:
            errs = provider.validate_config(config)
            errors.extend(errs)
        except Exception as e:
            errors.append(f"{name}: validation error: {e}")

    return errors


def _minimal_yaml_load(text: str) -> dict[str, Any]:
    """Fallback YAML parser for simple configs."""
    data: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if indent == 0 and line.endswith(":"):
            section_name = line[:-1]
            current_section = {}
            data[section_name] = current_section
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value in ("true", "false"):
            parsed = value == "true"
        elif value.startswith('"') and value.endswith('"'):
            parsed = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            parsed = value[1:-1]
        else:
            parsed = value
        target = current_section if indent > 0 and current_section is not None else data
        target[key.strip()] = parsed
    return data
