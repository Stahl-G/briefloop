from __future__ import annotations

import copy
import json
import os
import pickle
import subprocess
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
SECOND_CONTROL_RELATIVE_PATH = "output/intermediate/second.json"
WINDOWS_NATIVE = os.name == "nt" and control_context._WINDOWS_DESCRIPTOR_READ_SUPPORTED


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "output/intermediate").mkdir(parents=True)
    return workspace


def _control_path(workspace: Path) -> Path:
    return workspace / CONTROL_RELATIVE_PATH


def _write_control(workspace: Path, payload: object) -> bytes:
    return _write_relative_control(workspace, CONTROL_RELATIVE_PATH, payload)


def _write_relative_control(
    workspace: Path,
    relative_path: str,
    payload: object,
) -> bytes:
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    target = workspace / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return raw


def _create_windows_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"could not create Windows junction: {result.stderr or result.stdout}")


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


def test_session_stays_bound_after_preflight_when_workspace_is_replaced(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    trusted = _write_control(workspace, {"value": "trusted"})
    foreign = tmp_path / "foreign-workspace"
    foreign_raw = _write_control(foreign, {"value": "foreign"})
    moved = tmp_path / "opened-workspace"

    with control_context._open_workspace_control_read_session(workspace) as session:
        assert session.preflight(CONTROL_RELATIVE_PATH) is True
        workspace.rename(moved)
        foreign.rename(workspace)

        raw = session.read_bytes(CONTROL_RELATIVE_PATH)

    assert raw == trusted
    assert raw != foreign_raw


def test_session_stays_bound_when_workspace_is_replaced_between_reads(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    first = _write_control(workspace, {"value": "trusted-first"})
    second = _write_relative_control(
        workspace,
        SECOND_CONTROL_RELATIVE_PATH,
        {"value": "trusted-second"},
    )
    foreign = tmp_path / "foreign-workspace"
    _write_control(foreign, {"value": "foreign-first"})
    foreign_second = _write_relative_control(
        foreign,
        SECOND_CONTROL_RELATIVE_PATH,
        {"value": "foreign-second"},
    )
    moved = tmp_path / "opened-workspace"

    with control_context._open_workspace_control_read_session(workspace) as session:
        assert session.read_bytes(CONTROL_RELATIVE_PATH) == first
        workspace.rename(moved)
        foreign.rename(workspace)

        raw = session.read_bytes(SECOND_CONTROL_RELATIVE_PATH)

    assert raw == second
    assert raw != foreign_second


@pytest.mark.skipif(os.name == "nt", reason="Windows blocks ancestor rename")
def test_session_stays_bound_when_workspace_ancestor_is_replaced(
    tmp_path: Path,
) -> None:
    trusted_parent = tmp_path / "trusted-parent"
    workspace = trusted_parent / "workspace"
    trusted = _write_control(workspace, {"value": "trusted"})
    foreign_parent = tmp_path / "foreign-parent"
    foreign_workspace = foreign_parent / "workspace"
    foreign = _write_control(foreign_workspace, {"value": "foreign"})
    moved_parent = tmp_path / "opened-parent"

    with control_context._open_workspace_control_read_session(workspace) as session:
        trusted_parent.rename(moved_parent)
        foreign_parent.rename(trusted_parent)

        raw = session.read_bytes(CONTROL_RELATIVE_PATH)

    assert raw == trusted
    assert raw != foreign


@pytest.mark.skipif(not WINDOWS_NATIVE, reason="requires native Windows handles")
def test_windows_session_blocks_workspace_ancestor_replacement(
    tmp_path: Path,
) -> None:
    trusted_parent = tmp_path / "trusted-parent"
    workspace = trusted_parent / "workspace"
    trusted = _write_control(workspace, {"value": "trusted"})
    moved_parent = tmp_path / "opened-parent"

    with control_context._open_workspace_control_read_session(workspace) as session:
        with pytest.raises(PermissionError):
            trusted_parent.rename(moved_parent)

        raw = session.read_bytes(CONTROL_RELATIVE_PATH)

    assert raw == trusted


def test_session_optional_absence_is_only_missing_final_component(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    intermediate = workspace / "output/intermediate"
    external = tmp_path / "external"
    external.mkdir()

    with control_context._open_workspace_control_read_session(workspace) as session:
        assert session.preflight(CONTROL_RELATIVE_PATH, required=False) is False
        assert session.read_bytes(CONTROL_RELATIVE_PATH, required=False) is None

        intermediate.rmdir()
        if WINDOWS_NATIVE:
            _create_windows_junction(intermediate, external)
        else:
            intermediate.symlink_to(external, target_is_directory=True)
        try:
            with pytest.raises(RuntimeStateError) as exc_info:
                session.preflight(CONTROL_RELATIVE_PATH, required=False)
            assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
        finally:
            if WINDOWS_NATIVE and intermediate.exists():
                intermediate.rmdir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd matrix")
def test_session_descendant_opens_use_retained_posix_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    expected = _write_control(workspace, {"value": "trusted"})
    real_open = os.open
    calls: list[tuple[object, int | None, int]] = []

    with control_context._open_workspace_control_read_session(workspace) as session:

        def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
            opened = real_open(path, flags, mode, dir_fd=dir_fd)
            calls.append((path, dir_fd, opened))
            return opened

        monkeypatch.setattr(control_context.os, "open", tracking_open)
        assert session.preflight(CONTROL_RELATIVE_PATH) is True
        raw = session.read_bytes(CONTROL_RELATIVE_PATH)

    assert raw == expected
    assert [call[0] for call in calls] == [
        "output",
        "intermediate",
        "control.json",
        "output",
        "intermediate",
        "control.json",
    ]
    assert calls[0][1] is not None
    assert calls[1][1] == calls[0][2]
    assert calls[2][1] == calls[1][2]
    assert calls[3][1] == calls[0][1]
    assert calls[4][1] == calls[3][2]
    assert calls[5][1] == calls[4][2]


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor lifecycle")
def test_session_closes_resources_on_partial_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    real_open = os.open
    real_close = os.close
    opened: list[int] = []
    closed: list[int] = []

    def failing_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == workspace.name and dir_fd is not None:
            raise PermissionError("injected workspace acquisition failure")
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(fd)
        return fd

    def tracking_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(control_context.os, "open", failing_open)
    monkeypatch.setattr(control_context.os, "close", tracking_close)

    with pytest.raises(RuntimeStateError):
        control_context._open_workspace_control_read_session(workspace)

    assert opened
    assert sorted(opened) == sorted(closed)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor lifecycle")
def test_session_read_failure_close_is_idempotent_and_use_after_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    _write_control(workspace, {"value": "trusted"})
    session = control_context._open_workspace_control_read_session(workspace)
    real_close = os.close
    closed: list[int] = []

    def tracking_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    def failing_read(_fd: int, _size: int) -> bytes:
        raise OSError("injected read failure")

    monkeypatch.setattr(control_context.os, "close", tracking_close)
    monkeypatch.setattr(control_context.os, "read", failing_read)

    with pytest.raises(RuntimeStateError) as exc_info:
        session.read_bytes(CONTROL_RELATIVE_PATH)
    assert exc_info.value.details["reason_code"] == "control_file_read_failed"

    closed_before_root = len(closed)
    session.close()
    assert len(closed) == closed_before_root + 1
    session.close()
    assert len(closed) == closed_before_root + 1
    assert len(closed) == len(set(closed))

    with pytest.raises(RuntimeStateError) as exc_info:
        session.read_bytes(CONTROL_RELATIVE_PATH)
    assert exc_info.value.details["reason_code"] == "control_read_session_closed"


def test_session_cannot_be_forged_copied_or_serialized(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(TypeError):
        control_context._WorkspaceControlReadSession(
            _token=object(),
            backend="posix",
            display_root=workspace,
            root_resource=1,
        )

    session = control_context._open_workspace_control_read_session(workspace)
    try:
        with pytest.raises(TypeError):
            copy.copy(session)
        with pytest.raises(TypeError):
            copy.deepcopy(session)
        with pytest.raises(TypeError):
            pickle.dumps(session)
    finally:
        session.close()


def test_windows_session_descendants_use_retained_workspace_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[int, str, bool, int]] = []
    closed: list[int] = []
    handles = iter([201, 202, 203, 204, 205, 206, 207])

    monkeypatch.setattr(control_context, "_windows_open_root_handle", lambda _root: 100)
    monkeypatch.setattr(control_context, "_require_windows_handle_kind", lambda *_args, **_kwargs: None)

    def fake_relative_open(*, parent_handle: int, component: str, directory: bool):
        handle = next(handles)
        opened.append((parent_handle, component, directory, handle))
        return handle

    monkeypatch.setattr(control_context, "_windows_open_relative_handle", fake_relative_open)
    monkeypatch.setattr(control_context, "_windows_close_handle", closed.append)
    monkeypatch.setattr(
        control_context,
        "_windows_read_handle",
        lambda handle, *, path: b"trusted" if handle == 207 else b"wrong",
    )

    with control_context._open_workspace_control_read_session_windows(
        Path(r"C:\workspace")
    ) as session:
        assert session.preflight(CONTROL_RELATIVE_PATH) is True
        raw = session.read_bytes(CONTROL_RELATIVE_PATH)

    assert raw == b"trusted"
    assert opened == [
        (100, "workspace", True, 201),
        (201, "output", True, 202),
        (202, "intermediate", True, 203),
        (203, "control.json", False, 204),
        (201, "output", True, 205),
        (205, "intermediate", True, 206),
        (206, "control.json", False, 207),
    ]
    assert closed == [100, 204, 203, 202, 207, 206, 205, 201]


def test_windows_session_closes_partial_workspace_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    calls = 0

    monkeypatch.setattr(control_context, "_windows_open_root_handle", lambda _root: 100)
    monkeypatch.setattr(control_context, "_require_windows_handle_kind", lambda *_args, **_kwargs: None)

    def failing_relative_open(*, parent_handle: int, component: str, directory: bool):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 201
        raise OSError(5, "injected workspace acquisition failure")

    monkeypatch.setattr(control_context, "_windows_open_relative_handle", failing_relative_open)
    monkeypatch.setattr(control_context, "_windows_close_handle", closed.append)

    with pytest.raises(RuntimeStateError):
        control_context._open_workspace_control_read_session_windows(
            Path(r"C:\parent\workspace")
        )

    assert closed == [100, 201]


def test_windows_session_read_failure_closes_descendants_and_root_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    handles = iter([201, 202, 203, 204])

    monkeypatch.setattr(control_context, "_windows_open_root_handle", lambda _root: 100)
    monkeypatch.setattr(control_context, "_require_windows_handle_kind", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        control_context,
        "_windows_open_relative_handle",
        lambda **_kwargs: next(handles),
    )
    monkeypatch.setattr(control_context, "_windows_close_handle", closed.append)

    def failing_read(_handle: int, *, path: Path) -> bytes:
        raise RuntimeStateError(
            f"injected read failure: {path}",
            details={"reason_code": "control_file_read_failed"},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    monkeypatch.setattr(control_context, "_windows_read_handle", failing_read)

    with pytest.raises(RuntimeStateError) as exc_info:
        with control_context._open_workspace_control_read_session_windows(
            Path(r"C:\workspace")
        ) as session:
            session.read_bytes(CONTROL_RELATIVE_PATH)

    assert exc_info.value.details["reason_code"] == "control_file_read_failed"
    assert closed == [100, 204, 203, 202, 201]
    assert len(closed) == len(set(closed))


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
@pytest.mark.skipif(os.name == "nt", reason="covered by native reparse tests")
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
@pytest.mark.skipif(os.name == "nt", reason="covered by native junction tests")
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX os.open race hook")
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX os.open race hook")
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX os.open race hook")
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
    if WINDOWS_NATIVE:
        _create_windows_junction(intermediate, external)
    else:
        intermediate.symlink_to(external, target_is_directory=True)
    try:
        with pytest.raises(RuntimeStateError) as exc_info:
            read_workspace_control_bytes(
                workspace=workspace,
                relative_path=CONTROL_RELATIVE_PATH,
                required=False,
            )
        assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
    finally:
        if WINDOWS_NATIVE and intermediate.exists():
            intermediate.rmdir()


@pytest.mark.skipif(os.name == "nt", reason="covered by native reparse tests")
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
    monkeypatch.setattr(
        control_context,
        "_WINDOWS_DESCRIPTOR_READ_SUPPORTED",
        False,
    )

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


def test_descriptor_read_dispatches_to_windows_handle_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    calls: list[tuple[Path, tuple[str, ...], Path, bool]] = []

    def fake_windows_reader(*, display_root, parts, display_path, required):
        calls.append((display_root, parts, display_path, required))
        return b"windows-handle-bytes"

    monkeypatch.setattr(
        control_context,
        "_WINDOWS_DESCRIPTOR_READ_SUPPORTED",
        True,
    )
    monkeypatch.setattr(
        control_context,
        "_windows_workspace_selector",
        lambda value: Path(value).absolute(),
    )
    monkeypatch.setattr(
        control_context,
        "_read_workspace_control_bytes_windows",
        fake_windows_reader,
    )

    raw = read_workspace_control_bytes(
        workspace=workspace,
        relative_path=CONTROL_RELATIVE_PATH,
    )

    assert raw == b"windows-handle-bytes"
    assert calls == [
        (
            workspace.absolute(),
            ("output", "intermediate", "control.json"),
            _control_path(workspace).absolute(),
            True,
        )
    ]


@pytest.mark.parametrize(
    ("workspace", "expected_root", "expected_parts"),
    [
        (
            r"C:\Users\operator\workspace",
            "\\\\?\\C:\\",
            ("Users", "operator", "workspace"),
        ),
        (
            r"\\server\share\workspace",
            "\\\\?\\UNC\\server\\share\\",
            ("workspace",),
        ),
    ],
    ids=["drive", "unc"],
)
def test_windows_workspace_root_identity_is_canonical(
    workspace: str,
    expected_root: str,
    expected_parts: tuple[str, ...],
) -> None:
    root, parts = control_context._windows_workspace_root_and_parts(Path(workspace))

    assert root == expected_root
    assert parts == expected_parts


@pytest.mark.parametrize(
    "workspace",
    [
        r"C:workspace",
        r"\workspace",
        r"C:\workspace\..\foreign",
        r"\\?\C:\workspace",
        r"\\.\C:\workspace",
    ],
)
def test_windows_workspace_root_rejects_relative_or_namespace_escape(
    workspace: str,
) -> None:
    with pytest.raises(RuntimeStateError) as exc_info:
        control_context._windows_workspace_root_and_parts(Path(workspace))

    assert exc_info.value.details["reason_code"] == "control_workspace_root_invalid"


@pytest.mark.parametrize(
    "workspace",
    [
        r"C:workspace",
        r"\workspace",
        "workspace",
        r"~\workspace",
        r"C:\root\.\workspace",
        r"C:\root\..\workspace",
        r"\\?\C:\workspace",
        r"\\.\C:\workspace",
    ],
    ids=[
        "drive-relative",
        "root-relative",
        "plain-relative",
        "home-relative",
        "dot-component",
        "parent-component",
        "extended-namespace",
        "device-namespace",
    ],
)
def test_windows_public_reader_rejects_implicit_workspace_before_resolution(
    workspace: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment_calls: list[str] = []
    backend_calls: list[str] = []

    def forbidden_environment_lookup(_path):
        environment_calls.append("environment")
        raise AssertionError("invalid Windows selector reached environment lookup")

    def forbidden_backend(**_kwargs):
        backend_calls.append("backend")
        return b"wrong"

    monkeypatch.setattr(
        control_context,
        "_WINDOWS_DESCRIPTOR_READ_SUPPORTED",
        True,
    )
    monkeypatch.setattr(
        control_context._CONCRETE_PATH_TYPE,
        "absolute",
        forbidden_environment_lookup,
    )
    monkeypatch.setattr(
        control_context._CONCRETE_PATH_TYPE,
        "expanduser",
        forbidden_environment_lookup,
    )
    monkeypatch.setattr(
        control_context,
        "_read_workspace_control_bytes_windows",
        forbidden_backend,
    )

    for _ in range(2):
        with pytest.raises(RuntimeStateError) as exc_info:
            read_workspace_control_bytes(
                workspace=workspace,
                relative_path=CONTROL_RELATIVE_PATH,
            )
        assert exc_info.value.details["reason_code"] == (
            "control_workspace_root_invalid"
        )

    assert environment_calls == []
    assert backend_calls == []


@pytest.mark.parametrize(
    "workspace",
    [
        r"C:\workspace",
        r"\\server\share\workspace",
        Path(r"C:\workspace"),
    ],
    ids=["drive-str", "unc-str", "concrete-path"],
)
def test_windows_public_reader_accepts_canonical_workspace_selector(
    workspace: str | Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, tuple[str, ...], Path, bool]] = []

    def fake_windows_reader(*, display_root, parts, display_path, required):
        calls.append((display_root, parts, display_path, required))
        return b"canonical-windows-bytes"

    monkeypatch.setattr(
        control_context,
        "_WINDOWS_DESCRIPTOR_READ_SUPPORTED",
        True,
    )
    monkeypatch.setattr(
        control_context,
        "_read_workspace_control_bytes_windows",
        fake_windows_reader,
    )

    raw = read_workspace_control_bytes(
        workspace=workspace,
        relative_path=CONTROL_RELATIVE_PATH,
    )

    assert raw == b"canonical-windows-bytes"
    assert len(calls) == 1
    assert str(calls[0][0]) == str(workspace)
    assert calls[0][1] == ("output", "intermediate", "control.json")
    assert calls[0][3] is True


def test_windows_public_reader_rejects_polymorphic_workspace_without_coercion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coercions: list[str] = []
    backend_calls: list[str] = []

    class HostileString(str):
        def encode(self, *_args, **_kwargs):
            coercions.append("encode")
            return b"C:\\workspace"

    class HostilePathLike:
        def __fspath__(self):
            coercions.append("fspath")
            return r"C:\workspace"

    monkeypatch.setattr(
        control_context,
        "_WINDOWS_DESCRIPTOR_READ_SUPPORTED",
        True,
    )
    monkeypatch.setattr(
        control_context,
        "_read_workspace_control_bytes_windows",
        lambda **_kwargs: backend_calls.append("backend") or b"wrong",
    )

    for workspace in (HostileString(r"C:\workspace"), HostilePathLike()):
        with pytest.raises(RuntimeStateError) as exc_info:
            read_workspace_control_bytes(
                workspace=workspace,
                relative_path=CONTROL_RELATIVE_PATH,
            )
        assert exc_info.value.details["reason_code"] == (
            "control_workspace_root_invalid"
        )

    assert coercions == []
    assert backend_calls == []


@pytest.mark.parametrize(
    "parts",
    [
        ("output", "control.json:secret"),
        ("output", "trailing."),
        ("output", "trailing "),
    ],
)
def test_windows_control_parts_reject_aliasing_components(
    parts: tuple[str, ...],
) -> None:
    with pytest.raises(RuntimeStateError) as exc_info:
        control_context._validate_windows_path_parts(
            parts,
            display_path=Path("C:/workspace/output/control.json"),
            reason_code="control_file_relative_path_invalid",
        )

    assert exc_info.value.details["reason_code"] == (
        "control_file_relative_path_invalid"
    )


def test_windows_control_read_rejects_oversized_final_component_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = "control.json" + ("A" * 32768)
    native_calls: list[str] = []

    monkeypatch.setattr(
        control_context,
        "_windows_open_root_handle",
        lambda _path: native_calls.append("open") or 101,
    )
    monkeypatch.setattr(
        control_context,
        "_windows_read_handle",
        lambda _handle, *, path: native_calls.append("read") or b"wrong",
    )

    with pytest.raises(RuntimeStateError) as exc_info:
        control_context._read_workspace_control_bytes_windows(
            display_root=Path(r"C:\workspace"),
            parts=("output", "intermediate", oversized),
            display_path=Path(r"C:\workspace\output\intermediate") / oversized,
            required=True,
        )

    assert exc_info.value.details["reason_code"] == (
        "control_file_relative_path_invalid"
    )
    assert native_calls == []


def test_windows_control_read_rejects_oversized_workspace_component_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = "workspace" + ("A" * 32768)
    native_calls: list[str] = []

    monkeypatch.setattr(
        control_context,
        "_windows_open_root_handle",
        lambda _path: native_calls.append("open") or 101,
    )
    monkeypatch.setattr(
        control_context,
        "_windows_read_handle",
        lambda _handle, *, path: native_calls.append("read") or b"wrong",
    )

    with pytest.raises(RuntimeStateError) as exc_info:
        control_context._read_workspace_control_bytes_windows(
            display_root=Path("C:\\" + oversized),
            parts=("output", "intermediate", "control.json"),
            display_path=Path("C:\\" + oversized) / CONTROL_RELATIVE_PATH,
            required=True,
        )

    assert exc_info.value.details["reason_code"] == "control_workspace_root_invalid"
    assert native_calls == []


def test_windows_relative_open_defensively_rejects_unicode_length_narrowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = "control.json" + ("A" * 32768)
    native_calls: list[str] = []

    class FakeApi:
        def nt_create_file(self, *_args):
            native_calls.append("nt_create_file")
            return 0

    monkeypatch.setattr(control_context, "_windows_native_api", FakeApi)

    with pytest.raises(RuntimeStateError) as exc_info:
        control_context._windows_open_relative_handle(
            parent_handle=41,
            component=oversized,
            directory=False,
        )

    assert exc_info.value.details["reason_code"] == (
        "control_file_relative_path_invalid"
    )
    assert native_calls == []


def test_windows_relative_open_is_parent_handle_bound_and_no_follow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeApi:
        def nt_create_file(
            self,
            output_handle,
            desired_access,
            object_attributes,
            _io_status,
            _allocation_size,
            _file_attributes,
            _share_access,
            _create_disposition,
            create_options,
            _ea_buffer,
            _ea_length,
        ):
            attributes = object_attributes._obj
            name = attributes.ObjectName.contents
            calls.append(
                {
                    "root": attributes.RootDirectory,
                    "name": name.Buffer,
                    "desired_access": desired_access,
                    "create_options": create_options,
                }
            )
            output_handle._obj.value = 901
            return 0

        def rtl_nt_status_to_dos_error(self, status):
            raise AssertionError(f"unexpected NTSTATUS conversion: {status}")

    monkeypatch.setattr(control_context, "_windows_native_api", FakeApi)

    directory_handle = control_context._windows_open_relative_handle(
        parent_handle=41,
        component="intermediate",
        directory=True,
    )
    file_handle = control_context._windows_open_relative_handle(
        parent_handle=directory_handle,
        component="control.json",
        directory=False,
    )

    assert directory_handle == file_handle == 901
    assert calls[0]["root"] == 41
    assert calls[0]["name"] == "intermediate"
    assert calls[1]["root"] == 901
    assert calls[1]["name"] == "control.json"
    assert all(
        int(call["create_options"])
        & control_context._WINDOWS_FILE_OPEN_REPARSE_POINT
        for call in calls
    )
    assert int(calls[0]["create_options"]) & (
        control_context._WINDOWS_FILE_DIRECTORY_FILE
    )
    assert not int(calls[1]["create_options"]) & (
        control_context._WINDOWS_FILE_DIRECTORY_FILE
    )


@pytest.mark.skipif(not WINDOWS_NATIVE, reason="requires native Windows handles")
def test_windows_descriptor_read_closes_every_native_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    expected = _write_control(workspace, {"value": "trusted"})
    real_close = control_context._windows_close_handle
    closed: list[int] = []

    def tracking_close(handle: int) -> None:
        closed.append(handle)
        real_close(handle)

    monkeypatch.setattr(control_context, "_windows_close_handle", tracking_close)

    raw = read_workspace_control_bytes(
        workspace=workspace,
        relative_path=CONTROL_RELATIVE_PATH,
    )

    assert raw == expected
    assert len(closed) >= 5
    assert len(closed) == len(set(closed))
    workspace.rename(tmp_path / "closed-workspace")


@pytest.mark.skipif(not WINDOWS_NATIVE, reason="requires native Windows handles")
def test_windows_descriptor_read_rejects_workspace_junction(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    workspace = _workspace(real_parent)
    _write_control(workspace, {"value": "trusted"})
    junction = tmp_path / "workspace-parent-junction"
    _create_windows_junction(junction, real_parent)
    try:
        with pytest.raises(RuntimeStateError) as exc_info:
            read_workspace_control_bytes(
                workspace=junction / workspace.name,
                relative_path=CONTROL_RELATIVE_PATH,
            )
        assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
    finally:
        junction.rmdir()


@pytest.mark.skipif(not WINDOWS_NATIVE, reason="requires native Windows handles")
def test_windows_descriptor_read_rejects_final_reparse_as_optional_absence(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    target = _control_path(workspace)
    external = tmp_path / "external-target"
    external.mkdir()
    _create_windows_junction(target, external)
    try:
        with pytest.raises(RuntimeStateError) as exc_info:
            read_workspace_control_bytes(
                workspace=workspace,
                relative_path=CONTROL_RELATIVE_PATH,
                required=False,
            )
        assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
    finally:
        target.rmdir()


@pytest.mark.skipif(not WINDOWS_NATIVE, reason="requires native Windows handles")
def test_windows_descriptor_read_rejects_target_swap_at_native_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    target = _control_path(workspace)
    target.write_bytes(b'{"value":"trusted"}')
    external = tmp_path / "external-target"
    external.mkdir()
    external_secret = external / "secret.json"
    external_secret.write_bytes(b'{"value":"external-secret"}')
    before_external = _observation(external_secret)
    real_open = control_context._windows_open_relative_handle
    swapped = False

    def racing_open(*, parent_handle: int, component: str, directory: bool):
        nonlocal swapped
        if component == target.name and not directory and not swapped:
            target.unlink()
            _create_windows_junction(target, external)
            swapped = True
        return real_open(
            parent_handle=parent_handle,
            component=component,
            directory=directory,
        )

    monkeypatch.setattr(
        control_context,
        "_windows_open_relative_handle",
        racing_open,
    )

    try:
        with pytest.raises(RuntimeStateError) as exc_info:
            read_workspace_control_bytes(
                workspace=workspace,
                relative_path=CONTROL_RELATIVE_PATH,
            )
        assert swapped is True
        assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
        assert _observation(external_secret) == before_external
    finally:
        if target.exists():
            target.rmdir()


@pytest.mark.skipif(not WINDOWS_NATIVE, reason="requires native Windows handles")
def test_windows_descriptor_read_rejects_unopened_ancestor_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    _write_control(workspace, {"value": "trusted"})
    intermediate = workspace / "output/intermediate"
    moved = workspace / "output/intermediate-original"
    external = tmp_path / "external-intermediate"
    external.mkdir()
    (external / "control.json").write_bytes(b'{"value":"external-secret"}')
    before_external = _observation(external / "control.json")
    real_open = control_context._windows_open_relative_handle
    swapped = False

    def racing_open(*, parent_handle: int, component: str, directory: bool):
        nonlocal swapped
        if component == "intermediate" and directory and not swapped:
            intermediate.rename(moved)
            _create_windows_junction(intermediate, external)
            swapped = True
        return real_open(
            parent_handle=parent_handle,
            component=component,
            directory=directory,
        )

    monkeypatch.setattr(
        control_context,
        "_windows_open_relative_handle",
        racing_open,
    )
    try:
        with pytest.raises(RuntimeStateError) as exc_info:
            read_workspace_control_bytes(
                workspace=workspace,
                relative_path=CONTROL_RELATIVE_PATH,
            )
        assert swapped is True
        assert exc_info.value.details["reason_code"] == "control_file_path_unsafe"
        assert _observation(external / "control.json") == before_external
    finally:
        if intermediate.exists():
            intermediate.rmdir()
        if moved.exists():
            moved.rename(intermediate)


@pytest.mark.skipif(not WINDOWS_NATIVE, reason="requires native Windows handles")
def test_windows_descriptor_read_stays_bound_to_opened_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    trusted = _write_control(workspace, {"value": "trusted"})
    intermediate = workspace / "output/intermediate"
    moved = workspace / "output/intermediate-original"
    external = tmp_path / "external-intermediate"
    external.mkdir()
    external_target = external / "control.json"
    external_target.write_bytes(b'{"value":"external-secret"}')
    before_external = _observation(external_target)
    real_open = control_context._windows_open_relative_handle
    swapped = False

    def racing_open(*, parent_handle: int, component: str, directory: bool):
        nonlocal swapped
        if component == "control.json" and not directory and not swapped:
            intermediate.rename(moved)
            _create_windows_junction(intermediate, external)
            swapped = True
        return real_open(
            parent_handle=parent_handle,
            component=component,
            directory=directory,
        )

    monkeypatch.setattr(
        control_context,
        "_windows_open_relative_handle",
        racing_open,
    )
    try:
        raw = read_workspace_control_bytes(
            workspace=workspace,
            relative_path=CONTROL_RELATIVE_PATH,
        )
        assert swapped is True
        assert raw == trusted
        assert b"external-secret" not in raw
        assert _observation(external_target) == before_external
    finally:
        if intermediate.exists():
            intermediate.rmdir()
        if moved.exists():
            moved.rename(intermediate)
