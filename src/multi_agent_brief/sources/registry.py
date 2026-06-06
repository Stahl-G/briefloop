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
from multi_agent_brief.sources.feishu_provider import FeishuProvider
from multi_agent_brief.sources.mineru_provider import MineruProvider
from multi_agent_brief.sources.filing_resolver import FilingResolverProvider
from multi_agent_brief.sources.local_signal import LocalSignalProvider
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
    "feishu": FeishuProvider,
    "mineru": MineruProvider,
    "filing_resolver": FilingResolverProvider,
    "local_signal": LocalSignalProvider,
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
    config = SourceConfig.from_dict(data)
    config.config_dir = str(p.parent)
    return config


def _resolve_manual_paths(manual: dict[str, Any], config_dir: str) -> dict[str, Any]:
    """Resolve relative paths in manual.sources[] against config_dir."""
    if not config_dir or not manual.get("sources"):
        return manual
    base = Path(config_dir)
    resolved = []
    for src in manual["sources"]:
        path = src.get("path")
        if path and not Path(path).is_absolute():
            src = {**src, "path": str(base / path)}
        resolved.append(src)
    return {**manual, "sources": resolved}


def _resolve_cached_package_paths(cached_package: dict[str, Any], config_dir: str) -> dict[str, Any]:
    """Resolve relative cached_package.paths[] against config_dir."""
    if not config_dir or not cached_package.get("paths"):
        return cached_package
    base = Path(config_dir)
    paths = []
    for path in cached_package.get("paths", []):
        p = Path(str(path))
        paths.append(str(p if p.is_absolute() else base / p))
    return {**cached_package, "paths": paths}


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

    # Resolve relative manual source paths against config_dir
    manual_config = _resolve_manual_paths(source_config.manual, source_config.config_dir)
    cached_package_config = _resolve_cached_package_paths(
        source_config.cached_package,
        source_config.config_dir,
    )

    config_map = {
        "manual": manual_config,
        "rss": source_config.rss,
        "web_search": source_config.web_search,
        "api": source_config.api,
        "filings": source_config.api,  # filings share the api config section
        "mcp": source_config.mcp,
        "cli": source_config.mcp,  # cli shares the mcp config section
        "cached_package": cached_package_config,
        "feishu": source_config.feishu,
        "mineru": source_config.mineru,
        "filing_resolver": source_config.filing_resolver,
        "local_signal": source_config.local_signal,
    }

    # Run provider config validation before collecting (B08)
    validation_errors = validate_all_providers(source_config)
    for ve in validation_errors:
        errors.append({
            "provider": "validation",
            "error_type": "ConfigValidationError",
            "message": ve,
        })

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

    # Normalize, separate error/placeholder items from usable (B10)
    usable: list[SourceItem] = []
    for item in all_items:
        item = normalize_source_item(item)
        if _is_error_or_placeholder(item):
            errors.append({
                "provider": item.source_type.replace("_error", ""),
                "error_type": item.metadata.get("error_type", "PlaceholderSource"),
                "message": f"Source '{item.source_name}' is not usable: {item.content[:120]}",
            })
        else:
            usable.append(item)

    recency = query.recency_days
    report_date = query.metadata.get("report_date", "")
    filtered = filter_by_recency(usable, recency, report_date=report_date)

    return dedupe_sources(filtered), errors


def _is_error_or_placeholder(item: SourceItem) -> bool:
    """Return True if this SourceItem is an error or placeholder, not usable content."""
    if item.metadata.get("error_type"):
        return True
    if item.metadata.get("requires_fetch"):
        return True
    if item.metadata.get("ingestion_status") == "placeholder":
        return True
    if item.metadata.get("filtered_reason"):
        return True
    if item.metadata.get("low_quality"):
        return True
    if item.source_type.endswith("_error"):
        return True
    return False


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

    manual_config = _resolve_manual_paths(source_config.manual, source_config.config_dir)
    cached_package_config = _resolve_cached_package_paths(
        source_config.cached_package,
        source_config.config_dir,
    )

    config_map = {
        "manual": manual_config,
        "rss": source_config.rss,
        "web_search": source_config.web_search,
        "api": source_config.api,
        "filings": source_config.api,
        "mcp": source_config.mcp,
        "cli": source_config.mcp,
        "cached_package": cached_package_config,
        "feishu": source_config.feishu,
        "mineru": source_config.mineru,
        "filing_resolver": source_config.filing_resolver,
        "local_signal": source_config.local_signal,
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
