"""Non-authoritative invocation scratch materialization."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Iterable, TypeVar

from multi_agent_brief.control_store.serialization import canonical_json_bytes

from multi_agent_brief.contracts.v2 import StrictModel

from .contracts import RoleTaskEnvelope
from .errors import RuntimeHostError


MAX_ROLE_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_HOST_CONTRACT_BYTES = 1024 * 1024
_ModelT = TypeVar("_ModelT", bound=StrictModel)


def _prepare_host_parent(
    workspace: Path,
    relative: str,
    *,
    error_code: str,
) -> Path:
    """Create a workspace-relative parent chain without following links."""

    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts:
        raise RuntimeHostError(error_code)
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise RuntimeHostError(error_code)
    try:
        root = workspace.lstat()
        if workspace.is_symlink() or not stat.S_ISDIR(root.st_mode):
            raise RuntimeHostError(error_code)
        current = workspace
        for part in candidate.parts[:-1]:
            current = current / part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            metadata = current.lstat()
            if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeHostError(error_code)
        return current / candidate.parts[-1]
    except RuntimeHostError:
        raise
    except OSError as exc:
        raise RuntimeHostError(error_code) from exc


def _read_existing_host_bytes(
    path: Path,
    expected: bytes,
    *,
    error_code: str,
) -> None:
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != len(expected)
        ):
            raise RuntimeHostError(error_code)
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size != len(expected)
            ):
                raise RuntimeHostError(error_code)
            chunks: list[bytes] = []
            remaining = len(expected) + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
        finally:
            os.close(descriptor)
        if payload != expected:
            raise RuntimeHostError(error_code)
    except RuntimeHostError:
        raise
    except OSError as exc:
        raise RuntimeHostError(error_code) from exc


def materialize_host_bytes(
    workspace: Path,
    relative: str,
    payload: bytes,
    *,
    error_code: str,
) -> Path:
    """Create or exactly replay host-owned bytes without following links."""

    path = _prepare_host_parent(workspace, relative, error_code=error_code)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        _read_existing_host_bytes(path, payload, error_code=error_code)
        return path
    except OSError as exc:
        raise RuntimeHostError(error_code) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RuntimeHostError(error_code)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise RuntimeHostError(error_code)
            written += count
        os.fsync(descriptor)
        persisted = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(persisted.st_mode)
            or persisted.st_nlink != 1
            or (persisted.st_dev, persisted.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise RuntimeHostError(error_code)
    except RuntimeHostError:
        raise
    except OSError as exc:
        raise RuntimeHostError(error_code) from exc
    finally:
        os.close(descriptor)
    return path


def attest_host_directory(
    workspace: Path,
    relative: str,
    *,
    expected_members: Iterable[str],
    error_code: str,
) -> Path:
    """Create and durably attest one exact workspace-relative directory."""

    names = set(expected_members)
    if any(
        not name or name in {".", ".."} or Path(name).name != name for name in names
    ):
        raise RuntimeHostError(error_code)
    marker = _prepare_host_parent(
        workspace,
        f"{relative}/.briefloop-directory-attestation",
        error_code=error_code,
    )
    directory = marker.parent
    try:
        metadata = directory.lstat()
        if directory.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeHostError(error_code)
        if {entry.name for entry in os.scandir(directory)} != names:
            raise RuntimeHostError(error_code)
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ) or not stat.S_ISDIR(opened.st_mode):
                raise RuntimeHostError(error_code)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except RuntimeHostError:
        raise
    except OSError as exc:
        raise RuntimeHostError(error_code) from exc
    return directory


def read_host_contract(
    workspace: Path,
    input_path: str,
    model: type[_ModelT],
    *,
    error_code: str,
) -> _ModelT:
    """Read one strict workspace-contained host input without following links."""

    candidate = Path(input_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        relative = candidate.relative_to(workspace)
        if not relative.parts or ".." in relative.parts:
            raise RuntimeHostError(error_code)
        current = workspace
        for part in relative.parts:
            current = current / part
            metadata = current.lstat()
            if current.is_symlink():
                raise RuntimeHostError(error_code)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_HOST_CONTRACT_BYTES
        ):
            raise RuntimeHostError(error_code)
        descriptor = os.open(
            current,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                or opened.st_nlink != 1
                or opened.st_size > MAX_HOST_CONTRACT_BYTES
            ):
                raise RuntimeHostError(error_code)
            payload = os.read(descriptor, MAX_HOST_CONTRACT_BYTES + 1)
        finally:
            os.close(descriptor)
        if not payload or len(payload) > MAX_HOST_CONTRACT_BYTES:
            raise RuntimeHostError(error_code)
        return model.model_validate_json(payload, strict=True)
    except RuntimeHostError:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeHostError(error_code) from exc


def materialize_role_envelope(
    workspace: Path,
    envelope: RoleTaskEnvelope,
) -> Path:
    payload = canonical_json_bytes(
        envelope.model_dump(mode="json", exclude_unset=False)
    )
    return materialize_host_bytes(
        workspace,
        f"{envelope.scratch_directory}/role_task_envelope.json",
        payload,
        error_code="runtime_envelope_materialization_failed",
    )


def read_role_envelope(workspace: Path, invocation_id: str) -> RoleTaskEnvelope:
    path = workspace / "scratch" / invocation_id / "role_task_envelope.json"
    try:
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeHostError("runtime_envelope_invalid")
        payload = json.loads(path.read_bytes())
        return RoleTaskEnvelope.model_validate(payload, strict=True)
    except RuntimeHostError:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeHostError("runtime_envelope_invalid") from exc


def read_role_outputs(
    workspace: Path,
    envelope: RoleTaskEnvelope,
    *,
    host_filenames: Iterable[str] = (),
) -> dict[str, bytes]:
    """Read the exact invocation-scoped output set without following links."""

    scratch = workspace / envelope.scratch_directory
    allowed = set(envelope.allowed_output_filenames)
    host_owned = set(host_filenames)
    expected_members = allowed | host_owned | {"role_task_envelope.json"}
    try:
        if scratch.is_symlink() or not stat.S_ISDIR(scratch.lstat().st_mode):
            raise RuntimeHostError("runtime_scratch_invalid")
        members = {entry.name for entry in os.scandir(scratch)}
        if members != expected_members:
            raise RuntimeHostError(
                "runtime_proposal_missing"
                if members < expected_members
                else "runtime_scratch_invalid"
            )
        result: dict[str, bytes] = {}
        for filename in sorted(allowed):
            path = scratch / filename
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > MAX_ROLE_OUTPUT_BYTES
            ):
                raise RuntimeHostError("runtime_scratch_invalid")
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                opened = os.fstat(descriptor)
                if (
                    (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                    or opened.st_nlink != 1
                    or opened.st_size > MAX_ROLE_OUTPUT_BYTES
                ):
                    raise RuntimeHostError("runtime_scratch_invalid")
                chunks: list[bytes] = []
                remaining = MAX_ROLE_OUTPUT_BYTES + 1
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
                if not payload or len(payload) > MAX_ROLE_OUTPUT_BYTES:
                    raise RuntimeHostError("runtime_scratch_invalid")
                result[filename] = payload
            finally:
                os.close(descriptor)
        return result
    except RuntimeHostError:
        raise
    except OSError as exc:
        raise RuntimeHostError("runtime_scratch_invalid") from exc


def materialize_host_request(
    workspace: Path,
    envelope: RoleTaskEnvelope,
    payload: bytes,
) -> Path:
    """Create or replay the exact host-derived submit request bytes."""

    return materialize_host_bytes(
        workspace,
        f"{envelope.scratch_directory}/submit_request.json",
        payload,
        error_code="runtime_envelope_invalid",
    )


__all__ = [
    "MAX_HOST_CONTRACT_BYTES",
    "MAX_ROLE_OUTPUT_BYTES",
    "materialize_host_bytes",
    "materialize_host_request",
    "materialize_role_envelope",
    "read_role_envelope",
    "read_role_outputs",
    "read_host_contract",
]
