from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests import test_core_run_v2 as core_fixture
from tests import test_core_run_v2_gate_repair as gate_repair_fixture
from tests import test_core_run_v2_recovery as recovery_fixture

from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier


def _verified(workspace, run_id):
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        return CoreRunDomainVerifier().verify(store, run_id)


def test_next_action_delegation_and_active_invocation_precedence(tmp_path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    service = core_fixture._advance_to_scout_ready(workspace)
    ready = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    assert ready.action_kind == "delegate"
    assert ready.effect_kind == "role_proposal"
    assert ready.stage_id == "scout"
    assert ready.role_id == "scout"
    invocation_id = core_fixture._start_invocation(
        service,
        workspace,
        request_id="REQ-NEXT-ACTION-SCOUT-001",
        stage_id="scout",
        role_id="scout",
    )
    reserved = classify_core_run_next_action(
        _verified(workspace, core_fixture.RUN_ID)
    )
    assert invocation_id
    assert reserved.action_kind == "deterministic"
    assert reserved.effect_kind == "invocation_accept_or_fail"
    assert reserved.stage_id == "scout"


def test_next_action_selects_stage_complete_after_current_proposals(tmp_path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    service = core_fixture._advance_to_scout_ready(workspace)
    scout = core_fixture._start_invocation(
        service,
        workspace,
        request_id="REQ-NEXT-ACTION-SCOUT-CANDIDATE",
        stage_id="scout",
        role_id="scout",
    )
    core_fixture._submit_proposal(
        workspace,
        lane="candidate",
        invocation_id=scout,
        request_id="REQ-NEXT-ACTION-CANDIDATE",
        artifact_id="candidate_claims",
        payload=core_fixture._candidate_payload(),
    )
    screening = core_fixture._start_invocation(
        service,
        workspace,
        request_id="REQ-NEXT-ACTION-SCOUT-SCREENED",
        stage_id="scout",
        role_id="scout",
    )
    core_fixture._submit_proposal(
        workspace,
        lane="screened",
        invocation_id=screening,
        request_id="REQ-NEXT-ACTION-SCREENED",
        artifact_id="screened_candidates",
        payload=core_fixture._screened_payload(),
    )
    action = classify_core_run_next_action(
        _verified(workspace, core_fixture.RUN_ID)
    )
    assert (action.action_kind, action.effect_kind, action.stage_id) == (
        "deterministic",
        "stage_complete",
        "scout",
    )


def test_next_action_recovery_precedes_normal_workflow(tmp_path) -> None:
    workspace = recovery_fixture._initialized_workspace(tmp_path)
    with SQLiteControlStore.open(
        workspace / "briefloop.db", clock=recovery_fixture.CLOCK
    ) as store:
        recovery_fixture._accept_input_classification(store)
        recovery_fixture._record_contamination(store)
    action = classify_core_run_next_action(
        _verified(workspace, recovery_fixture.RUN_ID)
    )
    assert action.action_kind == "deterministic"
    assert action.effect_kind == "repair_start"


def test_active_gate_repair_contamination_precedes_legacy_recovery() -> None:
    cycle = SimpleNamespace(
        gate_repair_id="GATE-REPAIR-1",
        run_id="RUN-1",
        source_gate_batch_id="GATE-BATCH-1",
    )
    snapshot = gate_repair_fixture._snapshot(
        (gate_repair_fixture._finding("FINDING-EDITOR"),),
        cycle=cycle,
        contaminated=True,
    )
    snapshot.store_revision = 12
    verified = SimpleNamespace(
        snapshot=snapshot,
        runtime_adapter=SimpleNamespace(binding_fingerprint="a" * 64),
        source_plan=SimpleNamespace(source_plan_fingerprint="b" * 64),
    )

    first = classify_core_run_next_action(verified)
    second = classify_core_run_next_action(verified)

    assert first == second
    assert (
        first.action_kind,
        first.effect_kind,
        first.reason_code,
    ) == (
        "human_decision",
        "gate_repair_human_review",
        "gate_repair_failed_after_attempt",
    )


def test_mixed_gate_and_legacy_repair_authority_is_invalid() -> None:
    cycle = SimpleNamespace(
        gate_repair_id="GATE-REPAIR-1",
        run_id="RUN-1",
        source_gate_batch_id="GATE-BATCH-1",
    )
    snapshot = gate_repair_fixture._snapshot(
        (gate_repair_fixture._finding("FINDING-EDITOR"),),
        cycle=cycle,
        legacy_repair=True,
    )
    snapshot.store_revision = 12
    verified = SimpleNamespace(
        snapshot=snapshot,
        runtime_adapter=SimpleNamespace(binding_fingerprint="a" * 64),
        source_plan=SimpleNamespace(source_plan_fingerprint="b" * 64),
    )

    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        classify_core_run_next_action(verified)


def test_next_action_routes_repair_rerun_before_recovery_complete(tmp_path) -> None:
    workspace = recovery_fixture._initialized_workspace(tmp_path)
    with SQLiteControlStore.open(
        workspace / "briefloop.db", clock=recovery_fixture.CLOCK
    ) as store:
        recovery_fixture._accept_input_classification(store)
        recovery_fixture._record_contamination(store)
        recovery_fixture._start_repair(store)
        recovery_fixture._supersede_input_classification(store)
        recovery_fixture._complete_repair(store)
    rerun = classify_core_run_next_action(
        _verified(workspace, recovery_fixture.RUN_ID)
    )
    assert (rerun.effect_kind, rerun.stage_id) == (
        "stage_complete",
        "input-governance",
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db", clock=recovery_fixture.CLOCK
    ) as store:
        recovery_fixture._complete_reopened_stage(store)
    complete = classify_core_run_next_action(
        _verified(workspace, recovery_fixture.RUN_ID)
    )
    assert complete.effect_kind == "recovery_complete"


def test_next_action_finalize_is_pure_and_fingerprint_stable(tmp_path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    core_fixture._advance_to_finalize_ready(workspace)
    verified = _verified(workspace, core_fixture.RUN_ID)
    first = classify_core_run_next_action(verified)
    second = classify_core_run_next_action(verified)
    assert first == second
    assert first.action_kind == "deterministic"
    assert first.effect_kind == "finalize_render"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == verified.snapshot.store_revision
