"""Strict DTOs for agent-rollout evaluation.

The case schema deliberately carries no command, script, or shell field, so
the red line "Do not execute arbitrary shell strings from evaluation fixtures"
is structurally unviolable rather than merely observed.

Blocking expectation is likewise not a free-floating assertion: ``must_block``
is a derived read-only property computed from the seeded defects'
``expected_blocking_level`` values, because real annotated corpora contain
warning-only cases (an unblocked case carrying warning-level findings) that a
boolean input field cannot express honestly.  Payloads therefore must not carry
a ``must_block`` key; the schema rejects it as an extra field.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

FINDING_TYPES = frozenset(
    {
        "claim_support_matrix_blocking_support",
        "number_without_source",
        "stale_source",
        "target_priority_claim_missing_from_summary",
        "target_relevance_gap",
        "final_incomplete_key_case_fields",
        "final_missing_comparison_basis",
        "final_missing_limitation_section",
        "final_scope_title_mismatch",
        "final_unsupported_superlative",
    }
)

EVOLVABLE_ROLES = ("scout", "screener", "claim-ledger", "auditor", "editor")

FindingType: TypeAlias = Literal[
    "claim_support_matrix_blocking_support",
    "number_without_source",
    "stale_source",
    "target_priority_claim_missing_from_summary",
    "target_relevance_gap",
    "final_incomplete_key_case_fields",
    "final_missing_comparison_basis",
    "final_missing_limitation_section",
    "final_scope_title_mismatch",
    "final_unsupported_superlative",
]

BlockingLevel: TypeAlias = Literal["blocking", "warning"]

_ISO_DATE_PATTERN = r"\d{4}-\d{2}-\d{2}"

_NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _iso_date(value: str) -> str:
    """Validate a YYYY-MM-DD string (strict mode never coerces str to date)."""
    if re.fullmatch(_ISO_DATE_PATTERN, value) is None:
        raise ValueError("invalid date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid date") from exc
    return value


ReportDate = Annotated[str, AfterValidator(_iso_date)]


class _Strict(BaseModel):
    """No coercion, no undeclared fields, no mutation."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class SeededDefect(_Strict):
    """One deliberately planted defect with a known location.

    ``expected_blocking_level`` states whether the ground-truth finding is
    supposed to block delivery or only surface as a warning; case-level
    ``must_block`` is derived from these values, never asserted separately.
    """

    defect_id: _NonBlank
    finding_type: FindingType
    locator: _NonBlank
    expected_blocking_level: BlockingLevel


class RolloutSpec(_Strict):
    """Which role to run, on which runtime."""

    role: Literal["scout", "screener", "claim-ledger", "auditor", "editor"]
    runtime: Literal["codex"]


class EvaluationCase(_Strict):
    """One scored task instance.

    ``seeded_defects`` and ``clean_claims`` may each be empty at the DTO
    level; corpus-level composition invariants are enforced elsewhere.
    """

    case_id: _NonBlank
    synthetic: Literal[True]
    source_pack: _NonBlank
    report_date: ReportDate
    rollout: RolloutSpec
    seeded_defects: list[SeededDefect] = Field(default_factory=list)
    clean_claims: list[_NonBlank] = Field(default_factory=list)

    @model_validator(mode="after")
    def _defect_ids_unique(self) -> "EvaluationCase":
        ids = [defect.defect_id for defect in self.seeded_defects]
        if len(ids) != len(set(ids)):
            raise ValueError("defect_id values must be unique within a case")
        return self

    @property
    def must_block(self) -> bool:
        """Derived: the case must block iff any seeded defect is blocking."""
        return any(
            defect.expected_blocking_level == "blocking"
            for defect in self.seeded_defects
        )


class ReportedFinding(_Strict):
    """One finding a rollout actually reported."""

    finding_type: FindingType
    locator: _NonBlank
    blocking_level: BlockingLevel


class RolloutOutcome(_Strict):
    """What one rollout actually reported for one case."""

    case_id: _NonBlank
    found_defect_ids: list[_NonBlank] = Field(default_factory=list)
    flagged_claim_locators: list[_NonBlank] = Field(default_factory=list)
    blocked: bool
    findings: list[ReportedFinding] = Field(default_factory=list)


class CorpusScore(_Strict):
    """The reward and the counts it was computed from."""

    defect_recall: float
    true_negative_rate: float
    reward: float
    seeded_total: int
    seeded_detected: int
    clean_total: int
    clean_flagged: int
    block_agreement: float
    case_count: int
