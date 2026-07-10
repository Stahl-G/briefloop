from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.orchestrator.recovery_state import evaluate_recovery_state
from multi_agent_brief.orchestrator.runtime_state import (
    RUNTIME_STATE_FILES,
    RuntimeStateError,
    check_runtime_state,
    complete_stage_transaction,
    initialize_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state.errors import E_TRANSACTION_INTEGRITY
from multi_agent_brief.orchestrator.runtime_state.event_log import append_event
from tests.helpers import write_minimal_workspace_under


ROOT = Path(__file__).resolve().parent.parent


def _state_path(workspace: Path, key: str) -> Path:
    return workspace / RUNTIME_STATE_FILES[key]


def _control_bytes(workspace: Path) -> dict[str, bytes]:
    return {
        key: _state_path(workspace, key).read_bytes()
        for key in (
            "runtime_manifest",
            "workflow_state",
            "artifact_registry",
            "event_log",
        )
    }


def test_stage_complete_rejects_unrecovered_contamination_without_writes(
    tmp_path: Path,
) -> None:
    workspace = write_minimal_workspace_under(
        tmp_path,
        include_input_dir=True,
        include_output_dir=True,
    )
    initialize_runtime_state(workspace=workspace, repo_workdir=ROOT)
    check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    manifest = json.loads(
        _state_path(workspace, "runtime_manifest").read_text(encoding="utf-8")
    )
    append_event(
        workspace=workspace,
        run_id=manifest["run_id"],
        event_type="run_integrity_contaminated",
        actor="orchestrator",
        stage_id="doctor",
        reason="Synthetic current-run contamination.",
        metadata={"reason_code": "synthetic_contamination"},
    )
    recovery = evaluate_recovery_state(workspace=workspace, repo_workdir=ROOT)
    before = _control_bytes(workspace)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=workspace,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor complete",
        )

    assert recovery["status"] == "awaiting_recovery"
    assert excinfo.value.error_code == E_TRANSACTION_INTEGRITY
    assert excinfo.value.details["recovery_state"]["status"] == "awaiting_recovery"
    assert _control_bytes(workspace) == before
