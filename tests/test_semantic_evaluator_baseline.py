"""Independent baseline, witnessed composition, and advisory presentation."""

from __future__ import annotations

import pytest

from multi_agent_brief.semantic_evaluator.admission import admit_inputs
from multi_agent_brief.semantic_evaluator.baseline import (
    build_baseline,
    verify_baseline_payload,
)
from multi_agent_brief.semantic_evaluator.composition import (
    build_presentation,
    compose_actual_laj,
    compose_matched_non_llm,
    verify_additive_baseline,
    verify_composition_record,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    ADMISSION_REQUEST_SCHEMA_ID,
    DIMENSION_RESPONSE_SCHEMA_ID,
    AbstainUnableToAssessResult,
    BoundedRequirement,
    CompositionRecord,
    DimensionResponse,
    FindingDraft,
    FindingEmittedResult,
    InstrumentConfig,
    InstrumentManifest,
    LajCompositionWitness,
    NoFindingResult,
    O3HandoffDraft,
    SemanticEvaluatorEvent,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import (
    build_admitted_report_evidence,
    freeze_bounded_context,
    make_span_locator,
    replay_span,
)
from multi_agent_brief.semantic_evaluator.profile import load_profile
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_bytes,
    sha256_text,
)
from multi_agent_brief.semantic_evaluator.unit_planner import derive_finding_id
from multi_agent_brief.semantic_evaluator.validator import (
    assemble_semantic_assessment_run,
    event_stream_bytes,
    make_dimension_attempt_evidence,
    make_semantic_evaluator_event,
)
import multi_agent_brief.semantic_evaluator.instrument as instrument_module


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
    report_evidence, reader = build_admitted_report_evidence(
        REPORT.encode(), artifact_id="reader-baseline"
    )
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
        report_evidence=report_evidence,
        reader_artifact=reader.artifact,
        bounded_context=context,
        loaded_profile=profile,
    )
    return reader, context, profile, baseline


def _admitted_case():
    reader, context, profile, baseline = _case()
    report_bytes = REPORT.encode()
    decision = admit_inputs(
        {
            "schema_version": ADMISSION_REQUEST_SCHEMA_ID,
            "report_bytes_hex": report_bytes.hex(),
            "declared_report_sha256": sha256_bytes(report_bytes),
            "artifact_id": "reader-baseline",
            "bounded_context": context,
            "declared_bounded_context_sha256": context.context_sha256,
            "instrument_config": InstrumentConfig.minimal_example,
            "trial_id": "trial-baseline",
            "public_data_attestation": True,
            "private_or_confidential_material": False,
            "archive_root": None,
            "workspace_root": None,
        },
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
    evidence = []
    for ordinal, dimension in enumerate(profile.profile.dimensions):
        units = [
            item for item in plan.units if item.dimension_id == dimension.dimension_id
        ]
        prompt = next(
            item
            for item in decision.prompts
            if item.dimension_id == dimension.dimension_id
        )
        if state == "provider_failed" or (
            state == "incomplete" and ordinal == len(profile.profile.dimensions) - 1
        ):
            evidence.append(
                make_dimension_attempt_evidence(
                    trial_id=plan.trial_id,
                    dimension_id=dimension.dimension_id,
                    attempt_ordinal=1,
                    prompt_request_sha256=prompt.request_sha256,
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
        if state == "handoff_only" and ordinal == 0:
            handoff_span = make_span_locator(
                decision.reader.artifact,
                block_id="B000002",
                start_char=0,
                end_char=1,
            )
            unit_results[0] = AbstainUnableToAssessResult(
                assessment_unit_id=units[0].assessment_unit_id,
                disposition="abstain_unable_to_assess",
                reason_code="evidence_dependent_assessment",
                handoffs=[
                    O3HandoffDraft(
                        assessment_unit_id=units[0].assessment_unit_id,
                        type="evidence_dependent_assessment",
                        report_spans=[handoff_span],
                        context_requirement_ids=[],
                        reason="需要外部证据，转人工复核。",
                    )
                ],
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
        raw_bytes = (
            b"synthetic malformed provider response"
            if state == "parser_failed"
            and ordinal == len(profile.profile.dimensions) - 1
            else canonical_json_bytes(raw)
        )
        evidence.append(
            make_dimension_attempt_evidence(
                trial_id=plan.trial_id,
                dimension_id=dimension.dimension_id,
                attempt_ordinal=1,
                prompt_request_sha256=prompt.request_sha256,
                status="completed",
                raw_response_bytes=raw_bytes,
            )
        )
    assembled = assemble_semantic_assessment_run(
        admission=decision,
        dimension_attempt_evidence=evidence,
    )
    expected_state = "completed" if state == "handoff_only" else state
    assert assembled.run.run_status == expected_state
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


def _self_consistent_noncurrent_manifest_payload(
    manifest: InstrumentManifest,
) -> dict:
    payload = manifest.model_dump(mode="json")
    payload["implementation_components"][0]["source_sha256"] = "0" * 64
    component_identity = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "manifest_id", "instrument_sha256"}
    }
    payload["manifest_id"] = f"manifest-{canonical_sha256(component_identity)[:12]}"
    payload["instrument_sha256"] = canonical_sha256(
        {
            "schema_version": payload["schema_version"],
            "manifest_id": payload["manifest_id"],
            **component_identity,
        }
    )
    InstrumentManifest.model_validate(payload)
    return payload


def _replace_finding_id(value, *, old_id: str, new_id: str):
    if isinstance(value, list):
        return [
            _replace_finding_id(item, old_id=old_id, new_id=new_id) for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _replace_finding_id(item, old_id=old_id, new_id=new_id)
            for key, item in value.items()
        }
    return new_id if value == old_id else value


def _forge_rehashed_finding_projection(
    witness: LajCompositionWitness,
) -> LajCompositionWitness:
    payload = witness.model_dump(mode="json")
    finding = payload["run"]["findings"][0]
    old_id = finding["finding_id"]
    finding["observation"] = "重写后的派生观察，不存在于原始 provider bytes。"
    identity = {
        key: value
        for key, value in finding.items()
        if key not in {"finding_id", "status"}
    }
    new_id = derive_finding_id(
        assessment_unit_id=finding["assessment_unit_id"],
        ordinal=0,
        proposal_identity=identity,
    )
    finding["finding_id"] = new_id
    payload["run"]["assessment_units"] = _replace_finding_id(
        payload["run"]["assessment_units"],
        old_id=old_id,
        new_id=new_id,
    )
    payload["validation_report"]["accepted_finding_ids"] = _replace_finding_id(
        payload["validation_report"]["accepted_finding_ids"],
        old_id=old_id,
        new_id=new_id,
    )
    rebuilt_events = []
    for event_payload in payload["events"]:
        event_data = _replace_finding_id(
            event_payload["payload"],
            old_id=old_id,
            new_id=new_id,
        )
        event_data.pop("event_type")
        rebuilt_events.append(
            make_semantic_evaluator_event(
                sequence=event_payload["sequence"],
                run_id=event_payload["run_id"],
                trial_id=event_payload["trial_id"],
                event_type=event_payload["event_type"],
                payload=event_data,
            )
        )
    payload["events"] = [item.model_dump(mode="json") for item in rebuilt_events]
    payload["run"]["event_stream_sha256"] = sha256_bytes(
        event_stream_bytes(rebuilt_events)
    )
    return _rehash_witness(payload)


def _matched(witness: LajCompositionWitness) -> CompositionRecord:
    return compose_matched_non_llm(
        report_evidence=witness.report_evidence,
        reader_artifact=witness.reader_artifact,
        bounded_context=witness.bounded_context,
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
    report_evidence, _replayed = build_admitted_report_evidence(
        REPORT.encode(), artifact_id="reader-baseline"
    )
    second = build_baseline(
        report_evidence=report_evidence,
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
    matched = _matched(assembled.witness)
    actual = compose_actual_laj(assembled.witness)
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
    matched = _matched(assembled.witness)
    actual = compose_actual_laj(assembled.witness)
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
    ("state", "validation_status", "failure_count"),
    [
        ("incomplete", "incomplete", 1),
        ("provider_failed", "incomplete", 9),
        ("parser_failed", "rejected", 1),
        ("validation_failed", "rejected", 1),
        ("security_failed", "rejected", 1),
    ],
)
def test_failure_only_actual_laj_withholds_findings_and_keeps_baseline(
    state: str,
    validation_status: str,
    failure_count: int,
) -> None:
    baseline, assembled = _assembled(state=state, with_finding=True)
    matched = _matched(assembled.witness)
    actual = compose_actual_laj(assembled.witness)
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
    assert presentation.failure_count == failure_count
    assert state in presentation.disclaimer
    assert canonical_json_bytes(actual.baseline_payload) == canonical_json_bytes(
        matched.baseline_payload
    )


def test_completed_handoff_only_run_has_no_advice_or_reassurance() -> None:
    _baseline, assembled = _assembled(state="handoff_only")
    assert assembled.run.run_status == "completed"
    assert assembled.validation_report.validation_status == "accepted"
    assert assembled.run.findings == []
    assert len(assembled.run.handoffs) == 1
    actual = compose_actual_laj(assembled.witness)
    presentation = build_presentation(actual, witness=assembled.witness)
    assert presentation.additional_semantic_findings == []
    assert presentation.abstention_count == 1
    assert presentation.finding_count == 0
    assert "未发现问题" not in presentation.disclaimer


def test_rehashed_finding_run_report_and_events_cannot_launder_provider_bytes() -> None:
    _baseline, assembled = _assembled(with_finding=True)
    forged = _forge_rehashed_finding_projection(assembled.witness)
    assert forged.run.findings[0] != assembled.run.findings[0]
    assert forged.run.event_stream_sha256 != assembled.run.event_stream_sha256
    assert forged.witness_sha256 != assembled.witness.witness_sha256
    with pytest.raises(SemanticEvaluatorError, match="composition_witness_mismatch"):
        compose_actual_laj(forged)


def test_rehashed_unreferenced_reader_block_cannot_replace_exact_report_replay() -> (
    None
):
    _baseline, assembled = _assembled(with_finding=True)
    payload = assembled.witness.model_dump(mode="json")
    referenced = {
        span["block_id"]
        for finding in payload["run"]["findings"]
        for span in finding["report_spans"]
    }
    target = next(
        block
        for block in payload["reader_artifact"]["blocks"]
        if block["block_id"] not in referenced and block["text"]
    )
    target["text"] = ("篡" if target["text"][0] != "篡" else "改") + target["text"][1:]
    target["text_sha256"] = sha256_text(target["text"])
    forged = _rehash_witness(payload)
    with pytest.raises(SemanticEvaluatorError, match="composition_witness_mismatch"):
        compose_actual_laj(forged)


@pytest.mark.parametrize("mutation", ["manifest", "config"])
def test_noncurrent_instrument_roots_cannot_authorize_composition_or_presentation(
    mutation: str,
) -> None:
    _baseline, assembled = _assembled(with_finding=True)
    valid_composition = compose_actual_laj(assembled.witness)
    payload = assembled.witness.model_dump(mode="json")
    if mutation == "manifest":
        payload["instrument_manifest"] = _self_consistent_noncurrent_manifest_payload(
            assembled.witness.instrument_manifest
        )
    else:
        payload["instrument_config"]["model_version"] = "noncurrent-synthetic"
    forged = _rehash_witness(payload)
    with pytest.raises(SemanticEvaluatorError, match="composition_witness_mismatch"):
        compose_actual_laj(forged)
    with pytest.raises(SemanticEvaluatorError):
        build_presentation(valid_composition, witness=forged)


def test_installed_component_change_invalidates_existing_witness(
    monkeypatch,
) -> None:
    _baseline, assembled = _assembled(with_finding=True)
    original = instrument_module.source_sha256_for_module

    def changed_source(module_name: str) -> str:
        if module_name.endswith(".validator"):
            return "0" * 64
        return original(module_name)

    monkeypatch.setattr(
        instrument_module,
        "source_sha256_for_module",
        changed_source,
    )
    with pytest.raises(SemanticEvaluatorError, match="composition_witness_mismatch"):
        compose_actual_laj(assembled.witness)


@pytest.mark.parametrize("forged_status", ["policy_blocked", "archive_failed"])
def test_non_pr_se_1_run_status_cannot_be_forged_into_witness(
    forged_status: str,
) -> None:
    _baseline, assembled = _assembled()
    payload = assembled.witness.model_dump(mode="json")
    payload["run"]["run_status"] = forged_status
    forged = _rehash_witness(payload)
    with pytest.raises(SemanticEvaluatorError, match="composition_witness_mismatch"):
        compose_actual_laj(forged)


def test_stale_context_is_rejected_by_baseline_matched_and_actual_consumers() -> None:
    _baseline, assembled = _assembled()
    stale = assembled.witness.bounded_context.model_copy(deep=True)
    stale.requirements.reverse()
    with pytest.raises(SemanticEvaluatorError, match="baseline_input_binding_mismatch"):
        build_baseline(
            report_evidence=assembled.witness.report_evidence,
            reader_artifact=assembled.witness.reader_artifact,
            bounded_context=stale,
        )
    with pytest.raises(SemanticEvaluatorError, match="composition_record_mismatch"):
        compose_matched_non_llm(
            report_evidence=assembled.witness.report_evidence,
            reader_artifact=assembled.witness.reader_artifact,
            bounded_context=stale,
        )
    payload = assembled.witness.model_dump(mode="json")
    payload["bounded_context"]["requirements"].reverse()
    forged = _rehash_witness(payload)
    with pytest.raises(SemanticEvaluatorError, match="composition_witness_mismatch"):
        compose_actual_laj(forged)


def test_rehashed_bare_baseline_requires_exact_replay_roots() -> None:
    decision, context, _profile, baseline = _admitted_case()
    payload = baseline.model_dump(mode="json")
    payload["checklist_items"][0]["text"] = "rehashed synthetic mutation"
    payload["baseline_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "baseline_sha256"}
    )
    forged = type(baseline).model_validate(payload)
    with pytest.raises(SemanticEvaluatorError, match="baseline_input_binding_mismatch"):
        verify_baseline_payload(
            forged,
            report_evidence=decision.report_evidence,
            reader_artifact=decision.reader.artifact,
            bounded_context=context,
        )


def test_matched_presentation_requires_and_replays_exact_roots() -> None:
    _baseline, assembled = _assembled()
    matched = _matched(assembled.witness)
    with pytest.raises(SemanticEvaluatorError, match="composition_record_mismatch"):
        build_presentation(matched)
    presentation = build_presentation(
        matched,
        report_evidence=assembled.witness.report_evidence,
        reader_artifact=assembled.witness.reader_artifact,
        bounded_context=assembled.witness.bounded_context,
    )
    assert presentation.condition == "matched_non_LLM"
    assert presentation.additional_semantic_findings == []


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
        compose_actual_laj(forged)


@pytest.mark.parametrize(
    "mutation",
    ["advice", "status", "annotation", "baseline"],
)
def test_rehashed_composition_advice_or_status_tampering_is_rejected(
    mutation: str,
) -> None:
    baseline, assembled = _assembled(with_finding=True)
    actual = compose_actual_laj(assembled.witness)
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
    matched = _matched(assembled.witness)
    with pytest.raises(SemanticEvaluatorError, match="composition_record_mismatch"):
        build_presentation(matched, witness=assembled.witness)
