"""Render the three-page data contract into ONE self-contained read-only HTML.

The static shell/assets are package data with frozen provenance hashes.  The
renderer inlines style/script, embeds the page data as JSON, and never adds
any command endpoint or write affordance: the export is always read-only.
"""

from __future__ import annotations

from importlib.resources import files
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
import webbrowser

import yaml

from .builder import build_brief_pages_data

_ROOT = "static"
_ASSETS = frozenset({"index.html", "app.js", "style.css", "THIRD_PARTY_NOTICES.txt"})
_STYLE_PLACEHOLDER = "<!-- brief-html:style -->"
_DATA_PLACEHOLDER = "<!-- brief-html:data -->"
_SCRIPT_PLACEHOLDER = "<!-- brief-html:script -->"
OUTPUT_RELATIVE_PATH = Path("output") / "brief_pages.html"


class BriefHtmlError(ValueError):
    """Raised when static assets, provenance, or rendering fail closed."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_brief_asset(name: str) -> bytes:
    if name not in _ASSETS:
        raise BriefHtmlError("brief_html_asset_invalid")
    return files(__package__).joinpath(_ROOT, name).read_bytes()


def verify_asset_provenance() -> dict[str, Any]:
    raw = files(__package__).joinpath(_ROOT, "provenance.json").read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise BriefHtmlError("brief_html_provenance_invalid") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        "briefloop.brief_html.asset_provenance.v1"
    ):
        raise BriefHtmlError("brief_html_provenance_invalid")
    production = payload.get("production_assets")
    expected_keys = {f"{name}_sha256" for name in _ASSETS}
    if not isinstance(production, dict) or set(production) != expected_keys:
        raise BriefHtmlError("brief_html_provenance_invalid")
    for key, expected in production.items():
        name = key[: -len("_sha256")]
        if expected != _sha256_bytes(read_brief_asset(name)):
            raise BriefHtmlError("brief_html_asset_hash_mismatch")
    return payload


def render_brief_pages_html(data: dict[str, Any]) -> bytes:
    """Compose the self-contained HTML; all dynamic bytes are escaped/JSON."""

    verify_asset_provenance()
    shell = read_brief_asset("index.html").decode("utf-8")
    for placeholder in (_STYLE_PLACEHOLDER, _DATA_PLACEHOLDER, _SCRIPT_PLACEHOLDER):
        if placeholder not in shell:
            raise BriefHtmlError("brief_html_shell_invalid")
    embedded = json.dumps(data, ensure_ascii=False, sort_keys=True).replace(
        "</", "<\\/"
    )
    html = shell.replace(
        _STYLE_PLACEHOLDER,
        "<style>\n" + read_brief_asset("style.css").decode("utf-8") + "\n</style>",
    ).replace(
        _DATA_PLACEHOLDER,
        '<script type="application/json" id="brief-pages-data">\n'
        + embedded
        + "\n</script>",
    ).replace(
        _SCRIPT_PLACEHOLDER,
        "<script>\n" + read_brief_asset("app.js").decode("utf-8") + "\n</script>",
    )
    return (html + "\n").encode("utf-8")


def _replace_projection(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise BriefHtmlError("brief_html_write_failed") from exc


def write_brief_pages(
    workspace: str | Path,
    *,
    open_browser: bool = False,
    laj_view_path: str | Path | None = None,
    browser_open: Callable[[str], bool] = webbrowser.open,
) -> dict[str, Any]:
    """Write the replaceable read-only HTML view; optionally open it locally."""

    root = Path(workspace).expanduser().resolve()
    data = build_brief_pages_data(root, laj_view_path=laj_view_path)
    rendered = render_brief_pages_html(data)
    target = root / OUTPUT_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    _replace_projection(target, rendered)
    opened = False
    reason = "brief_html_headless"
    if open_browser:
        try:
            opened = browser_open(target.resolve().as_uri()) is not False
        except Exception:
            opened = False
        reason = "brief_html_opened" if opened else "brief_html_browser_unavailable"
    return {
        "ok": True,
        "boundary": "read_only_static_export",
        "workspace": str(root),
        "brief_pages": target.relative_to(root).as_posix(),
        "brief_pages_sha256": _sha256_bytes(rendered),
        "open_requested": open_browser,
        "browser_opened": opened,
        "reason_code": reason,
        "quality_status": data["quality"]["status"],
        "semantic_status": data["semantic"]["status"],
        "improvement_status": data["improvement"]["status"],
    }


def html_report_auto_open_enabled(workspace: str | Path) -> bool:
    """Read the optional output.html_report.auto_open config flag (default off)."""

    root = Path(workspace).expanduser().resolve()
    try:
        config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(config, dict):
        return False
    output = config.get("output") or config.get("outputs") or {}
    if not isinstance(output, dict):
        return False
    report = output.get("html_report") or {}
    if not isinstance(report, dict):
        return False
    return report.get("auto_open") is True


def maybe_auto_open_brief_pages(workspace: str | Path) -> dict[str, Any] | None:
    """Best-effort post-finalize/delivery hook; never raises into the run."""

    try:
        if not html_report_auto_open_enabled(workspace):
            return None
        return write_brief_pages(workspace, open_browser=True)
    except Exception:
        return None


__all__ = [
    "BriefHtmlError",
    "OUTPUT_RELATIVE_PATH",
    "html_report_auto_open_enabled",
    "maybe_auto_open_brief_pages",
    "read_brief_asset",
    "render_brief_pages_html",
    "verify_asset_provenance",
    "write_brief_pages",
]
