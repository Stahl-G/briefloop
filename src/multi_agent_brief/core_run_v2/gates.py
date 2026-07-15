"""Store-native deterministic non-final Gate evaluation for fresh-v2."""

from __future__ import annotations

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
from multi_agent_brief.quality_gates.state import (
    evaluate_quality_gate_findings_preloaded,
)

from .errors import CoreRunError, CoreRunResult, core_run_error_code
from .integrity import RunIntegrityService, materialize_checkout
from .policy import derived_id, transaction_type_for
from .verifier import CoreRunDomainVerifier, resolve_core_replay


_Clock = Callable[[], datetime]
_ProposalT = TypeVar("_ProposalT", bound=StrictModel)
_EVALUATOR_IMPLEMENTATION = "core-v2-preloaded-quality-gates"
_EVALUATOR_VERSION = "1"


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
            return CoreRunResult(
                status="failed_uncommitted",
                error_code=core_run_error_code(exc),
            )

    def _evaluate(self, request: GateCheckRequest) -> CoreRunResult:
        with self._open_store() as store:
            verified = self._verifier.verify(store, request.run_id)
            stage = next(
                (
                    item
                    for item in verified.snapshot.stage_states
                    if item.stage_id == "auditor"
                ),
                None,
            )
            artifacts = {item.artifact_id: item for item in verified.snapshot.artifacts}
            revisions = {
                (item.artifact_id, item.revision): item
                for item in verified.snapshot.artifact_revisions
            }
            required: list[tuple[ArtifactRevision, str]] = []

            def add_current(artifact_id: str, usage: str) -> ArtifactRevision:
                artifact = artifacts.get(artifact_id)
                if artifact is None or artifact.current_revision <= 0:
                    raise CoreRunError("gate_input_binding_invalid")
                revision = revisions.get((artifact_id, artifact.current_revision))
                if revision is None:
                    raise CoreRunError("control_store_integrity_invalid")
                required.append((revision, usage))
                return revision

            ledger_revision = add_current("claim_ledger", "ledger")
            brief_revision = add_current("audited_brief", "brief")
            analyst_revision = None
            if artifacts.get("analyst_draft_snapshot") is not None and artifacts[
                "analyst_draft_snapshot"
            ].current_revision:
                analyst_revision = add_current(
                    "analyst_draft_snapshot",
                    "analyst_snapshot",
                )
            screened_record = _one_proposal(
                verified.snapshot.accepted_proposals,
                "screened",
                current_revision=artifacts["screened_candidates"].current_revision,
            )
            candidate_record = next(
                (
                    item
                    for item in verified.snapshot.accepted_proposals
                    if item.proposal_id == screened_record.parent_proposal_id
                    and item.proposal_kind == "candidate"
                ),
                None,
            )
            if candidate_record is None:
                raise CoreRunError("gate_input_binding_invalid")
            screened_revision = revisions.get(
                (screened_record.artifact_id, screened_record.artifact_revision)
            )
            candidate_revision = revisions.get(
                (candidate_record.artifact_id, candidate_record.artifact_revision)
            )
            if screened_revision is None or candidate_revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            required.extend(
                (
                    (screened_revision, "screened_candidates"),
                    (candidate_revision, "screened_candidates"),
                )
            )
            expected = {
                (item.artifact_id, item.revision)
                for item in request.expected_input_artifacts
            }
            actual = {(item.artifact_id, item.revision) for item, _usage in required}
            if expected != actual or len(request.expected_input_artifacts) != len(actual):
                raise CoreRunError("gate_input_binding_invalid")
            report_record = artifacts.get("auditor_quality_gate_report")
            if report_record is None:
                raise CoreRunError("control_store_integrity_invalid")
            input_hashes = [
                {
                    "artifact_id": item.artifact_id,
                    "revision": item.revision,
                    "sha256": item.sha256,
                    "usage": usage,
                }
                for item, usage in required
            ]
            fingerprint = canonical_fingerprint(
                {
                    "request": request.model_dump(mode="json", exclude_unset=False),
                    "inputs": input_hashes,
                    "contract_fingerprint": verified.binding.contract_fingerprint,
                    "evaluator": _EVALUATOR_IMPLEMENTATION,
                    "evaluator_version": _EVALUATOR_VERSION,
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
            if stage is None or stage.status != "ready":
                raise CoreRunError("stage_not_current")
            if verified.snapshot.store_revision != request.expected_store_revision:
                raise CoreRunError("store_revision_conflict")
            if verified.snapshot.gate_evaluations:
                raise CoreRunError("gate_policy_binding_invalid")
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
                ledger_bytes = store.read_artifact_revision_bytes(
                    request.run_id,
                    ledger_revision.artifact_id,
                    ledger_revision.revision,
                )
                ledger = _claim_ledger(ledger_bytes)
                markdown = store.read_artifact_revision_bytes(
                    request.run_id,
                    brief_revision.artifact_id,
                    brief_revision.revision,
                ).decode("utf-8", errors="strict")
                analyst_markdown = (
                    None
                    if analyst_revision is None
                    else store.read_artifact_revision_bytes(
                        request.run_id,
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
                coverage = _coverage_projection(candidates, screened, ledger, markdown)
                direction = verified.binding.run_direction
                config = {
                    "project": {"name": direction.subject_name},
                    "report": {"cadence": direction.cadence},
                }
                policy_adapter = {
                    "status": "applied",
                    "gate_policy": {
                        gate_id: (
                            "strict"
                            if verified.binding.gate_strictness[gate_id]
                            else "standard"
                        )
                        for gate_id in sorted(verified.binding.gate_strictness)
                    },
                }
                try:
                    findings_by_gate = evaluate_quality_gate_findings_preloaded(
                        markdown=markdown,
                        ledger=ledger,
                        config=config,
                        user_text=f"Target: {direction.subject_name}",
                        analyst_markdown=analyst_markdown,
                        report_date=direction.report_date,
                        max_source_age_days=direction.max_source_age_days,
                        strict=False,
                        stages=list(verified.stages),
                        artifacts=list(verified.artifacts),
                        gate_stage_id="auditor",
                        gate_artifact_id="auditor_quality_gate_report",
                        policy_gate_adapter=policy_adapter,
                        coverage_omission_projection=coverage,
                        atomic_graph_payload=None,
                    )
                except (KeyError, RuntimeError, TypeError, ValueError) as exc:
                    raise CoreRunError("gate_input_binding_invalid") from exc
            except (ControlStoreError, IntakeError, UnicodeDecodeError, ValidationError) as exc:
                raise CoreRunError("gate_input_binding_invalid") from exc
            gate_outcomes = _classify_gate_outcomes(findings_by_gate)
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
                            "stage_id": "auditor",
                            "gate_id": gate_id,
                            "policy_version": policy_version,
                            "run_contract_fingerprint": verified.binding.contract_fingerprint,
                            "status": status,
                            "blocking": blocking,
                            "finding_ids": finding_ids,
                            "checked_at": now,
                            "producer_implementation": _EVALUATOR_IMPLEMENTATION,
                            "producer_version": _EVALUATOR_VERSION,
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
                "stage_id": "auditor",
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
                    "producer_id": _EVALUATOR_IMPLEMENTATION,
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
                    "stage_id": "auditor",
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
            materialize_checkout(self.workspace, report_record.path, report_bytes)
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
            receipt = unit.commit()
            self._verifier.verify(store, request.run_id)
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
    stages: tuple[dict[str, object], ...],
    artifacts: tuple[dict[str, object], ...],
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

    ledger_revision = current_revision("claim_ledger")
    brief_revision = current_revision("audited_brief")
    analyst_revision = (
        current_revision("analyst_draft_snapshot")
        if artifact_records["analyst_draft_snapshot"].current_revision
        else None
    )
    screened_record = _one_proposal(
        snapshot.accepted_proposals,
        "screened",
        current_revision=artifact_records["screened_candidates"].current_revision,
    )
    candidate_record = next(
        (
            item
            for item in snapshot.accepted_proposals
            if item.proposal_id == screened_record.parent_proposal_id
            and item.proposal_kind == "candidate"
        ),
        None,
    )
    if candidate_record is None:
        raise CoreRunError("gate_input_binding_invalid")
    try:
        ledger = _claim_ledger(
            store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                ledger_revision.artifact_id,
                ledger_revision.revision,
            )
        )
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
                "project": {"name": direction.subject_name},
                "report": {"cadence": direction.cadence},
            },
            user_text=f"Target: {direction.subject_name}",
            analyst_markdown=analyst_markdown,
            report_date=direction.report_date,
            max_source_age_days=direction.max_source_age_days,
            strict=False,
            stages=list(stages),
            artifacts=list(artifacts),
            gate_stage_id="auditor",
            gate_artifact_id="auditor_quality_gate_report",
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
            ),
            atomic_graph_payload=None,
        )
    except CoreRunError:
        raise
    except (ControlStoreError, IntakeError, UnicodeDecodeError, ValidationError) as exc:
        raise CoreRunError("gate_input_binding_invalid") from exc
    return _classify_gate_outcomes(raw)


def _classify_gate_outcomes(
    raw: object,
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
                [_negative_gate_finding(gate_id, status, reason)],
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
) -> dict[str, object]:
    return {
        "finding_type": f"gate_evaluator_{status}",
        "severity": "high",
        "blocking_level": "blocking",
        "repair_owner": "auditor",
        "stage_id": "auditor",
        "artifact_id": "auditor_quality_gate_report",
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
        elif not any(item.claim_id in cited for item in matches):
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
        "reader_facing_mode": False,
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
