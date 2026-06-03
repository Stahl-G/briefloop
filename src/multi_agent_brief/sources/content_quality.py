"""Content quality filtering for web search results.

Filters boilerplate, cookie notices, navigation, table-of-contents,
and other low-quality snippets before they enter the Claim Ledger.
"""
from __future__ import annotations

import re

# Boilerplate phrases (lowercased for matching)
_BOILERPLATE_PHRASES: list[str] = [
    # English
    "cookie", "cookies", "privacy policy", "terms of use", "subscribe",
    "sign in", "sign up", "login", "newsletter", "advertisement",
    "all rights reserved", "table of contents", "contents",
    "download report", "contact us", "sitemap",
    # Chinese
    "隐私政策", "用户协议", "订阅", "登录", "注册", "广告", "免责声明", "目录", "联系我们",
]

# URL-only pattern: snippet is just a URL or mostly URLs
_URL_ONLY_PATTERN = re.compile(
    r"^https?://\S+$",
    re.IGNORECASE,
)

# Menu-like / navigation patterns
_MENU_SEPARATOR_PATTERN = re.compile(r"[\|>»]{2,}")
_PIPE_SEPARATED_PATTERN = re.compile(r"\s*\|\s*")
_PUNCTUATION_HEAVY_PATTERN = re.compile(r"[|>»\-•·]{3,}")

# Minimum useful snippet length
_MIN_SNIPPET_LENGTH = 20


def is_low_quality_snippet(text: str) -> bool:
    """Return True if a snippet is boilerplate, navigation, or low quality.

    Returns True for:
    - Empty or very short text
    - Cookie/privacy/subscription notices
    - URL-only snippets
    - Menu-like / table-of-contents patterns
    - Punctuation-heavy navigation text
    """
    if not text or not text.strip():
        return True

    cleaned = text.strip()
    lowered = cleaned.lower()

    # Too short
    if len(cleaned) < _MIN_SNIPPET_LENGTH:
        return True

    # Boilerplate phrase match
    for phrase in _BOILERPLATE_PHRASES:
        if phrase in lowered:
            return True

    # URL-only
    if _URL_ONLY_PATTERN.match(cleaned):
        return True

    # Menu-like: multiple pipe/arrow separators
    if _MENU_SEPARATOR_PATTERN.search(cleaned):
        return True

    # Pipe-separated TOC pattern: "Chapter 1 | Chapter 2 | Chapter 3"
    pipe_segments = _PIPE_SEPARATED_PATTERN.split(cleaned)
    if len(pipe_segments) >= 3:
        return True

    # Arrow-separated TOC pattern: "Section A > Section B > Section C"
    arrow_segments = re.split(r"\s*>\s*", cleaned)
    if len(arrow_segments) >= 3:
        return True

    # Punctuation-heavy (navigation / TOC)
    if _PUNCTUATION_HEAVY_PATTERN.search(cleaned):
        return True

    return False


def sanitize_snippet(text: str) -> str:
    """Clean a snippet for claim extraction.

    Strips boilerplate prefixes/suffixes, collapses whitespace.
    Returns empty string if the result is too short.
    """
    if not text:
        return ""

    cleaned = text.strip()
    # Collapse internal whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)

    if len(cleaned) < _MIN_SNIPPET_LENGTH:
        return ""

    return cleaned
