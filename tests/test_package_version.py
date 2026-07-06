"""Tests for package version resolution."""

from __future__ import annotations

from pathlib import Path

import multi_agent_brief as mabw


def _write_checkout(root: Path, version: str = "9.9.9") -> Path:
    root.mkdir(parents=True)
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "briefloop"\n',
        encoding="utf-8",
    )
    package_dir = root / "src" / "multi_agent_brief"
    package_dir.mkdir(parents=True)
    return package_dir


def test_source_checkout_version_requires_import_from_src(monkeypatch, tmp_path):
    package_dir = _write_checkout(tmp_path / "repo")
    venv_package = (
        tmp_path
        / "repo"
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "multi_agent_brief"
    )
    venv_package.mkdir(parents=True)
    venv_init = venv_package / "__init__.py"
    venv_init.write_text("# installed copy\n", encoding="utf-8")

    monkeypatch.setattr(mabw, "__file__", str(venv_init))
    assert mabw._source_checkout_version() is None

    source_init = package_dir / "__init__.py"
    source_init.write_text("# source copy\n", encoding="utf-8")
    monkeypatch.setattr(mabw, "__file__", str(source_init))
    assert mabw._source_checkout_version() == "9.9.9"
