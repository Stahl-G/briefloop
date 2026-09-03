from __future__ import annotations

from multi_agent_brief.sources.base import SourceConfig, SourceItem, SourceProvider, SourceQuery


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
