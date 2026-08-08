"""Store-qualified, post-final LAJ assessment orchestration.

The evaluator archive remains evidence only.  This module is the sole product
coordinator for the non-secret policy, one request claim, and one qualified
advisory result.  It never opens SQLite directly and never lets browser state,
raw findings, or a provider response affect Core truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from pydantic import TypeAdapter, ValidationError

from multi_agent_brief.contracts.v2 import (
    ContractId,
    CoreRunNextAction,
    EventEnvelope,
    PostFinalAssessmentAbandonmentRecord,
    PostFinalAssessmentPolicyRevision,
    PostFinalAssessmentRequestRecord,
    PostFinalAssessmentResultRecord,
    ReaderReviewAssessmentInput,
    StrictModel,
    derive_reader_review_result_status,
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
from multi_agent_brief.product.post_final_assessment_read_model import (
    PostFinalAssessmentError,
    _ARCHIVE_DIRECTORY,
    _canonical_sha256,
    _require_current_finalized_action,
    _record_fingerprint,
    _resolve_current_post_final_assessment_policy,
    finalized_lineage_fingerprint,
    post_final_assessment_archive_root,
    reassessed_facts_fingerprint,
    resolve_current_post_final_assessment_policy,
    resolve_current_post_final_assessment_request,
    resolve_current_post_final_assessment_result,
    resolve_post_final_assessment_request_by_id,
    resolve_post_final_assessment_series,
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
from multi_agent_brief.semantic_evaluator.parser import PARSER_VERSION
from multi_agent_brief.semantic_evaluator.profile import READER_REVIEW_PROFILE_ID
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


POST_FINAL_ASSESSMENT_POLICY_SCHEMA = "briefloop.post_final_assessment_policy_set.v1"
POST_FINAL_ASSESSMENT_RUN_SCHEMA = "briefloop.post_final_assessment_run.v1"
READER_REVIEW_ASSESSMENT_KIND = "reader_review"
READER_REVIEW_REPORT_TYPE = "management_monthly"
READER_REVIEW_LANGUAGE = "en"
READER_REVIEW_PROJECTION_VERSION = "reader_review_projection_v1"
READER_REVIEW_MAX_PROVIDER_CALLS = 2
READER_REVIEW_MAX_TOTAL_INPUT_TOKENS = 400_000
READER_REVIEW_MAX_TOTAL_OUTPUT_TOKENS = 8_192
READER_REVIEW_MAX_OUTPUT_TOKENS_PER_CALL = 4_096


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
    assessment_kind: Optional[str] = None
    report_type: Optional[str] = None
    language: Optional[str] = None
    disclosure_confirmed: Optional[bool] = None
    cost_status: Optional[str] = None


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
    reader_review_authorization_fingerprint: Optional[str] = None


@dataclass(frozen=True)
class _AssessmentReadiness:
    """The shared, provider-free admission result for one new assessment."""

    policy: PostFinalAssessmentPolicyRevision
    context: BoundedContext
    prepared: PreparedShadowRun
    budget: Any
    predecessor: PostFinalAssessmentRequestRecord | None
    predecessor_result: PostFinalAssessmentResultRecord | None
    predecessor_abandonment: PostFinalAssessmentAbandonmentRecord | None
    create_abandonment: bool


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
    reader_review_authorization_fingerprint: str | None = None,
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
        "reader_review_authorization_fingerprint": (
            reader_review_authorization_fingerprint
        ),
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


def _id(prefix: str, value: object) -> str:
    return f"{prefix}-{_canonical_sha256(value)[:24]}"


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


def _bounded_context_from_direction(
    binding: Any,
    *,
    run_id: str,
    language: str = "zh-CN",
) -> BoundedContext:
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
        language=language,
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


def _require_reader_review_direction(binding: Any) -> None:
    """Require the frozen RunDirection supported by Reader Review v3.

    Reader Review is an exact profile admission, not a request-field hint.  A
    caller can enter through ``policy_set`` or the generic assessment command
    APIs, so every such path must consult the same frozen direction before it
    is allowed to prepare or recover evaluator evidence.
    """

    try:
        direction = binding.run_direction
        supported = (
            direction.report_type == READER_REVIEW_REPORT_TYPE
            and direction.output_language == READER_REVIEW_LANGUAGE
        )
    except AttributeError:
        supported = False
    if not supported:
        raise PostFinalAssessmentError("reader_review_not_supported")


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
        try:
            contract_id = TypeAdapter(ContractId)
            contract_id.validate_python(request.human_actor_id, strict=True)
            contract_id.validate_python(request.human_request_id, strict=True)
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
            or (
                request.reader_review_authorization_fingerprint is not None
                and (
                    len(request.reader_review_authorization_fingerprint) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in request.reader_review_authorization_fingerprint
                    )
                )
            )
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

    def _admit_assessment(
        self,
        *,
        command: PostFinalAssessmentRunInput,
        facts: Any,
        snapshot: Any,
        binding: Any,
        history: Any,
        action: CoreRunNextAction,
        series: tuple[PostFinalAssessmentRequestRecord, ...],
        authorization_fingerprint: str,
    ) -> _AssessmentReadiness:
        """Run every deterministic, provider-free assessment admission check.

        ``assessment_next`` and ``assessment_run`` both use this boundary so a
        request is never advertised as ready if execution would reject it
        before claiming its Store transaction or touching a provider.
        """

        command = self._validate_run_input(command.model_dump(mode="json"))
        if any(item.human_request_id == command.human_request_id for item in series):
            raise PostFinalAssessmentError("assessment_human_request_conflict")
        policy = self._policy_by_id(
            snapshot,
            facts.run_id,
            command.policy_revision_id,
            command.policy_fingerprint,
        )
        reader_review = (
            policy.schema_version
            == PostFinalAssessmentPolicyRevision.reader_review_schema_id
        )
        if reader_review:
            # This must precede every predecessor archive probe below.  The
            # frozen direction, not a request/profile field, is the authority
            # for Reader Review admission.
            _require_reader_review_direction(binding)
        if (
            facts.store_revision != command.expected_store_revision
            or command.finalized_lineage_fingerprint
            != finalized_lineage_fingerprint(facts, action)
            or command.assessment_generation != len(series) + 1
        ):
            raise PostFinalAssessmentError("assessment_series_conflict")

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
            raise PostFinalAssessmentError("assessment_predecessor_conflict")
        if predecessor is not None and (
            command.predecessor_assessment_request_id
            != predecessor.assessment_request_id
            or command.predecessor_assessment_request_fingerprint
            != predecessor.request_fingerprint
        ):
            raise PostFinalAssessmentError("assessment_predecessor_conflict")

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
                raise PostFinalAssessmentError("assessment_predecessor_conflict")
            # Generic legacy zero-advice results have no evidence-bearing
            # archive to reopen. Reader Review results remain archive-bound,
            # including a completed run with zero findings.
            if not (
                self._result_has_zero_advice(predecessor_result)
                and not self._is_reader_review_result(predecessor_result)
            ):
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
                    raise PostFinalAssessmentError(
                        str(
                            verified.get(
                                "reason_code",
                                "post_final_assessment_binding_invalid",
                            )
                        )
                    )
        elif predecessor_abandonment is not None:
            if (
                command.predecessor_abandonment_id
                != predecessor_abandonment.abandonment_id
                or command.predecessor_abandonment_fingerprint
                != predecessor_abandonment.abandonment_fingerprint
                or command.abandon_predecessor
            ):
                raise PostFinalAssessmentError("assessment_predecessor_conflict")
        elif not command.abandon_predecessor:
            raise PostFinalAssessmentError(
                "post_final_assessment_predecessor_outcome_unknown"
            )
        else:
            # A complete, intrinsically valid archive is still authoritative
            # recovery evidence even though the predecessor result receipt is
            # absent.  Probe it read-only here so ``assessment_next`` cannot
            # advertise an abandonment request that ``assessment_run`` would
            # immediately recover instead of claiming.
            if self._archive_recovery_available(
                facts,
                snapshot,
                binding,
                predecessor,
            ):
                raise PostFinalAssessmentError(
                    "assessment_predecessor_result_available"
                )
            create_abandonment = True

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
            raise PostFinalAssessmentError("post_final_assessment_policy_conflict")
        if (
            policy.schema_version
            == PostFinalAssessmentPolicyRevision.reader_review_schema_id
            and command.reader_review_authorization_fingerprint is None
        ):
            raise PostFinalAssessmentError("reader_review_authorization_invalid")
        context = _bounded_context_from_direction(
            binding,
            run_id=facts.run_id,
            language=policy.language or "zh-CN",
        )
        if (
            policy.bounded_context_sha256 != context.context_sha256
            or policy.bounded_context != context.model_dump(mode="json")
        ):
            raise PostFinalAssessmentError("post_final_assessment_policy_conflict")

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
                profile_id=policy.profile_id,
            )
        except (SemanticEvaluatorError, ValidationError, ValueError) as exc:
            raise PostFinalAssessmentError("preflight_invalid") from exc
        if isinstance(prepared, ShadowRunResult):
            reason = (
                prepared.reason_codes[0]
                if prepared.reason_codes
                else "preflight_invalid"
            )
            raise PostFinalAssessmentError(str(reason))
        budget = prepared_shadow_budget(prepared)
        if (
            budget.provider_call_ceiling > command.max_provider_calls
            or budget.total_input_token_upper_bound > command.max_total_input_tokens
            or budget.total_output_token_upper_bound > command.max_total_output_tokens
            or budget.per_call_output_token_cap > command.max_output_tokens_per_call
        ):
            raise PostFinalAssessmentError("budget_exceeded")
        try:
            capability_profile(self.workspace)
        except CoreRunError as exc:
            raise PostFinalAssessmentError(str(exc)) from exc
        return _AssessmentReadiness(
            policy=policy,
            context=context,
            prepared=prepared,
            budget=budget,
            predecessor=predecessor,
            predecessor_result=predecessor_result,
            predecessor_abandonment=predecessor_abandonment,
            create_abandonment=create_abandonment,
        )

    def _archive_recovery_available(
        self,
        facts: Any,
        snapshot: Any,
        binding: Any,
        predecessor: PostFinalAssessmentRequestRecord,
    ) -> bool:
        """Return whether retry can recover a missing predecessor result.

        This is deliberately a pure probe.  Result creation remains owned by
        ``retry``/``_qualify_archive``.  Admission reconstructs the same exact
        prepared request/execution identity as retry before treating an
        intrinsically valid archive as recoverable.
        """

        try:
            prepared = self._prepare_request_replay(
                facts=facts,
                snapshot=snapshot,
                binding=binding,
                request=predecessor,
            )
            if isinstance(prepared, ShadowRunResult):
                return False
            replay = execute_prepared_shadow_run(prepared, replay_only=True)
            if not replay.archive_complete or replay.archive_path is None:
                return False
            archive = verify_shadow_archive(Path(replay.archive_path))
            view = build_laj_reader_view(
                archive.path,
                expected_report_sha256=facts.report.sha256,
            )
        except (
            PostFinalAssessmentError,
            SemanticEvaluatorError,
            OSError,
            ValueError,
        ):
            return False
        return (
            view.archive_verified
            and view.binding is not None
            and view.binding.trial_id == predecessor.trial_id
            and archive.request.trial_id == predecessor.trial_id
        )

    def _prepare_request_replay(
        self,
        *,
        facts: Any,
        snapshot: Any,
        binding: Any,
        request: PostFinalAssessmentRequestRecord,
    ) -> PreparedShadowRun | ShadowRunResult:
        """Reconstruct the one immutable replay identity for a Store request."""

        policy = self._policy_by_id(
            snapshot,
            facts.run_id,
            request.policy_revision_id,
            request.policy_fingerprint,
        )
        context = _bounded_context_from_direction(
            binding,
            run_id=facts.run_id,
            language=policy.language or "zh-CN",
        )
        if (
            policy.bounded_context_sha256 != context.context_sha256
            or policy.bounded_context != context.model_dump(mode="json")
        ):
            raise PostFinalAssessmentError("post_final_assessment_policy_conflict")
        config = InstrumentConfig.model_validate(
            policy.instrument_config,
            strict=True,
        )
        return prepare_shadow_run_from_bytes(
            report_bytes=facts.report.markdown_utf8,
            bounded_context=context,
            instrument_config=config,
            trial_id=request.trial_id,
            archive_root=self._archive_root,
            workspace_root=self.workspace,
            messages_endpoint=policy.messages_endpoint,
            profile_id=policy.profile_id,
        )

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
        reader_review = request.assessment_kind is not None
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
            or (
                reader_review
                and (
                    request.assessment_kind != READER_REVIEW_ASSESSMENT_KIND
                    or request.report_type != READER_REVIEW_REPORT_TYPE
                    or request.language != READER_REVIEW_LANGUAGE
                    or request.disclosure_confirmed is not True
                    or request.cost_status != "not_measured"
                    or config.language != READER_REVIEW_LANGUAGE
                    or request.enabled is not True
                    or request.auto_run
                    or request.auto_open
                    or request.max_provider_calls != READER_REVIEW_MAX_PROVIDER_CALLS
                    or request.max_total_input_tokens
                    != READER_REVIEW_MAX_TOTAL_INPUT_TOKENS
                    or request.max_total_output_tokens
                    != READER_REVIEW_MAX_TOTAL_OUTPUT_TOKENS
                    or request.max_output_tokens_per_call
                    != READER_REVIEW_MAX_OUTPUT_TOKENS_PER_CALL
                )
            )
            or (
                not reader_review
                and (
                    any(
                        item is not None
                        for item in (
                            request.report_type,
                            request.language,
                            request.disclosure_confirmed,
                            request.cost_status,
                        )
                    )
                    or config.language != "zh-CN"
                )
            )
        ):
            raise PostFinalAssessmentError("post_final_assessment_policy_invalid")
        return request, config

    @staticmethod
    def _validate_reader_review_input(
        value: Mapping[str, object],
    ) -> ReaderReviewAssessmentInput:
        try:
            request = ReaderReviewAssessmentInput.model_validate(value, strict=True)
            if (
                canonical_messages_endpoint_v1(request.messages_endpoint)
                != request.messages_endpoint
                or request.expected_model_identity != request.model_version
            ):
                raise ValueError("reader_review_request_invalid")
        except (TypeError, ValidationError, ValueError) as exc:
            raise PostFinalAssessmentError("reader_review_request_invalid") from exc
        return request

    @staticmethod
    def _reader_review_instrument(
        request: ReaderReviewAssessmentInput,
    ) -> InstrumentConfig:
        payload = {
            "schema_version": InstrumentConfig.schema_id,
            "instrument_config_id": _id(
                "reader-review-instrument",
                {
                    "model_id": request.requested_model_id,
                    "model_version": request.model_version,
                },
            ),
            "provider_id": ANTHROPIC_PROVIDER_ID,
            "model_id": request.requested_model_id,
            "model_version": request.model_version,
            "language": READER_REVIEW_LANGUAGE,
            "decoding": {
                "temperature": 1.0,
                "top_p": 1.0,
                "max_output_tokens": READER_REVIEW_MAX_OUTPUT_TOKENS_PER_CALL,
                "seed": None,
            },
            "retry_policy": {
                "max_attempts": 1,
                "retryable_reason_codes": [],
                "backoff_schedule_ms": [],
            },
            "prompt_sizer": {
                "sizer_id": "anthropic_utf8_bytes_conservative_v1",
                "sizer_version": "anthropic_utf8_bytes_conservative_v1",
                "max_context_tokens": 200_000,
                "reserved_output_tokens": (READER_REVIEW_MAX_OUTPUT_TOKENS_PER_CALL),
            },
            "transport_policy": {
                "provider_transport_only": True,
                "model_tools": False,
                "browser": False,
                "cross_run_memory": False,
                "provider_file_search": False,
            },
        }
        try:
            return InstrumentConfig.model_validate(payload, strict=True)
        except ValidationError as exc:
            raise PostFinalAssessmentError("reader_review_request_invalid") from exc

    @staticmethod
    def _reader_review_authorization_fingerprint(
        request: ReaderReviewAssessmentInput,
        *,
        facts: Any,
        action: CoreRunNextAction,
    ) -> str:
        return _canonical_sha256(
            {
                "schema_version": "briefloop.reader_review_authorization.v1",
                "command": request.model_dump(mode="json", warnings="error"),
                "finalized_lineage_fingerprint": finalized_lineage_fingerprint(
                    facts, action
                ),
                "report_artifact_id": facts.report.artifact_id,
                "report_revision": facts.report.artifact_revision,
                "report_sha256": facts.report.sha256,
                "assessment_kind": READER_REVIEW_ASSESSMENT_KIND,
                "report_type": READER_REVIEW_REPORT_TYPE,
                "language": READER_REVIEW_LANGUAGE,
                "profile_id": READER_REVIEW_PROFILE_ID,
                "parser_version": PARSER_VERSION,
                "projection_version": READER_REVIEW_PROJECTION_VERSION,
                "max_provider_calls": READER_REVIEW_MAX_PROVIDER_CALLS,
                "max_total_input_tokens": (READER_REVIEW_MAX_TOTAL_INPUT_TOKENS),
                "max_total_output_tokens": (READER_REVIEW_MAX_TOTAL_OUTPUT_TOKENS),
                "max_output_tokens_per_call": (
                    READER_REVIEW_MAX_OUTPUT_TOKENS_PER_CALL
                ),
            }
        )

    def run_reader_review(
        self,
        value: Mapping[str, object],
    ) -> dict[str, object]:
        """Run one exact, disclosed Reader Review; replay never redials."""

        try:
            request = self._validate_reader_review_input(value)
            facts, snapshot, binding, _workspace_id, history, action = self._load()
        except PostFinalAssessmentError as exc:
            return {
                "ok": False,
                "status": "invalid",
                "user_status": "not_assessed",
                "reason_code": str(exc),
            }
        try:
            _require_reader_review_direction(binding)
        except PostFinalAssessmentError as exc:
            return {
                "ok": False,
                "status": "unsupported",
                "user_status": "not_assessed",
                "reason_code": str(exc),
            }
        try:
            series = self._series_for_facts(history, snapshot, facts, action)
        except PostFinalAssessmentError as exc:
            return {
                "ok": False,
                "status": "invalid",
                "user_status": "not_assessed",
                "reason_code": str(exc),
            }
        authorization = self._reader_review_authorization_fingerprint(
            request,
            facts=facts,
            action=action,
        )
        existing = next(
            (
                item
                for item in series
                if item.human_request_id == request.human_request_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.schema_version
                != PostFinalAssessmentRequestRecord.reader_review_schema_id
                or existing.human_actor_id != request.human_actor_id
                or existing.reader_review_authorization_fingerprint != authorization
            ):
                return {
                    "ok": False,
                    "status": "conflict",
                    "user_status": "not_assessed",
                    "reason_code": "reader_review_human_request_conflict",
                }
            stored = next(
                (
                    item
                    for item in snapshot.post_final_assessment_results
                    if item.assessment_request_id == existing.assessment_request_id
                ),
                None,
            )
            if stored is not None:
                return self._qualify_archive(
                    facts,
                    existing,
                    str(trial_archive_path(self._archive_root, existing.trial_id)),
                )
            replay = self.retry(existing.assessment_request_id)
            return {
                **replay,
                "user_status": replay.get(
                    "user_status",
                    (
                        "unable_to_assess"
                        if replay.get("status") == "pending"
                        else "not_assessed"
                    ),
                ),
            }

        if series:
            predecessor = series[-1]
            has_result = any(
                item.assessment_request_id == predecessor.assessment_request_id
                for item in snapshot.post_final_assessment_results
            )
            has_abandonment = any(
                item.assessment_request_id == predecessor.assessment_request_id
                for item in snapshot.post_final_assessment_abandonments
            )
            if not has_result and not has_abandonment:
                return {
                    "ok": False,
                    "status": "needs_human",
                    "user_status": "not_assessed",
                    "reason_code": (
                        "post_final_assessment_predecessor_outcome_unknown"
                    ),
                }

        config = self._reader_review_instrument(request)
        context = _bounded_context_from_direction(
            binding,
            run_id=facts.run_id,
            language=READER_REVIEW_LANGUAGE,
        )
        preflight = prepare_shadow_run_from_bytes(
            report_bytes=facts.report.markdown_utf8,
            bounded_context=context,
            instrument_config=config,
            trial_id=_id("reader-review-preflight", authorization),
            archive_root=self._archive_root,
            workspace_root=self.workspace,
            messages_endpoint=request.messages_endpoint,
            profile_id=READER_REVIEW_PROFILE_ID,
        )
        if isinstance(preflight, ShadowRunResult):
            return {
                "ok": False,
                "status": "invalid",
                "user_status": "not_assessed",
                "reason_code": (
                    preflight.reason_codes[0]
                    if preflight.reason_codes
                    else "preflight_invalid"
                ),
            }
        budget = prepared_shadow_budget(preflight)
        if (
            budget.prompt_count != 2
            or budget.provider_call_ceiling > READER_REVIEW_MAX_PROVIDER_CALLS
            or budget.total_input_token_upper_bound
            > READER_REVIEW_MAX_TOTAL_INPUT_TOKENS
            or budget.total_output_token_upper_bound
            > READER_REVIEW_MAX_TOTAL_OUTPUT_TOKENS
            or budget.per_call_output_token_cap
            > READER_REVIEW_MAX_OUTPUT_TOKENS_PER_CALL
        ):
            return {
                "ok": False,
                "status": "budget_blocked",
                "user_status": "not_assessed",
                "reason_code": "budget_exceeded",
            }
        policy_request_id = _id(
            "reader-review-policy-request",
            {
                "human_request_id": request.human_request_id,
                "authorization": authorization,
            },
        )
        policy_payload = {
            "schema_version": POST_FINAL_ASSESSMENT_POLICY_SCHEMA,
            "human_actor_id": request.human_actor_id,
            "human_request_id": policy_request_id,
            "enabled": True,
            "auto_run": False,
            "auto_open": False,
            "messages_endpoint": request.messages_endpoint,
            "requested_model_id": request.requested_model_id,
            "model_version": request.model_version,
            "expected_model_identity": request.expected_model_identity,
            "instrument_config": config.model_dump(mode="json", warnings="error"),
            "max_provider_calls": READER_REVIEW_MAX_PROVIDER_CALLS,
            "max_total_input_tokens": READER_REVIEW_MAX_TOTAL_INPUT_TOKENS,
            "max_total_output_tokens": READER_REVIEW_MAX_TOTAL_OUTPUT_TOKENS,
            "max_output_tokens_per_call": (READER_REVIEW_MAX_OUTPUT_TOKENS_PER_CALL),
            "public_safe_egress_attested": True,
            "assessment_kind": READER_REVIEW_ASSESSMENT_KIND,
            "report_type": READER_REVIEW_REPORT_TYPE,
            "language": READER_REVIEW_LANGUAGE,
            "disclosure_confirmed": True,
            "cost_status": "not_measured",
        }
        try:
            policy_result = self.policy_set(policy_payload)
            if not policy_result.get("ok"):
                raise PostFinalAssessmentError("post_final_assessment_policy_invalid")
            facts, snapshot, _binding, _workspace_id, history, action = self._load()
            series = self._series_for_facts(history, snapshot, facts, action)
            policy_matches = [
                item
                for item in snapshot.post_final_assessment_policy_revisions
                if item.policy_revision_id == str(policy_result["policy_revision_id"])
            ]
            if len(policy_matches) != 1:
                raise PostFinalAssessmentError("post_final_assessment_policy_conflict")
            policy = policy_matches[0]
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
                human_actor_id=request.human_actor_id,
                human_request_id=request.human_request_id,
                assessment_purpose="post_final_review",
                abandon_predecessor=False,
                reader_review_authorization_fingerprint=authorization,
            )
        except (AttributeError, KeyError, PostFinalAssessmentError) as exc:
            return {
                "ok": False,
                "status": "invalid",
                "user_status": "not_assessed",
                "reason_code": str(exc),
            }
        result = self.assessment_run(command.model_dump(mode="json", warnings="error"))
        if "user_status" not in result:
            result = {
                **result,
                "user_status": (
                    "unable_to_assess"
                    if result.get("status") == "pending"
                    else "not_assessed"
                ),
            }
        return result

    def policy_set(self, value: Mapping[str, object]) -> dict[str, object]:
        """Append or exactly replay one strict Human policy revision; no provider."""

        request, config = self._validate_policy_input(value)
        run_id, snapshot, binding = self._load_policy_context()
        if request.assessment_kind == READER_REVIEW_ASSESSMENT_KIND:
            _require_reader_review_direction(binding)
        context = _bounded_context_from_direction(
            binding,
            run_id=run_id,
            language=config.language,
        )
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
        reader_review = request.assessment_kind == READER_REVIEW_ASSESSMENT_KIND
        if reader_review:
            semantic.update(
                {
                    "assessment_kind": READER_REVIEW_ASSESSMENT_KIND,
                    "report_type": READER_REVIEW_REPORT_TYPE,
                    "language": READER_REVIEW_LANGUAGE,
                    "disclosure_confirmed": True,
                    "cost_status": "not_measured",
                }
            )
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
            if (
                existing.schema_version
                == PostFinalAssessmentPolicyRevision.reader_review_schema_id
            ):
                existing_semantic.update(
                    {
                        "assessment_kind": existing.assessment_kind,
                        "report_type": existing.report_type,
                        "language": existing.language,
                        "disclosure_confirmed": existing.disclosure_confirmed,
                        "cost_status": existing.cost_status,
                    }
                )
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
        if any(item.status == "active" for item in snapshot.invocations):
            raise PostFinalAssessmentError(
                "post_final_assessment_policy_active_invocation"
            )
        identity = {
            "run_id": run_id,
            "human_request_id": request.human_request_id,
            **semantic,
        }
        policy_revision_id = _id("pf-laj-policy", identity)
        transaction_id = _id("pf-laj-policy-tx", identity)
        event_id = _id("pf-laj-policy-event", identity)
        payload: dict[str, object] = {
            "schema_version": (
                PostFinalAssessmentPolicyRevision.reader_review_schema_id
                if reader_review
                else PostFinalAssessmentPolicyRevision.schema_id
            ),
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
            "profile_id": (
                READER_REVIEW_PROFILE_ID
                if reader_review
                else "research_design_report_zh_v1"
            ),
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
                live_snapshot = store.load_snapshot(run_id)
                if any(item.status == "active" for item in live_snapshot.invocations):
                    raise PostFinalAssessmentError(
                        "post_final_assessment_policy_active_invocation"
                    )
                with store.begin(
                    run_id,
                    transaction_id,
                    "post_final_assessment_policy",
                    snapshot.store_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_post_final_assessment_policy_revision(policy)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            if str(exc) == "store_revision_conflict":
                try:
                    _run_id, current_snapshot, _binding = self._load_policy_context()
                except PostFinalAssessmentError:
                    pass
                else:
                    if any(
                        item.status == "active" for item in current_snapshot.invocations
                    ):
                        raise PostFinalAssessmentError(
                            "post_final_assessment_policy_active_invocation"
                        ) from exc
                    loaded_policy = self._policy_for_run(snapshot, run_id)
                    current_policy = self._policy_for_run(
                        current_snapshot,
                        run_id,
                    )
                    if (loaded_policy is None and current_policy is not None) or (
                        loaded_policy is not None
                        and current_policy is not None
                        and loaded_policy.policy_revision_id
                        != current_policy.policy_revision_id
                    ):
                        raise PostFinalAssessmentError(
                            "relational_integrity_conflict"
                        ) from exc
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
        if (
            policy.schema_version
            == PostFinalAssessmentPolicyRevision.reader_review_schema_id
        ):
            try:
                _require_reader_review_direction(binding)
            except PostFinalAssessmentError as exc:
                return {
                    "ok": False,
                    "status": "unsupported",
                    "reason_code": str(exc),
                }
        context = _bounded_context_from_direction(
            binding,
            run_id=facts.run_id,
            language=policy.language or "zh-CN",
        )
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
                if self._result_has_zero_advice(
                    stored_result
                ) and not self._is_reader_review_result(stored_result):
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
                profile_id=policy.profile_id,
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
            results: dict[str, PostFinalAssessmentResultRecord] = {}
            for item in series:
                result = resolve_current_post_final_assessment_result(snapshot, item)
                if result is not None:
                    results[item.assessment_request_id] = result
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
        abandonments = {
            item.assessment_request_id: item
            for item in snapshot.post_final_assessment_abandonments
        }
        assessments: list[dict[str, object]] = []
        for item in series:
            result = results.get(item.assessment_request_id)
            abandonment = abandonments.get(item.assessment_request_id)
            assessments.append(
                {
                    "assessment_generation": item.assessment_generation,
                    "assessment_request_id": item.assessment_request_id,
                    "assessment_purpose": item.assessment_purpose,
                    "policy_revision_id": item.policy_revision_id,
                    "requested_model_id": item.requested_model_id,
                    "expected_model_identity": item.expected_model_identity,
                    "assessment_result_id": (
                        result.assessment_result_id if result is not None else None
                    ),
                    "assessment_result_fingerprint": (
                        result.result_fingerprint if result is not None else None
                    ),
                    "terminal_evidence_class": (
                        result.terminal_evidence_class
                        if result is not None
                        else "abandoned"
                        if abandonment is not None
                        else "outcome_unknown"
                    ),
                    "assessed_unit_count": (
                        result.assessed_unit_count if result is not None else None
                    ),
                    "finding_count": (
                        result.finding_count if result is not None else None
                    ),
                    "withheld_finding_count": (
                        result.withheld_finding_count if result is not None else None
                    ),
                    "abstention_count": (
                        result.abstention_count if result is not None else None
                    ),
                    "reason_codes": (
                        list(result.reason_codes) if result is not None else None
                    ),
                    "abandonment_id": (
                        abandonment.abandonment_id if abandonment is not None else None
                    ),
                }
            )
        return {
            "ok": True,
            "status": "available",
            "finalized_lineage_fingerprint": finalized_lineage_fingerprint(
                facts, action
            ),
            "assessments": assessments,
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
            facts, snapshot, binding, _workspace_id, history, action = self._load()
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
            self._admit_assessment(
                command=command,
                facts=facts,
                snapshot=snapshot,
                binding=binding,
                history=history,
                action=action,
                series=series,
                authorization_fingerprint=_canonical_sha256(
                    command.model_dump(mode="json")
                ),
            )
        except PostFinalAssessmentError as exc:
            return {
                "ok": False,
                "status": "needs_human"
                if str(exc) == "post_final_assessment_predecessor_outcome_unknown"
                else "unsupported"
                if str(exc) == "reader_review_not_supported"
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
        if existing is not None and (
            existing.schema_version
            == PostFinalAssessmentRequestRecord.reader_review_schema_id
        ):
            try:
                _require_reader_review_direction(binding)
            except PostFinalAssessmentError as exc:
                return {
                    "ok": False,
                    "status": "unsupported",
                    "reason_code": str(exc),
                }
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
        try:
            readiness = self._admit_assessment(
                command=command,
                facts=facts,
                snapshot=snapshot,
                binding=binding,
                history=history,
                action=action,
                series=series,
                authorization_fingerprint=authorization_fingerprint,
            )
        except PostFinalAssessmentError as exc:
            reason_code = str(exc)
            if reason_code == "assessment_predecessor_result_available":
                predecessor = series[-1] if series else None
                if predecessor is not None:
                    replay = self.retry(predecessor.assessment_request_id)
                    if replay.get("ok"):
                        return {
                            **replay,
                            "status": "predecessor_recovered",
                            "reason_code": "assessment_predecessor_result_available",
                        }
            status = (
                "needs_human"
                if reason_code == "post_final_assessment_predecessor_outcome_unknown"
                else "conflict"
                if reason_code
                in {
                    "assessment_series_conflict",
                    "assessment_predecessor_conflict",
                    "assessment_human_request_conflict",
                }
                else "budget_blocked"
                if reason_code == "budget_exceeded"
                else "unsupported"
                if reason_code == "reader_review_not_supported"
                else "unavailable"
                if reason_code
                in {"preflight_invalid", "checkout_publication_unsupported"}
                else "invalid"
            )
            return {"ok": False, "status": status, "reason_code": reason_code}

        if readiness.create_abandonment and readiness.predecessor is not None:
            replay = self.retry(readiness.predecessor.assessment_request_id)
            if replay.get("ok"):
                return {
                    **replay,
                    "status": "predecessor_recovered",
                    "reason_code": "assessment_predecessor_result_available",
                }
        claim = self._claim_series_request(
            facts=facts,
            policy=readiness.policy,
            prepared=readiness.prepared,
            budget=readiness.budget,
            command=command,
            authorization_fingerprint=authorization_fingerprint,
            predecessor=readiness.predecessor,
            predecessor_result=readiness.predecessor_result,
            predecessor_abandonment=readiness.predecessor_abandonment,
            create_abandonment=readiness.create_abandonment,
        )
        if isinstance(claim, dict):
            return claim
        result = execute_prepared_shadow_run(
            readiness.prepared,
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
        if (
            policy.schema_version
            == PostFinalAssessmentPolicyRevision.reader_review_schema_id
        ):
            try:
                _require_reader_review_direction(binding)
            except PostFinalAssessmentError as exc:
                return {
                    "ok": False,
                    "status": "unsupported",
                    "reason_code": str(exc),
                }
        context = _bounded_context_from_direction(
            binding,
            run_id=facts.run_id,
            language=policy.language or "zh-CN",
        )
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
            if self._result_has_zero_advice(
                stored_result
            ) and not self._is_reader_review_result(stored_result):
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
        try:
            prepared = self._prepare_request_replay(
                facts=facts,
                snapshot=snapshot,
                binding=binding,
                request=request,
            )
        except PostFinalAssessmentError as exc:
            return {"ok": False, "status": "invalid", "reason_code": str(exc)}
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
        reader_review = (
            policy.schema_version
            == PostFinalAssessmentPolicyRevision.reader_review_schema_id
        )
        payload: dict[str, object] = {
            "schema_version": (
                PostFinalAssessmentRequestRecord.reader_review_schema_id
                if reader_review
                else PostFinalAssessmentRequestRecord.series_schema_id
            ),
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
        if reader_review:
            payload.update(
                {
                    "assessment_kind": READER_REVIEW_ASSESSMENT_KIND,
                    "report_type": READER_REVIEW_REPORT_TYPE,
                    "language": READER_REVIEW_LANGUAGE,
                    "model_version": policy.model_version,
                    "parser_version": PARSER_VERSION,
                    "projection_version": READER_REVIEW_PROJECTION_VERSION,
                    "disclosure_confirmed": True,
                    "public_safe_egress_attested": True,
                    "cost_status": "not_measured",
                    "reader_review_authorization_fingerprint": (
                        command.reader_review_authorization_fingerprint
                    ),
                }
            )
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
                **(
                    {"user_status": existing.reader_review_status}
                    if existing.reader_review_status is not None
                    else {}
                ),
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
                reader_review = (
                    request.schema_version
                    == PostFinalAssessmentRequestRecord.reader_review_schema_id
                )
                payload: dict[str, object] = {
                    "schema_version": (
                        PostFinalAssessmentResultRecord.reader_review_schema_id
                        if reader_review
                        else PostFinalAssessmentResultRecord.schema_id
                    ),
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
                if reader_review:
                    view_payload = view.model_dump(mode="json", warnings="error")
                    requirement_states = [
                        item.state for item in view.requirement_assessments
                    ]
                    payload.update(
                        {
                            "assessment_kind": READER_REVIEW_ASSESSMENT_KIND,
                            "report_type": READER_REVIEW_REPORT_TYPE,
                            "language": READER_REVIEW_LANGUAGE,
                            "profile_id": READER_REVIEW_PROFILE_ID,
                            "model_version": request.model_version,
                            "expected_model_identity": (
                                request.expected_model_identity
                            ),
                            "parser_version": PARSER_VERSION,
                            "projection_version": (READER_REVIEW_PROJECTION_VERSION),
                            "reader_review_status": (
                                derive_reader_review_result_status(
                                    terminal_evidence_class=_terminal_class(view),
                                    assessed_unit_count=view.assessed_unit_count,
                                    finding_count=view.finding_count,
                                    withheld_finding_count=(
                                        view.withheld_finding_count
                                    ),
                                    abstention_count=view.abstention_count,
                                    requirement_states=requirement_states,
                                )
                            ),
                            "reader_view_payload": view_payload,
                        }
                    )
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
            **(
                {"user_status": record.reader_review_status}
                if record.reader_review_status is not None
                else {}
            ),
            "presentation": presentation,
        }

    @staticmethod
    def _result_has_zero_advice(result: PostFinalAssessmentResultRecord) -> bool:
        return result.finding_count == 0 and result.withheld_finding_count == 0

    @staticmethod
    def _is_reader_review_result(result: PostFinalAssessmentResultRecord) -> bool:
        return (
            result.schema_version
            == PostFinalAssessmentResultRecord.reader_review_schema_id
        )

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
            **(
                {"user_status": result.reader_review_status}
                if result.reader_review_status is not None
                else {}
            ),
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
            and (
                result.schema_version
                != PostFinalAssessmentResultRecord.reader_review_schema_id
                or (
                    request.schema_version
                    == PostFinalAssessmentRequestRecord.reader_review_schema_id
                    and result.assessment_kind == READER_REVIEW_ASSESSMENT_KIND
                    and result.report_type == READER_REVIEW_REPORT_TYPE
                    and result.language == READER_REVIEW_LANGUAGE
                    and result.profile_id == READER_REVIEW_PROFILE_ID
                    and result.model_version == request.model_version
                    and result.expected_model_identity
                    == request.expected_model_identity
                    and result.parser_version == PARSER_VERSION
                    and result.projection_version == READER_REVIEW_PROJECTION_VERSION
                    and result.reader_view_payload
                    == view.model_dump(mode="json", warnings="error")
                )
            )
        )


__all__ = [
    "POST_FINAL_ASSESSMENT_POLICY_SCHEMA",
    "POST_FINAL_ASSESSMENT_RUN_SCHEMA",
    "READER_REVIEW_ASSESSMENT_KIND",
    "READER_REVIEW_LANGUAGE",
    "READER_REVIEW_PROFILE_ID",
    "READER_REVIEW_PROJECTION_VERSION",
    "READER_REVIEW_REPORT_TYPE",
    "ReaderReviewAssessmentInput",
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
