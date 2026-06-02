from __future__ import annotations

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext


class AuditorAgent(BaseAgent):
    name = "auditor"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        report = run_deterministic_audit(context.report_state.draft_markdown, ledger)
        context.report_state.audit_report = report
        return AgentOutput(
            agent_name=self.name,
            summary=f"Audit status: {report.audit_status}; findings: {len(report.findings)}.",
            artifacts={"audit_status": report.audit_status, "finding_count": len(report.findings)},
        )

