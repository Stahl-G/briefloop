"""Pluggable Analysis Module interface.

AnalysisModules run between Screener and Analyst in the pipeline.
They consume a screened ClaimLedger and produce structured artifacts
(analysis cards, events, coverage reports, etc.) — deterministically,
without external model calls.  LLM subagents consume the artifacts
later to generate the final human-readable analysis.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from multi_agent_brief.core.schemas import AuditFinding

if TYPE_CHECKING:
    from multi_agent_brief.core.claim_ledger import ClaimLedger
    from multi_agent_brief.core.schemas import PipelineContext


@dataclass
class ModuleOutput:
    """Output from a single AnalysisModule.analyze() call."""

    module_name: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    findings: list[AuditFinding] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_name": self.module_name,
            "artifacts": self.artifacts,
            "findings": [f.to_dict() for f in self.findings],
            "metadata": self.metadata,
        }


class AnalysisModule(ABC):
    """Abstract base class for pluggable analysis modules.

    Each module must provide:

    - ``name`` — unique string key (e.g. ``"market_competitor"``).
    - ``validate_config(config: dict) -> list[str]`` — return a list of
      human-readable config errors (empty = valid).
    - ``analyze(context, ledger) -> ModuleOutput`` — run the module's
      deterministic analysis pass.
    """

    name: str = ""

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate module-specific configuration.

        Args:
            config: The module's section from config.yaml's ``modules`` block
                    (e.g. ``config["modules"]["market_competitor"]``), or an
                    empty dict if not present.

        Returns:
            A list of error messages.  An empty list means "valid".
        """
        raise NotImplementedError

    @abstractmethod
    def analyze(
        self,
        context: "PipelineContext",
        ledger: "ClaimLedger",
    ) -> ModuleOutput:
        """Run the module's analysis on a screened ClaimLedger.

        Modules MUST NOT modify the ClaimLedger — only read it.
        Modules MUST NOT make external model/API calls — deterministic only.

        Args:
            context: The pipeline context containing sources, metadata, etc.
            ledger: The screened ClaimLedger (post-Screener).

        Returns:
            A ModuleOutput with artifacts, findings, and metadata.
        """
        raise NotImplementedError
