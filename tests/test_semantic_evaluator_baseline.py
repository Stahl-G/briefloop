"""Independent baseline, additive composition, and advisory presentation."""

from __future__ import annotations

import pytest

from multi_agent_brief.semantic_evaluator.baseline import build_baseline
from multi_agent_brief.semantic_evaluator.composition import (
    build_presentation,
    compose_actual_laj,
    compose_matched_non_llm,
    verify_additive_baseline,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    BoundedRequirement,
    DuplicateAnnotation,
    FindingProposal,
    SemanticAssessmentRun,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import (
    freeze_bounded_context,
    normalize_markdown,
    replay_span,
)
from multi_agent_brief.semantic_evaluator.profile import load_profile
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes


REPORT = """# 合成报告

## 重复标题

TODO：补齐合成结论。

## 重复标题

[异常链接](bad destination)

##

```text
未闭合的合成代码块
"""


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


def _run(*, baseline, with_finding: bool) -> SemanticAssessmentRun:
    finding = None
    if with_finding:
        finding = FindingProposal(
            finding_id="F-000000000001",
            assessment_unit_id="AU-000000000001",
            status="proposal",
            scope_class="O1",
            dimension_id="cross_section_consistency",
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
    return SemanticAssessmentRun.model_validate(
        {
            "schema_version": "briefloop.semantic_evaluator.run.v1",
            "run_id": "run-baseline",
            "trial_id": "trial-baseline",
            "report_sha256": baseline.report_sha256,
            "bounded_context_sha256": baseline.bounded_context_sha256,
            "profile_sha256": baseline.profile_sha256,
            "instrument_sha256": "1" * 64,
            "assessment_plan_sha256": "2" * 64,
            "run_status": "incomplete",
            "assessment_units": [
                {
                    "assessment_unit_id": "AU-000000000001",
                    "dimension_id": "cross_section_consistency",
                    "sub_aspect_id": "status_consistency",
                    "disposition": "finding_emitted" if finding else "no_finding",
                    "finding_ids": [finding.finding_id] if finding else [],
                    "handoff_ids": [],
                    "attempt_ref": "attempt-001",
                },
                {
                    "assessment_unit_id": "AU-000000000002",
                    "dimension_id": "cross_section_consistency",
                    "sub_aspect_id": "scope_consistency",
                    "disposition": "abstain_insufficient_context",
                    "finding_ids": [],
                    "handoff_ids": [],
                    "attempt_ref": "attempt-001",
                },
            ],
            "findings": [finding.model_dump(mode="json")] if finding else [],
            "handoffs": [],
            "attempt_refs": [
                {
                    "attempt_ref": "attempt-001",
                    "dimension_id": "cross_section_consistency",
                    "status": "completed",
                    "reason_code": None,
                },
                {
                    "attempt_ref": "attempt-002",
                    "dimension_id": "cross_section_consistency",
                    "status": "failed",
                    "reason_code": "provider_failed",
                },
            ],
            "event_stream_sha256": "3" * 64,
        }
    )


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


def test_actual_laj_copies_exact_baseline_and_zero_findings_are_not_reassuring() -> (
    None
):
    _reader, _context, _profile, baseline = _case()
    run = _run(baseline=baseline, with_finding=False)
    matched = compose_matched_non_llm(baseline)
    actual = compose_actual_laj(baseline, run)
    assert verify_additive_baseline(matched, actual) is True
    assert canonical_json_bytes(matched.baseline_payload) == canonical_json_bytes(
        actual.baseline_payload
    )
    presentation = build_presentation(actual, laj_run=run)
    assert presentation.assessed_unit_count == 2
    assert presentation.finding_count == 0
    assert presentation.abstention_count == 1
    assert presentation.failure_count == 1
    assert presentation.advisory_only is True
    assert "未发现问题" not in presentation.disclaimer
    assert presentation.disclaimer == (
        "本次运行未生成候选 finding。该结果不表示报告正确、完整或可交付。"
        "已评价 2 个 assessment units，其中 1 个弃权，1 个运行失败。"
    )


def test_laj_findings_and_duplicate_labels_are_additive_only() -> None:
    _reader, _context, _profile, baseline = _case()
    run = _run(baseline=baseline, with_finding=True)
    annotation = DuplicateAnnotation(
        baseline_item_id=baseline.checklist_items[0].item_id,
        finding_id=run.findings[0].finding_id,
        label="corroborating",
    )
    matched = compose_matched_non_llm(baseline)
    actual = compose_actual_laj(
        baseline,
        run,
        duplicate_annotations=[annotation],
    )
    assert verify_additive_baseline(matched, actual) is True
    assert actual.laj_advice_items == run.findings
    assert actual.duplicate_annotations == [annotation]
    assert len(actual.baseline_payload.checklist_items) == len(
        matched.baseline_payload.checklist_items
    )
    presentation = build_presentation(actual, laj_run=run)
    assert presentation.finding_count == 1
    assert presentation.additional_semantic_findings == run.findings


def test_actual_laj_rejects_different_report_context_or_profile_binding() -> None:
    _reader, _context, _profile, baseline = _case()
    run = _run(baseline=baseline, with_finding=False)
    for field in ("report_sha256", "bounded_context_sha256", "profile_sha256"):
        changed = run.model_copy(update={field: "f" * 64})
        with pytest.raises(
            SemanticEvaluatorError,
            match="composition_input_binding_mismatch",
        ):
            compose_actual_laj(baseline, changed)
