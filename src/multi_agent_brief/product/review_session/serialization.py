"""Self-contained canonical JSON and SHA-256 helpers for the Review Session.

The Review Session kernel sits behind the LAJ isolation ratchet: it must not
import the semantic evaluator (isolated instrument) or any authority module.
These helpers deliberately duplicate the evaluator's canonical byte format so
advisory payloads stay hash-compatible without a cross-boundary import.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from pydantic import BaseModel


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", warnings="error")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("canonical_json_invalid") from exc


def canonical_model_sha256(model: BaseModel, *, exclude: Iterable[str] = ()) -> str:
    payload = model.model_dump(mode="json", exclude=set(exclude), warnings="error")
    return sha256_bytes(canonical_json_bytes(payload))


__all__ = ["canonical_json_bytes", "canonical_model_sha256", "sha256_bytes"]
