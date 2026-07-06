from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state, runtime_state_paths
from multi_agent_brief.orchestrator.runtime_state.completion_gates import (
    _finalize_report_delivery_artifact_reasons,
    _finalize_report_reader_artifact_paths,
)
from multi_agent_brief.outputs.finalize import (
    finalize_reader_outputs,
    interpret_finalize_audit_binding,
    require_finalize_audit_binding_pass,
)
from tests.helpers import sha256_file as _sha256_file

ROOT = Path(__file__).resolve().parents[1]


def _docx_text(path: Path) -> str:
    docx = pytest.importorskip("docx", reason="python-docx not installed")
    document = docx.Document(str(path))
    paragraphs = "\n".join(p.text for p in document.paragraphs)
    tables = "\n".join(
        cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    )
    return paragraphs + "\n" + tables


def _write_claim_ledger(path: Path) -> None:
    claims = [
        {
            "claim_id": "SYN_CLAIM_001",
            "statement": "ExampleCo opened a public demo facility in June 2026.",
            "source_id": "SYN_SRC_001",
            "evidence_text": "Full synthetic evidence text must not render.",
            "source_url": "https://example.com/exampleco-demo",
            "source_type": "web_search",
            "metadata": {
                "source_title": "ExampleCo Opens Demo Facility",
                "publisher": "Example News",
                "published_at": "2026-06-01",
                "source_category": "news_media",
            },
        },
        {
            "claim_id": "SYN_CLAIM_002",
            "statement": "ExampleCo reported Q1 revenue.",
            "source_id": "SYN_SRC_002",
            "evidence_text": "Second evidence text must not render.",
            "source_url": "https://example.com/q1-results",
            "source_type": "filing",
            "metadata": {
                "source_title": "Q1 Results",
                "publisher": "ExampleCo",
                "published_at": "2026-05-15",
                "source_category": "company_press_release",
            },
        },
        {
            "claim_id": "SYN_CLAIM_UNUSED",
            "statement": "Unused claim must not appear.",
            "source_id": "SYN_SRC_UNUSED",
            "evidence_text": "Unused evidence must not render.",
            "source_url": "https://example.com/unused",
            "metadata": {
                "source_title": "Unused Source",
                "publisher": "Example News",
                "source_category": "news_media",
            },
        },
    ]
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_single_claim_ledger(path: Path, *, claim_id: str = "CL-001") -> None:
    claims = [
        {
            "claim_id": claim_id,
            "statement": "ExampleCo opened a public demo facility in June 2026.",
            "source_id": "SRC-001",
            "evidence_text": "ExampleCo opened a public demo facility in June 2026.",
            "source_url": "https://example.com/exampleco-demo",
            "source_type": "web_search",
            "metadata": {
                "source_title": "ExampleCo Opens Demo Facility",
                "publisher": "Example News",
                "published_at": "2026-06-01",
                "source_category": "news_media",
            },
        }
    ]
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_report_spec(workspace: Path, *, policy_profile: str = "finance_default") -> None:
    (workspace / "report_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "briefloop.report_spec.v1",
                "report_pack": "market_weekly",
                "policy_profile": policy_profile,
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
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _span_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_evidence_span_registry(output_dir: Path, *, raw_excerpt: str | None = None) -> None:
    raw_excerpt = raw_excerpt or "ExampleCo opened a public demo facility in June 2026."
    source_dir = output_dir.parent / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_text = f"Intro.\n{raw_excerpt}\nOutro.\n"
    (source_dir / "source-001.md").write_text(source_text, encoding="utf-8")
    start = source_text.index(raw_excerpt)
    intermediate = output_dir / "intermediate"
    (intermediate / "evidence_span_registry.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.evidence_span_registry.v1",
                "sources": [
                    {
                        "source_id": "SRC-001",
                        "source_type": "local_file",
                        "source_tier": "primary",
                        "source_path": "input/sources/source-001.md",
                        "retrieved_at": "2026-06-02",
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
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_audit_control_chain(intermediate: Path) -> None:
    ledger = intermediate / "claim_ledger.json"
    audited = intermediate / "audited_brief.md"
    audit_report = intermediate / "audit_report.json"
    ledger_sha = _sha256_file(ledger)
    audited_sha = _sha256_file(audited)
    audit_sha = _sha256_file(audit_report)
    (intermediate / "artifact_registry.json").write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-artifact-registry/v1",
                "run_id": "run-test",
                "updated_at": "2026-06-11T00:00:00+00:00",
                "artifacts": {
                    "claim_ledger": {
                        "artifact_id": "claim_ledger",
                        "path": "output/intermediate/claim_ledger.json",
                        "status": "valid",
                        "sha256": ledger_sha,
                    },
                    "audited_brief": {
                        "artifact_id": "audited_brief",
                        "path": "output/intermediate/audited_brief.md",
                        "status": "valid",
                        "sha256": audited_sha,
                    },
                    "audit_report": {
                        "artifact_id": "audit_report",
                        "path": "output/intermediate/audit_report.json",
                        "status": "valid",
                        "sha256": audit_sha,
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-workflow-state/v1",
                "current_stage": "finalize",
                "stage_statuses": {
                    "auditor": {
                        "status": "complete",
                        "reason": "auditor passed",
                        "updated_at": "2026-06-11T00:00:00+00:00",
                        "metadata": {
                            "upstream_artifact_sha256": {
                                "claim_ledger": ledger_sha,
                                "audited_brief": audited_sha,
                            },
                            "produced_artifact_sha256": {
                                "audit_report": audit_sha,
                            },
                        },
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_runtime_manifest(output_dir: Path, *, run_id: str = "mabw-run-test") -> None:
    manifest_path = output_dir / "intermediate" / "runtime_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-runtime-manifest/v1",
                "run_id": run_id,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _passing_audit_payload(**overrides) -> dict:
    payload = {
        "audit_status": "pass",
        "audit_score": 100,
        "passed": True,
        "recommendation": "approve",
        "summary": "CL-001 is ready for delivery.",
        "findings": [],
    }
    payload.update(overrides)
    return payload


def test_finalize_audit_binding_interpreter_rejects_pass_status_with_stale_hash(tmp_path: Path):
    ws = tmp_path
    output_dir = ws / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    ledger = intermediate / "claim_ledger.json"
    audited = intermediate / "audited_brief.md"
    audit_report = intermediate / "audit_report.json"
    _write_single_claim_ledger(ledger)
    audited.write_text("# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n", encoding="utf-8")
    audit_report.write_text(json.dumps(_passing_audit_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "audit_binding": {
            "status": "pass",
            "claim_ledger_sha256": "0" * 64,
            "audited_brief_sha256": _sha256_file(audited),
            "audit_report_sha256": _sha256_file(audit_report),
        }
    }

    verdict = interpret_finalize_audit_binding(workspace=ws, finalize_report=report)

    assert verdict.kind == "degraded"
    assert require_finalize_audit_binding_pass(verdict) == [
        "finalize_report.json audit_binding.claim_ledger_sha256 does not match current artifact bytes."
    ]


def test_finalize_audit_binding_interpreter_rejects_pass_status_with_findings(tmp_path: Path):
    ws = tmp_path
    output_dir = ws / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    ledger = intermediate / "claim_ledger.json"
    audited = intermediate / "audited_brief.md"
    audit_report = intermediate / "audit_report.json"
    _write_single_claim_ledger(ledger)
    audited.write_text("# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n", encoding="utf-8")
    audit_report.write_text(json.dumps(_passing_audit_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "audit_binding": {
            "status": "pass",
            "claim_ledger_sha256": _sha256_file(ledger),
            "audited_brief_sha256": _sha256_file(audited),
            "audit_report_sha256": _sha256_file(audit_report),
            "findings": [{"kind": "audit_binding_mismatch"}],
        }
    }

    verdict = interpret_finalize_audit_binding(workspace=ws, finalize_report=report)

    assert verdict.kind == "degraded"
    assert require_finalize_audit_binding_pass(verdict) == [
        "finalize_report.json audit_binding.findings must be empty when audit_binding.status is pass."
    ]


def test_finalize_regenerates_reader_outputs_from_audited_brief(tmp_path: Path):
    """Subagent-updated audited_brief.md must be the single source for final delivery."""
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# 上能电气 电力设备市场周报\n\n"
        "- 美国政策出现变化 [src:POLICY_123456]\n"
        "- 市场需求增长 5% [src:MARKET_ABCDEF]\n",
        encoding="utf-8",
    )

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="上能电气 电力设备市场周报",
        output_formats=["markdown", "docx"],
        output_named_outputs=True,
        output_filename_template="{project_name}_{report_date}",
        output_filename_tokens={"project_name": "上能电气_电力设备周报", "report_date": "2026-06-06"},
    )

    reader = (output_dir / "brief.md").read_text(encoding="utf-8")
    named = output_dir / "上能电气_电力设备周报_2026-06-06.md"

    assert "[src:" in audited.read_text(encoding="utf-8")
    assert "[src:" not in reader
    assert named.exists()
    assert "[src:" not in named.read_text(encoding="utf-8")
    assert reader == named.read_text(encoding="utf-8")
    assert result.stripped_src_marker_count == 2

    docx_path = output_dir / "brief.docx"
    assert docx_path.exists()
    assert "[src:" not in _docx_text(docx_path)
    assert "[src:" not in _docx_text(output_dir / "上能电气_电力设备周报_2026-06-06.docx")
    assert (output_dir / "delivery" / "brief.md").exists()
    assert (output_dir / "delivery" / "上能电气_电力设备周报_2026-06-06.docx").exists()
    assert not (intermediate / "finalize_candidate").exists()
    assert not any(
        path.name.startswith(".briefloop-finalize-candidate-")
        for path in intermediate.iterdir()
    )
    assert not (output_dir / "delivery" / "source_appendix.md").exists()
    assert not (output_dir / "delivery" / "claim_ledger.json").exists()
    assert result.delivery_artifacts == [
        str(output_dir / "delivery" / "brief.md"),
        str(output_dir / "delivery" / "上能电气_电力设备周报_2026-06-06.docx"),
    ]


def test_finalize_applies_report_template_order_before_delivery(tmp_path: Path):
    workspace = tmp_path / "workspace"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    _write_report_spec(workspace)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "\n".join([
            "# Market Weekly Brief",
            "Opening note.",
            "## Demand and Supply",
            "Demand should move after market signals.",
            "## Executive Summary",
            "Summary should render first.",
            "## Market Signals",
            "Signals should render second.",
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

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="Market Weekly Brief",
        output_formats=["markdown"],
        output_named_outputs=False,
        workspace_dir=workspace,
    )

    reader = (output_dir / "delivery" / "brief.md").read_text(encoding="utf-8")
    assert reader.index("## Executive Summary") < reader.index("## Market Signals")
    assert reader.index("## Market Signals") < reader.index("## Demand and Supply")
    assert result.template_rendering["status"] == "rendered"
    assert result.template_rendering["template_id"] == "market_weekly"
    assert result.template_rendering["out_of_order_sections"] == [
        "executive_summary",
        "market_signals",
    ]
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["template_rendering"]["status"] == "rendered"
    assert report["template_rendering"]["blocking"] is False
    assert report["report_template_conformance"]["runtime_effect"] == "none"
    assert report["report_template_conformance"]["status"] == "warning"
    assert report["report_template_conformance"]["summary_counts"]["reader_block_warning_count"] > 0
    assert report["citation_profile"] == "executive"
    assert report["citation_profile_source"] == "report_template.reader_contract.citation_profile"
    assert report["citation_profile_runtime_effect"] == "citation_profile_resolution_only"
    assert report["citation_profile_delivery_exposes_internal_ids"] is False
    assert report["citation_profile_delivery_exposes_local_paths"] is False
    assert report["citation_profile_audit_bundle_keeps_trace"] is True


def test_finalize_cli_strips_src_markers_after_subagent_rewrite(tmp_path: Path, capsys):
    """CLI finalization prevents audited [src:...] markers from leaking to final files."""
    workspace = tmp_path / "workspace"
    input_dir = workspace / "input"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    input_dir.mkdir(parents=True)
    intermediate.mkdir(parents=True)
    (input_dir / "source.md").write_text("dummy", encoding="utf-8")
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: 上能电气_电力设备周报\n"
        "  audience: management\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n"
        "  named_outputs: true\n"
        "  filename_template: '{project_name}_{report_date}'\n"
        "report:\n"
        "  date: '2026-06-06'\n",
        encoding="utf-8",
    )
    audited_path = intermediate / "audited_brief.md"
    audited_path.write_text("# Brief\n\n- Claim [src:CLAIM_123456]\n", encoding="utf-8")

    assert main(["finalize", "--config", str(workspace / "config.yaml")]) == 0
    captured = capsys.readouterr()

    assert "[src:" in audited_path.read_text(encoding="utf-8")
    assert "[src:" not in (output_dir / "brief.md").read_text(encoding="utf-8")
    assert "[src:" not in (output_dir / "上能电气_电力设备周报_2026-06-06.md").read_text(encoding="utf-8")
    assert (output_dir / "delivery" / "brief.md").exists()
    assert "[finalize] Delivery snapshot:" in captured.out
    assert (intermediate / "finalize_report.json").exists()
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    closeout = report["quality_panel_closeout"]
    assert closeout["status"] == "recommended"
    assert closeout["command"] == "briefloop quality summarize --workspace <workspace>"
    assert closeout["runtime_effect"] == "operator_followup_only"
    assert closeout["delivery_bundle"] == "excluded"
    assert closeout["delivery_authority"] is False
    assert closeout["release_authority"] is False
    assert not (intermediate / "quality_panel.json").exists()
    assert not (intermediate / "quality_summary.md").exists()
    assert not (intermediate / "quality_panel.html").exists()


def test_finalize_cli_fails_without_writing_when_active_repair_open(tmp_path: Path, capsys):
    workspace = tmp_path / "workspace"
    input_dir = workspace / "input"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    input_dir.mkdir(parents=True)
    intermediate.mkdir(parents=True)
    (input_dir / "source.md").write_text("dummy", encoding="utf-8")
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: Active Repair Brief\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n- Claim [src:CLAIM_123456]\n",
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps(
            {
                "active_repair": {
                    "schema_version": "mabw.active_repair.v1",
                    "transaction_id": "repair-test-001",
                    "repair_owner": "editor",
                    "allowed_artifacts": ["output/intermediate/audited_brief.md"],
                    "must_rerun_from": "auditor",
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["finalize", "--config", str(workspace / "config.yaml")]) == 1
    captured = capsys.readouterr()

    assert "repair complete" in captured.err
    assert not (output_dir / "brief.md").exists()
    assert not (output_dir / "delivery").exists()
    assert not (intermediate / "finalize_report.json").exists()


def test_finalize_cli_replays_sticky_contamination_before_auditable_target_block(
    tmp_path: Path,
    capsys,
):
    workspace = tmp_path / "workspace"
    input_dir = workspace / "input"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    gates_dir = intermediate / "gates"
    input_dir.mkdir(parents=True)
    gates_dir.mkdir(parents=True)
    (input_dir / "source.md").write_text("dummy", encoding="utf-8")
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: Auditable Target Brief\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n",
        encoding="utf-8",
    )
    condition = workspace / "experiment" / "080" / "condition.json"
    condition.parent.mkdir(parents=True)
    condition.write_text(
        json.dumps(
            {
                "schema_version": "mabw.experiment_080.condition.v1",
                "experiment_id": "MABW-080",
                "case_id": "solar_public_001",
                "condition": "memory",
                "assessment_target": "auditable_brief",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    audited = intermediate / "audited_brief.md"
    audited.write_text("# Brief\n\nExampleCo opened a demo facility. [src:CL-001]\n", encoding="utf-8")
    audit_report = intermediate / "audit_report.json"
    audit_report.write_text(
        json.dumps(_passing_audit_payload(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    gate_report = gates_dir / "auditor_quality_gate_report.json"
    gate_report.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "status": "pass",
                "metadata": {"gate_stage_id": "auditor", "stage_id": "auditor"},
                "gate_results": [
                    {"gate_id": "material_fact", "status": "pass", "blocking": False, "finding_ids": []},
                    {"gate_id": "freshness", "status": "pass", "blocking": False, "finding_ids": []},
                    {"gate_id": "target_relevance", "status": "pass", "blocking": False, "finding_ids": []},
                ],
                "findings": [],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=workspace, repo_workdir=ROOT)
    paths = runtime_state_paths(workspace)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    ledger_sha = _sha256_file(intermediate / "claim_ledger.json")
    audited_sha = _sha256_file(audited)
    audit_sha = _sha256_file(audit_report)
    gate_sha = _sha256_file(gate_report)
    workflow["current_stage"] = "finalize"
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow["run_integrity"] = {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    statuses = dict(workflow.get("stage_statuses") or {})
    for stage_id, entry in statuses.items():
        if stage_id in {"analyst", "editor"}:
            entry["status"] = "complete"
            entry["reason"] = f"{stage_id} complete"
        elif stage_id == "auditor":
            entry["status"] = "complete"
            entry["reason"] = "auditor complete"
            entry["metadata"] = {
                "audit_binding": {
                    "schema_version": "mabw.auditable_audit_binding.v1",
                    "source": "auditor_stage_complete",
                    "claim_ledger_sha256": ledger_sha,
                    "audited_brief_sha256": audited_sha,
                    "audit_report_sha256": audit_sha,
                    "relevant_repair_transaction_ids": [],
                    "auditor_stage_transaction_id": "tx-auditor-complete",
                }
            }
        elif stage_id == "finalize":
            entry["status"] = "ready"
            entry["reason"] = ""
        elif entry.get("status") not in {"complete", "skipped"}:
            entry["status"] = "complete"
            entry["reason"] = f"{stage_id} complete"
    workflow["stage_statuses"] = statuses
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["artifact_registry"].write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-artifact-registry/v1",
                "run_id": workflow["run_id"],
                "artifacts": {
                    "claim_ledger": {
                        "artifact_id": "claim_ledger",
                        "path": "output/intermediate/claim_ledger.json",
                        "status": "valid",
                        "sha256": ledger_sha,
                    },
                    "audited_brief": {
                        "artifact_id": "audited_brief",
                        "path": "output/intermediate/audited_brief.md",
                        "status": "valid",
                        "sha256": audited_sha,
                    },
                    "audit_report": {
                        "artifact_id": "audit_report",
                        "path": "output/intermediate/audit_report.json",
                        "status": "valid",
                        "sha256": audit_sha,
                    },
                    "auditor_quality_gate_report": {
                        "artifact_id": "auditor_quality_gate_report",
                        "path": "output/intermediate/gates/auditor_quality_gate_report.json",
                        "status": "valid",
                        "sha256": gate_sha,
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with paths["event_log"].open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema_version": "multi-agent-brief-event-log/v1",
                    "event_id": "contam-1",
                    "run_id": workflow["run_id"],
                    "created_at": "2026-06-14T00:05:00+00:00",
                    "event_type": "run_integrity_contaminated",
                    "actor": "cli",
                    "stage_id": "editor",
                    "artifact_id": "audited_brief",
                    "decision": None,
                    "reason": "Synthetic sticky contamination.",
                    "metadata": {
                        "reason_code": "frozen_artifact_changed",
                        "message": "Synthetic sticky contamination.",
                        "stage_id": "editor",
                        "artifact_id": "audited_brief",
                    },
                },
                sort_keys=True,
            )
            + "\n"
        )

    assert main(["finalize", "--config", str(workspace / "config.yaml")]) == 1
    captured = capsys.readouterr()

    assert "run integrity is not clean before finalize" in captured.err
    assert "TARGET COMPLETE: auditable_brief" not in captured.err
    assert not (output_dir / "brief.md").exists()
    assert not (output_dir / "delivery").exists()
    assert not (intermediate / "finalize_report.json").exists()
    refreshed = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    assert refreshed["run_integrity"]["status"] == "contaminated"
    assert refreshed["run_integrity"]["reference_eligible"] is False


def test_finalize_cli_blocks_contaminated_delivery_run_before_writing(
    tmp_path: Path,
    capsys,
):
    workspace = tmp_path / "workspace"
    input_dir = workspace / "input"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    input_dir.mkdir(parents=True)
    intermediate.mkdir(parents=True)
    (input_dir / "source.md").write_text("dummy", encoding="utf-8")
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: Repaired Delivery Brief\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=workspace, repo_workdir=ROOT)
    paths = runtime_state_paths(workspace)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    run_id = str(workflow["run_id"])
    with paths["event_log"].open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema_version": "multi-agent-brief-event-log/v1",
                    "event_id": "contam-delivery-1",
                    "run_id": run_id,
                    "created_at": "2026-06-14T00:05:00+00:00",
                    "event_type": "run_integrity_contaminated",
                    "actor": "cli",
                    "stage_id": "editor",
                    "artifact_id": "audited_brief",
                    "decision": None,
                    "reason": "Prior legal repair keeps this run non-reference.",
                    "metadata": {
                        "reason_code": "prior_repair",
                        "message": "Prior legal repair keeps this run non-reference.",
                        "stage_id": "editor",
                        "artifact_id": "audited_brief",
                    },
                },
                sort_keys=True,
            )
            + "\n"
        )

    assert main(["finalize", "--config", str(workspace / "config.yaml")]) == 1
    captured = capsys.readouterr()

    assert "run integrity is not clean before finalize" in captured.err
    assert not (output_dir / "delivery").exists()
    assert not (intermediate / "finalize_report.json").exists()
    refreshed = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    assert refreshed["run_integrity"]["status"] == "contaminated"
    assert refreshed["run_integrity"]["reference_eligible"] is False


def test_finalize_cli_blocks_modified_frozen_audited_brief_before_writing(tmp_path: Path, capsys):
    workspace = tmp_path / "workspace"
    input_dir = workspace / "input"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    input_dir.mkdir(parents=True)
    intermediate.mkdir(parents=True)
    (input_dir / "source.md").write_text("dummy", encoding="utf-8")
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: Frozen Brief\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    (intermediate / "audit_report.json").write_text(
        json.dumps(_passing_audit_payload(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=workspace, repo_workdir=ROOT)
    workflow_path = runtime_state_paths(workspace)["workflow_state"]
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    _write_audit_control_chain(intermediate)
    seen_finalize = False
    for stage_id, entry in workflow["stage_statuses"].items():
        if stage_id == "finalize":
            seen_finalize = True
            entry["status"] = "ready"
            entry["reason"] = ""
        elif not seen_finalize:
            entry["status"] = "complete"
            entry["reason"] = f"{stage_id} complete"
        else:
            entry["status"] = "pending"
            entry["reason"] = ""
    workflow["current_stage"] = "finalize"
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audited.write_text(
        "# Brief\n\nFormatter changed the frozen audited brief. [src:CL-001]\n",
        encoding="utf-8",
    )

    assert main(["finalize", "--config", str(workspace / "config.yaml")]) == 1
    captured = capsys.readouterr()

    assert "Runtime state integrity check failed because a frozen artifact changed" in captured.err
    assert not (output_dir / "brief.md").exists()
    assert not (output_dir / "delivery").exists()
    assert not (intermediate / "finalize_report.json").exists()
    workflow = json.loads(runtime_state_paths(workspace)["workflow_state"].read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "contaminated"
    assert workflow["run_integrity"]["reference_eligible"] is False
    assert workflow["run_integrity"]["reasons"][0]["reason_code"] == "frozen_artifact_changed"


def test_finalize_generates_reader_facing_source_appendix_for_explicit_request(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "ExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n"
        "A missing internal ref should not leak. [src:SYN_CLAIM_MISSING]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )

    appendix = (output_dir / "source_appendix.md").read_text(encoding="utf-8")
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    reader = (output_dir / "brief.md").read_text(encoding="utf-8")

    assert result.source_appendix_generation == "generated_with_warnings"
    assert report["source_appendix_requested_by"] == "source_appendix"
    assert report["source_appendix_source_count"] == 1
    assert report["source_appendix_cited_claim_count"] == 2
    assert report["source_appendix_resolved_claim_count"] == 1
    assert report["source_appendix_mode"] == "separate"
    assert report["source_appendix_claim_map"]["SYN_CLAIM_001"] == {
        "source_label": "S1",
        "source_url": "https://example.com/exampleco-demo",
        "evidence_title": "ExampleCo Opens Demo Facility",
        "source_title": "ExampleCo Opens Demo Facility",
        "source_published_at": "2026-06-01",
        "retrieved_at": "",
        "source_type": "web_search",
        "source_category": "news_media",
    }
    assert "ExampleCo Opens Demo Facility" in appendix
    assert "Unused Source" not in appendix
    assert "SYN_CLAIM" not in appendix
    assert "SYN_SRC" not in appendix
    assert "Full synthetic evidence" not in appendix
    delivery = (output_dir / "delivery" / "brief.md").read_text(encoding="utf-8")
    assert "Source Appendix" in reader
    assert "https://example.com/exampleco-demo" in reader
    assert "Source Appendix" in delivery
    assert "https://example.com/exampleco-demo" in delivery
    assert "SYN_CLAIM" not in reader
    assert "SYN_CLAIM" not in delivery
    assert report["delivery_markdown"] == "output/delivery/brief.md"
    assert report["delivery_docx"] == ""
    assert report["delivery_artifacts"] == ["output/delivery/brief.md"]
    assert not (output_dir / "delivery" / "source_appendix.md").exists()
    assert not (output_dir / "delivery" / "claim_ledger.json").exists()


def test_finalize_records_citation_profile_override_without_reader_internal_ids(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "ExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
        source_appendix_config={"citation_profile": "analyst"},
    )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    reader = (output_dir / "delivery" / "brief.md").read_text(encoding="utf-8")

    assert result.citation_profile == "analyst"
    assert report["citation_profile"] == "analyst"
    assert report["citation_profile_source"] == "config.output.source_appendix.citation_profile"
    assert report["citation_profile_reader_citation_style"] == "source_label"
    assert report["citation_profile_delivery_exposes_internal_ids"] is False
    assert report["citation_profile_delivery_exposes_local_paths"] is False
    assert "[S1]" in reader
    assert "[src:" not in reader
    assert "SYN_CLAIM" not in reader


def test_finalize_maps_src_claim_to_reader_source_label(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "ExampleCo opened a public demo facility. [src:claim-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json", claim_id="claim-001")

    finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )

    reader = (output_dir / "brief.md").read_text(encoding="utf-8")
    delivery = (output_dir / "delivery" / "brief.md").read_text(encoding="utf-8")
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))

    assert "ExampleCo opened a public demo facility. [S1]" in reader
    assert "### [S1] ExampleCo Opens Demo Facility" in reader
    assert "ExampleCo opened a public demo facility. [S1]" in delivery
    assert "[src:" not in reader
    assert "claim-001" not in reader
    assert report["source_appendix_source_count"] == 1
    assert report["source_appendix_claim_map"]["claim-001"]["source_label"] == "S1"
    assert report["source_appendix_claim_map"]["claim-001"]["source_url"] == "https://example.com/exampleco-demo"
    assert report["reader_clean"]["status"] == "pass"
    assert report["reader_clean"]["blank_citation_row_count"] == 0


def test_finalize_writes_source_appendix_trace_audit_copy_without_delivery_leak(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    raw_excerpt = "ExampleCo opened a public demo facility in June 2026."
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "ExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    _write_evidence_span_registry(output_dir, raw_excerpt=raw_excerpt)

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )

    reader = (output_dir / "brief.md").read_text(encoding="utf-8")
    delivery = (output_dir / "delivery" / "brief.md").read_text(encoding="utf-8")
    appendix = (output_dir / "source_appendix.md").read_text(encoding="utf-8")
    trace = (output_dir / "source_appendix_trace.md").read_text(encoding="utf-8")
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))

    assert result.source_appendix_trace_generation == "generated"
    assert result.source_appendix_trace_source_count == 1
    assert result.source_appendix_trace_span_count == 1
    assert report["source_appendix_trace"] == "output/source_appendix_trace.md"
    assert report["source_appendix_trace_generation"] == "generated"
    assert report["source_appendix_trace_source_count"] == 1
    assert report["source_appendix_trace_span_count"] == 1
    assert "Evidence trace: 1 span; roles: direct statement" in appendix
    assert "ESP-001-01" not in reader
    assert "ESP-001-01" not in delivery
    assert "ESP-001-01" not in appendix
    assert "SRC-001" not in reader
    assert "input/sources/source-001.md" not in reader
    assert raw_excerpt not in reader
    assert raw_excerpt not in delivery
    assert raw_excerpt not in appendix
    assert "ESP-001-01" in trace
    assert "SRC-001" in trace
    assert "input/sources/source-001.md" in trace
    assert raw_excerpt in trace
    assert "traceability surface only" in trace
    assert not (output_dir / "delivery" / "source_appendix_trace.md").exists()
    assert "source_appendix_trace.md" not in "\n".join(report["delivery_artifacts"])
    assert report["reader_clean"]["status"] == "pass"


def test_finalize_skips_invalid_span_trace_and_removes_stale_trace_copy(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "ExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    _write_evidence_span_registry(output_dir)
    (tmp_path / "input" / "sources" / "source-001.md").write_text(
        "Different source bytes.\n",
        encoding="utf-8",
    )
    stale_trace = output_dir / "source_appendix_trace.md"
    stale_trace.write_text("stale trace", encoding="utf-8")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert result.status == "pass"
    assert result.source_appendix_generation == "generated"
    assert result.source_appendix_trace_generation == "skipped"
    assert result.source_appendix_trace == ""
    assert not stale_trace.exists()
    assert report["source_appendix_trace"] == ""
    assert report["source_appendix_trace_generation"] == "skipped"
    assert report["source_appendix_trace_span_count"] == 0
    assert report["source_appendix_trace_warnings"]
    assert "does not match source bytes" in report["source_appendix_trace_warnings"][0]
    assert report["reader_clean"]["status"] == "pass"


def test_finalize_auto_renders_source_labels_for_markdown_only_output(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "| Item | Source |\n"
        "| --- | --- |\n"
        "| Demo facility | [src:claim-001] |\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json", claim_id="claim-001")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown"],
        output_named_outputs=False,
    )

    reader = (output_dir / "brief.md").read_text(encoding="utf-8")
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))

    assert result.source_appendix_generation == "generated"
    assert result.source_appendix_requested_by == "cited_claims"
    assert "| Demo facility | [S1] |" in reader
    assert "### [S1] ExampleCo Opens Demo Facility" in reader
    assert "[src:" not in reader
    assert report["source_appendix_claim_map"]["claim-001"]["source_url"] == "https://example.com/exampleco-demo"
    assert report["reader_clean"]["status"] == "pass"
    assert report["reader_clean"]["blank_citation_row_count"] == 0


def test_finalize_fails_when_audit_report_mentions_stale_claim_ids(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    (intermediate / "audit_report.json").write_text(
        json.dumps(
            _passing_audit_payload(summary="Audited CL-001 and stale CL-002."),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)

    with pytest.raises(RuntimeError, match="Audit report binding check failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "source_appendix"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    audit_binding = report["audit_binding"]
    assert audit_binding["status"] == "fail"
    assert audit_binding["ledger_claim_count"] == 1
    assert audit_binding["audited_brief_cited_claim_count"] == 1
    assert any(
        finding["kind"] == "audit_mentions_unknown_claim_ids"
        and finding["claim_ids"] == ["CL-002"]
        for finding in audit_binding["findings"]
    )


def test_finalize_allows_non_real_claim_placeholder_in_audit_report_text(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    (intermediate / "audit_report.json").write_text(
        json.dumps(
            _passing_audit_payload(
                checks=[
                    {
                        "check_id": "citation_format",
                        "status": "pass",
                        "details": "Citations use the documented [src:<claim_id>] notation.",
                    }
                ],
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )

    assert result.audit_binding["status"] == "pass"
    assert not any(
        finding["kind"] == "audit_mentions_unknown_claim_ids"
        for finding in result.audit_binding["findings"]
    )


@pytest.mark.parametrize(
    ("audit_status", "audit_score", "finding_severity", "expected_kind"),
    [
        ("fail", 40, "medium", "audit_status_failed"),
        ("warning", 70, "high", "audit_high_severity_findings"),
        ("pass", 100, "high", "audit_high_severity_findings"),
    ],
)
def test_finalize_blocks_current_audit_report_failures(
    tmp_path: Path,
    audit_status: str,
    audit_score: int,
    finding_severity: str,
    expected_kind: str,
):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    (intermediate / "audit_report.json").write_text(
        json.dumps(
            _passing_audit_payload(
                audit_status=audit_status,
                audit_score=audit_score,
                findings=[
                    {
                        "finding_id": "AUDIT-001",
                        "severity": finding_severity,
                        "finding_type": "source_support",
                        "description": "Audit finding should block finalization when high severity.",
                    }
                ],
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)

    with pytest.raises(RuntimeError, match="Audit report binding check failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "source_appendix"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert any(
        finding["kind"] == expected_kind
        for finding in report["audit_binding"]["findings"]
    )


def test_finalize_blocks_malformed_current_audit_report_contract(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    (intermediate / "audit_report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "recommendation": "approve",
                "summary": "Legacy pass shape without current audit contract fields.",
                "findings": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)

    with pytest.raises(RuntimeError, match="Audit report binding check failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "source_appendix"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert any(
        finding["kind"] == "malformed_audit_report_contract"
        for finding in report["audit_binding"]["findings"]
    )


def test_finalize_preserves_legacy_passed_false_blocker(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    (intermediate / "audit_report.json").write_text(
        json.dumps(
            _passing_audit_payload(passed=False),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)

    with pytest.raises(RuntimeError, match="Audit report binding check failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "source_appendix"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert any(
        finding["kind"] == "audit_not_passed"
        for finding in report["audit_binding"]["findings"]
    )


def test_finalize_verifies_python_audit_binding_without_updating_audit_report(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    ledger = intermediate / "claim_ledger.json"
    _write_single_claim_ledger(ledger)
    audit_report = intermediate / "audit_report.json"
    audit_report.write_text(
        json.dumps(
            _passing_audit_payload(),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)
    original_audit = audit_report.read_text(encoding="utf-8")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )

    assert audit_report.read_text(encoding="utf-8") == original_audit
    assert result.audit_binding["status"] == "pass"
    assert result.audit_binding["claim_ledger_sha256"] == _sha256_file(ledger)
    assert result.audit_binding["audit_report_sha256"] == _sha256_file(audit_report)
    assert result.audit_binding["ledger_claim_count"] == 1
    assert result.audit_binding["audited_brief_cited_claim_count"] == 1


def test_finalize_accepts_missing_legacy_audit_report_metadata_binding(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    (intermediate / "audit_report.json").write_text(
        json.dumps(
            _passing_audit_payload(),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )

    assert result.audit_binding["status"] == "pass"


def test_finalize_ignores_legacy_audit_report_metadata_binding(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    ledger = intermediate / "claim_ledger.json"
    _write_single_claim_ledger(ledger)
    (intermediate / "audit_report.json").write_text(
        json.dumps(
            _passing_audit_payload(
                metadata={
                    "audit_binding": {
                        "status": "pass",
                        "claim_ledger_sha256": "legacy-wrong-sha",
                        "claim_ledger_mtime": "2000-01-01T00:00:00+00:00",
                        "ledger_claim_count": 999,
                    }
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )

    assert result.audit_binding["status"] == "pass"
    assert any(
        warning["kind"] == "legacy_audit_binding_ignored"
        for warning in result.audit_binding["warnings"]
    )


def test_finalize_rejects_if_claim_ledger_changed_after_auditor_complete(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    ledger = intermediate / "claim_ledger.json"
    _write_single_claim_ledger(ledger)
    audit_report = intermediate / "audit_report.json"
    audit_report.write_text(
        json.dumps(
            _passing_audit_payload(),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)
    _write_single_claim_ledger(ledger, claim_id="CL-001")
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload[0]["statement"] = "ExampleCo changed after auditor completion."
    ledger.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Audit report binding check failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "source_appendix"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["audit_binding"]["status"] == "fail"
    assert any(
        finding["kind"] == "audit_binding_mismatch"
        and finding["field"] == "claim_ledger_sha256"
        for finding in report["audit_binding"]["findings"]
    )


def test_finalize_rejects_if_audited_brief_changed_after_auditor_complete(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    audit_report = intermediate / "audit_report.json"
    audit_report.write_text(
        json.dumps(
            _passing_audit_payload(),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)
    audited.write_text(
        "# Brief\n\nChanged after auditor completion but still cites the same claim. [src:CL-001]\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Audit report binding check failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "source_appendix"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert any(
        finding["kind"] == "audit_binding_mismatch"
        and finding["field"] == "audited_brief_sha256"
        for finding in report["audit_binding"]["findings"]
    )


def test_finalize_rejects_if_audit_report_changed_after_auditor_complete(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    audit_report = intermediate / "audit_report.json"
    audit_report.write_text(
        json.dumps(
            _passing_audit_payload(),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_audit_control_chain(intermediate)
    audit_payload = json.loads(audit_report.read_text(encoding="utf-8"))
    audit_payload["summary"] = "Changed after auditor completion."
    audit_report.write_text(json.dumps(audit_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Audit report binding check failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "source_appendix"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert any(
        finding["kind"] == "audit_binding_mismatch"
        and finding["field"] == "audit_report_sha256"
        for finding in report["audit_binding"]["findings"]
    )


def test_finalize_legacy_source_map_skips_missing_ledger_without_failing(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nClaim. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_map"],
        output_named_outputs=False,
    )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert result.source_appendix_generation == "skipped_missing_ledger"
    assert report["source_appendix_requested_by"] == "legacy_source_map"
    assert not (output_dir / "source_appendix.md").exists()
    assert (output_dir / "brief.md").exists()


def test_finalize_explicit_source_appendix_fails_on_missing_ledger(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nClaim. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    stale_appendix = output_dir / "source_appendix.md"
    stale_trace = output_dir / "source_appendix_trace.md"
    stale_appendix.write_text("stale appendix", encoding="utf-8")
    stale_trace.write_text("stale trace", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "source_appendix"],
            output_named_outputs=False,
        )

    assert not stale_appendix.exists()
    assert not stale_trace.exists()
    assert not (intermediate / "finalize_candidate").exists()
    assert not any(
        path.name.startswith(".briefloop-finalize-candidate-")
        for path in intermediate.iterdir()
    )


def test_finalize_markdown_only_regenerates_stale_source_appendix_from_citations(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    first = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )
    assert first.source_appendix_generation == "generated"
    assert (output_dir / "source_appendix.md").exists()

    second = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown"],
        output_named_outputs=False,
    )
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))

    assert second.source_appendix_generation == "generated"
    assert second.source_appendix_requested_by == "cited_claims"
    assert report["source_appendix_requested_by"] == "cited_claims"
    assert report["source_appendix_claim_map"]["SYN_CLAIM_001"]["source_label"] == "S1"
    assert (output_dir / "source_appendix.md").exists()


def test_finalize_legacy_missing_ledger_removes_stale_source_appendix(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    ledger = intermediate / "claim_ledger.json"
    _write_claim_ledger(ledger)

    finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )
    assert (output_dir / "source_appendix.md").exists()
    ledger.unlink()

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_map"],
        output_named_outputs=False,
    )

    assert result.source_appendix_generation == "skipped_missing_ledger"
    assert result.source_appendix == ""
    assert not (output_dir / "source_appendix.md").exists()


def test_finalize_legacy_malformed_ledger_removes_stale_source_appendix(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    ledger = intermediate / "claim_ledger.json"
    _write_claim_ledger(ledger)

    finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )
    assert (output_dir / "source_appendix.md").exists()
    ledger.write_text("{not json", encoding="utf-8")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_map"],
        output_named_outputs=False,
    )

    assert result.source_appendix_generation == "skipped_malformed_ledger"
    assert result.source_appendix == ""
    assert not (output_dir / "source_appendix.md").exists()


def test_finalize_cli_reports_missing_explicit_source_appendix_ledger_without_traceback(
    tmp_path: Path,
    capsys,
):
    workspace = tmp_path / "workspace"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: ExampleCo Brief\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n"
        "    - source_appendix\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nClaim. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )

    rc = main(["finalize", "--config", str(workspace / "config.yaml")])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[finalize] Error:" in captured.err
    assert "Claim Ledger not found" in captured.err
    assert "Traceback" not in captured.err


def test_finalize_cli_supports_legacy_outputs_alias_for_source_appendix(tmp_path: Path):
    workspace = tmp_path / "workspace"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: ExampleCo Brief\n"
        "input:\n"
        "  path: input\n"
        "outputs:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n"
        "  source_appendix:\n"
        "    enabled: true\n"
        "  named_outputs: false\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    rc = main(["finalize", "--config", str(workspace / "config.yaml")])

    assert rc == 0
    assert (output_dir / "source_appendix.md").exists()
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["source_appendix_generation"] == "generated"
    assert report["source_appendix_requested_by"] == "config"


def test_finalize_append_mode_uses_same_markdown_for_named_and_docx(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "docx", "source_appendix"],
        output_named_outputs=True,
        output_filename_template="{project_name}_{report_date}",
        output_filename_tokens={"project_name": "ExampleCo", "report_date": "2026-06-09"},
        source_appendix_config={"enabled": True, "mode": "append"},
    )

    reader = (output_dir / "brief.md").read_text(encoding="utf-8")
    named = (output_dir / "ExampleCo_2026-06-09.md").read_text(encoding="utf-8")
    assert "Source Appendix" in reader
    assert reader == named
    assert "Source Appendix" in (output_dir / "source_appendix.md").read_text(encoding="utf-8")
    assert "Source Appendix" in _docx_text(output_dir / "brief.docx")
    assert "Source Appendix" in _docx_text(output_dir / "ExampleCo_2026-06-09.docx")
    assert "[src:" not in reader
    assert result.source_appendix_mode == "append"
    assert (output_dir / "delivery" / "brief.md").read_text(encoding="utf-8") == reader
    assert "Source Appendix" in _docx_text(output_dir / "delivery" / "ExampleCo_2026-06-09.docx")


def test_finalize_delivery_bundle_contains_appended_sources_without_audit_files(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    _write_runtime_manifest(output_dir, run_id="mabw-run-delivery")
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "docx", "source_appendix"],
        output_named_outputs=True,
        output_filename_template="{project_name}_{report_date}",
        output_filename_tokens={"project_name": "ExampleCo", "report_date": "2026-06-12"},
    )

    delivery_dir = output_dir / "delivery"
    delivery_markdown = delivery_dir / "brief.md"
    delivery_docx = delivery_dir / "ExampleCo_2026-06-12.docx"
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))

    assert delivery_markdown.exists()
    assert delivery_docx.exists()
    assert "Source Appendix" in delivery_markdown.read_text(encoding="utf-8")
    assert "Source Appendix" in _docx_text(delivery_docx)
    assert not (delivery_dir / "source_appendix.md").exists()
    assert not (delivery_dir / "claim_ledger.json").exists()
    assert not any(path.suffix == ".md" and path.name != "brief.md" for path in delivery_dir.iterdir())
    assert result.delivery_markdown == str(delivery_markdown)
    assert result.delivery_docx == str(delivery_docx)
    assert result.delivery_artifacts == [str(delivery_markdown), str(delivery_docx)]
    assert report["delivery_artifacts"] == [
        "output/delivery/brief.md",
        "output/delivery/ExampleCo_2026-06-12.docx",
    ]
    assert report["delivery_artifact_sha256"] == {
        "output/delivery/brief.md": _sha256_file(delivery_markdown),
        "output/delivery/ExampleCo_2026-06-12.docx": _sha256_file(delivery_docx),
    }
    manifest_path = intermediate / "delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["delivery_manifest"] == "output/intermediate/delivery_manifest.json"
    assert report["delivery_manifest_sha256"] == _sha256_file(manifest_path)
    assert manifest["schema_version"] == "briefloop.delivery_manifest.v1"
    assert manifest["status"] == "promoted"
    assert manifest["finalize_transaction_id"] == report["finalize_transaction_id"]
    assert manifest["reader_clean_status"] == "pass"
    assert manifest["artifacts"] == [
        {
            "path": "output/delivery/brief.md",
            "sha256": _sha256_file(delivery_markdown),
            "kind": "reader_markdown",
        },
        {
            "path": "output/delivery/ExampleCo_2026-06-12.docx",
            "sha256": _sha256_file(delivery_docx),
            "kind": "reader_docx",
        },
    ]
    assert result.delivery_artifact_sha256 == {
        str(delivery_markdown): _sha256_file(delivery_markdown),
        str(delivery_docx): _sha256_file(delivery_docx),
    }
    snapshot_dir = output_dir / "delivery-history" / "mabw-run-delivery"
    snapshot_markdown = snapshot_dir / "brief.md"
    snapshot_docx = snapshot_dir / "ExampleCo_2026-06-12.docx"
    assert result.delivery_latest_dir == str(delivery_dir)
    assert report["delivery_latest_dir"] == "output/delivery"
    assert result.delivery_snapshot_dir == str(snapshot_dir)
    assert report["delivery_snapshot_dir"] == "output/delivery-history/mabw-run-delivery"
    assert snapshot_markdown.exists()
    assert snapshot_docx.exists()
    assert not (snapshot_dir / "source_appendix.md").exists()
    assert not (snapshot_dir / "claim_ledger.json").exists()
    assert report["delivery_snapshot_artifacts"] == [
        "output/delivery-history/mabw-run-delivery/brief.md",
        "output/delivery-history/mabw-run-delivery/ExampleCo_2026-06-12.docx",
    ]
    assert report["delivery_snapshot_artifact_sha256"] == {
        "output/delivery-history/mabw-run-delivery/brief.md": _sha256_file(snapshot_markdown),
        "output/delivery-history/mabw-run-delivery/ExampleCo_2026-06-12.docx": _sha256_file(snapshot_docx),
    }
    assert report["delivery_snapshot_semantics"] == "convenience_copy_not_immutable_archive"
    assert report["reader_clean"]["status"] == "pass"


def test_finalize_delivery_snapshot_falls_back_to_timestamp_without_runtime_manifest(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text("# Brief\n\nReader-safe text.\n", encoding="utf-8")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown"],
        output_named_outputs=False,
    )

    snapshot_dir = Path(result.delivery_snapshot_dir)
    assert snapshot_dir.parent == output_dir / "delivery-history"
    assert snapshot_dir.name
    assert snapshot_dir.name != "mabw-run-test"
    assert (snapshot_dir / "brief.md").read_text(encoding="utf-8") == (
        output_dir / "delivery" / "brief.md"
    ).read_text(encoding="utf-8")


def test_finalize_delivery_snapshot_does_not_silently_overwrite_same_run_id(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    _write_runtime_manifest(output_dir, run_id="mabw-run-repeat")
    audited = intermediate / "audited_brief.md"
    audited.write_text("# Brief\n\nFirst reader-safe text.\n", encoding="utf-8")

    first = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown"],
        output_named_outputs=False,
    )
    first_snapshot = Path(first.delivery_snapshot_dir)
    first_text = (first_snapshot / "brief.md").read_text(encoding="utf-8")

    audited.write_text("# Brief\n\nSecond reader-safe text.\n", encoding="utf-8")
    second = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown"],
        output_named_outputs=False,
    )
    second_snapshot = Path(second.delivery_snapshot_dir)

    assert first_snapshot == output_dir / "delivery-history" / "mabw-run-repeat"
    assert second_snapshot == output_dir / "delivery-history" / "mabw-run-repeat-2"
    assert (first_snapshot / "brief.md").read_text(encoding="utf-8") == first_text
    assert (second_snapshot / "brief.md").read_text(encoding="utf-8") == (
        output_dir / "delivery" / "brief.md"
    ).read_text(encoding="utf-8")
    assert "Second reader-safe text" in (output_dir / "delivery" / "brief.md").read_text(encoding="utf-8")


def test_finalize_delivery_snapshot_failure_writes_failed_report(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text("# Brief\n\nReader-safe text.\n", encoding="utf-8")
    (output_dir / "delivery-history").write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Delivery snapshot creation failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["delivery_artifacts"] == ["output/delivery/brief.md"]
    assert report["delivery_snapshot_dir"] == ""
    assert report["delivery_snapshot_artifacts"] == []
    assert "FileExistsError" in report["delivery_snapshot_error"]


def test_finalize_report_relative_paths_survive_workspace_move(tmp_path: Path):
    ws = tmp_path / "workspace"
    output_dir = ws / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text("# Brief\n\nReader-safe text.\n", encoding="utf-8")

    finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown"],
        output_named_outputs=False,
    )

    moved = tmp_path / "moved-workspace"
    shutil.move(str(ws), str(moved))
    report = json.loads((moved / "output" / "intermediate" / "finalize_report.json").read_text(encoding="utf-8"))

    assert report["reader_brief"] == "output/brief.md"
    assert report["delivery_artifacts"] == ["output/delivery/brief.md"]
    assert _finalize_report_delivery_artifact_reasons(moved, report) == []
    reader_paths = _finalize_report_reader_artifact_paths(moved, report)
    assert (moved / "output" / "brief.md").resolve() in reader_paths
    assert all(path.exists() for path in reader_paths)


def test_finalize_rejects_unmarked_internal_claim_ledger_coverage_section(
    tmp_path: Path,
):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "ExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n\n"
        "## 附：本周 Claim Ledger 覆盖情况\n\n"
        "| 覆盖类别 | 要求最低条数 | 实际条数 | 状态 |\n"
        "| --- | --- | --- | --- |\n"
        "| 政策法规 | 4 | 4 | ok |\n\n"
        "> 内部覆盖说明。\n\n"
        "## Normal Reader Section\n\n"
        "This should remain.\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Audited brief contract failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["audited_brief_contract"]["status"] == "fail"
    assert not (output_dir / "brief.md").exists()
    assert not (output_dir / "delivery").exists()


def test_finalize_fails_on_bare_claim_id_reader_residue(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nA raw internal marker [CL-0001] should not ship.\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Reader final output gate failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["reader_clean"]["status"] == "fail"
    assert report["reader_clean"]["bare_claim_id_count"] == 1
    assert report["reader_clean"]["sample_findings"][0]["artifact"].endswith("brief.md")


def test_failed_reader_clean_does_not_overwrite_existing_delivery(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    audited = intermediate / "audited_brief.md"
    audited.write_text("# Brief\n\nFirst reader-safe delivery.\n", encoding="utf-8")

    finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown"],
        output_named_outputs=False,
    )
    root_brief = output_dir / "brief.md"
    delivery_brief = output_dir / "delivery" / "brief.md"
    manifest_path = intermediate / "delivery_manifest.json"
    first_root_text = root_brief.read_text(encoding="utf-8")
    first_delivery_text = delivery_brief.read_text(encoding="utf-8")
    first_manifest_sha = _sha256_file(manifest_path)

    audited.write_text(
        "# Brief\n\nSecond run has raw internal marker [CL-0001].\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Reader final output gate failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["delivery_promotion"] == "skipped_reader_clean_failed"
    assert report["delivery_manifest"] == ""
    assert root_brief.read_text(encoding="utf-8") == first_root_text
    assert delivery_brief.read_text(encoding="utf-8") == first_delivery_text
    assert _sha256_file(manifest_path) == first_manifest_sha


def test_finalize_applies_policy_profile_forbidden_phrases(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    _write_report_spec(tmp_path, policy_profile="finance_default")
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "The report must not promise a guaranteed return to the reader.\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Reader final output gate failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["policy_gate_adapter"]["status"] == "applied"
    assert report["policy_gate_adapter"]["policy_profile_id"] == "finance_default"
    reader_clean = report["reader_clean"]
    assert reader_clean["status"] == "fail"
    assert reader_clean["policy_forbidden_phrase_count"] == 1
    assert reader_clean["sample_findings"][0]["kind"] == "policy_forbidden_phrase"


def test_finalize_cli_resolves_policy_profile_from_workspace_for_nested_output(
    tmp_path: Path,
    capsys,
):
    workspace = tmp_path / "workspace"
    output_dir = workspace / "nested" / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    _write_report_spec(workspace, policy_profile="finance_default")
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: ExampleCo Brief\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: nested/output\n"
        "  formats:\n"
        "    - markdown\n"
        "  named_outputs: false\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "The report must not promise a guaranteed return to the reader.\n",
        encoding="utf-8",
    )

    rc = main(["finalize", "--config", str(workspace / "config.yaml")])
    captured = capsys.readouterr()

    assert rc == 1
    assert "Reader final output gate failed" in captured.err
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["policy_gate_adapter"]["status"] == "applied"
    assert report["policy_gate_adapter"]["policy_profile_id"] == "finance_default"
    assert report["reader_brief"] == "nested/output/brief.md"
    assert report["reader_clean"]["status"] == "fail"
    assert report["reader_clean"]["policy_forbidden_phrase_count"] == 1


def test_finalize_fails_on_common_internal_id_reader_residue(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "Raw IDs CLAIM_123456, CLAIM_TEST_001, SRC_ABCDEF, SRC_001, and SOURCE_A should not ship.\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Reader final output gate failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    reader_clean = report["reader_clean"]
    assert reader_clean["bare_claim_id_count"] == 2
    assert reader_clean["source_id_count"] == 3


def test_finalize_fails_on_docx_footer_reader_residue(tmp_path: Path):
    pytest.importorskip("docx", reason="python-docx not installed")
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nReader-safe body.\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Reader final output gate failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "docx"],
            output_footer="Footer leaks CLAIM_123456",
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    reader_clean = report["reader_clean"]
    assert reader_clean["status"] == "fail"
    assert reader_clean["bare_claim_id_count"] == 1
    assert any(
        finding["artifact"].endswith("brief.docx")
        for finding in reader_clean["sample_findings"]
    )


def test_finalize_fails_on_source_marker_process_and_local_residue(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "A residual marker [source:CL-0001] should fail.\n"
        "The Analyst subagent wrote this from the Claim Ledger.\n"
        "Local path /Users/example/workspace/source.md leaked.\n"
        "DEBUG details should not ship.\n"
        "质量门禁不应出现在读者终稿。\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Audited brief contract failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    contract = report["audited_brief_contract"]
    assert contract["status"] == "fail"
    kinds = {finding["kind"] for finding in contract["findings"]}
    assert {"internal_process_wording", "local_path"}.issubset(kinds)
    assert not (output_dir / "brief.md").exists()
    assert not (output_dir / "delivery").exists()


def test_finalize_fails_on_blank_source_index_row(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "Reader-safe content.\n\n"
        "## Source Index\n\n"
        "| Title | Publisher | URL |\n"
        "| --- | --- | --- |\n"
        "|  |  |  |\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Reader final output gate failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["reader_clean"]["blank_citation_row_count"] == 1


def test_finalize_fails_on_blank_source_index_id_cell(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "Reader-safe content.\n\n"
        "## Source Index\n\n"
        "| ID | Title | Date | Priority |\n"
        "| --- | --- | --- | --- |\n"
        "|  | USTR Section 301对60个经济体调查 | 2026-06-04 | 高 |\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Reader final output gate failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    reader_clean = report["reader_clean"]
    assert reader_clean["status"] == "fail"
    assert reader_clean["blank_citation_row_count"] == 1
    assert "blank ID/source/reference cell" in reader_clean["sample_findings"][0]["message"]


def test_finalize_fails_on_blank_source_column_in_reader_table(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "Reader-safe content.\n\n"
        "## Market Signals\n\n"
        "| Signal | Source | Notes |\n"
        "| --- | --- | --- |\n"
        "| Policy change |  | Needs a source label |\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Reader final output gate failed"):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    reader_clean = report["reader_clean"]
    assert reader_clean["status"] == "fail"
    assert reader_clean["blank_citation_row_count"] == 1
    assert "blank source/reference cell" in reader_clean["sample_findings"][0]["message"]


def test_finalize_cli_reports_reader_clean_failure_without_traceback(
    tmp_path: Path,
    capsys,
):
    workspace = tmp_path / "workspace"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: ExampleCo Brief\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nA raw CLM-001 marker should not ship.\n",
        encoding="utf-8",
    )

    rc = main(["finalize", "--config", str(workspace / "config.yaml")])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[finalize] Error:" in captured.err
    assert "Reader final output gate failed" in captured.err
    assert "finalize_report.json" in captured.err
    assert "Traceback" not in captured.err
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["reader_clean"]["status"] == "fail"
