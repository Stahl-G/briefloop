"""Role-topology selector contract tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.contracts.registry import ContractRegistry
from multi_agent_brief.contracts.role_topology import (
    ROLE_TOPOLOGY_DEFAULT,
    ROLE_TOPOLOGY_SATISFIER_VALUES,
    ROLE_TOPOLOGY_VALUES,
    resolve_role_topology,
)
from multi_agent_brief.contracts.validator import validate_contract_registry
from multi_agent_brief.orchestrator.role_topology import (
    stage_satisfaction_rules_for_topology,
)
from multi_agent_brief.orchestrator.runtime_state.completion_gates import (
    _role_topology_from_policy_pack,
    _topology_satisfaction_rules,
)


ROOT = Path(__file__).resolve().parents[1]


def test_default_policy_pack_selects_default_topology_without_active_satisfaction_until_assets_sync():
    rules = stage_satisfaction_rules_for_topology(
        stages=_stage_specs(),
        policy_pack=_default_policy_pack(),
    )

    assert resolve_role_topology(_default_policy_pack()) == ROLE_TOPOLOGY_DEFAULT
    assert _role_topology_from_policy_pack(_default_policy_pack()) == ROLE_TOPOLOGY_DEFAULT
    assert ROLE_TOPOLOGY_VALUES == frozenset({"default", "strict", "human_assisted"})
    assert ROLE_TOPOLOGY_SATISFIER_VALUES == frozenset({"scout", "writer"})
    assert "screener" not in rules


def test_strict_topology_keeps_independent_screener_stage():
    policy_pack = {"policy": {"role_topology": "strict"}}

    rules = stage_satisfaction_rules_for_topology(
        stages=_stage_specs(),
        policy_pack=policy_pack,
    )
    layer_d_rules = _topology_satisfaction_rules(
        stages=_stage_specs(),
        policy_pack=policy_pack,
    )

    assert resolve_role_topology(policy_pack) == "strict"
    assert "screener" not in rules
    assert layer_d_rules == rules


def test_human_assisted_topology_declares_writer_satisfaction_hooks():
    rules = stage_satisfaction_rules_for_topology(
        stages=_stage_specs(),
        policy_pack={"policy": {"role_topology": "human_assisted"}},
    )

    assert rules["screener"]["satisfied_by"] == "scout"
    assert rules["analyst"] == {
        "topology": "human_assisted",
        "satisfied_by": "writer",
        "required_artifacts": ["audited_brief"],
    }
    assert rules["editor"] == {
        "topology": "human_assisted",
        "satisfied_by": "writer",
        "required_artifacts": ["audited_brief"],
    }


def test_missing_role_topology_defaults_to_default():
    policy_pack = {"policy_pack": {"name": "legacy"}}

    assert resolve_role_topology(policy_pack) == "default"
    assert "screener" not in stage_satisfaction_rules_for_topology(
        stages=_stage_specs(),
        policy_pack=policy_pack,
    )


def test_unknown_role_topology_raises_for_layer_d_projection():
    with pytest.raises(ValueError, match="policy.role_topology"):
        resolve_role_topology({"policy": {"role_topology": "compact"}})


def test_unknown_role_topology_fails_contract_validation(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    policy_path = config_dir / "policy_packs" / "default.yaml"
    policy_pack = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy_pack["policy"]["role_topology"] = "compact"
    policy_path.write_text(yaml.safe_dump(policy_pack, sort_keys=False), encoding="utf-8")

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert any(item.field == "policy.role_topology" for item in violations)


def test_unknown_topology_satisfier_fails_contract_validation(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    stage_specs_path = config_dir / "stage_specs.yaml"
    stage_specs = yaml.safe_load(stage_specs_path.read_text(encoding="utf-8"))
    screener = next(
        stage
        for stage in stage_specs["workflow"]["stages"]
        if stage["stage_id"] == "screener"
    )
    screener["topology_satisfaction"]["human_assisted"]["satisfied_by"] = "typo_writer"
    stage_specs_path.write_text(yaml.safe_dump(stage_specs, sort_keys=False), encoding="utf-8")

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert any(
        item.field == "stages.screener.topology_satisfaction.human_assisted.satisfied_by"
        and "unknown topology satisfier: typo_writer" in item.error
        for item in violations
    )


def test_unknown_topology_required_artifact_fails_contract_validation(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    stage_specs_path = config_dir / "stage_specs.yaml"
    stage_specs = yaml.safe_load(stage_specs_path.read_text(encoding="utf-8"))
    screener = next(
        stage
        for stage in stage_specs["workflow"]["stages"]
        if stage["stage_id"] == "screener"
    )
    screener["topology_satisfaction"]["human_assisted"]["required_artifacts"] = [
        "candidate_claims",
        "missing_topology_artifact",
    ]
    stage_specs_path.write_text(yaml.safe_dump(stage_specs, sort_keys=False), encoding="utf-8")

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert any(
        item.field == "stages.screener.topology_satisfaction.human_assisted.required_artifacts"
        and "unknown artifact: missing_topology_artifact" in item.error
        for item in violations
    )


def test_topology_required_artifacts_must_be_a_list(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    stage_specs_path = config_dir / "stage_specs.yaml"
    stage_specs = yaml.safe_load(stage_specs_path.read_text(encoding="utf-8"))
    screener = next(
        stage
        for stage in stage_specs["workflow"]["stages"]
        if stage["stage_id"] == "screener"
    )
    screener["topology_satisfaction"]["human_assisted"]["required_artifacts"] = 123
    stage_specs_path.write_text(yaml.safe_dump(stage_specs, sort_keys=False), encoding="utf-8")

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert any(
        item.field == "stages.screener.topology_satisfaction.human_assisted.required_artifacts"
        and item.error == "must be a list of artifact ids"
        for item in violations
    )


def test_topology_required_artifacts_must_contain_strings(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    stage_specs_path = config_dir / "stage_specs.yaml"
    stage_specs = yaml.safe_load(stage_specs_path.read_text(encoding="utf-8"))
    screener = next(
        stage
        for stage in stage_specs["workflow"]["stages"]
        if stage["stage_id"] == "screener"
    )
    screener["topology_satisfaction"]["human_assisted"]["required_artifacts"] = [
        "candidate_claims",
        {"artifact_id": "screened_candidates"},
    ]
    stage_specs_path.write_text(yaml.safe_dump(stage_specs, sort_keys=False), encoding="utf-8")

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert any(
        item.field == "stages.screener.topology_satisfaction.human_assisted.required_artifacts"
        and item.error == "must contain only non-empty artifact id strings"
        for item in violations
    )


def test_absent_role_topology_validates_as_backcompat_default(tmp_path: Path):
    config_dir = _copy_configs(tmp_path)
    policy_path = config_dir / "policy_packs" / "default.yaml"
    policy_pack = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy_pack.pop("policy", None)
    policy_path.write_text(yaml.safe_dump(policy_pack, sort_keys=False), encoding="utf-8")

    violations = validate_contract_registry(ContractRegistry.from_config_dir(config_dir))

    assert violations == []


def test_contract_validator_does_not_import_orchestrator_layer():
    validator_path = ROOT / "src" / "multi_agent_brief" / "contracts" / "validator.py"
    tree = ast.parse(validator_path.read_text(encoding="utf-8"), filename=str(validator_path))

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not any(
        module == "multi_agent_brief.orchestrator"
        or module.startswith("multi_agent_brief.orchestrator.")
        for module in imported_modules
    )


def _stage_specs() -> list[dict]:
    data = yaml.safe_load((ROOT / "configs" / "stage_specs.yaml").read_text(encoding="utf-8"))
    return data["workflow"]["stages"]


def _default_policy_pack() -> dict:
    return yaml.safe_load(
        (ROOT / "configs" / "policy_packs" / "default.yaml").read_text(encoding="utf-8")
    )


def _copy_configs(tmp_path: Path) -> Path:
    import shutil

    config_dir = tmp_path / "configs"
    shutil.copytree(ROOT / "configs", config_dir)
    return config_dir
