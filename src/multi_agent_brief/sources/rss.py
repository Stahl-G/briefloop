"""RSS source provider: parses RSS/Atom feeds."""
from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery


class RssProvider(SourceProvider):
    """Fetches and parses RSS/Atom feeds."""

    name = "rss"
    source_type = "rss"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        feeds = config.get("feeds", [])
        for i, feed in enumerate(feeds):
            if not feed.get("url"):
                errors.append(f"rss.feeds[{i}]: missing 'url'")
            if not feed.get("name"):
                errors.append(f"rss.feeds[{i}]: missing 'name'")
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        items: list[SourceItem] = []
        for feed_config in config.get("feeds", []):
            if feed_config.get("enabled") is False:
                continue
            url = feed_config.get("url", "")
            if not url:
                continue
            try:
                items.extend(self._fetch_feed(url, feed_config, query))
            except Exception:
                # RSS fetch failures are non-fatal; log and continue
                pass
        return items

    def _fetch_feed(self, url: str, feed_config: dict, query: SourceQuery) -> list[SourceItem]:
        req = urllib.request.Request(url, headers={"User-Agent": "multi-agent-brief/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()

        root = ET.fromstring(raw)
        items: list[SourceItem] = []

        # Handle RSS 2.0
        for item_el in root.findall(".//item"):
            item = self._parse_rss_item(item_el, url, feed_config)
            if item and self._matches_query(item, query):
                items.append(item)

        # Handle Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry_el in root.findall(".//atom:entry", ns):
            item = self._parse_atom_entry(entry_el, url, feed_config, ns)
            if item and self._matches_query(item, query):
                items.append(item)

        return items[:query.max_results]

    def _parse_rss_item(self, el: ET.Element, feed_url: str, feed_config: dict) -> SourceItem | None:
        title = (el.findtext("title") or "").strip()
        link = (el.findtext("link") or "").strip()
        pub_date = (el.findtext("pubDate") or "").strip()
        description = (el.findtext("description") or "").strip()
        # Strip HTML tags from description
        description = re.sub(r"<[^>]+>", "", description).strip()

        if not title:
            return None

        source_id = _make_id(feed_config.get("name", feed_url), title)
        return SourceItem(
            source_id=source_id,
            source_name=feed_config.get("name", "RSS"),
            source_type="rss",
            title=title,
            content=description or title,
            url=link,
            published_at=pub_date,
            language=feed_config.get("language", ""),
            reliability=feed_config.get("reliability", "medium"),
            dedupe_key=link or title.lower(),
            metadata={"feed_url": feed_url, "category": feed_config.get("category", "")},
        )

    def _parse_atom_entry(self, el: ET.Element, feed_url: str, feed_config: dict, ns: dict) -> SourceItem | None:
        title = (el.findtext("atom:title", namespaces=ns) or "").strip()
        link_el = el.find("atom:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        published = (el.findtext("atom:published", namespaces=ns)
                     or el.findtext("atom:updated", namespaces=ns) or "").strip()
        summary = (el.findtext("atom:summary", namespaces=ns) or "").strip()
        summary = re.sub(r"<[^>]+>", "", summary).strip()

        if not title:
            return None

        source_id = _make_id(feed_config.get("name", feed_url), title)
        return SourceItem(
            source_id=source_id,
            source_name=feed_config.get("name", "RSS"),
            source_type="rss",
            title=title,
            content=summary or title,
            url=link,
            published_at=published,
            language=feed_config.get("language", ""),
            reliability=feed_config.get("reliability", "medium"),
            dedupe_key=link or title.lower(),
            metadata={"feed_url": feed_url, "category": feed_config.get("category", "")},
        )

    def _matches_query(self, item: SourceItem, query: SourceQuery) -> bool:
        if not query.keywords:
            return True
        text = f"{item.title} {item.content}".lower()
        return any(kw.lower() in text for kw in query.keywords)


def _make_id(source_name: str, title: str) -> str:
    import hashlib
    raw = f"{source_name}|{title}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    prefix = "".join(ch for ch in source_name.upper() if ch.isalnum())[:8] or "RSS"
    return f"{prefix}_{digest.upper()}"
