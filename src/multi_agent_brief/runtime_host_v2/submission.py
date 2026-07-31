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
MAX_PROVIDER_RESPONSE_BYTES = 4 * 1024 * 1024
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
    proposal_bytes: bytes
    content_bytes: bytes
    raw_payload_bytes: bytes | None
    proposal_sha256: str
    content_sha256: str
    raw_payload_sha256: str | None
    payload_size_bytes: int


@dataclass(frozen=True)
class VerifiedSourceStage:
    root: Path
    stage_kind: Literal["source_pack", "provider_outcome"]
    request_fingerprint: str
    members: tuple[StagedSourceMember, ...]
    manifest_bytes: bytes | None
    manifest_sha256: str | None
    provider_response_bytes: bytes | None
    provider_response_sha256: str | None
    provider_status_code: int | None


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
    stage_kind: Literal["source_pack", "provider_outcome"]
    request_fingerprint: str
    manifest_sha256: str | None
    provider_response_sha256: str | None = None
    provider_status_code: int | None = None
    members: tuple[_StageMember, ...] = Field(
        min_length=0,
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
        if self.provider_response_sha256 is not None:
            values.append(self.provider_response_sha256)
        if (self.provider_response_sha256 is None) != (
            self.provider_status_code is None
        ):
            raise ValueError("provider response identity is incomplete")
        if self.provider_status_code is not None and self.provider_status_code != 200:
            raise ValueError("provider response status is invalid")
        if self.stage_kind == "source_pack":
            if not self.members:
                raise ValueError("source pack stage requires members")
        elif self.provider_response_sha256 is None:
            raise ValueError("provider outcome stage requires a response")
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


def _stage_root_metadata_if_present(root: Path) -> os.stat_result | None:
    """Return no-follow metadata; only lexical ENOENT means absence."""

    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeHostError("runtime_source_staging_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeHostError("runtime_source_staging_invalid")
    return metadata


def _stage_metadata_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
    )


def _stage_metadata_matches(
    current: os.stat_result,
    expected: os.stat_result,
) -> bool:
    return _stage_metadata_identity(current) == _stage_metadata_identity(expected)


def _supports_retained_stage_descriptors() -> bool:
    return (
        os.name != "nt"
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.scandir in os.supports_fd
        and getattr(os, "O_DIRECTORY", 0) != 0
        and getattr(os, "O_NOFOLLOW", 0) != 0
    )


@dataclass
class _DescriptorStageDirectory:
    descriptor: int
    metadata: os.stat_result
    parent_descriptor: int | None
    entry_name: str | None
    expected_names: frozenset[str] | None = None


@dataclass(frozen=True)
class _DescriptorStageLeaf:
    parent_descriptor: int
    entry_name: str
    metadata: os.stat_result


class _DescriptorStageSnapshot:
    """Read one stage through retained no-follow directory descriptors."""

    def __init__(self, root: Path, root_metadata: os.stat_result) -> None:
        self._root = root
        self._directories: list[_DescriptorStageDirectory] = []
        self._leaves: list[_DescriptorStageLeaf] = []
        descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if not _stage_metadata_matches(opened, root_metadata) or not stat.S_ISDIR(
                opened.st_mode
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
        except Exception:
            os.close(descriptor)
            raise
        self._root_directory = _DescriptorStageDirectory(
            descriptor=descriptor,
            metadata=root_metadata,
            parent_descriptor=None,
            entry_name=None,
        )
        self._directories.append(self._root_directory)
        self._sources_directory: _DescriptorStageDirectory | None = None
        self._member_directories: dict[str, _DescriptorStageDirectory] = {}

    def __enter__(self) -> "_DescriptorStageSnapshot":
        return self

    def __exit__(self, *_args: object) -> None:
        for directory in reversed(self._directories):
            os.close(directory.descriptor)

    @staticmethod
    def _directory_names(directory: _DescriptorStageDirectory) -> frozenset[str]:
        return frozenset(item.name for item in os.scandir(directory.descriptor))

    @staticmethod
    def _entry_metadata(
        directory: _DescriptorStageDirectory,
        entry_name: str,
    ) -> os.stat_result:
        return os.stat(
            entry_name,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )

    def _names(self, directory: _DescriptorStageDirectory) -> frozenset[str]:
        names = self._directory_names(directory)
        if directory.expected_names is None:
            directory.expected_names = names
        elif directory.expected_names != names:
            raise RuntimeHostError("runtime_source_staging_invalid")
        return names

    def _open_directory(
        self,
        parent: _DescriptorStageDirectory,
        entry_name: str,
    ) -> _DescriptorStageDirectory:
        metadata = self._entry_metadata(parent, entry_name)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeHostError("runtime_source_staging_invalid")
        descriptor = os.open(
            entry_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent.descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            if not _stage_metadata_matches(opened, metadata) or not stat.S_ISDIR(
                opened.st_mode
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
        except Exception:
            os.close(descriptor)
            raise
        directory = _DescriptorStageDirectory(
            descriptor=descriptor,
            metadata=metadata,
            parent_descriptor=parent.descriptor,
            entry_name=entry_name,
        )
        self._directories.append(directory)
        return directory

    def root_names(self) -> frozenset[str]:
        return self._names(self._root_directory)

    def sources_names(self) -> frozenset[str]:
        if self._sources_directory is None:
            self._sources_directory = self._open_directory(
                self._root_directory,
                "sources",
            )
        return self._names(self._sources_directory)

    def member_names(self, member_id: str) -> frozenset[str]:
        if self._sources_directory is None:
            raise RuntimeHostError("runtime_source_staging_invalid")
        directory = self._member_directories.get(member_id)
        if directory is None:
            directory = self._open_directory(self._sources_directory, member_id)
            self._member_directories[member_id] = directory
        return self._names(directory)

    def _read_file(
        self,
        directory: _DescriptorStageDirectory,
        entry_name: str,
        *,
        max_size: int,
    ) -> bytes:
        metadata = self._entry_metadata(directory, entry_name)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > max_size
        ):
            raise RuntimeHostError("runtime_source_staging_invalid")
        descriptor = os.open(
            entry_name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory.descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not _stage_metadata_matches(opened, metadata)
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
        finally:
            os.close(descriptor)
        current = self._entry_metadata(directory, entry_name)
        if not _stage_metadata_matches(current, metadata):
            raise RuntimeHostError("runtime_source_staging_invalid")
        self._leaves.append(
            _DescriptorStageLeaf(
                parent_descriptor=directory.descriptor,
                entry_name=entry_name,
                metadata=metadata,
            )
        )
        return bytes(payload)

    def read_root_file(self, entry_name: str, *, max_size: int) -> bytes:
        return self._read_file(
            self._root_directory,
            entry_name,
            max_size=max_size,
        )

    def read_member_file(
        self,
        member_id: str,
        entry_name: str,
        *,
        max_size: int,
    ) -> bytes:
        directory = self._member_directories.get(member_id)
        if directory is None:
            raise RuntimeHostError("runtime_source_staging_invalid")
        return self._read_file(directory, entry_name, max_size=max_size)

    def finish(self) -> None:
        current_root = self._root.lstat()
        if (
            stat.S_ISLNK(current_root.st_mode)
            or not stat.S_ISDIR(current_root.st_mode)
            or not _stage_metadata_matches(current_root, self._root_directory.metadata)
        ):
            raise RuntimeHostError("runtime_source_staging_invalid")
        for directory in self._directories:
            opened = os.fstat(directory.descriptor)
            if not stat.S_ISDIR(opened.st_mode) or not _stage_metadata_matches(
                opened, directory.metadata
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
            if (
                directory.parent_descriptor is not None
                and directory.entry_name is not None
            ):
                current = os.stat(
                    directory.entry_name,
                    dir_fd=directory.parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    stat.S_ISLNK(current.st_mode)
                    or not stat.S_ISDIR(current.st_mode)
                    or not _stage_metadata_matches(current, directory.metadata)
                ):
                    raise RuntimeHostError("runtime_source_staging_invalid")
            if (
                directory.expected_names is not None
                and self._directory_names(directory) != directory.expected_names
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
        for leaf in self._leaves:
            current = os.stat(
                leaf.entry_name,
                dir_fd=leaf.parent_descriptor,
                follow_symlinks=False,
            )
            if (
                stat.S_ISLNK(current.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or not _stage_metadata_matches(current, leaf.metadata)
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")


@dataclass
class _PathStageDirectory:
    path: Path
    metadata: os.stat_result
    expected_names: frozenset[str] | None = None


@dataclass(frozen=True)
class _PathStageLeaf:
    path: Path
    metadata: os.stat_result


class _PathStageSnapshot:
    """Bounded identity-revalidating fallback for platforms without dir-fd."""

    def __init__(self, root: Path, root_metadata: os.stat_result) -> None:
        self._root_directory = _PathStageDirectory(root, root_metadata)
        self._directories = [self._root_directory]
        self._leaves: list[_PathStageLeaf] = []
        self._sources_directory: _PathStageDirectory | None = None
        self._member_directories: dict[str, _PathStageDirectory] = {}

    def __enter__(self) -> "_PathStageSnapshot":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    @staticmethod
    def _directory_names(directory: _PathStageDirectory) -> frozenset[str]:
        return frozenset(item.name for item in os.scandir(directory.path))

    @staticmethod
    def _metadata(path: Path) -> os.stat_result:
        return path.lstat()

    def _revalidate(self) -> None:
        for directory in self._directories:
            current = self._metadata(directory.path)
            if (
                stat.S_ISLNK(current.st_mode)
                or not stat.S_ISDIR(current.st_mode)
                or not _stage_metadata_matches(current, directory.metadata)
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
            if (
                directory.expected_names is not None
                and self._directory_names(directory) != directory.expected_names
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
        for leaf in self._leaves:
            current = self._metadata(leaf.path)
            if (
                stat.S_ISLNK(current.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or not _stage_metadata_matches(current, leaf.metadata)
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")

    def _names(self, directory: _PathStageDirectory) -> frozenset[str]:
        self._revalidate()
        names = self._directory_names(directory)
        if directory.expected_names is None:
            directory.expected_names = names
        elif directory.expected_names != names:
            raise RuntimeHostError("runtime_source_staging_invalid")
        self._revalidate()
        return names

    def _open_directory(
        self,
        parent: _PathStageDirectory,
        entry_name: str,
    ) -> _PathStageDirectory:
        self._revalidate()
        path = parent.path / entry_name
        metadata = self._metadata(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeHostError("runtime_source_staging_invalid")
        directory = _PathStageDirectory(path, metadata)
        self._directories.append(directory)
        self._revalidate()
        return directory

    def root_names(self) -> frozenset[str]:
        return self._names(self._root_directory)

    def sources_names(self) -> frozenset[str]:
        if self._sources_directory is None:
            self._sources_directory = self._open_directory(
                self._root_directory,
                "sources",
            )
        return self._names(self._sources_directory)

    def member_names(self, member_id: str) -> frozenset[str]:
        if self._sources_directory is None:
            raise RuntimeHostError("runtime_source_staging_invalid")
        directory = self._member_directories.get(member_id)
        if directory is None:
            directory = self._open_directory(self._sources_directory, member_id)
            self._member_directories[member_id] = directory
        return self._names(directory)

    def _read_file(
        self,
        directory: _PathStageDirectory,
        entry_name: str,
        *,
        max_size: int,
    ) -> bytes:
        self._revalidate()
        path = directory.path / entry_name
        metadata = self._metadata(path)
        payload = _read_regular_bytes(path, max_size=max_size)
        self._leaves.append(_PathStageLeaf(path, metadata))
        self._revalidate()
        return payload

    def read_root_file(self, entry_name: str, *, max_size: int) -> bytes:
        return self._read_file(
            self._root_directory,
            entry_name,
            max_size=max_size,
        )

    def read_member_file(
        self,
        member_id: str,
        entry_name: str,
        *,
        max_size: int,
    ) -> bytes:
        directory = self._member_directories.get(member_id)
        if directory is None:
            raise RuntimeHostError("runtime_source_staging_invalid")
        return self._read_file(directory, entry_name, max_size=max_size)

    def finish(self) -> None:
        self._revalidate()


def _open_stage_snapshot(
    root: Path,
    root_metadata: os.stat_result,
) -> _DescriptorStageSnapshot | _PathStageSnapshot:
    if _supports_retained_stage_descriptors():
        return _DescriptorStageSnapshot(root, root_metadata)
    return _PathStageSnapshot(root, root_metadata)


def load_source_stage(
    workspace: Path,
    *,
    stage_identity: str,
    request_fingerprint: str,
    expected_manifest_sha256: str | None,
    expected_stage_kind: Literal["source_pack", "provider_outcome"] = "source_pack",
) -> VerifiedSourceStage | None:
    """Reverify an existing inert stage without consulting mutable inputs."""

    root = source_stage_root(workspace, stage_identity)
    metadata = _stage_root_metadata_if_present(root)
    if metadata is None:
        return None
    try:
        with _open_stage_snapshot(root, metadata) as reader:
            attestation_bytes = reader.read_root_file(
                "stage_attestation.json",
                max_size=_MAX_STAGE_CONTRACT_BYTES,
            )
            attestation = _StageAttestation.model_validate_json(
                attestation_bytes,
                strict=True,
            )
            if attestation.request_fingerprint != request_fingerprint:
                raise RuntimeHostError("submission_replay_conflict")
            if attestation.stage_kind != expected_stage_kind:
                raise RuntimeHostError("runtime_source_staging_invalid")
            if attestation.manifest_sha256 != expected_manifest_sha256:
                raise RuntimeHostError("runtime_source_staging_invalid")
            expected_root_members = {"sources", "stage_attestation.json"}
            if expected_manifest_sha256 is not None:
                expected_root_members.add("source_manifest.json")
            if attestation.provider_response_sha256 is not None:
                expected_root_members.add("provider_response.json")
            if reader.root_names() != expected_root_members:
                raise RuntimeHostError("runtime_source_staging_invalid")
            expected_member_ids = {item.member_id for item in attestation.members}
            if reader.sources_names() != expected_member_ids:
                raise RuntimeHostError("runtime_source_staging_invalid")
            manifest_bytes: bytes | None = None
            if expected_manifest_sha256 is not None:
                manifest_bytes = reader.read_root_file(
                    "source_manifest.json",
                    max_size=MAX_SOURCE_MANIFEST_BYTES,
                )
                if (
                    not manifest_bytes
                    or sha256_hex(manifest_bytes) != expected_manifest_sha256
                ):
                    raise RuntimeHostError("runtime_source_staging_invalid")
            provider_response_bytes: bytes | None = None
            if attestation.provider_response_sha256 is not None:
                provider_response_bytes = reader.read_root_file(
                    "provider_response.json",
                    max_size=MAX_PROVIDER_RESPONSE_BYTES,
                )
                if (
                    not provider_response_bytes
                    or sha256_hex(provider_response_bytes)
                    != attestation.provider_response_sha256
                ):
                    raise RuntimeHostError("runtime_source_staging_invalid")
            staged: list[StagedSourceMember] = []
            aggregate_size = 0
            for declared in attestation.members:
                expected_names = {"source_proposal.json", "source_content.bin"}
                if declared.raw_payload_sha256 is not None:
                    expected_names.add("source_raw.json")
                if reader.member_names(declared.member_id) != expected_names:
                    raise RuntimeHostError("runtime_source_staging_invalid")
                proposal_bytes = reader.read_member_file(
                    declared.member_id,
                    "source_proposal.json",
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
                content_bytes = reader.read_member_file(
                    declared.member_id,
                    "source_content.bin",
                    max_size=MAX_SOURCE_MEMBER_BYTES,
                )
                content_digest = sha256_hex(content_bytes)
                content_size = len(content_bytes)
                if (
                    content_size == 0
                    or content_digest != declared.content_sha256
                    or content_digest != proposal.content_sha256
                ):
                    raise RuntimeHostError("runtime_source_staging_invalid")
                raw_payload_bytes: bytes | None = None
                raw_size = 0
                if declared.raw_payload_sha256 is not None:
                    raw_payload_bytes = reader.read_member_file(
                        declared.member_id,
                        "source_raw.json",
                        max_size=MAX_SOURCE_MEMBER_BYTES,
                    )
                    raw_digest = sha256_hex(raw_payload_bytes)
                    raw_size = len(raw_payload_bytes)
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
                        proposal_bytes=proposal_bytes,
                        content_bytes=content_bytes,
                        raw_payload_bytes=raw_payload_bytes,
                        proposal_sha256=declared.proposal_sha256,
                        content_sha256=content_digest,
                        raw_payload_sha256=declared.raw_payload_sha256,
                        payload_size_bytes=payload_size,
                    )
                )
            reader.finish()
        return VerifiedSourceStage(
            root=root,
            stage_kind=attestation.stage_kind,
            request_fingerprint=request_fingerprint,
            members=tuple(staged),
            manifest_bytes=manifest_bytes,
            manifest_sha256=expected_manifest_sha256,
            provider_response_bytes=provider_response_bytes,
            provider_response_sha256=attestation.provider_response_sha256,
            provider_status_code=attestation.provider_status_code,
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
            stage_kind="source_pack",
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
    provider_response_bytes: bytes | None = None,
    provider_status_code: int | None = None,
    stage_kind: Literal["source_pack", "provider_outcome"] = "source_pack",
) -> VerifiedSourceStage:
    """Bound and stage one deterministic provider result set."""

    existing = load_source_stage(
        workspace,
        stage_identity=stage_identity,
        request_fingerprint=request_fingerprint,
        expected_manifest_sha256=None,
        expected_stage_kind=stage_kind,
    )
    if existing is not None:
        return existing
    if len(members) > MAX_SOURCE_PACK_MEMBERS:
        raise RuntimeHostError("runtime_source_pack_invalid")
    if (
        provider_response_bytes is not None
        and (
            not provider_response_bytes
            or len(provider_response_bytes) > MAX_PROVIDER_RESPONSE_BYTES
            or provider_status_code != 200
        )
    ) or (provider_response_bytes is None and provider_status_code is not None):
        raise RuntimeHostError("runtime_source_pack_invalid")
    if (stage_kind == "source_pack" and not members) or (
        stage_kind == "provider_outcome" and provider_response_bytes is None
    ):
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
        provider_response_sha256: str | None = None
        if provider_response_bytes is not None:
            _write_regular_bytes(
                building / "provider_response.json",
                provider_response_bytes,
            )
            provider_response_sha256 = sha256_hex(provider_response_bytes)
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
            stage_kind=stage_kind,
            request_fingerprint=request_fingerprint,
            manifest_sha256=None,
            provider_response_sha256=provider_response_sha256,
            provider_status_code=provider_status_code,
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
        expected_stage_kind=stage_kind,
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
    stage_kind: Literal["source_pack", "provider_outcome"],
    request_fingerprint: str,
    manifest_sha256: str | None,
    provider_response_sha256: str | None = None,
    provider_status_code: int | None = None,
    members: tuple[_StageMember, ...],
) -> None:
    attestation = _StageAttestation(
        format=_STAGE_FORMAT,
        stage_kind=stage_kind,
        request_fingerprint=request_fingerprint,
        manifest_sha256=manifest_sha256,
        provider_response_sha256=provider_response_sha256,
        provider_status_code=provider_status_code,
        members=members,
    )
    _write_regular_bytes(
        building / "stage_attestation.json",
        canonical_json_bytes(attestation.model_dump(mode="json")),
    )


def _publish_stage(building: Path, root: Path) -> None:
    try:
        existing = _stage_root_metadata_if_present(root)
    except RuntimeHostError:
        _discard_path(building)
        raise
    if existing is not None:
        _discard_path(building)
        return
    try:
        os.rename(building, root)
    except FileExistsError:
        try:
            existing = _stage_root_metadata_if_present(root)
        except RuntimeHostError:
            _discard_path(building)
            raise
        _discard_path(building)
        if existing is None:
            raise RuntimeHostError("runtime_source_staging_invalid") from None
    except OSError:
        try:
            existing = _stage_root_metadata_if_present(root)
        except RuntimeHostError:
            _discard_path(building)
            raise
        if existing is None:
            raise
        _discard_path(building)


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
