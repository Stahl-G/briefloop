"""Product-layer report contracts and registries."""

from multi_agent_brief.product.policy_profile import PolicyProfile, validate_policy_profile_payload
from multi_agent_brief.product.policy_registry import PolicyProfileRegistry
from multi_agent_brief.product.report_pack_aliases import (
    aliases_for_report_pack,
    recommended_entries_for_pack_ids,
    resolve_report_pack_id,
)
from multi_agent_brief.product.report_pack import ReportPack, validate_report_pack_payload
from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import (
    ReportSpecValidationResult,
    load_report_spec,
    validate_report_spec_payload,
)
from multi_agent_brief.product.quality_panel import (
    quality_panel_html_path,
    quality_summary_path,
    render_quality_panel_html,
    render_quality_summary,
    validate_quality_panel_html,
    validate_quality_summary_markdown,
    write_quality_panel_html,
    write_quality_summary,
)

__all__ = [
    "PolicyProfile",
    "PolicyProfileRegistry",
    "ReportPack",
    "ReportPackRegistry",
    "ReportSpecValidationResult",
    "aliases_for_report_pack",
    "load_report_spec",
    "recommended_entries_for_pack_ids",
    "quality_panel_html_path",
    "quality_summary_path",
    "render_quality_panel_html",
    "render_quality_summary",
    "resolve_report_pack_id",
    "validate_quality_panel_html",
    "validate_quality_summary_markdown",
    "validate_policy_profile_payload",
    "validate_report_pack_payload",
    "validate_report_spec_payload",
    "write_quality_panel_html",
    "write_quality_summary",
]
