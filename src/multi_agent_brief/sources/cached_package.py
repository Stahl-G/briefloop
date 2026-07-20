"""Cached package provider: reads pre-collected source packages from local folders."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery


class CachedPackageProvider(SourceProvider):
    """Reads pre-collected source packages from a local cache directory.

    This supports OpenClaw-style workflows where an external agent or cron job
    continuously collects sources into a shared folder.
    """

    name = "cached_package"
    source_type = "cached"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []
        for path_str in config.get("paths", []):
            p = Path(path_str)
            if not p.exists():
                errors.append(f"cached_package: path not found: {path_str}")
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

        items: list[SourceItem] = []
        formats = config.get("formats", ["json", "md", "txt"])

        for path_str in config.get("paths", []):
            cache_path = Path(path_str)
            if not cache_path.exists():
                continue
            if cache_path.is_file():
                self._load_file(cache_path, formats, items)
            elif cache_path.is_dir():
                for f in sorted(cache_path.iterdir()):
                    if f.is_file() and not f.name.startswith("."):
                        self._load_file(f, formats, items)

        return items

    def _load_file(self, path: Path, formats: list[str], items: list[SourceItem]) -> None:
        ext = path.suffix.lower().lstrip(".")
        if ext not in formats:
            return
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return

        if ext == "json":
            self._load_json(path, content, items)
        elif ext in ("md", "txt"):
            self._load_text(path, content, items)

    def _load_json(self, path: Path, content: str, items: list[SourceItem]) -> None:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return

        # Handle list of items
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    items.append(self._dict_to_source_item(entry, path))
            return

        # Handle single object with items array
        if isinstance(data, dict):
            if isinstance(data.get("items"), list):
                for entry in data["items"]:
                    if isinstance(entry, dict):
                        items.append(self._dict_to_source_item(entry, path))
                    elif isinstance(entry, str):
                        items.append(SourceItem(
                            source_id=f"CACHE_{path.stem}",
                            source_name=path.stem,
                            source_type="cached",
                            title=entry[:80],
                            content=entry,
                            metadata={"path": str(path)},
                        ))
            else:
                items.append(self._dict_to_source_item(data, path))

    def _load_text(self, path: Path, content: str, items: list[SourceItem]) -> None:
        for line in content.splitlines():
            line = line.strip()
            if len(line) >= 25:
                items.append(SourceItem(
                    source_id=f"CACHE_{path.stem}_{len(items)}",
                    source_name=path.stem,
                    source_type="cached",
                    title=line[:80],
                    content=line,
                    metadata={"path": str(path)},
                ))

    def _dict_to_source_item(self, data: dict, path: Path) -> SourceItem:
        return SourceItem(
            source_id=data.get("source_id", f"CACHE_{path.stem}"),
            source_name=data.get("source_name", path.stem),
            source_type="cached",
            title=data.get("title", ""),
            content=data.get("content", data.get("snippet", "")),
            url=data.get("url", ""),
            published_at=data.get("published_at", ""),
            reliability=data.get("reliability", "medium"),
            metadata={**data.get("metadata", {}), "path": str(path)},
        )
