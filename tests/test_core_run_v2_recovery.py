from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest
from tests import test_runtime_host_continue_v2 as gate_runtime_fixture

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.contracts.v2 import (
    ArtifactRecord,
    ArtifactRevision,
    ArtifactSupersedeRequest,
    ArtifactSupersessionRecord,
    CoreRunInitializeRequest,
    CoreRunEventBinding,
    EventEnvelope,
    IntegrityCheckRequest,
    InvocationStartRequest,
    OwnedArtifactSubmitRequest,
    OwnedArtifactSubmissionRecord,
    ReceiptCheckoutBinding,
    RecoveryCompletionRecord,
    RepairCompletionRecord,
    RepairCycleRecord,
    RepairStartRequest,
    RunIntegrityRecord,
    RunHeadTransitionRecord,
    RunIdentity,
    RunResetRequest,
    StageState,
    StageTransitionRecord,
    WorkspaceRunHead,
)
from multi_agent_brief.control_store import (
    ControlStoreIntegrityError,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.core_run_v2 import (
    ArtifactAcceptanceService,
    CoreRunRecoveryService,
    CoreRunService,
)
from multi_agent_brief.core_run_v2.checkout import (
    build_checkout_revision,
    prepare_cross_run_checkout_effect,
    stage_checkout_effect,
)
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.integrity import (
    RunIntegrityService,
    read_workspace_file,
)
from multi_agent_brief.core_run_v2.policy import (
    derived_id,
    run_contract_fingerprint,
    transaction_type_for,
)
from multi_agent_brief.core_run_v2.recovery import (
    CoreEffect,
    classify_effect_authorization,
    classify_recovery_legality,
)
from multi_agent_brief.core_run_v2.verifier import (
    CoreRunDomainVerifier,
    _verified_core_receipt_binding,
    resolve_core_replay,
)
from multi_agent_brief.intake_v2.service import IntakeService

RUN_ID = "RUN-RECOVERY-PREFIX-001"
NOW = "2026-07-17T00:00:00Z"
CLOCK = lambda: datetime(2026, 7, 17, tzinfo=timezone.utc)


def _commit_core_fixture(store: SQLiteControlStore, unit):
    snapshot = store.load_snapshot(unit.run_id)
    current = {
        (item.artifact_id, item.revision): item
        for item in snapshot.artifact_revisions
    }
    selected = {
        artifact.artifact_id: current[(artifact.artifact_id, artifact.current_revision)]
        for artifact in snapshot.artifacts
        if artifact.current_revision > 0
        and not current[(artifact.artifact_id, artifact.current_revision)].path.startswith(
            "briefloop.db.blobs/"
        )
    }
    selected.update(
        {
            item.record.artifact_id: item.record
            for item in unit._artifact_revisions
            if not item.record.path.startswith("briefloop.db.blobs/")
        }
    )
    committed = {
        receipt.transaction_id: receipt.committed_revision
        for receipt in snapshot.transactions
    }
    current_checkout_binding = max(
        snapshot.receipt_checkout_bindings,
        key=lambda item: committed[item.transaction_id],
        default=None,
    )
    pre_checkout_revision_id = (
        None
        if current_checkout_binding is None
        else current_checkout_binding.post_checkout_revision_id
    )
    checkout = build_checkout_revision(
        workspace_id=snapshot.workspace_id,
        run_id=unit.run_id,
        transaction_id=unit.transaction_id,
        created_at=CLOCK(),
        artifact_revisions=selected.values(),
        parent_checkout_revision_id=pre_checkout_revision_id,
    )
    unit.put_checkout_revision(checkout.record)
    for member in checkout.members:
        unit.put_checkout_revision_member(member)
    unit.put_receipt_checkout_binding(
        ReceiptCheckoutBinding.model_validate(
            {
                "schema_version": ReceiptCheckoutBinding.schema_id,
                "workspace_id": snapshot.workspace_id,
                "run_id": unit.run_id,
                "transaction_id": unit.transaction_id,
                "pre_run_id": unit.run_id,
                "pre_checkout_revision_id": pre_checkout_revision_id,
                "post_run_id": unit.run_id,
                "post_checkout_revision_id": checkout.record.checkout_revision_id,
            },
            strict=True,
        )
    )
    return unit.commit()


def _initialized_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request = deepcopy(CoreRunInitializeRequest.minimal_example)
    request.update(
        request_id="REQ-RECOVERY-PREFIX-INIT-001",
        workspace_id="WS-RECOVERY-PREFIX-001",
        run_id=RUN_ID,
        input_governance_required=False,
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


def _event(
    *,
    event_id: str,
    transaction_id: str,
    event_type: str,
    reason: str,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    binding: CoreRunEventBinding | None = None,
) -> EventEnvelope:
    return _record(
        EventEnvelope,
        event_id=event_id,
        run_id=RUN_ID,
        event_type=event_type,
        created_at=NOW,
        actor="system",
        transaction_id=transaction_id,
        stage_id=stage_id,
        artifact_id=artifact_id,
        decision="block" if event_type == "run_blocked" else "continue",
        reason=reason,
        metadata={},
        intake_binding=None,
        core_run_binding=binding,
    )


def _binding(
    *,
    transaction_id: str,
    request_fingerprint: str,
    effect_kind: str,
    primary_record_id: str,
    outcome: str = "committed",
) -> CoreRunEventBinding:
    return CoreRunEventBinding.model_validate(
        {
            "request_id": transaction_id,
            "request_fingerprint": request_fingerprint,
            "effect_kind": effect_kind,
            "primary_record_id": primary_record_id,
            "outcome": outcome,
        },
        strict=True,
    )


def _accept_input_classification(store: SQLiteControlStore) -> None:
    snapshot = store.load_snapshot(RUN_ID)
    binding = snapshot.run_contract_bindings[0]
    artifact = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "input_classification"
    )
    transaction_id = "REQ-RECOVERY-INPUT-001"
    event_id = "EVT-RECOVERY-INPUT-001"
    submission_id = "SUBMISSION-RECOVERY-INPUT-001"
    content = b'{"classification":"canonical"}\n'
    digest = sha256_hex(content)
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "owned_artifact_acceptance",
            "artifact_id": artifact.artifact_id,
            "sha256": digest,
        }
    )
    updated = _record(
        ArtifactRecord,
        **{
            **artifact.model_dump(mode="json", exclude_unset=False),
            "current_revision": 1,
            "status": "valid",
        },
    )
    revision = _record(
        ArtifactRevision,
        run_id=RUN_ID,
        artifact_id=artifact.artifact_id,
        revision=1,
        path=artifact.path,
        sha256=digest,
        size_bytes=len(content),
        frozen=True,
        producer_kind="control_tool",
        producer_id="python_tool",
        created_at=NOW,
    )
    submission = _record(
        OwnedArtifactSubmissionRecord,
        submission_id=submission_id,
        run_id=RUN_ID,
        artifact_id=artifact.artifact_id,
        artifact_revision=1,
        artifact_sha256=digest,
        owner_stage_id="input-governance",
        owner_role_id="python_tool",
        run_contract_fingerprint=binding.contract_fingerprint,
        invocation_id=None,
        producer_tool_id="input-governance-v2",
        parent_artifact=None,
        source_proposal_id=None,
        canonical_workspace_path=artifact.path,
        request_fingerprint=request_fingerprint,
        accepted_event_id=event_id,
        accepted_transaction_id=transaction_id,
        created_at=NOW,
    )
    unit = store.begin(
        RUN_ID,
        transaction_id,
        transaction_type_for("owned_artifact_acceptance"),
        snapshot.store_revision,
    )
    unit.put_artifact(updated)
    unit.put_artifact_revision(revision, content)
    unit.put_owned_artifact_submission(submission)
    unit.append_event(
        _event(
            event_id=event_id,
            transaction_id=transaction_id,
            event_type="owned_artifact_accepted",
            stage_id="input-governance",
            artifact_id=artifact.artifact_id,
            reason="input classification accepted",
            binding=_binding(
                transaction_id=transaction_id,
                request_fingerprint=request_fingerprint,
                effect_kind="owned_artifact_acceptance",
                primary_record_id=submission_id,
            ),
        )
    )
    _commit_core_fixture(store, unit)


def _record_contamination(store: SQLiteControlStore) -> str:
    snapshot = store.load_snapshot(RUN_ID)
    artifact = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "input_classification"
    )
    revision = next(
        item
        for item in snapshot.artifact_revisions
        if item.artifact_id == artifact.artifact_id
        and item.revision == artifact.current_revision
    )
    transaction_id = "REQ-RECOVERY-CONTAMINATION-001"
    base_fingerprint = canonical_fingerprint(
        {"effect_kind": "integrity_contamination", "request_id": transaction_id}
    )
    observed_digest = sha256_hex(b"external mutation")
    record = _record(
        RunIntegrityRecord,
        run_id=RUN_ID,
        integrity_revision=2,
        status="contaminated",
        prior_integrity_revision=1,
        affected_artifact_id=artifact.artifact_id,
        affected_artifact_revision=revision.revision,
        expected_workspace_path=revision.path,
        expected_sha256=revision.sha256,
        observed_entry_kind="regular_file",
        observed_sha256=observed_digest,
        reason_code="frozen_artifact_contaminated",
        first_detected_at=NOW,
        first_detected_event_id="EVT-RECOVERY-CONTAMINATION-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint=base_fingerprint,
    )
    observation_fingerprint = canonical_fingerprint(
        {
            "run_id": record.run_id,
            "artifact_id": record.affected_artifact_id,
            "artifact_revision": record.affected_artifact_revision,
            "expected_workspace_path": record.expected_workspace_path,
            "expected_sha256": record.expected_sha256,
            "observed_entry_kind": record.observed_entry_kind,
            "observed_sha256": record.observed_sha256,
        }
    )
    binding_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "integrity_contamination",
            "base_request_fingerprint": base_fingerprint,
            "observation_fingerprint": observation_fingerprint,
        }
    )
    unit = store.begin(
        RUN_ID,
        transaction_id,
        transaction_type_for("integrity_contamination"),
        snapshot.store_revision,
    )
    unit.append_run_integrity_record(record)
    unit.append_event(
        _event(
            event_id=record.first_detected_event_id,
            transaction_id=transaction_id,
            event_type="run_integrity_contaminated",
            artifact_id=artifact.artifact_id,
            reason="frozen artifact differs from its accepted revision",
            binding=_binding(
                transaction_id=transaction_id,
                request_fingerprint=binding_fingerprint,
                effect_kind="integrity_contamination",
                primary_record_id=str(record.integrity_revision),
                outcome="blocked",
            ),
        )
    )
    unit.append_event(
        _event(
            event_id=derived_id(
                "EVT-BLOCK",
                transaction_id,
                observation_fingerprint,
            ),
            transaction_id=transaction_id,
            event_type="run_blocked",
            artifact_id=artifact.artifact_id,
            reason="run blocked by durable contamination",
        )
    )
    _commit_core_fixture(store, unit)
    return base_fingerprint


def _start_repair(store: SQLiteControlStore) -> tuple[str, str]:
    snapshot = store.load_snapshot(RUN_ID)
    transaction_id = "REQ-RECOVERY-REPAIR-START-001"
    repair_id = "REPAIR-RECOVERY-001"
    event_id = "EVT-RECOVERY-REPAIR-START-001"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "repair_start",
            "repair_id": repair_id,
            "contamination_revision": 2,
        }
    )
    repair = _record(
        RepairCycleRecord,
        repair_id=repair_id,
        run_id=RUN_ID,
        contamination_revision=2,
        owner_stage_id="input-governance",
        permitted_artifact_ids=["input_classification"],
        reason_code="frozen_artifact_contaminated",
        started_at=NOW,
        start_event_id=event_id,
        accepted_transaction_id=transaction_id,
        request_fingerprint=request_fingerprint,
    )
    unit = store.begin(
        RUN_ID,
        transaction_id,
        transaction_type_for("repair_start"),
        snapshot.store_revision,
    )
    unit.put_repair_cycle(repair)
    unit.append_event(
        _event(
            event_id=event_id,
            transaction_id=transaction_id,
            event_type="repair_started",
            stage_id="input-governance",
            reason="repair cycle started",
            binding=_binding(
                transaction_id=transaction_id,
                request_fingerprint=request_fingerprint,
                effect_kind="repair_start",
                primary_record_id=repair_id,
            ),
        )
    )
    _commit_core_fixture(store, unit)
    return transaction_id, request_fingerprint


def _supersede_input_classification(
    store: SQLiteControlStore,
) -> tuple[str, str]:
    snapshot = store.load_snapshot(RUN_ID)
    binding = snapshot.run_contract_bindings[0]
    artifact = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "input_classification"
    )
    prior = next(
        item
        for item in snapshot.artifact_revisions
        if item.artifact_id == artifact.artifact_id
        and item.revision == artifact.current_revision
    )
    transaction_id = "REQ-RECOVERY-SUPERSEDE-001"
    supersession_id = "SUPERSESSION-RECOVERY-001"
    accepted_event_id = "EVT-RECOVERY-SUPERSEDE-001"
    owned_event_id = "EVT-RECOVERY-SUPERSEDE-OWNED-001"
    submission_id = "SUBMISSION-RECOVERY-INPUT-002"
    content = b'{"classification":"repaired"}\n'
    digest = sha256_hex(content)
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "artifact_supersession",
            "supersession_id": supersession_id,
            "prior_sha256": prior.sha256,
            "successor_sha256": digest,
        }
    )
    updated = _record(
        ArtifactRecord,
        **{
            **artifact.model_dump(mode="json", exclude_unset=False),
            "current_revision": prior.revision + 1,
            "status": "valid",
        },
    )
    revision = _record(
        ArtifactRevision,
        run_id=RUN_ID,
        artifact_id=artifact.artifact_id,
        revision=prior.revision + 1,
        path=artifact.path,
        sha256=digest,
        size_bytes=len(content),
        frozen=True,
        producer_kind="control_tool",
        producer_id="python_tool",
        created_at=NOW,
    )
    submission = _record(
        OwnedArtifactSubmissionRecord,
        submission_id=submission_id,
        run_id=RUN_ID,
        artifact_id=artifact.artifact_id,
        artifact_revision=revision.revision,
        artifact_sha256=digest,
        owner_stage_id="input-governance",
        owner_role_id="python_tool",
        run_contract_fingerprint=binding.contract_fingerprint,
        invocation_id=None,
        producer_tool_id="repair-control-v2",
        parent_artifact={
            "artifact_id": prior.artifact_id,
            "revision": prior.revision,
        },
        source_proposal_id=None,
        canonical_workspace_path=artifact.path,
        request_fingerprint=request_fingerprint,
        accepted_event_id=owned_event_id,
        accepted_transaction_id=transaction_id,
        created_at=NOW,
    )
    supersession = _record(
        ArtifactSupersessionRecord,
        supersession_id=supersession_id,
        run_id=RUN_ID,
        repair_id="REPAIR-RECOVERY-001",
        mode="repair",
        prior_artifact={
            "artifact_id": prior.artifact_id,
            "revision": prior.revision,
        },
        successor_artifact={
            "artifact_id": revision.artifact_id,
            "revision": revision.revision,
        },
        reason_code="frozen_artifact_repaired",
        created_at=NOW,
        accepted_event_id=accepted_event_id,
        accepted_transaction_id=transaction_id,
        request_fingerprint=request_fingerprint,
    )
    unit = store.begin(
        RUN_ID,
        transaction_id,
        transaction_type_for("artifact_supersession"),
        snapshot.store_revision,
    )
    unit.put_artifact(updated)
    unit.put_artifact_revision(revision, content)
    unit.put_owned_artifact_submission(submission)
    unit.put_artifact_supersession(supersession)
    unit.append_event(
        _event(
            event_id=owned_event_id,
            transaction_id=transaction_id,
            event_type="owned_artifact_accepted",
            stage_id="input-governance",
            artifact_id=artifact.artifact_id,
            reason="repaired artifact accepted",
        )
    )
    unit.append_event(
        _event(
            event_id=accepted_event_id,
            transaction_id=transaction_id,
            event_type="repair_stage_superseded",
            stage_id="input-governance",
            artifact_id=artifact.artifact_id,
            reason="artifact revision superseded in active repair",
            binding=_binding(
                transaction_id=transaction_id,
                request_fingerprint=request_fingerprint,
                effect_kind="artifact_supersession",
                primary_record_id=supersession_id,
            ),
        )
    )
    _commit_core_fixture(store, unit)
    return transaction_id, request_fingerprint


def _complete_repair(store: SQLiteControlStore) -> tuple[str, str]:
    snapshot = store.load_snapshot(RUN_ID)
    contract = snapshot.run_contract_bindings[0]
    prior = next(
        item for item in snapshot.stage_states if item.stage_id == "input-governance"
    )
    transaction_id = "REQ-RECOVERY-REPAIR-COMPLETE-001"
    completion_id = "REPAIR-COMPLETION-RECOVERY-001"
    transition_id = "TRANSITION-RECOVERY-REOPEN-001"
    transition_event_id = "EVT-RECOVERY-REOPEN-001"
    completion_event_id = "EVT-RECOVERY-REPAIR-COMPLETE-001"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "repair_complete",
            "repair_id": "REPAIR-RECOVERY-001",
            "supersession_ids": ["SUPERSESSION-RECOVERY-001"],
            "reopened_stage_ids": ["input-governance"],
        }
    )
    transition = _record(
        StageTransitionRecord,
        transition_id=transition_id,
        run_id=RUN_ID,
        stage_id=prior.stage_id,
        transition_kind="repair_reopen",
        requested_decision=None,
        prior_status=prior.status,
        prior_revision=prior.revision,
        result_status="ready",
        result_revision=prior.revision + 1,
        reason="repair reopens the artifact owner stage",
        run_contract_fingerprint=contract.contract_fingerprint,
        actor="system",
        producer_invocation_id=None,
        producer_tool_id=None,
        producer_result_status=None,
        producer_result_fingerprint=None,
        producer_implementation=None,
        producer_version=None,
        topology=None,
        satisfaction_source_kind=None,
        satisfied_by_id=None,
        created_at=NOW,
        transition_event_id=transition_event_id,
        accepted_transaction_id=transaction_id,
        request_fingerprint=request_fingerprint,
    )
    completion = _record(
        RepairCompletionRecord,
        repair_completion_id=completion_id,
        run_id=RUN_ID,
        repair_id="REPAIR-RECOVERY-001",
        contamination_revision=2,
        supersession_ids=["SUPERSESSION-RECOVERY-001"],
        reopened_transition_ids=[transition_id],
        completed_at=NOW,
        completion_event_id=completion_event_id,
        accepted_transaction_id=transaction_id,
        request_fingerprint=request_fingerprint,
    )
    unit = store.begin(
        RUN_ID,
        transaction_id,
        transaction_type_for("repair_complete"),
        snapshot.store_revision,
    )
    unit.put_stage_state(
        _record(
            StageState,
            run_id=RUN_ID,
            stage_id=prior.stage_id,
            status=transition.result_status,
            revision=transition.result_revision,
            updated_at=NOW,
        )
    )
    unit.append_stage_transition(transition)
    unit.put_repair_completion(completion)
    unit.append_event(
        _event(
            event_id=transition_event_id,
            transaction_id=transaction_id,
            event_type="stage_status_changed",
            stage_id=prior.stage_id,
            reason="repair reopened artifact owner stage",
        )
    )
    unit.append_event(
        _event(
            event_id=completion_event_id,
            transaction_id=transaction_id,
            event_type="repair_completed",
            stage_id=prior.stage_id,
            reason="repair transaction completed",
            binding=_binding(
                transaction_id=transaction_id,
                request_fingerprint=request_fingerprint,
                effect_kind="repair_complete",
                primary_record_id=completion_id,
            ),
        )
    )
    _commit_core_fixture(store, unit)
    return transaction_id, request_fingerprint


def _complete_reopened_stage(store: SQLiteControlStore) -> str:
    snapshot = store.load_snapshot(RUN_ID)
    contract = snapshot.run_contract_bindings[0]
    prior = next(
        item for item in snapshot.stage_states if item.stage_id == "input-governance"
    )
    transaction_id = "REQ-RECOVERY-RERUN-COMPLETE-001"
    transition_id = "TRANSITION-RECOVERY-RERUN-001"
    event_id = "EVT-RECOVERY-RERUN-001"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "stage_transition",
            "transition_id": transition_id,
            "prior_revision": prior.revision,
        }
    )
    transition = _record(
        StageTransitionRecord,
        transition_id=transition_id,
        run_id=RUN_ID,
        stage_id=prior.stage_id,
        transition_kind="complete",
        requested_decision="continue",
        prior_status=prior.status,
        prior_revision=prior.revision,
        result_status="complete",
        result_revision=prior.revision + 1,
        reason="reopened owner stage rerun completed",
        run_contract_fingerprint=contract.contract_fingerprint,
        actor="system",
        producer_invocation_id=None,
        producer_tool_id=None,
        producer_result_status=None,
        producer_result_fingerprint=None,
        producer_implementation=None,
        producer_version=None,
        topology=None,
        satisfaction_source_kind=None,
        satisfied_by_id=None,
        created_at=NOW,
        transition_event_id=event_id,
        accepted_transaction_id=transaction_id,
        request_fingerprint=request_fingerprint,
    )
    unit = store.begin(
        RUN_ID,
        transaction_id,
        transaction_type_for("stage_transition"),
        snapshot.store_revision,
    )
    unit.put_stage_state(
        _record(
            StageState,
            run_id=RUN_ID,
            stage_id=prior.stage_id,
            status=transition.result_status,
            revision=transition.result_revision,
            updated_at=NOW,
        )
    )
    unit.append_stage_transition(transition)
    unit.append_event(
        _event(
            event_id=event_id,
            transaction_id=transaction_id,
            event_type="stage_status_changed",
            stage_id=prior.stage_id,
            reason="reopened owner stage rerun completed",
            binding=_binding(
                transaction_id=transaction_id,
                request_fingerprint=request_fingerprint,
                effect_kind="stage_transition",
                primary_record_id=transition_id,
            ),
        )
    )
    _commit_core_fixture(store, unit)
    return transition_id


def _complete_recovery(store: SQLiteControlStore) -> tuple[str, str]:
    snapshot = store.load_snapshot(RUN_ID)
    legality = classify_recovery_legality(snapshot)
    assert legality.state == "rerun_required"
    transaction_id = "REQ-RECOVERY-COMPLETE-001"
    recovery_id = "RECOVERY-COMPLETION-001"
    event_id = "EVT-RECOVERY-COMPLETE-001"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "recovery_complete",
            "repair_completion_id": "REPAIR-COMPLETION-RECOVERY-001",
            "rerun_transition_ids": list(legality.required_rerun_transition_ids),
        }
    )
    recovery = _record(
        RecoveryCompletionRecord,
        recovery_id=recovery_id,
        run_id=RUN_ID,
        repair_completion_id="REPAIR-COMPLETION-RECOVERY-001",
        contamination_revision=2,
        supersession_ids=["SUPERSESSION-RECOVERY-001"],
        rerun_transition_ids=list(legality.required_rerun_transition_ids),
        gate_evaluation_ids=list(legality.required_gate_evaluation_ids),
        disposition="recovered_non_reference",
        completed_at=NOW,
        completion_event_id=event_id,
        accepted_transaction_id=transaction_id,
        request_fingerprint=request_fingerprint,
    )
    unit = store.begin(
        RUN_ID,
        transaction_id,
        transaction_type_for("recovery_complete"),
        snapshot.store_revision,
    )
    unit.put_recovery_completion(recovery)
    unit.append_run_integrity_record(
        _record(
            RunIntegrityRecord,
            run_id=RUN_ID,
            integrity_revision=snapshot.run_integrity_records[-1].integrity_revision
            + 1,
            status="clean",
            prior_integrity_revision=snapshot.run_integrity_records[
                -1
            ].integrity_revision,
            affected_artifact_id=None,
            affected_artifact_revision=None,
            expected_workspace_path=None,
            expected_sha256=None,
            observed_entry_kind=None,
            observed_sha256=None,
            reason_code=None,
            first_detected_at=None,
            first_detected_event_id=None,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
    )
    unit.append_event(
        _event(
            event_id=event_id,
            transaction_id=transaction_id,
            event_type="decision_recorded",
            stage_id="input-governance",
            reason="recovery completed from exact repair rerun relations",
            binding=_binding(
                transaction_id=transaction_id,
                request_fingerprint=request_fingerprint,
                effect_kind="recovery_complete",
                primary_record_id=recovery_id,
            ),
        )
    )
    _commit_core_fixture(store, unit)
    return transaction_id, request_fingerprint


def _reset_run(
    store: SQLiteControlStore,
    *,
    predecessor_run_id: str,
    successor_run_id: str,
    sequence: int,
) -> tuple[str, str]:
    predecessor = store.load_snapshot(predecessor_run_id)
    prior_head = predecessor.workspace_run_head
    assert prior_head is not None
    assert prior_head.current_run_id == predecessor_run_id
    prior_revision = predecessor.store_revision
    committed_revision = prior_revision + 1
    transaction_id = f"REQ-RECOVERY-RESET-{sequence:03d}"
    transition_id = f"HEAD-RECOVERY-RESET-{sequence:03d}"
    reset_event_id = f"EVT-RECOVERY-RESET-{sequence:03d}"
    initialized_event_id = f"EVT-RECOVERY-INITIALIZED-{sequence:03d}"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "run_head_transition",
            "workspace_id": predecessor.workspace_id,
            "predecessor_run_id": predecessor_run_id,
            "successor_run_id": successor_run_id,
            "prior_workspace_revision": prior_revision,
        }
    )
    predecessor_contract = predecessor.run_contract_bindings[0]
    adapter_ref = predecessor_contract.runtime_adapter_artifact
    source_ref = predecessor_contract.runtime_source_plan_artifact
    adapter_payload = json.loads(
        store.read_artifact_revision_bytes(
            predecessor_run_id, adapter_ref.artifact_id, adapter_ref.revision
        )
    )
    adapter_payload["run_id"] = successor_run_id
    adapter_payload.pop("binding_fingerprint", None)
    adapter_payload["binding_fingerprint"] = canonical_fingerprint(adapter_payload)
    adapter_bytes = canonical_json_bytes(adapter_payload)
    source_payload = json.loads(
        store.read_artifact_revision_bytes(
            predecessor_run_id, source_ref.artifact_id, source_ref.revision
        )
    )
    source_payload["run_id"] = successor_run_id
    source_payload.pop("source_plan_fingerprint", None)
    source_payload["source_plan_fingerprint"] = canonical_fingerprint(source_payload)
    source_bytes = canonical_json_bytes(source_payload)
    contract = predecessor_contract.model_copy(
        update={
            "run_id": successor_run_id,
            "runtime_adapter_sha256": sha256_hex(adapter_bytes),
            "runtime_adapter_fingerprint": adapter_payload["binding_fingerprint"],
            "runtime_source_plan_sha256": sha256_hex(source_bytes),
            "runtime_source_plan_fingerprint": source_payload["source_plan_fingerprint"],
            "created_at": NOW,
            "initialization_event_id": initialized_event_id,
            "accepted_transaction_id": transaction_id,
            "request_fingerprint": request_fingerprint,
        }
    )
    contract = contract.model_copy(
        update={
            "contract_fingerprint": run_contract_fingerprint(
                runtime=contract.runtime,
                stage_specs_schema=contract.stage_specs_schema,
                stage_specs_sha256=contract.stage_specs_sha256,
                artifact_contracts_schema=contract.artifact_contracts_schema,
                artifact_contracts_sha256=contract.artifact_contracts_sha256,
                policy_pack_schema=contract.policy_pack_schema,
                policy_pack_name=contract.policy_pack_name,
                policy_pack_sha256=contract.policy_pack_sha256,
                runtime_adapter_sha256=contract.runtime_adapter_sha256,
                runtime_adapter_fingerprint=contract.runtime_adapter_fingerprint,
                runtime_source_plan_sha256=contract.runtime_source_plan_sha256,
                runtime_source_plan_fingerprint=contract.runtime_source_plan_fingerprint,
                run_direction=contract.run_direction.model_dump(mode="json"),
                workspace_config_sha256=contract.workspace_config_sha256,
                sources_config_sha256=contract.sources_config_sha256,
                role_topology=contract.role_topology,
                gate_strictness=contract.gate_strictness,
                input_governance_required=contract.input_governance_required,
            )
        }
    )
    head_transition = _record(
        RunHeadTransitionRecord,
        head_transition_id=transition_id,
        workspace_id=predecessor.workspace_id,
        predecessor_run_id=predecessor_run_id,
        successor_run_id=successor_run_id,
        prior_workspace_revision=prior_revision,
        successor_workspace_revision=committed_revision,
        reason_code="run_reset",
        successor_disposition="non_reference",
        created_at=NOW,
        transition_event_id=reset_event_id,
        accepted_transaction_id=transaction_id,
        request_fingerprint=request_fingerprint,
    )
    unit = store.begin(
        successor_run_id,
        transaction_id,
        transaction_type_for("run_head_transition"),
        prior_revision,
    )
    unit.put_run(
        _record(
            RunIdentity,
            run_id=successor_run_id,
            workspace_id=predecessor.workspace_id,
            runtime=predecessor.run.runtime,
            created_at=NOW,
        )
    )
    unit.put_workspace_run_head(
        _record(
            WorkspaceRunHead,
            workspace_id=predecessor.workspace_id,
            current_run_id=successor_run_id,
            updated_at=NOW,
        )
    )
    unit.put_run_contract_binding(contract)

    revisions = {
        (item.artifact_id, item.revision): item
        for item in predecessor.artifact_revisions
    }
    for artifact in predecessor.artifacts:
        unit.put_artifact(artifact.model_copy(update={"run_id": successor_run_id}))
        for revision_number in range(1, artifact.current_revision + 1):
            revision = revisions[(artifact.artifact_id, revision_number)]
            content = store.read_artifact_revision_bytes(
                predecessor_run_id,
                artifact.artifact_id,
                revision_number,
            )
            if (artifact.artifact_id, revision_number) == (
                adapter_ref.artifact_id,
                adapter_ref.revision,
            ):
                content = adapter_bytes
            elif (artifact.artifact_id, revision_number) == (
                source_ref.artifact_id,
                source_ref.revision,
            ):
                content = source_bytes
            unit.put_artifact_revision(
                revision.model_copy(
                    update={
                        "run_id": successor_run_id,
                        "sha256": sha256_hex(content),
                        "size_bytes": len(content),
                    }
                ),
                content,
            )

    for stage in predecessor.stage_states:
        transition_event_id = (
            f"EVT-RECOVERY-RESET-{sequence:03d}-STAGE-{stage.stage_id}"
        )
        stage_transition_id = (
            f"TRANSITION-RECOVERY-RESET-{sequence:03d}-{stage.stage_id}"
        )
        unit.put_stage_state(
            _record(
                StageState,
                run_id=successor_run_id,
                stage_id=stage.stage_id,
                status=stage.status,
                revision=0,
                updated_at=NOW,
            )
        )
        unit.append_stage_transition(
            _record(
                StageTransitionRecord,
                transition_id=stage_transition_id,
                run_id=successor_run_id,
                stage_id=stage.stage_id,
                transition_kind="initialize",
                requested_decision=None,
                prior_status=None,
                prior_revision=None,
                result_status=stage.status,
                result_revision=0,
                reason="reset successor stage initialized",
                run_contract_fingerprint=contract.contract_fingerprint,
                actor="system",
                producer_invocation_id=None,
                producer_tool_id=None,
                producer_result_status=None,
                producer_result_fingerprint=None,
                producer_implementation=None,
                producer_version=None,
                topology=None,
                satisfaction_source_kind=None,
                satisfied_by_id=None,
                created_at=NOW,
                transition_event_id=transition_event_id,
                accepted_transaction_id=transaction_id,
                request_fingerprint=request_fingerprint,
            )
        )
        unit.append_event(
            _event(
                event_id=transition_event_id,
                transaction_id=transaction_id,
                event_type="stage_status_changed",
                stage_id=stage.stage_id,
                reason="reset successor stage initialized",
            ).model_copy(update={"run_id": successor_run_id})
        )

    unit.append_run_integrity_record(
        _record(
            RunIntegrityRecord,
            run_id=successor_run_id,
            integrity_revision=1,
            status="clean",
            prior_integrity_revision=None,
            affected_artifact_id=None,
            affected_artifact_revision=None,
            expected_workspace_path=None,
            expected_sha256=None,
            observed_entry_kind=None,
            observed_sha256=None,
            reason_code=None,
            first_detected_at=None,
            first_detected_event_id=None,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
    )
    unit.put_run_head_transition(head_transition)
    unit.append_event(
        _event(
            event_id=initialized_event_id,
            transaction_id=transaction_id,
            event_type="run_initialized",
            stage_id="doctor",
            reason="reset successor run initialized",
        ).model_copy(update={"run_id": successor_run_id})
    )
    unit.append_event(
        _event(
            event_id=reset_event_id,
            transaction_id=transaction_id,
            event_type="run_reset",
            reason="workspace head advanced to reset successor",
            binding=_binding(
                transaction_id=transaction_id,
                request_fingerprint=request_fingerprint,
                effect_kind="run_head_transition",
                primary_record_id=transition_id,
            ),
        ).model_copy(update={"run_id": successor_run_id})
    )
    stage_checkout_effect(
        unit,
        prepare_cross_run_checkout_effect(
            workspace=store.path.parent,
            snapshot=predecessor,
            successor_run_id=successor_run_id,
            transaction_id=transaction_id,
            created_at=CLOCK(),
        ),
    )
    unit.commit()
    return transaction_id, request_fingerprint


def test_old_run_intake_after_reset_fails_closed_without_store_write(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    successor_run_id = "RUN-RECOVERY-PREFIX-002"
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        _reset_run(
            store,
            predecessor_run_id=RUN_ID,
            successor_run_id=successor_run_id,
            sequence=1,
        )
        before_revision = store.current_revision
        before = store.load_snapshot(RUN_ID)

    invocation_id = "INV-RECOVERY-OLD-RUN-SOURCE-001"
    scratch = workspace / "scratch" / invocation_id
    scratch.mkdir(parents=True)
    content = b"Synthetic old-run source bytes.\n"
    content_path = scratch / "source_content.txt"
    content_path.write_bytes(content)
    proposal_path = scratch / "source_proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": "briefloop.source_proposal.v2",
                "proposal_id": "PROP-RECOVERY-OLD-RUN-001",
                "run_id": RUN_ID,
                "source_id": "SRC-RECOVERY-OLD-RUN-001",
                "origin_type": "uploaded_file",
                "acquisition_method": "manual_upload",
                "material_kind": "uploaded_file",
                "locator": {
                    "kind": "file",
                    "path": f"scratch/{invocation_id}/source_content.txt",
                },
                "title": "Synthetic old-run source",
                "retrieved_at": NOW,
                "source_category": "regulator",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "filing",
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "content_media_type": "text/plain",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    request_path = scratch / "submit_request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": "briefloop.source_commit_request.v2",
                "request_id": "REQ-RECOVERY-OLD-RUN-SOURCE-001",
                "run_id": RUN_ID,
                "invocation_id": invocation_id,
                "proposal_path": f"scratch/{invocation_id}/source_proposal.json",
                "content_path": f"scratch/{invocation_id}/source_content.txt",
                "raw_payload_path": None,
                "expected_store_revision": before_revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    result = IntakeService(workspace, clock=CLOCK).submit_source(
        request_path.relative_to(workspace).as_posix()
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "control_store_integrity_invalid",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        after = store.load_snapshot(RUN_ID)
        assert store.current_revision == before_revision == 2
        assert after.sources == before.sources
        assert after.accepted_proposals == before.accepted_proposals
        assert after.artifacts == before.artifacts
        assert after.artifact_revisions == before.artifact_revisions
        assert store.load_workspace_run_head().current_run_id == successor_run_id
        history = store.load_history()
        CoreRunDomainVerifier().verify_history(history)
        successor = CoreRunDomainVerifier().verify(store, successor_run_id).snapshot
        assert [
            (
                item.predecessor_run_id,
                item.successor_run_id,
                item.prior_workspace_revision,
                item.successor_workspace_revision,
            )
            for item in successor.run_head_transitions
        ] == [(RUN_ID, successor_run_id, 1, 2)]


def test_blocked_recovery_denies_terminal_spine_effects_without_writes(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        _accept_input_classification(store)
        _record_contamination(store)
        blocked = store.load_snapshot(RUN_ID)
        revision = store.current_revision
        assert classify_recovery_legality(blocked).state == "blocked"
        for effect in (
            CoreEffect.FINALIZE_RENDER,
            CoreEffect.FINALIZE_GATE,
            CoreEffect.FINALIZE_COMPLETE,
        ):
            assert classify_effect_authorization(blocked, effect).decision == "deny"
        assert store.current_revision == revision


def test_later_recovered_epoch_cannot_mask_earliest_unresolved_contamination(
    tmp_path: Path,
) -> None:
    """Every epoch must recover in order; a later repair cannot jump ahead."""

    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        _accept_input_classification(store)
        _record_contamination(store)
        _start_repair(store)
        _supersede_input_classification(store)
        _complete_repair(store)
        _complete_reopened_stage(store)
        recovery_request = _complete_recovery(store)
        snapshot = store.load_snapshot(RUN_ID)
        revision = store.current_revision

    first_contamination = next(
        item for item in snapshot.run_integrity_records if item.status == "contaminated"
    )
    later_contamination = first_contamination.model_copy(
        update={
            "integrity_revision": 3,
            "prior_integrity_revision": 2,
            "accepted_transaction_id": recovery_request[0],
        }
    )
    later_repair = snapshot.repair_cycles[0].model_copy(
        update={"contamination_revision": 3}
    )
    later_completion = snapshot.repair_completions[0].model_copy(
        update={"contamination_revision": 3}
    )
    later_recovery = snapshot.recovery_completions[0].model_copy(
        update={"contamination_revision": 3}
    )
    jumped = replace(
        snapshot,
        run_integrity_records=(
            *snapshot.run_integrity_records,
            later_contamination,
        ),
        repair_cycles=(later_repair,),
        repair_completions=(later_completion,),
        recovery_completions=(later_recovery,),
    )
    assert classify_recovery_legality(jumped).state == "invalid"
    authorization = classify_effect_authorization(
        jumped,
        CoreEffect.FINALIZE_RENDER,
    )
    assert authorization.decision == "invalid"
    assert authorization.reason_code == "recovery_state_invalid"

    no_jump = replace(
        jumped,
        repair_cycles=(),
        artifact_supersessions=(),
        repair_completions=(),
        recovery_completions=(),
    )
    earliest = classify_recovery_legality(no_jump)
    assert earliest.state == "blocked"
    assert earliest.latest_contamination_revision == 2
    assert revision == snapshot.store_revision


def test_typed_recovery_service_starts_one_repair_after_exact_replay_probe(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        _accept_input_classification(store)
        _record_contamination(store)
        before = store.current_revision
    request = _record(
        RepairStartRequest,
        request_id="REQ-TYPED-REPAIR-START-001",
        run_id=RUN_ID,
        contamination_revision=2,
        owner_stage_id="input-governance",
        permitted_artifact_ids=["input_classification"],
        reason_code="frozen_artifact_contaminated",
        expected_store_revision=before,
    )
    service = CoreRunRecoveryService(workspace, clock=CLOCK)
    first = service.start_repair(request)
    assert first.status == "committed", first.to_dict()
    replay = service.start_repair(request)
    assert replay.status == "replayed"
    assert replay.receipt == first.receipt
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        assert store.current_revision == before + 1
        snapshot = store.load_snapshot(RUN_ID)
        assert classify_recovery_legality(snapshot).state == "active_repair"
        assert len(snapshot.receipt_checkout_bindings) >= 2


def test_gate_repair_contamination_rejects_direct_legacy_repair_zero_write(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        return
    workspace = gate_runtime_fixture._authorized_workspace(tmp_path)
    runtime = gate_runtime_fixture._service(workspace)

    for _ in range(10):
        result = runtime.continue_authorized()
        assert result.status == "role_work_required"
        assert result.trace.envelope_path is not None
        envelope = json.loads(
            (workspace / result.trace.envelope_path).read_text(encoding="utf-8")
        )
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            snapshot = store.load_snapshot(envelope["run_id"])
        gate_runtime_fixture._write_current_role_proposal(
            workspace,
            result,
            initial_editor_repetitions=20,
            repair_editor_repetitions=210,
        )
        if envelope["role_id"] == "editor" and snapshot.gate_repair_cycles:
            break
    else:
        raise AssertionError("authorized Gate repair was not reserved")

    audited = next(
        item for item in snapshot.artifacts if item.artifact_id == "audited_brief"
    )
    audited_revision = next(
        item
        for item in snapshot.artifact_revisions
        if item.artifact_id == audited.artifact_id
        and item.revision == audited.current_revision
    )
    (workspace / audited_revision.path).write_text(
        "tampered while Gate repair is active\n",
        encoding="utf-8",
    )
    contamination = RunIntegrityService(workspace).inspect(
        IntegrityCheckRequest.model_validate(
            {
                **IntegrityCheckRequest.minimal_example,
                "request_id": "REQ-GATE-RECOVERY-CONTAMINATION-001",
                "run_id": envelope["run_id"],
                "expected_store_revision": snapshot.store_revision,
            },
            strict=True,
        )
    )
    assert contamination["status"] == "blocked"

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        contaminated = store.load_snapshot(envelope["run_id"])
        before = store.current_revision
    integrity_record = contaminated.run_integrity_records[-1]
    request = _record(
        RepairStartRequest,
        request_id="REQ-GATE-LEGACY-REPAIR-START-001",
        run_id=envelope["run_id"],
        contamination_revision=integrity_record.integrity_revision,
        owner_stage_id="editor",
        permitted_artifact_ids=["audited_brief"],
        reason_code="frozen_artifact_contaminated",
        expected_store_revision=before,
    )
    service = CoreRunRecoveryService(workspace, clock=CLOCK)

    first = service.start_repair(request)
    second = service.start_repair(request)

    assert first.error_code == "repair_scope_invalid"
    assert second == first
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after = store.load_snapshot(envelope["run_id"])
        assert store.current_revision == before
    assert not after.repair_cycles
    assert not after.artifact_supersessions
    assert not after.repair_completions
    assert not after.recovery_completions


def test_repair_supersession_completion_and_recovery_are_exact_receipts(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    database = workspace / "briefloop.db"
    effect_requests: list[tuple[str, str, str]] = []
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        _accept_input_classification(store)
        _record_contamination(store)
        repair_request = _start_repair(store)
        supersession_request = _supersede_input_classification(store)
        repair_completion_request = _complete_repair(store)
        rerun_transition_id = _complete_reopened_stage(store)
        recovery_request = _complete_recovery(store)
        effect_requests.extend(
            [
                (*repair_request, "REPAIR-RECOVERY-001"),
                (*supersession_request, "SUPERSESSION-RECOVERY-001"),
                (
                    *repair_completion_request,
                    "REPAIR-COMPLETION-RECOVERY-001",
                ),
                (*recovery_request, "RECOVERY-COMPLETION-001"),
            ]
        )
        revision = store.current_revision
        snapshot = store.load_snapshot(RUN_ID)
        receipts = {item.transaction_id: item for item in snapshot.transactions}

        supersession_receipt = receipts[supersession_request[0]]
        assert [
            item.supersession_id for item in supersession_receipt.artifact_supersessions
        ] == ["SUPERSESSION-RECOVERY-001"]
        assert len(supersession_receipt.owned_artifact_submissions) == 1
        assert len(supersession_receipt.artifact_revisions) == 1
        assert sorted(supersession_receipt.event_ids) == sorted(
            [
                "EVT-RECOVERY-SUPERSEDE-OWNED-001",
                "EVT-RECOVERY-SUPERSEDE-001",
            ]
        )

        repair_receipt = receipts[repair_completion_request[0]]
        assert [
            item.repair_completion_id for item in repair_receipt.repair_completions
        ] == ["REPAIR-COMPLETION-RECOVERY-001"]
        assert [item.transition_id for item in repair_receipt.stage_transitions] == [
            "TRANSITION-RECOVERY-REOPEN-001"
        ]

        recovery_receipt = receipts[recovery_request[0]]
        assert [item.recovery_id for item in recovery_receipt.recovery_completions] == [
            "RECOVERY-COMPLETION-001"
        ]
        assert [
            item.integrity_revision for item in recovery_receipt.run_integrity_records
        ] == [3]
        assert recovery_receipt.event_ids == ["EVT-RECOVERY-COMPLETE-001"]

        legality = classify_recovery_legality(snapshot)
        assert legality.state == "recovered_current"
        assert snapshot.run_integrity_records[-1].status == "clean"
        assert legality.permitted_artifact_ids == ("input_classification",)

        assert legality.required_rerun_transition_ids == (rerun_transition_id,)
        CoreRunDomainVerifier().verify(store, RUN_ID)

        repair_receipt = receipts[repair_request[0]]
        repair_prefix = store.load_history().snapshot_at_revision(
            RUN_ID,
            repair_receipt.committed_revision,
        )
        assert classify_recovery_legality(repair_prefix).permitted_artifact_ids == (
            "input_classification",
        )

        # R16: a later repair may carry a different finite scope, but the
        # receipt-N projection keeps the original repair's exact authority.
        later_contamination = snapshot.run_integrity_records[-2].model_copy(
            update={
                "integrity_revision": 4,
                "prior_integrity_revision": 3,
                "accepted_transaction_id": recovery_request[0],
            }
        )
        later_repair = snapshot.repair_cycles[0].model_copy(
            update={
                "repair_id": "REPAIR-RECOVERY-LATER-001",
                "contamination_revision": 4,
                "permitted_artifact_ids": ["audited_brief"],
                "accepted_transaction_id": recovery_request[0],
            }
        )
        later_snapshot = replace(
            snapshot,
            run_integrity_records=(
                *snapshot.run_integrity_records,
                later_contamination,
            ),
            repair_cycles=(*snapshot.repair_cycles, later_repair),
        )
        assert classify_recovery_legality(later_snapshot).permitted_artifact_ids == (
            "audited_brief",
        )
        assert classify_recovery_legality(repair_prefix).permitted_artifact_ids == (
            "input_classification",
        )

    with SQLiteControlStore.open(database, clock=CLOCK) as reopened:
        for request_id, fingerprint, primary_id in effect_requests:
            receipt = next(
                item
                for item in reopened.load_history().transactions
                if item.transaction_id == request_id
            )
            replay = resolve_core_replay(
                reopened,
                run_id=RUN_ID,
                request_id=request_id,
                request_fingerprint=fingerprint,
            )
            assert replay is not None
            assert replay.status == "replayed"
            assert replay.primary_record_id == primary_id
            assert replay.receipt == receipt
            assert reopened.current_revision == revision

        with pytest.raises(CoreRunError, match="submission_replay_conflict"):
            resolve_core_replay(
                reopened,
                run_id=RUN_ID,
                request_id=supersession_request[0],
                request_fingerprint="e" * 64,
            )
        assert reopened.current_revision == revision


def test_recovered_clean_integrity_chain_and_receipt_tamper_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        _accept_input_classification(store)
        _record_contamination(store)
        _start_repair(store)
        _supersede_input_classification(store)
        _complete_repair(store)
        _complete_reopened_stage(store)
        recovery_request = _complete_recovery(store)
        snapshot = store.load_snapshot(RUN_ID)

    receipt = next(
        item
        for item in snapshot.transactions
        if item.transaction_id == recovery_request[0]
    )
    initial, contaminated, recovered = snapshot.run_integrity_records
    forged_snapshots = (
        replace(snapshot, recovery_completions=()),
        replace(
            snapshot,
            run_integrity_records=(
                initial,
                contaminated.model_copy(
                    update={
                        "status": "clean",
                        "affected_artifact_id": None,
                        "affected_artifact_revision": None,
                        "expected_workspace_path": None,
                        "expected_sha256": None,
                        "observed_entry_kind": None,
                        "observed_sha256": None,
                        "reason_code": None,
                        "first_detected_at": None,
                        "first_detected_event_id": None,
                    }
                ),
                recovered,
            ),
        ),
        replace(
            snapshot,
            run_integrity_records=(
                initial,
                contaminated,
                recovered.model_copy(update={"prior_integrity_revision": 1}),
            ),
        ),
        replace(
            snapshot,
            run_integrity_records=(
                initial,
                contaminated,
                recovered.model_copy(
                    update={"accepted_transaction_id": "REQ-FORGED-RECOVERY-001"}
                ),
            ),
        ),
        replace(
            snapshot,
            run_integrity_records=(
                initial,
                contaminated,
                recovered.model_copy(update={"request_fingerprint": "f" * 64}),
            ),
        ),
    )
    for forged in forged_snapshots:
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            CoreRunDomainVerifier._verify_integrity_chain(forged)

    store_forgeries = (
        replace(
            snapshot,
            run_integrity_records=(
                initial,
                contaminated.model_copy(update={"status": "clean"}),
                recovered,
            ),
        ),
        replace(
            snapshot,
            run_integrity_records=(
                initial,
                contaminated,
                recovered.model_copy(update={"status": "contaminated"}),
            ),
        ),
        replace(
            snapshot,
            run_integrity_records=(
                initial,
                contaminated,
                recovered.model_copy(update={"prior_integrity_revision": 1}),
            ),
        ),
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        for forged in store_forgeries:
            with pytest.raises(
                ControlStoreIntegrityError,
                match="core_run_relation_invalid",
            ):
                store._verify_core_snapshot_structure(forged)

    for forged_receipt in (
        receipt.model_copy(update={"run_integrity_records": []}),
        receipt.model_copy(
            update={
                "run_integrity_records": [
                    *receipt.run_integrity_records,
                    *receipt.run_integrity_records,
                ]
            }
        ),
    ):
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            _verified_core_receipt_binding(snapshot, forged_receipt)


@pytest.mark.skipif(sys.platform == "win32", reason="requires working-checkout publication")
def test_recovery_service_reset_is_cross_run_and_historical_replay_safe(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    database = workspace / "briefloop.db"
    service = CoreRunRecoveryService(workspace, clock=CLOCK)
    doctor = CoreRunService(workspace, clock=CLOCK).doctor_check(
        IntegrityCheckRequest.model_validate(
            {
                "schema_version": IntegrityCheckRequest.schema_id,
                "request_id": "REQ-RECOVERY-RESET-DOCTOR-001",
                "run_id": RUN_ID,
                "expected_store_revision": 1,
            },
            strict=True,
        )
    )
    assert doctor.status == "committed"
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        before_invocation = store.load_snapshot(RUN_ID)
    invocation = CoreRunService(workspace, clock=CLOCK).start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-RECOVERY-RESET-PLANNER-001",
            run_id=RUN_ID,
            stage_id="source-discovery",
            role_id="source-planner",
            runtime="operator",
            expected_store_revision=before_invocation.store_revision,
        )
    )
    assert invocation.status == "committed", invocation.to_dict()
    candidates = workspace / "scratch" / invocation.primary_record_id / "source_candidates.yaml"
    candidates.parent.mkdir(parents=True, exist_ok=True)
    candidates.write_text("sources:\n  - SRC-RESET-001\n", encoding="utf-8")
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        before_artifact = store.load_snapshot(RUN_ID)
    accepted = ArtifactAcceptanceService(workspace, clock=CLOCK).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-RECOVERY-RESET-SOURCES-001",
            run_id=RUN_ID,
            artifact_id="source_candidates",
            invocation_id=invocation.primary_record_id,
            producer_tool_id=None,
            input_path=candidates.relative_to(workspace).as_posix(),
            expected_store_revision=before_artifact.store_revision,
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )
    assert accepted.status == "committed", accepted.to_dict()
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        predecessor = store.load_snapshot(RUN_ID)
        predecessor_artifact = next(
            item
            for item in predecessor.artifacts
            if item.artifact_id == "source_candidates"
        )
    predecessor_projection = workspace / predecessor_artifact.path
    assert predecessor_projection.is_file()

    def request_for(predecessor: str, successor: str, request_id: str) -> RunResetRequest:
        with SQLiteControlStore.open(database, clock=CLOCK) as store:
            snapshot = store.load_snapshot(predecessor)
            binding = snapshot.run_contract_bindings[0]
        return RunResetRequest.model_validate(
            {
                "schema_version": RunResetRequest.schema_id,
                "request_id": request_id,
                "predecessor_run_id": predecessor,
                "successor_run_id": successor,
                "workspace_id": snapshot.workspace_id,
                "runtime": snapshot.run.runtime,
                "expected_head_run_id": predecessor,
                "expected_store_revision": snapshot.store_revision,
                "expected_workspace_revision": snapshot.store_revision,
                "run_direction": binding.run_direction.model_dump(mode="json"),
                "workspace_config_sha256": binding.workspace_config_sha256,
                "sources_config_sha256": binding.sources_config_sha256,
                "role_topology": binding.role_topology,
                "gate_strictness": binding.gate_strictness,
                "input_governance_required": binding.input_governance_required,
            },
            strict=True,
        )

    first_request = request_for(
        RUN_ID,
        "RUN-RECOVERY-SERVICE-RESET-002",
        "REQ-RECOVERY-SERVICE-RESET-001",
    )
    tampered_workspace = tmp_path / "tampered-reset-preimage"
    shutil.copytree(workspace, tampered_workspace)
    tampered_database = tampered_workspace / "briefloop.db"
    tampered_projection = tampered_workspace / predecessor_artifact.path
    tampered_projection.write_text(
        "sources:\n  - SRC-RESET-TAMPERED\n",
        encoding="utf-8",
    )
    tampered_database_before = tampered_database.read_bytes()
    with SQLiteControlStore.open(tampered_database, clock=CLOCK) as store:
        tampered_revision_before = store.current_revision
        tampered_history_before = store.load_history()
        tampered_head_before = store.load_workspace_run_head()

    tampered_result = CoreRunRecoveryService(
        tampered_workspace,
        clock=CLOCK,
    ).reset_run(first_request)

    assert (tampered_result.status, tampered_result.error_code) == (
        "failed_uncommitted",
        "checkout_projection_preimage_restore_required",
    )
    assert tampered_database.read_bytes() == tampered_database_before
    assert tampered_projection.read_text(encoding="utf-8") == (
        "sources:\n  - SRC-RESET-TAMPERED\n"
    )
    with SQLiteControlStore.open(tampered_database, clock=CLOCK) as store:
        assert store.current_revision == tampered_revision_before
        assert store.load_workspace_run_head() == tampered_head_before
        assert store.load_history() == tampered_history_before

    first = service.reset_run(first_request)
    assert first.status == "committed", first.to_dict()
    assert not predecessor_projection.exists()
    successor_run_id = "RUN-RECOVERY-SERVICE-RESET-002"
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        successor = store.load_snapshot(successor_run_id)
    successor_doctor = CoreRunService(workspace, clock=CLOCK).doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id="REQ-RECOVERY-RESET-DOCTOR-002",
            run_id=successor_run_id,
            expected_store_revision=successor.store_revision,
        )
    )
    assert successor_doctor.status == "committed", successor_doctor.to_dict()
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        successor = store.load_snapshot(successor_run_id)
    successor_invocation = CoreRunService(
        workspace, clock=CLOCK
    ).start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-RECOVERY-RESET-PLANNER-002",
            run_id=successor_run_id,
            stage_id="source-discovery",
            role_id="source-planner",
            runtime="operator",
            expected_store_revision=successor.store_revision,
        )
    )
    assert successor_invocation.status == "committed", (
        successor_invocation.to_dict()
    )
    successor_candidates = (
        workspace
        / "scratch"
        / successor_invocation.primary_record_id
        / "source_candidates.yaml"
    )
    successor_candidates.parent.mkdir(parents=True, exist_ok=True)
    successor_candidates.write_text(
        "sources:\n  - SRC-RESET-002\n", encoding="utf-8"
    )
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        successor = store.load_snapshot(successor_run_id)
    successor_accepted = ArtifactAcceptanceService(
        workspace, clock=CLOCK
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-RECOVERY-RESET-SOURCES-002",
            run_id=successor_run_id,
            artifact_id="source_candidates",
            invocation_id=successor_invocation.primary_record_id,
            producer_tool_id=None,
            input_path=successor_candidates.relative_to(workspace).as_posix(),
            expected_store_revision=successor.store_revision,
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )
    assert successor_accepted.status == "committed", (
        successor_accepted.to_dict()
    )
    assert predecessor_projection.read_text(encoding="utf-8") == (
        "sources:\n  - SRC-RESET-002\n"
    )
    second_request = request_for(
        successor_run_id,
        "RUN-RECOVERY-SERVICE-RESET-003",
        "REQ-RECOVERY-SERVICE-RESET-002",
    )
    second = service.reset_run(second_request)
    assert second.status == "committed"
    assert not predecessor_projection.exists()
    (workspace / "sources.yaml").write_text("not: [valid\n", encoding="utf-8")
    replay = service.reset_run(first_request)
    assert replay.status == "replayed"
    assert replay.receipt == first.receipt
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        latest = store.load_snapshot("RUN-RECOVERY-SERVICE-RESET-003")
        first_snapshot = store.load_snapshot("RUN-RECOVERY-SERVICE-RESET-002")
    assert latest.workspace_run_head.current_run_id == "RUN-RECOVERY-SERVICE-RESET-003"
    first_binding = first_snapshot.receipt_checkout_bindings[-1]
    assert first_binding.pre_run_id == RUN_ID
    assert first_binding.post_run_id == "RUN-RECOVERY-SERVICE-RESET-002"
    assert first_binding.pre_checkout_revision_id == first_binding.post_checkout_revision_id or first_binding.pre_checkout_revision_id is not None
    assert next(
        item for item in first_snapshot.stage_states if item.stage_id == "doctor"
    ).status == "complete"
