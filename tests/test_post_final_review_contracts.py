from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from multi_agent_brief.product.review_session.contracts import (
    IMPROVEMENT_PROJECTION_SCHEMA_ID,
    POST_FINAL_REVIEW_CONTEXT_SCHEMA_ID,
    POST_FINAL_REVIEW_POLICY_SCHEMA_ID,
    POST_FINAL_REVIEW_READ_MODEL_SCHEMA_ID,
    QUALITY_PROJECTION_SCHEMA_ID,
    SEMANTIC_REVIEW_SCHEMA_ID,
    ImprovementProjection,
    PostFinalReviewContext,
    PostFinalReviewPolicyBinding,
    PostFinalReviewReadModel,
    QualityProjection,
    SemanticReviewProjection,
)
from multi_agent_brief.semantic_evaluator.post_final_bridge import (
    build_post_final_semantic_review,
    empty_semantic_review,
)
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    LAJ_READER_SCHEMA_ID,
    LajReaderBinding,
    LajReaderView,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256


def context_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": POST_FINAL_REVIEW_CONTEXT_SCHEMA_ID,
        "workspace_id": "workspace-1",
        "run_id": "run-1",
        "store_revision": 7,
        "finalization_id": "finalization-1",
        "finalization_receipt_id": "receipt-final-1",
        "package_id": "package-1",
        "package_receipt_id": "receipt-package-1",
        "report_artifact_id": "artifact-report-1",
        "report_artifact_revision": 3,
        "report_sha256": "1" * 64,
        "review_policy_fingerprint": "2" * 64,
        "qp_projection_fingerprint": "3" * 64,
    }
    payload["context_fingerprint"] = canonical_sha256(payload)
    return payload


def quality_payload() -> dict[str, object]:
    return {
        "schema_version": QUALITY_PROJECTION_SCHEMA_ID,
        "authority_label": "deterministic_projection",
        "runtime_effect": "none",
        "overall_status": "warning",
        "source_fingerprint": "3" * 64,
        "metrics": [
            {"metric_id": "gate-blockers", "label": "Gate blockers", "value": 0, "status": "pass"},
            {"metric_id": "gate-warnings", "label": "Gate warnings", "value": 1, "status": "warning"},
        ],
        "sections": [
            {
                "section_id": "control-integrity",
                "title": "Control integrity",
                "items": [
                    {"item_id": "run-integrity", "label": "Run integrity", "status": "pass", "detail": "Verified by the upstream deterministic projector."}
                ],
            }
        ],
        "projection_fingerprint": "3" * 64,
    }


def build_read_model() -> PostFinalReviewReadModel:
    payload: dict[str, object] = {
        "schema_version": POST_FINAL_REVIEW_READ_MODEL_SCHEMA_ID,
        "generated_at": "2026-07-19T00:00:00Z",
        "context": context_payload(),
        "quality": quality_payload(),
        "semantic_review": empty_semantic_review(
            status="not_requested", reason_code="review_policy_disabled"
        ).model_dump(mode="json"),
        "improvement": ImprovementProjection(
            schema_version=IMPROVEMENT_PROJECTION_SCHEMA_ID,
            available=False,
            authority_effect="none",
            reason_code="pf_review_2_not_shipped",
        ).model_dump(mode="json"),
    }
    payload["read_model_fingerprint"] = canonical_sha256(payload)
    return PostFinalReviewReadModel.model_validate(payload)


def reader_view(*, report_sha256: str = "1" * 64) -> LajReaderView:
    binding = LajReaderBinding(
        artifact_id="artifact-report-1",
        report_sha256=report_sha256,
        trial_id="trial-1",
        shadow_receipt_id="shadow-receipt-1",
        instrument_sha256="4" * 64,
        execution_sha256="5" * 64,
        execution_origin="synthetic_fixture",
        model_id="model-1",
        model_version="model-version-1",
        archive_manifest_sha256="6" * 64,
        presentation_sha256="7" * 64,
    )
    payload: dict[str, object] = {
        "schema_version": LAJ_READER_SCHEMA_ID,
        "status": "available",
        "boundary": LAJ_READER_BOUNDARY,
        "advisory_only": True,
        "shadow_only": True,
        "runtime_authority": False,
        "authority_effect": "none",
        "archive_verified": True,
        "binding": binding.model_dump(mode="json"),
        "run_status": "completed",
        "validation_status": "accepted",
        "reason_codes": [],
        "assessed_unit_count": 25,
        "finding_count": 0,
        "withheld_finding_count": 0,
        "abstention_count": 0,
        "findings": [],
        "disclaimer": "Advisory only; no finding is neutral and never PASS.",
    }
    payload["view_sha256"] = canonical_sha256(payload)
    return LajReaderView.model_validate(payload)


def test_strict_context_rejects_extra_coercion_and_fingerprint_drift() -> None:
    payload = context_payload()
    assert PostFinalReviewContext.model_validate(payload).store_revision == 7
    for field, value in (("store_revision", True), ("report_artifact_revision", "3")):
        malformed = deepcopy(payload)
        malformed[field] = value
        with pytest.raises(ValidationError):
            PostFinalReviewContext.model_validate(malformed)
    extra = deepcopy(payload)
    extra["workspace_path"] = "/private/path"
    with pytest.raises(ValidationError):
        PostFinalReviewContext.model_validate(extra)
    drift = deepcopy(payload)
    drift["run_id"] = "run-2"
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        PostFinalReviewContext.model_validate(drift)


def test_policy_is_strict_immutable_input_without_activation_side_effect() -> None:
    payload: dict[str, object] = {
        "schema_version": POST_FINAL_REVIEW_POLICY_SCHEMA_ID,
        "workspace_id": "workspace-1",
        "run_id": "run-1",
        "revision": 1,
        "enabled": False,
        "trigger": "manual",
        "auto_open": False,
        "profile_id": "research_design_report_zh_v1",
        "provider_id": "local-proxy",
        "model_id": "model-1",
        "model_version": "model-version-1",
        "data_policy": "public_safe_only",
        "max_provider_calls": 0,
        "max_input_tokens": 0,
        "max_output_tokens": 0,
        "max_wall_seconds": 0,
        "instrument_sha256": "4" * 64,
        "created_by": "human-1",
        "created_at": "2026-07-19T00:00:00Z",
        "accepted_transaction_id": "transaction-1",
    }
    payload["policy_fingerprint"] = canonical_sha256(payload)
    policy = PostFinalReviewPolicyBinding.model_validate(payload)
    assert policy.enabled is False
    malformed = deepcopy(payload)
    malformed["enabled"] = 0
    with pytest.raises(ValidationError):
        PostFinalReviewPolicyBinding.model_validate(malformed)


def test_read_model_requires_exact_upstream_quality_binding() -> None:
    model = build_read_model()
    assert model.quality.authority_label == "deterministic_projection"
    payload = model.model_dump(mode="json")
    payload["quality"]["source_fingerprint"] = "9" * 64
    payload["quality"]["projection_fingerprint"] = "9" * 64
    payload["read_model_fingerprint"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "read_model_fingerprint"}
    )
    with pytest.raises(ValidationError, match="quality projection binding mismatch"):
        PostFinalReviewReadModel.model_validate(payload)


def test_laj_bridge_is_pure_bound_and_never_upgrades_no_finding_to_pass() -> None:
    context = PostFinalReviewContext.model_validate(context_payload())
    projection = build_post_final_semantic_review(
        context=context,
        reader_view=reader_view(),
    )
    assert projection.status == "available"
    assert projection.findings == []
    assert projection.authority_effect == "none"
    assert projection.binding is not None
    assert projection.binding.report_sha256 == context.report_sha256
    assert "pass" not in projection.model_dump_json().lower()

    stale = build_post_final_semantic_review(
        context=context,
        reader_view=reader_view(report_sha256="8" * 64),
    )
    assert stale.status == "stale"
    assert stale.binding is None
    assert stale.findings == []


def test_non_available_semantic_state_cannot_carry_findings_or_unknown_fields() -> None:
    payload = empty_semantic_review(
        status="budget_blocked", reason_code="budget_input_token_limit_exceeded"
    ).model_dump(mode="json")
    payload["unexpected"] = "authority"
    with pytest.raises(ValidationError):
        SemanticReviewProjection.model_validate(payload)
