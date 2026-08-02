"""Store-native Human review of one qualified post-final LAJ result.

This is the sole coordinator for finding dispositions and Human-authored
guidance.  It consumes only the verified assessment projection, writes through
the existing ControlStore/UoW, and never invokes a provider or changes Core
truth.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, ValidationError

from multi_agent_brief.contracts.v2 import (
    PostFinalFindingDispositionRecord,
    PostFinalGuidanceDraftRevision,
    PostFinalGuidanceStatusRevision,
    StrictModel,
    post_final_guidance_legal_actions,
    post_final_guidance_status_transition_allowed,
)
from multi_agent_brief.control_store.errors import ControlStoreError
from multi_agent_brief.control_store.serialization import canonical_json_bytes
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.product.post_final_assessment import (
    PostFinalAssessmentError,
    _event,
    _id,
    _record_fingerprint,
    _require_current_finalized_action,
    _utc_now,
    resolve_post_final_assessment_series,
)
from multi_agent_brief.product.post_final_assessment_projection import (
    build_post_final_assessment_projection,
)
from multi_agent_brief.runtime_host_v2.projections import (
    build_finalized_local_review_projection_from_history,
)
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError

POST_FINAL_DISPOSITION_INPUT_SCHEMA = (
    "briefloop.post_final_finding_disposition_input.v1"
)
POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA = "briefloop.post_final_guidance_draft_input.v1"
POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA = (
    "briefloop.post_final_guidance_status_input.v1"
)
NEXT_RUN_CONSUMPTION_STATUS = "explicit_opt_in_successor_only"


class PostFinalReviewError(RuntimeError):
    """Stable, value-free review failure."""


class FindingDispositionInput(StrictModel):
    schema_version: Literal["briefloop.post_final_finding_disposition_input.v1"]
    human_actor_id: str
    human_request_id: str
    assessment_result_id: str
    reader_view_sha256: str
    finding_id: str
    finding_fingerprint: str
    decision: Literal["accept", "reject", "defer"]
    human_note: str | None = Field(default=None, max_length=4000)


class GuidanceDraftInput(StrictModel):
    schema_version: Literal["briefloop.post_final_guidance_draft_input.v1"]
    human_actor_id: str
    human_request_id: str
    assessment_result_id: str
    finding_id: str
    disposition_id: str
    guidance_text: str = Field(min_length=1, max_length=12000)


class GuidanceStatusInput(StrictModel):
    schema_version: Literal["briefloop.post_final_guidance_status_input.v1"]
    human_actor_id: str
    human_request_id: str
    guidance_id: str
    draft_revision: int = Field(ge=1)


def _finding_fingerprint(
    *,
    result_id: str,
    result_fingerprint: str,
    view_sha256: str,
    finding: Any,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": "briefloop.post_final_finding_binding.v1",
                "assessment_result_id": result_id,
                "assessment_result_fingerprint": result_fingerprint,
                "reader_view_sha256": view_sha256,
                "finding": finding.model_dump(mode="json", exclude_unset=False),
            }
        )
    ).hexdigest()


class PostFinalReviewService:
    """The only Store writer for PF-LAJ Human dispositions and guidance."""

    def __init__(
        self,
        workspace: str | Path,
        assessment_result_id: str,
        assessment_result_fingerprint: str,
        *,
        allow_historical: bool = False,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.assessment_result_id = assessment_result_id
        self.assessment_result_fingerprint = assessment_result_fingerprint
        self.allow_historical = allow_historical

    @property
    def _database_path(self) -> Path:
        return self.workspace / "briefloop.db"

    def _load(self) -> dict[str, Any]:
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                history = store.load_history()
            selected = [
                item
                for run_snapshot in history.snapshots
                for item in run_snapshot.post_final_assessment_results
                if item.assessment_result_id == self.assessment_result_id
            ]
            if len(selected) != 1:
                raise PostFinalReviewError("post_final_review_selection_invalid")
            selected_result = selected[0]
            if selected_result.result_fingerprint != self.assessment_result_fingerprint:
                raise PostFinalReviewError("post_final_review_binding_invalid")
            if not self.allow_historical:
                current_heads = {
                    item.workspace_run_head.current_run_id
                    for item in history.snapshots
                    if item.workspace_run_head is not None
                }
                if current_heads != {selected_result.run_id}:
                    raise PostFinalReviewError(
                        "post_final_review_current_head_required"
                    )
            facts = build_finalized_local_review_projection_from_history(
                self.workspace,
                history,
                run_id=selected_result.run_id,
                require_current_head=False,
            ).facts
            verified = CoreRunDomainVerifier().verify_loaded_history(
                history,
                facts.run_id,
                require_current_head=False,
            )
            action = _require_current_finalized_action(
                facts, classify_core_run_next_action(verified)
            )
            snapshot = history.snapshot_at_revision(
                facts.run_id, history.store_revision
            )
            series = resolve_post_final_assessment_series(
                history, snapshot, facts, action
            )
            projection = build_post_final_assessment_projection(
                self.workspace,
                assessment_result_id=self.assessment_result_id,
                assessment_result_fingerprint=self.assessment_result_fingerprint,
                loaded_history=history,
            )
        except (
            ControlStoreError,
            CoreRunError,
            PostFinalAssessmentError,
            PostFinalReviewError,
            RuntimeHostError,
            OSError,
            ValueError,
        ) as exc:
            raise PostFinalReviewError("post_final_review_unavailable") from exc
        if projection.status != "available" or projection.view.binding is None:
            raise PostFinalReviewError("post_final_review_unavailable")
        results = [
            item
            for item in snapshot.post_final_assessment_results
            if item.assessment_result_id == self.assessment_result_id
        ]
        if len(results) != 1:
            raise PostFinalReviewError("control_store_integrity_invalid")
        result = results[0]
        if result.result_fingerprint != self.assessment_result_fingerprint:
            raise PostFinalReviewError("post_final_review_binding_invalid")
        requests = [
            item
            for item in series
            if item.assessment_request_id == result.assessment_request_id
        ]
        if len(requests) != 1:
            raise PostFinalReviewError("control_store_integrity_invalid")
        request = requests[0]
        if (
            result.finalized_lineage_fingerprint
            != request.finalized_lineage_fingerprint
            or result.reader_view_sha256 != projection.view.view_sha256
        ):
            raise PostFinalReviewError("post_final_review_binding_invalid")
        finding_map = {item.finding_id: item for item in projection.view.findings}
        if len(finding_map) != len(projection.view.findings):
            raise PostFinalReviewError("post_final_review_binding_invalid")
        return {
            "facts": facts,
            "history": history,
            "snapshot": snapshot,
            "request": request,
            "result": result,
            "view": projection.view,
            "findings": finding_map,
        }

    @staticmethod
    def _receipt(snapshot: Any, transaction_id: str) -> Any:
        matches = [
            item
            for item in snapshot.transactions
            if item.transaction_id == transaction_id
        ]
        if len(matches) != 1:
            raise PostFinalReviewError("control_store_integrity_invalid")
        return matches[0]

    @staticmethod
    def _current_disposition(snapshot: Any, result_id: str, finding_id: str) -> Any:
        receipts = {item.transaction_id: item for item in snapshot.transactions}
        rows = [
            item
            for item in snapshot.post_final_finding_dispositions
            if item.assessment_result_id == result_id and item.finding_id == finding_id
        ]
        rows.sort(
            key=lambda item: receipts[item.accepted_transaction_id].committed_revision
        )
        return rows[-1] if rows else None

    @staticmethod
    def _current_status(snapshot: Any, guidance_id: str) -> Any:
        receipts = {item.transaction_id: item for item in snapshot.transactions}
        rows = [
            item
            for item in snapshot.post_final_guidance_statuses
            if item.guidance_id == guidance_id
        ]
        rows.sort(
            key=lambda item: receipts[item.accepted_transaction_id].committed_revision
        )
        return rows[-1] if rows else None

    @staticmethod
    def _validate(model: type[StrictModel], value: Mapping[str, object]) -> Any:
        try:
            return model.model_validate(value, strict=True)
        except (TypeError, ValidationError, ValueError) as exc:
            raise PostFinalReviewError("post_final_review_request_invalid") from exc

    def record_disposition(self, value: Mapping[str, object]) -> dict[str, object]:
        command = self._validate(FindingDispositionInput, value)
        loaded = self._load()
        snapshot = loaded["snapshot"]
        result = loaded["result"]
        view = loaded["view"]
        existing = next(
            (
                item
                for item in snapshot.post_final_finding_dispositions
                if item.human_request_id == command.human_request_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.assessment_result_id != command.assessment_result_id
                or existing.reader_view_sha256 != command.reader_view_sha256
                or existing.finding_id != command.finding_id
                or existing.finding_fingerprint != command.finding_fingerprint
                or existing.decision != command.decision
                or existing.human_note != command.human_note
                or existing.human_actor_id != command.human_actor_id
            ):
                raise PostFinalReviewError("post_final_review_request_conflict")
            receipt = self._receipt(snapshot, existing.accepted_transaction_id)
            return {
                "ok": True,
                "replayed": True,
                "disposition_id": existing.disposition_id,
                "receipt_id": receipt.transaction_id,
            }
        finding = loaded["findings"].get(command.finding_id)
        expected_fingerprint = (
            _finding_fingerprint(
                result_id=result.assessment_result_id,
                result_fingerprint=result.result_fingerprint,
                view_sha256=view.view_sha256,
                finding=finding,
            )
            if finding is not None
            else None
        )
        if (
            command.assessment_result_id != result.assessment_result_id
            or command.reader_view_sha256 != view.view_sha256
            or command.finding_fingerprint != expected_fingerprint
        ):
            raise PostFinalReviewError("post_final_review_binding_invalid")
        semantic = {
            "assessment_result_id": result.assessment_result_id,
            "reader_view_sha256": view.view_sha256,
            "finding_id": command.finding_id,
            "finding_fingerprint": expected_fingerprint,
            "decision": command.decision,
            "human_note": command.human_note,
            "human_actor_id": command.human_actor_id,
        }
        previous = self._current_disposition(
            snapshot, result.assessment_result_id, command.finding_id
        )
        identity = {
            **semantic,
            "run_id": result.run_id,
            "human_request_id": command.human_request_id,
            "previous_disposition_id": (
                previous.disposition_id if previous is not None else None
            ),
        }
        disposition_id = _id("pf-laj-disposition", identity)
        transaction_id = _id("pf-laj-disposition-tx", identity)
        event_id = _id("pf-laj-disposition-event", identity)
        payload: dict[str, object] = {
            "schema_version": PostFinalFindingDispositionRecord.schema_id,
            "disposition_id": disposition_id,
            "run_id": result.run_id,
            "finalized_lineage_fingerprint": result.finalized_lineage_fingerprint,
            "assessment_result_id": result.assessment_result_id,
            "assessment_result_fingerprint": result.result_fingerprint,
            "reader_view_sha256": view.view_sha256,
            "finding_id": command.finding_id,
            "finding_fingerprint": expected_fingerprint,
            "previous_disposition_id": (
                previous.disposition_id if previous is not None else None
            ),
            "decision": command.decision,
            "human_note": command.human_note,
            "human_actor_id": command.human_actor_id,
            "human_request_id": command.human_request_id,
            "recorded_at": _utc_now(),
            "disposition_event_id": event_id,
            "accepted_transaction_id": transaction_id,
        }
        payload["disposition_fingerprint"] = _record_fingerprint(
            payload, "disposition_fingerprint"
        )
        record = PostFinalFindingDispositionRecord.model_validate(payload, strict=True)
        event = _event(
            run_id=result.run_id,
            event_id=event_id,
            event_type="post_final_finding_disposition_recorded",
            transaction_id=transaction_id,
            decision=disposition_id,
            metadata={"disposition_fingerprint": record.disposition_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                with store.begin(
                    result.run_id,
                    transaction_id,
                    "post_final_finding_disposition",
                    store.current_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_finding_disposition(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise PostFinalReviewError(str(exc)) from exc
        return {
            "ok": True,
            "replayed": False,
            "disposition_id": disposition_id,
            "receipt_id": receipt.transaction_id,
        }

    def append_guidance_draft(self, value: Mapping[str, object]) -> dict[str, object]:
        command = self._validate(GuidanceDraftInput, value)
        loaded = self._load()
        snapshot = loaded["snapshot"]
        result = loaded["result"]
        existing = next(
            (
                item
                for item in snapshot.post_final_guidance_drafts
                if item.human_request_id == command.human_request_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.assessment_result_id != command.assessment_result_id
                or existing.finding_id != command.finding_id
                or existing.disposition_id != command.disposition_id
                or existing.guidance_text != command.guidance_text
                or existing.human_actor_id != command.human_actor_id
            ):
                raise PostFinalReviewError("post_final_review_request_conflict")
            receipt = self._receipt(snapshot, existing.accepted_transaction_id)
            return {
                "ok": True,
                "replayed": True,
                "guidance_id": existing.guidance_id,
                "draft_revision": existing.draft_revision,
                "receipt_id": receipt.transaction_id,
            }
        finding = loaded["findings"].get(command.finding_id)
        disposition = next(
            (
                item
                for item in snapshot.post_final_finding_dispositions
                if item.disposition_id == command.disposition_id
            ),
            None,
        )
        current = self._current_disposition(
            snapshot, command.assessment_result_id, command.finding_id
        )
        if (
            finding is None
            or command.assessment_result_id != result.assessment_result_id
            or disposition is None
            or current is None
            or current.disposition_id != disposition.disposition_id
            or disposition.decision != "accept"
            or disposition.finding_fingerprint
            != _finding_fingerprint(
                result_id=result.assessment_result_id,
                result_fingerprint=result.result_fingerprint,
                view_sha256=loaded["view"].view_sha256,
                finding=finding,
            )
        ):
            raise PostFinalReviewError("post_final_guidance_not_accepted")
        guidance_id = _id(
            "pf-laj-guidance",
            {
                "run_id": result.run_id,
                "assessment_result_id": result.assessment_result_id,
                "finding_id": command.finding_id,
            },
        )
        prior = sorted(
            (
                item
                for item in snapshot.post_final_guidance_drafts
                if item.guidance_id == guidance_id
            ),
            key=lambda item: item.draft_revision,
        )
        draft_revision = len(prior) + 1
        identity = {
            "guidance_id": guidance_id,
            "draft_revision": draft_revision,
            "human_request_id": command.human_request_id,
            "guidance_text": command.guidance_text,
            "disposition_id": disposition.disposition_id,
        }
        transaction_id = _id("pf-laj-guidance-draft-tx", identity)
        event_id = _id("pf-laj-guidance-draft-event", identity)
        payload: dict[str, object] = {
            "schema_version": PostFinalGuidanceDraftRevision.schema_id,
            "guidance_id": guidance_id,
            "draft_revision": draft_revision,
            "run_id": result.run_id,
            "finalized_lineage_fingerprint": result.finalized_lineage_fingerprint,
            "assessment_result_id": result.assessment_result_id,
            "assessment_result_fingerprint": result.result_fingerprint,
            "finding_id": disposition.finding_id,
            "finding_fingerprint": disposition.finding_fingerprint,
            "disposition_id": disposition.disposition_id,
            "disposition_fingerprint": disposition.disposition_fingerprint,
            "previous_draft_revision": draft_revision - 1 or None,
            "guidance_scope": "finding_only",
            "guidance_text": command.guidance_text,
            "guidance_sha256": hashlib.sha256(
                command.guidance_text.encode("utf-8")
            ).hexdigest(),
            "human_actor_id": command.human_actor_id,
            "human_request_id": command.human_request_id,
            "recorded_at": _utc_now(),
            "draft_event_id": event_id,
            "accepted_transaction_id": transaction_id,
        }
        payload["draft_fingerprint"] = _record_fingerprint(payload, "draft_fingerprint")
        record = PostFinalGuidanceDraftRevision.model_validate(payload, strict=True)
        event = _event(
            run_id=result.run_id,
            event_id=event_id,
            event_type="post_final_guidance_draft_recorded",
            transaction_id=transaction_id,
            decision=guidance_id,
            metadata={"draft_fingerprint": record.draft_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                with store.begin(
                    result.run_id,
                    transaction_id,
                    "post_final_guidance_draft",
                    store.current_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_guidance_draft(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise PostFinalReviewError(str(exc)) from exc
        return {
            "ok": True,
            "replayed": False,
            "guidance_id": guidance_id,
            "draft_revision": draft_revision,
            "receipt_id": receipt.transaction_id,
        }

    def _record_status(
        self,
        value: Mapping[str, object],
        *,
        status: Literal["approved", "deactivated", "reverted", "superseded"],
    ) -> dict[str, object]:
        command = self._validate(GuidanceStatusInput, value)
        loaded = self._load()
        snapshot = loaded["snapshot"]
        result = loaded["result"]
        existing = next(
            (
                item
                for item in snapshot.post_final_guidance_statuses
                if item.human_request_id == command.human_request_id
            ),
            None,
        )
        if existing is not None:
            existing_draft = next(
                (
                    item
                    for item in snapshot.post_final_guidance_drafts
                    if item.guidance_id == existing.guidance_id
                    and item.draft_revision == existing.draft_revision
                ),
                None,
            )
            if (
                existing.guidance_id != command.guidance_id
                or existing.draft_revision != command.draft_revision
                or existing.status != status
                or existing.human_actor_id != command.human_actor_id
                or existing_draft is None
                or existing_draft.assessment_result_id != result.assessment_result_id
            ):
                raise PostFinalReviewError("post_final_review_request_conflict")
            receipt = self._receipt(snapshot, existing.accepted_transaction_id)
            return {
                "ok": True,
                "replayed": True,
                "status_revision_id": existing.status_revision_id,
                "receipt_id": receipt.transaction_id,
            }
        draft = next(
            (
                item
                for item in snapshot.post_final_guidance_drafts
                if item.guidance_id == command.guidance_id
                and item.draft_revision == command.draft_revision
            ),
            None,
        )
        if (
            draft is None
            or draft.finalized_lineage_fingerprint
            != result.finalized_lineage_fingerprint
            or draft.assessment_result_id != result.assessment_result_id
        ):
            raise PostFinalReviewError("post_final_guidance_stale")
        latest_draft_revision = max(
            (
                item.draft_revision
                for item in snapshot.post_final_guidance_drafts
                if item.guidance_id == command.guidance_id
            ),
            default=0,
        )
        if command.draft_revision != latest_draft_revision:
            raise PostFinalReviewError("post_final_guidance_stale")
        current_disposition = self._current_disposition(
            snapshot,
            draft.assessment_result_id,
            draft.finding_id,
        )
        approval_eligible = (
            current_disposition is not None
            and current_disposition.disposition_id == draft.disposition_id
            and current_disposition.decision == "accept"
        )
        if status == "approved":
            if not approval_eligible:
                raise PostFinalReviewError("post_final_guidance_stale")
        previous = self._current_status(snapshot, command.guidance_id)
        identity = {
            "guidance_id": command.guidance_id,
            "draft_revision": command.draft_revision,
            "status": status,
            "human_request_id": command.human_request_id,
        }
        status_revision_id = _id("pf-laj-guidance-status", identity)
        transaction_id = _id("pf-laj-guidance-status-tx", identity)
        event_id = _id("pf-laj-guidance-status-event", identity)
        payload: dict[str, object] = {
            "schema_version": PostFinalGuidanceStatusRevision.schema_id,
            "status_revision_id": status_revision_id,
            "run_id": result.run_id,
            "finalized_lineage_fingerprint": result.finalized_lineage_fingerprint,
            "guidance_id": draft.guidance_id,
            "draft_revision": draft.draft_revision,
            "guidance_sha256": draft.guidance_sha256,
            "status": status,
            "previous_status_revision_id": (
                previous.status_revision_id if previous is not None else None
            ),
            "human_actor_id": command.human_actor_id,
            "human_request_id": command.human_request_id,
            "recorded_at": _utc_now(),
            "status_event_id": event_id,
            "accepted_transaction_id": transaction_id,
        }
        payload["status_fingerprint"] = _record_fingerprint(
            payload, "status_fingerprint"
        )
        record = PostFinalGuidanceStatusRevision.model_validate(payload, strict=True)
        if not post_final_guidance_status_transition_allowed(
            previous,
            record,
            approval_eligible=approval_eligible,
        ):
            raise PostFinalReviewError("post_final_guidance_transition_invalid")
        event = _event(
            run_id=result.run_id,
            event_id=event_id,
            event_type="post_final_guidance_status_recorded",
            transaction_id=transaction_id,
            decision=status_revision_id,
            metadata={"status_fingerprint": record.status_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                with store.begin(
                    result.run_id,
                    transaction_id,
                    "post_final_guidance_status",
                    store.current_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_guidance_status(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise PostFinalReviewError(str(exc)) from exc
        return {
            "ok": True,
            "replayed": False,
            "status_revision_id": status_revision_id,
            "receipt_id": receipt.transaction_id,
        }

    def approve_guidance(self, value: Mapping[str, object]) -> dict[str, object]:
        return self._record_status(value, status="approved")

    def deactivate_guidance(self, value: Mapping[str, object]) -> dict[str, object]:
        return self._record_status(value, status="deactivated")

    def revert_guidance(self, value: Mapping[str, object]) -> dict[str, object]:
        return self._record_status(value, status="reverted")

    def supersede_guidance(self, value: Mapping[str, object]) -> dict[str, object]:
        return self._record_status(value, status="superseded")

    def review_status(self) -> dict[str, object]:
        loaded = self._load()
        snapshot = loaded["snapshot"]
        result = loaded["result"]
        view = loaded["view"]
        dispositions = []
        for finding in view.findings:
            fingerprint = _finding_fingerprint(
                result_id=result.assessment_result_id,
                result_fingerprint=result.result_fingerprint,
                view_sha256=view.view_sha256,
                finding=finding,
            )
            current = self._current_disposition(
                snapshot, result.assessment_result_id, finding.finding_id
            )
            dispositions.append(
                {
                    "finding_id": finding.finding_id,
                    "finding_fingerprint": fingerprint,
                    "current": (
                        current.model_dump(mode="json", exclude_unset=False)
                        if current is not None
                        else None
                    ),
                }
            )
        draft_rows = sorted(
            (
                item
                for item in snapshot.post_final_guidance_drafts
                if item.assessment_result_id == result.assessment_result_id
            ),
            key=lambda item: (item.guidance_id, item.draft_revision),
        )
        latest_draft_revisions = {
            item.guidance_id: max(
                candidate.draft_revision
                for candidate in draft_rows
                if candidate.guidance_id == item.guidance_id
            )
            for item in draft_rows
        }
        drafts = []
        for item in draft_rows:
            payload = item.model_dump(mode="json", exclude_unset=False)
            current_status = self._current_status(snapshot, item.guidance_id)
            current_disposition = self._current_disposition(
                snapshot,
                item.assessment_result_id,
                item.finding_id,
            )
            legal_statuses = post_final_guidance_legal_actions(
                current_status,
                target_draft_revision=item.draft_revision,
                approval_eligible=(
                    current_disposition is not None
                    and current_disposition.disposition_id == item.disposition_id
                    and current_disposition.decision == "accept"
                ),
            )
            payload["legal_actions"] = (
                [
                    {
                        "approved": "approve",
                        "deactivated": "deactivate",
                        "reverted": "revert",
                        "superseded": "supersede",
                    }[status]
                    for status in legal_statuses
                ]
                if item.draft_revision == latest_draft_revisions[item.guidance_id]
                else []
            )
            drafts.append(payload)
        selected_guidance_ids = {item.guidance_id for item in draft_rows}
        statuses = [
            item.model_dump(mode="json", exclude_unset=False)
            for item in snapshot.post_final_guidance_statuses
            if item.guidance_id in selected_guidance_ids
        ]
        return {
            "ok": True,
            "run_id": result.run_id,
            "finalized_lineage_fingerprint": result.finalized_lineage_fingerprint,
            "assessment_result_id": result.assessment_result_id,
            "assessment_result_fingerprint": result.result_fingerprint,
            "reader_view_sha256": view.view_sha256,
            "dispositions": dispositions,
            "guidance_drafts": drafts,
            "guidance_statuses": statuses,
            "next_run_consumption": NEXT_RUN_CONSUMPTION_STATUS,
            "provider_calls": 0,
        }


__all__ = [
    "FindingDispositionInput",
    "GuidanceDraftInput",
    "GuidanceStatusInput",
    "POST_FINAL_DISPOSITION_INPUT_SCHEMA",
    "POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA",
    "POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA",
    "NEXT_RUN_CONSUMPTION_STATUS",
    "PostFinalReviewError",
    "PostFinalReviewService",
]
