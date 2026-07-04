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
BUMP_VERSION_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bump_version.py"
RELEASE_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release.sh"
README_EN_POINTER = (
    "# BriefLoop English README\n\n"
    "The English README has moved to [README.md](README.md).\n\n"
    "This file is kept as a compatibility pointer so existing external links to\n"
    "`README_en.md` do not break.\n\n"
    "For the Simplified Chinese README, see [README.zh-CN.md](README.zh-CN.md).\n"
)


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
        assert "Product baseline readiness passes" in result.stdout
        assert "BriefLoop skill freshness passes" in result.stdout
        assert "Minimal comparative evaluation packet passes" in result.stdout
        assert "Launch demo smoke passes" in result.stdout
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

    def test_agent_config_failure_prints_stderr_and_dependency_hint(self, monkeypatch, capsys):
        spec = importlib.util.spec_from_file_location("release_consistency_test", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'yaml'\n",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert module.check_agent_configs() is False
        output = capsys.readouterr().out
        assert "missing dependency" in output
        assert "ModuleNotFoundError: No module named 'yaml'" in output
        assert "Agent configs out of sync" not in output

    def test_product_baseline_failure_prints_diagnostics(self, monkeypatch, capsys):
        spec = importlib.util.spec_from_file_location("release_consistency_test", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout='{"ok": false, "checks": [{"id": "new.industry-weekly", "status": "fail"}]}\n',
                stderr="baseline failed\n",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert module.check_product_baseline() is False
        output = capsys.readouterr().out
        assert "Product baseline readiness failed" in output
        assert '"ok": false' in output
        assert "baseline failed" in output

    def test_briefloop_skill_freshness_failure_prints_diagnostics(self, monkeypatch, capsys):
        spec = importlib.util.spec_from_file_location("release_consistency_test", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout='{"ok": false, "checks": [{"id": "canonical.references/version-matrix.md.freshness", "status": "fail"}]}\n',
                stderr="skill freshness failed\n",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert module.check_briefloop_skill_freshness() is False
        output = capsys.readouterr().out
        assert "BriefLoop skill freshness failed" in output
        assert "canonical.references/version-matrix.md.freshness" in output
        assert "skill freshness failed" in output

    def test_minimal_comparative_eval_failure_prints_diagnostics(self, monkeypatch, capsys):
        spec = importlib.util.spec_from_file_location("release_consistency_test", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout='{"ok": false, "errors": ["protocol.yaml schema_version mismatch"]}\n',
                stderr="comparative eval failed\n",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert module.check_minimal_comparative_eval() is False
        output = capsys.readouterr().out
        assert "Minimal comparative evaluation check failed" in output
        assert "protocol.yaml schema_version mismatch" in output
        assert "comparative eval failed" in output


    def test_launch_smoke_failure_prints_diagnostics(self, monkeypatch, capsys):
        spec = importlib.util.spec_from_file_location("release_consistency_test", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout='{"ok": false, "steps": [{"id": "demo_init", "ok": false}]}\n',
                stderr="launch smoke failed\n",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert module.check_launch_smoke() is False
        output = capsys.readouterr().out
        assert "Launch demo smoke failed" in output
        assert "demo_init" in output
        assert "launch smoke failed" in output


def test_release_consistency_rejects_stale_readme_en_with_pointer_sentence(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("release_consistency_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    root = tmp_path
    (root / "README_en.md").write_text(
        README_EN_POINTER + "\nCurrent version: **v0.1.0**\nOld full README body.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "REPO_ROOT", root)

    assert module.readme_en_is_pointer() is False


def test_check_version_consistency_fails_on_hermes_adapter_mismatch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_version_consistency_test", VERSION_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    root = tmp_path
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (root / "README.md").write_text("Current version: **v1.2.3**\n", encoding="utf-8")
    (root / "README_en.md").write_text(README_EN_POINTER, encoding="utf-8")
    (root / "README.zh-CN.md").write_text("当前版本：**v1.2.3**\n", encoding="utf-8")
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


def test_bump_version_does_not_rewrite_ruff_target_version(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bump_version_test", BUMP_VERSION_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    root = tmp_path
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'version = "0.0.1"\n'
        "\n"
        "[tool.ruff]\n"
        'target-version = "py39"\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text("Current version: **v0.0.1**\n", encoding="utf-8")
    (root / "README_en.md").write_text(README_EN_POINTER, encoding="utf-8")
    (root / "README.zh-CN.md").write_text("当前版本：**v0.0.1**\n", encoding="utf-8")
    adapter = root / "src" / "multi_agent_brief" / "hermes" / "adapter.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text('version="v0.0.1"\nversion: 0.0.1\nversion: str = "v0.0.1"\n', encoding="utf-8")
    skill = root / ".agents" / "hermes-skills" / "multi-agent-brief-hermes" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("version: 0.0.1\n", encoding="utf-8")
    formula = root / "Formula" / "multi-agent-brief.rb"
    formula.parent.mkdir()
    formula.write_text(
        'url "https://example.invalid/refs/tags/v0.0.1.tar.gz"\n'
        'sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
        'resource "pyyaml" do\n'
        '  url "https://example.invalid/pyyaml.tar.gz"\n'
        '  sha256 "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"\n'
        "end\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "VERSION_FILE", root / "VERSION")

    assert module.main() == 0
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "1.2.3"' in pyproject
    assert 'target-version = "py39"' in pyproject
    assert 'target-version = "1.2.3"' not in pyproject
    readme_en = (root / "README_en.md").read_text(encoding="utf-8")
    assert readme_en == README_EN_POINTER
    assert "Current version:" not in readme_en
    formula_text = formula.read_text(encoding="utf-8")
    assert "refs/tags/v1.2.3.tar.gz" in formula_text
    assert 'sha256 "0000000000000000000000000000000000000000000000000000000000000000"' in formula_text
    assert 'sha256 "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' in formula_text


def test_check_version_consistency_rejects_current_formula_before_tag(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_version_consistency_test", VERSION_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    root = tmp_path
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (root / "README.md").write_text("Current version: **v1.2.3**\n", encoding="utf-8")
    (root / "README_en.md").write_text(README_EN_POINTER, encoding="utf-8")
    (root / "README.zh-CN.md").write_text("当前版本：**v1.2.3**\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")

    package_dir = root / "src" / "multi_agent_brief"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(
        'from importlib.metadata import version\n__version__ = version("multi-agent-brief-workflow")\n',
        encoding="utf-8",
    )
    hermes_dir = package_dir / "hermes"
    hermes_dir.mkdir()
    (hermes_dir / "adapter.py").write_text('version="v1.2.3"\nversion: 1.2.3\n', encoding="utf-8")

    skill_dir = root / ".agents" / "hermes-skills" / "multi-agent-brief-hermes"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("version: 1.2.3\n", encoding="utf-8")
    formula = root / "Formula" / "multi-agent-brief.rb"
    formula.parent.mkdir()
    formula.write_text(
        'url "https://example.invalid/refs/tags/v1.2.3.tar.gz"\n'
        'sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "VERSION_FILE", root / "VERSION")

    assert module.main() == 1


def test_check_version_consistency_accepts_formula_when_tag_exists_on_origin(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_version_consistency_test", VERSION_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    root = tmp_path
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (root / "README.md").write_text("Current version: **v1.2.3**\n", encoding="utf-8")
    (root / "README_en.md").write_text(README_EN_POINTER, encoding="utf-8")
    (root / "README.zh-CN.md").write_text("当前版本：**v1.2.3**\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")

    package_dir = root / "src" / "multi_agent_brief"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(
        'from importlib.metadata import version\n__version__ = version("multi-agent-brief-workflow")\n',
        encoding="utf-8",
    )
    hermes_dir = package_dir / "hermes"
    hermes_dir.mkdir()
    (hermes_dir / "adapter.py").write_text('version="v1.2.3"\nversion: 1.2.3\n', encoding="utf-8")

    skill_dir = root / ".agents" / "hermes-skills" / "multi-agent-brief-hermes"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("version: 1.2.3\n", encoding="utf-8")
    formula = root / "Formula" / "multi-agent-brief.rb"
    formula.parent.mkdir()
    formula.write_text(
        'url "https://example.invalid/refs/tags/v1.2.3.tar.gz"\n'
        'sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "VERSION_FILE", root / "VERSION")
    monkeypatch.setattr(module, "_local_git_tag_exists", lambda tag: False)
    monkeypatch.setattr(module, "_remote_git_tag_exists", lambda tag: tag == "v1.2.3")

    assert module.main() == 0


def test_remote_git_tag_exists_requires_exact_ref(monkeypatch):
    spec = importlib.util.spec_from_file_location("check_version_consistency_test", VERSION_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Result:
        returncode = 0
        stdout = (
            "1111111111111111111111111111111111111111\trefs/tags/foo/refs/tags/v1.2.3\n"
            "2222222222222222222222222222222222222222\trefs/tags/v1.2.4\n"
        )
        stderr = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())

    assert module._remote_git_tag_exists("v1.2.3") is False


def test_check_version_consistency_rejects_stale_readme_en_with_pointer_sentence(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_version_consistency_test", VERSION_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    root = tmp_path
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (root / "README.md").write_text("Current version: **v1.2.3**\n", encoding="utf-8")
    (root / "README_en.md").write_text(
        README_EN_POINTER + "\nCurrent version: **v0.1.0**\nOld full README body.\n",
        encoding="utf-8",
    )
    (root / "README.zh-CN.md").write_text("当前版本：**v1.2.3**\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")
    package_dir = root / "src" / "multi_agent_brief"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(
        'from importlib.metadata import version\n__version__ = version("multi-agent-brief-workflow")\n',
        encoding="utf-8",
    )
    hermes_dir = package_dir / "hermes"
    hermes_dir.mkdir()
    (hermes_dir / "adapter.py").write_text('version="v1.2.3"\nversion: 1.2.3\n', encoding="utf-8")
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
