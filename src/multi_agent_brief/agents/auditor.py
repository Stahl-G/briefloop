from __future__ import annotations

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.audit.deterministic import DeterministicAuditAgent
from multi_agent_brief.audit.final_quality import FinalQualityAuditAgent
from multi_agent_brief.audit.harness import QualityHarnessAuditAgent
from multi_agent_brief.audit.interfaces import AuditAgentInterface, CompositeAuditAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext


class AuditorAgent(BaseAgent):
    name = "auditor"

    def __init__(self, audit_agent: AuditAgentInterface | None = None) -> None:
        self.audit_agent = audit_agent or CompositeAuditAgent(
            DeterministicAuditAgent(),
            additional_agents=[
                QualityHarnessAuditAgent(),
                FinalQualityAuditAgent(),
            ],
        )

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        report = self.audit_agent.run_audit(context.report_state.prepared_markdown, ledger, context)
        context.report_state.audit_report = report

        # Store final quality report in metadata
        final_quality_findings = [f for f in report.findings if f.finding_id.startswith("FINAL_")]
        if final_quality_findings:
            from multi_agent_brief.audit.interfaces import AuditReport, recompute_report_status
            fq_report = AuditReport(audit_status="pass", audit_score=100)
            fq_report.findings = final_quality_findings
            recompute_report_status(fq_report)
            fq_status = fq_report.audit_status
        else:
            fq_status = "pass"
        final_quality_report = {
            "audit_status": fq_status,
            "findings_count": len(final_quality_findings),
        }
        context.metadata["final_quality_report"] = final_quality_report

        # Persist final_quality_status into audit report metadata for audit_report.json
        report.metadata["final_quality_status"] = fq_status
        report.metadata["final_quality_findings_count"] = len(final_quality_findings)

        return AgentOutput(
            agent_name=self.name,
            summary=f"Audit status: {report.audit_status}; findings: {len(report.findings)}.",
            artifacts={
                "audit_status": report.audit_status,
                "finding_count": len(report.findings),
                "final_quality_status": final_quality_report["audit_status"],
            },
        )
