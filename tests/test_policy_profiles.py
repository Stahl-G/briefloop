"""Tests for experimental product-layer PolicyProfile contracts."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.contracts.registry import ContractRegistry
from multi_agent_brief.contracts.schemas.policy_profile import PolicyProfileContract
from multi_agent_brief.product.policy_profile import validate_policy_profile_payload
from multi_agent_brief.product.policy_gate_adapter import (
    policy_forbidden_phrases,
    policy_gate_is_strict,
    resolve_workspace_policy_gate_adapter,
)
from multi_agent_brief.product.policy_projection import project_workspace_policy_profile
from multi_agent_brief.product.policy_registry import PolicyProfileRegistry
from multi_agent_brief.product.report_pack import validate_report_pack_payload
from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import validate_report_spec_payload

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PROFILE_IDS = {
    "evidence_extract_default",
    "finance_default",
    "internet_default",
    "manufacturing_default",
    "solar_manufacturing_default",
}


def _manufacturing_profile() -> dict:
    return yaml.safe_load(
        (ROOT / "configs" / "policy_profiles" / "manufacturing_default.yaml").read_text(encoding="utf-8")
    )


def _policy_profile(profile_id: str) -> dict:
    return yaml.safe_load(
        (ROOT / "configs" / "policy_profiles" / f"{profile_id}.yaml").read_text(encoding="utf-8")
    )


def _market_pack() -> dict:
    return yaml.safe_load((ROOT / "configs" / "report_packs" / "market_weekly.yaml").read_text(encoding="utf-8"))


def _market_spec() -> dict:
    return dict(_market_pack()["default_report_spec"])


def test_policy_profile_contract_accepts_manufacturing_default() -> None:
    payload = _manufacturing_profile()

    assert PolicyProfileContract.validate(payload) == []
    assert validate_policy_profile_payload(payload) == []


def test_policy_profile_contract_rejects_scoring_like_tier_weights() -> None:
    payload = _manufacturing_profile()
    payload["source_policy"] = dict(payload["source_policy"])
    payload["source_policy"]["tier_weights"] = {"official": 1.0}

    violations = PolicyProfileContract.validate(payload)

    assert any(item.field == "source_policy.tier_weights" for item in violations)


def test_policy_profile_contract_rejects_invalid_gate_policy() -> None:
    payload = _manufacturing_profile()
    payload["gate_policy"] = dict(payload["gate_policy"])
    payload["gate_policy"]["freshness"] = "release_ready"

    violations = PolicyProfileContract.validate(payload)

    assert any(item.field == "gate_policy.freshness" for item in violations)


def test_policy_profile_contract_rejects_boolean_freshness_days() -> None:
    payload = _manufacturing_profile()
    payload["source_policy"] = dict(payload["source_policy"])
    payload["source_policy"]["freshness_days_by_tier"] = dict(
        payload["source_policy"]["freshness_days_by_tier"]
    )
    payload["source_policy"]["freshness_days_by_tier"]["official"] = True

    violations = PolicyProfileContract.validate(payload)

    assert any(
        item.field == "source_policy.freshness_days_by_tier.official"
        and "positive integer" in item.error
        for item in violations
    )


def test_policy_profile_registry_discovers_root_and_packaged_profiles() -> None:
    root_registry = PolicyProfileRegistry.from_config_dir(ROOT / "configs" / "policy_profiles")
    package_registry = PolicyProfileRegistry.from_package()

    for registry in (root_registry, package_registry):
        assert not registry.validation_errors
        assert registry.profile_ids() == EXPECTED_PROFILE_IDS
        assert registry.get("finance_default") is not None
        assert registry.get("internet_default") is not None
        assert registry.get("evidence_extract_default") is not None
        assert registry.get("manufacturing_default") is not None
        assert registry.get("solar_manufacturing_default") is not None


def test_finance_and_internet_profiles_are_conservative_skeletons() -> None:
    for profile_id, required_boundary in (
        ("finance_default", "no_finance_compliance_judgment"),
        ("internet_default", "no_rumor_verification"),
    ):
        payload = _policy_profile(profile_id)

        assert PolicyProfileContract.validate(payload) == []
        assert "tier_weights" not in payload["source_policy"]
        assert payload["metadata"]["boundary"] == "experimental_policy_profile_only"
        assert payload["metadata"]["maturity"] == "conservative_skeleton"
        assert required_boundary in payload["metadata"]["non_claims"]
        assert "no_release_authority" in payload["metadata"]["non_claims"]


def test_solar_manufacturing_profile_is_dogfood_contract_only() -> None:
    payload = _policy_profile("solar_manufacturing_default")

    assert PolicyProfileContract.validate(payload) == []
    assert payload["industry"] == "solar_manufacturing"
    assert payload["metadata"]["boundary"] == "experimental_policy_profile_only"
    assert payload["metadata"]["maturity"] == "solar_dogfood_profile"
    assert "no_industry_compliance_judgment" in payload["metadata"]["non_claims"]
    assert "no_tax_advice" in payload["metadata"]["non_claims"]
    assert "no_investment_advice" in payload["metadata"]["non_claims"]
    assert "no_release_authority" in payload["metadata"]["non_claims"]
    assert "tier_weights" not in payload["source_policy"]
    assert payload["gate_policy"] == {
        "freshness": "strict",
        "material_fact": "strict",
        "target_relevance": "standard",
    }
    assert "Section 232" in payload["claim_policy"]["materiality_terms"]
    assert "FEOC" in payload["claim_policy"]["materiality_terms"]
    assert "无风险" in payload["wording_policy"]["forbidden_phrases"]


def test_evidence_extract_profile_is_registration_contract_only() -> None:
    payload = _policy_profile("evidence_extract_default")

    assert PolicyProfileContract.validate(payload) == []
    assert payload["industry"] == "evidence_extract"
    assert payload["metadata"]["boundary"] == "experimental_policy_profile_only"
    assert payload["metadata"]["maturity"] == "conservative_skeleton"
    assert "no_legal_conclusion" in payload["metadata"]["non_claims"]
    assert "no_disclosure_readiness" in payload["metadata"]["non_claims"]
    assert "no_automatic_span_extraction" in payload["metadata"]["non_claims"]
    assert "no_release_authority" in payload["metadata"]["non_claims"]
    assert "tier_weights" not in payload["source_policy"]


def test_policy_profile_config_parity_between_root_and_package_copy() -> None:
    root_dir = ROOT / "configs" / "policy_profiles"
    package_dir = ROOT / "src" / "multi_agent_brief" / "configs" / "policy_profiles"

    for path in sorted(root_dir.glob("*.yaml")):
        package_path = package_dir / path.name
        assert package_path.exists()
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == yaml.safe_load(
            package_path.read_text(encoding="utf-8")
        )


def test_report_pack_default_policy_profile_reference_is_validated() -> None:
    policy_registry = PolicyProfileRegistry.from_config_dir(ROOT / "configs" / "policy_profiles")
    report_registry = ReportPackRegistry.from_config_dir(
        ROOT / "configs" / "report_packs",
        known_policy_profiles=policy_registry.profile_ids(),
    )

    assert not report_registry.validation_errors
    assert report_registry.default_policy_profile_by_pack() == {
        "evidence_extract": "evidence_extract_default",
        "management_monthly": "manufacturing_default",
        "market_weekly": "manufacturing_default",
        "solar_industry_periodic": "solar_manufacturing_default",
    }
    assert report_registry.get("evidence_extract").default_policy_profile == "evidence_extract_default"
    assert report_registry.get("market_weekly").default_policy_profile == "manufacturing_default"
    assert report_registry.get("solar_industry_periodic").default_policy_profile == "solar_manufacturing_default"


def test_report_pack_payload_rejects_unknown_default_policy_profile() -> None:
    payload = _market_pack()
    payload["default_policy_profile"] = "missing_profile"

    violations = validate_report_pack_payload(payload, known_policy_profiles={"manufacturing_default"})

    assert any(item.field == "default_policy_profile" for item in violations)


def test_report_pack_payload_rejects_default_spec_policy_profile_mismatch() -> None:
    payload = _market_pack()
    payload["default_report_spec"] = dict(payload["default_report_spec"])
    payload["default_report_spec"]["policy_profile"] = "finance_default"

    violations = validate_report_pack_payload(
        payload,
        known_policy_profiles={"manufacturing_default", "finance_default"},
    )

    assert any(item.field == "default_report_spec.policy_profile" for item in violations)


def test_report_spec_validation_resolves_pack_default_policy_profile() -> None:
    spec = _market_spec()
    spec.pop("policy_profile", None)
    report_registry = ReportPackRegistry.from_package()
    policy_registry = PolicyProfileRegistry.from_package()

    result = validate_report_spec_payload(
        spec,
        known_report_packs=report_registry.pack_ids(),
        report_type_by_pack=report_registry.report_type_by_pack(),
        known_policy_profiles=policy_registry.profile_ids(),
        default_policy_profile_by_pack=report_registry.default_policy_profile_by_pack(),
    )

    assert result.ok
    assert result.policy_profile is None
    assert result.resolved_policy_profile == "manufacturing_default"


def test_report_spec_validation_rejects_unknown_policy_profile() -> None:
    spec = _market_spec()
    spec["policy_profile"] = "missing_profile"
    report_registry = ReportPackRegistry.from_package()
    policy_registry = PolicyProfileRegistry.from_package()

    result = validate_report_spec_payload(
        spec,
        known_report_packs=report_registry.pack_ids(),
        report_type_by_pack=report_registry.report_type_by_pack(),
        known_policy_profiles=policy_registry.profile_ids(),
        default_policy_profile_by_pack=report_registry.default_policy_profile_by_pack(),
    )

    assert not result.ok
    assert any(item.field == "policy_profile" for item in result.errors)


def test_workspace_policy_projection_resolves_pack_default(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = _market_spec()
    spec.pop("policy_profile", None)
    (ws / "report_spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    projection = project_workspace_policy_profile(ws)

    assert projection["status"] == "resolved"
    assert projection["policy_profile"] is None
    assert projection["resolved_policy_profile"] == "manufacturing_default"
    assert projection["source"] == "report_pack.default_policy_profile"
    assert projection["runtime_effect"] == "none"
    assert projection["profile"]["industry"] == "manufacturing"
    assert projection["policy_profile_sha256"]


def test_workspace_policy_projection_uses_report_spec_override(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = _market_spec()
    spec["policy_profile"] = "finance_default"
    (ws / "report_spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    projection = project_workspace_policy_profile(ws)

    assert projection["status"] == "resolved"
    assert projection["policy_profile"] == "finance_default"
    assert projection["resolved_policy_profile"] == "finance_default"
    assert projection["source"] == "report_spec.policy_profile"
    assert "no_finance_compliance_judgment" in projection["profile"]["metadata"]["non_claims"]


def test_workspace_policy_projection_surfaces_industry_resolution_source(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = _market_spec()
    spec["policy_profile"] = "finance_default"
    spec["policy_profile_resolution"] = {
        "policy_profile": "finance_default",
        "source": "industry_resolver",
        "input": "listed company IR",
        "matched_rule": "finance_keywords",
        "confidence": "deterministic_exact_or_keyword",
        "alternatives": [],
    }
    (ws / "report_spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    projection = project_workspace_policy_profile(ws)

    assert projection["status"] == "resolved"
    assert projection["resolved_policy_profile"] == "finance_default"
    assert projection["source"] == "industry_resolver"
    assert projection["policy_profile_resolution"]["matched_rule"] == "finance_keywords"


def test_workspace_policy_projection_rejects_unknown_override(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = _market_spec()
    spec["policy_profile"] = "missing_profile"
    (ws / "report_spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    projection = project_workspace_policy_profile(ws)

    assert projection["status"] == "invalid_report_spec"
    assert any(item["field"] == "policy_profile" for item in projection["errors"])


def test_workspace_policy_projection_absent_report_spec_is_non_blocking(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()

    projection = project_workspace_policy_profile(ws)

    assert projection["status"] == "not_available"
    assert projection["runtime_effect"] == "none"


def test_workspace_policy_gate_adapter_resolves_deterministic_knobs(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = _market_spec()
    spec["policy_profile"] = "finance_default"
    (ws / "report_spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    adapter = resolve_workspace_policy_gate_adapter(ws)

    assert adapter["status"] == "applied"
    assert adapter["runtime_effect"] == "tighten_existing_deterministic_gates_only"
    assert adapter["policy_profile_id"] == "finance_default"
    assert policy_gate_is_strict(adapter, "freshness") is True
    assert policy_gate_is_strict(adapter, "target_relevance") is True
    assert "guaranteed return" in policy_forbidden_phrases(adapter)


def test_workspace_policy_gate_adapter_absent_spec_has_no_runtime_effect(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()

    adapter = resolve_workspace_policy_gate_adapter(ws)

    assert adapter["status"] == "not_available"
    assert adapter["runtime_effect"] == "none"
    assert policy_gate_is_strict(adapter, "freshness") is False
    assert policy_forbidden_phrases(adapter) == ()


def test_validate_report_spec_cli_reports_resolved_policy_profile(tmp_path: Path, capsys) -> None:
    spec = _market_spec()
    spec.pop("policy_profile", None)
    spec_path = tmp_path / "report_spec.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    assert main(["validate-report-spec", str(spec_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["policy_profile"] is None
    assert payload["resolved_policy_profile"] == "manufacturing_default"
    assert payload["policy_profile_source"] == "report_pack.default_policy_profile"


def test_report_spec_validation_reports_policy_profile_resolution_source() -> None:
    spec = _market_spec()
    spec["policy_profile"] = "finance_default"
    spec["policy_profile_resolution"] = {
        "policy_profile": "finance_default",
        "source": "industry_resolver",
        "input": "listed company IR",
        "matched_rule": "finance_keywords",
        "confidence": "deterministic_exact_or_keyword",
        "alternatives": [],
    }
    report_registry = ReportPackRegistry.from_package()
    policy_registry = PolicyProfileRegistry.from_package()

    result = validate_report_spec_payload(
        spec,
        known_report_packs=report_registry.pack_ids(),
        report_type_by_pack=report_registry.report_type_by_pack(),
        known_policy_profiles=policy_registry.profile_ids(),
        default_policy_profile_by_pack=report_registry.default_policy_profile_by_pack(),
    )

    assert result.ok
    assert result.resolved_policy_profile == "finance_default"
    assert result.policy_profile_source == "industry_resolver"
    assert result.policy_profile_resolution["matched_rule"] == "finance_keywords"


def test_report_spec_validation_rejects_resolver_source_without_explicit_policy_profile() -> None:
    spec = _market_spec()
    spec.pop("policy_profile", None)
    spec["policy_profile_resolution"] = {
        "policy_profile": "manufacturing_default",
        "source": "industry_resolver",
        "input": "listed company IR",
        "matched_rule": "finance_keywords",
        "confidence": "deterministic_exact_or_keyword",
        "alternatives": [],
    }
    report_registry = ReportPackRegistry.from_package()
    policy_registry = PolicyProfileRegistry.from_package()

    result = validate_report_spec_payload(
        spec,
        known_report_packs=report_registry.pack_ids(),
        report_type_by_pack=report_registry.report_type_by_pack(),
        known_policy_profiles=policy_registry.profile_ids(),
        default_policy_profile_by_pack=report_registry.default_policy_profile_by_pack(),
    )

    assert not result.ok
    assert result.resolved_policy_profile == "manufacturing_default"
    assert result.policy_profile_source == "report_pack.default_policy_profile"
    assert any(item.field == "policy_profile_resolution.source" for item in result.errors)


def test_report_spec_validation_rejects_policy_resolution_profile_mismatch() -> None:
    spec = _market_spec()
    spec["policy_profile"] = "finance_default"
    spec["policy_profile_resolution"] = {
        "policy_profile": "manufacturing_default",
        "source": "industry_resolver",
        "input": "listed company IR",
        "matched_rule": "finance_keywords",
        "confidence": "deterministic_exact_or_keyword",
        "alternatives": [],
    }
    report_registry = ReportPackRegistry.from_package()
    policy_registry = PolicyProfileRegistry.from_package()

    result = validate_report_spec_payload(
        spec,
        known_report_packs=report_registry.pack_ids(),
        report_type_by_pack=report_registry.report_type_by_pack(),
        known_policy_profiles=policy_registry.profile_ids(),
        default_policy_profile_by_pack=report_registry.default_policy_profile_by_pack(),
    )

    assert not result.ok
    assert any(item.field == "policy_profile_resolution.policy_profile" for item in result.errors)


def test_report_spec_validation_rejects_false_pack_default_policy_profile_source() -> None:
    spec = _market_spec()
    spec["policy_profile"] = "finance_default"
    spec["policy_profile_resolution"] = {
        "policy_profile": "finance_default",
        "source": "report_pack.default_policy_profile",
        "input": "finance",
        "matched_rule": "manual_edit",
        "confidence": "default_no_match",
        "alternatives": [],
    }
    report_registry = ReportPackRegistry.from_package()
    policy_registry = PolicyProfileRegistry.from_package()

    result = validate_report_spec_payload(
        spec,
        known_report_packs=report_registry.pack_ids(),
        report_type_by_pack=report_registry.report_type_by_pack(),
        known_policy_profiles=policy_registry.profile_ids(),
        default_policy_profile_by_pack=report_registry.default_policy_profile_by_pack(),
    )

    assert not result.ok
    assert result.resolved_policy_profile == "finance_default"
    assert any(
        item.field == "policy_profile_resolution.source"
        and "pack default:manufacturing_default" in item.error
        for item in result.errors
    )


def test_policy_profiles_do_not_change_runtime_stage_contracts() -> None:
    registry = ContractRegistry.from_config_dir(ROOT / "configs")

    assert registry.artifact("policy_profile") is None
    for stage in registry.stages:
        assert "policy_profile" not in stage.produces
        assert "policy_profile" not in stage.expected_artifacts
