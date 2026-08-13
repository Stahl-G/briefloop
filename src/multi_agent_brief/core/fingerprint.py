"""Layer-neutral canonical fingerprints for deterministic derived identities."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_fingerprint(value: Any) -> str:
    """Hash one JSON-compatible value without importing ControlStore authority."""

    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("canonical_json_invalid") from exc
    return hashlib.sha256(payload).hexdigest()


__all__ = ["canonical_fingerprint"]
