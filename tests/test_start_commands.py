"""Tests for briefloop start / handoff launcher."""
from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.cli.start_commands import (
    CONTRACT_REFERENCES,
    RUNTIME_CODEBUDDY,
    RUNTIME_OPERATOR,
    VALID_RUNTIMES,
    build_handoff,
    render_handoff_cli,
    write_handoff_artifacts,
)
from multi_agent_brief.orchestrator_contract import contract_references_exist
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.orchestrator.runtime_state import RUNTIME_STATE_FILES
from multi_agent_brief.orchestrator.runtime_state import (
    complete_stage_transaction,
    initialize_runtime_state,
    record_decision,
    runtime_state_paths,
)
from multi_agent_brief.audience_memory import AUDIENCE_MEMORY_FILES
from multi_agent_brief.controls.contract import CONTROL_SWITCHBOARD_FILES
from multi_agent_brief.feedback.feedback_contract import FEEDBACK_STATE_FILES
from multi_agent_brief.quality_gates.contract import QUALITY_GATE_STATE_FILES
from multi_agent_brief.provenance.contract import PROVENANCE_STATE_FILES
from tests.helpers import sha256_file as _sha256_file
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent


_write_workspace = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "Test Brief"
  company: "TestCo"
  industry: "testing"
  language: "en"
  audience: "management"
report:
  cadence: "weekly"
input:
  path: "input"
output:
  path: "output"
""".strip(),
    user_text="# Test User Profile\n\nCompany: TestCo\n",
    sources_text="""
source_strategy:
  profile: "conservative"
  enabled_providers:
    - "manual"
manual:
  enabled: true
  sources: []
""".strip(),
    include_input_dir=True,
)


def _write_market_report_spec(ws: Path, *, policy_profile: str = "internet_default") -> None:
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


def _write_solar_report_spec(ws: Path) -> None:
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "briefloop.report_spec.v1",
                "report_pack": "solar_industry_periodic",
                "policy_profile": "solar_manufacturing_default",
                "report_type": "solar_industry_periodic",
                "title": "Solar Industry Periodic Report",
                "cadence": "weekly",
                "audience": {"label": "management reader", "language": "zh-CN"},
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


def _mark_fact_layer_imported(ws: Path) -> None:
    paths = runtime_state_paths(ws)
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source-001.md").write_text("# Source\n\nExample evidence.\n", encoding="utf-8")
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "input_classification.json").write_text(
        json.dumps(
            {
                "evidence": [{"path": "input/sources/source-001.md", "name": "source-001.md"}],
                "feedback": [],
                "instruction": [],
                "context": [],
                "skipped": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "candidate_claims.json").write_text("[]\n", encoding="utf-8")
    (intermediate / "screened_candidates.json").write_text("[]\n", encoding="utf-8")
    (intermediate / "claim_ledger.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "CL-001",
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                }
            ]
        )
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
    fact_layer_sha256 = "c" * 64
    source_run_id = "mabw-20260614T010000Z-source"
    satisfied_stage_ids = [
        "doctor",
        "source-discovery",
        "input-governance",
        "scout",
        "screener",
        "claim-ledger",
    ]
    manifest["recipe"] = "fast-rerun"
    manifest["fact_layer_import"] = {
        "schema_version": "mabw.fact_layer_import.v1",
        "source_run_id": source_run_id,
        "source_archive_manifest": f"output/runs/{source_run_id}/manifest.json",
        "source_archive_manifest_sha256": "d" * 64,
        "fact_layer_sha256": fact_layer_sha256,
        "imported_file_count": len(imported_files),
        "imported_files": imported_files,
        "satisfied_stage_ids": satisfied_stage_ids,
    }
    statuses = dict(workflow.get("stage_statuses") or {})
    for stage_id in satisfied_stage_ids:
        statuses[stage_id] = {
            "status": "complete",
            "reason": "Satisfied by frozen fact layer import.",
            "updated_at": "2026-06-14T01:00:00+00:00",
            "metadata": {
                "satisfied_by_import": True,
                "fact_layer_import_sha256": fact_layer_sha256,
                "source_run_id": source_run_id,
            },
        }
    statuses["analyst"] = {
        "status": "ready",
        "reason": "",
        "updated_at": "2026-06-14T01:00:00+00:00",
    }
    workflow["current_stage"] = "analyst"
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow["stage_statuses"] = statuses
    paths["runtime_manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["workflow_state"].write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_auditable_condition_metadata(ws: Path) -> None:
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


def _assert_atomic_graph_boundary(text: str) -> None:
    normalized = " ".join(text.replace("`", "").split())
    assert "output/intermediate/atomic_claim_graph.json" in normalized
    assert "optional experimental structural decomposition aid" in normalized
    assert "not source evidence or proof of support" in normalized
    assert "Do not cite atom IDs" in normalized or "do not cite atom IDs" in normalized
    assert "create/edit/repair/extend" in normalized
    assert "material atoms absent" in normalized


def _assert_orchestrator_contract_handoff(data: dict[str, object]) -> None:
    text = "\n".join(
        str(data.get(key, ""))
        for key in ("next_steps", "prompt", "notes")
    )
    assert data["contract_references"] == CONTRACT_REFERENCES
    assert data["recipe"] in {"full", "fast-rerun"}
    assert data["runtime_state_files"] == RUNTIME_STATE_FILES
    assert data["audience_memory_files"] == AUDIENCE_MEMORY_FILES
    assert isinstance(data.get("improvement_memory_files"), dict)
    assert data["control_switchboard_files"] == CONTROL_SWITCHBOARD_FILES
    assert data["feedback_state_files"] == FEEDBACK_STATE_FILES
    assert data["quality_gate_state_files"] == QUALITY_GATE_STATE_FILES
    assert data["provenance_state_files"] == PROVENANCE_STATE_FILES
    protocol = data.get("stage_completion_protocol")
    assert isinstance(protocol, dict)
    protocol_text = json.dumps(protocol, sort_keys=True)
    assert protocol["schema_version"] == "multi-agent-brief-stage-completion-protocol/v1"
    assert protocol["status"] == "canonical_handoff_protocol"
    assert any("artifact-based" in rule for rule in protocol["rules"])
    assert any("Scout chunk outputs are scratch material only" in rule for rule in protocol["rules"])
    protocol_stages = {stage["stage_id"]: stage for stage in protocol["stages"]}
    assert protocol_stages["scout"]["required_output_artifacts"] == [
        {
            "artifact_id": "candidate_claims",
            "path": "output/intermediate/candidate_claims.json",
            "required": True,
            "format": "json",
        }
    ]
    scout_chunk_contract = protocol_stages["scout"]["parallel_chunk_contract"]
    assert scout_chunk_contract["status"] == "runtime_internal_optional"
    assert scout_chunk_contract["scratch_only"] is True
    assert scout_chunk_contract["final_joined_artifacts"] == {
        "always": ["output/intermediate/candidate_claims.json"],
        "default_topology": ["output/intermediate/screened_candidates.json"],
    }
    assert "stable ordering must use source identity" in scout_chunk_contract["join_requirement"]
    assert "append to candidate_claims.json from chunk workers" in scout_chunk_contract["forbidden"]
    assert "treat chunk outputs as workflow artifacts" in scout_chunk_contract["forbidden"]
    assert "drop duplicates silently during chunk join" in scout_chunk_contract["forbidden"]
    screener_protocol = protocol_stages["screener"]
    assert screener_protocol["topology_satisfaction"]["default"]["satisfied_by"] == "scout"
    assert screener_protocol["topology_satisfaction"]["default"]["forbidden_replay_actions"] == [
        "delegate screener",
        "state stage-complete --stage screener",
    ]
    assert screener_protocol["topology_satisfaction"]["default"]["required_artifacts"] == [
        {
            "artifact_id": "candidate_claims",
            "path": "output/intermediate/candidate_claims.json",
            "required": True,
            "format": "json",
        },
        {
            "artifact_id": "screened_candidates",
            "path": "output/intermediate/screened_candidates.json",
            "required": True,
            "format": "json",
        },
    ]
    assert screener_protocol["topology_satisfaction"]["human_assisted"]["satisfied_by"] == "scout"
    assert screener_protocol["topology_satisfaction"]["human_assisted"]["forbidden_replay_actions"] == [
        "delegate screener",
        "state stage-complete --stage screener",
    ]
    assert screener_protocol["independent_completion_topologies"] == ["strict"]
    analyst_protocol = protocol_stages["analyst"]
    assert analyst_protocol["topology_satisfaction"]["human_assisted"]["satisfied_by"] == "writer"
    assert "forbidden_replay_actions" not in analyst_protocol["topology_satisfaction"]["human_assisted"]
    editor_protocol = protocol_stages["editor"]
    assert editor_protocol["topology_satisfaction"]["human_assisted"]["satisfied_by"] == "writer"
    assert "forbidden_replay_actions" not in editor_protocol["topology_satisfaction"]["human_assisted"]
    claim_ledger_protocol = protocol_stages["claim-ledger"]
    assert claim_ledger_protocol["freeze_input_artifacts"] == [
        {
            "artifact_id": "claim_drafts",
            "path": "output/intermediate/claim_drafts.json",
            "required": False,
            "format": "json",
        }
    ]
    assert claim_ledger_protocol["pre_completion_transactions"][0]["transaction"] == "freeze_claim_ledger"
    assert "freeze-claim-ledger" in claim_ledger_protocol["pre_completion_transactions"][0]["command"]
    assert claim_ledger_protocol["pre_completion_transactions"][0]["writes"] == [
        {
            "artifact_id": "claim_ledger",
            "path": "output/intermediate/claim_ledger.json",
            "required": True,
            "format": "json",
        }
    ]
    assert protocol_stages["analyst"]["required_input_artifacts"] == [
        {
            "artifact_id": "claim_ledger",
            "path": "output/intermediate/claim_ledger.json",
            "required": True,
            "format": "json",
        },
        {
            "artifact_id": "input_classification",
            "path": "output/input_classification.json",
            "required": False,
            "format": "json",
        }
    ]
    assert protocol_stages["analyst"]["context_inputs"] == ["user_profile"]
    editor_inputs = {
        item["artifact_id"] for item in protocol_stages["editor"]["required_input_artifacts"]
    }
    assert {"analyst_draft_snapshot", "claim_ledger", "input_classification"} <= editor_inputs
    assert protocol_stages["editor"]["context_inputs"] == ["user_profile"]
    assert any("prose acknowledgement" in item for item in protocol_stages["auditor"]["forbidden_actions"])
    for rel_path in data["runtime_state_files"].values():
        assert not Path(str(rel_path)).is_absolute()
    for rel_path in data["audience_memory_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["improvement_memory_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["control_switchboard_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["feedback_state_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["quality_gate_state_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["provenance_state_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["expected_artifacts"]:
        assert not Path(str(rel_path)).is_absolute()
    assert "output/intermediate/atomic_claim_graph.json" not in data["expected_artifacts"]
    _assert_atomic_graph_boundary(protocol_text)
    assert "Orchestrator main agent" in text or "Orchestrator main-agent" in text
    assert "configs/orchestrator_contract.yaml" in text
    assert "configs/stage_specs.yaml" in text
    assert "configs/artifact_contracts.yaml" in text
    assert "configs/policy_packs/default.yaml" in text
    assert "runtime_manifest.json" in text
    assert "workflow_state.json" in text
    assert "audience_profile_snapshot.md" in text
    assert "orchestrator_control_switchboard.json" in text
    assert "control_selections.json" in text
    assert "Selection is not execution" in text or "selection is not execution" in text
    assert "feedback_issues.json" in text
    assert "repair_plan.json" in text
    assert "auditor_quality_gate_report.json" in text
    assert "finalize_quality_gate_report.json" in text
    assert "quality_gate_report.json" in text
    assert "provenance_graph.json" in text
    assert "briefloop state stage-complete --workspace <workspace>" in text
    assert "briefloop state finalize-complete --workspace <workspace>" in text
    assert "state decide" in text
    assert "briefloop repair route --workspace <workspace>" in text
    assert "briefloop repair start --workspace <workspace>" in text
    assert "briefloop repair complete --workspace <workspace>" in text
    assert "next_allowed_decisions" in text
    assert "Stage completion protocol" in text
    assert "MUST produce" in text
    assert "optional chunk parallelism" in text
    assert "runtime-internal scratch only" in text
    assert "append to candidate_claims.json from chunk workers" in text
    assert "topology satisfaction: default: satisfied by scout" in text
    assert "do not replay delegate screener, state stage-complete --stage screener" in text
    assert "independent MUST produce (strict): screened_candidates at output/intermediate/screened_candidates.json" in text
    assert "role MUST produce freeze input: audited_brief at output/intermediate/audited_brief.md" in text
    assert (
        "control transaction before stage-complete: "
        "briefloop state stage-complete --workspace <workspace> --stage analyst "
        "(reads audited_brief at output/intermediate/audited_brief.md; "
        "writes analyst_draft_snapshot at output/intermediate/analyst_draft_snapshot.md)"
    ) in text
    assert "MUST produce: analyst_draft_snapshot at output/intermediate/analyst_draft_snapshot.md" not in text
    assert (
        "- screener:\n"
        "  required input artifacts: candidate_claims at output/intermediate/candidate_claims.json\n"
        "  context inputs: none\n"
        "  MUST produce: screened_candidates at output/intermediate/screened_candidates.json"
    ) not in str(data.get("prompt", ""))
    assert "I completed the stage" in text
    assert "source_candidates.yaml is a source plan only, not source evidence" in text
    assert "URL, source title/name, published date or retrieved_at" in text
    assert "output artifacts are frozen for downstream stages" in text
    assert "must not rewrite them in place" in text
    assert "route repair back to the owner stage" in text
    assert "REFERENCE_RUN_ORCHESTRATOR_PROTOCOL.md" in text
    assert "briefloop gates check --workspace <workspace> --stage auditor" in text
    assert "briefloop gates check --workspace <workspace> --stage finalize" in text
    assert "briefloop state check --workspace <workspace> --strict" in text
    assert "Did 0 searches" in text
    assert "every query returns an empty result set" in text
    assert "Do not switch to source-planner" in text
    assert "Audit and quality gates passed" in text
    assert "do not finalize" in text
    assert "Audit warnings, overstatement findings, support-calibration findings" in text
    assert "do not authorize direct edits to frozen artifacts" in text
    assert "Quality gate controls are optional" not in text
    assert "retry_stage" in text
    assert "request_human_review" in text
    assert "block_run" in text
    repo = Path(str(data["repo_workdir"]))
    assert contract_references_exist(repo)
    for rel_path in data["contract_references"].values():
        assert (repo / str(rel_path)).exists()


def _write_packaged_contract_base(tmp_path: Path) -> Path:
    package_base = tmp_path / "multi_agent_brief"
    for rel_path in CONTRACT_REFERENCES.values():
        target = package_base / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder: true\n", encoding="utf-8")
    (package_base / "__init__.py").write_text("", encoding="utf-8")
    return package_base


def test_resolve_repo_workdir_falls_back_to_packaged_contracts(tmp_path):
    package_base = _write_packaged_contract_base(tmp_path)

    resolved = resolve_repo_workdir(package_base)

    assert resolved == package_base.resolve()
    assert contract_references_exist(resolved)


# ---------------------------------------------------------------------------
# Help and identity tests
# ---------------------------------------------------------------------------

def test_start_help_shows_runtime_options(capsys):
    """start --help must show runtime choices and launcher identity."""
    try:
        main(["start", "--help"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    output = captured.out
    assert "launcher" in output.lower() or "handoff" in output.lower()
    assert "--runtime" in output
    assert "--recipe" in output
    assert "hermes" in output
    assert "claude" in output
    assert "operator" in output
    assert "manual" in output
    assert "legacy alias for operator" in output
    assert "--workspace" in output


def test_start_help_does_not_claim_to_generate_briefs(capsys):
    """start help must not present itself as a brief generator."""
    try:
        main(["start", "--help"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    output = captured.out
    assert "generate" not in output.lower() or "never generates" in output.lower()


def test_handoff_help_shows_config_required(capsys):
    try:
        main(["handoff", "--help"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    output = captured.out
    assert "--config" in output
    assert "--runtime" in output


# ---------------------------------------------------------------------------
# start — no workspace
# ---------------------------------------------------------------------------

def test_start_no_workspace_in_non_workspace_dir(tmp_path, monkeypatch, capsys):
    """start without --workspace in a non-workspace dir should give guidance."""
    monkeypatch.chdir(tmp_path)
    rc = main(["start", "--skip-doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    output = captured.out
    assert "No workspace found" in output or "briefloop init" in output


def test_start_auto_detects_workspace_in_cwd(tmp_path, monkeypatch):
    """start without --workspace should detect workspace if CWD is one."""
    ws = _write_workspace(tmp_path)
    monkeypatch.chdir(ws)
    rc = main(["start", "--skip-doctor"])
    assert rc == 0
    assert (ws / "output" / "intermediate" / "agent_handoff.md").exists()
    json_path = ws / "output" / "intermediate" / "agent_handoff.json"
    assert json_path.exists()
    assert (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert (ws / "output" / "intermediate" / "workflow_state.json").exists()
    assert (ws / "output" / "intermediate" / "artifact_registry.json").exists()
    assert (ws / "output" / "intermediate" / "event_log.jsonl").exists()
    assert (ws / "audience_profile.md").exists()
    assert (ws / "output" / "intermediate" / "audience_profile_snapshot.md").exists()
    assert (ws / "output" / "intermediate" / "orchestrator_control_switchboard.json").exists()
    assert not (ws / "output" / "intermediate" / "control_selections.json").exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert Path(data["repo_workdir"]).resolve() == ROOT
    _assert_orchestrator_contract_handoff(data)


# ---------------------------------------------------------------------------
# start — with workspace
# ---------------------------------------------------------------------------

def test_start_with_workspace_generates_handoff(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0

    md = ws / "output" / "intermediate" / "agent_handoff.md"
    js = ws / "output" / "intermediate" / "agent_handoff.json"
    assert md.exists()
    assert js.exists()
    assert (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert (ws / "output" / "intermediate" / "audience_profile_snapshot.md").exists()

    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["runtime"] == "hermes"
    _assert_orchestrator_contract_handoff(data)
    md_text = md.read_text(encoding="utf-8")
    _assert_atomic_graph_boundary(data["prompt"])
    _assert_atomic_graph_boundary(md_text)
    claim_ledger_section = md_text.split("#### `claim-ledger`", 1)[1].split("#### `analyst`", 1)[0]
    assert "Freeze input artifacts" in claim_ledger_section
    assert "`claim_drafts` at `output/intermediate/claim_drafts.json`" in claim_ledger_section
    assert "Pre-completion transactions" in claim_ledger_section
    assert "freeze-claim-ledger" in claim_ledger_section
    assert "state stage-complete --stage claim-ledger" in claim_ledger_section


def test_start_handoff_projects_policy_profile_without_runtime_effect(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_market_report_spec(ws, policy_profile="internet_default")

    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0

    md = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    projection = data["policy_profile_projection"]
    assert projection["status"] == "resolved"
    assert projection["resolved_policy_profile"] == "internet_default"
    assert projection["source"] == "report_spec.policy_profile"
    assert projection["runtime_effect"] == "none"
    assert "Policy Profile Projection" in md
    assert "internet_default" in md
    assert "Runtime effect: `none`" in md
    assert "does not adapt gates" in md
    assert "PolicyProfile projection" in data["prompt"]
    assert "runtime_effect=none" in data["prompt"]


def test_start_handoff_projects_sourcehub_runtime_search_tasks(tmp_path):
    ws = _write_workspace(tmp_path)
    sources = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    sources["source_strategy"]["enabled_providers"].append("web_search")
    sources["web_search"] = {
        "enabled": True,
        "mode": "runtime_tool",
        "search_tasks": [
            {
                "query": "solar prices",
                "domains": ["example.com"],
                "max_results": 12,
                "sourcehub_registered": True,
                "handoff_only": True,
            }
        ],
    }
    (ws / "sources.yaml").write_text(
        yaml.safe_dump(sources, sort_keys=False),
        encoding="utf-8",
    )

    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0

    md = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    projection = data["sourcehub_projection"]
    assert projection["status"] == "available"
    assert projection["source_config_path"] == "sources.yaml"
    assert projection["runtime_effect"] == "handoff_only"
    runtime_web = projection["runtime_web_search"]
    assert runtime_web["enabled"] is True
    assert runtime_web["mode"] == "runtime_tool"
    assert runtime_web["search_tasks"][0]["query"] == "solar prices"
    assert runtime_web["search_tasks"][0]["domains"] == ["example.com"]
    assert "SourceHub Lite Projection" in md
    assert "solar prices" in md
    assert "sources.yaml" in md
    assert "SourceHub Lite projection" in data["prompt"]
    assert "queries=solar prices" in data["prompt"]
    assert "runtime search tool" in data["prompt"]


def test_build_handoff_all_runtimes_include_sourcehub_search_tasks(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        """
source_strategy:
  profile: "research"
  enabled_providers:
    - "manual"
    - "web_search"
manual:
  enabled: true
  sources: []
web_search:
  enabled: true
  mode: runtime_tool
  search_tasks:
    - query: "solar prices"
      max_results: 10
      sourcehub_registered: true
      handoff_only: true
""".strip(),
        encoding="utf-8",
    )
    for runtime in VALID_RUNTIMES:
        handoff = build_handoff(
            workspace=ws,
            repo_workdir=ROOT,
            runtime=runtime,
            run_doctor=False,
        )
        assert handoff.sourcehub_projection["status"] == "available"
        assert "solar prices" in handoff.prompt
        assert "sources.yaml" in handoff.prompt


def test_start_handoff_projects_report_template_section_order(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_solar_report_spec(ws)

    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0

    md = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    projection = data["report_template_projection"]

    assert projection["status"] == "resolved"
    assert projection["template_id"] == "solar_industry_periodic"
    assert projection["runtime_effect"] == "none"
    assert projection["section_order"] == [
        "cover",
        "executive_summary",
        "supply_chain_price_tracker",
        "demand_installation_outlook",
        "policy_tax_financing",
        "fx_rates_tracker",
        "company_implications",
        "source_appendix",
    ]
    assert "Report Template Projection" in md
    assert "`supply_chain_price_tracker`" in md
    assert "This projection is product section-order metadata only" in md
    assert "ReportTemplate projection" in data["prompt"]
    assert "section_order=cover, executive_summary, supply_chain_price_tracker" in data["prompt"]
    assert "runtime_effect=none" in data["prompt"]
    assert data["report_template_conformance_projection"]["status"] == "no_targets"
    assert "ReportTemplate conformance projection" not in data["prompt"]
    assert "Report Template Conformance Projection" not in md


def test_start_handoff_projects_report_template_conformance_pass(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_solar_report_spec(ws)
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "# Solar Industry Periodic Report",
            "Title.",
            "## Executive Summary",
            "Summary.",
            "## Supply Chain Price Tracker",
            "Prices.",
            "## Demand Installation Outlook",
            "Demand.",
            "## Policy Tax Financing",
            "Policy.",
            "## FX Rates Tracker",
            "Rates.",
            "## Company Implications",
            "Implications.",
            "## Source Appendix",
            "Sources.",
        ]),
        encoding="utf-8",
    )

    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0

    md = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    projection = data["report_template_conformance_projection"]

    assert projection["status"] == "pass"
    assert projection["runtime_effect"] == "none"
    assert projection["summary_counts"]["warning_target_count"] == 0
    assert "ReportTemplate conformance projection" in data["prompt"]
    assert "status=pass" in data["prompt"]
    assert "diagnostics=none" in data["prompt"]
    assert "runtime_effect=none" in data["prompt"]
    assert "Report Template Conformance Projection" in md
    assert "This projection is structure guidance only" in md


def test_start_handoff_projects_report_template_render_plan(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_solar_report_spec(ws)
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "# Solar Industry Periodic Report",
            "Title.",
            "## Executive Summary",
            "Summary.",
            "## Supply Chain Price Tracker",
            "Prices.",
            "## Demand Installation Outlook",
            "Demand.",
            "## Policy Tax Financing",
            "Policy.",
            "## FX Rates Tracker",
            "Rates.",
            "## Company Implications",
            "Implications.",
            "## Source Appendix",
            "Sources.",
        ]),
        encoding="utf-8",
    )

    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0

    md = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    projection = data["report_template_render_plan_projection"]

    assert projection["status"] == "planned"
    assert projection["runtime_effect"] == "none"
    assert projection["selected_source_artifact"] == "output/intermediate/audited_brief.md"
    assert projection["section_plan"][1]["matched_heading"] == "Executive Summary"
    assert projection["planned_delivery_targets"][0] == {
        "artifact": "output/brief.md",
        "kind": "reader_markdown",
        "concrete": "true",
    }
    assert projection["planned_delivery_targets"][3] == {
        "artifact": "output/delivery/Test_Brief.docx",
        "kind": "delivery_docx",
        "concrete": "true",
        "filename_source": "named_output",
    }
    assert projection["planned_delivery_targets"][3]["artifact"] != "output/delivery/brief.docx"
    assert "ReportTemplate render plan projection" in data["prompt"]
    assert "status=planned" in data["prompt"]
    assert "source=output/intermediate/audited_brief.md" in data["prompt"]
    assert "runtime_effect=none" in data["prompt"]
    assert "does not render templates" in data["prompt"]
    assert "Report Template Render Plan Projection" in md
    assert "Selected source artifact: `output/intermediate/audited_brief.md`" in md
    assert "This projection is render planning diagnostics only" in md


def test_start_handoff_projects_report_template_conformance_warning(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_solar_report_spec(ws)
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "audited_brief.md").write_text(
        "\n".join([
            "## Executive Summary",
            "Summary.",
            "## Cover",
            "Cover.",
            "## Unplanned Commentary",
            "Extra.",
        ]),
        encoding="utf-8",
    )

    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0

    md = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    projection = data["report_template_conformance_projection"]

    assert projection["status"] == "warning"
    assert projection["runtime_effect"] == "none"
    assert projection["summary_counts"]["missing_section_count"] > 0
    assert "ReportTemplate conformance projection" in data["prompt"]
    assert "status=warning" in data["prompt"]
    assert "missing=" in data["prompt"]
    assert "Use these diagnostics as structure guidance only" in data["prompt"]
    assert "does not render templates" in data["prompt"]
    assert "Report Template Conformance Projection" in md
    assert "Missing sections:" in md


def test_start_handoff_does_not_treat_invalid_report_template_as_contract(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_solar_report_spec(ws)
    spec = yaml.safe_load((ws / "report_spec.yaml").read_text(encoding="utf-8"))
    spec["report_type"] = "unknown_report_type"
    (ws / "report_spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0

    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    projection = data["report_template_projection"]

    assert projection["status"] == "invalid_report_spec"
    assert "ReportTemplate projection" in data["prompt"]
    assert "status=invalid_report_spec" in data["prompt"]
    assert "Do not use this as a section-order contract" in data["prompt"]
    assert "validate and fix report_spec.yaml first" in data["prompt"]
    assert "Use this as a stable product section-order contract" not in data["prompt"]


def test_run_handoff_projects_trajectory_decision_narrowing(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor complete",
    )
    for idx in range(3):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            decision="retry_stage",
            reason=f"retry {idx + 1}",
        )

    rc = main([
        "run",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])

    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    md = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    text = data["prompt"] + "\n" + "\n".join(data["notes"]) + "\n" + md
    assert any("Trajectory regulation narrowed current-stage decisions" in note for note in data["notes"])
    assert "stage=source-discovery" in text
    assert "allowed=request_human_review, block_run" in text
    assert "retry_budget_exhausted" in text
    assert "Do not call `state decide --decision retry_stage`" in text


def test_start_handoff_projects_auditable_assessment_target(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_auditable_condition_metadata(ws)

    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])

    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    md = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    text = data["prompt"] + "\n" + "\n".join(data["notes"]) + "\n" + md
    assert data["assessment_target_manifest"]["assessment_target"] == "auditable_brief"
    assert data["assessment_target_manifest"]["reader_clean_required"] is False
    assert data["assessment_target_manifest"]["audit_binding_status"] == "required_python_owned"
    assert "output/delivery/brief.md" not in data["expected_artifacts"]
    assert "finalize" not in {stage["stage_id"] for stage in data["stage_completion_protocol"]["stages"]}
    ownership_outputs = data["artifact_ownership"]["cli_owned_outputs"]
    assert all("finalize" not in str(item.get("path", "")).lower() for item in ownership_outputs)
    assert all("output/delivery/" not in str(item.get("path", "")).lower() for item in ownership_outputs)
    assert all("briefloop finalize" not in str(item.get("command", "")).lower() for item in ownership_outputs)
    assert all("--stage finalize" not in str(item.get("command", "")).lower() for item in ownership_outputs)
    assert "TARGET COMPLETE: auditable_brief" in text
    assert "auditor quality gates must have status pass before auditor stage-complete" in text
    assert "warning is not enough even when there are 0 blocking findings" in text
    assert "register-run" in text
    assert "score-run" in text
    assert "Do not run finalize" in text
    assert "briefloop finalize" not in text
    assert "state finalize-complete" not in text
    assert "--stage finalize" not in text
    assert "docx_pdf_delivery_quality" in text


def test_start_does_not_generate_brief(tmp_path):
    """start must NOT generate brief.md or claim_ledger.json."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    assert not (ws / "output" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "intermediate" / "audited_brief.md").exists()
    assert not (ws / "output" / "intermediate" / "candidate_claims.json").exists()
    assert not (ws / "output" / "intermediate" / "screened_candidates.json").exists()
    assert not (ws / "output" / "intermediate" / "audit_report.json").exists()
    assert not (ws / "output" / "intermediate" / "feedback_issues.json").exists()
    assert not (ws / "output" / "intermediate" / "repair_plan.json").exists()
    assert not (ws / "output" / "intermediate" / "delta_audit_report.json").exists()
    assert not (ws / "output" / "intermediate" / "quality_gate_report.json").exists()
    assert not (ws / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json").exists()
    assert not (ws / "output" / "intermediate" / "gates" / "finalize_quality_gate_report.json").exists()
    assert not (ws / "output" / "intermediate" / "provenance_graph.json").exists()
    assert (ws / "output" / "intermediate" / "orchestrator_control_switchboard.json").exists()
    assert not (ws / "output" / "intermediate" / "control_selections.json").exists()


# ---------------------------------------------------------------------------
# start — runtime variants
# ---------------------------------------------------------------------------

def test_start_hermes_handoff_contains_delegate_task(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "hermes",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert "delegate_task" in data["prompt"]
    assert "scout" in data["prompt"]
    assert "auditor" in data["prompt"]
    assert "briefloop finalize" in data["prompt"]
    _assert_orchestrator_contract_handoff(data)


def test_start_hermes_output_no_legacy_generate_brief(tmp_path, capsys):
    """start --runtime hermes must not mention legacy /generate-brief in CLI output or handoff."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "hermes",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    cli_output = captured.out
    assert "/generate-brief" not in cli_output

    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert "/generate-brief" not in data["prompt"]


def test_start_claude_output_contains_stage_workflow_command(tmp_path, capsys):
    """start --runtime claude must point the operator at the stage-running command."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "claude",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "/generate-brief" in captured.out
    assert "/briefloop run" in captured.out


def test_start_codex_handoff_uses_root_session_orchestrator(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "codex",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    prompt = data["prompt"]
    assert "/generate-brief" not in prompt
    assert "root Codex session" in prompt
    assert "You are the Orchestrator main agent" in prompt
    assert "Spawn the named Codex custom agent" in prompt
    assert "Do not invoke an orchestrator subagent" in prompt
    assert ".codex/agents/scout.toml" in prompt
    assert ".codex/agents/screener.toml" in prompt
    assert "default: discovery + screening" in prompt
    assert "strict topology or explicit repair/review only" in prompt
    assert ".codex/agents/claim-ledger.toml" in prompt
    assert ".codex/agents/analyst.toml" in prompt
    assert ".codex/agents/editor.toml" in prompt
    assert ".codex/agents/auditor.toml" in prompt
    assert "formatter/finalize -> Python finalize tool" in prompt
    assert "state stage-complete" in prompt
    assert "state finalize-complete" in prompt
    assert "With role_topology=default, Scout performs discovery and screening in one role" in prompt
    assert "do not call `state stage-complete --stage screener`" in prompt
    assert "workspace is trusted in Codex" in prompt
    assert "install Codex runtime assets" in prompt
    assert "Codex writer flow protocol" in prompt
    assert "Workspace Card" in prompt
    assert "Trust status is one Workspace Card line, not the main answer" in prompt
    assert "Do not launch the interactive terminal onboarding wizard inside Codex chat" in prompt
    assert "show the values to be written" in prompt
    assert "Before initializing into an existing directory" in prompt
    assert "output/intermediate/runtime_manifest.json" in prompt
    assert "Source Mode Card" in prompt
    assert "input/sources/" in prompt
    assert "raw excerpt/snippet" in prompt
    assert "Do not call sources decide --search unless web_search.mode is external_api" in prompt
    assert "Do not call sources decide --merge on source_plan_only artifacts" in prompt
    assert "source_candidates.yaml is planning/review only, not evidence" in prompt
    assert "report progress after every successful stage-complete transaction" in prompt
    assert "[stage] produced <artifact> -> stage-complete passed -> next <stage>" in prompt
    _assert_orchestrator_contract_handoff(data)


def test_run_fast_rerun_recipe_requires_fact_layer_import(tmp_path, capsys):
    ws = _write_workspace(tmp_path)

    rc = main([
        "run",
        "--workspace", str(ws),
        "--runtime", "claude",
        "--recipe", "fast-rerun",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])

    assert rc == 1
    out = capsys.readouterr().out
    assert "E_FAST_RERUN_IMPORT_REQUIRED" in out
    assert "briefloop state import-fact-layer" in out
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()


def test_run_fast_rerun_recipe_uses_imported_fact_layer_handoff(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, runtime="claude", repo_workdir=ROOT, recipe="fast-rerun")
    _mark_fact_layer_imported(ws)

    rc = main([
        "run",
        "--workspace", str(ws),
        "--runtime", "claude",
        "--recipe", "fast-rerun",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])

    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    manifest = json.loads((ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8"))
    text = data["prompt"] + "\n" + "\n".join(data["notes"])
    assert data["recipe"] == "fast-rerun"
    assert manifest["recipe"] == "fast-rerun"
    assert manifest["fact_layer_import"]["source_run_id"] == "mabw-20260614T010000Z-source"
    assert main([
        "state",
        "check",
        "--workspace", str(ws),
        "--repo-workdir", str(ROOT),
        "--strict",
    ]) == 0
    after_state_check = json.loads((ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8"))
    assert after_state_check["recipe"] == "fast-rerun"
    assert main([
        "controls",
        "build-switchboard",
        "--workspace", str(ws),
        "--repo-workdir", str(ROOT),
    ]) == 0
    after_switchboard = json.loads((ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8"))
    assert after_switchboard["recipe"] == "fast-rerun"
    assert "Runtime recipe: fast-rerun" in text
    assert "same frozen evidence, new writing -- verified by hash" in text
    assert "Source run: mabw-20260614T010000Z-source" in text
    assert "Imported fact-layer hash" in text
    assert "Start model-backed content work at Analyst" in text
    assert "Do not regenerate source-discovery, input-governance, Scout, Screener, or Claim Ledger" in text
    assert "Do not synthesize or backfill upstream stage-complete" in text
    assert "Then record the pre-analyst successful completions" not in text
    assert not (ws / "output" / "brief.md").exists()


def test_run_fast_rerun_recipe_rejects_missing_imported_file_before_handoff(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, runtime="claude", repo_workdir=ROOT, recipe="fast-rerun")
    _mark_fact_layer_imported(ws)
    (ws / "output" / "intermediate" / "claim_ledger.json").unlink()

    rc = main([
        "run",
        "--workspace", str(ws),
        "--runtime", "claude",
        "--recipe", "fast-rerun",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])

    assert rc == 1
    out = capsys.readouterr().out
    assert "E_FAST_RERUN_IMPORT_REQUIRED" in out
    assert "Imported fact-layer file is missing: output/intermediate/claim_ledger.json" in out
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()
    assert not (ws / "output" / "intermediate" / "agent_handoff.md").exists()
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
    assert not any(event.get("event_type") == "handoff_written" for event in events)


def test_build_handoff_fast_rerun_requires_import_manifest(tmp_path):
    ws = _write_workspace(tmp_path)

    try:
        build_handoff(
            workspace=ws,
            repo_workdir=ROOT,
            runtime="claude",
            recipe="fast-rerun",
            run_doctor=False,
        )
        assert False, "fast-rerun handoff without import should fail"
    except ValueError as exc:
        assert "E_FAST_RERUN_IMPORT_REQUIRED" in str(exc)


def test_start_operator_handoff_contains_compact_runtime_contract(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "operator",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    text = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    assert data["runtime"] == RUNTIME_OPERATOR
    assert data["legacy_runtime_alias"] is None
    assert data["runtime_capabilities"]["runtime"] == RUNTIME_OPERATOR
    assert data["runtime_capabilities"]["delegation_assumed"] is False
    assert data["runtime_capabilities"]["compact_workflow_allowed"] is True
    assert data["runtime_capabilities"]["host_specific_adapter"] is False
    assert data["runtime_capabilities"]["must_not_claim_subagents_ran"] is True
    assert data["runtime_capabilities"]["fallback_if_no_delegation"] == "compact_operator_workflow_or_stop"
    ownership = data["artifact_ownership"]
    assert ownership["schema_version"] == "briefloop.operator_artifact_ownership.v1"
    assert any(item["path"] == "output/intermediate/candidate_claims.json" for item in ownership["agent_owned_drafts"])
    assert any(item["path"] == "output/intermediate/claim_ledger.json" for item in ownership["cli_owned_outputs"])
    assert "output/intermediate/artifact_registry.json" in ownership["read_only_diagnostics"]
    assert "output/intermediate/workflow_state.json" in ownership["forbidden_direct_edits"]
    assert "output/intermediate/gates/*_quality_gate_report.json" in ownership["forbidden_direct_edits"]
    assert "host-agnostic compact operator workflow" in data["prompt"]
    assert "must not claim subagents ran" in data["prompt"]
    assert "Delegate the Scout role only if your host provides real delegation" in data["prompt"]
    assert "Use the 'scout' subagent" not in data["prompt"]
    assert "## Runtime Capabilities" in text
    assert "## Artifact Ownership" in text
    assert "### Agent-Owned Drafts" in text
    assert "### CLI-Owned Outputs" in text
    assert "### Read-Only Diagnostics" in text
    assert "### Forbidden Direct Edits" in text
    assert "`output/intermediate/workflow_state.json`" in text
    assert "`output/intermediate/gates/*_quality_gate_report.json`" in text
    assert "candidate_claims.json" in data["prompt"]
    assert "briefloop finalize" in data["prompt"]
    _assert_orchestrator_contract_handoff(data)


def test_start_codebuddy_handoff_contains_project_skill_and_role_agent_contract(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "codebuddy",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    text = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")

    assert data["runtime"] == RUNTIME_CODEBUDDY
    capabilities = data["runtime_capabilities"]
    assert capabilities["runtime"] == RUNTIME_CODEBUDDY
    assert capabilities["host"] == "codebuddy"
    assert capabilities["delegation_supported"] is True
    assert capabilities["nested_subagents_supported"] is False
    assert capabilities["skill_path"] == ".codebuddy/skills/briefloop/SKILL.md"
    assert capabilities["role_agent_path_glob"] == ".codebuddy/agents/briefloop-*.md"
    assert capabilities["main_session_runs_cli_transactions"] is True
    assert capabilities["role_agents_run_cli_transactions"] is False
    assert capabilities["must_not_claim_uninvoked_subagents_ran"] is True
    assert capabilities["subagent_names"] == [
        "briefloop-scout",
        "briefloop-claim-ledger",
        "briefloop-analyst",
        "briefloop-editor",
        "briefloop-auditor",
        "briefloop-formatter",
    ]

    assert ".codebuddy/skills/briefloop/SKILL.md" in data["prompt"]
    assert "Do not add or use `context: fork`" in data["prompt"]
    assert "The main CodeBuddy session is the Orchestrator" in data["prompt"]
    assert "CodeBuddy sub-agents cannot spawn other sub-agents" in data["prompt"]
    assert "Role sub-agents must not run `briefloop` or `multi-agent-brief` CLI commands" in data["prompt"]
    assert "briefloop-claim-ledger" in data["prompt"]
    assert "output/intermediate/claim_drafts.json" in data["prompt"]
    assert "freeze-claim-ledger" in data["prompt"]
    assert "briefloop-formatter` for finalize readiness only" in data["prompt"]
    assert "The main CodeBuddy session owns deterministic BriefLoop CLI transactions" in data["prompt"]
    workflow = data["prompt"].split("CodeBuddy stage workflow:", 1)[1]
    assert workflow.index("briefloop-scout") < workflow.index("briefloop-claim-ledger")
    assert workflow.index("briefloop-claim-ledger") < workflow.index("briefloop-analyst")
    assert "## Runtime Capabilities" in text
    assert "`runtime`: `codebuddy`" in text
    assert "briefloop-scout" in text
    assert "briefloop-claim-ledger" in text
    assert "gate authority" in " ".join(data["notes"])
    assert "delivery approval" in " ".join(data["notes"])
    assert "release authority" in " ".join(data["notes"])
    _assert_orchestrator_contract_handoff(data)


def test_start_codebuddy_handoff_fails_without_source_clone_assets(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    package_base = _write_packaged_contract_base(tmp_path)

    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "codebuddy",
        "--repo-workdir", str(package_base),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])

    assert rc == 1
    output = capsys.readouterr().out
    assert "CodeBuddy runtime is source-clone-only" in output
    assert ".codebuddy/skills/briefloop/SKILL.md" in output
    assert ".codebuddy/agents/briefloop-scout.md" in output
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()


def test_start_manual_alias_resolves_to_operator_and_warns(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "manual",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert 'runtime "manual" is a legacy alias for "operator"' in captured.err
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert data["runtime"] == RUNTIME_OPERATOR
    assert data["legacy_runtime_alias"] == "manual"
    assert data["runtime_capabilities"]["runtime"] == RUNTIME_OPERATOR
    assert data["runtime_capabilities"]["legacy_runtime_alias"] == "manual"
    assert data["artifact_ownership"]["schema_version"] == "briefloop.operator_artifact_ownership.v1"
    assert "candidate_claims.json" in data["prompt"]
    assert "briefloop finalize" in data["prompt"]
    _assert_orchestrator_contract_handoff(data)


# ---------------------------------------------------------------------------
# handoff
# ---------------------------------------------------------------------------

def test_handoff_with_config_generates_artifacts(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "handoff",
        "--config", str(ws / "config.yaml"),
        "--runtime", "hermes",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    assert (ws / "output" / "intermediate" / "agent_handoff.md").exists()
    assert (ws / "output" / "intermediate" / "agent_handoff.json").exists()
    assert (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert (ws / "output" / "intermediate" / "workflow_state.json").exists()
    assert (ws / "output" / "intermediate" / "artifact_registry.json").exists()
    assert (ws / "output" / "intermediate" / "event_log.jsonl").exists()
    assert (ws / "output" / "intermediate" / "audience_profile_snapshot.md").exists()
    assert (ws / "output" / "intermediate" / "orchestrator_control_switchboard.json").exists()
    assert not (ws / "output" / "intermediate" / "control_selections.json").exists()
    handoff_md = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    assert "Optional chunk parallelism" in handoff_md
    assert "scratch only: `True`" in handoff_md
    assert "Join chunk outputs deterministically before writing workflow artifacts" in handoff_md
    assert "append to candidate_claims.json from chunk workers" in handoff_md
    assert "Do not replay: delegate screener, state stage-complete --stage screener" in handoff_md


def test_handoff_no_config_fails(tmp_path):
    rc = main(["handoff", "--config", str(tmp_path / "nonexistent" / "config.yaml"), "--skip-doctor"])
    assert rc != 0


# ---------------------------------------------------------------------------
# build_handoff direct unit tests
# ---------------------------------------------------------------------------

def test_build_handoff_hermes_has_delegate_task(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="hermes",
        venv="/tmp/.venv/bin/activate",
        run_doctor=False,
    )
    assert handoff.runtime == "hermes"
    assert "delegate_task" in handoff.prompt
    assert "scout" in handoff.prompt
    assert "default topology" in handoff.prompt
    assert "screened_candidates.json" in handoff.prompt
    assert "auditor" in handoff.prompt
    assert "/generate-brief" not in handoff.prompt
    _assert_orchestrator_contract_handoff(handoff.to_dict())


def test_build_handoff_claude_has_stage_workflow_command(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="claude",
        venv="/tmp/.venv/bin/activate",
        run_doctor=False,
    )
    assert "/generate-brief" in handoff.next_steps
    assert "/generate-brief" in handoff.prompt
    assert "/briefloop run" in handoff.prompt
    assert "only to create or refresh handoff files" in handoff.prompt
    assert "does not execute specialists or complete stages" in handoff.prompt
    assert "With role_topology=default, Scout performs discovery and screening in one role" in handoff.prompt
    assert "do not call `state stage-complete --stage screener` in default topology" in handoff.prompt
    assert "strict: scout → screener" in handoff.prompt
    _assert_orchestrator_contract_handoff(handoff.to_dict())


def test_build_handoff_codex_maps_specialists_to_custom_agents(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="codex",
        venv="/tmp/.venv/bin/activate",
        run_doctor=False,
    )
    assert handoff.runtime == "codex"
    assert "/generate-brief" not in handoff.prompt
    assert "root Codex session" in handoff.prompt
    assert "Spawn the named Codex custom agent" in handoff.prompt
    assert ".codex/agents/scout.toml" in handoff.prompt
    assert "default: discovery + screening" in handoff.prompt
    assert "do not call `state stage-complete --stage screener` in default topology" in handoff.prompt
    assert "strict topology or explicit repair/review only" in handoff.prompt
    assert ".codex/agents/claim-ledger.toml" in handoff.prompt
    assert "Do not call the next specialist until" in handoff.prompt
    assert "state stage-complete" in handoff.prompt
    assert "state finalize-complete" in handoff.prompt
    assert "workspace is trusted in Codex" in handoff.prompt
    assert "Codex writer flow protocol" in handoff.prompt
    assert "Workspace Card" in handoff.prompt
    assert "Source Mode Card" in handoff.prompt
    assert "input/sources/" in handoff.prompt
    assert "Do not call sources decide --search unless web_search.mode is external_api" in handoff.prompt
    assert "Do not call sources decide --merge on source_plan_only artifacts" in handoff.prompt
    assert "source_candidates.yaml is planning/review only, not evidence" in handoff.prompt
    assert "Do not launch the interactive terminal onboarding wizard inside Codex chat" in handoff.prompt
    assert "[stage] produced <artifact> -> stage-complete passed -> next <stage>" in handoff.prompt
    assert any("Codex must trust the workspace" in note for note in handoff.notes)
    _assert_orchestrator_contract_handoff(handoff.to_dict())


def test_build_handoff_codebuddy_uses_project_skill_and_role_agents(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="codebuddy",
        venv="/tmp/.venv/bin/activate",
        run_doctor=False,
    )
    assert handoff.runtime == "codebuddy"
    assert handoff.runtime_capabilities["delegation_supported"] is True
    assert handoff.runtime_capabilities["nested_subagents_supported"] is False
    assert handoff.runtime_capabilities["role_agent_path_glob"] == ".codebuddy/agents/briefloop-*.md"
    assert handoff.runtime_capabilities["subagent_names"] == [
        "briefloop-scout",
        "briefloop-claim-ledger",
        "briefloop-analyst",
        "briefloop-editor",
        "briefloop-auditor",
        "briefloop-formatter",
    ]
    assert ".codebuddy/skills/briefloop/SKILL.md" in handoff.prompt
    assert "briefloop-scout" in handoff.prompt
    assert "briefloop-claim-ledger" in handoff.prompt
    assert "briefloop-auditor" in handoff.prompt
    assert "output/intermediate/claim_drafts.json" in handoff.prompt
    assert "freeze-claim-ledger" in handoff.prompt
    assert "briefloop-formatter` for finalize readiness only" in handoff.prompt
    assert "Role sub-agents must not run `briefloop` or `multi-agent-brief` CLI commands" in handoff.prompt
    assert "The main CodeBuddy session owns deterministic BriefLoop CLI transactions" in handoff.prompt
    assert "Do not add or use `context: fork`" in handoff.prompt
    assert "CodeBuddy sub-agents cannot spawn other sub-agents" in handoff.prompt
    workflow = handoff.prompt.split("CodeBuddy stage workflow:", 1)[1]
    assert workflow.index("briefloop-scout") < workflow.index("briefloop-claim-ledger")
    assert workflow.index("briefloop-claim-ledger") < workflow.index("briefloop-analyst")
    assert "This runtime handoff does not add gate authority" in " ".join(handoff.notes)
    _assert_orchestrator_contract_handoff(handoff.to_dict())


def test_build_handoff_codebuddy_requires_source_clone_assets(tmp_path):
    ws = _write_workspace(tmp_path)
    package_base = _write_packaged_contract_base(tmp_path)
    try:
        build_handoff(
            workspace=ws,
            repo_workdir=package_base,
            runtime="codebuddy",
            venv="/tmp/.venv/bin/activate",
            run_doctor=False,
        )
        assert False, "Should have rejected missing CodeBuddy source assets"
    except ValueError as exc:
        message = str(exc)
    assert "CodeBuddy runtime is source-clone-only" in message
    assert ".codebuddy/skills/briefloop/SKILL.md" in message
    assert ".codebuddy/agents/briefloop-formatter.md" in message


def test_build_handoff_unknown_runtime_raises(tmp_path):
    ws = _write_workspace(tmp_path)
    try:
        build_handoff(
            workspace=ws,
            repo_workdir=ROOT,
            runtime="nonexistent",
            run_doctor=False,
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unknown runtime" in str(e)


def test_build_handoff_all_runtimes_valid(tmp_path):
    """Every declared valid runtime must build without error."""
    ws = _write_workspace(tmp_path)
    for runtime in VALID_RUNTIMES:
        handoff = build_handoff(
            workspace=ws,
            repo_workdir=ROOT,
            runtime=runtime,
            run_doctor=False,
        )
        # auto resolves to hermes in v0.5.5
        if runtime == "auto":
            assert handoff.runtime == "hermes"
        elif runtime == "manual":
            assert handoff.runtime == RUNTIME_OPERATOR
            assert handoff.legacy_runtime_alias == "manual"
        else:
            assert handoff.runtime == runtime
        assert len(handoff.expected_artifacts) >= 2
        assert len(handoff.prompt) > 50
        _assert_orchestrator_contract_handoff(handoff.to_dict())


# ---------------------------------------------------------------------------
# write_handoff_artifacts
# ---------------------------------------------------------------------------

def test_write_handoff_artifacts_writes_both_files(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="hermes",
        run_doctor=False,
    )
    md_path, json_path = write_handoff_artifacts(handoff, ws)
    assert md_path.suffix == ".md"
    assert json_path.suffix == ".json"
    assert md_path.exists()
    assert json_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "# Agent Handoff" in md_content
    assert "## Contract References" in md_content
    assert "## Runtime State Files" in md_content
    assert "## Audience Memory Files" in md_content
    assert "## Improvement Memory Files" in md_content
    assert "## Control Switchboard Files" in md_content
    assert "## Feedback State Files" in md_content
    assert "## Provenance State Files" in md_content
    assert "## Stage Completion Protocol" in md_content
    assert "Stage completion is artifact-based" in md_content
    assert "`candidate_claims` at `output/intermediate/candidate_claims.json`" in md_content
    assert "`orchestrator_contract`: `configs/orchestrator_contract.yaml`" in md_content
    assert "`runtime_manifest`: `output/intermediate/runtime_manifest.json`" in md_content
    assert "`audience_profile_snapshot`: `output/intermediate/audience_profile_snapshot.md`" in md_content
    assert "`orchestrator_control_switchboard`: `output/intermediate/orchestrator_control_switchboard.json`" in md_content
    assert "`control_selections`: `output/intermediate/control_selections.json`" in md_content
    assert "`feedback_issues`: `output/intermediate/feedback_issues.json`" in md_content
    assert "`provenance_graph`: `output/intermediate/provenance_graph.json`" in md_content
    assert "delegate_task" in md_content
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "hermes"
    _assert_orchestrator_contract_handoff(data)


def test_render_handoff_cli_contains_runtime(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="opencode",
        run_doctor=False,
    )
    output = render_handoff_cli(handoff)
    assert "opencode" in output
    assert str(ws.resolve()) in output


# ---------------------------------------------------------------------------
# run command — launcher identity
# ---------------------------------------------------------------------------

def test_run_help_does_not_contain_deprecated(capsys):
    """run --help must not contain deprecated/prepare/deterministic pipeline language."""
    try:
        main(["run", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out
    assert "deprecated" not in output.lower()
    assert "deterministic pipeline" not in output.lower()
    assert "never generates" not in output.lower()


def test_run_default_auto_resolves_to_hermes(tmp_path):
    """Default run (--runtime auto) must resolve to hermes handoff."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "run",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert data["runtime"] == "hermes"
    assert "delegate_task" in data["prompt"]
    assert "/generate-brief" not in data["prompt"]
    _assert_orchestrator_contract_handoff(data)
    manifest = json.loads((ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8"))
    assert manifest["improvement"]["ledger_sha256"] is None
    assert isinstance(manifest["improvement"]["memory_sha256"], str)
    assert manifest["improvement"]["memory_sha256"]
    assert manifest["improvement"]["snapshot_path"] is None
    assert manifest["improvement"]["snapshot_sha256"] is None
    assert manifest["improvement"]["materialized_entry_ids"] == []


def test_run_claude_contains_stage_workflow_command(tmp_path):
    """run --runtime claude must contain the command that actually runs stages."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "run",
        "--workspace", str(ws),
        "--runtime", "claude",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert "/generate-brief" in data["next_steps"]
    assert "/generate-brief" in data["prompt"]
    assert "/briefloop run" in data["prompt"]
    assert "only to create or refresh handoff files" in data["prompt"]


def test_run_does_not_generate_brief(tmp_path):
    """run must NOT generate brief.md or claim_ledger.json."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "run",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    assert not (ws / "output" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "intermediate" / "provenance_graph.json").exists()
    assert (ws / "output" / "intermediate" / "orchestrator_control_switchboard.json").exists()
    assert not (ws / "output" / "intermediate" / "control_selections.json").exists()


def test_prepare_output_points_to_run(capsys):
    """prepare must only point to briefloop run, nothing else."""
    try:
        main(["prepare", "--config", "/tmp/nonexistent/config.yaml"])
    except SystemExit:
        pass
    output = capsys.readouterr().out + capsys.readouterr().err
    assert "briefloop run --workspace <workspace>" in output
    assert "/generate-brief" not in output
    assert "Python pipeline" not in output
    assert "deterministic pipeline" not in output


# ---------------------------------------------------------------------------
# onboard command discoverability
# ---------------------------------------------------------------------------

def test_onboard_help_exists(capsys):
    """onboard --help must exist as a discoverable command."""
    try:
        main(["onboard", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out
    assert "onboard" in output
    assert "onboarding" in output.lower()


def test_init_help_mentions_onboard(capsys):
    """init --help must reference onboard as the first step."""
    try:
        main(["init", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out
    assert "onboard" in output


def test_run_no_workspace_mentions_onboard(tmp_path, capsys):
    """run without a workspace must suggest onboard as the first path."""
    rc = main(["run", "--workspace", str(tmp_path / "no-such-ws"), "--skip-doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    output = captured.out
    assert "briefloop onboard" in output
    assert "briefloop init" in output
    assert "--from-onboarding onboarding.json" in output


def test_init_demo_mentions_onboard(tmp_path, capsys):
    """init --demo must say it's a demo and point to onboard for real projects."""
    ws = tmp_path / "demo-ws"
    rc = main(["init", str(ws), "--demo", "--force"])
    assert rc == 0
    captured = capsys.readouterr()
    output = captured.out
    assert "demo" in output.lower()
    assert "briefloop onboard" in output
    assert "input/context" in output
    assert "example brief Markdown" in output
    input_readme = (ws / "input" / "README.md").read_text(encoding="utf-8")
    context_readme = (ws / "input" / "context" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "prior weekly reports" in input_readme
    assert "input/context/" in input_readme
    assert "previous_weekly_reference.md" in context_readme
