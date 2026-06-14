"""Runtime manifest helpers for Orchestrator runtime state."""

from __future__ import annotations

from typing import Any

from multi_agent_brief.orchestrator_contract import CONTRACT_REFERENCES
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import _stage_ids
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_MANIFEST_EXTENSION_LOST,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.identity import _source_or_package_version
from multi_agent_brief.orchestrator.runtime_state.paths import RUNTIME_STATE_FILES


RUNTIME_MANIFEST_SCHEMA = "multi-agent-brief-runtime-manifest/v1"
PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS = ("improvement", "recipe", "fact_layer_import")


def _runtime_manifest(
    *,
    run_id: str,
    created_at: str,
    updated_at: str,
    runtime: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": RUNTIME_MANIFEST_SCHEMA,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "workspace": ".",
        "runtime": runtime,
        "mabw_version": _source_or_package_version(),
        "contract_references": dict(CONTRACT_REFERENCES),
        "runtime_state_files": dict(RUNTIME_STATE_FILES),
        "stage_order": _stage_ids(stages),
        "expected_artifacts": [
            {
                "artifact_id": artifact.get("artifact_id", ""),
                "path": artifact.get("path", ""),
                "required": bool(artifact.get("required", False)),
                "producer_stage": artifact.get("producer_stage", ""),
                "consumer_stages": artifact.get("consumer_stages", []),
            }
            for artifact in artifacts
        ],
    }


def _preserved_manifest_extensions(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: manifest[key]
        for key in PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS
        if key in manifest
    }


def _assert_manifest_extensions_preserved(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    missing = [
        key
        for key, value in before.items()
        if key not in after or after.get(key) != value
    ]
    if missing:
        raise RuntimeStateError(
            "Registered runtime_manifest extension keys were lost.",
            details={"missing_extensions": missing},
            error_code=E_MANIFEST_EXTENSION_LOST,
        )
