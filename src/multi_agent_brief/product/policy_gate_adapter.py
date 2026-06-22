"""Deterministic gate adapter for product-layer PolicyProfiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.product.policy_projection import project_workspace_policy_profile
from multi_agent_brief.product.policy_registry import PolicyProfileRegistry

POLICY_GATE_ADAPTER_BOUNDARY = "product_policy_profile_deterministic_gate_adapter"


def resolve_workspace_policy_gate_adapter(workspace: str | Path) -> dict[str, Any]:
    """Resolve deterministic gate knobs for a workspace.

    The adapter may only tighten existing deterministic checks. It must not
    create a second gate engine, make compliance/truth claims, or bypass the
    Claim Ledger / quality gate / human delivery spine.
    """

    projection = project_workspace_policy_profile(workspace)
    if projection.get("status") != "resolved":
        return {
            "status": projection.get("status") or "not_available",
            "boundary": POLICY_GATE_ADAPTER_BOUNDARY,
            "runtime_effect": "none",
            "projection_status": projection.get("status"),
            "reason": projection.get("reason") or "policy_profile_not_resolved",
        }

    profile_id = projection.get("resolved_policy_profile")
    if not isinstance(profile_id, str) or not profile_id.strip():
        return {
            "status": "not_resolved",
            "boundary": POLICY_GATE_ADAPTER_BOUNDARY,
            "runtime_effect": "none",
            "projection_status": projection.get("status"),
        }

    registry = PolicyProfileRegistry.from_package()
    profile = registry.get(profile_id.strip())
    if profile is None:
        return {
            "status": "unknown_policy_profile",
            "boundary": POLICY_GATE_ADAPTER_BOUNDARY,
            "runtime_effect": "none",
            "projection_status": projection.get("status"),
            "policy_profile_id": profile_id.strip(),
        }

    payload = dict(profile.payload)
    source_policy = payload.get("source_policy") if isinstance(payload.get("source_policy"), dict) else {}
    wording_policy = payload.get("wording_policy") if isinstance(payload.get("wording_policy"), dict) else {}
    gate_policy = payload.get("gate_policy") if isinstance(payload.get("gate_policy"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "status": "applied",
        "boundary": POLICY_GATE_ADAPTER_BOUNDARY,
        "runtime_effect": "tighten_existing_deterministic_gates_only",
        "policy_profile_id": profile_id.strip(),
        "source": projection.get("source"),
        "policy_profile_sha256": projection.get("policy_profile_sha256"),
        "gate_policy": {
            key: str(value).strip()
            for key, value in gate_policy.items()
            if isinstance(key, str) and isinstance(value, str) and value.strip()
        },
        "source_policy": {
            "freshness_days_by_tier": dict(source_policy.get("freshness_days_by_tier") or {}),
        },
        "wording_policy": {
            "forbidden_phrases": _stable_strings(wording_policy.get("forbidden_phrases")),
        },
        "adapter_scope": [
            "quality_gates.strictness",
            "reader_final_gate.forbidden_phrases",
        ],
        "non_claims": list(metadata.get("non_claims") or []),
    }


def policy_gate_is_strict(adapter: dict[str, Any] | None, gate_id: str, *, cli_strict: bool = False) -> bool:
    if cli_strict:
        return True
    if not isinstance(adapter, dict) or adapter.get("status") != "applied":
        return False
    gate_policy = adapter.get("gate_policy")
    if not isinstance(gate_policy, dict):
        return False
    return gate_policy.get(gate_id) == "strict"


def policy_forbidden_phrases(adapter: dict[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(adapter, dict) or adapter.get("status") != "applied":
        return ()
    wording_policy = adapter.get("wording_policy")
    if not isinstance(wording_policy, dict):
        return ()
    return tuple(_stable_strings(wording_policy.get("forbidden_phrases")))


def _stable_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split()).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result
