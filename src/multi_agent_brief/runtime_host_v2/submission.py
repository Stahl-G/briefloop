"""Pure, non-authoritative admission helpers for runtime submissions.

The SQLite ControlStore remains the only business authority.  This module owns
bounded host-private staging so a source pack can be completely checked before
an Invocation or workspace scratch path is created.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from multi_agent_brief.contracts.v2 import SourceProposal
from multi_agent_brief.control_store.serialization import (
    canonical_json_bytes,
    sha256_hex,
)

from .errors import RuntimeHostError


MAX_SOURCE_PACK_MEMBERS = 256
MAX_SOURCE_MEMBER_BYTES = 16 * 1024 * 1024
MAX_SOURCE_PACK_BYTES = 256 * 1024 * 1024
MAX_SOURCE_MANIFEST_BYTES = 4 * 1024 * 1024
SOURCE_STREAM_CHUNK_BYTES = 1024 * 1024
_MAX_STAGE_CONTRACT_BYTES = 1024 * 1024
_STAGE_FORMAT = "briefloop-runtime-source-stage/v1"


@dataclass(frozen=True)
class HumanSourceStageInput:
    member_id: str
    input_path: str
    expected_content_sha256: str
    proposal_bytes: bytes


@dataclass(frozen=True)
class SourceStageBytesInput:
    member_id: str
    proposal_bytes: bytes
    content_bytes: bytes
    raw_payload_bytes: bytes | None


@dataclass(frozen=True)
class StagedSourceMember:
    member_id: str
    proposal_path: Path
    content_path: Path
    raw_payload_path: Path | None
    proposal_sha256: str
    content_sha256: str
    raw_payload_sha256: str | None
    payload_size_bytes: int


@dataclass(frozen=True)
class VerifiedSourceStage:
    root: Path
    request_fingerprint: str
    members: tuple[StagedSourceMember, ...]
    manifest_path: Path | None
    manifest_sha256: str | None


class _StageMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    member_id: str
    proposal_sha256: str
    content_sha256: str
    raw_payload_sha256: str | None
    payload_size_bytes: int = Field(ge=1, le=MAX_SOURCE_PACK_BYTES)


class _StageAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format: Literal["briefloop-runtime-source-stage/v1"]
    request_fingerprint: str
    manifest_sha256: str | None
    members: tuple[_StageMember, ...] = Field(
        min_length=1,
        max_length=MAX_SOURCE_PACK_MEMBERS,
    )

    @model_validator(mode="after")
    def identities_are_canonical(self) -> "_StageAttestation":
        member_ids = [item.member_id for item in self.members]
        if member_ids != sorted(set(member_ids)):
            raise ValueError("stage member identities are not canonical")
        values = [
            self.request_fingerprint,
            *(
                value
                for item in self.members
                for value in (
                    item.proposal_sha256,
                    item.content_sha256,
                    item.raw_payload_sha256,
                )
                if value is not None
            ),
        ]
        if self.manifest_sha256 is not None:
            values.append(self.manifest_sha256)
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in values
        ):
            raise ValueError("stage digest is invalid")
        if any(
            not member_id
            or Path(member_id).name != member_id
            or member_id in {".", ".."}
            for member_id in member_ids
        ):
            raise ValueError("stage member identity is unsafe")
        return self


def source_stage_root(workspace: Path, stage_identity: str) -> Path:
    """Return one deterministic host-private location, never workspace state."""

    workspace_key = hashlib.sha256(
        str(workspace.resolve(strict=True)).encode("utf-8")
    ).hexdigest()
    stage_key = hashlib.sha256(stage_identity.encode("utf-8")).hexdigest()
    return (
        Path(tempfile.gettempdir())
        / "briefloop-runtime-host-v2"
        / workspace_key
        / stage_key
    )


def load_source_stage(
    workspace: Path,
    *,
    stage_identity: str,
    request_fingerprint: str,
    expected_manifest_sha256: str | None,
) -> VerifiedSourceStage | None:
    """Reverify an existing inert stage without consulting mutable inputs."""

    root = source_stage_root(workspace, stage_identity)
    if not root.exists():
        return None
    try:
        metadata = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeHostError("runtime_source_staging_invalid")
        attestation_bytes = _read_regular_bytes(
            root / "stage_attestation.json",
            max_size=_MAX_STAGE_CONTRACT_BYTES,
        )
        attestation = _StageAttestation.model_validate_json(
            attestation_bytes,
            strict=True,
        )
        if attestation.request_fingerprint != request_fingerprint:
            raise RuntimeHostError("submission_replay_conflict")
        if attestation.manifest_sha256 != expected_manifest_sha256:
            raise RuntimeHostError("runtime_source_staging_invalid")
        expected_root_members = {"sources", "stage_attestation.json"}
        if expected_manifest_sha256 is not None:
            expected_root_members.add("source_manifest.json")
        if {item.name for item in os.scandir(root)} != expected_root_members:
            raise RuntimeHostError("runtime_source_staging_invalid")
        sources = root / "sources"
        source_metadata = sources.lstat()
        if sources.is_symlink() or not stat.S_ISDIR(source_metadata.st_mode):
            raise RuntimeHostError("runtime_source_staging_invalid")
        expected_member_ids = {item.member_id for item in attestation.members}
        if {item.name for item in os.scandir(sources)} != expected_member_ids:
            raise RuntimeHostError("runtime_source_staging_invalid")
        manifest_path: Path | None = None
        if expected_manifest_sha256 is not None:
            manifest_path = root / "source_manifest.json"
            digest, size = _hash_regular_file(
                manifest_path,
                max_size=MAX_SOURCE_MANIFEST_BYTES,
            )
            if size == 0 or digest != expected_manifest_sha256:
                raise RuntimeHostError("runtime_source_staging_invalid")
        staged: list[StagedSourceMember] = []
        aggregate_size = 0
        for declared in attestation.members:
            member_root = sources / declared.member_id
            member_metadata = member_root.lstat()
            if member_root.is_symlink() or not stat.S_ISDIR(member_metadata.st_mode):
                raise RuntimeHostError("runtime_source_staging_invalid")
            expected_names = {"source_proposal.json", "source_content.bin"}
            if declared.raw_payload_sha256 is not None:
                expected_names.add("source_raw.json")
            if {item.name for item in os.scandir(member_root)} != expected_names:
                raise RuntimeHostError("runtime_source_staging_invalid")
            proposal_path = member_root / "source_proposal.json"
            proposal_bytes = _read_regular_bytes(
                proposal_path,
                max_size=_MAX_STAGE_CONTRACT_BYTES,
            )
            if sha256_hex(proposal_bytes) != declared.proposal_sha256:
                raise RuntimeHostError("runtime_source_staging_invalid")
            try:
                proposal = SourceProposal.model_validate_json(
                    proposal_bytes,
                    strict=True,
                )
            except ValidationError as exc:
                raise RuntimeHostError("runtime_source_staging_invalid") from exc
            content_path = member_root / "source_content.bin"
            content_digest, content_size = _hash_regular_file(
                content_path,
                max_size=MAX_SOURCE_MEMBER_BYTES,
            )
            if (
                content_size == 0
                or content_digest != declared.content_sha256
                or content_digest != proposal.content_sha256
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
            raw_path: Path | None = None
            raw_size = 0
            if declared.raw_payload_sha256 is not None:
                raw_path = member_root / "source_raw.json"
                raw_digest, raw_size = _hash_regular_file(
                    raw_path,
                    max_size=MAX_SOURCE_MEMBER_BYTES,
                )
                if (
                    raw_size == 0
                    or raw_digest != declared.raw_payload_sha256
                    or raw_digest != proposal.raw_payload_sha256
                ):
                    raise RuntimeHostError("runtime_source_staging_invalid")
            elif proposal.raw_payload_sha256 is not None:
                raise RuntimeHostError("runtime_source_staging_invalid")
            payload_size = content_size + raw_size
            if payload_size != declared.payload_size_bytes:
                raise RuntimeHostError("runtime_source_staging_invalid")
            aggregate_size += payload_size
            if aggregate_size > MAX_SOURCE_PACK_BYTES:
                raise RuntimeHostError("runtime_source_staging_invalid")
            staged.append(
                StagedSourceMember(
                    member_id=declared.member_id,
                    proposal_path=proposal_path,
                    content_path=content_path,
                    raw_payload_path=raw_path,
                    proposal_sha256=declared.proposal_sha256,
                    content_sha256=content_digest,
                    raw_payload_sha256=declared.raw_payload_sha256,
                    payload_size_bytes=payload_size,
                )
            )
        return VerifiedSourceStage(
            root=root,
            request_fingerprint=request_fingerprint,
            members=tuple(staged),
            manifest_path=manifest_path,
            manifest_sha256=expected_manifest_sha256,
        )
    except RuntimeHostError:
        raise
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        raise RuntimeHostError("runtime_source_staging_invalid") from exc


def stage_human_source_pack(
    workspace: Path,
    *,
    stage_identity: str,
    request_fingerprint: str,
    manifest_bytes: bytes,
    expected_manifest_sha256: str,
    members: tuple[HumanSourceStageInput, ...],
) -> VerifiedSourceStage:
    """Stream one human pack into a complete host-private stage."""

    existing = load_source_stage(
        workspace,
        stage_identity=stage_identity,
        request_fingerprint=request_fingerprint,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    if existing is not None:
        return existing
    if (
        not members
        or len(members) > MAX_SOURCE_PACK_MEMBERS
        or len(manifest_bytes) > MAX_SOURCE_MANIFEST_BYTES
        or sha256_hex(manifest_bytes) != expected_manifest_sha256
    ):
        raise RuntimeHostError("runtime_human_request_invalid")
    _require_canonical_members(tuple(item.member_id for item in members))
    _require_canonical_paths(tuple(item.input_path for item in members))
    root, building = _stage_build_directory(workspace, stage_identity)
    try:
        _write_regular_bytes(building / "source_manifest.json", manifest_bytes)
        staged_members: list[_StageMember] = []
        aggregate_size = 0
        for item in members:
            member_root = building / "sources" / item.member_id
            member_root.mkdir(mode=0o700, parents=True)
            proposal = _strict_source_proposal(item.proposal_bytes)
            if proposal.content_sha256 != item.expected_content_sha256:
                raise RuntimeHostError("runtime_human_request_invalid")
            _write_regular_bytes(
                member_root / "source_proposal.json",
                item.proposal_bytes,
            )
            remaining = MAX_SOURCE_PACK_BYTES - aggregate_size
            content_digest, content_size = _stream_workspace_input(
                workspace,
                item.input_path,
                member_root / "source_content.bin",
                max_size=min(MAX_SOURCE_MEMBER_BYTES, remaining),
            )
            if content_digest != item.expected_content_sha256:
                raise RuntimeHostError("runtime_human_request_invalid")
            aggregate_size += content_size
            staged_members.append(
                _StageMember(
                    member_id=item.member_id,
                    proposal_sha256=sha256_hex(item.proposal_bytes),
                    content_sha256=content_digest,
                    raw_payload_sha256=None,
                    payload_size_bytes=content_size,
                )
            )
        _finish_stage(
            building,
            request_fingerprint=request_fingerprint,
            manifest_sha256=expected_manifest_sha256,
            members=tuple(staged_members),
        )
        _publish_stage(building, root)
    except Exception:
        _discard_path(building)
        raise
    loaded = load_source_stage(
        workspace,
        stage_identity=stage_identity,
        request_fingerprint=request_fingerprint,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    if loaded is None:  # pragma: no cover - guarded by publish
        raise RuntimeHostError("runtime_source_staging_invalid")
    return loaded


def stage_source_pack_bytes(
    workspace: Path,
    *,
    stage_identity: str,
    request_fingerprint: str,
    members: tuple[SourceStageBytesInput, ...],
) -> VerifiedSourceStage:
    """Bound and stage one deterministic provider result set."""

    existing = load_source_stage(
        workspace,
        stage_identity=stage_identity,
        request_fingerprint=request_fingerprint,
        expected_manifest_sha256=None,
    )
    if existing is not None:
        return existing
    if not members or len(members) > MAX_SOURCE_PACK_MEMBERS:
        raise RuntimeHostError("runtime_source_pack_invalid")
    _require_canonical_members(tuple(item.member_id for item in members))
    aggregate_size = 0
    for item in members:
        payload_size = len(item.content_bytes) + (
            0 if item.raw_payload_bytes is None else len(item.raw_payload_bytes)
        )
        if (
            not item.content_bytes
            or len(item.content_bytes) > MAX_SOURCE_MEMBER_BYTES
            or (
                item.raw_payload_bytes is not None
                and (
                    not item.raw_payload_bytes
                    or len(item.raw_payload_bytes) > MAX_SOURCE_MEMBER_BYTES
                )
            )
        ):
            raise RuntimeHostError("runtime_source_pack_invalid")
        aggregate_size += payload_size
        if aggregate_size > MAX_SOURCE_PACK_BYTES:
            raise RuntimeHostError("runtime_source_pack_invalid")
        proposal = _strict_source_proposal(item.proposal_bytes)
        if proposal.content_sha256 != sha256_hex(
            item.content_bytes
        ) or proposal.raw_payload_sha256 != (
            None
            if item.raw_payload_bytes is None
            else sha256_hex(item.raw_payload_bytes)
        ):
            raise RuntimeHostError("runtime_source_pack_invalid")
    root, building = _stage_build_directory(workspace, stage_identity)
    try:
        staged_members: list[_StageMember] = []
        for item in members:
            member_root = building / "sources" / item.member_id
            member_root.mkdir(mode=0o700, parents=True)
            _write_regular_bytes(
                member_root / "source_proposal.json",
                item.proposal_bytes,
            )
            _write_regular_bytes(
                member_root / "source_content.bin",
                item.content_bytes,
            )
            raw_digest: str | None = None
            if item.raw_payload_bytes is not None:
                _write_regular_bytes(
                    member_root / "source_raw.json",
                    item.raw_payload_bytes,
                )
                raw_digest = sha256_hex(item.raw_payload_bytes)
            staged_members.append(
                _StageMember(
                    member_id=item.member_id,
                    proposal_sha256=sha256_hex(item.proposal_bytes),
                    content_sha256=sha256_hex(item.content_bytes),
                    raw_payload_sha256=raw_digest,
                    payload_size_bytes=len(item.content_bytes)
                    + (
                        0
                        if item.raw_payload_bytes is None
                        else len(item.raw_payload_bytes)
                    ),
                )
            )
        _finish_stage(
            building,
            request_fingerprint=request_fingerprint,
            manifest_sha256=None,
            members=tuple(staged_members),
        )
        _publish_stage(building, root)
    except Exception:
        _discard_path(building)
        raise
    loaded = load_source_stage(
        workspace,
        stage_identity=stage_identity,
        request_fingerprint=request_fingerprint,
        expected_manifest_sha256=None,
    )
    if loaded is None:  # pragma: no cover - guarded by publish
        raise RuntimeHostError("runtime_source_staging_invalid")
    return loaded


def discard_source_stage(workspace: Path, *, stage_identity: str) -> None:
    """Best-effort cleanup of inert, non-authoritative host staging."""

    try:
        _discard_path(source_stage_root(workspace, stage_identity))
    except (OSError, RuntimeError, ValueError):
        return


def read_verified_staged_bytes(
    path: Path,
    *,
    expected_sha256: str,
    max_size: int = MAX_SOURCE_MEMBER_BYTES,
) -> bytes:
    """Detach one already-staged member from the same verified descriptor."""

    payload = _read_regular_bytes(path, max_size=max_size)
    if sha256_hex(payload) != expected_sha256:
        raise RuntimeHostError("runtime_source_staging_invalid")
    return payload


def _strict_source_proposal(payload: bytes) -> SourceProposal:
    try:
        return SourceProposal.model_validate_json(payload, strict=True)
    except ValidationError as exc:
        raise RuntimeHostError("runtime_source_pack_invalid") from exc


def _require_canonical_members(member_ids: tuple[str, ...]) -> None:
    if list(member_ids) != sorted(set(member_ids)) or any(
        not value or Path(value).name != value or value in {".", ".."}
        for value in member_ids
    ):
        raise RuntimeHostError("runtime_source_pack_invalid")


def _require_canonical_paths(paths: tuple[str, ...]) -> None:
    normalized = [Path(value).as_posix() for value in paths]
    if len(normalized) != len(set(normalized)):
        raise RuntimeHostError("runtime_human_request_invalid")


def _stage_build_directory(workspace: Path, stage_identity: str) -> tuple[Path, Path]:
    root = source_stage_root(workspace, stage_identity)
    parent = root.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = parent.lstat()
    if parent.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeHostError("runtime_source_staging_invalid")
    building = Path(tempfile.mkdtemp(prefix=".building-", dir=parent))
    (building / "sources").mkdir(mode=0o700)
    return root, building


def _finish_stage(
    building: Path,
    *,
    request_fingerprint: str,
    manifest_sha256: str | None,
    members: tuple[_StageMember, ...],
) -> None:
    attestation = _StageAttestation(
        format=_STAGE_FORMAT,
        request_fingerprint=request_fingerprint,
        manifest_sha256=manifest_sha256,
        members=members,
    )
    _write_regular_bytes(
        building / "stage_attestation.json",
        canonical_json_bytes(attestation.model_dump(mode="json")),
    )


def _publish_stage(building: Path, root: Path) -> None:
    try:
        os.rename(building, root)
    except FileExistsError:
        _discard_path(building)
    except OSError:
        if root.exists():
            _discard_path(building)
        else:
            raise


def _write_regular_bytes(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RuntimeHostError("runtime_source_staging_invalid")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise RuntimeHostError("runtime_source_staging_invalid")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stream_workspace_input(
    workspace: Path,
    relative: str,
    destination: Path,
    *,
    max_size: int,
) -> tuple[str, int]:
    if max_size < 1:
        raise RuntimeHostError("runtime_human_request_invalid")
    candidate = workspace / relative
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    try:
        current = workspace
        metadata: os.stat_result | None = None
        for part in Path(relative).parts:
            current = current / part
            metadata = current.lstat()
            if current.is_symlink():
                raise RuntimeHostError("runtime_human_request_invalid")
        if metadata is None or not stat.S_ISREG(metadata.st_mode):
            raise RuntimeHostError("runtime_human_request_invalid")
        source_descriptor = os.open(
            candidate,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(source_descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > max_size
        ):
            raise RuntimeHostError("runtime_human_request_invalid")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_descriptor, SOURCE_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                raise RuntimeHostError("runtime_human_request_invalid")
            digest.update(chunk)
            offset = 0
            while offset < len(chunk):
                written = os.write(destination_descriptor, chunk[offset:])
                if written <= 0:
                    raise RuntimeHostError("runtime_source_staging_invalid")
                offset += written
        if total == 0:
            raise RuntimeHostError("runtime_human_request_invalid")
        os.fsync(destination_descriptor)
        return digest.hexdigest(), total
    except RuntimeHostError:
        raise
    except OSError as exc:
        raise RuntimeHostError("runtime_human_request_invalid") from exc
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def _read_regular_bytes(path: Path, *, max_size: int) -> bytes:
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > max_size
        ):
            raise RuntimeHostError("runtime_source_staging_invalid")
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
                or opened.st_size > max_size
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
            payload = bytearray()
            while len(payload) <= max_size:
                chunk = os.read(
                    descriptor,
                    min(SOURCE_STREAM_CHUNK_BYTES, max_size + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) != opened.st_size or len(payload) > max_size:
                raise RuntimeHostError("runtime_source_staging_invalid")
            return bytes(payload)
        finally:
            os.close(descriptor)
    except RuntimeHostError:
        raise
    except OSError as exc:
        raise RuntimeHostError("runtime_source_staging_invalid") from exc


def _hash_regular_file(path: Path, *, max_size: int) -> tuple[str, int]:
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > max_size
        ):
            raise RuntimeHostError("runtime_source_staging_invalid")
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
                or opened.st_size > max_size
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, SOURCE_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    raise RuntimeHostError("runtime_source_staging_invalid")
                digest.update(chunk)
            return digest.hexdigest(), total
        finally:
            os.close(descriptor)
    except RuntimeHostError:
        raise
    except OSError as exc:
        raise RuntimeHostError("runtime_source_staging_invalid") from exc


def _discard_path(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path)


__all__: tuple[str, ...] = ()
