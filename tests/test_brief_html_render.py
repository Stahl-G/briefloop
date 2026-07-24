"""Render/packaging tests for the self-contained three-page brief HTML."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from multi_agent_brief.product.brief_html import (
    BriefHtmlError,
    maybe_auto_open_brief_pages,
    render_brief_pages_html,
    verify_asset_provenance,
    write_brief_pages,
)
from multi_agent_brief.product.brief_html.render import (
    html_report_auto_open_enabled,
    present_local_run,
    read_brief_asset,
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
            "groups": {key: [] for key in (
                "control", "source", "gates", "claims", "reader_clean", "closeout"
            )},
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


def test_render_is_self_contained_and_embeds_parseable_data() -> None:
    html = render_brief_pages_html(_minimal_data()).decode("utf-8")

    assert "<!-- brief-html:" not in html
    assert "http://" not in html and "https://" not in html
    assert '<script src=' not in html and "<link" not in html
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


def test_static_assets_have_frozen_provenance_and_mit_notice() -> None:
    provenance = verify_asset_provenance()
    assert provenance["prototype_source_path"] == "quality-panel"
    assert provenance["upstream_ppt_master_commit"] == (
        "619a954695d866dde970552db9fb1a6640c643c8"
    )
    assert provenance["license"] == "MIT"
    assert b"MIT License" in read_brief_asset("THIRD_PARTY_NOTICES.txt")


def test_runtime_assets_have_no_injection_or_external_surface() -> None:
    app = read_brief_asset("app.js")
    assert b"innerHTML" not in app
    assert b"eval(" not in app
    index = read_brief_asset("index.html")
    assert b"https://" not in index and b"http://" not in index


def test_read_brief_asset_rejects_unknown_names() -> None:
    with pytest.raises(BriefHtmlError):
        read_brief_asset("../secret")


def test_write_brief_pages_headless_and_browser_paths(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
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


def test_auto_open_config_flag_defaults_off_and_reads_true(tmp_path: Path) -> None:
    import yaml

    workspace = initialize_workspace(tmp_path / "ws")
    assert html_report_auto_open_enabled(workspace) is False
    assert maybe_auto_open_brief_pages(workspace) is None

    config_path = workspace / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["output"]["html_report"]["auto_open"] = True
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert html_report_auto_open_enabled(workspace) is True


@pytest.mark.parametrize("conflict", ["output_parent", "final_leaf"])
def test_presentation_never_follows_output_symlinks_or_writes_external_files(
    tmp_path: Path,
    conflict: str,
) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    database = workspace / "briefloop.db"
    store_before = database.read_bytes()
    external = tmp_path / "external"
    external.mkdir()
    outside = external / "brief_pages.html"
    outside.write_text("EXTERNAL-SENTINEL\n", encoding="utf-8")
    output = workspace / "output"
    if conflict == "output_parent":
        shutil.rmtree(output)
        output.symlink_to(external, target_is_directory=True)
    else:
        output.mkdir(exist_ok=True)
        (output / "brief_pages.html").symlink_to(outside)

    result = present_local_run(workspace, browser_open=lambda _uri: True)

    assert result == {
        "status": "projection_unavailable",
        "relative_path": None,
        "reason_code": "brief_html_projection_unavailable",
    }
    assert outside.read_text(encoding="utf-8") == "EXTERNAL-SENTINEL\n"
    assert database.read_bytes() == store_before
