from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "scripts" / "install.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="install.sh requires a POSIX shell"
)


def _run_installer_dry(prefix: Path) -> str:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--prefix", str(prefix), "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_install_sh_creates_venv_when_missing(tmp_path: Path) -> None:
    output = _run_installer_dry(tmp_path / "prefix")

    assert "[3/5] Creating virtual environment..." in output
    assert "Recreating virtual environment" not in output


def test_install_sh_reuses_venv_that_meets_the_floor(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    venv_bin = prefix / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)

    output = _run_installer_dry(prefix)

    assert "[3/5] Reusing virtual environment." in output
    assert "rm -rf" not in output


def test_install_sh_recreates_venv_below_the_floor(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    venv_bin = prefix / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    stale_python = venv_bin / "python"
    stale_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stale_python.chmod(0o755)

    output = _run_installer_dry(prefix)

    assert "Recreating virtual environment" in output
    assert "rm -rf" in output
    assert "-m venv" in output


def test_install_sh_recreates_venv_with_missing_interpreter(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    (prefix / "venv").mkdir(parents=True)

    output = _run_installer_dry(prefix)

    assert "Recreating virtual environment" in output
    assert "rm -rf" in output
