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

from pydantic import Field, ValidationError, model_validator

from multi_agent_brief.contracts.v2 import (
    PostFinalFindingDispositionRecord,
    HumanObservationReportSpan,
    PostFinalHumanObservationRecord,
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
    _bounded_context_from_direction,
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
from multi_agent_brief.product.post_final_assessment_read_model import (
    finalized_lineage_fingerprint,
)
from multi_agent_brief.runtime_host_v2.projections import (
    build_finalized_local_review_projection_from_history,
)
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.semantic_evaluator.contracts import SpanLocator
from multi_agent_brief.semantic_evaluator.normalization import (
    normalize_markdown,
    replay_span,
)
from multi_agent_brief.semantic_evaluator.profile import load_profile

POST_FINAL_DISPOSITION_INPUT_SCHEMA = (
    "briefloop.post_final_finding_disposition_input.v1"
)
POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA = "briefloop.post_final_guidance_draft_input.v1"
POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA = (
    "briefloop.post_final_guidance_status_input.v1"
)
POST_FINAL_HUMAN_OBSERVATION_INPUT_SCHEMA = (
    "briefloop.post_final_human_observation_input.v1"
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


class HumanObservationInput(StrictModel):
    """Report-bound Human observation command (result binding is optional)."""

    schema_version: Literal[
        "briefloop.post_final_human_observation_input.v1",
        "briefloop.post_final_human_observation_supersede_input.v1",
    ]
    human_actor_id: str
    human_request_id: str
    observation_text: str = Field(min_length=1, max_length=12000)
    assessment_result_id: str | None = None
    assessment_result_fingerprint: str | None = None
    reader_view_sha256: str | None = None
    requirement_id: str | None = None
    claim_id: str | None = None
    report_span: HumanObservationReportSpan | None = None
    scope_class: Literal["O1", "O2"] | None = None
    dimension_id: str | None = None
    previous_observation_id: str | None = None
    previous_observation_fingerprint: str | None = None

    @staticmethod
    def _total(values: tuple[object, ...]) -> bool:
        return all(value is not None for value in values)

    @classmethod
    def _validate_observation_fields(
        cls, value: "HumanObservationInput"
    ) -> "HumanObservationInput":
        selected = (
            value.assessment_result_id,
            value.assessment_result_fingerprint,
            value.reader_view_sha256,
        )
        if any(item is not None for item in selected) and not cls._total(selected):
            raise ValueError("selected assessment binding must be complete")
        if (value.scope_class is None) != (value.dimension_id is None):
            raise ValueError("scope_class and dimension_id must be paired")
        if (value.previous_observation_id is None) != (
            value.previous_observation_fingerprint is None
        ):
            raise ValueError("observation predecessor binding must be complete")
        if not value.observation_text.strip():
            raise ValueError("observation_text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_observation(self) -> "HumanObservationInput":
        return self._validate_observation_fields(self)


class GuidanceDraftInput(StrictModel):
    schema_version: Literal["briefloop.post_final_guidance_draft_input.v1"]
    human_actor_id: str
    human_request_id: str
    provenance_kind: Literal["accepted_model_finding", "human_observation"] = (
        "accepted_model_finding"
    )
    assessment_result_id: str | None = None
    assessment_result_fingerprint: str | None = None
    finding_id: str | None = None
    finding_fingerprint: str | None = None
    disposition_id: str | None = None
    disposition_fingerprint: str | None = None
    observation_id: str | None = None
    observation_fingerprint: str | None = None
    guidance_text: str = Field(min_length=1, max_length=12000)

    @model_validator(mode="after")
    def validate_provenance(self) -> "GuidanceDraftInput":
        result = (self.assessment_result_id, self.assessment_result_fingerprint)
        finding = (self.finding_id, self.finding_fingerprint)
        disposition = (self.disposition_id, self.disposition_fingerprint)
        observation = (self.observation_id, self.observation_fingerprint)
        if self.provenance_kind == "accepted_model_finding":
            if not all(
                item is not None
                for item in (
                    self.assessment_result_id,
                    self.finding_id,
                    self.disposition_id,
                )
            ):
                raise ValueError("accepted model finding provenance is incomplete")
            if any(item is not None for item in observation):
                raise ValueError("accepted model finding cannot bind observation")
        else:
            if not all(item is not None for item in observation):
                raise ValueError("human observation provenance is incomplete")
            if any(item is not None for item in finding + disposition):
                raise ValueError("human observation cannot bind finding/disposition")
            if any(item is not None for item in result) and not all(
                item is not None for item in result
            ):
                raise ValueError("human observation result binding is incomplete")
        return self


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
        assessment_result_id: str | None = None,
        assessment_result_fingerprint: str | None = None,
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

    def _load_report_bound(self) -> dict[str, Any]:
        """Resolve the immutable finalized report without a Reader result."""
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                history = store.load_history()
            current_heads = {
                item.workspace_run_head.current_run_id
                for item in history.snapshots
                if item.workspace_run_head is not None
            }
            if len(current_heads) != 1:
                raise PostFinalReviewError("post_final_review_current_head_required")
            run_id = next(iter(current_heads))
            facts = build_finalized_local_review_projection_from_history(
                self.workspace,
                history,
                run_id=run_id,
                require_current_head=not self.allow_historical,
            ).facts
            verified = CoreRunDomainVerifier().verify_loaded_history(
                history, facts.run_id, require_current_head=False
            )
            action = _require_current_finalized_action(
                facts, classify_core_run_next_action(verified)
            )
            snapshot = history.snapshot_at_revision(
                facts.run_id, history.store_revision
            )
            lineage = finalized_lineage_fingerprint(facts, action)
            return {
                "facts": facts,
                "history": history,
                "snapshot": snapshot,
                "request": None,
                "result": None,
                "view": None,
                "findings": {},
                "finalized_lineage_fingerprint": lineage,
            }
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

    def _load(self) -> dict[str, Any]:
        if self.assessment_result_id is None:
            return self._load_report_bound()
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
                allow_historical=self.allow_historical,
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
        if projection.status in {"invalid", "unsupported", "pending", "not_requested"}:
            raise PostFinalReviewError("post_final_review_unavailable")
        if projection.view is None:
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
            "finalized_lineage_fingerprint": finalized_lineage_fingerprint(
                facts, action
            ),
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
    def _current_observation(snapshot: Any, observation_id: str) -> Any:
        rows = [
            item
            for item in snapshot.post_final_human_observations
            if item.observation_id == observation_id
        ]
        return rows[0] if len(rows) == 1 else None

    @staticmethod
    def _observation_chain_head(snapshot: Any, observation: Any) -> Any:
        successors = {
            item.previous_observation_id
            for item in snapshot.post_final_human_observations
            if item.previous_observation_id is not None
        }
        if observation.observation_id in successors:
            return None
        return observation

    def _validate_observation_refs(
        self,
        command: HumanObservationInput,
        loaded: Mapping[str, Any],
    ) -> tuple[Any, Any, Any]:
        facts = loaded["facts"]
        result = loaded.get("result")
        report = facts.report
        if command.report_span is not None:
            if command.report_span.report_sha256 != report.sha256:
                raise PostFinalReviewError("post_final_review_report_span_invalid")
            try:
                normalized = normalize_markdown(
                    report.markdown_utf8,
                    artifact_id=report.artifact_id,
                    language="en" if result is None else (result.language or "en"),
                )
                replay_span(
                    normalized.artifact,
                    SpanLocator.model_validate(
                        {
                            "report_sha256": command.report_span.report_sha256,
                            "block_id": command.report_span.block_id,
                            "start_char": command.report_span.start_char,
                            "end_char": command.report_span.end_char,
                            "excerpt_sha256": command.report_span.excerpt_sha256,
                        },
                        strict=True,
                    ),
                )
            except Exception as exc:
                raise PostFinalReviewError(
                    "post_final_review_report_span_invalid"
                ) from exc
        selected_result = None
        if command.assessment_result_id is not None:
            if (
                self.assessment_result_id is not None
                and command.assessment_result_id != self.assessment_result_id
            ):
                raise PostFinalReviewError("post_final_review_binding_invalid")
            snapshot = loaded["snapshot"]
            matches = [
                item
                for item in snapshot.post_final_assessment_results
                if item.assessment_result_id == command.assessment_result_id
                and item.result_fingerprint == command.assessment_result_fingerprint
            ]
            if len(matches) != 1:
                raise PostFinalReviewError("post_final_review_binding_invalid")
            selected_result = matches[0]
            if (
                selected_result.finalized_lineage_fingerprint
                != loaded["finalized_lineage_fingerprint"]
            ):
                raise PostFinalReviewError("post_final_review_binding_invalid")
            if selected_result.reader_view_sha256 != command.reader_view_sha256:
                raise PostFinalReviewError("post_final_review_binding_invalid")
        if command.requirement_id is not None:
            bindings = loaded["snapshot"].run_contract_bindings
            if len(bindings) != 1 or bindings[0].run_id != facts.run_id:
                raise PostFinalReviewError("post_final_review_reference_invalid")
            context = _bounded_context_from_direction(
                bindings[0],
                run_id=facts.run_id,
                language="en" if result is None else (result.language or "en"),
            )
            context_requirement_ids = {
                item.requirement_id for item in context.requirements
            }
            if command.requirement_id not in context_requirement_ids:
                raise PostFinalReviewError("post_final_review_reference_invalid")
            if selected_result is not None:
                if not isinstance(selected_result.reader_view_payload, dict):
                    raise PostFinalReviewError("post_final_review_reference_invalid")
                result_requirement_ids = {
                    str(item.get("requirement_id"))
                    for item in selected_result.reader_view_payload.get(
                        "requirement_assessments", []
                    )
                    if isinstance(item, dict) and item.get("requirement_id")
                }
                if command.requirement_id not in result_requirement_ids:
                    raise PostFinalReviewError("post_final_review_reference_invalid")
        if command.claim_id is not None:
            if command.claim_id not in {
                item.claim_id for item in loaded["snapshot"].claims
            }:
                raise PostFinalReviewError("post_final_review_reference_invalid")
        if command.scope_class is not None:
            dimensions = {
                (dimension.scope_class, dimension.dimension_id)
                for dimension in load_profile(
                    "management_brief_en_v1"
                ).profile.dimensions
            }
            if (command.scope_class, command.dimension_id) not in dimensions:
                raise PostFinalReviewError("post_final_review_reference_invalid")
        return facts, report, selected_result

    @staticmethod
    def _validate(model: type[StrictModel], value: Mapping[str, object]) -> Any:
        try:
            return model.model_validate(value, strict=True)
        except (TypeError, ValidationError, ValueError) as exc:
            raise PostFinalReviewError("post_final_review_request_invalid") from exc

    @staticmethod
    def _require_quiescent_current_head(store: SQLiteControlStore) -> int:
        """Bind a Human write to one live current-head quiescence snapshot."""

        history = store.load_history()
        current_run_ids = {
            item.workspace_run_head.current_run_id
            for item in history.snapshots
            if item.workspace_run_head is not None
        }
        if len(current_run_ids) != 1:
            raise PostFinalReviewError("post_final_review_request_conflict")
        current_run_id = next(iter(current_run_ids))
        current = next(
            (item for item in history.snapshots if item.run.run_id == current_run_id),
            None,
        )
        if current is None or any(
            item.status == "active" for item in current.invocations
        ):
            raise PostFinalReviewError("post_final_review_request_conflict")
        return history.store_revision

    @staticmethod
    def _write_error(exc: ControlStoreError) -> PostFinalReviewError:
        if str(exc) == "store_revision_conflict":
            return PostFinalReviewError("post_final_review_request_conflict")
        return PostFinalReviewError(str(exc))

    def record_disposition(self, value: Mapping[str, object]) -> dict[str, object]:
        command = self._validate(FindingDispositionInput, value)
        loaded = self._load()
        snapshot = loaded["snapshot"]
        result = loaded["result"]
        view = loaded["view"]
        if result is None or view is None or view.binding is None:
            raise PostFinalReviewError("post_final_review_unavailable")
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
            run_id=loaded["facts"].run_id,
            event_id=event_id,
            event_type="post_final_finding_disposition_recorded",
            transaction_id=transaction_id,
            decision=disposition_id,
            metadata={"disposition_fingerprint": record.disposition_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                expected_revision = self._require_quiescent_current_head(store)
                with store.begin(
                    result.run_id,
                    transaction_id,
                    "post_final_finding_disposition",
                    expected_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_finding_disposition(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise self._write_error(exc) from exc
        return {
            "ok": True,
            "replayed": False,
            "disposition_id": disposition_id,
            "receipt_id": receipt.transaction_id,
        }

    def record_human_observation(
        self, value: Mapping[str, object]
    ) -> dict[str, object]:
        """Append one report-bound Human observation, with optional result binding."""
        command = self._validate(HumanObservationInput, value)
        if (
            command.schema_version != POST_FINAL_HUMAN_OBSERVATION_INPUT_SCHEMA
            or command.previous_observation_id is not None
        ):
            raise PostFinalReviewError("post_final_review_request_invalid")
        loaded = self._load()
        facts, report, selected_result = self._validate_observation_refs(
            command, loaded
        )
        snapshot = loaded["snapshot"]
        existing = next(
            (
                item
                for item in snapshot.post_final_human_observations
                if item.human_request_id == command.human_request_id
            ),
            None,
        )
        if existing is not None:
            semantic = {
                "origin": existing.origin,
                "finalized_lineage_fingerprint": existing.finalized_lineage_fingerprint,
                "report_revision": existing.report_revision,
                "report_artifact_id": existing.report_artifact_id,
                "report_sha256": existing.report_sha256,
                "observation_text": existing.observation_text,
                "assessment_result_id": existing.assessment_result_id,
                "assessment_result_fingerprint": existing.assessment_result_fingerprint,
                "reader_view_sha256": existing.reader_view_sha256,
                "requirement_id": existing.requirement_id,
                "claim_id": existing.claim_id,
                "report_span": (
                    None
                    if existing.report_span is None
                    else existing.report_span.model_dump(mode="json")
                ),
                "scope_class": existing.scope_class,
                "dimension_id": existing.dimension_id,
                "human_actor_id": existing.human_actor_id,
            }
            incoming = {
                "origin": "human",
                "finalized_lineage_fingerprint": loaded[
                    "finalized_lineage_fingerprint"
                ],
                "report_revision": report.artifact_revision,
                "report_artifact_id": report.artifact_id,
                "report_sha256": report.sha256,
                "observation_text": command.observation_text,
                "assessment_result_id": command.assessment_result_id,
                "assessment_result_fingerprint": command.assessment_result_fingerprint,
                "reader_view_sha256": command.reader_view_sha256,
                "requirement_id": command.requirement_id,
                "claim_id": command.claim_id,
                "report_span": (
                    None
                    if command.report_span is None
                    else command.report_span.model_dump(mode="json")
                ),
                "scope_class": command.scope_class,
                "dimension_id": command.dimension_id,
                "human_actor_id": command.human_actor_id,
            }
            if semantic != incoming:
                raise PostFinalReviewError("post_final_review_request_conflict")
            receipt = self._receipt(snapshot, existing.accepted_transaction_id)
            return {
                "ok": True,
                "replayed": True,
                "origin": "human",
                "observation_id": existing.observation_id,
                "observation_revision": existing.observation_revision,
                "observation_fingerprint": existing.observation_fingerprint,
                "receipt_id": receipt.transaction_id,
            }
        lineage = loaded["finalized_lineage_fingerprint"]
        identity = {
            "run_id": facts.run_id,
            "lineage": lineage,
            "report_revision": report.artifact_revision,
            "report_artifact_id": report.artifact_id,
            "report_sha256": report.sha256,
            "assessment_result_id": (
                None
                if selected_result is None
                else selected_result.assessment_result_id
            ),
            "assessment_result_fingerprint": (
                None if selected_result is None else selected_result.result_fingerprint
            ),
            "reader_view_sha256": command.reader_view_sha256,
            "observation_text": command.observation_text,
            "requirement_id": command.requirement_id,
            "claim_id": command.claim_id,
            "report_span": (
                None
                if command.report_span is None
                else command.report_span.model_dump(mode="json")
            ),
            "scope_class": command.scope_class,
            "dimension_id": command.dimension_id,
            "human_request_id": command.human_request_id,
        }
        observation_id = _id("pf-human-observation", identity)
        transaction_id = _id("pf-human-observation-tx", identity)
        event_id = _id("pf-human-observation-event", identity)
        payload: dict[str, object] = {
            "schema_version": PostFinalHumanObservationRecord.schema_id,
            "origin": "human",
            "observation_id": observation_id,
            "observation_revision": 1,
            "run_id": facts.run_id,
            "finalized_lineage_fingerprint": lineage,
            "report_revision": report.artifact_revision,
            "report_artifact_id": report.artifact_id,
            "report_sha256": report.sha256,
            "assessment_result_id": (
                None
                if selected_result is None
                else selected_result.assessment_result_id
            ),
            "assessment_result_fingerprint": (
                None if selected_result is None else selected_result.result_fingerprint
            ),
            "reader_view_sha256": command.reader_view_sha256,
            "observation_text": command.observation_text,
            "observation_sha256": hashlib.sha256(
                command.observation_text.encode("utf-8")
            ).hexdigest(),
            "requirement_id": command.requirement_id,
            "claim_id": command.claim_id,
            "report_span": (
                None
                if command.report_span is None
                else command.report_span.model_dump(mode="json")
            ),
            "scope_class": command.scope_class,
            "dimension_id": command.dimension_id,
            "previous_observation_id": None,
            "previous_observation_fingerprint": None,
            "human_actor_id": command.human_actor_id,
            "human_request_id": command.human_request_id,
            "recorded_at": _utc_now(),
            "observation_event_id": event_id,
            "accepted_transaction_id": transaction_id,
        }
        payload["observation_fingerprint"] = _record_fingerprint(
            payload, "observation_fingerprint"
        )
        record = PostFinalHumanObservationRecord.model_validate(payload, strict=True)
        event = _event(
            run_id=facts.run_id,
            event_id=event_id,
            event_type="post_final_human_observation_recorded",
            transaction_id=transaction_id,
            decision=observation_id,
            metadata={"observation_fingerprint": record.observation_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                expected_revision = self._require_quiescent_current_head(store)
                with store.begin(
                    facts.run_id,
                    transaction_id,
                    "post_final_human_observation",
                    expected_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_human_observation(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise self._write_error(exc) from exc
        return {
            "ok": True,
            "replayed": False,
            "origin": "human",
            "observation_id": observation_id,
            "observation_revision": 1,
            "observation_fingerprint": record.observation_fingerprint,
            "receipt_id": receipt.transaction_id,
        }

    def supersede_human_observation(
        self, value: Mapping[str, object]
    ) -> dict[str, object]:
        """Append a new observation revision bound to one current predecessor."""
        command = self._validate(HumanObservationInput, value)
        if (
            command.schema_version
            not in {
                POST_FINAL_HUMAN_OBSERVATION_INPUT_SCHEMA,
                "briefloop.post_final_human_observation_supersede_input.v1",
            }
            or command.previous_observation_id is None
        ):
            raise PostFinalReviewError("post_final_review_request_invalid")
        loaded = self._load()
        facts, report, selected_result = self._validate_observation_refs(
            command, loaded
        )
        snapshot = loaded["snapshot"]
        predecessor = self._current_observation(
            snapshot, command.previous_observation_id
        )
        existing = next(
            (
                item
                for item in snapshot.post_final_human_observations
                if item.human_request_id == command.human_request_id
            ),
            None,
        )
        if existing is not None:
            incoming_span = (
                None
                if command.report_span is None
                else command.report_span.model_dump(mode="json")
            )
            existing_span = (
                None
                if existing.report_span is None
                else existing.report_span.model_dump(mode="json")
            )
            if (
                existing.finalized_lineage_fingerprint
                != loaded["finalized_lineage_fingerprint"]
                or existing.report_revision != report.artifact_revision
                or existing.report_artifact_id != report.artifact_id
                or existing.report_sha256 != report.sha256
                or existing.previous_observation_id != command.previous_observation_id
                or existing.previous_observation_fingerprint
                != command.previous_observation_fingerprint
                or existing.observation_text != command.observation_text
                or existing.assessment_result_id != command.assessment_result_id
                or existing.assessment_result_fingerprint
                != command.assessment_result_fingerprint
                or existing.reader_view_sha256 != command.reader_view_sha256
                or existing.requirement_id != command.requirement_id
                or existing.claim_id != command.claim_id
                or existing.scope_class != command.scope_class
                or existing.dimension_id != command.dimension_id
                or existing_span != incoming_span
                or existing.human_actor_id != command.human_actor_id
            ):
                raise PostFinalReviewError("post_final_review_request_conflict")
            receipt = self._receipt(snapshot, existing.accepted_transaction_id)
            return {
                "ok": True,
                "replayed": True,
                "origin": "human",
                "observation_id": existing.observation_id,
                "observation_revision": existing.observation_revision,
                "observation_fingerprint": existing.observation_fingerprint,
                "receipt_id": receipt.transaction_id,
            }
        if (
            predecessor is None
            or predecessor.observation_fingerprint
            != command.previous_observation_fingerprint
            or predecessor.finalized_lineage_fingerprint
            != loaded["finalized_lineage_fingerprint"]
            or self._observation_chain_head(snapshot, predecessor) is None
        ):
            raise PostFinalReviewError("post_final_review_observation_stale")
        lineage = loaded["finalized_lineage_fingerprint"]
        identity = {
            "run_id": facts.run_id,
            "lineage": lineage,
            "previous_observation_id": predecessor.observation_id,
            "previous_observation_fingerprint": predecessor.observation_fingerprint,
            "report_revision": report.artifact_revision,
            "report_artifact_id": report.artifact_id,
            "report_sha256": report.sha256,
            "assessment_result_id": (
                None
                if selected_result is None
                else selected_result.assessment_result_id
            ),
            "assessment_result_fingerprint": (
                None if selected_result is None else selected_result.result_fingerprint
            ),
            "reader_view_sha256": command.reader_view_sha256,
            "observation_text": command.observation_text,
            "requirement_id": command.requirement_id,
            "claim_id": command.claim_id,
            "report_span": (
                None
                if command.report_span is None
                else command.report_span.model_dump(mode="json")
            ),
            "scope_class": command.scope_class,
            "dimension_id": command.dimension_id,
            "human_request_id": command.human_request_id,
        }
        observation_id = _id("pf-human-observation", identity)
        transaction_id = _id("pf-human-observation-tx", identity)
        event_id = _id("pf-human-observation-event", identity)
        payload: dict[str, object] = {
            "schema_version": PostFinalHumanObservationRecord.schema_id,
            "origin": "human",
            "observation_id": observation_id,
            "observation_revision": predecessor.observation_revision + 1,
            "run_id": facts.run_id,
            "finalized_lineage_fingerprint": lineage,
            "report_revision": report.artifact_revision,
            "report_artifact_id": report.artifact_id,
            "report_sha256": report.sha256,
            "assessment_result_id": (
                None
                if selected_result is None
                else selected_result.assessment_result_id
            ),
            "assessment_result_fingerprint": (
                None if selected_result is None else selected_result.result_fingerprint
            ),
            "reader_view_sha256": command.reader_view_sha256,
            "observation_text": command.observation_text,
            "observation_sha256": hashlib.sha256(
                command.observation_text.encode("utf-8")
            ).hexdigest(),
            "requirement_id": command.requirement_id,
            "claim_id": command.claim_id,
            "report_span": (
                None
                if command.report_span is None
                else command.report_span.model_dump(mode="json")
            ),
            "scope_class": command.scope_class,
            "dimension_id": command.dimension_id,
            "previous_observation_id": predecessor.observation_id,
            "previous_observation_fingerprint": predecessor.observation_fingerprint,
            "human_actor_id": command.human_actor_id,
            "human_request_id": command.human_request_id,
            "recorded_at": _utc_now(),
            "observation_event_id": event_id,
            "accepted_transaction_id": transaction_id,
        }
        payload["observation_fingerprint"] = _record_fingerprint(
            payload, "observation_fingerprint"
        )
        record = PostFinalHumanObservationRecord.model_validate(payload, strict=True)
        event = _event(
            run_id=facts.run_id,
            event_id=event_id,
            event_type="post_final_human_observation_recorded",
            transaction_id=transaction_id,
            decision=observation_id,
            metadata={"observation_fingerprint": record.observation_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                expected_revision = self._require_quiescent_current_head(store)
                with store.begin(
                    facts.run_id,
                    transaction_id,
                    "post_final_human_observation",
                    expected_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_human_observation(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise self._write_error(exc) from exc
        return {
            "ok": True,
            "replayed": False,
            "origin": "human",
            "observation_id": observation_id,
            "observation_revision": record.observation_revision,
            "observation_fingerprint": record.observation_fingerprint,
            "receipt_id": receipt.transaction_id,
        }

    def _append_observation_guidance(
        self, command: GuidanceDraftInput, loaded: Mapping[str, Any]
    ) -> dict[str, object]:
        snapshot = loaded["snapshot"]
        observation = self._current_observation(snapshot, command.observation_id or "")
        if (
            observation is None
            or observation.observation_fingerprint != command.observation_fingerprint
            or self._observation_chain_head(snapshot, observation) is None
            or observation.finalized_lineage_fingerprint
            != loaded["finalized_lineage_fingerprint"]
        ):
            raise PostFinalReviewError("post_final_guidance_stale")
        if command.assessment_result_id is not None and (
            observation.assessment_result_id != command.assessment_result_id
            or observation.assessment_result_fingerprint
            != command.assessment_result_fingerprint
        ):
            raise PostFinalReviewError("post_final_guidance_stale")
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
                existing.provenance_kind != "human_observation"
                or existing.observation_id != observation.observation_id
                or existing.observation_fingerprint
                != observation.observation_fingerprint
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
        guidance_id = _id(
            "pf-human-guidance",
            {
                "run_id": observation.run_id,
                "observation_id": observation.observation_id,
            },
        )
        prior = sorted(
            [
                item
                for item in snapshot.post_final_guidance_drafts
                if item.guidance_id == guidance_id
            ],
            key=lambda item: item.draft_revision,
        )
        draft_revision = len(prior) + 1
        identity = {
            "guidance_id": guidance_id,
            "draft_revision": draft_revision,
            "human_request_id": command.human_request_id,
            "guidance_text": command.guidance_text,
            "observation_id": observation.observation_id,
        }
        transaction_id = _id("pf-human-guidance-draft-tx", identity)
        event_id = _id("pf-human-guidance-draft-event", identity)
        payload: dict[str, object] = {
            "schema_version": PostFinalGuidanceDraftRevision.schema_id,
            "guidance_id": guidance_id,
            "draft_revision": draft_revision,
            "run_id": observation.run_id,
            "finalized_lineage_fingerprint": observation.finalized_lineage_fingerprint,
            "provenance_kind": "human_observation",
            "assessment_result_id": observation.assessment_result_id,
            "assessment_result_fingerprint": observation.assessment_result_fingerprint,
            "finding_id": None,
            "finding_fingerprint": None,
            "disposition_id": None,
            "disposition_fingerprint": None,
            "observation_id": observation.observation_id,
            "observation_fingerprint": observation.observation_fingerprint,
            "previous_draft_revision": draft_revision - 1 or None,
            "guidance_scope": "observation_only",
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
            run_id=observation.run_id,
            event_id=event_id,
            event_type="post_final_guidance_draft_recorded",
            transaction_id=transaction_id,
            decision=guidance_id,
            metadata={"draft_fingerprint": record.draft_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                expected_revision = self._require_quiescent_current_head(store)
                with store.begin(
                    observation.run_id,
                    transaction_id,
                    "post_final_guidance_draft",
                    expected_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_guidance_draft(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise self._write_error(exc) from exc
        return {
            "ok": True,
            "replayed": False,
            "guidance_id": guidance_id,
            "draft_revision": draft_revision,
            "receipt_id": receipt.transaction_id,
        }

    def append_guidance_draft(self, value: Mapping[str, object]) -> dict[str, object]:
        command = self._validate(GuidanceDraftInput, value)
        loaded = self._load()
        snapshot = loaded["snapshot"]
        result = loaded["result"]
        if command.provenance_kind == "human_observation":
            return self._append_observation_guidance(command, loaded)
        if result is None:
            raise PostFinalReviewError("post_final_guidance_not_accepted")
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
        expected_disposition_fingerprint = (
            disposition.disposition_fingerprint if disposition is not None else None
        )
        expected_finding_fingerprint = (
            _finding_fingerprint(
                result_id=result.assessment_result_id,
                result_fingerprint=result.result_fingerprint,
                view_sha256=loaded["view"].view_sha256,
                finding=finding,
            )
            if finding is not None
            else None
        )
        if (
            finding is None
            or command.assessment_result_id != result.assessment_result_id
            or disposition is None
            or current is None
            or current.disposition_id != disposition.disposition_id
            or disposition.decision != "accept"
            or disposition.finding_fingerprint != expected_finding_fingerprint
            or (
                command.assessment_result_fingerprint is not None
                and command.assessment_result_fingerprint != result.result_fingerprint
            )
            or (
                command.finding_fingerprint is not None
                and command.finding_fingerprint != expected_finding_fingerprint
            )
            or (
                command.disposition_fingerprint is not None
                and command.disposition_fingerprint != expected_disposition_fingerprint
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
            "provenance_kind": "accepted_model_finding",
            "assessment_result_id": result.assessment_result_id,
            "assessment_result_fingerprint": result.result_fingerprint,
            "finding_id": disposition.finding_id,
            "finding_fingerprint": expected_finding_fingerprint,
            "disposition_id": disposition.disposition_id,
            "disposition_fingerprint": expected_disposition_fingerprint,
            "observation_id": None,
            "observation_fingerprint": None,
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
                expected_revision = self._require_quiescent_current_head(store)
                with store.begin(
                    result.run_id,
                    transaction_id,
                    "post_final_guidance_draft",
                    expected_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_guidance_draft(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise self._write_error(exc) from exc
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
        lineage = loaded["finalized_lineage_fingerprint"]
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
                or existing_draft.finalized_lineage_fingerprint != lineage
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
        if draft is None or draft.finalized_lineage_fingerprint != lineage:
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
        if draft.provenance_kind == "human_observation":
            observation = self._current_observation(
                snapshot, draft.observation_id or ""
            )
            approval_eligible = (
                observation is not None
                and observation.observation_fingerprint == draft.observation_fingerprint
                and self._observation_chain_head(snapshot, observation) is not None
            )
        else:
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
            "run_id": loaded["facts"].run_id,
            "finalized_lineage_fingerprint": lineage,
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
            run_id=loaded["facts"].run_id,
            event_id=event_id,
            event_type="post_final_guidance_status_recorded",
            transaction_id=transaction_id,
            decision=status_revision_id,
            metadata={"status_fingerprint": record.status_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                expected_revision = self._require_quiescent_current_head(store)
                with store.begin(
                    loaded["facts"].run_id,
                    transaction_id,
                    "post_final_guidance_status",
                    expected_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_guidance_status(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise self._write_error(exc) from exc
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
        if result is None or view is None:
            observations = []
            superseded = {
                item.previous_observation_id
                for item in snapshot.post_final_human_observations
                if item.previous_observation_id is not None
            }
            for item in snapshot.post_final_human_observations:
                if (
                    item.finalized_lineage_fingerprint
                    != loaded["finalized_lineage_fingerprint"]
                ):
                    continue
                payload = item.model_dump(mode="json", exclude_unset=False)
                payload["status"] = (
                    "superseded" if item.observation_id in superseded else "current"
                )
                observations.append(payload)
            observations.sort(
                key=lambda item: (item["observation_id"], item["observation_revision"])
            )
            guidance = [
                item.model_dump(mode="json", exclude_unset=False)
                for item in snapshot.post_final_guidance_drafts
                if item.finalized_lineage_fingerprint
                == loaded["finalized_lineage_fingerprint"]
                and item.provenance_kind == "human_observation"
            ]
            statuses = [
                item.model_dump(mode="json", exclude_unset=False)
                for item in snapshot.post_final_guidance_statuses
                if item.finalized_lineage_fingerprint
                == loaded["finalized_lineage_fingerprint"]
            ]
            return {
                "ok": True,
                "run_id": loaded["facts"].run_id,
                "finalized_lineage_fingerprint": loaded[
                    "finalized_lineage_fingerprint"
                ],
                "assessment_result_id": None,
                "assessment_result_fingerprint": None,
                "reader_view_sha256": None,
                "dispositions": [],
                "human_observations": observations,
                "guidance_drafts": guidance,
                "guidance_statuses": statuses,
                "next_run_consumption": NEXT_RUN_CONSUMPTION_STATUS,
                "provider_calls": 0,
            }
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
                if item.finalized_lineage_fingerprint
                == result.finalized_lineage_fingerprint
                and (
                    item.provenance_kind == "human_observation"
                    or item.assessment_result_id == result.assessment_result_id
                )
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
            if item.provenance_kind == "human_observation":
                current_observation = self._current_observation(
                    snapshot, item.observation_id or ""
                )
                approval_eligible = (
                    current_observation is not None
                    and current_observation.observation_fingerprint
                    == item.observation_fingerprint
                    and self._observation_chain_head(snapshot, current_observation)
                    is not None
                )
            else:
                current_disposition = self._current_disposition(
                    snapshot,
                    item.assessment_result_id,
                    item.finding_id,
                )
                approval_eligible = (
                    current_disposition is not None
                    and current_disposition.disposition_id == item.disposition_id
                    and current_disposition.decision == "accept"
                )
            legal_statuses = post_final_guidance_legal_actions(
                current_status,
                target_draft_revision=item.draft_revision,
                approval_eligible=approval_eligible,
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
        observation_successors = {
            item.previous_observation_id
            for item in snapshot.post_final_human_observations
            if item.previous_observation_id is not None
        }
        human_observations = []
        for item in snapshot.post_final_human_observations:
            if (
                item.finalized_lineage_fingerprint
                != result.finalized_lineage_fingerprint
            ):
                continue
            payload = item.model_dump(mode="json", exclude_unset=False)
            payload["status"] = (
                "superseded"
                if item.observation_id in observation_successors
                else "current"
            )
            human_observations.append(payload)
        human_observations.sort(
            key=lambda item: (item["observation_id"], item["observation_revision"])
        )
        return {
            "ok": True,
            "run_id": result.run_id,
            "finalized_lineage_fingerprint": result.finalized_lineage_fingerprint,
            "assessment_result_id": result.assessment_result_id,
            "assessment_result_fingerprint": result.result_fingerprint,
            "reader_view_sha256": view.view_sha256,
            "dispositions": dispositions,
            "human_observations": human_observations,
            "guidance_drafts": drafts,
            "guidance_statuses": statuses,
            "next_run_consumption": NEXT_RUN_CONSUMPTION_STATUS,
            "provider_calls": 0,
        }


__all__ = [
    "FindingDispositionInput",
    "HumanObservationInput",
    "GuidanceDraftInput",
    "GuidanceStatusInput",
    "POST_FINAL_DISPOSITION_INPUT_SCHEMA",
    "POST_FINAL_GUIDANCE_DRAFT_INPUT_SCHEMA",
    "POST_FINAL_GUIDANCE_STATUS_INPUT_SCHEMA",
    "POST_FINAL_HUMAN_OBSERVATION_INPUT_SCHEMA",
    "NEXT_RUN_CONSUMPTION_STATUS",
    "PostFinalReviewError",
    "PostFinalReviewService",
]
