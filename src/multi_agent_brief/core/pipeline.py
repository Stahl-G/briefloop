"""BriefPipeline — deterministic core pipeline."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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

logger = logging.getLogger(__name__)


class BriefPipeline:
    def __init__(self) -> None:
        self.agents = [
            ScoutAgent(),
            ScreenerAgent(),
            AnalystAgent(),
            EditorAgent(),
            AuditorAgent(),
            FormatterAgent(),
        ]

    def run(self, context: PipelineContext) -> list[AgentOutput]:
        ledger = ClaimLedger()
        context.metadata["_ledger"] = ledger  # expose for manifest/build_manifest
        outputs: list[AgentOutput] = []

        # Step 0: Source Collection — always via provider system
        source_output, collection_fatal = self._collect_sources(context)
        if source_output:
            outputs.append(source_output)
        if collection_fatal:
            return outputs  # abort — no Analyst/Editor/Auditor run

        # Step 1: Scout
        outputs.append(self.agents[0].run(context, ledger))

        # Source collection gate: if we expected sources but got 0 claims,
        # it's a quiet-week (pass, but flag it) — not a failure.
        # Only fail if source collection itself produced errors.
        if len(ledger) == 0 and context.sources:
            # Quiet week: sources collected, but no reportable claims extracted.
            pass  # Continue to Screener → Analyst (which will emit "No Reportable Signals")

        # Step 1.5: Entity & Event Enrichment (market-competitor only)
        _enrich_entities_if_configured(context, ledger)

        # Step 2: Screener
        outputs.append(self.agents[1].run(context, ledger))

        # Step 2.5: Analysis Modules (market-competitor, future earnings/policy/patent)
        module_outputs = self._run_analysis_modules(context, ledger)
        outputs.extend(module_outputs)

        # Step 3-4: Analyst → Editor
        outputs.append(self.agents[2].run(context, ledger))  # Analyst
        outputs.append(self.agents[3].run(context, ledger))  # Editor

        # Step 5: Auditor — may include specialist audit if MC module ran
        auditor_output = self._run_auditor(context, ledger)
        outputs.append(auditor_output)

        # Step 6: Formatter
        outputs.append(self.agents[5].run(context, ledger))
        return outputs

    def _collect_sources(
        self, context: PipelineContext,
    ) -> tuple[AgentOutput | None, bool]:
        """Collect sources via the provider system, populate context.sources.

        Returns (output, fatal) — when fatal=True the caller must abort the
        pipeline (no sources collected despite being enabled).
        """
        if context.sources:
            return None, False

        # Build SourceConfig from context if not already set
        source_config = context.metadata.get("source_config")
        if not source_config or not isinstance(source_config, SourceConfig):
            source_config = self._build_default_config(context)
            context.metadata["source_config"] = source_config

        # Create source plan
        plan = create_source_plan(
            industry=source_config.industry,
            report_date=context.report_date,
            recency_days=14 if context.max_source_age_days is None else context.max_source_age_days,
            enabled_providers=source_config.enabled_providers,
        )

        # Build query from plan — preserve individual tasks, don't collapse
        query = SourceQuery(
            recency_days=plan.recency_days,
            max_results=100,
        )
        # Pass report_date through for consistent recency filtering (B14)
        query.metadata["report_date"] = context.report_date

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

        # Enhance search tasks with source_discovery queries if available
        discovery = context.metadata.get("source_discovery")
        if discovery and "web_search" in source_config.enabled_providers:
            from multi_agent_brief.sources.decider import build_search_queries
            discovery_queries = build_search_queries(discovery)
            if discovery_queries:
                existing_tasks = source_config.web_search.get("search_tasks", [])
                existing_q = {t.get("query") for t in existing_tasks}
                for q in discovery_queries:
                    if q not in existing_q:
                        existing_tasks.append({"query": q, "domains": None})
                source_config.web_search["search_tasks"] = existing_tasks

        # Merge industry RSS feeds into config — only when rss is in
        # enabled_providers (B06): Industry Pack must not bypass user's
        # provider/profile choice.
        if plan.rss_feeds and not source_config.rss.get("feeds"):
            if "rss" in source_config.enabled_providers:
                source_config.rss["feeds"] = plan.rss_feeds
                source_config.rss["enabled"] = True

        # Competitor-aware search tasks — inject queries for each
        # primary competitor × dimension when competitor_universe.yaml
        # has non-empty entities and web_search is enabled.
        _inject_competitor_search_tasks(source_config, context)

        # Always include manual provider for local input/ directory
        if "manual" not in source_config.enabled_providers:
            source_config.enabled_providers.append("manual")
        input_dir = Path(context.input_dir)
        # Ensure Local Input Directory is present in manual sources.
        # Check by path/category, not by list emptiness — the list may already
        # contain URL entries from source discovery without a local input entry.
        manual_sources = source_config.manual.get("sources", [])
        has_local_input = any(
            s.get("category") == "local_files"
            and s.get("path") and Path(s["path"]).resolve() == input_dir.resolve()
            for s in manual_sources
        )
        if not has_local_input:
            source_config.manual["enabled"] = True
            source_config.manual.setdefault("sources", [])
            source_config.manual["sources"].append(
                {"name": "Local Input Directory", "path": str(input_dir.resolve()), "category": "local_files", "enabled": True}
            )

        # Collect from all providers
        items, collection_errors = collect_all_sources(source_config, query)

        # Populate context
        context.sources = items

        # Calculate source coverage report
        from multi_agent_brief.sources.coverage import calculate_coverage
        coverage_config = context.metadata.get("coverage_config", {})
        context.metadata["source_coverage"] = calculate_coverage(
            sources=items,
            config=coverage_config,
            report_date=context.report_date,
        )

        artifacts: dict = {
            "source_count": len(items),
            "providers": source_config.enabled_providers,
            "industry": source_config.industry,
            "plan_tasks": len(plan.search_tasks),
        }
        if collection_errors:
            artifacts["collection_errors"] = collection_errors

        # Failure gate: if web_search is enabled, fail loudly when no usable
        # sources are collected instead of silently continuing with an empty
        # Claim Ledger.  Only manual-only setups are allowed to proceed with
        # 0 sources (quiet-week).
        fatal = False
        web_search_cfg = source_config.web_search
        if (
            "web_search" in source_config.enabled_providers
            and web_search_cfg.get("enabled", False)
            and web_search_cfg.get("backend", "")
        ):
            # web_search is truly enabled with a configured backend.
            # Must have search_tasks and must produce usable sources.
            search_tasks = web_search_cfg.get("search_tasks", [])
            if not search_tasks:
                artifacts["collection_errors"] = artifacts.get("collection_errors", [])
                if isinstance(artifacts["collection_errors"], list):
                    artifacts["collection_errors"].append({
                        "provider": "web_search",
                        "error_type": "NoSearchTasks",
                        "message": (
                            "web_search is enabled and has a backend configured, "
                            "but no search_tasks are defined. "
                            "Add search_tasks in sources.yaml or run sources decide first."
                        ),
                    })
                fatal = True
            elif len(items) == 0:
                artifacts["collection_errors"] = artifacts.get("collection_errors", [])
                if isinstance(artifacts["collection_errors"], list):
                    artifacts["collection_errors"].append({
                        "provider": "web_search",
                        "error_type": "ZeroUsableSources",
                        "message": (
                            f"web_search executed {len(search_tasks)} task(s) but collected "
                            f"0 usable sources.  Check search backend configuration "
                            f"(TAVILY_API_KEY, EXA_API_KEY, etc.) or search task queries."
                        ),
                    })
                fatal = True

        return AgentOutput(
            agent_name="source-collection",
            summary=f"Collected {len(items)} sources from {len(source_config.enabled_providers)} providers.",
            artifacts=artifacts,
        ), fatal

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

    def _run_analysis_modules(
        self,
        context: PipelineContext,
        ledger: "ClaimLedger",
    ) -> list[AgentOutput]:
        """Run enabled analysis modules between Screener and Analyst."""
        from multi_agent_brief.analysis_modules.registry import load_enabled_modules

        # Load config from metadata
        config_dir = context.metadata.get("_config_dir", "")
        config: dict[str, Any] = {}
        if config_dir:
            config_path = Path(config_dir) / "config.yaml"
            if config_path.exists():
                try:
                    from multi_agent_brief.core.config import load_config
                    config = load_config(str(config_path))
                except Exception:
                    pass

        modules = load_enabled_modules(config)
        outputs: list[AgentOutput] = []
        for module in modules:
            try:
                result = module.analyze(context, ledger)
                context.metadata.setdefault("analysis_packs", {})
                context.metadata["analysis_packs"][module.name] = result
                outputs.append(AgentOutput(
                    agent_name=f"analysis-module-{module.name}",
                    summary=f"Module '{module.name}' completed with {len(result.artifacts)} artifacts.",
                    artifacts=result.to_dict(),
                ))
            except Exception as exc:
                outputs.append(AgentOutput(
                    agent_name=f"analysis-module-{module.name}",
                    summary=f"Module '{module.name}' FAILED: {type(exc).__name__}: {exc}",
                    artifacts={
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                    },
                ))
        return outputs

    def _run_auditor(
        self,
        context: PipelineContext,
        ledger: "ClaimLedger",
    ) -> AgentOutput:
        """Run auditor, injecting specialist auditor if MC module ran."""
        # Check if market_competitor module produced output
        analysis_packs = context.metadata.get("analysis_packs", {})
        has_mc = "market_competitor" in analysis_packs

        if has_mc:
            try:
                from multi_agent_brief.analysis_modules.market_competitor.auditor import (
                    MarketCompetitorAuditor,
                )
                mc_auditor = MarketCompetitorAuditor()
                # Create composite: deterministic + quality harness + MC specialist
                from multi_agent_brief.audit.deterministic import DeterministicAuditAgent
                from multi_agent_brief.audit.harness import QualityHarnessAuditAgent
                from multi_agent_brief.audit.interfaces import CompositeAuditAgent
                composite = CompositeAuditAgent(
                    DeterministicAuditAgent(),
                    additional_agents=[QualityHarnessAuditAgent(), mc_auditor],
                )
                report = composite.run_audit(
                    context.report_state.prepared_markdown, ledger, context,
                )
                context.report_state.audit_report = report
                return AgentOutput(
                    agent_name="auditor",
                    summary=f"Audit status: {report.audit_status}; findings: {len(report.findings)} (incl. MC specialist).",
                    artifacts={"audit_status": report.audit_status, "finding_count": len(report.findings)},
                )
            except Exception as exc:
                # Specialist auditor failed — log and fall back to default.
                logger.warning(
                    "MarketCompetitorAuditor failed (%s: %s). "
                    "Falling back to default audit.",
                    type(exc).__name__, exc,
                )
                # Still record the failure in metadata
                context.metadata.setdefault("analysis_packs", {})
                context.metadata["analysis_packs"].setdefault("market_competitor", {})
                context.metadata["analysis_packs"]["market_competitor"].setdefault(
                    "metadata", {},
                )["auditor_status"] = f"failed: {type(exc).__name__}"

        return self.agents[4].run(context, ledger)


# ── Module-level helpers ────────────────────────────────────────────────────


def _inject_competitor_search_tasks(
    source_config: SourceConfig,
    context: PipelineContext,
) -> None:
    """If competitor_universe.yaml has entities and web_search is enabled,
    inject competitor × dimension search tasks into the web_search config.
    """
    if "web_search" not in source_config.enabled_providers:
        return

    config_dir = context.metadata.get("_config_dir", "")
    if not config_dir:
        return

    universe_path = Path(config_dir) / "competitor_universe.yaml"
    if not universe_path.exists():
        return

    try:
        from multi_agent_brief.analysis_modules.market_competitor.config import (
            load_competitor_universe,
        )
        from multi_agent_brief.analysis_modules.market_competitor.enricher import (
            generate_competitor_search_tasks,
        )
        universe = load_competitor_universe(universe_path)
        if not universe.enabled or not universe.entities:
            return

        tasks = generate_competitor_search_tasks(universe)
        if not tasks:
            return

        existing_tasks = source_config.web_search.get("search_tasks", [])
        existing_q = {t.get("query") for t in existing_tasks}
        for task in tasks:
            if task["query"] not in existing_q:
                existing_tasks.append(task)
                existing_q.add(task["query"])
        source_config.web_search["search_tasks"] = existing_tasks
    except Exception:
        # If competitor_universe.yaml is malformed or dependencies missing,
        # silently skip — competitor search is an enhancement, not a requirement.
        pass


def _enrich_entities_if_configured(
    context: PipelineContext,
    ledger: "ClaimLedger",
) -> None:
    """Tag claims with entity/event metadata if competitor_universe.yaml exists."""
    config_dir = context.metadata.get("_config_dir", "")
    if not config_dir:
        return

    universe_path = Path(config_dir) / "competitor_universe.yaml"
    if not universe_path.exists():
        return

    try:
        from multi_agent_brief.analysis_modules.market_competitor.config import (
            load_competitor_universe,
        )
        from multi_agent_brief.analysis_modules.market_competitor.enricher import (
            EntityEventEnricher,
        )
        universe = load_competitor_universe(universe_path)
        if not universe.enabled or not universe.entities:
            return

        enricher = EntityEventEnricher(universe)
        claims = list(ledger)
        enricher.enrich(claims)
    except Exception:
        pass
