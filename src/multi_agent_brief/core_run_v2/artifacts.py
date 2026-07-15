"""Deterministic owned-artifact acceptance for dormant fresh-v2 runs."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath
from typing import Callable

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    ArtifactRecord,
    ArtifactRevision,
    AuditPromotionRequest,
    AuditProposal,
    AuditReportArtifact,
    CoreRunEventBinding,
    EventEnvelope,
    Invocation,
    OwnedArtifactSubmissionRecord,
    OwnedArtifactSubmitRequest,
)
from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import ScratchReader, parse_json_object
from multi_agent_brief.inputs.classifier import classify_input_dir

from .errors import CoreRunError, CoreRunResult, core_run_error_code
from .integrity import RunIntegrityService, materialize_checkout
from .policy import ARTIFACT_POLICIES, derived_id, transaction_type_for
from .verifier import CoreRunDomainVerifier, resolve_core_replay


_Clock = Callable[[], datetime]


class ArtifactAcceptanceService:
    """Accept exact role/tool bytes without completing a workflow Stage."""

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        clock: _Clock | None = None,
    ) -> None:
        try:
            self._reader = ScratchReader(workspace)
        except IntakeError as exc:
            raise CoreRunError("artifact_input_unsafe") from exc
        self.workspace = self._reader.root
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._verifier = CoreRunDomainVerifier()
        self._integrity = RunIntegrityService(self.workspace, clock=self._clock)

    def submit_owned_artifact(
        self,
        request: OwnedArtifactSubmitRequest,
    ) -> CoreRunResult:
        try:
            return self._submit_owned_artifact(request)
        except (CoreRunError, ControlStoreError) as exc:
            return CoreRunResult(
                status="failed_uncommitted",
                error_code=core_run_error_code(exc),
            )

    def promote_audit_proposal(
        self,
        request: AuditPromotionRequest,
    ) -> CoreRunResult:
        try:
            return self._promote_audit_proposal(request)
        except (CoreRunError, ControlStoreError) as exc:
            return CoreRunResult(
                status="failed_uncommitted",
                error_code=core_run_error_code(exc),
            )

    def _submit_owned_artifact(
        self,
        request: OwnedArtifactSubmitRequest,
    ) -> CoreRunResult:
        policy = ARTIFACT_POLICIES.get(request.artifact_id)
        if policy is None:
            raise CoreRunError("artifact_owner_mismatch")
        try:
            content = self._reader.read(request.input_path)
        except IntakeError as exc:
            raise CoreRunError("artifact_input_unsafe") from exc
        if PurePosixPath(request.input_path).suffix != policy.input_suffix:
            raise CoreRunError("artifact_input_unsafe")
        if request.artifact_id == "input_classification":
            expected_content = _input_classification_bytes(self.workspace)
            if content != expected_content:
                raise CoreRunError("artifact_input_unsafe")
            content = expected_content
        fingerprint = canonical_fingerprint(
            {
                "request": request.model_dump(mode="json", exclude_unset=False),
                "content_sha256": sha256_hex(content),
            }
        )
        with self._open_store() as store:
            replay = resolve_core_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            verified = self._verifier.verify(store, request.run_id)
            self._require_store_revision(
                verified.snapshot.store_revision,
                request.expected_store_revision,
            )
            stage_id = policy.owner_stage_id
            owner_role = policy.owner_role_id
            invocation: Invocation | None = None
            if policy.invocation_required:
                if request.invocation_id is None:
                    raise CoreRunError("artifact_owner_mismatch")
                invocation = next(
                    (
                        item
                        for item in verified.snapshot.invocations
                        if item.invocation_id == request.invocation_id
                    ),
                    None,
                )
                if invocation is None or invocation.status != "active":
                    raise CoreRunError("artifact_owner_mismatch")
                allowed_role = policy.invocation_role_id
                if (
                    request.artifact_id == "audited_brief"
                    and verified.binding.role_topology == "human_assisted"
                    and invocation.role_id == "writer"
                ):
                    stage_id = "analyst"
                    owner_role = "writer"
                elif invocation.role_id != allowed_role:
                    raise CoreRunError("artifact_owner_mismatch")
                invocation_stage = _invocation_stage(
                    verified.snapshot.events,
                    invocation.invocation_id,
                )
                if invocation_stage != stage_id:
                    raise CoreRunError("artifact_owner_mismatch")
            elif request.invocation_id is not None:
                raise CoreRunError("artifact_owner_mismatch")
            if request.producer_tool_id != policy.producer_tool_id:
                raise CoreRunError("artifact_owner_mismatch")
            stage = next(
                (item for item in verified.snapshot.stage_states if item.stage_id == stage_id),
                None,
            )
            if stage is None or stage.status != "ready":
                raise CoreRunError("stage_not_current")
            artifact = next(
                (
                    item
                    for item in verified.snapshot.artifacts
                    if item.artifact_id == request.artifact_id
                ),
                None,
            )
            if artifact is None:
                raise CoreRunError("artifact_owner_mismatch")
            if artifact.current_revision != request.expected_artifact_revision:
                raise CoreRunError("artifact_revision_conflict")
            if artifact.current_revision > 0 and self._integrity.revision_is_protected(
                verified,
                artifact.artifact_id,
                artifact.current_revision,
            ):
                raise CoreRunError("artifact_revision_conflict")
            parent = self._validate_parent(
                verified.snapshot,
                request,
                artifact.artifact_id,
                verified.binding.role_topology,
            )
            blocked = self._integrity.require_clean(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
                expected_store_revision=request.expected_store_revision,
                additional_revisions=(() if parent is None else (parent,)),
            )
            if blocked is not None:
                return blocked
            revision_number = artifact.current_revision + 1
            now = _now(self._clock)
            digest = sha256_hex(content)
            event_id = derived_id("EVT-ARTIFACT", request.request_id, fingerprint)
            submission_id = derived_id("SUBMISSION", request.request_id, digest)
            updated = ArtifactRecord.model_validate(
                {
                    **artifact.model_dump(mode="json", exclude_unset=False),
                    "current_revision": revision_number,
                    "status": "valid",
                },
                strict=True,
            )
            revision = ArtifactRevision.model_validate(
                {
                    "schema_version": ArtifactRevision.schema_id,
                    "run_id": request.run_id,
                    "artifact_id": artifact.artifact_id,
                    "revision": revision_number,
                    "path": artifact.path,
                    "sha256": digest,
                    "size_bytes": len(content),
                    "frozen": True,
                    "producer_kind": (
                        "workflow_stage" if invocation is not None else "control_tool"
                    ),
                    "producer_id": owner_role,
                    "created_at": now,
                },
                strict=True,
            )
            submission = OwnedArtifactSubmissionRecord.model_validate(
                {
                    "schema_version": OwnedArtifactSubmissionRecord.schema_id,
                    "submission_id": submission_id,
                    "run_id": request.run_id,
                    "artifact_id": artifact.artifact_id,
                    "artifact_revision": revision_number,
                    "artifact_sha256": digest,
                    "owner_stage_id": stage_id,
                    "owner_role_id": owner_role,
                    "run_contract_fingerprint": verified.binding.contract_fingerprint,
                    "invocation_id": request.invocation_id,
                    "producer_tool_id": request.producer_tool_id,
                    "parent_artifact": request.expected_parent_artifact,
                    "canonical_workspace_path": artifact.path,
                    "request_fingerprint": fingerprint,
                    "accepted_event_id": event_id,
                    "accepted_transaction_id": request.request_id,
                    "created_at": now,
                },
                strict=True,
            )
            event = _event(
                event_id=event_id,
                run_id=request.run_id,
                transaction_id=request.request_id,
                event_type="owned_artifact_accepted",
                stage_id=stage_id,
                artifact_id=artifact.artifact_id,
                reason="owned artifact accepted",
                created_at=now,
                binding=CoreRunEventBinding(
                    request_id=request.request_id,
                    request_fingerprint=fingerprint,
                    effect_kind="owned_artifact_acceptance",
                    primary_record_id=submission_id,
                    outcome="committed",
                ),
            )
            materialize_checkout(self.workspace, artifact.path, content)
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("owned_artifact_acceptance"),
                request.expected_store_revision,
            )
            if invocation is not None:
                unit.put_invocation(_completed_invocation(invocation, now))
            unit.put_artifact(updated)
            unit.put_artifact_revision(revision, content)
            unit.put_owned_artifact_submission(submission)
            unit.append_event(event)
            receipt = unit.commit()
            self._verifier.verify(store, request.run_id)
            return CoreRunResult(
                status="committed",
                receipt=receipt,
                primary_record_id=submission_id,
            )

    def _promote_audit_proposal(
        self,
        request: AuditPromotionRequest,
    ) -> CoreRunResult:
        with self._open_store() as store:
            verified = self._verifier.verify(store, request.run_id)
            proposal_record = next(
                (
                    item
                    for item in verified.snapshot.accepted_proposals
                    if item.proposal_id == request.audit_proposal_id
                ),
                None,
            )
            if proposal_record is None or proposal_record.proposal_kind != "audit":
                raise CoreRunError("artifact_owner_mismatch")
            try:
                proposal_bytes = store.read_artifact_revision_bytes(
                    request.run_id,
                    proposal_record.artifact_id,
                    proposal_record.artifact_revision,
                )
                proposal = AuditProposal.model_validate(
                    parse_json_object(proposal_bytes),
                    strict=True,
                )
            except (ControlStoreError, IntakeError, ValidationError) as exc:
                raise CoreRunError("control_store_integrity_invalid") from exc
            target = next(
                (
                    item
                    for item in verified.snapshot.artifact_revisions
                    if item.artifact_id == proposal.artifact_id
                    and item.revision == proposal.artifact_revision
                ),
                None,
            )
            report_record = next(
                (
                    item
                    for item in verified.snapshot.artifacts
                    if item.artifact_id == "audit_report"
                ),
                None,
            )
            if (
                target is None
                or report_record is None
                or proposal_record.target_artifact_id != target.artifact_id
                or proposal_record.target_artifact_revision != target.revision
                or request.expected_target_artifact.artifact_id != target.artifact_id
                or request.expected_target_artifact.revision != target.revision
                or report_record.current_revision != request.expected_audit_report_revision
            ):
                raise CoreRunError("artifact_revision_conflict")
            report = AuditReportArtifact.model_validate(
                {
                    "schema_version": AuditReportArtifact.schema_id,
                    "run_id": request.run_id,
                    "audit_proposal_id": proposal.proposal_id,
                    "target_artifact_id": target.artifact_id,
                    "target_artifact_revision": target.revision,
                    "target_artifact_sha256": target.sha256,
                    "decision": proposal.decision,
                    "findings": [
                        item.model_dump(mode="json", exclude_unset=False)
                        for item in proposal.findings
                    ],
                },
                strict=True,
            )
            content = canonical_json_bytes(report.model_dump(mode="json")) + b"\n"
            fingerprint = canonical_fingerprint(
                {
                    "request": request.model_dump(mode="json", exclude_unset=False),
                    "proposal_sha256": proposal_record.proposal_sha256,
                    "target_sha256": target.sha256,
                    "report_sha256": sha256_hex(content),
                }
            )
            replay = resolve_core_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            self._require_store_revision(
                verified.snapshot.store_revision,
                request.expected_store_revision,
            )
            auditor_stage = next(
                (
                    item
                    for item in verified.snapshot.stage_states
                    if item.stage_id == "auditor"
                ),
                None,
            )
            if auditor_stage is None or auditor_stage.status != "ready":
                raise CoreRunError("stage_not_current")
            if report_record.current_revision > 0 and self._integrity.revision_is_protected(
                verified,
                report_record.artifact_id,
                report_record.current_revision,
            ):
                raise CoreRunError("artifact_revision_conflict")
            blocked = self._integrity.require_clean(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
                expected_store_revision=request.expected_store_revision,
                additional_revisions=(target,),
            )
            if blocked is not None:
                return blocked
            now = _now(self._clock)
            digest = sha256_hex(content)
            revision_number = report_record.current_revision + 1
            event_id = derived_id("EVT-AUDIT", request.request_id, fingerprint)
            submission_id = derived_id("SUBMISSION-AUDIT", request.request_id, digest)
            updated = ArtifactRecord.model_validate(
                {
                    **report_record.model_dump(mode="json", exclude_unset=False),
                    "current_revision": revision_number,
                    "status": "valid",
                },
                strict=True,
            )
            revision = ArtifactRevision.model_validate(
                {
                    "schema_version": ArtifactRevision.schema_id,
                    "run_id": request.run_id,
                    "artifact_id": report_record.artifact_id,
                    "revision": revision_number,
                    "path": report_record.path,
                    "sha256": digest,
                    "size_bytes": len(content),
                    "frozen": True,
                    "producer_kind": "control_tool",
                    "producer_id": "audit-proposal-promoter-v2",
                    "created_at": now,
                },
                strict=True,
            )
            submission = OwnedArtifactSubmissionRecord.model_validate(
                {
                    "schema_version": OwnedArtifactSubmissionRecord.schema_id,
                    "submission_id": submission_id,
                    "run_id": request.run_id,
                    "artifact_id": report_record.artifact_id,
                    "artifact_revision": revision_number,
                    "artifact_sha256": digest,
                    "owner_stage_id": "auditor",
                    "owner_role_id": "auditor",
                    "run_contract_fingerprint": verified.binding.contract_fingerprint,
                    "invocation_id": proposal_record.invocation_id,
                    "producer_tool_id": "audit-proposal-promoter-v2",
                    "parent_artifact": request.expected_target_artifact,
                    "source_proposal_id": proposal_record.proposal_id,
                    "canonical_workspace_path": report_record.path,
                    "request_fingerprint": fingerprint,
                    "accepted_event_id": event_id,
                    "accepted_transaction_id": request.request_id,
                    "created_at": now,
                },
                strict=True,
            )
            event = _event(
                event_id=event_id,
                run_id=request.run_id,
                transaction_id=request.request_id,
                event_type="audit_proposal_promoted",
                stage_id="auditor",
                artifact_id=report_record.artifact_id,
                reason="audit proposal promoted",
                created_at=now,
                binding=CoreRunEventBinding(
                    request_id=request.request_id,
                    request_fingerprint=fingerprint,
                    effect_kind="audit_promotion",
                    primary_record_id=submission_id,
                    outcome="committed",
                ),
            )
            materialize_checkout(self.workspace, report_record.path, content)
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("audit_promotion"),
                request.expected_store_revision,
            )
            unit.put_artifact(updated)
            unit.put_artifact_revision(revision, content)
            unit.put_owned_artifact_submission(submission)
            unit.append_event(event)
            receipt = unit.commit()
            self._verifier.verify(store, request.run_id)
            return CoreRunResult(
                status="committed",
                receipt=receipt,
                primary_record_id=submission_id,
            )

    @staticmethod
    def _validate_parent(
        snapshot: object,
        request: OwnedArtifactSubmitRequest,
        artifact_id: str,
        topology: str,
    ):
        expected = request.expected_parent_artifact
        if artifact_id != "audited_brief":
            if expected is not None:
                raise CoreRunError("artifact_owner_mismatch")
            return None
        if expected is None and topology == "human_assisted":
            return None
        if expected is None:
            raise CoreRunError("artifact_revision_conflict")
        revision = next(
            (
                item
                for item in snapshot.artifact_revisions  # type: ignore[attr-defined]
                if item.artifact_id == expected.artifact_id
                and item.revision == expected.revision
            ),
            None,
        )
        artifact = next(
            (
                item
                for item in snapshot.artifacts  # type: ignore[attr-defined]
                if item.artifact_id == expected.artifact_id
            ),
            None,
        )
        if (
            revision is None
            or artifact is None
            or expected.artifact_id != "analyst_draft_snapshot"
            or artifact.current_revision != expected.revision
        ):
            raise CoreRunError("artifact_revision_conflict")
        return revision

    @staticmethod
    def _require_store_revision(actual: int, expected: int) -> None:
        if actual != expected:
            raise CoreRunError("store_revision_conflict")

    def _open_store(self) -> SQLiteControlStore:
        try:
            return SQLiteControlStore.open(self.workspace / "briefloop.db", clock=self._clock)
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc


def _completed_invocation(invocation: Invocation, completed_at: str) -> Invocation:
    return Invocation.model_validate(
        {
            **invocation.model_dump(mode="json", exclude_unset=False),
            "status": "completed",
            "completed_at": completed_at,
        },
        strict=True,
    )


def _input_classification_bytes(workspace: Path) -> bytes:
    """Recompute the fixed input-governance tool output before acceptance."""

    try:
        classified = classify_input_dir(workspace / "input")
        return canonical_json_bytes(classified) + b"\n"
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise CoreRunError("artifact_input_unsafe") from exc


def _invocation_stage(events: tuple[EventEnvelope, ...], invocation_id: str) -> str:
    starts = [
        event
        for event in events
        if event.event_type == "role_invocation_started"
        and event.core_run_binding is not None
        and event.core_run_binding.primary_record_id == invocation_id
    ]
    if len(starts) != 1 or starts[0].stage_id is None:
        raise CoreRunError("control_store_integrity_invalid")
    return starts[0].stage_id


def _event(
    *,
    event_id: str,
    run_id: str,
    transaction_id: str,
    event_type: str,
    stage_id: str,
    artifact_id: str,
    reason: str,
    created_at: str,
    binding: CoreRunEventBinding,
) -> EventEnvelope:
    return EventEnvelope.model_validate(
        {
            "schema_version": EventEnvelope.schema_id,
            "event_id": event_id,
            "run_id": run_id,
            "event_type": event_type,
            "created_at": created_at,
            "actor": "system",
            "transaction_id": transaction_id,
            "stage_id": stage_id,
            "artifact_id": artifact_id,
            "decision": "continue",
            "reason": reason,
            "metadata": {},
            "core_run_binding": binding,
        },
        strict=True,
    )


def _now(clock: _Clock) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CoreRunError("core_run_request_invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["ArtifactAcceptanceService"]
