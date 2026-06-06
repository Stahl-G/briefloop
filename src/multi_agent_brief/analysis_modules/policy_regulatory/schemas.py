"""Data structures for Policy & Regulatory Risk Module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class PolicyEvent:
    """A single policy or regulatory event."""

    event_id: str
    jurisdiction: str
    authority: str
    instrument_name: str
    publication_date: str = ""
    effective_date: str = ""
    affected_entities: list[str] = field(default_factory=list)
    core_change: str = ""
    compliance_deadline: str = ""
    source_refs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    epistemic_type: str = "FACT"  # FACT, HYPOTHESIS, TO_VERIFY

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "jurisdiction": self.jurisdiction,
            "authority": self.authority,
            "instrument_name": self.instrument_name,
            "publication_date": self.publication_date,
            "effective_date": self.effective_date,
            "affected_entities": self.affected_entities,
            "core_change": self.core_change,
            "compliance_deadline": self.compliance_deadline,
            "source_refs": self.source_refs,
            "limitations": self.limitations,
            "epistemic_type": self.epistemic_type,
        }


@dataclass
class RiskItem:
    """A single risk item in the risk register."""

    risk_id: str
    event_id: str
    risk_type: str  # compliance, operational, market_access, reputational
    severity: str  # low, medium, high, critical
    likelihood: str  # unlikely, possible, likely, certain
    affected_entities: list[str] = field(default_factory=list)
    mitigation_notes: str = ""
    applicability_status: str = "TO_VERIFY"  # CONFIRMED, TO_VERIFY, HYPOTHESIS
    source_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_id": self.risk_id,
            "event_id": self.event_id,
            "risk_type": self.risk_type,
            "severity": self.severity,
            "likelihood": self.likelihood,
            "affected_entities": self.affected_entities,
            "mitigation_notes": self.mitigation_notes,
            "applicability_status": self.applicability_status,
            "source_refs": self.source_refs,
        }


@dataclass
class ApplicabilityQuestion:
    """An open question about applicability of a policy event."""

    question_id: str
    event_id: str
    question: str
    context: str = ""
    priority: str = "medium"  # low, medium, high

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "event_id": self.event_id,
            "question": self.question,
            "context": self.context,
            "priority": self.priority,
        }


@dataclass
class PolicyEvidencePack:
    """Evidence pack for policy & regulatory analysis."""

    events: list[PolicyEvent] = field(default_factory=list)
    risks: list[RiskItem] = field(default_factory=list)
    applicability_questions: list[ApplicabilityQuestion] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [e.to_dict() for e in self.events],
            "risks": [r.to_dict() for r in self.risks],
            "applicability_questions": [q.to_dict() for q in self.applicability_questions],
            "metadata": self.metadata,
        }


@dataclass
class PolicyCoverageReport:
    """Coverage report for policy & regulatory analysis."""

    total_events: int = 0
    jurisdictions_covered: list[str] = field(default_factory=list)
    authorities_covered: list[str] = field(default_factory=list)
    risk_types_covered: list[str] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "jurisdictions_covered": self.jurisdictions_covered,
            "authorities_covered": self.authorities_covered,
            "risk_types_covered": self.risk_types_covered,
            "coverage_gaps": self.coverage_gaps,
            "metadata": self.metadata,
        }
