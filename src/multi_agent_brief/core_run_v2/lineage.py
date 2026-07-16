"""Pure current-lineage and terminal-consumption classification for core v2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    AcceptedProposalRecord,
    ArtifactRevision,
    AuditProposal,
    AuditReportArtifact,
    GateEvaluationRecord,
    OwnedArtifactSubmissionRecord,
)
from multi_agent_brief.control_store import ControlStoreError, ControlStoreSnapshot
from multi_agent_brief.control_store.serialization import (
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import parse_json_object
from multi_agent_brief.quality_gates.contract import GATE_IDS

from .errors import CoreRunError


class LineageState(str, Enum):
    """The derived mutability state of one current lane."""

    OPEN = "open"
    RESERVED = "reserved"
    MUTABLE = "mutable"
    SEALED = "sealed"
    INVALID = "invalid"


_PROPOSAL_ARTIFACTS = {
    "candidate": "candidate_claims",
    "screened": "screened_candidates",
    "claim_drafts": "claim_drafts",
    "audit": "audit_proposal",
}


@dataclass(frozen=True)
class CurrentProposalLineage:
    candidate: AcceptedProposalRecord | None
    screened: AcceptedProposalRecord | None
    claim_drafts: AcceptedProposalRecord | None
    audit: AcceptedProposalRecord | None
    state_by_kind: dict[str, LineageState]

    def current(self, kind: str) -> AcceptedProposalRecord:
        value = getattr(self, kind, None)
        if not isinstance(value, AcceptedProposalRecord):
            raise CoreRunError("claim_lineage_invalid")
        return value

    def require_current_claim_chain(
        self,
        *,
        claim_drafts_proposal_id: str | None = None,
    ) -> tuple[AcceptedProposalRecord, AcceptedProposalRecord, AcceptedProposalRecord]:
        candidate = self.current("candidate")
        screened = self.current("screened")
        drafts = self.current("claim_drafts")
        if (
            screened.parent_proposal_id != candidate.proposal_id
            or drafts.parent_proposal_id != screened.proposal_id
            or (
                claim_drafts_proposal_id is not None
                and drafts.proposal_id != claim_drafts_proposal_id
            )
        ):
            raise CoreRunError("claim_lineage_invalid")
        return candidate, screened, drafts


@dataclass(frozen=True)
class CurrentGateBatch:
    gate_batch_id: str
    report_artifact_id: str
    report_artifact_revision: int
    evaluations: tuple[GateEvaluationRecord, ...]
    owning_transaction_id: str
    committed_revision: int


@dataclass(frozen=True)
class CurrentAuditPromotion:
    """One structurally valid current report revision and its source lineage."""

    proposal_record: AcceptedProposalRecord
    proposal: AuditProposal
    brief_revision: ArtifactRevision
    report_revision: ArtifactRevision
    submission: OwnedArtifactSubmissionRecord
    canonical_report_bytes: bytes
    is_current_lineage: bool
    owning_transaction_id: str
    committed_revision: int


@dataclass(frozen=True)
class LineageClassification:
    """One derived view over exactly one structurally verified snapshot."""

    proposals: CurrentProposalLineage
    active_invocations_by_stage: dict[str, tuple[str, ...]]
    sealed_stages: frozenset[str]
    claim_freeze_sealed: bool
    current_submissions: dict[str, OwnedArtifactSubmissionRecord]
    current_gate_batch: CurrentGateBatch | None

    def stage_state(self, stage_id: str) -> LineageState:
        if stage_id in self.sealed_stages or (
            stage_id == "claim-ledger" and self.claim_freeze_sealed
        ):
            return LineageState.SEALED
        if self.active_invocations_by_stage.get(stage_id):
            return LineageState.RESERVED
        return LineageState.MUTABLE

    def require_stage_mutable(
        self,
        stage_id: str,
        *,
        allow_reservation: str | None = None,
    ) -> None:
        if self.stage_state(stage_id) == LineageState.SEALED:
            raise CoreRunError("stage_not_current")
        active = self.active_invocations_by_stage.get(stage_id, ())
        if active and (allow_reservation is None or active != (allow_reservation,)):
            raise CoreRunError("invocation_owner_mismatch")

    def require_no_active_invocation(self, stage_id: str) -> None:
        if self.active_invocations_by_stage.get(stage_id):
            raise CoreRunError("claim_lineage_invalid")

    def current_proposal(self, kind: str) -> AcceptedProposalRecord:
        return self.proposals.current(kind)

    def require_current_audit(
        self,
        *,
        proposal_id: str,
        target_artifact_id: str,
        target_artifact_revision: int,
    ) -> AcceptedProposalRecord:
        proposal = self.proposals.current("audit")
        if (
            proposal.proposal_id != proposal_id
            or proposal.target_artifact_id != target_artifact_id
            or proposal.target_artifact_revision != target_artifact_revision
        ):
            raise CoreRunError("artifact_revision_conflict")
        return proposal


def classify_current_lineage(snapshot: ControlStoreSnapshot) -> LineageClassification:
    """Classify current lineage without creating a new persisted truth."""

    artifacts = {item.artifact_id: item for item in snapshot.artifacts}
    proposals_by_kind: dict[str, AcceptedProposalRecord | None] = {}
    states: dict[str, LineageState] = {}
    for kind, artifact_id in _PROPOSAL_ARTIFACTS.items():
        artifact = artifacts.get(artifact_id)
        if artifact is None:
            proposals_by_kind[kind] = None
            states[kind] = LineageState.OPEN
            continue
        current = [
            item
            for item in snapshot.accepted_proposals
            if item.proposal_kind == kind
            and item.artifact_id == artifact_id
            and item.artifact_revision == artifact.current_revision
        ]
        if artifact.current_revision == 0:
            if current:
                raise CoreRunError("control_store_integrity_invalid")
            proposals_by_kind[kind] = None
            states[kind] = LineageState.OPEN
        elif len(current) == 1:
            proposals_by_kind[kind] = current[0]
            states[kind] = LineageState.MUTABLE
        else:
            raise CoreRunError("control_store_integrity_invalid")

    candidate = proposals_by_kind["candidate"]
    screened = proposals_by_kind["screened"]
    claim_drafts = proposals_by_kind["claim_drafts"]
    if (
        screened is not None
        and candidate is not None
        and screened.parent_proposal_id != candidate.proposal_id
    ):
        states["screened"] = LineageState.INVALID
    if (
        claim_drafts is not None
        and screened is not None
        and claim_drafts.parent_proposal_id != screened.proposal_id
    ):
        states["claim_drafts"] = LineageState.INVALID
    current_proposals = CurrentProposalLineage(
        candidate=candidate,
        screened=screened,
        claim_drafts=claim_drafts,
        audit=proposals_by_kind["audit"],
        state_by_kind=states,
    )

    invocation_stages = _invocation_stage_map(snapshot)
    active: dict[str, list[str]] = {}
    for invocation in snapshot.invocations:
        stage_id = invocation_stages.get(invocation.invocation_id)
        if stage_id is None:
            raise CoreRunError("control_store_integrity_invalid")
        if invocation.status == "active":
            active.setdefault(stage_id, []).append(invocation.invocation_id)
    if any(len(values) > 1 for values in active.values()):
        raise CoreRunError("control_store_integrity_invalid")

    sealed_stages = frozenset(
        item.stage_id for item in snapshot.stage_states if item.status == "complete"
    )
    current_submissions: dict[str, OwnedArtifactSubmissionRecord] = {}
    for artifact_id, artifact in artifacts.items():
        if artifact.current_revision <= 0:
            continue
        values = [
            item
            for item in snapshot.owned_artifact_submissions
            if item.artifact_id == artifact_id
            and item.artifact_revision == artifact.current_revision
        ]
        if len(values) == 1:
            current_submissions[artifact_id] = values[0]
        elif len(values) > 1:
            raise CoreRunError("control_store_integrity_invalid")

    return LineageClassification(
        proposals=current_proposals,
        active_invocations_by_stage={
            key: tuple(values) for key, values in active.items()
        },
        sealed_stages=sealed_stages,
        claim_freeze_sealed=bool(snapshot.claim_freezes),
        current_submissions=current_submissions,
        current_gate_batch=_current_gate_batch(snapshot),
    )


def canonical_audit_report_bytes(
    *,
    run_id: str,
    proposal_record: AcceptedProposalRecord,
    proposal_bytes: bytes,
    brief_revision: ArtifactRevision,
) -> tuple[AuditProposal, bytes]:
    """Validate one proposal record and derive its byte-exact report projection."""

    try:
        proposal = AuditProposal.model_validate(
            parse_json_object(proposal_bytes),
            strict=True,
        )
    except (IntakeError, ValidationError) as exc:
        raise CoreRunError("control_store_integrity_invalid") from exc
    if (
        proposal_record.run_id != run_id
        or proposal_record.proposal_kind != "audit"
        or sha256_hex(proposal_bytes) != proposal_record.proposal_sha256
        or proposal.proposal_id != proposal_record.proposal_id
        or proposal.run_id != run_id
        or proposal.artifact_id != brief_revision.artifact_id
        or proposal.artifact_revision != brief_revision.revision
        or proposal_record.target_artifact_id != brief_revision.artifact_id
        or proposal_record.target_artifact_revision != brief_revision.revision
    ):
        raise CoreRunError("control_store_integrity_invalid")
    report = AuditReportArtifact.model_validate(
        {
            "schema_version": AuditReportArtifact.schema_id,
            "run_id": run_id,
            "audit_proposal_id": proposal.proposal_id,
            "target_artifact_id": brief_revision.artifact_id,
            "target_artifact_revision": brief_revision.revision,
            "target_artifact_sha256": brief_revision.sha256,
            "decision": proposal.decision,
            "findings": [
                item.model_dump(mode="json", exclude_unset=False)
                for item in proposal.findings
            ],
        },
        strict=True,
    )
    return proposal, canonical_json_bytes(report.model_dump(mode="json")) + b"\n"


def classify_current_audit_promotion(
    snapshot: ControlStoreSnapshot,
    read_artifact_revision_bytes: Callable[[str, str, int], bytes],
) -> CurrentAuditPromotion | None:
    """Validate the current report's immutable promotion graph.

    A newer accepted proposal or brief may make this graph stale without making
    its historical records invalid. Consumers must additionally require
    ``is_current_lineage`` before using the report.
    """

    artifacts = {item.artifact_id: item for item in snapshot.artifacts}
    revisions = {
        (item.artifact_id, item.revision): item
        for item in snapshot.artifact_revisions
    }
    report_artifact = artifacts.get("audit_report")
    if report_artifact is None or report_artifact.current_revision == 0:
        return None
    report_revision = revisions.get(
        (report_artifact.artifact_id, report_artifact.current_revision)
    )
    submissions = [
        item
        for item in snapshot.owned_artifact_submissions
        if item.artifact_id == report_artifact.artifact_id
        and item.artifact_revision == report_artifact.current_revision
    ]
    if report_revision is None or len(submissions) != 1:
        raise CoreRunError("control_store_integrity_invalid")
    submission = submissions[0]
    proposals = [
        item
        for item in snapshot.accepted_proposals
        if item.proposal_id == submission.source_proposal_id
        and item.proposal_kind == "audit"
    ]
    parent = submission.parent_artifact
    if len(proposals) != 1 or parent is None:
        raise CoreRunError("control_store_integrity_invalid")
    proposal_record = proposals[0]
    brief_revision = revisions.get((parent.artifact_id, parent.revision))
    if (
        brief_revision is None
        or brief_revision.artifact_id != "audited_brief"
        or submission.invocation_id != proposal_record.invocation_id
        or submission.producer_tool_id != "audit-proposal-promoter-v2"
        or submission.source_proposal_id != proposal_record.proposal_id
        or submission.artifact_sha256 != report_revision.sha256
        or submission.canonical_workspace_path != report_revision.path
        or proposal_record.target_artifact_id != brief_revision.artifact_id
        or proposal_record.target_artifact_revision != brief_revision.revision
    ):
        raise CoreRunError("control_store_integrity_invalid")
    try:
        proposal_bytes = read_artifact_revision_bytes(
            snapshot.run.run_id,
            proposal_record.artifact_id,
            proposal_record.artifact_revision,
        )
        report_bytes = read_artifact_revision_bytes(
            snapshot.run.run_id,
            report_revision.artifact_id,
            report_revision.revision,
        )
    except ControlStoreError as exc:
        raise CoreRunError("control_store_integrity_invalid") from exc
    proposal, expected_report_bytes = canonical_audit_report_bytes(
        run_id=snapshot.run.run_id,
        proposal_record=proposal_record,
        proposal_bytes=proposal_bytes,
        brief_revision=brief_revision,
    )
    if (
        sha256_hex(report_bytes) != report_revision.sha256
        or report_bytes != expected_report_bytes
    ):
        raise CoreRunError("control_store_integrity_invalid")

    lineage = classify_current_lineage(snapshot)
    current_audit = lineage.proposals.audit
    current_brief = artifacts.get("audited_brief")
    is_current = (
        current_audit == proposal_record
        and current_brief is not None
        and current_brief.artifact_id == brief_revision.artifact_id
        and current_brief.current_revision == brief_revision.revision
    )
    owning_transaction_id = submission.accepted_transaction_id
    receipts = [
        item
        for item in snapshot.transactions
        if item.transaction_id == owning_transaction_id
    ]
    if len(receipts) != 1:
        raise CoreRunError("control_store_integrity_invalid")
    return CurrentAuditPromotion(
        proposal_record=proposal_record,
        proposal=proposal,
        brief_revision=brief_revision,
        report_revision=report_revision,
        submission=submission,
        canonical_report_bytes=expected_report_bytes,
        is_current_lineage=is_current,
        owning_transaction_id=owning_transaction_id,
        committed_revision=receipts[0].committed_revision,
    )


def require_current_gate_after_audit_promotion(
    *,
    audit_promotion: CurrentAuditPromotion,
    gate_batch: CurrentGateBatch | None,
) -> CurrentGateBatch:
    """Require a current Gate batch committed after audit promotion.

    Historical Gate batches remain valid records. Only the current batch whose
    owning transaction committed strictly after the current byte-exact audit
    promotion may be consumed by Auditor completion.
    """

    if (
        gate_batch is None
        or gate_batch.committed_revision <= audit_promotion.committed_revision
    ):
        raise CoreRunError("stage_gate_binding_invalid")
    return gate_batch


def verify_no_post_seal_records(snapshot: ControlStoreSnapshot) -> None:
    """Reject forged records accepted after their lane's terminal transaction."""

    transaction_revisions = {
        item.transaction_id: item.committed_revision for item in snapshot.transactions
    }
    seal_revision_by_stage: dict[str, int] = {}
    for transition in snapshot.stage_transitions:
        if transition.transition_kind not in {"complete", "satisfied_by_topology"}:
            continue
        revision = transaction_revisions.get(transition.accepted_transaction_id)
        if revision is None:
            raise CoreRunError("control_store_integrity_invalid")
        existing = seal_revision_by_stage.get(transition.stage_id)
        seal_revision_by_stage[transition.stage_id] = (
            revision if existing is None else min(existing, revision)
        )
    for freeze in snapshot.claim_freezes:
        revision = transaction_revisions.get(freeze.accepted_transaction_id)
        if revision is None:
            raise CoreRunError("control_store_integrity_invalid")
        existing = seal_revision_by_stage.get("claim-ledger")
        seal_revision_by_stage["claim-ledger"] = (
            revision if existing is None else min(existing, revision)
        )

    invocation_stages = _invocation_stage_map(snapshot)
    for invocation in snapshot.invocations:
        stage_id = invocation_stages[invocation.invocation_id]
        seal = seal_revision_by_stage.get(stage_id)
        accepted = _record_revision(snapshot, invocation.invocation_id)
        if seal is not None and (
            invocation.status == "active" or accepted >= seal
        ):
            raise CoreRunError("control_store_integrity_invalid")
    for source in snapshot.sources:
        stage_id = invocation_stages.get(source.invocation_id)
        seal = seal_revision_by_stage.get(stage_id) if stage_id is not None else None
        accepted = transaction_revisions.get(source.accepted_transaction_id)
        if (
            stage_id is None
            or accepted is None
            or (seal is not None and accepted >= seal)
        ):
            raise CoreRunError("control_store_integrity_invalid")
    for proposal in snapshot.accepted_proposals:
        seal = seal_revision_by_stage.get(proposal.owner_stage_id)
        accepted = transaction_revisions.get(proposal.accepted_transaction_id)
        if accepted is None or (seal is not None and accepted >= seal):
            raise CoreRunError("control_store_integrity_invalid")
    for submission in snapshot.owned_artifact_submissions:
        seal = seal_revision_by_stage.get(submission.owner_stage_id)
        accepted = transaction_revisions.get(submission.accepted_transaction_id)
        if accepted is None or (seal is not None and accepted >= seal):
            raise CoreRunError("control_store_integrity_invalid")
    for evaluation in snapshot.gate_evaluations:
        seal = seal_revision_by_stage.get(evaluation.stage_id)
        accepted = transaction_revisions.get(evaluation.accepted_transaction_id)
        if accepted is None or (seal is not None and accepted >= seal):
            raise CoreRunError("control_store_integrity_invalid")


def _invocation_stage_map(snapshot: ControlStoreSnapshot) -> dict[str, str]:
    result: dict[str, str] = {}
    for event in snapshot.events:
        binding = event.core_run_binding
        if binding is None or binding.effect_kind != "invocation_start":
            continue
        if event.stage_id is None or binding.primary_record_id in result:
            raise CoreRunError("control_store_integrity_invalid")
        result[binding.primary_record_id] = event.stage_id
    if set(result) != {item.invocation_id for item in snapshot.invocations}:
        raise CoreRunError("control_store_integrity_invalid")
    return result


def _record_revision(snapshot: ControlStoreSnapshot, invocation_id: str) -> int:
    transaction_revisions = {
        item.transaction_id: item.committed_revision for item in snapshot.transactions
    }
    for event in snapshot.events:
        binding = event.core_run_binding
        if (
            binding is not None
            and binding.effect_kind == "invocation_start"
            and binding.primary_record_id == invocation_id
            and event.transaction_id in transaction_revisions
        ):
            return transaction_revisions[event.transaction_id]
    raise CoreRunError("control_store_integrity_invalid")


def _current_gate_batch(snapshot: ControlStoreSnapshot) -> CurrentGateBatch | None:
    artifacts = {item.artifact_id: item for item in snapshot.artifacts}
    report = artifacts.get("auditor_quality_gate_report")
    if report is None or report.current_revision == 0:
        return None
    current = [
        item
        for item in snapshot.gate_evaluations
        if item.report_artifact.artifact_id == report.artifact_id
        and item.report_artifact.revision == report.current_revision
    ]
    if not current:
        return None
    batches = {item.gate_batch_id for item in current}
    if len(batches) != 1 or {item.gate_id for item in current} != set(GATE_IDS):
        raise CoreRunError("control_store_integrity_invalid")
    batch_id = next(iter(batches))
    owning_transaction_ids = {
        item.accepted_transaction_id for item in current
    }
    if len(owning_transaction_ids) != 1:
        raise CoreRunError("control_store_integrity_invalid")
    owning_transaction_id = next(iter(owning_transaction_ids))
    receipts = [
        item
        for item in snapshot.transactions
        if item.transaction_id == owning_transaction_id
    ]
    if len(receipts) != 1:
        raise CoreRunError("control_store_integrity_invalid")
    return CurrentGateBatch(
        gate_batch_id=batch_id,
        report_artifact_id=report.artifact_id,
        report_artifact_revision=report.current_revision,
        evaluations=tuple(sorted(current, key=lambda item: item.gate_id)),
        owning_transaction_id=owning_transaction_id,
        committed_revision=receipts[0].committed_revision,
    )


__all__ = [
    "CurrentAuditPromotion",
    "CurrentGateBatch",
    "CurrentProposalLineage",
    "LineageClassification",
    "LineageState",
    "canonical_audit_report_bytes",
    "classify_current_audit_promotion",
    "classify_current_lineage",
    "require_current_gate_after_audit_promotion",
    "verify_no_post_seal_records",
]
