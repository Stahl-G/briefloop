"""Citation profile resolution for reader delivery and audit trace surfaces.

Profiles describe how much citation detail belongs in reader delivery versus
audit/control artifacts. They do not prove support, approve delivery, or weaken
the audit trace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

CITATION_PROFILE_SCHEMA_VERSION = "briefloop.citation_profile.v1"
CITATION_PROFILE_BOUNDARY = "reader_citation_profile_split_only"
CITATION_PROFILE_RUNTIME_EFFECT = "citation_profile_resolution_only"
VALID_CITATION_PROFILES = {"executive", "analyst", "audit"}
DEFAULT_CITATION_PROFILE = "executive"

_PROFILE_DETAILS: dict[str, dict[str, Any]] = {
    "executive": {
        "reader_citation_style": "source_label",
        "reader_metadata_level": "low_interference",
        "audit_trace_level": "complete_when_available",
        "delivery_exposes_internal_ids": False,
        "delivery_exposes_local_paths": False,
        "audit_bundle_keeps_trace": True,
    },
    "analyst": {
        "reader_citation_style": "source_label",
        "reader_metadata_level": "moderate_source_metadata",
        "audit_trace_level": "complete_when_available",
        "delivery_exposes_internal_ids": False,
        "delivery_exposes_local_paths": False,
        "audit_bundle_keeps_trace": True,
    },
    "audit": {
        "reader_citation_style": "source_label",
        "reader_metadata_level": "reader_safe_source_metadata",
        "audit_trace_level": "complete_claim_source_span_hash_trace",
        "delivery_exposes_internal_ids": False,
        "delivery_exposes_local_paths": False,
        "audit_bundle_keeps_trace": True,
    },
}


def normalize_citation_profile(value: Any) -> str:
    raw = value.strip().lower().replace("-", "_") if isinstance(value, str) else ""
    return raw if raw in VALID_CITATION_PROFILES else ""


def citation_profile_report(
    *,
    profile: str,
    source: str,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    normalized = normalize_citation_profile(profile) or DEFAULT_CITATION_PROFILE
    details = dict(_PROFILE_DETAILS[normalized])
    return {
        "schema_version": CITATION_PROFILE_SCHEMA_VERSION,
        "boundary": CITATION_PROFILE_BOUNDARY,
        "runtime_effect": CITATION_PROFILE_RUNTIME_EFFECT,
        "profile": normalized,
        "source": source,
        "warnings": list(warnings or []),
        **details,
        "non_goals": [
            "semantic_support_proof",
            "delivery_approval",
            "release_authority",
            "audit_trace_removal",
        ],
    }


def resolve_workspace_citation_profile(
    workspace: str | Path,
    *,
    source_appendix_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a workspace citation profile without writing state."""

    warnings: list[str] = []
    config = source_appendix_config if isinstance(source_appendix_config, Mapping) else {}
    config_profile = normalize_citation_profile(config.get("citation_profile"))
    if config.get("citation_profile") and not config_profile:
        warnings.append("Invalid source_appendix.citation_profile ignored.")
    if config_profile:
        return citation_profile_report(
            profile=config_profile,
            source="config.output.source_appendix.citation_profile",
            warnings=warnings,
        )

    from multi_agent_brief.product.template_projection import project_workspace_report_template

    template = project_workspace_report_template(workspace)
    reader_contract = template.get("reader_contract") if isinstance(template, Mapping) else {}
    template_profile = (
        normalize_citation_profile(reader_contract.get("citation_profile"))
        if isinstance(reader_contract, Mapping)
        else ""
    )
    if template_profile:
        return citation_profile_report(
            profile=template_profile,
            source="report_template.reader_contract.citation_profile",
            warnings=warnings,
        )

    return citation_profile_report(
        profile=DEFAULT_CITATION_PROFILE,
        source="default",
        warnings=warnings,
    )


def validate_citation_profile_report(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return "citation_profile_schema_error:not_object"
    if payload.get("schema_version") != CITATION_PROFILE_SCHEMA_VERSION:
        return "citation_profile_schema_error:schema_version"
    if payload.get("boundary") != CITATION_PROFILE_BOUNDARY:
        return "citation_profile_schema_error:boundary"
    if payload.get("runtime_effect") != CITATION_PROFILE_RUNTIME_EFFECT:
        return "citation_profile_schema_error:runtime_effect"
    if payload.get("profile") not in VALID_CITATION_PROFILES:
        return "citation_profile_schema_error:profile"
    for field in ("source", "reader_citation_style", "reader_metadata_level", "audit_trace_level"):
        if not isinstance(payload.get(field), str) or not payload.get(field).strip():
            return f"citation_profile_schema_error:{field}"
    for field in ("delivery_exposes_internal_ids", "delivery_exposes_local_paths", "audit_bundle_keeps_trace"):
        if not isinstance(payload.get(field), bool):
            return f"citation_profile_schema_error:{field}"
    if payload.get("delivery_exposes_internal_ids") is not False:
        return "citation_profile_schema_error:delivery_exposes_internal_ids"
    if payload.get("delivery_exposes_local_paths") is not False:
        return "citation_profile_schema_error:delivery_exposes_local_paths"
    if not isinstance(payload.get("warnings"), list):
        return "citation_profile_schema_error:warnings"
    non_goals = payload.get("non_goals")
    if not isinstance(non_goals, list) or "audit_trace_removal" not in {str(item) for item in non_goals}:
        return "citation_profile_schema_error:non_goals"
    return None
