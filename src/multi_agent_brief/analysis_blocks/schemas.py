"""Analysis Block schemas — epistemic presentation layer."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CaseApplicability:
    """Comparable case boundary metadata (PR 3 of v0.5.3)."""

    comparable_dimensions: list[str] = field(default_factory=list)
    non_comparable_dimensions: list[str] = field(default_factory=list)
    market_context: str = ""
    stage_context: str = ""
    price_band_or_audience_fit: str = ""
    local_verification_needed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisBlock:
    """One observation unit with epistemic classification.

    Groups claim IDs by their epistemic role so the renderer can
    present facts, cases, interpretations, limitations, and actions
    in a fixed structure instead of prose.
    """

    block_id: str
    title: str
    # Claim IDs by epistemic role
    fact_claim_ids: list[str] = field(default_factory=list)          # epistemic_type=observed, evidence_relation=direct
    case_claim_ids: list[str] = field(default_factory=list)          # evidence_relation=analogous
    interpretation_claim_ids: list[str] = field(default_factory=list) # epistemic_type=hypothesis or interpreted
    limitation_claim_ids: list[str] = field(default_factory=list)    # claims with limitations
    action_claim_ids: list[str] = field(default_factory=list)        # epistemic_type=action WITH direct evidence
    to_verify_claim_ids: list[str] = field(default_factory=list)     # action without evidence, or hypothesis
    # Metadata
    applicability_note: str = ""
    verification_path: str = ""
    confidence: float = 0.0
    # PR 3: comparable case boundary
    case_applicability: CaseApplicability | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.case_applicability:
            data["case_applicability"] = self.case_applicability.to_dict()
        return data
