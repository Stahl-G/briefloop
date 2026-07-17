"""Table-driven deterministic validation for Semantic Evaluator proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import TypeAdapter, ValidationError

from multi_agent_brief.contracts.v2 import ContractId
from multi_agent_brief.semantic_evaluator.contracts import (
    EVENT_SCHEMA_ID,
    LAJ_COMPOSITION_WITNESS_SCHEMA_ID,
    RUN_SCHEMA_ID,
    VALIDATION_REPORT_SCHEMA_ID,
    AdmittedReportEvidence,
    AbstainConflictingContextResult,
    AbstainInsufficientContextResult,
    AbstainUnableToAssessResult,
    AssessmentPlan,
    AssessmentUnitOutcome,
    AttemptRef,
    BoundedContext,
    DimensionAttemptEvidence,
    DimensionResponse,
    FindingDraft,
    FindingEmittedResult,
    FindingProposal,
    InputBinding,
    InstrumentConfig,
    InstrumentManifest,
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
    NormalizedReader,
    bounded_context_sha256 as compute_bounded_context_sha256,
    verify_admitted_report_evidence,
    verify_bounded_context,
    replay_reader_artifact,
    replay_span,
)
from multi_agent_brief.semantic_evaluator.parser import (
    FORBIDDEN_AUTHORITY_KEYS,
    FORBIDDEN_SECURITY_KEYS,
    find_forbidden_keys,
    parse_dimension_response,
)
from multi_agent_brief.semantic_evaluator.prompts import (
    FrozenDimensionPrompt,
    _strict_frozen_dimension_prompt,
    build_dimension_prompt,
    derive_forbidden_canary_values,
)
from multi_agent_brief.semantic_evaluator.resources import EvaluatorResourceError
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_sha256,
    canonical_sha256,
    sha256_bytes,
    strict_model_payload,
)
from multi_agent_brief.semantic_evaluator.unit_planner import (
    build_assessment_plan,
    derive_attempt_ref,
    derive_finding_id,
    derive_handoff_id,
    derive_run_id,
    validate_frozen_assessment_plan,
)


VALIDATOR_VERSION = "dimension_validator_v3"
_CONTRACT_ID_ADAPTER = TypeAdapter(ContractId)


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
        strict_draft = FindingDraft.model_validate(strict_model_payload(draft))
    except ValidationError as exc:
        raise SemanticEvaluatorError(
            "raw_response_binding_mismatch",
            violations=value_free_violations(exc),
        ) from exc
    identity = strict_draft.model_dump(mode="json", warnings="error")
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
        strict_draft = O3HandoffDraft.model_validate(strict_model_payload(draft))
    except ValidationError as exc:
        raise SemanticEvaluatorError(
            "raw_response_binding_mismatch",
            violations=value_free_violations(exc),
        ) from exc
    identity = strict_draft.model_dump(mode="json", warnings="error")
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
    _loaded_profile: Any | None = None,
) -> DimensionValidationResult:
    response_invalid = False
    violations = ()
    try:
        response = DimensionResponse.model_validate(strict_model_payload(response))
    except Exception as exc:
        if isinstance(exc, ValidationError):
            violations = value_free_violations(exc)
        response_invalid = True
    if response_invalid:
        raise SemanticEvaluatorError(
            "raw_response_binding_mismatch",
            violations=violations,
        ) from None
    roots_invalid = False
    try:
        strict_plan = AssessmentPlan.model_validate(strict_model_payload(plan))
        strict_reader = ReaderArtifact.model_validate(
            strict_model_payload(reader_artifact)
        )
        strict_context = BoundedContext.model_validate(
            strict_model_payload(bounded_context)
        )
        roots_invalid = any(
            canonical_json_bytes(strict) != canonical_json_bytes(observed)
            for strict, observed in (
                (strict_plan, plan),
                (strict_reader, reader_artifact),
                (strict_context, bounded_context),
            )
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        roots_invalid = True
    if roots_invalid:
        raise SemanticEvaluatorError("run_binding_mismatch") from None
    plan = strict_plan
    reader_artifact = strict_reader
    bounded_context = strict_context
    reasons: set[str] = set()
    canaries: tuple[str, ...] = ()
    try:
        validate_frozen_assessment_plan(plan, loaded_profile=_loaded_profile)
        bounded_context = verify_bounded_context(bounded_context)
        canaries = derive_forbidden_canary_values(
            assessment_plan_sha256=plan.assessment_plan_sha256,
            bounded_context_sha256=bounded_context.context_sha256,
            dimension_id=expected_dimension_id,
        )
    except (SemanticEvaluatorError, ValueError):
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
    result: SemanticEvaluatorEvent | None = None
    try:
        payload_data = dict(payload)
        if "event_type" in payload_data:
            if payload_data.pop("event_type") != event_type:
                raise ValueError("event_type_mismatch")
        typed_payload = {"event_type": event_type, **payload_data}
        event_id = f"event-{canonical_sha256([run_id, trial_id, sequence, event_type, typed_payload])[:12]}"
        result = SemanticEvaluatorEvent.model_validate(
            {
                "schema_version": EVENT_SCHEMA_ID,
                "event_id": event_id,
                "sequence": sequence,
                "run_id": run_id,
                "trial_id": trial_id,
                "event_type": event_type,
                "payload": typed_payload,
            }
        )
    except (AttributeError, KeyError, TypeError, ValueError, SemanticEvaluatorError):
        pass
    if result is None:
        raise SemanticEvaluatorError("event_sequence_invalid") from None
    return result


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
            {
                "dimension_id": attempt.dimension_id,
                "attempt_ref": attempt.attempt_ref,
                "attempt_ordinal": attempt.attempt_ordinal,
                "prompt_request_sha256": attempt.prompt_request_sha256,
            },
        )
        if attempt.status == "completed":
            add(
                "attempt_completed",
                {
                    "dimension_id": attempt.dimension_id,
                    "attempt_ref": attempt.attempt_ref,
                    "attempt_ordinal": attempt.attempt_ordinal,
                    "prompt_request_sha256": attempt.prompt_request_sha256,
                },
            )
        else:
            add(
                "attempt_failed",
                {
                    "dimension_id": attempt.dimension_id,
                    "attempt_ref": attempt.attempt_ref,
                    "attempt_ordinal": attempt.attempt_ordinal,
                    "prompt_request_sha256": attempt.prompt_request_sha256,
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
                    attempt_ordinal=item.payload.attempt_ordinal,
                    prompt_request_sha256=item.payload.prompt_request_sha256,
                    status="completed",
                    reason_code=None,
                )
            )
        elif item.event_type == "attempt_failed":
            attempts.append(
                AttemptRef(
                    attempt_ref=item.payload.attempt_ref,
                    dimension_id=item.payload.dimension_id,
                    attempt_ordinal=item.payload.attempt_ordinal,
                    prompt_request_sha256=item.payload.prompt_request_sha256,
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
        and final_status in {"parser_failed", "validation_failed", "security_failed"}
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


# RECUT2_DERIVATION: assembly and replay share this one root-to-projection path.


@dataclass(frozen=True)
class _ReplayRoots:
    report_evidence: AdmittedReportEvidence
    reader: NormalizedReader
    bounded_context: BoundedContext
    instrument_config: InstrumentConfig
    input_binding: InputBinding
    instrument_manifest: InstrumentManifest
    assessment_plan: AssessmentPlan
    prompts: tuple[Any, ...]
    instrument_snapshot: Any


@dataclass(frozen=True)
class _DerivedProjection:
    run: SemanticAssessmentRun
    validation_report: ValidationReport
    events: tuple[SemanticEvaluatorEvent, ...]


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


def make_dimension_attempt_evidence(
    *,
    trial_id: str,
    prompt: FrozenDimensionPrompt,
    attempt_ordinal: int,
    status: str,
    raw_response_bytes: bytes | None = None,
    reason_code: str | None = None,
) -> DimensionAttemptEvidence:
    result: DimensionAttemptEvidence | None = None
    try:
        if type(trial_id) is not str:
            raise TypeError("trial_id_invalid")
        strict_trial_id = _CONTRACT_ID_ADAPTER.validate_python(trial_id, strict=True)
        strict_prompt = _strict_frozen_dimension_prompt(prompt)
        if raw_response_bytes is not None and type(raw_response_bytes) is not bytes:
            raise TypeError("raw_response_invalid")
        attempt_ref = derive_attempt_ref(
            trial_id=strict_trial_id,
            dimension_id=strict_prompt.dimension_id,
            attempt_ordinal=attempt_ordinal,
            prompt_request_sha256=strict_prompt.request_sha256,
        )
        raw_hex = raw_response_bytes.hex() if raw_response_bytes is not None else None
        payload = {
            "attempt_ref": attempt_ref,
            "dimension_id": strict_prompt.dimension_id,
            "attempt_ordinal": attempt_ordinal,
            "prompt_request_sha256": strict_prompt.request_sha256,
            "status": status,
            "reason_code": reason_code,
            "raw_response_bytes_hex": raw_hex,
            "raw_response_sha256": (
                sha256_bytes(raw_response_bytes)
                if raw_response_bytes is not None
                else None
            ),
            "forbidden_canary_values": list(strict_prompt.forbidden_canary_values),
        }
        result = DimensionAttemptEvidence.model_validate(
            {**payload, "evidence_sha256": canonical_sha256(payload)}
        )
    except (AttributeError, KeyError, TypeError, ValueError, SemanticEvaluatorError):
        pass
    if result is None:
        raise SemanticEvaluatorError("assessment_evidence_mismatch") from None
    return result


def _verify_root_bundle(
    *,
    report_evidence: AdmittedReportEvidence,
    reader_artifact: ReaderArtifact,
    bounded_context: BoundedContext,
    instrument_config: InstrumentConfig,
    input_binding: InputBinding,
    instrument_manifest: InstrumentManifest,
    assessment_plan: AssessmentPlan,
    retained_prompts: Iterable[Any] | None,
    retained_prompt_hashes: Iterable[str] | None,
    mismatch_reason: str,
    instrument_snapshot: Any | None = None,
    include_baseline: bool = False,
) -> _ReplayRoots:
    root_failure_reason: str | None = None
    try:
        strict_report = AdmittedReportEvidence.model_validate(
            strict_model_payload(report_evidence)
        )
        reader = verify_admitted_report_evidence(
            strict_report,
            reader_artifact=reader_artifact,
        )
        context = verify_bounded_context(bounded_context)
        config = InstrumentConfig.model_validate(
            strict_model_payload(instrument_config)
        )
        binding = InputBinding.model_validate(strict_model_payload(input_binding))
        plan = AssessmentPlan.model_validate(strict_model_payload(assessment_plan))
    except SemanticEvaluatorError as exc:
        root_failure_reason = (
            "instrument_manifest_mismatch"
            if mismatch_reason == "run_binding_mismatch"
            and exc.reason_code == "instrument_manifest_mismatch"
            else mismatch_reason
        )
    except Exception:
        root_failure_reason = mismatch_reason
    if root_failure_reason is not None:
        raise SemanticEvaluatorError(root_failure_reason) from None

    manifest_failed = False
    try:
        manifest = InstrumentManifest.model_validate(
            strict_model_payload(instrument_manifest)
        )
    except Exception:
        manifest_failed = True
    if manifest_failed:
        reason = (
            "instrument_manifest_mismatch"
            if mismatch_reason == "run_binding_mismatch"
            else mismatch_reason
        )
        raise SemanticEvaluatorError(reason) from None

    acquisition_failure_reason: str | None = None
    try:
        from multi_agent_brief.semantic_evaluator.instrument import (
            _InstrumentSnapshot,
            _acquire_instrument_snapshot,
            verify_instrument_manifest,
        )
        from multi_agent_brief.semantic_evaluator.admission import build_input_binding

        snapshot = (
            _acquire_instrument_snapshot(
                config,
                include_baseline=include_baseline,
            )
            if instrument_snapshot is None
            else instrument_snapshot
        )
        if not isinstance(snapshot, _InstrumentSnapshot):
            raise SemanticEvaluatorError(mismatch_reason)
        verify_instrument_manifest(
            manifest,
            config,
            _snapshot=snapshot,
        )
        loaded_profile = snapshot.resources.loaded_profile
    except EvaluatorResourceError:
        acquisition_failure_reason = (
            "instrument_manifest_mismatch"
            if mismatch_reason == "run_binding_mismatch"
            else mismatch_reason
        )
    except SemanticEvaluatorError as exc:
        acquisition_failure_reason = (
            "instrument_manifest_mismatch"
            if mismatch_reason == "run_binding_mismatch"
            and exc.reason_code == "instrument_manifest_mismatch"
            else mismatch_reason
        )
    except (AttributeError, TypeError, ValueError):
        acquisition_failure_reason = mismatch_reason
    if acquisition_failure_reason is not None:
        raise SemanticEvaluatorError(acquisition_failure_reason) from None

    replay_failed = False
    try:
        expected_binding = build_input_binding(
            trial_id=binding.trial_id,
            reader=reader,
            context=context,
            profile_sha256=loaded_profile.profile_sha256,
            config_sha256=canonical_model_sha256(config),
            public_data_attestation=True,
            private_or_confidential_material=False,
        )
        expected_plan = build_assessment_plan(
            trial_id=binding.trial_id,
            report_sha256=reader.artifact.report_sha256,
            profile=loaded_profile.profile,
            profile_sha256=loaded_profile.profile_sha256,
        )
    except Exception:
        replay_failed = True
    if replay_failed:
        raise SemanticEvaluatorError(mismatch_reason) from None

    try:
        expected_prompts = tuple(
            build_dimension_prompt(
                reader_artifact=reader.artifact,
                normalized_text=reader.normalized_text,
                bounded_context=context,
                dimension=dimension,
                assessment_plan=expected_plan,
                _resource_snapshot=snapshot.resources,
            )
            for dimension in loaded_profile.profile.dimensions
        )
    except EvaluatorResourceError:
        reason = (
            "instrument_manifest_mismatch"
            if mismatch_reason == "run_binding_mismatch"
            else mismatch_reason
        )
        raise SemanticEvaluatorError(reason) from None
    except (AttributeError, SemanticEvaluatorError, TypeError, ValueError):
        raise SemanticEvaluatorError(mismatch_reason) from None

    exact_pairs = (
        (strict_report, report_evidence),
        (reader.artifact, reader_artifact),
        (context, bounded_context),
        (config, instrument_config),
        (expected_binding, binding),
        (expected_plan, plan),
    )
    if any(
        canonical_json_bytes(expected) != canonical_json_bytes(observed)
        for expected, observed in exact_pairs
    ):
        raise SemanticEvaluatorError(mismatch_reason)
    if (
        binding.instrument_config_sha256 != canonical_model_sha256(config)
        or manifest.instrument_config_sha256 != binding.instrument_config_sha256
        or manifest.profile_sha256 != binding.profile_sha256
    ):
        raise SemanticEvaluatorError(mismatch_reason)
    if retained_prompts is not None:
        observed_prompts = tuple(retained_prompts)
        if observed_prompts != expected_prompts:
            raise SemanticEvaluatorError(mismatch_reason)
    if retained_prompt_hashes is not None and tuple(retained_prompt_hashes) != tuple(
        item.request_sha256 for item in expected_prompts
    ):
        raise SemanticEvaluatorError(mismatch_reason)
    return _ReplayRoots(
        report_evidence=strict_report,
        reader=reader,
        bounded_context=context,
        instrument_config=config,
        input_binding=binding,
        instrument_manifest=manifest,
        assessment_plan=plan,
        prompts=expected_prompts,
        instrument_snapshot=snapshot,
    )


def _require_admission(admission: Any) -> _ReplayRoots:
    from multi_agent_brief.semantic_evaluator.admission import AdmissionDecision

    if not isinstance(admission, AdmissionDecision) or not admission.admitted:
        raise SemanticEvaluatorError("run_binding_mismatch")
    required = (
        admission.report_evidence,
        admission.reader,
        admission.bounded_context,
        admission.instrument_config,
        admission.input_binding,
        admission.instrument_manifest,
        admission.assessment_plan,
    )
    if any(item is None for item in required):
        raise SemanticEvaluatorError("run_binding_mismatch")
    return _verify_root_bundle(
        report_evidence=admission.report_evidence,
        reader_artifact=admission.reader.artifact,
        bounded_context=admission.bounded_context,
        instrument_config=admission.instrument_config,
        input_binding=admission.input_binding,
        instrument_manifest=admission.instrument_manifest,
        assessment_plan=admission.assessment_plan,
        retained_prompts=admission.prompts,
        retained_prompt_hashes=admission.prompt_request_sha256s,
        mismatch_reason="run_binding_mismatch",
        instrument_snapshot=admission._instrument_snapshot,
    )


def _strict_attempt_evidence(
    evidence: Iterable[DimensionAttemptEvidence],
    *,
    roots: _ReplayRoots,
) -> tuple[DimensionAttemptEvidence, ...]:
    observed: tuple[DimensionAttemptEvidence, ...] = ()
    strict: tuple[DimensionAttemptEvidence, ...] = ()
    evidence_invalid = False
    try:
        observed = tuple(evidence)
        strict = tuple(
            DimensionAttemptEvidence.model_validate(strict_model_payload(item))
            for item in observed
        )
    except Exception:
        evidence_invalid = True
    if evidence_invalid:
        raise SemanticEvaluatorError("assessment_evidence_mismatch") from None
    if not strict or any(
        canonical_json_bytes(left) != canonical_json_bytes(right)
        for left, right in zip(strict, observed)
    ):
        raise SemanticEvaluatorError("assessment_evidence_mismatch")

    prompt_by_dimension = {item.dimension_id: item for item in roots.prompts}
    profile_dimensions = list(prompt_by_dimension)
    grouped: dict[str, list[DimensionAttemptEvidence]] = {
        dimension_id: [] for dimension_id in profile_dimensions
    }
    for item in strict:
        if item.dimension_id not in grouped:
            raise SemanticEvaluatorError("assessment_evidence_mismatch")
        grouped[item.dimension_id].append(item)
        expected_ref = derive_attempt_ref(
            trial_id=roots.input_binding.trial_id,
            dimension_id=item.dimension_id,
            attempt_ordinal=item.attempt_ordinal,
            prompt_request_sha256=item.prompt_request_sha256,
        )
        if (
            item.attempt_ref != expected_ref
            or item.prompt_request_sha256
            != prompt_by_dimension[item.dimension_id].request_sha256
            or tuple(item.forbidden_canary_values)
            != prompt_by_dimension[item.dimension_id].forbidden_canary_values
            or item.evidence_sha256
            != canonical_model_sha256(item, exclude=("evidence_sha256",))
        ):
            raise SemanticEvaluatorError("assessment_evidence_mismatch")
        if item.status == "completed":
            raw: bytes | None = None
            try:
                raw = bytes.fromhex(item.raw_response_bytes_hex or "")
            except ValueError:
                pass
            if raw is None:
                raise SemanticEvaluatorError("assessment_evidence_mismatch") from None
            if (
                raw.hex() != item.raw_response_bytes_hex
                or sha256_bytes(raw) != item.raw_response_sha256
            ):
                raise SemanticEvaluatorError("assessment_evidence_mismatch")

    expected_order = tuple(
        item for dimension_id in profile_dimensions for item in grouped[dimension_id]
    )
    if strict != expected_order:
        raise SemanticEvaluatorError("assessment_evidence_mismatch")
    policy = roots.instrument_config.retry_policy
    for dimension_id in profile_dimensions:
        attempts = grouped[dimension_id]
        if not attempts:
            raise SemanticEvaluatorError("assessment_unit_failure_link_missing")
        if len(attempts) > policy.max_attempts or [
            item.attempt_ordinal for item in attempts
        ] != list(range(1, len(attempts) + 1)):
            raise SemanticEvaluatorError("assessment_evidence_mismatch")
        for prior in attempts[:-1]:
            if (
                prior.status != "failed"
                or prior.reason_code not in policy.retryable_reason_codes
            ):
                raise SemanticEvaluatorError("assessment_evidence_mismatch")
    return strict


def _attempt_refs(
    evidence: Iterable[DimensionAttemptEvidence],
) -> list[AttemptRef]:
    return [
        AttemptRef(
            attempt_ref=item.attempt_ref,
            dimension_id=item.dimension_id,
            attempt_ordinal=item.attempt_ordinal,
            prompt_request_sha256=item.prompt_request_sha256,
            status=item.status,
            reason_code=item.reason_code,
        )
        for item in evidence
    ]


def _derive_projection(
    *,
    roots: _ReplayRoots,
    dimension_attempt_evidence: Iterable[DimensionAttemptEvidence],
) -> _DerivedProjection:
    evidence = _strict_attempt_evidence(
        dimension_attempt_evidence,
        roots=roots,
    )
    attempts = _attempt_refs(evidence)
    terminal_by_dimension: dict[str, DimensionAttemptEvidence] = {}
    for item in evidence:
        terminal_by_dimension[item.dimension_id] = item

    parsed_by_dimension: dict[str, Any] = {}
    terminal_failure_reasons: set[str] = set()
    for prompt in roots.prompts:
        terminal = terminal_by_dimension[prompt.dimension_id]
        if terminal.status == "failed":
            terminal_failure_reasons.add(terminal.reason_code or "provider_failed")
            continue
        raw = bytes.fromhex(terminal.raw_response_bytes_hex or "")
        parsed_by_dimension[prompt.dimension_id] = parse_dimension_response(
            raw,
            forbidden_canary_values=prompt.forbidden_canary_values,
        )

    security_failure = any(
        "tool_or_canary_output_forbidden" in parsed.reason_codes
        for parsed in parsed_by_dimension.values()
    )
    results: list[DimensionValidationResult] = []
    parser_reasons: set[str]
    if security_failure:
        parser_reasons = {"tool_or_canary_output_forbidden"}
        terminal_failure_reasons.clear()
    else:
        parser_reasons = set()
        for prompt in roots.prompts:
            terminal = terminal_by_dimension[prompt.dimension_id]
            if terminal.status == "failed":
                continue
            parsed = parsed_by_dimension[prompt.dimension_id]
            if not parsed.ok:
                parser_reasons.update(parsed.reason_codes)
                continue
            result = validate_dimension_response(
                parsed.response,
                raw_object=parsed.raw_object,
                expected_dimension_id=prompt.dimension_id,
                plan=roots.assessment_plan,
                reader_artifact=roots.reader.artifact,
                bounded_context=roots.bounded_context,
                attempt_ref=terminal.attempt_ref,
                _loaded_profile=(roots.instrument_snapshot.resources.loaded_profile),
            )
            expected_ids = {
                item.assessment_unit_id
                for item in roots.assessment_plan.units
                if item.dimension_id == prompt.dimension_id
            }
            observed_ids = {item.assessment_unit_id for item in result.unit_outcomes}
            if observed_ids != expected_ids and not result.reason_codes:
                raise SemanticEvaluatorError("assessment_unit_failure_link_missing")
            results.append(result)

    outcomes = [item for result in results for item in result.unit_outcomes]
    findings = [item for result in results for item in result.accepted_findings]
    handoffs = [item for result in results for item in result.handoffs]
    rejected_ids = [item for result in results for item in result.rejected_finding_ids]
    _global_id_preflight(outcomes)
    if len({item.assessment_unit_id for item in outcomes}) != len(outcomes):
        raise SemanticEvaluatorError("assessment_unit_set_mismatch")
    validation_reasons = {code for result in results for code in result.reason_codes}
    reason_codes = set(validation_reasons)
    reason_codes.update(parser_reasons)
    reason_codes.update(terminal_failure_reasons)
    non_security_parser = bool(parser_reasons - {"tool_or_canary_output_forbidden"})
    terminal_failure_count = sum(
        item.status == "failed" for item in terminal_by_dimension.values()
    )
    if security_failure:
        run_status = "security_failed"
    elif non_security_parser:
        run_status = "parser_failed"
    elif terminal_failure_count == len(terminal_by_dimension):
        run_status = "provider_failed"
    elif terminal_failure_count:
        run_status = "incomplete"
    elif validation_reasons:
        run_status = "validation_failed"
    else:
        run_status = "completed"
    run_id = derive_run_id(
        input_binding_sha256=roots.input_binding.input_binding_sha256,
        assessment_plan_sha256=roots.assessment_plan.assessment_plan_sha256,
        instrument_sha256=roots.instrument_manifest.instrument_sha256,
    )
    events = build_validation_events(
        run_id=run_id,
        trial_id=roots.input_binding.trial_id,
        plan=roots.assessment_plan,
        results=results,
        attempt_refs=attempts,
        run_status=run_status,
        run_reason_codes=reason_codes,
    )
    try:
        run = SemanticAssessmentRun(
            schema_version=RUN_SCHEMA_ID,
            run_id=run_id,
            trial_id=roots.input_binding.trial_id,
            report_sha256=roots.input_binding.report_sha256,
            bounded_context_sha256=roots.input_binding.bounded_context_sha256,
            profile_sha256=roots.input_binding.profile_sha256,
            instrument_sha256=roots.instrument_manifest.instrument_sha256,
            assessment_plan_sha256=roots.assessment_plan.assessment_plan_sha256,
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
        else "incomplete"
        if run_status in {"incomplete", "provider_failed"}
        else "rejected"
    )
    report = ValidationReport(
        schema_version=VALIDATION_REPORT_SCHEMA_ID,
        run_id=run_id,
        trial_id=roots.input_binding.trial_id,
        validation_status=validation_status,
        reason_codes=sorted(reason_codes),
        accepted_finding_ids=[item.finding_id for item in findings],
        rejected_finding_ids=sorted(set(rejected_ids)),
        planned_unit_count=len(roots.assessment_plan.units),
        disposed_unit_count=counts["disposed_unit_count"],
        finding_count=counts["finding_count"],
        abstention_count=counts["abstention_count"],
        handoff_count=counts["handoff_count"],
        raw_attempt_refs=[item.attempt_ref for item in attempts],
    )
    return _DerivedProjection(run=run, validation_report=report, events=events)


def _build_witness(
    *,
    roots: _ReplayRoots,
    dimension_attempt_evidence: tuple[DimensionAttemptEvidence, ...],
    projection: _DerivedProjection,
) -> LajCompositionWitness:
    payload = {
        "schema_version": LAJ_COMPOSITION_WITNESS_SCHEMA_ID,
        "input_binding": roots.input_binding.model_dump(mode="json", warnings="error"),
        "report_evidence": roots.report_evidence.model_dump(
            mode="json", warnings="error"
        ),
        "reader_artifact": roots.reader.artifact.model_dump(
            mode="json", warnings="error"
        ),
        "bounded_context": roots.bounded_context.model_dump(
            mode="json", warnings="error"
        ),
        "instrument_config": roots.instrument_config.model_dump(
            mode="json", warnings="error"
        ),
        "instrument_manifest": roots.instrument_manifest.model_dump(
            mode="json", warnings="error"
        ),
        "assessment_plan": roots.assessment_plan.model_dump(
            mode="json", warnings="error"
        ),
        "dimension_attempt_evidence": [
            item.model_dump(mode="json", warnings="error")
            for item in dimension_attempt_evidence
        ],
        "run": projection.run.model_dump(mode="json", warnings="error"),
        "validation_report": projection.validation_report.model_dump(
            mode="json", warnings="error"
        ),
        "events": [
            item.model_dump(mode="json", warnings="error") for item in projection.events
        ],
    }
    return LajCompositionWitness.model_validate(
        {**payload, "witness_sha256": canonical_sha256(payload)}
    )


def assemble_semantic_assessment_run(
    *,
    admission: Any,
    dimension_attempt_evidence: Iterable[DimensionAttemptEvidence],
) -> AssembledRun:
    roots = _require_admission(admission)
    evidence = _strict_attempt_evidence(
        dimension_attempt_evidence,
        roots=roots,
    )
    projection = _derive_projection(
        roots=roots,
        dimension_attempt_evidence=evidence,
    )
    witness = _build_witness(
        roots=roots,
        dimension_attempt_evidence=evidence,
        projection=projection,
    )
    verified, _verified_roots = _verify_laj_composition_witness_with_roots(
        witness,
        instrument_snapshot=roots.instrument_snapshot,
    )
    return AssembledRun(
        run=projection.run,
        validation_report=projection.validation_report,
        events=projection.events,
        witness=verified,
    )


def _verify_laj_composition_witness_with_roots(
    witness: LajCompositionWitness,
    *,
    instrument_snapshot: Any | None = None,
    include_baseline: bool = False,
) -> tuple[LajCompositionWitness, _ReplayRoots]:
    verification_failed = False
    try:
        strict = LajCompositionWitness.model_validate(strict_model_payload(witness))
        if canonical_json_bytes(strict) != canonical_json_bytes(
            witness
        ) or strict.witness_sha256 != canonical_model_sha256(
            strict, exclude=("witness_sha256",)
        ):
            raise SemanticEvaluatorError("composition_witness_mismatch")
        roots = _verify_root_bundle(
            report_evidence=strict.report_evidence,
            reader_artifact=strict.reader_artifact,
            bounded_context=strict.bounded_context,
            instrument_config=strict.instrument_config,
            input_binding=strict.input_binding,
            instrument_manifest=strict.instrument_manifest,
            assessment_plan=strict.assessment_plan,
            retained_prompts=None,
            retained_prompt_hashes=None,
            mismatch_reason="composition_witness_mismatch",
            instrument_snapshot=instrument_snapshot,
            include_baseline=include_baseline,
        )
        projection = _derive_projection(
            roots=roots,
            dimension_attempt_evidence=strict.dimension_attempt_evidence,
        )
        if any(
            canonical_json_bytes(expected) != canonical_json_bytes(observed)
            for expected, observed in (
                (projection.run, strict.run),
                (projection.validation_report, strict.validation_report),
            )
        ) or canonical_json_bytes(
            [
                item.model_dump(mode="json", warnings="error")
                for item in projection.events
            ]
        ) != canonical_json_bytes(
            [item.model_dump(mode="json", warnings="error") for item in strict.events]
        ):
            raise SemanticEvaluatorError("composition_witness_mismatch")
    except Exception:
        verification_failed = True
    if verification_failed:
        raise SemanticEvaluatorError("composition_witness_mismatch") from None
    return strict, roots


def verify_laj_composition_witness(
    witness: LajCompositionWitness,
) -> LajCompositionWitness:
    strict, _roots = _verify_laj_composition_witness_with_roots(witness)
    return strict


__all__ = [
    "AssembledRun",
    "DimensionValidationResult",
    "VALIDATOR_VERSION",
    "assemble_semantic_assessment_run",
    "build_validation_events",
    "event_stream_bytes",
    "make_dimension_attempt_evidence",
    "make_semantic_evaluator_event",
    "recompute_event_counts",
    "validate_dimension_response",
    "verify_laj_composition_witness",
]
