"""Store/LAJ → local brief data contract (read-only projections only).

The Brief, run state, quality summary, and frozen reader bytes all come from
one strict runtime-host read model built from one verified ControlStore
history. Store-qualified Reader Review is selected only by the pure canonical
projection. An explicit hash-bound ``laj_view`` may be rendered only as an
advanced read-only fallback when no Store assessment lifecycle exists; it
never participates in selection or Human effects. Store-native Human
dispositions and approved guidance are projected read-only; reuse occurs only
through a separate explicit successor transaction. No legacy JSON fold-in is
read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args

from multi_agent_brief.product.review_session.contracts import FindingDimensionId
from multi_agent_brief.product.post_final_assessment_projection import (
    PostFinalAssessmentProjection,
    build_post_final_assessment_projection,
    build_successor_start_projection,
)
from multi_agent_brief.runtime_host_v2.projections import (
    build_local_run_presentation,
    build_quality_projection_from_local_run,
)
from multi_agent_brief.semantic_evaluator.reader import (
    LajReaderView,
    bind_laj_reader_view_to_report,
    build_empty_laj_reader_view,
    load_laj_reader_view,
)

BRIEF_PAGES_DATA_SCHEMA = "briefloop.brief_pages.data.v2"
BRIEF_PAGES_BOUNDARY = (
    "Read-only projection. No Gate, approval, delivery, repair, or runtime "
    "authority. LAJ surfaces are Experimental advisory; no finding is neutral "
    "and LAJ utility is NOT MEASURED."
)
LAJ_EXPERIMENTAL_BANNER = (
    "Experimental AI assessment. Advisory only. Not a Gate, delivery decision, "
    "or proof of correctness. Utility NOT MEASURED."
)
IMPROVEMENT_CONSUMPTION_NOTE = (
    "Approved guidance remains advisory here. A Human may explicitly opt into "
    "freezing compatible guidance when starting a separate successor run."
)
IMPROVEMENT_PLANNED_NOTE = (
    "Human disposition, edited guidance, separate approval, and explicit "
    "successor-only reuse are available."
)
READER_REVIEW_ZERO_FINDING_DISCLAIMER = (
    "No finding was returned in the completed supported checks. This is not a "
    "quality pass and does not verify facts, source quality, strategic "
    "correctness, or publication readiness."
)


class BriefPagesError(ValueError):
    """Raised when the three-page data contract cannot be built."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _row(label: str, value: Any, tone: str = "neutral") -> dict[str, Any]:
    return {"label": label, "value": value, "tone": tone}


def _quality_groups(
    local: Any,
) -> dict[str, list[dict[str, Any]]]:
    gates = [
        _row(
            item.gate_id,
            item.status,
            "pass"
            if item.status == "pass"
            else ("block" if item.blocking and item.status == "fail" else "attention"),
        )
        for item in local.summary.gates
    ]
    if not gates:
        gates = [_row("Gate evaluations", "not_evaluated", "unavailable")]

    return {
        "control": [
            _row("run_id", local.run_id),
            _row("runtime", local.runtime),
            _row("store_revision", local.store_revision),
            _row("transactions", len(local.summary.receipt_ids)),
            _row("role_topology", local.execution_topology),
            _row("view_state", local.view_state),
        ],
        "source": [
            _row("accepted_sources", local.summary.accepted_source_count),
        ],
        "gates": gates,
        "claims": [
            _row("claims", local.summary.claim_count),
        ],
        "reader_clean": [
            _row("finalizations", local.summary.finalization_count),
            _row("reader_brief", local.reader_brief.state),
        ],
        "closeout": [
            _row(
                "terminal_state",
                local.terminal_state,
                "pass" if local.view_state == "finalized" else "neutral",
            ),
            _row("completion_target", local.completion_target or "manual"),
        ],
    }


def _quality_page(local: Any) -> dict[str, Any]:
    projection = build_quality_projection_from_local_run(local)
    return {
        "status": "available" if projection.get("ok") else "unavailable",
        "reason_code": None if projection.get("ok") else projection.get("reason_code"),
        "boundary": "projection_only_not_gate_or_delivery_authority",
        "projection": projection,
        "groups": _quality_groups(local),
        "actions": [local.next_action.model_dump(mode="json", exclude_unset=False)],
    }


def _requirement_assessment_rows(
    view: LajReaderView,
    qualified: PostFinalAssessmentProjection,
) -> list[dict[str, Any]]:
    labels = {item.requirement_id: item for item in qualified.requirement_labels}
    rows: list[dict[str, Any]] = []
    for assessment in view.requirement_assessments:
        row = assessment.model_dump(mode="json", exclude_unset=False)
        label = labels.get(assessment.requirement_id)
        row["requirement_type"] = label.requirement_type if label else None
        row["requirement_text"] = label.text if label else None
        row["source_locator"] = label.source_locator if label else None
        rows.append(row)
    return rows


def _semantic_page(
    local: Any,
    laj_view_path: str | Path | None,
    *,
    qualified: PostFinalAssessmentProjection,
) -> dict[str, Any]:
    view: LajReaderView
    if qualified.lifecycle_present:
        view = qualified.view
    else:
        source = Path(laj_view_path).expanduser() if laj_view_path is not None else None
        if source is None or not source.is_file():
            view = build_empty_laj_reader_view(
                status="not_available", reason_code="laj_not_run"
            )
        else:
            try:
                view = load_laj_reader_view(source)
                if (
                    local.reader_brief.state == "available"
                    and local.reader_brief.sha256 is not None
                ):
                    view = bind_laj_reader_view_to_report(
                        view,
                        expected_report_sha256=local.reader_brief.sha256,
                    )
                else:
                    view = build_empty_laj_reader_view(
                        status="not_available",
                        reason_code="final_reader_not_available",
                    )
            except Exception:
                view = build_empty_laj_reader_view(
                    status="invalid", reason_code="laj_reader_view_invalid"
                )

    dimension_ids = list(get_args(FindingDimensionId))
    findings = [
        finding.model_dump(mode="json", exclude_unset=False)
        for finding in view.findings
    ]
    review_status = qualified.review_status
    dispositions = review_status.get("dispositions", []) if review_status else []
    human_observations = (
        review_status.get("human_observations", []) if review_status else []
    )
    review_by_finding = {
        item["finding_id"]: item
        for item in dispositions
        if isinstance(item, dict) and isinstance(item.get("finding_id"), str)
    }
    for finding in findings:
        review = review_by_finding.get(finding["finding_id"])
        finding["finding_fingerprint"] = (
            review["finding_fingerprint"] if review is not None else None
        )
        finding["human_disposition"] = review["current"] if review is not None else None
    dimensions = [
        {
            "dimension_id": dimension,
            "state": (
                "finding_reported"
                if any(item["dimension_id"] == dimension for item in findings)
                else "not_assessed_in_view"
            ),
        }
        for dimension in dimension_ids
    ]
    status = (
        qualified.user_status
        if qualified.lifecycle_present or qualified.request_template is not None
        else "not_run"
        if view.reason_codes == ["laj_not_run"]
        else view.status
    )
    compatible_result_options = [
        {
            "assessment_result_id": item.assessment_result_id,
            "assessment_result_fingerprint": item.assessment_result_fingerprint,
            "assessment_generation": item.assessment_generation,
            "requested_model_id": item.requested_model_id,
            "model_version": item.model_version,
            "terminal_evidence_class": item.terminal_evidence_class,
            "assessed_unit_count": item.assessed_unit_count,
            "finding_count": item.finding_count,
            "withheld_finding_count": item.withheld_finding_count,
            "abstention_count": item.abstention_count,
            "recorded_at": item.recorded_at,
        }
        for item in qualified.compatible_result_options
    ]
    request_template = (
        {
            "schema_version": qualified.request_template.schema_version,
            "assessment_kind": qualified.request_template.assessment_kind,
            "report_type": qualified.request_template.report_type,
            "language": qualified.request_template.language,
            "profile_id": qualified.request_template.profile_id,
            "protocol": qualified.request_template.protocol,
            "endpoint_class": qualified.request_template.endpoint_class,
            "egress_scope": qualified.request_template.egress_scope,
            "report_scope": qualified.request_template.report_scope,
            "context_scope": qualified.request_template.context_scope,
            "disclosure_confirmed": qualified.request_template.disclosure_confirmed,
            "public_safe_egress_attested": (
                qualified.request_template.public_safe_egress_attested
            ),
            "cost_status": qualified.request_template.cost_status,
            "provider_call_ceiling": (qualified.request_template.provider_call_ceiling),
            "total_input_token_ceiling": (
                qualified.request_template.total_input_token_ceiling
            ),
            "total_output_token_ceiling": (
                qualified.request_template.total_output_token_ceiling
            ),
            "output_tokens_per_call": (
                qualified.request_template.output_tokens_per_call
            ),
            "automatic_retry": qualified.request_template.automatic_retry,
            "advisory_only": qualified.request_template.advisory_only,
            "authority_effect": qualified.request_template.authority_effect,
        }
        if qualified.request_template is not None
        else None
    )
    return {
        "status": status,
        "assessment_status": qualified.status if qualified.lifecycle_present else None,
        "banner": LAJ_EXPERIMENTAL_BANNER,
        "boundary": view.boundary,
        "coverage": {
            "assessed_unit_count": view.assessed_unit_count,
            "finding_count": view.finding_count,
            "withheld_finding_count": view.withheld_finding_count,
            "abstention_count": view.abstention_count,
        },
        "dimensions": dimensions,
        "findings": findings,
        "requirement_assessments": _requirement_assessment_rows(view, qualified),
        "handoff_note": (
            "Handoff units are evidence needs, not defects; they never trigger Gates."
        ),
        "reason_codes": view.reason_codes,
        "store_qualified": qualified.lifecycle_present,
        "compatible_result_options": compatible_result_options,
        "selected_result_id": qualified.selected_result_id,
        "selected_result_fingerprint": qualified.selected_result_fingerprint,
        "selection_required": qualified.selection_required,
        "request_template": request_template,
        "run_action_available": qualified.run_action_available,
        "review_actions_available": review_status is not None,
        "human_observations": human_observations,
        "assessment_result_id": (
            review_status["assessment_result_id"] if review_status is not None else None
        ),
        "assessment_result_fingerprint": (
            review_status["assessment_result_fingerprint"]
            if review_status is not None
            else None
        ),
        "reader_view_sha256": (
            review_status["reader_view_sha256"] if review_status is not None else None
        ),
        "disclaimer": (
            READER_REVIEW_ZERO_FINDING_DISCLAIMER
            if status == "no_finding_returned_in_completed_supported_checks"
            else view.disclaimer
        ),
    }


def _brief_page(local: Any) -> dict[str, Any]:
    reader = local.reader_brief
    markdown = (
        reader.markdown_utf8.decode("utf-8")
        if reader.markdown_utf8 is not None
        else None
    )
    return {
        "status": reader.state,
        "view_state": local.view_state,
        "terminal_state": local.terminal_state,
        "completion_target": local.completion_target,
        "reason_code": local.reason_code,
        "artifact": (
            {
                "artifact_id": reader.artifact_id,
                "revision": reader.revision,
                "sha256": reader.sha256,
            }
            if reader.state == "available"
            else None
        ),
        "markdown": markdown,
        "boundary": (
            "Exact Store-bound local reader projection; not approval, package, "
            "delivery, or publication."
        ),
    }


def _improvement_page(
    local: Any,
    qualified: PostFinalAssessmentProjection,
) -> dict[str, Any]:
    """Build the feedback projection without making assessment a prerequisite.

    A finalized report is sufficient for a report-bound Human observation.  A
    selected Reader Review result adds an exact result binding, but a missing,
    failed, unavailable, or zero-finding assessment must not hide the Human
    observation affordance.  All persisted rows still come from the optional
    Store review status; this function never invents a row.
    """

    reader = local.reader_brief
    report_available = (
        local.view_state == "finalized"
        and reader.state == "available"
        and reader.sha256 is not None
    )
    status = qualified.review_status
    if status is not None:
        return {
            "status": "available",
            "reason_code": None,
            "recorded": status.get("guidance_drafts", []),
            "guidance_statuses": status.get("guidance_statuses", []),
            "human_observations": status.get("human_observations", []),
            "observation_allowed": report_available,
            "observation_binding_mode": (
                "selected_result"
                if qualified.selected_result_id is not None
                else "report_bound"
            ),
            "report_binding": {
                "run_id": local.run_id,
                "artifact_id": reader.artifact_id,
                "revision": reader.revision,
                "sha256": reader.sha256,
            },
            "selected_result": (
                {
                    "assessment_result_id": qualified.selected_result_id,
                    "assessment_result_fingerprint": (
                        qualified.selected_result_fingerprint
                    ),
                }
                if qualified.selected_result_id is not None
                else None
            ),
            "consumption_note": IMPROVEMENT_CONSUMPTION_NOTE,
            "planned_note": IMPROVEMENT_PLANNED_NOTE,
            "next_run_consumption": qualified.next_run_consumption,
        }
    if report_available:
        return {
            "status": "available",
            "reason_code": None,
            "recorded": [],
            "guidance_statuses": [],
            "human_observations": [],
            "observation_allowed": True,
            "observation_binding_mode": "report_bound",
            "report_binding": {
                "run_id": local.run_id,
                "artifact_id": reader.artifact_id,
                "revision": reader.revision,
                "sha256": reader.sha256,
            },
            "selected_result": None,
            "consumption_note": IMPROVEMENT_CONSUMPTION_NOTE,
            "planned_note": IMPROVEMENT_PLANNED_NOTE,
            "next_run_consumption": qualified.next_run_consumption,
        }
    return {
        "status": "unavailable",
        "reason_code": "post_final_review_not_available",
        "recorded": [],
        "guidance_statuses": [],
        "human_observations": [],
        "observation_allowed": False,
        "observation_binding_mode": "unavailable",
        "report_binding": None,
        "selected_result": None,
        "consumption_note": IMPROVEMENT_CONSUMPTION_NOTE,
        "planned_note": IMPROVEMENT_PLANNED_NOTE,
        "next_run_consumption": qualified.next_run_consumption,
    }


def build_brief_pages_data(
    workspace: str | Path,
    *,
    laj_view_path: str | Path | None = None,
    assessment_result_id: str | None = None,
    assessment_result_fingerprint: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the full three-page data contract from Store/LAJ sources only."""

    root = Path(workspace).expanduser().resolve()
    try:
        local = build_local_run_presentation(root)
    except Exception as exc:
        raise BriefPagesError("control_store_integrity_invalid") from exc
    qualified = build_post_final_assessment_projection(
        root,
        assessment_result_id=assessment_result_id,
        assessment_result_fingerprint=assessment_result_fingerprint,
    )
    improvement = _improvement_page(local, qualified)
    return {
        "schema_version": BRIEF_PAGES_DATA_SCHEMA,
        "generated_at": generated_at or _utc_now(),
        "boundary": BRIEF_PAGES_BOUNDARY,
        "workspace": {
            "run_id": local.run_id,
            "runtime": local.runtime,
            "store_revision": local.store_revision,
            "authority": "sqlite_control_store",
        },
        "run": {
            "view_state": local.view_state,
            "completed_stages": local.completed_stages,
            "total_stages": local.total_stages,
            "current_stage": local.current_stage,
            "current_role": local.current_role,
            "reason_code": local.reason_code,
            "terminal_state": local.terminal_state,
            "completion_target": local.completion_target,
        },
        "brief": _brief_page(local),
        "quality": _quality_page(local),
        "semantic": _semantic_page(
            local,
            laj_view_path,
            qualified=qualified,
        ),
        "improvement": improvement,
        "successor": build_successor_start_projection(root, local, improvement),
    }


__all__ = [
    "BRIEF_PAGES_BOUNDARY",
    "BRIEF_PAGES_DATA_SCHEMA",
    "BriefPagesError",
    "IMPROVEMENT_CONSUMPTION_NOTE",
    "IMPROVEMENT_PLANNED_NOTE",
    "LAJ_EXPERIMENTAL_BANNER",
    "READER_REVIEW_ZERO_FINDING_DISCLAIMER",
    "build_brief_pages_data",
]
