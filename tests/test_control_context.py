from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import multi_agent_brief.orchestrator.runtime_state.control_context as control_context
from multi_agent_brief.orchestrator.runtime_state.control_context import (
    load_workspace_control_object,
    read_workspace_control_bytes,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)


CONTROL_RELATIVE_PATH = "output/intermediate/control.json"


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "output/intermediate").mkdir(parents=True)
    return workspace


def _control_path(workspace: Path) -> Path:
    return workspace / CONTROL_RELATIVE_PATH


def _write_control(workspace: Path, payload: object) -> bytes:
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    _control_path(workspace).write_bytes(raw)
    return raw


def _observation(path: Path) -> tuple[object, ...]:
    if path.is_symlink():
        stat_result = path.lstat()
        return ("symlink", path.readlink().as_posix(), stat_result.st_mtime_ns)
    if not path.exists():
        return ("missing",)
    stat_result = path.stat()
    if path.is_file():
        return ("file", path.read_bytes(), stat_result.st_mtime_ns)
    return ("other", stat_result.st_mode, stat_result.st_mtime_ns)


def test_descriptor_read_decodes_exact_acquired_json_bytes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    original = {"schema_version": "control.v1", "value": "trusted"}
    _write_control(workspace, original)
    real_reader = control_context.read_workspace_control_bytes
    calls: list[str] = []

    def acquire_then_replace(**kwargs):
        calls.append(str(kwargs["relative_path"]))
        raw = real_reader(**kwargs)
        _control_path(workspace).write_text(
            '{"schema_version":"control.v1","value":"replacement"}',
            encoding="utf-8",
        )
        return raw

    monkeypatch.setattr(
        control_context,
        "read_workspace_control_bytes",
        acquire_then_replace,
    )

    payload = load_workspace_control_object(
        workspace=workspace,
        relative_path=CONTROL_RELATIVE_PATH,
        expected_schema="control.v1",
    )

    assert payload == original
    assert calls == [CONTROL_RELATIVE_PATH]
    assert json.loads(_control_path(workspace).read_text(encoding="utf-8"))[
        "value"
    ] == "replacement"


@pytest.mark.parametrize("symlink_kind", ["target", "ancestor"])
def test_descriptor_read_rejects_preexisting_symlink_without_external_read(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    workspace = _workspace(tmp_path)
    target = _control_path(workspace)
    target.write_bytes(b'{"value":"trusted"}')
    external = tmp_path / "external.json"
    external.write_bytes(b'{"value":"external-secret"}')
    if symlink_kind == "target":
        target.unlink()
        target.symlink_to(external)
    else:
        intermediate = workspace / "output/intermediate"
        moved = tmp_path / "trusted-intermediate"
        intermediate.rename(moved)
        intermediate.symlink_to(tmp_path, target_is_directory=True)
    before_external = _observation(external)

    with pytest.raises(RuntimeStateError) as exc_info:
        read_workspace_control_bytes(
            workspace=workspace,
            relative_path=CONTROL_RELATIVE_PATH,
        )

    assert exc_info.value.error_code == E_TRANSACTION_INTEGRITY
    assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
    assert "external-secret" not in str(exc_info.value)
    assert "external-secret" not in repr(exc_info.value.details)
    assert _observation(external) == before_external


@pytest.mark.parametrize("symlink_kind", ["workspace", "workspace_ancestor"])
def test_descriptor_read_rejects_symlink_in_workspace_absolute_chain_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    symlink_kind: str,
) -> None:
    real_parent = tmp_path / "real"
    workspace = real_parent / "workspace"
    (workspace / "output/intermediate").mkdir(parents=True)
    trusted = _control_path(workspace)
    trusted.write_bytes(b'{"value":"trusted"}')
    if symlink_kind == "workspace":
        supplied_workspace = tmp_path / "workspace-alias"
        supplied_workspace.symlink_to(workspace, target_is_directory=True)
    else:
        alias_parent = tmp_path / "parent-alias"
        alias_parent.symlink_to(real_parent, target_is_directory=True)
        supplied_workspace = alias_parent / "workspace"
    before_trusted = _observation(trusted)
    read_calls: list[int] = []
    real_read = os.read

    def tracking_read(fd: int, size: int) -> bytes:
        read_calls.append(fd)
        return real_read(fd, size)

    monkeypatch.setattr(control_context.os, "read", tracking_read)

    with pytest.raises(RuntimeStateError) as exc_info:
        read_workspace_control_bytes(
            workspace=supplied_workspace,
            relative_path=CONTROL_RELATIVE_PATH,
        )

    assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
    assert read_calls == []
    assert _observation(trusted) == before_trusted


def test_descriptor_read_rejects_target_symlink_swap_at_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    target = _control_path(workspace)
    target.write_bytes(b'{"value":"trusted"}')
    external = tmp_path / "external.json"
    external.write_bytes(b'{"value":"external-secret"}')
    before_external = _observation(external)
    real_open = os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == target.name and dir_fd is not None and not swapped:
            target.unlink()
            target.symlink_to(external)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(control_context.os, "open", racing_open)

    with pytest.raises(RuntimeStateError) as exc_info:
        read_workspace_control_bytes(
            workspace=workspace,
            relative_path=CONTROL_RELATIVE_PATH,
        )

    assert swapped is True
    assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
    assert "external-secret" not in repr(exc_info.value.details)
    assert _observation(external) == before_external


def test_descriptor_read_stays_bound_when_opened_ancestor_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    trusted_raw = _write_control(workspace, {"value": "trusted"})
    output = workspace / "output"
    moved_output = tmp_path / "opened-output"
    external_output = tmp_path / "external-output"
    external_target = external_output / "intermediate/control.json"
    external_target.parent.mkdir(parents=True)
    external_target.write_bytes(b'{"value":"external-secret"}')
    before_external = _observation(external_target)
    real_open = os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == "intermediate" and dir_fd is not None and not swapped:
            output.rename(moved_output)
            output.symlink_to(external_output, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(control_context.os, "open", racing_open)

    raw = read_workspace_control_bytes(
        workspace=workspace,
        relative_path=CONTROL_RELATIVE_PATH,
    )

    assert swapped is True
    assert raw == trusted_raw
    assert b"external-secret" not in raw
    assert _observation(external_target) == before_external


def test_descriptor_read_stays_bound_when_opened_workspace_ancestor_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_parent = tmp_path / "workspace-parent"
    workspace = workspace_parent / "workspace"
    (workspace / "output/intermediate").mkdir(parents=True)
    trusted_raw = _write_control(workspace, {"value": "trusted"})
    moved_parent = tmp_path / "opened-workspace-parent"
    external_parent = tmp_path / "external-parent"
    external_target = external_parent / "workspace/output/intermediate/control.json"
    external_target.parent.mkdir(parents=True)
    external_target.write_bytes(b'{"value":"external-secret"}')
    before_external = _observation(external_target)
    real_open = os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == workspace.name and dir_fd is not None and not swapped:
            workspace_parent.rename(moved_parent)
            workspace_parent.symlink_to(external_parent, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(control_context.os, "open", racing_open)

    raw = read_workspace_control_bytes(
        workspace=workspace,
        relative_path=CONTROL_RELATIVE_PATH,
    )

    assert swapped is True
    assert raw == trusted_raw
    assert b"external-secret" not in raw
    assert _observation(external_target) == before_external


def test_descriptor_read_optional_absence_is_only_final_component_absence(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    assert read_workspace_control_bytes(
        workspace=workspace,
        relative_path=CONTROL_RELATIVE_PATH,
        required=False,
    ) is None

    external = tmp_path / "external"
    external.mkdir()
    intermediate = workspace / "output/intermediate"
    intermediate.rmdir()
    intermediate.symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeStateError) as exc_info:
        read_workspace_control_bytes(
            workspace=workspace,
            relative_path=CONTROL_RELATIVE_PATH,
            required=False,
        )

    assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"


def test_descriptor_read_optional_dangling_symlink_is_not_absence(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    target = _control_path(workspace)
    target.symlink_to(tmp_path / "missing-external.json")
    before = _observation(target)

    with pytest.raises(RuntimeStateError) as exc_info:
        read_workspace_control_bytes(
            workspace=workspace,
            relative_path=CONTROL_RELATIVE_PATH,
            required=False,
        )

    assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
    assert _observation(target) == before


@pytest.mark.parametrize("target_kind", ["directory", "fifo"])
def test_descriptor_read_rejects_non_regular_target_without_blocking(
    tmp_path: Path,
    target_kind: str,
) -> None:
    if target_kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable on this platform")
    workspace = _workspace(tmp_path)
    target = _control_path(workspace)
    if target_kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target)

    with pytest.raises(RuntimeStateError) as exc_info:
        read_workspace_control_bytes(
            workspace=workspace,
            relative_path=CONTROL_RELATIVE_PATH,
            required=False,
        )

    assert exc_info.value.details["reason_code"] == "control_file_not_regular"


@pytest.mark.parametrize(
    ("raw", "expected_schema", "message"),
    [
        (b"\xff", None, "not valid UTF-8"),
        (b'{"secret":', None, "not valid JSON"),
        (b"[]", None, "must contain an object"),
        (b'{"schema_version":"wrong","secret":"do-not-leak"}', "expected", "unsupported schema"),
    ],
    ids=["invalid-utf8", "malformed-json", "non-object", "wrong-schema"],
)
def test_descriptor_read_preserves_typed_json_failures(
    tmp_path: Path,
    raw: bytes,
    expected_schema: str | None,
    message: str,
) -> None:
    workspace = _workspace(tmp_path)
    _control_path(workspace).write_bytes(raw)

    with pytest.raises(RuntimeStateError) as exc_info:
        load_workspace_control_object(
            workspace=workspace,
            relative_path=CONTROL_RELATIVE_PATH,
            expected_schema=expected_schema,
        )

    assert exc_info.value.error_code == E_TRANSACTION_INTEGRITY
    assert message in str(exc_info.value)
    assert "do-not-leak" not in str(exc_info.value)


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "/absolute.json",
        "../escape.json",
        "output/./control.json",
        "output\\control.json",
        "C:relative-control.json",
    ],
)
def test_descriptor_read_rejects_noncanonical_relative_path(
    tmp_path: Path,
    relative_path: str,
) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(RuntimeStateError) as exc_info:
        read_workspace_control_bytes(
            workspace=workspace,
            relative_path=relative_path,
            required=False,
        )

    assert exc_info.value.details["reason_code"] == (
        "control_file_relative_path_invalid"
    )


def test_descriptor_read_rejects_noncanonical_workspace_root(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(RuntimeStateError) as exc_info:
        read_workspace_control_bytes(
            workspace=workspace / ".." / workspace.name,
            relative_path=CONTROL_RELATIVE_PATH,
        )

    assert exc_info.value.details["reason_code"] == "control_workspace_root_invalid"


def test_descriptor_read_fails_closed_when_platform_support_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    expected = _write_control(workspace, {"value": "must-not-fallback"})
    before = _observation(_control_path(workspace))
    monkeypatch.setattr(control_context, "_DESCRIPTOR_READ_SUPPORTED", False)

    with pytest.raises(RuntimeStateError) as exc_info:
        read_workspace_control_bytes(
            workspace=workspace,
            relative_path=CONTROL_RELATIVE_PATH,
        )

    assert exc_info.value.details["reason_code"] == (
        "control_file_descriptor_read_unsupported"
    )
    assert expected == before[1]
    assert _observation(_control_path(workspace)) == before
