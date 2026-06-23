"""Read-only PolicyProfile projection for product-layer workspace surfaces."""

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

POLICY_PROFILE_PROJECTION_BOUNDARY = "product_policy_profile_projection_only"


def project_workspace_policy_profile(workspace: str | Path) -> dict[str, Any]:
    """Resolve the product PolicyProfile for a workspace without side effects."""

    ws = Path(workspace)
    spec_path = ws / "report_spec.yaml"
    if not spec_path.exists():
        return {
            "status": "not_available",
            "boundary": POLICY_PROFILE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "reason": "report_spec_missing",
        }

    try:
        spec_payload = load_report_spec(spec_path)
    except (OSError, ReportSpecLoadError) as exc:
        return {
            "status": "invalid_report_spec",
            "boundary": POLICY_PROFILE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "report_spec_path": "report_spec.yaml",
            "errors": [{"field": "report_spec.yaml", "error": str(exc)}],
        }

    policy_registry = PolicyProfileRegistry.from_package()
    report_registry = ReportPackRegistry.from_package()
    if policy_registry.validation_errors:
        return {
            "status": "invalid_policy_registry",
            "boundary": POLICY_PROFILE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "report_spec_path": "report_spec.yaml",
            "errors": [_violation_to_dict(item) for item in policy_registry.validation_errors],
        }

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
            "boundary": POLICY_PROFILE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "report_spec_path": "report_spec.yaml",
            "report_pack": validation.report_pack,
            "policy_profile": validation.policy_profile,
            "resolved_policy_profile": validation.resolved_policy_profile,
            "source": validation.policy_profile_source,
            "policy_profile_resolution": validation.policy_profile_resolution,
            "errors": [_violation_to_dict(item) for item in validation.errors],
        }

    resolved = validation.resolved_policy_profile
    if not resolved:
        return {
            "status": "not_resolved",
            "boundary": POLICY_PROFILE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "report_spec_path": "report_spec.yaml",
            "report_pack": validation.report_pack,
            "policy_profile": validation.policy_profile,
            "source": validation.policy_profile_source,
            "policy_profile_resolution": validation.policy_profile_resolution,
        }

    profile = policy_registry.get(resolved)
    if profile is None:
        return {
            "status": "unknown_policy_profile",
            "boundary": POLICY_PROFILE_PROJECTION_BOUNDARY,
            "runtime_effect": "none",
            "report_spec_path": "report_spec.yaml",
            "report_pack": validation.report_pack,
            "policy_profile": validation.policy_profile,
            "resolved_policy_profile": resolved,
        }

    return {
        "status": "resolved",
        "boundary": POLICY_PROFILE_PROJECTION_BOUNDARY,
        "runtime_effect": "none",
        "report_spec_path": "report_spec.yaml",
        "report_pack": validation.report_pack,
        "policy_profile": validation.policy_profile,
        "resolved_policy_profile": resolved,
        "source": (
            validation.policy_profile_source
            or ("report_spec.policy_profile" if validation.policy_profile else "report_pack.default_policy_profile")
        ),
        "policy_profile_resolution": validation.policy_profile_resolution,
        "policy_profile_sha256": _sha256_file(Path(profile.source_path)),
        "profile": _profile_summary(dict(profile.payload)),
    }


def _profile_summary(payload: dict[str, Any]) -> dict[str, Any]:
    source_policy = payload.get("source_policy") if isinstance(payload.get("source_policy"), dict) else {}
    claim_policy = payload.get("claim_policy") if isinstance(payload.get("claim_policy"), dict) else {}
    wording_policy = payload.get("wording_policy") if isinstance(payload.get("wording_policy"), dict) else {}
    gate_policy = payload.get("gate_policy") if isinstance(payload.get("gate_policy"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    forbidden_phrases = wording_policy.get("forbidden_phrases")
    return {
        "policy_profile_id": payload.get("policy_profile_id"),
        "industry": payload.get("industry"),
        "source_policy": {
            "freshness_days_by_tier": dict(source_policy.get("freshness_days_by_tier") or {}),
            "preferred_source_tiers": list(source_policy.get("preferred_source_tiers") or []),
            "discouraged_source_tiers": list(source_policy.get("discouraged_source_tiers") or []),
        },
        "claim_policy": {
            "materiality_terms": list(claim_policy.get("materiality_terms") or []),
        },
        "wording_policy": {
            "forbidden_phrase_count": len(forbidden_phrases) if isinstance(forbidden_phrases, list) else 0,
        },
        "gate_policy": dict(gate_policy),
        "metadata": {
            "boundary": metadata.get("boundary"),
            "maturity": metadata.get("maturity"),
            "non_claims": list(metadata.get("non_claims") or []),
        },
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
