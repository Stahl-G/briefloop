from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "scripts" / "install.sh"
SETUP_SH = ROOT / "scripts" / "setup.sh"
INSTALL_PS1 = ROOT / "scripts" / "install.ps1"
SETUP_PS1 = ROOT / "scripts" / "setup.ps1"
PWSH = shutil.which("pwsh")

requires_posix = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX shell required"
)
requires_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh is not available")


def _run_installer_dry(prefix: Path) -> str:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--prefix", str(prefix), "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@requires_posix
def test_install_sh_creates_venv_when_missing(tmp_path: Path) -> None:
    output = _run_installer_dry(tmp_path / "prefix")

    assert "[3/5] Creating virtual environment..." in output
    assert "Recreating virtual environment" not in output


@requires_posix
def test_install_sh_reuses_venv_that_meets_the_floor(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    venv_bin = prefix / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)

    output = _run_installer_dry(prefix)

    assert "[3/5] Reusing virtual environment." in output
    assert "rm -rf" not in output


@requires_posix
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


@requires_posix
def test_install_sh_recreates_venv_with_missing_interpreter(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    (prefix / "venv").mkdir(parents=True)

    output = _run_installer_dry(prefix)

    assert "Recreating virtual environment" in output
    assert "rm -rf" in output


def _stage_setup_sh(tmp_path: Path) -> Path:
    # setup.sh operates on the repository it lives in; stage an isolated copy
    # so the venv decision logic runs against tmp_path instead of the checkout.
    script_dir = tmp_path / "scripts"
    script_dir.mkdir(parents=True)
    staged = script_dir / "setup.sh"
    staged.write_text(SETUP_SH.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def _run_setup_sh_dry(workdir: Path) -> str:
    result = subprocess.run(
        ["bash", str(workdir / "scripts" / "setup.sh"), "--dry-run"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@requires_posix
def test_setup_sh_creates_venv_when_missing(tmp_path: Path) -> None:
    workdir = _stage_setup_sh(tmp_path)

    output = _run_setup_sh_dry(workdir)

    assert "[2/4] Creating virtual environment..." in output
    assert "Recreating virtual environment" not in output
    assert not (workdir / ".venv").exists()


@requires_posix
def test_setup_sh_reuses_venv_that_meets_the_floor(tmp_path: Path) -> None:
    workdir = _stage_setup_sh(tmp_path)
    venv_bin = workdir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)

    output = _run_setup_sh_dry(workdir)

    assert "[2/4] Virtual environment already exists." in output
    assert "Recreating virtual environment" not in output
    assert "rm -rf" not in output


@requires_posix
def test_setup_sh_recreates_venv_below_the_floor(tmp_path: Path) -> None:
    workdir = _stage_setup_sh(tmp_path)
    venv_bin = workdir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    stale_python = venv_bin / "python"
    stale_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stale_python.chmod(0o755)

    output = _run_setup_sh_dry(workdir)

    assert "Recreating virtual environment" in output
    assert "+ rm -rf .venv" in output


@requires_posix
def test_setup_sh_recreates_venv_with_missing_interpreter(tmp_path: Path) -> None:
    workdir = _stage_setup_sh(tmp_path)
    (workdir / ".venv").mkdir()

    output = _run_setup_sh_dry(workdir)

    assert "Recreating virtual environment" in output
    assert "+ rm -rf .venv" in output


def _run_install_ps1_dry(prefix: Path) -> str:
    result = subprocess.run(
        [
            PWSH or "pwsh",
            "-NoProfile",
            "-File",
            str(INSTALL_PS1),
            "-Prefix",
            str(prefix),
            "-BinDir",
            str(prefix / "bin"),
            "-NoPathUpdate",
            "-DryRun",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@requires_pwsh
def test_install_ps1_creates_venv_when_missing(tmp_path: Path) -> None:
    output = _run_install_ps1_dry(tmp_path / "prefix")

    assert "Creating virtual environment" in output
    assert "Recreating virtual environment" not in output


@requires_pwsh
def test_install_ps1_reuses_venv_that_meets_the_floor(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    venv_dir = prefix / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    if os.name != "nt":
        # PowerShell's filesystem provider treats the backslash in
        # Scripts\python.exe as a path separator even on POSIX. Mirror the
        # Windows venv layout while reusing the real POSIX venv interpreter.
        scripts_dir = venv_dir / "Scripts"
        scripts_dir.mkdir()
        (scripts_dir / "python.exe").symlink_to(venv_dir / "bin" / "python")

    output = _run_install_ps1_dry(prefix)

    assert "Reusing virtual environment." in output
    assert "Recreating virtual environment" not in output


@requires_pwsh
def test_install_ps1_recreates_venv_with_missing_interpreter(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    (prefix / "venv").mkdir(parents=True)

    output = _run_install_ps1_dry(prefix)

    assert "Recreating virtual environment" in output


def test_install_ps1_recreate_branch_enforces_the_312_floor() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")

    assert "Recreating virtual environment" in text
    assert "sys.version_info >= (3, 12)" in text
    assert "Remove-Item -Recurse -Force -Path $venvDir" in text


def test_setup_ps1_recreate_branch_enforces_the_312_floor() -> None:
    text = SETUP_PS1.read_text(encoding="utf-8")

    assert "Recreating virtual environment" in text
    assert "sys.version_info >= (3, 12)" in text
    assert "Remove-Item -Recurse -Force -Path $venvDir" in text
