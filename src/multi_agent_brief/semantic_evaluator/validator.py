"""Table-driven deterministic validation for Semantic Evaluator proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import ValidationError

from multi_agent_brief.semantic_evaluator.contracts import (
    EVENT_SCHEMA_ID,
    LAJ_COMPOSITION_WITNESS_SCHEMA_ID,
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
    FindingDraft,
    FindingEmittedResult,
    FindingProposal,
    LajCompositionWitness,
    O3Handoff,
    O3HandoffDraft,
    ReaderArtifact,
    SemanticAssessmentRun,
    SemanticEvaluatorEvent,
    ValidationReport,
)
from multi_agent_brief.semantic_evaluator.errors import (
    SemanticEvaluatorError,
    value_free_violations,
)
from multi_agent_brief.semantic_evaluator.normalization import (
    bounded_context_sha256 as compute_bounded_context_sha256,
    replay_reader_artifact,
    replay_span,
)
from multi_agent_brief.semantic_evaluator.parser import (
    FORBIDDEN_AUTHORITY_KEYS,
    FORBIDDEN_SECURITY_KEYS,
    find_forbidden_keys,
)
from multi_agent_brief.semantic_evaluator.profile import load_profile
from multi_agent_brief.semantic_evaluator.prompts import build_dimension_prompt
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_payload,
    canonical_model_sha256,
    canonical_sha256,
    sha256_bytes,
)
from multi_agent_brief.semantic_evaluator.unit_planner import (
    derive_finding_id,
    derive_handoff_id,
    derive_run_id,
    validate_frozen_assessment_plan,
)


VALIDATOR_VERSION = "dimension_validator_v1"


@dataclass(frozen=True)
class DimensionEvidence:
    """Replayable caller evidence; assembly always validates it again."""

    response: DimensionResponse
    raw_object: dict[str, Any]
    expected_dimension_id: str
    attempt_ref: str
    forbidden_canary_values: tuple[str, ...] = ()


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
    witness: LajCompositionWitness


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
    handoff: O3HandoffDraft | O3Handoff,
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
    finding: FindingDraft | FindingProposal,
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


def _canonical_finding(draft: FindingDraft, *, ordinal: int) -> FindingProposal:
    try:
        strict_draft = FindingDraft.model_validate(draft.model_dump(mode="json"))
    except ValidationError as exc:
        raise SemanticEvaluatorError(
            "raw_response_binding_mismatch",
            violations=value_free_violations(exc),
        ) from exc
    identity = strict_draft.model_dump(mode="json")
    return FindingProposal.model_validate(
        {
            **identity,
            "finding_id": derive_finding_id(
                assessment_unit_id=draft.assessment_unit_id,
                ordinal=ordinal,
                proposal_identity=identity,
            ),
            "status": "proposal",
        }
    )


def _canonical_handoff(draft: O3HandoffDraft, *, ordinal: int) -> O3Handoff:
    try:
        strict_draft = O3HandoffDraft.model_validate(draft.model_dump(mode="json"))
    except ValidationError as exc:
        raise SemanticEvaluatorError(
            "raw_response_binding_mismatch",
            violations=value_free_violations(exc),
        ) from exc
    identity = strict_draft.model_dump(mode="json")
    return O3Handoff.model_validate(
        {
            **identity,
            "handoff_id": derive_handoff_id(
                assessment_unit_id=draft.assessment_unit_id,
                ordinal=ordinal,
                handoff_identity=identity,
            ),
        }
    )


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
    try:
        response = DimensionResponse.model_validate(response.model_dump(mode="json"))
    except (AttributeError, ValidationError, ValueError) as exc:
        violations = (
            value_free_violations(exc) if isinstance(exc, ValidationError) else ()
        )
        raise SemanticEvaluatorError(
            "raw_response_binding_mismatch",
            violations=violations,
        ) from exc
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

    response_by_id = {item.assessment_unit_id: item for item in response.unit_results}
    for unit in expected_units:
        result = response_by_id.get(unit.assessment_unit_id)
        if result is None:
            continue
        finding_ids: list[str] = []
        handoff_ids: list[str] = []
        if isinstance(result, FindingEmittedResult):
            for ordinal, draft in enumerate(result.findings):
                finding = _canonical_finding(draft, ordinal=ordinal)
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
            for ordinal, draft in enumerate(result.handoffs):
                handoff = _canonical_handoff(draft, ordinal=ordinal)
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
    if "tool_or_canary_output_forbidden" in all_reason_codes:
        add(
            "security_failure_recorded",
            {"reason_code": "tool_or_canary_output_forbidden"},
        )
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
                            "reason_codes": sorted(all_reason_codes),
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


def _terminal_attempts(attempts: Iterable[AttemptRef]) -> dict[str, AttemptRef]:
    terminal: dict[str, AttemptRef] = {}
    for attempt in attempts:
        terminal[attempt.dimension_id] = attempt
    return terminal


def recompute_event_counts(events: Iterable[SemanticEvaluatorEvent]) -> dict[str, int]:
    event_list = list(events)
    event_stream_bytes(event_list)
    dispositions = [
        item.payload
        for item in event_list
        if item.event_type == "unit_disposition_recorded"
    ]
    attempts: list[AttemptRef] = []
    for item in event_list:
        if item.event_type == "attempt_completed":
            attempts.append(
                AttemptRef(
                    attempt_ref=item.payload.attempt_ref,
                    dimension_id=item.payload.dimension_id,
                    status="completed",
                    reason_code=None,
                )
            )
        elif item.event_type == "attempt_failed":
            attempts.append(
                AttemptRef(
                    attempt_ref=item.payload.attempt_ref,
                    dimension_id=item.payload.dimension_id,
                    status="failed",
                    reason_code=item.payload.reason_code,
                )
            )
    terminal_failure_count = sum(
        item.status == "failed" for item in _terminal_attempts(attempts).values()
    )
    final_status = next(
        (
            item.payload.run_status
            for item in reversed(event_list)
            if item.event_type == "run_incomplete"
        ),
        "completed",
    )
    run_level_failure = int(
        terminal_failure_count == 0
        and final_status in {"validation_failed", "security_failed"}
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
        "failure_count": terminal_failure_count + run_level_failure,
    }


def _require_admission(admission: Any) -> tuple[Any, Any, Any, Any, Any]:
    try:
        admitted = admission.admitted
        reader = admission.reader
        context = admission.bounded_context
        binding = admission.input_binding
        manifest = admission.instrument_manifest
        plan = admission.assessment_plan
        prompts = admission.prompts
        prompt_hashes = admission.prompt_request_sha256s
    except AttributeError as exc:
        raise SemanticEvaluatorError("run_binding_mismatch") from exc
    if not admitted or any(
        item is None for item in (reader, context, binding, manifest, plan)
    ):
        raise SemanticEvaluatorError("run_binding_mismatch")
    try:
        replay_reader_artifact(reader.artifact, reader.normalized_text)
        validate_frozen_assessment_plan(plan)
        loaded_profile = load_profile()
        expected_prompts = tuple(
            build_dimension_prompt(
                reader_artifact=reader.artifact,
                normalized_text=reader.normalized_text,
                bounded_context=context,
                dimension=dimension,
                assessment_plan=plan,
            )
            for dimension in loaded_profile.profile.dimensions
        )
    except (SemanticEvaluatorError, ValidationError, ValueError) as exc:
        raise SemanticEvaluatorError("run_binding_mismatch") from exc
    binding_identity = [
        binding.trial_id,
        binding.report_sha256,
        binding.normalized_text_sha256,
        binding.bounded_context_sha256,
        binding.profile_sha256,
        binding.instrument_config_sha256,
    ]
    expected_binding_id = f"binding-{canonical_sha256(binding_identity)[:12]}"
    if (
        binding.input_binding_sha256
        != canonical_model_sha256(binding, exclude=("input_binding_sha256",))
        or binding.binding_id != expected_binding_id
        or binding.public_data_attestation is not True
        or binding.private_or_confidential_material is not False
        or context.context_sha256 != compute_bounded_context_sha256(context)
        or reader.artifact.report_sha256 != binding.report_sha256
        or reader.artifact.normalized_text_sha256 != binding.normalized_text_sha256
        or context.context_sha256 != binding.bounded_context_sha256
        or context.language != binding.language
        or context.data_class != binding.data_class
        or plan.trial_id != binding.trial_id
        or plan.report_sha256 != binding.report_sha256
        or plan.profile_sha256 != binding.profile_sha256
        or manifest.profile_sha256 != binding.profile_sha256
        or manifest.instrument_config_sha256 != binding.instrument_config_sha256
        or manifest.instrument_sha256
        != canonical_model_sha256(manifest, exclude=("instrument_sha256",))
        or tuple(prompts) != expected_prompts
        or tuple(item.request_sha256 for item in prompts) != tuple(prompt_hashes)
    ):
        raise SemanticEvaluatorError("run_binding_mismatch")
    return reader, context, binding, manifest, plan


def _global_id_preflight(outcomes: Iterable[AssessmentUnitOutcome]) -> None:
    outcome_list = list(outcomes)
    finding_ids = [
        finding_id for item in outcome_list for finding_id in item.finding_ids
    ]
    if len(finding_ids) != len(set(finding_ids)):
        raise SemanticEvaluatorError("finding_id_duplicate")
    handoff_ids = [
        handoff_id for item in outcome_list for handoff_id in item.handoff_ids
    ]
    if len(handoff_ids) != len(set(handoff_ids)):
        raise SemanticEvaluatorError("handoff_id_duplicate")


def _build_witness(
    *,
    binding: Any,
    reader_artifact: ReaderArtifact,
    bounded_context: BoundedContext,
    instrument_manifest: Any,
    plan: AssessmentPlan,
    run: SemanticAssessmentRun,
    validation_report: ValidationReport,
    events: tuple[SemanticEvaluatorEvent, ...],
) -> LajCompositionWitness:
    payload = {
        "schema_version": LAJ_COMPOSITION_WITNESS_SCHEMA_ID,
        "input_binding": binding.model_dump(mode="json"),
        "reader_artifact": reader_artifact.model_dump(mode="json"),
        "bounded_context": bounded_context.model_dump(mode="json"),
        "instrument_manifest": instrument_manifest.model_dump(mode="json"),
        "assessment_plan": plan.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
        "validation_report": validation_report.model_dump(mode="json"),
        "events": [item.model_dump(mode="json") for item in events],
    }
    witness = LajCompositionWitness.model_validate(
        {**payload, "witness_sha256": canonical_sha256(payload)}
    )
    return verify_laj_composition_witness(witness)


def assemble_semantic_assessment_run(
    *,
    admission: Any,
    dimension_evidence: Iterable[DimensionEvidence],
    attempt_refs: Iterable[AttemptRef],
) -> AssembledRun:
    reader, context, binding, manifest, plan = _require_admission(admission)
    evidence_list = list(dimension_evidence)
    attempts = list(attempt_refs)
    attempt_ids = [item.attempt_ref for item in attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise SemanticEvaluatorError("attempt_reference_incomplete")
    evidence_by_dimension: dict[str, DimensionEvidence] = {}
    for evidence in evidence_list:
        if not isinstance(evidence, DimensionEvidence):
            raise SemanticEvaluatorError("run_binding_mismatch")
        if evidence.expected_dimension_id in evidence_by_dimension:
            raise SemanticEvaluatorError("assessment_unit_set_mismatch")
        evidence_by_dimension[evidence.expected_dimension_id] = evidence
    terminal = _terminal_attempts(attempts)
    dimensions = list(dict.fromkeys(item.dimension_id for item in plan.units))
    results: list[DimensionValidationResult] = []
    terminal_failure_reasons: set[str] = set()
    for dimension_id in dimensions:
        evidence = evidence_by_dimension.get(dimension_id)
        final_attempt = terminal.get(dimension_id)
        if evidence is None:
            if final_attempt is None or final_attempt.status != "failed":
                raise SemanticEvaluatorError("assessment_unit_failure_link_missing")
            terminal_failure_reasons.add(final_attempt.reason_code or "provider_failed")
            continue
        if (
            final_attempt is None
            or final_attempt.status != "completed"
            or final_attempt.attempt_ref != evidence.attempt_ref
        ):
            raise SemanticEvaluatorError("attempt_reference_incomplete")
        result = validate_dimension_response(
            evidence.response,
            raw_object=evidence.raw_object,
            expected_dimension_id=dimension_id,
            plan=plan,
            reader_artifact=reader.artifact,
            bounded_context=context,
            attempt_ref=evidence.attempt_ref,
            forbidden_canary_values=evidence.forbidden_canary_values,
        )
        expected_ids = {
            item.assessment_unit_id
            for item in plan.units
            if item.dimension_id == dimension_id
        }
        observed_ids = {item.assessment_unit_id for item in result.unit_outcomes}
        if observed_ids != expected_ids:
            if not observed_ids and evidence.response.unit_results:
                raise SemanticEvaluatorError("run_binding_mismatch")
            raise SemanticEvaluatorError("assessment_unit_failure_link_missing")
        results.append(result)
    if set(evidence_by_dimension) - set(dimensions):
        raise SemanticEvaluatorError("dimension_identity_mismatch")

    outcomes = [item for result in results for item in result.unit_outcomes]
    findings = [item for result in results for item in result.accepted_findings]
    handoffs = [item for result in results for item in result.handoffs]
    rejected_ids = [item for result in results for item in result.rejected_finding_ids]
    _global_id_preflight(outcomes)
    outcome_ids = [item.assessment_unit_id for item in outcomes]
    if len(outcome_ids) != len(set(outcome_ids)):
        raise SemanticEvaluatorError("assessment_unit_set_mismatch")
    reason_codes = {code for result in results for code in result.reason_codes}
    reason_codes.update(terminal_failure_reasons)
    missing_dimensions = set(dimensions) - set(evidence_by_dimension)
    if "tool_or_canary_output_forbidden" in reason_codes:
        run_status = "security_failed"
    elif missing_dimensions:
        run_status = "incomplete"
    elif reason_codes:
        run_status = "validation_failed"
    else:
        run_status = "completed"
    run_id = derive_run_id(
        input_binding_sha256=binding.input_binding_sha256,
        assessment_plan_sha256=plan.assessment_plan_sha256,
        instrument_sha256=manifest.instrument_sha256,
    )
    events = build_validation_events(
        run_id=run_id,
        trial_id=binding.trial_id,
        plan=plan,
        results=results,
        attempt_refs=attempts,
        run_status=run_status,
        run_reason_codes=reason_codes,
    )
    try:
        run = SemanticAssessmentRun(
            schema_version=RUN_SCHEMA_ID,
            run_id=run_id,
            trial_id=binding.trial_id,
            report_sha256=binding.report_sha256,
            bounded_context_sha256=binding.bounded_context_sha256,
            profile_sha256=binding.profile_sha256,
            instrument_sha256=manifest.instrument_sha256,
            assessment_plan_sha256=plan.assessment_plan_sha256,
            run_status=run_status,
            assessment_units=outcomes,
            findings=findings,
            handoffs=handoffs,
            attempt_refs=attempts,
            event_stream_sha256=sha256_bytes(event_stream_bytes(events)),
        )
    except ValidationError as exc:
        duplicate_finding = len({item.finding_id for item in findings}) != len(findings)
        duplicate_handoff = len({item.handoff_id for item in handoffs}) != len(handoffs)
        reason = (
            "finding_id_duplicate"
            if duplicate_finding
            else "handoff_id_duplicate"
            if duplicate_handoff
            else "run_binding_mismatch"
        )
        raise SemanticEvaluatorError(
            reason,
            violations=value_free_violations(exc),
        ) from exc
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
        trial_id=binding.trial_id,
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
    witness = _build_witness(
        binding=binding,
        reader_artifact=reader.artifact,
        bounded_context=context,
        instrument_manifest=manifest,
        plan=plan,
        run=run,
        validation_report=validation_report,
        events=events,
    )
    return AssembledRun(
        run=run,
        validation_report=validation_report,
        events=events,
        witness=witness,
    )


def _composition_witness_mismatch() -> None:
    raise SemanticEvaluatorError("composition_witness_mismatch")


def _verify_event_relations(witness: LajCompositionWitness) -> None:
    plan = witness.assessment_plan
    run = witness.run
    report = witness.validation_report
    events = witness.events
    try:
        stream = event_stream_bytes(events)
    except SemanticEvaluatorError:
        _composition_witness_mismatch()
        return
    if sha256_bytes(stream) != run.event_stream_sha256:
        _composition_witness_mismatch()
    for event in events:
        if event.run_id != run.run_id or event.trial_id != run.trial_id:
            _composition_witness_mismatch()
        expected = make_semantic_evaluator_event(
            sequence=event.sequence,
            run_id=event.run_id,
            trial_id=event.trial_id,
            event_type=event.event_type,
            payload=event.payload.model_dump(mode="json", exclude={"event_type"}),
        )
        if canonical_json_bytes(expected) != canonical_json_bytes(event):
            _composition_witness_mismatch()

    plan_events = [
        item for item in events if item.event_type == "assessment_plan_created"
    ]
    if len(plan_events) != 1 or plan_events[0].payload.model_dump(mode="json") != {
        "event_type": "assessment_plan_created",
        "assessment_plan_sha256": plan.assessment_plan_sha256,
        "planned_unit_count": len(plan.units),
    }:
        _composition_witness_mismatch()
    started = [item for item in events if item.event_type == "attempt_started"]
    terminal_events = [
        item
        for item in events
        if item.event_type in {"attempt_completed", "attempt_failed"}
    ]
    if len(started) != len(run.attempt_refs) or len(terminal_events) != len(
        run.attempt_refs
    ):
        _composition_witness_mismatch()
    for attempt, start, terminal_event in zip(
        run.attempt_refs, started, terminal_events
    ):
        if (
            start.payload.dimension_id != attempt.dimension_id
            or start.payload.attempt_ref != attempt.attempt_ref
            or terminal_event.payload.dimension_id != attempt.dimension_id
            or terminal_event.payload.attempt_ref != attempt.attempt_ref
            or terminal_event.event_type
            != (
                "attempt_completed"
                if attempt.status == "completed"
                else "attempt_failed"
            )
        ):
            _composition_witness_mismatch()
        if attempt.status == "failed" and (
            terminal_event.payload.reason_code != attempt.reason_code
        ):
            _composition_witness_mismatch()

    dimensions_with_outcomes = list(
        dict.fromkeys(item.dimension_id for item in run.assessment_units)
    )
    parsed = [item for item in events if item.event_type == "dimension_parsed"]
    expected_parsed = [
        (
            dimension_id,
            sum(item.dimension_id == dimension_id for item in run.assessment_units),
        )
        for dimension_id in dimensions_with_outcomes
    ]
    if [
        (item.payload.dimension_id, item.payload.disposed_unit_count) for item in parsed
    ] != expected_parsed:
        _composition_witness_mismatch()
    disposition_events = [
        item for item in events if item.event_type == "unit_disposition_recorded"
    ]
    if len(disposition_events) != len(run.assessment_units):
        _composition_witness_mismatch()
    for outcome, event in zip(run.assessment_units, disposition_events):
        if event.payload.model_dump(mode="json") != {
            "event_type": "unit_disposition_recorded",
            "assessment_unit_id": outcome.assessment_unit_id,
            "disposition": outcome.disposition,
            "finding_ids": outcome.finding_ids,
            "handoff_ids": outcome.handoff_ids,
        }:
            _composition_witness_mismatch()
    accepted = [item for item in events if item.event_type == "finding_accepted"]
    if [item.payload.finding_id for item in accepted] != [
        item.finding_id for item in run.findings
    ]:
        _composition_witness_mismatch()
    rejected = [item for item in events if item.event_type == "finding_rejected"]
    if sorted(item.payload.finding_id for item in rejected) != list(
        report.rejected_finding_ids
    ):
        _composition_witness_mismatch()
    owner_by_finding = {
        finding_id: outcome.assessment_unit_id
        for outcome in run.assessment_units
        for finding_id in outcome.finding_ids
    }
    if any(
        item.payload.assessment_unit_id != owner_by_finding.get(item.payload.finding_id)
        for item in [*accepted, *rejected]
    ):
        _composition_witness_mismatch()
    if any(item.payload.reason_codes != report.reason_codes for item in rejected):
        _composition_witness_mismatch()
    handoff_events = [
        item for item in events if item.event_type == "o3_handoff_recorded"
    ]
    if [item.payload.handoff_id for item in handoff_events] != [
        item.handoff_id for item in run.handoffs
    ]:
        _composition_witness_mismatch()
    owner_by_handoff = {
        handoff_id: outcome.assessment_unit_id
        for outcome in run.assessment_units
        for handoff_id in outcome.handoff_ids
    }
    if any(
        item.payload.assessment_unit_id != owner_by_handoff.get(item.payload.handoff_id)
        for item in handoff_events
    ):
        _composition_witness_mismatch()

    security_events = [
        item for item in events if item.event_type == "security_failure_recorded"
    ]
    expected_security = int("tool_or_canary_output_forbidden" in report.reason_codes)
    if len(security_events) != expected_security or any(
        item.payload.reason_code != "tool_or_canary_output_forbidden"
        for item in security_events
    ):
        _composition_witness_mismatch()
    final_events = [
        item
        for item in events
        if item.event_type in {"run_completed", "run_incomplete"}
    ]
    if len(final_events) != 1 or events[-1] != final_events[0]:
        _composition_witness_mismatch()
    counts = recompute_event_counts(events)
    final = final_events[0]
    if run.run_status == "completed":
        expected_final = {
            "event_type": "run_completed",
            "disposed_unit_count": len(run.assessment_units),
            "finding_count": len(run.findings),
            "abstention_count": sum(
                item.disposition.startswith("abstain_") for item in run.assessment_units
            ),
            "handoff_count": len(run.handoffs),
        }
        if (
            final.event_type != "run_completed"
            or final.payload.model_dump(mode="json") != expected_final
        ):
            _composition_witness_mismatch()
    else:
        expected_final = {
            "event_type": "run_incomplete",
            "run_status": run.run_status,
            "reason_codes": report.reason_codes,
        }
        if (
            final.event_type != "run_incomplete"
            or final.payload.model_dump(mode="json") != expected_final
        ):
            _composition_witness_mismatch()
    accepted_ids = {item.finding_id for item in run.findings}
    rejected_ids = set(report.rejected_finding_ids)
    recorded_handoffs = {item.handoff_id for item in run.handoffs}
    expected_specs: list[tuple[str, dict[str, Any]]] = [
        (
            "assessment_plan_created",
            {
                "event_type": "assessment_plan_created",
                "assessment_plan_sha256": plan.assessment_plan_sha256,
                "planned_unit_count": len(plan.units),
            },
        )
    ]
    for attempt in run.attempt_refs:
        expected_specs.append(
            (
                "attempt_started",
                {
                    "event_type": "attempt_started",
                    "dimension_id": attempt.dimension_id,
                    "attempt_ref": attempt.attempt_ref,
                },
            )
        )
        terminal_payload = {
            "event_type": (
                "attempt_completed"
                if attempt.status == "completed"
                else "attempt_failed"
            ),
            "dimension_id": attempt.dimension_id,
            "attempt_ref": attempt.attempt_ref,
        }
        if attempt.status == "failed":
            terminal_payload["reason_code"] = attempt.reason_code
        expected_specs.append((terminal_payload["event_type"], terminal_payload))
    if expected_security:
        expected_specs.append(
            (
                "security_failure_recorded",
                {
                    "event_type": "security_failure_recorded",
                    "reason_code": "tool_or_canary_output_forbidden",
                },
            )
        )
    for dimension_id in dimensions_with_outcomes:
        dimension_outcomes = [
            item for item in run.assessment_units if item.dimension_id == dimension_id
        ]
        expected_specs.append(
            (
                "dimension_parsed",
                {
                    "event_type": "dimension_parsed",
                    "dimension_id": dimension_id,
                    "disposed_unit_count": len(dimension_outcomes),
                },
            )
        )
        for outcome in dimension_outcomes:
            expected_specs.append(
                (
                    "unit_disposition_recorded",
                    {
                        "event_type": "unit_disposition_recorded",
                        "assessment_unit_id": outcome.assessment_unit_id,
                        "disposition": outcome.disposition,
                        "finding_ids": outcome.finding_ids,
                        "handoff_ids": outcome.handoff_ids,
                    },
                )
            )
            for finding_id in outcome.finding_ids:
                if finding_id in accepted_ids:
                    expected_specs.append(
                        (
                            "finding_accepted",
                            {
                                "event_type": "finding_accepted",
                                "finding_id": finding_id,
                                "assessment_unit_id": outcome.assessment_unit_id,
                            },
                        )
                    )
                elif finding_id in rejected_ids:
                    expected_specs.append(
                        (
                            "finding_rejected",
                            {
                                "event_type": "finding_rejected",
                                "finding_id": finding_id,
                                "assessment_unit_id": outcome.assessment_unit_id,
                                "reason_codes": report.reason_codes,
                            },
                        )
                    )
            for handoff_id in outcome.handoff_ids:
                if handoff_id in recorded_handoffs:
                    expected_specs.append(
                        (
                            "o3_handoff_recorded",
                            {
                                "event_type": "o3_handoff_recorded",
                                "handoff_id": handoff_id,
                                "assessment_unit_id": outcome.assessment_unit_id,
                            },
                        )
                    )
    expected_specs.append((final.event_type, expected_final))
    actual_specs = [
        (item.event_type, item.payload.model_dump(mode="json")) for item in events
    ]
    if actual_specs != expected_specs:
        _composition_witness_mismatch()
    expected_report = ValidationReport(
        schema_version=VALIDATION_REPORT_SCHEMA_ID,
        run_id=run.run_id,
        trial_id=run.trial_id,
        validation_status=(
            "accepted"
            if run.run_status == "completed"
            else "rejected"
            if run.run_status in {"validation_failed", "security_failed"}
            else "incomplete"
        ),
        reason_codes=report.reason_codes,
        accepted_finding_ids=[item.finding_id for item in run.findings],
        rejected_finding_ids=sorted(item.payload.finding_id for item in rejected),
        planned_unit_count=len(plan.units),
        disposed_unit_count=counts["disposed_unit_count"],
        finding_count=counts["finding_count"],
        abstention_count=counts["abstention_count"],
        handoff_count=counts["handoff_count"],
        raw_attempt_refs=[item.attempt_ref for item in run.attempt_refs],
    )
    if canonical_json_bytes(expected_report) != canonical_json_bytes(report):
        _composition_witness_mismatch()


def verify_laj_composition_witness(
    witness: LajCompositionWitness,
) -> LajCompositionWitness:
    try:
        strict_witness = LajCompositionWitness.model_validate(
            witness.model_dump(mode="json")
        )
    except (AttributeError, ValidationError, ValueError) as exc:
        raise SemanticEvaluatorError("composition_witness_mismatch") from exc
    if canonical_json_bytes(strict_witness) != canonical_json_bytes(
        witness
    ) or witness.witness_sha256 != canonical_model_sha256(
        witness, exclude=("witness_sha256",)
    ):
        _composition_witness_mismatch()
    binding = witness.input_binding
    reader = witness.reader_artifact
    context = witness.bounded_context
    manifest = witness.instrument_manifest
    plan = witness.assessment_plan
    run = witness.run
    report = witness.validation_report
    try:
        validate_frozen_assessment_plan(plan)
    except SemanticEvaluatorError:
        _composition_witness_mismatch()
    binding_identity = [
        binding.trial_id,
        binding.report_sha256,
        binding.normalized_text_sha256,
        binding.bounded_context_sha256,
        binding.profile_sha256,
        binding.instrument_config_sha256,
    ]
    expected_binding_id = f"binding-{canonical_sha256(binding_identity)[:12]}"
    if (
        binding.input_binding_sha256
        != canonical_model_sha256(binding, exclude=("input_binding_sha256",))
        or binding.binding_id != expected_binding_id
        or binding.public_data_attestation is not True
        or binding.private_or_confidential_material is not False
        or reader.report_sha256 != binding.report_sha256
        or reader.normalized_text_sha256 != binding.normalized_text_sha256
        or context.context_sha256 != compute_bounded_context_sha256(context)
        or context.context_sha256 != binding.bounded_context_sha256
        or context.language != binding.language
        or context.data_class != binding.data_class
        or plan.trial_id != binding.trial_id
        or plan.report_sha256 != binding.report_sha256
        or plan.profile_sha256 != binding.profile_sha256
        or manifest.instrument_config_sha256 != binding.instrument_config_sha256
        or manifest.profile_sha256 != binding.profile_sha256
        or manifest.instrument_sha256
        != canonical_sha256(
            canonical_model_payload(manifest, exclude=("instrument_sha256",))
        )
        or run.run_id
        != derive_run_id(
            input_binding_sha256=binding.input_binding_sha256,
            assessment_plan_sha256=plan.assessment_plan_sha256,
            instrument_sha256=manifest.instrument_sha256,
        )
        or run.trial_id != binding.trial_id
        or run.report_sha256 != binding.report_sha256
        or run.bounded_context_sha256 != binding.bounded_context_sha256
        or run.profile_sha256 != binding.profile_sha256
        or run.instrument_sha256 != manifest.instrument_sha256
        or run.assessment_plan_sha256 != plan.assessment_plan_sha256
        or report.run_id != run.run_id
        or report.trial_id != run.trial_id
    ):
        _composition_witness_mismatch()

    plan_by_id = {item.assessment_unit_id: item for item in plan.units}
    outcome_by_id = {item.assessment_unit_id: item for item in run.assessment_units}
    if len(outcome_by_id) != len(run.assessment_units) or not set(
        outcome_by_id
    ).issubset(plan_by_id):
        _composition_witness_mismatch()
    terminal = _terminal_attempts(run.attempt_refs)
    missing_dimensions: set[str] = set()
    for dimension_id in dict.fromkeys(item.dimension_id for item in plan.units):
        expected_units = {
            item.assessment_unit_id
            for item in plan.units
            if item.dimension_id == dimension_id
        }
        observed_units = expected_units & set(outcome_by_id)
        final_attempt = terminal.get(dimension_id)
        if observed_units:
            if (
                observed_units != expected_units
                or final_attempt is None
                or (final_attempt.status != "completed")
            ):
                _composition_witness_mismatch()
            if any(
                outcome_by_id[item].attempt_ref != final_attempt.attempt_ref
                for item in observed_units
            ):
                _composition_witness_mismatch()
        else:
            missing_dimensions.add(dimension_id)
            if final_attempt is None or final_attempt.status != "failed":
                _composition_witness_mismatch()
    expected_outcome_order = [
        item.assessment_unit_id
        for item in plan.units
        if item.dimension_id not in missing_dimensions
    ]
    if [item.assessment_unit_id for item in run.assessment_units] != (
        expected_outcome_order
    ):
        _composition_witness_mismatch()
    terminal_failure_reasons = {
        terminal[dimension_id].reason_code
        for dimension_id in missing_dimensions
        if terminal[dimension_id].reason_code is not None
    }
    if not terminal_failure_reasons.issubset(report.reason_codes):
        _composition_witness_mismatch()
    for outcome in run.assessment_units:
        unit = plan_by_id[outcome.assessment_unit_id]
        if (
            outcome.dimension_id != unit.dimension_id
            or outcome.sub_aspect_id != unit.sub_aspect_id
        ):
            _composition_witness_mismatch()
    finding_by_id = {item.finding_id: item for item in run.findings}
    handoff_by_id = {item.handoff_id: item for item in run.handoffs}
    if len(finding_by_id) != len(run.findings) or len(handoff_by_id) != len(
        run.handoffs
    ):
        _composition_witness_mismatch()
    for outcome in run.assessment_units:
        unit = plan_by_id[outcome.assessment_unit_id]
        for ordinal, finding_id in enumerate(outcome.finding_ids):
            finding = finding_by_id.get(finding_id)
            if finding is None:
                if finding_id not in report.rejected_finding_ids:
                    _composition_witness_mismatch()
                continue
            draft_payload = finding.model_dump(
                mode="json", exclude={"finding_id", "status"}
            )
            if finding_id != derive_finding_id(
                assessment_unit_id=outcome.assessment_unit_id,
                ordinal=ordinal,
                proposal_identity=draft_payload,
            ) or _validate_finding(
                finding,
                unit=unit,
                artifact=reader,
                context=context,
            ):
                _composition_witness_mismatch()
        for ordinal, handoff_id in enumerate(outcome.handoff_ids):
            handoff = handoff_by_id.get(handoff_id)
            if handoff is None:
                _composition_witness_mismatch()
            handoff_payload = handoff.model_dump(mode="json", exclude={"handoff_id"})
            if handoff_id != derive_handoff_id(
                assessment_unit_id=outcome.assessment_unit_id,
                ordinal=ordinal,
                handoff_identity=handoff_payload,
            ) or _validate_handoff(
                handoff,
                artifact=reader,
                context=context,
                scope_class=unit.scope_class,
                eligible_requirement_types=set(unit.eligible_requirement_types),
            ):
                _composition_witness_mismatch()
    if set(finding_by_id) != {
        finding_id
        for outcome in run.assessment_units
        for finding_id in outcome.finding_ids
        if finding_id not in set(report.rejected_finding_ids)
    }:
        _composition_witness_mismatch()
    if set(handoff_by_id) != {
        handoff_id
        for outcome in run.assessment_units
        for handoff_id in outcome.handoff_ids
    }:
        _composition_witness_mismatch()
    expected_status = (
        "security_failed"
        if "tool_or_canary_output_forbidden" in report.reason_codes
        else "incomplete"
        if missing_dimensions
        else "validation_failed"
        if report.reason_codes
        else "completed"
    )
    legal_pairs = {
        ("completed", "accepted"),
        ("incomplete", "incomplete"),
        ("validation_failed", "rejected"),
        ("security_failed", "rejected"),
    }
    if (
        run.run_status != expected_status
        or (run.run_status, report.validation_status) not in legal_pairs
    ):
        _composition_witness_mismatch()
    _verify_event_relations(witness)
    return strict_witness


__all__ = [
    "AssembledRun",
    "DimensionEvidence",
    "DimensionValidationResult",
    "VALIDATOR_VERSION",
    "assemble_semantic_assessment_run",
    "build_validation_events",
    "event_stream_bytes",
    "make_semantic_evaluator_event",
    "recompute_event_counts",
    "validate_dimension_response",
    "verify_laj_composition_witness",
]
