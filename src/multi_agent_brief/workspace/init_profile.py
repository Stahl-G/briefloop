"""Workspace initialization profile domain model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InitProfile:
    interface_language: str = "zh-CN"
    output_language: str = "zh-CN"
    source_handling: str = "preserve_original"
    company: str = "Sample Company"
    role: str = "strategy_office"
    industry: str = ""
    industry_text: str = ""  # raw user description, preserved in user.md
    brief_title: str = "Weekly Industry Brief"
    audience: str = "management"
    audience_profile: str = ""  # mapped profile ID (management, research, ir, legal_compliance, default)
    focus_areas: list[str] = field(default_factory=lambda: ["policy", "competitor", "market", "customer_demand"])
    task_objective: str = ""  # free-text task description
    forbidden_sources: list[str] = field(default_factory=list)
    cadence: str = "weekly"
    max_source_age_days: int = 14
    selector_max_items: int = 20
    retrieval_enabled: bool = False
    retrieval_provider: str = "ollama"
    retrieval_model: str = "nomic-embed-text"
    output_formats: list[str] = field(
        default_factory=lambda: ["markdown", "docx", "claim_ledger", "audit_report", "source_appendix"]
    )
    source_profile: str = "llm_decide"
    source_decision_mode: str = "agent_decide"
    optional_seed_pack: str = ""  # registered pack key or empty
    tavily_enabled: bool = True  # legacy flag, kept for backward compatibility
    web_search_enabled: bool = True
    web_search_mode: str = "external_api"  # disabled, runtime_tool, external_api, configure_later
    search_backend: str = "tavily"  # tavily, exa, brave, firecrawl, serper (only when mode=external_api)
    initial_news_backfill_enabled: bool = False
    initial_news_backfill_days: int = 7
    initial_news_backfill_daily_max_results: int = 20
    preferred_news_domains: list[str] = field(default_factory=list)
    excluded_news_domains: list[str] = field(default_factory=list)
    competitor_module_enabled: bool = False
    competitor_names: list[str] = field(default_factory=list)
