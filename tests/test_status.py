from __future__ import annotations

import json
import hashlib
from pathlib import Path

import yaml

from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    build_semantic_assessment_checked_inputs,
)
from multi_agent_brief.orchestrator.runtime_state import (
    check_runtime_state,
    initialize_runtime_state,
)
from multi_agent_brief.status import build_workspace_status, format_workspace_status
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent


def _write_runtime_workspace(tmp_path: Path) -> Path:
    return write_workspace_files_under(
        tmp_path,
        config_text=(
            'project:\n  name: "Status Intake Test"\n'
            'output:\n  path: "output"\n'
            'input:\n  path: "input"\n'
        ),
        include_input_dir=True,
    )


def _normalized_candidate_wrapper() -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "candidate_id": "CAND-001",
                    "claim_statement": "ExampleCo opened a demo facility.",
                    "source_excerpt": "ExampleCo opened a demo facility in June.",
                    "source_url": "https://example.com/source",
                    "source_category": "industry_news",
                    "published_at": "2026-06-01",
                    "topic": "demo market",
                    "claim_type": "fact",
                    "confidence": 0.91,
                }
            ]
        }
    ) + "\n"


def _span_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_semantic_support_base(ws: Path) -> tuple[Path, str, int]:
    intermediate = ws / "output" / "intermediate"
    source_dir = ws / "input" / "sources"
    intermediate.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    raw_excerpt = "TargetCo opened a demo facility."
    source_text = f"Intro.\n{raw_excerpt}\nOutro.\n"
    (source_dir / "source-001.md").write_text(source_text, encoding="utf-8")
    start = source_text.index(raw_excerpt)
    (intermediate / "claim_ledger.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "CL-0001",
                    "statement": raw_excerpt,
                    "source_id": "SRC-001",
                    "evidence_text": raw_excerpt,
                    "claim_type": "fact",
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
                                "text": raw_excerpt,
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
    (intermediate / "evidence_span_registry.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.evidence_span_registry.v1",
                "sources": [
                    {
                        "source_id": "SRC-001",
                        "source_type": "company_release",
                        "source_path": "input/sources/source-001.md",
                        "published_at": "2026-06-01",
                        "source_tier": "company_official",
                        "spans": [
                            {
                                "span_id": "ESP-001-01",
                                "raw_excerpt": raw_excerpt,
                                "hash": _span_hash(raw_excerpt),
                                "span_role": "direct_statement",
                                "char_start": start,
                                "char_end": start + len(raw_excerpt),
                            }
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return intermediate, raw_excerpt, start


def _semantic_assessment_report_payload(*, atom_id: str = "AC-0001-01") -> dict:
    return {
        "schema_version": "mabw.semantic_assessment_report.v1",
        "assessors": [
            {
                "assessor_id": "ASR-001",
                "assessment_method": "llm_only",
                "label": "Model review",
            }
        ],
        "rows": [
            {
                "row_id": "SAR-0001",
                "claim_id": "CL-0001",
                "atom_id": atom_id,
                "evidence_span_id": "ESP-001-01",
                "proposed_support_label": "partial_support",
                "confidence": 0.51,
                "uncertainty": "high",
                "disagreement": "high",
                "requires_human_adjudication": True,
                "assessment_method": "llm_only",
                "assessor_id": "ASR-001",
                "rationale": "The span supports activity, but not the stronger interpretation.",
            }
        ],
    }


def _write_bound_semantic_assessment_report(intermediate: Path, ws: Path, *, atom_id: str = "AC-0001-01") -> dict:
    (intermediate / "audited_brief.md").write_text("# Audited Brief\n\nTargetCo opened a demo facility.\n", encoding="utf-8")
    payload = _semantic_assessment_report_payload(atom_id=atom_id)
    payload["checked_inputs"] = build_semantic_assessment_checked_inputs(ws)
    (intermediate / "semantic_assessment_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


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


def test_status_derives_claim_support_matrix_projection_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    source_dir = ws / "input" / "sources"
    intermediate.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    raw_excerpt = "TargetCo opened a demo facility."
    source_text = f"Intro.\n{raw_excerpt}\nOutro.\n"
    (source_dir / "source-001.md").write_text(source_text, encoding="utf-8")
    start = source_text.index(raw_excerpt)
    (intermediate / "claim_ledger.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "CL-0001",
                    "statement": raw_excerpt,
                    "source_id": "SRC-001",
                    "evidence_text": raw_excerpt,
                    "claim_type": "fact",
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
                                "text": raw_excerpt,
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
    (intermediate / "evidence_span_registry.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.evidence_span_registry.v1",
                "sources": [
                    {
                        "source_id": "SRC-001",
                        "source_type": "company_release",
                        "source_path": "input/sources/source-001.md",
                        "published_at": "2026-06-01",
                        "source_tier": "company_official",
                        "spans": [
                            {
                                "span_id": "ESP-001-01",
                                "raw_excerpt": raw_excerpt,
                                "hash": _span_hash(raw_excerpt),
                                "span_role": "direct_statement",
                                "char_start": start,
                                "char_end": start + len(raw_excerpt),
                            }
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "claim_support_matrix.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.claim_support_matrix.v1",
                "rows": [
                    {
                        "row_id": "CSM-0001",
                        "claim_id": "CL-0001",
                        "atom_id": "AC-0001-01",
                        "evidence_span_id": None,
                        "support_label": "unsupported",
                        "support_strength": "none",
                        "support_reason": "No span supports the high-materiality atom.",
                        "required_action": "block_release",
                        "repair_owner": "editor",
                        "decision_source": "human",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["claim_support_matrix"]
    assert status["read_only"] is True
    assert projection["status"] == "valid"
    assert projection["summary_counts"]["blocking_atom_count"] == 1
    assert projection["atoms"][0]["verdict"] == "blocking"
    assert "[status] claim_support_matrix: valid blocking_atoms=1" in formatted
    assert not (intermediate / "quality_gate_report.json").exists()
    assert not (intermediate / "event_log.jsonl").exists()


def test_status_derives_semantic_assessment_report_projection_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate, _raw_excerpt, _start = _write_semantic_support_base(ws)
    (intermediate / "semantic_assessment_report.json").write_text(
        json.dumps(_semantic_assessment_report_payload()) + "\n",
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["semantic_assessment_report"]
    counts = projection["summary_counts"]
    assert status["read_only"] is True
    assert projection["status"] == "valid"
    assert projection["checked_inputs_status"] == "missing_checked_inputs"
    assert projection["semantic_boundary"] == "proposal_projection_only_not_accepted_support_truth"
    assert counts["proposal_row_count"] == 1
    assert counts["llm_only_count"] == 1
    assert counts["high_uncertainty_count"] == 1
    assert counts["high_disagreement_count"] == 1
    assert counts["requires_human_adjudication_count"] == 1
    assert projection["proposal_projection"]["proposed_csm_delta"]["accepted_csm_rows"] == []
    assert projection["proposed_claim_support_rows"][0]["accepted_support_truth"] is False
    assert (
        "[status] semantic_assessment_report: valid "
        "boundary=proposal_only not_a_gate not_release_authority "
        "proposals=1 labels=none llm_only=1 "
        "high_uncertainty=1 high_disagreement=1 adjudication=1"
    ) in formatted
    assert not (intermediate / "quality_gate_report.json").exists()
    assert not (intermediate / "event_log.jsonl").exists()


def test_status_reports_fresh_bound_semantic_assessment_report_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate, _raw_excerpt, _start = _write_semantic_support_base(ws)
    _write_bound_semantic_assessment_report(intermediate, ws)

    status = build_workspace_status(ws)

    projection = status["semantic_assessment_report"]
    assert status["read_only"] is True
    assert projection["status"] == "valid"
    assert projection["checked_inputs_status"] == "fresh"
    assert projection["checked_inputs_digest"]
    assert projection["report_sha256"]
    assert projection["summary_counts"]["proposal_row_count"] == 1
    assert not (intermediate / "event_log.jsonl").exists()


def test_status_reports_stale_bound_semantic_assessment_report_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate, _raw_excerpt, _start = _write_semantic_support_base(ws)
    _write_bound_semantic_assessment_report(intermediate, ws)
    (intermediate / "audited_brief.md").write_text("# Edited after assessment\n", encoding="utf-8")

    status = build_workspace_status(ws)

    projection = status["semantic_assessment_report"]
    assert status["read_only"] is True
    assert projection["status"] == "stale"
    assert projection["checked_inputs_status"] == "stale"
    assert projection["reason"] == "checked_input_stale:audited_brief"
    assert projection["summary_counts"]["proposal_row_count"] == 1
    assert not (intermediate / "event_log.jsonl").exists()


def test_status_treats_null_checked_inputs_as_unbound_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate, _raw_excerpt, _start = _write_semantic_support_base(ws)
    _write_bound_semantic_assessment_report(intermediate, ws)
    report_path = intermediate / "semantic_assessment_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["checked_inputs"] = None
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    status = build_workspace_status(ws)

    projection = status["semantic_assessment_report"]
    assert projection["status"] == "valid"
    assert projection["checked_inputs_status"] == "missing_checked_inputs"
    assert projection["reason"] is None
    assert not (intermediate / "event_log.jsonl").exists()


def test_status_reports_non_file_checked_input_without_crashing(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate, _raw_excerpt, _start = _write_semantic_support_base(ws)
    _write_bound_semantic_assessment_report(intermediate, ws)
    brief_path = intermediate / "audited_brief.md"
    brief_path.unlink()
    brief_path.mkdir()

    status = build_workspace_status(ws)

    projection = status["semantic_assessment_report"]
    assert projection["status"] == "missing_input"
    assert projection["checked_inputs_status"] == "missing_input"
    assert projection["reason"] == "checked_input_not_file:audited_brief"
    assert not (intermediate / "event_log.jsonl").exists()


def test_status_reports_invalid_semantic_assessment_report_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate, _raw_excerpt, _start = _write_semantic_support_base(ws)
    (intermediate / "semantic_assessment_report.json").write_text(
        json.dumps(_semantic_assessment_report_payload(atom_id="AC-0001-99")) + "\n",
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["semantic_assessment_report"]
    assert status["read_only"] is True
    assert projection["status"] == "invalid_report"
    assert projection["reason"] == "semantic_assessment_report_validation_error:unknown_atom_reference:AC-0001-99"
    assert projection["proposal_projection"]["status"] == "not_available"
    assert (
        "[status] semantic_assessment_report: invalid_report "
        "boundary=proposal_only not_a_gate not_release_authority proposals=0"
    ) in formatted
    assert not (intermediate / "quality_gate_report.json").exists()
    assert not (intermediate / "event_log.jsonl").exists()


def test_status_formats_intake_projection(tmp_path: Path) -> None:
    ws = _write_runtime_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    candidate_path = ws / "output" / "intermediate" / "candidate_claims.json"
    candidate_path.write_text(_normalized_candidate_wrapper(), encoding="utf-8")
    check_runtime_state(workspace=ws, repo_workdir=ROOT)

    before = build_workspace_status(ws)
    before_intake = before["artifacts"]["intake"]
    before_line = next(
        line for line in format_workspace_status(before).splitlines() if line.startswith("[status] intake:")
    )
    candidate_path.write_text("{not current registry bytes", encoding="utf-8")
    after = build_workspace_status(ws)
    after_intake = after["artifacts"]["intake"]
    after_line = next(
        line for line in format_workspace_status(after).splitlines() if line.startswith("[status] intake:")
    )

    assert before_intake == after_intake
    assert before_line == after_line
    assert before_intake["present"] is True
    assert before_intake["valid"] is True
    assert before_intake["projection_count"] == 1
    assert before_intake["normalized_artifact_count"] == 1
    assert before_intake["normalization_count"] > 0
    assert before_intake["fatal_finding_count"] == 0
    assert before_intake["artifacts"][0]["artifact_id"] == "candidate_claims"
    assert before_intake["artifacts"][0]["projection_valid"] is True


def test_status_rejects_invalid_intake_projection(tmp_path: Path) -> None:
    ws = _write_runtime_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    candidate_path = ws / "output" / "intermediate" / "candidate_claims.json"
    candidate_path.write_text(_normalized_candidate_wrapper(), encoding="utf-8")
    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    registry_path = ws / "output" / "intermediate" / "artifact_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    persisted = registry["artifacts"]["candidate_claims"]["intake_projection"]
    persisted_normalization_count = persisted["normalization_count"]
    persisted["schema_version"] = "briefloop.intake_projection.v99"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    candidate_path.write_text("{malformed current bytes", encoding="utf-8")

    status = build_workspace_status(ws)
    intake = status["artifacts"]["intake"]

    assert intake["present"] is True
    assert intake["valid"] is False
    assert intake["invalid_projection_count"] == 1
    assert intake["normalization_count"] == persisted_normalization_count
    assert intake["fatal_finding_count"] == 0
    assert intake["artifacts"][0]["projection_valid"] is False
    assert intake["artifacts"][0]["reasons"] == [
        "intake_projection schema_version is unsupported"
    ]
    assert "[status] intake: invalid" in format_workspace_status(status)
