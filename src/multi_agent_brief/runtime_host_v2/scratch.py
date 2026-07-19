"""Non-authoritative invocation scratch materialization."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Iterable

from multi_agent_brief.control_store.serialization import canonical_json_bytes

from .contracts import RoleTaskEnvelope
from .errors import RuntimeHostError


MAX_ROLE_OUTPUT_BYTES = 16 * 1024 * 1024


def materialize_role_envelope(
    workspace: Path,
    envelope: RoleTaskEnvelope,
) -> Path:
    scratch = workspace / envelope.scratch_directory
    payload = canonical_json_bytes(
        envelope.model_dump(mode="json", exclude_unset=False)
    )
    try:
        parent = scratch.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or not stat.S_ISDIR(parent.lstat().st_mode):
            raise RuntimeHostError("runtime_envelope_materialization_failed")
        envelope_path = scratch / "role_task_envelope.json"
        try:
            scratch.mkdir(mode=0o700, exist_ok=False)
        except FileExistsError:
            if (
                scratch.is_symlink()
                or not stat.S_ISDIR(scratch.lstat().st_mode)
                or envelope_path.is_symlink()
                or not stat.S_ISREG(envelope_path.lstat().st_mode)
                or envelope_path.read_bytes() != payload
            ):
                raise RuntimeHostError("runtime_envelope_materialization_failed")
            return envelope_path
        descriptor = os.open(
            envelope_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return envelope_path
    except RuntimeHostError:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeHostError("runtime_envelope_materialization_failed") from exc


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

    path = workspace / envelope.scratch_directory / "submit_request.json"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or path.read_bytes() != payload
            ):
                raise RuntimeHostError("runtime_envelope_invalid")
            return path
        except RuntimeHostError:
            raise
        except OSError as exc:
            raise RuntimeHostError("runtime_envelope_invalid") from exc
    except OSError as exc:
        raise RuntimeHostError("runtime_envelope_invalid") from exc
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


__all__ = [
    "MAX_ROLE_OUTPUT_BYTES",
    "materialize_host_request",
    "materialize_role_envelope",
    "read_role_envelope",
    "read_role_outputs",
]
