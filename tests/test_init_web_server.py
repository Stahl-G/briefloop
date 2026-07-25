"""Loopback security and lifecycle tests for the init web server."""

from __future__ import annotations

import http.client
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from multi_agent_brief.product.init_web.server import (
    MAX_JSON_BODY_BYTES,
    SESSION_TOKEN_HEADER,
    create_init_web_server,
)
from multi_agent_brief.product.init_web.submit import (
    InitWebSubmitter,
    SubmissionError,
)


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
            "next_action": {
                "action_kind": "deterministic",
                "effect_kind": "doctor_check",
                "reason_code": "doctor_check_required",
                "stage_id": None,
                "role_id": None,
            },
            "progress": {"reason_code": "doctor_check_required"},
        }
        if self._authorized:
            response["completion_target"] = "finalized_local"
            response["repair_budget"] = 1
        else:
            response["completion_target"] = None
            response["repair_budget"] = None
        if self._tavily_discovery:
            response["source_discovery"] = {
                "mode": "automatic",
                "profile": "llm_decide",
                "backend": "tavily",
                "api_key_env": "TAVILY_API_KEY",
            }
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
    assert b"function hasCurrentOutputContractPreview()" in body
    assert b"STATE.outputContractPreviewKey === currentOutputContractPreviewKey()" in body
    assert b"requestNumber !== STATE.outputContractPreviewRequest" in body
    assert b"else if (!hasCurrentOutputContractPreview())" in body
    assert b"finalized_local" in body
    assert b"payload.repair_budget = 1" in body
    assert b'/api/v1/search-secret' in body
    assert (
        b'web_search_mode: c.source === "public_web" ? "external_api" : "disabled"'
        in body
    )
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
    assert b"This creates a local workspace/run without RunExecutionAuthorization" in body
    assert b'"review_web_boundary"' in body
    assert b'"review_authorized_boundary"' in body
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


def test_public_web_success_reports_automatic_tavily_discovery() -> None:
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
        assert payload["source_discovery"] == {
            "mode": "automatic",
            "profile": "llm_decide",
            "backend": "tavily",
            "api_key_env": "TAVILY_API_KEY",
        }
    finally:
        instance.close()


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
    body = json.dumps(
        {"output_extent": "balanced", "output_language": "en"}
    ).encode("utf-8")
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
