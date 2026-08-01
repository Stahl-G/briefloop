"""Store-qualified, post-final LAJ assessment orchestration.

The evaluator archive remains evidence only.  This module is the sole product
coordinator for the non-secret policy, one request claim, and one qualified
advisory result.  It never opens SQLite directly and never lets browser state,
raw findings, or a provider response affect Core truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    CoreRunNextAction,
    EventEnvelope,
    PostFinalAssessmentAbandonmentRecord,
    PostFinalAssessmentPolicyRevision,
    PostFinalAssessmentRequestRecord,
    PostFinalAssessmentResultRecord,
    StrictModel,
)
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.control_store.errors import ControlStoreError
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.publication_platform import capability_profile
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.projections import (
    build_finalized_local_review_projection,
)
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_ADAPTER_ID,
    ANTHROPIC_PROVIDER_ID,
    canonical_messages_endpoint_v1,
)
from multi_agent_brief.semantic_evaluator.archive import (
    trial_archive_path,
    verify_shadow_archive,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    BoundedContext,
    BoundedRequirement,
    InstrumentConfig,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import freeze_bounded_context
from multi_agent_brief.semantic_evaluator.reader import (
    build_laj_reader_view,
    render_laj_reader_json,
)
from multi_agent_brief.semantic_evaluator.runner import (
    PreparedShadowRun,
    ShadowRunResult,
    execute_prepared_shadow_run,
    prepared_shadow_budget,
    prepare_shadow_run_from_bytes,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes


POST_FINAL_ASSESSMENT_POLICY_SCHEMA = "briefloop.post_final_assessment_policy_set.v1"
POST_FINAL_ASSESSMENT_RUN_SCHEMA = "briefloop.post_final_assessment_run.v1"
# The evaluator's frozen admission boundary intentionally rejects archive roots
# beneath a declared workspace.  Keep this local evidence outside that input
# tree, while deriving it solely from the resolved workspace identity; no CLI
# path or persisted local path becomes authority.
_ARCHIVE_DIRECTORY = ".briefloop-post-final-laj"
_FINALIZED_LINEAGE_SCHEMA = "briefloop.post_final_assessment_finalized_lineage.v2"
_FINALIZED_REPORT_MEDIA_TYPE = "text/markdown"
_POST_FINAL_ASSESSMENT_RECEIPT_TYPES = frozenset(
    {
        "post_final_assessment_policy",
        "post_final_assessment_claim",
        "post_final_assessment_series_claim",
        "post_final_assessment_result",
        "post_final_finding_disposition",
        "post_final_guidance_draft",
        "post_final_guidance_status",
    }
)


class PostFinalAssessmentError(RuntimeError):
    """Stable, value-free product failure."""


class PostFinalAssessmentPolicyInput(StrictModel):
    """Human-only, non-secret policy input accepted by ``quality laj``."""

    schema_version: str
    human_actor_id: str
    human_request_id: str
    enabled: bool
    auto_run: bool
    auto_open: bool
    messages_endpoint: str
    requested_model_id: str
    model_version: str
    expected_model_identity: str
    instrument_config: dict[str, object]
    max_provider_calls: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_output_tokens_per_call: int
    public_safe_egress_attested: bool


class PostFinalAssessmentRunInput(StrictModel):
    """One explicit Human authorization for an independent assessment run."""

    schema_version: str
    human_actor_id: str
    human_request_id: str
    expected_store_revision: int
    finalized_lineage_fingerprint: str
    assessment_generation: int
    assessment_purpose: str
    predecessor_assessment_request_id: Optional[str] = None
    predecessor_assessment_request_fingerprint: Optional[str] = None
    predecessor_assessment_result_id: Optional[str] = None
    predecessor_result_fingerprint: Optional[str] = None
    predecessor_abandonment_id: Optional[str] = None
    predecessor_abandonment_fingerprint: Optional[str] = None
    abandon_predecessor: bool = False
    policy_revision_id: str
    policy_fingerprint: str
    public_safe_egress_attested: bool
    max_provider_calls: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_output_tokens_per_call: int


def _build_next_assessment_command(
    *,
    facts: Any,
    action: CoreRunNextAction,
    policy: PostFinalAssessmentPolicyRevision,
    series: tuple[PostFinalAssessmentRequestRecord, ...],
    results: Mapping[str, PostFinalAssessmentResultRecord],
    abandonments: Mapping[str, PostFinalAssessmentAbandonmentRecord],
    human_actor_id: str,
    human_request_id: str,
    assessment_purpose: str,
    abandon_predecessor: bool,
) -> PostFinalAssessmentRunInput:
    """Build the strict next-run authorization from verified read inputs only."""

    predecessor = series[-1] if series else None
    predecessor_result = (
        results.get(predecessor.assessment_request_id)
        if predecessor is not None
        else None
    )
    predecessor_abandonment = (
        abandonments.get(predecessor.assessment_request_id)
        if predecessor is not None
        else None
    )
    if predecessor is None:
        if abandon_predecessor:
            raise PostFinalAssessmentError("post_final_assessment_predecessor_conflict")
    elif predecessor_result is None and predecessor_abandonment is None:
        if not abandon_predecessor:
            raise PostFinalAssessmentError(
                "post_final_assessment_predecessor_outcome_unknown"
            )
    elif abandon_predecessor:
        raise PostFinalAssessmentError("post_final_assessment_predecessor_conflict")

    lineage = finalized_lineage_fingerprint(facts, action)
    payload: dict[str, object] = {
        "schema_version": POST_FINAL_ASSESSMENT_RUN_SCHEMA,
        "human_actor_id": human_actor_id,
        "human_request_id": human_request_id,
        "expected_store_revision": facts.store_revision,
        "finalized_lineage_fingerprint": lineage,
        "assessment_generation": len(series) + 1,
        "assessment_purpose": assessment_purpose,
        "predecessor_assessment_request_id": (
            predecessor.assessment_request_id if predecessor else None
        ),
        "predecessor_assessment_request_fingerprint": (
            predecessor.request_fingerprint if predecessor else None
        ),
        "predecessor_assessment_result_id": (
            predecessor_result.assessment_result_id if predecessor_result else None
        ),
        "predecessor_result_fingerprint": (
            predecessor_result.result_fingerprint if predecessor_result else None
        ),
        "predecessor_abandonment_id": (
            predecessor_abandonment.abandonment_id if predecessor_abandonment else None
        ),
        "predecessor_abandonment_fingerprint": (
            predecessor_abandonment.abandonment_fingerprint
            if predecessor_abandonment
            else None
        ),
        "abandon_predecessor": abandon_predecessor,
        "policy_revision_id": policy.policy_revision_id,
        "policy_fingerprint": policy.policy_fingerprint,
        "public_safe_egress_attested": policy.public_safe_egress_attested,
        "max_provider_calls": policy.max_provider_calls,
        "max_total_input_tokens": policy.max_total_input_tokens,
        "max_total_output_tokens": policy.max_total_output_tokens,
        "max_output_tokens_per_call": policy.max_output_tokens_per_call,
    }
    try:
        return PostFinalAssessmentRunInput.model_validate(payload, strict=True)
    except (ValidationError, TypeError, ValueError) as exc:
        raise PostFinalAssessmentError("post_final_assessment_request_invalid") from exc


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _id(prefix: str, value: object) -> str:
    return f"{prefix}-{_canonical_sha256(value)[:24]}"


def _record_fingerprint(payload: Mapping[str, object], field: str) -> str:
    canonical = dict(payload)
    canonical.pop(field, None)
    return _canonical_sha256(canonical)


def post_final_assessment_archive_root(workspace: str | Path) -> Path:
    """Derive the sole local evidence root without making it product authority.

    The frozen evaluator admission boundary rejects a root inside the declared
    BriefLoop workspace. This package-owned sibling location is therefore
    derived only from the resolved workspace placement; callers cannot select
    it and the resulting local path is never recorded in Store evidence.
    """

    resolved_workspace = Path(workspace).expanduser().resolve()
    workspace_identity = _canonical_sha256(
        {
            "schema": "briefloop.post_final_assessment_archive_root.v1",
            "workspace_path": str(resolved_workspace),
        }
    )
    return resolved_workspace.parent / _ARCHIVE_DIRECTORY / workspace_identity


def _event(
    *,
    run_id: str,
    event_id: str,
    event_type: str,
    transaction_id: str,
    decision: str,
    metadata: Mapping[str, object],
) -> EventEnvelope:
    return EventEnvelope.model_validate(
        {
            "schema_version": EventEnvelope.schema_id,
            "event_id": event_id,
            "run_id": run_id,
            "event_type": event_type,
            "created_at": _utc_now(),
            "actor": "cli",
            "transaction_id": transaction_id,
            "decision": decision,
            "reason": event_type,
            "metadata": dict(metadata),
        },
        strict=True,
    )


def _bounded_context_from_direction(binding: Any, *, run_id: str) -> BoundedContext:
    direction = binding.run_direction
    rows: list[tuple[str, str, str, str]] = [
        (
            "objective",
            "must_answer",
            direction.task_objective,
            "run_direction.objective",
        ),
        ("audience", "audience_need", direction.audience, "run_direction.audience"),
    ]
    rows.extend(
        (f"focus-{index}", "must_include", value, f"run_direction.focus.{index}")
        for index, value in enumerate(direction.focus_areas, start=1)
    )
    rows.extend(
        (
            f"excluded-{index}",
            "scope_excluded",
            value,
            f"run_direction.excluded.{index}",
        )
        for index, value in enumerate(direction.excluded_topics, start=1)
    )
    rows.extend(
        (f"term-{index}", "scope_included", value, f"run_direction.term.{index}")
        for index, value in enumerate(direction.target_terms, start=1)
    )
    requirements = [
        BoundedRequirement.model_validate(
            {
                "requirement_id": f"pf-laj-{name}",
                "type": requirement_type,
                "text": text,
                "source_locator": source_locator,
            },
            strict=True,
        )
        for name, requirement_type, text, source_locator in rows
    ]
    return freeze_bounded_context(
        context_id=_id("pf-laj-context", {"run_id": run_id, "rows": rows}),
        data_class="public",
        requirements=requirements,
    )


def _terminal_class(view: Any) -> str:
    if view.status == "available":
        return "available"
    if view.status == "abstained":
        return "abstained"
    reasons = set(view.reason_codes)
    if any("incomplete" in item or "truncat" in item for item in reasons):
        return "incomplete"
    if any("refus" in item for item in reasons):
        return "refused"
    if reasons:
        return "provider_failed"
    return "unavailable"


def _require_current_finalized_action(
    facts: Any,
    action: CoreRunNextAction,
) -> CoreRunNextAction:
    """Require the current verified Core action to be exact finalized-local."""

    if (
        facts.terminal_state != "finalized_local"
        or action.run_id != facts.run_id
        or action.store_revision != facts.store_revision
        or action.action_kind != "complete"
        or action.effect_kind != "finalized_local"
        or action.reason_code != "local_finalization_complete"
        or action.stage_id is not None
        or action.role_id is not None
        or action.source_route_id is not None
        or action.source_provider_id is not None
        or action.request_schema_id is not None
        or action.action_fingerprint != facts.terminal_action_fingerprint
    ):
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    return action


def finalized_lineage_fingerprint(
    facts: Any,
    action: CoreRunNextAction,
) -> str:
    """Return the PF-LAJ request-slot identity for immutable finalized facts.

    ``FinalizedLocalReviewFacts.facts_fingerprint`` deliberately includes the
    current Store revision.  This PF-LAJ-owned digest binds only the immutable
    finalized-local lineage and never replaces the strict facts fingerprint.
    """

    _require_current_finalized_action(facts, action)
    try:
        gate_bindings = [
            item.model_dump(mode="json", exclude_unset=False)
            for item in facts.gate_bindings
        ]
        if gate_bindings != sorted(
            gate_bindings,
            key=lambda item: (item["gate_id"], item["evaluation_id"]),
        ):
            raise ValueError("gate bindings are not canonical")
        payload = {
            "schema_version": _FINALIZED_LINEAGE_SCHEMA,
            "workspace_id": facts.workspace_id,
            "run_id": facts.run_id,
            "terminal_state": "finalized_local",
            "terminal_action_kind": "complete",
            "terminal_effect_kind": "finalized_local",
            "terminal_reason_code": "local_finalization_complete",
            "finalization_id": facts.finalization_id,
            "finalization_receipt_id": facts.finalization_receipt_id,
            "finalize_gate_batch_id": facts.finalize_gate_batch_id,
            "gate_bindings": gate_bindings,
            "report": {
                "artifact_id": facts.report.artifact_id,
                "artifact_revision": facts.report.artifact_revision,
                "sha256": facts.report.sha256,
                "media_type": _FINALIZED_REPORT_MEDIA_TYPE,
                "size_bytes": facts.report.size_bytes,
            },
        }
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PostFinalAssessmentError("control_store_integrity_invalid") from exc
    return _canonical_sha256(payload)


def reassessed_facts_fingerprint(
    facts: Any,
    action: CoreRunNextAction,
    *,
    claim_prior_revision: int,
) -> str:
    """Reconstruct the exact facts digest assessed immediately before claim."""

    if (
        type(claim_prior_revision) is not int
        or claim_prior_revision < 1
        or claim_prior_revision > facts.store_revision
    ):
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    _require_current_finalized_action(facts, action)
    try:
        action_payload = action.model_dump(mode="json", exclude_unset=False)
        action_payload["store_revision"] = claim_prior_revision
        action_payload.pop("action_fingerprint", None)
        action_payload["action_fingerprint"] = canonical_fingerprint(action_payload)
        assessed_action = CoreRunNextAction.model_validate(action_payload, strict=True)
        payload = facts.model_dump(mode="json", exclude={"facts_fingerprint"})
        payload["store_revision"] = claim_prior_revision
        payload["terminal_action_fingerprint"] = assessed_action.action_fingerprint
        return type(facts).fingerprint_for(payload)
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise PostFinalAssessmentError("control_store_integrity_invalid") from exc


def _request_has_exact_finalized_bindings(
    request: PostFinalAssessmentRequestRecord,
    facts: Any,
    action: CoreRunNextAction,
) -> bool:
    return (
        request.run_id == facts.run_id
        and request.report_artifact_id == facts.report.artifact_id
        and request.report_revision == facts.report.artifact_revision
        and request.report_sha256 == facts.report.sha256
        and request.finalization_id == facts.finalization_id
        and request.finalization_receipt_id == facts.finalization_receipt_id
        and request.finalize_gate_batch_id == facts.finalize_gate_batch_id
        and request.finalized_lineage_fingerprint
        == finalized_lineage_fingerprint(facts, action)
    )


def _require_advisory_only_receipt_suffix(
    snapshot: Any,
    facts: Any,
    claim_receipt: Any,
) -> None:
    """Reject any post-claim run receipt outside the PF-LAJ advisory family."""

    suffix = sorted(
        (
            item
            for item in snapshot.transactions
            if item.run_id == facts.run_id
            and item.committed_revision > claim_receipt.prior_revision
        ),
        key=lambda item: item.committed_revision,
    )
    if not suffix or suffix[0].transaction_id != claim_receipt.transaction_id:
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    if any(
        item.transaction_type not in _POST_FINAL_ASSESSMENT_RECEIPT_TYPES
        for item in suffix
    ):
        raise PostFinalAssessmentError("control_store_integrity_invalid")


def resolve_post_final_assessment_series(
    history: Any,
    snapshot: Any,
    facts: Any,
    action: CoreRunNextAction,
) -> tuple[PostFinalAssessmentRequestRecord, ...]:
    """Resolve and reverify the complete request series for one stable lineage."""

    lineage = finalized_lineage_fingerprint(facts, action)
    requests = list(snapshot.post_final_assessment_requests)
    matches = [
        item for item in requests if item.finalized_lineage_fingerprint == lineage
    ]
    if not matches:
        if requests:
            raise PostFinalAssessmentError("post_final_assessment_stale")
        return ()
    matches.sort(key=lambda item: item.assessment_generation)
    if [item.assessment_generation for item in matches] != list(
        range(1, len(matches) + 1)
    ):
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    results_by_request = {
        item.assessment_request_id: item
        for item in snapshot.post_final_assessment_results
        if item.finalized_lineage_fingerprint == lineage
    }
    abandonments_by_request = {
        item.assessment_request_id: item
        for item in snapshot.post_final_assessment_abandonments
        if item.finalized_lineage_fingerprint == lineage
    }
    if set(results_by_request) & set(abandonments_by_request):
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    for index, request in enumerate(matches):
        if not _request_has_exact_finalized_bindings(request, facts, action):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        receipts = [
            item
            for item in snapshot.transactions
            if item.transaction_id == request.accepted_transaction_id
        ]
        if len(receipts) != 1:
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        receipt = receipts[0]
        references = [
            item
            for item in receipt.post_final_assessment_requests
            if item.assessment_request_id == request.assessment_request_id
        ]
        if (
            receipt.run_id != request.run_id
            or receipt.transaction_type
            not in {
                "post_final_assessment_claim",
                "post_final_assessment_series_claim",
            }
            or receipt.prior_revision + 1 != receipt.committed_revision
            or len(receipt.post_final_assessment_requests) != 1
            or len(references) != 1
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        _require_advisory_only_receipt_suffix(snapshot, facts, receipt)
        if (
            reassessed_facts_fingerprint(
                facts,
                action,
                claim_prior_revision=receipt.prior_revision,
            )
            != request.finalized_facts_fingerprint
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        try:
            claim_snapshot = history.snapshot_at_revision(
                request.run_id,
                receipt.committed_revision,
            )
        except Exception as exc:
            raise PostFinalAssessmentError("control_store_integrity_invalid") from exc
        if not any(
            item.assessment_request_id == request.assessment_request_id
            and item.request_fingerprint == request.request_fingerprint
            for item in claim_snapshot.post_final_assessment_requests
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        if index == 0:
            if request.predecessor_assessment_request_id is not None:
                raise PostFinalAssessmentError("control_store_integrity_invalid")
            continue
        predecessor = matches[index - 1]
        result = results_by_request.get(predecessor.assessment_request_id)
        abandonment = abandonments_by_request.get(predecessor.assessment_request_id)
        if (
            request.predecessor_assessment_request_id
            != predecessor.assessment_request_id
            or request.predecessor_assessment_request_fingerprint
            != predecessor.request_fingerprint
            or (result is None) == (abandonment is None)
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        if result is not None and (
            request.predecessor_assessment_result_id != result.assessment_result_id
            or request.predecessor_result_fingerprint != result.result_fingerprint
            or request.predecessor_abandonment_id is not None
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        if abandonment is not None and (
            request.predecessor_abandonment_id != abandonment.abandonment_id
            or request.predecessor_abandonment_fingerprint
            != abandonment.abandonment_fingerprint
            or request.predecessor_assessment_result_id is not None
        ):
            raise PostFinalAssessmentError("control_store_integrity_invalid")
    return tuple(matches)


def resolve_current_post_final_assessment_request(
    history: Any,
    snapshot: Any,
    facts: Any,
    action: CoreRunNextAction,
) -> PostFinalAssessmentRequestRecord | None:
    """Resolve generation one only; automatic observation never advances a series."""

    series = resolve_post_final_assessment_series(
        history,
        snapshot,
        facts,
        action,
    )
    return None if not series else series[0]


def resolve_post_final_assessment_request_by_id(
    history: Any,
    snapshot: Any,
    facts: Any,
    action: CoreRunNextAction,
    assessment_request_id: str,
) -> PostFinalAssessmentRequestRecord:
    """Resolve one explicit request without any implicit head/latest selection."""

    matches = [
        item
        for item in resolve_post_final_assessment_series(
            history, snapshot, facts, action
        )
        if item.assessment_request_id == assessment_request_id
    ]
    if len(matches) != 1:
        raise PostFinalAssessmentError("assessment_request_not_found")
    return matches[0]


def resolve_current_post_final_assessment_result(
    snapshot: Any,
    request: PostFinalAssessmentRequestRecord,
) -> PostFinalAssessmentResultRecord | None:
    """Resolve one exact Store-qualified result without touching its archive."""

    matches = [
        item
        for item in snapshot.post_final_assessment_results
        if item.assessment_request_id == request.assessment_request_id
    ]
    if len(matches) > 1:
        raise PostFinalAssessmentError("control_store_integrity_invalid")
    if any(
        item.assessment_request_id == request.assessment_request_id
        for item in snapshot.post_final_assessment_abandonments
    ):
        if matches:
            raise PostFinalAssessmentError("control_store_integrity_invalid")
        return None
    if not matches:
        return None
    result = matches[0]
    if (
        result.policy_revision_id != request.policy_revision_id
        or result.finalized_facts_fingerprint != request.finalized_facts_fingerprint
        or result.finalized_lineage_fingerprint != request.finalized_lineage_fingerprint
        or result.result_fingerprint
        != _record_fingerprint(
            result.model_dump(mode="json", warnings="error"),
            "result_fingerprint",
        )
    ):
        raise PostFinalAssessmentError("post_final_assessment_binding_invalid")
    return result


def _resolve_current_post_final_assessment_policy(
    snapshot: Any,
    run_id: str,
) -> PostFinalAssessmentPolicyRevision | None:
    """Return the one current policy by receipt order, never wall-clock ties."""

    policies = [
        item
        for item in snapshot.post_final_assessment_policy_revisions
        if item.run_id == run_id
    ]
    if not policies:
        return None
    receipts = {item.transaction_id: item for item in snapshot.transactions}
    ordered: list[tuple[int, PostFinalAssessmentPolicyRevision]] = []
    try:
        for policy in policies:
            receipt = receipts.get(policy.accepted_transaction_id)
            if (
                receipt is None
                or receipt.run_id != run_id
                or receipt.transaction_type != "post_final_assessment_policy"
                or receipt.prior_revision + 1 != receipt.committed_revision
            ):
                raise ValueError("policy receipt is invalid")
            ordered.append((receipt.committed_revision, policy))
        ordered.sort(key=lambda item: item[0])
        if len({revision for revision, _policy in ordered}) != len(ordered):
            raise ValueError("policy receipts are not unique")
        previous_policy_id: str | None = None
        for _revision, policy in ordered:
            if policy.previous_policy_revision_id != previous_policy_id:
                raise ValueError("policy chain is not append-only")
            previous_policy_id = policy.policy_revision_id
    except (AttributeError, TypeError, ValueError) as exc:
        raise PostFinalAssessmentError("control_store_integrity_invalid") from exc
    return ordered[-1][1]


def resolve_current_post_final_assessment_policy(
    snapshot: Any,
    facts: Any,
) -> PostFinalAssessmentPolicyRevision | None:
    """Return the current policy for the finalized-facts run."""

    try:
        run_id = facts.run_id
    except AttributeError as exc:
        raise PostFinalAssessmentError("control_store_integrity_invalid") from exc
    return _resolve_current_post_final_assessment_policy(snapshot, run_id)


class PostFinalAssessmentService:
    """The only product-facing Store coordinator for PF-LAJ-1."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        adapter_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self._adapter_factory = adapter_factory

    @property
    def _database_path(self) -> Path:
        return self.workspace / "briefloop.db"

    @property
    def _archive_root(self) -> Path:
        return post_final_assessment_archive_root(self.workspace)

    def _load(self) -> tuple[Any, Any, Any, Any, Any, CoreRunNextAction]:
        try:
            facts = build_finalized_local_review_projection(self.workspace).facts
            with SQLiteControlStore.open(self._database_path) as store:
                history = store.load_history()
                verified = CoreRunDomainVerifier().verify_loaded_history(
                    history,
                    facts.run_id,
                )
                action = _require_current_finalized_action(
                    facts,
                    classify_core_run_next_action(verified),
                )
                snapshot = history.snapshot_at_revision(
                    facts.run_id,
                    history.store_revision,
                )
                if len(snapshot.run_contract_bindings) != 1:
                    raise PostFinalAssessmentError("control_store_integrity_invalid")
                if facts.store_revision != snapshot.store_revision:
                    raise PostFinalAssessmentError("control_store_integrity_invalid")
                return (
                    facts,
                    snapshot,
                    snapshot.run_contract_bindings[0],
                    store.workspace_id,
                    history,
                    action,
                )
        except (
            ControlStoreError,
            CoreRunError,
            RuntimeHostError,
            OSError,
            ValueError,
        ) as exc:
            raise PostFinalAssessmentError("post_final_assessment_unavailable") from exc

    def _load_policy_context(self) -> tuple[str, Any, Any]:
        """Load the verified current initialized run for a Human policy write."""

        try:
            with SQLiteControlStore.open(self._database_path) as store:
                history = store.load_history()
                heads = {
                    (
                        item.workspace_run_head.workspace_id,
                        item.workspace_run_head.current_run_id,
                    )
                    for item in history.snapshots
                    if item.workspace_run_head is not None
                }
                if len(heads) != 1:
                    raise PostFinalAssessmentError("control_store_integrity_invalid")
                workspace_id, run_id = next(iter(heads))
                if workspace_id != store.workspace_id:
                    raise PostFinalAssessmentError("control_store_integrity_invalid")
                verified = CoreRunDomainVerifier().verify_loaded_history(
                    history, run_id
                )
                snapshot = verified.snapshot
                head = snapshot.workspace_run_head
                if (
                    snapshot.store_revision != history.store_revision
                    or snapshot.workspace_id != workspace_id
                    or head is None
                    or head.workspace_id != workspace_id
                    or head.current_run_id != run_id
                    or len(snapshot.run_contract_bindings) != 1
                    or snapshot.run_contract_bindings[0] != verified.binding
                ):
                    raise PostFinalAssessmentError("control_store_integrity_invalid")
                return run_id, snapshot, verified.binding
        except (
            ControlStoreError,
            CoreRunError,
            RuntimeHostError,
            OSError,
            ValueError,
        ) as exc:
            raise PostFinalAssessmentError("post_final_assessment_unavailable") from exc

    @staticmethod
    def _policy_for_facts(
        snapshot: Any, facts: Any
    ) -> PostFinalAssessmentPolicyRevision | None:
        return resolve_current_post_final_assessment_policy(snapshot, facts)

    @staticmethod
    def _policy_for_run(
        snapshot: Any, run_id: str
    ) -> PostFinalAssessmentPolicyRevision | None:
        return _resolve_current_post_final_assessment_policy(snapshot, run_id)

    @staticmethod
    def _request_for_facts(
        history: Any,
        snapshot: Any,
        facts: Any,
        action: CoreRunNextAction,
    ) -> PostFinalAssessmentRequestRecord | None:
        return resolve_current_post_final_assessment_request(
            history,
            snapshot,
            facts,
            action,
        )

    @staticmethod
    def _series_for_facts(
        history: Any,
        snapshot: Any,
        facts: Any,
        action: CoreRunNextAction,
    ) -> tuple[PostFinalAssessmentRequestRecord, ...]:
        return resolve_post_final_assessment_series(
            history,
            snapshot,
            facts,
            action,
        )

    @staticmethod
    def _request_by_id(
        history: Any,
        snapshot: Any,
        facts: Any,
        action: CoreRunNextAction,
        assessment_request_id: str,
    ) -> PostFinalAssessmentRequestRecord:
        return resolve_post_final_assessment_request_by_id(
            history,
            snapshot,
            facts,
            action,
            assessment_request_id,
        )

    @staticmethod
    def _policy_by_id(
        snapshot: Any,
        run_id: str,
        policy_revision_id: str,
        policy_fingerprint: str,
    ) -> PostFinalAssessmentPolicyRevision:
        matches = [
            item
            for item in snapshot.post_final_assessment_policy_revisions
            if item.run_id == run_id
            and item.policy_revision_id == policy_revision_id
            and item.policy_fingerprint == policy_fingerprint
        ]
        if len(matches) != 1:
            raise PostFinalAssessmentError("post_final_assessment_policy_conflict")
        return matches[0]

    @staticmethod
    def _validate_run_input(
        value: Mapping[str, object],
    ) -> PostFinalAssessmentRunInput:
        try:
            request = PostFinalAssessmentRunInput.model_validate(value, strict=True)
        except (TypeError, ValidationError, ValueError) as exc:
            raise PostFinalAssessmentError(
                "post_final_assessment_request_invalid"
            ) from exc
        result_pair = (
            request.predecessor_assessment_result_id,
            request.predecessor_result_fingerprint,
        )
        abandonment_pair = (
            request.predecessor_abandonment_id,
            request.predecessor_abandonment_fingerprint,
        )
        invalid_common = (
            request.schema_version != POST_FINAL_ASSESSMENT_RUN_SCHEMA
            or request.expected_store_revision < 0
            or request.assessment_generation < 1
            or request.assessment_purpose
            not in {"post_final_review", "model_evaluation"}
            or request.max_provider_calls < 1
            or request.max_total_input_tokens < 1
            or request.max_total_output_tokens < 1
            or request.max_output_tokens_per_call < 1
            or request.max_output_tokens_per_call > request.max_total_output_tokens
            or not request.public_safe_egress_attested
        )
        predecessor_values = (
            request.predecessor_assessment_request_id,
            request.predecessor_assessment_request_fingerprint,
            *result_pair,
            *abandonment_pair,
        )
        if request.assessment_generation == 1:
            invalid_predecessor = request.abandon_predecessor or any(
                value is not None for value in predecessor_values
            )
        else:
            invalid_predecessor = (
                request.predecessor_assessment_request_id is None
                or request.predecessor_assessment_request_fingerprint is None
                or (
                    request.abandon_predecessor
                    and any(
                        value is not None for value in (*result_pair, *abandonment_pair)
                    )
                )
                or (
                    not request.abandon_predecessor
                    and (all(value is not None for value in result_pair))
                    == (all(value is not None for value in abandonment_pair))
                )
                or (
                    any(value is None for value in result_pair)
                    and any(value is not None for value in result_pair)
                )
                or (
                    any(value is None for value in abandonment_pair)
                    and any(value is not None for value in abandonment_pair)
                )
            )
        if invalid_common or invalid_predecessor:
            raise PostFinalAssessmentError("post_final_assessment_request_invalid")
        return request

    def _validate_policy_input(
        self, value: Mapping[str, object]
    ) -> tuple[PostFinalAssessmentPolicyInput, InstrumentConfig]:
        try:
            request = PostFinalAssessmentPolicyInput.model_validate(value, strict=True)
            if request.schema_version != POST_FINAL_ASSESSMENT_POLICY_SCHEMA:
                raise ValueError
            endpoint = canonical_messages_endpoint_v1(request.messages_endpoint)
            if endpoint != request.messages_endpoint:
                raise ValueError
            config = InstrumentConfig.model_validate(
                request.instrument_config, strict=True
            )
        except (TypeError, ValidationError, ValueError) as exc:
            raise PostFinalAssessmentError(
                "post_final_assessment_policy_invalid"
            ) from exc
        if (
            config.provider_id != ANTHROPIC_PROVIDER_ID
            or config.model_id != request.requested_model_id
            or config.model_version != request.model_version
            or request.expected_model_identity != config.model_version
            or config.decoding.temperature != 1.0
            or config.decoding.top_p != 1.0
            or config.decoding.seed is not None
            or config.transport_policy.model_tools is not False
            or request.max_provider_calls < 1
            or request.max_total_input_tokens < 1
            or request.max_total_output_tokens < 1
            or request.max_output_tokens_per_call < 1
            or request.max_output_tokens_per_call > request.max_total_output_tokens
            or (request.enabled and not request.public_safe_egress_attested)
        ):
            raise PostFinalAssessmentError("post_final_assessment_policy_invalid")
        return request, config

    def policy_set(self, value: Mapping[str, object]) -> dict[str, object]:
        """Append or exactly replay one strict Human policy revision; no provider."""

        request, config = self._validate_policy_input(value)
        run_id, snapshot, binding = self._load_policy_context()
        context = _bounded_context_from_direction(binding, run_id=run_id)
        existing = next(
            (
                item
                for item in snapshot.post_final_assessment_policy_revisions
                if item.human_request_id == request.human_request_id
            ),
            None,
        )
        semantic = {
            "enabled": request.enabled,
            "auto_run": request.auto_run,
            "auto_open": request.auto_open,
            "messages_endpoint": request.messages_endpoint,
            "requested_model_id": request.requested_model_id,
            "model_version": request.model_version,
            "expected_model_identity": request.expected_model_identity,
            "instrument_config": config.model_dump(mode="json"),
            "bounded_context": context.model_dump(mode="json"),
            "max_provider_calls": request.max_provider_calls,
            "max_total_input_tokens": request.max_total_input_tokens,
            "max_total_output_tokens": request.max_total_output_tokens,
            "max_output_tokens_per_call": request.max_output_tokens_per_call,
            "public_safe_egress_attested": request.public_safe_egress_attested,
        }
        if existing is not None:
            existing_semantic = {
                "enabled": existing.enabled,
                "auto_run": existing.auto_run,
                "auto_open": existing.auto_open,
                "messages_endpoint": existing.messages_endpoint,
                "requested_model_id": existing.requested_model_id,
                "model_version": existing.model_version,
                "expected_model_identity": existing.expected_model_identity,
                "instrument_config": existing.instrument_config,
                "bounded_context": existing.bounded_context,
                "max_provider_calls": existing.max_provider_calls,
                "max_total_input_tokens": existing.max_total_input_tokens,
                "max_total_output_tokens": existing.max_total_output_tokens,
                "max_output_tokens_per_call": existing.max_output_tokens_per_call,
                "public_safe_egress_attested": existing.public_safe_egress_attested,
            }
            if existing_semantic != semantic:
                raise PostFinalAssessmentError("post_final_assessment_policy_conflict")
            receipt = next(
                item
                for item in snapshot.transactions
                if item.transaction_id == existing.accepted_transaction_id
            )
            return {
                "ok": True,
                "replayed": True,
                "policy_revision_id": existing.policy_revision_id,
                "receipt_id": receipt.transaction_id,
            }
        identity = {
            "run_id": run_id,
            "human_request_id": request.human_request_id,
            **semantic,
        }
        policy_revision_id = _id("pf-laj-policy", identity)
        transaction_id = _id("pf-laj-policy-tx", identity)
        event_id = _id("pf-laj-policy-event", identity)
        payload: dict[str, object] = {
            "schema_version": PostFinalAssessmentPolicyRevision.schema_id,
            "policy_revision_id": policy_revision_id,
            "run_id": run_id,
            "previous_policy_revision_id": (
                self._policy_for_run(snapshot, run_id).policy_revision_id
                if self._policy_for_run(snapshot, run_id) is not None
                else None
            ),
            **semantic,
            "adapter_id": ANTHROPIC_ADAPTER_ID,
            "messages_endpoint_sha256": hashlib.sha256(
                request.messages_endpoint.encode("utf-8")
            ).hexdigest(),
            "profile_id": "research_design_report_zh_v1",
            "instrument_config_sha256": _canonical_sha256(
                config.model_dump(mode="json")
            ),
            "bounded_context_sha256": context.context_sha256,
            "temperature": 1.0,
            "top_p": 1.0,
            "wall_timeout_seconds": 60,
            "egress_scope": "public_safe_report",
            "human_actor_id": request.human_actor_id,
            "human_request_id": request.human_request_id,
            "recorded_at": _utc_now(),
            "policy_event_id": event_id,
            "accepted_transaction_id": transaction_id,
        }
        payload["policy_fingerprint"] = _record_fingerprint(
            payload, "policy_fingerprint"
        )
        policy = PostFinalAssessmentPolicyRevision.model_validate(payload, strict=True)
        event = _event(
            run_id=run_id,
            event_id=event_id,
            event_type="post_final_assessment_policy_recorded",
            transaction_id=transaction_id,
            decision=policy_revision_id,
            metadata={"policy_fingerprint": policy.policy_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                with store.begin(
                    run_id,
                    transaction_id,
                    "post_final_assessment_policy",
                    store.current_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_assessment_policy_revision(policy)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise PostFinalAssessmentError(str(exc)) from exc
        return {
            "ok": True,
            "replayed": False,
            "policy_revision_id": policy.policy_revision_id,
            "receipt_id": receipt.transaction_id,
        }

    def status(self) -> dict[str, object]:
        """Read only policy/request/result status.  No credential or archive access."""

        try:
            facts, snapshot, _binding, _workspace_id, history, action = self._load()
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "unavailable", "reason_code": str(exc)}
        policy = self._policy_for_facts(snapshot, facts)
        try:
            request = self._request_for_facts(history, snapshot, facts, action)
        except PostFinalAssessmentError as exc:
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": str(exc),
            }
        try:
            result = (
                resolve_current_post_final_assessment_result(snapshot, request)
                if request is not None
                else None
            )
        except PostFinalAssessmentError as exc:
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": str(exc),
            }
        status = (
            "not_requested"
            if policy is None or not policy.enabled
            else "available"
            if result is not None and result.terminal_evidence_class == "available"
            else "unavailable"
            if result is not None
            else "pending"
            if request is not None
            else "not_requested"
        )
        return {
            "ok": True,
            "status": status,
            "facts_fingerprint": facts.facts_fingerprint,
            "policy_revision_id": policy.policy_revision_id if policy else None,
            "assessment_request_id": request.assessment_request_id if request else None,
            "assessment_result_id": result.assessment_result_id if result else None,
            "reason_codes": result.reason_codes if result else [],
        }

    def assess(self, *, allow_first_execution: bool = True) -> dict[str, object]:
        """Claim at most one request, then run/replay it outside the Store UoW."""

        try:
            facts, snapshot, binding, _workspace_id, history, action = self._load()
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "unavailable", "reason_code": str(exc)}
        policy = self._policy_for_facts(snapshot, facts)
        if policy is None or not policy.enabled:
            return {
                "ok": False,
                "status": "not_requested",
                "reason_code": "policy_not_enabled",
            }
        try:
            existing = self._request_for_facts(history, snapshot, facts, action)
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        if existing is not None:
            try:
                policy = self._policy_by_id(
                    snapshot,
                    facts.run_id,
                    existing.policy_revision_id,
                    existing.policy_fingerprint,
                )
            except PostFinalAssessmentError as exc:
                return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        context = _bounded_context_from_direction(binding, run_id=facts.run_id)
        if (
            policy.bounded_context_sha256 != context.context_sha256
            or policy.bounded_context != context.model_dump(mode="json")
        ):
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": "post_final_assessment_policy_conflict",
            }
        if existing is not None:
            try:
                stored_result = resolve_current_post_final_assessment_result(
                    snapshot,
                    existing,
                )
            except PostFinalAssessmentError as exc:
                return {"ok": False, "status": "invalid", "reason_code": str(exc)}
            if stored_result is not None:
                if self._result_has_zero_advice(stored_result):
                    return self._stored_result_replay(stored_result)
                return self._qualify_archive(
                    facts,
                    existing,
                    str(trial_archive_path(self._archive_root, existing.trial_id)),
                )
        try:
            config = InstrumentConfig.model_validate(
                policy.instrument_config, strict=True
            )
            prepared = prepare_shadow_run_from_bytes(
                report_bytes=facts.report.markdown_utf8,
                bounded_context=context,
                instrument_config=config,
                trial_id=_id(
                    "pf-laj-trial",
                    {
                        "lineage": finalized_lineage_fingerprint(facts, action),
                        "policy": policy.policy_fingerprint,
                    },
                ),
                archive_root=self._archive_root,
                workspace_root=self.workspace,
                messages_endpoint=policy.messages_endpoint,
            )
        except (SemanticEvaluatorError, ValidationError, ValueError) as exc:
            return {
                "ok": False,
                "status": "unavailable",
                "reason_code": "preflight_invalid",
            }
        if isinstance(prepared, ShadowRunResult):
            return {
                "ok": False,
                "status": "unavailable",
                "reason_codes": list(prepared.reason_codes),
            }
        budget = prepared_shadow_budget(prepared)
        if (
            budget.provider_call_ceiling > policy.max_provider_calls
            or budget.total_input_token_upper_bound > policy.max_total_input_tokens
            or budget.total_output_token_upper_bound > policy.max_total_output_tokens
            or budget.per_call_output_token_cap > policy.max_output_tokens_per_call
        ):
            return {
                "ok": False,
                "status": "budget_blocked",
                "reason_code": "budget_exceeded",
            }
        if existing is not None:
            return self._recover_existing(prepared, facts, existing)
        if not allow_first_execution:
            return {
                "ok": True,
                "status": "not_requested",
                "reason_code": "auto_run_disabled",
            }
        try:
            capability_profile(self.workspace)
        except CoreRunError as exc:
            return {"ok": False, "status": "unavailable", "reason_code": str(exc)}
        claim = self._claim_request(
            facts,
            policy,
            prepared,
            budget,
            finalized_lineage_fingerprint(facts, action),
        )
        if isinstance(claim, dict):
            return claim
        result = execute_prepared_shadow_run(
            prepared, adapter_factory=self._adapter_factory
        )
        if not result.archive_complete or result.archive_path is None:
            return {
                "ok": False,
                "status": "pending",
                "assessment_request_id": claim.assessment_request_id,
            }
        return self._qualify_archive(facts, claim, result.archive_path)

    def assessment_list(self) -> dict[str, object]:
        """Return the verified series in deterministic generation order."""

        try:
            facts, snapshot, _binding, _workspace_id, history, action = self._load()
            series = self._series_for_facts(
                history,
                snapshot,
                facts,
                action,
            )
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        results = {
            item.assessment_request_id: item
            for item in snapshot.post_final_assessment_results
        }
        abandonments = {
            item.assessment_request_id: item
            for item in snapshot.post_final_assessment_abandonments
        }
        return {
            "ok": True,
            "status": "available",
            "finalized_lineage_fingerprint": finalized_lineage_fingerprint(
                facts, action
            ),
            "assessments": [
                {
                    "assessment_generation": item.assessment_generation,
                    "assessment_request_id": item.assessment_request_id,
                    "assessment_purpose": item.assessment_purpose,
                    "policy_revision_id": item.policy_revision_id,
                    "requested_model_id": item.requested_model_id,
                    "expected_model_identity": item.expected_model_identity,
                    "assessment_result_id": (
                        results[item.assessment_request_id].assessment_result_id
                        if item.assessment_request_id in results
                        else None
                    ),
                    "assessment_result_fingerprint": (
                        results[item.assessment_request_id].result_fingerprint
                        if item.assessment_request_id in results
                        else None
                    ),
                    "terminal_evidence_class": (
                        results[item.assessment_request_id].terminal_evidence_class
                        if item.assessment_request_id in results
                        else "abandoned"
                        if item.assessment_request_id in abandonments
                        else "outcome_unknown"
                    ),
                    "abandonment_id": (
                        abandonments[item.assessment_request_id].abandonment_id
                        if item.assessment_request_id in abandonments
                        else None
                    ),
                }
                for item in series
            ],
        }

    def assessment_next(
        self,
        *,
        policy_revision_id: str,
        human_actor_id: str,
        human_request_id: str,
        assessment_purpose: str,
        abandon_predecessor: bool = False,
    ) -> dict[str, object]:
        """Project one complete next-run authorization without writing state."""

        try:
            facts, snapshot, _binding, _workspace_id, history, action = self._load()
            series = self._series_for_facts(history, snapshot, facts, action)
            current_policy = self._policy_for_facts(snapshot, facts)
            if (
                current_policy is None
                or current_policy.policy_revision_id != policy_revision_id
            ):
                raise PostFinalAssessmentError("post_final_assessment_policy_conflict")
            policy = self._policy_by_id(
                snapshot,
                facts.run_id,
                policy_revision_id,
                current_policy.policy_fingerprint,
            )
            context = _bounded_context_from_direction(
                _binding,
                run_id=facts.run_id,
            )
            if (
                not policy.enabled
                or not policy.public_safe_egress_attested
                or policy.bounded_context_sha256 != context.context_sha256
                or policy.bounded_context != context.model_dump(mode="json")
            ):
                raise PostFinalAssessmentError("post_final_assessment_policy_conflict")
            results = {
                item.assessment_request_id: item
                for item in snapshot.post_final_assessment_results
            }
            abandonments = {
                item.assessment_request_id: item
                for item in snapshot.post_final_assessment_abandonments
            }
            command = _build_next_assessment_command(
                facts=facts,
                action=action,
                policy=policy,
                series=series,
                results=results,
                abandonments=abandonments,
                human_actor_id=human_actor_id,
                human_request_id=human_request_id,
                assessment_purpose=assessment_purpose,
                abandon_predecessor=abandon_predecessor,
            )
        except PostFinalAssessmentError as exc:
            return {
                "ok": False,
                "status": "needs_human"
                if str(exc) == "post_final_assessment_predecessor_outcome_unknown"
                else "invalid",
                "reason_code": str(exc),
            }
        request = command.model_dump(mode="json")
        return {
            "ok": True,
            "status": "ready",
            "request": request,
            "request_fingerprint": _canonical_sha256(request),
            "finalized_lineage_fingerprint": command.finalized_lineage_fingerprint,
            "assessment_generation": command.assessment_generation,
            "store_revision": command.expected_store_revision,
            "policy_revision_id": command.policy_revision_id,
            "boundary": "read_only_human_authorization_projection",
        }

    def assessment_run(self, value: Mapping[str, object]) -> dict[str, object]:
        """Execute one new explicitly Human-authorized assessment generation."""

        try:
            command = self._validate_run_input(value)
            authorization_fingerprint = _canonical_sha256(
                command.model_dump(mode="json")
            )
            facts, snapshot, binding, _workspace_id, history, action = self._load()
            series = self._series_for_facts(
                history,
                snapshot,
                facts,
                action,
            )
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        existing = next(
            (
                item
                for item in series
                if item.human_request_id == command.human_request_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.authorization_fingerprint != authorization_fingerprint
                or existing.human_actor_id != command.human_actor_id
            ):
                return {
                    "ok": False,
                    "status": "conflict",
                    "reason_code": "assessment_human_request_conflict",
                }
            return self.retry(existing.assessment_request_id)
        if (
            facts.store_revision != command.expected_store_revision
            or command.finalized_lineage_fingerprint
            != finalized_lineage_fingerprint(facts, action)
            or command.assessment_generation != len(series) + 1
        ):
            return {
                "ok": False,
                "status": "conflict",
                "reason_code": "assessment_series_conflict",
            }
        predecessor = series[-1] if series else None
        if predecessor is None and (
            command.predecessor_assessment_request_id is not None
            or command.predecessor_assessment_request_fingerprint is not None
            or command.predecessor_assessment_result_id is not None
            or command.predecessor_result_fingerprint is not None
            or command.predecessor_abandonment_id is not None
            or command.predecessor_abandonment_fingerprint is not None
            or command.abandon_predecessor
        ):
            return {
                "ok": False,
                "status": "conflict",
                "reason_code": "assessment_predecessor_conflict",
            }
        if predecessor is not None and (
            command.predecessor_assessment_request_id
            != predecessor.assessment_request_id
            or command.predecessor_assessment_request_fingerprint
            != predecessor.request_fingerprint
        ):
            return {
                "ok": False,
                "status": "conflict",
                "reason_code": "assessment_predecessor_conflict",
            }
        predecessor_result = next(
            (
                item
                for item in snapshot.post_final_assessment_results
                if predecessor is not None
                and item.assessment_request_id == predecessor.assessment_request_id
            ),
            None,
        )
        predecessor_abandonment = next(
            (
                item
                for item in snapshot.post_final_assessment_abandonments
                if predecessor is not None
                and item.assessment_request_id == predecessor.assessment_request_id
            ),
            None,
        )
        create_abandonment = False
        if predecessor is None:
            pass
        elif predecessor_result is not None:
            if (
                command.predecessor_assessment_result_id
                != predecessor_result.assessment_result_id
                or command.predecessor_result_fingerprint
                != predecessor_result.result_fingerprint
                or command.abandon_predecessor
            ):
                return {
                    "ok": False,
                    "status": "conflict",
                    "reason_code": "assessment_predecessor_conflict",
                }
            verified = self._qualify_archive(
                facts,
                predecessor,
                str(
                    trial_archive_path(
                        self._archive_root,
                        predecessor.trial_id,
                    )
                ),
            )
            if not verified.get("ok"):
                return verified
        elif predecessor_abandonment is not None:
            if (
                command.predecessor_abandonment_id
                != predecessor_abandonment.abandonment_id
                or command.predecessor_abandonment_fingerprint
                != predecessor_abandonment.abandonment_fingerprint
                or command.abandon_predecessor
            ):
                return {
                    "ok": False,
                    "status": "conflict",
                    "reason_code": "assessment_predecessor_conflict",
                }
        elif not command.abandon_predecessor:
            return {
                "ok": False,
                "status": "needs_human",
                "reason_code": "assessment_predecessor_outcome_unknown",
            }
        else:
            replay = self.retry(predecessor.assessment_request_id)
            if replay.get("ok"):
                return {
                    **replay,
                    "status": "predecessor_recovered",
                    "reason_code": "assessment_predecessor_result_available",
                }
            create_abandonment = True
        try:
            policy = self._policy_by_id(
                snapshot,
                facts.run_id,
                command.policy_revision_id,
                command.policy_fingerprint,
            )
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        if (
            not policy.enabled
            or not policy.public_safe_egress_attested
            or not command.public_safe_egress_attested
            or (
                command.max_provider_calls,
                command.max_total_input_tokens,
                command.max_total_output_tokens,
                command.max_output_tokens_per_call,
            )
            != (
                policy.max_provider_calls,
                policy.max_total_input_tokens,
                policy.max_total_output_tokens,
                policy.max_output_tokens_per_call,
            )
        ):
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": "post_final_assessment_policy_conflict",
            }
        context = _bounded_context_from_direction(binding, run_id=facts.run_id)
        if (
            policy.bounded_context_sha256 != context.context_sha256
            or policy.bounded_context != context.model_dump(mode="json")
        ):
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": "post_final_assessment_policy_conflict",
            }
        trial_id = _id(
            "pf-laj-trial",
            {
                "lineage": command.finalized_lineage_fingerprint,
                "generation": command.assessment_generation,
                "authorization": authorization_fingerprint,
                "policy": policy.policy_fingerprint,
            },
        )
        try:
            config = InstrumentConfig.model_validate(
                policy.instrument_config,
                strict=True,
            )
            prepared = prepare_shadow_run_from_bytes(
                report_bytes=facts.report.markdown_utf8,
                bounded_context=context,
                instrument_config=config,
                trial_id=trial_id,
                archive_root=self._archive_root,
                workspace_root=self.workspace,
                messages_endpoint=policy.messages_endpoint,
            )
        except (SemanticEvaluatorError, ValidationError, ValueError):
            return {
                "ok": False,
                "status": "unavailable",
                "reason_code": "preflight_invalid",
            }
        if isinstance(prepared, ShadowRunResult):
            return {
                "ok": False,
                "status": "unavailable",
                "reason_codes": list(prepared.reason_codes),
            }
        budget = prepared_shadow_budget(prepared)
        if (
            budget.provider_call_ceiling > command.max_provider_calls
            or budget.total_input_token_upper_bound > command.max_total_input_tokens
            or budget.total_output_token_upper_bound > command.max_total_output_tokens
            or budget.per_call_output_token_cap > command.max_output_tokens_per_call
        ):
            return {
                "ok": False,
                "status": "budget_blocked",
                "reason_code": "budget_exceeded",
            }
        try:
            capability_profile(self.workspace)
        except CoreRunError as exc:
            return {"ok": False, "status": "unavailable", "reason_code": str(exc)}
        claim = self._claim_series_request(
            facts=facts,
            policy=policy,
            prepared=prepared,
            budget=budget,
            command=command,
            authorization_fingerprint=authorization_fingerprint,
            predecessor=predecessor,
            predecessor_result=predecessor_result,
            predecessor_abandonment=predecessor_abandonment,
            create_abandonment=create_abandonment,
        )
        if isinstance(claim, dict):
            return claim
        result = execute_prepared_shadow_run(
            prepared,
            adapter_factory=self._adapter_factory,
        )
        if not result.archive_complete or result.archive_path is None:
            return {
                "ok": False,
                "status": "pending",
                "assessment_request_id": claim.assessment_request_id,
            }
        return self._qualify_archive(facts, claim, result.archive_path)

    def retry(self, assessment_request_id: str) -> dict[str, object]:
        """Recovery-only archive qualification; never makes a paid provider call."""

        try:
            facts, snapshot, binding, _workspace_id, history, action = self._load()
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "unavailable", "reason_code": str(exc)}
        try:
            request = self._request_by_id(
                history,
                snapshot,
                facts,
                action,
                assessment_request_id,
            )
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        try:
            policy = self._policy_by_id(
                snapshot,
                facts.run_id,
                request.policy_revision_id,
                request.policy_fingerprint,
            )
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        context = _bounded_context_from_direction(binding, run_id=facts.run_id)
        if (
            policy.bounded_context_sha256 != context.context_sha256
            or policy.bounded_context != context.model_dump(mode="json")
        ):
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": "post_final_assessment_policy_conflict",
            }
        try:
            stored_result = resolve_current_post_final_assessment_result(
                snapshot,
                request,
            )
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        if stored_result is not None:
            # Same rule as assess(): a zero-advice result carries no evidence to
            # bind, so it replays directly. Anything else must be qualified
            # against the archive on disk, or a different self-valid archive
            # would replay a result bound to evidence it does not contain.
            if self._result_has_zero_advice(stored_result):
                return self._stored_result_replay(stored_result)
            return self._qualify_archive(
                facts,
                request,
                str(trial_archive_path(self._archive_root, request.trial_id)),
            )
        if any(
            item.assessment_request_id == request.assessment_request_id
            for item in snapshot.post_final_assessment_abandonments
        ):
            return {
                "ok": False,
                "status": "abandoned",
                "reason_code": "assessment_request_abandoned",
            }
        config = InstrumentConfig.model_validate(policy.instrument_config, strict=True)
        prepared = prepare_shadow_run_from_bytes(
            report_bytes=facts.report.markdown_utf8,
            bounded_context=context,
            instrument_config=config,
            trial_id=request.trial_id,
            archive_root=self._archive_root,
            workspace_root=self.workspace,
            messages_endpoint=policy.messages_endpoint,
        )
        if isinstance(prepared, ShadowRunResult):
            return {
                "ok": False,
                "status": "invalid",
                "reason_codes": list(prepared.reason_codes),
            }
        return self._recover_existing(prepared, facts, request)

    def observe_finalized_local(self) -> dict[str, object]:
        """Best-effort post-commit observer; Store replay prevents redial."""

        status = self.status()
        if not status.get("ok") or status.get("status") != "not_requested":
            return status
        try:
            facts, snapshot, _binding, _workspace_id, _history, _action = self._load()
        except PostFinalAssessmentError:
            return status
        policy = self._policy_for_facts(snapshot, facts)
        if policy is None or not policy.enabled or not policy.auto_run:
            return status
        return self.assess(allow_first_execution=True)

    def _claim_request(
        self,
        facts: Any,
        policy: PostFinalAssessmentPolicyRevision,
        prepared: PreparedShadowRun,
        budget: Any,
        lineage: str,
    ) -> PostFinalAssessmentRequestRecord | dict[str, object]:
        admission = prepared.admission
        identity = {
            "facts": facts.facts_fingerprint,
            "lineage": lineage,
            "policy": policy.policy_fingerprint,
            "trial": prepared.trial_id,
            "input": admission.input_binding.input_binding_sha256,
            "prompts": list(admission.prompt_request_sha256s),
            "budget": budget.__dict__,
        }
        request_id = _id("pf-laj-request", identity)
        transaction_id = _id("pf-laj-request-tx", identity)
        event_id = _id("pf-laj-request-event", identity)
        payload: dict[str, object] = {
            "schema_version": PostFinalAssessmentRequestRecord.schema_id,
            "assessment_request_id": request_id,
            "run_id": facts.run_id,
            "finalized_facts_fingerprint": facts.facts_fingerprint,
            "finalized_lineage_fingerprint": lineage,
            "report_artifact_id": facts.report.artifact_id,
            "report_revision": facts.report.artifact_revision,
            "report_sha256": facts.report.sha256,
            "finalization_id": facts.finalization_id,
            "finalization_receipt_id": facts.finalization_receipt_id,
            "finalize_gate_batch_id": facts.finalize_gate_batch_id,
            "policy_revision_id": policy.policy_revision_id,
            "policy_fingerprint": policy.policy_fingerprint,
            "adapter_id": ANTHROPIC_ADAPTER_ID,
            "messages_endpoint_sha256": policy.messages_endpoint_sha256,
            "requested_model_id": policy.requested_model_id,
            "expected_model_identity": policy.expected_model_identity,
            "profile_id": policy.profile_id,
            "instrument_config_sha256": policy.instrument_config_sha256,
            "bounded_context_sha256": policy.bounded_context_sha256,
            "input_binding_sha256": admission.input_binding.input_binding_sha256,
            "assessment_plan_sha256": admission.assessment_plan.assessment_plan_sha256,
            "ordered_prompt_request_sha256s": list(admission.prompt_request_sha256s),
            "prompt_count": budget.prompt_count,
            "provider_call_ceiling": budget.provider_call_ceiling,
            "total_input_token_upper_bound": budget.total_input_token_upper_bound,
            "total_output_token_upper_bound": budget.total_output_token_upper_bound,
            "output_tokens_per_call": budget.per_call_output_token_cap,
            "trial_id": prepared.trial_id,
            "archive_identity_sha256": _canonical_sha256(
                {"root": _ARCHIVE_DIRECTORY, "trial_id": prepared.trial_id}
            ),
            "request_status": "claimed",
            "claimed_at": _utc_now(),
            "request_event_id": event_id,
            "accepted_transaction_id": transaction_id,
        }
        payload["request_fingerprint"] = _record_fingerprint(
            payload, "request_fingerprint"
        )
        request = PostFinalAssessmentRequestRecord.model_validate(payload, strict=True)
        event = _event(
            run_id=facts.run_id,
            event_id=event_id,
            event_type="post_final_assessment_claimed",
            transaction_id=transaction_id,
            decision=request_id,
            metadata={"request_fingerprint": request.request_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                with store.begin(
                    facts.run_id,
                    transaction_id,
                    "post_final_assessment_claim",
                    facts.store_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_assessment_request(request)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            return {"ok": False, "status": "pending", "reason_code": str(exc)}
        if (
            receipt.prior_revision != facts.store_revision
            or receipt.committed_revision != facts.store_revision + 1
        ):
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": "control_store_integrity_invalid",
            }
        return request

    def _claim_series_request(
        self,
        *,
        facts: Any,
        policy: PostFinalAssessmentPolicyRevision,
        prepared: PreparedShadowRun,
        budget: Any,
        command: PostFinalAssessmentRunInput,
        authorization_fingerprint: str,
        predecessor: PostFinalAssessmentRequestRecord | None,
        predecessor_result: PostFinalAssessmentResultRecord | None,
        predecessor_abandonment: PostFinalAssessmentAbandonmentRecord | None,
        create_abandonment: bool,
    ) -> PostFinalAssessmentRequestRecord | dict[str, object]:
        """Atomically close an unknown predecessor and claim one new generation."""

        identity = {
            "lineage": command.finalized_lineage_fingerprint,
            "generation": command.assessment_generation,
            "authorization": authorization_fingerprint,
            "policy": policy.policy_fingerprint,
            "trial": prepared.trial_id,
        }
        request_id = _id("pf-laj-request", identity)
        transaction_id = _id("pf-laj-series-tx", identity)
        request_event_id = _id("pf-laj-request-event", identity)
        abandonment: PostFinalAssessmentAbandonmentRecord | None = None
        abandonment_event: EventEnvelope | None = None
        if create_abandonment:
            abandonment_id = _id(
                "pf-laj-abandonment",
                {
                    "predecessor": (
                        predecessor.request_fingerprint
                        if predecessor is not None
                        else None
                    ),
                    "authorization": authorization_fingerprint,
                },
            )
            abandonment_event_id = _id(
                "pf-laj-abandonment-event",
                {
                    "abandonment": abandonment_id,
                    "transaction": transaction_id,
                },
            )
            if predecessor is None:
                return {
                    "ok": False,
                    "status": "invalid",
                    "reason_code": "assessment_predecessor_conflict",
                }
            abandonment_payload: dict[str, object] = {
                "schema_version": PostFinalAssessmentAbandonmentRecord.schema_id,
                "abandonment_id": abandonment_id,
                "run_id": facts.run_id,
                "assessment_request_id": predecessor.assessment_request_id,
                "assessment_request_fingerprint": predecessor.request_fingerprint,
                "finalized_lineage_fingerprint": (
                    predecessor.finalized_lineage_fingerprint
                ),
                "assessment_generation": predecessor.assessment_generation,
                "reason": "outcome_unknown",
                "human_actor_id": command.human_actor_id,
                "human_request_id": command.human_request_id,
                "expected_store_revision": command.expected_store_revision,
                "recorded_at": _utc_now(),
                "abandonment_event_id": abandonment_event_id,
                "accepted_transaction_id": transaction_id,
            }
            abandonment_payload["abandonment_fingerprint"] = _record_fingerprint(
                abandonment_payload,
                "abandonment_fingerprint",
            )
            abandonment = PostFinalAssessmentAbandonmentRecord.model_validate(
                abandonment_payload,
                strict=True,
            )
            abandonment_event = _event(
                run_id=facts.run_id,
                event_id=abandonment_event_id,
                event_type="post_final_assessment_abandoned",
                transaction_id=transaction_id,
                decision=abandonment.abandonment_id,
                metadata={
                    "abandonment_fingerprint": abandonment.abandonment_fingerprint
                },
            )
            predecessor_abandonment = abandonment
        admission = prepared.admission
        payload: dict[str, object] = {
            "schema_version": PostFinalAssessmentRequestRecord.series_schema_id,
            "assessment_request_id": request_id,
            "run_id": facts.run_id,
            "finalized_facts_fingerprint": facts.facts_fingerprint,
            "finalized_lineage_fingerprint": (command.finalized_lineage_fingerprint),
            "report_artifact_id": facts.report.artifact_id,
            "report_revision": facts.report.artifact_revision,
            "report_sha256": facts.report.sha256,
            "finalization_id": facts.finalization_id,
            "finalization_receipt_id": facts.finalization_receipt_id,
            "finalize_gate_batch_id": facts.finalize_gate_batch_id,
            "policy_revision_id": policy.policy_revision_id,
            "policy_fingerprint": policy.policy_fingerprint,
            "adapter_id": ANTHROPIC_ADAPTER_ID,
            "messages_endpoint_sha256": policy.messages_endpoint_sha256,
            "requested_model_id": policy.requested_model_id,
            "expected_model_identity": policy.expected_model_identity,
            "profile_id": policy.profile_id,
            "instrument_config_sha256": policy.instrument_config_sha256,
            "bounded_context_sha256": policy.bounded_context_sha256,
            "input_binding_sha256": admission.input_binding.input_binding_sha256,
            "assessment_plan_sha256": (
                admission.assessment_plan.assessment_plan_sha256
            ),
            "ordered_prompt_request_sha256s": list(admission.prompt_request_sha256s),
            "prompt_count": budget.prompt_count,
            "provider_call_ceiling": budget.provider_call_ceiling,
            "total_input_token_upper_bound": (budget.total_input_token_upper_bound),
            "total_output_token_upper_bound": (budget.total_output_token_upper_bound),
            "output_tokens_per_call": budget.per_call_output_token_cap,
            "trial_id": prepared.trial_id,
            "archive_identity_sha256": _canonical_sha256(
                {"root": _ARCHIVE_DIRECTORY, "trial_id": prepared.trial_id}
            ),
            "request_status": "claimed",
            "claimed_at": _utc_now(),
            "request_event_id": request_event_id,
            "accepted_transaction_id": transaction_id,
            "assessment_generation": command.assessment_generation,
            "predecessor_assessment_request_id": (
                predecessor.assessment_request_id if predecessor is not None else None
            ),
            "predecessor_assessment_request_fingerprint": (
                predecessor.request_fingerprint if predecessor is not None else None
            ),
            "predecessor_assessment_result_id": (
                predecessor_result.assessment_result_id
                if predecessor_result is not None
                else None
            ),
            "predecessor_result_fingerprint": (
                predecessor_result.result_fingerprint
                if predecessor_result is not None
                else None
            ),
            "predecessor_abandonment_id": (
                predecessor_abandonment.abandonment_id
                if predecessor_abandonment is not None
                else None
            ),
            "predecessor_abandonment_fingerprint": (
                predecessor_abandonment.abandonment_fingerprint
                if predecessor_abandonment is not None
                else None
            ),
            "assessment_purpose": command.assessment_purpose,
            "human_actor_id": command.human_actor_id,
            "human_request_id": command.human_request_id,
            "authorization_fingerprint": authorization_fingerprint,
        }
        payload["request_fingerprint"] = _record_fingerprint(
            payload,
            "request_fingerprint",
        )
        request = PostFinalAssessmentRequestRecord.model_validate(
            payload,
            strict=True,
        )
        request_event = _event(
            run_id=facts.run_id,
            event_id=request_event_id,
            event_type="post_final_assessment_claimed",
            transaction_id=transaction_id,
            decision=request.assessment_request_id,
            metadata={"request_fingerprint": request.request_fingerprint},
        )
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                with store.begin(
                    facts.run_id,
                    transaction_id,
                    "post_final_assessment_series_claim",
                    command.expected_store_revision,
                ) as uow:
                    if abandonment is not None and abandonment_event is not None:
                        uow.append_event(abandonment_event)
                        uow.put_post_final_assessment_abandonment(abandonment)
                    uow.append_event(request_event)
                    uow.put_post_final_assessment_request(request)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            return {
                "ok": False,
                "status": "conflict",
                "reason_code": str(exc),
            }
        if (
            receipt.prior_revision != command.expected_store_revision
            or receipt.committed_revision != command.expected_store_revision + 1
        ):
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": "control_store_integrity_invalid",
            }
        return request

    def _recover_existing(
        self,
        prepared: PreparedShadowRun,
        facts: Any,
        request: PostFinalAssessmentRequestRecord,
    ) -> dict[str, object]:
        try:
            result = execute_prepared_shadow_run(prepared, replay_only=True)
        except Exception:
            return {
                "ok": False,
                "status": "pending",
                "assessment_request_id": request.assessment_request_id,
            }
        if not result.archive_complete or result.archive_path is None:
            if any(
                code
                in {
                    "archive_root_unsafe",
                    "shadow_archive_invalid",
                    "shadow_request_conflict",
                }
                for code in result.reason_codes
            ):
                return {
                    "ok": False,
                    "status": "invalid",
                    "reason_code": "archive_verification_failed",
                }
            return {
                "ok": False,
                "status": "pending",
                "assessment_request_id": request.assessment_request_id,
            }
        return self._qualify_archive(facts, request, result.archive_path)

    def _qualify_archive(
        self, facts: Any, request: PostFinalAssessmentRequestRecord, archive_path: str
    ) -> dict[str, object]:
        try:
            (
                current_facts,
                snapshot,
                _binding,
                _workspace_id,
                history,
                current_action,
            ) = self._load()
            current_request = self._request_by_id(
                history,
                snapshot,
                current_facts,
                current_action,
                request.assessment_request_id,
            )
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        if (
            current_request is None
            or current_request.assessment_request_id != request.assessment_request_id
            or current_request.request_fingerprint != request.request_fingerprint
        ):
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": "post_final_assessment_stale",
            }
        request = current_request
        try:
            policy = self._policy_by_id(
                snapshot,
                current_facts.run_id,
                request.policy_revision_id,
                request.policy_fingerprint,
            )
        except PostFinalAssessmentError:
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": "post_final_assessment_policy_conflict",
            }
        if any(
            item.assessment_request_id == request.assessment_request_id
            for item in snapshot.post_final_assessment_abandonments
        ):
            return {
                "ok": False,
                "status": "abandoned",
                "reason_code": "assessment_request_abandoned",
            }
        try:
            existing = resolve_current_post_final_assessment_result(snapshot, request)
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        try:
            archive = verify_shadow_archive(Path(archive_path))
            view = build_laj_reader_view(
                archive.path,
                expected_report_sha256=current_facts.report.sha256,
            )
            if view.binding is None or view.binding.trial_id != request.trial_id:
                raise SemanticEvaluatorError("shadow_request_conflict")
        except (SemanticEvaluatorError, OSError, ValueError):
            return {
                "ok": False,
                "status": "invalid",
                "reason_code": "archive_verification_failed",
            }
        if existing is not None:
            if not self._result_matches_verified_evidence(
                existing,
                request,
                archive,
                view,
            ):
                return {
                    "ok": False,
                    "status": "invalid",
                    "reason_code": "post_final_assessment_binding_invalid",
                }
            return {
                "ok": True,
                "replayed": True,
                "status": existing.terminal_evidence_class,
                "assessment_result_id": existing.assessment_result_id,
                "assessment_result_fingerprint": existing.result_fingerprint,
            }
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                identity = {
                    "request": request.request_fingerprint,
                    "archive": archive.archive_manifest.archive_manifest_sha256,
                    "view": view.view_sha256,
                }
                result_id = _id("pf-laj-result", identity)
                transaction_id = _id("pf-laj-result-tx", identity)
                event_id = _id("pf-laj-result-event", identity)
                payload: dict[str, object] = {
                    "schema_version": PostFinalAssessmentResultRecord.schema_id,
                    "assessment_result_id": result_id,
                    "run_id": facts.run_id,
                    "assessment_request_id": request.assessment_request_id,
                    "policy_revision_id": request.policy_revision_id,
                    "finalized_facts_fingerprint": request.finalized_facts_fingerprint,
                    "finalized_lineage_fingerprint": request.finalized_lineage_fingerprint,
                    "terminal_evidence_class": _terminal_class(view),
                    "reason_codes": sorted(set(view.reason_codes)),
                    "shadow_request_sha256": archive.request.shadow_request_sha256,
                    "execution_manifest_sha256": archive.execution_manifest.execution_sha256,
                    "archive_manifest_sha256": archive.archive_manifest.archive_manifest_sha256,
                    "archive_receipt_id": archive.receipt.receipt_id,
                    "composition_sha256": archive.presentation.composition_sha256,
                    "presentation_sha256": archive.presentation.presentation_sha256,
                    "reader_view_sha256": view.view_sha256,
                    "assessed_unit_count": view.assessed_unit_count,
                    "finding_count": view.finding_count,
                    "withheld_finding_count": view.withheld_finding_count,
                    "abstention_count": view.abstention_count,
                    "recorded_at": _utc_now(),
                    "result_event_id": event_id,
                    "accepted_transaction_id": transaction_id,
                }
                payload["result_fingerprint"] = _record_fingerprint(
                    payload, "result_fingerprint"
                )
                record = PostFinalAssessmentResultRecord.model_validate(
                    payload, strict=True
                )
                event = _event(
                    run_id=facts.run_id,
                    event_id=event_id,
                    event_type="post_final_assessment_result_recorded",
                    transaction_id=transaction_id,
                    decision=result_id,
                    metadata={"result_fingerprint": record.result_fingerprint},
                )
                with store.begin(
                    current_facts.run_id,
                    transaction_id,
                    "post_final_assessment_result",
                    current_facts.store_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_assessment_result(record)
                    uow.commit()
        except ControlStoreError as exc:
            return {"ok": False, "status": "pending", "reason_code": str(exc)}
        presentation: dict[str, object] | None = None
        if policy.auto_open:
            try:
                from multi_agent_brief.product.review_session import (
                    launch_actionable_review_session,
                )

                launched = launch_actionable_review_session(
                    self.workspace,
                    assessment_result_id=record.assessment_result_id,
                    assessment_result_fingerprint=record.result_fingerprint,
                    open_browser=True,
                )
                presentation = {
                    "status": launched.reason_code,
                    "browser_opened": launched.browser_opened,
                    "runtime_authority": False,
                }
            except Exception:
                presentation = {
                    "status": "browser_unavailable",
                    "reason_code": "post_final_review_session_unavailable",
                }
        return {
            "ok": True,
            "replayed": False,
            "status": record.terminal_evidence_class,
            "assessment_result_id": record.assessment_result_id,
            "assessment_result_fingerprint": record.result_fingerprint,
            "finding_count": record.finding_count,
            "presentation": presentation,
        }

    @staticmethod
    def _result_has_zero_advice(result: PostFinalAssessmentResultRecord) -> bool:
        return result.finding_count == 0 and result.withheld_finding_count == 0

    @staticmethod
    def _stored_result_replay(
        result: PostFinalAssessmentResultRecord,
    ) -> dict[str, object]:
        return {
            "ok": True,
            "replayed": True,
            "status": result.terminal_evidence_class,
            "assessment_result_id": result.assessment_result_id,
            "assessment_result_fingerprint": result.result_fingerprint,
        }

    @staticmethod
    def _result_matches_verified_evidence(
        result: PostFinalAssessmentResultRecord,
        request: PostFinalAssessmentRequestRecord,
        archive: Any,
        view: Any,
    ) -> bool:
        """Require replay evidence to match the exact persisted qualification."""

        return (
            result.run_id == request.run_id
            and result.assessment_request_id == request.assessment_request_id
            and result.policy_revision_id == request.policy_revision_id
            and result.finalized_facts_fingerprint
            == request.finalized_facts_fingerprint
            and result.finalized_lineage_fingerprint
            == request.finalized_lineage_fingerprint
            and result.shadow_request_sha256 == archive.request.shadow_request_sha256
            and result.execution_manifest_sha256
            == archive.execution_manifest.execution_sha256
            and result.archive_manifest_sha256
            == archive.archive_manifest.archive_manifest_sha256
            and result.archive_receipt_id == archive.receipt.receipt_id
            and result.composition_sha256 == archive.presentation.composition_sha256
            and result.presentation_sha256 == archive.presentation.presentation_sha256
            and result.reader_view_sha256 == view.view_sha256
            and result.terminal_evidence_class == _terminal_class(view)
            and result.reason_codes == sorted(set(view.reason_codes))
            and result.assessed_unit_count == view.assessed_unit_count
            and result.finding_count == view.finding_count
            and result.withheld_finding_count == view.withheld_finding_count
            and result.abstention_count == view.abstention_count
        )


__all__ = [
    "POST_FINAL_ASSESSMENT_POLICY_SCHEMA",
    "POST_FINAL_ASSESSMENT_RUN_SCHEMA",
    "PostFinalAssessmentError",
    "PostFinalAssessmentPolicyInput",
    "PostFinalAssessmentRunInput",
    "PostFinalAssessmentService",
    "post_final_assessment_archive_root",
    "resolve_current_post_final_assessment_policy",
    "resolve_current_post_final_assessment_result",
    "resolve_post_final_assessment_request_by_id",
    "resolve_post_final_assessment_series",
]
