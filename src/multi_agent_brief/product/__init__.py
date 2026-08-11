"""Product-layer report contracts and registries."""

from importlib import import_module


_EXPORTS = {
    "PolicyProfile": ("policy_profile", "PolicyProfile"),
    "PolicyProfileRegistry": ("policy_registry", "PolicyProfileRegistry"),
    "ReportPack": ("report_pack", "ReportPack"),
    "ReportPackRegistry": ("report_registry", "ReportPackRegistry"),
    "ReportSpecValidationResult": (
        "report_spec",
        "ReportSpecValidationResult",
    ),
    "aliases_for_report_pack": ("report_pack_aliases", "aliases_for_report_pack"),
    "load_report_spec": ("report_spec", "load_report_spec"),
    "recommended_entries_for_pack_ids": (
        "report_pack_aliases",
        "recommended_entries_for_pack_ids",
    ),
    "quality_panel_html_path": ("quality_panel", "quality_panel_html_path"),
    "quality_summary_path": ("quality_panel", "quality_summary_path"),
    "render_quality_panel_html": ("quality_panel", "render_quality_panel_html"),
    "render_quality_summary": ("quality_panel", "render_quality_summary"),
    "resolve_report_pack_id": ("report_pack_aliases", "resolve_report_pack_id"),
    "validate_quality_panel_html": (
        "quality_panel",
        "validate_quality_panel_html",
    ),
    "validate_quality_summary_markdown": (
        "quality_panel",
        "validate_quality_summary_markdown",
    ),
    "validate_policy_profile_payload": (
        "policy_profile",
        "validate_policy_profile_payload",
    ),
    "validate_report_pack_payload": ("report_pack", "validate_report_pack_payload"),
    "validate_report_spec_payload": (
        "report_spec",
        "validate_report_spec_payload",
    ),
    "write_quality_panel_html": ("quality_panel", "write_quality_panel_html"),
    "write_quality_summary": ("quality_panel", "write_quality_summary"),
}

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


def __getattr__(name: str):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
