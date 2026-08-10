"""Tavily search backend using the Tavily Search API.

Uses Python stdlib urllib.request — no mandatory Tavily SDK dependency.
Reads API key from env var TAVILY_API_KEY by default, or a custom env var
specified via api_key_env in config.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from multi_agent_brief.contracts.v2 import (
        TavilyAcquisitionExchange,
        TavilyExtractBatchExchange,
        TavilyExtractUrlOutcome,
        TavilySearchTaskExchange,
        TavilyTaskAcquisitionStatus,
    )
from multi_agent_brief.sources.search_backends.base import (
    SearchBackend,
    SearchBackendError,
    SearchResponse,
    SearchResult,
)
from multi_agent_brief.sources.search_backends.capabilities import (
    TAVILY_CAPABILITIES,
    SearchBackendCapabilities,
)
from multi_agent_brief.sources.tavily_acquisition import (
    tavily_search_discovery_projection,
)

TAVILY_API_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_API_URL = "https://api.tavily.com/extract"
DEFAULT_API_KEY_ENV = "TAVILY_API_KEY"
TAVILY_REQUEST_BODY_BYTE_CAP = 64 * 1024
TAVILY_SINGLE_HTTP_RESPONSE_BYTE_CAP = 2_400_000
TAVILY_MULTI_EXCHANGE_RESPONSE_BYTE_CAP = 4 * 1024 * 1024
TAVILY_MULTI_ACQUISITION_BUNDLE_BYTE_CAP = 256 * 1024 * 1024
TAVILY_TIMEOUT_SECONDS = 30
_ORIGINAL_URLOPEN = urllib.request.urlopen


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Retain a 3xx response as the one bounded exchange; never follow it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _open_no_redirect(request: urllib.request.Request, *, timeout: int):
    """Use the no-redirect product transport while retaining test injection."""

    if urllib.request.urlopen is not _ORIGINAL_URLOPEN:
        return urllib.request.urlopen(request, timeout=timeout)
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _transport_error_class(
    error: BaseException,
) -> str:
    """Return a coarse, value-free class for one failed HTTP attempt."""

    # urllib commonly wraps the useful exception in URLError.reason.  Inspect
    # only exception types; never persist or expose their messages.
    candidates = (error, getattr(error, "reason", None))
    names = {type(item).__name__.lower() for item in candidates if item is not None}
    if any(
        isinstance(item, PermissionError) for item in candidates if item is not None
    ):
        return "network_permission_denied"
    if any("proxy" in name for name in names):
        return "proxy"
    if any(
        isinstance(item, (TimeoutError, socket.timeout))
        for item in candidates
        if item is not None
    ):
        return "timeout"
    if any(isinstance(item, ssl.SSLError) for item in candidates if item is not None):
        return "tls"
    if any(
        isinstance(item, socket.gaierror) for item in candidates if item is not None
    ):
        return "dns"
    if any(
        isinstance(item, ConnectionError) for item in candidates if item is not None
    ):
        return "connect"
    return "other"


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_ISO_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?\Z"
)
_RFC_DATETIME = re.compile(
    r"(?:(?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun), )?"
    r"\d{2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"\d{4} \d{2}:\d{2}:\d{2} (?:GMT|[+-]\d{4})\Z"
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_object_without_duplicate_keys(payload: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def invalid_constant(_value: str):
        raise ValueError("non-finite JSON number")

    decoded = json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=invalid_constant,
    )
    if type(decoded) is not dict:
        raise ValueError("invalid JSON object")
    stack: list[Any] = [decoded]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            value.encode("utf-8")
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return decoded


def _response_contains_credential(
    payload: bytes,
    *,
    api_key: str,
    api_key_sha256: str,
) -> bool:
    """Catch exact and JSON-escaped credential echoes without rendering them."""

    api_key_bytes = api_key.encode("utf-8")
    digest_bytes = api_key_sha256.encode("ascii")
    if api_key_bytes in payload or digest_bytes in payload.lower():
        return True

    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=lambda pairs: pairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        # Invalid JSON carrying escape sequences cannot be proved secret-free.
        return b"\\u" in payload.lower()

    stack = [decoded]
    visited = 0
    while stack:
        value = stack.pop()
        visited += 1
        if visited > 100_000:
            return True
        if isinstance(value, str):
            if api_key in value or api_key_sha256 in value.lower():
                return True
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
    return False


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _TavilyResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False)

    title: str
    url: str
    content: str
    raw_content: str | None = None
    published_date: str | None = None
    score: float | int | None = None

    @field_validator("url")
    @classmethod
    def _public_http_url(cls, value: str) -> str:
        if value != value.strip() or not value or len(value) > 2048:
            raise ValueError("invalid result URL")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("invalid result URL")
        return value


class _TavilyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    results: list[_TavilyResult] = Field(max_length=100)


class _TavilyExtractResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    url: str
    raw_content: str | None = None

    _public_http_url = field_validator("url")(_TavilyResult._public_http_url.__func__)


class _TavilyExtractFailedResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    url: str

    _public_http_url = field_validator("url")(_TavilyResult._public_http_url.__func__)


class _TavilyExtractResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    results: list[_TavilyExtractResult] = Field(max_length=20)
    failed_results: list[_TavilyExtractFailedResult] = Field(max_length=20)


def _extract_domain(url: str) -> str:
    """Extract domain from URL, safe for malformed URLs."""
    try:
        parts = url.split("/")
        if len(parts) >= 3:
            return parts[2]
    except (IndexError, AttributeError):
        pass
    return ""


def _normalized_published_date(value: str) -> str:
    """Return one strict calendar date without rewriting provider evidence."""

    if not value or value != value.strip():
        return ""
    if _ISO_DATE.fullmatch(value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return ""
    if _ISO_DATETIME.fullmatch(value):
        try:
            parsed = datetime.fromisoformat(
                f"{value[:-1]}+00:00" if value.endswith("Z") else value
            )
        except ValueError:
            return ""
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date().isoformat()
    rfc_match = _RFC_DATETIME.fullmatch(value)
    if rfc_match is None:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return ""
    weekday = rfc_match.group("weekday")
    if weekday is not None and parsed.strftime("%a") != weekday:
        return ""
    return parsed.astimezone(timezone.utc).date().isoformat()


class TavilyBackend(SearchBackend):
    """Tavily web search backend.

    Reads API key from environment variable (default: TAVILY_API_KEY).
    No API key is ever printed or stored in metadata.
    """

    name = "tavily"

    def __init__(self, api_key_env: str = DEFAULT_API_KEY_ENV) -> None:
        self._api_key_env = api_key_env

    @staticmethod
    def capabilities() -> SearchBackendCapabilities:
        return TAVILY_CAPABILITIES

    def is_available(self) -> bool:
        return bool(os.environ.get(self._api_key_env))

    @staticmethod
    def _exchange(
        operation: str,
        request_body: bytes,
        *,
        response_body: bytes | None = None,
        status_code: int | None = None,
        transport_error_class: str | None = None,
    ) -> TavilyAcquisitionExchange:
        from multi_agent_brief.contracts.v2 import TavilyAcquisitionExchange

        payload: dict[str, Any] = {
            "operation": operation,
            "endpoint": f"/{operation}",
            "request_body_base64": base64.b64encode(request_body).decode("ascii"),
            "request_body_sha256": _sha256_hex(request_body),
            "request_body_size_bytes": len(request_body),
        }
        if response_body is not None:
            payload.update(
                {
                    "response_body_base64": base64.b64encode(response_body).decode(
                        "ascii"
                    ),
                    "response_body_sha256": _sha256_hex(response_body),
                    "response_body_size_bytes": len(response_body),
                    "status_code": status_code,
                }
            )
        if transport_error_class is not None:
            payload["transport_error_class"] = transport_error_class
        return TavilyAcquisitionExchange.model_validate(payload, strict=True)

    @staticmethod
    def _post_json(
        endpoint: str,
        payload: dict[str, Any],
        api_key: str,
        *,
        response_byte_cap: int = TAVILY_SINGLE_HTTP_RESPONSE_BYTE_CAP,
    ) -> tuple[bytes, int, bytes]:
        request_body = _canonical_json_bytes(payload)
        if (
            not request_body
            or len(request_body) > TAVILY_REQUEST_BODY_BYTE_CAP
            or response_byte_cap < 0
            or response_byte_cap > TAVILY_MULTI_EXCHANGE_RESPONSE_BYTE_CAP
        ):
            raise SearchBackendError(
                "Tavily request failed",
                backend="tavily",
            ) from None
        request = urllib.request.Request(
            endpoint,
            data=request_body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        transport_error_class: str | None = None
        try:
            with _open_no_redirect(request, timeout=TAVILY_TIMEOUT_SECONDS) as response:
                status_code = int(getattr(response, "status", 200))
                response_body = response.read(response_byte_cap + 1)
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            response_body = exc.read(response_byte_cap + 1)
        except Exception as exc:
            transport_error_class = _transport_error_class(exc)
            status_code = 0
            response_body = b""
        if transport_error_class is not None:
            raise SearchBackendError(
                "Tavily request failed",
                backend="tavily",
                error_class=transport_error_class,
            ) from None
        if len(response_body) > response_byte_cap:
            raise SearchBackendError(
                "Tavily request failed",
                backend="tavily",
            ) from None
        api_key_sha256 = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        if _response_contains_credential(
            response_body,
            api_key=api_key,
            api_key_sha256=api_key_sha256,
        ):
            raise SearchBackendError(
                "Tavily request failed",
                backend="tavily",
            ) from None
        return request_body, status_code, response_body

    @staticmethod
    def _search_payload(
        query: str,
        max_results: int,
        *,
        domains: list[str] | None,
        topic: str,
        search_depth: str,
        time_range: str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> dict[str, Any]:
        if (start_date is None) != (end_date is None):
            raise SearchBackendError(
                "Tavily search failed",
                backend="tavily",
            ) from None
        payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "topic": topic,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": False,
            "auto_parameters": False,
        }
        if time_range is not None:
            if time_range not in {"week", "month"}:
                raise SearchBackendError(
                    "Tavily search failed",
                    backend="tavily",
                ) from None
            payload["time_range"] = time_range
        elif start_date is not None and end_date is not None:
            payload["start_date"] = start_date
            payload["end_date"] = end_date
        if domains:
            payload["include_domains"] = domains
        return payload

    def search(
        self,
        query: str,
        max_results: int = 10,
        *,
        domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        return list(
            self.search_response(
                query,
                max_results=max_results,
                domains=domains,
                **kwargs,
            ).results
        )

    def search_response(
        self,
        query: str,
        max_results: int = 10,
        *,
        domains: list[str] | None = None,
        **kwargs: Any,
    ) -> SearchResponse:
        """Execute one bounded request and retain the exact response bytes."""

        api_key = os.environ.get(self._api_key_env, "")
        if not api_key:
            return SearchResponse(raw_response=b"", status_code=0, results=())

        topic = kwargs.get("topic", "news")
        search_depth = kwargs.get("search_depth", "basic")
        time_range = kwargs.get("time_range")
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        payload = self._search_payload(
            query,
            max_results,
            domains=domains,
            topic=topic,
            search_depth=search_depth,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
        )
        invalid_response = False
        try:
            _, status_code, raw_response = self._post_json(
                TAVILY_API_URL, payload, api_key
            )
            if status_code != 200:
                raise ValueError("invalid Tavily response")
            decoded = _json_object_without_duplicate_keys(raw_response)
            data = _TavilyResponse.model_validate(decoded, strict=True)
            if len(data.results) > max_results:
                raise ValueError("invalid Tavily result count")
        except Exception:
            invalid_response = True
            status_code = 0
            raw_response = b""
            data = None
        if invalid_response or data is None:
            raise SearchBackendError(
                "Tavily search failed",
                backend="tavily",
            ) from None

        results: list[SearchResult] = []
        for item in data.results:
            raw_published = item.published_date or ""
            published_at = _normalized_published_date(raw_published)
            has_published = bool(published_at)
            projection = {
                "title": item.title,
                "url": str(item.url),
                "snippet": item.content,
                "raw_content": None,
                "published_date": raw_published,
                "score": item.score,
            }
            results.append(
                SearchResult(
                    title=item.title,
                    url=str(item.url),
                    snippet=item.content,
                    raw_content=None,
                    published_at=published_at,
                    source_name=_extract_domain(str(item.url)),
                    raw_projection=projection,
                    metadata={
                        "backend": "tavily",
                        "query": query,
                        "date_status": "published_at_present"
                        if has_published
                        else "missing_published_at",
                        "source_temporality": "published"
                        if has_published
                        else "retrieved_only",
                        "evidence_quality": "snippet",
                        "vertical": topic,
                        "raw_score": item.score,
                        "has_raw_content": False,
                    },
                )
            )
        return SearchResponse(
            raw_response=raw_response,
            status_code=status_code,
            results=tuple(results[:max_results]),
        )

    @staticmethod
    def _multi_bundle_response(
        *,
        searches: list[TavilySearchTaskExchange],
        extract_batches: list[TavilyExtractBatchExchange],
        unique_urls: list[str],
        task_statuses: list[TavilyTaskAcquisitionStatus],
        results: list[SearchResult],
    ) -> SearchResponse:
        from multi_agent_brief.contracts.v2 import TavilyAcquisitionBundleV2

        covered = sum(item.status == "covered" for item in task_statuses)
        status = (
            "complete"
            if covered == len(task_statuses)
            else "failed"
            if covered == 0
            else "partial"
        )
        bundle = TavilyAcquisitionBundleV2.model_validate(
            {
                "schema_version": TavilyAcquisitionBundleV2.schema_id,
                "provider_id": "tavily",
                "status": status,
                "searches": [item.model_dump(mode="json") for item in searches],
                "extract_batches": [
                    item.model_dump(mode="json") for item in extract_batches
                ],
                "unique_urls": unique_urls,
                "task_statuses": [
                    item.model_dump(mode="json") for item in task_statuses
                ],
            },
            strict=True,
        )
        bundle_bytes = _canonical_json_bytes(bundle.model_dump(mode="json"))
        if len(bundle_bytes) > TAVILY_MULTI_ACQUISITION_BUNDLE_BYTE_CAP:
            raise SearchBackendError(
                "Tavily multi-acquisition failed",
                backend="tavily",
            ) from None
        return SearchResponse(
            raw_response=bundle_bytes,
            status_code=200,
            results=tuple(sorted(results, key=lambda item: item.url)),
        )

    def multi_acquisition_response(
        self,
        tasks: list[dict[str, Any]],
        *,
        max_unique_urls: int = 800,
        extract_batch_size: int = 20,
    ) -> SearchResponse:
        """Execute the frozen Solar Stock task matrix without budget pruning."""

        from multi_agent_brief.contracts.v2 import (
            TavilyExtractBatchExchange,
            TavilyExtractUrlOutcome,
            TavilySearchTaskExchange,
            TavilyTaskAcquisitionStatus,
        )

        if (
            not 1 <= len(tasks) <= 20
            or max_unique_urls != min(800, len(tasks) * 40)
            or extract_batch_size != 20
            or [item.get("task_id") for item in tasks]
            != sorted({item.get("task_id") for item in tasks})
        ):
            raise SearchBackendError(
                "Tavily multi-acquisition failed", backend="tavily"
            )
        api_key = os.environ.get(self._api_key_env, "")
        if not api_key:
            return SearchResponse(raw_response=b"", status_code=0, results=())

        searches: list[TavilySearchTaskExchange] = []
        batches: list[TavilyExtractBatchExchange] = []
        task_urls: dict[str, set[str]] = {
            str(item["task_id"]): set() for item in tasks
        }
        search_items: dict[str, tuple[_TavilyResult, dict[str, Any]]] = {}
        url_tasks: dict[str, set[str]] = {}
        extract_outcomes: dict[str, TavilyExtractUrlOutcome] = {}
        successful_results: dict[str, SearchResult] = {}
        search_statuses: dict[str, list[str]] = {
            str(item["task_id"]): [] for item in tasks
        }
        extract_unavailable_urls: set[str] = set()

        def execute_search(
            task: dict[str, Any], *, phase: str, ordinal: int
        ) -> None:
            task_id = str(task["task_id"])
            request = task if phase == "primary" else task["backfill"]
            payload = self._search_payload(
                str(request["query"]),
                20,
                domains=list(request.get("domains") or []),
                topic=str(task["topic"]),
                search_depth="advanced",
                time_range="week" if phase == "primary" else "month",
                start_date=None,
                end_date=None,
            )
            request_bytes = _canonical_json_bytes(payload)
            try:
                request_bytes, status_code, response_bytes = self._post_json(
                    TAVILY_API_URL,
                    payload,
                    api_key,
                    response_byte_cap=TAVILY_MULTI_EXCHANGE_RESPONSE_BYTE_CAP,
                )
            except SearchBackendError as exc:
                exchange = self._exchange(
                    "search",
                    request_bytes,
                    transport_error_class=exc.error_class,
                )
                status = "unavailable"
                discovered: list[str] = []
            else:
                exchange = self._exchange(
                    "search",
                    request_bytes,
                    response_body=response_bytes,
                    status_code=status_code,
                )
                status = "unavailable"
                discovered = []
                if status_code == 200:
                    try:
                        decoded = _json_object_without_duplicate_keys(response_bytes)
                        parsed = _TavilyResponse.model_validate(decoded, strict=True)
                        raw_results = decoded.get("results")
                        if (
                            len(parsed.results) > 20
                            or not isinstance(raw_results, list)
                            or len(raw_results) != len(parsed.results)
                        ):
                            raise ValueError("invalid result count")
                        by_url: dict[str, tuple[_TavilyResult, dict[str, Any]]] = {}
                        for raw_item, item in zip(
                            raw_results, parsed.results, strict=True
                        ):
                            if not isinstance(raw_item, dict):
                                raise ValueError("invalid result")
                            by_url.setdefault(item.url, (item, raw_item))
                        discovered = sorted(by_url)
                        status = "succeeded" if discovered else "empty"
                        for url, pair in by_url.items():
                            task_urls[task_id].add(url)
                            url_tasks.setdefault(url, set()).add(task_id)
                            search_items.setdefault(url, pair)
                    except Exception:
                        status = "invalid"
                        discovered = []
            searches.append(
                TavilySearchTaskExchange.model_validate(
                    {
                        "task_id": task_id,
                        "phase": phase,
                        "status": status,
                        "exchange": exchange.model_dump(mode="json"),
                        "discovered_urls": discovered,
                    },
                    strict=True,
                )
            )
            search_statuses[task_id].append(status)

        def execute_extracts(urls: list[str], *, phase: str) -> None:
            for start in range(0, len(urls), extract_batch_size):
                batch_urls = sorted(urls[start : start + extract_batch_size])
                payload: dict[str, Any] = {
                    "urls": batch_urls,
                    "chunks_per_source": 5,
                    "extract_depth": "advanced",
                    "include_images": False,
                    "include_favicon": False,
                    "format": "markdown",
                    "include_usage": True,
                }
                request_bytes = _canonical_json_bytes(payload)
                try:
                    request_bytes, status_code, response_bytes = self._post_json(
                        TAVILY_EXTRACT_API_URL,
                        payload,
                        api_key,
                        response_byte_cap=TAVILY_MULTI_EXCHANGE_RESPONSE_BYTE_CAP,
                    )
                except SearchBackendError as exc:
                    exchange = self._exchange(
                        "extract",
                        request_bytes,
                        transport_error_class=exc.error_class,
                    )
                    batch_status = "unavailable"
                    outcomes: list[TavilyExtractUrlOutcome] = []
                    extract_unavailable_urls.update(batch_urls)
                else:
                    exchange = self._exchange(
                        "extract",
                        request_bytes,
                        response_body=response_bytes,
                        status_code=status_code,
                    )
                    outcomes = []
                    if status_code != 200:
                        batch_status = "unavailable"
                        extract_unavailable_urls.update(batch_urls)
                    else:
                        try:
                            decoded = _json_object_without_duplicate_keys(
                                response_bytes
                            )
                            parsed = _TavilyExtractResponse.model_validate(
                                decoded, strict=True
                            )
                            raw_successes = decoded.get("results")
                            raw_failures = decoded.get("failed_results")
                            if not isinstance(raw_successes, list) or not isinstance(
                                raw_failures, list
                            ):
                                raise ValueError("invalid extract result lists")
                            observed: dict[str, TavilyExtractUrlOutcome] = {}
                            for raw_item, item in zip(
                                raw_successes, parsed.results, strict=True
                            ):
                                if (
                                    not isinstance(raw_item, dict)
                                    or item.url not in batch_urls
                                    or item.url in observed
                                ):
                                    raise ValueError("invalid extract success")
                                content = (
                                    item.raw_content.strip()
                                    if item.raw_content
                                    else ""
                                )
                                item_sha = _sha256_hex(
                                    _canonical_json_bytes(raw_item)
                                )
                                outcome_payload: dict[str, Any] = {
                                    "url": item.url,
                                    "status": "succeeded"
                                    if content
                                    else "empty_content",
                                    "response_item_sha256": item_sha,
                                }
                                if content:
                                    content_bytes = content.encode("utf-8")
                                    outcome_payload.update(
                                        {
                                            "content_sha256": _sha256_hex(
                                                content_bytes
                                            ),
                                            "content_size_bytes": len(content_bytes),
                                        }
                                    )
                                    search_item, raw_search_item = search_items[
                                        item.url
                                    ]
                                    published_at = _normalized_published_date(
                                        search_item.published_date or ""
                                    )
                                    successful_results[item.url] = SearchResult(
                                        title=search_item.title,
                                        url=item.url,
                                        snippet=search_item.content,
                                        raw_content=content,
                                        published_at=published_at,
                                        source_name=_extract_domain(item.url),
                                        raw_projection={
                                            "schema_version": "briefloop.tavily_extract_source_projection.v2",
                                            "discovery_task_ids": sorted(
                                                url_tasks[item.url]
                                            ),
                                            "search_result": tavily_search_discovery_projection(
                                                raw_search_item
                                            ),
                                            "extract_result": raw_item,
                                        },
                                        metadata={
                                            "backend": "tavily",
                                            "discovery_task_ids": sorted(
                                                url_tasks[item.url]
                                            ),
                                            "date_status": "published_at_present"
                                            if published_at
                                            else "missing_published_at",
                                            "source_temporality": "published"
                                            if published_at
                                            else "retrieved_only",
                                            "content_shape": "provider_extract_content",
                                            "evidence_quality": "partial_extract",
                                            "has_raw_content": True,
                                        },
                                    )
                                observed[item.url] = (
                                    TavilyExtractUrlOutcome.model_validate(
                                        outcome_payload, strict=True
                                    )
                                )
                            for raw_item, item in zip(
                                raw_failures, parsed.failed_results, strict=True
                            ):
                                if (
                                    not isinstance(raw_item, dict)
                                    or item.url not in batch_urls
                                    or item.url in observed
                                ):
                                    raise ValueError("invalid extract failure")
                                observed[item.url] = (
                                    TavilyExtractUrlOutcome.model_validate(
                                        {
                                            "url": item.url,
                                            "status": "provider_failed",
                                            "response_item_sha256": _sha256_hex(
                                                _canonical_json_bytes(raw_item)
                                            ),
                                        },
                                        strict=True,
                                    )
                                )
                            if set(observed) != set(batch_urls):
                                raise ValueError("extract outcomes are not total")
                            outcomes = [observed[url] for url in batch_urls]
                            for outcome in outcomes:
                                extract_outcomes[outcome.url] = outcome
                            succeeded = sum(
                                item.status == "succeeded" for item in outcomes
                            )
                            batch_status = (
                                "all_failed"
                                if succeeded == 0
                                else "succeeded"
                                if succeeded == len(outcomes)
                                else "partial"
                            )
                        except Exception:
                            batch_status = "invalid"
                            outcomes = []
                            extract_unavailable_urls.update(batch_urls)
                batches.append(
                    TavilyExtractBatchExchange.model_validate(
                        {
                            "phase": phase,
                            "batch_ordinal": len(batches) + 1,
                            "status": batch_status,
                            "exchange": exchange.model_dump(mode="json"),
                            "urls": batch_urls,
                            "outcomes": [
                                item.model_dump(mode="json") for item in outcomes
                            ],
                        },
                        strict=True,
                    )
                )

        for ordinal, task in enumerate(tasks, start=1):
            execute_search(task, phase="primary", ordinal=ordinal)
        primary_urls = sorted(search_items)[:max_unique_urls]
        execute_extracts(primary_urls, phase="primary")

        backfill_tasks = [
            task
            for task in tasks
            if sum(
                extract_outcomes.get(url) is not None
                and extract_outcomes[url].status == "succeeded"
                for url in task_urls[str(task["task_id"])]
            )
            < int(task["minimum_extract_successes"])
        ]
        for task in backfill_tasks:
            execute_search(
                task,
                phase="backfill",
                ordinal=len(searches) + 1,
            )
        backfill_urls = sorted(set(search_items) - set(primary_urls))
        if len(primary_urls) + len(backfill_urls) > max_unique_urls:
            raise SearchBackendError(
                "Tavily multi-acquisition failed", backend="tavily"
            )
        execute_extracts(backfill_urls, phase="backfill")

        task_statuses: list[TavilyTaskAcquisitionStatus] = []
        for primary_ordinal, task in enumerate(tasks, start=1):
            task_id = str(task["task_id"])
            urls = task_urls[task_id]
            success_count = sum(
                extract_outcomes.get(url) is not None
                and extract_outcomes[url].status == "succeeded"
                for url in urls
            )
            threshold = int(task["minimum_extract_successes"])
            if success_count >= threshold:
                status = "covered"
            elif all(
                item in {"unavailable", "invalid"}
                for item in search_statuses[task_id]
            ):
                status = "search_unavailable"
            elif urls & extract_unavailable_urls:
                status = "extract_unavailable"
            else:
                status = "coverage_insufficient"
            backfill_exchange = next(
                (
                    index
                    for index, item in enumerate(searches, start=1)
                    if item.task_id == task_id and item.phase == "backfill"
                ),
                None,
            )
            task_statuses.append(
                TavilyTaskAcquisitionStatus.model_validate(
                    {
                        "task_id": task_id,
                        "primary_search_ordinal": primary_ordinal,
                        "backfill_search_ordinal": backfill_exchange,
                        "discovered_unique_url_count": len(urls),
                        "extracted_success_count": success_count,
                        "minimum_extract_successes": threshold,
                        "status": status,
                    },
                    strict=True,
                )
            )
        return self._multi_bundle_response(
            searches=searches,
            extract_batches=batches,
            unique_urls=sorted(set(primary_urls) | set(backfill_urls)),
            task_statuses=task_statuses,
            results=list(successful_results.values()),
        )
