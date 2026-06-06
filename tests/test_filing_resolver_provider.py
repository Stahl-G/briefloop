"""Tests for FilingResolverProvider."""
from __future__ import annotations

import os
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from multi_agent_brief.sources.base import SourceConfig, SourceQuery
from multi_agent_brief.sources.filing_resolver import FilingResolverProvider
from multi_agent_brief.sources.registry import (
    PROVIDER_CLASSES,
    collect_all_sources,
)


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
        evidence.entity.legal_name = "TOYO Co., Ltd"
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


# --- Provider registration ---

def test_provider_registered():
    assert "filing_resolver" in PROVIDER_CLASSES
    assert PROVIDER_CLASSES["filing_resolver"] is FilingResolverProvider


# --- validate_config ---

def test_validate_disabled_returns_empty():
    provider = FilingResolverProvider()
    errors = provider.validate_config({"enabled": False})
    assert errors == []


def test_validate_no_tickers_returns_error():
    provider = FilingResolverProvider()
    errors = provider.validate_config({"enabled": True})
    assert any("'tickers'" in e for e in errors)


def test_validate_empty_tickers_returns_error():
    provider = FilingResolverProvider()
    errors = provider.validate_config({"enabled": True, "tickers": []})
    assert any("'tickers'" in e for e in errors)


def test_validate_entry_without_identifier():
    provider = FilingResolverProvider()
    errors = provider.validate_config({
        "enabled": True,
        "tickers": [{"intent": "quarterly"}],
    })
    assert any("at least one of" in e for e in errors)


def test_validate_valid_entry():
    mock_mod = _make_mock_dfr()
    prev = _patch_dfr(mock_mod)
    try:
        provider = FilingResolverProvider()
        errors = provider.validate_config({
            "enabled": True,
            "tickers": [{"ticker": "TOYO"}],
        })
        identifier_errors = [e for e in errors if "at least one of" in e]
        assert identifier_errors == []
    finally:
        _unpatch_dfr(prev)


# --- collect ---

def test_collect_disabled_returns_empty():
    provider = FilingResolverProvider()
    items = provider.collect(SourceQuery(), {"enabled": False})
    assert items == []


def test_collect_no_tickers_returns_empty():
    provider = FilingResolverProvider()
    items = provider.collect(SourceQuery(), {"enabled": True})
    assert items == []


def test_collect_basic():
    sources = [
        {
            "title": "TOYO Co., Ltd — 6-K — financial statements",
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
            "tickers": [{"ticker": "TOYO"}],
        })
        assert len(items) == 1
        item = items[0]
        assert item.source_type == "filing_resolver"
        assert "TOYO" in item.title
        assert item.reliability == "high"
        assert item.metadata["source_tier"] == "T1"
    finally:
        _unpatch_dfr(prev)


def test_collect_xbrl_observations():
    obs = MagicMock(
        category="revenue",
        key="Revenues",
        value=150000000,
        unit="USD",
        period="2025-12-31",
        provenance={
            "form": "10-K",
            "filed": "2026-03-15",
            "accession": "0001213900-26-058577",
            "taxonomy": "us-gaap",
            "fiscal_year": "2025",
            "fiscal_period": "FY",
        },
    )
    evidence = MagicMock()
    evidence.observations = [obs]
    evidence.entity.legal_name = "TOYO Co., Ltd"

    mock_mod = _make_mock_dfr(evidence=evidence, sources=[])
    prev = _patch_dfr(mock_mod)
    try:
        provider = FilingResolverProvider()
        items = provider.collect(SourceQuery(), {
            "enabled": True,
            "tickers": [{"ticker": "TOYO"}],
            "include_xbrl": True,
        })
        assert len(items) == 1
        item = items[0]
        assert "revenue" in item.title.lower()
        assert item.metadata["claim_type"] == "number"
        assert item.metadata["observation_category"] == "revenue"
    finally:
        _unpatch_dfr(prev)


def test_collect_multiple_tickers():
    sources = [
        {
            "title": "Test Filing",
            "url": "https://example.com",
            "source_type": "filing",
            "date": "2026-01-01",
            "provider": "sec_edgar",
            "metadata": {"form": "10-K", "role": "annual_report"},
        },
    ]
    mock_mod = _make_mock_dfr(sources=sources)
    prev = _patch_dfr(mock_mod)
    try:
        provider = FilingResolverProvider()
        items = provider.collect(SourceQuery(), {
            "enabled": True,
            "tickers": [{"ticker": "TOYO"}, {"ticker": "TSLA"}],
        })
        assert mock_mod.resolve_disclosure.call_count == 2
        assert len(items) == 2
    finally:
        _unpatch_dfr(prev)


def test_collect_import_error(monkeypatch):
    """When disclosure_filing_resolver is not installed, returns error item."""
    # Ensure module is NOT in sys.modules
    prev = sys.modules.pop("disclosure_filing_resolver", None)
    # Also remove from import cache to force re-import
    import importlib
    if "disclosure_filing_resolver" in sys.modules:
        del sys.modules["disclosure_filing_resolver"]

    # Mock import to raise ImportError
    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def mock_import(name, *args, **kwargs):
        if name == "disclosure_filing_resolver":
            raise ImportError("No module named 'disclosure_filing_resolver'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)

    try:
        provider = FilingResolverProvider()
        items = provider.collect(SourceQuery(), {
            "enabled": True,
            "tickers": [{"ticker": "TOYO"}],
        })
        assert len(items) == 1
        assert items[0].source_type == "filing_resolver_error"
        # Error message from _resolve_one exception or validate_config
        assert items[0].content  # has some error message
    finally:
        if prev is not None:
            sys.modules["disclosure_filing_resolver"] = prev


def test_collect_resolve_exception():
    """When resolve_disclosure raises, returns error item."""
    mock_mod = _make_mock_dfr()
    mock_mod.resolve_disclosure.side_effect = Exception("SEC API down")
    prev = _patch_dfr(mock_mod)
    try:
        provider = FilingResolverProvider()
        items = provider.collect(SourceQuery(), {
            "enabled": True,
            "tickers": [{"ticker": "TOYO"}],
        })
        assert len(items) == 1
        assert items[0].source_type == "filing_resolver_error"
        assert "SEC API down" in items[0].content
    finally:
        _unpatch_dfr(prev)


# --- value formatting ---

def test_format_value_millions():
    assert FilingResolverProvider._format_value(150000000, "USD") == "$150.0M"


def test_format_value_thousands():
    assert FilingResolverProvider._format_value(50000, "USD") == "$50.0K"


def test_format_value_per_share():
    assert FilingResolverProvider._format_value(1.25, "USD/shares") == "$1.25/share"


def test_format_value_none():
    assert FilingResolverProvider._format_value(None, "USD") == "N/A"


def test_format_value_small():
    assert FilingResolverProvider._format_value(100, "USD") == "$100"


# --- SourceConfig integration ---

def test_source_config_has_filing_resolver():
    config = SourceConfig()
    assert hasattr(config, "filing_resolver")
    assert config.filing_resolver == {}


def test_source_config_from_dict_with_filing_resolver():
    data = {
        "source_strategy": {"enabled_providers": ["manual", "filing_resolver"]},
        "filing_resolver": {
            "enabled": True,
            "tickers": [{"ticker": "TOYO"}],
        },
    }
    config = SourceConfig.from_dict(data)
    assert config.filing_resolver["enabled"] is True
    assert config.filing_resolver["tickers"][0]["ticker"] == "TOYO"


# --- Source profiles ---

def test_filing_resolver_in_all_profiles():
    from multi_agent_brief.sources.base import SOURCE_PROFILES
    for profile_name, profile in SOURCE_PROFILES.items():
        assert "filing_resolver" in profile["allowed_types"], (
            f"filing_resolver missing from {profile_name} allowed_types"
        )


# --- Registry integration ---

def test_filing_resolver_in_config_map():
    """collect_all_sources passes filing_resolver config to the provider."""
    config = SourceConfig(
        enabled_providers=["filing_resolver"],
        filing_resolver={"enabled": True, "tickers": [{"ticker": "TEST"}]},
    )
    mock_mod = _make_mock_dfr(sources=[])
    prev = _patch_dfr(mock_mod)
    try:
        collect_all_sources(config)
        mock_mod.resolve_disclosure.assert_called_once()
    finally:
        _unpatch_dfr(prev)
