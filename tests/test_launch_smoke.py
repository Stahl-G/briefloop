"""Tests for the launch/demo smoke guard."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_launch_smoke.py"
DEMO_SCRIPT = SCRIPT.parent / "demo.py"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def test_demo_quality_panel_artifacts_are_deterministic(tmp_path):
    spec = importlib.util.spec_from_file_location("demo_script_test", DEMO_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    first = module.create_demo_workspace(output_dir=str(tmp_path / "first"))
    second = module.create_demo_workspace(output_dir=str(tmp_path / "second"))

    for rel_path in (
        "output/intermediate/quality_panel.json",
        "output/intermediate/quality_summary.md",
        "output/intermediate/quality_panel.html",
    ):
        assert _sha256_file(first / rel_path) == _sha256_file(second / rel_path)
