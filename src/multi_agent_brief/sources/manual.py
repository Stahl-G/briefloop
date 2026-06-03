"""Manual source provider: reads local files and manual URL entries."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery


class ManualProvider(SourceProvider):
    """Loads sources from local files (md/txt/json) and manual URL entries."""

    name = "manual"
    source_type = "manual"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        sources = config.get("sources", [])
        for i, src in enumerate(sources):
            if not src.get("name"):
                errors.append(f"manual.sources[{i}]: missing 'name'")
            # Either path or url must be present
            if not src.get("path") and not src.get("url"):
                errors.append(f"manual.sources[{i}] '{src.get('name', '?')}': needs 'path' or 'url'")
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        sources: list[SourceItem] = []
        for src_config in config.get("sources", []):
            if src_config.get("enabled") is False:
                continue
            path = src_config.get("path")
            url = src_config.get("url")
            if path:
                sources.extend(self._load_local_path(Path(path), src_config))
            elif url:
                sources.append(self._url_entry(url, src_config))
        return sources

    def _load_local_path(self, path: Path, src_config: dict) -> list[SourceItem]:
        if not path.exists():
            return []
        items: list[SourceItem] = []
        if path.is_file():
            item = self._load_file(path, src_config)
            if item:
                items.append(item)
        elif path.is_dir():
            for f in sorted(path.iterdir()):
                if f.is_dir() or f.name.startswith(".") or f.name.lower() == "readme.md":
                    continue
                if f.suffix.lower() not in {".md", ".txt", ".json"}:
                    continue
                item = self._load_file(f, src_config)
                if item:
                    items.append(item)
        return items

    def _load_file(self, path: Path, src_config: dict) -> SourceItem | None:
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None

        source_id = path.stem.upper().replace("-", "_")
        title = path.stem.replace("_", " ").replace("-", " ").title()
        url = ""
        published_at = ""
        source_tier = ""

        claim_type = ""
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(content)
                url = str(parsed.get("source_url", ""))
                published_at = str(parsed.get("published_at", ""))
                source_tier = str(parsed.get("source_tier", ""))
                claim_type = str(parsed.get("claim_type", ""))
                if isinstance(parsed.get("items"), list):
                    content = "\n".join(str(item) for item in parsed["items"])
                elif parsed.get("content"):
                    content = str(parsed["content"])
            except json.JSONDecodeError:
                pass

        return SourceItem(
            source_id=source_id,
            source_name=src_config.get("name", path.stem),
            source_type="local_file",
            title=title,
            content=content,
            url=url,
            published_at=published_at,
            language=src_config.get("language", ""),
            reliability=src_config.get("reliability", "high"),
            metadata={
                "path": str(path),
                "source_tier": source_tier,
                "claim_type": claim_type,
                "category": src_config.get("category", ""),
            },
        )

    def _url_entry(self, url: str, src_config: dict) -> SourceItem:
        source_id = src_config.get("name", url).upper().replace(" ", "_").replace("-", "_")[:32]
        return SourceItem(
            source_id=source_id,
            source_name=src_config.get("name", url),
            source_type="manual_url",
            title=src_config.get("name", url),
            content=f"Manual URL source: {url}",
            url=url,
            language=src_config.get("language", ""),
            reliability=src_config.get("reliability", "medium"),
            metadata={
                "category": src_config.get("category", ""),
                "note": "URL registered but not fetched in Phase 1. Add content to input/ directory.",
                "ingestion_status": "placeholder",
                "requires_fetch": True,
            },
        )
