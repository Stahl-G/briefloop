"""Best-effort launchers for dormant and actionable local Review Sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable
import webbrowser

from .contracts import (
    PostFinalReviewReadModel,
    ReaderReviewResultSelection,
    ReviewSessionCommand,
)
from .server import ReviewSessionServer, create_review_session_server


@dataclass(frozen=True)
class ReviewLaunchResult:
    server: ReviewSessionServer
    url: str
    browser_opened: bool
    reason_code: str
    static_quality_panel_path: str | None
    runtime_authority: bool = False
    user_status: str | None = None
    compatible_result_count: int = 0
    run_action_available: bool = False
    next_run_consumption: str | None = None


def launch_review_session(
    read_model: PostFinalReviewReadModel,
    *,
    open_browser: bool = True,
    browser_open: Callable[[str], bool] = webbrowser.open,
    static_quality_panel_path: str | None = None,
) -> ReviewLaunchResult:
    """Start the ephemeral server; browser failure never changes its authority."""

    server = create_review_session_server(read_model)
    server.start()
    opened = False
    reason = "review_session_headless"
    if open_browser:
        try:
            opened = browser_open(server.url) is not False
        except Exception:
            opened = False
        reason = (
            "review_session_opened" if opened else "review_session_browser_unavailable"
        )
    return ReviewLaunchResult(
        server=server,
        url=server.url,
        browser_opened=opened,
        reason_code=reason,
        static_quality_panel_path=static_quality_panel_path,
    )


__all__ = ["ReviewLaunchResult", "launch_review_session"]


def launch_actionable_review_session(
    workspace: str | Path,
    assessment_result_id: str | None = None,
    assessment_result_fingerprint: str | None = None,
    *,
    open_browser: bool = True,
    browser_open: Callable[[str], bool] = webbrowser.open,
) -> ReviewLaunchResult:
    """Serve the canonical brief_html page with strict local command transport."""

    from multi_agent_brief.product.brief_html.builder import (
        BriefPagesError,
        build_brief_pages_data,
    )
    from multi_agent_brief.product.brief_html.render import render_brief_pages_html
    from multi_agent_brief.product.post_final_assessment import (
        PostFinalAssessmentError,
        PostFinalAssessmentService,
    )
    from multi_agent_brief.product.post_final_review import (
        PostFinalReviewError,
        PostFinalReviewService,
    )

    root = Path(workspace).expanduser().resolve()
    data = build_brief_pages_data(
        root,
        assessment_result_id=assessment_result_id,
        assessment_result_fingerprint=assessment_result_fingerprint,
    )
    if (assessment_result_id is None) != (assessment_result_fingerprint is None):
        raise PostFinalAssessmentError("post_final_assessment_selection_invalid")
    if assessment_result_id is not None and (
        data["semantic"].get("selected_result_id") != assessment_result_id
        or data["semantic"].get("selected_result_fingerprint")
        != assessment_result_fingerprint
    ):
        raise PostFinalAssessmentError("post_final_assessment_selection_invalid")
    run_id = str(data["workspace"]["run_id"])
    html = render_brief_pages_html(data)
    assessment_service = PostFinalAssessmentService(root)
    command_lock = Lock()
    selected: list[tuple[str, str] | None] = [None]

    def update_selection(page_data: dict[str, object]) -> None:
        semantic = page_data.get("semantic")
        if not isinstance(semantic, dict):
            selected[0] = None
            return
        result_id = semantic.get("selected_result_id")
        fingerprint = semantic.get("selected_result_fingerprint")
        selected[0] = (
            (result_id, fingerprint)
            if isinstance(result_id, str) and isinstance(fingerprint, str)
            else None
        )

    def rebuild(
        selection: ReaderReviewResultSelection | None = None,
    ) -> dict[str, object]:
        selected_result_id = (
            selection.assessment_result_id
            if selection is not None
            else selected[0][0]
            if selected[0] is not None
            else None
        )
        selected_fingerprint = (
            selection.assessment_result_fingerprint
            if selection is not None
            else selected[0][1]
            if selected[0] is not None
            else None
        )
        refreshed = build_brief_pages_data(
            root,
            assessment_result_id=selected_result_id,
            assessment_result_fingerprint=selected_fingerprint,
        )
        semantic = refreshed["semantic"]
        if selection is not None and (
            semantic.get("selected_result_id") != selection.assessment_result_id
            or semantic.get("selected_result_fingerprint")
            != selection.assessment_result_fingerprint
        ):
            raise PostFinalAssessmentError("post_final_assessment_selection_invalid")
        update_selection(refreshed)
        return refreshed

    update_selection(data)

    def handle(command: ReviewSessionCommand) -> dict[str, object]:
        with command_lock:
            try:
                payload = dict(command.payload)
                if command.action == "run_reader_review":
                    result = assessment_service.run_reader_review(payload)
                    selected[0] = None
                    refreshed = rebuild(None)
                    return {
                        "ok": result.get("ok") is True,
                        "reason_code": result.get("reason_code"),
                        "reason_codes": result.get("reason_codes", []),
                        "result": result,
                        "page_data": refreshed,
                    }
                if command.action == "select_result":
                    selection = ReaderReviewResultSelection.model_validate(
                        payload, strict=True
                    )
                    refreshed = rebuild(selection)
                    return {
                        "ok": True,
                        "reason_code": "reader_review_selection_refreshed",
                        "page_data": refreshed,
                    }
                if command.action == "refresh":
                    refreshed = rebuild(None)
                    return {
                        "ok": True,
                        "reason_code": "reader_review_projection_refreshed",
                        "page_data": refreshed,
                    }

                if selected[0] is None:
                    raise PostFinalReviewError("post_final_review_selection_required")
                service = PostFinalReviewService(root, *selected[0])
                if command.action in {"accept", "reject", "defer"}:
                    payload["decision"] = command.action
                    result = service.record_disposition(payload)
                elif command.action == "draft":
                    result = service.append_guidance_draft(payload)
                elif command.action == "approve":
                    result = service.approve_guidance(payload)
                elif command.action == "deactivate":
                    result = service.deactivate_guidance(payload)
                elif command.action == "revert":
                    result = service.revert_guidance(payload)
                elif command.action == "supersede":
                    result = service.supersede_guidance(payload)
                else:
                    result = service.review_status()
                refreshed = rebuild(None)
                return {
                    "ok": True,
                    "result": result,
                    "review_status": service.review_status(),
                    "page_data": refreshed,
                }
            except (
                BriefPagesError,
                PostFinalAssessmentError,
                PostFinalReviewError,
            ) as exc:
                return {
                    "ok": False,
                    "reason_code": str(exc),
                    "reason_codes": [str(exc)],
                }

    server = create_review_session_server(
        None,
        brief_html=html,
        run_id=run_id,
        command_handler=handle,
    )
    server.start()
    opened = False
    reason = "review_session_headless"
    if open_browser:
        try:
            opened = browser_open(server.url) is not False
        except Exception:
            opened = False
        reason = (
            "review_session_opened" if opened else "review_session_browser_unavailable"
        )
    return ReviewLaunchResult(
        server=server,
        url=server.url,
        browser_opened=opened,
        reason_code=reason,
        static_quality_panel_path=None,
        user_status=str(data["semantic"]["status"]),
        compatible_result_count=len(
            data["semantic"].get("compatible_result_options", [])
        ),
        run_action_available=data["semantic"].get("run_action_available") is True,
        next_run_consumption=str(data["improvement"]["next_run_consumption"]),
    )


__all__ = [
    "ReviewLaunchResult",
    "launch_actionable_review_session",
    "launch_review_session",
]
