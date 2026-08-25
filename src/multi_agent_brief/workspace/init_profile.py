"""Workspace initialization profile domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from multi_agent_brief.contracts.v2 import (
    GATE_ID_VALUES,
    RunDirection,
    RunExecutionAuthorizationBootstrap,
    WorkspaceControlStoreBootstrapV2,
)
from multi_agent_brief.core_run_v2.output_contract import (
    BODY_LENGTH_BASIS,
    BODY_LENGTH_UNIT,
    OUTPUT_EXTENT_CATALOG_ID,
    resolve_output_extent,
)


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
    report_type: str | None = None
    audience: str = "management"
    audience_profile: str = (
        ""  # mapped profile ID (management, research, ir, legal_compliance, default)
    )
    focus_areas: list[str] = field(
        default_factory=lambda: ["policy", "competitor", "market", "customer_demand"]
    )
    task_objective: str = ""  # free-text task description
    forbidden_sources: list[str] = field(default_factory=list)
    # Reader-skeleton contract from the report pack; frozen into RunDirection
    # so the deterministic section gate reads Store state only.
    required_section_intents: list[str] = field(default_factory=list)
    cadence: str = "weekly"
    max_source_age_days: int = 14
    selector_max_items: int = 20
    retrieval_enabled: bool = False
    retrieval_provider: str = "ollama"
    retrieval_model: str = "nomic-embed-text"
    output_formats: list[str] = field(
        default_factory=lambda: [
            "markdown",
            "docx",
            "claim_ledger",
            "audit_report",
            "source_appendix",
        ]
    )
    source_profile: str = "llm_decide"
    source_decision_mode: str = "agent_decide"
    optional_seed_pack: str = ""  # registered pack key or empty
    tavily_enabled: bool = False  # legacy flag, kept for backward compatibility
    web_search_enabled: bool = True
    web_search_mode: str = (
        "configure_later"  # disabled, runtime_tool, external_api, configure_later
    )
    search_backend: str = (
        ""  # tavily, exa, brave, firecrawl, serper (only when mode=external_api)
    )
    initial_news_backfill_enabled: bool = False
    initial_news_backfill_days: int = 7
    initial_news_backfill_daily_max_results: int = 20
    preferred_news_domains: list[str] = field(default_factory=list)
    excluded_news_domains: list[str] = field(default_factory=list)
    competitor_module_enabled: bool = False
    competitor_names: list[str] = field(default_factory=list)
    output_extent: str | None = None


def _ordered_unique_nonempty(values: list[str], *, field_name: str) -> list[str]:
    normalized = [value.strip() for value in values if value.strip()]
    if not normalized or len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must be non-empty, ordered and unique")
    return normalized


def _unique_nonempty(values: list[str], *, field_name: str) -> list[str]:
    normalized = [value.strip() for value in values if value.strip()]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must be unique")
    return normalized


def build_controlstore_bootstrap(
    profile: InitProfile,
    *,
    workspace_id: str,
    run_id: str,
    report_date: date,
    execution_authorization: RunExecutionAuthorizationBootstrap | None = None,
) -> WorkspaceControlStoreBootstrapV2:
    """Map one validated init profile into the exact fresh-v2 bootstrap."""

    focus_areas = _ordered_unique_nonempty(
        profile.focus_areas,
        field_name="focus_areas",
    )
    output_formats = _ordered_unique_nonempty(
        profile.output_formats,
        field_name="output_formats",
    )
    forbidden_sources = [
        value.strip() for value in profile.forbidden_sources if value.strip()
    ]
    if len(forbidden_sources) != len(set(forbidden_sources)):
        raise ValueError("forbidden_sources must be ordered and unique")
    task_objective = profile.task_objective.strip()
    if not task_objective:
        raise ValueError("task_objective is required for ControlStore v2")
    industry_or_theme = (
        profile.industry_text.strip() or profile.industry.strip() or None
    )
    search_backend = (
        profile.search_backend.strip()
        if profile.web_search_mode == "external_api"
        else None
    )
    output_contract = None
    if profile.output_extent is not None:
        resolved = resolve_output_extent(
            profile.output_extent,
            profile.output_language,
        )
        output_contract = {
            "schema_version": "briefloop.run_output_contract.v2",
            "output_extent": resolved.output_extent,
            "extent_catalog_id": OUTPUT_EXTENT_CATALOG_ID,
            "body_length_basis": BODY_LENGTH_BASIS,
            "body_length_unit": BODY_LENGTH_UNIT,
            "resolved_minimum": resolved.resolved_minimum,
            "resolved_maximum": resolved.resolved_maximum,
        }
    report_window_start = report_date - timedelta(days=profile.max_source_age_days)
    direction = RunDirection.model_validate(
        {
            "schema_version": RunDirection.schema_id,
            "subject_name": profile.company.strip(),
            "industry_or_theme": industry_or_theme,
            "brief_title": profile.brief_title.strip(),
            "report_type": (
                profile.report_type.strip()
                if profile.report_type is not None and profile.report_type.strip()
                else None
            ),
            "task_objective": task_objective,
            "audience": profile.audience.strip(),
            "audience_profile": (
                profile.audience_profile.strip() or profile.audience.strip()
            ),
            "output_language": profile.output_language.strip(),
            "source_handling": profile.source_handling.strip(),
            "cadence": profile.cadence.strip(),
            "focus_areas": focus_areas,
            "excluded_topics": [],
            "forbidden_sources": forbidden_sources,
            "source_profile": profile.source_profile.strip(),
            "web_search_mode": profile.web_search_mode,
            "search_backend": search_backend,
            "output_style": None,
            "output_formats": output_formats,
            "report_date": report_date.isoformat(),
            "report_window_start": report_window_start.isoformat(),
            "report_window_end": report_date.isoformat(),
            "max_source_age_days": profile.max_source_age_days,
            "selector_max_items": profile.selector_max_items,
            "target_terms": list(focus_areas),
            "required_section_intents": _unique_nonempty(
                profile.required_section_intents,
                field_name="required_section_intents",
            ),
            "output_contract": output_contract,
        }
    )
    return WorkspaceControlStoreBootstrapV2.model_validate(
        {
            "schema_version": WorkspaceControlStoreBootstrapV2.schema_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "runtime": "codex",
            "role_topology": "single_session",
            "input_governance_required": True,
            "gate_strictness": {gate_id: True for gate_id in GATE_ID_VALUES},
            "run_direction": direction.model_dump(mode="json", exclude_unset=False),
            "execution_authorization": (
                None
                if execution_authorization is None
                else execution_authorization.model_dump(
                    mode="json", exclude_unset=False
                )
            ),
        }
    )
