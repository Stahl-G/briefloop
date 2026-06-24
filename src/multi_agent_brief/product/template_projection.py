"""Read-only ReportTemplate projection for product-layer workspace surfaces."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from multi_agent_brief.product.policy_registry import PolicyProfileRegistry
from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import (
    ReportSpecLoadError,
    load_report_spec,
    validate_report_spec_payload,
)
from multi_agent_brief.product.template_registry import ReportTemplateRegistry

REPORT_TEMPLATE_PROJECTION_BOUNDARY = "product_report_template_projection_only"


def project_workspace_report_template(workspace: str | Path) -> dict[str, Any]:
    """Resolve the product ReportTemplate for a workspace without side effects."""

    ws = Path(workspace)
    spec_path = ws / "report_spec.yaml"
    if not spec_path.exists():
        return {
            "status": "not_available",
            "boundary": REPORT_TEMPLATE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "reason": "report_spec_missing",
        }

    try:
        spec_payload = load_report_spec(spec_path)
    except (OSError, ReportSpecLoadError) as exc:
        return {
            "status": "invalid_report_spec",
            "boundary": REPORT_TEMPLATE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "report_spec_path": "report_spec.yaml",
            "errors": [{"field": "report_spec.yaml", "error": str(exc)}],
        }

    policy_registry = PolicyProfileRegistry.from_package()
    report_registry = ReportPackRegistry.from_package()
    validation = validate_report_spec_payload(
        spec_payload,
        known_report_packs=report_registry.pack_ids(),
        report_type_by_pack=report_registry.report_type_by_pack(),
        known_policy_profiles=policy_registry.profile_ids(),
        default_policy_profile_by_pack=report_registry.default_policy_profile_by_pack(),
    )
    if not validation.ok:
        return {
            "status": "invalid_report_spec",
            "boundary": REPORT_TEMPLATE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "report_spec_path": "report_spec.yaml",
            "report_pack": validation.report_pack,
            "report_type": validation.report_type,
            "errors": [_violation_to_dict(item) for item in validation.errors],
        }

    template_registry = ReportTemplateRegistry.from_package()
    if template_registry.validation_errors:
        return {
            "status": "invalid_template_registry",
            "boundary": REPORT_TEMPLATE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "report_spec_path": "report_spec.yaml",
            "report_pack": validation.report_pack,
            "report_type": validation.report_type,
            "errors": list(template_registry.validation_errors),
        }

    template = template_registry.get_by_report_type(validation.report_type or "")
    if template is None:
        return {
            "status": "not_resolved",
            "boundary": REPORT_TEMPLATE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "report_spec_path": "report_spec.yaml",
            "report_pack": validation.report_pack,
            "report_type": validation.report_type,
            "reason": f"template_missing_for_report_type:{validation.report_type or '<missing>'}",
        }

    return {
        "status": "resolved",
        "boundary": REPORT_TEMPLATE_PROJECTION_BOUNDARY,
        "runtime_effect": "none",
        "report_spec_path": "report_spec.yaml",
        "report_pack": validation.report_pack,
        "report_type": validation.report_type,
        "template_id": template.template_id,
        "display_name": template.display_name,
        "source": "packaged_report_template",
        "section_order": list(template.section_order),
        "section_aliases": {
            key: list(value)
            for key, value in template.section_aliases.items()
        },
        "section_count": len(template.section_order),
        "template_sha256": _sha256_file(Path(template.source_path)),
    }


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _violation_to_dict(violation: Any) -> dict[str, str]:
    return {
        "field": str(getattr(violation, "field", "")),
        "error": str(getattr(violation, "error", "")),
        "severity": str(getattr(violation, "severity", "error")),
    }
