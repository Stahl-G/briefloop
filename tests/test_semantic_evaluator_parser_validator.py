"""Strict parser, scope routing, span validation, and event replay tests."""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.semantic_evaluator.contracts import (
    DIMENSION_RESPONSE_SCHEMA_ID,
    AbstainConflictingContextResult,
    AbstainInsufficientContextResult,
    AbstainUnableToAssessResult,
    AttemptRef,
    BoundedRequirement,
    DimensionResponse,
    FindingEmittedResult,
    FindingProposal,
    NoFindingResult,
    O3Handoff,
    SpanLocator,
)
from multi_agent_brief.semantic_evaluator.normalization import (
    freeze_bounded_context,
    make_span_locator,
    normalize_markdown,
)
from multi_agent_brief.semantic_evaluator.parser import parse_dimension_response
from multi_agent_brief.semantic_evaluator.profile import load_profile
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes
from multi_agent_brief.semantic_evaluator.unit_planner import (
    build_assessment_plan,
    derive_finding_id,
    derive_handoff_id,
)
from multi_agent_brief.semantic_evaluator.validator import (
    assemble_semantic_assessment_run,
    recompute_event_counts,
    validate_dimension_response,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "semantic_evaluator"


def _case():
    reader = normalize_markdown(
        "# 合成报告\n\n当前状态为 HOLD。\n\n结论写为 READY。\n".encode(),
        artifact_id="reader-validator",
    )
    context = freeze_bounded_context(
        context_id="context-validator",
        data_class="synthetic",
        requirements=[
            BoundedRequirement(
                requirement_id="REQ-A",
                type="must_answer",
                text="说明当前状态。",
                source_locator="brief:B1",
            ),
            BoundedRequirement(
                requirement_id="REQ-B",
                type="must_include",
                text="包含下一步边界。",
                source_locator="brief:B2",
            ),
        ],
    )
    profile = load_profile()
    plan = build_assessment_plan(
        trial_id="trial-validator",
        report_sha256=reader.artifact.report_sha256,
        profile=profile.profile,
        profile_sha256=profile.profile_sha256,
    )
    return reader, context, profile, plan


def _finding(unit, span, *, requirement_ids=None, finding_id=None):
    requirement_ids = requirement_ids or []
    identity = [unit.assessment_unit_id, span.model_dump(mode="json"), requirement_ids]
    return FindingProposal(
        finding_id=finding_id
        or derive_finding_id(
            assessment_unit_id=unit.assessment_unit_id,
            ordinal=0,
            proposal_identity=identity,
        ),
        assessment_unit_id=unit.assessment_unit_id,
        status="proposal",
        scope_class=unit.scope_class,
        dimension_id=unit.dimension_id,
        severity="major",
        impact_scope="key_conclusion",
        report_spans=[span],
        context_requirement_ids=requirement_ids,
        observation="状态表述与结论不一致。",
        rationale="两个片段描述同一阶段但不能同时成立。",
        severity_basis="可能导致读者错误推进阶段。",
        confidence_basis=(
            "explicit_requirement_mismatch"
            if unit.scope_class == "O2"
            else "direct_cross_span_conflict"
        ),
        external_premise_disclosure="none",
        recommended_human_action="reconcile_status_language",
        suggested_rewrite=None,
    )


def _no_finding_response(plan, dimension_id: str) -> DimensionResponse:
    units = [item for item in plan.units if item.dimension_id == dimension_id]
    return DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=plan.trial_id,
        dimension_id=dimension_id,
        unit_results=[
            NoFindingResult(
                assessment_unit_id=item.assessment_unit_id,
                disposition="no_finding",
            )
            for item in units
        ],
    )


def _complete_no_finding_validation_case(*, inject_security: bool = False):
    reader, context, profile, plan = _case()
    results = []
    attempts = []
    for ordinal, dimension in enumerate(profile.profile.dimensions):
        response = _no_finding_response(plan, dimension.dimension_id)
        raw = response.model_dump(mode="json")
        if inject_security and ordinal == 0:
            raw["tool_calls"] = [{"name": "forbidden-synthetic-tool"}]
        attempt_ref = f"attempt-{dimension.dimension_id}"
        results.append(
            validate_dimension_response(
                response,
                raw_object=raw,
                expected_dimension_id=dimension.dimension_id,
                plan=plan,
                reader_artifact=reader.artifact,
                bounded_context=context,
                attempt_ref=attempt_ref,
            )
        )
        attempts.append(
            AttemptRef(
                attempt_ref=attempt_ref,
                dimension_id=dimension.dimension_id,
                status="completed",
                reason_code=None,
            )
        )
    return reader, context, profile, plan, results, attempts


def test_parser_accepts_one_strict_object_and_never_repairs_wrapped_json() -> None:
    _reader, _context, _profile, plan = _case()
    response = _no_finding_response(plan, "cross_section_consistency")
    parsed = parse_dimension_response(canonical_json_bytes(response))
    assert parsed.ok is True
    assert parsed.response == response
    wrapped = b"```json\n" + canonical_json_bytes(response) + b"\n```"
    assert parse_dimension_response(wrapped).reason_codes == ("parser_invalid_json",)
    malformed = (FIXTURE_ROOT / "malformed_response.txt").read_bytes()
    assert parse_dimension_response(malformed).reason_codes == ("parser_invalid_json",)
    assert parse_dimension_response(b"[]").reason_codes == (
        "parser_top_level_not_object",
    )


def test_parser_classifies_nested_authority_keys_before_generic_extra_failure() -> None:
    _reader, _context, _profile, plan = _case()
    payload = _no_finding_response(plan, "cross_section_consistency").model_dump(
        mode="json"
    )
    payload["metadata"] = {"nested": {"PASS": True}}
    parsed = parse_dimension_response(canonical_json_bytes(payload))
    assert parsed.reason_codes == ("authority_output_forbidden",)
    assert parsed.violations == ()


def test_valid_o1_finding_replays_span_and_preserves_explicit_dispositions() -> None:
    reader, context, _profile, plan = _case()
    units = [
        item for item in plan.units if item.dimension_id == "cross_section_consistency"
    ]
    span = make_span_locator(
        reader.artifact,
        block_id="B000002",
        start_char=0,
        end_char=len(reader.artifact.blocks[1].text),
    )
    finding = _finding(units[0], span)
    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=plan.trial_id,
        dimension_id="cross_section_consistency",
        unit_results=[
            FindingEmittedResult(
                assessment_unit_id=units[0].assessment_unit_id,
                disposition="finding_emitted",
                findings=[finding],
            ),
            NoFindingResult(
                assessment_unit_id=units[1].assessment_unit_id,
                disposition="no_finding",
            ),
            NoFindingResult(
                assessment_unit_id=units[2].assessment_unit_id,
                disposition="no_finding",
            ),
        ],
    )
    result = validate_dimension_response(
        response,
        raw_object=response.model_dump(mode="json"),
        expected_dimension_id="cross_section_consistency",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-o1",
    )
    assert result.accepted is True
    assert result.accepted_findings == (finding,)
    assert [item.disposition for item in result.unit_outcomes] == [
        "finding_emitted",
        "no_finding",
        "no_finding",
    ]


def test_bad_span_is_rejected_and_canary_value_is_security_failure() -> None:
    reader, context, _profile, plan = _case()
    units = [
        item for item in plan.units if item.dimension_id == "cross_section_consistency"
    ]
    good = make_span_locator(
        reader.artifact,
        block_id="B000002",
        start_char=0,
        end_char=1,
    )
    bad = SpanLocator.model_validate(
        {**good.model_dump(mode="json"), "excerpt_sha256": "0" * 64}
    )
    finding = _finding(units[0], bad)
    results = [
        FindingEmittedResult(
            assessment_unit_id=units[0].assessment_unit_id,
            disposition="finding_emitted",
            findings=[finding],
        ),
        *[
            NoFindingResult(
                assessment_unit_id=item.assessment_unit_id, disposition="no_finding"
            )
            for item in units[1:]
        ],
    ]
    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=plan.trial_id,
        dimension_id="cross_section_consistency",
        unit_results=results,
    )
    raw = response.model_dump(mode="json")
    raw["unit_results"][0]["findings"][0]["observation"] += " CANARY-SECRET"
    raw["CANARY-SECRET-AS-KEY"] = "synthetic"
    result = validate_dimension_response(
        response,
        raw_object=raw,
        expected_dimension_id="cross_section_consistency",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-invalid",
        forbidden_canary_values=("CANARY-SECRET",),
    )
    assert "span_excerpt_hash_mismatch" in result.reason_codes
    assert "tool_or_canary_output_forbidden" in result.reason_codes
    assert result.rejected_finding_ids == (finding.finding_id,)


def test_o2_requires_legal_requirement_binding() -> None:
    reader, context, _profile, plan = _case()
    units = [
        item for item in plan.units if item.dimension_id == "brief_requirement_coverage"
    ]
    span = make_span_locator(
        reader.artifact, block_id="B000002", start_char=0, end_char=1
    )
    finding = _finding(units[0], span, requirement_ids=["REQ-A"])
    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=plan.trial_id,
        dimension_id="brief_requirement_coverage",
        unit_results=[
            FindingEmittedResult(
                assessment_unit_id=units[0].assessment_unit_id,
                disposition="finding_emitted",
                findings=[finding],
            ),
            NoFindingResult(
                assessment_unit_id=units[1].assessment_unit_id, disposition="no_finding"
            ),
        ],
    )
    accepted = validate_dimension_response(
        response,
        raw_object=response.model_dump(mode="json"),
        expected_dimension_id="brief_requirement_coverage",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-o2",
    )
    assert accepted.accepted is True
    wrong = response.model_copy(deep=True)
    wrong.unit_results[0].findings[0].context_requirement_ids = ["REQ-B"]
    rejected = validate_dimension_response(
        wrong,
        raw_object=wrong.model_dump(mode="json"),
        expected_dimension_id="brief_requirement_coverage",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-o2-wrong",
    )
    assert "requirement_type_not_eligible" in rejected.reason_codes


def test_evidence_question_is_an_o3_handoff_not_a_truth_finding() -> None:
    reader, context, _profile, plan = _case()
    units = [
        item for item in plan.units if item.dimension_id == "cross_section_consistency"
    ]
    span = make_span_locator(
        reader.artifact, block_id="B000003", start_char=0, end_char=1
    )
    handoff = O3Handoff(
        handoff_id=derive_handoff_id(
            assessment_unit_id=units[0].assessment_unit_id,
            ordinal=0,
            handoff_identity="source-support-question",
        ),
        assessment_unit_id=units[0].assessment_unit_id,
        type="evidence_dependent_assessment",
        report_spans=[span],
        context_requirement_ids=[],
        reason="需要打开外部来源判断支持关系，超出 O1 范围。",
    )
    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=plan.trial_id,
        dimension_id="cross_section_consistency",
        unit_results=[
            AbstainUnableToAssessResult(
                assessment_unit_id=units[0].assessment_unit_id,
                disposition="abstain_unable_to_assess",
                reason_code="evidence_dependent_assessment",
                handoffs=[handoff],
            ),
            *[
                NoFindingResult(
                    assessment_unit_id=item.assessment_unit_id, disposition="no_finding"
                )
                for item in units[1:]
            ],
        ],
    )
    result = validate_dimension_response(
        response,
        raw_object=response.model_dump(mode="json"),
        expected_dimension_id="cross_section_consistency",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-o3",
    )
    assert result.accepted is True
    assert result.accepted_findings == ()
    assert result.handoffs == (handoff,)


def test_evidence_dependent_abstention_cannot_drop_the_o3_handoff() -> None:
    reader, context, _profile, plan = _case()
    units = [
        item for item in plan.units if item.dimension_id == "cross_section_consistency"
    ]
    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=plan.trial_id,
        dimension_id="cross_section_consistency",
        unit_results=[
            AbstainUnableToAssessResult(
                assessment_unit_id=units[0].assessment_unit_id,
                disposition="abstain_unable_to_assess",
                reason_code="evidence_dependent_assessment",
                handoffs=[],
            ),
            *[
                NoFindingResult(
                    assessment_unit_id=item.assessment_unit_id,
                    disposition="no_finding",
                )
                for item in units[1:]
            ],
        ],
    )
    result = validate_dimension_response(
        response,
        raw_object=response.model_dump(mode="json"),
        expected_dimension_id="cross_section_consistency",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-missing-o3-handoff",
    )
    assert result.reason_codes == ("evidence_dependent_handoff_required",)


def test_insufficient_and_conflicting_context_are_explicit_abstentions() -> None:
    reader, context, _profile, plan = _case()
    units = [
        item for item in plan.units if item.dimension_id == "cross_section_consistency"
    ]
    variants = (
        AbstainInsufficientContextResult(
            assessment_unit_id=units[0].assessment_unit_id,
            disposition="abstain_insufficient_context",
            reason_code="insufficient_context",
            handoffs=[],
        ),
        AbstainConflictingContextResult(
            assessment_unit_id=units[0].assessment_unit_id,
            disposition="abstain_conflicting_context",
            reason_code="conflicting_context",
            handoffs=[],
        ),
    )
    for first_result in variants:
        response = DimensionResponse(
            schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
            trial_id=plan.trial_id,
            dimension_id="cross_section_consistency",
            unit_results=[
                first_result,
                *[
                    NoFindingResult(
                        assessment_unit_id=item.assessment_unit_id,
                        disposition="no_finding",
                    )
                    for item in units[1:]
                ],
            ],
        )
        result = validate_dimension_response(
            response,
            raw_object=response.model_dump(mode="json"),
            expected_dimension_id="cross_section_consistency",
            plan=plan,
            reader_artifact=reader.artifact,
            bounded_context=context,
            attempt_ref=f"attempt-{first_result.disposition}",
        )
        assert result.accepted is True
        assert result.abstention_count == 1
        assert result.unit_outcomes[0].disposition == first_result.disposition


def test_raw_object_and_requested_dimension_are_bound_before_acceptance() -> None:
    reader, context, _profile, plan = _case()
    response = _no_finding_response(plan, "cross_section_consistency")
    wrong_dimension = response.model_copy(
        update={"dimension_id": "scope_definition_stability"}
    )
    dimension_result = validate_dimension_response(
        wrong_dimension,
        raw_object=wrong_dimension.model_dump(mode="json"),
        expected_dimension_id="cross_section_consistency",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-wrong-dimension",
    )
    assert "dimension_identity_mismatch" in dimension_result.reason_codes

    raw = response.model_dump(mode="json")
    raw["trial_id"] = "trial-raw-does-not-match-typed"
    raw_result = validate_dimension_response(
        response,
        raw_object=raw,
        expected_dimension_id="cross_section_consistency",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-raw-mismatch",
    )
    assert "raw_response_binding_mismatch" in raw_result.reason_codes

    forged_plan = plan.model_copy(update={"assessment_plan_sha256": "0" * 64})
    plan_result = validate_dimension_response(
        response,
        raw_object=response.model_dump(mode="json"),
        expected_dimension_id="cross_section_consistency",
        plan=forged_plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-forged-plan",
    )
    assert "run_binding_mismatch" in plan_result.reason_codes


def test_synthetic_events_recompute_complete_25_unit_run_counts() -> None:
    reader, context, profile, plan, results, attempts = (
        _complete_no_finding_validation_case()
    )
    assembled = assemble_semantic_assessment_run(
        run_id="run-synthetic-complete",
        trial_id=plan.trial_id,
        report_sha256=reader.artifact.report_sha256,
        bounded_context_sha256=context.context_sha256,
        profile_sha256=profile.profile_sha256,
        instrument_sha256="4" * 64,
        plan=plan,
        results=results,
        attempt_refs=attempts,
    )
    assert assembled.run.run_status == "completed"
    assert assembled.validation_report.validation_status == "accepted"
    assert assembled.validation_report.planned_unit_count == 25
    assert recompute_event_counts(assembled.events) == {
        "disposed_unit_count": 25,
        "finding_count": 0,
        "abstention_count": 0,
        "handoff_count": 0,
        "failure_count": 0,
    }
    assert [item.sequence for item in assembled.events] == list(
        range(1, len(assembled.events) + 1)
    )
    replay = assemble_semantic_assessment_run(
        run_id="run-synthetic-complete",
        trial_id=plan.trial_id,
        report_sha256=reader.artifact.report_sha256,
        bounded_context_sha256=context.context_sha256,
        profile_sha256=profile.profile_sha256,
        instrument_sha256="4" * 64,
        plan=plan,
        results=results,
        attempt_refs=attempts,
    )
    assert replay == assembled


def test_security_failure_has_precedence_and_a_recomputable_event() -> None:
    reader, context, profile, plan, results, attempts = (
        _complete_no_finding_validation_case(inject_security=True)
    )
    assembled = assemble_semantic_assessment_run(
        run_id="run-synthetic-security",
        trial_id=plan.trial_id,
        report_sha256=reader.artifact.report_sha256,
        bounded_context_sha256=context.context_sha256,
        profile_sha256=profile.profile_sha256,
        instrument_sha256="5" * 64,
        plan=plan,
        results=results,
        attempt_refs=attempts,
    )
    assert assembled.run.run_status == "security_failed"
    assert assembled.validation_report.validation_status == "rejected"
    assert "tool_or_canary_output_forbidden" in (
        assembled.validation_report.reason_codes
    )
    assert [item.event_type for item in assembled.events].count(
        "security_failure_recorded"
    ) == 1
    assert recompute_event_counts(assembled.events)["failure_count"] == 1


def test_failed_retry_attempt_does_not_turn_a_completed_dimension_into_failure() -> (
    None
):
    reader, context, profile, plan, results, attempts = (
        _complete_no_finding_validation_case()
    )
    retry_history = [
        AttemptRef(
            attempt_ref="attempt-retryable-failure",
            dimension_id="cross_section_consistency",
            status="failed",
            reason_code="provider_retryable_failure",
        ),
        *attempts,
    ]
    assembled = assemble_semantic_assessment_run(
        run_id="run-synthetic-retry",
        trial_id=plan.trial_id,
        report_sha256=reader.artifact.report_sha256,
        bounded_context_sha256=context.context_sha256,
        profile_sha256=profile.profile_sha256,
        instrument_sha256="6" * 64,
        plan=plan,
        results=results,
        attempt_refs=retry_history,
    )
    assert assembled.run.run_status == "completed"
    assert assembled.validation_report.validation_status == "accepted"
    assert recompute_event_counts(assembled.events)["failure_count"] == 1
