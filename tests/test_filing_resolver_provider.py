"""Tests for FilingResolverProvider."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from multi_agent_brief.sources.base import SourceQuery
from multi_agent_brief.sources.filing_resolver import FilingResolverProvider


@pytest.fixture(autouse=True)
def _set_sec_user_agent(monkeypatch):
    """Set SEC_USER_AGENT to avoid warnings from disclosure_filing_resolver."""
    monkeypatch.setenv("SEC_USER_AGENT", "test@example.com multi-agent-brief-workflow")


def _make_mock_dfr(evidence: MagicMock | None = None, sources: list | None = None):
    """Create a mock disclosure_filing_resolver module."""
    mod = ModuleType("disclosure_filing_resolver")
    if evidence is None:
        evidence = MagicMock()
        evidence.observations = []
        evidence.entity.legal_name = "Demo Holdings Ltd"
    if sources is None:
        sources = []
    mod.resolve_disclosure = MagicMock(return_value=evidence)
    mod.evidence_to_sources = MagicMock(return_value=sources)
    return mod


def _patch_dfr(mock_mod):
    """Inject mock module into sys.modules."""
    prev = sys.modules.get("disclosure_filing_resolver")
    sys.modules["disclosure_filing_resolver"] = mock_mod
    return prev


def _unpatch_dfr(prev):
    """Restore previous module state."""
    if prev is None:
        sys.modules.pop("disclosure_filing_resolver", None)
    else:
        sys.modules["disclosure_filing_resolver"] = prev


# --- validate_config ---


def test_validate_valid_entry():
    mock_mod = _make_mock_dfr()
    prev = _patch_dfr(mock_mod)
    try:
        provider = FilingResolverProvider()
        errors = provider.validate_config({
            "enabled": True,
            "tickers": [{"ticker": "DEMO"}],
        })
        identifier_errors = [e for e in errors if "at least one of" in e]
        assert identifier_errors == []
    finally:
        _unpatch_dfr(prev)


# --- collect ---


def test_collect_basic():
    sources = [
        {
            "title": "Demo Holdings Ltd — 6-K — financial statements",
            "url": "https://www.sec.gov/test.htm",
            "source_type": "filing",
            "date": "2026-03-15",
            "provider": "sec_edgar",
            "metadata": {
                "form": "6-K",
                "role": "financial_statements",
                "filename": "test.htm",
                "file_format": "html",
                "confidence": 0.9,
            },
        },
    ]
    mock_mod = _make_mock_dfr(sources=sources)
    prev = _patch_dfr(mock_mod)
    try:
        provider = FilingResolverProvider()
        items = provider.collect(SourceQuery(), {
            "enabled": True,
            "tickers": [{"ticker": "DEMO"}],
        })
        assert len(items) == 1
        item = items[0]
        assert item.source_type == "filing_resolver"
        assert "Demo Holdings" in item.title
        assert item.reliability == "high"
        assert item.metadata["source_tier"] == "T1"
    finally:
        _unpatch_dfr(prev)
