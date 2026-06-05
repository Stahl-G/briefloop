"""Rule Packs — maps finding_type → (blocking_level, repair_owner).

Every user-facing audit finding type must have an entry here.
The CI gate (check_rule_packs.py) enforces completeness.
"""

from __future__ import annotations

from typing import Literal

BlockingLevel = Literal[
    "editor_fixable", "analyst_blocking", "source_blocking",
    "configuration_error", "rendering_error", "safety_blocking",
]
RepairOwner = Literal["editor", "analyst", "source", "configuration", "rendering", "safety"]

# finding_type → (blocking_level, repair_owner, short_description)
RULE_PACK: dict[str, tuple[BlockingLevel, RepairOwner, str]] = {
    # ── Deterministic audit findings ──
    "missing_claim":              ("editor_fixable",     "editor",      "Orphan [src:ID] reference to non-existent claim"),
    "number_without_source":      ("analyst_blocking",   "analyst",     "Number-like value without source reference"),
    "missing_source":             ("source_blocking",    "source",      "Claim missing source_id or evidence_text"),
    "duplicate_claim":            ("editor_fixable",     "editor",      "Duplicate claim statements"),
    "missing_source_date":        ("source_blocking",    "source",      "Claim source missing published_at date"),
    "stale_source":               ("source_blocking",    "source",      "Source date exceeds reporting window"),
    "redaction_risk":             ("safety_blocking",    "safety",      "Potential PII or sensitive data detected"),

    # ── Epistemic gate findings (Claim Schema v2) ──
    "hypothesis_high_confidence": ("analyst_blocking",   "analyst",     "Hypothesis presented as high-confidence fact"),
    "action_without_basis":       ("analyst_blocking",   "analyst",     "Action claim lacks applicability rationale"),
    "analogy_without_limitations":("analyst_blocking",   "analyst",     "Analogy claim has no stated limitations"),
    "analogy_direct_relation":    ("analyst_blocking",   "analyst",     "Analogy uses direct instead of indirect evidence"),

    # ── Quality harness findings ──
    "no_reportable_claims":       ("source_blocking",    "source",      "Pipeline produced brief with zero claims"),
    "placeholder_text":           ("editor_fixable",     "editor",      "Draft contains placeholder text (TBD, etc.)"),
    "internal_process_term":      ("editor_fixable",     "editor",      "Draft contains internal workflow terminology"),
    "step_label_residue":         ("editor_fixable",     "editor",      "Draft contains process step labels"),
    "compilation_residue":        ("editor_fixable",     "editor",      "Draft contains audit/generation metadata residue"),
    "unsupported_certainty":      ("analyst_blocking",   "analyst",     "High-certainty wording without date/support"),
    "investment_advice_language": ("safety_blocking",    "safety",      "Investment-advice style language in brief"),
    "needs_recrawl_claim_used":   ("source_blocking",    "source",      "A needs_recrawl claim is cited"),
    "low_confidence_source_used": ("source_blocking",    "source",      "A T5/low-confidence source is cited"),
    "low_source_density":         ("analyst_blocking",   "analyst",     "Many numbers lack source coverage"),
    "possible_eia_unit_inflation":("analyst_blocking",   "analyst",     "EIA generation value may be unit-inflated"),
    "repeat_claim_in_summary":    ("editor_fixable",     "editor",      "Repeated claim appears in executive summary"),
    "stale_filler_language":      ("editor_fixable",     "editor",      "Multiple stale/no-update filler phrases"),
}


def get_taxonomy(finding_type: str) -> tuple[BlockingLevel, RepairOwner]:
    """Look up blocking_level and repair_owner for a finding_type.

    Returns defaults ("editor_fixable", "editor") for unknown types.
    """
    entry = RULE_PACK.get(finding_type)
    if entry:
        return entry[0], entry[1]
    return "editor_fixable", "editor"


def tag_finding(finding_type: str) -> dict[str, str]:
    """Return blocking_level and repair_owner for a finding_type as a dict."""
    level, owner = get_taxonomy(finding_type)
    return {"blocking_level": level, "repair_owner": owner}


def list_uncovered_types(known_types: set[str]) -> list[str]:
    """Return finding types in known_types not covered by RULE_PACK."""
    return sorted(known_types - set(RULE_PACK.keys()))
