from __future__ import annotations

import re


DEFAULT_FILENAME_TEMPLATE = "{project_name}_{report_date}"


class _SafeFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def render_output_stem(
    template: str,
    tokens: dict[str, str],
    *,
    default_stem: str = "brief",
) -> str:
    """Render and sanitize a configured output filename stem."""
    chosen_template = template or DEFAULT_FILENAME_TEMPLATE
    try:
        raw = chosen_template.format_map(_SafeFormatDict(tokens))
    except ValueError:
        raw = tokens.get("project_name") or default_stem
    return sanitize_filename_stem(raw, default_stem=default_stem)


def sanitize_filename_stem(raw: str, *, default_stem: str = "brief") -> str:
    """Return a filesystem-safe filename stem while preserving readable text."""
    stem = raw.rsplit(".", 1)[0] if raw.lower().endswith((".md", ".docx")) else raw
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
    stem = re.sub(r"\s+", "_", stem)
    stem = re.sub(r"_+", "_", stem)
    stem = stem.strip(" ._")
    if not stem:
        stem = default_stem
    return stem[:120].rstrip(" ._") or default_stem
