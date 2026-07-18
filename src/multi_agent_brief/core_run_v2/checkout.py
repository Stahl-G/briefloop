"""Pure immutable checkout-revision and publication-identity builders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from multi_agent_brief.contracts.v2 import (
    ArtifactRevision,
    CheckoutPublicationIntent,
    CheckoutPublicationMember,
    CheckoutRevisionMember,
    CheckoutRevisionRecord,
    PublicationIdentityV1,
    _CheckoutStructureError,
    _build_checkout_revision_structure,
    _derive_publication_structure,
    _publication_identity_digest,
    _publication_sibling_name,
)

from .errors import CoreRunError


CHECKOUT_MANIFEST_SCHEMA = "multi-agent-brief-checkout-revision/v1"
PUBLICATION_IDENTITY_SCHEMA = "briefloop-publication-identity/v1"


@dataclass(frozen=True)
class BuiltCheckoutRevision:
    record: CheckoutRevisionRecord
    members: tuple[CheckoutRevisionMember, ...]
    manifest_bytes: bytes


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


__all__ = [
    "BuiltCheckoutRevision",
    "build_checkout_revision",
    "build_publication_intent",
    "publication_identity_sha256",
    "publication_sibling_basename",
]
