"""Runtime-state contract loading helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.runtime_state._io import _load_yaml
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
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
    return [artifact for artifact in artifacts if isinstance(artifact, dict)]


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
