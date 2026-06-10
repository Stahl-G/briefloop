from __future__ import annotations

from multi_agent_brief.improvement.product_definition import (
    classify_improvement_source,
    classify_ledger_entry_materialization,
)


def test_product_definition_allows_human_feedback_guidance():
    decision = classify_improvement_source(
        source_type="human_feedback",
        issue_category="audience_mismatch",
    )

    assert decision.materializable is True
    assert decision.action == "allow"
    assert decision.classification == "audience_guidance"


def test_product_definition_allows_other_with_warning():
    decision = classify_improvement_source(
        source_type="human_feedback",
        issue_category="other",
    )

    assert decision.materializable is True
    assert decision.action == "warn_other"
    assert decision.reason_code == "category_other"


def test_product_definition_rejects_format_field_missing():
    decision = classify_improvement_source(
        source_type="feedback_issue",
        issue_category="formatting",
        finding_type="format_field_missing",
        source="audit",
        control_file="audit_report.json",
    )

    assert decision.materializable is False
    assert decision.action == "reject_with_rewrite_path"
    assert decision.classification == "repair_only"
    assert decision.reason_code == "repair_only_finding_type"


def test_product_definition_rejects_repair_only_categories():
    for category in ("unsupported_claim", "missing_source", "stale_source", "freshness_required"):
        decision = classify_improvement_source(
            source_type="feedback_issue",
            issue_category=category,
            source="audit",
        )

        assert decision.materializable is False
        assert decision.reason_code == "repair_only_issue_category"


def test_product_definition_rejects_audit_gate_target_relevance():
    for finding_type in ("target_relevance_failed", "target_relevance_gap", "target_priority_claim_missing_from_summary"):
        decision = classify_improvement_source(
            source_type="feedback_issue",
            issue_category="audience_mismatch",
            finding_type=finding_type,
            source="audit",
            control_file="output/intermediate/quality_gate_report.json",
        )

        assert decision.materializable is False
        assert decision.classification == "ambiguous_rewrite_required"
        assert decision.reason_code == "target_relevance_rewrite_required"


def test_product_definition_allows_human_rewritten_guidance_for_same_topic():
    decision = classify_improvement_source(
        source_type="human_feedback",
        issue_category="audience_mismatch",
        finding_type="target_relevance_failed",
        source="human",
        control_file="quality_gate_report.json",
    )

    assert decision.materializable is True
    assert decision.action == "allow"


def test_product_definition_rejects_legacy_feedback_issue_without_origin():
    decision = classify_ledger_entry_materialization({
        "status": "approved",
        "change": {
            "category": "audience_mismatch",
            "scope": "brief",
            "guidance_text": "Put the audience implication before methodology details.",
        },
        "source_evidence": [{
            "source_type": "feedback_issue",
            "summary": "Legacy feedback issue.",
            "run_id": "mabw-run-001",
            "issue_id": "fi-0001",
        }],
    })

    assert decision.materializable is False
    assert decision.requires_product_definition_review is True
    assert decision.reason_code == "missing_feedback_issue_product_definition_origin"
