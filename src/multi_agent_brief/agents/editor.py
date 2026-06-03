from __future__ import annotations

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.agents.draft_cleanup import clean_process_residue, strip_claim_citations
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext


class EditorAgent(BaseAgent):
    name = "editor"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        draft = context.report_state.draft_markdown
        # Remove process residue ([SRC:], [SOURCE:], empty [src:], Claude/Codex logs)
        cleaned = clean_process_residue(draft)
        # Strip [src:CLAIM_ID] citations — humans cannot read these internal references.
        # The claim_ledger.json preserves the full traceability; the reader-facing brief
        # should be clean prose.
        cleaned = strip_claim_citations(cleaned)
        # Audit status belongs in audit_report.json, not in the reader-facing brief.
        context.report_state.prepared_markdown = cleaned
        return AgentOutput(agent_name=self.name, summary="Cleaned draft Markdown for review.")
