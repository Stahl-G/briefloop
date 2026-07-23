"""One-shot loopback server for the init web wizard (stdlib only).

Mirrors the Review Session security posture: random port, per-session token in
the URL fragment, strict Host/Origin checks, CSP/no-store/nosniff headers,
body cap, and a single accepted POST route.  The server performs no authority
action itself; submissions go through ``InitWebSubmitter``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
from threading import Lock, Thread
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from multi_agent_brief.product.review_session.serialization import canonical_json_bytes

from .submit import InitWebSubmitter, SubmissionError, preview_output_contract

SESSION_TOKEN_HEADER = "X-BriefLoop-Session-Token"
MAX_JSON_BODY_BYTES = 64 * 1024
MAX_UPLOAD_BODY_BYTES = 16 * 1024 * 1024
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
)
_ROOT = "static"
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/style.css": ("style.css", "text/css; charset=utf-8"),
}


class InitWebError(ValueError):
    """Raised when static assets or provenance fail closed."""


def _verify_assets() -> None:
    raw = files(__package__).joinpath(_ROOT, "provenance.json").read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise InitWebError("init_web_provenance_invalid") from None
    production = payload.get("production_assets") if isinstance(payload, dict) else None
    expected_names = {
        f"{name}_sha256" for name, _kind in _ASSETS.values()
    } | {"THIRD_PARTY_NOTICES.txt_sha256"}
    if not isinstance(production, dict) or set(production) != expected_names:
        raise InitWebError("init_web_provenance_invalid")
    for key, expected in production.items():
        name = key[: -len("_sha256")]
        actual = files(__package__).joinpath(_ROOT, name).read_bytes()
        if expected != hashlib.sha256(actual).hexdigest():
            raise InitWebError("init_web_asset_hash_mismatch")


@dataclass
class InitWebSubmissionOutcome:
    """In-memory handoff to the already-active initiating controller."""

    workspace: str
    workspace_id: str
    run_id: str
    transaction_id: str
    status: str


@dataclass
class InitWebServer:
    session_id: str
    url: str
    port: int
    _token: str = field(repr=False)
    _server: ThreadingHTTPServer = field(repr=False)
    _cleanup: Callable[[], None] = field(repr=False)
    _outcome_getter: Callable[[], InitWebSubmissionOutcome | None] = field(
        repr=False
    )
    _thread: Thread | None = field(default=None, repr=False)

    def start(self) -> None:
        if self._thread is None:
            self._thread = Thread(
                target=self._server.serve_forever,
                name=f"briefloop-init-web-{self.session_id}",
                daemon=True,
            )
            self._thread.start()

    def serve_forever(self) -> None:
        self._server.serve_forever()

    @property
    def outcome(self) -> InitWebSubmissionOutcome | None:
        return self._outcome_getter()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._cleanup()

    def __enter__(self) -> "InitWebServer":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def create_init_web_server(
    submitter: InitWebSubmitter,
    *,
    port: int = 0,
    exit_on_success: bool = True,
) -> InitWebServer:
    """Bind the one-shot loopback server; caller starts or serves it."""

    _verify_assets()
    token = secrets.token_urlsafe(32)
    session_id = f"init-{secrets.token_hex(16)}"
    outcome_lock = Lock()
    successful_outcome: InitWebSubmissionOutcome | None = None

    def _outcome() -> InitWebSubmissionOutcome | None:
        with outcome_lock:
            return successful_outcome

    def _friendly_response(response: dict[str, Any]) -> dict[str, Any]:
        action = response.get("next_action")
        progress = response.get("progress")
        action_payload = action if isinstance(action, dict) else {}
        progress_payload = progress if isinstance(progress, dict) else {}
        return {
            "ok": True,
            "status": response.get("status"),
            "workspace_id": response.get("workspace_id"),
            "run_id": response.get("run_id"),
            "transaction_id": response.get("transaction_id"),
            "completion_target": "finalized_local",
            "repair_budget": 1,
            "first_action": {
                "action_kind": action_payload.get("action_kind"),
                "effect_kind": action_payload.get("effect_kind"),
                "reason_code": action_payload.get("reason_code"),
                "stage_id": action_payload.get("stage_id"),
                "role_id": action_payload.get("role_id"),
            },
            "progress": {
                "status": "initialized",
                "current_stage": progress_payload.get("current_stage"),
                "current_role": progress_payload.get("current_role"),
                "reason_code": progress_payload.get("reason_code"),
            },
        }

    def _shutdown_soon() -> None:
        import threading

        threading.Timer(0.2, server.shutdown).start()

    class Handler(BaseHTTPRequestHandler):
        server_version = "BriefLoopInitWeb/1"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self._headers(status, content_type, len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

        def _reject(self, status: HTTPStatus, reason: str) -> None:
            body = canonical_json_bytes({"ok": False, "reason_code": reason}) + b"\n"
            self._send(status, body, "application/json; charset=utf-8")

        def _valid_host(self) -> bool:
            return self.headers.get("Host", "") == f"127.0.0.1:{self.server.server_port}"

        def _valid_origin(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin == f"http://127.0.0.1:{self.server.server_port}"

        def _authorized(self, query: dict[str, list[str]]) -> bool:
            supplied = self.headers.get(SESSION_TOKEN_HEADER, "")
            if not supplied or not hmac.compare_digest(supplied, token):
                self._reject(HTTPStatus.UNAUTHORIZED, "init_web_token_invalid")
                return False
            if query.get("session_id") != [session_id]:
                self._reject(HTTPStatus.CONFLICT, "init_web_identity_mismatch")
                return False
            return True

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            if not self._valid_host():
                self._reject(HTTPStatus.FORBIDDEN, "init_web_origin_invalid")
                return
            target = urlsplit(self.path)
            if target.query:
                self._reject(HTTPStatus.NOT_FOUND, "init_web_route_not_found")
                return
            selected = _ASSETS.get(target.path)
            if selected is None:
                self._reject(HTTPStatus.NOT_FOUND, "init_web_route_not_found")
                return
            name, content_type = selected
            body = files(__package__).joinpath(_ROOT, name).read_bytes()
            self._send(HTTPStatus.OK, body, content_type)

        def do_POST(self) -> None:
            if not self._valid_host() or not self._valid_origin():
                self._reject(HTTPStatus.FORBIDDEN, "init_web_origin_invalid")
                return
            target = urlsplit(self.path)
            if target.path not in {
                "/api/v1/submit",
                "/api/v1/output-contract-preview",
                "/api/v1/source-manifest-preview",
                "/api/v1/source-upload",
            }:
                self._reject(HTTPStatus.NOT_FOUND, "init_web_route_not_found")
                return
            query = parse_qs(target.query, keep_blank_values=True)
            if not self._authorized(query):
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reject(HTTPStatus.BAD_REQUEST, "init_web_body_invalid")
                return
            maximum = (
                MAX_UPLOAD_BODY_BYTES
                if target.path == "/api/v1/source-upload"
                else MAX_JSON_BODY_BYTES
            )
            if length < 1 or length > maximum:
                self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "init_web_body_too_large")
                return
            if target.path == "/api/v1/source-upload":
                if self.headers.get("Content-Type") != "application/octet-stream":
                    self._reject(
                        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                        "init_web_content_type_invalid",
                    )
                    return
                filename = self.headers.get("X-BriefLoop-Upload-Name", "")
                try:
                    response = submitter.stage_upload(
                        session_id=session_id,
                        filename=filename,
                        stream=self.rfile,
                        declared_length=length,
                    )
                except (AttributeError, SubmissionError) as exc:
                    reason = (
                        exc.error_code
                        if isinstance(exc, SubmissionError)
                        else "init_web_source_upload_unsupported"
                    )
                    status = (
                        exc.http_status
                        if isinstance(exc, SubmissionError)
                        else HTTPStatus.UNPROCESSABLE_ENTITY
                    )
                    self._reject(HTTPStatus(status), reason)
                    return
                payload = canonical_json_bytes(response) + b"\n"
                self._send(HTTPStatus.OK, payload, "application/json; charset=utf-8")
                return
            if self.headers.get("Content-Type") != "application/json":
                self._reject(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "init_web_content_type_invalid")
                return
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                self._reject(HTTPStatus.BAD_REQUEST, "init_web_body_invalid")
                return
            try:
                if target.path == "/api/v1/output-contract-preview":
                    status, response = HTTPStatus.OK, preview_output_contract(body)
                elif target.path == "/api/v1/source-manifest-preview":
                    status, response = HTTPStatus.OK, submitter.preview_source_manifest(
                        session_id=session_id,
                        body=body,
                    )
                else:
                    status, response = submitter.submit(body)
            except SubmissionError as exc:
                status, response = exc.http_status, {
                    "ok": False,
                    "reason_code": exc.error_code,
                }
            if (
                target.path == "/api/v1/submit"
                and status == HTTPStatus.OK
                and response.get("status") in {"committed", "replayed"}
            ):
                workspace = response.get("workspace")
                if not isinstance(workspace, str):
                    self._reject(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "init_web_handoff_unavailable",
                    )
                    return
                outcome = InitWebSubmissionOutcome(
                    workspace=workspace,
                    workspace_id=str(response.get("workspace_id")),
                    run_id=str(response.get("run_id")),
                    transaction_id=str(response.get("transaction_id")),
                    status=str(response.get("status")),
                )
                with outcome_lock:
                    nonlocal successful_outcome
                    successful_outcome = outcome
                response = _friendly_response(response)
            payload = canonical_json_bytes(response) + b"\n"
            self._send(HTTPStatus(status), payload, "application/json; charset=utf-8")
            if (
                exit_on_success
                and status == HTTPStatus.OK
                and response.get("status") in {"committed", "replayed"}
            ):
                _shutdown_soon()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    bound_port = server.server_port
    url = f"http://127.0.0.1:{bound_port}/index.html#token={token}&session={session_id}"
    return InitWebServer(
        session_id=session_id,
        url=url,
        port=bound_port,
        _token=token,
        _server=server,
        _cleanup=getattr(submitter, "close", lambda: None),
        _outcome_getter=_outcome,
    )


__all__ = [
    "CONTENT_SECURITY_POLICY",
    "InitWebError",
    "InitWebServer",
    "InitWebSubmissionOutcome",
    "MAX_JSON_BODY_BYTES",
    "MAX_UPLOAD_BODY_BYTES",
    "SESSION_TOKEN_HEADER",
    "create_init_web_server",
]
