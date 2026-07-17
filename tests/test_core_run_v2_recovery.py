from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.contracts.v2 import (
    ArtifactRecord,
    ArtifactRevision,
    ArtifactSupersessionRecord,
    CoreRunInitializeRequest,
    CoreRunEventBinding,
    EventEnvelope,
    OwnedArtifactSubmissionRecord,
    RecoveryCompletionRecord,
    RepairCompletionRecord,
    RepairCycleRecord,
    RunIntegrityRecord,
    RunHeadTransitionRecord,
    RunIdentity,
    StageState,
    StageTransitionRecord,
    TransactionReceipt,
    WorkspaceRunHead,
)
from multi_agent_brief.control_store import (
    ControlStoreIntegrityError,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    sha256_hex,
)
from multi_agent_brief.core_run_v2 import CoreRunService
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.policy import derived_id, transaction_type_for
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
    unit.commit()


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
    unit.commit()
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
    unit.commit()
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
    unit.commit()
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
        result_status="pending",
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
    unit.commit()
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
    unit.commit()
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
    unit.commit()
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
    contract = predecessor.run_contract_bindings[0].model_copy(
        update={
            "run_id": successor_run_id,
            "created_at": NOW,
            "initialization_event_id": initialized_event_id,
            "accepted_transaction_id": transaction_id,
            "request_fingerprint": request_fingerprint,
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
            unit.put_artifact_revision(
                revision.model_copy(update={"run_id": successor_run_id}),
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
    unit.commit()
    return transaction_id, request_fingerprint


def test_clean_historical_prefix_has_no_recovery_authority(tmp_path: Path) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        prefix = history.snapshot_at_revision(RUN_ID, 1)

    legality = classify_recovery_legality(prefix)
    assert legality.state == "not_required"
    assert legality.ordinary_consumption_eligible is True
    assert (
        classify_effect_authorization(
            prefix,
            CoreEffect.FINALIZE_RENDER,
        ).decision
        == "allow"
    )
    assert (
        classify_effect_authorization(
            prefix,
            CoreEffect.REPAIR_START,
        ).decision
        == "deny"
    )


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


def test_repair_start_is_receipt_bound_replayable_and_restart_safe(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    database = workspace / "briefloop.db"
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        _accept_input_classification(store)
        _record_contamination(store)
        transaction_id, request_fingerprint = _start_repair(store)
        revision = store.current_revision
        snapshot = store.load_snapshot(RUN_ID)
        receipt = next(
            item
            for item in snapshot.transactions
            if item.transaction_id == transaction_id
        )

        assert [item.repair_id for item in receipt.repair_cycles] == [
            "REPAIR-RECOVERY-001"
        ]
        assert receipt.event_ids == ["EVT-RECOVERY-REPAIR-START-001"]
        assert classify_recovery_legality(snapshot).state == "active_repair"
        CoreRunDomainVerifier().verify(store, RUN_ID)

    with SQLiteControlStore.open(database, clock=CLOCK) as reopened:
        replay = resolve_core_replay(
            reopened,
            run_id=RUN_ID,
            request_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        assert replay is not None
        assert replay.receipt == receipt
        assert replay.status == "replayed"
        assert replay.primary_record_id == "REPAIR-RECOVERY-001"
        assert reopened.current_revision == revision

        with pytest.raises(CoreRunError, match="submission_replay_conflict"):
            resolve_core_replay(
                reopened,
                run_id=RUN_ID,
                request_id=transaction_id,
                request_fingerprint="f" * 64,
            )
        assert reopened.current_revision == revision


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
        assert recovery_receipt.event_ids == ["EVT-RECOVERY-COMPLETE-001"]

        legality = classify_recovery_legality(snapshot)
        assert legality.state == "recovered_current"
        assert legality.required_rerun_transition_ids == (rerun_transition_id,)
        CoreRunDomainVerifier().verify(store, RUN_ID)

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


def test_repair_start_before_commit_failure_rolls_back_every_relation(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    database = workspace / "briefloop.db"
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        _accept_input_classification(store)
        _record_contamination(store)
        before = store.current_revision

    def fail(stage: str) -> None:
        if stage == "before_commit":
            raise ControlStoreIntegrityError("injected_core_run_failure")

    with SQLiteControlStore.open(
        database,
        clock=CLOCK,
        _failure_hook=fail,
    ) as store:
        with pytest.raises(
            ControlStoreIntegrityError,
            match="injected_core_run_failure",
        ):
            _start_repair(store)

    with SQLiteControlStore.open(database, clock=CLOCK) as reopened:
        snapshot = reopened.load_snapshot(RUN_ID)
        assert reopened.current_revision == before
        assert not snapshot.repair_cycles
        assert all(
            item.transaction_id != "REQ-RECOVERY-REPAIR-START-001"
            for item in snapshot.transactions
        )
        assert all(
            item.transaction_id != "REQ-RECOVERY-REPAIR-START-001"
            for item in snapshot.events
        )


def test_sequential_resets_verify_each_as_of_prefix_and_replay_first_reset(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    database = workspace / "briefloop.db"
    second_run_id = "RUN-RECOVERY-PREFIX-002"
    third_run_id = "RUN-RECOVERY-PREFIX-003"
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        first_request = _reset_run(
            store,
            predecessor_run_id=RUN_ID,
            successor_run_id=second_run_id,
            sequence=1,
        )
        second_request = _reset_run(
            store,
            predecessor_run_id=second_run_id,
            successor_run_id=third_run_id,
            sequence=2,
        )
        history = store.load_history()

        first_prefix = history.snapshot_at_revision(RUN_ID, 1)
        second_prefix = history.snapshot_at_revision(second_run_id, 2)
        third_prefix = history.snapshot_at_revision(third_run_id, 3)
        assert first_prefix.workspace_run_head is not None
        assert first_prefix.workspace_run_head.current_run_id == RUN_ID
        assert second_prefix.workspace_run_head is not None
        assert second_prefix.workspace_run_head.current_run_id == second_run_id
        assert third_prefix.workspace_run_head is not None
        assert third_prefix.workspace_run_head.current_run_id == third_run_id
        assert [
            item.predecessor_run_id for item in second_prefix.run_head_transitions
        ] == [RUN_ID]
        assert [
            item.predecessor_run_id for item in third_prefix.run_head_transitions
        ] == [second_run_id]
        CoreRunDomainVerifier().verify_history(history)
        CoreRunDomainVerifier().verify(store, third_run_id)
        final_revision = store.current_revision
        first_receipt = next(
            item
            for item in history.transactions
            if item.transaction_id == first_request[0]
        )
        second_receipt = next(
            item
            for item in history.transactions
            if item.transaction_id == second_request[0]
        )
        assert len(first_receipt.run_head_transitions) == 1
        assert len(second_receipt.run_head_transitions) == 1

    with SQLiteControlStore.open(database, clock=CLOCK) as reopened:
        replay = resolve_core_replay(
            reopened,
            run_id=second_run_id,
            request_id=first_request[0],
            request_fingerprint=first_request[1],
        )
        assert replay is not None
        assert replay.status == "replayed"
        assert replay.receipt == first_receipt
        assert replay.primary_record_id == "HEAD-RECOVERY-RESET-001"
        assert reopened.current_revision == final_revision

        with pytest.raises(CoreRunError, match="submission_replay_conflict"):
            resolve_core_replay(
                reopened,
                run_id=second_run_id,
                request_id=first_request[0],
                request_fingerprint="d" * 64,
            )
        assert reopened.current_revision == final_revision


def test_reset_history_is_bound_to_the_exact_predecessor_prefix(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        predecessor = history.snapshot_at_revision(RUN_ID, 1)

    transaction_id = "REQ-RECOVERY-RESET-001"
    successor_run_id = "RUN-RECOVERY-PREFIX-002"
    transition = _record(
        RunHeadTransitionRecord,
        head_transition_id="HEAD-RECOVERY-RESET-001",
        workspace_id=predecessor.workspace_id,
        predecessor_run_id=RUN_ID,
        successor_run_id=successor_run_id,
        prior_workspace_revision=1,
        successor_workspace_revision=2,
        reason_code="run_reset",
        successor_disposition="non_reference",
        created_at="2026-07-17T00:00:00Z",
        transition_event_id="EVT-RECOVERY-RESET-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="a" * 64,
    )
    receipt = _record(
        TransactionReceipt,
        transaction_id=transaction_id,
        run_id=successor_run_id,
        transaction_type=transaction_type_for("run_head_transition"),
        prior_revision=1,
        committed_revision=2,
        committed_at="2026-07-17T00:00:00Z",
        projection_status="current",
        run_head_transitions=[{"head_transition_id": transition.head_transition_id}],
    )
    post = replace(
        predecessor,
        store_revision=2,
        run=_record(
            RunIdentity,
            run_id=successor_run_id,
            workspace_id=predecessor.workspace_id,
            runtime=predecessor.run.runtime,
            created_at="2026-07-17T00:00:00Z",
        ),
        workspace_run_head=_record(
            WorkspaceRunHead,
            workspace_id=predecessor.workspace_id,
            current_run_id=successor_run_id,
            updated_at="2026-07-17T00:00:00Z",
        ),
        run_head_transitions=(transition,),
        transactions=(receipt,),
    )

    CoreRunDomainVerifier._verify_reset_history(history, post, receipt)

    forged_transitions = (
        transition.model_copy(
            update={"predecessor_run_id": "RUN-RECOVERY-MISSING-001"}
        ),
        transition.model_copy(
            update={
                "prior_workspace_revision": 0,
                "successor_workspace_revision": 1,
            }
        ),
    )
    for forged in forged_transitions:
        with pytest.raises(CoreRunError, match="reset_history_invalid"):
            CoreRunDomainVerifier._verify_reset_history(
                history,
                replace(post, run_head_transitions=(forged,)),
                receipt,
            )


def test_repair_complete_and_reset_reject_any_extra_receipt_event(
    tmp_path: Path,
) -> None:
    repair_workspace = _initialized_workspace(tmp_path / "repair")
    with SQLiteControlStore.open(
        repair_workspace / "briefloop.db",
        clock=CLOCK,
    ) as store:
        _accept_input_classification(store)
        _record_contamination(store)
        _start_repair(store)
        _supersede_input_classification(store)
        repair_request = _complete_repair(store)
        repair_snapshot = store.load_snapshot(RUN_ID)
    repair_receipt = next(
        item
        for item in repair_snapshot.transactions
        if item.transaction_id == repair_request[0]
    )
    repair_extra = _event(
        event_id="EVT-REPAIR-EXTRA-001",
        transaction_id=repair_receipt.transaction_id,
        event_type="run_blocked",
        reason="forged extra repair event",
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(
            replace(
                repair_snapshot,
                events=(*repair_snapshot.events, repair_extra),
            ),
            repair_receipt.model_copy(
                update={"event_ids": [*repair_receipt.event_ids, repair_extra.event_id]}
            ),
        )

    reset_workspace = _initialized_workspace(tmp_path / "reset")
    successor_run_id = "RUN-RECOVERY-EXTRA-EVENT-002"
    with SQLiteControlStore.open(
        reset_workspace / "briefloop.db",
        clock=CLOCK,
    ) as store:
        reset_request = _reset_run(
            store,
            predecessor_run_id=RUN_ID,
            successor_run_id=successor_run_id,
            sequence=1,
        )
        reset_snapshot = store.load_snapshot(successor_run_id)
    reset_receipt = next(
        item
        for item in reset_snapshot.transactions
        if item.transaction_id == reset_request[0]
    )
    reset_extra = _event(
        event_id="EVT-RESET-EXTRA-001",
        transaction_id=reset_receipt.transaction_id,
        event_type="stage_status_changed",
        stage_id="doctor",
        reason="forged extra successor initialization event",
    ).model_copy(update={"run_id": successor_run_id})
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(
            replace(
                reset_snapshot,
                events=(*reset_snapshot.events, reset_extra),
            ),
            reset_receipt.model_copy(
                update={"event_ids": [*reset_receipt.event_ids, reset_extra.event_id]}
            ),
        )
