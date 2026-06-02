"""Source Provider data models and abstract base class."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SourceItem:
    """Unified output from all source providers."""

    source_id: str
    source_name: str
    source_type: str  # manual, rss, web_search, api, filings, mcp, cli, local_file
    title: str
    content: str
    url: str = ""
    published_at: str = ""
    retrieved_at: str = field(default_factory=_utc_now_iso)
    language: str = ""
    reliability: str = "medium"  # high, medium, low
    dedupe_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class SourceQuery:
    """Parameters for source collection."""

    keywords: list[str] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    max_results: int = 50
    recency_days: int = 14
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceConfig:
    """Parsed source configuration from sources.yaml."""

    profile: str = "research"
    industry: str = ""
    enabled_providers: list[str] = field(default_factory=lambda: ["manual"])
    manual: dict[str, Any] = field(default_factory=dict)
    rss: dict[str, Any] = field(default_factory=dict)
    web_search: dict[str, Any] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)
    mcp: dict[str, Any] = field(default_factory=dict)
    cached_package: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceConfig:
        strategy = data.get("source_strategy", {})
        return cls(
            profile=strategy.get("profile", "research"),
            industry=strategy.get("industry", ""),
            enabled_providers=strategy.get("enabled_providers", ["manual"]),
            manual=data.get("manual", {}),
            rss=data.get("rss", {}),
            web_search=data.get("web_search", {}),
            api=data.get("api", {}),
            mcp=data.get("mcp", {}),
            cached_package=data.get("cached_package", {}),
        )


# Source profiles with allowed source types
SOURCE_PROFILES: dict[str, dict[str, Any]] = {
    "conservative": {
        "description": "Official and approved sources only",
        "allowed_types": ["manual", "local_file"],
        "web_search": False,
    },
    "research": {
        "description": "Official + industry media + web search",
        "allowed_types": ["manual", "rss", "local_file", "web_search"],
        "web_search": True,
    },
    "aggressive_signal": {
        "description": "Includes forums, social media, GitHub, blogs",
        "allowed_types": ["manual", "rss", "web_search", "mcp", "cli"],
        "web_search": True,
    },
}


class SourceProvider(ABC):
    """Abstract base class for all source providers."""

    name: str = "base"
    source_type: str = "unknown"

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate provider config. Return list of error messages (empty = OK)."""
        raise NotImplementedError

    @abstractmethod
    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        """Collect sources based on query and provider config."""
        raise NotImplementedError
