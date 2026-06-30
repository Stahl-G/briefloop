"""Tests for experimental product-layer ReportSpec and ReportPack contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import build_parser, main
from multi_agent_brief.contracts.registry import ContractRegistry
from multi_agent_brief.contracts.schemas.report_spec import ReportSpecContract
from multi_agent_brief.product.report_pack import validate_report_pack_payload
from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import validate_report_spec_payload

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PACK_IDS = {
    "evidence_extract",
    "market_weekly",
    "management_monthly",
    "solar_industry_periodic",
}


def _market_pack() -> dict:
    return yaml.safe_load((ROOT / "configs" / "report_packs" / "market_weekly.yaml").read_text(encoding="utf-8"))


def _solar_pack() -> dict:
    return yaml.safe_load(
        (ROOT / "configs" / "report_packs" / "solar_industry_periodic.yaml").read_text(encoding="utf-8")
    )


def _evidence_extract_pack() -> dict:
    return yaml.safe_load((ROOT / "configs" / "report_packs" / "evidence_extract.yaml").read_text(encoding="utf-8"))


def _market_spec() -> dict:
    return dict(_market_pack()["default_report_spec"])


def test_report_spec_contract_accepts_valid_market_weekly_spec() -> None:
    spec = _market_spec()

    assert ReportSpecContract.validate(spec) == []


def test_report_spec_contract_accepts_solar_industry_periodic_spec() -> None:
    spec = dict(_solar_pack()["default_report_spec"])

    assert ReportSpecContract.validate(spec) == []
    assert spec["report_pack"] == "solar_industry_periodic"
    assert spec["policy_profile"] == "solar_manufacturing_default"
    assert spec["audience"]["language"] == "zh-CN"
    assert spec["metadata"]["dogfood_use_case"] == "solar_industry_periodic_report"


def test_report_spec_contract_accepts_evidence_extract_spec() -> None:
    spec = dict(_evidence_extract_pack()["default_report_spec"])

    assert ReportSpecContract.validate(spec) == []
    assert spec["report_pack"] == "evidence_extract"
    assert spec["policy_profile"] == "evidence_extract_default"
    assert spec["source_policy"]["mode"] == "explicit_sources"
    assert "no_legal_conclusion" in spec["metadata"]["non_claims"]
    assert "no_binary_span_extraction" in spec["metadata"]["non_claims"]
    assert "no_semantic_support_assessment" in spec["metadata"]["non_claims"]


def test_report_spec_contract_rejects_control_spine_bypass() -> None:
    spec = _market_spec()
    spec["control_spine"] = dict(spec["control_spine"])
    spec["control_spine"]["quality_gates"] = False
    spec["source_policy"] = dict(spec["source_policy"])
    spec["source_policy"]["hidden_autonomous_crawling"] = True

    violations = ReportSpecContract.validate(spec)

    assert any(item.field == "control_spine.quality_gates" for item in violations)
    assert any(item.field == "source_policy.hidden_autonomous_crawling" for item in violations)


def test_report_spec_registry_validation_rejects_unknown_pack() -> None:
    spec = _market_spec()
    spec["report_pack"] = "missing_pack"
    registry = ReportPackRegistry.from_config_dir(ROOT / "configs" / "report_packs")

    result = validate_report_spec_payload(
        spec,
        known_report_packs=registry.pack_ids(),
        report_type_by_pack=registry.report_type_by_pack(),
    )

    assert not result.ok
    assert any(item.field == "report_pack" for item in result.errors)


def test_report_pack_registry_discovers_root_and_packaged_packs() -> None:
    root_registry = ReportPackRegistry.from_config_dir(ROOT / "configs" / "report_packs")
    package_registry = ReportPackRegistry.from_package()

    for registry in (root_registry, package_registry):
        assert not registry.validation_errors
        assert registry.pack_ids() == EXPECTED_PACK_IDS
        assert registry.get("evidence_extract") is not None
        assert registry.get("market_weekly") is not None
        assert registry.get("management_monthly") is not None
        assert registry.get("solar_industry_periodic") is not None


def test_report_pack_config_parity_between_root_and_package_copy() -> None:
    root_dir = ROOT / "configs" / "report_packs"
    package_dir = ROOT / "src" / "multi_agent_brief" / "configs" / "report_packs"

    for path in sorted(root_dir.glob("*.yaml")):
        package_path = package_dir / path.name
        assert package_path.exists()
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == yaml.safe_load(
            package_path.read_text(encoding="utf-8")
        )


def test_report_pack_payload_validation_rejects_invalid_default_spec() -> None:
    payload = _market_pack()
    payload["default_report_spec"] = dict(payload["default_report_spec"])
    payload["default_report_spec"]["control_spine"] = dict(payload["default_report_spec"]["control_spine"])
    payload["default_report_spec"]["control_spine"]["archive"] = False

    violations = validate_report_pack_payload(payload)

    assert any(item.field == "default_report_spec.control_spine.archive" for item in violations)


def test_report_pack_payload_validation_allows_supported_and_experimental_statuses() -> None:
    supported = _market_pack()
    experimental = _solar_pack()

    assert not [item for item in validate_report_pack_payload(supported) if item.field == "status"]
    assert not [item for item in validate_report_pack_payload(experimental) if item.field == "status"]

    invalid = _market_pack()
    invalid["status"] = "stable"

    violations = validate_report_pack_payload(invalid)

    assert any(item.field == "status" for item in violations)


def test_report_packs_do_not_change_runtime_stage_contracts() -> None:
    registry = ContractRegistry.from_config_dir(ROOT / "configs")

    assert registry.artifact("report_spec") is None
    for stage in registry.stages:
        assert "report_spec" not in stage.produces
        assert "report_spec" not in stage.expected_artifacts
        assert "report_pack" not in stage.produces
        assert "report_pack" not in stage.expected_artifacts


def test_packs_cli_list_and_show_pack(capsys) -> None:
    assert main(["packs", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["ok"] is True
    assert {item["pack_id"] for item in listed["packs"]} == EXPECTED_PACK_IDS
    market = next(item for item in listed["packs"] if item["pack_id"] == "market_weekly")
    assert market["recommended_entry"] == "industry-weekly"
    assert "market-weekly" in market["aliases"]
    assert "industry-weekly" in market["aliases"]

    assert main(["packs", "show", "industry-weekly", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["ok"] is True
    assert shown["pack"]["pack_id"] == "market_weekly"
    assert shown["pack"]["status"] == "supported"
    assert shown["recommended_entry"] == "industry-weekly"
    assert "market_weekly" in shown["aliases"]

    assert main(["packs", "show", "solar_industry_periodic", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["ok"] is True
    assert shown["pack"]["pack_id"] == "solar_industry_periodic"
    assert shown["pack"]["status"] == "experimental"
    assert shown["pack"]["default_policy_profile"] == "solar_manufacturing_default"

    assert main(["packs", "show", "evidence_extract", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["ok"] is True
    assert shown["pack"]["pack_id"] == "evidence_extract"
    assert shown["pack"]["status"] == "supported"
    assert shown["pack"]["default_policy_profile"] == "evidence_extract_default"


def test_packs_show_human_output_matches_pack_support_status(capsys) -> None:
    assert main(["packs", "show", "industry-weekly"]) == 0

    output = capsys.readouterr().out
    assert "status: supported" in output
    assert "boundary: supported product-layer contract" in output
    assert "boundary: experimental product-layer contract only" not in output

    assert main(["packs", "show", "solar-periodic"]) == 0

    output = capsys.readouterr().out
    assert "status: experimental" in output
    assert "boundary: experimental product-layer contract only" in output


def test_validate_report_spec_cli_accepts_valid_spec(tmp_path: Path, capsys) -> None:
    spec_path = tmp_path / "report_spec.yaml"
    spec_path.write_text(yaml.safe_dump(_market_spec(), sort_keys=False), encoding="utf-8")

    assert main(["validate-report-spec", str(spec_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["report_pack"] == "market_weekly"
    assert payload["policy_profile"] == "manufacturing_default"
    assert payload["resolved_policy_profile"] == "manufacturing_default"
    assert payload["policy_profile_source"] == "report_spec.policy_profile"


def test_validate_report_spec_cli_rejects_disabled_spine(tmp_path: Path, capsys) -> None:
    spec = _market_spec()
    spec["control_spine"] = dict(spec["control_spine"])
    spec["control_spine"]["event_log"] = False
    spec_path = tmp_path / "report_spec.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    assert main(["validate-report-spec", str(spec_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(item["field"] == "control_spine.event_log" for item in payload["errors"])


def test_validate_report_spec_cli_rejects_malformed_yaml_without_traceback(
    tmp_path: Path,
    capsys,
) -> None:
    spec_path = tmp_path / "report_spec.yaml"
    spec_path.write_text("schema_version: [\n", encoding="utf-8")

    assert main(["validate-report-spec", str(spec_path), "--json"]) == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["errors"][0]["field"] == str(spec_path)
    assert payload["errors"][0]["severity"] == "error"
    assert "invalid YAML" in payload["errors"][0]["error"]
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_new_report_pack_workspace_creates_local_first_skeleton(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "weekly"

    assert main(["new", "industry-weekly", str(workspace)]) == 0

    output = capsys.readouterr().out
    assert "Created BriefLoop workspace" in output
    assert "briefloop run --workspace" in output
    assert (workspace / "config.yaml").exists()
    assert (workspace / "sources.yaml").exists()
    assert (workspace / "report_spec.yaml").exists()
    assert (workspace / "user.md").exists()
    assert (workspace / ".gitignore").exists()
    assert (workspace / "input" / "sources").is_dir()

    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))
    assert spec["report_pack"] == "market_weekly"
    assert spec["policy_profile"] == "manufacturing_default"
    assert spec["policy_profile_resolution"]["policy_profile"] == "manufacturing_default"
    assert spec["policy_profile_resolution"]["source"] == "report_pack.default_policy_profile"
    assert spec["source_policy"]["mode"] == "local_first"
    assert spec["source_policy"]["hidden_autonomous_crawling"] is False

    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["source_strategy"]["profile"] == "conservative"
    assert sources["source_strategy"]["enabled_providers"] == ["manual"]
    assert sources["web_search"]["enabled"] is False
    assert sources["web_search"]["mode"] == "disabled"


def test_new_report_pack_workspace_accepts_product_aliases(tmp_path: Path, capsys) -> None:
    cases = [
        ("management-monthly", "management_monthly"),
        ("document-review", "evidence_extract"),
        ("solar-periodic", "solar_industry_periodic"),
        ("market-weekly", "market_weekly"),
        ("evidence-extract", "evidence_extract"),
        ("solar-industry-periodic", "solar_industry_periodic"),
    ]
    for entry, expected_pack in cases:
        workspace = tmp_path / entry

        assert main(["new", entry, str(workspace)]) == 0

        capsys.readouterr()
        spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))
        assert spec["report_pack"] == expected_pack


def test_new_report_pack_workspace_overrides_are_written_to_report_spec(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "custom-weekly"

    assert main(
        [
            "new",
            "market-weekly",
            str(workspace),
            "--title",
            "Custom Weekly",
            "--audience",
            "投资委员会",
            "--language",
            "zh-CN",
        ]
    ) == 0
    capsys.readouterr()

    config = yaml.safe_load((workspace / "config.yaml").read_text(encoding="utf-8"))
    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert config["project"]["name"] == "Custom Weekly"
    assert config["project"]["audience"] == "投资委员会"
    assert config["language"]["output"] == "zh-CN"
    assert spec["title"] == "Custom Weekly"
    assert spec["audience"]["label"] == "投资委员会"
    assert spec["audience"]["language"] == "zh-CN"


def test_new_solar_industry_periodic_workspace_uses_solar_defaults(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "solar-weekly"

    assert main(["new", "solar-industry-periodic", str(workspace)]) == 0

    output = capsys.readouterr().out
    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert "report_pack: solar_industry_periodic" in output
    assert "policy_profile: solar_manufacturing_default" in output
    assert spec["report_pack"] == "solar_industry_periodic"
    assert spec["report_type"] == "solar_industry_periodic"
    assert spec["policy_profile"] == "solar_manufacturing_default"
    assert spec["policy_profile_resolution"]["source"] == "report_pack.default_policy_profile"
    assert spec["title"] == "Solar Industry Periodic Report"
    assert spec["audience"] == {
        "label": "management reader",
        "language": "zh-CN",
    }
    assert spec["metadata"]["required_section_intents"] == [
        "executive_summary",
        "supply_chain_price_tracker",
        "demand_installation_outlook",
        "policy_tax_financing",
        "fx_rates_tracker",
        "company_implications",
    ]


def test_new_evidence_extract_workspace_uses_extract_defaults(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "evidence-extract"

    assert main(["new", "evidence-extract", str(workspace)]) == 0

    output = capsys.readouterr().out
    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert "report_pack: evidence_extract" in output
    assert "policy_profile: evidence_extract_default" in output
    assert spec["report_pack"] == "evidence_extract"
    assert spec["report_type"] == "evidence_extract"
    assert spec["policy_profile"] == "evidence_extract_default"
    assert spec["policy_profile_resolution"]["source"] == "report_pack.default_policy_profile"
    assert spec["source_policy"]["mode"] == "explicit_sources"
    assert spec["title"] == "Evidence Extract Brief"
    assert "no_disclosure_readiness" in spec["metadata"]["non_claims"]


def test_new_evidence_extract_workspace_keeps_extract_default_for_other_industry(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "evidence-finance"

    assert main(["new", "evidence-extract", str(workspace), "--industry", "finance"]) == 0

    output = capsys.readouterr().out
    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert "policy_profile: evidence_extract_default" in output
    assert spec["policy_profile"] == "evidence_extract_default"
    assert spec["policy_profile_resolution"]["source"] == "report_pack.default_policy_profile"
    assert spec["policy_profile_resolution"]["matched_rule"] == "specialized_report_pack_default"
    assert spec["policy_profile_resolution"]["alternatives"] == ["finance_default"]


def test_new_solar_industry_periodic_workspace_keeps_solar_default_for_non_solar_industry(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "solar-private-equity"

    assert (
        main(
            [
                "new",
                "solar-industry-periodic",
                str(workspace),
                "--industry",
                "私募股权投资",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert "policy_profile: solar_manufacturing_default" in output
    assert spec["policy_profile"] == "solar_manufacturing_default"
    assert spec["policy_profile_resolution"] == {
        "policy_profile": "solar_manufacturing_default",
        "source": "report_pack.default_policy_profile",
        "input": "私募股权投资",
        "matched_rule": "specialized_report_pack_default",
        "confidence": "default_specialized_pack",
        "alternatives": ["finance_default"],
    }


def test_new_solar_industry_periodic_workspace_allows_explicit_policy_override(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "solar-explicit-finance"

    assert (
        main(
            [
                "new",
                "solar-industry-periodic",
                str(workspace),
                "--industry",
                "私募股权投资",
                "--policy-profile",
                "finance_default",
            ]
        )
        == 0
    )

    capsys.readouterr()
    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert spec["policy_profile"] == "finance_default"
    assert spec["policy_profile_resolution"]["source"] == "explicit_override"


def test_new_report_pack_workspace_resolves_policy_profile_from_industry(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "finance-weekly"

    assert main(["new", "market-weekly", str(workspace), "--industry", "listed company IR"]) == 0

    output = capsys.readouterr().out
    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert "policy_profile: finance_default" in output
    assert "policy_profile_source: industry_resolver" in output
    assert spec["policy_profile"] == "finance_default"
    assert spec["policy_profile_resolution"] == {
        "policy_profile": "finance_default",
        "source": "industry_resolver",
        "input": "listed company IR",
        "matched_rule": "finance_keywords",
        "confidence": "deterministic_exact_or_keyword",
        "alternatives": [],
    }


def test_new_report_pack_workspace_resolves_solar_industry_hint_to_solar_profile(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "solar-market-weekly"

    assert main(["new", "market-weekly", str(workspace), "--industry", "solar manufacturing"]) == 0

    output = capsys.readouterr().out
    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert "policy_profile: solar_manufacturing_default" in output
    assert spec["policy_profile"] == "solar_manufacturing_default"
    assert spec["policy_profile_resolution"] == {
        "policy_profile": "solar_manufacturing_default",
        "source": "industry_resolver",
        "input": "solar manufacturing",
        "matched_rule": "solar_manufacturing_keywords",
        "confidence": "deterministic_exact_or_keyword",
        "alternatives": [],
    }


def test_new_report_pack_workspace_does_not_treat_generic_wafer_as_solar(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "semiconductor-weekly"

    assert main(
        [
            "new",
            "market-weekly",
            str(workspace),
            "--industry",
            "semiconductor wafer manufacturing",
        ]
    ) == 0
    capsys.readouterr()

    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert spec["policy_profile"] == "manufacturing_default"
    assert spec["policy_profile_resolution"] == {
        "policy_profile": "manufacturing_default",
        "source": "industry_resolver",
        "input": "semiconductor wafer manufacturing",
        "matched_rule": "manufacturing_keywords",
        "confidence": "deterministic_exact_or_keyword",
        "alternatives": [],
    }


def test_new_report_pack_workspace_uses_pack_default_for_ambiguous_industry(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "ambiguous-weekly"

    assert main(["new", "market-weekly", str(workspace), "--industry", "solar finance"]) == 0
    capsys.readouterr()

    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert spec["policy_profile"] == "manufacturing_default"
    resolution = spec["policy_profile_resolution"]
    assert resolution["source"] == "report_pack.default_policy_profile"
    assert resolution["matched_rule"] == "ambiguous_industry_keywords"
    assert resolution["confidence"] == "default_ambiguous"
    assert resolution["alternatives"] == ["finance_default", "solar_manufacturing_default"]


def test_new_report_pack_workspace_industry_takes_precedence_over_company_for_policy_profile(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "finance-weekly"

    assert (
        main(
            [
                "new",
                "market-weekly",
                str(workspace),
                "--industry",
                "finance",
                "--company",
                "Industrial Bank",
            ]
        )
        == 0
    )
    capsys.readouterr()

    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert spec["policy_profile"] == "finance_default"
    assert spec["policy_profile_resolution"]["source"] == "industry_resolver"
    assert spec["policy_profile_resolution"]["input"] == "finance"
    assert spec["policy_profile_resolution"]["matched_rule"] == "finance_keywords"


def test_new_report_pack_workspace_explicit_policy_profile_override_wins(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "override-weekly"

    assert main(
        [
            "new",
            "market-weekly",
            str(workspace),
            "--industry",
            "solar manufacturing",
            "--policy-profile",
            "internet_default",
        ]
    ) == 0
    capsys.readouterr()

    spec = yaml.safe_load((workspace / "report_spec.yaml").read_text(encoding="utf-8"))

    assert spec["policy_profile"] == "internet_default"
    assert spec["policy_profile_resolution"]["source"] == "explicit_override"
    assert spec["policy_profile_resolution"]["matched_rule"] == "explicit_policy_profile"


def test_new_report_pack_workspace_rejects_unknown_policy_profile(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "bad-profile"

    assert main(["new", "market-weekly", str(workspace), "--policy-profile", "missing_profile"]) == 1

    output = capsys.readouterr().out
    assert "unknown policy_profile:missing_profile" in output
    assert not workspace.exists()


def test_new_report_pack_workspace_rejects_unknown_pack(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "missing"

    assert main(["new", "missing-pack", str(workspace)]) == 1

    output = capsys.readouterr().out
    assert output.count("[new] ok: False") == 1
    assert "unknown report pack" in output
    assert "industry-weekly" in output
    assert "document-review" in output
    assert "solar-periodic" in output
    assert "market_weekly" in output
    assert not workspace.exists()


def test_new_report_pack_workspace_does_not_overwrite_report_spec_without_force(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "weekly"
    workspace.mkdir()
    (workspace / "report_spec.yaml").write_text("existing: true\n", encoding="utf-8")

    assert main(["new", "market-weekly", str(workspace)]) == 1

    output = capsys.readouterr().out
    assert "Refusing to overwrite existing file" in output
    assert (workspace / "report_spec.yaml").read_text(encoding="utf-8") == "existing: true\n"


def test_briefloop_alias_help_uses_alias_program_name(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["/tmp/briefloop"])

    help_text = build_parser().format_help()

    assert help_text.startswith("usage: briefloop ")
    assert not help_text.startswith("usage: multi-agent-brief ")
