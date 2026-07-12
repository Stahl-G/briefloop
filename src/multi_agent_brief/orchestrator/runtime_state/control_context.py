"""Fail-closed loaders for runtime control records."""

from __future__ import annotations

import errno
import json
import os
import stat
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


def read_workspace_control_bytes(
    *,
    workspace: str | Path,
    relative_path: str,
    required: bool = True,
) -> bytes | None:
    """Acquire one regular control file through a workspace-rooted descriptor chain."""

    display_root = Path(workspace).expanduser().absolute()
    parts = _workspace_control_relative_parts(relative_path)
    display_path = display_root.joinpath(*parts)
    if not _DESCRIPTOR_READ_SUPPORTED:
        raise _control_read_error(
            display_path,
            reason_code="control_file_descriptor_read_unsupported",
            reason="Descriptor-bound no-follow control reads are unavailable.",
        )

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
    try:
        try:
            directory_fds.append(os.open(display_root, directory_flags))
        except OSError as exc:
            raise _control_open_error(
                display_path,
                reason_code="control_workspace_root_unsafe",
                exc=exc,
            ) from exc

        current_fd = directory_fds[-1]
        current_display = display_root
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
                return None
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
                return b"".join(chunks)
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
