"""Best-effort browser launcher for an already-built Review Session model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import webbrowser

from .contracts import PostFinalReviewReadModel, ReviewSessionCommand
from .server import ReviewSessionServer, create_review_session_server


@dataclass(frozen=True)
class ReviewLaunchResult:
    server: ReviewSessionServer
    url: str
    browser_opened: bool
    reason_code: str
    static_quality_panel_path: str | None
    runtime_authority: bool = False


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
    *,
    open_browser: bool = True,
    browser_open: Callable[[str], bool] = webbrowser.open,
) -> ReviewLaunchResult:
    """Serve the canonical brief_html page with strict local command transport."""

    from multi_agent_brief.product.brief_html.builder import build_brief_pages_data
    from multi_agent_brief.product.brief_html.render import render_brief_pages_html
    from multi_agent_brief.product.post_final_review import PostFinalReviewService

    root = Path(workspace).expanduser().resolve()
    data = build_brief_pages_data(root)
    run_id = str(data["workspace"]["run_id"])
    html = render_brief_pages_html(data)
    service = PostFinalReviewService(root)

    def handle(command: ReviewSessionCommand) -> dict[str, object]:
        payload = dict(command.payload)
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
        return {
            "ok": True,
            "result": result,
            "review_status": service.review_status(),
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
    )


__all__ = [
    "ReviewLaunchResult",
    "launch_actionable_review_session",
    "launch_review_session",
]
