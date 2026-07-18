"""Tests for v0.6.3 deterministic quality gate controls."""

from __future__ import annotations

import json
import hashlib
import re
import shutil
from functools import partial
from pathlib import Path

import pytest
import yaml

import multi_agent_brief.orchestrator.runtime_state as runtime_state
from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    check_runtime_state,
    initialize_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import ARTIFACT_REGISTRY_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.claim_support_matrix import (
    project_claim_support_matrix_from_workspace,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import _allowed_decisions_for_stage
from multi_agent_brief.quality_gates import state as quality_gate_state
from multi_agent_brief.quality_gates.contract import (
    GATE_IDS,
    interpret_quality_gate_binding,
    quality_gate_report_path_for_stage,
    require_quality_gate_binding_pass,
)
from multi_agent_brief.repair import router as repair_router
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent


_write_workspace_files = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "TargetCo"
output:
  path: "output"
input:
  path: "input"
""".strip(),
    user_text="# User\nTarget: TargetCo\n",
    include_input_dir=True,
)


def _write_workspace(tmp_path: Path) -> Path:
    ws = _write_workspace_files(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, runtime="operator")
    return ws


def _write_uninitialized_workspace(tmp_path: Path) -> Path:
    return _write_workspace_files(tmp_path)


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


def _write_screened_candidates(
    ws: Path,
    *,
    selected: list[dict],
    excluded: list[dict] | None = None,
) -> None:
    universe = [*selected, *(excluded or [])]
    (_intermediate(ws) / "candidate_claims.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": item["candidate_id"],
                    "claim": item.get("statement") or item["candidate_id"],
                    "source_id": item.get("source_id") or f"SRC-{item['candidate_id']}",
                }
                for item in universe
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (_intermediate(ws) / "screened_candidates.json").write_text(
        json.dumps(
            {
                "selected": selected,
                "excluded": excluded if excluded is not None else [],
                "screening_policy": {
                    "max_items": 8,
                    "freshness_window_days": 90,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _selected_candidate(
    *,
    candidate_id: str = "CAND-001",
    statement: str = "TargetCo opened a high-priority demo facility.",
    priority: str = "high",
    **extra: object,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "statement": statement,
        "evidence_text": statement,
        "source_id": "SRC-001",
        "published_at": "2026-06-01",
        "priority": priority,
        **extra,
    }


def _write_report_spec(ws: Path, *, policy_profile: str) -> None:
    (ws / "report_spec.yaml").write_text(
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


def _write_atomic_graph(ws: Path, *, claim_id: str = "CL-001") -> None:
    (_intermediate(ws) / "atomic_claim_graph.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.atomic_claim_graph.v1",
                "claims": [
                    {
                        "claim_id": claim_id,
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
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _span_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_claim_support_matrix_fixture(
    ws: Path,
    *,
    support_label: str,
    support_strength: str = "none",
    required_action: str = "block_release",
    repair_owner: str = "editor",
    evidence_span_id: str | None = None,
) -> None:
    raw_excerpt = "TargetCo opened a demo facility and reported 42 deployments."
    source_text = f"Intro.\n{raw_excerpt}\nOutro.\n"
    source_path = ws / "input" / "sources" / "source-001.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source_text, encoding="utf-8")
    start = source_text.index(raw_excerpt)
    _write_ledger(
        ws,
        [
            {
                "claim_id": "CL-0001",
                "statement": "TargetCo opened a demo facility and reported 42 deployments.",
                "source_id": "SRC-001",
                "evidence_text": raw_excerpt,
                "source_url": "https://example.com/targetco-demo",
                "source_type": "web_search",
                "claim_type": "fact",
                "metadata": {
                    "source_title": "TargetCo Demo Facility",
                    "publisher": "Example News",
                    "published_at": "2026-06-01",
                    "importance": "high",
                },
            }
        ],
    )
    intermediate = _intermediate(ws)
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
            },
            ensure_ascii=False,
            indent=2,
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
            },
            ensure_ascii=False,
            indent=2,
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
                        "evidence_span_id": evidence_span_id,
                        "support_label": support_label,
                        "support_strength": support_strength,
                        "support_reason": "Explicit support record for gate projection.",
                        "required_action": required_action,
                        "repair_owner": repair_owner,
                        "decision_source": "human",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_supported_strategic_ledger(ws: Path) -> None:
    _write_ledger(
        ws,
        [
            {
                "claim_id": "CL-001",
                "statement": "TargetCo opened a demo facility that may support early-mover demand.",
                "source_id": "SRC-001",
                "evidence_text": "TargetCo opened a demo facility that may support early-mover demand.",
                "source_url": "https://example.com/targetco-demo",
                "source_type": "web_search",
                "claim_type": "interpretation",
                "metadata": {
                    "source_title": "TargetCo Demo Facility",
                    "publisher": "Example News",
                    "published_at": "2026-06-01",
                },
            }
        ],
    )


def _prepare_editor_gate_workspace(tmp_path: Path, *, analyst_text: str, editor_text: str) -> Path:
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
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
            for gate_id in ("coverage_omission", "freshness", "material_fact", "target_relevance")
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


def _mark_active_repair(ws: Path) -> None:
    paths = runtime_state.runtime_state_paths(ws)
    workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    workflow["active_repair"] = {
        "schema_version": "mabw.active_repair.v1",
        "transaction_id": "repair-test-001",
        "repair_owner": "editor",
        "allowed_artifacts": ["output/intermediate/audited_brief.md"],
        "blocked_direct_edits": ["output/intermediate/claim_ledger.json"],
        "must_rerun_from": "auditor",
    }
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def test_quality_gate_binding_interpreter_requires_coverage_omission_for_stage_completion(tmp_path):
    ws = _write_workspace(tmp_path)
    report_path = quality_gate_report_path_for_stage(ws, "auditor")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _quality_gate_payload(status="pass", stage_id="auditor")
    payload["gate_results"] = [
        item for item in payload["gate_results"] if item["gate_id"] != "coverage_omission"
    ]
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
    assert any("coverage_omission" in reason for reason in require_quality_gate_binding_pass(verdict))


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
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _write_json(ws, "candidate_claims.json")
    _write_json(ws, "screened_candidates.json")
    if not (_intermediate(ws) / "claim_ledger.json").exists():
        _write_ledger(ws, [])
    if not (_intermediate(ws) / "audited_brief.md").exists():
        _write_audited_brief(ws, "# Brief\n")
    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "auditor")


def _write_json(ws: Path, name: str, payload: str = "[]\n") -> None:
    (_intermediate(ws) / name).write_text(payload, encoding="utf-8")


def _write_minimal_artifact_registry(ws: Path) -> None:
    path = runtime_state.runtime_state_paths(ws)["artifact_registry"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": ARTIFACT_REGISTRY_SCHEMA,
                "artifacts": {},
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_stage_gate_report(
    ws: Path,
    *,
    stage_id: str,
    finding: dict[str, object],
) -> None:
    if not runtime_state.runtime_state_paths(ws)["artifact_registry"].exists():
        _write_minimal_artifact_registry(ws)
    artifact_id = "finalize_quality_gate_report" if stage_id == "finalize" else "auditor_quality_gate_report"
    report_path = _finalize_report_path(ws) if stage_id == "finalize" else _auditor_report_path(ws)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "created_at": "2026-07-07T00:00:00+00:00",
                "updated_at": "2026-07-07T00:00:00+00:00",
                "workspace": ".",
                "report_date": "2026-07-07",
                "policy_pack": "default",
                "status": "fail",
                "gate_results": [
                    {
                        "gate_id": gate_id,
                        "status": "fail" if gate_id == "target_relevance" else "pass",
                        "blocking": gate_id == "target_relevance",
                        "finding_ids": [str(finding["finding_id"])] if gate_id == "target_relevance" else [],
                    }
                    for gate_id in ("coverage_omission", "freshness", "material_fact", "target_relevance")
                ],
                "findings": [finding],
                "metadata": {
                    "brief": "output/brief.md" if stage_id == "finalize" else "output/intermediate/audited_brief.md",
                    "ledger": "output/intermediate/claim_ledger.json",
                    "stage_id": stage_id,
                    "gate_stage_id": stage_id,
                    "gate_artifact_id": artifact_id,
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


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
    assert blocker["metadata"]["requires_content_edit"] is True
    assert blocker["metadata"]["owner_stage"] == "editor"
    assert blocker["metadata"]["post_freeze_action"] == "open_editor_repair"
    assert blocker["metadata"]["delivery_effect"] == "blocks_until_repaired"
    assert payload["repair_route"]["repair_owner"] == "editor"
    assert payload["repair_route"]["route_kind"] == "owner_stage_repair"
    assert payload["repair_route"]["must_rerun_from"] == "auditor"
    assert payload["repair_route"]["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert "output/intermediate/audit_report.json" in payload["repair_route"]["blocked_direct_edits"]
    assert payload["required_commands"] == [
        (
            f"briefloop repair start --workspace {ws.resolve()} "
            "--gate-stage auditor --gate-artifact auditor_quality_gate_report --json"
        ),
        f"briefloop repair complete --workspace {ws.resolve()} --reason \"<reason>\" --json",
    ]
    assert payload["repair_steps"] == [
        "Current gate has an owner-stage repair route. Scoped repair start is handled by the repair transaction.",
        "Delegate only the reported repair_owner role.",
        "Allow edits only to repair_route.allowed_artifacts.",
        "Run repair complete after the owner edits.",
        "Rerun downstream stages from repair_route.must_rerun_from.",
    ]
    assert f"briefloop repair route --workspace {ws.resolve()} --json" not in payload["required_commands"]
    assert payload["repair_warnings"] == [
        "Do not edit frozen artifacts directly.",
        "Direct edits will mark the run contaminated and non-reference-eligible.",
        "Never manually update artifact_registry.json, runtime_manifest.json, workflow_state.json, event_log.jsonl, or SHA fields.",
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
    assert (
        f"briefloop repair start --workspace {ws.resolve()} "
        "--gate-stage auditor --gate-artifact auditor_quality_gate_report --json"
    ) in output
    assert f"briefloop repair start --workspace {ws.resolve()} --json" not in output
    assert "[gates show] repair_warnings:" in output
    assert "Do not edit frozen artifacts directly." in output
    assert "Direct edits will mark the run contaminated and non-reference-eligible." in output
    assert "Never manually update artifact_registry.json" in output
    assert "[gates show] repair_steps:" in output
    assert "Delegate only the reported repair_owner role." in output
    assert "Allow edits only to repair_route.allowed_artifacts." in output
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


@pytest.mark.parametrize("repair_owner", ["human", "human_review", "human-review"])
def test_quality_gate_guidance_does_not_start_human_review_route(tmp_path, repair_owner):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _write_minimal_artifact_registry(ws)
    _set_current_stage(ws, "auditor")
    _auditor_report_path(ws).parent.mkdir(parents=True, exist_ok=True)
    _auditor_report_path(ws).write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "created_at": "2026-07-07T00:00:00+00:00",
                "updated_at": "2026-07-07T00:00:00+00:00",
                "workspace": ".",
                "report_date": "2026-07-07",
                "policy_pack": "default",
                "status": "fail",
                "gate_results": [
                    {
                        "gate_id": gate_id,
                        "status": "fail" if gate_id == "target_relevance" else "pass",
                        "blocking": gate_id == "target_relevance",
                        "finding_ids": ["QG_HUMAN_001"] if gate_id == "target_relevance" else [],
                    }
                    for gate_id in ("coverage_omission", "freshness", "material_fact", "target_relevance")
                ],
                "findings": [
                    {
                        "finding_id": "QG_HUMAN_001",
                        "finding_type": "target_mapping_ambiguous",
                        "severity": "high",
                        "blocking_level": "blocking",
                        "blocking": True,
                        "artifact_id": "audited_brief",
                        "repair_owner": repair_owner,
                        "repair_stage_id": "editor",
                        "repair_artifact_id": "audited_brief",
                        "message": "Target mapping is ambiguous and requires human review.",
                    },
                    {
                        "finding_id": "QG_WARN_REPAIR_001",
                        "finding_type": "unsupported_claim",
                        "severity": "medium",
                        "blocking_level": "warning",
                        "blocking": False,
                        "artifact_id": "audited_brief",
                        "repair_owner": "editor",
                        "repair_stage_id": "editor",
                        "repair_artifact_id": "audited_brief",
                        "message": "Warning-only editor finding should not outrank the blocker.",
                    },
                ],
                "metadata": {
                    "brief": "output/intermediate/audited_brief.md",
                    "ledger": "output/intermediate/claim_ledger.json",
                    "stage_id": "auditor",
                    "gate_stage_id": "auditor",
                    "gate_artifact_id": "auditor_quality_gate_report",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    guidance = quality_gate_state._blocking_repair_guidance(
        workspace=ws.resolve(),
        validation={"blocking_count": 1},
        repo_workdir=ROOT,
    )

    assert guidance["repair_route"]["route_kind"] == "human_review"
    assert guidance["repair_route"]["repair_owner"] == "none"
    assert guidance["repair_route"]["recommended_action"] == "request_human_review_for_blocking_gate"
    commands = guidance["required_commands"]
    assert f"briefloop repair route --workspace {ws.resolve()} --json" not in commands
    assert not any(" repair start " in command for command in commands)
    assert not any(" repair complete " in command for command in commands)
    assert any(" request_human_review " in command for command in commands)
    assert any(" block_run " in command for command in commands)
    assert guidance["repair_steps"] == [
        "This blocking gate requires human review before deterministic repair can proceed.",
        "Use request_human_review or block_run instead of starting owner-stage repair.",
    ]


def test_quality_gate_guidance_uses_current_gate_route_for_scoped_start(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "finalize")
    _write_stage_gate_report(
        ws,
        stage_id="auditor",
        finding={
            "finding_id": "QG_STALE_HUMAN_001",
            "finding_type": "target_mapping_ambiguous",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "artifact_id": "audited_brief",
            "repair_owner": "human",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Stale auditor finding requires human review.",
        },
    )
    _write_stage_gate_report(
        ws,
        stage_id="finalize",
        finding={
            "finding_id": "QG_CURRENT_EDITOR_001",
            "finding_type": "target_relevance_gap",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Current finalize gate needs editor repair.",
        },
    )

    guidance = quality_gate_state._blocking_repair_guidance(
        workspace=ws.resolve(),
        validation={"blocking_count": 1},
        repo_workdir=ROOT,
    )

    assert guidance["repair_route"]["route_kind"] == "owner_stage_repair"
    assert guidance["repair_route"]["source"]["kind"] == "finalize_quality_gate_report"
    assert guidance["repair_route"]["source"]["finding_id"] == "QG_CURRENT_EDITOR_001"
    commands = guidance["required_commands"]
    assert f"briefloop repair route --workspace {ws.resolve()} --json" not in commands
    assert any(
        command
        == (
            f"briefloop repair start --workspace {ws.resolve()} "
            "--gate-stage finalize --gate-artifact finalize_quality_gate_report --json"
        )
        for command in commands
    )
    assert f"briefloop repair start --workspace {ws.resolve()} --json" not in commands


def test_quality_gate_guidance_does_not_route_stale_downstream_blocker(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "auditor")
    _write_stage_gate_report(
        ws,
        stage_id="finalize",
        finding={
            "finding_id": "QG_STALE_FINALIZE_001",
            "finding_type": "target_relevance_gap",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Stale finalize gate should not route current auditor repair.",
        },
    )
    _report_path(ws).write_text(_finalize_report_path(ws).read_text(encoding="utf-8"), encoding="utf-8")

    guidance = quality_gate_state._blocking_repair_guidance(
        workspace=ws.resolve(),
        validation={"blocking_count": 1, "statuses": {"finalize_quality_gate_report": "fail"}},
        repo_workdir=ROOT,
    )

    assert guidance["repair_route"]["route_kind"] == "none"
    assert guidance["repair_route"]["repair_owner"] == "none"
    assert guidance["repair_route"]["recommended_action"] == ""
    assert guidance["required_commands"] == []
    assert not any(" repair start " in command for command in guidance["required_commands"])
    assert not any(" request_human_review " in command for command in guidance["required_commands"])
    assert not any(" block_run " in command for command in guidance["required_commands"])
    assert guidance["repair_steps"] == [
        "Blocking quality-gate reports exist outside the current workflow stage.",
        "Do not start repair from stale downstream reports.",
        "Rerun the current or downstream gates, or inspect stage_quality_gate_reports to locate the blocking report.",
    ]


def test_quality_gate_guidance_does_not_scope_non_gate_current_stage(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "editor")
    _write_stage_gate_report(
        ws,
        stage_id="auditor",
        finding={
            "finding_id": "QG_STALE_AUDITOR_001",
            "finding_type": "unsupported_claim",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Stale auditor gate must not become editor scoped guidance.",
        },
    )

    def fail_route_for_gate(**kwargs):
        raise AssertionError("route_repair_for_gate should not be called for non-gate current stages")

    monkeypatch.setattr(repair_router, "route_repair_for_gate", fail_route_for_gate)

    guidance = quality_gate_state._blocking_repair_guidance(
        workspace=ws.resolve(),
        validation={"blocking_count": 1, "statuses": {"auditor_quality_gate_report": "fail"}},
        repo_workdir=ROOT,
    )

    assert guidance["repair_route"]["route_kind"] == "none"
    assert guidance["repair_route"]["repair_owner"] == "none"
    assert guidance["repair_route"]["source"] == {"stage_id": "editor", "kind": ""}
    assert guidance["required_commands"] == []
    assert not any(" repair start " in command for command in guidance["required_commands"])
    assert not any(" request_human_review " in command for command in guidance["required_commands"])
    assert not any(" block_run " in command for command in guidance["required_commands"])
    assert guidance["repair_steps"] == [
        "Blocking quality-gate reports exist outside the current workflow stage.",
        "Do not start repair from stale downstream reports.",
        "Rerun the current or downstream gates, or inspect stage_quality_gate_reports to locate the blocking report.",
    ]


def test_quality_gate_guidance_materializes_legacy_current_gate_blocker(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "auditor")
    _report_path(ws).write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "created_at": "2026-07-07T00:00:00+00:00",
                "updated_at": "2026-07-07T00:00:00+00:00",
                "workspace": ".",
                "report_date": "2026-07-07",
                "policy_pack": "default",
                "status": "fail",
                "gate_results": [
                    {
                        "gate_id": gate_id,
                        "status": "fail" if gate_id == "target_relevance" else "pass",
                        "blocking": gate_id == "target_relevance",
                        "finding_ids": ["QG_LEGACY_001"] if gate_id == "target_relevance" else [],
                    }
                    for gate_id in ("coverage_omission", "freshness", "material_fact", "target_relevance")
                ],
                "findings": [
                    {
                        "finding_id": "QG_LEGACY_001",
                        "finding_type": "target_relevance_gap",
                        "severity": "high",
                        "blocking_level": "blocking",
                        "blocking": True,
                        "artifact_id": "audited_brief",
                        "repair_owner": "editor",
                        "repair_stage_id": "editor",
                        "repair_artifact_id": "audited_brief",
                        "message": "Legacy projection blocker needs stage-scoped materialization.",
                    }
                ],
                "metadata": {
                    "brief": "output/intermediate/audited_brief.md",
                    "ledger": "output/intermediate/claim_ledger.json",
                    "stage_id": "auditor",
                    "gate_stage_id": "auditor",
                    "gate_artifact_id": "auditor_quality_gate_report",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    guidance = quality_gate_state._blocking_repair_guidance(
        workspace=ws.resolve(),
        validation={"blocking_count": 1, "statuses": {"quality_gate_report": "fail"}},
        repo_workdir=ROOT,
    )

    commands = guidance["required_commands"]
    assert commands == [
        f"briefloop gates check --workspace {ws.resolve()} --stage auditor --json",
        f"briefloop gates show --workspace {ws.resolve()} --json",
    ]
    assert guidance["repair_route"]["route_kind"] == "none"
    assert guidance["repair_route"]["source"]["legacy_projection"] == "quality_gate_report"
    assert not any(" repair start " in command for command in commands)
    assert not any(" request_human_review " in command for command in commands)
    assert not any(" block_run " in command for command in commands)
    assert guidance["repair_steps"] == [
        "Legacy quality_gate_report.json has blocking findings, but no current-stage scoped gate report is available.",
        "Rerun gates check for workflow.current_stage to materialize a stage-scoped report.",
        "Then rerun gates show and follow required_commands.",
    ]


def test_quality_gate_guidance_uses_workflow_scope_for_scoped_start_command(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "finalize")
    _write_stage_gate_report(
        ws,
        stage_id="finalize",
        finding={
            "finding_id": "QG_CURRENT_EDITOR_001",
            "finding_type": "target_relevance_gap",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Current finalize gate needs editor repair.",
        },
    )
    seen: dict[str, object] = {}

    def fake_route_for_gate(**kwargs):
        seen.update(kwargs)
        return {
            "ok": True,
            "route_kind": "owner_stage_repair",
            "repair_owner": "editor",
            "allowed_artifacts": ["output/intermediate/audited_brief.md"],
            "must_rerun_from": "auditor",
            "blocked_direct_edits": [],
            "source": {
                "stage_id": "auditor",
                "kind": "auditor_quality_gate_report",
                "finding_id": "QG_STALE_AUDITOR_001",
            },
        }

    monkeypatch.setattr(repair_router, "route_repair_for_gate", fake_route_for_gate)

    guidance = quality_gate_state._blocking_repair_guidance(
        workspace=ws.resolve(),
        validation={"blocking_count": 1},
        repo_workdir=ROOT,
    )

    assert seen["gate_stage_id"] == "finalize"
    assert seen["gate_artifact_id"] == "finalize_quality_gate_report"
    commands = guidance["required_commands"]
    assert (
        f"briefloop repair start --workspace {ws.resolve()} "
        "--gate-stage finalize --gate-artifact finalize_quality_gate_report --json"
    ) in commands
    assert not any("--gate-stage auditor --gate-artifact auditor_quality_gate_report" in command for command in commands)


def test_runtime_repair_instructions_use_scoped_current_gate_start() -> None:
    instruction_files = [
        ROOT / "src/multi_agent_brief/orchestrator/handoff.py",
        ROOT / "src/multi_agent_brief/hermes/adapter.py",
        ROOT / "configs/agent_roles.yaml",
        ROOT / "scripts/generate_agent_configs.py",
        ROOT / ".agents/skills/orchestrator/SKILL.md",
    ]
    for directory in (
        ROOT / ".agents/skills",
        ROOT / ".agents/hermes-skills",
        ROOT / ".codex",
        ROOT / ".claude",
        ROOT / ".opencode",
        ROOT / "docs/agents",
        ROOT / "integrations/hermes-plugin",
        ROOT / "integrations/workbuddy",
    ):
        if directory.exists():
            instruction_files.extend(
                path
                for path in directory.rglob("*")
                if path.is_file() and path.suffix in {".md", ".toml", ".yaml", ".yml", ".py"}
            )
    instruction_files = sorted(set(instruction_files))
    bare_start = re.compile(
        r"(?:briefloop|multi-agent-brief)\s+repair\s+start\s+--workspace\s+"
        r"(?:<workspace>|\{workspace\}|\$ARGUMENTS)"
        r"(?![^`\n]*(?:--gate-stage|--finding-id|--route-index))"
    )
    combined_text = ""

    for path in instruction_files:
        text = path.read_text(encoding="utf-8")
        combined_text += f"\n# {path}\n{text}"
        offenders = [
            line
            for line in text.splitlines()
            if bare_start.search(line) and "do not use bare" not in line and "Do not use bare" not in line
        ]
        assert offenders == [], path

    assert "gates show --workspace" in combined_text
    assert "repair route --workspace" in combined_text
    assert "--gate-stage" in combined_text
    assert "--gate-artifact" in combined_text
    assert "--finding-id" in combined_text
    assert "--route-index" in combined_text
    assert "do not use unscoped repair start for current-gate blockers" in combined_text


def test_evaluate_quality_gate_findings_is_read_only_and_matches_report(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    event_log_before = event_log.read_bytes()
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
    assert event_log.read_bytes() == event_log_before

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


def test_gates_check_can_rerun_hashed_report_before_auditor_complete(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "auditor")
    _write_ledger(ws, [])
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo update.\n\n## Detail\nRevenue was $42 million.\n",
    )
    first = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT, stage_id="auditor")
    assert first["quality_gate_report"]["status"] == "fail"
    runtime_state.check_runtime_state(workspace=ws, repo_workdir=ROOT)
    state = runtime_state.show_runtime_state(workspace=ws)
    auditor_status = state["workflow_state"]["stage_statuses"]["auditor"]["status"]
    assert auditor_status in {"ready", "blocked"}
    report_record = state["artifact_registry"]["artifacts"]["auditor_quality_gate_report"]
    assert report_record["status"] == "valid"
    assert report_record["sha256"]
    report_bytes = _auditor_report_path(ws).read_bytes()

    second = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT, stage_id="auditor")

    assert second["quality_gate_report"]["status"] == "fail"
    assert _auditor_report_path(ws).read_bytes() == report_bytes


def test_gates_check_fails_without_writing_when_active_repair_open(tmp_path):
    ws = _prepare_editor_gate_workspace(
        tmp_path,
        analyst_text="## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
        editor_text="## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
    )
    _auditor_report_path(ws).parent.mkdir(parents=True, exist_ok=True)
    _auditor_report_path(ws).write_text("existing gate report\n", encoding="utf-8")
    _report_path(ws).write_text("existing legacy report\n", encoding="utf-8")
    report_bytes = _auditor_report_path(ws).read_bytes()
    legacy_bytes = _report_path(ws).read_bytes()
    event_bytes = (ws / "output" / "intermediate" / "event_log.jsonl").read_bytes()
    _mark_active_repair(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        quality_gate_state.check_quality_gates(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
        )

    assert excinfo.value.error_code == runtime_state.E_ACTIVE_REPAIR_OPEN
    assert "repair complete" in str(excinfo.value)
    assert _auditor_report_path(ws).read_bytes() == report_bytes
    assert _report_path(ws).read_bytes() == legacy_bytes
    assert (ws / "output" / "intermediate" / "event_log.jsonl").read_bytes() == event_bytes


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


def test_quality_gates_warn_on_unsupported_strategic_implication(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_supported_target_ledger(ws)
    _write_audited_brief(
        ws,
        (
            "## Executive Summary\n"
            "TargetCo opened a demo facility and this creates early-mover demand. [src:CL-001]\n"
        ),
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    findings = state["quality_gate_report"]["findings"]
    strategic = [
        finding
        for finding in findings
        if finding.get("finding_type") == "unsupported_strategic_implication"
    ]
    assert len(strategic) == 1
    assert strategic[0]["blocking_level"] == "warning"
    assert strategic[0]["blocking"] is False
    assert strategic[0]["category"] == "strategic_overreach"
    assert strategic[0]["metadata"]["support_check"] == "lexical_phrase_absent_from_claim_ledger"
    assert "requires_content_edit" not in strategic[0]["metadata"]
    assert "post_freeze_action" not in strategic[0]["metadata"]
    assert "delivery_effect" not in strategic[0]["metadata"]


def test_quality_gates_warn_when_strategic_implication_only_appears_in_limitations(tmp_path):
    ws = _write_workspace(tmp_path)
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
                "claim_type": "fact",
                "limitations": ["This evidence does not establish early-mover demand."],
                "metadata": {"published_at": "2026-06-01"},
            }
        ],
    )
    _write_audited_brief(
        ws,
        (
            "## Executive Summary\n"
            "TargetCo opened a demo facility and this creates early-mover demand. [src:CL-001]\n"
        ),
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    strategic = [
        finding
        for finding in state["quality_gate_report"]["findings"]
        if finding.get("finding_type") == "unsupported_strategic_implication"
    ]
    assert len(strategic) == 1
    assert strategic[0]["blocking_level"] == "warning"


def test_quality_gates_do_not_warn_when_strategic_implication_is_ledger_supported(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_supported_strategic_ledger(ws)
    _write_audited_brief(
        ws,
        (
            "## Executive Summary\n"
            "TargetCo opened a demo facility that may support early-mover demand. [src:CL-001]\n"
        ),
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    assert not [
        finding
        for finding in state["quality_gate_report"]["findings"]
        if finding.get("finding_type") == "unsupported_strategic_implication"
    ]


def test_final_abstract_quality_warns_without_blocking_under_strict(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    (ws / "config.yaml").write_text(
        (ws / "config.yaml").read_text(encoding="utf-8")
        + "\nreport:\n  cadence: weekly\n",
        encoding="utf-8",
    )
    _write_supported_target_ledger(ws)
    _write_audited_brief(
        ws,
        "# Monthly Market Brief\n\n"
        "## Executive Summary\n"
        "TargetCo opened a demo facility and reported 42 deployments. [src:CL-001]\n"
        "TargetCo is the dominant choice for municipal buyers.\n\n"
        "## Comparison\n"
        "TargetCo outperforms peers on deployment velocity. [src:CL-001]\n\n"
        "## Key Cases\n"
        "- Case: TargetCo demo facility\n",
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
    findings = [item for item in report["findings"] if item["gate_id"] == "final_abstract_quality"]
    finding_types = {item["finding_type"] for item in findings}
    assert report["status"] == "warning"
    assert finding_types == {
        "final_scope_title_mismatch",
        "final_missing_comparison_basis",
        "final_missing_limitation_section",
        "final_incomplete_key_case_fields",
        "final_unsupported_superlative",
    }
    assert all(item["blocking_level"] == "warning" for item in findings)
    assert all(item["blocking"] is False for item in findings)
    assert all(item["repair_owner"] == "none" for item in findings)
    assert all(item["repair_stage_id"] is None for item in findings)
    assert all(item["repair_artifact_id"] is None for item in findings)
    result = next(item for item in report["gate_results"] if item["gate_id"] == "final_abstract_quality")
    assert result["status"] == "warning"
    assert result["blocking"] is False
    assert "not a prose-quality score" in findings[0]["metadata"]["semantic_boundary"]
    assert findings[0]["metadata"]["repair_boundary"] == "advisory_non_routable"


def test_quality_gate_report_includes_atomic_reader_projection_metadata(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_supported_target_ledger(ws)
    _write_atomic_graph(ws)
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    projection = state["quality_gate_report"]["metadata"]["atomic_reader_projection"]
    assert projection["status"] == "pass"
    assert projection["claim_citation_coverage"]["cited_graph_claim_ids"] == ["CL-001"]
    assert projection["summary_counts"]["graph_atom_count"] == 1


def test_quality_gate_atom_residue_is_warning_only(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_supported_target_ledger(ws)
    _write_atomic_graph(ws)
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. AC-0001-01 [src:CL-001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    atom_findings = [
        finding for finding in report["findings"] if finding.get("finding_type") == "atomic_atom_id_residue"
    ]
    assert report["status"] == "warning"
    assert len(atom_findings) == 1
    assert atom_findings[0]["blocking_level"] == "warning"
    assert atom_findings[0]["blocking"] is False
    assert atom_findings[0]["metadata"]["semantic_boundary"] == "deterministic_id_and_citation_projection_only"


def test_coverage_omission_warns_when_high_priority_selected_candidate_missing_from_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_supported_target_ledger(ws)
    _write_screened_candidates(
        ws,
        selected=[
            _selected_candidate(
                candidate_id="CAND-999",
                statement="TargetCo disclosed a high-priority omitted item.",
            )
        ],
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility and reported 42 deployments. [src:CL-001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    projection = report["metadata"]["coverage_omission_projection"]
    findings = [item for item in report["findings"] if item["finding_type"] == "selected_candidate_missing_from_ledger"]
    assert projection["status"] == "checked"
    assert projection["high_priority_selected_count"] == 1
    assert projection["missing_from_ledger_count"] == 1
    assert len(findings) == 1
    assert findings[0]["gate_id"] == "coverage_omission"
    assert findings[0]["blocking_level"] == "warning"
    assert "not full-world recall" in findings[0]["metadata"]["semantic_boundary"]


def test_coverage_omission_consumes_normalized_intake_view(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _write_supported_target_ledger(ws)
    (_intermediate(ws) / "candidate_claims.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "CAND-999",
                    "claim": "TargetCo disclosed a high-priority omitted item.",
                    "source_id": "SRC-999",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (_intermediate(ws) / "screened_candidates.json").write_text(
        json.dumps(
            {
                "selected_candidates": [
                    {
                        "candidate_id": "CAND-999",
                        "claim_statement": "TargetCo disclosed a high-priority omitted item.",
                        "source_excerpt": "TargetCo disclosed the omitted item.",
                        "source_id": "SRC-999",
                        "published_at": "2026-06-01",
                        "priority": "high",
                    }
                ],
                "excluded_candidates": [],
                "screening_policy": {"total_candidates": 1},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility and reported 42 deployments. [src:CL-001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    projection = state["quality_gate_report"]["metadata"]["coverage_omission_projection"]
    assert projection["status"] == "checked"
    assert projection["high_priority_selected_count"] == 1
    assert projection["missing_from_ledger_count"] == 1


def test_coverage_omission_blocks_in_strict_mode(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_supported_target_ledger(ws)
    _write_screened_candidates(
        ws,
        selected=[
            _selected_candidate(
                candidate_id="CAND-999",
                statement="TargetCo disclosed a high-priority omitted item.",
            )
        ],
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility and reported 42 deployments. [src:CL-001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT, strict=True)

    findings = [item for item in state["quality_gate_report"]["findings"] if item["gate_id"] == "coverage_omission"]
    assert findings[0]["blocking_level"] == "blocking"


def test_coverage_omission_warns_when_selected_ledger_claim_is_not_cited(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_ledger(
        ws,
        [
            {
                "claim_id": "CL-001",
                "statement": "TargetCo opened a high-priority demo facility.",
                "source_id": "SRC-001",
                "evidence_text": "TargetCo opened a high-priority demo facility.",
                "source_url": "https://example.com/targetco-demo",
                "source_type": "web_search",
                "metadata": {
                    "candidate_id": "CAND-001",
                    "source_title": "TargetCo Demo Facility",
                    "published_at": "2026-06-01",
                    "importance": "high",
                },
            }
        ],
    )
    _write_screened_candidates(ws, selected=[_selected_candidate()])
    _write_audited_brief(ws, "## Executive Summary\nTargetCo update.\n")

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    projection = report["metadata"]["coverage_omission_projection"]
    findings = [item for item in report["findings"] if item["finding_type"] == "selected_candidate_missing_from_brief"]
    assert projection["missing_from_brief_count"] == 1
    assert len(findings) == 1
    assert findings[0]["claim_id"] == "CL-001"


def test_coverage_omission_does_not_require_internal_claim_refs_in_reader_brief(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_ledger(
        ws,
        [
            {
                "claim_id": "CL-001",
                "statement": "TargetCo opened a high-priority demo facility.",
                "source_id": "SRC-001",
                "evidence_text": "TargetCo opened a high-priority demo facility.",
                "source_url": "https://example.com/targetco-demo",
                "source_type": "web_search",
                "metadata": {
                    "candidate_id": "CAND-001",
                    "source_title": "TargetCo Demo Facility",
                    "published_at": "2026-06-01",
                    "importance": "high",
                },
            }
        ],
    )
    _write_screened_candidates(ws, selected=[_selected_candidate()])
    _write_reader_brief(
        ws,
        "## Executive Summary\n"
        "TargetCo opened a high-priority demo facility. [S1]\n\n"
        "## Source Appendix\n"
        "### [S1] TargetCo Demo Facility\n"
        "- URL: [https://example.com/targetco-demo](https://example.com/targetco-demo)\n",
    )

    rc = main([
        "gates",
        "check",
        "--workspace",
        str(ws),
        "--stage",
        "finalize",
        "--brief",
        "output/brief.md",
        "--strict",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)["quality_gate_report"]
    projection = report["metadata"]["coverage_omission_projection"]
    findings = [item for item in report["findings"] if item["gate_id"] == "coverage_omission"]
    assert report["metadata"]["reader_facing_mode"] is True
    assert projection["reader_facing_mode"] is True
    assert projection["missing_from_brief_check"] == "skipped_reader_facing_no_internal_claim_refs"
    assert projection["missing_from_brief_count"] == 0
    assert findings == []


def test_coverage_omission_respects_explicit_omission_reason(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_supported_target_ledger(ws)
    _write_screened_candidates(
        ws,
        selected=[
            _selected_candidate(
                candidate_id="CAND-999",
                statement="TargetCo disclosed a high-priority scoped-out item.",
                omission_reason="Outside the configured report scope.",
            )
        ],
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility and reported 42 deployments. [src:CL-001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    projection = state["quality_gate_report"]["metadata"]["coverage_omission_projection"]
    findings = [item for item in state["quality_gate_report"]["findings"] if item["gate_id"] == "coverage_omission"]
    assert projection["scoped_out_count"] == 1
    assert projection["missing_from_ledger_count"] == 0
    assert findings == []


def test_coverage_omission_does_not_interpret_invalid_screened_candidates(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_supported_target_ledger(ws)
    (_intermediate(ws) / "screened_candidates.json").write_text(
        json.dumps(
            {
                "selected": [{"candidate_id": "CAND-999", "statement": "No evidence.", "priority": "high"}],
                "excluded": [],
                "screening_policy": {"max_items": 8},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility and reported 42 deployments. [src:CL-001]\n",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_ARTIFACT_INVALID
    assert "screened_candidates_schema_error" in str(excinfo.value.details)
    assert not _auditor_report_path(ws).exists()


def test_coverage_gate_rejects_screened_candidate_universe_mismatch(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _write_supported_target_ledger(ws)
    _write_screened_candidates(
        ws,
        selected=[_selected_candidate(candidate_id="CAND-999")],
    )
    (_intermediate(ws) / "candidate_claims.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "CAND-001",
                    "claim": "TargetCo opened a demo facility.",
                    "source_id": "SRC-001",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
    )
    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_path = _intermediate(ws) / "event_log.jsonl"
    before_events = event_path.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_ARTIFACT_INVALID
    assert "unknown_candidate_id:CAND-999" in str(excinfo.value.details)
    assert not _auditor_report_path(ws).exists()
    assert event_path.read_bytes() == before_events
    assert "quality_gate_passed" not in event_path.read_text(encoding="utf-8")


def test_quality_gate_invalid_atomic_graph_is_non_blocking(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_supported_target_ledger(ws)
    (_intermediate(ws) / "atomic_claim_graph.json").write_text(
        json.dumps({"claims": []}) + "\n",
        encoding="utf-8",
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    projection = state["quality_gate_report"]["metadata"]["atomic_reader_projection"]
    assert projection["status"] == "invalid_graph"
    assert projection["atom_residue_findings"] == []


def test_quality_gate_claim_support_matrix_blocking_row_blocks(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_claim_support_matrix_fixture(
        ws,
        support_label="unsupported",
        support_strength="none",
        required_action="block_release",
        evidence_span_id=None,
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-0001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    projection = report["metadata"]["claim_support_matrix_projection"]
    csm_findings = [
        finding
        for finding in report["findings"]
        if finding.get("finding_type") == "claim_support_matrix_blocking_support"
    ]
    assert projection["status"] == "valid"
    assert projection["summary_counts"]["blocking_atom_count"] == 1
    assert report["status"] == "fail"
    assert len(csm_findings) == 1
    assert csm_findings[0]["blocking_level"] == "blocking"
    assert csm_findings[0]["metadata"]["semantic_boundary"] == (
        "explicit_support_record_projection_only_not_support_assessment"
    )


@pytest.mark.parametrize(
    ("support_label", "support_strength", "required_action", "evidence_span_id", "finding_type"),
    [
        ("unsupported", "none", "block_release", None, "claim_support_matrix_blocking_support"),
        ("weak_support", "low", "downgrade_wording", "ESP-001-01", "claim_support_matrix_weak_support"),
    ],
    ids=["blocking-custom-csm", "weak-custom-csm"],
)
def test_quality_gate_uses_one_contract_path_context_for_csm_and_atomic_graph(
    tmp_path: Path,
    support_label: str,
    support_strength: str,
    required_action: str,
    evidence_span_id: str | None,
    finding_type: str,
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "configs", repo / "configs")
    (repo / "pyproject.toml").write_text("[project]\nname='path-authority-test'\n", encoding="utf-8")
    (repo / "src" / "multi_agent_brief").mkdir(parents=True)
    ws = _write_workspace(tmp_path)
    _write_claim_support_matrix_fixture(
        ws,
        support_label=support_label,
        support_strength=support_strength,
        required_action=required_action,
        evidence_span_id=evidence_span_id,
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-0001]\n",
    )
    custom_paths = {
        "claim_ledger": "custom/ledger/claims.json",
        "atomic_claim_graph": "custom/graph/atoms.json",
        "evidence_span_registry": "custom/evidence/spans.json",
        "claim_support_matrix": "custom/matrix/support.json",
    }
    contracts_path = repo / "configs" / "artifact_contracts.yaml"
    contracts = yaml.safe_load(contracts_path.read_text(encoding="utf-8"))
    filenames = {
        "claim_ledger": "claim_ledger.json",
        "atomic_claim_graph": "atomic_claim_graph.json",
        "evidence_span_registry": "evidence_span_registry.json",
        "claim_support_matrix": "claim_support_matrix.json",
    }
    for artifact in contracts["artifacts"]:
        artifact_id = str(artifact.get("artifact_id") or "")
        if artifact_id not in custom_paths:
            continue
        artifact["path"] = custom_paths[artifact_id]
        target = ws / custom_paths[artifact_id]
        target.parent.mkdir(parents=True, exist_ok=True)
        (_intermediate(ws) / filenames[artifact_id]).replace(target)
    contracts_path.write_text(yaml.safe_dump(contracts, sort_keys=False), encoding="utf-8")
    for filename in filenames.values():
        (_intermediate(ws) / filename).write_text("{wrong-default-bytes\n", encoding="utf-8")

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=repo)

    report = state["quality_gate_report"]
    assert report["metadata"]["claim_support_matrix_projection"]["status"] == "valid"
    assert report["metadata"]["atomic_reader_projection"]["graph_present"] is True
    assert any(finding.get("finding_type") == finding_type for finding in report["findings"])


def test_custom_auditor_brief_and_ledger_bind_quality_report_through_completion(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "configs", repo / "configs")
    (repo / "pyproject.toml").write_text("[project]\nname='quality-path-authority-test'\n", encoding="utf-8")
    (repo / "src" / "multi_agent_brief").mkdir(parents=True)
    contracts_path = repo / "configs" / "artifact_contracts.yaml"
    contracts = yaml.safe_load(contracts_path.read_text(encoding="utf-8"))
    custom_paths = {
        "audited_brief": "custom/brief/audited.md",
        "claim_ledger": "custom/ledger/claims.json",
    }
    for artifact in contracts["artifacts"]:
        artifact_id = str(artifact.get("artifact_id") or "")
        if artifact_id in custom_paths:
            artifact["path"] = custom_paths[artifact_id]
    contracts_path.write_text(yaml.safe_dump(contracts, sort_keys=False), encoding="utf-8")

    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=repo)
    _set_current_stage(ws, "auditor")
    _write_json(ws, "candidate_claims.json")
    _write_json(ws, "screened_candidates.json")
    (_intermediate(ws) / "analyst_draft_snapshot.md").write_text("# Analyst snapshot\n", encoding="utf-8")
    (_intermediate(ws) / "audit_report.json").write_text(_valid_audit_report_payload(), encoding="utf-8")

    _write_supported_target_ledger(ws)
    custom_ledger = ws / custom_paths["claim_ledger"]
    custom_ledger.parent.mkdir(parents=True)
    (_intermediate(ws) / "claim_ledger.json").replace(custom_ledger)
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility and reported 42 deployments. [src:CL-001]\n",
    )
    custom_brief = ws / custom_paths["audited_brief"]
    custom_brief.parent.mkdir(parents=True)
    (_intermediate(ws) / "audited_brief.md").replace(custom_brief)

    (_intermediate(ws) / "claim_ledger.json").write_text("{poison-default-ledger\n", encoding="utf-8")
    (_intermediate(ws) / "audited_brief.md").write_text("# Poison default brief\n", encoding="utf-8")

    gate_state = quality_gate_state.check_quality_gates(
        workspace=ws,
        repo_workdir=repo,
        report_date="2026-06-18",
        stage_id="auditor",
    )

    report = gate_state["quality_gate_report"]
    assert report["status"] in {"pass", "warning"}
    assert not any(result.get("status") == "fail" for result in report["gate_results"])
    assert not any(finding.get("blocking_level") == "blocking" for finding in report["findings"])
    assert report["metadata"]["brief"] == custom_paths["audited_brief"]
    assert report["metadata"]["ledger"] == custom_paths["claim_ledger"]

    completed = runtime_state.complete_stage_transaction(
        workspace=ws,
        repo_workdir=repo,
        stage_id="auditor",
        reason="custom-path auditor and gates passed",
    )

    assert completed["workflow_state"]["current_stage"] == "finalize"


def test_claim_support_matrix_rejects_incomplete_explicit_path_context(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _write_claim_support_matrix_fixture(
        ws,
        support_label="unsupported",
        support_strength="none",
        required_action="block_release",
        evidence_span_id=None,
    )

    projection = project_claim_support_matrix_from_workspace(ws, artifact_paths={})

    assert projection["status"] == "invalid_matrix"
    assert projection["reason"] == (
        "claim_support_matrix_validation_error:artifact_path_binding_missing:"
        "claim_support_matrix,claim_ledger,atomic_claim_graph,evidence_span_registry"
    )


def test_quality_gate_claim_support_matrix_weak_support_warns(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_claim_support_matrix_fixture(
        ws,
        support_label="weak_support",
        support_strength="low",
        required_action="downgrade_wording",
        evidence_span_id="ESP-001-01",
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-0001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    csm_findings = [
        finding
        for finding in report["findings"]
        if finding.get("finding_type") == "claim_support_matrix_weak_support"
    ]
    assert report["status"] == "warning"
    assert len(csm_findings) == 1
    assert csm_findings[0]["blocking"] is False
    assert csm_findings[0]["metadata"]["row"]["required_action"] == "downgrade_wording"


def test_quality_gate_claim_support_matrix_inferential_support_warns(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_claim_support_matrix_fixture(
        ws,
        support_label="inferential_support",
        support_strength="medium",
        required_action="mark_as_inference",
        evidence_span_id="ESP-001-01",
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-0001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    csm_findings = [
        finding
        for finding in report["findings"]
        if finding.get("finding_type") == "claim_support_matrix_inference_framing"
    ]
    assert report["status"] == "warning"
    assert len(csm_findings) == 1
    assert csm_findings[0]["blocking"] is False
    assert csm_findings[0]["metadata"]["row"]["required_action"] == "mark_as_inference"


def test_quality_gate_claim_support_matrix_auditor_owner_targets_audit_report(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_claim_support_matrix_fixture(
        ws,
        support_label="weak_support",
        support_strength="low",
        required_action="human_adjudication",
        repair_owner="auditor",
        evidence_span_id="ESP-001-01",
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-0001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    csm_finding = next(
        finding
        for finding in report["findings"]
        if finding.get("finding_type") == "claim_support_matrix_weak_support"
    )
    assert csm_finding["repair_owner"] == "auditor"
    assert csm_finding["stage_id"] == "auditor"
    assert csm_finding["artifact_id"] == "audit_report"
    assert csm_finding["repair_artifact_id"] == "audit_report"


def test_quality_gate_claim_support_matrix_human_review_owner_has_no_repair_artifact(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_claim_support_matrix_fixture(
        ws,
        support_label="unsupported",
        support_strength="none",
        required_action="block_release",
        repair_owner="human_review",
        evidence_span_id=None,
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-0001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    csm_finding = next(
        finding
        for finding in report["findings"]
        if finding.get("finding_type") == "claim_support_matrix_blocking_support"
    )
    assert csm_finding["repair_owner"] == "human_review"
    assert csm_finding["stage_id"] is None
    assert csm_finding["artifact_id"] is None
    assert csm_finding["repair_artifact_id"] is None


def test_quality_gate_invalid_claim_support_matrix_does_not_create_findings(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_claim_support_matrix_fixture(
        ws,
        support_label="unsupported",
        support_strength="none",
        required_action="block_release",
        evidence_span_id=None,
    )
    payload = json.loads((_intermediate(ws) / "claim_support_matrix.json").read_text(encoding="utf-8"))
    payload["rows"][0]["atom_id"] = "AC-0001-99"
    (_intermediate(ws) / "claim_support_matrix.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-0001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    assert report["metadata"]["claim_support_matrix_projection"]["status"] == "invalid_matrix"
    assert not [
        finding
        for finding in report["findings"]
        if str(finding.get("finding_type") or "").startswith("claim_support_matrix_")
    ]


def test_quality_gate_invalid_claim_ledger_dependency_skips_claim_support_findings(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_claim_support_matrix_fixture(
        ws,
        support_label="unsupported",
        support_strength="none",
        required_action="block_release",
        evidence_span_id=None,
    )
    _write_ledger(
        ws,
        [
            {
                "claim_id": "CL-0001",
                "statement": "TargetCo opened a demo facility.",
                "source_id": "SRC-001",
                "evidence_text": "TargetCo opened a demo facility.",
            },
            {
                "claim_id": "CL-0001",
                "statement": "Duplicate TargetCo claim.",
                "source_id": "SRC-001",
                "evidence_text": "Duplicate evidence.",
            },
        ],
    )
    _write_audited_brief(
        ws,
        "## Executive Summary\nTargetCo opened a demo facility. [src:CL-0001]\n",
    )

    state = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)

    report = state["quality_gate_report"]
    projection = report["metadata"]["claim_support_matrix_projection"]
    assert projection["status"] == "invalid_matrix"
    assert projection["reason"] == "claim_ledger_schema_error:duplicate_claim_id:CL-0001"
    assert not [
        finding
        for finding in report["findings"]
        if str(finding.get("finding_type") or "").startswith("claim_support_matrix_")
    ]


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


def test_policy_profile_strict_target_relevance_tightens_existing_gate(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    _write_report_spec(ws, policy_profile="finance_default")
    _write_supported_target_ledger(ws)
    _write_audited_brief(ws, "TargetCo update is discussed outside a summary. [src:CL-001]\n")

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
    adapter = report["metadata"]["policy_gate_adapter"]
    assert adapter["status"] == "applied"
    assert adapter["policy_profile_id"] == "finance_default"
    assert report["metadata"]["gate_strictness"]["target_relevance"] is True
    finding = next(item for item in report["findings"] if item["gate_id"] == "target_relevance")
    assert finding["finding_type"] == "target_relevance_gap"
    assert finding["blocking_level"] == "blocking"
    assert finding["metadata"]["strict"] is True


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
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
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
