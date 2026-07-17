"""Strict parser, scope routing, span validation, and event replay tests."""

from __future__ import annotations

from dataclasses import replace
import inspect
from pathlib import Path

import pytest

from multi_agent_brief.semantic_evaluator.admission import admit_inputs
import multi_agent_brief.semantic_evaluator.validator as validator_module
from multi_agent_brief.semantic_evaluator.contracts import (
    ADMISSION_REQUEST_SCHEMA_ID,
    DIMENSION_RESPONSE_SCHEMA_ID,
    AbstainConflictingContextResult,
    AbstainInsufficientContextResult,
    AbstainUnableToAssessResult,
    BoundedRequirement,
    DimensionResponse,
    FindingDraft,
    FindingEmittedResult,
    InstrumentConfig,
    InstrumentManifest,
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
import multi_agent_brief.semantic_evaluator.instrument as instrument_module
from multi_agent_brief.semantic_evaluator.parser import parse_dimension_response
import multi_agent_brief.semantic_evaluator.profile as profile_module
from multi_agent_brief.semantic_evaluator.profile import load_profile
import multi_agent_brief.semantic_evaluator.snapshot as snapshot_module
from multi_agent_brief.semantic_evaluator.prompts import (
    CANARY_DERIVATION_VERSION,
    derive_forbidden_canary_values,
    system_prompt_text,
)
from multi_agent_brief.semantic_evaluator.resources import EvaluatorResourceError
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_json_text,
    canonical_model_sha256,
    canonical_sha256,
    sha256_bytes,
)
from multi_agent_brief.semantic_evaluator.unit_planner import (
    build_assessment_plan,
)
from multi_agent_brief.semantic_evaluator.validator import (
    assemble_semantic_assessment_run,
    make_dimension_attempt_evidence,
    recompute_event_counts,
    validate_dimension_response,
    verify_laj_composition_witness,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "semantic_evaluator"
REPORT_BYTES = "# 合成报告\n\n当前状态为 HOLD。\n\n结论写为 READY。\n".encode()
PARSER_CANARIES = derive_forbidden_canary_values(
    assessment_plan_sha256="0" * 64,
    bounded_context_sha256="1" * 64,
    dimension_id="synthetic_parser_probe",
)


class _Sizer:
    sizer_id = "fake-sizer"
    sizer_version = "v1"

    def count_tokens(self, *, system_text: str, user_text: str) -> int:
        return 10


def _parse(raw_body: bytes):
    return parse_dimension_response(
        raw_body,
        forbidden_canary_values=PARSER_CANARIES,
    )


def _fail_if_semantic_validation_runs(*_args, **_kwargs):
    pytest.fail("security preflight reached semantic validation")


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
    config_payload = InstrumentConfig.minimal_example.copy()
    config_payload["retry_policy"] = {
        "max_attempts": 2,
        "retryable_reason_codes": ["provider_retryable_failure"],
        "backoff_schedule_ms": [0],
    }
    decision = admit_inputs(
        {
            "schema_version": ADMISSION_REQUEST_SCHEMA_ID,
            "report_bytes_hex": REPORT_BYTES.hex(),
            "declared_report_sha256": sha256_bytes(REPORT_BYTES),
            "artifact_id": "reader-validator",
            "bounded_context": context,
            "declared_bounded_context_sha256": context.context_sha256,
            "instrument_config": config_payload,
            "trial_id": "trial-validator",
            "public_data_attestation": True,
            "private_or_confidential_material": False,
            "archive_root": None,
            "workspace_root": None,
        },
        prompt_sizer=_Sizer(),
    )
    assert decision.admitted
    return decision.reader, context, profile, decision.assessment_plan, decision


def _case():
    reader, context, profile, plan, _decision = _admitted_case()
    return reader, context, profile, plan


def _prompt_for(decision, dimension_id: str):
    return next(item for item in decision.prompts if item.dimension_id == dimension_id)


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


def _complete_no_finding_validation_case(
    *,
    inject_security: bool = False,
    inject_canary: bool = False,
):
    reader, context, profile, plan, decision = _admitted_case()
    evidence = []
    attempts = []
    for ordinal, dimension in enumerate(profile.profile.dimensions):
        prompt = _prompt_for(decision, dimension.dimension_id)
        response = _no_finding_response(plan, dimension.dimension_id)
        if inject_canary and ordinal == 0:
            units = [
                item
                for item in plan.units
                if item.dimension_id == dimension.dimension_id
            ]
            span = make_span_locator(
                reader.artifact,
                block_id="B000002",
                start_char=0,
                end_char=1,
            )
            finding_payload = _finding(units[0], span).model_dump(mode="json")
            finding_payload["observation"] += f" {prompt.forbidden_canary_values[0]}"
            response = DimensionResponse(
                schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
                trial_id=plan.trial_id,
                dimension_id=dimension.dimension_id,
                unit_results=[
                    FindingEmittedResult(
                        assessment_unit_id=units[0].assessment_unit_id,
                        disposition="finding_emitted",
                        findings=[FindingDraft.model_validate(finding_payload)],
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
        raw = response.model_dump(mode="json")
        if inject_security and ordinal == 0:
            raw["tool_calls"] = [{"name": "forbidden-synthetic-tool"}]
        evidence.append(
            make_dimension_attempt_evidence(
                trial_id=plan.trial_id,
                prompt=prompt,
                attempt_ordinal=1,
                status="completed",
                raw_response_bytes=canonical_json_bytes(raw),
            )
        )
        attempts.append(evidence[-1])
    return reader, context, profile, plan, decision, evidence, attempts


def _rehash_attempt_canaries(attempt, values: list[str]):
    payload = attempt.model_dump(mode="json")
    payload["forbidden_canary_values"] = values
    payload["evidence_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "evidence_sha256"}
    )
    return attempt.model_copy(update=payload)


def _self_consistent_noncurrent_manifest(
    manifest: InstrumentManifest,
) -> InstrumentManifest:
    payload = manifest.model_dump(mode="json")
    payload["implementation_components"][0]["source_sha256"] = "0" * 64
    component_identity = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "manifest_id", "instrument_sha256"}
    }
    payload["manifest_id"] = f"manifest-{canonical_sha256(component_identity)[:12]}"
    manifest_payload = {
        "schema_version": payload["schema_version"],
        "manifest_id": payload["manifest_id"],
        **component_identity,
    }
    payload["instrument_sha256"] = canonical_sha256(manifest_payload)
    return InstrumentManifest.model_validate(payload)


def test_parser_accepts_one_strict_object_and_never_repairs_wrapped_json() -> None:
    _reader, _context, _profile, plan = _case()
    response = _no_finding_response(plan, "cross_section_consistency")
    parsed = _parse(canonical_json_bytes(response))
    assert parsed.ok is True
    assert parsed.response == response
    wrapped = b"```json\n" + canonical_json_bytes(response) + b"\n```"
    assert _parse(wrapped).reason_codes == ("parser_invalid_json",)
    malformed = (FIXTURE_ROOT / "malformed_response.txt").read_bytes()
    assert _parse(malformed).reason_codes == ("parser_invalid_json",)
    assert _parse(b"[]").reason_codes == ("parser_top_level_not_object",)


def test_parser_classifies_nested_authority_keys_before_generic_extra_failure() -> None:
    _reader, _context, _profile, plan = _case()
    payload = _no_finding_response(plan, "cross_section_consistency").model_dump(
        mode="json"
    )
    payload["metadata"] = {"nested": {"PASS": True}}
    parsed = _parse(canonical_json_bytes(payload))
    assert parsed.reason_codes == ("authority_output_forbidden",)
    assert parsed.violations == ()


def test_prompt_boundary_owns_exact_canary_and_attempt_helper_has_no_legacy_ingress() -> (
    None
):
    _reader, context, _profile, plan, decision = _admitted_case()
    prompt = _prompt_for(decision, "cross_section_consistency")
    expected_digest = canonical_sha256(
        [
            CANARY_DERIVATION_VERSION,
            plan.assessment_plan_sha256,
            context.context_sha256,
            prompt.dimension_id,
        ]
    )
    expected_canaries = (f"BLSE_CANARY_V1_{expected_digest}",)
    expected_policy = (
        "\n<SECURITY_CANARY_POLICY>\n"
        + canonical_json_text(
            {
                "forbidden_output_values": list(expected_canaries),
                "rule": "never_emit",
            }
        )
        + "\n</SECURITY_CANARY_POLICY>"
    )
    assert prompt.forbidden_canary_values == expected_canaries
    assert prompt.system_text == system_prompt_text() + expected_policy
    assert prompt.system_text.count(expected_canaries[0]) == 1
    assert prompt.request_sha256 == canonical_sha256(
        {
            "dimension_id": prompt.dimension_id,
            "forbidden_canary_values": list(expected_canaries),
            "system_text": prompt.system_text,
            "user_text": prompt.user_text,
        }
    )
    assert tuple(inspect.signature(make_dimension_attempt_evidence).parameters) == (
        "trial_id",
        "prompt",
        "attempt_ordinal",
        "status",
        "raw_response_bytes",
        "reason_code",
    )


@pytest.mark.parametrize(
    ("plan_sha", "context_sha", "dimension_id"),
    [
        ("0" * 63, "1" * 64, "cross_section_consistency"),
        ("0" * 64, "1" * 63, "cross_section_consistency"),
        ("0" * 64, "1" * 64, "Cross-Section"),
        (object(), "1" * 64, "cross_section_consistency"),
    ],
)
def test_canary_derivation_rejects_malformed_inputs_value_free(
    plan_sha,
    context_sha,
    dimension_id,
) -> None:
    with pytest.raises(ValueError) as caught:
        derive_forbidden_canary_values(
            assessment_plan_sha256=plan_sha,
            bounded_context_sha256=context_sha,
            dimension_id=dimension_id,
        )
    assert str(caught.value) == "canary_derivation_input_invalid"
    assert caught.value.__cause__ is None


def test_raw_canary_preflight_has_security_precedence_and_backslash_parity() -> None:
    canary = PARSER_CANARIES[0]
    literal = canary.encode("ascii")
    escaped = "".join(f"\\u00{ord(char):02x}" for char in canary).encode()
    midpoint = len(canary) // 2
    mixed = canary[:midpoint].encode() + b"".join(
        f"\\u00{ord(char):02X}".encode() for char in canary[midpoint:]
    )
    security_rows = {
        "literal-value": b'{"value":"' + literal + b'"}',
        "extra-key": b'{"' + literal + b'":true}',
        "duplicate-member": b'{"value":null,"value":"' + literal + b'"}',
        "schema-invalid": b'{"schema_version":"' + literal + b'"}',
        "malformed-tail": b'{"value":"' + literal + b'"} trailing',
        "truncated": b'{"value":"' + literal,
        "invalid-utf8": b"\xff" + literal,
        "fully-escaped": b'{"value":"' + escaped + b'"}',
        "mixed": b'{"value":"' + mixed + b'"}',
    }
    for raw in security_rows.values():
        result = _parse(raw)
        assert result.reason_codes == ("tool_or_canary_output_forbidden",)
        assert result.response is None
        assert result.violations == ()

    even_backslash = "".join(f"\\\\u00{ord(char):02x}" for char in canary).encode()
    assert _parse(b'{"value":"' + even_backslash + b'"}').reason_codes == (
        "parser_schema_invalid",
    )


def test_parser_requires_one_valid_prompt_owned_canary_tuple_value_free() -> None:
    raw = b'{"synthetic":"input"}'
    with pytest.raises(TypeError) as omitted:
        parse_dimension_response(raw)  # type: ignore[call-arg]
    assert PARSER_CANARIES[0] not in str(omitted.value)

    second = derive_forbidden_canary_values(
        assessment_plan_sha256="2" * 64,
        bounded_context_sha256="3" * 64,
        dimension_id="synthetic_parser_probe",
    )[0]
    malformed_values = (
        (),
        (PARSER_CANARIES[0], PARSER_CANARIES[0]),
        (second, PARSER_CANARIES[0]),
        ("synthetic-invalid-canary",),
        (object(),),
    )
    for values in malformed_values:
        result = parse_dimension_response(
            raw,
            forbidden_canary_values=values,  # type: ignore[arg-type]
        )
        assert result.reason_codes == ("parser_schema_invalid",)
        assert result.violations == ()
        assert PARSER_CANARIES[0] not in repr(result)


def test_parser_rejects_duplicate_members_before_collapse_with_security_precedence() -> (
    None
):
    _reader, _context, _profile, plan = _case()
    response = _no_finding_response(plan, "cross_section_consistency")
    text = canonical_json_bytes(response).decode("utf-8")
    top_level_duplicate = text[:-1] + f',"trial_id":"{plan.trial_id}"}}'
    assert _parse(top_level_duplicate.encode()).reason_codes == (
        "parser_duplicate_member",
    )
    unit_id = response.unit_results[0].assessment_unit_id
    nested_duplicate = text.replace(
        f'"assessment_unit_id":"{unit_id}"',
        f'"assessment_unit_id":"{unit_id}","assessment_unit_id":"{unit_id}"',
        1,
    )
    assert _parse(nested_duplicate.encode()).reason_codes == (
        "parser_duplicate_member",
    )
    authority = b'{"pass":false,"pass":true}'
    assert _parse(authority).reason_codes == ("authority_output_forbidden",)
    security = b'{"pass":false,"tool_calls":[],"pass":true}'
    assert _parse(security).reason_codes == ("tool_or_canary_output_forbidden",)


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
    assert _parse(canonical_json_bytes(finding_payload)).reason_codes == (
        "parser_schema_invalid",
    )

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
    assert _parse(canonical_json_bytes(handoff_payload)).reason_codes == (
        "parser_schema_invalid",
    )


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
    canary = derive_forbidden_canary_values(
        assessment_plan_sha256=plan.assessment_plan_sha256,
        bounded_context_sha256=context.context_sha256,
        dimension_id="cross_section_consistency",
    )[0]
    raw["unit_results"][0]["findings"][0]["observation"] += f" {canary}"
    raw[f"{canary}-AS-KEY"] = "synthetic"
    result = validate_dimension_response(
        response,
        raw_object=raw,
        expected_dimension_id="cross_section_consistency",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref="attempt-invalid",
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

    stale_context = context.model_copy(deep=True)
    stale_context.requirements[0].text = "stale synthetic mutation"
    context_result = validate_dimension_response(
        response,
        raw_object=response.model_dump(mode="json"),
        expected_dimension_id="cross_section_consistency",
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=stale_context,
        attempt_ref="attempt-forged-context",
    )
    assert "run_binding_mismatch" in context_result.reason_codes
    assert context_result.accepted_findings == ()


def test_synthetic_events_recompute_complete_25_unit_run_counts() -> None:
    _reader, _context, _profile, plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=evidence,
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
        dimension_attempt_evidence=evidence,
    )
    assert replay == assembled


def test_security_failure_has_precedence_and_a_recomputable_event() -> None:
    _reader, _context, _profile, _plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case(inject_security=True)
    )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=evidence,
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


def test_security_in_one_dimension_erases_another_dimensions_valid_finding(
    monkeypatch,
) -> None:
    reader, _context, _profile, plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    security_prompt = _prompt_for(decision, evidence[0].dimension_id)
    security_attempt = make_dimension_attempt_evidence(
        trial_id=decision.input_binding.trial_id,
        prompt=security_prompt,
        attempt_ordinal=1,
        status="completed",
        raw_response_bytes=(
            b'{"value":"'
            + security_prompt.forbidden_canary_values[0].encode("ascii")
            + b'"}\xff'
        ),
    )
    finding_prompt = _prompt_for(decision, evidence[1].dimension_id)
    units = [
        item for item in plan.units if item.dimension_id == finding_prompt.dimension_id
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
        dimension_id=finding_prompt.dimension_id,
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
    finding_attempt = make_dimension_attempt_evidence(
        trial_id=decision.input_binding.trial_id,
        prompt=finding_prompt,
        attempt_ordinal=1,
        status="completed",
        raw_response_bytes=canonical_json_bytes(finding_response),
    )
    parser_calls = 0
    real_parser = validator_module.parse_dimension_response

    def record_parser(raw_body, *, forbidden_canary_values):
        nonlocal parser_calls
        parser_calls += 1
        return real_parser(
            raw_body,
            forbidden_canary_values=forbidden_canary_values,
        )

    monkeypatch.setattr(validator_module, "parse_dimension_response", record_parser)
    monkeypatch.setattr(
        validator_module,
        "validate_dimension_response",
        _fail_if_semantic_validation_runs,
    )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=[
            security_attempt,
            finding_attempt,
            *evidence[2:],
        ],
    )
    # Assembly derives once and its mandatory witness verification replays once.
    assert parser_calls == 2 * len(decision.prompts)
    assert assembled.run.run_status == "security_failed"
    assert assembled.run.assessment_units == []
    assert assembled.run.findings == []
    assert assembled.run.handoffs == []
    assert assembled.validation_report.validation_status == "rejected"
    assert assembled.validation_report.reason_codes == [
        "tool_or_canary_output_forbidden"
    ]
    assert assembled.validation_report.accepted_finding_ids == []
    assert assembled.validation_report.finding_count == 0
    assert assembled.validation_report.handoff_count == 0
    event_types = [item.event_type for item in assembled.events]
    assert event_types.count("security_failure_recorded") == 1
    assert event_types[-1] == "run_incomplete"
    assert not {
        "dimension_parsed",
        "unit_disposition_recorded",
        "finding_accepted",
        "finding_rejected",
        "o3_handoff_recorded",
    }.intersection(event_types)
    assert verify_laj_composition_witness(assembled.witness) == assembled.witness


def test_prompt_owned_canary_cannot_be_laundered_by_rehashed_attempt_metadata() -> None:
    _reader, _context, _profile, _plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case(inject_canary=True)
    )
    canonical = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=evidence,
    )
    assert canonical.run.run_status == "security_failed"
    assert canonical.validation_report.validation_status == "rejected"
    assert canonical.validation_report.reason_codes == [
        "tool_or_canary_output_forbidden"
    ]
    assert canonical.run.findings == []

    first = evidence[0]
    canary = first.forbidden_canary_values[0]
    synthetic_non_authority = "caller_secret_shaped_value_DO_NOT_LEAK"
    mutations = {
        "remove": [],
        "change": ["BLSE_CANARY_V1_" + "0" * 64],
        "add": [canary, synthetic_non_authority],
        "reorder": [synthetic_non_authority, canary],
        "duplicate": [canary, canary],
    }
    for values in mutations.values():
        forged = _rehash_attempt_canaries(first, values)
        with pytest.raises(SemanticEvaluatorError) as caught:
            assemble_semantic_assessment_run(
                admission=decision,
                dimension_attempt_evidence=[forged, *evidence[1:]],
            )
        assert str(caught.value) == "assessment_evidence_mismatch"
        assert synthetic_non_authority not in str(caught.value)


def test_failed_retry_attempt_does_not_turn_a_completed_dimension_into_failure() -> (
    None
):
    _reader, _context, _profile, _plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    first = evidence[0]
    prompt = _prompt_for(decision, first.dimension_id)
    retry_history = [
        make_dimension_attempt_evidence(
            trial_id=decision.input_binding.trial_id,
            prompt=prompt,
            attempt_ordinal=1,
            status="failed",
            reason_code="provider_retryable_failure",
        ),
        make_dimension_attempt_evidence(
            trial_id=decision.input_binding.trial_id,
            prompt=prompt,
            attempt_ordinal=2,
            status="completed",
            raw_response_bytes=bytes.fromhex(first.raw_response_bytes_hex),
        ),
        *evidence[1:],
    ]
    assert retry_history[0].forbidden_canary_values == list(
        prompt.forbidden_canary_values
    )
    assert retry_history[1].forbidden_canary_values == list(
        prompt.forbidden_canary_values
    )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=retry_history,
    )
    assert assembled.run.run_status == "completed"
    assert assembled.validation_report.validation_status == "accepted"
    assert recompute_event_counts(assembled.events)["failure_count"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "gap",
        "reorder",
        "duplicate",
        "non_retryable",
        "excess",
        "prompt_sha",
        "raw_sha",
        "evidence_sha",
    ],
)
def test_attempt_evidence_topology_and_integrity_fail_closed(mutation: str) -> None:
    _reader, _context, _profile, _plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    first = evidence[0]
    prompt = _prompt_for(decision, first.dimension_id)
    raw = bytes.fromhex(first.raw_response_bytes_hex)
    if mutation == "gap":
        candidate = [
            make_dimension_attempt_evidence(
                trial_id=decision.input_binding.trial_id,
                prompt=prompt,
                attempt_ordinal=2,
                status="completed",
                raw_response_bytes=raw,
            ),
            *evidence[1:],
        ]
    elif mutation == "reorder":
        candidate = [evidence[1], evidence[0], *evidence[2:]]
    elif mutation == "duplicate":
        candidate = [first, first, *evidence[1:]]
    elif mutation == "non_retryable":
        candidate = [
            make_dimension_attempt_evidence(
                trial_id=decision.input_binding.trial_id,
                prompt=prompt,
                attempt_ordinal=1,
                status="failed",
                reason_code="provider_failed",
            ),
            make_dimension_attempt_evidence(
                trial_id=decision.input_binding.trial_id,
                prompt=prompt,
                attempt_ordinal=2,
                status="completed",
                raw_response_bytes=raw,
            ),
            *evidence[1:],
        ]
    elif mutation == "excess":
        candidate = [
            make_dimension_attempt_evidence(
                trial_id=decision.input_binding.trial_id,
                prompt=prompt,
                attempt_ordinal=ordinal,
                status="completed" if ordinal == 3 else "failed",
                raw_response_bytes=raw if ordinal == 3 else None,
                reason_code=(None if ordinal == 3 else "provider_retryable_failure"),
            )
            for ordinal in (1, 2, 3)
        ] + evidence[1:]
    elif mutation == "prompt_sha":
        candidate = [
            make_dimension_attempt_evidence(
                trial_id=decision.input_binding.trial_id,
                prompt=replace(prompt, request_sha256="0" * 64),
                attempt_ordinal=1,
                status="completed",
                raw_response_bytes=raw,
            ),
            *evidence[1:],
        ]
    elif mutation == "raw_sha":
        candidate = [
            first.model_copy(update={"raw_response_sha256": "0" * 64}),
            *evidence[1:],
        ]
    else:
        candidate = [
            first.model_copy(update={"evidence_sha256": "0" * 64}),
            *evidence[1:],
        ]
    with pytest.raises(SemanticEvaluatorError, match="assessment_evidence_mismatch"):
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=candidate,
        )


def test_raw_response_rewrite_requires_new_evidence_and_witness_identity() -> None:
    _reader, _context, _profile, _plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    original = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=evidence,
    )
    first = evidence[0]
    raw = bytes.fromhex(first.raw_response_bytes_hex)
    stale = first.model_copy(
        update={"raw_response_bytes_hex": (b" " + raw + b"\n").hex()}
    )
    with pytest.raises(SemanticEvaluatorError, match="assessment_evidence_mismatch"):
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=[stale, *evidence[1:]],
        )

    rederived = make_dimension_attempt_evidence(
        trial_id=decision.input_binding.trial_id,
        prompt=_prompt_for(decision, first.dimension_id),
        attempt_ordinal=1,
        status="completed",
        raw_response_bytes=b" " + raw + b"\n",
    )
    changed = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=[rederived, *evidence[1:]],
    )
    assert changed.run == original.run
    assert changed.validation_report == original.validation_report
    assert changed.events == original.events
    assert changed.witness.witness_sha256 != original.witness.witness_sha256
    assert rederived.evidence_sha256 != first.evidence_sha256


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"not-json", "parser_invalid_json"),
        (b"[]", "parser_top_level_not_object"),
        (b"\xff", "parser_invalid_utf8"),
    ],
)
def test_raw_parser_failures_are_derived_from_attempt_bytes(
    raw: bytes,
    reason: str,
) -> None:
    _reader, _context, _profile, _plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    first = evidence[0]
    failed_parse = make_dimension_attempt_evidence(
        trial_id=decision.input_binding.trial_id,
        prompt=_prompt_for(decision, first.dimension_id),
        attempt_ordinal=1,
        status="completed",
        raw_response_bytes=raw,
    )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=[failed_parse, *evidence[1:]],
    )
    assert assembled.run.run_status == "parser_failed"
    assert assembled.validation_report.validation_status == "rejected"
    assert assembled.validation_report.reason_codes == [reason]
    assert assembled.run.findings == []


def test_partial_and_all_provider_failures_have_distinct_terminal_statuses() -> None:
    _reader, _context, _profile, _plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    terminal_failures = [
        make_dimension_attempt_evidence(
            trial_id=decision.input_binding.trial_id,
            prompt=_prompt_for(decision, item.dimension_id),
            attempt_ordinal=1,
            status="failed",
            reason_code="provider_failed",
        )
        for item in evidence
    ]
    partial = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=[*evidence[:-1], terminal_failures[-1]],
    )
    assert partial.run.run_status == "incomplete"
    assert partial.validation_report.validation_status == "incomplete"
    all_failed = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=terminal_failures,
    )
    assert all_failed.run.run_status == "provider_failed"
    assert all_failed.validation_report.validation_status == "incomplete"
    assert all_failed.run.findings == []


def test_assembly_rejects_self_consistent_noncurrent_manifest_before_evidence() -> None:
    _reader, _context, _profile, _plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    noncurrent = _self_consistent_noncurrent_manifest(decision.instrument_manifest)
    forged = replace(decision, instrument_manifest=noncurrent)
    with pytest.raises(SemanticEvaluatorError, match="instrument_manifest_mismatch"):
        assemble_semantic_assessment_run(
            admission=forged,
            dimension_attempt_evidence=evidence,
        )


def test_public_manifest_mutation_cannot_rewrite_retained_snapshot_authority() -> None:
    _reader, _context, _profile, _plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    decision.instrument_manifest.provider_id = "forged-provider"
    decision.instrument_manifest.instrument_sha256 = canonical_model_sha256(
        decision.instrument_manifest,
        exclude=("instrument_sha256",),
    )
    with pytest.raises(SemanticEvaluatorError) as raised:
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=evidence,
        )
    assert raised.value.reason_code == "instrument_manifest_mismatch"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_malformed_public_manifest_is_value_free_across_assembly_and_replay() -> None:
    _reader, _context, _profile, _plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    hidden_detail = "PRIVATE-SYNTHETIC-CANARY-DO-NOT-RENDER"
    decision.instrument_manifest.schema_sha256s["unexpected_private"] = hidden_detail
    with pytest.raises(SemanticEvaluatorError) as assembly_error:
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=evidence,
        )
    assert assembly_error.value.reason_code == "instrument_manifest_mismatch"
    assert assembly_error.value.__cause__ is None
    assert assembly_error.value.__context__ is None
    assert hidden_detail not in repr(assembly_error.value)

    _reader, _context, _profile, _plan, fresh, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    assembled = assemble_semantic_assessment_run(
        admission=fresh,
        dimension_attempt_evidence=evidence,
    )
    assembled.witness.instrument_manifest.schema_sha256s["unexpected_private"] = (
        hidden_detail
    )
    assembled.witness.witness_sha256 = canonical_model_sha256(
        assembled.witness,
        exclude=("witness_sha256",),
    )
    with pytest.raises(SemanticEvaluatorError) as replay_error:
        verify_laj_composition_witness(assembled.witness)
    assert replay_error.value.reason_code == "composition_witness_mismatch"
    assert replay_error.value.__cause__ is None
    assert replay_error.value.__context__ is None
    assert hidden_detail not in repr(replay_error.value)


def test_untrusted_attempt_and_response_errors_retain_no_caller_values() -> None:
    reader, context, _profile, plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    hidden_detail = "PRIVATE-SYNTHETIC-EXPORTED-VALUE"
    malformed_attempt = evidence[0].model_copy(
        update={"raw_response_bytes_hex": hidden_detail}
    )
    with pytest.raises(SemanticEvaluatorError) as assembly_error:
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=[malformed_attempt, *evidence[1:]],
        )
    assert assembly_error.value.reason_code == "assessment_evidence_mismatch"
    assert assembly_error.value.__cause__ is None
    assert assembly_error.value.__context__ is None
    assert hidden_detail not in repr(assembly_error.value)

    response = _no_finding_response(plan, "cross_section_consistency")
    malformed_response = response.model_copy(update={"dimension_id": hidden_detail})
    with pytest.raises(SemanticEvaluatorError) as response_error:
        validate_dimension_response(
            malformed_response,
            raw_object=malformed_response.model_dump(mode="json"),
            expected_dimension_id="cross_section_consistency",
            plan=plan,
            reader_artifact=reader.artifact,
            bounded_context=context,
            attempt_ref="attempt-value-free-response",
        )
    assert response_error.value.reason_code == "raw_response_binding_mismatch"
    assert response_error.value.__cause__ is None
    assert response_error.value.__context__ is None
    assert hidden_detail not in repr(response_error.value)


@pytest.mark.parametrize("failure_site", ["profile", "component", "prompt"])
def test_source_resolution_failure_is_value_free_at_assembly_and_witness_boundaries(
    monkeypatch,
    failure_site: str,
) -> None:
    _reader, _context, _profile, _plan, decision, evidence, _attempts = (
        _complete_no_finding_validation_case()
    )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=evidence,
    )
    hidden_detail = "/private/synthetic-customer/source.py"

    if failure_site == "profile":

        def fail_profile_resource(*_args) -> str:
            raise OSError(hidden_detail)

        monkeypatch.setattr(profile_module, "resource_text", fail_profile_resource)
    elif failure_site == "component":

        def fail_source_resolution(_module_name: str) -> str:
            raise EvaluatorResourceError("evaluator_source_unavailable")

        monkeypatch.setattr(
            instrument_module,
            "source_sha256_for_module",
            fail_source_resolution,
        )
    else:

        def fail_prompt_resource(*_parts: str) -> str:
            raise EvaluatorResourceError("evaluator_resource_unavailable")

        monkeypatch.setattr(
            snapshot_module,
            "resource_text",
            fail_prompt_resource,
        )
    replayed = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=evidence,
    )
    assert replayed == assembled

    with pytest.raises(SemanticEvaluatorError) as witness_error:
        verify_laj_composition_witness(assembled.witness)
    assert str(witness_error.value) == "composition_witness_mismatch"
    assert witness_error.value.__cause__ is None
    assert witness_error.value.__context__ is None
    assert hidden_detail not in str(witness_error.value)


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
            dimension_attempt_evidence=evidence[:-1],
        )

    first = evidence[0]
    parsed_first = parse_dimension_response(
        bytes.fromhex(first.raw_response_bytes_hex),
        forbidden_canary_values=tuple(first.forbidden_canary_values),
    )
    partial_response = parsed_first.response.model_copy(
        update={"unit_results": parsed_first.response.unit_results[:1]}
    )
    partial = make_dimension_attempt_evidence(
        trial_id=decision.input_binding.trial_id,
        prompt=_prompt_for(decision, first.dimension_id),
        attempt_ordinal=1,
        status="completed",
        raw_response_bytes=canonical_json_bytes(partial_response),
    )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=[partial, *evidence[1:]],
    )
    assert assembled.run.run_status == "validation_failed"
    assert "assessment_unit_set_mismatch" in assembled.validation_report.reason_codes


def test_assembly_revalidates_evidence_and_rejects_injected_validation_result() -> None:
    reader, context, _profile, plan, decision, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    raw = bytes.fromhex(evidence[0].raw_response_bytes_hex)
    parsed = parse_dimension_response(
        raw,
        forbidden_canary_values=tuple(evidence[0].forbidden_canary_values),
    )
    injected = validate_dimension_response(
        parsed.response,
        raw_object=parsed.raw_object,
        expected_dimension_id=evidence[0].dimension_id,
        plan=plan,
        reader_artifact=reader.artifact,
        bounded_context=context,
        attempt_ref=evidence[0].attempt_ref,
    )
    with pytest.raises(SemanticEvaluatorError, match="assessment_evidence_mismatch"):
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=[injected, *evidence[1:]],
        )


def test_cross_admission_response_substitution_fails_before_run_witness() -> None:
    _reader, context, _profile, _plan, first_decision, evidence, attempts = (
        _complete_no_finding_validation_case()
    )
    other_report = REPORT_BYTES + "\n不同报告。\n".encode()
    second = admit_inputs(
        {
            "schema_version": ADMISSION_REQUEST_SCHEMA_ID,
            "report_bytes_hex": other_report.hex(),
            "declared_report_sha256": sha256_bytes(other_report),
            "artifact_id": "reader-validator-other",
            "bounded_context": context,
            "declared_bounded_context_sha256": context.context_sha256,
            "instrument_config": first_decision.instrument_config,
            "trial_id": "trial-validator",
            "public_data_attestation": True,
            "private_or_confidential_material": False,
            "archive_root": None,
            "workspace_root": None,
        },
        prompt_sizer=_Sizer(),
    )
    assert second.admitted
    with pytest.raises(SemanticEvaluatorError, match="assessment_evidence_mismatch"):
        assemble_semantic_assessment_run(
            admission=second,
            dimension_attempt_evidence=evidence,
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
        if item.dimension_id == first.dimension_id
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
        dimension_id=first.dimension_id,
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
    collision = make_dimension_attempt_evidence(
        trial_id=decision.input_binding.trial_id,
        prompt=_prompt_for(decision, first.dimension_id),
        attempt_ordinal=1,
        status="completed",
        raw_response_bytes=canonical_json_bytes(response),
    )
    monkeypatch.setattr(
        validator_module,
        "derive_finding_id",
        lambda **_kwargs: "F-000000000001",
    )
    with pytest.raises(SemanticEvaluatorError, match="finding_id_duplicate") as caught:
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=[collision, *evidence[1:]],
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
        if item.dimension_id == first.dimension_id
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
        dimension_id=first.dimension_id,
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
    collision = make_dimension_attempt_evidence(
        trial_id=decision.input_binding.trial_id,
        prompt=_prompt_for(decision, first.dimension_id),
        attempt_ordinal=1,
        status="completed",
        raw_response_bytes=canonical_json_bytes(response),
    )
    monkeypatch.setattr(
        validator_module,
        "derive_handoff_id",
        lambda **_kwargs: "H-000000000001",
    )
    with pytest.raises(SemanticEvaluatorError, match="handoff_id_duplicate") as caught:
        assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=[collision, *evidence[1:]],
        )
    assert str(caught.value) == "handoff_id_duplicate"
