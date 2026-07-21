"""Descriptor-bound reads for invocation scratch inputs."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.contracts.json import StrictJsonError, parse_strict_json_object


def parse_json_object(payload: bytes) -> dict[str, Any]:
    """Parse one strict scratch JSON object without exposing input values."""

    try:
        return parse_strict_json_object(payload)
    except StrictJsonError:
        raise IntakeError("scratch_payload_unreadable") from None


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
    def _parts(relative_path: str | os.PathLike[str]) -> tuple[str, ...]:
        if not isinstance(relative_path, (str, Path)):
            raise IntakeError("scratch_path_invalid")
        value = relative_path.as_posix() if isinstance(relative_path, Path) else relative_path
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or str(path) != value
            or len(path.parts) not in {3, 5}
            or path.parts[0] != "scratch"
            or any(part in {"", ".", ".."} for part in path.parts)
            or "\\" in value
        ):
            raise IntakeError("scratch_path_invalid")
        if len(path.parts) == 5 and path.parts[2] != "sources":
            raise IntakeError("scratch_path_invalid")
        return path.parts

    def read_request(self, relative_path: str | os.PathLike[str]) -> bytes:
        parts = self._parts(relative_path)
        if len(parts) != 3 or parts[2] != "submit_request.json":
            raise IntakeError("scratch_path_invalid")
        return self.read(relative_path)

    def read(self, relative_path: str | os.PathLike[str]) -> bytes:
        parts = self._parts(relative_path)
        directory_parts = parts[1:-1]
        filename = parts[-1]
        if os.open in os.supports_dir_fd:
            return self._read_with_directory_descriptors(directory_parts, filename)
        return self._read_with_absolute_path(directory_parts, filename)

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
        directory_parts: tuple[str, ...],
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
            parent_fd = scratch_fd
            opened_directories: list[int] = []
            try:
                for part in directory_parts:
                    before = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
                    if not stat.S_ISDIR(before.st_mode):
                        raise IntakeError("scratch_entry_unsafe")
                    opened_fd = os.open(
                        part,
                        self._directory_flags(),
                        dir_fd=parent_fd,
                    )
                    opened = os.fstat(opened_fd)
                    if not self._same_identity(before, opened):
                        os.close(opened_fd)
                        raise IntakeError("scratch_entry_unsafe")
                    opened_directories.append(opened_fd)
                    parent_fd = opened_fd
                leaf_before = os.stat(
                    filename,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if not stat.S_ISREG(leaf_before.st_mode):
                    raise IntakeError("scratch_entry_unsafe")
                leaf_fd = os.open(
                    filename,
                    self._file_flags(),
                    dir_fd=parent_fd,
                )
                try:
                    leaf_open = os.fstat(leaf_fd)
                    if not self._same_identity(leaf_before, leaf_open):
                        raise IntakeError("scratch_entry_unsafe")
                    return self._read_fd(leaf_fd)
                finally:
                    os.close(leaf_fd)
            finally:
                for descriptor in reversed(opened_directories):
                    os.close(descriptor)
        except IntakeError:
            raise
        except OSError as exc:
            raise IntakeError("scratch_entry_unsafe") from exc
        finally:
            os.close(scratch_fd)

    def _read_with_absolute_path(
        self,
        directory_parts: tuple[str, ...],
        filename: str,
    ) -> bytes:
        parent_path = self.root / "scratch" / Path(*directory_parts)
        leaf_path = parent_path / filename
        try:
            scratch_info = (self.root / "scratch").lstat()
            directory_infos = [
                (self.root / "scratch" / Path(*directory_parts[:index])).lstat()
                for index in range(1, len(directory_parts) + 1)
            ]
            leaf_before = leaf_path.lstat()
            if (
                not stat.S_ISDIR(scratch_info.st_mode)
                or any(not stat.S_ISDIR(item.st_mode) for item in directory_infos)
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
