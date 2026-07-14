"""Contract validation errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import ValidationError


@dataclass
class FieldViolation:
    """A single validation failure on a specific field."""

    field: str
    error: str
    severity: str = "error"  # "error" | "warning"

    def __str__(self) -> str:
        return f"[{self.severity}] {self.field}: {self.error}"


class ContractError(Exception):
    """Raised when contract validation fails with one or more violations."""

    def __init__(
        self,
        violations: list[FieldViolation],
        schema_id: str = "",
        schema_version: str = "",
    ) -> None:
        self.violations = violations
        self.schema_id = schema_id
        self.schema_version = schema_version
        msg = f"Contract '{schema_id}' v{schema_version} failed with {len(violations)} violation(s)"
        super().__init__(msg)

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")


_PYDANTIC_ERROR_MESSAGES = {
    "missing": "required field is missing",
    "extra_forbidden": "extra field is not permitted",
    "bool_type": "must be a boolean",
    "bytes_type": "must be bytes",
    "date_type": "must be a valid date",
    "datetime_type": "must be a valid date-time",
    "dict_type": "must be an object",
    "enum": "must be one of the allowed values",
    "finite_number": "must be a finite number",
    "float_type": "must be a number",
    "frozen_instance": "field is immutable",
    "greater_than": "must be greater than the minimum",
    "greater_than_equal": "must meet the minimum",
    "int_type": "must be an integer",
    "invalid_key": "object key is invalid",
    "is_instance_of": "has the wrong type",
    "less_than": "must be less than the maximum",
    "less_than_equal": "must not exceed the maximum",
    "list_type": "must be a list",
    "literal_error": "must be one of the allowed values",
    "mapping_type": "must be an object",
    "model_type": "must be an object",
    "multiple_of": "must use the required increment",
    "non_finite_json_number": "must contain only finite JSON numbers",
    "set_type": "must be a set",
    "string_pattern_mismatch": "has invalid format",
    "string_too_long": "is too long",
    "string_too_short": "is too short",
    "string_type": "must be a string",
    "tuple_type": "must be a tuple",
    "union_tag_invalid": "has an unsupported discriminator",
    "union_tag_not_found": "is missing a discriminator",
    "url_parsing": "must be a valid URL",
    "url_scheme": "uses an unsupported URL scheme",
    "url_type": "must be a valid URL",
    "value_error": "is invalid",
}


def _stable_field_path(location: Iterable[Any]) -> str:
    """Render a Pydantic location without exposing its native tuple format."""

    path = ""
    for segment in location:
        if isinstance(segment, int) and not isinstance(segment, bool):
            path += f"[{segment}]"
            continue
        if not isinstance(segment, str) or segment in {"__root__", "root"}:
            continue
        path += f".{segment}" if path else segment
    return path or "$"


def pydantic_error_violations(error: ValidationError) -> list[FieldViolation]:
    """Translate Pydantic failures into stable, value-free BriefLoop violations.

    Native Pydantic messages, input values, context values, documentation URLs,
    and tuple ``loc`` formatting are intentionally not part of this contract.
    """

    violations = [
        FieldViolation(
            field=_stable_field_path(item.get("loc", ())),
            error=_PYDANTIC_ERROR_MESSAGES.get(str(item.get("type", "")), "is invalid"),
        )
        for item in error.errors(
            include_url=False, include_context=False, include_input=False
        )
    ]
    return sorted(violations, key=lambda item: (item.field, item.error, item.severity))
