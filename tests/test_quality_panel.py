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
from multi_agent_brief.product.quality_panel import (
    QUALITY_PANEL_BOUNDARY,
    QUALITY_SUMMARY_BOUNDARY,
    QualityPanelError,
    build_quality_panel,
    quality_panel_path,
    quality_summary_path,
    render_quality_summary,
    validate_quality_panel_payload,
    validate_quality_summary_markdown,
    write_quality_panel,
    write_quality_summary,
)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(
        "project:\n  name: Quality Panel Test\n",
        encoding="utf-8",
    )
    assert main(["state", "init", "--workspace", str(ws)]) == 0
    return ws


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


def _write_gate_report(
    ws: Path,
    *,
    status: str = "pass",
    findings: list[dict] | None = None,
    stage: str = "auditor",
) -> None:
    gates = ws / "output" / "intermediate" / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "mabw.quality_gate_report.v1",
        "status": status,
        "findings": findings or [],
        "metadata": {"gate_stage_id": stage},
    }
    (gates / f"{stage}_quality_gate_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_finalize_report(ws: Path, *, reader_status: str = "pass") -> None:
    report = {
        "status": "pass",
        "reader_clean": {"status": reader_status, "sample_findings": []},
        "duplicate_citation_count": 0,
        "source_appendix_warnings": [],
        "source_appendix_trace_warnings": [],
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
                "build_quality_panel, render_quality_summary; "
                "print(build_quality_panel, render_quality_summary)"
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
    assert payload["recommended_actions"][0]["action"] == "materialize_durable_source_evidence"
    assert not quality_panel_path(ws).exists()
    assert validate_quality_panel_payload(payload) is None


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
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0

    assert main(["state", "init", "--workspace", str(ws), "--reset-state"]) == 0

    intermediate = ws / "output" / "intermediate"
    assert (intermediate / f"quality_panel.{old_run_id}.json").exists()
    assert (intermediate / f"quality_summary.{old_run_id}.md").exists()
    assert not quality_panel_path(ws).exists()
    assert not quality_summary_path(ws).exists()
    assert main(["state", "check", "--workspace", str(ws), "--json"]) == 0
    registry = _json(intermediate / "artifact_registry.json")
    record = registry["artifacts"]["quality_panel"]
    assert record["status"] == "expected"
    assert record["sha256"] is None
    summary_record = registry["artifacts"]["quality_summary"]
    assert summary_record["status"] == "expected"
    assert summary_record["sha256"] is None


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
        "recommended_actions": [],
        "non_goals": ["quality_score"],
    }

    assert validate_quality_panel_payload(payload) == "quality_panel_schema_error:non_goals"
