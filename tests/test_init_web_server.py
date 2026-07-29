"""Loopback security and lifecycle tests for the init web server."""

from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sqlite3
import stat
import time
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.cli.init_commands import _init_web_wizard
from multi_agent_brief.cli.main import main
from multi_agent_brief.core.env import get_known_env_value
from multi_agent_brief.product.init_web.server import (
    MAX_JSON_BODY_BYTES,
    SESSION_TOKEN_HEADER,
    create_init_web_server,
)
from multi_agent_brief.product.init_web.submit import (
    InitWebSubmitter,
    SubmissionError,
)
from multi_agent_brief.product.projection_platform import (
    supports_retained_directory_publication,
)
from multi_agent_brief.sources.web_search import WebSearchProvider


class _StubSubmitter:
    def __init__(
        self,
        response_status: str = "committed",
        *,
        authorized: bool = True,
        tavily_discovery: bool = False,
    ) -> None:
        self.calls: list[object] = []
        self._response_status = response_status
        self._authorized = authorized
        self._tavily_discovery = tavily_discovery

    def configure_search_secret(
        self, *, session_id: str, body: object
    ) -> dict[str, object]:
        self.calls.append({"session_id": session_id, "secret": body})
        return {
            "ok": True,
            "provider": "tavily",
            "api_key_env": "TAVILY_API_KEY",
            "configured": True,
        }

    def submit(self, body: object) -> tuple[int, dict[str, object]]:
        self.calls.append(body)
        if self._response_status == "conflict":
            raise SubmissionError("submission_replay_conflict", 409)
        response: dict[str, object] = {
            "ok": True,
            "status": self._response_status,
            "workspace_id": "WS-1",
            "run_id": "RUN-1",
            "transaction_id": "REQ-CX-INIT-x",
            "committed_revision": 1,
            "receipt": {},
            "workspace": "/private/secret/workspace",
            "execution_authorized": self._authorized,
            "source_discovery_authorized": self._tavily_discovery,
            "next_action": {
                "action_kind": (
                    "blocked" if self._tavily_discovery else "deterministic"
                ),
                "effect_kind": (
                    "source_discovery_acquisition_unavailable"
                    if self._tavily_discovery
                    else "doctor_check"
                ),
                "reason_code": (
                    "automatic_source_acquisition_not_yet_available"
                    if self._tavily_discovery
                    else "doctor_check_required"
                ),
                "stage_id": "source-discovery" if self._tavily_discovery else None,
                "role_id": None,
            },
            "progress": {
                "reason_code": (
                    "automatic_source_acquisition_not_yet_available"
                    if self._tavily_discovery
                    else "doctor_check_required"
                )
            },
        }
        if self._authorized or self._tavily_discovery:
            response["completion_target"] = "finalized_local"
            response["repair_budget"] = 1
        if self._tavily_discovery:
            response["source_discovery"] = {
                "mode": "pre_provider_authorization",
                "profile": "llm_decide",
                "backend": "tavily",
                "api_key_env": "TAVILY_API_KEY",
            }
            response["search_secret_status"] = "ready"
        return 200, response


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


def _public_web_tavily_body(
    *,
    request_id: str,
    session_id: str,
    workspace_target: str,
    task_objective: str = "Prepare the weekly manufacturing brief.",
) -> dict[str, object]:
    return {
        "schema_version": "briefloop.init_web.submission.v1",
        "request_id": request_id,
        "payload": {
            "workspace_target": workspace_target,
            "selections": {
                "company": "Loopback ExampleCo",
                "industry_or_theme": "manufacturing",
                "task_objective": task_objective,
                "brief_title": "Loopback discovery brief",
                "audience": "management",
                "interface_language": "en",
                "output_language": "en",
                "cadence": "weekly",
                "max_source_age_days": 30,
                "focus_areas": ["operations"],
                "output_formats": ["markdown"],
                "forbidden_sources": [],
                "source_profile": "llm_decide",
                "web_search_mode": "external_api",
                "search_backend": "tavily",
                "output_extent": "balanced",
            },
            "completion_target": "finalized_local",
            "repair_budget": 1,
            "search_secret_session_id": session_id,
            "human_confirmation": True,
        },
    }


def _post_json(
    server,
    *,
    token: str,
    session_id: str,
    path: str,
    body: dict[str, object],
) -> tuple[int, bytes]:
    status, _headers, raw = _request(
        server,
        "POST",
        f"{path}?session_id={session_id}",
        body=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", SESSION_TOKEN_HEADER: token},
    )
    return status, raw


def _assert_workspace_secret_file(path: Path) -> None:
    assert path.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.fixture()
def server():
    instance = create_init_web_server(_StubSubmitter(), exit_on_success=False)
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
    assert b"function hasCurrentOutputContractPreview()" in body
    assert (
        b"STATE.outputContractPreviewKey === currentOutputContractPreviewKey()" in body
    )
    assert b"requestNumber !== STATE.outputContractPreviewRequest" in body
    assert b"else if (!hasCurrentOutputContractPreview())" in body
    assert b"finalized_local" in body
    assert b"payload.repair_budget = 1" in body
    assert b'payload.completion_target = "finalized_local"' in body
    assert b"/api/v1/search-secret" in body
    assert (
        b'web_search_mode: c.source === "public_web" ? "external_api" : "disabled"'
        in body
    )
    assert b'{ "7d": 7, "30d": 30, "90d": 90 }' in body
    assert b"max_source_age_days: maxSourceAgeDays" in body
    assert b'id: "90d"' in body
    assert b'id: "quarter"' not in body
    assert b"custom_window" not in body
    assert b"search_secret_session_id" in body
    assert b"runtime continue --workspace" not in body
    assert b"response.workspace ||" not in body
    assert b"published_at: null" in body
    assert b"observed_filename" in body
    assert b"observed_sha256" in body
    assert b"source_manifest_sha256" in body
    assert b"Self-contained web reading" not in body
    assert b"Delivery & style" not in body
    assert b"This creates and authorizes a local run" in body
    assert b"It does not deliver externally or display the final report" in body
    assert b"no RunExecutionAuthorization" in body
    assert (
        b"This creates a local workspace/run without RunExecutionAuthorization" in body
    )
    assert b'"review_web_boundary"' in body
    assert b'"review_authorized_boundary"' in body
    assert b"Confirm Tavily runtime acquisition (Experimental)" in body
    assert b"Synthetic transport is tested" in body
    assert b"NOT MEASURED" in body
    assert b"automatic discovery enabled" not in body
    assert b"review_statement" not in body
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


def test_search_secret_endpoint_is_token_bound_and_never_echoes_key(server) -> None:
    token, session = _credentials(server.url)
    secret = "tvly-test-secret-123"
    status, _headers, body = _request(
        server,
        "POST",
        f"/api/v1/search-secret?session_id={session}",
        body=json.dumps({"provider": "tavily", "api_key": secret}).encode(),
        headers={
            "Content-Type": "application/json",
            SESSION_TOKEN_HEADER: token,
        },
    )

    assert status == 200
    payload = json.loads(body)
    assert payload == {
        "api_key_env": "TAVILY_API_KEY",
        "configured": True,
        "ok": True,
        "provider": "tavily",
    }
    assert secret.encode() not in body


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
        body=b"x",
        headers={**auth, "Content-Length": str(MAX_JSON_BODY_BYTES + 1)},
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
    assert payload["execution_authorized"] is True
    assert payload["completion_target"] == "finalized_local"
    assert payload["repair_budget"] == 1
    assert "workspace" not in payload
    assert "next_command" not in payload
    assert "/private/secret" not in body.decode("utf-8")
    assert server.outcome is not None
    assert server.outcome.workspace == "/private/secret/workspace"


def test_manual_success_never_claims_authorized_terminal_or_budget() -> None:
    instance = create_init_web_server(
        _StubSubmitter(authorized=False), exit_on_success=False
    )
    instance.start()
    try:
        token, session = _credentials(instance.url)
        status, _headers, body = _request(
            instance,
            "POST",
            f"/api/v1/submit?session_id={session}",
            body=_submit_body(),
            headers={
                "Content-Type": "application/json",
                SESSION_TOKEN_HEADER: token,
            },
        )
        payload = json.loads(body)
        assert status == 200
        assert payload["execution_authorized"] is False
        assert "completion_target" not in payload
        assert "repair_budget" not in payload
        assert b"finalized_local" not in body
    finally:
        instance.close()


def test_public_web_success_reports_pre_provider_discovery_authorization() -> None:
    instance = create_init_web_server(
        _StubSubmitter(authorized=False, tavily_discovery=True),
        exit_on_success=False,
    )
    instance.start()
    try:
        token, session = _credentials(instance.url)
        status, _headers, body = _request(
            instance,
            "POST",
            f"/api/v1/submit?session_id={session}",
            body=_submit_body(),
            headers={
                "Content-Type": "application/json",
                SESSION_TOKEN_HEADER: token,
            },
        )
        payload = json.loads(body)
        assert status == 200
        assert payload["execution_authorized"] is False
        assert payload["source_discovery_authorized"] is True
        assert payload["completion_target"] == "finalized_local"
        assert payload["repair_budget"] == 1
        assert payload["search_secret_status"] == "ready"
        assert payload["source_discovery"] == {
            "mode": "pre_provider_authorization",
            "profile": "llm_decide",
            "backend": "tavily",
            "api_key_env": "TAVILY_API_KEY",
        }
        assert payload["first_action"]["reason_code"] == (
            "automatic_source_acquisition_not_yet_available"
        )
    finally:
        instance.close()


def test_real_loopback_public_web_tavily_replays_before_credential_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = "tvly-loopback-discovery-sentinel"
    request_id = "REQ-LOOPBACK-DISCOVERY-001"
    workspace_target = "loopback-discovery"
    response_bytes: list[bytes] = []
    provider_requests: list[dict[str, object]] = []
    provider_authorizations: list[str | None] = []

    class _TavilyLoopbackHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(length))
            provider_requests.append(payload)
            provider_authorizations.append(self.headers.get("Authorization"))
            response = json.dumps(
                {
                    "results": [
                        {
                            "title": "Durable public result",
                            "url": "https://example.com/public-durable",
                            "content": "discovery summary",
                            "raw_content": "provider-returned durable content",
                            "published_date": "2026-07-29",
                            "score": 0.9,
                        },
                        {
                            "title": "Snippet-only result",
                            "url": "https://example.com/public-snippet",
                            "content": "snippet only",
                            "raw_content": "",
                            "published_date": "2026-07-29",
                            "score": 0.7,
                        },
                    ]
                },
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    provider_server = ThreadingHTTPServer(("127.0.0.1", 0), _TavilyLoopbackHandler)
    provider_thread = Thread(target=provider_server.serve_forever, daemon=True)
    provider_thread.start()
    monkeypatch.setattr(
        "multi_agent_brief.sources.search_backends.tavily.TAVILY_API_URL",
        f"http://127.0.0.1:{provider_server.server_port}/search",
    )

    first = create_init_web_server(InitWebSubmitter(base_dir=tmp_path))
    wizard_errors: list[Exception] = []

    def _submit_via_real_wizard() -> None:
        try:
            token, session_id = _credentials(first.url)
            for _attempt in range(50):
                try:
                    status, raw = _post_json(
                        first,
                        token=token,
                        session_id=session_id,
                        path="/api/v1/search-secret",
                        body={"provider": "tavily", "api_key": sentinel},
                    )
                except OSError:
                    time.sleep(0.01)
                else:
                    break
            else:
                raise AssertionError("real init-web wizard did not accept loopback")
            response_bytes.append(raw)
            assert status == 200
            assert json.loads(raw) == {
                "api_key_env": "TAVILY_API_KEY",
                "configured": True,
                "ok": True,
                "provider": "tavily",
            }
            status, raw = _post_json(
                first,
                token=token,
                session_id=session_id,
                path="/api/v1/submit",
                body=_public_web_tavily_body(
                    request_id=request_id,
                    session_id=session_id,
                    workspace_target=workspace_target,
                ),
            )
            response_bytes.append(raw)
            assert status == 200
        except Exception as exc:  # pragma: no cover - re-raised below
            wizard_errors.append(exc)

    monkeypatch.setattr(
        "multi_agent_brief.product.init_web.create_init_web_server",
        lambda *_args, **_kwargs: first,
    )
    monkeypatch.setattr("webbrowser.open", lambda _url: True)
    client = Thread(target=_submit_via_real_wizard, daemon=True)
    client.start()
    assert _init_web_wizard(SimpleNamespace(port=0)) == 0
    client.join(timeout=2)
    assert not client.is_alive()
    assert wizard_errors == []
    wizard_output = capsys.readouterr().out

    first_payload = json.loads(response_bytes[-1])
    assert first_payload["status"] == "committed"
    assert first_payload["execution_authorized"] is False
    assert first_payload["source_discovery_authorized"] is True
    assert first_payload["search_secret_status"] == "ready"
    assert first.outcome is not None
    assert first.outcome.execution_authorized is False
    assert first.outcome.source_discovery_authorized is True
    assert "workspace" not in first_payload
    assert "receipt" not in first_payload

    workspace = tmp_path / workspace_target
    handoff = f"briefloop runtime continue --workspace {workspace}"
    assert handoff in wizard_output

    db_path = workspace / "briefloop.db"
    env_path = workspace / ".env"
    assert db_path.is_file()
    _assert_workspace_secret_file(env_path)
    assert get_known_env_value("TAVILY_API_KEY", workspace) == sentinel
    with SQLiteControlStore.open(db_path) as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
        receipt = store.load_transaction_receipt(
            head.current_run_id,
            first_payload["transaction_id"],
        )
    assert len(snapshot.run_execution_authorizations) == 0
    assert len(snapshot.run_source_discovery_authorizations) == 1
    assert snapshot.sources == ()
    assert receipt is not None
    assert len(receipt.run_source_discovery_authorizations) == 1
    assert snapshot.store_revision == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
    for artifact in workspace.rglob("*"):
        if artifact.is_file() and artifact != env_path:
            assert sentinel.encode("utf-8") not in artifact.read_bytes()

    assert main(handoff.removeprefix("briefloop ").split()) == 0
    planner = json.loads(capsys.readouterr().out)
    assert planner["status"] == "role_work_required"
    assert planner["current_stage"] == "source-discovery"
    assert provider_requests == []
    with SQLiteControlStore.open(db_path) as store:
        head = store.load_workspace_run_head()
        assert head is not None
        planner_snapshot = store.load_snapshot(head.current_run_id)
    planners = [
        invocation
        for invocation in planner_snapshot.invocations
        if invocation.role_id == "source-planner" and invocation.status == "active"
    ]
    assert len(planners) == 1
    planner_scratch = workspace / "scratch" / planners[0].invocation_id
    (planner_scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: web-search\n",
        encoding="utf-8",
    )

    if not supports_retained_directory_publication():
        before_platform_stop = db_path.read_bytes()
        assert main(handoff.removeprefix("briefloop ").split()) == 0
        platform_stop = json.loads(capsys.readouterr().out)
        assert platform_stop["status"] == "needs_attention"
        assert platform_stop["reason_code"] == "checkout_publication_unsupported"
        assert provider_requests == []
        assert db_path.read_bytes() == before_platform_stop
        provider_server.shutdown()
        provider_thread.join(timeout=2)
        assert not provider_thread.is_alive()
        provider_server.server_close()
        return

    assert main(handoff.removeprefix("briefloop ").split()) == 0
    continuation = json.loads(capsys.readouterr().out)
    assert continuation["status"] == "role_work_required"
    assert continuation["current_stage"] == "scout"
    assert len(provider_requests) == 1
    provider_request = provider_requests[0]
    assert provider_request["query"] == ("Prepare the weekly manufacturing brief.")
    assert provider_request["max_results"] == 5
    assert provider_request["include_raw_content"] == "markdown"
    assert provider_request["auto_parameters"] is False
    assert provider_request["search_depth"] == "basic"
    assert provider_request["days"] == 30
    assert "time_range" not in provider_request
    assert provider_request["include_answer"] is False
    assert "api_key" not in provider_request
    assert provider_authorizations == [f"Bearer {sentinel}"]
    db_bytes = db_path.read_bytes()
    with SQLiteControlStore.open(db_path) as store:
        head = store.load_workspace_run_head()
        assert head is not None
        promoted = store.load_snapshot(head.current_run_id)
        history = store.load_history()
    assert len(promoted.run_source_discovery_authorizations) == 1
    assert len(promoted.run_execution_authorizations) == 1
    assert promoted.store_revision > snapshot.store_revision
    assert len(promoted.sources) == 2
    assert sorted(source.claims_eligible for source in promoted.sources) == [
        False,
        True,
    ]
    promotion = [
        receipt
        for receipt in history.transactions
        if receipt.transaction_type == "source_evidence_intake"
    ]
    assert len(promotion) == 1
    assert len(promotion[0].run_execution_authorizations) == 1
    assert len(promotion[0].run_source_discovery_authorizations) == 1
    assert len(promotion[0].source_ids) == 2
    provider_server.shutdown()
    provider_thread.join(timeout=2)
    assert not provider_thread.is_alive()
    monkeypatch.setattr(
        WebSearchProvider,
        "collect_with_response",
        lambda *_args, **_kwargs: pytest.fail(
            "committed promotion replay must not reopen the provider"
        ),
    )
    assert main(handoff.removeprefix("briefloop ").split()) == 0
    replayed_runtime = json.loads(capsys.readouterr().out)
    assert replayed_runtime["status"] == "role_work_required"
    assert len(provider_requests) == 1
    assert db_path.read_bytes() == db_bytes
    initial_env_mtime = env_path.stat().st_mtime_ns

    ready = create_init_web_server(
        InitWebSubmitter(base_dir=tmp_path), exit_on_success=False
    )
    ready.start()
    try:
        token, session_id = _credentials(ready.url)
        status, raw = _post_json(
            ready,
            token=token,
            session_id=session_id,
            path="/api/v1/submit",
            body=_public_web_tavily_body(
                request_id=request_id,
                session_id=session_id,
                workspace_target=workspace_target,
            ),
        )
        response_bytes.append(raw)
        assert status == 200
        ready_payload = json.loads(raw)
        assert ready_payload["status"] == "replayed"
        assert ready_payload["search_secret_status"] == "ready"
    finally:
        ready.close()
    assert env_path.stat().st_mtime_ns == initial_env_mtime
    assert db_path.read_bytes() == db_bytes

    env_path.unlink()
    missing = create_init_web_server(
        InitWebSubmitter(base_dir=tmp_path), exit_on_success=False
    )
    missing.start()
    try:
        token, session_id = _credentials(missing.url)
        status, raw = _post_json(
            missing,
            token=token,
            session_id=session_id,
            path="/api/v1/submit",
            body=_public_web_tavily_body(
                request_id=request_id,
                session_id=session_id,
                workspace_target=workspace_target,
            ),
        )
        response_bytes.append(raw)
        assert status == 422
        assert json.loads(raw) == {
            "ok": False,
            "reason_code": "submission_search_api_key_required",
            "search_secret_status": "required",
        }
    finally:
        missing.close()
    assert not env_path.exists()
    assert db_path.read_bytes() == db_bytes

    recovery_submitter = InitWebSubmitter(base_dir=tmp_path)
    recovery = create_init_web_server(recovery_submitter, exit_on_success=False)
    recovery.start()
    try:
        token, session_id = _credentials(recovery.url)
        status, raw = _post_json(
            recovery,
            token=token,
            session_id=session_id,
            path="/api/v1/search-secret",
            body={"provider": "tavily", "api_key": sentinel},
        )
        response_bytes.append(raw)
        assert status == 200
        status, raw = _post_json(
            recovery,
            token=token,
            session_id=session_id,
            path="/api/v1/submit",
            body=_public_web_tavily_body(
                request_id=request_id,
                session_id=session_id,
                workspace_target=workspace_target,
            ),
        )
        response_bytes.append(raw)
        assert status == 200
        recovered_payload = json.loads(raw)
        assert recovered_payload["status"] == "replayed"
        assert recovered_payload["search_secret_status"] == "recovered"
        _assert_workspace_secret_file(env_path)
        assert get_known_env_value("TAVILY_API_KEY", workspace) == sentinel
        recovered_env_mtime = env_path.stat().st_mtime_ns
        assert db_path.read_bytes() == db_bytes

        status, raw = _post_json(
            recovery,
            token=token,
            session_id=session_id,
            path="/api/v1/submit",
            body=_public_web_tavily_body(
                request_id=request_id,
                session_id=session_id,
                workspace_target=workspace_target,
            ),
        )
        response_bytes.append(raw)
        assert status == 200
        assert json.loads(raw)["search_secret_status"] == "ready"
        assert env_path.stat().st_mtime_ns == recovered_env_mtime

        env_path.unlink()

        def _secret_effect_must_not_run(**_kwargs: object) -> str:
            raise AssertionError("semantic conflict must precede credential effect")

        monkeypatch.setattr(
            recovery_submitter,
            "_apply_search_secret_effect",
            _secret_effect_must_not_run,
        )
        status, raw = _post_json(
            recovery,
            token=token,
            session_id=session_id,
            path="/api/v1/submit",
            body=_public_web_tavily_body(
                request_id=request_id,
                session_id=session_id,
                workspace_target=workspace_target,
                task_objective="Prepare a changed manufacturing brief.",
            ),
        )
        response_bytes.append(raw)
        assert status == 409
        assert json.loads(raw) == {
            "ok": False,
            "reason_code": "submission_replay_conflict",
        }
    finally:
        recovery.close()

    assert not env_path.exists()
    assert db_path.read_bytes() == db_bytes
    assert len(provider_requests) == 1
    assert all(sentinel.encode("utf-8") not in raw for raw in response_bytes)
    provider_server.server_close()


def test_source_upload_is_session_bound_and_server_hashed(tmp_path: Path) -> None:
    instance = create_init_web_server(
        InitWebSubmitter(base_dir=tmp_path), exit_on_success=False
    )
    instance.start()
    try:
        token, session = _credentials(instance.url)
        content = b"bounded public source\n"
        status, _headers, body = _request(
            instance,
            "POST",
            f"/api/v1/source-upload?session_id={session}",
            body=content,
            headers={
                "Content-Type": "application/octet-stream",
                "X-BriefLoop-Upload-Name": "source.txt",
                SESSION_TOKEN_HEADER: token,
            },
        )
        payload = json.loads(body)
        assert status == 200
        assert payload["filename"] == "source.txt"
        assert payload["byte_count"] == len(content)
        assert len(payload["sha256"]) == 64
        assert payload["upload_handle"].startswith("upload-")

        source_metadata = [
            {
                "source_id": "SRC-INIT-001",
                "expected_content_sha256": payload["sha256"],
                "origin_type": "uploaded_file",
                "acquisition_method": "manual_upload",
                "material_kind": "uploaded_file",
                "provider": None,
                "original_url": None,
                "title": "Public source",
                "publisher": None,
                "published_at": "2026-07-22",
                "retrieved_at": "2026-07-23T00:00:00Z",
                "source_category": "other",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "unknown",
                "raw_underlying_evidence_type": None,
                "document_kind": None,
                "opened_at": None,
                "resolved_at": None,
            }
        ]
        preview_body = json.dumps(
            {
                "source_manifest_mode": "imported",
                "source_metadata": source_metadata,
                "upload_bindings": [
                    {
                        "metadata_index": 0,
                        "upload_handle": payload["upload_handle"],
                    }
                ],
            }
        ).encode("utf-8")
        status, _headers, body = _request(
            instance,
            "POST",
            f"/api/v1/source-manifest-preview?session_id={session}",
            body=preview_body,
            headers={
                "Content-Type": "application/json",
                SESSION_TOKEN_HEADER: token,
            },
        )
        preview = json.loads(body)
        assert status == 200
        assert preview["member_count"] == 1
        assert preview["source_manifest"]["members"][0]["source_id"] == "SRC-INIT-001"
        assert preview["source_preview"][0]["observed_filename"] == "source.txt"
        assert preview["source_preview"][0]["observed_sha256"] == payload["sha256"]
        assert preview["source_preview"][0]["byte_count"] == len(content)
        assert preview["routing_bindings"] == [
            {"metadata_index": 0, "upload_handle": payload["upload_handle"]}
        ]
    finally:
        instance.close()


def test_output_contract_preview_is_session_bound_and_zero_write(server) -> None:
    token, session = _credentials(server.url)
    body = json.dumps({"output_extent": "balanced", "output_language": "en"}).encode(
        "utf-8"
    )
    status, _headers, raw = _request(
        server,
        "POST",
        f"/api/v1/output-contract-preview?session_id={session}",
        body=body,
        headers={"Content-Type": "application/json", SESSION_TOKEN_HEADER: token},
    )
    assert status == 200
    assert json.loads(raw) == {
        "ok": True,
        "output_extent": "balanced",
        "extent_catalog_id": "briefloop.output_extent_catalog.v1",
        "body_length_basis": "reader_body_excluding_source_reference_sections",
        "body_length_unit": "word_equivalent_tokens",
        "resolved_minimum": 600,
        "resolved_maximum": 800,
    }

    status, _headers, raw = _request(
        server,
        "POST",
        f"/api/v1/output-contract-preview?session_id={session}",
        body=json.dumps(
            {"output_extent": "balanced", "output_language": "en", "minimum": 1}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", SESSION_TOKEN_HEADER: token},
    )
    assert status == 422
    assert json.loads(raw) == {
        "ok": False,
        "reason_code": "submission_output_extent_invalid",
    }


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
                        "output_extent": "balanced",
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
        assert "receipt" not in payload
        assert "workspace" not in payload
        assert "next_command" not in payload
        assert instance.outcome is not None
        assert instance.outcome.workspace == str(tmp_path / "web-ws")
    finally:
        instance.close()
