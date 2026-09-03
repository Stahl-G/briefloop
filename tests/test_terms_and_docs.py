"""Tests for terminology consistency and documented CLI commands."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_terminology() -> dict:
    path = ROOT / "configs" / "terminology.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_forbidden_terms_not_in_public_docs():
    """Forbidden terms must not appear in public docs."""
    config = _load_terminology()
    forbidden = config.get("forbidden_terms", [])
    doc_files = [
        ROOT / "README.md",
        ROOT / "README_en.md",
        ROOT / "README.zh-CN.md",
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
    ]
    for fpath in doc_files:
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8").lower()
        for term in forbidden:
            assert term.lower() not in text, (
                f"Forbidden term '{term}' found in {fpath.name}"
            )
