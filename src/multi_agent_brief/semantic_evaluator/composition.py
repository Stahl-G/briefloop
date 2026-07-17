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
    strict_model_payload,
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
    baseline: BaselinePayload | None = None
    try:
        baseline = build_baseline(
            report_evidence=report_evidence,
            reader_artifact=reader_artifact,
            bounded_context=bounded_context,
            _resource_snapshot=resource_snapshot,
        )
    except SemanticEvaluatorError:
        pass
    if baseline is None:
        raise SemanticEvaluatorError(mismatch_reason) from None
    return baseline


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
            "baseline_payload": baseline.model_dump(mode="json", warnings="error"),
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
    result: CompositionRecord | None = None
    try:
        resources = acquire_resource_snapshot(include_baseline=True)
    except EvaluatorResourceError:
        resources = None
    if resources is not None:
        try:
            result = _compose_matched_with_resources(
                report_evidence=report_evidence,
                reader_artifact=reader_artifact,
                bounded_context=bounded_context,
                resource_snapshot=resources,
            )
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            SemanticEvaluatorError,
        ):
            pass
    if result is None:
        raise SemanticEvaluatorError("composition_record_mismatch") from None
    return result


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
            "baseline_payload": baseline.model_dump(mode="json", warnings="error"),
            "laj_witness_sha256": witness.witness_sha256,
            "laj_run_sha256": canonical_model_sha256(run),
            "laj_run_status": run.run_status,
            "laj_validation_status": report.validation_status,
            "laj_reason_codes": list(report.reason_codes),
            "laj_advice_items": [
                item.model_dump(mode="json", warnings="error") for item in advice
            ],
            "duplicate_annotations": [
                item.model_dump(mode="json", warnings="error") for item in annotations
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
    strict: CompositionRecord | None = None
    exact = False
    try:
        strict = CompositionRecord.model_validate(strict_model_payload(composition))
        exact = canonical_json_bytes(strict) == canonical_json_bytes(composition)
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    if strict is None or not exact:
        raise SemanticEvaluatorError("composition_record_mismatch") from None
    if strict.condition == "matched_non_LLM":
        if witness is not None or any(
            item is None for item in (report_evidence, reader_artifact, bounded_context)
        ):
            raise SemanticEvaluatorError("composition_record_mismatch")
        resources: EvaluatorResourceSnapshot | None = None
        try:
            resources = acquire_resource_snapshot(include_baseline=True)
        except EvaluatorResourceError:
            pass
        if resources is None:
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
    *,
    witness: LajCompositionWitness,
) -> bool:
    verified = False
    try:
        strict_matched = CompositionRecord.model_validate(strict_model_payload(matched))
        strict_actual = CompositionRecord.model_validate(strict_model_payload(actual))
        verified_witness, roots = _verify_laj_composition_witness_with_roots(
            witness,
            include_baseline=True,
        )
        expected_matched = _compose_matched_with_resources(
            report_evidence=verified_witness.report_evidence,
            reader_artifact=verified_witness.reader_artifact,
            bounded_context=verified_witness.bounded_context,
            resource_snapshot=roots.instrument_snapshot.resources,
        )
        expected_actual = _compose_actual_verified(
            verified_witness,
            resource_snapshot=roots.instrument_snapshot.resources,
        )
        verified = all(
            canonical_json_bytes(left) == canonical_json_bytes(right)
            for left, right in (
                (strict_matched, matched),
                (strict_actual, actual),
                (strict_matched, expected_matched),
                (strict_actual, expected_actual),
                (strict_matched.baseline_payload, strict_actual.baseline_payload),
            )
        )
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        SemanticEvaluatorError,
    ):
        pass
    return verified


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
    strict_composition, verified_witness = _verify_composition_record_with_context(
        composition,
        witness=witness,
        report_evidence=report_evidence,
        reader_artifact=reader_artifact,
        bounded_context=bounded_context,
    )
    if strict_composition.condition == "actual_LAJ":
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
    finding_count = len(strict_composition.laj_advice_items)
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
        strict_composition.composition_sha256,
        witness_sha,
        assessed,
        abstentions,
        failures,
        withheld,
    ]
    payload = {
        "schema_version": PRESENTATION_SCHEMA_ID,
        "presentation_id": f"presentation-{canonical_sha256(identity)[:12]}",
        "condition": strict_composition.condition,
        "composition_sha256": strict_composition.composition_sha256,
        "baseline_sha256": strict_composition.baseline_sha256,
        "baseline_items": [
            item.model_dump(mode="json", warnings="error")
            for item in strict_composition.baseline_payload.checklist_items
        ],
        "baseline_lint_items": [
            item.model_dump(mode="json", warnings="error")
            for item in strict_composition.baseline_payload.lint_items
        ],
        "additional_semantic_findings": [
            item.model_dump(mode="json", warnings="error")
            for item in strict_composition.laj_advice_items
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
