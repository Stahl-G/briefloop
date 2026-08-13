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
from multi_agent_brief.product.review_session.contracts import (
    READER_REVIEW_SELECTION_SCHEMA_ID,
)
from multi_agent_brief.product.post_final_assessment import PostFinalAssessmentError
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


def _page_data(
    *,
    status: str,
    options: list[dict[str, object]] | None = None,
    selected: tuple[str, str] | None = None,
    run_action_available: bool = False,
) -> dict[str, object]:
    return {
        "schema_version": "briefloop.brief_pages.data.v2",
        "workspace": {"run_id": "run-reader-review-ui-1"},
        "semantic": {
            "status": status,
            "compatible_result_options": options or [],
            "selected_result_id": selected[0] if selected else None,
            "selected_result_fingerprint": selected[1] if selected else None,
            "selection_required": status == "selection_required",
            "run_action_available": run_action_available,
        },
        "improvement": {"next_run_consumption": "explicit_opt_in_successor_only"},
    }


def _post_action_command(
    launched,
    command: dict[str, object],
) -> tuple[int, dict[str, object]]:
    token, session_id, csrf, port = _action_credentials(launched.url)
    status, _, body = _request(
        port,
        f"/api/v1/command?session_id={session_id}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            SESSION_TOKEN_HEADER: token,
            CSRF_TOKEN_HEADER: csrf,
            "Origin": f"http://127.0.0.1:{port}",
        },
        body=json.dumps(command).encode("utf-8"),
    )
    return status, json.loads(body)


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


def test_actionable_session_defaults_leave_human_time_to_review_before_expiry() -> None:
    now = [datetime(2026, 7, 19, tzinfo=timezone.utc)]
    page = (
        b"<html><head><style>body{color:black}</style></head>"
        b"<body><script>0</script></body></html>"
    )
    server = create_review_session_server(
        None,
        brief_html=page,
        run_id="run-actionable-expiry-1",
        command_handler=lambda _command: {"ok": True},
        clock=lambda: now[0],
    )
    try:
        created = datetime.fromisoformat(
            server.descriptor.created_at.replace("Z", "+00:00")
        )
        expires = datetime.fromisoformat(
            server.descriptor.expires_at.replace("Z", "+00:00")
        )
        assert expires - created == timedelta(hours=1)
        server.start()
        _token, _session_id, port = _credentials(server.url)
        now[0] += timedelta(minutes=6)
        assert _request(port, "/index.html")[0] == 200
        now[0] += timedelta(minutes=30)
        expired_status, _, expired_body = _request(port, "/index.html")
        assert expired_status == 410
        assert json.loads(expired_body)["reason_code"] == "review_session_expired"
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


def test_actionable_command_requires_origin_token_csrf_and_strict_selection() -> None:
    observed: list[dict[str, object]] = []

    def handle(command) -> dict[str, object]:
        observed.append(command.model_dump(mode="json", exclude_unset=False))
        return {"ok": True, "reason_code": "reader_review_selection_refreshed"}

    page = b"<html><head><style>body{color:black}</style></head><body><script>0</script></body></html>"
    with create_review_session_server(
        None,
        brief_html=page,
        run_id="run-reader-review-1",
        command_handler=handle,
    ) as server:
        token, session_id, csrf, port = _action_credentials(server.url)
        path = f"/api/v1/command?session_id={session_id}"
        command = {
            "schema_version": "briefloop.post_final_review.command.v1",
            "action": "select_result",
            "payload": {
                "schema_version": READER_REVIEW_SELECTION_SCHEMA_ID,
                "assessment_result_id": "assessment-result-1",
                "assessment_result_fingerprint": "a" * 64,
            },
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
                    CSRF_TOKEN_HEADER: csrf,
                    "Origin": "https://example.invalid",
                },
                body=encoded,
            )[0]
            == 403
        )

        malformed = json.loads(encoded)
        malformed["payload"]["lineage_id"] = "dom-is-not-authority"
        status, _, response = _request(
            port,
            path,
            method="POST",
            headers={
                "Content-Type": "application/json",
                SESSION_TOKEN_HEADER: token,
                CSRF_TOKEN_HEADER: csrf,
                "Origin": f"http://127.0.0.1:{port}",
            },
            body=json.dumps(malformed).encode("utf-8"),
        )
        assert status == 400
        assert json.loads(response)["reason_code"] == "review_session_body_invalid"
        assert observed == []

        unconfirmed = {
            "schema_version": "briefloop.post_final_review.command.v1",
            "action": "run_reader_review",
            "payload": {
                "schema_version": "briefloop.reader_review_assessment_input.v1",
                "human_actor_id": "local-human-reviewer",
                "human_request_id": "reader-review-unconfirmed-1",
                "disclosure_confirmed": False,
                "messages_endpoint": "https://messages.example.invalid/v1/messages",
                "requested_model_id": "opaque-model",
                "model_version": "opaque-version",
                "expected_model_identity": "expected-opaque-model",
                "public_safe_egress_attested": True,
                "cost_status": "not_measured",
            },
        }
        status, _, response = _request(
            port,
            path,
            method="POST",
            headers={
                "Content-Type": "application/json",
                SESSION_TOKEN_HEADER: token,
                CSRF_TOKEN_HEADER: csrf,
                "Origin": f"http://127.0.0.1:{port}",
            },
            body=json.dumps(unconfirmed).encode("utf-8"),
        )
        assert status == 400
        assert json.loads(response)["reason_code"] == "review_session_body_invalid"
        assert observed == []

        status, _, response = _request(
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
        )
        assert status == 200
        assert json.loads(response)["ok"] is True
        assert len(observed) == 1


def test_actionable_launcher_runs_once_and_returns_same_page_store_projection(
    tmp_path, monkeypatch
) -> None:
    state = {"completed": False}
    service_payloads: list[dict[str, object]] = []
    build_selections: list[tuple[str | None, str | None]] = []
    result_id = "assessment-result-reader-review-1"
    fingerprint = "a" * 64

    def build_pages(
        _workspace,
        *,
        assessment_result_id=None,
        assessment_result_fingerprint=None,
        **_kwargs,
    ):
        build_selections.append((assessment_result_id, assessment_result_fingerprint))
        if state["completed"]:
            return _page_data(
                status="finding_returned",
                options=[
                    {
                        "assessment_result_id": result_id,
                        "assessment_result_fingerprint": fingerprint,
                    }
                ],
                selected=(result_id, fingerprint),
            )
        return _page_data(status="not_assessed", run_action_available=True)

    class AssessmentService:
        def __init__(self, workspace) -> None:
            assert workspace == tmp_path.resolve()

        def run_reader_review(self, payload):
            service_payloads.append(dict(payload))
            state["completed"] = True
            return {
                "ok": True,
                "reason_code": "reader_review_completed",
                "reason_codes": ["reader_review_completed"],
                "user_status": "finding_returned",
                "replayed": False,
            }

    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.builder.build_brief_pages_data",
        build_pages,
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.render.render_brief_pages_html",
        lambda _data: (
            b"<html><head><style>body{color:black}</style></head>"
            b"<body><script>0</script></body></html>"
        ),
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.post_final_assessment.PostFinalAssessmentService",
        AssessmentService,
    )

    launched = launch_actionable_review_session(tmp_path, open_browser=False)
    try:
        assert launched.user_status == "not_assessed"
        assert launched.run_action_available is True
        assert service_payloads == []
        command = {
            "schema_version": "briefloop.post_final_review.command.v1",
            "action": "run_reader_review",
            "payload": {
                "schema_version": "briefloop.reader_review_assessment_input.v1",
                "human_actor_id": "local-human-reviewer",
                "human_request_id": "reader-review-request-1",
                "disclosure_confirmed": True,
                "messages_endpoint": "https://messages.example.invalid/v1/messages",
                "requested_model_id": "opaque-model",
                "model_version": "opaque-version",
                "expected_model_identity": "expected-opaque-model",
                "public_safe_egress_attested": True,
                "cost_status": "not_measured",
            },
        }
        status, response = _post_action_command(launched, command)
        assert status == 200
        assert response["ok"] is True
        assert response["page_data"]["semantic"]["status"] == "finding_returned"
        assert response["page_data"]["semantic"]["selected_result_id"] == result_id
        assert len(service_payloads) == 1
        assert "api_key" not in service_payloads[0]

        refresh_status, refreshed = _post_action_command(
            launched,
            {
                "schema_version": "briefloop.post_final_review.command.v1",
                "action": "refresh",
                "payload": {"schema_version": "briefloop.post_final_review.refresh.v1"},
            },
        )
        assert refresh_status == 200
        assert refreshed["page_data"]["semantic"]["status"] == "finding_returned"
        assert len(service_payloads) == 1
        assert build_selections[-1] == (result_id, fingerprint)
    finally:
        launched.server.close()


def test_actionable_launcher_requires_human_selection_for_multiple_results(
    tmp_path, monkeypatch
) -> None:
    options = [
        {
            "assessment_result_id": "assessment-result-reader-review-1",
            "assessment_result_fingerprint": "a" * 64,
        },
        {
            "assessment_result_id": "assessment-result-reader-review-2",
            "assessment_result_fingerprint": "b" * 64,
        },
    ]
    selections: list[tuple[str | None, str | None]] = []

    def build_pages(
        _workspace,
        *,
        assessment_result_id=None,
        assessment_result_fingerprint=None,
        **_kwargs,
    ):
        selections.append((assessment_result_id, assessment_result_fingerprint))
        if assessment_result_id is None:
            return _page_data(status="selection_required", options=options)
        return _page_data(
            status="no_finding_returned_in_completed_supported_checks",
            options=options,
            selected=(assessment_result_id, assessment_result_fingerprint),
        )

    class AssessmentService:
        def __init__(self, _workspace) -> None:
            pass

        def run_reader_review(self, _payload):
            raise AssertionError("selection cannot call the provider service")

    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.builder.build_brief_pages_data",
        build_pages,
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.render.render_brief_pages_html",
        lambda _data: (
            b"<html><head><style>body{color:black}</style></head>"
            b"<body><script>0</script></body></html>"
        ),
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.post_final_assessment.PostFinalAssessmentService",
        AssessmentService,
    )

    launched = launch_actionable_review_session(tmp_path, open_browser=False)
    try:
        assert launched.user_status == "selection_required"
        assert launched.compatible_result_count == 2
        selected = options[1]
        status, response = _post_action_command(
            launched,
            {
                "schema_version": "briefloop.post_final_review.command.v1",
                "action": "select_result",
                "payload": {
                    "schema_version": READER_REVIEW_SELECTION_SCHEMA_ID,
                    "assessment_result_id": selected["assessment_result_id"],
                    "assessment_result_fingerprint": selected[
                        "assessment_result_fingerprint"
                    ],
                },
            },
        )
        assert status == 200
        assert response["ok"] is True
        assert response["page_data"]["semantic"]["status"] == (
            "no_finding_returned_in_completed_supported_checks"
        )
        assert selections[-1] == (
            selected["assessment_result_id"],
            selected["assessment_result_fingerprint"],
        )
    finally:
        launched.server.close()


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
    with pytest.raises(
        PostFinalAssessmentError,
        match="post_final_assessment_selection_invalid",
    ):
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
        assert b"briefloop.brief_pages.data.v3" in body
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
        assert len(provider_calls) == 2
    finally:
        launched.server.close()
