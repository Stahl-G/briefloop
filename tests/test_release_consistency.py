"""Tests for Release Consistency Gate."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_release_consistency.py"
VERSION_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_version_consistency.py"
RELEASE_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release.sh"


def _bash_executable() -> str:
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe",
        ]
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        if program_files_x86:
            candidates.extend([
                Path(program_files_x86) / "Git" / "bin" / "bash.exe",
                Path(program_files_x86) / "Git" / "usr" / "bin" / "bash.exe",
            ])
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "Git" / "bin" / "bash.exe")
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
    return shutil.which("bash") or "bash"


class TestCheckReleaseConsistency:
    def test_script_runs_clean(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--no-tag"],
            capture_output=True, text=True,
            cwd=str(SCRIPT.parent.parent),
        )
        assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"
        assert "ALL CHECKS PASSED" in result.stdout

    def test_strict_mode_runs(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--strict", "--no-tag"],
            capture_output=True, text=True,
            cwd=str(SCRIPT.parent.parent),
        )
        assert result.returncode == 0, f"Strict mode failed:\n{result.stdout}\n{result.stderr}"

    def test_version_consistency(self):
        """pyproject.toml and VERSION must agree without importing installed packages."""
        import re
        repo = SCRIPT.parent.parent
        pyproject = (repo / "pyproject.toml").read_text()
        v1 = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE).group(1)
        v2 = (repo / "VERSION").read_text(encoding="utf-8").strip()
        assert v1 == v2, f"pyproject={v1}, VERSION={v2}"


def test_check_version_consistency_fails_on_hermes_adapter_mismatch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_version_consistency_test", VERSION_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    root = tmp_path
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (root / "README.md").write_text("当前版本：**v1.2.3**\n", encoding="utf-8")
    (root / "README_en.md").write_text("Current version: **v1.2.3**\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")

    package_dir = root / "src" / "multi_agent_brief"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(
        'from importlib.metadata import version\n__version__ = version("multi-agent-brief-workflow")\n',
        encoding="utf-8",
    )
    hermes_dir = package_dir / "hermes"
    hermes_dir.mkdir()
    (hermes_dir / "adapter.py").write_text('version="v9.9.9"\nversion: 9.9.9\n', encoding="utf-8")

    skill_dir = root / ".agents" / "hermes-skills" / "multi-agent-brief-hermes"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("version: 1.2.3\n", encoding="utf-8")

    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "VERSION_FILE", root / "VERSION")

    assert module.main() == 1


def test_release_script_syntax():
    result = subprocess.run(
        [_bash_executable(), "-n", str(RELEASE_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
