from __future__ import annotations

from pathlib import Path

import yaml

from multi_agent_brief.status import build_workspace_status, format_workspace_status


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
