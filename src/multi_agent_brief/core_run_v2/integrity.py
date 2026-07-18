"""Logical-checkout integrity and durable contamination for fresh-v2 runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable, Iterable

from multi_agent_brief.contracts.v2 import (
    ArtifactRevision,
    CheckoutPublicationMember,
    CheckoutRevisionMember,
    CoreRunEventBinding,
    EventEnvelope,
    IntegrityCheckRequest,
    RunIntegrityRecord,
)
from multi_agent_brief.control_store import (
    ControlStoreCommitOutcomeUnknown,
    ControlStoreError,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    sha256_hex,
)

from .errors import CoreRunError, CoreRunResult, core_run_error_code
from .checkout import prepare_checkout_effect, stage_checkout_effect
from .publication_platform import CapabilityProfile, open_retained_parent
from .policy import derived_id, transaction_type_for
from .verifier import (
    CoreRunDomainVerifier,
    VerifiedCoreRun,
    _integrity_contamination_binding_fingerprint,
    resolve_core_replay,
)


_Clock = Callable[[], datetime]


def retained_member_parent(
    workspace: Path,
    canonical_path: str,
) -> tuple[Path, str]:
    """Return one lexical parent only when every component is a real directory."""

    path = PurePosixPath(canonical_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CoreRunError("checkout_topology_invalid")
    try:
        root = workspace.resolve(strict=True)
        cursor = root
        for part in path.parts[:-1]:
            candidate = cursor / part
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise CoreRunError("checkout_topology_invalid")
            cursor = candidate
        cursor.relative_to(root)
    except CoreRunError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise CoreRunError("checkout_topology_invalid") from exc
    return cursor, path.name


def verify_protected_working_checkout(
    workspace: Path,
    revision_members: tuple[CheckoutRevisionMember, ...],
    changed_members: tuple[CheckoutPublicationMember, ...],
    profile: CapabilityProfile,
) -> None:
    """Compare cooperative files with immutable revision truth, never vice versa."""

    expected_paths = {item.canonical_path for item in revision_members}
    for item in revision_members:
        parent, leaf = retained_member_parent(workspace, item.canonical_path)
        with open_retained_parent(parent, profile) as retained:
            observed = retained.observe(leaf)
            if (
                observed.kind != "blob"
                or observed.sha256 != item.blob_sha256
                or observed.size != item.byte_size
            ):
                raise CoreRunError("checkout_projection_conflict")
    for changed in changed_members:
        if changed.post_kind != "absent":
            continue
        if changed.canonical_path in expected_paths:
            raise CoreRunError("checkout_publication_journal_invalid")
        parent, leaf = retained_member_parent(
            workspace, changed.canonical_path
        )
        with open_retained_parent(parent, profile) as retained:
            if retained.observe(leaf).kind != "absent":
                raise CoreRunError("checkout_projection_conflict")


@dataclass(frozen=True)
class CheckoutObservation:
    entry_kind: str
    sha256: str | None = None
    content: bytes | None = None


class RunIntegrityService:
    """Inspect the exact protected revision union through one read boundary."""

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        clock: _Clock | None = None,
    ) -> None:
        self.workspace = _workspace_root(workspace)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._verifier = CoreRunDomainVerifier()

    def inspect(self, request: IntegrityCheckRequest) -> dict[str, object]:
        try:
            return self._inspect(request)
        except CoreRunError:
            raise
        except ControlStoreCommitOutcomeUnknown:
            return {
                "status": "commit_outcome_unknown",
                "error_code": "commit_outcome_unknown",
            }
        except ControlStoreError as exc:
            raise CoreRunError(core_run_error_code(exc)) from exc

    def _inspect(self, request: IntegrityCheckRequest) -> dict[str, object]:
        request_fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        with self._open_store() as store:
            replay = resolve_core_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay.to_dict()
            verified = self._verifier.verify(store, request.run_id)
            if verified.snapshot.store_revision != request.expected_store_revision:
                raise CoreRunError("store_revision_conflict")
            current = verified.snapshot.run_integrity_records[-1]
            if current.status == "contaminated":
                return {
                    "status": "contaminated",
                    "integrity_revision": current.integrity_revision,
                    "reason_code": current.reason_code,
                }
            mismatch = self.first_mismatch(verified)
            if mismatch is None:
                return {
                    "status": "clean",
                    "integrity_revision": current.integrity_revision,
                }
            revision, observation = mismatch
            result = self.record_contamination(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=request_fingerprint,
                expected_store_revision=request.expected_store_revision,
                revision=revision,
                observation=observation,
            )
            return result.to_dict()

    def require_clean(
        self,
        store: SQLiteControlStore,
        verified: VerifiedCoreRun,
        *,
        request_id: str,
        request_fingerprint: str,
        expected_store_revision: int,
        additional_revisions: Iterable[ArtifactRevision] = (),
    ) -> CoreRunResult | None:
        current = verified.snapshot.run_integrity_records[-1]
        if current.status == "contaminated":
            raise CoreRunError("core_run_integrity_blocked")
        mismatch = self.first_mismatch(
            verified,
            additional_revisions=additional_revisions,
        )
        if mismatch is None:
            return None
        revision, observation = mismatch
        return self.record_contamination(
            store,
            verified,
            request_id=request_id,
            request_fingerprint=request_fingerprint,
            expected_store_revision=expected_store_revision,
            revision=revision,
            observation=observation,
        )

    def first_mismatch(
        self,
        verified: VerifiedCoreRun,
        *,
        additional_revisions: Iterable[ArtifactRevision] = (),
    ) -> tuple[ArtifactRevision, CheckoutObservation] | None:
        protected_keys = protected_revision_keys(verified)
        revisions = {
            (item.artifact_id, item.revision): item
            for item in verified.snapshot.artifact_revisions
        }
        for item in additional_revisions:
            revisions[(item.artifact_id, item.revision)] = item
            protected_keys.add((item.artifact_id, item.revision))
        for key in sorted(protected_keys):
            revision = revisions.get(key)
            if revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            if revision.path.startswith("briefloop.db.blobs/"):
                # Store-managed blobs are verified by ControlStore itself. Only
                # logical workspace checkouts participate in contamination.
                continue
            observation = read_workspace_file(self.workspace, revision.path)
            if (
                observation.entry_kind != "regular_file"
                or observation.sha256 != revision.sha256
            ):
                return revision, observation
        return None

    @staticmethod
    def revision_is_protected(
        verified: VerifiedCoreRun,
        artifact_id: str,
        revision: int,
    ) -> bool:
        return (artifact_id, revision) in protected_revision_keys(verified)

    def record_contamination(
        self,
        store: SQLiteControlStore,
        verified: VerifiedCoreRun,
        *,
        request_id: str,
        request_fingerprint: str,
        expected_store_revision: int,
        revision: ArtifactRevision,
        observation: CheckoutObservation,
    ) -> CoreRunResult:
        current = verified.snapshot.run_integrity_records[-1]
        observation_fingerprint = canonical_fingerprint(
            {
                "run_id": verified.snapshot.run.run_id,
                "artifact_id": revision.artifact_id,
                "artifact_revision": revision.revision,
                "expected_workspace_path": revision.path,
                "expected_sha256": revision.sha256,
                "observed_entry_kind": observation.entry_kind,
                "observed_sha256": observation.sha256,
            }
        )
        binding_fingerprint = _integrity_contamination_binding_fingerprint(
            request_fingerprint,
            observation_fingerprint,
        )
        now = _now(self._clock)
        event_id = derived_id(
            "EVT-INTEGRITY",
            request_id,
            observation_fingerprint,
        )
        integrity_revision = current.integrity_revision + 1
        record = RunIntegrityRecord.model_validate(
            {
                "schema_version": RunIntegrityRecord.schema_id,
                "run_id": verified.snapshot.run.run_id,
                "integrity_revision": integrity_revision,
                "status": "contaminated",
                "prior_integrity_revision": current.integrity_revision,
                "affected_artifact_id": revision.artifact_id,
                "affected_artifact_revision": revision.revision,
                "expected_workspace_path": revision.path,
                "expected_sha256": revision.sha256,
                "observed_entry_kind": observation.entry_kind,
                "observed_sha256": observation.sha256,
                "reason_code": "frozen_artifact_contaminated",
                "first_detected_at": now,
                "first_detected_event_id": event_id,
                "accepted_transaction_id": request_id,
                "request_fingerprint": request_fingerprint,
            },
            strict=True,
        )
        event = EventEnvelope.model_validate(
            {
                "schema_version": EventEnvelope.schema_id,
                "event_id": event_id,
                "run_id": verified.snapshot.run.run_id,
                "event_type": "run_integrity_contaminated",
                "created_at": now,
                "actor": "system",
                "transaction_id": request_id,
                "artifact_id": revision.artifact_id,
                "decision": "block",
                "reason": "frozen_artifact_contaminated",
                "metadata": {},
                "core_run_binding": CoreRunEventBinding(
                    request_id=request_id,
                    request_fingerprint=binding_fingerprint,
                    effect_kind="integrity_contamination",
                    primary_record_id=str(integrity_revision),
                    outcome="blocked",
                ),
            },
            strict=True,
        )
        block_event = EventEnvelope.model_validate(
            {
                "schema_version": EventEnvelope.schema_id,
                "event_id": derived_id(
                    "EVT-BLOCK",
                    request_id,
                    observation_fingerprint,
                ),
                "run_id": verified.snapshot.run.run_id,
                "event_type": "run_blocked",
                "created_at": now,
                "actor": "system",
                "transaction_id": request_id,
                "artifact_id": revision.artifact_id,
                "decision": "block",
                "reason": "frozen_artifact_contaminated",
                "metadata": {},
            },
            strict=True,
        )
        unit = store.begin(
            verified.snapshot.run.run_id,
            request_id,
            transaction_type_for("integrity_contamination"),
            expected_store_revision,
        )
        unit.append_event(event)
        unit.append_event(block_event)
        unit.append_run_integrity_record(record)
        checkout = prepare_checkout_effect(
            workspace=self.workspace,
            snapshot=verified.snapshot,
            transaction_id=request_id,
            created_at=self._clock(),
        )
        stage_checkout_effect(unit, checkout)
        receipt = unit.commit(
            _postcommit_observer=lambda _receipt: self._verifier.verify(
                store,
                verified.snapshot.run.run_id,
            )
        )
        return CoreRunResult(
            status="blocked",
            receipt=receipt,
            error_code="frozen_artifact_contaminated",
            primary_record_id=str(integrity_revision),
        )

    def _open_store(self) -> SQLiteControlStore:
        try:
            return SQLiteControlStore.open(self.workspace / "briefloop.db", clock=self._clock)
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc


def read_workspace_file(workspace: Path, relative_path: str) -> CheckoutObservation:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return CheckoutObservation("unsafe")
    current = workspace
    try:
        for part in path.parts[:-1]:
            current = current / part
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                return CheckoutObservation("unsafe")
        leaf = current / path.name
        before = leaf.lstat()
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            return CheckoutObservation("non_regular")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
        )
        descriptor = os.open(leaf, flags)
        try:
            opened = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_mode) != (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
            ):
                return CheckoutObservation("unsafe")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        return CheckoutObservation("absent")
    except (OSError, RuntimeError, ValueError):
        return CheckoutObservation("unsafe")
    content = b"".join(chunks)
    return CheckoutObservation(
        "regular_file",
        sha256=sha256_hex(content),
        content=content,
    )


def materialize_checkout(workspace: Path, relative_path: str, content: bytes) -> None:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CoreRunError("artifact_input_unsafe")
    parent = workspace
    try:
        for part in path.parts[:-1]:
            parent = parent / part
            if parent.exists() or parent.is_symlink():
                info = parent.lstat()
                if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    raise CoreRunError("artifact_input_unsafe")
            else:
                parent.mkdir()
        target = parent / path.name
        if target.is_symlink():
            raise CoreRunError("artifact_input_unsafe")
        temporary = parent / f".{path.name}.core-v2.tmp"
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
    except CoreRunError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise CoreRunError("artifact_input_unsafe") from exc


def protected_revision_keys(
    verified: VerifiedCoreRun,
) -> set[tuple[str, int]]:
    keys = {
        (item.artifact_id, item.artifact_revision)
        for item in verified.snapshot.stage_artifact_bindings
    }
    for freeze in verified.snapshot.claim_freezes:
        keys.add(
            (
                freeze.claim_drafts_artifact.artifact_id,
                freeze.claim_drafts_artifact.revision,
            )
        )
        keys.add(
            (freeze.ledger_artifact.artifact_id, freeze.ledger_artifact.revision)
        )
    return keys


def _workspace_root(workspace: str | os.PathLike[str]) -> Path:
    try:
        root = Path(workspace).expanduser().resolve(strict=True)
        if not stat.S_ISDIR(root.stat().st_mode):
            raise ValueError
        return root
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise CoreRunError("core_run_request_invalid") from exc


def _now(clock: _Clock) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CoreRunError("core_run_request_invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CheckoutObservation",
    "RunIntegrityService",
    "materialize_checkout",
    "protected_revision_keys",
    "read_workspace_file",
]
