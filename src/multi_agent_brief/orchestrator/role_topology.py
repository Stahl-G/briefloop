"""Role-topology selector helpers for Orchestrator policy packs."""

from __future__ import annotations

from typing import Any

from multi_agent_brief.contracts.role_topology import resolve_role_topology


def stage_satisfaction_rules_for_topology(
    *,
    stages: list[dict[str, Any]],
    policy_pack: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Project declared stage satisfaction hooks for the selected topology.

    This is a read-only selector projection. PR1 records the contract shape only;
    later satisfaction logic decides how these hooks affect runtime progress.
    """

    topology = resolve_role_topology(policy_pack)
    rules: dict[str, dict[str, Any]] = {}
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_id = str(stage.get("stage_id") or "")
        topology_satisfaction = stage.get("topology_satisfaction")
        if not stage_id or not isinstance(topology_satisfaction, dict):
            continue
        selected = topology_satisfaction.get(topology)
        if not isinstance(selected, dict) or not selected.get("satisfied_by"):
            continue
        rules[stage_id] = {
            "topology": topology,
            "satisfied_by": str(selected.get("satisfied_by")),
            "required_artifacts": [
                str(item)
                for item in (selected.get("required_artifacts") or [])
                if item
            ],
        }
    return rules
