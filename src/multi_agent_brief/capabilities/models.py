"""Data models for the Capability Center."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CapabilityOption:
    """A sub-option within a capability (e.g. Tavily under web_search)."""

    id: str
    name: str
    description: str
    enabled: bool = False
    dependencies: list[str] = field(default_factory=list)


@dataclass
class CapabilitySpec:
    """Full specification of a user-facing capability."""

    id: str
    name: dict[str, str]  # {'en': ..., 'zh': ...}
    summary: dict[str, str]
    category: str  # source / processing / output / integration
    provider_name: str  # maps to SourceProvider key
    visibility: str = "standard"  # core / standard / advanced / internal
    maturity: str = "stable"  # stable / beta / experimental
    options: list[CapabilityOption] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    privacy_note: str = ""
    docs_path: str = ""


@dataclass
class RequirementResult:
    """Result of checking a single requirement (env var, CLI tool, file)."""

    requirement: str
    status: str  # OK / WARN / ERROR
    message: str


@dataclass
class CapabilityStatus:
    """Runtime status of a capability in a specific workspace context."""

    capability_id: str
    state: str  # ENABLED_READY / ENABLED_NEEDS_SETUP / AVAILABLE / UNAVAILABLE
    recommended: bool = False
    notes: str = ""


@dataclass
class Recommendation:
    """A deterministic recommendation for enabling a capability."""

    capability_id: str
    reason: str
    trigger_rule: str
