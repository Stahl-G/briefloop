"""Loopback security and lifecycle tests for the init web server."""

from __future__ import annotations

import http.client
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from multi_agent_brief.product.init_web.server import (
    SESSION_TOKEN_HEADER,
    create_init_web_server,
)
from multi_agent_brief.product.init_web.submit import (
    InitWebSubmitter,
    SubmissionError,
)


class _StubSubmitter:
    def __init__(self, response_status: str = "committed") -> None:
        self.calls: list[object] = []
        self._response_status = response_status

    def submit(self, body: object) -> tuple[int, dict[str, object]]:
        self.calls.append(body)
        if self._response_status == "conflict":
            raise SubmissionError("submission_replay_conflict", 409)
        return 200, {
            "ok": True,
            "status": self._response_status,
            "workspace_id": "WS-1",
            "run_id": "RUN-1",
            "transaction_id": "REQ-CX-INIT-x",
            "committed_revision": 1,
            "receipt": {},
        }


def _credentials(url: str) -> tuple[str, str]:
    fragment = parse_qs(urlsplit(url).fragment)
    return fragment["token"][0], fragment["session"][0]


def _request(
    server,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        return response.status, dict(response.getheaders()), payload
    finally:
        connection.close()


def _submit_body(request_id: str = "REQ-TEST01") -> bytes:
    return json.dumps(
        {
            "schema_version": "briefloop.init_web.submission.v1",
            "request_id": request_id,
            "payload": {"workspace_target": "./ws", "human_confirmation": True},
        }
    ).encode("utf-8")


@pytest.fixture()
def server():
    instance = create_init_web_server(
        _StubSubmitter(), exit_on_success=False
    )
    instance.start()
    try:
        yield instance
    finally:
        instance.close()


def test_get_assets_and_security_headers(server) -> None:
    status, headers, body = _request(server, "GET", "/index.html")
    assert status == 200
    assert b"<html" in body
    assert headers.get("Content-Security-Policy", "").startswith("default-src 'none'")
    assert headers.get("Cache-Control") == "no-store"
    assert headers.get("X-Content-Type-Options") == "nosniff"

    status, _headers, body = _request(server, "GET", "/assets/app.js")
    assert status == 200 and b"submit" in body
    status, _headers, _body = _request(server, "GET", "/assets/style.css")
    assert status == 200
    assert server._server.server_address[0] == "127.0.0.1"


def test_get_rejects_bad_host_and_unknown_routes(server) -> None:
    status, _headers, _body = _request(
        server, "GET", "/index.html", headers={"Host": "evil.example"}
    )
    assert status == 403
    status, _headers, _body = _request(server, "GET", "/nope")
    assert status == 404
    status, _headers, _body = _request(server, "GET", "/index.html?x=1")
    assert status == 404


def test_post_requires_token_and_session(server) -> None:
    token, session = _credentials(server.url)
    status, _headers, _body = _request(
        server,
        "POST",
        f"/api/v1/submit?session_id={session}",
        body=_submit_body(),
        headers={"Content-Type": "application/json"},
    )
    assert status == 401

    status, _headers, _body = _request(
        server,
        "POST",
        "/api/v1/submit?session_id=wrong",
        body=_submit_body(),
        headers={
            "Content-Type": "application/json",
            SESSION_TOKEN_HEADER: token,
        },
    )
    assert status == 409


def test_post_rejects_other_routes_and_bad_envelope(server) -> None:
    token, session = _credentials(server.url)
    auth = {"Content-Type": "application/json", SESSION_TOKEN_HEADER: token}

    status, _headers, _body = _request(
        server, "POST", "/api/v1/other", body=_submit_body(), headers=auth
    )
    assert status == 404

    status, _headers, _body = _request(
        server,
        "POST",
        f"/api/v1/submit?session_id={session}",
        body=_submit_body(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            SESSION_TOKEN_HEADER: token,
        },
    )
    assert status == 415

    status, _headers, _body = _request(
        server,
        "POST",
        f"/api/v1/submit?session_id={session}",
        body=b"not-json",
        headers=auth,
    )
    assert status == 400

    status, _headers, _body = _request(
        server,
        "POST",
        f"/api/v1/submit?session_id={session}",
        body=b" " * (64 * 1024 + 1),
        headers=auth,
    )
    assert status == 413


def test_post_success_returns_real_response(server) -> None:
    token, session = _credentials(server.url)
    status, _headers, body = _request(
        server,
        "POST",
        f"/api/v1/submit?session_id={session}",
        body=_submit_body(),
        headers={"Content-Type": "application/json", SESSION_TOKEN_HEADER: token},
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["status"] == "committed"
    assert payload["transaction_id"] == "REQ-CX-INIT-x"


def test_submission_error_maps_to_status_and_reason(server) -> None:
    conflict = create_init_web_server(
        _StubSubmitter(response_status="conflict"), exit_on_success=False
    )
    conflict.start()
    try:
        token, session = _credentials(conflict.url)
        status, _headers, body = _request(
            conflict,
            "POST",
            f"/api/v1/submit?session_id={session}",
            body=_submit_body(),
            headers={"Content-Type": "application/json", SESSION_TOKEN_HEADER: token},
        )
        assert status == 409
        assert json.loads(body)["reason_code"] == "submission_replay_conflict"
    finally:
        conflict.close()


def test_server_exits_on_success_when_configured() -> None:
    instance = create_init_web_server(_StubSubmitter(), exit_on_success=True)
    instance.start()
    token, session = _credentials(instance.url)
    status, _headers, _body = _request(
        instance,
        "POST",
        f"/api/v1/submit?session_id={session}",
        body=_submit_body(),
        headers={"Content-Type": "application/json", SESSION_TOKEN_HEADER: token},
    )
    assert status == 200
    deadline = time.time() + 5
    while time.time() < deadline:
        if instance._thread is not None and not instance._thread.is_alive():
            break
        time.sleep(0.05)
    assert instance._thread is not None and not instance._thread.is_alive()
    instance.close()


def test_server_survives_success_when_exit_disabled(server) -> None:
    token, session = _credentials(server.url)
    auth = {"Content-Type": "application/json", SESSION_TOKEN_HEADER: token}
    for _ in range(2):
        status, _headers, _body = _request(
            server,
            "POST",
            f"/api/v1/submit?session_id={session}",
            body=_submit_body(),
            headers=auth,
        )
        assert status == 200
    assert server._thread is not None and server._thread.is_alive()


def test_real_submitter_end_to_end(tmp_path: Path) -> None:
    instance = create_init_web_server(
        InitWebSubmitter(base_dir=tmp_path), exit_on_success=True
    )
    instance.start()
    try:
        token, session = _credentials(instance.url)
        body = json.dumps(
            {
                "schema_version": "briefloop.init_web.submission.v1",
                "request_id": "REQ-E2E00001",
                "payload": {
                    "workspace_target": "web-ws",
                    "selections": {
                        "company": "ExampleCo",
                        "industry_or_theme": "manufacturing",
                        "task_objective": "Prepare the weekly manufacturing brief.",
                        "audience": "management",
                        "focus_areas": ["operations"],
                        "output_formats": ["markdown"],
                        "web_search_mode": "disabled",
                    },
                    "raw_free_text": "",
                    "discarded": [],
                    "human_confirmation": True,
                },
            }
        ).encode("utf-8")
        status, _headers, raw = _request(
            instance,
            "POST",
            f"/api/v1/submit?session_id={session}",
            body=body,
            headers={"Content-Type": "application/json", SESSION_TOKEN_HEADER: token},
        )
        assert status == 200
        payload = json.loads(raw)
        assert payload["status"] == "committed"
        assert (tmp_path / "web-ws" / "briefloop.db").is_file()
        assert payload["receipt"]["transaction_id"] == payload["transaction_id"]
    finally:
        instance.close()
