from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

# Re-export SourceItem from the canonical location (sources.base)
from multi_agent_brief.sources.base import SourceItem  # noqa: F401


ClaimType = Literal["fact", "number", "date", "interpretation", "forecast", "risk"]
Confidence = Literal["low", "medium", "high"]
AuditSeverity = Literal["low", "medium", "high"]
AuditStatus = Literal["pass", "warning", "fail"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class CandidateItem:
    item_id: str
    title: str
    summary: str
    source_id: str
    topic: str = "general"
    importance: str = "medium"
    reason_for_inclusion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Claim:
    claim_id: str
    statement: str
    source_id: str
    evidence_text: str
    source_url: str = ""
    source_type: str = "local_file"
    claim_type: ClaimType = "fact"
    confidence: Confidence = "medium"
    requires_audit: bool = True
    created_by: str = "scout"
    used_in_sections: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        return cls(**data)


@dataclass
class BriefSection:
    title: str
    body: str
    claim_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditFinding:
    finding_id: str
    severity: AuditSeverity
    finding_type: str
    description: str
    recommendation: str = ""
    related_claim_id: str = ""
    line_number: int | None = None
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditReport:
    audit_status: AuditStatus
    audit_score: int
    findings: list[AuditFinding] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [finding.to_dict() for finding in self.findings]
        return data


@dataclass
class AgentOutput:
    agent_name: str
    summary: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReportState:
    draft_markdown: str = ""
    prepared_markdown: str = ""
    sections: list[BriefSection] = field(default_factory=list)
    audit_report: AuditReport | None = None


@dataclass
class PipelineContext:
    project_name: str
    input_dir: str
    output_dir: str
    language: str = "en-US"
    audience: str = "management"
    report_date: str = ""
    max_source_age_days: int | None = None
    fail_on_stale_source: bool = False
    previous_report_dir: str = ""
    previous_report_text: str = ""
    max_claims: int = 160
    quiet_week_min_claims: int = 5
    sources: list[SourceItem] = field(default_factory=list)
    candidates: list[CandidateItem] = field(default_factory=list)
    report_state: ReportState = field(default_factory=ReportState)
    output_formats: list[str] = field(default_factory=lambda: ["markdown"])
    output_footer: str = ""
    output_filename_template: str = ""
    output_filename_tokens: dict[str, str] = field(default_factory=dict)
    output_named_outputs: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
