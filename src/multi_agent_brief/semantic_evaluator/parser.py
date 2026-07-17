"""Strict JSON-only parser for untrusted dimension responses."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Optional

from pydantic import ValidationError

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.semantic_evaluator.contracts import DimensionResponse, JsonObject
from multi_agent_brief.semantic_evaluator.errors import value_free_violations


PARSER_VERSION = "strict_dimension_json_v2"

_CANARY_RE = re.compile(r"^BLSE_CANARY_V1_[0-9a-f]{64}$")
_HEX_BYTES = frozenset(b"0123456789abcdefABCDEF")

FORBIDDEN_AUTHORITY_KEYS = frozenset(
    {
        "overall_quality_score",
        "pass",
        "fail",
        "gate_result",
        "delivery_decision",
        "release_recommendation",
        "claim_support_truth",
        "auto_apply",
        "finalize",
    }
)
FORBIDDEN_SECURITY_KEYS = frozenset(
    {
        "tool_call",
        "tool_calls",
        "tool_result",
        "tool_results",
        "canary",
        "model_role",
        "role_change",
        "hidden_attachment",
        "hidden_attachment_ref",
        "provider_file_search",
    }
)


@dataclass(frozen=True)
class ParseResult:
    response: Optional[DimensionResponse]
    raw_object: Optional[JsonObject]
    reason_codes: tuple[str, ...]
    violations: tuple[FieldViolation, ...] = ()

    @property
    def ok(self) -> bool:
        return self.response is not None and not self.reason_codes


class _ObjectPairs(list[tuple[str, Any]]):
    """JSON object members before last-member-wins collapse."""


def _object_pairs_hook(pairs: list[tuple[str, Any]]) -> _ObjectPairs:
    return _ObjectPairs(pairs)


def _scan_member_occurrences(
    value: Any,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    authority: set[str] = set()
    security: set[str] = set()
    duplicate = False
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, _ObjectPairs):
            seen: set[str] = set()
            for key, item in current:
                if key in seen:
                    duplicate = True
                seen.add(key)
                normalized = key.strip().casefold()
                if normalized in FORBIDDEN_AUTHORITY_KEYS:
                    authority.add(normalized)
                if normalized in FORBIDDEN_SECURITY_KEYS:
                    security.add(normalized)
                stack.append(item)
        elif isinstance(current, list):
            stack.extend(current)
    return tuple(sorted(authority)), tuple(sorted(security)), duplicate


def _collapse_object_pairs(value: Any) -> Any:
    if isinstance(value, _ObjectPairs):
        return {key: _collapse_object_pairs(item) for key, item in value}
    if isinstance(value, list):
        return [_collapse_object_pairs(item) for item in value]
    return value


def find_forbidden_keys(value: Any, forbidden: frozenset[str]) -> tuple[str, ...]:
    found: set[str] = set()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, _ObjectPairs):
            for key, item in current:
                normalized = key.strip().casefold()
                if normalized in forbidden:
                    found.add(normalized)
                stack.append(item)
        elif isinstance(current, dict):
            for key, item in current.items():
                if isinstance(key, str):
                    normalized = key.strip().casefold()
                    if normalized in forbidden:
                        found.add(normalized)
                stack.append(item)
        elif isinstance(current, list):
            stack.extend(current)
    return tuple(sorted(found))


def _strict_canary_bytes(values: tuple[str, ...]) -> tuple[bytes, ...] | None:
    if (
        type(values) is not tuple
        or len(values) != 1
        or type(values[0]) is not str
        or _CANARY_RE.fullmatch(values[0]) is None
    ):
        return None
    return (values[0].encode("ascii"),)


def _security_scan_bytes(raw_body: bytes, canaries: tuple[bytes, ...]) -> bool:
    """Recognize literal/JSON-unicode sentinel bytes without parsing JSON."""

    normalized = bytearray()
    index = 0
    size = len(raw_body)
    while index < size:
        current = raw_body[index]
        if current != 0x5C:
            normalized.append(current)
            index += 1
            continue

        run_start = index
        while index < size and raw_body[index] == 0x5C:
            index += 1
        run_length = index - run_start
        normalized.extend(b"\xff" * (run_length // 2))
        if run_length % 2 == 0:
            continue

        if (
            index + 5 <= size
            and raw_body[index] == ord("u")
            and raw_body[index + 1 : index + 3] == b"00"
            and raw_body[index + 3] in _HEX_BYTES
            and raw_body[index + 4] in _HEX_BYTES
        ):
            normalized.append(int(raw_body[index + 3 : index + 5], 16))
            index += 5
        else:
            normalized.append(0xFF)

    return any(canary in normalized for canary in canaries)


def parse_dimension_response(
    raw_body: bytes,
    *,
    forbidden_canary_values: tuple[str, ...],
) -> ParseResult:
    canaries = _strict_canary_bytes(forbidden_canary_values)
    if canaries is None:
        return ParseResult(None, None, ("parser_schema_invalid",))
    if _security_scan_bytes(raw_body, canaries):
        return ParseResult(None, None, ("tool_or_canary_output_forbidden",))
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return ParseResult(None, None, ("parser_invalid_utf8",))
    try:
        uncollapsed = json.loads(text, object_pairs_hook=_object_pairs_hook)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return ParseResult(None, None, ("parser_invalid_json",))
    try:
        authority, security, duplicate = _scan_member_occurrences(uncollapsed)
        payload = _collapse_object_pairs(uncollapsed)
    except RecursionError:
        return ParseResult(None, None, ("parser_invalid_json",))
    if not isinstance(payload, dict):
        return ParseResult(None, None, ("parser_top_level_not_object",))
    if security:
        return ParseResult(None, payload, ("tool_or_canary_output_forbidden",))
    if authority:
        return ParseResult(None, payload, ("authority_output_forbidden",))
    if duplicate:
        return ParseResult(None, payload, ("parser_duplicate_member",))
    try:
        response = DimensionResponse.model_validate(payload)
    except ValidationError as exc:
        return ParseResult(
            None,
            payload,
            ("parser_schema_invalid",),
            value_free_violations(exc),
        )
    return ParseResult(response, payload, ())


__all__ = [
    "FORBIDDEN_AUTHORITY_KEYS",
    "FORBIDDEN_SECURITY_KEYS",
    "PARSER_VERSION",
    "ParseResult",
    "find_forbidden_keys",
    "parse_dimension_response",
]
