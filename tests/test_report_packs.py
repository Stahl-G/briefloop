"""Tests for experimental product-layer ReportSpec and ReportPack contracts."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import build_parser, main
from multi_agent_brief.contracts.registry import ContractRegistry
from multi_agent_brief.contracts.schemas.report_spec import ReportSpecContract
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.product.report_pack import validate_report_pack_payload
from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import validate_report_spec_payload
from multi_agent_brief.runtime_host_v2.codex import load_codex_adapter_binding
from multi_agent_brief.runtime_host_v2.initialization import initialize_or_open_runtime

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PACK_IDS = {
    "evidence_extract",
    "market_weekly",
    "management_monthly",
    "solar_industry_periodic",
    "solar_stock_periodic",
}


def _market_pack() -> dict:
    return yaml.safe_load(
        (ROOT / "configs" / "report_packs" / "market_weekly.yaml").read_text(
            encoding="utf-8"
        )
    )


def _solar_pack() -> dict:
    return yaml.safe_load(
        (ROOT / "configs" / "report_packs" / "solar_industry_periodic.yaml").read_text(
            encoding="utf-8"
        )
    )


def _evidence_extract_pack() -> dict:
    return yaml.safe_load(
        (ROOT / "configs" / "report_packs" / "evidence_extract.yaml").read_text(
            encoding="utf-8"
        )
    )


def _market_spec() -> dict:
    return dict(_market_pack()["default_report_spec"])








def test_report_spec_contract_rejects_control_spine_bypass() -> None:
    spec = _market_spec()
    spec["control_spine"] = dict(spec["control_spine"])
    spec["control_spine"]["quality_gates"] = False
    spec["source_policy"] = dict(spec["source_policy"])
    spec["source_policy"]["hidden_autonomous_crawling"] = True

    violations = ReportSpecContract.validate(spec)

    assert any(item.field == "control_spine.quality_gates" for item in violations)
    assert any(
        item.field == "source_policy.hidden_autonomous_crawling" for item in violations
    )




def test_report_pack_registry_discovers_root_and_packaged_packs() -> None:
    root_registry = ReportPackRegistry.from_config_dir(
        ROOT / "configs" / "report_packs"
    )
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








def test_packs_cli_list_and_show_pack(capsys) -> None:
    assert main(["packs", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["ok"] is True
    assert {item["pack_id"] for item in listed["packs"]} == EXPECTED_PACK_IDS
    market = next(
        item for item in listed["packs"] if item["pack_id"] == "market_weekly"
    )
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

    assert main(["packs", "show", "equity-periodic", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["ok"] is True
    assert shown["pack"]["pack_id"] == "solar_stock_periodic"
    assert "equity-periodic" in shown["aliases"]
    assert "stock-periodic-report" in shown["aliases"]

    assert main(["packs", "show", "evidence_extract", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["ok"] is True
    assert shown["pack"]["pack_id"] == "evidence_extract"
    assert shown["pack"]["status"] == "supported"
    assert shown["pack"]["default_policy_profile"] == "evidence_extract_default"




def test_validate_report_spec_cli_accepts_valid_spec(tmp_path: Path, capsys) -> None:
    spec_path = tmp_path / "report_spec.yaml"
    spec_path.write_text(
        yaml.safe_dump(_market_spec(), sort_keys=False), encoding="utf-8"
    )

    assert main(["validate-report-spec", str(spec_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["report_pack"] == "market_weekly"
    assert payload["policy_profile"] == "manufacturing_default"
    assert payload["resolved_policy_profile"] == "manufacturing_default"
    assert payload["policy_profile_source"] == "report_spec.policy_profile"


















def test_new_solar_stock_workspace_requires_explicit_core_ticker(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "solar-stock-no-core"

    assert main(["new", "solar-stock-periodic", str(workspace)]) == 1
    output = capsys.readouterr().out
    assert "requires an explicit --core-ticker" in output
    assert "packaged defaults carry no issuer identity" in output
    assert not workspace.exists()


def test_new_solar_stock_workspace_freezes_explicit_workbook_window(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "solar-stock-workbook-window"

    assert (
        main(
            [
                "new",
                "solar-stock-periodic",
                str(workspace),
                "--core-ticker",
                "DEMO",
                "--report-window-start",
                "2026-08-03",
                "--report-window-end",
                "2026-08-12",
            ]
        )
        == 0
    )

    capsys.readouterr()
    config = yaml.safe_load((workspace / "config.yaml").read_text(encoding="utf-8"))
    direction = config["controlstore_v2"]["run_direction"]
    assert direction["report_date"] == "2026-08-12"
    assert direction["report_window_start"] == "2026-08-03"
    assert direction["report_window_end"] == "2026-08-12"
    assert direction["max_source_age_days"] == 9






def test_new_report_pack_workspace_does_not_infer_tavily_from_external_api(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "weekly"

    assert (
        main(
            [
                "new",
                "industry-weekly",
                str(workspace),
                "--web-search-mode",
                "external_api",
            ]
        )
        == 1
    )

    output = capsys.readouterr().out
    assert "requires an explicit --search-backend" in output
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




