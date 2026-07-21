"""Wheel packaging proof for the two new static-asset surfaces (C1)."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_WHEEL_MEMBERS = {
    "multi_agent_brief/product/brief_html/static/index.html",
    "multi_agent_brief/product/brief_html/static/app.js",
    "multi_agent_brief/product/brief_html/static/style.css",
    "multi_agent_brief/product/brief_html/static/provenance.json",
    "multi_agent_brief/product/brief_html/static/THIRD_PARTY_NOTICES.txt",
    "multi_agent_brief/product/init_web/static/index.html",
    "multi_agent_brief/product/init_web/static/app.js",
    "multi_agent_brief/product/init_web/static/style.css",
    "multi_agent_brief/product/init_web/static/provenance.json",
    "multi_agent_brief/product/init_web/static/THIRD_PARTY_NOTICES.txt",
}


def test_built_wheel_serves_all_static_assets(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel_path = next(wheel_dir.glob("briefloop-*.whl"))
    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())
        assert EXPECTED_WHEEL_MEMBERS <= names

        installed = tmp_path / "installed"
        installed.mkdir()
        archive.extractall(installed)

    script = (
        "from multi_agent_brief.product.brief_html import verify_asset_provenance;"
        "verify_asset_provenance();"
        "from multi_agent_brief.product.init_web.server import _verify_assets;"
        "_verify_assets();"
        "print('wheel-assets-ok')"
    )
    verify = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(installed), "PATH": "/usr/bin:/bin"},
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "wheel-assets-ok" in verify.stdout
