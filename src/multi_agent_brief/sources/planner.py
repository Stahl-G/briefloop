"""Source Planner: decides what to search based on context.

Two planning modes:
- Static: industry_packs.py — fallback for CI/testing with known industries
- Runtime: LLM-generated SearchPlan — production path for any industry
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from multi_agent_brief.sources.industry_packs import get_industry_pack

# ── Runtime Search Plan schema ──────────────────────────────────────────────


@dataclass
class RuntimeSearchPlan:
    """LLM-generated search plan for a single prepare run.

    The plan is produced by a subagent (not the Python layer) and validated
    deterministically before execution.  Each task becomes a separate search
    API call.
    """

    company: str = ""
    industry: str = ""  # free-text, not a slug
    objective: str = ""
    focus_areas: list[str] = field(default_factory=list)
    report_date: str = ""
    recency_days: int = 7
    tasks: list[dict[str, Any]] = field(default_factory=list)

    def to_search_task_dicts(self) -> list[dict[str, str]]:
        """Convert plan into the format expected by web_search.search_tasks."""
        results: list[dict[str, str]] = []
        for t in self.tasks:
            q = t.get("query", "").strip()
            if not q:
                continue
            entry: dict[str, str] = {"query": q}
            domains = t.get("domains")
            if domains:
                entry["domains"] = (
                    ",".join(domains) if isinstance(domains, list) else str(domains)
                )
            results.append(entry)
        return results


def validate_runtime_search_plan(plan: RuntimeSearchPlan) -> list[str]:
    """Deterministic validator for LLM-generated SearchPlan.

    Returns a list of human-readable error messages.  Empty list = valid.
    """
    errors: list[str] = []

    # Minimum 3 tasks for a meaningful search
    if len(plan.tasks) < 3:
        errors.append(
            f"SearchPlan has {len(plan.tasks)} task(s) — at least 3 are required "
            f"for meaningful source coverage."
        )

    # Every task must have a non-empty query
    for i, t in enumerate(plan.tasks):
        q = t.get("query", "").strip()
        if not q:
            errors.append(f"SearchPlan task {i} has an empty query.")

    # No duplicate queries (after normalization)
    seen: set[str] = set()
    for i, t in enumerate(plan.tasks):
        q = t.get("query", "").strip().lower()
        if q and q in seen:
            errors.append(f"SearchPlan task {i} duplicates query '{t.get('query', '')}'.")
        if q:
            seen.add(q)

    # Coverage: at least one task should cover company + industry + policy
    all_queries = " ".join(t.get("query", "") for t in plan.tasks).lower()
    if plan.company and plan.company.lower() not in all_queries:
        errors.append(
            f"SearchPlan has no query mentioning company '{plan.company}'."
        )
    if plan.industry and plan.industry.lower() not in all_queries:
        errors.append(
            f"SearchPlan has no query mentioning industry '{plan.industry}'."
        )

    # Recency sanity
    if plan.recency_days < 1 or plan.recency_days > 90:
        errors.append(
            f"SearchPlan recency_days={plan.recency_days} is out of range [1, 90]."
        )

    return errors


# ── Legacy SourcePlan (static industry packs) ───────────────────────────────


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
    """Plan for what sources to collect (static industry-pack based)."""

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
