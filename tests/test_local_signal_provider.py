"""Tests for LocalSignalProvider — local_signal_samples.jsonl as first-class SourceItems."""
from __future__ import annotations

import json
from pathlib import Path

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
