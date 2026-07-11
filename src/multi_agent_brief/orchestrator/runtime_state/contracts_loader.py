"""Runtime-state contract loading helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.runtime_state._io import _load_yaml
from multi_agent_brief.orchestrator.runtime_state.artifact_paths import (
    validate_workspace_relative_artifact_path,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.paths import RUNTIME_STATE_FILES
from multi_agent_brief.orchestrator_contract import CONTRACT_REFERENCES


def _contract_file(repo_workdir: Path, rel_path: str) -> Path:
    path = repo_workdir / rel_path
    if not path.exists():
        raise RuntimeStateError(
            f"Contract file not found: {path}",
            details={"contract": rel_path, "repo_workdir": str(repo_workdir)},
        )
    return path


def load_stage_specs(repo_workdir: str | Path) -> list[dict[str, Any]]:
    repo = Path(repo_workdir).expanduser().resolve()
    data = _load_yaml(_contract_file(repo, CONTRACT_REFERENCES["stage_specs"]))
    stages = ((data.get("workflow") or {}).get("stages") or [])
    if not isinstance(stages, list):
        raise RuntimeStateError("stage_specs.yaml workflow.stages must be a list")
    return [stage for stage in stages if isinstance(stage, dict)]


def load_artifact_contracts(repo_workdir: str | Path) -> list[dict[str, Any]]:
    repo = Path(repo_workdir).expanduser().resolve()
    data = _load_yaml(_contract_file(repo, CONTRACT_REFERENCES["artifact_contracts"]))
    artifacts = data.get("artifacts") or []
    if not isinstance(artifacts, list):
        raise RuntimeStateError("artifact_contracts.yaml artifacts must be a list")
    records = [artifact for artifact in artifacts if isinstance(artifact, dict)]
    owners: dict[str, tuple[str, str]] = {}
    artifact_ids: set[str] = set()
    reserved = {path.casefold(): path for path in RUNTIME_STATE_FILES.values()}
    for artifact in records:
        raw_artifact_id = artifact.get("artifact_id")
        artifact_id = raw_artifact_id.strip() if isinstance(raw_artifact_id, str) else ""
        if not artifact_id:
            raise RuntimeStateError(
                "Artifact contract artifact_id must be a non-empty string.",
                details={"artifact_id": raw_artifact_id},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        if artifact_id in artifact_ids:
            raise RuntimeStateError(
                "Artifact contract artifact_id must be unique.",
                details={"artifact_id": artifact_id},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        artifact_ids.add(artifact_id)
        artifact["artifact_id"] = artifact_id
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise RuntimeStateError(
                "Artifact contract path must be a non-empty string.",
                details={"artifact_id": artifact_id, "path": raw_path},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        path = validate_workspace_relative_artifact_path(
            raw_path,
            artifact_id=artifact_id,
            binding_source="artifact_contract",
        )
        artifact["path"] = path
        identity_key = path.casefold()
        if identity_key in reserved:
            raise RuntimeStateError(
                "Workflow artifact path conflicts with a runtime control file.",
                details={
                    "artifact_id": artifact_id,
                    "path": path,
                    "reserved_path": reserved[identity_key],
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
        existing_owner = owners.get(identity_key)
        if existing_owner is not None:
            raise RuntimeStateError(
                "Canonical workflow artifact path must have exactly one owner.",
                details={
                    "path": path,
                    "existing_path": existing_owner[0],
                    "artifact_ids": [existing_owner[1], artifact_id],
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
        owners[identity_key] = (path, artifact_id)
    return records


def load_default_policy_pack(repo_workdir: str | Path) -> dict[str, Any]:
    repo = Path(repo_workdir).expanduser().resolve()
    data = _load_yaml(_contract_file(repo, CONTRACT_REFERENCES["default_policy_pack"]))
    if not isinstance(data, dict):
        raise RuntimeStateError("policy_packs/default.yaml must contain an object")
    return data


def _stage_ids(stages: list[dict[str, Any]]) -> list[str]:
    return [str(stage["stage_id"]) for stage in stages if stage.get("stage_id")]


def _artifact_ids(artifacts: list[dict[str, Any]]) -> set[str]:
    return {
        str(artifact["artifact_id"])
        for artifact in artifacts
        if artifact.get("artifact_id")
    }


def _artifact_map(artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(artifact["artifact_id"]): artifact
        for artifact in artifacts
        if artifact.get("artifact_id")
    }
