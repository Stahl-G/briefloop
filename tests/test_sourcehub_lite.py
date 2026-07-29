"""Fail-closed contract for the retired public ``sources`` command family."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from multi_agent_brief.cli.main import main


ROOT = Path(__file__).parents[1]
RETIRED_SOURCE_ACTIONS = (
    "decide",
    "materialize-pack",
    "add-file",
    "add-rss",
    "add-web-search",
)
REQUIRED_RETIRED_HELP = (
    "Retired compatibility command",
    "unavailable",
    "runtime_command_unsupported",
    "init-web",
    "briefloop runtime continue --workspace <workspace>",
)
FORBIDDEN_ACTIVE_HELP = (
    "Source discovery and management",
    "Resolve llm_decide profile into concrete source candidates",
    "Run web search to discover sources",
    "Merge approved source_candidates.yaml into sources.yaml",
    "Materialize explicit durable source records",
    "Copy local text evidence files",
    "Register an RSS/Atom feed",
    "Register a runtime web-search handoff task",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _require_truthful_retired_help(label: str, output: str) -> None:
    normalized = " ".join(output.split())
    for required in REQUIRED_RETIRED_HELP:
        _require(required in normalized, f"{label}: missing help fragment {required!r}")
    for forbidden in FORBIDDEN_ACTIVE_HELP:
        _require(forbidden not in normalized, f"{label}: stale help {forbidden!r}")


def _workspace_file_bytes(workspace: Path) -> dict[str, bytes]:
    return {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }


def _create_workspace(root: Path, authority: str) -> Path:
    workspace = root / authority
    workspace.mkdir(parents=True)
    (workspace / "config.yaml").write_text(
        "project:\n  name: retired-sources-guard\n",
        encoding="utf-8",
    )
    (workspace / "sources.yaml").write_text(
        "source_strategy:\n  profile: conservative\n  enabled_providers: []\n",
        encoding="utf-8",
    )
    if authority == "sqlite":
        (workspace / "briefloop.db").write_bytes(b"authority-classification-only")
    elif authority == "legacy":
        control = workspace / "output" / "intermediate" / "runtime_manifest.json"
        control.parent.mkdir(parents=True)
        control.write_text("{}\n", encoding="utf-8")
    return workspace


def _args(action: str, workspace: Path) -> list[str]:
    config = str(workspace / "config.yaml")
    if action == "decide":
        return ["sources", action, "--config", config, "--search"]
    if action == "materialize-pack":
        return ["sources", action, "--config", config]
    if action == "add-file":
        return ["sources", action, "evidence.md", "--workspace", str(workspace)]
    if action == "add-rss":
        return [
            "sources",
            action,
            "https://example.com/feed.xml",
            "--workspace",
            str(workspace),
        ]
    if action == "add-web-search":
        return [
            "sources",
            action,
            "--query",
            "solar module prices latest",
            "--workspace",
            str(workspace),
        ]
    raise AssertionError(f"unknown retired sources action: {action}")


@pytest.mark.parametrize("action", RETIRED_SOURCE_ACTIONS)
@pytest.mark.parametrize(
    ("authority", "expected"),
    (
        ("fresh", "runtime_command_unsupported\n"),
        ("sqlite", "runtime_command_unsupported\n"),
        ("legacy", "legacy_workspace_unsupported\n"),
    ),
)
def test_retired_sources_public_cli_is_typed_and_zero_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    action: str,
    authority: str,
    expected: str,
) -> None:
    workspace = _create_workspace(tmp_path / action, authority)
    before = _workspace_file_bytes(workspace)

    rc = main(_args(action, workspace))
    captured = capsys.readouterr()

    _require(rc == 1, f"{action}/{authority}: unexpected return code {rc}")
    _require(captured.out == expected, f"{action}/{authority}: {captured.out!r}")
    _require(captured.err == "", f"{action}/{authority}: {captured.err!r}")
    _require(
        _workspace_file_bytes(workspace) == before,
        f"{action}/{authority}: workspace changed",
    )


@pytest.mark.parametrize(
    "args",
    (
        ["sources", "add-file", "evidence.md"],
        ["sources", "add-rss", "https://example.com/feed.xml"],
        ["sources", "add-web-search", "--query", "solar module prices latest"],
    ),
)
def test_retired_sources_no_workspace_bypass_reaches_fail_closed_handler(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    (tmp_path / "evidence.md").write_text("# Evidence\n", encoding="utf-8")
    before = _workspace_file_bytes(tmp_path)
    monkeypatch.chdir(tmp_path)

    rc = main(args)
    captured = capsys.readouterr()

    _require(rc == 1, f"unexpected return code {rc}")
    _require(captured.out == "runtime_command_unsupported\n", repr(captured.out))
    _require(captured.err == "", repr(captured.err))
    _require(_workspace_file_bytes(tmp_path) == before, "workspace changed")


def test_retired_sources_source_and_non_editable_wheel_parity(
    tmp_path: Path,
) -> None:
    build_root = tmp_path / "build-root"
    build_root.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", build_root / "README.md")
    shutil.copytree(ROOT / "src", build_root / "src")
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
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
    )
    _require(build.returncode == 0, build.stdout + build.stderr)
    wheels = sorted(wheel_dir.glob("briefloop-*.whl"))
    _require(len(wheels) == 1, f"expected one wheel, found {wheels}")
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        for deleted in ("sourcehub.py", "decider.py", "evidence_pack.py"):
            _require(
                not any(name.endswith(f"/sources/{deleted}") for name in names),
                f"deleted module survived in wheel: {deleted}",
            )
        archive.extractall(installed)

    def execute(label: str, package_root: Path, optimized: bool) -> list[object]:
        workspace = _create_workspace(
            tmp_path / f"{label}-{'opt' if optimized else 'normal'}",
            "fresh",
        )
        before = _workspace_file_bytes(workspace)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(package_root)
        prefix = [sys.executable, "-O"] if optimized else [sys.executable]
        results: list[object] = []
        help_commands = (
            ("sources", ["sources", "--help"]),
            *(
                (action, ["sources", action, "--help"])
                for action in RETIRED_SOURCE_ACTIONS
            ),
        )
        for help_label, help_args in help_commands:
            process = subprocess.run(
                [
                    *prefix,
                    "-m",
                    "multi_agent_brief.cli.main",
                    *help_args,
                ],
                cwd=tmp_path,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            _require(
                process.returncode == 0,
                f"{label}/{optimized}/{help_label}: help exit {process.returncode}",
            )
            _require(
                process.stderr == "",
                f"{label}/{optimized}/{help_label}: {process.stderr!r}",
            )
            _require_truthful_retired_help(
                f"{label}/{optimized}/{help_label}",
                process.stdout,
            )
            results.append(
                (
                    f"{help_label}-help",
                    process.returncode,
                    process.stdout,
                    process.stderr,
                )
            )
        for action in RETIRED_SOURCE_ACTIONS:
            process = subprocess.run(
                [
                    *prefix,
                    "-m",
                    "multi_agent_brief.cli.main",
                    *_args(action, workspace),
                ],
                cwd=tmp_path,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            results.append((action, process.returncode, process.stdout, process.stderr))
        _require(
            _workspace_file_bytes(workspace) == before,
            f"{label}/{optimized}: workspace changed",
        )
        return results

    for optimized in (False, True):
        source = execute("source", ROOT / "src", optimized)
        wheel = execute("wheel", installed, optimized)
        _require(source == wheel, f"source/wheel mismatch under -O={optimized}")
        for action, returncode, stdout, stderr in source:
            if action.endswith("-help"):
                _require(returncode == 0, f"{action}: return code {returncode}")
                _require_truthful_retired_help(action, stdout)
                _require(stderr == "", f"{action}: {stderr!r}")
                continue
            _require(returncode == 1, f"{action}: return code {returncode}")
            _require(stdout == "runtime_command_unsupported\n", f"{action}: {stdout!r}")
            _require(stderr == "", f"{action}: {stderr!r}")
