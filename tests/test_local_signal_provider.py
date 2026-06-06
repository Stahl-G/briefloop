"""Tests for LocalSignalProvider — local_signal_samples.jsonl as first-class SourceItems."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.sources.base import SourceQuery
from multi_agent_brief.sources.local_signal import LocalSignalProvider


def _write_samples(path: Path, samples: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


def _valid_sample(**overrides) -> dict:
    base = {
        "sample_id": "S001",
        "task_id": "TASK_VN_SHOPEE_001",
        "platform": "Shopee",
        "market": "Vietnam",
        "language": "vi",
        "collected_at": "2026-06-01T10:00:00Z",
        "access_level": "public",
        "sample_type": "text_export",
        "contains_personal_data": False,
        "collector": "manual",
        "text_excerpt": "Sản phẩm này rất tốt, tôi đã mua lần thứ hai rồi.",
        "platform_group": "ecommerce",
        "signal_type": "consumer_discussion",
    }
    base.update(overrides)
    return base


class TestLocalSignalProvider:
    def test_returns_source_items(self, tmp_path):
        samples_file = tmp_path / "local_signal_samples.jsonl"
        _write_samples(samples_file, [_valid_sample(), _valid_sample(sample_id="S002", task_id="TASK_VN_FB_001")])

        provider = LocalSignalProvider()
        items = provider.collect(
            SourceQuery(),
            {"enabled": True, "samples_path": str(samples_file)},
        )

        assert len(items) == 2
        assert all(item.source_type == "local_signal" for item in items)
        assert items[0].content == "Sản phẩm này rất tốt, tôi đã mua lần thứ hai rồi."
        assert items[0].metadata["platform"] == "Shopee"
        assert items[0].metadata["market"] == "Vietnam"
        assert items[0].metadata["language"] == "vi"
        assert items[0].metadata["source_family"] == "local_signal"

    def test_returns_empty_for_missing_file(self, tmp_path):
        provider = LocalSignalProvider()
        items = provider.collect(
            SourceQuery(),
            {"enabled": True, "samples_path": str(tmp_path / "nonexistent.jsonl")},
        )
        assert items == []

    def test_returns_empty_when_disabled(self, tmp_path):
        samples_file = tmp_path / "local_signal_samples.jsonl"
        _write_samples(samples_file, [_valid_sample()])

        provider = LocalSignalProvider()
        items = provider.collect(
            SourceQuery(),
            {"enabled": False, "samples_path": str(samples_file)},
        )
        assert items == []

    def test_returns_empty_when_no_samples_path(self):
        provider = LocalSignalProvider()
        items = provider.collect(SourceQuery(), {"enabled": True})
        assert items == []

    def test_filters_personal_data(self, tmp_path):
        samples_file = tmp_path / "local_signal_samples.jsonl"
        _write_samples(samples_file, [
            _valid_sample(sample_id="S001"),
            _valid_sample(sample_id="S002", contains_personal_data=True, text_excerpt="Private message content"),
        ])

        provider = LocalSignalProvider()
        items = provider.collect(
            SourceQuery(),
            {"enabled": True, "samples_path": str(samples_file)},
        )

        assert len(items) == 1
        assert items[0].metadata["contains_personal_data"] is False

    def test_source_id_is_deterministic(self, tmp_path):
        sample = _valid_sample()
        samples_file = tmp_path / "local_signal_samples.jsonl"
        _write_samples(samples_file, [sample])

        provider = LocalSignalProvider()
        items1 = provider.collect(SourceQuery(), {"enabled": True, "samples_path": str(samples_file)})
        items2 = provider.collect(SourceQuery(), {"enabled": True, "samples_path": str(samples_file)})

        assert items1[0].source_id == items2[0].source_id
        assert items1[0].source_id.startswith("LS_")

    def test_reliability_is_low(self, tmp_path):
        samples_file = tmp_path / "local_signal_samples.jsonl"
        _write_samples(samples_file, [_valid_sample()])

        provider = LocalSignalProvider()
        items = provider.collect(SourceQuery(), {"enabled": True, "samples_path": str(samples_file)})

        assert items[0].reliability == "low"

    def test_validate_config_returns_empty(self):
        provider = LocalSignalProvider()
        assert provider.validate_config({}) == []


class TestLocalSignalProviderIntegration:
    """Integration: samples enter context.sources via pipeline."""

    def test_provider_in_registry(self):
        from multi_agent_brief.sources.registry import PROVIDER_CLASSES
        assert "local_signal" in PROVIDER_CLASSES

    def test_source_config_has_local_signal_field(self):
        from multi_agent_brief.sources.base import SourceConfig
        cfg = SourceConfig()
        assert hasattr(cfg, "local_signal")
        assert cfg.local_signal == {}

    def test_source_config_from_dict_parses_local_signal(self):
        from multi_agent_brief.sources.base import SourceConfig
        cfg = SourceConfig.from_dict({
            "local_signal": {"enabled": True},
            "source_strategy": {"enabled_providers": ["manual", "local_signal"]},
        })
        assert "local_signal" in cfg.enabled_providers
        assert cfg.local_signal == {"enabled": True}
