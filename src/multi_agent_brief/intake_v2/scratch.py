"""Descriptor-bound reads for invocation scratch inputs."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

from multi_agent_brief.intake_v2.errors import IntakeError


class _DuplicateKey(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_non_finite_constant(_token: str) -> None:
    raise ValueError


def parse_json_object(payload: bytes) -> dict[str, Any]:
    """Parse one strict scratch JSON object without exposing input values."""

    if type(payload) is not bytes:
        raise TypeError("scratch JSON payload must be bytes")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_non_finite_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey, ValueError):
        raise IntakeError("scratch_payload_unreadable") from None
    except RecursionError:
        raise IntakeError("scratch_payload_unreadable") from None
    stack = [value]
    while stack:
        current = stack.pop()
        if type(current) is float and not math.isfinite(current):
            raise IntakeError("scratch_payload_unreadable")
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    if not isinstance(value, dict):
        raise IntakeError("scratch_payload_unreadable")
    return value


class ScratchReader:
    """Read detached bytes from one canonical workspace scratch tree."""

    def __init__(self, workspace: str | os.PathLike[str]) -> None:
        try:
            root = Path(workspace).expanduser().resolve(strict=True)
            mode = root.stat().st_mode
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise IntakeError("scratch_path_invalid") from exc
        if not stat.S_ISDIR(mode):
            raise IntakeError("scratch_path_invalid")
        self.root = root

    @staticmethod
    def _parts(relative_path: str | os.PathLike[str]) -> tuple[str, str, str]:
        if not isinstance(relative_path, (str, Path)):
            raise IntakeError("scratch_path_invalid")
        value = relative_path.as_posix() if isinstance(relative_path, Path) else relative_path
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or str(path) != value
            or len(path.parts) != 3
            or path.parts[0] != "scratch"
            or any(part in {"", ".", ".."} for part in path.parts)
            or "\\" in value
        ):
            raise IntakeError("scratch_path_invalid")
        return path.parts[0], path.parts[1], path.parts[2]

    def read_request(self, relative_path: str | os.PathLike[str]) -> bytes:
        parts = self._parts(relative_path)
        if parts[2] != "submit_request.json":
            raise IntakeError("scratch_path_invalid")
        return self.read(relative_path)

    def read(self, relative_path: str | os.PathLike[str]) -> bytes:
        _scratch, invocation_id, filename = self._parts(relative_path)
        if os.open in os.supports_dir_fd:
            return self._read_with_directory_descriptors(invocation_id, filename)
        return self._read_with_absolute_path(invocation_id, filename)

    @staticmethod
    def _directory_flags() -> int:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        return flags

    @staticmethod
    def _file_flags() -> int:
        return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

    @staticmethod
    def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
        return (left.st_dev, left.st_ino, left.st_mode) == (
            right.st_dev,
            right.st_ino,
            right.st_mode,
        )

    @staticmethod
    def _read_fd(fd: int) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    def _read_with_directory_descriptors(
        self,
        invocation_id: str,
        filename: str,
    ) -> bytes:
        scratch_path = self.root / "scratch"
        try:
            scratch_before = scratch_path.lstat()
            if not stat.S_ISDIR(scratch_before.st_mode):
                raise IntakeError("scratch_entry_unsafe")
            scratch_fd = os.open(scratch_path, self._directory_flags())
        except IntakeError:
            raise
        except OSError as exc:
            raise IntakeError("scratch_entry_unsafe") from exc
        try:
            scratch_open = os.fstat(scratch_fd)
            if not self._same_identity(scratch_before, scratch_open):
                raise IntakeError("scratch_entry_unsafe")
            invocation_before = os.stat(
                invocation_id,
                dir_fd=scratch_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(invocation_before.st_mode):
                raise IntakeError("scratch_entry_unsafe")
            invocation_fd = os.open(
                invocation_id,
                self._directory_flags(),
                dir_fd=scratch_fd,
            )
            try:
                invocation_open = os.fstat(invocation_fd)
                if not self._same_identity(invocation_before, invocation_open):
                    raise IntakeError("scratch_entry_unsafe")
                leaf_before = os.stat(
                    filename,
                    dir_fd=invocation_fd,
                    follow_symlinks=False,
                )
                if not stat.S_ISREG(leaf_before.st_mode):
                    raise IntakeError("scratch_entry_unsafe")
                leaf_fd = os.open(
                    filename,
                    self._file_flags(),
                    dir_fd=invocation_fd,
                )
                try:
                    leaf_open = os.fstat(leaf_fd)
                    if not self._same_identity(leaf_before, leaf_open):
                        raise IntakeError("scratch_entry_unsafe")
                    return self._read_fd(leaf_fd)
                finally:
                    os.close(leaf_fd)
            finally:
                os.close(invocation_fd)
        except IntakeError:
            raise
        except OSError as exc:
            raise IntakeError("scratch_entry_unsafe") from exc
        finally:
            os.close(scratch_fd)

    def _read_with_absolute_path(self, invocation_id: str, filename: str) -> bytes:
        invocation_path = self.root / "scratch" / invocation_id
        leaf_path = invocation_path / filename
        try:
            scratch_info = (self.root / "scratch").lstat()
            invocation_info = invocation_path.lstat()
            leaf_before = leaf_path.lstat()
            if (
                not stat.S_ISDIR(scratch_info.st_mode)
                or not stat.S_ISDIR(invocation_info.st_mode)
                or not stat.S_ISREG(leaf_before.st_mode)
            ):
                raise IntakeError("scratch_entry_unsafe")
            fd = os.open(leaf_path, self._file_flags())
            try:
                leaf_open = os.fstat(fd)
                if not self._same_identity(leaf_before, leaf_open):
                    raise IntakeError("scratch_entry_unsafe")
                return self._read_fd(fd)
            finally:
                os.close(fd)
        except IntakeError:
            raise
        except OSError as exc:
            raise IntakeError("scratch_entry_unsafe") from exc


__all__ = ["ScratchReader", "parse_json_object"]
