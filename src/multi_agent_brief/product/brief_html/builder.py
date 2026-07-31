"""Store/LAJ → local brief data contract (read-only projections only).

The Brief, run state, quality summary, and frozen reader bytes all come from
one strict runtime-host read model built from one verified ControlStore
history.  LAJ is rendered only when an explicit hash-bound view is supplied.
Store-native Human dispositions and approved guidance are projected read-only;
next-run guidance consumption remains unavailable.  No legacy JSON fold-in is
read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args

from multi_agent_brief.product.review_session.contracts import FindingDimensionId
from multi_agent_brief.product.post_final_assessment_projection import (
    build_post_final_assessment_projection,
)
from multi_agent_brief.product.post_final_review import (
    PostFinalReviewError,
    PostFinalReviewService,
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
    "Approved guidance is Store-native, but no current or future run consumes "
    "it until the separate next-run snapshot unit ships."
)
IMPROVEMENT_PLANNED_NOTE = (
    "Human disposition, edited guidance and separate approval are available; "
    "next-run consumption is not shipped."
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


def _semantic_page(
    local: Any,
    laj_view_path: str | Path | None,
    *,
    workspace: Path,
    assessment_result_id: str | None,
    assessment_result_fingerprint: str | None,
) -> dict[str, Any]:
    view: LajReaderView
    qualified = build_post_final_assessment_projection(
        workspace,
        assessment_result_id=assessment_result_id,
        assessment_result_fingerprint=assessment_result_fingerprint,
    )
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
    review_status: dict[str, Any] | None = None
    if qualified.lifecycle_present and qualified.status == "available":
        try:
            if assessment_result_id is None or assessment_result_fingerprint is None:
                raise PostFinalReviewError("post_final_review_selection_required")
            review_status = PostFinalReviewService(
                workspace,
                assessment_result_id,
                assessment_result_fingerprint,
            ).review_status()
        except PostFinalReviewError:
            review_status = None
    review_by_finding = {
        item["finding_id"]: item
        for item in (review_status or {}).get("dispositions", [])
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
    return {
        "status": (
            qualified.status
            if qualified.lifecycle_present
            else "not_run"
            if view.reason_codes == ["laj_not_run"]
            else view.status
        ),
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
        "handoff_note": (
            "Handoff units are evidence needs, not defects; they never trigger Gates."
        ),
        "reason_codes": view.reason_codes,
        "store_qualified": qualified.lifecycle_present,
        "review_actions_available": review_status is not None,
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
        "disclaimer": view.disclaimer,
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
    workspace: Path,
    assessment_result_id: str | None,
    assessment_result_fingerprint: str | None,
) -> dict[str, Any]:
    try:
        if assessment_result_id is None or assessment_result_fingerprint is None:
            raise PostFinalReviewError("post_final_review_selection_required")
        status = PostFinalReviewService(
            workspace,
            assessment_result_id,
            assessment_result_fingerprint,
        ).review_status()
    except PostFinalReviewError:
        status = None
    if status is not None:
        return {
            "status": "available",
            "reason_code": None,
            "recorded": status["guidance_drafts"],
            "guidance_statuses": status["guidance_statuses"],
            "consumption_note": IMPROVEMENT_CONSUMPTION_NOTE,
            "planned_note": IMPROVEMENT_PLANNED_NOTE,
            "next_run_consumption": "not_shipped",
        }
    return {
        "status": "unavailable",
        "reason_code": "post_final_review_not_available",
        "recorded": [],
        "guidance_statuses": [],
        "consumption_note": IMPROVEMENT_CONSUMPTION_NOTE,
        "planned_note": IMPROVEMENT_PLANNED_NOTE,
        "next_run_consumption": "not_shipped",
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
            workspace=root,
            assessment_result_id=assessment_result_id,
            assessment_result_fingerprint=assessment_result_fingerprint,
        ),
        "improvement": _improvement_page(
            root,
            assessment_result_id,
            assessment_result_fingerprint,
        ),
    }


__all__ = [
    "BRIEF_PAGES_BOUNDARY",
    "BRIEF_PAGES_DATA_SCHEMA",
    "BriefPagesError",
    "IMPROVEMENT_CONSUMPTION_NOTE",
    "IMPROVEMENT_PLANNED_NOTE",
    "LAJ_EXPERIMENTAL_BANNER",
    "build_brief_pages_data",
]
