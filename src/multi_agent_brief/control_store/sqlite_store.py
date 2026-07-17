"""Typed SQLite ControlStore substrate with no current runtime authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
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
    ContractId,
    Delivery,
    DeliveryAttemptRecord,
    DeliveryAuthorizationRecord,
    DeliveryResultRecord,
    EventEnvelope,
    GateArtifactBinding,
    GateEvaluationRecord,
    GateFindingRecord,
    FinalizationRecord,
    FinalizeRenderRecord,
    Invocation,
    OwnedArtifactSubmissionRecord,
    ProposalSourceBinding,
    PackageArtifactBinding,
    PackageReadyRecord,
    RecoveryCompletionRecord,
    RepairCompletionRecord,
    RepairCycleRecord,
    ArtifactSupersessionRecord,
    RunContractBinding,
    RunIdentity,
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
_EXTENDED_RECORD_MODELS = (
    WorkspaceRunHead,
    ArtifactIdentityRecord,
    AcceptedSourceRecord,
    AcceptedProposalRecord,
    ProposalSourceBinding,
    RunContractBinding,
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
)


def _canonical_record_text(record: StrictModel) -> str:
    if type(record) not in _EXTENDED_RECORD_MODELS:
        return canonical_model_text(record)
    payload = record.model_dump(mode="json", exclude_unset=False)
    return canonical_json_bytes(payload).decode("utf-8")


def _decode_record(model_type: type[_ModelT], payload_text: str) -> _ModelT:
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
                raise ControlStoreIntegrityError(
                    missing_blob_error_code or error_code
                )
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
                raise ControlStoreIntegrityError(
                    missing_blob_error_code or error_code
                )
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
        def relation_keys(name: str, fields: tuple[str, ...]) -> set[tuple[object, ...]]:
            return {
                tuple(getattr(reference, field) for field in fields)
                for receipt in transactions
                for reference in getattr(receipt, name)
            }

        event_ids = {
            event_id for receipt in transactions for event_id in receipt.event_ids
        }
        revision_keys = relation_keys(
            "artifact_revisions", ("artifact_id", "revision")
        )
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
        for identity in sorted(
            artifact_identities, key=lambda item: item.artifact_id
        ):
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
            for transition in sorted(latest_stage.values(), key=lambda item: item.stage_id)
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
            if event.intake_binding is None or event.intake_binding.outcome != "rejected":
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
        workspace_run_head = self._workspace_head_at_revision(committed_revision)
        proposal_source_bindings = tuple(
            item
            for item in full.proposal_source_bindings
            if item.proposal_id in proposal_ids
        )
        return replace(
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
            transactions=transactions,
        )

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
                        (transition.successor_run_id, transition.accepted_transaction_id)
                    )
                    is not None
                    and revision_by_transaction[
                        (transition.successor_run_id, transition.accepted_transaction_id)
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

    def _existing_receipt(
        self,
        run_id: str,
        transaction_id: str,
        fingerprint: str,
    ) -> TransactionReceipt | None:
        row = self._connection.execute(
            """
            SELECT fingerprint, payload_json
            FROM transactions
            WHERE run_id = ? AND transaction_id = ?
            """,
            (run_id, transaction_id),
        ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise ControlStoreConflict("transaction_replay_conflict")
        receipt = _decode_record(TransactionReceipt, str(row[1]))
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
                self._insert_owned_artifact_submissions(
                    uow._owned_artifact_submissions.values()
                )
                self._insert_stage_transitions(uow._stage_transitions.values())
                self._insert_stage_artifact_bindings(
                    uow._stage_artifact_bindings.values()
                )
                self._insert_stage_gate_bindings(
                    uow._stage_gate_bindings.values()
                )
                self._insert_claims(uow._claims.values())
                self._insert_claim_source_bindings(
                    uow._claim_source_bindings.values()
                )
                self._insert_claim_freezes(uow._claim_freezes.values())
                self._insert_gate_evaluations(uow._gate_evaluations.values())
                self._insert_gate_findings(uow._gate_findings.values())
                self._insert_gate_artifact_bindings(
                    uow._gate_artifact_bindings.values()
                )
                self._insert_run_integrity_records(
                    uow._run_integrity_records.values()
                )
                self._insert_pr4b_records(uow)
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
            if self._connection.execute(
                """
                SELECT 1 FROM artifact_revisions
                WHERE run_id = ? AND artifact_id = ? AND revision = ?
                """,
                (run_id, artifact_id, revision),
            ).fetchone() is not None:
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
                raise ControlStoreIntegrityError(
                    "transaction_ledger_integrity_invalid"
                )
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
        if binding is not None:
            refs = {
                (binding.stage_specs_artifact.artifact_id, binding.stage_specs_artifact.revision),
                (
                    binding.artifact_contracts_artifact.artifact_id,
                    binding.artifact_contracts_artifact.revision,
                ),
                (binding.policy_pack_artifact.artifact_id, binding.policy_pack_artifact.revision),
            }
            if (
                binding.accepted_transaction_id != uow.transaction_id
                or binding.initialization_event_id not in staged_events
                or not refs <= available_revisions
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
                bool(uow._owned_artifact_submissions),
                bool(uow._stage_transitions),
                bool(uow._claims),
                bool(uow._claim_freezes),
                bool(uow._gate_evaluations),
                bool(uow._run_integrity_records),
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
                or (record.claim_id is not None and record.claim_id not in available_claims)
                or (record.source_id is not None and record.source_id not in available_sources)
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
            if getattr(record, "run_id", None) != run_id or getattr(
                record, "accepted_transaction_id", None
            ) != uow.transaction_id:
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
            if set(completion.supersession_ids) != set(uow._artifact_supersessions) and not all(
                self._connection.execute(
                    "SELECT 1 FROM artifact_supersessions WHERE run_id=? AND supersession_id=?",
                    (run_id, item),
                ).fetchone()
                for item in completion.supersession_ids
            ):
                raise ControlStoreConflict("relational_integrity_conflict")
            if len(completion.reopened_transition_ids) != len(set(completion.reopened_transition_ids)):
                raise ControlStoreConflict("relational_integrity_conflict")
        for render in uow._finalize_renders.values():
            if not render.reader_artifacts:
                raise ControlStoreConflict("relational_integrity_conflict")
        for archive in uow._run_archives.values():
            bindings = [item for item in uow._run_archive_artifact_bindings.values() if item.archive_id == archive.archive_id]
            if len(bindings) != archive.included_count or sorted(item.position for item in bindings) != list(range(len(bindings))):
                raise ControlStoreConflict("relational_integrity_conflict")
        for package in uow._package_ready_records.values():
            bindings = [item for item in uow._package_artifact_bindings.values() if item.package_id == package.package_id]
            if len(bindings) != package.artifact_count or sorted(item.position for item in bindings) != list(range(len(bindings))):
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
                    "owned_artifact_submissions": [
                        {"submission_id": key}
                        for key in sorted(uow._owned_artifact_submissions)
                    ],
                    "stage_transitions": [
                        {"transition_id": key}
                        for key in sorted(uow._stage_transitions)
                    ],
                    "stage_artifact_bindings": [
                        {"transition_id": key[0], "position": key[1]}
                        for key in sorted(uow._stage_artifact_bindings)
                    ],
                    "stage_gate_bindings": [
                        {"transition_id": key[0], "gate_id": key[1]}
                        for key in sorted(uow._stage_gate_bindings)
                    ],
                    "claims": [
                        {"claim_id": key} for key in sorted(uow._claims)
                    ],
                    "claim_source_bindings": [
                        {"claim_id": key[0], "source_id": key[1]}
                        for key in sorted(uow._claim_source_bindings)
                    ],
                    "claim_freezes": [
                        {"freeze_id": key}
                        for key in sorted(uow._claim_freezes)
                    ],
                    "gate_evaluations": [
                        {"evaluation_id": key}
                        for key in sorted(uow._gate_evaluations)
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
                    "repair_cycles": [{"repair_id": key} for key in sorted(uow._repair_cycles)],
                    "artifact_supersessions": [{"supersession_id": key} for key in sorted(uow._artifact_supersessions)],
                    "repair_completions": [{"repair_completion_id": key} for key in sorted(uow._repair_completions)],
                    "recovery_completions": [{"recovery_id": key} for key in sorted(uow._recovery_completions)],
                    "run_head_transitions": [{"head_transition_id": key} for key in sorted(uow._run_head_transitions)],
                    "finalize_renders": [{"render_id": key} for key in sorted(uow._finalize_renders)],
                    "finalizations": [{"finalization_id": key} for key in sorted(uow._finalizations)],
                    "run_archives": [{"archive_id": key} for key in sorted(uow._run_archives)],
                    "run_archive_artifact_bindings": [
                        {"archive_id": key[0], "position": key[1]}
                        for key in sorted(uow._run_archive_artifact_bindings)
                    ],
                    "package_ready_records": [{"package_id": key} for key in sorted(uow._package_ready_records)],
                    "package_artifact_bindings": [
                        {"package_id": key[0], "position": key[1]}
                        for key in sorted(uow._package_artifact_bindings)
                    ],
                    "approvals": [{"approval_id": key} for key in sorted(uow._approvals)],
                    "approval_package_bindings": [
                        {"approval_id": key[0], "package_id": key[1]}
                        for key in sorted(uow._approval_package_bindings)
                    ],
                    "delivery_authorizations": [{"authorization_id": key} for key in sorted(uow._delivery_authorizations)],
                    "delivery_attempts": [{"attempt_id": key} for key in sorted(uow._delivery_attempts)],
                    "delivery_results": [{"result_id": key} for key in sorted(uow._delivery_results)],
                }
            )
        except ValueError as exc:
            raise ControlStoreIntegrityError("transaction_identity_invalid") from exc

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

    def _insert_pr4b_records(self, uow: "ControlUnitOfWork") -> None:
        for record in uow._repair_cycles.values():
            self._connection.execute(
                "INSERT INTO repair_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (record.run_id, record.repair_id, record.schema_version, record.contamination_revision,
                 record.owner_stage_id, record.reason_code, record.started_at, record.start_event_id,
                 record.accepted_transaction_id, record.request_fingerprint, _canonical_record_text(record)),
            )
        for record in uow._artifact_supersessions.values():
            self._connection.execute(
                "INSERT INTO artifact_supersessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.run_id, record.supersession_id, record.repair_id, record.mode, record.schema_version,
                 record.prior_artifact.artifact_id, record.prior_artifact.revision, record.successor_artifact.revision,
                 record.reason_code, record.created_at, record.accepted_event_id, record.accepted_transaction_id,
                 record.request_fingerprint, _canonical_record_text(record)),
            )
        for record in uow._repair_completions.values():
            self._connection.execute(
                "INSERT INTO repair_completions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (record.run_id, record.repair_completion_id, record.repair_id, record.schema_version,
                 record.contamination_revision, record.completed_at, record.completion_event_id,
                 record.accepted_transaction_id, record.request_fingerprint, _canonical_record_text(record)),
            )
            for position, value in enumerate(record.supersession_ids):
                self._connection.execute("INSERT INTO repair_completion_supersessions VALUES (?,?,?,?)", (record.run_id, record.repair_completion_id, position, value))
            for position, value in enumerate(record.reopened_transition_ids):
                self._connection.execute("INSERT INTO repair_completion_transitions VALUES (?,?,?,?)", (record.run_id, record.repair_completion_id, position, value))
        for record in uow._recovery_completions.values():
            self._connection.execute(
                "INSERT INTO recovery_completions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (record.run_id, record.recovery_id, record.repair_completion_id, record.schema_version,
                 record.contamination_revision, record.disposition, record.completed_at, record.completion_event_id,
                 record.accepted_transaction_id, record.request_fingerprint, _canonical_record_text(record)),
            )
            for table, values in (
                ("recovery_supersessions", record.supersession_ids),
                ("recovery_stage_transitions", record.rerun_transition_ids),
                ("recovery_gate_evaluations", record.gate_evaluation_ids),
            ):
                for position, value in enumerate(values):
                    self._connection.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", (record.run_id, record.recovery_id, position, value))
        for record in uow._run_head_transitions.values():
            self._connection.execute(
                "INSERT INTO run_head_transitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.workspace_id, record.head_transition_id, record.successor_run_id, record.predecessor_run_id,
                 record.schema_version, record.prior_workspace_revision, record.successor_workspace_revision,
                 record.reason_code, record.successor_disposition, record.created_at, record.transition_event_id,
                 record.accepted_transaction_id, record.request_fingerprint, _canonical_record_text(record)),
            )
        for record in uow._finalize_renders.values():
            self._connection.execute(
                "INSERT INTO finalize_renders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.run_id, record.render_id, record.schema_version, record.audit_proposal_id,
                 record.audited_brief.artifact_id, record.audited_brief.revision,
                 record.audit_report.artifact_id, record.audit_report.revision, record.reader_clean_status,
                 record.policy_result_fingerprint, record.run_contract_fingerprint, record.created_at,
                 record.render_event_id, record.accepted_transaction_id, record.request_fingerprint,
                 _canonical_record_text(record)),
            )
            revisions = {
                (item.record.artifact_id, item.record.revision): item
                for item in uow._artifact_revisions
            }
            for position, reference in enumerate(record.reader_artifacts):
                revision = revisions.get((reference.artifact_id, reference.revision))
                if revision is None:
                    row = self._connection.execute("SELECT sha256 FROM artifact_revisions WHERE run_id=? AND artifact_id=? AND revision=?", (record.run_id, reference.artifact_id, reference.revision)).fetchone()
                    if row is None:
                        raise ControlStoreConflict("relational_integrity_conflict")
                    digest = str(row[0])
                else:
                    digest = revision.record.sha256
                self._connection.execute("INSERT INTO finalize_render_artifacts VALUES (?,?,?,?,?,?)", (record.run_id, record.render_id, position, reference.artifact_id, reference.revision, digest))
        for record in uow._finalizations.values():
            self._connection.execute(
                "INSERT INTO finalizations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.run_id, record.finalization_id, record.schema_version, record.render_id,
                 record.finalize_transition_id, record.finalize_gate_batch_id, record.recovery_id,
                 record.integrity_revision, record.finalized_at, record.finalization_event_id,
                 record.accepted_transaction_id, record.request_fingerprint, _canonical_record_text(record)),
            )
            for position, value in enumerate(record.finalize_gate_evaluation_ids):
                self._connection.execute("INSERT INTO finalization_gate_evaluations VALUES (?,?,?,?)", (record.run_id, record.finalization_id, position, value))
        for record in uow._run_archives.values():
            self._connection.execute(
                "INSERT INTO run_archives VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.run_id, record.archive_id, record.schema_version, record.finalization_id,
                 record.archive_artifact.artifact_id, record.archive_artifact.revision, record.manifest_sha256,
                 record.included_count, record.created_at, record.archive_event_id, record.accepted_transaction_id,
                 record.request_fingerprint, _canonical_record_text(record)),
            )
        for record in uow._run_archive_artifact_bindings.values():
            self._connection.execute("INSERT INTO run_archive_artifact_bindings VALUES (?,?,?,?,?,?,?,?,?,?)", (record.run_id, record.archive_id, record.position, record.schema_version, record.artifact_id, record.artifact_revision, record.artifact_sha256, record.usage, record.accepted_transaction_id, _canonical_record_text(record)))
        for record in uow._package_ready_records.values():
            self._connection.execute(
                "INSERT INTO package_ready_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.run_id, record.package_id, record.schema_version, record.finalization_id, record.archive_id,
                 record.package_manifest_artifact.artifact_id, record.package_manifest_artifact.revision,
                 record.package_manifest_sha256, record.artifact_count, record.created_at, record.package_event_id,
                 record.accepted_transaction_id, record.request_fingerprint, _canonical_record_text(record)),
            )
        for record in uow._package_artifact_bindings.values():
            self._connection.execute("INSERT INTO package_artifact_bindings VALUES (?,?,?,?,?,?,?,?,?,?)", (record.run_id, record.package_id, record.position, record.schema_version, record.artifact_id, record.artifact_revision, record.artifact_sha256, record.usage, record.accepted_transaction_id, _canonical_record_text(record)))
        for record in uow._approval_package_bindings.values():
            self._connection.execute("INSERT INTO approval_package_bindings VALUES (?,?,?,?,?,?)", (record.run_id, record.approval_id, record.package_id, record.schema_version, record.accepted_transaction_id, _canonical_record_text(record)))
        for record in uow._delivery_authorizations.values():
            self._connection.execute("INSERT INTO delivery_authorizations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (record.run_id, record.authorization_id, record.schema_version, record.package_id, record.prior_authorization_id, record.approval_mode, record.retry_of_attempt_id, record.purpose, record.decision, record.target, record.channel, record.recipient_fingerprint, record.actor_id, record.recorded_at, record.authorization_event_id, record.accepted_transaction_id, record.request_fingerprint, _canonical_record_text(record)))
        for record in uow._delivery_attempts.values():
            self._connection.execute("INSERT INTO delivery_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (record.run_id, record.attempt_id, record.schema_version, record.package_id, record.authorization_id, record.target, record.channel, record.recipient_fingerprint, record.connector_operation_id, record.connector_request_fingerprint, record.created_at, record.attempt_event_id, record.accepted_transaction_id, record.request_fingerprint, _canonical_record_text(record)))
        for record in uow._delivery_results.values():
            evidence = record.evidence_artifact
            self._connection.execute("INSERT INTO delivery_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (record.run_id, record.result_id, record.schema_version, record.attempt_id, record.prior_result_id, record.reconciliation_authorization_id, record.status, record.adapter_id, record.adapter_version, record.connector_operation_id, record.evidence_sha256, evidence.artifact_id if evidence else None, evidence.revision if evidence else None, record.recorded_at, record.result_event_id, record.accepted_transaction_id, record.request_fingerprint, _canonical_record_text(record)))

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
            ("transaction_artifact_supersessions", receipt.artifact_supersessions, "supersession_id"),
            ("transaction_repair_completions", receipt.repair_completions, "repair_completion_id"),
            ("transaction_recovery_completions", receipt.recovery_completions, "recovery_id"),
            ("transaction_run_head_transitions", receipt.run_head_transitions, "head_transition_id"),
            ("transaction_finalize_renders", receipt.finalize_renders, "render_id"),
            ("transaction_finalizations", receipt.finalizations, "finalization_id"),
            ("transaction_run_archives", receipt.run_archives, "archive_id"),
            ("transaction_package_ready_records", receipt.package_ready_records, "package_id"),
            ("transaction_approvals", receipt.approvals, "approval_id"),
            ("transaction_delivery_authorizations", receipt.delivery_authorizations, "authorization_id"),
            ("transaction_delivery_attempts", receipt.delivery_attempts, "attempt_id"),
            ("transaction_delivery_results", receipt.delivery_results, "result_id"),
        )
        for table, references, field in simple_relations:
            for position, reference in enumerate(references):
                self._connection.execute(
                    f"INSERT INTO {table} VALUES (?,?,?,?)",
                    (receipt.run_id, receipt.transaction_id, position, getattr(reference, field)),
                )
        for table, references, identity_field in (
            ("transaction_run_archive_artifact_bindings", receipt.run_archive_artifact_bindings, "archive_id"),
            ("transaction_package_artifact_bindings", receipt.package_artifact_bindings, "package_id"),
        ):
            for position, reference in enumerate(references):
                self._connection.execute(
                    f"INSERT INTO {table} VALUES (?,?,?,?,?)",
                    (receipt.run_id, receipt.transaction_id, position, getattr(reference, identity_field), reference.position),
                )
        for position, reference in enumerate(receipt.approval_package_bindings):
            self._connection.execute(
                "INSERT INTO transaction_approval_package_bindings VALUES (?,?,?,?,?)",
                (receipt.run_id, receipt.transaction_id, position, reference.approval_id, reference.package_id),
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
                            raise ControlStoreIntegrityError("blob_read_failed") from exc
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

    def _load_snapshot_in_transaction(self, run_id: str) -> ControlStoreSnapshot:
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
                    "raw_underlying_evidence_type": (
                        "raw_underlying_evidence_type"
                    ),
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
                    "raw_payload_artifact_revision": (
                        "raw_payload_artifact_revision"
                    ),
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
            repair_cycles=self._load_for_run(RepairCycleRecord, "repair_cycles", run_id, "started_at, repair_id", {"run_id":"run_id","repair_id":"repair_id","schema_version":"schema_version","contamination_revision":"contamination_revision","owner_stage_id":"owner_stage_id","reason_code":"reason_code","started_at":"started_at","start_event_id":"start_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            artifact_supersessions=self._load_for_run(ArtifactSupersessionRecord, "artifact_supersessions", run_id, "created_at, supersession_id", {"run_id":"run_id","supersession_id":"supersession_id","repair_id":"repair_id","schema_version":"schema_version","mode":"mode","artifact_id":"prior_artifact.artifact_id","prior_revision":"prior_artifact.revision","successor_revision":"successor_artifact.revision","reason_code":"reason_code","created_at":"created_at","accepted_event_id":"accepted_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            repair_completions=self._load_for_run(RepairCompletionRecord, "repair_completions", run_id, "completed_at, repair_completion_id", {"run_id":"run_id","repair_completion_id":"repair_completion_id","repair_id":"repair_id","schema_version":"schema_version","contamination_revision":"contamination_revision","completed_at":"completed_at","completion_event_id":"completion_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            recovery_completions=self._load_for_run(RecoveryCompletionRecord, "recovery_completions", run_id, "completed_at, recovery_id", {"run_id":"run_id","recovery_id":"recovery_id","repair_completion_id":"repair_completion_id","schema_version":"schema_version","contamination_revision":"contamination_revision","disposition":"disposition","completed_at":"completed_at","completion_event_id":"completion_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            run_head_transitions=self._load_for_run(RunHeadTransitionRecord, "run_head_transitions", run_id, "created_at, head_transition_id", {"successor_run_id":"successor_run_id","head_transition_id":"head_transition_id","workspace_id":"workspace_id","predecessor_run_id":"predecessor_run_id","schema_version":"schema_version","prior_workspace_revision":"prior_workspace_revision","successor_workspace_revision":"successor_workspace_revision","reason_code":"reason_code","successor_disposition":"successor_disposition","created_at":"created_at","transition_event_id":"transition_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}, run_column="successor_run_id"),
            finalize_renders=self._load_for_run(FinalizeRenderRecord, "finalize_renders", run_id, "created_at, render_id", {"run_id":"run_id","render_id":"render_id","schema_version":"schema_version","audit_proposal_id":"audit_proposal_id","audited_brief_artifact_id":"audited_brief.artifact_id","audited_brief_revision":"audited_brief.revision","audit_report_artifact_id":"audit_report.artifact_id","audit_report_revision":"audit_report.revision","reader_clean_status":"reader_clean_status","policy_result_fingerprint":"policy_result_fingerprint","run_contract_fingerprint":"run_contract_fingerprint","created_at":"created_at","render_event_id":"render_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            finalizations=self._load_for_run(FinalizationRecord, "finalizations", run_id, "finalized_at, finalization_id", {"run_id":"run_id","finalization_id":"finalization_id","schema_version":"schema_version","render_id":"render_id","finalize_transition_id":"finalize_transition_id","finalize_gate_batch_id":"finalize_gate_batch_id","recovery_id":"recovery_id","integrity_revision":"integrity_revision","finalized_at":"finalized_at","finalization_event_id":"finalization_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            run_archives=self._load_for_run(RunArchiveRecord, "run_archives", run_id, "created_at, archive_id", {"run_id":"run_id","archive_id":"archive_id","schema_version":"schema_version","finalization_id":"finalization_id","archive_artifact_id":"archive_artifact.artifact_id","archive_artifact_revision":"archive_artifact.revision","manifest_sha256":"manifest_sha256","included_count":"included_count","created_at":"created_at","archive_event_id":"archive_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            run_archive_artifact_bindings=self._load_for_run(RunArchiveArtifactBinding, "run_archive_artifact_bindings", run_id, "archive_id, position", {"run_id":"run_id","archive_id":"archive_id","position":"position","schema_version":"schema_version","artifact_id":"artifact_id","artifact_revision":"artifact_revision","artifact_sha256":"artifact_sha256","usage":"usage","accepted_transaction_id":"accepted_transaction_id"}),
            package_ready_records=self._load_for_run(PackageReadyRecord, "package_ready_records", run_id, "created_at, package_id", {"run_id":"run_id","package_id":"package_id","schema_version":"schema_version","finalization_id":"finalization_id","archive_id":"archive_id","package_manifest_artifact_id":"package_manifest_artifact.artifact_id","package_manifest_revision":"package_manifest_artifact.revision","package_manifest_sha256":"package_manifest_sha256","artifact_count":"artifact_count","created_at":"created_at","package_event_id":"package_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            package_artifact_bindings=self._load_for_run(PackageArtifactBinding, "package_artifact_bindings", run_id, "package_id, position", {"run_id":"run_id","package_id":"package_id","position":"position","schema_version":"schema_version","artifact_id":"artifact_id","artifact_revision":"artifact_revision","artifact_sha256":"artifact_sha256","usage":"usage","accepted_transaction_id":"accepted_transaction_id"}),
            approval_package_bindings=self._load_for_run(ApprovalPackageBinding, "approval_package_bindings", run_id, "approval_id, package_id", {"run_id":"run_id","approval_id":"approval_id","package_id":"package_id","schema_version":"schema_version","accepted_transaction_id":"accepted_transaction_id"}),
            delivery_authorizations=self._load_for_run(DeliveryAuthorizationRecord, "delivery_authorizations", run_id, "recorded_at, authorization_id", {"run_id":"run_id","authorization_id":"authorization_id","schema_version":"schema_version","package_id":"package_id","prior_authorization_id":"prior_authorization_id","approval_mode":"approval_mode","retry_of_attempt_id":"retry_of_attempt_id","purpose":"purpose","decision":"decision","target":"target","channel":"channel","recipient_fingerprint":"recipient_fingerprint","actor_id":"actor_id","recorded_at":"recorded_at","authorization_event_id":"authorization_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            delivery_attempts=self._load_for_run(DeliveryAttemptRecord, "delivery_attempts", run_id, "created_at, attempt_id", {"run_id":"run_id","attempt_id":"attempt_id","schema_version":"schema_version","package_id":"package_id","authorization_id":"authorization_id","target":"target","channel":"channel","recipient_fingerprint":"recipient_fingerprint","connector_operation_id":"connector_operation_id","connector_request_fingerprint":"connector_request_fingerprint","created_at":"created_at","attempt_event_id":"attempt_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            delivery_results=self._load_for_run(DeliveryResultRecord, "delivery_results", run_id, "recorded_at, result_id", {"run_id":"run_id","result_id":"result_id","schema_version":"schema_version","attempt_id":"attempt_id","prior_result_id":"prior_result_id","reconciliation_authorization_id":"reconciliation_authorization_id","status":"status","adapter_id":"adapter_id","adapter_version":"adapter_version","connector_operation_id":"connector_operation_id","evidence_sha256":"evidence_sha256","evidence_artifact_id":"evidence_artifact.artifact_id","evidence_artifact_revision":"evidence_artifact.revision","recorded_at":"recorded_at","result_event_id":"result_event_id","accepted_transaction_id":"accepted_transaction_id","request_fingerprint":"request_fingerprint"}),
            transactions=self._load_transactions(run_id),
        )
        self._verify_core_snapshot_structure(snapshot)
        return snapshot

    def _verify_core_snapshot_structure(self, snapshot: ControlStoreSnapshot) -> None:
        """Verify PR-4A relation closure without interpreting domain policy."""

        core_rows_exist = any(
            (
                snapshot.run_contract_bindings,
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
                "run_direction": binding.run_direction.model_dump(
                    mode="json",
                    exclude_unset=False,
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

        receipts = {
            item.transaction_id: item for item in snapshot.transactions
        }
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
        }
        if (
            initialization is None
            or initialization.run_id != snapshot.run.run_id
            or initialization.transaction_type
            not in {"core-v2-initialize", "core-v2-run-reset"}
            or [item.run_id for item in initialization.run_contract_bindings]
            != [snapshot.run.run_id]
            or binding.initialization_event_id not in initialization.event_ids
            or (
                initialization.transaction_type == "core-v2-run-reset"
                and len(initialization.run_head_transitions) != 1
            )
            or (
                initialization.transaction_type == "core-v2-initialize"
                and initialization.run_head_transitions
            )
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
                initialization.transaction_type == "core-v2-run-reset"
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

        transitions_by_stage: dict[str, list[StageTransitionRecord]] = {}
        transition_by_id: dict[str, StageTransitionRecord] = {}
        for transition in snapshot.stage_transitions:
            if transition.transition_id in transition_by_id:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            transition_by_id[transition.transition_id] = transition
            transitions_by_stage.setdefault(transition.stage_id, []).append(
                transition
            )
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
            or [item.integrity_revision for item in initialization.run_integrity_records]
            != [1]
        ):
            raise ControlStoreIntegrityError("core_run_relation_invalid")
        contaminated = False
        for position, record in enumerate(integrity_rows, start=1):
            if record.integrity_revision != position:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            if record.status == "contaminated":
                contaminated = True
            elif contaminated:
                raise ControlStoreIntegrityError("core_run_relation_invalid")

        invocation_events: dict[str, list[EventEnvelope]] = {}
        for event in snapshot.events:
            core = event.core_run_binding
            if core is not None and core.effect_kind == "invocation_start":
                invocation_events.setdefault(core.primary_record_id, []).append(
                    event
                )
        source_invocations = [item.invocation_id for item in snapshot.sources]
        proposal_invocations = [
            item.invocation_id for item in snapshot.accepted_proposals
        ]
        submission_invocations = [
            item.invocation_id
            for item in snapshot.owned_artifact_submissions
            if item.invocation_id is not None
            and item.source_proposal_id is None
        ]
        completed_records = (
            source_invocations + proposal_invocations + submission_invocations
        )
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
            explanations = completed_records.count(invocation.invocation_id)
            failures = failed_records.count(invocation.invocation_id)
            if invocation.status == "active" and (explanations or failures):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            if invocation.status == "completed" and (
                explanations != 1 or failures
            ):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            if invocation.status == "failed" and (
                failures != 1 or explanations
            ):
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
        revisions_by_artifact: dict[str, list[ArtifactRevision]] = {}
        for revision in snapshot.artifact_revisions:
            revisions_by_artifact.setdefault(revision.artifact_id, []).append(
                revision
            )
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
        evaluations = {
            item.evaluation_id: item for item in snapshot.gate_evaluations
        }
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
        for transaction_id, transaction_evaluations in evaluations_by_transaction.items():
            receipt = receipts.get(transaction_id)
            evaluation_ids = {
                item.evaluation_id for item in transaction_evaluations
            }
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

        def values(table: str, owner_column: str, owner_id: str, value_column: str) -> tuple[str, ...]:
            rows = self._connection.execute(
                f"SELECT position, {value_column} FROM {table} "
                f"WHERE run_id=? AND {owner_column}=? ORDER BY position",
                (snapshot.run.run_id, owner_id),
            ).fetchall()
            if [row[0] for row in rows] != list(range(len(rows))):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            return tuple(str(row[1]) for row in rows)

        for record in snapshot.repair_completions:
            if values("repair_completion_supersessions", "repair_completion_id", record.repair_completion_id, "supersession_id") != tuple(record.supersession_ids):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            if values("repair_completion_transitions", "repair_completion_id", record.repair_completion_id, "transition_id") != tuple(record.reopened_transition_ids):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        for record in snapshot.recovery_completions:
            for table, column, expected in (
                ("recovery_supersessions", "supersession_id", record.supersession_ids),
                ("recovery_stage_transitions", "transition_id", record.rerun_transition_ids),
                ("recovery_gate_evaluations", "evaluation_id", record.gate_evaluation_ids),
            ):
                if values(table, "recovery_id", record.recovery_id, column) != tuple(expected):
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
            expected = tuple((item.artifact_id, item.revision) for item in record.reader_artifacts)
            actual = tuple((str(row[1]), int(row[2])) for row in rows)
            if [row[0] for row in rows] != list(range(len(rows))) or actual != expected:
                raise ControlStoreIntegrityError("core_run_relation_invalid")
            if any(revision_digests.get((str(row[1]), int(row[2]))) != str(row[3]) for row in rows):
                raise ControlStoreIntegrityError("core_run_relation_invalid")
        for record in snapshot.finalizations:
            if values("finalization_gate_evaluations", "finalization_id", record.finalization_id, "evaluation_id") != tuple(record.finalize_gate_evaluation_ids):
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
        model = _decode_record(model_type, str(row["payload_json"]))
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
        return model

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
        if [row[0] for row in event_rows] != list(range(len(event_rows))) or [
            row[0] for row in revision_rows
        ] != list(range(len(revision_rows))) or [
            row[0] for row in identity_rows
        ] != list(range(len(identity_rows))) or [
            row[0] for row in source_rows
        ] != list(range(len(source_rows))) or [
            row[0] for row in proposal_rows
        ] != list(range(len(proposal_rows))):
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
                "transaction_owned_artifact_submissions",
                ("submission_id",),
                tuple(
                    (item.submission_id,)
                    for item in receipt.owned_artifact_submissions
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
                    (item.integrity_revision,)
                    for item in receipt.run_integrity_records
                ),
            ),
            ("transaction_repair_cycles", ("repair_id",), tuple((item.repair_id,) for item in receipt.repair_cycles)),
            ("transaction_artifact_supersessions", ("supersession_id",), tuple((item.supersession_id,) for item in receipt.artifact_supersessions)),
            ("transaction_repair_completions", ("repair_completion_id",), tuple((item.repair_completion_id,) for item in receipt.repair_completions)),
            ("transaction_recovery_completions", ("recovery_id",), tuple((item.recovery_id,) for item in receipt.recovery_completions)),
            ("transaction_run_head_transitions", ("head_transition_id",), tuple((item.head_transition_id,) for item in receipt.run_head_transitions)),
            ("transaction_finalize_renders", ("render_id",), tuple((item.render_id,) for item in receipt.finalize_renders)),
            ("transaction_finalizations", ("finalization_id",), tuple((item.finalization_id,) for item in receipt.finalizations)),
            ("transaction_run_archives", ("archive_id",), tuple((item.archive_id,) for item in receipt.run_archives)),
            ("transaction_run_archive_artifact_bindings", ("archive_id", "binding_position"), tuple((item.archive_id, item.position) for item in receipt.run_archive_artifact_bindings)),
            ("transaction_package_ready_records", ("package_id",), tuple((item.package_id,) for item in receipt.package_ready_records)),
            ("transaction_package_artifact_bindings", ("package_id", "binding_position"), tuple((item.package_id, item.position) for item in receipt.package_artifact_bindings)),
            ("transaction_approvals", ("approval_id",), tuple((item.approval_id,) for item in receipt.approvals)),
            ("transaction_approval_package_bindings", ("approval_id", "package_id"), tuple((item.approval_id, item.package_id) for item in receipt.approval_package_bindings)),
            ("transaction_delivery_authorizations", ("authorization_id",), tuple((item.authorization_id,) for item in receipt.delivery_authorizations)),
            ("transaction_delivery_attempts", ("attempt_id",), tuple((item.attempt_id,) for item in receipt.delivery_attempts)),
            ("transaction_delivery_results", ("result_id",), tuple((item.result_id,) for item in receipt.delivery_results)),
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
            actual = tuple(tuple(row[index + 1] for index in range(len(columns))) for row in rows)
            if actual != expected:
                raise ControlStoreIntegrityError("transaction_relation_mismatch")

    def _verify_workspace_ledger_graph(self) -> None:
        """Verify one complete workspace transaction graph in this SQL snapshot."""

        def invalid() -> None:
            raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")

        workspace_revision = self._workspace_revision_in_transaction()
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
        revisions_by_artifact: dict[
            tuple[str, str], list[ArtifactRevision]
        ] = {}
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

    def _verify_run_head_transition_chain(self) -> None:
        """Verify that reset transitions form one acyclic chain ending at head."""

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
        if initial is None or initial not in run_ids or len(transitions) + 1 != len(run_ids):
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
                or transition.successor_workspace_revision != transition.prior_workspace_revision + 1
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
            relation_columns = ", ".join(
                ("run_id", "transaction_id", *relation_ids)
            )
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
                raise ControlStoreIntegrityError(
                    "transaction_ledger_integrity_invalid"
                )

    def _verify_pr4b_relation_coverage(self) -> None:
        """Prove every PR-4B authoritative row has one receipt owner."""

        specs = (
            ("transaction_repair_cycles", ("repair_id",), "repair_cycles", "run_id", ("repair_id",)),
            ("transaction_artifact_supersessions", ("supersession_id",), "artifact_supersessions", "run_id", ("supersession_id",)),
            ("transaction_repair_completions", ("repair_completion_id",), "repair_completions", "run_id", ("repair_completion_id",)),
            ("transaction_recovery_completions", ("recovery_id",), "recovery_completions", "run_id", ("recovery_id",)),
            ("transaction_run_head_transitions", ("head_transition_id",), "run_head_transitions", "successor_run_id", ("head_transition_id",)),
            ("transaction_finalize_renders", ("render_id",), "finalize_renders", "run_id", ("render_id",)),
            ("transaction_finalizations", ("finalization_id",), "finalizations", "run_id", ("finalization_id",)),
            ("transaction_run_archives", ("archive_id",), "run_archives", "run_id", ("archive_id",)),
            ("transaction_run_archive_artifact_bindings", ("archive_id", "binding_position"), "run_archive_artifact_bindings", "run_id", ("archive_id", "position")),
            ("transaction_package_ready_records", ("package_id",), "package_ready_records", "run_id", ("package_id",)),
            ("transaction_package_artifact_bindings", ("package_id", "binding_position"), "package_artifact_bindings", "run_id", ("package_id", "position")),
            ("transaction_approval_package_bindings", ("approval_id", "package_id"), "approval_package_bindings", "run_id", ("approval_id", "package_id")),
            ("transaction_delivery_authorizations", ("authorization_id",), "delivery_authorizations", "run_id", ("authorization_id",)),
            ("transaction_delivery_attempts", ("attempt_id",), "delivery_attempts", "run_id", ("attempt_id",)),
            ("transaction_delivery_results", ("result_id",), "delivery_results", "run_id", ("result_id",)),
        )
        for relation_table, relation_ids, domain_table, domain_run, domain_ids in specs:
            relation_columns = ", ".join(("run_id", "transaction_id", *relation_ids))
            owners: dict[tuple[object, ...], str] = {}
            for row in self._connection.execute(f"SELECT {relation_columns} FROM {relation_table}").fetchall():
                key = (row[0], *(row[index + 2] for index in range(len(relation_ids))))
                if key in owners:
                    raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
                owners[key] = str(row[1])

            domain_columns = ", ".join((domain_run, *domain_ids, "accepted_transaction_id"))
            domain_keys: set[tuple[object, ...]] = set()
            for row in self._connection.execute(f"SELECT {domain_columns} FROM {domain_table}").fetchall():
                key = tuple(row[index] for index in range(len(domain_ids) + 1))
                owner = str(row[len(domain_ids) + 1])
                if key in domain_keys or owners.get(key) != owner:
                    raise ControlStoreIntegrityError("transaction_ledger_integrity_invalid")
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
                or raw_artifact.current_revision
                != source.raw_payload_artifact_revision
                or raw_artifact.path != expected_raw_path
            ):
                raise ControlStoreIntegrityError(
                    "transaction_ledger_integrity_invalid"
                )
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
