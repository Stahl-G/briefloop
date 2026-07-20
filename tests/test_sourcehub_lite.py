"""Tests for the SourceHub Lite source setup commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.sources.registry import collect_all_sources, load_sources_config
from multi_agent_brief.sources.sourcehub import (
    SourceHubError,
    add_file_sources,
    add_rss_feed,
    add_web_search_handoff,
)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "config.yaml").write_text("project:\n  name: Test\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  profile: conservative\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "manual:\n"
        "  enabled: true\n"
        "  sources: []\n"
        "web_search:\n"
        "  enabled: false\n"
        "  mode: disabled\n",
        encoding="utf-8",
    )
    return ws


def _workspace_file_bytes(workspace: Path) -> dict[str, bytes]:
    return {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "subcommand_args",
    [
        ["add-file", "evidence.md"],
        ["add-rss", "https://example.com/feed.xml"],
        ["add-web-search", "--query", "solar module prices latest"],
    ],
)
def test_sources_public_cli_is_retired_with_typed_rejection_and_zero_writes(
    tmp_path: Path,
    capsys,
    subcommand_args: list[str],
) -> None:
    ws = _workspace(tmp_path)
    (tmp_path / "evidence.md").write_text("# Evidence\n", encoding="utf-8")
    before = _workspace_file_bytes(ws)

    rc = main(["sources", *subcommand_args, "--workspace", str(ws)])
    captured = capsys.readouterr()

    # LEGACY-DELETE: retired public `sources ...` operator surface; the
    # workspace authority guard rejects it before dispatch with zero writes.
    assert rc == 1
    assert captured.out == "runtime_command_unsupported\n"
    assert captured.err == ""
    assert _workspace_file_bytes(ws) == before
    assert not (ws / "briefloop.db").exists()


def test_sources_add_file_copies_text_source_without_external_path_leak(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside-user-folder"
    outside.mkdir()
    source = outside / "market-note.md"
    source.write_text("# Market Note\n\nPrice moved.\n", encoding="utf-8")

    payload = add_file_sources(
        workspace=ws,
        values=[str(source)],
        source_category="market_report",
    )
    assert payload["ok"] is True
    assert payload["source_count"] == 1
    output = json.dumps(payload, ensure_ascii=False)
    assert str(source) not in output
    assert "outside-user-folder" not in output

    sources_text = (ws / "sources.yaml").read_text(encoding="utf-8")
    assert str(source) not in sources_text
    assert "outside-user-folder" not in sources_text
    data = yaml.safe_load(sources_text)
    entry = data["manual"]["sources"][0]
    assert entry["path"].startswith("input/sources/sourcehub/")
    assert entry["category"] == "market_report"
    copied = ws / entry["path"]
    assert copied.read_text(encoding="utf-8").startswith("# Market Note")

    source_config = load_sources_config(ws / "sources.yaml")
    items, errors = collect_all_sources(source_config)
    assert errors == []
    assert len(items) == 1
    assert items[0].source_type == "local_file"


def test_sources_add_file_expands_home_globs(tmp_path: Path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    home = tmp_path / "home"
    docs = home / "docs"
    docs.mkdir(parents=True)
    (docs / "one.md").write_text("# One\n", encoding="utf-8")
    (docs / "two.txt").write_text("Two\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    payload = add_file_sources(workspace=ws, values=["~/docs/*"])
    assert payload["ok"] is True
    assert payload["source_count"] == 2
    data = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    paths = [item["path"] for item in data["manual"]["sources"]]
    assert len(paths) == 2
    assert all(path.startswith("input/sources/sourcehub/") for path in paths)


def test_sources_add_file_rejects_binary_without_writing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    binary = tmp_path / "deck.pdf"
    binary.write_bytes(b"%PDF-1.4")

    with pytest.raises(SourceHubError, match="text evidence only"):
        add_file_sources(workspace=ws, values=[str(binary)])
    assert not (ws / "input" / "sources" / "sourcehub").exists()
    data = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert data["manual"]["sources"] == []


def test_sources_add_rss_registers_feed(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    payload = add_rss_feed(
        workspace=ws,
        url="https://example.com/feed.xml",
        name="Industry Feed",
    )
    assert payload["ok"] is True
    data = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert "rss" in data["source_strategy"]["enabled_providers"]
    assert data["rss"]["enabled"] is True
    assert data["rss"]["feeds"][0]["url"] == "https://example.com/feed.xml"
    assert data["rss"]["feeds"][0]["category"] == "news_media"


def test_sources_add_rss_duplicate_updates_and_reports_persisted_feed(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    first = add_rss_feed(
        workspace=ws,
        url="https://example.com/feed.xml",
        name="Old Feed",
    )
    assert first["ok"] is True

    payload = add_rss_feed(
        workspace=ws,
        url="https://example.com/feed.xml",
        name="New Feed",
        source_category="market_report",
    )
    assert payload["updated"] is True
    assert payload["feed_count"] == 0
    assert payload["feed"]["name"] == "New Feed"
    assert payload["feed"]["source_category"] == "market_report"

    data = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert len(data["rss"]["feeds"]) == 1
    assert data["rss"]["feeds"][0]["name"] == "New Feed"
    assert data["rss"]["feeds"][0]["category"] == "market_report"


def test_sources_add_rss_rejects_invalid_url(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    with pytest.raises(SourceHubError, match=r"http\(s\)"):
        add_rss_feed(workspace=ws, url="not a url")
    data = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert "rss" not in data.get("source_strategy", {}).get("enabled_providers", [])


def test_sources_add_web_search_is_runtime_handoff_only(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    payload = add_web_search_handoff(
        workspace=ws,
        query="solar module prices latest",
        domains=["example.com"],
        recency_days=7,
    )
    assert payload["ok"] is True
    # LEGACY-DELETE: the retired CLI printed the handoff boundary line; the
    # direct payload carries the same boundary and non-claims.
    assert payload["boundary"] == "runtime_web_search_handoff_only"
    assert "no_python_web_search_execution" in payload["non_claims"]
    data = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert "web_search" in data["source_strategy"]["enabled_providers"]
    assert data["web_search"]["enabled"] is True
    assert data["web_search"]["mode"] == "runtime_tool"
    assert "backend" not in data["web_search"]
    assert data["web_search"]["search_tasks"][0]["handoff_only"] is True
    source_config = load_sources_config(ws / "sources.yaml")
    items, errors = collect_all_sources(source_config)
    assert items == []
    assert errors == []


def test_sources_add_web_search_duplicate_updates_and_reports_persisted_task(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    first = add_web_search_handoff(
        workspace=ws,
        query="solar prices",
        domains=["old.example"],
        max_results=10,
    )
    assert first["ok"] is True

    payload = add_web_search_handoff(
        workspace=ws,
        query="solar prices",
        domains=["new.example"],
        max_results=25,
    )
    assert payload["updated"] is True
    assert payload["task_count"] == 0
    assert payload["task"]["domains"] == ["new.example"]
    assert payload["task"]["max_results"] == 25

    data = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    tasks = data["web_search"]["search_tasks"]
    assert len(tasks) == 1
    assert tasks[0]["domains"] == ["new.example"]
    assert tasks[0]["max_results"] == 25


def test_sourcehub_bad_sources_yaml_fails_without_copying(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    (ws / "sources.yaml").write_text("source_strategy: [\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="while parsing"):
        add_file_sources(workspace=ws, values=[str(source)])
    assert not (ws / "input" / "sources" / "sourcehub").exists()
