from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent

ORCHESTRATOR_CONTRACT = ROOT / "configs" / "orchestrator_contract.yaml"
STAGE_SPECS = ROOT / "configs" / "stage_specs.yaml"
ARTIFACT_CONTRACTS = ROOT / "configs" / "artifact_contracts.yaml"
DEFAULT_POLICY_PACK = ROOT / "configs" / "policy_packs" / "default.yaml"
PACKAGE_CONTRACT_BASE = ROOT / "src" / "multi_agent_brief"

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
        "public_golden_cases",
    ]

    refs = contract["orchestrator"]["contract_references"]
    for rel_path in refs.values():
        assert (ROOT / rel_path).exists(), f"missing contract reference: {rel_path}"


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
        assert artifact["producer_stage"] in stage_ids
        for consumer_stage in artifact["consumer_stages"]:
            assert consumer_stage in stage_ids
        assert set(artifact["allowed_decisions"]) <= EXPECTED_DECISIONS


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
    for artifact in _load_yaml(ARTIFACT_CONTRACTS)["artifacts"]:
        assert required_fields <= set(artifact), artifact["artifact_id"]


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
