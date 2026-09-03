from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_root_and_packaged_runtime_contracts_are_byte_identical() -> None:
    root = ROOT / "configs/orchestrator_contract.yaml"
    packaged = ROOT / "src/multi_agent_brief/configs/orchestrator_contract.yaml"
    assert root.read_bytes() == packaged.read_bytes()
