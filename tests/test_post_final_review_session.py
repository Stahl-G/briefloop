from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from urllib.parse import parse_qs, urlsplit

from multi_agent_brief.product.review_session.launcher import launch_review_session
from multi_agent_brief.product.review_session.server import (
    CONTENT_SECURITY_POLICY,
    MAX_JSON_BODY_BYTES,
    SESSION_TOKEN_HEADER,
    create_review_session_server,
)
from multi_agent_brief.product.review_session.static_qp import (
    render_static_quality_panel,
)
from tests.test_post_final_review_contracts import build_read_model


def _credentials(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    fragment = parse_qs(parsed.fragment)
    return fragment["token"][0], fragment["session"][0], parsed.port or 0


def _request(port: int, path: str, *, headers: dict[str, str] | None = None, method: str = "GET", body: bytes | None = None):
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
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
        assert _request(port, path, headers={SESSION_TOKEN_HEADER: token, "Host": "evil.example"})[0] == 403
        assert _request(port, path, headers={SESSION_TOKEN_HEADER: token, "Origin": "https://evil.example"})[0] == 403
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
    server = create_review_session_server(build_read_model(), ttl_seconds=1, clock=lambda: now[0])
    server.start()
    try:
        token, session_id, port = _credentials(server.url)
        now[0] += timedelta(seconds=2)
        assert _request(port, f"/api/v1/read-model?session_id={session_id}", headers={SESSION_TOKEN_HEADER: token})[0] == 410
        too_large = {"Content-Type": "application/json", "Content-Length": str(MAX_JSON_BODY_BYTES + 1)}
        assert _request(port, "/api/v1/commands", method="POST", headers=too_large)[0] == 413
        assert _request(port, "/api/v1/commands", method="POST", headers={"Content-Type": "text/plain"}, body=b"x")[0] == 415
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
