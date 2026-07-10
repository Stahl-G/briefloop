"""Tests for the read-only writer-facing status command."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    complete_stage_transaction,
    initialize_runtime_state,
    record_decision,
    runtime_state_paths,
)
from tests.helpers import sha256_file as _sha256_file
from tests.helpers import write_minimal_workspace


def _minimal_workspace(path: Path) -> Path:
    return write_minimal_workspace(
        path,
        project_name="status-test",
        user_text="# Status test\n",
    )


def _mark_fact_layer_imported(ws: Path) -> None:
    paths = runtime_state_paths(ws)
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source-001.md").write_text("# Source\n\nExample evidence.\n", encoding="utf-8")
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "input_classification.json").write_text(
        json.dumps({
            "evidence": [{"path": "input/sources/source-001.md", "name": "source-001.md"}],
            "feedback": [],
            "instruction": [],
            "context": [],
            "skipped": [],
        })
        + "\n",
        encoding="utf-8",
    )
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "candidate_claims.json").write_text("[]\n", encoding="utf-8")
    (intermediate / "screened_candidates.json").write_text("[]\n", encoding="utf-8")
    (intermediate / "claim_ledger.json").write_text(
        json.dumps([
            {
                "claim_id": "CL-001",
                "statement": "ExampleCo opened a demo facility.",
                "source_id": "SRC-001",
                "evidence_text": "Example evidence.",
            }
        ])
        + "\n",
        encoding="utf-8",
    )
    imported_files = []
    for artifact_id, path in (
        ("durable_source_evidence_or_source_pack", source_dir / "source-001.md"),
        ("input_classification", output_dir / "input_classification.json"),
        ("candidate_claims", intermediate / "candidate_claims.json"),
        ("screened_candidates", intermediate / "screened_candidates.json"),
        ("claim_ledger", intermediate / "claim_ledger.json"),
    ):
        rel_path = path.relative_to(ws).as_posix()
        imported_files.append({
            "artifact_id": artifact_id,
            "archive_path": f"fact_layer/{rel_path}",
            "workspace_path": rel_path,
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        })
    manifest = json.loads(paths["runtime_manifest"].read_text(encoding="utf-8"))
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    fact_layer_sha256 = "a" * 64
    manifest["recipe"] = "fast-rerun"
    manifest["fact_layer_import"] = {
        "schema_version": "mabw.fact_layer_import.v1",
        "source_run_id": "mabw-20260614T000000Z-source",
        "source_archive_manifest": "output/runs/mabw-20260614T000000Z-source/manifest.json",
        "source_archive_manifest_sha256": "b" * 64,
        "fact_layer_sha256": fact_layer_sha256,
        "imported_file_count": len(imported_files),
        "imported_files": imported_files,
        "satisfied_stage_ids": [
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
        ],
    }
    statuses = dict(workflow.get("stage_statuses") or {})
    for stage_id in manifest["fact_layer_import"]["satisfied_stage_ids"]:
        statuses[stage_id] = {
            "status": "complete",
            "reason": "Satisfied by frozen fact layer import.",
            "updated_at": "2026-06-14T00:00:00+00:00",
            "metadata": {
                "satisfied_by_import": True,
                "fact_layer_import_sha256": fact_layer_sha256,
                "source_run_id": manifest["fact_layer_import"]["source_run_id"],
            },
        }
    statuses["analyst"] = {
        "status": "ready",
        "reason": "",
        "updated_at": "2026-06-14T00:00:00+00:00",
    }
    workflow["current_stage"] = "analyst"
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow["stage_statuses"] = statuses
    paths["runtime_manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _event(event_id: str, event_type: str, created_at: str, *, run_id: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "multi-agent-brief-event-log/v1",
        "event_id": event_id,
        "run_id": run_id,
        "created_at": created_at,
        "event_type": event_type,
        "actor": "cli",
        "stage_id": None,
        "artifact_id": None,
        "decision": None,
        "reason": "",
        "metadata": {},
    }
    payload.update(extra)
    return payload


def _completion(event_id: str, created_at: str, stage_id: str, *, run_id: str) -> dict[str, object]:
    return _event(
        event_id,
        "decision_recorded",
        created_at,
        run_id=run_id,
        stage_id=stage_id,
        decision="continue",
        metadata={"transaction_id": f"tx-{event_id}"},
    )


def _topology_satisfied(
    event_id: str,
    created_at: str,
    stage_id: str,
    *,
    run_id: str,
    trigger_stage: str,
) -> dict[str, object]:
    return _event(
        event_id,
        "stage_satisfied_by_topology",
        created_at,
        run_id=run_id,
        stage_id=stage_id,
        metadata={
            "transaction_id": f"tx-{event_id}",
            "topology": "default",
            "satisfied_by": trigger_stage,
            "satisfied_by_stage": trigger_stage,
            "required_artifacts": ["candidate_claims", "screened_candidates"],
        },
    )


def _write_auditable_target_complete_state(ws: Path) -> None:
    paths = runtime_state_paths(ws)
    condition_path = ws / "experiment" / "080" / "condition.json"
    condition_path.parent.mkdir(parents=True, exist_ok=True)
    condition_path.write_text(
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
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["current_stage"] = "finalize"
    workflow["blocked"] = False
    workflow["run_integrity"] = {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    audit_binding = {
        "schema_version": "mabw.auditable_audit_binding.v1",
        "source": "auditor_stage_complete",
        "claim_ledger_sha256": "d" * 64,
        "audited_brief_sha256": "a" * 64,
        "audit_report_sha256": "b" * 64,
        "relevant_repair_transaction_ids": [],
        "auditor_stage_transaction_id": "tx-auditor-complete",
    }
    workflow["stage_statuses"] = {
        "analyst": {"status": "complete"},
        "editor": {"status": "complete"},
        "auditor": {"status": "complete", "metadata": {"audit_binding": audit_binding}},
        "finalize": {"status": "ready"},
    }
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["artifact_registry"].write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-artifact-registry/v1",
                "run_id": workflow.get("run_id", "run-test"),
                "artifacts": {
                    "claim_ledger": {
                        "artifact_id": "claim_ledger",
                        "path": "output/intermediate/claim_ledger.json",
                        "status": "valid",
                        "sha256": "d" * 64,
                    },
                    "audited_brief": {
                        "artifact_id": "audited_brief",
                        "path": "output/intermediate/audited_brief.md",
                        "status": "valid",
                        "sha256": "a" * 64,
                    },
                    "audit_report": {
                        "artifact_id": "audit_report",
                        "path": "output/intermediate/audit_report.json",
                        "status": "valid",
                        "sha256": "b" * 64,
                    },
                    "auditor_quality_gate_report": {
                        "artifact_id": "auditor_quality_gate_report",
                        "path": "output/intermediate/gates/auditor_quality_gate_report.json",
                        "status": "valid",
                        "sha256": "c" * 64,
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
    gate_path = ws / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(
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
    run_id = str(workflow.get("run_id") or "run-test")
    events = [
        _event(
            "analyst-complete",
            "decision_recorded",
            "2026-06-14T00:02:00Z",
            run_id=run_id,
            stage_id="analyst",
            decision="continue",
            metadata={"transaction_id": "tx-analyst-complete"},
        ),
        _event(
            "editor-complete",
            "decision_recorded",
            "2026-06-14T00:03:00Z",
            run_id=run_id,
            stage_id="editor",
            decision="continue",
            metadata={"transaction_id": "tx-editor-complete"},
        ),
        _event(
            "auditor-complete",
            "decision_recorded",
            "2026-06-14T00:04:00Z",
            run_id=run_id,
            stage_id="auditor",
            decision="continue",
            metadata={"transaction_id": "tx-auditor-complete"},
        ),
    ]
    with paths["event_log"].open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def test_status_command_is_read_only_for_existing_runtime_state(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    paths = runtime_state_paths(ws)
    manifest = json.loads(paths["runtime_manifest"].read_text(encoding="utf-8"))
    paths["artifact_registry"].write_text(
        json.dumps(
                {
                    "schema_version": "multi-agent-brief-artifact-registry/v1",
                    "run_id": manifest["run_id"],
                "artifacts": {
                    "candidate_claims": {
                        "artifact_id": "candidate_claims",
                        "path": "output/intermediate/candidate_claims.json",
                        "status": "expected",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    watched = [path for path in paths.values() if path.exists()]
    before_bytes = {path: path.read_bytes() for path in watched}
    before_mtime = {path: path.stat().st_mtime_ns for path in watched}
    before_event_count = len(paths["event_log"].read_text(encoding="utf-8").splitlines())

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert payload["runtime"]["runtime"] == "claude"
    assert payload["workflow"]["current_stage"] == "doctor"
    assert payload["workflow"]["run_integrity"]["status"] == "clean"
    assert payload["workflow"]["run_integrity"]["reference_eligible"] is True
    assert payload["recovery_state"]["status"] == "not_applicable"
    assert payload["recovery_state"]["runtime_effect"] == "read_only_recovery_projection"
    assert payload["timing"]["schema_version"] == "mabw.control_timing.v1"
    assert payload["timing"]["source"] == "event_log"
    assert payload["timing"]["precision"] == "control_trace_bucket"
    assert payload["timing"]["status"] == "unknown"
    assert payload["artifacts"]["expected_count"] == 1
    assert payload["events"]["event_count"] == before_event_count
    assert payload["progress"] == {
        "schema_version": "briefloop.status_progress.v1",
        "runtime_effect": "read_only",
        "source": "workspace_status_projection",
        "current_stage": "doctor",
        "current_work": "prepare sources",
        "next_command": f"/briefloop run {ws}",
        "status": "ready_for_operator",
        "message": "Continue the prepare sources step through the suggested command or handoff.",
    }
    assert "stage-complete" not in payload["suggested_next_command"]
    assert payload["suggested_next_command"] == f"/briefloop run {ws}"

    for path in watched:
        assert path.read_bytes() == before_bytes[path]
        assert path.stat().st_mtime_ns == before_mtime[path]
    assert len(paths["event_log"].read_text(encoding="utf-8").splitlines()) == before_event_count


def test_status_command_human_output_reports_user_progress_language(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert '[status] progress: ready_for_operator current_work="prepare sources"' in out
    assert 'message="Continue the prepare sources step through the suggested command or handoff."' in out


def test_status_command_reports_trajectory_decision_narrowing(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    complete_stage_transaction(workspace=ws, stage_id="doctor", reason="doctor complete")
    for idx in range(3):
        record_decision(
            workspace=ws,
            stage_id="source-discovery",
            decision="retry_stage",
            reason=f"retry {idx + 1}",
        )

    rc = main(["status", "--workspace", str(ws), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["workflow"]["next_allowed_decisions"] == ["request_human_review", "block_run"]
    assert payload["workflow"]["trajectory_regulation"]["status"] == "decision_narrowed"
    assert payload["workflow"]["trajectory_regulation"]["reasons"] == ["retry_budget_exhausted"]
    assert payload["trajectory_regulation"]["status"] == "action_required"
    assert payload["progress"]["status"] == "human_review_needed"
    assert payload["progress"]["current_work"] == "prepare sources"
    assert payload["progress"]["message"] == "Retry or repair budget is exhausted; request human review or block the run."

    rc = main(["status", "--workspace", str(ws)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[status] trajectory_decision_narrowing: decision_narrowed" in out
    assert "allowed=request_human_review,block_run" in out
    assert "reasons=retry_budget_exhausted" in out


def test_status_command_reports_contaminated_run_integrity(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [
            {
                "reason_code": "run_reset",
                "message": "run_reset occurred; this run is not clean single-shot reference evidence.",
                "created_at": "2026-06-13T00:00:00+00:00",
            }
        ],
    }
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[status] run_integrity: contaminated reference_eligible=False" in out
    assert "[status] timing: contaminated; elapsed buckets are not clean evidence" in out


def test_status_command_reports_fact_layer_import_summary(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _mark_fact_layer_imported(ws)

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    summary = payload["fact_layer_import"]
    assert summary["status"] == "valid"
    assert summary["source_run_id"] == "mabw-20260614T000000Z-source"
    assert summary["fact_layer_sha256"] == "a" * 64
    assert summary["next_stage"] == "analyst"
    assert all(stage["display_status"] == "complete via import" for stage in summary["imported_stages"])
    assert payload["suggested_next_command"] == f"briefloop run --workspace {ws} --recipe fast-rerun --skip-doctor"


def test_status_command_recommends_quality_package_before_delivery(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["current_stage"] = "finalize"
    workflow["blocked"] = False
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finalize_report = ws / "output" / "intermediate" / "finalize_report.json"
    finalize_report.write_text(
        json.dumps(
            {
                "schema_version": "mabw.finalize_report.v1",
                "status": "pass",
                "reader_clean": {"status": "pass", "sample_findings": []},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    expected = f"briefloop quality summarize --workspace {ws}"
    assert payload["quality_panel_closeout"]["status"] == "recommended"
    assert payload["workflow"]["blocked"] is False
    assert payload["quality_panel_closeout"]["gate_authority"] is False
    assert payload["quality_panel_closeout"]["delivery_authority"] is False
    assert payload["quality_panel_closeout"]["release_authority"] is False
    assert payload["suggested_next_command"] == expected
    assert payload["progress"]["status"] == "needs_quality_package"
    assert payload["progress"]["current_work"] == "build quality package"
    assert payload["progress"]["next_command"] == expected
    assert "/briefloop deliver" not in payload["progress"]["next_command"]


def test_status_command_reports_invalid_fact_layer_import_when_file_missing(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _mark_fact_layer_imported(ws)
    (ws / "output" / "intermediate" / "claim_ledger.json").unlink()

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    summary = payload["fact_layer_import"]
    assert summary["status"] == "invalid"
    assert "Imported fact-layer file is missing: output/intermediate/claim_ledger.json." in summary["errors"]
    assert payload["suggested_next_command"] != f"briefloop run --workspace {ws} --recipe fast-rerun --skip-doctor"


def test_status_command_human_output_reports_fact_layer_import(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _mark_fact_layer_imported(ws)

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[status] fact_layer_import: valid" in out
    assert "source_run=mabw-20260614T000000Z-source" in out
    assert "satisfied=complete via import" in out


def test_status_command_human_output_reports_topology_satisfied_stage(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    paths = runtime_state_paths(ws)
    manifest = json.loads(paths["runtime_manifest"].read_text(encoding="utf-8"))
    run_id = manifest["run_id"]
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["current_stage"] = "claim-ledger"
    workflow["stage_statuses"] = {
        "scout": {"status": "complete"},
        "screener": {
            "status": "complete",
            "metadata": {"satisfied_by_topology": True},
        },
        "claim-ledger": {"status": "ready"},
    }
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["event_log"].write_text(
        "\n".join(
            json.dumps(event, sort_keys=True)
            for event in (
                _event("e0", "run_initialized", "2026-06-14T00:00:00Z", run_id=run_id),
                _completion("e1", "2026-06-14T00:01:00Z", "scout", run_id=run_id),
                _topology_satisfied(
                    "e2",
                    "2026-06-14T00:01:01Z",
                    "screener",
                    run_id=run_id,
                    trigger_stage="scout",
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert (
        "[status] topology: screener complete via scout "
        "(default; required=candidate_claims,screened_candidates)"
    ) in out

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    screener = next(
        stage
        for stage in payload["timing"]["stages"]
        if stage.get("stage_id") == "screener"
    )
    assert screener["status"] == "satisfied_by_topology"
    assert screener["completion_event_type"] == "stage_satisfied_by_topology"
    assert screener["topology"] == "default"
    assert screener["satisfied_by"] == "scout"
    assert screener["required_artifacts"] == ["candidate_claims", "screened_candidates"]


def test_status_command_reports_auditable_target_complete(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _write_auditable_target_complete_state(ws)

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    experiment = payload["experiment_080"]
    assert experiment["assessment_target"] == "auditable_brief"
    assert experiment["target_complete"] is True
    assert experiment["status"] == "complete"
    assert "experiments 080 register-run" in payload["suggested_next_command"]

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[status] experiment_080: case=solar_public_001 condition=memory assessment_target=auditable_brief" in out
    assert "[status] target_complete: auditable_brief" in out
    assert "do not finalize for this target" in out


def test_status_command_treats_final_abstract_advisory_warning_as_auditable_target_complete(
    tmp_path,
    capsys,
):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _write_auditable_target_complete_state(ws)
    gate_path = ws / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json"
    report = json.loads(gate_path.read_text(encoding="utf-8"))
    finding = {
        "finding_id": "QG_FINAL_ABSTRACT_QUALITY_001",
        "gate_id": "final_abstract_quality",
        "finding_type": "final_missing_limitation_section",
        "blocking_level": "warning",
        "blocking": False,
        "metadata": {"repair_boundary": "advisory_non_routable"},
    }
    report["status"] = "warning"
    report["findings"] = [finding]
    report["gate_results"].append(
        {
            "gate_id": "final_abstract_quality",
            "status": "warning",
            "blocking": False,
            "finding_ids": [finding["finding_id"]],
        }
    )
    gate_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    experiment = payload["experiment_080"]
    assert experiment["assessment_target"] == "auditable_brief"
    assert experiment["target_complete"] is True
    assert experiment["status"] == "complete"
    assert experiment["reasons"] == []


def test_status_command_rejects_unknown_final_abstract_warning_type_for_auditable_target(
    tmp_path,
    capsys,
):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _write_auditable_target_complete_state(ws)
    gate_path = ws / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json"
    report = json.loads(gate_path.read_text(encoding="utf-8"))
    finding = {
        "finding_id": "QG_FINAL_ABSTRACT_QUALITY_001",
        "gate_id": "final_abstract_quality",
        "finding_type": "future_non_advisory_rule",
        "blocking_level": "warning",
        "blocking": False,
        "metadata": {"repair_boundary": "advisory_non_routable"},
    }
    report["status"] = "warning"
    report["findings"] = [finding]
    report["gate_results"].append(
        {
            "gate_id": "final_abstract_quality",
            "status": "warning",
            "blocking": False,
            "finding_ids": [finding["finding_id"]],
        }
    )
    gate_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    experiment = payload["experiment_080"]
    assert experiment["assessment_target"] == "auditable_brief"
    assert experiment["target_complete"] is False
    assert experiment["status"] == "incomplete"
    assert "auditor quality gate report status is not pass" in experiment["reasons"]


def test_status_command_requires_auditable_downstream_stage_completion_events(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _write_auditable_target_complete_state(ws)
    paths = runtime_state_paths(ws)
    events = [
        json.loads(line)
        for line in paths["event_log"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    paths["event_log"].write_text(
        "".join(
            json.dumps(event, sort_keys=True) + "\n"
            for event in events
            if event.get("stage_id") != "analyst"
        ),
        encoding="utf-8",
    )

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    experiment = payload["experiment_080"]
    assert experiment["target_complete"] is False
    assert "analyst stage completion decision_recorded event is missing" in experiment["reasons"]
    assert "experiments 080 register-run" not in payload["suggested_next_command"]


def test_status_command_projects_recovery_without_replaying_run_integrity(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _write_auditable_target_complete_state(ws)
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "clean"
    run_id = str(workflow.get("run_id") or "run-test")
    with paths["event_log"].open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                _event(
                    "contam-1",
                    "run_integrity_contaminated",
                    "2026-06-14T00:05:00Z",
                    run_id=run_id,
                    stage_id="editor",
                    artifact_id="audited_brief",
                    reason="Synthetic sticky contamination.",
                    metadata={
                        "reason_code": "frozen_artifact_changed",
                        "message": "Synthetic sticky contamination.",
                        "stage_id": "editor",
                        "artifact_id": "audited_brief",
                    },
                ),
                sort_keys=True,
            )
            + "\n"
        )

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"]["run_integrity"]["status"] == "clean"
    assert payload["workflow"]["run_integrity"]["reference_eligible"] is True
    assert payload["recovery_state"]["status"] == "awaiting_recovery"
    assert payload["recovery_state"]["recovery_blocks_delivery"] is True
    assert "experiments 080 register-run" not in payload["suggested_next_command"]
    assert "workbuddy diagnose" in payload["suggested_next_command"]

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[status] run_integrity: clean reference_eligible=True" in out
    assert "[status] recovery: awaiting_recovery action=request_recovery_decision" in out


def test_status_command_keeps_legacy_repair_history_out_of_recovery_guidance(
    tmp_path,
    capsys,
):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _write_auditable_target_complete_state(ws)
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    run_id = str(workflow.get("run_id") or "run-test")
    with paths["event_log"].open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                _event(
                    "repair-1",
                    "repair_completed",
                    "2026-06-14T00:06:00Z",
                    run_id=run_id,
                    stage_id="editor",
                    reason="Editor repair completed.",
                    metadata={
                        "transaction_id": "repair-editor-1",
                        "allowed_artifacts": ["output/intermediate/audited_brief.md"],
                    },
                ),
                sort_keys=True,
            )
            + "\n"
        )

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    experiment = payload["experiment_080"]
    assert experiment["target_complete"] is False
    assert "audit binding relevant_repair_transaction_ids does not match event_log" in experiment["reasons"]
    assert payload["recovery_state"]["status"] == "not_applicable"
    assert "experiments 080 register-run" not in payload["suggested_next_command"]
    assert payload["suggested_next_command"] == f"briefloop status --workspace {ws} --json"
    assert "/mabw deliver" not in payload["suggested_next_command"]
    assert "/generate-brief" not in payload["suggested_next_command"]

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[status] target_complete: auditable_brief" not in out
    assert "[status] target_incomplete: auditable_brief" in out


def test_status_command_rejects_auditable_target_with_fake_auditor_transaction(
    tmp_path,
    capsys,
):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    _write_auditable_target_complete_state(ws)
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["stage_statuses"]["auditor"]["metadata"]["audit_binding"][
        "auditor_stage_transaction_id"
    ] = "fake-nonexistent-tx"
    paths["workflow_state"].write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    experiment = payload["experiment_080"]
    assert experiment["target_complete"] is False
    assert "audit binding auditor_stage_transaction_id does not match event_log" in experiment["reasons"]
    assert "experiments 080 register-run" not in payload["suggested_next_command"]

    rc = main(["status", "--workspace", str(ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[status] target_complete: auditable_brief" not in out
    assert "[status] target_incomplete: auditable_brief" in out


def test_status_command_reports_malformed_run_integrity_as_unknown(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(workspace=ws, runtime="claude", actor="cli")
    paths = runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["run_integrity"] = "bad"
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"]["run_integrity"]["status"] == "unknown"
    assert payload["workflow"]["run_integrity"]["reference_eligible"] is False
    assert payload["workflow"]["run_integrity"]["reasons"][0]["reason_code"] == "run_integrity_malformed"
    assert payload["timing"]["status"] == "unknown"
    assert payload["timing"]["run_integrity"]["reference_eligible"] is False
    assert "run_integrity_unknown" in payload["timing"]["warnings"]


def test_status_command_does_not_initialize_missing_runtime_state(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    paths = runtime_state_paths(ws)

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert payload["runtime"]["present"] is False
    assert payload["progress"]["status"] == "not_started"
    assert payload["progress"]["current_work"] == "create handoff"
    assert payload["progress"]["runtime_effect"] == "read_only"
    assert "runtime_manifest missing" in payload["stale_or_unknown"]
    for path in paths.values():
        assert not path.exists()


def test_status_timing_is_unknown_when_workflow_state_missing_even_with_event_log(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    paths = runtime_state_paths(ws)
    paths["event_log"].parent.mkdir(parents=True, exist_ok=True)
    paths["event_log"].write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema_version": "multi-agent-brief-event-log/v1",
                        "event_id": "e0",
                        "run_id": "run-test",
                        "created_at": "2026-06-14T00:00:00Z",
                        "event_type": "run_initialized",
                        "actor": "cli",
                        "stage_id": None,
                        "artifact_id": None,
                        "decision": None,
                        "reason": "",
                        "metadata": {},
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "schema_version": "multi-agent-brief-event-log/v1",
                        "event_id": "e1",
                        "run_id": "run-test",
                        "created_at": "2026-06-14T00:01:00Z",
                        "event_type": "decision_recorded",
                        "actor": "cli",
                        "stage_id": "doctor",
                        "artifact_id": None,
                        "decision": "continue",
                        "reason": "complete",
                        "metadata": {"transaction_id": "tx-e1"},
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"]["present"] is False
    assert payload["timing"]["status"] == "unknown"
    assert payload["timing"]["run_integrity"]["status"] == "unknown"
    assert payload["timing"]["run_integrity"]["reference_eligible"] is False
    assert "run_integrity_unknown" in payload["timing"]["warnings"]


def test_status_command_reports_corrupt_event_log_without_writing(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    event_log.parent.mkdir(parents=True)
    event_log.write_text("{bad json}\n", encoding="utf-8")
    before = event_log.read_bytes()
    before_mtime = event_log.stat().st_mtime_ns

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["events"]["corrupt_count"] == 1
    assert payload["progress"]["status"] == "needs_operator_action"
    assert payload["progress"]["current_work"] == "check run record"
    assert payload["timing"]["status"] == "invalid_event_log"
    assert "event_log contains unreadable records" in payload["stale_or_unknown"]
    assert event_log.read_bytes() == before
    assert event_log.stat().st_mtime_ns == before_mtime


def test_status_command_reports_invalid_utf8_event_log_without_writing(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    event_log.parent.mkdir(parents=True)
    event_log.write_bytes(b"\xff\xfe\x00")
    before = event_log.read_bytes()
    before_mtime = event_log.stat().st_mtime_ns

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["events"]["corrupt_count"] == 1
    assert payload["progress"]["status"] == "needs_operator_action"
    assert payload["progress"]["current_work"] == "check run record"
    assert payload["timing"]["status"] == "invalid_event_log"
    assert "event_log contains unreadable records" in payload["stale_or_unknown"]
    assert event_log.read_bytes() == before
    assert event_log.stat().st_mtime_ns == before_mtime


def test_status_command_reports_malformed_quality_gate_as_unknown(tmp_path, capsys):
    ws = _minimal_workspace(tmp_path / "ws")
    quality_gate = ws / "output" / "intermediate" / "quality_gate_report.json"
    quality_gate.parent.mkdir(parents=True)
    quality_gate.write_text(
        json.dumps(
            {
                "metadata": "bad",
                "findings": [],
                "status": "pass",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    before = quality_gate.read_bytes()

    rc = main(["status", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quality_gate"]["present"] is True
    assert payload["quality_gate"]["status"] == "unknown"
    assert payload["quality_gate"]["raw_status"] == "pass"
    assert payload["quality_gate"]["schema_warnings"] == ["metadata is not an object"]
    assert "quality_gate_report schema warning: metadata is not an object" in payload["stale_or_unknown"]
    assert quality_gate.read_bytes() == before
