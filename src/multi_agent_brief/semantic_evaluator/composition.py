"""Deterministic matched-baseline and additive-LAJ composition."""

from __future__ import annotations

from multi_agent_brief.semantic_evaluator.baseline import build_baseline
from multi_agent_brief.semantic_evaluator.contracts import (
    BASELINE_SCHEMA_ID,
    COMPOSITION_SCHEMA_ID,
    PRESENTATION_SCHEMA_ID,
    AdmittedReportEvidence,
    BaselinePayload,
    BoundedContext,
    CompositionRecord,
    DuplicateAnnotation,
    LajCompositionWitness,
    PresentationRecord,
    ReaderArtifact,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.resources import EvaluatorResourceError
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_sha256,
    canonical_sha256,
)
from multi_agent_brief.semantic_evaluator.snapshot import (
    EvaluatorResourceSnapshot,
    acquire_resource_snapshot,
)
from multi_agent_brief.semantic_evaluator.validator import (
    _verify_laj_composition_witness_with_roots,
)


NO_FINDING_DISCLAIMER = (
    "本次运行未生成可展示的候选 finding。该结果不表示报告正确、完整或可交付。"
)
ADVISORY_DISCLAIMER = (
    "本记录仅供研究复核；候选 finding 不具有 Gate、Finalize、Delivery、"
    "Claim-Support 或发布权威。"
)


def _finalize_composition(payload: dict[str, object]) -> CompositionRecord:
    return CompositionRecord.model_validate(
        {**payload, "composition_sha256": canonical_sha256(payload)}
    )


def _derive_baseline(
    *,
    report_evidence: AdmittedReportEvidence,
    reader_artifact: ReaderArtifact,
    bounded_context: BoundedContext,
    resource_snapshot: EvaluatorResourceSnapshot,
    mismatch_reason: str,
) -> BaselinePayload:
    try:
        return build_baseline(
            report_evidence=report_evidence,
            reader_artifact=reader_artifact,
            bounded_context=bounded_context,
            _resource_snapshot=resource_snapshot,
        )
    except SemanticEvaluatorError as exc:
        raise SemanticEvaluatorError(mismatch_reason) from exc


def _compose_matched_with_resources(
    *,
    report_evidence: AdmittedReportEvidence,
    reader_artifact: ReaderArtifact,
    bounded_context: BoundedContext,
    resource_snapshot: EvaluatorResourceSnapshot,
) -> CompositionRecord:
    baseline = _derive_baseline(
        report_evidence=report_evidence,
        reader_artifact=reader_artifact,
        bounded_context=bounded_context,
        resource_snapshot=resource_snapshot,
        mismatch_reason="composition_record_mismatch",
    )
    return _finalize_composition(
        {
            "schema_version": COMPOSITION_SCHEMA_ID,
            "condition": "matched_non_LLM",
            "baseline_schema_id": BASELINE_SCHEMA_ID,
            "baseline_sha256": baseline.baseline_sha256,
            "baseline_payload": baseline.model_dump(mode="json"),
            "laj_witness_sha256": None,
            "laj_run_sha256": None,
            "laj_run_status": None,
            "laj_validation_status": None,
            "laj_reason_codes": [],
            "laj_advice_items": [],
            "duplicate_annotations": [],
        }
    )


def compose_matched_non_llm(
    *,
    report_evidence: AdmittedReportEvidence,
    reader_artifact: ReaderArtifact,
    bounded_context: BoundedContext,
) -> CompositionRecord:
    try:
        resources = acquire_resource_snapshot(include_baseline=True)
    except EvaluatorResourceError:
        raise SemanticEvaluatorError("composition_record_mismatch") from None
    return _compose_matched_with_resources(
        report_evidence=report_evidence,
        reader_artifact=reader_artifact,
        bounded_context=bounded_context,
        resource_snapshot=resources,
    )


def _derive_duplicate_annotations(
    baseline: BaselinePayload,
    witness: LajCompositionWitness,
) -> list[DuplicateAnnotation]:
    if not (
        witness.run.run_status == "completed"
        and witness.validation_report.validation_status == "accepted"
    ):
        return []
    annotations: list[DuplicateAnnotation] = []
    for lint_item in baseline.lint_items:
        lint_spans = {canonical_json_bytes(span) for span in lint_item.report_spans}
        for finding in witness.run.findings:
            if lint_spans & {
                canonical_json_bytes(span) for span in finding.report_spans
            }:
                annotations.append(
                    DuplicateAnnotation(
                        baseline_item_id=lint_item.item_id,
                        finding_id=finding.finding_id,
                        label="corroborating",
                    )
                )
    return sorted(
        annotations,
        key=lambda item: (item.baseline_item_id, item.finding_id, item.label),
    )


def _compose_actual_verified(
    witness: LajCompositionWitness,
    *,
    resource_snapshot: EvaluatorResourceSnapshot,
) -> CompositionRecord:
    baseline = _derive_baseline(
        report_evidence=witness.report_evidence,
        reader_artifact=witness.reader_artifact,
        bounded_context=witness.bounded_context,
        resource_snapshot=resource_snapshot,
        mismatch_reason="composition_witness_mismatch",
    )
    run = witness.run
    report = witness.validation_report
    if (
        run.report_sha256 != baseline.report_sha256
        or run.bounded_context_sha256 != baseline.bounded_context_sha256
        or run.profile_sha256 != baseline.profile_sha256
    ):
        raise SemanticEvaluatorError("composition_input_binding_mismatch")
    displayable = (
        run.run_status == "completed" and report.validation_status == "accepted"
    )
    advice = list(run.findings) if displayable else []
    annotations = _derive_duplicate_annotations(baseline, witness) if advice else []
    return _finalize_composition(
        {
            "schema_version": COMPOSITION_SCHEMA_ID,
            "condition": "actual_LAJ",
            "baseline_schema_id": BASELINE_SCHEMA_ID,
            "baseline_sha256": baseline.baseline_sha256,
            "baseline_payload": baseline.model_dump(mode="json"),
            "laj_witness_sha256": witness.witness_sha256,
            "laj_run_sha256": canonical_model_sha256(run),
            "laj_run_status": run.run_status,
            "laj_validation_status": report.validation_status,
            "laj_reason_codes": list(report.reason_codes),
            "laj_advice_items": [item.model_dump(mode="json") for item in advice],
            "duplicate_annotations": [
                item.model_dump(mode="json") for item in annotations
            ],
        }
    )


def compose_actual_laj(witness: LajCompositionWitness) -> CompositionRecord:
    verified, roots = _verify_laj_composition_witness_with_roots(
        witness,
        include_baseline=True,
    )
    return _compose_actual_verified(
        verified,
        resource_snapshot=roots.instrument_snapshot.resources,
    )


def _verify_composition_record_with_context(
    composition: CompositionRecord,
    *,
    witness: LajCompositionWitness | None = None,
    report_evidence: AdmittedReportEvidence | None = None,
    reader_artifact: ReaderArtifact | None = None,
    bounded_context: BoundedContext | None = None,
) -> tuple[CompositionRecord, LajCompositionWitness | None]:
    try:
        strict = CompositionRecord.model_validate(composition.model_dump(mode="json"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SemanticEvaluatorError("composition_record_mismatch") from exc
    if canonical_json_bytes(strict) != canonical_json_bytes(composition):
        raise SemanticEvaluatorError("composition_record_mismatch")
    if strict.condition == "matched_non_LLM":
        if witness is not None or any(
            item is None for item in (report_evidence, reader_artifact, bounded_context)
        ):
            raise SemanticEvaluatorError("composition_record_mismatch")
        try:
            resources = acquire_resource_snapshot(include_baseline=True)
        except EvaluatorResourceError:
            raise SemanticEvaluatorError("composition_record_mismatch") from None
        expected = _compose_matched_with_resources(
            report_evidence=report_evidence,
            reader_artifact=reader_artifact,
            bounded_context=bounded_context,
            resource_snapshot=resources,
        )
        verified_witness = None
    else:
        if witness is None or any(
            item is not None
            for item in (report_evidence, reader_artifact, bounded_context)
        ):
            raise SemanticEvaluatorError("composition_record_mismatch")
        verified_witness, roots = _verify_laj_composition_witness_with_roots(
            witness,
            include_baseline=True,
        )
        expected = _compose_actual_verified(
            verified_witness,
            resource_snapshot=roots.instrument_snapshot.resources,
        )
    if canonical_json_bytes(expected) != canonical_json_bytes(strict):
        raise SemanticEvaluatorError("composition_record_mismatch")
    return strict, verified_witness


def verify_composition_record(
    composition: CompositionRecord,
    *,
    witness: LajCompositionWitness | None = None,
    report_evidence: AdmittedReportEvidence | None = None,
    reader_artifact: ReaderArtifact | None = None,
    bounded_context: BoundedContext | None = None,
) -> bool:
    _verify_composition_record_with_context(
        composition,
        witness=witness,
        report_evidence=report_evidence,
        reader_artifact=reader_artifact,
        bounded_context=bounded_context,
    )
    return True


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


def _failure_count(witness: LajCompositionWitness) -> int:
    terminal_by_dimension = {}
    for attempt in witness.run.attempt_refs:
        terminal_by_dimension[attempt.dimension_id] = attempt
    terminal_failures = sum(
        item.status == "failed" for item in terminal_by_dimension.values()
    )
    if terminal_failures:
        return terminal_failures
    return int(
        witness.run.run_status
        in {"parser_failed", "validation_failed", "security_failed"}
    )


def build_presentation(
    composition: CompositionRecord,
    *,
    witness: LajCompositionWitness | None = None,
    report_evidence: AdmittedReportEvidence | None = None,
    reader_artifact: ReaderArtifact | None = None,
    bounded_context: BoundedContext | None = None,
) -> PresentationRecord:
    _strict_composition, verified_witness = _verify_composition_record_with_context(
        composition,
        witness=witness,
        report_evidence=report_evidence,
        reader_artifact=reader_artifact,
        bounded_context=bounded_context,
    )
    if composition.condition == "actual_LAJ":
        if verified_witness is None:
            raise SemanticEvaluatorError("composition_witness_mismatch")
        run = verified_witness.run
        report = verified_witness.validation_report
        assessed = len(run.assessment_units)
        abstentions = sum(
            item.disposition.startswith("abstain_") for item in run.assessment_units
        )
        failures = _failure_count(verified_witness)
        withheld = (
            0
            if run.run_status == "completed" and report.validation_status == "accepted"
            else len(run.findings)
        )
        witness_sha = verified_witness.witness_sha256
        run_status = run.run_status
        validation_status = report.validation_status
        failure_reasons = list(report.reason_codes)
    else:
        assessed = 0
        abstentions = 0
        failures = 0
        withheld = 0
        witness_sha = None
        run_status = None
        validation_status = None
        failure_reasons = []
    finding_count = len(composition.laj_advice_items)
    if finding_count == 0:
        disclaimer = (
            f"{NO_FINDING_DISCLAIMER}状态：{run_status or 'matched_non_LLM'}/"
            f"{validation_status or 'not_applicable'}；已评价 {assessed} 个 assessment "
            f"units，其中 {abstentions} 个弃权，{failures} 个终态失败，"
            f"{withheld} 个 finding 被保留但未展示。"
        )
    else:
        disclaimer = ADVISORY_DISCLAIMER
    identity = [
        composition.composition_sha256,
        witness_sha,
        assessed,
        abstentions,
        failures,
        withheld,
    ]
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
        "laj_witness_sha256": witness_sha,
        "laj_run_status": run_status,
        "laj_validation_status": validation_status,
        "failure_reason_codes": failure_reasons,
        "assessed_unit_count": assessed,
        "finding_count": finding_count,
        "withheld_finding_count": withheld,
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
    "verify_composition_record",
]
