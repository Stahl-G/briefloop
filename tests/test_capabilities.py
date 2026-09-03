"""Tests for the Capability Center: models, catalog, detect, and CI gate."""
from __future__ import annotations

from multi_agent_brief.capabilities.catalog import get_capability
from multi_agent_brief.capabilities.detect import assess_capability


def test_web_search_has_all_backends():
    ws = get_capability("web_search")
    assert ws is not None
    backend_ids = {o.id for o in ws.options}
    assert backend_ids == {"tavily", "exa", "brave", "firecrawl", "serper"}


def test_assess_capability_runtime_tool_ready_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    (tmp_path / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )

    status = assess_capability(
        "web_search",
        workspace_dir=tmp_path,
        enabled_providers={"web_search"},
    )

    assert status.state == "ENABLED_READY"
    assert "No search backend API key" not in status.notes


def test_features_prints_table(capsys):
    from multi_agent_brief.cli.main import main
    assert main(["features"]) == 0
    out = capsys.readouterr().out
    assert "Source Providers" in out
    assert "Manual Inputs" in out


def test_features_json_output(capsys):
    import json
    from multi_agent_brief.cli.main import main
    assert main(["features", "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) >= 14
    assert any(c["id"] == "manual" for c in data)
