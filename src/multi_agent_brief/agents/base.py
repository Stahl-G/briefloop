from __future__ import annotations

from abc import ABC, abstractmethod

from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext


class BaseAgent(ABC):
    name = "base"

    @abstractmethod
    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        raise NotImplementedError

