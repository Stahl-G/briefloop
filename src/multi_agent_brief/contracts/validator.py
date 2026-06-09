"""Validation helpers for MABW orchestration contract registries."""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.contracts.registry import ContractRegistry


PARITY_CONFIG_FILES = (
    "stage_specs.yaml",
    "artifact_contracts.yaml",
    "orchestrator_contract.yaml",
    "policy_packs/default.yaml",
)


def validate_contract_registry(registry: ContractRegistry) -> list[FieldViolation]:
    """Validate stage/artifact/decision references in a registry."""
    violations: list[FieldViolation] = []
    stage_ids = [stage.stage_id for stage in registry.stages]
    artifact_ids = [artifact.artifact_id for artifact in registry.artifacts]
    stage_id_set = set(stage_ids)
    artifact_id_set = set(artifact_ids)
    decision_set = set(registry.decision_vocabulary)
    producer_kind_set = set(registry.producer_kind_values)

    violations.extend(_duplicate_violations("stages.stage_id", stage_ids))
    violations.extend(_duplicate_violations("artifacts.artifact_id", artifact_ids))

    for stage in registry.stages:
        if not stage.stage_id:
            violations.append(FieldViolation("stages.stage_id", "stage_id is required"))
        for artifact_id in stage.expected_artifacts:
            if artifact_id not in artifact_id_set:
                violations.append(
                    FieldViolation(
                        f"stages.{stage.stage_id}.expected_artifacts",
                        f"unknown artifact: {artifact_id}",
                    )
                )
        for decision in stage.allowed_decisions:
            if decision not in decision_set:
                violations.append(
                    FieldViolation(
                        f"stages.{stage.stage_id}.allowed_decisions",
                        f"unknown decision: {decision}",
                    )
                )

    for artifact in registry.artifacts:
        if not artifact.artifact_id:
            violations.append(
                FieldViolation("artifacts.artifact_id", "artifact_id is required")
            )
        if producer_kind_set and artifact.producer_kind not in producer_kind_set:
            violations.append(
                FieldViolation(
                    f"artifacts.{artifact.artifact_id}.producer_kind",
                    f"unknown producer kind: {artifact.producer_kind}",
                )
            )
        if artifact.producer_kind == "workflow_stage" and artifact.producer_stage not in stage_id_set:
            violations.append(
                FieldViolation(
                    f"artifacts.{artifact.artifact_id}.producer_stage",
                    f"unknown producer stage: {artifact.producer_stage}",
                )
            )
        for stage_id in artifact.consumer_stages:
            if stage_id not in stage_id_set:
                violations.append(
                    FieldViolation(
                        f"artifacts.{artifact.artifact_id}.consumer_stages",
                        f"unknown consumer stage: {stage_id}",
                    )
                )
        for decision in artifact.allowed_decisions:
            if decision not in decision_set:
                violations.append(
                    FieldViolation(
                        f"artifacts.{artifact.artifact_id}.allowed_decisions",
                        f"unknown decision: {decision}",
                    )
                )

    return violations


def validate_config_parity(
    *,
    root_config_dir: str | Path,
    package_config_dir: str | Path,
) -> list[FieldViolation]:
    """Validate that root and packaged config copies are byte-equivalent."""
    root = Path(root_config_dir)
    package = Path(package_config_dir)
    violations: list[FieldViolation] = []
    for rel_path in PARITY_CONFIG_FILES:
        root_path = root / rel_path
        package_path = package / rel_path
        if not root_path.exists():
            violations.append(
                FieldViolation(f"configs.{rel_path}", "root config file is missing")
            )
            continue
        if not package_path.exists():
            violations.append(
                FieldViolation(f"configs.{rel_path}", "packaged config file is missing")
            )
            continue
        if root_path.read_text(encoding="utf-8") != package_path.read_text(encoding="utf-8"):
            violations.append(
                FieldViolation(
                    f"configs.{rel_path}",
                    "root and packaged config copies differ",
                )
            )
    return violations


def _duplicate_violations(field: str, values: list[str]) -> list[FieldViolation]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [
        FieldViolation(field, f"duplicate value: {value}")
        for value in sorted(duplicates)
    ]

