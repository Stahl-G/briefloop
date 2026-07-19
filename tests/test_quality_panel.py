"""Tests for the Product OS Quality Panel JSON projection."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    SEMANTIC_ASSESSMENT_REPORT_STATUSES,
    build_semantic_assessment_checked_inputs,
)
from multi_agent_brief.status import build_workspace_status, format_workspace_status
from multi_agent_brief.product.quality_panel import (
    QUALITY_PANEL_HTML_BOUNDARY,
    QUALITY_PANEL_BOUNDARY,
    QUALITY_SUMMARY_BOUNDARY,
    QualityPanelError,
    build_quality_panel,
    quality_panel_html_path,
    quality_panel_path,
    render_quality_panel_html,
    quality_summary_path,
    _status_level,
    validate_quality_panel_html,
    render_quality_summary,
    validate_quality_panel_payload,
    validate_quality_summary_markdown,
    write_quality_panel,
    write_quality_panel_html,
    write_quality_summary,
)
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    LajReaderView,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256
from tests.helpers import initialized_workspace_writer


@pytest.fixture(autouse=True)
def _exercise_pre_cutover_quality_internals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain dead-path coverage until the serial LEGACY-DELETE merge unit."""

    monkeypatch.setattr(
        "multi_agent_brief.cli.authority_guard.active_command_authority_error",
        lambda _workspace, _command: None,
    )


_workspace = initialized_workspace_writer(
    project_name="Quality Panel Test",
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_json(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_laj_reader_view(path: Path, *, report_sha256: str) -> LajReaderView:
    finding = {
        "assessment_unit_id": "AU-000000000001",
        "scope_class": "O1",
        "dimension_id": "cross_section_consistency",
        "severity": "major",
        "impact_scope": "supporting_text",
        "report_spans": [
            {
                "report_sha256": report_sha256,
                "block_id": "B000001",
                "start_char": 0,
                "end_char": 5,
                "excerpt_sha256": "2" * 64,
            }
        ],
        "context_requirement_ids": [],
        "observation": "<script>alert(1)</script> needs human review.",
        "rationale": "The section does not reconcile its own stated values.",
        "severity_basis": "The inconsistency changes the reader interpretation.",
        "confidence_basis": "direct_single_span",
        "external_premise_disclosure": "none",
        "recommended_human_action": "inspect_manually",
        "suggested_rewrite": None,
        "finding_id": "F-000000000001",
        "status": "proposal",
    }
    payload = {
        "schema_version": "briefloop.semantic_evaluator.reader_view.v1",
        "status": "available",
        "boundary": LAJ_READER_BOUNDARY,
        "advisory_only": True,
        "shadow_only": True,
        "runtime_authority": False,
        "authority_effect": "none",
        "archive_verified": True,
        "binding": {
            "artifact_id": "reader-test",
            "report_sha256": report_sha256,
            "trial_id": "trial-reader-test",
            "shadow_receipt_id": "receipt-reader-test",
            "instrument_sha256": "3" * 64,
            "execution_sha256": "4" * 64,
            "execution_origin": "synthetic_fixture",
            "model_id": "synthetic-fixture-v4",
            "model_version": "synthetic-fixture-v4",
            "archive_manifest_sha256": "5" * 64,
            "presentation_sha256": "6" * 64,
        },
        "run_status": "completed",
        "validation_status": "accepted",
        "reason_codes": [],
        "assessed_unit_count": 1,
        "finding_count": 1,
        "withheld_finding_count": 0,
        "abstention_count": 0,
        "findings": [finding],
        "disclaimer": "Experimental advisory finding.",
    }
    view = LajReaderView.model_validate(
        {**payload, "view_sha256": canonical_sha256(payload)}
    )
    path.write_text(
        json.dumps(view.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return view


def _write_source_evidence_pack(ws: Path) -> None:
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "source-001.json"
    source_record = {
        "schema_version": "mabw.source_evidence_record.v1",
        "source": "sources.materialize-pack",
        "source_id": "SRC-001",
        "source_title": "Example Source",
        "source_name": "Example Source",
        "publisher": "Example Publisher",
        "source_type": "manual",
        "source_category": "market_report",
        "retrieval_source_type": "local_file",
        "underlying_evidence_type": "market_data",
        "content": "Example source content",
        "raw_excerpt": "Example source content",
    }
    source_path.write_text(
        json.dumps(source_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record = {
        "source_id": "SRC-001",
        "path": "input/sources/source-001.json",
        "sha256": _sha256_file(source_path),
        "size_bytes": source_path.stat().st_size,
        "source_title": "Example Source",
        "publisher": "Example Publisher",
        "source_type": "manual",
        "source_category": "market_report",
        "retrieval_source_type": "local_file",
        "underlying_evidence_type": "market_data",
    }
    manifest = {
        "schema_version": "mabw.source_evidence_pack_manifest.v1",
        "source": "sources.materialize-pack",
        "source_config_path": "sources.yaml",
        "durable_provider_names": ["manual"],
        "record_count": 1,
        "error_count": 0,
        "records": [record],
        "provider_errors": [],
        "pack_sha256": _sha256_json([
            {
                "path": record["path"],
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
                "source_id": record["source_id"],
            }
        ]),
        "non_goals": [
            "semantic_support_assessment",
            "claim_support_matrix_generation",
            "source_candidates_as_evidence",
            "automatic_delivery_approval",
        ],
    }
    manifest_path = ws / "output" / "intermediate" / "source_evidence_pack_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_source_evidence_pack_with_metadata_gaps(ws: Path) -> None:
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "source-gaps.json"
    source_record = {
        "schema_version": "mabw.source_evidence_record.v1",
        "source": "sources.materialize-pack",
        "source_id": "SRC-001",
        "source_type": "manual",
        "source_category": "market_report",
        "retrieval_source_type": "local_file",
        "underlying_evidence_type": "market_data",
        "content": "Example source content",
    }
    source_path.write_text(
        json.dumps(source_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record = {
        "source_id": "SRC-001",
        "path": "input/sources/source-gaps.json",
        "sha256": _sha256_file(source_path),
        "size_bytes": source_path.stat().st_size,
        "source_type": "manual",
        "source_category": "market_report",
        "retrieval_source_type": "local_file",
        "underlying_evidence_type": "market_data",
    }
    manifest = {
        "schema_version": "mabw.source_evidence_pack_manifest.v1",
        "source": "sources.materialize-pack",
        "source_config_path": "sources.yaml",
        "durable_provider_names": ["manual"],
        "record_count": 1,
        "error_count": 0,
        "records": [record],
        "provider_errors": [],
        "pack_sha256": _sha256_json([
            {
                "path": record["path"],
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
                "source_id": record["source_id"],
            }
        ]),
        "non_goals": [
            "semantic_support_assessment",
            "claim_support_matrix_generation",
            "source_candidates_as_evidence",
            "automatic_delivery_approval",
        ],
    }
    (ws / "output" / "intermediate" / "source_evidence_pack_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_invalid_source_evidence_pack(ws: Path) -> None:
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "source-001.json"
    source_path.write_text(
        json.dumps(
            {
                "schema_version": "mabw.source_evidence_record.v1",
                "source": "sources.materialize-pack",
                "source_id": "SRC-001",
                "source_title": "Invalid Source",
                "publisher": "Invalid Publisher",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "market_data",
                "content": "Example source content",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "mabw.source_evidence_pack_manifest.v1",
        "source": "sources.materialize-pack",
        "source_config_path": "sources.yaml",
        "durable_provider_names": ["manual"],
        "record_count": 999,
        "error_count": 0,
        "records": [
            {
                "source_id": "SRC-001",
                "path": "input/sources/source-001.json",
                "sha256": "not-a-valid-source-hash",
                "size_bytes": 1,
                "source_title": "Invalid Source",
                "publisher": "Invalid Publisher",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "market_data",
            }
        ],
        "provider_errors": [],
        "pack_sha256": "not-a-valid-pack-hash",
        "non_goals": [
            "semantic_support_assessment",
            "claim_support_matrix_generation",
            "source_candidates_as_evidence",
            "automatic_delivery_approval",
        ],
    }
    (ws / "output" / "intermediate" / "source_evidence_pack_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_claim_ledger(ws: Path) -> None:
    ledger = [
        {
            "claim_id": "CL-0001",
            "statement": "ExampleCo reported weekly production growth.",
            "source_id": "SRC-001",
            "evidence_text": "Example source content",
            "claim_type": "fact",
            "confidence": "medium",
            "metadata": {
                "source_title": "Example Source",
                "publisher": "Example Publisher",
                "source_category": "market_report",
            },
        }
    ]
    (ws / "output" / "intermediate" / "claim_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_semantic_support_artifacts(ws: Path, *, atom_id: str = "AC-0001-01") -> None:
    intermediate = ws / "output" / "intermediate"
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    raw_excerpt = "ExampleCo reported weekly production growth."
    source_text = f"Intro.\n{raw_excerpt}\nOutro.\n"
    source_path = source_dir / "semantic-source.md"
    source_path.write_text(source_text, encoding="utf-8")
    start = source_text.index(raw_excerpt)
    _write_claim_ledger(ws)
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
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
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
                        "source_path": "input/sources/semantic-source.md",
                        "published_at": "2026-06-01",
                        "source_tier": "company_official",
                        "spans": [
                            {
                                "span_id": "ESP-001-01",
                                "raw_excerpt": raw_excerpt,
                                "hash": "sha256:" + hashlib.sha256(raw_excerpt.encode("utf-8")).hexdigest(),
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
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "semantic_assessment_report.json").write_text(
        json.dumps(
            {
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
                        "metadata": {"calibration_label": "overstated_claim"},
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_gate_report(
    ws: Path,
    *,
    status: str = "pass",
    findings: list[dict] | None = None,
    stage: str = "auditor",
) -> None:
    gates = ws / "output" / "intermediate" / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    normalized_findings = findings or []
    gate_results: list[dict] = []
    gate_ids = sorted({
        str(finding.get("gate_id") or "target_relevance")
        for finding in normalized_findings
        if isinstance(finding, dict)
    })
    if not gate_ids and status != "pass":
        gate_ids = ["target_relevance"]
    for gate_id in gate_ids:
        refs = [
            str(finding.get("finding_id"))
            for finding in normalized_findings
            if isinstance(finding, dict)
            and str(finding.get("gate_id") or "target_relevance") == gate_id
            and finding.get("finding_id")
        ]
        result_status = status if not refs else ("fail" if status == "fail" else "warning")
        gate_results.append(
            {
                "gate_id": gate_id,
                "status": result_status,
                "blocking": result_status == "fail",
                "finding_ids": refs,
            }
        )
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "status": status,
        "gate_results": gate_results,
        "findings": normalized_findings,
        "metadata": {"gate_stage_id": stage},
    }
    (gates / f"{stage}_quality_gate_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_legacy_quality_gate_report(
    ws: Path,
    *,
    status: str = "pass",
    stage: str = "finalize",
    findings: list[dict] | None = None,
) -> None:
    payload = {
        "schema_version": "mabw.quality_gate_report.v1",
        "status": status,
        "findings": findings or [],
        "metadata": {"gate_stage_id": stage},
    }
    (ws / "output" / "intermediate" / "quality_gate_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_finalize_report(
    ws: Path,
    *,
    reader_status: str = "pass",
    duplicate_citation_count: int = 0,
    source_appendix_warnings: list[dict] | None = None,
    source_appendix_trace_warnings: list[dict] | None = None,
) -> None:
    report = {
        "status": "pass",
        "reader_clean": {"status": reader_status, "sample_findings": []},
        "duplicate_citation_count": duplicate_citation_count,
        "source_appendix_warnings": source_appendix_warnings or [],
        "source_appendix_trace_warnings": source_appendix_trace_warnings or [],
    }
    (ws / "output" / "intermediate" / "finalize_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _set_workflow_blocked(ws: Path) -> None:
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = _json(workflow_path)
    workflow["blocked"] = True
    workflow["blocking_reason"] = "adversarial workflow blocker"
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _set_workflow_unblocked(ws: Path) -> None:
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = _json(workflow_path)
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    stages = workflow.get("stage_statuses")
    if isinstance(stages, dict):
        for entry in stages.values():
            if isinstance(entry, dict) and entry.get("status") == "blocked":
                entry["status"] = "pending"
                entry["reason"] = ""
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_quality_panel_direct_import_has_no_runtime_state_cycle() -> None:
    env = dict(os.environ)
    src_path = str(Path.cwd() / "src")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else src_path

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from multi_agent_brief.product.quality_panel import "
                "build_quality_panel, render_quality_panel_html, render_quality_summary; "
                "print(build_quality_panel, render_quality_panel_html, render_quality_summary)"
            ),
        ],
        check=False,
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "build_quality_panel" in result.stdout
    assert "render_quality_panel_html" in result.stdout
    assert "render_quality_summary" in result.stdout


def test_quality_panel_builds_incomplete_projection_without_writing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)

    payload = build_quality_panel(ws)

    assert payload["schema_version"] == "briefloop.quality_panel.v1"
    assert payload["boundary"] == QUALITY_PANEL_BOUNDARY
    assert payload["runtime_effect"] == "projection_only"
    assert payload["overall_status"] == "incomplete"
    assert payload["source_evidence"]["source_pack_status"] == "missing"
    assert payload["control_integrity"]["fact_layer_status"] == "missing"
    assert payload["quality_panel_closeout"]["status"] == "not_ready"
    assert payload["recommended_actions"][0]["action"] == "materialize_durable_source_evidence"
    assert not quality_panel_path(ws).exists()
    assert validate_quality_panel_payload(payload) is None


def test_quality_panel_renders_bound_laj_as_advisory_without_authority_effect(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    reader_target = ws / "output" / "brief.md"
    reader_target.parent.mkdir(parents=True, exist_ok=True)
    reader_target.write_text("# Final reader brief\n\nBound public-safe text.\n", encoding="utf-8")
    laj_path = tmp_path / "laj.json"
    _write_laj_reader_view(laj_path, report_sha256=_sha256_file(reader_target))

    base = build_quality_panel(ws, generated_at="2026-07-18T00:00:00Z")
    panel = build_quality_panel(
        ws,
        generated_at="2026-07-18T00:00:00Z",
        laj_view_path=laj_path,
    )

    for field in (
        "overall_status",
        "control_integrity",
        "gates",
        "delivery",
        "recommended_actions",
    ):
        assert panel[field] == base[field]
    laj = panel["laj_advisory"]
    assert laj["status"] == "available"
    assert laj["advisory_only"] is True
    assert laj["runtime_authority"] is False
    assert laj["authority_effect"] == "none"
    assert laj["finding_count"] == 1
    assert validate_quality_panel_payload(panel) is None

    html = render_quality_panel_html(panel, quality_panel_sha256="7" * 64)
    markdown = render_quality_summary(panel, quality_panel_sha256="7" * 64)
    assert 'data-section="laj-advisory"' in html
    assert "Experimental AI assessment · Advisory only" in html
    assert "Not a Gate, delivery decision, or proof of correctness" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; needs human review." in html
    assert "<script>alert(1)</script>" not in html
    assert "## Experimental AI Assessment" in markdown
    assert validate_quality_panel_html(html) is None
    assert validate_quality_summary_markdown(markdown) is None


@pytest.mark.parametrize(
    ("view_state", "expected_status"),
    (("missing", "not_available"), ("malformed", "invalid"), ("stale", "stale")),
)
def test_laj_failure_states_never_change_quality_panel_authority(
    tmp_path: Path,
    view_state: str,
    expected_status: str,
) -> None:
    ws = _workspace(tmp_path)
    reader_target = ws / "output" / "brief.md"
    reader_target.parent.mkdir(parents=True, exist_ok=True)
    reader_target.write_text("# Final reader brief\n", encoding="utf-8")
    laj_path = tmp_path / "laj.json"
    if view_state == "malformed":
        laj_path.write_bytes(b"{not-json")
    elif view_state == "stale":
        _write_laj_reader_view(laj_path, report_sha256="0" * 64)

    base = build_quality_panel(ws, generated_at="2026-07-18T00:00:00Z")
    panel = build_quality_panel(
        ws,
        generated_at="2026-07-18T00:00:00Z",
        laj_view_path=laj_path,
    )

    assert panel["laj_advisory"]["status"] == expected_status
    assert panel["laj_advisory"]["findings"] == []
    assert panel["laj_advisory"]["finding_count"] == 0
    assert panel["overall_status"] == base["overall_status"]
    assert panel["gates"] == base["gates"]
    assert panel["delivery"] == base["delivery"]
    assert panel["recommended_actions"] == base["recommended_actions"]
    assert validate_quality_panel_payload(panel) is None


def test_quality_panel_rejects_forged_laj_authority_field(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    reader_target = ws / "output" / "brief.md"
    reader_target.parent.mkdir(parents=True, exist_ok=True)
    reader_target.write_text("# Final reader brief\n", encoding="utf-8")
    laj_path = tmp_path / "laj.json"
    _write_laj_reader_view(laj_path, report_sha256=_sha256_file(reader_target))
    panel = build_quality_panel(ws, laj_view_path=laj_path)
    forged = json.loads(json.dumps(panel))
    forged["laj_advisory"]["gate_decision"] = "pass"

    assert validate_quality_panel_payload(forged) == (
        "quality_panel_schema_error:laj_advisory:reader_view_invalid"
    )


def test_cross_report_laj_span_fails_closed_without_authority_effect(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    reader_target = ws / "output" / "brief.md"
    reader_target.parent.mkdir(parents=True, exist_ok=True)
    reader_target.write_text("# Final reader brief\n", encoding="utf-8")
    laj_path = tmp_path / "laj.json"
    view = _write_laj_reader_view(
        laj_path,
        report_sha256=_sha256_file(reader_target),
    )
    cross_bound = view.model_dump(mode="json", exclude={"view_sha256"})
    cross_bound["findings"][0]["report_spans"][0]["report_sha256"] = "9" * 64
    cross_bound["view_sha256"] = canonical_sha256(cross_bound)
    laj_path.write_text(
        json.dumps(cross_bound, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    base = build_quality_panel(ws, generated_at="2026-07-18T00:00:00Z")
    panel = build_quality_panel(
        ws,
        generated_at="2026-07-18T00:00:00Z",
        laj_view_path=laj_path,
    )

    assert panel["laj_advisory"]["status"] == "invalid"
    assert panel["laj_advisory"]["findings"] == []
    assert panel["laj_advisory"]["finding_count"] == 0
    for field in (
        "overall_status",
        "control_integrity",
        "gates",
        "delivery",
        "recommended_actions",
    ):
        assert panel[field] == base[field]


def test_status_recommends_quality_closeout_after_finalize_report_pass(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)

    status = build_workspace_status(ws)
    closeout = status["quality_panel_closeout"]

    assert closeout["status"] == "recommended"
    assert closeout["command"] == "briefloop quality summarize --workspace <workspace>"
    assert closeout["runtime_effect"] == "operator_followup_only"
    assert closeout["delivery_authority"] is False
    assert closeout["release_authority"] is False
    assert "quality_panel.json" in "\n".join(closeout["missing_artifacts"])
    assert "quality_panel_closeout: recommended" in format_workspace_status(status)


def test_quality_summarize_marks_closeout_generated_and_then_complete(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)

    panel = write_quality_panel(workspace=ws)
    summary = write_quality_summary(workspace=ws, panel_payload=panel)
    html = write_quality_panel_html(workspace=ws, panel_payload=panel)

    assert panel["quality_panel_closeout"]["status"] == "generated"
    assert panel["quality_panel_closeout"]["audit_bundle"] == "included_when_present_and_valid"
    assert panel["quality_panel_closeout"]["delivery_bundle"] == "excluded"
    assert panel["quality_panel_closeout"]["gate_authority"] is False
    assert validate_quality_panel_payload(panel) is None
    assert summary["path"] == "output/intermediate/quality_summary.md"
    assert html["path"] == "output/intermediate/quality_panel.html"

    pre_registry_status = build_workspace_status(ws)
    assert pre_registry_status["quality_panel_closeout"]["status"] == "stale_or_invalid"

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    status = build_workspace_status(ws)
    assert status["quality_panel_closeout"]["status"] == "complete"
    assert not status["quality_panel_closeout"]["missing_artifacts"]

    summary_text = quality_summary_path(ws).read_text(encoding="utf-8")
    html_text = quality_panel_html_path(ws).read_text(encoding="utf-8")
    assert "## Quality Closeout And Bundle Separation" in summary_text
    assert "- Delivery bundle: `excluded`" in summary_text
    assert "Quality Closeout And Bundle Separation" in html_text


def test_quality_closeout_rejects_stale_or_hand_edited_quality_artifacts(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    panel = write_quality_panel(workspace=ws)
    write_quality_summary(workspace=ws, panel_payload=panel)
    write_quality_panel_html(workspace=ws, panel_payload=panel)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    assert build_workspace_status(ws)["quality_panel_closeout"]["status"] == "complete"

    html_path = quality_panel_html_path(ws)
    html_path.write_text(
        html_path.read_text(encoding="utf-8").replace("Run integrity", "Run integrity edited", 1),
        encoding="utf-8",
    )
    status = build_workspace_status(ws)

    closeout = status["quality_panel_closeout"]
    assert closeout["status"] == "stale_or_invalid"
    assert closeout["reason"] == "quality_panel_html_stale_or_hand_edited"
    assert "quality_panel.html" in "\n".join(closeout["invalid_artifacts"])
    assert status["artifacts"]["registry_status"] == "degradation"
    assert status["artifacts"]["registry_reason_code"] == (
        "artifact_registry_producer_replay_mismatch"
    )
    assert status["artifacts"]["artifact_count"] == 0
    assert status["artifacts"]["intake"]["present"] is False
    assert status["suggested_next_command"] == (
        f"briefloop state show --workspace {ws} --json"
    )


def test_quality_panel_handles_corrupt_finalize_report_utf8_without_crashing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    (ws / "output" / "intermediate" / "finalize_report.json").write_bytes(b"\xff\xfe")

    status = build_workspace_status(ws)
    panel = build_quality_panel(ws)

    assert status["quality_panel_closeout"]["status"] == "not_ready"
    assert panel["quality_panel_closeout"]["status"] == "not_ready"
    assert validate_quality_panel_payload(panel) is None


def test_quality_panel_payload_validator_rejects_forged_closeout_authority(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    payload = build_quality_panel(ws)

    forged_delivery_bundle = json.loads(json.dumps(payload))
    forged_delivery_bundle["quality_panel_closeout"]["delivery_bundle"] = "included"
    assert validate_quality_panel_payload(forged_delivery_bundle) == (
        "quality_panel_schema_error:quality_panel_closeout:"
        "quality_panel_closeout_schema_error:delivery_bundle"
    )

    forged_delivery_authority = json.loads(json.dumps(payload))
    forged_delivery_authority["quality_panel_closeout"]["delivery_authority"] = True
    assert validate_quality_panel_payload(forged_delivery_authority) == (
        "quality_panel_schema_error:quality_panel_closeout:"
        "quality_panel_closeout_schema_error:delivery_authority"
    )


def test_quality_panel_surfaces_reader_template_conformance_without_authority(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    (ws / "report_spec.yaml").write_text(
        "\n".join([
            "schema_version: briefloop.report_spec.v1",
            "report_pack: market_weekly",
            "report_type: market_weekly",
            "title: Market Weekly Brief",
            "cadence: weekly",
            "policy_profile: manufacturing_default",
            "audience:",
            "  label: business reader",
            "  language: en-US",
            "source_policy:",
            "  mode: local_first",
            "  hidden_autonomous_crawling: false",
            "control_spine:",
            "  claim_ledger: true",
            "  artifact_registry: true",
            "  quality_gates: true",
            "  event_log: true",
            "  archive: true",
            "  source_appendix: true",
            "  support_records: true",
            "  human_delivery_approval: true",
            "  frozen_artifact_integrity: true",
            "outputs:",
            "  - markdown",
            "  - docx",
            "",
        ]),
        encoding="utf-8",
    )
    delivery = ws / "output" / "delivery"
    delivery.mkdir(parents=True, exist_ok=True)
    (delivery / "brief.md").write_text(
        "\n".join([
            "# Market Weekly Brief",
            "Title.",
            "## Executive Summary",
            "Summary.",
            "## Market Signals",
            "Signals without the required reader table.",
            "## Demand and Supply",
            "Demand.",
            "## Competitor Moves",
            "Competitors.",
            "## Policy and Regulatory",
            "Policy.",
            "## Risks and Watchlist",
            "| Risk | Status |",
            "| --- | --- |",
            "| Supply | Watch |",
            "## Source Appendix",
            "Sources.",
        ]),
        encoding="utf-8",
    )

    payload = build_quality_panel(ws)

    assert validate_quality_panel_payload(payload) is None
    conformance = payload["report_template_conformance"]
    assert conformance["status"] == "warning"
    assert conformance["summary_counts"]["reader_block_warning_count"] == 1
    assert {
        "action": "review_reader_template_conformance",
        "reason": "reader_template_conformance_warning_only",
    } in payload["recommended_actions"]
    assert payload["runtime_effect"] == "projection_only"


def test_quality_panel_writes_source_gate_claim_summary(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_gate_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    payload = write_quality_panel(workspace=ws)

    assert quality_panel_path(ws).exists()
    assert validate_quality_panel_payload(payload) is None
    assert payload["source_evidence"]["source_pack_status"] == "present"
    assert payload["source_evidence"]["source_count"] == 1
    assert payload["source_evidence"]["missing_title_count"] == 0
    assert payload["source_evidence"]["retrieval_source_mix"] == {"local_file": 1}
    assert payload["source_evidence"]["underlying_evidence_mix"] == {"market_data": 1}
    assert payload["control_integrity"]["fact_layer_status"] == "complete"
    assert payload["gates"]["auditor_status"] == "pass"
    assert payload["claims"]["claim_count"] == 1


def test_quality_summary_renders_human_markdown_without_authority_claims(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_gate_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    panel = write_quality_panel(workspace=ws)
    panel_sha = _sha256_file(quality_panel_path(ws))
    markdown = render_quality_summary(panel, quality_panel_sha256=panel_sha)

    assert markdown.startswith("# Quality Summary\n")
    assert f"Boundary: {QUALITY_SUMMARY_BOUNDARY}." in markdown
    assert f"Quality-Panel-SHA256: sha256:{panel_sha}" in markdown
    assert "## Overall" in markdown
    assert "## Source Evidence" in markdown
    assert "## Gates And Reader Clean" in markdown
    assert "## Claims And Support Records" in markdown
    assert "## Recommended Next Actions" in markdown
    assert "ready to publish" not in markdown.lower()
    assert "truth proven" not in markdown.lower()
    assert "release authorized" not in markdown.lower()
    assert validate_quality_summary_markdown(markdown) is None


def test_quality_panel_surfaces_final_abstract_quality_warnings(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_gate_report(
        ws,
        status="warning",
        findings=[
            {
                "finding_id": "QG_FINAL_ABSTRACT_QUALITY_001",
                "gate_id": "final_abstract_quality",
                "finding_type": "final_missing_limitation_section",
                "severity": "medium",
                "blocking": False,
                "blocking_level": "warning",
                "description": "warning only",
            }
        ],
    )
    _write_gate_report(ws, stage="finalize")
    _write_finalize_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    panel = write_quality_panel(workspace=ws)
    panel_sha = _sha256_file(quality_panel_path(ws))
    markdown = render_quality_summary(panel, quality_panel_sha256=panel_sha)

    assert panel["overall_status"] == "warning"
    assert panel["gates"]["warning_count"] == 1
    assert panel["gates"]["blocking_count"] == 0
    assert "Quality gates report `1` warning finding(s)." in markdown
    assert "approved for release" not in markdown.lower()


def test_quality_panel_surfaces_semantic_support_proposals_without_authority(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_semantic_support_artifacts(ws)
    _write_gate_report(ws)
    _write_gate_report(ws, stage="finalize")
    _write_finalize_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    _set_workflow_unblocked(ws)

    panel = write_quality_panel(workspace=ws)
    panel_sha = _sha256_file(quality_panel_path(ws))
    markdown = render_quality_summary(panel, quality_panel_sha256=panel_sha)
    html = render_quality_panel_html(panel, quality_panel_sha256=panel_sha)

    semantic = panel["semantic_support"]
    assert panel["overall_status"] == "warning"
    assert semantic == {
        "status": "valid",
        "boundary": "proposal_only_not_a_gate_not_release_authority",
        "proposal_count": 1,
        "calibration_label_counts": {"overstated_claim": 1},
        "llm_only_count": 1,
        "high_uncertainty_count": 1,
        "high_disagreement_count": 1,
        "requires_human_adjudication_count": 1,
        "recommended_human_review": True,
    }
    assert {
        "action": "request_human_review",
        "reason": "semantic_support_human_adjudication_required",
    } in panel["recommended_actions"]
    forbidden_actions = {"approve_delivery", "deliver", "block_release", "auto_repair"}
    assert not forbidden_actions.intersection(
        str(item.get("action") or "") for item in panel["recommended_actions"]
    )
    assert "Semantic support proposals: `1`" in markdown
    assert "not a gate, not release authority" in markdown
    assert "ready to publish" not in markdown.lower()
    assert 'data-section="claim-support-risk"' in html
    assert "Semantic support boundary" in html
    assert "proposal_only_not_a_gate_not_release_authority" in html
    assert "语义支持提案" in html
    assert validate_quality_panel_payload(panel) is None
    assert validate_quality_summary_markdown(markdown) is None
    assert validate_quality_panel_html(html) is None


def test_quality_panel_requests_review_for_invalid_semantic_calibration_label(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_semantic_support_artifacts(ws)
    report_path = ws / "output" / "intermediate" / "semantic_assessment_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["assessors"][0]["assessment_method"] = "human"
    report["rows"][0]["requires_human_adjudication"] = False
    report["rows"][0]["assessment_method"] = "human"
    report["rows"][0]["metadata"]["calibration_label"] = "not_a_known_label"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    panel = write_quality_panel(workspace=ws)
    semantic = panel["semantic_support"]

    assert semantic["status"] == "valid"
    assert semantic["calibration_label_counts"] == {"<invalid_calibration_label>": 1}
    assert semantic["requires_human_adjudication_count"] == 1
    assert semantic["recommended_human_review"] is True
    assert {
        "action": "request_human_review",
        "reason": "semantic_support_human_adjudication_required",
    } in panel["recommended_actions"]
    assert validate_quality_panel_payload(panel) is None


def test_quality_panel_surfaces_invalid_semantic_support_report_as_warning_only(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws, atom_id="AC-0001-99")

    panel = build_quality_panel(ws)
    semantic = panel["semantic_support"]

    assert semantic["status"] == "invalid_report"
    assert semantic["proposal_count"] == 0
    assert semantic["calibration_label_counts"] == {}
    assert semantic["recommended_human_review"] is False
    assert panel["overall_status"] == "incomplete"
    assert not any(
        str(item.get("reason") or "") == "semantic_support_human_adjudication_required"
        for item in panel["recommended_actions"]
    )
    assert validate_quality_panel_payload(panel) is None


def test_quality_panel_accepts_stale_checked_semantic_support_report_as_warning_only(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_semantic_support_artifacts(ws)
    intermediate = ws / "output" / "intermediate"
    (intermediate / "audited_brief.md").write_text("# Audited Brief\n\nExampleCo reported growth.\n", encoding="utf-8")
    report_path = intermediate / "semantic_assessment_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["checked_inputs"] = build_semantic_assessment_checked_inputs(ws)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ledger_path = intermediate / "claim_ledger.json"
    ledger_path.write_text(ledger_path.read_text(encoding="utf-8").rstrip("\n") + "\n\n", encoding="utf-8")

    panel = build_quality_panel(ws)
    semantic = panel["semantic_support"]

    assert semantic["status"] == "stale"
    assert panel["overall_status"] in {"incomplete", "warning"}
    assert validate_quality_panel_payload(panel) is None


def test_quality_panel_accepts_all_semantic_assessment_report_statuses(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    panel = build_quality_panel(ws)

    for status in SEMANTIC_ASSESSMENT_REPORT_STATUSES:
        candidate = json.loads(json.dumps(panel))
        candidate["semantic_support"]["status"] = status
        assert validate_quality_panel_payload(candidate) is None


def test_quality_panel_validator_rejects_forged_semantic_support_authority(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    panel = build_quality_panel(ws)
    assert validate_quality_panel_payload(panel) is None

    for key in (
        "accepted_support_truth",
        "delivery_approval",
        "delivery_authority",
        "gate_decision",
        "release_authority",
        "repair_execution",
        "runtime_effect",
        "state_transition",
        "writes_claim_support_matrix",
    ):
        forged = json.loads(json.dumps(panel))
        forged["semantic_support"][key] = True

        assert validate_quality_panel_payload(forged) == (
            f"quality_panel_schema_error:semantic_support:semantic_support_schema_error:{key}"
        )


def test_quality_panel_semantic_support_survives_corrupt_reader_target(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_semantic_support_artifacts(ws)
    _write_gate_report(ws)
    _write_gate_report(ws, stage="finalize")
    _write_finalize_report(ws)
    output = ws / "output"
    output.mkdir(exist_ok=True)
    (output / "brief.md").write_bytes(b"\xff\xfe")
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    status = build_workspace_status(ws)
    panel = build_quality_panel(ws)

    assert status["support_wording"]["status"] == "not_available"
    assert status["support_wording"]["reason"] == "reader_targets_unreadable"
    assert panel["support_wording"]["status"] == "not_available"
    assert panel["semantic_support"]["status"] == "valid"
    assert panel["semantic_support"]["proposal_count"] == 1
    assert validate_quality_panel_payload(panel) is None


def test_quality_summary_write_reads_existing_panel_and_registers_artifact(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    write_quality_panel(workspace=ws)

    result = write_quality_summary(workspace=ws)

    assert result["path"] == "output/intermediate/quality_summary.md"
    assert quality_summary_path(ws).exists()
    summary = quality_summary_path(ws).read_text(encoding="utf-8")
    assert f"Quality-Panel-SHA256: sha256:{_sha256_file(quality_panel_path(ws))}" in summary
    assert validate_quality_summary_markdown(summary) is None
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_summary"]
    assert record["status"] == "valid"
    assert record["validation_result"] == "experimental_quality_summary_markdown"


def test_quality_panel_html_renders_static_audit_attachment_without_external_assets(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_gate_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    panel = write_quality_panel(workspace=ws)

    html = render_quality_panel_html(panel, quality_panel_sha256=_sha256_file(quality_panel_path(ws)))

    assert html.startswith("<!doctype html>\n")
    assert QUALITY_PANEL_HTML_BOUNDARY in html
    assert f"Quality-Panel-SHA256: sha256:{_sha256_file(quality_panel_path(ws))}" in html
    assert 'id="lang-en"' in html
    assert 'id="lang-zh"' in html
    assert 'for="lang-en">English</label>' in html
    assert 'for="lang-zh">中文</label>' in html
    assert '<span class="lang-en" lang="en">Quality Panel</span>' in html
    assert '<span class="lang-zh" lang="zh-CN">质量面板</span>' in html
    assert '<span class="lang-zh" lang="zh-CN">控制完整性</span>' in html
    assert '<span class="lang-zh" lang="zh-CN">来源证据</span>' in html
    assert '<span class="lang-zh" lang="zh-CN">建议下一步</span>' in html
    assert 'data-section="control-integrity"' in html
    assert 'data-section="source-evidence"' in html
    assert 'data-section="gate-findings"' in html
    assert 'data-section="claim-support-risk"' in html
    assert 'data-section="reader-clean-citation-hygiene"' in html
    assert 'data-section="quality-closeout-bundle-separation"' in html
    assert 'data-section="recommended-next-actions"' in html
    # status values render as color-level badges, bilingual (en = machine value, zh = translation)
    assert '<span class="badge badge-' in html
    assert 'class="status-pill level-' in html
    assert 'data-section="color-legend"' in html
    assert '<span class="lang-zh" lang="zh-CN">通过</span>' in html
    # boundary statement is bilingual
    assert "仅为 quality_panel.json 的静态确定性投影" in html
    lower = html.lower()
    assert "<script" not in lower
    assert "<link" not in lower
    assert " src=" not in lower
    assert "http://" not in lower
    assert "https://" not in lower
    assert "multi-agent-brief" not in lower
    assert "/generate-brief" not in lower
    assert "/mabw" not in lower
    assert "ready to publish" not in lower
    assert "truth proven" not in lower
    assert "release authorized" not in lower
    assert validate_quality_panel_html(html) is None


def test_quality_panel_html_marks_unavailable_states_as_missing_badges(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    panel = write_quality_panel(workspace=ws)

    html = render_quality_panel_html(panel, quality_panel_sha256=_sha256_file(quality_panel_path(ws)))

    assert _status_level("unavailable") == "missing"
    assert '<span class="badge badge-missing" title="not_ready">' in html
    assert '<span class="badge badge-missing" title="not_available">' in html
    assert '<span class="badge badge-info" title="not_ready">' not in html
    assert '<span class="badge badge-info" title="not_available">' not in html
    assert '<span class="lang-zh" lang="zh-CN">未就绪</span>' in html
    assert '<span class="lang-zh" lang="zh-CN">不可用</span>' in html
    assert validate_quality_panel_html(html) is None


def test_quality_panel_html_rendering_does_not_mutate_json_payload(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    panel = write_quality_panel(workspace=ws)
    before = json.loads(quality_panel_path(ws).read_text(encoding="utf-8"))

    render_quality_panel_html(panel, quality_panel_sha256=_sha256_file(quality_panel_path(ws)))
    after = json.loads(quality_panel_path(ws).read_text(encoding="utf-8"))

    assert after == before
    assert "质量面板" not in json.dumps(after, ensure_ascii=False)


def test_quality_panel_html_validator_rejects_active_or_external_content(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    panel = write_quality_panel(workspace=ws)
    html = render_quality_panel_html(panel, quality_panel_sha256=_sha256_file(quality_panel_path(ws)))

    assert validate_quality_panel_html(html.replace("</main>", "<script>alert(1)</script></main>")).startswith(
        "quality_panel_html_schema_error:external_or_active_content:script"
    )
    assert validate_quality_panel_html(html.replace("</head>", '<link rel="stylesheet" href="x.css"></head>')).startswith(
        "quality_panel_html_schema_error:external_or_active_content:link"
    )


def test_quality_panel_html_write_reads_existing_panel_and_registers_artifact(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    write_quality_panel(workspace=ws)

    result = write_quality_panel_html(workspace=ws)

    assert result["path"] == "output/intermediate/quality_panel.html"
    assert quality_panel_html_path(ws).exists()
    html = quality_panel_html_path(ws).read_text(encoding="utf-8")
    assert f"Quality-Panel-SHA256: sha256:{_sha256_file(quality_panel_path(ws))}" in html
    assert validate_quality_panel_html(html) is None
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_panel_html"]
    assert record["status"] == "valid"
    assert record["validation_result"] == "experimental_quality_panel_html"


def test_quality_summarize_cli_writes_panel_and_summary_json(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    capsys.readouterr()

    assert main(["quality", "summarize", "--workspace", str(ws), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["quality_panel"] == "output/intermediate/quality_panel.json"
    assert payload["quality_summary"] == "output/intermediate/quality_summary.md"
    assert payload["quality_panel_html"] == "output/intermediate/quality_panel.html"
    assert payload["boundary"] == "quality_projection_only_not_gate_or_release_authority"
    assert payload["laj_advisory_status"] == "not_requested"
    assert "not_release_authorization" in payload["non_claims"]
    assert quality_panel_path(ws).exists()
    assert quality_summary_path(ws).exists()
    assert quality_panel_html_path(ws).exists()
    assert validate_quality_summary_markdown(quality_summary_path(ws).read_text(encoding="utf-8")) is None
    assert validate_quality_panel_html(quality_panel_html_path(ws).read_text(encoding="utf-8")) is None
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    assert registry["artifacts"]["quality_panel"]["status"] == "valid"
    assert registry["artifacts"]["quality_summary"]["status"] == "valid"
    assert registry["artifacts"]["quality_panel_html"]["status"] == "valid"


def test_quality_summarize_cli_accepts_explicit_bound_laj_view(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    reader_target = ws / "output" / "brief.md"
    reader_target.parent.mkdir(parents=True, exist_ok=True)
    reader_target.write_text("# Final reader brief\n", encoding="utf-8")
    laj_path = tmp_path / "laj.json"
    _write_laj_reader_view(laj_path, report_sha256=_sha256_file(reader_target))
    capsys.readouterr()

    assert main(
        [
            "quality",
            "summarize",
            "--workspace",
            str(ws),
            "--laj-view",
            str(laj_path),
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["laj_advisory_status"] == "available"
    panel = _json(quality_panel_path(ws))
    assert panel["laj_advisory"]["status"] == "available"
    assert panel["overall_status"] in {"incomplete", "warning", "pass", "block"}
    assert validate_quality_panel_payload(panel) is None


def test_quality_summarize_cli_human_output_keeps_projection_boundary(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    capsys.readouterr()

    assert main(["quality", "summarize", "--workspace", str(ws)]) == 0
    output = capsys.readouterr().out

    assert "quality_panel: output/intermediate/quality_panel.json" in output
    assert "quality_summary: output/intermediate/quality_summary.md" in output
    assert "quality_panel_html: output/intermediate/quality_panel.html" in output
    assert "quality projection only" in output
    assert "no gates were run" in output
    assert "no release was authorized" in output
    assert "ready to publish" not in output.lower()
    assert "truth proven" not in output.lower()


def test_quality_summarize_cli_rejects_missing_workspace_without_writing(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing-ws"
    capsys.readouterr()

    assert main(["quality", "summarize", "--workspace", str(missing), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert "workspace does not exist" in payload["error"]
    assert not missing.exists()


def test_quality_summarize_cli_rejects_output_intermediate_shell_without_writing(
    tmp_path: Path,
    capsys,
) -> None:
    shell = tmp_path / "not-a-workspace"
    (shell / "output" / "intermediate").mkdir(parents=True)
    capsys.readouterr()

    assert main(["quality", "summarize", "--workspace", str(shell), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert "not a BriefLoop workspace" in payload["error"]
    assert not (shell / "output" / "intermediate" / "quality_panel.json").exists()
    assert not (shell / "output" / "intermediate" / "quality_summary.md").exists()
    assert not (shell / "output" / "intermediate" / "quality_panel.html").exists()


def test_quality_summary_missing_or_invalid_panel_fails_without_writing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)

    with pytest.raises(QualityPanelError, match="quality_panel.json is required"):
        write_quality_summary(workspace=ws)
    assert not quality_summary_path(ws).exists()

    quality_panel_path(ws).write_text('{"schema_version": "bad"}\n', encoding="utf-8")
    with pytest.raises(QualityPanelError, match="quality_panel invalid"):
        write_quality_summary(workspace=ws)
    assert not quality_summary_path(ws).exists()


def test_quality_summary_validator_rejects_release_authority_shape() -> None:
    bad = (
        "# Quality Summary\n\n"
        f"Boundary: {QUALITY_SUMMARY_BOUNDARY}.\n\n"
        f"Quality-Panel-SHA256: sha256:{'0' * 64}\n\n"
        "## Overall\n\n"
        "- This report is ready to publish.\n\n"
        "## Blocking Issues\n\n- None.\n\n"
        "## Warnings\n\n- None.\n\n"
        "## Missing Or Incomplete Surfaces\n\n- None.\n\n"
        "## Source Evidence\n\n- None.\n\n"
        "## Gates And Reader Clean\n\n- None.\n\n"
        "## Claims And Support Records\n\n- None.\n\n"
        "## Recommended Next Actions\n\n- None.\n"
    )

    assert validate_quality_summary_markdown(bad) == (
        "quality_summary_schema_error:forbidden_phrase:ready_to_publish"
    )


def test_quality_summary_registry_requires_valid_quality_panel_source(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    summary = render_quality_summary(build_quality_panel(ws), quality_panel_sha256="0" * 64)
    quality_summary_path(ws).write_text(summary, encoding="utf-8")

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_summary"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "quality_summary_validation_error:quality_panel_missing"

    quality_panel_path(ws).write_text('{"schema_version": "bad"}\n', encoding="utf-8")
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_summary"]
    assert record["status"] == "invalid"
    assert record["validation_result"].startswith(
        "quality_summary_validation_error:quality_panel_invalid:"
    )


def test_quality_summary_registry_treats_invalid_utf8_panel_as_invalid(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    summary = render_quality_summary(build_quality_panel(ws), quality_panel_sha256="0" * 64)
    quality_summary_path(ws).write_text(summary, encoding="utf-8")
    quality_panel_path(ws).write_bytes(b"\xff\xfe\x00")

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_summary"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "quality_summary_validation_error:quality_panel_unreadable"


def test_quality_summary_registry_rejects_stale_or_hand_edited_summary(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    write_quality_panel(workspace=ws)
    write_quality_summary(workspace=ws)
    panel = _json(quality_panel_path(ws))
    panel["generated_at"] = "2099-01-01T00:00:00Z"
    quality_panel_path(ws).write_text(
        json.dumps(panel, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_summary"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "quality_summary_validation_error:stale_or_hand_edited"


def test_quality_panel_html_missing_or_invalid_panel_fails_without_writing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)

    with pytest.raises(QualityPanelError, match="quality_panel.json is required"):
        write_quality_panel_html(workspace=ws)
    assert not quality_panel_html_path(ws).exists()

    quality_panel_path(ws).write_text('{"schema_version": "bad"}\n', encoding="utf-8")
    with pytest.raises(QualityPanelError, match="quality_panel invalid"):
        write_quality_panel_html(workspace=ws)
    assert not quality_panel_html_path(ws).exists()


def test_quality_panel_html_registry_requires_valid_quality_panel_source(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    html = render_quality_panel_html(build_quality_panel(ws), quality_panel_sha256="0" * 64)
    quality_panel_html_path(ws).write_text(html, encoding="utf-8")

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_panel_html"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "quality_panel_html_validation_error:quality_panel_missing"

    quality_panel_path(ws).write_text('{"schema_version": "bad"}\n', encoding="utf-8")
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_panel_html"]
    assert record["status"] == "invalid"
    assert record["validation_result"].startswith(
        "quality_panel_html_validation_error:quality_panel_invalid:"
    )


def test_quality_panel_html_registry_treats_invalid_utf8_panel_as_invalid(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    html = render_quality_panel_html(build_quality_panel(ws), quality_panel_sha256="0" * 64)
    quality_panel_html_path(ws).write_text(html, encoding="utf-8")
    quality_panel_path(ws).write_bytes(b"\xff\xfe\x00")

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_panel_html"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "quality_panel_html_validation_error:quality_panel_unreadable"


def test_quality_panel_html_registry_rejects_stale_or_hand_edited_html(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    write_quality_panel(workspace=ws)
    write_quality_panel_html(workspace=ws)
    html = quality_panel_html_path(ws).read_text(encoding="utf-8")
    quality_panel_html_path(ws).write_text(html.replace("Quality Panel", "Quality Panel Edited", 1), encoding="utf-8")

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_panel_html"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "quality_panel_html_validation_error:stale_or_hand_edited"


def test_quality_panel_stays_incomplete_before_finalize_and_reader_hygiene(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_gate_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    _set_workflow_unblocked(ws)

    payload = build_quality_panel(ws)

    assert payload["source_evidence"]["source_pack_status"] == "present"
    assert payload["control_integrity"]["fact_layer_status"] == "complete"
    assert payload["gates"]["auditor_status"] == "pass"
    assert payload["gates"]["finalize_status"] == "missing"
    assert payload["delivery"]["reader_clean_status"] == "missing"
    assert payload["overall_status"] == "incomplete"
    assert {
        "action": "complete_finalize_delivery_hygiene",
        "reason": "finalize_or_reader_clean_missing",
    } in payload["recommended_actions"]


def test_quality_panel_distinguishes_legacy_gate_report_from_missing_scoped_reports(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_legacy_quality_gate_report(ws, status="pass", stage="finalize")
    _write_finalize_report(ws, reader_status="pass")
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    _set_workflow_unblocked(ws)

    payload = build_quality_panel(ws)

    assert payload["gates"]["auditor_status"] == "missing"
    assert payload["gates"]["finalize_status"] == "missing"
    assert payload["gates"]["auditor_report_status"] == "missing_scoped_report"
    assert payload["gates"]["finalize_report_status"] == "missing_scoped_report"
    assert payload["gates"]["legacy_quality_gate_present"] is True
    assert payload["gates"]["legacy_quality_gate_status"] == "pass"
    assert payload["gates"]["legacy_quality_gate_stage"] == "finalize"
    assert payload["delivery"]["reader_clean_status"] == "pass"
    assert payload["overall_status"] == "incomplete"
    assert {
        "action": "regenerate_scoped_gate_reports",
        "reason": "scoped_quality_gate_reports_missing",
    } in payload["recommended_actions"]
    assert {
        "action": "complete_finalize_delivery_hygiene",
        "reason": "finalize_or_reader_clean_missing",
    } not in payload["recommended_actions"]


def test_quality_panel_does_not_interpret_invalid_claim_support_matrix_rows(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_gate_report(ws)
    _write_gate_report(ws, stage="finalize")
    _write_finalize_report(ws)
    invalid_matrix = {
        "schema_version": "mabw.claim_support_matrix.v1",
        "rows": [
            {
                "row_id": "CSM-0001",
                "claim_id": "CL-0001",
                "atom_id": "AC-0001-01",
                "evidence_span_id": None,
                "support_label": "unsupported",
                "support_strength": "none",
                "required_action": "block_release",
                "repair_owner": "analyst",
                "decision_source": "human",
            }
        ],
    }
    (ws / "output" / "intermediate" / "claim_support_matrix.json").write_text(
        json.dumps(invalid_matrix, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    _set_workflow_unblocked(ws)

    payload = build_quality_panel(ws)

    assert payload["claims"]["claim_support_matrix_status"] == "invalid"
    assert payload["claims"]["unsupported_count"] == 0
    assert payload["claims"]["weak_support_count"] == 0
    assert payload["overall_status"] == "warning"


def test_quality_panel_honors_workflow_blocker_in_overall_status(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_gate_report(ws)
    _write_gate_report(ws, stage="finalize")
    _write_finalize_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    _set_workflow_blocked(ws)

    payload = build_quality_panel(ws)

    assert payload["overall_status"] == "block"
    assert {
        "action": "inspect_workflow_blocker",
        "reason": "adversarial workflow blocker",
    } in payload["recommended_actions"]


def test_quality_panel_blocks_failed_finalize_gate_status_without_findings(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_gate_report(ws)
    _write_gate_report(ws, status="fail", findings=[], stage="finalize")
    _write_finalize_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    _set_workflow_unblocked(ws)

    payload = build_quality_panel(ws)

    assert payload["gates"]["finalize_status"] == "fail"
    assert payload["gates"]["blocking_count"] == 0
    assert payload["overall_status"] == "block"
    assert {
        "action": "resolve_quality_gate_blockers",
        "reason": "quality_gate_status_failed",
    } in payload["recommended_actions"]


def test_quality_panel_keeps_unknown_reader_clean_incomplete(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    _write_gate_report(ws)
    _write_gate_report(ws, stage="finalize")
    _write_finalize_report(ws, reader_status="unknown")
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    _set_workflow_unblocked(ws)

    payload = build_quality_panel(ws)

    assert payload["delivery"]["reader_clean_status"] == "unknown"
    assert payload["overall_status"] == "incomplete"
    assert {
        "action": "complete_finalize_delivery_hygiene",
        "reason": "finalize_or_reader_clean_missing",
    } in payload["recommended_actions"]


def test_quality_panel_does_not_interpret_invalid_source_evidence_pack_counts(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_invalid_source_evidence_pack(ws)
    _write_claim_ledger(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    payload = build_quality_panel(ws)

    assert payload["source_evidence"]["source_pack_status"] == "invalid"
    assert payload["source_evidence"]["source_count"] == 0
    assert payload["source_evidence"]["missing_title_count"] == 0
    assert payload["source_evidence"]["missing_publisher_count"] == 0
    assert payload["source_evidence"]["retrieval_source_mix"] == {}
    assert payload["source_evidence"]["underlying_evidence_mix"] == {}


def test_quality_panel_dogfood_surfaces_source_and_reader_hygiene_failures(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_source_evidence_pack_with_metadata_gaps(ws)
    _write_claim_ledger(ws)
    _write_gate_report(ws)
    _write_gate_report(ws, stage="finalize")
    _write_finalize_report(
        ws,
        reader_status="fail",
        duplicate_citation_count=2,
        source_appendix_warnings=[{"kind": "missing_source_title"}],
        source_appendix_trace_warnings=[{"kind": "metadata_warning"}],
    )
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    _set_workflow_unblocked(ws)

    payload = build_quality_panel(ws)

    assert payload["control_integrity"]["fact_layer_status"] == "complete"
    assert payload["source_evidence"]["source_pack_status"] == "present"
    assert payload["source_evidence"]["source_count"] == 1
    assert payload["source_evidence"]["missing_title_count"] == 1
    assert payload["source_evidence"]["missing_publisher_count"] == 1
    assert payload["source_evidence"]["retrieval_source_mix"] == {"local_file": 1}
    assert payload["source_evidence"]["underlying_evidence_mix"] == {"market_data": 1}
    assert payload["delivery"]["reader_clean_status"] == "fail"
    assert payload["delivery"]["duplicate_citation_count"] == 2
    assert payload["delivery"]["source_appendix_warning_count"] == 2
    assert payload["overall_status"] == "block"
    assert {"action": "repair_reader_final_residue", "reason": "reader_clean_failed"} in payload[
        "recommended_actions"
    ]


def test_quality_panel_artifact_registry_validation(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    write_quality_panel(workspace=ws)

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_panel"]
    assert record["status"] == "valid"
    assert record["validation_result"] == "experimental_quality_panel"


def test_runtime_reset_archives_prior_run_quality_panel(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    old_run_id = _json(ws / "output" / "intermediate" / "runtime_manifest.json")["run_id"]
    write_quality_panel(workspace=ws)
    write_quality_summary(workspace=ws)
    write_quality_panel_html(workspace=ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    assert main(["state", "init", "--runtime", "operator", "--workspace", str(ws), "--reset-state"]) == 0

    intermediate = ws / "output" / "intermediate"
    assert (intermediate / f"quality_panel.{old_run_id}.json").exists()
    assert (intermediate / f"quality_summary.{old_run_id}.md").exists()
    assert (intermediate / f"quality_panel.{old_run_id}.html").exists()
    assert not quality_panel_path(ws).exists()
    assert not quality_summary_path(ws).exists()
    assert not quality_panel_html_path(ws).exists()
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(intermediate / "artifact_registry.json")
    record = registry["artifacts"]["quality_panel"]
    assert record["status"] == "expected"
    assert record["sha256"] is None
    summary_record = registry["artifacts"]["quality_summary"]
    assert summary_record["status"] == "expected"
    assert summary_record["sha256"] is None
    html_record = registry["artifacts"]["quality_panel_html"]
    assert html_record["status"] == "expected"
    assert html_record["sha256"] is None


def test_quality_panel_surfaces_blocking_gate_and_reader_failure(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_gate_report(
        ws,
        status="fail",
        findings=[{"finding_id": "QG-1", "blocking": True, "message": "blocked"}],
    )
    finalize_report = {
        "status": "pass",
        "reader_clean": {
            "status": "fail",
            "sample_findings": [{"kind": "local_path"}],
        },
    }
    (ws / "output" / "intermediate" / "finalize_report.json").write_text(
        json.dumps(finalize_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    payload = build_quality_panel(ws)

    assert payload["overall_status"] == "block"
    assert payload["gates"]["blocking_count"] == 1
    assert payload["delivery"]["reader_clean_status"] == "fail"
    assert {"action": "resolve_quality_gate_blockers", "reason": "blocking_gate_findings"} in payload[
        "recommended_actions"
    ]
    assert {"action": "repair_reader_final_residue", "reason": "reader_clean_failed"} in payload[
        "recommended_actions"
    ]


def test_quality_panel_payload_validator_rejects_release_authority_shape() -> None:
    payload = {
        "schema_version": "briefloop.quality_panel.v1",
        "workspace": ".",
        "run_id": "run-1",
        "runtime_effect": "projection_only",
        "boundary": QUALITY_PANEL_BOUNDARY,
        "overall_status": "pass",
        "control_integrity": {},
        "source_evidence": {},
        "gates": {},
        "claims": {},
        "delivery": {},
        "trajectory_regulation": {
            "schema_version": "briefloop.trajectory_regulation.v1",
            "status": "ok",
            "read_only": True,
            "runtime_effect": "none",
            "boundary": "trajectory_regulation_projection_only_not_state_transition_or_repair_execution",
            "run_id": "run-1",
            "current_stage": "doctor",
            "event_log_present": True,
            "event_log_corrupt_count": 0,
            "limits": {},
            "summary_counts": {},
            "stages": [],
            "recommended_actions": [],
            "non_goals": [
                "state_transition",
                "repair_execution",
                "gate_decision",
                "release_authority",
                "quality_score",
            ],
        },
        "recommended_actions": [],
        "non_goals": ["quality_score"],
    }

    assert validate_quality_panel_payload(payload) == "quality_panel_schema_error:non_goals"


def test_quality_panel_payload_validator_rejects_forged_trajectory_authority() -> None:
    trajectory = {
        "schema_version": "briefloop.trajectory_regulation.v1",
        "status": "ok",
        "read_only": True,
        "runtime_effect": "none",
        "boundary": "trajectory_regulation_projection_only_not_state_transition_or_repair_execution",
        "run_id": "run-1",
        "current_stage": "doctor",
        "event_log_present": True,
        "event_log_corrupt_count": 0,
        "limits": {},
        "summary_counts": {},
        "stages": [],
        "recommended_actions": [],
        "non_goals": [
            "state_transition",
            "repair_execution",
            "gate_decision",
            "release_authority",
            "quality_score",
        ],
    }
    payload = {
        "schema_version": "briefloop.quality_panel.v1",
        "workspace": ".",
        "run_id": "run-1",
        "runtime_effect": "projection_only",
        "boundary": QUALITY_PANEL_BOUNDARY,
        "overall_status": "pass",
        "control_integrity": {},
        "source_evidence": {},
        "gates": {},
        "claims": {},
        "delivery": {},
        "trajectory_regulation": trajectory,
        "recommended_actions": [],
        "non_goals": [
            "semantic_truth_proof",
            "release_eligibility_decision",
            "delivery_approval",
        ],
    }

    forged_trajectory = json.loads(json.dumps(payload))
    forged_trajectory["trajectory_regulation"]["runtime_effect"] = "state_transition"
    assert (
        validate_quality_panel_payload(forged_trajectory)
        == "quality_panel_schema_error:trajectory_regulation:trajectory_regulation_schema_error:runtime_effect"
    )

    forged_nested_action = json.loads(json.dumps(payload))
    forged_nested_action["trajectory_regulation"]["recommended_actions"] = [{"action": "approve_delivery"}]
    assert (
        validate_quality_panel_payload(forged_nested_action)
        == "quality_panel_schema_error:trajectory_regulation:trajectory_regulation_schema_error:recommended_actions.action"
    )

    forged_action = json.loads(json.dumps(payload))
    forged_action["recommended_actions"] = [{"action": "approve_delivery"}]
    assert validate_quality_panel_payload(forged_action) == "quality_panel_schema_error:recommended_actions.action"


def test_quality_panel_payload_validator_rejects_forged_template_conformance_authority() -> None:
    payload = {
        "schema_version": "briefloop.quality_panel.v1",
        "workspace": ".",
        "run_id": "run-test",
        "runtime_effect": "projection_only",
        "boundary": QUALITY_PANEL_BOUNDARY,
        "overall_status": "warning",
        "control_integrity": {},
        "source_evidence": {},
        "gates": {},
        "claims": {},
        "delivery": {},
        "report_template_conformance": {
            "boundary": "product_report_template_conformance_projection_only",
            "runtime_effect": "state_transition",
            "status": "warning",
            "targets": [],
            "summary_counts": {},
        },
        "recommended_actions": [],
        "non_goals": [
            "semantic_truth_proof",
            "release_eligibility_decision",
            "delivery_approval",
        ],
    }

    assert validate_quality_panel_payload(payload) == (
        "quality_panel_schema_error:report_template_conformance:"
        "report_template_conformance_schema_error:runtime_effect"
    )
