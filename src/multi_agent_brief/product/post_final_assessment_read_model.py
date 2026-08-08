"""Pure Store read model helpers for the post-final Reader Review lifecycle.

This module is deliberately independent of the assessment writer/service and
of the Semantic Evaluator runner.  It verifies immutable Store bindings and
resolves the append-only assessment series; it performs no Store writes,
archive I/O, provider work, or Human effects.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    CoreRunNextAction,
    PostFinalAssessmentPolicyRevision,
    PostFinalAssessmentRequestRecord,
    PostFinalAssessmentResultRecord,
)
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes


# The evaluator admission boundary rejects an archive root beneath a declared
# workspace.  This sibling location is derived from the resolved workspace
# identity only; it is not a caller-selected authority or Store field.
_ARCHIVE_DIRECTORY = ".briefloop-post-final-laj"
_FINALIZED_LINEAGE_SCHEMA = "briefloop.post_final_assessment_finalized_lineage.v2"
_FINALIZED_REPORT_MEDIA_TYPE = "text/markdown"
_POST_FINAL_ASSESSMENT_RECEIPT_TYPES = frozenset(
    {
        "post_final_assessment_policy",
        "post_final_assessment_claim",
        "post_final_assessment_series_claim",
        "post_final_assessment_result",
        "post_final_finding_disposition",
        "post_final_human_observation",
        "post_final_guidance_draft",
        "post_final_guidance_status",
    }
)


class PostFinalAssessmentError(RuntimeError):
    """Stable, value-free product failure."""


def _canonical_sha256(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _record_fingerprint(payload: Mapping[str, object], field: str) -> str:
    canonical = dict(payload)
    canonical.pop(field, None)
    return _canonical_sha256(canonical)


def post_final_assessment_archive_root(workspace: str | Path) -> Path:
    """Derive the sole local evidence root without making it product authority.

    The frozen evaluator admission boundary rejects a root inside the declared
    BriefLoop workspace. This package-owned sibling location is therefore
    derived only from the resolved workspace placement; callers cannot select
    it and the resulting local path is never recorded in Store evidence.
    """

    resolved_workspace = Path(workspace).expanduser().resolve()
    workspace_identity = _canonical_sha256(
        {
            "schema": "briefloop.post_final_assessment_archive_root.v1",
            "workspace_path": str(resolved_workspace),
        }
    )
    return resolved_workspace.parent / _ARCHIVE_DIRECTORY / workspace_identity


def _require_current_finalized_action(
    facts: Any,
    action: CoreRunNextAction,
) -> CoreRunNextAction:
    """Require the current verified Core action to be exact finalized-local."""

    if (
        facts.terminal_state != "finalized_local"
        or action.run_id != facts.run_id
        or action.store_revision != facts.store_revision
        or action.action_kind != "complete"
        or action.effect_kind != "finalized_local"
        or action.reason_code != "local_finalization_complete"
        or action.stage_id is not None
        or action.role_id is not None
        or action.source_route_id is not None
        or action.source_provider_id is not None
        or action.request_schema_id is not None
        or action.action_fingerprint != facts.terminal_action_fingerprint
    ):
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    return action


def finalized_lineage_fingerprint(
    facts: Any,
    action: CoreRunNextAction,
) -> str:
    """Return the PF-LAJ request-slot identity for immutable finalized facts.

    ``FinalizedLocalReviewFacts.facts_fingerprint`` deliberately includes the
    current Store revision.  This PF-LAJ-owned digest binds only the immutable
    finalized-local lineage and never replaces the strict facts fingerprint.
    """

    _require_current_finalized_action(facts, action)
    try:
        gate_bindings = [
            item.model_dump(mode="json", exclude_unset=False)
            for item in facts.gate_bindings
        ]
        if gate_bindings != sorted(
            gate_bindings,
            key=lambda item: (item["gate_id"], item["evaluation_id"]),
        ):
            raise ValueError("gate bindings are not canonical")
        payload = {
            "schema_version": _FINALIZED_LINEAGE_SCHEMA,
            "workspace_id": facts.workspace_id,
            "run_id": facts.run_id,
            "terminal_state": "finalized_local",
            "terminal_action_kind": "complete",
            "terminal_effect_kind": "finalized_local",
            "terminal_reason_code": "local_finalization_complete",
            "finalization_id": facts.finalization_id,
            "finalization_receipt_id": facts.finalization_receipt_id,
            "finalize_gate_batch_id": facts.finalize_gate_batch_id,
            "gate_bindings": gate_bindings,
            "report": {
                "artifact_id": facts.report.artifact_id,
                "artifact_revision": facts.report.artifact_revision,
                "sha256": facts.report.sha256,
                "media_type": _FINALIZED_REPORT_MEDIA_TYPE,
                "size_bytes": facts.report.size_bytes,
            },
        }
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PostFinalAssessmentError("control_store_integrity_invalid") from exc
    return _canonical_sha256(payload)


def reassessed_facts_fingerprint(
    facts: Any,
    action: CoreRunNextAction,
    *,
    claim_prior_revision: int,
) -> str:
    """Reconstruct the exact facts digest assessed immediately before claim."""

    if (
        type(claim_prior_revision) is not int
        or claim_prior_revision < 1
        or claim_prior_revision > facts.store_revision
    ):
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    _require_current_finalized_action(facts, action)
    try:
        action_payload = action.model_dump(mode="json", exclude_unset=False)
        action_payload["store_revision"] = claim_prior_revision
        action_payload.pop("action_fingerprint", None)
        action_payload["action_fingerprint"] = canonical_fingerprint(action_payload)
        assessed_action = CoreRunNextAction.model_validate(action_payload, strict=True)
        payload = facts.model_dump(mode="json", exclude={"facts_fingerprint"})
        payload["store_revision"] = claim_prior_revision
        payload["terminal_action_fingerprint"] = assessed_action.action_fingerprint
        return type(facts).fingerprint_for(payload)
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise PostFinalAssessmentError("control_store_integrity_invalid") from exc


def _request_has_exact_finalized_bindings(
    request: PostFinalAssessmentRequestRecord,
    facts: Any,
    action: CoreRunNextAction,
) -> bool:
    return (
        request.run_id == facts.run_id
        and request.report_artifact_id == facts.report.artifact_id
        and request.report_revision == facts.report.artifact_revision
        and request.report_sha256 == facts.report.sha256
        and request.finalization_id == facts.finalization_id
        and request.finalization_receipt_id == facts.finalization_receipt_id
        and request.finalize_gate_batch_id == facts.finalize_gate_batch_id
        and request.finalized_lineage_fingerprint
        == finalized_lineage_fingerprint(facts, action)
    )


def _require_advisory_only_receipt_suffix(
    snapshot: Any,
    facts: Any,
    claim_receipt: Any,
) -> None:
    """Reject any post-claim run receipt outside the PF-LAJ advisory family."""

    suffix = sorted(
        (
            item
            for item in snapshot.transactions
            if item.run_id == facts.run_id
            and item.committed_revision > claim_receipt.prior_revision
        ),
        key=lambda item: item.committed_revision,
    )
    if not suffix or suffix[0].transaction_id != claim_receipt.transaction_id:
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    if any(
        item.transaction_type not in _POST_FINAL_ASSESSMENT_RECEIPT_TYPES
        for item in suffix
    ):
        raise PostFinalAssessmentError("control_store_integrity_invalid")


def resolve_post_final_assessment_series(
    history: Any,
    snapshot: Any,
    facts: Any,
    action: CoreRunNextAction,
) -> tuple[PostFinalAssessmentRequestRecord, ...]:
    """Resolve and reverify the complete request series for one stable lineage."""

    lineage = finalized_lineage_fingerprint(facts, action)
    requests = list(snapshot.post_final_assessment_requests)
    matches = [
        item for item in requests if item.finalized_lineage_fingerprint == lineage
    ]
    if not matches:
        if requests:
            raise PostFinalAssessmentError("post_final_assessment_stale")
        return ()
    matches.sort(key=lambda item: item.assessment_generation)
    if [item.assessment_generation for item in matches] != list(
        range(1, len(matches) + 1)
    ):
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    results_by_request = {
        item.assessment_request_id: item
        for item in snapshot.post_final_assessment_results
        if item.finalized_lineage_fingerprint == lineage
    }
    abandonments_by_request = {
        item.assessment_request_id: item
        for item in snapshot.post_final_assessment_abandonments
        if item.finalized_lineage_fingerprint == lineage
    }
    if set(results_by_request) & set(abandonments_by_request):
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    for index, request in enumerate(matches):
        if not _request_has_exact_finalized_bindings(request, facts, action):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        receipts = [
            item
            for item in snapshot.transactions
            if item.transaction_id == request.accepted_transaction_id
        ]
        if len(receipts) != 1:
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        receipt = receipts[0]
        references = [
            item
            for item in receipt.post_final_assessment_requests
            if item.assessment_request_id == request.assessment_request_id
        ]
        if (
            receipt.run_id != request.run_id
            or receipt.transaction_type
            not in {
                "post_final_assessment_claim",
                "post_final_assessment_series_claim",
            }
            or receipt.prior_revision + 1 != receipt.committed_revision
            or len(receipt.post_final_assessment_requests) != 1
            or len(references) != 1
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        _require_advisory_only_receipt_suffix(snapshot, facts, receipt)
        if (
            reassessed_facts_fingerprint(
                facts,
                action,
                claim_prior_revision=receipt.prior_revision,
            )
            != request.finalized_facts_fingerprint
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        try:
            claim_snapshot = history.snapshot_at_revision(
                request.run_id,
                receipt.committed_revision,
            )
        except Exception as exc:
            raise PostFinalAssessmentError("control_store_integrity_invalid") from exc
        if not any(
            item.assessment_request_id == request.assessment_request_id
            and item.request_fingerprint == request.request_fingerprint
            for item in claim_snapshot.post_final_assessment_requests
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        if index == 0:
            if request.predecessor_assessment_request_id is not None:
                raise PostFinalAssessmentError("control_store_integrity_invalid")
            continue
        predecessor = matches[index - 1]
        result = results_by_request.get(predecessor.assessment_request_id)
        abandonment = abandonments_by_request.get(predecessor.assessment_request_id)
        if (
            request.predecessor_assessment_request_id
            != predecessor.assessment_request_id
            or request.predecessor_assessment_request_fingerprint
            != predecessor.request_fingerprint
            or (result is None) == (abandonment is None)
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        if result is not None and (
            request.predecessor_assessment_result_id != result.assessment_result_id
            or request.predecessor_result_fingerprint != result.result_fingerprint
            or request.predecessor_abandonment_id is not None
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        if abandonment is not None and (
            request.predecessor_abandonment_id != abandonment.abandonment_id
            or request.predecessor_abandonment_fingerprint
            != abandonment.abandonment_fingerprint
            or request.predecessor_assessment_result_id is not None
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
    return tuple(matches)


def resolve_current_post_final_assessment_request(
    history: Any,
    snapshot: Any,
    facts: Any,
    action: CoreRunNextAction,
) -> PostFinalAssessmentRequestRecord | None:
    """Resolve generation one only; automatic observation never advances a series."""

    series = resolve_post_final_assessment_series(history, snapshot, facts, action)
    return None if not series else series[0]


def resolve_post_final_assessment_request_by_id(
    history: Any,
    snapshot: Any,
    facts: Any,
    action: CoreRunNextAction,
    assessment_request_id: str,
) -> PostFinalAssessmentRequestRecord:
    """Resolve one explicit request without any implicit head/latest selection."""

    matches = [
        item
        for item in resolve_post_final_assessment_series(
            history, snapshot, facts, action
        )
        if item.assessment_request_id == assessment_request_id
    ]
    if len(matches) != 1:
        raise PostFinalAssessmentError("assessment_request_not_found")
    return matches[0]


def resolve_current_post_final_assessment_result(
    snapshot: Any,
    request: PostFinalAssessmentRequestRecord,
) -> PostFinalAssessmentResultRecord | None:
    """Resolve one exact Store-qualified result without touching its archive."""

    matches = [
        item
        for item in snapshot.post_final_assessment_results
        if item.assessment_request_id == request.assessment_request_id
    ]
    if len(matches) > 1:
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    if any(
        item.assessment_request_id == request.assessment_request_id
        for item in snapshot.post_final_assessment_abandonments
    ):
        if matches:
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        return None
    if not matches:
        return None
    result = matches[0]
    result_payload = result.model_dump(mode="json", warnings="error")
    if result.schema_version == PostFinalAssessmentResultRecord.schema_id:
        for field in (
            "assessment_kind",
            "report_type",
            "language",
            "profile_id",
            "model_version",
            "expected_model_identity",
            "parser_version",
            "projection_version",
            "reader_review_status",
            "reader_view_payload",
        ):
            result_payload.pop(field, None)
    if (
        result.policy_revision_id != request.policy_revision_id
        or result.finalized_facts_fingerprint != request.finalized_facts_fingerprint
        or result.finalized_lineage_fingerprint != request.finalized_lineage_fingerprint
        or result.result_fingerprint
        != _record_fingerprint(result_payload, "result_fingerprint")
    ):
        raise PostFinalAssessmentError("post_final_assessment_binding_invalid")
    return result


def _resolve_current_post_final_assessment_policy(
    snapshot: Any,
    run_id: str,
) -> PostFinalAssessmentPolicyRevision | None:
    """Return the one current policy by receipt order, never wall-clock ties."""

    policies = [
        item
        for item in snapshot.post_final_assessment_policy_revisions
        if item.run_id == run_id
    ]
    if not policies:
        return None
    receipts = {item.transaction_id: item for item in snapshot.transactions}
    ordered: list[tuple[int, PostFinalAssessmentPolicyRevision]] = []
    try:
        for policy in policies:
            receipt = receipts.get(policy.accepted_transaction_id)
            if (
                receipt is None
                or receipt.run_id != run_id
                or receipt.transaction_type != "post_final_assessment_policy"
                or receipt.prior_revision + 1 != receipt.committed_revision
            ):
                raise ValueError("policy receipt is invalid")
            ordered.append((receipt.committed_revision, policy))
        ordered.sort(key=lambda item: item[0])
        if len({revision for revision, _policy in ordered}) != len(ordered):
            raise ValueError("policy receipts are not unique")
        previous_policy_id: str | None = None
        for _revision, policy in ordered:
            if policy.previous_policy_revision_id != previous_policy_id:
                raise ValueError("policy chain is not append-only")
            previous_policy_id = policy.policy_revision_id
    except (AttributeError, TypeError, ValueError) as exc:
        raise PostFinalAssessmentError("control_store_integrity_invalid") from exc
    return ordered[-1][1]


def resolve_current_post_final_assessment_policy(
    snapshot: Any,
    facts: Any,
) -> PostFinalAssessmentPolicyRevision | None:
    """Return the current policy for the finalized-facts run."""

    try:
        run_id = facts.run_id
    except AttributeError as exc:
        raise PostFinalAssessmentError("control_store_integrity_invalid") from exc
    return _resolve_current_post_final_assessment_policy(snapshot, run_id)


__all__ = [
    "PostFinalAssessmentError",
    "finalized_lineage_fingerprint",
    "post_final_assessment_archive_root",
    "reassessed_facts_fingerprint",
    "resolve_current_post_final_assessment_policy",
    "resolve_current_post_final_assessment_request",
    "resolve_current_post_final_assessment_result",
    "resolve_post_final_assessment_request_by_id",
    "resolve_post_final_assessment_series",
]
