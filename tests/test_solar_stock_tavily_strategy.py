from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from multi_agent_brief.contracts.v2 import (
    RuntimeWebSearchAcquisitionSpecV3,
    RuntimeWebSearchTaskSpecV3,
    TavilyAcquisitionBundleV2,
)
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.sources.search_backends.tavily import (
    TAVILY_EXTRACT_API_URL,
    TAVILY_API_URL,
    TAVILY_MULTI_EXCHANGE_RESPONSE_BYTE_CAP,
    TavilyBackend,
)
from multi_agent_brief.sources.search_backends.base import SearchBackendError
from multi_agent_brief.sources.solar_stock_plan import solar_stock_search_tasks
from multi_agent_brief.sources.tavily_acquisition import (
    TavilyMultiAcquisitionObservation,
    parse_tavily_acquisition_bundle,
    tavily_observation_matches_spec,
)


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _frozen_spec() -> RuntimeWebSearchAcquisitionSpecV3:
    tasks = [
        RuntimeWebSearchTaskSpecV3.model_validate(
            {
                "schema_version": RuntimeWebSearchTaskSpecV3.schema_id,
                **item,
            },
            strict=True,
        ).model_dump(mode="json", exclude_unset=False)
        for item in solar_stock_search_tasks()
    ]
    payload = {
        "schema_version": RuntimeWebSearchAcquisitionSpecV3.schema_id,
        "kind": "web_search_multi",
        "provider_id": "tavily",
        "tasks": tasks,
        "max_primary_search_calls": 20,
        "max_backfill_search_calls": 20,
        "max_extract_calls": 40,
        "max_unique_urls": 800,
        "extract_batch_size": 20,
    }
    payload["acquisition_spec_fingerprint"] = canonical_fingerprint(payload)
    return RuntimeWebSearchAcquisitionSpecV3.model_validate(payload, strict=True)


def test_solar_stock_executes_twenty_atomic_searches_and_extracts_over_one_hundred(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    calls: list[tuple[str, dict[str, object]]] = []

    def post_json(endpoint, payload, _api_key, *, response_byte_cap):
        calls.append((endpoint, payload))
        request = _json_bytes(payload)
        if endpoint == TAVILY_API_URL:
            search_ordinal = sum(call[0] == TAVILY_API_URL for call in calls)
            results = [
                {
                    "title": f"Source {search_ordinal}-{item}",
                    "url": f"https://example.com/{search_ordinal}/{item}",
                    "content": "discovery only",
                    "published_date": "2026-08-10",
                    "score": 0.9,
                }
                for item in range(6)
            ]
            return request, 200, _json_bytes({"results": results})
        assert endpoint == TAVILY_EXTRACT_API_URL
        urls = payload["urls"]
        return request, 200, _json_bytes(
            {
                "results": [
                    {"url": url, "raw_content": f"durable body for {url}"}
                    for url in urls
                ],
                "failed_results": [],
            }
        )

    monkeypatch.setattr(TavilyBackend, "_post_json", staticmethod(post_json))
    response = TavilyBackend().multi_acquisition_response(
        solar_stock_search_tasks(),
        max_unique_urls=800,
        extract_batch_size=20,
    )

    bundle = TavilyAcquisitionBundleV2.model_validate_json(
        response.raw_response, strict=True
    )
    assert len(bundle.searches) == 20
    assert all(item.phase == "primary" for item in bundle.searches)
    assert all(
        json.loads(
            base64.b64decode(item.exchange.request_body_base64).decode("utf-8")
        )["max_results"]
        == 20
        for item in bundle.searches
    )
    assert all(
        json.loads(
            base64.b64decode(item.exchange.request_body_base64).decode("utf-8")
        )["search_depth"]
        == "advanced"
        for item in bundle.searches
    )
    assert len(bundle.unique_urls) == 120
    assert len(bundle.extract_batches) == 6
    assert all(len(item.urls) == 20 for item in bundle.extract_batches)
    assert len(response.results) == 120
    assert len(calls) == 26

    observation = parse_tavily_acquisition_bundle(response.raw_response)
    assert isinstance(observation, TavilyMultiAcquisitionObservation)
    assert observation.result_count == 120
    assert observation.durable_content_count == 120
    assert tavily_observation_matches_spec(observation, _frozen_spec())


def test_undercovered_task_gets_one_targeted_backfill_and_extracts_every_url(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    task = solar_stock_search_tasks()[0]
    calls: list[tuple[str, dict[str, object]]] = []

    def post_json(endpoint, payload, _api_key, *, response_byte_cap):
        calls.append((endpoint, payload))
        request = _json_bytes(payload)
        if endpoint == TAVILY_API_URL:
            search_count = sum(item[0] == TAVILY_API_URL for item in calls)
            suffixes = range(1) if search_count == 1 else range(1, 3)
            return request, 200, _json_bytes(
                {
                    "results": [
                        {
                            "title": f"Source {item}",
                            "url": f"https://example.com/source-{item}",
                            "content": "discovery only",
                            "published_date": "2026-08-10",
                            "score": 0.9,
                        }
                        for item in suffixes
                    ]
                }
            )
        urls = payload["urls"]
        return request, 200, _json_bytes(
            {
                "results": [
                    {"url": url, "raw_content": f"durable body for {url}"}
                    for url in urls
                ],
                "failed_results": [],
            }
        )

    monkeypatch.setattr(TavilyBackend, "_post_json", staticmethod(post_json))
    response = TavilyBackend().multi_acquisition_response(
        [task],
        max_unique_urls=40,
        extract_batch_size=20,
    )
    bundle = TavilyAcquisitionBundleV2.model_validate_json(
        response.raw_response, strict=True
    )

    assert [item.phase for item in bundle.searches] == ["primary", "backfill"]
    assert len(bundle.extract_batches) == 2
    assert len(bundle.unique_urls) == 3
    assert len(response.results) == 3
    assert bundle.task_statuses[0].status == "covered"
    backfill_request = json.loads(
        base64.b64decode(
            bundle.searches[1].exchange.request_body_base64
        ).decode("utf-8")
    )
    assert backfill_request["time_range"] == "month"
    assert backfill_request["max_results"] == 20


def test_multi_tavily_partial_extract_retains_exact_outcomes(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    task = dict(solar_stock_search_tasks()[0])
    task["minimum_extract_successes"] = 1

    def post_json(endpoint, payload, _api_key, *, response_byte_cap):
        request = _json_bytes(payload)
        if endpoint == TAVILY_API_URL:
            return request, 200, _json_bytes(
                {
                    "results": [
                        {
                            "title": f"Source {item}",
                            "url": f"https://example.com/source-{item}",
                            "content": "discovery only",
                            "published_date": "2026-08-10",
                            "score": 0.9,
                        }
                        for item in range(2)
                    ]
                }
            )
        urls = payload["urls"]
        return request, 200, _json_bytes(
            {
                "results": [
                    {"url": urls[0], "raw_content": "durable body"}
                ],
                "failed_results": [
                    {"url": urls[1], "error": "value-free failure"}
                ],
            }
        )

    monkeypatch.setattr(TavilyBackend, "_post_json", staticmethod(post_json))
    response = TavilyBackend().multi_acquisition_response(
        [task], max_unique_urls=40, extract_batch_size=20
    )
    bundle = TavilyAcquisitionBundleV2.model_validate_json(
        response.raw_response, strict=True
    )
    assert bundle.status == "complete"
    assert bundle.extract_batches[0].status == "partial"
    assert [item.status for item in bundle.extract_batches[0].outcomes] == [
        "succeeded",
        "provider_failed",
    ]
    assert len(response.results) == 1


def test_multi_tavily_transport_failures_are_value_free(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")

    def post_json(endpoint, payload, _api_key, *, response_byte_cap):
        del endpoint, payload, response_byte_cap
        error = SearchBackendError(
            "Tavily request failed",
            backend="tavily",
            error_class="network_permission_denied",
        )
        raise error from None

    monkeypatch.setattr(TavilyBackend, "_post_json", staticmethod(post_json))
    response = TavilyBackend().multi_acquisition_response(
        [solar_stock_search_tasks()[0]],
        max_unique_urls=40,
        extract_batch_size=20,
    )
    bundle = TavilyAcquisitionBundleV2.model_validate_json(
        response.raw_response, strict=True
    )
    assert bundle.status == "failed"
    assert len(bundle.searches) == 2
    assert all(item.status == "unavailable" for item in bundle.searches)
    assert all(
        item.exchange.transport_error_class == "network_permission_denied"
        for item in bundle.searches
    )
    assert "sandbox host" not in response.raw_response.decode("utf-8").lower()


def test_multi_tavily_overbound_exchange_is_not_retained(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    sentinel = b"overbound-provider-body-must-not-persist"
    body = sentinel + (b"x" * TAVILY_MULTI_EXCHANGE_RESPONSE_BYTE_CAP)
    read_limits: list[int] = []

    class Response:
        status = 200

        def read(self, limit=-1):
            read_limits.append(limit)
            return body if limit < 0 else body[:limit]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    response = TavilyBackend().multi_acquisition_response(
        [solar_stock_search_tasks()[0]],
        max_unique_urls=40,
        extract_batch_size=20,
    )
    bundle = TavilyAcquisitionBundleV2.model_validate_json(
        response.raw_response, strict=True
    )
    assert len(bundle.searches) == 2
    assert all(item.status == "unavailable" for item in bundle.searches)
    assert all(
        item.exchange.response_body_base64 is None for item in bundle.searches
    )
    assert read_limits == [TAVILY_MULTI_EXCHANGE_RESPONSE_BYTE_CAP + 1] * 2
    assert sentinel not in response.raw_response


@pytest.mark.parametrize("phase", ["search", "extract"])
@pytest.mark.parametrize("invalid_kind", ["duplicate", "surrogate", "nonfinite"])
def test_multi_tavily_malformed_responses_are_retained_as_invalid(
    monkeypatch,
    phase,
    invalid_kind,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    invalid = {
        "duplicate": b'{"results":[],"results":[]}',
        "surrogate": b'{"results":[{"title":"\\ud800","url":"https://example.com/a","content":"x","score":1.0}]}',
        "nonfinite": b'{"results":[{"title":"A","url":"https://example.com/a","content":"x","score":NaN}]}',
    }[invalid_kind]
    valid_search = _json_bytes(
        {
            "results": [
                {
                    "title": "A",
                    "url": "https://example.com/a",
                    "content": "discovery only",
                    "published_date": "2026-08-10",
                    "score": 1.0,
                }
            ]
        }
    )

    def post_json(endpoint, payload, _api_key, *, response_byte_cap):
        request = _json_bytes(payload)
        if endpoint == TAVILY_API_URL:
            return request, 200, invalid if phase == "search" else valid_search
        if invalid_kind == "duplicate":
            extract = b'{"results":[],"results":[],"failed_results":[]}'
        elif invalid_kind == "surrogate":
            extract = b'{"results":[{"url":"https://example.com/a","raw_content":"\\ud800"}],"failed_results":[]}'
        else:
            extract = b'{"results":[{"url":"https://example.com/a","raw_content":"x","score":NaN}],"failed_results":[]}'
        return request, 200, extract

    monkeypatch.setattr(TavilyBackend, "_post_json", staticmethod(post_json))
    response = TavilyBackend().multi_acquisition_response(
        [solar_stock_search_tasks()[0]],
        max_unique_urls=40,
        extract_batch_size=20,
    )
    bundle = TavilyAcquisitionBundleV2.model_validate_json(
        response.raw_response, strict=True
    )
    if phase == "search":
        assert all(item.status == "invalid" for item in bundle.searches)
    else:
        assert all(item.status == "invalid" for item in bundle.extract_batches)
    assert response.results == ()


@pytest.mark.parametrize("phase", ["search", "extract"])
@pytest.mark.parametrize("echo_kind", ["key", "hash"])
def test_multi_tavily_credential_echo_is_never_persisted(
    monkeypatch,
    phase,
    echo_kind,
) -> None:
    sentinel = "tvly-response-echo-sentinel"
    sentinel_hash = hashlib.sha256(sentinel.encode("utf-8")).hexdigest()
    echoed = sentinel if echo_kind == "key" else sentinel_hash
    escaped = "".join(f"\\u{ord(character):04x}" for character in echoed)
    unsafe = ('{"ignored_diagnostic":"' + escaped + '","results":[]}').encode(
        "ascii"
    )
    valid_search = _json_bytes(
        {
            "results": [
                {
                    "title": "A",
                    "url": "https://example.com/a",
                    "content": "discovery only",
                    "published_date": "2026-08-10",
                    "score": 1.0,
                }
            ]
        }
    )

    class Response:
        status = 200

        def __init__(self, body):
            self.body = body

        def read(self, limit=-1):
            return self.body if limit < 0 else self.body[:limit]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout=30):
        del timeout
        if request.full_url.endswith("/search"):
            return Response(unsafe if phase == "search" else valid_search)
        return Response(unsafe)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setenv("TAVILY_API_KEY", sentinel)
    response = TavilyBackend().multi_acquisition_response(
        [solar_stock_search_tasks()[0]],
        max_unique_urls=40,
        extract_batch_size=20,
    )
    bundle = TavilyAcquisitionBundleV2.model_validate_json(
        response.raw_response, strict=True
    )
    exchanges = (
        [item.exchange for item in bundle.searches]
        if phase == "search"
        else [item.exchange for item in bundle.extract_batches]
    )
    assert exchanges
    assert all(item.response_body_base64 is None for item in exchanges)
    assert sentinel not in response.raw_response.decode("utf-8")
    assert sentinel_hash not in response.raw_response.decode("utf-8").lower()


def test_multi_tavily_redirect_is_not_followed(monkeypatch) -> None:
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(content_length)
            hits.append(self.path)
            self.send_response(302 if self.path == "/search" else 200)
            if self.path == "/search":
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{self.server.server_port}/redirect-target",
                )
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _format, *args):
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(
            "multi_agent_brief.sources.search_backends.tavily.TAVILY_API_URL",
            f"http://127.0.0.1:{server.server_port}/search",
        )
        monkeypatch.setenv("TAVILY_API_KEY", "test-only")
        response = TavilyBackend().multi_acquisition_response(
            [solar_stock_search_tasks()[0]],
            max_unique_urls=40,
            extract_batch_size=20,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    bundle = TavilyAcquisitionBundleV2.model_validate_json(
        response.raw_response, strict=True
    )
    assert hits == ["/search", "/search"]
    assert all(item.exchange.status_code == 302 for item in bundle.searches)
    assert "/redirect-target" not in hits
