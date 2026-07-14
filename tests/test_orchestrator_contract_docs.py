from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from multi_agent_brief.orchestrator_contract import HISTORICAL_READ_ONLY_RUNTIMES
from multi_agent_brief.orchestrator_contract import RUNTIME_OPERATOR
from multi_agent_brief.orchestrator_contract import VALID_RUNTIMES


ROOT = Path(__file__).resolve().parent.parent

ORCHESTRATOR_CONTRACT = ROOT / "configs" / "orchestrator_contract.yaml"
STAGE_SPECS = ROOT / "configs" / "stage_specs.yaml"
ARTIFACT_CONTRACTS = ROOT / "configs" / "artifact_contracts.yaml"
DEFAULT_POLICY_PACK = ROOT / "configs" / "policy_packs" / "default.yaml"
PACKAGE_CONTRACT_BASE = ROOT / "src" / "multi_agent_brief"
DRAFT_PROMOTE_MATRIX = ROOT / "docs" / "implementation" / "draft-promote-ownership-matrix.md"

EXPECTED_DECISIONS = {
    "continue",
    "retry_stage",
    "delegate_repair",
    "request_human_review",
    "block_run",
    "finalize",
}

EXPECTED_STAGE_ORDER = [
    "doctor",
    "source-discovery",
    "input-governance",
    "scout",
    "screener",
    "claim-ledger",
    "analyst",
    "editor",
    "auditor",
    "finalize",
]


def _load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} must contain a YAML mapping"
    return payload


def test_orchestrator_contract_files_exist_and_parse():
    for path in (
        ORCHESTRATOR_CONTRACT,
        STAGE_SPECS,
        ARTIFACT_CONTRACTS,
        DEFAULT_POLICY_PACK,
    ):
        assert path.exists(), f"missing contract source: {path.relative_to(ROOT)}"
        data = _load_yaml(path)
        assert data["schema_version"].startswith("multi-agent-brief-")


def test_packaged_contract_files_match_public_contracts():
    for rel_path in (
        "configs/orchestrator_contract.yaml",
        "configs/stage_specs.yaml",
        "configs/artifact_contracts.yaml",
        "configs/policy_packs/default.yaml",
    ):
        public_path = ROOT / rel_path
        package_path = PACKAGE_CONTRACT_BASE / rel_path
        assert package_path.exists(), f"missing packaged contract: {rel_path}"
        assert package_path.read_text(encoding="utf-8") == public_path.read_text(encoding="utf-8")


def test_orchestrator_contract_defines_main_agent_and_decisions():
    contract = _load_yaml(ORCHESTRATOR_CONTRACT)

    assert contract["orchestrator"]["role"] == "main_agent"
    assert contract["orchestrator"]["authority"] == "runtime_controller"
    assert set(contract["decision_vocabulary"]) == EXPECTED_DECISIONS
    assert contract["v060_boundaries"]["deferred"] == [
        "persisted_workflow_state",
        "artifact_registry_execution",
        "feedback_repair_loop",
        "evidence_execution_graph",
        "public_safe_evaluation_cases",
    ]
    assert contract["v061_boundaries"]["implements"] == [
        "persisted_runtime_state_control_files",
        "minimum_artifact_registry_status_check",
        "stage_scoped_blocking_summary",
        "orchestrator_decision_event_entrypoint",
    ]
    assert "feedback_repair_loop" in contract["v061_boundaries"]["deferred"]
    assert contract["v062_boundaries"]["implements"] == [
        "feedback_issue_handling",
        "bounded_repair_planning",
        "feedback_event_trace",
        "current_stage_feedback_blocking",
    ]
    assert "automatic_repair_execution" in contract["v062_boundaries"]["deferred"]
    assert "semantic_repair_verification" in contract["v062_boundaries"]["deferred"]
    assert contract["v063_boundaries"]["implements"] == [
        "deterministic_material_fact_gate",
        "deterministic_freshness_gate",
        "deterministic_target_relevance_gate",
        "quality_gate_report_control_artifact",
        "current_stage_quality_gate_blocking",
    ]
    assert "live_market_quote_fetching" in contract["v063_boundaries"]["deferred"]
    assert "semantic_truth_judgment" in contract["v063_boundaries"]["deferred"]
    assert contract["v064_boundaries"]["implements"] == [
        "packaged_public_safe_evaluation_cases",
        "structured_eval_case_action_dispatch",
        "fixture_leakage_scanner",
        "gates_feedback_runtime_static_regression_cases",
    ]
    assert "llm_as_judge_prose_scoring" in contract["v064_boundaries"]["deferred"]
    assert "private_commercial_benchmark_suites" in contract["v064_boundaries"]["deferred"]
    assert contract["v065_boundaries"]["implements"] == [
        "deterministic_provenance_projection",
        "workspace_local_audit_graph",
        "provenance_graph_validation",
        "provenance_event_trace",
    ]
    assert "semantic_truth_verification" in contract["v065_boundaries"]["deferred"]
    assert "workflow_dag_runtime" in contract["v065_boundaries"]["deferred"]

    refs = contract["orchestrator"]["contract_references"]
    for rel_path in refs.values():
        assert (ROOT / rel_path).exists(), f"missing contract reference: {rel_path}"


def test_orchestrator_contract_runtime_surfaces_match_handoff_contract():
    contract = _load_yaml(ORCHESTRATOR_CONTRACT)
    runtime_surfaces = contract["runtime_surfaces"]

    assert RUNTIME_OPERATOR in runtime_surfaces
    assert runtime_surfaces == list(VALID_RUNTIMES)
    assert set(runtime_surfaces).isdisjoint(HISTORICAL_READ_ONLY_RUNTIMES)


def test_stage_specs_use_shared_decision_vocabulary_and_order():
    stages = _load_yaml(STAGE_SPECS)["workflow"]["stages"]

    assert [stage["stage_id"] for stage in stages] == EXPECTED_STAGE_ORDER
    for stage in stages:
        decisions = set(stage["allowed_decisions"])
        assert decisions <= EXPECTED_DECISIONS, stage["stage_id"]
        assert decisions, f"{stage['stage_id']} must declare decisions"


def test_artifact_contracts_match_stage_specs():
    stages = _load_yaml(STAGE_SPECS)["workflow"]["stages"]
    artifacts = _load_yaml(ARTIFACT_CONTRACTS)["artifacts"]

    artifact_ids = {artifact["artifact_id"] for artifact in artifacts}
    stage_ids = {stage["stage_id"] for stage in stages}

    for stage in stages:
        for artifact_id in stage.get("expected_artifacts", []):
            assert artifact_id in artifact_ids, (
                f"{stage['stage_id']} expects unknown artifact {artifact_id}"
            )

    for artifact in artifacts:
        if artifact["producer_stage"] not in stage_ids:
            assert artifact["producer_kind"] == "control_tool"
            assert artifact["required"] is False
        else:
            assert artifact["producer_stage"] in stage_ids
        for consumer_stage in artifact["consumer_stages"]:
            assert consumer_stage in stage_ids
        assert set(artifact["allowed_decisions"]) <= EXPECTED_DECISIONS


def test_source_candidates_is_reference_only_not_scout_input():
    artifacts = _load_yaml(ARTIFACT_CONTRACTS)["artifacts"]
    source_candidates = next(
        artifact for artifact in artifacts if artifact["artifact_id"] == "source_candidates"
    )

    assert source_candidates["required"] is False
    assert source_candidates["validation_result"] == "reference_only"
    assert "scout" not in source_candidates["consumer_stages"]


def test_artifact_contracts_preserve_future_provenance_fields():
    contract = _load_yaml(ARTIFACT_CONTRACTS)["artifact_contract"]
    required_fields = {
        "artifact_id",
        "path",
        "producer_stage",
        "producer_role",
        "consumer_stages",
        "validation_result",
        "blocking_reason",
        "allowed_decisions",
        "retry_or_human_review_decision",
    }

    assert required_fields <= set(contract["provenance_ready_fields"])
    assert contract["producer_kind_values"] == ["workflow_stage", "control_tool"]
    assert "artifact_derived_from" in contract["edge_direction_notes"]
    assert "derived/output artifact" in contract["edge_direction_notes"]["artifact_derived_from"]
    for artifact in _load_yaml(ARTIFACT_CONTRACTS)["artifacts"]:
        assert required_fields <= set(artifact), artifact["artifact_id"]


def test_production_modules_do_not_import_experiment_target_contract():
    allowed = {
        "src/multi_agent_brief/experiments/target_contract.py",
    }
    violations: list[str] = []
    for path in (ROOT / "src" / "multi_agent_brief").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "multi_agent_brief.experiments.target_contract":
                violations.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "multi_agent_brief.experiments.target_contract":
                        violations.append(f"{rel}:{node.lineno}")

    assert violations == []


def test_experiment_080_imports_contract_target_contract():
    text = (ROOT / "src" / "multi_agent_brief" / "experiments" / "experiment_080.py").read_text(
        encoding="utf-8"
    )

    assert "from multi_agent_brief.contracts.target_contract import" in text
    assert "from multi_agent_brief.experiments.target_contract import" not in text


def test_evaluation_and_onboarding_modules_do_not_import_cli_layer():
    roots = [
        ROOT / "src" / "multi_agent_brief" / "evaluation_cases",
        ROOT / "src" / "multi_agent_brief" / "onboarding",
    ]
    violations: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("multi_agent_brief.cli"):
                    violations.append(f"{rel}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("multi_agent_brief.cli"):
                            violations.append(f"{rel}:{node.lineno}")

    assert violations == []


def test_draft_promote_ownership_matrix_stays_non_authoritative_docs() -> None:
    text = DRAFT_PROMOTE_MATRIX.read_text(encoding="utf-8")

    for phrase in [
        "agent-owned draft -> deterministic Python validation/promotion -> authoritative artifact",
        "`agent_owned_draft`",
        "`python_promoted_authoritative`",
        "`python_only_control`",
        "`human_approval_record`",
        "`projection_only`",
        "`reader_delivery`",
        "`claim_drafts.json`",
        "`claim_ledger.json`",
        "`workflow_state.json`",
        "`event_log.jsonl`",
        "`quality_panel.json`",
        "`output/delivery/brief.md`",
        "does not add a runtime, stage, artifact schema, validator, gate, delivery approval",
        "make WorkBuddy, operator runtime, or any unadapted host a delegated runtime",
        "claim output-quality improvement or semantic correctness",
    ]:
        assert phrase in text


def test_v060_public_overview_uses_precise_boundary():
    text = (ROOT / "docs" / "implementation" / "v0.6.0-explicit-orchestrator-contract.md").read_text(
        encoding="utf-8"
    )
    assert (
        "v0.6.0 establishes shared Orchestrator authority, decision vocabulary, "
        "contract references, and runtime role parity."
    ) in text
    assert "It does not persist runtime state or execute artifact registry validation." in text
    assert "artifact identity" in text
    assert "producer stage or role" in text
    assert "consumer stage or role" in text


def test_orchestrator_architecture_docs_define_v060_boundary():
    for rel_path in (
        "docs/orchestrator-architecture.md",
        "docs/orchestrator-architecture.zh-CN.md",
    ):
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        assert "runtime main agent" in text
        assert "configs/orchestrator_contract.yaml" in text
        assert "configs/stage_specs.yaml" in text
        assert "configs/artifact_contracts.yaml" in text
        assert "does not" in text or "不实现" in text
        assert "artifact identity" in text
        assert "producer stage or role" in text


def test_public_roadmap_implementation_links_resolve():
    for file_name in ("docs/roadmap.md", "docs/roadmap.zh-CN.md"):
        path = ROOT / file_name
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#")):
                continue
            assert (path.parent / target).exists(), f"{file_name} links missing {target}"
