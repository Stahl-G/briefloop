"""Pure immutable checkout-revision and publication-identity builders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import PurePosixPath
from typing import Iterable

from multi_agent_brief.contracts.v2 import (
    ArtifactRevision,
    CheckoutPublicationIntent,
    CheckoutPublicationMember,
    CheckoutRevisionMember,
    CheckoutRevisionRecord,
    PublicationIdentityV1,
)
from multi_agent_brief.control_store.serialization import canonical_json_bytes

from .errors import CoreRunError


CHECKOUT_MANIFEST_SCHEMA = "multi-agent-brief-checkout-revision/v1"
PUBLICATION_IDENTITY_SCHEMA = "briefloop-publication-identity/v1"
TREE_DOMAIN = b"briefloop-checkout-tree-v1\0"
IDENTITY_DOMAIN = b"briefloop-publication-identity-v1\0"


@dataclass(frozen=True)
class BuiltCheckoutRevision:
    record: CheckoutRevisionRecord
    members: tuple[CheckoutRevisionMember, ...]
    manifest_bytes: bytes


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise CoreRunError("checkout_revision_invalid")
    return value.isoformat().replace("+00:00", "Z")


def _validate_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise CoreRunError("checkout_revision_invalid")


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

    ordered = sorted(artifact_revisions, key=lambda item: item.path)
    paths: set[str] = set()
    folded: set[str] = set()
    identities: set[tuple[str, int]] = set()
    member_payloads: list[dict[str, object]] = []
    for item in ordered:
        if item.run_id != run_id or not item.frozen:
            raise CoreRunError("checkout_revision_invalid")
        _validate_path(item.path)
        identity = (item.artifact_id, item.revision)
        if (
            item.path in paths
            or item.path.casefold() in folded
            or identity in identities
        ):
            raise CoreRunError("checkout_revision_invalid")
        paths.add(item.path)
        folded.add(item.path.casefold())
        identities.add(identity)
        member_payloads.append(
            {
                "canonical_path": item.path,
                "artifact_id": item.artifact_id,
                "artifact_revision": item.revision,
                "blob_sha256": item.sha256,
                "byte_size": item.size_bytes,
            }
        )
    manifest_payload = {
        "schema_version": CHECKOUT_MANIFEST_SCHEMA,
        "workspace_id": workspace_id,
        "run_id": run_id,
        "parent_checkout_revision_id": parent_checkout_revision_id,
        "members": member_payloads,
    }
    manifest_bytes = canonical_json_bytes(manifest_payload)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    tree_sha256 = hashlib.sha256(TREE_DOMAIN + manifest_bytes).hexdigest()
    revision_id = f"crv_{tree_sha256}"
    record = CheckoutRevisionRecord.model_validate(
        {
            "schema_version": CheckoutRevisionRecord.schema_id,
            "checkout_revision_id": revision_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "parent_checkout_revision_id": parent_checkout_revision_id,
            "manifest_sha256": manifest_sha256,
            "tree_sha256": tree_sha256,
            "member_count": len(member_payloads),
            "created_at": _iso(created_at),
            "creator_transaction_id": transaction_id,
        },
        strict=True,
    )
    members = tuple(
        CheckoutRevisionMember.model_validate(
            {
                "schema_version": CheckoutRevisionMember.schema_id,
                "checkout_revision_id": revision_id,
                "ordinal": ordinal,
                "workspace_id": workspace_id,
                "run_id": run_id,
                **payload,
            },
            strict=True,
        )
        for ordinal, payload in enumerate(member_payloads)
    )
    return BuiltCheckoutRevision(record, members, manifest_bytes)


def publication_identity_sha256(identity: PublicationIdentityV1) -> str:
    payload = identity.model_dump(mode="json", exclude_unset=False)
    return hashlib.sha256(IDENTITY_DOMAIN + canonical_json_bytes(payload)).hexdigest()


def publication_sibling_basename(
    identity: PublicationIdentityV1,
    ordinal: int,
    role: str,
) -> str:
    if role not in {"tmp", "claim"} or type(ordinal) is not int or ordinal < 0:
        raise CoreRunError("checkout_publication_journal_invalid")
    return (
        f".briefloop-pub-v1-{publication_identity_sha256(identity)}-"
        f"{ordinal:08d}-{role}"
    )


def build_publication_intent(
    *,
    identity: PublicationIdentityV1,
    pre: BuiltCheckoutRevision | None,
    post: BuiltCheckoutRevision,
    capability_profile_sha256: str,
) -> tuple[CheckoutPublicationIntent, tuple[CheckoutPublicationMember, ...]]:
    """Derive the complete changed-member journal from immutable revisions."""

    if identity.checkout_revision_id != post.record.checkout_revision_id:
        raise CoreRunError("checkout_publication_journal_invalid")
    pre_by_path = {} if pre is None else {m.canonical_path: m for m in pre.members}
    post_by_path = {m.canonical_path: m for m in post.members}
    def projection_value(
        member: CheckoutRevisionMember | None,
    ) -> tuple[str, int] | None:
        if member is None:
            return None
        return member.blob_sha256, member.byte_size

    changed_paths = sorted(
        path
        for path in set(pre_by_path) | set(post_by_path)
        if projection_value(pre_by_path.get(path))
        != projection_value(post_by_path.get(path))
    )
    if not changed_paths:
        raise CoreRunError("checkout_publication_journal_invalid")
    members: list[CheckoutPublicationMember] = []
    for ordinal, path in enumerate(changed_paths):
        before = pre_by_path.get(path)
        after = post_by_path.get(path)
        members.append(
            CheckoutPublicationMember.model_validate(
                {
                    "schema_version": CheckoutPublicationMember.schema_id,
                    "identity": identity.model_dump(mode="json"),
                    "ordinal": ordinal,
                    "canonical_path": path,
                    "temporary_basename": publication_sibling_basename(
                        identity, ordinal, "tmp"
                    ),
                    "claim_basename": publication_sibling_basename(
                        identity, ordinal, "claim"
                    ),
                    "pre_kind": "absent" if before is None else "blob",
                    "pre_sha256": None if before is None else before.blob_sha256,
                    "pre_size": None if before is None else before.byte_size,
                    "post_kind": "absent" if after is None else "blob",
                    "post_sha256": None if after is None else after.blob_sha256,
                    "post_size": None if after is None else after.byte_size,
                },
                strict=True,
            )
        )
    digest = publication_identity_sha256(identity)
    intent = CheckoutPublicationIntent.model_validate(
        {
            "schema_version": CheckoutPublicationIntent.schema_id,
            "identity": identity.model_dump(mode="json"),
            "publication_identity_sha256": digest,
            "pre_checkout_revision_id": (
                None if pre is None else pre.record.checkout_revision_id
            ),
            "post_checkout_revision_id": post.record.checkout_revision_id,
            "post_manifest_sha256": post.record.manifest_sha256,
            "post_tree_sha256": post.record.tree_sha256,
            "changed_member_count": len(members),
            "capability_profile_sha256": capability_profile_sha256,
        },
        strict=True,
    )
    return intent, tuple(members)


__all__ = [
    "BuiltCheckoutRevision",
    "build_checkout_revision",
    "build_publication_intent",
    "publication_identity_sha256",
    "publication_sibling_basename",
]
