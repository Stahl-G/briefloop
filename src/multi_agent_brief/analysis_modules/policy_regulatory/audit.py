"""Audit checks for Policy & Regulatory Risk Module."""

from __future__ import annotations

from typing import Any

from multi_agent_brief.analysis_modules.policy_regulatory.schemas import (
    PolicyEvent,
    RiskItem,
    PolicyEvidencePack,
)
from multi_agent_brief.core.schemas import AuditFinding


def audit_policy_events(
    evidence_pack: PolicyEvidencePack,
    config: dict[str, Any] | None = None,
) -> list[AuditFinding]:
    """Run audit checks on policy events and risks.

    Args:
        evidence_pack: The policy evidence pack to audit.
        config: Optional audit configuration with thresholds.

    Returns:
        List of audit findings.
    """
    findings: list[AuditFinding] = []
    config = config or {}

    # Check each event
    for event in evidence_pack.events:
        findings.extend(_audit_single_event(event, config))

    # Check each risk
    for risk in evidence_pack.risks:
        findings.extend(_audit_single_risk(risk, evidence_pack.events, config))

    # Check applicability questions
    findings.extend(_audit_applicability_questions(evidence_pack, config))

    return findings


def _audit_single_event(
    event: PolicyEvent,
    config: dict[str, Any],
) -> list[AuditFinding]:
    """Audit a single policy event."""
    findings: list[AuditFinding] = []

    # Check: official source missing
    if not event.source_refs:
        findings.append(AuditFinding(
            finding_id=f"POLICY_SOURCE_MISSING_{event.event_id}",
            finding_type="policy_regulatory",
            severity="warning",
            description=f"Policy event '{event.instrument_name}' has no source references.",
            blocking_level="editor_fixable",
            repair_owner="source",
        ))

    # Check: effective date missing
    if not event.effective_date:
        findings.append(AuditFinding(
            finding_id=f"POLICY_EFFECTIVE_DATE_MISSING_{event.event_id}",
            finding_type="policy_regulatory",
            severity="warning",
            description=f"Policy event '{event.instrument_name}' has no effective date.",
            blocking_level="editor_fixable",
            repair_owner="analyst",
        ))

    # Check: jurisdiction missing
    if not event.jurisdiction:
        findings.append(AuditFinding(
            finding_id=f"POLICY_JURISDICTION_MISSING_{event.event_id}",
            finding_type="policy_regulatory",
            severity="high",
            description=f"Policy event '{event.instrument_name}' has no jurisdiction.",
            blocking_level="analyst_blocking",
            repair_owner="analyst",
        ))

    # Check: stale regulatory framing
    if event.effective_date:
        try:
            from datetime import date
            effective = date.fromisoformat(event.effective_date)
            today = date.today()
            if effective < today:
                # Event is already in effect - check if it's being presented as new
                if event.epistemic_type == "TO_VERIFY":
                    findings.append(AuditFinding(
                        finding_id=f"POLICY_STALE_FRAMING_{event.event_id}",
                        finding_type="policy_regulatory",
                        severity="warning",
                        description=(
                            f"Policy event '{event.instrument_name}' is already in effect "
                            f"(since {event.effective_date}) but marked as TO_VERIFY."
                        ),
                        blocking_level="editor_fixable",
                        repair_owner="analyst",
                    ))
        except ValueError:
            pass  # Invalid date format - skip check

    return findings


def _audit_single_risk(
    risk: RiskItem,
    events: list[PolicyEvent],
    config: dict[str, Any],
) -> list[AuditFinding]:
    """Audit a single risk item."""
    findings: list[AuditFinding] = []

    # Find the related event
    related_event = None
    for event in events:
        if event.event_id == risk.event_id:
            related_event = event
            break

    # Check: applicability overclaim
    if risk.applicability_status == "CONFIRMED" and related_event:
        if related_event.epistemic_type in ("HYPOTHESIS", "TO_VERIFY"):
            findings.append(AuditFinding(
                finding_id=f"POLICY_APPLICABILITY_OVERCLAIM_{risk.risk_id}",
                finding_type="policy_regulatory",
                severity="high",
                description=(
                    f"Risk '{risk.risk_id}' claims CONFIRMED applicability but "
                    f"related event '{risk.event_id}' is {related_event.epistemic_type}."
                ),
                blocking_level="analyst_blocking",
                repair_owner="analyst",
            ))

    # Check: compliance advice without basis
    if risk.risk_type == "compliance" and risk.mitigation_notes:
        if not risk.source_refs and not (related_event and related_event.source_refs):
            findings.append(AuditFinding(
                finding_id=f"POLICY_COMPLIANCE_ADVICE_NO_BASIS_{risk.risk_id}",
                finding_type="policy_regulatory",
                severity="high",
                description=(
                    f"Compliance risk '{risk.risk_id}' provides mitigation notes "
                    f"but has no source references."
                ),
                blocking_level="analyst_blocking",
                repair_owner="analyst",
            ))

    # Check: critical risk without confirmation
    if risk.severity == "critical" and risk.applicability_status != "CONFIRMED":
        findings.append(AuditFinding(
            finding_id=f"POLICY_CRITICAL_UNCONFIRMED_{risk.risk_id}",
            finding_type="policy_regulatory",
            severity="warning",
            description=(
                f"Risk '{risk.risk_id}' is marked as critical but "
                f"applicability is {risk.applicability_status}, not CONFIRMED."
            ),
            blocking_level="editor_fixable",
            repair_owner="analyst",
        ))

    return findings


def _audit_applicability_questions(
    evidence_pack: PolicyEvidencePack,
    config: dict[str, Any],
) -> list[AuditFinding]:
    """Audit applicability questions."""
    findings: list[AuditFinding] = []

    # Check: high priority questions without answers
    for question in evidence_pack.applicability_questions:
        if question.priority == "high":
            findings.append(AuditFinding(
                finding_id=f"POLICY_OPEN_QUESTION_{question.question_id}",
                finding_type="policy_regulatory",
                severity="low",
                description=(
                    f"High-priority applicability question remains open: "
                    f"{question.question}"
                ),
                blocking_level="editor_fixable",
                repair_owner="analyst",
            ))

    return findings
