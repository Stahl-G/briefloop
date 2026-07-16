"""Strict parser, scope routing, span validation, and event replay tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.semantic_evaluator.admission import admit_inputs
import multi_agent_brief.semantic_evaluator.validator as validator_module
from multi_agent_brief.semantic_evaluator.contracts import (
    DIMENSION_RESPONSE_SCHEMA_ID,
    AbstainConflictingContextResult,
    AbstainInsufficientContextResult,
    AbstainUnableToAssessResult,
    AttemptRef,
    BoundedRequirement,
    DimensionResponse,
    FindingDraft,
    FindingEmittedResult,
    InstrumentConfig,
    NoFindingResult,
    O3HandoffDraft,
    SpanLocator,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import (
    freeze_bounded_context,
    make_span_locator,
    normalize_markdown,
)
from multi_agent_brief.semantic_evaluator.parser import parse_dimension_response
from multi_agent_brief.semantic_evaluator.profile import load_profile
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    sha256_bytes,
)
from multi_agent_brief.semantic_evaluator.unit_planner import (
    build_assessment_plan,
)
from multi_agent_brief.semantic_evaluator.validator import (
    DimensionEvidence,
    assemble_semantic_assessment_run,
    recompute_event_counts,
    validate_dimension_response,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "semantic_evaluator"
REPORT_BYTES = "# 合成报告\n\n当前状态为 HOLD。\n\n结论写为 READY。\n".encode()


class _Sizer:
    sizer_id = "fake-sizer"
    sizer_version = "v1"

    def count_tokens(self, *, system_text: str, user_text: str) -> int:
        return 10


def _admitted_case():
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
    decision = admit_inputs(
        report_bytes=REPORT_BYTES,
        declared_report_sha256=sha256_bytes(REPORT_BYTES),
        artifact_id="reader-validator",
        bounded_context=context,
        declared_bounded_context_sha256=context.context_sha256,
        instrument_config=InstrumentConfig.model_validate(
            InstrumentConfig.minimal_example
        ),
        trial_id="trial-validator",
        public_data_attestation=True,
        private_or_confidential_material=False,
        prompt_sizer=_Sizer(),
    )
    assert decision.admitted
    return decision.reader, context, profile, decision.assessment_plan, decision


def _case():
    reader, context, profile, plan, _decision = _admitted_case()
    return reader, context, profile, plan


def _finding(unit, span, *, requirement_ids=None):
    requirement_ids = requirement_ids or []
    return FindingDraft(
        assessment_unit_id=unit.assessment_unit_id,
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
    reader, context, profile, plan, decision = _admitted_case()
    evidence = []
    attempts = []
    for ordinal, dimension in enumerate(profile.profile.dimensions):
        response = _no_finding_response(plan, dimension.dimension_id)
        raw = response.model_dump(mode="json")
        if inject_security and ordinal == 0:
            raw["tool_calls"] = [{"name": "forbidden-synthetic-tool"}]
        attempt_ref = f"attempt-{dimension.dimension_id}"
        evidence.append(
            DimensionEvidence(
                response=response,
                raw_object=raw,
                expected_dimension_id=dimension.dimension_id,
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
    return reader, context, profile, plan, decision, evidence, attempts


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


def test_parser_rejects_duplicate_members_before_collapse_with_security_precedence() -> (
    None
):
    _reader, _context, _profile, plan = _case()
    response = _no_finding_response(plan, "cross_section_consistency")
    text = canonical_json_bytes(response).decode("utf-8")
    top_level_duplicate = text[:-1] + f',"trial_id":"{plan.trial_id}"}}'
    assert parse_dimension_response(top_level_duplicate.encode()).reason_codes == (
        "parser_duplicate_member",
    )
    unit_id = response.unit_results[0].assessment_unit_id
    nested_duplicate = text.replace(
        f'"assessment_unit_id":"{unit_id}"',
        f'"assessment_unit_id":"{unit_id}","assessment_unit_id":"{unit_id}"',
        1,
    )
    assert parse_dimension_response(nested_duplicate.encode()).reason_codes == (
        "parser_duplicate_member",
    )
    authority = b'{"pass":false,"pass":true}'
    assert parse_dimension_response(authority).reason_codes == (
        "authority_output_forbidden",
    )
    security = b'{"pass":false,"tool_calls":[],"pass":true}'
    assert parse_dimension_response(security).reason_codes == (
        "tool_or_canary_output_forbidden",
    )


def test_provider_supplied_finding_or_handoff_ids_are_schema_invalid() -> None:
    reader, _context, _profile, plan = _case()
    units = [
        item for item in plan.units if item.dimension_id == "cross_section_consistency"
    ]
    span = make_span_locator(
        reader.artifact,
        block_id="B000002",
        start_char=0,
        end_char=1,
    )
    finding_response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=plan.trial_id,
        dimension_id="cross_section_consistency",
        unit_results=[
            FindingEmittedResult(
                assessment_unit_id=units[0].assessment_unit_id,
                disposition="finding_emitted",
                findings=[_finding(units[0], span)],
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
    finding_payload = finding_response.model_dump(mode="json")
    finding_payload["unit_results"][0]["findings"][0].update(
        {"finding_id": "F-000000000001", "status": "proposal"}
    )
    assert parse_dimension_response(
        canonical_json_bytes(finding_payload)
    ).reason_codes == ("parser_schema_invalid",)

    handoff = O3HandoffDraft(
        assessment_unit_id=units[0].assessment_unit_id,
        type="evidence_dependent_assessment",
        report_spans=[span],
        context_requirement_ids=[],
        reason="需要外部证据，转人工复核。",
    )
    handoff_response = DimensionResponse(
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
                    assessment_unit_id=item.assessment_unit_id,
                    disposition="no_finding",
                )
                for item in units[1:]
            ],
        ],
    )
    handoff_payload = handoff_response.model_dump(mode="json")
    handoff_payload["unit_results"][0]["handoffs"][0]["handoff_id"] = "H-000000000001"
    assert parse_dimension_response(
        canonical_json_bytes(handoff_payload)
    ).reason_codes == ("parser_schema_invalid",)


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
    assert len(result.accepted_findings) == 1
    assert result.accepted_findings[0].model_dump(
        mode="json", exclude={"finding_id", "status"}
    ) == finding.model_dump(mode="json")
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
    assert len(result.rejected_finding_ids) == 1


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
    handoff = O3HandoffDraft(
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
    assert len(result.handoffs) == 1
    assert result.handoffs[0].model_dump(
        mode="json", exclude={"handoff_id"}
    ) == handoff.model_dump(mode="json")


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
    _reader, _context, _profile, plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_evidence=evidence,
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
        admission=decision,
        dimension_evidence=evidence,
        attempt_refs=attempts,
    )
    assert replay == assembled


def test_security_failure_has_precedence_and_a_recomputable_event() -> None:
    _reader, _context, _profile, _plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case(inject_security=True)
    )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_evidence=evidence,
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
    _reader, _context, _profile, _plan, decision, evidence, attempts = (
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
        admission=decision,
        dimension_evidence=evidence,
        attempt_refs=retry_history,
    )
    assert assembled.run.run_status == "completed"
    assert assembled.validation_report.validation_status == "accepted"
    assert recompute_event_counts(assembled.events)["failure_count"] == 0


def test_missing_dimension_requires_explicit_terminal_failed_attempt() -> None:
    _reader, _context, _profile, _plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    with pytest.raises(
        SemanticEvaluatorError,
        match="assessment_unit_failure_link_missing",
    ):
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_evidence=evidence[:-1],
            attempt_refs=attempts[:-1],
        )

    first = evidence[0]
    partial_response = first.response.model_copy(
        update={"unit_results": first.response.unit_results[:1]}
    )
    partial = DimensionEvidence(
        response=partial_response,
        raw_object=partial_response.model_dump(mode="json"),
        expected_dimension_id=first.expected_dimension_id,
        attempt_ref=first.attempt_ref,
    )
    with pytest.raises(
        SemanticEvaluatorError,
        match="assessment_unit_failure_link_missing",
    ):
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_evidence=[partial, *evidence[1:]],
            attempt_refs=attempts,
        )


def test_assembly_revalidates_evidence_and_rejects_injected_validation_result() -> None:
    reader, context, _profile, plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    injected = validate_dimension_response(
        evidence[0].response,
        raw_object=evidence[0].raw_object,
        expected_dimension_id=evidence[0].expected_dimension_id,
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref=evidence[0].attempt_ref,
    )
    with pytest.raises(SemanticEvaluatorError, match="run_binding_mismatch"):
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_evidence=[injected, *evidence[1:]],
            attempt_refs=attempts,
        )


def test_cross_admission_response_substitution_fails_before_run_witness() -> None:
    _reader, context, _profile, _plan, _first, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    other_report = REPORT_BYTES + "\n不同报告。\n".encode()
    second = admit_inputs(
        report_bytes=other_report,
        declared_report_sha256=sha256_bytes(other_report),
        artifact_id="reader-validator-other",
        bounded_context=context,
        declared_bounded_context_sha256=context.context_sha256,
        instrument_config=InstrumentConfig.model_validate(
            InstrumentConfig.minimal_example
        ),
        trial_id="trial-validator",
        public_data_attestation=True,
        private_or_confidential_material=False,
        prompt_sizer=_Sizer(),
    )
    assert second.admitted
    with pytest.raises(SemanticEvaluatorError, match="run_binding_mismatch"):
        assemble_semantic_assessment_run(
            admission=second,
            dimension_evidence=evidence,
            attempt_refs=attempts,
        )


def test_global_derived_finding_collision_has_stable_value_free_error(
    monkeypatch,
) -> None:
    reader, _context, _profile, _plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    first = evidence[0]
    units = [
        item
        for item in decision.assessment_plan.units
        if item.dimension_id == first.expected_dimension_id
    ]
    span = make_span_locator(
        reader.artifact,
        block_id="B000002",
        start_char=0,
        end_char=1,
    )
    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=decision.assessment_plan.trial_id,
        dimension_id=first.expected_dimension_id,
        unit_results=[
            FindingEmittedResult(
                assessment_unit_id=units[0].assessment_unit_id,
                disposition="finding_emitted",
                findings=[_finding(units[0], span)],
            ),
            FindingEmittedResult(
                assessment_unit_id=units[1].assessment_unit_id,
                disposition="finding_emitted",
                findings=[_finding(units[1], span)],
            ),
            NoFindingResult(
                assessment_unit_id=units[2].assessment_unit_id,
                disposition="no_finding",
            ),
        ],
    )
    collision = DimensionEvidence(
        response=response,
        raw_object=response.model_dump(mode="json"),
        expected_dimension_id=first.expected_dimension_id,
        attempt_ref=first.attempt_ref,
    )
    monkeypatch.setattr(
        validator_module,
        "derive_finding_id",
        lambda **_kwargs: "F-000000000001",
    )
    with pytest.raises(SemanticEvaluatorError, match="finding_id_duplicate") as caught:
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_evidence=[collision, *evidence[1:]],
            attempt_refs=attempts,
        )
    assert str(caught.value) == "finding_id_duplicate"


def test_global_derived_handoff_collision_has_stable_value_free_error(
    monkeypatch,
) -> None:
    reader, _context, _profile, _plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    first = evidence[0]
    units = [
        item
        for item in decision.assessment_plan.units
        if item.dimension_id == first.expected_dimension_id
    ]
    span = make_span_locator(
        reader.artifact,
        block_id="B000003",
        start_char=0,
        end_char=1,
    )

    def handoff(unit):
        return O3HandoffDraft(
            assessment_unit_id=unit.assessment_unit_id,
            type="evidence_dependent_assessment",
            report_spans=[span],
            context_requirement_ids=[],
            reason="需要外部证据，转人工复核。",
        )

    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=decision.assessment_plan.trial_id,
        dimension_id=first.expected_dimension_id,
        unit_results=[
            AbstainUnableToAssessResult(
                assessment_unit_id=units[0].assessment_unit_id,
                disposition="abstain_unable_to_assess",
                reason_code="evidence_dependent_assessment",
                handoffs=[handoff(units[0])],
            ),
            AbstainUnableToAssessResult(
                assessment_unit_id=units[1].assessment_unit_id,
                disposition="abstain_unable_to_assess",
                reason_code="evidence_dependent_assessment",
                handoffs=[handoff(units[1])],
            ),
            NoFindingResult(
                assessment_unit_id=units[2].assessment_unit_id,
                disposition="no_finding",
            ),
        ],
    )
    collision = DimensionEvidence(
        response=response,
        raw_object=response.model_dump(mode="json"),
        expected_dimension_id=first.expected_dimension_id,
        attempt_ref=first.attempt_ref,
    )
    monkeypatch.setattr(
        validator_module,
        "derive_handoff_id",
        lambda **_kwargs: "H-000000000001",
    )
    with pytest.raises(SemanticEvaluatorError, match="handoff_id_duplicate") as caught:
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_evidence=[collision, *evidence[1:]],
            attempt_refs=attempts,
        )
    assert str(caught.value) == "handoff_id_duplicate"
