from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from multi_agent_brief.runtime_host_v2 import submission
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.submission import HumanSourceStageInput


def _proposal_bytes() -> bytes:
    return json.dumps(
        {
            "schema_version": "briefloop.source_proposal.v2",
            "proposal_id": "PROP-STAGE-001",
            "run_id": "RUN-STAGE-001",
            "source_id": "SRC-STAGE-001",
            "origin_type": "uploaded_file",
            "acquisition_method": "manual_upload",
            "material_kind": "uploaded_file",
            "provider": None,
            "locator": {"kind": "file", "path": "input/large.bin"},
            "title": "Bounded source",
            "publisher": None,
            "published_at": None,
            "retrieved_at": "2026-07-22T00:00:00Z",
            "source_category": "other",
            "retrieval_source_type": "local_file",
            "underlying_evidence_type": "unknown",
            "raw_underlying_evidence_type": None,
            "content_sha256": "0" * 64,
            "content_media_type": "application/octet-stream",
            "raw_payload_sha256": None,
            "raw_payload_media_type": None,
            "source_manifest_sha256": None,
            "manifest_local_file": None,
            "document_kind": None,
            "opened_at": None,
            "resolved_at": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _stage_request(workspace: Path) -> tuple[bytes, tuple[HumanSourceStageInput, ...]]:
    manifest = b'{"schema_version":"test.manifest.v1","sources":[]}'
    return manifest, (
        HumanSourceStageInput(
            member_id="SRC-STAGE-001",
            input_path="input/large.bin",
            expected_content_sha256="0" * 64,
            proposal_bytes=_proposal_bytes(),
        ),
    )


def test_human_stage_fstat_rejects_oversize_before_first_payload_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "input").mkdir(parents=True)
    source = workspace / "input" / "large.bin"
    source.write_bytes(b"")
    with source.open("r+b") as handle:
        handle.truncate(submission.MAX_SOURCE_MEMBER_BYTES + 1)
    manifest, members = _stage_request(workspace)
    reads = 0
    original_read = submission.os.read

    def counted_read(descriptor: int, size: int) -> bytes:
        nonlocal reads
        if os.fstat(descriptor).st_ino == source.stat().st_ino:
            reads += 1
        return original_read(descriptor, size)

    monkeypatch.setattr(submission.os, "read", counted_read)
    stage_identity = "fstat-oversize"

    with pytest.raises(RuntimeHostError, match="runtime_human_request_invalid"):
        submission.stage_human_source_pack(
            workspace,
            stage_identity=stage_identity,
            request_fingerprint="1" * 64,
            manifest_bytes=manifest,
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            members=members,
        )

    assert reads == 0
    assert not (workspace / "scratch").exists()
    assert not submission.source_stage_root(workspace, stage_identity).exists()


def test_human_stage_stream_aborts_when_bytes_outgrow_observed_fstat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "input").mkdir(parents=True)
    source = workspace / "input" / "large.bin"
    source.write_bytes(b"")
    with source.open("r+b") as handle:
        handle.truncate(submission.MAX_SOURCE_MEMBER_BYTES + 1)
    source_identity = source.stat().st_ino
    manifest, members = _stage_request(workspace)
    original_fstat = submission.os.fstat
    original_read = submission.os.read
    reads = 0

    def stale_size_fstat(descriptor: int):
        observed = original_fstat(descriptor)
        if observed.st_ino != source_identity:
            return observed
        values = list(observed)
        values[6] = submission.MAX_SOURCE_MEMBER_BYTES
        return os.stat_result(values)

    def counted_read(descriptor: int, size: int) -> bytes:
        nonlocal reads
        if original_fstat(descriptor).st_ino == source_identity:
            reads += 1
        return original_read(descriptor, size)

    monkeypatch.setattr(submission.os, "fstat", stale_size_fstat)
    monkeypatch.setattr(submission.os, "read", counted_read)
    stage_identity = "stream-oversize"

    with pytest.raises(RuntimeHostError, match="runtime_human_request_invalid"):
        submission.stage_human_source_pack(
            workspace,
            stage_identity=stage_identity,
            request_fingerprint="2" * 64,
            manifest_bytes=manifest,
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            members=members,
        )

    assert reads == submission.MAX_SOURCE_MEMBER_BYTES // (1024 * 1024) + 1
    assert not (workspace / "scratch").exists()
    assert not submission.source_stage_root(workspace, stage_identity).exists()
