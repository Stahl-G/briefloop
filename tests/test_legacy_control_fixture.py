"""Guards for the legacy-workspace fixture the retired-surface probes depend on.

LD2-3 deletes the runtime-state writers that retired-surface probes used to build
their precondition. `write_legacy_control_files` replaces that constructor, so it
carries its own verification: if the fixture stops producing a `legacy`
classification, every probe that relies on it degrades into a vacuous pass.
"""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.cli.authority_guard import (
    LEGACY_CONTROL_PATHS,
    classify_workspace_authority,
)
from multi_agent_brief.cli.main import main
from tests.helpers import write_legacy_control_files, write_minimal_workspace


def _workspace_file_bytes(workspace: Path) -> dict[str, bytes]:
    return {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
    }


def test_fixture_classifies_workspace_as_legacy(tmp_path: Path) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    assert classify_workspace_authority(ws).kind == "fresh"

    write_legacy_control_files(ws)

    assert classify_workspace_authority(ws).kind == "legacy"


def test_fixture_writes_every_guard_path_without_duplicating_literals(tmp_path: Path) -> None:
    ws = write_legacy_control_files(write_minimal_workspace(tmp_path / "ws"))

    for relative in LEGACY_CONTROL_PATHS:
        assert (ws / relative).is_file(), relative


def test_fixture_leaves_sqlite_authority_untouched(tmp_path: Path) -> None:
    """A workspace already carrying briefloop.db must stay classified as sqlite."""

    ws = write_minimal_workspace(tmp_path / "ws")
    (ws / "briefloop.db").write_bytes(b"")

    write_legacy_control_files(ws)

    assert classify_workspace_authority(ws).kind == "sqlite"


def test_fixture_drives_retired_surface_typed_rejection(tmp_path: Path, capsys) -> None:
    """End-to-end: the fixture is sufficient to exercise a retired public surface."""

    ws = write_legacy_control_files(write_minimal_workspace(tmp_path / "ws"))
    before = _workspace_file_bytes(ws)

    rc = main(["state", "check", "--strict", "--workspace", str(ws)])

    assert rc == 1
    assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
    assert _workspace_file_bytes(ws) == before
