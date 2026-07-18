"""Capability-gated retained-dirfd publication primitives for local POSIX."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import platform
import secrets
import stat
import sys

from multi_agent_brief.control_store.serialization import canonical_json_bytes

from .errors import CoreRunError


RENAME_NOREPLACE = 1
RENAME_EXCL = 0x00000004
F_FULLFSYNC = 51
SUPPORTED_LINUX_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs"})
SUPPORTED_DARWIN_FILESYSTEMS = frozenset({"apfs", "hfs"})


@dataclass(frozen=True)
class CapabilityProfile:
    platform: str
    filesystem: str
    namespace_primitive: str
    temp_durability: str
    canonical_post_durability: str
    parent_durability: str
    canonical_open_flags: str
    cleanup_policy: str = "retain_residue_v1"

    @property
    def bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "canonical_open_flags": self.canonical_open_flags,
                "canonical_post_durability": self.canonical_post_durability,
                "cleanup_policy": self.cleanup_policy,
                "filesystem": self.filesystem,
                "namespace_primitive": self.namespace_primitive,
                "parent_durability": self.parent_durability,
                "platform": self.platform,
                "temp_durability": self.temp_durability,
            }
        )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.bytes).hexdigest()


@dataclass(frozen=True)
class LeafObservation:
    kind: str
    sha256: str | None = None
    size: int | None = None
    identity: tuple[int, int] | None = None


class RetainedParent:
    """One opened parent retained across all relative leaf operations."""

    def __init__(self, path: Path, fd: int, profile: CapabilityProfile) -> None:
        self.path = path
        self.fd = fd
        self.profile = profile

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "RetainedParent":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def observe(self, leaf: str) -> LeafObservation:
        _validate_leaf(leaf)
        try:
            info = os.stat(leaf, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return LeafObservation("absent")
        except OSError as exc:
            raise CoreRunError("checkout_projection_unreadable") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return LeafObservation("unsafe")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(leaf, flags, dir_fd=self.fd)
            try:
                data = _read_all(fd)
                current = os.fstat(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise CoreRunError("checkout_projection_unreadable") from exc
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            return LeafObservation("unsafe")
        return LeafObservation(
            "blob",
            hashlib.sha256(data).hexdigest(),
            len(data),
            (info.st_dev, info.st_ino),
        )

    def create_and_flush(self, leaf: str, content: bytes) -> None:
        _validate_leaf(leaf)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            fd = os.open(leaf, flags, 0o600, dir_fd=self.fd)
            try:
                view = memoryview(content)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError(errno.EIO, "short write")
                    view = view[written:]
                self._flush_file(fd)
            finally:
                os.close(fd)
        except FileExistsError:
            raise CoreRunError("checkout_projection_conflict") from None
        except CoreRunError:
            raise
        except OSError as exc:
            raise CoreRunError("checkout_publication_io_error") from exc

    def no_clobber_rename(self, old_leaf: str, new_leaf: str) -> None:
        _validate_leaf(old_leaf)
        _validate_leaf(new_leaf)
        _no_clobber_rename(self.fd, old_leaf, new_leaf)

    def sync_parent(self) -> None:
        try:
            os.fsync(self.fd)
        except OSError as exc:
            raise CoreRunError("checkout_publication_io_error") from exc

    def attest_canonical_blob(self, leaf: str, expected_sha256: str, size: int) -> None:
        _validate_leaf(leaf)
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(leaf, flags, dir_fd=self.fd)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise CoreRunError("checkout_projection_conflict")
                data = _read_all(fd)
                if len(data) != size or hashlib.sha256(data).hexdigest() != expected_sha256:
                    raise CoreRunError("checkout_projection_conflict")
                self._flush_file(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                after_data = _read_all(fd)
                after = os.fstat(fd)
                named = os.stat(leaf, dir_fd=self.fd, follow_symlinks=False)
                if (
                    (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                    or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino)
                    or len(after_data) != size
                    or hashlib.sha256(after_data).hexdigest() != expected_sha256
                    or not stat.S_ISREG(named.st_mode)
                    or named.st_nlink != 1
                ):
                    raise CoreRunError("checkout_projection_conflict")
            finally:
                os.close(fd)
            self.sync_parent()
        except CoreRunError:
            raise
        except OSError as exc:
            raise CoreRunError("checkout_publication_io_error") from exc

    def _flush_file(self, fd: int) -> None:
        try:
            if sys.platform == "darwin":
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.fcntl(fd, F_FULLFSYNC) != 0:
                    _raise_errno("checkout_publication_io_error")
            else:
                os.fsync(fd)
        except CoreRunError:
            raise
        except OSError as exc:
            raise CoreRunError("checkout_publication_io_error") from exc


def _validate_leaf(value: str) -> None:
    if (
        type(value) is not str
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise CoreRunError("checkout_topology_invalid")


def _read_all(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _raise_errno(code: str) -> None:
    value = ctypes.get_errno()
    if value in {errno.EEXIST, errno.ENOTEMPTY}:
        raise CoreRunError("checkout_projection_conflict")
    raise CoreRunError(code)


def _no_clobber_rename(parent_fd: int, old_leaf: str, new_leaf: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    old = os.fsencode(old_leaf)
    new = os.fsencode(new_leaf)
    if sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        if function is None:
            raise CoreRunError("checkout_publication_unsupported")
        result = function(parent_fd, old, parent_fd, new, RENAME_NOREPLACE)
    elif sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
        if function is None:
            raise CoreRunError("checkout_publication_unsupported")
        result = function(parent_fd, old, parent_fd, new, RENAME_EXCL)
    else:
        raise CoreRunError("checkout_publication_unsupported")
    if result != 0:
        _raise_errno("checkout_publication_io_error")


def _linux_filesystem(path: Path) -> str:
    resolved = path.resolve()
    best: tuple[int, str] | None = None
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CoreRunError("checkout_publication_unsupported") from exc
    for line in lines:
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        right_fields = right.split()
        if len(fields) < 5 or not right_fields:
            continue
        mount = Path(fields[4].replace("\\040", " ")).resolve()
        try:
            resolved.relative_to(mount)
        except ValueError:
            continue
        candidate = (len(mount.parts), right_fields[0].lower())
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None or best[1] not in SUPPORTED_LINUX_FILESYSTEMS:
        raise CoreRunError("checkout_publication_unsupported")
    return best[1]


def _darwin_filesystem(path: Path) -> str:
    class StatFs(ctypes.Structure):
        _fields_ = [
            ("f_bsize", ctypes.c_uint32), ("f_iosize", ctypes.c_int32),
            ("f_blocks", ctypes.c_uint64), ("f_bfree", ctypes.c_uint64),
            ("f_bavail", ctypes.c_uint64), ("f_files", ctypes.c_uint64),
            ("f_ffree", ctypes.c_uint64), ("f_fsid", ctypes.c_int32 * 2),
            ("f_owner", ctypes.c_uint32), ("f_type", ctypes.c_uint32),
            ("f_flags", ctypes.c_uint32), ("f_fssubtype", ctypes.c_uint32),
            ("f_fstypename", ctypes.c_char * 16),
            ("f_mntonname", ctypes.c_char * 1024),
            ("f_mntfromname", ctypes.c_char * 1024),
            ("f_reserved", ctypes.c_uint32 * 8),
        ]
    value = StatFs()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.statfs(os.fsencode(path), ctypes.byref(value)) != 0:
        _raise_errno("checkout_publication_unsupported")
    fs = value.f_fstypename.split(b"\0", 1)[0].decode("ascii").lower()
    if fs not in SUPPORTED_DARWIN_FILESYSTEMS:
        raise CoreRunError("checkout_publication_unsupported")
    return fs


def capability_profile(parent: Path) -> CapabilityProfile:
    if os.name == "nt" or platform.system() == "Windows":
        raise CoreRunError("checkout_publication_unsupported")
    if sys.platform.startswith("linux"):
        fs = _linux_filesystem(parent)
        primitive, durability = "renameat2(RENAME_NOREPLACE)", "fsync"
    elif sys.platform == "darwin":
        fs = _darwin_filesystem(parent)
        primitive, durability = "renameatx_np(RENAME_EXCL)", "F_FULLFSYNC"
    else:
        raise CoreRunError("checkout_publication_unsupported")
    return CapabilityProfile(
        platform=sys.platform,
        filesystem=fs,
        namespace_primitive=primitive,
        temp_durability=durability,
        canonical_post_durability=durability,
        parent_durability="fsync",
        canonical_open_flags="O_RDWR|O_NOFOLLOW|O_CLOEXEC",
    )


def open_retained_parent(parent: Path, profile: CapabilityProfile | None = None) -> RetainedParent:
    try:
        resolved = parent.resolve(strict=True)
        info = resolved.stat()
        if not stat.S_ISDIR(info.st_mode) or resolved.is_symlink():
            raise CoreRunError("checkout_topology_invalid")
        actual = profile or capability_profile(resolved)
        fd = os.open(
            resolved,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except CoreRunError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise CoreRunError("checkout_topology_invalid") from exc
    return RetainedParent(resolved, fd, actual)


def probe_publication_capability(parent: Path) -> CapabilityProfile:
    """Prove the exact live v1 primitives; residue is deliberately retained."""

    profile = capability_profile(parent)
    name = f".briefloop-pub-probe-{secrets.token_hex(16)}"
    with open_retained_parent(parent, profile) as outer:
        try:
            os.mkdir(name, 0o700, dir_fd=outer.fd)
            outer.sync_parent()
            probe_path = outer.path / name
            with open_retained_parent(probe_path, profile) as probe:
                data = b"briefloop-publication-probe-v1\n"
                digest = hashlib.sha256(data).hexdigest()
                probe.create_and_flush("source", data)
                probe.create_and_flush("occupied", b"occupied\n")
                try:
                    probe.no_clobber_rename("source", "occupied")
                except CoreRunError as exc:
                    if exc.code != "checkout_projection_conflict":
                        raise
                else:
                    raise CoreRunError("checkout_publication_unsupported")
                if probe.observe("occupied").sha256 != hashlib.sha256(b"occupied\n").hexdigest():
                    raise CoreRunError("checkout_publication_unsupported")
                probe.no_clobber_rename("source", "canonical")
                probe.sync_parent()
                probe.attest_canonical_blob("canonical", digest, len(data))
                probe.sync_parent()
        except CoreRunError:
            raise
        except OSError as exc:
            raise CoreRunError("checkout_publication_unsupported") from exc
    return profile


__all__ = [
    "CapabilityProfile",
    "LeafObservation",
    "RetainedParent",
    "capability_profile",
    "open_retained_parent",
    "probe_publication_capability",
]
