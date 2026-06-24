from __future__ import annotations

import json
import hashlib
from pathlib import Path

import yaml

from multi_agent_brief.status import build_workspace_status, format_workspace_status


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
            "Prices.",
            "## Demand Installation Outlook",
            "Demand.",
            "## Policy Tax Financing",
            "Policy.",
            "## FX Rates Tracker",
            "Rates.",
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
    assert projection["semantic_boundary"] == "proposal_projection_only_not_accepted_support_truth"
    assert counts["proposal_row_count"] == 1
    assert counts["llm_only_count"] == 1
    assert counts["high_uncertainty_count"] == 1
    assert counts["high_disagreement_count"] == 1
    assert counts["requires_human_adjudication_count"] == 1
    assert projection["proposal_projection"]["proposed_csm_delta"]["accepted_csm_rows"] == []
    assert projection["proposed_claim_support_rows"][0]["accepted_support_truth"] is False
    assert (
        "[status] semantic_assessment_report: valid boundary=proposal_only proposals=1 llm_only=1 "
        "high_uncertainty=1 high_disagreement=1 adjudication=1"
    ) in formatted
    assert not (intermediate / "quality_gate_report.json").exists()
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
    assert "[status] semantic_assessment_report: invalid_report boundary=proposal_only proposals=0" in formatted
    assert not (intermediate / "quality_gate_report.json").exists()
    assert not (intermediate / "event_log.jsonl").exists()
