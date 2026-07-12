"""Fail-closed loaders for runtime control records."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import stat
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)


_DESCRIPTOR_READ_SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
)
_WINDOWS_DESCRIPTOR_READ_SUPPORTED = os.name == "nt" and hasattr(ctypes, "WinDLL")

_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_FILE_READ_DATA = 0x00000001
_WINDOWS_FILE_TRAVERSE = 0x00000020
_WINDOWS_FILE_READ_ATTRIBUTES = 0x00000080
_WINDOWS_SYNCHRONIZE = 0x00100000
_WINDOWS_FILE_SHARE_READ = 0x00000001
_WINDOWS_FILE_SHARE_WRITE = 0x00000002
_WINDOWS_FILE_SHARE_DELETE = 0x00000004
_WINDOWS_FILE_OPEN = 0x00000001
_WINDOWS_FILE_DIRECTORY_FILE = 0x00000001
_WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_WINDOWS_FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
_WINDOWS_FILE_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_OBJ_CASE_INSENSITIVE = 0x00000040
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_WINDOWS_ERROR_FILE_NOT_FOUND = 2
_WINDOWS_ERROR_PATH_NOT_FOUND = 3
_WINDOWS_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_WINDOWS_UNICODE_STRING_MAX_BYTES = (1 << 16) - 1
_CONCRETE_PATH_TYPE = type(Path())
_CONTROL_SESSION_TOKEN = object()


class _WindowsUnicodeString(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    )


class _WindowsObjectAttributes(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_WindowsUnicodeString)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", wintypes.LPVOID),
        ("SecurityQualityOfService", wintypes.LPVOID),
    )


class _WindowsIoStatusBlock(ctypes.Structure):
    _fields_ = (
        ("StatusOrPointer", wintypes.LPVOID),
        ("Information", ctypes.c_size_t),
    )


class _WindowsFileAttributeTagInfo(ctypes.Structure):
    _fields_ = (
        ("FileAttributes", wintypes.DWORD),
        ("ReparseTag", wintypes.DWORD),
    )


class _WorkspaceControlReadSession:
    """One internal, non-transferable workspace-root read capability."""

    __slots__ = (
        "__backend",
        "__closed",
        "__display_root",
        "__root_resource",
    )

    def __init__(
        self,
        *,
        _token: object,
        backend: str,
        display_root: Path,
        root_resource: int,
    ) -> None:
        if (
            _token is not _CONTROL_SESSION_TOKEN
            or backend not in {"posix", "windows"}
            or type(display_root) is not _CONCRETE_PATH_TYPE
            or type(root_resource) is not int
        ):
            raise TypeError("Workspace control read sessions are factory-owned.")
        self.__backend = backend
        self.__closed = False
        self.__display_root = display_root
        self.__root_resource: int | None = root_resource

    def __enter__(self) -> "_WorkspaceControlReadSession":
        self.__require_open()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.close()
        return False

    def __copy__(self):
        raise TypeError("Workspace control read sessions cannot be copied.")

    def __deepcopy__(self, _memo):
        raise TypeError("Workspace control read sessions cannot be copied.")

    def __reduce__(self):
        raise TypeError("Workspace control read sessions cannot be serialized.")

    def __reduce_ex__(self, _protocol):
        raise TypeError("Workspace control read sessions cannot be serialized.")

    def close(self) -> None:
        """Close the retained workspace root exactly once."""

        if self.__closed:
            return
        resource = self.__root_resource
        self.__root_resource = None
        self.__closed = True
        if resource is None:
            return
        if self.__backend == "windows":
            _windows_close_handle(resource)
            return
        try:
            os.close(resource)
        except OSError:
            pass

    def preflight(self, relative_path: str, *, required: bool = True) -> bool:
        """Validate one relative control target through the retained root."""

        parts, display_path = self.__relative_target(relative_path)
        present, _raw = self.__acquire(
            parts=parts,
            display_path=display_path,
            required=required,
            read_contents=False,
        )
        return present

    def read_bytes(
        self,
        relative_path: str,
        *,
        required: bool = True,
    ) -> bytes | None:
        """Read one relative control target through the retained root."""

        parts, display_path = self.__relative_target(relative_path)
        present, raw = self.__acquire(
            parts=parts,
            display_path=display_path,
            required=required,
            read_contents=True,
        )
        if not present:
            return None
        if raw is None:
            raise _control_read_error(
                display_path,
                reason_code="control_file_read_failed",
                reason="Control file bytes were not returned by the session backend.",
            )
        return raw

    def load_object(
        self,
        relative_path: str,
        *,
        expected_schema: str | None = None,
        required: bool = True,
    ) -> dict[str, Any] | None:
        """Read and decode one JSON object through the retained root."""

        parts, display_path = self.__relative_target(relative_path)
        present, raw = self.__acquire(
            parts=parts,
            display_path=display_path,
            required=required,
            read_contents=True,
        )
        if not present:
            return None
        if raw is None:
            raise _control_read_error(
                display_path,
                reason_code="control_file_read_failed",
                reason="Control file bytes were not returned by the session backend.",
            )
        return _decode_control_object_bytes(
            raw,
            path=display_path,
            expected_schema=expected_schema,
        )

    def __relative_target(self, relative_path: str) -> tuple[tuple[str, ...], Path]:
        self.__require_open()
        parts = _workspace_control_relative_parts(relative_path)
        display_path = self.__display_root.joinpath(*parts)
        if self.__backend == "windows":
            _validate_windows_path_parts(
                parts,
                display_path=display_path,
                reason_code="control_file_relative_path_invalid",
            )
        return parts, display_path

    def __acquire(
        self,
        *,
        parts: tuple[str, ...],
        display_path: Path,
        required: bool,
        read_contents: bool,
    ) -> tuple[bool, bytes | None]:
        resource = self.__require_open()
        if self.__backend == "windows":
            return _acquire_workspace_control_file_windows(
                workspace_handle=resource,
                parts=parts,
                display_root=self.__display_root,
                display_path=display_path,
                required=required,
                read_contents=read_contents,
            )
        return _acquire_workspace_control_file_posix(
            workspace_fd=resource,
            parts=parts,
            display_root=self.__display_root,
            display_path=display_path,
            required=required,
            read_contents=read_contents,
        )

    def __require_open(self) -> int:
        resource = self.__root_resource
        if self.__closed or resource is None:
            raise _control_read_error(
                self.__display_root,
                reason_code="control_read_session_closed",
                reason="Workspace control read session is closed.",
            )
        return resource


def _open_workspace_control_read_session(
    workspace: str | Path,
) -> _WorkspaceControlReadSession:
    """Acquire one internal workspace-root capability for repeated reads."""

    if _WINDOWS_DESCRIPTOR_READ_SUPPORTED:
        display_root = _windows_workspace_selector(workspace)
        return _open_workspace_control_read_session_windows(display_root)
    display_root = Path(workspace).expanduser().absolute()
    if not _DESCRIPTOR_READ_SUPPORTED:
        raise _control_read_error(
            display_root,
            reason_code="control_file_descriptor_read_unsupported",
            reason="Descriptor-bound no-follow control reads are unavailable.",
        )
    return _open_workspace_control_read_session_posix(display_root)


def read_workspace_control_bytes(
    *,
    workspace: str | Path,
    relative_path: str,
    required: bool = True,
) -> bytes | None:
    """Acquire one regular control file through a workspace-rooted descriptor chain."""

    parts = _workspace_control_relative_parts(relative_path)
    if _WINDOWS_DESCRIPTOR_READ_SUPPORTED:
        display_root = _windows_workspace_selector(workspace)
        display_path = display_root.joinpath(*parts)
        return _read_workspace_control_bytes_windows(
            display_root=display_root,
            parts=parts,
            display_path=display_path,
            required=required,
        )
    display_root = Path(workspace).expanduser().absolute()
    display_path = display_root.joinpath(*parts)
    if not _DESCRIPTOR_READ_SUPPORTED:
        raise _control_read_error(
            display_path,
            reason_code="control_file_descriptor_read_unsupported",
            reason="Descriptor-bound no-follow control reads are unavailable.",
        )
    return _read_workspace_control_bytes_posix(
        display_root=display_root,
        parts=parts,
        display_path=display_path,
        required=required,
    )


def _read_workspace_control_bytes_posix(
    *,
    display_root: Path,
    parts: tuple[str, ...],
    display_path: Path,
    required: bool,
) -> bytes | None:
    """Acquire one control file through a POSIX descriptor chain."""

    relative_path = _workspace_control_relative_path_from_parts(
        parts,
        display_path=display_path,
    )
    with _open_workspace_control_read_session_posix(display_root) as session:
        return session.read_bytes(relative_path, required=required)


def _read_workspace_control_bytes_windows(
    *,
    display_root: Path,
    parts: tuple[str, ...],
    display_path: Path,
    required: bool,
) -> bytes | None:
    """Acquire one control file through a Windows handle-relative chain."""

    relative_path = _workspace_control_relative_path_from_parts(
        parts,
        display_path=display_path,
    )
    _validate_windows_path_parts(
        parts,
        display_path=display_path,
        reason_code="control_file_relative_path_invalid",
    )
    with _open_workspace_control_read_session_windows(display_root) as session:
        return session.read_bytes(relative_path, required=required)


def _open_workspace_control_read_session_posix(
    display_root: Path,
) -> _WorkspaceControlReadSession:
    """Acquire and retain one POSIX workspace directory descriptor."""

    workspace_parts = _workspace_root_relative_parts(display_root)
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    current_fd: int | None = None
    current_display = Path(os.sep)
    try:
        try:
            current_fd = os.open(os.sep, directory_flags)
        except OSError as exc:
            raise _control_open_error(
                display_root,
                reason_code="control_workspace_root_unsafe",
                exc=exc,
            ) from exc

        for component in workspace_parts:
            current_display = current_display / component
            try:
                next_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=current_fd,
                )
            except OSError as exc:
                raise _control_open_error(
                    current_display,
                    reason_code="control_workspace_root_unsafe",
                    exc=exc,
                ) from exc
            try:
                os.close(current_fd)
            except OSError:
                pass
            current_fd = next_fd

        session = _WorkspaceControlReadSession(
            _token=_CONTROL_SESSION_TOKEN,
            backend="posix",
            display_root=display_root,
            root_resource=current_fd,
        )
        current_fd = None
        return session
    finally:
        if current_fd is not None:
            try:
                os.close(current_fd)
            except OSError:
                pass


def _acquire_workspace_control_file_posix(
    *,
    workspace_fd: int,
    parts: tuple[str, ...],
    display_root: Path,
    display_path: Path,
    required: bool,
    read_contents: bool,
) -> tuple[bool, bytes | None]:
    """Open one target relative to an already-open POSIX workspace root."""

    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    directory_fds: list[int] = []
    file_fd: int | None = None
    current_fd = workspace_fd
    current_display = display_root
    try:
        for component in parts[:-1]:
            current_display = current_display / component
            try:
                current_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=current_fd,
                )
            except OSError as exc:
                raise _control_open_error(
                    current_display,
                    reason_code="control_file_ancestor_unsafe",
                    exc=exc,
                ) from exc
            directory_fds.append(current_fd)

        try:
            file_fd = os.open(
                parts[-1],
                file_flags,
                dir_fd=current_fd,
            )
        except FileNotFoundError as exc:
            if not required:
                return False, None
            raise RuntimeStateError(
                f"Required control file is missing: {display_path}",
                details={
                    "path": str(display_path),
                    "reason_code": "control_file_missing",
                },
                error_code=E_TRANSACTION_INTEGRITY,
            ) from exc
        except OSError as exc:
            raise _control_open_error(
                display_path,
                reason_code="control_file_target_unsafe",
                exc=exc,
            ) from exc

        try:
            mode = os.fstat(file_fd).st_mode
        except OSError as exc:
            raise _control_open_error(
                display_path,
                reason_code="control_file_identity_unavailable",
                exc=exc,
            ) from exc
        if not stat.S_ISREG(mode):
            raise _control_read_error(
                display_path,
                reason_code="control_file_not_regular",
                reason="Control file target must be a regular file.",
            )
        if not read_contents:
            return True, None

        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(file_fd, 1024 * 1024)
            except OSError as exc:
                raise _control_open_error(
                    display_path,
                    reason_code="control_file_read_failed",
                    exc=exc,
                ) from exc
            if not chunk:
                return True, b"".join(chunks)
            chunks.append(chunk)
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        for directory_fd in reversed(directory_fds):
            try:
                os.close(directory_fd)
            except OSError:
                pass


def _open_workspace_control_read_session_windows(
    display_root: Path,
) -> _WorkspaceControlReadSession:
    """Acquire and retain one Windows workspace directory handle."""

    root_path, workspace_parts = _windows_workspace_root_and_parts(display_root)
    current_handle: int | None = None
    current_display = Path(PureWindowsPath(str(display_root)).anchor)
    try:
        try:
            current_handle = _windows_open_root_handle(root_path)
        except OSError as exc:
            raise _windows_control_open_error(
                display_root,
                reason_code="control_workspace_root_unsafe",
                exc=exc,
            ) from exc
        _require_windows_handle_kind(
            current_handle,
            path=current_display,
            directory=True,
        )

        for component in workspace_parts:
            current_display = current_display / component
            next_handle = _windows_open_safe_directory(
                parent_handle=current_handle,
                component=component,
                path=current_display,
                reason_code="control_workspace_root_unsafe",
            )
            _windows_close_handle(current_handle)
            current_handle = next_handle

        session = _WorkspaceControlReadSession(
            _token=_CONTROL_SESSION_TOKEN,
            backend="windows",
            display_root=display_root,
            root_resource=current_handle,
        )
        current_handle = None
        return session
    finally:
        if current_handle is not None:
            _windows_close_handle(current_handle)


def _acquire_workspace_control_file_windows(
    *,
    workspace_handle: int,
    parts: tuple[str, ...],
    display_root: Path,
    display_path: Path,
    required: bool,
    read_contents: bool,
) -> tuple[bool, bytes | None]:
    """Open one target relative to an already-open Windows workspace root."""

    _validate_windows_path_parts(
        parts,
        display_path=display_path,
        reason_code="control_file_relative_path_invalid",
    )
    directory_handles: list[int] = []
    file_handle: int | None = None
    current_handle = workspace_handle
    current_display = display_root
    try:
        for component in parts[:-1]:
            current_display = current_display / component
            current_handle = _windows_open_safe_directory(
                parent_handle=current_handle,
                component=component,
                path=current_display,
                reason_code="control_file_ancestor_unsafe",
            )
            directory_handles.append(current_handle)

        try:
            file_handle = _windows_open_relative_handle(
                parent_handle=current_handle,
                component=parts[-1],
                directory=False,
            )
        except OSError as exc:
            if _windows_error_code(exc) in {
                _WINDOWS_ERROR_FILE_NOT_FOUND,
                _WINDOWS_ERROR_PATH_NOT_FOUND,
            }:
                if not required:
                    return False, None
                raise RuntimeStateError(
                    f"Required control file is missing: {display_path}",
                    details={
                        "path": str(display_path),
                        "reason_code": "control_file_missing",
                    },
                    error_code=E_TRANSACTION_INTEGRITY,
                ) from exc
            raise _windows_control_open_error(
                display_path,
                reason_code="control_file_target_unsafe",
                exc=exc,
            ) from exc
        _require_windows_handle_kind(
            file_handle,
            path=display_path,
            directory=False,
        )
        if not read_contents:
            return True, None
        return True, _windows_read_handle(file_handle, path=display_path)
    finally:
        if file_handle is not None:
            _windows_close_handle(file_handle)
        for directory_handle in reversed(directory_handles):
            _windows_close_handle(directory_handle)


def _windows_open_safe_directory(
    *,
    parent_handle: int,
    component: str,
    path: Path,
    reason_code: str,
) -> int:
    try:
        handle = _windows_open_relative_handle(
            parent_handle=parent_handle,
            component=component,
            directory=True,
        )
    except OSError as exc:
        raise _windows_control_open_error(
            path,
            reason_code=reason_code,
            exc=exc,
        ) from exc
    try:
        _require_windows_handle_kind(handle, path=path, directory=True)
    except Exception:
        _windows_close_handle(handle)
        raise
    return handle


def _require_windows_handle_kind(
    handle: int,
    *,
    path: Path,
    directory: bool,
) -> None:
    try:
        attributes = _windows_handle_attributes(handle)
    except OSError as exc:
        raise _windows_control_open_error(
            path,
            reason_code="control_file_identity_unavailable",
            exc=exc,
        ) from exc
    if attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise _control_read_error(
            path,
            reason_code="control_file_path_unsafe",
            reason="Control file path contains a Windows reparse point.",
        )
    is_directory = bool(attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY)
    if directory and not is_directory:
        raise _control_read_error(
            path,
            reason_code="control_file_path_unsafe",
            reason="Control file ancestor must be a directory.",
        )
    if not directory and is_directory:
        raise _control_read_error(
            path,
            reason_code="control_file_not_regular",
            reason="Control file target must be a regular file.",
        )


def _windows_workspace_root_and_parts(display_root: Path) -> tuple[str, tuple[str, ...]]:
    pure_root = PureWindowsPath(str(display_root))
    if (
        not pure_root.is_absolute()
        or not pure_root.anchor
        or not pure_root.drive
        or pure_root.drive.startswith("\\\\?\\")
        or pure_root.drive.startswith("\\\\.\\")
    ):
        raise _control_read_error(
            display_root,
            reason_code="control_workspace_root_invalid",
            reason="Workspace root must be a canonical Windows drive or UNC path.",
        )
    workspace_parts = tuple(pure_root.parts[1:])
    _validate_windows_path_parts(
        workspace_parts,
        display_path=display_root,
        reason_code="control_workspace_root_invalid",
    )
    drive = pure_root.drive
    if drive.startswith("\\\\"):
        share_parts = tuple(part for part in drive[2:].split("\\") if part)
        if len(share_parts) != 2:
            raise _control_read_error(
                display_root,
                reason_code="control_workspace_root_invalid",
                reason="Workspace UNC root must contain one server and share.",
            )
        _validate_windows_path_parts(
            share_parts,
            display_path=display_root,
            reason_code="control_workspace_root_invalid",
        )
        root_path = "\\\\?\\UNC\\" + "\\".join(share_parts) + "\\"
    elif len(drive) == 2 and drive[0].isalpha() and drive[1] == ":":
        root_path = f"\\\\?\\{drive}\\"
    else:
        raise _control_read_error(
            display_root,
            reason_code="control_workspace_root_invalid",
            reason="Workspace root must use a drive letter or UNC share.",
        )
    return root_path, workspace_parts


def _windows_workspace_selector(workspace: str | Path) -> Path:
    """Bind one already-canonical Windows selector without environment lookup."""

    if type(workspace) is str:
        raw_workspace = workspace
    elif type(workspace) is _CONCRETE_PATH_TYPE:
        raw_workspace = str(workspace)
    else:
        raise _control_read_error(
            Path("<invalid-workspace>"),
            reason_code="control_workspace_root_invalid",
            reason="Workspace root must be an exact string or concrete Path.",
        )
    lexical_parts = raw_workspace.replace("/", "\\").split("\\")
    if (
        not raw_workspace
        or raw_workspace.startswith("~")
        or "\x00" in raw_workspace
        or any(part in {".", ".."} for part in lexical_parts)
    ):
        raise _control_read_error(
            Path(raw_workspace or "<invalid-workspace>"),
            reason_code="control_workspace_root_invalid",
            reason="Workspace root must be a canonical absolute Windows selector.",
        )
    pure_workspace = PureWindowsPath(raw_workspace)
    if (
        not pure_workspace.is_absolute()
        or not pure_workspace.anchor
        or not pure_workspace.drive
        or pure_workspace.drive.startswith("\\\\?\\")
        or pure_workspace.drive.startswith("\\\\.\\")
    ):
        raise _control_read_error(
            Path(raw_workspace),
            reason_code="control_workspace_root_invalid",
            reason="Workspace root must be a canonical drive or UNC selector.",
        )
    display_root = Path(raw_workspace)
    _windows_workspace_root_and_parts(display_root)
    return display_root


def _validate_windows_path_parts(
    parts: tuple[str, ...],
    *,
    display_path: Path,
    reason_code: str,
) -> None:
    for part in parts:
        if (
            not isinstance(part, str)
            or not part
            or part in {".", ".."}
            or "\x00" in part
            or ":" in part
            or "\\" in part
            or "/" in part
            or part.endswith((" ", "."))
        ):
            raise _control_read_error(
                display_path,
                reason_code=reason_code,
                reason="Windows path contains a noncanonical component.",
            )
        _windows_component_utf16_bytes(
            part,
            display_path=display_path,
            reason_code=reason_code,
        )


def _windows_component_utf16_bytes(
    component: str,
    *,
    display_path: Path,
    reason_code: str,
) -> bytes:
    """Return one exact NT component encoding without USHORT narrowing."""

    if not isinstance(component, str) or not component:
        raise _control_read_error(
            display_path,
            reason_code=reason_code,
            reason="Windows path contains a noncanonical component.",
        )
    try:
        encoded = component.encode("utf-16-le")
    except UnicodeEncodeError as exc:
        raise _control_read_error(
            display_path,
            reason_code=reason_code,
            reason="Windows path component is not valid UTF-16.",
        ) from exc
    if len(encoded) + 2 > _WINDOWS_UNICODE_STRING_MAX_BYTES:
        raise _control_read_error(
            display_path,
            reason_code=reason_code,
            reason="Windows path component exceeds the native identity limit.",
        )
    return encoded


@lru_cache(maxsize=1)
def _windows_native_api() -> "_WindowsNativeApi":
    return _WindowsNativeApi()


class _WindowsNativeApi:
    def __init__(self) -> None:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise OSError(0, "Windows native APIs are unavailable.")
        kernel32 = win_dll("kernel32", use_last_error=True)
        ntdll = win_dll("ntdll", use_last_error=True)

        self.create_file = kernel32.CreateFileW
        self.create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        self.create_file.restype = wintypes.HANDLE

        self.nt_create_file = ntdll.NtCreateFile
        self.nt_create_file.argtypes = (
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(_WindowsObjectAttributes),
            ctypes.POINTER(_WindowsIoStatusBlock),
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        self.nt_create_file.restype = ctypes.c_long

        self.rtl_nt_status_to_dos_error = ntdll.RtlNtStatusToDosError
        self.rtl_nt_status_to_dos_error.argtypes = (ctypes.c_long,)
        self.rtl_nt_status_to_dos_error.restype = wintypes.ULONG

        self.get_file_information = kernel32.GetFileInformationByHandleEx
        self.get_file_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        self.get_file_information.restype = wintypes.BOOL

        self.read_file = kernel32.ReadFile
        self.read_file.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        self.read_file.restype = wintypes.BOOL

        self.close_handle = kernel32.CloseHandle
        self.close_handle.argtypes = (wintypes.HANDLE,)
        self.close_handle.restype = wintypes.BOOL


def _windows_open_root_handle(root_path: str) -> int:
    api = _windows_native_api()
    handle = api.create_file(
        root_path,
        _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_FILE_TRAVERSE | _WINDOWS_SYNCHRONIZE,
        _WINDOWS_FILE_SHARE_READ
        | _WINDOWS_FILE_SHARE_WRITE
        | _WINDOWS_FILE_SHARE_DELETE,
        None,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS
        | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    value = _windows_handle_value(handle)
    if value == _WINDOWS_INVALID_HANDLE_VALUE:
        raise _windows_os_error(ctypes.get_last_error(), root_path)
    return value


def _windows_open_relative_handle(
    *,
    parent_handle: int,
    component: str,
    directory: bool,
) -> int:
    encoded_name = _windows_component_utf16_bytes(
        component,
        display_path=Path(component),
        reason_code="control_file_relative_path_invalid",
    )
    name_length = len(encoded_name)
    name_buffer = ctypes.create_unicode_buffer(component, (name_length // 2) + 1)
    api = _windows_native_api()
    unicode_name = _WindowsUnicodeString(
        Length=name_length,
        MaximumLength=name_length + 2,
        Buffer=ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    object_attributes = _WindowsObjectAttributes(
        Length=ctypes.sizeof(_WindowsObjectAttributes),
        RootDirectory=wintypes.HANDLE(parent_handle),
        ObjectName=ctypes.pointer(unicode_name),
        Attributes=_WINDOWS_OBJ_CASE_INSENSITIVE,
        SecurityDescriptor=None,
        SecurityQualityOfService=None,
    )
    io_status = _WindowsIoStatusBlock()
    output_handle = wintypes.HANDLE()
    desired_access = _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_SYNCHRONIZE
    create_options = (
        _WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
        | _WINDOWS_FILE_OPEN_REPARSE_POINT
    )
    if directory:
        desired_access |= _WINDOWS_FILE_TRAVERSE
        create_options |= (
            _WINDOWS_FILE_DIRECTORY_FILE | _WINDOWS_FILE_OPEN_FOR_BACKUP_INTENT
        )
    else:
        desired_access |= _WINDOWS_FILE_READ_DATA
    status = int(
        api.nt_create_file(
            ctypes.byref(output_handle),
            desired_access,
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            0,
            _WINDOWS_FILE_SHARE_READ
            | _WINDOWS_FILE_SHARE_WRITE
            | _WINDOWS_FILE_SHARE_DELETE,
            _WINDOWS_FILE_OPEN,
            create_options,
            None,
            0,
        )
    )
    if status < 0:
        error_code = int(api.rtl_nt_status_to_dos_error(status))
        raise _windows_os_error(error_code, component)
    value = _windows_handle_value(output_handle)
    if value in {None, _WINDOWS_INVALID_HANDLE_VALUE}:
        raise _windows_os_error(6, component)
    return value


def _windows_handle_attributes(handle: int) -> int:
    api = _windows_native_api()
    info = _WindowsFileAttributeTagInfo()
    if not api.get_file_information(
        wintypes.HANDLE(handle),
        _WINDOWS_FILE_ATTRIBUTE_TAG_INFO_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise _windows_os_error(ctypes.get_last_error(), "<control-handle>")
    return int(info.FileAttributes)


def _windows_read_handle(handle: int, *, path: Path) -> bytes:
    api = _windows_native_api()
    chunks: list[bytes] = []
    while True:
        buffer = ctypes.create_string_buffer(1024 * 1024)
        count = wintypes.DWORD()
        if not api.read_file(
            wintypes.HANDLE(handle),
            buffer,
            len(buffer),
            ctypes.byref(count),
            None,
        ):
            exc = _windows_os_error(ctypes.get_last_error(), str(path))
            raise _windows_control_open_error(
                path,
                reason_code="control_file_read_failed",
                exc=exc,
            ) from exc
        if count.value == 0:
            return b"".join(chunks)
        chunks.append(buffer.raw[: count.value])


def _windows_close_handle(handle: int) -> None:
    try:
        _windows_native_api().close_handle(wintypes.HANDLE(handle))
    except Exception:
        pass


def _windows_handle_value(handle: object) -> int | None:
    value = getattr(handle, "value", handle)
    return None if value is None else int(value)


def _windows_os_error(error_code: int, path: str) -> OSError:
    format_error = getattr(ctypes, "FormatError", None)
    message = (
        format_error(error_code)
        if error_code and format_error is not None
        else f"Windows API failure ({error_code})"
    )
    exc = OSError(error_code, message, path)
    exc.winerror = error_code
    return exc


def _windows_error_code(exc: OSError) -> int | None:
    return getattr(exc, "winerror", None) or exc.errno


def _windows_control_open_error(
    path: Path,
    *,
    reason_code: str,
    exc: OSError,
) -> RuntimeStateError:
    return _control_read_error(
        path,
        reason_code=reason_code,
        reason=str(exc),
    )


def load_workspace_control_object(
    *,
    workspace: str | Path,
    relative_path: str,
    expected_schema: str | None = None,
    required: bool = True,
) -> dict[str, Any] | None:
    """Acquire and decode one JSON control object without reopening its path."""

    raw = read_workspace_control_bytes(
        workspace=workspace,
        relative_path=relative_path,
        required=required,
    )
    if raw is None:
        return None
    display_path = Path(workspace).expanduser().absolute() / relative_path
    return _decode_control_object_bytes(
        raw,
        path=display_path,
        expected_schema=expected_schema,
    )


def load_control_object(
    path: str | Path,
    *,
    expected_schema: str | None = None,
    required: bool = True,
) -> dict[str, Any] | None:
    """Load one JSON control object and validate its optional schema."""

    control_path = Path(path)
    if not control_path.exists():
        if not required:
            return None
        raise RuntimeStateError(
            f"Required control file is missing: {control_path}",
            details={"path": str(control_path), "reason_code": "control_file_missing"},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    try:
        raw = control_path.read_bytes()
    except OSError as exc:
        raise RuntimeStateError(
            f"Control file could not be read: {control_path}",
            details={"path": str(control_path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    return _decode_control_object_bytes(
        raw,
        path=control_path,
        expected_schema=expected_schema,
    )


def _decode_control_object_bytes(
    raw: bytes,
    *,
    path: Path,
    expected_schema: str | None,
) -> dict[str, Any]:
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeStateError(
            f"Control file is not valid UTF-8: {path}",
            details={"path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise RuntimeStateError(
            f"Control file is not valid JSON: {path}",
            details={"path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeStateError(
            f"Control file must contain an object: {path}",
            details={"path": str(path), "reason_code": "control_file_not_object"},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    if expected_schema is not None and payload.get("schema_version") != expected_schema:
        raise RuntimeStateError(
            f"Control file has an unsupported schema: {path}",
            details={
                "path": str(path),
                "expected_schema": expected_schema,
                "schema_version": payload.get("schema_version"),
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return payload


def _workspace_control_relative_parts(relative_path: str) -> tuple[str, ...]:
    if type(relative_path) is not str or not relative_path:
        raise _control_read_error(
            Path("<invalid-control-path>"),
            reason_code="control_file_relative_path_invalid",
            reason="Control file path must be a non-empty relative string.",
        )
    if "\\" in relative_path or "\x00" in relative_path:
        raise _control_read_error(
            Path(relative_path.replace("\x00", "<NUL>")),
            reason_code="control_file_relative_path_invalid",
            reason="Control file path contains an unsupported component.",
        )
    posix_path = PurePosixPath(relative_path)
    windows_path = PureWindowsPath(relative_path)
    parts = tuple(relative_path.split("/"))
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise _control_read_error(
            Path(relative_path),
            reason_code="control_file_relative_path_invalid",
            reason="Control file path must be canonical and workspace-relative.",
        )
    return parts


def _workspace_control_relative_path_from_parts(
    parts: tuple[str, ...],
    *,
    display_path: Path,
) -> str:
    """Re-enter the canonical string validator from a compatibility tuple."""

    if (
        type(parts) is not tuple
        or not parts
        or any(
            type(part) is not str
            or not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            or "\x00" in part
            for part in parts
        )
    ):
        raise _control_read_error(
            display_path,
            reason_code="control_file_relative_path_invalid",
            reason="Control file path parts must be canonical relative components.",
        )
    relative_path = "/".join(parts)
    if _workspace_control_relative_parts(relative_path) != parts:
        raise _control_read_error(
            display_path,
            reason_code="control_file_relative_path_invalid",
            reason="Control file path parts do not match the canonical relative path.",
        )
    return relative_path


def _workspace_root_relative_parts(display_root: Path) -> tuple[str, ...]:
    if (
        display_root.anchor != os.sep
        or not display_root.is_absolute()
        or any(part in {"", ".", ".."} for part in display_root.parts[1:])
    ):
        raise _control_read_error(
            display_root,
            reason_code="control_workspace_root_invalid",
            reason="Workspace root must be a canonical absolute path.",
        )
    return tuple(display_root.parts[1:])


def _control_open_error(
    path: Path,
    *,
    reason_code: str,
    exc: OSError,
) -> RuntimeStateError:
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        reason_code = "control_file_path_unsafe"
    return _control_read_error(
        path,
        reason_code=reason_code,
        reason=str(exc),
    )


def _control_read_error(
    path: Path,
    *,
    reason_code: str,
    reason: str,
) -> RuntimeStateError:
    return RuntimeStateError(
        f"Control file could not be acquired safely: {path}",
        details={
            "path": str(path),
            "reason_code": reason_code,
            "reason": reason,
        },
        error_code=E_TRANSACTION_INTEGRITY,
    )
