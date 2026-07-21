"""Best-effort browser launcher for an already-built Review Session model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import webbrowser

from .contracts import PostFinalReviewReadModel
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
        reason = "review_session_opened" if opened else "review_session_browser_unavailable"
    return ReviewLaunchResult(
        server=server,
        url=server.url,
        browser_opened=opened,
        reason_code=reason,
        static_quality_panel_path=static_quality_panel_path,
    )


__all__ = ["ReviewLaunchResult", "launch_review_session"]
