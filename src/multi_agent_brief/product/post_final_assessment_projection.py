"""Read-only Store-qualified LAJ projection for the canonical HTML renderer."""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
from pathlib import Path
from typing import Any, Literal, Mapping

from multi_agent_brief.contracts.v2 import (
    PostFinalAssessmentResultRecord,
    post_final_guidance_legal_actions,
)
from multi_agent_brief.control_store.errors import ControlStoreError
from multi_agent_brief.control_store.serialization import canonical_json_bytes
from multi_agent_brief.control_store.sqlite_store import (
    ControlStoreHistory,
    SQLiteControlStore,
)
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.product.post_final_assessment_read_model import (
    PostFinalAssessmentError,
    finalized_lineage_fingerprint,
    post_final_assessment_archive_root,
    resolve_current_post_final_assessment_result,
    resolve_post_final_assessment_series,
)
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.projections import (
    build_finalized_local_review_projection_from_history,
)
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    LAJ_READER_SCHEMA_ID,
    LajReaderView,
    build_empty_laj_reader_view,
    build_laj_reader_view,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256


NEXT_RUN_CONSUMPTION = "explicit_opt_in_successor_only"


class _DataclassMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        if key not in {item.name for item in fields(self)}:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        return (item.name for item in fields(self))

    def __len__(self) -> int:
        return len(fields(self))


@dataclass(frozen=True)
class ReaderReviewCompatibleResultOption(_DataclassMapping):
    assessment_result_id: str
    assessment_result_fingerprint: str
    assessment_generation: int
    requested_model_id: str
    model_version: str
    terminal_evidence_class: str
    assessed_unit_count: int
    finding_count: int
    withheld_finding_count: int
    abstention_count: int
    recorded_at: str


@dataclass(frozen=True)
class ReaderReviewRequirementLabel(_DataclassMapping):
    requirement_id: str
    requirement_type: Literal[
        "must_answer",
        "must_include",
        "must_not_claim",
        "audience_need",
        "decision_use",
        "scope_included",
        "scope_excluded",
    ]
    text: str
    source_locator: str


@dataclass(frozen=True)
class ReaderReviewRequestTemplate(_DataclassMapping):
    schema_version: Literal["briefloop.reader_review_assessment_input.v1"]
    assessment_kind: Literal["reader_review"]
    report_type: Literal["management_monthly"]
    language: Literal["en"]
    profile_id: Literal["management_brief_en_v1"]
    protocol: Literal["anthropic_messages_compatible"]
    endpoint_class: Literal["explicit_messages_api"]
    egress_scope: Literal["public_safe_report"]
    report_scope: Literal["final_reader_markdown"]
    context_scope: Literal["frozen_run_direction_requirements"]
    disclosure_confirmed: Literal[True]
    public_safe_egress_attested: Literal[True]
    cost_status: Literal["not_measured"]
    provider_call_ceiling: Literal[2]
    total_input_token_ceiling: Literal[400000]
    total_output_token_ceiling: Literal[8192]
    output_tokens_per_call: Literal[4096]
    automatic_retry: Literal[False]
    advisory_only: Literal[True]
    authority_effect: Literal["none"]


_REQUEST_TEMPLATE = ReaderReviewRequestTemplate(
    schema_version="briefloop.reader_review_assessment_input.v1",
    assessment_kind="reader_review",
    report_type="management_monthly",
    language="en",
    profile_id="management_brief_en_v1",
    protocol="anthropic_messages_compatible",
    endpoint_class="explicit_messages_api",
    egress_scope="public_safe_report",
    report_scope="final_reader_markdown",
    context_scope="frozen_run_direction_requirements",
    disclosure_confirmed=True,
    public_safe_egress_attested=True,
    cost_status="not_measured",
    provider_call_ceiling=2,
    total_input_token_ceiling=400000,
    total_output_token_ceiling=8192,
    output_tokens_per_call=4096,
    automatic_retry=False,
    advisory_only=True,
    authority_effect="none",
)


@dataclass(frozen=True)
class PostFinalAssessmentProjection:
    """One non-authoritative, fail-closed semantic page input."""

    lifecycle_present: bool
    status: str
    reason_code: str | None
    view: LajReaderView
    user_status: Literal[
        "finding_returned",
        "no_finding_returned_in_completed_supported_checks",
        "partially_assessed",
        "unable_to_assess",
        "not_assessed",
        "selection_required",
    ]
    compatible_result_options: tuple[ReaderReviewCompatibleResultOption, ...]
    requirement_labels: tuple[ReaderReviewRequirementLabel, ...]
    selected_result_id: str | None
    selected_result_fingerprint: str | None
    review_status: Mapping[str, object] | None
    request_template: ReaderReviewRequestTemplate | None
    next_run_consumption: Literal["explicit_opt_in_successor_only"]
    run_action_available: bool
    selection_required: bool


def _empty(
    *,
    lifecycle_present: bool,
    status: str,
    reason_code: str,
    user_status: Literal[
        "partially_assessed",
        "unable_to_assess",
        "not_assessed",
        "selection_required",
    ] = "not_assessed",
    compatible_result_options: tuple[ReaderReviewCompatibleResultOption, ...] = (),
    requirement_labels: tuple[ReaderReviewRequirementLabel, ...] = (),
    request_template: ReaderReviewRequestTemplate | None = None,
    run_action_available: bool = False,
    review_status: Mapping[str, object] | None = None,
) -> PostFinalAssessmentProjection:
    return PostFinalAssessmentProjection(
        lifecycle_present=lifecycle_present,
        status=status,
        reason_code=reason_code,
        view=build_empty_laj_reader_view(
            status="not_available", reason_code=reason_code
        ),
        user_status=user_status,
        compatible_result_options=compatible_result_options,
        requirement_labels=requirement_labels,
        selected_result_id=None,
        selected_result_fingerprint=None,
        review_status=review_status,
        request_template=request_template,
        next_run_consumption=NEXT_RUN_CONSUMPTION,
        run_action_available=run_action_available,
        selection_required=user_status == "selection_required",
    )


def _build_human_observation_status(
    *,
    snapshot: Any,
    run_id: str,
    finalized_lineage: str,
    assessment_result_id: str | None = None,
    assessment_result_fingerprint: str | None = None,
    reader_view_sha256: str | None = None,
) -> Mapping[str, object]:
    """Project append-only Human observations for any finalized report state.

    Unlike model finding dispositions, a Human observation is legal before a
    Reader Review result exists and after a terminal failure.  The rows remain
    strictly lineage-bound and are only a read projection; no finding or
    evaluator input is synthesized here.
    """

    receipts = {
        item.transaction_id: item for item in getattr(snapshot, "transactions", ())
    }

    def receipt_revision(item: Any) -> int:
        receipt = receipts.get(getattr(item, "accepted_transaction_id", None))
        return receipt.committed_revision if receipt is not None else 0

    records = [
        item
        for item in getattr(snapshot, "post_final_human_observations", ())
        if item.run_id == run_id
        and item.finalized_lineage_fingerprint == finalized_lineage
    ]
    records.sort(key=lambda item: (item.observation_revision, receipt_revision(item)))
    by_observation_id = {item.observation_id: item for item in records}
    superseded = {
        item.previous_observation_id
        for item in records
        if item.previous_observation_id is not None
    }
    observations: list[dict[str, object]] = []
    for item in records:
        payload = item.model_dump(mode="json", exclude_unset=False)
        payload["origin"] = "human"
        payload["status"] = (
            "superseded" if item.observation_id in superseded else "recorded"
        )
        observations.append(payload)

    def current_status(guidance_id: str) -> Any | None:
        rows = [
            item
            for item in getattr(snapshot, "post_final_guidance_statuses", ())
            if item.run_id == run_id
            and item.finalized_lineage_fingerprint == finalized_lineage
            and item.guidance_id == guidance_id
        ]
        rows.sort(key=receipt_revision)
        return rows[-1] if rows else None

    human_drafts = sorted(
        (
            item
            for item in getattr(snapshot, "post_final_guidance_drafts", ())
            if item.run_id == run_id
            and item.finalized_lineage_fingerprint == finalized_lineage
            and item.provenance_kind == "human_observation"
        ),
        key=lambda item: (
            item.guidance_id,
            item.draft_revision,
            receipt_revision(item),
        ),
    )
    latest_drafts = {
        item.guidance_id: max(
            candidate.draft_revision
            for candidate in human_drafts
            if candidate.guidance_id == item.guidance_id
        )
        for item in human_drafts
    }
    guidance_drafts: list[dict[str, object]] = []
    for item in human_drafts:
        observation = by_observation_id.get(item.observation_id)
        approval_eligible = (
            observation is not None
            and observation.observation_fingerprint == item.observation_fingerprint
            and observation.observation_id not in superseded
        )
        legal_statuses = post_final_guidance_legal_actions(
            current_status(item.guidance_id),
            target_draft_revision=item.draft_revision,
            approval_eligible=approval_eligible,
        )
        payload = item.model_dump(mode="json", exclude_unset=False)
        payload["legal_actions"] = (
            [
                {
                    "approved": "approve",
                    "deactivated": "deactivate",
                    "reverted": "revert",
                    "superseded": "supersede",
                }[status]
                for status in legal_statuses
            ]
            if item.draft_revision == latest_drafts[item.guidance_id]
            else []
        )
        guidance_drafts.append(payload)
    guidance_ids = {item.guidance_id for item in human_drafts}
    guidance_statuses = [
        item.model_dump(mode="json", exclude_unset=False)
        for item in getattr(snapshot, "post_final_guidance_statuses", ())
        if item.run_id == run_id
        and item.finalized_lineage_fingerprint == finalized_lineage
        and item.guidance_id in guidance_ids
    ]
    return {
        "ok": True,
        "run_id": run_id,
        "finalized_lineage_fingerprint": finalized_lineage,
        "assessment_result_id": assessment_result_id,
        "assessment_result_fingerprint": assessment_result_fingerprint,
        "reader_view_sha256": reader_view_sha256,
        "dispositions": [],
        "guidance_drafts": guidance_drafts,
        "guidance_statuses": guidance_statuses,
        "human_observations": observations,
        "next_run_consumption": NEXT_RUN_CONSUMPTION,
        "provider_calls": 0,
    }


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


def _reader_review_request_is_compatible(request: Any) -> bool:
    return (
        request.schema_version == "briefloop.post_final_assessment_request_record.v4"
        and request.assessment_kind == "reader_review"
        and request.report_type == "management_monthly"
        and request.language == "en"
        and request.profile_id == "management_brief_en_v1"
        and request.parser_version == "strict_dimension_json_v3"
        and request.projection_version == "reader_review_projection_v1"
        and request.disclosure_confirmed is True
        and request.public_safe_egress_attested is True
        and request.cost_status == "not_measured"
    )


def _reader_review_result_is_compatible(
    result: PostFinalAssessmentResultRecord,
    request: Any,
) -> bool:
    return (
        result.schema_version == PostFinalAssessmentResultRecord.reader_review_schema_id
        and _reader_review_request_is_compatible(request)
        and result.assessment_request_id == request.assessment_request_id
        and result.policy_revision_id == request.policy_revision_id
        and result.finalized_facts_fingerprint == request.finalized_facts_fingerprint
        and result.finalized_lineage_fingerprint
        == request.finalized_lineage_fingerprint
        and result.assessment_kind == request.assessment_kind
        and result.report_type == request.report_type
        and result.language == request.language
        and result.profile_id == request.profile_id
        and result.model_version == request.model_version
        and result.expected_model_identity == request.expected_model_identity
        and result.parser_version == request.parser_version
        and result.projection_version == request.projection_version
        and result.reader_review_status is not None
        and result.reader_view_payload is not None
    )


def _reader_review_result_matches_archive(
    result: PostFinalAssessmentResultRecord,
    request: Any,
    archive: Any,
    view: LajReaderView,
) -> bool:
    """Require a Reader Review result to bind the exact local archive."""

    return (
        result.finalized_facts_fingerprint == request.finalized_facts_fingerprint
        and result.finalized_lineage_fingerprint
        == request.finalized_lineage_fingerprint
        and archive.request.trial_id == request.trial_id
        and archive.archive_manifest.trial_id == request.trial_id
        and archive.receipt.trial_id == request.trial_id
        and view.archive_verified
        and view.binding is not None
        and view.binding.trial_id == request.trial_id
        and archive.request.shadow_request_sha256 == result.shadow_request_sha256
        and archive.execution_manifest.execution_sha256
        == result.execution_manifest_sha256
        and archive.archive_manifest.archive_manifest_sha256
        == result.archive_manifest_sha256
        and archive.receipt.receipt_id == result.archive_receipt_id
        and archive.presentation.composition_sha256 == result.composition_sha256
        and archive.presentation.presentation_sha256 == result.presentation_sha256
        and view.view_sha256 == result.reader_view_sha256
        and _terminal_class(view) == result.terminal_evidence_class
        and view.reason_codes == result.reason_codes
        and view.assessed_unit_count == result.assessed_unit_count
        and view.finding_count == result.finding_count
        and view.withheld_finding_count == result.withheld_finding_count
        and view.abstention_count == result.abstention_count
    )


def _compatible_option(
    result: PostFinalAssessmentResultRecord,
    request: Any,
) -> ReaderReviewCompatibleResultOption:
    return ReaderReviewCompatibleResultOption(
        assessment_result_id=result.assessment_result_id,
        assessment_result_fingerprint=result.result_fingerprint,
        assessment_generation=request.assessment_generation,
        requested_model_id=request.requested_model_id,
        model_version=request.model_version,
        terminal_evidence_class=result.terminal_evidence_class,
        assessed_unit_count=result.assessed_unit_count,
        finding_count=result.finding_count,
        withheld_finding_count=result.withheld_finding_count,
        abstention_count=result.abstention_count,
        recorded_at=result.recorded_at,
    )


def _requirement_labels(policy: Any) -> tuple[ReaderReviewRequirementLabel, ...]:
    try:
        requirements = policy.bounded_context["requirements"]
        if not isinstance(requirements, list):
            raise TypeError("bounded context requirements invalid")
        labels = tuple(
            ReaderReviewRequirementLabel(
                requirement_id=str(item["requirement_id"]),
                requirement_type=item["type"],
                text=str(item["text"]),
                source_locator=str(item["source_locator"]),
            )
            for item in requirements
            if isinstance(item, dict)
        )
        if len(labels) != len(requirements) or len(
            {item.requirement_id for item in labels}
        ) != len(labels):
            raise ValueError("bounded context requirement inventory invalid")
        return labels
    except (KeyError, TypeError, ValueError):
        raise PostFinalAssessmentError(
            "post_final_assessment_binding_invalid"
        ) from None


def _finding_fingerprint(
    *,
    result: PostFinalAssessmentResultRecord,
    view: LajReaderView,
    finding: Any,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": "briefloop.post_final_finding_binding.v1",
                "assessment_result_id": result.assessment_result_id,
                "assessment_result_fingerprint": result.result_fingerprint,
                "reader_view_sha256": view.view_sha256,
                "finding": finding.model_dump(mode="json", exclude_unset=False),
            }
        )
    ).hexdigest()


def _latest_by_receipt(
    rows: list[Any],
    *,
    receipts: Mapping[str, Any],
) -> Any | None:
    if not rows:
        return None
    rows.sort(
        key=lambda item: receipts[item.accepted_transaction_id].committed_revision
    )
    return rows[-1]


def _build_review_status(
    *,
    snapshot: Any,
    result: PostFinalAssessmentResultRecord,
    view: LajReaderView,
) -> Mapping[str, object] | None:
    human_status = _build_human_observation_status(
        snapshot=snapshot,
        run_id=result.run_id,
        finalized_lineage=result.finalized_lineage_fingerprint,
        assessment_result_id=result.assessment_result_id,
        assessment_result_fingerprint=result.result_fingerprint,
        reader_view_sha256=view.view_sha256,
    )
    if view.status != "available" or view.binding is None:
        return human_status
    receipts = {item.transaction_id: item for item in snapshot.transactions}

    def current_disposition(finding_id: str) -> Any | None:
        return _latest_by_receipt(
            [
                item
                for item in snapshot.post_final_finding_dispositions
                if item.assessment_result_id == result.assessment_result_id
                and item.finding_id == finding_id
            ],
            receipts=receipts,
        )

    def current_status(guidance_id: str) -> Any | None:
        return _latest_by_receipt(
            [
                item
                for item in snapshot.post_final_guidance_statuses
                if item.guidance_id == guidance_id
            ],
            receipts=receipts,
        )

    dispositions = []
    for finding in view.findings:
        current = current_disposition(finding.finding_id)
        dispositions.append(
            {
                "finding_id": finding.finding_id,
                "finding_fingerprint": _finding_fingerprint(
                    result=result,
                    view=view,
                    finding=finding,
                ),
                "current": (
                    current.model_dump(mode="json", exclude_unset=False)
                    if current is not None
                    else None
                ),
            }
        )
    draft_rows = sorted(
        (
            item
            for item in snapshot.post_final_guidance_drafts
            if item.provenance_kind == "accepted_model_finding"
            and item.assessment_result_id == result.assessment_result_id
        ),
        key=lambda item: (item.guidance_id, item.draft_revision),
    )
    latest_drafts = {
        item.guidance_id: max(
            candidate.draft_revision
            for candidate in draft_rows
            if candidate.guidance_id == item.guidance_id
        )
        for item in draft_rows
    }
    drafts = []
    for item in draft_rows:
        payload = item.model_dump(mode="json", exclude_unset=False)
        status = current_status(item.guidance_id)
        disposition = current_disposition(item.finding_id)
        legal_statuses = post_final_guidance_legal_actions(
            status,
            target_draft_revision=item.draft_revision,
            approval_eligible=(
                disposition is not None
                and disposition.disposition_id == item.disposition_id
                and disposition.decision == "accept"
            ),
        )
        payload["legal_actions"] = (
            [
                {
                    "approved": "approve",
                    "deactivated": "deactivate",
                    "reverted": "revert",
                    "superseded": "supersede",
                }[status_value]
                for status_value in legal_statuses
            ]
            if item.draft_revision == latest_drafts[item.guidance_id]
            else []
        )
        drafts.append(payload)
    guidance_ids = {item.guidance_id for item in draft_rows}
    statuses = [
        item.model_dump(mode="json", exclude_unset=False)
        for item in snapshot.post_final_guidance_statuses
        if item.guidance_id in guidance_ids
    ]
    drafts.extend(human_status["guidance_drafts"])
    statuses.extend(human_status["guidance_statuses"])
    return {
        "ok": True,
        "run_id": result.run_id,
        "finalized_lineage_fingerprint": result.finalized_lineage_fingerprint,
        "assessment_result_id": result.assessment_result_id,
        "assessment_result_fingerprint": result.result_fingerprint,
        "reader_view_sha256": view.view_sha256,
        "dispositions": dispositions,
        "guidance_drafts": drafts,
        "guidance_statuses": statuses,
        "human_observations": human_status["human_observations"],
        "next_run_consumption": NEXT_RUN_CONSUMPTION,
        "provider_calls": 0,
    }


def _load_verified_archive_view(
    root: Path,
    request: Any,
    expected_report_sha256: str,
) -> tuple[Any, LajReaderView] | None:
    """Load archive evidence only when a projection needs the legacy fallback."""

    from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError

    try:
        from multi_agent_brief.semantic_evaluator.archive import (
            trial_archive_path,
            verify_shadow_archive,
        )

        archive = verify_shadow_archive(
            trial_archive_path(
                post_final_assessment_archive_root(root), request.trial_id
            )
        )
        view = build_laj_reader_view(
            archive.path,
            expected_report_sha256=expected_report_sha256,
        )
    except (ImportError, SemanticEvaluatorError, OSError, ValueError):
        return None
    return archive, view


def build_post_final_assessment_projection(
    workspace: str | Path,
    *,
    assessment_result_id: str | None = None,
    assessment_result_fingerprint: str | None = None,
    loaded_history: ControlStoreHistory | None = None,
    allow_historical: bool = False,
) -> PostFinalAssessmentProjection:
    """Return one explicitly selected Store-qualified result, or zero advice."""

    root = Path(workspace).expanduser().resolve()
    selected_result: PostFinalAssessmentResultRecord | None = None
    try:
        if loaded_history is None:
            with SQLiteControlStore.open(root / "briefloop.db") as store:
                history = store.load_history()
        else:
            history = loaded_history
        if assessment_result_id is None and assessment_result_fingerprint is not None:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_selection_invalid",
            )
        if assessment_result_id is not None and assessment_result_fingerprint is None:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_selection_invalid",
            )
        heads = {
            item.workspace_run_head.current_run_id
            for item in history.snapshots
            if item.workspace_run_head is not None
        }
        if len(heads) != 1:
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        # The default selection is always current-head-bound.  The explicit
        # historical product surface may read one exact predecessor result,
        # but it still chooses the run only from the uniquely bound result row
        # (and never from a caller-provided run identifier).
        if allow_historical and assessment_result_id is not None:
            historical_matches = [
                item
                for run_snapshot in history.snapshots
                for item in run_snapshot.post_final_assessment_results
                if item.assessment_result_id == assessment_result_id
                and item.result_fingerprint == assessment_result_fingerprint
            ]
            if len(historical_matches) != 1:
                return _empty(
                    lifecycle_present=True,
                    status="invalid",
                    reason_code="reader_review_selection_incompatible",
                )
            selected_run_id = historical_matches[0].run_id
            require_current_head = False
        else:
            selected_run_id = next(iter(heads))
            require_current_head = True
        facts = build_finalized_local_review_projection_from_history(
            root,
            history,
            run_id=selected_run_id,
            require_current_head=require_current_head,
        ).facts
        verified = CoreRunDomainVerifier().verify_loaded_history(
            history,
            facts.run_id,
            require_current_head=require_current_head,
        )
        action = classify_core_run_next_action(verified)
        snapshot = history.snapshot_at_revision(facts.run_id, history.store_revision)
        if facts.store_revision != snapshot.store_revision:
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        finalized_lineage = finalized_lineage_fingerprint(facts, action)
        human_review_status = _build_human_observation_status(
            snapshot=snapshot,
            run_id=facts.run_id,
            finalized_lineage=finalized_lineage,
            assessment_result_id=assessment_result_id,
            assessment_result_fingerprint=assessment_result_fingerprint,
        )
        if assessment_result_id is not None:
            matches = [
                item
                for item in snapshot.post_final_assessment_results
                if item.assessment_result_id == assessment_result_id
                and item.run_id == facts.run_id
            ]
            if (
                len(matches) != 1
                or matches[0].result_fingerprint != assessment_result_fingerprint
            ):
                return _empty(
                    lifecycle_present=True,
                    status="invalid",
                    reason_code="reader_review_selection_incompatible",
                    review_status=human_review_status,
                )
            selected_result = matches[0]
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
    direction = snapshot.run_contract_bindings[0].run_direction
    request_template = (
        _REQUEST_TEMPLATE
        if direction.report_type == "management_monthly"
        and direction.output_language == "en"
        else None
    )
    policies = list(snapshot.post_final_assessment_policy_revisions)
    if request_template is None and (
        any(
            item.schema_version == "briefloop.post_final_assessment_request_record.v4"
            for item in series
        )
        or any(
            item.schema_version == "briefloop.post_final_assessment_policy_revision.v3"
            for item in policies
        )
    ):
        return _empty(
            lifecycle_present=True,
            status="unsupported",
            reason_code="reader_review_not_supported",
            user_status="not_assessed",
            review_status=human_review_status,
        )
    if not series and not policies:
        if request_template is None:
            return _empty(
                lifecycle_present=False,
                status="unsupported",
                reason_code="reader_review_not_supported",
                user_status="not_assessed",
                review_status=human_review_status,
            )
        return _empty(
            lifecycle_present=False,
            status="not_requested",
            reason_code="laj_not_run",
            user_status="not_assessed",
            request_template=request_template,
            run_action_available=request_template is not None,
            review_status=human_review_status,
        )
    if not series:
        return _empty(
            lifecycle_present=True,
            status="not_requested",
            reason_code="post_final_assessment_not_requested",
            user_status="not_assessed",
            request_template=request_template,
            run_action_available=request_template is not None,
            review_status=human_review_status,
        )
    run_results = [
        item
        for item in snapshot.post_final_assessment_results
        if item.run_id == facts.run_id
        and item.finalized_lineage_fingerprint
        == series[0].finalized_lineage_fingerprint
    ]
    requests_by_id = {item.assessment_request_id: item for item in series}
    compatible_pairs = [
        (item, requests_by_id[item.assessment_request_id])
        for item in run_results
        if item.assessment_request_id in requests_by_id
        and _reader_review_result_is_compatible(
            item, requests_by_id[item.assessment_request_id]
        )
    ]
    compatible_pairs.sort(key=lambda pair: pair[1].assessment_generation)
    verified_reader_archives: dict[str, tuple[Any, LajReaderView]] = {}
    for result_item, request_item in compatible_pairs:
        verified_archive = _load_verified_archive_view(
            root,
            request_item,
            facts.report.sha256,
        )
        if verified_archive is None:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_archive_invalid",
                review_status=human_review_status,
            )
        archive, view = verified_archive
        if not _reader_review_result_matches_archive(
            result_item,
            request_item,
            archive,
            view,
        ):
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_binding_invalid",
                review_status=human_review_status,
            )
        verified_reader_archives[result_item.assessment_result_id] = (
            archive,
            view,
        )
    compatible_options = tuple(
        _compatible_option(result_item, request_item)
        for result_item, request_item in compatible_pairs
    )
    compatible_pending = [
        item
        for item in series
        if _reader_review_request_is_compatible(item)
        and not any(
            result_item.assessment_request_id == item.assessment_request_id
            for result_item in run_results
        )
    ]
    if assessment_result_id is None:
        if len(compatible_pairs) > 1:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_selection_required",
                user_status="selection_required",
                compatible_result_options=compatible_options,
                request_template=request_template,
                review_status=human_review_status,
            )
        result = compatible_pairs[0][0] if compatible_pairs else None
    else:
        matches = [
            item for item, _request in compatible_pairs if item == selected_result
        ]
        if len(matches) != 1 or selected_result is None:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="reader_review_selection_incompatible",
                compatible_result_options=compatible_options,
                request_template=request_template,
                review_status=human_review_status,
            )
        result = selected_result
    if result is None and not compatible_pending:
        return _empty(
            lifecycle_present=True,
            status="unsupported",
            reason_code="reader_review_not_supported",
            user_status="not_assessed",
            compatible_result_options=compatible_options,
            request_template=request_template,
            run_action_available=(
                request_template is not None and assessment_result_id is None
            ),
            review_status=human_review_status,
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
        else compatible_pending[-1]
    )
    if request is None:
        return _empty(
            lifecycle_present=True,
            status="invalid",
            reason_code="post_final_assessment_selection_invalid",
            review_status=human_review_status,
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
            review_status=human_review_status,
        )
    try:
        requirement_labels = (
            _requirement_labels(policy)
            if _reader_review_request_is_compatible(request)
            else ()
        )
    except PostFinalAssessmentError:
        return _empty(
            lifecycle_present=True,
            status="invalid",
            reason_code="post_final_assessment_binding_invalid",
            compatible_result_options=compatible_options,
            request_template=request_template,
            review_status=human_review_status,
        )
    if result is not None:
        try:
            resolved = resolve_current_post_final_assessment_result(snapshot, request)
        except PostFinalAssessmentError as exc:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code=str(exc),
                review_status=human_review_status,
            )
        if resolved != result:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_selection_invalid",
                review_status=human_review_status,
            )
    if result is None:
        return _empty(
            lifecycle_present=True,
            status="pending",
            reason_code="post_final_assessment_outcome_unknown",
            user_status="unable_to_assess",
            compatible_result_options=compatible_options,
            requirement_labels=requirement_labels,
            request_template=request_template,
            review_status=human_review_status,
        )
    if result.schema_version == PostFinalAssessmentResultRecord.reader_review_schema_id:
        verified_archive = verified_reader_archives.get(result.assessment_result_id)
        if verified_archive is None:
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_archive_invalid",
                compatible_result_options=compatible_options,
                request_template=request_template,
                review_status=human_review_status,
            )
        _archive, archive_view = verified_archive
        projected_view = archive_view
        if result.reader_view_payload is not None:
            try:
                stored_view = LajReaderView.model_validate(
                    result.reader_view_payload,
                    strict=True,
                )
            except (TypeError, ValueError):
                return _empty(
                    lifecycle_present=True,
                    status="invalid",
                    reason_code="post_final_assessment_binding_invalid",
                    request_template=request_template,
                    review_status=human_review_status,
                )
            if stored_view.model_dump(
                mode="json", warnings="error"
            ) != archive_view.model_dump(
                mode="json",
                warnings="error",
            ):
                return _empty(
                    lifecycle_present=True,
                    status="invalid",
                    reason_code="post_final_assessment_binding_invalid",
                    request_template=request_template,
                    review_status=human_review_status,
                )
            # The Store payload remains the reader-facing projection; the
            # verified archive above is the eligibility check, not a second
            # presentation authority.
            projected_view = stored_view
        return PostFinalAssessmentProjection(
            lifecycle_present=True,
            status=projected_view.status,
            reason_code=(result.reason_codes[0] if result.reason_codes else None),
            view=projected_view,
            user_status=result.reader_review_status or "unable_to_assess",
            compatible_result_options=compatible_options,
            requirement_labels=requirement_labels,
            selected_result_id=result.assessment_result_id,
            selected_result_fingerprint=result.result_fingerprint,
            review_status=_build_review_status(
                snapshot=snapshot,
                result=result,
                view=projected_view,
            ),
            request_template=request_template,
            next_run_consumption=NEXT_RUN_CONSUMPTION,
            run_action_available=False,
            selection_required=False,
        )
    if result.reader_view_payload is not None:
        try:
            stored_view = LajReaderView.model_validate(
                result.reader_view_payload,
                strict=True,
            )
        except (TypeError, ValueError):
            return _empty(
                lifecycle_present=True,
                status="invalid",
                reason_code="post_final_assessment_binding_invalid",
                compatible_result_options=compatible_options,
                request_template=request_template,
                review_status=human_review_status,
            )
        return PostFinalAssessmentProjection(
            lifecycle_present=True,
            status=stored_view.status,
            reason_code=(result.reason_codes[0] if result.reason_codes else None),
            view=stored_view,
            user_status=result.reader_review_status or "unable_to_assess",
            compatible_result_options=compatible_options,
            requirement_labels=requirement_labels,
            selected_result_id=result.assessment_result_id,
            selected_result_fingerprint=result.result_fingerprint,
            review_status=_build_review_status(
                snapshot=snapshot,
                result=result,
                view=stored_view,
            ),
            request_template=request_template,
            next_run_consumption=NEXT_RUN_CONSUMPTION,
            run_action_available=False,
            selection_required=False,
        )
    if result.finding_count == 0 and result.withheld_finding_count == 0:
        return PostFinalAssessmentProjection(
            lifecycle_present=True,
            status=result.terminal_evidence_class,
            reason_code=result.reason_codes[0] if result.reason_codes else None,
            view=_recorded_zero_advice_view(result),
            user_status=(result.reader_review_status or "unable_to_assess"),
            compatible_result_options=compatible_options,
            requirement_labels=requirement_labels,
            selected_result_id=result.assessment_result_id,
            selected_result_fingerprint=result.result_fingerprint,
            review_status=_build_human_observation_status(
                snapshot=snapshot,
                run_id=result.run_id,
                finalized_lineage=result.finalized_lineage_fingerprint,
                assessment_result_id=result.assessment_result_id,
                assessment_result_fingerprint=result.result_fingerprint,
                reader_view_sha256=result.reader_view_sha256,
            ),
            request_template=request_template,
            next_run_consumption=NEXT_RUN_CONSUMPTION,
            run_action_available=False,
            selection_required=False,
        )
    verified_archive = _load_verified_archive_view(
        root,
        request,
        facts.report.sha256,
    )
    if verified_archive is None:
        return _empty(
            lifecycle_present=True,
            status="invalid",
            reason_code="post_final_assessment_archive_invalid",
            review_status=human_review_status,
        )
    archive, view = verified_archive
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
            review_status=human_review_status,
        )
    return PostFinalAssessmentProjection(
        lifecycle_present=True,
        status=view.status,
        reason_code=None,
        view=view,
        user_status=(
            result.reader_review_status
            or ("finding_returned" if view.finding_count else "unable_to_assess")
        ),
        compatible_result_options=compatible_options,
        requirement_labels=requirement_labels,
        selected_result_id=result.assessment_result_id,
        selected_result_fingerprint=result.result_fingerprint,
        review_status=_build_review_status(
            snapshot=snapshot,
            result=result,
            view=view,
        ),
        request_template=request_template,
        next_run_consumption=NEXT_RUN_CONSUMPTION,
        run_action_available=False,
        selection_required=False,
    )


__all__ = [
    "NEXT_RUN_CONSUMPTION",
    "PostFinalAssessmentProjection",
    "ReaderReviewCompatibleResultOption",
    "ReaderReviewRequirementLabel",
    "ReaderReviewRequestTemplate",
    "build_post_final_assessment_projection",
]
