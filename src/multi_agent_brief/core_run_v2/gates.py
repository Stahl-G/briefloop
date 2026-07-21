"""Store-native deterministic non-final Gate evaluation for fresh-v2."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable, Literal, TypeVar

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    AcceptedProposalRecord,
    ArtifactRecord,
    ArtifactRevision,
    CandidateClaimsProposal,
    CoreRunEventBinding,
    EventEnvelope,
    GateArtifactBinding,
    GateCheckRequest,
    GateEvaluationRecord,
    GateFindingRecord,
    RunContractBinding,
    ScreenedCandidatesProposal,
    StrictModel,
)
from multi_agent_brief.control_store import (
    ControlStoreError,
    ControlStoreSnapshot,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import parse_json_object
from multi_agent_brief.quality_gates.contract import GATE_IDS
from multi_agent_brief.quality_gates.evaluation import (
    evaluate_quality_gate_findings_preloaded,
)

from .errors import CoreRunError, CoreRunResult, core_run_failure_result
from .integrity import RunIntegrityService
from .checkout import (
    prepare_checkout_effect,
    publish_checkout_effect,
    stage_checkout_effect,
)
from .lineage import classify_current_audit_promotion, classify_current_lineage
from .policy import derived_id, transaction_type_for
from .verifier import CoreRunDomainVerifier, resolve_core_replay


_Clock = Callable[[], datetime]
_ProposalT = TypeVar("_ProposalT", bound=StrictModel)
EVALUATOR_IMPLEMENTATION = "core-v2-preloaded-quality-gates"
EVALUATOR_VERSION = "2"


class GateEvaluationService:
    """Evaluate one complete Auditor Gate batch from exact Store revisions."""

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

    def evaluate(self, request: GateCheckRequest) -> CoreRunResult:
        try:
            return self._evaluate(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def _evaluate(self, request: GateCheckRequest) -> CoreRunResult:
        replay_fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        with self._open_store() as store:
            replay = resolve_core_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=replay_fingerprint,
            )
            if replay is not None:
                return replay
            verified = self._verifier.verify(store, request.run_id)
            stage = next(
                (
                    item
                    for item in verified.snapshot.stage_states
                    if item.stage_id == request.stage_id
                ),
                None,
            )
            if stage is None or stage.status != "ready":
                raise CoreRunError("stage_not_current")
            artifacts = {item.artifact_id: item for item in verified.snapshot.artifacts}
            revisions = {
                (item.artifact_id, item.revision): item
                for item in verified.snapshot.artifact_revisions
            }
            explicit = {
                (item.artifact_id, item.revision): revisions.get(
                    (item.artifact_id, item.revision)
                )
                for item in request.expected_input_artifacts
            }
            if any(item is None for item in explicit.values()):
                raise CoreRunError("gate_input_binding_invalid")
            if request.stage_id == "auditor":
                classify_current_lineage(verified.snapshot).require_stage_mutable(
                    "auditor"
                )
            report_artifact_id = f"{request.stage_id}_quality_gate_report"
            report_record = artifacts.get(report_artifact_id)
            if report_record is None:
                contract = next(
                    (
                        item
                        for item in verified.artifacts
                        if item["artifact_id"] == report_artifact_id
                    ),
                    None,
                )
                if contract is None or request.expected_report_artifact_revision != 0:
                    raise CoreRunError("control_store_integrity_invalid")
                report_record = ArtifactRecord.model_validate(
                    {
                        "schema_version": ArtifactRecord.schema_id,
                        "run_id": request.run_id,
                        "artifact_id": report_artifact_id,
                        "current_revision": 0,
                        "status": "expected",
                        "required": bool(contract["required"]),
                        "path": str(contract["path"]),
                        "format": str(contract["format"]),
                    },
                    strict=True,
                )
            required: list[tuple[ArtifactRevision, str]] = []
            bindings: list[GateArtifactBinding] = []
            for position, reference in enumerate(request.expected_input_artifacts):
                revision = explicit[(reference.artifact_id, reference.revision)]
                assert revision is not None
                usage = _gate_input_usage(request.stage_id, reference.artifact_id)
                required.append((revision, usage))
                bindings.append(
                    GateArtifactBinding.model_validate(
                        {
                            "schema_version": GateArtifactBinding.schema_id,
                            "run_id": request.run_id,
                            "evaluation_id": "GATE-TEMPLATE",
                            "position": position,
                            "artifact_id": revision.artifact_id,
                            "artifact_revision": revision.revision,
                            "artifact_sha256": revision.sha256,
                            "usage": usage,
                            "accepted_transaction_id": request.request_id,
                        },
                        strict=True,
                    )
                )
            input_hashes = [
                {
                    "artifact_id": item.artifact_id,
                    "revision": item.revision,
                    "sha256": item.sha256,
                    "usage": usage,
                }
                for item, usage in required
            ]
            fingerprint = replay_fingerprint
            if verified.snapshot.store_revision != request.expected_store_revision:
                raise CoreRunError("store_revision_conflict")
            if (
                report_record.current_revision
                != request.expected_report_artifact_revision
            ):
                raise CoreRunError("artifact_revision_conflict")
            blocked = self._integrity.require_clean(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
                expected_store_revision=request.expected_store_revision,
                additional_revisions=tuple(item for item, _usage in required),
            )
            if blocked is not None:
                return blocked
            try:
                gate_outcomes = _replay_gate_outcomes(
                    store,
                    verified.snapshot,
                    verified.binding,
                    stage_id=request.stage_id,
                    stages=tuple(dict(item) for item in verified.stages),
                    artifacts=tuple(dict(item) for item in verified.artifacts),
                    artifact_bindings=tuple(bindings),
                )
            except (
                ControlStoreError,
                IntakeError,
                KeyError,
                RuntimeError,
                TypeError,
                UnicodeDecodeError,
                ValidationError,
                ValueError,
            ) as exc:
                raise CoreRunError("gate_input_binding_invalid") from exc
            now = _now(self._clock)
            batch_id = derived_id("GATE-BATCH", request.request_id, fingerprint)
            event_id = derived_id("EVT-GATES", request.request_id, fingerprint)
            report_revision_number = report_record.current_revision + 1
            evaluations: list[GateEvaluationRecord] = []
            findings: list[GateFindingRecord] = []
            finding_projection: list[dict[str, object]] = []
            policy_version = (
                f"{verified.binding.policy_pack_name}:"
                f"{verified.binding.policy_pack_sha256[:16]}"
            )
            for gate_id in sorted(GATE_IDS):
                forced_status, raw_findings = gate_outcomes[gate_id]
                evaluation_id = derived_id("GATE", batch_id, gate_id)
                finding_ids: list[str] = []
                for position, raw in enumerate(raw_findings, start=1):
                    finding = _gate_finding_record(
                        run_id=request.run_id,
                        evaluation_id=evaluation_id,
                        gate_id=gate_id,
                        position=position,
                        raw=raw,
                        accepted_transaction_id=request.request_id,
                    )
                    finding_ids.append(finding.finding_id)
                    findings.append(finding)
                    finding_projection.append(
                        finding.model_dump(mode="json", exclude_unset=False)
                    )
                blocking = any(
                    item.blocking_level == "blocking"
                    for item in findings
                    if item.evaluation_id == evaluation_id
                )
                status = (
                    forced_status
                    if forced_status is not None
                    else ("fail" if blocking else ("warning" if finding_ids else "pass"))
                )
                evaluations.append(
                    GateEvaluationRecord.model_validate(
                        {
                            "schema_version": GateEvaluationRecord.schema_id,
                            "evaluation_id": evaluation_id,
                            "gate_batch_id": batch_id,
                            "run_id": request.run_id,
                            "stage_id": request.stage_id,
                            "gate_id": gate_id,
                            "policy_version": policy_version,
                            "run_contract_fingerprint": verified.binding.contract_fingerprint,
                            "status": status,
                            "blocking": blocking,
                            "finding_ids": finding_ids,
                            "checked_at": now,
                            "producer_implementation": EVALUATOR_IMPLEMENTATION,
                            "producer_version": EVALUATOR_VERSION,
                            "report_artifact": {
                                "artifact_id": report_record.artifact_id,
                                "revision": report_revision_number,
                            },
                            "evaluation_event_id": event_id,
                            "accepted_transaction_id": request.request_id,
                            "request_fingerprint": fingerprint,
                        },
                        strict=True,
                    )
                )
            report_payload = {
                "schema_version": "briefloop.gate_report.v2",
                "run_id": request.run_id,
                "stage_id": request.stage_id,
                "gate_batch_id": batch_id,
                "policy_version": policy_version,
                "run_contract_fingerprint": verified.binding.contract_fingerprint,
                "input_artifacts": input_hashes,
                "evaluations": [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in evaluations
                ],
                "findings": finding_projection,
            }
            report_bytes = canonical_json_bytes(report_payload) + b"\n"
            digest = sha256_hex(report_bytes)
            updated_report = ArtifactRecord.model_validate(
                {
                    **report_record.model_dump(mode="json", exclude_unset=False),
                    "current_revision": report_revision_number,
                    "status": "valid",
                },
                strict=True,
            )
            report_revision = ArtifactRevision.model_validate(
                {
                    "schema_version": ArtifactRevision.schema_id,
                    "run_id": request.run_id,
                    "artifact_id": report_record.artifact_id,
                    "revision": report_revision_number,
                    "path": report_record.path,
                    "sha256": digest,
                    "size_bytes": len(report_bytes),
                    "frozen": True,
                    "producer_kind": "control_tool",
                    "producer_id": EVALUATOR_IMPLEMENTATION,
                    "created_at": now,
                },
                strict=True,
            )
            event = EventEnvelope.model_validate(
                {
                    "schema_version": EventEnvelope.schema_id,
                    "event_id": event_id,
                    "run_id": request.run_id,
                    "event_type": "quality_gate_checked",
                    "created_at": now,
                    "actor": "system",
                    "transaction_id": request.request_id,
                    "stage_id": request.stage_id,
                    "artifact_id": report_record.artifact_id,
                    "decision": (
                        "block" if any(item.blocking for item in evaluations) else "continue"
                    ),
                    "reason": "preloaded deterministic Gate batch evaluated",
                    "metadata": {},
                    "core_run_binding": CoreRunEventBinding(
                        request_id=request.request_id,
                        request_fingerprint=fingerprint,
                        effect_kind="gate_evaluation",
                        primary_record_id=batch_id,
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
                additional_revisions=(report_revision,),
            )
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("gate_evaluation"),
                request.expected_store_revision,
            )
            unit.put_artifact(updated_report)
            unit.put_artifact_revision(report_revision, report_bytes)
            for evaluation in evaluations:
                unit.put_gate_evaluation(evaluation)
                for position, (revision, usage) in enumerate(required):
                    unit.put_gate_artifact_binding(
                        GateArtifactBinding.model_validate(
                            {
                                "schema_version": GateArtifactBinding.schema_id,
                                "run_id": request.run_id,
                                "evaluation_id": evaluation.evaluation_id,
                                "position": position,
                                "artifact_id": revision.artifact_id,
                                "artifact_revision": revision.revision,
                                "artifact_sha256": revision.sha256,
                                "usage": usage,
                                "accepted_transaction_id": request.request_id,
                            },
                            strict=True,
                        )
                    )
            for finding in findings:
                unit.put_gate_finding(finding)
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
                primary_record_id=batch_id,
            )

    def _open_store(self) -> SQLiteControlStore:
        try:
            return SQLiteControlStore.open(self.workspace / "briefloop.db", clock=self._clock)
        except ControlStoreError as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc


def _one_proposal(
    records: tuple[AcceptedProposalRecord, ...],
    kind: str,
    *,
    current_revision: int,
) -> AcceptedProposalRecord:
    selected = [
        item
        for item in records
        if item.proposal_kind == kind
        and item.artifact_revision == current_revision
    ]
    if len(selected) != 1:
        raise CoreRunError("gate_input_binding_invalid")
    return selected[0]


def _gate_input_usage(
    stage_id: Literal["auditor", "finalize"], artifact_id: str
) -> str:
    if artifact_id in {"candidate_claims", "screened_candidates"}:
        return "screened_candidates"
    if artifact_id == "claim_ledger":
        return "ledger"
    if stage_id == "auditor":
        values = {
            "audited_brief": "brief",
            "analyst_draft_snapshot": "analyst_snapshot",
        }
    else:
        values = {
            "audit_report": "audit_report",
            "reader_brief": "reader_artifact",
            "reader_brief_docx": "reader_artifact",
        }
    try:
        return values[artifact_id]
    except KeyError as exc:
        raise CoreRunError("gate_input_binding_invalid") from exc


def _gate_finding_record(
    *,
    run_id: str,
    evaluation_id: str,
    gate_id: str,
    position: int,
    raw: dict[str, object],
    accepted_transaction_id: str,
) -> GateFindingRecord:
    finding_id = derived_id(
        "GATE-FINDING",
        evaluation_id,
        str(position),
        str(raw.get("finding_type") or "finding"),
    )
    return GateFindingRecord.model_validate(
        {
            "schema_version": GateFindingRecord.schema_id,
            "run_id": run_id,
            "evaluation_id": evaluation_id,
            "finding_id": finding_id,
            "gate_id": gate_id,
            "finding_type": str(raw.get("finding_type") or "gate_finding"),
            "severity": str(raw.get("severity") or "medium"),
            "blocking_level": str(raw.get("blocking_level") or "warning"),
            "repair_owner": str(raw.get("repair_owner") or "auditor"),
            "stage_id": raw.get("stage_id"),
            "artifact_id": raw.get("artifact_id"),
            "claim_id": raw.get("claim_id"),
            "source_id": raw.get("source_id"),
            "line_number": raw.get("line_number"),
            "description": str(
                raw.get("description") or "deterministic Gate finding"
            ),
            "recommendation": str(
                raw.get("recommendation") or "inspect Gate inputs"
            ),
            "category": str(raw.get("category") or "gate_finding"),
            "evidence_ref": str(raw.get("evidence_ref") or finding_id),
            "metadata": raw.get("metadata") or {},
            "accepted_transaction_id": accepted_transaction_id,
        },
        strict=True,
    )


def _replay_gate_outcomes(
    store: SQLiteControlStore,
    snapshot: ControlStoreSnapshot,
    binding: RunContractBinding,
    *,
    stage_id: Literal["auditor", "finalize"],
    stages: tuple[dict[str, object], ...],
    artifacts: tuple[dict[str, object], ...],
    artifact_bindings: tuple[GateArtifactBinding, ...],
    evaluator_version: str = EVALUATOR_VERSION,
) -> dict[str, tuple[str | None, list[dict[str, object]]]]:
    """Replay the sole preloaded Gate evaluator from exact Store revisions."""

    artifact_records = {item.artifact_id: item for item in snapshot.artifacts}
    revisions = {
        (item.artifact_id, item.revision): item
        for item in snapshot.artifact_revisions
    }

    def current_revision(artifact_id: str) -> ArtifactRevision:
        record = artifact_records.get(artifact_id)
        if record is None or record.current_revision <= 0:
            raise CoreRunError("gate_input_binding_invalid")
        revision = revisions.get((artifact_id, record.current_revision))
        if revision is None:
            raise CoreRunError("control_store_integrity_invalid")
        return revision

    if stage_id not in {"auditor", "finalize"} or not artifact_bindings:
        raise CoreRunError("gate_input_binding_invalid")
    binding_keys = [
        (item.artifact_id, item.artifact_revision, item.usage)
        for item in artifact_bindings
    ]
    if (
        [item.position for item in artifact_bindings]
        != list(range(len(artifact_bindings)))
        or len(binding_keys) != len(set(binding_keys))
    ):
        raise CoreRunError("gate_input_binding_invalid")
    bound_revisions: list[tuple[ArtifactRevision, str]] = []
    for item in artifact_bindings:
        revision = revisions.get((item.artifact_id, item.artifact_revision))
        record = artifact_records.get(item.artifact_id)
        if (
            item.run_id != snapshot.run.run_id
            or revision is None
            or record is None
            or record.current_revision != item.artifact_revision
            or revision.sha256 != item.artifact_sha256
        ):
            raise CoreRunError("gate_input_binding_invalid")
        bound_revisions.append((revision, item.usage))

    candidate_artifact = current_revision("candidate_claims")
    screened_artifact = current_revision("screened_candidates")
    candidate_record = _one_proposal(
        snapshot.accepted_proposals,
        "candidate",
        current_revision=candidate_artifact.revision,
    )
    screened_record = _one_proposal(
        snapshot.accepted_proposals,
        "screened",
        current_revision=screened_artifact.revision,
    )
    if (
        screened_record.parent_proposal_id != candidate_record.proposal_id
        or candidate_record.artifact_id != candidate_artifact.artifact_id
        or candidate_record.artifact_revision != candidate_artifact.revision
        or screened_record.artifact_id != screened_artifact.artifact_id
        or screened_record.artifact_revision != screened_artifact.revision
    ):
        raise CoreRunError("gate_input_binding_invalid")

    analyst_revision: ArtifactRevision | None = None
    render = None
    if stage_id == "auditor":
        ledger_revision = current_revision("claim_ledger")
        brief_revision = current_revision("audited_brief")
        analyst_record = artifact_records.get("analyst_draft_snapshot")
        if analyst_record is not None and analyst_record.current_revision:
            analyst_revision = current_revision("analyst_draft_snapshot")
        expected = [
            (ledger_revision, "ledger"),
            (brief_revision, "brief"),
        ]
        if analyst_revision is not None:
            expected.append((analyst_revision, "analyst_snapshot"))
        expected.extend(
            (
                (screened_artifact, "screened_candidates"),
                (candidate_artifact, "screened_candidates"),
            )
        )
        reader_facing_mode = False
        target_artifact = "audited_brief"
        gate_artifact_id = "auditor_quality_gate_report"
    else:
        promotion = classify_current_audit_promotion(
            snapshot,
            store.read_artifact_revision_bytes,
        )
        current_renders = []
        for candidate_render in snapshot.finalize_renders:
            if (
                promotion is None
                or not promotion.is_current_lineage
                or candidate_render.run_contract_fingerprint
                != binding.contract_fingerprint
                or candidate_render.audit_proposal_id
                != promotion.proposal_record.proposal_id
                or (
                    candidate_render.audited_brief.artifact_id,
                    candidate_render.audited_brief.revision,
                )
                != (
                    promotion.brief_revision.artifact_id,
                    promotion.brief_revision.revision,
                )
                or (
                    candidate_render.audit_report.artifact_id,
                    candidate_render.audit_report.revision,
                )
                != (
                    promotion.report_revision.artifact_id,
                    promotion.report_revision.revision,
                )
            ):
                continue
            reader_revisions = []
            for reference in candidate_render.reader_artifacts:
                record = artifact_records.get(reference.artifact_id)
                revision = revisions.get((reference.artifact_id, reference.revision))
                if (
                    record is None
                    or record.current_revision != reference.revision
                    or revision is None
                ):
                    break
                reader_revisions.append(revision)
            else:
                current_renders.append((candidate_render, reader_revisions))
        if len(current_renders) != 1:
            raise CoreRunError("gate_input_binding_invalid")
        render, reader_revisions = current_renders[0]
        primary_readers = [
            item for item in reader_revisions if item.artifact_id == "reader_brief"
        ]
        if len(primary_readers) != 1:
            raise CoreRunError("gate_input_binding_invalid")
        brief_revision = primary_readers[0]
        ledger_revision = current_revision("claim_ledger")
        expected = [
            (candidate_artifact, "screened_candidates"),
            (screened_artifact, "screened_candidates"),
            *((item, "reader_artifact") for item in reader_revisions),
            (promotion.report_revision, "audit_report"),
            (ledger_revision, "ledger"),
        ]
        reader_facing_mode = True
        target_artifact = "reader_brief"
        gate_artifact_id = "finalize_quality_gate_report"

    expected_signature = [
        (item.artifact_id, item.revision, item.sha256, usage)
        for item, usage in expected
    ]
    actual_signature = [
        (item.artifact_id, item.revision, item.sha256, usage)
        for item, usage in bound_revisions
    ]
    if actual_signature != expected_signature:
        raise CoreRunError("gate_input_binding_invalid")
    try:
        ledger = _claim_ledger(
            store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                ledger_revision.artifact_id,
                ledger_revision.revision,
            )
        )
        if evaluator_version == "2":
            sources_by_id = {item.source_id: item for item in snapshot.sources}
            for claim in ledger:
                source = sources_by_id.get(claim.source_id)
                if (
                    source is not None
                    and source.document_kind == "status_incident"
                    and source.opened_at is not None
                    and not claim.metadata.get("published_at")
                ):
                    claim.metadata["published_at"] = source.opened_at
        markdown = store.read_artifact_revision_bytes(
            snapshot.run.run_id,
            brief_revision.artifact_id,
            brief_revision.revision,
        ).decode("utf-8", errors="strict")
        analyst_markdown = (
            None
            if analyst_revision is None
            else store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                analyst_revision.artifact_id,
                analyst_revision.revision,
            ).decode("utf-8", errors="strict")
        )
        screened, _ = _load_proposal(
            store,
            screened_record,
            ScreenedCandidatesProposal,
        )
        candidates, _ = _load_proposal(
            store,
            candidate_record,
            CandidateClaimsProposal,
        )
        direction = binding.run_direction
        raw = evaluate_quality_gate_findings_preloaded(
            markdown=markdown,
            ledger=ledger,
            config={
                "project": (
                    {"name": direction.subject_name}
                    if evaluator_version == "1"
                    else {
                        "name": direction.subject_name,
                        "target_terms": list(direction.target_terms),
                    }
                ),
                "report": {"cadence": direction.cadence},
            },
            user_text=f"Target: {direction.subject_name}",
            analyst_markdown=analyst_markdown,
            report_date=direction.report_date,
            max_source_age_days=direction.max_source_age_days,
            strict=False,
            reader_facing_mode=reader_facing_mode,
            target_artifact=target_artifact,
            stages=list(stages),
            artifacts=list(artifacts),
            gate_stage_id=stage_id,
            gate_artifact_id=gate_artifact_id,
            policy_gate_adapter={
                "status": "applied",
                "gate_policy": {
                    gate_id: (
                        "strict" if binding.gate_strictness[gate_id] else "standard"
                    )
                    for gate_id in sorted(binding.gate_strictness)
                },
            },
            coverage_omission_projection=_coverage_projection(
                candidates,
                screened,
                ledger,
                markdown,
                reader_facing_mode=reader_facing_mode,
            ),
            atomic_graph_payload=None,
        )
    except CoreRunError:
        raise
    except (ControlStoreError, IntakeError, UnicodeDecodeError, ValidationError) as exc:
        raise CoreRunError("gate_input_binding_invalid") from exc
    return _classify_gate_outcomes(
        raw,
        stage_id=stage_id,
        gate_artifact_id=gate_artifact_id,
    )


def _classify_gate_outcomes(
    raw: object,
    *,
    stage_id: Literal["auditor", "finalize"] = "auditor",
    gate_artifact_id: str = "auditor_quality_gate_report",
) -> dict[str, tuple[str | None, list[dict[str, object]]]]:
    """Convert evaluator availability into explicit durable Gate outcomes."""

    mapping = raw if isinstance(raw, dict) else {}
    exact_keys = set(mapping) == set(GATE_IDS)
    outcomes: dict[str, tuple[str | None, list[dict[str, object]]]] = {}
    for gate_id in sorted(GATE_IDS):
        if gate_id not in mapping:
            status = "unavailable" if exact_keys or isinstance(raw, dict) else "invalid"
            reason = (
                "The deterministic Gate evaluator did not return this Gate."
                if status == "unavailable"
                else "The deterministic Gate evaluator returned an invalid batch."
            )
            outcomes[gate_id] = (
                status,
                [
                    _negative_gate_finding(
                        gate_id,
                        status,
                        reason,
                        stage_id=stage_id,
                        gate_artifact_id=gate_artifact_id,
                    )
                ],
            )
            continue
        values = mapping[gate_id]
        if (
            not exact_keys
            or not isinstance(values, list)
            or not all(isinstance(item, dict) for item in values)
            or not _gate_findings_are_valid(gate_id, values)
        ):
            outcomes[gate_id] = (
                "invalid",
                [
                    _negative_gate_finding(
                        gate_id,
                        "invalid",
                        "The deterministic Gate evaluator returned an invalid result.",
                        stage_id=stage_id,
                        gate_artifact_id=gate_artifact_id,
                    )
                ],
            )
            continue
        outcomes[gate_id] = (None, values)
    return outcomes


def _gate_findings_are_valid(
    gate_id: str,
    findings: list[dict[str, object]],
) -> bool:
    """Validate evaluator-owned finding shape before it becomes Gate truth."""

    try:
        for position, finding in enumerate(findings, start=1):
            _gate_finding_record(
                run_id="RUN-GATE-VALIDATION",
                evaluation_id="GATE-VALIDATION",
                gate_id=gate_id,
                position=position,
                raw=finding,
                accepted_transaction_id="TX-GATE-VALIDATION",
            )
    except (RecursionError, TypeError, ValueError, ValidationError):
        return False
    return True


def _negative_gate_finding(
    gate_id: str,
    status: str,
    description: str,
    *,
    stage_id: Literal["auditor", "finalize"] = "auditor",
    gate_artifact_id: str = "auditor_quality_gate_report",
) -> dict[str, object]:
    return {
        "finding_type": f"gate_evaluator_{status}",
        "severity": "high",
        "blocking_level": "blocking",
        "repair_owner": stage_id,
        "stage_id": stage_id,
        "artifact_id": gate_artifact_id,
        "description": description,
        "recommendation": "Inspect the deterministic Gate input and evaluator.",
        "category": "gate_evaluator",
        "evidence_ref": f"gate:{gate_id}:{status}",
        "metadata": {"outcome": status},
    }


def _load_proposal(
    store: SQLiteControlStore,
    record: AcceptedProposalRecord,
    model_type: type[_ProposalT],
) -> tuple[_ProposalT, bytes]:
    try:
        payload = store.read_artifact_revision_bytes(
            record.run_id,
            record.artifact_id,
            record.artifact_revision,
        )
        model = model_type.model_validate(parse_json_object(payload), strict=True)
    except (ControlStoreError, IntakeError, ValidationError) as exc:
        raise CoreRunError("gate_input_binding_invalid") from exc
    if sha256_hex(payload) != record.proposal_sha256:
        raise CoreRunError("control_store_integrity_invalid")
    return model, payload


def _claim_ledger(payload: bytes) -> ClaimLedger:
    try:
        data = parse_json_object(payload)
        rows = data.get("claims")
        if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
            raise IntakeError("scratch_payload_unreadable")
        return ClaimLedger([Claim.from_dict(item) for item in rows])
    except (IntakeError, TypeError, ValueError) as exc:
        raise CoreRunError("gate_input_binding_invalid") from exc


def _coverage_projection(
    candidates: CandidateClaimsProposal,
    screened: ScreenedCandidatesProposal,
    ledger: ClaimLedger,
    markdown: str,
    *,
    reader_facing_mode: bool = False,
) -> dict[str, object]:
    by_id = {item.candidate_id: item for item in candidates.candidates}
    selected: list[dict[str, object]] = []
    for decision in screened.decisions:
        candidate = by_id.get(decision.candidate_id)
        if candidate is None:
            raise CoreRunError("gate_input_binding_invalid")
        if decision.decision == "selected":
            row = candidate.model_dump(mode="json", exclude_unset=False)
            row["priority"] = decision.priority
            selected.append(row)
    high = [item for item in selected if item.get("priority") == "high"]
    ledger_rows = list(ledger)
    cited = set()
    from multi_agent_brief.core.citations import extract_src_ref_ids

    cited.update(extract_src_ref_ids(markdown))
    missing_ledger: list[dict[str, object]] = []
    missing_brief: list[dict[str, object]] = []
    for candidate in high:
        matches = [
            claim
            for claim in ledger_rows
            if claim.source_id == candidate["source_id"]
            or " ".join(claim.statement.split()).casefold()
            == " ".join(str(candidate["statement"]).split()).casefold()
        ]
        trace = {
            "candidate_id": candidate["candidate_id"],
            "statement": candidate["statement"],
            "source_id": candidate["source_id"],
            "display": candidate["candidate_id"],
            "priority": "high",
        }
        if not matches:
            missing_ledger.append(trace)
        elif (
            not reader_facing_mode
            and not any(item.claim_id in cited for item in matches)
        ):
            missing_brief.append(
                {
                    **trace,
                    "claim_ids": [item.claim_id for item in matches],
                    "source_ids": [item.source_id for item in matches],
                }
            )
    return {
        "status": "checked",
        "semantic_boundary": "deterministic_selected_candidate_continuity_only",
        "reader_facing_mode": reader_facing_mode,
        "selected_count": len(selected),
        "high_priority_selected_count": len(high),
        "missing_from_ledger_count": len(missing_ledger),
        "missing_from_brief_count": len(missing_brief),
        "missing_from_ledger": missing_ledger,
        "missing_from_brief": missing_brief,
        "scoped_out": [],
        "untraceable_high_priority": [],
    }


def _now(clock: _Clock) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CoreRunError("core_run_request_invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["GateEvaluationService"]
