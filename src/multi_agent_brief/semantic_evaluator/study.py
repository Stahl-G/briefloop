"""Deterministic, advisory-only LAJ study preparation and comparison."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Any, BinaryIO, Mapping

from pydantic import ValidationError

from multi_agent_brief.semantic_evaluator.archive import verify_shadow_archive
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import normalize_markdown
from multi_agent_brief.semantic_evaluator.runner import (
    PROFILE_ID,
    PreparedShadowRun,
    ShadowRunResult,
    execute_prepared_shadow_run,
    prepare_shadow_run,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_sha256,
    canonical_sha256,
    sha256_bytes,
    sha256_text,
    source_sha256_for_module,
)
from multi_agent_brief.semantic_evaluator.shadow_contracts import ProviderAttemptRecord
from multi_agent_brief.semantic_evaluator.study_contracts import (
    BUDGET_PREFLIGHT_SCHEMA_ID,
    PROVIDER_EXECUTION_AUTHORIZATION_SCHEMA_ID,
    RESOLVED_SENSITIVITY_CASE_SCHEMA_ID,
    SENSITIVITY_COMPARISON_SCHEMA_ID,
    SENSITIVITY_MANIFEST_SCHEMA_ID,
    STUDY_CONTRACT_MODELS,
    STUDY_EXECUTION_EVIDENCE_SCHEMA_ID,
    LajBudgetPreflightV1,
    LajProviderBudgetPolicyV1,
    LajProviderExecutionAuthorizationV1,
    LajSensitivityComparisonV1,
    LajSensitivityGroundTruthSourceV1,
    LajSensitivityManifestV1,
    LajStudyDeclarationV1,
    LajStudyExecutionEvidenceV1,
    ResolvedSensitivityCaseV1,
    ResolvedSensitivityMutationV1,
    SensitivityCandidateLinkV1,
    SensitivityComparisonRowV1,
)


@dataclass(frozen=True)
class StudyEligibility:
    eligible: bool
    evidence_class: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "evidence_class": self.evidence_class,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class StudyPreflightResult:
    ok: bool
    eligibility: StudyEligibility | None
    resolved_case: ResolvedSensitivityCaseV1 | None
    authorization: LajProviderExecutionAuthorizationV1 | None
    preflight: LajBudgetPreflightV1 | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "eligibility": self.eligibility.to_dict() if self.eligibility else None,
            "resolved_case": _model_dict(self.resolved_case),
            "authorization": _model_dict(self.authorization),
            "preflight": _model_dict(self.preflight),
            "reason_codes": list(self.reason_codes),
            "provider_calls": 0,
            "runtime_authority": False,
        }


@dataclass(frozen=True)
class BudgetedShadowRunResult:
    preflight: LajBudgetPreflightV1 | None
    shadow_result: ShadowRunResult | None
    execution_evidence: LajStudyExecutionEvidenceV1 | None
    reason_codes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return bool(
            self.shadow_result and self.shadow_result.ok and self.execution_evidence
        )

    def to_dict(self) -> dict[str, object]:
        shadow = None
        if self.shadow_result is not None:
            shadow = {
                "ok": self.shadow_result.ok,
                "replayed": self.shadow_result.replayed,
                "archive_complete": self.shadow_result.archive_complete,
                "receipt_id": self.shadow_result.receipt_id,
                "run_status": self.shadow_result.run_status,
                "validation_status": self.shadow_result.validation_status,
                "reason_codes": list(self.shadow_result.reason_codes),
                "execution_origin": self.shadow_result.execution_origin,
                "qualification_class": self.shadow_result.qualification_class,
                "qualification_eligible": self.shadow_result.qualification_eligible,
            }
        return {
            "ok": self.ok,
            "preflight": _model_dict(self.preflight),
            "shadow_result": shadow,
            "execution_evidence": _model_dict(self.execution_evidence),
            "reason_codes": list(self.reason_codes),
            "runtime_authority": False,
        }


def _model_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return value.model_dump(mode="json", warnings="error")


def _study_preparation_reasons(result: ShadowRunResult) -> tuple[str, ...]:
    if result.reason_codes == ("prompt_sizer_unavailable",):
        return ("budget_preflight_unavailable",)
    return result.reason_codes


def parse_study_json(raw: bytes, model: type[Any], reason_code: str) -> Any:
    duplicate = False

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicate
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicate = True
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_hook)
        if duplicate or type(payload) is not dict:
            raise ValueError
        return model.model_validate(payload)
    except (UnicodeError, ValueError, TypeError, ValidationError, RecursionError):
        raise SemanticEvaluatorError(reason_code) from None


def parse_sensitivity_manifest(raw: bytes) -> LajSensitivityManifestV1:
    """Validate the exact private source shape, then remove every local locator."""

    source = parse_study_json(
        raw,
        LajSensitivityGroundTruthSourceV1,
        "sensitivity_manifest_invalid",
    )
    mutations = [
        {
            "mutation_id": item.mutation_id,
            "before_text": item.before_text,
            "after_text": item.after_text,
            "inserted_text": item.inserted_text,
            "expected_primary_dimension": item.expected_primary_dimension,
            "expected_secondary_dimension": item.expected_secondary_dimension,
            "expected_severity": item.expected_severity,
            "public_label": item.label,
            "rationale": item.in_scope_basis,
        }
        for item in source.mutations
    ]
    payload: dict[str, object] = {
        "schema_version": SENSITIVITY_MANIFEST_SCHEMA_ID,
        "source_manifest_sha256": sha256_bytes(raw),
        "control_report_sha256": source.control_report.sha256,
        "mutated_report_sha256": source.mutated_report.sha256,
        "mutation_count": source.mutation_count,
        "mutations": mutations,
    }
    try:
        return LajSensitivityManifestV1.model_validate(
            {**payload, "manifest_sha256": canonical_sha256(payload)}
        )
    except (ValidationError, ValueError, TypeError):
        raise SemanticEvaluatorError("sensitivity_manifest_invalid") from None


def evaluate_study_eligibility(
    declaration: LajStudyDeclarationV1,
) -> StudyEligibility:
    if declaration.study_kind == "sensitivity_calibration":
        eligible = (
            declaration.public_safe
            and not declaration.synthetic
            and declaration.artifact_class
            in {"technical_postmortem", "self_diagnosing_case_study"}
            and declaration.expected_mutation_count > 0
        )
        return StudyEligibility(
            eligible=eligible,
            evidence_class="calibration_only" if eligible else "ineligible",
            reason_codes=() if eligible else ("utility_target_ineligible",),
        )
    eligible = (
        declaration.artifact_class == "reader_facing_business_report"
        and declaration.public_safe
        and not declaration.synthetic
        and not declaration.self_diagnosing
        and declaration.reader_facing
        and declaration.expected_mutation_count == 0
    )
    return StudyEligibility(
        eligible=eligible,
        evidence_class="product_utility_candidate" if eligible else "ineligible",
        reason_codes=() if eligible else ("utility_target_ineligible",),
    )


def verify_study_report_binding(
    declaration: LajStudyDeclarationV1, report: str | Path
) -> bool:
    try:
        return sha256_bytes(Path(report).read_bytes()) == declaration.report_sha256
    except OSError:
        return False


def resolve_sensitivity_case(
    *,
    declaration: LajStudyDeclarationV1,
    manifest: LajSensitivityManifestV1,
    control_report_bytes: bytes,
    mutated_report_bytes: bytes,
) -> ResolvedSensitivityCaseV1:
    try:
        if (
            declaration.study_kind != "sensitivity_calibration"
            or declaration.expected_mutation_count != manifest.mutation_count
            or declaration.report_sha256 != manifest.mutated_report_sha256
            or sha256_bytes(control_report_bytes) != manifest.control_report_sha256
            or sha256_bytes(mutated_report_bytes) != manifest.mutated_report_sha256
        ):
            raise ValueError
        control = normalize_markdown(control_report_bytes, artifact_id="study-control")
        mutated = normalize_markdown(mutated_report_bytes, artifact_id="study-mutated")
        resolved: list[ResolvedSensitivityMutationV1] = []
        occupied: list[tuple[str, int, int]] = []
        for item in manifest.mutations:
            if control.normalized_text.count(item.before_text) != 1:
                raise ValueError
            if mutated.normalized_text.count(item.after_text) != 1:
                raise ValueError
            if mutated.normalized_text.count(item.inserted_text) != 1:
                raise ValueError
            inserted_start = mutated.normalized_text.index(item.inserted_text)
            inserted_end = inserted_start + len(item.inserted_text)
            after_start = mutated.normalized_text.index(item.after_text)
            after_end = after_start + len(item.after_text)
            if not (after_start <= inserted_start < inserted_end <= after_end):
                raise ValueError
            matches = [
                block
                for block in mutated.artifact.blocks
                if block.start_char <= inserted_start and inserted_end <= block.end_char
            ]
            if len(matches) != 1:
                raise ValueError
            block = matches[0]
            local_start = inserted_start - block.start_char
            local_end = inserted_end - block.start_char
            if any(
                block.block_id == occupied_block
                and max(local_start, start) < min(local_end, end)
                for occupied_block, start, end in occupied
            ):
                raise ValueError
            occupied.append((block.block_id, local_start, local_end))
            resolved.append(
                ResolvedSensitivityMutationV1(
                    mutation_id=item.mutation_id,
                    mutated_report_sha256=manifest.mutated_report_sha256,
                    normalized_text_sha256=mutated.artifact.normalized_text_sha256,
                    block_id=block.block_id,
                    start_char=local_start,
                    end_char=local_end,
                    excerpt_sha256=sha256_text(item.inserted_text),
                    expected_primary_dimension=item.expected_primary_dimension,
                    expected_secondary_dimension=item.expected_secondary_dimension,
                    expected_severity=item.expected_severity,
                )
            )
        payload: dict[str, object] = {
            "schema_version": RESOLVED_SENSITIVITY_CASE_SCHEMA_ID,
            "study_id": declaration.study_id,
            "study_declaration_sha256": declaration.declaration_sha256,
            "source_manifest_sha256": manifest.source_manifest_sha256,
            "control_report_sha256": manifest.control_report_sha256,
            "mutated_report_sha256": manifest.mutated_report_sha256,
            "normalized_text_sha256": mutated.artifact.normalized_text_sha256,
            "resolved_mutation_count": len(resolved),
            "resolved_mutations": [item.model_dump(mode="json") for item in resolved],
        }
        return ResolvedSensitivityCaseV1.model_validate(
            {**payload, "case_sha256": canonical_sha256(payload)}
        )
    except (
        SemanticEvaluatorError,
        ValidationError,
        ValueError,
        TypeError,
        UnicodeError,
    ):
        raise SemanticEvaluatorError("sensitivity_manifest_invalid") from None


def make_execution_authorization(
    *,
    study_id: str,
    prepared: PreparedShadowRun,
    policy: LajProviderBudgetPolicyV1,
) -> LajProviderExecutionAuthorizationV1:
    admission = prepared.admission
    payload: dict[str, object] = {
        "schema_version": PROVIDER_EXECUTION_AUTHORIZATION_SCHEMA_ID,
        "study_id": study_id,
        "trial_id": admission.input_binding.trial_id,
        "report_sha256": admission.input_binding.report_sha256,
        "bounded_context_sha256": admission.input_binding.bounded_context_sha256,
        "instrument_sha256": admission.instrument_manifest.instrument_sha256,
        "assessment_plan_sha256": admission.assessment_plan.assessment_plan_sha256,
        "ordered_prompt_request_sha256s": list(admission.prompt_request_sha256s),
        "budget_policy_sha256": policy.policy_sha256,
    }
    return LajProviderExecutionAuthorizationV1.model_validate(
        {**payload, "authorization_sha256": canonical_sha256(payload)}
    )


def _count_semantics(sizer_id: str) -> str:
    if sizer_id == "openai_tiktoken_v1":
        return "exact_tokenizer"
    if sizer_id == "local_proxy_utf8_bytes_conservative_v1":
        return "conservative_utf8_byte_upper_bound"
    return "synthetic_test_counter"


def compute_budget_preflight(
    *,
    prepared: PreparedShadowRun,
    authorization: LajProviderExecutionAuthorizationV1,
    policy: LajProviderBudgetPolicyV1,
) -> LajBudgetPreflightV1:
    admission = prepared.admission
    expected = make_execution_authorization(
        study_id=authorization.study_id, prepared=prepared, policy=policy
    )
    if canonical_json_bytes(expected) != canonical_json_bytes(authorization):
        raise SemanticEvaluatorError("provider_execution_authorization_invalid")
    try:
        counts = [
            prepared.prompt_sizer.count_tokens(
                system_text=prompt.system_text, user_text=prompt.user_text
            )
            for prompt in admission.prompts
        ]
        if not counts or any(type(value) is not int or value <= 0 for value in counts):
            raise ValueError
        sizer_values = (
            prepared.prompt_sizer.sizer_id,
            prepared.prompt_sizer.sizer_version,
            prepared.prompt_sizer.package_name,
            prepared.prompt_sizer.package_version,
            prepared.prompt_sizer.encoding_name,
        )
        if any(type(value) is not str or not value for value in sizer_values):
            raise ValueError
    except Exception:
        raise SemanticEvaluatorError("budget_preflight_unavailable") from None
    attempts = admission.instrument_config.retry_policy.max_attempts
    calls = len(counts) * attempts
    token_bound = sum(counts) * attempts
    reasons: list[str] = []
    if calls > policy.max_provider_calls:
        reasons.append("budget_provider_call_limit_exceeded")
    elif token_bound > policy.max_input_tokens:
        reasons.append("budget_input_token_limit_exceeded")
    payload: dict[str, object] = {
        "schema_version": BUDGET_PREFLIGHT_SCHEMA_ID,
        "authorization_sha256": authorization.authorization_sha256,
        "input_binding_sha256": admission.input_binding.input_binding_sha256,
        "instrument_sha256": admission.instrument_manifest.instrument_sha256,
        "assessment_plan_sha256": admission.assessment_plan.assessment_plan_sha256,
        "ordered_prompt_request_sha256s": list(admission.prompt_request_sha256s),
        "prompt_sizer_id": sizer_values[0],
        "prompt_sizer_version": sizer_values[1],
        "tokenizer_package": sizer_values[2],
        "tokenizer_version": sizer_values[3],
        "tokenizer_encoding": sizer_values[4],
        "count_semantics": _count_semantics(sizer_values[0]),
        "per_prompt_input_counts": counts,
        "max_attempts": attempts,
        "planned_provider_calls": calls,
        "planned_input_token_upper_bound": token_bound,
        "max_provider_calls": policy.max_provider_calls,
        "max_input_tokens": policy.max_input_tokens,
        "decision": "blocked" if reasons else "allowed",
        "reason_codes": reasons,
    }
    return LajBudgetPreflightV1.model_validate(
        {**payload, "preflight_sha256": canonical_sha256(payload)}
    )


def prepare_study(
    *,
    declaration: LajStudyDeclarationV1,
    report: str | Path,
    bounded_context: str | Path,
    instrument: str | Path,
    trial_id: str,
    archive_root: str | Path,
    budget_policy: LajProviderBudgetPolicyV1,
    manifest: LajSensitivityManifestV1 | None = None,
    control_report: str | Path | None = None,
) -> StudyPreflightResult:
    eligibility = evaluate_study_eligibility(declaration)
    if not eligibility.eligible:
        return StudyPreflightResult(
            False, eligibility, None, None, None, eligibility.reason_codes
        )
    try:
        report_bytes = Path(report).read_bytes()
    except OSError:
        return StudyPreflightResult(
            False, eligibility, None, None, None, ("study_report_binding_mismatch",)
        )
    if sha256_bytes(report_bytes) != declaration.report_sha256:
        return StudyPreflightResult(
            False, eligibility, None, None, None, ("study_report_binding_mismatch",)
        )
    resolved_case = None
    if declaration.study_kind == "sensitivity_calibration":
        if manifest is None or control_report is None:
            return StudyPreflightResult(
                False, eligibility, None, None, None, ("sensitivity_manifest_invalid",)
            )
        try:
            resolved_case = resolve_sensitivity_case(
                declaration=declaration,
                manifest=manifest,
                control_report_bytes=Path(control_report).read_bytes(),
                mutated_report_bytes=report_bytes,
            )
        except (OSError, SemanticEvaluatorError):
            return StudyPreflightResult(
                False, eligibility, None, None, None, ("sensitivity_manifest_invalid",)
            )
    prepared = prepare_shadow_run(
        report=report,
        bounded_context=bounded_context,
        profile=PROFILE_ID,
        instrument=instrument,
        trial_id=trial_id,
        archive_root=archive_root,
    )
    if isinstance(prepared, ShadowRunResult):
        return StudyPreflightResult(
            False,
            eligibility,
            resolved_case,
            None,
            None,
            _study_preparation_reasons(prepared),
        )
    authorization = make_execution_authorization(
        study_id=declaration.study_id, prepared=prepared, policy=budget_policy
    )
    if authorization.report_sha256 != declaration.report_sha256:
        return StudyPreflightResult(
            False,
            eligibility,
            resolved_case,
            None,
            None,
            ("study_report_binding_mismatch",),
        )
    try:
        preflight = compute_budget_preflight(
            prepared=prepared, authorization=authorization, policy=budget_policy
        )
    except SemanticEvaluatorError as exc:
        return StudyPreflightResult(
            False, eligibility, resolved_case, authorization, None, (exc.reason_code,)
        )
    return StudyPreflightResult(
        preflight.decision == "allowed",
        eligibility,
        resolved_case,
        authorization,
        preflight,
        tuple(preflight.reason_codes),
    )


def _usage_from_archive(path: Path) -> tuple[str, int | None, int | None, int | None]:
    input_values: list[int] = []
    output_values: list[int] = []
    total_values: list[int] = []
    for member in sorted(path.glob("attempts/*/*/transport.json")):
        record = parse_study_json(
            member.read_bytes(),
            ProviderAttemptRecord,
            "study_execution_binding_mismatch",
        )
        if None in (record.input_tokens, record.output_tokens, record.total_tokens):
            return "not_reported", None, None, None
        input_values.append(record.input_tokens)
        output_values.append(record.output_tokens)
        total_values.append(record.total_tokens)
    if not input_values:
        return "not_reported", None, None, None
    return "reported", sum(input_values), sum(output_values), sum(total_values)


def _build_execution_evidence(
    *,
    authorization: LajProviderExecutionAuthorizationV1,
    budget_policy: LajProviderBudgetPolicyV1,
    preflight: LajBudgetPreflightV1,
    result: ShadowRunResult,
) -> LajStudyExecutionEvidenceV1:
    if not result.archive_complete or result.archive_path is None:
        raise SemanticEvaluatorError("study_execution_evidence_incomplete")
    archive = verify_shadow_archive(Path(result.archive_path))
    usage, input_tokens, output_tokens, total_tokens = _usage_from_archive(archive.path)
    schema_hashes = {
        model.schema_id: canonical_sha256(model.model_json_schema())
        for model in sorted(STUDY_CONTRACT_MODELS, key=lambda item: item.schema_id)
    }
    payload: dict[str, object] = {
        "schema_version": STUDY_EXECUTION_EVIDENCE_SCHEMA_ID,
        "study_id": authorization.study_id,
        "trial_id": authorization.trial_id,
        "budget_policy": budget_policy.model_dump(mode="json", warnings="error"),
        "authorization": authorization.model_dump(mode="json", warnings="error"),
        "preflight": preflight.model_dump(mode="json", warnings="error"),
        "authorization_sha256": authorization.authorization_sha256,
        "preflight_sha256": preflight.preflight_sha256,
        "shadow_request_sha256": archive.request.shadow_request_sha256,
        "receipt_sha256": archive.receipt.receipt_sha256,
        "archive_manifest_sha256": archive.archive_manifest.archive_manifest_sha256,
        "execution_sha256": archive.execution_manifest.execution_sha256,
        "report_sha256": archive.request.report_sha256,
        "provider_usage": usage,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "study_source_sha256": source_sha256_for_module(
            "multi_agent_brief.semantic_evaluator.study"
        ),
        "study_schema_sha256s": schema_hashes,
        "runner_source_sha256": archive.execution_manifest.runner_source_sha256,
    }
    return LajStudyExecutionEvidenceV1.model_validate(
        {**payload, "evidence_sha256": canonical_sha256(payload)}
    )


def _verify_execution_evidence(
    *,
    evidence: LajStudyExecutionEvidenceV1,
    archive: Any,
    authorization: LajProviderExecutionAuthorizationV1 | None = None,
    preflight: LajBudgetPreflightV1 | None = None,
    budget_policy: LajProviderBudgetPolicyV1 | None = None,
) -> LajStudyExecutionEvidenceV1:
    current_schema_hashes = {
        model.schema_id: canonical_sha256(model.model_json_schema())
        for model in sorted(STUDY_CONTRACT_MODELS, key=lambda item: item.schema_id)
    }
    auth = evidence.authorization
    frozen_preflight = evidence.preflight
    frozen_policy = evidence.budget_policy
    usage = _usage_from_archive(archive.path)
    expected = (
        authorization is None
        or canonical_json_bytes(authorization) == canonical_json_bytes(auth),
        preflight is None
        or canonical_json_bytes(preflight) == canonical_json_bytes(frozen_preflight),
        budget_policy is None
        or canonical_json_bytes(budget_policy) == canonical_json_bytes(frozen_policy),
        evidence.study_id == auth.study_id,
        evidence.trial_id == auth.trial_id == archive.request.trial_id,
        evidence.authorization_sha256 == auth.authorization_sha256,
        evidence.preflight_sha256 == frozen_preflight.preflight_sha256,
        auth.report_sha256 == archive.request.report_sha256,
        auth.bounded_context_sha256 == archive.request.bounded_context_sha256,
        auth.instrument_sha256 == archive.request.instrument_sha256,
        auth.assessment_plan_sha256 == archive.request.assessment_plan_sha256,
        auth.ordered_prompt_request_sha256s
        == archive.request.ordered_prompt_request_sha256s,
        frozen_preflight.input_binding_sha256 == archive.request.input_binding_sha256,
        frozen_preflight.instrument_sha256 == archive.request.instrument_sha256,
        frozen_preflight.assessment_plan_sha256
        == archive.request.assessment_plan_sha256,
        frozen_preflight.ordered_prompt_request_sha256s
        == archive.request.ordered_prompt_request_sha256s,
        frozen_preflight.decision == "allowed",
        not frozen_preflight.reason_codes,
        evidence.shadow_request_sha256 == archive.request.shadow_request_sha256,
        evidence.receipt_sha256 == archive.receipt.receipt_sha256,
        evidence.archive_manifest_sha256
        == archive.archive_manifest.archive_manifest_sha256,
        evidence.execution_sha256 == archive.execution_manifest.execution_sha256,
        evidence.report_sha256 == archive.request.report_sha256,
        evidence.study_source_sha256
        == source_sha256_for_module("multi_agent_brief.semantic_evaluator.study"),
        evidence.study_schema_sha256s == current_schema_hashes,
        evidence.runner_source_sha256
        == archive.execution_manifest.runner_source_sha256,
        (
            evidence.provider_usage,
            evidence.input_tokens,
            evidence.output_tokens,
            evidence.total_tokens,
        )
        == usage,
    )
    if not all(expected):
        raise SemanticEvaluatorError("study_execution_binding_mismatch")
    return evidence


def budgeted_shadow_run(
    *,
    authorization: LajProviderExecutionAuthorizationV1,
    budget_policy: LajProviderBudgetPolicyV1,
    report: str | Path,
    bounded_context: str | Path,
    instrument: str | Path,
    archive_root: str | Path,
    evidence_output: str | Path,
    adapter_factory: Any = None,
    clock: Any = None,
    sleep: Any = None,
) -> BudgetedShadowRunResult:
    try:
        evidence_path = validate_standalone_study_output(evidence_output)
        existing_execution_evidence = (
            parse_study_json(
                evidence_path.read_bytes(),
                LajStudyExecutionEvidenceV1,
                "study_execution_evidence_incomplete",
            )
            if evidence_path.exists()
            else None
        )
    except SemanticEvaluatorError as exc:
        return BudgetedShadowRunResult(None, None, None, (exc.reason_code,))
    prepared = prepare_shadow_run(
        report=report,
        bounded_context=bounded_context,
        profile=PROFILE_ID,
        instrument=instrument,
        trial_id=authorization.trial_id,
        archive_root=archive_root,
    )
    if isinstance(prepared, ShadowRunResult):
        return BudgetedShadowRunResult(
            None, None, None, _study_preparation_reasons(prepared)
        )
    try:
        preflight = compute_budget_preflight(
            prepared=prepared, authorization=authorization, policy=budget_policy
        )
    except SemanticEvaluatorError as exc:
        return BudgetedShadowRunResult(None, None, None, (exc.reason_code,))
    if preflight.decision == "blocked":
        return BudgetedShadowRunResult(
            preflight, None, None, tuple(preflight.reason_codes)
        )
    reserved: BinaryIO | None = None
    if existing_execution_evidence is None:
        try:
            _, reserved = reserve_study_output(evidence_path)
        except SemanticEvaluatorError as exc:
            return BudgetedShadowRunResult(preflight, None, None, (exc.reason_code,))
    kwargs: dict[str, Any] = {"adapter_factory": adapter_factory}
    if existing_execution_evidence is not None:
        kwargs["replay_only"] = True
    if clock is not None:
        kwargs["clock"] = clock
    if sleep is not None:
        kwargs["sleep"] = sleep
    result = execute_prepared_shadow_run(prepared, **kwargs)
    if not result.archive_complete:
        if reserved is not None:
            reserved.close()
        reason_codes = (
            ("study_execution_evidence_incomplete",)
            if existing_execution_evidence is not None
            else result.reason_codes
        )
        return BudgetedShadowRunResult(preflight, result, None, reason_codes)
    try:
        if result.archive_path is None:
            raise SemanticEvaluatorError("study_execution_evidence_incomplete")
        archive = verify_shadow_archive(Path(result.archive_path))
        if result.replayed:
            if existing_execution_evidence is None:
                raise SemanticEvaluatorError("study_execution_evidence_incomplete")
            evidence = _verify_execution_evidence(
                evidence=existing_execution_evidence,
                archive=archive,
                authorization=authorization,
                preflight=preflight,
                budget_policy=budget_policy,
            )
        else:
            evidence = _build_execution_evidence(
                authorization=authorization,
                budget_policy=budget_policy,
                preflight=preflight,
                result=result,
            )
            if reserved is None:
                raise SemanticEvaluatorError("study_execution_evidence_incomplete")
            _commit_reserved(
                reserved,
                canonical_json_bytes(
                    evidence.model_dump(mode="json", warnings="error")
                ),
            )
    except SemanticEvaluatorError as exc:
        if reserved is not None and not reserved.closed:
            reserved.close()
        return BudgetedShadowRunResult(preflight, result, None, (exc.reason_code,))
    except (ValidationError, OSError, TypeError, ValueError):
        if reserved is not None and not reserved.closed:
            reserved.close()
        return BudgetedShadowRunResult(
            preflight,
            result,
            None,
            ("study_execution_evidence_incomplete",),
        )
    return BudgetedShadowRunResult(preflight, result, evidence, ())


def compare_sensitivity(
    *,
    case: ResolvedSensitivityCaseV1,
    evidence: LajStudyExecutionEvidenceV1,
    archive_path: str | Path,
) -> LajSensitivityComparisonV1:
    try:
        archive = verify_shadow_archive(Path(archive_path))
        _verify_execution_evidence(evidence=evidence, archive=archive)
        if (
            case.study_id != evidence.study_id
            or case.mutated_report_sha256 != evidence.report_sha256
            or archive.request.report_sha256 != evidence.report_sha256
            or archive.receipt.receipt_sha256 != evidence.receipt_sha256
            or archive.archive_manifest.archive_manifest_sha256
            != evidence.archive_manifest_sha256
        ):
            raise ValueError
        rows: list[SensitivityComparisonRowV1] = []
        findings = archive.witness.run.findings if archive.ok else []
        for mutation in case.resolved_mutations:
            dimensions = {mutation.expected_primary_dimension}
            if mutation.expected_secondary_dimension is not None:
                dimensions.add(mutation.expected_secondary_dimension)
            links: list[SensitivityCandidateLinkV1] = []
            for finding in findings:
                if finding.dimension_id not in dimensions:
                    continue
                for span in finding.report_spans:
                    start = max(span.start_char, mutation.start_char)
                    end = min(span.end_char, mutation.end_char)
                    if (
                        span.report_sha256 == case.mutated_report_sha256
                        and span.block_id == mutation.block_id
                        and start < end
                    ):
                        links.append(
                            SensitivityCandidateLinkV1(
                                finding_id=finding.finding_id,
                                dimension_id=finding.dimension_id,
                                block_id=span.block_id,
                                overlap_start_char=start,
                                overlap_end_char=end,
                            )
                        )
            links.sort(
                key=lambda item: (
                    item.finding_id,
                    item.block_id,
                    item.overlap_start_char,
                )
            )
            rows.append(
                SensitivityComparisonRowV1(
                    mutation_id=mutation.mutation_id,
                    candidate_links=links,
                    human_adjudication="unreviewed",
                )
            )
        payload: dict[str, object] = {
            "schema_version": SENSITIVITY_COMPARISON_SCHEMA_ID,
            "study_id": case.study_id,
            "case_sha256": case.case_sha256,
            "execution_evidence_sha256": evidence.evidence_sha256,
            "archive_manifest_sha256": archive.archive_manifest.archive_manifest_sha256,
            "receipt_sha256": archive.receipt.receipt_sha256,
            "report_sha256": case.mutated_report_sha256,
            "state": "ready_for_human_adjudication" if archive.ok else "invalid",
            "rows": [row.model_dump(mode="json") for row in rows],
            "reason_codes": [] if archive.ok else ["sensitivity_comparison_invalid"],
        }
        return LajSensitivityComparisonV1.model_validate(
            {**payload, "comparison_sha256": canonical_sha256(payload)}
        )
    except (SemanticEvaluatorError, ValidationError, ValueError, TypeError, OSError):
        raise SemanticEvaluatorError("sensitivity_comparison_invalid") from None


_WORKSPACE_MARKERS = frozenset({"config.yaml", "sources.yaml", "user.md"})
_ARCHIVE_MARKERS = frozenset({"COMPLETE", "archive_manifest.json", "receipt.json"})


def validate_standalone_study_output(path: str | Path) -> Path:
    """Require a new JSON file in a real standalone laj-study-* directory."""

    try:
        candidate = Path(path).expanduser()
        if (
            not candidate.is_absolute()
            or candidate.suffix != ".json"
            or candidate.name in _WORKSPACE_MARKERS | _ARCHIVE_MARKERS
            or not candidate.parent.name.startswith("laj-study-")
            or candidate.parent.name == "laj-study-"
        ):
            raise ValueError
        parent_metadata = candidate.parent.lstat()
        if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(
            parent_metadata.st_mode
        ):
            raise ValueError
        if candidate.parent.resolve(strict=True) != candidate.parent:
            raise ValueError
        for ancestor in (candidate.parent, *candidate.parent.parents):
            metadata = ancestor.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError
            if all((ancestor / marker).is_file() for marker in _WORKSPACE_MARKERS):
                raise ValueError
            if any((ancestor / marker).is_file() for marker in _ARCHIVE_MARKERS):
                raise ValueError
        if candidate.exists():
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ValueError
    except (OSError, RuntimeError, ValueError, TypeError):
        raise SemanticEvaluatorError("provider_exclusion_invalid") from None
    return candidate


def reserve_study_output(path: str | Path) -> tuple[Path, BinaryIO]:
    candidate = validate_standalone_study_output(path)
    try:
        descriptor = os.open(
            candidate,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        return candidate, os.fdopen(descriptor, "wb")
    except OSError:
        raise SemanticEvaluatorError("provider_exclusion_invalid") from None


def _commit_reserved(handle: BinaryIO, payload: bytes) -> None:
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
    except OSError:
        try:
            handle.close()
        except OSError:
            pass
        raise SemanticEvaluatorError("study_execution_evidence_incomplete") from None


def write_canonical_model(path: Path, model: Any) -> None:
    _, handle = reserve_study_output(path)
    _commit_reserved(
        handle, canonical_json_bytes(model.model_dump(mode="json", warnings="error"))
    )


def write_canonical_payload(path: Path, payload: Mapping[str, Any]) -> None:
    _, handle = reserve_study_output(path)
    _commit_reserved(handle, canonical_json_bytes(dict(payload)))


__all__ = [
    "BudgetedShadowRunResult",
    "StudyEligibility",
    "StudyPreflightResult",
    "budgeted_shadow_run",
    "compare_sensitivity",
    "compute_budget_preflight",
    "evaluate_study_eligibility",
    "make_execution_authorization",
    "parse_study_json",
    "parse_sensitivity_manifest",
    "prepare_study",
    "resolve_sensitivity_case",
    "reserve_study_output",
    "validate_standalone_study_output",
    "verify_study_report_binding",
    "write_canonical_model",
    "write_canonical_payload",
]
