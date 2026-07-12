from __future__ import annotations

import json
import os
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
WINDOWS_NATIVE = os.name == "nt" and control_context._WINDOWS_DESCRIPTOR_READ_SUPPORTED


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
