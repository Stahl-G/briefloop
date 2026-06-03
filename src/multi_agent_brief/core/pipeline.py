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
from multi_agent_brief.sources.registry import collect_all_sources


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

        # Step 0: Source Collection — always via provider system
        source_output = self._collect_sources(context)
        if source_output:
            outputs.append(source_output)

        for agent in self.agents:
            outputs.append(agent.run(context, ledger))
        return outputs

    def _collect_sources(self, context: PipelineContext) -> AgentOutput | None:
        """Collect sources via the provider system, populate context.sources."""
        if context.sources:
            return None

        # Build SourceConfig from context if not already set
        source_config = context.metadata.get("source_config")
        if not source_config or not isinstance(source_config, SourceConfig):
            source_config = self._build_default_config(context)
            context.metadata["source_config"] = source_config

        # Create source plan
        plan = create_source_plan(
            industry=source_config.industry,
            report_date=context.report_date,
            recency_days=context.max_source_age_days or 14,
            enabled_providers=source_config.enabled_providers,
        )

        # Build query from plan — preserve individual tasks, don't collapse
        query = SourceQuery(
            recency_days=plan.recency_days,
            max_results=100,
        )

        # Bridge planner search_tasks into web_search config as separate tasks
        if plan.search_tasks and "web_search" in source_config.enabled_providers:
            if not source_config.web_search.get("search_tasks"):
                source_config.web_search["search_tasks"] = [
                    {
                        "query": task.query,
                        "domains": task.source_domains or None,
                    }
                    for task in plan.search_tasks
                    if task.query
                ]

        # Merge industry RSS feeds into config
        if plan.rss_feeds and not source_config.rss.get("feeds"):
            source_config.rss["feeds"] = plan.rss_feeds
            source_config.rss["enabled"] = True
            if "rss" not in source_config.enabled_providers:
                source_config.enabled_providers.append("rss")

        # Always include manual provider for local input/ directory
        if "manual" not in source_config.enabled_providers:
            source_config.enabled_providers.append("manual")
        input_dir = Path(context.input_dir)
        if not source_config.manual.get("sources"):
            source_config.manual["enabled"] = True
            source_config.manual["sources"] = [
                {"name": "Local Input Directory", "path": str(input_dir), "category": "local_files", "enabled": True}
            ]

        # Collect from all providers
        items, collection_errors = collect_all_sources(source_config, query)

        # Populate context
        context.sources = items

        artifacts: dict = {
            "source_count": len(items),
            "providers": source_config.enabled_providers,
            "industry": source_config.industry,
            "plan_tasks": len(plan.search_tasks),
        }
        if collection_errors:
            artifacts["collection_errors"] = collection_errors

        return AgentOutput(
            agent_name="source-collection",
            summary=f"Collected {len(items)} sources from {len(source_config.enabled_providers)} providers.",
            artifacts=artifacts,
        )

    def _build_default_config(self, context: PipelineContext) -> SourceConfig:
        """Build a default SourceConfig when none is provided."""
        return SourceConfig(
            profile="research",
            enabled_providers=["manual"],
            manual={
                "enabled": True,
                "sources": [
                    {"name": "Local Input Directory", "path": context.input_dir, "category": "local_files", "enabled": True}
                ],
            },
            rss={"enabled": False, "feeds": []},
            web_search={"enabled": False},
        )
