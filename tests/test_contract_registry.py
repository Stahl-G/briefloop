"""Tests for orchestration contract registry validation."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from multi_agent_brief.contracts.registry import ContractRegistry
from multi_agent_brief.contracts.validator import (
    validate_config_parity,
    validate_contract_registry,
)


ROOT = Path(__file__).resolve().parent.parent


def test_contract_registry_loads_current_configs():
    registry = ContractRegistry.from_config_dir(ROOT / "configs")

    assert registry.stage("auditor") is not None
    assert registry.artifact("claim_ledger") is not None
    assert "continue" in registry.decision_vocabulary
    assert "control_tool" in registry.producer_kind_values


def test_current_contract_registry_validates_cleanly():
    registry = ContractRegistry.from_config_dir(ROOT / "configs")

    assert validate_contract_registry(registry) == []


def test_packaged_contract_registry_validates_cleanly():
    registry = ContractRegistry.from_package()

    assert validate_contract_registry(registry) == []


def test_root_and_packaged_contract_configs_match():
    violations = validate_config_parity(
        root_config_dir=ROOT / "configs",
        package_config_dir=ROOT / "src" / "multi_agent_brief" / "configs",
    )

    assert violations == []


def test_audited_brief_ownership_contract_is_editor_scoped():
    root_registry = ContractRegistry.from_config_dir(ROOT / "configs")
    package_registry = ContractRegistry.from_package()

    for registry in (root_registry, package_registry):
        audited = registry.artifact("audited_brief")
        snapshot = registry.artifact("analyst_draft_snapshot")
        analyst = registry.stage("analyst")
        editor = registry.stage("editor")
        assert audited is not None
        assert snapshot is not None
        assert analyst is not None
        assert editor is not None
        assert audited.producer_stage == "editor"
        assert audited.producer_role == "editor"
        assert "audited_brief" in editor.produces
        assert "audited_brief" in editor.expected_artifacts
        assert snapshot.producer_kind == "control_tool"
        assert snapshot.producer_stage == "analyst"
        assert snapshot.producer_role == "python_tool"
        assert "analyst_draft_snapshot" in analyst.produces
        assert "analyst_draft_snapshot" in analyst.expected_artifacts


def test_registry_reports_unknown_expected_artifact(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    stage_specs_path = config_dir / "stage_specs.yaml"
    stage_specs = yaml.safe_load(stage_specs_path.read_text(encoding="utf-8"))
    stage_specs["workflow"]["stages"][0]["expected_artifacts"] = ["missing_artifact"]
    stage_specs_path.write_text(yaml.safe_dump(stage_specs, sort_keys=False), encoding="utf-8")

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert any("unknown artifact: missing_artifact" in item.error for item in violations)


def test_registry_reports_unknown_workflow_producer_stage(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    artifact_path = config_dir / "artifact_contracts.yaml"
    artifact_contracts = yaml.safe_load(artifact_path.read_text(encoding="utf-8"))
    artifact_contracts["artifacts"][0]["producer_stage"] = "missing-stage"
    artifact_path.write_text(
        yaml.safe_dump(artifact_contracts, sort_keys=False),
        encoding="utf-8",
    )

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert any("unknown producer stage: missing-stage" in item.error for item in violations)


def test_registry_allows_control_tool_producer_stage_outside_workflow():
    registry = ContractRegistry.from_config_dir(ROOT / "configs")
    provenance = registry.artifact("provenance_graph")

    assert provenance is not None
    assert provenance.producer_kind == "control_tool"
    assert provenance.producer_stage not in registry.stage_ids()
    assert validate_contract_registry(registry) == []


def test_config_parity_reports_drift(tmp_path: Path):
    root_config_dir = _copy_configs(tmp_path / "root")
    package_config_dir = _copy_configs(tmp_path / "package")
    (package_config_dir / "stage_specs.yaml").write_text(
        "schema_version: drifted\n",
        encoding="utf-8",
    )

    violations = validate_config_parity(
        root_config_dir=root_config_dir,
        package_config_dir=package_config_dir,
    )

    assert any("stage_specs.yaml" in item.field for item in violations)


def _copy_configs(tmp_path: Path) -> Path:
    config_dir = tmp_path / "configs"
    shutil.copytree(ROOT / "configs", config_dir)
    return config_dir
