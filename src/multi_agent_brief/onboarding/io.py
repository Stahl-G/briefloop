"""OnboardingResult I/O: JSON load/save with tolerance for unknown or missing fields."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.onboarding.schema import OnboardingResult

_DATACLASS_FIELDS = {f.name for f in OnboardingResult.__dataclass_fields__.values()}  # type: ignore[attr-defined]


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
    # Keep only known fields; ignore unknown keys.
    known = {k: v for k, v in data.items() if k in _DATACLASS_FIELDS}
    return OnboardingResult(**known)


def save_onboarding_result(result: OnboardingResult, path: str | Path) -> None:
    """Save an OnboardingResult to a JSON file (UTF-8, pretty-printed)."""
    from dataclasses import asdict

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
