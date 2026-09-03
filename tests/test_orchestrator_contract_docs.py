from __future__ import annotations

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
