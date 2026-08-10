from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests import test_core_run_v2 as core_fixture
from tests import test_core_run_v2_gate_repair as gate_repair_fixture
from tests import test_core_run_v2_recovery as recovery_fixture
from tests.test_runtime_host_continue_v2 import (
    _advance_discovery_to_source_action,
    _discovery_workspace,
    _service,
    _tavily_collection,
    _tavily_item,
)

from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.product.projection_platform import (
    supports_retained_directory_publication,
)
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.initialization import (
    initialize_or_open_runtime,
)
from multi_agent_brief.sources.web_search import WebSearchProvider


_REQUIRES_RETAINED_PUBLICATION = pytest.mark.skipif(
    not supports_retained_directory_publication(),
    reason="discovery reservation setup requires retained-directory publication",
)


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
    reserved = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    assert invocation_id
    assert reserved.action_kind == "deterministic"
    assert reserved.effect_kind == "invocation_accept_or_fail"
    assert reserved.stage_id == "scout"


@_REQUIRES_RETAINED_PUBLICATION
def test_next_action_recovers_only_exact_discovery_reservation(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    action = _advance_discovery_to_source_action(workspace)
    monkeypatch.setattr(
        WebSearchProvider,
        "collect_with_response",
        lambda _provider, _query, _config: _tavily_collection(
            [_tavily_item(durable=True)], tasks=_config["search_tasks"]
        ),
    )

    def crash_before_promotion(*_args, **_kwargs):
        raise RuntimeHostError("simulated_post_invocation_crash")

    monkeypatch.setattr(
        IntakeService,
        "_commit_discovery_source_pack_from_core",
        crash_before_promotion,
    )

    with pytest.raises(RuntimeHostError, match="simulated_post_invocation_crash"):
        _service(workspace).apply_current(action)

    recovered = classify_core_run_next_action(_verified(workspace, action.run_id))
    assert recovered.action_kind == "deterministic"
    assert recovered.effect_kind == "source_acquire"
    assert recovered.reason_code == "active_discovery_source_acquire_requires_resume"


@_REQUIRES_RETAINED_PUBLICATION
def test_next_action_keeps_arbitrary_discovery_invocation_reserved(tmp_path) -> None:
    workspace = _discovery_workspace(tmp_path)
    action = _advance_discovery_to_source_action(workspace)
    service = _service(workspace)
    current = initialize_or_open_runtime(
        workspace,
        adapter_loader=service._adapter_loader,
    )
    service._start_invocation_for_action(
        current,
        action,
        role_id="source-provider",
        request_id="REQ-ARBITRARY-DISCOVERY-INVOCATION",
    )

    reserved = classify_core_run_next_action(_verified(workspace, action.run_id))
    assert reserved.action_kind == "deterministic"
    assert reserved.effect_kind == "invocation_accept_or_fail"
    assert reserved.reason_code == "active_invocation_reserved"


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
    action = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    assert (action.action_kind, action.effect_kind, action.stage_id) == (
        "deterministic",
        "stage_complete",
        "scout",
    )


def test_next_action_routes_negative_audit_truth_to_human_review(tmp_path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    core_fixture._advance_to_auditor_ready(workspace, audit_decision="fail")
    gate = core_fixture.GateEvaluationService(
        workspace,
        clock=core_fixture.CLOCK,
    ).evaluate(core_fixture._gate_request(workspace))
    assert gate.status == "committed", gate.to_dict()

    verified = _verified(workspace, core_fixture.RUN_ID)
    before_revision = verified.snapshot.store_revision
    first = classify_core_run_next_action(verified)
    second = classify_core_run_next_action(verified)

    assert first == second
    assert (
        first.action_kind,
        first.effect_kind,
        first.reason_code,
        first.stage_id,
    ) == (
        "human_decision",
        "audit_human_review",
        "negative_audit_truth_requires_human_review",
        "auditor",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_revision


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
    rerun = classify_core_run_next_action(_verified(workspace, recovery_fixture.RUN_ID))
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
