from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from multi_agent_brief.product.review_session.launcher import launch_review_session
from multi_agent_brief.product.review_session.launcher import (
    launch_actionable_review_session,
)
from multi_agent_brief.product.review_session.server import (
    CONTENT_SECURITY_POLICY,
    CSRF_TOKEN_HEADER,
    MAX_JSON_BODY_BYTES,
    SESSION_TOKEN_HEADER,
    create_review_session_server,
)
from multi_agent_brief.product.review_session.static_qp import (
    render_static_quality_panel,
)
from multi_agent_brief.product.post_final_review import PostFinalReviewError
from tests.test_post_final_review_contracts import build_read_model
from tests.test_post_final_human_review import (
    _disposition_payload,
    _qualified_review,
)


def _credentials(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    fragment = parse_qs(parsed.fragment)
    return fragment["token"][0], fragment["session"][0], parsed.port or 0


def _action_credentials(url: str) -> tuple[str, str, str, int]:
    parsed = urlsplit(url)
    fragment = parse_qs(parsed.fragment)
    return (
        fragment["token"][0],
        fragment["session"][0],
        fragment["csrf"][0],
        parsed.port or 0,
    )


def _request(
    port: int,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    body: bytes | None = None,
    timeout: int = 3,
):
    connection = HTTPConnection("127.0.0.1", port, timeout=timeout)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    result = response.status, dict(response.getheaders()), payload
    connection.close()
    return result


def test_loopback_session_serves_assets_and_token_bound_read_model() -> None:
    model = build_read_model()
    with create_review_session_server(model) as server:
        token, session_id, port = _credentials(server.url)
        status, headers, body = _request(port, "/index.html")
        assert status == 200
        assert b"Post-final Review" in body
        assert headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
        assert headers["Cache-Control"] == "no-store"

        status, _, body = _request(
            port,
            f"/api/v1/read-model?session_id={session_id}",
            headers={SESSION_TOKEN_HEADER: token},
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["read_model_fingerprint"] == model.read_model_fingerprint
        assert payload["improvement"]["available"] is False


def test_token_host_origin_and_cross_session_fail_closed_value_free() -> None:
    with create_review_session_server(build_read_model()) as server:
        token, session_id, port = _credentials(server.url)
        path = f"/api/v1/read-model?session_id={session_id}"
        assert _request(port, path)[0] == 401
        assert _request(port, path, headers={SESSION_TOKEN_HEADER: "wrong"})[0] == 401
        assert (
            _request(
                port,
                path,
                headers={SESSION_TOKEN_HEADER: token, "Host": "evil.example"},
            )[0]
            == 403
        )
        assert (
            _request(
                port,
                path,
                headers={SESSION_TOKEN_HEADER: token, "Origin": "https://evil.example"},
            )[0]
            == 403
        )
        status, _, body = _request(
            port,
            "/api/v1/read-model?session_id=review-other",
            headers={SESSION_TOKEN_HEADER: token},
        )
        assert status == 409
        assert b"workspace-1" not in body
        assert token.encode() not in body


def test_new_session_invalidates_old_session_for_same_run() -> None:
    first = create_review_session_server(build_read_model())
    second = create_review_session_server(build_read_model())
    first.start()
    second.start()
    try:
        token, session_id, port = _credentials(first.url)
        status, _, body = _request(
            port,
            f"/api/v1/read-model?session_id={session_id}",
            headers={SESSION_TOKEN_HEADER: token},
        )
        assert status == 410
        assert json.loads(body)["reason_code"] == "review_session_replaced"
    finally:
        first.close()
        second.close()


def test_expiry_and_bounded_nonexistent_command_surface() -> None:
    now = [datetime(2026, 7, 19, tzinfo=timezone.utc)]
    server = create_review_session_server(
        build_read_model(), ttl_seconds=1, clock=lambda: now[0]
    )
    server.start()
    try:
        token, session_id, port = _credentials(server.url)
        now[0] += timedelta(seconds=2)
        assert (
            _request(
                port,
                f"/api/v1/read-model?session_id={session_id}",
                headers={SESSION_TOKEN_HEADER: token},
            )[0]
            == 410
        )
        too_large = {
            "Content-Type": "application/json",
            "Content-Length": str(MAX_JSON_BODY_BYTES + 1),
        }
        assert (
            _request(port, "/api/v1/commands", method="POST", headers=too_large)[0]
            == 413
        )
        assert (
            _request(
                port,
                "/api/v1/commands",
                method="POST",
                headers={"Content-Type": "text/plain"},
                body=b"x",
            )[0]
            == 415
        )
    finally:
        server.close()


def test_launcher_browser_failure_is_ephemeral_headless_fallback() -> None:
    result = launch_review_session(
        build_read_model(),
        browser_open=lambda _url: False,
        static_quality_panel_path="/tmp/static-quality-panel.html",
    )
    try:
        assert result.browser_opened is False
        assert result.reason_code == "review_session_browser_unavailable"
        assert result.runtime_authority is False
        assert result.static_quality_panel_path == "/tmp/static-quality-panel.html"
    finally:
        result.server.close()


def test_static_quality_panel_is_separate_no_js_read_only_projection() -> None:
    html = render_static_quality_panel(build_read_model()).decode("utf-8")
    assert "static-quality-panel-read-only" in html
    assert "Deterministic read-only projection" in html
    assert "<script" not in html.lower()
    assert "<form" not in html.lower()
    assert "<button" not in html.lower()
    assert "AI semantic" not in html
    assert "Improvement Ledger" not in html


@pytest.mark.explicit_e2e
@pytest.mark.timeout(900)
def test_actionable_canonical_session_requires_token_origin_and_csrf(
    tmp_path, monkeypatch
) -> None:
    workspace, _run_id, provider_calls, review, status, finding = _qualified_review(
        tmp_path, monkeypatch
    )
    with pytest.raises(PostFinalReviewError, match="post_final_review_"):
        launch_actionable_review_session(
            workspace,
            status["assessment_result_id"],
            "0" * 64,
            open_browser=False,
        )
    launched = launch_actionable_review_session(
        workspace,
        status["assessment_result_id"],
        status["assessment_result_fingerprint"],
        open_browser=True,
        browser_open=lambda _url: False,
    )
    try:
        token, session_id, csrf, port = _action_credentials(launched.url)
        page_status, headers, body = _request(port, "/index.html")
        assert page_status == 200
        assert b"briefloop.brief_pages.data.v2" in body
        assert headers["Content-Security-Policy"] != CONTENT_SECURITY_POLICY
        assert "'unsafe-inline'" not in headers["Content-Security-Policy"]
        path = f"/api/v1/command?session_id={session_id}"
        command = {
            "schema_version": "briefloop.post_final_review.command.v1",
            "action": "accept",
            "payload": _disposition_payload(
                status,
                finding,
                request_id="loopback-human-accept-1",
                decision="accept",
            ),
        }
        encoded = json.dumps(command).encode("utf-8")
        assert (
            _request(
                port,
                path,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{port}",
                },
                body=encoded,
            )[0]
            == 401
        )
        assert (
            _request(
                port,
                path,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    SESSION_TOKEN_HEADER: token,
                    "Origin": f"http://127.0.0.1:{port}",
                },
                body=encoded,
            )[0]
            == 403
        )
        assert (
            _request(
                port,
                path,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    SESSION_TOKEN_HEADER: token,
                    CSRF_TOKEN_HEADER: csrf,
                    "Origin": "https://example.invalid",
                },
                body=encoded,
            )[0]
            == 403
        )
        accepted_status, _, accepted_body = _request(
            port,
            path,
            method="POST",
            headers={
                "Content-Type": "application/json",
                SESSION_TOKEN_HEADER: token,
                CSRF_TOKEN_HEADER: csrf,
                "Origin": f"http://127.0.0.1:{port}",
            },
            body=encoded,
            timeout=20,
        )
        assert accepted_status == 200
        accepted = json.loads(accepted_body)
        assert accepted["ok"] is True
        assert accepted["result"]["replayed"] is False
        assert (
            accepted["review_status"]["dispositions"][0]["current"]["decision"]
            == "accept"
        )
        assert (
            review.review_status()["dispositions"][0]["current"]["decision"] == "accept"
        )
        assert len(provider_calls) == 9
    finally:
        launched.server.close()
