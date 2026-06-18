"""Tests for v0.6.3 deterministic quality gate controls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import multi_agent_brief.orchestrator.runtime_state as runtime_state
from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    check_runtime_state,
    initialize_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import _allowed_decisions_for_stage
from multi_agent_brief.quality_gates import state as quality_gate_state
from multi_agent_brief.quality_gates.contract import (
    GATE_IDS,
    interpret_quality_gate_binding,
    quality_gate_report_path_for_stage,
    require_quality_gate_binding_pass,
)


ROOT = Path(__file__).resolve().parent.parent


def _write_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        """
project:
  name: "TargetCo"
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    (ws / "user.md").write_text("# User\nTarget: TargetCo\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    return ws


def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _report_path(ws: Path) -> Path:
    return ws / "output" / "intermediate" / "quality_gate_report.json"


def _auditor_report_path(ws: Path) -> Path:
    return ws / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json"


def _finalize_report_path(ws: Path) -> Path:
    return ws / "output" / "intermediate" / "gates" / "finalize_quality_gate_report.json"


def _write_ledger(ws: Path, claims: list[dict]) -> None:
    (_intermediate(ws) / "claim_ledger.json").write_text(
        json.dumps(claims, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_audited_brief(ws: Path, text: str) -> None:
    (_intermediate(ws) / "audited_brief.md").write_text(text, encoding="utf-8")


def _write_reader_brief(ws: Path, text: str) -> None:
    output = ws / "output"
    output.mkdir(parents=True, exist_ok=True)
    (output / "brief.md").write_text(text, encoding="utf-8")


def _write_delivery_brief(ws: Path, text: str, *, name: str = "brief.md") -> None:
    delivery = ws / "output" / "delivery"
    delivery.mkdir(parents=True, exist_ok=True)
    (delivery / name).write_text(text, encoding="utf-8")


def _write_supported_target_ledger(ws: Path) -> None:
    _write_ledger(
        ws,
        [
            {
                "claim_id": "CL-001",
                "statement": "TargetCo opened a demo facility and reported 42 deployments.",
                "source_id": "SRC-001",
                "evidence_text": "TargetCo opened a demo facility and reported 42 deployments.",
                "source_url": "https://example.com/targetco-demo",
                "source_type": "web_search",
                "metadata": {
                    "source_title": "TargetCo Demo Facility",
                    "publisher": "Example News",
                    "published_at": "2026-06-01",
                    "importance": "high",
                },
            }
        ],
    )


def _prepare_editor_gate_workspace(tmp_path: Path, *, analyst_text: str, editor_text: str) -> Path:
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_supported_target_ledger(ws)
    _set_current_stage(ws, "analyst")
    _write_audited_brief(ws, analyst_text)
    runtime_state.complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    _write_audited_brief(ws, editor_text)
    runtime_state.complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    return ws


def _quality_gate_payload(*, status: str, stage_id: str) -> dict:
    artifact_id = "finalize_quality_gate_report" if stage_id == "finalize" else "auditor_quality_gate_report"
    return {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "created_at": "2026-06-08T00:00:00+00:00",
        "updated_at": "2026-06-08T00:00:00+00:00",
        "workspace": ".",
        "report_date": "",
        "policy_pack": "default",
        "status": status,
        "gate_results": [
            {
                "gate_id": gate_id,
                "status": status if gate_id == "target_relevance" else "pass",
                "blocking": status == "fail" and gate_id == "target_relevance",
                "finding_ids": ["QG_TARGET_001"] if gate_id == "target_relevance" and status == "fail" else [],
            }
            for gate_id in ("freshness", "material_fact", "target_relevance")
        ],
        "findings": [
            {
                "finding_id": "QG_TARGET_001",
                "gate_id": "target_relevance",
                "finding_type": "target_relevance_gap",
                "severity": "high",
                "blocking_level": "blocking",
                "blocking": True,
                "repair_owner": "editor",
                "stage_id": stage_id,
                "artifact_id": artifact_id,
                "gate_stage_id": stage_id,
                "gate_artifact_id": artifact_id,
                "claim_id": None,
                "source_id": None,
                "line_number": None,
                "description": "Synthetic gate failure.",
                "recommendation": "Repair before completion.",
                "evidence_ref": "",
                "metadata": {},
            }
        ] if status == "fail" else [],
        "metadata": {
            "brief": "output/brief.md" if stage_id == "finalize" else "output/intermediate/audited_brief.md",
            "ledger": "output/intermediate/claim_ledger.json",
            "stage_id": stage_id,
            "gate_stage_id": stage_id,
            "gate_artifact_id": artifact_id,
        },
    }


def _valid_audit_report_payload() -> str:
    return json.dumps(
        {
            "audit_status": "pass",
            "audit_score": 100,
            "findings": [],
            "metadata": {},
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _events(ws: Path) -> list[dict[str, object]]:
    path = ws / "output" / "intermediate" / "event_log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_quality_gate_binding_interpreter_rejects_pass_status_with_blocking_finding(tmp_path):
    ws = _write_workspace(tmp_path)
    report_path = quality_gate_report_path_for_stage(ws, "auditor")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _quality_gate_payload(status="pass", stage_id="auditor")
    blocking_payload = _quality_gate_payload(status="fail", stage_id="auditor")
    payload["findings"] = blocking_payload["findings"]
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    verdict = interpret_quality_gate_binding(
        workspace=ws,
        stage_id="auditor",
        expected_brief="output/intermediate/audited_brief.md",
        expected_ledger="output/intermediate/claim_ledger.json",
        stages=runtime_state.load_stage_specs(ROOT),
        artifacts=runtime_state.load_artifact_contracts(ROOT),
    )

    assert verdict.kind == "degraded"
    assert any("blocking findings" in reason for reason in require_quality_gate_binding_pass(verdict))


def test_quality_gate_binding_interpreter_rejects_pass_status_with_blocking_gate_result(tmp_path):
    ws = _write_workspace(tmp_path)
    report_path = quality_gate_report_path_for_stage(ws, "auditor")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _quality_gate_payload(status="pass", stage_id="auditor")
    payload["gate_results"][1]["blocking"] = True
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    verdict = interpret_quality_gate_binding(
        workspace=ws,
        stage_id="auditor",
        expected_brief="output/intermediate/audited_brief.md",
        expected_ledger="output/intermediate/claim_ledger.json",
        stages=runtime_state.load_stage_specs(ROOT),
        artifacts=runtime_state.load_artifact_contracts(ROOT),
    )

    assert verdict.kind == "degraded"
    assert any("blocking gate_results" in reason for reason in require_quality_gate_binding_pass(verdict))


def _set_current_stage(ws: Path, stage_id: str) -> None:
    stages = runtime_state.load_stage_specs(ROOT)
    stage_ids = [str(stage.get("stage_id") or "") for stage in stages if stage.get("stage_id")]
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
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
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _advance_to_auditor(ws: Path) -> None:
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json(ws, "candidate_claims.json")
    _write_json(ws, "screened_candidates.json")
    if not (_intermediate(ws) / "claim_ledger.json").exists():
        _write_ledger(ws, [])
    if not (_intermediate(ws) / "audited_brief.md").exists():
        _write_audited_brief(ws, "# Brief\n")
    _set_current_stage(ws, "auditor")


def _write_json(ws: Path, name: str, payload: str = "[]\n") -> None:
    (_intermediate(ws) / name).write_text(payload, encoding="utf-8")


def test_real_gate_check_blocks_current_auditor_but_keeps_repair_target(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo update.\n\n## Detail\nRevenue was $42 million.\n",
    )
    _advance_to_auditor(ws)
    _write_json(ws, "audit_report.json", "{}\n")

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    report = payload["quality_gate_report"]
    blocker = next(finding for finding in report["findings"] if finding["finding_type"] == "number_without_source")
    assert blocker["gate_stage_id"] == "auditor"
    assert blocker["gate_artifact_id"] == "auditor_quality_gate_report"
    assert blocker["stage_id"] == "editor"
    assert blocker["artifact_id"] == "audited_brief"
    assert blocker["repair_stage_id"] == "editor"
    assert blocker["repair_artifact_id"] == "audited_brief"
    assert payload["repair_route"]["repair_owner"] == "editor"
    assert payload["repair_route"]["must_rerun_from"] == "auditor"
    assert payload["repair_route"]["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert "output/intermediate/audit_report.json" in payload["repair_route"]["blocked_direct_edits"]
    assert payload["required_commands"] == [
        f"multi-agent-brief repair route --workspace {ws.resolve()} --json",
        f"multi-agent-brief repair start --workspace {ws.resolve()} --json",
        f"multi-agent-brief repair complete --workspace {ws.resolve()} --reason \"<reason>\" --json",
    ]
    assert payload["repair_warnings"] == [
        "Do not edit frozen artifacts directly.",
        "Direct edits will mark the run contaminated and non-reference-eligible.",
    ]

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert state["workflow_state"]["current_stage"] == "auditor"
    assert state["workflow_state"]["blocked"] is True
    assert "blocking quality gate findings" in state["workflow_state"]["blocking_reason"]

    rc = main([
        "gates",
        "show",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
    ])
    assert rc == 0
    output = capsys.readouterr().out
    assert "[gates show] required_commands:" in output
    assert f"multi-agent-brief repair start --workspace {ws.resolve()} --json" in output
    assert "[gates show] repair_warnings:" in output
    assert "Do not edit frozen artifacts directly." in output
    assert "Direct edits will mark the run contaminated and non-reference-eligible." in output
    assert "[gates show] repair_owner: editor" in output
    assert "[gates show] must_rerun_from: auditor" in output
    assert "output/intermediate/audited_brief.md" in output
    assert "output/intermediate/audit_report.json" in output

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
        "skip quality gates",
        "--json",
    ])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert payload["details"]["required_command"] == "stage-complete"


def test_evaluate_quality_gate_findings_is_read_only_and_matches_report(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo update.\n\n## Detail\nRevenue was $42 million.\n",
    )
    _repo, stages, artifacts = quality_gate_state._contracts(workspace=ws, repo_workdir=ROOT)
    ledger = quality_gate_state._load_ledger(_intermediate(ws) / "claim_ledger.json", required=True)

    gate_findings = quality_gate_state.evaluate_quality_gate_findings(
        markdown=(_intermediate(ws) / "audited_brief.md").read_text(encoding="utf-8"),
        ledger=ledger,
        config=quality_gate_state._load_config(ws),
        user_text=(ws / "user.md").read_text(encoding="utf-8"),
        analyst_markdown=None,
        report_date="",
        max_source_age_days=None,
        strict=False,
        reader_facing_mode=False,
        stages=stages,
        artifacts=artifacts,
    )

    assert list(gate_findings) == sorted(GATE_IDS)
    assert not _report_path(ws).exists()
    assert not _auditor_report_path(ws).exists()
    assert not (ws / "output" / "intermediate" / "event_log.jsonl").exists()

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    report_finding_types = {
        gate_id: [finding["finding_type"] for finding in report["findings"] if finding["gate_id"] == gate_id]
        for gate_id in sorted(GATE_IDS)
    }
    helper_finding_types = {
        gate_id: [finding["finding_type"] for finding in gate_findings[gate_id]]
        for gate_id in sorted(GATE_IDS)
    }
    assert helper_finding_types == report_finding_types


def test_parallel_quality_gate_findings_match_serial(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo update.\n\n## Detail\nRevenue was $42 million.\n",
    )
    _repo, stages, artifacts = quality_gate_state._contracts(workspace=ws, repo_workdir=ROOT)
    ledger = quality_gate_state._load_ledger(_intermediate(ws) / "claim_ledger.json", required=True)
    kwargs = {
        "markdown": (_intermediate(ws) / "audited_brief.md").read_text(encoding="utf-8"),
        "ledger": ledger,
        "config": quality_gate_state._load_config(ws),
        "user_text": (ws / "user.md").read_text(encoding="utf-8"),
        "analyst_markdown": None,
        "report_date": "",
        "max_source_age_days": None,
        "strict": False,
        "reader_facing_mode": False,
        "stages": stages,
        "artifacts": artifacts,
    }

    serial = quality_gate_state.evaluate_quality_gate_findings(**kwargs)
    parallel = quality_gate_state.evaluate_quality_gate_findings(**kwargs, parallel=True)

    assert list(parallel) == sorted(GATE_IDS)
    assert parallel == serial


def test_parallel_quality_gate_errors_wait_for_scheduled_gates(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(ws, "## Executive Summary\nTargetCo update.\n")
    _repo, stages, artifacts = quality_gate_state._contracts(workspace=ws, repo_workdir=ROOT)
    ledger = quality_gate_state._load_ledger(_intermediate(ws) / "claim_ledger.json", required=True)
    target_called = {"value": False}

    def boom(**_kwargs):
        raise ValueError("material boom")

    def target_relevance(**_kwargs):
        target_called["value"] = True
        return []

    monkeypatch.setattr(quality_gate_state, "_material_findings", boom)
    monkeypatch.setattr(quality_gate_state, "_target_relevance_findings", target_relevance)

    with pytest.raises(RuntimeStateError) as excinfo:
        quality_gate_state.evaluate_quality_gate_findings(
            markdown=(_intermediate(ws) / "audited_brief.md").read_text(encoding="utf-8"),
            ledger=ledger,
            config=quality_gate_state._load_config(ws),
            user_text=(ws / "user.md").read_text(encoding="utf-8"),
            analyst_markdown=None,
            report_date="",
            max_source_age_days=None,
            strict=False,
            reader_facing_mode=False,
            stages=stages,
            artifacts=artifacts,
            parallel=True,
        )

    assert excinfo.value.details["gate_errors"] == {"material_fact": "material boom"}
    assert target_called["value"] is True


def test_gates_check_writes_report_and_events_for_material_blocker(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo update.\n\n## Detail\nRevenue was $42 million.\n",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    report = payload["quality_gate_report"]
    assert report["status"] == "fail"
    assert _report_path(ws).exists()
    assert _auditor_report_path(ws).exists()
    assert not _finalize_report_path(ws).exists()
    findings = report["findings"]
    number_finding = next(finding for finding in findings if finding["finding_type"] == "number_without_source")
    assert number_finding["rule_summary"] == "Numbers in the brief must be tied to source-backed Claim Ledger support."
    assert number_finding["docs_anchor"] == "docs/agent-contract.md#number_without_source"
    assert any(finding["blocking_level"] == "blocking" for finding in findings)
    material_result = next(result for result in report["gate_results"] if result["gate_id"] == "material_fact")
    assert "Claim Ledger entries" in material_result["rule_summary"]
    assert material_result["docs_anchor"] == "docs/agent-contract.md#material_fact"
    event_types = [event["event_type"] for event in _events(ws)]
    assert event_types.count("quality_gate_checked") == 1
    assert event_types.count("quality_gate_blocked") == 1


def test_gates_check_repeated_same_report_preserves_stage_report_bytes(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo update.\n\n## Detail\nRevenue was $42 million.\n",
    )

    monkeypatch.setattr(quality_gate_state, "utc_now", lambda: "2026-06-18T00:00:00+00:00")
    first = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)
    assert first["quality_gate_report"]["status"] == "fail"
    report_bytes = _auditor_report_path(ws).read_bytes()
    legacy_bytes = _report_path(ws).read_bytes()
    event_count = len(_events(ws))

    monkeypatch.setattr(quality_gate_state, "utc_now", lambda: "2026-06-18T00:05:00+00:00")
    second = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    assert second["quality_gate_report"]["status"] == "fail"
    assert _auditor_report_path(ws).read_bytes() == report_bytes
    assert _report_path(ws).read_bytes() == legacy_bytes
    assert len(_events(ws)) == event_count


def test_gates_check_rejects_frozen_auditor_gate_report_without_mutation(tmp_path):
    ws = _prepare_editor_gate_workspace(
        tmp_path,
        analyst_text="## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
        editor_text="## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
    )
    (_intermediate(ws) / "audit_report.json").write_text(_valid_audit_report_payload(), encoding="utf-8")

    quality_gate_state.check_quality_gates(
        workspace=ws,
        repo_workdir=ROOT,
        report_date="2026-06-18",
        stage_id="auditor",
    )
    runtime_state.complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )
    report_bytes = _auditor_report_path(ws).read_bytes()
    event_count = len(_events(ws))

    with pytest.raises(RuntimeStateError) as excinfo:
        quality_gate_state.check_quality_gates(
            workspace=ws,
            repo_workdir=ROOT,
            report_date="2026-06-18",
            stage_id="auditor",
        )

    assert excinfo.value.error_code == "E_FROZEN_GATE_REPORT_ALREADY_EXISTS"
    assert "Stage-scoped gate report is already frozen" in str(excinfo.value)
    assert _auditor_report_path(ws).read_bytes() == report_bytes
    assert len(_events(ws)) == event_count
    state = runtime_state.show_runtime_state(workspace=ws)
    assert state["workflow_state"]["run_integrity"]["status"] == "clean"
    assert state["workflow_state"]["run_integrity"]["reference_eligible"] is True


def test_gates_check_finalize_stage_not_blocked_by_frozen_auditor_gate(tmp_path):
    ws = _prepare_editor_gate_workspace(
        tmp_path,
        analyst_text="## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
        editor_text="## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
    )
    (_intermediate(ws) / "audit_report.json").write_text(_valid_audit_report_payload(), encoding="utf-8")
    quality_gate_state.check_quality_gates(
        workspace=ws,
        repo_workdir=ROOT,
        report_date="2026-06-18",
        stage_id="auditor",
    )
    runtime_state.complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )
    _write_reader_brief(ws, "## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n")

    state = quality_gate_state.check_quality_gates(
        workspace=ws,
        repo_workdir=ROOT,
        report_date="2026-06-18",
        stage_id="finalize",
        brief=ws / "output" / "brief.md",
    )

    assert state["quality_gate_report"]["metadata"]["gate_artifact_id"] == "finalize_quality_gate_report"
    assert _finalize_report_path(ws).exists()


def test_gate_report_can_be_explicitly_ingested_as_audit_feedback(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo update.\n\n## Detail\nRevenue was $42 million.\n",
    )
    assert main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
    ]) == 0
    capsys.readouterr()
    assert not (ws / "output" / "intermediate" / "feedback_issues.json").exists()

    rc = main([
        "feedback",
        "ingest",
        "--workspace",
        str(ws),
        "--feedback",
        str(_report_path(ws)),
        "--source",
        "audit",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    issue = json.loads(capsys.readouterr().out)["feedback_issues"]["issues"][0]
    assert issue["source"] == "audit"
    assert issue["status"] == "open"
    assert issue["stage_id"] == "editor"
    assert issue["artifact_id"] == "audited_brief"
    assert issue["category"] == "unsupported_claim"
    assert issue["metadata"]["source_finding_id"].startswith("QG_")
    assert issue["metadata"]["raw_finding"]["gate_stage_id"] == "auditor"


def test_gates_show_and_validate_are_machine_readable(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(ws, "## Executive Summary\nTargetCo update.\n")
    assert main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
    ]) == 0
    capsys.readouterr()

    rc = main([
        "gates",
        "show",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    rc = main([
        "gates",
        "validate",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_gates_validate_rejects_unknown_stage_and_artifact_refs(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _report_path(ws).parent.mkdir(parents=True, exist_ok=True)
    _report_path(ws).write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-quality-gates/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "workspace": ".",
            "report_date": "",
            "policy_pack": "default",
            "status": "fail",
            "gate_results": [
                {
                    "gate_id": "material_fact",
                    "status": "fail",
                    "blocking": True,
                    "finding_ids": ["QG_BAD_001"],
                }
            ],
            "findings": [
                {
                    "finding_id": "QG_BAD_001",
                    "gate_id": "material_fact",
                    "finding_type": "unsupported_material_fact",
                    "severity": "high",
                    "blocking_level": "blocking",
                    "blocking": True,
                    "repair_owner": "analyst",
                    "stage_id": "future-stage",
                    "artifact_id": "future_artifact",
                    "claim_id": None,
                    "source_id": None,
                    "line_number": None,
                    "description": "Bad refs.",
                    "recommendation": "Fix refs.",
                    "evidence_ref": "",
                    "metadata": {},
                }
            ],
            "metadata": {},
        }),
        encoding="utf-8",
    )

    rc = main([
        "gates",
        "validate",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert any("stage_id is unknown" in error for error in result["errors"])
    assert any("artifact_id is unknown" in error for error in result["errors"])


def test_explicit_reader_brief_skips_source_reference_material_gate(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [
        {
            "claim_id": "TARGET_ABCDEF",
            "statement": "TargetCo revenue was $42 million.",
            "source_id": "SRC",
            "evidence_text": "TargetCo revenue was $42 million.",
            "metadata": {"importance": "high"},
        }
    ])
    _write_reader_brief(
        ws,
        "## Executive Summary\nTargetCo revenue was $42 million.\n",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--brief",
        "output/brief.md",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    finding_types = {finding["finding_type"] for finding in report["findings"]}
    assert "number_without_source" not in finding_types
    assert "target_priority_claim_missing_from_summary" not in finding_types
    assert report["metadata"]["gate_stage_id"] == "finalize"
    assert report["metadata"]["gate_artifact_id"] == "finalize_quality_gate_report"
    assert _finalize_report_path(ws).exists()


def test_delivery_reader_brief_skips_internal_citation_material_gate(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [
        {
            "claim_id": "CL-0001",
            "statement": "TargetCo revenue was $42 million.",
            "source_id": "SRC",
            "evidence_text": "TargetCo revenue was $42 million.",
            "metadata": {"importance": "high"},
        }
    ])
    _write_delivery_brief(
        ws,
        "## Executive Summary\nTargetCo revenue was $42 million. [S1]\n\n"
        "## Source Appendix\n### [S1] TargetCo Filing\n",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--brief",
        "output/delivery/brief.md",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    finding_types = {finding["finding_type"] for finding in report["findings"]}
    assert "number_without_source" not in finding_types
    assert report["metadata"]["reader_facing_mode"] is True
    assert report["metadata"]["gate_stage_id"] == "finalize"
    assert report["metadata"]["brief"] == "output/delivery/brief.md"


def test_auditable_brief_hyphenated_target_claim_ref_counts_for_summary(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [
        {
            "claim_id": "CLM-001",
            "statement": "TargetCo revenue was $42 million.",
            "source_id": "SRC",
            "evidence_text": "TargetCo revenue was $42 million.",
            "metadata": {"importance": "high"},
        }
    ])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo revenue was $42 million. [src:CLM-001]\n",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    finding_types = {finding["finding_type"] for finding in report["findings"]}
    assert "target_priority_claim_missing_from_summary" not in finding_types
    assert "number_without_source" not in finding_types


def test_gates_check_accepts_cwd_relative_workspace_prefixed_paths(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    work = repo / "work"
    work.mkdir(parents=True)
    ws = _write_workspace(work)
    _write_ledger(ws, [
        {
            "claim_id": "CLM-001",
            "statement": "TargetCo revenue was $42 million.",
            "source_id": "SRC",
            "evidence_text": "TargetCo revenue was $42 million.",
            "metadata": {"importance": "high"},
        }
    ])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo revenue was $42 million. [src:CLM-001]\n",
    )
    monkeypatch.chdir(repo)

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws.relative_to(repo)),
        "--brief",
        str((ws / "output" / "intermediate" / "audited_brief.md").relative_to(repo)),
        "--ledger",
        str((ws / "output" / "intermediate" / "claim_ledger.json").relative_to(repo)),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    assert report["metadata"]["brief"] == "output/intermediate/audited_brief.md"
    assert report["metadata"]["ledger"] == "output/intermediate/claim_ledger.json"
    finding_types = {finding["finding_type"] for finding in report["findings"]}
    assert "number_without_source" not in finding_types


def test_reader_brief_missing_target_blocks_finalize_stage(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(ws, "## Executive Summary\nTargetCo update.\n")
    _advance_to_auditor(ws)
    _write_json(ws, "audit_report.json", "{}\n")
    _set_current_stage(ws, "finalize")
    _write_reader_brief(ws, "## Executive Summary\nMarket update without the configured company.\n")

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--brief",
        "output/brief.md",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    gap = next(finding for finding in report["findings"] if finding["finding_type"] == "target_relevance_gap")
    assert gap["gate_stage_id"] == "finalize"
    assert gap["gate_artifact_id"] == "finalize_quality_gate_report"
    assert gap["stage_id"] == "editor"

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert state["workflow_state"]["current_stage"] == "finalize"
    assert state["workflow_state"]["blocked"] is True
    assert "blocking quality gate findings" in state["workflow_state"]["blocking_reason"]

    rc = main([
        "state",
        "decide",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "finalize",
        "--decision",
        "finalize",
        "--reason",
        "ship",
        "--json",
    ])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert payload["details"]["required_command"] == "finalize-complete"


def test_freshness_reads_config_report_defaults_and_preserves_zero(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    (ws / "config.yaml").write_text(
        """
project:
  name: "TargetCo"
report:
  date: "2026-06-08"
  max_source_age_days: 0
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_ledger(ws, [
        {
            "claim_id": "OLD_ABCDEF",
            "statement": "TargetCo announced a dated operating update.",
            "source_id": "OLD",
            "evidence_text": "TargetCo announced a dated operating update.",
            "metadata": {"published_at": "2026-06-07"},
        }
    ])
    _write_audited_brief(ws, "## Executive Summary\nTargetCo update. [src:OLD_ABCDEF]\n")

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    assert report["report_date"] == "2026-06-08"
    assert report["metadata"]["max_source_age_days"] == 0
    assert any(finding["finding_type"] == "stale_source" for finding in report["findings"])


def test_quality_gates_enabled_blocks_required_stage_when_report_missing(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    (ws / "config.yaml").write_text(
        """
project:
  name: "TargetCo"
quality_gates:
  enabled: true
  required_stages:
    - auditor
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_ledger(ws, [])
    _write_audited_brief(ws, "## Executive Summary\nTargetCo update.\n")
    _advance_to_auditor(ws)
    _write_json(ws, "audit_report.json", "{}\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["workflow_state"]["current_stage"] == "auditor"
    assert state["workflow_state"]["blocked"] is True
    assert "requires output/intermediate/gates/auditor_quality_gate_report.json" in state["workflow_state"]["blocking_reason"]

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
        "skip missing gate",
        "--json",
    ])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert payload["details"]["required_command"] == "stage-complete"


def test_gates_validate_json_rolls_up_auditor_scoped_status(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _auditor_report_path(ws).parent.mkdir(parents=True, exist_ok=True)
    _auditor_report_path(ws).write_text(
        json.dumps(_quality_gate_payload(status="fail", stage_id="auditor")),
        encoding="utf-8",
    )

    rc = main(["gates", "validate", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "fail"
    assert payload["statuses"] == {"auditor_quality_gate_report": "fail"}


def test_gates_validate_json_rolls_up_finalize_scoped_status(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _finalize_report_path(ws).parent.mkdir(parents=True, exist_ok=True)
    _finalize_report_path(ws).write_text(
        json.dumps(_quality_gate_payload(status="warning", stage_id="finalize")),
        encoding="utf-8",
    )

    rc = main(["gates", "validate", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"
    assert payload["statuses"] == {"finalize_quality_gate_report": "warning"}


def test_gates_validate_json_rolls_up_both_scoped_statuses(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _auditor_report_path(ws).parent.mkdir(parents=True, exist_ok=True)
    _auditor_report_path(ws).write_text(
        json.dumps(_quality_gate_payload(status="pass", stage_id="auditor")),
        encoding="utf-8",
    )
    _finalize_report_path(ws).write_text(
        json.dumps(_quality_gate_payload(status="fail", stage_id="finalize")),
        encoding="utf-8",
    )

    rc = main(["gates", "validate", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "fail"
    assert payload["statuses"] == {
        "auditor_quality_gate_report": "pass",
        "finalize_quality_gate_report": "fail",
    }


def test_gates_check_rolls_back_report_when_event_append_fails(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(ws, "## Executive Summary\nTargetCo update.\n")

    def fail_append_event(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(quality_gate_state, "append_event", fail_append_event)

    with pytest.raises(RuntimeStateError, match="event append failed"):
        quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    assert not _report_path(ws).exists()


def test_strict_freshness_gate_blocks_stale_source(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [
        {
            "claim_id": "OLD_ABCDEF",
            "statement": "TargetCo announced a dated operating update.",
            "source_id": "OLD",
            "evidence_text": "TargetCo announced a dated operating update.",
            "metadata": {"published_at": "2026-03-01"},
        }
    ])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo update. [src:OLD_ABCDEF]\n",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--report-date",
        "2026-06-08",
        "--max-source-age-days",
        "14",
        "--strict",
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    stale = [finding for finding in report["findings"] if finding["finding_type"] == "stale_source"]
    assert stale
    assert stale[0]["blocking_level"] == "blocking"


def test_quality_gate_blocker_enforced_by_state_check_and_decide(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(ws, [])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo update.\n\n## Detail\nRevenue was $42 million.\n",
    )
    _advance_to_auditor(ws)
    _auditor_report_path(ws).parent.mkdir(parents=True, exist_ok=True)
    _auditor_report_path(ws).write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-quality-gates/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "workspace": ".",
            "report_date": "",
            "policy_pack": "default",
            "status": "fail",
            "gate_results": [
                {
                    "gate_id": "material_fact",
                    "status": "fail",
                    "blocking": True,
                    "finding_ids": ["QG_AUDITOR_001"],
                }
            ],
            "findings": [
                {
                    "finding_id": "QG_AUDITOR_001",
                    "gate_id": "material_fact",
                    "finding_type": "unsupported_material_fact",
                    "category": "unsupported_claim",
                    "severity": "high",
                    "blocking_level": "blocking",
                    "blocking": True,
                    "repair_owner": "auditor",
                    "stage_id": "auditor",
                        "artifact_id": "audit_report",
                        "gate_artifact_id": "auditor_quality_gate_report",
                    "claim_id": None,
                    "source_id": None,
                    "line_number": None,
                    "description": "Auditor gate blocker.",
                    "recommendation": "Resolve before auditor continues.",
                    "summary": "Auditor gate blocker.",
                    "evidence_ref": "",
                    "metadata": {},
                }
            ],
            "metadata": {},
        }),
        encoding="utf-8",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow = state["workflow_state"]
    assert workflow["current_stage"] == "auditor"
    assert workflow["blocked"] is True
    assert "blocking quality gate findings" in workflow["blocking_reason"]

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
        "skip quality gates",
        "--json",
    ])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert payload["details"]["required_command"] == "stage-complete"


def test_editor_new_fact_gate_warns_when_editor_adds_number(tmp_path, capsys):
    ws = _prepare_editor_gate_workspace(
        tmp_path,
        analyst_text="## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
        editor_text="## Executive Summary\nTargetCo opened a demo facility and reported 42 deployments. [src:CL-001]\n",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    editor_result = next(result for result in report["gate_results"] if result["gate_id"] == "editor_new_fact")
    editor_finding = next(finding for finding in report["findings"] if finding["finding_type"] == "editor_introduced_new_fact")
    assert report["status"] == "warning"
    assert editor_result["status"] == "warning"
    assert editor_result["blocking"] is False
    assert editor_finding["blocking_level"] == "warning"
    assert editor_finding["repair_owner"] == "editor"
    assert editor_finding["metadata"]["introduced_numbers"] == ["42"]


def test_editor_new_fact_gate_allows_pure_restructuring(tmp_path, capsys):
    ws = _prepare_editor_gate_workspace(
        tmp_path,
        analyst_text=(
            "## Executive Summary\n"
            "- TargetCo opened a demo facility. [src:CL-001]\n"
            "- TargetCo reported 42 deployments. [src:CL-001]\n"
        ),
        editor_text=(
            "## Executive Summary\n"
            "- TargetCo reported 42 deployments. [src:CL-001]\n"
            "- TargetCo opened a demo facility. [src:CL-001]\n"
        ),
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    finding_types = {finding["finding_type"] for finding in report["findings"]}
    editor_result = next(result for result in report["gate_results"] if result["gate_id"] == "editor_new_fact")
    assert "editor_introduced_new_fact" not in finding_types
    assert editor_result["status"] == "pass"
    assert editor_result["blocking"] is False


def test_editor_new_fact_gate_allows_added_markdown_heading_in_strict_mode(tmp_path, capsys):
    ws = _prepare_editor_gate_workspace(
        tmp_path,
        analyst_text="TargetCo opened a demo facility. [src:CL-001]\nNo wording changes.\n",
        editor_text="## Market Update\nTargetCo opened a demo facility. [src:CL-001]\nNo wording changes.\n",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    finding_types = {finding["finding_type"] for finding in report["findings"]}
    editor_result = next(result for result in report["gate_results"] if result["gate_id"] == "editor_new_fact")
    assert "editor_introduced_new_fact" not in finding_types
    assert editor_result["status"] == "pass"
    assert editor_result["blocking"] is False


def test_editor_new_fact_gate_allows_declared_project_name_metadata(tmp_path, capsys):
    ws = _prepare_editor_gate_workspace(
        tmp_path,
        analyst_text="## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
        editor_text=(
            "## Executive Summary\n"
            "Solar Insights Media Weekly Brief: TargetCo opened a demo facility. [src:CL-001]\n"
        ),
    )
    config_path = ws / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'name: "TargetCo"',
            'name: "Solar Insights Media Weekly Brief"',
        ),
        encoding="utf-8",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    finding_types = {finding["finding_type"] for finding in report["findings"]}
    editor_result = next(result for result in report["gate_results"] if result["gate_id"] == "editor_new_fact")
    assert "editor_introduced_new_fact" not in finding_types
    assert editor_result["status"] == "pass"
    assert editor_result["blocking"] is False


def test_editor_new_fact_gate_blocks_added_entity_in_bullet_strict_mode(tmp_path, capsys):
    ws = _prepare_editor_gate_workspace(
        tmp_path,
        analyst_text="- TargetCo opened a demo facility. [src:CL-001]\n",
        editor_text=(
            "- TargetCo opened a demo facility. [src:CL-001]\n"
            "- NewCo Holdings opened a plant. [src:CL-001]\n"
        ),
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    editor_result = next(result for result in report["gate_results"] if result["gate_id"] == "editor_new_fact")
    editor_finding = next(finding for finding in report["findings"] if finding["finding_type"] == "editor_introduced_new_fact")
    assert report["status"] == "fail"
    assert editor_result["status"] == "fail"
    assert editor_result["blocking"] is True
    assert editor_finding["blocking_level"] == "blocking"
    assert "NewCo Holdings" in editor_finding["metadata"]["introduced_entities"]


def test_editor_new_fact_gate_blocks_in_strict_mode(tmp_path, capsys):
    ws = _prepare_editor_gate_workspace(
        tmp_path,
        analyst_text="## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
        editor_text="## Executive Summary\nTargetCo opened a demo facility and reported 42 deployments. [src:CL-001]\n",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    editor_result = next(result for result in report["gate_results"] if result["gate_id"] == "editor_new_fact")
    editor_finding = next(finding for finding in report["findings"] if finding["finding_type"] == "editor_introduced_new_fact")
    assert report["status"] == "fail"
    assert editor_result["status"] == "fail"
    assert editor_result["blocking"] is True
    assert editor_finding["blocking_level"] == "blocking"


def test_high_severity_warning_does_not_block_by_default(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _auditor_report_path(ws).parent.mkdir(parents=True, exist_ok=True)
    _auditor_report_path(ws).write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-quality-gates/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "workspace": ".",
            "report_date": "",
            "policy_pack": "default",
            "status": "warning",
            "gate_results": [
                {
                    "gate_id": "target_relevance",
                    "status": "warning",
                    "blocking": False,
                    "finding_ids": ["QG_WARN_001"],
                }
            ],
            "findings": [
                {
                    "finding_id": "QG_WARN_001",
                    "gate_id": "target_relevance",
                    "finding_type": "target_mapping_ambiguous",
                    "severity": "high",
                    "blocking_level": "warning",
                    "blocking": False,
                    "repair_owner": "human",
                    "stage_id": "doctor",
                    "artifact_id": "auditor_quality_gate_report",
                    "gate_artifact_id": "auditor_quality_gate_report",
                    "claim_id": None,
                    "source_id": None,
                    "line_number": None,
                    "description": "High severity but non-blocking.",
                    "recommendation": "Review.",
                    "evidence_ref": "",
                    "metadata": {},
                }
            ],
            "metadata": {},
        }),
        encoding="utf-8",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["workflow_state"]["current_stage"] == "doctor"
    assert state["workflow_state"]["blocked"] is False
    assert state["artifact_registry"]["artifacts"]["auditor_quality_gate_report"]["status"] == "valid"
