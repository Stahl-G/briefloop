"""Strict, value-free JSON object decoding shared by contract boundaries."""

from __future__ import annotations

import json
import math
from typing import Any


class StrictJsonError(ValueError):
    """Raised when bytes are not one finite, duplicate-free JSON object."""


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError
        result[key] = value
    return result


def _reject_non_finite_constant(_token: str) -> None:
    raise StrictJsonError


def parse_strict_json_object(payload: bytes) -> dict[str, Any]:
    """Decode one strict JSON object without exposing input values."""

    if type(payload) is not bytes:
        raise TypeError("strict JSON payload must be bytes")
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_non_finite_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, StrictJsonError, ValueError):
        raise StrictJsonError from None
    except RecursionError:
        raise StrictJsonError from None
    stack = [value]
    while stack:
        current = stack.pop()
        if type(current) is float and not math.isfinite(current):
            raise StrictJsonError
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    if not isinstance(value, dict):
        raise StrictJsonError
    return value


__all__ = ["StrictJsonError", "parse_strict_json_object"]
