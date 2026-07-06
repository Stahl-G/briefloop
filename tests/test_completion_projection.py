from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.orchestrator.runtime_state import (
    COMPLETION_PROJECTION_SCHEMA_VERSION,
    build_completion_projection,
    initialize_runtime_state,
    runtime_state_paths,
)
from tests.helpers import sha256_file as _sha256_file
from tests.helpers import write_minimal_workspace


ROOT = Path(__file__).resolve().parents[1]


def _workspace(tmp_path: Path) -> Path:
    ws = write_minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    return ws


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _set_workflow(ws: Path, **updates: object) -> None:
    path = runtime_state_paths(ws)["workflow_state"]
    payload = _read_json(path)
    payload.update(updates)
    _write_json(path, payload)


def _write_gate(ws: Path, *, stage: str = "finalize", status: str = "pass", blocking: bool = False) -> None:
    findings = []
    gate_results = []
    if blocking:
        findings = [
            {
                "finding_id": "F-001",
                "finding_type": "coverage_gap",
                "severity": "high",
                "blocking_level": "blocking",
                "blocking": True,
                "stage_id": stage,
                "artifact_id": "reader_brief",
            }
        ]
        gate_results = [
            {
                "gate_id": "coverage_omission",
                "status": "fail",
                "blocking": True,
                "finding_ids": ["F-001"],
            }
        ]
    _write_json(
        ws / "output" / "intermediate" / "gates" / f"{stage}_quality_gate_report.json",
        {
            "schema_version": "multi-agent-brief-quality-gates/v1",
            "created_at": "2026-07-06T00:00:00Z",
            "updated_at": "2026-07-06T00:00:00Z",
            "workspace": str(ws),
            "report_date": "2026-07-06",
            "policy_pack": "default",
            "status": status,
            "gate_results": gate_results,
            "findings": findings,
            "metadata": {},
        },
    )


def _write_valid_delivery(ws: Path) -> None:
    delivery = ws / "output" / "delivery"
    intermediate = ws / "output" / "intermediate"
    delivery.mkdir(parents=True, exist_ok=True)
    brief = delivery / "brief.md"
    brief.write_text("# Reader Brief\n\nClean reader text.\n", encoding="utf-8")
    manifest_path = intermediate / "delivery_manifest.json"
    manifest = {
        "schema_version": "briefloop.delivery_manifest.v1",
        "status": "promoted",
        "finalize_transaction_id": "tx-valid",
        "reader_clean_status": "pass",
        "delivery_dir": "output/delivery",
        "artifacts": [
            {
                "path": "output/delivery/brief.md",
                "sha256": _sha256_file(brief),
                "kind": "reader_markdown",
            }
        ],
    }
    _write_json(manifest_path, manifest)
    _write_json(
        intermediate / "finalize_report.json",
        {
            "status": "pass",
            "reader_clean": {"status": "pass", "sample_findings": []},
            "delivery_artifacts": [str(brief)],
            "delivery_artifact_sha256": {str(brief): _sha256_file(brief)},
            "delivery_manifest": "output/intermediate/delivery_manifest.json",
            "delivery_manifest_sha256": _sha256_file(manifest_path),
        },
    )


def test_completion_projection_reports_valid_delivery_truth(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _set_workflow(ws, current_stage="finalize")
    _write_gate(ws, stage="finalize", status="warning")
    _write_valid_delivery(ws)

    payload = build_completion_projection(workspace=ws)

    assert payload["schema_version"] == COMPLETION_PROJECTION_SCHEMA_VERSION
    assert payload["runtime_effect"] == "read_only_completion_projection"
    assert payload["gate_truth"]["status"] == "warning"
    assert payload["gate_truth"]["blocking"] is False
    assert payload["finalize_truth"]["status"] == "pass"
    assert payload["delivery_truth"]["valid"] is True
    assert payload["delivery_truth"]["paths_current"] is True
    assert payload["delivery_truth"]["hash_bound"] is True
    assert payload["next_allowed_action"] == "inspect_status_before_delivery_or_quality"


def test_completion_projection_rejects_delivery_dir_without_manifest(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _set_workflow(ws, current_stage="finalize")
    _write_gate(ws, stage="finalize")
    _write_valid_delivery(ws)
    (ws / "output" / "intermediate" / "delivery_manifest.json").unlink()

    payload = build_completion_projection(workspace=ws)

    assert payload["delivery_truth"]["valid"] is False
    assert payload["delivery_truth"]["manifest_status"] == "missing"
    assert "delivery_manifest_missing" in payload["delivery_truth"]["findings"]
    assert payload["next_allowed_action"] == "inspect_invalid_delivery_truth"


def test_completion_projection_rejects_missing_delivery_artifact(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _set_workflow(ws, current_stage="finalize")
    _write_gate(ws, stage="finalize")
    _write_valid_delivery(ws)
    (ws / "output" / "delivery" / "brief.md").unlink()

    payload = build_completion_projection(workspace=ws)

    assert payload["delivery_truth"]["valid"] is False
    assert "delivery_artifact_missing:output/delivery/brief.md" in payload["delivery_truth"]["findings"]
    assert payload["next_allowed_action"] == "inspect_invalid_delivery_truth"


def test_completion_projection_rejects_finalize_failure_without_promoting_stale_delivery(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _set_workflow(ws, current_stage="finalize")
    _write_gate(ws, stage="finalize")
    _write_valid_delivery(ws)
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = _read_json(report_path)
    report["status"] = "fail"
    report["reader_clean"] = {"status": "fail", "sample_findings": [{"message": "residue"}]}
    _write_json(report_path, report)

    payload = build_completion_projection(workspace=ws)

    assert payload["finalize_truth"]["status"] == "fail"
    assert payload["delivery_truth"]["valid"] is False
    assert "delivery_dir_not_current_after_failed_finalize" in payload["delivery_truth"]["findings"]
    assert payload["next_allowed_action"] == "stop_finalize_failed_no_valid_delivery"


def test_completion_projection_stops_on_contaminated_run(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _set_workflow(
        ws,
        current_stage="finalize",
        run_integrity={
            "status": "contaminated",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [{"reason_code": "frozen_artifact_changed", "message": "Changed."}],
        },
    )
    _write_gate(ws, stage="finalize")
    _write_valid_delivery(ws)

    payload = build_completion_projection(workspace=ws)

    assert payload["run_integrity"]["status"] == "contaminated"
    assert payload["next_allowed_action"] == "stop_run_integrity_not_clean"


def test_completion_projection_stops_on_active_repair(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _set_workflow(
        ws,
        current_stage="editor",
        active_repair={
            "schema_version": "mabw.active_repair.v1",
            "repair_owner": "editor",
        },
    )

    payload = build_completion_projection(workspace=ws)

    assert payload["workflow"]["active_repair"] is True
    assert payload["next_allowed_action"] == "stop_complete_or_inspect_active_repair"


def test_completion_projection_stops_on_blocked_workflow(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _set_workflow(ws, current_stage="editor", blocked=True, blocking_reason="human review")

    payload = build_completion_projection(workspace=ws)

    assert payload["workflow"]["blocked"] is True
    assert payload["next_allowed_action"] == "stop_workflow_blocked_human_review_required"


def test_completion_projection_stops_on_malformed_gate_report(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _set_workflow(ws, current_stage="finalize")
    _write_json(
        ws / "output" / "intermediate" / "gates" / "finalize_quality_gate_report.json",
        {"schema_version": "multi-agent-brief-quality-gates/v1", "status": "pass"},
    )
    _write_valid_delivery(ws)

    payload = build_completion_projection(workspace=ws)

    assert payload["gate_truth"]["status"] == "invalid"
    assert payload["gate_truth"]["blocking"] is True
    assert payload["next_allowed_action"] == "stop_resolve_blocking_gate_report"


def test_completion_projection_stops_on_blocking_gate_result(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _set_workflow(ws, current_stage="finalize")
    _write_gate(ws, stage="finalize", status="fail", blocking=True)
    _write_valid_delivery(ws)

    payload = build_completion_projection(workspace=ws)

    assert payload["gate_truth"]["blocking_count"] == 2
    assert payload["next_allowed_action"] == "stop_resolve_blocking_gate_report"
