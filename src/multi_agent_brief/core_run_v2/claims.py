"""Deterministic canonical Claim freeze for dormant fresh-v2 runs."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    AcceptedProposalRecord,
    ArtifactRecord,
    ArtifactRevision,
    CandidateClaimsProposal,
    ClaimDraftsProposal,
    ClaimFreezeRecord,
    ClaimFreezeRequest,
    ClaimRecord,
    ClaimSourceBinding,
    CoreRunEventBinding,
    EventEnvelope,
    ScreenedCandidatesProposal,
    StrictModel,
)
from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import parse_json_object

from .errors import CoreRunError, CoreRunResult, core_run_failure_result
from .integrity import RunIntegrityService
from .checkout import (
    prepare_checkout_effect,
    publish_checkout_effect,
    stage_checkout_effect,
)
from .lineage import classify_current_lineage
from .policy import (
    CLAIM_EPISTEMIC,
    derived_id,
    normalize_text,
    transaction_type_for,
)
from .verifier import CoreRunDomainVerifier, resolve_core_replay


_Clock = Callable[[], datetime]
_ProposalT = TypeVar("_ProposalT", bound=StrictModel)
class ClaimFreezeService:
    """Freeze complete canonical Claims and their deterministic Ledger bytes."""

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        clock: _Clock | None = None,
    ) -> None:
        try:
            self.workspace = Path(workspace).expanduser().resolve(strict=True)
            if not self.workspace.is_dir():
                raise ValueError
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise CoreRunError("core_run_request_invalid") from exc
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._verifier = CoreRunDomainVerifier()
        self._integrity = RunIntegrityService(self.workspace, clock=self._clock)

    def freeze(self, request: ClaimFreezeRequest) -> CoreRunResult:
        try:
            return self._freeze(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def _freeze(self, request: ClaimFreezeRequest) -> CoreRunResult:
        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
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
            lineage = classify_current_lineage(verified.snapshot)
            candidate_record, screened_record, drafts_record = (
                lineage.proposals.require_current_claim_chain()
            )
            if drafts_record.proposal_id != request.claim_drafts_proposal_id:
                raise CoreRunError("artifact_revision_conflict")
            drafts, drafts_bytes = _load_proposal(
                store,
                drafts_record,
                ClaimDraftsProposal,
            )
            if drafts.screened_candidates_proposal_id != screened_record.proposal_id:
                raise CoreRunError("claim_lineage_invalid")
            screened, screened_bytes = _load_proposal(
                store,
                screened_record,
                ScreenedCandidatesProposal,
            )
            if screened.candidate_claims_proposal_id != candidate_record.proposal_id:
                raise CoreRunError("claim_lineage_invalid")
            candidates, candidate_bytes = _load_proposal(
                store,
                candidate_record,
                CandidateClaimsProposal,
            )
            lineage.require_no_active_invocation("claim-ledger")
            lineage.require_stage_mutable("claim-ledger")
            if verified.snapshot.store_revision != request.expected_store_revision:
                raise CoreRunError("store_revision_conflict")
            if verified.snapshot.claim_freezes:
                raise CoreRunError("claim_lineage_invalid")
            stage = next(
                (
                    item
                    for item in verified.snapshot.stage_states
                    if item.stage_id == "claim-ledger"
                ),
                None,
            )
            if stage is None or stage.status != "ready":
                raise CoreRunError("stage_not_current")
            if (
                drafts_record.artifact_id
                != request.expected_claim_drafts_artifact.artifact_id
                or drafts_record.artifact_revision
                != request.expected_claim_drafts_artifact.revision
            ):
                raise CoreRunError("artifact_revision_conflict")
            drafts_artifact = next(
                (
                    item
                    for item in verified.snapshot.artifacts
                    if item.artifact_id == drafts_record.artifact_id
                ),
                None,
            )
            if (
                drafts_artifact is None
                or drafts_artifact.current_revision
                != drafts_record.artifact_revision
            ):
                raise CoreRunError("artifact_revision_conflict")
            ledger = next(
                (
                    item
                    for item in verified.snapshot.artifacts
                    if item.artifact_id == "claim_ledger"
                ),
                None,
            )
            if ledger is None or ledger.current_revision != request.expected_ledger_revision:
                raise CoreRunError("artifact_revision_conflict")
            revisions = {
                (item.artifact_id, item.revision): item
                for item in verified.snapshot.artifact_revisions
            }
            drafts_revision = revisions.get(
                (drafts_record.artifact_id, drafts_record.artifact_revision)
            )
            if drafts_revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            blocked = self._integrity.require_clean(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
                expected_store_revision=request.expected_store_revision,
                additional_revisions=(drafts_revision,),
            )
            if blocked is not None:
                return blocked
            sources = {item.source_id: item for item in verified.snapshot.sources}
            selected_sources = self._selected_source_ids(candidates, screened)
            transaction_revisions = {
                item.transaction_id: item.committed_revision
                for item in verified.snapshot.transactions
            }
            proposal_revision = transaction_revisions.get(
                drafts_record.accepted_transaction_id
            )
            if proposal_revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            canonical_drafts = sorted(
                drafts.drafts,
                key=lambda item: (
                    tuple(sorted(item.source_ids)),
                    normalize_text(item.statement),
                    normalize_text(item.evidence_text),
                    item.claim_type,
                    item.draft_id,
                ),
            )
            freeze_id = derived_id("FREEZE", request.request_id, fingerprint)
            now = _now(self._clock)
            claims: list[ClaimRecord] = []
            bindings: list[ClaimSourceBinding] = []
            ledger_claims: list[dict[str, object]] = []
            duplicate_statements: dict[str, list[str]] = defaultdict(list)
            for ordinal, draft in enumerate(canonical_drafts, start=1):
                source_ids = tuple(sorted(draft.source_ids))
                for source_id in source_ids:
                    source = sources.get(source_id)
                    source_revision = (
                        None
                        if source is None
                        else transaction_revisions.get(source.accepted_transaction_id)
                    )
                    if source is None or source.run_id != request.run_id:
                        raise CoreRunError("claim_lineage_invalid")
                    if not source.claims_eligible:
                        raise CoreRunError("claim_source_not_eligible")
                    if source_id not in selected_sources:
                        raise CoreRunError("claim_lineage_invalid")
                    if source_revision is None or source_revision >= proposal_revision:
                        raise CoreRunError("claim_source_order_invalid")
                claim_id = f"CL-{ordinal:04d}"
                statement = normalize_text(draft.statement)
                evidence = normalize_text(draft.evidence_text)
                duplicate_statements[statement.casefold()].append(draft.draft_id)
                claim = ClaimRecord.model_validate(
                    {
                        "schema_version": ClaimRecord.schema_id,
                        "run_id": request.run_id,
                        "claim_id": claim_id,
                        "freeze_id": freeze_id,
                        "ordinal": ordinal,
                        "claim_drafts_proposal_id": drafts.proposal_id,
                        "draft_id": draft.draft_id,
                        "statement": statement,
                        "evidence_text": evidence,
                        "primary_source_id": source_ids[0],
                        "claim_type": draft.claim_type,
                        "confidence": "medium",
                        "requires_audit": True,
                        "epistemic_type": CLAIM_EPISTEMIC[draft.claim_type],
                        "evidence_relation": "direct",
                        "applicability_reason": None,
                        "limitations": [],
                        "metadata": {"source_ids": list(source_ids)},
                        "created_at": now,
                        "accepted_transaction_id": request.request_id,
                    },
                    strict=True,
                )
                claims.append(claim)
                for position, source_id in enumerate(source_ids):
                    bindings.append(
                        ClaimSourceBinding.model_validate(
                            {
                                "schema_version": ClaimSourceBinding.schema_id,
                                "run_id": request.run_id,
                                "claim_id": claim_id,
                                "source_id": source_id,
                                "position": position,
                                "citation_role": (
                                    "primary" if position == 0 else "additional"
                                ),
                                "claim_drafts_proposal_id": drafts.proposal_id,
                                "accepted_transaction_id": request.request_id,
                            },
                            strict=True,
                        )
                    )
                primary = sources[source_ids[0]]
                locator = primary.locator.model_dump(mode="json", exclude_unset=False)
                ledger_claims.append(
                    {
                        "claim_id": claim_id,
                        "statement": statement,
                        "source_id": source_ids[0],
                        "evidence_text": evidence,
                        "source_url": locator.get("url", locator.get("path", "")),
                        "source_type": primary.retrieval_source_type,
                        "claim_type": draft.claim_type,
                        "confidence": "medium",
                        "requires_audit": True,
                        "created_by": "claim-ledger",
                        "used_in_sections": [],
                        "metadata": {
                            "source_ids": list(source_ids),
                            "source_title": primary.title,
                            "source_category": primary.source_category,
                            "published_at": primary.published_at,
                            "retrieved_at": primary.retrieved_at,
                            "underlying_evidence_type": primary.underlying_evidence_type,
                        },
                        "schema_version": "v2",
                        "epistemic_type": CLAIM_EPISTEMIC[draft.claim_type],
                        "evidence_relation": "direct",
                        "applicability_reason": "",
                        "limitations": [],
                    }
                )
            warnings = [
                {
                    "warning_type": "lexical_duplicate_statement",
                    "draft_ids": sorted(draft_ids),
                }
                for draft_ids in duplicate_statements.values()
                if len(draft_ids) > 1
            ]
            warnings.sort(key=lambda item: item["draft_ids"])
            ledger_bytes = canonical_json_bytes({"claims": ledger_claims}) + b"\n"
            ledger_digest = sha256_hex(ledger_bytes)
            ledger_revision_number = ledger.current_revision + 1
            event_id = derived_id("EVT-CLAIMS", request.request_id, fingerprint)
            updated_ledger = ArtifactRecord.model_validate(
                {
                    **ledger.model_dump(mode="json", exclude_unset=False),
                    "current_revision": ledger_revision_number,
                    "status": "valid",
                },
                strict=True,
            )
            ledger_revision = ArtifactRevision.model_validate(
                {
                    "schema_version": ArtifactRevision.schema_id,
                    "run_id": request.run_id,
                    "artifact_id": ledger.artifact_id,
                    "revision": ledger_revision_number,
                    "path": ledger.path,
                    "sha256": ledger_digest,
                    "size_bytes": len(ledger_bytes),
                    "frozen": True,
                    "producer_kind": "control_tool",
                    "producer_id": "claim-freeze-v2",
                    "created_at": now,
                },
                strict=True,
            )
            freeze = ClaimFreezeRecord.model_validate(
                {
                    "schema_version": ClaimFreezeRecord.schema_id,
                    "freeze_id": freeze_id,
                    "run_id": request.run_id,
                    "claim_drafts_proposal_id": drafts.proposal_id,
                    "screened_proposal_id": screened.proposal_id,
                    "candidate_proposal_id": candidates.proposal_id,
                    "claim_drafts_artifact": {
                        "artifact_id": drafts_record.artifact_id,
                        "revision": drafts_record.artifact_revision,
                    },
                    "claim_drafts_sha256": sha256_hex(drafts_bytes),
                    "ledger_artifact": {
                        "artifact_id": ledger.artifact_id,
                        "revision": ledger_revision_number,
                    },
                    "ledger_sha256": ledger_digest,
                    "normalization_policy": "sorted_sequential_v2",
                    "run_contract_fingerprint": verified.binding.contract_fingerprint,
                    "claim_count": len(claims),
                    "warnings": warnings,
                    "warning_count": len(warnings),
                    "frozen_at": now,
                    "freeze_event_id": event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            event = EventEnvelope.model_validate(
                {
                    "schema_version": EventEnvelope.schema_id,
                    "event_id": event_id,
                    "run_id": request.run_id,
                    "event_type": "claim_ledger_frozen",
                    "created_at": now,
                    "actor": "system",
                    "transaction_id": request.request_id,
                    "stage_id": "claim-ledger",
                    "artifact_id": ledger.artifact_id,
                    "decision": "continue",
                    "reason": "canonical Claims frozen",
                    "metadata": {},
                    "core_run_binding": CoreRunEventBinding(
                        request_id=request.request_id,
                        request_fingerprint=fingerprint,
                        effect_kind="claim_freeze",
                        primary_record_id=freeze_id,
                        outcome="committed",
                    ),
                },
                strict=True,
            )
            checkout = prepare_checkout_effect(
                workspace=self.workspace,
                snapshot=verified.snapshot,
                transaction_id=request.request_id,
                created_at=self._clock(),
                additional_revisions=(ledger_revision,),
            )
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("claim_freeze"),
                request.expected_store_revision,
            )
            unit.put_artifact(updated_ledger)
            unit.put_artifact_revision(ledger_revision, ledger_bytes)
            for claim in claims:
                unit.put_claim(claim)
            for binding in bindings:
                unit.put_claim_source_binding(binding)
            unit.put_claim_freeze(freeze)
            unit.append_event(event)
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: self._verifier.verify(
                    store,
                    request.run_id,
                )
            )
            published, _warnings = publish_checkout_effect(
                workspace=self.workspace,
                store=store,
                prepared=checkout,
            )
            if not published:
                return CoreRunResult(
                    status="commit_outcome_unknown",
                    error_code="commit_outcome_unknown",
                )
            return CoreRunResult(
                status="committed",
                receipt=receipt,
                primary_record_id=freeze_id,
            )

    @staticmethod
    def _selected_source_ids(
        candidates: CandidateClaimsProposal,
        screened: ScreenedCandidatesProposal,
    ) -> set[str]:
        candidate_sources = {
            item.candidate_id: item.source_id for item in candidates.candidates
        }
        decisions = {item.candidate_id: item.decision for item in screened.decisions}
        if set(decisions) != set(candidate_sources):
            raise CoreRunError("claim_lineage_invalid")
        return {
            candidate_sources[candidate_id]
            for candidate_id, decision in decisions.items()
            if decision == "selected"
        }

    def _open_store(self) -> SQLiteControlStore:
        try:
            return SQLiteControlStore.open(self.workspace / "briefloop.db", clock=self._clock)
        except ControlStoreError as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc


def _accepted(
    records: tuple[AcceptedProposalRecord, ...],
    proposal_id: str,
    kind: str,
) -> AcceptedProposalRecord:
    record = next((item for item in records if item.proposal_id == proposal_id), None)
    if record is None or record.proposal_kind != kind:
        raise CoreRunError("claim_lineage_invalid")
    return record


def _load_proposal(
    store: SQLiteControlStore,
    record: AcceptedProposalRecord,
    model_type: type[_ProposalT],
) -> tuple[_ProposalT, bytes]:
    try:
        content = store.read_artifact_revision_bytes(
            record.run_id,
            record.artifact_id,
            record.artifact_revision,
        )
        model = model_type.model_validate(parse_json_object(content), strict=True)
    except (ControlStoreError, IntakeError, ValidationError) as exc:
        raise CoreRunError("control_store_integrity_invalid") from exc
    if (
        sha256_hex(content) != record.proposal_sha256
        or model.proposal_id != record.proposal_id
        or model.run_id != record.run_id
    ):
        raise CoreRunError("control_store_integrity_invalid")
    return model, content


def _now(clock: _Clock) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CoreRunError("core_run_request_invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["ClaimFreezeService"]
