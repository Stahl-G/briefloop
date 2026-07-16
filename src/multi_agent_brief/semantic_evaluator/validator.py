"""Table-driven deterministic validation for Semantic Evaluator proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from multi_agent_brief.semantic_evaluator.contracts import (
    EVENT_SCHEMA_ID,
    RUN_SCHEMA_ID,
    VALIDATION_REPORT_SCHEMA_ID,
    AbstainConflictingContextResult,
    AbstainInsufficientContextResult,
    AbstainUnableToAssessResult,
    AssessmentPlan,
    AssessmentUnitOutcome,
    AttemptRef,
    BoundedContext,
    DimensionResponse,
    FindingEmittedResult,
    FindingProposal,
    O3Handoff,
    ReaderArtifact,
    SemanticAssessmentRun,
    SemanticEvaluatorEvent,
    ValidationReport,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import (
    bounded_context_sha256 as compute_bounded_context_sha256,
    replay_span,
)
from multi_agent_brief.semantic_evaluator.parser import (
    FORBIDDEN_AUTHORITY_KEYS,
    FORBIDDEN_SECURITY_KEYS,
    find_forbidden_keys,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_bytes,
)
from multi_agent_brief.semantic_evaluator.unit_planner import (
    validate_frozen_assessment_plan,
)


VALIDATOR_VERSION = "dimension_validator_v1"


@dataclass(frozen=True)
class DimensionValidationResult:
    trial_id: str
    report_sha256: str
    bounded_context_sha256: str
    assessment_plan_sha256: str
    dimension_id: str
    unit_outcomes: tuple[AssessmentUnitOutcome, ...]
    accepted_findings: tuple[FindingProposal, ...]
    rejected_finding_ids: tuple[str, ...]
    handoffs: tuple[O3Handoff, ...]
    reason_codes: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return not self.reason_codes

    @property
    def abstention_count(self) -> int:
        return sum(
            item.disposition.startswith("abstain_") for item in self.unit_outcomes
        )


@dataclass(frozen=True)
class AssembledRun:
    run: SemanticAssessmentRun
    validation_report: ValidationReport
    events: tuple[SemanticEvaluatorEvent, ...]


def _all_string_values(value: Any) -> Iterable[str]:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            yield current
        elif isinstance(current, dict):
            yield from (key for key in current if isinstance(key, str))
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _span_reason(artifact: ReaderArtifact, span: Any) -> str | None:
    try:
        replay_span(artifact, span)
    except SemanticEvaluatorError as exc:
        return exc.reason_code
    return None


def _validate_handoff(
    handoff: O3Handoff,
    *,
    artifact: ReaderArtifact,
    context: BoundedContext,
    scope_class: str,
    eligible_requirement_types: set[str],
) -> set[str]:
    reasons = {
        reason
        for span in handoff.report_spans
        if (reason := _span_reason(artifact, span)) is not None
    }
    requirements = {item.requirement_id: item for item in context.requirements}
    if scope_class == "O1" and handoff.context_requirement_ids:
        reasons.add("o1_requirement_binding_forbidden")
    for requirement_id in handoff.context_requirement_ids:
        requirement = requirements.get(requirement_id)
        if requirement is None:
            reasons.add("requirement_reference_unknown")
        elif requirement.type not in eligible_requirement_types:
            reasons.add("requirement_type_not_eligible")
    return reasons


def _validate_finding(
    finding: FindingProposal,
    *,
    unit: Any,
    artifact: ReaderArtifact,
    context: BoundedContext,
) -> set[str]:
    reasons: set[str] = set()
    if (
        finding.assessment_unit_id != unit.assessment_unit_id
        or finding.dimension_id != unit.dimension_id
        or finding.scope_class != unit.scope_class
    ):
        reasons.add("finding_owner_mismatch")
    for span in finding.report_spans:
        reason = _span_reason(artifact, span)
        if reason is not None:
            reasons.add(reason)
    requirement_map = {item.requirement_id: item for item in context.requirements}
    if unit.scope_class == "O1":
        if finding.context_requirement_ids:
            reasons.add("o1_requirement_binding_forbidden")
    else:
        if not finding.context_requirement_ids:
            reasons.add("o2_requirement_binding_required")
        for requirement_id in finding.context_requirement_ids:
            requirement = requirement_map.get(requirement_id)
            if requirement is None:
                reasons.add("requirement_reference_unknown")
            elif requirement.type not in set(unit.eligible_requirement_types):
                reasons.add("requirement_type_not_eligible")
    if finding.external_premise_disclosure == "required":
        reasons.add("evidence_dependent_finding_forbidden")
    return reasons


def validate_dimension_response(
    response: DimensionResponse,
    *,
    raw_object: dict[str, Any],
    expected_dimension_id: str,
    plan: AssessmentPlan,
    reader_artifact: ReaderArtifact,
    bounded_context: BoundedContext,
    attempt_ref: str,
    forbidden_canary_values: Iterable[str] = (),
) -> DimensionValidationResult:
    reasons: set[str] = set()
    try:
        validate_frozen_assessment_plan(plan)
    except SemanticEvaluatorError:
        reasons.add("run_binding_mismatch")
    if (
        reader_artifact.report_sha256 != plan.report_sha256
        or bounded_context.context_sha256
        != compute_bounded_context_sha256(bounded_context)
    ):
        reasons.add("run_binding_mismatch")
    if canonical_json_bytes(raw_object) != canonical_json_bytes(response):
        reasons.add("raw_response_binding_mismatch")
    if find_forbidden_keys(raw_object, FORBIDDEN_AUTHORITY_KEYS):
        reasons.add("authority_output_forbidden")
    if find_forbidden_keys(raw_object, FORBIDDEN_SECURITY_KEYS):
        reasons.add("tool_or_canary_output_forbidden")
    canaries = tuple(item for item in forbidden_canary_values if item)
    if canaries and any(
        canary in value
        for value in _all_string_values(raw_object)
        for canary in canaries
    ):
        reasons.add("tool_or_canary_output_forbidden")
    if response.trial_id != plan.trial_id:
        reasons.add("trial_identity_mismatch")
    if response.dimension_id != expected_dimension_id:
        reasons.add("dimension_identity_mismatch")

    expected_units = [
        unit for unit in plan.units if unit.dimension_id == expected_dimension_id
    ]
    expected_by_id = {unit.assessment_unit_id: unit for unit in expected_units}
    observed_ids = [item.assessment_unit_id for item in response.unit_results]
    if set(observed_ids) != set(expected_by_id) or len(observed_ids) != len(
        expected_by_id
    ):
        reasons.add("assessment_unit_set_mismatch")
    response_level_reasons = set(reasons)

    accepted_findings: list[FindingProposal] = []
    rejected_ids: list[str] = []
    handoffs: list[O3Handoff] = []
    outcomes: list[AssessmentUnitOutcome] = []

    for result in response.unit_results:
        unit = expected_by_id.get(result.assessment_unit_id)
        if unit is None:
            continue
        finding_ids: list[str] = []
        handoff_ids: list[str] = []
        if isinstance(result, FindingEmittedResult):
            for finding in result.findings:
                finding_ids.append(finding.finding_id)
                finding_reasons = _validate_finding(
                    finding,
                    unit=unit,
                    artifact=reader_artifact,
                    context=bounded_context,
                )
                finding_reasons.update(response_level_reasons)
                if finding_reasons:
                    rejected_ids.append(finding.finding_id)
                    reasons.update(finding_reasons)
                else:
                    accepted_findings.append(finding)
        elif isinstance(
            result,
            (
                AbstainInsufficientContextResult,
                AbstainUnableToAssessResult,
                AbstainConflictingContextResult,
            ),
        ):
            if (
                isinstance(result, AbstainUnableToAssessResult)
                and result.reason_code == "evidence_dependent_assessment"
                and not result.handoffs
            ):
                reasons.add("evidence_dependent_handoff_required")
            for handoff in result.handoffs:
                handoff_ids.append(handoff.handoff_id)
                handoff_reasons = _validate_handoff(
                    handoff,
                    artifact=reader_artifact,
                    context=bounded_context,
                    scope_class=unit.scope_class,
                    eligible_requirement_types=set(unit.eligible_requirement_types),
                )
                handoff_reasons.update(response_level_reasons)
                if handoff_reasons:
                    reasons.update(handoff_reasons)
                else:
                    handoffs.append(handoff)
        outcomes.append(
            AssessmentUnitOutcome(
                assessment_unit_id=unit.assessment_unit_id,
                dimension_id=unit.dimension_id,
                sub_aspect_id=unit.sub_aspect_id,
                disposition=result.disposition,
                finding_ids=finding_ids,
                handoff_ids=handoff_ids,
                attempt_ref=attempt_ref,
            )
        )

    return DimensionValidationResult(
        trial_id=plan.trial_id,
        report_sha256=reader_artifact.report_sha256,
        bounded_context_sha256=bounded_context.context_sha256,
        assessment_plan_sha256=plan.assessment_plan_sha256,
        dimension_id=expected_dimension_id,
        unit_outcomes=tuple(outcomes),
        accepted_findings=tuple(accepted_findings),
        rejected_finding_ids=tuple(sorted(set(rejected_ids))),
        handoffs=tuple(handoffs),
        reason_codes=tuple(sorted(reasons)),
    )


def make_semantic_evaluator_event(
    *,
    sequence: int,
    run_id: str,
    trial_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> SemanticEvaluatorEvent:
    event_id = f"event-{canonical_sha256([run_id, trial_id, sequence, event_type, payload])[:12]}"
    return SemanticEvaluatorEvent.model_validate(
        {
            "schema_version": EVENT_SCHEMA_ID,
            "event_id": event_id,
            "sequence": sequence,
            "run_id": run_id,
            "trial_id": trial_id,
            "event_type": event_type,
            "payload": {"event_type": event_type, **payload},
        }
    )


def build_validation_events(
    *,
    run_id: str,
    trial_id: str,
    plan: AssessmentPlan,
    results: Iterable[DimensionValidationResult],
    attempt_refs: Iterable[AttemptRef],
    run_status: str,
    run_reason_codes: Iterable[str] = (),
) -> tuple[SemanticEvaluatorEvent, ...]:
    result_list = list(results)
    attempts = list(attempt_refs)
    events: list[SemanticEvaluatorEvent] = []

    def add(event_type: str, payload: dict[str, Any]) -> None:
        events.append(
            make_semantic_evaluator_event(
                sequence=len(events) + 1,
                run_id=run_id,
                trial_id=trial_id,
                event_type=event_type,
                payload=payload,
            )
        )

    add(
        "assessment_plan_created",
        {
            "assessment_plan_sha256": plan.assessment_plan_sha256,
            "planned_unit_count": len(plan.units),
        },
    )
    for attempt in attempts:
        add(
            "attempt_started",
            {"dimension_id": attempt.dimension_id, "attempt_ref": attempt.attempt_ref},
        )
        if attempt.status == "completed":
            add(
                "attempt_completed",
                {
                    "dimension_id": attempt.dimension_id,
                    "attempt_ref": attempt.attempt_ref,
                },
            )
        else:
            add(
                "attempt_failed",
                {
                    "dimension_id": attempt.dimension_id,
                    "attempt_ref": attempt.attempt_ref,
                    "reason_code": attempt.reason_code or "provider_failed",
                },
            )
    all_reason_codes = {code for result in result_list for code in result.reason_codes}
    all_reason_codes.update(run_reason_codes)
    for reason_code in sorted(
        code for code in all_reason_codes if code == "tool_or_canary_output_forbidden"
    ):
        add("security_failure_recorded", {"reason_code": reason_code})
    for result in result_list:
        add(
            "dimension_parsed",
            {
                "dimension_id": result.dimension_id,
                "disposed_unit_count": len(result.unit_outcomes),
            },
        )
        accepted_ids = {item.finding_id for item in result.accepted_findings}
        rejected_ids = set(result.rejected_finding_ids)
        handoff_ids = {item.handoff_id for item in result.handoffs}
        for outcome in result.unit_outcomes:
            add(
                "unit_disposition_recorded",
                {
                    "assessment_unit_id": outcome.assessment_unit_id,
                    "disposition": outcome.disposition,
                    "finding_ids": outcome.finding_ids,
                    "handoff_ids": outcome.handoff_ids,
                },
            )
            for finding_id in outcome.finding_ids:
                if finding_id in accepted_ids:
                    add(
                        "finding_accepted",
                        {
                            "finding_id": finding_id,
                            "assessment_unit_id": outcome.assessment_unit_id,
                        },
                    )
                elif finding_id in rejected_ids:
                    add(
                        "finding_rejected",
                        {
                            "finding_id": finding_id,
                            "assessment_unit_id": outcome.assessment_unit_id,
                            "reason_codes": list(result.reason_codes),
                        },
                    )
            for handoff_id in outcome.handoff_ids:
                if handoff_id in handoff_ids:
                    add(
                        "o3_handoff_recorded",
                        {
                            "handoff_id": handoff_id,
                            "assessment_unit_id": outcome.assessment_unit_id,
                        },
                    )
    outcomes = [item for result in result_list for item in result.unit_outcomes]
    if run_status == "completed":
        add(
            "run_completed",
            {
                "disposed_unit_count": len(outcomes),
                "finding_count": sum(
                    len(result.accepted_findings) for result in result_list
                ),
                "abstention_count": sum(
                    item.disposition.startswith("abstain_") for item in outcomes
                ),
                "handoff_count": sum(len(result.handoffs) for result in result_list),
            },
        )
    else:
        codes = sorted(all_reason_codes) or [run_status]
        add("run_incomplete", {"run_status": run_status, "reason_codes": codes})
    return tuple(events)


def event_stream_bytes(events: Iterable[SemanticEvaluatorEvent]) -> bytes:
    event_list = list(events)
    expected = list(range(1, len(event_list) + 1))
    if [item.sequence for item in event_list] != expected:
        raise SemanticEvaluatorError("event_sequence_invalid")
    return b"".join(canonical_json_bytes(item) + b"\n" for item in event_list)


def recompute_event_counts(events: Iterable[SemanticEvaluatorEvent]) -> dict[str, int]:
    event_list = list(events)
    event_stream_bytes(event_list)
    dispositions = [
        item.payload
        for item in event_list
        if item.event_type == "unit_disposition_recorded"
    ]
    attempt_failure_count = sum(
        item.event_type == "attempt_failed" for item in event_list
    )
    security_failure_count = sum(
        item.event_type == "security_failure_recorded" for item in event_list
    )
    return {
        "disposed_unit_count": len(dispositions),
        "finding_count": sum(
            item.event_type == "finding_accepted" for item in event_list
        ),
        "abstention_count": sum(
            getattr(item, "disposition", "").startswith("abstain_")
            for item in dispositions
        ),
        "handoff_count": sum(
            item.event_type == "o3_handoff_recorded" for item in event_list
        ),
        "failure_count": attempt_failure_count
        + (1 if security_failure_count and not attempt_failure_count else 0),
    }


def assemble_semantic_assessment_run(
    *,
    run_id: str,
    trial_id: str,
    report_sha256: str,
    bounded_context_sha256: str,
    profile_sha256: str,
    instrument_sha256: str,
    plan: AssessmentPlan,
    results: Iterable[DimensionValidationResult],
    attempt_refs: Iterable[AttemptRef],
) -> AssembledRun:
    result_list = list(results)
    attempts = list(attempt_refs)
    outcomes = [item for result in result_list for item in result.unit_outcomes]
    findings = [item for result in result_list for item in result.accepted_findings]
    handoffs = [item for result in result_list for item in result.handoffs]
    rejected_ids = [
        item for result in result_list for item in result.rejected_finding_ids
    ]
    reason_codes = {code for result in result_list for code in result.reason_codes}
    expected_ids = {item.assessment_unit_id for item in plan.units}
    disposed_ids = {item.assessment_unit_id for item in outcomes}
    if len(disposed_ids) != len(outcomes):
        raise SemanticEvaluatorError("assessment_unit_set_mismatch")
    attempt_by_id = {item.attempt_ref: item for item in attempts}
    if len(attempt_by_id) != len(attempts):
        raise SemanticEvaluatorError("attempt_reference_incomplete")
    if plan.trial_id != trial_id:
        reason_codes.add("trial_identity_mismatch")
    try:
        validate_frozen_assessment_plan(plan)
    except SemanticEvaluatorError:
        reason_codes.add("run_binding_mismatch")
    if plan.report_sha256 != report_sha256 or plan.profile_sha256 != profile_sha256:
        reason_codes.add("run_binding_mismatch")
    if any(
        result.trial_id != trial_id
        or result.report_sha256 != report_sha256
        or result.bounded_context_sha256 != bounded_context_sha256
        or result.assessment_plan_sha256 != plan.assessment_plan_sha256
        for result in result_list
    ):
        reason_codes.add("run_binding_mismatch")
    if expected_ids != disposed_ids:
        reason_codes.add("assessment_unit_set_mismatch")
    invalid_attempt_binding = any(
        (attempt := attempt_by_id.get(item.attempt_ref)) is None
        or attempt.status != "completed"
        or attempt.dimension_id != item.dimension_id
        for item in outcomes
    )
    if invalid_attempt_binding:
        reason_codes.add("attempt_reference_incomplete")
    security_failed = "tool_or_canary_output_forbidden" in reason_codes
    if security_failed:
        run_status = "security_failed"
    elif expected_ids != disposed_ids or invalid_attempt_binding:
        run_status = "incomplete"
    elif reason_codes:
        run_status = "validation_failed"
    else:
        run_status = "completed"
    events = build_validation_events(
        run_id=run_id,
        trial_id=trial_id,
        plan=plan,
        results=result_list,
        attempt_refs=attempts,
        run_status=run_status,
        run_reason_codes=reason_codes,
    )
    run = SemanticAssessmentRun(
        schema_version=RUN_SCHEMA_ID,
        run_id=run_id,
        trial_id=trial_id,
        report_sha256=report_sha256,
        bounded_context_sha256=bounded_context_sha256,
        profile_sha256=profile_sha256,
        instrument_sha256=instrument_sha256,
        assessment_plan_sha256=plan.assessment_plan_sha256,
        run_status=run_status,
        assessment_units=outcomes,
        findings=findings,
        handoffs=handoffs,
        attempt_refs=attempts,
        event_stream_sha256=sha256_bytes(event_stream_bytes(events)),
    )
    counts = recompute_event_counts(events)
    validation_status = (
        "accepted"
        if run_status == "completed"
        else "rejected"
        if run_status in {"validation_failed", "security_failed"}
        else "incomplete"
    )
    validation_report = ValidationReport(
        schema_version=VALIDATION_REPORT_SCHEMA_ID,
        run_id=run_id,
        trial_id=trial_id,
        validation_status=validation_status,
        reason_codes=sorted(reason_codes),
        accepted_finding_ids=[item.finding_id for item in findings],
        rejected_finding_ids=sorted(set(rejected_ids)),
        planned_unit_count=len(plan.units),
        disposed_unit_count=counts["disposed_unit_count"],
        finding_count=counts["finding_count"],
        abstention_count=counts["abstention_count"],
        handoff_count=counts["handoff_count"],
        raw_attempt_refs=[item.attempt_ref for item in attempts],
    )
    return AssembledRun(run=run, validation_report=validation_report, events=events)


__all__ = [
    "AssembledRun",
    "DimensionValidationResult",
    "VALIDATOR_VERSION",
    "assemble_semantic_assessment_run",
    "build_validation_events",
    "event_stream_bytes",
    "make_semantic_evaluator_event",
    "recompute_event_counts",
    "validate_dimension_response",
]
