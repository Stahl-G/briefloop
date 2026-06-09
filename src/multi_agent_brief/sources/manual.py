"""Manual source provider: reads local files and manual URL entries."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery

# Known non-evidence input subdirectories that ManualProvider must block
NON_EVIDENCE_SUBDIRS = {"feedback", "instructions", "context"}


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
            # If path is present, check it exists
            path = src.get("path")
            if path and not Path(path).exists():
                errors.append(f"manual.sources[{i}] '{src.get('name', '?')}': path does not exist: {path}")
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        # Respect top-level enabled flag (B07)
        if config.get("enabled") is False:
            return []
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
            return [self._path_error_item(path, "path_not_found", src_config)]

        # ── hard gate: block non-evidence input subdirectories ──
        subdir = _input_subdir(path)
        if subdir in NON_EVIDENCE_SUBDIRS:
            return [self._path_error_item(
                path, "non_evidence_path_blocked", src_config,
                detail=f"Manual source points to input/{subdir}/ which is not evidence material. "
                       "Only input/sources/ (and input/ root for backward compatibility) "
                       "are valid evidence paths.",
            )]

        items: list[SourceItem] = []
        if path.is_file():
            item = self._load_file(path, src_config)
            if item:
                items.append(item)
            else:
                items.append(self._path_error_item(path, "unreadable_file", src_config))
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
                if not isinstance(parsed, dict):
                    return self._json_error_item(path, "invalid_json_structure", src_config)
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
                "input_subdir": _input_subdir(path),
            },
        )

    def _url_entry(self, url: str, src_config: dict) -> SourceItem:
        source_id = src_config.get("name", url).upper().replace(" ", "_").replace("-", "_")[:32]
        try:
            req = Request(url, headers={"User-Agent": "multi-agent-brief/0.7"})
            with urlopen(req, timeout=float(src_config.get("timeout", 10))) as response:
                raw = response.read(int(src_config.get("max_bytes", 2_000_000)))
                charset = response.headers.get_content_charset() or "utf-8"
            html_or_text = raw.decode(charset, errors="replace")
            content = _html_to_text(html_or_text)
            if not content.strip():
                return self._url_error_item(url, "empty_url_content", src_config)
            return SourceItem(
                source_id=source_id,
                source_name=src_config.get("name", url),
                source_type="manual_url",
                title=src_config.get("name", url),
                content=content,
                url=url,
                published_at=str(src_config.get("published_at", "")),
                language=src_config.get("language", ""),
                reliability=src_config.get("reliability", "medium"),
                metadata={
                    "category": src_config.get("category", ""),
                    "ingestion_status": "fetched",
                    "source_name": src_config.get("source_name", ""),
                    "search_intent": src_config.get("search_intent", ""),
                    "date_window_start": src_config.get("date_window_start", ""),
                    "date_window_end": src_config.get("date_window_end", ""),
                },
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            return self._url_error_item(url, type(exc).__name__, src_config)

    def _url_error_item(self, url: str, error_type: str, src_config: dict) -> SourceItem:
        source_id = src_config.get("name", url).upper().replace(" ", "_").replace("-", "_")[:32]
        return SourceItem(
            source_id=source_id,
            source_name=src_config.get("name", url),
            source_type="manual_error",
            title=f"Manual URL error: {error_type}",
            content=f"URL not accessible: {url} ({error_type})",
            url=url,
            language=src_config.get("language", ""),
            reliability="low",
            metadata={
                "category": src_config.get("category", ""),
                "error_type": error_type,
            },
        )

    def _json_error_item(self, path: Path, error_type: str, src_config: dict) -> SourceItem:
        source_id = path.stem.upper().replace("-", "_")
        return SourceItem(
            source_id=source_id,
            source_name=src_config.get("name", path.stem),
            source_type="manual_error",
            title=f"Manual JSON error: {error_type}",
            content=f"JSON source has unsupported top-level structure: {path}",
            reliability="low",
            metadata={
                "path": str(path),
                "error_type": error_type,
                "category": src_config.get("category", ""),
            },
        )

    def _path_error_item(self, path: Path, error_type: str, src_config: dict,
                         detail: str = "") -> SourceItem:
        """Create a diagnostic SourceItem for a missing or unreadable manual path."""
        name = src_config.get("name", str(path))
        source_id = name.upper().replace(" ", "_").replace("-", "_")[:32]
        content = detail or f"Path not accessible: {path} ({error_type})"
        return SourceItem(
            source_id=source_id,
            source_name=name,
            source_type="manual_error",
            title=f"Manual source error: {error_type}",
            content=content,
            reliability="low",
            metadata={
                "path": str(path),
                "error_type": error_type,
                "category": src_config.get("category", ""),
            },
        )


def _input_subdir(path: Path) -> str:
    """Determine which input/ subdirectory a file belongs to.

    Returns one of: "sources", "feedback", "instructions", "context", "root", "other".
    """
    resolved = path.resolve()
    parts = resolved.parts

    # Walk parts backwards looking for "input"
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "input":
            # "input" found at index i
            if i + 1 < len(parts):
                subdir = parts[i + 1]
                # If the path IS a directory, the subdir name is the last component
                # If the path is a file, the file is at parts[-1] and subdir is before it
                if resolved.is_dir():
                    # The subdir IS the last component
                    if subdir in {"sources", "feedback", "instructions", "context"}:
                        return subdir
                    return "other"
                # It's a file — check if subdir is one of the known subdirs
                if i + 1 == len(parts) - 1:
                    # The next part after "input" = the file itself → root level
                    return "root"
                if subdir in {"sources", "feedback", "instructions", "context"}:
                    return subdir
                return "other"
            return "root"
    return "other"


def _html_to_text(content: str) -> str:
    """Convert simple HTML or plain text into readable source text."""
    import re

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", content)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
