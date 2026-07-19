from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests import test_core_run_v2 as core_fixture
from tests import test_core_run_v2_recovery as recovery_fixture

from multi_agent_brief.contracts.v2 import (
    AuditPromotionRequest,
    ClaimDraftsProposal,
    IntegrityCheckRequest,
    InvocationStartRequest,
    OwnedArtifactSubmitRequest,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2 import (
    ArtifactAcceptanceService,
    GateEvaluationService,
)
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier


def _verified(workspace, run_id):
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        return CoreRunDomainVerifier().verify(store, run_id)


def _accept_source_candidates(workspace, service):
    planner_action = classify_core_run_next_action(
        _verified(workspace, core_fixture.RUN_ID)
    )
    assert planner_action.action_kind == "delegate"
    assert planner_action.role_id == "source-planner"
    assert (
        planner_action.request_schema_id == "briefloop.owned_artifact_submit_request.v2"
    )
    planner = core_fixture._start_invocation(
        service,
        workspace,
        request_id="REQ-NEXT-SOURCE-PLANNER",
        stage_id="source-discovery",
        role_id="source-planner",
    )
    candidates = workspace / "scratch" / planner / "source_candidates.yaml"
    candidates.parent.mkdir(parents=True, exist_ok=True)
    candidates.write_text("sources:\n  - SRC-001\n", encoding="utf-8")
    accepted = ArtifactAcceptanceService(
        workspace,
        clock=core_fixture.CLOCK,
    ).submit_owned_artifact(
        core_fixture._record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-NEXT-SOURCE-CANDIDATES",
            run_id=core_fixture.RUN_ID,
            artifact_id="source_candidates",
            invocation_id=planner,
            producer_tool_id=None,
            input_path=candidates.relative_to(workspace).as_posix(),
            expected_store_revision=core_fixture._store_revision(workspace),
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )
    assert accepted.status == "committed", accepted.to_dict()


def _source_discovery_ready(workspace, *, role_ids=None):
    service = core_fixture._initialize(workspace, role_ids=role_ids)
    doctor = service.doctor_check(
        core_fixture._record(
            IntegrityCheckRequest,
            request_id="REQ-NEXT-SOURCE-DOCTOR",
            run_id=core_fixture.RUN_ID,
            expected_store_revision=core_fixture._store_revision(workspace),
        )
    )
    assert doctor.status == "committed", doctor.to_dict()
    return service


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


def test_next_action_stale_revision_cannot_reserve_later_action(tmp_path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    service = core_fixture._advance_to_scout_ready(workspace)
    action = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    invocation_id = core_fixture._start_invocation(
        service,
        workspace,
        request_id="REQ-NEXT-ACTION-STALE-FIRST",
        stage_id="scout",
        role_id="scout",
    )
    assert invocation_id
    committed_revision = core_fixture._store_revision(workspace)

    stale = service.start_invocation(
        core_fixture._record(
            InvocationStartRequest,
            request_id="REQ-NEXT-ACTION-STALE-SECOND",
            run_id=core_fixture.RUN_ID,
            stage_id="scout",
            role_id="scout",
            runtime="operator",
            expected_store_revision=action.store_revision,
        )
    )

    assert stale.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "store_revision_conflict",
    }
    assert core_fixture._store_revision(workspace) == committed_revision


def test_next_action_routes_planner_to_frozen_runtime_tool_provider(tmp_path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    core_fixture._configure_runtime_tool_source_route(workspace)
    service = _source_discovery_ready(workspace)
    _accept_source_candidates(workspace, service)

    action = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    assert action.action_kind == "delegate"
    assert action.effect_kind == "role_proposal"
    assert action.stage_id == "source-discovery"
    assert action.role_id == "source-provider"
    assert action.source_route_id == "web-search"
    assert action.source_provider_id == "runtime-tool"
    assert action.request_schema_id == "briefloop.source_commit_request.v2"

    invocation_id = core_fixture._start_invocation(
        service,
        workspace,
        request_id="REQ-NEXT-SOURCE-PROVIDER",
        stage_id="source-discovery",
        role_id="source-provider",
    )
    assert invocation_id


def test_next_action_external_api_is_deterministic_provider_reservation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = core_fixture._workspace(tmp_path)
    path = workspace / "sources.yaml"
    payload = core_fixture.yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["source_strategy"]["enabled_providers"] = ["manual", "web_search"]
    web_search = payload.setdefault("web_search", {})
    web_search["enabled"] = True
    web_search["mode"] = "external_api"
    web_search["backend"] = "tavily"
    path.write_text(
        core_fixture.yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.service.run_doctor",
        lambda **_kwargs: [SimpleNamespace(status="OK")],
    )
    service = _source_discovery_ready(workspace)
    _accept_source_candidates(workspace, service)

    action = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    assert action.action_kind == "deterministic"
    assert action.effect_kind == "source_acquire"
    assert action.source_route_id == "web-search"
    assert action.source_provider_id == "tavily"
    assert action.request_schema_id == "briefloop.source_commit_request.v2"

    before = core_fixture._store_revision(workspace)
    reserved = service.start_invocation(
        core_fixture._record(
            InvocationStartRequest,
            request_id="REQ-NEXT-DETERMINISTIC-SOURCE",
            run_id=core_fixture.RUN_ID,
            stage_id="source-discovery",
            role_id="source-provider",
            runtime="operator",
            expected_store_revision=before,
        )
    )
    assert reserved.status == "committed", reserved.to_dict()
    assert core_fixture._store_revision(workspace) == before + 1
    reserved_action = classify_core_run_next_action(
        _verified(workspace, core_fixture.RUN_ID)
    )
    assert reserved_action.effect_kind == "invocation_accept_or_fail"
    assert reserved_action.request_schema_id == "briefloop.source_commit_request.v2"


def test_next_action_manual_route_requires_human_source_input(tmp_path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    role_ids = list(
        core_fixture.CoreRunInitializeRequest.minimal_example[
            "runtime_adapter_binding"
        ]["role_ids"]
    )
    role_ids.remove("source-provider")
    service = _source_discovery_ready(workspace, role_ids=role_ids)
    _accept_source_candidates(workspace, service)

    action = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    assert action.action_kind == "human_decision"
    assert action.effect_kind == "source_input_required"
    assert action.source_route_id == "manual"
    assert action.source_provider_id is None


def test_next_action_missing_source_provider_role_is_zero_write_block(
    tmp_path,
) -> None:
    workspace = core_fixture._workspace(tmp_path)
    core_fixture._configure_runtime_tool_source_route(workspace)
    roles = [
        role
        for role in deepcopy(
            core_fixture.CoreRunInitializeRequest.minimal_example[
                "runtime_adapter_binding"
            ]["role_ids"]
        )
        if role != "source-provider"
    ]
    service = _source_discovery_ready(workspace, role_ids=roles)
    _accept_source_candidates(workspace, service)
    before = core_fixture._store_revision(workspace)

    action = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    assert action.action_kind == "blocked"
    assert action.reason_code == "runtime_role_unavailable"
    rejected = service.start_invocation(
        core_fixture._record(
            InvocationStartRequest,
            request_id="REQ-NEXT-MISSING-SOURCE-PROVIDER",
            run_id=core_fixture.RUN_ID,
            stage_id="source-discovery",
            role_id="source-provider",
            runtime="operator",
            expected_store_revision=before,
        )
    )
    assert rejected.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "runtime_role_unavailable",
    }
    assert core_fixture._store_revision(workspace) == before


def test_next_action_missing_source_planner_role_is_zero_write_block(
    tmp_path,
) -> None:
    workspace = core_fixture._workspace(tmp_path)
    roles = [
        role
        for role in deepcopy(
            core_fixture.CoreRunInitializeRequest.minimal_example[
                "runtime_adapter_binding"
            ]["role_ids"]
        )
        if role != "source-planner"
    ]
    _source_discovery_ready(workspace, role_ids=roles)
    before = core_fixture._store_revision(workspace)

    action = classify_core_run_next_action(
        _verified(workspace, core_fixture.RUN_ID)
    )

    assert action.action_kind == "blocked"
    assert action.effect_kind == "role_unavailable"
    assert action.reason_code == "runtime_role_unavailable"
    assert action.source_route_id is None
    assert core_fixture._store_revision(workspace) == before


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


def test_next_action_claim_drafts_switches_to_deterministic_freeze(tmp_path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    service = core_fixture._advance_to_claim_ledger_ready(workspace)
    invocation_id = core_fixture._start_invocation(
        service,
        workspace,
        request_id="REQ-NEXT-CLAIM-DRAFTS",
        stage_id="claim-ledger",
        role_id="claim-ledger",
    )
    payload = deepcopy(ClaimDraftsProposal.minimal_example)
    payload.update(
        proposal_id="PROP-NEXT-CLAIM-DRAFTS",
        run_id=core_fixture.RUN_ID,
        screened_candidates_proposal_id="PROP-SCREENED-001",
    )
    payload["drafts"][0]["source_ids"] = ["SRC-001"]
    core_fixture._submit_proposal(
        workspace,
        lane="claim-drafts",
        invocation_id=invocation_id,
        request_id="REQ-NEXT-CLAIM-DRAFTS-ACCEPT",
        artifact_id="claim_drafts",
        payload=payload,
    )

    action = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    assert action.action_kind == "deterministic"
    assert action.effect_kind == "claim_freeze"
    assert action.request_schema_id == "briefloop.claim_freeze_request.v2"


def test_next_action_audit_promotion_gate_then_stage_complete(tmp_path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    core_fixture._advance_to_auditor_ready(workspace, promote_audit=False)

    promotion_action = classify_core_run_next_action(
        _verified(workspace, core_fixture.RUN_ID)
    )
    assert promotion_action.action_kind == "deterministic"
    assert promotion_action.effect_kind == "audit_promotion"
    assert promotion_action.request_schema_id == "briefloop.audit_promotion_request.v2"

    promoted = ArtifactAcceptanceService(
        workspace,
        clock=core_fixture.CLOCK,
    ).promote_audit_proposal(
        core_fixture._record(
            AuditPromotionRequest,
            request_id="REQ-NEXT-AUDIT-PROMOTION",
            run_id=core_fixture.RUN_ID,
            audit_proposal_id="PROP-AUDIT-001",
            expected_target_artifact={
                "artifact_id": "audited_brief",
                "revision": 1,
            },
            expected_audit_report_revision=0,
            expected_store_revision=core_fixture._store_revision(workspace),
        )
    )
    assert promoted.status == "committed", promoted.to_dict()
    gate_action = classify_core_run_next_action(
        _verified(workspace, core_fixture.RUN_ID)
    )
    assert gate_action.action_kind == "deterministic"
    assert gate_action.effect_kind == "gate_evaluation"
    assert gate_action.request_schema_id == "briefloop.gate_check_request.v2"

    gated = GateEvaluationService(
        workspace,
        clock=core_fixture.CLOCK,
    ).evaluate(core_fixture._gate_request(workspace, request_id="REQ-NEXT-GATE"))
    assert gated.status == "committed", gated.to_dict()
    complete = classify_core_run_next_action(_verified(workspace, core_fixture.RUN_ID))
    assert complete.action_kind == "deterministic"
    assert complete.effect_kind == "stage_complete"
    assert complete.stage_id == "auditor"


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
