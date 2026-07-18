"""Dormant receipt-derived working-checkout publication and recovery engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable

from multi_agent_brief.contracts.v2 import (
    CheckoutPublicationAck,
    CheckoutPublicationCleanupObservation,
    CheckoutPublicationIntent,
    CheckoutPublicationMember,
    CheckoutRevisionMember,
    PublicationIdentityV1,
)
from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.control_store.serialization import canonical_json_bytes

from .errors import CoreRunError
from .integrity import retained_member_parent, verify_protected_working_checkout
from .publication_platform import (
    CapabilityProfile,
    LeafObservation,
    RetainedParent,
    open_retained_parent,
    probe_publication_capability,
)


Hook = Callable[[str, PublicationIdentityV1, int], None]


@dataclass(frozen=True)
class PublicationResult:
    status: str
    error_code: str | None = None
    warnings: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _invoke(hook: Hook | None, name: str, identity: PublicationIdentityV1, ordinal: int) -> None:
    if hook is not None:
        hook(name, identity, ordinal)


def preflight_publication(
    workspace: Path,
    members: tuple[CheckoutPublicationMember, ...],
) -> CapabilityProfile:
    """Fail before business commit unless every changed parent proves v1 support."""

    if not members:
        raise CoreRunError("checkout_publication_journal_invalid")
    profiles: dict[str, CapabilityProfile] = {}
    for member in members:
        parent, _leaf = retained_member_parent(workspace, member.canonical_path)
        profile = probe_publication_capability(parent)
        profiles[str(parent)] = profile
    values = tuple(profiles.values())
    first = values[0]
    if any(item != first for item in values[1:]):
        raise CoreRunError("checkout_publication_unsupported")
    return first


class CheckoutPublicationEngine:
    """Publish cooperative projections without changing business authority."""

    def __init__(
        self,
        workspace: Path,
        store: SQLiteControlStore,
        *,
        hook: Hook | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.store = store
        self.hook = hook

    def publish(self, identity: PublicationIdentityV1) -> PublicationResult:
        try:
            return self._publish(identity)
        except CoreRunError as exc:
            return PublicationResult(
                "commit_outcome_unknown",
                exc.code if exc.code in {
                    "checkout_projection_conflict",
                    "checkout_projection_unreadable",
                    "checkout_publication_io_error",
                    "checkout_publication_journal_invalid",
                    "checkout_publication_unsupported",
                    "checkout_topology_invalid",
                } else "checkout_publication_io_error",
            )
        except ControlStoreError:
            return PublicationResult(
                "commit_outcome_unknown", "checkout_publication_journal_invalid"
            )
        except Exception:
            return PublicationResult(
                "commit_outcome_unknown", "checkout_publication_io_error"
            )

    recover = publish

    def _publish(self, identity: PublicationIdentityV1) -> PublicationResult:
        intent, members, acks, _observations = self.store.load_checkout_publication(identity)
        profile = self._profile_for_members(members)
        if profile.sha256 != intent.capability_profile_sha256:
            raise CoreRunError("checkout_publication_journal_invalid")
        if acks:
            if len(acks) != len(members):
                raise CoreRunError("checkout_publication_journal_invalid")
            for member in members:
                self._attest_member(member, profile)
            self._verify_full_post_checkout(intent, members, profile)
            warnings = self._cleanup_after_ack(identity, members)
            return PublicationResult("published", warnings=warnings)
        for member in members:
            self._advance_member(member, profile)
            _invoke(self.hook, "between_members", identity, member.ordinal)
        for member in members:
            self._attest_member(member, profile)
        _invoke(self.hook, "before_full_checkout_verify", identity, -1)
        self._verify_full_post_checkout(intent, members, profile)
        _invoke(self.hook, "after_full_checkout_verify", identity, -1)
        now = _now()
        ack_records = tuple(
            CheckoutPublicationAck.model_validate(
                {
                    "schema_version": CheckoutPublicationAck.schema_id,
                    "identity": identity.model_dump(mode="json"),
                    "ordinal": member.ordinal,
                    "publication_identity_sha256": intent.publication_identity_sha256,
                    "capability_profile_sha256": intent.capability_profile_sha256,
                    "post_kind": member.post_kind,
                    "post_sha256": member.post_sha256,
                    "post_size": member.post_size,
                    "verification": "post_verified_durable",
                    "cleanup_policy": "retain_residue_v1",
                    "appended_at": now,
                },
                strict=True,
            )
            for member in members
        )
        _invoke(self.hook, "before_ack", identity, -1)
        self.store.append_checkout_publication_acks(ack_records)
        _invoke(self.hook, "after_ack", identity, -1)
        warnings = self._cleanup_after_ack(identity, members)
        return PublicationResult("published", warnings=warnings)

    def _cleanup_after_ack(
        self,
        identity: PublicationIdentityV1,
        members: tuple[CheckoutPublicationMember, ...],
    ) -> tuple[str, ...]:
        try:
            return self._record_cleanup_observations(identity, members)
        except Exception:
            return ("checkout_projection_cleanup_io_warning",)

    def _profile_for_members(
        self, members: tuple[CheckoutPublicationMember, ...]
    ) -> CapabilityProfile:
        profiles: list[CapabilityProfile] = []
        seen: set[Path] = set()
        for member in members:
            parent, _leaf = retained_member_parent(
                self.workspace, member.canonical_path
            )
            if parent not in seen:
                # Recovery revalidates the same exact capability contract but
                # does not require or consume a new business transaction.
                profiles.append(probe_publication_capability(parent))
                seen.add(parent)
        if not profiles or any(item != profiles[0] for item in profiles[1:]):
            raise CoreRunError("checkout_publication_unsupported")
        return profiles[0]

    def _advance_member(
        self, member: CheckoutPublicationMember, profile: CapabilityProfile
    ) -> None:
        identity = member.identity
        parent_path, canonical_leaf = retained_member_parent(
            self.workspace, member.canonical_path
        )
        with open_retained_parent(parent_path, profile) as parent:
            canonical = parent.observe(canonical_leaf)
            temp = parent.observe(member.temporary_basename)
            claim = parent.observe(member.claim_basename)
            _invoke(self.hook, "after_observe", identity, member.ordinal)
            if self._matches(canonical, member.post_kind, member.post_sha256, member.post_size):
                return
            resume_after_claim = (
                member.pre_kind == "blob"
                and canonical.kind == "absent"
                and self._matches(
                    claim, "blob", member.pre_sha256, member.pre_size
                )
                and self._matches(
                    temp, member.post_kind, member.post_sha256, member.post_size
                )
            )
            if resume_after_claim:
                parent.sync_parent()
                if member.post_kind == "blob":
                    _invoke(self.hook, "before_publish", identity, member.ordinal)
                    parent.no_clobber_rename(
                        member.temporary_basename, canonical_leaf
                    )
                    _invoke(self.hook, "after_publish", identity, member.ordinal)
                    parent.sync_parent()
                return
            if not self._matches(canonical, member.pre_kind, member.pre_sha256, member.pre_size):
                raise CoreRunError("checkout_projection_conflict")
            if member.post_kind == "blob" and temp.kind == "absent":
                content = self._post_content(member)
                _invoke(self.hook, "after_temp_create", identity, member.ordinal)
                parent.create_and_flush(member.temporary_basename, content)
                _invoke(self.hook, "after_temp_write", identity, member.ordinal)
                _invoke(self.hook, "after_temp_flush", identity, member.ordinal)
                temp = parent.observe(member.temporary_basename)
            if member.post_kind == "blob" and not self._matches(
                temp, "blob", member.post_sha256, member.post_size
            ):
                raise CoreRunError("checkout_projection_conflict")
            if member.pre_kind == "blob":
                if claim.kind != "absent":
                    raise CoreRunError("checkout_projection_conflict")
                _invoke(self.hook, "before_claim", identity, member.ordinal)
                canonical = parent.observe(canonical_leaf)
                if not self._matches(
                    canonical,
                    member.pre_kind,
                    member.pre_sha256,
                    member.pre_size,
                ):
                    raise CoreRunError("checkout_projection_conflict")
                parent.no_clobber_rename(canonical_leaf, member.claim_basename)
                _invoke(self.hook, "after_claim", identity, member.ordinal)
                _invoke(self.hook, "before_claim_parent_sync", identity, member.ordinal)
                parent.sync_parent()
                _invoke(self.hook, "after_claim_parent_sync", identity, member.ordinal)
            if member.post_kind == "blob":
                _invoke(self.hook, "before_publish", identity, member.ordinal)
                parent.no_clobber_rename(member.temporary_basename, canonical_leaf)
                _invoke(self.hook, "after_publish", identity, member.ordinal)
                _invoke(self.hook, "before_publish_parent_sync", identity, member.ordinal)
                parent.sync_parent()
                _invoke(self.hook, "after_publish_parent_sync", identity, member.ordinal)
            else:
                parent.sync_parent()
                if parent.observe(canonical_leaf).kind != "absent":
                    raise CoreRunError("checkout_projection_conflict")

    def _post_content(self, member: CheckoutPublicationMember) -> bytes:
        snapshot = self.store.load_snapshot(member.identity.run_id)
        revision_member = next(
            (
                item for item in snapshot.checkout_revision_members
                if item.checkout_revision_id == member.identity.checkout_revision_id
                and item.canonical_path == member.canonical_path
            ),
            None,
        )
        if revision_member is None:
            raise CoreRunError("checkout_publication_journal_invalid")
        content = self.store.read_artifact_revision_bytes(
            member.identity.run_id,
            revision_member.artifact_id,
            revision_member.artifact_revision,
        )
        if (
            len(content) != member.post_size
            or hashlib.sha256(content).hexdigest() != member.post_sha256
        ):
            raise CoreRunError("checkout_publication_journal_invalid")
        return content

    def _attest_member(
        self, member: CheckoutPublicationMember, profile: CapabilityProfile
    ) -> None:
        parent_path, canonical_leaf = retained_member_parent(
            self.workspace, member.canonical_path
        )
        with open_retained_parent(parent_path, profile) as parent:
            if member.post_kind == "blob":
                _invoke(self.hook, "before_canonical_post_flush", member.identity, member.ordinal)
                parent.attest_canonical_blob(
                    canonical_leaf,
                    member.post_sha256 or "",
                    member.post_size if member.post_size is not None else -1,
                )
                _invoke(self.hook, "after_canonical_post_flush", member.identity, member.ordinal)
            else:
                parent.sync_parent()
                if parent.observe(canonical_leaf).kind != "absent":
                    raise CoreRunError("checkout_projection_conflict")

    def _verify_full_post_checkout(
        self,
        intent: CheckoutPublicationIntent,
        changed: tuple[CheckoutPublicationMember, ...],
        profile: CapabilityProfile,
    ) -> None:
        snapshot = self.store.load_snapshot(intent.identity.run_id)
        post_members = tuple(
            item for item in snapshot.checkout_revision_members
            if item.checkout_revision_id == intent.post_checkout_revision_id
        )
        verify_protected_working_checkout(
            self.workspace, post_members, changed, profile
        )

    def _record_cleanup_observations(
        self,
        identity: PublicationIdentityV1,
        members: tuple[CheckoutPublicationMember, ...],
    ) -> tuple[str, ...]:
        records: list[CheckoutPublicationCleanupObservation] = []
        warnings: list[str] = []
        now = _now()
        for member in members:
            parent_path, _leaf = retained_member_parent(
                self.workspace, member.canonical_path
            )
            with open_retained_parent(parent_path) as parent:
                for role, basename, expected_kind, expected_hash, expected_size in (
                    ("temp", member.temporary_basename, member.post_kind, member.post_sha256, member.post_size),
                    ("claim", member.claim_basename, member.pre_kind, member.pre_sha256, member.pre_size),
                ):
                    _invoke(self.hook, "before_cleanup_observation", identity, member.ordinal)
                    observation = parent.observe(basename)
                    if observation.kind == "absent":
                        _invoke(self.hook, "after_cleanup_observation", identity, member.ordinal)
                        continue
                    exact = self._matches(observation, expected_kind, expected_hash, expected_size)
                    reason = (
                        "checkout_projection_cleanup_retained"
                        if exact else "checkout_projection_cleanup_conflict"
                    )
                    warnings.append(reason)
                    payload = {
                        "identity": identity.model_dump(mode="json"),
                        "ordinal": member.ordinal,
                        "auxiliary_role": role,
                        "reason_code": reason,
                        "observed_kind": observation.kind,
                        "observed_sha256": observation.sha256,
                        "observed_size": observation.size,
                    }
                    observation_id = hashlib.sha256(
                        canonical_json_bytes(payload)
                    ).hexdigest()
                    records.append(
                        CheckoutPublicationCleanupObservation.model_validate(
                            {
                                "schema_version": CheckoutPublicationCleanupObservation.schema_id,
                                "cleanup_observation_id": observation_id,
                                "identity": identity.model_dump(mode="json"),
                                "ordinal": member.ordinal,
                                "auxiliary_role": role,
                                "reason_code": reason,
                                "expected_kind": expected_kind,
                                "expected_sha256": expected_hash,
                                "expected_size": expected_size,
                                "observed_kind": observation.kind,
                                "observed_sha256": observation.sha256,
                                "observed_size": observation.size,
                                "appended_at": now,
                            },
                            strict=True,
                        )
                    )
                    _invoke(self.hook, "after_cleanup_observation", identity, member.ordinal)
        if records:
            try:
                self.store.append_checkout_cleanup_observations(tuple(records))
            except ControlStoreError:
                warnings.append("checkout_projection_cleanup_io_warning")
        return tuple(sorted(set(warnings)))

    @staticmethod
    def _matches(
        observation: LeafObservation,
        kind: str,
        digest: str | None,
        size: int | None,
    ) -> bool:
        if kind == "absent":
            return observation.kind == "absent"
        return (
            observation.kind == "blob"
            and observation.sha256 == digest
            and observation.size == size
        )


__all__ = [
    "CheckoutPublicationEngine",
    "PublicationResult",
    "preflight_publication",
]
