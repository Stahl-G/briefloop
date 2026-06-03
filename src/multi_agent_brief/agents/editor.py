from __future__ import annotations

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext


class EditorAgent(BaseAgent):
    name = "editor"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        draft = context.report_state.draft_markdown
        # Audit status belongs in audit_report.json, not in the reader-facing brief.
        context.report_state.final_markdown = draft
        return AgentOutput(agent_name=self.name, summary="Prepared final Markdown brief.")
