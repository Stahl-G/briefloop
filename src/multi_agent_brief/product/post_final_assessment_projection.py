"""Read-only Store-qualified LAJ projection for the canonical HTML renderer."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
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
    report_type: Literal["management_monthly", "industry_weekly"]
    language: Literal["en", "zh"]
    profile_id: Literal["management_brief_en_v1", "industry_weekly_zh_v1"]
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
    total_output_token_ceiling: Literal[16384]
    output_tokens_per_call: Literal[8192]
    automatic_retry: Literal[False]
    advisory_only: Literal[True]
    authority_effect: Literal["none"]


@dataclass(frozen=True)
class ReaderReviewAssessmentUnitStatus(_DataclassMapping):
    """Archive-derived status for one frozen assessment-plan unit.

    These are deliberately projection-only values.  They are reconstructed from
    the verified archive's plan, run, and terminal attempts; a failed provider
    response never contributes a fabricated outcome here.
    """

    assessment_unit_id: str
    scope_class: str
    dimension_id: str
    sub_aspect_id: str
    state: Literal[
        "completed_no_finding",
        "finding_reported",
        "finding_withheld",
        "abstained",
        "unable_to_assess",
        "not_assessed",
    ]
    disposition: str | None = None
    attempt_ref: str | None = None
    attempt_status: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class ReaderReviewAssessmentScopeStatus(_DataclassMapping):
    """Aggregate status for the real O1/O2 scope inventory."""

    scope_class: str
    state: Literal[
        "completed_no_finding",
        "finding_reported",
        "partially_assessed",
        "unable_to_assess",
        "not_assessed",
    ]
    planned_unit_count: int
    completed_unit_count: int
    unable_unit_count: int
    finding_unit_count: int
    withheld_unit_count: int
    abstention_unit_count: int
    assessment_unit_ids: tuple[str, ...]
    note_code: Literal[
        "completed_no_finding_not_pass",
        "provider_attempt_incomplete",
        "finding_reported",
        "partial_scope",
        "not_assessed",
    ]
    reason_code: str | None = None


@dataclass(frozen=True)
class ReaderReviewProviderCall(_DataclassMapping):
    """Safe metadata for one provider call; no prompt/response bytes."""

    dimension_id: str
    status: str
    reason_code: str | None
    prompt_request_sha256: str


@dataclass(frozen=True)
class ReaderReviewRunEvidence(_DataclassMapping):
    """Non-secret, verified metadata explaining one Reader Review run."""

    trigger: Literal["explicit_human_authorization"]
    surface: Literal["not_recorded"]
    human_actor_id: str
    human_request_id: str
    assessment_generation: int
    assessment_request_id: str
    assessment_request_fingerprint: str
    policy_revision_id: str
    policy_fingerprint: str
    requested_model_id: str
    model_version: str
    expected_model_identity: str
    profile_id: str
    claimed_at: str
    auto_run: bool
    auto_open: bool
    ordered_prompt_request_sha256s: tuple[str, ...]
    system_prompt_sha256: str
    dimension_prompt_sha256: str
    assessment_plan_sha256: str
    input_binding_sha256: str
    instrument_sha256: str
    provider_call_count: int
    automatic_retry: Literal[False]
    calls: tuple[ReaderReviewProviderCall, ...]


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
    total_output_token_ceiling=16384,
    output_tokens_per_call=8192,
    automatic_retry=False,
    advisory_only=True,
    authority_effect="none",
)

_REQUEST_TEMPLATES = {
    ("management_monthly", "en"): _REQUEST_TEMPLATE,
    ("industry_weekly", "zh"): replace(
        _REQUEST_TEMPLATE,
        report_type="industry_weekly",
        language="zh",
        profile_id="industry_weekly_zh_v1",
    ),
}


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
    assessment_scopes: tuple[ReaderReviewAssessmentScopeStatus, ...] = ()
    assessment_units: tuple[ReaderReviewAssessmentUnitStatus, ...] = ()
    run_evidence: ReaderReviewRunEvidence | None = None


def build_successor_start_projection(
    root: Path,
    local: Any,
    improvement: Mapping[str, object],
) -> dict[str, object]:
    """Build the Store-derived successor choice for the secured review page.

    This keeps SQLite access in the existing read-model/projection layer.  The
    Brief HTML builder remains a pure consumer of this DTO and never opens the
    ControlStore itself.  Only frozen RunDirection and approved guidance text
    cross the page boundary; Core request fingerprints are intentionally absent.
    """

    unavailable: dict[str, object] = {
        "available": False,
        "reason_code": "successor_run_not_ready",
        "predecessor_run_id": local.run_id,
        "run_direction": None,
        "approved_guidance": [],
        "include_default": False,
        "next_run_consumption": NEXT_RUN_CONSUMPTION,
    }
    if local.view_state != "finalized" or local.reader_brief.state != "available":
        return unavailable
    try:
        with SQLiteControlStore.open(root / "briefloop.db") as store:
            history = store.load_history()
        heads = {
            snapshot.workspace_run_head.current_run_id
            for snapshot in history.snapshots
            if snapshot.workspace_run_head is not None
        }
        if heads != {local.run_id}:
            return {**unavailable, "reason_code": "successor_run_stale"}
        snapshot = history.snapshot_at_revision(local.run_id, history.store_revision)
        bindings = snapshot.run_contract_bindings
        if len(bindings) != 1:
            return {
                **unavailable,
                "reason_code": "successor_run_direction_unavailable",
            }
        direction = bindings[0].run_direction.model_dump(
            mode="json", exclude_unset=False
        )

        drafts = [
            row
            for row in improvement.get("recorded", ())
            if isinstance(row, dict) and row.get("guidance_id")
        ]
        statuses = {
            row.get("guidance_id"): row
            for row in improvement.get("guidance_statuses", ())
            if isinstance(row, dict) and row.get("guidance_id")
        }
        latest: dict[str, dict[str, object]] = {}
        for row in drafts:
            guidance_id = str(row["guidance_id"])
            current = latest.get(guidance_id)
            if current is None or int(row.get("draft_revision", 0)) > int(
                current.get("draft_revision", 0)
            ):
                latest[guidance_id] = row
        approved: list[dict[str, object]] = []
        for guidance_id, row in sorted(latest.items()):
            status = statuses.get(guidance_id)
            if not status or status.get("status") != "approved":
                continue
            if status.get("guidance_sha256") != row.get("guidance_sha256"):
                continue
            approved.append(
                {
                    "guidance_id": guidance_id,
                    "draft_revision": row.get("draft_revision"),
                    "guidance_scope": row.get("guidance_scope"),
                    "provenance_kind": row.get("provenance_kind"),
                    "guidance_text": row.get("guidance_text"),
                }
            )
        return {
            "available": True,
            "reason_code": None,
            "predecessor_run_id": local.run_id,
            "run_direction": direction,
            "approved_guidance": approved,
            "include_default": False,
            "next_run_consumption": NEXT_RUN_CONSUMPTION,
        }
    except (ControlStoreError, OSError, TypeError, ValueError):
        return {
            **unavailable,
            "reason_code": "successor_run_projection_unavailable",
        }


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


def _archive_assessment_projection(
    *,
    archive: Any,
    request: Any,
    policy: Any,
) -> tuple[
    tuple[ReaderReviewAssessmentScopeStatus, ...],
    tuple[ReaderReviewAssessmentUnitStatus, ...],
    ReaderReviewRunEvidence | None,
]:
    """Derive truthful profile inventory from one verified archive.

    ``LajReaderView`` intentionally contains only the reader-safe result.  For
    an incomplete run that view drops the failed dimension, which made the HTML
    fall back to the unrelated nine-dimension legacy inventory.  This helper
    reads only verified witness metadata (plan, run outcomes, and attempt refs)
    and never parses retained provider bytes.
    """

    witness = archive.witness
    plan = witness.assessment_plan
    run = witness.run
    terminal_reasons = tuple(str(item) for item in archive.reason_codes)
    specific_reason = next(
        (
            item
            for item in terminal_reasons
            if item in {"provider_incomplete", "provider_refused"}
        ),
        None,
    )
    outcomes = {item.assessment_unit_id: item for item in run.assessment_units}
    attempts = {item.dimension_id: item for item in run.attempt_refs}
    failed_dimensions = {
        item.dimension_id for item in run.attempt_refs if item.status == "failed"
    }
    unit_rows: list[ReaderReviewAssessmentUnitStatus] = []
    for unit in plan.units:
        outcome = outcomes.get(unit.assessment_unit_id)
        attempt = attempts.get(unit.dimension_id)
        reason_code = None
        if attempt is not None and attempt.status == "failed":
            reason_code = (
                specific_reason
                if specific_reason is not None and len(failed_dimensions) == 1
                else attempt.reason_code
            )
        if attempt is not None and attempt.status == "failed":
            # A terminal failed attempt dominates any retained body.  The
            # validator does not admit that body as an assessment outcome.
            state = "unable_to_assess"
            disposition = None
        elif outcome is None:
            if run.run_status != "completed" and unit.scope_class == "O2":
                state = "unable_to_assess"
                if reason_code is None and specific_reason is not None:
                    reason_code = specific_reason
            else:
                state = "not_assessed"
            disposition = None
        else:
            disposition = str(outcome.disposition)
            if disposition == "no_finding":
                state = "completed_no_finding"
            elif disposition == "finding_emitted":
                # In a non-terminal archive, findings are not reader-visible;
                # preserve that boundary rather than exposing hidden content.
                state = (
                    "finding_withheld"
                    if run.run_status != "completed"
                    else "finding_reported"
                )
            elif disposition.startswith("abstain_"):
                state = "abstained"
            else:
                state = "not_assessed"
        unit_rows.append(
            ReaderReviewAssessmentUnitStatus(
                assessment_unit_id=unit.assessment_unit_id,
                scope_class=unit.scope_class,
                dimension_id=unit.dimension_id,
                sub_aspect_id=unit.sub_aspect_id,
                state=state,
                disposition=disposition,
                attempt_ref=(attempt.attempt_ref if attempt is not None else None),
                attempt_status=(attempt.status if attempt is not None else None),
                reason_code=reason_code,
            )
        )

    scope_rows: list[ReaderReviewAssessmentScopeStatus] = []
    scope_order = {"O1": 0, "O2": 1}
    grouped: dict[str, list[ReaderReviewAssessmentUnitStatus]] = {}
    for row in unit_rows:
        grouped.setdefault(row.scope_class, []).append(row)
    for scope_class in sorted(
        grouped, key=lambda item: (scope_order.get(item, 9), item)
    ):
        rows = grouped[scope_class]
        completed_count = sum(row.state == "completed_no_finding" for row in rows)
        unable_count = sum(row.state == "unable_to_assess" for row in rows)
        finding_count = sum(row.state == "finding_reported" for row in rows)
        withheld_count = sum(row.state == "finding_withheld" for row in rows)
        abstention_count = sum(row.state == "abstained" for row in rows)
        scope_reasons = {row.reason_code for row in rows if row.reason_code is not None}
        states = {row.state for row in rows}
        if states == {"completed_no_finding"}:
            scope_state = "completed_no_finding"
            note_code = "completed_no_finding_not_pass"
        elif states == {"unable_to_assess"}:
            scope_state = "unable_to_assess"
            note_code = "provider_attempt_incomplete"
        elif "finding_reported" in states:
            scope_state = "finding_reported"
            note_code = "finding_reported"
        elif "finding_withheld" in states or len(states) > 1:
            scope_state = "partially_assessed"
            note_code = "partial_scope"
        else:
            scope_state = "not_assessed"
            note_code = "not_assessed"
        scope_rows.append(
            ReaderReviewAssessmentScopeStatus(
                scope_class=scope_class,
                state=scope_state,
                planned_unit_count=len(rows),
                completed_unit_count=completed_count,
                unable_unit_count=unable_count,
                finding_unit_count=finding_count,
                withheld_unit_count=withheld_count,
                abstention_unit_count=abstention_count,
                assessment_unit_ids=tuple(row.assessment_unit_id for row in rows),
                note_code=note_code,
                reason_code=(
                    next(iter(scope_reasons)) if len(scope_reasons) == 1 else None
                ),
            )
        )

    run_evidence: ReaderReviewRunEvidence | None = None
    # v4 Reader Review requests are the only requests with a Human actor/request
    # and a policy-level auto-run boundary.  ``surface`` remains explicitly
    # unknown; the archive proves authorization, not that a user clicked a UI.
    if (
        getattr(request, "schema_version", None)
        == "briefloop.post_final_assessment_request_record.v4"
        and getattr(request, "human_actor_id", None)
        and getattr(request, "human_request_id", None)
        and getattr(policy, "schema_version", None)
        == "briefloop.post_final_assessment_policy_revision.v3"
        and getattr(policy, "auto_run", True) is False
        and getattr(policy, "auto_open", True) is False
    ):
        calls = tuple(
            ReaderReviewProviderCall(
                dimension_id=item.dimension_id,
                status=item.status,
                reason_code=(
                    (
                        specific_reason
                        if specific_reason is not None and len(failed_dimensions) == 1
                        else item.reason_code
                    )
                    if item.status == "failed"
                    else None
                ),
                prompt_request_sha256=item.prompt_request_sha256,
            )
            for item in witness.dimension_attempt_evidence
        )
        manifest = witness.instrument_manifest
        run_evidence = ReaderReviewRunEvidence(
            trigger="explicit_human_authorization",
            surface="not_recorded",
            human_actor_id=request.human_actor_id,
            human_request_id=request.human_request_id,
            assessment_generation=request.assessment_generation,
            assessment_request_id=request.assessment_request_id,
            assessment_request_fingerprint=request.request_fingerprint,
            policy_revision_id=policy.policy_revision_id,
            policy_fingerprint=policy.policy_fingerprint,
            requested_model_id=request.requested_model_id,
            model_version=request.model_version,
            expected_model_identity=request.expected_model_identity,
            profile_id=request.profile_id,
            claimed_at=request.claimed_at,
            auto_run=policy.auto_run,
            auto_open=policy.auto_open,
            ordered_prompt_request_sha256s=tuple(
                archive.request.ordered_prompt_request_sha256s
            ),
            system_prompt_sha256=manifest.system_prompt_sha256,
            dimension_prompt_sha256=manifest.dimension_prompt_sha256,
            assessment_plan_sha256=plan.assessment_plan_sha256,
            input_binding_sha256=witness.input_binding.input_binding_sha256,
            instrument_sha256=manifest.instrument_sha256,
            provider_call_count=len(calls),
            automatic_retry=False,
            calls=calls,
        )
    return tuple(scope_rows), tuple(unit_rows), run_evidence


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
        and (request.report_type, request.language) in _REQUEST_TEMPLATES
        and request.profile_id
        == _REQUEST_TEMPLATES[(request.report_type, request.language)].profile_id
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
    """Load and verify archive evidence for Store projection/fallback reads."""

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
    request_template = _REQUEST_TEMPLATES.get(
        (direction.report_type, direction.output_language)
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
        assessment_scopes, assessment_units, run_evidence = (
            _archive_assessment_projection(
                archive=_archive,
                request=request,
                policy=policy,
            )
        )
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
            assessment_scopes=assessment_scopes,
            assessment_units=assessment_units,
            run_evidence=run_evidence,
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
    "ReaderReviewAssessmentScopeStatus",
    "ReaderReviewAssessmentUnitStatus",
    "ReaderReviewCompatibleResultOption",
    "ReaderReviewProviderCall",
    "ReaderReviewRequirementLabel",
    "ReaderReviewRequestTemplate",
    "ReaderReviewRunEvidence",
    "build_post_final_assessment_projection",
]
