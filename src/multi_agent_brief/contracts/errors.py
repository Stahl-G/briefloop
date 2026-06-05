"""Contract validation errors."""

from __future__ import annotations

from dataclasses import dataclass, field


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
