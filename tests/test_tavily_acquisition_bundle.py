from __future__ import annotations

import base64
import hashlib
import json

import pytest

from multi_agent_brief.contracts.v2 import (
    RuntimeWebSearchAcquisitionSpec,
    TavilyAcquisitionBundle,
    TavilyAcquisitionExchange,
)
from multi_agent_brief.sources.tavily_acquisition import (
    TavilyAcquisitionBundleError,
    parse_tavily_acquisition_bundle,
    tavily_observation_matches_spec,
)


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exchange(operation: str, request: dict, response: dict):
    request_bytes = _canonical(request)
    response_bytes = _canonical(response)
    return TavilyAcquisitionExchange.model_validate(
        {
            "operation": operation,
            "endpoint": f"/{operation}",
            "request_body_base64": base64.b64encode(request_bytes).decode("ascii"),
            "request_body_sha256": _sha(request_bytes),
            "request_body_size_bytes": len(request_bytes),
            "response_body_base64": base64.b64encode(response_bytes).decode("ascii"),
            "response_body_sha256": _sha(response_bytes),
            "response_body_size_bytes": len(response_bytes),
            "status_code": 200,
        },
        strict=True,
    )


def _partial_bundle(
    *,
    query: str = "solar supply chain",
    response_content: str = "durable extract",
    outcome_content: str = "durable extract",
) -> bytes:
    urls = ["https://a.example/item", "https://b.example/item"]
    search_request = {
        "query": query,
        "max_results": 5,
        "topic": "news",
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
        "auto_parameters": False,
        "time_range": "week",
        "include_domains": ["a.example", "b.example"],
    }
    search_response = {
        "results": [
            {
                "title": "First",
                "url": urls[0],
                "content": "snippet one",
                "published_date": "2026-08-01T23:00:00-02:00",
                "score": 0.9,
            },
            {
                "title": "Second",
                "url": urls[1],
                "content": "snippet two",
                "published_date": None,
                "score": 0.8,
            },
        ]
    }
    extract_request = {
        "urls": urls,
        "query": query,
        "chunks_per_source": 5,
        "extract_depth": "basic",
        "include_images": False,
        "include_favicon": False,
        "format": "markdown",
        "include_usage": True,
    }
    success = {"url": urls[0], "raw_content": response_content}
    failure = {"url": urls[1], "error": "unavailable"}
    extract_response = {"results": [success], "failed_results": [failure]}
    outcome_bytes = outcome_content.strip().encode("utf-8")
    bundle = TavilyAcquisitionBundle.model_validate(
        {
            "schema_version": TavilyAcquisitionBundle.schema_id,
            "provider_id": "tavily",
            "status": "extract_results_partial",
            "search": _exchange("search", search_request, search_response).model_dump(
                mode="json"
            ),
            "extract": _exchange(
                "extract", extract_request, extract_response
            ).model_dump(mode="json"),
            "extract_urls": urls,
            "outcomes": [
                {
                    "url": urls[0],
                    "status": "succeeded",
                    "response_item_sha256": _sha(
                        _canonical({"url": urls[0], "raw_content": outcome_content})
                    ),
                    "content_sha256": _sha(outcome_bytes),
                    "content_size_bytes": len(outcome_bytes),
                },
                {
                    "url": urls[1],
                    "status": "provider_failed",
                    "response_item_sha256": _sha(_canonical(failure)),
                },
            ],
        },
        strict=True,
    )
    return _canonical(bundle.model_dump(mode="json"))


def _spec() -> RuntimeWebSearchAcquisitionSpec:
    payload = {
        "schema_version": RuntimeWebSearchAcquisitionSpec.schema_id,
        "kind": "web_search",
        "provider_id": "tavily",
        "requests": [
            {
                "schema_version": "briefloop.runtime_web_search_request_spec.v2",
                "query": "solar supply chain",
                "domains": ["a.example", "b.example"],
                "max_results": 5,
                "recency_days": 7,
            }
        ],
    }
    payload["acquisition_spec_fingerprint"] = _sha(_canonical(payload))
    return RuntimeWebSearchAcquisitionSpec.model_validate(payload, strict=True)


def _bundle_with_nonfinite_search_score() -> bytes:
    payload = json.loads(_partial_bundle())
    search = payload["search"]
    response = base64.b64decode(search["response_body_base64"])
    response = response.replace(b'"score":0.9', b'"score":NaN')
    search["response_body_base64"] = base64.b64encode(response).decode("ascii")
    search["response_body_sha256"] = _sha(response)
    search["response_body_size_bytes"] = len(response)
    return _canonical(payload)


def test_partial_bundle_binds_exact_success_and_authorized_request() -> None:
    observation = parse_tavily_acquisition_bundle(_partial_bundle())

    assert observation.result_count == 2
    assert observation.durable_content_count == 1
    assert observation.sources[0].content == b"durable extract"
    assert observation.sources[0].title == "First"
    assert observation.sources[0].publisher == "a.example"
    assert observation.sources[0].published_at == "2026-08-02"
    assert tavily_observation_matches_spec(observation, _spec())


def test_self_consistent_changed_request_does_not_match_frozen_spec() -> None:
    observation = parse_tavily_acquisition_bundle(
        _partial_bundle(query="different authorized-looking query")
    )

    assert not tavily_observation_matches_spec(observation, _spec())


@pytest.mark.parametrize(
    "payload",
    [
        _partial_bundle(response_content="tampered extract"),
        _bundle_with_nonfinite_search_score(),
        json.dumps(
            json.loads(_partial_bundle()),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8"),
    ],
)
def test_bundle_tamper_or_noncanonical_bytes_fail_closed(payload: bytes) -> None:
    with pytest.raises(TavilyAcquisitionBundleError):
        parse_tavily_acquisition_bundle(payload)
