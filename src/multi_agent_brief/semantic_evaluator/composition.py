"""Deterministic matched-baseline and additive-LAJ composition."""

from __future__ import annotations

from typing import Iterable

from multi_agent_brief.semantic_evaluator.contracts import (
    BASELINE_SCHEMA_ID,
    COMPOSITION_SCHEMA_ID,
    PRESENTATION_SCHEMA_ID,
    BaselinePayload,
    CompositionRecord,
    DuplicateAnnotation,
    PresentationRecord,
    SemanticAssessmentRun,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_payload,
    canonical_model_sha256,
    canonical_sha256,
)


NO_FINDING_DISCLAIMER = (
    "本次运行未生成候选 finding。该结果不表示报告正确、完整或可交付。"
)
ADVISORY_DISCLAIMER = (
    "本记录仅供研究复核；候选 finding 不具有 Gate、Finalize、Delivery、"
    "Claim-Support 或发布权威。"
)


def _copy_baseline(baseline: BaselinePayload) -> BaselinePayload:
    expected = canonical_model_sha256(baseline, exclude=("baseline_sha256",))
    if baseline.baseline_sha256 != expected:
        raise SemanticEvaluatorError("baseline_hash_mismatch")
    return BaselinePayload.model_validate(baseline.model_dump(mode="json"))


def _finalize_composition(payload: dict[str, object]) -> CompositionRecord:
    return CompositionRecord.model_validate(
        {**payload, "composition_sha256": canonical_sha256(payload)}
    )


def compose_matched_non_llm(baseline: BaselinePayload) -> CompositionRecord:
    frozen_baseline = _copy_baseline(baseline)
    return _finalize_composition(
        {
            "schema_version": COMPOSITION_SCHEMA_ID,
            "condition": "matched_non_LLM",
            "baseline_schema_id": BASELINE_SCHEMA_ID,
            "baseline_sha256": frozen_baseline.baseline_sha256,
            "baseline_payload": frozen_baseline.model_dump(mode="json"),
            "laj_run_sha256": None,
            "laj_advice_items": [],
            "duplicate_annotations": [],
        }
    )


def compose_actual_laj(
    baseline: BaselinePayload,
    laj_run: SemanticAssessmentRun,
    *,
    duplicate_annotations: Iterable[DuplicateAnnotation] = (),
) -> CompositionRecord:
    frozen_baseline = _copy_baseline(baseline)
    if (
        laj_run.report_sha256 != frozen_baseline.report_sha256
        or laj_run.bounded_context_sha256 != frozen_baseline.bounded_context_sha256
        or laj_run.profile_sha256 != frozen_baseline.profile_sha256
    ):
        raise SemanticEvaluatorError("composition_input_binding_mismatch")
    annotations = sorted(
        duplicate_annotations,
        key=lambda item: (item.baseline_item_id, item.finding_id, item.label),
    )
    baseline_ids = {
        item.item_id
        for item in [*frozen_baseline.checklist_items, *frozen_baseline.lint_items]
    }
    finding_ids = {item.finding_id for item in laj_run.findings}
    if any(item.baseline_item_id not in baseline_ids for item in annotations):
        raise SemanticEvaluatorError("duplicate_annotation_baseline_unknown")
    if any(item.finding_id not in finding_ids for item in annotations):
        raise SemanticEvaluatorError("duplicate_annotation_finding_unknown")
    return _finalize_composition(
        {
            "schema_version": COMPOSITION_SCHEMA_ID,
            "condition": "actual_LAJ",
            "baseline_schema_id": BASELINE_SCHEMA_ID,
            "baseline_sha256": frozen_baseline.baseline_sha256,
            "baseline_payload": frozen_baseline.model_dump(mode="json"),
            "laj_run_sha256": canonical_model_sha256(laj_run),
            "laj_advice_items": [
                item.model_dump(mode="json") for item in laj_run.findings
            ],
            "duplicate_annotations": [
                item.model_dump(mode="json") for item in annotations
            ],
        }
    )


def verify_additive_baseline(
    matched: CompositionRecord,
    actual: CompositionRecord,
) -> bool:
    return (
        matched.condition == "matched_non_LLM"
        and actual.condition == "actual_LAJ"
        and matched.baseline_sha256 == actual.baseline_sha256
        and canonical_json_bytes(matched.baseline_payload)
        == canonical_json_bytes(actual.baseline_payload)
    )


def build_presentation(
    composition: CompositionRecord,
    *,
    laj_run: SemanticAssessmentRun | None = None,
) -> PresentationRecord:
    expected_composition_sha = canonical_sha256(
        canonical_model_payload(composition, exclude=("composition_sha256",))
    )
    if expected_composition_sha != composition.composition_sha256:
        raise SemanticEvaluatorError("composition_hash_mismatch")
    if composition.condition == "actual_LAJ":
        if (
            laj_run is None
            or canonical_model_sha256(laj_run) != composition.laj_run_sha256
        ):
            raise SemanticEvaluatorError("composition_run_mismatch")
        assessed = len(laj_run.assessment_units)
        abstentions = sum(
            item.disposition.startswith("abstain_") for item in laj_run.assessment_units
        )
        failures = sum(item.status == "failed" for item in laj_run.attempt_refs)
        if laj_run.run_status != "completed" and failures == 0:
            failures = 1
    else:
        if laj_run is not None:
            raise SemanticEvaluatorError("matched_baseline_run_forbidden")
        assessed = 0
        abstentions = 0
        failures = 0
    finding_count = len(composition.laj_advice_items)
    if finding_count == 0:
        disclaimer = (
            f"{NO_FINDING_DISCLAIMER}已评价 {assessed} 个 assessment units，"
            f"其中 {abstentions} 个弃权，{failures} 个运行失败。"
        )
    else:
        disclaimer = ADVISORY_DISCLAIMER
    identity = [composition.composition_sha256, assessed, abstentions, failures]
    payload = {
        "schema_version": PRESENTATION_SCHEMA_ID,
        "presentation_id": f"presentation-{canonical_sha256(identity)[:12]}",
        "condition": composition.condition,
        "composition_sha256": composition.composition_sha256,
        "baseline_sha256": composition.baseline_sha256,
        "baseline_items": [
            item.model_dump(mode="json")
            for item in composition.baseline_payload.checklist_items
        ],
        "baseline_lint_items": [
            item.model_dump(mode="json")
            for item in composition.baseline_payload.lint_items
        ],
        "additional_semantic_findings": [
            item.model_dump(mode="json") for item in composition.laj_advice_items
        ],
        "assessed_unit_count": assessed,
        "finding_count": finding_count,
        "abstention_count": abstentions,
        "failure_count": failures,
        "advisory_only": True,
        "disclaimer": disclaimer,
    }
    return PresentationRecord.model_validate(
        {**payload, "presentation_sha256": canonical_sha256(payload)}
    )


__all__ = [
    "ADVISORY_DISCLAIMER",
    "NO_FINDING_DISCLAIMER",
    "build_presentation",
    "compose_actual_laj",
    "compose_matched_non_llm",
    "verify_additive_baseline",
]
