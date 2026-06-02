from __future__ import annotations

from multi_agent_brief.agents.analyst import AnalystAgent
from multi_agent_brief.agents.auditor import AuditorAgent
from multi_agent_brief.agents.editor import EditorAgent
from multi_agent_brief.agents.formatter import FormatterAgent
from multi_agent_brief.agents.scout import ScoutAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext


class BriefPipeline:
    def __init__(self) -> None:
        self.agents = [
            ScoutAgent(),
            AnalystAgent(),
            AuditorAgent(),
            EditorAgent(),
            FormatterAgent(),
        ]

    def run(self, context: PipelineContext) -> list[AgentOutput]:
        ledger = ClaimLedger()
        outputs: list[AgentOutput] = []
        for agent in self.agents:
            outputs.append(agent.run(context, ledger))
        return outputs

