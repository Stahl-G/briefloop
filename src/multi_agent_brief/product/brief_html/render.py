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
import secrets
import stat
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


def _replace_projection(workspace: Path, payload: bytes) -> Path:
    root_fd = -1
    output_fd = -1
    temporary = f".brief_pages.html.tmp-{secrets.token_hex(16)}"
    target = OUTPUT_RELATIVE_PATH.name
    try:
        root_fd = os.open(
            workspace,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.mkdir("output", 0o755, dir_fd=root_fd)
        except FileExistsError:
            pass
        output_info = os.stat("output", dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(output_info.st_mode):
            raise OSError("brief HTML output parent is not a directory")
        output_identity = (output_info.st_dev, output_info.st_ino)
        output_fd = os.open(
            "output",
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        opened_output = os.fstat(output_fd)
        if (opened_output.st_dev, opened_output.st_ino) != output_identity:
            raise OSError("brief HTML output parent changed")
        try:
            existing = os.stat(target, dir_fd=output_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise OSError("brief HTML target is not a regular file")
        temp_fd = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=output_fd,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(temp_fd, view)
                if written <= 0:
                    raise OSError("brief HTML short write")
                view = view[written:]
            os.fsync(temp_fd)
            created = os.fstat(temp_fd)
            if not stat.S_ISREG(created.st_mode) or created.st_size != len(payload):
                raise OSError("brief HTML temporary verification failed")
        finally:
            os.close(temp_fd)
        current_output = os.stat("output", dir_fd=root_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current_output.st_mode)
            or (current_output.st_dev, current_output.st_ino) != output_identity
        ):
            raise OSError("brief HTML output parent changed")
        if existing is not None:
            current_target = os.stat(target, dir_fd=output_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(current_target.st_mode)
                or (current_target.st_dev, current_target.st_ino)
                != (existing.st_dev, existing.st_ino)
            ):
                raise OSError("brief HTML target changed")
        else:
            try:
                os.stat(target, dir_fd=output_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise OSError("brief HTML target appeared")
        os.replace(
            temporary,
            target,
            src_dir_fd=output_fd,
            dst_dir_fd=output_fd,
        )
        final_fd = os.open(
            target,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=output_fd,
        )
        try:
            final_info = os.fstat(final_fd)
            if not stat.S_ISREG(final_info.st_mode):
                raise OSError("brief HTML final target is not regular")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(final_fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(final_fd)
        if _sha256_bytes(b"".join(chunks)) != _sha256_bytes(payload):
            raise OSError("brief HTML verification failed")
        os.fsync(output_fd)
    except OSError as exc:
        try:
            if output_fd >= 0:
                os.unlink(temporary, dir_fd=output_fd)
        except OSError:
            pass
        raise BriefHtmlError("brief_html_write_failed") from exc
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        if root_fd >= 0:
            os.close(root_fd)
    return workspace / OUTPUT_RELATIVE_PATH


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
    target = _replace_projection(root, rendered)
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
        "presentation": {
            "status": (
                "opened"
                if opened
                else ("browser_unavailable" if open_browser else "written")
            ),
            "relative_path": target.relative_to(root).as_posix(),
            "reason_code": reason,
        },
        "quality_status": data["quality"]["status"],
        "semantic_status": data["semantic"]["status"],
        "improvement_status": data["improvement"]["status"],
    }


def present_local_run(
    workspace: str | Path,
    *,
    browser_open: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Attempt the replaceable final HTML and return a typed relative fallback."""

    try:
        result = write_brief_pages(
            workspace,
            open_browser=True,
            browser_open=browser_open or webbrowser.open,
        )
        return dict(result["presentation"])
    except Exception:
        return {
            "status": "projection_unavailable",
            "relative_path": None,
            "reason_code": "brief_html_projection_unavailable",
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
        return present_local_run(workspace)
    except Exception:
        return {
            "status": "projection_unavailable",
            "relative_path": None,
            "reason_code": "brief_html_projection_unavailable",
        }


__all__ = [
    "BriefHtmlError",
    "OUTPUT_RELATIVE_PATH",
    "html_report_auto_open_enabled",
    "maybe_auto_open_brief_pages",
    "present_local_run",
    "read_brief_asset",
    "render_brief_pages_html",
    "verify_asset_provenance",
    "write_brief_pages",
]
