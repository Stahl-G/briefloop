from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    check_runtime_state,
    initialize_runtime_state,
)
from multi_agent_brief.product.guidance_manifestation import (
    GUIDANCE_MANIFESTATION_BOUNDARY,
    GUIDANCE_MANIFESTATION_REPORT_SCHEMA_VERSION,
    GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS,
    GUIDANCE_MANIFESTATION_RUNTIME_EFFECT,
    project_workspace_guidance_manifestation,
    validate_guidance_manifestation_projection_payload,
    validate_guidance_manifestation_report_payload,
)
from multi_agent_brief.product.quality_panel import build_quality_panel, validate_quality_panel_payload
from multi_agent_brief.status import build_workspace_status, format_workspace_status
from tests.helpers import write_minimal_workspace_under


def _workspace(base_path: Path) -> Path:
    """Legacy module fixture without claiming a public CLI path.

    The retired public `state init` operator CLI is replaced by the direct
    runtime-state seam; the status/quality-panel fold-in and artifact-registry
    invariants below remain live product behavior, preserved unchanged.
    """

    workspace = write_minimal_workspace_under(
        base_path,
        project_name="Guidance Manifestation Test",
        user_text="# Guidance manifestation test\n",
    )
    initialize_runtime_state(runtime="operator", workspace=workspace)
    return workspace


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }


def _write_manifest_improvement(ws: Path, entry_ids: list[str]) -> str:
    manifest_path = ws / "output" / "intermediate" / "runtime_manifest.json"
    manifest = _json(manifest_path)
    manifest["improvement"] = {
        "snapshot_path": "output/intermediate/improvement_memory_snapshot.md",
        "snapshot_sha256": "0" * 64,
        "materialized_entry_ids": list(entry_ids),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(manifest["run_id"])


def _report(run_id: str, *, status: str = "not_observable") -> dict:
    return {
        "schema_version": GUIDANCE_MANIFESTATION_REPORT_SCHEMA_VERSION,
        "workspace": ".",
        "run_id": run_id,
        "generated_at": "2026-07-01T00:00:00+00:00",
        "read_only": True,
        "runtime_effect": GUIDANCE_MANIFESTATION_RUNTIME_EFFECT,
        "boundary": GUIDANCE_MANIFESTATION_BOUNDARY,
        "assessment_method": "human_review",
        "entries": [
            {
                "entry_id": "AG-0001",
                "status": status,
                "assessment_source": "human",
                "notes": "The approved guidance was not observable in the reviewed artifacts.",
                "artifact_refs": [
                    {
                        "path": "output/intermediate/audited_brief.md",
                        "label": "review target",
                    }
                ],
            }
        ],
        "non_goals": sorted(GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS),
    }


def _write_report(ws: Path, payload: dict) -> None:
    (ws / "output" / "intermediate" / "guidance_manifestation_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_guidance_manifestation_direct_import_has_no_runtime_state_cycle() -> None:
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from multi_agent_brief.product.guidance_manifestation import "
                "project_workspace_guidance_manifestation; "
                "print(project_workspace_guidance_manifestation)"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "project_workspace_guidance_manifestation" in result.stdout


def test_state_check_cli_is_retired_with_typed_rejection(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    run_id = _write_manifest_improvement(ws, ["AG-0001"])
    _write_report(ws, _report(run_id))
    files_before = _workspace_file_bytes(ws)

    # retired public `state check` operator CLI; typed rejection
    # with zero writes replaces the pre-CX artifact-registry refresh.
    assert main(["state", "check", "--workspace", str(ws)]) == 1
    assert capsys.readouterr().out == "legacy_workspace_unsupported\n"
    assert _workspace_file_bytes(ws) == files_before


def test_guidance_manifestation_missing_runtime_manifest_is_not_available(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text("project:\n  name: No runtime state\n", encoding="utf-8")

    projection = project_workspace_guidance_manifestation(ws)
    status = build_workspace_status(ws)
    panel = build_quality_panel(ws)

    assert validate_guidance_manifestation_projection_payload(projection) is None
    assert projection["status"] == "not_available"
    assert projection["reason"] == "runtime_manifest_missing"
    assert projection["summary_counts"]["materialized_entry_count"] == 0
    assert status["guidance_manifestation"]["status"] == "not_available"
    assert status["guidance_manifestation"]["reason"] == "runtime_manifest_missing"
    assert panel["guidance_manifestation"]["status"] == "not_available"
    assert panel["guidance_manifestation"]["reason"] == "runtime_manifest_missing"


def test_guidance_manifestation_unreadable_runtime_manifest_is_not_available(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    manifest_path = ws / "output" / "intermediate" / "runtime_manifest.json"
    manifest_path.write_bytes(b"\xff\xfe\x00bad-runtime-manifest")

    projection = project_workspace_guidance_manifestation(ws)
    status = build_workspace_status(ws)
    panel = build_quality_panel(ws)

    assert validate_guidance_manifestation_projection_payload(projection) is None
    assert projection["status"] == "not_available"
    assert projection["reason"] == "runtime_manifest_unreadable"
    assert status["guidance_manifestation"]["status"] == "not_available"
    assert status["guidance_manifestation"]["reason"] == "runtime_manifest_unreadable"
    assert status["guidance_manifestation"]["summary_counts"]["materialized_entry_count"] == 0
    assert panel["guidance_manifestation"]["status"] == "not_available"
    assert panel["guidance_manifestation"]["reason"] == "runtime_manifest_unreadable"


def test_guidance_manifestation_missing_report_is_explicit_and_read_only(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_manifest_improvement(ws, ["AG-0001"])
    workflow_before = _json(ws / "output" / "intermediate" / "workflow_state.json")

    projection = project_workspace_guidance_manifestation(ws)
    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)
    workflow_after = _json(ws / "output" / "intermediate" / "workflow_state.json")

    assert validate_guidance_manifestation_projection_payload(projection) is None
    assert projection["status"] == "missing_report"
    assert projection["summary_counts"]["unassessed_entry_count"] == 1
    assert status["guidance_manifestation"]["status"] == "missing_report"
    assert "[status] guidance_manifestation: missing_report" in formatted
    assert workflow_after == workflow_before


def test_guidance_manifestation_report_projects_not_observable(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    run_id = _write_manifest_improvement(ws, ["AG-0001"])
    report = _report(run_id)
    _write_report(ws, report)

    projection = build_workspace_status(ws)["guidance_manifestation"]
    panel = build_quality_panel(ws)

    assert validate_guidance_manifestation_report_payload(report, current_run_id=run_id) is None
    assert validate_guidance_manifestation_projection_payload(projection) is None
    assert projection["status"] == "present"
    assert projection["python_judged_manifestation"] is False
    assert projection["summary_counts"]["not_observable_count"] == 1
    assert projection["summary_counts"]["materialized_entry_count"] == 1
    assert projection["non_goals"] == sorted(GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS)
    assert validate_quality_panel_payload(panel) is None
    assert panel["guidance_manifestation"]["summary_counts"]["not_observable_count"] == 1


def test_guidance_manifestation_rejects_non_materialized_report_entries(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    run_id = _write_manifest_improvement(ws, ["AG-0001"])
    report = _report(run_id)
    report["entries"].append(
        {
            "entry_id": "AG-9999",
            "status": "explicitly_reflected",
            "assessment_source": "human",
            "notes": "This entry was not materialized into the current run.",
            "artifact_refs": [],
        }
    )
    _write_report(ws, report)

    reason = validate_guidance_manifestation_report_payload(
        report,
        current_run_id=run_id,
        materialized_entry_ids=["AG-0001"],
    )
    projection = build_workspace_status(ws)["guidance_manifestation"]

    assert reason == "guidance_manifestation_report_schema_error:entries[1].entry_id_not_materialized"
    assert projection["status"] == "invalid_report"
    assert projection["reason"] == reason
    assert projection["summary_counts"]["explicitly_reflected_count"] == 0
    assert projection["summary_counts"]["not_observable_count"] == 0


def test_guidance_manifestation_report_rejects_authority_shapes(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    run_id = _write_manifest_improvement(ws, ["AG-0001"])
    payload = _report(run_id)
    payload["entries"][0]["quality_score"] = 100

    assert validate_guidance_manifestation_report_payload(payload, current_run_id=run_id) == (
        "guidance_manifestation_report_schema_error:authority_field"
    )

    forged_method = _report(run_id)
    forged_method["assessment_method"] = "python_auto_manifestation_judge"
    assert validate_guidance_manifestation_report_payload(
        forged_method,
        current_run_id=run_id,
        materialized_entry_ids=["AG-0001"],
    ) == "guidance_manifestation_report_schema_error:assessment_method"

    forged_projection = {
        "schema_version": "briefloop.guidance_manifestation_projection.v1",
        "status": "present",
        "read_only": True,
        "runtime_effect": "state_transition",
        "boundary": GUIDANCE_MANIFESTATION_BOUNDARY,
        "run_id": run_id,
        "report_present": True,
        "report_path": "output/intermediate/guidance_manifestation_report.json",
        "python_judged_manifestation": False,
        "assessment_method": "human_review",
        "generated_at": "2026-07-01T00:00:00+00:00",
        "materialized_entry_ids": ["AG-0001"],
        "entries": [],
        "missing_entry_ids": [],
        "extra_entry_ids": [],
        "summary_counts": {},
        "snapshot": {},
        "non_goals": sorted(GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS),
    }
    panel = build_quality_panel(ws)
    panel["guidance_manifestation"] = forged_projection
    assert validate_quality_panel_payload(panel) == (
        "quality_panel_schema_error:guidance_manifestation:"
        "guidance_manifestation_projection_schema_error:runtime_effect"
    )


def test_guidance_manifestation_artifact_registry_validates_current_run(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    run_id = _write_manifest_improvement(ws, ["AG-0001"])
    _write_report(ws, _report(run_id))

    # retired public `state check` CLI; the artifact-registry
    # refresh is driven through the direct runtime-state seam.
    check_runtime_state(workspace=ws)
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    artifact = registry["artifacts"]["guidance_manifestation_report"]
    assert artifact["status"] == "valid"
    assert artifact["validation_result"] == "experimental_guidance_manifestation_report"

    stale = _report("mabw-20260101T000000Z-stale")
    _write_report(ws, stale)
    # retired public `state check` CLI; the artifact-registry
    # refresh is driven through the direct runtime-state seam.
    check_runtime_state(workspace=ws)
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    artifact = registry["artifacts"]["guidance_manifestation_report"]
    assert artifact["status"] == "invalid"
    assert "run_id_mismatch" in artifact["validation_result"]

    extra = _report(run_id)
    extra["entries"].append(
        {
            "entry_id": "AG-9999",
            "status": "explicitly_reflected",
            "assessment_source": "human",
            "notes": "This entry was not materialized into the current run.",
            "artifact_refs": [],
        }
    )
    _write_report(ws, extra)
    # retired public `state check` CLI; the artifact-registry
    # refresh is driven through the direct runtime-state seam.
    check_runtime_state(workspace=ws)
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    artifact = registry["artifacts"]["guidance_manifestation_report"]
    assert artifact["status"] == "invalid"
    assert "entry_id_not_materialized" in artifact["validation_result"]

    forged_method = _report(run_id)
    forged_method["assessment_method"] = "python_auto_manifestation_judge"
    _write_report(ws, forged_method)
    # retired public `state check` CLI; the artifact-registry
    # refresh is driven through the direct runtime-state seam.
    check_runtime_state(workspace=ws)
    registry = _json(ws / "output" / "intermediate" / "artifact_registry.json")
    artifact = registry["artifacts"]["guidance_manifestation_report"]
    assert artifact["status"] == "invalid"
    assert "assessment_method" in artifact["validation_result"]
