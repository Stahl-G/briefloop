"""Read-only Store-qualified LAJ projection for the canonical HTML renderer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multi_agent_brief.contracts.v2 import PostFinalAssessmentResultRecord
from multi_agent_brief.control_store.errors import ControlStoreError
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.product.post_final_assessment import (
    PostFinalAssessmentError,
    post_final_assessment_archive_root,
    resolve_current_post_final_assessment_result,
    resolve_post_final_assessment_series,
)
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.projections import (
    build_finalized_local_review_projection,
)
from multi_agent_brief.semantic_evaluator.archive import (
    trial_archive_path,
    verify_shadow_archive,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    LAJ_READER_SCHEMA_ID,
    LajReaderView,
    build_empty_laj_reader_view,
    build_laj_reader_view,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256


@dataclass(frozen=True)
class PostFinalAssessmentProjection:
    """One non-authoritative, fail-closed semantic page input."""

    lifecycle_present: bool
    status: str
    reason_code: str | None
    view: LajReaderView


def _empty(
    *, lifecycle_present: bool, status: str, reason_code: str
) -> PostFinalAssessmentProjection:
    return PostFinalAssessmentProjection(
        lifecycle_present=lifecycle_present,
        status=status,
        reason_code=reason_code,
        view=build_empty_laj_reader_view(
            status="not_available", reason_code=reason_code
        ),
    )


def _terminal_class(view: LajReaderView) -> str:
    if view.status == "available":
        return "available"
    if view.status == "abstained":
        return "abstained"
    reasons = set(view.reason_codes)
    if any("incomplete" in item or "truncat" in item for item in reasons):
        return "incomplete"
    if any("refus" in item for item in reasons):
        return "refused"
    return "provider_failed" if reasons else "unavailable"


def _recorded_zero_advice_view(
    result: PostFinalAssessmentResultRecord,
) -> LajReaderView:
    """Project exact Store terminal truth without claiming archive verification."""

    payload: dict[str, object] = {
        "schema_version": LAJ_READER_SCHEMA_ID,
        "status": "unavailable",
        "boundary": LAJ_READER_BOUNDARY,
        "advisory_only": True,
        "shadow_only": True,
        "runtime_authority": False,
        "authority_effect": "none",
        "archive_verified": False,
        "binding": None,
        "run_status": None,
        "validation_status": None,
        "reason_codes": list(result.reason_codes),
        "assessed_unit_count": result.assessed_unit_count,
        "finding_count": 0,
        "withheld_finding_count": 0,
        "abstention_count": result.abstention_count,
        "findings": [],
        "disclaimer": (
            "Experimental advisory terminal status is recorded without actionable "
            "findings. The current archive was not semantically reverified. No "
            "workflow, Gate, finalization, delivery, repair, approval, or next-action "
            "effect."
        ),
    }
    return LajReaderView.model_validate(
        {**payload, "view_sha256": canonical_sha256(payload)},
        strict=True,
    )


def build_post_final_assessment_projection(
    workspace: str | Path,
    *,
    assessment_result_id: str | None = None,
    assessment_result_fingerprint: str | None = None,
) -> PostFinalAssessmentProjection:
    """Return one explicitly selected Store-qualified result, or zero advice."""

    root = Path(workspace).expanduser().resolve()
    try:
        facts = build_finalized_local_review_projection(root).facts
        with SQLiteControlStore.open(root / "briefloop.db") as store:
            history = store.load_history()
            verified = CoreRunDomainVerifier().verify_loaded_history(
                history,
                facts.run_id,
            )
            action = classify_core_run_next_action(verified)
            snapshot = history.snapshot_at_revision(
                facts.run_id, history.store_revision
            )
            if facts.store_revision != snapshot.store_revision:
                raise PostFinalAssessmentError("control_store_integrity_invalid")
            series = resolve_post_final_assessment_series(
                history,
                snapshot,
                facts,
                action,
            )
    except RuntimeHostError as exc:
        # A run that has not reached finalized_local has no PF-LAJ lifecycle at
        # all.  Keep the existing explicit ``quality html --laj-view``
        # presentation-only surface available in that state; it is never a
        # Store-qualified assessment and cannot override one once present.
        if str(exc) == "run_not_finalized_local":
            return _empty(
                lifecycle_present=False,
                status="not_requested",
                reason_code="laj_not_run",
            )
        return _empty(
            lifecycle_present=True,
            status="unavailable",
            reason_code="post_final_assessment_unavailable",
        )
    except (
        ControlStoreError,
        CoreRunError,
        PostFinalAssessmentError,
        OSError,
        ValueError,
    ):
        return _empty(
            lifecycle_present=True,
            status="unavailable",
            reason_code="post_final_assessment_unavailable",
        )
    policies = list(snapshot.post_final_assessment_policy_revisions)
    if not series and not policies:
        return _empty(
            lifecycle_present=False,
            status="not_requested",
            reason_code="laj_not_run",
        )
    if not series:
        return _empty(
            lifecycle_present=True,
            status="not_requested",
            reason_code="post_final_assessment_not_requested",
        )
    run_results = [
        item
        for item in snapshot.post_final_assessment_results
        if item.run_id == facts.run_id
        and item.finalized_lineage_fingerprint
        == series[0].finalized_lineage_fingerprint
    ]
    if assessment_result_id is None:
        if assessment_result_fingerprint is not None:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_selection_invalid",
            )
        if len(run_results) > 1:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_selection_required",
            )
        result = run_results[0] if run_results else None
    else:
        matches = [
            item
            for item in run_results
            if item.assessment_result_id == assessment_result_id
        ]
        if len(matches) != 1:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_selection_invalid",
            )
        result = matches[0]
        if (
            assessment_result_fingerprint is None
            or result.result_fingerprint != assessment_result_fingerprint
        ):
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_selection_invalid",
            )
    request = (
        next(
            (
                item
                for item in series
                if result is not None
                and item.assessment_request_id == result.assessment_request_id
            ),
            None,
        )
        if result is not None
        else series[0]
    )
    if request is None:
        return _empty(
            lifecycle_present=True,
            status="invalid",
            reason_code="post_final_assessment_selection_invalid",
        )
    policy_matches = [
        item
        for item in snapshot.post_final_assessment_policy_revisions
        if item.policy_revision_id == request.policy_revision_id
        and item.policy_fingerprint == request.policy_fingerprint
    ]
    policy = policy_matches[0] if len(policy_matches) == 1 else None
    if (
        policy is None
        or policy.policy_revision_id != request.policy_revision_id
        or request.report_artifact_id != facts.report.artifact_id
        or request.report_revision != facts.report.artifact_revision
        or request.report_sha256 != facts.report.sha256
        or request.finalization_id != facts.finalization_id
        or request.finalization_receipt_id != facts.finalization_receipt_id
        or request.finalize_gate_batch_id != facts.finalize_gate_batch_id
        or request.policy_fingerprint != policy.policy_fingerprint
    ):
        return _empty(
            lifecycle_present=True,
            status="invalid",
            reason_code="control_store_integrity_invalid",
        )
    if result is not None:
        try:
            resolved = resolve_current_post_final_assessment_result(snapshot, request)
        except PostFinalAssessmentError as exc:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code=str(exc),
            )
        if resolved != result:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_selection_invalid",
            )
    if result is None:
        return _empty(
            lifecycle_present=True,
            status="pending",
            reason_code="post_final_assessment_pending",
        )
    if result.finding_count == 0 and result.withheld_finding_count == 0:
        return PostFinalAssessmentProjection(
            lifecycle_present=True,
            status=result.terminal_evidence_class,
            reason_code=result.reason_codes[0] if result.reason_codes else None,
            view=_recorded_zero_advice_view(result),
        )
    try:
        archive = verify_shadow_archive(
            trial_archive_path(
                post_final_assessment_archive_root(root), request.trial_id
            )
        )
        view = build_laj_reader_view(
            archive.path,
            expected_report_sha256=facts.report.sha256,
        )
    except (SemanticEvaluatorError, OSError, ValueError):
        return _empty(
            lifecycle_present=True,
            status="invalid",
            reason_code="post_final_assessment_archive_invalid",
        )
    if (
        result.finalized_facts_fingerprint != request.finalized_facts_fingerprint
        or result.finalized_lineage_fingerprint != request.finalized_lineage_fingerprint
        or view.binding is None
        or view.binding.trial_id != request.trial_id
        or archive.request.shadow_request_sha256 != result.shadow_request_sha256
        or archive.execution_manifest.execution_sha256
        != result.execution_manifest_sha256
        or archive.archive_manifest.archive_manifest_sha256
        != result.archive_manifest_sha256
        or archive.receipt.receipt_id != result.archive_receipt_id
        or archive.presentation.composition_sha256 != result.composition_sha256
        or archive.presentation.presentation_sha256 != result.presentation_sha256
        or view.view_sha256 != result.reader_view_sha256
        or _terminal_class(view) != result.terminal_evidence_class
        or view.finding_count != result.finding_count
        or view.withheld_finding_count != result.withheld_finding_count
        or view.abstention_count != result.abstention_count
    ):
        return _empty(
            lifecycle_present=True,
            status="invalid",
            reason_code="post_final_assessment_binding_invalid",
        )
    return PostFinalAssessmentProjection(
        lifecycle_present=True,
        status=view.status,
        reason_code=None,
        view=view,
    )


__all__ = [
    "PostFinalAssessmentProjection",
    "build_post_final_assessment_projection",
]
