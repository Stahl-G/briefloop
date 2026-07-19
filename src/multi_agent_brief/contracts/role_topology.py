"""Role-topology contract vocabulary."""

from __future__ import annotations

from typing import Any


ROLE_TOPOLOGY_DEFAULT = "default"
ROLE_TOPOLOGY_SINGLE_SESSION = "single_session"
ROLE_TOPOLOGY_VALUES = frozenset(
    {"single_session", "default", "strict", "human_assisted"}
)
ROLE_TOPOLOGY_SATISFIER_VALUES = frozenset({"scout", "writer"})


def resolve_role_topology(policy_pack: dict[str, Any] | None) -> str:
    """Return the selected role topology, defaulting missing legacy packs."""

    policy = policy_pack.get("policy") if isinstance(policy_pack, dict) else None
    if not isinstance(policy, dict) or "role_topology" not in policy:
        return ROLE_TOPOLOGY_DEFAULT
    value = str(policy.get("role_topology") or "").strip()
    if value not in ROLE_TOPOLOGY_VALUES:
        raise ValueError(
            "policy.role_topology must be one of: "
            + ", ".join(sorted(ROLE_TOPOLOGY_VALUES))
        )
    return value
