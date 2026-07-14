"""Tests for the Product OS Quality Panel JSON projection."""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import multi_agent_brief.cli.product_commands as product_commands
import multi_agent_brief.product.quality_closeout as quality_closeout
import multi_agent_brief.product.quality_panel as quality_panel_module
from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import (
    RegistryDegradation,
    RegistryNotMaterialized,
    RegistrySnapshotDrift,
)
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    SEMANTIC_ASSESSMENT_REPORT_STATUSES,
    build_semantic_assessment_checked_inputs,
)
from multi_agent_brief.status import build_workspace_status, format_workspace_status
from multi_agent_brief.product.quality_closeout import (
    CanonicalQualityPanelView,
    QualityPanelDegradation,
    QualityPanelCloseoutError,
    QualityPanelNotMaterialized,
    display_quality_panel_closeout,
    interpret_quality_panel_closeout,
    materialize_quality_panel_closeout,
)
from multi_agent_brief.product.quality_panel import (
    QUALITY_PANEL_HTML_BOUNDARY,
    QUALITY_PANEL_BOUNDARY,
    QUALITY_SUMMARY_BOUNDARY,
    QualityPanelError,
    build_quality_panel,
    build_quality_panel_producer_context,
    project_quality_panel,
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
from tests.helpers import initialized_workspace_writer


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


def _write_materiality_inputs(ws: Path) -> None:
    intermediate = ws / "output" / "intermediate"
    candidate_ids = ("CAND-001", "CAND-002", "CAND-003")
    (intermediate / "candidate_claims.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": candidate_id,
                    "claim": f"Example candidate {candidate_id}.",
                    "source_id": f"SRC-{index:03d}",
                }
                for index, candidate_id in enumerate(candidate_ids, start=1)
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "screened_candidates.json").write_text(
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo reported routine supplier updates.",
                        "evidence_text": "ExampleCo reported routine supplier updates.",
                        "source_id": "SRC-001",
                        "retrieved_at": "2026-07-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-002",
                        "statement": (
                            "ExampleCo capacity expansion is delayed by tariff uncertainty."
                        ),
                        "source_id": "SRC-002",
                        "reason_code": "capacity_capped",
                        "explanation": "Capacity cap applied after selection.",
                    }
                ],
                "deprioritized": [
                    {
                        "candidate_id": "CAND-003",
                        "statement": "Inventory movements were off focus for this brief.",
                        "source_id": "SRC-003",
                        "reason_code": "off_focus",
                        "explanation": "Outside the selected brief focus.",
                    }
                ],
                "screening_policy": {
                    "method": "deterministic_test",
                    "total_candidates": 3,
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


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


def test_quality_panel_projector_consumes_only_deep_frozen_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws, duplicate_citation_count=2)
    context = build_quality_panel_producer_context(ws)
    expected = project_quality_panel(producer_context=context)

    def fail_workspace_read(*args, **kwargs):
        raise AssertionError("pure projector attempted a workspace read")

    for helper_name in (
        "_source_evidence_summary",
        "_gate_summary",
        "_claim_summary",
        "_delivery_summary",
    ):
        monkeypatch.setattr(quality_panel_module, helper_name, fail_workspace_read)

    assert project_quality_panel(producer_context=context) == expected
    with pytest.raises(TypeError):
        context["ok"] = False
    with pytest.raises(TypeError):
        context["workflow"]["blocked"] = True


def test_quality_panel_context_is_an_immutable_workspace_snapshot(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws, duplicate_citation_count=0)
    context = build_quality_panel_producer_context(ws)
    before = project_quality_panel(producer_context=context)

    _write_finalize_report(ws, duplicate_citation_count=3)

    assert project_quality_panel(producer_context=context) == before
    refreshed = project_quality_panel(
        producer_context=build_quality_panel_producer_context(ws),
    )
    assert refreshed != before
    assert refreshed["delivery"]["duplicate_citation_count"] == 3


def test_quality_panel_projector_is_structurally_free_of_workspace_reads() -> None:
    signature = inspect.signature(project_quality_panel)
    assert tuple(signature.parameters) == ("producer_context",)
    tree = ast.parse(textwrap.dedent(inspect.getsource(project_quality_panel)))
    call_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            call_names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            call_names.add(node.func.attr)

    forbidden_calls = {
        "Path",
        "build_quality_panel_producer_context",
        "exists",
        "is_file",
        "read_bytes",
        "read_text",
        "_claim_summary",
        "_delivery_summary",
        "_gate_summary",
        "_read_json_mapping",
        "_source_evidence_summary",
    }
    assert call_names.isdisjoint(forbidden_calls)
    assert not any(name.startswith("project_workspace_") for name in call_names)


def test_quality_panel_projector_is_identical_under_optimized_python(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws, duplicate_citation_count=1)
    context_path = tmp_path / "quality-panel-context.json"
    context_path.write_text(
        json.dumps(
            quality_panel_module._plain_json(
                build_quality_panel_producer_context(ws)
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    script = (
        "import json,sys;"
        "from pathlib import Path;"
        "from multi_agent_brief.product.quality_panel import project_quality_panel;"
        "context=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));"
        "payload=project_quality_panel(producer_context=context);"
        "print(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')))"
    )
    env = dict(os.environ)
    src_path = str(Path.cwd() / "src")
    env["PYTHONPATH"] = (
        f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else src_path
    )
    outputs: list[str] = []
    for optimize in (False, True):
        command = [sys.executable]
        if optimize:
            command.append("-O")
        command.extend(["-c", script, str(context_path)])
        result = subprocess.run(
            command,
            check=False,
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize(
    ("manifest_state", "expected_reason"),
    [
        pytest.param("missing", "runtime_manifest_missing", id="QP-CONTEXT-MANIFEST-MISSING"),
        pytest.param(
            "unreadable",
            "runtime_manifest_unreadable",
            id="QP-CONTEXT-MANIFEST-UNREADABLE",
        ),
    ],
)
def test_quality_panel_context_preserves_guidance_manifest_input_state(
    tmp_path: Path,
    manifest_state: str,
    expected_reason: str,
) -> None:
    ws = _workspace(tmp_path)
    manifest_path = ws / "output" / "intermediate" / "runtime_manifest.json"
    if manifest_state == "missing":
        manifest_path.unlink()
    else:
        manifest_path.write_bytes(b"\xff\xfe\x00invalid-runtime-manifest")

    direct = quality_panel_module.project_workspace_guidance_manifestation(ws)
    status = build_workspace_status(ws)["guidance_manifestation"]
    panel = build_quality_panel(ws)["guidance_manifestation"]

    assert direct["reason"] == expected_reason
    assert status["reason"] == expected_reason
    assert panel["reason"] == expected_reason


def test_quality_panel_context_distinguishes_stale_disk_and_fresh_explicit_registry(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    registry_path = ws / "output" / "intermediate" / "artifact_registry.json"
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    stale_registry_bytes = registry_path.read_bytes()
    _write_materiality_inputs(ws)

    status_materiality = build_workspace_status(ws)["materiality_selection"]
    ordinary_context = build_quality_panel_producer_context(ws)
    ordinary_panel = project_quality_panel(producer_context=ordinary_context)

    assert ordinary_panel["materiality_selection"] == status_materiality
    assert registry_path.read_bytes() == stale_registry_bytes

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    fresh_registry = _json(registry_path)
    replay_context = build_quality_panel_producer_context(
        ws,
        artifact_registry=fresh_registry,
    )
    refreshed_context = build_quality_panel_producer_context(ws)

    assert replay_context["materiality_selection"] == refreshed_context[
        "materiality_selection"
    ]
    assert project_quality_panel(
        producer_context=replay_context,
    ) == project_quality_panel(producer_context=refreshed_context)


def test_quality_panel_context_calls_each_child_workspace_projector_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    claim_support_matrix = importlib.import_module(
        "multi_agent_brief.orchestrator.runtime_state.claim_support_matrix"
    )
    semantic_assessment_report = importlib.import_module(
        "multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report"
    )

    ws = _workspace(tmp_path)
    calls: dict[str, int] = {}
    projectors = [
        (quality_panel_module, "project_workspace_policy_profile"),
        (quality_panel_module, "project_workspace_materiality_selection"),
        (quality_panel_module, "project_workspace_support_wording"),
        (quality_panel_module, "project_workspace_report_template_conformance"),
        (quality_panel_module, "project_workspace_trajectory_regulation"),
        (quality_panel_module, "project_workspace_guidance_manifestation"),
        (claim_support_matrix, "project_claim_support_matrix_from_workspace"),
        (semantic_assessment_report, "project_semantic_assessment_report_from_workspace"),
    ]
    for module, name in projectors:
        original = getattr(module, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            calls[_name] = calls.get(_name, 0) + 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)

    build_quality_panel_producer_context(ws)

    assert calls == {name: 1 for _module, name in projectors}


def test_quality_panel_materializer_allows_only_one_registry_reprojection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    _write_source_evidence_pack(ws)
    refresh_calls = 0
    write_calls = 0
    original_refresh = quality_closeout._refresh_runtime_state
    original_write = quality_panel_module.write_quality_panel

    def counted_refresh(**kwargs):
        nonlocal refresh_calls
        refresh_calls += 1
        return original_refresh(**kwargs)

    def counted_write(**kwargs):
        nonlocal write_calls
        write_calls += 1
        return original_write(**kwargs)

    monkeypatch.setattr(quality_closeout, "_refresh_runtime_state", counted_refresh)
    monkeypatch.setattr(quality_panel_module, "write_quality_panel", counted_write)

    result = materialize_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())

    assert result["status"] == "complete"
    assert refresh_calls == 2
    assert write_calls == 2


def test_quality_panel_second_registry_replay_mismatch_fails_value_free(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    refresh_calls = 0
    write_calls = 0
    original_write = quality_panel_module.write_quality_panel

    def mismatching_refresh(**_kwargs):
        nonlocal refresh_calls
        refresh_calls += 1
        return {
            "artifact_registry": {
                "artifacts": {
                    "quality_panel": {
                        "status": "invalid",
                        "validation_result": (
                            "quality_panel_validation_error:producer_replay_mismatch"
                        ),
                    }
                }
            }
        }

    def counted_write(**kwargs):
        nonlocal write_calls
        write_calls += 1
        return original_write(**kwargs)

    monkeypatch.setattr(quality_closeout, "_refresh_runtime_state", mismatching_refresh)
    monkeypatch.setattr(
        quality_closeout,
        "interpret_quality_panel_closeout",
        lambda **_kwargs: QualityPanelDegradation(
            "quality_panel_registry_producer_replay_mismatch"
        ),
    )
    monkeypatch.setattr(quality_panel_module, "write_quality_panel", counted_write)

    with pytest.raises(QualityPanelCloseoutError) as excinfo:
        materialize_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())

    assert excinfo.value.reason_code == "quality_projection_registry_binding_invalid"
    assert refresh_calls == 2
    assert write_calls == 2
    assert "expected" not in excinfo.value.details
    assert "actual" not in excinfo.value.details


def test_quality_panel_builds_incomplete_projection_without_writing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)

    payload = build_quality_panel(ws)

    assert payload["schema_version"] == "briefloop.quality_panel.v1"
    assert "generated_at" not in payload
    assert payload["boundary"] == QUALITY_PANEL_BOUNDARY
    assert payload["runtime_effect"] == "projection_only"
    assert payload["overall_status"] == "incomplete"
    assert payload["source_evidence"]["source_pack_status"] == "missing"
    assert payload["control_integrity"]["fact_layer_status"] == "missing"
    assert payload["quality_panel_closeout"]["status"] == "not_ready"
    assert payload["recommended_actions"][0]["action"] == "materialize_durable_source_evidence"
    assert not quality_panel_path(ws).exists()
    assert validate_quality_panel_payload(payload) is None


def test_status_recommends_quality_closeout_after_finalize_report_pass(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    status = build_workspace_status(ws)
    closeout = status["quality_panel_closeout"]

    assert closeout["status"] == "recommended"
    assert closeout["command"] == "briefloop quality summarize --workspace <workspace>"
    assert closeout["runtime_effect"] == "operator_followup_only"
    assert closeout["delivery_authority"] is False
    assert closeout["release_authority"] is False
    assert "quality_panel.json" in "\n".join(closeout["missing_artifacts"])
    assert "quality_panel_closeout: recommended" in format_workspace_status(status)


@pytest.mark.parametrize(
    ("registry_verdict", "expected_status", "expected_reason"),
    [
        pytest.param(
            None,
            "stale_or_invalid",
            "quality_panel_registry_verdict_missing",
            id="QP-STATUS-REGISTRY-VERDICT-MISSING",
        ),
        pytest.param(
            RegistryDegradation("artifact_registry_recovery_context_invalid"),
            "stale_or_invalid",
            "quality_panel_registry_degradation",
            id="QP-STATUS-REGISTRY-DEGRADATION",
        ),
        pytest.param(
            RegistrySnapshotDrift("artifact_registry_snapshot_sha256_drift"),
            "stale_or_invalid",
            "quality_panel_registry_snapshot_drift",
            id="QP-STATUS-REGISTRY-SNAPSHOT-DRIFT",
        ),
        pytest.param(
            RegistryNotMaterialized(),
            "not_ready",
            "quality_panel_registry_not_materialized",
            id="QP-STATUS-REGISTRY-NOT-MATERIALIZED",
        ),
    ],
)
def test_quality_panel_closeout_registry_precedence_matrix(
    tmp_path: Path,
    registry_verdict: object | None,
    expected_status: str,
    expected_reason: str,
) -> None:
    ws = _workspace(tmp_path)

    projection = quality_closeout.quality_panel_closeout_projection(
        workspace=ws,
        finalize_report={
            "status": "pass",
            "reader_clean": {"status": "pass", "sample_findings": []},
        },
        registry_verdict=registry_verdict,
    )

    assert projection["status"] == expected_status
    assert projection["reason"] == expected_reason
    assert projection["present_artifacts"] == []


@pytest.mark.parametrize(
    ("present_filenames", "expected_status", "expected_bucket"),
    [
        pytest.param((), "not_ready", "missing_artifacts", id="QP-STATUS-NRM-NONE"),
        pytest.param(
            ("quality_panel.json",),
            "stale_or_invalid",
            "invalid_artifacts",
            id="QP-STATUS-NRM-ONE",
        ),
        pytest.param(
            ("quality_panel.json", "quality_summary.md", "quality_panel.html"),
            "stale_or_invalid",
            "invalid_artifacts",
            id="QP-STATUS-NRM-ALL",
        ),
    ],
)
def test_quality_panel_closeout_registry_not_materialized_presence_matrix(
    tmp_path: Path,
    present_filenames: tuple[str, ...],
    expected_status: str,
    expected_bucket: str,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    (intermediate / "artifact_registry.json").unlink(missing_ok=True)
    for filename in present_filenames:
        (intermediate / filename).write_text("unbound projection\n", encoding="utf-8")

    projection = quality_closeout.quality_panel_closeout_projection(
        workspace=ws,
        finalize_report={
            "status": "pass",
            "reader_clean": {"status": "pass", "sample_findings": []},
        },
        registry_verdict=RegistryNotMaterialized(),
    )

    assert projection["status"] == expected_status
    assert projection["reason"] == "quality_panel_registry_not_materialized"
    assert projection[expected_bucket] == list(
        quality_closeout.QUALITY_PANEL_CLOSEOUT_ARTIFACTS
    )
    other_bucket = (
        "invalid_artifacts"
        if expected_bucket == "missing_artifacts"
        else "missing_artifacts"
    )
    assert projection[other_bucket] == []


def test_quality_summarize_marks_closeout_generated_and_then_complete(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)

    result = materialize_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())
    panel = _json(quality_panel_path(ws))

    assert "generated_at" not in panel
    assert panel["quality_panel_closeout"]["status"] == "generated"
    assert panel["quality_panel_closeout"]["audit_bundle"] == "included_when_present_and_valid"
    assert panel["quality_panel_closeout"]["delivery_bundle"] == "excluded"
    assert panel["quality_panel_closeout"]["gate_authority"] is False
    assert validate_quality_panel_payload(panel) is None
    assert result["status"] == "complete"
    assert result["reason_code"] == "quality_projection_materialized"
    assert result["artifacts"]["quality_summary"]["path"] == "output/intermediate/quality_summary.md"
    assert result["artifacts"]["quality_panel_html"]["path"] == "output/intermediate/quality_panel.html"
    assert result["registry_refresh"]["status"] == "complete"

    status = build_workspace_status(ws)
    assert status["quality_panel_closeout"]["status"] == "complete"
    assert not status["quality_panel_closeout"]["missing_artifacts"]

    summary_text = quality_summary_path(ws).read_text(encoding="utf-8")
    html_text = quality_panel_html_path(ws).read_text(encoding="utf-8")
    assert "## Quality Closeout And Bundle Separation" in summary_text
    assert "- Delivery bundle: `excluded`" in summary_text
    assert "Quality Closeout And Bundle Separation" in html_text


def test_quality_panel_legacy_generated_at_cannot_self_validate(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    materialize_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())

    panel_path = quality_panel_path(ws)
    panel = _json(panel_path)
    panel["generated_at"] = "2099-01-01T00:00:00Z"
    panel_path.write_text(
        json.dumps(panel, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_quality_summary(workspace=ws, panel_payload=panel)
    write_quality_panel_html(workspace=ws, panel_payload=panel)

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    assert registry["artifacts"]["quality_panel"]["status"] == "invalid"
    assert registry["artifacts"]["quality_panel"]["validation_result"] == (
        "quality_panel_validation_error:producer_replay_mismatch"
    )
    verdict = interpret_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())
    assert isinstance(verdict, QualityPanelDegradation)

    repaired = materialize_quality_panel_closeout(
        workspace=ws,
        repo_workdir=Path.cwd(),
    )
    assert repaired["status"] == "complete"
    assert "generated_at" not in _json(panel_path)
    assert isinstance(
        interpret_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd()),
        CanonicalQualityPanelView,
    )


@pytest.mark.parametrize(
    ("case", "expected_kind", "expected_reason"),
    [
        pytest.param(
            "all_absent",
            QualityPanelNotMaterialized,
            "quality_panel_not_materialized",
            id="QP-READ-ALL-ABSENT",
        ),
        pytest.param(
            "all_absent_registry_missing",
            QualityPanelNotMaterialized,
            "quality_panel_not_materialized",
            id="QP-READ-ALL-ABSENT-REGISTRY-MISSING",
        ),
        pytest.param(
            "all_absent_registry_malformed",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-ALL-ABSENT-REGISTRY-MALFORMED",
        ),
        pytest.param(
            "all_absent_registry_cross_run",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-ALL-ABSENT-REGISTRY-CROSS-RUN",
        ),
        pytest.param(
            "all_absent_registry_snapshot_drift",
            QualityPanelDegradation,
            "quality_panel_registry_snapshot_drift",
            id="QP-READ-ALL-ABSENT-REGISTRY-SNAPSHOT-DRIFT",
        ),
        pytest.param(
            "all_absent_registry_dangling",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-ALL-ABSENT-REGISTRY-DANGLING",
        ),
        pytest.param(
            "dangling_quality_panel",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-DANGLING-PANEL",
        ),
        pytest.param(
            "dangling_quality_summary",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-DANGLING-SUMMARY",
        ),
        pytest.param(
            "dangling_quality_panel_html",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-DANGLING-HTML",
        ),
        pytest.param(
            "quality_panel_directory",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-NON-REGULAR-PANEL",
        ),
        pytest.param(
            "partial",
            QualityPanelDegradation,
            "quality_panel_artifact_set_incomplete",
            id="QP-READ-PARTIAL",
        ),
        pytest.param(
            "canonical",
            CanonicalQualityPanelView,
            None,
            id="QP-READ-CANONICAL",
        ),
        pytest.param(
            "registry_missing",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-REGISTRY-MISSING",
        ),
        pytest.param(
            "registry_mutated",
            QualityPanelDegradation,
            "quality_panel_registry_snapshot_drift",
            id="QP-READ-REGISTRY-MUTATED",
        ),
        pytest.param(
            "artifact_mutated",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-ARTIFACT-MUTATED",
        ),
        pytest.param(
            "all_deleted_after_canonical",
            QualityPanelDegradation,
            "quality_panel_registry_degradation",
            id="QP-READ-REGISTRY-VALID-ALL-DELETED",
        ),
    ],
)
def test_quality_panel_read_interpreter_matrix(
    tmp_path: Path,
    case: str,
    expected_kind: type,
    expected_reason: str | None,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    if case == "partial":
        write_quality_panel(workspace=ws)
        assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    elif case in {
        "canonical",
        "registry_missing",
        "registry_mutated",
        "artifact_mutated",
        "all_deleted_after_canonical",
    }:
        materialize_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())
    elif case == "all_absent_registry_snapshot_drift":
        (ws / "output" / "brief.md").write_text(
            "# Reader Brief\n\nCurrent reader text.\n",
            encoding="utf-8",
        )
        assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    registry_path = ws / "output" / "intermediate" / "artifact_registry.json"
    if case in {"registry_missing", "all_absent_registry_missing"}:
        registry_path.unlink()
    elif case == "all_absent_registry_malformed":
        registry_path.write_text("{broken\n", encoding="utf-8")
    elif case == "all_absent_registry_cross_run":
        registry = _json(registry_path)
        registry["run_id"] = "run-from-another-workspace"
        registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif case == "all_absent_registry_snapshot_drift":
        registry = _json(registry_path)
        registry["artifacts"]["reader_brief"]["sha256"] = "0" * 64
        registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif case == "all_absent_registry_dangling":
        registry_path.unlink()
        registry_path.symlink_to("missing-artifact-registry.json")
    elif case.startswith("dangling_"):
        target_by_case = {
            "dangling_quality_panel": quality_panel_path(ws),
            "dangling_quality_summary": quality_summary_path(ws),
            "dangling_quality_panel_html": quality_panel_html_path(ws),
        }
        target_by_case[case].symlink_to(f"missing-{target_by_case[case].name}")
    elif case == "quality_panel_directory":
        quality_panel_path(ws).mkdir()
    elif case == "registry_mutated":
        registry = _json(registry_path)
        registry["artifacts"]["quality_panel"]["sha256"] = "0" * 64
        registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif case == "artifact_mutated":
        quality_panel_html_path(ws).write_text(
            quality_panel_html_path(ws).read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
    elif case == "all_deleted_after_canonical":
        quality_panel_path(ws).unlink()
        quality_summary_path(ws).unlink()
        quality_panel_html_path(ws).unlink()

    verdict = interpret_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())

    assert isinstance(verdict, expected_kind)
    if expected_reason is not None:
        assert verdict.reason_code == expected_reason
        assert set(vars(verdict)) <= {"kind", "reason_code"}
    else:
        assert isinstance(verdict, CanonicalQualityPanelView)
        assert set(verdict.artifact_paths) == {
            "quality_panel",
            "quality_summary",
            "quality_panel_html",
        }
        assert all(verdict.artifact_sha256.values())


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("parent_missing", id="QP-PRESENCE-PARENT-MISSING"),
        pytest.param("parent_dangling", id="QP-PRESENCE-PARENT-DANGLING"),
        pytest.param("parent_escape", id="QP-PRESENCE-PARENT-ESCAPE"),
    ],
)
def test_quality_panel_presence_context_fails_closed(
    tmp_path: Path,
    case: str,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    intermediate.rename(ws / "output" / "intermediate.backup")
    if case == "parent_dangling":
        intermediate.symlink_to("missing-intermediate", target_is_directory=True)
    elif case == "parent_escape":
        external = tmp_path / "external-intermediate"
        external.mkdir()
        intermediate.symlink_to(external, target_is_directory=True)

    verdict = interpret_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())

    assert isinstance(verdict, QualityPanelDegradation)
    assert verdict.reason_code == "quality_panel_presence_context_invalid"
    assert set(vars(verdict)) <= {"kind", "reason_code"}


def test_quality_panel_presence_probe_error_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    original_probe = quality_closeout._path_entry_presence

    def fail_panel_probe(path: Path):
        if path.name == "quality_panel.json":
            return "unsafe"
        return original_probe(path)

    monkeypatch.setattr(quality_closeout, "_path_entry_presence", fail_panel_probe)

    verdict = interpret_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())

    assert isinstance(verdict, QualityPanelDegradation)
    assert verdict.reason_code == "quality_panel_presence_probe_failed"
    assert set(vars(verdict)) <= {"kind", "reason_code"}


def test_quality_closeout_registry_refresh_failure_keeps_repair_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)

    def fail_refresh(**_kwargs):
        raise RuntimeError("synthetic registry refresh failure")

    monkeypatch.setattr(quality_closeout, "_refresh_runtime_state", fail_refresh)

    with pytest.raises(QualityPanelCloseoutError) as excinfo:
        materialize_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())

    assert excinfo.value.reason_code == "quality_projection_registry_refresh_failed"
    assert quality_panel_path(ws).exists()
    assert quality_summary_path(ws).exists()
    assert quality_panel_html_path(ws).exists()
    assert build_workspace_status(ws)["quality_panel_closeout"]["status"] != "complete"


@pytest.mark.parametrize(
    (
        "as_json",
        "is_interactive",
        "open_outcome",
        "expected_status",
        "expected_reason",
        "expected_calls",
    ),
    [
        pytest.param(
            True,
            True,
            True,
            "skipped",
            "quality_panel_browser_suppressed_for_json",
            0,
            id="QP-AUTO-BROWSER-JSON",
        ),
        pytest.param(
            False,
            False,
            True,
            "skipped",
            "quality_panel_browser_suppressed_for_non_interactive_output",
            0,
            id="QP-AUTO-BROWSER-NONINTERACTIVE",
        ),
        pytest.param(
            False,
            True,
            True,
            "opened",
            "quality_panel_opened_in_default_browser",
            1,
            id="QP-AUTO-BROWSER-OPENED",
        ),
        pytest.param(
            False,
            True,
            False,
            "warning",
            "quality_panel_browser_open_rejected",
            1,
            id="QP-AUTO-BROWSER-REJECTED",
        ),
        pytest.param(
            False,
            True,
            RuntimeError("synthetic browser failure"),
            "warning",
            "quality_panel_browser_open_failed",
            1,
            id="QP-AUTO-BROWSER-ERROR",
        ),
    ],
)
def test_quality_panel_browser_display_matrix(
    tmp_path: Path,
    monkeypatch,
    as_json: bool,
    is_interactive: bool,
    open_outcome: bool | Exception,
    expected_status: str,
    expected_reason: str,
    expected_calls: int,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    materialization = materialize_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())
    calls: list[tuple[str, int]] = []

    def fake_open(url: str, *, new: int = 0) -> bool:
        calls.append((url, new))
        if isinstance(open_outcome, Exception):
            raise open_outcome
        return open_outcome

    monkeypatch.setattr(quality_closeout.webbrowser, "open", fake_open)

    display = display_quality_panel_closeout(
        materialization,
        as_json=as_json,
        is_interactive=is_interactive,
    )

    assert display["status"] == expected_status
    assert display["reason_code"] == expected_reason
    assert len(calls) == expected_calls
    if calls:
        assert calls[0][0].startswith("file://")
        assert calls[0][1] == 2


def test_quality_closeout_rejects_stale_or_hand_edited_quality_artifacts(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
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
    assert closeout["reason"] == "quality_panel_registry_degradation"
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


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("overall_status", id="QP-REPLAY-OVERALL-STATUS"),
        pytest.param("recommended_actions", id="QP-REPLAY-RECOMMENDED-ACTIONS"),
        pytest.param("control_projection", id="QP-REPLAY-CONTROL"),
        pytest.param("source_projection", id="QP-REPLAY-SOURCE"),
        pytest.param("gate_projection", id="QP-REPLAY-GATE"),
        pytest.param("delivery_projection", id="QP-REPLAY-DELIVERY"),
        pytest.param("run_identity", id="QP-REPLAY-RUN-IDENTITY"),
    ],
)
def test_quality_panel_registry_replay_rejects_coherent_hand_edit(
    tmp_path: Path,
    case: str,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    materialize_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())
    panel_path = quality_panel_path(ws)
    panel = _json(panel_path)
    if case == "overall_status":
        panel["overall_status"] = (
            "pass" if panel["overall_status"] != "pass" else "warning"
        )
    elif case == "recommended_actions":
        panel["recommended_actions"].append(
            {"action": "inspect_run_integrity", "reason": "forged_action"}
        )
    elif case == "control_projection":
        panel["control_integrity"]["reference_eligible"] = not panel[
            "control_integrity"
        ]["reference_eligible"]
    elif case == "source_projection":
        panel["source_evidence"]["source_count"] += 1
    elif case == "gate_projection":
        panel["gates"]["warning_count"] += 1
    elif case == "delivery_projection":
        panel["delivery"]["duplicate_citation_count"] += 1
    elif case == "run_identity":
        panel["run_id"] = "forged-run"
    panel_path.write_text(
        json.dumps(panel, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_quality_summary(workspace=ws, panel_payload=panel)
    write_quality_panel_html(workspace=ws, panel_payload=panel)

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    record = registry["artifacts"]["quality_panel"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == (
        "quality_panel_validation_error:producer_replay_mismatch"
    )
    assert "expected" not in record
    assert "actual" not in record
    verdict = interpret_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())
    assert isinstance(verdict, QualityPanelDegradation)


def test_quality_panel_registry_replay_rejects_stale_authoritative_inputs(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    materialize_quality_panel_closeout(workspace=ws, repo_workdir=Path.cwd())
    finalize_path = ws / "output" / "intermediate" / "finalize_report.json"
    finalize_report = _json(finalize_path)
    finalize_report["duplicate_citation_count"] = 1
    finalize_path.write_text(
        json.dumps(finalize_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    assert registry["artifacts"]["quality_panel"]["status"] == "invalid"
    assert registry["artifacts"]["quality_panel"]["validation_result"] == (
        "quality_panel_validation_error:producer_replay_mismatch"
    )


def test_quality_panel_handles_corrupt_finalize_report_utf8_without_crashing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    (ws / "output" / "intermediate" / "finalize_report.json").write_bytes(b"\xff\xfe")

    status = build_workspace_status(ws)
    panel = build_quality_panel(ws)

    assert status["quality_panel_closeout"]["status"] == "stale_or_invalid"
    assert status["quality_panel_closeout"]["reason"] == (
        "quality_panel_registry_degradation"
    )
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


def test_quality_summarize_cli_writes_panel_and_summary_json(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    capsys.readouterr()
    monkeypatch.setattr(
        quality_closeout.webbrowser,
        "open",
        lambda *_args, **_kwargs: pytest.fail("--json must not open a browser"),
    )

    assert main(["quality", "summarize", "--workspace", str(ws), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["quality_panel"] == "output/intermediate/quality_panel.json"
    assert payload["quality_summary"] == "output/intermediate/quality_summary.md"
    assert payload["quality_panel_html"] == "output/intermediate/quality_panel.html"
    assert payload["registry_refresh"]["status"] == "complete"
    assert payload["browser_display"]["status"] == "skipped"
    assert payload["browser_display"]["reason_code"] == "quality_panel_browser_suppressed_for_json"
    assert payload["boundary"] == "quality_projection_only_not_gate_or_release_authority"
    assert "not_release_authorization" in payload["non_claims"]
    assert quality_panel_path(ws).exists()
    assert quality_summary_path(ws).exists()
    assert quality_panel_html_path(ws).exists()
    assert validate_quality_summary_markdown(quality_summary_path(ws).read_text(encoding="utf-8")) is None
    assert validate_quality_panel_html(quality_panel_html_path(ws).read_text(encoding="utf-8")) is None
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    assert registry["artifacts"]["quality_panel"]["status"] == "valid"
    assert registry["artifacts"]["quality_summary"]["status"] == "valid"
    assert registry["artifacts"]["quality_panel_html"]["status"] == "valid"


def test_quality_summarize_cli_human_output_keeps_projection_boundary(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    capsys.readouterr()
    opened: list[str] = []
    monkeypatch.setattr(product_commands, "_stdout_is_interactive", lambda: True)
    monkeypatch.setattr(
        quality_closeout.webbrowser,
        "open",
        lambda url, **_kwargs: opened.append(url) or True,
    )

    assert main(["quality", "summarize", "--workspace", str(ws)]) == 0
    output = capsys.readouterr().out

    assert "quality_panel: output/intermediate/quality_panel.json" in output
    assert "quality_summary: output/intermediate/quality_summary.md" in output
    assert "quality_panel_html: output/intermediate/quality_panel.html" in output
    assert "registry_refresh: complete" in output
    assert "browser_display: opened" in output
    assert "quality projection only" in output
    assert "no gates were run" in output
    assert "no release was authorized" in output
    assert "ready to publish" not in output.lower()
    assert "truth proven" not in output.lower()
    assert len(opened) == 1
    assert opened[0].startswith("file://")


def test_quality_summarize_cli_registry_failure_is_nonzero_without_changing_finalize_truth(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_finalize_report(ws)
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    finalize_path = ws / "output" / "intermediate" / "finalize_report.json"
    workflow_before = workflow_path.read_bytes()
    finalize_before = finalize_path.read_bytes()
    capsys.readouterr()

    def fail_refresh(**_kwargs):
        raise RuntimeError("synthetic registry refresh failure")

    monkeypatch.setattr(quality_closeout, "_refresh_runtime_state", fail_refresh)

    assert main(["quality", "summarize", "--workspace", str(ws), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["reason_code"] == "quality_projection_registry_refresh_failed"
    assert workflow_path.read_bytes() == workflow_before
    assert finalize_path.read_bytes() == finalize_before


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
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
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
