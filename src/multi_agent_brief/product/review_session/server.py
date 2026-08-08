"""Loopback-only ephemeral server for the post-final Review Session."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
from threading import Event, Lock, Thread
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from .serialization import canonical_json_bytes

from .contracts import (
    REVIEW_SESSION_DESCRIPTOR_SCHEMA_ID,
    PostFinalReviewReadModel,
    ReviewSessionCommand,
    ReviewSessionDescriptor,
)
from .resources import load_asset_provenance, read_review_asset


SESSION_TOKEN_HEADER = "X-BriefLoop-Session-Token"
CSRF_TOKEN_HEADER = "X-BriefLoop-CSRF-Token"
MAX_JSON_BODY_BYTES = 64 * 1024
DEFAULT_SESSION_TTL_SECONDS = 15 * 60
DEFAULT_SESSION_IDLE_SECONDS = 5 * 60
# Actionable sessions are deliberately longer-lived than the dormant
# read-only review session.  A Human may need to read the frozen brief,
# inspect the AI second opinion, and compose an observation before the first
# write.  These remain bounded local loopback sessions; callers can still pass
# explicit values for tests or a stricter embedding.
ACTIONABLE_SESSION_TTL_SECONDS = 60 * 60
ACTIONABLE_SESSION_IDLE_SECONDS = 30 * 60
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_page_csp(page: bytes) -> str:
    """Allow only the exact inline canonical page assets for this session."""

    try:
        text = page.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("review_session_input_invalid") from None

    def hashes(pattern: str) -> str:
        values = re.findall(pattern, text, flags=re.DOTALL)
        if not values:
            raise ValueError("review_session_input_invalid")
        return " ".join(
            "'sha256-"
            + base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode(
                "ascii"
            )
            + "'"
            for value in values
        )

    script_hashes = hashes(r"<script(?: [^>]*)?>(.*?)</script>")
    style_hashes = hashes(r"<style>(.*?)</style>")
    return (
        f"default-src 'none'; script-src {script_hashes}; "
        f"style-src {style_hashes}; img-src 'self' data:; connect-src 'self'; "
        "font-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'; object-src 'none'"
    )


class _SessionRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._by_run: dict[str, str] = {}

    def activate(self, run_id: str, session_id: str) -> None:
        with self._lock:
            self._by_run[run_id] = session_id

    def is_current(self, run_id: str, session_id: str) -> bool:
        with self._lock:
            return self._by_run.get(run_id) == session_id

    def deactivate(self, run_id: str, session_id: str) -> None:
        with self._lock:
            if self._by_run.get(run_id) == session_id:
                self._by_run.pop(run_id, None)


_SESSIONS = _SessionRegistry()


@dataclass
class ReviewSessionServer:
    descriptor: ReviewSessionDescriptor
    url: str
    _token: str = field(repr=False)
    _read_model: PostFinalReviewReadModel | None = field(repr=False)
    _server: ThreadingHTTPServer = field(repr=False)
    _clock: Callable[[], datetime] = field(repr=False)
    _is_expired: Callable[[], bool] = field(repr=False)
    _thread: Thread | None = field(default=None, repr=False)
    _watchdog: Thread | None = field(default=None, repr=False)
    _stopped: Event = field(default_factory=Event, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(
            target=self._server.serve_forever,
            name=f"briefloop-review-{self.descriptor.session_id}",
            daemon=True,
        )
        self._thread.start()
        self._watchdog = Thread(
            target=self._watch_expiry,
            name=f"briefloop-review-watchdog-{self.descriptor.session_id}",
            daemon=True,
        )
        self._watchdog.start()

    def _watch_expiry(self) -> None:
        while not self._stopped.wait(0.25):
            if self._is_expired():
                self._stopped.set()
                _SESSIONS.deactivate(self.descriptor.run_id, self.descriptor.session_id)
                self._server.shutdown()
                return

    def close(self) -> None:
        self._stopped.set()
        _SESSIONS.deactivate(self.descriptor.run_id, self.descriptor.session_id)
        if self._thread is not None and self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._watchdog is not None:
            self._watchdog.join(timeout=2)

    def wait(self) -> None:
        """Keep a CLI-owned session alive until expiry or interruption."""

        if self._thread is None:
            self.start()
        while self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.25)

    def __enter__(self) -> "ReviewSessionServer":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def create_review_session_server(
    read_model: PostFinalReviewReadModel | None,
    *,
    brief_html: bytes | None = None,
    run_id: str | None = None,
    command_handler: Callable[[ReviewSessionCommand], dict[str, object]] | None = None,
    ttl_seconds: int | None = None,
    idle_timeout_seconds: int | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> ReviewSessionServer:
    """Create a bound loopback server; caller explicitly starts or closes it."""

    default_ttl = (
        ACTIONABLE_SESSION_TTL_SECONDS
        if brief_html is not None
        else DEFAULT_SESSION_TTL_SECONDS
    )
    default_idle_timeout = (
        ACTIONABLE_SESSION_IDLE_SECONDS
        if brief_html is not None
        else DEFAULT_SESSION_IDLE_SECONDS
    )
    effective_ttl = default_ttl if ttl_seconds is None else ttl_seconds
    effective_idle_timeout = (
        default_idle_timeout if idle_timeout_seconds is None else idle_timeout_seconds
    )
    if (
        type(effective_ttl) is not int
        or effective_ttl < 1
        or effective_ttl > 24 * 60 * 60
    ):
        raise ValueError("review_session_ttl_invalid")
    if (
        type(effective_idle_timeout) is not int
        or effective_idle_timeout < 1
        or effective_idle_timeout > 24 * 60 * 60
    ):
        raise ValueError("review_session_idle_timeout_invalid")
    if read_model is not None:
        load_asset_provenance()
    if read_model is None:
        if (
            type(brief_html) is not bytes
            or not brief_html
            or type(run_id) is not str
            or not run_id
        ):
            raise ValueError("review_session_input_invalid")
        session_run_id = run_id
        content_security_policy = _canonical_page_csp(brief_html)
    else:
        if brief_html is not None or run_id is not None or command_handler is not None:
            raise ValueError("review_session_input_invalid")
        session_run_id = read_model.context.run_id
        content_security_policy = CONTENT_SECURITY_POLICY
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    session_id = f"review-{secrets.token_hex(16)}"
    created_at = clock()
    expires_at = created_at + timedelta(seconds=effective_ttl)
    activity_lock = Lock()
    last_activity = [created_at]

    def is_expired() -> bool:
        now = clock()
        with activity_lock:
            idle_at = last_activity[0] + timedelta(seconds=effective_idle_timeout)
        return now >= expires_at or now >= idle_at

    def touch() -> None:
        with activity_lock:
            last_activity[0] = clock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "BriefLoopReview/1"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", content_security_policy)
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
            return (
                self.headers.get("Host", "") == f"127.0.0.1:{self.server.server_port}"
            )

        def _valid_origin(self, *, required: bool = False) -> bool:
            origin = self.headers.get("Origin")
            if required and origin is None:
                return False
            return (
                origin is None
                or origin == f"http://127.0.0.1:{self.server.server_port}"
            )

        def _valid_api_request(
            self,
            query: dict[str, list[str]],
            *,
            require_origin: bool = False,
            require_csrf: bool = False,
        ) -> bool:
            now = clock()
            if now.tzinfo is None or is_expired():
                self._reject(HTTPStatus.GONE, "review_session_expired")
                return False
            if not _SESSIONS.is_current(session_run_id, session_id):
                self._reject(HTTPStatus.GONE, "review_session_replaced")
                return False
            if not self._valid_host() or not self._valid_origin(
                required=require_origin
            ):
                self._reject(HTTPStatus.FORBIDDEN, "review_session_origin_invalid")
                return False
            supplied = self.headers.get(SESSION_TOKEN_HEADER, "")
            if not supplied or not hmac.compare_digest(supplied, token):
                self._reject(HTTPStatus.UNAUTHORIZED, "review_session_token_invalid")
                return False
            if query.get("session_id") != [session_id]:
                self._reject(HTTPStatus.CONFLICT, "review_session_identity_mismatch")
                return False
            if require_csrf:
                supplied_csrf = self.headers.get(CSRF_TOKEN_HEADER, "")
                if not supplied_csrf or not hmac.compare_digest(
                    supplied_csrf, csrf_token
                ):
                    self._reject(HTTPStatus.FORBIDDEN, "review_session_csrf_invalid")
                    return False
            touch()
            return True

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            target = urlsplit(self.path)
            if not self._valid_host():
                self._reject(HTTPStatus.FORBIDDEN, "review_session_origin_invalid")
                return
            if target.path == "/api/v1/read-model":
                if read_model is None:
                    self._reject(HTTPStatus.NOT_FOUND, "review_session_route_not_found")
                    return
                query = parse_qs(target.query, keep_blank_values=True)
                if not self._valid_api_request(query):
                    return
                body = canonical_json_bytes(read_model) + b"\n"
                self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
                return
            if target.query:
                self._reject(HTTPStatus.NOT_FOUND, "review_session_route_not_found")
                return
            assets = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/assets/style.css": ("style.css", "text/css; charset=utf-8"),
            }
            selected = assets.get(target.path)
            if brief_html is not None and target.path in {"/", "/index.html"}:
                if is_expired():
                    self._reject(HTTPStatus.GONE, "review_session_expired")
                    return
                touch()
                self._send(
                    HTTPStatus.OK,
                    brief_html,
                    "text/html; charset=utf-8",
                )
                return
            if selected is None:
                self._reject(HTTPStatus.NOT_FOUND, "review_session_route_not_found")
                return
            name, content_type = selected
            if is_expired():
                self._reject(HTTPStatus.GONE, "review_session_expired")
                return
            touch()
            self._send(HTTPStatus.OK, read_review_asset(name), content_type)

        def do_POST(self) -> None:
            if command_handler is None:
                try:
                    dormant_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._reject(HTTPStatus.BAD_REQUEST, "review_session_body_invalid")
                    return
                if dormant_length > MAX_JSON_BODY_BYTES:
                    self._reject(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "review_session_body_too_large",
                    )
                    return
                if self.headers.get("Content-Type") != "application/json":
                    self._reject(
                        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                        "review_session_content_type_invalid",
                    )
                    return
                if dormant_length:
                    self.rfile.read(dormant_length)
                self._reject(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "review_session_command_not_shipped",
                )
                return
            target = urlsplit(self.path)
            query = parse_qs(target.query, keep_blank_values=True)
            if target.path != "/api/v1/command":
                self._reject(
                    HTTPStatus.METHOD_NOT_ALLOWED, "review_session_command_not_shipped"
                )
                return
            if not self._valid_api_request(
                query, require_origin=True, require_csrf=True
            ):
                return
            if self.headers.get("Content-Type") != "application/json":
                self._reject(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "review_session_content_type_invalid",
                )
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reject(HTTPStatus.BAD_REQUEST, "review_session_body_invalid")
                return
            if length > MAX_JSON_BODY_BYTES:
                self._reject(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "review_session_body_too_large"
                )
                return
            if length < 1:
                self._reject(HTTPStatus.BAD_REQUEST, "review_session_body_invalid")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                command = ReviewSessionCommand.model_validate(payload, strict=True)
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValidationError,
                ValueError,
            ):
                self._reject(HTTPStatus.BAD_REQUEST, "review_session_body_invalid")
                return
            try:
                result = command_handler(command)
            except Exception:
                self._reject(HTTPStatus.CONFLICT, "post_final_review_command_failed")
                return
            body = canonical_json_bytes(result) + b"\n"
            self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    descriptor = ReviewSessionDescriptor(
        schema_version=REVIEW_SESSION_DESCRIPTOR_SCHEMA_ID,
        session_id=session_id,
        run_id=session_run_id,
        loopback_host="127.0.0.1",
        port=port,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        created_at=_iso(created_at),
        expires_at=_iso(expires_at),
        ephemeral=True,
        runtime_authority=False,
    )
    _SESSIONS.activate(session_run_id, session_id)
    url = (
        f"http://127.0.0.1:{port}/index.html"
        f"#token={token}&session={session_id}&csrf={csrf_token}"
    )
    return ReviewSessionServer(
        descriptor=descriptor,
        url=url,
        _token=token,
        _read_model=read_model,
        _server=server,
        _clock=clock,
        _is_expired=is_expired,
    )


__all__ = [
    "ACTIONABLE_SESSION_IDLE_SECONDS",
    "ACTIONABLE_SESSION_TTL_SECONDS",
    "CONTENT_SECURITY_POLICY",
    "CSRF_TOKEN_HEADER",
    "DEFAULT_SESSION_IDLE_SECONDS",
    "DEFAULT_SESSION_TTL_SECONDS",
    "MAX_JSON_BODY_BYTES",
    "ReviewSessionServer",
    "SESSION_TOKEN_HEADER",
    "create_review_session_server",
]
