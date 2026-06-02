from __future__ import annotations

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext


class EditorAgent(BaseAgent):
    name = "editor"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        draft = context.report_state.draft_markdown
        audit_report = context.report_state.audit_report
        if audit_report and audit_report.audit_status == "fail":
            final = draft + "\n\n> Audit status: fail. Review high-severity findings before distribution.\n"
        elif audit_report and audit_report.audit_status == "warning":
            final = draft + "\n\n> Audit status: pass with warnings. Review audit report for details.\n"
        else:
            final = draft
        context.report_state.final_markdown = final
        return AgentOutput(agent_name=self.name, summary="Prepared final Markdown brief.")

