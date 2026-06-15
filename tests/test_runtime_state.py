"""Tests for v0.6.1 Orchestrator runtime state."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import multi_agent_brief.orchestrator.runtime_state as runtime_state
import multi_agent_brief.orchestrator.runtime_state.event_log as runtime_event_log
from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file
from multi_agent_brief.orchestrator.runtime_state import (
    RUNTIME_STATE_FILES,
    RuntimeStateError,
    check_runtime_state,
    complete_finalize_transaction,
    complete_stage_transaction,
    initialize_runtime_state,
    import_fact_layer_transaction,
    record_decision,
    show_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import _allowed_decisions_for_stage
from multi_agent_brief.orchestrator.run_archive import archive_finalized_run
from multi_agent_brief.outputs.finalize import finalize_reader_outputs


ROOT = Path(__file__).resolve().parent.parent


def _write_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    (ws / "user.md").write_text("# User\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    return ws


def _state_file(ws: Path, key: str) -> Path:
    return ws / RUNTIME_STATE_FILES[key]


def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json_artifact(ws: Path, name: str, payload: str = "[]\n") -> None:
    (_intermediate(ws) / name).write_text(payload, encoding="utf-8")


def _valid_claim_ledger_payload(
    claim_id: str = "CL-001",
    statement: str = "ExampleCo opened a demo facility.",
    metadata: dict[str, object] | None = None,
) -> str:
    return json.dumps(
        [
            {
                "claim_id": claim_id,
                "statement": statement,
                "source_id": "SRC-001",
                "evidence_text": "Example evidence.",
                "metadata": metadata or {},
            }
        ]
    ) + "\n"


def _valid_audit_report_payload() -> str:
    return json.dumps(
        {
            "audit_status": "pass",
            "audit_score": 100,
            "passed": True,
            "recommendation": "approve",
            "findings": [],
        }
    ) + "\n"


def _event_records(ws: Path) -> list[dict]:
    path = _state_file(ws, "event_log")
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _fail_appending_event_type(monkeypatch: pytest.MonkeyPatch, event_type: str) -> None:
    original = runtime_event_log._append_jsonl

    def flaky_append(path: Path, payload: dict) -> None:
        if payload.get("event_type") == event_type:
            raise RuntimeStateError("forced event append failure")
        original(path, payload)

    monkeypatch.setattr(runtime_event_log, "_append_jsonl", flaky_append)


def _write_quality_gate_report(
    ws: Path,
    *,
    status: str = "pass",
    blocking: bool = False,
    stage_id: str = "auditor",
    legacy_only: bool = False,
) -> None:
    gate_artifact_id = "finalize_quality_gate_report" if stage_id == "finalize" else "auditor_quality_gate_report"
    brief_ref = "output/brief.md" if stage_id == "finalize" else "output/intermediate/audited_brief.md"
    findings = []
    if blocking:
        findings.append({
            "finding_id": "QG_TARGET_RELEVANCE_001",
            "finding_type": "target_relevance_failed",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "stage_id": stage_id,
            "gate_stage_id": stage_id,
            "artifact_id": gate_artifact_id,
            "gate_artifact_id": gate_artifact_id,
            "repair_stage_id": stage_id,
            "repair_artifact_id": "audited_brief",
            "repair_owner": "orchestrator",
            "message": "Synthetic blocking finding.",
            "metadata": {},
        })
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "created_at": "2026-06-11T00:00:00+00:00",
        "updated_at": "2026-06-11T00:00:00+00:00",
        "workspace": ".",
        "report_date": "2026-06-11",
        "policy_pack": "default",
        "status": status,
        "gate_results": [
            {
                "gate_id": "freshness",
                "status": "pass",
                "blocking": False,
                "finding_ids": [],
            },
            {
                "gate_id": "material_fact",
                "status": "pass",
                "blocking": False,
                "finding_ids": [],
            },
            {
                "gate_id": "target_relevance",
                "status": "fail" if blocking else status,
                "blocking": blocking,
                "finding_ids": [item["finding_id"] for item in findings],
            },
        ],
        "findings": findings,
        "metadata": {
            "brief": brief_ref,
            "ledger": "output/intermediate/claim_ledger.json",
            "stage_id": stage_id,
            "gate_stage_id": stage_id,
            "gate_artifact_id": gate_artifact_id,
        },
    }
    payload_text = json.dumps(payload)
    if not legacy_only:
        report_path = (
            _intermediate(ws)
            / "gates"
            / ("finalize_quality_gate_report.json" if stage_id == "finalize" else "auditor_quality_gate_report.json")
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload_text, encoding="utf-8")
    (_intermediate(ws) / "quality_gate_report.json").write_text(payload_text, encoding="utf-8")


def _write_finalize_report(
    ws: Path,
    *,
    status: str = "pass",
    reader_clean_status: str = "pass",
) -> None:
    output = ws / "output"
    brief_text = "# Reader Brief\n\nClean reader text.\n"
    (output / "brief.md").write_text(brief_text, encoding="utf-8")
    delivery_dir = output / "delivery"
    delivery_dir.mkdir(parents=True, exist_ok=True)
    delivery_brief = delivery_dir / "brief.md"
    delivery_brief.write_text(brief_text, encoding="utf-8")
    (_intermediate(ws) / "finalize_report.json").write_text(
        json.dumps({
            "status": status,
            "audited_brief": str(_intermediate(ws) / "audited_brief.md"),
            "reader_brief": str(output / "brief.md"),
            "named_reader_brief": "",
            "reader_docx": "",
            "named_reader_docx": "",
            "source_appendix": "",
            "delivery_markdown": str(delivery_brief),
            "delivery_docx": "",
            "delivery_artifacts": [str(delivery_brief)],
            "delivery_artifact_sha256": {
                str(delivery_brief): _sha256_file(delivery_brief),
            },
            "audit_binding": {
                "status": "pass",
                "claim_ledger_sha256": _sha256_file(
                    _intermediate(ws) / "claim_ledger.json"
                ),
                "audited_brief_sha256": _sha256_file(
                    _intermediate(ws) / "audited_brief.md"
                ),
                "audit_report_sha256": _sha256_file(
                    _intermediate(ws) / "audit_report.json"
                ),
                "ledger_claim_count": 1,
                "audited_brief_cited_claim_count": 0,
                "findings": [],
                "warnings": [],
            },
            "reader_clean": {
                "status": reader_clean_status,
                "src_marker_count": 0,
                "bare_claim_id_count": 0,
                "source_id_count": 0,
                "process_wording_count": 0,
                "blank_citation_row_count": 0,
                "local_path_count": 0,
                "debug_residue_count": 0,
                "sample_findings": [],
            },
        }),
        encoding="utf-8",
    )


def _write_fact_layer_inputs(ws: Path) -> None:
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source-001.md").write_text(
        "# Source 001\n\nExample evidence for the archived fact layer.\n",
        encoding="utf-8",
    )
    (source_dir / "README.md").write_text(
        "This README is not source evidence.\n",
        encoding="utf-8",
    )
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "input_classification.json").write_text(
        json.dumps({
            "evidence": [{"path": str(source_dir / "source-001.md"), "name": "source-001.md"}],
            "feedback": [],
            "instruction": [],
            "context": [],
            "skipped": [],
        }),
        encoding="utf-8",
    )


def _set_current_stage(ws: Path, stage_id: str) -> None:
    stages = runtime_state.load_stage_specs(ROOT)
    stage_ids = [str(stage.get("stage_id") or "") for stage in stages if stage.get("stage_id")]
    assert stage_id in stage_ids
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    now = runtime_state.utc_now()
    statuses = {}
    for item in stage_ids:
        if stage_ids.index(item) < stage_ids.index(stage_id):
            statuses[item] = {"status": "complete", "reason": f"{item} fixture complete", "updated_at": now}
        elif item == stage_id:
            statuses[item] = {"status": "ready", "reason": "", "updated_at": now}
        else:
            statuses[item] = {"status": "pending", "reason": "", "updated_at": now}
    workflow["updated_at"] = now
    workflow["current_stage"] = stage_id
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow["stage_statuses"] = statuses
    workflow["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, stage_id)
    _state_file(ws, "workflow_state").write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _advance_to_finalize(ws: Path) -> None:
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(ws, "finalize")


def _advance_to_auditor(ws: Path) -> None:
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(ws, "auditor")


def _complete_finalized_workspace(ws: Path) -> dict:
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    return complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )


def _complete_finalized_workspace_with_claim_metadata(ws: Path, metadata: dict[str, object]) -> dict:
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    (_intermediate(ws) / "claim_ledger.json").write_text(
        _valid_claim_ledger_payload(metadata=metadata),
        encoding="utf-8",
    )
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    return complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )


def test_state_init_creates_runtime_control_files_without_old_run_manifest(tmp_path):
    ws = _write_workspace(tmp_path)

    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["ok"] is True
    assert _state_file(ws, "runtime_manifest").exists()
    assert _state_file(ws, "workflow_state").exists()
    assert _state_file(ws, "event_log").exists()
    assert not (ws / "output" / "intermediate" / "run_manifest.json").exists()

    manifest = json.loads(_state_file(ws, "runtime_manifest").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "multi-agent-brief-runtime-manifest/v1"
    assert manifest["workspace"] == "."
    assert manifest["runtime_state_files"] == RUNTIME_STATE_FILES
    assert manifest["stage_order"][0] == "doctor"
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"] == {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }


def test_state_check_fresh_workspace_is_not_globally_blocked(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow = state["workflow_state"]
    registry = state["artifact_registry"]["artifacts"]

    assert workflow["blocked"] is False
    assert workflow["current_stage"] == "doctor"
    assert workflow["stage_statuses"]["doctor"]["status"] == "ready"
    assert workflow["stage_statuses"]["claim-ledger"]["status"] == "pending"
    assert registry["claim_ledger"]["status"] == "expected"
    assert registry["audited_brief"]["status"] == "expected"
    assert registry["reader_brief"]["status"] == "expected"
    assert registry["auditor_quality_gate_report"]["status"] == "expected"
    assert registry["finalize_quality_gate_report"]["status"] == "expected"
    assert registry["auditor_quality_gate_report"]["validation_result"] == "not_checked"
    assert registry["finalize_quality_gate_report"]["validation_result"] == "not_checked"


def test_state_check_rejects_malformed_run_integrity_without_rewrite(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow_path = _state_file(ws, "workflow_state")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_integrity"] = "bad"
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = workflow_path.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert workflow_path.read_bytes() == before


def test_state_show_rejects_invalid_run_integrity_status_without_rewrite(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow_path = _state_file(ws, "workflow_state")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_integrity"] = {
        "status": "unknown",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = workflow_path.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        show_runtime_state(workspace=ws)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert workflow_path.read_bytes() == before


def test_stage_complete_rejects_invalid_run_integrity_status_without_rewrite(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow_path = _state_file(ws, "workflow_state")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_integrity"] = {
        "status": "unknown",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = workflow_path.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor complete",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert workflow_path.read_bytes() == before


def test_state_check_strict_fresh_workspace_returns_zero(tmp_path):
    ws = _write_workspace(tmp_path)

    rc = main([
        "state",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])

    assert rc == 0


def test_state_decide_continue_requires_completion_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)

    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    with pytest.raises(RuntimeStateError) as excinfo:
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            decision="continue",
            reason="auditor complete",
        )
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    assert excinfo.value.error_code == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert excinfo.value.details["required_command"] == "stage-complete"
    assert after == before


def test_state_decide_finalize_requires_completion_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)

    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    with pytest.raises(RuntimeStateError) as excinfo:
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="finalize",
            decision="finalize",
            reason="finalize complete",
        )
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    assert excinfo.value.error_code == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert excinfo.value.details["required_command"] == "finalize-complete"
    assert after == before


def test_invalid_optional_expected_artifact_rejects_continue(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")
    (ws / "source_candidates.yaml").write_text(": [", encoding="utf-8")

    with pytest.raises(RuntimeStateError, match="Optional expected artifact 'source_candidates'"):
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )


def test_source_discovery_runtime_tool_without_sources_or_candidates_rejects_complete(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "runtime_tool web search is enabled" in str(excinfo.value)


def test_source_discovery_runtime_tool_allows_local_source(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "input" / "source.md").write_text("source text\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "    - web_search\n"
        "manual:\n"
        "  sources:\n"
        "    - name: Local Source\n"
        "      path: input/source.md\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="source-discovery",
        reason="source discovery complete",
    )

    assert state["workflow_state"]["current_stage"] == "input-governance"


def test_source_discovery_configure_later_with_plan_only_rejects_complete(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: configure_later\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "schema_version: mabw.source_candidates.v1\n"
        "artifact_type: source_plan_only\n"
        "evidence_status: not_evidence\n"
        "recommended_sources:\n"
        "  - name: Example Source\n"
        "    url: https://example.com/source\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "source plan, not evidence" in str(excinfo.value)
    assert "configure_later" in str(excinfo.value)


def test_source_discovery_configure_later_allows_real_input_source(tmp_path):
    ws = _write_workspace(tmp_path)
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "example.md").write_text(
        "Title: Example Source\nURL: https://example.com/source\nRetrieved: 2026-06-13\nExcerpt: Example evidence.\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: configure_later\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="source-discovery",
        reason="source discovery complete",
    )

    assert state["workflow_state"]["current_stage"] == "input-governance"


def test_source_discovery_runtime_tool_rejects_source_candidates_plan_only(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "recommended_sources:\n"
        "  - name: Example Source\n"
        "    url: https://example.com/source\n"
        "    category: industry_media\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "source_candidates.yaml is a source plan, not evidence" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_input_readme_only(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "input" / "README.md").write_text("Put source files here.\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "no evidence source is available" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_input_sources_readme_only(tmp_path):
    ws = _write_workspace(tmp_path)
    sources_dir = ws / "input" / "sources"
    sources_dir.mkdir(parents=True)
    (sources_dir / "README.md").write_text("Put source files here.\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "no evidence source is available" in str(excinfo.value)


def test_source_discovery_runtime_tool_allows_input_sources_file(tmp_path):
    ws = _write_workspace(tmp_path)
    sources_dir = ws / "input" / "sources"
    sources_dir.mkdir(parents=True)
    (sources_dir / "example.md").write_text(
        "Title: Example Source\nURL: https://example.com/source\nRetrieved: 2026-06-13\nExcerpt: Example evidence.\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="source-discovery",
        reason="source discovery complete",
    )

    assert state["workflow_state"]["current_stage"] == "input-governance"


def test_source_discovery_source_plan_only_allows_real_source_file(tmp_path):
    ws = _write_workspace(tmp_path)
    sources_dir = ws / "input" / "sources"
    sources_dir.mkdir(parents=True)
    (sources_dir / "example.md").write_text(
        "Title: Example Source\nURL: https://example.com/source\nRetrieved: 2026-06-13\nExcerpt: Example evidence.\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "schema_version: mabw.source_candidates.v1\n"
        "artifact_type: source_plan_only\n"
        "evidence_status: not_evidence\n"
        "recommended_sources:\n"
        "  - name: Example Source\n"
        "    url: https://example.com/source\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="source-discovery",
        reason="source discovery complete",
    )

    assert state["workflow_state"]["current_stage"] == "input-governance"


def test_source_discovery_runtime_tool_rejects_context_only_input(tmp_path):
    ws = _write_workspace(tmp_path)
    context_dir = ws / "input" / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "style.md").write_text("Use concise prose.\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "    - web_search\n"
        "manual:\n"
        "  sources:\n"
        "    - name: Context Only\n"
        "      path: input/context/style.md\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "no evidence source" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_zero_observation_fixture(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "metadata:\n"
        "  runtime_search_observation:\n"
        "    message: Did 0 searches\n"
        "recommended_sources: []\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "zero searches" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_positive_observation_without_durable_source(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "metadata:\n"
        "  runtime_search_observations:\n"
        "    - query: useful query\n"
        "      result_count: 2\n"
        "recommended_sources: []\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "no evidence source is available" in str(excinfo.value)
    assert "input/sources/" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_candidates_urls_even_with_positive_observation(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "metadata:\n"
        "  runtime_search_observations:\n"
        "    - query: empty query\n"
        "      result_count: 0\n"
        "    - query: useful query\n"
        "      result_count: 2\n"
        "recommended_sources:\n"
        "  - name: Example Source\n"
        "    url: https://example.com/source\n"
        "    category: industry_media\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "source_candidates.yaml is a source plan, not evidence" in str(excinfo.value)


def test_optional_feedback_artifacts_do_not_become_missing_after_auditor_complete(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    registry = state["artifact_registry"]["artifacts"]

    assert registry["feedback_issues"]["status"] == "expected"
    assert registry["repair_plan"]["status"] == "expected"
    assert registry["delta_audit_report"]["status"] == "expected"
    assert registry["auditor_quality_gate_report"]["status"] == "expected"
    assert registry["finalize_quality_gate_report"]["status"] == "expected"
    assert registry["feedback_issues"]["validation_result"] == "not_checked"
    assert registry["repair_plan"]["validation_result"] == "not_checked"
    assert registry["delta_audit_report"]["validation_result"] == "not_checked"
    assert registry["auditor_quality_gate_report"]["validation_result"] == "not_checked"
    assert registry["finalize_quality_gate_report"]["validation_result"] == "not_checked"


def test_delta_audit_report_missing_only_when_repair_active(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    out = _intermediate(ws)
    (out / "feedback_issues.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-feedback-issues/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "issues": [
                {
                    "issue_id": "fb_active",
                    "source": "human",
                    "severity": "blocking",
                    "stage_id": "auditor",
                    "artifact_id": "audit_report",
                    "category": "unsupported_claim",
                    "summary": "Repair requires delta audit.",
                    "feedback_excerpt": "Repair requires delta audit.",
                    "raw_feedback_ref": "feedback.txt",
                    "source_artifact": "feedback.txt",
                    "supporting_context": [],
                    "metadata": {},
                    "status": "in_progress",
                    "created_at": "2026-06-08T00:00:00+00:00",
                    "updated_at": "2026-06-08T00:00:00+00:00",
                    "fingerprint": "active",
                }
            ],
        }),
        encoding="utf-8",
    )
    (out / "repair_plan.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-repair-plan/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "repair_plans": [
                {
                    "repair_plan_id": "rp_active",
                    "created_at": "2026-06-08T00:00:00+00:00",
                    "updated_at": "2026-06-08T00:00:00+00:00",
                    "target_stage": "auditor",
                    "target_artifacts": ["audit_report"],
                    "issue_ids": ["fb_active"],
                    "allowed_decision": "delegate_repair",
                    "repair_scope": "minimal",
                    "instructions": ["Run delta audit."],
                    "requires_human_review": False,
                    "status": "in_progress",
                    "fingerprint": "active",
                }
            ],
        }),
        encoding="utf-8",
    )
    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["artifact_registry"]["artifacts"]["delta_audit_report"]["status"] == "missing"


def test_invalid_current_stage_output_blocks_only_that_stage(tmp_path):
    ws = _write_workspace(tmp_path)
    output = ws / "output" / "intermediate"
    output.mkdir(parents=True)
    (output / "candidate_claims.json").write_text("{broken", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow = state["workflow_state"]
    registry = state["artifact_registry"]["artifacts"]

    assert registry["candidate_claims"]["status"] == "invalid"
    assert workflow["current_stage"] == "scout"
    assert workflow["stage_statuses"]["scout"]["status"] == "blocked"
    assert workflow["stage_statuses"]["claim-ledger"]["status"] == "pending"


def test_state_decide_validates_decision_vocabulary(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    rc = main([
        "state",
        "decide",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "doctor",
        "--decision",
        "invent_decision",
        "--reason",
        "bad",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Unknown Orchestrator decision" in payload["error"]


def test_state_decide_records_event_and_last_decision(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = record_decision(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        decision="block_run",
        reason="doctor failed",
    )

    workflow = state["workflow_state"]
    assert workflow["last_decision"]["decision"] == "block_run"
    assert workflow["stage_statuses"]["doctor"]["status"] == "blocked"
    assert workflow["current_stage"] == "doctor"
    events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line)["event_type"] == "decision_recorded" for line in events)


def test_stage_complete_records_transaction_event_and_advances(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )

    workflow = state["workflow_state"]
    transaction_id = workflow["last_completion_transaction"]["transaction_id"]
    assert workflow["current_stage"] == "source-discovery"
    assert workflow["stage_statuses"]["doctor"]["status"] == "complete"
    assert state["transaction"]["transaction_id"] == transaction_id
    decision_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "decision_recorded"
        and (event.get("metadata") or {}).get("transaction_id") == transaction_id
    ]
    assert len(decision_events) == 1
    assert decision_events[0]["decision"] == "continue"


def test_stage_complete_duplicate_rejects_without_duplicate_event(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor passed again",
        )

    assert excinfo.value.error_code == "E_STAGE_ALREADY_COMPLETED"
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "contaminated"
    assert workflow["run_integrity"]["reference_eligible"] is False
    assert workflow["run_integrity"]["reasons"][0]["reason_code"] == "older_stage_replay"
    contamination_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ]
    assert len(contamination_events) == 1
    assert contamination_events[0]["metadata"]["reason_code"] == "older_stage_replay"
    assert contamination_events[0]["metadata"]["reference_eligible"] is False
    assert contamination_events[0]["metadata"]["clean_single_shot"] is False
    workflow_bytes_after_first_contamination = _state_file(ws, "workflow_state").read_bytes()
    event_bytes_after_first_contamination = _state_file(ws, "event_log").read_bytes()

    with pytest.raises(RuntimeStateError):
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor passed yet again",
        )

    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    contamination_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ]
    assert len(workflow["run_integrity"]["reasons"]) == 1
    assert len(contamination_events) == 1
    assert len(_event_records(ws)) == len(before_events) + 1
    assert _state_file(ws, "workflow_state").read_bytes() == workflow_bytes_after_first_contamination
    assert _state_file(ws, "event_log").read_bytes() == event_bytes_after_first_contamination


def test_stage_complete_contamination_event_failure_rolls_back_workflow(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()
    _fail_appending_event_type(monkeypatch, "run_integrity_contaminated")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor replay should fail atomically",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_PARTIAL_WRITE
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_stage_complete_missing_required_output_writes_nothing(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="scout",
            reason="scout complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8")) == before_workflow
    assert _event_records(ws) == before_events


def test_stage_complete_cli_json_error_includes_error_code(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    rc = main([
        "state",
        "stage-complete",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "auditor",
        "--reason",
        "out of order",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_STAGE_MISMATCH"


def test_stage_complete_event_append_failure_is_detectable_partial_write(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(runtime_event_log, "_append_jsonl", fail_append)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor passed",
        )

    assert excinfo.value.error_code == "E_TRANSACTION_PARTIAL_WRITE"
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["current_stage"] == "source-discovery"
    assert workflow["last_completion_transaction"]["transaction_id"]

    monkeypatch.undo()
    checked = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert checked["workflow_state"]["blocked"] is True
    assert checked["transaction_integrity_warning"]["error_code"] == "E_TRANSACTION_INTEGRITY"


def test_stage_complete_stale_gate_report_does_not_block_early_stage(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")
    _write_json_artifact(ws, "candidate_claims.json")
    _write_quality_gate_report(ws, blocking=True, stage_id="auditor")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="scout",
        reason="scout complete",
    )

    assert state["workflow_state"]["current_stage"] == "screener"


def test_claim_ledger_stage_complete_rejects_non_ledger_json_shape(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_ledger.json", '{"not_claims": []}\n')
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "claim_ledger_schema_error" in str(excinfo.value)
    assert json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8")) == before_workflow
    assert _event_records(ws) == before_events
    assert not _state_file(ws, "artifact_registry").exists()


def test_claim_ledger_stage_complete_rejects_invalid_claim_schema(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        json.dumps(
            [
                {
                    "claim_id": "CL-001",
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "claim_type": "unsupported_kind",
                }
            ]
        )
        + "\n",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "claim_ledger_schema_error:claim[0].claim_type" in str(excinfo.value)


def test_claim_ledger_stage_complete_accepts_valid_flat_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )

    assert state["workflow_state"]["current_stage"] == "analyst"
    registry = state["artifact_registry"]["artifacts"]
    assert registry["claim_ledger"]["status"] == "valid"
    assert registry["claim_ledger"]["validation_result"] == "valid_claim_ledger_schema"
    assert registry["claim_ledger"]["sha256"]


def test_claim_ledger_stage_complete_rejects_nested_meta_ai_shape(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "CL-001",
                        "claim_text": "ExampleCo opened a demo facility.",
                        "evidence": {
                            "source_id": "SRC-001",
                            "source_url": "https://example.com/source",
                            "text": "Example evidence.",
                        },
                        "metadata": {"confidence": "high"},
                    }
                ]
            }
        )
        + "\n",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "claim_ledger_schema_error:claim[0].statement" in str(excinfo.value)


def test_claim_ledger_stage_complete_rejects_missing_evidence_text(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        json.dumps(
            [
                {
                    "claim_id": "CL-001",
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                }
            ]
        )
        + "\n",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "claim_ledger_schema_error:claim[0].evidence_text" in str(excinfo.value)


def test_state_check_marks_malformed_claim_ledger_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "analyst")
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_json_artifact(ws, "claim_ledger.json", '{"not_claims": []}\n')

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    ledger_record = state["artifact_registry"]["artifacts"]["claim_ledger"]
    assert ledger_record["status"] == "invalid"
    assert ledger_record["validation_result"].startswith("claim_ledger_schema_error:")
    assert state["workflow_state"]["blocked"] is True


def test_state_check_blocks_modified_frozen_claim_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        _valid_claim_ledger_payload(
            claim_id="CL-002",
            statement="ExampleCo changed the already completed ledger.",
        ),
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Frozen artifact" in str(excinfo.value)
    assert "owner stage 'claim-ledger'" in str(excinfo.value)
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "contaminated"
    assert workflow["run_integrity"]["reference_eligible"] is False
    assert workflow["run_integrity"]["reasons"][0]["reason_code"] == "frozen_artifact_changed"
    contamination_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ]
    assert len(contamination_events) == 1
    assert contamination_events[0]["metadata"]["reason_code"] == "frozen_artifact_changed"


def test_state_check_contamination_event_failure_rolls_back_workflow(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        _valid_claim_ledger_payload(
            claim_id="CL-002",
            statement="ExampleCo changed the already completed ledger.",
        ),
    )
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()
    _fail_appending_event_type(monkeypatch, "run_integrity_contaminated")

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_PARTIAL_WRITE
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_state_check_accepts_unchanged_frozen_claim_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["artifact_registry"]["artifacts"]["claim_ledger"]["sha256"]


def test_editor_stage_complete_can_rewrite_audited_brief(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    analyst_state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    analyst_sha = analyst_state["artifact_registry"]["artifacts"]["audited_brief"]["sha256"]

    audited.write_text("# Brief\n\nEditor-polished draft. [src:CL-001]\n", encoding="utf-8")
    editor_state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )

    editor_sha = editor_state["artifact_registry"]["artifacts"]["audited_brief"]["sha256"]
    assert editor_state["workflow_state"]["current_stage"] == "auditor"
    assert editor_sha
    assert editor_sha != analyst_sha


def test_state_check_allows_append_only_event_log_after_frozen_artifact(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )
    runtime_state.append_event(
        workspace=ws,
        run_id=state["manifest"]["run_id"],
        event_type="control_switchboard_warning",
        actor="system",
        reason="append-only event log is not a frozen artifact",
        metadata={},
    )

    checked = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert checked["artifact_registry"]["artifacts"]["claim_ledger"]["status"] == "valid"


def test_auditor_stage_complete_requires_passing_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)

    with pytest.raises(RuntimeStateError) as missing:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )
    assert missing.value.error_code == "E_QUALITY_GATE_REQUIRED"

    _write_quality_gate_report(ws, blocking=True, stage_id="auditor")
    with pytest.raises(RuntimeStateError) as blocking:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )
    assert blocking.value.error_code == "E_QUALITY_GATE_REQUIRED"


def test_auditor_stage_complete_rejects_wrong_stage_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws, stage_id="doctor")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "gate_stage_id='auditor'" in str(excinfo.value)


def test_auditor_stage_complete_rejects_incomplete_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)
    report_path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["gate_results"] = [
        result for result in report["gate_results"] if result["gate_id"] != "freshness"
    ]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "missing: freshness" in str(excinfo.value)


def test_auditor_stage_complete_rejects_missing_quality_gate_input_metadata(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)
    report_path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["metadata"].pop("brief")
    report["metadata"].pop("ledger")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "brief metadata must be output/intermediate/audited_brief.md" in str(excinfo.value)


def test_auditor_stage_complete_passes_with_clean_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )

    assert state["workflow_state"]["current_stage"] == "finalize"


def test_auditor_stage_complete_ignores_legacy_quality_gate_projection(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws, legacy_only=True)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "output/intermediate/gates/auditor_quality_gate_report.json is required" in str(excinfo.value)


def test_finalize_gate_does_not_mutate_frozen_auditor_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)

    auditor_state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )
    auditor_report = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    auditor_sha = auditor_state["artifact_registry"]["artifacts"]["auditor_quality_gate_report"]["sha256"]
    assert auditor_sha == _sha256_file(auditor_report)

    _write_finalize_report(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    finalize_report = _intermediate(ws) / "gates" / "finalize_quality_gate_report.json"

    assert finalize_report.exists()
    assert _sha256_file(auditor_report) == auditor_sha

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader-facing gates and final artifacts passed",
    )

    registry = state["artifact_registry"]["artifacts"]
    assert registry["auditor_quality_gate_report"]["sha256"] == auditor_sha
    assert registry["finalize_quality_gate_report"]["sha256"] == _sha256_file(finalize_report)
    assert state["workflow_state"]["current_stage"] is None


def test_auditor_stage_complete_rejects_audit_report_missing_audit_status(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    report = json.loads((_intermediate(ws) / "audit_report.json").read_text(encoding="utf-8"))
    report.pop("audit_status")
    _write_json_artifact(ws, "audit_report.json", json.dumps(report) + "\n")
    _write_quality_gate_report(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor and gates passed",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "audit_report_schema_error:audit_status" in str(excinfo.value)


def test_auditor_stage_complete_rejects_audit_report_missing_audit_score(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    report = json.loads((_intermediate(ws) / "audit_report.json").read_text(encoding="utf-8"))
    report.pop("audit_score")
    _write_json_artifact(ws, "audit_report.json", json.dumps(report) + "\n")
    _write_quality_gate_report(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor and gates passed",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "audit_report_schema_error:audit_score" in str(excinfo.value)


def test_auditor_stage_complete_rejects_non_integer_audit_score(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    report = json.loads((_intermediate(ws) / "audit_report.json").read_text(encoding="utf-8"))
    report["audit_score"] = 99.5
    _write_json_artifact(ws, "audit_report.json", json.dumps(report) + "\n")
    _write_quality_gate_report(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor and gates passed",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "audit_report_schema_error:audit_score" in str(excinfo.value)


def test_auditor_stage_complete_records_ledger_and_audit_report_sha(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )

    registry = state["artifact_registry"]["artifacts"]
    auditor_status = state["workflow_state"]["stage_statuses"]["auditor"]
    metadata = auditor_status["metadata"]
    assert metadata["upstream_artifact_sha256"]["claim_ledger"] == registry["claim_ledger"]["sha256"]
    assert metadata["upstream_artifact_sha256"]["audited_brief"] == registry["audited_brief"]["sha256"]
    assert metadata["produced_artifact_sha256"]["audit_report"] == registry["audit_report"]["sha256"]


def test_state_check_preserves_auditor_binding_metadata_for_finalize(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)

    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )

    refreshed = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    metadata = refreshed["workflow_state"]["stage_statuses"]["auditor"]["metadata"]
    assert metadata["upstream_artifact_sha256"]["claim_ledger"]
    assert metadata["upstream_artifact_sha256"]["audited_brief"]
    assert metadata["produced_artifact_sha256"]["audit_report"]

    result = finalize_reader_outputs(
        output_dir=ws / "output",
        project_name="Runtime State Test",
        output_formats=["markdown"],
        output_named_outputs=False,
    )
    assert result.audit_binding["status"] == "pass"


def test_finalize_complete_requires_passing_audit_binding(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    report_path = _intermediate(ws) / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.pop("audit_binding")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == "E_READER_FINAL_GATE_FAILED"
    assert "audit_binding.status must be pass" in str(excinfo.value)


def test_finalize_complete_rejects_stale_audit_binding_hash(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    report_path = _intermediate(ws) / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["audit_binding"]["claim_ledger_sha256"] = "0" * 64
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == "E_READER_FINAL_GATE_FAILED"
    assert "audit_binding.claim_ledger_sha256 does not match current artifact bytes" in str(
        excinfo.value
    )


def test_finalize_complete_rejects_forged_clean_report_with_dirty_artifact(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    (ws / "output" / "brief.md").write_text("# Brief\n\nLeaked [CL-0001].\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="finalize complete",
        )

    assert excinfo.value.error_code == "E_READER_FINAL_GATE_FAILED"


def test_finalize_complete_records_terminal_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )

    workflow = state["workflow_state"]
    transaction_id = workflow["last_completion_transaction"]["transaction_id"]
    assert workflow["current_stage"] is None
    assert workflow["stage_statuses"]["finalize"]["status"] == "complete"
    assert workflow["last_decision"]["decision"] == "finalize"
    assert any(
        event["event_type"] == "decision_recorded"
        and (event.get("metadata") or {}).get("transaction_id") == transaction_id
        for event in _event_records(ws)
    )


def test_finalize_complete_archives_delivery_intermediate_and_control_files(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    run_id = state["manifest"]["run_id"]
    archive = ws / "output" / "runs" / run_id

    assert (archive / "delivery" / "brief.md").exists()
    assert (archive / "intermediate" / "claim_ledger.json").exists()
    assert (archive / "intermediate" / "audit_report.json").exists()
    assert (archive / "intermediate" / "finalize_report.json").exists()
    assert (archive / "control" / "runtime_manifest.json").exists()
    assert (archive / "control" / "workflow_state.json").exists()
    assert (archive / "control" / "artifact_registry.json").exists()
    assert (archive / "control" / "event_log.jsonl").exists()
    assert any(event["event_type"] == "run_archived" for event in _event_records(ws))


def test_run_archive_manifest_uses_workspace_relative_paths_only(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    manifest_path = ws / "output" / "runs" / state["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for record in manifest["files"]:
        assert not Path(record["archive_path"]).is_absolute()
        assert not Path(record["original_path"]).is_absolute()
        assert str(ws) not in record["archive_path"]
        assert str(ws) not in record["original_path"]


def test_run_archive_records_sha256_for_every_file(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "mabw.run_archive.v1"
    assert manifest["run_id"] == state["manifest"]["run_id"]
    assert manifest["run_integrity"] == {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    assert manifest["timing"]["schema_version"] == "mabw.control_timing.v1"
    assert manifest["timing"]["source"] == "event_log"
    assert manifest["timing"]["precision"] == "control_trace_bucket"
    assert manifest["timing"]["status"] in {"available", "partial", "incomplete", "contaminated", "unknown"}
    assert manifest["files"]
    for record in manifest["files"]:
        path = archive / record["archive_path"]
        assert path.exists()
        assert record["sha256"] == _sha256_file(path)
        assert record["size_bytes"] == path.stat().st_size


def test_run_archive_manifest_records_complete_fact_layer(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]

    assert fact_layer["schema_version"] == "mabw.run_archive.fact_layer.v1"
    assert fact_layer["status"] == "complete"
    assert fact_layer["missing_artifact_ids"] == []
    assert fact_layer["source_evidence_count"] == 1
    fact_artifact_ids = {record["artifact_id"] for record in fact_layer["artifacts"]}
    assert {
        "durable_source_evidence_or_source_pack",
        "input_classification",
        "candidate_claims",
        "screened_candidates",
        "claim_ledger",
    } <= fact_artifact_ids
    assert len(fact_artifact_ids) == len(fact_layer["artifacts"])
    for record in fact_layer["artifacts"]:
        if record["artifact_id"] == "durable_source_evidence_or_source_pack":
            assert record["fact_role"] == "durable_source_evidence_pack"
            assert record["file_count"] == 1
            assert len(record["files"]) == 1
            for file_record in record["files"]:
                path = archive / file_record["archive_path"]
                assert path.exists()
                assert file_record["sha256"] == _sha256_file(path)
                assert not Path(file_record["archive_path"]).is_absolute()
                assert not Path(file_record["original_path"]).is_absolute()
        else:
            path = archive / record["archive_path"]
            assert path.exists()
            assert record["sha256"] == _sha256_file(path)
            assert not Path(record["archive_path"]).is_absolute()
            assert not Path(record["original_path"]).is_absolute()


def test_run_archive_manifest_groups_multiple_source_files_as_one_source_pack(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    second = ws / "input" / "sources" / "source-002.md"
    second.write_text("# Source 002\n\nSecond evidence file.\n", encoding="utf-8")

    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]
    artifacts = {
        record["artifact_id"]: record
        for record in fact_layer["artifacts"]
    }
    source_pack = artifacts["durable_source_evidence_or_source_pack"]

    assert fact_layer["status"] == "complete"
    assert fact_layer["source_evidence_count"] == 2
    assert len(artifacts) == len(fact_layer["artifacts"])
    assert source_pack["file_count"] == 2
    assert len(source_pack["files"]) == 2
    assert {record["original_path"] for record in source_pack["files"]} == {
        "input/sources/source-001.md",
        "input/sources/source-002.md",
    }
    for file_record in source_pack["files"]:
        path = archive / file_record["archive_path"]
        assert path.exists()
        assert file_record["sha256"] == _sha256_file(path)


def test_run_archive_excludes_source_candidates_from_fact_layer(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    (ws / "source_candidates.yaml").write_text(
        "schema_version: mabw.source_candidates.v1\n"
        "artifact_type: source_plan_only\n"
        "evidence_status: not_evidence\n",
        encoding="utf-8",
    )
    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]

    assert {
        "artifact_id": "source_candidates",
        "original_path": "source_candidates.yaml",
        "reason": "source_plan_not_evidence",
        "sha256": _sha256_file(ws / "source_candidates.yaml"),
        "size_bytes": (ws / "source_candidates.yaml").stat().st_size,
    } in fact_layer["excluded"]
    assert all(record["original_path"] != "source_candidates.yaml" for record in manifest["files"])
    assert not (archive / "fact_layer" / "source_candidates.yaml").exists()


def test_run_archive_marks_fact_layer_incomplete_when_source_evidence_missing(tmp_path):
    ws = _write_workspace(tmp_path)
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "input_classification.json").write_text(
        json.dumps({"evidence": [], "feedback": [], "instruction": [], "context": [], "skipped": []}),
        encoding="utf-8",
    )

    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]

    assert fact_layer["status"] == "incomplete"
    assert "durable_source_evidence_or_source_pack" in fact_layer["missing_artifact_ids"]


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("empty.md", ""),
        ("template.md", "# Template\n\nFill this in later.\n"),
        ("placeholder.json", '{"placeholder": true}\n'),
        ("readme.txt", "Source directory notes.\n"),
        (".gitkeep", "keep\n"),
    ],
)
def test_run_archive_uses_source_discovery_evidence_filter_for_fact_layer_sources(
    tmp_path,
    filename: str,
    content: str,
):
    ws = _write_workspace(tmp_path)
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / filename).write_text(content, encoding="utf-8")
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "input_classification.json").write_text(
        json.dumps({
            "evidence": [{"path": str(source_dir / filename), "name": filename}],
            "feedback": [],
            "instruction": [],
            "context": [],
            "skipped": [],
        }),
        encoding="utf-8",
    )

    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]

    assert fact_layer["status"] == "incomplete"
    assert fact_layer["source_evidence_count"] == 0
    assert "durable_source_evidence_or_source_pack" in fact_layer["missing_artifact_ids"]
    assert all(
        record["artifact_id"] != "durable_source_evidence_or_source_pack"
        for record in fact_layer["artifacts"]
    )


def test_run_archive_manifest_marks_malformed_run_integrity_unknown(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    finalize_report = json.loads((_intermediate(ws) / "finalize_report.json").read_text(encoding="utf-8"))
    workflow = dict(state["workflow_state"])
    workflow["run_integrity"] = "bad"
    run_id = f"{state['manifest']['run_id']}-malformed"

    archive = archive_finalized_run(
        workspace=ws,
        run_id=run_id,
        manifest=state["manifest"],
        workflow=workflow,
        artifact_registry=state["artifact_registry"],
        finalize_report=finalize_report,
    )
    manifest = archive["manifest"]

    assert manifest["run_integrity"]["status"] == "unknown"
    assert manifest["run_integrity"]["reference_eligible"] is False


def test_finalize_complete_archive_is_idempotent_when_content_matches(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    archive = state["run_archive"]
    finalize_report = json.loads((_intermediate(ws) / "finalize_report.json").read_text(encoding="utf-8"))

    result = archive_finalized_run(
        workspace=ws,
        run_id=state["manifest"]["run_id"],
        manifest=state["manifest"],
        workflow=state["workflow_state"],
        artifact_registry=state["artifact_registry"],
        finalize_report=finalize_report,
    )

    assert result["archive_manifest_sha256"] == archive["archive_manifest_sha256"]
    assert result["file_count"] == archive["file_count"]


def test_existing_archive_rejects_corrupted_fact_layer_projection(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    state = _complete_finalized_workspace(ws)
    archive_root = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest_path = archive_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fact_layer"] = {
        "schema_version": "mabw.run_archive.fact_layer.v1",
        "status": "complete",
        "artifacts": [],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    finalize_report = json.loads((_intermediate(ws) / "finalize_report.json").read_text(encoding="utf-8"))

    with pytest.raises(runtime_state.operations.RunArchiveError) as excinfo:
        archive_finalized_run(
            workspace=ws,
            run_id=state["manifest"]["run_id"],
            manifest=state["manifest"],
            workflow=state["workflow_state"],
            artifact_registry=state["artifact_registry"],
            finalize_report=finalize_report,
        )

    assert excinfo.value.error_code == runtime_state.operations.E_RUN_ARCHIVE_CONFLICT
    assert "fact_layer projection differs" in str(excinfo.value)


def test_import_fact_layer_transaction_copies_archive_and_marks_upstream_stages(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"

    state = import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        runtime="codex",
        repo_workdir=ROOT,
    )

    manifest = state["manifest"]
    workflow = state["workflow_state"]
    assert manifest["recipe"] == "fast-rerun"
    assert manifest["runtime"] == "codex"
    import_record = manifest["fact_layer_import"]
    assert import_record["schema_version"] == runtime_state.operations.FACT_LAYER_IMPORT_SCHEMA
    assert import_record["source_run_id"] == finalized["manifest"]["run_id"]
    assert import_record["source_archive_manifest"] == f"output/runs/{finalized['manifest']['run_id']}/manifest.json"
    assert str(source_ws) not in json.dumps(import_record)
    assert import_record["imported_file_count"] >= 4
    assert workflow["current_stage"] == "analyst"
    for stage_id in ("doctor", "source-discovery", "input-governance", "scout", "screener", "claim-ledger"):
        status = workflow["stage_statuses"][stage_id]
        assert status["status"] == "complete"
        assert status["metadata"]["satisfied_by_import"] is True
    assert (target_ws / "input" / "sources" / "source-001.md").exists()
    assert (target_ws / "output" / "input_classification.json").exists()
    assert (_intermediate(target_ws) / "candidate_claims.json").exists()
    assert (_intermediate(target_ws) / "screened_candidates.json").exists()
    assert (_intermediate(target_ws) / "claim_ledger.json").exists()
    events = _event_records(target_ws)
    assert [event["event_type"] for event in events].count("fact_layer_imported") == 1
    assert not any(event["event_type"] == "decision_recorded" for event in events)

    checked = check_runtime_state(workspace=target_ws, repo_workdir=ROOT)
    assert checked["manifest"]["fact_layer_import"] == import_record
    assert checked["fact_layer_import"]["status"] == "valid"
    assert checked["fact_layer_import"]["source_run_id"] == finalized["manifest"]["run_id"]
    assert all(
        stage["display_status"] == "complete via import"
        for stage in checked["fact_layer_import"]["imported_stages"]
    )
    assert checked["workflow_state"]["stage_statuses"]["claim-ledger"]["metadata"]["satisfied_by_import"] is True

    shown = show_runtime_state(workspace=target_ws)
    assert shown["fact_layer_import"]["status"] == "valid"
    assert shown["fact_layer_import"]["next_stage"] == "analyst"


def test_import_fact_layer_transaction_records_target_freshness_snapshot(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-05-01"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"

    state = import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )

    freshness = state["manifest"]["fact_layer_import"]["freshness_at_import"]
    assert freshness["schema_version"] == "mabw.fact_layer_import.freshness.v1"
    assert freshness["status"] == "stale"
    assert freshness["report_date"] == "2026-06-20"
    assert freshness["max_source_age_days"] == 14
    assert freshness["stale_claim_count"] == 1
    assert freshness["stale_claims"][0]["claim_id"] == "CL-001"


def test_fast_rerun_finalize_complete_rejects_stale_imported_fact_layer(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-05-01"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=target_ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_READER_FINAL_GATE_FAILED
    assert "Fast-rerun imported fact layer is stale at target delivery time" in str(excinfo.value)
    assert not (target_ws / "output" / "runs").exists()


@pytest.mark.parametrize(
    "report_yaml",
    [
        """
report:
  date: "not-a-date"
  max_source_age_days: 14
""",
        """
report:
  date: "2026-06-20"
  max_source_age_days: "not-a-number"
""",
        """
report:
  max_source_age_days: 14
""",
    ],
)
def test_fast_rerun_finalize_complete_rejects_unknown_target_freshness_window(tmp_path, report_yaml):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        f"""
project:
  name: "Runtime State Test"
{report_yaml}
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-06-10"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=target_ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_READER_FINAL_GATE_FAILED
    assert "freshness cannot be verified at target delivery time" in str(excinfo.value)
    assert not (target_ws / "output" / "runs").exists()


def test_fast_rerun_finalize_complete_rejects_retrieved_at_as_publication_freshness(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"retrieved_at": "2026-06-19"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_state = import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    import_freshness = import_state["manifest"]["fact_layer_import"]["freshness_at_import"]
    assert import_freshness["status"] == "unknown"
    assert import_freshness["reason"] == "claim_publication_dates_missing"

    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=target_ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_READER_FINAL_GATE_FAILED
    assert "claim_publication_dates_missing" in str(excinfo.value)
    assert not (target_ws / "output" / "runs").exists()


def test_fast_rerun_archive_records_source_relationship(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-06-10"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_state = import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    state = complete_finalize_transaction(
        workspace=target_ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )

    archive_manifest_path = target_ws / "output" / "runs" / state["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest_path.read_text(encoding="utf-8"))
    fast_rerun = manifest["fast_rerun"]
    assert fast_rerun["schema_version"] == "mabw.run_archive.fast_rerun.v1"
    assert fast_rerun["source_run_id"] == finalized["manifest"]["run_id"]
    assert fast_rerun["fact_layer_sha256"] == import_state["manifest"]["fact_layer_import"]["fact_layer_sha256"]
    assert fast_rerun["freshness_at_finalize"]["status"] == "within_window"
    assert fast_rerun["timing_comparability"] == "downstream_only"


def test_fast_rerun_archive_records_finalize_time_freshness(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-05-01"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-05-10"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    state = complete_finalize_transaction(
        workspace=target_ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )

    archive_manifest_path = target_ws / "output" / "runs" / state["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest_path.read_text(encoding="utf-8"))
    fast_rerun = manifest["fast_rerun"]
    runtime_manifest = json.loads((target_ws / "output" / "intermediate" / "runtime_manifest.json").read_text())
    persisted_freshness = runtime_manifest["fact_layer_import"]["freshness_at_finalize"]
    assert fast_rerun["freshness_at_import"]["status"] == "stale"
    assert fast_rerun["freshness_at_import"]["report_date"] == "2026-06-20"
    assert fast_rerun["freshness_at_finalize"]["status"] == "within_window"
    assert fast_rerun["freshness_at_finalize"]["report_date"] == "2026-05-10"
    assert persisted_freshness == fast_rerun["freshness_at_finalize"]


def test_state_show_human_output_reports_imported_stages(tmp_path, capsys):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        runtime="codex",
        repo_workdir=ROOT,
    )

    rc = main(["state", "show", "--workspace", str(target_ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[state show] fact_layer_import: valid" in out
    assert "[state show] imported_satisfied_stages:" in out
    assert "source-discovery: complete via import" in out
    assert "claim-ledger: complete via import" in out
    assert "[state show] next_runtime_stage: analyst" in out


def test_import_fact_layer_transaction_rejects_incomplete_fact_layer(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "incomplete" in str(excinfo.value)


def test_import_fact_layer_transaction_rejects_contaminated_archive(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "test"}],
    }
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "clean reference-eligible" in str(excinfo.value)


def test_import_fact_layer_transaction_rejects_archive_hash_mismatch(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    (archive_root / "fact_layer" / "output" / "intermediate" / "claim_ledger.json").write_text(
        "corrupted\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "hash does not match manifest" in str(excinfo.value)
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_existing_source_target(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    existing_source = target_ws / "input" / "sources" / "source-001.md"
    existing_source.parent.mkdir(parents=True, exist_ok=True)
    existing_source.write_text("user source that must not be overwritten\n", encoding="utf-8")
    before = existing_source.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "already exist" in str(excinfo.value)
    assert existing_source.read_bytes() == before
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_existing_intermediate_target(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    existing_ledger = _intermediate(target_ws) / "claim_ledger.json"
    existing_ledger.write_text(_valid_claim_ledger_payload("CL-LOCAL"), encoding="utf-8")
    before = existing_ledger.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "already exist" in str(excinfo.value)
    assert existing_ledger.read_bytes() == before
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_uses_code_required_artifacts_not_manifest_claim(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]
    fact_layer["required_artifact_ids"] = [
        item for item in fact_layer["required_artifact_ids"] if item != "claim_ledger"
    ]
    fact_layer["artifacts"] = [
        item for item in fact_layer["artifacts"] if item.get("artifact_id") != "claim_ledger"
    ]
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert excinfo.value.details["missing_artifact_ids"] == ["claim_ledger"]
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_self_consistent_invalid_claim_ledger(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    bad_ledger = archive_root / "fact_layer" / "output" / "intermediate" / "claim_ledger.json"
    bad_ledger.write_text("{not json}\n", encoding="utf-8")
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    for artifact in manifest["fact_layer"]["artifacts"]:
        if artifact.get("artifact_id") == "claim_ledger":
            artifact["sha256"] = _sha256_file(bad_ledger)
            artifact["size_bytes"] = bad_ledger.stat().st_size
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "claim_ledger" in str(excinfo.value)
    assert not (_intermediate(target_ws) / "claim_ledger.json").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_source_candidates_fact_layer_artifact(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    source_candidates = archive_root / "fact_layer" / "source_candidates.yaml"
    source_candidates.write_text("artifact_type: source_plan_only\n", encoding="utf-8")
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["fact_layer"]["artifacts"].append({
        "artifact_id": "source_candidates",
        "fact_role": "fact_layer_artifact",
        "archive_path": "fact_layer/source_candidates.yaml",
        "original_path": "source_candidates.yaml",
        "sha256": _sha256_file(source_candidates),
        "size_bytes": source_candidates.stat().st_size,
    })
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "source_candidates" in str(excinfo.value)
    assert not (target_ws / "source_candidates.yaml").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_unknown_delivery_artifact(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    delivery_brief = archive_root / "delivery" / "brief.md"
    assert delivery_brief.exists()
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["fact_layer"]["artifacts"].append({
        "artifact_id": "delivery_brief",
        "fact_role": "fact_layer_artifact",
        "archive_path": "delivery/brief.md",
        "original_path": "output/delivery/brief.md",
        "sha256": _sha256_file(delivery_brief),
        "size_bytes": delivery_brief.stat().st_size,
    })
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "unsupported artifact_id" in str(excinfo.value)
    assert not (target_ws / "output" / "delivery" / "brief.md").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_source_pack_file_outside_sources(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    delivery_brief = archive_root / "delivery" / "brief.md"
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    for artifact in manifest["fact_layer"]["artifacts"]:
        if artifact.get("artifact_id") == "durable_source_evidence_or_source_pack":
            artifact["files"].append({
                "archive_path": "delivery/brief.md",
                "original_path": "output/delivery/brief.md",
                "sha256": _sha256_file(delivery_brief),
                "size_bytes": delivery_brief.stat().st_size,
            })
            artifact["file_count"] = len(artifact["files"])
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "input/sources" in str(excinfo.value)
    assert not (target_ws / "output" / "delivery" / "brief.md").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_source_candidates_inside_source_pack(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    source_candidates = archive_root / "fact_layer" / "input" / "sources" / "source_candidates.yaml"
    source_candidates.parent.mkdir(parents=True, exist_ok=True)
    source_candidates.write_text("artifact_type: source_plan_only\n", encoding="utf-8")
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    for artifact in manifest["fact_layer"]["artifacts"]:
        if artifact.get("artifact_id") == "durable_source_evidence_or_source_pack":
            artifact["files"].append({
                "archive_path": "fact_layer/input/sources/source_candidates.yaml",
                "original_path": "input/sources/source_candidates.yaml",
                "sha256": _sha256_file(source_candidates),
                "size_bytes": source_candidates.stat().st_size,
            })
            artifact["file_count"] = len(artifact["files"])
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "source_candidates.yaml" in str(excinfo.value)
    assert not (target_ws / "input" / "sources" / "source_candidates.yaml").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_duplicate_non_pack_artifact_id(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    claim_record = next(
        item for item in manifest["fact_layer"]["artifacts"] if item.get("artifact_id") == "claim_ledger"
    )
    duplicate = dict(claim_record)
    duplicate["original_path"] = "output/intermediate/claim_ledger_duplicate.json"
    manifest["fact_layer"]["artifacts"].append(duplicate)
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "duplicate non-pack artifact" in str(excinfo.value)
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_duplicate_workspace_target(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    extra_source = archive_root / "fact_layer" / "input" / "sources" / "source-duplicate.md"
    extra_source.parent.mkdir(parents=True, exist_ok=True)
    extra_source.write_text("# duplicate target source\n", encoding="utf-8")
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    for artifact in manifest["fact_layer"]["artifacts"]:
        if artifact.get("artifact_id") == "durable_source_evidence_or_source_pack":
            artifact["files"].append({
                "archive_path": "fact_layer/input/sources/source-duplicate.md",
                "original_path": "input/sources/source-001.md",
                "sha256": _sha256_file(extra_source),
                "size_bytes": extra_source.stat().st_size,
            })
            artifact["file_count"] = len(artifact["files"])
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "duplicate import targets" in str(excinfo.value)
    assert excinfo.value.details["duplicate_targets"][0]["workspace_path"] == "input/sources/source-001.md"
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_stale_downstream_output(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    stale_delivery = target_ws / "output" / "delivery" / "brief.md"
    stale_delivery.parent.mkdir(parents=True, exist_ok=True)
    stale_delivery.write_text("stale delivery must not survive import\n", encoding="utf-8")
    before = stale_delivery.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "existing source/output leftovers" in str(excinfo.value)
    assert stale_delivery.read_bytes() == before
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_existing_runtime_state(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    initialize_runtime_state(workspace=target_ws, repo_workdir=ROOT)

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "without existing runtime state" in str(excinfo.value)


def test_state_import_fact_layer_cli_outputs_json(tmp_path, capsys):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"

    rc = main([
        "state",
        "import-fact-layer",
        "--workspace",
        str(target_ws),
        "--archive",
        str(archive_manifest),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest"]["recipe"] == "fast-rerun"
    assert payload["fact_layer_import"]["source_run_id"] == finalized["manifest"]["run_id"]


def test_finalize_complete_rejects_archive_conflict_for_same_run_id(tmp_path):
    ws = _write_workspace(tmp_path)
    initialized = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    run_id = initialized["manifest"]["run_id"]
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    archive = ws / "output" / "runs" / run_id
    (archive / "delivery").mkdir(parents=True)
    (archive / "delivery" / "brief.md").write_text("conflicting archive content\n", encoding="utf-8")
    (archive / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.run_archive.v1",
                "run_id": run_id,
                "archived_at": "2026-06-11T00:00:00Z",
                "source": "finalize-complete",
                "files": [
                    {
                        "role": "delivery",
                        "archive_path": "delivery/brief.md",
                        "original_path": "output/delivery/brief.md",
                        "sha256": "not-the-real-sha",
                        "size_bytes": 0,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_RUN_ARCHIVE_CONFLICT
    after_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after_workflow == before_workflow
    assert after_workflow["current_stage"] == "finalize"
    assert after_workflow["stage_statuses"]["finalize"]["status"] == "ready"


def test_reset_state_archives_finalized_run_before_new_run(tmp_path):
    ws = _write_workspace(tmp_path)
    first = _complete_finalized_workspace(ws)
    old_run_id = first["manifest"]["run_id"]
    shutil.rmtree(ws / "output" / "runs" / old_run_id)

    second = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert second["manifest"]["run_id"] != old_run_id
    assert (ws / "output" / "runs" / old_run_id / "manifest.json").exists()


def test_fresh_reset_state_init_remains_reference_eligible(tmp_path):
    ws = _write_workspace(tmp_path)

    state = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    integrity = state["workflow_state"]["run_integrity"]
    assert integrity["status"] == "clean"
    assert integrity["reference_eligible"] is True
    assert integrity["clean_single_shot"] is True
    assert integrity["reasons"] == []
    assert [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ] == []


def test_reset_state_does_not_require_archive_for_incomplete_run(tmp_path):
    ws = _write_workspace(tmp_path)
    first = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    second = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert second["manifest"]["run_id"] != first["manifest"]["run_id"]
    assert not (ws / "output" / "runs" / first["manifest"]["run_id"]).exists()
    integrity = second["workflow_state"]["run_integrity"]
    assert integrity["status"] == "contaminated"
    assert integrity["reference_eligible"] is False
    assert integrity["clean_single_shot"] is False
    assert integrity["reasons"][0]["reason_code"] == "run_reset"
    contamination_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ]
    assert len(contamination_events) == 1
    assert contamination_events[0]["metadata"]["reason_code"] == "run_reset"


def test_reset_state_event_append_failure_rolls_back_control_files(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    first = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    old_run_id = first["manifest"]["run_id"]
    before_manifest = _state_file(ws, "runtime_manifest").read_bytes()
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()
    _fail_appending_event_type(monkeypatch, "run_reset")

    with pytest.raises(RuntimeStateError) as excinfo:
        initialize_runtime_state(
            workspace=ws,
            repo_workdir=ROOT,
            reset_state=True,
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_PARTIAL_WRITE
    assert _state_file(ws, "runtime_manifest").read_bytes() == before_manifest
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events
    assert not (ws / "output" / "intermediate" / f"event_log.{old_run_id}.jsonl").exists()


def test_archive_rejects_finalize_report_delivery_artifact_outside_output_delivery(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    report_path = _intermediate(ws) / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_artifacts"] = [str(ws / "output" / "brief.md")]
    report["delivery_artifact_sha256"] = {
        str(ws / "output" / "brief.md"): _sha256_file(ws / "output" / "brief.md"),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_READER_FINAL_GATE_FAILED
    assert "output/delivery" in str(excinfo.value)


def test_finalize_complete_accepts_reader_facing_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_finalize_report(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    report_path = _intermediate(ws) / "quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["metadata"]["brief"] = "output/brief.md"
    report["metadata"]["ledger"] = "output/intermediate/claim_ledger.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader-facing gates and final artifacts passed",
    )

    assert state["workflow_state"]["current_stage"] is None
    assert state["workflow_state"]["stage_statuses"]["finalize"]["status"] == "complete"


def test_finalize_complete_ignores_legacy_quality_gate_projection(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_finalize_report(ws)
    _write_quality_gate_report(ws, stage_id="finalize", legacy_only=True)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader-facing gates and final artifacts passed",
        )

    assert excinfo.value.error_code == "E_READER_FINAL_GATE_FAILED"
    assert "output/intermediate/gates/finalize_quality_gate_report.json is required" in str(excinfo.value)


def test_completion_transactions_preserve_manifest_extensions(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    manifest_path = _state_file(ws, "runtime_manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recipe"] = "fast-rerun"
    manifest["improvement"] = {
        "ledger_sha256": None,
        "memory_sha256": "zero",
        "snapshot_path": None,
        "snapshot_sha256": None,
        "materialized_entry_ids": [],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )
    after_stage = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_stage["recipe"] == "fast-rerun"
    assert after_stage["improvement"] == manifest["improvement"]

    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    after_check = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_check["recipe"] == "fast-rerun"
    assert after_check["improvement"] == manifest["improvement"]

    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )
    after_finalize = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_finalize["recipe"] == "fast-rerun"
    assert after_finalize["improvement"] == manifest["improvement"]


def test_state_decide_rejects_out_of_order_stage_and_leaves_workflow_unchanged(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    rc = main([
        "state",
        "decide",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "auditor",
        "--decision",
        "continue",
        "--reason",
        "out of order",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "does not match current stage" in payload["error"]
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after == before


def test_state_decide_event_failure_leaves_workflow_unchanged(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(runtime_event_log, "_append_jsonl", fail_append)

    with pytest.raises(RuntimeStateError):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            decision="block_run",
            reason="doctor failed",
        )

    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after == before


def test_state_check_preserves_explicit_block_decision(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    record_decision(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        decision="block_run",
        reason="doctor failed",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["workflow_state"]["blocked"] is True
    assert state["workflow_state"]["current_stage"] == "doctor"
    assert state["workflow_state"]["stage_statuses"]["doctor"]["status"] == "blocked"
    assert state["workflow_state"]["blocking_reason"] == "doctor failed"


def test_state_check_event_failure_leaves_state_unchanged(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "candidate_claims.json")
    _set_current_stage(ws, "scout")
    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(runtime_event_log, "_append_jsonl", fail_append)

    with pytest.raises(RuntimeStateError):
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after == before
    assert not _state_file(ws, "artifact_registry").exists()


def test_state_check_rejects_unknown_event_type(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "cli",
                "event_id": "evt_fake",
                "event_type": "stage_completed",
                "reason": "model-written fake event",
                "run_id": state["manifest"]["run_id"],
                "schema_version": runtime_state.operations.EVENT_LOG_SCHEMA,
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Unknown event type" in str(excinfo.value)


def test_state_check_rejects_unknown_event_actor(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "scout",
                "event_id": "evt_fake",
                "event_type": "decision_recorded",
                "reason": "model-written fake event",
                "run_id": state["manifest"]["run_id"],
                "schema_version": runtime_state.operations.EVENT_LOG_SCHEMA,
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Unknown event actor" in str(excinfo.value)


def test_state_check_strict_json_reports_event_log_integrity_error(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "scout",
                "event_id": "evt_fake",
                "event_type": "stage_completed",
                "reason": "model-written fake event",
                "run_id": state["manifest"]["run_id"],
                "schema_version": runtime_state.operations.EVENT_LOG_SCHEMA,
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main([
        "state",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error_code"] == runtime_state.operations.E_TRANSACTION_INTEGRITY


def test_state_check_rejects_event_log_missing_schema_version(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "cli",
                "event_id": "evt_fake",
                "event_type": "decision_recorded",
                "reason": "model-written incomplete event",
                "run_id": state["manifest"]["run_id"],
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Unsupported event log schema" in str(excinfo.value)


def test_state_check_rejects_non_newline_terminated_event_log(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "cli",
                "event_id": "evt_fake",
                "event_type": "decision_recorded",
                "reason": "model-written unterminated event",
                "run_id": state["manifest"]["run_id"],
                "schema_version": runtime_state.operations.EVENT_LOG_SCHEMA,
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "newline-terminated" in str(excinfo.value)


def test_state_check_rejects_malformed_event_log_line(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(event_log.read_text(encoding="utf-8") + "{bad json}\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Invalid JSON event log line" in str(excinfo.value)


def test_state_check_only_writes_changed_events_once(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")
    (_intermediate(ws) / "candidate_claims.json").write_text("{broken", encoding="utf-8")

    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    first_events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()
    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    second_events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()

    assert len(second_events) == len(first_events)
    event_types = [json.loads(line)["event_type"] for line in first_events]
    assert event_types.count("stage_status_changed") == 1
    assert event_types.count("run_blocked") == 1


def test_state_show_json_handles_corrupted_state_without_traceback(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _state_file(ws, "workflow_state").write_text("{broken", encoding="utf-8")

    rc = main(["state", "show", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Invalid JSON state file" in payload["error"]


def test_reset_state_archives_old_event_log(tmp_path):
    ws = _write_workspace(tmp_path)
    first = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    old_run_id = first["manifest"]["run_id"]

    second = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert second["manifest"]["run_id"] != old_run_id
    archived = ws / "output" / "intermediate" / f"event_log.{old_run_id}.jsonl"
    assert archived.exists()
    assert _state_file(ws, "event_log").exists()
    reset_event = json.loads(_state_file(ws, "event_log").read_text(encoding="utf-8").splitlines()[0])
    assert reset_event["event_type"] == "run_reset"
    assert reset_event["metadata"]["previous_run_id"] == old_run_id
    assert reset_event["metadata"]["archived_event_log"] == f"output/intermediate/event_log.{old_run_id}.jsonl"


def test_reset_state_recovers_from_corrupted_workflow_state(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _state_file(ws, "workflow_state").write_text("{broken", encoding="utf-8")

    state = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert state["ok"] is True
    assert state["workflow_state"]["current_stage"] == "doctor"


def test_state_paths_are_workspace_relative(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["manifest"]["runtime_state_files"] == RUNTIME_STATE_FILES
    for record in state["artifact_registry"]["artifacts"].values():
        assert not Path(record["path"]).is_absolute()


def test_show_runtime_state_reports_event_count(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = show_runtime_state(workspace=ws)

    assert state["event_count"] >= 1
