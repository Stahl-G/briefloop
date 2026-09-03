from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import MappingProxyType

import pytest

from tests import test_core_run_v2 as core_fixture

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.contracts.v2 import (
    Approval,
    ApprovalPackageBinding,
    ArtifactRecord,
    ArtifactRevision,
    CoreRunInitializeRequest,
    CoreRunEventBinding,
    DeliveryAttemptRecord,
    DeliveryAuthorizationRecord,
    DeliveryResultRecord,
    EventEnvelope,
    ExecutionSourceManifest,
    FinalizeCompleteRequest,
    FinalizeRenderRequest,
    InternalApprovalRequest,
    IntegrityCheckRequest,
    FinalizationRecord,
    FinalizeRenderRecord,
    GateCheckRequest,
    GateEvaluationRecord,
    PackageArtifactBinding,
    PackageReadyRecord,
    PublicationIdentityV1,
    RunArchiveArtifactBinding,
    RunArchiveRecord,
    StageArtifactBinding,
    StageGateBinding,
    StageState,
    StageTransitionRecord,
    TransactionReceipt,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.sqlite_store import ControlStoreHistory
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.core_run_v2 import CoreRunService, CoreRunTerminalService
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.evaluation_v2 import staging as staging_fixture
from multi_agent_brief.core_run_v2.lineage import classify_current_audit_promotion
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.gates import GateEvaluationService
from multi_agent_brief.core_run_v2.policy import (
    archive_artifact_usage,
    transaction_type_for,
)
from multi_agent_brief.core_run_v2.recovery import CoreEffect
from multi_agent_brief.core_run_v2.terminal import (
    TerminalEffectSubject,
    classify_terminal_effect_authorization,
    classify_terminal_legality,
    classify_terminal_state,
)
from multi_agent_brief.core_run_v2.verifier import (
    CoreRunDomainVerifier,
    _verified_core_receipt_binding,
    resolve_core_replay,
)
from multi_agent_brief.quality_gates.contract import GATE_IDS

RUN_ID = "RUN-TERMINAL-PREFIX-001"


def _commit_core_fixture(store: SQLiteControlStore, unit, *, observer=None):
    return core_fixture._commit_core_fixture(
        store,
        unit,
        observer=observer,
    )


def _finalize_ready_workspace(tmp_path: Path) -> tuple[Path, str, object]:
    workspace = core_fixture._workspace(tmp_path)
    core_fixture._advance_to_finalize_ready(workspace)
    return workspace, core_fixture.RUN_ID, core_fixture.CLOCK


def _authorized_finalize_ready_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, object]:
    """Reuse the normal spine after replacing only its source-intake prefix."""

    workspace = core_fixture._workspace(tmp_path)
    authorization = core_fixture._execution_authorization(workspace)
    manifest_payload = deepcopy(authorization["source_manifest"])
    manifest_payload["members"][0]["source_id"] = "SRC-001"
    manifest = ExecutionSourceManifest.model_validate(manifest_payload, strict=True)
    manifest_bytes = canonical_json_bytes(
        manifest.model_dump(mode="json", exclude_unset=False)
    )
    authorization.update(
        source_manifest=manifest.model_dump(mode="json", exclude_unset=False),
        source_manifest_sha256=sha256_hex(manifest_bytes),
    )

    def advance_authorized_source_prefix(
        target: Path,
        *,
        topology: str = "default",
        role_ids: list[str] | None = None,
        output_contract: dict[str, object] | None = None,
    ) -> CoreRunService:
        service = core_fixture._initialize(
            target,
            topology=topology,
            role_ids=role_ids,
            output_contract=output_contract,
            input_governance_required=True,
            execution_authorization=authorization,
        )
        doctor = service.doctor_check(
            core_fixture._record(
                IntegrityCheckRequest,
                request_id="REQ-TERMINAL-AUTHORIZED-DOCTOR-001",
                run_id=core_fixture.RUN_ID,
                expected_store_revision=core_fixture._store_revision(target),
            )
        )
        assert doctor.status == "committed", doctor.to_dict()
        pack = service.apply_authorized_source_pack()
        assert pack.status == "committed", pack.to_dict()
        with SQLiteControlStore.open(
            target / "briefloop.db", clock=core_fixture.CLOCK
        ) as store:
            snapshot = CoreRunDomainVerifier().verify(
                store, core_fixture.RUN_ID
            ).snapshot
            source = snapshot.sources[0]
            frozen = snapshot.run_execution_authorizations[0]
        core_fixture._complete_stage(
            service,
            target,
            stage_id="source-discovery",
            artifacts=[
                (
                    frozen.source_manifest_artifact.artifact_id,
                    frozen.source_manifest_artifact.revision,
                ),
                (source.content_artifact_id, source.content_artifact_revision),
            ],
        )
        core_fixture._complete_stage(
            service,
            target,
            stage_id="input-governance",
            artifacts=[("input_classification", 1)],
        )
        return service

    monkeypatch.setattr(
        core_fixture,
        "_advance_to_scout_ready",
        advance_authorized_source_prefix,
    )
    # The walk chain (_advance_to_claim_ledger_ready -> _advance_to_scout_ready,
    # ... -> _advance_to_finalize_ready) was extracted into
    # evaluation_v2.staging and resolves its internal callees from that
    # module's globals, so the interception must land there as well;
    # core_fixture stays patched for any direct caller.
    monkeypatch.setattr(
        staging_fixture,
        "_advance_to_scout_ready",
        advance_authorized_source_prefix,
    )
    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.gates.evaluate_quality_gate_findings_preloaded",
        lambda **_kwargs: {gate_id: [] for gate_id in GATE_IDS},
    )
    core_fixture._advance_to_finalize_ready(workspace)
    return workspace, core_fixture.RUN_ID, core_fixture.CLOCK


def _commit_finalize_render(
    workspace: Path,
    run_id: str,
    clock: object,
) -> tuple[TransactionReceipt, str, FinalizeRenderRecord]:
    transaction_id = "REQ-TERMINAL-RENDER-001"
    reader_bytes = (
        b"# ExampleCo reader brief\n\n## Executive Summary\n\n"
        b"ExampleCo opened a public pilot facility on 2026-07-14.\n"
    )
    scratch = workspace / "scratch" / "terminal-render-helper"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "brief.md").write_bytes(reader_bytes)
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        promotion = classify_current_audit_promotion(
            verified.snapshot,
            store.read_artifact_revision_bytes,
        )
        assert promotion is not None
        assert promotion.is_current_lineage
        expected_store_revision = verified.snapshot.store_revision
    request = FinalizeRenderRequest.model_validate(
        {
            "schema_version": FinalizeRenderRequest.schema_id,
            "request_id": transaction_id,
            "run_id": run_id,
            "audit_proposal_id": promotion.proposal_record.proposal_id,
            "expected_audited_brief": {
                "artifact_id": promotion.brief_revision.artifact_id,
                "revision": promotion.brief_revision.revision,
            },
            "expected_audit_report": {
                "artifact_id": promotion.report_revision.artifact_id,
                "revision": promotion.report_revision.revision,
            },
            "reader_scratch_inputs": {
                "reader_brief": "scratch/terminal-render-helper/brief.md"
            },
            "expected_reader_sha256": {"reader_brief": sha256_hex(reader_bytes)},
            "expected_reader_revisions": {"reader_brief": 0},
            "expected_store_revision": expected_store_revision,
        },
        strict=True,
    )
    request_fingerprint = canonical_fingerprint(
        request.model_dump(mode="json", exclude_unset=False)
    )
    result = CoreRunTerminalService(workspace, clock=clock).accept_finalize_render(
        request
    )
    assert (result.status, result.error_code) == ("committed", None)
    assert result.receipt is not None
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
        render = next(
            item
            for item in snapshot.finalize_renders
            if item.render_id == result.primary_record_id
        )
    return result.receipt, request_fingerprint, render


def test_finalize_render_persists_replays_conflicts_and_survives_restart(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    receipt, fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )

    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        assert verified.snapshot.finalize_renders == (render,)
        revision = store.current_revision
        replay = resolve_core_replay(
            store,
            run_id=run_id,
            request_id=receipt.transaction_id,
            request_fingerprint=fingerprint,
        )
        assert replay is not None
        assert replay.status == "replayed"
        assert replay.receipt == receipt
        assert replay.primary_record_id == render.render_id
        with pytest.raises(CoreRunError) as error:
            resolve_core_replay(
                store,
                run_id=run_id,
                request_id=receipt.transaction_id,
                request_fingerprint="0" * 64,
            )
        assert error.value.code == "submission_replay_conflict"
        assert store.current_revision == revision


@pytest.mark.parametrize("case", ("existing_artifact_id", "missing_scratch"))
def test_finalize_render_rejects_non_reader_or_unreadable_scratch_without_writes(
    tmp_path: Path,
    case: str,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    scratch = workspace / "scratch" / "terminal-render-invalid"
    scratch.mkdir(parents=True)
    reader_bytes = b"# Reader-safe synthetic brief\n"
    (scratch / "brief.md").write_bytes(reader_bytes)
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        promotion = classify_current_audit_promotion(
            verified.snapshot,
            store.read_artifact_revision_bytes,
        )
        assert promotion is not None
        before_revision = verified.snapshot.store_revision
        claim_ledger = next(
            item
            for item in verified.snapshot.artifact_revisions
            if item.artifact_id == "claim_ledger"
            and item.revision
            == next(
                artifact.current_revision
                for artifact in verified.snapshot.artifacts
                if artifact.artifact_id == "claim_ledger"
            )
        )
        claim_ledger_bytes = store.read_artifact_revision_bytes(
            run_id,
            claim_ledger.artifact_id,
            claim_ledger.revision,
        )

    artifact_id = "claim_ledger" if case == "existing_artifact_id" else "reader_brief"
    input_path = (
        "scratch/terminal-render-invalid/brief.md"
        if case == "existing_artifact_id"
        else "scratch/terminal-render-invalid/missing.md"
    )
    request = FinalizeRenderRequest.model_validate(
        {
            "schema_version": FinalizeRenderRequest.schema_id,
            "request_id": f"REQ-TERMINAL-RENDER-INVALID-{case.upper()}",
            "run_id": run_id,
            "audit_proposal_id": promotion.proposal_record.proposal_id,
            "expected_audited_brief": {
                "artifact_id": promotion.brief_revision.artifact_id,
                "revision": promotion.brief_revision.revision,
            },
            "expected_audit_report": {
                "artifact_id": promotion.report_revision.artifact_id,
                "revision": promotion.report_revision.revision,
            },
            "reader_scratch_inputs": {artifact_id: input_path},
            "expected_reader_sha256": {artifact_id: sha256_hex(reader_bytes)},
            "expected_reader_revisions": {artifact_id: 0},
            "expected_store_revision": before_revision,
        },
        strict=True,
    )
    result = CoreRunTerminalService(workspace, clock=clock).accept_finalize_render(
        request
    )
    assert (result.status, result.error_code) == (
        "failed_uncommitted",
        "finalize_input_invalid",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
        assert snapshot.store_revision == before_revision
        assert not snapshot.finalize_renders
        assert (
            store.read_artifact_revision_bytes(
                run_id,
                claim_ledger.artifact_id,
                claim_ledger.revision,
            )
            == claim_ledger_bytes
        )


def test_authorized_finalize_local_commits_nonpublishing_checkout_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_agent_brief.core_run_v2.checkout import (
        build_checkout_revision,
        build_publication_intent,
        prepare_checkout_effect,
    )

    workspace, run_id, clock = _authorized_finalize_ready_workspace(
        tmp_path, monkeypatch
    )
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace, run_id, clock
    )
    _gate_receipt, _gate_fingerprint, evaluations = _commit_finalize_gate(
        workspace, run_id, clock, render
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        before = verified.snapshot
        finalize_stage = next(
            item for item in before.stage_states if item.stage_id == "finalize"
        )
        committed_revisions = {
            item.transaction_id: item.committed_revision
            for item in before.transactions
        }
        prior_binding = max(
            before.receipt_checkout_bindings,
            key=lambda item: committed_revisions[item.transaction_id],
        )
        prior_members = tuple(
            item
            for item in before.checkout_revision_members
            if item.checkout_revision_id
            == prior_binding.post_checkout_revision_id
        )
        observed_projection = {
            item.canonical_path: (workspace / item.canonical_path).read_bytes()
            for item in prior_members
        }

        # A real projection delta remains precommit-unsupported on Windows.
        changed_bytes = b"new projected bytes\n"
        changed = core_fixture._record(
            ArtifactRevision,
            run_id=run_id,
            artifact_id="windows_projection_probe",
            revision=1,
            path="output/intermediate/windows-projection-probe.txt",
            sha256=sha256_hex(changed_bytes),
            size_bytes=len(changed_bytes),
            frozen=True,
            producer_kind="control_tool",
            producer_id="core-v2-test-probe",
            created_at=core_fixture.NOW,
        )
        monkeypatch.setattr(sys, "platform", "win32")
        with pytest.raises(CoreRunError, match="checkout_publication_unsupported"):
            prepare_checkout_effect(
                workspace=workspace,
                snapshot=before,
                transaction_id="REQ-WINDOWS-PROJECTION-PROBE-001",
                created_at=clock(),
                additional_revisions=(changed,),
            )

    request = FinalizeCompleteRequest.model_validate(
        {
            "schema_version": FinalizeCompleteRequest.schema_id,
            "request_id": "REQ-TERMINAL-AUTHORIZED-LOCAL-COMPLETE-001",
            "run_id": run_id,
            "render_id": render.render_id,
            "expected_finalize_stage_revision": finalize_stage.revision,
            "gate_evaluation_ids": sorted(
                item.evaluation_id for item in evaluations
            ),
            "recovery_id": None,
            "expected_store_revision": before.store_revision,
        },
        strict=True,
    )
    service = CoreRunTerminalService(workspace, clock=clock)
    result = service.complete_finalize(request)
    assert (result.status, result.error_code) == ("committed", None)
    assert result.receipt is not None
    assert len(result.receipt.stage_transitions) == 1
    assert result.receipt.stage_artifact_bindings
    assert len(result.receipt.stage_gate_bindings) == len(evaluations)
    assert len(result.receipt.finalizations) == 1
    assert len(result.receipt.checkout_revisions) == 1
    assert len(result.receipt.receipt_checkout_bindings) == 1
    assert result.receipt.checkout_publication_intents == []
    assert result.receipt.artifact_revisions == []
    assert result.receipt.run_archives == []
    assert result.receipt.package_ready_records == []

    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        after_verified = CoreRunDomainVerifier().verify(store, run_id)
        after = after_verified.snapshot
        revision_after_commit = after.store_revision
        finalization = next(
            item
            for item in after.finalizations
            if item.accepted_transaction_id == request.request_id
        )
        consumed_bindings = tuple(
            item
            for item in after.stage_artifact_bindings
            if item.transition_id == finalization.finalize_transition_id
        )
        assert consumed_bindings
        forged_missing_lineage = replace(
            after,
            stage_artifact_bindings=tuple(
                item
                for item in after.stage_artifact_bindings
                if item != consumed_bindings[0]
            ),
        )
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            CoreRunDomainVerifier._verify_stage_chain(
                store,
                forged_missing_lineage,
                after_verified.contracts,
                after_verified.binding,
            )
    binding = next(
        item
        for item in after.receipt_checkout_bindings
        if item.transaction_id == request.request_id
    )
    checkout = next(
        item
        for item in after.checkout_revisions
        if item.checkout_revision_id == binding.post_checkout_revision_id
    )
    assert checkout.parent_checkout_revision_id == prior_binding.post_checkout_revision_id
    post_members = tuple(
        item
        for item in after.checkout_revision_members
        if item.checkout_revision_id == checkout.checkout_revision_id
    )
    assert {
        (item.canonical_path, item.blob_sha256, item.byte_size)
        for item in post_members
    } == {
        (item.canonical_path, item.blob_sha256, item.byte_size)
        for item in prior_members
    }

    revisions = {
        (item.artifact_id, item.revision): item for item in after.artifact_revisions
    }

    def forged_checkout_snapshot(artifact_revisions):
        rebuilt = build_checkout_revision(
            workspace_id=after.workspace_id,
            run_id=run_id,
            transaction_id=request.request_id,
            created_at=datetime.fromisoformat(
                checkout.created_at.replace("Z", "+00:00")
            ),
            artifact_revisions=artifact_revisions,
            parent_checkout_revision_id=prior_binding.post_checkout_revision_id,
        )
        forged_binding = binding.model_copy(
            update={"post_checkout_revision_id": rebuilt.record.checkout_revision_id}
        )
        receipt_payload = result.receipt.model_dump(
            mode="json", exclude_unset=False
        )
        receipt_payload["checkout_revisions"] = [
            {"checkout_revision_id": rebuilt.record.checkout_revision_id}
        ]
        forged_receipt = TransactionReceipt.model_validate(
            receipt_payload, strict=True
        )
        forged_snapshot = replace(
            after,
            checkout_revisions=tuple(
                rebuilt.record
                if item.checkout_revision_id == checkout.checkout_revision_id
                else item
                for item in after.checkout_revisions
            ),
            checkout_revision_members=tuple(
                item
                for item in after.checkout_revision_members
                if item.checkout_revision_id != checkout.checkout_revision_id
            )
            + rebuilt.members,
            receipt_checkout_bindings=tuple(
                forged_binding if item == binding else item
                for item in after.receipt_checkout_bindings
            ),
        )
        return forged_snapshot, forged_receipt, rebuilt

    parent_revisions = tuple(
        revisions[(item.artifact_id, item.artifact_revision)]
        for item in prior_members
    )
    omitted_snapshot, omitted_receipt, _omitted_checkout = forged_checkout_snapshot(
        parent_revisions[:-1]
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(omitted_snapshot, omitted_receipt)

    parent_keys = {
        (item.artifact_id, item.artifact_revision) for item in prior_members
    }
    extra_revision = next(
        item
        for item in after.artifact_revisions
        if (item.artifact_id, item.revision) not in parent_keys
    )
    added_snapshot, added_receipt, added_checkout = forged_checkout_snapshot(
        (*parent_revisions, extra_revision)
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(added_snapshot, added_receipt)

    reordered_members = tuple(
        item.model_copy(update={"ordinal": len(post_members) - item.ordinal - 1})
        if item.checkout_revision_id == checkout.checkout_revision_id
        else item
        for item in after.checkout_revision_members
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(
            replace(after, checkout_revision_members=reordered_members),
            result.receipt,
        )

    parent_record = next(
        item
        for item in after.checkout_revisions
        if item.checkout_revision_id == prior_binding.post_checkout_revision_id
    )
    parent_checkout = build_checkout_revision(
        workspace_id=after.workspace_id,
        run_id=run_id,
        transaction_id=parent_record.creator_transaction_id,
        created_at=datetime.fromisoformat(
            parent_record.created_at.replace("Z", "+00:00")
        ),
        artifact_revisions=parent_revisions,
        parent_checkout_revision_id=parent_record.parent_checkout_revision_id,
    )
    assert parent_checkout.record == parent_record
    publication_identity = PublicationIdentityV1.model_validate(
        {
            "schema_version": "briefloop-publication-identity/v1",
            "workspace_id": after.workspace_id,
            "run_id": run_id,
            "transaction_id": request.request_id,
            "checkout_revision_id": added_checkout.record.checkout_revision_id,
        },
        strict=True,
    )
    forged_intent, forged_publication_members = build_publication_intent(
        identity=publication_identity,
        pre=parent_checkout,
        post=added_checkout,
        capability_profile_sha256="0" * 64,
    )
    assert forged_publication_members
    publication_receipt_payload = added_receipt.model_dump(
        mode="json", exclude_unset=False
    )
    publication_receipt_payload["checkout_publication_intents"] = [
        {"checkout_revision_id": added_checkout.record.checkout_revision_id}
    ]
    publication_receipt = TransactionReceipt.model_validate(
        publication_receipt_payload, strict=True
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(
            replace(
                added_snapshot,
                checkout_publication_intents=(
                    *added_snapshot.checkout_publication_intents,
                    forged_intent,
                ),
                checkout_publication_members=(
                    *added_snapshot.checkout_publication_members,
                    *forged_publication_members,
                ),
            ),
            publication_receipt,
        )
    assert not any(
        item.identity.checkout_revision_id == checkout.checkout_revision_id
        for item in after.checkout_publication_intents
    )
    assert not any(
        item.identity.checkout_revision_id == checkout.checkout_revision_id
        for item in after.checkout_publication_members
    )
    assert {
        path: (workspace / path).read_bytes() for path in observed_projection
    } == observed_projection
    assert classify_terminal_legality(after).terminal_state == "finalized_local"
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        action = classify_core_run_next_action(
            CoreRunDomainVerifier().verify(store, run_id)
        )
    assert (
        action.action_kind,
        action.effect_kind,
        action.reason_code,
        action.stage_id,
        action.role_id,
        action.request_schema_id,
    ) == (
        "complete",
        "finalized_local",
        "local_finalization_complete",
        None,
        None,
        None,
    )
    assert not after.run_archives
    assert not after.package_ready_records
    assert not after.approvals
    assert not after.delivery_authorizations
    assert not after.delivery_attempts
    assert not after.delivery_results

    receipt_payload = result.receipt.model_dump(mode="json", exclude_unset=False)
    first_consumed = consumed_bindings[0]
    receipt_payload["artifact_revisions"] = [
        {
            "artifact_id": first_consumed.artifact_id,
            "revision": first_consumed.artifact_revision,
        }
    ]
    forged_extra_family = TransactionReceipt.model_validate(
        receipt_payload, strict=True
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(after, forged_extra_family)

    replay = service.complete_finalize(request)
    assert replay.status == "replayed"
    assert replay.receipt == result.receipt
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        assert store.current_revision == revision_after_commit


def test_finalize_complete_records_contamination_before_requested_effect(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _receipt, _fingerprint, render = _commit_finalize_render(workspace, run_id, clock)
    _commit_finalize_gate(workspace, run_id, clock, render)
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
        stage = next(item for item in snapshot.stage_states if item.stage_id == "finalize")
        reader = next(
            item
            for item in snapshot.artifact_revisions
            if item.artifact_id == render.reader_artifacts[0].artifact_id
            and item.revision == render.reader_artifacts[0].revision
        )
        gate_ids = sorted(
            item.evaluation_id
            for item in snapshot.gate_evaluations
            if item.stage_id == "finalize"
        )
        before_revision = snapshot.store_revision
    (workspace / reader.path).write_bytes(b"tampered reader brief\n")
    request = FinalizeCompleteRequest.model_validate(
        {
            "schema_version": FinalizeCompleteRequest.schema_id,
            "request_id": "REQ-TERMINAL-COMPLETE-TAMPER-001",
            "run_id": run_id,
            "render_id": render.render_id,
            "expected_finalize_stage_revision": stage.revision,
            "gate_evaluation_ids": gate_ids,
            "recovery_id": None,
            "expected_store_revision": before_revision,
        },
        strict=True,
    )
    result = CoreRunTerminalService(workspace, clock=clock).complete_finalize(request)
    assert result.status == "blocked"
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
    assert not snapshot.finalizations
    assert snapshot.run_integrity_records[-1].status == "contaminated"


def _terminal_event(
    *,
    event_id: str,
    run_id: str,
    transaction_id: str,
    event_type: str,
    reason: str,
    fingerprint: str,
    effect_kind: str,
    primary_record_id: str,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    bind: bool = True,
) -> EventEnvelope:
    return _record(
        EventEnvelope,
        event_id=event_id,
        run_id=run_id,
        event_type=event_type,
        created_at=core_fixture.NOW,
        actor="system",
        transaction_id=transaction_id,
        stage_id=stage_id,
        artifact_id=artifact_id,
        decision="record",
        reason=reason,
        metadata={},
        intake_binding=None,
        core_run_binding=(
            CoreRunEventBinding.model_validate(
                {
                    "request_id": transaction_id,
                    "request_fingerprint": fingerprint,
                    "effect_kind": effect_kind,
                    "primary_record_id": primary_record_id,
                    "outcome": "committed",
                },
                strict=True,
            )
            if bind
            else None
        ),
    )


def _archive_usage(artifact_id: str) -> str:
    if artifact_id.startswith("run_contract_"):
        return "control"
    if artifact_id.endswith("quality_gate_report"):
        return "gate"
    if artifact_id == "reader_brief":
        return "reader"
    if artifact_id in {"claim_ledger", "audit_report"}:
        return "evidence"
    return "workflow"


def _commit_finalize_gate(
    workspace: Path,
    run_id: str,
    clock: object,
    render: FinalizeRenderRecord,
    *,
    sequence: int = 1,
) -> tuple[TransactionReceipt, str, tuple[GateEvaluationRecord, ...]]:
    transaction_id = f"REQ-TERMINAL-FINALIZE-GATE-{sequence:03d}"
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        before = verified.snapshot
        artifacts = {item.artifact_id: item for item in before.artifacts}
        revisions = {
            (item.artifact_id, item.revision): item
            for item in before.artifact_revisions
        }

        def current_revision(artifact_id: str) -> ArtifactRevision:
            record = artifacts[artifact_id]
            return revisions[(artifact_id, record.current_revision)]

        candidate = current_revision("candidate_claims")
        screened = current_revision("screened_candidates")
        reader_revisions = [
            revisions[(item.artifact_id, item.revision)]
            for item in render.reader_artifacts
        ]
        audit_report = revisions[
            (render.audit_report.artifact_id, render.audit_report.revision)
        ]
        ledger = current_revision("claim_ledger")
        assessed = [
            (candidate, "screened_candidates"),
            (screened, "screened_candidates"),
            *((item, "reader_artifact") for item in reader_revisions),
            (audit_report, "audit_report"),
            (ledger, "ledger"),
        ]
        input_hashes = [
            {
                "artifact_id": item.artifact_id,
                "revision": item.revision,
                "sha256": item.sha256,
                "usage": usage,
            }
            for item, usage in assessed
        ]
        expected_store_revision = before.store_revision
        expected_report_revision = (
            artifacts["finalize_quality_gate_report"].current_revision
            if "finalize_quality_gate_report" in artifacts
            else 0
        )
    request = _record(
        GateCheckRequest,
        request_id=transaction_id,
        run_id=run_id,
        stage_id="finalize",
        expected_store_revision=expected_store_revision,
        expected_report_artifact_revision=expected_report_revision,
        expected_input_artifacts=[
            {"artifact_id": item.artifact_id, "revision": item.revision}
            for item, _usage in assessed
        ],
    )
    result = GateEvaluationService(workspace, clock=clock).evaluate(request)
    assert result.status == "committed", result.to_dict()
    assert result.receipt is not None
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        evaluations = tuple(
            item
            for item in store.load_snapshot(run_id).gate_evaluations
            if item.gate_batch_id == result.primary_record_id
        )
    return (
        result.receipt,
        canonical_fingerprint(request.model_dump(mode="json", exclude_unset=False)),
        evaluations,
    )


def test_finalize_complete_rejects_stale_gate_batch_before_commit(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _first_receipt, _first_fingerprint, first_evaluations = _commit_finalize_gate(
        workspace,
        run_id,
        clock,
        render,
        sequence=1,
    )
    _second_receipt, _second_fingerprint, second_evaluations = _commit_finalize_gate(
        workspace,
        run_id,
        clock,
        render,
        sequence=2,
    )
    assert {item.report_artifact.revision for item in first_evaluations} == {1}
    assert {item.report_artifact.revision for item in second_evaluations} == {2}

    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
        finalize_stage = next(
            item for item in snapshot.stage_states if item.stage_id == "finalize"
        )
        before_revision = snapshot.store_revision
    request = FinalizeCompleteRequest.model_validate(
        {
            "schema_version": FinalizeCompleteRequest.schema_id,
            "request_id": "REQ-TERMINAL-STALE-FINALIZE-GATE-001",
            "run_id": run_id,
            "render_id": render.render_id,
            "expected_finalize_stage_revision": finalize_stage.revision,
            "gate_evaluation_ids": sorted(
                item.evaluation_id for item in first_evaluations
            ),
            "recovery_id": None,
            "expected_store_revision": before_revision,
        },
        strict=True,
    )
    result = CoreRunTerminalService(workspace, clock=clock).complete_finalize(request)
    assert (result.status, result.error_code) == (
        "failed_uncommitted",
        "finalize_gate_blocked",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
        assert snapshot.store_revision == before_revision
        assert not snapshot.finalizations
        assert not snapshot.run_archives
        assert not snapshot.package_ready_records


def _commit_finalize_complete(
    workspace: Path,
    run_id: str,
    clock: object,
    render: FinalizeRenderRecord,
) -> tuple[TransactionReceipt, str, PackageReadyRecord]:
    transaction_id = "REQ-TERMINAL-FINALIZE-COMPLETE-001"
    finalization_id = "FINALIZATION-TERMINAL-PERSISTED-001"
    transition_id = "TRANSITION-TERMINAL-FINALIZE-001"
    final_event_id = "EVT-TERMINAL-FINALIZED-001"
    archive_id = "ARCHIVE-TERMINAL-PERSISTED-001"
    archive_event_id = "EVT-TERMINAL-ARCHIVE-PERSISTED-001"
    package_id = "PACKAGE-TERMINAL-PERSISTED-001"
    package_event_id = "EVT-TERMINAL-PACKAGE-PERSISTED-001"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "finalize_complete",
            "render_id": render.render_id,
            "finalization_id": finalization_id,
        }
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        before = store.load_snapshot(run_id)
        finalize_state = next(
            item for item in before.stage_states if item.stage_id == "finalize"
        )
        selected_gates = tuple(
            sorted(
                (
                    item
                    for item in before.gate_evaluations
                    if item.stage_id == "finalize"
                    and not item.blocking
                    and item.status in {"pass", "warning"}
                ),
                key=lambda item: item.gate_id,
            )
        )
        assert {item.gate_id for item in selected_gates} == set(GATE_IDS)
        assert len({item.gate_batch_id for item in selected_gates}) == 1
        current_revisions = {
            (item.artifact_id, item.current_revision): next(
                revision
                for revision in before.artifact_revisions
                if revision.artifact_id == item.artifact_id
                and revision.revision == item.current_revision
            )
            for item in before.artifacts
            if item.current_revision > 0
        }
        ordered_current = sorted(
            current_revisions.values(),
            key=lambda item: (item.artifact_id, item.revision),
        )
        archive_bytes = (
            canonical_json_bytes(
                {
                    "schema_version": "briefloop.core_v2_run_archive.v1",
                    "run_id": run_id,
                    "finalization_id": finalization_id,
                    "artifacts": [
                        {
                            "artifact_id": item.artifact_id,
                            "revision": item.revision,
                            "sha256": item.sha256,
                        }
                        for item in ordered_current
                    ],
                }
            )
            + b"\n"
        )
        archive_revision = _record(
            ArtifactRevision,
            run_id=run_id,
            artifact_id="core_v2_run_archive",
            revision=1,
            path="output/intermediate/core_v2_run_archive.json",
            sha256=sha256_hex(archive_bytes),
            size_bytes=len(archive_bytes),
            frozen=True,
            producer_kind="control_tool",
            producer_id="core-v2-finalize-complete",
            created_at=core_fixture.NOW,
        )
        reader_revisions = [
            current_revisions[(item.artifact_id, item.revision)]
            for item in render.reader_artifacts
        ]
        package_bytes = (
            canonical_json_bytes(
                {
                    "schema_version": "briefloop.core_v2_package_manifest.v1",
                    "run_id": run_id,
                    "finalization_id": finalization_id,
                    "archive": {
                        "artifact_id": archive_revision.artifact_id,
                        "revision": archive_revision.revision,
                        "sha256": archive_revision.sha256,
                    },
                    "reader_artifacts": [
                        {
                            "artifact_id": item.artifact_id,
                            "revision": item.revision,
                            "sha256": item.sha256,
                        }
                        for item in reader_revisions
                    ],
                }
            )
            + b"\n"
        )
        package_revision = _record(
            ArtifactRevision,
            run_id=run_id,
            artifact_id="core_v2_package_manifest",
            revision=1,
            path="output/intermediate/core_v2_package_manifest.json",
            sha256=sha256_hex(package_bytes),
            size_bytes=len(package_bytes),
            frozen=True,
            producer_kind="control_tool",
            producer_id="core-v2-finalize-complete",
            created_at=core_fixture.NOW,
        )
        transition = _record(
            StageTransitionRecord,
            transition_id=transition_id,
            run_id=run_id,
            stage_id="finalize",
            transition_kind="complete",
            requested_decision="continue",
            prior_status=finalize_state.status,
            prior_revision=finalize_state.revision,
            result_status="complete",
            result_revision=finalize_state.revision + 1,
            reason="Finalize Gate passed and immutable package was created",
            run_contract_fingerprint=before.run_contract_bindings[
                0
            ].contract_fingerprint,
            actor="system",
            producer_invocation_id=None,
            producer_tool_id="core-v2-finalize-complete",
            created_at=core_fixture.NOW,
            transition_event_id=final_event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        finalization = _record(
            FinalizationRecord,
            finalization_id=finalization_id,
            run_id=run_id,
            render_id=render.render_id,
            finalize_transition_id=transition_id,
            finalize_gate_batch_id=selected_gates[0].gate_batch_id,
            finalize_gate_evaluation_ids=sorted(
                item.evaluation_id for item in selected_gates
            ),
            recovery_id=None,
            integrity_revision=before.run_integrity_records[-1].integrity_revision,
            finalized_at=core_fixture.NOW,
            finalization_event_id=final_event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        archive = _record(
            RunArchiveRecord,
            archive_id=archive_id,
            run_id=run_id,
            finalization_id=finalization_id,
            archive_artifact={
                "artifact_id": archive_revision.artifact_id,
                "revision": archive_revision.revision,
            },
            manifest_sha256=archive_revision.sha256,
            included_count=len(ordered_current),
            created_at=core_fixture.NOW,
            archive_event_id=archive_event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        package_members = [*reader_revisions, archive_revision, package_revision]
        package = _record(
            PackageReadyRecord,
            package_id=package_id,
            run_id=run_id,
            finalization_id=finalization_id,
            archive_id=archive_id,
            package_manifest_artifact={
                "artifact_id": package_revision.artifact_id,
                "revision": package_revision.revision,
            },
            package_manifest_sha256=package_revision.sha256,
            artifact_count=len(package_members),
            created_at=core_fixture.NOW,
            package_event_id=package_event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("finalize_complete"),
            before.store_revision,
        )
        unit.put_stage_state(
            _record(
                StageState,
                run_id=run_id,
                stage_id="finalize",
                status="complete",
                revision=finalize_state.revision + 1,
                updated_at=core_fixture.NOW,
            )
        )
        unit.append_stage_transition(transition)
        first_gate_id = selected_gates[0].evaluation_id
        consumed_revisions = {
            (item.artifact_id, item.artifact_revision): current_revisions[
                (item.artifact_id, item.artifact_revision)
            ]
            for item in before.gate_artifact_bindings
            if item.evaluation_id == first_gate_id
        }
        consumed_revisions.update(
            {(item.artifact_id, item.revision): item for item in reader_revisions}
        )
        transition_inputs = sorted(
            [
                *((item, "consumed") for item in consumed_revisions.values()),
                (archive_revision, "produced"),
                (package_revision, "produced"),
            ],
            key=lambda item: (item[0].artifact_id, item[0].revision),
        )
        for position, (revision, usage) in enumerate(transition_inputs):
            unit.put_stage_artifact_binding(
                _record(
                    StageArtifactBinding,
                    run_id=run_id,
                    transition_id=transition_id,
                    position=position,
                    artifact_id=revision.artifact_id,
                    artifact_revision=revision.revision,
                    artifact_sha256=revision.sha256,
                    usage=usage,
                    accepted_transaction_id=transaction_id,
                )
            )
        for evaluation in selected_gates:
            unit.put_stage_gate_binding(
                _record(
                    StageGateBinding,
                    run_id=run_id,
                    transition_id=transition_id,
                    gate_id=evaluation.gate_id,
                    evaluation_id=evaluation.evaluation_id,
                    accepted_transaction_id=transaction_id,
                )
            )
        for record, revision, content in (
            (
                _record(
                    ArtifactRecord,
                    run_id=run_id,
                    artifact_id=archive_revision.artifact_id,
                    current_revision=1,
                    status="valid",
                    required=True,
                    path=archive_revision.path,
                    format="json",
                ),
                archive_revision,
                archive_bytes,
            ),
            (
                _record(
                    ArtifactRecord,
                    run_id=run_id,
                    artifact_id=package_revision.artifact_id,
                    current_revision=1,
                    status="valid",
                    required=True,
                    path=package_revision.path,
                    format="json",
                ),
                package_revision,
                package_bytes,
            ),
        ):
            unit.put_artifact(record)
            unit.put_artifact_revision(revision, content)
        unit.put_finalization(finalization)
        unit.put_run_archive(archive)
        for position, revision in enumerate(ordered_current):
            unit.put_run_archive_artifact_binding(
                _record(
                    RunArchiveArtifactBinding,
                    run_id=run_id,
                    archive_id=archive_id,
                    position=position,
                    artifact_id=revision.artifact_id,
                    artifact_revision=revision.revision,
                    artifact_sha256=revision.sha256,
                    usage=_archive_usage(revision.artifact_id),
                    accepted_transaction_id=transaction_id,
                )
            )
        unit.put_package_ready(package)
        for position, revision in enumerate(package_members):
            unit.put_package_artifact_binding(
                _record(
                    PackageArtifactBinding,
                    run_id=run_id,
                    package_id=package_id,
                    position=position,
                    artifact_id=revision.artifact_id,
                    artifact_revision=revision.revision,
                    artifact_sha256=revision.sha256,
                    usage=(
                        "archive"
                        if revision.artifact_id == archive_revision.artifact_id
                        else "manifest"
                        if revision.artifact_id == package_revision.artifact_id
                        else "reader"
                    ),
                    accepted_transaction_id=transaction_id,
                )
            )
        for event in (
            _terminal_event(
                event_id=final_event_id,
                run_id=run_id,
                transaction_id=transaction_id,
                event_type="stage_status_changed",
                stage_id="finalize",
                reason="finalized",
                fingerprint=request_fingerprint,
                effect_kind="finalize_complete",
                primary_record_id=finalization_id,
            ),
            _terminal_event(
                event_id=archive_event_id,
                run_id=run_id,
                transaction_id=transaction_id,
                event_type="run_archived",
                artifact_id=archive_revision.artifact_id,
                reason="immutable run archive created",
                fingerprint=request_fingerprint,
                effect_kind="finalize_complete",
                primary_record_id=archive_id,
                bind=False,
            ),
            _terminal_event(
                event_id=package_event_id,
                run_id=run_id,
                transaction_id=transaction_id,
                event_type="decision_recorded",
                artifact_id=package_revision.artifact_id,
                reason="package ready",
                fingerprint=request_fingerprint,
                effect_kind="finalize_complete",
                primary_record_id=package_id,
                bind=False,
            ),
        ):
            unit.append_event(event)
        receipt = _commit_core_fixture(
            store,
            unit,
            observer=lambda _receipt: CoreRunDomainVerifier().verify(store, run_id),
        )
        assert [item.finalization_id for item in receipt.finalizations] == [
            finalization_id
        ]
        assert [item.transition_id for item in receipt.stage_transitions] == [
            transition_id
        ]
        assert [
            (item.transition_id, item.position)
            for item in receipt.stage_artifact_bindings
        ] == [(transition_id, position) for position in range(len(transition_inputs))]
        assert {
            (item.transition_id, item.gate_id) for item in receipt.stage_gate_bindings
        } == {(transition_id, item.gate_id) for item in selected_gates}
        assert [item.archive_id for item in receipt.run_archives] == [archive_id]
        assert [
            (item.archive_id, item.position)
            for item in receipt.run_archive_artifact_bindings
        ] == [(archive_id, position) for position in range(len(ordered_current))]
        assert [item.package_id for item in receipt.package_ready_records] == [
            package_id
        ]
        assert [
            (item.package_id, item.position)
            for item in receipt.package_artifact_bindings
        ] == [(package_id, position) for position in range(len(package_members))]
        assert receipt.event_ids == [
            final_event_id,
            archive_event_id,
            package_event_id,
        ]
    return receipt, request_fingerprint, package


def test_terminal_approval_integrity_preflight_blocks_before_business_effect(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        reader = next(
            item
            for item in verified.snapshot.artifact_revisions
            if item.artifact_id == render.reader_artifacts[0].artifact_id
            and item.revision == render.reader_artifacts[0].revision
        )
        before_revision = verified.snapshot.store_revision
    (workspace / reader.path).write_bytes(b"tampered after package ready\n")

    request = InternalApprovalRequest.model_validate(
        {
            "schema_version": InternalApprovalRequest.schema_id,
            "request_id": "REQ-TERMINAL-APPROVAL-CONTAMINATED-001",
            "run_id": run_id,
            "package_id": package.package_id,
            "approval_id": "APPROVAL-TERMINAL-CONTAMINATED-001",
            "mode": "internal_management_review",
            "role": "content_owner",
            "decision": "approve",
            "reason": "must not survive integrity preflight",
            "actor_id": "human-reviewer",
            "expected_store_revision": before_revision,
        },
        strict=True,
    )
    result = CoreRunTerminalService(workspace, clock=clock).record_internal_approval(
        request
    )
    assert (result.status, result.error_code) == (
        "blocked",
        "frozen_artifact_contaminated",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
        assert not snapshot.approvals
        assert snapshot.store_revision == before_revision + 1
        assert snapshot.run_integrity_records[-1].status == "contaminated"


class _InjectedTerminalFailure(RuntimeError):
    pass


def _commit_internal_approval(
    workspace: Path,
    run_id: str,
    clock: object,
    package: PackageReadyRecord,
    *,
    fail_before_commit: bool = False,
    sequence: int = 1,
    decision: str = "approve",
) -> tuple[TransactionReceipt, str, Approval]:
    transaction_id = f"REQ-TERMINAL-APPROVAL-{sequence:03d}"
    approval_id = f"APPROVAL-TERMINAL-PERSISTED-{sequence:03d}"
    event_id = f"EVT-TERMINAL-APPROVAL-PERSISTED-{sequence:03d}"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "internal_approval",
            "approval_id": approval_id,
            "package_id": package.package_id,
            "decision": decision,
        }
    )

    def failure_hook(stage: str) -> None:
        if fail_before_commit and stage == "before_commit":
            raise _InjectedTerminalFailure("injected terminal failure")

    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
        _failure_hook=failure_hook,
    ) as store:
        before = store.load_snapshot(run_id)
        approval = _record(
            Approval,
            approval_id=approval_id,
            run_id=run_id,
            mode="internal_management_review",
            role="content_owner",
            decision=decision,
            reason="Synthetic internal content-owner approval",
            actor_id="HUMAN-TERMINAL-001",
            recorded_at=core_fixture.NOW,
            boundary=(
                "internal_review_approval_records_only_not_public_release_authorization"
            ),
            event_id=event_id,
        )
        binding = _record(
            ApprovalPackageBinding,
            run_id=run_id,
            approval_id=approval_id,
            package_id=package.package_id,
            accepted_transaction_id=transaction_id,
        )
        event = _terminal_event(
            event_id=event_id,
            run_id=run_id,
            transaction_id=transaction_id,
            event_type="human_approval_recorded",
            reason="internal package approval recorded",
            fingerprint=request_fingerprint,
            effect_kind="internal_approval",
            primary_record_id=approval_id,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("internal_approval"),
            before.store_revision,
        )
        unit.put_approval(approval)
        unit.put_approval_package_binding(binding)
        unit.append_event(event)
        receipt = _commit_core_fixture(
            store,
            unit,
            observer=lambda _receipt: CoreRunDomainVerifier().verify(store, run_id),
        )
        assert [item.approval_id for item in receipt.approvals] == [approval_id]
        assert [
            (item.approval_id, item.package_id)
            for item in receipt.approval_package_bindings
        ] == [(approval_id, package.package_id)]
        assert receipt.event_ids == [event_id]
    return receipt, request_fingerprint, approval


def _commit_delivery_authorization(
    workspace: Path,
    run_id: str,
    clock: object,
    package: PackageReadyRecord,
    *,
    sequence: int = 1,
    approval_mode: str = "internal_management_review",
    decision: str = "authorize",
    prior_authorization_id: str | None = None,
    retry_of_attempt_id: str | None = None,
    purpose: str = "initial_attempt",
    target: str = "local",
    channel: str = "filesystem",
    recipient_fingerprint: str = "d" * 64,
    verify: bool = True,
) -> tuple[TransactionReceipt, str, DeliveryAuthorizationRecord]:
    transaction_id = f"REQ-TERMINAL-AUTHORIZATION-{sequence:03d}"
    authorization_id = f"AUTH-TERMINAL-PERSISTED-{sequence:03d}"
    event_id = f"EVT-TERMINAL-AUTHORIZATION-PERSISTED-{sequence:03d}"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "delivery_authorization",
            "authorization_id": authorization_id,
            "package_id": package.package_id,
            "prior_authorization_id": prior_authorization_id,
            "approval_mode": approval_mode,
            "retry_of_attempt_id": retry_of_attempt_id,
            "purpose": purpose,
            "decision": decision,
            "target": target,
            "channel": channel,
            "recipient_fingerprint": recipient_fingerprint,
        }
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        before = store.load_snapshot(run_id)
        authorization = _record(
            DeliveryAuthorizationRecord,
            authorization_id=authorization_id,
            run_id=run_id,
            package_id=package.package_id,
            prior_authorization_id=prior_authorization_id,
            approval_mode=approval_mode,
            retry_of_attempt_id=retry_of_attempt_id,
            purpose=purpose,
            decision=decision,
            target=target,
            channel=channel,
            recipient_fingerprint=recipient_fingerprint,
            actor_id="HUMAN-TERMINAL-001",
            reason="Authorize deterministic local package preparation",
            recorded_at=core_fixture.NOW,
            authorization_event_id=event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        event = _terminal_event(
            event_id=event_id,
            run_id=run_id,
            transaction_id=transaction_id,
            event_type="decision_recorded",
            reason="delivery authorization recorded",
            fingerprint=request_fingerprint,
            effect_kind="delivery_authorization",
            primary_record_id=authorization_id,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("delivery_authorization"),
            before.store_revision,
        )
        unit.put_delivery_authorization(authorization)
        unit.append_event(event)
        observer = (
            (lambda _receipt: CoreRunDomainVerifier().verify(store, run_id))
            if verify
            else None
        )
        receipt = _commit_core_fixture(store, unit, observer=observer)
        assert [item.authorization_id for item in receipt.delivery_authorizations] == [
            authorization_id
        ]
        assert receipt.event_ids == [event_id]
    return receipt, request_fingerprint, authorization


def _commit_delivery_attempt(
    workspace: Path,
    run_id: str,
    clock: object,
    authorization: DeliveryAuthorizationRecord,
    *,
    sequence: int = 1,
    verify: bool = True,
) -> tuple[TransactionReceipt, str, DeliveryAttemptRecord]:
    transaction_id = f"REQ-TERMINAL-ATTEMPT-{sequence:03d}"
    attempt_id = f"ATTEMPT-TERMINAL-PERSISTED-{sequence:03d}"
    event_id = f"EVT-TERMINAL-ATTEMPT-PERSISTED-{sequence:03d}"
    operation_id = f"LOCAL-PACKAGE-TERMINAL-{sequence:03d}"
    connector_fingerprint = "e" * 64
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "delivery_attempt",
            "attempt_id": attempt_id,
            "authorization_id": authorization.authorization_id,
            "connector_operation_id": operation_id,
            "connector_request_fingerprint": connector_fingerprint,
        }
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        before = store.load_snapshot(run_id)
        attempt = _record(
            DeliveryAttemptRecord,
            attempt_id=attempt_id,
            run_id=run_id,
            package_id=authorization.package_id,
            authorization_id=authorization.authorization_id,
            target=authorization.target,
            channel=authorization.channel,
            recipient_fingerprint=authorization.recipient_fingerprint,
            connector_operation_id=operation_id,
            connector_request_fingerprint=connector_fingerprint,
            created_at=core_fixture.NOW,
            attempt_event_id=event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        event = _terminal_event(
            event_id=event_id,
            run_id=run_id,
            transaction_id=transaction_id,
            event_type="delivery_attempted",
            reason="delivery attempt prepared before connector call",
            fingerprint=request_fingerprint,
            effect_kind="delivery_attempt",
            primary_record_id=attempt_id,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("delivery_attempt"),
            before.store_revision,
        )
        unit.put_delivery_attempt(attempt)
        unit.append_event(event)
        observer = (
            (lambda _receipt: CoreRunDomainVerifier().verify(store, run_id))
            if verify
            else None
        )
        receipt = _commit_core_fixture(store, unit, observer=observer)
        assert [item.attempt_id for item in receipt.delivery_attempts] == [attempt_id]
        assert receipt.event_ids == [event_id]
    return receipt, request_fingerprint, attempt


def _commit_delivery_result(
    workspace: Path,
    run_id: str,
    clock: object,
    package: PackageReadyRecord,
    attempt: DeliveryAttemptRecord,
    *,
    sequence: int = 1,
    status: str = "bundle_prepared",
    prior_result_id: str | None = None,
    reconciliation_authorization_id: str | None = None,
    verify: bool = True,
) -> tuple[TransactionReceipt, str, DeliveryResultRecord]:
    transaction_id = f"REQ-TERMINAL-RESULT-{sequence:03d}"
    result_id = f"DELIVERY-RESULT-TERMINAL-PERSISTED-{sequence:03d}"
    event_id = f"EVT-TERMINAL-RESULT-PERSISTED-{sequence:03d}"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "delivery_result",
            "result_id": result_id,
            "attempt_id": attempt.attempt_id,
            "status": status,
            "evidence_sha256": package.package_manifest_sha256,
        }
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        before = store.load_snapshot(run_id)
        result = _record(
            DeliveryResultRecord,
            result_id=result_id,
            run_id=run_id,
            attempt_id=attempt.attempt_id,
            prior_result_id=prior_result_id,
            reconciliation_authorization_id=reconciliation_authorization_id,
            status=status,
            adapter_id=(
                "briefloop-local-package"
                if attempt.target == "local"
                else "terminal-test-adapter"
            ),
            adapter_version="V2",
            connector_operation_id=attempt.connector_operation_id,
            evidence_sha256=(
                package.package_manifest_sha256
                if attempt.target == "local"
                else "f" * 64
            ),
            evidence_artifact=(
                package.package_manifest_artifact if attempt.target == "local" else None
            ),
            recorded_at=core_fixture.NOW,
            result_event_id=event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        event = _terminal_event(
            event_id=event_id,
            run_id=run_id,
            transaction_id=transaction_id,
            event_type={
                "bundle_prepared": "delivery_bundle_prepared",
                "draft_created": "delivery_draft_created",
                "succeeded": "delivery_succeeded",
                "failed": "delivery_failed",
                "outcome_unknown": "decision_recorded",
            }[status],
            artifact_id=(
                package.package_manifest_artifact.artifact_id
                if attempt.target == "local"
                else None
            ),
            reason="typed local package observation recorded",
            fingerprint=request_fingerprint,
            effect_kind="delivery_result",
            primary_record_id=result_id,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("delivery_result"),
            before.store_revision,
        )
        unit.put_delivery_result(result)
        unit.append_event(event)
        observer = (
            (lambda _receipt: CoreRunDomainVerifier().verify(store, run_id))
            if verify
            else None
        )
        receipt = _commit_core_fixture(store, unit, observer=observer)
        assert [item.result_id for item in receipt.delivery_results] == [result_id]
        assert receipt.event_ids == [event_id]
    return receipt, request_fingerprint, result


def _external_unknown_branch(
    tmp_path: Path,
) -> tuple[
    Path,
    str,
    object,
    PackageReadyRecord,
    DeliveryAuthorizationRecord,
    DeliveryAttemptRecord,
    DeliveryResultRecord,
]:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    _authorization_receipt, _authorization_fingerprint, authorization = (
        _commit_delivery_authorization(
            workspace,
            run_id,
            clock,
            package,
            approval_mode="internal_draft",
            target="gmail",
            channel="email",
            recipient_fingerprint="a" * 64,
        )
    )
    _attempt_receipt, _attempt_fingerprint, attempt = _commit_delivery_attempt(
        workspace,
        run_id,
        clock,
        authorization,
    )
    _result_receipt, _result_fingerprint, unknown = _commit_delivery_result(
        workspace,
        run_id,
        clock,
        package,
        attempt,
        status="outcome_unknown",
    )
    return workspace, run_id, clock, package, authorization, attempt, unknown


def test_next_action_routes_unknown_delivery_to_reconciliation(tmp_path: Path) -> None:
    workspace, run_id, clock, *_rest = _external_unknown_branch(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        action = classify_core_run_next_action(
            CoreRunDomainVerifier().verify(store, run_id)
        )
    assert (action.action_kind, action.effect_kind) == (
        "human_decision",
        "delivery_reconciliation",
    )


def test_terminal_effect_chain_rolls_back_replays_and_survives_restart(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        before_failure = store.current_revision
    with pytest.raises(_InjectedTerminalFailure):
        _commit_internal_approval(
            workspace,
            run_id,
            clock,
            package,
            fail_before_commit=True,
        )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
        assert store.current_revision == before_failure
        assert all(
            item.transaction_id != "REQ-TERMINAL-APPROVAL-001"
            for item in snapshot.transactions
        )
        assert not snapshot.approvals
        assert not snapshot.approval_package_bindings

    approval_receipt, approval_fingerprint, approval = _commit_internal_approval(
        workspace,
        run_id,
        clock,
        package,
    )
    authorization_receipt, authorization_fingerprint, authorization = (
        _commit_delivery_authorization(workspace, run_id, clock, package)
    )
    attempt_receipt, attempt_fingerprint, attempt = _commit_delivery_attempt(
        workspace,
        run_id,
        clock,
        authorization,
    )
    result_receipt, result_fingerprint, result = _commit_delivery_result(
        workspace,
        run_id,
        clock,
        package,
        attempt,
    )

    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        snapshot = verified.snapshot
        assert snapshot.approvals == (approval,)
        assert snapshot.delivery_authorizations == (authorization,)
        assert snapshot.delivery_attempts == (attempt,)
        assert snapshot.delivery_results == (result,)
        assert classify_terminal_state(snapshot).state == "package_ready"
        revision = store.current_revision
        for receipt, fingerprint, primary_id in (
            (approval_receipt, approval_fingerprint, approval.approval_id),
            (
                authorization_receipt,
                authorization_fingerprint,
                authorization.authorization_id,
            ),
            (attempt_receipt, attempt_fingerprint, attempt.attempt_id),
            (result_receipt, result_fingerprint, result.result_id),
        ):
            replay = resolve_core_replay(
                store,
                run_id=run_id,
                request_id=receipt.transaction_id,
                request_fingerprint=fingerprint,
            )
            assert replay is not None
            assert replay.status == "replayed"
            assert replay.receipt == receipt
            assert replay.primary_record_id == primary_id
        with pytest.raises(CoreRunError) as error:
            resolve_core_replay(
                store,
                run_id=run_id,
                request_id=authorization_receipt.transaction_id,
                request_fingerprint="0" * 64,
            )
        assert error.value.code == "submission_replay_conflict"
        assert store.current_revision == revision


def test_terminal_authorization_is_recordable_before_approval_but_not_consumable(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path / "required")
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    _authorization_receipt, _authorization_fingerprint, authorization = (
        _commit_delivery_authorization(workspace, run_id, clock, package)
    )
    attempt_subject = TerminalEffectSubject(
        package_id=package.package_id,
        authorization_id=authorization.authorization_id,
        target=authorization.target,
        channel=authorization.channel,
        recipient_fingerprint=authorization.recipient_fingerprint,
        attempt_id="ATTEMPT-PREFLIGHT-001",
        connector_operation_id="CONNECTOR-PREFLIGHT-001",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        pre_approval = CoreRunDomainVerifier().verify(store, run_id).snapshot
        assert classify_terminal_legality(pre_approval).terminal_state == (
            "approval_incomplete"
        )
        assert (
            classify_terminal_effect_authorization(
                pre_approval,
                CoreEffect.DELIVERY_ATTEMPT,
                attempt_subject,
            ).decision
            == "deny"
        )
        revision = store.current_revision
        assert store.current_revision == revision

    _commit_internal_approval(workspace, run_id, clock, package)
    _commit_delivery_attempt(workspace, run_id, clock, authorization)

    draft_workspace, draft_run_id, draft_clock = _finalize_ready_workspace(
        tmp_path / "draft"
    )
    _draft_render_receipt, _draft_render_fingerprint, draft_render = (
        _commit_finalize_render(draft_workspace, draft_run_id, draft_clock)
    )
    _commit_finalize_gate(draft_workspace, draft_run_id, draft_clock, draft_render)
    _draft_complete_receipt, _draft_complete_fingerprint, draft_package = (
        _commit_finalize_complete(
            draft_workspace,
            draft_run_id,
            draft_clock,
            draft_render,
        )
    )
    _draft_auth_receipt, _draft_auth_fingerprint, draft_authorization = (
        _commit_delivery_authorization(
            draft_workspace,
            draft_run_id,
            draft_clock,
            draft_package,
            approval_mode="internal_draft",
        )
    )
    draft_subject = TerminalEffectSubject(
        package_id=draft_package.package_id,
        authorization_id=draft_authorization.authorization_id,
        target=draft_authorization.target,
        channel=draft_authorization.channel,
        recipient_fingerprint=draft_authorization.recipient_fingerprint,
        attempt_id="ATTEMPT-DRAFT-PREFLIGHT-001",
        connector_operation_id="CONNECTOR-DRAFT-PREFLIGHT-001",
    )
    with SQLiteControlStore.open(
        draft_workspace / "briefloop.db",
        clock=draft_clock,
    ) as store:
        draft_snapshot = (
            CoreRunDomainVerifier()
            .verify(
                store,
                draft_run_id,
            )
            .snapshot
        )
        draft_legality = classify_terminal_legality(draft_snapshot)
        assert draft_legality.required_roles == ()
        assert draft_legality.approval_complete is True
        assert (
            classify_terminal_effect_authorization(
                draft_snapshot,
                CoreEffect.DELIVERY_ATTEMPT,
                draft_subject,
            ).decision
            == "allow"
        )
        assert (
            classify_terminal_effect_authorization(
                draft_snapshot,
                CoreEffect.DELIVERY_ATTEMPT,
                replace(
                    draft_subject,
                    recipient_fingerprint="0" * 64,
                ),
            ).decision
            == "deny"
        )
    _draft_attempt_receipt, _draft_attempt_fingerprint, draft_attempt = (
        _commit_delivery_attempt(
            draft_workspace,
            draft_run_id,
            draft_clock,
            draft_authorization,
        )
    )
    collision_attempt = draft_attempt.model_copy(
        update={
            "attempt_id": "ATTEMPT-UNRELATED-COLLISION-001",
            "authorization_id": "AUTH-UNRELATED-COLLISION-001",
        }
    )
    assert (
        classify_terminal_effect_authorization(
            replace(draft_snapshot, delivery_attempts=(collision_attempt,)),
            CoreEffect.DELIVERY_ATTEMPT,
            replace(
                draft_subject,
                connector_operation_id=collision_attempt.connector_operation_id,
            ),
        ).decision
        == "deny"
    )
    _deny_receipt, _deny_fingerprint, denied = _commit_delivery_authorization(
        draft_workspace,
        draft_run_id,
        draft_clock,
        draft_package,
        sequence=2,
        approval_mode="internal_draft",
        decision="deny",
        recipient_fingerprint="f" * 64,
    )
    with SQLiteControlStore.open(
        draft_workspace / "briefloop.db",
        clock=draft_clock,
    ) as store:
        denied_snapshot = (
            CoreRunDomainVerifier()
            .verify(
                store,
                draft_run_id,
            )
            .snapshot
        )
        denied_subject = replace(
            draft_subject,
            authorization_id=denied.authorization_id,
            recipient_fingerprint=denied.recipient_fingerprint,
            attempt_id="ATTEMPT-DENIED-PREFLIGHT-001",
        )
        assert (
            classify_terminal_effect_authorization(
                denied_snapshot,
                CoreEffect.DELIVERY_ATTEMPT,
                denied_subject,
            ).decision
            == "deny"
        )
        used_subject = replace(
            draft_subject,
            attempt_id="ATTEMPT-REUSE-PREFLIGHT-001",
            connector_operation_id=draft_attempt.connector_operation_id,
        )
        assert (
            classify_terminal_effect_authorization(
                denied_snapshot,
                CoreEffect.DELIVERY_ATTEMPT,
                used_subject,
            ).decision
            == "deny"
        )


def test_result_observation_uses_attempt_receipt_not_later_auth_or_approval(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    _commit_internal_approval(workspace, run_id, clock, package)
    _auth_receipt, _auth_fingerprint, authorization = _commit_delivery_authorization(
        workspace, run_id, clock, package
    )
    _attempt_receipt, _attempt_fingerprint, attempt = _commit_delivery_attempt(
        workspace,
        run_id,
        clock,
        authorization,
    )
    _commit_delivery_authorization(
        workspace,
        run_id,
        clock,
        package,
        sequence=2,
        decision="deny",
        recipient_fingerprint="f" * 64,
    )
    _commit_internal_approval(
        workspace,
        run_id,
        clock,
        package,
        sequence=2,
        decision="reject",
    )
    result_receipt, _result_fingerprint, result = _commit_delivery_result(
        workspace,
        run_id,
        clock,
        package,
        attempt,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = CoreRunDomainVerifier().verify(store, run_id).snapshot
        assert result in snapshot.delivery_results
        assert result_receipt.committed_revision == snapshot.store_revision


def test_result_reconciliation_consumes_current_exact_authorization_once(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    _auth_receipt, _auth_fingerprint, authorization = _commit_delivery_authorization(
        workspace,
        run_id,
        clock,
        package,
        approval_mode="internal_draft",
        target="gmail",
        channel="email",
        recipient_fingerprint="a" * 64,
    )
    _attempt_receipt, _attempt_fingerprint, attempt = _commit_delivery_attempt(
        workspace,
        run_id,
        clock,
        authorization,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = CoreRunDomainVerifier().verify(store, run_id).snapshot

    unknown = _record(
        DeliveryResultRecord,
        result_id="RESULT-RECONCILIATION-UNKNOWN-001",
        run_id=run_id,
        attempt_id=attempt.attempt_id,
        prior_result_id=None,
        reconciliation_authorization_id=None,
        status="outcome_unknown",
        adapter_id="gmail-adapter",
        adapter_version="V2",
        connector_operation_id=attempt.connector_operation_id,
        evidence_sha256="b" * 64,
        evidence_artifact=None,
        recorded_at=core_fixture.NOW,
        result_event_id="EVT-RECONCILIATION-UNKNOWN-001",
        accepted_transaction_id="REQ-RECONCILIATION-UNKNOWN-001",
        request_fingerprint="c" * 64,
    )
    reconciliation = authorization.model_copy(
        update={
            "authorization_id": "AUTH-RECONCILIATION-001",
            "prior_authorization_id": authorization.authorization_id,
            "retry_of_attempt_id": attempt.attempt_id,
            "purpose": "result_reconciliation",
            "authorization_event_id": "EVT-AUTH-RECONCILIATION-001",
            "accepted_transaction_id": "REQ-AUTH-RECONCILIATION-001",
            "request_fingerprint": "d" * 64,
        }
    )
    reconciliation_snapshot = replace(
        snapshot,
        delivery_authorizations=(authorization, reconciliation),
        delivery_results=(unknown,),
    )
    retry_subject = TerminalEffectSubject(
        package_id=package.package_id,
        approval_mode="internal_draft",
        authorization_id="AUTH-RETRY-LEGAL-001",
        prior_authorization_id=authorization.authorization_id,
        retry_of_attempt_id=attempt.attempt_id,
        purpose="retry_attempt",
        decision="authorize",
        target=authorization.target,
        channel=authorization.channel,
        recipient_fingerprint=authorization.recipient_fingerprint,
    )
    assert (
        classify_terminal_effect_authorization(
            replace(snapshot, delivery_results=(unknown,)),
            CoreEffect.DELIVERY_AUTHORIZE,
            retry_subject,
        ).decision
        == "allow"
    )
    assert (
        classify_terminal_effect_authorization(
            replace(snapshot, delivery_results=(unknown,)),
            CoreEffect.DELIVERY_AUTHORIZE,
            replace(retry_subject, retry_of_attempt_id="ATTEMPT-WRONG-001"),
        ).decision
        == "deny"
    )
    subject = TerminalEffectSubject(
        package_id=package.package_id,
        attempt_id=attempt.attempt_id,
        connector_operation_id=attempt.connector_operation_id,
        prior_result_id=unknown.result_id,
        reconciliation_authorization_id=reconciliation.authorization_id,
        result_status="succeeded",
    )
    assert (
        classify_terminal_effect_authorization(
            reconciliation_snapshot,
            CoreEffect.DELIVERY_RESULT,
            subject,
        ).decision
        == "allow"
    )
    assert (
        classify_terminal_effect_authorization(
            reconciliation_snapshot,
            CoreEffect.DELIVERY_RESULT,
            replace(subject, attempt_id="ATTEMPT-WRONG-001"),
        ).decision
        == "deny"
    )

    consumed = unknown.model_copy(
        update={
            "result_id": "RESULT-RECONCILIATION-CONSUMED-001",
            "prior_result_id": unknown.result_id,
            "reconciliation_authorization_id": reconciliation.authorization_id,
            "status": "succeeded",
        }
    )
    assert (
        classify_terminal_effect_authorization(
            replace(reconciliation_snapshot, delivery_results=(unknown, consumed)),
            CoreEffect.DELIVERY_RESULT,
            subject,
        ).decision
        == "deny"
    )
    required_approval = reconciliation.model_copy(
        update={"approval_mode": "internal_management_review"}
    )
    assert (
        classify_terminal_effect_authorization(
            replace(
                reconciliation_snapshot,
                delivery_authorizations=(authorization, required_approval),
            ),
            CoreEffect.DELIVERY_RESULT,
            subject,
        ).decision
        == "deny"
    )


def test_real_uow_retry_consumption_closes_older_reconciliation_branch(
    tmp_path: Path,
) -> None:
    (
        workspace,
        run_id,
        clock,
        package,
        initial,
        first_attempt,
        unknown,
    ) = _external_unknown_branch(tmp_path)
    _reconciliation_receipt, _reconciliation_fingerprint, reconciliation = (
        _commit_delivery_authorization(
            workspace,
            run_id,
            clock,
            package,
            sequence=2,
            approval_mode="internal_draft",
            prior_authorization_id=initial.authorization_id,
            retry_of_attempt_id=first_attempt.attempt_id,
            purpose="result_reconciliation",
            target=initial.target,
            channel=initial.channel,
            recipient_fingerprint=initial.recipient_fingerprint,
        )
    )
    _retry_receipt, _retry_fingerprint, retry = _commit_delivery_authorization(
        workspace,
        run_id,
        clock,
        package,
        sequence=3,
        approval_mode="internal_draft",
        prior_authorization_id=reconciliation.authorization_id,
        retry_of_attempt_id=first_attempt.attempt_id,
        purpose="retry_attempt",
        target=initial.target,
        channel=initial.channel,
        recipient_fingerprint=initial.recipient_fingerprint,
    )
    _retry_attempt_receipt, _retry_attempt_fingerprint, _retry_attempt = (
        _commit_delivery_attempt(workspace, run_id, clock, retry, sequence=2)
    )

    bad_authorization_receipt, _fingerprint, bad_authorization = (
        _commit_delivery_authorization(
            workspace,
            run_id,
            clock,
            package,
            sequence=4,
            approval_mode="internal_draft",
            prior_authorization_id=retry.authorization_id,
            retry_of_attempt_id=first_attempt.attempt_id,
            purpose="result_reconciliation",
            target=initial.target,
            channel=initial.channel,
            recipient_fingerprint=initial.recipient_fingerprint,
            verify=False,
        )
    )
    bad_result_receipt, _fingerprint, bad_result = _commit_delivery_result(
        workspace,
        run_id,
        clock,
        package,
        first_attempt,
        sequence=2,
        status="succeeded",
        prior_result_id=unknown.result_id,
        reconciliation_authorization_id=reconciliation.authorization_id,
        verify=False,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        history = store.load_history()
        authorization_prefix = history.snapshot_at_revision(
            run_id, bad_authorization_receipt.prior_revision
        )
        result_prefix = history.snapshot_at_revision(
            run_id, bad_result_receipt.prior_revision
        )
        assert (
            classify_terminal_effect_authorization(
                authorization_prefix,
                CoreEffect.DELIVERY_AUTHORIZE,
                TerminalEffectSubject(
                    package_id=bad_authorization.package_id,
                    approval_mode=bad_authorization.approval_mode,
                    authorization_id=bad_authorization.authorization_id,
                    prior_authorization_id=bad_authorization.prior_authorization_id,
                    retry_of_attempt_id=bad_authorization.retry_of_attempt_id,
                    purpose=bad_authorization.purpose,
                    decision=bad_authorization.decision,
                    target=bad_authorization.target,
                    channel=bad_authorization.channel,
                    recipient_fingerprint=bad_authorization.recipient_fingerprint,
                ),
            ).decision
            == "deny"
        )
        assert (
            classify_terminal_effect_authorization(
                result_prefix,
                CoreEffect.DELIVERY_RESULT,
                TerminalEffectSubject(
                    package_id=first_attempt.package_id,
                    attempt_id=first_attempt.attempt_id,
                    connector_operation_id=first_attempt.connector_operation_id,
                    prior_result_id=bad_result.prior_result_id,
                    reconciliation_authorization_id=(
                        bad_result.reconciliation_authorization_id
                    ),
                    result_status=bad_result.status,
                ),
            ).decision
            == "deny"
        )
        with pytest.raises(CoreRunError, match="historical_prefix_invalid"):
            CoreRunDomainVerifier().verify(store, run_id)


def _initialized_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request = deepcopy(CoreRunInitializeRequest.minimal_example)
    request.update(
        request_id="REQ-TERMINAL-PREFIX-INIT-001",
        workspace_id="WS-TERMINAL-PREFIX-001",
        run_id=RUN_ID,
        workspace_config_sha256=read_workspace_file(workspace, "config.yaml").sha256,
        sources_config_sha256=read_workspace_file(workspace, "sources.yaml").sha256,
    )
    adapter = dict(request["runtime_adapter_binding"])
    adapter["run_id"] = RUN_ID
    adapter.pop("binding_fingerprint", None)
    adapter["binding_fingerprint"] = canonical_fingerprint(adapter)
    request["runtime_adapter_binding"] = adapter
    result = CoreRunService(
        workspace,
        clock=lambda: datetime(2026, 7, 17, tzinfo=timezone.utc),
    ).initialize(CoreRunInitializeRequest.model_validate(request, strict=True))
    assert result.status == "committed"
    return workspace


def _record(model: type, **values: object):
    return model.model_validate(
        {"schema_version": model.schema_id, **values},
        strict=True,
    )


def test_terminal_projection_is_pure_over_one_historical_prefix(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        prefix = history.snapshot_at_revision(RUN_ID, 1)

    legality = classify_terminal_legality(prefix)
    assert legality.terminal_state == "core_active"
    assert legality.next_effects == ()
    assert classify_terminal_state(prefix).state == "core_active"


def _terminal_reconstruction_fixture(
    tmp_path: Path,
) -> tuple[ControlStoreHistory, object, TransactionReceipt]:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        original_history = store.load_history()
        original = original_history.snapshot_at_revision(RUN_ID, 1)

    timestamp = "2026-07-17T00:00:00Z"
    initialization = original.transactions[0]
    reader_revision = original.artifact_revisions[0]
    reader_reference = {
        "artifact_id": reader_revision.artifact_id,
        "revision": reader_revision.revision,
    }
    render = _record(
        FinalizeRenderRecord,
        render_id="RENDER-TERMINAL-001",
        run_id=RUN_ID,
        audit_proposal_id="PROP-TERMINAL-AUDIT-001",
        audited_brief=reader_reference,
        audit_report=reader_reference,
        reader_artifacts=[reader_reference],
        reader_clean_status="pass",
        policy_result_fingerprint="a" * 64,
        run_contract_fingerprint="b" * 64,
        created_at=timestamp,
        render_event_id="EVT-TERMINAL-RENDER-001",
        accepted_transaction_id=initialization.transaction_id,
        request_fingerprint="c" * 64,
    )
    initialization_with_render = TransactionReceipt.model_validate(
        {
            **initialization.model_dump(mode="json", exclude_unset=False),
            "finalize_renders": [{"render_id": render.render_id}],
        },
        strict=True,
    )
    historical_full = replace(
        original_history.snapshots[0],
        finalize_renders=(render,),
        transactions=(initialization_with_render,),
    )
    history = replace(original_history, snapshots=(historical_full,))
    pre = history.snapshot_at_revision(RUN_ID, 1)

    transaction_id = "REQ-TERMINAL-COMPLETE-001"
    finalization = _record(
        FinalizationRecord,
        finalization_id="FINALIZATION-TERMINAL-001",
        run_id=RUN_ID,
        render_id=render.render_id,
        finalize_transition_id="TRN-TERMINAL-FINALIZE-001",
        finalize_gate_batch_id="BATCH-TERMINAL-FINALIZE-001",
        finalize_gate_evaluation_ids=["EVAL-TERMINAL-FINALIZE-001"],
        recovery_id=None,
        integrity_revision=1,
        finalized_at=timestamp,
        finalization_event_id="EVT-TERMINAL-FINALIZE-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="d" * 64,
    )
    archive_members = [
        next(
            revision
            for revision in pre.artifact_revisions
            if revision.artifact_id == artifact.artifact_id
            and revision.revision == artifact.current_revision
        )
        for artifact in sorted(pre.artifacts, key=lambda item: item.artifact_id)
        if artifact.current_revision > 0
    ]
    archive_payload = {
        "schema_version": "briefloop.core_v2_run_archive.v1",
        "run_id": RUN_ID,
        "finalization_id": finalization.finalization_id,
        "artifacts": [
            {
                "artifact_id": item.artifact_id,
                "revision": item.revision,
                "sha256": item.sha256,
            }
            for item in archive_members
        ],
    }
    archive_bytes = canonical_json_bytes(archive_payload) + b"\n"
    archive_revision = _record(
        ArtifactRevision,
        run_id=RUN_ID,
        artifact_id="core_v2_run_archive",
        revision=1,
        path="output/intermediate/core_v2_run_archive.json",
        sha256=sha256_hex(archive_bytes),
        size_bytes=len(archive_bytes),
        frozen=True,
        producer_kind="control_tool",
        producer_id="core-v2-finalize-complete",
        created_at=timestamp,
    )
    archive = _record(
        RunArchiveRecord,
        archive_id="ARCHIVE-TERMINAL-001",
        run_id=RUN_ID,
        finalization_id=finalization.finalization_id,
        archive_artifact={
            "artifact_id": archive_revision.artifact_id,
            "revision": archive_revision.revision,
        },
        manifest_sha256=archive_revision.sha256,
        included_count=len(archive_members),
        created_at=timestamp,
        archive_event_id="EVT-TERMINAL-ARCHIVE-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="e" * 64,
    )
    package_payload = {
        "schema_version": "briefloop.core_v2_package_manifest.v1",
        "run_id": RUN_ID,
        "finalization_id": finalization.finalization_id,
        "archive": {
            "artifact_id": archive_revision.artifact_id,
            "revision": archive_revision.revision,
            "sha256": archive_revision.sha256,
        },
        "reader_artifacts": [
            {
                "artifact_id": reader_revision.artifact_id,
                "revision": reader_revision.revision,
                "sha256": reader_revision.sha256,
            }
        ],
    }
    package_bytes = canonical_json_bytes(package_payload) + b"\n"
    package_revision = _record(
        ArtifactRevision,
        run_id=RUN_ID,
        artifact_id="core_v2_package_manifest",
        revision=1,
        path="output/intermediate/core_v2_package_manifest.json",
        sha256=sha256_hex(package_bytes),
        size_bytes=len(package_bytes),
        frozen=True,
        producer_kind="control_tool",
        producer_id="core-v2-finalize-complete",
        created_at=timestamp,
    )
    package = _record(
        PackageReadyRecord,
        package_id="PACKAGE-TERMINAL-001",
        run_id=RUN_ID,
        finalization_id=finalization.finalization_id,
        archive_id=archive.archive_id,
        package_manifest_artifact={
            "artifact_id": package_revision.artifact_id,
            "revision": package_revision.revision,
        },
        package_manifest_sha256=package_revision.sha256,
        artifact_count=3,
        created_at=timestamp,
        package_event_id="EVT-TERMINAL-PACKAGE-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="f" * 64,
    )
    archive_bindings = tuple(
        _record(
            RunArchiveArtifactBinding,
            run_id=RUN_ID,
            archive_id=archive.archive_id,
            position=position,
            artifact_id=item.artifact_id,
            artifact_revision=item.revision,
            artifact_sha256=item.sha256,
            usage=archive_artifact_usage(item.artifact_id),
            accepted_transaction_id=transaction_id,
        )
        for position, item in enumerate(archive_members)
    )
    package_members = (
        (reader_revision, "reader"),
        (archive_revision, "archive"),
        (package_revision, "manifest"),
    )
    package_bindings = tuple(
        _record(
            PackageArtifactBinding,
            run_id=RUN_ID,
            package_id=package.package_id,
            position=position,
            artifact_id=item.artifact_id,
            artifact_revision=item.revision,
            artifact_sha256=item.sha256,
            usage=usage,
            accepted_transaction_id=transaction_id,
        )
        for position, (item, usage) in enumerate(package_members)
    )
    receipt = _record(
        TransactionReceipt,
        transaction_id=transaction_id,
        run_id=RUN_ID,
        transaction_type=transaction_type_for("finalize_complete"),
        prior_revision=1,
        committed_revision=2,
        committed_at=timestamp,
        projection_status="current",
        artifact_revisions=[
            {"artifact_id": archive_revision.artifact_id, "revision": 1},
            {"artifact_id": package_revision.artifact_id, "revision": 1},
        ],
        finalizations=[{"finalization_id": finalization.finalization_id}],
        run_archives=[{"archive_id": archive.archive_id}],
        run_archive_artifact_bindings=[
            {"archive_id": archive.archive_id, "position": item.position}
            for item in archive_bindings
        ],
        package_ready_records=[{"package_id": package.package_id}],
        package_artifact_bindings=[
            {"package_id": package.package_id, "position": item.position}
            for item in package_bindings
        ],
    )
    terminal_records = (
        _record(
            ArtifactRecord,
            run_id=RUN_ID,
            artifact_id=archive_revision.artifact_id,
            current_revision=1,
            status="valid",
            required=True,
            path=archive_revision.path,
            format="json",
        ),
        _record(
            ArtifactRecord,
            run_id=RUN_ID,
            artifact_id=package_revision.artifact_id,
            current_revision=1,
            status="valid",
            required=True,
            path=package_revision.path,
            format="json",
        ),
    )
    post = replace(
        pre,
        store_revision=2,
        artifacts=(*pre.artifacts, *terminal_records),
        artifact_revisions=(
            *pre.artifact_revisions,
            archive_revision,
            package_revision,
        ),
        finalizations=(finalization,),
        run_archives=(archive,),
        run_archive_artifact_bindings=archive_bindings,
        package_ready_records=(package,),
        package_artifact_bindings=package_bindings,
        transactions=(initialization_with_render, receipt),
    )
    history = replace(
        history,
        artifact_contents=MappingProxyType(
            {
                **history.artifact_contents,
                (RUN_ID, archive_revision.artifact_id, 1): archive_bytes,
                (RUN_ID, package_revision.artifact_id, 1): package_bytes,
            }
        ),
    )
    return history, post, receipt


def _forge_terminal_membership(post: object, target: str, forgery: str):
    binding_field = (
        "run_archive_artifact_bindings"
        if target == "archive"
        else "package_artifact_bindings"
    )
    bindings = getattr(post, binding_field)
    if forgery == "insertion":
        forged_bindings = (
            *bindings,
            bindings[-1].model_copy(update={"position": len(bindings)}),
        )
    elif forgery == "deletion":
        forged_bindings = bindings[:-1]
    elif forgery == "substitution":
        forged_bindings = (
            bindings[0].model_copy(update={"artifact_id": bindings[1].artifact_id}),
            *bindings[1:],
        )
    elif forgery == "duplicate":
        forged_bindings = (*bindings, bindings[0])
    elif forgery == "reorder":
        forged_bindings = (
            bindings[0].model_copy(update={"position": 1}),
            bindings[1].model_copy(update={"position": 0}),
            *bindings[2:],
        )
    elif forgery == "stale":
        forged_bindings = (
            bindings[0].model_copy(
                update={"artifact_revision": bindings[0].artifact_revision + 1}
            ),
            *bindings[1:],
        )
    elif forgery == "cross_run":
        forged_bindings = (
            bindings[0].model_copy(update={"run_id": "RUN-TERMINAL-OTHER-001"}),
            *bindings[1:],
        )
    elif forgery == "wrong_usage":
        forged_bindings = (
            bindings[0].model_copy(
                update={"usage": "evidence" if target == "archive" else "archive"}
            ),
            *bindings[1:],
        )
    elif forgery == "member_hash":
        forged_bindings = (
            bindings[0].model_copy(update={"artifact_sha256": "0" * 64}),
            *bindings[1:],
        )
    elif forgery == "count":
        if target == "archive":
            record = post.run_archives[0]
            return replace(
                post,
                run_archives=(
                    record.model_copy(
                        update={"included_count": record.included_count + 1}
                    ),
                ),
            )
        record = post.package_ready_records[0]
        return replace(
            post,
            package_ready_records=(
                record.model_copy(update={"artifact_count": record.artifact_count + 1}),
            ),
        )
    elif forgery == "aggregate_hash":
        if target == "archive":
            record = post.run_archives[0]
            return replace(
                post,
                run_archives=(record.model_copy(update={"manifest_sha256": "0" * 64}),),
            )
        record = post.package_ready_records[0]
        return replace(
            post,
            package_ready_records=(
                record.model_copy(update={"package_manifest_sha256": "0" * 64}),
            ),
        )
    else:
        raise AssertionError(f"unknown forgery: {forgery}")
    return replace(post, **{binding_field: forged_bindings})


@pytest.mark.parametrize("target", ("archive", "package"))
@pytest.mark.parametrize(
    "forgery",
    (
        "insertion",
        "deletion",
        "substitution",
        "duplicate",
        "reorder",
        "stale",
        "cross_run",
        "wrong_usage",
        "count",
        "member_hash",
        "aggregate_hash",
    ),
)
def test_archive_and_package_reconstruction_rejects_parameterized_forgeries(
    tmp_path: Path,
    target: str,
    forgery: str,
) -> None:
    history, post, receipt = _terminal_reconstruction_fixture(tmp_path)
    CoreRunDomainVerifier._verify_archive_package_reconstruction(
        history,
        post,
        receipt,
    )

    with pytest.raises(CoreRunError) as error:
        CoreRunDomainVerifier._verify_archive_package_reconstruction(
            history,
            _forge_terminal_membership(post, target, forgery),
            receipt,
        )
    assert error.value.code == f"{target}_membership_invalid"
