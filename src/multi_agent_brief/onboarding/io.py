"""OnboardingResult I/O: JSON load/save with tolerance for unknown or missing fields."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from dataclasses import fields

from multi_agent_brief.onboarding.schema import OnboardingResult

_DATACLASS_FIELDS = {f.name for f in fields(OnboardingResult)}
_LIST_FIELDS = {"forbidden_sources", "must_watch", "missing"}
_INT_FIELDS = {"max_items_per_brief", "source_age_days"}
_BOOL_FIELDS = {"tavily_enabled"}
_DICT_FIELDS = {"market_scope", "competitor_preferences"}

# Agent-generated onboarding.json often uses simplified field names.
# Map them to canonical OnboardingResult field names.
_FIELD_ALIASES: dict[str, str] = {
    "company": "company_or_org",
    "industry": "industry_or_theme",
    "title": "brief_title",
    "audience": "audience_plain",
    "language": "language_plain",
    "output_language": "language_plain",
    "cadence": "cadence_plain",
    "source_style": "source_style_plain",
    "output_style": "output_style_plain",
    "focus_areas": "must_watch",
    "monitoring_scope": "must_watch",
}


def load_onboarding_result(path: str | Path) -> OnboardingResult:
    """Load an OnboardingResult from a JSON file.

    Unknown fields are silently ignored.
    Missing fields use dataclass defaults.
    """
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(
            f"Onboarding JSON must be a JSON object, got {type(data).__name__}."
        )
    # Apply field aliases (agent-generated JSON often uses short names)
    aliased: dict[str, Any] = {}
    for k, v in data.items():
        canonical = _FIELD_ALIASES.get(k, k)
        if canonical in aliased:
            if k == canonical:
                aliased[canonical] = v
            continue  # canonical keys override aliases; aliases otherwise keep first write
        aliased[canonical] = v

    # Keep only known fields; ignore unknown keys.
    known = {k: _normalize_field(k, v) for k, v in aliased.items() if k in _DATACLASS_FIELDS}
    return OnboardingResult(**known)


def _normalize_field(name: str, value: Any) -> Any:
    if name in _LIST_FIELDS:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        raise ValueError(f"onboarding.{name} must be a string or list of strings")
    if name in _INT_FIELDS:
        if value in (None, ""):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"onboarding.{name} must be an integer") from exc
    if name in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
    if name in _DICT_FIELDS:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        raise ValueError(f"onboarding.{name} must be a JSON object")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return "" if value is None else str(value) if not isinstance(value, bool) else value
    raise ValueError(f"onboarding.{name} must be a scalar value")


def save_onboarding_result(result: OnboardingResult, path: str | Path) -> None:
    """Save an OnboardingResult to a JSON file (UTF-8, pretty-printed)."""
    from dataclasses import asdict

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
