"""Product-definition guardrails for Improvement Ledger materialization.

This module is deliberately side-effect free. It separates audience guidance
from deterministic correctness, delivery, and repair findings before PR3 can
materialize approved ledger entries into runtime memory.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


AUDIENCE_GUIDANCE_ISSUE_CATEGORIES = {
    "audience_mismatch",
    "tone",
    "structure",
    "length",
    "source_presentation",
}

REPAIR_ONLY_ISSUE_CATEGORIES = {
    "unsupported_claim",
    "unsupported_affiliation_claim",
    "missing_source",
    "stale_source",
    "freshness_required",
    "metric_definition_missing",
    "format_field_missing",
    "source_link_format_noncompliant",
}

REPAIR_ONLY_FINDING_TYPES = set(REPAIR_ONLY_ISSUE_CATEGORIES)

TARGET_RELEVANCE_FINDING_TYPES = {
    "target_relevance",
    "target_relevance_failed",
    "target_relevance_gap",
    "target_priority_claim_missing_from_summary",
}

GATE_OR_AUDIT_SOURCES = {"audit", "gate", "quality_gate"}
QUALITY_GATE_CONTROL_FILES = {"quality_gate_report.json"}


@dataclass(frozen=True)
class ProductDefinitionDecision:
    guidance_eligible: bool
    action: str
    classification: str
    reason_code: str
    message: str
    materializable: bool
    requires_product_definition_review: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_improvement_source(
    *,
    source_type: str,
    issue_category: str | None = None,
    finding_type: str | None = None,
    source: str | None = None,
    control_file: str | None = None,
) -> ProductDefinitionDecision:
    """Classify whether an improvement source can become audience guidance."""
    source_type_value = _normalize(source_type)
    issue_category_value = _normalize(issue_category)
    finding_type_value = _normalize(finding_type)
    source_value = _normalize(source)
    control_file_value = _basename(control_file)

    if source_type_value == "human_feedback":
        if issue_category_value == "other":
            return _warn_other()
        return _allow("human_feedback_guidance", "Human-authored guidance is eligible after ledger hygiene and approval.")

    if source_type_value != "feedback_issue":
        return _reject_rewrite(
            "unknown_source_type",
            "Only human_feedback and feedback_issue can source audience guidance proposals.",
            classification="ambiguous_rewrite_required",
        )

    if issue_category_value in REPAIR_ONLY_ISSUE_CATEGORIES:
        return _reject_rewrite(
            "repair_only_issue_category",
            "This feedback issue category is machine-checkable or repair-only; use feedback/repair for this run, or rewrite a persistent preference as explicit human_feedback guidance with --source-summary.",
            classification="repair_only",
        )

    if finding_type_value in REPAIR_ONLY_FINDING_TYPES:
        return _reject_rewrite(
            "repair_only_finding_type",
            "This finding type is machine-checkable or repair-only; use feedback/repair for this run, or rewrite a persistent preference as explicit human_feedback guidance with --source-summary.",
            classification="repair_only",
        )

    if (
        finding_type_value in TARGET_RELEVANCE_FINDING_TYPES
        and (
            source_value in GATE_OR_AUDIT_SOURCES
            or control_file_value in QUALITY_GATE_CONTROL_FILES
        )
    ):
        return _reject_rewrite(
            "target_relevance_rewrite_required",
            "Audit/gate target relevance findings mix coverage defects with possible audience preference. Use feedback/repair for this run; if there is a durable reader preference, rewrite it as explicit human_feedback guidance with --source-summary.",
            classification="ambiguous_rewrite_required",
        )

    if issue_category_value == "other":
        return _warn_other()

    if issue_category_value in AUDIENCE_GUIDANCE_ISSUE_CATEGORIES:
        return _allow("audience_feedback_issue", "Audience feedback issue can be used as evidence because the guidance text is still human-authored.")

    if source_value == "human":
        return _allow("human_feedback_issue", "Human feedback issue can be used as evidence because the guidance text is still human-authored.")

    return _reject_rewrite(
        "issue_requires_human_rewrite",
        "This feedback issue is not clearly audience guidance. Use feedback/repair for this run, or rewrite a persistent preference as explicit human_feedback guidance with --source-summary.",
        classification="ambiguous_rewrite_required",
    )


def classify_ledger_entry_materialization(entry: dict[str, Any]) -> ProductDefinitionDecision:
    """Classify a current ledger entry for future PR3 materialization."""
    change = entry.get("change") if isinstance(entry.get("change"), dict) else {}
    category = str(change.get("category") or "")
    evidence = entry.get("source_evidence") or []
    if not isinstance(evidence, list) or not evidence:
        return _reject_rewrite(
            "missing_source_evidence",
            "Entry has no source evidence and cannot be materialized safely.",
            classification="ambiguous_rewrite_required",
        )

    saw_warning: ProductDefinitionDecision | None = None
    for item in evidence:
        if not isinstance(item, dict):
            return _reject_rewrite(
                "invalid_source_evidence",
                "Entry source evidence is invalid and cannot be materialized safely.",
                classification="ambiguous_rewrite_required",
            )
        origin = item.get("origin") if isinstance(item.get("origin"), dict) else {}
        source_type = _normalize(str(item.get("source_type") or ""))
        if source_type == "feedback_issue" and not _has_feedback_issue_product_definition_origin(origin):
            return _reject_rewrite(
                "missing_feedback_issue_product_definition_origin",
                "FeedbackIssue evidence needs product-definition origin metadata before materialization; review and rewrite as human_feedback guidance if this is a durable audience preference.",
                classification="ambiguous_rewrite_required",
            )
        decision = classify_improvement_source(
            source_type=source_type,
            issue_category=str(origin.get("issue_category") or (category if source_type == "human_feedback" else "") or ""),
            finding_type=str(origin.get("finding_type") or ""),
            source=str(origin.get("issue_source") or ""),
            control_file=str(origin.get("control_file") or ""),
        )
        if not decision.materializable:
            return decision
        if decision.action == "warn_other":
            saw_warning = decision
    return saw_warning or _allow("materializable_guidance", "Approved guidance is eligible for PR3 materialization.")


def _allow(reason_code: str, message: str) -> ProductDefinitionDecision:
    return ProductDefinitionDecision(
        guidance_eligible=True,
        action="allow",
        classification="audience_guidance",
        reason_code=reason_code,
        message=message,
        materializable=True,
        requires_product_definition_review=False,
    )


def _warn_other() -> ProductDefinitionDecision:
    return ProductDefinitionDecision(
        guidance_eligible=True,
        action="warn_other",
        classification="audience_guidance",
        reason_code="category_other",
        message="category=other is allowed but should be reclassified when possible; it remains materializable after human approval.",
        materializable=True,
        requires_product_definition_review=False,
    )


def _reject_rewrite(reason_code: str, message: str, *, classification: str) -> ProductDefinitionDecision:
    return ProductDefinitionDecision(
        guidance_eligible=False,
        action="reject_with_rewrite_path",
        classification=classification,
        reason_code=reason_code,
        message=message,
        materializable=False,
        requires_product_definition_review=True,
    )


def _normalize(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace("-", "_")


def _basename(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return Path(text).name.strip().lower()


def _has_feedback_issue_product_definition_origin(origin: dict[str, Any]) -> bool:
    issue_category = _normalize(str(origin.get("issue_category") or ""))
    issue_source = _normalize(str(origin.get("issue_source") or ""))
    finding_type = _normalize(str(origin.get("finding_type") or ""))
    return bool(issue_category and (issue_source or finding_type))
