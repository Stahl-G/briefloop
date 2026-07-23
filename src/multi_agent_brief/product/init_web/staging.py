"""Bounded, non-authoritative upload staging for the one-shot init server."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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
    device: int
    inode: int


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
                device=target.stat().st_dev,
                inode=target.stat().st_ino,
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
        self._materialize_bound(bound, target=target)

    def materialize_canonical(
        self,
        *,
        session_id: str,
        mode: str,
        source_metadata: object,
        upload_bindings: object,
        manifest: ExecutionSourceManifest,
        target: Path,
    ) -> None:
        """Regenerate and materialize the exact server-confirmed source set."""

        regenerated, ordered_uploads = self._canonical_manifest_and_uploads(
            session_id=session_id,
            mode=mode,
            source_metadata=source_metadata,
            upload_bindings=upload_bindings,
        )
        if regenerated != manifest:
            raise InitWebStagingError("init_web_source_manifest_invalid")
        self._materialize_bound(
            list(zip(manifest.members, ordered_uploads, strict=True)),
            target=target,
        )

    @staticmethod
    def _materialize_bound(
        bound: list[tuple[ExecutionSourceManifestMember, StagedUpload]],
        *,
        target: Path,
    ) -> None:
        for member, staged in bound:
            descriptor = InitWebStaging._open_verified(
                member.content_sha256, staged
            )
            destination = target / member.input_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with os.fdopen(descriptor, "rb", closefd=True) as incoming:
                    descriptor = -1
                    with destination.open("xb") as outgoing:
                        digest = hashlib.sha256()
                        copied = 0
                        while True:
                            chunk = incoming.read(_CHUNK_BYTES)
                            if not chunk:
                                break
                            copied += len(chunk)
                            digest.update(chunk)
                            outgoing.write(chunk)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                if copied != staged.byte_count or digest.hexdigest() != staged.sha256:
                    raise InitWebStagingError("init_web_source_hash_mismatch")
                InitWebStaging._verify_materialized(destination, staged, member)
            except OSError as exc:
                raise InitWebStagingError(
                    "init_web_source_materialization_failed"
                ) from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    @staticmethod
    def _verify_materialized(
        destination: Path,
        staged: StagedUpload,
        member: ExecutionSourceManifestMember,
    ) -> None:
        destination_digest = hashlib.sha256()
        destination_size = 0
        with destination.open("rb") as materialized:
            while True:
                chunk = materialized.read(_CHUNK_BYTES)
                if not chunk:
                    break
                destination_size += len(chunk)
                destination_digest.update(chunk)
        if (
            destination_size != staged.byte_count
            or destination_digest.hexdigest() != staged.sha256
            or member.content_sha256 != staged.sha256
        ):
            raise InitWebStagingError("init_web_source_hash_mismatch")

    def canonical_manifest(
        self,
        *,
        session_id: str,
        mode: str,
        source_metadata: object,
        upload_bindings: object,
    ) -> ExecutionSourceManifest:
        """Derive the only canonical manifest from semantic metadata and staging."""

        manifest, _uploads = self._canonical_manifest_and_uploads(
            session_id=session_id,
            mode=mode,
            source_metadata=source_metadata,
            upload_bindings=upload_bindings,
        )
        return manifest

    def canonical_manifest_details(
        self,
        *,
        session_id: str,
        mode: str,
        source_metadata: object,
        upload_bindings: object,
    ) -> tuple[ExecutionSourceManifest, tuple[StagedUpload, ...]]:
        manifest, uploads = self._canonical_manifest_and_uploads(
            session_id=session_id,
            mode=mode,
            source_metadata=source_metadata,
            upload_bindings=upload_bindings,
        )
        return manifest, tuple(uploads)

    def _canonical_manifest_and_uploads(
        self,
        *,
        session_id: str,
        mode: str,
        source_metadata: object,
        upload_bindings: object,
    ) -> tuple[ExecutionSourceManifest, list[StagedUpload]]:
        if mode not in {"imported", "generated"} or not isinstance(
            source_metadata, list
        ):
            raise InitWebStagingError("init_web_source_manifest_invalid")
        if not isinstance(upload_bindings, list) or len(upload_bindings) != len(
            source_metadata
        ):
            raise InitWebStagingError("init_web_source_bindings_invalid")
        by_index: dict[int, StagedUpload] = {}
        for raw in upload_bindings:
            if type(raw) is not dict or set(raw) != {"metadata_index", "upload_handle"}:
                raise InitWebStagingError("init_web_source_bindings_invalid")
            index = raw.get("metadata_index")
            handle = raw.get("upload_handle")
            if type(index) is not int or not isinstance(handle, str) or index in by_index:
                raise InitWebStagingError("init_web_source_bindings_invalid")
            staged = self._uploads.get(handle)
            if staged is None or staged.session_id != session_id:
                raise InitWebStagingError("init_web_source_handle_invalid")
            by_index[index] = staged
        if set(by_index) != set(range(len(source_metadata))):
            raise InitWebStagingError("init_web_source_bindings_invalid")

        semantic_keys = {
            "source_id",
            "expected_content_sha256",
            "title",
            "publisher",
            "published_at",
            "retrieved_at",
            "origin_type",
            "acquisition_method",
            "material_kind",
            "provider",
            "original_url",
            "source_category",
            "retrieval_source_type",
            "underlying_evidence_type",
            "raw_underlying_evidence_type",
            "document_kind",
            "opened_at",
            "resolved_at",
        }
        rows: list[tuple[dict[str, object], StagedUpload, str]] = []
        for index, raw in enumerate(source_metadata):
            if type(raw) is not dict or not set(raw).issubset(semantic_keys):
                raise InitWebStagingError("init_web_source_manifest_invalid")
            metadata = dict(raw)
            source_id = metadata.get("source_id")
            if mode == "imported":
                if not isinstance(source_id, str):
                    raise InitWebStagingError("init_web_source_manifest_invalid")
                if metadata.get("expected_content_sha256") != by_index[index].sha256:
                    raise InitWebStagingError("init_web_source_hash_mismatch")
            elif source_id is not None:
                raise InitWebStagingError("init_web_source_manifest_invalid")
            stable = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            rows.append((metadata, by_index[index], stable))
        if mode == "imported":
            source_ids = [str(row[0]["source_id"]) for row in rows]
            if source_ids != sorted(set(source_ids)):
                raise InitWebStagingError("init_web_source_manifest_invalid")
        else:
            keys = [(row[2], row[1].filename, row[1].sha256) for row in rows]
            if len(keys) != len(set(keys)):
                raise InitWebStagingError("init_web_source_manifest_invalid")
            rows.sort(key=lambda row: (row[2], row[1].filename, row[1].sha256))

        members: list[dict[str, object]] = []
        for ordinal, (metadata, staged, _stable) in enumerate(rows, start=1):
            filename = _safe_source_name(staged.filename)
            input_path = f"input/sources/{ordinal:03d}-{filename}"
            original_url = metadata.get("original_url")
            locator = (
                {"kind": "web", "url": original_url}
                if isinstance(original_url, str) and original_url
                else {"kind": "file", "path": input_path}
            )
            members.append(
                {
                    "source_id": (
                        metadata["source_id"]
                        if mode == "imported"
                        else f"SRC-INIT-{ordinal:03d}"
                    ),
                    "input_path": input_path,
                    "content_sha256": staged.sha256,
                    "content_media_type": _observed_media_type(staged.filename),
                    "origin_type": metadata.get("origin_type", "uploaded_file"),
                    "acquisition_method": metadata.get(
                        "acquisition_method", "manual_upload"
                    ),
                    "material_kind": metadata.get("material_kind", "uploaded_file"),
                    "provider": metadata.get("provider"),
                    "locator": locator,
                    "title": metadata.get("title") or staged.filename,
                    "publisher": metadata.get("publisher"),
                    "published_at": metadata.get("published_at"),
                    "retrieved_at": metadata.get("retrieved_at"),
                    "source_category": metadata.get("source_category", "other"),
                    "retrieval_source_type": metadata.get(
                        "retrieval_source_type", "local_file"
                    ),
                    "underlying_evidence_type": metadata.get(
                        "underlying_evidence_type", "unknown"
                    ),
                    "raw_underlying_evidence_type": metadata.get(
                        "raw_underlying_evidence_type"
                    ),
                    "document_kind": metadata.get("document_kind"),
                    "opened_at": metadata.get("opened_at"),
                    "resolved_at": metadata.get("resolved_at"),
                }
            )
        try:
            manifest = ExecutionSourceManifest.model_validate(
                {
                    "schema_version": ExecutionSourceManifest.schema_id,
                    "members": members,
                },
                strict=True,
            )
            for member, staged in zip(
                manifest.members, (row[1] for row in rows), strict=True
            ):
                descriptor = self._open_verified(member.content_sha256, staged)
                os.close(descriptor)
            return manifest, [row[1] for row in rows]
        except InitWebStagingError:
            raise
        except Exception as exc:
            raise InitWebStagingError("init_web_source_manifest_invalid") from exc

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
            if observed.st_dev != staged.device or observed.st_ino != staged.inode:
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
            actual_sha256 = digest.hexdigest()
            if (
                size != staged.byte_count
                or actual_sha256 != staged.sha256
                or actual_sha256 != expected_sha256
            ):
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


def _safe_source_name(name: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in {".", "_", "-"} else "-"
        for character in Path(name).name
    )
    return cleaned or "source.bin"


def _observed_media_type(name: str) -> str:
    return {
        ".csv": "text/csv",
        ".html": "text/html",
        ".json": "application/json",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }.get(Path(name).suffix.lower(), "application/octet-stream")


__all__ = [
    "InitWebStaging",
    "InitWebStagingError",
    "MAX_SOURCE_AGGREGATE_BYTES",
    "MAX_SOURCE_MEMBER_BYTES",
    "MAX_SOURCE_MEMBERS",
    "StagedUpload",
]
