"""Pure immutable checkout-revision and publication-identity builders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import Iterable

from multi_agent_brief.contracts.v2 import (
    ArtifactRevision,
    CheckoutPublicationIntent,
    CheckoutPublicationMember,
    CheckoutRevisionMember,
    CheckoutRevisionRecord,
    PublicationIdentityV1,
    ReceiptCheckoutBinding,
    _CheckoutStructureError,
    _build_checkout_revision_structure,
    _derive_publication_structure,
    _publication_identity_digest,
    _publication_sibling_name,
)
from multi_agent_brief.control_store.sqlite_store import (
    ControlStoreSnapshot,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.uow import ControlUnitOfWork

from .errors import CoreRunError


CHECKOUT_MANIFEST_SCHEMA = "multi-agent-brief-checkout-revision/v1"
PUBLICATION_IDENTITY_SCHEMA = "briefloop-publication-identity/v1"


@dataclass(frozen=True)
class BuiltCheckoutRevision:
    record: CheckoutRevisionRecord
    members: tuple[CheckoutRevisionMember, ...]
    manifest_bytes: bytes


@dataclass(frozen=True)
class PreparedCheckoutEffect:
    pre: BuiltCheckoutRevision | None
    post: BuiltCheckoutRevision
    binding: ReceiptCheckoutBinding
    identity: PublicationIdentityV1 | None
    intent: CheckoutPublicationIntent | None
    publication_members: tuple[CheckoutPublicationMember, ...]


def build_checkout_revision(
    *,
    workspace_id: str,
    run_id: str,
    transaction_id: str,
    created_at: datetime,
    artifact_revisions: Iterable[ArtifactRevision],
    parent_checkout_revision_id: str | None,
) -> BuiltCheckoutRevision:
    """Build one deterministic revision from exact immutable artifact rows."""

    try:
        record, members, manifest_bytes = _build_checkout_revision_structure(
            workspace_id=workspace_id,
            run_id=run_id,
            transaction_id=transaction_id,
            created_at=created_at,
            artifact_revisions=tuple(artifact_revisions),
            parent_checkout_revision_id=parent_checkout_revision_id,
        )
    except _CheckoutStructureError as exc:
        raise CoreRunError("checkout_revision_invalid") from exc
    return BuiltCheckoutRevision(record, members, manifest_bytes)


def publication_identity_sha256(identity: PublicationIdentityV1) -> str:
    try:
        return _publication_identity_digest(identity)
    except _CheckoutStructureError as exc:
        raise CoreRunError("checkout_publication_journal_invalid") from exc


def publication_sibling_basename(
    identity: PublicationIdentityV1,
    ordinal: int,
    role: str,
) -> str:
    try:
        return _publication_sibling_name(identity, ordinal, role)
    except _CheckoutStructureError as exc:
        raise CoreRunError("checkout_publication_journal_invalid") from exc


def build_publication_intent(
    *,
    identity: PublicationIdentityV1,
    pre: BuiltCheckoutRevision | None,
    post: BuiltCheckoutRevision,
    capability_profile_sha256: str,
) -> tuple[CheckoutPublicationIntent, tuple[CheckoutPublicationMember, ...]]:
    """Derive the complete changed-member journal from immutable revisions."""

    try:
        return _derive_publication_structure(
            identity=identity,
            pre_record=None if pre is None else pre.record,
            pre_members=() if pre is None else pre.members,
            post_record=post.record,
            post_members=post.members,
            capability_profile_sha256=capability_profile_sha256,
        )
    except _CheckoutStructureError as exc:
        raise CoreRunError("checkout_publication_journal_invalid") from exc


def _current_checkout(
    snapshot: ControlStoreSnapshot,
) -> BuiltCheckoutRevision | None:
    if not snapshot.receipt_checkout_bindings:
        return None
    committed = {
        item.transaction_id: item.committed_revision for item in snapshot.transactions
    }
    candidates = [
        item
        for item in snapshot.receipt_checkout_bindings
        if item.post_run_id == snapshot.run.run_id
    ]
    if not candidates:
        return None
    binding = max(
        candidates,
        key=lambda item: committed.get(item.transaction_id, -1),
    )
    records = [
        item
        for item in snapshot.checkout_revisions
        if item.checkout_revision_id == binding.post_checkout_revision_id
    ]
    if len(records) != 1:
        raise CoreRunError("checkout_revision_invalid")
    members = tuple(
        sorted(
            (
                item
                for item in snapshot.checkout_revision_members
                if item.checkout_revision_id == binding.post_checkout_revision_id
            ),
            key=lambda item: item.ordinal,
        )
    )
    return BuiltCheckoutRevision(records[0], members, b"")


def prepare_checkout_effect(
    *,
    workspace: "Path",
    snapshot: ControlStoreSnapshot,
    transaction_id: str,
    created_at: datetime,
    additional_revisions: Iterable[ArtifactRevision] = (),
) -> PreparedCheckoutEffect:
    """Build one child CheckoutRevision and optional preflighted intent."""

    from .publication import preflight_publication

    pre = _current_checkout(snapshot)
    current = {
        (item.artifact_id, item.revision): item
        for item in snapshot.artifact_revisions
    }
    selected: dict[str, ArtifactRevision] = {}
    for artifact in snapshot.artifacts:
        if artifact.current_revision <= 0:
            continue
        revision = current.get((artifact.artifact_id, artifact.current_revision))
        if revision is None:
            raise CoreRunError("checkout_revision_invalid")
        if not revision.path.startswith("briefloop.db.blobs/"):
            selected[artifact.artifact_id] = revision
    for revision in additional_revisions:
        if not revision.path.startswith("briefloop.db.blobs/"):
            selected[revision.artifact_id] = revision
    post = build_checkout_revision(
        workspace_id=snapshot.workspace_id,
        run_id=snapshot.run.run_id,
        transaction_id=transaction_id,
        created_at=created_at,
        artifact_revisions=selected.values(),
        parent_checkout_revision_id=(
            None if pre is None else pre.record.checkout_revision_id
        ),
    )
    binding = ReceiptCheckoutBinding.model_validate(
        {
            "schema_version": ReceiptCheckoutBinding.schema_id,
            "workspace_id": snapshot.workspace_id,
            "run_id": snapshot.run.run_id,
            "transaction_id": transaction_id,
            "pre_run_id": snapshot.run.run_id,
            "pre_checkout_revision_id": (
                None if pre is None else pre.record.checkout_revision_id
            ),
            "post_run_id": snapshot.run.run_id,
            "post_checkout_revision_id": post.record.checkout_revision_id,
        },
        strict=True,
    )
    identity = PublicationIdentityV1.model_validate(
        {
            "schema_version": PUBLICATION_IDENTITY_SCHEMA,
            "workspace_id": snapshot.workspace_id,
            "run_id": snapshot.run.run_id,
            "transaction_id": transaction_id,
            "checkout_revision_id": post.record.checkout_revision_id,
        },
        strict=True,
    )
    pre_projection = {
        item.canonical_path: (item.blob_sha256, item.byte_size)
        for item in (() if pre is None else pre.members)
    }
    post_projection = {
        item.canonical_path: (item.blob_sha256, item.byte_size)
        for item in post.members
    }
    if pre_projection == post_projection:
        return PreparedCheckoutEffect(pre, post, binding, None, None, ())
    if sys.platform == "win32":
        raise CoreRunError("checkout_publication_unsupported")
    for revision in additional_revisions:
        if not revision.path.startswith("briefloop.db.blobs/"):
            _ensure_projection_parent(Path(workspace), revision.path)
    provisional_intent, provisional_members = build_publication_intent(
        identity=identity,
        pre=pre,
        post=post,
        capability_profile_sha256="0" * 64,
    )
    if not provisional_members:
        return PreparedCheckoutEffect(pre, post, binding, None, None, ())
    profile = preflight_publication(Path(workspace), provisional_members)
    intent, members = build_publication_intent(
        identity=identity,
        pre=pre,
        post=post,
        capability_profile_sha256=profile.sha256,
    )
    return PreparedCheckoutEffect(pre, post, binding, identity, intent, members)


def prepare_cross_run_checkout_effect(
    *,
    workspace: "Path",
    snapshot: ControlStoreSnapshot,
    successor_run_id: str,
    transaction_id: str,
    created_at: datetime,
) -> PreparedCheckoutEffect:
    """Build the reset successor child from the exact predecessor checkout."""

    from .publication import preflight_publication

    pre = _current_checkout(snapshot)
    if pre is None:
        raise CoreRunError("checkout_revision_invalid")
    post = build_checkout_revision(
        workspace_id=snapshot.workspace_id,
        run_id=successor_run_id,
        transaction_id=transaction_id,
        created_at=created_at,
        artifact_revisions=(),
        parent_checkout_revision_id=pre.record.checkout_revision_id,
    )
    binding = ReceiptCheckoutBinding.model_validate(
        {
            "schema_version": ReceiptCheckoutBinding.schema_id,
            "workspace_id": snapshot.workspace_id,
            "run_id": successor_run_id,
            "transaction_id": transaction_id,
            "pre_run_id": snapshot.run.run_id,
            "pre_checkout_revision_id": pre.record.checkout_revision_id,
            "post_run_id": successor_run_id,
            "post_checkout_revision_id": post.record.checkout_revision_id,
        },
        strict=True,
    )
    if not pre.members:
        return PreparedCheckoutEffect(pre, post, binding, None, None, ())
    if sys.platform == "win32":
        raise CoreRunError("checkout_publication_unsupported")
    identity = PublicationIdentityV1.model_validate(
        {
            "schema_version": PUBLICATION_IDENTITY_SCHEMA,
            "workspace_id": snapshot.workspace_id,
            "run_id": successor_run_id,
            "transaction_id": transaction_id,
            "checkout_revision_id": post.record.checkout_revision_id,
        },
        strict=True,
    )
    provisional_intent, provisional_members = build_publication_intent(
        identity=identity,
        pre=pre,
        post=post,
        capability_profile_sha256="0" * 64,
    )
    if not provisional_members:
        return PreparedCheckoutEffect(pre, post, binding, None, None, ())
    profile = preflight_publication(Path(workspace), provisional_members)
    intent, members = build_publication_intent(
        identity=identity,
        pre=pre,
        post=post,
        capability_profile_sha256=profile.sha256,
    )
    return PreparedCheckoutEffect(pre, post, binding, identity, intent, members)


def _ensure_projection_parent(workspace: Path, canonical_path: str) -> None:
    """Create only missing canonical parent directories under a verified root."""

    root = workspace.resolve(strict=True)
    cursor = root
    for part in PurePosixPath(canonical_path).parts[:-1]:
        candidate = cursor / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            try:
                candidate.mkdir(mode=0o700)
                info = candidate.lstat()
            except OSError as exc:
                raise CoreRunError("checkout_topology_invalid") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise CoreRunError("checkout_topology_invalid")
        cursor = candidate
    try:
        cursor.relative_to(root)
    except ValueError as exc:
        raise CoreRunError("checkout_topology_invalid") from exc


def stage_checkout_effect(
    unit: ControlUnitOfWork,
    prepared: PreparedCheckoutEffect,
) -> None:
    unit.put_checkout_revision(prepared.post.record)
    for member in prepared.post.members:
        unit.put_checkout_revision_member(member)
    unit.put_receipt_checkout_binding(prepared.binding)
    if prepared.intent is not None:
        unit.put_checkout_publication_intent(prepared.intent)
        for member in prepared.publication_members:
            unit.put_checkout_publication_member(member)


def publish_checkout_effect(
    *,
    workspace: "Path",
    store: SQLiteControlStore,
    prepared: PreparedCheckoutEffect,
) -> tuple[bool, tuple[str, ...]]:
    if prepared.identity is None:
        return True, ()
    from .publication import CheckoutPublicationEngine

    # The business UoW's connection has just performed the post-commit domain
    # verification. Publication acknowledgements are recovery metadata and use
    # a fresh connection so their journal transaction cannot inherit reader
    # cursor state from that authoritative commit.
    with SQLiteControlStore.open(store.path) as publication_store:
        result = CheckoutPublicationEngine(workspace, publication_store).publish(
            prepared.identity
        )
    return result.status == "published", result.warnings


def recover_checkout_replay(
    *,
    store: SQLiteControlStore,
    replay,
):
    """Recover only an unacknowledged publication owned by an exact replay."""

    from .errors import CoreRunResult
    from .publication import CheckoutPublicationEngine

    receipt = replay.receipt
    if receipt is None or not receipt.checkout_publication_intents:
        return replay
    if len(receipt.checkout_publication_intents) != 1:
        raise CoreRunError("checkout_publication_journal_invalid")
    reference = receipt.checkout_publication_intents[0]
    identity = PublicationIdentityV1.model_validate(
        {
            "schema_version": PUBLICATION_IDENTITY_SCHEMA,
            "workspace_id": store.workspace_id,
            "run_id": receipt.run_id,
            "transaction_id": receipt.transaction_id,
            "checkout_revision_id": reference.checkout_revision_id,
        },
        strict=True,
    )
    with SQLiteControlStore.open(store.path) as publication_store:
        _intent, members, acks, _observations = (
            publication_store.load_checkout_publication(identity)
        )
        recovered = (
            None
            if len(acks) == len(members)
            else CheckoutPublicationEngine(
                store.path.parent, publication_store
            ).recover(identity)
        )
    if recovered is not None and recovered.status != "published":
        return CoreRunResult(
            status="commit_outcome_unknown",
            error_code="commit_outcome_unknown",
        )
    return replay


__all__ = [
    "BuiltCheckoutRevision",
    "PreparedCheckoutEffect",
    "build_checkout_revision",
    "build_publication_intent",
    "prepare_checkout_effect",
    "prepare_cross_run_checkout_effect",
    "publish_checkout_effect",
    "recover_checkout_replay",
    "publication_identity_sha256",
    "publication_sibling_basename",
    "stage_checkout_effect",
]
