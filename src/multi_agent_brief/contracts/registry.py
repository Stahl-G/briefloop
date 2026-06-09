"""Read-only registry for MABW orchestration YAML contracts."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class StageSpec:
    """A stage entry from configs/stage_specs.yaml."""

    stage_id: str
    owner: str
    category: str
    command: str
    consumes: tuple[str, ...]
    produces: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    allowed_decisions: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "StageSpec":
        return cls(
            stage_id=str(data.get("stage_id", "")),
            owner=str(data.get("owner", "")),
            category=str(data.get("category", "")),
            command=str(data.get("command", "")),
            consumes=tuple(str(item) for item in data.get("consumes", []) or []),
            produces=tuple(str(item) for item in data.get("produces", []) or []),
            expected_artifacts=tuple(
                str(item) for item in data.get("expected_artifacts", []) or []
            ),
            allowed_decisions=tuple(
                str(item) for item in data.get("allowed_decisions", []) or []
            ),
        )


@dataclass(frozen=True)
class ArtifactContract:
    """An artifact entry from configs/artifact_contracts.yaml."""

    artifact_id: str
    path: str
    format: str
    required: bool
    producer_stage: str
    producer_role: str
    producer_kind: str
    consumer_stages: tuple[str, ...]
    validation_result: str
    allowed_decisions: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ArtifactContract":
        return cls(
            artifact_id=str(data.get("artifact_id", "")),
            path=str(data.get("path", "")),
            format=str(data.get("format", "")),
            required=bool(data.get("required", False)),
            producer_stage=str(data.get("producer_stage", "")),
            producer_role=str(data.get("producer_role", "")),
            producer_kind=str(data.get("producer_kind", "workflow_stage")),
            consumer_stages=tuple(
                str(item) for item in data.get("consumer_stages", []) or []
            ),
            validation_result=str(data.get("validation_result", "")),
            allowed_decisions=tuple(
                str(item) for item in data.get("allowed_decisions", []) or []
            ),
        )


@dataclass(frozen=True)
class ContractRegistry:
    """Typed, read-only view over current contract YAML files."""

    config_dir: Path
    stage_specs_data: dict[str, Any]
    artifact_contracts_data: dict[str, Any]
    orchestrator_contract_data: dict[str, Any]
    policy_pack_data: dict[str, Any]
    stages: tuple[StageSpec, ...]
    artifacts: tuple[ArtifactContract, ...]
    decision_vocabulary: tuple[str, ...]
    producer_kind_values: tuple[str, ...]

    @classmethod
    def from_config_dir(cls, config_dir: str | Path) -> "ContractRegistry":
        base = Path(config_dir)
        stage_specs = _load_yaml(base / "stage_specs.yaml")
        artifact_contracts = _load_yaml(base / "artifact_contracts.yaml")
        orchestrator_contract = _load_yaml(base / "orchestrator_contract.yaml")
        policy_pack = _load_yaml(base / "policy_packs" / "default.yaml")

        stages = tuple(
            StageSpec.from_mapping(item)
            for item in (stage_specs.get("workflow", {}) or {}).get("stages", []) or []
        )
        artifacts = tuple(
            ArtifactContract.from_mapping(item)
            for item in artifact_contracts.get("artifacts", []) or []
        )
        decision_vocabulary = tuple(
            str(item) for item in orchestrator_contract.get("decision_vocabulary", []) or []
        )
        producer_kind_values = tuple(
            str(item)
            for item in (
                artifact_contracts.get("artifact_contract", {}) or {}
            ).get("producer_kind_values", []) or []
        )
        return cls(
            config_dir=base,
            stage_specs_data=stage_specs,
            artifact_contracts_data=artifact_contracts,
            orchestrator_contract_data=orchestrator_contract,
            policy_pack_data=policy_pack,
            stages=stages,
            artifacts=artifacts,
            decision_vocabulary=decision_vocabulary,
            producer_kind_values=producer_kind_values,
        )

    @classmethod
    def from_package(cls) -> "ContractRegistry":
        config_dir = files("multi_agent_brief").joinpath("configs")
        return cls.from_config_dir(Path(str(config_dir)))

    def stage_ids(self) -> set[str]:
        return {stage.stage_id for stage in self.stages}

    def artifact_ids(self) -> set[str]:
        return {artifact.artifact_id for artifact in self.artifacts}

    def stage(self, stage_id: str) -> StageSpec | None:
        return next((stage for stage in self.stages if stage.stage_id == stage_id), None)

    def artifact(self, artifact_id: str) -> ArtifactContract | None:
        return next(
            (artifact for artifact in self.artifacts if artifact.artifact_id == artifact_id),
            None,
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data

