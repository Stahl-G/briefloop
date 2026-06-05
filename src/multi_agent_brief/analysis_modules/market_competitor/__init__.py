"""Market & Competitor Intelligence Analysis Module.

Pluggable analysis module that transforms Claim Ledger entries into structured
competitive intelligence artifacts:
- MarketEvent — source-grounded competitor/market events
- AnalysisCard — multi-source analytical judgments (LLM-generated)
- CompetitorMatrix — entity × dimension comparison table
- CoverageReport — entity/dimension coverage gaps
- Watchlist — cross-period tracking items
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.analysis_modules.base import AnalysisModule, ModuleOutput
from multi_agent_brief.analysis_modules.market_competitor.config import (
    load_competitor_universe,
)
from multi_agent_brief.analysis_modules.market_competitor.event_builder import (
    build_events,
)
from multi_agent_brief.analysis_modules.market_competitor.renderer import render_all
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import PipelineContext


class MarketCompetitorModule(AnalysisModule):
    name = "market_competitor"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not isinstance(config, dict):
            errors.append("market_competitor config must be a dict")
            return errors
        return errors

    def analyze(self, context: PipelineContext, ledger: ClaimLedger) -> ModuleOutput:
        config_dir = context.metadata.get("_config_dir", "")
        output_dir = Path(context.output_dir)

        # Load universe
        universe_path = Path(config_dir) / "competitor_universe.yaml" if config_dir else None
        universe = None
        if universe_path and universe_path.exists():
            universe = load_competitor_universe(universe_path)

        if universe is None or not universe.enabled or not universe.entities:
            return ModuleOutput(
                module_name=self.name,
                metadata={"status": "disabled", "reason": "no_entities"},
            )

        # Build events from entity-tagged claims
        state_dir = str(Path(config_dir).parent / "state") if config_dir else None
        events = build_events(ledger, universe, state_dir=state_dir)

        # Render artifacts
        artifact_paths = render_all(events, ledger, universe, output_dir)

        return ModuleOutput(
            module_name=self.name,
            artifacts=artifact_paths,
            metadata={
                "event_count": len(events),
                "entity_count": len(universe.entities),
            },
        )
