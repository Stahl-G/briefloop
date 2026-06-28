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


def test_claim_drafts_contract_is_optional_experimental_freeze_input():
    root_registry = ContractRegistry.from_config_dir(ROOT / "configs")
    package_registry = ContractRegistry.from_package()

    for registry in (root_registry, package_registry):
        claim_drafts = registry.artifact("claim_drafts")
        claim_ledger = registry.artifact("claim_ledger")
        claim_ledger_stage = registry.stage("claim-ledger")
        assert claim_drafts is not None
        assert claim_ledger is not None
        assert claim_ledger_stage is not None
        assert claim_drafts.required is False
        assert claim_drafts.path == "output/intermediate/claim_drafts.json"
        assert claim_drafts.validation_result == "experimental_freeze_input"
        assert "claim_drafts" not in claim_ledger_stage.produces
        assert "claim_drafts" not in claim_ledger_stage.expected_artifacts
        assert claim_ledger.required is True
        assert "claim_ledger" in claim_ledger_stage.produces
        assert "claim_ledger" in claim_ledger_stage.expected_artifacts


def test_atomic_claim_graph_contract_is_optional_experimental_schema_foundation():
    root_registry = ContractRegistry.from_config_dir(ROOT / "configs")
    package_registry = ContractRegistry.from_package()

    for registry in (root_registry, package_registry):
        atomic_graph = registry.artifact("atomic_claim_graph")
        claim_ledger_stage = registry.stage("claim-ledger")
        assert atomic_graph is not None
        assert claim_ledger_stage is not None
        assert atomic_graph.required is False
        assert atomic_graph.path == "output/intermediate/atomic_claim_graph.json"
        assert atomic_graph.producer_stage == "claim-ledger"
        assert atomic_graph.producer_role == "claim-ledger"
        assert atomic_graph.consumer_stages == ()
        assert atomic_graph.validation_result == "experimental_atomic_claim_graph_schema"
        assert "atomic_claim_graph" not in claim_ledger_stage.produces
        assert "atomic_claim_graph" not in claim_ledger_stage.expected_artifacts


def test_evidence_span_registry_contract_is_optional_experimental_schema_foundation():
    root_registry = ContractRegistry.from_config_dir(ROOT / "configs")
    package_registry = ContractRegistry.from_package()

    for registry in (root_registry, package_registry):
        span_registry = registry.artifact("evidence_span_registry")
        claim_ledger_stage = registry.stage("claim-ledger")
        assert span_registry is not None
        assert claim_ledger_stage is not None
        assert span_registry.required is False
        assert span_registry.path == "output/intermediate/evidence_span_registry.json"
        assert span_registry.producer_stage == "claim-ledger"
        assert span_registry.producer_role == "claim-ledger"
        assert span_registry.consumer_stages == ()
        assert span_registry.validation_result == "experimental_evidence_span_registry_schema"
        assert "evidence_span_registry" not in claim_ledger_stage.produces
        assert "evidence_span_registry" not in claim_ledger_stage.expected_artifacts


def test_claim_support_matrix_contract_is_optional_experimental_schema_foundation():
    root_registry = ContractRegistry.from_config_dir(ROOT / "configs")
    package_registry = ContractRegistry.from_package()

    for registry in (root_registry, package_registry):
        support_matrix = registry.artifact("claim_support_matrix")
        auditor_stage = registry.stage("auditor")
        claim_ledger_stage = registry.stage("claim-ledger")
        assert support_matrix is not None
        assert auditor_stage is not None
        assert claim_ledger_stage is not None
        assert support_matrix.required is False
        assert support_matrix.path == "output/intermediate/claim_support_matrix.json"
        assert support_matrix.producer_stage == "auditor"
        assert support_matrix.producer_role == "auditor"
        assert support_matrix.consumer_stages == ()
        assert support_matrix.validation_result == "experimental_claim_support_matrix_schema"
        assert "claim_support_matrix" not in auditor_stage.produces
        assert "claim_support_matrix" not in auditor_stage.expected_artifacts
        assert "claim_support_matrix" not in claim_ledger_stage.produces
        assert "claim_support_matrix" not in claim_ledger_stage.expected_artifacts


def test_semantic_assessment_report_contract_is_optional_experimental_schema_foundation():
    root_registry = ContractRegistry.from_config_dir(ROOT / "configs")
    package_registry = ContractRegistry.from_package()

    for registry in (root_registry, package_registry):
        assessment_report = registry.artifact("semantic_assessment_report")
        auditor_stage = registry.stage("auditor")
        assert assessment_report is not None
        assert auditor_stage is not None
        assert assessment_report.required is False
        assert assessment_report.path == "output/intermediate/semantic_assessment_report.json"
        assert assessment_report.producer_stage == "auditor"
        assert assessment_report.producer_role == "auditor"
        assert assessment_report.consumer_stages == ()
        assert assessment_report.validation_result == "experimental_semantic_assessment_report_schema"
        assert "semantic_assessment_report" not in auditor_stage.produces
        assert "semantic_assessment_report" not in auditor_stage.expected_artifacts


def test_source_evidence_pack_manifest_contract_is_optional_source_discovery_control():
    root_registry = ContractRegistry.from_config_dir(ROOT / "configs")
    package_registry = ContractRegistry.from_package()

    for registry in (root_registry, package_registry):
        manifest = registry.artifact("source_evidence_pack_manifest")
        source_stage = registry.stage("source-discovery")
        scout_stage = registry.stage("scout")
        assert manifest is not None
        assert source_stage is not None
        assert scout_stage is not None
        assert manifest.required is False
        assert manifest.path == "output/intermediate/source_evidence_pack_manifest.json"
        assert manifest.producer_kind == "control_tool"
        assert manifest.producer_stage == "source-discovery"
        assert manifest.producer_role == "python_tool"
        assert manifest.validation_result == "experimental_source_evidence_pack_manifest"
        assert "source_evidence_pack_manifest" not in source_stage.produces
        assert "source_evidence_pack_manifest" not in source_stage.expected_artifacts
        assert "source_evidence_pack_manifest" not in scout_stage.expected_artifacts


def test_release_approval_contracts_are_optional_internal_review_controls():
    root_registry = ContractRegistry.from_config_dir(ROOT / "configs")
    package_registry = ContractRegistry.from_package()

    for registry in (root_registry, package_registry):
        finalize_stage = registry.stage("finalize")
        ledger = registry.artifact("human_approval_ledger")
        report = registry.artifact("release_readiness_report")
        assert finalize_stage is not None
        assert ledger is not None
        assert report is not None
        assert ledger.required is False
        assert ledger.producer_kind == "control_tool"
        assert ledger.path == "output/intermediate/human_approval_ledger.json"
        assert ledger.validation_result == "experimental_human_approval_ledger"
        assert report.required is False
        assert report.producer_kind == "control_tool"
        assert report.path == "output/intermediate/release_readiness_report.json"
        assert report.validation_result == "experimental_release_readiness_report"
        assert "human_approval_ledger" not in finalize_stage.produces
        assert "human_approval_ledger" not in finalize_stage.expected_artifacts
        assert "release_readiness_report" not in finalize_stage.produces
        assert "release_readiness_report" not in finalize_stage.expected_artifacts


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
