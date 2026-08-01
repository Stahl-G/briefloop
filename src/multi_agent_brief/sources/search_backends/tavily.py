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
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from multi_agent_brief.contracts.v2 import (
        TavilyAcquisitionExchange,
        TavilyExtractUrlOutcome,
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
TAVILY_RESPONSE_BYTE_CAP = 4 * 1024 * 1024
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
        return TavilyAcquisitionExchange.model_validate(payload, strict=True)

    @staticmethod
    def _bundle_response(
        status: str,
        search: TavilyAcquisitionExchange,
        *,
        extract: TavilyAcquisitionExchange | None = None,
        extract_urls: list[str] | None = None,
        outcomes: tuple[TavilyExtractUrlOutcome, ...] = (),
        results: tuple[SearchResult, ...] = (),
    ) -> SearchResponse:
        from multi_agent_brief.contracts.v2 import TavilyAcquisitionBundle

        bundle = TavilyAcquisitionBundle.model_validate(
            {
                "schema_version": TavilyAcquisitionBundle.schema_id,
                "provider_id": "tavily",
                "status": status,
                "search": search.model_dump(mode="json"),
                "extract": None if extract is None else extract.model_dump(mode="json"),
                "extract_urls": extract_urls or [],
                "outcomes": [item.model_dump(mode="json") for item in outcomes],
            },
            strict=True,
        )
        return SearchResponse(
            raw_response=_canonical_json_bytes(bundle.model_dump(mode="json")),
            status_code=200,
            results=results,
        )

    @staticmethod
    def _post_json(
        endpoint: str,
        payload: dict[str, Any],
        api_key: str,
    ) -> tuple[bytes, int, bytes]:
        request_body = _canonical_json_bytes(payload)
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
        transport_failed = False
        try:
            with _open_no_redirect(request, timeout=TAVILY_TIMEOUT_SECONDS) as response:
                status_code = int(getattr(response, "status", 200))
                response_body = response.read(TAVILY_RESPONSE_BYTE_CAP + 1)
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            response_body = exc.read(TAVILY_RESPONSE_BYTE_CAP + 1)
        except Exception:
            transport_failed = True
            status_code = 0
            response_body = b""
        if transport_failed:
            raise SearchBackendError(
                "Tavily request failed",
                backend="tavily",
            ) from None
        if len(response_body) > TAVILY_RESPONSE_BYTE_CAP:
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

    def acquisition_response(
        self,
        query: str,
        max_results: int = 5,
        *,
        domains: list[str] | None = None,
        **kwargs: Any,
    ) -> SearchResponse:
        """Execute one Search then at most one batch Extract and freeze both."""

        if max_results < 1 or max_results > 5:
            raise SearchBackendError("Tavily acquisition failed", backend="tavily")
        time_range = kwargs.get("time_range")
        if (
            time_range not in {"week", "month"}
            or kwargs.get("start_date") is not None
            or kwargs.get("end_date") is not None
        ):
            raise SearchBackendError("Tavily acquisition failed", backend="tavily")
        search_payload = self._search_payload(
            query,
            max_results,
            domains=domains,
            topic=str(kwargs.get("topic", "news")),
            search_depth=str(kwargs.get("search_depth", "basic")),
            time_range=time_range,
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
        )
        api_key = os.environ.get(self._api_key_env, "")
        if not api_key:
            return SearchResponse(raw_response=b"", status_code=0, results=())
        search_request = _canonical_json_bytes(search_payload)
        try:
            search_request, search_status, search_response = self._post_json(
                TAVILY_API_URL, search_payload, api_key
            )
        except SearchBackendError:
            return self._bundle_response(
                "search_response_unavailable",
                self._exchange("search", search_request),
            )
        search_exchange = self._exchange(
            "search",
            search_request,
            response_body=search_response,
            status_code=search_status,
        )
        if search_status != 200:
            return self._bundle_response(
                "search_response_unavailable",
                search_exchange,
            )
        invalid_search = False
        try:
            decoded_search = _json_object_without_duplicate_keys(search_response)
            search_data = _TavilyResponse.model_validate(decoded_search, strict=True)
            if len(search_data.results) > max_results:
                raise ValueError("invalid Search result count")
        except Exception:
            invalid_search = True
            decoded_search = {}
            search_data = None
        if invalid_search or search_data is None:
            return self._bundle_response("search_response_invalid", search_exchange)
        search_by_url: dict[str, tuple[_TavilyResult, dict[str, Any]]] = {}
        raw_results = decoded_search.get("results")
        if not isinstance(raw_results, list):
            return self._bundle_response("search_response_invalid", search_exchange)
        for raw_item, item in zip(raw_results, search_data.results, strict=True):
            if not isinstance(raw_item, dict):
                return self._bundle_response("search_response_invalid", search_exchange)
            search_by_url.setdefault(item.url, (item, raw_item))
        extract_urls = sorted(search_by_url)[:max_results]
        if not extract_urls:
            return self._bundle_response("search_results_empty", search_exchange)

        extract_payload: dict[str, Any] = {
            "urls": extract_urls,
            "query": query,
            "chunks_per_source": 5,
            "extract_depth": "basic",
            "include_images": False,
            "include_favicon": False,
            "format": "markdown",
            "include_usage": True,
        }
        extract_request = _canonical_json_bytes(extract_payload)
        try:
            extract_request, extract_status, extract_response = self._post_json(
                TAVILY_EXTRACT_API_URL, extract_payload, api_key
            )
        except SearchBackendError:
            extract_exchange = self._exchange("extract", extract_request)
            return self._bundle_response(
                "extract_response_unavailable",
                search_exchange,
                extract=extract_exchange,
                extract_urls=extract_urls,
            )
        extract_exchange = self._exchange(
            "extract",
            extract_request,
            response_body=extract_response,
            status_code=extract_status,
        )
        if extract_status != 200:
            return self._bundle_response(
                "extract_response_unavailable",
                search_exchange,
                extract=extract_exchange,
                extract_urls=extract_urls,
            )
        invalid_extract = False
        try:
            decoded_extract = _json_object_without_duplicate_keys(extract_response)
            extract_data = _TavilyExtractResponse.model_validate(
                decoded_extract, strict=True
            )
            raw_successes = decoded_extract.get("results")
            raw_failures = decoded_extract.get("failed_results")
            if not isinstance(raw_successes, list) or not isinstance(
                raw_failures, list
            ):
                raise ValueError("invalid Extract result lists")
            if len(raw_successes) != len(extract_data.results) or len(
                raw_failures
            ) != len(extract_data.failed_results):
                raise ValueError("invalid Extract result counts")
        except Exception:
            invalid_extract = True
            decoded_extract = {}
            extract_data = None
            raw_successes = None
            raw_failures = None
        if (
            invalid_extract
            or extract_data is None
            or not isinstance(raw_successes, list)
            or not isinstance(raw_failures, list)
        ):
            return self._bundle_response(
                "extract_response_invalid",
                search_exchange,
                extract=extract_exchange,
                extract_urls=extract_urls,
            )

        outcome_payloads: dict[str, dict[str, Any]] = {}
        successful_results: dict[str, SearchResult] = {}
        for raw_item, item in zip(raw_successes, extract_data.results, strict=True):
            if not isinstance(raw_item, dict) or item.url not in search_by_url:
                return self._bundle_response(
                    "extract_response_invalid",
                    search_exchange,
                    extract=extract_exchange,
                    extract_urls=extract_urls,
                )
            if item.url in outcome_payloads:
                return self._bundle_response(
                    "extract_response_invalid",
                    search_exchange,
                    extract=extract_exchange,
                    extract_urls=extract_urls,
                )
            content = item.raw_content.strip() if item.raw_content else ""
            response_item_sha256 = _sha256_hex(_canonical_json_bytes(raw_item))
            if not content:
                outcome_payloads[item.url] = {
                    "url": item.url,
                    "status": "empty_content",
                    "response_item_sha256": response_item_sha256,
                }
                continue
            outcome_payloads[item.url] = {
                "url": item.url,
                "status": "succeeded",
                "response_item_sha256": response_item_sha256,
                "content_sha256": _sha256_hex(content.encode("utf-8")),
                "content_size_bytes": len(content.encode("utf-8")),
            }
            search_item, raw_search_item = search_by_url[item.url]
            raw_published = search_item.published_date or ""
            published_at = _normalized_published_date(raw_published)
            successful_results[item.url] = SearchResult(
                title=search_item.title,
                url=item.url,
                snippet=search_item.content,
                raw_content=content,
                published_at=published_at,
                source_name=_extract_domain(item.url),
                raw_projection={
                    "schema_version": "briefloop.tavily_extract_source_projection.v1",
                    "search_result": tavily_search_discovery_projection(
                        raw_search_item
                    ),
                    "extract_result": raw_item,
                },
                metadata={
                    "backend": "tavily",
                    "query": query,
                    "date_status": "published_at_present"
                    if published_at
                    else "missing_published_at",
                    "source_temporality": "published"
                    if published_at
                    else "retrieved_only",
                    "content_shape": "provider_extract_content",
                    "evidence_quality": "partial_extract",
                    "vertical": search_payload["topic"],
                    "raw_score": search_item.score,
                    "has_raw_content": True,
                },
            )
        for raw_item, item in zip(
            raw_failures, extract_data.failed_results, strict=True
        ):
            if not isinstance(raw_item, dict) or item.url not in search_by_url:
                return self._bundle_response(
                    "extract_response_invalid",
                    search_exchange,
                    extract=extract_exchange,
                    extract_urls=extract_urls,
                )
            if item.url in outcome_payloads:
                return self._bundle_response(
                    "extract_response_invalid",
                    search_exchange,
                    extract=extract_exchange,
                    extract_urls=extract_urls,
                )
            outcome_payloads[item.url] = {
                "url": item.url,
                "status": "provider_failed",
                "response_item_sha256": _sha256_hex(_canonical_json_bytes(raw_item)),
            }
        if set(outcome_payloads) != set(extract_urls):
            return self._bundle_response(
                "extract_response_invalid",
                search_exchange,
                extract=extract_exchange,
                extract_urls=extract_urls,
            )
        from multi_agent_brief.contracts.v2 import TavilyExtractUrlOutcome

        outcomes = tuple(
            TavilyExtractUrlOutcome.model_validate(outcome_payloads[url], strict=True)
            for url in extract_urls
        )
        succeeded = len(successful_results)
        status = (
            "extract_results_all_failed"
            if succeeded == 0
            else "extract_results_succeeded"
            if succeeded == len(extract_urls)
            else "extract_results_partial"
        )
        return self._bundle_response(
            status,
            search_exchange,
            extract=extract_exchange,
            extract_urls=extract_urls,
            outcomes=outcomes,
            results=tuple(
                successful_results[url]
                for url in extract_urls
                if url in successful_results
            ),
        )
