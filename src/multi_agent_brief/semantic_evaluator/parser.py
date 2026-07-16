"""Strict JSON-only parser for untrusted dimension responses."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Optional

from pydantic import ValidationError

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.semantic_evaluator.contracts import DimensionResponse, JsonObject
from multi_agent_brief.semantic_evaluator.errors import value_free_violations


PARSER_VERSION = "strict_dimension_json_v1"

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


def find_forbidden_keys(value: Any, forbidden: frozenset[str]) -> tuple[str, ...]:
    found: set[str] = set()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                if isinstance(key, str):
                    normalized = key.strip().casefold()
                    if normalized in forbidden:
                        found.add(normalized)
                stack.append(item)
        elif isinstance(current, list):
            stack.extend(current)
    return tuple(sorted(found))


def parse_dimension_response(raw_body: bytes) -> ParseResult:
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return ParseResult(None, None, ("parser_invalid_utf8",))
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return ParseResult(None, None, ("parser_invalid_json",))
    if not isinstance(payload, dict):
        return ParseResult(None, None, ("parser_top_level_not_object",))
    if find_forbidden_keys(payload, FORBIDDEN_AUTHORITY_KEYS):
        return ParseResult(None, payload, ("authority_output_forbidden",))
    if find_forbidden_keys(payload, FORBIDDEN_SECURITY_KEYS):
        return ParseResult(None, payload, ("tool_or_canary_output_forbidden",))
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
