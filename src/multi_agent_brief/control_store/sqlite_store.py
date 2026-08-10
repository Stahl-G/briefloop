"""Typed SQLite ControlStore substrate with no current runtime authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Iterable, Mapping, TypeVar, cast
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from multi_agent_brief.contracts.v2 import (
    AcceptedProposalRecord,
    AcceptedSourceRecord,
    Approval,
    ApprovalPackageBinding,
    ArtifactIdentityRecord,
    ArtifactIdentityReference,
    ArtifactRecord,
    ArtifactRevision,
    ArtifactRevisionReference,
    ClaimFreezeRecord,
    ClaimRecord,
    ClaimSourceBinding,
    CheckoutPublicationAck,
    CheckoutPublicationCleanupObservation,
    CheckoutPublicationIntent,
    CheckoutPublicationIntentReference,
    CheckoutPublicationMember,
    PublicationIdentityV1,
    CheckoutRevisionMember,
    CheckoutRevisionRecord,
    CheckoutRevisionReference,
    ContractId,
    Delivery,
    DeliveryAttemptRecord,
    DeliveryAuthorizationRecord,
    DeliveryResultRecord,
    EventEnvelope,
    GateArtifactBinding,
    GateEvaluationRecord,
    GateFindingRecord,
    GateRepairArtifactBinding,
    GateRepairCycleRecord,
    GateRepairOutcomeRecord,
    GuidanceReuseScopeV1,
    FinalizationRecord,
    FinalizeRenderRecord,
    Invocation,
    OwnedArtifactSubmissionRecord,
    ProposalSourceBinding,
    PackageArtifactBinding,
    PackageReadyRecord,
    PostFinalAssessmentAbandonmentRecord,
    PostFinalAssessmentAbandonmentReference,
    PostFinalAssessmentExecutionRecord,
    PostFinalAssessmentExecutionReference,
    PostFinalAssessmentPolicyRevision,
    PostFinalAssessmentPolicyRevisionReference,
    PostFinalAssessmentRequestRecord,
    PostFinalAssessmentRequestReference,
    PostFinalAssessmentResultRecord,
    PostFinalAssessmentResultReference,
    PostFinalFindingDispositionRecord,
    PostFinalFindingDispositionReference,
    PostFinalHumanObservationRecord,
    PostFinalHumanObservationReference,
    PostFinalGuidanceDraftRevision,
    PostFinalGuidanceDraftReference,
    PostFinalGuidanceStatusRevision,
    PostFinalGuidanceStatusReference,
    post_final_guidance_status_transition_allowed,
    RecoveryCompletionRecord,
    RepairCompletionRecord,
    RepairCycleRecord,
    ReceiptCheckoutBinding,
    ReceiptCheckoutBindingReference,
    ArtifactSupersessionRecord,
    RunContractBinding,
    RunExecutionAuthorization,
    RunSourceAcquisitionAttemptAuthorization,
    RunSourceDiscoveryAuthorization,
    RuntimeSourceSearchPlanV2,
    TavilyAcquisitionBundleRecordV2,
    RunIdentity,
    RunGuidanceSelectionDecisionRecord,
    RunGuidanceSelectionDecisionReference,
    RunGuidanceSnapshotItemRecord,
    RunGuidanceSnapshotItemReference,
    RunGuidanceSnapshotRecord,
    RunGuidanceSnapshotReference,
    RunIntegrityRecord,
    RunArchiveArtifactBinding,
    RunArchiveRecord,
    RunHeadTransitionRecord,
    StageArtifactBinding,
    StageGateBinding,
    StageState,
    StageTransitionRecord,
    StrictModel,
    TransactionReceipt,
    WorkspaceRunHead,
    _CheckoutStructureError,
    _build_checkout_revision_structure,
    canonical_run_direction_for_binding,
    _derive_publication_structure,
    _current_post_final_disposition_at_cutoff,
    _publication_identity_digest,
)
from multi_agent_brief.control_store.errors import (
    ControlStoreCommitOutcomeUnknown,
    ControlStoreConflict,
    ControlStoreError,
    ControlStoreIntegrityError,
    ControlStoreStateError,
)
from multi_agent_brief.control_store.schema import (
    configure_connection,
    initialize_schema,
    verify_schema,
)
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    canonical_model_text,
    decode_model,
    sha256_hex,
)


_ModelT = TypeVar("_ModelT", bound=StrictModel)
_FailureHook = Callable[[str], None]
_CONTRACT_ID_ADAPTER = TypeAdapter(ContractId)
_POST_FINAL_RECEIPT_RELATION_FIELDS = (
    "post_final_assessment_policy_revisions",
    "post_final_assessment_requests",
    "post_final_assessment_results",
    "post_final_finding_dispositions",
    "post_final_human_observations",
    "post_final_guidance_drafts",
    "post_final_guidance_statuses",
)
_POST_FINAL_ABANDONMENT_RECEIPT_FIELD = "post_final_assessment_abandonments"
_GUIDANCE_RECEIPT_RELATION_FIELDS = (
    "run_guidance_snapshots",
    "run_guidance_selection_decisions",
    "run_guidance_snapshot_items",
)
_POST_FINAL_RECEIPT_TRANSACTION_TYPES = frozenset(
    {
        "post_final_assessment_policy",
        "post_final_assessment_claim",
        "post_final_assessment_series_claim",
        "post_final_assessment_execution",
        "post_final_assessment_result",
        "post_final_finding_disposition",
        "post_final_human_observation",
        "post_final_guidance_draft",
        "post_final_guidance_status",
    }
)
_RECEIPT_COMPATIBILITY_BOUNDARY_ID = (
    "briefloop.transaction_receipt_relation_compatibility.v1"
)
_SOURCE_ATTEMPT_COMPATIBILITY_BOUNDARY_ID = (
    "briefloop.source_acquisition_attempt_compatibility.v1"
)
_POST_FINAL_ABANDONMENT_COMPATIBILITY_BOUNDARY_ID = (
    "briefloop.post_final_assessment_abandonment_compatibility.v1"
)
_GUIDANCE_SUCCESSOR_TRANSACTION_TYPE = "core-v2-run-successor-start"
_MAX_GUIDANCE_SNAPSHOT_ITEMS = 16
_MAX_GUIDANCE_SNAPSHOT_UTF8_BYTES = 65_536
_EXTENDED_RECORD_MODELS = (
    WorkspaceRunHead,
    ArtifactIdentityRecord,
    AcceptedSourceRecord,
    AcceptedProposalRecord,
    ProposalSourceBinding,
    RunContractBinding,
    RunExecutionAuthorization,
    RunSourceDiscoveryAuthorization,
    RunSourceAcquisitionAttemptAuthorization,
    RuntimeSourceSearchPlanV2,
    TavilyAcquisitionBundleRecordV2,
    OwnedArtifactSubmissionRecord,
    StageTransitionRecord,
    StageArtifactBinding,
    StageGateBinding,
    ClaimRecord,
    ClaimSourceBinding,
    ClaimFreezeRecord,
    GateEvaluationRecord,
    GateFindingRecord,
    GateArtifactBinding,
    GateRepairCycleRecord,
    GateRepairArtifactBinding,
    GateRepairOutcomeRecord,
    RunIntegrityRecord,
    RepairCycleRecord,
    ArtifactSupersessionRecord,
    RepairCompletionRecord,
    RecoveryCompletionRecord,
    RunHeadTransitionRecord,
    FinalizeRenderRecord,
    FinalizationRecord,
    RunArchiveRecord,
    RunArchiveArtifactBinding,
    PackageReadyRecord,
    PackageArtifactBinding,
    ApprovalPackageBinding,
    DeliveryAuthorizationRecord,
    DeliveryAttemptRecord,
    DeliveryResultRecord,
    PostFinalAssessmentPolicyRevision,
    PostFinalAssessmentRequestRecord,
    PostFinalAssessmentAbandonmentRecord,
    PostFinalAssessmentExecutionRecord,
    PostFinalAssessmentResultRecord,
    PostFinalFindingDispositionRecord,
    PostFinalHumanObservationRecord,
    PostFinalGuidanceDraftRevision,
    PostFinalGuidanceStatusRevision,
    RunGuidanceSelectionDecisionRecord,
    RunGuidanceSnapshotItemRecord,
    RunGuidanceSnapshotRecord,
    CheckoutRevisionRecord,
    CheckoutRevisionMember,
    ReceiptCheckoutBinding,
    CheckoutPublicationIntent,
    CheckoutPublicationMember,
    CheckoutPublicationAck,
    CheckoutPublicationCleanupObservation,
)


def _canonical_record_text(record: StrictModel) -> str:
    if type(record) not in _EXTENDED_RECORD_MODELS:
        return canonical_model_text(record)
    payload = record.model_dump(mode="json", exclude_unset=False)
    if (
        type(record) is PostFinalAssessmentRequestRecord
        and record.schema_version == PostFinalAssessmentRequestRecord.schema_id
    ):
        for field in (
            "assessment_generation",
            "predecessor_assessment_request_id",
            "predecessor_assessment_request_fingerprint",
            "predecessor_assessment_result_id",
            "predecessor_result_fingerprint",
            "predecessor_abandonment_id",
            "predecessor_abandonment_fingerprint",
            "assessment_purpose",
            "human_actor_id",
            "human_request_id",
            "authorization_fingerprint",
        ):
            payload.pop(field, None)
    if type(record) is RunContractBinding:
        payload["run_direction"] = canonical_run_direction_for_binding(
            payload["run_direction"]
        )
    return canonical_json_bytes(payload).decode("utf-8")


def _decode_record(
    model_type: type[_ModelT],
    payload_text: str,
    *,
    receipt_committed_revision: int | None = None,
    legacy_receipt_max_committed_revision: int | None = None,
    legacy_source_attempt_receipt_max_committed_revision: int | None = None,
    legacy_post_final_abandonment_receipt_max_committed_revision: int | None = None,
) -> _ModelT:
    if model_type is TransactionReceipt:
        try:
            payload = json.loads(payload_text)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ControlStoreIntegrityError("stored_payload_invalid") from exc
        if not isinstance(payload, dict):
            raise ControlStoreIntegrityError("stored_payload_invalid")
        if any(field not in payload for field in _GUIDANCE_RECEIPT_RELATION_FIELDS):
            raise ControlStoreIntegrityError("stored_payload_not_canonical")
        missing_post_final = {
            field
            for field in _POST_FINAL_RECEIPT_RELATION_FIELDS
            if field not in payload
        }
        attempt_field = "run_source_acquisition_attempt_authorizations"
        missing_attempt = attempt_field not in payload
        missing_abandonment = _POST_FINAL_ABANDONMENT_RECEIPT_FIELD not in payload
        if not missing_post_final and not missing_attempt and not missing_abandonment:
            return cast(_ModelT, decode_model(TransactionReceipt, payload_text))
        post_final_invalid = bool(missing_post_final) and (
            type(receipt_committed_revision) is not int
            or type(legacy_receipt_max_committed_revision) is not int
            or receipt_committed_revision < 1
            or legacy_receipt_max_committed_revision < 0
            or receipt_committed_revision > legacy_receipt_max_committed_revision
            or missing_post_final != set(_POST_FINAL_RECEIPT_RELATION_FIELDS)
            or payload.get("transaction_type") in _POST_FINAL_RECEIPT_TRANSACTION_TYPES
        )
        source_attempt_invalid = missing_attempt and (
            type(receipt_committed_revision) is not int
            or type(legacy_source_attempt_receipt_max_committed_revision) is not int
            or receipt_committed_revision < 1
            or legacy_source_attempt_receipt_max_committed_revision < 0
            or receipt_committed_revision
            > legacy_source_attempt_receipt_max_committed_revision
            or payload.get("transaction_type")
            == "core-v2-source-acquisition-attempt-authorize"
        )
        abandonment_invalid = missing_abandonment and (
            type(receipt_committed_revision) is not int
            or type(legacy_post_final_abandonment_receipt_max_committed_revision)
            is not int
            or receipt_committed_revision < 1
            or legacy_post_final_abandonment_receipt_max_committed_revision < 0
            or receipt_committed_revision
            > legacy_post_final_abandonment_receipt_max_committed_revision
            or payload.get("transaction_type") == "post_final_assessment_abandonment"
        )
        if post_final_invalid or source_attempt_invalid or abandonment_invalid:
            raise ControlStoreIntegrityError("stored_payload_not_canonical")
        expanded = dict(payload)
        expanded.update({field: [] for field in missing_post_final})
        if missing_attempt:
            expanded[attempt_field] = []
        if missing_abandonment:
            expanded[_POST_FINAL_ABANDONMENT_RECEIPT_FIELD] = []
        try:
            receipt = TransactionReceipt.model_validate(expanded)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ControlStoreIntegrityError("stored_payload_invalid") from exc
        legacy_projection = receipt.model_dump(mode="json", exclude_unset=False)
        for field in missing_post_final:
            if legacy_projection.pop(field) != []:
                raise ControlStoreIntegrityError("stored_payload_not_canonical")
        if missing_attempt and legacy_projection.pop(attempt_field) != []:
            raise ControlStoreIntegrityError("stored_payload_not_canonical")
        if (
            missing_abandonment
            and legacy_projection.pop(_POST_FINAL_ABANDONMENT_RECEIPT_FIELD) != []
        ):
            raise ControlStoreIntegrityError("stored_payload_not_canonical")
        if canonical_json_bytes(legacy_projection).decode("utf-8") != payload_text:
            raise ControlStoreIntegrityError("stored_payload_not_canonical")
        if receipt.committed_revision != receipt_committed_revision:
            raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        return cast(_ModelT, receipt)
    if model_type not in _EXTENDED_RECORD_MODELS:
        return decode_model(model_type, payload_text)
    try:
        model = model_type.model_validate_json(payload_text, strict=True)
    except (ValidationError, ValueError) as exc:
        raise ControlStoreIntegrityError("stored_payload_invalid") from exc
    if _canonical_record_text(model) != payload_text:
        raise ControlStoreIntegrityError("stored_payload_not_canonical")
    return model


def _validate_contract_id(value: object, error_code: str) -> str:
    """Reuse the PR-1 ContractId vocabulary without copying its grammar."""

    try:
        return _CONTRACT_ID_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise ControlStoreIntegrityError(error_code) from exc


def _guidance_reuse_scope(binding: RunContractBinding) -> GuidanceReuseScopeV1:
    direction = binding.run_direction
    payload = {
        "schema_version": GuidanceReuseScopeV1.schema_id,
        "audience": direction.audience,
        "audience_profile": direction.audience_profile,
        "output_language": direction.output_language,
        "output_style": direction.output_style,
        "output_formats": list(direction.output_formats),
        "cadence": direction.cadence,
    }
    payload["scope_fingerprint"] = canonical_fingerprint(payload)
    try:
        return GuidanceReuseScopeV1.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise ControlStoreIntegrityError("control_store_integrity_invalid") from exc


def _validate_blob_topology(
    blob_root: Path,
    *,
    error_code: str,
    blob_path: Path | None = None,
    allow_missing_directories: bool = False,
    require_blob: bool = False,
    missing_blob_error_code: str | None = None,
) -> tuple[Path, ...]:
    """Validate one lexical, non-symlink blob tree without following links."""

    def fail(exc: BaseException | None = None) -> None:
        if exc is None:
            raise ControlStoreIntegrityError(error_code)
        raise ControlStoreIntegrityError(error_code) from exc

    def require_real_directory(path: Path, *, allow_missing: bool = False) -> bool:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            if allow_missing:
                return False
            if require_blob and blob_path is not None:
                raise ControlStoreIntegrityError(missing_blob_error_code or error_code)
            fail()
        except OSError as exc:
            fail(exc)
        if not stat.S_ISDIR(mode):
            fail()
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            fail(exc)
        if not resolved.is_relative_to(root_resolved):
            fail()
        return True

    try:
        root_mode = blob_root.lstat().st_mode
    except OSError as exc:
        fail(exc)
    if not stat.S_ISDIR(root_mode):
        fail()
    try:
        root_resolved = blob_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        fail(exc)
    if os.path.normcase(str(root_resolved)) != os.path.normcase(str(blob_root)):
        fail()

    hash_root = blob_root / "sha256"
    if blob_path is not None:
        try:
            relative = blob_path.relative_to(blob_root)
        except ValueError:
            fail()
        parts = relative.parts
        if (
            len(parts) != 3
            or parts[0] != "sha256"
            or len(parts[1]) != 2
            or len(parts[2]) != 64
            or parts[1] != parts[2][:2]
            or any(char not in "0123456789abcdef" for char in parts[2])
        ):
            fail()
        if not require_real_directory(
            hash_root,
            allow_missing=allow_missing_directories,
        ):
            return ()
        prefix = hash_root / parts[1]
        if not require_real_directory(
            prefix,
            allow_missing=allow_missing_directories,
        ):
            return ()
        try:
            mode = blob_path.lstat().st_mode
        except FileNotFoundError:
            if require_blob:
                raise ControlStoreIntegrityError(missing_blob_error_code or error_code)
            return ()
        except OSError as exc:
            fail(exc)
        if not stat.S_ISREG(mode):
            fail()
        try:
            resolved_blob = blob_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            fail(exc)
        if not resolved_blob.is_relative_to(root_resolved):
            fail()
        return (blob_path,)

    files: list[Path] = []
    try:
        with os.scandir(blob_root) as root_entries:
            for root_entry in root_entries:
                if root_entry.name == "sha256":
                    continue
                if root_entry.is_symlink() or not root_entry.is_file(
                    follow_symlinks=False
                ):
                    fail()
                files.append(Path(root_entry.path))
    except OSError as exc:
        fail(exc)
    if not require_real_directory(hash_root, allow_missing=True):
        return tuple(sorted(files, key=lambda path: path.as_posix()))
    try:
        with os.scandir(hash_root) as prefixes:
            for prefix_entry in prefixes:
                if prefix_entry.is_symlink() or not prefix_entry.is_dir(
                    follow_symlinks=False
                ):
                    fail()
                prefix = Path(prefix_entry.path)
                require_real_directory(prefix)
                with os.scandir(prefix) as blobs:
                    for blob_entry in blobs:
                        if blob_entry.is_symlink() or not blob_entry.is_file(
                            follow_symlinks=False
                        ):
                            fail()
                        path = Path(blob_entry.path)
                        try:
                            resolved_blob = path.resolve(strict=True)
                        except (OSError, RuntimeError) as exc:
                            fail(exc)
                        if not resolved_blob.is_relative_to(root_resolved):
                            fail()
                        files.append(path)
    except OSError as exc:
        fail(exc)
    return tuple(sorted(files, key=lambda path: path.as_posix()))


@dataclass(frozen=True)
class ControlStoreSnapshot:
    """One immutable typed view of a run at the store's current revision."""

    workspace_id: str
    store_revision: int
    run: RunIdentity
    workspace_run_head: WorkspaceRunHead | None
    stage_states: tuple[StageState, ...]
    invocations: tuple[Invocation, ...]
    artifacts: tuple[ArtifactRecord, ...]
    artifact_identities: tuple[ArtifactIdentityRecord, ...]
    artifact_revisions: tuple[ArtifactRevision, ...]
    events: tuple[EventEnvelope, ...]
    approvals: tuple[Approval, ...]
    deliveries: tuple[Delivery, ...]
    sources: tuple[AcceptedSourceRecord, ...]
    accepted_proposals: tuple[AcceptedProposalRecord, ...]
    proposal_source_bindings: tuple[ProposalSourceBinding, ...]
    run_contract_bindings: tuple[RunContractBinding, ...]
    run_execution_authorizations: tuple[RunExecutionAuthorization, ...]
    run_source_discovery_authorizations: tuple[RunSourceDiscoveryAuthorization, ...]
    run_source_acquisition_attempt_authorizations: tuple[
        RunSourceAcquisitionAttemptAuthorization, ...
    ]
    runtime_source_search_plans: tuple[RuntimeSourceSearchPlanV2, ...]
    tavily_acquisition_bundle_records: tuple[TavilyAcquisitionBundleRecordV2, ...]
    owned_artifact_submissions: tuple[OwnedArtifactSubmissionRecord, ...]
    stage_transitions: tuple[StageTransitionRecord, ...]
    stage_artifact_bindings: tuple[StageArtifactBinding, ...]
    stage_gate_bindings: tuple[StageGateBinding, ...]
    claims: tuple[ClaimRecord, ...]
    claim_source_bindings: tuple[ClaimSourceBinding, ...]
    claim_freezes: tuple[ClaimFreezeRecord, ...]
    gate_evaluations: tuple[GateEvaluationRecord, ...]
    gate_findings: tuple[GateFindingRecord, ...]
    gate_artifact_bindings: tuple[GateArtifactBinding, ...]
    run_integrity_records: tuple[RunIntegrityRecord, ...]
    repair_cycles: tuple[RepairCycleRecord, ...]
    gate_repair_cycles: tuple[GateRepairCycleRecord, ...]
    gate_repair_artifact_bindings: tuple[GateRepairArtifactBinding, ...]
    gate_repair_outcomes: tuple[GateRepairOutcomeRecord, ...]
    artifact_supersessions: tuple[ArtifactSupersessionRecord, ...]
    repair_completions: tuple[RepairCompletionRecord, ...]
    recovery_completions: tuple[RecoveryCompletionRecord, ...]
    run_head_transitions: tuple[RunHeadTransitionRecord, ...]
    finalize_renders: tuple[FinalizeRenderRecord, ...]
    finalizations: tuple[FinalizationRecord, ...]
    run_archives: tuple[RunArchiveRecord, ...]
    run_archive_artifact_bindings: tuple[RunArchiveArtifactBinding, ...]
    package_ready_records: tuple[PackageReadyRecord, ...]
    package_artifact_bindings: tuple[PackageArtifactBinding, ...]
    approval_package_bindings: tuple[ApprovalPackageBinding, ...]
    delivery_authorizations: tuple[DeliveryAuthorizationRecord, ...]
    delivery_attempts: tuple[DeliveryAttemptRecord, ...]
    delivery_results: tuple[DeliveryResultRecord, ...]
    post_final_assessment_policy_revisions: tuple[
        PostFinalAssessmentPolicyRevision, ...
    ]
    post_final_assessment_requests: tuple[PostFinalAssessmentRequestRecord, ...]
    post_final_assessment_abandonments: tuple[PostFinalAssessmentAbandonmentRecord, ...]
    post_final_assessment_executions: tuple[PostFinalAssessmentExecutionRecord, ...]
    post_final_assessment_results: tuple[PostFinalAssessmentResultRecord, ...]
    post_final_finding_dispositions: tuple[PostFinalFindingDispositionRecord, ...]
    post_final_human_observations: tuple[PostFinalHumanObservationRecord, ...]
    post_final_guidance_drafts: tuple[PostFinalGuidanceDraftRevision, ...]
    post_final_guidance_statuses: tuple[PostFinalGuidanceStatusRevision, ...]
    run_guidance_snapshots: tuple[RunGuidanceSnapshotRecord, ...]
    run_guidance_selection_decisions: tuple[RunGuidanceSelectionDecisionRecord, ...]
    run_guidance_snapshot_items: tuple[RunGuidanceSnapshotItemRecord, ...]
    checkout_revisions: tuple[CheckoutRevisionRecord, ...]
    checkout_revision_members: tuple[CheckoutRevisionMember, ...]
    receipt_checkout_bindings: tuple[ReceiptCheckoutBinding, ...]
    checkout_publication_intents: tuple[CheckoutPublicationIntent, ...]
    checkout_publication_members: tuple[CheckoutPublicationMember, ...]
    checkout_publication_acks: tuple[CheckoutPublicationAck, ...]
    checkout_publication_cleanup_observations: tuple[
        CheckoutPublicationCleanupObservation, ...
    ]
    transactions: tuple[TransactionReceipt, ...]


@dataclass(frozen=True)
class ControlStoreHistory:
    """One verified SQLite read snapshot with pure as-of projections."""

    workspace_id: str
    store_revision: int
    snapshots: tuple[ControlStoreSnapshot, ...]
    artifact_contents: Mapping[tuple[str, str, int], bytes]

    @property
    def transactions(self) -> tuple[TransactionReceipt, ...]:
        return tuple(
            sorted(
                (
                    receipt
                    for snapshot in self.snapshots
                    for receipt in snapshot.transactions
                ),
                key=lambda item: item.committed_revision,
            )
        )

    def read_artifact_revision_bytes(
        self,
        run_id: str,
        artifact_id: str,
        revision: int,
    ) -> bytes:
        try:
            return self.artifact_contents[(run_id, artifact_id, revision)]
        except KeyError as exc:
            raise ControlStoreStateError("artifact_revision_not_found") from exc

    def snapshot_at_revision(
        self,
        run_id: str,
        committed_revision: int,
    ) -> ControlStoreSnapshot:
        """Project one run strictly from receipt-owned rows through a revision."""

        if (
            type(committed_revision) is not int
            or committed_revision < 1
            or committed_revision > self.store_revision
        ):
            raise ControlStoreStateError("store_revision_not_found")
        full = next(
            (item for item in self.snapshots if item.run.run_id == run_id),
            None,
        )
        if full is None:
            raise ControlStoreStateError("run_not_found")
        transactions = tuple(
            item
            for item in full.transactions
            if item.committed_revision <= committed_revision
        )
        if not transactions:
            raise ControlStoreStateError("run_not_found_at_revision")

        def relation_keys(
            name: str, fields: tuple[str, ...]
        ) -> set[tuple[object, ...]]:
            return {
                tuple(getattr(reference, field) for field in fields)
                for receipt in transactions
                for reference in getattr(receipt, name)
            }

        event_ids = {
            event_id for receipt in transactions for event_id in receipt.event_ids
        }
        revision_keys = relation_keys("artifact_revisions", ("artifact_id", "revision"))
        identity_ids = {
            reference.artifact_id
            for receipt in transactions
            for reference in receipt.artifact_identities
        }
        source_ids = {
            source_id for receipt in transactions for source_id in receipt.source_ids
        }
        proposal_ids = {
            proposal_id
            for receipt in transactions
            for proposal_id in receipt.proposal_ids
        }

        artifact_identities = tuple(
            item
            for item in full.artifact_identities
            if item.artifact_id in identity_ids
        )
        if {item.artifact_id for item in artifact_identities} != identity_ids:
            raise ControlStoreIntegrityError("snapshot_history_invalid")
        artifact_revisions = tuple(
            item
            for item in full.artifact_revisions
            if (item.artifact_id, item.revision) in revision_keys
        )
        revisions_by_artifact: dict[str, list[ArtifactRevision]] = {}
        for revision in artifact_revisions:
            revisions_by_artifact.setdefault(revision.artifact_id, []).append(revision)
        if set(revisions_by_artifact) - identity_ids:
            raise ControlStoreIntegrityError("snapshot_history_invalid")
        artifacts: list[ArtifactRecord] = []
        for identity in sorted(artifact_identities, key=lambda item: item.artifact_id):
            revisions = sorted(
                revisions_by_artifact.get(identity.artifact_id, []),
                key=lambda item: item.revision,
            )
            if [item.revision for item in revisions] != list(
                range(1, len(revisions) + 1)
            ):
                raise ControlStoreIntegrityError("snapshot_history_invalid")
            if revisions:
                latest = revisions[-1]
                artifacts.append(
                    ArtifactRecord.model_validate(
                        {
                            "schema_version": ArtifactRecord.schema_id,
                            "run_id": run_id,
                            "artifact_id": identity.artifact_id,
                            "current_revision": latest.revision,
                            "status": "valid",
                            "path": latest.path,
                            "required": identity.required,
                            "format": identity.format,
                        },
                        strict=True,
                    )
                )
            else:
                artifacts.append(
                    ArtifactRecord.model_validate(
                        {
                            "schema_version": ArtifactRecord.schema_id,
                            "run_id": run_id,
                            "artifact_id": identity.artifact_id,
                            "current_revision": 0,
                            "status": "expected",
                            "path": identity.initial_path,
                            "required": identity.required,
                            "format": identity.format,
                        },
                        strict=True,
                    )
                )

        events = tuple(item for item in full.events if item.event_id in event_ids)
        stage_transitions = tuple(
            item
            for item in full.stage_transitions
            if (item.transition_id,)
            in relation_keys("stage_transitions", ("transition_id",))
        )
        latest_stage: dict[str, StageTransitionRecord] = {}
        for transition in stage_transitions:
            prior = latest_stage.get(transition.stage_id)
            if prior is None or transition.result_revision > prior.result_revision:
                latest_stage[transition.stage_id] = transition
        stage_states = tuple(
            StageState.model_validate(
                {
                    "schema_version": StageState.schema_id,
                    "run_id": run_id,
                    "stage_id": transition.stage_id,
                    "status": transition.result_status,
                    "revision": transition.result_revision,
                    "updated_at": transition.created_at,
                },
                strict=True,
            )
            for transition in sorted(
                latest_stage.values(), key=lambda item: item.stage_id
            )
        )

        sources = tuple(item for item in full.sources if item.source_id in source_ids)
        accepted_proposals = tuple(
            item for item in full.accepted_proposals if item.proposal_id in proposal_ids
        )
        owned_artifact_submissions = tuple(
            item
            for item in full.owned_artifact_submissions
            if (item.submission_id,)
            in relation_keys("owned_artifact_submissions", ("submission_id",))
        )
        global_revision = {
            (receipt.run_id, receipt.transaction_id): receipt.committed_revision
            for receipt in self.transactions
        }
        completion_by_invocation: dict[str, tuple[int, str]] = {}
        for record in (*sources, *accepted_proposals, *owned_artifact_submissions):
            invocation_id = getattr(record, "invocation_id", None)
            created_at = getattr(record, "created_at", None)
            accepted_transaction_id = getattr(record, "accepted_transaction_id", None)
            owner_revision = global_revision.get((run_id, accepted_transaction_id))
            if (
                invocation_id is not None
                and created_at is not None
                and owner_revision is not None
            ):
                candidate = (owner_revision, created_at)
                prior = completion_by_invocation.get(invocation_id)
                if prior is None or candidate < prior:
                    completion_by_invocation[invocation_id] = candidate
        invocation_starts = {
            event.core_run_binding.primary_record_id: event
            for event in events
            if event.core_run_binding is not None
            and event.core_run_binding.effect_kind == "invocation_start"
        }
        rejections: dict[str, tuple[int, EventEnvelope]] = {}
        for event in events:
            if (
                event.intake_binding is None
                or event.intake_binding.outcome != "rejected"
            ):
                continue
            owner_revision = global_revision.get((run_id, event.transaction_id))
            if owner_revision is None:
                continue
            invocation_id = event.intake_binding.invocation_id
            candidate = (owner_revision, event)
            prior = rejections.get(invocation_id)
            if prior is None or candidate[0] < prior[0]:
                rejections[invocation_id] = candidate
        invocations: list[Invocation] = []
        for invocation_id, start in sorted(invocation_starts.items()):
            source = next(
                item for item in full.invocations if item.invocation_id == invocation_id
            )
            payload = source.model_dump(mode="json", exclude_unset=False)
            if invocation_id in completion_by_invocation:
                payload.update(
                    status="completed",
                    completed_at=completion_by_invocation[invocation_id][1],
                    failure_reason=None,
                )
            elif invocation_id in rejections:
                rejected = rejections[invocation_id][1]
                payload.update(
                    status="failed",
                    completed_at=rejected.created_at,
                    failure_reason=rejected.intake_binding.reason_code,
                )
            else:
                payload.update(status="active", completed_at=None, failure_reason=None)
            payload["started_at"] = start.created_at
            invocations.append(Invocation.model_validate(payload, strict=True))

        def selected(name: str, fields: tuple[str, ...], rows: tuple[object, ...]):
            keys = relation_keys(name, fields)
            return tuple(
                row
                for row in rows
                if tuple(getattr(row, field) for field in fields) in keys
            )

        run_contract_bindings = selected(
            "run_contract_bindings", ("run_id",), full.run_contract_bindings
        )
        run_execution_authorizations = selected(
            "run_execution_authorizations",
            ("authorization_id",),
            full.run_execution_authorizations,
        )
        run_source_discovery_authorizations = selected(
            "run_source_discovery_authorizations",
            ("authorization_id",),
            full.run_source_discovery_authorizations,
        )
        run_source_acquisition_attempt_authorizations = selected(
            "run_source_acquisition_attempt_authorizations",
            ("attempt_authorization_id",),
            full.run_source_acquisition_attempt_authorizations,
        )
        committed_transaction_ids = {receipt.transaction_id for receipt in transactions}
        runtime_source_search_plans = tuple(
            item
            for item in full.runtime_source_search_plans
            if item.accepted_transaction_id in committed_transaction_ids
        )
        tavily_acquisition_bundle_records = tuple(
            item
            for item in full.tavily_acquisition_bundle_records
            if item.accepted_transaction_id in committed_transaction_ids
        )
        stage_artifact_bindings = selected(
            "stage_artifact_bindings",
            ("transition_id", "position"),
            full.stage_artifact_bindings,
        )
        stage_gate_bindings = selected(
            "stage_gate_bindings",
            ("transition_id", "gate_id"),
            full.stage_gate_bindings,
        )
        claims = selected("claims", ("claim_id",), full.claims)
        claim_source_bindings = selected(
            "claim_source_bindings",
            ("claim_id", "source_id"),
            full.claim_source_bindings,
        )
        claim_freezes = selected("claim_freezes", ("freeze_id",), full.claim_freezes)
        gate_evaluations = selected(
            "gate_evaluations", ("evaluation_id",), full.gate_evaluations
        )
        gate_findings = selected(
            "gate_findings",
            ("evaluation_id", "finding_id"),
            full.gate_findings,
        )
        gate_artifact_bindings = selected(
            "gate_artifact_bindings",
            ("evaluation_id", "position"),
            full.gate_artifact_bindings,
        )
        run_integrity_records = selected(
            "run_integrity_records",
            ("integrity_revision",),
            full.run_integrity_records,
        )
        repair_cycles = selected("repair_cycles", ("repair_id",), full.repair_cycles)
        gate_repair_cycles = selected(
            "gate_repair_cycles",
            ("gate_repair_id",),
            full.gate_repair_cycles,
        )
        gate_repair_artifact_bindings = selected(
            "gate_repair_artifact_bindings",
            ("gate_repair_id",),
            full.gate_repair_artifact_bindings,
        )
        gate_repair_outcomes = selected(
            "gate_repair_outcomes",
            ("outcome_id",),
            full.gate_repair_outcomes,
        )
        artifact_supersessions = selected(
            "artifact_supersessions",
            ("supersession_id",),
            full.artifact_supersessions,
        )
        repair_completions = selected(
            "repair_completions",
            ("repair_completion_id",),
            full.repair_completions,
        )
        recovery_completions = selected(
            "recovery_completions", ("recovery_id",), full.recovery_completions
        )
        run_head_transitions = selected(
            "run_head_transitions",
            ("head_transition_id",),
            full.run_head_transitions,
        )
        finalize_renders = selected(
            "finalize_renders", ("render_id",), full.finalize_renders
        )
        finalizations = selected(
            "finalizations", ("finalization_id",), full.finalizations
        )
        run_archives = selected("run_archives", ("archive_id",), full.run_archives)
        run_archive_artifact_bindings = selected(
            "run_archive_artifact_bindings",
            ("archive_id", "position"),
            full.run_archive_artifact_bindings,
        )
        package_ready_records = selected(
            "package_ready_records", ("package_id",), full.package_ready_records
        )
        package_artifact_bindings = selected(
            "package_artifact_bindings",
            ("package_id", "position"),
            full.package_artifact_bindings,
        )
        approvals = selected("approvals", ("approval_id",), full.approvals)
        approval_package_bindings = selected(
            "approval_package_bindings",
            ("approval_id", "package_id"),
            full.approval_package_bindings,
        )
        delivery_authorizations = selected(
            "delivery_authorizations",
            ("authorization_id",),
            full.delivery_authorizations,
        )
        delivery_attempts = selected(
            "delivery_attempts", ("attempt_id",), full.delivery_attempts
        )
        delivery_results = selected(
            "delivery_results", ("result_id",), full.delivery_results
        )
        post_final_assessment_policy_revisions = selected(
            "post_final_assessment_policy_revisions",
            ("policy_revision_id",),
            full.post_final_assessment_policy_revisions,
        )
        post_final_assessment_requests = selected(
            "post_final_assessment_requests",
            ("assessment_request_id",),
            full.post_final_assessment_requests,
        )
        post_final_assessment_abandonments = selected(
            "post_final_assessment_abandonments",
            ("abandonment_id",),
            full.post_final_assessment_abandonments,
        )
        # Execution witnesses are owned by their dedicated append-only table,
        # not by a TransactionReceipt relation list.  This keeps schema17
        # receipts byte-compatible with workspaces created before the witness
        # table existed while still projecting only rows committed by this
        # historical prefix.
        post_final_assessment_executions = tuple(
            item
            for item in full.post_final_assessment_executions
            if item.run_id == run_id
            and item.accepted_transaction_id in committed_transaction_ids
        )
        post_final_assessment_results = selected(
            "post_final_assessment_results",
            ("assessment_result_id",),
            full.post_final_assessment_results,
        )
        post_final_finding_dispositions = selected(
            "post_final_finding_dispositions",
            ("disposition_id",),
            full.post_final_finding_dispositions,
        )
        post_final_human_observations = selected(
            "post_final_human_observations",
            ("observation_id",),
            full.post_final_human_observations,
        )
        post_final_guidance_drafts = selected(
            "post_final_guidance_drafts",
            ("guidance_id", "draft_revision"),
            full.post_final_guidance_drafts,
        )
        post_final_guidance_statuses = selected(
            "post_final_guidance_statuses",
            ("status_revision_id",),
            full.post_final_guidance_statuses,
        )
        run_guidance_snapshots = selected(
            "run_guidance_snapshots",
            ("snapshot_id",),
            full.run_guidance_snapshots,
        )
        run_guidance_selection_decisions = selected(
            "run_guidance_selection_decisions",
            ("decision_id",),
            full.run_guidance_selection_decisions,
        )
        run_guidance_snapshot_items = selected(
            "run_guidance_snapshot_items",
            ("item_id",),
            full.run_guidance_snapshot_items,
        )
        checkout_revision_ids = {
            reference.checkout_revision_id
            for receipt in transactions
            for reference in receipt.checkout_revisions
        }
        checkout_revisions = tuple(
            item
            for item in full.checkout_revisions
            if item.checkout_revision_id in checkout_revision_ids
        )
        checkout_revision_members = tuple(
            item
            for item in full.checkout_revision_members
            if item.checkout_revision_id in checkout_revision_ids
        )
        binding_transaction_ids = {
            reference.transaction_id
            for receipt in transactions
            for reference in receipt.receipt_checkout_bindings
        }
        receipt_checkout_bindings = tuple(
            item
            for item in full.receipt_checkout_bindings
            if item.transaction_id in binding_transaction_ids
        )
        intent_revision_ids = {
            reference.checkout_revision_id
            for receipt in transactions
            for reference in receipt.checkout_publication_intents
        }
        checkout_publication_intents = tuple(
            item
            for item in full.checkout_publication_intents
            if item.identity.checkout_revision_id in intent_revision_ids
        )
        checkout_publication_members = tuple(
            item
            for item in full.checkout_publication_members
            if item.identity.checkout_revision_id in intent_revision_ids
        )
        workspace_run_head = self._workspace_head_at_revision(committed_revision)
        proposal_source_bindings = tuple(
            item
            for item in full.proposal_source_bindings
            if item.proposal_id in proposal_ids
        )
        projection = replace(
            full,
            store_revision=committed_revision,
            workspace_run_head=workspace_run_head,
            stage_states=stage_states,
            invocations=tuple(invocations),
            artifacts=tuple(artifacts),
            artifact_identities=artifact_identities,
            artifact_revisions=artifact_revisions,
            events=events,
            approvals=approvals,
            deliveries=(),
            sources=sources,
            accepted_proposals=accepted_proposals,
            proposal_source_bindings=proposal_source_bindings,
            run_contract_bindings=run_contract_bindings,
            run_execution_authorizations=run_execution_authorizations,
            run_source_discovery_authorizations=run_source_discovery_authorizations,
            run_source_acquisition_attempt_authorizations=(
                run_source_acquisition_attempt_authorizations
            ),
            runtime_source_search_plans=runtime_source_search_plans,
            tavily_acquisition_bundle_records=tavily_acquisition_bundle_records,
            owned_artifact_submissions=owned_artifact_submissions,
            stage_transitions=stage_transitions,
            stage_artifact_bindings=stage_artifact_bindings,
            stage_gate_bindings=stage_gate_bindings,
            claims=claims,
            claim_source_bindings=claim_source_bindings,
            claim_freezes=claim_freezes,
            gate_evaluations=gate_evaluations,
            gate_findings=gate_findings,
            gate_artifact_bindings=gate_artifact_bindings,
            run_integrity_records=run_integrity_records,
            repair_cycles=repair_cycles,
            gate_repair_cycles=gate_repair_cycles,
            gate_repair_artifact_bindings=gate_repair_artifact_bindings,
            gate_repair_outcomes=gate_repair_outcomes,
            artifact_supersessions=artifact_supersessions,
            repair_completions=repair_completions,
            recovery_completions=recovery_completions,
            run_head_transitions=run_head_transitions,
            finalize_renders=finalize_renders,
            finalizations=finalizations,
            run_archives=run_archives,
            run_archive_artifact_bindings=run_archive_artifact_bindings,
            package_ready_records=package_ready_records,
            package_artifact_bindings=package_artifact_bindings,
            approval_package_bindings=approval_package_bindings,
            delivery_authorizations=delivery_authorizations,
            delivery_attempts=delivery_attempts,
            delivery_results=delivery_results,
            post_final_assessment_policy_revisions=(
                post_final_assessment_policy_revisions
            ),
            post_final_assessment_requests=post_final_assessment_requests,
            post_final_assessment_abandonments=post_final_assessment_abandonments,
            post_final_assessment_executions=post_final_assessment_executions,
            post_final_assessment_results=post_final_assessment_results,
            post_final_finding_dispositions=post_final_finding_dispositions,
            post_final_human_observations=post_final_human_observations,
            post_final_guidance_drafts=post_final_guidance_drafts,
            post_final_guidance_statuses=post_final_guidance_statuses,
            run_guidance_snapshots=run_guidance_snapshots,
            run_guidance_selection_decisions=run_guidance_selection_decisions,
            run_guidance_snapshot_items=run_guidance_snapshot_items,
            checkout_revisions=checkout_revisions,
            checkout_revision_members=checkout_revision_members,
            receipt_checkout_bindings=receipt_checkout_bindings,
            checkout_publication_intents=checkout_publication_intents,
            checkout_publication_members=checkout_publication_members,
            # Ack and cleanup rows are postcommit recovery metadata without a
            # receipt-owned revision. They never enter historical prefixes.
            checkout_publication_acks=(),
            checkout_publication_cleanup_observations=(),
            transactions=transactions,
        )
        return projection

    def _workspace_head_at_revision(self, committed_revision: int) -> WorkspaceRunHead:
        receipts = self.transactions
        initial_candidates = sorted(
            (
                (receipt.committed_revision, snapshot, receipt)
                for snapshot in self.snapshots
                for receipt in snapshot.transactions
                if receipt.transaction_type == "core-v2-initialize"
            ),
            key=lambda item: item[0],
        )
        initial = None if not initial_candidates else initial_candidates[0]
        if initial is None or initial[0] > committed_revision:
            raise ControlStoreStateError("workspace_head_not_found_at_revision")
        current_run_id = initial[1].run.run_id
        updated_at = initial[1].run.created_at
        revision_by_transaction = {
            (item.run_id, item.transaction_id): item.committed_revision
            for item in receipts
        }
        transitions = sorted(
            (
                transition
                for snapshot in self.snapshots
                for transition in snapshot.run_head_transitions
                if (
                    revision_by_transaction.get(
                        (
                            transition.successor_run_id,
                            transition.accepted_transaction_id,
                        )
                    )
                    is not None
                    and revision_by_transaction[
                        (
                            transition.successor_run_id,
                            transition.accepted_transaction_id,
                        )
                    ]
                    <= committed_revision
                )
            ),
            key=lambda item: item.successor_workspace_revision,
        )
        for transition in transitions:
            current_run_id = transition.successor_run_id
            updated_at = transition.created_at
        return WorkspaceRunHead.model_validate(
            {
                "schema_version": WorkspaceRunHead.schema_id,
                "workspace_id": self.workspace_id,
                "current_run_id": current_run_id,
                "updated_at": updated_at,
            },
            strict=True,
        )


@dataclass(frozen=True)
class _GuidanceCandidateAtRevision:
    draft: PostFinalGuidanceDraftRevision
    status: PostFinalGuidanceStatusRevision | None
    result: PostFinalAssessmentResultRecord | None
    disposition: PostFinalFindingDispositionRecord | None
    source_scope: GuidanceReuseScopeV1
    reason_code: str


@dataclass(frozen=True)
class OrphanBlobScan:
    """Report-only blob inventory; it never accepts or removes a blob."""

    orphan_hashes: tuple[str, ...]
    malformed_paths: tuple[str, ...]


class SQLiteControlStore:
    """Persist typed v2 DTOs without replacing any current JSON authority."""

    def __init__(
        self,
        *,
        path: Path,
        blob_root: Path,
        connection: sqlite3.Connection,
        workspace_id: str,
        clock: Callable[[], datetime] | None = None,
        failure_hook: _FailureHook | None = None,
    ) -> None:
        self.path = path
        self.blob_root = blob_root
        self.workspace_id = workspace_id
        self._connection = connection
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._failure_hook = failure_hook
        self._lock = threading.RLock()
        self._closed = False
        # Token of the database state whose ledger graph was last verified in
        # full. Process-local and never persisted, so a fresh connection always
        # re-verifies from bytes and crash recovery is unchanged.
        self._verified_ledger_token: tuple[int, int, int] | None = None

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        *,
        workspace_id: str,
        blob_root: str | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
        _failure_hook: _FailureHook | None = None,
    ) -> "SQLiteControlStore":
        workspace_id = _validate_contract_id(workspace_id, "workspace_id_invalid")
        database_path = cls._normalize_path(path, "database_path_invalid")
        blobs = cls._blob_root_for(database_path, blob_root)
        cls._validate_database_blob_separation(database_path, blobs)
        if database_path.exists() or database_path.is_symlink():
            raise ControlStoreStateError("database_already_exists")
        blob_root_preexisting = blobs.exists()
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            if blobs.is_symlink() or (blobs.exists() and not blobs.is_dir()):
                raise ControlStoreStateError("blob_root_invalid")
            if blobs.exists() and any(blobs.iterdir()):
                raise ControlStoreStateError("blob_root_not_empty")
            blobs.mkdir(parents=True, exist_ok=True)
            _validate_blob_topology(
                blobs,
                error_code="blob_topology_invalid",
            )
            connection = sqlite3.connect(
                database_path,
                isolation_level=None,
                check_same_thread=False,
            )
        except ControlStoreError:
            raise
        except (OSError, sqlite3.Error) as exc:
            cls._remove_database_files(database_path)
            if not blob_root_preexisting:
                try:
                    blobs.rmdir()
                except OSError:
                    pass
            raise ControlStoreStateError("store_path_unavailable") from exc
        try:
            configure_connection(connection)
            initialize_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO workspaces(workspace_id, revision) VALUES (?, 0)",
                (workspace_id,),
            )
            connection.execute(
                """
                INSERT INTO transaction_receipt_compatibility_boundaries(
                    workspace_id,
                    boundary_id,
                    legacy_receipt_max_committed_revision
                ) VALUES (?, ?, 0)
                """,
                (workspace_id, _RECEIPT_COMPATIBILITY_BOUNDARY_ID),
            )
            connection.execute(
                """
                INSERT INTO source_acquisition_attempt_compatibility_boundaries(
                    workspace_id,
                    boundary_id,
                    legacy_receipt_max_committed_revision
                ) VALUES (?, ?, 0)
                """,
                (workspace_id, _SOURCE_ATTEMPT_COMPATIBILITY_BOUNDARY_ID),
            )
            connection.execute(
                """
                INSERT INTO post_final_assessment_abandonment_compatibility_boundaries(
                    workspace_id,
                    boundary_id,
                    legacy_receipt_max_committed_revision
                ) VALUES (?, ?, 0)
                """,
                (
                    workspace_id,
                    _POST_FINAL_ABANDONMENT_COMPATIBILITY_BOUNDARY_ID,
                ),
            )
            connection.commit()
        except Exception:
            connection.close()
            cls._remove_database_files(database_path)
            if not blob_root_preexisting:
                try:
                    blobs.rmdir()
                except OSError:
                    pass
            raise
        return cls(
            path=database_path,
            blob_root=blobs,
            connection=connection,
            workspace_id=workspace_id,
            clock=clock,
            failure_hook=_failure_hook,
        )

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        *,
        blob_root: str | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
        _failure_hook: _FailureHook | None = None,
    ) -> "SQLiteControlStore":
        database_path = cls._normalize_path(path, "database_path_invalid")
        if not database_path.is_file():
            raise ControlStoreStateError("database_not_found")
        blobs = cls._blob_root_for(database_path, blob_root)
        cls._validate_database_blob_separation(database_path, blobs)
        try:
            connection = sqlite3.connect(
                database_path,
                isolation_level=None,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise ControlStoreStateError("database_open_failed") from exc
        try:
            configure_connection(connection)
            verify_schema(connection)
            workspace_rows = connection.execute(
                "SELECT workspace_id FROM workspaces ORDER BY workspace_id"
            ).fetchall()
            if len(workspace_rows) != 1:
                raise ControlStoreIntegrityError("workspace_binding_invalid")
            workspace_id = _validate_contract_id(
                workspace_rows[0][0],
                "workspace_id_invalid",
            )
            _validate_blob_topology(
                blobs,
                error_code="blob_topology_invalid",
            )
            store = cls(
                path=database_path,
                blob_root=blobs,
                connection=connection,
                workspace_id=workspace_id,
                clock=clock,
                failure_hook=_failure_hook,
            )
            store._verify_all_payloads()
            return store
        except Exception:
            connection.close()
            raise

    @staticmethod
    def _normalize_path(
        path: str | os.PathLike[str],
        error_code: str,
    ) -> Path:
        try:
            value = Path(path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ControlStoreStateError(error_code) from exc
        return value

    @classmethod
    def _blob_root_for(
        cls,
        database_path: Path,
        blob_root: str | os.PathLike[str] | None,
    ) -> Path:
        if blob_root is None:
            return database_path.with_name(f"{database_path.name}.blobs")
        try:
            lexical_root = Path(blob_root).expanduser()
            if lexical_root.is_symlink():
                raise ControlStoreStateError("blob_root_invalid")
        except ControlStoreError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ControlStoreStateError("blob_root_invalid") from exc
        return cls._normalize_path(blob_root, "blob_root_invalid")

    @staticmethod
    def _validate_database_blob_separation(
        database_path: Path,
        blob_root: Path,
    ) -> None:
        if database_path == blob_root or database_path.is_relative_to(blob_root):
            raise ControlStoreStateError("database_blob_paths_overlap")

    @staticmethod
    def _remove_database_files(database_path: Path) -> None:
        for path in (
            database_path,
            database_path.with_name(f"{database_path.name}-wal"),
            database_path.with_name(f"{database_path.name}-shm"),
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def __enter__(self) -> "SQLiteControlStore":
        self._require_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise ControlStoreStateError("store_closed")

    def _inject(self, stage: str) -> None:
        if self._failure_hook is not None:
            self._failure_hook(stage)

    @property
    def current_revision(self) -> int:
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT revision FROM workspaces WHERE workspace_id = ?",
                (self.workspace_id,),
            ).fetchone()
            if row is None or type(row[0]) is not int or row[0] < 0:
                raise ControlStoreIntegrityError("workspace_revision_invalid")
            return int(row[0])

    def begin(
        self,
        run_id: str,
        transaction_id: str,
        transaction_type: str,
        expected_revision: int,
    ) -> "ControlUnitOfWork":
        self._require_open()
        run_id = _validate_contract_id(run_id, "transaction_identity_invalid")
        transaction_id = _validate_contract_id(
            transaction_id,
            "transaction_identity_invalid",
        )
        transaction_type = _validate_contract_id(
            transaction_type,
            "transaction_identity_invalid",
        )
        if type(expected_revision) is not int or expected_revision < 0:
            raise ControlStoreIntegrityError("expected_revision_invalid")
        from multi_agent_brief.control_store.uow import ControlUnitOfWork

        return ControlUnitOfWork(
            self,
            run_id=run_id,
            transaction_id=transaction_id,
            transaction_type=transaction_type,
            expected_revision=expected_revision,
        )

    def load_checkout_publication(
        self,
        identity: "PublicationIdentityV1",
    ) -> tuple[
        CheckoutPublicationIntent,
        tuple[CheckoutPublicationMember, ...],
        tuple[CheckoutPublicationAck, ...],
        tuple[CheckoutPublicationCleanupObservation, ...],
    ]:
        """Load one exact non-business recovery graph by its full identity."""

        from multi_agent_brief.contracts.v2 import PublicationIdentityV1

        if type(identity) is not PublicationIdentityV1:
            raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
        with self._lock:
            self._require_open()
            snapshot = self.load_snapshot(identity.run_id)
            match = lambda item: item.identity == identity
            intents = tuple(filter(match, snapshot.checkout_publication_intents))
            if len(intents) != 1:
                raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
            return (
                intents[0],
                tuple(filter(match, snapshot.checkout_publication_members)),
                tuple(filter(match, snapshot.checkout_publication_acks)),
                tuple(
                    filter(match, snapshot.checkout_publication_cleanup_observations)
                ),
            )

    def append_checkout_publication_acks(
        self,
        records: tuple[CheckoutPublicationAck, ...],
    ) -> None:
        """Atomically append the complete intent ack set without domain writes."""

        if not records:
            raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
        identity = records[0].identity
        if any(item.identity != identity for item in records):
            raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
        with self._lock:
            intent, members, existing, _observations = self.load_checkout_publication(
                identity
            )
            if existing:
                if existing == records:
                    return
                raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
            ordered = tuple(sorted(records, key=lambda item: item.ordinal))
            if len(ordered) != intent.changed_member_count or [
                item.ordinal for item in ordered
            ] != list(range(len(members))):
                raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
            for ack, member in zip(ordered, members, strict=True):
                if (
                    ack.publication_identity_sha256
                    != intent.publication_identity_sha256
                    or ack.capability_profile_sha256 != intent.capability_profile_sha256
                    or ack.post_kind != member.post_kind
                    or ack.post_sha256 != member.post_sha256
                    or ack.post_size != member.post_size
                ):
                    raise ControlStoreIntegrityError(
                        "checkout_publication_journal_invalid"
                    )
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                for ack in ordered:
                    item = ack.identity
                    self._connection.execute(
                        "INSERT INTO checkout_publication_acks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            item.workspace_id,
                            item.run_id,
                            item.transaction_id,
                            item.checkout_revision_id,
                            ack.ordinal,
                            ack.schema_version,
                            ack.publication_identity_sha256,
                            ack.capability_profile_sha256,
                            ack.post_kind,
                            ack.post_sha256,
                            ack.post_size,
                            ack.verification,
                            ack.cleanup_policy,
                            ack.appended_at,
                            _canonical_record_text(ack),
                        ),
                    )
                self._connection.commit()
                self.load_snapshot(identity.run_id)
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_write_failed") from exc

    def append_checkout_cleanup_observations(
        self,
        records: tuple[CheckoutPublicationCleanupObservation, ...],
    ) -> None:
        """Append idempotent diagnostic residue observations after ack."""

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                for record in records:
                    identity = record.identity
                    semantic_payload = {
                        "identity": identity.model_dump(
                            mode="json", exclude_unset=False
                        ),
                        "ordinal": record.ordinal,
                        "auxiliary_role": record.auxiliary_role,
                        "reason_code": record.reason_code,
                        "observed_kind": record.observed_kind,
                        "observed_sha256": record.observed_sha256,
                        "observed_size": record.observed_size,
                    }
                    if record.cleanup_observation_id != sha256_hex(
                        canonical_json_bytes(semantic_payload)
                    ):
                        raise ControlStoreIntegrityError(
                            "checkout_publication_journal_invalid"
                        )
                    key = (
                        identity.workspace_id,
                        identity.run_id,
                        identity.transaction_id,
                        identity.checkout_revision_id,
                        record.ordinal,
                    )
                    member_row = self._connection.execute(
                        "SELECT payload_json FROM checkout_publication_members "
                        "WHERE workspace_id=? AND run_id=? AND transaction_id=? "
                        "AND checkout_revision_id=? AND ordinal=?",
                        key,
                    ).fetchone()
                    ack_row = self._connection.execute(
                        "SELECT 1 FROM checkout_publication_acks "
                        "WHERE workspace_id=? AND run_id=? AND transaction_id=? "
                        "AND checkout_revision_id=? AND ordinal=?",
                        key,
                    ).fetchone()
                    if member_row is None or ack_row is None:
                        raise ControlStoreIntegrityError(
                            "checkout_publication_journal_invalid"
                        )
                    member = _decode_record(
                        CheckoutPublicationMember,
                        str(member_row[0]),
                    )
                    expected = (
                        (member.post_kind, member.post_sha256, member.post_size)
                        if record.auxiliary_role == "temp"
                        else (member.pre_kind, member.pre_sha256, member.pre_size)
                    )
                    if expected != (
                        record.expected_kind,
                        record.expected_sha256,
                        record.expected_size,
                    ):
                        raise ControlStoreIntegrityError(
                            "checkout_publication_journal_invalid"
                        )
                    row = self._connection.execute(
                        "SELECT payload_json FROM checkout_publication_cleanup_observations WHERE cleanup_observation_id=?",
                        (record.cleanup_observation_id,),
                    ).fetchone()
                    if row is not None:
                        existing = _decode_record(
                            CheckoutPublicationCleanupObservation, str(row[0])
                        )
                        if (
                            existing.model_copy(
                                update={"appended_at": record.appended_at}
                            )
                            != record
                        ):
                            raise ControlStoreIntegrityError(
                                "checkout_publication_journal_invalid"
                            )
                        continue
                    self._connection.execute(
                        "INSERT INTO checkout_publication_cleanup_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            record.cleanup_observation_id,
                            identity.workspace_id,
                            identity.run_id,
                            identity.transaction_id,
                            identity.checkout_revision_id,
                            record.ordinal,
                            record.schema_version,
                            record.auxiliary_role,
                            record.reason_code,
                            record.expected_kind,
                            record.expected_sha256,
                            record.expected_size,
                            record.observed_kind,
                            record.observed_sha256,
                            record.observed_size,
                            record.appended_at,
                            _canonical_record_text(record),
                        ),
                    )
                self._connection.commit()
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_write_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def _existing_receipt(
        self,
        run_id: str,
        transaction_id: str,
        fingerprint: str,
    ) -> TransactionReceipt | None:
        row = self._connection.execute(
            """
            SELECT fingerprint, payload_json, committed_revision
            FROM transactions
            WHERE run_id = ? AND transaction_id = ?
            """,
            (run_id, transaction_id),
        ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise ControlStoreConflict("transaction_replay_conflict")
        receipt = _decode_record(
            TransactionReceipt,
            str(row[1]),
            receipt_committed_revision=int(row[2]),
            legacy_receipt_max_committed_revision=self._legacy_receipt_cutoff(),
            legacy_source_attempt_receipt_max_committed_revision=(
                self._legacy_source_attempt_receipt_cutoff()
            ),
            legacy_post_final_abandonment_receipt_max_committed_revision=(
                self._legacy_post_final_abandonment_receipt_cutoff()
            ),
        )
        self._verify_transaction_relations(receipt)
        self._verify_receipt_blobs(receipt)
        return receipt

    def _commit_unit_of_work(self, uow: "ControlUnitOfWork") -> TransactionReceipt:
        # Freeze the validated identity at the commit linearization point. Every
        # replay lookup, fingerprint, receipt, and revision check below uses this
        # immutable snapshot rather than rereading caller-visible UoW state.
        identity = uow._identity_snapshot()
        run_id = _validate_contract_id(
            identity.run_id,
            "transaction_identity_invalid",
        )
        transaction_id = _validate_contract_id(
            identity.transaction_id,
            "transaction_identity_invalid",
        )
        transaction_type = _validate_contract_id(
            identity.transaction_type,
            "transaction_identity_invalid",
        )
        expected_revision = identity.expected_revision
        if type(expected_revision) is not int or expected_revision < 0:
            raise ControlStoreIntegrityError("expected_revision_invalid")
        fingerprint = uow._fingerprint(identity)
        with self._lock:
            self._require_open()
            # Exact replay and new work both require a complete trusted
            # baseline. This read transaction finishes before any new blob is
            # written, so pre-existing ledger corruption cannot create another
            # orphan or be mistaken for a successful replay.
            prior = self._verify_baseline_and_existing_receipt(
                run_id,
                transaction_id,
                fingerprint,
            )
            if prior is not None:
                return prior
            if self.current_revision != expected_revision:
                raise ControlStoreConflict("store_revision_conflict")
            if uow._run is None:
                existing_run = self._connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if existing_run is None:
                    raise ControlStoreConflict("run_not_found")
            new_artifact_identities = self._preflight_artifact_subgraph(
                uow,
                run_id,
                transaction_id,
            )
            self._preflight_intake_subgraph(uow, run_id)
            self._preflight_core_run_subgraph(uow, run_id)
            self._preflight_pr4b_subgraph(uow, run_id)
            self._preflight_post_final_assessment_subgraph(uow, run_id)
            self._preflight_guidance_snapshot_subgraph(uow, run_id)
            self._preflight_checkout_subgraph(uow, run_id)
            self._inject("before_blob_write")
            for position, item in enumerate(uow._artifact_revisions, start=1):
                self._inject(f"before_blob_write:{position}")
                self._write_blob(item.record, item.content)
                self._inject(f"after_blob_write:{position}")
            self._inject("after_blob_write")
            receipt: TransactionReceipt | None = None
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._inject("after_begin")
                # Another connection may have committed after the first read
                # snapshot and before this write transaction. Recheck the
                # accepted baseline before an exact replay can return.
                verify_schema(self._connection)
                self._verify_committed_blob_bindings()
                self._verify_workspace_ledger_graph()
                replay = self._existing_receipt(
                    run_id,
                    transaction_id,
                    fingerprint,
                )
                if replay is not None:
                    self._connection.rollback()
                    return replay
                locked_revision = self._workspace_revision_in_transaction()
                if locked_revision != expected_revision:
                    raise ControlStoreConflict("store_revision_conflict")
                locked_artifact_identities = self._preflight_artifact_subgraph(
                    uow,
                    run_id,
                    transaction_id,
                )
                if locked_artifact_identities != new_artifact_identities:
                    raise ControlStoreConflict("relational_integrity_conflict")
                self._preflight_post_final_assessment_subgraph(uow, run_id)
                self._preflight_guidance_snapshot_subgraph(uow, run_id)
                committed_revision = locked_revision + 1
                receipt = self._build_receipt(
                    uow,
                    identity,
                    committed_revision,
                    locked_artifact_identities,
                )
                self._insert_run(uow._run)
                self._insert_transaction(receipt, self.workspace_id, fingerprint)
                self._upsert_workspace_run_head(uow._workspace_run_head)
                self._upsert_stage_states(uow._stage_states.values())
                self._upsert_invocations(uow._invocations.values())
                self._upsert_artifacts(uow._artifacts.values())
                self._insert_artifact_identities(locked_artifact_identities)
                self._insert_artifact_revisions(uow._artifact_revisions)
                self._insert_events(uow._events)
                self._insert_approvals(uow._approvals.values())
                self._upsert_deliveries(uow._deliveries.values())
                self._insert_sources(uow._sources.values())
                self._insert_accepted_proposals(uow._accepted_proposals.values())
                self._insert_proposal_source_bindings(
                    uow._proposal_source_bindings.values()
                )
                self._insert_run_contract_binding(uow._run_contract_binding)
                self._insert_run_execution_authorization(
                    uow._run_execution_authorization
                )
                self._insert_run_source_discovery_authorization(
                    uow._run_source_discovery_authorization
                )
                self._insert_run_source_acquisition_attempt_authorization(
                    uow._run_source_acquisition_attempt_authorization
                )
                self._insert_runtime_source_search_plans(
                    uow._runtime_source_search_plans.values()
                )
                self._insert_tavily_acquisition_bundle_records(
                    uow._tavily_acquisition_bundle_records.values()
                )
                self._insert_owned_artifact_submissions(
                    uow._owned_artifact_submissions.values()
                )
                self._insert_stage_transitions(uow._stage_transitions.values())
                self._insert_stage_artifact_bindings(
                    uow._stage_artifact_bindings.values()
                )
                self._insert_stage_gate_bindings(uow._stage_gate_bindings.values())
                self._insert_claims(uow._claims.values())
                self._insert_claim_source_bindings(uow._claim_source_bindings.values())
                self._insert_claim_freezes(uow._claim_freezes.values())
                self._insert_gate_evaluations(uow._gate_evaluations.values())
                self._insert_gate_findings(uow._gate_findings.values())
                self._insert_gate_artifact_bindings(
                    uow._gate_artifact_bindings.values()
                )
                self._insert_run_integrity_records(uow._run_integrity_records.values())
                self._insert_gate_repair_records(uow)
                self._insert_pr4b_records(uow)
                self._insert_post_final_assessment_records(uow)
                self._insert_guidance_snapshot_records(uow)
                self._insert_checkout_records(uow)
                self._insert_transaction_relations(receipt)
                self._inject("after_records")
                self._inject("before_commit")
                for item in uow._artifact_revisions:
                    self._verify_blob(item.record, self._blob_path(item.record.sha256))
                updated = self._connection.execute(
                    """
                    UPDATE workspaces SET revision = ?
                    WHERE workspace_id = ? AND revision = ?
                    """,
                    (committed_revision, self.workspace_id, locked_revision),
                )
                if updated.rowcount != 1:
                    raise ControlStoreConflict("store_revision_conflict")
                # Validate the proposed graph while all inserted rows and the
                # workspace revision remain rollback-capable in this same
                # SQLite write transaction.
                self._verify_workspace_ledger_graph()
                self._load_snapshot_in_transaction(run_id)
                self._connection.commit()
            except sqlite3.IntegrityError as exc:
                self._connection.rollback()
                raise ControlStoreConflict("relational_integrity_conflict") from exc
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_write_failed") from exc
            except Exception:
                self._connection.rollback()
                raise
            # Private test-only boundary for a real process exit after the durable
            # commit but before the caller observes the receipt.
            try:
                if receipt is None:
                    raise ControlStoreIntegrityError("transaction_receipt_missing")
                self._inject("after_commit")
                verify_schema(self._connection)
                self._verify_committed_blob_bindings(run_id=run_id)
                self._verify_workspace_ledger_graph()
                self._load_snapshot_in_transaction(run_id)
            except ControlStoreCommitOutcomeUnknown:
                raise
            except Exception as exc:
                raise ControlStoreCommitOutcomeUnknown(
                    "commit_outcome_unknown"
                ) from exc
            return receipt

    def _preflight_artifact_subgraph(
        self,
        uow: "ControlUnitOfWork",
        run_id: str,
        transaction_id: str,
    ) -> tuple[ArtifactIdentityRecord, ...]:
        """Reject deterministically unbound blob records before file writes."""

        staged_artifact_ids = set(uow._artifacts)
        staged_revision_keys = {
            (item.record.artifact_id, item.record.revision)
            for item in uow._artifact_revisions
        }
        existing_artifact_ids = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT artifact_id FROM artifacts WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        for artifact_id, revision in staged_revision_keys:
            if artifact_id not in staged_artifact_ids | existing_artifact_ids:
                raise ControlStoreConflict("relational_integrity_conflict")
            if (
                self._connection.execute(
                    """
                SELECT 1 FROM artifact_revisions
                WHERE run_id = ? AND artifact_id = ? AND revision = ?
                """,
                    (run_id, artifact_id, revision),
                ).fetchone()
                is not None
            ):
                # Exact transaction replay returned before this preflight. Any
                # remaining revision-key collision belongs to different intent.
                raise ControlStoreConflict("relational_integrity_conflict")
        new_identities: list[ArtifactIdentityRecord] = []
        staged_revisions = {
            (item.record.artifact_id, item.record.revision): item.record
            for item in uow._artifact_revisions
        }
        for record in uow._artifacts.values():
            artifact_row = self._connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND artifact_id = ?",
                (run_id, record.artifact_id),
            ).fetchone()
            identity_row = self._connection.execute(
                """
                SELECT * FROM artifact_identities
                WHERE run_id = ? AND artifact_id = ?
                """,
                (run_id, record.artifact_id),
            ).fetchone()
            if (artifact_row is None) != (identity_row is None):
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
            if artifact_row is None:
                identity = ArtifactIdentityRecord.model_validate(
                    {
                        "schema_version": ArtifactIdentityRecord.schema_id,
                        "run_id": record.run_id,
                        "artifact_id": record.artifact_id,
                        "required": record.required,
                        "initial_path": record.path,
                        "format": record.format,
                        "accepted_transaction_id": transaction_id,
                    },
                    strict=True,
                )
                new_identities.append(identity)
            else:
                existing_artifact = self._decode_artifact_record_row(artifact_row)
                existing_identity = self._decode_artifact_identity_row(identity_row)
                if (
                    existing_artifact.required != existing_identity.required
                    or existing_artifact.format != existing_identity.format
                    or record.required != existing_identity.required
                    or record.format != existing_identity.format
                ):
                    raise ControlStoreConflict("relational_integrity_conflict")
                if record.current_revision == 0 and (
                    record.path != existing_identity.initial_path
                ):
                    raise ControlStoreConflict("relational_integrity_conflict")

            if record.current_revision == 0:
                if any(
                    artifact_id == record.artifact_id
                    for artifact_id, _revision in staged_revision_keys
                ):
                    raise ControlStoreConflict("relational_integrity_conflict")
                continue
            key = (record.artifact_id, record.current_revision)
            revision_record = staged_revisions.get(key)
            if revision_record is None:
                revision_row = self._connection.execute(
                    """
                    SELECT * FROM artifact_revisions
                    WHERE run_id = ? AND artifact_id = ? AND revision = ?
                    """,
                    (run_id, record.artifact_id, record.current_revision),
                ).fetchone()
                if revision_row is None:
                    raise ControlStoreConflict("relational_integrity_conflict")
                revision_record = self._decode_checked(
                    ArtifactRevision,
                    revision_row,
                    {
                        "run_id": "run_id",
                        "artifact_id": "artifact_id",
                        "revision": "revision",
                        "schema_version": "schema_version",
                        "path": "path",
                        "sha256": "sha256",
                        "size_bytes": "size_bytes",
                        "frozen": "frozen",
                        "producer_kind": "producer_kind",
                        "producer_id": "producer_id",
                        "created_at": "created_at",
                    },
                )
            if record.path != revision_record.path:
                raise ControlStoreConflict("relational_integrity_conflict")
        return tuple(sorted(new_identities, key=lambda item: item.artifact_id))

    def _workspace_revision_in_transaction(self) -> int:
        row = self._connection.execute(
            "SELECT revision FROM workspaces WHERE workspace_id = ?",
            (self.workspace_id,),
        ).fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0:
            raise ControlStoreIntegrityError("workspace_revision_invalid")
        return int(row[0])

    def _preflight_intake_subgraph(
        self,
        uow: "ControlUnitOfWork",
        run_id: str,
    ) -> None:
        """Reject known missing intake relations before any blob promotion."""

        staged_invocations = set(uow._invocations)
        existing_invocations = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT invocation_id FROM agent_invocations WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        staged_events = {event.event_id for event in uow._events}
        staged_revisions = {
            (item.record.artifact_id, item.record.revision)
            for item in uow._artifact_revisions
        }
        existing_revisions = {
            (str(row[0]), int(row[1]))
            for row in self._connection.execute(
                """
                SELECT artifact_id, revision FROM artifact_revisions
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()
        }
        staged_sources = set(uow._sources)
        existing_sources = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT source_id FROM sources WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        staged_proposals = set(uow._accepted_proposals)
        existing_proposals = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT proposal_id FROM accepted_proposals WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        available_invocations = staged_invocations | existing_invocations
        available_revisions = staged_revisions | existing_revisions
        available_sources = staged_sources | existing_sources
        available_proposals = staged_proposals | existing_proposals

        for source in uow._sources.values():
            required_revisions = {
                (source.content_artifact_id, source.content_artifact_revision)
            }
            if source.raw_payload_artifact_id is not None:
                required_revisions.add(
                    (
                        source.raw_payload_artifact_id,
                        source.raw_payload_artifact_revision,
                    )
                )
            if (
                source.invocation_id not in available_invocations
                or source.acquisition_event_id not in staged_events
                or source.accepted_transaction_id != uow.transaction_id
                or not required_revisions <= available_revisions
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        for proposal in uow._accepted_proposals.values():
            if (
                proposal.invocation_id not in available_invocations
                or proposal.accepted_event_id not in staged_events
                or proposal.accepted_transaction_id != uow.transaction_id
                or (proposal.artifact_id, proposal.artifact_revision)
                not in available_revisions
                or (
                    proposal.parent_proposal_id is not None
                    and proposal.parent_proposal_id not in available_proposals
                )
                or (
                    proposal.target_artifact_id is not None
                    and (
                        proposal.target_artifact_id,
                        proposal.target_artifact_revision,
                    )
                    not in available_revisions
                )
                or not set(proposal.source_ids) <= available_sources
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        binding_keys = {
            (record.proposal_id, record.source_id)
            for record in uow._proposal_source_bindings.values()
        }
        expected_binding_keys = {
            (proposal.proposal_id, source_id)
            for proposal in uow._accepted_proposals.values()
            for source_id in proposal.source_ids
        }
        if binding_keys != expected_binding_keys:
            raise ControlStoreConflict("relational_integrity_conflict")
        if any(
            proposal_id not in available_proposals or source_id not in available_sources
            for proposal_id, source_id in binding_keys
        ):
            raise ControlStoreConflict("relational_integrity_conflict")

    def _preflight_core_run_subgraph(
        self,
        uow: "ControlUnitOfWork",
        run_id: str,
    ) -> None:
        """Reject structurally unbound PR-4A rows before blob promotion."""

        staged_revisions = {
            (item.record.artifact_id, item.record.revision)
            for item in uow._artifact_revisions
        }
        existing_revisions = {
            (str(row[0]), int(row[1]))
            for row in self._connection.execute(
                "SELECT artifact_id, revision FROM artifact_revisions WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        available_revisions = staged_revisions | existing_revisions
        staged_events = {event.event_id for event in uow._events}
        available_invocations = set(uow._invocations) | {
            str(row[0])
            for row in self._connection.execute(
                "SELECT invocation_id FROM agent_invocations WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        available_proposals = set(uow._accepted_proposals) | {
            str(row[0])
            for row in self._connection.execute(
                "SELECT proposal_id FROM accepted_proposals WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        available_sources = set(uow._sources) | {
            str(row[0])
            for row in self._connection.execute(
                "SELECT source_id FROM sources WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        available_transitions = set(uow._stage_transitions) | {
            str(row[0])
            for row in self._connection.execute(
                "SELECT transition_id FROM stage_transitions WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        available_evaluations = set(uow._gate_evaluations) | {
            str(row[0])
            for row in self._connection.execute(
                "SELECT evaluation_id FROM gate_evaluations WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }

        binding = uow._run_contract_binding
        existing_binding_row = self._connection.execute(
            """
            SELECT contract_fingerprint
            FROM run_contract_bindings
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        binding_fingerprint = (
            binding.contract_fingerprint
            if binding is not None
            else (
                None if existing_binding_row is None else str(existing_binding_row[0])
            )
        )
        if binding is not None:
            refs = {
                (
                    binding.stage_specs_artifact.artifact_id,
                    binding.stage_specs_artifact.revision,
                ),
                (
                    binding.artifact_contracts_artifact.artifact_id,
                    binding.artifact_contracts_artifact.revision,
                ),
                (
                    binding.policy_pack_artifact.artifact_id,
                    binding.policy_pack_artifact.revision,
                ),
            }
            if (
                binding.accepted_transaction_id != uow.transaction_id
                or binding.initialization_event_id not in staged_events
                or not refs <= available_revisions
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        execution_authorization = uow._run_execution_authorization
        if execution_authorization is not None:
            if (
                execution_authorization.accepted_transaction_id != uow.transaction_id
                or execution_authorization.authorization_event_id not in staged_events
                or (
                    execution_authorization.source_manifest_artifact.artifact_id,
                    execution_authorization.source_manifest_artifact.revision,
                )
                not in available_revisions
                or execution_authorization.run_contract_fingerprint
                != binding_fingerprint
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        source_discovery_authorization = uow._run_source_discovery_authorization
        if source_discovery_authorization is not None:
            if (
                source_discovery_authorization.accepted_transaction_id
                != uow.transaction_id
                or source_discovery_authorization.authorization_event_id
                not in staged_events
                or binding is None
                or source_discovery_authorization.run_contract_fingerprint
                != binding.contract_fingerprint
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for (
            authorization_id,
            referenced,
        ) in uow._referenced_source_discovery_authorizations.items():
            if source_discovery_authorization is not None and (
                authorization_id == source_discovery_authorization.authorization_id
            ):
                continue
            row = self._connection.execute(
                "SELECT payload_json FROM run_source_discovery_authorizations "
                "WHERE run_id=? AND authorization_id=?",
                (run_id, authorization_id),
            ).fetchone()
            if (
                row is None
                or _decode_record(RunSourceDiscoveryAuthorization, str(row[0]))
                != referenced
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        attempt_authorization = uow._run_source_acquisition_attempt_authorization
        if attempt_authorization is not None:
            discovery_record = source_discovery_authorization
            if discovery_record is None:
                discovery_row = self._connection.execute(
                    "SELECT payload_json FROM run_source_discovery_authorizations "
                    "WHERE run_id=? AND authorization_id=?",
                    (
                        run_id,
                        attempt_authorization.discovery_authorization_id,
                    ),
                ).fetchone()
                discovery_record = (
                    None
                    if discovery_row is None
                    else _decode_record(
                        RunSourceDiscoveryAuthorization,
                        str(discovery_row[0]),
                    )
                )
            previous_rows = self._connection.execute(
                """
                SELECT payload_json
                FROM run_source_acquisition_attempt_authorizations_v2
                WHERE run_id=?
                ORDER BY attempt_ordinal
                """,
                (run_id,),
            ).fetchall()
            previous = [
                _decode_record(
                    RunSourceAcquisitionAttemptAuthorization,
                    str(row[0]),
                )
                for row in previous_rows
            ]
            expected_ordinal = len(previous) + 1
            expected_previous = (
                None if not previous else previous[-1].attempt_authorization_id
            )
            if (
                attempt_authorization.accepted_transaction_id != uow.transaction_id
                or attempt_authorization.authorization_event_id not in staged_events
                or discovery_record is None
                or attempt_authorization.discovery_authorization_id
                != discovery_record.authorization_id
                or attempt_authorization.workspace_id != discovery_record.workspace_id
                or attempt_authorization.run_contract_fingerprint
                != discovery_record.run_contract_fingerprint
                or attempt_authorization.run_direction_fingerprint
                != discovery_record.run_direction_fingerprint
                or attempt_authorization.runtime_source_plan_fingerprint
                != discovery_record.runtime_source_plan_fingerprint
                or attempt_authorization.source_route_fingerprint
                != discovery_record.source_route_fingerprint
                or attempt_authorization.provider_id != discovery_record.provider_id
                or attempt_authorization.route_id != discovery_record.route_id
                or attempt_authorization.attempt_ordinal != expected_ordinal
                or attempt_authorization.previous_attempt_authorization_id
                != expected_previous
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for (
            attempt_id,
            referenced_attempt,
        ) in uow._referenced_source_acquisition_attempt_authorizations.items():
            if attempt_authorization is not None and (
                attempt_id == attempt_authorization.attempt_authorization_id
            ):
                continue
            row = self._connection.execute(
                "SELECT payload_json "
                "FROM run_source_acquisition_attempt_authorizations_v2 "
                "WHERE run_id=? AND attempt_authorization_id=?",
                (run_id, attempt_id),
            ).fetchone()
            if (
                row is None
                or _decode_record(
                    RunSourceAcquisitionAttemptAuthorization,
                    str(row[0]),
                )
                != referenced_attempt
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        for record in uow._owned_artifact_submissions.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.accepted_event_id not in staged_events
                or (record.artifact_id, record.artifact_revision)
                not in available_revisions
                or (
                    record.invocation_id is not None
                    and record.invocation_id not in available_invocations
                )
                or (
                    record.parent_artifact is not None
                    and (
                        record.parent_artifact.artifact_id,
                        record.parent_artifact.revision,
                    )
                    not in available_revisions
                )
                or (
                    record.source_proposal_id is not None
                    and record.source_proposal_id not in available_proposals
                )
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        for record in uow._stage_transitions.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.transition_event_id not in staged_events
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for record in uow._stage_artifact_bindings.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.transition_id not in available_transitions
                or (record.artifact_id, record.artifact_revision)
                not in available_revisions
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for record in uow._stage_gate_bindings.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.transition_id not in available_transitions
                or record.evaluation_id not in available_evaluations
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        core_run_effect = any(
            (
                uow._run_contract_binding is not None,
                uow._run_execution_authorization is not None,
                uow._run_source_discovery_authorization is not None,
                bool(uow._owned_artifact_submissions),
                bool(uow._stage_transitions),
                bool(uow._claims),
                bool(uow._claim_freezes),
                bool(uow._gate_evaluations),
                bool(uow._run_integrity_records),
                bool(uow._gate_repair_cycles),
                bool(uow._gate_repair_artifact_bindings),
                bool(uow._gate_repair_outcomes),
            )
        )
        if core_run_effect:
            for record in uow._stage_states.values():
                if not any(
                    transition.stage_id == record.stage_id
                    and transition.result_revision == record.revision
                    and transition.result_status == record.status
                    for transition in uow._stage_transitions.values()
                ):
                    raise ControlStoreConflict("relational_integrity_conflict")

        for record in uow._claims.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.claim_drafts_proposal_id not in available_proposals
                or record.primary_source_id not in available_sources
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        available_claims = set(uow._claims) | {
            str(row[0])
            for row in self._connection.execute(
                "SELECT claim_id FROM claims WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        for record in uow._claim_source_bindings.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.claim_id not in available_claims
                or record.source_id not in available_sources
                or record.claim_drafts_proposal_id not in available_proposals
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for record in uow._claim_freezes.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.freeze_event_id not in staged_events
                or not {
                    record.claim_drafts_proposal_id,
                    record.screened_proposal_id,
                    record.candidate_proposal_id,
                }
                <= available_proposals
                or (
                    record.claim_drafts_artifact.artifact_id,
                    record.claim_drafts_artifact.revision,
                )
                not in available_revisions
                or (record.ledger_artifact.artifact_id, record.ledger_artifact.revision)
                not in available_revisions
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        for record in uow._gate_evaluations.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.evaluation_event_id not in staged_events
                or (record.report_artifact.artifact_id, record.report_artifact.revision)
                not in available_revisions
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for record in uow._gate_findings.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.evaluation_id not in available_evaluations
                or (
                    record.claim_id is not None
                    and record.claim_id not in available_claims
                )
                or (
                    record.source_id is not None
                    and record.source_id not in available_sources
                )
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for record in uow._gate_artifact_bindings.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.evaluation_id not in available_evaluations
                or (record.artifact_id, record.artifact_revision)
                not in available_revisions
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        for record in uow._run_integrity_records.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or (
                    record.first_detected_event_id is not None
                    and record.first_detected_event_id not in staged_events
                )
                or (
                    record.affected_artifact_id is not None
                    and (
                        record.affected_artifact_id,
                        record.affected_artifact_revision,
                    )
                    not in available_revisions
                )
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        self._preflight_gate_repair_subgraph(
            uow,
            run_id,
            staged_events=staged_events,
            available_revisions=available_revisions,
            available_evaluations=available_evaluations,
            available_transitions=available_transitions,
        )

    def _preflight_gate_repair_subgraph(
        self,
        uow: "ControlUnitOfWork",
        run_id: str,
        *,
        staged_events: set[str],
        available_revisions: set[tuple[str, int]],
        available_evaluations: set[str],
        available_transitions: set[str],
    ) -> None:
        """Validate ownership links for the distinct bounded Gate-repair graph."""

        staged_cycles = set(uow._gate_repair_cycles)
        existing_cycles = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT gate_repair_id FROM gate_repair_cycles WHERE run_id=?",
                (run_id,),
            ).fetchall()
        }
        existing_authorizations = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT authorization_id FROM run_execution_authorizations WHERE run_id=?",
                (run_id,),
            ).fetchall()
        }
        available_findings = {
            (str(row[0]), str(row[1]))
            for row in self._connection.execute(
                "SELECT evaluation_id,finding_id FROM gate_findings WHERE run_id=?",
                (run_id,),
            ).fetchall()
        } | set(uow._gate_findings)
        available_submissions = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT submission_id FROM owned_artifact_submissions WHERE run_id=?",
                (run_id,),
            ).fetchall()
        } | set(uow._owned_artifact_submissions)
        for record in uow._gate_repair_cycles.values():
            finding_keys = {
                (item.evaluation_id, item.finding_id)
                for item in record.blocking_findings
            }
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.start_event_id not in staged_events
                or record.authorization_id not in existing_authorizations
                or not set(record.blocking_evaluation_ids) <= available_evaluations
                or not finding_keys <= available_findings
                or not set(record.reopened_transition_ids) <= available_transitions
                or (
                    record.target_artifact.artifact_id,
                    record.target_artifact.revision,
                )
                not in available_revisions
                or existing_cycles
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for record in uow._gate_repair_artifact_bindings.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.accepted_event_id not in staged_events
                or record.gate_repair_id not in (staged_cycles | existing_cycles)
                or record.owned_artifact_submission_id not in available_submissions
                or (
                    record.prior_artifact.artifact_id,
                    record.prior_artifact.revision,
                )
                not in available_revisions
                or (
                    record.successor_artifact.artifact_id,
                    record.successor_artifact.revision,
                )
                not in available_revisions
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for record in uow._gate_repair_outcomes.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.completion_event_id not in staged_events
                or record.gate_repair_id not in (staged_cycles | existing_cycles)
                or not set(record.evaluation_ids) <= available_evaluations
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

    def _preflight_pr4b_subgraph(self, uow: "ControlUnitOfWork", run_id: str) -> None:
        """Validate structural ownership only; domain legality stays in services."""

        staged_events = {event.event_id for event in uow._events}
        records: tuple[StrictModel, ...] = (
            *uow._repair_cycles.values(),
            *uow._artifact_supersessions.values(),
            *uow._repair_completions.values(),
            *uow._recovery_completions.values(),
            *uow._finalize_renders.values(),
            *uow._finalizations.values(),
            *uow._run_archives.values(),
            *uow._run_archive_artifact_bindings.values(),
            *uow._package_ready_records.values(),
            *uow._package_artifact_bindings.values(),
            *uow._approval_package_bindings.values(),
            *uow._delivery_authorizations.values(),
            *uow._delivery_attempts.values(),
            *uow._delivery_results.values(),
        )
        for record in records:
            if (
                getattr(record, "run_id", None) != run_id
                or getattr(record, "accepted_transaction_id", None)
                != uow.transaction_id
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for transition in uow._run_head_transitions.values():
            if (
                transition.successor_run_id != run_id
                or transition.accepted_transaction_id != uow.transaction_id
                or transition.workspace_id != self.workspace_id
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
            current = self.load_workspace_run_head()
            if (
                current is None
                or current.current_run_id != transition.predecessor_run_id
                or transition.prior_workspace_revision != uow.expected_revision
                or uow._run is None
                or uow._workspace_run_head is None
            ):
                raise ControlStoreConflict("workspace_run_head_conflict")
        event_fields = (
            (uow._repair_cycles.values(), "start_event_id"),
            (uow._artifact_supersessions.values(), "accepted_event_id"),
            (uow._repair_completions.values(), "completion_event_id"),
            (uow._recovery_completions.values(), "completion_event_id"),
            (uow._run_head_transitions.values(), "transition_event_id"),
            (uow._finalize_renders.values(), "render_event_id"),
            (uow._finalizations.values(), "finalization_event_id"),
            (uow._run_archives.values(), "archive_event_id"),
            (uow._package_ready_records.values(), "package_event_id"),
            (uow._delivery_authorizations.values(), "authorization_event_id"),
            (uow._delivery_attempts.values(), "attempt_event_id"),
            (uow._delivery_results.values(), "result_event_id"),
        )
        for values, field in event_fields:
            for record in values:
                if getattr(record, field) not in staged_events:
                    raise ControlStoreConflict("relational_integrity_conflict")
        for completion in uow._repair_completions.values():
            if set(completion.supersession_ids) != set(
                uow._artifact_supersessions
            ) and not all(
                self._connection.execute(
                    "SELECT 1 FROM artifact_supersessions WHERE run_id=? AND supersession_id=?",
                    (run_id, item),
                ).fetchone()
                for item in completion.supersession_ids
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
            if len(completion.reopened_transition_ids) != len(
                set(completion.reopened_transition_ids)
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        for render in uow._finalize_renders.values():
            if not render.reader_artifacts:
                raise ControlStoreConflict("relational_integrity_conflict")
        for archive in uow._run_archives.values():
            bindings = [
                item
                for item in uow._run_archive_artifact_bindings.values()
                if item.archive_id == archive.archive_id
            ]
            if len(bindings) != archive.included_count or sorted(
                item.position for item in bindings
            ) != list(range(len(bindings))):
                raise ControlStoreConflict("relational_integrity_conflict")
        for package in uow._package_ready_records.values():
            bindings = [
                item
                for item in uow._package_artifact_bindings.values()
                if item.package_id == package.package_id
            ]
            if len(bindings) != package.artifact_count or sorted(
                item.position for item in bindings
            ) != list(range(len(bindings))):
                raise ControlStoreConflict("relational_integrity_conflict")

    def _guidance_candidates_at_revision(
        self,
        *,
        cutoff_revision: int,
        successor_binding: RunContractBinding,
        reuse_requested: bool,
        exclude_run_id: str,
    ) -> tuple[_GuidanceCandidateAtRevision, ...]:
        """Recompute the complete deterministic candidate set as of one Receipt."""

        if type(cutoff_revision) is not int or cutoff_revision < 0:
            raise ControlStoreIntegrityError("control_store_integrity_invalid")
        source_snapshots: dict[str, ControlStoreSnapshot] = {}
        for row in self._connection.execute(
            "SELECT run_id FROM runs WHERE workspace_id=? ORDER BY created_at,run_id",
            (self.workspace_id,),
        ).fetchall():
            source_run_id = str(row[0])
            if source_run_id == exclude_run_id:
                continue
            source_snapshots[source_run_id] = self._load_snapshot_in_transaction(
                source_run_id,
                _verify_guidance=False,
            )

        receipt_revisions = {
            (source.run.run_id, receipt.transaction_id): receipt.committed_revision
            for source in source_snapshots.values()
            for receipt in source.transactions
        }

        def existed_at_cutoff(record: StrictModel) -> bool:
            accepted_transaction_id = getattr(
                record,
                "accepted_transaction_id",
                None,
            )
            revision = receipt_revisions.get(
                (str(getattr(record, "run_id", "")), accepted_transaction_id)
            )
            return revision is not None and revision <= cutoff_revision

        latest_drafts: dict[tuple[str, str], PostFinalGuidanceDraftRevision] = {}
        for source in source_snapshots.values():
            for draft in source.post_final_guidance_drafts:
                if not existed_at_cutoff(draft):
                    continue
                key = (draft.run_id, draft.guidance_id)
                prior = latest_drafts.get(key)
                if prior is None or draft.draft_revision > prior.draft_revision:
                    latest_drafts[key] = draft

        run_order = {
            source.run.run_id: (source.run.created_at, source.run.run_id)
            for source in source_snapshots.values()
        }
        successor_scope = _guidance_reuse_scope(successor_binding)
        candidates: list[_GuidanceCandidateAtRevision] = []
        for draft in sorted(
            latest_drafts.values(),
            key=lambda item: (
                run_order.get(item.run_id, ("", item.run_id)),
                item.guidance_id,
                item.draft_revision,
            ),
        ):
            source = source_snapshots.get(draft.run_id)
            if source is None or len(source.run_contract_bindings) != 1:
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            source_scope = _guidance_reuse_scope(source.run_contract_bindings[0])
            if draft.provenance_kind == "human_observation":
                observations = tuple(
                    item
                    for item in source.post_final_human_observations
                    if item.observation_id == draft.observation_id
                    and existed_at_cutoff(item)
                )
                if len(observations) != 1:
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
                observation = observations[0]
                result = None
                if draft.assessment_result_id is not None:
                    result_rows = tuple(
                        item
                        for item in source.post_final_assessment_results
                        if item.assessment_result_id == draft.assessment_result_id
                        and existed_at_cutoff(item)
                    )
                    if len(result_rows) != 1:
                        raise ControlStoreIntegrityError(
                            "control_store_integrity_invalid"
                        )
                    result = result_rows[0]
                if (
                    observation.observation_fingerprint != draft.observation_fingerprint
                    or observation.finalized_lineage_fingerprint
                    != draft.finalized_lineage_fingerprint
                    or (
                        result is not None
                        and (
                            observation.assessment_result_id
                            != result.assessment_result_id
                            or observation.assessment_result_fingerprint
                            != result.result_fingerprint
                        )
                    )
                ):
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
                disposition = None
                current_disposition = None
                observation_successor = any(
                    item.previous_observation_id == observation.observation_id
                    and existed_at_cutoff(item)
                    for item in source.post_final_human_observations
                )
            else:
                results = tuple(
                    item
                    for item in source.post_final_assessment_results
                    if item.assessment_result_id == draft.assessment_result_id
                    and existed_at_cutoff(item)
                )
                dispositions = tuple(
                    item
                    for item in source.post_final_finding_dispositions
                    if item.disposition_id == draft.disposition_id
                    and existed_at_cutoff(item)
                )
                if len(results) != 1 or len(dispositions) != 1:
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
                result = results[0]
                disposition = dispositions[0]
                if (
                    disposition.decision != "accept"
                    or disposition.run_id != draft.run_id
                    or disposition.assessment_result_id != draft.assessment_result_id
                    or disposition.assessment_result_fingerprint
                    != draft.assessment_result_fingerprint
                    or disposition.finding_id != draft.finding_id
                    or disposition.finding_fingerprint != draft.finding_fingerprint
                    or disposition.disposition_fingerprint
                    != draft.disposition_fingerprint
                    or result.run_id != draft.run_id
                    or result.result_fingerprint != draft.assessment_result_fingerprint
                    or result.finalized_lineage_fingerprint
                    != draft.finalized_lineage_fingerprint
                ):
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
                try:
                    current_disposition = _current_post_final_disposition_at_cutoff(
                        tuple(source.post_final_finding_dispositions),
                        receipt_revisions=receipt_revisions,
                        run_id=draft.run_id,
                        assessment_result_id=draft.assessment_result_id,
                        finding_id=draft.finding_id,
                        cutoff_revision=cutoff_revision,
                    )
                except ValueError as exc:
                    raise ControlStoreIntegrityError(
                        "control_store_integrity_invalid"
                    ) from exc
                observation_successor = False
            statuses = tuple(
                item
                for item in source.post_final_guidance_statuses
                if item.guidance_id == draft.guidance_id and existed_at_cutoff(item)
            )
            current_status = (
                None
                if not statuses
                else max(
                    statuses,
                    key=lambda item: receipt_revisions[
                        (item.run_id, item.accepted_transaction_id)
                    ],
                )
            )
            if current_status is not None and (
                current_status.run_id != draft.run_id
                or current_status.guidance_sha256 != draft.guidance_sha256
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            if not reuse_requested:
                reason = "reuse_not_requested"
            elif (
                current_status is None
                or current_status.draft_revision != draft.draft_revision
            ):
                reason = "guidance_unapproved"
            elif current_status.status in {"deactivated", "reverted"}:
                reason = "guidance_inactive"
            elif current_status.status == "superseded":
                reason = "guidance_superseded"
            elif current_status.status != "approved":
                reason = "guidance_unapproved"
            elif draft.provenance_kind == "human_observation" and observation_successor:
                reason = "guidance_superseded"
            elif draft.provenance_kind == "human_observation" and observation is None:
                reason = "guidance_unapproved"
            elif draft.provenance_kind != "human_observation" and (
                current_disposition is None
                or current_disposition.disposition_id != draft.disposition_id
                or current_disposition.decision != "accept"
            ):
                reason = "guidance_unapproved"
            elif source_scope.scope_fingerprint != successor_scope.scope_fingerprint:
                reason = "guidance_scope_mismatch"
            else:
                reason = "approved_scope_match"
            candidates.append(
                _GuidanceCandidateAtRevision(
                    draft=draft,
                    status=current_status,
                    result=result,
                    disposition=disposition,
                    source_scope=source_scope,
                    reason_code=reason,
                )
            )
        return tuple(candidates)

    def _guidance_snapshot_content_is_exact(
        self,
        *,
        snapshot: RunGuidanceSnapshotRecord,
        decisions: tuple[RunGuidanceSelectionDecisionRecord, ...],
        items: tuple[RunGuidanceSnapshotItemRecord, ...],
        successor_binding: RunContractBinding,
        cutoff_revision: int,
    ) -> bool:
        successor_scope = _guidance_reuse_scope(successor_binding)
        if (
            snapshot.successor_run_contract_fingerprint
            != successor_binding.contract_fingerprint
            or snapshot.successor_direction_fingerprint
            != canonical_fingerprint(
                successor_binding.run_direction.model_dump(mode="json")
            )
        ):
            return False
        candidates = self._guidance_candidates_at_revision(
            cutoff_revision=cutoff_revision,
            successor_binding=successor_binding,
            reuse_requested=snapshot.reuse_requested,
            exclude_run_id=snapshot.run_id,
        )
        candidate_payloads = [
            {
                "source_run_id": candidate.draft.run_id,
                "guidance_id": candidate.draft.guidance_id,
                "draft_revision": candidate.draft.draft_revision,
                "draft_fingerprint": candidate.draft.draft_fingerprint,
                "provenance_kind": candidate.draft.provenance_kind,
                "observation_id": candidate.draft.observation_id,
                "observation_fingerprint": candidate.draft.observation_fingerprint,
                "status_revision_id": (
                    None
                    if candidate.status is None
                    else candidate.status.status_revision_id
                ),
                "status_fingerprint": (
                    None
                    if candidate.status is None
                    else candidate.status.status_fingerprint
                ),
                "source_scope_fingerprint": (candidate.source_scope.scope_fingerprint),
                "reason_code": candidate.reason_code,
            }
            for candidate in candidates
        ]
        if snapshot.candidate_set_fingerprint != canonical_fingerprint(
            {"candidates": candidate_payloads}
        ):
            return False
        decision_by_id = {item.decision_id: item for item in decisions}
        item_by_id = {item.item_id: item for item in items}
        if (
            len(decision_by_id) != len(decisions)
            or len(item_by_id) != len(items)
            or set(decision_by_id) != set(snapshot.decision_ids)
            or set(item_by_id) != set(snapshot.selected_item_ids)
            or len(snapshot.decision_ids) != len(candidates)
        ):
            return False
        selected_candidates: list[_GuidanceCandidateAtRevision] = []
        for decision_id, candidate in zip(
            snapshot.decision_ids,
            candidates,
            strict=True,
        ):
            decision = decision_by_id[decision_id]
            status_id = (
                None
                if candidate.status is None
                else candidate.status.status_revision_id
            )
            status_fingerprint = (
                None
                if candidate.status is None
                else candidate.status.status_fingerprint
            )
            if (
                decision.run_id != snapshot.run_id
                or decision.snapshot_id != snapshot.snapshot_id
                or decision.source_run_id != candidate.draft.run_id
                or decision.guidance_id != candidate.draft.guidance_id
                or decision.draft_revision != candidate.draft.draft_revision
                or decision.status_revision_id != status_id
                or decision.provenance_kind != candidate.draft.provenance_kind
                or decision.assessment_result_id
                != (
                    None
                    if candidate.result is None
                    else candidate.result.assessment_result_id
                )
                or decision.finding_id != candidate.draft.finding_id
                or decision.disposition_id
                != (
                    None
                    if candidate.disposition is None
                    else candidate.disposition.disposition_id
                )
                or decision.result_fingerprint
                != (
                    None
                    if candidate.result is None
                    else candidate.result.result_fingerprint
                )
                or decision.finding_fingerprint != candidate.draft.finding_fingerprint
                or decision.disposition_fingerprint
                != (
                    None
                    if candidate.disposition is None
                    else candidate.disposition.disposition_fingerprint
                )
                or decision.observation_id != candidate.draft.observation_id
                or decision.observation_fingerprint
                != candidate.draft.observation_fingerprint
                or decision.draft_fingerprint != candidate.draft.draft_fingerprint
                or decision.status_fingerprint != status_fingerprint
                or decision.source_scope_fingerprint
                != candidate.source_scope.scope_fingerprint
                or decision.successor_scope_fingerprint
                != successor_scope.scope_fingerprint
                or decision.selected
                != (candidate.reason_code == "approved_scope_match")
                or decision.reason_code != candidate.reason_code
            ):
                return False
            if decision.selected:
                selected_candidates.append(candidate)

        if len(snapshot.selected_item_ids) != len(selected_candidates):
            return False
        for position, (item_id, candidate) in enumerate(
            zip(snapshot.selected_item_ids, selected_candidates, strict=True)
        ):
            item = item_by_id[item_id]
            status = candidate.status
            if status is None or (
                item.run_id != snapshot.run_id
                or item.snapshot_id != snapshot.snapshot_id
                or item.position != position
                or item.source_run_id != candidate.draft.run_id
                or item.finalized_lineage_fingerprint
                != candidate.draft.finalized_lineage_fingerprint
                or item.provenance_kind != candidate.draft.provenance_kind
                or item.assessment_result_id
                != (
                    None
                    if candidate.result is None
                    else candidate.result.assessment_result_id
                )
                or item.assessment_result_fingerprint
                != (
                    None
                    if candidate.result is None
                    else candidate.result.result_fingerprint
                )
                or item.finding_id != candidate.draft.finding_id
                or item.finding_fingerprint != candidate.draft.finding_fingerprint
                or item.disposition_id
                != (
                    None
                    if candidate.disposition is None
                    else candidate.disposition.disposition_id
                )
                or item.disposition_fingerprint
                != (
                    None
                    if candidate.disposition is None
                    else candidate.disposition.disposition_fingerprint
                )
                or item.observation_id != candidate.draft.observation_id
                or item.observation_fingerprint
                != candidate.draft.observation_fingerprint
                or item.guidance_id != candidate.draft.guidance_id
                or item.draft_revision != candidate.draft.draft_revision
                or item.draft_fingerprint != candidate.draft.draft_fingerprint
                or item.status_revision_id != status.status_revision_id
                or item.status_fingerprint != status.status_fingerprint
                or item.guidance_text != candidate.draft.guidance_text
                or item.guidance_sha256 != candidate.draft.guidance_sha256
                or item.reuse_scope != candidate.source_scope
            ):
                return False
        return (
            len(items) <= _MAX_GUIDANCE_SNAPSHOT_ITEMS
            and sum(len(item.guidance_text.encode("utf-8")) for item in items)
            <= _MAX_GUIDANCE_SNAPSHOT_UTF8_BYTES
        )

    def _preflight_guidance_snapshot_subgraph(
        self,
        uow: "ControlUnitOfWork",
        run_id: str,
    ) -> None:
        snapshots = tuple(uow._run_guidance_snapshots.values())
        decisions = tuple(uow._run_guidance_selection_decisions.values())
        items = tuple(uow._run_guidance_snapshot_items.values())
        has_guidance_effect = any((snapshots, decisions, items))
        is_successor = uow.transaction_type == _GUIDANCE_SUCCESSOR_TRANSACTION_TYPE
        if not has_guidance_effect:
            if is_successor:
                raise ControlStoreConflict("relational_integrity_conflict")
            return
        if not is_successor or len(snapshots) != 1:
            raise ControlStoreConflict("relational_integrity_conflict")
        snapshot = snapshots[0]
        binding = uow._run_contract_binding
        transitions = tuple(uow._run_head_transitions.values())
        events = {item.event_id: item for item in uow._events}
        event = events.get(snapshot.snapshot_event_id)
        if (
            binding is None
            or len(transitions) != 1
            or uow._run is None
            or snapshot.run_id != run_id
            or snapshot.workspace_id != self.workspace_id
            or snapshot.accepted_transaction_id != uow.transaction_id
            or snapshot.request_fingerprint != binding.request_fingerprint
            or event is None
            or event.run_id != run_id
            or event.transaction_id != uow.transaction_id
            or event.event_type != "run_guidance_snapshot_frozen"
            or event.core_run_binding is not None
            or transitions[0].predecessor_run_id != snapshot.predecessor_run_id
            or transitions[0].successor_run_id != run_id
            or transitions[0].reason_code != "human_started_successor"
            or transitions[0].successor_disposition != "reference"
            or transitions[0].request_fingerprint != snapshot.request_fingerprint
        ):
            raise ControlStoreConflict("relational_integrity_conflict")
        if not self._guidance_snapshot_content_is_exact(
            snapshot=snapshot,
            decisions=decisions,
            items=items,
            successor_binding=binding,
            cutoff_revision=uow.expected_revision,
        ):
            raise ControlStoreConflict("relational_integrity_conflict")

    def _preflight_post_final_assessment_subgraph(
        self, uow: "ControlUnitOfWork", run_id: str
    ) -> None:
        """Bind the three advisory records without interpreting Core legality."""

        staged_events = {event.event_id for event in uow._events}
        existing_policies = {
            str(row[0]): (
                str(row[1]),
                None if row[2] is None else str(row[2]),
            )
            for row in self._connection.execute(
                "SELECT policy_revision_id, policy_fingerprint, "
                "previous_policy_revision_id "
                "FROM post_final_assessment_policy_revisions WHERE run_id=?",
                (run_id,),
            ).fetchall()
        }
        existing_policy_records = {
            record.policy_revision_id: record
            for record in (
                _decode_record(PostFinalAssessmentPolicyRevision, str(row[0]))
                for row in self._connection.execute(
                    "SELECT payload_json FROM post_final_assessment_policy_revisions "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchall()
            )
        }
        existing_request_records = {
            record.assessment_request_id: record
            for record in (
                _decode_record(PostFinalAssessmentRequestRecord, str(row[0]))
                for row in self._connection.execute(
                    "SELECT payload_json FROM post_final_assessment_requests "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchall()
            )
        }
        existing_result_records = {
            record.assessment_result_id: record
            for record in (
                _decode_record(PostFinalAssessmentResultRecord, str(row[0]))
                for row in self._connection.execute(
                    "SELECT payload_json FROM post_final_assessment_results "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchall()
            )
        }
        existing_abandonment_records = {
            record.abandonment_id: record
            for record in (
                _decode_record(PostFinalAssessmentAbandonmentRecord, str(row[0]))
                for row in self._connection.execute(
                    "SELECT payload_json FROM post_final_assessment_abandonments "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchall()
            )
        }
        existing_execution_records = {
            record.execution_id: record
            for record in (
                _decode_record(PostFinalAssessmentExecutionRecord, str(row[0]))
                for row in self._connection.execute(
                    "SELECT payload_json FROM post_final_assessment_executions "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchall()
            )
        }
        staged_policies = uow._post_final_assessment_policy_revisions
        staged_requests = uow._post_final_assessment_requests
        staged_abandonments = uow._post_final_assessment_abandonments
        staged_executions = uow._post_final_assessment_executions
        staged_results = uow._post_final_assessment_results

        if (
            len(staged_policies) > 1
            or len(staged_requests) > 1
            or len(staged_abandonments) > 1
            or len(staged_executions) > 1
            or len(staged_results) > 1
        ):
            raise ControlStoreConflict("relational_integrity_conflict")
        policy_ids = set(existing_policies)
        predecessor_ids = {
            previous_policy_revision_id
            for _fingerprint, previous_policy_revision_id in existing_policies.values()
            if previous_policy_revision_id is not None
        }
        policy_heads = policy_ids - predecessor_ids
        if not policy_ids:
            expected_previous_policy_id = None
        elif len(policy_heads) == 1:
            expected_previous_policy_id = next(iter(policy_heads))
        else:
            raise ControlStoreConflict("relational_integrity_conflict")
        for record in staged_policies.values():
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.policy_event_id not in staged_events
                or record.policy_revision_id in existing_policies
                or record.previous_policy_revision_id != expected_previous_policy_id
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        available_policies = {
            policy_revision_id: fingerprint
            for policy_revision_id, (
                fingerprint,
                _previous_policy_revision_id,
            ) in existing_policies.items()
        }
        available_policies.update(
            {key: value.policy_fingerprint for key, value in staged_policies.items()}
        )
        available_policy_records = dict(existing_policy_records)
        available_policy_records.update(staged_policies)
        available_results = dict(existing_result_records)
        available_results.update(staged_results)
        available_abandonments = dict(existing_abandonment_records)
        available_abandonments.update(staged_abandonments)
        available_executions = dict(existing_execution_records)
        available_executions.update(staged_executions)
        available_requests = dict(existing_request_records)
        available_requests.update(staged_requests)

        for record in staged_executions.values():
            request = available_requests.get(record.assessment_request_id)
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.execution_event_id not in staged_events
                or record.execution_id in existing_execution_records
                or request is None
                or request.request_fingerprint != record.assessment_request_fingerprint
                or request.trial_id != record.trial_id
                or request.finalized_lineage_fingerprint
                != record.finalized_lineage_fingerprint
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        for record in staged_abandonments.values():
            request = available_requests.get(record.assessment_request_id)
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.abandonment_event_id not in staged_events
                or record.abandonment_id in existing_abandonment_records
                or request is None
                or request.request_fingerprint != record.assessment_request_fingerprint
                or request.finalized_lineage_fingerprint
                != record.finalized_lineage_fingerprint
                or request.assessment_generation != record.assessment_generation
                or any(
                    result.assessment_request_id == record.assessment_request_id
                    for result in available_results.values()
                )
                or any(
                    item.assessment_request_id == record.assessment_request_id
                    for item in existing_abandonment_records.values()
                )
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        existing_series: dict[str, list[PostFinalAssessmentRequestRecord]] = {}
        for request in existing_request_records.values():
            existing_series.setdefault(
                request.finalized_lineage_fingerprint, []
            ).append(request)
        for record in staged_requests.values():
            policy_fingerprint = available_policies.get(record.policy_revision_id)
            policy_record = available_policy_records.get(record.policy_revision_id)
            series = sorted(
                existing_series.get(record.finalized_lineage_fingerprint, []),
                key=lambda item: item.assessment_generation,
            )
            predecessor = None if not series else series[-1]
            predecessor_result = (
                None
                if predecessor is None
                else next(
                    (
                        item
                        for item in available_results.values()
                        if item.assessment_request_id
                        == predecessor.assessment_request_id
                    ),
                    None,
                )
            )
            predecessor_abandonment = (
                None
                if predecessor is None
                else next(
                    (
                        item
                        for item in available_abandonments.values()
                        if item.assessment_request_id
                        == predecessor.assessment_request_id
                    ),
                    None,
                )
            )
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.request_event_id not in staged_events
                or record.assessment_request_id in existing_request_records
                or policy_fingerprint != record.policy_fingerprint
                or (
                    record.schema_version
                    == PostFinalAssessmentRequestRecord.reader_review_schema_id
                    and (
                        policy_record is None
                        or policy_record.schema_version
                        != PostFinalAssessmentPolicyRevision.reader_review_schema_id
                        or policy_record.assessment_kind != record.assessment_kind
                        or policy_record.report_type != record.report_type
                        or policy_record.language != record.language
                        or policy_record.profile_id != record.profile_id
                        or policy_record.model_version != record.model_version
                        or policy_record.expected_model_identity
                        != record.expected_model_identity
                        or policy_record.disclosure_confirmed
                        != record.disclosure_confirmed
                        or policy_record.public_safe_egress_attested
                        != record.public_safe_egress_attested
                        or policy_record.cost_status != record.cost_status
                    )
                )
                or record.assessment_generation != len(series) + 1
                or (
                    predecessor is None
                    and any(
                        value is not None
                        for value in (
                            record.predecessor_assessment_request_id,
                            record.predecessor_assessment_request_fingerprint,
                            record.predecessor_assessment_result_id,
                            record.predecessor_result_fingerprint,
                            record.predecessor_abandonment_id,
                            record.predecessor_abandonment_fingerprint,
                        )
                    )
                )
                or (
                    predecessor is not None
                    and (
                        record.predecessor_assessment_request_id
                        != predecessor.assessment_request_id
                        or record.predecessor_assessment_request_fingerprint
                        != predecessor.request_fingerprint
                        or (
                            predecessor_result is not None
                            and (
                                record.predecessor_assessment_result_id
                                != predecessor_result.assessment_result_id
                                or record.predecessor_result_fingerprint
                                != predecessor_result.result_fingerprint
                                or record.predecessor_abandonment_id is not None
                                or record.predecessor_abandonment_fingerprint
                                is not None
                            )
                        )
                        or (
                            predecessor_abandonment is not None
                            and (
                                record.predecessor_abandonment_id
                                != predecessor_abandonment.abandonment_id
                                or record.predecessor_abandonment_fingerprint
                                != predecessor_abandonment.abandonment_fingerprint
                                or record.predecessor_assessment_result_id is not None
                                or record.predecessor_result_fingerprint is not None
                            )
                        )
                        or (
                            predecessor_result is None
                            and predecessor_abandonment is None
                        )
                        or (
                            predecessor_result is not None
                            and predecessor_abandonment is not None
                        )
                    )
                )
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
            existing_series.setdefault(record.finalized_lineage_fingerprint, []).append(
                record
            )
        for record in staged_results.values():
            request = available_requests.get(record.assessment_request_id)
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.result_event_id not in staged_events
                or record.assessment_result_id in existing_result_records
                or request is None
                or request.policy_revision_id != record.policy_revision_id
                or request.finalized_facts_fingerprint
                != record.finalized_facts_fingerprint
                or (
                    record.schema_version
                    == PostFinalAssessmentResultRecord.reader_review_schema_id
                    and (
                        request.schema_version
                        != PostFinalAssessmentRequestRecord.reader_review_schema_id
                        or request.assessment_kind != record.assessment_kind
                        or request.report_type != record.report_type
                        or request.language != record.language
                        or request.profile_id != record.profile_id
                        or request.model_version != record.model_version
                        or request.expected_model_identity
                        != record.expected_model_identity
                        or request.parser_version != record.parser_version
                        or request.projection_version != record.projection_version
                    )
                )
                or any(
                    abandonment.assessment_request_id == record.assessment_request_id
                    for abandonment in available_abandonments.values()
                )
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        def load_models(model_type: type[_ModelT], table: str) -> dict[object, _ModelT]:
            rows = self._connection.execute(
                f"SELECT payload_json FROM {table} WHERE run_id=?",
                (run_id,),
            ).fetchall()
            models = [_decode_record(model_type, str(row[0])) for row in rows]
            if model_type is PostFinalAssessmentResultRecord:
                return {item.assessment_result_id: item for item in models}
            if model_type is PostFinalFindingDispositionRecord:
                return {item.disposition_id: item for item in models}
            if model_type is PostFinalHumanObservationRecord:
                return {item.observation_id: item for item in models}
            if model_type is PostFinalGuidanceDraftRevision:
                return {
                    (item.guidance_id, item.draft_revision): item for item in models
                }
            if model_type is PostFinalGuidanceStatusRevision:
                return {item.status_revision_id: item for item in models}
            raise ControlStoreConflict("relational_integrity_conflict")

        available_results = load_models(
            PostFinalAssessmentResultRecord, "post_final_assessment_results"
        )
        available_results.update(staged_results)
        existing_dispositions = load_models(
            PostFinalFindingDispositionRecord, "post_final_finding_dispositions"
        )
        staged_dispositions = uow._post_final_finding_dispositions
        if len(staged_dispositions) > 1:
            raise ControlStoreConflict("relational_integrity_conflict")
        disposition_heads: dict[tuple[str, str], str] = {}
        disposition_groups: dict[
            tuple[str, str], list[PostFinalFindingDispositionRecord]
        ] = {}
        for disposition in existing_dispositions.values():
            key = (disposition.assessment_result_id, disposition.finding_id)
            disposition_groups.setdefault(key, []).append(disposition)
        for key, records in disposition_groups.items():
            ids = {item.disposition_id for item in records}
            referenced = {
                item.previous_disposition_id
                for item in records
                if item.previous_disposition_id is not None
            }
            heads = ids - referenced
            if len(heads) != 1:
                raise ControlStoreConflict("relational_integrity_conflict")
            disposition_heads[key] = next(iter(heads))
        for record in staged_dispositions.values():
            result = available_results.get(record.assessment_result_id)
            key = (record.assessment_result_id, record.finding_id)
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.disposition_event_id not in staged_events
                or record.disposition_id in existing_dispositions
                or result is None
                or result.result_fingerprint != record.assessment_result_fingerprint
                or result.finalized_lineage_fingerprint
                != record.finalized_lineage_fingerprint
                or result.reader_view_sha256 != record.reader_view_sha256
                or record.previous_disposition_id != disposition_heads.get(key)
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        available_dispositions = dict(existing_dispositions)
        available_dispositions.update(staged_dispositions)
        existing_observations = load_models(
            PostFinalHumanObservationRecord, "post_final_human_observations"
        )
        staged_observations = uow._post_final_human_observations
        if len(staged_observations) > 1:
            raise ControlStoreConflict("relational_integrity_conflict")
        available_observations = dict(existing_observations)
        available_observations.update(staged_observations)
        existing_drafts = load_models(
            PostFinalGuidanceDraftRevision, "post_final_guidance_drafts"
        )
        staged_drafts = uow._post_final_guidance_drafts
        if len(staged_drafts) > 1:
            raise ControlStoreConflict("relational_integrity_conflict")
        draft_heads: dict[str, int] = {}
        for guidance_id, revision in existing_drafts:
            draft_heads[guidance_id] = max(draft_heads.get(guidance_id, 0), revision)
        for record in staged_drafts.values():
            disposition = available_dispositions.get(record.disposition_id)
            observation = available_observations.get(record.observation_id)
            expected_revision = draft_heads.get(record.guidance_id, 0) + 1
            disposition_key = (
                record.assessment_result_id,
                record.finding_id,
            )
            model_draft_valid = (
                record.provenance_kind == "accepted_model_finding"
                and disposition is not None
                and disposition.decision == "accept"
                and disposition.disposition_fingerprint
                == record.disposition_fingerprint
                and disposition.assessment_result_id == record.assessment_result_id
                and disposition.assessment_result_fingerprint
                == record.assessment_result_fingerprint
                and disposition.finding_id == record.finding_id
                and disposition.finding_fingerprint == record.finding_fingerprint
                and disposition.finalized_lineage_fingerprint
                == record.finalized_lineage_fingerprint
                and disposition_heads.get(disposition_key) == record.disposition_id
            )
            observation_draft_valid = (
                record.provenance_kind == "human_observation"
                and observation is not None
                and observation.observation_fingerprint
                == record.observation_fingerprint
                and observation.finalized_lineage_fingerprint
                == record.finalized_lineage_fingerprint
                and (
                    record.assessment_result_id is None
                    or (
                        observation.assessment_result_id == record.assessment_result_id
                        and observation.assessment_result_fingerprint
                        == record.assessment_result_fingerprint
                    )
                )
                and not any(
                    item.previous_observation_id == observation.observation_id
                    for item in available_observations.values()
                )
            )
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.draft_event_id not in staged_events
                or (record.guidance_id, record.draft_revision) in existing_drafts
                or not (model_draft_valid or observation_draft_valid)
                or record.draft_revision != expected_revision
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

        available_drafts = dict(existing_drafts)
        available_drafts.update(staged_drafts)
        existing_statuses = load_models(
            PostFinalGuidanceStatusRevision, "post_final_guidance_statuses"
        )
        staged_statuses = uow._post_final_guidance_statuses
        if len(staged_statuses) > 1:
            raise ControlStoreConflict("relational_integrity_conflict")
        status_heads: dict[str, str] = {}
        status_groups: dict[str, list[PostFinalGuidanceStatusRevision]] = {}
        for status in existing_statuses.values():
            status_groups.setdefault(status.guidance_id, []).append(status)
        for guidance_id, records in status_groups.items():
            ids = {item.status_revision_id for item in records}
            referenced = {
                item.previous_status_revision_id
                for item in records
                if item.previous_status_revision_id is not None
            }
            heads = ids - referenced
            if len(heads) != 1:
                raise ControlStoreConflict("relational_integrity_conflict")
            status_heads[guidance_id] = next(iter(heads))
        for record in staged_statuses.values():
            draft = available_drafts.get((record.guidance_id, record.draft_revision))
            current_status = (
                existing_statuses.get(status_heads.get(record.guidance_id, ""))
                if status_heads.get(record.guidance_id) is not None
                else None
            )
            current_disposition = (
                available_dispositions.get(draft.disposition_id)
                if draft is not None
                else None
            )
            current_observation = (
                available_observations.get(draft.observation_id)
                if draft is not None
                else None
            )
            disposition_key = (
                (draft.assessment_result_id, draft.finding_id)
                if draft is not None
                else None
            )
            if (
                record.accepted_transaction_id != uow.transaction_id
                or record.status_event_id not in staged_events
                or record.status_revision_id in existing_statuses
                or draft is None
                or draft.finalized_lineage_fingerprint
                != record.finalized_lineage_fingerprint
                or draft.guidance_sha256 != record.guidance_sha256
                or (
                    record.status == "approved"
                    and (
                        (
                            draft.provenance_kind == "human_observation"
                            and (
                                current_observation is None
                                or current_observation.observation_fingerprint
                                != draft.observation_fingerprint
                            )
                        )
                        or (
                            draft.provenance_kind != "human_observation"
                            and (
                                current_disposition is None
                                or current_disposition.decision != "accept"
                                or disposition_heads.get(disposition_key)
                                != draft.disposition_id
                            )
                        )
                    )
                )
                or record.previous_status_revision_id
                != status_heads.get(record.guidance_id)
                or not post_final_guidance_status_transition_allowed(
                    current_status,
                    record,
                    approval_eligible=(
                        (
                            current_observation is not None
                            and current_observation.observation_fingerprint
                            == draft.observation_fingerprint
                        )
                        if draft.provenance_kind == "human_observation"
                        else (
                            current_disposition is not None
                            and current_disposition.decision == "accept"
                            and disposition_heads.get(disposition_key)
                            == draft.disposition_id
                        )
                    ),
                )
            ):
                raise ControlStoreConflict("relational_integrity_conflict")

    def _build_receipt(
        self,
        uow: "ControlUnitOfWork",
        identity: "_TransactionIdentity",
        committed_revision: int,
        artifact_identities: tuple[ArtifactIdentityRecord, ...],
    ) -> TransactionReceipt:
        timestamp = self._clock()
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise ControlStoreStateError("store_clock_invalid")
        committed_at = timestamp.isoformat().replace("+00:00", "Z")
        try:
            return TransactionReceipt.model_validate(
                {
                    "schema_version": TransactionReceipt.schema_id,
                    "transaction_id": identity.transaction_id,
                    "run_id": identity.run_id,
                    "transaction_type": identity.transaction_type,
                    "prior_revision": identity.expected_revision,
                    "committed_revision": committed_revision,
                    "committed_at": committed_at,
                    "projection_status": "stale",
                    "event_ids": [event.event_id for event in uow._events],
                    "artifact_revisions": [
                        {
                            "artifact_id": item.record.artifact_id,
                            "revision": item.record.revision,
                        }
                        for item in uow._artifact_revisions
                    ],
                    "artifact_identities": [
                        {"artifact_id": item.artifact_id}
                        for item in artifact_identities
                    ],
                    "source_ids": list(uow._sources),
                    "proposal_ids": list(uow._accepted_proposals),
                    "run_contract_bindings": (
                        [{"run_id": uow._run_contract_binding.run_id}]
                        if uow._run_contract_binding is not None
                        else []
                    ),
                    "run_execution_authorizations": (
                        [
                            {
                                "authorization_id": uow._run_execution_authorization.authorization_id
                            }
                        ]
                        if uow._run_execution_authorization is not None
                        else []
                    ),
                    "run_source_discovery_authorizations": [
                        {"authorization_id": authorization_id}
                        for authorization_id in sorted(
                            uow._referenced_source_discovery_authorizations
                        )
                    ],
                    "run_source_acquisition_attempt_authorizations": (
                        [
                            {"attempt_authorization_id": attempt_id}
                            for attempt_id in sorted(
                                uow._referenced_source_acquisition_attempt_authorizations
                            )
                        ]
                    ),
                    "owned_artifact_submissions": [
                        {"submission_id": key}
                        for key in sorted(uow._owned_artifact_submissions)
                    ],
                    "stage_transitions": [
                        {"transition_id": key} for key in sorted(uow._stage_transitions)
                    ],
                    "stage_artifact_bindings": [
                        {"transition_id": key[0], "position": key[1]}
                        for key in sorted(uow._stage_artifact_bindings)
                    ],
                    "stage_gate_bindings": [
                        {"transition_id": key[0], "gate_id": key[1]}
                        for key in sorted(uow._stage_gate_bindings)
                    ],
                    "claims": [{"claim_id": key} for key in sorted(uow._claims)],
                    "claim_source_bindings": [
                        {"claim_id": key[0], "source_id": key[1]}
                        for key in sorted(uow._claim_source_bindings)
                    ],
                    "claim_freezes": [
                        {"freeze_id": key} for key in sorted(uow._claim_freezes)
                    ],
                    "gate_evaluations": [
                        {"evaluation_id": key} for key in sorted(uow._gate_evaluations)
                    ],
                    "gate_findings": [
                        {"evaluation_id": key[0], "finding_id": key[1]}
                        for key in sorted(uow._gate_findings)
                    ],
                    "gate_artifact_bindings": [
                        {"evaluation_id": key[0], "position": key[1]}
                        for key in sorted(uow._gate_artifact_bindings)
                    ],
                    "run_integrity_records": [
                        {"integrity_revision": key}
                        for key in sorted(uow._run_integrity_records)
                    ],
                    "repair_cycles": [
                        {"repair_id": key} for key in sorted(uow._repair_cycles)
                    ],
                    "gate_repair_cycles": [
                        {"gate_repair_id": key}
                        for key in sorted(uow._gate_repair_cycles)
                    ],
                    "gate_repair_artifact_bindings": [
                        {"gate_repair_id": key}
                        for key in sorted(uow._gate_repair_artifact_bindings)
                    ],
                    "gate_repair_outcomes": [
                        {"outcome_id": key} for key in sorted(uow._gate_repair_outcomes)
                    ],
                    "artifact_supersessions": [
                        {"supersession_id": key}
                        for key in sorted(uow._artifact_supersessions)
                    ],
                    "repair_completions": [
                        {"repair_completion_id": key}
                        for key in sorted(uow._repair_completions)
                    ],
                    "recovery_completions": [
                        {"recovery_id": key}
                        for key in sorted(uow._recovery_completions)
                    ],
                    "run_head_transitions": [
                        {"head_transition_id": key}
                        for key in sorted(uow._run_head_transitions)
                    ],
                    "finalize_renders": [
                        {"render_id": key} for key in sorted(uow._finalize_renders)
                    ],
                    "finalizations": [
                        {"finalization_id": key} for key in sorted(uow._finalizations)
                    ],
                    "run_archives": [
                        {"archive_id": key} for key in sorted(uow._run_archives)
                    ],
                    "run_archive_artifact_bindings": [
                        {"archive_id": key[0], "position": key[1]}
                        for key in sorted(uow._run_archive_artifact_bindings)
                    ],
                    "package_ready_records": [
                        {"package_id": key}
                        for key in sorted(uow._package_ready_records)
                    ],
                    "package_artifact_bindings": [
                        {"package_id": key[0], "position": key[1]}
                        for key in sorted(uow._package_artifact_bindings)
                    ],
                    "approvals": [
                        {"approval_id": key} for key in sorted(uow._approvals)
                    ],
                    "approval_package_bindings": [
                        {"approval_id": key[0], "package_id": key[1]}
                        for key in sorted(uow._approval_package_bindings)
                    ],
                    "delivery_authorizations": [
                        {"authorization_id": key}
                        for key in sorted(uow._delivery_authorizations)
                    ],
                    "delivery_attempts": [
                        {"attempt_id": key} for key in sorted(uow._delivery_attempts)
                    ],
                    "delivery_results": [
                        {"result_id": key} for key in sorted(uow._delivery_results)
                    ],
                    "post_final_assessment_policy_revisions": [
                        {"policy_revision_id": key}
                        for key in sorted(uow._post_final_assessment_policy_revisions)
                    ],
                    "post_final_assessment_requests": [
                        {"assessment_request_id": key}
                        for key in sorted(uow._post_final_assessment_requests)
                    ],
                    "post_final_assessment_abandonments": [
                        {"abandonment_id": key}
                        for key in sorted(uow._post_final_assessment_abandonments)
                    ],
                    "post_final_assessment_results": [
                        {"assessment_result_id": key}
                        for key in sorted(uow._post_final_assessment_results)
                    ],
                    "post_final_finding_dispositions": [
                        {"disposition_id": key}
                        for key in sorted(uow._post_final_finding_dispositions)
                    ],
                    "post_final_human_observations": [
                        {"observation_id": key}
                        for key in sorted(uow._post_final_human_observations)
                    ],
                    "post_final_guidance_drafts": [
                        {"guidance_id": key[0], "draft_revision": key[1]}
                        for key in sorted(uow._post_final_guidance_drafts)
                    ],
                    "post_final_guidance_statuses": [
                        {"status_revision_id": key}
                        for key in sorted(uow._post_final_guidance_statuses)
                    ],
                    "run_guidance_snapshots": [
                        {"snapshot_id": key}
                        for key in sorted(uow._run_guidance_snapshots)
                    ],
                    "run_guidance_selection_decisions": [
                        {"decision_id": key}
                        for key in sorted(uow._run_guidance_selection_decisions)
                    ],
                    "run_guidance_snapshot_items": [
                        {"item_id": key}
                        for key in sorted(uow._run_guidance_snapshot_items)
                    ],
                    "checkout_revisions": [
                        {"checkout_revision_id": key}
                        for key in sorted(uow._checkout_revisions)
                    ],
                    "receipt_checkout_bindings": (
                        [{"transaction_id": uow.transaction_id}]
                        if uow._receipt_checkout_binding is not None
                        else []
                    ),
                    "checkout_publication_intents": (
                        [
                            {
                                "checkout_revision_id": uow._checkout_publication_intent.identity.checkout_revision_id
                            }
                        ]
                        if uow._checkout_publication_intent is not None
                        else []
                    ),
                }
            )
        except ValueError as exc:
            raise ControlStoreIntegrityError("transaction_identity_invalid") from exc

    def _preflight_checkout_subgraph(
        self,
        uow: "ControlUnitOfWork",
        run_id: str,
    ) -> None:
        """Validate the complete immutable revision and journal before writes."""

        revisions = tuple(uow._checkout_revisions.values())
        members = tuple(uow._checkout_revision_members.values())
        binding = uow._receipt_checkout_binding
        intent = uow._checkout_publication_intent
        publication_members = tuple(uow._checkout_publication_members.values())
        requires_checkout = uow.transaction_type.startswith("core-v2-")
        if not any((revisions, members, binding, intent, publication_members)):
            if requires_checkout:
                raise ControlStoreConflict("relational_integrity_conflict")
            return
        if len(revisions) != 1 or binding is None:
            raise ControlStoreConflict("relational_integrity_conflict")
        revision = revisions[0]
        revision_members = tuple(
            sorted(
                (
                    item
                    for item in members
                    if item.checkout_revision_id == revision.checkout_revision_id
                ),
                key=lambda item: item.ordinal,
            )
        )
        if (
            len(revision_members) != revision.member_count
            or [item.ordinal for item in revision_members]
            != list(range(revision.member_count))
            or binding.post_checkout_revision_id != revision.checkout_revision_id
            or binding.post_run_id != revision.run_id
        ):
            raise ControlStoreConflict("relational_integrity_conflict")
        if (
            binding.workspace_id != self.workspace_id
            or binding.run_id != run_id
            or binding.transaction_id != uow.transaction_id
            or binding.post_run_id != revision.run_id
            or binding.post_checkout_revision_id != revision.checkout_revision_id
            or binding.pre_checkout_revision_id != revision.parent_checkout_revision_id
        ):
            raise ControlStoreConflict("relational_integrity_conflict")
        available = {
            (item.record.artifact_id, item.record.revision): item.record
            for item in uow._artifact_revisions
        }
        for item in revision_members:
            record = available.get((item.artifact_id, item.artifact_revision))
            if record is None:
                row = self._connection.execute(
                    "SELECT payload_json FROM artifact_revisions WHERE run_id=? AND artifact_id=? AND revision=?",
                    (run_id, item.artifact_id, item.artifact_revision),
                ).fetchone()
                if row is None:
                    raise ControlStoreConflict("relational_integrity_conflict")
                record = _decode_record(ArtifactRevision, str(row[0]))
            if (
                record.path != item.canonical_path
                or record.sha256 != item.blob_sha256
                or record.size_bytes != item.byte_size
                or not record.frozen
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
        try:
            rebuilt_record, rebuilt_members, _manifest_bytes = (
                _build_checkout_revision_structure(
                    workspace_id=revision.workspace_id,
                    run_id=revision.run_id,
                    transaction_id=revision.creator_transaction_id,
                    created_at=datetime.fromisoformat(
                        revision.created_at.replace("Z", "+00:00")
                    ),
                    artifact_revisions=tuple(
                        available.get((item.artifact_id, item.artifact_revision))
                        or _decode_record(
                            ArtifactRevision,
                            str(
                                self._connection.execute(
                                    "SELECT payload_json FROM artifact_revisions WHERE run_id=? AND artifact_id=? AND revision=?",
                                    (run_id, item.artifact_id, item.artifact_revision),
                                ).fetchone()[0]
                            ),
                        )
                        for item in revision_members
                    ),
                    parent_checkout_revision_id=revision.parent_checkout_revision_id,
                )
            )
        except (_CheckoutStructureError, ValueError) as exc:
            raise ControlStoreConflict("relational_integrity_conflict") from exc
        if rebuilt_record != revision or rebuilt_members != revision_members:
            raise ControlStoreConflict("relational_integrity_conflict")
        pre_record: CheckoutRevisionRecord | None = None
        pre_members: tuple[CheckoutRevisionMember, ...] = ()
        if binding.pre_checkout_revision_id is not None:
            try:
                pre_snapshot = self.load_snapshot(binding.pre_run_id)
            except ControlStoreError as exc:
                raise ControlStoreConflict("relational_integrity_conflict") from exc
            pre_records = tuple(
                item
                for item in pre_snapshot.checkout_revisions
                if item.checkout_revision_id == binding.pre_checkout_revision_id
            )
            if len(pre_records) != 1:
                raise ControlStoreConflict("relational_integrity_conflict")
            pre_members = tuple(
                sorted(
                    (
                        item
                        for item in pre_snapshot.checkout_revision_members
                        if item.checkout_revision_id == binding.pre_checkout_revision_id
                    ),
                    key=lambda item: item.ordinal,
                )
            )
            pre_record = pre_records[0]
        if intent is None:
            if publication_members:
                raise ControlStoreConflict("relational_integrity_conflict")
            return
        if (
            intent.identity.workspace_id != self.workspace_id
            or intent.identity.run_id != run_id
            or intent.identity.transaction_id != uow.transaction_id
            or intent.identity.checkout_revision_id != revision.checkout_revision_id
            or binding.workspace_id != self.workspace_id
            or binding.run_id != run_id
            or binding.transaction_id != uow.transaction_id
            or binding.post_run_id != run_id
            or binding.post_checkout_revision_id != revision.checkout_revision_id
            or revision.parent_checkout_revision_id != binding.pre_checkout_revision_id
        ):
            raise ControlStoreConflict("relational_integrity_conflict")
        try:
            expected_intent, expected_members = _derive_publication_structure(
                identity=intent.identity,
                pre_record=pre_record,
                pre_members=pre_members,
                post_record=rebuilt_record,
                post_members=rebuilt_members,
                capability_profile_sha256=intent.capability_profile_sha256,
            )
        except _CheckoutStructureError as exc:
            raise ControlStoreConflict("relational_integrity_conflict") from exc
        if (
            intent != expected_intent
            or tuple(sorted(publication_members, key=lambda item: item.ordinal))
            != expected_members
        ):
            raise ControlStoreConflict("relational_integrity_conflict")

    def _insert_run(self, record: RunIdentity | None) -> None:
        if record is None:
            return
        self._connection.execute(
            """
            INSERT INTO runs(
                run_id, workspace_id, schema_version, runtime, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.workspace_id,
                record.schema_version,
                record.runtime,
                record.created_at,
                _canonical_record_text(record),
            ),
        )

    def _insert_transaction(
        self,
        receipt: TransactionReceipt,
        workspace_id: str,
        fingerprint: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO transactions(
                run_id, transaction_id, workspace_id, schema_version,
                transaction_type, prior_revision, committed_revision, committed_at,
                projection_status, fingerprint, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.run_id,
                receipt.transaction_id,
                workspace_id,
                receipt.schema_version,
                receipt.transaction_type,
                receipt.prior_revision,
                receipt.committed_revision,
                receipt.committed_at,
                receipt.projection_status,
                fingerprint,
                canonical_model_text(receipt),
            ),
        )

    def _upsert_workspace_run_head(
        self,
        record: WorkspaceRunHead | None,
    ) -> None:
        if record is None:
            return
        self._connection.execute(
            """
            INSERT INTO workspace_run_heads(
                workspace_id, schema_version, current_run_id, updated_at, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                current_run_id=excluded.current_run_id,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                record.workspace_id,
                record.schema_version,
                record.current_run_id,
                record.updated_at,
                _canonical_record_text(record),
            ),
        )

    def _upsert_stage_states(self, records: Iterable[StageState]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO stage_states(
                    run_id, stage_id, schema_version, status, revision,
                    updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, stage_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    status=excluded.status,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    record.run_id,
                    record.stage_id,
                    record.schema_version,
                    record.status,
                    record.revision,
                    record.updated_at,
                    _canonical_record_text(record),
                ),
            )

    def _upsert_invocations(self, records: Iterable[Invocation]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO agent_invocations(
                    run_id, invocation_id, schema_version, role_id, runtime, status,
                    started_at, completed_at, failure_reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, invocation_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    role_id=excluded.role_id,
                    runtime=excluded.runtime,
                    status=excluded.status,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    failure_reason=excluded.failure_reason,
                    payload_json=excluded.payload_json
                """,
                (
                    record.run_id,
                    record.invocation_id,
                    record.schema_version,
                    record.role_id,
                    record.runtime,
                    record.status,
                    record.started_at,
                    record.completed_at,
                    record.failure_reason,
                    _canonical_record_text(record),
                ),
            )

    def _upsert_artifacts(self, records: Iterable[ArtifactRecord]) -> None:
        for record in records:
            revision_ref = record.current_revision or None
            self._connection.execute(
                """
                INSERT INTO artifacts(
                    run_id, artifact_id, schema_version, current_revision,
                    current_revision_ref, status, required, path, format, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, artifact_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    current_revision=excluded.current_revision,
                    current_revision_ref=excluded.current_revision_ref,
                    status=excluded.status,
                    required=excluded.required,
                    path=excluded.path,
                    format=excluded.format,
                    payload_json=excluded.payload_json
                """,
                (
                    record.run_id,
                    record.artifact_id,
                    record.schema_version,
                    record.current_revision,
                    revision_ref,
                    record.status,
                    int(record.required),
                    record.path,
                    record.format,
                    _canonical_record_text(record),
                ),
            )

    def _insert_artifact_identities(
        self,
        records: Iterable[ArtifactIdentityRecord],
    ) -> None:
        for position, record in enumerate(records, start=1):
            self._inject(f"before_artifact_identity_insert:{position}")
            self._connection.execute(
                """
                INSERT INTO artifact_identities(
                    run_id, artifact_id, schema_version, required,
                    initial_path, format, accepted_transaction_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.artifact_id,
                    record.schema_version,
                    int(record.required),
                    record.initial_path,
                    record.format,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )
            self._inject(f"after_artifact_identity_insert:{position}")

    def _insert_artifact_revisions(
        self,
        records: Iterable["_StagedArtifactRevision"],
    ) -> None:
        for item in records:
            record = item.record
            self._connection.execute(
                """
                INSERT INTO artifact_revisions(
                    run_id, artifact_id, revision, schema_version, path, sha256,
                    size_bytes, frozen, producer_kind, producer_id, created_at,
                    blob_relpath, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.artifact_id,
                    record.revision,
                    record.schema_version,
                    record.path,
                    record.sha256,
                    record.size_bytes,
                    int(record.frozen),
                    record.producer_kind,
                    record.producer_id,
                    record.created_at,
                    self._blob_relpath(record.sha256),
                    _canonical_record_text(record),
                ),
            )

    def _insert_events(self, records: Iterable[EventEnvelope]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO events(
                    event_id, run_id, schema_version, event_type, created_at, actor,
                    transaction_id, stage_id, artifact_id, decision, reason,
                    metadata_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    record.run_id,
                    record.schema_version,
                    record.event_type,
                    record.created_at,
                    record.actor,
                    record.transaction_id,
                    record.stage_id,
                    record.artifact_id,
                    record.decision,
                    record.reason,
                    canonical_json_bytes(record.metadata).decode("utf-8"),
                    _canonical_record_text(record),
                ),
            )

    def _insert_approvals(self, records: Iterable[Approval]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO approvals(
                    run_id, approval_id, schema_version, mode, role, decision,
                    reason, actor_id, recorded_at, boundary, event_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.approval_id,
                    record.schema_version,
                    record.mode,
                    record.role,
                    record.decision,
                    record.reason,
                    record.actor_id,
                    record.recorded_at,
                    record.boundary,
                    record.event_id,
                    _canonical_record_text(record),
                ),
            )

    def _upsert_deliveries(self, records: Iterable[Delivery]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO deliveries(
                    run_id, delivery_id, schema_version, artifact_id,
                    artifact_revision, approval_id, status, target, channel,
                    created_at, completed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, delivery_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    artifact_id=excluded.artifact_id,
                    artifact_revision=excluded.artifact_revision,
                    approval_id=excluded.approval_id,
                    status=excluded.status,
                    target=excluded.target,
                    channel=excluded.channel,
                    created_at=excluded.created_at,
                    completed_at=excluded.completed_at,
                    payload_json=excluded.payload_json
                """,
                (
                    record.run_id,
                    record.delivery_id,
                    record.schema_version,
                    record.artifact_id,
                    record.artifact_revision,
                    record.approval_id,
                    record.status,
                    record.target,
                    record.channel,
                    record.created_at,
                    record.completed_at,
                    _canonical_record_text(record),
                ),
            )

    def _insert_sources(self, records: Iterable[AcceptedSourceRecord]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO sources(
                    run_id, source_id, schema_version, origin_type,
                    acquisition_method, material_kind, provider, locator_json,
                    title, publisher, published_at, retrieved_at, source_category,
                    retrieval_source_type, underlying_evidence_type,
                    raw_underlying_evidence_type, content_sha256,
                    content_size_bytes, content_media_type, content_blob_path,
                    content_artifact_id, content_artifact_revision,
                    raw_payload_sha256, raw_payload_size_bytes,
                    raw_payload_media_type, raw_payload_blob_path,
                    raw_payload_artifact_id, raw_payload_artifact_revision,
                    claims_eligible, eligibility_reason, invocation_id,
                    acquisition_event_id, accepted_transaction_id,
                    request_fingerprint, created_at, payload_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    record.run_id,
                    record.source_id,
                    record.schema_version,
                    record.origin_type,
                    record.acquisition_method,
                    record.material_kind,
                    record.provider,
                    canonical_json_bytes(record.locator.model_dump(mode="json")).decode(
                        "utf-8"
                    ),
                    record.title,
                    record.publisher,
                    record.published_at,
                    record.retrieved_at,
                    record.source_category,
                    record.retrieval_source_type,
                    record.underlying_evidence_type,
                    record.raw_underlying_evidence_type,
                    record.content_sha256,
                    record.content_size_bytes,
                    record.content_media_type,
                    record.content_blob_path,
                    record.content_artifact_id,
                    record.content_artifact_revision,
                    record.raw_payload_sha256,
                    record.raw_payload_size_bytes,
                    record.raw_payload_media_type,
                    record.raw_payload_blob_path,
                    record.raw_payload_artifact_id,
                    record.raw_payload_artifact_revision,
                    int(record.claims_eligible),
                    record.eligibility_reason,
                    record.invocation_id,
                    record.acquisition_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    record.created_at,
                    _canonical_record_text(record),
                ),
            )

    def _insert_accepted_proposals(
        self,
        records: Iterable[AcceptedProposalRecord],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO accepted_proposals(
                    run_id, proposal_id, schema_version, proposal_kind, artifact_id,
                    artifact_revision, proposal_sha256, invocation_id,
                    owner_stage_id, owner_role_id, parent_proposal_id,
                    target_artifact_id, target_artifact_revision, source_ids_json,
                    accepted_event_id, accepted_transaction_id,
                    request_fingerprint, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.proposal_id,
                    record.schema_version,
                    record.proposal_kind,
                    record.artifact_id,
                    record.artifact_revision,
                    record.proposal_sha256,
                    record.invocation_id,
                    record.owner_stage_id,
                    record.owner_role_id,
                    record.parent_proposal_id,
                    record.target_artifact_id,
                    record.target_artifact_revision,
                    canonical_json_bytes(record.source_ids).decode("utf-8"),
                    record.accepted_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    record.created_at,
                    _canonical_record_text(record),
                ),
            )

    def _insert_proposal_source_bindings(
        self,
        records: Iterable[ProposalSourceBinding],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO proposal_source_bindings(
                    run_id, proposal_id, source_id, schema_version, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.proposal_id,
                    record.source_id,
                    record.schema_version,
                    _canonical_record_text(record),
                ),
            )

    def _insert_run_contract_binding(
        self,
        record: RunContractBinding | None,
    ) -> None:
        if record is None:
            return
        self._connection.execute(
            """
            INSERT INTO run_contract_bindings(
                run_id, workspace_id, schema_version, runtime,
                stage_specs_artifact_id, stage_specs_revision, stage_specs_sha256,
                artifact_contracts_artifact_id, artifact_contracts_revision,
                artifact_contracts_sha256, policy_pack_artifact_id,
                policy_pack_revision, policy_pack_sha256, contract_fingerprint,
                initialization_event_id, accepted_transaction_id,
                request_fingerprint, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.workspace_id,
                record.schema_version,
                record.runtime,
                record.stage_specs_artifact.artifact_id,
                record.stage_specs_artifact.revision,
                record.stage_specs_sha256,
                record.artifact_contracts_artifact.artifact_id,
                record.artifact_contracts_artifact.revision,
                record.artifact_contracts_sha256,
                record.policy_pack_artifact.artifact_id,
                record.policy_pack_artifact.revision,
                record.policy_pack_sha256,
                record.contract_fingerprint,
                record.initialization_event_id,
                record.accepted_transaction_id,
                record.request_fingerprint,
                _canonical_record_text(record),
            ),
        )

    def _insert_run_execution_authorization(
        self,
        record: RunExecutionAuthorization | None,
    ) -> None:
        if record is None:
            return
        self._connection.execute(
            """
            INSERT INTO run_execution_authorizations(
                run_id, authorization_id, workspace_id, schema_version,
                run_contract_fingerprint, run_direction_fingerprint,
                completion_target, source_manifest_artifact_id,
                source_manifest_revision, source_manifest_sha256,
                source_manifest_member_count, repair_budget,
                authorization_event_id, accepted_transaction_id,
                request_fingerprint, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.authorization_id,
                record.workspace_id,
                record.schema_version,
                record.run_contract_fingerprint,
                record.run_direction_fingerprint,
                record.completion_target,
                record.source_manifest_artifact.artifact_id,
                record.source_manifest_artifact.revision,
                record.source_manifest_sha256,
                record.source_manifest_member_count,
                record.repair_budget,
                record.authorization_event_id,
                record.accepted_transaction_id,
                record.request_fingerprint,
                record.created_at,
                _canonical_record_text(record),
            ),
        )

    def _insert_run_source_discovery_authorization(
        self,
        record: RunSourceDiscoveryAuthorization | None,
    ) -> None:
        if record is None:
            return
        self._connection.execute(
            """
            INSERT INTO run_source_discovery_authorizations(
                run_id, authorization_id, workspace_id, schema_version,
                run_contract_fingerprint, run_direction_fingerprint,
                runtime_source_plan_fingerprint, source_route_fingerprint,
                route_id, provider_id, execution_owner, credential_env,
                completion_target, repair_budget, authorization_event_id,
                accepted_transaction_id, request_fingerprint, created_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.authorization_id,
                record.workspace_id,
                record.schema_version,
                record.run_contract_fingerprint,
                record.run_direction_fingerprint,
                record.runtime_source_plan_fingerprint,
                record.source_route_fingerprint,
                record.route_id,
                record.provider_id,
                record.execution_owner,
                record.credential_env,
                record.completion_target,
                record.repair_budget,
                record.authorization_event_id,
                record.accepted_transaction_id,
                record.request_fingerprint,
                record.created_at,
                _canonical_record_text(record),
            ),
        )

    def _insert_run_source_acquisition_attempt_authorization(
        self,
        record: RunSourceAcquisitionAttemptAuthorization | None,
    ) -> None:
        if record is None:
            return
        self._connection.execute(
            """
            INSERT INTO run_source_acquisition_attempt_authorizations_v2(
                run_id, attempt_authorization_id, attempt_ordinal, workspace_id,
                schema_version, discovery_authorization_id,
                run_contract_fingerprint, run_direction_fingerprint,
                runtime_source_plan_fingerprint, source_route_fingerprint,
                provider_request_fingerprint, provider_id, route_id,
                max_provider_calls, provider_cost_status,
                previous_attempt_authorization_id, human_request_id,
                authorization_event_id, accepted_transaction_id,
                request_fingerprint, created_at, payload_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                record.run_id,
                record.attempt_authorization_id,
                record.attempt_ordinal,
                record.workspace_id,
                record.schema_version,
                record.discovery_authorization_id,
                record.run_contract_fingerprint,
                record.run_direction_fingerprint,
                record.runtime_source_plan_fingerprint,
                record.source_route_fingerprint,
                record.provider_request_fingerprint,
                record.provider_id,
                record.route_id,
                record.max_provider_calls,
                record.provider_cost_status,
                record.previous_attempt_authorization_id,
                record.human_request_id,
                record.authorization_event_id,
                record.accepted_transaction_id,
                record.request_fingerprint,
                record.created_at,
                _canonical_record_text(record),
            ),
        )

    def _insert_owned_artifact_submissions(
        self,
        records: Iterable[OwnedArtifactSubmissionRecord],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO owned_artifact_submissions(
                    run_id, submission_id, schema_version, artifact_id,
                    artifact_revision, artifact_sha256, owner_stage_id,
                    owner_role_id, run_contract_fingerprint, invocation_id,
                    producer_tool_id, parent_artifact_id,
                    parent_artifact_revision, source_proposal_id,
                    canonical_workspace_path, request_fingerprint,
                    accepted_event_id, accepted_transaction_id, created_at,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.submission_id,
                    record.schema_version,
                    record.artifact_id,
                    record.artifact_revision,
                    record.artifact_sha256,
                    record.owner_stage_id,
                    record.owner_role_id,
                    record.run_contract_fingerprint,
                    record.invocation_id,
                    record.producer_tool_id,
                    (
                        record.parent_artifact.artifact_id
                        if record.parent_artifact is not None
                        else None
                    ),
                    (
                        record.parent_artifact.revision
                        if record.parent_artifact is not None
                        else None
                    ),
                    record.source_proposal_id,
                    record.canonical_workspace_path,
                    record.request_fingerprint,
                    record.accepted_event_id,
                    record.accepted_transaction_id,
                    record.created_at,
                    _canonical_record_text(record),
                ),
            )

    def _insert_stage_transitions(
        self,
        records: Iterable[StageTransitionRecord],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO stage_transitions(
                    run_id, transition_id, schema_version, stage_id,
                    transition_kind, prior_status, prior_revision, result_status,
                    result_revision, run_contract_fingerprint,
                    transition_event_id, accepted_transaction_id,
                    request_fingerprint, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.transition_id,
                    record.schema_version,
                    record.stage_id,
                    record.transition_kind,
                    record.prior_status,
                    record.prior_revision,
                    record.result_status,
                    record.result_revision,
                    record.run_contract_fingerprint,
                    record.transition_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )

    def _insert_stage_artifact_bindings(
        self,
        records: Iterable[StageArtifactBinding],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO stage_artifact_bindings(
                    run_id, transition_id, position, schema_version, artifact_id,
                    artifact_revision, artifact_sha256, usage,
                    accepted_transaction_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.transition_id,
                    record.position,
                    record.schema_version,
                    record.artifact_id,
                    record.artifact_revision,
                    record.artifact_sha256,
                    record.usage,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )

    def _insert_stage_gate_bindings(
        self,
        records: Iterable[StageGateBinding],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO stage_gate_bindings(
                    run_id, transition_id, gate_id, schema_version,
                    evaluation_id, accepted_transaction_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.transition_id,
                    record.gate_id,
                    record.schema_version,
                    record.evaluation_id,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )

    def _insert_claims(self, records: Iterable[ClaimRecord]) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO claims(
                    run_id, claim_id, schema_version, freeze_id, ordinal,
                    claim_drafts_proposal_id, draft_id, primary_source_id,
                    claim_type, accepted_transaction_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.claim_id,
                    record.schema_version,
                    record.freeze_id,
                    record.ordinal,
                    record.claim_drafts_proposal_id,
                    record.draft_id,
                    record.primary_source_id,
                    record.claim_type,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )

    def _insert_claim_source_bindings(
        self,
        records: Iterable[ClaimSourceBinding],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO claim_source_bindings(
                    run_id, claim_id, source_id, schema_version, position,
                    citation_role, claim_drafts_proposal_id,
                    accepted_transaction_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.claim_id,
                    record.source_id,
                    record.schema_version,
                    record.position,
                    record.citation_role,
                    record.claim_drafts_proposal_id,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )

    def _insert_claim_freezes(
        self,
        records: Iterable[ClaimFreezeRecord],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO claim_freezes(
                    run_id, freeze_id, schema_version,
                    claim_drafts_proposal_id, screened_proposal_id,
                    candidate_proposal_id, claim_drafts_artifact_id,
                    claim_drafts_artifact_revision, claim_drafts_sha256,
                    ledger_artifact_id, ledger_artifact_revision, ledger_sha256,
                    run_contract_fingerprint, claim_count, freeze_event_id,
                    accepted_transaction_id, request_fingerprint, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.freeze_id,
                    record.schema_version,
                    record.claim_drafts_proposal_id,
                    record.screened_proposal_id,
                    record.candidate_proposal_id,
                    record.claim_drafts_artifact.artifact_id,
                    record.claim_drafts_artifact.revision,
                    record.claim_drafts_sha256,
                    record.ledger_artifact.artifact_id,
                    record.ledger_artifact.revision,
                    record.ledger_sha256,
                    record.run_contract_fingerprint,
                    record.claim_count,
                    record.freeze_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )

    def _insert_gate_evaluations(
        self,
        records: Iterable[GateEvaluationRecord],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO gate_evaluations(
                    run_id, evaluation_id, schema_version, gate_batch_id,
                    stage_id, gate_id, policy_version, run_contract_fingerprint,
                    status, blocking, report_artifact_id,
                    report_artifact_revision, evaluation_event_id,
                    accepted_transaction_id, request_fingerprint, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.evaluation_id,
                    record.schema_version,
                    record.gate_batch_id,
                    record.stage_id,
                    record.gate_id,
                    record.policy_version,
                    record.run_contract_fingerprint,
                    record.status,
                    int(record.blocking),
                    record.report_artifact.artifact_id,
                    record.report_artifact.revision,
                    record.evaluation_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )

    def _insert_gate_findings(
        self,
        records: Iterable[GateFindingRecord],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO gate_findings(
                    run_id, evaluation_id, finding_id, schema_version, gate_id,
                    blocking_level, artifact_id, claim_id, source_id,
                    accepted_transaction_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.evaluation_id,
                    record.finding_id,
                    record.schema_version,
                    record.gate_id,
                    record.blocking_level,
                    record.artifact_id,
                    record.claim_id,
                    record.source_id,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )

    def _insert_gate_artifact_bindings(
        self,
        records: Iterable[GateArtifactBinding],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO gate_artifact_bindings(
                    run_id, evaluation_id, position, schema_version, artifact_id,
                    artifact_revision, artifact_sha256, usage,
                    accepted_transaction_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.evaluation_id,
                    record.position,
                    record.schema_version,
                    record.artifact_id,
                    record.artifact_revision,
                    record.artifact_sha256,
                    record.usage,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )

    def _insert_run_integrity_records(
        self,
        records: Iterable[RunIntegrityRecord],
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO run_integrity_records(
                    run_id, integrity_revision, schema_version, status,
                    prior_integrity_revision, affected_artifact_id,
                    affected_artifact_revision, expected_workspace_path,
                    expected_sha256, observed_entry_kind, observed_sha256,
                    reason_code, first_detected_event_id,
                    accepted_transaction_id, request_fingerprint, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.integrity_revision,
                    record.schema_version,
                    record.status,
                    record.prior_integrity_revision,
                    record.affected_artifact_id,
                    record.affected_artifact_revision,
                    record.expected_workspace_path,
                    record.expected_sha256,
                    record.observed_entry_kind,
                    record.observed_sha256,
                    record.reason_code,
                    record.first_detected_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )

    def _insert_gate_repair_records(self, uow: "ControlUnitOfWork") -> None:
        for record in uow._gate_repair_cycles.values():
            self._connection.execute(
                "INSERT INTO gate_repair_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.gate_repair_id,
                    record.schema_version,
                    record.authorization_id,
                    record.repair_ordinal,
                    record.source_gate_batch_id,
                    record.source_stage_id,
                    record.repair_owner,
                    record.target_artifact.artifact_id,
                    record.target_artifact.revision,
                    record.started_at,
                    record.start_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
            for position, evaluation_id in enumerate(record.blocking_evaluation_ids):
                self._connection.execute(
                    "INSERT INTO gate_repair_cycle_evaluations VALUES (?,?,?,?)",
                    (
                        record.run_id,
                        record.gate_repair_id,
                        position,
                        evaluation_id,
                    ),
                )

            for position, finding in enumerate(record.blocking_findings):
                self._connection.execute(
                    "INSERT INTO gate_repair_cycle_findings VALUES (?,?,?,?,?)",
                    (
                        record.run_id,
                        record.gate_repair_id,
                        position,
                        finding.evaluation_id,
                        finding.finding_id,
                    ),
                )
            for position, transition_id in enumerate(record.reopened_transition_ids):
                self._connection.execute(
                    "INSERT INTO gate_repair_cycle_transitions VALUES (?,?,?,?)",
                    (
                        record.run_id,
                        record.gate_repair_id,
                        position,
                        transition_id,
                    ),
                )
        for record in uow._gate_repair_artifact_bindings.values():
            self._connection.execute(
                "INSERT INTO gate_repair_artifact_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.gate_repair_id,
                    record.schema_version,
                    record.prior_artifact.artifact_id,
                    record.prior_artifact.revision,
                    record.successor_artifact.artifact_id,
                    record.successor_artifact.revision,
                    record.owned_artifact_submission_id,
                    record.accepted_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._gate_repair_outcomes.values():
            self._connection.execute(
                "INSERT INTO gate_repair_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.outcome_id,
                    record.schema_version,
                    record.gate_repair_id,
                    record.replacement_gate_batch_id,
                    record.replacement_stage_id,
                    record.disposition,
                    record.completed_at,
                    record.completion_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
            for position, evaluation_id in enumerate(record.evaluation_ids):
                self._connection.execute(
                    "INSERT INTO gate_repair_outcome_evaluations VALUES (?,?,?,?)",
                    (record.run_id, record.outcome_id, position, evaluation_id),
                )

    def _insert_runtime_source_search_plans(
        self, records: Iterable[RuntimeSourceSearchPlanV2]
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO runtime_source_search_plans(
                    run_id, search_plan_id, schema_version, plan_revision,
                    report_type, task_count, acquisition_spec_fingerprint,
                    plan_fingerprint, record_event_id, accepted_transaction_id,
                    created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.search_plan_id,
                    record.schema_version,
                    record.plan_revision,
                    record.report_type,
                    record.task_count,
                    record.acquisition_spec_fingerprint,
                    record.plan_fingerprint,
                    record.record_event_id,
                    record.accepted_transaction_id,
                    record.created_at,
                    _canonical_record_text(record),
                ),
            )

    def _insert_tavily_acquisition_bundle_records(
        self, records: Iterable[TavilyAcquisitionBundleRecordV2]
    ) -> None:
        for record in records:
            self._connection.execute(
                """
                INSERT INTO tavily_acquisition_bundle_records(
                    run_id, bundle_record_id, schema_version,
                    attempt_authorization_id, provider_response_artifact_id,
                    provider_response_sha256, bundle_status, search_count,
                    extract_batch_count, unique_url_count, durable_content_count,
                    record_fingerprint, record_event_id, accepted_transaction_id,
                    recorded_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.bundle_record_id,
                    record.schema_version,
                    record.attempt_authorization_id,
                    record.provider_response_artifact_id,
                    record.provider_response_sha256,
                    record.bundle_status,
                    record.search_count,
                    record.extract_batch_count,
                    record.unique_url_count,
                    record.durable_content_count,
                    record.record_fingerprint,
                    record.record_event_id,
                    record.accepted_transaction_id,
                    record.recorded_at,
                    _canonical_record_text(record),
                ),
            )

    def _insert_pr4b_records(self, uow: "ControlUnitOfWork") -> None:
        for record in uow._repair_cycles.values():
            self._connection.execute(
                "INSERT INTO repair_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.repair_id,
                    record.schema_version,
                    record.contamination_revision,
                    record.owner_stage_id,
                    record.reason_code,
                    record.started_at,
                    record.start_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._artifact_supersessions.values():
            self._connection.execute(
                "INSERT INTO artifact_supersessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.supersession_id,
                    record.repair_id,
                    record.mode,
                    record.schema_version,
                    record.prior_artifact.artifact_id,
                    record.prior_artifact.revision,
                    record.successor_artifact.revision,
                    record.reason_code,
                    record.created_at,
                    record.accepted_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._repair_completions.values():
            self._connection.execute(
                "INSERT INTO repair_completions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.repair_completion_id,
                    record.repair_id,
                    record.schema_version,
                    record.contamination_revision,
                    record.completed_at,
                    record.completion_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
            for position, value in enumerate(record.supersession_ids):
                self._connection.execute(
                    "INSERT INTO repair_completion_supersessions VALUES (?,?,?,?)",
                    (record.run_id, record.repair_completion_id, position, value),
                )
            for position, value in enumerate(record.reopened_transition_ids):
                self._connection.execute(
                    "INSERT INTO repair_completion_transitions VALUES (?,?,?,?)",
                    (record.run_id, record.repair_completion_id, position, value),
                )
        for record in uow._recovery_completions.values():
            self._connection.execute(
                "INSERT INTO recovery_completions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.recovery_id,
                    record.repair_completion_id,
                    record.schema_version,
                    record.contamination_revision,
                    record.disposition,
                    record.completed_at,
                    record.completion_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
            for table, values in (
                ("recovery_supersessions", record.supersession_ids),
                ("recovery_stage_transitions", record.rerun_transition_ids),
                ("recovery_gate_evaluations", record.gate_evaluation_ids),
            ):
                for position, value in enumerate(values):
                    self._connection.execute(
                        f"INSERT INTO {table} VALUES (?,?,?,?)",
                        (record.run_id, record.recovery_id, position, value),
                    )
        for record in uow._run_head_transitions.values():
            self._connection.execute(
                "INSERT INTO run_head_transitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.workspace_id,
                    record.head_transition_id,
                    record.successor_run_id,
                    record.predecessor_run_id,
                    record.schema_version,
                    record.prior_workspace_revision,
                    record.successor_workspace_revision,
                    record.reason_code,
                    record.successor_disposition,
                    record.created_at,
                    record.transition_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._finalize_renders.values():
            self._connection.execute(
                "INSERT INTO finalize_renders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.render_id,
                    record.schema_version,
                    record.audit_proposal_id,
                    record.audited_brief.artifact_id,
                    record.audited_brief.revision,
                    record.audit_report.artifact_id,
                    record.audit_report.revision,
                    record.reader_clean_status,
                    record.policy_result_fingerprint,
                    record.run_contract_fingerprint,
                    record.created_at,
                    record.render_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
            revisions = {
                (item.record.artifact_id, item.record.revision): item
                for item in uow._artifact_revisions
            }
            for position, reference in enumerate(record.reader_artifacts):
                revision = revisions.get((reference.artifact_id, reference.revision))
                if revision is None:
                    row = self._connection.execute(
                        "SELECT sha256 FROM artifact_revisions WHERE run_id=? AND artifact_id=? AND revision=?",
                        (record.run_id, reference.artifact_id, reference.revision),
                    ).fetchone()
                    if row is None:
                        raise ControlStoreConflict("relational_integrity_conflict")
                    digest = str(row[0])
                else:
                    digest = revision.record.sha256
                self._connection.execute(
                    "INSERT INTO finalize_render_artifacts VALUES (?,?,?,?,?,?)",
                    (
                        record.run_id,
                        record.render_id,
                        position,
                        reference.artifact_id,
                        reference.revision,
                        digest,
                    ),
                )
        for record in uow._finalizations.values():
            self._connection.execute(
                "INSERT INTO finalizations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.finalization_id,
                    record.schema_version,
                    record.render_id,
                    record.finalize_transition_id,
                    record.finalize_gate_batch_id,
                    record.recovery_id,
                    record.integrity_revision,
                    record.finalized_at,
                    record.finalization_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
            for position, value in enumerate(record.finalize_gate_evaluation_ids):
                self._connection.execute(
                    "INSERT INTO finalization_gate_evaluations VALUES (?,?,?,?)",
                    (record.run_id, record.finalization_id, position, value),
                )
        for record in uow._run_archives.values():
            self._connection.execute(
                "INSERT INTO run_archives VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.archive_id,
                    record.schema_version,
                    record.finalization_id,
                    record.archive_artifact.artifact_id,
                    record.archive_artifact.revision,
                    record.manifest_sha256,
                    record.included_count,
                    record.created_at,
                    record.archive_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._run_archive_artifact_bindings.values():
            self._connection.execute(
                "INSERT INTO run_archive_artifact_bindings VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.archive_id,
                    record.position,
                    record.schema_version,
                    record.artifact_id,
                    record.artifact_revision,
                    record.artifact_sha256,
                    record.usage,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._package_ready_records.values():
            self._connection.execute(
                "INSERT INTO package_ready_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.package_id,
                    record.schema_version,
                    record.finalization_id,
                    record.archive_id,
                    record.package_manifest_artifact.artifact_id,
                    record.package_manifest_artifact.revision,
                    record.package_manifest_sha256,
                    record.artifact_count,
                    record.created_at,
                    record.package_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._package_artifact_bindings.values():
            self._connection.execute(
                "INSERT INTO package_artifact_bindings VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.package_id,
                    record.position,
                    record.schema_version,
                    record.artifact_id,
                    record.artifact_revision,
                    record.artifact_sha256,
                    record.usage,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._approval_package_bindings.values():
            self._connection.execute(
                "INSERT INTO approval_package_bindings VALUES (?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.approval_id,
                    record.package_id,
                    record.schema_version,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._delivery_authorizations.values():
            self._connection.execute(
                "INSERT INTO delivery_authorizations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.authorization_id,
                    record.schema_version,
                    record.package_id,
                    record.prior_authorization_id,
                    record.approval_mode,
                    record.retry_of_attempt_id,
                    record.purpose,
                    record.decision,
                    record.target,
                    record.channel,
                    record.recipient_fingerprint,
                    record.actor_id,
                    record.recorded_at,
                    record.authorization_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._delivery_attempts.values():
            self._connection.execute(
                "INSERT INTO delivery_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.attempt_id,
                    record.schema_version,
                    record.package_id,
                    record.authorization_id,
                    record.target,
                    record.channel,
                    record.recipient_fingerprint,
                    record.connector_operation_id,
                    record.connector_request_fingerprint,
                    record.created_at,
                    record.attempt_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._delivery_results.values():
            evidence = record.evidence_artifact
            self._connection.execute(
                "INSERT INTO delivery_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.result_id,
                    record.schema_version,
                    record.attempt_id,
                    record.prior_result_id,
                    record.reconciliation_authorization_id,
                    record.status,
                    record.adapter_id,
                    record.adapter_version,
                    record.connector_operation_id,
                    record.evidence_sha256,
                    evidence.artifact_id if evidence else None,
                    evidence.revision if evidence else None,
                    record.recorded_at,
                    record.result_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )

    def _insert_post_final_assessment_records(self, uow: "ControlUnitOfWork") -> None:
        """Insert the sole Store-owned PF-LAJ advisory lifecycle records."""

        for record in uow._post_final_assessment_policy_revisions.values():
            self._connection.execute(
                """
                INSERT INTO post_final_assessment_policy_revisions VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.run_id,
                    record.policy_revision_id,
                    record.schema_version,
                    record.previous_policy_revision_id,
                    int(record.enabled),
                    int(record.auto_run),
                    int(record.auto_open),
                    record.adapter_id,
                    record.messages_endpoint_sha256,
                    record.requested_model_id,
                    record.profile_id,
                    record.human_request_id,
                    record.policy_fingerprint,
                    record.recorded_at,
                    record.policy_event_id,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._post_final_assessment_requests.values():
            self._connection.execute(
                """
                INSERT INTO post_final_assessment_requests VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.run_id,
                    record.assessment_request_id,
                    record.schema_version,
                    record.finalized_facts_fingerprint,
                    record.finalized_lineage_fingerprint,
                    record.policy_revision_id,
                    record.trial_id,
                    record.archive_identity_sha256,
                    record.request_fingerprint,
                    record.claimed_at,
                    record.request_event_id,
                    record.accepted_transaction_id,
                    record.assessment_generation,
                    record.predecessor_assessment_request_id,
                    record.predecessor_assessment_request_fingerprint,
                    record.predecessor_assessment_result_id,
                    record.predecessor_result_fingerprint,
                    record.predecessor_abandonment_id,
                    record.predecessor_abandonment_fingerprint,
                    record.assessment_purpose,
                    record.human_actor_id,
                    record.human_request_id,
                    record.authorization_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._post_final_assessment_abandonments.values():
            self._connection.execute(
                """
                INSERT INTO post_final_assessment_abandonments VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.run_id,
                    record.abandonment_id,
                    record.schema_version,
                    record.assessment_request_id,
                    record.assessment_request_fingerprint,
                    record.finalized_lineage_fingerprint,
                    record.assessment_generation,
                    record.reason,
                    record.human_actor_id,
                    record.human_request_id,
                    record.expected_store_revision,
                    record.abandonment_fingerprint,
                    record.recorded_at,
                    record.abandonment_event_id,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._post_final_assessment_executions.values():
            self._connection.execute(
                """
                INSERT INTO post_final_assessment_executions VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.run_id,
                    record.execution_id,
                    record.schema_version,
                    record.assessment_request_id,
                    record.trial_id,
                    record.execution_archive_manifest_sha256,
                    record.execution_receipt_id,
                    record.execution_status,
                    record.run_status,
                    record.validation_status,
                    record.reason_codes_json,
                    record.execution_fingerprint,
                    record.recorded_at,
                    record.execution_event_id,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._post_final_assessment_results.values():
            self._connection.execute(
                """
                INSERT INTO post_final_assessment_results VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.run_id,
                    record.assessment_result_id,
                    record.schema_version,
                    record.assessment_request_id,
                    record.policy_revision_id,
                    record.finalized_facts_fingerprint,
                    record.finalized_lineage_fingerprint,
                    record.terminal_evidence_class,
                    record.result_fingerprint,
                    record.recorded_at,
                    record.result_event_id,
                    record.accepted_transaction_id,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._post_final_finding_dispositions.values():
            self._connection.execute(
                "INSERT INTO post_final_finding_dispositions VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.disposition_id,
                    record.schema_version,
                    record.finalized_lineage_fingerprint,
                    record.assessment_result_id,
                    record.assessment_result_fingerprint,
                    record.reader_view_sha256,
                    record.finding_id,
                    record.finding_fingerprint,
                    record.previous_disposition_id,
                    record.decision,
                    record.human_note,
                    record.human_actor_id,
                    record.human_request_id,
                    record.recorded_at,
                    record.disposition_event_id,
                    record.accepted_transaction_id,
                    record.disposition_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._post_final_human_observations.values():
            self._connection.execute(
                "INSERT INTO post_final_human_observations VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.observation_id,
                    record.schema_version,
                    record.origin,
                    record.observation_revision,
                    record.finalized_lineage_fingerprint,
                    record.report_revision,
                    record.report_artifact_id,
                    record.report_sha256,
                    record.assessment_result_id,
                    record.assessment_result_fingerprint,
                    record.reader_view_sha256,
                    record.observation_text,
                    record.observation_sha256,
                    record.requirement_id,
                    record.claim_id,
                    (
                        "null"
                        if record.report_span is None
                        else canonical_json_bytes(
                            record.report_span.model_dump(mode="json")
                        ).decode("utf-8")
                    ),
                    record.scope_class,
                    record.dimension_id,
                    record.previous_observation_id,
                    record.previous_observation_fingerprint,
                    record.human_actor_id,
                    record.human_request_id,
                    record.recorded_at,
                    record.observation_event_id,
                    record.accepted_transaction_id,
                    record.observation_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._post_final_guidance_drafts.values():
            self._connection.execute(
                "INSERT INTO post_final_guidance_drafts VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.guidance_id,
                    record.draft_revision,
                    record.schema_version,
                    record.finalized_lineage_fingerprint,
                    record.provenance_kind,
                    record.assessment_result_id,
                    record.assessment_result_fingerprint,
                    record.finding_id,
                    record.finding_fingerprint,
                    record.disposition_id,
                    record.disposition_fingerprint,
                    record.observation_id,
                    record.observation_fingerprint,
                    record.previous_draft_revision,
                    record.guidance_scope,
                    record.guidance_text,
                    record.guidance_sha256,
                    record.human_actor_id,
                    record.human_request_id,
                    record.recorded_at,
                    record.draft_event_id,
                    record.accepted_transaction_id,
                    record.draft_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._post_final_guidance_statuses.values():
            self._connection.execute(
                "INSERT INTO post_final_guidance_statuses VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.status_revision_id,
                    record.schema_version,
                    record.finalized_lineage_fingerprint,
                    record.guidance_id,
                    record.draft_revision,
                    record.guidance_sha256,
                    record.status,
                    record.previous_status_revision_id,
                    record.human_actor_id,
                    record.human_request_id,
                    record.recorded_at,
                    record.status_event_id,
                    record.accepted_transaction_id,
                    record.status_fingerprint,
                    _canonical_record_text(record),
                ),
            )

    def _insert_guidance_snapshot_records(self, uow: "ControlUnitOfWork") -> None:
        """Insert the immutable successor guidance snapshot graph."""

        for record in uow._run_guidance_snapshots.values():
            self._connection.execute(
                "INSERT INTO run_guidance_snapshots VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.snapshot_id,
                    record.workspace_id,
                    record.predecessor_run_id,
                    record.schema_version,
                    int(record.reuse_requested),
                    record.successor_direction_fingerprint,
                    record.successor_run_contract_fingerprint,
                    record.candidate_set_fingerprint,
                    record.selected_count,
                    record.omitted_count,
                    record.snapshot_fingerprint,
                    record.snapshot_event_id,
                    record.accepted_transaction_id,
                    record.request_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._run_guidance_selection_decisions.values():
            self._connection.execute(
                "INSERT INTO run_guidance_selection_decisions VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.decision_id,
                    record.snapshot_id,
                    record.source_run_id,
                    record.schema_version,
                    record.guidance_id,
                    record.draft_revision,
                    record.status_revision_id,
                    record.provenance_kind,
                    record.assessment_result_id,
                    record.finding_id,
                    record.disposition_id,
                    record.result_fingerprint,
                    record.finding_fingerprint,
                    record.disposition_fingerprint,
                    record.observation_id,
                    record.observation_fingerprint,
                    record.draft_fingerprint,
                    record.status_fingerprint,
                    record.source_scope_fingerprint,
                    record.successor_scope_fingerprint,
                    int(record.selected),
                    record.reason_code,
                    record.decision_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._run_guidance_snapshot_items.values():
            self._connection.execute(
                "INSERT INTO run_guidance_snapshot_items VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.item_id,
                    record.snapshot_id,
                    record.position,
                    record.source_run_id,
                    record.schema_version,
                    record.finalized_lineage_fingerprint,
                    record.provenance_kind,
                    record.assessment_result_id,
                    record.assessment_result_fingerprint,
                    record.finding_id,
                    record.finding_fingerprint,
                    record.disposition_id,
                    record.disposition_fingerprint,
                    record.observation_id,
                    record.observation_fingerprint,
                    record.guidance_id,
                    record.draft_revision,
                    record.draft_fingerprint,
                    record.status_revision_id,
                    record.status_fingerprint,
                    record.guidance_text,
                    record.guidance_sha256,
                    record.reuse_scope.scope_fingerprint,
                    record.item_fingerprint,
                    _canonical_record_text(record),
                ),
            )
        for snapshot in uow._run_guidance_snapshots.values():
            for position, decision_id in enumerate(snapshot.decision_ids):
                self._connection.execute(
                    "INSERT INTO run_guidance_snapshot_decisions VALUES (?,?,?,?)",
                    (snapshot.run_id, snapshot.snapshot_id, position, decision_id),
                )
            for position, item_id in enumerate(snapshot.selected_item_ids):
                self._connection.execute(
                    "INSERT INTO run_guidance_snapshot_selected_items VALUES (?,?,?,?)",
                    (snapshot.run_id, snapshot.snapshot_id, position, item_id),
                )

    def _insert_checkout_records(self, uow: "ControlUnitOfWork") -> None:
        for record in uow._checkout_revisions.values():
            self._connection.execute(
                "INSERT INTO checkout_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.checkout_revision_id,
                    record.workspace_id,
                    record.run_id,
                    record.parent_checkout_revision_id,
                    record.schema_version,
                    record.manifest_sha256,
                    record.tree_sha256,
                    record.member_count,
                    record.created_at,
                    record.creator_transaction_id,
                    _canonical_record_text(record),
                ),
            )
        for record in uow._checkout_revision_members.values():
            self._connection.execute(
                "INSERT INTO checkout_revision_members VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.checkout_revision_id,
                    record.ordinal,
                    record.workspace_id,
                    record.run_id,
                    record.schema_version,
                    record.canonical_path,
                    record.artifact_id,
                    record.artifact_revision,
                    record.blob_sha256,
                    record.byte_size,
                    _canonical_record_text(record),
                ),
            )
        binding = uow._receipt_checkout_binding
        if binding is not None:
            self._connection.execute(
                "INSERT INTO receipt_checkout_bindings VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    binding.workspace_id,
                    binding.run_id,
                    binding.transaction_id,
                    binding.schema_version,
                    binding.pre_run_id,
                    binding.pre_checkout_revision_id,
                    binding.post_run_id,
                    binding.post_checkout_revision_id,
                    _canonical_record_text(binding),
                ),
            )
        intent = uow._checkout_publication_intent
        if intent is not None:
            identity = intent.identity
            self._connection.execute(
                "INSERT INTO checkout_publication_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity.workspace_id,
                    identity.run_id,
                    identity.transaction_id,
                    identity.checkout_revision_id,
                    intent.schema_version,
                    intent.publication_identity_sha256,
                    intent.pre_checkout_revision_id,
                    intent.post_checkout_revision_id,
                    intent.post_manifest_sha256,
                    intent.post_tree_sha256,
                    intent.changed_member_count,
                    intent.capability_profile_sha256,
                    _canonical_record_text(intent),
                ),
            )
        for record in uow._checkout_publication_members.values():
            identity = record.identity
            self._connection.execute(
                "INSERT INTO checkout_publication_members VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity.workspace_id,
                    identity.run_id,
                    identity.transaction_id,
                    identity.checkout_revision_id,
                    record.ordinal,
                    record.schema_version,
                    record.canonical_path,
                    record.temporary_basename,
                    record.claim_basename,
                    record.pre_kind,
                    record.pre_sha256,
                    record.pre_size,
                    record.post_kind,
                    record.post_sha256,
                    record.post_size,
                    _canonical_record_text(record),
                ),
            )

    def _insert_transaction_relations(self, receipt: TransactionReceipt) -> None:
        for position, event_id in enumerate(receipt.event_ids):
            self._connection.execute(
                """
                INSERT INTO transaction_events(
                    run_id, transaction_id, position, event_id
                ) VALUES (?, ?, ?, ?)
                """,
                (receipt.run_id, receipt.transaction_id, position, event_id),
            )
        for position, reference in enumerate(receipt.artifact_revisions):
            self._connection.execute(
                """
                INSERT INTO transaction_artifact_revisions(
                    run_id, transaction_id, position, artifact_id, revision
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.artifact_id,
                    reference.revision,
                ),
            )
        for position, reference in enumerate(receipt.artifact_identities):
            self._connection.execute(
                """
                INSERT INTO transaction_artifact_identities(
                    run_id, transaction_id, position, artifact_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.artifact_id,
                ),
            )
        for position, source_id in enumerate(receipt.source_ids):
            self._connection.execute(
                """
                INSERT INTO transaction_sources(
                    run_id, transaction_id, position, source_id
                ) VALUES (?, ?, ?, ?)
                """,
                (receipt.run_id, receipt.transaction_id, position, source_id),
            )
        for position, proposal_id in enumerate(receipt.proposal_ids):
            self._connection.execute(
                """
                INSERT INTO transaction_proposals(
                    run_id, transaction_id, position, proposal_id
                ) VALUES (?, ?, ?, ?)
                """,
                (receipt.run_id, receipt.transaction_id, position, proposal_id),
            )
        for position, reference in enumerate(receipt.run_contract_bindings):
            self._connection.execute(
                """
                INSERT INTO transaction_run_contract_bindings(
                    run_id, transaction_id, position, binding_run_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.run_id,
                ),
            )
        for position, reference in enumerate(receipt.run_execution_authorizations):
            self._connection.execute(
                """
                INSERT INTO transaction_run_execution_authorizations(
                    run_id, transaction_id, position, authorization_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.authorization_id,
                ),
            )
        for position, reference in enumerate(
            receipt.run_source_discovery_authorizations
        ):
            self._connection.execute(
                """
                INSERT INTO transaction_run_source_discovery_authorizations(
                    run_id, transaction_id, position, authorization_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.authorization_id,
                ),
            )
        for position, reference in enumerate(
            receipt.run_source_acquisition_attempt_authorizations
        ):
            self._connection.execute(
                """
                INSERT INTO transaction_run_source_acquisition_attempt_authorizations_v2(
                    run_id, transaction_id, position, attempt_authorization_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.attempt_authorization_id,
                ),
            )
        for position, reference in enumerate(receipt.owned_artifact_submissions):
            self._connection.execute(
                """
                INSERT INTO transaction_owned_artifact_submissions(
                    run_id, transaction_id, position, submission_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.submission_id,
                ),
            )
        for position, reference in enumerate(receipt.stage_transitions):
            self._connection.execute(
                """
                INSERT INTO transaction_stage_transitions(
                    run_id, transaction_id, position, transition_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.transition_id,
                ),
            )
        for position, reference in enumerate(receipt.stage_artifact_bindings):
            self._connection.execute(
                """
                INSERT INTO transaction_stage_artifact_bindings(
                    run_id, transaction_id, position, transition_id,
                    binding_position
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.transition_id,
                    reference.position,
                ),
            )
        for position, reference in enumerate(receipt.stage_gate_bindings):
            self._connection.execute(
                """
                INSERT INTO transaction_stage_gate_bindings(
                    run_id, transaction_id, position, transition_id, gate_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.transition_id,
                    reference.gate_id,
                ),
            )
        for position, reference in enumerate(receipt.claims):
            self._connection.execute(
                """
                INSERT INTO transaction_claims(
                    run_id, transaction_id, position, claim_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.claim_id,
                ),
            )
        for position, reference in enumerate(receipt.claim_source_bindings):
            self._connection.execute(
                """
                INSERT INTO transaction_claim_source_bindings(
                    run_id, transaction_id, position, claim_id, source_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.claim_id,
                    reference.source_id,
                ),
            )
        for position, reference in enumerate(receipt.claim_freezes):
            self._connection.execute(
                """
                INSERT INTO transaction_claim_freezes(
                    run_id, transaction_id, position, freeze_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.freeze_id,
                ),
            )
        for position, reference in enumerate(receipt.gate_evaluations):
            self._connection.execute(
                """
                INSERT INTO transaction_gate_evaluations(
                    run_id, transaction_id, position, evaluation_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.evaluation_id,
                ),
            )
        for position, reference in enumerate(receipt.gate_findings):
            self._connection.execute(
                """
                INSERT INTO transaction_gate_findings(
                    run_id, transaction_id, position, evaluation_id, finding_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.evaluation_id,
                    reference.finding_id,
                ),
            )
        for position, reference in enumerate(receipt.gate_artifact_bindings):
            self._connection.execute(
                """
                INSERT INTO transaction_gate_artifact_bindings(
                    run_id, transaction_id, position, evaluation_id,
                    binding_position
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.evaluation_id,
                    reference.position,
                ),
            )
        for position, reference in enumerate(receipt.run_integrity_records):
            self._connection.execute(
                """
                INSERT INTO transaction_run_integrity_records(
                    run_id, transaction_id, position, integrity_revision
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.integrity_revision,
                ),
            )
        simple_relations = (
            ("transaction_repair_cycles", receipt.repair_cycles, "repair_id"),
            (
                "transaction_gate_repair_cycles",
                receipt.gate_repair_cycles,
                "gate_repair_id",
            ),
            (
                "transaction_gate_repair_artifact_bindings",
                receipt.gate_repair_artifact_bindings,
                "gate_repair_id",
            ),
            (
                "transaction_gate_repair_outcomes",
                receipt.gate_repair_outcomes,
                "outcome_id",
            ),
            (
                "transaction_artifact_supersessions",
                receipt.artifact_supersessions,
                "supersession_id",
            ),
            (
                "transaction_repair_completions",
                receipt.repair_completions,
                "repair_completion_id",
            ),
            (
                "transaction_recovery_completions",
                receipt.recovery_completions,
                "recovery_id",
            ),
            (
                "transaction_run_head_transitions",
                receipt.run_head_transitions,
                "head_transition_id",
            ),
            ("transaction_finalize_renders", receipt.finalize_renders, "render_id"),
            ("transaction_finalizations", receipt.finalizations, "finalization_id"),
            ("transaction_run_archives", receipt.run_archives, "archive_id"),
            (
                "transaction_package_ready_records",
                receipt.package_ready_records,
                "package_id",
            ),
            ("transaction_approvals", receipt.approvals, "approval_id"),
            (
                "transaction_delivery_authorizations",
                receipt.delivery_authorizations,
                "authorization_id",
            ),
            ("transaction_delivery_attempts", receipt.delivery_attempts, "attempt_id"),
            ("transaction_delivery_results", receipt.delivery_results, "result_id"),
            (
                "transaction_post_final_assessment_policy_revisions",
                receipt.post_final_assessment_policy_revisions,
                "policy_revision_id",
            ),
            (
                "transaction_post_final_assessment_requests",
                receipt.post_final_assessment_requests,
                "assessment_request_id",
            ),
            (
                "transaction_post_final_assessment_abandonments",
                receipt.post_final_assessment_abandonments,
                "abandonment_id",
            ),
            (
                "transaction_post_final_assessment_results",
                receipt.post_final_assessment_results,
                "assessment_result_id",
            ),
            (
                "transaction_post_final_finding_dispositions",
                receipt.post_final_finding_dispositions,
                "disposition_id",
            ),
            (
                "transaction_post_final_human_observations",
                receipt.post_final_human_observations,
                "observation_id",
            ),
            (
                "transaction_post_final_guidance_statuses",
                receipt.post_final_guidance_statuses,
                "status_revision_id",
            ),
            (
                "transaction_run_guidance_snapshots",
                receipt.run_guidance_snapshots,
                "snapshot_id",
            ),
            (
                "transaction_run_guidance_selection_decisions",
                receipt.run_guidance_selection_decisions,
                "decision_id",
            ),
            (
                "transaction_run_guidance_snapshot_items",
                receipt.run_guidance_snapshot_items,
                "item_id",
            ),
        )
        for table, references, field in simple_relations:
            for position, reference in enumerate(references):
                self._connection.execute(
                    f"INSERT INTO {table} VALUES (?,?,?,?)",
                    (
                        receipt.run_id,
                        receipt.transaction_id,
                        position,
                        getattr(reference, field),
                    ),
                )
        for position, reference in enumerate(receipt.post_final_guidance_drafts):
            self._connection.execute(
                "INSERT INTO transaction_post_final_guidance_drafts VALUES (?,?,?,?,?)",
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.guidance_id,
                    reference.draft_revision,
                ),
            )
        for table, references, identity_field in (
            (
                "transaction_run_archive_artifact_bindings",
                receipt.run_archive_artifact_bindings,
                "archive_id",
            ),
            (
                "transaction_package_artifact_bindings",
                receipt.package_artifact_bindings,
                "package_id",
            ),
        ):
            for position, reference in enumerate(references):
                self._connection.execute(
                    f"INSERT INTO {table} VALUES (?,?,?,?,?)",
                    (
                        receipt.run_id,
                        receipt.transaction_id,
                        position,
                        getattr(reference, identity_field),
                        reference.position,
                    ),
                )
        for position, reference in enumerate(receipt.approval_package_bindings):
            self._connection.execute(
                "INSERT INTO transaction_approval_package_bindings VALUES (?,?,?,?,?)",
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.approval_id,
                    reference.package_id,
                ),
            )
        for position, reference in enumerate(receipt.checkout_revisions):
            self._connection.execute(
                "INSERT INTO transaction_checkout_revisions VALUES (?,?,?,?)",
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.checkout_revision_id,
                ),
            )
        for position, reference in enumerate(receipt.receipt_checkout_bindings):
            self._connection.execute(
                "INSERT INTO transaction_receipt_checkout_bindings VALUES (?,?,?,?)",
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.transaction_id,
                ),
            )
        for position, reference in enumerate(receipt.checkout_publication_intents):
            self._connection.execute(
                "INSERT INTO transaction_checkout_publication_intents VALUES (?,?,?,?)",
                (
                    receipt.run_id,
                    receipt.transaction_id,
                    position,
                    reference.checkout_revision_id,
                ),
            )

    def _blob_relpath(self, sha256: str) -> str:
        return f"sha256/{sha256[:2]}/{sha256}"

    def _blob_path(self, sha256: str) -> Path:
        return self.blob_root.joinpath(*self._blob_relpath(sha256).split("/"))

    def _workspace_blob_path(self, sha256: str) -> str:
        # PR-3's fresh workspace contract fixes the logical accepted-byte path.
        # Backup/restore may use a different physical blob root while retaining
        # the same immutable workspace-relative record.
        return f"briefloop.db.blobs/{self._blob_relpath(sha256)}"

    def _write_blob(self, record: ArtifactRevision, content: bytes) -> None:
        destination = self._blob_path(record.sha256)
        existing = _validate_blob_topology(
            self.blob_root,
            error_code="blob_topology_invalid",
            blob_path=destination,
            allow_missing_directories=True,
        )
        if existing:
            self._verify_blob(record, destination)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        _validate_blob_topology(
            self.blob_root,
            error_code="blob_topology_invalid",
            blob_path=destination,
        )
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if sha256_hex(temporary.read_bytes()) != record.sha256:
                raise ControlStoreIntegrityError("artifact_blob_hash_mismatch")
            os.replace(temporary, destination)
            if os.name != "nt":
                directory_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except ControlStoreError:
            raise
        except OSError as exc:
            raise ControlStoreIntegrityError("artifact_blob_write_failed") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        self._verify_blob(record, destination)

    def _verify_blob(self, record: ArtifactRevision, path: Path) -> None:
        _validate_blob_topology(
            self.blob_root,
            error_code="blob_topology_invalid",
            blob_path=path,
            require_blob=True,
            missing_blob_error_code="committed_blob_missing",
        )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ControlStoreIntegrityError("committed_blob_unreadable") from exc
        if len(content) != record.size_bytes:
            raise ControlStoreIntegrityError("committed_blob_size_mismatch")
        if sha256_hex(content) != record.sha256:
            raise ControlStoreIntegrityError("committed_blob_hash_mismatch")

    def _verify_committed_blob_bindings(self, run_id: str | None = None) -> None:
        sql = (
            "SELECT run_id, sha256, blob_relpath, payload_json FROM artifact_revisions"
        )
        parameters: tuple[object, ...] = ()
        if run_id is not None:
            sql += " WHERE run_id = ?"
            parameters = (run_id,)
        sql += " ORDER BY run_id, artifact_id, revision"
        for row in self._connection.execute(sql, parameters).fetchall():
            record = decode_model(ArtifactRevision, str(row[3]))
            if row[0] != record.run_id or row[1] != record.sha256:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
            expected = self._blob_relpath(record.sha256)
            if row[2] != expected:
                raise ControlStoreIntegrityError("blob_binding_invalid")
            self._verify_blob(record, self._blob_path(record.sha256))

    def _verify_receipt_blobs(self, receipt: TransactionReceipt) -> None:
        for reference in receipt.artifact_revisions:
            row = self._connection.execute(
                """
                SELECT payload_json FROM artifact_revisions
                WHERE run_id = ? AND artifact_id = ? AND revision = ?
                """,
                (receipt.run_id, reference.artifact_id, reference.revision),
            ).fetchone()
            if row is None:
                raise ControlStoreIntegrityError("transaction_relation_mismatch")
            record = decode_model(ArtifactRevision, str(row[0]))
            self._verify_blob(record, self._blob_path(record.sha256))

    def _verify_baseline_and_existing_receipt(
        self,
        run_id: str,
        transaction_id: str,
        fingerprint: str,
    ) -> TransactionReceipt | None:
        try:
            self._connection.execute("BEGIN")
            self._verify_all_payloads_in_transaction()
            receipt = self._existing_receipt(run_id, transaction_id, fingerprint)
            self._connection.commit()
            return receipt
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise ControlStoreIntegrityError("sqlite_read_failed") from exc
        except Exception:
            self._connection.rollback()
            raise

    def _verify_all_payloads(self) -> None:
        try:
            self._connection.execute("BEGIN")
            self._verify_all_payloads_in_transaction()
            self._connection.commit()
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise ControlStoreIntegrityError("sqlite_read_failed") from exc
        except Exception:
            self._connection.rollback()
            raise

    def _verify_all_payloads_in_transaction(self) -> None:
        verify_schema(self._connection)
        self._verify_committed_blob_bindings()
        self._verify_workspace_ledger_graph()
        run_ids = [
            str(row[0])
            for row in self._connection.execute(
                "SELECT run_id FROM runs ORDER BY run_id"
            ).fetchall()
        ]
        for run_id in run_ids:
            self._load_snapshot_in_transaction(run_id)

    def load_snapshot(self, run_id: str) -> ControlStoreSnapshot:
        with self._lock:
            self._require_open()
            if type(run_id) is not str or not run_id:
                raise ControlStoreIntegrityError("run_id_invalid")
            try:
                self._connection.execute("BEGIN")
                verify_schema(self._connection)
                self._verify_committed_blob_bindings()
                self._verify_workspace_ledger_graph()
                snapshot = self._load_snapshot_in_transaction(run_id)
                self._connection.commit()
                return snapshot
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def load_history(self) -> ControlStoreHistory:
        """Load every run and committed blob through one SQLite read snapshot."""

        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN")
                verify_schema(self._connection)
                self._verify_committed_blob_bindings()
                self._verify_workspace_ledger_graph()
                run_ids = tuple(
                    str(row[0])
                    for row in self._connection.execute(
                        "SELECT run_id FROM runs ORDER BY run_id"
                    ).fetchall()
                )
                snapshots = tuple(
                    self._load_snapshot_in_transaction(run_id) for run_id in run_ids
                )
                contents: dict[tuple[str, str, int], bytes] = {}
                for snapshot in snapshots:
                    for revision in snapshot.artifact_revisions:
                        path = self._blob_path(revision.sha256)
                        self._verify_blob(revision, path)
                        try:
                            contents[
                                (
                                    revision.run_id,
                                    revision.artifact_id,
                                    revision.revision,
                                )
                            ] = path.read_bytes()
                        except OSError as exc:
                            raise ControlStoreIntegrityError(
                                "blob_read_failed"
                            ) from exc
                history = ControlStoreHistory(
                    workspace_id=self.workspace_id,
                    store_revision=self.current_revision,
                    snapshots=snapshots,
                    artifact_contents=MappingProxyType(contents),
                )
                self._connection.commit()
                return history
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def load_workspace_run_head(self) -> WorkspaceRunHead | None:
        """Return the explicit workspace head after full Store verification."""

        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN")
                self._verify_all_payloads_in_transaction()
                head = self._load_workspace_run_head_in_transaction()
                self._connection.commit()
                return head
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def load_transaction_receipt(
        self,
        run_id: str,
        transaction_id: str,
    ) -> TransactionReceipt | None:
        """Load one receipt without inferring a current run or replay intent."""

        run_id = _validate_contract_id(run_id, "transaction_identity_invalid")
        transaction_id = _validate_contract_id(
            transaction_id,
            "transaction_identity_invalid",
        )
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN")
                self._verify_all_payloads_in_transaction()
                row = self._connection.execute(
                    """
                    SELECT * FROM transactions
                    WHERE run_id = ? AND transaction_id = ?
                    """,
                    (run_id, transaction_id),
                ).fetchone()
                receipt = None if row is None else self._decode_transaction_row(row)
                if receipt is not None:
                    self._verify_transaction_relations(receipt)
                    self._verify_receipt_blobs(receipt)
                self._connection.commit()
                return receipt
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def find_invocation_run_ids(self, invocation_id: str) -> tuple[str, ...]:
        """Return exact run bindings for one invocation after Store verification."""

        invocation_id = _validate_contract_id(
            invocation_id,
            "invocation_identity_invalid",
        )
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN")
                self._verify_all_payloads_in_transaction()
                rows = self._connection.execute(
                    """
                    SELECT run_id FROM agent_invocations
                    WHERE invocation_id = ? ORDER BY run_id
                    """,
                    (invocation_id,),
                ).fetchall()
                run_ids = tuple(str(row[0]) for row in rows)
                self._connection.commit()
                return run_ids
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def read_artifact_revision_bytes(
        self,
        run_id: str,
        artifact_id: str,
        revision: int,
    ) -> bytes:
        """Read bytes only through one verified artifact-revision binding."""

        run_id = _validate_contract_id(run_id, "artifact_identity_invalid")
        artifact_id = _validate_contract_id(
            artifact_id,
            "artifact_identity_invalid",
        )
        if type(revision) is not int or revision <= 0:
            raise ControlStoreIntegrityError("artifact_identity_invalid")
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN")
                self._verify_all_payloads_in_transaction()
                row = self._connection.execute(
                    """
                    SELECT * FROM artifact_revisions
                    WHERE run_id = ? AND artifact_id = ? AND revision = ?
                    """,
                    (run_id, artifact_id, revision),
                ).fetchone()
                if row is None:
                    raise ControlStoreStateError("artifact_revision_not_found")
                record = self._decode_checked(
                    ArtifactRevision,
                    row,
                    {
                        "run_id": "run_id",
                        "artifact_id": "artifact_id",
                        "revision": "revision",
                        "schema_version": "schema_version",
                        "path": "path",
                        "sha256": "sha256",
                        "size_bytes": "size_bytes",
                        "frozen": "frozen",
                        "producer_kind": "producer_kind",
                        "producer_id": "producer_id",
                        "created_at": "created_at",
                    },
                )
                path = self._blob_path(record.sha256)
                self._verify_blob(record, path)
                try:
                    content = path.read_bytes()
                except OSError as exc:
                    raise ControlStoreIntegrityError("blob_read_failed") from exc
                self._connection.commit()
                return content
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ControlStoreIntegrityError("sqlite_read_failed") from exc
            except Exception:
                self._connection.rollback()
                raise

    def _load_snapshot_in_transaction(
        self,
        run_id: str,
        *,
        _verify_guidance: bool = True,
    ) -> ControlStoreSnapshot:
        run_rows = self._connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        if len(run_rows) != 1:
            raise ControlStoreStateError("run_not_found")
        run = self._decode_checked(
            RunIdentity,
            run_rows[0],
            {
                "run_id": "run_id",
                "workspace_id": "workspace_id",
                "schema_version": "schema_version",
                "runtime": "runtime",
                "created_at": "created_at",
            },
        )
        snapshot = ControlStoreSnapshot(
            workspace_id=self.workspace_id,
            store_revision=self.current_revision,
            run=run,
            workspace_run_head=self._load_workspace_run_head_in_transaction(),
            stage_states=self._load_for_run(
                StageState,
                "stage_states",
                run_id,
                "stage_id",
                {
                    "run_id": "run_id",
                    "stage_id": "stage_id",
                    "schema_version": "schema_version",
                    "status": "status",
                    "revision": "revision",
                    "updated_at": "updated_at",
                },
            ),
            invocations=self._load_for_run(
                Invocation,
                "agent_invocations",
                run_id,
                "invocation_id",
                {
                    "run_id": "run_id",
                    "invocation_id": "invocation_id",
                    "schema_version": "schema_version",
                    "role_id": "role_id",
                    "runtime": "runtime",
                    "status": "status",
                    "started_at": "started_at",
                    "completed_at": "completed_at",
                    "failure_reason": "failure_reason",
                },
            ),
            artifacts=self._load_for_run(
                ArtifactRecord,
                "artifacts",
                run_id,
                "artifact_id",
                {
                    "run_id": "run_id",
                    "artifact_id": "artifact_id",
                    "schema_version": "schema_version",
                    "current_revision": "current_revision",
                    "status": "status",
                    "required": "required",
                    "path": "path",
                    "format": "format",
                },
            ),
            artifact_identities=self._load_for_run(
                ArtifactIdentityRecord,
                "artifact_identities",
                run_id,
                "artifact_id",
                {
                    "run_id": "run_id",
                    "artifact_id": "artifact_id",
                    "schema_version": "schema_version",
                    "required": "required",
                    "initial_path": "initial_path",
                    "format": "format",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            artifact_revisions=self._load_for_run(
                ArtifactRevision,
                "artifact_revisions",
                run_id,
                "artifact_id, revision",
                {
                    "run_id": "run_id",
                    "artifact_id": "artifact_id",
                    "revision": "revision",
                    "schema_version": "schema_version",
                    "path": "path",
                    "sha256": "sha256",
                    "size_bytes": "size_bytes",
                    "frozen": "frozen",
                    "producer_kind": "producer_kind",
                    "producer_id": "producer_id",
                    "created_at": "created_at",
                },
            ),
            events=self._load_for_run(
                EventEnvelope,
                "events",
                run_id,
                "created_at, event_id",
                {
                    "run_id": "run_id",
                    "event_id": "event_id",
                    "schema_version": "schema_version",
                    "event_type": "event_type",
                    "created_at": "created_at",
                    "actor": "actor",
                    "transaction_id": "transaction_id",
                    "stage_id": "stage_id",
                    "artifact_id": "artifact_id",
                    "decision": "decision",
                    "reason": "reason",
                },
            ),
            approvals=self._load_for_run(
                Approval,
                "approvals",
                run_id,
                "recorded_at, approval_id",
                {
                    "run_id": "run_id",
                    "approval_id": "approval_id",
                    "schema_version": "schema_version",
                    "mode": "mode",
                    "role": "role",
                    "decision": "decision",
                    "reason": "reason",
                    "actor_id": "actor_id",
                    "recorded_at": "recorded_at",
                    "boundary": "boundary",
                    "event_id": "event_id",
                },
            ),
            deliveries=self._load_for_run(
                Delivery,
                "deliveries",
                run_id,
                "delivery_id",
                {
                    "run_id": "run_id",
                    "delivery_id": "delivery_id",
                    "schema_version": "schema_version",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "approval_id": "approval_id",
                    "status": "status",
                    "target": "target",
                    "channel": "channel",
                    "created_at": "created_at",
                    "completed_at": "completed_at",
                },
            ),
            sources=self._load_for_run(
                AcceptedSourceRecord,
                "sources",
                run_id,
                "source_id",
                {
                    "run_id": "run_id",
                    "source_id": "source_id",
                    "schema_version": "schema_version",
                    "origin_type": "origin_type",
                    "acquisition_method": "acquisition_method",
                    "material_kind": "material_kind",
                    "provider": "provider",
                    "title": "title",
                    "publisher": "publisher",
                    "published_at": "published_at",
                    "retrieved_at": "retrieved_at",
                    "source_category": "source_category",
                    "retrieval_source_type": "retrieval_source_type",
                    "underlying_evidence_type": "underlying_evidence_type",
                    "raw_underlying_evidence_type": ("raw_underlying_evidence_type"),
                    "content_sha256": "content_sha256",
                    "content_size_bytes": "content_size_bytes",
                    "content_media_type": "content_media_type",
                    "content_blob_path": "content_blob_path",
                    "content_artifact_id": "content_artifact_id",
                    "content_artifact_revision": "content_artifact_revision",
                    "raw_payload_sha256": "raw_payload_sha256",
                    "raw_payload_size_bytes": "raw_payload_size_bytes",
                    "raw_payload_media_type": "raw_payload_media_type",
                    "raw_payload_blob_path": "raw_payload_blob_path",
                    "raw_payload_artifact_id": "raw_payload_artifact_id",
                    "raw_payload_artifact_revision": ("raw_payload_artifact_revision"),
                    "claims_eligible": "claims_eligible",
                    "eligibility_reason": "eligibility_reason",
                    "invocation_id": "invocation_id",
                    "acquisition_event_id": "acquisition_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                    "created_at": "created_at",
                },
            ),
            accepted_proposals=self._load_for_run(
                AcceptedProposalRecord,
                "accepted_proposals",
                run_id,
                "proposal_id",
                {
                    "run_id": "run_id",
                    "proposal_id": "proposal_id",
                    "schema_version": "schema_version",
                    "proposal_kind": "proposal_kind",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "proposal_sha256": "proposal_sha256",
                    "invocation_id": "invocation_id",
                    "owner_stage_id": "owner_stage_id",
                    "owner_role_id": "owner_role_id",
                    "parent_proposal_id": "parent_proposal_id",
                    "target_artifact_id": "target_artifact_id",
                    "target_artifact_revision": "target_artifact_revision",
                    "accepted_event_id": "accepted_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                    "created_at": "created_at",
                },
            ),
            proposal_source_bindings=self._load_for_run(
                ProposalSourceBinding,
                "proposal_source_bindings",
                run_id,
                "proposal_id, source_id",
                {
                    "run_id": "run_id",
                    "proposal_id": "proposal_id",
                    "source_id": "source_id",
                    "schema_version": "schema_version",
                },
            ),
            run_contract_bindings=self._load_for_run(
                RunContractBinding,
                "run_contract_bindings",
                run_id,
                "run_id",
                {
                    "run_id": "run_id",
                    "workspace_id": "workspace_id",
                    "schema_version": "schema_version",
                    "runtime": "runtime",
                    "stage_specs_artifact_id": "stage_specs_artifact.artifact_id",
                    "stage_specs_revision": "stage_specs_artifact.revision",
                    "stage_specs_sha256": "stage_specs_sha256",
                    "artifact_contracts_artifact_id": (
                        "artifact_contracts_artifact.artifact_id"
                    ),
                    "artifact_contracts_revision": (
                        "artifact_contracts_artifact.revision"
                    ),
                    "artifact_contracts_sha256": "artifact_contracts_sha256",
                    "policy_pack_artifact_id": "policy_pack_artifact.artifact_id",
                    "policy_pack_revision": "policy_pack_artifact.revision",
                    "policy_pack_sha256": "policy_pack_sha256",
                    "contract_fingerprint": "contract_fingerprint",
                    "initialization_event_id": "initialization_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            run_execution_authorizations=self._load_for_run(
                RunExecutionAuthorization,
                "run_execution_authorizations",
                run_id,
                "authorization_id",
                {
                    "run_id": "run_id",
                    "authorization_id": "authorization_id",
                    "workspace_id": "workspace_id",
                    "schema_version": "schema_version",
                    "run_contract_fingerprint": "run_contract_fingerprint",
                    "run_direction_fingerprint": "run_direction_fingerprint",
                    "completion_target": "completion_target",
                    "source_manifest_artifact_id": (
                        "source_manifest_artifact.artifact_id"
                    ),
                    "source_manifest_revision": ("source_manifest_artifact.revision"),
                    "source_manifest_sha256": "source_manifest_sha256",
                    "source_manifest_member_count": "source_manifest_member_count",
                    "repair_budget": "repair_budget",
                    "authorization_event_id": "authorization_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                    "created_at": "created_at",
                },
            ),
            run_source_discovery_authorizations=self._load_for_run(
                RunSourceDiscoveryAuthorization,
                "run_source_discovery_authorizations",
                run_id,
                "authorization_id",
                {
                    "run_id": "run_id",
                    "authorization_id": "authorization_id",
                    "workspace_id": "workspace_id",
                    "schema_version": "schema_version",
                    "run_contract_fingerprint": "run_contract_fingerprint",
                    "run_direction_fingerprint": "run_direction_fingerprint",
                    "runtime_source_plan_fingerprint": (
                        "runtime_source_plan_fingerprint"
                    ),
                    "source_route_fingerprint": "source_route_fingerprint",
                    "route_id": "route_id",
                    "provider_id": "provider_id",
                    "execution_owner": "execution_owner",
                    "credential_env": "credential_env",
                    "completion_target": "completion_target",
                    "repair_budget": "repair_budget",
                    "authorization_event_id": "authorization_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                    "created_at": "created_at",
                },
            ),
            run_source_acquisition_attempt_authorizations=self._load_for_run(
                RunSourceAcquisitionAttemptAuthorization,
                "run_source_acquisition_attempt_authorizations_v2",
                run_id,
                "attempt_ordinal",
                {
                    "run_id": "run_id",
                    "attempt_authorization_id": "attempt_authorization_id",
                    "attempt_ordinal": "attempt_ordinal",
                    "workspace_id": "workspace_id",
                    "schema_version": "schema_version",
                    "discovery_authorization_id": "discovery_authorization_id",
                    "run_contract_fingerprint": "run_contract_fingerprint",
                    "run_direction_fingerprint": "run_direction_fingerprint",
                    "runtime_source_plan_fingerprint": (
                        "runtime_source_plan_fingerprint"
                    ),
                    "source_route_fingerprint": "source_route_fingerprint",
                    "provider_request_fingerprint": ("provider_request_fingerprint"),
                    "provider_id": "provider_id",
                    "route_id": "route_id",
                    "max_provider_calls": "max_provider_calls",
                    "provider_cost_status": "provider_cost_status",
                    "previous_attempt_authorization_id": (
                        "previous_attempt_authorization_id"
                    ),
                    "human_request_id": "human_request_id",
                    "authorization_event_id": "authorization_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                    "created_at": "created_at",
                },
            ),
            runtime_source_search_plans=self._load_for_run(
                RuntimeSourceSearchPlanV2,
                "runtime_source_search_plans",
                run_id,
                "plan_revision, search_plan_id",
                {
                    "run_id": "run_id",
                    "search_plan_id": "search_plan_id",
                    "schema_version": "schema_version",
                    "plan_revision": "plan_revision",
                    "report_type": "report_type",
                    "task_count": "task_count",
                    "acquisition_spec_fingerprint": (
                        "acquisition_spec_fingerprint"
                    ),
                    "plan_fingerprint": "plan_fingerprint",
                    "record_event_id": "record_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "created_at": "created_at",
                },
            ),
            tavily_acquisition_bundle_records=self._load_for_run(
                TavilyAcquisitionBundleRecordV2,
                "tavily_acquisition_bundle_records",
                run_id,
                "recorded_at, bundle_record_id",
                {
                    "run_id": "run_id",
                    "bundle_record_id": "bundle_record_id",
                    "schema_version": "schema_version",
                    "attempt_authorization_id": "attempt_authorization_id",
                    "provider_response_artifact_id": (
                        "provider_response_artifact_id"
                    ),
                    "provider_response_sha256": "provider_response_sha256",
                    "bundle_status": "bundle_status",
                    "search_count": "search_count",
                    "extract_batch_count": "extract_batch_count",
                    "unique_url_count": "unique_url_count",
                    "durable_content_count": "durable_content_count",
                    "record_fingerprint": "record_fingerprint",
                    "record_event_id": "record_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "recorded_at": "recorded_at",
                },
            ),
            owned_artifact_submissions=self._load_for_run(
                OwnedArtifactSubmissionRecord,
                "owned_artifact_submissions",
                run_id,
                "submission_id",
                {
                    "run_id": "run_id",
                    "submission_id": "submission_id",
                    "schema_version": "schema_version",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "artifact_sha256": "artifact_sha256",
                    "owner_stage_id": "owner_stage_id",
                    "owner_role_id": "owner_role_id",
                    "run_contract_fingerprint": "run_contract_fingerprint",
                    "invocation_id": "invocation_id",
                    "producer_tool_id": "producer_tool_id",
                    "parent_artifact_id": "parent_artifact.artifact_id",
                    "parent_artifact_revision": "parent_artifact.revision",
                    "source_proposal_id": "source_proposal_id",
                    "canonical_workspace_path": "canonical_workspace_path",
                    "request_fingerprint": "request_fingerprint",
                    "accepted_event_id": "accepted_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "created_at": "created_at",
                },
            ),
            stage_transitions=self._load_for_run(
                StageTransitionRecord,
                "stage_transitions",
                run_id,
                "result_revision, transition_id",
                {
                    "run_id": "run_id",
                    "transition_id": "transition_id",
                    "schema_version": "schema_version",
                    "stage_id": "stage_id",
                    "transition_kind": "transition_kind",
                    "prior_status": "prior_status",
                    "prior_revision": "prior_revision",
                    "result_status": "result_status",
                    "result_revision": "result_revision",
                    "run_contract_fingerprint": "run_contract_fingerprint",
                    "transition_event_id": "transition_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            stage_artifact_bindings=self._load_for_run(
                StageArtifactBinding,
                "stage_artifact_bindings",
                run_id,
                "transition_id, position",
                {
                    "run_id": "run_id",
                    "transition_id": "transition_id",
                    "position": "position",
                    "schema_version": "schema_version",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "artifact_sha256": "artifact_sha256",
                    "usage": "usage",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            stage_gate_bindings=self._load_for_run(
                StageGateBinding,
                "stage_gate_bindings",
                run_id,
                "transition_id, gate_id",
                {
                    "run_id": "run_id",
                    "transition_id": "transition_id",
                    "gate_id": "gate_id",
                    "schema_version": "schema_version",
                    "evaluation_id": "evaluation_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            claims=self._load_for_run(
                ClaimRecord,
                "claims",
                run_id,
                "ordinal, claim_id",
                {
                    "run_id": "run_id",
                    "claim_id": "claim_id",
                    "schema_version": "schema_version",
                    "freeze_id": "freeze_id",
                    "ordinal": "ordinal",
                    "claim_drafts_proposal_id": "claim_drafts_proposal_id",
                    "draft_id": "draft_id",
                    "primary_source_id": "primary_source_id",
                    "claim_type": "claim_type",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            claim_source_bindings=self._load_for_run(
                ClaimSourceBinding,
                "claim_source_bindings",
                run_id,
                "claim_id, position",
                {
                    "run_id": "run_id",
                    "claim_id": "claim_id",
                    "source_id": "source_id",
                    "schema_version": "schema_version",
                    "position": "position",
                    "citation_role": "citation_role",
                    "claim_drafts_proposal_id": "claim_drafts_proposal_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            claim_freezes=self._load_for_run(
                ClaimFreezeRecord,
                "claim_freezes",
                run_id,
                "freeze_id",
                {
                    "run_id": "run_id",
                    "freeze_id": "freeze_id",
                    "schema_version": "schema_version",
                    "claim_drafts_proposal_id": "claim_drafts_proposal_id",
                    "screened_proposal_id": "screened_proposal_id",
                    "candidate_proposal_id": "candidate_proposal_id",
                    "claim_drafts_artifact_id": "claim_drafts_artifact.artifact_id",
                    "claim_drafts_artifact_revision": (
                        "claim_drafts_artifact.revision"
                    ),
                    "claim_drafts_sha256": "claim_drafts_sha256",
                    "ledger_artifact_id": "ledger_artifact.artifact_id",
                    "ledger_artifact_revision": "ledger_artifact.revision",
                    "ledger_sha256": "ledger_sha256",
                    "run_contract_fingerprint": "run_contract_fingerprint",
                    "claim_count": "claim_count",
                    "freeze_event_id": "freeze_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            gate_evaluations=self._load_for_run(
                GateEvaluationRecord,
                "gate_evaluations",
                run_id,
                "gate_batch_id, gate_id",
                {
                    "run_id": "run_id",
                    "evaluation_id": "evaluation_id",
                    "schema_version": "schema_version",
                    "gate_batch_id": "gate_batch_id",
                    "stage_id": "stage_id",
                    "gate_id": "gate_id",
                    "policy_version": "policy_version",
                    "run_contract_fingerprint": "run_contract_fingerprint",
                    "status": "status",
                    "blocking": "blocking",
                    "report_artifact_id": "report_artifact.artifact_id",
                    "report_artifact_revision": "report_artifact.revision",
                    "evaluation_event_id": "evaluation_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            gate_findings=self._load_for_run(
                GateFindingRecord,
                "gate_findings",
                run_id,
                "evaluation_id, finding_id",
                {
                    "run_id": "run_id",
                    "evaluation_id": "evaluation_id",
                    "finding_id": "finding_id",
                    "schema_version": "schema_version",
                    "gate_id": "gate_id",
                    "blocking_level": "blocking_level",
                    "artifact_id": "artifact_id",
                    "claim_id": "claim_id",
                    "source_id": "source_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            gate_artifact_bindings=self._load_for_run(
                GateArtifactBinding,
                "gate_artifact_bindings",
                run_id,
                "evaluation_id, position",
                {
                    "run_id": "run_id",
                    "evaluation_id": "evaluation_id",
                    "position": "position",
                    "schema_version": "schema_version",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "artifact_sha256": "artifact_sha256",
                    "usage": "usage",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            run_integrity_records=self._load_for_run(
                RunIntegrityRecord,
                "run_integrity_records",
                run_id,
                "integrity_revision",
                {
                    "run_id": "run_id",
                    "integrity_revision": "integrity_revision",
                    "schema_version": "schema_version",
                    "status": "status",
                    "prior_integrity_revision": "prior_integrity_revision",
                    "affected_artifact_id": "affected_artifact_id",
                    "affected_artifact_revision": "affected_artifact_revision",
                    "expected_workspace_path": "expected_workspace_path",
                    "expected_sha256": "expected_sha256",
                    "observed_entry_kind": "observed_entry_kind",
                    "observed_sha256": "observed_sha256",
                    "reason_code": "reason_code",
                    "first_detected_event_id": "first_detected_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            repair_cycles=self._load_for_run(
                RepairCycleRecord,
                "repair_cycles",
                run_id,
                "started_at, repair_id",
                {
                    "run_id": "run_id",
                    "repair_id": "repair_id",
                    "schema_version": "schema_version",
                    "contamination_revision": "contamination_revision",
                    "owner_stage_id": "owner_stage_id",
                    "reason_code": "reason_code",
                    "started_at": "started_at",
                    "start_event_id": "start_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            gate_repair_cycles=self._load_for_run(
                GateRepairCycleRecord,
                "gate_repair_cycles",
                run_id,
                "started_at, gate_repair_id",
                {
                    "run_id": "run_id",
                    "gate_repair_id": "gate_repair_id",
                    "schema_version": "schema_version",
                    "authorization_id": "authorization_id",
                    "repair_ordinal": "repair_ordinal",
                    "source_gate_batch_id": "source_gate_batch_id",
                    "source_stage_id": "source_stage_id",
                    "repair_owner": "repair_owner",
                    "target_artifact_id": "target_artifact.artifact_id",
                    "target_artifact_revision": "target_artifact.revision",
                    "started_at": "started_at",
                    "start_event_id": "start_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            gate_repair_artifact_bindings=self._load_for_run(
                GateRepairArtifactBinding,
                "gate_repair_artifact_bindings",
                run_id,
                "gate_repair_id",
                {
                    "run_id": "run_id",
                    "gate_repair_id": "gate_repair_id",
                    "schema_version": "schema_version",
                    "prior_artifact_id": "prior_artifact.artifact_id",
                    "prior_artifact_revision": "prior_artifact.revision",
                    "successor_artifact_id": "successor_artifact.artifact_id",
                    "successor_artifact_revision": "successor_artifact.revision",
                    "owned_artifact_submission_id": ("owned_artifact_submission_id"),
                    "accepted_event_id": "accepted_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            gate_repair_outcomes=self._load_for_run(
                GateRepairOutcomeRecord,
                "gate_repair_outcomes",
                run_id,
                "completed_at, outcome_id",
                {
                    "run_id": "run_id",
                    "outcome_id": "outcome_id",
                    "schema_version": "schema_version",
                    "gate_repair_id": "gate_repair_id",
                    "replacement_gate_batch_id": "replacement_gate_batch_id",
                    "replacement_stage_id": "replacement_stage_id",
                    "disposition": "disposition",
                    "completed_at": "completed_at",
                    "completion_event_id": "completion_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            artifact_supersessions=self._load_for_run(
                ArtifactSupersessionRecord,
                "artifact_supersessions",
                run_id,
                "created_at, supersession_id",
                {
                    "run_id": "run_id",
                    "supersession_id": "supersession_id",
                    "repair_id": "repair_id",
                    "schema_version": "schema_version",
                    "mode": "mode",
                    "artifact_id": "prior_artifact.artifact_id",
                    "prior_revision": "prior_artifact.revision",
                    "successor_revision": "successor_artifact.revision",
                    "reason_code": "reason_code",
                    "created_at": "created_at",
                    "accepted_event_id": "accepted_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            repair_completions=self._load_for_run(
                RepairCompletionRecord,
                "repair_completions",
                run_id,
                "completed_at, repair_completion_id",
                {
                    "run_id": "run_id",
                    "repair_completion_id": "repair_completion_id",
                    "repair_id": "repair_id",
                    "schema_version": "schema_version",
                    "contamination_revision": "contamination_revision",
                    "completed_at": "completed_at",
                    "completion_event_id": "completion_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            recovery_completions=self._load_for_run(
                RecoveryCompletionRecord,
                "recovery_completions",
                run_id,
                "completed_at, recovery_id",
                {
                    "run_id": "run_id",
                    "recovery_id": "recovery_id",
                    "repair_completion_id": "repair_completion_id",
                    "schema_version": "schema_version",
                    "contamination_revision": "contamination_revision",
                    "disposition": "disposition",
                    "completed_at": "completed_at",
                    "completion_event_id": "completion_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            run_head_transitions=self._load_for_run(
                RunHeadTransitionRecord,
                "run_head_transitions",
                run_id,
                "created_at, head_transition_id",
                {
                    "successor_run_id": "successor_run_id",
                    "head_transition_id": "head_transition_id",
                    "workspace_id": "workspace_id",
                    "predecessor_run_id": "predecessor_run_id",
                    "schema_version": "schema_version",
                    "prior_workspace_revision": "prior_workspace_revision",
                    "successor_workspace_revision": "successor_workspace_revision",
                    "reason_code": "reason_code",
                    "successor_disposition": "successor_disposition",
                    "created_at": "created_at",
                    "transition_event_id": "transition_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
                run_column="successor_run_id",
            ),
            finalize_renders=self._load_for_run(
                FinalizeRenderRecord,
                "finalize_renders",
                run_id,
                "created_at, render_id",
                {
                    "run_id": "run_id",
                    "render_id": "render_id",
                    "schema_version": "schema_version",
                    "audit_proposal_id": "audit_proposal_id",
                    "audited_brief_artifact_id": "audited_brief.artifact_id",
                    "audited_brief_revision": "audited_brief.revision",
                    "audit_report_artifact_id": "audit_report.artifact_id",
                    "audit_report_revision": "audit_report.revision",
                    "reader_clean_status": "reader_clean_status",
                    "policy_result_fingerprint": "policy_result_fingerprint",
                    "run_contract_fingerprint": "run_contract_fingerprint",
                    "created_at": "created_at",
                    "render_event_id": "render_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            finalizations=self._load_for_run(
                FinalizationRecord,
                "finalizations",
                run_id,
                "finalized_at, finalization_id",
                {
                    "run_id": "run_id",
                    "finalization_id": "finalization_id",
                    "schema_version": "schema_version",
                    "render_id": "render_id",
                    "finalize_transition_id": "finalize_transition_id",
                    "finalize_gate_batch_id": "finalize_gate_batch_id",
                    "recovery_id": "recovery_id",
                    "integrity_revision": "integrity_revision",
                    "finalized_at": "finalized_at",
                    "finalization_event_id": "finalization_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            run_archives=self._load_for_run(
                RunArchiveRecord,
                "run_archives",
                run_id,
                "created_at, archive_id",
                {
                    "run_id": "run_id",
                    "archive_id": "archive_id",
                    "schema_version": "schema_version",
                    "finalization_id": "finalization_id",
                    "archive_artifact_id": "archive_artifact.artifact_id",
                    "archive_artifact_revision": "archive_artifact.revision",
                    "manifest_sha256": "manifest_sha256",
                    "included_count": "included_count",
                    "created_at": "created_at",
                    "archive_event_id": "archive_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            run_archive_artifact_bindings=self._load_for_run(
                RunArchiveArtifactBinding,
                "run_archive_artifact_bindings",
                run_id,
                "archive_id, position",
                {
                    "run_id": "run_id",
                    "archive_id": "archive_id",
                    "position": "position",
                    "schema_version": "schema_version",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "artifact_sha256": "artifact_sha256",
                    "usage": "usage",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            package_ready_records=self._load_for_run(
                PackageReadyRecord,
                "package_ready_records",
                run_id,
                "created_at, package_id",
                {
                    "run_id": "run_id",
                    "package_id": "package_id",
                    "schema_version": "schema_version",
                    "finalization_id": "finalization_id",
                    "archive_id": "archive_id",
                    "package_manifest_artifact_id": "package_manifest_artifact.artifact_id",
                    "package_manifest_revision": "package_manifest_artifact.revision",
                    "package_manifest_sha256": "package_manifest_sha256",
                    "artifact_count": "artifact_count",
                    "created_at": "created_at",
                    "package_event_id": "package_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            package_artifact_bindings=self._load_for_run(
                PackageArtifactBinding,
                "package_artifact_bindings",
                run_id,
                "package_id, position",
                {
                    "run_id": "run_id",
                    "package_id": "package_id",
                    "position": "position",
                    "schema_version": "schema_version",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "artifact_sha256": "artifact_sha256",
                    "usage": "usage",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            approval_package_bindings=self._load_for_run(
                ApprovalPackageBinding,
                "approval_package_bindings",
                run_id,
                "approval_id, package_id",
                {
                    "run_id": "run_id",
                    "approval_id": "approval_id",
                    "package_id": "package_id",
                    "schema_version": "schema_version",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            delivery_authorizations=self._load_for_run(
                DeliveryAuthorizationRecord,
                "delivery_authorizations",
                run_id,
                "recorded_at, authorization_id",
                {
                    "run_id": "run_id",
                    "authorization_id": "authorization_id",
                    "schema_version": "schema_version",
                    "package_id": "package_id",
                    "prior_authorization_id": "prior_authorization_id",
                    "approval_mode": "approval_mode",
                    "retry_of_attempt_id": "retry_of_attempt_id",
                    "purpose": "purpose",
                    "decision": "decision",
                    "target": "target",
                    "channel": "channel",
                    "recipient_fingerprint": "recipient_fingerprint",
                    "actor_id": "actor_id",
                    "recorded_at": "recorded_at",
                    "authorization_event_id": "authorization_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            delivery_attempts=self._load_for_run(
                DeliveryAttemptRecord,
                "delivery_attempts",
                run_id,
                "created_at, attempt_id",
                {
                    "run_id": "run_id",
                    "attempt_id": "attempt_id",
                    "schema_version": "schema_version",
                    "package_id": "package_id",
                    "authorization_id": "authorization_id",
                    "target": "target",
                    "channel": "channel",
                    "recipient_fingerprint": "recipient_fingerprint",
                    "connector_operation_id": "connector_operation_id",
                    "connector_request_fingerprint": "connector_request_fingerprint",
                    "created_at": "created_at",
                    "attempt_event_id": "attempt_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            delivery_results=self._load_for_run(
                DeliveryResultRecord,
                "delivery_results",
                run_id,
                "recorded_at, result_id",
                {
                    "run_id": "run_id",
                    "result_id": "result_id",
                    "schema_version": "schema_version",
                    "attempt_id": "attempt_id",
                    "prior_result_id": "prior_result_id",
                    "reconciliation_authorization_id": "reconciliation_authorization_id",
                    "status": "status",
                    "adapter_id": "adapter_id",
                    "adapter_version": "adapter_version",
                    "connector_operation_id": "connector_operation_id",
                    "evidence_sha256": "evidence_sha256",
                    "evidence_artifact_id": "evidence_artifact.artifact_id",
                    "evidence_artifact_revision": "evidence_artifact.revision",
                    "recorded_at": "recorded_at",
                    "result_event_id": "result_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            post_final_assessment_policy_revisions=self._load_for_run(
                PostFinalAssessmentPolicyRevision,
                "post_final_assessment_policy_revisions",
                run_id,
                "recorded_at, policy_revision_id",
                {
                    "run_id": "run_id",
                    "policy_revision_id": "policy_revision_id",
                    "schema_version": "schema_version",
                    "previous_policy_revision_id": "previous_policy_revision_id",
                    "enabled": "enabled",
                    "auto_run": "auto_run",
                    "auto_open": "auto_open",
                    "adapter_id": "adapter_id",
                    "messages_endpoint_sha256": "messages_endpoint_sha256",
                    "requested_model_id": "requested_model_id",
                    "profile_id": "profile_id",
                    "human_request_id": "human_request_id",
                    "policy_fingerprint": "policy_fingerprint",
                    "recorded_at": "recorded_at",
                    "policy_event_id": "policy_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            post_final_assessment_requests=self._load_for_run(
                PostFinalAssessmentRequestRecord,
                "post_final_assessment_requests",
                run_id,
                "claimed_at, assessment_request_id",
                {
                    "run_id": "run_id",
                    "assessment_request_id": "assessment_request_id",
                    "schema_version": "schema_version",
                    "finalized_facts_fingerprint": "finalized_facts_fingerprint",
                    "finalized_lineage_fingerprint": "finalized_lineage_fingerprint",
                    "policy_revision_id": "policy_revision_id",
                    "trial_id": "trial_id",
                    "archive_identity_sha256": "archive_identity_sha256",
                    "request_fingerprint": "request_fingerprint",
                    "claimed_at": "claimed_at",
                    "request_event_id": "request_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "assessment_generation": "assessment_generation",
                    "predecessor_assessment_request_id": (
                        "predecessor_assessment_request_id"
                    ),
                    "predecessor_assessment_request_fingerprint": (
                        "predecessor_assessment_request_fingerprint"
                    ),
                    "predecessor_assessment_result_id": (
                        "predecessor_assessment_result_id"
                    ),
                    "predecessor_result_fingerprint": (
                        "predecessor_result_fingerprint"
                    ),
                    "predecessor_abandonment_id": "predecessor_abandonment_id",
                    "predecessor_abandonment_fingerprint": (
                        "predecessor_abandonment_fingerprint"
                    ),
                    "assessment_purpose": "assessment_purpose",
                    "human_actor_id": "human_actor_id",
                    "human_request_id": "human_request_id",
                    "authorization_fingerprint": "authorization_fingerprint",
                },
            ),
            post_final_assessment_abandonments=self._load_for_run(
                PostFinalAssessmentAbandonmentRecord,
                "post_final_assessment_abandonments",
                run_id,
                "assessment_generation, abandonment_id",
                {
                    "run_id": "run_id",
                    "abandonment_id": "abandonment_id",
                    "schema_version": "schema_version",
                    "assessment_request_id": "assessment_request_id",
                    "assessment_request_fingerprint": (
                        "assessment_request_fingerprint"
                    ),
                    "finalized_lineage_fingerprint": ("finalized_lineage_fingerprint"),
                    "assessment_generation": "assessment_generation",
                    "reason": "reason",
                    "human_actor_id": "human_actor_id",
                    "human_request_id": "human_request_id",
                    "expected_store_revision": "expected_store_revision",
                    "abandonment_fingerprint": "abandonment_fingerprint",
                    "recorded_at": "recorded_at",
                    "abandonment_event_id": "abandonment_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            post_final_assessment_executions=self._load_for_run(
                PostFinalAssessmentExecutionRecord,
                "post_final_assessment_executions",
                run_id,
                "recorded_at, execution_id",
                {
                    "run_id": "run_id",
                    "execution_id": "execution_id",
                    "schema_version": "schema_version",
                    "assessment_request_id": "assessment_request_id",
                    "trial_id": "trial_id",
                    "execution_archive_manifest_sha256": "execution_archive_manifest_sha256",
                    "execution_receipt_id": "execution_receipt_id",
                    "execution_status": "execution_status",
                    "run_status": "run_status",
                    "validation_status": "validation_status",
                    "reason_codes_json": "reason_codes_json",
                    "recorded_at": "recorded_at",
                    "execution_event_id": "execution_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "execution_fingerprint": "execution_fingerprint",
                },
            ),
            post_final_assessment_results=self._load_for_run(
                PostFinalAssessmentResultRecord,
                "post_final_assessment_results",
                run_id,
                "recorded_at, assessment_result_id",
                {
                    "run_id": "run_id",
                    "assessment_result_id": "assessment_result_id",
                    "schema_version": "schema_version",
                    "assessment_request_id": "assessment_request_id",
                    "policy_revision_id": "policy_revision_id",
                    "finalized_facts_fingerprint": "finalized_facts_fingerprint",
                    "finalized_lineage_fingerprint": "finalized_lineage_fingerprint",
                    "terminal_evidence_class": "terminal_evidence_class",
                    "result_fingerprint": "result_fingerprint",
                    "recorded_at": "recorded_at",
                    "result_event_id": "result_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                },
            ),
            post_final_finding_dispositions=self._load_for_run(
                PostFinalFindingDispositionRecord,
                "post_final_finding_dispositions",
                run_id,
                "recorded_at, disposition_id",
                {
                    "run_id": "run_id",
                    "disposition_id": "disposition_id",
                    "schema_version": "schema_version",
                    "finalized_lineage_fingerprint": "finalized_lineage_fingerprint",
                    "assessment_result_id": "assessment_result_id",
                    "assessment_result_fingerprint": "assessment_result_fingerprint",
                    "reader_view_sha256": "reader_view_sha256",
                    "finding_id": "finding_id",
                    "finding_fingerprint": "finding_fingerprint",
                    "previous_disposition_id": "previous_disposition_id",
                    "decision": "decision",
                    "human_note": "human_note",
                    "human_actor_id": "human_actor_id",
                    "human_request_id": "human_request_id",
                    "recorded_at": "recorded_at",
                    "disposition_event_id": "disposition_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "disposition_fingerprint": "disposition_fingerprint",
                },
            ),
            post_final_human_observations=self._load_for_run(
                PostFinalHumanObservationRecord,
                "post_final_human_observations",
                run_id,
                "recorded_at, observation_id",
                {
                    "run_id": "run_id",
                    "observation_id": "observation_id",
                    "schema_version": "schema_version",
                    "origin": "origin",
                    "observation_revision": "observation_revision",
                    "finalized_lineage_fingerprint": "finalized_lineage_fingerprint",
                    "report_revision": "report_revision",
                    "report_artifact_id": "report_artifact_id",
                    "report_sha256": "report_sha256",
                    "assessment_result_id": "assessment_result_id",
                    "assessment_result_fingerprint": "assessment_result_fingerprint",
                    "reader_view_sha256": "reader_view_sha256",
                    "observation_text": "observation_text",
                    "observation_sha256": "observation_sha256",
                    "requirement_id": "requirement_id",
                    "claim_id": "claim_id",
                    "scope_class": "scope_class",
                    "dimension_id": "dimension_id",
                    "previous_observation_id": "previous_observation_id",
                    "previous_observation_fingerprint": "previous_observation_fingerprint",
                    "human_actor_id": "human_actor_id",
                    "human_request_id": "human_request_id",
                    "recorded_at": "recorded_at",
                    "observation_event_id": "observation_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "observation_fingerprint": "observation_fingerprint",
                },
            ),
            post_final_guidance_drafts=self._load_for_run(
                PostFinalGuidanceDraftRevision,
                "post_final_guidance_drafts",
                run_id,
                "guidance_id, draft_revision",
                {
                    "run_id": "run_id",
                    "guidance_id": "guidance_id",
                    "draft_revision": "draft_revision",
                    "schema_version": "schema_version",
                    "finalized_lineage_fingerprint": "finalized_lineage_fingerprint",
                    "provenance_kind": "provenance_kind",
                    "assessment_result_id": "assessment_result_id",
                    "assessment_result_fingerprint": "assessment_result_fingerprint",
                    "finding_id": "finding_id",
                    "finding_fingerprint": "finding_fingerprint",
                    "disposition_id": "disposition_id",
                    "disposition_fingerprint": "disposition_fingerprint",
                    "observation_id": "observation_id",
                    "observation_fingerprint": "observation_fingerprint",
                    "previous_draft_revision": "previous_draft_revision",
                    "guidance_scope": "guidance_scope",
                    "guidance_text": "guidance_text",
                    "guidance_sha256": "guidance_sha256",
                    "human_actor_id": "human_actor_id",
                    "human_request_id": "human_request_id",
                    "recorded_at": "recorded_at",
                    "draft_event_id": "draft_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "draft_fingerprint": "draft_fingerprint",
                },
            ),
            post_final_guidance_statuses=self._load_for_run(
                PostFinalGuidanceStatusRevision,
                "post_final_guidance_statuses",
                run_id,
                "recorded_at, status_revision_id",
                {
                    "run_id": "run_id",
                    "status_revision_id": "status_revision_id",
                    "schema_version": "schema_version",
                    "finalized_lineage_fingerprint": "finalized_lineage_fingerprint",
                    "guidance_id": "guidance_id",
                    "draft_revision": "draft_revision",
                    "guidance_sha256": "guidance_sha256",
                    "status": "status",
                    "previous_status_revision_id": "previous_status_revision_id",
                    "human_actor_id": "human_actor_id",
                    "human_request_id": "human_request_id",
                    "recorded_at": "recorded_at",
                    "status_event_id": "status_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "status_fingerprint": "status_fingerprint",
                },
            ),
            run_guidance_snapshots=self._load_for_run(
                RunGuidanceSnapshotRecord,
                "run_guidance_snapshots",
                run_id,
                "snapshot_id",
                {
                    "run_id": "run_id",
                    "snapshot_id": "snapshot_id",
                    "workspace_id": "workspace_id",
                    "predecessor_run_id": "predecessor_run_id",
                    "schema_version": "schema_version",
                    "reuse_requested": "reuse_requested",
                    "successor_direction_fingerprint": (
                        "successor_direction_fingerprint"
                    ),
                    "successor_run_contract_fingerprint": (
                        "successor_run_contract_fingerprint"
                    ),
                    "candidate_set_fingerprint": "candidate_set_fingerprint",
                    "selected_count": "selected_count",
                    "omitted_count": "omitted_count",
                    "snapshot_fingerprint": "snapshot_fingerprint",
                    "snapshot_event_id": "snapshot_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            ),
            run_guidance_selection_decisions=self._load_for_run(
                RunGuidanceSelectionDecisionRecord,
                "run_guidance_selection_decisions",
                run_id,
                "decision_id",
                {
                    "run_id": "run_id",
                    "decision_id": "decision_id",
                    "snapshot_id": "snapshot_id",
                    "source_run_id": "source_run_id",
                    "schema_version": "schema_version",
                    "guidance_id": "guidance_id",
                    "draft_revision": "draft_revision",
                    "provenance_kind": "provenance_kind",
                    "status_revision_id": "status_revision_id",
                    "assessment_result_id": "assessment_result_id",
                    "finding_id": "finding_id",
                    "disposition_id": "disposition_id",
                    "observation_id": "observation_id",
                    "result_fingerprint": "result_fingerprint",
                    "finding_fingerprint": "finding_fingerprint",
                    "disposition_fingerprint": "disposition_fingerprint",
                    "draft_fingerprint": "draft_fingerprint",
                    "status_fingerprint": "status_fingerprint",
                    "source_scope_fingerprint": "source_scope_fingerprint",
                    "successor_scope_fingerprint": ("successor_scope_fingerprint"),
                    "selected": "selected",
                    "reason_code": "reason_code",
                    "decision_fingerprint": "decision_fingerprint",
                },
            ),
            run_guidance_snapshot_items=self._load_for_run(
                RunGuidanceSnapshotItemRecord,
                "run_guidance_snapshot_items",
                run_id,
                "position, item_id",
                {
                    "run_id": "run_id",
                    "item_id": "item_id",
                    "snapshot_id": "snapshot_id",
                    "position": "position",
                    "source_run_id": "source_run_id",
                    "schema_version": "schema_version",
                    "finalized_lineage_fingerprint": ("finalized_lineage_fingerprint"),
                    "provenance_kind": "provenance_kind",
                    "assessment_result_id": "assessment_result_id",
                    "assessment_result_fingerprint": ("assessment_result_fingerprint"),
                    "finding_id": "finding_id",
                    "finding_fingerprint": "finding_fingerprint",
                    "disposition_id": "disposition_id",
                    "disposition_fingerprint": "disposition_fingerprint",
                    "observation_id": "observation_id",
                    "observation_fingerprint": "observation_fingerprint",
                    "guidance_id": "guidance_id",
                    "draft_revision": "draft_revision",
                    "draft_fingerprint": "draft_fingerprint",
                    "status_revision_id": "status_revision_id",
                    "status_fingerprint": "status_fingerprint",
                    "guidance_text": "guidance_text",
                    "guidance_sha256": "guidance_sha256",
                    "reuse_scope_fingerprint": "reuse_scope.scope_fingerprint",
                    "item_fingerprint": "item_fingerprint",
                },
            ),
            checkout_revisions=self._load_for_run(
                CheckoutRevisionRecord,
                "checkout_revisions",
                run_id,
                "created_at, checkout_revision_id",
                {
                    "checkout_revision_id": "checkout_revision_id",
                    "workspace_id": "workspace_id",
                    "run_id": "run_id",
                    "parent_checkout_revision_id": "parent_checkout_revision_id",
                    "schema_version": "schema_version",
                    "manifest_sha256": "manifest_sha256",
                    "tree_sha256": "tree_sha256",
                    "member_count": "member_count",
                    "created_at": "created_at",
                    "creator_transaction_id": "creator_transaction_id",
                },
            ),
            checkout_revision_members=self._load_for_run(
                CheckoutRevisionMember,
                "checkout_revision_members",
                run_id,
                "checkout_revision_id, ordinal",
                {
                    "checkout_revision_id": "checkout_revision_id",
                    "ordinal": "ordinal",
                    "workspace_id": "workspace_id",
                    "run_id": "run_id",
                    "schema_version": "schema_version",
                    "canonical_path": "canonical_path",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "blob_sha256": "blob_sha256",
                    "byte_size": "byte_size",
                },
            ),
            receipt_checkout_bindings=self._load_for_run(
                ReceiptCheckoutBinding,
                "receipt_checkout_bindings",
                run_id,
                "transaction_id",
                {
                    "workspace_id": "workspace_id",
                    "run_id": "run_id",
                    "transaction_id": "transaction_id",
                    "schema_version": "schema_version",
                    "pre_run_id": "pre_run_id",
                    "pre_checkout_revision_id": "pre_checkout_revision_id",
                    "post_run_id": "post_run_id",
                    "post_checkout_revision_id": "post_checkout_revision_id",
                },
            ),
            checkout_publication_intents=self._load_for_run(
                CheckoutPublicationIntent,
                "checkout_publication_intents",
                run_id,
                "transaction_id, checkout_revision_id",
                {
                    "workspace_id": "identity.workspace_id",
                    "run_id": "identity.run_id",
                    "transaction_id": "identity.transaction_id",
                    "checkout_revision_id": "identity.checkout_revision_id",
                    "schema_version": "schema_version",
                    "publication_identity_sha256": "publication_identity_sha256",
                    "pre_checkout_revision_id": "pre_checkout_revision_id",
                    "post_checkout_revision_id": "post_checkout_revision_id",
                    "post_manifest_sha256": "post_manifest_sha256",
                    "post_tree_sha256": "post_tree_sha256",
                    "changed_member_count": "changed_member_count",
                    "capability_profile_sha256": "capability_profile_sha256",
                },
            ),
            checkout_publication_members=self._load_for_run(
                CheckoutPublicationMember,
                "checkout_publication_members",
                run_id,
                "transaction_id, checkout_revision_id, ordinal",
                {
                    "workspace_id": "identity.workspace_id",
                    "run_id": "identity.run_id",
                    "transaction_id": "identity.transaction_id",
                    "checkout_revision_id": "identity.checkout_revision_id",
                    "ordinal": "ordinal",
                    "schema_version": "schema_version",
                    "canonical_path": "canonical_path",
                    "temporary_basename": "temporary_basename",
                    "claim_basename": "claim_basename",
                    "pre_kind": "pre_kind",
                    "pre_sha256": "pre_sha256",
                    "pre_size": "pre_size",
                    "post_kind": "post_kind",
                    "post_sha256": "post_sha256",
                    "post_size": "post_size",
                },
            ),
            checkout_publication_acks=self._load_for_run(
                CheckoutPublicationAck,
                "checkout_publication_acks",
                run_id,
                "transaction_id, checkout_revision_id, ordinal",
                {
                    "workspace_id": "identity.workspace_id",
                    "run_id": "identity.run_id",
                    "transaction_id": "identity.transaction_id",
                    "checkout_revision_id": "identity.checkout_revision_id",
                    "ordinal": "ordinal",
                    "schema_version": "schema_version",
                    "publication_identity_sha256": "publication_identity_sha256",
                    "capability_profile_sha256": "capability_profile_sha256",
                    "post_kind": "post_kind",
                    "post_sha256": "post_sha256",
                    "post_size": "post_size",
                    "verification": "verification",
                    "cleanup_policy": "cleanup_policy",
                    "appended_at": "appended_at",
                },
            ),
            checkout_publication_cleanup_observations=self._load_for_run(
                CheckoutPublicationCleanupObservation,
                "checkout_publication_cleanup_observations",
                run_id,
                "transaction_id, checkout_revision_id, ordinal, auxiliary_role",
                {
                    "cleanup_observation_id": "cleanup_observation_id",
                    "workspace_id": "identity.workspace_id",
                    "run_id": "identity.run_id",
                    "transaction_id": "identity.transaction_id",
                    "checkout_revision_id": "identity.checkout_revision_id",
                    "ordinal": "ordinal",
                    "schema_version": "schema_version",
                    "auxiliary_role": "auxiliary_role",
                    "reason_code": "reason_code",
                    "expected_kind": "expected_kind",
                    "expected_sha256": "expected_sha256",
                    "expected_size": "expected_size",
                    "observed_kind": "observed_kind",
                    "observed_sha256": "observed_sha256",
                    "observed_size": "observed_size",
                    "appended_at": "appended_at",
                },
            ),
            transactions=self._load_transactions(run_id),
        )
        self._verify_core_snapshot_structure(snapshot)
        self._verify_runtime_source_search_snapshot_structure(snapshot)
        self._verify_gate_repair_snapshot_structure(snapshot)
        self._verify_post_final_assessment_snapshot_structure(snapshot)
        if _verify_guidance:
            self._verify_guidance_snapshot_structure(snapshot)
        self._verify_checkout_snapshot_structure(snapshot)
        return snapshot

    @staticmethod
    def _verify_runtime_source_search_snapshot_structure(
        snapshot: ControlStoreSnapshot,
    ) -> None:
        """Verify the schema18 atomic search-plan and bundle evidence graph."""

        plans = sorted(
            snapshot.runtime_source_search_plans,
            key=lambda item: item.plan_revision,
        )
        bundles = snapshot.tavily_acquisition_bundle_records
        if not plans and not bundles:
            return
        if len(snapshot.run_contract_bindings) != 1:
            raise ControlStoreIntegrityError("runtime_source_search_graph_invalid")
        binding = snapshot.run_contract_bindings[0]
        if binding.run_direction.report_type is None:
            raise ControlStoreIntegrityError("runtime_source_search_graph_invalid")
        if [item.plan_revision for item in plans] != list(
            range(1, len(plans) + 1)
        ):
            raise ControlStoreIntegrityError("runtime_source_search_graph_invalid")

        transactions = {
            item.transaction_id: item for item in snapshot.transactions
        }
        events = {item.event_id: item for item in snapshot.events}
        attempts = {
            item.attempt_authorization_id: item
            for item in snapshot.run_source_acquisition_attempt_authorizations
        }
        artifacts = {item.artifact_id: item for item in snapshot.artifacts}
        revisions_by_artifact: dict[str, list[ArtifactRevision]] = {}
        for revision in snapshot.artifact_revisions:
            revisions_by_artifact.setdefault(revision.artifact_id, []).append(
                revision
            )
        plan_fingerprints: set[str] = set()
        for plan in plans:
            event = events.get(plan.record_event_id)
            if (
                plan.run_id != snapshot.run.run_id
                or plan.report_type != binding.run_direction.report_type
                or plan.accepted_transaction_id not in transactions
                or event is None
                or event.run_id != plan.run_id
                or event.transaction_id != plan.accepted_transaction_id
                or event.event_type != "runtime_source_search_plan_recorded"
                or not any(
                    attempt.provider_request_fingerprint
                    == plan.acquisition_spec_fingerprint
                    for attempt in attempts.values()
                )
            ):
                raise ControlStoreIntegrityError(
                    "runtime_source_search_graph_invalid"
                )
            plan_fingerprints.add(plan.acquisition_spec_fingerprint)

        seen_attempts: set[str] = set()
        for bundle in bundles:
            event = events.get(bundle.record_event_id)
            attempt = attempts.get(bundle.attempt_authorization_id)
            artifact = artifacts.get(bundle.provider_response_artifact_id)
            revisions = revisions_by_artifact.get(
                bundle.provider_response_artifact_id,
                [],
            )
            matching_revisions = [
                item
                for item in revisions
                if item.sha256 == bundle.provider_response_sha256
                and item.frozen
            ]
            if (
                bundle.run_id != snapshot.run.run_id
                or bundle.attempt_authorization_id in seen_attempts
                or bundle.accepted_transaction_id not in transactions
                or event is None
                or event.run_id != bundle.run_id
                or event.transaction_id != bundle.accepted_transaction_id
                or event.event_type != "tavily_acquisition_bundle_recorded"
                or event.artifact_id != bundle.provider_response_artifact_id
                or attempt is None
                or attempt.provider_request_fingerprint not in plan_fingerprints
                or artifact is None
                or not matching_revisions
            ):
                raise ControlStoreIntegrityError(
                    "runtime_source_search_graph_invalid"
                )
            seen_attempts.add(bundle.attempt_authorization_id)

    def _verify_gate_repair_snapshot_structure(
        self,
        snapshot: ControlStoreSnapshot,
    ) -> None:
        """Verify exact list relations for the distinct Gate-repair lifecycle."""

        cycles = {item.gate_repair_id: item for item in snapshot.gate_repair_cycles}
        bindings = {
            item.gate_repair_id: item for item in snapshot.gate_repair_artifact_bindings
        }
        outcomes = {item.outcome_id: item for item in snapshot.gate_repair_outcomes}
        if len(cycles) != len(snapshot.gate_repair_cycles):
            raise ControlStoreIntegrityError("gate_repair_relation_invalid")
        if len(bindings) != len(snapshot.gate_repair_artifact_bindings):
            raise ControlStoreIntegrityError("gate_repair_relation_invalid")
        if len(outcomes) != len(snapshot.gate_repair_outcomes):
            raise ControlStoreIntegrityError("gate_repair_relation_invalid")
        for cycle in cycles.values():
            evaluation_rows = self._connection.execute(
                """
                SELECT position,evaluation_id
                FROM gate_repair_cycle_evaluations
                WHERE run_id=? AND gate_repair_id=? ORDER BY position
                """,
                (snapshot.run.run_id, cycle.gate_repair_id),
            ).fetchall()
            finding_rows = self._connection.execute(
                """
                SELECT position,evaluation_id,finding_id
                FROM gate_repair_cycle_findings
                WHERE run_id=? AND gate_repair_id=? ORDER BY position
                """,
                (snapshot.run.run_id, cycle.gate_repair_id),
            ).fetchall()
            transition_rows = self._connection.execute(
                """
                SELECT position,transition_id
                FROM gate_repair_cycle_transitions
                WHERE run_id=? AND gate_repair_id=? ORDER BY position
                """,
                (snapshot.run.run_id, cycle.gate_repair_id),
            ).fetchall()
            if (
                [row[0] for row in evaluation_rows] != list(range(len(evaluation_rows)))
                or [row[0] for row in finding_rows] != list(range(len(finding_rows)))
                or [row[0] for row in transition_rows]
                != list(range(len(transition_rows)))
                or [str(row[1]) for row in evaluation_rows]
                != cycle.blocking_evaluation_ids
                or [(str(row[1]), str(row[2])) for row in finding_rows]
                != [
                    (item.evaluation_id, item.finding_id)
                    for item in cycle.blocking_findings
                ]
                or [str(row[1]) for row in transition_rows]
                != cycle.reopened_transition_ids
            ):
                raise ControlStoreIntegrityError("gate_repair_relation_invalid")
        for binding in bindings.values():
            if binding.gate_repair_id not in cycles:
                raise ControlStoreIntegrityError("gate_repair_relation_invalid")
        for outcome in outcomes.values():
            rows = self._connection.execute(
                """
                SELECT position,evaluation_id
                FROM gate_repair_outcome_evaluations
                WHERE run_id=? AND outcome_id=? ORDER BY position
                """,
                (snapshot.run.run_id, outcome.outcome_id),
            ).fetchall()
            if (
                outcome.gate_repair_id not in cycles
                or [row[0] for row in rows] != list(range(len(rows)))
                or [str(row[1]) for row in rows] != outcome.evaluation_ids
            ):
                raise ControlStoreIntegrityError("gate_repair_relation_invalid")

    def _verify_post_final_assessment_snapshot_structure(
        self, snapshot: ControlStoreSnapshot
    ) -> None:
        """Reject detached, cross-run, or reordered PF-LAJ Store relations."""

        graph = (
            snapshot.post_final_assessment_policy_revisions,
            snapshot.post_final_assessment_requests,
            snapshot.post_final_assessment_abandonments,
            snapshot.post_final_assessment_executions,
            snapshot.post_final_assessment_results,
            snapshot.post_final_finding_dispositions,
            snapshot.post_final_human_observations,
            snapshot.post_final_guidance_drafts,
            snapshot.post_final_guidance_statuses,
        )
        if not any(graph):
            return
        receipts = {item.transaction_id: item for item in snapshot.transactions}
        events = {item.event_id: item for item in snapshot.events}
        policies = {
            item.policy_revision_id: item
            for item in snapshot.post_final_assessment_policy_revisions
        }
        requests = {
            item.assessment_request_id: item
            for item in snapshot.post_final_assessment_requests
        }
        abandonments = {
            item.abandonment_id: item
            for item in snapshot.post_final_assessment_abandonments
        }
        executions = {
            item.execution_id: item
            for item in snapshot.post_final_assessment_executions
        }
        results = {
            item.assessment_result_id: item
            for item in snapshot.post_final_assessment_results
        }
        dispositions = {
            item.disposition_id: item
            for item in snapshot.post_final_finding_dispositions
        }
        observations = {
            item.observation_id: item for item in snapshot.post_final_human_observations
        }
        drafts = {
            (item.guidance_id, item.draft_revision): item
            for item in snapshot.post_final_guidance_drafts
        }
        statuses = {
            item.status_revision_id: item
            for item in snapshot.post_final_guidance_statuses
        }
        if (
            len(policies) != len(snapshot.post_final_assessment_policy_revisions)
            or len(requests) != len(snapshot.post_final_assessment_requests)
            or len(abandonments) != len(snapshot.post_final_assessment_abandonments)
            or len(executions) != len(snapshot.post_final_assessment_executions)
            or len(results) != len(snapshot.post_final_assessment_results)
            or len(dispositions) != len(snapshot.post_final_finding_dispositions)
            or len(observations) != len(snapshot.post_final_human_observations)
            or len(drafts) != len(snapshot.post_final_guidance_drafts)
            or len(statuses) != len(snapshot.post_final_guidance_statuses)
        ):
            raise ControlStoreIntegrityError("control_store_integrity_invalid")
        result_request_ids: set[str] = set()
        abandonment_request_ids: set[str] = set()
        policy_receipt_order: list[tuple[int, PostFinalAssessmentPolicyRevision]] = []
        for policy in policies.values():
            receipt = receipts.get(policy.accepted_transaction_id)
            event = events.get(policy.policy_event_id)
            if (
                policy.run_id != snapshot.run.run_id
                or receipt is None
                or event is None
                or event.transaction_id != receipt.transaction_id
                or event.event_type != "post_final_assessment_policy_recorded"
                or event.core_run_binding is not None
                or not any(
                    item.policy_revision_id == policy.policy_revision_id
                    for item in receipt.post_final_assessment_policy_revisions
                )
                or (
                    policy.previous_policy_revision_id is not None
                    and policy.previous_policy_revision_id not in policies
                )
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            policy_receipt_order.append((receipt.committed_revision, policy))
        policy_receipt_order.sort(key=lambda item: item[0])
        if len({revision for revision, _policy in policy_receipt_order}) != len(
            policy_receipt_order
        ):
            raise ControlStoreIntegrityError("control_store_integrity_invalid")
        expected_previous_policy_id: str | None = None
        for _revision, policy in policy_receipt_order:
            if policy.previous_policy_revision_id != expected_previous_policy_id:
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            expected_previous_policy_id = policy.policy_revision_id
        lineage_series: dict[str, list[PostFinalAssessmentRequestRecord]] = {}
        for request in requests.values():
            receipt = receipts.get(request.accepted_transaction_id)
            event = events.get(request.request_event_id)
            policy = policies.get(request.policy_revision_id)
            if (
                request.run_id != snapshot.run.run_id
                or policy is None
                or policy.policy_fingerprint != request.policy_fingerprint
                or receipt is None
                or event is None
                or event.transaction_id != receipt.transaction_id
                or event.event_type != "post_final_assessment_claimed"
                or event.core_run_binding is not None
                or not any(
                    item.assessment_request_id == request.assessment_request_id
                    for item in receipt.post_final_assessment_requests
                )
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            lineage_series.setdefault(request.finalized_lineage_fingerprint, []).append(
                request
            )
        for series in lineage_series.values():
            series.sort(key=lambda item: item.assessment_generation)
            if [item.assessment_generation for item in series] != list(
                range(1, len(series) + 1)
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            for position, request in enumerate(series):
                if position == 0:
                    if request.predecessor_assessment_request_id is not None:
                        raise ControlStoreIntegrityError(
                            "control_store_integrity_invalid"
                        )
                    continue
                predecessor = series[position - 1]
                if (
                    request.predecessor_assessment_request_id
                    != predecessor.assessment_request_id
                    or request.predecessor_assessment_request_fingerprint
                    != predecessor.request_fingerprint
                ):
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
        for abandonment in abandonments.values():
            receipt = receipts.get(abandonment.accepted_transaction_id)
            event = events.get(abandonment.abandonment_event_id)
            request = requests.get(abandonment.assessment_request_id)
            if (
                abandonment.run_id != snapshot.run.run_id
                or abandonment.assessment_request_id in abandonment_request_ids
                or request is None
                or request.request_fingerprint
                != abandonment.assessment_request_fingerprint
                or request.finalized_lineage_fingerprint
                != abandonment.finalized_lineage_fingerprint
                or request.assessment_generation != abandonment.assessment_generation
                or receipt is None
                or event is None
                or event.transaction_id != receipt.transaction_id
                or event.event_type != "post_final_assessment_abandoned"
                or event.core_run_binding is not None
                or not any(
                    item.abandonment_id == abandonment.abandonment_id
                    for item in receipt.post_final_assessment_abandonments
                )
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            abandonment_request_ids.add(abandonment.assessment_request_id)
        for execution in executions.values():
            receipt = receipts.get(execution.accepted_transaction_id)
            event = events.get(execution.execution_event_id)
            request = requests.get(execution.assessment_request_id)
            if (
                execution.run_id != snapshot.run.run_id
                or request is None
                or request.request_fingerprint
                != execution.assessment_request_fingerprint
                or request.trial_id != execution.trial_id
                or request.finalized_lineage_fingerprint
                != execution.finalized_lineage_fingerprint
                or receipt is None
                or event is None
                or event.transaction_id != receipt.transaction_id
                or event.event_type != "post_final_assessment_execution_recorded"
                or event.core_run_binding is not None
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
        for result in results.values():
            receipt = receipts.get(result.accepted_transaction_id)
            event = events.get(result.result_event_id)
            request = requests.get(result.assessment_request_id)
            if (
                result.run_id != snapshot.run.run_id
                or result.assessment_request_id in result_request_ids
                or result.assessment_request_id in abandonment_request_ids
                or request is None
                or result.policy_revision_id != request.policy_revision_id
                or result.finalized_facts_fingerprint
                != request.finalized_facts_fingerprint
                or result.finalized_lineage_fingerprint
                != request.finalized_lineage_fingerprint
                or receipt is None
                or event is None
                or event.transaction_id != receipt.transaction_id
                or event.event_type != "post_final_assessment_result_recorded"
                or event.core_run_binding is not None
                or not any(
                    item.assessment_result_id == result.assessment_result_id
                    for item in receipt.post_final_assessment_results
                )
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            result_request_ids.add(result.assessment_request_id)
        if result_request_ids & abandonment_request_ids:
            raise ControlStoreIntegrityError("control_store_integrity_invalid")
        for series in lineage_series.values():
            for request in series[1:]:
                predecessor = requests[request.predecessor_assessment_request_id]
                predecessor_result = next(
                    (
                        item
                        for item in results.values()
                        if item.assessment_request_id
                        == predecessor.assessment_request_id
                    ),
                    None,
                )
                predecessor_abandonment = next(
                    (
                        item
                        for item in abandonments.values()
                        if item.assessment_request_id
                        == predecessor.assessment_request_id
                    ),
                    None,
                )
                if (
                    (
                        predecessor_result is not None
                        and predecessor_abandonment is None
                        and (
                            request.predecessor_assessment_result_id
                            != predecessor_result.assessment_result_id
                            or request.predecessor_result_fingerprint
                            != predecessor_result.result_fingerprint
                            or request.predecessor_abandonment_id is not None
                        )
                    )
                    or (
                        predecessor_abandonment is not None
                        and predecessor_result is None
                        and (
                            request.predecessor_abandonment_id
                            != predecessor_abandonment.abandonment_id
                            or request.predecessor_abandonment_fingerprint
                            != predecessor_abandonment.abandonment_fingerprint
                            or request.predecessor_assessment_result_id is not None
                        )
                    )
                    or (
                        (predecessor_result is None)
                        == (predecessor_abandonment is None)
                    )
                ):
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")

        observation_chains: dict[
            tuple[str, str], list[tuple[int, PostFinalHumanObservationRecord]]
        ] = {}
        for observation in observations.values():
            receipt = receipts.get(observation.accepted_transaction_id)
            event = events.get(observation.observation_event_id)
            result = (
                None
                if observation.assessment_result_id is None
                else results.get(observation.assessment_result_id)
            )
            report_revisions = {
                (item.artifact_id, item.revision, item.sha256)
                for item in snapshot.artifact_revisions
            }
            finalized_reader_refs = {
                (reference.artifact_id, reference.revision)
                for render in snapshot.finalize_renders
                for reference in render.reader_artifacts
            }
            report_binding_valid = (
                observation.report_artifact_id,
                observation.report_revision,
                observation.report_sha256,
            ) in report_revisions and (
                observation.report_artifact_id,
                observation.report_revision,
            ) in finalized_reader_refs
            result_request = (
                None if result is None else requests.get(result.assessment_request_id)
            )
            if (
                observation.run_id != snapshot.run.run_id
                or observation.origin != "human"
                or not report_binding_valid
                or receipt is None
                or event is None
                or event.transaction_id != receipt.transaction_id
                or event.event_type != "post_final_human_observation_recorded"
                or event.core_run_binding is not None
                or len(receipt.post_final_human_observations) != 1
                or receipt.post_final_human_observations[0].observation_id
                != observation.observation_id
                or (
                    result is not None
                    and (
                        result.result_fingerprint
                        != observation.assessment_result_fingerprint
                        or result.reader_view_sha256 != observation.reader_view_sha256
                        or result.finalized_lineage_fingerprint
                        != observation.finalized_lineage_fingerprint
                    )
                )
                or (
                    result is not None
                    and (
                        result_request is None
                        or result_request.report_artifact_id
                        != observation.report_artifact_id
                        or result_request.report_revision != observation.report_revision
                        or result_request.report_sha256 != observation.report_sha256
                        or result_request.finalized_lineage_fingerprint
                        != observation.finalized_lineage_fingerprint
                    )
                )
                or (observation.assessment_result_id is not None and result is None)
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            if observation.previous_observation_id is not None:
                predecessor = observations.get(observation.previous_observation_id)
                if (
                    predecessor is None
                    or predecessor.observation_fingerprint
                    != observation.previous_observation_fingerprint
                    or predecessor.finalized_lineage_fingerprint
                    != observation.finalized_lineage_fingerprint
                    or predecessor.report_revision != observation.report_revision
                    or predecessor.report_artifact_id != observation.report_artifact_id
                    or predecessor.report_sha256 != observation.report_sha256
                    or observation.observation_revision
                    != predecessor.observation_revision + 1
                ):
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
            elif observation.observation_revision != 1:
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            root_id = observation.observation_id
            seen_ids: set[str] = set()
            cursor = observation
            while cursor.previous_observation_id is not None:
                if cursor.observation_id in seen_ids:
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
                seen_ids.add(cursor.observation_id)
                predecessor = observations.get(cursor.previous_observation_id)
                if predecessor is None:
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
                root_id = predecessor.observation_id
                cursor = predecessor
            observation_chains.setdefault(
                (observation.finalized_lineage_fingerprint, root_id), []
            ).append((receipt.committed_revision, observation))
        for rows in observation_chains.values():
            rows.sort(key=lambda item: item[1].observation_revision)
            if [item.observation_revision for _revision, item in rows] != list(
                range(1, len(rows) + 1)
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")

        disposition_chains: dict[
            tuple[str, str], list[tuple[int, PostFinalFindingDispositionRecord]]
        ] = {}
        for disposition in dispositions.values():
            receipt = receipts.get(disposition.accepted_transaction_id)
            event = events.get(disposition.disposition_event_id)
            result = results.get(disposition.assessment_result_id)
            if (
                disposition.run_id != snapshot.run.run_id
                or result is None
                or result.result_fingerprint
                != disposition.assessment_result_fingerprint
                or result.finalized_lineage_fingerprint
                != disposition.finalized_lineage_fingerprint
                or result.reader_view_sha256 != disposition.reader_view_sha256
                or receipt is None
                or event is None
                or event.transaction_id != receipt.transaction_id
                or event.event_type != "post_final_finding_disposition_recorded"
                or event.core_run_binding is not None
                or len(receipt.post_final_finding_dispositions) != 1
                or receipt.post_final_finding_dispositions[0].disposition_id
                != disposition.disposition_id
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            disposition_chains.setdefault(
                (disposition.assessment_result_id, disposition.finding_id), []
            ).append((receipt.committed_revision, disposition))
        for rows in disposition_chains.values():
            rows.sort(key=lambda item: item[0])
            previous: str | None = None
            for _revision, disposition in rows:
                if disposition.previous_disposition_id != previous:
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
                previous = disposition.disposition_id

        draft_chains: dict[str, list[PostFinalGuidanceDraftRevision]] = {}
        for draft in drafts.values():
            receipt = receipts.get(draft.accepted_transaction_id)
            event = events.get(draft.draft_event_id)
            disposition = dispositions.get(draft.disposition_id)
            observation = observations.get(draft.observation_id)
            accepted_model_valid = (
                draft.provenance_kind == "accepted_model_finding"
                and disposition is not None
                and disposition.decision == "accept"
                and disposition.disposition_fingerprint == draft.disposition_fingerprint
                and disposition.assessment_result_id == draft.assessment_result_id
                and disposition.assessment_result_fingerprint
                == draft.assessment_result_fingerprint
                and disposition.finding_id == draft.finding_id
                and disposition.finding_fingerprint == draft.finding_fingerprint
                and disposition.finalized_lineage_fingerprint
                == draft.finalized_lineage_fingerprint
            )
            human_observation_valid = (
                draft.provenance_kind == "human_observation"
                and observation is not None
                and observation.observation_fingerprint == draft.observation_fingerprint
                and observation.finalized_lineage_fingerprint
                == draft.finalized_lineage_fingerprint
                and (
                    draft.assessment_result_id is None
                    or (
                        observation.assessment_result_id == draft.assessment_result_id
                        and observation.assessment_result_fingerprint
                        == draft.assessment_result_fingerprint
                    )
                )
            )
            if (
                draft.run_id != snapshot.run.run_id
                or not (accepted_model_valid or human_observation_valid)
                or receipt is None
                or event is None
                or event.transaction_id != receipt.transaction_id
                or event.event_type != "post_final_guidance_draft_recorded"
                or event.core_run_binding is not None
                or len(receipt.post_final_guidance_drafts) != 1
                or receipt.post_final_guidance_drafts[0].guidance_id
                != draft.guidance_id
                or receipt.post_final_guidance_drafts[0].draft_revision
                != draft.draft_revision
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            draft_chains.setdefault(draft.guidance_id, []).append(draft)
        for rows in draft_chains.values():
            rows.sort(key=lambda item: item.draft_revision)
            if [item.draft_revision for item in rows] != list(range(1, len(rows) + 1)):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            for index, draft in enumerate(rows):
                expected_previous = rows[index - 1].draft_revision if index else None
                if draft.previous_draft_revision != expected_previous:
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")

        status_chains: dict[str, list[tuple[int, PostFinalGuidanceStatusRevision]]] = {}
        for status in statuses.values():
            receipt = receipts.get(status.accepted_transaction_id)
            event = events.get(status.status_event_id)
            draft = drafts.get((status.guidance_id, status.draft_revision))
            if (
                status.run_id != snapshot.run.run_id
                or draft is None
                or draft.finalized_lineage_fingerprint
                != status.finalized_lineage_fingerprint
                or draft.guidance_sha256 != status.guidance_sha256
                or receipt is None
                or event is None
                or event.transaction_id != receipt.transaction_id
                or event.event_type != "post_final_guidance_status_recorded"
                or event.core_run_binding is not None
                or len(receipt.post_final_guidance_statuses) != 1
                or receipt.post_final_guidance_statuses[0].status_revision_id
                != status.status_revision_id
            ):
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            status_chains.setdefault(status.guidance_id, []).append(
                (receipt.committed_revision, status)
            )
        for rows in status_chains.values():
            rows.sort(key=lambda item: item[0])
            previous = None
            previous_status = None
            for _revision, status in rows:
                draft = drafts.get((status.guidance_id, status.draft_revision))
                approval_eligible = False
                if draft is not None:
                    if draft.provenance_kind == "human_observation":
                        observation = observations.get(draft.observation_id)
                        status_receipt = receipts.get(status.accepted_transaction_id)
                        observation_receipt = (
                            None
                            if observation is None
                            else receipts.get(observation.accepted_transaction_id)
                        )
                        successors_at_cutoff = {
                            item.previous_observation_id
                            for item in observations.values()
                            if item.previous_observation_id is not None
                            and (
                                (
                                    child_receipt := receipts.get(
                                        item.accepted_transaction_id
                                    )
                                )
                                is not None
                                and status_receipt is not None
                                and child_receipt.committed_revision
                                <= status_receipt.prior_revision
                            )
                        }
                        approval_eligible = (
                            observation is not None
                            and observation.finalized_lineage_fingerprint
                            == draft.finalized_lineage_fingerprint
                            and observation.observation_fingerprint
                            == draft.observation_fingerprint
                            and observation_receipt is not None
                            and status_receipt is not None
                            and observation_receipt.committed_revision
                            <= status_receipt.prior_revision
                            and observation.observation_id not in successors_at_cutoff
                        )
                    else:
                        disposition_rows = disposition_chains.get(
                            (draft.assessment_result_id, draft.finding_id),
                            [],
                        )
                        prior_dispositions = [
                            disposition
                            for committed_revision, disposition in disposition_rows
                            if committed_revision
                            <= receipts[status.accepted_transaction_id].prior_revision
                        ]
                        if prior_dispositions:
                            current_disposition = prior_dispositions[-1]
                            approval_eligible = (
                                current_disposition.disposition_id
                                == draft.disposition_id
                                and current_disposition.decision == "accept"
                            )
                if (
                    status.previous_status_revision_id != previous
                    or not post_final_guidance_status_transition_allowed(
                        previous_status,
                        status,
                        approval_eligible=approval_eligible,
                    )
                ):
                    raise ControlStoreIntegrityError("control_store_integrity_invalid")
                previous = status.status_revision_id
                previous_status = status

    def _verify_guidance_snapshot_structure(
        self,
        snapshot: ControlStoreSnapshot,
    ) -> None:
        """Verify one successor's frozen guidance graph and its historical inputs."""

        graph = (
            snapshot.run_guidance_snapshots,
            snapshot.run_guidance_selection_decisions,
            snapshot.run_guidance_snapshot_items,
        )
        successor_receipts = tuple(
            item
            for item in snapshot.transactions
            if item.transaction_type == _GUIDANCE_SUCCESSOR_TRANSACTION_TYPE
        )
        if not any(graph):
            if successor_receipts:
                raise ControlStoreIntegrityError("control_store_integrity_invalid")
            return
        if len(snapshot.run_guidance_snapshots) != 1 or len(successor_receipts) != 1:
            raise ControlStoreIntegrityError("control_store_integrity_invalid")
        guidance_snapshot = snapshot.run_guidance_snapshots[0]
        receipt = successor_receipts[0]
        bindings = tuple(
            item
            for item in snapshot.run_contract_bindings
            if item.accepted_transaction_id == receipt.transaction_id
        )
        transitions = tuple(
            item
            for item in snapshot.run_head_transitions
            if item.accepted_transaction_id == receipt.transaction_id
        )
        events = {item.event_id: item for item in snapshot.events}
        event = events.get(guidance_snapshot.snapshot_event_id)
        decision_rows = self._connection.execute(
            "SELECT position,decision_id FROM run_guidance_snapshot_decisions "
            "WHERE run_id=? AND snapshot_id=? ORDER BY position",
            (snapshot.run.run_id, guidance_snapshot.snapshot_id),
        ).fetchall()
        item_rows = self._connection.execute(
            "SELECT position,item_id FROM run_guidance_snapshot_selected_items "
            "WHERE run_id=? AND snapshot_id=? ORDER BY position",
            (snapshot.run.run_id, guidance_snapshot.snapshot_id),
        ).fetchall()
        if (
            guidance_snapshot.run_id != snapshot.run.run_id
            or guidance_snapshot.workspace_id != snapshot.workspace_id
            or guidance_snapshot.accepted_transaction_id != receipt.transaction_id
            or len(bindings) != 1
            or len(transitions) != 1
            or guidance_snapshot.request_fingerprint != bindings[0].request_fingerprint
            or transitions[0].predecessor_run_id != guidance_snapshot.predecessor_run_id
            or transitions[0].successor_run_id != snapshot.run.run_id
            or transitions[0].reason_code != "human_started_successor"
            or transitions[0].successor_disposition != "reference"
            or transitions[0].request_fingerprint
            != guidance_snapshot.request_fingerprint
            or event is None
            or event.run_id != snapshot.run.run_id
            or event.transaction_id != receipt.transaction_id
            or event.event_type != "run_guidance_snapshot_frozen"
            or event.core_run_binding is not None
            or [row[0] for row in decision_rows] != list(range(len(decision_rows)))
            or [str(row[1]) for row in decision_rows] != guidance_snapshot.decision_ids
            or [row[0] for row in item_rows] != list(range(len(item_rows)))
            or [str(row[1]) for row in item_rows] != guidance_snapshot.selected_item_ids
            or [item.snapshot_id for item in snapshot.run_guidance_selection_decisions]
            != [
                guidance_snapshot.snapshot_id
                for _item in snapshot.run_guidance_selection_decisions
            ]
            or [item.snapshot_id for item in snapshot.run_guidance_snapshot_items]
            != [
                guidance_snapshot.snapshot_id
                for _item in snapshot.run_guidance_snapshot_items
            ]
            or {item.snapshot_id for item in receipt.run_guidance_snapshots}
            != {guidance_snapshot.snapshot_id}
            or {item.decision_id for item in receipt.run_guidance_selection_decisions}
            != {item.decision_id for item in snapshot.run_guidance_selection_decisions}
            or {item.item_id for item in receipt.run_guidance_snapshot_items}
            != {item.item_id for item in snapshot.run_guidance_snapshot_items}
        ):
            raise ControlStoreIntegrityError("control_store_integrity_invalid")
        if not self._guidance_snapshot_content_is_exact(
            snapshot=guidance_snapshot,
            decisions=snapshot.run_guidance_selection_decisions,
            items=snapshot.run_guidance_snapshot_items,
            successor_binding=bindings[0],
            cutoff_revision=receipt.prior_revision,
        ):
            raise ControlStoreIntegrityError("control_store_integrity_invalid")

    def _verify_checkout_snapshot_structure(
        self, snapshot: ControlStoreSnapshot
    ) -> None:
        graph = (
            snapshot.checkout_revisions,
            snapshot.checkout_revision_members,
            snapshot.receipt_checkout_bindings,
            snapshot.checkout_publication_intents,
            snapshot.checkout_publication_members,
            snapshot.checkout_publication_acks,
            snapshot.checkout_publication_cleanup_observations,
        )
        if not any(graph):
            return
        if not (snapshot.checkout_revisions and snapshot.receipt_checkout_bindings):
            raise ControlStoreIntegrityError("checkout_revision_invalid")
        revisions = {
            item.checkout_revision_id: item for item in snapshot.checkout_revisions
        }
        receipts = {item.transaction_id: item for item in snapshot.transactions}
        built_revisions: dict[
            str,
            tuple[CheckoutRevisionRecord, tuple[CheckoutRevisionMember, ...]],
        ] = {}
        members_by_revision: dict[str, list[CheckoutRevisionMember]] = {}
        for member in snapshot.checkout_revision_members:
            members_by_revision.setdefault(member.checkout_revision_id, []).append(
                member
            )
            revision = revisions.get(member.checkout_revision_id)
            artifact = next(
                (
                    item
                    for item in snapshot.artifact_revisions
                    if item.artifact_id == member.artifact_id
                    and item.revision == member.artifact_revision
                ),
                None,
            )
            if (
                revision is None
                or artifact is None
                or artifact.path != member.canonical_path
                or artifact.sha256 != member.blob_sha256
                or artifact.size_bytes != member.byte_size
            ):
                raise ControlStoreIntegrityError("checkout_revision_invalid")
        for revision in snapshot.checkout_revisions:
            members = sorted(
                members_by_revision.get(revision.checkout_revision_id, []),
                key=lambda item: item.ordinal,
            )
            if len(members) != revision.member_count or [
                m.ordinal for m in members
            ] != list(range(len(members))):
                raise ControlStoreIntegrityError("checkout_revision_invalid")
            artifact_rows = []
            for member in members:
                artifact = next(
                    (
                        item
                        for item in snapshot.artifact_revisions
                        if item.artifact_id == member.artifact_id
                        and item.revision == member.artifact_revision
                    ),
                    None,
                )
                if artifact is None:
                    raise ControlStoreIntegrityError("checkout_revision_invalid")
                artifact_rows.append(artifact)
            try:
                rebuilt_record, rebuilt_members, _manifest_bytes = (
                    _build_checkout_revision_structure(
                        workspace_id=revision.workspace_id,
                        run_id=revision.run_id,
                        transaction_id=revision.creator_transaction_id,
                        created_at=datetime.fromisoformat(
                            revision.created_at.replace("Z", "+00:00")
                        ),
                        artifact_revisions=tuple(artifact_rows),
                        parent_checkout_revision_id=revision.parent_checkout_revision_id,
                    )
                )
            except (_CheckoutStructureError, ValueError) as exc:
                raise ControlStoreIntegrityError("checkout_revision_invalid") from exc
            if rebuilt_record != revision or rebuilt_members != tuple(members):
                raise ControlStoreIntegrityError("checkout_revision_invalid")
            built_revisions[revision.checkout_revision_id] = (
                rebuilt_record,
                rebuilt_members,
            )
            receipt = receipts.get(revision.creator_transaction_id)
            if receipt is None or not any(
                item.checkout_revision_id == revision.checkout_revision_id
                for item in receipt.checkout_revisions
            ):
                raise ControlStoreIntegrityError("checkout_revision_invalid")
        bindings = {
            item.transaction_id: item for item in snapshot.receipt_checkout_bindings
        }
        for transaction_id, binding in bindings.items():
            receipt = receipts.get(transaction_id)
            post = revisions.get(binding.post_checkout_revision_id)
            pre_exists = binding.pre_checkout_revision_id is None
            if binding.pre_checkout_revision_id is not None:
                row = self._connection.execute(
                    """
                    SELECT payload_json FROM checkout_revisions
                    WHERE workspace_id=? AND run_id=?
                      AND checkout_revision_id=?
                    """,
                    (
                        self.workspace_id,
                        binding.pre_run_id,
                        binding.pre_checkout_revision_id,
                    ),
                ).fetchone()
                pre_exists = row is not None
            if (
                receipt is None
                or post is None
                or binding.workspace_id != self.workspace_id
                or binding.run_id != snapshot.run.run_id
                or binding.transaction_id != transaction_id
                or binding.post_run_id != snapshot.run.run_id
                or post.run_id != binding.post_run_id
                or post.workspace_id != binding.workspace_id
                or post.creator_transaction_id != transaction_id
                or post.parent_checkout_revision_id != binding.pre_checkout_revision_id
                or not pre_exists
                or not any(
                    item.transaction_id == transaction_id
                    for item in receipt.receipt_checkout_bindings
                )
            ):
                raise ControlStoreIntegrityError("checkout_revision_invalid")
        intents = {
            (
                item.identity.workspace_id,
                item.identity.run_id,
                item.identity.transaction_id,
                item.identity.checkout_revision_id,
            ): item
            for item in snapshot.checkout_publication_intents
        }
        members_by_intent: dict[
            tuple[str, str, str, str], list[CheckoutPublicationMember]
        ] = {}
        for member in snapshot.checkout_publication_members:
            key = (
                member.identity.workspace_id,
                member.identity.run_id,
                member.identity.transaction_id,
                member.identity.checkout_revision_id,
            )
            members_by_intent.setdefault(key, []).append(member)
        for key, intent in intents.items():
            members = sorted(
                members_by_intent.get(key, []), key=lambda item: item.ordinal
            )
            binding = bindings.get(intent.identity.transaction_id)
            receipt = receipts.get(intent.identity.transaction_id)
            post_structure = built_revisions.get(intent.post_checkout_revision_id)
            pre_structure = (
                None
                if binding is None or binding.pre_checkout_revision_id is None
                else built_revisions.get(binding.pre_checkout_revision_id)
            )
            if (
                pre_structure is None
                and binding is not None
                and binding.pre_checkout_revision_id is not None
                and binding.pre_run_id != snapshot.run.run_id
            ):
                pre_structure = self._load_checkout_structure_in_transaction(
                    binding.pre_run_id,
                    binding.pre_checkout_revision_id,
                )
            if (
                intent.publication_identity_sha256
                != _publication_identity_digest(intent.identity)
                or binding is None
                or receipt is None
                or post_structure is None
                or intent.identity.workspace_id != self.workspace_id
                or intent.identity.run_id != snapshot.run.run_id
                or intent.identity.checkout_revision_id
                != intent.post_checkout_revision_id
                or binding.workspace_id != self.workspace_id
                or binding.run_id != snapshot.run.run_id
                or binding.transaction_id != intent.identity.transaction_id
                or binding.post_run_id != snapshot.run.run_id
                or binding.post_checkout_revision_id != intent.post_checkout_revision_id
                or post_structure[0].parent_checkout_revision_id
                != binding.pre_checkout_revision_id
                or (
                    binding.pre_checkout_revision_id is not None
                    and pre_structure is None
                )
                or tuple(receipt.checkout_revisions)
                != (
                    CheckoutRevisionReference(
                        checkout_revision_id=intent.post_checkout_revision_id
                    ),
                )
                or tuple(receipt.receipt_checkout_bindings)
                != (
                    ReceiptCheckoutBindingReference(
                        transaction_id=intent.identity.transaction_id
                    ),
                )
                or tuple(receipt.checkout_publication_intents)
                != (
                    CheckoutPublicationIntentReference(
                        checkout_revision_id=intent.post_checkout_revision_id
                    ),
                )
            ):
                raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
            try:
                expected_intent, expected_members = _derive_publication_structure(
                    identity=intent.identity,
                    pre_record=(None if pre_structure is None else pre_structure[0]),
                    pre_members=(() if pre_structure is None else pre_structure[1]),
                    post_record=post_structure[0],
                    post_members=post_structure[1],
                    capability_profile_sha256=intent.capability_profile_sha256,
                )
            except _CheckoutStructureError as exc:
                raise ControlStoreIntegrityError(
                    "checkout_publication_journal_invalid"
                ) from exc
            if intent != expected_intent or tuple(members) != expected_members:
                raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
        if set(members_by_intent) - set(intents):
            raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
        ack_by_intent: dict[
            tuple[str, str, str, str], list[CheckoutPublicationAck]
        ] = {}
        for ack in snapshot.checkout_publication_acks:
            key = (
                ack.identity.workspace_id,
                ack.identity.run_id,
                ack.identity.transaction_id,
                ack.identity.checkout_revision_id,
            )
            ack_by_intent.setdefault(key, []).append(ack)
        for key, acks in ack_by_intent.items():
            expected = sorted(
                members_by_intent.get(key, []), key=lambda item: item.ordinal
            )
            intent = intents.get(key)
            ordered_acks = sorted(acks, key=lambda item: item.ordinal)
            if (
                intent is None
                or len(ordered_acks) != len(expected)
                or [ack.ordinal for ack in ordered_acks] != list(range(len(expected)))
                or any(
                    ack.identity != member.identity
                    or ack.publication_identity_sha256
                    != intent.publication_identity_sha256
                    or ack.capability_profile_sha256 != intent.capability_profile_sha256
                    or ack.post_kind != member.post_kind
                    or ack.post_sha256 != member.post_sha256
                    or ack.post_size != member.post_size
                    or ack.verification != "post_verified_durable"
                    or ack.cleanup_policy != "retain_residue_v1"
                    for ack, member in zip(ordered_acks, expected, strict=True)
                )
            ):
                raise ControlStoreIntegrityError("checkout_publication_journal_invalid")

    def _load_checkout_structure_in_transaction(
        self,
        run_id: str,
        checkout_revision_id: str,
    ) -> tuple[CheckoutRevisionRecord, tuple[CheckoutRevisionMember, ...]]:
        """Rebuild one cross-run publication preimage on this connection."""

        records = tuple(
            item
            for item in self._load_for_run(
                CheckoutRevisionRecord,
                "checkout_revisions",
                run_id,
                "created_at, checkout_revision_id",
                {
                    "checkout_revision_id": "checkout_revision_id",
                    "workspace_id": "workspace_id",
                    "run_id": "run_id",
                    "parent_checkout_revision_id": "parent_checkout_revision_id",
                    "schema_version": "schema_version",
                    "manifest_sha256": "manifest_sha256",
                    "tree_sha256": "tree_sha256",
                    "member_count": "member_count",
                    "created_at": "created_at",
                    "creator_transaction_id": "creator_transaction_id",
                },
            )
            if item.checkout_revision_id == checkout_revision_id
        )
        if len(records) != 1:
            raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
        members = tuple(
            item
            for item in self._load_for_run(
                CheckoutRevisionMember,
                "checkout_revision_members",
                run_id,
                "checkout_revision_id, ordinal",
                {
                    "checkout_revision_id": "checkout_revision_id",
                    "ordinal": "ordinal",
                    "workspace_id": "workspace_id",
                    "run_id": "run_id",
                    "schema_version": "schema_version",
                    "canonical_path": "canonical_path",
                    "artifact_id": "artifact_id",
                    "artifact_revision": "artifact_revision",
                    "blob_sha256": "blob_sha256",
                    "byte_size": "byte_size",
                },
            )
            if item.checkout_revision_id == checkout_revision_id
        )
        artifact_revisions = {
            (item.artifact_id, item.revision): item
            for item in self._load_for_run(
                ArtifactRevision,
                "artifact_revisions",
                run_id,
                "artifact_id, revision",
                {
                    "run_id": "run_id",
                    "artifact_id": "artifact_id",
                    "revision": "revision",
                    "schema_version": "schema_version",
                    "path": "path",
                    "sha256": "sha256",
                    "size_bytes": "size_bytes",
                    "frozen": "frozen",
                    "producer_kind": "producer_kind",
                    "producer_id": "producer_id",
                    "created_at": "created_at",
                },
            )
        }
        try:
            rebuilt_record, rebuilt_members, _manifest_bytes = (
                _build_checkout_revision_structure(
                    workspace_id=records[0].workspace_id,
                    run_id=run_id,
                    transaction_id=records[0].creator_transaction_id,
                    created_at=datetime.fromisoformat(
                        records[0].created_at.replace("Z", "+00:00")
                    ),
                    artifact_revisions=tuple(
                        artifact_revisions[(item.artifact_id, item.artifact_revision)]
                        for item in members
                    ),
                    parent_checkout_revision_id=(
                        records[0].parent_checkout_revision_id
                    ),
                )
            )
        except (KeyError, _CheckoutStructureError, ValueError) as exc:
            raise ControlStoreIntegrityError(
                "checkout_publication_journal_invalid"
            ) from exc
        if rebuilt_record != records[0] or rebuilt_members != members:
            raise ControlStoreIntegrityError("checkout_publication_journal_invalid")
        return rebuilt_record, rebuilt_members

    def _verify_core_snapshot_structure(self, snapshot: ControlStoreSnapshot) -> None:
        """Verify PR-4A relation closure without interpreting domain policy."""

        core_rows_exist = any(
            (
                snapshot.run_contract_bindings,
                snapshot.run_execution_authorizations,
                snapshot.run_source_discovery_authorizations,
                snapshot.run_source_acquisition_attempt_authorizations,
                snapshot.owned_artifact_submissions,
                snapshot.stage_transitions,
                snapshot.stage_artifact_bindings,
                snapshot.stage_gate_bindings,
                snapshot.claims,
                snapshot.claim_source_bindings,
                snapshot.claim_freezes,
                snapshot.gate_evaluations,
                snapshot.gate_findings,
                snapshot.gate_artifact_bindings,
                snapshot.run_integrity_records,
                snapshot.repair_cycles,
                snapshot.gate_repair_cycles,
                snapshot.gate_repair_artifact_bindings,
                snapshot.gate_repair_outcomes,
                snapshot.artifact_supersessions,
                snapshot.repair_completions,
                snapshot.recovery_completions,
                snapshot.run_head_transitions,
                snapshot.finalize_renders,
                snapshot.finalizations,
                snapshot.run_archives,
                snapshot.run_archive_artifact_bindings,
                snapshot.package_ready_records,
                snapshot.package_artifact_bindings,
                snapshot.approval_package_bindings,
                snapshot.delivery_authorizations,
                snapshot.delivery_attempts,
                snapshot.delivery_results,
            )
        )
        if not snapshot.run_contract_bindings:
            if core_rows_exist:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            return
        if len(snapshot.run_contract_bindings) != 1:
            raise ControlStoreIntegrityError("core_run_relation_invalid")

        binding = snapshot.run_contract_bindings[0]
        head = snapshot.workspace_run_head
        if (
            binding.run_id != snapshot.run.run_id
            or binding.workspace_id != snapshot.workspace_id
            or binding.workspace_id != snapshot.run.workspace_id
            or binding.runtime != snapshot.run.runtime
            or head is None
            or head.workspace_id != snapshot.workspace_id
        ):
            raise ControlStoreIntegrityError("core_run_relation_invalid")

        expected_fingerprint = canonical_fingerprint(
            {
                "runtime": binding.runtime,
                "stage_specs_schema": binding.stage_specs_schema,
                "stage_specs_sha256": binding.stage_specs_sha256,
                "artifact_contracts_schema": binding.artifact_contracts_schema,
                "artifact_contracts_sha256": binding.artifact_contracts_sha256,
                "policy_pack_schema": binding.policy_pack_schema,
                "policy_pack_name": binding.policy_pack_name,
                "policy_pack_sha256": binding.policy_pack_sha256,
                "runtime_adapter_sha256": binding.runtime_adapter_sha256,
                "runtime_adapter_fingerprint": binding.runtime_adapter_fingerprint,
                "runtime_source_plan_sha256": binding.runtime_source_plan_sha256,
                "runtime_source_plan_fingerprint": binding.runtime_source_plan_fingerprint,
                "run_direction": canonical_run_direction_for_binding(
                    binding.run_direction.model_dump(
                        mode="json",
                        exclude_unset=False,
                    )
                ),
                "workspace_config_sha256": binding.workspace_config_sha256,
                "sources_config_sha256": binding.sources_config_sha256,
                "role_topology": binding.role_topology,
                "gate_strictness": binding.gate_strictness,
                "input_governance_required": binding.input_governance_required,
            }
        )
        if binding.contract_fingerprint != expected_fingerprint:
            raise ControlStoreIntegrityError("core_run_relation_invalid")

        self._verify_pr4b_snapshot_relations(snapshot)

        receipts = {item.transaction_id: item for item in snapshot.transactions}
        events = {item.event_id: item for item in snapshot.events}
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        initialization = receipts.get(binding.accepted_transaction_id)
        contract_refs = {
            (
                binding.stage_specs_artifact.artifact_id,
                binding.stage_specs_artifact.revision,
                binding.stage_specs_sha256,
            ),
            (
                binding.artifact_contracts_artifact.artifact_id,
                binding.artifact_contracts_artifact.revision,
                binding.artifact_contracts_sha256,
            ),
            (
                binding.policy_pack_artifact.artifact_id,
                binding.policy_pack_artifact.revision,
                binding.policy_pack_sha256,
            ),
            (
                binding.runtime_adapter_artifact.artifact_id,
                binding.runtime_adapter_artifact.revision,
                binding.runtime_adapter_sha256,
            ),
            (
                binding.runtime_source_plan_artifact.artifact_id,
                binding.runtime_source_plan_artifact.revision,
                binding.runtime_source_plan_sha256,
            ),
        }
        if (
            initialization is None
            or initialization.run_id != snapshot.run.run_id
            or initialization.transaction_type
            not in {
                "core-v2-initialize",
                "core-v2-run-reset",
                _GUIDANCE_SUCCESSOR_TRANSACTION_TYPE,
            }
            or [item.run_id for item in initialization.run_contract_bindings]
            != [snapshot.run.run_id]
            or binding.initialization_event_id not in initialization.event_ids
            or (
                initialization.transaction_type
                in {"core-v2-run-reset", _GUIDANCE_SUCCESSOR_TRANSACTION_TYPE}
                and len(initialization.run_head_transitions) != 1
            )
            or (
                initialization.transaction_type == "core-v2-initialize"
                and initialization.run_head_transitions
            )
        ):
            raise ControlStoreIntegrityError("core_run_relation_invalid")
        if initialization.run_head_transitions:
            head_transition_id = initialization.run_head_transitions[
                0
            ].head_transition_id
            matching_head_transitions = tuple(
                item
                for item in snapshot.run_head_transitions
                if item.head_transition_id == head_transition_id
            )
            expected_transition = (
                ("run_reset", "non_reference")
                if initialization.transaction_type == "core-v2-run-reset"
                else ("human_started_successor", "reference")
            )
            if (
                len(matching_head_transitions) != 1
                or matching_head_transitions[0].successor_run_id != snapshot.run.run_id
                or matching_head_transitions[0].accepted_transaction_id
                != initialization.transaction_id
                or (
                    matching_head_transitions[0].reason_code,
                    matching_head_transitions[0].successor_disposition,
                )
                != expected_transition
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        init_event = events.get(binding.initialization_event_id)
        if (
            init_event is None
            or init_event.transaction_id != initialization.transaction_id
            or (
                initialization.transaction_type == "core-v2-initialize"
                and (
                    init_event.core_run_binding is None
                    or init_event.core_run_binding.effect_kind != "initialize"
                    or init_event.core_run_binding.primary_record_id
                    != snapshot.run.run_id
                )
            )
            or (
                initialization.transaction_type
                in {"core-v2-run-reset", _GUIDANCE_SUCCESSOR_TRANSACTION_TYPE}
                and (
                    init_event.event_type != "run_initialized"
                    or init_event.core_run_binding is not None
                )
            )
        ):
            raise ControlStoreIntegrityError("core_run_relation_invalid")
        receipt_revision_refs = {
            (item.artifact_id, item.revision)
            for item in initialization.artifact_revisions
        }
        for artifact_id, revision_number, digest in contract_refs:
            revision = revisions.get((artifact_id, revision_number))
            if (
                revision is None
                or revision.sha256 != digest
                or (artifact_id, revision_number) not in receipt_revision_refs
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")

        if len(snapshot.run_source_discovery_authorizations) > 1:
            raise ControlStoreIntegrityError("core_run_relation_invalid")
        for authorization in snapshot.run_source_discovery_authorizations:
            owner = receipts.get(authorization.accepted_transaction_id)
            if (
                authorization.run_id != snapshot.run.run_id
                or authorization.workspace_id != snapshot.workspace_id
                or authorization.run_contract_fingerprint
                != binding.contract_fingerprint
                or authorization.run_direction_fingerprint
                != canonical_fingerprint(
                    canonical_run_direction_for_binding(
                        binding.run_direction.model_dump(
                            mode="json",
                            exclude_unset=False,
                        )
                    )
                )
                or authorization.runtime_source_plan_fingerprint
                != binding.runtime_source_plan_fingerprint
                or owner is None
                or owner.transaction_type != "core-v2-initialize"
                or [
                    item.authorization_id
                    for item in owner.run_source_discovery_authorizations
                ]
                != [authorization.authorization_id]
                or authorization.authorization_event_id not in owner.event_ids
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")

        attempts = list(snapshot.run_source_acquisition_attempt_authorizations)
        legacy_discovery = (
            bool(snapshot.run_source_discovery_authorizations)
            and initialization.committed_revision
            <= self._legacy_source_attempt_receipt_cutoff()
        )
        if snapshot.run_source_discovery_authorizations and not legacy_discovery:
            if not attempts:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        if attempts and not snapshot.run_source_discovery_authorizations:
            raise ControlStoreIntegrityError("core_run_relation_invalid")
        for index, attempt in enumerate(attempts, start=1):
            owner = receipts.get(attempt.accepted_transaction_id)
            discovery = snapshot.run_source_discovery_authorizations[0]
            if (
                attempt.run_id != snapshot.run.run_id
                or attempt.workspace_id != snapshot.workspace_id
                or attempt.discovery_authorization_id != discovery.authorization_id
                or attempt.run_contract_fingerprint
                != discovery.run_contract_fingerprint
                or attempt.run_direction_fingerprint
                != discovery.run_direction_fingerprint
                or attempt.runtime_source_plan_fingerprint
                != discovery.runtime_source_plan_fingerprint
                or attempt.source_route_fingerprint
                != discovery.source_route_fingerprint
                or attempt.provider_id != discovery.provider_id
                or attempt.route_id != discovery.route_id
                or attempt.attempt_ordinal != index
                or attempt.previous_attempt_authorization_id
                != (
                    None if index == 1 else attempts[index - 2].attempt_authorization_id
                )
                or owner is None
                or (index == 1 and owner.transaction_type != "core-v2-initialize")
                or (
                    index > 1
                    and owner.transaction_type
                    != "core-v2-source-acquisition-attempt-authorize"
                )
                or [
                    item.attempt_authorization_id
                    for item in (owner.run_source_acquisition_attempt_authorizations)
                ]
                != [attempt.attempt_authorization_id]
                or attempt.authorization_event_id not in owner.event_ids
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")

        transitions_by_stage: dict[str, list[StageTransitionRecord]] = {}
        transition_by_id: dict[str, StageTransitionRecord] = {}
        for transition in snapshot.stage_transitions:
            if transition.transition_id in transition_by_id:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            transition_by_id[transition.transition_id] = transition
            transitions_by_stage.setdefault(transition.stage_id, []).append(transition)
        states = {item.stage_id: item for item in snapshot.stage_states}
        if set(states) != set(transitions_by_stage):
            raise ControlStoreIntegrityError("core_run_relation_invalid")
        initial_transition_ids: set[str] = set()
        for stage_id, state in states.items():
            rows = sorted(
                transitions_by_stage[stage_id],
                key=lambda item: item.result_revision,
            )
            if not rows or rows[0].transition_kind != "initialize":
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            initial_transition_ids.add(rows[0].transition_id)
            for position, transition in enumerate(rows):
                if (
                    transition.result_revision != position
                    or transition.run_contract_fingerprint
                    != binding.contract_fingerprint
                ):
                    raise ControlStoreIntegrityError("core_run_relation_invalid")
                if position and (
                    transition.prior_revision != position - 1
                    or transition.prior_status != rows[position - 1].result_status
                ):
                    raise ControlStoreIntegrityError("core_run_relation_invalid")
            if (
                state.revision != rows[-1].result_revision
                or state.status != rows[-1].result_status
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        if {
            item.transition_id for item in initialization.stage_transitions
        } != initial_transition_ids:
            raise ControlStoreIntegrityError("core_run_relation_invalid")

        integrity_rows = sorted(
            snapshot.run_integrity_records,
            key=lambda item: item.integrity_revision,
        )
        if (
            not integrity_rows
            or integrity_rows[0].integrity_revision != 1
            or integrity_rows[0].status != "clean"
            or [
                item.integrity_revision for item in initialization.run_integrity_records
            ]
            != [1]
        ):
            raise ControlStoreIntegrityError("core_run_relation_invalid")
        prior_status: str | None = None
        for position, record in enumerate(integrity_rows, start=1):
            expected_prior = None if position == 1 else position - 1
            if (
                record.integrity_revision != position
                or record.prior_integrity_revision != expected_prior
                or (position == 1 and record.status != "clean")
                or (prior_status is not None and record.status == prior_status)
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            prior_status = record.status

        invocation_events: dict[str, list[EventEnvelope]] = {}
        for event in snapshot.events:
            core = event.core_run_binding
            if core is not None and core.effect_kind == "invocation_start":
                invocation_events.setdefault(core.primary_record_id, []).append(event)
        source_invocations = [item.invocation_id for item in snapshot.sources]
        proposal_invocations = [
            item.invocation_id for item in snapshot.accepted_proposals
        ]
        submission_invocations = [
            item.invocation_id
            for item in snapshot.owned_artifact_submissions
            if item.invocation_id is not None and item.source_proposal_id is None
        ]
        failed_records = [
            event.intake_binding.invocation_id
            for event in snapshot.events
            if event.intake_binding is not None
            and event.intake_binding.outcome == "rejected"
        ]
        for invocation in snapshot.invocations:
            start_events = invocation_events.get(invocation.invocation_id, [])
            if (
                len(start_events) != 1
                or start_events[0].transaction_id is None
                or start_events[0].run_id != snapshot.run.run_id
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            source_explanations = source_invocations.count(invocation.invocation_id)
            proposal_explanations = proposal_invocations.count(invocation.invocation_id)
            submission_explanations = submission_invocations.count(
                invocation.invocation_id
            )
            explanation_kinds = sum(
                count > 0
                for count in (
                    source_explanations,
                    proposal_explanations,
                    submission_explanations,
                )
            )
            failures = failed_records.count(invocation.invocation_id)
            if invocation.status == "active" and (explanation_kinds or failures):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            if invocation.status == "completed" and (
                explanation_kinds != 1
                or proposal_explanations > 1
                or submission_explanations > 1
                or failures
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            if invocation.status == "failed" and (failures != 1 or explanation_kinds):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        if set(invocation_events) != {
            item.invocation_id for item in snapshot.invocations
        }:
            raise ControlStoreIntegrityError("core_run_relation_invalid")

        producer_transactions: dict[tuple[str, int], set[str]] = {}

        def add_producer(
            artifact_id: str,
            revision_number: int,
            transaction_id: str,
        ) -> None:
            producer_transactions.setdefault(
                (artifact_id, revision_number),
                set(),
            ).add(transaction_id)

        for artifact_id, revision_number, _digest in contract_refs:
            add_producer(
                artifact_id,
                revision_number,
                initialization.transaction_id,
            )
        for authorization in snapshot.run_execution_authorizations:
            add_producer(
                authorization.source_manifest_artifact.artifact_id,
                authorization.source_manifest_artifact.revision,
                authorization.accepted_transaction_id,
            )
        for source in snapshot.sources:
            add_producer(
                source.content_artifact_id,
                source.content_artifact_revision,
                source.accepted_transaction_id,
            )
            if source.raw_payload_artifact_id is not None:
                add_producer(
                    source.raw_payload_artifact_id,
                    cast(int, source.raw_payload_artifact_revision),
                    source.accepted_transaction_id,
                )
        for proposal in snapshot.accepted_proposals:
            add_producer(
                proposal.artifact_id,
                proposal.artifact_revision,
                proposal.accepted_transaction_id,
            )
        for submission in snapshot.owned_artifact_submissions:
            add_producer(
                submission.artifact_id,
                submission.artifact_revision,
                submission.accepted_transaction_id,
            )
        for freeze in snapshot.claim_freezes:
            add_producer(
                freeze.ledger_artifact.artifact_id,
                freeze.ledger_artifact.revision,
                freeze.accepted_transaction_id,
            )
        for evaluation in snapshot.gate_evaluations:
            add_producer(
                evaluation.report_artifact.artifact_id,
                evaluation.report_artifact.revision,
                evaluation.accepted_transaction_id,
            )
        for render in snapshot.finalize_renders:
            for reference in render.reader_artifacts:
                add_producer(
                    reference.artifact_id,
                    reference.revision,
                    render.accepted_transaction_id,
                )
        for archive in snapshot.run_archives:
            add_producer(
                archive.archive_artifact.artifact_id,
                archive.archive_artifact.revision,
                archive.accepted_transaction_id,
            )
        for package in snapshot.package_ready_records:
            add_producer(
                package.package_manifest_artifact.artifact_id,
                package.package_manifest_artifact.revision,
                package.accepted_transaction_id,
            )
        for result in snapshot.delivery_results:
            if (
                result.status != "bundle_prepared"
                and result.evidence_artifact is not None
            ):
                add_producer(
                    result.evidence_artifact.artifact_id,
                    result.evidence_artifact.revision,
                    result.accepted_transaction_id,
                )
        artifacts_by_id = {item.artifact_id: item for item in snapshot.artifacts}
        for receipt in receipts.values():
            unclaimed = [
                item
                for item in receipt.artifact_revisions
                if (item.artifact_id, item.revision) not in producer_transactions
            ]
            if not unclaimed:
                continue
            failure_evidence = [
                event.intake_binding.source_acquisition_failure
                for event in snapshot.events
                if event.transaction_id == receipt.transaction_id
                and event.intake_binding is not None
                and event.intake_binding.source_acquisition_failure is not None
            ]
            successful_discovery = (
                len(receipt.run_source_discovery_authorizations) == 1
                and len(receipt.run_execution_authorizations) == 1
                and not failure_evidence
            )
            failed_discovery = (
                len(receipt.run_source_discovery_authorizations) == 1
                and len(receipt.run_source_acquisition_attempt_authorizations) == 1
                and not receipt.run_execution_authorizations
                and len(failure_evidence) == 1
                and failure_evidence[0].provider_response_artifact is not None
                and receipt.run_source_acquisition_attempt_authorizations[
                    0
                ].attempt_authorization_id
                == failure_evidence[0].attempt_authorization_id
                and len(
                    [
                        item
                        for item in snapshot.invocations
                        if item.invocation_id == failure_evidence[0].invocation_id
                        and item.status == "failed"
                    ]
                )
                == 1
            )
            if not successful_discovery and not failed_discovery:
                continue
            if len(unclaimed) != 1:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            response_ref = unclaimed[0]
            if failed_discovery:
                failure_response_ref = failure_evidence[0].provider_response_artifact
                if (
                    failure_response_ref is None
                    or response_ref.artifact_id != failure_response_ref.artifact_id
                    or response_ref.revision != failure_response_ref.revision
                ):
                    raise ControlStoreIntegrityError("core_run_relation_invalid")
            response_revision = revisions.get(
                (response_ref.artifact_id, response_ref.revision)
            )
            response_artifact = artifacts_by_id.get(response_ref.artifact_id)
            if (
                response_revision is None
                or response_artifact is None
                or response_ref.revision != 1
                or response_revision.producer_id != "source-discovery"
                or response_artifact.format != "json"
                or response_artifact.current_revision != 1
                or not response_revision.frozen
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            add_producer(
                response_ref.artifact_id,
                response_ref.revision,
                receipt.transaction_id,
            )
        revisions_by_artifact: dict[str, list[ArtifactRevision]] = {}
        for revision in snapshot.artifact_revisions:
            revisions_by_artifact.setdefault(revision.artifact_id, []).append(revision)
        for artifact in snapshot.artifacts:
            values = sorted(
                revisions_by_artifact.get(artifact.artifact_id, []),
                key=lambda item: item.revision,
            )
            if artifact.current_revision == 0:
                if values or artifact.status != "expected":
                    raise ControlStoreIntegrityError("core_run_relation_invalid")
                continue
            if (
                not values
                or values[-1].revision != artifact.current_revision
                or [item.revision for item in values]
                != list(range(1, artifact.current_revision + 1))
                or artifact.status != "valid"
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            for revision in values:
                producers = producer_transactions.get(
                    (revision.artifact_id, revision.revision),
                    set(),
                )
                if len(producers) != 1:
                    raise ControlStoreIntegrityError("core_run_relation_invalid")
                receipt = receipts.get(next(iter(producers)))
                if receipt is None or (
                    revision.artifact_id,
                    revision.revision,
                ) not in {
                    (item.artifact_id, item.revision)
                    for item in receipt.artifact_revisions
                }:
                    raise ControlStoreIntegrityError("core_run_relation_invalid")

        for artifact_binding in snapshot.stage_artifact_bindings:
            transition = transition_by_id.get(artifact_binding.transition_id)
            revision = revisions.get(
                (
                    artifact_binding.artifact_id,
                    artifact_binding.artifact_revision,
                )
            )
            if (
                transition is None
                or revision is None
                or revision.sha256 != artifact_binding.artifact_sha256
                or transition.accepted_transaction_id
                != artifact_binding.accepted_transaction_id
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        evaluations = {item.evaluation_id: item for item in snapshot.gate_evaluations}
        for gate_binding in snapshot.stage_gate_bindings:
            transition = transition_by_id.get(gate_binding.transition_id)
            evaluation = evaluations.get(gate_binding.evaluation_id)
            if (
                transition is None
                or evaluation is None
                or evaluation.gate_id != gate_binding.gate_id
                or evaluation.stage_id != transition.stage_id
                or transition.accepted_transaction_id
                != gate_binding.accepted_transaction_id
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")

        claims_by_id = {item.claim_id: item for item in snapshot.claims}
        bindings_by_claim: dict[str, list[ClaimSourceBinding]] = {}
        for source_binding in snapshot.claim_source_bindings:
            if source_binding.claim_id not in claims_by_id:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            bindings_by_claim.setdefault(source_binding.claim_id, []).append(
                source_binding
            )
        for freeze in snapshot.claim_freezes:
            receipt = receipts.get(freeze.accepted_transaction_id)
            freeze_claims = {
                item.claim_id
                for item in snapshot.claims
                if item.accepted_transaction_id == freeze.accepted_transaction_id
            }
            freeze_bindings = {
                (item.claim_id, item.source_id)
                for item in snapshot.claim_source_bindings
                if item.accepted_transaction_id == freeze.accepted_transaction_id
            }
            if (
                receipt is None
                or {item.freeze_id for item in receipt.claim_freezes}
                != {freeze.freeze_id}
                or {item.claim_id for item in receipt.claims} != freeze_claims
                or {
                    (item.claim_id, item.source_id)
                    for item in receipt.claim_source_bindings
                }
                != freeze_bindings
                or (
                    freeze.ledger_artifact.artifact_id,
                    freeze.ledger_artifact.revision,
                )
                not in {
                    (item.artifact_id, item.revision)
                    for item in receipt.artifact_revisions
                }
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        if bool(snapshot.claims or snapshot.claim_source_bindings) != bool(
            snapshot.claim_freezes
        ):
            raise ControlStoreIntegrityError("core_run_relation_invalid")

        findings_by_evaluation: dict[str, set[str]] = {}
        for finding in snapshot.gate_findings:
            if finding.evaluation_id not in evaluations:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            findings_by_evaluation.setdefault(finding.evaluation_id, set()).add(
                finding.finding_id
            )
        bindings_by_evaluation: dict[str, list[GateArtifactBinding]] = {}
        for gate_binding in snapshot.gate_artifact_bindings:
            if gate_binding.evaluation_id not in evaluations:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            bindings_by_evaluation.setdefault(
                gate_binding.evaluation_id,
                [],
            ).append(gate_binding)
        evaluations_by_transaction: dict[str, list[GateEvaluationRecord]] = {}
        for evaluation in snapshot.gate_evaluations:
            if set(evaluation.finding_ids) != findings_by_evaluation.get(
                evaluation.evaluation_id,
                set(),
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            positions = sorted(
                item.position
                for item in bindings_by_evaluation.get(
                    evaluation.evaluation_id,
                    [],
                )
            )
            if positions != list(range(len(positions))):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            evaluations_by_transaction.setdefault(
                evaluation.accepted_transaction_id,
                [],
            ).append(evaluation)
        for (
            transaction_id,
            transaction_evaluations,
        ) in evaluations_by_transaction.items():
            receipt = receipts.get(transaction_id)
            evaluation_ids = {item.evaluation_id for item in transaction_evaluations}
            finding_ids = {
                (item.evaluation_id, item.finding_id)
                for item in snapshot.gate_findings
                if item.accepted_transaction_id == transaction_id
            }
            input_ids = {
                (item.evaluation_id, item.position)
                for item in snapshot.gate_artifact_bindings
                if item.accepted_transaction_id == transaction_id
            }
            report_refs = {
                (item.report_artifact.artifact_id, item.report_artifact.revision)
                for item in transaction_evaluations
            }
            if (
                receipt is None
                or {item.evaluation_id for item in receipt.gate_evaluations}
                != evaluation_ids
                or {
                    (item.evaluation_id, item.finding_id)
                    for item in receipt.gate_findings
                }
                != finding_ids
                or {
                    (item.evaluation_id, item.position)
                    for item in receipt.gate_artifact_bindings
                }
                != input_ids
                or not report_refs
                <= {
                    (item.artifact_id, item.revision)
                    for item in receipt.artifact_revisions
                }
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")

    def _verify_pr4b_snapshot_relations(self, snapshot: ControlStoreSnapshot) -> None:
        """Match list-valued PR-4B payload fields to their relation rows."""

        def values(
            table: str, owner_column: str, owner_id: str, value_column: str
        ) -> tuple[str, ...]:
            rows = self._connection.execute(
                f"SELECT position, {value_column} FROM {table} "
                f"WHERE run_id=? AND {owner_column}=? ORDER BY position",
                (snapshot.run.run_id, owner_id),
            ).fetchall()
            if [row[0] for row in rows] != list(range(len(rows))):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            return tuple(str(row[1]) for row in rows)

        for record in snapshot.repair_completions:
            if values(
                "repair_completion_supersessions",
                "repair_completion_id",
                record.repair_completion_id,
                "supersession_id",
            ) != tuple(record.supersession_ids):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            if values(
                "repair_completion_transitions",
                "repair_completion_id",
                record.repair_completion_id,
                "transition_id",
            ) != tuple(record.reopened_transition_ids):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        for record in snapshot.recovery_completions:
            for table, column, expected in (
                ("recovery_supersessions", "supersession_id", record.supersession_ids),
                (
                    "recovery_stage_transitions",
                    "transition_id",
                    record.rerun_transition_ids,
                ),
                (
                    "recovery_gate_evaluations",
                    "evaluation_id",
                    record.gate_evaluation_ids,
                ),
            ):
                if values(table, "recovery_id", record.recovery_id, column) != tuple(
                    expected
                ):
                    raise ControlStoreIntegrityError("core_run_relation_invalid")
        revision_digests = {
            (item.artifact_id, item.revision): item.sha256
            for item in snapshot.artifact_revisions
        }
        for record in snapshot.finalize_renders:
            rows = self._connection.execute(
                "SELECT position,artifact_id,artifact_revision,artifact_sha256 "
                "FROM finalize_render_artifacts WHERE run_id=? AND render_id=? ORDER BY position",
                (record.run_id, record.render_id),
            ).fetchall()
            expected = tuple(
                (item.artifact_id, item.revision) for item in record.reader_artifacts
            )
            actual = tuple((str(row[1]), int(row[2])) for row in rows)
            if [row[0] for row in rows] != list(range(len(rows))) or actual != expected:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            if any(
                revision_digests.get((str(row[1]), int(row[2]))) != str(row[3])
                for row in rows
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        for record in snapshot.finalizations:
            if values(
                "finalization_gate_evaluations",
                "finalization_id",
                record.finalization_id,
                "evaluation_id",
            ) != tuple(record.finalize_gate_evaluation_ids):
                raise ControlStoreIntegrityError("core_run_relation_invalid")

    def _load_workspace_run_head_in_transaction(self) -> WorkspaceRunHead | None:
        rows = self._connection.execute(
            "SELECT * FROM workspace_run_heads WHERE workspace_id = ?",
            (self.workspace_id,),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ControlStoreIntegrityError("workspace_run_head_invalid")
        return self._decode_checked(
            WorkspaceRunHead,
            rows[0],
            {
                "workspace_id": "workspace_id",
                "schema_version": "schema_version",
                "current_run_id": "current_run_id",
                "updated_at": "updated_at",
            },
        )

    def _load_for_run(
        self,
        model_type: type[_ModelT],
        table: str,
        run_id: str,
        order_by: str,
        columns: dict[str, str],
        *,
        run_column: str = "run_id",
    ) -> tuple[_ModelT, ...]:
        # Table and ordering values are closed internal constants above.
        rows = self._connection.execute(
            f"SELECT * FROM {table} WHERE {run_column} = ? ORDER BY {order_by}",
            (run_id,),
        ).fetchall()
        return tuple(self._decode_checked(model_type, row, columns) for row in rows)

    def _decode_checked(
        self,
        model_type: type[_ModelT],
        row: sqlite3.Row,
        columns: dict[str, str],
    ) -> _ModelT:
        model = _decode_record(
            model_type,
            str(row["payload_json"]),
            receipt_committed_revision=(
                int(row["committed_revision"])
                if model_type is TransactionReceipt
                else None
            ),
            legacy_receipt_max_committed_revision=(
                self._legacy_receipt_cutoff()
                if model_type is TransactionReceipt
                else None
            ),
            legacy_source_attempt_receipt_max_committed_revision=(
                self._legacy_source_attempt_receipt_cutoff()
                if model_type is TransactionReceipt
                else None
            ),
            legacy_post_final_abandonment_receipt_max_committed_revision=(
                self._legacy_post_final_abandonment_receipt_cutoff()
                if model_type is TransactionReceipt
                else None
            ),
        )
        for column, attribute in columns.items():
            stored = row[column]
            expected: object = model
            for component in attribute.split("."):
                if expected is None:
                    break
                expected = getattr(expected, component)
            if type(expected) is bool:
                expected = int(expected)
            if stored != expected:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        if model_type is EventEnvelope:
            metadata_text = canonical_json_bytes(model.metadata).decode("utf-8")
            if row["metadata_json"] != metadata_text:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        elif model_type is AcceptedSourceRecord:
            locator_text = canonical_json_bytes(
                model.locator.model_dump(mode="json")
            ).decode("utf-8")
            if row["locator_json"] != locator_text:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        elif model_type is AcceptedProposalRecord:
            source_ids_text = canonical_json_bytes(model.source_ids).decode("utf-8")
            if row["source_ids_json"] != source_ids_text:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        elif model_type is PostFinalHumanObservationRecord:
            span_text = (
                "null"
                if model.report_span is None
                else canonical_json_bytes(
                    model.report_span.model_dump(mode="json")
                ).decode("utf-8")
            )
            if row["report_span_json"] != span_text:
                raise ControlStoreIntegrityError("stored_payload_identity_mismatch")
        return model

    def _legacy_receipt_cutoff(self) -> int:
        rows = self._connection.execute(
            """
            SELECT boundary_id,legacy_receipt_max_committed_revision
            FROM transaction_receipt_compatibility_boundaries
            WHERE workspace_id=?
            """,
            (self.workspace_id,),
        ).fetchall()
        if (
            len(rows) != 1
            or rows[0]["boundary_id"] != _RECEIPT_COMPATIBILITY_BOUNDARY_ID
            or type(rows[0]["legacy_receipt_max_committed_revision"]) is not int
            or rows[0]["legacy_receipt_max_committed_revision"] < 0
        ):
            raise ControlStoreIntegrityError("control_store_integrity_invalid")
        return int(rows[0]["legacy_receipt_max_committed_revision"])

    def _legacy_source_attempt_receipt_cutoff(self) -> int:
        rows = self._connection.execute(
            """
            SELECT boundary_id,legacy_receipt_max_committed_revision
            FROM source_acquisition_attempt_compatibility_boundaries
            WHERE workspace_id=?
            """,
            (self.workspace_id,),
        ).fetchall()
        if (
            len(rows) != 1
            or rows[0]["boundary_id"] != _SOURCE_ATTEMPT_COMPATIBILITY_BOUNDARY_ID
            or type(rows[0]["legacy_receipt_max_committed_revision"]) is not int
            or rows[0]["legacy_receipt_max_committed_revision"] < 0
        ):
            raise ControlStoreIntegrityError("control_store_integrity_invalid")
        return int(rows[0]["legacy_receipt_max_committed_revision"])

    def _legacy_post_final_abandonment_receipt_cutoff(self) -> int:
        rows = self._connection.execute(
            """
            SELECT boundary_id,legacy_receipt_max_committed_revision
            FROM post_final_assessment_abandonment_compatibility_boundaries
            WHERE workspace_id=?
            """,
            (self.workspace_id,),
        ).fetchall()
        if (
            len(rows) != 1
            or rows[0]["boundary_id"]
            != _POST_FINAL_ABANDONMENT_COMPATIBILITY_BOUNDARY_ID
            or type(rows[0]["legacy_receipt_max_committed_revision"]) is not int
            or rows[0]["legacy_receipt_max_committed_revision"] < 0
        ):
            raise ControlStoreIntegrityError("control_store_integrity_invalid")
        return int(rows[0]["legacy_receipt_max_committed_revision"])

    def _decode_artifact_record_row(self, row: sqlite3.Row) -> ArtifactRecord:
        return self._decode_checked(
            ArtifactRecord,
            row,
            {
                "run_id": "run_id",
                "artifact_id": "artifact_id",
                "schema_version": "schema_version",
                "current_revision": "current_revision",
                "status": "status",
                "required": "required",
                "path": "path",
                "format": "format",
            },
        )

    def _decode_artifact_identity_row(
        self,
        row: sqlite3.Row,
    ) -> ArtifactIdentityRecord:
        return self._decode_checked(
            ArtifactIdentityRecord,
            row,
            {
                "run_id": "run_id",
                "artifact_id": "artifact_id",
                "schema_version": "schema_version",
                "required": "required",
                "initial_path": "initial_path",
                "format": "format",
                "accepted_transaction_id": "accepted_transaction_id",
            },
        )

    def _decode_source_row(self, row: sqlite3.Row) -> AcceptedSourceRecord:
        return self._decode_checked(
            AcceptedSourceRecord,
            row,
            {
                "run_id": "run_id",
                "source_id": "source_id",
                "schema_version": "schema_version",
                "origin_type": "origin_type",
                "acquisition_method": "acquisition_method",
                "material_kind": "material_kind",
                "provider": "provider",
                "title": "title",
                "publisher": "publisher",
                "published_at": "published_at",
                "retrieved_at": "retrieved_at",
                "source_category": "source_category",
                "retrieval_source_type": "retrieval_source_type",
                "underlying_evidence_type": "underlying_evidence_type",
                "raw_underlying_evidence_type": "raw_underlying_evidence_type",
                "content_sha256": "content_sha256",
                "content_size_bytes": "content_size_bytes",
                "content_media_type": "content_media_type",
                "content_blob_path": "content_blob_path",
                "content_artifact_id": "content_artifact_id",
                "content_artifact_revision": "content_artifact_revision",
                "raw_payload_sha256": "raw_payload_sha256",
                "raw_payload_size_bytes": "raw_payload_size_bytes",
                "raw_payload_media_type": "raw_payload_media_type",
                "raw_payload_blob_path": "raw_payload_blob_path",
                "raw_payload_artifact_id": "raw_payload_artifact_id",
                "raw_payload_artifact_revision": "raw_payload_artifact_revision",
                "claims_eligible": "claims_eligible",
                "eligibility_reason": "eligibility_reason",
                "invocation_id": "invocation_id",
                "acquisition_event_id": "acquisition_event_id",
                "accepted_transaction_id": "accepted_transaction_id",
                "request_fingerprint": "request_fingerprint",
                "created_at": "created_at",
            },
        )

    def _decode_proposal_row(self, row: sqlite3.Row) -> AcceptedProposalRecord:
        return self._decode_checked(
            AcceptedProposalRecord,
            row,
            {
                "run_id": "run_id",
                "proposal_id": "proposal_id",
                "schema_version": "schema_version",
                "proposal_kind": "proposal_kind",
                "artifact_id": "artifact_id",
                "artifact_revision": "artifact_revision",
                "proposal_sha256": "proposal_sha256",
                "invocation_id": "invocation_id",
                "owner_stage_id": "owner_stage_id",
                "owner_role_id": "owner_role_id",
                "parent_proposal_id": "parent_proposal_id",
                "target_artifact_id": "target_artifact_id",
                "target_artifact_revision": "target_artifact_revision",
                "accepted_event_id": "accepted_event_id",
                "accepted_transaction_id": "accepted_transaction_id",
                "request_fingerprint": "request_fingerprint",
                "created_at": "created_at",
            },
        )

    def _load_transactions(self, run_id: str) -> tuple[TransactionReceipt, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM transactions
            WHERE run_id = ? ORDER BY committed_revision, transaction_id
            """,
            (run_id,),
        ).fetchall()
        receipts: list[TransactionReceipt] = []
        for row in rows:
            receipt = self._decode_transaction_row(row)
            self._verify_transaction_relations(receipt)
            receipts.append(receipt)
        return tuple(receipts)

    def _decode_transaction_row(self, row: sqlite3.Row) -> TransactionReceipt:
        return self._decode_checked(
            TransactionReceipt,
            row,
            {
                "run_id": "run_id",
                "transaction_id": "transaction_id",
                "schema_version": "schema_version",
                "transaction_type": "transaction_type",
                "prior_revision": "prior_revision",
                "committed_revision": "committed_revision",
                "committed_at": "committed_at",
                "projection_status": "projection_status",
            },
        )

    def _transaction_relation_values(
        self,
        receipt: TransactionReceipt,
    ) -> tuple[
        tuple[str, ...],
        tuple[ArtifactRevisionReference, ...],
        tuple[ArtifactIdentityReference, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        event_rows = self._connection.execute(
            """
            SELECT position, event_id FROM transaction_events
            WHERE run_id = ? AND transaction_id = ? ORDER BY position
            """,
            (receipt.run_id, receipt.transaction_id),
        ).fetchall()
        revision_rows = self._connection.execute(
            """
            SELECT position, artifact_id, revision
            FROM transaction_artifact_revisions
            WHERE run_id = ? AND transaction_id = ? ORDER BY position
            """,
            (receipt.run_id, receipt.transaction_id),
        ).fetchall()
        identity_rows = self._connection.execute(
            """
            SELECT position, artifact_id
            FROM transaction_artifact_identities
            WHERE run_id = ? AND transaction_id = ? ORDER BY position
            """,
            (receipt.run_id, receipt.transaction_id),
        ).fetchall()
        source_rows = self._connection.execute(
            """
            SELECT position, source_id FROM transaction_sources
            WHERE run_id = ? AND transaction_id = ? ORDER BY position
            """,
            (receipt.run_id, receipt.transaction_id),
        ).fetchall()
        proposal_rows = self._connection.execute(
            """
            SELECT position, proposal_id FROM transaction_proposals
            WHERE run_id = ? AND transaction_id = ? ORDER BY position
            """,
            (receipt.run_id, receipt.transaction_id),
        ).fetchall()
        if (
            [row[0] for row in event_rows] != list(range(len(event_rows)))
            or [row[0] for row in revision_rows] != list(range(len(revision_rows)))
            or [row[0] for row in identity_rows] != list(range(len(identity_rows)))
            or [row[0] for row in source_rows] != list(range(len(source_rows)))
            or [row[0] for row in proposal_rows] != list(range(len(proposal_rows)))
        ):
            raise ControlStoreIntegrityError("transaction_relation_mismatch")
        event_ids = tuple(str(row[1]) for row in event_rows)
        try:
            revision_refs = tuple(
                ArtifactRevisionReference.model_validate(
                    {"artifact_id": row[1], "revision": row[2]}
                )
                for row in revision_rows
            )
            identity_refs = tuple(
                ArtifactIdentityReference.model_validate(
                    {"artifact_id": row[1]},
                    strict=True,
                )
                for row in identity_rows
            )
        except ValidationError as exc:
            raise ControlStoreIntegrityError("transaction_relation_mismatch") from exc
        source_ids = tuple(str(row[1]) for row in source_rows)
        proposal_ids = tuple(str(row[1]) for row in proposal_rows)
        return event_ids, revision_refs, identity_refs, source_ids, proposal_ids

    def _verify_transaction_relations(self, receipt: TransactionReceipt) -> None:
        event_ids, revision_refs, identity_refs, source_ids, proposal_ids = (
            self._transaction_relation_values(receipt)
        )
        if (
            list(event_ids) != receipt.event_ids
            or list(revision_refs) != receipt.artifact_revisions
            or list(identity_refs) != receipt.artifact_identities
            or list(source_ids) != receipt.source_ids
            or list(proposal_ids) != receipt.proposal_ids
        ):
            raise ControlStoreIntegrityError("transaction_relation_mismatch")
        self._verify_core_transaction_relations(receipt)

    def _verify_core_transaction_relations(
        self,
        receipt: TransactionReceipt,
    ) -> None:
        specs: tuple[
            tuple[str, tuple[str, ...], tuple[tuple[object, ...], ...]], ...
        ] = (
            (
                "transaction_run_contract_bindings",
                ("binding_run_id",),
                tuple((item.run_id,) for item in receipt.run_contract_bindings),
            ),
            (
                "transaction_run_execution_authorizations",
                ("authorization_id",),
                tuple(
                    (item.authorization_id,)
                    for item in receipt.run_execution_authorizations
                ),
            ),
            (
                "transaction_run_source_discovery_authorizations",
                ("authorization_id",),
                tuple(
                    (item.authorization_id,)
                    for item in receipt.run_source_discovery_authorizations
                ),
            ),
            (
                "transaction_run_source_acquisition_attempt_authorizations_v2",
                ("attempt_authorization_id",),
                tuple(
                    (item.attempt_authorization_id,)
                    for item in (receipt.run_source_acquisition_attempt_authorizations)
                ),
            ),
            (
                "transaction_owned_artifact_submissions",
                ("submission_id",),
                tuple(
                    (item.submission_id,) for item in receipt.owned_artifact_submissions
                ),
            ),
            (
                "transaction_stage_transitions",
                ("transition_id",),
                tuple((item.transition_id,) for item in receipt.stage_transitions),
            ),
            (
                "transaction_stage_artifact_bindings",
                ("transition_id", "binding_position"),
                tuple(
                    (item.transition_id, item.position)
                    for item in receipt.stage_artifact_bindings
                ),
            ),
            (
                "transaction_stage_gate_bindings",
                ("transition_id", "gate_id"),
                tuple(
                    (item.transition_id, item.gate_id)
                    for item in receipt.stage_gate_bindings
                ),
            ),
            (
                "transaction_claims",
                ("claim_id",),
                tuple((item.claim_id,) for item in receipt.claims),
            ),
            (
                "transaction_claim_source_bindings",
                ("claim_id", "source_id"),
                tuple(
                    (item.claim_id, item.source_id)
                    for item in receipt.claim_source_bindings
                ),
            ),
            (
                "transaction_claim_freezes",
                ("freeze_id",),
                tuple((item.freeze_id,) for item in receipt.claim_freezes),
            ),
            (
                "transaction_gate_evaluations",
                ("evaluation_id",),
                tuple((item.evaluation_id,) for item in receipt.gate_evaluations),
            ),
            (
                "transaction_gate_findings",
                ("evaluation_id", "finding_id"),
                tuple(
                    (item.evaluation_id, item.finding_id)
                    for item in receipt.gate_findings
                ),
            ),
            (
                "transaction_gate_artifact_bindings",
                ("evaluation_id", "binding_position"),
                tuple(
                    (item.evaluation_id, item.position)
                    for item in receipt.gate_artifact_bindings
                ),
            ),
            (
                "transaction_run_integrity_records",
                ("integrity_revision",),
                tuple(
                    (item.integrity_revision,) for item in receipt.run_integrity_records
                ),
            ),
            (
                "transaction_repair_cycles",
                ("repair_id",),
                tuple((item.repair_id,) for item in receipt.repair_cycles),
            ),
            (
                "transaction_gate_repair_cycles",
                ("gate_repair_id",),
                tuple((item.gate_repair_id,) for item in receipt.gate_repair_cycles),
            ),
            (
                "transaction_gate_repair_artifact_bindings",
                ("gate_repair_id",),
                tuple(
                    (item.gate_repair_id,)
                    for item in receipt.gate_repair_artifact_bindings
                ),
            ),
            (
                "transaction_gate_repair_outcomes",
                ("outcome_id",),
                tuple((item.outcome_id,) for item in receipt.gate_repair_outcomes),
            ),
            (
                "transaction_artifact_supersessions",
                ("supersession_id",),
                tuple(
                    (item.supersession_id,) for item in receipt.artifact_supersessions
                ),
            ),
            (
                "transaction_repair_completions",
                ("repair_completion_id",),
                tuple(
                    (item.repair_completion_id,) for item in receipt.repair_completions
                ),
            ),
            (
                "transaction_recovery_completions",
                ("recovery_id",),
                tuple((item.recovery_id,) for item in receipt.recovery_completions),
            ),
            (
                "transaction_run_head_transitions",
                ("head_transition_id",),
                tuple(
                    (item.head_transition_id,) for item in receipt.run_head_transitions
                ),
            ),
            (
                "transaction_finalize_renders",
                ("render_id",),
                tuple((item.render_id,) for item in receipt.finalize_renders),
            ),
            (
                "transaction_finalizations",
                ("finalization_id",),
                tuple((item.finalization_id,) for item in receipt.finalizations),
            ),
            (
                "transaction_run_archives",
                ("archive_id",),
                tuple((item.archive_id,) for item in receipt.run_archives),
            ),
            (
                "transaction_run_archive_artifact_bindings",
                ("archive_id", "binding_position"),
                tuple(
                    (item.archive_id, item.position)
                    for item in receipt.run_archive_artifact_bindings
                ),
            ),
            (
                "transaction_package_ready_records",
                ("package_id",),
                tuple((item.package_id,) for item in receipt.package_ready_records),
            ),
            (
                "transaction_package_artifact_bindings",
                ("package_id", "binding_position"),
                tuple(
                    (item.package_id, item.position)
                    for item in receipt.package_artifact_bindings
                ),
            ),
            (
                "transaction_approvals",
                ("approval_id",),
                tuple((item.approval_id,) for item in receipt.approvals),
            ),
            (
                "transaction_approval_package_bindings",
                ("approval_id", "package_id"),
                tuple(
                    (item.approval_id, item.package_id)
                    for item in receipt.approval_package_bindings
                ),
            ),
            (
                "transaction_delivery_authorizations",
                ("authorization_id",),
                tuple(
                    (item.authorization_id,) for item in receipt.delivery_authorizations
                ),
            ),
            (
                "transaction_delivery_attempts",
                ("attempt_id",),
                tuple((item.attempt_id,) for item in receipt.delivery_attempts),
            ),
            (
                "transaction_delivery_results",
                ("result_id",),
                tuple((item.result_id,) for item in receipt.delivery_results),
            ),
            (
                "transaction_post_final_assessment_policy_revisions",
                ("policy_revision_id",),
                tuple(
                    (item.policy_revision_id,)
                    for item in receipt.post_final_assessment_policy_revisions
                ),
            ),
            (
                "transaction_post_final_assessment_requests",
                ("assessment_request_id",),
                tuple(
                    (item.assessment_request_id,)
                    for item in receipt.post_final_assessment_requests
                ),
            ),
            (
                "transaction_post_final_assessment_abandonments",
                ("abandonment_id",),
                tuple(
                    (item.abandonment_id,)
                    for item in receipt.post_final_assessment_abandonments
                ),
            ),
            (
                "transaction_post_final_assessment_results",
                ("assessment_result_id",),
                tuple(
                    (item.assessment_result_id,)
                    for item in receipt.post_final_assessment_results
                ),
            ),
            (
                "transaction_post_final_finding_dispositions",
                ("disposition_id",),
                tuple(
                    (item.disposition_id,)
                    for item in receipt.post_final_finding_dispositions
                ),
            ),
            (
                "transaction_post_final_human_observations",
                ("observation_id",),
                tuple(
                    (item.observation_id,)
                    for item in receipt.post_final_human_observations
                ),
            ),
            (
                "transaction_post_final_guidance_drafts",
                ("guidance_id", "draft_revision"),
                tuple(
                    (item.guidance_id, item.draft_revision)
                    for item in receipt.post_final_guidance_drafts
                ),
            ),
            (
                "transaction_post_final_guidance_statuses",
                ("status_revision_id",),
                tuple(
                    (item.status_revision_id,)
                    for item in receipt.post_final_guidance_statuses
                ),
            ),
            (
                "transaction_run_guidance_snapshots",
                ("snapshot_id",),
                tuple((item.snapshot_id,) for item in receipt.run_guidance_snapshots),
            ),
            (
                "transaction_run_guidance_selection_decisions",
                ("decision_id",),
                tuple(
                    (item.decision_id,)
                    for item in receipt.run_guidance_selection_decisions
                ),
            ),
            (
                "transaction_run_guidance_snapshot_items",
                ("item_id",),
                tuple((item.item_id,) for item in receipt.run_guidance_snapshot_items),
            ),
            (
                "transaction_checkout_revisions",
                ("checkout_revision_id",),
                tuple(
                    (item.checkout_revision_id,) for item in receipt.checkout_revisions
                ),
            ),
            (
                "transaction_receipt_checkout_bindings",
                ("binding_transaction_id",),
                tuple(
                    (item.transaction_id,) for item in receipt.receipt_checkout_bindings
                ),
            ),
            (
                "transaction_checkout_publication_intents",
                ("checkout_revision_id",),
                tuple(
                    (item.checkout_revision_id,)
                    for item in receipt.checkout_publication_intents
                ),
            ),
        )
        for table, columns, expected in specs:
            selected = ", ".join(("position", *columns))
            rows = self._connection.execute(
                f"SELECT {selected} FROM {table} "
                "WHERE run_id = ? AND transaction_id = ? ORDER BY position",
                (receipt.run_id, receipt.transaction_id),
            ).fetchall()
            if [row[0] for row in rows] != list(range(len(rows))):
                raise ControlStoreIntegrityError("transaction_relation_mismatch")
            actual = tuple(
                tuple(row[index + 1] for index in range(len(columns))) for row in rows
            )
            if actual != expected:
                raise ControlStoreIntegrityError("transaction_relation_mismatch")

    def _ledger_verification_token(self) -> tuple[int, int, int]:
        """Token that changes whenever this database could have changed.

        ``total_changes`` counts every row this connection wrote. It never
        decreases, so a rolled-back write still moves the token and forces a
        re-verification rather than hiding one. ``data_version`` moves when any
        other connection commits. Together they cover every way the bytes under
        us can differ from the ones already verified.
        """

        data_version = self._connection.execute("PRAGMA data_version").fetchone()[0]
        return (
            self._workspace_revision_in_transaction(),
            self._connection.total_changes,
            int(data_version),
        )

    def _verify_workspace_ledger_graph(self) -> None:
        """Verify one complete workspace transaction graph in this SQL snapshot."""

        def invalid() -> None:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

        # Every read re-verified the whole workspace graph: all transactions,
        # events, revisions, identities, sources and proposals, decoded and
        # revalidated. That made one read O(run history) and a run that drives
        # N steps O(N^2). The graph is a pure function of the bytes, so an
        # unchanged token means the previous verification still holds.
        token = self._ledger_verification_token()
        if token == self._verified_ledger_token:
            return

        workspace_revision = token[0]
        transaction_rows = self._connection.execute(
            """
            SELECT * FROM transactions
            ORDER BY committed_revision, run_id, transaction_id
            """
        ).fetchall()
        if len(transaction_rows) != workspace_revision:
            invalid()

        event_owners: dict[tuple[str, str], str] = {}
        revision_owners: dict[tuple[str, str, int], str] = {}
        identity_owners: dict[tuple[str, str], str] = {}
        source_owners: dict[tuple[str, str], str] = {}
        proposal_owners: dict[tuple[str, str], str] = {}
        for expected_revision, row in enumerate(transaction_rows, start=1):
            receipt = self._decode_transaction_row(row)
            if (
                row["workspace_id"] != self.workspace_id
                or receipt.prior_revision != expected_revision - 1
                or receipt.committed_revision != expected_revision
            ):
                invalid()
            event_ids, revision_refs, identity_refs, source_ids, proposal_ids = (
                self._transaction_relation_values(receipt)
            )
            if (
                list(event_ids) != receipt.event_ids
                or list(revision_refs) != receipt.artifact_revisions
                or list(identity_refs) != receipt.artifact_identities
                or list(source_ids) != receipt.source_ids
                or list(proposal_ids) != receipt.proposal_ids
            ):
                raise ControlStoreIntegrityError("transaction_relation_mismatch")
            if [item.artifact_id for item in identity_refs] != sorted(
                item.artifact_id for item in identity_refs
            ):
                invalid()
            self._verify_core_transaction_relations(receipt)
            for event_id in event_ids:
                key = (receipt.run_id, event_id)
                if key in event_owners:
                    invalid()
                event_owners[key] = receipt.transaction_id
            for reference in revision_refs:
                key = (receipt.run_id, reference.artifact_id, reference.revision)
                if key in revision_owners:
                    invalid()
                revision_owners[key] = receipt.transaction_id
            for reference in identity_refs:
                key = (receipt.run_id, reference.artifact_id)
                if key in identity_owners:
                    invalid()
                identity_owners[key] = receipt.transaction_id
            for source_id in source_ids:
                key = (receipt.run_id, source_id)
                if key in source_owners:
                    invalid()
                source_owners[key] = receipt.transaction_id
            for proposal_id in proposal_ids:
                key = (receipt.run_id, proposal_id)
                if key in proposal_owners:
                    invalid()
                proposal_owners[key] = receipt.transaction_id

        event_keys: set[tuple[str, str]] = set()
        for row in self._connection.execute(
            "SELECT * FROM events ORDER BY run_id, event_id"
        ).fetchall():
            event = self._decode_checked(
                EventEnvelope,
                row,
                {
                    "run_id": "run_id",
                    "event_id": "event_id",
                    "schema_version": "schema_version",
                    "event_type": "event_type",
                    "created_at": "created_at",
                    "actor": "actor",
                    "transaction_id": "transaction_id",
                    "stage_id": "stage_id",
                    "artifact_id": "artifact_id",
                    "decision": "decision",
                    "reason": "reason",
                },
            )
            key = (event.run_id, event.event_id)
            owner = event_owners.get(key)
            if owner is None or key in event_keys:
                invalid()
            if event.transaction_id is not None and event.transaction_id != owner:
                invalid()
            event_keys.add(key)
        if event_keys != set(event_owners):
            invalid()

        revision_keys: set[tuple[str, str, int]] = set()
        revisions_by_artifact: dict[tuple[str, str], list[ArtifactRevision]] = {}
        for row in self._connection.execute(
            """
            SELECT * FROM artifact_revisions
            ORDER BY run_id, artifact_id, revision
            """
        ).fetchall():
            revision = self._decode_checked(
                ArtifactRevision,
                row,
                {
                    "run_id": "run_id",
                    "artifact_id": "artifact_id",
                    "revision": "revision",
                    "schema_version": "schema_version",
                    "path": "path",
                    "sha256": "sha256",
                    "size_bytes": "size_bytes",
                    "frozen": "frozen",
                    "producer_kind": "producer_kind",
                    "producer_id": "producer_id",
                    "created_at": "created_at",
                },
            )
            key = (revision.run_id, revision.artifact_id, revision.revision)
            if key not in revision_owners or key in revision_keys:
                invalid()
            revision_keys.add(key)
            revisions_by_artifact.setdefault(
                (revision.run_id, revision.artifact_id), []
            ).append(revision)
        if revision_keys != set(revision_owners):
            invalid()

        identity_keys: set[tuple[str, str]] = set()
        identities: dict[tuple[str, str], ArtifactIdentityRecord] = {}
        for row in self._connection.execute(
            "SELECT * FROM artifact_identities ORDER BY run_id, artifact_id"
        ).fetchall():
            try:
                identity = self._decode_artifact_identity_row(row)
            except ControlStoreIntegrityError:
                invalid()
            key = (identity.run_id, identity.artifact_id)
            owner = identity_owners.get(key)
            if (
                owner is None
                or owner != identity.accepted_transaction_id
                or key in identity_keys
            ):
                invalid()
            identity_keys.add(key)
            identities[key] = identity
        if identity_keys != set(identity_owners):
            invalid()

        artifact_keys: set[tuple[str, str]] = set()
        for row in self._connection.execute(
            "SELECT * FROM artifacts ORDER BY run_id, artifact_id"
        ).fetchall():
            try:
                artifact = self._decode_artifact_record_row(row)
            except ControlStoreIntegrityError:
                invalid()
            key = (artifact.run_id, artifact.artifact_id)
            identity = identities.get(key)
            if identity is None or key in artifact_keys:
                invalid()
            revisions = sorted(
                revisions_by_artifact.get(key, []),
                key=lambda item: item.revision,
            )
            if [item.revision for item in revisions] != list(
                range(1, len(revisions) + 1)
            ):
                invalid()
            if (
                artifact.required != identity.required
                or artifact.format != identity.format
            ):
                invalid()
            if not revisions:
                if (
                    artifact.current_revision != 0
                    or artifact.status != "expected"
                    or artifact.path != identity.initial_path
                ):
                    invalid()
            else:
                latest = revisions[-1]
                if (
                    artifact.current_revision != latest.revision
                    or artifact.status != "valid"
                    or artifact.path != latest.path
                ):
                    invalid()
            artifact_keys.add(key)
        if artifact_keys != identity_keys:
            invalid()
        if set(revisions_by_artifact) - identity_keys:
            invalid()

        source_keys: set[tuple[str, str]] = set()
        for row in self._connection.execute(
            "SELECT * FROM sources ORDER BY run_id, source_id"
        ).fetchall():
            source = self._decode_source_row(row)
            key = (source.run_id, source.source_id)
            owner = source_owners.get(key)
            if (
                owner is None
                or owner != source.accepted_transaction_id
                or key in source_keys
            ):
                invalid()
            self._verify_source_graph_record(source)
            source_keys.add(key)
        if source_keys != set(source_owners):
            invalid()

        proposal_keys: set[tuple[str, str]] = set()
        proposal_source_ids: dict[tuple[str, str], set[str]] = {}
        for row in self._connection.execute(
            "SELECT * FROM accepted_proposals ORDER BY run_id, proposal_id"
        ).fetchall():
            proposal = self._decode_proposal_row(row)
            key = (proposal.run_id, proposal.proposal_id)
            owner = proposal_owners.get(key)
            if (
                owner is None
                or owner != proposal.accepted_transaction_id
                or key in proposal_keys
            ):
                invalid()
            self._verify_proposal_graph_record(proposal)
            proposal_keys.add(key)
            proposal_source_ids[key] = set(proposal.source_ids)
        if proposal_keys != set(proposal_owners):
            invalid()

        binding_source_ids: dict[tuple[str, str], set[str]] = {}
        for row in self._connection.execute(
            """
            SELECT * FROM proposal_source_bindings
            ORDER BY run_id, proposal_id, source_id
            """
        ).fetchall():
            binding = self._decode_checked(
                ProposalSourceBinding,
                row,
                {
                    "run_id": "run_id",
                    "proposal_id": "proposal_id",
                    "source_id": "source_id",
                    "schema_version": "schema_version",
                },
            )
            key = (binding.run_id, binding.proposal_id)
            binding_source_ids.setdefault(key, set()).add(binding.source_id)
        for key, expected in proposal_source_ids.items():
            if binding_source_ids.get(key, set()) != expected:
                invalid()
        if set(binding_source_ids) - set(proposal_source_ids):
            invalid()
        self._verify_run_head_transition_chain()
        self._verify_core_relation_coverage()
        self._verify_pr4b_relation_coverage()
        self._verify_guidance_snapshot_relation_coverage()
        # Only reached when every check above passed; each one raises instead of
        # returning, so a failed verification never records a token.
        self._verified_ledger_token = token

    def _verify_run_head_transition_chain(self) -> None:
        """Verify that run-successor transitions form one chain ending at head."""

        run_ids = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT run_id FROM runs WHERE workspace_id=?",
                (self.workspace_id,),
            ).fetchall()
        }
        head = self._load_workspace_run_head_in_transaction()
        if not run_ids:
            if head is not None:
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
            return
        rows = self._connection.execute(
            "SELECT * FROM run_head_transitions WHERE workspace_id=? "
            "ORDER BY successor_workspace_revision, head_transition_id",
            (self.workspace_id,),
        ).fetchall()
        if head is None:
            if rows:
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
            return
        if head.current_run_id not in run_ids:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        if not rows:
            if len(run_ids) != 1 or head.current_run_id not in run_ids:
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
            return
        transitions = [
            self._decode_checked(
                RunHeadTransitionRecord,
                row,
                {
                    "workspace_id": "workspace_id",
                    "head_transition_id": "head_transition_id",
                    "successor_run_id": "successor_run_id",
                    "predecessor_run_id": "predecessor_run_id",
                    "schema_version": "schema_version",
                    "prior_workspace_revision": "prior_workspace_revision",
                    "successor_workspace_revision": "successor_workspace_revision",
                    "reason_code": "reason_code",
                    "successor_disposition": "successor_disposition",
                    "created_at": "created_at",
                    "transition_event_id": "transition_event_id",
                    "accepted_transaction_id": "accepted_transaction_id",
                    "request_fingerprint": "request_fingerprint",
                },
            )
            for row in rows
        ]
        initial = transitions[0].predecessor_run_id
        if (
            initial is None
            or initial not in run_ids
            or len(transitions) + 1 != len(run_ids)
        ):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        seen = {initial}
        current = initial
        for transition in transitions:
            transaction = self._connection.execute(
                "SELECT run_id,prior_revision,committed_revision FROM transactions "
                "WHERE run_id=? AND transaction_id=?",
                (transition.successor_run_id, transition.accepted_transaction_id),
            ).fetchone()
            if (
                transition.predecessor_run_id != current
                or transition.successor_run_id in seen
                or transition.successor_run_id not in run_ids
                or transition.successor_workspace_revision
                != transition.prior_workspace_revision + 1
                or transaction is None
                or str(transaction[0]) != transition.successor_run_id
                or int(transaction[1]) != transition.prior_workspace_revision
                or int(transaction[2]) != transition.successor_workspace_revision
            ):
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
            seen.add(transition.successor_run_id)
            current = transition.successor_run_id
        if seen != run_ids or current != head.current_run_id:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

    def _verify_core_relation_coverage(self) -> None:
        """Prove every PR-4A append-only row has exactly one receipt owner."""

        specs = (
            (
                "transaction_run_contract_bindings",
                ("binding_run_id",),
                "run_contract_bindings",
                ("run_id",),
                False,
            ),
            (
                "transaction_run_execution_authorizations",
                ("authorization_id",),
                "run_execution_authorizations",
                ("authorization_id",),
                True,
            ),
            (
                "transaction_owned_artifact_submissions",
                ("submission_id",),
                "owned_artifact_submissions",
                ("submission_id",),
                True,
            ),
            (
                "transaction_stage_transitions",
                ("transition_id",),
                "stage_transitions",
                ("transition_id",),
                True,
            ),
            (
                "transaction_stage_artifact_bindings",
                ("transition_id", "binding_position"),
                "stage_artifact_bindings",
                ("transition_id", "position"),
                True,
            ),
            (
                "transaction_stage_gate_bindings",
                ("transition_id", "gate_id"),
                "stage_gate_bindings",
                ("transition_id", "gate_id"),
                True,
            ),
            (
                "transaction_claims",
                ("claim_id",),
                "claims",
                ("claim_id",),
                True,
            ),
            (
                "transaction_claim_source_bindings",
                ("claim_id", "source_id"),
                "claim_source_bindings",
                ("claim_id", "source_id"),
                True,
            ),
            (
                "transaction_claim_freezes",
                ("freeze_id",),
                "claim_freezes",
                ("freeze_id",),
                True,
            ),
            (
                "transaction_gate_evaluations",
                ("evaluation_id",),
                "gate_evaluations",
                ("evaluation_id",),
                True,
            ),
            (
                "transaction_gate_findings",
                ("evaluation_id", "finding_id"),
                "gate_findings",
                ("evaluation_id", "finding_id"),
                True,
            ),
            (
                "transaction_gate_artifact_bindings",
                ("evaluation_id", "binding_position"),
                "gate_artifact_bindings",
                ("evaluation_id", "position"),
                True,
            ),
            (
                "transaction_run_integrity_records",
                ("integrity_revision",),
                "run_integrity_records",
                ("integrity_revision",),
                True,
            ),
        )
        for relation_table, relation_ids, domain_table, domain_ids, with_run in specs:
            relation_columns = ", ".join(("run_id", "transaction_id", *relation_ids))
            relation_rows = self._connection.execute(
                f"SELECT {relation_columns} FROM {relation_table}"
            ).fetchall()
            owners: dict[tuple[object, ...], str] = {}
            for row in relation_rows:
                identity = tuple(row[index + 2] for index in range(len(relation_ids)))
                key = ((row[0],) + identity) if with_run else identity
                if not with_run and row[0] != identity[0]:
                    raise ControlStoreIntegrityError(
                        "transaction_ledger_integrity_invalid"
                    )
                if key in owners:
                    raise ControlStoreIntegrityError(
                        "transaction_ledger_integrity_invalid"
                    )
                owners[key] = str(row[1])

            domain_columns = ", ".join(
                ("run_id", *domain_ids, "accepted_transaction_id")
            )
            domain_rows = self._connection.execute(
                f"SELECT {domain_columns} FROM {domain_table}"
            ).fetchall()
            domain_keys: set[tuple[object, ...]] = set()
            for row in domain_rows:
                identity = tuple(row[index + 1] for index in range(len(domain_ids)))
                key = ((row[0],) + identity) if with_run else identity
                accepted_transaction_id = str(row[len(domain_ids) + 1])
                if key in domain_keys or owners.get(key) != accepted_transaction_id:
                    raise ControlStoreIntegrityError(
                        "transaction_ledger_integrity_invalid"
                    )
                domain_keys.add(key)
            if domain_keys != set(owners):
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        discovery_rows = self._connection.execute(
            "SELECT run_id,authorization_id,accepted_transaction_id "
            "FROM run_source_discovery_authorizations"
        ).fetchall()
        for run_id, authorization_id, accepted_transaction_id in discovery_rows:
            owner = self._connection.execute(
                "SELECT 1 FROM transaction_run_source_discovery_authorizations "
                "WHERE run_id=? AND transaction_id=? AND authorization_id=?",
                (run_id, accepted_transaction_id, authorization_id),
            ).fetchone()
            if owner is None:
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        attempt_rows = self._connection.execute(
            "SELECT run_id,attempt_authorization_id,accepted_transaction_id "
            "FROM run_source_acquisition_attempt_authorizations_v2"
        ).fetchall()
        for run_id, attempt_id, accepted_transaction_id in attempt_rows:
            owner = self._connection.execute(
                "SELECT 1 "
                "FROM transaction_run_source_acquisition_attempt_authorizations_v2 "
                "WHERE run_id=? AND transaction_id=? "
                "AND attempt_authorization_id=?",
                (run_id, accepted_transaction_id, attempt_id),
            ).fetchone()
            if owner is None:
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

    def _verify_pr4b_relation_coverage(self) -> None:
        """Prove every PR-4B authoritative row has one receipt owner."""

        specs = (
            (
                "transaction_repair_cycles",
                ("repair_id",),
                "repair_cycles",
                "run_id",
                ("repair_id",),
            ),
            (
                "transaction_gate_repair_cycles",
                ("gate_repair_id",),
                "gate_repair_cycles",
                "run_id",
                ("gate_repair_id",),
            ),
            (
                "transaction_gate_repair_artifact_bindings",
                ("gate_repair_id",),
                "gate_repair_artifact_bindings",
                "run_id",
                ("gate_repair_id",),
            ),
            (
                "transaction_gate_repair_outcomes",
                ("outcome_id",),
                "gate_repair_outcomes",
                "run_id",
                ("outcome_id",),
            ),
            (
                "transaction_artifact_supersessions",
                ("supersession_id",),
                "artifact_supersessions",
                "run_id",
                ("supersession_id",),
            ),
            (
                "transaction_repair_completions",
                ("repair_completion_id",),
                "repair_completions",
                "run_id",
                ("repair_completion_id",),
            ),
            (
                "transaction_recovery_completions",
                ("recovery_id",),
                "recovery_completions",
                "run_id",
                ("recovery_id",),
            ),
            (
                "transaction_run_head_transitions",
                ("head_transition_id",),
                "run_head_transitions",
                "successor_run_id",
                ("head_transition_id",),
            ),
            (
                "transaction_finalize_renders",
                ("render_id",),
                "finalize_renders",
                "run_id",
                ("render_id",),
            ),
            (
                "transaction_finalizations",
                ("finalization_id",),
                "finalizations",
                "run_id",
                ("finalization_id",),
            ),
            (
                "transaction_run_archives",
                ("archive_id",),
                "run_archives",
                "run_id",
                ("archive_id",),
            ),
            (
                "transaction_run_archive_artifact_bindings",
                ("archive_id", "binding_position"),
                "run_archive_artifact_bindings",
                "run_id",
                ("archive_id", "position"),
            ),
            (
                "transaction_package_ready_records",
                ("package_id",),
                "package_ready_records",
                "run_id",
                ("package_id",),
            ),
            (
                "transaction_package_artifact_bindings",
                ("package_id", "binding_position"),
                "package_artifact_bindings",
                "run_id",
                ("package_id", "position"),
            ),
            (
                "transaction_approval_package_bindings",
                ("approval_id", "package_id"),
                "approval_package_bindings",
                "run_id",
                ("approval_id", "package_id"),
            ),
            (
                "transaction_delivery_authorizations",
                ("authorization_id",),
                "delivery_authorizations",
                "run_id",
                ("authorization_id",),
            ),
            (
                "transaction_delivery_attempts",
                ("attempt_id",),
                "delivery_attempts",
                "run_id",
                ("attempt_id",),
            ),
            (
                "transaction_delivery_results",
                ("result_id",),
                "delivery_results",
                "run_id",
                ("result_id",),
            ),
            (
                "transaction_post_final_assessment_policy_revisions",
                ("policy_revision_id",),
                "post_final_assessment_policy_revisions",
                "run_id",
                ("policy_revision_id",),
            ),
            (
                "transaction_post_final_assessment_requests",
                ("assessment_request_id",),
                "post_final_assessment_requests",
                "run_id",
                ("assessment_request_id",),
            ),
            (
                "transaction_post_final_assessment_abandonments",
                ("abandonment_id",),
                "post_final_assessment_abandonments",
                "run_id",
                ("abandonment_id",),
            ),
            (
                "transaction_post_final_assessment_results",
                ("assessment_result_id",),
                "post_final_assessment_results",
                "run_id",
                ("assessment_result_id",),
            ),
            (
                "transaction_post_final_finding_dispositions",
                ("disposition_id",),
                "post_final_finding_dispositions",
                "run_id",
                ("disposition_id",),
            ),
            (
                "transaction_post_final_guidance_drafts",
                ("guidance_id", "draft_revision"),
                "post_final_guidance_drafts",
                "run_id",
                ("guidance_id", "draft_revision"),
            ),
            (
                "transaction_post_final_guidance_statuses",
                ("status_revision_id",),
                "post_final_guidance_statuses",
                "run_id",
                ("status_revision_id",),
            ),
        )
        for relation_table, relation_ids, domain_table, domain_run, domain_ids in specs:
            relation_columns = ", ".join(("run_id", "transaction_id", *relation_ids))
            owners: dict[tuple[object, ...], str] = {}
            for row in self._connection.execute(
                f"SELECT {relation_columns} FROM {relation_table}"
            ).fetchall():
                key = (row[0], *(row[index + 2] for index in range(len(relation_ids))))
                if key in owners:
                    raise ControlStoreIntegrityError(
                        "transaction_ledger_integrity_invalid"
                    )
                owners[key] = str(row[1])

            domain_columns = ", ".join(
                (domain_run, *domain_ids, "accepted_transaction_id")
            )
            domain_keys: set[tuple[object, ...]] = set()
            for row in self._connection.execute(
                f"SELECT {domain_columns} FROM {domain_table}"
            ).fetchall():
                key = tuple(row[index] for index in range(len(domain_ids) + 1))
                owner = str(row[len(domain_ids) + 1])
                if key in domain_keys or owners.get(key) != owner:
                    raise ControlStoreIntegrityError(
                        "transaction_ledger_integrity_invalid"
                    )
                domain_keys.add(key)
            if domain_keys != set(owners):
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

        approval_relations = self._connection.execute(
            "SELECT run_id,transaction_id,approval_id FROM transaction_approvals"
        ).fetchall()
        approval_owners = {(row[0], row[2]): str(row[1]) for row in approval_relations}
        if len(approval_owners) != len(approval_relations):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        approval_rows = self._connection.execute(
            "SELECT approvals.run_id,approvals.approval_id,events.transaction_id "
            "FROM approvals JOIN events ON events.run_id=approvals.run_id "
            "AND events.event_id=approvals.event_id"
        ).fetchall()
        approval_keys = {(row[0], row[1]) for row in approval_rows}
        if approval_keys != set(approval_owners) or any(
            approval_owners[(row[0], row[1])] != str(row[2]) for row in approval_rows
        ):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

    def _verify_guidance_snapshot_relation_coverage(self) -> None:
        """Prove every guidance snapshot row has its successor Receipt owner."""

        snapshot_relations = self._connection.execute(
            "SELECT run_id,transaction_id,snapshot_id "
            "FROM transaction_run_guidance_snapshots"
        ).fetchall()
        snapshot_owners = {(row[0], row[2]): str(row[1]) for row in snapshot_relations}
        if len(snapshot_owners) != len(snapshot_relations):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

        snapshot_rows = self._connection.execute(
            "SELECT run_id,snapshot_id,accepted_transaction_id "
            "FROM run_guidance_snapshots"
        ).fetchall()
        snapshot_keys = {(row[0], row[1]) for row in snapshot_rows}
        if snapshot_keys != set(snapshot_owners) or any(
            snapshot_owners[(row[0], row[1])] != str(row[2]) for row in snapshot_rows
        ):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

        for relation_table, domain_table, identity_column in (
            (
                "transaction_run_guidance_selection_decisions",
                "run_guidance_selection_decisions",
                "decision_id",
            ),
            (
                "transaction_run_guidance_snapshot_items",
                "run_guidance_snapshot_items",
                "item_id",
            ),
        ):
            relation_rows = self._connection.execute(
                f"SELECT run_id,transaction_id,{identity_column} FROM {relation_table}"
            ).fetchall()
            owners = {(row[0], row[2]): str(row[1]) for row in relation_rows}
            if len(owners) != len(relation_rows):
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
            domain_rows = self._connection.execute(
                f"SELECT run_id,{identity_column},snapshot_id FROM {domain_table}"
            ).fetchall()
            domain_keys = {(row[0], row[1]) for row in domain_rows}
            if domain_keys != set(owners) or any(
                owners[(row[0], row[1])] != snapshot_owners.get((row[0], row[2]))
                for row in domain_rows
            ):
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

    def _verify_source_graph_record(self, source: AcceptedSourceRecord) -> None:
        content_revision = self._artifact_revision_for(
            source.run_id,
            source.content_artifact_id,
            source.content_artifact_revision,
        )
        content_artifact = self._artifact_for(
            source.run_id,
            source.content_artifact_id,
        )
        expected_content_path = self._workspace_blob_path(source.content_sha256)
        if (
            content_revision.sha256 != source.content_sha256
            or content_revision.size_bytes != source.content_size_bytes
            or content_revision.path != source.content_blob_path
            or source.content_blob_path != expected_content_path
            or content_artifact.current_revision != source.content_artifact_revision
            or content_artifact.path != expected_content_path
        ):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        if source.raw_payload_artifact_id is not None:
            raw_revision = self._artifact_revision_for(
                source.run_id,
                source.raw_payload_artifact_id,
                cast(int, source.raw_payload_artifact_revision),
            )
            raw_artifact = self._artifact_for(
                source.run_id,
                source.raw_payload_artifact_id,
            )
            expected_raw_path = self._workspace_blob_path(
                cast(str, source.raw_payload_sha256)
            )
            if (
                raw_revision.sha256 != source.raw_payload_sha256
                or raw_revision.size_bytes != source.raw_payload_size_bytes
                or raw_revision.path != source.raw_payload_blob_path
                or source.raw_payload_blob_path != expected_raw_path
                or raw_artifact.current_revision != source.raw_payload_artifact_revision
                or raw_artifact.path != expected_raw_path
            ):
                raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        event = self._event_for(source.run_id, source.acquisition_event_id)
        binding = event.intake_binding
        if (
            event.event_type != "source_evidence_committed"
            or event.transaction_id != source.accepted_transaction_id
            or event.artifact_id != source.content_artifact_id
            or binding is None
            or binding.outcome != "committed"
            or binding.request_id != source.accepted_transaction_id
            or binding.request_fingerprint != source.request_fingerprint
            or binding.invocation_id != source.invocation_id
            or binding.source_id != source.source_id
            or binding.proposal_id is not None
        ):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

    def _verify_proposal_graph_record(
        self,
        proposal: AcceptedProposalRecord,
    ) -> None:
        revision = self._artifact_revision_for(
            proposal.run_id,
            proposal.artifact_id,
            proposal.artifact_revision,
        )
        self._artifact_for(proposal.run_id, proposal.artifact_id)
        expected_path = self._workspace_blob_path(proposal.proposal_sha256)
        event = self._event_for(proposal.run_id, proposal.accepted_event_id)
        binding = event.intake_binding
        if (
            revision.sha256 != proposal.proposal_sha256
            or revision.path != expected_path
            or event.event_type != "role_proposal_committed"
            or event.transaction_id != proposal.accepted_transaction_id
            or event.artifact_id != proposal.artifact_id
            or binding is None
            or binding.outcome != "committed"
            or binding.request_id != proposal.accepted_transaction_id
            or binding.request_fingerprint != proposal.request_fingerprint
            or binding.invocation_id != proposal.invocation_id
            or binding.proposal_id != proposal.proposal_id
            or binding.source_id is not None
        ):
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

    def _artifact_for(self, run_id: str, artifact_id: str) -> ArtifactRecord:
        row = self._connection.execute(
            "SELECT * FROM artifacts WHERE run_id = ? AND artifact_id = ?",
            (run_id, artifact_id),
        ).fetchone()
        if row is None:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        return self._decode_checked(
            ArtifactRecord,
            row,
            {
                "run_id": "run_id",
                "artifact_id": "artifact_id",
                "schema_version": "schema_version",
                "current_revision": "current_revision",
                "status": "status",
                "required": "required",
                "path": "path",
                "format": "format",
            },
        )

    def _artifact_revision_for(
        self,
        run_id: str,
        artifact_id: str,
        revision: int,
    ) -> ArtifactRevision:
        row = self._connection.execute(
            """
            SELECT * FROM artifact_revisions
            WHERE run_id = ? AND artifact_id = ? AND revision = ?
            """,
            (run_id, artifact_id, revision),
        ).fetchone()
        if row is None:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        return self._decode_checked(
            ArtifactRevision,
            row,
            {
                "run_id": "run_id",
                "artifact_id": "artifact_id",
                "revision": "revision",
                "schema_version": "schema_version",
                "path": "path",
                "sha256": "sha256",
                "size_bytes": "size_bytes",
                "frozen": "frozen",
                "producer_kind": "producer_kind",
                "producer_id": "producer_id",
                "created_at": "created_at",
            },
        )

    def _event_for(self, run_id: str, event_id: str) -> EventEnvelope:
        row = self._connection.execute(
            "SELECT * FROM events WHERE run_id = ? AND event_id = ?",
            (run_id, event_id),
        ).fetchone()
        if row is None:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
        return self._decode_checked(
            EventEnvelope,
            row,
            {
                "run_id": "run_id",
                "event_id": "event_id",
                "schema_version": "schema_version",
                "event_type": "event_type",
                "created_at": "created_at",
                "actor": "actor",
                "transaction_id": "transaction_id",
                "stage_id": "stage_id",
                "artifact_id": "artifact_id",
                "decision": "decision",
                "reason": "reason",
            },
        )

    def scan_orphans(self) -> OrphanBlobScan:
        with self._lock:
            self._require_open()
            referenced = {
                str(row[0])
                for row in self._connection.execute(
                    "SELECT sha256 FROM artifact_revisions"
                ).fetchall()
            }
            found: set[str] = set()
            malformed: list[str] = []
            try:
                for path in _validate_blob_topology(
                    self.blob_root,
                    error_code="blob_topology_invalid",
                ):
                    relative = path.relative_to(self.blob_root).as_posix()
                    parts = relative.split("/")
                    if (
                        len(parts) == 3
                        and parts[0] == "sha256"
                        and len(parts[1]) == 2
                        and len(parts[2]) == 64
                        and parts[1] == parts[2][:2]
                        and all(char in "0123456789abcdef" for char in parts[2])
                    ):
                        found.add(parts[2])
                    else:
                        malformed.append(relative)
            except ControlStoreError:
                raise
            except OSError as exc:
                raise ControlStoreIntegrityError("orphan_scan_failed") from exc
            return OrphanBlobScan(
                orphan_hashes=tuple(sorted(found - referenced)),
                malformed_paths=tuple(malformed),
            )

    def backup_to(self, destination: str | os.PathLike[str]) -> Path:
        from multi_agent_brief.control_store.backup import backup_store

        with self._lock:
            self._require_open()
            return backup_store(self, destination)

    @classmethod
    def restore_to_new_path(
        cls,
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        blob_root: str | os.PathLike[str] | None = None,
    ) -> "SQLiteControlStore":
        from multi_agent_brief.control_store.backup import restore_store

        return restore_store(cls, source, destination, blob_root=blob_root)


if TYPE_CHECKING:
    from multi_agent_brief.control_store.uow import (
        ControlUnitOfWork,
        _StagedArtifactRevision,
        _TransactionIdentity,
    )


__all__ = ["ControlStoreSnapshot", "OrphanBlobScan", "SQLiteControlStore"]
