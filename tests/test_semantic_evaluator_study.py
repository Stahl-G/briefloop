"""Finite LAJ-EVAL-1 eligibility, budget, exclusion, and comparison rows."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest
from pydantic import ValidationError

from multi_agent_brief.semantic_evaluator.runner import (
    PROFILE_ID,
    PreparedShadowRun,
    ShadowRunResult,
    prepare_shadow_run,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_sha256,
    sha256_bytes,
)
from multi_agent_brief.semantic_evaluator.study import (
    budgeted_shadow_run,
    compare_sensitivity,
    compute_budget_preflight,
    evaluate_study_eligibility,
    make_execution_authorization,
    parse_sensitivity_manifest,
    parse_study_json,
    resolve_sensitivity_case,
)
from multi_agent_brief.semantic_evaluator.study_contracts import (
    PROVIDER_BUDGET_POLICY_SCHEMA_ID,
    SENSITIVITY_GROUND_TRUTH_SCHEMA_ID,
    STUDY_DECLARATION_SCHEMA_ID,
    LajProviderBudgetPolicyV1,
    LajProviderExecutionAuthorizationV1,
    LajStudyDeclarationV1,
)


FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"


def _hashed(model, payload: dict[str, object], field: str):
    return model.model_validate({**payload, field: canonical_sha256(payload)})


def _declaration(
    report_sha256: str,
    *,
    study_kind: str = "product_utility_check",
    artifact_class: str = "reader_facing_business_report",
    mutation_count: int = 0,
    self_diagnosing: bool = False,
) -> LajStudyDeclarationV1:
    payload: dict[str, object] = {
        "schema_version": STUDY_DECLARATION_SCHEMA_ID,
        "study_id": "study-test-v1",
        "study_kind": study_kind,
        "artifact_class": artifact_class,
        "report_sha256": report_sha256,
        "origin_label": "public test report",
        "public_safe": True,
        "synthetic": False,
        "self_diagnosing": self_diagnosing,
        "reader_facing": study_kind == "product_utility_check",
        "expected_mutation_count": mutation_count,
    }
    return _hashed(LajStudyDeclarationV1, payload, "declaration_sha256")


def _policy(*, calls: int = 9, tokens: int = 1_000_000):
    payload = {
        "schema_version": PROVIDER_BUDGET_POLICY_SCHEMA_ID,
        "max_provider_calls": calls,
        "max_input_tokens": tokens,
    }
    return _hashed(LajProviderBudgetPolicyV1, payload, "policy_sha256")


def _invocation(tmp_path: Path) -> dict[str, object]:
    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True)
    for name in ("report.md", "bounded_context.json", "instrument.json"):
        shutil.copyfile(FIXTURES / name, inputs / name)
    return {
        "report": inputs / "report.md",
        "bounded_context": inputs / "bounded_context.json",
        "profile": PROFILE_ID,
        "instrument": inputs / "instrument.json",
        "trial_id": "trial-study-v1",
        "archive_root": (tmp_path / "archives").resolve(),
    }


def _prepared(tmp_path: Path) -> PreparedShadowRun:
    prepared = prepare_shadow_run(**_invocation(tmp_path))
    assert isinstance(prepared, PreparedShadowRun)
    return prepared


def test_product_utility_target_is_strict_and_sensitivity_is_calibration_only() -> None:
    eligible = evaluate_study_eligibility(_declaration("0" * 64))
    assert eligible.eligible is True
    assert eligible.evidence_class == "product_utility_candidate"

    postmortem = _declaration(
        "0" * 64,
        artifact_class="technical_postmortem",
        self_diagnosing=True,
    )
    assert evaluate_study_eligibility(postmortem).reason_codes == (
        "utility_target_ineligible",
    )

    sensitivity = _declaration(
        "0" * 64,
        study_kind="sensitivity_calibration",
        artifact_class="technical_postmortem",
        mutation_count=1,
        self_diagnosing=True,
    )
    result = evaluate_study_eligibility(sensitivity)
    assert result.eligible is True
    assert result.evidence_class == "calibration_only"


def test_study_contract_rejects_coercion_extra_and_self_hash_tamper() -> None:
    valid = _declaration("0" * 64)
    payload = valid.model_dump(mode="json")
    with pytest.raises(ValidationError):
        LajStudyDeclarationV1.model_validate({**payload, "public_safe": 1})
    with pytest.raises(ValidationError):
        LajStudyDeclarationV1.model_validate({**payload, "extra": True})
    with pytest.raises(ValidationError):
        LajStudyDeclarationV1.model_validate({**payload, "origin_label": "changed"})
    duplicate = (
        '{"schema_version":"briefloop.semantic_evaluator.study_declaration.v1",'
        '"study_id":"a","study_id":"b"}'
    ).encode()
    with pytest.raises(Exception) as error:
        parse_study_json(duplicate, LajStudyDeclarationV1, "study_declaration_invalid")
    assert getattr(error.value, "reason_code") == "study_declaration_invalid"


def test_exact_sensitivity_span_resolution_and_reordered_inventory_rejection() -> None:
    control = "# 报告\n\n结论仍需人工确认。\n".encode()
    inserted = "模型已经证明事实正确。"
    mutated = f"# 报告\n\n结论仍需人工确认。{inserted}\n".encode()
    declaration = _declaration(
        sha256_bytes(mutated),
        study_kind="sensitivity_calibration",
        artifact_class="technical_postmortem",
        mutation_count=1,
        self_diagnosing=True,
    )
    mutation = {
        "mutation_id": "M1",
        "label": "summary_body_alignment",
        "location": "summary",
        "before_text": "结论仍需人工确认。",
        "after_text": f"结论仍需人工确认。{inserted}",
        "inserted_text": inserted,
        "expected_primary_dimension": "summary_body_alignment",
        "expected_secondary_dimension": None,
        "expected_severity": "major",
        "in_scope_basis": "direct internal contradiction",
    }
    payload: dict[str, object] = {
        "schema_version": SENSITIVITY_GROUND_TRUTH_SCHEMA_ID,
        "classification": "private_provider_excluded_calibration_evidence",
        "control_report": {
            "path": "/private/control.md",
            "sha256": sha256_bytes(control),
        },
        "mutated_report": {
            "path": "/private/mutated.md",
            "sha256": sha256_bytes(mutated),
        },
        "mutation_count": 1,
        "mutations": [mutation],
        "provider_exclusion": {
            "manifest_path": "/private/ground-truth.json",
            "manifest_is_outside_admission_workspace_root": True,
            "admission_workspace_root": "/private/inputs",
            "archive_root": "/private/archive",
            "manifest_path_is_not_a_cli_argument": True,
            "manifest_bytes_are_not_an_admission_input": True,
            "manifest_bytes_are_not_in_prompt_or_provider_request": True,
            "proof_method": "exact prompt inventory scan",
        },
        "evaluation_rule": {
            "human_is_detection_authority": True,
            "provider_does_not_receive_this_manifest": True,
            "no_finding_is_neutral": True,
            "sensitivity_pass": "at_least_3_of_4",
            "sensitivity_partial": "exactly_2_of_4",
            "sensitivity_fail": "0_or_1_of_4_or_invalid_archive_or_false_quality_pass_claim",
        },
    }
    manifest = parse_sensitivity_manifest(json.dumps(payload).encode())
    case = resolve_sensitivity_case(
        declaration=declaration,
        manifest=manifest,
        control_report_bytes=control,
        mutated_report_bytes=mutated,
    )
    assert len(case.resolved_mutations) == 1
    assert case.resolved_mutations[0].mutation_id == "M1"
    assert case.resolved_mutations[0].end_char - case.resolved_mutations[
        0
    ].start_char == len(inserted)

    invalid = dict(payload)
    invalid["mutations"] = [{**mutation, "mutation_id": "M2"}]
    with pytest.raises(Exception) as error:
        parse_sensitivity_manifest(json.dumps(invalid).encode())
    assert getattr(error.value, "reason_code") == "sensitivity_manifest_invalid"


class _FixedCountSizer:
    sizer_id = "local_proxy_utf8_bytes_conservative_v1"
    sizer_version = "local_proxy_utf8_bytes_conservative_v1"
    package_name = "briefloop"
    package_version = "test"
    encoding_name = "utf8-bytes-upper-bound-v1"

    def count_tokens(self, **_kwargs) -> int:
        return 72_586


class _MalformedCountSizer(_FixedCountSizer):
    def count_tokens(self, **_kwargs):
        return True


def test_frozen_budget_case_blocks_653274_before_archive_or_adapter(
    tmp_path: Path,
) -> None:
    prepared = replace(_prepared(tmp_path), prompt_sizer=_FixedCountSizer())
    policy = _policy(tokens=250_000)
    authorization = make_execution_authorization(
        study_id="study-frozen-case", prepared=prepared, policy=policy
    )
    preflight = compute_budget_preflight(
        prepared=prepared, authorization=authorization, policy=policy
    )
    assert preflight.planned_provider_calls == 9
    assert preflight.planned_input_token_upper_bound == 653_274
    assert preflight.decision == "blocked"
    assert preflight.reason_codes == ["budget_input_token_limit_exceeded"]

    malformed = replace(prepared, prompt_sizer=_MalformedCountSizer())
    malformed_authorization = make_execution_authorization(
        study_id="study-malformed-sizer", prepared=malformed, policy=policy
    )
    with pytest.raises(Exception) as error:
        compute_budget_preflight(
            prepared=malformed,
            authorization=malformed_authorization,
            policy=policy,
        )
    assert getattr(error.value, "reason_code") == "budget_preflight_unavailable"

    calls = 0

    def factory(_execution):
        nonlocal calls
        calls += 1
        raise AssertionError

    invocation = _invocation(tmp_path / "second")
    normal_prepared = prepare_shadow_run(**invocation)
    assert isinstance(normal_prepared, PreparedShadowRun)
    normal_auth = make_execution_authorization(
        study_id="study-blocked", prepared=normal_prepared, policy=_policy(tokens=1)
    )
    result = budgeted_shadow_run(
        authorization=normal_auth,
        budget_policy=_policy(tokens=1),
        report=invocation["report"],
        bounded_context=invocation["bounded_context"],
        instrument=invocation["instrument"],
        archive_root=invocation["archive_root"],
        adapter_factory=factory,
    )
    assert result.reason_codes == ("budget_input_token_limit_exceeded",)
    assert calls == 0
    assert not Path(invocation["archive_root"]).exists()


def test_execution_authorization_has_no_ground_truth_surface(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    authorization = make_execution_authorization(
        study_id="study-exclusion", prepared=prepared, policy=_policy()
    )
    payload = authorization.model_dump(mode="json")
    forbidden = {
        "ground_truth",
        "manifest_path",
        "manifest_sha256",
        "mutations",
        "expected_primary_dimension",
        "expected_severity",
    }
    assert forbidden.isdisjoint(payload)
    with pytest.raises(ValidationError):
        LajProviderExecutionAuthorizationV1.model_validate(
            {**payload, "ground_truth": "not-provider-visible"}
        )


def test_allowed_synthetic_budget_creates_evidence_and_zero_finding_is_neutral(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    prepared = prepare_shadow_run(**invocation)
    assert isinstance(prepared, PreparedShadowRun)
    policy = _policy(tokens=10_000_000)
    authorization = make_execution_authorization(
        study_id="study-compare", prepared=prepared, policy=policy
    )
    result = budgeted_shadow_run(
        authorization=authorization,
        budget_policy=policy,
        report=invocation["report"],
        bounded_context=invocation["bounded_context"],
        instrument=invocation["instrument"],
        archive_root=invocation["archive_root"],
        clock=lambda: "2026-07-19T00:00:00Z",
        sleep=lambda _seconds: None,
    )
    assert result.shadow_result is not None
    assert result.execution_evidence is not None
    assert result.execution_evidence.provider_usage in {"reported", "not_reported"}
    assert "/private" not in json.dumps(result.to_dict())
    replay_without_evidence = budgeted_shadow_run(
        authorization=authorization,
        budget_policy=policy,
        report=invocation["report"],
        bounded_context=invocation["bounded_context"],
        instrument=invocation["instrument"],
        archive_root=invocation["archive_root"],
    )
    assert replay_without_evidence.reason_codes == (
        "study_execution_evidence_incomplete",
    )
    replay = budgeted_shadow_run(
        authorization=authorization,
        budget_policy=policy,
        report=invocation["report"],
        bounded_context=invocation["bounded_context"],
        instrument=invocation["instrument"],
        archive_root=invocation["archive_root"],
        existing_execution_evidence=result.execution_evidence,
    )
    assert replay.ok is True
    assert replay.shadow_result is not None and replay.shadow_result.replayed is True
    # The packaged synthetic result may emit findings; an unrelated mutation must
    # still remain an unreviewed candidate row, never an automatic verdict.
    report_sha = authorization.report_sha256
    case_payload: dict[str, object] = {
        "schema_version": "briefloop.semantic_evaluator.resolved_sensitivity_case.v1",
        "study_id": "study-compare",
        "study_declaration_sha256": "1" * 64,
        "source_manifest_sha256": "2" * 64,
        "control_report_sha256": report_sha,
        "mutated_report_sha256": report_sha,
        "normalized_text_sha256": prepared.admission.reader.artifact.normalized_text_sha256,
        "resolved_mutation_count": 1,
        "resolved_mutations": [
            {
                "mutation_id": "M1",
                "mutated_report_sha256": report_sha,
                "normalized_text_sha256": prepared.admission.reader.artifact.normalized_text_sha256,
                "block_id": prepared.admission.reader.artifact.blocks[0].block_id,
                "start_char": 0,
                "end_char": 1,
                "excerpt_sha256": sha256_bytes("#".encode()),
                "expected_primary_dimension": "recommendation_constraint_consistency",
                "expected_secondary_dimension": None,
                "expected_severity": "major",
            }
        ],
    }
    from multi_agent_brief.semantic_evaluator.study_contracts import (
        ResolvedSensitivityCaseV1,
    )

    case = _hashed(ResolvedSensitivityCaseV1, case_payload, "case_sha256")
    comparison = compare_sensitivity(
        case=case,
        evidence=result.execution_evidence,
        archive_path=result.shadow_result.archive_path,
    )
    assert comparison.state == "ready_for_human_adjudication"
    assert comparison.rows[0].human_adjudication == "unreviewed"
    assert "PASS" not in json.dumps(comparison.model_dump(mode="json"))
