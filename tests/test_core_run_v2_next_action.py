from __future__ import annotations

from tests import test_core_run_v2 as core_fixture
from tests import test_core_run_v2_recovery as recovery_fixture

from multi_agent_brief.control_store import SQLiteControlStore
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
