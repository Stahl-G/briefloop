"""Deterministic validation for frozen Tavily acquisition evidence.

The v1 single-Search parser is read-only support for immutable historical
receipts.  It is not an executable acquisition strategy and has no producer.
All current Tavily execution emits the v2 multi-Search/batch-Extract bundle.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from multi_agent_brief.contracts.v2 import (
        RuntimeWebSearchAcquisitionSpec,
        RuntimeWebSearchAcquisitionSpecV3,
        TavilyAcquisitionBundle,
        TavilyAcquisitionBundleV2,
    )


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


class TavilyAcquisitionBundleError(ValueError):
    """The frozen bundle is not one canonical, internally bound acquisition."""


@dataclass(frozen=True)
class TavilySearchRequestFacts:
    """Non-secret request facts that must match the frozen acquisition spec."""

    query: str
    domains: tuple[str, ...]
    max_results: int
    time_range: str


@dataclass(frozen=True)
class TavilyExtractedSource:
    """One successful Extract item and its exact accepted-source projection."""

    url: str
    projection: bytes
    content: bytes
    search_title: str
    title: str
    publisher: str
    published_at: str | None
    discovery_task_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TavilyAcquisitionObservation:
    """Bounded facts recomputed from exact Search and Extract exchange bytes."""

    bundle: TavilyAcquisitionBundle
    request: TavilySearchRequestFacts
    result_count: int
    durable_content_count: int
    sources: tuple[TavilyExtractedSource, ...]


@dataclass(frozen=True)
class TavilyMultiSearchRequestFacts:
    """One exact task/phase request reconstructed from provider bytes."""

    task_id: str
    phase: str
    query: str
    domains: tuple[str, ...]
    max_results: int
    time_range: str
    topic: str
    search_depth: str


@dataclass(frozen=True)
class TavilyMultiAcquisitionObservation:
    """Recomputed facts for a multi-task, multi-batch acquisition."""

    bundle: TavilyAcquisitionBundleV2
    requests: tuple[TavilyMultiSearchRequestFacts, ...]
    result_count: int
    durable_content_count: int
    sources: tuple[TavilyExtractedSource, ...]


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise TavilyAcquisitionBundleError(
            "provider JSON is not canonical UTF-8"
        ) from exc


def _json_object(payload: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise TavilyAcquisitionBundleError("duplicate JSON object key")
            result[key] = value
        return result

    def nonfinite(value: str) -> None:
        raise TavilyAcquisitionBundleError(
            f"non-finite provider number is forbidden: {value}"
        )

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TavilyAcquisitionBundleError("invalid JSON object") from exc
    if type(value) is not dict:
        raise TavilyAcquisitionBundleError("invalid JSON object")
    return value


def _exchange_bytes(encoded: str | None) -> bytes | None:
    if encoded is None:
        return None
    try:
        return base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:  # pragma: no cover - model closes
        raise TavilyAcquisitionBundleError("invalid exchange bytes") from exc


def _http_url(value: Any) -> str:
    if type(value) is not str or value != value.strip() or not value:
        raise TavilyAcquisitionBundleError("invalid provider URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise TavilyAcquisitionBundleError("invalid provider URL")
    return value


def _search_request(payload: bytes) -> tuple[dict[str, Any], int]:
    request = _json_object(payload)
    allowed = {
        "query",
        "max_results",
        "topic",
        "search_depth",
        "include_answer",
        "include_raw_content",
        "auto_parameters",
        "time_range",
        "include_domains",
    }
    if set(request) - allowed:
        raise TavilyAcquisitionBundleError("unexpected Search request field")
    query = request.get("query")
    max_results = request.get("max_results")
    domains = request.get("include_domains")
    if (
        type(query) is not str
        or not query.strip()
        or type(max_results) is not int
        or not 1 <= max_results <= 5
        or request.get("topic") != "news"
        or request.get("search_depth") != "basic"
        or request.get("include_answer") is not False
        or request.get("include_raw_content") is not False
        or request.get("auto_parameters") is not False
        or request.get("time_range") not in {"week", "month"}
        or (
            domains is not None
            and (
                type(domains) is not list
                or not domains
                or any(type(item) is not str or not item.strip() for item in domains)
                or len(domains) != len(set(domains))
            )
        )
        or payload != _canonical_json_bytes(request)
    ):
        raise TavilyAcquisitionBundleError("invalid Search request")
    return request, max_results


def _normalized_published_date(value: str) -> str | None:
    if not value or value != value.strip():
        return None
    if _ISO_DATE.fullmatch(value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    if _ISO_DATETIME.fullmatch(value):
        try:
            parsed = datetime.fromisoformat(
                f"{value[:-1]}+00:00" if value.endswith("Z") else value
            )
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date().isoformat()
    rfc_match = _RFC_DATETIME.fullmatch(value)
    if rfc_match is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    weekday = rfc_match.group("weekday")
    if weekday is not None and parsed.strftime("%a") != weekday:
        return None
    return parsed.astimezone(timezone.utc).date().isoformat()


def _search_results(
    payload: bytes,
    *,
    max_results: int,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    response = _json_object(payload)
    _canonical_json_bytes(response)
    results = response.get("results")
    if type(results) is not list or len(results) > max_results:
        raise TavilyAcquisitionBundleError("invalid Search results")
    by_url: dict[str, dict[str, Any]] = {}
    for item in results:
        if type(item) is not dict:
            raise TavilyAcquisitionBundleError("invalid Search result")
        title = item.get("title")
        snippet = item.get("content")
        published = item.get("published_date")
        score = item.get("score")
        if (
            type(title) is not str
            or type(snippet) is not str
            or type(published) not in {str, type(None)}
            or (
                score is not None
                and (type(score) not in {int, float} or isinstance(score, bool))
            )
        ):
            raise TavilyAcquisitionBundleError("invalid Search result")
        url = _http_url(item.get("url"))
        by_url.setdefault(url, item)
    return sorted(by_url), by_url


def tavily_search_discovery_projection(item: dict[str, Any]) -> dict[str, Any]:
    """Allowlist Search-owned discovery fields for per-source raw residue."""

    return {
        "title": item["title"],
        "url": item["url"],
        "content": item["content"],
        "published_date": item.get("published_date"),
        "score": item.get("score"),
    }


def _extract_request(
    payload: bytes,
    *,
    query: str,
    extract_urls: list[str],
) -> None:
    request = _json_object(payload)
    expected = {
        "urls": extract_urls,
        "query": query,
        "chunks_per_source": 5,
        "extract_depth": "basic",
        "include_images": False,
        "include_favicon": False,
        "format": "markdown",
        "include_usage": True,
    }
    if request != expected or payload != _canonical_json_bytes(request):
        raise TavilyAcquisitionBundleError("invalid batch Extract request")


def _multi_search_request(payload: bytes) -> dict[str, Any]:
    request = _json_object(payload)
    allowed = {
        "query",
        "max_results",
        "topic",
        "search_depth",
        "include_answer",
        "include_raw_content",
        "auto_parameters",
        "time_range",
        "include_domains",
    }
    domains = request.get("include_domains")
    if (
        set(request) - allowed
        or type(request.get("query")) is not str
        or not request["query"].strip()
        or request.get("max_results") != 20
        or request.get("topic") not in {"news", "general"}
        or request.get("search_depth") != "advanced"
        or request.get("include_answer") is not False
        or request.get("include_raw_content") is not False
        or request.get("auto_parameters") is not False
        or request.get("time_range") not in {"week", "month"}
        or (
            domains is not None
            and (
                type(domains) is not list
                or not domains
                or domains != sorted(set(domains))
                or any(type(item) is not str or not item.strip() for item in domains)
            )
        )
        or payload != _canonical_json_bytes(request)
    ):
        raise TavilyAcquisitionBundleError("invalid multi-Search request")
    return request


def _multi_extract_request(payload: bytes, *, urls: list[str]) -> None:
    request = _json_object(payload)
    expected = {
        "urls": urls,
        "chunks_per_source": 5,
        "extract_depth": "advanced",
        "include_images": False,
        "include_favicon": False,
        "format": "markdown",
        "include_usage": True,
    }
    if request != expected or payload != _canonical_json_bytes(request):
        raise TavilyAcquisitionBundleError("invalid multi-batch Extract request")


def _parse_tavily_acquisition_bundle_v1(
    payload: bytes,
) -> TavilyAcquisitionObservation:
    """Parse and cross-bind one canonical bundle, including response-item hashes."""

    # Source-provider modules load while the legacy contract registry is still
    # being assembled.  Resolving v2 at module-import time freezes that registry
    # mid-initialization, so strict models are intentionally loaded on demand.
    from multi_agent_brief.contracts.v2 import TavilyAcquisitionBundle

    raw_bundle = _json_object(payload)
    try:
        bundle = TavilyAcquisitionBundle.model_validate(raw_bundle, strict=True)
    except Exception as exc:
        raise TavilyAcquisitionBundleError("invalid acquisition bundle") from exc
    if payload != _canonical_json_bytes(bundle.model_dump(mode="json")):
        raise TavilyAcquisitionBundleError("acquisition bundle is not canonical")

    search_request_bytes = _exchange_bytes(bundle.search.request_body_base64)
    if search_request_bytes is None:  # pragma: no cover - strict model closes
        raise TavilyAcquisitionBundleError("missing Search request")
    search_request, max_results = _search_request(search_request_bytes)
    request_facts = TavilySearchRequestFacts(
        query=search_request["query"],
        domains=tuple(search_request.get("include_domains", ())),
        max_results=max_results,
        time_range=search_request["time_range"],
    )
    search_response_bytes = _exchange_bytes(bundle.search.response_body_base64)

    if bundle.status == "search_response_unavailable":
        return TavilyAcquisitionObservation(bundle, request_facts, 0, 0, ())
    if search_response_bytes is None or bundle.search.status_code != 200:
        raise TavilyAcquisitionBundleError("missing HTTP 200 Search response")
    if bundle.status == "search_response_invalid":
        return TavilyAcquisitionObservation(bundle, request_facts, 0, 0, ())

    search_urls, search_by_url = _search_results(
        search_response_bytes,
        max_results=max_results,
    )
    if bundle.status == "search_results_empty":
        if search_urls:
            raise TavilyAcquisitionBundleError("non-empty Search marked empty")
        return TavilyAcquisitionObservation(bundle, request_facts, 0, 0, ())
    if bundle.extract_urls != search_urls:
        raise TavilyAcquisitionBundleError("Extract URLs do not match Search results")
    if bundle.extract is None:  # pragma: no cover - strict model closes
        raise TavilyAcquisitionBundleError("missing Extract exchange")
    extract_request_bytes = _exchange_bytes(bundle.extract.request_body_base64)
    if extract_request_bytes is None:  # pragma: no cover - strict model closes
        raise TavilyAcquisitionBundleError("missing Extract request")
    _extract_request(
        extract_request_bytes,
        query=search_request["query"],
        extract_urls=bundle.extract_urls,
    )
    if bundle.status == "extract_response_unavailable":
        return TavilyAcquisitionObservation(
            bundle, request_facts, len(search_urls), 0, ()
        )
    extract_response_bytes = _exchange_bytes(bundle.extract.response_body_base64)
    if extract_response_bytes is None or bundle.extract.status_code != 200:
        raise TavilyAcquisitionBundleError("missing HTTP 200 Extract response")
    if bundle.status == "extract_response_invalid":
        return TavilyAcquisitionObservation(
            bundle, request_facts, len(search_urls), 0, ()
        )

    extract_response = _json_object(extract_response_bytes)
    _canonical_json_bytes(extract_response)
    successful_items = extract_response.get("results")
    failed_items = extract_response.get("failed_results")
    if type(successful_items) is not list or type(failed_items) is not list:
        raise TavilyAcquisitionBundleError("invalid Extract result lists")

    observed: dict[str, tuple[str, str, bytes | None]] = {}
    sources: dict[str, TavilyExtractedSource] = {}
    for item in successful_items:
        if type(item) is not dict:
            raise TavilyAcquisitionBundleError("invalid Extract success")
        url = _http_url(item.get("url"))
        raw_content = item.get("raw_content")
        if (
            type(raw_content) not in {str, type(None)}
            or url not in search_by_url
            or url in observed
        ):
            raise TavilyAcquisitionBundleError("invalid Extract success")
        item_hash = hashlib.sha256(_canonical_json_bytes(item)).hexdigest()
        content = raw_content.strip().encode("utf-8") if raw_content else b""
        status = "succeeded" if content else "empty_content"
        observed[url] = (status, item_hash, content or None)
        if content:
            sources[url] = TavilyExtractedSource(
                url=url,
                projection=_canonical_json_bytes(
                    {
                        "schema_version": (
                            "briefloop.tavily_extract_source_projection.v1"
                        ),
                        "search_result": tavily_search_discovery_projection(
                            search_by_url[url]
                        ),
                        "extract_result": item,
                    }
                ),
                content=content,
                search_title=search_by_url[url]["title"],
                title=(
                    search_by_url[url]["title"].strip()
                    or url.split("/")[2].strip()
                    or "Collected source"
                ),
                publisher=url.split("/")[2],
                published_at=_normalized_published_date(
                    search_by_url[url].get("published_date") or ""
                ),
            )
    for item in failed_items:
        if type(item) is not dict:
            raise TavilyAcquisitionBundleError("invalid Extract failure")
        url = _http_url(item.get("url"))
        if url not in search_by_url or url in observed:
            raise TavilyAcquisitionBundleError("duplicate Extract outcome")
        observed[url] = (
            "provider_failed",
            hashlib.sha256(_canonical_json_bytes(item)).hexdigest(),
            None,
        )
    if set(observed) != set(search_urls):
        raise TavilyAcquisitionBundleError("Extract outcomes are not total")

    by_outcome_url = {item.url: item for item in bundle.outcomes}
    if set(by_outcome_url) != set(search_urls):
        raise TavilyAcquisitionBundleError("bundle outcomes are not total")
    for url in search_urls:
        status, item_hash, content = observed[url]
        outcome = by_outcome_url[url]
        if outcome.status != status or outcome.response_item_sha256 != item_hash:
            raise TavilyAcquisitionBundleError("Extract outcome identity mismatch")
        if content is not None and (
            outcome.content_sha256 != hashlib.sha256(content).hexdigest()
            or outcome.content_size_bytes != len(content)
        ):
            raise TavilyAcquisitionBundleError("Extract content identity mismatch")

    ordered_sources = tuple(sources[url] for url in search_urls if url in sources)
    return TavilyAcquisitionObservation(
        bundle=bundle,
        request=request_facts,
        result_count=len(search_urls),
        durable_content_count=len(ordered_sources),
        sources=ordered_sources,
    )


def _parse_tavily_acquisition_bundle_v2(
    payload: bytes,
) -> TavilyMultiAcquisitionObservation:
    from multi_agent_brief.contracts.v2 import TavilyAcquisitionBundleV2

    raw_bundle = _json_object(payload)
    try:
        bundle = TavilyAcquisitionBundleV2.model_validate(raw_bundle, strict=True)
    except Exception as exc:
        raise TavilyAcquisitionBundleError(
            "invalid multi-acquisition bundle"
        ) from exc
    if payload != _canonical_json_bytes(bundle.model_dump(mode="json")):
        raise TavilyAcquisitionBundleError(
            "multi-acquisition bundle is not canonical"
        )

    requests: list[TavilyMultiSearchRequestFacts] = []
    search_by_url: dict[str, dict[str, Any]] = {}
    url_tasks: dict[str, set[str]] = {}
    task_search_ordinals: dict[str, dict[str, int]] = {}
    for ordinal, item in enumerate(bundle.searches, start=1):
        request_bytes = _exchange_bytes(item.exchange.request_body_base64)
        if request_bytes is None:  # pragma: no cover - strict exchange closes
            raise TavilyAcquisitionBundleError("missing multi-Search request")
        request = _multi_search_request(request_bytes)
        requests.append(
            TavilyMultiSearchRequestFacts(
                task_id=item.task_id,
                phase=item.phase,
                query=request["query"],
                domains=tuple(request.get("include_domains", ())),
                max_results=20,
                time_range=request["time_range"],
                topic=request["topic"],
                search_depth=request["search_depth"],
            )
        )
        phases = task_search_ordinals.setdefault(item.task_id, {})
        if item.phase in phases:
            raise TavilyAcquisitionBundleError("duplicate task Search phase")
        phases[item.phase] = ordinal
        response_bytes = _exchange_bytes(item.exchange.response_body_base64)
        if item.status == "unavailable":
            if response_bytes is not None:
                raise TavilyAcquisitionBundleError(
                    "unavailable Search carries response bytes"
                )
            continue
        if response_bytes is None or item.exchange.status_code != 200:
            raise TavilyAcquisitionBundleError("missing HTTP 200 task Search")
        if item.status == "invalid":
            continue
        urls, by_url = _search_results(response_bytes, max_results=20)
        if item.status == "empty":
            if urls:
                raise TavilyAcquisitionBundleError("non-empty task Search marked empty")
            continue
        if urls != item.discovered_urls:
            raise TavilyAcquisitionBundleError("task Search URL identity mismatch")
        for url in urls:
            url_tasks.setdefault(url, set()).add(item.task_id)
            search_by_url.setdefault(url, by_url[url])

    if sorted(search_by_url) != bundle.unique_urls:
        raise TavilyAcquisitionBundleError(
            "multi-acquisition URL universe does not match Search evidence"
        )

    sources: dict[str, TavilyExtractedSource] = {}
    succeeded_urls: set[str] = set()
    unavailable_urls: set[str] = set()
    for batch in bundle.extract_batches:
        request_bytes = _exchange_bytes(batch.exchange.request_body_base64)
        if request_bytes is None:  # pragma: no cover - strict exchange closes
            raise TavilyAcquisitionBundleError("missing batch Extract request")
        _multi_extract_request(request_bytes, urls=batch.urls)
        response_bytes = _exchange_bytes(batch.exchange.response_body_base64)
        if batch.status == "unavailable":
            if response_bytes is not None:
                raise TavilyAcquisitionBundleError(
                    "unavailable Extract carries response bytes"
                )
            unavailable_urls.update(batch.urls)
            continue
        if response_bytes is None or batch.exchange.status_code != 200:
            raise TavilyAcquisitionBundleError("missing HTTP 200 batch Extract")
        if batch.status == "invalid":
            unavailable_urls.update(batch.urls)
            continue
        response = _json_object(response_bytes)
        successes = response.get("results")
        failures = response.get("failed_results")
        if type(successes) is not list or type(failures) is not list:
            raise TavilyAcquisitionBundleError("invalid batch Extract result lists")
        observed: dict[str, tuple[str, str, bytes | None, dict[str, Any] | None]] = {}
        for raw_item in successes:
            if type(raw_item) is not dict:
                raise TavilyAcquisitionBundleError("invalid batch Extract success")
            url = _http_url(raw_item.get("url"))
            raw_content = raw_item.get("raw_content")
            if (
                type(raw_content) not in {str, type(None)}
                or url not in batch.urls
                or url in observed
            ):
                raise TavilyAcquisitionBundleError("invalid batch Extract success")
            content = raw_content.strip().encode("utf-8") if raw_content else b""
            observed[url] = (
                "succeeded" if content else "empty_content",
                hashlib.sha256(_canonical_json_bytes(raw_item)).hexdigest(),
                content or None,
                raw_item,
            )
        for raw_item in failures:
            if type(raw_item) is not dict:
                raise TavilyAcquisitionBundleError("invalid batch Extract failure")
            url = _http_url(raw_item.get("url"))
            if url not in batch.urls or url in observed:
                raise TavilyAcquisitionBundleError("duplicate batch Extract outcome")
            observed[url] = (
                "provider_failed",
                hashlib.sha256(_canonical_json_bytes(raw_item)).hexdigest(),
                None,
                raw_item,
            )
        if set(observed) != set(batch.urls):
            raise TavilyAcquisitionBundleError("batch Extract outcomes are not total")
        outcomes = {item.url: item for item in batch.outcomes}
        if set(outcomes) != set(batch.urls):
            raise TavilyAcquisitionBundleError("bundle batch outcomes are not total")
        for url in batch.urls:
            status, item_hash, content, raw_item = observed[url]
            outcome = outcomes[url]
            if outcome.status != status or outcome.response_item_sha256 != item_hash:
                raise TavilyAcquisitionBundleError(
                    "batch Extract outcome identity mismatch"
                )
            if content is None:
                continue
            if (
                outcome.content_sha256 != hashlib.sha256(content).hexdigest()
                or outcome.content_size_bytes != len(content)
                or raw_item is None
            ):
                raise TavilyAcquisitionBundleError(
                    "batch Extract content identity mismatch"
                )
            discovery = search_by_url[url]
            task_ids = tuple(sorted(url_tasks[url]))
            succeeded_urls.add(url)
            sources[url] = TavilyExtractedSource(
                url=url,
                projection=_canonical_json_bytes(
                    {
                        "schema_version": "briefloop.tavily_extract_source_projection.v2",
                        "discovery_task_ids": list(task_ids),
                        "search_result": tavily_search_discovery_projection(discovery),
                        "extract_result": raw_item,
                    }
                ),
                content=content,
                search_title=discovery["title"],
                title=discovery["title"].strip() or url.split("/")[2],
                publisher=url.split("/")[2],
                published_at=_normalized_published_date(
                    discovery.get("published_date") or ""
                ),
                discovery_task_ids=task_ids,
            )

    task_statuses = {item.task_id: item for item in bundle.task_statuses}
    if set(task_statuses) != set(task_search_ordinals):
        raise TavilyAcquisitionBundleError("task status universe mismatch")
    for task_id, status in task_statuses.items():
        ordinals = task_search_ordinals[task_id]
        discovered = {url for url, tasks in url_tasks.items() if task_id in tasks}
        extracted = discovered & succeeded_urls
        if (
            ordinals.get("primary") != status.primary_search_ordinal
            or ordinals.get("backfill") != status.backfill_search_ordinal
            or len(discovered) != status.discovered_unique_url_count
            or len(extracted) != status.extracted_success_count
            or (
                status.status == "extract_unavailable"
                and not (discovered & unavailable_urls)
            )
        ):
            raise TavilyAcquisitionBundleError("task coverage projection mismatch")

    ordered_sources = tuple(sources[url] for url in bundle.unique_urls if url in sources)
    return TavilyMultiAcquisitionObservation(
        bundle=bundle,
        requests=tuple(requests),
        result_count=len(bundle.unique_urls),
        durable_content_count=len(ordered_sources),
        sources=ordered_sources,
    )


def parse_tavily_acquisition_bundle(
    payload: bytes,
) -> TavilyAcquisitionObservation | TavilyMultiAcquisitionObservation:
    """Dispatch one exact frozen Tavily bundle by its declared schema."""

    raw = _json_object(payload)
    schema_version = raw.get("schema_version")
    if schema_version == "briefloop.tavily_acquisition_bundle.v1":
        return _parse_tavily_acquisition_bundle_v1(payload)
    if schema_version == "briefloop.tavily_acquisition_bundle.v2":
        return _parse_tavily_acquisition_bundle_v2(payload)
    raise TavilyAcquisitionBundleError("unsupported acquisition bundle schema")


def tavily_observation_matches_spec(
    observation: TavilyAcquisitionObservation | TavilyMultiAcquisitionObservation,
    spec: RuntimeWebSearchAcquisitionSpec | RuntimeWebSearchAcquisitionSpecV3,
) -> bool:
    """Bind exchange request bytes to the exact Store-frozen Tavily route."""

    from multi_agent_brief.contracts.v2 import RuntimeWebSearchAcquisitionSpecV3

    if isinstance(spec, RuntimeWebSearchAcquisitionSpecV3):
        if not isinstance(observation, TavilyMultiAcquisitionObservation):
            return False
        expected: list[TavilyMultiSearchRequestFacts] = []
        by_task = {item.task_id: item for item in spec.tasks}
        for observed in observation.requests:
            task = by_task.get(observed.task_id)
            if task is None:
                return False
            request = task if observed.phase == "primary" else task.backfill
            expected.append(
                TavilyMultiSearchRequestFacts(
                    task_id=task.task_id,
                    phase=observed.phase,
                    query=request.query,
                    domains=tuple(request.domains),
                    max_results=20,
                    time_range="week" if observed.phase == "primary" else "month",
                    topic=task.topic,
                    search_depth="advanced",
                )
            )
        primary_ids = [item.task_id for item in observation.requests if item.phase == "primary"]
        return (
            primary_ids == [item.task_id for item in spec.tasks]
            and tuple(expected) == observation.requests
            and observation.bundle.unique_urls
            == sorted(observation.bundle.unique_urls)
        )
    if not isinstance(observation, TavilyAcquisitionObservation):
        return False
    if spec.provider_id != "tavily" or len(spec.requests) != 1:
        return False
    request = spec.requests[0]
    expected_time_range = (
        "week"
        if request.recency_days == 7
        else "month"
        if request.recency_days == 30
        else None
    )
    return observation.request == TavilySearchRequestFacts(
        query=request.query,
        domains=tuple(request.domains),
        max_results=request.max_results,
        time_range=expected_time_range or "",
    )


__all__ = [
    "TavilyAcquisitionBundleError",
    "TavilyAcquisitionObservation",
    "TavilyMultiAcquisitionObservation",
    "TavilyMultiSearchRequestFacts",
    "TavilyExtractedSource",
    "TavilySearchRequestFacts",
    "parse_tavily_acquisition_bundle",
    "tavily_search_discovery_projection",
    "tavily_observation_matches_spec",
]
