"""Package-owned Review Session static resource access and provenance checks."""

from __future__ import annotations

from importlib.resources import files
import json
from typing import Any

from multi_agent_brief.semantic_evaluator.serialization import sha256_bytes


_ROOT = "static"
_ASSETS = frozenset(
    {"index.html", "app.js", "style.css", "THIRD_PARTY_NOTICES.txt"}
)


def read_review_asset(name: str) -> bytes:
    if name not in _ASSETS:
        raise ValueError("review_session_asset_invalid")
    return files(__package__).joinpath(_ROOT, name).read_bytes()


def load_asset_provenance() -> dict[str, Any]:
    raw = files(__package__).joinpath(_ROOT, "provenance.json").read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("review_session_provenance_invalid") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        "briefloop.review_session.asset_provenance.v1"
    ):
        raise ValueError("review_session_provenance_invalid")
    production = payload.get("production_assets")
    if not isinstance(production, dict) or set(production) != _ASSETS:
        raise ValueError("review_session_provenance_invalid")
    for name, expected in production.items():
        if expected != sha256_bytes(read_review_asset(name)):
            raise ValueError("review_session_asset_hash_mismatch")
    return payload


__all__ = ["load_asset_provenance", "read_review_asset"]
