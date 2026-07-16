"""Independent baseline, witnessed composition, and advisory presentation."""

from __future__ import annotations

import pytest

from multi_agent_brief.semantic_evaluator.admission import admit_inputs
from multi_agent_brief.semantic_evaluator.baseline import build_baseline
from multi_agent_brief.semantic_evaluator.composition import (
    build_presentation,
    compose_actual_laj,
    compose_matched_non_llm,
    verify_additive_baseline,
    verify_composition_record,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    DIMENSION_RESPONSE_SCHEMA_ID,
    AttemptRef,
    BoundedRequirement,
    CompositionRecord,
    DimensionResponse,
    FindingDraft,
    FindingEmittedResult,
    InstrumentConfig,
    LajCompositionWitness,
    NoFindingResult,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import (
    freeze_bounded_context,
    normalize_markdown,
    replay_span,
)
from multi_agent_brief.semantic_evaluator.profile import load_profile
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_bytes,
)
from multi_agent_brief.semantic_evaluator.validator import (
    DimensionEvidence,
    assemble_semantic_assessment_run,
)


REPORT = """# 合成报告

## 重复标题

TODO：补齐合成结论。

## 重复标题

[异常链接](bad destination)

##

```text
未闭合的合成代码块
"""


class _Sizer:
    sizer_id = "fake-sizer"
    sizer_version = "v1"

    def count_tokens(self, *, system_text: str, user_text: str) -> int:
        return 10


def _case():
    reader = normalize_markdown(REPORT.encode(), artifact_id="reader-baseline")
    context = freeze_bounded_context(
        context_id="context-baseline",
        data_class="synthetic",
        requirements=[
            BoundedRequirement(
                requirement_id="REQ-001",
                type="must_answer",
                text="说明当前状态。",
                source_locator="brief:B1",
            ),
            BoundedRequirement(
                requirement_id="REQ-002",
                type="must_not_claim",
                text="不得声称已获得发布授权。",
                source_locator="brief:B2",
            ),
        ],
    )
    profile = load_profile()
    baseline = build_baseline(
        reader_artifact=reader.artifact,
        bounded_context=context,
        loaded_profile=profile,
    )
    return reader, context, profile, baseline


def _admitted_case():
    reader, context, profile, baseline = _case()
    report_bytes = REPORT.encode()
    decision = admit_inputs(
        report_bytes=report_bytes,
        declared_report_sha256=sha256_bytes(report_bytes),
        artifact_id="reader-baseline",
        bounded_context=context,
        declared_bounded_context_sha256=context.context_sha256,
        instrument_config=InstrumentConfig.model_validate(
            InstrumentConfig.minimal_example
        ),
        trial_id="trial-baseline",
        public_data_attestation=True,
        private_or_confidential_material=False,
        prompt_sizer=_Sizer(),
    )
    assert decision.admitted
    assert decision.reader.artifact == reader.artifact
    return decision, context, profile, baseline


def _finding_draft(unit, baseline) -> FindingDraft:
    return FindingDraft(
        assessment_unit_id=unit.assessment_unit_id,
        scope_class=unit.scope_class,
        dimension_id=unit.dimension_id,
        severity="major",
        impact_scope="key_conclusion",
        report_spans=[baseline.lint_items[0].report_spans[0]],
        context_requirement_ids=[],
        observation="报告中的阶段表述需要人工核对。",
        rationale="两个内部片段可能指向不同阶段。",
        severity_basis="可能影响关键结论理解。",
        confidence_basis="artifact_internal_inference",
        external_premise_disclosure="none",
        recommended_human_action="inspect_manually",
        suggested_rewrite=None,
    )


def _assembled(*, state: str = "completed", with_finding: bool = False):
    decision, context, profile, baseline = _admitted_case()
    plan = decision.assessment_plan
    evidence: list[DimensionEvidence] = []
    attempts: list[AttemptRef] = []
    for ordinal, dimension in enumerate(profile.profile.dimensions):
        units = [
            item for item in plan.units if item.dimension_id == dimension.dimension_id
        ]
        attempt_ref = f"attempt-{dimension.dimension_id}"
        if state == "incomplete" and ordinal == len(profile.profile.dimensions) - 1:
            attempts.append(
                AttemptRef(
                    attempt_ref=attempt_ref,
                    dimension_id=dimension.dimension_id,
                    status="failed",
                    reason_code="provider_failed",
                )
            )
            continue
        unit_results = [
            NoFindingResult(
                assessment_unit_id=unit.assessment_unit_id,
                disposition="no_finding",
            )
            for unit in units
        ]
        if with_finding and ordinal == 0:
            unit_results[0] = FindingEmittedResult(
                assessment_unit_id=units[0].assessment_unit_id,
                disposition="finding_emitted",
                findings=[_finding_draft(units[0], baseline)],
            )
        response = DimensionResponse(
            schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
            trial_id=plan.trial_id,
            dimension_id=dimension.dimension_id,
            unit_results=unit_results,
        )
        raw = response.model_dump(mode="json")
        if (
            state == "validation_failed"
            and ordinal == len(profile.profile.dimensions) - 1
        ):
            raw["trial_id"] = "trial-tampered-raw"
        if (
            state == "security_failed"
            and ordinal == len(profile.profile.dimensions) - 1
        ):
            raw["tool_calls"] = [{"name": "synthetic-forbidden-tool"}]
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
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_evidence=evidence,
        attempt_refs=attempts,
    )
    assert assembled.run.run_status == state
    return baseline, assembled


def _rehash_witness(payload: dict) -> LajCompositionWitness:
    payload["witness_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "witness_sha256"}
    )
    return LajCompositionWitness.model_validate(payload)


def _rehash_composition(payload: dict) -> CompositionRecord:
    payload["composition_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "composition_sha256"}
    )
    return CompositionRecord.model_validate(payload)


def test_baseline_contains_exact_profile_then_requirement_checklist_order() -> None:
    _reader, context, profile, baseline = _case()
    assert len(baseline.checklist_items) == 9 + len(context.requirements)
    assert [item.dimension_id for item in baseline.checklist_items[:9]] == [
        item.dimension_id for item in profile.profile.dimensions
    ]
    assert [item.requirement_id for item in baseline.checklist_items[9:]] == [
        "REQ-001",
        "REQ-002",
    ]
    assert all("请人工" in item.text for item in baseline.checklist_items)
    assert all("通过" not in item.text for item in baseline.checklist_items)


def test_deterministic_lint_is_lexical_structural_and_span_replayable() -> None:
    reader, _context, _profile, baseline = _case()
    assert {item.rule_id for item in baseline.lint_items} == {
        "unresolved_placeholder",
        "empty_atx_heading",
        "duplicate_atx_heading",
        "unclosed_fenced_code",
        "malformed_markdown_link_destination",
    }
    assert [item.ordinal for item in baseline.lint_items] == list(
        range(len(baseline.lint_items))
    )
    for item in baseline.lint_items:
        assert item.report_spans
        assert all(replay_span(reader.artifact, span) for span in item.report_spans)


def test_baseline_build_and_canonical_reread_are_byte_stable() -> None:
    reader, context, profile, first = _case()
    second = build_baseline(
        reader_artifact=reader.artifact,
        bounded_context=context,
        loaded_profile=profile,
    )
    assert first == second
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    reread = type(first).model_validate_json(canonical_json_bytes(first))
    assert canonical_json_bytes(reread) == canonical_json_bytes(first)


def test_completed_accepted_zero_findings_preserves_exact_baseline() -> None:
    baseline, assembled = _assembled()
    matched = compose_matched_non_llm(baseline)
    actual = compose_actual_laj(baseline, assembled.witness)
    assert verify_additive_baseline(matched, actual) is True
    assert canonical_json_bytes(matched.baseline_payload) == canonical_json_bytes(
        actual.baseline_payload
    )
    presentation = build_presentation(actual, witness=assembled.witness)
    assert presentation.assessed_unit_count == 25
    assert presentation.finding_count == 0
    assert presentation.withheld_finding_count == 0
    assert presentation.failure_count == 0
    assert presentation.advisory_only is True
    assert "未发现问题" not in presentation.disclaimer
    assert "completed/accepted" in presentation.disclaimer


def test_completed_accepted_findings_and_labels_are_deterministically_additive() -> (
    None
):
    baseline, assembled = _assembled(with_finding=True)
    matched = compose_matched_non_llm(baseline)
    actual = compose_actual_laj(baseline, assembled.witness)
    assert verify_additive_baseline(matched, actual) is True
    assert actual.laj_advice_items == assembled.run.findings
    assert actual.duplicate_annotations
    assert (
        actual.duplicate_annotations[0].baseline_item_id
        == baseline.lint_items[0].item_id
    )
    presentation = build_presentation(actual, witness=assembled.witness)
    assert presentation.finding_count == 1
    assert presentation.withheld_finding_count == 0
    assert presentation.additional_semantic_findings == assembled.run.findings


@pytest.mark.parametrize(
    ("state", "validation_status"),
    [
        ("incomplete", "incomplete"),
        ("validation_failed", "rejected"),
        ("security_failed", "rejected"),
    ],
)
def test_failure_only_actual_laj_withholds_findings_and_keeps_baseline(
    state: str,
    validation_status: str,
) -> None:
    baseline, assembled = _assembled(state=state, with_finding=True)
    matched = compose_matched_non_llm(baseline)
    actual = compose_actual_laj(baseline, assembled.witness)
    assert verify_additive_baseline(matched, actual)
    assert actual.condition == "actual_LAJ"
    assert actual.laj_run_status == state
    assert actual.laj_validation_status == validation_status
    assert actual.laj_advice_items == []
    assert actual.duplicate_annotations == []
    presentation = build_presentation(actual, witness=assembled.witness)
    assert presentation.additional_semantic_findings == []
    expected_withheld = len(assembled.run.findings)
    assert presentation.withheld_finding_count == expected_withheld
    assert presentation.failure_count == 1
    assert state in presentation.disclaimer
    assert canonical_json_bytes(actual.baseline_payload) == canonical_json_bytes(
        matched.baseline_payload
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "reader_report",
        "context",
        "profile",
        "plan",
        "instrument",
        "event",
        "validation",
        "run_report",
    ],
)
def test_rehashed_witness_relation_substitution_is_rejected(mutation: str) -> None:
    baseline, assembled = _assembled(with_finding=True)
    payload = assembled.witness.model_dump(mode="json")
    if mutation == "reader_report":
        payload["reader_artifact"]["report_sha256"] = "f" * 64
    elif mutation == "context":
        payload["bounded_context"]["context_sha256"] = "f" * 64
    elif mutation == "profile":
        payload["assessment_plan"]["profile_sha256"] = "f" * 64
    elif mutation == "plan":
        payload["assessment_plan"]["assessment_plan_sha256"] = "f" * 64
    elif mutation == "instrument":
        payload["instrument_manifest"]["instrument_sha256"] = "f" * 64
    elif mutation == "event":
        payload["events"][0]["sequence"] = 2
    elif mutation == "validation":
        payload["validation_report"]["finding_count"] += 1
    else:
        payload["run"]["report_sha256"] = "f" * 64
    forged = _rehash_witness(payload)
    with pytest.raises(SemanticEvaluatorError, match="composition_witness_mismatch"):
        compose_actual_laj(baseline, forged)


@pytest.mark.parametrize(
    "mutation",
    ["advice", "status", "annotation", "baseline"],
)
def test_rehashed_composition_advice_or_status_tampering_is_rejected(
    mutation: str,
) -> None:
    baseline, assembled = _assembled(with_finding=True)
    actual = compose_actual_laj(baseline, assembled.witness)
    payload = actual.model_dump(mode="json")
    if mutation == "advice":
        payload["laj_advice_items"] = []
        payload["duplicate_annotations"] = []
    elif mutation == "status":
        payload["laj_run_status"] = "incomplete"
        payload["laj_validation_status"] = "incomplete"
        payload["laj_advice_items"] = []
        payload["duplicate_annotations"] = []
    elif mutation == "annotation":
        payload["duplicate_annotations"][0]["label"] = "duplicate"
    else:
        baseline_payload = payload["baseline_payload"]
        baseline_payload["checklist_items"][0]["text"] = "篡改后的合成检查项。"
        baseline_payload["baseline_sha256"] = canonical_sha256(
            {
                key: value
                for key, value in baseline_payload.items()
                if key != "baseline_sha256"
            }
        )
        payload["baseline_sha256"] = baseline_payload["baseline_sha256"]
    forged = _rehash_composition(payload)
    with pytest.raises(SemanticEvaluatorError, match="composition_record_mismatch"):
        verify_composition_record(forged, witness=assembled.witness)


def test_matched_baseline_rejects_any_laj_witness() -> None:
    baseline, assembled = _assembled()
    matched = compose_matched_non_llm(baseline)
    with pytest.raises(SemanticEvaluatorError, match="composition_record_mismatch"):
        build_presentation(matched, witness=assembled.witness)
