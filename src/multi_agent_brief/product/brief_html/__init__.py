"""Read-only three-page brief HTML (QP / LAJ / Improvement) static export."""

from .builder import (
    BRIEF_PAGES_BOUNDARY,
    BRIEF_PAGES_DATA_SCHEMA,
    BriefPagesError,
    build_brief_pages_data,
)
from .render import (
    BriefHtmlError,
    maybe_auto_open_brief_pages,
    render_brief_pages_html,
    verify_asset_provenance,
    write_brief_pages,
)

__all__ = [
    "BRIEF_PAGES_BOUNDARY",
    "BRIEF_PAGES_DATA_SCHEMA",
    "BriefHtmlError",
    "BriefPagesError",
    "build_brief_pages_data",
    "maybe_auto_open_brief_pages",
    "render_brief_pages_html",
    "verify_asset_provenance",
    "write_brief_pages",
]
