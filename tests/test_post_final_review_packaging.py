from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from multi_agent_brief.product.review_session.resources import (
    load_asset_provenance,
    read_review_asset,
)


def test_package_owned_assets_have_frozen_provenance_and_mit_notice() -> None:
    provenance = load_asset_provenance()
    assert provenance["prototype_source_path"] == "quality-panel"
    assert provenance["upstream_ppt_master_commit"] == "619a954695d866dde970552db9fb1a6640c643c8"
    assert provenance["license"] == "MIT"
    assert b"MIT License" in read_review_asset("THIRD_PARTY_NOTICES.txt")
    assert b"data-tab=\"quality\"" in read_review_asset("index.html")
    assert b"data-tab=\"semantic\"" in read_review_asset("index.html")
    assert b"data-tab=\"improvement\"" in read_review_asset("index.html")


def test_runtime_assets_do_not_depend_on_sibling_prototype_or_external_content() -> None:
    package_root = Path(str(files("multi_agent_brief.product.review_session")))
    source = b"\n".join(
        path.read_bytes()
        for path in package_root.rglob("*.py")
        if path.is_file()
    )
    assert b"/Users/yihongguo" not in source
    assert b"briefloop-prototypes" not in source
    index = read_review_asset("index.html")
    assert b"https://" not in index
    assert b"http://" not in index
    app = read_review_asset("app.js")
    assert b"innerHTML" not in app
    assert b"eval(" not in app
