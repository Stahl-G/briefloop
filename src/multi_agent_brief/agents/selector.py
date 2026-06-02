from __future__ import annotations

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.previous import load_previous_report_baseline
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext
from multi_agent_brief.core.selection import ledger_from_selected, select_reportable_claims


class ScreencerAgent(BaseAgent):
    name = "screencer"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        previous_text = context.previous_report_text
        previous_names: list[str] = []
        if not previous_text and context.previous_report_dir:
            previous_text, previous_names = load_previous_report_baseline(
                context.previous_report_dir,
                context.report_date,
            )
            context.previous_report_text = previous_text

        result = select_reportable_claims(ledger, context, previous_text)
        selected_ledger = ledger_from_selected(result)
        ledger._claims = selected_ledger._claims
        context.metadata["selection"] = result.stats
        context.metadata["selection_excluded"] = result.excluded
        context.metadata["previous_report_names"] = previous_names
        return AgentOutput(
            agent_name=self.name,
            summary=(
                f"Selected {result.stats['selected_claims']} of {result.stats['input_claims']} claims "
                f"({result.stats['excluded_claims']} excluded)."
            ),
            artifacts=result.stats,
        )

