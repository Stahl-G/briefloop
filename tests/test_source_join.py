from __future__ import annotations

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
