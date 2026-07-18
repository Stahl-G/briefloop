"""Immutable CheckoutRevision and publication-identity contracts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from multi_agent_brief.contracts.v2 import ArtifactRevision, PublicationIdentityV1
from multi_agent_brief.core_run_v2.checkout import (
    build_checkout_revision,
    build_publication_intent,
    publication_identity_sha256,
    publication_sibling_basename,
)
from multi_agent_brief.core_run_v2.errors import CoreRunError


NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def revision(
    artifact_id: str,
    path: str,
    digest: str,
    size: int = 4,
    *,
    revision_number: int = 1,
) -> ArtifactRevision:
    return ArtifactRevision.model_validate(
        {
            "schema_version": ArtifactRevision.schema_id,
            "run_id": "run-001",
            "artifact_id": artifact_id,
            "revision": revision_number,
            "path": path,
            "sha256": digest,
            "size_bytes": size,
            "frozen": True,
            "producer_kind": "workflow_stage",
            "producer_id": "editor",
            "created_at": "2026-07-18T00:00:00Z",
        },
        strict=True,
    )


def identity(revision_id: str, *, run_id: str = "run-001") -> PublicationIdentityV1:
    return PublicationIdentityV1.model_validate(
        {
            "schema_version": "briefloop-publication-identity/v1",
            "workspace_id": "workspace-001",
            "run_id": run_id,
            "transaction_id": "transaction-001",
            "checkout_revision_id": revision_id,
        },
        strict=True,
    )


def test_checkout_revision_is_sorted_content_addressed_and_reproducible() -> None:
    first = build_checkout_revision(
        workspace_id="workspace-001", run_id="run-001",
        transaction_id="transaction-001", created_at=NOW,
        artifact_revisions=(
            revision("b", "output/z.md", "b" * 64),
            revision("a", "output/a.md", "a" * 64),
        ), parent_checkout_revision_id=None,
    )
    second = build_checkout_revision(
        workspace_id="workspace-001", run_id="run-001",
        transaction_id="transaction-001", created_at=NOW,
        artifact_revisions=tuple(reversed((
            revision("b", "output/z.md", "b" * 64),
            revision("a", "output/a.md", "a" * 64),
        ))), parent_checkout_revision_id=None,
    )
    assert first == second
    assert [item.canonical_path for item in first.members] == [
        "output/a.md", "output/z.md"
    ]
    assert first.record.checkout_revision_id == f"crv_{first.record.tree_sha256}"
    assert b"mtime" not in first.manifest_bytes
    assert b"inode" not in first.manifest_bytes


def test_checkout_revision_rejects_casefold_collision_and_unfrozen_member() -> None:
    with pytest.raises(CoreRunError, match="checkout_revision_invalid"):
        build_checkout_revision(
            workspace_id="workspace-001", run_id="run-001",
            transaction_id="transaction-001", created_at=NOW,
            artifact_revisions=(
                revision("a", "output/Brief.md", "a" * 64),
                revision("b", "output/brief.md", "b" * 64),
            ), parent_checkout_revision_id=None,
        )
    bad = revision("a", "output/a.md", "a" * 64).model_copy(
        update={"frozen": False}
    )
    with pytest.raises(CoreRunError, match="checkout_revision_invalid"):
        build_checkout_revision(
            workspace_id="workspace-001", run_id="run-001",
            transaction_id="transaction-001", created_at=NOW,
            artifact_revisions=(bad,), parent_checkout_revision_id=None,
        )


def test_full_publication_identity_changes_across_run_and_drives_sibling_names() -> None:
    built = build_checkout_revision(
        workspace_id="workspace-001", run_id="run-001",
        transaction_id="transaction-001", created_at=NOW,
        artifact_revisions=(revision("a", "output/a.md", "a" * 64),),
        parent_checkout_revision_id=None,
    )
    first = identity(built.record.checkout_revision_id)
    other = identity(built.record.checkout_revision_id, run_id="run-002")
    assert publication_identity_sha256(first) != publication_identity_sha256(other)
    basename = publication_sibling_basename(first, 7, "tmp")
    assert basename == (
        f".briefloop-pub-v1-{publication_identity_sha256(first)}-00000007-tmp"
    )


def test_publication_delta_covers_create_replace_and_delete_in_path_order() -> None:
    before = build_checkout_revision(
        workspace_id="workspace-001", run_id="run-001",
        transaction_id="prior", created_at=NOW,
        artifact_revisions=(
            revision("replace", "output/replace.md", "a" * 64),
            revision("delete", "output/delete.md", "b" * 64),
        ), parent_checkout_revision_id=None,
    )
    after = build_checkout_revision(
        workspace_id="workspace-001", run_id="run-001",
        transaction_id="transaction-001", created_at=NOW,
        artifact_revisions=(
            revision("create", "output/create.md", "c" * 64),
            revision("replace", "output/replace.md", "d" * 64),
        ), parent_checkout_revision_id=before.record.checkout_revision_id,
    )
    intent, members = build_publication_intent(
        identity=identity(after.record.checkout_revision_id),
        pre=before, post=after, capability_profile_sha256="e" * 64,
    )
    assert intent.changed_member_count == 3
    assert [(m.canonical_path, m.pre_kind, m.post_kind) for m in members] == [
        ("output/create.md", "absent", "blob"),
        ("output/delete.md", "blob", "absent"),
        ("output/replace.md", "blob", "blob"),
    ]


def test_publication_delta_ignores_container_revision_when_projection_is_unchanged() -> None:
    before = build_checkout_revision(
        workspace_id="workspace-001", run_id="run-001",
        transaction_id="prior", created_at=NOW,
        artifact_revisions=(
            revision("brief", "output/brief.md", "a" * 64),
            revision("other", "output/other.md", "b" * 64),
        ), parent_checkout_revision_id=None,
    )
    after = build_checkout_revision(
        workspace_id="workspace-001", run_id="run-001",
        transaction_id="transaction-001", created_at=NOW,
        artifact_revisions=(
            revision(
                "brief", "output/brief.md", "a" * 64,
                revision_number=2,
            ),
            revision("other", "output/other.md", "c" * 64),
        ), parent_checkout_revision_id=before.record.checkout_revision_id,
    )
    _intent, members = build_publication_intent(
        identity=identity(after.record.checkout_revision_id),
        pre=before, post=after, capability_profile_sha256="e" * 64,
    )
    assert [item.canonical_path for item in members] == ["output/other.md"]
