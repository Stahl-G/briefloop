"""Render/packaging tests for the self-contained three-page brief HTML."""

from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest

from multi_agent_brief.product import projection_platform
from multi_agent_brief.product.brief_html import (
    BriefHtmlError,
    render_brief_pages_html,
    write_brief_pages,
)
from tests.helpers import initialize_workspace


def _minimal_data() -> dict[str, object]:
    return {
        "schema_version": "briefloop.brief_pages.data.v2",
        "generated_at": "2026-07-21T00:00:00Z",
        "boundary": "read-only",
        "workspace": {
            "run_id": "RUN-1",
            "runtime": "codex",
            "store_revision": 1,
            "authority": "sqlite_control_store",
        },
        "run": {
            "view_state": "finalized",
            "completed_stages": 10,
            "total_stages": 10,
            "current_stage": None,
            "current_role": None,
            "reason_code": "local_finalization_complete",
            "terminal_state": "finalized_local",
            "completion_target": "finalized_local",
        },
        "brief": {
            "status": "available",
            "view_state": "finalized",
            "terminal_state": "finalized_local",
            "completion_target": "finalized_local",
            "reason_code": "local_finalization_complete",
            "artifact": {
                "artifact_id": "reader_brief",
                "revision": 1,
                "sha256": "a" * 64,
            },
            "markdown": "# Reader brief\n\nExact text.\n",
            "boundary": "local only",
        },
        "quality": {
            "status": "unavailable",
            "reason_code": "final_reader_not_available",
            "boundary": "projection_only_not_gate_or_delivery_authority",
            "projection": {"ok": False},
            "groups": {
                key: []
                for key in (
                    "control",
                    "source",
                    "gates",
                    "claims",
                    "reader_clean",
                    "closeout",
                )
            },
            "actions": [],
        },
        "semantic": {
            "status": "not_run",
            "banner": "Experimental",
            "boundary": "advisory",
            "coverage": {
                "assessed_unit_count": 0,
                "finding_count": 0,
                "withheld_finding_count": 0,
                "abstention_count": 0,
            },
            "dimensions": [],
            "findings": [],
            "handoff_note": "note",
            "reason_codes": ["laj_not_run"],
            "disclaimer": "none",
        },
        "improvement": {
            "status": "unavailable",
            "reason_code": "pf_review_2_not_shipped",
            "recorded": [],
            "consumption_note": "note",
            "planned_note": "planned",
        },
    }


def _output_snapshot(workspace: Path) -> dict[str, tuple[int, int, int, bytes | None]]:
    output = workspace / "output"
    if not output.exists() and not output.is_symlink():
        return {}
    paths = [output, *output.rglob("*")]
    snapshot: dict[str, tuple[int, int, int, bytes | None]] = {}
    for path in paths:
        observed = path.lstat()
        snapshot[path.relative_to(workspace).as_posix()] = (
            observed.st_mode,
            observed.st_dev,
            observed.st_ino,
            path.read_bytes() if stat.S_ISREG(observed.st_mode) else None,
        )
    return snapshot


def test_render_is_self_contained_and_embeds_parseable_data() -> None:
    html = render_brief_pages_html(_minimal_data()).decode("utf-8")

    assert "<!-- brief-html:" not in html
    assert "http://" not in html and "https://" not in html
    assert "<script src=" not in html and "<link" not in html
    island = html.split('id="brief-pages-data">', 1)[1].split("</script>", 1)[0]
    payload = json.loads(island)
    assert payload["schema_version"] == "briefloop.brief_pages.data.v2"
    assert payload["workspace"]["run_id"] == "RUN-1"


def test_render_escapes_script_terminators_inside_data() -> None:
    data = _minimal_data()
    data["boundary"] = "x</script><script>alert(1)</script>"
    html = render_brief_pages_html(data).decode("utf-8")
    assert "</script><script>alert" not in html
    island = html.split('id="brief-pages-data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(island)["boundary"] == data["boundary"]


def test_write_brief_pages_headless_and_browser_paths(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    if not projection_platform.supports_retained_directory_publication():
        store_before = (workspace / "briefloop.db").read_bytes()
        output_before = _output_snapshot(workspace)

        with pytest.raises(
            BriefHtmlError,
            match="^brief_html_publication_unsupported$",
        ):
            write_brief_pages(workspace)

        assert (workspace / "briefloop.db").read_bytes() == store_before
        assert _output_snapshot(workspace) == output_before
        return

    headless = write_brief_pages(workspace)
    target = workspace / "output" / "brief_pages.html"
    assert headless["ok"] is True
    assert headless["browser_opened"] is False
    assert headless["reason_code"] == "brief_html_headless"
    assert target.is_file()
    assert b"brief-pages-data" in target.read_bytes()

    opened_uris: list[str] = []
    result = write_brief_pages(
        workspace,
        open_browser=True,
        browser_open=lambda uri: opened_uris.append(uri) or True,
    )
    assert result["browser_opened"] is True
    assert result["reason_code"] == "brief_html_opened"
    assert opened_uris and opened_uris[0].startswith("file://")

    failed = write_brief_pages(
        workspace,
        open_browser=True,
        browser_open=lambda uri: False,
    )
    assert failed["browser_opened"] is False
    assert failed["reason_code"] == "brief_html_browser_unavailable"
