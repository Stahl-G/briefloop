from __future__ import annotations

from pathlib import Path

from multi_agent_brief.agents.analyst import AnalystAgent
from multi_agent_brief.agents.auditor import AuditorAgent
from multi_agent_brief.agents.editor import EditorAgent
from multi_agent_brief.agents.formatter import FormatterAgent
from multi_agent_brief.agents.scout import ScoutAgent, load_local_sources
from multi_agent_brief.agents.selector import ScreenerAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, PipelineContext
from multi_agent_brief.sources.base import SourceConfig, SourceQuery
from multi_agent_brief.sources.planner import create_source_plan
from multi_agent_brief.sources.registry import collect_all_sources, load_sources_config


class BriefPipeline:
    def __init__(self) -> None:
        self.agents = [
            ScoutAgent(),
            ScreenerAgent(),
            AnalystAgent(),
            AuditorAgent(),
            EditorAgent(),
            FormatterAgent(),
        ]

    def run(self, context: PipelineContext) -> list[AgentOutput]:
        ledger = ClaimLedger()
        outputs: list[AgentOutput] = []

        # Step 0: Source Collection (before Scout)
        source_output = self._collect_sources(context)
        if source_output:
            outputs.append(source_output)

        for agent in self.agents:
            outputs.append(agent.run(context, ledger))
        return outputs

    def _collect_sources(self, context: PipelineContext) -> AgentOutput | None:
        """Collect sources from providers or local files, populate context.sources.

        Priority:
        1. If context.sources already populated → skip (pre-loaded)
        2. If source_config available → use provider system (planner + providers)
        3. Fallback → load from input_dir (backward compatible)
        """
        # Already have sources? Skip.
        if context.sources:
            return None

        # Try provider-based collection
        source_config = context.metadata.get("source_config")
        if source_config and isinstance(source_config, SourceConfig):
            return self._collect_from_providers(context, source_config)

        # Fallback: load local files (backward compatible)
        return self._collect_from_local(context)

    def _collect_from_providers(self, context: PipelineContext, source_config: SourceConfig) -> AgentOutput:
        """Collect sources using the provider system with optional SourcePlanner."""
        # Create source plan from config
        plan = create_source_plan(
            industry=source_config.industry,
            report_date=context.report_date,
            recency_days=context.max_source_age_days or 14,
            enabled_providers=source_config.enabled_providers,
        )

        # Build query from plan
        query = SourceQuery(
            keywords=[task.query for task in plan.search_tasks],
            recency_days=plan.recency_days,
            max_results=100,
        )

        # Merge industry RSS feeds into config if available
        if plan.rss_feeds and not source_config.rss.get("feeds"):
            source_config.rss["feeds"] = plan.rss_feeds
            source_config.rss["enabled"] = True
            if "rss" not in source_config.enabled_providers:
                source_config.enabled_providers.append("rss")

        # Collect from all providers
        items = collect_all_sources(source_config, query)

        # Also load local input files (always available)
        input_dir = Path(context.input_dir)
        if input_dir.exists():
            try:
                local_items = load_local_sources(input_dir)
                items.extend(local_items)
            except Exception:
                pass

        # Populate context
        # Convert sources.base.SourceItem to core.schemas.SourceItem (same class now)
        context.sources = items

        return AgentOutput(
            agent_name="source-collection",
            summary=f"Collected {len(items)} sources from {len(source_config.enabled_providers)} providers.",
            artifacts={
                "source_count": len(items),
                "providers": source_config.enabled_providers,
                "industry": source_config.industry,
                "plan_tasks": len(plan.search_tasks),
            },
        )

    def _collect_from_local(self, context: PipelineContext) -> AgentOutput:
        """Fallback: load sources from local input directory."""
        input_dir = Path(context.input_dir)
        try:
            sources = load_local_sources(input_dir)
        except FileNotFoundError:
            sources = []
        context.sources = sources
        return AgentOutput(
            agent_name="source-collection",
            summary=f"Loaded {len(sources)} local sources from {context.input_dir}.",
            artifacts={"source_count": len(sources), "mode": "local_only"},
        )
