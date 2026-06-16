from __future__ import annotations

import os
import threading
import time

from multi_agent_brief.sources.base import SourceConfig, SourceItem, SourceProvider, SourceQuery
from multi_agent_brief.sources.join import (
    SourceProviderBatch,
    join_source_provider_batches,
    source_join_digest,
)


def _source_item(
    *,
    source_name: str,
    title: str,
    content: str,
    url: str = "",
    retrieved_at: str = "2026-06-16T00:00:00+00:00",
    metadata: dict | None = None,
) -> SourceItem:
    return SourceItem(
        source_id="",
        source_name=source_name,
        source_type=source_name.lower(),
        title=title,
        content=content,
        url=url,
        retrieved_at=retrieved_at,
        metadata=metadata or {},
    )


def test_source_join_is_independent_of_provider_batch_order():
    early = SourceProviderBatch(
        provider="early",
        provider_priority=0,
        items=[
            _source_item(
                source_name="Early",
                title="Shared",
                content="early provider wins duplicate",
                url="https://example.com/shared",
            )
        ],
    )
    late = SourceProviderBatch(
        provider="late",
        provider_priority=1,
        items=[
            _source_item(
                source_name="Late",
                title="Shared",
                content="late provider loses duplicate",
                url="https://example.com/shared",
            ),
            _source_item(
                source_name="Late",
                title="Unique",
                content="late unique survives",
                url="https://example.com/unique",
            ),
        ],
    )

    items_a, errors_a = join_source_provider_batches(
        [late, early],
        recency_days=0,
    )
    items_b, errors_b = join_source_provider_batches(
        [early, late],
        recency_days=0,
    )

    assert [item.to_dict() for item in items_a] == [item.to_dict() for item in items_b]
    assert errors_a == errors_b == []
    assert any(item.content == "early provider wins duplicate" for item in items_a)
    assert all(item.content != "late provider loses duplicate" for item in items_a)
    assert source_join_digest(items_a, errors_a) == source_join_digest(items_b, errors_b)


def test_source_join_sorts_errors_by_provider_priority_not_completion_order():
    first = SourceProviderBatch(
        provider="first",
        provider_priority=0,
        errors=[{"provider": "first", "error_type": "ConfigError", "message": "first failed"}],
    )
    second = SourceProviderBatch(
        provider="second",
        provider_priority=1,
        errors=[{"provider": "second", "error_type": "ConfigError", "message": "second failed"}],
    )

    _items, errors = join_source_provider_batches(
        [second, first],
        recency_days=0,
    )

    assert [error["provider"] for error in errors] == ["first", "second"]


def test_source_join_digest_ignores_retrieved_at():
    items_a, errors_a = join_source_provider_batches(
        [
            SourceProviderBatch(
                provider="provider",
                provider_priority=0,
                items=[
                    _source_item(
                        source_name="Provider",
                        title="Stable",
                        content="same content",
                        url="https://example.com/stable",
                        retrieved_at="2026-06-16T00:00:00+00:00",
                        metadata={"retrieved_at": "2026-06-16T00:00:00+00:00"},
                    )
                ],
            )
        ],
        recency_days=0,
    )
    items_b, errors_b = join_source_provider_batches(
        [
            SourceProviderBatch(
                provider="provider",
                provider_priority=0,
                items=[
                    _source_item(
                        source_name="Provider",
                        title="Stable",
                        content="same content",
                        url="https://example.com/stable",
                        retrieved_at="2026-06-16T00:01:00+00:00",
                        metadata={"retrieved_at": "2026-06-16T00:01:00+00:00"},
                    )
                ],
            )
        ],
        recency_days=0,
    )

    assert source_join_digest(items_a, errors_a) == source_join_digest(items_b, errors_b)


def test_collect_all_sources_uses_enabled_provider_priority_for_duplicate_winner(monkeypatch):
    from multi_agent_brief.sources import registry

    class EarlyProvider(SourceProvider):
        name = "early"
        source_type = "early"

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            return [
                _source_item(
                    source_name="Early",
                    title="Shared",
                    content="enabled provider priority winner",
                    url="https://example.com/shared",
                )
            ]

    class LateProvider(SourceProvider):
        name = "late"
        source_type = "late"

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            return [
                _source_item(
                    source_name="Late",
                    title="Shared",
                    content="collection-order loser",
                    url="https://example.com/shared",
                )
            ]

    monkeypatch.setitem(registry.PROVIDER_CLASSES, "early", EarlyProvider)
    monkeypatch.setitem(registry.PROVIDER_CLASSES, "late", LateProvider)
    monkeypatch.setattr(
        registry,
        "get_providers",
        lambda _config: {"late": LateProvider(), "early": EarlyProvider()},
    )

    items, errors = registry.collect_all_sources(
        SourceConfig(enabled_providers=["early", "late"]),
        SourceQuery(recency_days=0),
    )

    assert errors == []
    assert len(items) == 1
    assert items[0].content == "enabled provider priority winner"


def test_collect_all_sources_parallel_matches_serial_when_safe_completion_order_changes(monkeypatch):
    from multi_agent_brief.sources import registry

    class SlowPriorityProvider(SourceProvider):
        name = "slow_priority"
        source_type = "slow_priority"
        parallel_safe = True

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            time.sleep(0.03)
            return [
                _source_item(
                    source_name="SlowPriority",
                    title="Shared",
                    content="provider priority winner",
                    url="https://example.com/shared",
                )
            ]

    class FastLaterProvider(SourceProvider):
        name = "fast_later"
        source_type = "fast_later"
        parallel_safe = True

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            return [
                _source_item(
                    source_name="FastLater",
                    title="Shared",
                    content="future completion loser",
                    url="https://example.com/shared",
                ),
                _source_item(
                    source_name="FastLater",
                    title="Unique",
                    content="unique parallel item",
                    url="https://example.com/unique",
                ),
            ]

    monkeypatch.setitem(registry.PROVIDER_CLASSES, "slow_priority", SlowPriorityProvider)
    monkeypatch.setitem(registry.PROVIDER_CLASSES, "fast_later", FastLaterProvider)
    config = SourceConfig(enabled_providers=["slow_priority", "fast_later"])
    query = SourceQuery(recency_days=0)

    serial_items, serial_errors = registry.collect_all_sources(config, query, parallel=False)
    parallel_items, parallel_errors = registry.collect_all_sources(config, query, parallel=True)

    assert [item.to_dict() for item in parallel_items] == [item.to_dict() for item in serial_items]
    assert parallel_errors == serial_errors == []
    assert any(item.content == "provider priority winner" for item in parallel_items)
    assert all(item.content != "future completion loser" for item in parallel_items)
    assert source_join_digest(parallel_items, parallel_errors) == source_join_digest(serial_items, serial_errors)


def test_collect_all_sources_parallel_keeps_unsafe_provider_serial(monkeypatch):
    from multi_agent_brief.sources import registry

    calls: list[tuple[str, bool]] = []

    class SafeProvider(SourceProvider):
        name = "safe"
        source_type = "safe"
        parallel_safe = True

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            calls.append(("safe", threading.current_thread() is threading.main_thread()))
            return [
                _source_item(
                    source_name="Safe",
                    title="Safe Source",
                    content="safe item",
                    url="https://example.com/safe",
                )
            ]

    class UnsafeProvider(SourceProvider):
        name = "unsafe"
        source_type = "unsafe"

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            calls.append(("unsafe", threading.current_thread() is threading.main_thread()))
            return [
                _source_item(
                    source_name="Unsafe",
                    title="Unsafe Source",
                    content="unsafe item",
                    url="https://example.com/unsafe",
                )
            ]

    monkeypatch.setitem(registry.PROVIDER_CLASSES, "safe", SafeProvider)
    monkeypatch.setitem(registry.PROVIDER_CLASSES, "unsafe", UnsafeProvider)

    items, errors = registry.collect_all_sources(
        SourceConfig(enabled_providers=["safe", "unsafe"]),
        SourceQuery(recency_days=0),
        parallel=True,
    )

    assert errors == []
    assert {item.content for item in items} == {"safe item", "unsafe item"}
    assert ("safe", False) in calls
    assert ("unsafe", True) in calls


def test_collect_all_sources_parallel_treats_unsafe_provider_as_ordering_barrier(monkeypatch):
    from multi_agent_brief.sources import registry

    state = {"ready": False}
    events: list[str] = []

    class UnsafeWriterProvider(SourceProvider):
        name = "unsafe_writer"
        source_type = "unsafe_writer"

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            events.append("unsafe_writer")
            state["ready"] = True
            return [
                _source_item(
                    source_name="UnsafeWriter",
                    title="Writer Source",
                    content="writer completed",
                    url="https://example.com/writer",
                )
            ]

    class SafeReaderProvider(SourceProvider):
        name = "safe_reader"
        source_type = "safe_reader"
        parallel_safe = True

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            events.append("safe_reader")
            content = "reader saw writer" if state["ready"] else "reader ran before writer"
            return [
                _source_item(
                    source_name="SafeReader",
                    title="Reader Source",
                    content=content,
                    url="https://example.com/reader",
                )
            ]

    monkeypatch.setitem(registry.PROVIDER_CLASSES, "unsafe_writer", UnsafeWriterProvider)
    monkeypatch.setitem(registry.PROVIDER_CLASSES, "safe_reader", SafeReaderProvider)
    config = SourceConfig(enabled_providers=["unsafe_writer", "safe_reader"])

    serial_items, serial_errors = registry.collect_all_sources(
        config,
        SourceQuery(recency_days=0),
        parallel=False,
    )
    state["ready"] = False
    events.clear()
    parallel_items, parallel_errors = registry.collect_all_sources(
        config,
        SourceQuery(recency_days=0),
        parallel=True,
    )

    assert events == ["unsafe_writer", "safe_reader"]
    assert [item.to_dict() for item in parallel_items] == [item.to_dict() for item in serial_items]
    assert parallel_errors == serial_errors == []
    assert any(item.content == "reader saw writer" for item in parallel_items)


def test_collect_all_sources_parallel_keeps_web_search_as_env_barrier(monkeypatch, tmp_path):
    from multi_agent_brief.sources import registry
    from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
    from multi_agent_brief.sources.web_search import WebSearchProvider

    observed: list[str | None] = []
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("TAVILY_API_KEY=SECRET_FROM_WORKSPACE\n", encoding="utf-8")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    class EnvBridgeBackend(SearchBackend):
        name = "env_bridge"
        _api_key_env = "TAVILY_API_KEY"

        def is_available(self):
            return bool(os.environ.get("TAVILY_API_KEY"))

        def search(self, query, max_results=10, *, domains=None, **kwargs):
            assert os.environ.get("TAVILY_API_KEY") == "SECRET_FROM_WORKSPACE"
            return [
                SearchResult(
                    title="Web Search Source",
                    url="https://example.com/web",
                    snippet="web search content",
                    published_at="2026-06-01",
                    source_name="Web",
                )
            ]

    class SafeObserverProvider(SourceProvider):
        name = "safe_observer"
        source_type = "safe_observer"
        parallel_safe = True

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            observed.append(os.environ.get("TAVILY_API_KEY"))
            return [
                _source_item(
                    source_name="SafeObserver",
                    title="Observer Source",
                    content="observer item",
                    url="https://example.com/observer",
                )
            ]

    monkeypatch.setitem(
        registry.PROVIDER_CLASSES,
        "web_search",
        lambda: WebSearchProvider(backend=EnvBridgeBackend()),
    )
    monkeypatch.setitem(registry.PROVIDER_CLASSES, "safe_observer", SafeObserverProvider)

    items, errors = registry.collect_all_sources(
        SourceConfig(
            enabled_providers=["web_search", "safe_observer"],
            web_search={
                "enabled": True,
                "mode": "external_api",
                "backend": "tavily",
                "_workspace_dir": str(workspace),
            },
            config_dir=str(workspace),
        ),
        SourceQuery(recency_days=0, keywords=["solar"]),
        parallel=True,
    )

    assert errors == []
    assert {item.url for item in items} == {
        "https://example.com/web",
        "https://example.com/observer",
    }
    assert observed == [None]


def test_collect_all_sources_parallel_records_provider_errors(monkeypatch):
    from multi_agent_brief.sources import registry

    class FailingSafeProvider(SourceProvider):
        name = "failing_safe"
        source_type = "failing_safe"
        parallel_safe = True

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            raise TimeoutError("parallel timeout")

    monkeypatch.setitem(registry.PROVIDER_CLASSES, "failing_safe", FailingSafeProvider)

    items, errors = registry.collect_all_sources(
        SourceConfig(enabled_providers=["failing_safe"]),
        SourceQuery(recency_days=0),
        parallel=True,
    )

    assert items == []
    assert errors == [
        {
            "provider": "failing_safe",
            "error_type": "TimeoutError",
            "message": "parallel timeout",
        }
    ]


def test_parallel_safe_override_must_be_boolean_true(monkeypatch):
    from multi_agent_brief.sources import registry

    calls: list[bool] = []

    class StringFalseProvider(SourceProvider):
        name = "string_false"
        source_type = "string_false"
        parallel_safe = "false"

        def validate_config(self, config):
            return []

        def collect(self, query, config):
            calls.append(threading.current_thread() is threading.main_thread())
            return [
                _source_item(
                    source_name="StringFalse",
                    title="String False",
                    content="serial despite truthy string",
                    url="https://example.com/string-false",
                )
            ]

    monkeypatch.setitem(registry.PROVIDER_CLASSES, "string_false", StringFalseProvider)

    items, errors = registry.collect_all_sources(
        SourceConfig(enabled_providers=["string_false"]),
        SourceQuery(recency_days=0),
        parallel=True,
    )

    assert errors == []
    assert [item.content for item in items] == ["serial despite truthy string"]
    assert calls == [True]
