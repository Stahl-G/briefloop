"""Pure bridge from verified LAJ reader evidence to post-final advisory state.

No evaluator, adapter, archive writer, Store, or runtime service is imported.
The caller must provide a strict reader view that was produced by the existing
archive verifier.
"""

from __future__ import annotations

from typing import Literal

from multi_agent_brief.product.review_session.contracts import (
    POST_FINAL_REVIEW_CONTEXT_SCHEMA_ID,
    SEMANTIC_REVIEW_SCHEMA_ID,
    PostFinalReviewContext,
    SemanticReviewBinding,
    SemanticReviewProjection,
    SemanticReviewStatus,
)
from multi_agent_brief.semantic_evaluator.reader import LajReaderView
from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256


_READER_STATUS_MAP: dict[str, SemanticReviewStatus] = {
    "available": "available",
    "abstained": "abstained",
    "invalid": "invalid",
    "not_available": "unavailable",
    "stale": "stale",
    "unavailable": "unavailable",
}


def empty_semantic_review(
    *,
    status: Literal[
        "not_requested",
        "pending",
        "unsupported",
        "budget_blocked",
        "unavailable",
        "invalid",
        "stale",
    ],
    reason_code: str,
) -> SemanticReviewProjection:
    """Create a zero-advice state before or instead of advisory execution."""

    return SemanticReviewProjection(
        schema_version=SEMANTIC_REVIEW_SCHEMA_ID,
        status=status,
        advisory_only=True,
        authority_effect="none",
        binding=None,
        reason_codes=[reason_code],
        findings=[],
        abstention_count=0,
        assessed_unit_count=0,
    )


def build_post_final_semantic_review(
    *,
    context: PostFinalReviewContext,
    reader_view: LajReaderView,
) -> SemanticReviewProjection:
    """Bind one verified/failed reader view to the exact post-final context."""

    status = _READER_STATUS_MAP[reader_view.status]
    reader_binding = reader_view.binding
    if reader_binding is None:
        return SemanticReviewProjection(
            schema_version=SEMANTIC_REVIEW_SCHEMA_ID,
            status=status,
            advisory_only=True,
            authority_effect="none",
            binding=None,
            reason_codes=reader_view.reason_codes,
            findings=[],
            abstention_count=reader_view.abstention_count,
            assessed_unit_count=reader_view.assessed_unit_count,
        )

    if reader_binding.report_sha256 != context.report_sha256:
        return empty_semantic_review(
            status="stale",
            reason_code="report_binding_stale",
        )

    assessment_key = canonical_sha256(
        {
            "schema_version": POST_FINAL_REVIEW_CONTEXT_SCHEMA_ID,
            "workspace_id": context.workspace_id,
            "run_id": context.run_id,
            "finalization_id": context.finalization_id,
            "report_sha256": context.report_sha256,
            "instrument_sha256": reader_binding.instrument_sha256,
            "review_policy_fingerprint": context.review_policy_fingerprint,
        }
    )
    binding = SemanticReviewBinding(
        report_sha256=reader_binding.report_sha256,
        assessment_key=assessment_key,
        instrument_sha256=reader_binding.instrument_sha256,
        archive_manifest_sha256=reader_binding.archive_manifest_sha256,
        receipt_id=reader_binding.shadow_receipt_id,
        model_id=reader_binding.model_id,
        model_version=reader_binding.model_version,
    )
    return SemanticReviewProjection(
        schema_version=SEMANTIC_REVIEW_SCHEMA_ID,
        status=status,
        advisory_only=True,
        authority_effect="none",
        binding=binding,
        reason_codes=reader_view.reason_codes,
        findings=reader_view.findings if status == "available" else [],
        abstention_count=reader_view.abstention_count,
        assessed_unit_count=reader_view.assessed_unit_count,
    )


__all__ = ["build_post_final_semantic_review", "empty_semantic_review"]
