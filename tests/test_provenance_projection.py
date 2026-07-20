import json
from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    append_event,
    check_runtime_state,
    initialize_runtime_state,
    show_runtime_state,
)
from multi_agent_brief.provenance.builder import (
    build_provenance_workspace,
    provenance_graph_path,
    show_provenance_workspace,
    validate_provenance_workspace,
)
from multi_agent_brief.provenance.model import ProvenanceError
from multi_agent_brief.provenance.validator import validate_graph_payload
from tests.helpers import write_workspace_files_under


REPO = Path(__file__).resolve().parents[1]


_workspace = partial(
    write_workspace_files_under,
    config_text="\n".join([
        "project:",
        "  name: Synthetic TargetCo",
        "report:",
        "  date: '2026-06-09'",
        "output:",
        "  path: output",
        "input:",
        "  path: input",
        "",
    ]),
    user_text="# User\n\nSynthetic TargetCo\n",
)


def _init_state(ws: Path) -> str:
    state = initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=REPO, actor="system")
    run_id = str((state.get("manifest") or {}).get("run_id") or "")
    append_event(
        workspace=ws,
        run_id=run_id,
        event_type="run_initialized",
        actor="system",
        reason="Synthetic runtime state initialized.",
    )
    check_runtime_state(workspace=ws, repo_workdir=REPO, actor="system")
    return run_id


def _edge_types(graph: dict) -> set[str]:
    return {str(edge.get("type")) for edge in graph.get("edges") or []}


def _artifact_registry_path(ws: Path) -> Path:
    return ws / "output" / "intermediate" / "artifact_registry.json"


def _write_artifact_registry_path(ws: Path, artifact_id: str, rel_path: str) -> None:
    path = _artifact_registry_path(ws)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["artifacts"][artifact_id]["path"] = rel_path
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_provenance_build_requires_existing_runtime_state(tmp_path):
    ws = _workspace(tmp_path)

    with pytest.raises(ProvenanceError) as exc:
        build_provenance_workspace(workspace=ws, repo_workdir=REPO)

    assert "Runtime state is missing" in str(exc.value)
    assert not provenance_graph_path(ws).exists()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/etc/hosts",
        "../outside.txt",
        "C:\\Users\\example\\secret.txt",
        "file:///etc/hosts",
    ],
)
def test_provenance_build_rejects_unsafe_artifact_registry_paths_before_file_access(tmp_path, unsafe_path):
    ws = _workspace(tmp_path)
    _init_state(ws)
    _write_artifact_registry_path(ws, "candidate_claims", unsafe_path)

    with pytest.raises(ProvenanceError) as exc:
        build_provenance_workspace(workspace=ws, repo_workdir=REPO)

    assert "artifact_registry[candidate_claims].path" in str(exc.value)
    assert not provenance_graph_path(ws).exists()


def test_provenance_builds_runtime_artifact_event_and_decision_edges(tmp_path):
    ws = _workspace(tmp_path)
    run_id = _init_state(ws)
    append_event(
        workspace=ws,
        run_id=run_id,
        event_type="decision_recorded",
        actor="orchestrator",
        stage_id="doctor",
        decision="continue",
        reason="Synthetic decision.",
    )

    result = build_provenance_workspace(workspace=ws, repo_workdir=REPO)
    graph = result["provenance_graph"]

    assert result["ok"] is True
    assert {"run_has_stage", "stage_produces_artifact", "artifact_consumed_by_stage", "decision_applies_to_stage"} <= _edge_types(graph)
    assert any(node["id"].startswith("event:") for node in graph["nodes"])
    assert any(node["id"].startswith("decision:") for node in graph["nodes"])
    assert not any(node.get("event_type") == "provenance_graph_built" for node in graph["nodes"])
    events = (ws / "output" / "intermediate" / "event_log.jsonl").read_text(encoding="utf-8")
    assert "provenance_graph_built" in events


def test_provenance_filters_prior_provenance_lifecycle_events(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)

    build_provenance_workspace(workspace=ws, repo_workdir=REPO)
    validate_provenance_workspace(workspace=ws)
    graph = build_provenance_workspace(workspace=ws, repo_workdir=REPO)["provenance_graph"]

    event_types = {
        str(node.get("event_type"))
        for node in graph["nodes"]
        if node.get("type") == "event"
    }
    assert not any(event_type.startswith("provenance_graph_") for event_type in event_types)
    events = (ws / "output" / "intermediate" / "event_log.jsonl").read_text(encoding="utf-8")
    assert "provenance_graph_built" in events
    assert "provenance_graph_validated" in events


def test_provenance_does_not_expand_audience_memory_events(tmp_path):
    ws = _workspace(tmp_path)
    run_id = _init_state(ws)
    append_event(
        workspace=ws,
        run_id=run_id,
        event_type="audience_profile_snapshot_created",
        actor="cli",
        reason="Synthetic audience snapshot event.",
        metadata={
            "audience_profile": "audience_profile.md",
            "audience_profile_snapshot": "output/intermediate/audience_profile_snapshot.md",
        },
    )

    graph = build_provenance_workspace(workspace=ws, repo_workdir=REPO)["provenance_graph"]

    event_types = {
        str(node.get("event_type"))
        for node in graph["nodes"]
        if node.get("type") == "event"
    }
    assert "audience_profile_snapshot_created" not in event_types
    events = (ws / "output" / "intermediate" / "event_log.jsonl").read_text(encoding="utf-8")
    assert "audience_profile_snapshot_created" in events


def test_artifact_derived_from_direction_is_output_to_input(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)

    graph = build_provenance_workspace(workspace=ws, repo_workdir=REPO)["provenance_graph"]

    edge = next(
        item
        for item in graph["edges"]
        if item.get("type") == "artifact_derived_from" and item.get("stage_id") == "screener"
    )
    assert edge["from"] == "artifact:screened_candidates"
    assert edge["to"] == "artifact:candidate_claims"
    assert edge["semantic_verified"] is False


def test_provenance_projects_claim_source_and_auditable_references_without_evidence_text(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)
    out = ws / "output" / "intermediate"
    out.mkdir(parents=True, exist_ok=True)
    (out / "claim_ledger.json").write_text(
        json.dumps([
            {
                "claim_id": "SYN_CLAIM_001",
                "statement": "Synthetic TargetCo opened a new facility.",
                "source_id": "SYN_SRC_001",
                "source_url": "https://example.com/source",
                "evidence_text": "Full synthetic evidence text must not be copied.",
                "claim_type": "fact",
                "confidence": "high",
            }
        ]),
        encoding="utf-8",
    )
    (out / "audited_brief.md").write_text(
        "Synthetic TargetCo opened a new facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )

    graph = build_provenance_workspace(workspace=ws, repo_workdir=REPO)["provenance_graph"]
    graph_text = json.dumps(graph, ensure_ascii=False, sort_keys=True)

    assert "claim_cites_source" in _edge_types(graph)
    assert "claim_recorded_in_artifact" in _edge_types(graph)
    assert "artifact_references_claim" in _edge_types(graph)
    assert "source_supports_claim" not in graph_text
    assert "Full synthetic evidence text must not be copied." not in graph_text
    claim_edge = next(edge for edge in graph["edges"] if edge["type"] == "claim_cites_source")
    assert claim_edge["semantic_verified"] is False


def test_provenance_accepts_wrapped_claim_ledger(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)
    out = ws / "output" / "intermediate"
    out.mkdir(parents=True, exist_ok=True)
    (out / "claim_ledger.json").write_text(
        json.dumps({
            "metadata": {"synthetic": True},
            "claims": [
                {
                    "claim_id": "SYN_CLAIM_002",
                    "statement": "Synthetic wrapped claim.",
                    "source_id": "SYN_SRC_002",
                    "evidence_text": "Wrapped evidence text.",
                }
            ],
        }),
        encoding="utf-8",
    )

    graph = build_provenance_workspace(workspace=ws, repo_workdir=REPO)["provenance_graph"]

    assert any(node["id"] == "claim:SYN_CLAIM_002" for node in graph["nodes"])
    assert any(edge["type"] == "claim_cites_source" for edge in graph["edges"])


def test_provenance_projects_feedback_repair_and_quality_gate_targets(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)
    out = ws / "output" / "intermediate"
    out.mkdir(parents=True, exist_ok=True)
    (out / "feedback_issues.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-feedback-issues/v1",
            "created_at": "2026-06-09T00:00:00+00:00",
            "updated_at": "2026-06-09T00:00:00+00:00",
            "issues": [
                {
                    "issue_id": "fb_SYN_001",
                    "source": "human",
                    "severity": "blocking",
                    "stage_id": "analyst",
                    "artifact_id": "audited_brief",
                    "category": "unsupported_claim",
                    "summary": "Synthetic feedback summary",
                    "status": "planned",
                }
            ],
        }),
        encoding="utf-8",
    )
    (out / "repair_plan.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-repair-plan/v1",
            "created_at": "2026-06-09T00:00:00+00:00",
            "updated_at": "2026-06-09T00:00:00+00:00",
            "repair_plans": [
                {
                    "repair_plan_id": "rp_SYN_001",
                    "status": "planned",
                    "target_stage": "analyst",
                    "target_artifacts": ["audited_brief"],
                    "issue_ids": ["fb_SYN_001"],
                }
            ],
        }),
        encoding="utf-8",
    )
    (out / "quality_gate_report.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-quality-gates/v1",
            "created_at": "2026-06-09T00:00:00+00:00",
            "updated_at": "2026-06-09T00:00:00+00:00",
            "workspace": ".",
            "status": "fail",
            "gate_results": [],
            "findings": [
                {
                    "finding_id": "qg_SYN_001",
                    "finding_type": "target_relevance_gap",
                    "severity": "high",
                    "blocking_level": "blocking",
                    "blocking": True,
                    "gate_stage_id": "auditor",
                    "gate_artifact_id": "quality_gate_report",
                }
            ],
            "metadata": {},
        }),
        encoding="utf-8",
    )

    graph = build_provenance_workspace(workspace=ws, repo_workdir=REPO)["provenance_graph"]
    edge_types = _edge_types(graph)

    assert "feedback_targets_stage" in edge_types
    assert "feedback_targets_artifact" in edge_types
    assert "repair_plan_addresses_issue" in edge_types
    assert "gate_finding_targets_stage" in edge_types
    assert "gate_finding_targets_artifact" in edge_types


def test_repair_plan_unknown_feedback_issue_warns_without_dangling_edge(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)
    out = ws / "output" / "intermediate"
    out.mkdir(parents=True, exist_ok=True)
    (out / "repair_plan.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-repair-plan/v1",
            "created_at": "2026-06-09T00:00:00+00:00",
            "updated_at": "2026-06-09T00:00:00+00:00",
            "repair_plans": [
                {
                    "repair_plan_id": "rp_SYN_missing_issue",
                    "status": "planned",
                    "target_stage": "analyst",
                    "target_artifacts": ["audited_brief"],
                    "issue_ids": ["fb_SYN_missing"],
                }
            ],
        }),
        encoding="utf-8",
    )

    result = build_provenance_workspace(workspace=ws, repo_workdir=REPO)
    graph = result["provenance_graph"]

    assert result["ok"] is True
    assert any("unknown feedback issue id: fb_SYN_missing" in warning for warning in graph["warnings"])
    assert any(node["id"] == "repair_plan:rp_SYN_missing_issue" for node in graph["nodes"])
    assert not any(edge["type"] == "repair_plan_addresses_issue" for edge in graph["edges"])


def test_provenance_validator_rejects_hard_graph_errors():
    graph = {
        "schema_version": "multi-agent-brief-provenance-graph/v1",
        "run_id": "RUN",
        "workspace": ".",
        "source_files": [{"path": "/etc/hosts"}],
        "nodes": [
            {"id": "run:RUN", "type": "run", "ref": "."},
            {"id": "run:RUN", "type": "run", "ref": "."},
            {"id": "artifact:bad", "type": "artifact", "ref": "../bad.json"},
        ],
        "edges": [
            {"from": "source:S1", "to": "claim:C1", "type": "source_supports_claim"},
            {"from": "run:RUN", "to": "missing:node", "type": "run_has_stage"},
            {"from": "run:RUN", "to": "artifact:bad", "type": "unknown_edge"},
        ],
        "warnings": [],
    }

    result = validate_graph_payload(graph)

    assert result["ok"] is False
    assert any("duplicated" in error for error in result["errors"])
    assert any("semantic" in error for error in result["errors"])
    assert any("missing node" in error for error in result["errors"])
    assert any("relative" in error or "traversal" in error for error in result["errors"])


def test_provenance_build_rejects_malformed_jsonl(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)
    (ws / "output" / "intermediate" / "event_log.jsonl").write_text("{broken\n", encoding="utf-8")

    with pytest.raises(ProvenanceError) as exc:
        build_provenance_workspace(workspace=ws, repo_workdir=REPO)

    assert "Invalid JSONL" in str(exc.value)


def test_missing_provenance_does_not_block_state_check(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)

    state = check_runtime_state(workspace=ws, repo_workdir=REPO)
    registry = state["artifact_registry"]["artifacts"]

    assert state["workflow_state"]["blocked"] is False
    assert registry["provenance_graph"]["status"] == "expected"
    assert registry["provenance_graph"]["validation_result"] == "not_checked"
    assert not provenance_graph_path(ws).exists()

    build_provenance_workspace(workspace=ws, repo_workdir=REPO)
    state = check_runtime_state(workspace=ws, repo_workdir=REPO)
    registry = state["artifact_registry"]["artifacts"]

    assert state["workflow_state"]["blocked"] is False
    assert registry["provenance_graph"]["status"] == "valid"
    assert registry["provenance_graph"]["validation_result"] == "valid_minimum"


@pytest.mark.parametrize("action", ("build", "show", "validate"))
def test_provenance_cli_is_retired_with_zero_writes(tmp_path, capsys, action):
    ws = _workspace(tmp_path)
    _init_state(ws)
    argv = ["provenance", action, "--workspace", str(ws), "--json"]
    if action == "build":
        argv.extend(["--repo-workdir", str(REPO)])
    before_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }

    rc = main(argv)

    # LEGACY-DELETE: the JSON provenance CLI payloads are removed with the
    # retired legacy-workspace public surface; rejection is now fail-closed.
    assert rc == 1
    assert capsys.readouterr().out == "legacy_workspace_unsupported\n"
    after_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
    assert not provenance_graph_path(ws).exists()


def test_provenance_build_show_validate_round_trip(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)

    build_state = build_provenance_workspace(workspace=ws, repo_workdir=REPO)
    assert build_state["ok"] is True

    show_state = show_provenance_workspace(workspace=ws)
    assert show_state["ok"] is True

    validate_state = validate_provenance_workspace(workspace=ws)
    assert validate_state["ok"] is True


def test_validate_strict_fails_on_warnings(tmp_path):
    ws = _workspace(tmp_path)
    _init_state(ws)
    build_provenance_workspace(workspace=ws, repo_workdir=REPO)

    result = validate_provenance_workspace(workspace=ws, strict=True)

    assert result["ok"] is False
    assert any("strict mode" in error for error in result["validation"]["errors"])
