"""Tests for orchestration contract registry validation."""

from __future__ import annotations

import shutil
from pathlib import Path

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


def test_root_and_packaged_contract_configs_match():
    violations = validate_config_parity(
        root_config_dir=ROOT / "configs",
        package_config_dir=ROOT / "src" / "multi_agent_brief" / "configs",
    )

    assert violations == []


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
