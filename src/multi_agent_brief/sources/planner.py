"""Source Planner: decides what to search based on industry, role, and time window."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from multi_agent_brief.sources.industry_packs import get_industry_pack


@dataclass
class SearchTask:
    """A single search task produced by the planner."""

    task_id: str
    query: str
    source_domains: list[str] = field(default_factory=list)
    topic: str = "general"
    priority: str = "medium"  # high | medium | low
    max_results: int = 10


@dataclass
class SourcePlan:
    """Plan for what sources to collect."""

    industry: str
    role: str
    report_date: str
    recency_days: int = 7
    search_tasks: list[SearchTask] = field(default_factory=list)
    enabled_providers: list[str] = field(default_factory=list)
    rss_feeds: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def create_source_plan(
    *,
    industry: str = "",
    role: str = "",
    report_date: str = "",
    recency_days: int = 7,
    enabled_providers: list[str] | None = None,
    extra_keywords: list[str] | None = None,
) -> SourcePlan:
    """Create a source plan based on industry and role context.

    This is deterministic in the MVP — no LLM calls.
    """
    providers = enabled_providers or ["manual"]
    plan = SourcePlan(
        industry=industry,
        role=role,
        report_date=report_date,
        recency_days=recency_days,
        enabled_providers=providers,
    )

    # Load industry pack if available
    pack = get_industry_pack(industry) if industry else None
    if pack:
        plan.rss_feeds = list(pack.get("rss_feeds", []))
        for i, task_def in enumerate(pack.get("search_tasks", [])):
            plan.search_tasks.append(
                SearchTask(
                    task_id=f"{industry}_{i:03d}",
                    query=task_def.get("query", ""),
                    source_domains=task_def.get("domains", []),
                    topic=task_def.get("topic", "general"),
                    priority=task_def.get("priority", "medium"),
                )
            )

    # Add extra keywords as additional search tasks
    if extra_keywords:
        plan.search_tasks.append(
            SearchTask(
                task_id=f"extra_{len(plan.search_tasks):03d}",
                query=" ".join(extra_keywords),
                topic="general",
                priority="medium",
            )
        )

    return plan
