"""Dormant fresh-v2 source and role-proposal intake service."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import cast

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    AcceptedProposalRecord,
    AcceptedSourceRecord,
    ArtifactRecord,
    ArtifactRevision,
    ArtifactSubmitRequest,
    AuditProposal,
    CandidateClaimsProposal,
    ClaimDraftsProposal,
    EventEnvelope,
    IntakeEventBinding,
    Invocation,
    ProposalSourceBinding,
    ScreenedCandidatesProposal,
    SourceCommitRequest,
    SourceProposal,
    StrictModel,
    TransactionReceipt,
)
from multi_agent_brief.control_store import (
    ControlStoreSnapshot,
    ControlStoreConflict,
    ControlStoreError,
    ControlStoreIntegrityError,
    ControlStoreSchemaError,
    ControlStoreStateError,
    ControlUnitOfWork,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    sha256_hex,
)
from multi_agent_brief.intake_v2.errors import IntakeError, IntakeResult
from multi_agent_brief.intake_v2.policy import (
    INTAKE_LANES,
    LanePolicy,
    SourcePolicyError,
    evaluate_source_eligibility,
)
from multi_agent_brief.intake_v2.scratch import ScratchReader, parse_json_object


_Clock = Callable[[], datetime]
_FailureHook = Callable[[str], None]
_SOURCE_FORMATS = {
    ".json": "json",
    ".md": "markdown",
    ".txt": "text",
    ".html": "html",
    ".pdf": "pdf",
    ".bin": "binary",
}


class IntakeService:
    """Validate one request and commit its complete accepted/rejected graph."""

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        clock: _Clock | None = None,
        _store_failure_hook: _FailureHook | None = None,
    ) -> None:
        self._reader = ScratchReader(workspace)
        self.workspace = self._reader.root
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._store_failure_hook = _store_failure_hook

    def submit_source(self, request_path: str | os.PathLike[str]) -> IntakeResult:
        try:
            return self._submit_source(request_path)
        except IntakeError as exc:
            return IntakeResult(status="failed_uncommitted", error_code=exc.code)

    def submit_proposal(
        self,
        lane: str,
        request_path: str | os.PathLike[str],
    ) -> IntakeResult:
        try:
            if lane not in INTAKE_LANES or lane == "source":
                raise IntakeError("intake_request_invalid")
            return self._submit_proposal(INTAKE_LANES[lane], request_path)
        except IntakeError as exc:
            return IntakeResult(status="failed_uncommitted", error_code=exc.code)

    def _submit_source(self, request_path: str | os.PathLike[str]) -> IntakeResult:
        request = self._read_request(SourceCommitRequest, request_path)
        proposal_bytes = self._reader.read(request.proposal_path)
        content_bytes = self._reader.read(request.content_path)
        raw_bytes = (
            None
            if request.raw_payload_path is None
            else self._reader.read(request.raw_payload_path)
        )
        request_fingerprint = canonical_fingerprint(
            {
                "lane": "source",
                "request": request.model_dump(mode="json", exclude_unset=False),
                "proposal_sha256": sha256_hex(proposal_bytes),
                "content_sha256": sha256_hex(content_bytes),
                "raw_payload_sha256": (
                    None if raw_bytes is None else sha256_hex(raw_bytes)
                ),
            }
        )
        with self._open_store() as store:
            replay = self._resolve_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay
            snapshot, invocation, owner_stage = self._trusted_submission_context(
                store,
                INTAKE_LANES["source"],
                request,
            )
            proposal: SourceProposal | None = None
            try:
                proposal = self._parse_proposal(SourceProposal, proposal_bytes)
                if proposal.run_id != request.run_id:
                    raise _KnownInvalid("proposal_contract_invalid")
                raw_declared = proposal.raw_payload_sha256 is not None
                if raw_declared != (raw_bytes is not None):
                    raise _KnownInvalid("proposal_contract_invalid")
                if proposal.content_sha256 != sha256_hex(content_bytes):
                    raise _KnownInvalid("source_hash_mismatch")
                if raw_bytes is not None and (
                    proposal.raw_payload_sha256 != sha256_hex(raw_bytes)
                ):
                    raise _KnownInvalid("source_hash_mismatch")
                try:
                    claims_eligible, eligibility_reason = (
                        evaluate_source_eligibility(
                            proposal,
                            raw_payload_present=raw_bytes is not None,
                        )
                    )
                except SourcePolicyError as exc:
                    raise _KnownInvalid(str(exc)) from exc
                if any(
                    source.source_id == proposal.source_id
                    for source in snapshot.sources
                ):
                    raise IntakeError("submission_replay_conflict")
                content_artifact_id, raw_artifact_id = _source_artifact_ids(
                    request.run_id,
                    proposal.source_id,
                )
                if any(
                    artifact.artifact_id in {content_artifact_id, raw_artifact_id}
                    for artifact in snapshot.artifacts
                ):
                    raise IntakeError("submission_replay_conflict")
            except _KnownInvalid as exc:
                return self._record_rejection(
                    store,
                    request=request,
                    request_fingerprint=request_fingerprint,
                    invocation=invocation,
                    owner_stage=owner_stage,
                    reason_code=exc.code,
                    source_id=None if proposal is None else proposal.source_id,
                )
            except (IntakeError, ValidationError):
                if proposal is None:
                    return self._record_rejection(
                        store,
                        request=request,
                        request_fingerprint=request_fingerprint,
                        invocation=invocation,
                        owner_stage=owner_stage,
                        reason_code="proposal_contract_invalid",
                    )
                raise
            return self._commit_source(
                store,
                request=request,
                proposal=proposal,
                content_bytes=content_bytes,
                raw_bytes=raw_bytes,
                request_fingerprint=request_fingerprint,
                invocation=invocation,
                owner_stage=owner_stage,
                claims_eligible=claims_eligible,
                eligibility_reason=eligibility_reason,
            )

    def _submit_proposal(
        self,
        lane: LanePolicy,
        request_path: str | os.PathLike[str],
    ) -> IntakeResult:
        request = self._read_request(ArtifactSubmitRequest, request_path)
        proposal_bytes = self._reader.read(request.input_path)
        request_fingerprint = canonical_fingerprint(
            {
                "lane": lane.lane,
                "request": request.model_dump(mode="json", exclude_unset=False),
                "proposal_sha256": sha256_hex(proposal_bytes),
            }
        )
        with self._open_store() as store:
            replay = self._resolve_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay
            snapshot, invocation, owner_stage = self._trusted_submission_context(
                store,
                lane,
                request,
            )
            if request.artifact_id != lane.artifact_id:
                raise IntakeError("artifact_owner_mismatch")
            artifact = _by_id(snapshot.artifacts, "artifact_id", request.artifact_id)
            current_revision = 0 if artifact is None else artifact.current_revision
            if request.expected_artifact_revision != current_revision:
                raise IntakeError("expected_artifact_revision_conflict")

            proposal: StrictModel | None = None
            try:
                proposal = self._parse_proposal(lane.proposal_model, proposal_bytes)
                if getattr(proposal, "run_id") != request.run_id:
                    raise _KnownInvalid("proposal_contract_invalid")
                proposal_id = cast(str, getattr(proposal, "proposal_id"))
                if any(
                    item.proposal_id == proposal_id
                    for item in snapshot.accepted_proposals
                ):
                    raise IntakeError("submission_replay_conflict")
                lineage = self._validate_proposal_lineage(
                    store,
                    snapshot,
                    lane,
                    proposal,
                )
            except _KnownInvalid as exc:
                return self._record_rejection(
                    store,
                    request=request,
                    request_fingerprint=request_fingerprint,
                    invocation=invocation,
                    owner_stage=owner_stage,
                    reason_code=exc.code,
                    proposal_id=(
                        None if proposal is None else getattr(proposal, "proposal_id")
                    ),
                )
            except (IntakeError, ValidationError):
                if proposal is None:
                    return self._record_rejection(
                        store,
                        request=request,
                        request_fingerprint=request_fingerprint,
                        invocation=invocation,
                        owner_stage=owner_stage,
                        reason_code="proposal_contract_invalid",
                    )
                raise
            return self._commit_proposal(
                store,
                request=request,
                lane=lane,
                proposal=proposal,
                proposal_bytes=proposal_bytes,
                request_fingerprint=request_fingerprint,
                invocation=invocation,
                owner_stage=owner_stage,
                lineage=lineage,
                prior_artifact=artifact,
            )

    def _read_request(
        self,
        model_type: type[SourceCommitRequest] | type[ArtifactSubmitRequest],
        request_path: str | os.PathLike[str],
    ) -> SourceCommitRequest | ArtifactSubmitRequest:
        try:
            payload = self._reader.read_request(request_path)
            data = parse_json_object(payload)
        except IntakeError as exc:
            if exc.code == "scratch_payload_unreadable":
                raise IntakeError("intake_request_invalid") from exc
            raise
        try:
            request = model_type.model_validate(data, strict=True)
        except ValidationError as exc:
            raise IntakeError("intake_request_invalid") from exc
        request_parent = PurePosixPath(str(request_path)).parent
        if request_parent != PurePosixPath("scratch") / request.invocation_id:
            raise IntakeError("intake_request_invalid")
        return request

    @staticmethod
    def _parse_proposal(
        model_type: type[StrictModel],
        payload: bytes,
    ) -> StrictModel:
        try:
            data = parse_json_object(payload)
            return model_type.model_validate(data, strict=True)
        except (IntakeError, ValidationError) as exc:
            raise _KnownInvalid("proposal_contract_invalid") from exc

    def _open_store(self) -> SQLiteControlStore:
        database = self.workspace / "briefloop.db"
        try:
            return SQLiteControlStore.open(
                database,
                clock=self._clock,
                _failure_hook=self._store_failure_hook,
            )
        except ControlStoreStateError as exc:
            if exc.code == "database_not_found":
                raise IntakeError("control_store_not_found") from exc
            raise IntakeError("control_store_integrity_invalid") from exc
        except ControlStoreSchemaError as exc:
            if exc.code in {"unsupported_schema_version", "future_schema_version"}:
                raise IntakeError("unsupported_schema_version") from exc
            raise IntakeError("control_store_integrity_invalid") from exc
        except ControlStoreIntegrityError as exc:
            raise IntakeError("control_store_integrity_invalid") from exc
        except ControlStoreError as exc:
            raise IntakeError("control_store_integrity_invalid") from exc

    def _resolve_replay(
        self,
        store: SQLiteControlStore,
        *,
        run_id: str,
        request_id: str,
        request_fingerprint: str,
    ) -> IntakeResult | None:
        try:
            receipt = store.load_transaction_receipt(run_id, request_id)
        except ControlStoreError as exc:
            raise IntakeError("control_store_integrity_invalid") from exc
        if receipt is None:
            return None
        try:
            snapshot = store.load_snapshot(run_id)
        except ControlStoreError as exc:
            raise IntakeError("control_store_integrity_invalid") from exc
        bound_events = [
            event
            for event in snapshot.events
            if event.event_id in receipt.event_ids
            and event.intake_binding is not None
            and event.intake_binding.request_id == request_id
        ]
        if len(bound_events) != 1:
            raise IntakeError("control_store_integrity_invalid")
        binding = cast(IntakeEventBinding, bound_events[0].intake_binding)
        if binding.request_fingerprint != request_fingerprint:
            raise IntakeError("submission_replay_conflict")
        if binding.outcome == "rejected":
            return IntakeResult(
                status="rejected_recorded",
                receipt=receipt,
                error_code=binding.reason_code,
                source_id=binding.source_id,
                proposal_id=binding.proposal_id,
            )
        return IntakeResult(
            status="replayed",
            receipt=receipt,
            source_id=binding.source_id,
            proposal_id=binding.proposal_id,
        )

    def _trusted_submission_context(
        self,
        store: SQLiteControlStore,
        lane: LanePolicy,
        request: SourceCommitRequest | ArtifactSubmitRequest,
    ) -> tuple[ControlStoreSnapshot, Invocation, str]:
        try:
            head = store.load_workspace_run_head()
        except ControlStoreError as exc:
            raise IntakeError("control_store_integrity_invalid") from exc
        if head is None:
            raise IntakeError("current_run_binding_missing")
        if head.current_run_id != request.run_id:
            raise IntakeError("run_not_current")
        try:
            snapshot = store.load_snapshot(request.run_id)
        except ControlStoreError as exc:
            raise IntakeError("control_store_integrity_invalid") from exc
        if any(
            stage.stage_id == "finalize" and stage.status == "complete"
            for stage in snapshot.stage_states
        ) or any(event.event_type == "run_archived" for event in snapshot.events):
            raise IntakeError("new_run_required")
        if snapshot.store_revision != request.expected_store_revision:
            raise IntakeError("expected_store_revision_conflict")
        invocation = _by_id(
            snapshot.invocations,
            "invocation_id",
            request.invocation_id,
        )
        if invocation is None:
            try:
                bound_runs = store.find_invocation_run_ids(request.invocation_id)
            except ControlStoreError as exc:
                raise IntakeError("control_store_integrity_invalid") from exc
            if bound_runs:
                raise IntakeError("invocation_run_mismatch")
            raise IntakeError("invocation_not_found")
        if invocation.run_id != request.run_id:
            raise IntakeError("invocation_run_mismatch")
        if invocation.status != "active":
            raise IntakeError("invocation_not_active")
        owner = next(
            (item for item in lane.owners if item[1] == invocation.role_id),
            None,
        )
        if owner is None:
            raise IntakeError("invocation_role_mismatch")
        owner_stage, _owner_role = owner
        stage = _by_id(snapshot.stage_states, "stage_id", owner_stage)
        if stage is None or stage.status != "ready":
            raise IntakeError("stage_not_ready")
        return snapshot, invocation, owner_stage

    def _validate_proposal_lineage(
        self,
        store: SQLiteControlStore,
        snapshot: ControlStoreSnapshot,
        lane: LanePolicy,
        proposal: StrictModel,
    ) -> "_ProposalLineage":
        if lane.lane == "candidate":
            typed = cast(CandidateClaimsProposal, proposal)
            source_ids = _ordered_unique(item.source_id for item in typed.candidates)
            self._require_eligible_sources(snapshot, source_ids)
            return _ProposalLineage(source_ids=source_ids)
        if lane.lane == "screened":
            typed = cast(ScreenedCandidatesProposal, proposal)
            parent = _by_id(
                snapshot.accepted_proposals,
                "proposal_id",
                typed.candidate_claims_proposal_id,
            )
            if parent is None or parent.proposal_kind != "candidate":
                raise _KnownInvalid("proposal_parent_invalid")
            parent_bytes = self._trusted_proposal_bytes(
                store,
                parent,
                CandidateClaimsProposal,
            )
            expected_ids = {item.candidate_id for item in parent_bytes.candidates}
            actual_ids = {item.candidate_id for item in typed.decisions}
            if actual_ids != expected_ids or len(typed.decisions) != len(expected_ids):
                raise _KnownInvalid("candidate_universe_mismatch")
            return _ProposalLineage(parent_proposal_id=parent.proposal_id)
        if lane.lane == "claim-drafts":
            typed = cast(ClaimDraftsProposal, proposal)
            parent = _by_id(
                snapshot.accepted_proposals,
                "proposal_id",
                typed.screened_candidates_proposal_id,
            )
            if parent is None or parent.proposal_kind != "screened":
                raise _KnownInvalid("proposal_parent_invalid")
            source_ids = _ordered_unique(
                source_id for draft in typed.drafts for source_id in draft.source_ids
            )
            self._require_eligible_sources(snapshot, source_ids)
            return _ProposalLineage(
                parent_proposal_id=parent.proposal_id,
                source_ids=source_ids,
            )
        typed = cast(AuditProposal, proposal)
        artifact = _by_id(snapshot.artifacts, "artifact_id", typed.artifact_id)
        revision = next(
            (
                item
                for item in snapshot.artifact_revisions
                if item.artifact_id == typed.artifact_id
                and item.revision == typed.artifact_revision
            ),
            None,
        )
        if (
            artifact is None
            or revision is None
            or artifact.current_revision != typed.artifact_revision
            or not revision.frozen
        ):
            raise _KnownInvalid("audit_target_invalid")
        return _ProposalLineage(
            target_artifact_id=typed.artifact_id,
            target_artifact_revision=typed.artifact_revision,
        )

    @staticmethod
    def _require_eligible_sources(
        snapshot: ControlStoreSnapshot,
        source_ids: tuple[str, ...],
    ) -> None:
        sources = {source.source_id: source for source in snapshot.sources}
        for source_id in source_ids:
            source = sources.get(source_id)
            if source is None:
                raise _KnownInvalid("source_not_found")
            if not source.claims_eligible:
                raise _KnownInvalid("source_not_claims_eligible")

    @staticmethod
    def _trusted_proposal_bytes(
        store: SQLiteControlStore,
        record: AcceptedProposalRecord,
        model_type: type[StrictModel],
    ) -> StrictModel:
        try:
            payload = store.read_artifact_revision_bytes(
                record.run_id,
                record.artifact_id,
                record.artifact_revision,
            )
            value = parse_json_object(payload)
            model = model_type.model_validate(value, strict=True)
        except (ControlStoreError, IntakeError, ValidationError) as exc:
            raise IntakeError("control_store_integrity_invalid") from exc
        if (
            sha256_hex(payload) != record.proposal_sha256
            or getattr(model, "proposal_id", None) != record.proposal_id
            or getattr(model, "run_id", None) != record.run_id
        ):
            raise IntakeError("control_store_integrity_invalid")
        return model

    def _commit_source(
        self,
        store: SQLiteControlStore,
        *,
        request: SourceCommitRequest,
        proposal: SourceProposal,
        content_bytes: bytes,
        raw_bytes: bytes | None,
        request_fingerprint: str,
        invocation: Invocation,
        owner_stage: str,
        claims_eligible: bool,
        eligibility_reason: str,
    ) -> IntakeResult:
        now = self._now()
        content_artifact_id, raw_artifact_id = _source_artifact_ids(
            request.run_id,
            proposal.source_id,
        )
        content_path = _blob_workspace_path(proposal.content_sha256)
        content_artifact, content_revision = _artifact_pair(
            run_id=request.run_id,
            artifact_id=content_artifact_id,
            revision=1,
            path=content_path,
            artifact_format=_SOURCE_FORMATS[PurePosixPath(request.content_path).suffix],
            sha256=proposal.content_sha256,
            size_bytes=len(content_bytes),
            producer_id=owner_stage,
            created_at=now,
        )
        raw_artifact: ArtifactRecord | None = None
        raw_revision: ArtifactRevision | None = None
        raw_path: str | None = None
        if raw_bytes is not None:
            if proposal.raw_payload_sha256 is None or raw_artifact_id is None:
                raise IntakeError("control_store_integrity_invalid")
            raw_path = _blob_workspace_path(proposal.raw_payload_sha256)
            raw_artifact, raw_revision = _artifact_pair(
                run_id=request.run_id,
                artifact_id=raw_artifact_id,
                revision=1,
                path=raw_path,
                artifact_format=_SOURCE_FORMATS[
                    PurePosixPath(cast(str, request.raw_payload_path)).suffix
                ],
                sha256=proposal.raw_payload_sha256,
                size_bytes=len(raw_bytes),
                producer_id=owner_stage,
                created_at=now,
            )
        event_id = _derived_id("EVT-SOURCE", request.request_id, request_fingerprint)
        source = AcceptedSourceRecord.model_validate(
            {
                "schema_version": AcceptedSourceRecord.schema_id,
                "source_id": proposal.source_id,
                "run_id": request.run_id,
                "origin_type": proposal.origin_type,
                "acquisition_method": proposal.acquisition_method,
                "material_kind": proposal.material_kind,
                "provider": proposal.provider,
                "locator": proposal.locator.model_dump(mode="json"),
                "title": proposal.title,
                "publisher": proposal.publisher,
                "published_at": proposal.published_at,
                "retrieved_at": proposal.retrieved_at,
                "source_category": proposal.source_category,
                "retrieval_source_type": proposal.retrieval_source_type,
                "underlying_evidence_type": proposal.underlying_evidence_type,
                "raw_underlying_evidence_type": (
                    proposal.raw_underlying_evidence_type
                ),
                "content_sha256": proposal.content_sha256,
                "content_size_bytes": len(content_bytes),
                "content_media_type": proposal.content_media_type,
                "content_blob_path": content_path,
                "content_artifact_id": content_artifact_id,
                "content_artifact_revision": 1,
                "raw_payload_sha256": proposal.raw_payload_sha256,
                "raw_payload_size_bytes": (
                    None if raw_bytes is None else len(raw_bytes)
                ),
                "raw_payload_media_type": proposal.raw_payload_media_type,
                "raw_payload_blob_path": raw_path,
                "raw_payload_artifact_id": (
                    None if raw_bytes is None else raw_artifact_id
                ),
                "raw_payload_artifact_revision": (
                    None if raw_bytes is None else 1
                ),
                "claims_eligible": claims_eligible,
                "eligibility_reason": eligibility_reason,
                "invocation_id": request.invocation_id,
                "acquisition_event_id": event_id,
                "accepted_transaction_id": request.request_id,
                "request_fingerprint": request_fingerprint,
                "created_at": now,
            },
            strict=True,
        )
        event = _intake_event(
            event_id=event_id,
            run_id=request.run_id,
            event_type="source_evidence_committed",
            transaction_id=request.request_id,
            invocation_id=request.invocation_id,
            request_fingerprint=request_fingerprint,
            outcome="committed",
            created_at=now,
            stage_id=owner_stage,
            artifact_id=content_artifact_id,
            source_id=proposal.source_id,
        )
        completed = _completed_invocation(invocation, now)
        unit = store.begin(
            request.run_id,
            request.request_id,
            "source_evidence_intake",
            request.expected_store_revision,
        )
        unit.put_invocation(completed)
        unit.put_artifact(content_artifact)
        unit.put_artifact_revision(content_revision, content_bytes)
        if raw_artifact is not None and raw_revision is not None and raw_bytes is not None:
            unit.put_artifact(raw_artifact)
            unit.put_artifact_revision(raw_revision, raw_bytes)
        unit.append_event(event)
        unit.put_source(source)
        receipt = self._commit_uow(unit)
        self._verify_source_readback(store, source, receipt)
        return IntakeResult(
            status="committed",
            receipt=receipt,
            source_id=source.source_id,
        )

    def _commit_proposal(
        self,
        store: SQLiteControlStore,
        *,
        request: ArtifactSubmitRequest,
        lane: LanePolicy,
        proposal: StrictModel,
        proposal_bytes: bytes,
        request_fingerprint: str,
        invocation: Invocation,
        owner_stage: str,
        lineage: "_ProposalLineage",
        prior_artifact: ArtifactRecord | None,
    ) -> IntakeResult:
        now = self._now()
        revision_number = request.expected_artifact_revision + 1
        digest = sha256_hex(proposal_bytes)
        path = _blob_workspace_path(digest)
        artifact, revision = _artifact_pair(
            run_id=request.run_id,
            artifact_id=request.artifact_id,
            revision=revision_number,
            path=path,
            artifact_format="json",
            sha256=digest,
            size_bytes=len(proposal_bytes),
            producer_id=owner_stage,
            created_at=now,
            required=False if prior_artifact is None else prior_artifact.required,
        )
        proposal_id = cast(str, getattr(proposal, "proposal_id"))
        event_id = _derived_id("EVT-PROPOSAL", request.request_id, request_fingerprint)
        accepted = AcceptedProposalRecord.model_validate(
            {
                "schema_version": AcceptedProposalRecord.schema_id,
                "proposal_id": proposal_id,
                "run_id": request.run_id,
                "proposal_kind": lane.proposal_kind,
                "artifact_id": request.artifact_id,
                "artifact_revision": revision_number,
                "proposal_sha256": digest,
                "invocation_id": request.invocation_id,
                "owner_stage_id": owner_stage,
                "owner_role_id": invocation.role_id,
                "parent_proposal_id": lineage.parent_proposal_id,
                "target_artifact_id": lineage.target_artifact_id,
                "target_artifact_revision": lineage.target_artifact_revision,
                "source_ids": list(lineage.source_ids),
                "accepted_event_id": event_id,
                "accepted_transaction_id": request.request_id,
                "request_fingerprint": request_fingerprint,
                "created_at": now,
            },
            strict=True,
        )
        event = _intake_event(
            event_id=event_id,
            run_id=request.run_id,
            event_type="role_proposal_committed",
            transaction_id=request.request_id,
            invocation_id=request.invocation_id,
            request_fingerprint=request_fingerprint,
            outcome="committed",
            created_at=now,
            stage_id=owner_stage,
            artifact_id=request.artifact_id,
            proposal_id=proposal_id,
        )
        completed = _completed_invocation(invocation, now)
        unit = store.begin(
            request.run_id,
            request.request_id,
            lane.transaction_type,
            request.expected_store_revision,
        )
        unit.put_invocation(completed)
        unit.put_artifact(artifact)
        unit.put_artifact_revision(revision, proposal_bytes)
        unit.append_event(event)
        unit.put_accepted_proposal(accepted)
        for source_id in lineage.source_ids:
            unit.put_proposal_source_binding(
                ProposalSourceBinding.model_validate(
                    {
                        "schema_version": ProposalSourceBinding.schema_id,
                        "run_id": request.run_id,
                        "proposal_id": proposal_id,
                        "source_id": source_id,
                    },
                    strict=True,
                )
            )
        receipt = self._commit_uow(unit)
        self._verify_proposal_readback(store, accepted, receipt)
        return IntakeResult(
            status="committed",
            receipt=receipt,
            proposal_id=proposal_id,
        )

    def _record_rejection(
        self,
        store: SQLiteControlStore,
        *,
        request: SourceCommitRequest | ArtifactSubmitRequest,
        request_fingerprint: str,
        invocation: Invocation,
        owner_stage: str,
        reason_code: str,
        source_id: str | None = None,
        proposal_id: str | None = None,
    ) -> IntakeResult:
        now = self._now()
        event = _intake_event(
            event_id=_derived_id("EVT-REJECT", request.request_id, request_fingerprint),
            run_id=request.run_id,
            event_type="intake_rejected",
            transaction_id=request.request_id,
            invocation_id=request.invocation_id,
            request_fingerprint=request_fingerprint,
            outcome="rejected",
            created_at=now,
            stage_id=owner_stage,
            reason_code=reason_code,
            source_id=source_id,
            proposal_id=proposal_id,
        )
        failed = _failed_invocation(invocation, now, reason_code)
        unit = store.begin(
            request.run_id,
            request.request_id,
            "intake_rejection",
            request.expected_store_revision,
        )
        unit.put_invocation(failed)
        unit.append_event(event)
        receipt = self._commit_uow(unit)
        return IntakeResult(
            status="rejected_recorded",
            receipt=receipt,
            error_code=reason_code,
            source_id=source_id,
            proposal_id=proposal_id,
        )

    @staticmethod
    def _commit_uow(unit: ControlUnitOfWork) -> TransactionReceipt:
        try:
            return unit.commit()
        except ControlStoreConflict as exc:
            if exc.code == "store_revision_conflict":
                raise IntakeError("expected_store_revision_conflict") from exc
            if exc.code == "transaction_replay_conflict":
                raise IntakeError("submission_replay_conflict") from exc
            raise IntakeError("intake_commit_failed") from exc
        except ControlStoreError as exc:
            raise IntakeError("intake_commit_failed") from exc

    @staticmethod
    def _verify_source_readback(store, expected, receipt) -> None:
        try:
            snapshot = store.load_snapshot(expected.run_id)
        except ControlStoreError as exc:
            raise IntakeError("intake_commit_failed") from exc
        actual = _by_id(snapshot.sources, "source_id", expected.source_id)
        if actual != expected or receipt.source_ids != [expected.source_id]:
            raise IntakeError("intake_commit_failed")

    @staticmethod
    def _verify_proposal_readback(store, expected, receipt) -> None:
        try:
            snapshot = store.load_snapshot(expected.run_id)
        except ControlStoreError as exc:
            raise IntakeError("intake_commit_failed") from exc
        actual = _by_id(
            snapshot.accepted_proposals,
            "proposal_id",
            expected.proposal_id,
        )
        bindings = {
            item.source_id
            for item in snapshot.proposal_source_bindings
            if item.proposal_id == expected.proposal_id
        }
        if (
            actual != expected
            or bindings != set(expected.source_ids)
            or receipt.proposal_ids != [expected.proposal_id]
        ):
            raise IntakeError("intake_commit_failed")

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise IntakeError("intake_commit_failed")
        return value.isoformat().replace("+00:00", "Z")


class _KnownInvalid(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _ProposalLineage:
    parent_proposal_id: str | None = None
    target_artifact_id: str | None = None
    target_artifact_revision: int | None = None
    source_ids: tuple[str, ...] = ()


def _by_id(records: Iterable[object], attribute: str, value: str):
    return next((item for item in records if getattr(item, attribute) == value), None)


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _derived_id(prefix: str, *parts: str) -> str:
    payload = "\0".join((prefix, *parts)).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:32]}"


def _source_artifact_ids(run_id: str, source_id: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"{run_id}\0{source_id}".encode("utf-8")).hexdigest()[:32]
    return f"SRC-CONTENT-{digest}", f"SRC-RAW-{digest}"


def _blob_workspace_path(digest: str) -> str:
    return f"briefloop.db.blobs/sha256/{digest[:2]}/{digest}"


def _artifact_pair(
    *,
    run_id: str,
    artifact_id: str,
    revision: int,
    path: str,
    artifact_format: str,
    sha256: str,
    size_bytes: int,
    producer_id: str,
    created_at: str,
    required: bool = False,
) -> tuple[ArtifactRecord, ArtifactRevision]:
    artifact = ArtifactRecord.model_validate(
        {
            "schema_version": ArtifactRecord.schema_id,
            "run_id": run_id,
            "artifact_id": artifact_id,
            "current_revision": revision,
            "status": "valid",
            "required": required,
            "path": path,
            "format": artifact_format,
        },
        strict=True,
    )
    record = ArtifactRevision.model_validate(
        {
            "schema_version": ArtifactRevision.schema_id,
            "run_id": run_id,
            "artifact_id": artifact_id,
            "revision": revision,
            "path": path,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "frozen": True,
            "producer_kind": "workflow_stage",
            "producer_id": producer_id,
            "created_at": created_at,
        },
        strict=True,
    )
    return artifact, record


def _completed_invocation(invocation: Invocation, completed_at: str) -> Invocation:
    payload = invocation.model_dump(mode="json", exclude_unset=False)
    payload.update(status="completed", completed_at=completed_at, failure_reason=None)
    return Invocation.model_validate(payload, strict=True)


def _failed_invocation(
    invocation: Invocation,
    completed_at: str,
    reason_code: str,
) -> Invocation:
    payload = invocation.model_dump(mode="json", exclude_unset=False)
    payload.update(
        status="failed",
        completed_at=completed_at,
        failure_reason=reason_code,
    )
    return Invocation.model_validate(payload, strict=True)


def _intake_event(
    *,
    event_id: str,
    run_id: str,
    event_type: str,
    transaction_id: str,
    invocation_id: str,
    request_fingerprint: str,
    outcome: str,
    created_at: str,
    stage_id: str,
    artifact_id: str | None = None,
    reason_code: str | None = None,
    source_id: str | None = None,
    proposal_id: str | None = None,
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
            "decision": outcome,
            "reason": "" if reason_code is None else reason_code,
            "metadata": {},
            "intake_binding": {
                "request_id": transaction_id,
                "request_fingerprint": request_fingerprint,
                "invocation_id": invocation_id,
                "outcome": outcome,
                "source_id": source_id,
                "proposal_id": proposal_id,
                "reason_code": reason_code,
            },
        },
        strict=True,
    )


def submit_source(
    workspace: str | os.PathLike[str],
    request_path: str | os.PathLike[str],
) -> IntakeResult:
    return IntakeService(workspace).submit_source(request_path)


def submit_proposal(
    workspace: str | os.PathLike[str],
    lane: str,
    request_path: str | os.PathLike[str],
) -> IntakeResult:
    return IntakeService(workspace).submit_proposal(lane, request_path)


__all__ = ["IntakeService", "submit_proposal", "submit_source"]
