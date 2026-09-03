"""Role-topology selector contract tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from multi_agent_brief.contracts.registry import ContractRegistry
from multi_agent_brief.contracts.validator import validate_contract_registry


ROOT = Path(__file__).resolve().parents[1]


def test_unknown_role_topology_fails_contract_validation(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    policy_path = config_dir / "policy_packs" / "default.yaml"
    policy_pack = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy_pack["policy"]["role_topology"] = "compact"
    policy_path.write_text(yaml.safe_dump(policy_pack, sort_keys=False), encoding="utf-8")

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert any(item.field == "policy.role_topology" for item in violations)


def test_absent_role_topology_validates_as_backcompat_default(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    policy_path = config_dir / "policy_packs" / "default.yaml"
    policy_pack = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy_pack.pop("policy", None)
    policy_path.write_text(yaml.safe_dump(policy_pack, sort_keys=False), encoding="utf-8")

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert violations == []


def _copy_configs(tmp_path: Path) -> Path:
    import shutil

    config_dir = tmp_path / "configs"
    shutil.copytree(ROOT / "configs", config_dir)
    return config_dir
