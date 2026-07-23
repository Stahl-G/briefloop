"""Bounded, non-authoritative upload staging for the one-shot init server."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import stat
import tempfile
from threading import RLock
from typing import BinaryIO

from multi_agent_brief.contracts.v2 import (
    ExecutionSourceManifest,
    ExecutionSourceManifestMember,
)


MAX_SOURCE_MEMBERS = 256
MAX_SOURCE_MEMBER_BYTES = 16 * 1024 * 1024
MAX_SOURCE_AGGREGATE_BYTES = 256 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


class InitWebStagingError(ValueError):
    """Value-free rejection for inert init-web staging input."""


@dataclass(frozen=True)
class StagedUpload:
    handle: str
    session_id: str
    filename: str
    path: Path
    byte_count: int
    sha256: str


class InitWebStaging:
    """Keep bounded upload bytes outside the workspace and outside authority."""

    def __init__(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="briefloop-init-web-"))
        self._uploads: dict[str, StagedUpload] = {}
        self._session_sizes: dict[str, int] = {}
        self._lock = RLock()

    def close(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def stage(
        self,
        *,
        session_id: str,
        filename: str,
        stream: BinaryIO,
        declared_length: int,
    ) -> StagedUpload:
        if not session_id or not filename or declared_length < 0:
            raise InitWebStagingError("init_web_source_upload_invalid")
        if declared_length > MAX_SOURCE_MEMBER_BYTES:
            raise InitWebStagingError("init_web_source_member_too_large")
        with self._lock:
            existing = [item for item in self._uploads.values() if item.session_id == session_id]
            if len(existing) >= MAX_SOURCE_MEMBERS:
                raise InitWebStagingError("init_web_source_member_limit")
            current_total = self._session_sizes.get(session_id, 0)
            if current_total + declared_length > MAX_SOURCE_AGGREGATE_BYTES:
                raise InitWebStagingError("init_web_source_aggregate_too_large")
            handle = f"upload-{secrets.token_hex(16)}"
            target = self._root / handle
            digest = hashlib.sha256()
            observed = 0
            try:
                with target.open("xb") as output:
                    remaining = declared_length
                    while remaining:
                        chunk = stream.read(min(_CHUNK_BYTES, remaining))
                        if not chunk:
                            raise InitWebStagingError("init_web_source_upload_invalid")
                        observed += len(chunk)
                        remaining -= len(chunk)
                        if observed > MAX_SOURCE_MEMBER_BYTES:
                            raise InitWebStagingError("init_web_source_member_too_large")
                        if current_total + observed > MAX_SOURCE_AGGREGATE_BYTES:
                            raise InitWebStagingError("init_web_source_aggregate_too_large")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                target.unlink(missing_ok=True)
                raise
            staged = StagedUpload(
                handle=handle,
                session_id=session_id,
                filename=Path(filename).name,
                path=target,
                byte_count=observed,
                sha256=digest.hexdigest(),
            )
            self._uploads[handle] = staged
            self._session_sizes[session_id] = current_total + observed
            return staged

    def materialize_confirmed(
        self,
        *,
        session_id: str,
        manifest: ExecutionSourceManifest,
        upload_bindings: object,
        target: Path,
    ) -> None:
        bound = self._bound_uploads(
            session_id=session_id,
            manifest=manifest,
            upload_bindings=upload_bindings,
        )
        for member, staged in bound:
            descriptor = self._open_verified(member.content_sha256, staged)
            destination = target / member.input_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with os.fdopen(descriptor, "rb", closefd=True) as incoming:
                    descriptor = -1
                    with destination.open("xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing, length=_CHUNK_BYTES)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
            except OSError as exc:
                raise InitWebStagingError(
                    "init_web_source_materialization_failed"
                ) from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    def preview_confirmed(
        self,
        *,
        session_id: str,
        manifest: ExecutionSourceManifest,
        upload_bindings: object,
    ) -> ExecutionSourceManifest:
        """Reverify inert staged bytes and return the strict canonical manifest."""

        bound = self._bound_uploads(
            session_id=session_id,
            manifest=manifest,
            upload_bindings=upload_bindings,
        )
        for member, staged in bound:
            descriptor = self._open_verified(member.content_sha256, staged)
            os.close(descriptor)
        return manifest

    def _bound_uploads(
        self,
        *,
        session_id: str,
        manifest: ExecutionSourceManifest,
        upload_bindings: object,
    ) -> list[tuple[ExecutionSourceManifestMember, StagedUpload]]:
        if not isinstance(upload_bindings, list):
            raise InitWebStagingError("init_web_source_bindings_invalid")
        bindings: dict[str, str] = {}
        for item in upload_bindings:
            if type(item) is not dict or set(item) != {"input_path", "upload_handle"}:
                raise InitWebStagingError("init_web_source_bindings_invalid")
            input_path = item.get("input_path")
            handle = item.get("upload_handle")
            if not isinstance(input_path, str) or not isinstance(handle, str):
                raise InitWebStagingError("init_web_source_bindings_invalid")
            if input_path in bindings or handle in bindings.values():
                raise InitWebStagingError("init_web_source_bindings_invalid")
            bindings[input_path] = handle
        expected_paths = [member.input_path for member in manifest.members]
        if set(bindings) != set(expected_paths) or len(bindings) != len(expected_paths):
            raise InitWebStagingError("init_web_source_bindings_invalid")

        verified: list[tuple[ExecutionSourceManifestMember, StagedUpload]] = []
        aggregate_size = 0
        for member in manifest.members:
            staged = self._uploads.get(bindings[member.input_path])
            if staged is None or staged.session_id != session_id:
                raise InitWebStagingError("init_web_source_handle_invalid")
            aggregate_size += staged.byte_count
            if aggregate_size > MAX_SOURCE_AGGREGATE_BYTES:
                raise InitWebStagingError("init_web_source_aggregate_too_large")
            verified.append((member, staged))
        return verified

    @staticmethod
    def _open_verified(expected_sha256: str, staged: StagedUpload) -> int:
        descriptor = -1
        try:
            descriptor = os.open(
                staged.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                raise InitWebStagingError("init_web_source_handle_invalid")
            if observed.st_size > MAX_SOURCE_MEMBER_BYTES:
                raise InitWebStagingError("init_web_source_member_too_large")
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(descriptor, _CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_SOURCE_MEMBER_BYTES:
                    raise InitWebStagingError("init_web_source_member_too_large")
                digest.update(chunk)
            if size != staged.byte_count or digest.hexdigest() != expected_sha256:
                raise InitWebStagingError("init_web_source_hash_mismatch")
            os.lseek(descriptor, 0, os.SEEK_SET)
            return descriptor
        except InitWebStagingError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise InitWebStagingError("init_web_source_handle_invalid") from exc


__all__ = [
    "InitWebStaging",
    "InitWebStagingError",
    "MAX_SOURCE_AGGREGATE_BYTES",
    "MAX_SOURCE_MEMBER_BYTES",
    "MAX_SOURCE_MEMBERS",
    "StagedUpload",
]
