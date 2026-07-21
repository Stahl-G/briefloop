from __future__ import annotations

import json
from pathlib import Path

import yaml

from multi_agent_brief.status import build_workspace_status, format_workspace_status
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent














def _market_report_spec(*, policy_profile: str | None = "finance_default") -> dict:
    spec = {
        "schema_version": "briefloop.report_spec.v1",
        "report_pack": "market_weekly",
        "report_type": "market_weekly",
        "title": "Market Weekly Brief",
        "cadence": "weekly",
        "audience": {"label": "business reader", "language": "en-US"},
        "source_policy": {"mode": "local_first", "hidden_autonomous_crawling": False},
        "control_spine": {
            "claim_ledger": True,
            "artifact_registry": True,
            "quality_gates": True,
            "event_log": True,
            "archive": True,
            "source_appendix": True,
            "support_records": True,
            "human_delivery_approval": True,
            "frozen_artifact_integrity": True,
        },
        "outputs": ["markdown", "docx"],
    }
    if policy_profile is not None:
        spec["policy_profile"] = policy_profile
    return spec


def _solar_report_spec() -> dict:
    return {
        "schema_version": "briefloop.report_spec.v1",
        "report_pack": "solar_industry_periodic",
        "policy_profile": "solar_manufacturing_default",
        "report_type": "solar_industry_periodic",
        "title": "Solar Industry Periodic Report",
        "cadence": "weekly",
        "audience": {"label": "management reader", "language": "zh-CN"},
        "source_policy": {"mode": "local_first", "hidden_autonomous_crawling": False},
        "control_spine": {
            "claim_ledger": True,
            "artifact_registry": True,
            "quality_gates": True,
            "event_log": True,
            "archive": True,
            "source_appendix": True,
            "support_records": True,
            "human_delivery_approval": True,
            "frozen_artifact_integrity": True,
        },
        "outputs": ["markdown", "docx"],
    }


def test_status_projects_resolved_policy_profile_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_market_report_spec(policy_profile="finance_default"), sort_keys=False),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["policy_profile"]
    assert projection["status"] == "resolved"
    assert projection["resolved_policy_profile"] == "finance_default"
    assert projection["source"] == "report_spec.policy_profile"
    assert projection["runtime_effect"] == "none"
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()
    assert "[status] policy_profile: resolved" in formatted
    assert "id=finance_default" in formatted
    assert "runtime_effect=none" in formatted


def test_status_projects_report_template_section_order_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_solar_report_spec(), sort_keys=False),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["report_template"]
    assert projection["status"] == "resolved"
    assert projection["template_id"] == "solar_industry_periodic"
    assert projection["section_count"] == 8
    assert projection["section_order"][0] == "cover"
    assert "supply_chain_price_tracker" in projection["section_order"]
    assert projection["reader_contract"]["citation_profile"] == "executive"
    assert projection["runtime_effect"] == "none"
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()
    assert "[status] report_template: resolved" in formatted
    assert "id=solar_industry_periodic" in formatted
    assert "sections=8" in formatted
    assert "runtime_effect=none" in formatted


def test_status_projects_report_template_conformance_for_audited_brief(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_solar_report_spec(), sort_keys=False),
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "# Cover",
            "Intro.",
            "## Executive Summary",
            "Summary.",
            "### Key Takeaways",
            "Nested heading.",
            "## Supply Chain Price Tracker",
            "| Item | Value |",
            "| --- | --- |",
            "| Module | 1.00 |",
            "## Demand Installation Outlook",
            "Demand.",
            "## Policy Tax Financing",
            "Policy.",
            "## FX Rates Tracker",
            "| Item | Value |",
            "| --- | --- |",
            "| USD/CNY | 7.20 |",
            "## Company Implications",
            "Implications.",
            "## Source Appendix",
            "Sources.",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["report_template_conformance"]
    assert projection["status"] == "pass"
    assert projection["runtime_effect"] == "none"
    target = next(
        item for item in projection["targets"]
        if item["target_artifact"] == "output/intermediate/audited_brief.md"
    )
    assert target["status"] == "pass"
    assert target["missing_sections"] == []
    assert target["out_of_order_sections"] == []
    assert target["extra_headings"] == []
    assert target["nested_heading_count"] == 1
    assert "[status] report_template_conformance: pass" in formatted
    assert "runtime_effect=none" in formatted


def test_status_projects_report_template_render_plan_for_audited_brief(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_solar_report_spec(), sort_keys=False),
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "# Solar Industry Periodic Report",
            "Title.",
            "## Executive Summary",
            "Summary.",
            "## Supply Chain Price Tracker",
            "| Item | Value |",
            "| --- | --- |",
            "| Module | 1.00 |",
            "## Demand Installation Outlook",
            "Demand.",
            "## Policy Tax Financing",
            "Policy.",
            "## FX Rates Tracker",
            "| Item | Value |",
            "| --- | --- |",
            "| USD/CNY | 7.20 |",
            "## Company Implications",
            "Implications.",
            "## Source Appendix",
            "Sources.",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["report_template_render_plan"]
    assert projection["status"] == "planned"
    assert projection["runtime_effect"] == "none"
    assert projection["selected_source_artifact"] == "output/intermediate/audited_brief.md"
    assert projection["source_artifact_candidates"][0]["selected"] is True
    assert projection["section_plan"][1] == {
        "section": "executive_summary",
        "order": 2,
        "status": "matched",
        "matched_heading": "Executive Summary",
        "line": 3,
        "level": 2,
    }
    assert projection["unresolved_sections"] == []
    assert projection["planned_delivery_targets"] == [
        {"artifact": "output/brief.md", "kind": "reader_markdown", "concrete": "true"},
        {"artifact": "output/delivery/brief.md", "kind": "delivery_markdown", "concrete": "true"},
        {"artifact": "output/brief.docx", "kind": "reader_docx", "concrete": "true"},
        {
            "artifact": "output/delivery/<named-output>.docx",
            "artifact_pattern": "output/delivery/<named-output>.docx",
            "kind": "delivery_docx",
            "concrete": "false",
            "filename_source": "unknown_without_config",
        },
    ]
    assert "[status] report_template_render_plan: planned" in formatted
    assert "source=output/intermediate/audited_brief.md" in formatted
    assert "runtime_effect=none" in formatted
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()


def test_status_render_plan_degrades_on_malformed_config(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (ws / "config.yaml").write_text("project: [\n", encoding="utf-8")
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_solar_report_spec(), sort_keys=False),
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "# Solar Industry Periodic Report",
            "Title.",
            "## Executive Summary",
            "Summary.",
            "## Supply Chain Price Tracker",
            "| Item | Value |",
            "| --- | --- |",
            "| Module | 1.00 |",
            "## Demand Installation Outlook",
            "Demand.",
            "## Policy Tax Financing",
            "Policy.",
            "## FX Rates Tracker",
            "| Item | Value |",
            "| --- | --- |",
            "| USD/CNY | 7.20 |",
            "## Company Implications",
            "Implications.",
            "## Source Appendix",
            "Sources.",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)

    projection = status["report_template_render_plan"]
    assert projection["status"] == "planned"
    assert projection["planned_delivery_targets"][3] == {
        "artifact": "output/delivery/<named-output>.docx",
        "artifact_pattern": "output/delivery/<named-output>.docx",
        "kind": "delivery_docx",
        "concrete": "false",
        "filename_source": "unknown_without_config",
    }


def test_status_template_conformance_ignores_source_appendix_child_headings(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    output = ws / "output"
    output.mkdir(parents=True)
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_solar_report_spec(), sort_keys=False),
        encoding="utf-8",
    )
    (output / "brief.md").write_text(
        "\n".join([
            "# Solar Industry Periodic Report",
            "Title.",
            "## Executive Summary",
            "Summary.",
            "## Supply Chain Price Tracker",
            "| Item | Value |",
            "| --- | --- |",
            "| Module | 1.00 |",
            "## Demand Installation Outlook",
            "Demand.",
            "## Policy Tax Financing",
            "Policy.",
            "## FX Rates Tracker",
            "| Item | Value |",
            "| --- | --- |",
            "| USD/CNY | 7.20 |",
            "## Company Implications",
            "Implications.",
            "# Source Appendix",
            "Generated appendix.",
            "## Sources",
            "Generated source list.",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    projection = status["report_template_conformance"]
    target = next(
        item for item in projection["targets"]
        if item["target_artifact"] == "output/brief.md"
    )

    assert projection["status"] == "pass"
    assert target["status"] == "pass"
    assert target["missing_sections"] == []
    assert target["out_of_order_sections"] == []
    assert target["extra_headings"] == []
    assert target["nested_heading_count"] == 1


def test_status_reader_template_conformance_warns_on_reader_contract(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    delivery = ws / "output" / "delivery"
    delivery.mkdir(parents=True)
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_market_report_spec(policy_profile="manufacturing_default"), sort_keys=False),
        encoding="utf-8",
    )
    long_summary = " ".join(["signal"] * 230)
    (delivery / "brief.md").write_text(
        "\n".join([
            "# Market Weekly Brief",
            "Title.",
            "## Executive Summary",
            long_summary,
            "## Market Signals",
            "Signals paragraph without a required table.",
            "## Demand and Supply",
            "Demand.",
            "## Competitor Moves",
            "Competitors.",
            "## Policy and Regulatory",
            "Policy.",
            "## Source Appendix",
            "Sources.",
            "## Risks and Watchlist",
            "Risks paragraph without a required table.",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["report_template_conformance"]
    target = next(
        item for item in projection["targets"]
        if item["target_artifact"] == "output/delivery/brief.md"
    )
    warning_types = {
        item["type"]
        for item in target["reader_block_warnings"]
    }

    assert projection["status"] == "warning"
    assert target["reader_contract_applied"] is True
    assert {
        "executive_summary_too_long",
        "missing_table_slot",
        "source_appendix_not_last",
    }.issubset(warning_types)
    assert projection["summary_counts"]["reader_block_warning_count"] >= 4
    assert projection["summary_counts"]["missing_table_slot_count"] == 2
    assert projection["summary_counts"]["overlong_executive_summary_count"] == 1
    assert projection["summary_counts"]["source_appendix_position_warning_count"] == 1
    assert "reader_contract_warnings=" in formatted


def test_status_matches_chinese_report_template_section_aliases(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (ws / "config.yaml").write_text(
        yaml.safe_dump({"project": {"company": "Example Solar"}}, sort_keys=False),
        encoding="utf-8",
    )
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_solar_report_spec(), sort_keys=False),
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "# 封面",
            "封面内容。",
            "## 执行摘要",
            "摘要。",
            "### 关键结论",
            "子标题不应触发额外主标题 warning。",
            "## 供应链价格跟踪",
            "价格。",
            "## 中美欧需求与装机展望",
            "需求。",
            "## 政策、税务与融资",
            "政策。",
            "## 汇率跟踪",
            "汇率。",
            "## 公司影响",
            "启示。",
            "## 来源附录",
            "来源。",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["report_template_conformance"]
    target = next(
        item for item in projection["targets"]
        if item["target_artifact"] == "output/intermediate/audited_brief.md"
    )
    assert projection["status"] == "pass"
    assert target["matched_sections"] == [
        "cover",
        "executive_summary",
        "supply_chain_price_tracker",
        "demand_installation_outlook",
        "policy_tax_financing",
        "fx_rates_tracker",
        "company_implications",
        "source_appendix",
    ]
    assert target["missing_sections"] == []
    assert target["extra_headings"] == []
    assert target["nested_heading_count"] == 1
    assert "[status] report_template_conformance: pass" in formatted


def test_status_report_template_treats_report_title_h1_as_cover_section(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    spec = _solar_report_spec()
    spec["title"] = "Example Solar 太阳能行业定期报告"
    (ws / "config.yaml").write_text(
        yaml.safe_dump({"project": {"company": "Example Solar"}}, sort_keys=False),
        encoding="utf-8",
    )
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(spec, sort_keys=False),
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "# Example Solar 太阳能行业定期报告",
            "标题页。",
            "## 核心摘要",
            "摘要。",
            "## 供应链价格追踪",
            "价格。",
            "## 需求与装机展望",
            "需求。",
            "## 政策、税务与融资",
            "政策。",
            "## 汇率追踪",
            "汇率。",
            "## 对 Example Solar 的启示",
            "启示。",
            "## 来源附录",
            "来源。",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    projection = status["report_template_conformance"]
    target = next(
        item for item in projection["targets"]
        if item["target_artifact"] == "output/intermediate/audited_brief.md"
    )

    assert projection["status"] == "pass"
    assert target["matched_sections"][0] == "cover"
    assert target["missing_sections"] == []
    assert target["extra_headings"] == []


def test_status_reports_report_template_conformance_warnings(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_solar_report_spec(), sort_keys=False),
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "## Executive Summary",
            "Summary.",
            "## Cover",
            "Cover.",
            "## Unplanned Commentary",
            "Extra.",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["report_template_conformance"]
    assert projection["status"] == "warning"
    target = next(
        item for item in projection["targets"]
        if item["target_artifact"] == "output/intermediate/audited_brief.md"
    )
    assert target["status"] == "warning"
    assert "cover" in target["out_of_order_sections"]
    assert "supply_chain_price_tracker" in target["missing_sections"]
    assert "Unplanned Commentary" in target["extra_headings"]
    assert "[status] report_template_conformance: warning" in formatted
    assert "missing_sections=" in formatted
    assert "boundary=projection_only" in formatted


def test_status_reports_extra_top_level_h1_as_conformance_warning(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_market_report_spec(policy_profile="manufacturing_default"), sort_keys=False),
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "# Market Weekly Brief",
            "Title.",
            "# Unplanned Commentary",
            "Unexpected top-level section.",
            "## Executive Summary",
            "Summary.",
            "## Market Signals",
            "Signals.",
            "## Demand and Supply",
            "Demand.",
            "## Competitor Moves",
            "Competitors.",
            "## Policy and Regulatory",
            "Policy.",
            "## Risks and Watchlist",
            "Risks.",
            "## Source Appendix",
            "Sources.",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    projection = status["report_template_conformance"]
    target = next(
        item for item in projection["targets"]
        if item["target_artifact"] == "output/intermediate/audited_brief.md"
    )

    assert projection["status"] == "warning"
    assert target["status"] == "warning"
    assert target["missing_sections"] == []
    assert target["out_of_order_sections"] == []
    assert target["extra_headings"] == ["Unplanned Commentary"]


def test_status_does_not_match_nested_headings_as_template_sections(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(_market_report_spec(policy_profile="manufacturing_default"), sort_keys=False),
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "## Executive Summary",
            "Summary.",
            "### Market Signals",
            "Nested.",
            "### Demand And Supply",
            "Nested.",
            "### Competitor Moves",
            "Nested.",
            "### Policy And Regulatory",
            "Nested.",
            "### Risks And Watchlist",
            "Nested.",
            "### Source Appendix",
            "Nested.",
        ]),
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    projection = status["report_template_conformance"]
    target = next(
        item for item in projection["targets"]
        if item["target_artifact"] == "output/intermediate/audited_brief.md"
    )

    assert projection["status"] == "warning"
    assert target["status"] == "warning"
    assert target["matched_sections"] == ["executive_summary"]
    assert "market_signals" in target["missing_sections"]
    assert "source_appendix" in target["missing_sections"]
    assert target["extra_headings"] == []
    assert target["nested_heading_count"] == 6


def test_status_derives_atomic_reader_projection_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "claim_ledger.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "CL-0001",
                    "statement": "TargetCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Evidence.",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "atomic_claim_graph.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.atomic_claim_graph.v1",
                "claims": [
                    {
                        "claim_id": "CL-0001",
                        "atoms": [
                            {
                                "atom_id": "AC-0001-01",
                                "text": "TargetCo opened a demo facility.",
                                "claim_role": "observed_fact",
                                "materiality": "high",
                            }
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "TargetCo opened a demo facility. AC-0001-01 [src:CL-0001]\n",
        encoding="utf-8",
    )

    status = build_workspace_status(ws)

    projection = status["atomic_reader_projection"]["audited_brief"]
    assert status["read_only"] is True
    assert projection["status"] == "warning"
    assert projection["summary_counts"]["atom_residue_count"] == 1
    assert projection["claim_citation_coverage"]["cited_graph_claim_ids"] == ["CL-0001"]
    assert not (intermediate / "quality_gate_report.json").exists()


















