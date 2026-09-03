"""Stage seeding, extracted from the core-run test module.

The landmark assertions below encode the durable end-state each
``_advance_*`` helper produced when it still lived in
``tests/test_core_run_v2.py``: stage statuses, artifact revisions, frozen
claims, and required auditor gate evaluations.  Timestamps, request ids,
transaction ids, and content-hash-derived artifact ids are deliberately
not asserted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2 import CoreRunService
from multi_agent_brief.core_run_v2.policy import REQUIRED_AUDITOR_GATES
from multi_agent_brief.evaluation_v2.staging import (
    RUN_ID,
    SEEDABLE_STAGES,
    StagingError,
    seed_workspace_to_stage,
)


def _seeded_workspace(parent: Path, name: str) -> Path:
    workspace = parent / name
    create_demo_workspace(workspace)
    return workspace


def _snapshot(workspace: Path):
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        return store.load_snapshot(RUN_ID)


def _stage_statuses(workspace: Path) -> dict[str, str]:
    return {item.stage_id: item.status for item in _snapshot(workspace).stage_states}


def _artifact_revisions(workspace: Path) -> dict[str, int]:
    return {
        item.artifact_id: item.current_revision
        for item in _snapshot(workspace).artifacts
    }


def test_seedable_stages_is_the_expected_tuple() -> None:
    assert SEEDABLE_STAGES == (
        "scout",
        "screener",
        "claim-ledger",
        "analyst",
        "auditor",
        "finalize",
    )


def test_seed_rejects_unknown_stage(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path, "ws")
    with pytest.raises(StagingError, match="not seedable"):
        seed_workspace_to_stage(workspace, "nonexistent")


def test_seed_returns_the_run_service(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path, "ws")
    service = seed_workspace_to_stage(workspace, "scout")
    assert isinstance(service, CoreRunService)


def test_seed_scout_matches_advance_to_scout_ready(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path, "ws")
    seed_workspace_to_stage(workspace, "scout")
    assert _stage_statuses(workspace) == {
        "doctor": "complete",
        "source-discovery": "complete",
        "input-governance": "complete",
        "scout": "ready",
        "screener": "pending",
        "claim-ledger": "pending",
        "analyst": "pending",
        "editor": "pending",
        "auditor": "pending",
        "finalize": "pending",
    }
    revisions = _artifact_revisions(workspace)
    assert revisions["source_candidates"] == 1
    assert (
        sum(
            1
            for artifact_id in revisions
            if artifact_id.startswith("SRC-CONTENT-")
        )
        == 1
    )


def test_seed_screener_matches_advance_to_input_governance_ready(
    tmp_path: Path,
) -> None:
    workspace = _seeded_workspace(tmp_path, "ws")
    seed_workspace_to_stage(workspace, "screener")
    assert _stage_statuses(workspace) == {
        "doctor": "complete",
        "source-discovery": "complete",
        "input-governance": "ready",
        "scout": "pending",
        "screener": "pending",
        "claim-ledger": "pending",
        "analyst": "pending",
        "editor": "pending",
        "auditor": "pending",
        "finalize": "pending",
    }
    assert _artifact_revisions(workspace)["source_candidates"] == 1


def test_seed_claim_ledger_matches_advance_to_claim_ledger_ready(
    tmp_path: Path,
) -> None:
    workspace = _seeded_workspace(tmp_path, "ws")
    seed_workspace_to_stage(workspace, "claim-ledger")
    assert _stage_statuses(workspace) == {
        "doctor": "complete",
        "source-discovery": "complete",
        "input-governance": "complete",
        "scout": "complete",
        "screener": "complete",
        "claim-ledger": "ready",
        "analyst": "pending",
        "editor": "pending",
        "auditor": "pending",
        "finalize": "pending",
    }
    revisions = _artifact_revisions(workspace)
    assert revisions["candidate_claims"] == 1
    assert revisions["screened_candidates"] == 1


def test_seed_analyst_matches_advance_to_analyst_ready(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path, "ws")
    seed_workspace_to_stage(workspace, "analyst")
    assert _stage_statuses(workspace) == {
        "doctor": "complete",
        "source-discovery": "complete",
        "input-governance": "complete",
        "scout": "complete",
        "screener": "complete",
        "claim-ledger": "complete",
        "analyst": "ready",
        "editor": "pending",
        "auditor": "pending",
        "finalize": "pending",
    }
    revisions = _artifact_revisions(workspace)
    assert revisions["claim_drafts"] == 1
    assert revisions["claim_ledger"] == 1
    assert len(_snapshot(workspace).claims) == 1


def test_seed_auditor_matches_advance_to_auditor_ready(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path, "ws")
    seed_workspace_to_stage(workspace, "auditor")
    assert _stage_statuses(workspace) == {
        "doctor": "complete",
        "source-discovery": "complete",
        "input-governance": "complete",
        "scout": "complete",
        "screener": "complete",
        "claim-ledger": "complete",
        "analyst": "complete",
        "editor": "complete",
        "auditor": "ready",
        "finalize": "pending",
    }
    revisions = _artifact_revisions(workspace)
    assert revisions["analyst_draft_snapshot"] == 1
    assert revisions["audited_brief"] == 1
    assert revisions["audit_proposal"] == 1
    assert revisions["audit_report"] == 1


def test_seed_finalize_matches_advance_to_finalize_ready(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path, "ws")
    seed_workspace_to_stage(workspace, "finalize")
    assert _stage_statuses(workspace) == {
        "doctor": "complete",
        "source-discovery": "complete",
        "input-governance": "complete",
        "scout": "complete",
        "screener": "complete",
        "claim-ledger": "complete",
        "analyst": "complete",
        "editor": "complete",
        "auditor": "complete",
        "finalize": "ready",
    }
    revisions = _artifact_revisions(workspace)
    assert revisions["audit_report"] == 1
    assert revisions["auditor_quality_gate_report"] == 1
    evaluated_gate_ids = {
        item.gate_id for item in _snapshot(workspace).gate_evaluations
    }
    assert set(REQUIRED_AUDITOR_GATES) <= evaluated_gate_ids


def test_seed_forwards_kwargs_to_the_stage_helper(tmp_path: Path) -> None:
    workspace = _seeded_workspace(tmp_path, "ws")
    seed_workspace_to_stage(workspace, "scout", topology="default")
    assert _stage_statuses(workspace)["scout"] == "ready"


def test_seed_is_deterministic(tmp_path: Path) -> None:
    first = _seeded_workspace(tmp_path, "a")
    second = _seeded_workspace(tmp_path, "b")
    seed_workspace_to_stage(first, "finalize")
    seed_workspace_to_stage(second, "finalize")
    assert _stage_statuses(first) == _stage_statuses(second)
    stable_ids = (
        "audited_brief",
        "audit_report",
        "auditor_quality_gate_report",
        "claim_ledger",
        "claim_drafts",
        "screened_candidates",
        "candidate_claims",
        "source_candidates",
    )
    first_revisions = _artifact_revisions(first)
    second_revisions = _artifact_revisions(second)
    for artifact_id in stable_ids:
        assert first_revisions[artifact_id] == second_revisions[artifact_id] == 1
