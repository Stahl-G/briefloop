"""Tests for multi-agent-brief start / handoff launcher."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.cli.start_commands import (
    CONTRACT_REFERENCES,
    VALID_RUNTIMES,
    build_handoff,
    render_handoff_cli,
    write_handoff_artifacts,
)
from multi_agent_brief.orchestrator_contract import contract_references_exist
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.orchestrator.runtime_state import RUNTIME_STATE_FILES
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state, runtime_state_paths
from multi_agent_brief.audience_memory import AUDIENCE_MEMORY_FILES
from multi_agent_brief.controls.contract import CONTROL_SWITCHBOARD_FILES
from multi_agent_brief.feedback.feedback_contract import FEEDBACK_STATE_FILES
from multi_agent_brief.quality_gates.contract import QUALITY_GATE_STATE_FILES
from multi_agent_brief.provenance.contract import PROVENANCE_STATE_FILES


ROOT = Path(__file__).resolve().parent.parent


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        """
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
        encoding="utf-8",
    )
    (ws / "user.md").write_text("# Test User Profile\n\nCompany: TestCo\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        """
source_strategy:
  profile: "conservative"
  enabled_providers:
    - "manual"
manual:
  enabled: true
  sources: []
""".strip(),
        encoding="utf-8",
    )
    return ws


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
    assert protocol["schema_version"] == "multi-agent-brief-stage-completion-protocol/v1"
    assert protocol["status"] == "canonical_handoff_protocol"
    assert any("artifact-based" in rule for rule in protocol["rules"])
    protocol_stages = {stage["stage_id"]: stage for stage in protocol["stages"]}
    assert protocol_stages["scout"]["required_output_artifacts"] == [
        {
            "artifact_id": "candidate_claims",
            "path": "output/intermediate/candidate_claims.json",
            "required": True,
            "format": "json",
        }
    ]
    screener_protocol = protocol_stages["screener"]
    assert screener_protocol["topology_satisfaction"]["default"]["satisfied_by"] == "scout"
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
    assert screener_protocol["independent_completion_topologies"] == ["strict"]
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
    assert {"audited_brief", "claim_ledger", "input_classification"} <= editor_inputs
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
    assert "multi-agent-brief state stage-complete --workspace <workspace>" in text
    assert "multi-agent-brief state finalize-complete --workspace <workspace>" in text
    assert "state decide" in text
    assert "next_allowed_decisions" in text
    assert "Stage completion protocol" in text
    assert "MUST produce" in text
    assert "topology satisfaction: default: satisfied by scout" in text
    assert "independent MUST produce (strict): screened_candidates at output/intermediate/screened_candidates.json" in text
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
    assert "multi-agent-brief gates check --workspace <workspace> --stage auditor" in text
    assert "multi-agent-brief gates check --workspace <workspace> --stage finalize" in text
    assert "multi-agent-brief state check --workspace <workspace> --strict" in text
    assert "Did 0 searches" in text
    assert "every query returns an empty result set" in text
    assert "Do not switch to source-planner" in text
    assert "Audit and quality gates passed" in text
    assert "do not finalize" in text
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
    assert "No workspace found" in output or "multi-agent-brief init" in output


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
    assert "multi-agent-brief finalize" in data["prompt"]
    _assert_orchestrator_contract_handoff(data)


def test_start_hermes_output_no_generate_brief(tmp_path, capsys):
    """start --runtime hermes must not mention /generate-brief in CLI output or handoff."""
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


def test_start_claude_output_contains_generate_brief(tmp_path, capsys):
    """start --runtime claude must mention /generate-brief."""
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
    assert "multi-agent-brief state import-fact-layer" in out
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


def test_start_manual_handoff_contains_artifact_contract(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "manual",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert "candidate_claims.json" in data["prompt"]
    assert "multi-agent-brief finalize" in data["prompt"]
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


def test_build_handoff_claude_has_generate_brief(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="claude",
        venv="/tmp/.venv/bin/activate",
        run_doctor=False,
    )
    assert "/generate-brief" in handoff.prompt
    assert "With role_topology=default, Scout performs discovery and screening in one role" in handoff.prompt
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


def test_run_claude_contains_generate_brief(tmp_path):
    """run --runtime claude must contain /generate-brief."""
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
    assert "/generate-brief" in data["prompt"]


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
    """prepare must only point to multi-agent-brief run, nothing else."""
    try:
        main(["prepare", "--config", "/tmp/nonexistent/config.yaml"])
    except SystemExit:
        pass
    output = capsys.readouterr().out + capsys.readouterr().err
    assert "multi-agent-brief run --workspace <workspace>" in output
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
    assert "multi-agent-brief onboard" in output
    assert "multi-agent-brief init" in output
    assert "--from-onboarding onboarding.json" in output


def test_init_demo_mentions_onboard(tmp_path, capsys):
    """init --demo must say it's a demo and point to onboard for real projects."""
    ws = tmp_path / "demo-ws"
    rc = main(["init", str(ws), "--demo", "--force"])
    assert rc == 0
    captured = capsys.readouterr()
    output = captured.out
    assert "demo" in output.lower()
    assert "multi-agent-brief onboard" in output
    assert "input/context" in output
    assert "example brief Markdown" in output
    input_readme = (ws / "input" / "README.md").read_text(encoding="utf-8")
    context_readme = (ws / "input" / "context" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "prior weekly reports" in input_readme
    assert "input/context/" in input_readme
    assert "previous_weekly_reference.md" in context_readme
