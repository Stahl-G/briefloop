"""Adversarial dormant checkout publication and recovery matrix."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys

import pytest

from multi_agent_brief.contracts.v2 import (
    ArtifactRecord,
    ArtifactRevision,
    CheckoutPublicationAck,
    PublicationIdentityV1,
    ReceiptCheckoutBinding,
    RunIdentity,
)
from multi_agent_brief.control_store import (
    ControlStoreConflict,
    ControlStoreIntegrityError,
    SQLiteControlStore,
)
from multi_agent_brief.core_run_v2.checkout import (
    BuiltCheckoutRevision,
    build_checkout_revision,
    build_publication_intent,
)
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.publication import CheckoutPublicationEngine
from multi_agent_brief.core_run_v2.publication_platform import (
    capability_profile,
    open_retained_parent,
    probe_publication_capability,
)


NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def model(cls, **values):
    return cls.model_validate(
        {"schema_version": cls.schema_id, **values}, strict=True
    )


def artifact_revision(
    content: bytes,
    *,
    revision: int = 1,
    artifact_id: str = "reader_brief",
    path: str = "output/brief.md",
) -> ArtifactRevision:
    return model(
        ArtifactRevision,
        run_id="run-001", artifact_id=artifact_id, revision=revision,
        path=path, sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content), frozen=True,
        producer_kind="workflow_stage", producer_id="editor",
        created_at="2026-07-18T00:00:00Z",
    )


def commit_checkout(
    store: SQLiteControlStore,
    workspace: Path,
    *,
    transaction_id: str,
    expected_revision: int,
    revisions: tuple[tuple[ArtifactRevision, bytes], ...],
    parent: BuiltCheckoutRevision | None,
    publish: bool,
    publication_member_patch: dict[str, object] | None = None,
) -> tuple[BuiltCheckoutRevision, PublicationIdentityV1 | None]:
    current = tuple(item[0] for item in revisions)
    built = build_checkout_revision(
        workspace_id="workspace-001", run_id="run-001",
        transaction_id=transaction_id, created_at=NOW,
        artifact_revisions=current,
        parent_checkout_revision_id=(
            None if parent is None else parent.record.checkout_revision_id
        ),
    )
    unit = store.begin("run-001", transaction_id, "core-v2-test", expected_revision)
    if expected_revision == 0:
        unit.put_run(model(
            RunIdentity, run_id="run-001", workspace_id="workspace-001",
            runtime="operator", created_at="2026-07-18T00:00:00Z",
        ))
    for revision, content in revisions:
        unit.put_artifact(model(
            ArtifactRecord, run_id="run-001", artifact_id=revision.artifact_id,
            current_revision=revision.revision, status="valid",
            path=revision.path, required=True, format="markdown",
        ))
        unit.put_artifact_revision(revision, content)
    unit.put_checkout_revision(built.record)
    for member in built.members:
        unit.put_checkout_revision_member(member)
    binding = model(
        ReceiptCheckoutBinding,
        workspace_id="workspace-001", run_id="run-001",
        transaction_id=transaction_id, pre_run_id="run-001",
        pre_checkout_revision_id=(
            None if parent is None else parent.record.checkout_revision_id
        ), post_run_id="run-001",
        post_checkout_revision_id=built.record.checkout_revision_id,
    )
    unit.put_receipt_checkout_binding(binding)
    identity = None
    if publish:
        profile = capability_profile(workspace / "output")
        identity = PublicationIdentityV1.model_validate(
            {
                "schema_version": "briefloop-publication-identity/v1",
                "workspace_id": "workspace-001", "run_id": "run-001",
                "transaction_id": transaction_id,
                "checkout_revision_id": built.record.checkout_revision_id,
            }, strict=True,
        )
        intent, changed = build_publication_intent(
            identity=identity, pre=parent, post=built,
            capability_profile_sha256=profile.sha256,
        )
        unit.put_checkout_publication_intent(intent)
        for member in changed:
            if member.ordinal == 0 and publication_member_patch is not None:
                member = member.model_copy(update=publication_member_patch)
            unit.put_checkout_publication_member(member)
    unit.commit()
    return built, identity


@pytest.fixture
def checkout(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    store = SQLiteControlStore.create(
        tmp_path / "briefloop.db", workspace_id="workspace-001",
        clock=lambda: NOW,
    )
    try:
        yield workspace, store
    finally:
        store.close()


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX publication")
def test_create_publish_attests_full_checkout_and_batch_acks(checkout) -> None:
    workspace, store = checkout
    content = b"reader brief\n"
    built, identity = commit_checkout(
        store, workspace, transaction_id="tx-create", expected_revision=0,
        revisions=((artifact_revision(content), content),), parent=None, publish=True,
    )
    assert identity is not None
    result = CheckoutPublicationEngine(workspace, store).publish(identity)
    assert result.status == "published"
    assert (workspace / "output/brief.md").read_bytes() == content
    snapshot = store.load_snapshot("run-001")
    assert len(snapshot.checkout_publication_acks) == 1
    assert snapshot.checkout_revisions == (built.record,)
    assert store.current_revision == 1


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX publication")
def test_exact_recovery_reuses_receipt_and_never_republishes(checkout) -> None:
    workspace, store = checkout
    content = b"reader brief\n"
    _built, identity = commit_checkout(
        store, workspace, transaction_id="tx-replay", expected_revision=0,
        revisions=((artifact_revision(content), content),), parent=None, publish=True,
    )
    assert identity is not None
    first = CheckoutPublicationEngine(workspace, store).publish(identity)
    inode = (workspace / "output/brief.md").stat().st_ino
    second = CheckoutPublicationEngine(workspace, store).recover(identity)
    assert first.status == second.status == "published"
    assert (workspace / "output/brief.md").stat().st_ino == inode
    assert store.current_revision == 1
    assert len(store.load_snapshot("run-001").checkout_publication_acks) == 1


def test_real_uow_rejects_off_delta_publication_member(checkout) -> None:
    workspace, store = checkout
    content = b"reader brief\n"
    with pytest.raises(ControlStoreConflict, match="relational_integrity_conflict"):
        commit_checkout(
            store, workspace, transaction_id="tx-off-delta", expected_revision=0,
            revisions=((artifact_revision(content), content),),
            parent=None, publish=True,
            publication_member_patch={"post_sha256": "f" * 64},
        )
    assert store.current_revision == 0


def test_store_rejects_ack_values_not_equal_to_reconstructed_delta(checkout) -> None:
    workspace, store = checkout
    content = b"reader brief\n"
    _built, identity = commit_checkout(
        store, workspace, transaction_id="tx-bad-ack", expected_revision=0,
        revisions=((artifact_revision(content), content),), parent=None, publish=True,
    )
    assert identity is not None
    intent, members, _acks, _observations = store.load_checkout_publication(identity)
    member = members[0]
    bad = CheckoutPublicationAck.model_validate(
        {
            "schema_version": CheckoutPublicationAck.schema_id,
            "identity": identity.model_dump(mode="json"),
            "ordinal": member.ordinal,
            "publication_identity_sha256": intent.publication_identity_sha256,
            "capability_profile_sha256": intent.capability_profile_sha256,
            "post_kind": "blob",
            "post_sha256": "f" * 64,
            "post_size": member.post_size,
            "verification": "post_verified_durable",
            "cleanup_policy": "retain_residue_v1",
            "appended_at": "2026-07-18T00:00:00Z",
        },
        strict=True,
    )
    with pytest.raises(
        ControlStoreIntegrityError,
        match="checkout_publication_journal_invalid",
    ):
        store.append_checkout_publication_acks((bad,))
    assert store.current_revision == 1


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX publication")
def test_nonpost_external_winner_is_preserved_and_no_ack(checkout) -> None:
    workspace, store = checkout
    content = b"expected\n"
    _built, identity = commit_checkout(
        store, workspace, transaction_id="tx-conflict", expected_revision=0,
        revisions=((artifact_revision(content), content),), parent=None, publish=True,
    )
    assert identity is not None
    target = workspace / "output/brief.md"
    target.write_bytes(b"external\n")
    result = CheckoutPublicationEngine(workspace, store).publish(identity)
    assert result == result.__class__(
        "commit_outcome_unknown", "checkout_projection_conflict"
    )
    assert target.read_bytes() == b"external\n"
    assert store.load_snapshot("run-001").checkout_publication_acks == ()
    assert store.current_revision == 1


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX publication")
def test_replace_retains_claim_and_records_non_authoritative_warning(checkout) -> None:
    workspace, store = checkout
    old, new = b"old\n", b"new\n"
    prior, _ = commit_checkout(
        store, workspace, transaction_id="tx-old", expected_revision=0,
        revisions=((artifact_revision(old), old),), parent=None, publish=False,
    )
    (workspace / "output/brief.md").write_bytes(old)
    current, identity = commit_checkout(
        store, workspace, transaction_id="tx-new", expected_revision=1,
        revisions=((artifact_revision(new, revision=2), new),),
        parent=prior, publish=True,
    )
    assert identity is not None
    result = CheckoutPublicationEngine(workspace, store).publish(identity)
    assert result.status == "published"
    assert "checkout_projection_cleanup_retained" in result.warnings
    assert (workspace / "output/brief.md").read_bytes() == new
    member = store.load_snapshot("run-001").checkout_publication_members[0]
    assert (workspace / "output" / member.claim_basename).read_bytes() == old
    assert current.record.parent_checkout_revision_id == prior.record.checkout_revision_id


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX publication")
def test_recovery_resumes_exact_after_claim_state(checkout) -> None:
    workspace, store = checkout
    old, new = b"old\n", b"new\n"
    prior, _ = commit_checkout(
        store, workspace, transaction_id="tx-claim-old", expected_revision=0,
        revisions=((artifact_revision(old), old),), parent=None, publish=False,
    )
    (workspace / "output/brief.md").write_bytes(old)
    _current, identity = commit_checkout(
        store, workspace, transaction_id="tx-claim-new", expected_revision=1,
        revisions=((artifact_revision(new, revision=2), new),),
        parent=prior, publish=True,
    )
    assert identity is not None

    def stop_after_claim(name, _identity, _ordinal):
        if name == "after_claim":
            raise CoreRunError("checkout_publication_io_error")

    first = CheckoutPublicationEngine(
        workspace, store, hook=stop_after_claim
    ).publish(identity)
    assert first.status == "commit_outcome_unknown"
    assert not (workspace / "output/brief.md").exists()
    recovered = CheckoutPublicationEngine(workspace, store).recover(identity)
    assert recovered.status == "published"
    assert (workspace / "output/brief.md").read_bytes() == new
    assert len(store.load_snapshot("run-001").checkout_publication_acks) == 1


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX publication")
def test_before_claim_mutation_fails_closed_without_ack(checkout) -> None:
    workspace, store = checkout
    old, new = b"old\n", b"new\n"
    prior, _ = commit_checkout(
        store, workspace, transaction_id="tx-race-old", expected_revision=0,
        revisions=((artifact_revision(old), old),), parent=None, publish=False,
    )
    target = workspace / "output/brief.md"
    target.write_bytes(old)
    _current, identity = commit_checkout(
        store, workspace, transaction_id="tx-race-new", expected_revision=1,
        revisions=((artifact_revision(new, revision=2), new),),
        parent=prior, publish=True,
    )
    assert identity is not None

    def mutate_before_claim(name, _identity, _ordinal):
        if name == "before_claim":
            target.write_bytes(b"third-party\n")

    result = CheckoutPublicationEngine(
        workspace, store, hook=mutate_before_claim
    ).publish(identity)
    assert result == result.__class__(
        "commit_outcome_unknown", "checkout_projection_conflict"
    )
    assert target.read_bytes() == b"third-party\n"
    assert store.load_snapshot("run-001").checkout_publication_acks == ()


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX publication")
def test_acked_fast_path_detects_drift_without_republication(checkout) -> None:
    workspace, store = checkout
    content = b"reader brief\n"
    _built, identity = commit_checkout(
        store, workspace, transaction_id="tx-acked-drift", expected_revision=0,
        revisions=((artifact_revision(content), content),), parent=None, publish=True,
    )
    assert identity is not None
    engine = CheckoutPublicationEngine(workspace, store)
    assert engine.publish(identity).status == "published"
    before_acks = store.load_snapshot("run-001").checkout_publication_acks
    target = workspace / "output/brief.md"
    target.write_bytes(b"drift\n")
    result = engine.recover(identity)
    assert result == result.__class__(
        "commit_outcome_unknown", "checkout_projection_conflict"
    )
    assert target.read_bytes() == b"drift\n"
    assert store.load_snapshot("run-001").checkout_publication_acks == before_acks


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX publication")
def test_unrelated_protected_drift_before_ack_blocks_complete_ack(checkout) -> None:
    workspace, store = checkout
    a, b = b"a\n", b"b\n"
    revisions = (
        (artifact_revision(a, artifact_id="a", path="output/a.md"), a),
        (artifact_revision(b, artifact_id="b", path="output/b.md"), b),
    )
    _built, identity = commit_checkout(
        store, workspace, transaction_id="tx-multi", expected_revision=0,
        revisions=revisions, parent=None, publish=True,
    )
    assert identity is not None

    def drift(name, _identity, ordinal):
        if name == "before_full_checkout_verify" and ordinal == -1:
            (workspace / "output/a.md").write_bytes(b"drift\n")

    result = CheckoutPublicationEngine(workspace, store, hook=drift).publish(identity)
    assert result.error_code == "checkout_projection_conflict"
    assert store.load_snapshot("run-001").checkout_publication_acks == ()


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX publication")
def test_retain_residue_never_invokes_cleanup_removal_hooks(checkout) -> None:
    workspace, store = checkout
    content = b"content\n"
    _built, identity = commit_checkout(
        store, workspace, transaction_id="tx-hooks", expected_revision=0,
        revisions=((artifact_revision(content), content),), parent=None, publish=True,
    )
    assert identity is not None
    called: list[str] = []
    result = CheckoutPublicationEngine(
        workspace, store, hook=lambda name, _identity, _ordinal: called.append(name)
    ).publish(identity)
    assert result.status == "published"
    assert not any("cleanup_claim_removal" in name for name in called)
    assert not any("cleanup_temp_removal" in name for name in called)
    assert not any("cleanup_parent_sync" in name for name in called)


def test_windows_capability_rejects_without_store_or_filesystem_delta(
    checkout, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, store = checkout
    before = (store.current_revision, tuple(workspace.rglob("*")))
    monkeypatch.setattr("platform.system", lambda: "Windows")
    with pytest.raises(CoreRunError, match="checkout_publication_unsupported"):
        probe_publication_capability(workspace / "output")
    assert (store.current_revision, tuple(workspace.rglob("*"))) == before


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS primitive")
def test_macos_real_primitive_refuses_occupied_target_and_attests_post(tmp_path: Path) -> None:
    profile = probe_publication_capability(tmp_path)
    assert profile.namespace_primitive == "renameatx_np(RENAME_EXCL)"
    assert profile.canonical_post_durability == "F_FULLFSYNC"
    with open_retained_parent(tmp_path, profile) as parent:
        parent.create_and_flush("source", b"source\n")
        parent.create_and_flush("occupied", b"occupied\n")
        with pytest.raises(CoreRunError, match="checkout_projection_conflict"):
            parent.no_clobber_rename("source", "occupied")
        assert parent.observe("occupied").sha256 == hashlib.sha256(b"occupied\n").hexdigest()


def test_publication_source_has_no_path_replace_or_automatic_unlink() -> None:
    source = Path(__file__).parents[1].joinpath(
        "src/multi_agent_brief/core_run_v2/publication.py"
    ).read_text(encoding="utf-8")
    platform_source = Path(__file__).parents[1].joinpath(
        "src/multi_agent_brief/core_run_v2/publication_platform.py"
    ).read_text(encoding="utf-8")
    assert "os.replace(" not in source + platform_source
    assert "os.rename(" not in source + platform_source
    assert "os.unlink(" not in source + platform_source
