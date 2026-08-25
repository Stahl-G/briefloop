"""Strict, versioned v2 proposal and control DTO contracts.

These models define input shape only.  They do not write runtime state, decide
stage legality, establish source truth, or replace any current v1 authority.
"""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import base64
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Iterable, Literal, Optional, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from multi_agent_brief.contracts.agent_artifact_intake import AGENT_ARTIFACT_IDS
from multi_agent_brief.contracts.base import SchemaRegistry
from multi_agent_brief.contracts.errors import (
    ContractError,
    FieldViolation,
    pydantic_error_violations,
)
from multi_agent_brief.contracts.source_metadata import (
    VALID_RETRIEVAL_SOURCE_TYPES,
    VALID_SOURCE_CATEGORIES,
    VALID_UNDERLYING_EVIDENCE_TYPES,
)
from multi_agent_brief.orchestrator_contract import VALID_RUNTIMES


_CLEAN_TEXT_PATTERN = r"^\S(?:[\s\S]*\S)?$"
_ISO_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_ISO_DATETIME_PATTERN = r"^\d{4}-\d{2}-\d{2}T[\s\S]*(?:Z|[+-]\d{2}:\d{2})$"
_WORKSPACE_PATH_PATTERN = (
    r"^(?!/)(?!.*(?:^|/)(?:\.{1,2})(?:/|$))(?!.*//)(?!.*\\)(?!.*\/$).+$"
)
_SCRATCH_INPUT_PATH_PATTERN = (
    r"^scratch/[A-Za-z0-9][A-Za-z0-9._:-]*/"
    r"[A-Za-z0-9][A-Za-z0-9._:-]*\.(?:json|md|docx)$"
)
_APPROVAL_REASON_MAX_LENGTH = 1000
_MIME_TYPE_PATTERN = r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"

SOURCE_ORIGIN_TYPES = (
    "uploaded_file",
    "manual_evidence",
    "provider_response",
    "authorized_web_fetch",
    "cached_provider_response",
    "claim_ledger_derivative",
    "claim_draft_derivative",
    "brief_derivative",
    "audit_derivative",
    "model_summary_derivative",
    "search_snippet_only",
    "unknown",
)
SOURCE_ACQUISITION_METHODS = (
    "manual_upload",
    "manual_evidence",
    "provider_search",
    "provider_extract",
    "authorized_web_fetch",
    "cached_provider_response",
    "model_generated",
    "downstream_derivative",
    "unknown",
)
SOURCE_MATERIAL_KINDS = (
    "full_content",
    "partial_extract",
    "dataset_snapshot",
    "uploaded_file",
    "search_result",
    "search_snippet",
    "model_synthesis",
    "downstream_derivative",
    "unknown",
)
SOURCE_ELIGIBILITY_REASONS = (
    "eligible_durable_source_content",
    "ineligible_search_result",
    "ineligible_search_snippet",
    "ineligible_model_synthesis",
    "ineligible_downstream_derivative",
    "ineligible_unknown_origin",
)


def _scan_non_finite_numbers(value: Any, scanned: set[int]) -> bool:
    """Walk one payload for non-finite floats, recording visited containers.

    ``scanned`` accumulates the identity of every container the walk entered.
    An enclosing validation keeps that set so a nested contract can prove its
    payload was already covered instead of rewalking the same subtree.
    """

    stack = [value]
    while stack:
        current = stack.pop()
        if type(current) is float and not math.isfinite(current):
            return True
        if isinstance(current, dict):
            identity = id(current)
            if identity in scanned:
                continue
            scanned.add(identity)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in scanned:
                continue
            scanned.add(identity)
            stack.extend(current)
    return False


def _contains_non_finite_number(value: Any) -> bool:
    return _scan_non_finite_numbers(value, set())


# Set for the duration of one outermost contract validation. Nested contracts
# validated inside it are subtrees of a payload that was already walked, so
# they skip their own walk. The value is the identity set of the containers
# that walk actually covered: a nested payload that did not come from the
# outer structure is absent from it and is still walked.
_SCANNED_FINITE_CONTAINERS: ContextVar[set[int] | None] = ContextVar(
    "briefloop_scanned_finite_containers",
    default=None,
)


def _contract_fingerprint(payload: dict[str, Any], *, field: str) -> str:
    """Recompute one self-authenticating contract fingerprint."""

    canonical = dict(payload)
    canonical.pop(field, None)
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def canonical_run_direction_for_binding(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Normalize the one backward-compatible frozen RunDirection shape.

    ``output_contract`` was introduced after already-valid v2 run bindings had
    been frozen.  An absent field in those bindings and an explicitly null
    field both retain the unconstrained-output semantics, so neither belongs
    in serialized bindings or their fingerprints.  A present contract remains
    exact input.
    """

    canonical = dict(payload)
    if canonical.get("output_contract") is None:
        canonical.pop("output_contract", None)
    if canonical.get("report_type") is None:
        canonical.pop("report_type", None)
    return canonical


def _clean_text(value: str) -> str:
    if re.fullmatch(_CLEAN_TEXT_PATTERN, value) is None:
        raise ValueError("invalid text")
    return value


def _iso_date(value: str) -> str:
    if re.fullmatch(_ISO_DATE_PATTERN, value) is None:
        raise ValueError("invalid date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid date") from exc
    return value


def _iso_datetime(value: str) -> str:
    if re.fullmatch(_ISO_DATETIME_PATTERN, value) is None:
        raise ValueError("invalid date-time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid date-time") from exc
    if parsed.tzinfo is None:
        raise ValueError("date-time requires a timezone")
    return value


def _workspace_path(value: str) -> str:
    if re.fullmatch(_WORKSPACE_PATH_PATTERN, value) is None:
        raise ValueError("invalid workspace-relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("invalid workspace-relative path")
    if str(path) != value:
        raise ValueError("workspace path must be canonical")
    return value


def _scratch_input_path(value: str) -> str:
    _workspace_path(value)
    if re.fullmatch(_SCRATCH_INPUT_PATH_PATTERN, value) is None:
        raise ValueError("invalid invocation scratch path")
    return value


CleanText = Annotated[
    str,
    AfterValidator(_clean_text),
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 1,
            "pattern": _CLEAN_TEXT_PATTERN,
        }
    ),
]
ApprovalReason = Annotated[
    str,
    StringConstraints(max_length=_APPROVAL_REASON_MAX_LENGTH),
    AfterValidator(_clean_text),
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 1,
            "maxLength": _APPROVAL_REASON_MAX_LENGTH,
            "pattern": _CLEAN_TEXT_PATTERN,
        }
    ),
]
ContractId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
IsoDate = Annotated[
    str,
    AfterValidator(_iso_date),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date",
            "pattern": _ISO_DATE_PATTERN,
        }
    ),
]
IsoDateTime = Annotated[
    str,
    AfterValidator(_iso_datetime),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": _ISO_DATETIME_PATTERN,
        }
    ),
]
HttpUrlString = HttpUrl
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
WorkspacePath = Annotated[
    str,
    AfterValidator(_workspace_path),
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 1,
            "pattern": _WORKSPACE_PATH_PATTERN,
        }
    ),
]
ScratchInputPath = Annotated[
    str,
    AfterValidator(_scratch_input_path),
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 1,
            "pattern": _SCRATCH_INPUT_PATH_PATTERN,
        }
    ),
]
MimeType = Annotated[
    str,
    StringConstraints(pattern=_MIME_TYPE_PATTERN),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _MIME_TYPE_PATTERN,
        }
    ),
]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
RuntimeName = Literal[VALID_RUNTIMES]
RoleTopology = Literal["single_session", "default", "strict", "human_assisted"]
GateId = Literal[
    "coverage_omission",
    "editor_new_fact",
    "final_abstract_quality",
    "material_fact",
    "freshness",
    "target_relevance",
]
GATE_ID_VALUES = (
    "coverage_omission",
    "editor_new_fact",
    "final_abstract_quality",
    "material_fact",
    "freshness",
    "target_relevance",
)


# Event Log owner vocabulary. This is the DTO truth source; the Event Log
# writer imports it from here.
EVENT_TYPES = {
    "run_initialized",
    "handoff_written",
    "artifact_observed",
    "artifact_validated",
    "stage_status_changed",
    "stage_satisfied_by_topology",
    "decision_recorded",
    "feedback_issue_created",
    "feedback_issue_planned",
    "feedback_issue_resolved",
    "repair_plan_created",
    "repair_plan_completed",
    "repair_started",
    "gate_repair_started",
    "gate_repair_outcome_recorded",
    "repair_completed",
    "repair_stage_superseded",
    "quality_gate_checked",
    "quality_gate_blocked",
    "quality_gate_passed",
    "provenance_graph_built",
    "provenance_graph_validated",
    "provenance_graph_invalid",
    "audience_profile_snapshot_created",
    "control_switchboard_built",
    "control_switchboard_warning",
    "control_selection_recorded",
    "control_selection_validated",
    "improvement_proposed",
    "improvement_approved",
    "improvement_rejected",
    "improvement_reverted",
    "improvement_memory_snapshot_created",
    "delivery_attempted",
    "delivery_bundle_prepared",
    "delivery_draft_created",
    "delivery_succeeded",
    "delivery_failed",
    "human_approval_ledger_initialized",
    "human_approval_recorded",
    "release_readiness_checked",
    "fact_layer_imported",
    "claim_ledger_frozen",
    "claim_ledger_metadata_enriched",
    "trajectory_decision_narrowed",
    "run_archived",
    "run_blocked",
    "run_integrity_contaminated",
    "run_reset",
    "run_successor_started",
    "run_guidance_snapshot_frozen",
    "semantic_assessment_checked_inputs_bound",
    "semantic_support_finding_adjudicated",
    "source_evidence_committed",
    "input_classification_committed",
    "role_proposal_committed",
    "intake_rejected",
    "role_invocation_started",
    "owned_artifact_accepted",
    "audit_proposal_promoted",
    "post_final_assessment_policy_recorded",
    "post_final_assessment_claimed",
    "post_final_assessment_abandoned",
    "post_final_assessment_execution_recorded",
    "post_final_assessment_result_recorded",
    "post_final_finding_disposition_recorded",
    "post_final_guidance_draft_recorded",
    "post_final_guidance_status_recorded",
    "post_final_human_observation_recorded",
    "source_acquisition_attempt_authorized",
    "runtime_source_search_plan_recorded",
    "tavily_acquisition_bundle_recorded",
    "market_data_snapshot_recorded",
    "run_terminated",
}

# Release-mode approval vocabulary and boundary. DTO truth source;
# the product approval layer imports them from here.
APPROVAL_BOUNDARY = (
    "internal_review_approval_records_only_not_public_release_authorization"
)

RELEASE_MODES: dict[str, dict[str, Any]] = {
    "internal_draft": {
        "approval_required": False,
        "required_roles": [],
        "description": "Internal draft readiness. No human approval is required.",
    },
    "internal_management_review": {
        "approval_required": True,
        "required_roles": ["content_owner"],
        "description": "Ready for internal management review when content owner approval is present.",
    },
    "research_review": {
        "approval_required": True,
        "required_roles": ["content_owner", "evidence_reviewer"],
        "description": "Ready for research review when content and evidence approvals are present.",
    },
    "ir_draft": {
        "approval_required": True,
        "required_roles": [
            "ir_owner",
            "evidence_reviewer",
            "legal_or_compliance_reviewer",
        ],
        "description": "Ready for IR draft review when owner, evidence, and legal/compliance approvals are present.",
    },
    "formal_release_candidate": {
        "approval_required": True,
        "required_roles": [
            "content_owner",
            "evidence_reviewer",
            "legal_or_compliance_reviewer",
        ],
        "description": "Ready for formal release-candidate review when required internal approvals are present.",
    },
}


def _event_type_json_schema(schema: dict[str, Any]) -> None:
    schema["enum"] = sorted(EVENT_TYPES)


class StrictModel(BaseModel):
    """Base for v2 contracts with no coercion and no undeclared fields."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        validate_default=True,
        allow_inf_nan=False,
    )

    schema_id: ClassVar[str]
    schema_version_number: ClassVar[str] = "2"
    minimal_example: ClassVar[dict[str, Any]]
    full_example: ClassVar[dict[str, Any]]

    @model_validator(mode="wrap")
    @classmethod
    def reject_non_finite_json_numbers(
        cls,
        value: Any,
        handler: ValidatorFunctionWrapHandler,
    ) -> Any:
        scanned = _SCANNED_FINITE_CONTAINERS.get()
        if scanned is not None:
            if id(value) not in scanned and _scan_non_finite_numbers(value, scanned):
                raise PydanticCustomError(
                    "non_finite_json_number",
                    "non-finite JSON number",
                )
            return handler(value)
        scanned = set()
        if _scan_non_finite_numbers(value, scanned):
            raise PydanticCustomError(
                "non_finite_json_number",
                "non-finite JSON number",
            )
        token = _SCANNED_FINITE_CONTAINERS.set(scanned)
        try:
            return handler(value)
        finally:
            _SCANNED_FINITE_CONTAINERS.reset(token)

    @classmethod
    def contract_validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        try:
            cls.model_validate(data)
        except ValidationError as exc:
            return pydantic_error_violations(exc)
        return []

    @classmethod
    def contract_validate_or_raise(cls, data: dict[str, Any]) -> None:
        violations = cls.contract_validate(data)
        if violations:
            raise ContractError(
                violations=violations,
                schema_id=cls.schema_id,
                schema_version=cls.schema_version_number,
            )

    _contract_json_schema_cache: ClassVar[dict[type, dict[str, Any]]] = {}

    @classmethod
    def contract_json_schema(cls) -> dict[str, Any]:
        # model_json_schema() regenerates the whole schema on every call, and
        # the verifier asks for contract schemas thousands of times per run.
        # The schema is a pure function of the class, but callers own the dict
        # they get back, so cache one canonical build and hand out copies.
        cached = StrictModel._contract_json_schema_cache.get(cls)
        if cached is None:
            cached = cls.model_json_schema()
            cached["$id"] = cls.schema_id
            cached["examples"] = [
                deepcopy(cls.minimal_example),
                deepcopy(cls.full_example),
            ]
            StrictModel._contract_json_schema_cache[cls] = cached
        return deepcopy(cached)

    @classmethod
    def contract_example(cls, detail: str) -> dict[str, Any]:
        if detail == "minimal":
            example = cls.minimal_example
        elif detail == "full":
            example = cls.full_example
        else:
            raise ValueError("Example detail must be 'minimal' or 'full'.")
        cls.model_validate(example)
        return deepcopy(example)


class WebSourceLocator(StrictModel):
    kind: Literal["web"]
    url: HttpUrlString


class FileSourceLocator(StrictModel):
    kind: Literal["file"]
    path: WorkspacePath


SourceLocator = Annotated[
    Union[WebSourceLocator, FileSourceLocator], Field(discriminator="kind")
]


class CandidateClaimItem(StrictModel):
    candidate_id: ContractId
    source_id: ContractId
    statement: CleanText
    evidence_text: CleanText
    topic: CleanText
    claim_type: Literal["fact", "trend", "risk", "opportunity", "estimate"]
    confidence: Literal["low", "medium", "high"]


class ScreeningDecisionItem(StrictModel):
    candidate_id: ContractId
    decision: Literal["selected", "excluded", "deprioritized"]
    priority: Optional[Literal["low", "medium", "high"]] = None
    reason_code: Optional[ContractId] = None
    explanation: Optional[CleanText] = None


class ClaimDraftItem(StrictModel):
    draft_id: ContractId
    statement: CleanText
    evidence_text: CleanText
    source_ids: list[ContractId] = Field(min_length=1)
    claim_type: Literal["fact", "trend", "risk", "opportunity", "estimate"]

    @model_validator(mode="after")
    def unique_sources(self) -> "ClaimDraftItem":
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("duplicate source identity")
        return self


class AuditFindingItem(StrictModel):
    finding_code: ContractId
    severity: Literal["warning", "error"]
    artifact_id: ContractId
    summary: CleanText


class SourceAcquisitionArtifactReference(StrictModel):
    """Exact optional acquisition-bundle artifact frozen with one attempt."""

    artifact_id: ContractId
    revision: Literal[1]


class TavilyAcquisitionExchange(StrictModel):
    """One exact non-secret HTTP body exchange in a Tavily acquisition attempt."""

    operation: Literal["search", "extract"]
    endpoint: Literal["/search", "/extract"]
    request_body_base64: str
    request_body_sha256: Sha256
    request_body_size_bytes: PositiveInt
    response_body_base64: str | None = None
    response_body_sha256: Sha256 | None = None
    response_body_size_bytes: NonNegativeInt | None = None
    status_code: Annotated[int, Field(ge=100, le=599)] | None = None
    # Present only when no HTTP response was received.  Keep this projection
    # value-free: it is a coarse stdlib transport class, never an exception
    # message, URL, host, or credential.
    transport_error_class: (
        Literal[
            "dns",
            "tls",
            "connect",
            "timeout",
            "proxy",
            "network_permission_denied",
            "other",
        ]
        | None
    ) = Field(default=None, exclude_if=lambda value: value is None)

    @staticmethod
    def _decode_exact(value: str) -> bytes:
        try:
            decoded = base64.b64decode(value.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError) as exc:
            raise ValueError("exchange bytes are not canonical base64") from exc
        if base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError("exchange bytes are not canonical base64")
        return decoded

    @model_validator(mode="after")
    def byte_identity_is_total(self) -> "TavilyAcquisitionExchange":
        if self.endpoint != f"/{self.operation}":
            raise ValueError("exchange endpoint does not match operation")
        request = self._decode_exact(self.request_body_base64)
        if (
            not request
            or len(request) != self.request_body_size_bytes
            or hashlib.sha256(request).hexdigest() != self.request_body_sha256
        ):
            raise ValueError("exchange request identity mismatch")
        response_fields = (
            self.response_body_base64,
            self.response_body_sha256,
            self.response_body_size_bytes,
            self.status_code,
        )
        if all(value is None for value in response_fields):
            return self
        if any(value is None for value in response_fields):
            raise ValueError("exchange response identity is incomplete")
        if self.transport_error_class is not None:
            raise ValueError("transport error cannot accompany an HTTP response")
        response = self._decode_exact(self.response_body_base64 or "")
        if (
            len(response) != self.response_body_size_bytes
            or hashlib.sha256(response).hexdigest() != self.response_body_sha256
        ):
            raise ValueError("exchange response identity mismatch")
        return self


class TavilyExtractUrlOutcome(StrictModel):
    """Value-free per-URL projection from one batch Extract response."""

    url: CleanText
    status: Literal["succeeded", "provider_failed", "empty_content"]
    response_item_sha256: Sha256
    content_sha256: Sha256 | None = None
    content_size_bytes: PositiveInt | None = None

    @model_validator(mode="after")
    def success_content_identity_is_total(self) -> "TavilyExtractUrlOutcome":
        if self.status == "succeeded":
            if self.content_sha256 is None or self.content_size_bytes is None:
                raise ValueError("successful extract outcome requires content identity")
        elif self.content_sha256 is not None or self.content_size_bytes is not None:
            raise ValueError("failed extract outcome cannot claim content identity")
        return self


class TavilyAcquisitionBundle(StrictModel):
    """Historical v1 single-Search evidence; read-only and never newly emitted."""

    schema_id = "briefloop.tavily_acquisition_bundle.v1"

    schema_version: Literal["briefloop.tavily_acquisition_bundle.v1"]
    provider_id: Literal["tavily"]
    status: Literal[
        "search_response_unavailable",
        "search_response_invalid",
        "search_results_empty",
        "extract_response_unavailable",
        "extract_response_invalid",
        "extract_results_all_failed",
        "extract_results_partial",
        "extract_results_succeeded",
    ]
    search: TavilyAcquisitionExchange
    extract: TavilyAcquisitionExchange | None = None
    extract_urls: list[CleanText] = Field(max_length=5)
    outcomes: list[TavilyExtractUrlOutcome] = Field(max_length=5)

    @model_validator(mode="after")
    def acquisition_shape_is_canonical(self) -> "TavilyAcquisitionBundle":
        if self.search.operation != "search":
            raise ValueError("bundle search exchange is invalid")
        if self.extract_urls != sorted(set(self.extract_urls)):
            raise ValueError("extract URLs must be sorted and unique")
        if [item.url for item in self.outcomes] != sorted(
            {item.url for item in self.outcomes}
        ):
            raise ValueError("extract outcomes must be sorted and unique")
        if self.status in {
            "search_response_unavailable",
            "search_response_invalid",
            "search_results_empty",
        }:
            if self.extract is not None or self.extract_urls or self.outcomes:
                raise ValueError("terminal Search cannot carry Extract evidence")
            if self.status == "search_response_unavailable":
                if self.search.status_code == 200:
                    raise ValueError("unavailable Search cannot be HTTP 200")
            elif self.search.status_code != 200:
                raise ValueError("valid or invalid Search requires HTTP 200 evidence")
            return self
        if (
            self.search.status_code != 200
            or self.extract is None
            or self.extract.operation != "extract"
            or not self.extract_urls
        ):
            raise ValueError("non-empty search requires one batch Extract exchange")
        if self.status == "extract_response_unavailable":
            if self.outcomes or self.extract.status_code == 200:
                raise ValueError("unavailable Extract response cannot carry outcomes")
            return self
        if self.status == "extract_response_invalid":
            if self.outcomes or self.extract.status_code != 200:
                raise ValueError("invalid Extract response requires exact HTTP 200")
            return self
        if self.extract.status_code != 200 or set(self.extract_urls) != {
            item.url for item in self.outcomes
        }:
            raise ValueError("Extract outcomes do not cover the exact request")
        succeeded = sum(item.status == "succeeded" for item in self.outcomes)
        expected = (
            "extract_results_all_failed"
            if succeeded == 0
            else "extract_results_succeeded"
            if succeeded == len(self.outcomes)
            else "extract_results_partial"
        )
        if self.status != expected:
            raise ValueError("bundle status does not match Extract outcomes")
        return self


class TavilySearchTaskExchange(StrictModel):
    """One exact primary or backfill Search in a multi-task acquisition."""

    task_id: ContractId
    phase: Literal["primary", "backfill"]
    status: Literal["succeeded", "empty", "unavailable", "invalid"]
    exchange: TavilyAcquisitionExchange
    discovered_urls: list[CleanText] = Field(max_length=20)

    @model_validator(mode="after")
    def task_search_shape(self) -> "TavilySearchTaskExchange":
        if self.exchange.operation != "search":
            raise ValueError("task Search exchange is invalid")
        if self.discovered_urls != sorted(set(self.discovered_urls)):
            raise ValueError("task Search URLs must be sorted and unique")
        if self.status in {"unavailable", "invalid", "empty"} and self.discovered_urls:
            raise ValueError("terminal task Search cannot claim discovered URLs")
        if self.status == "unavailable" and self.exchange.status_code == 200:
            raise ValueError("unavailable task Search cannot be HTTP 200")
        if self.status in {"succeeded", "empty", "invalid"} and (
            self.exchange.status_code != 200
        ):
            raise ValueError("parsed task Search requires HTTP 200")
        if self.status == "succeeded" and not self.discovered_urls:
            raise ValueError("successful task Search requires discovered URLs")
        return self


class TavilyExtractBatchExchange(StrictModel):
    """One exact technical batch of at most twenty Extract URLs."""

    phase: Literal["primary", "backfill"]
    batch_ordinal: PositiveInt
    status: Literal["succeeded", "partial", "all_failed", "unavailable", "invalid"]
    exchange: TavilyAcquisitionExchange
    urls: list[CleanText] = Field(min_length=1, max_length=20)
    outcomes: list[TavilyExtractUrlOutcome] = Field(max_length=20)

    @model_validator(mode="after")
    def extract_batch_shape(self) -> "TavilyExtractBatchExchange":
        if self.exchange.operation != "extract":
            raise ValueError("batch Extract exchange is invalid")
        if self.urls != sorted(set(self.urls)):
            raise ValueError("batch Extract URLs must be sorted and unique")
        outcome_urls = [item.url for item in self.outcomes]
        if outcome_urls != sorted(set(outcome_urls)):
            raise ValueError("batch Extract outcomes must be sorted and unique")
        if self.status in {"unavailable", "invalid"}:
            if self.outcomes:
                raise ValueError("unusable Extract batch cannot claim outcomes")
            if self.status == "unavailable" and self.exchange.status_code == 200:
                raise ValueError("unavailable Extract batch cannot be HTTP 200")
            if self.status == "invalid" and self.exchange.status_code != 200:
                raise ValueError("invalid Extract batch requires HTTP 200")
            return self
        if self.exchange.status_code != 200 or set(self.urls) != set(outcome_urls):
            raise ValueError("Extract batch outcomes must cover the exact URL batch")
        succeeded = sum(item.status == "succeeded" for item in self.outcomes)
        expected = (
            "all_failed"
            if succeeded == 0
            else "succeeded"
            if succeeded == len(self.outcomes)
            else "partial"
        )
        if self.status != expected:
            raise ValueError("Extract batch status does not match outcomes")
        return self


class TavilyTaskAcquisitionStatus(StrictModel):
    """Value-free final coverage status for one frozen search task."""

    task_id: ContractId
    primary_search_ordinal: PositiveInt
    backfill_search_ordinal: PositiveInt | None = None
    discovered_unique_url_count: NonNegativeInt
    extracted_success_count: NonNegativeInt
    minimum_extract_successes: PositiveInt
    status: Literal[
        "covered",
        "coverage_insufficient",
        "search_unavailable",
        "extract_unavailable",
    ]

    @model_validator(mode="after")
    def coverage_status_matches_counts(self) -> "TavilyTaskAcquisitionStatus":
        covered = self.extracted_success_count >= self.minimum_extract_successes
        if covered != (self.status == "covered"):
            if self.status not in {"search_unavailable", "extract_unavailable"}:
                raise ValueError("task coverage status does not match counts")
            if covered:
                raise ValueError("failed task cannot meet its coverage threshold")
        return self


class TavilyAcquisitionBundleV2(StrictModel):
    """Exact multi-Search and multi-batch Extract execution evidence."""

    schema_id = "briefloop.tavily_acquisition_bundle.v2"

    schema_version: Literal["briefloop.tavily_acquisition_bundle.v2"]
    provider_id: Literal["tavily"]
    status: Literal["complete", "partial", "failed"]
    searches: list[TavilySearchTaskExchange] = Field(min_length=1, max_length=40)
    extract_batches: list[TavilyExtractBatchExchange] = Field(max_length=40)
    unique_urls: list[CleanText] = Field(max_length=800)
    task_statuses: list[TavilyTaskAcquisitionStatus] = Field(
        min_length=1, max_length=20
    )

    @model_validator(mode="after")
    def multi_acquisition_shape(self) -> "TavilyAcquisitionBundleV2":
        if self.unique_urls != sorted(set(self.unique_urls)):
            raise ValueError("multi-acquisition URLs must be sorted and unique")
        if [item.task_id for item in self.task_statuses] != sorted(
            {item.task_id for item in self.task_statuses}
        ):
            raise ValueError("task statuses must be sorted and unique")
        if [item.batch_ordinal for item in self.extract_batches] != list(
            range(1, len(self.extract_batches) + 1)
        ):
            raise ValueError("Extract batch ordinals must be contiguous")
        batch_urls = [url for batch in self.extract_batches for url in batch.urls]
        if (
            len(batch_urls) != len(set(batch_urls))
            or sorted(batch_urls) != self.unique_urls
        ):
            raise ValueError("Extract batches must partition every unique URL")
        covered = sum(item.status == "covered" for item in self.task_statuses)
        expected = (
            "complete"
            if covered == len(self.task_statuses)
            else "failed"
            if covered == 0
            else "partial"
        )
        if self.status != expected:
            raise ValueError("multi-acquisition status does not match task coverage")
        return self


class TavilyAcquisitionBundleRecordV2(StrictModel):
    """Receipt-owned Store identity for one frozen multi-Tavily bundle."""

    schema_id = "briefloop.tavily_acquisition_bundle_record.v2"

    schema_version: Literal["briefloop.tavily_acquisition_bundle_record.v2"]
    bundle_record_id: ContractId
    run_id: ContractId
    attempt_authorization_id: ContractId
    provider_response_artifact_id: ContractId
    provider_response_sha256: Sha256
    bundle_status: Literal["complete", "partial", "failed"]
    search_count: Annotated[int, Field(ge=1, le=40)]
    extract_batch_count: Annotated[int, Field(ge=0, le=40)]
    unique_url_count: Annotated[int, Field(ge=0, le=800)]
    durable_content_count: Annotated[int, Field(ge=0, le=800)]
    record_event_id: ContractId
    accepted_transaction_id: ContractId
    recorded_at: IsoDateTime
    record_fingerprint: Sha256

    @model_validator(mode="after")
    def bundle_record_identity_is_exact(self) -> "TavilyAcquisitionBundleRecordV2":
        if self.durable_content_count > self.unique_url_count:
            raise ValueError("durable content count exceeds the URL universe")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"record_fingerprint"}),
            field="record_fingerprint",
        )
        if self.record_fingerprint != expected:
            raise ValueError("Tavily bundle record fingerprint mismatch")
        return self


class MarketDataSecurityV1(StrictModel):
    """One exact weekly equity quote inside a frozen market data snapshot.

    Quote and valuation fields are explicit nulls when the provider response
    or manual input file does not carry them; nothing is estimated,
    interpolated, or backfilled.
    """

    ticker: ContractId
    exchange: CleanText
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    as_of: IsoDate
    data_origin: Literal["yahoo_chart_api", "manual_input"]
    week_open: Optional[Annotated[float, Field(gt=0)]]
    week_high: Optional[Annotated[float, Field(gt=0)]]
    week_low: Optional[Annotated[float, Field(gt=0)]]
    week_close: Annotated[float, Field(gt=0)]
    week_volume: Optional[NonNegativeInt]
    weekly_change_pct: Optional[float]
    market_cap: Optional[Annotated[float, Field(ge=0)]]
    trailing_pe: Optional[Annotated[float, Field(gt=0)]]

    @model_validator(mode="after")
    def weekly_bar_is_coherent(self) -> "MarketDataSecurityV1":
        if (
            self.week_high is not None
            and self.week_low is not None
            and self.week_high < self.week_low
        ):
            raise ValueError("weekly high is below weekly low")
        return self


class MarketDataSecurityGapV1(StrictModel):
    """Value-free record of one security that could not be quoted."""

    ticker: ContractId
    failure_class: Literal[
        "transport_unavailable",
        "http_error",
        "response_invalid",
        "symbol_data_missing",
        "manual_record_invalid",
    ]


class MarketDataSnapshotV1(StrictModel):
    """Append-only weekly market data snapshot for one run and as-of date.

    The Store enforces one snapshot per (run_id, as_of_date) and rejects any
    in-place change; a correction requires a later as-of date.  Missing
    securities appear only as value-free gaps, never as fabricated quotes.
    """

    schema_id = "briefloop.market_data_snapshot.v1"

    schema_version: Literal["briefloop.market_data_snapshot.v1"]
    market_data_snapshot_id: ContractId
    run_id: ContractId
    as_of_date: IsoDate
    security_count: Annotated[int, Field(ge=1, le=11)]
    provider_id: Literal["yahoo_finance_chart"]
    securities: list[MarketDataSecurityV1] = Field(min_length=1, max_length=11)
    gaps: list[MarketDataSecurityGapV1] = Field(max_length=11)
    record_event_id: ContractId
    accepted_transaction_id: ContractId
    recorded_at: IsoDateTime
    snapshot_fingerprint: Sha256

    @model_validator(mode="after")
    def snapshot_identity_is_exact(self) -> "MarketDataSnapshotV1":
        tickers = [item.ticker for item in self.securities]
        if tickers != sorted(set(tickers)):
            raise ValueError("market data securities must be sorted and unique")
        if self.security_count != len(self.securities):
            raise ValueError("market data security count mismatch")
        gap_tickers = [item.ticker for item in self.gaps]
        if gap_tickers != sorted(set(gap_tickers)):
            raise ValueError("market data gaps must be sorted and unique")
        if set(tickers) & set(gap_tickers):
            raise ValueError("market data gap tickers must not carry a quote")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"snapshot_fingerprint"}),
            field="snapshot_fingerprint",
        )
        if self.snapshot_fingerprint != expected:
            raise ValueError("market data snapshot fingerprint mismatch")
        return self


class MarketDataWorkbookIdentityV2(StrictModel):
    """Content identity and parser boundary for one manually supplied workbook."""

    source_name: CleanText
    content_sha256: Sha256
    content_size_bytes: NonNegativeInt
    profile_id: Literal["toyo-weekly-v1"]
    parsed_sheet_names: list[CleanText] = Field(min_length=7, max_length=16)
    contains_macros: Literal[False]
    contains_external_links: Literal[False]

    @model_validator(mode="after")
    def sheets_are_canonical(self) -> "MarketDataWorkbookIdentityV2":
        if self.parsed_sheet_names != sorted(set(self.parsed_sheet_names)):
            raise ValueError("workbook sheet identities must be sorted and unique")
        return self


class MarketDataSeriesPointV2(StrictModel):
    """One exact daily market observation with a stable source locator."""

    date: IsoDate
    close: Annotated[float, Field(gt=0)]
    adjusted_close: Optional[Annotated[float, Field(gt=0)]] = None
    volume: Optional[NonNegativeInt] = None
    data_origin: Literal[
        "manual_xlsx",
        "manual_json",
        "manual_csv",
        "yahoo_chart_api",
    ]
    source_locator: CleanText
    source_sha256: Sha256


class MarketDataCorporateActionV2(StrictModel):
    """One provider- or workbook-bound corporate action observation."""

    action_id: ContractId
    date: IsoDate
    action_type: Literal["dividend", "split", "capital_gain"]
    value: Annotated[float, Field(gt=0)]
    currency: Optional[Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]] = None
    split_numerator: Optional[Annotated[float, Field(gt=0)]] = None
    split_denominator: Optional[Annotated[float, Field(gt=0)]] = None
    data_origin: Literal[
        "manual_xlsx",
        "manual_json",
        "manual_csv",
        "yahoo_chart_api",
    ]
    source_locator: CleanText
    source_sha256: Sha256

    @model_validator(mode="after")
    def action_shape_matches_kind(self) -> "MarketDataCorporateActionV2":
        split_values = (self.split_numerator, self.split_denominator)
        if self.action_type == "split":
            if not all(value is not None for value in split_values):
                raise ValueError("split action requires an exact ratio")
            if self.currency is not None:
                raise ValueError("split action cannot carry currency")
        elif any(value is not None for value in split_values):
            raise ValueError("non-split action cannot carry a split ratio")
        return self


class MarketDataFieldValueV2(StrictModel):
    """One independently sourced or derived field in a security row."""

    field_id: ContractId
    status: Literal["available", "unavailable", "not_meaningful"]
    value_number: Optional[float] = None
    value_text: Optional[CleanText] = None
    unit: CleanText
    as_of: Optional[IsoDate] = None
    currency: Optional[Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]] = None
    data_origin: Literal[
        "manual_xlsx",
        "manual_json",
        "manual_csv",
        "yahoo_chart_api",
        "yahoo_quote_summary",
        "derived",
    ]
    derivation: Literal[
        "direct",
        "recomputed",
        "converted",
        "manual_override",
        "provider_fill",
    ]
    source_locator: CleanText
    source_sha256: Sha256
    reason_code: Optional[ContractId] = None

    @model_validator(mode="after")
    def value_shape_matches_status(self) -> "MarketDataFieldValueV2":
        present = int(self.value_number is not None) + int(self.value_text is not None)
        if self.status == "available":
            if present != 1 or self.reason_code is not None:
                raise ValueError("available market field requires one exact value")
        elif present != 0 or self.reason_code is None:
            raise ValueError("unavailable market field requires a reason and no value")
        return self


class MarketDataSecurityV2(StrictModel):
    """One security's daily series and per-field provenance."""

    ticker: ContractId
    display_name: CleanText
    universe: Literal["primary", "overseas"]
    exchange: CleanText
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    return_basis: Literal["adjusted_close", "close"]
    price_series: list[MarketDataSeriesPointV2] = Field(min_length=1, max_length=400)
    corporate_actions: list[MarketDataCorporateActionV2] = Field(max_length=128)
    fields: list[MarketDataFieldValueV2] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def security_rows_are_canonical(self) -> "MarketDataSecurityV2":
        dates = [item.date for item in self.price_series]
        if dates != sorted(set(dates)):
            raise ValueError("market data series must be date-sorted and unique")
        field_ids = [item.field_id for item in self.fields]
        if field_ids != sorted(set(field_ids)):
            raise ValueError("market data fields must be sorted and unique")
        if self.return_basis == "adjusted_close" and any(
            item.adjusted_close is None for item in self.price_series
        ):
            raise ValueError("adjusted-close return basis requires a complete series")
        action_keys = [(item.date, item.action_id) for item in self.corporate_actions]
        if action_keys != sorted(set(action_keys)):
            raise ValueError("corporate actions must be ordered and unique")
        return self


class MarketDataBenchmarkV2(StrictModel):
    ticker: ContractId
    display_name: CleanText
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    return_basis: Literal["adjusted_close", "close"]
    price_series: list[MarketDataSeriesPointV2] = Field(min_length=2, max_length=400)

    @model_validator(mode="after")
    def benchmark_series_is_canonical(self) -> "MarketDataBenchmarkV2":
        dates = [item.date for item in self.price_series]
        if dates != sorted(set(dates)):
            raise ValueError("benchmark series must be date-sorted and unique")
        if self.return_basis == "adjusted_close" and any(
            item.adjusted_close is None for item in self.price_series
        ):
            raise ValueError("adjusted-close benchmark requires a complete series")
        return self


class MarketDataFxRateV2(StrictModel):
    base_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    quote_currency: Literal["USD"]
    units_per_usd: Annotated[float, Field(gt=0)]
    as_of: IsoDate
    data_origin: Literal["manual_xlsx", "manual_json", "manual_csv", "yahoo_chart_api"]
    source_locator: CleanText
    source_sha256: Sha256


class MarketDataEventReactionV2(StrictModel):
    """One company event and contemporaneous price observations.

    Return fields describe co-movement only. They never encode causal proof.
    """

    event_id: ContractId
    ticker: ContractId
    title: CleanText
    published_at: IsoDate
    publication_timing: Literal[
        "pre_market", "during_market", "after_market", "unknown"
    ]
    original_url: Optional[HttpUrlString] = None
    evidence_status: Literal["claim_eligible", "display_only_source_url_missing"]
    event_day_return_pct: Optional[float] = None
    benchmark_event_day_return_pct: Optional[float] = None
    event_day_excess_return_pct: Optional[float] = None
    t1_return_pct: Optional[float] = None
    t5_return_pct: Optional[float] = None
    event_day_volume: Optional[NonNegativeInt] = None
    volume_ratio: Optional[Annotated[float, Field(ge=0)]] = None
    data_origin: Literal["manual_xlsx", "manual_json", "manual_csv", "derived"]
    source_locator: CleanText
    source_sha256: Sha256

    @model_validator(mode="after")
    def evidence_status_matches_url(self) -> "MarketDataEventReactionV2":
        if (self.original_url is None) != (
            self.evidence_status == "display_only_source_url_missing"
        ):
            raise ValueError("event evidence status does not match original URL")
        return self


class MarketDataGapV2(StrictModel):
    gap_id: ContractId
    severity: Literal["warning", "blocking"]
    category: Literal[
        "missing_security_series",
        "field_unavailable",
        "event_source_url_missing",
        "workbook_profile_mismatch",
        "workbook_formula_unresolved",
        "display_only_chart_underlying_series_missing",
        "provider_unavailable",
    ]
    ticker: Optional[ContractId] = None
    field_id: Optional[ContractId] = None
    source_locator: Optional[CleanText] = None
    reason_code: ContractId


class MarketDataConflictV2(StrictModel):
    conflict_id: ContractId
    severity: Literal["warning", "blocking"]
    category: Literal[
        "manual_provider_value_mismatch",
        "formula_recompute_mismatch",
        "duplicate_manual_field",
    ]
    ticker: Optional[ContractId] = None
    field_id: ContractId
    manual_value: Optional[float] = None
    provider_value: Optional[float] = None
    resolution: Literal["manual_wins", "unresolved"]
    source_locator: CleanText


class MarketDataSnapshotV2(StrictModel):
    """Append-only structured market evidence for Solar Stock Periodic."""

    schema_id = "briefloop.market_data_snapshot.v2"

    schema_version: Literal["briefloop.market_data_snapshot.v2"]
    market_data_snapshot_id: ContractId
    run_id: ContractId
    report_window_start: IsoDate
    report_window_end: IsoDate
    as_of_date: IsoDate
    universe_tickers: list[ContractId] = Field(min_length=1, max_length=20)
    security_count: Annotated[int, Field(ge=1, le=20)]
    provider_ids: list[ContractId] = Field(min_length=1, max_length=8)
    workbook: Optional[MarketDataWorkbookIdentityV2] = None
    securities: list[MarketDataSecurityV2] = Field(min_length=1, max_length=20)
    benchmark: Optional[MarketDataBenchmarkV2] = None
    fx_rates: list[MarketDataFxRateV2] = Field(max_length=16)
    events: list[MarketDataEventReactionV2] = Field(max_length=128)
    gaps: list[MarketDataGapV2] = Field(max_length=128)
    conflicts: list[MarketDataConflictV2] = Field(max_length=128)
    derivation_version: ContractId
    record_event_id: ContractId
    accepted_transaction_id: ContractId
    recorded_at: IsoDateTime
    snapshot_fingerprint: Sha256

    @model_validator(mode="after")
    def snapshot_v2_identity_is_exact(self) -> "MarketDataSnapshotV2":
        if self.report_window_end < self.report_window_start:
            raise ValueError("market data report window is inverted")
        if not (self.report_window_start <= self.as_of_date <= self.report_window_end):
            raise ValueError("market data as-of date is outside the report window")
        if self.universe_tickers != list(dict.fromkeys(self.universe_tickers)):
            raise ValueError("market data universe must be ordered and unique")
        tickers = [item.ticker for item in self.securities]
        if tickers != sorted(set(tickers)):
            raise ValueError("market data securities must be sorted and unique")
        if self.security_count != len(self.securities):
            raise ValueError("market data security count mismatch")
        if not set(tickers) <= set(self.universe_tickers):
            raise ValueError("market data security is outside the frozen universe")
        if self.provider_ids != sorted(set(self.provider_ids)):
            raise ValueError(
                "market data provider identities must be sorted and unique"
            )
        fx_pairs = [(item.base_currency, item.quote_currency) for item in self.fx_rates]
        if fx_pairs != sorted(set(fx_pairs)):
            raise ValueError("market data FX rates must be sorted and unique")
        event_ids = [item.event_id for item in self.events]
        if event_ids != sorted(set(event_ids)):
            raise ValueError("market data events must be sorted and unique")
        if any(item.ticker not in self.universe_tickers for item in self.events):
            raise ValueError("market data event is outside the frozen universe")
        gap_ids = [item.gap_id for item in self.gaps]
        if gap_ids != sorted(set(gap_ids)):
            raise ValueError("market data gaps must be sorted and unique")
        if any(
            item.ticker is not None and item.ticker not in self.universe_tickers
            for item in self.gaps
        ):
            raise ValueError("market data gap is outside the frozen universe")
        conflict_ids = [item.conflict_id for item in self.conflicts]
        if conflict_ids != sorted(set(conflict_ids)):
            raise ValueError("market data conflicts must be sorted and unique")
        if any(
            item.ticker is not None and item.ticker not in self.universe_tickers
            for item in self.conflicts
        ):
            raise ValueError("market data conflict is outside the frozen universe")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"snapshot_fingerprint"}),
            field="snapshot_fingerprint",
        )
        if self.snapshot_fingerprint != expected:
            raise ValueError("market data snapshot fingerprint mismatch")
        return self


class SourceAcquisitionFailureEvidence(StrictModel):
    """Value-free, receipt-owned evidence for one failed discovery attempt."""

    schema_id = "briefloop.source_acquisition_failure_evidence.v1"

    schema_version: Literal["briefloop.source_acquisition_failure_evidence.v1"]
    attempt_id: ContractId
    attempt_authorization_id: ContractId
    attempt_ordinal: PositiveInt
    run_id: ContractId
    invocation_id: ContractId
    discovery_authorization_id: ContractId
    provider_id: Literal["tavily"]
    route_fingerprint: Sha256
    provider_request_fingerprint: Sha256
    request_fingerprint: Sha256
    failure_class: Literal[
        "provider_transport_unavailable",
        "provider_search_failed",
        "provider_extract_failed",
        "provider_results_empty",
        "provider_results_without_durable_content",
        "intake_rejected_no_eligible_source",
        "source_pack_validation_rejected",
        "provider_response_unavailable",
    ]
    provider_status_class: Literal[
        "acquisition_bundle_retained",
        "response_unavailable",
    ]
    provider_response_artifact: SourceAcquisitionArtifactReference | None = None
    provider_response_sha256: Sha256 | None = None
    provider_response_size_bytes: PositiveInt | None = None
    transport_phase: Literal["search", "extract"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    transport_error_class: (
        Literal[
            "dns",
            "tls",
            "connect",
            "timeout",
            "proxy",
            "network_permission_denied",
            "other",
        ]
        | None
    ) = Field(default=None, exclude_if=lambda value: value is None)
    result_count: NonNegativeInt | None = None
    durable_content_count: NonNegativeInt | None = None
    claims_eligible_count: NonNegativeInt | None = None
    rejection_counts: dict[ContractId, NonNegativeInt] | None = None

    @model_validator(mode="after")
    def failure_shape_is_total(self) -> "SourceAcquisitionFailureEvidence":
        artifact_values = (
            self.provider_response_artifact,
            self.provider_response_sha256,
            self.provider_response_size_bytes,
        )
        if any(value is None for value in artifact_values) != all(
            value is None for value in artifact_values
        ):
            raise ValueError("provider response artifact identity is incomplete")
        count_values = (
            self.result_count,
            self.durable_content_count,
            self.claims_eligible_count,
        )
        if self.failure_class == "provider_response_unavailable":
            if (
                self.provider_status_class != "response_unavailable"
                or any(value is not None for value in artifact_values)
                or any(value is not None for value in count_values)
                or self.rejection_counts is not None
            ):
                raise ValueError("unavailable response cannot carry response evidence")
            return self
        if self.failure_class == "provider_transport_unavailable":
            if (
                self.provider_status_class != "acquisition_bundle_retained"
                or any(value is None for value in artifact_values)
                or self.durable_content_count != 0
                or self.claims_eligible_count != 0
                or self.rejection_counts is not None
                or self.transport_phase is None
                or self.transport_error_class is None
                or self.result_count is None
                or (self.transport_phase == "search" and self.result_count != 0)
                or (self.transport_phase == "extract" and self.result_count == 0)
            ):
                raise ValueError("transport failure evidence is incomplete")
            return self
        if self.transport_phase is not None or self.transport_error_class is not None:
            raise ValueError(
                "transport classification is only valid for transport failure"
            )
        if (
            self.provider_status_class != "acquisition_bundle_retained"
            or any(value is None for value in artifact_values)
            or self.result_count is None
            or self.durable_content_count is None
            or self.durable_content_count > self.result_count
        ):
            raise ValueError("safe response evidence is incomplete")
        if self.claims_eligible_count is not None and (
            self.claims_eligible_count > self.result_count
        ):
            raise ValueError("eligible source count exceeds result count")
        if self.rejection_counts is not None and (
            not self.rejection_counts
            or sum(self.rejection_counts.values()) + (self.claims_eligible_count or 0)
            != self.result_count
        ):
            raise ValueError("source rejection counts are not total")
        if self.failure_class == "provider_results_empty" and (
            self.result_count != 0
            or self.durable_content_count != 0
            or self.claims_eligible_count != 0
            or self.rejection_counts is not None
        ):
            raise ValueError("empty response evidence is inconsistent")
        if self.failure_class == "provider_search_failed" and (
            self.result_count != 0
            or self.durable_content_count != 0
            or self.claims_eligible_count != 0
            or self.rejection_counts is not None
        ):
            raise ValueError("failed Search evidence is inconsistent")
        if self.failure_class in {
            "provider_extract_failed",
            "provider_results_without_durable_content",
        } and (
            self.result_count == 0
            or self.durable_content_count != 0
            or self.claims_eligible_count != 0
            or self.rejection_counts is None
        ):
            raise ValueError("non-durable response evidence is inconsistent")
        if self.failure_class == "intake_rejected_no_eligible_source" and (
            self.result_count == 0
            or self.durable_content_count == 0
            or self.claims_eligible_count != 0
            or self.rejection_counts is None
        ):
            raise ValueError("ineligible response evidence is inconsistent")
        if self.failure_class == "source_pack_validation_rejected" and (
            self.claims_eligible_count is not None or self.rejection_counts is not None
        ):
            raise ValueError("validation rejection cannot claim eligibility results")
        return self


class IntakeEventBinding(StrictModel):
    request_id: ContractId
    request_fingerprint: Sha256
    invocation_id: ContractId
    outcome: Literal["committed", "rejected"]
    source_id: Optional[ContractId] = None
    proposal_id: Optional[ContractId] = None
    reason_code: Optional[ContractId] = None
    source_acquisition_failure: SourceAcquisitionFailureEvidence | None = None

    @model_validator(mode="after")
    def identity_shape_is_unambiguous(self) -> "IntakeEventBinding":
        if self.source_id is not None and self.proposal_id is not None:
            raise ValueError("intake binding cannot name source and proposal")
        if self.outcome == "committed" and self.reason_code is not None:
            raise ValueError("committed intake binding cannot carry a rejection reason")
        if self.outcome == "rejected" and self.reason_code is None:
            raise ValueError("rejected intake binding requires a reason code")
        if self.outcome == "committed" and self.source_acquisition_failure is not None:
            raise ValueError("committed intake binding cannot carry failure evidence")
        if self.source_acquisition_failure is not None and (
            self.source_id is not None
            or self.proposal_id is not None
            or self.source_acquisition_failure.run_id == ""
        ):
            raise ValueError("source acquisition failure binding is ambiguous")
        return self


class CoreRunEventBinding(StrictModel):
    """Replay identity for one deterministic PR-4A domain effect."""

    request_id: ContractId
    request_fingerprint: Sha256
    effect_kind: Literal[
        "initialize",
        "source_acquisition_attempt_authorize",
        "invocation_start",
        "owned_artifact_acceptance",
        "claim_freeze",
        "audit_promotion",
        "gate_evaluation",
        "stage_transition",
        "integrity_contamination",
        "repair_start",
        "artifact_supersession",
        "repair_complete",
        "recovery_complete",
        "gate_repair_start",
        "run_head_transition",
        "run_successor_start",
        "finalize_render",
        "finalize_complete",
        "internal_approval",
        "delivery_authorization",
        "delivery_attempt",
        "delivery_result",
    ]
    primary_record_id: ContractId
    outcome: Literal["committed", "blocked"]


class SourceProposal(StrictModel):
    schema_id = "briefloop.source_proposal.v2"

    schema_version: Literal["briefloop.source_proposal.v2"]
    proposal_id: ContractId
    run_id: ContractId
    source_id: ContractId
    origin_type: Literal[SOURCE_ORIGIN_TYPES]
    acquisition_method: Literal[SOURCE_ACQUISITION_METHODS]
    material_kind: Literal[SOURCE_MATERIAL_KINDS]
    provider: Optional[ContractId] = None
    locator: SourceLocator
    title: CleanText
    publisher: Optional[CleanText] = None
    published_at: Optional[IsoDate] = None
    retrieved_at: IsoDateTime
    source_category: Literal[tuple(sorted(VALID_SOURCE_CATEGORIES))]
    retrieval_source_type: Literal[tuple(sorted(VALID_RETRIEVAL_SOURCE_TYPES))]
    underlying_evidence_type: Literal[tuple(sorted(VALID_UNDERLYING_EVIDENCE_TYPES))]
    raw_underlying_evidence_type: Optional[CleanText] = None
    content_sha256: Sha256
    content_media_type: MimeType
    raw_payload_sha256: Optional[Sha256] = None
    raw_payload_media_type: Optional[MimeType] = None
    source_manifest_sha256: Optional[Sha256] = None
    manifest_local_file: Optional[WorkspacePath] = None
    document_kind: Optional[CleanText] = None
    opened_at: Optional[IsoDateTime] = None
    resolved_at: Optional[IsoDateTime] = None

    @model_validator(mode="after")
    def raw_payload_fields_are_paired(self) -> "SourceProposal":
        if (self.raw_payload_sha256 is None) != (self.raw_payload_media_type is None):
            raise ValueError("raw payload hash and media type must be paired")
        if self.document_kind == "status_incident":
            if self.opened_at is None or self.published_at is not None:
                raise ValueError(
                    "status incident requires opened_at instead of published_at"
                )
        elif self.opened_at is not None or self.resolved_at is not None:
            raise ValueError("incident timestamps require status_incident")
        if self.resolved_at is not None and self.opened_at is None:
            raise ValueError("resolved_at requires opened_at")
        return self


class SourceCommitRequest(StrictModel):
    schema_id = "briefloop.source_commit_request.v2"

    schema_version: Literal["briefloop.source_commit_request.v2"]
    request_id: ContractId
    run_id: ContractId
    invocation_id: ContractId
    proposal_path: WorkspacePath
    content_path: WorkspacePath
    raw_payload_path: Optional[WorkspacePath] = None
    expected_store_revision: NonNegativeInt

    @model_validator(mode="after")
    def paths_bind_exactly_to_invocation(self) -> "SourceCommitRequest":
        parent = PurePosixPath("scratch") / self.invocation_id
        proposal = PurePosixPath(self.proposal_path)
        content = PurePosixPath(self.content_path)
        if proposal.parent != parent or proposal.name != "source_proposal.json":
            raise ValueError("source proposal path must be invocation scoped")
        if (
            content.parent != parent
            or content.stem != "source_content"
            or content.suffix not in {".json", ".md", ".txt", ".html", ".pdf", ".bin"}
        ):
            raise ValueError("source content path must be invocation scoped")
        if self.raw_payload_path is not None:
            raw = PurePosixPath(self.raw_payload_path)
            if (
                raw.parent != parent
                or raw.stem != "source_raw"
                or raw.suffix not in {".json", ".txt", ".bin"}
            ):
                raise ValueError("source raw payload path must be invocation scoped")
        return self


class SourcePackCommitMember(StrictModel):
    """One ordered source member inside an atomic intake transaction."""

    member_id: ContractId
    proposal_path: WorkspacePath
    content_path: WorkspacePath
    raw_payload_path: Optional[WorkspacePath] = None

    @model_validator(mode="after")
    def paths_are_member_scoped(self) -> "SourcePackCommitMember":
        proposal = PurePosixPath(self.proposal_path)
        content = PurePosixPath(self.content_path)
        expected_parent = proposal.parent
        if proposal.name != "source_proposal.json":
            raise ValueError("source pack proposal filename is invalid")
        if (
            content.parent != expected_parent
            or content.stem != "source_content"
            or content.suffix not in {".json", ".md", ".txt", ".html", ".pdf", ".bin"}
        ):
            raise ValueError("source pack content path is invalid")
        if self.raw_payload_path is not None:
            raw = PurePosixPath(self.raw_payload_path)
            if (
                raw.parent != expected_parent
                or raw.stem != "source_raw"
                or raw.suffix not in {".json", ".txt", ".bin"}
            ):
                raise ValueError("source pack raw payload path is invalid")
        return self


class SourcePackCommitRequest(StrictModel):
    """Atomically commit one complete, ordered set of source materials."""

    schema_id = "briefloop.source_pack_commit_request.v2"

    schema_version: Literal["briefloop.source_pack_commit_request.v2"]
    request_id: ContractId
    run_id: ContractId
    invocation_id: ContractId
    members: list[SourcePackCommitMember] = Field(min_length=1, max_length=256)
    manifest_path: Optional[WorkspacePath] = None
    expected_manifest_sha256: Optional[Sha256] = None
    expected_store_revision: NonNegativeInt

    @model_validator(mode="after")
    def pack_is_ordered_unique_and_invocation_scoped(self) -> "SourcePackCommitRequest":
        if (self.manifest_path is None) != (self.expected_manifest_sha256 is None):
            raise ValueError("source pack manifest path and hash must be paired")
        if self.manifest_path is not None:
            expected_manifest = (
                PurePosixPath("scratch") / self.invocation_id / "source_manifest.json"
            )
            if PurePosixPath(self.manifest_path) != expected_manifest:
                raise ValueError("source pack manifest path must be invocation scoped")
        member_ids = [item.member_id for item in self.members]
        if member_ids != sorted(set(member_ids)):
            raise ValueError("source pack members must be sorted and unique")
        paths: list[str] = []
        expected_root = PurePosixPath("scratch") / self.invocation_id / "sources"
        for item in self.members:
            proposal = PurePosixPath(item.proposal_path)
            if (
                proposal.parent.parent != expected_root
                or proposal.parent.name != item.member_id
            ):
                raise ValueError("source pack member path must be invocation scoped")
            paths.extend(
                value
                for value in (
                    item.proposal_path,
                    item.content_path,
                    item.raw_payload_path,
                )
                if value is not None
            )
        if len(paths) != len(set(paths)):
            raise ValueError("source pack paths must be unique")
        return self


class MultiTavilySourcePackCommitRequest(StrictModel):
    """Core-only atomic request for the schema18 multi-Tavily stage profile."""

    schema_id = "briefloop.multi_tavily_source_pack_commit_request.v1"

    schema_version: Literal["briefloop.multi_tavily_source_pack_commit_request.v1"]
    capacity_profile: Literal["multi_tavily_v2"]
    request_id: ContractId
    run_id: ContractId
    invocation_id: ContractId
    members: list[SourcePackCommitMember] = Field(min_length=1, max_length=800)
    manifest_path: WorkspacePath
    expected_manifest_sha256: Sha256
    expected_store_revision: NonNegativeInt

    @model_validator(mode="after")
    def pack_is_ordered_unique_and_invocation_scoped(
        self,
    ) -> "MultiTavilySourcePackCommitRequest":
        expected_manifest = (
            PurePosixPath("scratch") / self.invocation_id / "source_manifest.json"
        )
        if PurePosixPath(self.manifest_path) != expected_manifest:
            raise ValueError("multi-Tavily manifest path must be invocation scoped")
        member_ids = [item.member_id for item in self.members]
        if member_ids != sorted(set(member_ids)):
            raise ValueError("multi-Tavily members must be sorted and unique")
        expected_root = PurePosixPath("scratch") / self.invocation_id / "sources"
        paths: list[str] = []
        for item in self.members:
            proposal = PurePosixPath(item.proposal_path)
            if (
                proposal.parent.parent != expected_root
                or proposal.parent.name != item.member_id
            ):
                raise ValueError("multi-Tavily member path is not invocation scoped")
            paths.extend(
                value
                for value in (
                    item.proposal_path,
                    item.content_path,
                    item.raw_payload_path,
                )
                if value is not None
            )
        if len(paths) != len(set(paths)):
            raise ValueError("multi-Tavily member paths must be unique")
        return self


class CandidateClaimsProposal(StrictModel):
    schema_id = "briefloop.candidate_claims_proposal.v2"

    schema_version: Literal["briefloop.candidate_claims_proposal.v2"]
    proposal_id: ContractId
    run_id: ContractId
    created_at: IsoDateTime
    candidates: list[CandidateClaimItem] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_candidates(self) -> "CandidateClaimsProposal":
        identities = [item.candidate_id for item in self.candidates]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate candidate identity")
        return self


class ScreenedCandidatesProposal(StrictModel):
    schema_id = "briefloop.screened_candidates_proposal.v2"

    schema_version: Literal["briefloop.screened_candidates_proposal.v2"]
    proposal_id: ContractId
    run_id: ContractId
    candidate_claims_proposal_id: ContractId
    created_at: IsoDateTime
    decisions: list[ScreeningDecisionItem] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_decisions(self) -> "ScreenedCandidatesProposal":
        identities = [item.candidate_id for item in self.decisions]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate screening decision")
        return self


class ClaimDraftsProposal(StrictModel):
    schema_id = "briefloop.claim_drafts_proposal.v2"

    schema_version: Literal["briefloop.claim_drafts_proposal.v2"]
    proposal_id: ContractId
    run_id: ContractId
    screened_candidates_proposal_id: ContractId
    created_at: IsoDateTime
    drafts: list[ClaimDraftItem] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_drafts(self) -> "ClaimDraftsProposal":
        identities = [item.draft_id for item in self.drafts]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate draft identity")
        return self


class AuditProposal(StrictModel):
    schema_id = "briefloop.audit_proposal.v2"

    schema_version: Literal["briefloop.audit_proposal.v2"]
    proposal_id: ContractId
    run_id: ContractId
    artifact_id: ContractId
    artifact_revision: PositiveInt
    decision: Literal["pass", "warning", "fail"]
    created_at: IsoDateTime
    findings: list[AuditFindingItem] = Field(default_factory=list)


class ArtifactSubmitRequest(StrictModel):
    schema_id = "briefloop.artifact_submit_request.v2"

    schema_version: Literal["briefloop.artifact_submit_request.v2"]
    request_id: ContractId
    run_id: ContractId
    artifact_id: ContractId
    invocation_id: ContractId
    input_path: ScratchInputPath
    expected_store_revision: NonNegativeInt
    expected_artifact_revision: NonNegativeInt

    @model_validator(mode="after")
    def scratch_input_matches_invocation_and_artifact(self) -> "ArtifactSubmitRequest":
        path = PurePosixPath(self.input_path)
        expected_parent = PurePosixPath("scratch") / self.invocation_id
        if path.parent != expected_parent or path.name != f"{self.artifact_id}.json":
            raise ValueError(
                "artifact submission input must use its invocation scratch path"
            )
        return self


class WorkspaceRunHead(StrictModel):
    schema_id = "briefloop.workspace_run_head.v2"

    schema_version: Literal["briefloop.workspace_run_head.v2"]
    workspace_id: ContractId
    current_run_id: ContractId
    updated_at: IsoDateTime


class AcceptedSourceRecord(StrictModel):
    schema_id = "briefloop.accepted_source_record.v2"

    schema_version: Literal["briefloop.accepted_source_record.v2"]
    source_id: ContractId
    run_id: ContractId
    origin_type: Literal[SOURCE_ORIGIN_TYPES]
    acquisition_method: Literal[SOURCE_ACQUISITION_METHODS]
    material_kind: Literal[SOURCE_MATERIAL_KINDS]
    provider: Optional[ContractId] = None
    locator: SourceLocator
    title: CleanText
    publisher: Optional[CleanText] = None
    published_at: Optional[IsoDate] = None
    retrieved_at: IsoDateTime
    source_category: Literal[tuple(sorted(VALID_SOURCE_CATEGORIES))]
    retrieval_source_type: Literal[tuple(sorted(VALID_RETRIEVAL_SOURCE_TYPES))]
    underlying_evidence_type: Literal[tuple(sorted(VALID_UNDERLYING_EVIDENCE_TYPES))]
    raw_underlying_evidence_type: Optional[CleanText] = None
    content_sha256: Sha256
    content_size_bytes: NonNegativeInt
    content_media_type: MimeType
    content_blob_path: WorkspacePath
    content_artifact_id: ContractId
    content_artifact_revision: Literal[1]
    raw_payload_sha256: Optional[Sha256] = None
    raw_payload_size_bytes: Optional[NonNegativeInt] = None
    raw_payload_media_type: Optional[MimeType] = None
    raw_payload_blob_path: Optional[WorkspacePath] = None
    raw_payload_artifact_id: Optional[ContractId] = None
    raw_payload_artifact_revision: Optional[Literal[1]] = None
    source_manifest_sha256: Optional[Sha256] = None
    manifest_local_file: Optional[WorkspacePath] = None
    document_kind: Optional[CleanText] = None
    opened_at: Optional[IsoDateTime] = None
    resolved_at: Optional[IsoDateTime] = None
    claims_eligible: bool
    eligibility_reason: Literal[SOURCE_ELIGIBILITY_REASONS]
    invocation_id: ContractId
    acquisition_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256
    created_at: IsoDateTime

    @model_validator(mode="after")
    def source_record_shape_is_complete(self) -> "AcceptedSourceRecord":
        raw_values = (
            self.raw_payload_sha256,
            self.raw_payload_size_bytes,
            self.raw_payload_media_type,
            self.raw_payload_blob_path,
            self.raw_payload_artifact_id,
            self.raw_payload_artifact_revision,
        )
        if not (
            all(value is None for value in raw_values)
            or all(value is not None for value in raw_values)
        ):
            raise ValueError("raw payload fields must be all present or all absent")
        if self.claims_eligible != (
            self.eligibility_reason == "eligible_durable_source_content"
        ):
            raise ValueError("source eligibility reason does not match verdict")
        if self.document_kind == "status_incident":
            if self.opened_at is None or self.published_at is not None:
                raise ValueError(
                    "status incident requires opened_at instead of published_at"
                )
        elif self.opened_at is not None or self.resolved_at is not None:
            raise ValueError("incident timestamps require status_incident")
        if self.resolved_at is not None and self.opened_at is None:
            raise ValueError("resolved_at requires opened_at")
        return self


class AcceptedProposalRecord(StrictModel):
    schema_id = "briefloop.accepted_proposal_record.v2"

    schema_version: Literal["briefloop.accepted_proposal_record.v2"]
    proposal_id: ContractId
    run_id: ContractId
    proposal_kind: Literal["candidate", "screened", "claim_drafts", "audit"]
    artifact_id: ContractId
    artifact_revision: PositiveInt
    proposal_sha256: Sha256
    invocation_id: ContractId
    owner_stage_id: ContractId
    owner_role_id: ContractId
    parent_proposal_id: Optional[ContractId] = None
    target_artifact_id: Optional[ContractId] = None
    target_artifact_revision: Optional[PositiveInt] = None
    source_ids: list[ContractId] = Field(default_factory=list)
    accepted_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256
    created_at: IsoDateTime

    @model_validator(mode="after")
    def proposal_shape_matches_kind(self) -> "AcceptedProposalRecord":
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("duplicate direct source identity")
        if self.proposal_kind == "candidate":
            valid = (
                self.parent_proposal_id is None
                and self.target_artifact_id is None
                and self.target_artifact_revision is None
                and bool(self.source_ids)
            )
        elif self.proposal_kind in {"screened", "claim_drafts"}:
            valid = (
                self.parent_proposal_id is not None
                and self.target_artifact_id is None
                and self.target_artifact_revision is None
                and (self.proposal_kind == "claim_drafts" or not self.source_ids)
            )
        else:
            valid = (
                self.parent_proposal_id is None
                and self.target_artifact_id is not None
                and self.target_artifact_revision is not None
                and not self.source_ids
            )
        if not valid:
            raise ValueError("accepted proposal shape does not match its kind")
        return self


class ProposalSourceBinding(StrictModel):
    schema_id = "briefloop.proposal_source_binding.v2"

    schema_version: Literal["briefloop.proposal_source_binding.v2"]
    run_id: ContractId
    proposal_id: ContractId
    source_id: ContractId


class RunIdentity(StrictModel):
    schema_id = "briefloop.run_identity.v2"

    schema_version: Literal["briefloop.run_identity.v2"]
    run_id: ContractId
    workspace_id: ContractId
    runtime: RuntimeName
    created_at: IsoDateTime


class StageState(StrictModel):
    schema_id = "briefloop.stage_state.v2"

    schema_version: Literal["briefloop.stage_state.v2"]
    run_id: ContractId
    stage_id: ContractId
    status: Literal["pending", "ready", "complete", "blocked", "skipped"]
    revision: NonNegativeInt
    updated_at: IsoDateTime


ArtifactFormat = Literal[
    "json", "yaml", "markdown", "html", "docx", "pdf", "text", "binary"
]


class ArtifactRecord(StrictModel):
    schema_id = "briefloop.artifact_record.v2"

    schema_version: Literal["briefloop.artifact_record.v2"]
    run_id: ContractId
    artifact_id: ContractId
    current_revision: NonNegativeInt
    status: Literal[
        "expected", "missing", "present", "valid", "invalid", "blocked", "stale"
    ]
    required: bool
    path: WorkspacePath
    format: ArtifactFormat


class ArtifactIdentityRecord(StrictModel):
    schema_id = "briefloop.artifact_identity_record.v2"

    schema_version: Literal["briefloop.artifact_identity_record.v2"]
    run_id: ContractId
    artifact_id: ContractId
    required: bool
    initial_path: WorkspacePath
    format: ArtifactFormat
    accepted_transaction_id: ContractId


class ArtifactRevision(StrictModel):
    schema_id = "briefloop.artifact_revision.v2"

    schema_version: Literal["briefloop.artifact_revision.v2"]
    run_id: ContractId
    artifact_id: ContractId
    revision: PositiveInt
    path: WorkspacePath
    sha256: Sha256
    size_bytes: NonNegativeInt
    frozen: bool
    producer_kind: Literal["workflow_stage", "control_tool"]
    producer_id: ContractId
    created_at: IsoDateTime


class EventEnvelope(StrictModel):
    schema_id = "briefloop.event_envelope.v2"

    schema_version: Literal["briefloop.event_envelope.v2"]
    event_id: ContractId
    run_id: ContractId
    event_type: ContractId = Field(json_schema_extra=_event_type_json_schema)
    created_at: IsoDateTime
    actor: Literal["cli", "orchestrator", "runtime", "system"]
    transaction_id: Optional[ContractId] = None
    stage_id: Optional[ContractId] = None
    artifact_id: Optional[ContractId] = None
    decision: Optional[ContractId] = None
    reason: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    intake_binding: Optional[IntakeEventBinding] = None
    core_run_binding: Optional[CoreRunEventBinding] = None

    @field_validator("event_type")
    @classmethod
    def event_type_is_owned(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise PydanticCustomError(
                "unknown_event_type",
                "event type is not registered by the Event Log owner",
            )
        return value

    @model_validator(mode="after")
    def intake_binding_matches_event_type(self) -> "EventEnvelope":
        if self.event_type == "source_evidence_committed":
            valid = (
                self.intake_binding is not None
                and self.intake_binding.outcome == "committed"
                and self.intake_binding.source_id is not None
                and self.intake_binding.proposal_id is None
            )
        elif self.event_type == "role_proposal_committed":
            valid = (
                self.intake_binding is not None
                and self.intake_binding.outcome == "committed"
                and self.intake_binding.proposal_id is not None
                and self.intake_binding.source_id is None
            )
        elif self.event_type == "intake_rejected":
            valid = (
                self.intake_binding is not None
                and self.intake_binding.outcome == "rejected"
            )
        else:
            valid = self.intake_binding is None
        if not valid:
            raise ValueError("event intake binding does not match event type")
        if self.intake_binding is not None and self.core_run_binding is not None:
            raise ValueError("event cannot carry intake and core-run replay bindings")
        if self.core_run_binding is not None:
            allowed_core_events = {
                "initialize": {"run_initialized"},
                "source_acquisition_attempt_authorize": {
                    "source_acquisition_attempt_authorized"
                },
                "invocation_start": {"role_invocation_started"},
                "owned_artifact_acceptance": {"owned_artifact_accepted"},
                "claim_freeze": {"claim_ledger_frozen"},
                "audit_promotion": {"audit_proposal_promoted"},
                "gate_evaluation": {"quality_gate_checked"},
                "stage_transition": {
                    "stage_status_changed",
                    "stage_satisfied_by_topology",
                },
                "integrity_contamination": {"run_integrity_contaminated"},
                "gate_repair_start": {"gate_repair_started"},
                "repair_start": {"repair_started"},
                "artifact_supersession": {
                    "repair_stage_superseded",
                    "owned_artifact_accepted",
                },
                "repair_complete": {"repair_completed", "stage_status_changed"},
                "recovery_complete": {"decision_recorded"},
                "run_head_transition": {"run_reset"},
                "run_successor_start": {"run_successor_started"},
                "finalize_render": {"owned_artifact_accepted"},
                "finalize_complete": {
                    "stage_status_changed",
                    "run_archived",
                    "decision_recorded",
                },
                "internal_approval": {"human_approval_recorded"},
                "delivery_authorization": {"decision_recorded"},
                "delivery_attempt": {"delivery_attempted"},
                "delivery_result": {
                    "delivery_bundle_prepared",
                    "delivery_draft_created",
                    "delivery_succeeded",
                    "delivery_failed",
                    "decision_recorded",
                },
            }
            binding = self.core_run_binding
            if (
                self.event_type not in allowed_core_events[binding.effect_kind]
                or binding.request_id != self.transaction_id
                or (
                    binding.effect_kind == "integrity_contamination"
                    and binding.outcome != "blocked"
                )
                or (
                    binding.effect_kind != "integrity_contamination"
                    and binding.outcome != "committed"
                )
            ):
                raise ValueError("event core-run binding does not match event type")
        return self


class Invocation(StrictModel):
    schema_id = "briefloop.invocation.v2"

    schema_version: Literal["briefloop.invocation.v2"]
    invocation_id: ContractId
    run_id: ContractId
    role_id: ContractId
    runtime: RuntimeName
    status: Literal["pending", "active", "completed", "failed"]
    started_at: IsoDateTime
    completed_at: Optional[IsoDateTime] = None
    failure_reason: Optional[ContractId] = None

    @model_validator(mode="after")
    def completion_fields_match_status(self) -> "Invocation":
        if self.status in {"pending", "active"}:
            valid = self.completed_at is None and self.failure_reason is None
        elif self.status == "completed":
            valid = self.completed_at is not None and self.failure_reason is None
        else:
            valid = self.completed_at is not None and self.failure_reason is not None
        if not valid:
            raise ValueError("invocation completion fields do not match status")
        return self


class Approval(StrictModel):
    schema_id = "briefloop.approval.v2"

    schema_version: Literal["briefloop.approval.v2"]
    approval_id: ContractId
    run_id: ContractId
    mode: Literal[
        "internal_draft",
        "internal_management_review",
        "research_review",
        "ir_draft",
        "formal_release_candidate",
    ]
    role: Literal[
        "content_owner",
        "evidence_reviewer",
        "ir_owner",
        "legal_or_compliance_reviewer",
    ]
    decision: Literal["approve", "reject", "request_changes"]
    reason: ApprovalReason
    actor_id: ContractId
    recorded_at: IsoDateTime
    boundary: Literal[
        "internal_review_approval_records_only_not_public_release_authorization"
    ]
    event_id: ContractId

    @field_validator("role")
    @classmethod
    def role_is_required_for_mode(
        cls,
        value: str,
        info: ValidationInfo,
    ) -> str:
        mode = info.data.get("mode")
        if mode is None:
            return value
        # Import lazily so the strict contract package does not initialize the
        # product package while its own registry is still being imported. The
        # existing release-approval owner remains the mode/role authority.
        if value not in RELEASE_MODES[mode]["required_roles"]:
            raise PydanticCustomError(
                "approval_role_not_required",
                "approval role is not required for the selected mode",
            )
        return value


class Delivery(StrictModel):
    schema_id = "briefloop.delivery.v2"

    schema_version: Literal["briefloop.delivery.v2"]
    delivery_id: ContractId
    run_id: ContractId
    artifact_id: ContractId
    artifact_revision: PositiveInt
    approval_id: Optional[ContractId] = None
    status: Literal["bundle_prepared", "draft_created", "succeeded", "failed"]
    target: Literal["local", "feishu", "gmail"]
    channel: CleanText
    created_at: IsoDateTime
    completed_at: Optional[IsoDateTime] = None


class ArtifactRevisionReference(StrictModel):
    artifact_id: ContractId
    revision: PositiveInt


class ArtifactIdentityReference(StrictModel):
    artifact_id: ContractId


class RunOutputContract(StrictModel):
    schema_id = "briefloop.run_output_contract.v2"

    schema_version: Literal["briefloop.run_output_contract.v2"]
    output_extent: Literal["compact", "balanced", "detailed"]
    extent_catalog_id: Literal["briefloop.output_extent_catalog.v1"]
    body_length_basis: Literal["reader_body_excluding_source_reference_sections"]
    body_length_unit: Literal["word_equivalent_tokens"]
    resolved_minimum: int = Field(ge=1, le=100000)
    resolved_maximum: int = Field(ge=1, le=100000)

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> "RunOutputContract":
        if self.resolved_minimum > self.resolved_maximum:
            raise ValueError("resolved output contract bounds are not ordered")
        return self


class RunDirection(StrictModel):
    schema_id = "briefloop.run_direction.v2"

    schema_version: Literal["briefloop.run_direction.v2"]
    subject_name: CleanText
    industry_or_theme: Optional[CleanText] = None
    brief_title: CleanText
    report_type: Optional[ContractId] = None
    task_objective: CleanText
    audience: CleanText
    audience_profile: CleanText
    output_language: CleanText
    source_handling: CleanText
    cadence: CleanText
    focus_areas: list[CleanText]
    excluded_topics: list[CleanText]
    forbidden_sources: list[CleanText]
    source_profile: CleanText
    web_search_mode: Literal[
        "disabled",
        "runtime_tool",
        "external_api",
        "configure_later",
    ]
    search_backend: Optional[
        Literal["tavily", "exa", "brave", "firecrawl", "serper"]
    ] = None
    output_style: Optional[CleanText] = None
    output_formats: list[ContractId] = Field(min_length=1)
    report_date: IsoDate
    report_window_start: Optional[IsoDate] = None
    report_window_end: Optional[IsoDate] = None
    max_source_age_days: Optional[PositiveInt] = None
    selector_max_items: Optional[PositiveInt] = None
    target_terms: list[CleanText] = Field(min_length=1)
    required_section_intents: list[CleanText] = Field(default_factory=list)
    market_divergence_threshold_pct: Optional[float] = None
    output_contract: Optional[RunOutputContract] = None

    @model_validator(mode="after")
    def direction_is_canonical(self) -> "RunDirection":
        for field_name in (
            "focus_areas",
            "excluded_topics",
            "forbidden_sources",
            "output_formats",
            "target_terms",
            "required_section_intents",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique values")
        if (self.report_window_start is None) != (self.report_window_end is None):
            raise ValueError("report window boundaries must be paired")
        if (
            self.market_divergence_threshold_pct is not None
            and not (
                0
                < self.market_divergence_threshold_pct
                <= 100
            )
        ):
            raise ValueError(
                "market divergence threshold must be in (0, 100] percent"
            )
        if self.report_window_start is not None:
            if self.report_window_start > self.report_window_end:
                raise ValueError("report window is not ordered")
            if self.report_window_end > self.report_date:
                raise ValueError("report window cannot end after report date")
        if self.web_search_mode == "external_api":
            if self.search_backend is None:
                raise ValueError("external API search requires a backend")
        elif self.search_backend is not None:
            raise ValueError("search backend is allowed only for external API mode")
        if self.output_contract is not None:
            try:
                # Lazy import avoids initializing the Core package while the
                # strict contract registry itself is still being constructed.
                from multi_agent_brief.core_run_v2.output_contract import (
                    verify_output_contract,
                )

                verify_output_contract(self.output_contract, self.output_language)
            except ValueError as exc:
                raise ValueError(
                    "output contract catalog resolution is invalid"
                ) from exc
        return self


class ExecutionSourceManifestMember(StrictModel):
    """One Human-confirmed source row frozen before an authorized run starts."""

    source_id: ContractId
    input_path: WorkspacePath
    content_sha256: Sha256
    content_media_type: MimeType
    origin_type: Literal[SOURCE_ORIGIN_TYPES]
    acquisition_method: Literal[SOURCE_ACQUISITION_METHODS]
    material_kind: Literal[SOURCE_MATERIAL_KINDS]
    provider: Optional[ContractId] = None
    locator: SourceLocator
    title: CleanText
    publisher: Optional[CleanText] = None
    published_at: Optional[IsoDate] = None
    retrieved_at: IsoDateTime
    source_category: Literal[tuple(sorted(VALID_SOURCE_CATEGORIES))]
    retrieval_source_type: Literal[tuple(sorted(VALID_RETRIEVAL_SOURCE_TYPES))]
    underlying_evidence_type: Literal[tuple(sorted(VALID_UNDERLYING_EVIDENCE_TYPES))]
    raw_underlying_evidence_type: Optional[CleanText] = None
    document_kind: Optional[CleanText] = None
    opened_at: Optional[IsoDateTime] = None
    resolved_at: Optional[IsoDateTime] = None

    @model_validator(mode="after")
    def frozen_member_shape_is_explicit(self) -> "ExecutionSourceManifestMember":
        if not self.input_path.startswith("input/"):
            raise ValueError("execution source input must be under input")
        if self.document_kind == "status_incident":
            if self.opened_at is None or self.published_at is not None:
                raise ValueError(
                    "status incident requires opened_at instead of published_at"
                )
        elif self.opened_at is not None or self.resolved_at is not None:
            raise ValueError("incident timestamps require status_incident")
        if self.resolved_at is not None and self.opened_at is None:
            raise ValueError("resolved_at requires opened_at")
        return self


class ExecutionSourceManifest(StrictModel):
    """The canonical, Human-confirmed source set for an authorized run."""

    schema_id = "briefloop.execution_source_manifest.v2"

    schema_version: Literal["briefloop.execution_source_manifest.v2"]
    members: list[ExecutionSourceManifestMember] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def members_are_ordered_and_unique(self) -> "ExecutionSourceManifest":
        source_ids = [member.source_id for member in self.members]
        input_paths = [member.input_path for member in self.members]
        if source_ids != sorted(set(source_ids)):
            raise ValueError("execution source members must be sorted and unique")
        if len(input_paths) != len(set(input_paths)):
            raise ValueError("execution source input paths must be unique")
        return self


class MultiTavilyExecutionSourceManifest(StrictModel):
    """Core-only manifest for up to 800 successfully extracted Tavily URLs."""

    schema_id = "briefloop.multi_tavily_execution_source_manifest.v1"

    schema_version: Literal["briefloop.multi_tavily_execution_source_manifest.v1"]
    capacity_profile: Literal["multi_tavily_v2"]
    members: list[ExecutionSourceManifestMember] = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def members_are_tavily_ordered_and_unique(
        self,
    ) -> "MultiTavilyExecutionSourceManifest":
        source_ids = [member.source_id for member in self.members]
        input_paths = [member.input_path for member in self.members]
        if source_ids != sorted(set(source_ids)):
            raise ValueError("multi-Tavily members must be sorted and unique")
        if len(input_paths) != len(set(input_paths)):
            raise ValueError("multi-Tavily input paths must be unique")
        if any(
            member.provider != "tavily"
            or member.acquisition_method != "provider_extract"
            for member in self.members
        ):
            raise ValueError("multi-Tavily manifest contains a non-Tavily member")
        return self


class RunExecutionAuthorizationInput(StrictModel):
    """Strict bootstrap input; Core turns it into the sole durable authority."""

    schema_id = "briefloop.run_execution_authorization_input.v2"

    schema_version: Literal["briefloop.run_execution_authorization_input.v2"]
    completion_target: Literal["finalized_local"]
    source_manifest: ExecutionSourceManifest
    source_manifest_sha256: Sha256
    source_manifest_member_count: PositiveInt
    repair_budget: Literal[1]

    @model_validator(mode="after")
    def manifest_identity_is_exact(self) -> "RunExecutionAuthorizationInput":
        payload = self.source_manifest.model_dump(mode="json", exclude_unset=False)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != self.source_manifest_sha256:
            raise ValueError("execution source manifest hash mismatch")
        if len(self.source_manifest.members) != self.source_manifest_member_count:
            raise ValueError("execution source manifest member count mismatch")
        return self


class RunExecutionAuthorizationBootstrap(StrictModel):
    """The non-authoritative init-file pointer for an explicit manifest."""

    schema_id = "briefloop.run_execution_authorization_bootstrap.v2"

    schema_version: Literal["briefloop.run_execution_authorization_bootstrap.v2"]
    completion_target: Literal["finalized_local"]
    source_manifest_path: WorkspacePath
    source_manifest_sha256: Sha256
    source_manifest_member_count: PositiveInt
    repair_budget: Literal[1]

    @model_validator(mode="after")
    def manifest_path_is_explicit_input(self) -> "RunExecutionAuthorizationBootstrap":
        if not self.source_manifest_path.startswith("input/"):
            raise ValueError("execution source manifest must be under input")
        return self


class RunExecutionAuthorization(StrictModel):
    """Receipt-owned authorization for the automated local completion path."""

    schema_id = "briefloop.run_execution_authorization.v2"

    schema_version: Literal["briefloop.run_execution_authorization.v2"]
    authorization_id: ContractId
    run_id: ContractId
    workspace_id: ContractId
    run_contract_fingerprint: Sha256
    run_direction_fingerprint: Sha256
    completion_target: Literal["finalized_local"]
    source_manifest_artifact: ArtifactRevisionReference
    source_manifest_sha256: Sha256
    source_manifest_member_count: PositiveInt
    repair_budget: Literal[1]
    authorization_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256
    created_at: IsoDateTime


class RunSourceDiscoveryAuthorizationInput(StrictModel):
    """Strict bootstrap input for one Store-owned Tavily discovery authority."""

    schema_id = "briefloop.run_source_discovery_authorization_input.v2"

    schema_version: Literal["briefloop.run_source_discovery_authorization_input.v2"]
    route_id: Literal["web-search"]
    provider_id: Literal["tavily"]
    execution_owner: Literal["deterministic"]
    credential_env: Literal["TAVILY_API_KEY"]
    completion_target: Literal["finalized_local"]
    repair_budget: Literal[1]


class RunSourceDiscoveryAuthorizationBootstrap(StrictModel):
    """Non-authoritative init-file request for the discovery authority."""

    schema_id = "briefloop.run_source_discovery_authorization_bootstrap.v2"

    schema_version: Literal["briefloop.run_source_discovery_authorization_bootstrap.v2"]
    route_id: Literal["web-search"]
    provider_id: Literal["tavily"]
    execution_owner: Literal["deterministic"]
    credential_env: Literal["TAVILY_API_KEY"]
    completion_target: Literal["finalized_local"]
    repair_budget: Literal[1]


class RunSourceDiscoveryAuthorization(StrictModel):
    """Receipt-owned authority for one future, not-yet-executed source route."""

    schema_id = "briefloop.run_source_discovery_authorization.v2"

    schema_version: Literal["briefloop.run_source_discovery_authorization.v2"]
    authorization_id: ContractId
    run_id: ContractId
    workspace_id: ContractId
    run_contract_fingerprint: Sha256
    run_direction_fingerprint: Sha256
    runtime_source_plan_fingerprint: Sha256
    source_route_fingerprint: Sha256
    route_id: Literal["web-search"]
    provider_id: Literal["tavily"]
    execution_owner: Literal["deterministic"]
    credential_env: Literal["TAVILY_API_KEY"]
    completion_target: Literal["finalized_local"]
    repair_budget: Literal[1]
    authorization_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256
    created_at: IsoDateTime


class RunSourceAcquisitionAttemptAuthorization(StrictModel):
    """Receipt-owned ceiling for one frozen multi-task Tavily acquisition."""

    schema_id = "briefloop.run_source_acquisition_attempt_authorization.v2"

    schema_version: Literal["briefloop.run_source_acquisition_attempt_authorization.v2"]
    attempt_authorization_id: ContractId
    attempt_ordinal: PositiveInt
    run_id: ContractId
    workspace_id: ContractId
    discovery_authorization_id: ContractId
    run_contract_fingerprint: Sha256
    run_direction_fingerprint: Sha256
    runtime_source_plan_fingerprint: Sha256
    source_route_fingerprint: Sha256
    provider_request_fingerprint: Sha256
    provider_id: Literal["tavily"]
    route_id: Literal["web-search"]
    max_provider_calls: Annotated[int, Field(ge=4, le=80)]
    max_search_calls: Annotated[int, Field(ge=2, le=40)]
    max_extract_calls: Annotated[int, Field(ge=2, le=40)]
    max_extract_urls: Annotated[int, Field(ge=40, le=800)]
    provider_call_sequence: Literal[
        "primary_search_extract_then_conditional_backfill_search_extract"
    ]
    provider_cost_status: Literal["not_reported_acknowledged"]
    previous_attempt_authorization_id: ContractId | None = None
    human_request_id: ContractId
    authorization_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256
    created_at: IsoDateTime

    @model_validator(mode="after")
    def ordinal_chain_shape(self) -> "RunSourceAcquisitionAttemptAuthorization":
        if (self.attempt_ordinal == 1) != (
            self.previous_attempt_authorization_id is None
        ):
            raise ValueError("attempt predecessor does not match ordinal")
        if (
            self.max_provider_calls != self.max_search_calls + self.max_extract_calls
            or self.max_extract_calls != (self.max_extract_urls + 19) // 20
        ):
            raise ValueError("attempt call ceilings are inconsistent")
        return self


class SourceAcquisitionAttemptAuthorizeRequest(StrictModel):
    """Deterministic Core request carrying one explicit Human authorization."""

    schema_id = "briefloop.source_acquisition_attempt_authorize_request.v1"

    schema_version: Literal["briefloop.source_acquisition_attempt_authorize_request.v1"]
    request_id: ContractId
    run_id: ContractId
    expected_store_revision: NonNegativeInt
    expected_action_fingerprint: Sha256
    previous_attempt_authorization_id: ContractId
    human_confirmation: Literal[True]
    provider_cost_status: Literal["not_reported_acknowledged"]


def authorized_input_classification_bytes(
    manifest: ExecutionSourceManifest,
    sources: list[AcceptedSourceRecord],
) -> bytes:
    """Serialize the single deterministic classification for an authorized pack.

    This is deliberately a pure projection: the receipt-owned intake transaction
    remains the only writer of the artifact which carries these bytes.
    """

    payload = {
        "schema_version": "briefloop.input_classification.v2",
        "source_manifest_sha256": hashlib.sha256(
            json.dumps(
                manifest.model_dump(mode="json", exclude_unset=False),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "sources": [
            {
                "source_id": source.source_id,
                "content_sha256": source.content_sha256,
                "title": source.title,
                "locator": source.locator.model_dump(mode="json"),
                "manifest_local_file": source.manifest_local_file,
                "document_kind": source.document_kind,
                "opened_at": source.opened_at,
                "resolved_at": source.resolved_at,
            }
            for source in sorted(sources, key=lambda item: item.source_id)
        ],
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


class CoreRunInitializeRequest(StrictModel):
    schema_id = "briefloop.core_run_initialize_request.v2"

    schema_version: Literal["briefloop.core_run_initialize_request.v2"]
    request_id: ContractId
    workspace_id: ContractId
    run_id: ContractId
    runtime: RuntimeName
    expected_store_revision: Literal[0]
    run_direction: RunDirection
    workspace_config_sha256: Sha256
    sources_config_sha256: Sha256
    role_topology: RoleTopology
    gate_strictness: dict[GateId, bool]
    input_governance_required: bool
    runtime_adapter_binding: "RuntimeAdapterBinding"
    execution_authorization: "RunExecutionAuthorizationInput | None" = None
    source_discovery_authorization: "RunSourceDiscoveryAuthorizationInput | None" = None

    @field_validator("gate_strictness")
    @classmethod
    def exact_gate_set(cls, value: dict[str, bool]) -> dict[str, bool]:
        if set(value) != set(GATE_ID_VALUES):
            raise ValueError("gate strictness must name the exact Gate universe")
        return value


class WorkspaceControlStoreBootstrapV2(StrictModel):
    """One-time, non-authoritative initialization input for a fresh v2 Store."""

    schema_id = "briefloop.workspace_controlstore_bootstrap.v2"

    schema_version: Literal["briefloop.workspace_controlstore_bootstrap.v2"]
    workspace_id: ContractId
    run_id: ContractId
    runtime: Literal["codex"]
    role_topology: Literal["single_session", "default", "strict"]
    input_governance_required: bool
    gate_strictness: dict[GateId, bool]
    run_direction: RunDirection
    execution_authorization: "RunExecutionAuthorizationBootstrap | None" = None
    source_discovery_authorization: "RunSourceDiscoveryAuthorizationBootstrap | None" = None

    @field_validator("gate_strictness")
    @classmethod
    def exact_gate_set(cls, value: dict[str, bool]) -> dict[str, bool]:
        if set(value) != set(GATE_ID_VALUES):
            raise ValueError("gate strictness must name the exact Gate universe")
        return value


class RuntimeAdapterBinding(StrictModel):
    """Frozen, non-secret identity and capability boundary of one runtime kit."""

    schema_id = "briefloop.runtime_adapter_binding.v2"

    schema_version: Literal["briefloop.runtime_adapter_binding.v2"]
    run_id: ContractId
    runtime: RuntimeName
    adapter_id: ContractId
    adapter_version: ContractId
    briefloop_version: ContractId
    control_protocol: Literal["controlstore_v2"]
    action_protocol: Literal["core_run_next_action_v2"]
    proposal_protocol: Literal["pydantic_scratch_v2"]
    role_ids: list[ContractId] = Field(min_length=1)
    supported_role_topologies: list[RoleTopology] = Field(min_length=1)
    adapter_asset_sha256: dict[ContractId, Sha256]
    max_delegation_depth: PositiveInt
    max_threads: PositiveInt
    binding_fingerprint: Sha256

    @model_validator(mode="after")
    def canonical_binding(self) -> "RuntimeAdapterBinding":
        if self.role_ids != sorted(set(self.role_ids)):
            raise ValueError("role IDs must be sorted and unique")
        if self.supported_role_topologies != sorted(
            set(self.supported_role_topologies)
        ):
            raise ValueError("topology IDs must be sorted and unique")
        if list(self.adapter_asset_sha256) != sorted(self.adapter_asset_sha256):
            raise ValueError("adapter asset hashes must be sorted")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude_unset=False),
            field="binding_fingerprint",
        )
        if self.binding_fingerprint != expected:
            raise ValueError("runtime adapter fingerprint mismatch")
        return self


RUNTIME_SOURCE_ROUTE_IDS = (
    "api",
    "cached_package",
    "local_file",
    "manual",
    "rss",
    "runtime_tool",
    "web-search",
)
RUNTIME_SOURCE_WEB_PROVIDER_IDS = (
    "brave",
    "exa",
    "firecrawl",
    "serper",
    "tavily",
)
RUNTIME_SOURCE_GENERIC_PROVIDER_IDS = ("api", "runtime-tool")
RUNTIME_SOURCE_PROVIDER_IDS = (
    *RUNTIME_SOURCE_WEB_PROVIDER_IDS,
    *RUNTIME_SOURCE_GENERIC_PROVIDER_IDS,
)


class RuntimeWebSearchRequestSpec(StrictModel):
    """One exact, non-secret web request frozen at initialization."""

    schema_id = "briefloop.runtime_web_search_request_spec.v2"

    schema_version: Literal["briefloop.runtime_web_search_request_spec.v2"]
    query: CleanText
    domains: list[CleanText]
    max_results: Annotated[int, Field(ge=1, le=100)]
    recency_days: Optional[PositiveInt] = None

    @model_validator(mode="after")
    def canonical_request(self) -> "RuntimeWebSearchRequestSpec":
        if self.domains != sorted(set(self.domains)):
            raise ValueError("web request domains must be sorted and unique")
        if any(item != item.lower() for item in self.domains):
            raise ValueError("web request domains must be lowercase")
        return self


class RuntimeWebSearchAcquisitionSpec(StrictModel):
    """Generic external-web plan; its Tavily variant is historical read-only."""

    schema_id = "briefloop.runtime_web_search_acquisition_spec.v2"

    schema_version: Literal["briefloop.runtime_web_search_acquisition_spec.v2"]
    kind: Literal["web_search"]
    provider_id: Literal["tavily", "exa", "brave", "firecrawl", "serper"]
    requests: list[RuntimeWebSearchRequestSpec] = Field(min_length=1)
    acquisition_spec_fingerprint: Sha256

    @model_validator(mode="after")
    def canonical_spec(self) -> "RuntimeWebSearchAcquisitionSpec":
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude_unset=False),
            field="acquisition_spec_fingerprint",
        )
        if self.acquisition_spec_fingerprint != expected:
            raise ValueError("web acquisition fingerprint mismatch")
        return self


class RuntimeWebSearchBackfillSpecV1(StrictModel):
    """One deterministic fallback request for an under-covered primary task."""

    enabled: Literal[True]
    query: CleanText
    domains: list[CleanText]
    max_results: Literal[20]
    recency_days: Literal[30]
    search_depth: Literal["advanced"]

    @model_validator(mode="after")
    def canonical_domains(self) -> "RuntimeWebSearchBackfillSpecV1":
        if self.domains != sorted(set(self.domains)) or any(
            item != item.lower() for item in self.domains
        ):
            raise ValueError("backfill domains must be lowercase, sorted and unique")
        return self


class RuntimeWebSearchTaskSpecV3(StrictModel):
    """One frozen atomic discovery cell in the Solar Stock product plan."""

    schema_id = "briefloop.runtime_web_search_task_spec.v3"

    schema_version: Literal["briefloop.runtime_web_search_task_spec.v3"]
    task_id: ContractId
    task_category: Literal[
        "listed_company",
        "event_entity",
        "industry_prices",
        "us_policy",
        "china_policy",
        "capital_markets",
        "general",
    ]
    entity_id: CleanText | None = None
    query: CleanText
    topic: Literal["news", "general"]
    domains: list[CleanText]
    max_results: Literal[20]
    recency_days: Literal[7]
    search_depth: Literal["advanced"]
    minimum_extract_successes: Annotated[int, Field(ge=1, le=20)]
    backfill: RuntimeWebSearchBackfillSpecV1

    @model_validator(mode="after")
    def atomic_task_shape(self) -> "RuntimeWebSearchTaskSpecV3":
        if self.domains != sorted(set(self.domains)) or any(
            item != item.lower() for item in self.domains
        ):
            raise ValueError("task domains must be lowercase, sorted and unique")
        company_task = self.task_category in {"listed_company", "event_entity"}
        if company_task != (self.entity_id is not None):
            raise ValueError("entity identity does not match task category")
        return self


class RuntimeWebSearchAcquisitionSpecV3(StrictModel):
    """Multi-task, coverage-first Tavily acquisition plan."""

    schema_id = "briefloop.runtime_web_search_acquisition_spec.v3"

    schema_version: Literal["briefloop.runtime_web_search_acquisition_spec.v3"]
    kind: Literal["web_search_multi"]
    provider_id: Literal["tavily"]
    tasks: list[RuntimeWebSearchTaskSpecV3] = Field(min_length=1, max_length=20)
    max_primary_search_calls: Annotated[int, Field(ge=1, le=20)]
    max_backfill_search_calls: Annotated[int, Field(ge=1, le=20)]
    max_extract_calls: Annotated[int, Field(ge=1, le=40)]
    max_unique_urls: Annotated[int, Field(ge=40, le=800)]
    extract_batch_size: Literal[20]
    acquisition_spec_fingerprint: Sha256

    @model_validator(mode="after")
    def canonical_spec(self) -> "RuntimeWebSearchAcquisitionSpecV3":
        if [item.task_id for item in self.tasks] != sorted(
            {item.task_id for item in self.tasks}
        ):
            raise ValueError("multi-search tasks must be sorted and unique")
        expected_urls = min(800, len(self.tasks) * 40)
        if (
            self.max_primary_search_calls != len(self.tasks)
            or self.max_backfill_search_calls != len(self.tasks)
            or self.max_unique_urls != expected_urls
            or self.max_extract_calls != (expected_urls + 19) // 20
        ):
            raise ValueError("multi-search limits do not match the task matrix")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude_unset=False),
            field="acquisition_spec_fingerprint",
        )
        if self.acquisition_spec_fingerprint != expected:
            raise ValueError("multi-search acquisition fingerprint mismatch")
        return self


class RuntimeSourceSearchPlanV2(StrictModel):
    """Append-only identity for the exact atomic search plan actually executed."""

    schema_id = "briefloop.runtime_source_search_plan.v2"

    schema_version: Literal["briefloop.runtime_source_search_plan.v2"]
    search_plan_id: ContractId
    run_id: ContractId
    plan_revision: PositiveInt
    report_type: CleanText
    acquisition_spec: RuntimeWebSearchAcquisitionSpecV3
    task_count: Annotated[int, Field(ge=1, le=20)]
    acquisition_spec_fingerprint: Sha256
    record_event_id: ContractId
    accepted_transaction_id: ContractId
    created_at: IsoDateTime
    plan_fingerprint: Sha256

    @model_validator(mode="after")
    def search_plan_identity_is_exact(self) -> "RuntimeSourceSearchPlanV2":
        if (
            self.task_count != len(self.acquisition_spec.tasks)
            or self.acquisition_spec_fingerprint
            != self.acquisition_spec.acquisition_spec_fingerprint
        ):
            raise ValueError("runtime source search plan spec identity mismatch")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"plan_fingerprint"}),
            field="plan_fingerprint",
        )
        if self.plan_fingerprint != expected:
            raise ValueError("runtime source search plan fingerprint mismatch")
        return self


class RuntimeCachedPackageAcquisitionSpec(StrictModel):
    """Exact workspace-relative cached-package inputs."""

    schema_id = "briefloop.runtime_cached_package_acquisition_spec.v2"

    schema_version: Literal["briefloop.runtime_cached_package_acquisition_spec.v2"]
    kind: Literal["cached_package"]
    paths: list[WorkspacePath] = Field(min_length=1)
    formats: list[Literal["json", "md", "txt"]] = Field(min_length=1)
    acquisition_spec_fingerprint: Sha256

    @model_validator(mode="after")
    def canonical_spec(self) -> "RuntimeCachedPackageAcquisitionSpec":
        if len(self.paths) != len(set(self.paths)):
            raise ValueError("cached-package paths must be unique")
        if self.formats != sorted(set(self.formats)):
            raise ValueError("cached-package formats must be sorted and unique")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude_unset=False),
            field="acquisition_spec_fingerprint",
        )
        if self.acquisition_spec_fingerprint != expected:
            raise ValueError("cached-package acquisition fingerprint mismatch")
        return self


class RuntimeNewsApiAcquisitionSpec(StrictModel):
    """Exact non-secret NewsAPI query and filters."""

    schema_id = "briefloop.runtime_newsapi_acquisition_spec.v2"

    schema_version: Literal["briefloop.runtime_newsapi_acquisition_spec.v2"]
    kind: Literal["newsapi"]
    provider_id: Literal["newsapi"]
    query: CleanText
    terms: list[CleanText] = Field(min_length=1)
    max_results: Annotated[int, Field(ge=1, le=100)]
    start_date: Optional[IsoDate] = None
    end_date: Optional[IsoDate] = None
    sort_by: Optional[Literal["relevancy", "popularity", "publishedAt"]] = None
    language: Optional[Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")]] = None
    domains: list[CleanText]
    acquisition_spec_fingerprint: Sha256

    @model_validator(mode="after")
    def canonical_spec(self) -> "RuntimeNewsApiAcquisitionSpec":
        if len(self.terms) != len(set(self.terms)):
            raise ValueError("NewsAPI terms must be unique")
        if self.domains != sorted(set(self.domains)):
            raise ValueError("NewsAPI domains must be sorted and unique")
        if any(item != item.lower() for item in self.domains):
            raise ValueError("NewsAPI domains must be lowercase")
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("NewsAPI date bounds must be paired")
        if self.start_date is not None and self.start_date > self.end_date:
            raise ValueError("NewsAPI date bounds must be ordered")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude_unset=False),
            field="acquisition_spec_fingerprint",
        )
        if self.acquisition_spec_fingerprint != expected:
            raise ValueError("NewsAPI acquisition fingerprint mismatch")
        return self


RuntimeSourceAcquisitionSpec = Annotated[
    Union[
        RuntimeWebSearchAcquisitionSpec,
        RuntimeWebSearchAcquisitionSpecV3,
        RuntimeCachedPackageAcquisitionSpec,
        RuntimeNewsApiAcquisitionSpec,
    ],
    Field(discriminator="kind"),
]


class RuntimeSourceRouteBinding(StrictModel):
    """One safe source route frozen from initialization input."""

    schema_id = "briefloop.runtime_source_route_binding.v2"

    schema_version: Literal["briefloop.runtime_source_route_binding.v2"]
    route_id: Literal[
        "api",
        "cached_package",
        "local_file",
        "manual",
        "rss",
        "runtime_tool",
        "web-search",
    ]
    route_kind: Literal[
        "manual",
        "local_file",
        "rss",
        "external_api",
        "runtime_tool",
        "cached_package",
        "disabled",
    ]
    provider_id: Optional[
        Literal[
            "api",
            "runtime-tool",
            "tavily",
            "exa",
            "brave",
            "firecrawl",
            "serper",
        ]
    ] = None
    execution_owner: Literal["specialist", "deterministic", "human"]
    required: bool
    acquisition_spec: Optional[RuntimeSourceAcquisitionSpec] = None
    route_fingerprint: Sha256

    @model_validator(mode="after")
    def canonical_route(self) -> "RuntimeSourceRouteBinding":
        fixed_shapes: dict[str, tuple[set[str], str, set[str | None]]] = {
            "api": ({"external_api"}, "deterministic", {"api"}),
            "cached_package": ({"cached_package"}, "deterministic", {None}),
            "local_file": ({"local_file"}, "human", {None}),
            "manual": ({"manual"}, "human", {None}),
            "rss": ({"rss"}, "specialist", {None}),
            "runtime_tool": ({"runtime_tool"}, "specialist", {"runtime-tool"}),
        }
        if self.route_id == "web-search":
            expected_owner = {
                "manual": "human",
                "external_api": "deterministic",
                "runtime_tool": "specialist",
                "cached_package": "deterministic",
                "disabled": "human",
            }[self.route_kind]
            expected_providers: set[str | None]
            if self.route_kind == "external_api":
                expected_providers = set(RUNTIME_SOURCE_WEB_PROVIDER_IDS)
            elif self.route_kind == "runtime_tool":
                expected_providers = {"runtime-tool"}
            else:
                expected_providers = {None}
            if (
                self.execution_owner != expected_owner
                or self.provider_id not in expected_providers
            ):
                raise ValueError("source route owner/provider mismatch")
        else:
            route_kinds, owner, provider_ids = fixed_shapes[self.route_id]
            if (
                self.route_kind not in route_kinds
                or self.execution_owner != owner
                or self.provider_id not in provider_ids
            ):
                raise ValueError("source route owner/provider mismatch")
        if (self.execution_owner == "deterministic") != (
            self.acquisition_spec is not None
        ):
            raise ValueError("deterministic routes require one acquisition spec")
        if self.acquisition_spec is not None:
            if self.route_kind == "external_api" and self.route_id == "web-search":
                if (
                    self.acquisition_spec.kind not in {"web_search", "web_search_multi"}
                    or self.acquisition_spec.provider_id != self.provider_id
                ):
                    raise ValueError("source route acquisition spec mismatch")
            elif self.route_kind == "external_api" and self.route_id == "api":
                if self.acquisition_spec.kind != "newsapi":
                    raise ValueError("source route acquisition spec mismatch")
            elif self.route_kind == "cached_package":
                if self.acquisition_spec.kind != "cached_package":
                    raise ValueError("source route acquisition spec mismatch")
            else:
                raise ValueError("source route acquisition spec mismatch")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude_unset=False),
            field="route_fingerprint",
        )
        if self.route_fingerprint != expected:
            raise ValueError("source route fingerprint mismatch")
        return self


class RuntimeSourcePlanBinding(StrictModel):
    """Frozen non-secret source routing derived from exact sources.yaml bytes."""

    schema_id = "briefloop.runtime_source_plan_binding.v2"

    schema_version: Literal["briefloop.runtime_source_plan_binding.v2"]
    run_id: ContractId
    sources_config_sha256: Sha256
    web_search_mode: Literal[
        "manual",
        "disabled",
        "configure_later",
        "external_api",
        "runtime_tool",
        "cached_package",
    ]
    search_backend: Optional[
        Literal["tavily", "exa", "brave", "firecrawl", "serper"]
    ] = None
    routes: list[RuntimeSourceRouteBinding]
    source_plan_fingerprint: Sha256

    @model_validator(mode="after")
    def canonical_source_plan(self) -> "RuntimeSourcePlanBinding":
        if [item.route_id for item in self.routes] != sorted(
            {item.route_id for item in self.routes}
        ):
            raise ValueError("source routes must be sorted and unique")
        if self.web_search_mode == "external_api":
            if self.search_backend is None:
                raise ValueError("external API search requires a backend")
        elif self.search_backend is not None:
            raise ValueError("search backend is allowed only for external API mode")
        web_routes = [item for item in self.routes if item.route_id == "web-search"]
        if len(web_routes) > 1:
            raise ValueError("source plan has duplicate web-search route")
        if web_routes:
            web_route = web_routes[0]
            expected_kind = {
                "manual": "manual",
                "disabled": "disabled",
                "configure_later": "disabled",
                "external_api": "external_api",
                "runtime_tool": "runtime_tool",
                "cached_package": "cached_package",
            }[self.web_search_mode]
            if web_route.route_kind != expected_kind:
                raise ValueError("web-search route mode mismatch")
            if self.web_search_mode == "external_api":
                if web_route.provider_id != self.search_backend:
                    raise ValueError("web-search provider mismatch")
            elif self.search_backend is not None:
                raise ValueError("web-search provider mismatch")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude_unset=False),
            field="source_plan_fingerprint",
        )
        if self.source_plan_fingerprint != expected:
            raise ValueError("source plan fingerprint mismatch")
        return self


class CoreRunNextAction(StrictModel):
    """One deterministic, runtime-neutral legal next action."""

    schema_id = "briefloop.core_run_next_action.v2"

    schema_version: Literal["briefloop.core_run_next_action.v2"]
    run_id: ContractId
    store_revision: NonNegativeInt
    action_kind: Literal[
        "delegate", "deterministic", "human_decision", "blocked", "complete"
    ]
    effect_kind: ContractId
    stage_id: Optional[ContractId] = None
    role_id: Optional[ContractId] = None
    source_route_id: Optional[
        Literal[
            "api",
            "cached_package",
            "local_file",
            "manual",
            "rss",
            "runtime_tool",
            "web-search",
        ]
    ] = None
    source_provider_id: Optional[
        Literal[
            "api",
            "runtime-tool",
            "tavily",
            "exa",
            "brave",
            "firecrawl",
            "serper",
        ]
    ] = None
    source_acquisition_attempt_authorization_id: Optional[ContractId] = None
    reason_code: ContractId
    input_artifacts: list[ArtifactRevisionReference]
    request_schema_id: Optional[CleanText] = None
    adapter_binding_fingerprint: Sha256
    source_plan_fingerprint: Sha256
    action_fingerprint: Sha256

    @model_validator(mode="after")
    def canonical_action(self) -> "CoreRunNextAction":
        keys = [(item.artifact_id, item.revision) for item in self.input_artifacts]
        if keys != sorted(set(keys)):
            raise ValueError("input artifact references must be sorted and unique")
        if self.action_kind == "delegate":
            if (
                self.stage_id is None
                or self.role_id is None
                or self.request_schema_id is None
            ):
                raise ValueError(
                    "delegate action requires stage, role and request schema"
                )
        elif self.role_id is not None:
            raise ValueError("only delegate actions name a role")
        source_route_action = (
            self.stage_id == "source-discovery"
            and self.effect_kind
            in {
                "source_acquire",
                "source_input_required",
                "role_proposal",
                "role_unavailable",
                "source_discovery_acquisition_unavailable",
            }
            and (
                self.effect_kind != "role_unavailable"
                or self.source_route_id is not None
            )
            and (self.role_id == "source-provider" or self.action_kind != "delegate")
        )
        if source_route_action:
            all_routes_exhausted = (
                self.action_kind == "human_decision"
                and self.effect_kind == "source_input_required"
                and self.source_route_id is None
                and self.source_provider_id is None
            )
            if self.source_route_id is None and not all_routes_exhausted:
                raise ValueError("source route action requires a frozen route")
        elif self.source_route_id is not None or self.source_provider_id is not None:
            raise ValueError("only source route actions name source routing")
        if self.source_provider_id is not None and self.source_route_id is None:
            raise ValueError("source provider requires a source route")
        tavily_acquisition_family = (
            self.effect_kind == "source_acquire" and self.source_provider_id == "tavily"
        )
        recovery_family = self.effect_kind == "source_acquisition_recovery"
        exact_tavily_acquisition = (
            tavily_acquisition_family
            and self.action_kind == "deterministic"
            and self.stage_id == "source-discovery"
            and self.role_id is None
            and self.source_route_id == "web-search"
            and self.reason_code
            in {
                "deterministic_source_route_required",
                "active_discovery_source_acquire_requires_resume",
            }
            and self.request_schema_id == "briefloop.source_pack_commit_request.v2"
        )
        exact_recovery = (
            recovery_family
            and self.action_kind == "human_decision"
            and self.stage_id == "source-discovery"
            and self.role_id is None
            and self.source_route_id is None
            and self.source_provider_id is None
            and self.reason_code == "source_acquisition_recovery_decision_required"
            and self.request_schema_id
            == "briefloop.runtime_source_acquisition_recovery_request.v1"
        )
        if (tavily_acquisition_family and not exact_tavily_acquisition) or (
            recovery_family and not exact_recovery
        ):
            raise ValueError(
                "source acquisition lifecycle requires an exact action shape"
            )
        if exact_tavily_acquisition or exact_recovery:
            if self.source_acquisition_attempt_authorization_id is None:
                raise ValueError(
                    "source acquisition lifecycle requires exact attempt authority"
                )
        elif self.source_acquisition_attempt_authorization_id is not None:
            raise ValueError(
                "only Tavily acquisition lifecycle names attempt authority"
            )
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude_unset=False),
            field="action_fingerprint",
        )
        if self.action_fingerprint != expected:
            raise ValueError("next action fingerprint mismatch")
        return self


class RunContractBinding(StrictModel):
    schema_id = "briefloop.run_contract_binding.v2"

    schema_version: Literal["briefloop.run_contract_binding.v2"]
    run_id: ContractId
    workspace_id: ContractId
    runtime: RuntimeName
    stage_specs_schema: CleanText
    stage_specs_artifact: ArtifactRevisionReference
    stage_specs_sha256: Sha256
    artifact_contracts_schema: CleanText
    artifact_contracts_artifact: ArtifactRevisionReference
    artifact_contracts_sha256: Sha256
    policy_pack_schema: CleanText
    policy_pack_name: ContractId
    policy_pack_artifact: ArtifactRevisionReference
    policy_pack_sha256: Sha256
    runtime_adapter_artifact: ArtifactRevisionReference
    runtime_adapter_sha256: Sha256
    runtime_adapter_fingerprint: Sha256
    runtime_source_plan_artifact: ArtifactRevisionReference
    runtime_source_plan_sha256: Sha256
    runtime_source_plan_fingerprint: Sha256
    run_direction: RunDirection
    workspace_config_sha256: Sha256
    sources_config_sha256: Sha256
    role_topology: RoleTopology
    gate_strictness: dict[GateId, bool]
    input_governance_required: bool
    contract_fingerprint: Sha256
    created_at: IsoDateTime
    initialization_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @field_validator("gate_strictness")
    @classmethod
    def exact_binding_gate_set(cls, value: dict[str, bool]) -> dict[str, bool]:
        if set(value) != set(GATE_ID_VALUES):
            raise ValueError("gate strictness must name the exact Gate universe")
        return value


class InvocationStartRequest(StrictModel):
    schema_id = "briefloop.invocation_start_request.v2"

    schema_version: Literal["briefloop.invocation_start_request.v2"]
    request_id: ContractId
    run_id: ContractId
    stage_id: ContractId
    role_id: ContractId
    runtime: RuntimeName
    expected_store_revision: NonNegativeInt


class InvocationFailureRequest(StrictModel):
    schema_id = "briefloop.invocation_failure_request.v2"

    schema_version: Literal["briefloop.invocation_failure_request.v2"]
    request_id: ContractId
    run_id: ContractId
    invocation_id: ContractId
    reason_code: Literal[
        "dispatch_unavailable",
        "child_failed",
        "child_timed_out",
        "session_interrupted",
        "envelope_materialization_failed",
        "proposal_missing",
        "proposal_invalid",
    ]
    expected_store_revision: NonNegativeInt


class OwnedArtifactSubmitRequest(StrictModel):
    schema_id = "briefloop.owned_artifact_submit_request.v2"

    schema_version: Literal["briefloop.owned_artifact_submit_request.v2"]
    request_id: ContractId
    run_id: ContractId
    artifact_id: ContractId
    invocation_id: Optional[ContractId] = None
    producer_tool_id: Optional[ContractId] = None
    input_path: WorkspacePath
    expected_store_revision: NonNegativeInt
    expected_artifact_revision: NonNegativeInt
    expected_parent_artifact: Optional[ArtifactRevisionReference] = None

    @model_validator(mode="after")
    def producer_and_scratch_shape(self) -> "OwnedArtifactSubmitRequest":
        if self.invocation_id is None and self.producer_tool_id is None:
            raise ValueError("owned artifact requires an invocation or producer tool")
        path = PurePosixPath(self.input_path)
        if self.invocation_id is not None:
            if path.parent != PurePosixPath("scratch") / self.invocation_id:
                raise ValueError("owned artifact input must be invocation scoped")
        elif path.parts[:1] != ("scratch",):
            raise ValueError("owned artifact tool input must be scratch scoped")
        return self


class OwnedArtifactSubmissionRecord(StrictModel):
    schema_id = "briefloop.owned_artifact_submission_record.v2"

    schema_version: Literal["briefloop.owned_artifact_submission_record.v2"]
    submission_id: ContractId
    run_id: ContractId
    artifact_id: ContractId
    artifact_revision: PositiveInt
    artifact_sha256: Sha256
    owner_stage_id: ContractId
    owner_role_id: ContractId
    run_contract_fingerprint: Sha256
    invocation_id: Optional[ContractId] = None
    producer_tool_id: Optional[ContractId] = None
    parent_artifact: Optional[ArtifactRevisionReference] = None
    source_proposal_id: Optional[ContractId] = None
    canonical_workspace_path: WorkspacePath
    request_fingerprint: Sha256
    accepted_event_id: ContractId
    accepted_transaction_id: ContractId
    created_at: IsoDateTime

    @model_validator(mode="after")
    def producer_identity_present(self) -> "OwnedArtifactSubmissionRecord":
        if self.invocation_id is None and self.producer_tool_id is None:
            raise ValueError("owned artifact record requires a producer")
        return self


class ClaimRecord(StrictModel):
    schema_id = "briefloop.claim_record.v2"

    schema_version: Literal["briefloop.claim_record.v2"]
    run_id: ContractId
    claim_id: ContractId
    freeze_id: ContractId
    ordinal: PositiveInt
    claim_drafts_proposal_id: ContractId
    draft_id: ContractId
    statement: CleanText
    evidence_text: CleanText
    primary_source_id: ContractId
    claim_type: Literal["fact", "trend", "risk", "opportunity", "estimate"]
    confidence: Literal["medium"]
    requires_audit: Literal[True]
    epistemic_type: Literal["observed", "interpreted", "hypothesis"]
    evidence_relation: Literal["direct"]
    applicability_reason: None = None
    limitations: list[CleanText] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: IsoDateTime
    accepted_transaction_id: ContractId


class ClaimSourceBinding(StrictModel):
    schema_id = "briefloop.claim_source_binding.v2"

    schema_version: Literal["briefloop.claim_source_binding.v2"]
    run_id: ContractId
    claim_id: ContractId
    source_id: ContractId
    position: NonNegativeInt
    citation_role: Literal["primary", "additional"]
    claim_drafts_proposal_id: ContractId
    accepted_transaction_id: ContractId

    @model_validator(mode="after")
    def primary_position_matches_role(self) -> "ClaimSourceBinding":
        if (self.position == 0) != (self.citation_role == "primary"):
            raise ValueError("primary Claim source must occupy position zero")
        return self


class ClaimFreezeWarning(StrictModel):
    warning_type: Literal["lexical_duplicate_statement"]
    draft_ids: list[ContractId] = Field(min_length=2)

    @model_validator(mode="after")
    def draft_ids_are_canonical(self) -> "ClaimFreezeWarning":
        if self.draft_ids != sorted(set(self.draft_ids)):
            raise ValueError("warning draft identities must be sorted and unique")
        return self


class ClaimFreezeRecord(StrictModel):
    schema_id = "briefloop.claim_freeze_record.v2"

    schema_version: Literal["briefloop.claim_freeze_record.v2"]
    freeze_id: ContractId
    run_id: ContractId
    claim_drafts_proposal_id: ContractId
    screened_proposal_id: ContractId
    candidate_proposal_id: ContractId
    claim_drafts_artifact: ArtifactRevisionReference
    claim_drafts_sha256: Sha256
    ledger_artifact: ArtifactRevisionReference
    ledger_sha256: Sha256
    normalization_policy: Literal["sorted_sequential_v2"]
    run_contract_fingerprint: Sha256
    claim_count: PositiveInt
    warnings: list[ClaimFreezeWarning] = Field(default_factory=list)
    warning_count: NonNegativeInt
    frozen_at: IsoDateTime
    freeze_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def warning_count_matches(self) -> "ClaimFreezeRecord":
        if self.warning_count != len(self.warnings):
            raise ValueError("warning count does not match warnings")
        return self


class ClaimFreezeRequest(StrictModel):
    schema_id = "briefloop.claim_freeze_request.v2"

    schema_version: Literal["briefloop.claim_freeze_request.v2"]
    request_id: ContractId
    run_id: ContractId
    claim_drafts_proposal_id: ContractId
    expected_claim_drafts_artifact: ArtifactRevisionReference
    expected_store_revision: NonNegativeInt
    expected_ledger_revision: NonNegativeInt


class StageTransitionRecord(StrictModel):
    schema_id = "briefloop.stage_transition_record.v2"

    schema_version: Literal["briefloop.stage_transition_record.v2"]
    transition_id: ContractId
    run_id: ContractId
    stage_id: ContractId
    transition_kind: Literal[
        "initialize",
        "activate",
        "complete",
        "satisfied_by_topology",
        "repair_reopen",
        "gate_repair_reopen",
        "gate_repair_reset",
    ]
    requested_decision: Optional[Literal["continue"]] = None
    prior_status: Optional[
        Literal["pending", "ready", "complete", "blocked", "skipped"]
    ] = None
    prior_revision: Optional[NonNegativeInt] = None
    result_status: Literal["pending", "ready", "complete", "blocked", "skipped"]
    result_revision: NonNegativeInt
    reason: CleanText
    run_contract_fingerprint: Sha256
    actor: Literal["cli", "orchestrator", "runtime", "system"]
    producer_invocation_id: Optional[ContractId] = None
    producer_tool_id: Optional[ContractId] = None
    producer_result_status: Optional[Literal["pass"]] = None
    producer_result_fingerprint: Optional[Sha256] = None
    producer_implementation: Optional[ContractId] = None
    producer_version: Optional[ContractId] = None
    topology: Optional[RoleTopology] = None
    satisfaction_source_kind: Optional[Literal["stage", "role"]] = None
    satisfied_by_id: Optional[ContractId] = None
    created_at: IsoDateTime
    transition_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def transition_shape_is_complete(self) -> "StageTransitionRecord":
        if self.transition_kind == "initialize":
            if self.prior_status is not None or self.prior_revision is not None:
                raise ValueError("initial transition cannot have prior state")
            if self.result_revision != 0:
                raise ValueError("initial transition must create revision zero")
        else:
            if self.prior_status is None or self.prior_revision is None:
                raise ValueError("non-initial transition requires prior state")
            if self.result_revision != self.prior_revision + 1:
                raise ValueError("stage transition revision must advance once")
        topology_values = (
            self.topology,
            self.satisfaction_source_kind,
            self.satisfied_by_id,
        )
        if self.transition_kind == "satisfied_by_topology":
            if any(item is None for item in topology_values):
                raise ValueError("topology transition requires its source tuple")
        elif any(item is not None for item in topology_values):
            raise ValueError("non-topology transition cannot carry topology source")
        doctor_values = (
            self.producer_result_status,
            self.producer_result_fingerprint,
            self.producer_implementation,
            self.producer_version,
        )
        if any(item is not None for item in doctor_values) and not all(
            item is not None for item in doctor_values
        ):
            raise ValueError("deterministic producer result tuple is incomplete")
        return self


class StageArtifactBinding(StrictModel):
    schema_id = "briefloop.stage_artifact_binding.v2"

    schema_version: Literal["briefloop.stage_artifact_binding.v2"]
    run_id: ContractId
    transition_id: ContractId
    position: NonNegativeInt
    artifact_id: ContractId
    artifact_revision: PositiveInt
    artifact_sha256: Sha256
    usage: Literal["produced", "consumed", "topology_required"]
    accepted_transaction_id: ContractId


class StageGateBinding(StrictModel):
    schema_id = "briefloop.stage_gate_binding.v2"

    schema_version: Literal["briefloop.stage_gate_binding.v2"]
    run_id: ContractId
    transition_id: ContractId
    gate_id: GateId
    evaluation_id: ContractId
    accepted_transaction_id: ContractId


class StageCompleteRequest(StrictModel):
    schema_id = "briefloop.stage_complete_request.v2"

    schema_version: Literal["briefloop.stage_complete_request.v2"]
    request_id: ContractId
    run_id: ContractId
    stage_id: ContractId
    reason: CleanText
    expected_stage_revision: NonNegativeInt
    expected_store_revision: NonNegativeInt
    expected_artifact_revisions: list[ArtifactRevisionReference]
    expected_gate_evaluation_ids: list[ContractId]

    @model_validator(mode="after")
    def expected_bindings_are_unique(self) -> "StageCompleteRequest":
        artifact_keys = [
            (item.artifact_id, item.revision)
            for item in self.expected_artifact_revisions
        ]
        if len(artifact_keys) != len(set(artifact_keys)):
            raise ValueError("duplicate expected artifact revision")
        if len(self.expected_gate_evaluation_ids) != len(
            set(self.expected_gate_evaluation_ids)
        ):
            raise ValueError("duplicate expected Gate evaluation")
        return self


class GateFindingRecord(StrictModel):
    schema_id = "briefloop.gate_finding_record.v2"

    schema_version: Literal["briefloop.gate_finding_record.v2"]
    run_id: ContractId
    evaluation_id: ContractId
    finding_id: ContractId
    gate_id: GateId
    finding_type: ContractId
    severity: Literal["low", "medium", "high"]
    blocking_level: Literal["none", "warning", "blocking"]
    repair_owner: ContractId
    stage_id: Optional[ContractId] = None
    artifact_id: Optional[ContractId] = None
    claim_id: Optional[ContractId] = None
    source_id: Optional[ContractId] = None
    line_number: Optional[PositiveInt] = None
    description: CleanText
    recommendation: CleanText
    category: ContractId
    evidence_ref: CleanText
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    accepted_transaction_id: ContractId


class GateEvaluationRecord(StrictModel):
    schema_id = "briefloop.gate_evaluation_record.v2"

    schema_version: Literal["briefloop.gate_evaluation_record.v2"]
    evaluation_id: ContractId
    gate_batch_id: ContractId
    run_id: ContractId
    stage_id: Literal["auditor", "finalize"]
    gate_id: GateId
    policy_version: ContractId
    run_contract_fingerprint: Sha256
    status: Literal["pass", "warning", "fail", "unavailable", "invalid"]
    blocking: bool
    finding_ids: list[ContractId]
    checked_at: IsoDateTime
    producer_implementation: ContractId
    producer_version: ContractId
    report_artifact: ArtifactRevisionReference
    evaluation_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def findings_are_unique(self) -> "GateEvaluationRecord":
        if len(self.finding_ids) != len(set(self.finding_ids)):
            raise ValueError("duplicate Gate finding identity")
        expected_blocking = self.status in {"fail", "unavailable", "invalid"}
        if self.blocking != expected_blocking:
            raise ValueError("Gate blocking flag does not match its status")
        if self.status in {"unavailable", "invalid"} and not self.finding_ids:
            raise ValueError("negative Gate availability requires a finding")
        return self


class GateArtifactBinding(StrictModel):
    schema_id = "briefloop.gate_artifact_binding.v2"

    schema_version: Literal["briefloop.gate_artifact_binding.v2"]
    run_id: ContractId
    evaluation_id: ContractId
    position: NonNegativeInt
    artifact_id: ContractId
    artifact_revision: PositiveInt
    artifact_sha256: Sha256
    usage: Literal[
        "brief",
        "ledger",
        "analyst_snapshot",
        "screened_candidates",
        "reader_artifact",
        "audit_report",
    ]
    accepted_transaction_id: ContractId


class GateCheckRequest(StrictModel):
    schema_id = "briefloop.gate_check_request.v2"

    schema_version: Literal["briefloop.gate_check_request.v2"]
    request_id: ContractId
    run_id: ContractId
    stage_id: Literal["auditor", "finalize"]
    expected_store_revision: NonNegativeInt
    expected_report_artifact_revision: NonNegativeInt
    expected_input_artifacts: list[ArtifactRevisionReference]

    @model_validator(mode="after")
    def gate_inputs_are_unique(self) -> "GateCheckRequest":
        keys = [
            (item.artifact_id, item.revision) for item in self.expected_input_artifacts
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate Gate input artifact")
        return self


class AuditPromotionRequest(StrictModel):
    schema_id = "briefloop.audit_promotion_request.v2"

    schema_version: Literal["briefloop.audit_promotion_request.v2"]
    request_id: ContractId
    run_id: ContractId
    audit_proposal_id: ContractId
    expected_target_artifact: ArtifactRevisionReference
    expected_audit_report_revision: NonNegativeInt
    expected_store_revision: NonNegativeInt


class AuditReportArtifact(StrictModel):
    schema_id = "briefloop.audit_report_artifact.v2"

    schema_version: Literal["briefloop.audit_report_artifact.v2"]
    run_id: ContractId
    audit_proposal_id: ContractId
    target_artifact_id: ContractId
    target_artifact_revision: PositiveInt
    target_artifact_sha256: Sha256
    decision: Literal["pass", "warning", "fail"]
    findings: list[AuditFindingItem] = Field(default_factory=list)


class RunIntegrityRecord(StrictModel):
    schema_id = "briefloop.run_integrity_record.v2"

    schema_version: Literal["briefloop.run_integrity_record.v2"]
    run_id: ContractId
    integrity_revision: PositiveInt
    status: Literal["clean", "contaminated"]
    prior_integrity_revision: Optional[PositiveInt] = None
    affected_artifact_id: Optional[ContractId] = None
    affected_artifact_revision: Optional[PositiveInt] = None
    expected_workspace_path: Optional[WorkspacePath] = None
    expected_sha256: Optional[Sha256] = None
    observed_entry_kind: Optional[
        Literal["absent", "regular_file", "non_regular", "unsafe"]
    ] = None
    observed_sha256: Optional[Sha256] = None
    reason_code: Optional[ContractId] = None
    first_detected_at: Optional[IsoDateTime] = None
    first_detected_event_id: Optional[ContractId] = None
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def integrity_shape_matches_status(self) -> "RunIntegrityRecord":
        contamination = (
            self.affected_artifact_id,
            self.affected_artifact_revision,
            self.expected_workspace_path,
            self.expected_sha256,
            self.observed_entry_kind,
            self.reason_code,
            self.first_detected_at,
            self.first_detected_event_id,
        )
        if self.status == "clean":
            if (self.integrity_revision == 1) != (
                self.prior_integrity_revision is None
            ):
                raise ValueError(
                    "initial clean integrity has no predecessor; recovered clean integrity does"
                )
            if (
                self.integrity_revision > 1
                and self.prior_integrity_revision != self.integrity_revision - 1
            ):
                raise ValueError(
                    "recovered clean integrity must extend the prior revision"
                )
            if (
                any(item is not None for item in contamination)
                or self.observed_sha256 is not None
            ):
                raise ValueError("clean integrity cannot carry contamination data")
        else:
            if self.prior_integrity_revision is None or any(
                item is None for item in contamination
            ):
                raise ValueError("contaminated integrity requires complete lineage")
        return self


class IntegrityCheckRequest(StrictModel):
    schema_id = "briefloop.integrity_check_request.v2"

    schema_version: Literal["briefloop.integrity_check_request.v2"]
    request_id: ContractId
    run_id: ContractId
    expected_store_revision: NonNegativeInt


class RepairCycleRecord(StrictModel):
    schema_id = "briefloop.repair_cycle_record.v2"

    schema_version: Literal["briefloop.repair_cycle_record.v2"]
    repair_id: ContractId
    run_id: ContractId
    contamination_revision: PositiveInt
    owner_stage_id: ContractId
    permitted_artifact_ids: list[ContractId] = Field(min_length=1)
    reason_code: ContractId
    started_at: IsoDateTime
    start_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def scope_is_canonical(self) -> "RepairCycleRecord":
        if self.permitted_artifact_ids != sorted(set(self.permitted_artifact_ids)):
            raise ValueError("repair artifact scope must be sorted and unique")
        return self


class GateRepairCycleRecord(StrictModel):
    """One preauthorized, bounded editor-only Gate repair attempt."""

    schema_id = "briefloop.gate_repair_cycle_record.v2"

    schema_version: Literal["briefloop.gate_repair_cycle_record.v2"]
    gate_repair_id: ContractId
    run_id: ContractId
    authorization_id: ContractId
    repair_ordinal: Literal[1]
    source_gate_batch_id: ContractId
    source_stage_id: Literal["auditor", "finalize"]
    blocking_evaluation_ids: list[ContractId] = Field(min_length=1)
    blocking_findings: list["GateFindingReference"] = Field(min_length=1)
    repair_owner: Literal["editor"]
    target_artifact: ArtifactRevisionReference
    reopened_transition_ids: list[ContractId] = Field(min_length=1)
    started_at: IsoDateTime
    start_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def scope_is_canonical(self) -> "GateRepairCycleRecord":
        if self.blocking_evaluation_ids != sorted(set(self.blocking_evaluation_ids)):
            raise ValueError("Gate repair evaluations must be sorted and unique")
        finding_keys = [
            (item.evaluation_id, item.finding_id) for item in self.blocking_findings
        ]
        if finding_keys != sorted(set(finding_keys)):
            raise ValueError("Gate repair findings must be sorted and unique")
        if self.target_artifact.artifact_id != "audited_brief":
            raise ValueError("Gate repair target must be audited_brief")
        if self.reopened_transition_ids != sorted(set(self.reopened_transition_ids)):
            raise ValueError("Gate repair transitions must be sorted and unique")
        return self


class GateRepairArtifactBinding(StrictModel):
    """Bind the sole repaired audited-brief revision to its Gate repair cycle."""

    schema_id = "briefloop.gate_repair_artifact_binding.v2"

    schema_version: Literal["briefloop.gate_repair_artifact_binding.v2"]
    run_id: ContractId
    gate_repair_id: ContractId
    prior_artifact: ArtifactRevisionReference
    successor_artifact: ArtifactRevisionReference
    owned_artifact_submission_id: ContractId
    accepted_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def revision_advances_once(self) -> "GateRepairArtifactBinding":
        if (
            self.prior_artifact.artifact_id != "audited_brief"
            or self.successor_artifact.artifact_id != "audited_brief"
        ):
            raise ValueError("Gate repair binding must name audited_brief")
        if self.successor_artifact.revision != self.prior_artifact.revision + 1:
            raise ValueError("Gate repair artifact revision must advance once")
        return self


class GateRepairOutcomeRecord(StrictModel):
    """Terminal result of the sole bounded Gate repair attempt."""

    schema_id = "briefloop.gate_repair_outcome_record.v2"

    schema_version: Literal["briefloop.gate_repair_outcome_record.v2"]
    outcome_id: ContractId
    run_id: ContractId
    gate_repair_id: ContractId
    replacement_gate_batch_id: ContractId
    replacement_stage_id: Literal["auditor", "finalize"]
    evaluation_ids: list[ContractId] = Field(min_length=1)
    disposition: Literal["passed", "blocked"]
    completed_at: IsoDateTime
    completion_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def evaluations_are_canonical(self) -> "GateRepairOutcomeRecord":
        if self.evaluation_ids != sorted(set(self.evaluation_ids)):
            raise ValueError(
                "Gate repair outcome evaluations must be sorted and unique"
            )
        return self


class ArtifactSupersessionRecord(StrictModel):
    schema_id = "briefloop.artifact_supersession_record.v2"

    schema_version: Literal["briefloop.artifact_supersession_record.v2"]
    supersession_id: ContractId
    run_id: ContractId
    repair_id: ContractId
    mode: Literal["repair", "supersede", "revert"]
    prior_artifact: ArtifactRevisionReference
    successor_artifact: ArtifactRevisionReference
    reason_code: ContractId
    created_at: IsoDateTime
    accepted_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def revision_advances_once(self) -> "ArtifactSupersessionRecord":
        if self.prior_artifact.artifact_id != self.successor_artifact.artifact_id:
            raise ValueError("supersession must retain artifact identity")
        if self.successor_artifact.revision != self.prior_artifact.revision + 1:
            raise ValueError("supersession revision must advance once")
        return self


class RepairCompletionRecord(StrictModel):
    schema_id = "briefloop.repair_completion_record.v2"

    schema_version: Literal["briefloop.repair_completion_record.v2"]
    repair_completion_id: ContractId
    run_id: ContractId
    repair_id: ContractId
    contamination_revision: PositiveInt
    supersession_ids: list[ContractId] = Field(min_length=1)
    reopened_transition_ids: list[ContractId] = Field(min_length=1)
    completed_at: IsoDateTime
    completion_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def relations_are_unique(self) -> "RepairCompletionRecord":
        for values in (self.supersession_ids, self.reopened_transition_ids):
            if len(values) != len(set(values)):
                raise ValueError("duplicate repair completion relation")
        return self


class RecoveryCompletionRecord(StrictModel):
    schema_id = "briefloop.recovery_completion_record.v2"

    schema_version: Literal["briefloop.recovery_completion_record.v2"]
    recovery_id: ContractId
    run_id: ContractId
    repair_completion_id: ContractId
    contamination_revision: PositiveInt
    supersession_ids: list[ContractId] = Field(min_length=1)
    rerun_transition_ids: list[ContractId] = Field(min_length=1)
    gate_evaluation_ids: list[ContractId] = Field(default_factory=list)
    disposition: Literal["recovered_non_reference"]
    completed_at: IsoDateTime
    completion_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def relations_are_unique(self) -> "RecoveryCompletionRecord":
        for values in (
            self.supersession_ids,
            self.rerun_transition_ids,
            self.gate_evaluation_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("duplicate recovery completion relation")
        return self


class RunHeadTransitionRecord(StrictModel):
    schema_id = "briefloop.run_head_transition_record.v2"

    schema_version: Literal["briefloop.run_head_transition_record.v2"]
    head_transition_id: ContractId
    workspace_id: ContractId
    predecessor_run_id: ContractId
    successor_run_id: ContractId
    prior_workspace_revision: NonNegativeInt
    successor_workspace_revision: PositiveInt
    reason_code: Literal["run_reset", "human_started_successor"]
    successor_disposition: Literal["non_reference", "reference"]
    created_at: IsoDateTime
    transition_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def head_transition_advances_once(self) -> "RunHeadTransitionRecord":
        if self.predecessor_run_id == self.successor_run_id:
            raise ValueError("reset successor must be a distinct run")
        if self.successor_workspace_revision != self.prior_workspace_revision + 1:
            raise ValueError("workspace revision must advance once")
        if (self.reason_code, self.successor_disposition) not in {
            ("run_reset", "non_reference"),
            ("human_started_successor", "reference"),
        }:
            raise ValueError("head transition reason and disposition do not match")
        return self


class GuidanceReuseScopeV1(StrictModel):
    """Deterministic presentation-only compatibility scope for guidance reuse."""

    schema_id = "briefloop.guidance_reuse_scope.v1"

    schema_version: Literal["briefloop.guidance_reuse_scope.v1"]
    audience: CleanText
    audience_profile: CleanText
    output_language: CleanText
    output_style: Optional[CleanText] = None
    output_formats: list[ContractId] = Field(min_length=1)
    cadence: CleanText
    scope_fingerprint: Sha256

    @model_validator(mode="after")
    def scope_identity_is_exact(self) -> "GuidanceReuseScopeV1":
        if len(self.output_formats) != len(set(self.output_formats)):
            raise ValueError("duplicate guidance reuse output format")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"scope_fingerprint"}),
            field="scope_fingerprint",
        )
        if self.scope_fingerprint != expected:
            raise ValueError("guidance reuse scope fingerprint mismatch")
        return self


class RunGuidanceSelectionDecisionRecord(StrictModel):
    """One immutable deterministic decision for a guidance draft head."""

    schema_id = "briefloop.run_guidance_selection_decision_record.v1"

    schema_version: Literal["briefloop.run_guidance_selection_decision_record.v1"]
    decision_id: ContractId
    run_id: ContractId
    snapshot_id: ContractId
    source_run_id: ContractId
    guidance_id: ContractId
    draft_revision: PositiveInt
    status_revision_id: Optional[ContractId] = None
    provenance_kind: Literal["accepted_model_finding", "human_observation"] = (
        "accepted_model_finding"
    )
    assessment_result_id: Optional[ContractId] = None
    finding_id: Optional[ContractId] = None
    disposition_id: Optional[ContractId] = None
    result_fingerprint: Optional[Sha256] = None
    finding_fingerprint: Optional[Sha256] = None
    disposition_fingerprint: Optional[Sha256] = None
    observation_id: Optional[ContractId] = None
    observation_fingerprint: Optional[Sha256] = None
    draft_fingerprint: Sha256
    status_fingerprint: Optional[Sha256] = None
    source_scope_fingerprint: Sha256
    successor_scope_fingerprint: Sha256
    selected: bool
    reason_code: Literal[
        "approved_scope_match",
        "reuse_not_requested",
        "guidance_unapproved",
        "guidance_inactive",
        "guidance_superseded",
        "guidance_scope_mismatch",
    ]
    decision_fingerprint: Sha256

    @model_validator(mode="after")
    def decision_identity_is_exact(self) -> "RunGuidanceSelectionDecisionRecord":
        result_fields = (self.assessment_result_id, self.result_fingerprint)
        finding_fields = (self.finding_id, self.finding_fingerprint)
        disposition_fields = (self.disposition_id, self.disposition_fingerprint)
        observation_fields = (self.observation_id, self.observation_fingerprint)
        if self.provenance_kind == "accepted_model_finding":
            if not all(
                item is not None
                for item in (*result_fields, *finding_fields, *disposition_fields)
            ):
                raise ValueError("accepted model finding selection is incomplete")
            if any(item is not None for item in observation_fields):
                raise ValueError("model finding selection cannot bind observation")
        else:
            if not all(item is not None for item in observation_fields):
                raise ValueError("human observation selection is incomplete")
            if any(item is not None for item in finding_fields + disposition_fields):
                raise ValueError("human observation selection cannot bind finding")
            if any(item is not None for item in result_fields) and not all(
                item is not None for item in result_fields
            ):
                raise ValueError(
                    "human observation selection result binding is partial"
                )
        if self.selected != (self.reason_code == "approved_scope_match"):
            raise ValueError("guidance selection verdict does not match reason")
        if (self.status_revision_id is None) != (self.status_fingerprint is None):
            raise ValueError("guidance status identity must be total or absent")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"decision_fingerprint"}),
            field="decision_fingerprint",
        )
        if self.decision_fingerprint != expected:
            raise ValueError("guidance selection decision fingerprint mismatch")
        return self


class RunGuidanceSnapshotItemRecord(StrictModel):
    """One copied Human-authored guidance item frozen for a successor run."""

    schema_id = "briefloop.run_guidance_snapshot_item_record.v1"

    schema_version: Literal["briefloop.run_guidance_snapshot_item_record.v1"]
    item_id: ContractId
    run_id: ContractId
    snapshot_id: ContractId
    position: NonNegativeInt
    source_run_id: ContractId
    finalized_lineage_fingerprint: Sha256
    provenance_kind: Literal["accepted_model_finding", "human_observation"] = (
        "accepted_model_finding"
    )
    assessment_result_id: Optional[ContractId] = None
    assessment_result_fingerprint: Optional[Sha256] = None
    finding_id: Optional[ContractId] = None
    finding_fingerprint: Optional[Sha256] = None
    disposition_id: Optional[ContractId] = None
    disposition_fingerprint: Optional[Sha256] = None
    observation_id: Optional[ContractId] = None
    observation_fingerprint: Optional[Sha256] = None
    guidance_id: ContractId
    draft_revision: PositiveInt
    draft_fingerprint: Sha256
    status_revision_id: ContractId
    status_fingerprint: Sha256
    guidance_text: CleanText
    guidance_sha256: Sha256
    reuse_scope: GuidanceReuseScopeV1
    item_fingerprint: Sha256

    @model_validator(mode="after")
    def snapshot_item_identity_is_exact(self) -> "RunGuidanceSnapshotItemRecord":
        result_fields = (self.assessment_result_id, self.assessment_result_fingerprint)
        finding_fields = (self.finding_id, self.finding_fingerprint)
        disposition_fields = (self.disposition_id, self.disposition_fingerprint)
        observation_fields = (self.observation_id, self.observation_fingerprint)
        if self.provenance_kind == "accepted_model_finding":
            if not all(
                item is not None
                for item in (*result_fields, *finding_fields, *disposition_fields)
            ):
                raise ValueError("accepted model finding snapshot item is incomplete")
            if any(item is not None for item in observation_fields):
                raise ValueError("model finding snapshot item cannot bind observation")
        else:
            if not all(item is not None for item in observation_fields):
                raise ValueError("human observation snapshot item is incomplete")
            if any(item is not None for item in finding_fields + disposition_fields):
                raise ValueError("human observation snapshot item cannot bind finding")
            if any(item is not None for item in result_fields) and not all(
                item is not None for item in result_fields
            ):
                raise ValueError("human observation snapshot result binding is partial")
        if (
            self.guidance_sha256
            != hashlib.sha256(self.guidance_text.encode("utf-8")).hexdigest()
        ):
            raise ValueError("snapshot guidance text hash mismatch")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"item_fingerprint"}),
            field="item_fingerprint",
        )
        if self.item_fingerprint != expected:
            raise ValueError("guidance snapshot item fingerprint mismatch")
        return self


class RunGuidanceSnapshotRecord(StrictModel):
    """The one immutable approved-guidance context frozen with a successor run."""

    schema_id = "briefloop.run_guidance_snapshot_record.v1"

    schema_version: Literal["briefloop.run_guidance_snapshot_record.v1"]
    snapshot_id: ContractId
    workspace_id: ContractId
    run_id: ContractId
    predecessor_run_id: ContractId
    reuse_requested: bool
    successor_direction_fingerprint: Sha256
    successor_run_contract_fingerprint: Sha256
    candidate_set_fingerprint: Sha256
    selected_item_ids: list[ContractId]
    decision_ids: list[ContractId]
    selected_count: NonNegativeInt
    omitted_count: NonNegativeInt
    snapshot_fingerprint: Sha256
    snapshot_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def snapshot_identity_is_exact(self) -> "RunGuidanceSnapshotRecord":
        if self.predecessor_run_id == self.run_id:
            raise ValueError("guidance snapshot predecessor must be distinct")
        if len(self.selected_item_ids) != len(set(self.selected_item_ids)):
            raise ValueError("duplicate guidance snapshot item identity")
        if len(self.decision_ids) != len(set(self.decision_ids)):
            raise ValueError("duplicate guidance decision identity")
        if self.selected_count != len(self.selected_item_ids):
            raise ValueError("guidance selected count mismatch")
        if self.selected_count + self.omitted_count != len(self.decision_ids):
            raise ValueError("guidance decision count mismatch")
        if not self.reuse_requested and self.selected_count != 0:
            raise ValueError("guidance reuse opt-out cannot select items")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"snapshot_fingerprint"}),
            field="snapshot_fingerprint",
        )
        if self.snapshot_fingerprint != expected:
            raise ValueError("guidance snapshot fingerprint mismatch")
        return self


class FinalizeRenderRecord(StrictModel):
    schema_id = "briefloop.finalize_render_record.v2"

    schema_version: Literal["briefloop.finalize_render_record.v2"]
    render_id: ContractId
    run_id: ContractId
    audit_proposal_id: ContractId
    audited_brief: ArtifactRevisionReference
    audit_report: ArtifactRevisionReference
    reader_artifacts: list[ArtifactRevisionReference] = Field(min_length=1)
    reader_clean_status: Literal["pass"]
    policy_result_fingerprint: Sha256
    run_contract_fingerprint: Sha256
    created_at: IsoDateTime
    render_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def reader_artifacts_are_unique(self) -> "FinalizeRenderRecord":
        keys = [(item.artifact_id, item.revision) for item in self.reader_artifacts]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate reader artifact revision")
        return self


class FinalizationRecord(StrictModel):
    schema_id = "briefloop.finalization_record.v2"

    schema_version: Literal["briefloop.finalization_record.v2"]
    finalization_id: ContractId
    run_id: ContractId
    render_id: ContractId
    finalize_transition_id: ContractId
    finalize_gate_batch_id: ContractId
    finalize_gate_evaluation_ids: list[ContractId] = Field(min_length=1)
    recovery_id: Optional[ContractId] = None
    integrity_revision: PositiveInt
    finalized_at: IsoDateTime
    finalization_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256

    @field_validator("finalize_gate_evaluation_ids")
    @classmethod
    def gate_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate finalize Gate evaluation")
        return value


class RunArchiveRecord(StrictModel):
    schema_id = "briefloop.run_archive_record.v2"

    schema_version: Literal["briefloop.run_archive_record.v2"]
    archive_id: ContractId
    run_id: ContractId
    finalization_id: ContractId
    archive_artifact: ArtifactRevisionReference
    manifest_sha256: Sha256
    included_count: PositiveInt
    created_at: IsoDateTime
    archive_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256


class RunArchiveArtifactBinding(StrictModel):
    schema_id = "briefloop.run_archive_artifact_binding.v2"

    schema_version: Literal["briefloop.run_archive_artifact_binding.v2"]
    run_id: ContractId
    archive_id: ContractId
    position: NonNegativeInt
    artifact_id: ContractId
    artifact_revision: PositiveInt
    artifact_sha256: Sha256
    usage: Literal["control", "evidence", "workflow", "reader", "gate"]
    accepted_transaction_id: ContractId


class PackageReadyRecord(StrictModel):
    schema_id = "briefloop.package_ready_record.v2"

    schema_version: Literal["briefloop.package_ready_record.v2"]
    package_id: ContractId
    run_id: ContractId
    finalization_id: ContractId
    archive_id: ContractId
    package_manifest_artifact: ArtifactRevisionReference
    package_manifest_sha256: Sha256
    artifact_count: PositiveInt
    created_at: IsoDateTime
    package_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256


class PackageArtifactBinding(StrictModel):
    schema_id = "briefloop.package_artifact_binding.v2"

    schema_version: Literal["briefloop.package_artifact_binding.v2"]
    run_id: ContractId
    package_id: ContractId
    position: NonNegativeInt
    artifact_id: ContractId
    artifact_revision: PositiveInt
    artifact_sha256: Sha256
    usage: Literal["reader", "archive", "manifest"]
    accepted_transaction_id: ContractId


class ApprovalPackageBinding(StrictModel):
    schema_id = "briefloop.approval_package_binding.v2"

    schema_version: Literal["briefloop.approval_package_binding.v2"]
    run_id: ContractId
    approval_id: ContractId
    package_id: ContractId
    accepted_transaction_id: ContractId


class DeliveryAuthorizationRecord(StrictModel):
    schema_id = "briefloop.delivery_authorization_record.v2"

    schema_version: Literal["briefloop.delivery_authorization_record.v2"]
    authorization_id: ContractId
    run_id: ContractId
    package_id: ContractId
    prior_authorization_id: Optional[ContractId] = None
    approval_mode: Literal[
        "internal_draft",
        "internal_management_review",
        "research_review",
        "ir_draft",
        "formal_release_candidate",
    ]
    retry_of_attempt_id: Optional[ContractId] = None
    purpose: Literal["initial_attempt", "retry_attempt", "result_reconciliation"]
    decision: Literal["authorize", "deny"]
    target: Literal["local", "feishu", "gmail"]
    channel: CleanText
    recipient_fingerprint: Sha256
    actor_id: ContractId
    reason: ApprovalReason
    recorded_at: IsoDateTime
    authorization_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256


class DeliveryAttemptRecord(StrictModel):
    schema_id = "briefloop.delivery_attempt_record.v2"

    schema_version: Literal["briefloop.delivery_attempt_record.v2"]
    attempt_id: ContractId
    run_id: ContractId
    package_id: ContractId
    authorization_id: ContractId
    target: Literal["local", "feishu", "gmail"]
    channel: CleanText
    recipient_fingerprint: Sha256
    connector_operation_id: ContractId
    connector_request_fingerprint: Sha256
    created_at: IsoDateTime
    attempt_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256


class DeliveryResultRecord(StrictModel):
    schema_id = "briefloop.delivery_result_record.v2"

    schema_version: Literal["briefloop.delivery_result_record.v2"]
    result_id: ContractId
    run_id: ContractId
    attempt_id: ContractId
    prior_result_id: Optional[ContractId] = None
    reconciliation_authorization_id: Optional[ContractId] = None
    status: Literal[
        "bundle_prepared", "draft_created", "succeeded", "failed", "outcome_unknown"
    ]
    adapter_id: ContractId
    adapter_version: ContractId
    connector_operation_id: ContractId
    evidence_sha256: Sha256
    evidence_artifact: Optional[ArtifactRevisionReference] = None
    recorded_at: IsoDateTime
    result_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256


class DeliveryResultObservation(StrictModel):
    """Value-free connector observation parsed from exact scratch bytes."""

    schema_id = "briefloop.delivery_result_observation.v2"

    schema_version: Literal["briefloop.delivery_result_observation.v2"]
    attempt_id: ContractId
    adapter_id: ContractId
    adapter_version: ContractId
    connector_operation_id: ContractId
    status: Literal[
        "bundle_prepared", "draft_created", "succeeded", "failed", "outcome_unknown"
    ]
    evidence_sha256: Sha256
    diagnostic_code: ContractId
    connector_request_fingerprint: Sha256

    @model_validator(mode="after")
    def value_free_diagnostic_matches_status(self) -> "DeliveryResultObservation":
        if self.diagnostic_code != self.status:
            raise ValueError("delivery diagnostic must be the fixed status code")
        return self


def _canonical_json_sha256(value: object) -> str:
    """Return the exact JSON identity used by Store-owned opaque subcontracts."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("canonical JSON identity is invalid") from exc
    return hashlib.sha256(encoded).hexdigest()


class ReaderReviewAssessmentInput(StrictModel):
    """The complete Human-supplied command for one paid Reader Review."""

    schema_id: ClassVar[str] = "briefloop.reader_review_assessment_input.v1"

    schema_version: Literal["briefloop.reader_review_assessment_input.v1"]
    human_actor_id: ContractId
    human_request_id: ContractId
    disclosure_confirmed: Literal[True]
    messages_endpoint: CleanText
    requested_model_id: CleanText
    model_version: CleanText
    expected_model_identity: CleanText
    public_safe_egress_attested: Literal[True]
    cost_status: Literal["not_measured"]


_READER_REVIEW_PROFILE_BINDINGS = {
    "management_brief_en_v1": ("management_monthly", "en"),
    # ``zh`` is the ordinary Init Web RunDirection value.  The evaluator's
    # internal Language contract remains the canonical ``zh-CN`` below the
    # product boundary; Store records retain the user-facing direction value.
    "industry_weekly_zh_v1": ("industry_weekly", "zh"),
}


class PostFinalAssessmentPolicyRevision(StrictModel):
    """One Human-recorded, non-secret advisory assessment policy revision."""

    schema_id = "briefloop.post_final_assessment_policy_revision.v2"
    reader_review_schema_id: ClassVar[str] = (
        "briefloop.post_final_assessment_policy_revision.v3"
    )

    schema_version: Literal[
        "briefloop.post_final_assessment_policy_revision.v2",
        "briefloop.post_final_assessment_policy_revision.v3",
    ]
    policy_revision_id: ContractId
    run_id: ContractId
    previous_policy_revision_id: Optional[ContractId] = None
    enabled: bool
    auto_run: bool
    auto_open: bool
    adapter_id: Literal["anthropic_messages_v1"]
    messages_endpoint: CleanText
    messages_endpoint_sha256: Sha256
    requested_model_id: CleanText
    model_version: CleanText
    expected_model_identity: CleanText
    profile_id: Literal[
        "research_design_report_zh_v1",
        "management_brief_en_v1",
        "industry_weekly_zh_v1",
    ]
    instrument_config: dict[str, JsonValue]
    instrument_config_sha256: Sha256
    bounded_context: dict[str, JsonValue]
    bounded_context_sha256: Sha256
    temperature: Literal[1.0]
    top_p: Literal[1.0]
    max_provider_calls: PositiveInt
    max_total_input_tokens: PositiveInt
    max_total_output_tokens: PositiveInt
    max_output_tokens_per_call: PositiveInt
    wall_timeout_seconds: Literal[60]
    public_safe_egress_attested: bool
    egress_scope: Literal["public_safe_report"]
    human_actor_id: ContractId
    human_request_id: ContractId
    recorded_at: IsoDateTime
    policy_event_id: ContractId
    accepted_transaction_id: ContractId
    policy_fingerprint: Sha256
    assessment_kind: Optional[Literal["reader_review"]] = None
    report_type: Optional[Literal["management_monthly", "industry_weekly"]] = None
    language: Optional[Literal["en", "zh"]] = None
    disclosure_confirmed: Optional[Literal[True]] = None
    cost_status: Optional[Literal["not_measured"]] = None

    @model_validator(mode="after")
    def policy_identity_is_exact(self) -> "PostFinalAssessmentPolicyRevision":
        reader_fields = {
            "assessment_kind",
            "report_type",
            "language",
            "disclosure_confirmed",
            "cost_status",
        }
        if (
            self.messages_endpoint_sha256
            != hashlib.sha256(self.messages_endpoint.encode("utf-8")).hexdigest()
            or self.instrument_config_sha256
            != _canonical_json_sha256(self.instrument_config)
            or self.bounded_context.get("context_sha256") != self.bounded_context_sha256
            or self.max_output_tokens_per_call > self.max_total_output_tokens
            or (self.enabled and not self.public_safe_egress_attested)
        ):
            raise ValueError("post-final assessment policy identity is invalid")
        if self.schema_version == self.schema_id:
            if self.profile_id != "research_design_report_zh_v1" or any(
                getattr(self, field) is not None for field in reader_fields
            ):
                raise ValueError("historical post-final policy fields are invalid")
            fingerprint_payload = self.model_dump(
                mode="json",
                exclude={"policy_fingerprint", *reader_fields},
            )
        else:
            binding = _READER_REVIEW_PROFILE_BINDINGS.get(self.profile_id)
            if (
                binding is None
                or self.assessment_kind != "reader_review"
                or (self.report_type, self.language) != binding
                or self.disclosure_confirmed is not True
                or self.cost_status != "not_measured"
                or not self.enabled
                or self.auto_run
                or self.auto_open
            ):
                raise ValueError("Reader Review policy binding is invalid")
            fingerprint_payload = self.model_dump(
                mode="json", exclude={"policy_fingerprint"}
            )
        expected = _contract_fingerprint(
            fingerprint_payload,
            field="policy_fingerprint",
        )
        if self.policy_fingerprint != expected:
            raise ValueError("post-final assessment policy fingerprint mismatch")
        return self


class PostFinalAssessmentRequestRecord(StrictModel):
    """One durable assessment claim in an exact finalized-local lineage series."""

    schema_id = "briefloop.post_final_assessment_request_record.v2"
    series_schema_id: ClassVar[str] = (
        "briefloop.post_final_assessment_request_record.v3"
    )
    reader_review_schema_id: ClassVar[str] = (
        "briefloop.post_final_assessment_request_record.v4"
    )

    schema_version: Literal[
        "briefloop.post_final_assessment_request_record.v2",
        "briefloop.post_final_assessment_request_record.v3",
        "briefloop.post_final_assessment_request_record.v4",
    ]
    assessment_request_id: ContractId
    run_id: ContractId
    finalized_facts_fingerprint: Sha256
    finalized_lineage_fingerprint: Sha256
    report_artifact_id: ContractId
    report_revision: PositiveInt
    report_sha256: Sha256
    finalization_id: ContractId
    finalization_receipt_id: ContractId
    finalize_gate_batch_id: ContractId
    policy_revision_id: ContractId
    policy_fingerprint: Sha256
    adapter_id: Literal["anthropic_messages_v1"]
    messages_endpoint_sha256: Sha256
    requested_model_id: CleanText
    expected_model_identity: CleanText
    profile_id: Literal[
        "research_design_report_zh_v1",
        "management_brief_en_v1",
        "industry_weekly_zh_v1",
    ]
    instrument_config_sha256: Sha256
    bounded_context_sha256: Sha256
    input_binding_sha256: Sha256
    assessment_plan_sha256: Sha256
    ordered_prompt_request_sha256s: list[Sha256] = Field(min_length=1, max_length=9)
    prompt_count: PositiveInt
    provider_call_ceiling: PositiveInt
    total_input_token_upper_bound: PositiveInt
    total_output_token_upper_bound: PositiveInt
    output_tokens_per_call: PositiveInt
    trial_id: ContractId
    archive_identity_sha256: Sha256
    request_status: Literal["claimed"]
    claimed_at: IsoDateTime
    request_event_id: ContractId
    accepted_transaction_id: ContractId
    request_fingerprint: Sha256
    assessment_generation: PositiveInt = 1
    predecessor_assessment_request_id: Optional[ContractId] = None
    predecessor_assessment_request_fingerprint: Optional[Sha256] = None
    predecessor_assessment_result_id: Optional[ContractId] = None
    predecessor_result_fingerprint: Optional[Sha256] = None
    predecessor_abandonment_id: Optional[ContractId] = None
    predecessor_abandonment_fingerprint: Optional[Sha256] = None
    assessment_purpose: Literal["post_final_review", "model_evaluation"] = (
        "post_final_review"
    )
    human_actor_id: Optional[ContractId] = None
    human_request_id: Optional[ContractId] = None
    authorization_fingerprint: Optional[Sha256] = None
    assessment_kind: Optional[Literal["reader_review"]] = None
    report_type: Optional[Literal["management_monthly", "industry_weekly"]] = None
    language: Optional[Literal["en", "zh"]] = None
    model_version: Optional[CleanText] = None
    parser_version: Optional[ContractId] = None
    projection_version: Optional[ContractId] = None
    disclosure_confirmed: Optional[Literal[True]] = None
    public_safe_egress_attested: Optional[Literal[True]] = None
    cost_status: Optional[Literal["not_measured"]] = None
    reader_review_authorization_fingerprint: Optional[Sha256] = None

    @model_validator(mode="after")
    def request_identity_is_exact(self) -> "PostFinalAssessmentRequestRecord":
        reader_fields = {
            "assessment_kind",
            "report_type",
            "language",
            "model_version",
            "parser_version",
            "projection_version",
            "disclosure_confirmed",
            "public_safe_egress_attested",
            "cost_status",
            "reader_review_authorization_fingerprint",
        }
        if (
            len(set(self.ordered_prompt_request_sha256s))
            != len(self.ordered_prompt_request_sha256s)
            or self.prompt_count != len(self.ordered_prompt_request_sha256s)
            or self.output_tokens_per_call > self.total_output_token_upper_bound
        ):
            raise ValueError("post-final assessment request identity is invalid")
        if self.schema_version != self.reader_review_schema_id and (
            self.profile_id != "research_design_report_zh_v1"
            or len(self.ordered_prompt_request_sha256s) != 9
            or any(getattr(self, field) is not None for field in reader_fields)
        ):
            raise ValueError("historical assessment request fields invalid")
        binding = _READER_REVIEW_PROFILE_BINDINGS.get(self.profile_id)
        if self.schema_version == self.reader_review_schema_id and (
            binding is None
            or self.assessment_kind != "reader_review"
            or (self.report_type, self.language) != binding
            or self.model_version is None
            or self.parser_version is None
            or self.projection_version is None
            or self.disclosure_confirmed is not True
            or self.public_safe_egress_attested is not True
            or self.cost_status != "not_measured"
            or self.reader_review_authorization_fingerprint is None
            or self.prompt_count != 2
            or self.assessment_purpose != "post_final_review"
        ):
            raise ValueError("Reader Review request binding is invalid")
        series_fields = {
            "assessment_generation",
            "predecessor_assessment_request_id",
            "predecessor_assessment_request_fingerprint",
            "predecessor_assessment_result_id",
            "predecessor_result_fingerprint",
            "predecessor_abandonment_id",
            "predecessor_abandonment_fingerprint",
            "assessment_purpose",
            "human_actor_id",
            "human_request_id",
            "authorization_fingerprint",
        }
        if self.schema_version == self.schema_id:
            if (
                self.assessment_generation != 1
                or self.predecessor_assessment_request_id is not None
                or self.predecessor_assessment_request_fingerprint is not None
                or self.predecessor_assessment_result_id is not None
                or self.predecessor_result_fingerprint is not None
                or self.predecessor_abandonment_id is not None
                or self.predecessor_abandonment_fingerprint is not None
                or self.assessment_purpose != "post_final_review"
                or self.human_actor_id is not None
                or self.human_request_id is not None
                or self.authorization_fingerprint is not None
            ):
                raise ValueError("historical assessment request series fields invalid")
            payload = self.model_dump(
                mode="json",
                exclude={"request_fingerprint", *series_fields, *reader_fields},
            )
        else:
            result_predecessor = (
                self.predecessor_assessment_result_id,
                self.predecessor_result_fingerprint,
            )
            abandonment_predecessor = (
                self.predecessor_abandonment_id,
                self.predecessor_abandonment_fingerprint,
            )
            if (
                self.human_actor_id is None
                or self.human_request_id is None
                or self.authorization_fingerprint is None
            ):
                raise ValueError("assessment Human authorization is required")
            if self.assessment_generation == 1:
                if any(
                    value is not None
                    for value in (
                        self.predecessor_assessment_request_id,
                        self.predecessor_assessment_request_fingerprint,
                        *result_predecessor,
                        *abandonment_predecessor,
                    )
                ):
                    raise ValueError("generation one cannot bind a predecessor")
            elif (
                self.predecessor_assessment_request_id is None
                or self.predecessor_assessment_request_fingerprint is None
                or (all(value is not None for value in result_predecessor))
                == (all(value is not None for value in abandonment_predecessor))
                or any(value is None for value in result_predecessor)
                and any(value is not None for value in result_predecessor)
                or any(value is None for value in abandonment_predecessor)
                and any(value is not None for value in abandonment_predecessor)
            ):
                raise ValueError("assessment predecessor binding is invalid")
            payload = self.model_dump(
                mode="json",
                exclude=(
                    {"request_fingerprint", *reader_fields}
                    if self.schema_version == self.series_schema_id
                    else {"request_fingerprint"}
                ),
            )
        expected = _contract_fingerprint(
            payload,
            field="request_fingerprint",
        )
        if self.request_fingerprint != expected:
            raise ValueError("post-final assessment request fingerprint mismatch")
        return self


class PostFinalAssessmentAbandonmentRecord(StrictModel):
    """One Human-recorded terminal closure for an outcome-unknown request."""

    schema_id = "briefloop.post_final_assessment_abandonment_record.v1"

    schema_version: Literal["briefloop.post_final_assessment_abandonment_record.v1"]
    abandonment_id: ContractId
    run_id: ContractId
    assessment_request_id: ContractId
    assessment_request_fingerprint: Sha256
    finalized_lineage_fingerprint: Sha256
    assessment_generation: PositiveInt
    reason: Literal["outcome_unknown"]
    human_actor_id: ContractId
    human_request_id: ContractId
    expected_store_revision: NonNegativeInt
    recorded_at: IsoDateTime
    abandonment_event_id: ContractId
    accepted_transaction_id: ContractId
    abandonment_fingerprint: Sha256

    @model_validator(mode="after")
    def abandonment_identity_is_exact(
        self,
    ) -> "PostFinalAssessmentAbandonmentRecord":
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"abandonment_fingerprint"}),
            field="abandonment_fingerprint",
        )
        if self.abandonment_fingerprint != expected:
            raise ValueError("post-final assessment abandonment fingerprint mismatch")
        return self


PostFinalAssessmentExecutionStatus = Literal[
    "complete",
    "provider_failed",
    "local_derivation_failed",
]

PostFinalAssessmentFailurePhase = Literal[
    "provider_execution",
    "run_assembly",
    "baseline_derivation",
    "composition_derivation",
    "archive_publication",
]


class PostFinalAssessmentExecutionRecord(StrictModel):
    """Immutable evidence that provider execution reached a known local boundary."""

    # Keep the schema id used by the already-created fresh schema17 workspace.
    # The row is an execution witness; the complete identity remains in the
    # immutable payload_json so the table can stay deliberately small.
    schema_id = "briefloop.post_final_assessment_execution.v1"

    schema_version: Literal["briefloop.post_final_assessment_execution.v1"]
    execution_id: ContractId
    run_id: ContractId
    assessment_request_id: ContractId
    assessment_request_fingerprint: Sha256
    trial_id: ContractId
    finalized_lineage_fingerprint: Sha256
    execution_archive_manifest_sha256: Sha256
    execution_receipt_id: ContractId
    execution_status: PostFinalAssessmentExecutionStatus
    run_status: Optional[str] = None
    validation_status: Optional[str] = None
    failure_phase: Optional[PostFinalAssessmentFailurePhase] = None
    reason_codes: list[ContractId] = Field(default_factory=list)
    recorded_at: IsoDateTime
    execution_event_id: ContractId
    accepted_transaction_id: ContractId
    execution_fingerprint: Sha256

    @property
    def reason_codes_json(self) -> str:
        """Canonical value mirrored by the compact schema17 table column."""

        return json.dumps(
            self.reason_codes,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @model_validator(mode="after")
    def execution_identity_is_exact(self) -> "PostFinalAssessmentExecutionRecord":
        if self.reason_codes != sorted(set(self.reason_codes)):
            raise ValueError("post-final execution reason codes are not canonical")
        if (self.execution_status == "complete") != (self.failure_phase is None):
            raise ValueError("post-final execution failure phase is invalid")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"execution_fingerprint"}),
            field="execution_fingerprint",
        )
        if self.execution_fingerprint != expected:
            raise ValueError("post-final execution fingerprint mismatch")
        return self


ReaderReviewResultStatus = Literal[
    "finding_returned",
    "no_finding_returned_in_completed_supported_checks",
    "partially_assessed",
    "unable_to_assess",
]


def derive_reader_review_result_status(
    *,
    terminal_evidence_class: str,
    assessed_unit_count: int,
    finding_count: int,
    withheld_finding_count: int,
    abstention_count: int,
    requirement_states: Iterable[str],
) -> str:
    """Derive the limited Reader Review outcome vocabulary from stored facts."""

    states = tuple(requirement_states)
    if terminal_evidence_class == "available":
        if (
            withheld_finding_count > 0
            or abstention_count > 0
            or "unable_to_assess" in states
        ):
            return "partially_assessed"
        if finding_count > 0:
            return "finding_returned"
        return "no_finding_returned_in_completed_supported_checks"
    # A terminal incomplete provider attempt never certifies the units whose
    # response was truncated.  The projection may still expose completed O1
    # units separately, but the result-level status must remain unable to
    # assess instead of implying a partial supported conclusion.
    if terminal_evidence_class == "incomplete":
        return "unable_to_assess"
    return "unable_to_assess"


def _contains_reader_review_secret_key(value: object) -> bool:
    forbidden = {
        "api_key",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
    if isinstance(value, dict):
        return any(
            key.lower() in forbidden or _contains_reader_review_secret_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_reader_review_secret_key(item) for item in value)
    return False


class PostFinalAssessmentResultRecord(StrictModel):
    """One qualified, archive-bound advisory outcome; never raw provider data."""

    schema_id = "briefloop.post_final_assessment_result_record.v2"
    reader_review_schema_id: ClassVar[str] = (
        "briefloop.post_final_assessment_result_record.v3"
    )

    schema_version: Literal[
        "briefloop.post_final_assessment_result_record.v2",
        "briefloop.post_final_assessment_result_record.v3",
    ]
    assessment_result_id: ContractId
    run_id: ContractId
    assessment_request_id: ContractId
    policy_revision_id: ContractId
    finalized_facts_fingerprint: Sha256
    finalized_lineage_fingerprint: Sha256
    terminal_evidence_class: Literal[
        "available",
        "abstained",
        "provider_failed",
        "refused",
        "incomplete",
        "unavailable",
    ]
    reason_codes: list[ContractId] = Field(default_factory=list)
    shadow_request_sha256: Sha256
    execution_manifest_sha256: Sha256
    archive_manifest_sha256: Sha256
    archive_receipt_id: ContractId
    composition_sha256: Sha256
    presentation_sha256: Sha256
    reader_view_sha256: Sha256
    assessed_unit_count: NonNegativeInt
    finding_count: NonNegativeInt
    withheld_finding_count: NonNegativeInt
    abstention_count: NonNegativeInt
    recorded_at: IsoDateTime
    result_event_id: ContractId
    accepted_transaction_id: ContractId
    result_fingerprint: Sha256
    assessment_kind: Optional[Literal["reader_review"]] = None
    report_type: Optional[Literal["management_monthly", "industry_weekly"]] = None
    language: Optional[Literal["en", "zh"]] = None
    profile_id: Optional[Literal["management_brief_en_v1", "industry_weekly_zh_v1"]] = (
        None
    )
    model_version: Optional[CleanText] = None
    expected_model_identity: Optional[CleanText] = None
    parser_version: Optional[Literal["strict_dimension_json_v3"]] = None
    projection_version: Optional[Literal["reader_review_projection_v1"]] = None
    reader_review_status: Optional[ReaderReviewResultStatus] = None
    reader_view_payload: Optional[dict[str, JsonValue]] = None

    @model_validator(mode="after")
    def result_identity_and_non_effect_are_exact(
        self,
    ) -> "PostFinalAssessmentResultRecord":
        reader_fields = {
            "assessment_kind",
            "report_type",
            "language",
            "profile_id",
            "model_version",
            "expected_model_identity",
            "parser_version",
            "projection_version",
            "reader_review_status",
            "reader_view_payload",
        }
        if self.reason_codes != sorted(set(self.reason_codes)):
            raise ValueError("post-final assessment reason codes are not canonical")
        if self.terminal_evidence_class != "available" and (
            self.finding_count != 0 or self.withheld_finding_count != 0
        ):
            raise ValueError("unavailable assessment cannot expose findings")
        if self.schema_version == self.schema_id:
            if any(getattr(self, field) is not None for field in reader_fields):
                raise ValueError("historical assessment result fields are invalid")
            fingerprint_payload = self.model_dump(
                mode="json",
                exclude={"result_fingerprint", *reader_fields},
            )
        else:
            view = self.reader_view_payload
            allowed_view_keys = {
                "schema_version",
                "status",
                "boundary",
                "advisory_only",
                "shadow_only",
                "runtime_authority",
                "authority_effect",
                "archive_verified",
                "binding",
                "run_status",
                "validation_status",
                "reason_codes",
                "assessed_unit_count",
                "finding_count",
                "withheld_finding_count",
                "abstention_count",
                "findings",
                "requirement_assessments",
                "disclaimer",
                "view_sha256",
            }
            binding = _READER_REVIEW_PROFILE_BINDINGS.get(self.profile_id)
            if (
                self.assessment_kind != "reader_review"
                or binding is None
                or (self.report_type, self.language) != binding
                or self.model_version is None
                or self.expected_model_identity is None
                or self.model_version != self.expected_model_identity
                or self.parser_version != "strict_dimension_json_v3"
                or self.projection_version != "reader_review_projection_v1"
                or view is None
                or set(view) != allowed_view_keys
                or _contains_reader_review_secret_key(view)
                or view.get("view_sha256") != self.reader_view_sha256
                or _canonical_json_sha256(
                    {key: item for key, item in view.items() if key != "view_sha256"}
                )
                != self.reader_view_sha256
                or view.get("finding_count") != self.finding_count
                or view.get("withheld_finding_count") != self.withheld_finding_count
                or view.get("abstention_count") != self.abstention_count
                or view.get("assessed_unit_count") != self.assessed_unit_count
                or view.get("reason_codes") != self.reason_codes
            ):
                raise ValueError("Reader Review result binding is invalid")
            assessments = view.get("requirement_assessments")
            if not isinstance(assessments, list) or any(
                not isinstance(item, dict)
                or item.get("state")
                not in {
                    "fulfilled",
                    "unfulfilled_transparent",
                    "unfulfilled_undisclosed",
                    "unable_to_assess",
                }
                for item in assessments
            ):
                raise ValueError("Reader Review requirement states are invalid")
            expected_status = derive_reader_review_result_status(
                terminal_evidence_class=self.terminal_evidence_class,
                assessed_unit_count=self.assessed_unit_count,
                finding_count=self.finding_count,
                withheld_finding_count=self.withheld_finding_count,
                abstention_count=self.abstention_count,
                requirement_states=(str(item["state"]) for item in assessments),
            )
            if self.reader_review_status != expected_status:
                raise ValueError("Reader Review result status is invalid")
            fingerprint_payload = self.model_dump(
                mode="json", exclude={"result_fingerprint"}
            )
        expected = _contract_fingerprint(
            fingerprint_payload,
            field="result_fingerprint",
        )
        if self.result_fingerprint != expected:
            raise ValueError("post-final assessment result fingerprint mismatch")
        return self


class PostFinalFindingDispositionRecord(StrictModel):
    """One append-only Human decision on one Store-qualified LAJ finding."""

    schema_id = "briefloop.post_final_finding_disposition_record.v2"

    schema_version: Literal["briefloop.post_final_finding_disposition_record.v2"]
    disposition_id: ContractId
    run_id: ContractId
    finalized_lineage_fingerprint: Sha256
    assessment_result_id: ContractId
    assessment_result_fingerprint: Sha256
    reader_view_sha256: Sha256
    finding_id: ContractId
    finding_fingerprint: Sha256
    previous_disposition_id: Optional[ContractId] = None
    decision: Literal["accept", "reject", "defer"]
    human_note: Optional[CleanText] = None
    human_actor_id: ContractId
    human_request_id: ContractId
    recorded_at: IsoDateTime
    disposition_event_id: ContractId
    accepted_transaction_id: ContractId
    disposition_fingerprint: Sha256

    @model_validator(mode="after")
    def disposition_identity_is_exact(self) -> "PostFinalFindingDispositionRecord":
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"disposition_fingerprint"}),
            field="disposition_fingerprint",
        )
        if self.disposition_fingerprint != expected:
            raise ValueError("post-final disposition fingerprint mismatch")
        return self


class HumanObservationReportSpan(StrictModel):
    """Strict, report-bound span reference supplied by a Human observer."""

    schema_id = "briefloop.post_final_human_observation_report_span.v1"

    schema_version: Literal["briefloop.post_final_human_observation_report_span.v1"]
    report_sha256: Sha256
    block_id: ContractId
    start_char: NonNegativeInt
    end_char: PositiveInt
    excerpt_sha256: Sha256

    @model_validator(mode="after")
    def span_offsets_are_ordered(self) -> "HumanObservationReportSpan":
        if self.start_char >= self.end_char:
            raise ValueError("human observation span offsets must be ordered")
        return self


class PostFinalHumanObservationRecord(StrictModel):
    """One append-only, report-bound Human observation.

    A Human observation is deliberately not a model finding: it never carries a
    finding identity.  Superseding creates a new observation identity linked to
    the prior record; no row is updated in place.
    """

    schema_id = "briefloop.post_final_human_observation_record.v1"

    schema_version: Literal["briefloop.post_final_human_observation_record.v1"]
    origin: Literal["human"]
    observation_id: ContractId
    observation_revision: PositiveInt
    run_id: ContractId
    finalized_lineage_fingerprint: Sha256
    report_revision: PositiveInt
    report_artifact_id: ContractId
    report_sha256: Sha256
    assessment_result_id: Optional[ContractId] = None
    assessment_result_fingerprint: Optional[Sha256] = None
    reader_view_sha256: Optional[Sha256] = None
    observation_text: CleanText
    observation_sha256: Sha256
    requirement_id: Optional[ContractId] = None
    claim_id: Optional[ContractId] = None
    report_span: Optional[HumanObservationReportSpan] = None
    scope_class: Optional[Literal["O1", "O2"]] = None
    dimension_id: Optional[ContractId] = None
    previous_observation_id: Optional[ContractId] = None
    previous_observation_fingerprint: Optional[Sha256] = None
    human_actor_id: ContractId
    human_request_id: ContractId
    recorded_at: IsoDateTime
    observation_event_id: ContractId
    accepted_transaction_id: ContractId
    observation_fingerprint: Sha256

    @model_validator(mode="after")
    def observation_identity_is_exact(self) -> "PostFinalHumanObservationRecord":
        result_fields = (
            self.assessment_result_id,
            self.assessment_result_fingerprint,
            self.reader_view_sha256,
        )
        if any(item is not None for item in result_fields) and not all(
            item is not None for item in result_fields
        ):
            raise ValueError("human observation assessment binding must be total")
        if (self.scope_class is None) != (self.dimension_id is None):
            raise ValueError("human observation dimension binding must be total")
        if (self.previous_observation_id is None) != (
            self.previous_observation_fingerprint is None
        ):
            raise ValueError("human observation predecessor binding must be total")
        if self.observation_revision == 1 and self.previous_observation_id is not None:
            raise ValueError("first human observation revision cannot have predecessor")
        if self.observation_revision > 1 and self.previous_observation_id is None:
            raise ValueError("superseding human observation requires predecessor")
        if (
            self.observation_sha256
            != hashlib.sha256(self.observation_text.encode("utf-8")).hexdigest()
        ):
            raise ValueError("human observation text hash mismatch")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"observation_fingerprint"}),
            field="observation_fingerprint",
        )
        if self.observation_fingerprint != expected:
            raise ValueError("human observation fingerprint mismatch")
        return self


def _current_post_final_disposition_at_cutoff(
    records: tuple[PostFinalFindingDispositionRecord, ...],
    *,
    receipt_revisions: dict[tuple[str, str], int],
    run_id: str,
    assessment_result_id: str,
    finding_id: str,
    cutoff_revision: int,
) -> PostFinalFindingDispositionRecord | None:
    """Select one deterministic disposition head at an exact Store revision."""

    if type(cutoff_revision) is not int or cutoff_revision < 0:
        raise ValueError("post-final disposition cutoff is invalid")
    candidates: list[tuple[int, PostFinalFindingDispositionRecord]] = []
    seen_revisions: set[int] = set()
    for record in records:
        if (
            record.run_id != run_id
            or record.assessment_result_id != assessment_result_id
            or record.finding_id != finding_id
        ):
            continue
        revision = receipt_revisions.get(
            (record.run_id, record.accepted_transaction_id)
        )
        if type(revision) is not int or revision < 0:
            raise ValueError("post-final disposition receipt is invalid")
        if revision > cutoff_revision:
            continue
        if revision in seen_revisions:
            raise ValueError("post-final disposition revision is ambiguous")
        seen_revisions.add(revision)
        candidates.append((revision, record))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


class PostFinalGuidanceDraftRevision(StrictModel):
    """One Human-authored guidance text revision sourced from an acceptance."""

    schema_id = "briefloop.post_final_guidance_draft_revision.v2"

    schema_version: Literal["briefloop.post_final_guidance_draft_revision.v2"]
    guidance_id: ContractId
    draft_revision: PositiveInt
    run_id: ContractId
    finalized_lineage_fingerprint: Sha256
    provenance_kind: Literal["accepted_model_finding", "human_observation"] = (
        "accepted_model_finding"
    )
    assessment_result_id: Optional[ContractId] = None
    assessment_result_fingerprint: Optional[Sha256] = None
    finding_id: Optional[ContractId] = None
    finding_fingerprint: Optional[Sha256] = None
    disposition_id: Optional[ContractId] = None
    disposition_fingerprint: Optional[Sha256] = None
    observation_id: Optional[ContractId] = None
    observation_fingerprint: Optional[Sha256] = None
    previous_draft_revision: Optional[PositiveInt] = None
    guidance_scope: Literal["finding_only", "observation_only"]
    guidance_text: CleanText
    guidance_sha256: Sha256
    human_actor_id: ContractId
    human_request_id: ContractId
    recorded_at: IsoDateTime
    draft_event_id: ContractId
    accepted_transaction_id: ContractId
    draft_fingerprint: Sha256

    @model_validator(mode="after")
    def guidance_draft_identity_is_exact(self) -> "PostFinalGuidanceDraftRevision":
        result_fields = (
            self.assessment_result_id,
            self.assessment_result_fingerprint,
        )
        finding_fields = (self.finding_id, self.finding_fingerprint)
        disposition_fields = (self.disposition_id, self.disposition_fingerprint)
        observation_fields = (self.observation_id, self.observation_fingerprint)
        if self.provenance_kind == "accepted_model_finding":
            if not all(
                item is not None
                for item in (*result_fields, *finding_fields, *disposition_fields)
            ):
                raise ValueError("accepted model finding provenance is incomplete")
            if any(item is not None for item in observation_fields):
                raise ValueError("model finding guidance cannot bind observation")
            if self.guidance_scope != "finding_only":
                raise ValueError("model finding guidance scope is invalid")
        else:
            if not all(item is not None for item in observation_fields):
                raise ValueError("human observation provenance is incomplete")
            if any(item is not None for item in finding_fields + disposition_fields):
                raise ValueError("human observation guidance cannot bind finding")
            if any(item is not None for item in result_fields) and not all(
                item is not None for item in result_fields
            ):
                raise ValueError("human observation result binding must be total")
            if self.guidance_scope != "observation_only":
                raise ValueError("human observation guidance scope is invalid")
        if (
            self.guidance_sha256
            != hashlib.sha256(self.guidance_text.encode("utf-8")).hexdigest()
        ):
            raise ValueError("post-final guidance text hash mismatch")
        if (self.draft_revision == 1 and self.previous_draft_revision is not None) or (
            self.draft_revision > 1
            and self.previous_draft_revision != self.draft_revision - 1
        ):
            raise ValueError("post-final guidance revision chain is invalid")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"draft_fingerprint"}),
            field="draft_fingerprint",
        )
        if self.draft_fingerprint != expected:
            raise ValueError("post-final guidance draft fingerprint mismatch")
        return self


class PostFinalGuidanceStatusRevision(StrictModel):
    """One separate Human approval/lifecycle decision for an exact draft."""

    schema_id = "briefloop.post_final_guidance_status_revision.v2"

    schema_version: Literal["briefloop.post_final_guidance_status_revision.v2"]
    status_revision_id: ContractId
    run_id: ContractId
    finalized_lineage_fingerprint: Sha256
    guidance_id: ContractId
    draft_revision: PositiveInt
    guidance_sha256: Sha256
    status: Literal["approved", "deactivated", "reverted", "superseded"]
    previous_status_revision_id: Optional[ContractId] = None
    human_actor_id: ContractId
    human_request_id: ContractId
    recorded_at: IsoDateTime
    status_event_id: ContractId
    accepted_transaction_id: ContractId
    status_fingerprint: Sha256

    @model_validator(mode="after")
    def guidance_status_identity_is_exact(self) -> "PostFinalGuidanceStatusRevision":
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"status_fingerprint"}),
            field="status_fingerprint",
        )
        if self.status_fingerprint != expected:
            raise ValueError("post-final guidance status fingerprint mismatch")
        return self


POST_FINAL_GUIDANCE_STATUS_TRANSITIONS = MappingProxyType(
    {
        None: ("approved",),
        "approved": ("approved", "deactivated", "reverted", "superseded"),
        "deactivated": ("approved",),
        "reverted": ("approved",),
        "superseded": ("approved",),
    }
)


def post_final_guidance_legal_actions(
    current: PostFinalGuidanceStatusRevision | None,
    *,
    target_draft_revision: int,
    approval_eligible: bool,
) -> tuple[str, ...]:
    """Return the only legal Human actions for one exact draft revision."""

    if current is None:
        return (
            POST_FINAL_GUIDANCE_STATUS_TRANSITIONS[None]
            if target_draft_revision >= 1 and approval_eligible
            else ()
        )
    if target_draft_revision > current.draft_revision:
        return ("approved",) if approval_eligible else ()
    if target_draft_revision == current.draft_revision and current.status == "approved":
        return ("deactivated", "reverted", "superseded")
    return ()


def post_final_guidance_status_transition_allowed(
    current: PostFinalGuidanceStatusRevision | None,
    candidate: PostFinalGuidanceStatusRevision,
    *,
    approval_eligible: bool,
) -> bool:
    """Validate one append-only status transition against the current head."""

    return candidate.status in post_final_guidance_legal_actions(
        current,
        target_draft_revision=candidate.draft_revision,
        approval_eligible=approval_eligible,
    )


class RepairStartRequest(StrictModel):
    schema_id = "briefloop.repair_start_request.v2"
    schema_version: Literal["briefloop.repair_start_request.v2"]
    request_id: ContractId
    run_id: ContractId
    contamination_revision: PositiveInt
    owner_stage_id: ContractId
    permitted_artifact_ids: list[ContractId] = Field(min_length=1)
    reason_code: ContractId
    expected_store_revision: NonNegativeInt


class ArtifactSupersedeRequest(StrictModel):
    schema_id = "briefloop.artifact_supersede_request.v2"
    schema_version: Literal["briefloop.artifact_supersede_request.v2"]
    request_id: ContractId
    run_id: ContractId
    repair_id: ContractId
    prior_artifact: ArtifactRevisionReference
    input_path: ScratchInputPath
    expected_input_sha256: Sha256
    expected_current_revision: PositiveInt
    mode: Literal["repair", "supersede"]
    reason_code: ContractId
    expected_store_revision: NonNegativeInt


class ArtifactRevertRequest(StrictModel):
    schema_id = "briefloop.artifact_revert_request.v2"
    schema_version: Literal["briefloop.artifact_revert_request.v2"]
    request_id: ContractId
    run_id: ContractId
    repair_id: ContractId
    current_artifact: ArtifactRevisionReference
    historical_source: ArtifactRevisionReference
    expected_current_revision: PositiveInt
    mode: Literal["revert"]
    reason_code: ContractId
    expected_store_revision: NonNegativeInt


class RepairCompleteRequest(StrictModel):
    schema_id = "briefloop.repair_complete_request.v2"
    schema_version: Literal["briefloop.repair_complete_request.v2"]
    request_id: ContractId
    run_id: ContractId
    repair_id: ContractId
    supersession_ids: list[ContractId] = Field(min_length=1)
    expected_stage_revisions: dict[ContractId, NonNegativeInt]
    expected_store_revision: NonNegativeInt


class RecoveryCompleteRequest(StrictModel):
    schema_id = "briefloop.recovery_complete_request.v2"
    schema_version: Literal["briefloop.recovery_complete_request.v2"]
    request_id: ContractId
    run_id: ContractId
    repair_completion_id: ContractId
    contamination_revision: PositiveInt
    rerun_transition_ids: list[ContractId] = Field(min_length=1)
    gate_evaluation_ids: list[ContractId] = Field(default_factory=list)
    expected_store_revision: NonNegativeInt


class RunResetRequest(StrictModel):
    schema_id = "briefloop.run_reset_request.v2"
    schema_version: Literal["briefloop.run_reset_request.v2"]
    request_id: ContractId
    predecessor_run_id: ContractId
    successor_run_id: ContractId
    workspace_id: ContractId
    runtime: RuntimeName
    expected_head_run_id: ContractId
    expected_store_revision: NonNegativeInt
    expected_workspace_revision: NonNegativeInt
    run_direction: RunDirection
    workspace_config_sha256: Sha256
    sources_config_sha256: Sha256
    role_topology: RoleTopology
    gate_strictness: dict[GateId, bool]
    input_governance_required: bool


class RunSuccessorStartRequest(StrictModel):
    """Human request for one normal same-workspace successor run."""

    schema_id = "briefloop.run_successor_start_request.v1"

    schema_version: Literal["briefloop.run_successor_start_request.v1"]
    request_id: ContractId
    predecessor_run_id: ContractId
    successor_run_id: ContractId
    workspace_id: ContractId
    runtime: RuntimeName
    expected_head_run_id: ContractId
    expected_store_revision: NonNegativeInt
    expected_workspace_revision: NonNegativeInt
    run_direction: RunDirection
    workspace_config_sha256: Sha256
    sources_config_sha256: Sha256
    role_topology: RoleTopology
    gate_strictness: dict[GateId, bool]
    input_governance_required: bool
    include_approved_guidance: bool
    request_fingerprint: Sha256

    @model_validator(mode="after")
    def successor_request_identity_is_exact(self) -> "RunSuccessorStartRequest":
        if self.predecessor_run_id == self.successor_run_id:
            raise ValueError("successor run must be distinct")
        expected = _contract_fingerprint(
            self.model_dump(mode="json", exclude={"request_fingerprint"}),
            field="request_fingerprint",
        )
        if self.request_fingerprint != expected:
            raise ValueError("successor request fingerprint mismatch")
        return self


class FinalizeRenderRequest(StrictModel):
    schema_id = "briefloop.finalize_render_request.v2"
    schema_version: Literal["briefloop.finalize_render_request.v2"]
    request_id: ContractId
    run_id: ContractId
    audit_proposal_id: ContractId
    expected_audited_brief: ArtifactRevisionReference
    expected_audit_report: ArtifactRevisionReference
    reader_scratch_inputs: dict[ContractId, ScratchInputPath]
    expected_reader_sha256: dict[ContractId, Sha256]
    expected_reader_revisions: dict[ContractId, NonNegativeInt]
    expected_store_revision: NonNegativeInt

    @model_validator(mode="after")
    def reader_maps_are_exact(self) -> "FinalizeRenderRequest":
        keys = set(self.reader_scratch_inputs)
        if keys != set(self.expected_reader_sha256) or keys != set(
            self.expected_reader_revisions
        ):
            raise ValueError("reader input, hash and revision maps must match")
        return self


class FinalizeCompleteRequest(StrictModel):
    schema_id = "briefloop.finalize_complete_request.v2"
    schema_version: Literal["briefloop.finalize_complete_request.v2"]
    request_id: ContractId
    run_id: ContractId
    render_id: ContractId
    expected_finalize_stage_revision: NonNegativeInt
    gate_evaluation_ids: list[ContractId] = Field(min_length=1)
    recovery_id: Optional[ContractId] = None
    expected_store_revision: NonNegativeInt


class InternalApprovalRequest(StrictModel):
    schema_id = "briefloop.internal_approval_request.v2"
    schema_version: Literal["briefloop.internal_approval_request.v2"]
    request_id: ContractId
    run_id: ContractId
    package_id: ContractId
    approval_id: ContractId
    mode: Literal[
        "internal_draft",
        "internal_management_review",
        "research_review",
        "ir_draft",
        "formal_release_candidate",
    ]
    role: Literal[
        "content_owner",
        "evidence_reviewer",
        "ir_owner",
        "legal_or_compliance_reviewer",
    ]
    decision: Literal["approve", "reject", "request_changes"]
    reason: ApprovalReason
    actor_id: ContractId
    expected_store_revision: NonNegativeInt


class DeliveryAuthorizationRequest(StrictModel):
    schema_id = "briefloop.delivery_authorization_request.v2"
    schema_version: Literal["briefloop.delivery_authorization_request.v2"]
    request_id: ContractId
    run_id: ContractId
    package_id: ContractId
    prior_authorization_id: Optional[ContractId] = None
    approval_mode: Literal[
        "internal_draft",
        "internal_management_review",
        "research_review",
        "ir_draft",
        "formal_release_candidate",
    ]
    retry_of_attempt_id: Optional[ContractId] = None
    purpose: Literal["initial_attempt", "retry_attempt", "result_reconciliation"]
    decision: Literal["authorize", "deny"]
    target: Literal["local", "feishu", "gmail"]
    channel: CleanText
    recipient_fingerprint: Sha256
    actor_id: ContractId
    reason: ApprovalReason
    expected_store_revision: NonNegativeInt


class RunTerminationRequest(StrictModel):
    """Explicit Human decision that irreversibly terminates one run."""

    schema_id = "briefloop.run_termination_request.v2"

    schema_version: Literal["briefloop.run_termination_request.v2"]
    request_id: ContractId
    run_id: ContractId
    decision: Literal["terminate"]
    reason_code: Literal[
        "gate_repair_unresolvable",
        "negative_audit_truth_accepted",
        "operator_abandon",
    ]
    reason: ApprovalReason
    actor_id: ContractId
    expected_action_fingerprint: Sha256
    expected_store_revision: NonNegativeInt


class DeliveryAttemptRequest(StrictModel):
    schema_id = "briefloop.delivery_attempt_request.v2"
    schema_version: Literal["briefloop.delivery_attempt_request.v2"]
    request_id: ContractId
    run_id: ContractId
    package_id: ContractId
    authorization_id: ContractId
    connector_operation_id: ContractId
    connector_request_fingerprint: Sha256
    expected_store_revision: NonNegativeInt


class DeliveryResultRequest(StrictModel):
    schema_id = "briefloop.delivery_result_request.v2"
    schema_version: Literal["briefloop.delivery_result_request.v2"]
    request_id: ContractId
    run_id: ContractId
    attempt_id: ContractId
    prior_result_id: Optional[ContractId] = None
    observation_input_path: Optional[ScratchInputPath] = None
    expected_observation_sha256: Optional[Sha256] = None
    reconciliation_authorization_id: Optional[ContractId] = None
    expected_store_revision: NonNegativeInt

    @model_validator(mode="after")
    def observation_hash_is_exact(self) -> "DeliveryResultRequest":
        if (self.observation_input_path is None) != (
            self.expected_observation_sha256 is None
        ):
            raise ValueError("observation path and expected hash must be paired")
        return self


CheckoutRevisionId = Annotated[
    str,
    StringConstraints(pattern=r"^crv_[0-9a-f]{64}$"),
]
PublicationKind = Literal["absent", "blob"]


class CheckoutRevisionRecord(StrictModel):
    """Immutable receipt-owned identity of one protected checkout tree."""

    schema_id = "briefloop.checkout_revision.v2"
    schema_version: Literal["briefloop.checkout_revision.v2"]
    checkout_revision_id: CheckoutRevisionId
    workspace_id: ContractId
    run_id: ContractId
    parent_checkout_revision_id: Optional[CheckoutRevisionId] = None
    manifest_sha256: Sha256
    tree_sha256: Sha256
    member_count: NonNegativeInt
    created_at: IsoDateTime
    creator_transaction_id: ContractId


class CheckoutRevisionMember(StrictModel):
    schema_id = "briefloop.checkout_revision_member.v2"
    schema_version: Literal["briefloop.checkout_revision_member.v2"]
    checkout_revision_id: CheckoutRevisionId
    ordinal: NonNegativeInt
    workspace_id: ContractId
    run_id: ContractId
    canonical_path: WorkspacePath
    artifact_id: ContractId
    artifact_revision: PositiveInt
    blob_sha256: Sha256
    byte_size: NonNegativeInt


class ReceiptCheckoutBinding(StrictModel):
    schema_id = "briefloop.receipt_checkout_binding.v2"
    schema_version: Literal["briefloop.receipt_checkout_binding.v2"]
    workspace_id: ContractId
    run_id: ContractId
    transaction_id: ContractId
    pre_run_id: ContractId
    pre_checkout_revision_id: Optional[CheckoutRevisionId] = None
    post_run_id: ContractId
    post_checkout_revision_id: CheckoutRevisionId


class PublicationIdentityV1(StrictModel):
    schema_id = "briefloop.publication_identity.v1"
    schema_version: Literal["briefloop-publication-identity/v1"]
    workspace_id: ContractId
    run_id: ContractId
    transaction_id: ContractId
    checkout_revision_id: CheckoutRevisionId


class CheckoutPublicationIntent(StrictModel):
    schema_id = "briefloop.checkout_publication_intent.v2"
    schema_version: Literal["briefloop.checkout_publication_intent.v2"]
    identity: PublicationIdentityV1
    publication_identity_sha256: Sha256
    pre_checkout_revision_id: Optional[CheckoutRevisionId] = None
    post_checkout_revision_id: CheckoutRevisionId
    post_manifest_sha256: Sha256
    post_tree_sha256: Sha256
    changed_member_count: PositiveInt
    capability_profile_sha256: Sha256


class CheckoutPublicationMember(StrictModel):
    schema_id = "briefloop.checkout_publication_member.v2"
    schema_version: Literal["briefloop.checkout_publication_member.v2"]
    identity: PublicationIdentityV1
    ordinal: NonNegativeInt
    canonical_path: WorkspacePath
    temporary_basename: CleanText
    claim_basename: CleanText
    pre_kind: PublicationKind
    pre_sha256: Optional[Sha256] = None
    pre_size: Optional[NonNegativeInt] = None
    post_kind: PublicationKind
    post_sha256: Optional[Sha256] = None
    post_size: Optional[NonNegativeInt] = None

    @model_validator(mode="after")
    def kinds_match_values(self) -> "CheckoutPublicationMember":
        for kind, digest, size in (
            (self.pre_kind, self.pre_sha256, self.pre_size),
            (self.post_kind, self.post_sha256, self.post_size),
        ):
            if kind == "absent" and (digest is not None or size is not None):
                raise ValueError("absent publication member cannot carry blob values")
            if kind == "blob" and (digest is None or size is None):
                raise ValueError("blob publication member requires exact values")
        if self.pre_kind == self.post_kind == "absent":
            raise ValueError("unchanged absent member is not publishable")
        return self


class CheckoutPublicationAck(StrictModel):
    schema_id = "briefloop.checkout_publication_ack.v2"
    schema_version: Literal["briefloop.checkout_publication_ack.v2"]
    identity: PublicationIdentityV1
    ordinal: NonNegativeInt
    publication_identity_sha256: Sha256
    capability_profile_sha256: Sha256
    post_kind: PublicationKind
    post_sha256: Optional[Sha256] = None
    post_size: Optional[NonNegativeInt] = None
    verification: Literal["post_verified_durable"]
    cleanup_policy: Literal["retain_residue_v1"]
    appended_at: IsoDateTime

    @model_validator(mode="after")
    def post_matches_kind(self) -> "CheckoutPublicationAck":
        if self.post_kind == "absent" and (
            self.post_sha256 is not None or self.post_size is not None
        ):
            raise ValueError("absent ack cannot carry blob values")
        if self.post_kind == "blob" and (
            self.post_sha256 is None or self.post_size is None
        ):
            raise ValueError("blob ack requires exact values")
        return self


class CheckoutPublicationCleanupObservation(StrictModel):
    schema_id = "briefloop.checkout_publication_cleanup_observation.v2"
    schema_version: Literal["briefloop.checkout_publication_cleanup_observation.v2"]
    cleanup_observation_id: Sha256
    identity: PublicationIdentityV1
    ordinal: NonNegativeInt
    auxiliary_role: Literal["temp", "claim"]
    reason_code: Literal[
        "checkout_projection_cleanup_retained",
        "checkout_projection_cleanup_conflict",
        "checkout_projection_cleanup_io_warning",
    ]
    expected_kind: PublicationKind
    expected_sha256: Optional[Sha256] = None
    expected_size: Optional[NonNegativeInt] = None
    observed_kind: Literal["absent", "blob", "unsafe", "unreadable"]
    observed_sha256: Optional[Sha256] = None
    observed_size: Optional[NonNegativeInt] = None
    appended_at: IsoDateTime

    @model_validator(mode="after")
    def blob_values_match_kinds(self) -> "CheckoutPublicationCleanupObservation":
        if self.expected_kind == "absent" and (
            self.expected_sha256 is not None or self.expected_size is not None
        ):
            raise ValueError("absent expected residue cannot carry blob values")
        if self.expected_kind == "blob" and (
            self.expected_sha256 is None or self.expected_size is None
        ):
            raise ValueError("blob expected residue requires exact values")
        if self.observed_kind != "blob" and (
            self.observed_sha256 is not None or self.observed_size is not None
        ):
            raise ValueError("non-blob observed residue cannot carry blob values")
        if self.observed_kind == "blob" and (
            self.observed_sha256 is None or self.observed_size is None
        ):
            raise ValueError("blob observed residue requires exact values")
        return self


# Private neutral structural kernel shared by the Core adapter and Store.
# These helpers define no domain legality and are intentionally not exported.
_CHECKOUT_MANIFEST_SCHEMA = "multi-agent-brief-checkout-revision/v1"
_CHECKOUT_TREE_DOMAIN = b"briefloop-checkout-tree-v1\0"
_PUBLICATION_IDENTITY_DOMAIN = b"briefloop-publication-identity-v1\0"


class _CheckoutStructureError(ValueError):
    pass


def _checkout_canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _CheckoutStructureError from exc


def _build_checkout_revision_structure(
    *,
    workspace_id: str,
    run_id: str,
    transaction_id: str,
    created_at: datetime,
    artifact_revisions: tuple[ArtifactRevision, ...],
    parent_checkout_revision_id: str | None,
) -> tuple[
    CheckoutRevisionRecord,
    tuple[CheckoutRevisionMember, ...],
    bytes,
]:
    if created_at.tzinfo is None:
        raise _CheckoutStructureError
    ordered = sorted(artifact_revisions, key=lambda item: item.path)
    paths: set[str] = set()
    folded: set[str] = set()
    identities: set[tuple[str, int]] = set()
    member_payloads: list[dict[str, object]] = []
    for item in ordered:
        path = PurePosixPath(item.path)
        identity = (item.artifact_id, item.revision)
        if (
            item.run_id != run_id
            or not item.frozen
            or path.is_absolute()
            or str(path) != item.path
            or any(part in {"", ".", ".."} for part in path.parts)
            or item.path in paths
            or item.path.casefold() in folded
            or identity in identities
        ):
            raise _CheckoutStructureError
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
    manifest_bytes = _checkout_canonical_json_bytes(
        {
            "schema_version": _CHECKOUT_MANIFEST_SCHEMA,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "parent_checkout_revision_id": parent_checkout_revision_id,
            "members": member_payloads,
        }
    )
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    tree_sha256 = hashlib.sha256(_CHECKOUT_TREE_DOMAIN + manifest_bytes).hexdigest()
    revision_id = f"crv_{tree_sha256}"
    try:
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
                "created_at": created_at.isoformat().replace("+00:00", "Z"),
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
    except (TypeError, ValueError) as exc:
        raise _CheckoutStructureError from exc
    return record, members, manifest_bytes


def _publication_identity_digest(identity: PublicationIdentityV1) -> str:
    payload = identity.model_dump(mode="json", exclude_unset=False)
    return hashlib.sha256(
        _PUBLICATION_IDENTITY_DOMAIN + _checkout_canonical_json_bytes(payload)
    ).hexdigest()


def _publication_sibling_name(
    identity: PublicationIdentityV1,
    ordinal: int,
    role: str,
) -> str:
    if role not in {"tmp", "claim"} or type(ordinal) is not int or ordinal < 0:
        raise _CheckoutStructureError
    return (
        f".briefloop-pub-v1-{_publication_identity_digest(identity)}-"
        f"{ordinal:08d}-{role}"
    )


def _derive_publication_structure(
    *,
    identity: PublicationIdentityV1,
    pre_record: CheckoutRevisionRecord | None,
    pre_members: tuple[CheckoutRevisionMember, ...],
    post_record: CheckoutRevisionRecord,
    post_members: tuple[CheckoutRevisionMember, ...],
    capability_profile_sha256: str,
) -> tuple[CheckoutPublicationIntent, tuple[CheckoutPublicationMember, ...]]:
    if identity.checkout_revision_id != post_record.checkout_revision_id:
        raise _CheckoutStructureError
    pre_by_path = {item.canonical_path: item for item in pre_members}
    post_by_path = {item.canonical_path: item for item in post_members}

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
        raise _CheckoutStructureError
    try:
        members = tuple(
            CheckoutPublicationMember.model_validate(
                {
                    "schema_version": CheckoutPublicationMember.schema_id,
                    "identity": identity.model_dump(mode="json"),
                    "ordinal": ordinal,
                    "canonical_path": path,
                    "temporary_basename": _publication_sibling_name(
                        identity, ordinal, "tmp"
                    ),
                    "claim_basename": _publication_sibling_name(
                        identity, ordinal, "claim"
                    ),
                    "pre_kind": "absent" if pre_by_path.get(path) is None else "blob",
                    "pre_sha256": (
                        None
                        if pre_by_path.get(path) is None
                        else pre_by_path[path].blob_sha256
                    ),
                    "pre_size": (
                        None
                        if pre_by_path.get(path) is None
                        else pre_by_path[path].byte_size
                    ),
                    "post_kind": "absent" if post_by_path.get(path) is None else "blob",
                    "post_sha256": (
                        None
                        if post_by_path.get(path) is None
                        else post_by_path[path].blob_sha256
                    ),
                    "post_size": (
                        None
                        if post_by_path.get(path) is None
                        else post_by_path[path].byte_size
                    ),
                },
                strict=True,
            )
            for ordinal, path in enumerate(changed_paths)
        )
        intent = CheckoutPublicationIntent.model_validate(
            {
                "schema_version": CheckoutPublicationIntent.schema_id,
                "identity": identity.model_dump(mode="json"),
                "publication_identity_sha256": _publication_identity_digest(identity),
                "pre_checkout_revision_id": (
                    None if pre_record is None else pre_record.checkout_revision_id
                ),
                "post_checkout_revision_id": post_record.checkout_revision_id,
                "post_manifest_sha256": post_record.manifest_sha256,
                "post_tree_sha256": post_record.tree_sha256,
                "changed_member_count": len(members),
                "capability_profile_sha256": capability_profile_sha256,
            },
            strict=True,
        )
    except (TypeError, ValueError) as exc:
        raise _CheckoutStructureError from exc
    return intent, members


class RunContractBindingReference(StrictModel):
    run_id: ContractId


class RunExecutionAuthorizationReference(StrictModel):
    authorization_id: ContractId


class RunSourceDiscoveryAuthorizationReference(StrictModel):
    authorization_id: ContractId


class RunSourceAcquisitionAttemptAuthorizationReference(StrictModel):
    attempt_authorization_id: ContractId


class OwnedArtifactSubmissionReference(StrictModel):
    submission_id: ContractId


class StageTransitionReference(StrictModel):
    transition_id: ContractId


class StageArtifactBindingReference(StrictModel):
    transition_id: ContractId
    position: NonNegativeInt


class StageGateBindingReference(StrictModel):
    transition_id: ContractId
    gate_id: GateId


class ClaimReference(StrictModel):
    claim_id: ContractId


class ClaimSourceBindingReference(StrictModel):
    claim_id: ContractId
    source_id: ContractId


class ClaimFreezeReference(StrictModel):
    freeze_id: ContractId


class GateEvaluationReference(StrictModel):
    evaluation_id: ContractId


class GateFindingReference(StrictModel):
    evaluation_id: ContractId
    finding_id: ContractId


class GateArtifactBindingReference(StrictModel):
    evaluation_id: ContractId
    position: NonNegativeInt


class RunIntegrityReference(StrictModel):
    integrity_revision: PositiveInt


class RepairCycleReference(StrictModel):
    repair_id: ContractId


class GateRepairCycleReference(StrictModel):
    gate_repair_id: ContractId


class GateRepairArtifactBindingReference(StrictModel):
    gate_repair_id: ContractId


class GateRepairOutcomeReference(StrictModel):
    outcome_id: ContractId


class ArtifactSupersessionReference(StrictModel):
    supersession_id: ContractId


class RepairCompletionReference(StrictModel):
    repair_completion_id: ContractId


class RecoveryCompletionReference(StrictModel):
    recovery_id: ContractId


class RunHeadTransitionReference(StrictModel):
    head_transition_id: ContractId


class FinalizeRenderReference(StrictModel):
    render_id: ContractId


class FinalizationReference(StrictModel):
    finalization_id: ContractId


class RunArchiveReference(StrictModel):
    archive_id: ContractId


class RunArchiveArtifactBindingReference(StrictModel):
    archive_id: ContractId
    position: NonNegativeInt


class PackageReadyReference(StrictModel):
    package_id: ContractId


class PackageArtifactBindingReference(StrictModel):
    package_id: ContractId
    position: NonNegativeInt


class ApprovalReference(StrictModel):
    approval_id: ContractId


class ApprovalPackageBindingReference(StrictModel):
    approval_id: ContractId
    package_id: ContractId


class DeliveryAuthorizationReference(StrictModel):
    authorization_id: ContractId


class DeliveryAttemptReference(StrictModel):
    attempt_id: ContractId


class DeliveryResultReference(StrictModel):
    result_id: ContractId


class PostFinalAssessmentPolicyRevisionReference(StrictModel):
    policy_revision_id: ContractId


class PostFinalAssessmentRequestReference(StrictModel):
    assessment_request_id: ContractId


class PostFinalAssessmentAbandonmentReference(StrictModel):
    abandonment_id: ContractId


class PostFinalAssessmentExecutionReference(StrictModel):
    execution_id: ContractId


class PostFinalAssessmentResultReference(StrictModel):
    assessment_result_id: ContractId


class PostFinalFindingDispositionReference(StrictModel):
    disposition_id: ContractId


class PostFinalHumanObservationReference(StrictModel):
    observation_id: ContractId


class PostFinalGuidanceDraftReference(StrictModel):
    guidance_id: ContractId
    draft_revision: PositiveInt


class PostFinalGuidanceStatusReference(StrictModel):
    status_revision_id: ContractId


class RunGuidanceSnapshotReference(StrictModel):
    snapshot_id: ContractId


class RunGuidanceSelectionDecisionReference(StrictModel):
    decision_id: ContractId


class RunGuidanceSnapshotItemReference(StrictModel):
    item_id: ContractId


class CheckoutRevisionReference(StrictModel):
    checkout_revision_id: CheckoutRevisionId


class ReceiptCheckoutBindingReference(StrictModel):
    transaction_id: ContractId


class CheckoutPublicationIntentReference(StrictModel):
    checkout_revision_id: CheckoutRevisionId


class TransactionReceipt(StrictModel):
    schema_id = "briefloop.transaction_receipt.v2"

    schema_version: Literal["briefloop.transaction_receipt.v2"]
    transaction_id: ContractId
    run_id: ContractId
    transaction_type: ContractId
    prior_revision: NonNegativeInt
    committed_revision: PositiveInt
    committed_at: IsoDateTime
    projection_status: Literal["current", "stale"]
    event_ids: list[ContractId] = Field(default_factory=list)
    artifact_revisions: list[ArtifactRevisionReference] = Field(default_factory=list)
    artifact_identities: list[ArtifactIdentityReference] = Field(default_factory=list)
    source_ids: list[ContractId] = Field(default_factory=list)
    proposal_ids: list[ContractId] = Field(default_factory=list)
    run_contract_bindings: list[RunContractBindingReference] = Field(
        default_factory=list
    )
    run_execution_authorizations: list[RunExecutionAuthorizationReference] = Field(
        default_factory=list
    )
    run_source_discovery_authorizations: list[
        RunSourceDiscoveryAuthorizationReference
    ] = Field(default_factory=list)
    run_source_acquisition_attempt_authorizations: list[
        RunSourceAcquisitionAttemptAuthorizationReference
    ] = Field(default_factory=list)
    owned_artifact_submissions: list[OwnedArtifactSubmissionReference] = Field(
        default_factory=list
    )
    stage_transitions: list[StageTransitionReference] = Field(default_factory=list)
    stage_artifact_bindings: list[StageArtifactBindingReference] = Field(
        default_factory=list
    )
    stage_gate_bindings: list[StageGateBindingReference] = Field(default_factory=list)
    claims: list[ClaimReference] = Field(default_factory=list)
    claim_source_bindings: list[ClaimSourceBindingReference] = Field(
        default_factory=list
    )
    claim_freezes: list[ClaimFreezeReference] = Field(default_factory=list)
    gate_evaluations: list[GateEvaluationReference] = Field(default_factory=list)
    gate_findings: list[GateFindingReference] = Field(default_factory=list)
    gate_artifact_bindings: list[GateArtifactBindingReference] = Field(
        default_factory=list
    )
    run_integrity_records: list[RunIntegrityReference] = Field(default_factory=list)
    repair_cycles: list[RepairCycleReference] = Field(default_factory=list)
    gate_repair_cycles: list[GateRepairCycleReference] = Field(default_factory=list)
    gate_repair_artifact_bindings: list[GateRepairArtifactBindingReference] = Field(
        default_factory=list
    )
    gate_repair_outcomes: list[GateRepairOutcomeReference] = Field(default_factory=list)
    artifact_supersessions: list[ArtifactSupersessionReference] = Field(
        default_factory=list
    )
    repair_completions: list[RepairCompletionReference] = Field(default_factory=list)
    recovery_completions: list[RecoveryCompletionReference] = Field(
        default_factory=list
    )
    run_head_transitions: list[RunHeadTransitionReference] = Field(default_factory=list)
    finalize_renders: list[FinalizeRenderReference] = Field(default_factory=list)
    finalizations: list[FinalizationReference] = Field(default_factory=list)
    run_archives: list[RunArchiveReference] = Field(default_factory=list)
    run_archive_artifact_bindings: list[RunArchiveArtifactBindingReference] = Field(
        default_factory=list
    )
    package_ready_records: list[PackageReadyReference] = Field(default_factory=list)
    package_artifact_bindings: list[PackageArtifactBindingReference] = Field(
        default_factory=list
    )
    approvals: list[ApprovalReference] = Field(default_factory=list)
    approval_package_bindings: list[ApprovalPackageBindingReference] = Field(
        default_factory=list
    )
    delivery_authorizations: list[DeliveryAuthorizationReference] = Field(
        default_factory=list
    )
    delivery_attempts: list[DeliveryAttemptReference] = Field(default_factory=list)
    delivery_results: list[DeliveryResultReference] = Field(default_factory=list)
    post_final_assessment_policy_revisions: list[
        PostFinalAssessmentPolicyRevisionReference
    ] = Field(default_factory=list)
    post_final_assessment_requests: list[PostFinalAssessmentRequestReference] = Field(
        default_factory=list
    )
    post_final_assessment_abandonments: list[
        PostFinalAssessmentAbandonmentReference
    ] = Field(default_factory=list)
    post_final_assessment_results: list[PostFinalAssessmentResultReference] = Field(
        default_factory=list
    )
    post_final_finding_dispositions: list[PostFinalFindingDispositionReference] = Field(
        default_factory=list
    )
    post_final_human_observations: list[PostFinalHumanObservationReference] = Field(
        default_factory=list
    )
    post_final_guidance_drafts: list[PostFinalGuidanceDraftReference] = Field(
        default_factory=list
    )
    post_final_guidance_statuses: list[PostFinalGuidanceStatusReference] = Field(
        default_factory=list
    )
    run_guidance_snapshots: list[RunGuidanceSnapshotReference] = Field(
        default_factory=list
    )
    run_guidance_selection_decisions: list[RunGuidanceSelectionDecisionReference] = (
        Field(default_factory=list)
    )
    run_guidance_snapshot_items: list[RunGuidanceSnapshotItemReference] = Field(
        default_factory=list
    )
    checkout_revisions: list[CheckoutRevisionReference] = Field(default_factory=list)
    receipt_checkout_bindings: list[ReceiptCheckoutBindingReference] = Field(
        default_factory=list
    )
    checkout_publication_intents: list[CheckoutPublicationIntentReference] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def revision_advances(self) -> "TransactionReceipt":
        if self.committed_revision <= self.prior_revision:
            raise ValueError("committed revision must advance")
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("duplicate event identity")
        artifact_keys = [
            (item.artifact_id, item.revision) for item in self.artifact_revisions
        ]
        if len(artifact_keys) != len(set(artifact_keys)):
            raise ValueError("duplicate artifact revision identity")
        identity_keys = [item.artifact_id for item in self.artifact_identities]
        if len(identity_keys) != len(set(identity_keys)):
            raise ValueError("duplicate artifact identity")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("duplicate source identity")
        if len(self.proposal_ids) != len(set(self.proposal_ids)):
            raise ValueError("duplicate proposal identity")
        relation_lists = (
            self.run_contract_bindings,
            self.run_execution_authorizations,
            self.run_source_discovery_authorizations,
            self.run_source_acquisition_attempt_authorizations,
            self.owned_artifact_submissions,
            self.stage_transitions,
            self.stage_artifact_bindings,
            self.stage_gate_bindings,
            self.claims,
            self.claim_source_bindings,
            self.claim_freezes,
            self.gate_evaluations,
            self.gate_findings,
            self.gate_artifact_bindings,
            self.run_integrity_records,
            self.repair_cycles,
            self.gate_repair_cycles,
            self.gate_repair_artifact_bindings,
            self.gate_repair_outcomes,
            self.artifact_supersessions,
            self.repair_completions,
            self.recovery_completions,
            self.run_head_transitions,
            self.finalize_renders,
            self.finalizations,
            self.run_archives,
            self.run_archive_artifact_bindings,
            self.package_ready_records,
            self.package_artifact_bindings,
            self.approvals,
            self.approval_package_bindings,
            self.delivery_authorizations,
            self.delivery_attempts,
            self.delivery_results,
            self.post_final_assessment_policy_revisions,
            self.post_final_assessment_requests,
            self.post_final_assessment_abandonments,
            self.post_final_assessment_results,
            self.post_final_finding_dispositions,
            self.post_final_human_observations,
            self.post_final_guidance_drafts,
            self.post_final_guidance_statuses,
            self.run_guidance_snapshots,
            self.run_guidance_selection_decisions,
            self.run_guidance_snapshot_items,
            self.checkout_revisions,
            self.receipt_checkout_bindings,
            self.checkout_publication_intents,
        )
        for values in relation_lists:
            keys = [item.model_dump_json() for item in values]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate transaction relation identity")
        return self


_RUN = "RUN-20260714-001"
_NOW = "2026-07-14T09:00:00Z"
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64

SourceProposal.minimal_example = {
    "schema_version": SourceProposal.schema_id,
    "proposal_id": "PROP-SOURCE-001",
    "run_id": _RUN,
    "source_id": "SRC-001",
    "origin_type": "uploaded_file",
    "acquisition_method": "manual_upload",
    "material_kind": "uploaded_file",
    "locator": {"kind": "file", "path": "scratch/INV-SOURCE-001/source_content.pdf"},
    "title": "Uploaded public filing",
    "retrieved_at": _NOW,
    "source_category": "regulator",
    "retrieval_source_type": "local_file",
    "underlying_evidence_type": "filing",
    "content_sha256": _SHA_A,
    "content_media_type": "application/pdf",
}
SourceProposal.full_example = {
    "schema_version": SourceProposal.schema_id,
    "proposal_id": "PROP-SOURCE-002",
    "run_id": _RUN,
    "source_id": "SRC-002",
    "origin_type": "provider_response",
    "acquisition_method": "provider_extract",
    "material_kind": "full_content",
    "provider": "tavily",
    "locator": {"kind": "web", "url": "https://example.com/report"},
    "title": "Public source",
    "publisher": "Example Publisher",
    "published_at": "2026-07-13",
    "retrieved_at": _NOW,
    "source_category": "market_report",
    "retrieval_source_type": "paper_page",
    "underlying_evidence_type": "market_data",
    "raw_underlying_evidence_type": "research-report",
    "content_sha256": _SHA_A,
    "content_media_type": "text/html",
    "raw_payload_sha256": _SHA_B,
    "raw_payload_media_type": "application/json",
}

SourceCommitRequest.minimal_example = {
    "schema_version": SourceCommitRequest.schema_id,
    "request_id": "REQ-SOURCE-001",
    "run_id": _RUN,
    "invocation_id": "INV-SOURCE-001",
    "proposal_path": "scratch/INV-SOURCE-001/source_proposal.json",
    "content_path": "scratch/INV-SOURCE-001/source_content.pdf",
    "expected_store_revision": 1,
}
SourceCommitRequest.full_example = {
    **SourceCommitRequest.minimal_example,
    "raw_payload_path": "scratch/INV-SOURCE-001/source_raw.json",
}
SourcePackCommitRequest.minimal_example = {
    "schema_version": SourcePackCommitRequest.schema_id,
    "request_id": "REQ-SOURCE-PACK-001",
    "run_id": "RUN-001",
    "invocation_id": "INV-SOURCE-001",
    "members": [
        {
            "member_id": "SRC-MEMBER-0001",
            "proposal_path": "scratch/INV-SOURCE-001/sources/SRC-MEMBER-0001/source_proposal.json",
            "content_path": "scratch/INV-SOURCE-001/sources/SRC-MEMBER-0001/source_content.txt",
            "raw_payload_path": None,
        }
    ],
    "expected_store_revision": 1,
}
SourcePackCommitRequest.full_example = deepcopy(SourcePackCommitRequest.minimal_example)
MultiTavilySourcePackCommitRequest.minimal_example = {
    "schema_version": MultiTavilySourcePackCommitRequest.schema_id,
    "capacity_profile": "multi_tavily_v2",
    "request_id": "REQ-MULTI-TAVILY-PACK-001",
    "run_id": "RUN-001",
    "invocation_id": "INV-SOURCE-001",
    "members": [
        {
            "member_id": "SRC-MEMBER-0001",
            "proposal_path": "scratch/INV-SOURCE-001/sources/SRC-MEMBER-0001/source_proposal.json",
            "content_path": "scratch/INV-SOURCE-001/sources/SRC-MEMBER-0001/source_content.txt",
            "raw_payload_path": "scratch/INV-SOURCE-001/sources/SRC-MEMBER-0001/source_raw.json",
        }
    ],
    "manifest_path": "scratch/INV-SOURCE-001/source_manifest.json",
    "expected_manifest_sha256": _SHA_A,
    "expected_store_revision": 1,
}
MultiTavilySourcePackCommitRequest.full_example = deepcopy(
    MultiTavilySourcePackCommitRequest.minimal_example
)

_CANDIDATE = {
    "candidate_id": "CAND-001",
    "source_id": "SRC-001",
    "statement": "ExampleCo opened a public pilot facility.",
    "evidence_text": "The release says the facility opened on 13 July.",
    "topic": "operations",
    "claim_type": "fact",
    "confidence": "high",
}
CandidateClaimsProposal.minimal_example = {
    "schema_version": CandidateClaimsProposal.schema_id,
    "proposal_id": "PROP-CANDIDATES-001",
    "run_id": _RUN,
    "created_at": _NOW,
    "candidates": [_CANDIDATE],
}
CandidateClaimsProposal.full_example = deepcopy(CandidateClaimsProposal.minimal_example)
CandidateClaimsProposal.full_example["candidates"].append(
    {**_CANDIDATE, "candidate_id": "CAND-002", "confidence": "medium"}
)

ScreenedCandidatesProposal.minimal_example = {
    "schema_version": ScreenedCandidatesProposal.schema_id,
    "proposal_id": "PROP-SCREENED-001",
    "run_id": _RUN,
    "candidate_claims_proposal_id": "PROP-CANDIDATES-001",
    "created_at": _NOW,
    "decisions": [{"candidate_id": "CAND-001", "decision": "selected"}],
}
ScreenedCandidatesProposal.full_example = {
    **ScreenedCandidatesProposal.minimal_example,
    "decisions": [
        {"candidate_id": "CAND-001", "decision": "selected"},
        {
            "candidate_id": "CAND-002",
            "decision": "deprioritized",
            "reason_code": "LOW-MATERIALITY",
            "explanation": "The item is background context only.",
        },
    ],
}

_DRAFT = {
    "draft_id": "DRAFT-001",
    "statement": "ExampleCo opened a public pilot facility.",
    "evidence_text": "The release says the facility opened on 13 July.",
    "source_ids": ["SRC-001"],
    "claim_type": "fact",
}
ClaimDraftsProposal.minimal_example = {
    "schema_version": ClaimDraftsProposal.schema_id,
    "proposal_id": "PROP-DRAFTS-001",
    "run_id": _RUN,
    "screened_candidates_proposal_id": "PROP-SCREENED-001",
    "created_at": _NOW,
    "drafts": [_DRAFT],
}
ClaimDraftsProposal.full_example = deepcopy(ClaimDraftsProposal.minimal_example)

AuditProposal.minimal_example = {
    "schema_version": AuditProposal.schema_id,
    "proposal_id": "PROP-AUDIT-001",
    "run_id": _RUN,
    "artifact_id": "audited_brief",
    "artifact_revision": 1,
    "decision": "pass",
    "created_at": _NOW,
}
AuditProposal.full_example = {
    **AuditProposal.minimal_example,
    "decision": "warning",
    "findings": [
        {
            "finding_code": "SOURCE-AGE",
            "severity": "warning",
            "artifact_id": "audited_brief",
            "summary": "One background source is older than the preferred window.",
        }
    ],
}

ArtifactSubmitRequest.minimal_example = {
    "schema_version": ArtifactSubmitRequest.schema_id,
    "request_id": "REQ-ARTIFACT-001",
    "run_id": _RUN,
    "artifact_id": "candidate_claims",
    "invocation_id": "INV-SCOUT-001",
    "input_path": "scratch/INV-SCOUT-001/candidate_claims.json",
    "expected_store_revision": 1,
    "expected_artifact_revision": 0,
}
ArtifactSubmitRequest.full_example = {
    **ArtifactSubmitRequest.minimal_example,
    "expected_store_revision": 2,
    "expected_artifact_revision": 1,
}

WorkspaceRunHead.minimal_example = {
    "schema_version": WorkspaceRunHead.schema_id,
    "workspace_id": "WS-PUBLIC-DEMO",
    "current_run_id": _RUN,
    "updated_at": _NOW,
}
WorkspaceRunHead.full_example = deepcopy(WorkspaceRunHead.minimal_example)

AcceptedSourceRecord.minimal_example = {
    "schema_version": AcceptedSourceRecord.schema_id,
    "source_id": "SRC-001",
    "run_id": _RUN,
    "origin_type": "uploaded_file",
    "acquisition_method": "manual_upload",
    "material_kind": "uploaded_file",
    "locator": {"kind": "file", "path": "scratch/INV-SOURCE-001/source_content.pdf"},
    "title": "Uploaded public filing",
    "retrieved_at": _NOW,
    "source_category": "regulator",
    "retrieval_source_type": "local_file",
    "underlying_evidence_type": "filing",
    "content_sha256": _SHA_A,
    "content_size_bytes": 100,
    "content_media_type": "application/pdf",
    "content_blob_path": f"briefloop.db.blobs/sha256/{_SHA_A[:2]}/{_SHA_A}",
    "content_artifact_id": "SRC-CONTENT-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "content_artifact_revision": 1,
    "claims_eligible": True,
    "eligibility_reason": "eligible_durable_source_content",
    "invocation_id": "INV-SOURCE-001",
    "acquisition_event_id": "EVT-SOURCE-001",
    "accepted_transaction_id": "REQ-SOURCE-001",
    "request_fingerprint": _SHA_B,
    "created_at": _NOW,
}
AcceptedSourceRecord.full_example = {
    **AcceptedSourceRecord.minimal_example,
    "source_id": "SRC-002",
    "origin_type": "provider_response",
    "acquisition_method": "provider_extract",
    "material_kind": "full_content",
    "provider": "tavily",
    "locator": {"kind": "web", "url": "https://example.com/report"},
    "publisher": "Example Publisher",
    "raw_underlying_evidence_type": "research-report",
    "raw_payload_sha256": _SHA_B,
    "raw_payload_size_bytes": 200,
    "raw_payload_media_type": "application/json",
    "raw_payload_blob_path": f"briefloop.db.blobs/sha256/{_SHA_B[:2]}/{_SHA_B}",
    "raw_payload_artifact_id": "SRC-RAW-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "raw_payload_artifact_revision": 1,
}

AcceptedProposalRecord.minimal_example = {
    "schema_version": AcceptedProposalRecord.schema_id,
    "proposal_id": "PROP-CANDIDATES-001",
    "run_id": _RUN,
    "proposal_kind": "candidate",
    "artifact_id": "candidate_claims",
    "artifact_revision": 1,
    "proposal_sha256": _SHA_A,
    "invocation_id": "INV-SCOUT-001",
    "owner_stage_id": "scout",
    "owner_role_id": "scout",
    "source_ids": ["SRC-001"],
    "accepted_event_id": "EVT-PROPOSAL-001",
    "accepted_transaction_id": "REQ-PROPOSAL-001",
    "request_fingerprint": _SHA_B,
    "created_at": _NOW,
}
AcceptedProposalRecord.full_example = {
    **AcceptedProposalRecord.minimal_example,
    "proposal_id": "PROP-SCREENED-001",
    "proposal_kind": "screened",
    "artifact_id": "screened_candidates",
    "invocation_id": "INV-SCREENER-001",
    "owner_stage_id": "screener",
    "owner_role_id": "screener",
    "parent_proposal_id": "PROP-CANDIDATES-001",
    "source_ids": [],
}

ProposalSourceBinding.minimal_example = {
    "schema_version": ProposalSourceBinding.schema_id,
    "run_id": _RUN,
    "proposal_id": "PROP-CANDIDATES-001",
    "source_id": "SRC-001",
}
ProposalSourceBinding.full_example = deepcopy(ProposalSourceBinding.minimal_example)

RunIdentity.minimal_example = {
    "schema_version": RunIdentity.schema_id,
    "run_id": _RUN,
    "workspace_id": "WS-PUBLIC-DEMO",
    "runtime": "operator",
    "created_at": _NOW,
}
RunIdentity.full_example = {**RunIdentity.minimal_example, "runtime": "codebuddy"}

StageState.minimal_example = {
    "schema_version": StageState.schema_id,
    "run_id": _RUN,
    "stage_id": "scout",
    "status": "pending",
    "revision": 0,
    "updated_at": _NOW,
}
StageState.full_example = {
    **StageState.minimal_example,
    "status": "complete",
    "revision": 1,
}

ArtifactRecord.minimal_example = {
    "schema_version": ArtifactRecord.schema_id,
    "run_id": _RUN,
    "artifact_id": "candidate_claims",
    "current_revision": 0,
    "status": "expected",
    "required": True,
    "path": "output/intermediate/candidate_claims.json",
    "format": "json",
}
ArtifactRecord.full_example = {
    **ArtifactRecord.minimal_example,
    "current_revision": 1,
    "status": "valid",
}

ArtifactIdentityRecord.minimal_example = {
    "schema_version": ArtifactIdentityRecord.schema_id,
    "run_id": _RUN,
    "artifact_id": "candidate_claims",
    "required": True,
    "initial_path": "output/intermediate/candidate_claims.json",
    "format": "json",
    "accepted_transaction_id": "TX-001",
}
ArtifactIdentityRecord.full_example = deepcopy(ArtifactIdentityRecord.minimal_example)

ArtifactRevision.minimal_example = {
    "schema_version": ArtifactRevision.schema_id,
    "run_id": _RUN,
    "artifact_id": "candidate_claims",
    "revision": 1,
    "path": f"output/artifacts/{_SHA_A}/candidate_claims.json",
    "sha256": _SHA_A,
    "size_bytes": 256,
    "frozen": True,
    "producer_kind": "workflow_stage",
    "producer_id": "scout",
    "created_at": _NOW,
}
ArtifactRevision.full_example = deepcopy(ArtifactRevision.minimal_example)

EventEnvelope.minimal_example = {
    "schema_version": EventEnvelope.schema_id,
    "event_id": "EVT-001",
    "run_id": _RUN,
    "event_type": "stage_status_changed",
    "created_at": _NOW,
    "actor": "cli",
}
EventEnvelope.full_example = {
    **EventEnvelope.minimal_example,
    "transaction_id": "TX-001",
    "stage_id": "scout",
    "decision": "continue",
    "reason": "Scout stage became complete.",
    "metadata": {"previous_status": "ready", "status": "complete"},
}

Invocation.minimal_example = {
    "schema_version": Invocation.schema_id,
    "invocation_id": "INV-001",
    "run_id": _RUN,
    "role_id": "scout",
    "runtime": "operator",
    "status": "active",
    "started_at": _NOW,
}
Invocation.full_example = {
    **Invocation.minimal_example,
    "status": "completed",
    "completed_at": "2026-07-14T09:00:01Z",
}

Approval.minimal_example = {
    "schema_version": Approval.schema_id,
    "approval_id": "APR-001",
    "run_id": _RUN,
    "mode": "internal_management_review",
    "role": "content_owner",
    "decision": "approve",
    "reason": "Reader-facing brief reviewed.",
    "actor_id": "human-operator",
    "recorded_at": _NOW,
    "boundary": "internal_review_approval_records_only_not_public_release_authorization",
    "event_id": "EVT-APPROVAL-001",
}
Approval.full_example = {
    **Approval.minimal_example,
    "mode": "formal_release_candidate",
    "role": "legal_or_compliance_reviewer",
    "decision": "request_changes",
    "reason": "Clarify the public limitation wording.",
}

Delivery.minimal_example = {
    "schema_version": Delivery.schema_id,
    "delivery_id": "DEL-001",
    "run_id": _RUN,
    "artifact_id": "brief",
    "artifact_revision": 1,
    "status": "bundle_prepared",
    "target": "local",
    "channel": "local",
    "created_at": _NOW,
}
Delivery.full_example = {
    **Delivery.minimal_example,
    "approval_id": "APR-001",
    "status": "succeeded",
    "target": "gmail",
    "channel": "send",
    "completed_at": "2026-07-14T09:00:01Z",
}

TransactionReceipt.minimal_example = {
    "schema_version": TransactionReceipt.schema_id,
    "transaction_id": "TX-001",
    "run_id": _RUN,
    "transaction_type": "stage_complete",
    "prior_revision": 0,
    "committed_revision": 1,
    "committed_at": "2026-07-14T09:00:01Z",
    "projection_status": "current",
}
TransactionReceipt.full_example = {
    **TransactionReceipt.minimal_example,
    "event_ids": ["EVT-001"],
    "artifact_revisions": [{"artifact_id": "candidate_claims", "revision": 1}],
    "artifact_identities": [{"artifact_id": "candidate_claims"}],
    "proposal_ids": ["PROP-CANDIDATES-001"],
}

_RUN_DIRECTION = {
    "schema_version": RunDirection.schema_id,
    "subject_name": "ExampleCo",
    "industry_or_theme": "synthetic operations",
    "brief_title": "ExampleCo weekly brief",
    "task_objective": "Summarize the supplied public evidence.",
    "audience": "management",
    "audience_profile": "management",
    "output_language": "en",
    "source_handling": "local_first",
    "cadence": "weekly",
    "focus_areas": ["operations"],
    "excluded_topics": [],
    "forbidden_sources": [],
    "source_profile": "public_safe",
    "web_search_mode": "disabled",
    "search_backend": None,
    "output_style": "concise",
    "output_formats": ["markdown", "docx"],
    "report_date": "2026-07-14",
    "report_window_start": "2026-07-07",
    "report_window_end": "2026-07-14",
    "max_source_age_days": 30,
    "target_terms": ["ExampleCo"],
}
RunDirection.minimal_example = deepcopy(_RUN_DIRECTION)
RunDirection.full_example = deepcopy(_RUN_DIRECTION)

_GATE_STRICTNESS = {gate_id: True for gate_id in GATE_ID_VALUES}
_EXECUTION_SOURCE_MEMBER = {
    "source_id": "SRC-INIT-001",
    "input_path": "input/evidence/source-001.txt",
    "content_sha256": _SHA_A,
    "content_media_type": "text/plain",
    "origin_type": "manual_evidence",
    "acquisition_method": "manual_evidence",
    "material_kind": "full_content",
    "provider": None,
    "locator": {"kind": "file", "path": "input/evidence/source-001.txt"},
    "title": "Public example source",
    "publisher": "Example publisher",
    "published_at": "2026-07-14",
    "retrieved_at": _NOW,
    "source_category": "other",
    "retrieval_source_type": "local_file",
    "underlying_evidence_type": "unknown",
    "raw_underlying_evidence_type": None,
    "document_kind": None,
    "opened_at": None,
    "resolved_at": None,
}
ExecutionSourceManifestMember.minimal_example = deepcopy(_EXECUTION_SOURCE_MEMBER)
ExecutionSourceManifestMember.full_example = deepcopy(_EXECUTION_SOURCE_MEMBER)
_EXECUTION_SOURCE_MANIFEST = {
    "schema_version": ExecutionSourceManifest.schema_id,
    "members": [deepcopy(_EXECUTION_SOURCE_MEMBER)],
}
ExecutionSourceManifest.minimal_example = deepcopy(_EXECUTION_SOURCE_MANIFEST)
ExecutionSourceManifest.full_example = deepcopy(_EXECUTION_SOURCE_MANIFEST)
_MULTI_TAVILY_EXECUTION_SOURCE_MEMBER = {
    **deepcopy(_EXECUTION_SOURCE_MEMBER),
    "origin_type": "provider_response",
    "acquisition_method": "provider_extract",
    "provider": "tavily",
    "locator": {"kind": "web", "url": "https://example.com/report"},
    "retrieval_source_type": "paper_page",
    "raw_underlying_evidence_type": "provider-extracted-document",
}
_MULTI_TAVILY_EXECUTION_SOURCE_MANIFEST = {
    "schema_version": MultiTavilyExecutionSourceManifest.schema_id,
    "capacity_profile": "multi_tavily_v2",
    "members": [deepcopy(_MULTI_TAVILY_EXECUTION_SOURCE_MEMBER)],
}
MultiTavilyExecutionSourceManifest.minimal_example = deepcopy(
    _MULTI_TAVILY_EXECUTION_SOURCE_MANIFEST
)
MultiTavilyExecutionSourceManifest.full_example = deepcopy(
    _MULTI_TAVILY_EXECUTION_SOURCE_MANIFEST
)
_EXECUTION_SOURCE_MANIFEST_SHA = hashlib.sha256(
    json.dumps(
        _EXECUTION_SOURCE_MANIFEST,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
_EXECUTION_AUTHORIZATION_INPUT = {
    "schema_version": RunExecutionAuthorizationInput.schema_id,
    "completion_target": "finalized_local",
    "source_manifest": deepcopy(_EXECUTION_SOURCE_MANIFEST),
    "source_manifest_sha256": _EXECUTION_SOURCE_MANIFEST_SHA,
    "source_manifest_member_count": 1,
    "repair_budget": 1,
}
RunExecutionAuthorizationInput.minimal_example = deepcopy(
    _EXECUTION_AUTHORIZATION_INPUT
)
RunExecutionAuthorizationInput.full_example = deepcopy(_EXECUTION_AUTHORIZATION_INPUT)
_EXECUTION_AUTHORIZATION_BOOTSTRAP = {
    "schema_version": RunExecutionAuthorizationBootstrap.schema_id,
    "completion_target": "finalized_local",
    "source_manifest_path": "input/execution-source-manifest.json",
    "source_manifest_sha256": _EXECUTION_SOURCE_MANIFEST_SHA,
    "source_manifest_member_count": 1,
    "repair_budget": 1,
}
RunExecutionAuthorizationBootstrap.minimal_example = deepcopy(
    _EXECUTION_AUTHORIZATION_BOOTSTRAP
)
RunExecutionAuthorizationBootstrap.full_example = deepcopy(
    _EXECUTION_AUTHORIZATION_BOOTSTRAP
)
RunExecutionAuthorization.minimal_example = {
    "schema_version": RunExecutionAuthorization.schema_id,
    "authorization_id": "EXEC-AUTH-001",
    "run_id": _RUN,
    "workspace_id": "WS-PUBLIC-DEMO",
    "run_contract_fingerprint": _SHA_A,
    "run_direction_fingerprint": _SHA_B,
    "completion_target": "finalized_local",
    "source_manifest_artifact": {
        "artifact_id": "run_execution_source_manifest",
        "revision": 1,
    },
    "source_manifest_sha256": _EXECUTION_SOURCE_MANIFEST_SHA,
    "source_manifest_member_count": 1,
    "repair_budget": 1,
    "authorization_event_id": "EVT-EXEC-AUTH-001",
    "accepted_transaction_id": "TXN-001",
    "request_fingerprint": _SHA_A,
    "created_at": _NOW,
}
RunExecutionAuthorization.full_example = deepcopy(
    RunExecutionAuthorization.minimal_example
)
_SOURCE_DISCOVERY_AUTHORIZATION_INPUT = {
    "schema_version": RunSourceDiscoveryAuthorizationInput.schema_id,
    "route_id": "web-search",
    "provider_id": "tavily",
    "execution_owner": "deterministic",
    "credential_env": "TAVILY_API_KEY",
    "completion_target": "finalized_local",
    "repair_budget": 1,
}
RunSourceDiscoveryAuthorizationInput.minimal_example = deepcopy(
    _SOURCE_DISCOVERY_AUTHORIZATION_INPUT
)
RunSourceDiscoveryAuthorizationInput.full_example = deepcopy(
    _SOURCE_DISCOVERY_AUTHORIZATION_INPUT
)
_SOURCE_DISCOVERY_AUTHORIZATION_BOOTSTRAP = {
    "schema_version": RunSourceDiscoveryAuthorizationBootstrap.schema_id,
    "route_id": "web-search",
    "provider_id": "tavily",
    "execution_owner": "deterministic",
    "credential_env": "TAVILY_API_KEY",
    "completion_target": "finalized_local",
    "repair_budget": 1,
}
RunSourceDiscoveryAuthorizationBootstrap.minimal_example = deepcopy(
    _SOURCE_DISCOVERY_AUTHORIZATION_BOOTSTRAP
)
RunSourceDiscoveryAuthorizationBootstrap.full_example = deepcopy(
    _SOURCE_DISCOVERY_AUTHORIZATION_BOOTSTRAP
)
RunSourceDiscoveryAuthorization.minimal_example = {
    "schema_version": RunSourceDiscoveryAuthorization.schema_id,
    "authorization_id": "DISCOVERY-AUTH-001",
    "run_id": _RUN,
    "workspace_id": "WS-PUBLIC-DEMO",
    "run_contract_fingerprint": _SHA_A,
    "run_direction_fingerprint": _SHA_B,
    "runtime_source_plan_fingerprint": _SHA_C,
    "source_route_fingerprint": _SHA_D,
    "route_id": "web-search",
    "provider_id": "tavily",
    "execution_owner": "deterministic",
    "credential_env": "TAVILY_API_KEY",
    "completion_target": "finalized_local",
    "repair_budget": 1,
    "authorization_event_id": "EVT-DISCOVERY-AUTH-001",
    "accepted_transaction_id": "TXN-001",
    "request_fingerprint": _SHA_A,
    "created_at": _NOW,
}
RunSourceDiscoveryAuthorization.full_example = deepcopy(
    RunSourceDiscoveryAuthorization.minimal_example
)
RunSourceAcquisitionAttemptAuthorization.minimal_example = {
    "schema_version": RunSourceAcquisitionAttemptAuthorization.schema_id,
    "attempt_authorization_id": "ATTEMPT-AUTH-001",
    "attempt_ordinal": 1,
    "run_id": _RUN,
    "workspace_id": "WS-001",
    "discovery_authorization_id": "DISCOVERY-AUTH-001",
    "run_contract_fingerprint": _SHA_A,
    "run_direction_fingerprint": _SHA_B,
    "runtime_source_plan_fingerprint": _SHA_C,
    "source_route_fingerprint": _SHA_D,
    "provider_request_fingerprint": _SHA_A,
    "provider_id": "tavily",
    "route_id": "web-search",
    "max_provider_calls": 4,
    "max_search_calls": 2,
    "max_extract_calls": 2,
    "max_extract_urls": 40,
    "provider_call_sequence": (
        "primary_search_extract_then_conditional_backfill_search_extract"
    ),
    "provider_cost_status": "not_reported_acknowledged",
    "previous_attempt_authorization_id": None,
    "human_request_id": "REQ-INIT-001",
    "authorization_event_id": "EVT-INIT-001",
    "accepted_transaction_id": "REQ-INIT-001",
    "request_fingerprint": _SHA_B,
    "created_at": _NOW,
}
RunSourceAcquisitionAttemptAuthorization.full_example = {
    **RunSourceAcquisitionAttemptAuthorization.minimal_example,
    "attempt_authorization_id": "ATTEMPT-AUTH-002",
    "attempt_ordinal": 2,
    "previous_attempt_authorization_id": "ATTEMPT-AUTH-001",
    "human_request_id": "REQ-ATTEMPT-002",
    "authorization_event_id": "EVT-ATTEMPT-002",
    "accepted_transaction_id": "REQ-ATTEMPT-002",
}
_TAVILY_SEARCH_REQUEST_EXAMPLE = b'{"include_raw_content":false,"max_results":5,"query":"ExampleCo","search_depth":"basic","time_range":"week","topic":"news"}'
_TAVILY_SEARCH_RESPONSE_EXAMPLE = b'{"results":[]}'
_TAVILY_ACQUISITION_BUNDLE_EXAMPLE = {
    "schema_version": TavilyAcquisitionBundle.schema_id,
    "provider_id": "tavily",
    "status": "search_results_empty",
    "search": {
        "operation": "search",
        "endpoint": "/search",
        "request_body_base64": base64.b64encode(_TAVILY_SEARCH_REQUEST_EXAMPLE).decode(
            "ascii"
        ),
        "request_body_sha256": hashlib.sha256(
            _TAVILY_SEARCH_REQUEST_EXAMPLE
        ).hexdigest(),
        "request_body_size_bytes": len(_TAVILY_SEARCH_REQUEST_EXAMPLE),
        "response_body_base64": base64.b64encode(
            _TAVILY_SEARCH_RESPONSE_EXAMPLE
        ).decode("ascii"),
        "response_body_sha256": hashlib.sha256(
            _TAVILY_SEARCH_RESPONSE_EXAMPLE
        ).hexdigest(),
        "response_body_size_bytes": len(_TAVILY_SEARCH_RESPONSE_EXAMPLE),
        "status_code": 200,
    },
    "extract": None,
    "extract_urls": [],
    "outcomes": [],
}
TavilyAcquisitionBundle.minimal_example = deepcopy(_TAVILY_ACQUISITION_BUNDLE_EXAMPLE)
TavilyAcquisitionBundle.full_example = deepcopy(_TAVILY_ACQUISITION_BUNDLE_EXAMPLE)
_TAVILY_MULTI_SEARCH_REQUEST_EXAMPLE = b'{"auto_parameters":false,"include_answer":false,"include_raw_content":false,"max_results":20,"query":"ExampleCo catalyst","search_depth":"advanced","time_range":"week","topic":"news"}'
_TAVILY_MULTI_SEARCH_EXCHANGE_EXAMPLE = {
    "operation": "search",
    "endpoint": "/search",
    "request_body_base64": base64.b64encode(
        _TAVILY_MULTI_SEARCH_REQUEST_EXAMPLE
    ).decode("ascii"),
    "request_body_sha256": hashlib.sha256(
        _TAVILY_MULTI_SEARCH_REQUEST_EXAMPLE
    ).hexdigest(),
    "request_body_size_bytes": len(_TAVILY_MULTI_SEARCH_REQUEST_EXAMPLE),
    "response_body_base64": None,
    "response_body_sha256": None,
    "response_body_size_bytes": None,
    "status_code": None,
    "transport_error_class": "timeout",
}
TavilySearchTaskExchange.minimal_example = {
    "task_id": "solar-stock-task-01",
    "phase": "primary",
    "status": "unavailable",
    "exchange": deepcopy(_TAVILY_MULTI_SEARCH_EXCHANGE_EXAMPLE),
    "discovered_urls": [],
}
TavilySearchTaskExchange.full_example = deepcopy(
    TavilySearchTaskExchange.minimal_example
)
TavilyExtractBatchExchange.minimal_example = {
    "phase": "primary",
    "batch_ordinal": 1,
    "status": "unavailable",
    "exchange": {
        **deepcopy(_TAVILY_MULTI_SEARCH_EXCHANGE_EXAMPLE),
        "operation": "extract",
        "endpoint": "/extract",
    },
    "urls": ["https://example.com/report"],
    "outcomes": [],
}
TavilyExtractBatchExchange.full_example = deepcopy(
    TavilyExtractBatchExchange.minimal_example
)
TavilyTaskAcquisitionStatus.minimal_example = {
    "task_id": "solar-stock-task-01",
    "primary_search_ordinal": 1,
    "backfill_search_ordinal": None,
    "discovered_unique_url_count": 0,
    "extracted_success_count": 0,
    "minimum_extract_successes": 2,
    "status": "search_unavailable",
}
TavilyTaskAcquisitionStatus.full_example = deepcopy(
    TavilyTaskAcquisitionStatus.minimal_example
)
TavilyAcquisitionBundleV2.minimal_example = {
    "schema_version": TavilyAcquisitionBundleV2.schema_id,
    "provider_id": "tavily",
    "status": "failed",
    "searches": [deepcopy(TavilySearchTaskExchange.minimal_example)],
    "extract_batches": [],
    "unique_urls": [],
    "task_statuses": [deepcopy(TavilyTaskAcquisitionStatus.minimal_example)],
}
TavilyAcquisitionBundleV2.full_example = deepcopy(
    TavilyAcquisitionBundleV2.minimal_example
)
_TAVILY_ACQUISITION_BUNDLE_RECORD_V2 = {
    "schema_version": TavilyAcquisitionBundleRecordV2.schema_id,
    "bundle_record_id": "TAVILY-BUNDLE-RECORD-001",
    "run_id": _RUN,
    "attempt_authorization_id": "ATTEMPT-AUTH-001",
    "provider_response_artifact_id": "ARTIFACT-PROVIDER-RESPONSE-001",
    "provider_response_sha256": _SHA_A,
    "bundle_status": "failed",
    "search_count": 1,
    "extract_batch_count": 0,
    "unique_url_count": 0,
    "durable_content_count": 0,
    "record_event_id": "EVT-TAVILY-BUNDLE-001",
    "accepted_transaction_id": "TXN-TAVILY-BUNDLE-001",
    "recorded_at": _NOW,
    "record_fingerprint": "0" * 64,
}
_TAVILY_ACQUISITION_BUNDLE_RECORD_V2["record_fingerprint"] = _contract_fingerprint(
    _TAVILY_ACQUISITION_BUNDLE_RECORD_V2,
    field="record_fingerprint",
)
TavilyAcquisitionBundleRecordV2.minimal_example = deepcopy(
    _TAVILY_ACQUISITION_BUNDLE_RECORD_V2
)
TavilyAcquisitionBundleRecordV2.full_example = deepcopy(
    _TAVILY_ACQUISITION_BUNDLE_RECORD_V2
)
SourceAcquisitionAttemptAuthorizeRequest.minimal_example = {
    "schema_version": SourceAcquisitionAttemptAuthorizeRequest.schema_id,
    "request_id": "REQ-ATTEMPT-002",
    "run_id": _RUN,
    "expected_store_revision": 4,
    "expected_action_fingerprint": _SHA_A,
    "previous_attempt_authorization_id": "ATTEMPT-AUTH-001",
    "human_confirmation": True,
    "provider_cost_status": "not_reported_acknowledged",
}
SourceAcquisitionAttemptAuthorizeRequest.full_example = deepcopy(
    SourceAcquisitionAttemptAuthorizeRequest.minimal_example
)
WorkspaceControlStoreBootstrapV2.minimal_example = {
    "schema_version": WorkspaceControlStoreBootstrapV2.schema_id,
    "workspace_id": "WS-PUBLIC-DEMO",
    "run_id": _RUN,
    "runtime": "codex",
    "role_topology": "single_session",
    "input_governance_required": True,
    "gate_strictness": deepcopy(_GATE_STRICTNESS),
    "run_direction": deepcopy(_RUN_DIRECTION),
}
WorkspaceControlStoreBootstrapV2.full_example = deepcopy(
    WorkspaceControlStoreBootstrapV2.minimal_example
)
_RUNTIME_ADAPTER_BINDING = {
    "schema_version": RuntimeAdapterBinding.schema_id,
    "run_id": _RUN,
    "runtime": "operator",
    "adapter_id": "briefloop-operator-controlstore",
    "adapter_version": "1",
    "briefloop_version": "0.13.0",
    "control_protocol": "controlstore_v2",
    "action_protocol": "core_run_next_action_v2",
    "proposal_protocol": "pydantic_scratch_v2",
    "role_ids": [
        "analyst",
        "auditor",
        "claim-ledger",
        "editor",
        "scout",
        "screener",
        "source-planner",
        "source-provider",
        "writer",
    ],
    "supported_role_topologies": ["default", "strict"],
    "adapter_asset_sha256": {"role_catalog": _SHA_A},
    "max_delegation_depth": 1,
    "max_threads": 4,
    "binding_fingerprint": "0" * 64,
}
_RUNTIME_ADAPTER_BINDING["binding_fingerprint"] = _contract_fingerprint(
    _RUNTIME_ADAPTER_BINDING,
    field="binding_fingerprint",
)
RuntimeAdapterBinding.minimal_example = deepcopy(_RUNTIME_ADAPTER_BINDING)
RuntimeAdapterBinding.full_example = deepcopy(_RUNTIME_ADAPTER_BINDING)

_SOURCE_ROUTE = {
    "schema_version": RuntimeSourceRouteBinding.schema_id,
    "route_id": "manual",
    "route_kind": "manual",
    "provider_id": None,
    "execution_owner": "human",
    "required": False,
    "acquisition_spec": None,
    "route_fingerprint": "0" * 64,
}
_SOURCE_ROUTE["route_fingerprint"] = _contract_fingerprint(
    _SOURCE_ROUTE,
    field="route_fingerprint",
)
RuntimeSourceRouteBinding.minimal_example = deepcopy(_SOURCE_ROUTE)
RuntimeSourceRouteBinding.full_example = deepcopy(_SOURCE_ROUTE)

_WEB_REQUEST_SPEC = {
    "schema_version": RuntimeWebSearchRequestSpec.schema_id,
    "query": "ExampleCo operations",
    "domains": ["example.com"],
    "max_results": 20,
    "recency_days": 7,
}
RuntimeWebSearchRequestSpec.minimal_example = deepcopy(_WEB_REQUEST_SPEC)
RuntimeWebSearchRequestSpec.full_example = deepcopy(_WEB_REQUEST_SPEC)

_WEB_ACQUISITION_SPEC = {
    "schema_version": RuntimeWebSearchAcquisitionSpec.schema_id,
    "kind": "web_search",
    "provider_id": "exa",
    "requests": [deepcopy(_WEB_REQUEST_SPEC)],
    "acquisition_spec_fingerprint": "0" * 64,
}
_WEB_ACQUISITION_SPEC["acquisition_spec_fingerprint"] = _contract_fingerprint(
    _WEB_ACQUISITION_SPEC,
    field="acquisition_spec_fingerprint",
)
RuntimeWebSearchAcquisitionSpec.minimal_example = deepcopy(_WEB_ACQUISITION_SPEC)
RuntimeWebSearchAcquisitionSpec.full_example = deepcopy(_WEB_ACQUISITION_SPEC)

_WEB_BACKFILL_SPEC_V1 = {
    "enabled": True,
    "query": "ExampleCo official filing investor relations",
    "domains": ["example.com"],
    "max_results": 20,
    "recency_days": 30,
    "search_depth": "advanced",
}
RuntimeWebSearchBackfillSpecV1.minimal_example = deepcopy(_WEB_BACKFILL_SPEC_V1)
RuntimeWebSearchBackfillSpecV1.full_example = deepcopy(_WEB_BACKFILL_SPEC_V1)
_WEB_TASK_SPEC_V3 = {
    "schema_version": RuntimeWebSearchTaskSpecV3.schema_id,
    "task_id": "solar-stock-task-01",
    "task_category": "listed_company",
    "entity_id": "EXMPL",
    "query": "ExampleCo catalyst",
    "topic": "news",
    "domains": [],
    "max_results": 20,
    "recency_days": 7,
    "search_depth": "advanced",
    "minimum_extract_successes": 2,
    "backfill": deepcopy(_WEB_BACKFILL_SPEC_V1),
}
RuntimeWebSearchTaskSpecV3.minimal_example = deepcopy(_WEB_TASK_SPEC_V3)
RuntimeWebSearchTaskSpecV3.full_example = deepcopy(_WEB_TASK_SPEC_V3)
_WEB_MULTI_TASKS_V3 = [
    {
        **deepcopy(_WEB_TASK_SPEC_V3),
        "task_id": f"solar-stock-task-{position:02d}",
        "query": f"ExampleCo catalyst {position:02d}",
    }
    for position in range(1, 21)
]
_WEB_ACQUISITION_SPEC_V3 = {
    "schema_version": RuntimeWebSearchAcquisitionSpecV3.schema_id,
    "kind": "web_search_multi",
    "provider_id": "tavily",
    "tasks": _WEB_MULTI_TASKS_V3,
    "max_primary_search_calls": 20,
    "max_backfill_search_calls": 20,
    "max_extract_calls": 40,
    "max_unique_urls": 800,
    "extract_batch_size": 20,
    "acquisition_spec_fingerprint": "0" * 64,
}
_WEB_ACQUISITION_SPEC_V3["acquisition_spec_fingerprint"] = _contract_fingerprint(
    _WEB_ACQUISITION_SPEC_V3,
    field="acquisition_spec_fingerprint",
)
RuntimeWebSearchAcquisitionSpecV3.minimal_example = deepcopy(_WEB_ACQUISITION_SPEC_V3)
RuntimeWebSearchAcquisitionSpecV3.full_example = deepcopy(_WEB_ACQUISITION_SPEC_V3)
_RUNTIME_SOURCE_SEARCH_PLAN_V2 = {
    "schema_version": RuntimeSourceSearchPlanV2.schema_id,
    "search_plan_id": "SOURCE-SEARCH-PLAN-001",
    "run_id": _RUN,
    "plan_revision": 1,
    "report_type": "solar_stock_periodic",
    "acquisition_spec": deepcopy(_WEB_ACQUISITION_SPEC_V3),
    "task_count": 20,
    "acquisition_spec_fingerprint": _WEB_ACQUISITION_SPEC_V3[
        "acquisition_spec_fingerprint"
    ],
    "record_event_id": "EVT-SOURCE-SEARCH-PLAN-001",
    "accepted_transaction_id": "TXN-SOURCE-SEARCH-PLAN-001",
    "created_at": _NOW,
    "plan_fingerprint": "0" * 64,
}
_RUNTIME_SOURCE_SEARCH_PLAN_V2["plan_fingerprint"] = _contract_fingerprint(
    _RUNTIME_SOURCE_SEARCH_PLAN_V2,
    field="plan_fingerprint",
)
RuntimeSourceSearchPlanV2.minimal_example = deepcopy(_RUNTIME_SOURCE_SEARCH_PLAN_V2)
RuntimeSourceSearchPlanV2.full_example = deepcopy(_RUNTIME_SOURCE_SEARCH_PLAN_V2)

_MARKET_DATA_SECURITY_V1 = {
    "ticker": "TOYO",
    "exchange": "NasdaqCM",
    "currency": "USD",
    "as_of": "2026-08-07",
    "data_origin": "yahoo_chart_api",
    "week_open": 10.4,
    "week_high": 10.9,
    "week_low": 10.1,
    "week_close": 10.62,
    "week_volume": 1523400,
    "weekly_change_pct": 2.31,
    "market_cap": 812000000.0,
    "trailing_pe": None,
}
_MARKET_DATA_SNAPSHOT_V1 = {
    "schema_version": MarketDataSnapshotV1.schema_id,
    "market_data_snapshot_id": "MARKET-DATA-SNAPSHOT-001",
    "run_id": _RUN,
    "as_of_date": "2026-08-07",
    "security_count": 1,
    "provider_id": "yahoo_finance_chart",
    "securities": [deepcopy(_MARKET_DATA_SECURITY_V1)],
    "gaps": [
        {
            "ticker": "DQ",
            "failure_class": "transport_unavailable",
        }
    ],
    "record_event_id": "EVT-MARKET-DATA-SNAPSHOT-001",
    "accepted_transaction_id": "TXN-MARKET-DATA-SNAPSHOT-001",
    "recorded_at": _NOW,
    "snapshot_fingerprint": "0" * 64,
}
_MARKET_DATA_SNAPSHOT_V1["snapshot_fingerprint"] = _contract_fingerprint(
    _MARKET_DATA_SNAPSHOT_V1,
    field="snapshot_fingerprint",
)
MarketDataSnapshotV1.minimal_example = deepcopy(_MARKET_DATA_SNAPSHOT_V1)
MarketDataSnapshotV1.full_example = deepcopy(_MARKET_DATA_SNAPSHOT_V1)

_MARKET_DATA_SNAPSHOT_V2 = {
    "schema_version": MarketDataSnapshotV2.schema_id,
    "market_data_snapshot_id": "MARKET-DATA-SNAPSHOT-002",
    "run_id": _RUN,
    "report_window_start": "2026-08-03",
    "report_window_end": "2026-08-07",
    "as_of_date": "2026-08-07",
    "universe_tickers": ["TOYO"],
    "security_count": 1,
    "provider_ids": ["manual_xlsx"],
    "workbook": {
        "source_name": "public-safe-market-data.xlsx",
        "content_sha256": _SHA_A,
        "content_size_bytes": 1024,
        "profile_id": "toyo-weekly-v1",
        "parsed_sheet_names": [
            "PR事件复盘",
            "Sources",
            "TOYO周明细",
            "估值与多空",
            "海外对标",
            "美股对标",
            "走势数据",
        ],
        "contains_macros": False,
        "contains_external_links": False,
    },
    "securities": [
        {
            "ticker": "TOYO",
            "display_name": "Example Solar",
            "universe": "primary",
            "exchange": "NASDAQ",
            "currency": "USD",
            "return_basis": "close",
            "price_series": [
                {
                    "date": "2026-08-03",
                    "close": 5.0,
                    "adjusted_close": None,
                    "volume": 1000,
                    "data_origin": "manual_xlsx",
                    "source_locator": "走势数据!B4",
                    "source_sha256": _SHA_A,
                },
                {
                    "date": "2026-08-07",
                    "close": 5.5,
                    "adjusted_close": None,
                    "volume": 1200,
                    "data_origin": "manual_xlsx",
                    "source_locator": "走势数据!B5",
                    "source_sha256": _SHA_A,
                },
            ],
            "corporate_actions": [],
            "fields": [
                {
                    "field_id": "return_1w_pct",
                    "status": "available",
                    "value_number": 10.0,
                    "value_text": None,
                    "unit": "percent",
                    "as_of": "2026-08-07",
                    "currency": None,
                    "data_origin": "derived",
                    "derivation": "recomputed",
                    "source_locator": "走势数据!B4:B5",
                    "source_sha256": _SHA_A,
                    "reason_code": None,
                }
            ],
        }
    ],
    "benchmark": None,
    "fx_rates": [],
    "events": [],
    "gaps": [],
    "conflicts": [],
    "derivation_version": "solar-market-data-v2",
    "record_event_id": "EVT-MARKET-DATA-SNAPSHOT-002",
    "accepted_transaction_id": "TXN-MARKET-DATA-SNAPSHOT-002",
    "recorded_at": _NOW,
    "snapshot_fingerprint": "0" * 64,
}
_MARKET_DATA_SNAPSHOT_V2["snapshot_fingerprint"] = _contract_fingerprint(
    _MARKET_DATA_SNAPSHOT_V2,
    field="snapshot_fingerprint",
)
MarketDataSnapshotV2.minimal_example = deepcopy(_MARKET_DATA_SNAPSHOT_V2)
MarketDataSnapshotV2.full_example = deepcopy(_MARKET_DATA_SNAPSHOT_V2)

_CACHED_ACQUISITION_SPEC = {
    "schema_version": RuntimeCachedPackageAcquisitionSpec.schema_id,
    "kind": "cached_package",
    "paths": ["input/sources"],
    "formats": ["json", "md", "txt"],
    "acquisition_spec_fingerprint": "0" * 64,
}
_CACHED_ACQUISITION_SPEC["acquisition_spec_fingerprint"] = _contract_fingerprint(
    _CACHED_ACQUISITION_SPEC,
    field="acquisition_spec_fingerprint",
)
RuntimeCachedPackageAcquisitionSpec.minimal_example = deepcopy(_CACHED_ACQUISITION_SPEC)
RuntimeCachedPackageAcquisitionSpec.full_example = deepcopy(_CACHED_ACQUISITION_SPEC)

_NEWSAPI_ACQUISITION_SPEC = {
    "schema_version": RuntimeNewsApiAcquisitionSpec.schema_id,
    "kind": "newsapi",
    "provider_id": "newsapi",
    "query": "ExampleCo operations",
    "terms": ["ExampleCo", "operations"],
    "max_results": 20,
    "start_date": None,
    "end_date": None,
    "sort_by": "publishedAt",
    "language": "en",
    "domains": [],
    "acquisition_spec_fingerprint": "0" * 64,
}
_NEWSAPI_ACQUISITION_SPEC["acquisition_spec_fingerprint"] = _contract_fingerprint(
    _NEWSAPI_ACQUISITION_SPEC,
    field="acquisition_spec_fingerprint",
)
RuntimeNewsApiAcquisitionSpec.minimal_example = deepcopy(_NEWSAPI_ACQUISITION_SPEC)
RuntimeNewsApiAcquisitionSpec.full_example = deepcopy(_NEWSAPI_ACQUISITION_SPEC)

_SOURCE_PLAN = {
    "schema_version": RuntimeSourcePlanBinding.schema_id,
    "run_id": _RUN,
    "sources_config_sha256": _SHA_B,
    "web_search_mode": "manual",
    "search_backend": None,
    "routes": [deepcopy(_SOURCE_ROUTE)],
    "source_plan_fingerprint": "0" * 64,
}
_SOURCE_PLAN["source_plan_fingerprint"] = _contract_fingerprint(
    _SOURCE_PLAN,
    field="source_plan_fingerprint",
)
RuntimeSourcePlanBinding.minimal_example = deepcopy(_SOURCE_PLAN)
RuntimeSourcePlanBinding.full_example = deepcopy(_SOURCE_PLAN)

_NEXT_ACTION = {
    "schema_version": CoreRunNextAction.schema_id,
    "run_id": _RUN,
    "store_revision": 1,
    "action_kind": "delegate",
    "effect_kind": "role_proposal",
    "stage_id": "scout",
    "role_id": "scout",
    "source_route_id": None,
    "source_provider_id": None,
    "source_acquisition_attempt_authorization_id": None,
    "reason_code": "role_proposal_required",
    "input_artifacts": [],
    "request_schema_id": "briefloop.candidate_claims_proposal.v2",
    "adapter_binding_fingerprint": _RUNTIME_ADAPTER_BINDING["binding_fingerprint"],
    "source_plan_fingerprint": _SOURCE_PLAN["source_plan_fingerprint"],
    "action_fingerprint": "0" * 64,
}
_NEXT_ACTION["action_fingerprint"] = _contract_fingerprint(
    _NEXT_ACTION,
    field="action_fingerprint",
)
CoreRunNextAction.minimal_example = deepcopy(_NEXT_ACTION)
CoreRunNextAction.full_example = deepcopy(_NEXT_ACTION)

CoreRunInitializeRequest.minimal_example = {
    "schema_version": CoreRunInitializeRequest.schema_id,
    "request_id": "REQ-CORE-INIT-001",
    "workspace_id": "WS-PUBLIC-DEMO",
    "run_id": _RUN,
    "runtime": "operator",
    "expected_store_revision": 0,
    "run_direction": deepcopy(_RUN_DIRECTION),
    "workspace_config_sha256": _SHA_A,
    "sources_config_sha256": _SHA_B,
    "role_topology": "default",
    "gate_strictness": deepcopy(_GATE_STRICTNESS),
    "input_governance_required": True,
    "runtime_adapter_binding": deepcopy(_RUNTIME_ADAPTER_BINDING),
}
CoreRunInitializeRequest.full_example = deepcopy(
    CoreRunInitializeRequest.minimal_example
)

RunContractBinding.minimal_example = {
    "schema_version": RunContractBinding.schema_id,
    "run_id": _RUN,
    "workspace_id": "WS-PUBLIC-DEMO",
    "runtime": "operator",
    "stage_specs_schema": "multi-agent-brief-stage-specs/v1",
    "stage_specs_artifact": {
        "artifact_id": "run_contract_stage_specs",
        "revision": 1,
    },
    "stage_specs_sha256": _SHA_A,
    "artifact_contracts_schema": "multi-agent-brief-artifact-contracts/v1",
    "artifact_contracts_artifact": {
        "artifact_id": "run_contract_artifact_contracts",
        "revision": 1,
    },
    "artifact_contracts_sha256": _SHA_B,
    "policy_pack_schema": "multi-agent-brief-policy-pack/v1",
    "policy_pack_name": "default",
    "policy_pack_artifact": {
        "artifact_id": "run_contract_policy_pack",
        "revision": 1,
    },
    "policy_pack_sha256": "c" * 64,
    "runtime_adapter_artifact": {
        "artifact_id": "run_contract_runtime_adapter",
        "revision": 1,
    },
    "runtime_adapter_sha256": "f" * 64,
    "runtime_adapter_fingerprint": _RUNTIME_ADAPTER_BINDING["binding_fingerprint"],
    "runtime_source_plan_artifact": {
        "artifact_id": "run_contract_runtime_source_plan",
        "revision": 1,
    },
    "runtime_source_plan_sha256": "9" * 64,
    "runtime_source_plan_fingerprint": _SOURCE_PLAN["source_plan_fingerprint"],
    "run_direction": deepcopy(_RUN_DIRECTION),
    "workspace_config_sha256": _SHA_A,
    "sources_config_sha256": _SHA_B,
    "role_topology": "default",
    "gate_strictness": deepcopy(_GATE_STRICTNESS),
    "input_governance_required": True,
    "contract_fingerprint": "d" * 64,
    "created_at": _NOW,
    "initialization_event_id": "EVT-CORE-INIT-001",
    "accepted_transaction_id": "REQ-CORE-INIT-001",
    "request_fingerprint": "e" * 64,
}
RunContractBinding.full_example = deepcopy(RunContractBinding.minimal_example)

InvocationStartRequest.minimal_example = {
    "schema_version": InvocationStartRequest.schema_id,
    "request_id": "REQ-INVOCATION-001",
    "run_id": _RUN,
    "stage_id": "scout",
    "role_id": "scout",
    "runtime": "operator",
    "expected_store_revision": 2,
}
InvocationStartRequest.full_example = deepcopy(InvocationStartRequest.minimal_example)

InvocationFailureRequest.minimal_example = {
    "schema_version": InvocationFailureRequest.schema_id,
    "request_id": "REQ-INVOCATION-FAILURE-001",
    "run_id": "RUN-001",
    "invocation_id": "INV-001",
    "reason_code": "child_failed",
    "expected_store_revision": 1,
}
InvocationFailureRequest.full_example = deepcopy(
    InvocationFailureRequest.minimal_example
)

OwnedArtifactSubmitRequest.minimal_example = {
    "schema_version": OwnedArtifactSubmitRequest.schema_id,
    "request_id": "REQ-OWNED-001",
    "run_id": _RUN,
    "artifact_id": "audited_brief",
    "invocation_id": "INV-EDITOR-001",
    "producer_tool_id": None,
    "input_path": "scratch/INV-EDITOR-001/audited_brief.md",
    "expected_store_revision": 8,
    "expected_artifact_revision": 0,
    "expected_parent_artifact": {
        "artifact_id": "analyst_draft_snapshot",
        "revision": 1,
    },
}
OwnedArtifactSubmitRequest.full_example = deepcopy(
    OwnedArtifactSubmitRequest.minimal_example
)

OwnedArtifactSubmissionRecord.minimal_example = {
    "schema_version": OwnedArtifactSubmissionRecord.schema_id,
    "submission_id": "SUBMISSION-OWNED-001",
    "run_id": _RUN,
    "artifact_id": "audited_brief",
    "artifact_revision": 1,
    "artifact_sha256": _SHA_A,
    "owner_stage_id": "editor",
    "owner_role_id": "editor",
    "run_contract_fingerprint": "d" * 64,
    "invocation_id": "INV-EDITOR-001",
    "producer_tool_id": None,
    "parent_artifact": {"artifact_id": "analyst_draft_snapshot", "revision": 1},
    "source_proposal_id": None,
    "canonical_workspace_path": "output/intermediate/audited_brief.md",
    "request_fingerprint": "e" * 64,
    "accepted_event_id": "EVT-OWNED-001",
    "accepted_transaction_id": "REQ-OWNED-001",
    "created_at": _NOW,
}
OwnedArtifactSubmissionRecord.full_example = deepcopy(
    OwnedArtifactSubmissionRecord.minimal_example
)

ClaimRecord.minimal_example = {
    "schema_version": ClaimRecord.schema_id,
    "run_id": _RUN,
    "claim_id": "CL-0001",
    "freeze_id": "FREEZE-001",
    "ordinal": 1,
    "claim_drafts_proposal_id": "PROP-DRAFTS-001",
    "draft_id": "DRAFT-001",
    "statement": "ExampleCo opened a public pilot facility.",
    "evidence_text": "The supplied release states that the facility opened.",
    "primary_source_id": "SRC-001",
    "claim_type": "fact",
    "confidence": "medium",
    "requires_audit": True,
    "epistemic_type": "observed",
    "evidence_relation": "direct",
    "applicability_reason": None,
    "limitations": [],
    "metadata": {"source_title": "Public release"},
    "created_at": _NOW,
    "accepted_transaction_id": "REQ-FREEZE-001",
}
ClaimRecord.full_example = deepcopy(ClaimRecord.minimal_example)

ClaimSourceBinding.minimal_example = {
    "schema_version": ClaimSourceBinding.schema_id,
    "run_id": _RUN,
    "claim_id": "CL-0001",
    "source_id": "SRC-001",
    "position": 0,
    "citation_role": "primary",
    "claim_drafts_proposal_id": "PROP-DRAFTS-001",
    "accepted_transaction_id": "REQ-FREEZE-001",
}
ClaimSourceBinding.full_example = deepcopy(ClaimSourceBinding.minimal_example)

ClaimFreezeRecord.minimal_example = {
    "schema_version": ClaimFreezeRecord.schema_id,
    "freeze_id": "FREEZE-001",
    "run_id": _RUN,
    "claim_drafts_proposal_id": "PROP-DRAFTS-001",
    "screened_proposal_id": "PROP-SCREENED-001",
    "candidate_proposal_id": "PROP-CANDIDATES-001",
    "claim_drafts_artifact": {"artifact_id": "claim_drafts", "revision": 1},
    "claim_drafts_sha256": _SHA_A,
    "ledger_artifact": {"artifact_id": "claim_ledger", "revision": 1},
    "ledger_sha256": _SHA_B,
    "normalization_policy": "sorted_sequential_v2",
    "run_contract_fingerprint": "d" * 64,
    "claim_count": 1,
    "warnings": [],
    "warning_count": 0,
    "frozen_at": _NOW,
    "freeze_event_id": "EVT-FREEZE-001",
    "accepted_transaction_id": "REQ-FREEZE-001",
    "request_fingerprint": "e" * 64,
}
ClaimFreezeRecord.full_example = deepcopy(ClaimFreezeRecord.minimal_example)

ClaimFreezeRequest.minimal_example = {
    "schema_version": ClaimFreezeRequest.schema_id,
    "request_id": "REQ-FREEZE-001",
    "run_id": _RUN,
    "claim_drafts_proposal_id": "PROP-DRAFTS-001",
    "expected_claim_drafts_artifact": {"artifact_id": "claim_drafts", "revision": 1},
    "expected_store_revision": 7,
    "expected_ledger_revision": 0,
}
ClaimFreezeRequest.full_example = deepcopy(ClaimFreezeRequest.minimal_example)

StageTransitionRecord.minimal_example = {
    "schema_version": StageTransitionRecord.schema_id,
    "transition_id": "TRANSITION-SCOUT-001",
    "run_id": _RUN,
    "stage_id": "scout",
    "transition_kind": "complete",
    "requested_decision": "continue",
    "prior_status": "ready",
    "prior_revision": 0,
    "result_status": "complete",
    "result_revision": 1,
    "reason": "The accepted Scout output satisfies the stage contract.",
    "run_contract_fingerprint": "d" * 64,
    "actor": "orchestrator",
    "producer_invocation_id": "INV-SCOUT-001",
    "producer_tool_id": None,
    "producer_result_status": None,
    "producer_result_fingerprint": None,
    "producer_implementation": None,
    "producer_version": None,
    "topology": None,
    "satisfaction_source_kind": None,
    "satisfied_by_id": None,
    "created_at": _NOW,
    "transition_event_id": "EVT-TRANSITION-SCOUT-001",
    "accepted_transaction_id": "REQ-STAGE-SCOUT-001",
    "request_fingerprint": "e" * 64,
}
StageTransitionRecord.full_example = deepcopy(StageTransitionRecord.minimal_example)

StageArtifactBinding.minimal_example = {
    "schema_version": StageArtifactBinding.schema_id,
    "run_id": _RUN,
    "transition_id": "TRANSITION-SCOUT-001",
    "position": 0,
    "artifact_id": "candidate_claims",
    "artifact_revision": 1,
    "artifact_sha256": _SHA_A,
    "usage": "produced",
    "accepted_transaction_id": "REQ-STAGE-SCOUT-001",
}
StageArtifactBinding.full_example = deepcopy(StageArtifactBinding.minimal_example)

StageGateBinding.minimal_example = {
    "schema_version": StageGateBinding.schema_id,
    "run_id": _RUN,
    "transition_id": "TRANSITION-AUDITOR-001",
    "gate_id": "material_fact",
    "evaluation_id": "EVAL-MATERIAL-001",
    "accepted_transaction_id": "REQ-STAGE-AUDITOR-001",
}
StageGateBinding.full_example = deepcopy(StageGateBinding.minimal_example)

StageCompleteRequest.minimal_example = {
    "schema_version": StageCompleteRequest.schema_id,
    "request_id": "REQ-STAGE-SCOUT-001",
    "run_id": _RUN,
    "stage_id": "scout",
    "reason": "Scout output accepted.",
    "expected_stage_revision": 0,
    "expected_store_revision": 5,
    "expected_artifact_revisions": [{"artifact_id": "candidate_claims", "revision": 1}],
    "expected_gate_evaluation_ids": [],
}
StageCompleteRequest.full_example = deepcopy(StageCompleteRequest.minimal_example)

GateFindingRecord.minimal_example = {
    "schema_version": GateFindingRecord.schema_id,
    "run_id": _RUN,
    "evaluation_id": "EVAL-MATERIAL-001",
    "finding_id": "FINDING-MATERIAL-001",
    "gate_id": "material_fact",
    "finding_type": "missing_claim_citation",
    "severity": "high",
    "blocking_level": "blocking",
    "repair_owner": "editor",
    "stage_id": "auditor",
    "artifact_id": "audited_brief",
    "claim_id": "CL-0001",
    "source_id": "SRC-001",
    "line_number": 1,
    "description": "A material statement lacks a valid Claim citation.",
    "recommendation": "Bind the statement to a frozen Claim.",
    "category": "material_fact",
    "evidence_ref": "audited_brief:1",
    "metadata": {},
    "accepted_transaction_id": "REQ-GATE-001",
}
GateFindingRecord.full_example = deepcopy(GateFindingRecord.minimal_example)

GateEvaluationRecord.minimal_example = {
    "schema_version": GateEvaluationRecord.schema_id,
    "evaluation_id": "EVAL-MATERIAL-001",
    "gate_batch_id": "GATE-BATCH-001",
    "run_id": _RUN,
    "stage_id": "auditor",
    "gate_id": "material_fact",
    "policy_version": "default-v1",
    "run_contract_fingerprint": "d" * 64,
    "status": "pass",
    "blocking": False,
    "finding_ids": [],
    "checked_at": _NOW,
    "producer_implementation": "quality-gates-preloaded",
    "producer_version": "1",
    "report_artifact": {"artifact_id": "auditor_quality_gate_report", "revision": 1},
    "evaluation_event_id": "EVT-GATE-MATERIAL-001",
    "accepted_transaction_id": "REQ-GATE-001",
    "request_fingerprint": "e" * 64,
}
GateEvaluationRecord.full_example = deepcopy(GateEvaluationRecord.minimal_example)

GateArtifactBinding.minimal_example = {
    "schema_version": GateArtifactBinding.schema_id,
    "run_id": _RUN,
    "evaluation_id": "EVAL-MATERIAL-001",
    "position": 0,
    "artifact_id": "audited_brief",
    "artifact_revision": 1,
    "artifact_sha256": _SHA_A,
    "usage": "brief",
    "accepted_transaction_id": "REQ-GATE-001",
}
GateArtifactBinding.full_example = deepcopy(GateArtifactBinding.minimal_example)

GateCheckRequest.minimal_example = {
    "schema_version": GateCheckRequest.schema_id,
    "request_id": "REQ-GATE-001",
    "run_id": _RUN,
    "stage_id": "auditor",
    "expected_store_revision": 12,
    "expected_report_artifact_revision": 0,
    "expected_input_artifacts": [
        {"artifact_id": "claim_ledger", "revision": 1},
        {"artifact_id": "audited_brief", "revision": 1},
        {"artifact_id": "analyst_draft_snapshot", "revision": 1},
        {"artifact_id": "screened_candidates", "revision": 1},
    ],
}
GateCheckRequest.full_example = deepcopy(GateCheckRequest.minimal_example)

AuditPromotionRequest.minimal_example = {
    "schema_version": AuditPromotionRequest.schema_id,
    "request_id": "REQ-AUDIT-PROMOTE-001",
    "run_id": _RUN,
    "audit_proposal_id": "PROP-AUDIT-001",
    "expected_target_artifact": {"artifact_id": "audited_brief", "revision": 1},
    "expected_audit_report_revision": 0,
    "expected_store_revision": 11,
}
AuditPromotionRequest.full_example = deepcopy(AuditPromotionRequest.minimal_example)

AuditReportArtifact.minimal_example = {
    "schema_version": AuditReportArtifact.schema_id,
    "run_id": _RUN,
    "audit_proposal_id": "PROP-AUDIT-001",
    "target_artifact_id": "audited_brief",
    "target_artifact_revision": 1,
    "target_artifact_sha256": _SHA_A,
    "decision": "pass",
    "findings": [],
}
AuditReportArtifact.full_example = deepcopy(AuditReportArtifact.minimal_example)

RunIntegrityRecord.minimal_example = {
    "schema_version": RunIntegrityRecord.schema_id,
    "run_id": _RUN,
    "integrity_revision": 1,
    "status": "clean",
    "prior_integrity_revision": None,
    "affected_artifact_id": None,
    "affected_artifact_revision": None,
    "expected_workspace_path": None,
    "expected_sha256": None,
    "observed_entry_kind": None,
    "observed_sha256": None,
    "reason_code": None,
    "first_detected_at": None,
    "first_detected_event_id": None,
    "accepted_transaction_id": "REQ-CORE-INIT-001",
    "request_fingerprint": "e" * 64,
}
RunIntegrityRecord.full_example = deepcopy(RunIntegrityRecord.minimal_example)

IntegrityCheckRequest.minimal_example = {
    "schema_version": IntegrityCheckRequest.schema_id,
    "request_id": "REQ-INTEGRITY-001",
    "run_id": _RUN,
    "expected_store_revision": 14,
}
IntegrityCheckRequest.full_example = deepcopy(IntegrityCheckRequest.minimal_example)

_AR1 = {"artifact_id": "audited_brief", "revision": 1}
_AR2 = {"artifact_id": "audit_report", "revision": 1}
_READER = {"artifact_id": "reader_brief", "revision": 1}

RepairCycleRecord.minimal_example = {
    "schema_version": RepairCycleRecord.schema_id,
    "repair_id": "REPAIR-001",
    "run_id": _RUN,
    "contamination_revision": 2,
    "owner_stage_id": "editor",
    "permitted_artifact_ids": ["audited_brief"],
    "reason_code": "artifact_drift",
    "started_at": _NOW,
    "start_event_id": "EVT-REPAIR-001",
    "accepted_transaction_id": "REQ-REPAIR-001",
    "request_fingerprint": _SHA_A,
}
GateRepairCycleRecord.minimal_example = {
    "schema_version": GateRepairCycleRecord.schema_id,
    "gate_repair_id": "GATE-REPAIR-001",
    "run_id": _RUN,
    "authorization_id": "AUTH-RUN-001",
    "repair_ordinal": 1,
    "source_gate_batch_id": "GATE-BATCH-001",
    "source_stage_id": "auditor",
    "blocking_evaluation_ids": ["EVAL-MATERIAL-001"],
    "blocking_findings": [
        {
            "evaluation_id": "EVAL-MATERIAL-001",
            "finding_id": "FINDING-MATERIAL-001",
        }
    ],
    "repair_owner": "editor",
    "target_artifact": _AR1,
    "reopened_transition_ids": ["TRANS-EDITOR-REOPEN-001"],
    "started_at": _NOW,
    "start_event_id": "EVT-GATE-REPAIR-001",
    "accepted_transaction_id": "REQ-GATE-REPAIR-001",
    "request_fingerprint": _SHA_A,
}
GateRepairArtifactBinding.minimal_example = {
    "schema_version": GateRepairArtifactBinding.schema_id,
    "run_id": _RUN,
    "gate_repair_id": "GATE-REPAIR-001",
    "prior_artifact": _AR1,
    "successor_artifact": {"artifact_id": "audited_brief", "revision": 2},
    "owned_artifact_submission_id": "SUBMISSION-AUDITED-BRIEF-002",
    "accepted_event_id": "EVT-GATE-REPAIR-ARTIFACT-001",
    "accepted_transaction_id": "REQ-GATE-REPAIR-ARTIFACT-001",
    "request_fingerprint": _SHA_A,
}
GateRepairOutcomeRecord.minimal_example = {
    "schema_version": GateRepairOutcomeRecord.schema_id,
    "outcome_id": "GATE-REPAIR-OUTCOME-001",
    "run_id": _RUN,
    "gate_repair_id": "GATE-REPAIR-001",
    "replacement_gate_batch_id": "GATE-BATCH-002",
    "replacement_stage_id": "auditor",
    "evaluation_ids": ["EVAL-MATERIAL-002"],
    "disposition": "passed",
    "completed_at": _NOW,
    "completion_event_id": "EVT-GATE-REPAIR-OUTCOME-001",
    "accepted_transaction_id": "REQ-GATE-REPAIR-OUTCOME-001",
    "request_fingerprint": _SHA_A,
}
ArtifactSupersessionRecord.minimal_example = {
    "schema_version": ArtifactSupersessionRecord.schema_id,
    "supersession_id": "SUPERSEDE-001",
    "run_id": _RUN,
    "repair_id": "REPAIR-001",
    "mode": "repair",
    "prior_artifact": _AR1,
    "successor_artifact": {"artifact_id": "audited_brief", "revision": 2},
    "reason_code": "repair_replacement",
    "created_at": _NOW,
    "accepted_event_id": "EVT-SUPERSEDE-001",
    "accepted_transaction_id": "REQ-SUPERSEDE-001",
    "request_fingerprint": _SHA_A,
}
RepairCompletionRecord.minimal_example = {
    "schema_version": RepairCompletionRecord.schema_id,
    "repair_completion_id": "REPAIR-DONE-001",
    "run_id": _RUN,
    "repair_id": "REPAIR-001",
    "contamination_revision": 2,
    "supersession_ids": ["SUPERSEDE-001"],
    "reopened_transition_ids": ["TRANS-REOPEN-001"],
    "completed_at": _NOW,
    "completion_event_id": "EVT-REPAIR-DONE-001",
    "accepted_transaction_id": "REQ-REPAIR-DONE-001",
    "request_fingerprint": _SHA_A,
}
RecoveryCompletionRecord.minimal_example = {
    "schema_version": RecoveryCompletionRecord.schema_id,
    "recovery_id": "RECOVERY-001",
    "run_id": _RUN,
    "repair_completion_id": "REPAIR-DONE-001",
    "contamination_revision": 2,
    "supersession_ids": ["SUPERSEDE-001"],
    "rerun_transition_ids": ["TRANS-RERUN-001"],
    "gate_evaluation_ids": [],
    "disposition": "recovered_non_reference",
    "completed_at": _NOW,
    "completion_event_id": "EVT-RECOVERY-001",
    "accepted_transaction_id": "REQ-RECOVERY-001",
    "request_fingerprint": _SHA_A,
}
RunHeadTransitionRecord.minimal_example = {
    "schema_version": RunHeadTransitionRecord.schema_id,
    "head_transition_id": "HEAD-TRANS-001",
    "workspace_id": "WS-001",
    "predecessor_run_id": _RUN,
    "successor_run_id": "RUN-20260714-002",
    "prior_workspace_revision": 14,
    "successor_workspace_revision": 15,
    "reason_code": "run_reset",
    "successor_disposition": "non_reference",
    "created_at": _NOW,
    "transition_event_id": "EVT-RESET-001",
    "accepted_transaction_id": "REQ-RESET-001",
    "request_fingerprint": _SHA_A,
}
_GUIDANCE_REUSE_SCOPE_EXAMPLE = {
    "schema_version": GuidanceReuseScopeV1.schema_id,
    "audience": "Executive team",
    "audience_profile": "Decision makers",
    "output_language": "English",
    "output_style": "concise",
    "output_formats": ["markdown"],
    "cadence": "weekly",
}
_GUIDANCE_REUSE_SCOPE_EXAMPLE["scope_fingerprint"] = _contract_fingerprint(
    _GUIDANCE_REUSE_SCOPE_EXAMPLE,
    field="scope_fingerprint",
)
GuidanceReuseScopeV1.minimal_example = deepcopy(_GUIDANCE_REUSE_SCOPE_EXAMPLE)

_GUIDANCE_DECISION_EXAMPLE = {
    "schema_version": RunGuidanceSelectionDecisionRecord.schema_id,
    "decision_id": "GUIDANCE-DECISION-001",
    "run_id": "RUN-20260714-002",
    "snapshot_id": "GUIDANCE-SNAPSHOT-001",
    "source_run_id": _RUN,
    "guidance_id": "GUIDANCE-001",
    "draft_revision": 1,
    "status_revision_id": "GUIDANCE-STATUS-001",
    "provenance_kind": "accepted_model_finding",
    "assessment_result_id": "PFLAJ-RESULT-001",
    "finding_id": "FINDING-001",
    "disposition_id": "DISPOSITION-001",
    "result_fingerprint": _SHA_A,
    "finding_fingerprint": _SHA_B,
    "disposition_fingerprint": _SHA_C,
    "observation_id": None,
    "observation_fingerprint": None,
    "draft_fingerprint": _SHA_D,
    "status_fingerprint": _SHA_A,
    "source_scope_fingerprint": _GUIDANCE_REUSE_SCOPE_EXAMPLE["scope_fingerprint"],
    "successor_scope_fingerprint": _GUIDANCE_REUSE_SCOPE_EXAMPLE["scope_fingerprint"],
    "selected": True,
    "reason_code": "approved_scope_match",
}
_GUIDANCE_DECISION_EXAMPLE["decision_fingerprint"] = _contract_fingerprint(
    _GUIDANCE_DECISION_EXAMPLE,
    field="decision_fingerprint",
)
RunGuidanceSelectionDecisionRecord.minimal_example = deepcopy(
    _GUIDANCE_DECISION_EXAMPLE
)

_GUIDANCE_ITEM_EXAMPLE = {
    "schema_version": RunGuidanceSnapshotItemRecord.schema_id,
    "item_id": "GUIDANCE-ITEM-001",
    "run_id": "RUN-20260714-002",
    "snapshot_id": "GUIDANCE-SNAPSHOT-001",
    "position": 0,
    "source_run_id": _RUN,
    "finalized_lineage_fingerprint": _SHA_A,
    "provenance_kind": "accepted_model_finding",
    "assessment_result_id": "PFLAJ-RESULT-001",
    "assessment_result_fingerprint": _SHA_B,
    "finding_id": "FINDING-001",
    "finding_fingerprint": _SHA_C,
    "disposition_id": "DISPOSITION-001",
    "disposition_fingerprint": _SHA_D,
    "observation_id": None,
    "observation_fingerprint": None,
    "guidance_id": "GUIDANCE-001",
    "draft_revision": 1,
    "draft_fingerprint": _SHA_A,
    "status_revision_id": "GUIDANCE-STATUS-001",
    "status_fingerprint": _SHA_B,
    "guidance_text": "Prefer a short executive summary before the detail.",
    "guidance_sha256": hashlib.sha256(
        b"Prefer a short executive summary before the detail."
    ).hexdigest(),
    "reuse_scope": deepcopy(_GUIDANCE_REUSE_SCOPE_EXAMPLE),
}
_GUIDANCE_ITEM_EXAMPLE["item_fingerprint"] = _contract_fingerprint(
    _GUIDANCE_ITEM_EXAMPLE,
    field="item_fingerprint",
)
RunGuidanceSnapshotItemRecord.minimal_example = deepcopy(_GUIDANCE_ITEM_EXAMPLE)

_GUIDANCE_SNAPSHOT_EXAMPLE = {
    "schema_version": RunGuidanceSnapshotRecord.schema_id,
    "snapshot_id": "GUIDANCE-SNAPSHOT-001",
    "workspace_id": "WS-001",
    "run_id": "RUN-20260714-002",
    "predecessor_run_id": _RUN,
    "reuse_requested": True,
    "successor_direction_fingerprint": _SHA_A,
    "successor_run_contract_fingerprint": _SHA_B,
    "candidate_set_fingerprint": _SHA_C,
    "selected_item_ids": ["GUIDANCE-ITEM-001"],
    "decision_ids": ["GUIDANCE-DECISION-001"],
    "selected_count": 1,
    "omitted_count": 0,
    "snapshot_event_id": "EVT-GUIDANCE-SNAPSHOT-001",
    "accepted_transaction_id": "REQ-SUCCESSOR-001",
    "request_fingerprint": _SHA_D,
}
_GUIDANCE_SNAPSHOT_EXAMPLE["snapshot_fingerprint"] = _contract_fingerprint(
    _GUIDANCE_SNAPSHOT_EXAMPLE,
    field="snapshot_fingerprint",
)
RunGuidanceSnapshotRecord.minimal_example = deepcopy(_GUIDANCE_SNAPSHOT_EXAMPLE)

for _model in (
    GuidanceReuseScopeV1,
    RunGuidanceSelectionDecisionRecord,
    RunGuidanceSnapshotItemRecord,
    RunGuidanceSnapshotRecord,
):
    _model.full_example = deepcopy(_model.minimal_example)
FinalizeRenderRecord.minimal_example = {
    "schema_version": FinalizeRenderRecord.schema_id,
    "render_id": "RENDER-001",
    "run_id": _RUN,
    "audit_proposal_id": "PROP-AUDIT-001",
    "audited_brief": _AR1,
    "audit_report": _AR2,
    "reader_artifacts": [_READER],
    "reader_clean_status": "pass",
    "policy_result_fingerprint": _SHA_A,
    "run_contract_fingerprint": _SHA_B,
    "created_at": _NOW,
    "render_event_id": "EVT-RENDER-001",
    "accepted_transaction_id": "REQ-RENDER-001",
    "request_fingerprint": _SHA_A,
}
FinalizationRecord.minimal_example = {
    "schema_version": FinalizationRecord.schema_id,
    "finalization_id": "FINAL-001",
    "run_id": _RUN,
    "render_id": "RENDER-001",
    "finalize_transition_id": "TRANS-FINAL-001",
    "finalize_gate_batch_id": "GATE-BATCH-FINAL-001",
    "finalize_gate_evaluation_ids": ["GATE-FINAL-001"],
    "recovery_id": None,
    "integrity_revision": 1,
    "finalized_at": _NOW,
    "finalization_event_id": "EVT-FINAL-001",
    "accepted_transaction_id": "REQ-FINAL-001",
    "request_fingerprint": _SHA_A,
}
RunArchiveRecord.minimal_example = {
    "schema_version": RunArchiveRecord.schema_id,
    "archive_id": "ARCHIVE-001",
    "run_id": _RUN,
    "finalization_id": "FINAL-001",
    "archive_artifact": {"artifact_id": "run_archive", "revision": 1},
    "manifest_sha256": _SHA_A,
    "included_count": 1,
    "created_at": _NOW,
    "archive_event_id": "EVT-ARCHIVE-001",
    "accepted_transaction_id": "REQ-FINAL-001",
    "request_fingerprint": _SHA_A,
}
RunArchiveArtifactBinding.minimal_example = {
    "schema_version": RunArchiveArtifactBinding.schema_id,
    "run_id": _RUN,
    "archive_id": "ARCHIVE-001",
    "position": 0,
    "artifact_id": "audited_brief",
    "artifact_revision": 1,
    "artifact_sha256": _SHA_A,
    "usage": "workflow",
    "accepted_transaction_id": "REQ-FINAL-001",
}
PackageReadyRecord.minimal_example = {
    "schema_version": PackageReadyRecord.schema_id,
    "package_id": "PACKAGE-001",
    "run_id": _RUN,
    "finalization_id": "FINAL-001",
    "archive_id": "ARCHIVE-001",
    "package_manifest_artifact": {"artifact_id": "package_manifest", "revision": 1},
    "package_manifest_sha256": _SHA_A,
    "artifact_count": 2,
    "created_at": _NOW,
    "package_event_id": "EVT-PACKAGE-001",
    "accepted_transaction_id": "REQ-FINAL-001",
    "request_fingerprint": _SHA_A,
}
PackageArtifactBinding.minimal_example = {
    "schema_version": PackageArtifactBinding.schema_id,
    "run_id": _RUN,
    "package_id": "PACKAGE-001",
    "position": 0,
    "artifact_id": "reader_brief",
    "artifact_revision": 1,
    "artifact_sha256": _SHA_A,
    "usage": "reader",
    "accepted_transaction_id": "REQ-FINAL-001",
}
ApprovalPackageBinding.minimal_example = {
    "schema_version": ApprovalPackageBinding.schema_id,
    "run_id": _RUN,
    "approval_id": "APPROVAL-001",
    "package_id": "PACKAGE-001",
    "accepted_transaction_id": "REQ-APPROVAL-001",
}
DeliveryAuthorizationRecord.minimal_example = {
    "schema_version": DeliveryAuthorizationRecord.schema_id,
    "authorization_id": "AUTH-001",
    "run_id": _RUN,
    "package_id": "PACKAGE-001",
    "prior_authorization_id": None,
    "approval_mode": "internal_draft",
    "retry_of_attempt_id": None,
    "purpose": "initial_attempt",
    "decision": "authorize",
    "target": "local",
    "channel": "filesystem",
    "recipient_fingerprint": _SHA_A,
    "actor_id": "HUMAN-001",
    "reason": "Approved local package preparation",
    "recorded_at": _NOW,
    "authorization_event_id": "EVT-AUTH-001",
    "accepted_transaction_id": "REQ-AUTH-001",
    "request_fingerprint": _SHA_A,
}
DeliveryAttemptRecord.minimal_example = {
    "schema_version": DeliveryAttemptRecord.schema_id,
    "attempt_id": "ATTEMPT-001",
    "run_id": _RUN,
    "package_id": "PACKAGE-001",
    "authorization_id": "AUTH-001",
    "target": "local",
    "channel": "filesystem",
    "recipient_fingerprint": _SHA_A,
    "connector_operation_id": "OP-001",
    "connector_request_fingerprint": _SHA_B,
    "created_at": _NOW,
    "attempt_event_id": "EVT-ATTEMPT-001",
    "accepted_transaction_id": "REQ-ATTEMPT-001",
    "request_fingerprint": _SHA_A,
}
DeliveryResultRecord.minimal_example = {
    "schema_version": DeliveryResultRecord.schema_id,
    "result_id": "RESULT-001",
    "run_id": _RUN,
    "attempt_id": "ATTEMPT-001",
    "prior_result_id": None,
    "reconciliation_authorization_id": None,
    "status": "bundle_prepared",
    "adapter_id": "local-adapter",
    "adapter_version": "V1",
    "connector_operation_id": "OP-001",
    "evidence_sha256": _SHA_A,
    "evidence_artifact": None,
    "recorded_at": _NOW,
    "result_event_id": "EVT-RESULT-001",
    "accepted_transaction_id": "REQ-RESULT-001",
    "request_fingerprint": _SHA_A,
}
DeliveryResultObservation.minimal_example = {
    "schema_version": DeliveryResultObservation.schema_id,
    "attempt_id": "ATTEMPT-001",
    "adapter_id": "local-adapter",
    "adapter_version": "V1",
    "connector_operation_id": "OP-001",
    "status": "bundle_prepared",
    "evidence_sha256": _SHA_A,
    "diagnostic_code": "bundle_prepared",
    "connector_request_fingerprint": _SHA_B,
}
DeliveryResultObservation.full_example = deepcopy(
    DeliveryResultObservation.minimal_example
)

RepairStartRequest.minimal_example = {
    "schema_version": RepairStartRequest.schema_id,
    "request_id": "REQ-REPAIR-001",
    "run_id": _RUN,
    "contamination_revision": 2,
    "owner_stage_id": "editor",
    "permitted_artifact_ids": ["audited_brief"],
    "reason_code": "artifact_drift",
    "expected_store_revision": 14,
}
ArtifactSupersedeRequest.minimal_example = {
    "schema_version": ArtifactSupersedeRequest.schema_id,
    "request_id": "REQ-SUPERSEDE-001",
    "run_id": _RUN,
    "repair_id": "REPAIR-001",
    "prior_artifact": _AR1,
    "input_path": "scratch/INV-REPAIR-001/audited_brief.md",
    "expected_input_sha256": _SHA_A,
    "expected_current_revision": 1,
    "mode": "repair",
    "reason_code": "repair_replacement",
    "expected_store_revision": 14,
}
ArtifactRevertRequest.minimal_example = {
    "schema_version": ArtifactRevertRequest.schema_id,
    "request_id": "REQ-REVERT-001",
    "run_id": _RUN,
    "repair_id": "REPAIR-001",
    "current_artifact": {"artifact_id": "audited_brief", "revision": 2},
    "historical_source": _AR1,
    "expected_current_revision": 2,
    "mode": "revert",
    "reason_code": "explicit_revert",
    "expected_store_revision": 15,
}
RepairCompleteRequest.minimal_example = {
    "schema_version": RepairCompleteRequest.schema_id,
    "request_id": "REQ-REPAIR-DONE-001",
    "run_id": _RUN,
    "repair_id": "REPAIR-001",
    "supersession_ids": ["SUPERSEDE-001"],
    "expected_stage_revisions": {"editor": 2},
    "expected_store_revision": 16,
}
RecoveryCompleteRequest.minimal_example = {
    "schema_version": RecoveryCompleteRequest.schema_id,
    "request_id": "REQ-RECOVERY-001",
    "run_id": _RUN,
    "repair_completion_id": "REPAIR-DONE-001",
    "contamination_revision": 2,
    "rerun_transition_ids": ["TRANS-RERUN-001"],
    "gate_evaluation_ids": [],
    "expected_store_revision": 18,
}
RunResetRequest.minimal_example = {
    "schema_version": RunResetRequest.schema_id,
    "request_id": "REQ-RESET-001",
    "predecessor_run_id": _RUN,
    "successor_run_id": "RUN-20260714-002",
    "workspace_id": "WS-001",
    "runtime": "operator",
    "expected_head_run_id": _RUN,
    "expected_store_revision": 14,
    "expected_workspace_revision": 1,
    "run_direction": deepcopy(
        CoreRunInitializeRequest.minimal_example["run_direction"]
    ),
    "workspace_config_sha256": _SHA_A,
    "sources_config_sha256": _SHA_B,
    "role_topology": "default",
    "gate_strictness": {key: True for key in GATE_ID_VALUES},
    "input_governance_required": False,
}
_SUCCESSOR_REQUEST_EXAMPLE = {
    "schema_version": RunSuccessorStartRequest.schema_id,
    "request_id": "REQ-SUCCESSOR-001",
    "predecessor_run_id": _RUN,
    "successor_run_id": "RUN-20260714-002",
    "workspace_id": "WS-001",
    "runtime": "operator",
    "expected_head_run_id": _RUN,
    "expected_store_revision": 14,
    "expected_workspace_revision": 14,
    "run_direction": deepcopy(
        CoreRunInitializeRequest.minimal_example["run_direction"]
    ),
    "workspace_config_sha256": _SHA_A,
    "sources_config_sha256": _SHA_B,
    "role_topology": "default",
    "gate_strictness": {key: True for key in GATE_ID_VALUES},
    "input_governance_required": False,
    "include_approved_guidance": True,
}
_SUCCESSOR_REQUEST_EXAMPLE["run_direction"] = RunDirection.model_validate(
    _SUCCESSOR_REQUEST_EXAMPLE["run_direction"],
    strict=True,
).model_dump(mode="json")
_SUCCESSOR_REQUEST_EXAMPLE["request_fingerprint"] = _contract_fingerprint(
    _SUCCESSOR_REQUEST_EXAMPLE,
    field="request_fingerprint",
)
RunSuccessorStartRequest.minimal_example = _SUCCESSOR_REQUEST_EXAMPLE
FinalizeRenderRequest.minimal_example = {
    "schema_version": FinalizeRenderRequest.schema_id,
    "request_id": "REQ-RENDER-001",
    "run_id": _RUN,
    "audit_proposal_id": "PROP-AUDIT-001",
    "expected_audited_brief": _AR1,
    "expected_audit_report": _AR2,
    "reader_scratch_inputs": {"reader_brief": "scratch/INV-FINAL-001/brief.md"},
    "expected_reader_sha256": {"reader_brief": _SHA_A},
    "expected_reader_revisions": {"reader_brief": 0},
    "expected_store_revision": 20,
}
FinalizeCompleteRequest.minimal_example = {
    "schema_version": FinalizeCompleteRequest.schema_id,
    "request_id": "REQ-FINAL-001",
    "run_id": _RUN,
    "render_id": "RENDER-001",
    "expected_finalize_stage_revision": 0,
    "gate_evaluation_ids": ["GATE-FINAL-001"],
    "recovery_id": None,
    "expected_store_revision": 22,
}
InternalApprovalRequest.minimal_example = {
    "schema_version": InternalApprovalRequest.schema_id,
    "request_id": "REQ-APPROVAL-001",
    "run_id": _RUN,
    "package_id": "PACKAGE-001",
    "approval_id": "APPROVAL-001",
    "mode": "internal_management_review",
    "role": "content_owner",
    "decision": "approve",
    "reason": "Approved for internal management review",
    "actor_id": "HUMAN-001",
    "expected_store_revision": 23,
}
DeliveryAuthorizationRequest.minimal_example = {
    "schema_version": DeliveryAuthorizationRequest.schema_id,
    "request_id": "REQ-AUTH-001",
    "run_id": _RUN,
    "package_id": "PACKAGE-001",
    "prior_authorization_id": None,
    "approval_mode": "internal_draft",
    "retry_of_attempt_id": None,
    "purpose": "initial_attempt",
    "decision": "authorize",
    "target": "local",
    "channel": "filesystem",
    "recipient_fingerprint": _SHA_A,
    "actor_id": "HUMAN-001",
    "reason": "Approved local package preparation",
    "expected_store_revision": 24,
}
RunTerminationRequest.minimal_example = {
    "schema_version": RunTerminationRequest.schema_id,
    "request_id": "REQ-TERMINATE-001",
    "run_id": _RUN,
    "decision": "terminate",
    "reason_code": "gate_repair_unresolvable",
    "reason": "The frozen repair budget is exhausted; preserve the failed run.",
    "actor_id": "local-human-reviewer",
    "expected_action_fingerprint": "0" * 64,
    "expected_store_revision": 24,
}
DeliveryAttemptRequest.minimal_example = {
    "schema_version": DeliveryAttemptRequest.schema_id,
    "request_id": "REQ-ATTEMPT-001",
    "run_id": _RUN,
    "package_id": "PACKAGE-001",
    "authorization_id": "AUTH-001",
    "connector_operation_id": "OP-001",
    "connector_request_fingerprint": _SHA_B,
    "expected_store_revision": 25,
}
DeliveryResultRequest.minimal_example = {
    "schema_version": DeliveryResultRequest.schema_id,
    "request_id": "REQ-RESULT-001",
    "run_id": _RUN,
    "attempt_id": "ATTEMPT-001",
    "prior_result_id": None,
    "observation_input_path": None,
    "expected_observation_sha256": None,
    "reconciliation_authorization_id": None,
    "expected_store_revision": 26,
}

for _model in (
    RepairCycleRecord,
    GateRepairCycleRecord,
    GateRepairArtifactBinding,
    GateRepairOutcomeRecord,
    ArtifactSupersessionRecord,
    RepairCompletionRecord,
    RecoveryCompletionRecord,
    RunHeadTransitionRecord,
    FinalizeRenderRecord,
    FinalizationRecord,
    RunArchiveRecord,
    RunArchiveArtifactBinding,
    PackageReadyRecord,
    PackageArtifactBinding,
    ApprovalPackageBinding,
    DeliveryAuthorizationRecord,
    DeliveryAttemptRecord,
    DeliveryResultRecord,
    RepairStartRequest,
    ArtifactSupersedeRequest,
    ArtifactRevertRequest,
    RepairCompleteRequest,
    RecoveryCompleteRequest,
    RunResetRequest,
    RunSuccessorStartRequest,
    FinalizeRenderRequest,
    FinalizeCompleteRequest,
    InternalApprovalRequest,
    DeliveryAuthorizationRequest,
    RunTerminationRequest,
    DeliveryAttemptRequest,
    DeliveryResultRequest,
):
    _model.full_example = deepcopy(_model.minimal_example)

_CHECKOUT_REVISION_EXAMPLE = "crv_" + "a" * 64
_PUBLICATION_IDENTITY_EXAMPLE = {
    "schema_version": "briefloop-publication-identity/v1",
    "workspace_id": "WS-001",
    "run_id": _RUN,
    "transaction_id": "TXN-001",
    "checkout_revision_id": _CHECKOUT_REVISION_EXAMPLE,
}
CheckoutRevisionRecord.minimal_example = {
    "schema_version": CheckoutRevisionRecord.schema_id,
    "checkout_revision_id": _CHECKOUT_REVISION_EXAMPLE,
    "workspace_id": "WS-001",
    "run_id": _RUN,
    "parent_checkout_revision_id": None,
    "manifest_sha256": _SHA_B,
    "tree_sha256": "a" * 64,
    "member_count": 1,
    "created_at": _NOW,
    "creator_transaction_id": "TXN-001",
}
CheckoutRevisionMember.minimal_example = {
    "schema_version": CheckoutRevisionMember.schema_id,
    "checkout_revision_id": _CHECKOUT_REVISION_EXAMPLE,
    "ordinal": 0,
    "workspace_id": "WS-001",
    "run_id": _RUN,
    "canonical_path": "output/brief.md",
    "artifact_id": "reader_brief",
    "artifact_revision": 1,
    "blob_sha256": _SHA_A,
    "byte_size": 4,
}
ReceiptCheckoutBinding.minimal_example = {
    "schema_version": ReceiptCheckoutBinding.schema_id,
    "workspace_id": "WS-001",
    "run_id": _RUN,
    "transaction_id": "TXN-001",
    "pre_run_id": _RUN,
    "pre_checkout_revision_id": None,
    "post_run_id": _RUN,
    "post_checkout_revision_id": _CHECKOUT_REVISION_EXAMPLE,
}
PublicationIdentityV1.minimal_example = deepcopy(_PUBLICATION_IDENTITY_EXAMPLE)
CheckoutPublicationIntent.minimal_example = {
    "schema_version": CheckoutPublicationIntent.schema_id,
    "identity": deepcopy(_PUBLICATION_IDENTITY_EXAMPLE),
    "publication_identity_sha256": "d" * 64,
    "pre_checkout_revision_id": None,
    "post_checkout_revision_id": _CHECKOUT_REVISION_EXAMPLE,
    "post_manifest_sha256": _SHA_B,
    "post_tree_sha256": "a" * 64,
    "changed_member_count": 1,
    "capability_profile_sha256": "e" * 64,
}
CheckoutPublicationMember.minimal_example = {
    "schema_version": CheckoutPublicationMember.schema_id,
    "identity": deepcopy(_PUBLICATION_IDENTITY_EXAMPLE),
    "ordinal": 0,
    "canonical_path": "output/brief.md",
    "temporary_basename": ".briefloop-pub-v1-" + "d" * 64 + "-00000000-tmp",
    "claim_basename": ".briefloop-pub-v1-" + "d" * 64 + "-00000000-claim",
    "pre_kind": "absent",
    "pre_sha256": None,
    "pre_size": None,
    "post_kind": "blob",
    "post_sha256": _SHA_A,
    "post_size": 4,
}
CheckoutPublicationAck.minimal_example = {
    "schema_version": CheckoutPublicationAck.schema_id,
    "identity": deepcopy(_PUBLICATION_IDENTITY_EXAMPLE),
    "ordinal": 0,
    "publication_identity_sha256": "d" * 64,
    "capability_profile_sha256": "e" * 64,
    "post_kind": "blob",
    "post_sha256": _SHA_A,
    "post_size": 4,
    "verification": "post_verified_durable",
    "cleanup_policy": "retain_residue_v1",
    "appended_at": _NOW,
}
CheckoutPublicationCleanupObservation.minimal_example = {
    "schema_version": CheckoutPublicationCleanupObservation.schema_id,
    "cleanup_observation_id": "f" * 64,
    "identity": deepcopy(_PUBLICATION_IDENTITY_EXAMPLE),
    "ordinal": 0,
    "auxiliary_role": "temp",
    "reason_code": "checkout_projection_cleanup_retained",
    "expected_kind": "blob",
    "expected_sha256": _SHA_A,
    "expected_size": 4,
    "observed_kind": "blob",
    "observed_sha256": _SHA_A,
    "observed_size": 4,
    "appended_at": _NOW,
}
for _model in (
    CheckoutRevisionRecord,
    CheckoutRevisionMember,
    ReceiptCheckoutBinding,
    PublicationIdentityV1,
    CheckoutPublicationIntent,
    CheckoutPublicationMember,
    CheckoutPublicationAck,
    CheckoutPublicationCleanupObservation,
):
    _model.full_example = deepcopy(_model.minimal_example)


_PFLAJ_INSTRUMENT = {
    "schema_version": "briefloop.semantic_evaluator.instrument_config.v1",
    "instrument_config_id": "PFLAJ-INSTRUMENT-001",
    "provider_id": "anthropic_messages",
    "model_id": "messages-model-001",
    "model_version": "model-version-001",
    "language": "zh-CN",
    "decoding": {
        "temperature": 1.0,
        "top_p": 1.0,
        "max_output_tokens": 1024,
        "seed": None,
    },
    "retry_policy": {
        "max_attempts": 1,
        "retryable_reason_codes": [],
        "backoff_schedule_ms": [],
    },
    "prompt_sizer": {
        "sizer_id": "anthropic_utf8_byte_v1",
        "sizer_version": "v1",
        "max_context_tokens": 8192,
        "reserved_output_tokens": 1024,
    },
    "transport_policy": {
        "provider_transport_only": True,
        "model_tools": False,
        "browser": False,
        "cross_run_memory": False,
        "provider_file_search": False,
    },
}
_PFLAJ_CONTEXT = {
    "schema_version": "briefloop.semantic_evaluator.bounded_context.v1",
    "context_id": "PFLAJ-CONTEXT-001",
    "context_sha256": _SHA_A,
    "language": "zh-CN",
    "data_class": "public",
    "requirements": [],
}
_PFLAJ_CONTEXT["context_sha256"] = _canonical_json_sha256(
    {key: value for key, value in _PFLAJ_CONTEXT.items() if key != "context_sha256"}
)
_PFLAJ_ENDPOINT = "https://messages.example.com"
_PFLAJ_POLICY = {
    "schema_version": PostFinalAssessmentPolicyRevision.schema_id,
    "policy_revision_id": "PFLAJ-POLICY-001",
    "run_id": _RUN,
    "previous_policy_revision_id": None,
    "enabled": True,
    "auto_run": False,
    "auto_open": False,
    "adapter_id": "anthropic_messages_v1",
    "messages_endpoint": _PFLAJ_ENDPOINT,
    "messages_endpoint_sha256": hashlib.sha256(
        _PFLAJ_ENDPOINT.encode("utf-8")
    ).hexdigest(),
    "requested_model_id": "messages-model-001",
    "model_version": "model-version-001",
    "expected_model_identity": "model-version-001",
    "profile_id": "research_design_report_zh_v1",
    "instrument_config": deepcopy(_PFLAJ_INSTRUMENT),
    "instrument_config_sha256": _canonical_json_sha256(_PFLAJ_INSTRUMENT),
    "bounded_context": deepcopy(_PFLAJ_CONTEXT),
    "bounded_context_sha256": _PFLAJ_CONTEXT["context_sha256"],
    "temperature": 1.0,
    "top_p": 1.0,
    "max_provider_calls": 9,
    "max_total_input_tokens": 100000,
    "max_total_output_tokens": 9216,
    "max_output_tokens_per_call": 1024,
    "wall_timeout_seconds": 60,
    "public_safe_egress_attested": True,
    "egress_scope": "public_safe_report",
    "human_actor_id": "HUMAN-001",
    "human_request_id": "PFLAJ-POLICY-REQUEST-001",
    "recorded_at": _NOW,
    "policy_event_id": "EVENT-PFLAJ-POLICY-001",
    "accepted_transaction_id": "TX-PFLAJ-POLICY-001",
    "policy_fingerprint": _SHA_A,
}
_PFLAJ_POLICY["policy_fingerprint"] = _contract_fingerprint(
    _PFLAJ_POLICY, field="policy_fingerprint"
)
PostFinalAssessmentPolicyRevision.minimal_example = deepcopy(_PFLAJ_POLICY)
PostFinalAssessmentPolicyRevision.full_example = deepcopy(_PFLAJ_POLICY)

_PFLAJ_REQUEST = {
    "schema_version": PostFinalAssessmentRequestRecord.schema_id,
    "assessment_request_id": "PFLAJ-REQUEST-001",
    "run_id": _RUN,
    "finalized_facts_fingerprint": _SHA_A,
    "finalized_lineage_fingerprint": _SHA_B,
    "report_artifact_id": "reader_brief",
    "report_revision": 1,
    "report_sha256": _SHA_B,
    "finalization_id": "FINALIZATION-001",
    "finalization_receipt_id": "TX-FINALIZATION-001",
    "finalize_gate_batch_id": "GATE-BATCH-001",
    "policy_revision_id": _PFLAJ_POLICY["policy_revision_id"],
    "policy_fingerprint": _PFLAJ_POLICY["policy_fingerprint"],
    "adapter_id": "anthropic_messages_v1",
    "messages_endpoint_sha256": _PFLAJ_POLICY["messages_endpoint_sha256"],
    "requested_model_id": "messages-model-001",
    "expected_model_identity": "model-version-001",
    "profile_id": "research_design_report_zh_v1",
    "instrument_config_sha256": _PFLAJ_POLICY["instrument_config_sha256"],
    "bounded_context_sha256": _PFLAJ_POLICY["bounded_context_sha256"],
    "input_binding_sha256": "c" * 64,
    "assessment_plan_sha256": "d" * 64,
    "ordered_prompt_request_sha256s": [f"{item:064x}" for item in range(1, 10)],
    "prompt_count": 9,
    "provider_call_ceiling": 9,
    "total_input_token_upper_bound": 10000,
    "total_output_token_upper_bound": 9216,
    "output_tokens_per_call": 1024,
    "trial_id": "PFLAJ-TRIAL-001",
    "archive_identity_sha256": "e" * 64,
    "request_status": "claimed",
    "claimed_at": _NOW,
    "request_event_id": "EVENT-PFLAJ-REQUEST-001",
    "accepted_transaction_id": "TX-PFLAJ-REQUEST-001",
    "request_fingerprint": _SHA_A,
}
_PFLAJ_REQUEST["request_fingerprint"] = _contract_fingerprint(
    _PFLAJ_REQUEST, field="request_fingerprint"
)
PostFinalAssessmentRequestRecord.minimal_example = deepcopy(_PFLAJ_REQUEST)
PostFinalAssessmentRequestRecord.full_example = deepcopy(_PFLAJ_REQUEST)

_PFLAJ_ABANDONMENT = {
    "schema_version": PostFinalAssessmentAbandonmentRecord.schema_id,
    "abandonment_id": "PFLAJ-ABANDONMENT-001",
    "run_id": _RUN,
    "assessment_request_id": _PFLAJ_REQUEST["assessment_request_id"],
    "assessment_request_fingerprint": _PFLAJ_REQUEST["request_fingerprint"],
    "finalized_lineage_fingerprint": _SHA_B,
    "assessment_generation": 1,
    "reason": "outcome_unknown",
    "human_actor_id": "HUMAN-001",
    "human_request_id": "PFLAJ-ASSESSMENT-REQUEST-002",
    "expected_store_revision": 20,
    "recorded_at": _NOW,
    "abandonment_event_id": "EVENT-PFLAJ-ABANDONMENT-001",
    "accepted_transaction_id": "TX-PFLAJ-SERIES-002",
    "abandonment_fingerprint": _SHA_A,
}
_PFLAJ_ABANDONMENT["abandonment_fingerprint"] = _contract_fingerprint(
    _PFLAJ_ABANDONMENT,
    field="abandonment_fingerprint",
)
PostFinalAssessmentAbandonmentRecord.minimal_example = deepcopy(_PFLAJ_ABANDONMENT)
PostFinalAssessmentAbandonmentRecord.full_example = deepcopy(_PFLAJ_ABANDONMENT)

_PFLAJ_EXECUTION = {
    "schema_version": PostFinalAssessmentExecutionRecord.schema_id,
    "execution_id": "PFLAJ-EXECUTION-001",
    "run_id": _RUN,
    "assessment_request_id": _PFLAJ_REQUEST["assessment_request_id"],
    "assessment_request_fingerprint": _PFLAJ_REQUEST["request_fingerprint"],
    "trial_id": _PFLAJ_REQUEST["trial_id"],
    "finalized_lineage_fingerprint": _SHA_B,
    "execution_archive_manifest_sha256": "f" * 64,
    "execution_receipt_id": "PFLAJ-EXECUTION-RECEIPT-001",
    "execution_status": "complete",
    "run_status": None,
    "validation_status": None,
    "failure_phase": None,
    "reason_codes": [],
    "recorded_at": _NOW,
    "execution_event_id": "EVENT-PFLAJ-EXECUTION-001",
    "accepted_transaction_id": "TX-PFLAJ-EXECUTION-001",
    "execution_fingerprint": _SHA_A,
}
_PFLAJ_EXECUTION["execution_fingerprint"] = _contract_fingerprint(
    _PFLAJ_EXECUTION,
    field="execution_fingerprint",
)
PostFinalAssessmentExecutionRecord.minimal_example = deepcopy(_PFLAJ_EXECUTION)
PostFinalAssessmentExecutionRecord.full_example = deepcopy(_PFLAJ_EXECUTION)

_PFLAJ_RESULT = {
    "schema_version": PostFinalAssessmentResultRecord.schema_id,
    "assessment_result_id": "PFLAJ-RESULT-001",
    "run_id": _RUN,
    "assessment_request_id": _PFLAJ_REQUEST["assessment_request_id"],
    "policy_revision_id": _PFLAJ_POLICY["policy_revision_id"],
    "finalized_facts_fingerprint": _SHA_A,
    "finalized_lineage_fingerprint": _SHA_B,
    "terminal_evidence_class": "available",
    "reason_codes": [],
    "shadow_request_sha256": "f" * 64,
    "execution_manifest_sha256": "1" * 64,
    "archive_manifest_sha256": "2" * 64,
    "archive_receipt_id": "PFLAJ-ARCHIVE-RECEIPT-001",
    "composition_sha256": "3" * 64,
    "presentation_sha256": "4" * 64,
    "reader_view_sha256": "5" * 64,
    "assessed_unit_count": 25,
    "finding_count": 0,
    "withheld_finding_count": 0,
    "abstention_count": 0,
    "recorded_at": _NOW,
    "result_event_id": "EVENT-PFLAJ-RESULT-001",
    "accepted_transaction_id": "TX-PFLAJ-RESULT-001",
    "result_fingerprint": _SHA_A,
}
_PFLAJ_RESULT["result_fingerprint"] = _contract_fingerprint(
    _PFLAJ_RESULT, field="result_fingerprint"
)
PostFinalAssessmentResultRecord.minimal_example = deepcopy(_PFLAJ_RESULT)
PostFinalAssessmentResultRecord.full_example = deepcopy(_PFLAJ_RESULT)

_PFLAJ_DISPOSITION = {
    "schema_version": PostFinalFindingDispositionRecord.schema_id,
    "disposition_id": "PFLAJ-DISPOSITION-001",
    "run_id": _RUN,
    "finalized_lineage_fingerprint": _SHA_B,
    "assessment_result_id": _PFLAJ_RESULT["assessment_result_id"],
    "assessment_result_fingerprint": _PFLAJ_RESULT["result_fingerprint"],
    "reader_view_sha256": _PFLAJ_RESULT["reader_view_sha256"],
    "finding_id": "F-000000000001",
    "finding_fingerprint": "6" * 64,
    "previous_disposition_id": None,
    "decision": "accept",
    "human_note": "Useful post-final observation.",
    "human_actor_id": "HUMAN-001",
    "human_request_id": "PFLAJ-DISPOSITION-REQUEST-001",
    "recorded_at": _NOW,
    "disposition_event_id": "EVENT-PFLAJ-DISPOSITION-001",
    "accepted_transaction_id": "TX-PFLAJ-DISPOSITION-001",
    "disposition_fingerprint": _SHA_A,
}
_PFLAJ_DISPOSITION["disposition_fingerprint"] = _contract_fingerprint(
    _PFLAJ_DISPOSITION, field="disposition_fingerprint"
)
PostFinalFindingDispositionRecord.minimal_example = deepcopy(_PFLAJ_DISPOSITION)
PostFinalFindingDispositionRecord.full_example = deepcopy(_PFLAJ_DISPOSITION)

_HUMAN_OBSERVATION_SPAN = {
    "schema_version": HumanObservationReportSpan.schema_id,
    "report_sha256": _SHA_C,
    "block_id": "BLOCK-PFLAJ-001",
    "start_char": 0,
    "end_char": 12,
    "excerpt_sha256": hashlib.sha256(b"Human note.").hexdigest(),
}
HumanObservationReportSpan.minimal_example = deepcopy(_HUMAN_OBSERVATION_SPAN)
HumanObservationReportSpan.full_example = deepcopy(_HUMAN_OBSERVATION_SPAN)
_PFLAJ_HUMAN_OBSERVATION = {
    "schema_version": PostFinalHumanObservationRecord.schema_id,
    "origin": "human",
    "observation_id": "PFLAJ-HUMAN-OBSERVATION-001",
    "observation_revision": 1,
    "run_id": _RUN,
    "finalized_lineage_fingerprint": _SHA_B,
    "report_revision": 1,
    "report_artifact_id": "reader_brief",
    "report_sha256": _SHA_C,
    "assessment_result_id": None,
    "assessment_result_fingerprint": None,
    "reader_view_sha256": None,
    "observation_text": "Human note.",
    "observation_sha256": hashlib.sha256(b"Human note.").hexdigest(),
    "requirement_id": None,
    "claim_id": None,
    "report_span": _HUMAN_OBSERVATION_SPAN,
    "scope_class": None,
    "dimension_id": None,
    "previous_observation_id": None,
    "previous_observation_fingerprint": None,
    "human_actor_id": "HUMAN-001",
    "human_request_id": "PFLAJ-HUMAN-OBSERVATION-REQUEST-001",
    "recorded_at": _NOW,
    "observation_event_id": "EVENT-PFLAJ-HUMAN-OBSERVATION-001",
    "accepted_transaction_id": "TX-PFLAJ-HUMAN-OBSERVATION-001",
    "observation_fingerprint": _SHA_A,
}
_PFLAJ_HUMAN_OBSERVATION["observation_fingerprint"] = _contract_fingerprint(
    _PFLAJ_HUMAN_OBSERVATION, field="observation_fingerprint"
)
PostFinalHumanObservationRecord.minimal_example = deepcopy(_PFLAJ_HUMAN_OBSERVATION)
PostFinalHumanObservationRecord.full_example = deepcopy(_PFLAJ_HUMAN_OBSERVATION)

_PFLAJ_GUIDANCE_DRAFT = {
    "schema_version": PostFinalGuidanceDraftRevision.schema_id,
    "guidance_id": "PFLAJ-GUIDANCE-001",
    "draft_revision": 1,
    "run_id": _RUN,
    "finalized_lineage_fingerprint": _SHA_B,
    "provenance_kind": "accepted_model_finding",
    "assessment_result_id": _PFLAJ_RESULT["assessment_result_id"],
    "assessment_result_fingerprint": _PFLAJ_RESULT["result_fingerprint"],
    "finding_id": _PFLAJ_DISPOSITION["finding_id"],
    "finding_fingerprint": _PFLAJ_DISPOSITION["finding_fingerprint"],
    "disposition_id": _PFLAJ_DISPOSITION["disposition_id"],
    "disposition_fingerprint": _PFLAJ_DISPOSITION["disposition_fingerprint"],
    "observation_id": None,
    "observation_fingerprint": None,
    "previous_draft_revision": None,
    "guidance_scope": "finding_only",
    "guidance_text": "Keep the conclusion aligned with the report body.",
    "guidance_sha256": hashlib.sha256(
        "Keep the conclusion aligned with the report body.".encode("utf-8")
    ).hexdigest(),
    "human_actor_id": "HUMAN-001",
    "human_request_id": "PFLAJ-GUIDANCE-DRAFT-REQUEST-001",
    "recorded_at": _NOW,
    "draft_event_id": "EVENT-PFLAJ-GUIDANCE-DRAFT-001",
    "accepted_transaction_id": "TX-PFLAJ-GUIDANCE-DRAFT-001",
    "draft_fingerprint": _SHA_A,
}
_PFLAJ_GUIDANCE_DRAFT["draft_fingerprint"] = _contract_fingerprint(
    _PFLAJ_GUIDANCE_DRAFT, field="draft_fingerprint"
)
PostFinalGuidanceDraftRevision.minimal_example = deepcopy(_PFLAJ_GUIDANCE_DRAFT)
PostFinalGuidanceDraftRevision.full_example = deepcopy(_PFLAJ_GUIDANCE_DRAFT)

_PFLAJ_GUIDANCE_STATUS = {
    "schema_version": PostFinalGuidanceStatusRevision.schema_id,
    "status_revision_id": "PFLAJ-GUIDANCE-STATUS-001",
    "run_id": _RUN,
    "finalized_lineage_fingerprint": _SHA_B,
    "guidance_id": _PFLAJ_GUIDANCE_DRAFT["guidance_id"],
    "draft_revision": _PFLAJ_GUIDANCE_DRAFT["draft_revision"],
    "guidance_sha256": _PFLAJ_GUIDANCE_DRAFT["guidance_sha256"],
    "status": "approved",
    "previous_status_revision_id": None,
    "human_actor_id": "HUMAN-001",
    "human_request_id": "PFLAJ-GUIDANCE-STATUS-REQUEST-001",
    "recorded_at": _NOW,
    "status_event_id": "EVENT-PFLAJ-GUIDANCE-STATUS-001",
    "accepted_transaction_id": "TX-PFLAJ-GUIDANCE-STATUS-001",
    "status_fingerprint": _SHA_A,
}
_PFLAJ_GUIDANCE_STATUS["status_fingerprint"] = _contract_fingerprint(
    _PFLAJ_GUIDANCE_STATUS, field="status_fingerprint"
)
PostFinalGuidanceStatusRevision.minimal_example = deepcopy(_PFLAJ_GUIDANCE_STATUS)
PostFinalGuidanceStatusRevision.full_example = deepcopy(_PFLAJ_GUIDANCE_STATUS)


V2_CONTRACT_MODELS: tuple[type[StrictModel], ...] = (
    SourceProposal,
    SourceCommitRequest,
    SourcePackCommitRequest,
    MultiTavilySourcePackCommitRequest,
    CandidateClaimsProposal,
    ScreenedCandidatesProposal,
    ClaimDraftsProposal,
    AuditProposal,
    ArtifactSubmitRequest,
    WorkspaceRunHead,
    AcceptedSourceRecord,
    AcceptedProposalRecord,
    ProposalSourceBinding,
    RunIdentity,
    StageState,
    ArtifactRecord,
    ArtifactIdentityRecord,
    ArtifactRevision,
    EventEnvelope,
    Invocation,
    Approval,
    Delivery,
    TransactionReceipt,
    RunDirection,
    ExecutionSourceManifest,
    MultiTavilyExecutionSourceManifest,
    RunExecutionAuthorizationInput,
    RunExecutionAuthorizationBootstrap,
    RunExecutionAuthorization,
    RunSourceDiscoveryAuthorizationInput,
    RunSourceDiscoveryAuthorizationBootstrap,
    RunSourceDiscoveryAuthorization,
    RunSourceAcquisitionAttemptAuthorization,
    TavilyAcquisitionBundle,
    TavilyAcquisitionBundleV2,
    TavilyAcquisitionBundleRecordV2,
    MarketDataSnapshotV1,
    MarketDataSnapshotV2,
    SourceAcquisitionAttemptAuthorizeRequest,
    WorkspaceControlStoreBootstrapV2,
    RuntimeAdapterBinding,
    RuntimeWebSearchRequestSpec,
    RuntimeWebSearchAcquisitionSpec,
    RuntimeWebSearchTaskSpecV3,
    RuntimeWebSearchAcquisitionSpecV3,
    RuntimeSourceSearchPlanV2,
    RuntimeCachedPackageAcquisitionSpec,
    RuntimeNewsApiAcquisitionSpec,
    RuntimeSourceRouteBinding,
    RuntimeSourcePlanBinding,
    CoreRunNextAction,
    CoreRunInitializeRequest,
    RunContractBinding,
    InvocationStartRequest,
    InvocationFailureRequest,
    OwnedArtifactSubmitRequest,
    OwnedArtifactSubmissionRecord,
    ClaimRecord,
    ClaimSourceBinding,
    ClaimFreezeRecord,
    ClaimFreezeRequest,
    StageTransitionRecord,
    StageArtifactBinding,
    StageGateBinding,
    StageCompleteRequest,
    GateFindingRecord,
    GateEvaluationRecord,
    GateArtifactBinding,
    GateCheckRequest,
    AuditPromotionRequest,
    AuditReportArtifact,
    RunIntegrityRecord,
    IntegrityCheckRequest,
    RepairCycleRecord,
    GateRepairCycleRecord,
    GateRepairArtifactBinding,
    GateRepairOutcomeRecord,
    ArtifactSupersessionRecord,
    RepairCompletionRecord,
    RecoveryCompletionRecord,
    RunHeadTransitionRecord,
    GuidanceReuseScopeV1,
    RunGuidanceSelectionDecisionRecord,
    RunGuidanceSnapshotItemRecord,
    RunGuidanceSnapshotRecord,
    FinalizeRenderRecord,
    FinalizationRecord,
    RunArchiveRecord,
    RunArchiveArtifactBinding,
    PackageReadyRecord,
    PackageArtifactBinding,
    ApprovalPackageBinding,
    DeliveryAuthorizationRecord,
    DeliveryAttemptRecord,
    DeliveryResultRecord,
    DeliveryResultObservation,
    PostFinalAssessmentPolicyRevision,
    PostFinalAssessmentRequestRecord,
    PostFinalAssessmentExecutionRecord,
    PostFinalAssessmentResultRecord,
    PostFinalFindingDispositionRecord,
    HumanObservationReportSpan,
    PostFinalHumanObservationRecord,
    PostFinalGuidanceDraftRevision,
    PostFinalGuidanceStatusRevision,
    RepairStartRequest,
    ArtifactSupersedeRequest,
    ArtifactRevertRequest,
    RepairCompleteRequest,
    RecoveryCompleteRequest,
    RunResetRequest,
    RunSuccessorStartRequest,
    FinalizeRenderRequest,
    FinalizeCompleteRequest,
    InternalApprovalRequest,
    DeliveryAuthorizationRequest,
    RunTerminationRequest,
    DeliveryAttemptRequest,
    DeliveryResultRequest,
    CheckoutRevisionRecord,
    CheckoutRevisionMember,
    ReceiptCheckoutBinding,
    PublicationIdentityV1,
    CheckoutPublicationIntent,
    CheckoutPublicationMember,
    CheckoutPublicationAck,
    CheckoutPublicationCleanupObservation,
)

V2_CONTRACT_IDS: tuple[str, ...] = tuple(
    model.schema_id for model in V2_CONTRACT_MODELS
)

for _contract_model in V2_CONTRACT_MODELS:
    SchemaRegistry.register(_contract_model)


LEGACY_READ_ONLY_CONTRACTS: tuple[str, ...] = tuple(
    sorted(
        {
            *(
                schema_id
                for schema_id in SchemaRegistry.all_ids()
                if schema_id not in V2_CONTRACT_IDS
            ),
            *AGENT_ARTIFACT_IDS,
        }
    )
)


def _freeze_json(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise TypeError("Legacy contract payload contains a non-finite number.")
        return value
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    raise TypeError("Legacy contract payload must contain JSON-compatible values.")


@dataclass(frozen=True)
class ContractReadResult:
    """Shape/read classification with no write-permission semantics."""

    classification: Literal["canonical_v2", "opaque_legacy_read_only", "invalid"]
    requested_schema_id: str
    canonical_model: Optional[StrictModel] = None
    legacy_payload: Optional[Any] = None
    violations: tuple[FieldViolation, ...] = ()


def read_contract_payload(schema_id: str, payload: Any) -> ContractReadResult:
    """Classify a canonical v2 payload or an explicitly named legacy payload.

    A legacy classification is deliberately opaque: this boundary proves only
    that the exact legacy owner identity is known and the payload is immutable
    finite JSON.  It does not validate domain semantics, name a v2 successor,
    migrate fields, or expose write permission.
    """

    if schema_id in LEGACY_READ_ONLY_CONTRACTS:
        try:
            frozen_payload = _freeze_json(payload)
        except TypeError:
            return ContractReadResult(
                classification="invalid",
                requested_schema_id=schema_id,
                violations=(
                    FieldViolation(
                        field="$",
                        error="must contain finite JSON-compatible values",
                    ),
                ),
            )
        return ContractReadResult(
            classification="opaque_legacy_read_only",
            requested_schema_id=schema_id,
            legacy_payload=frozen_payload,
        )

    contract = SchemaRegistry.get(schema_id)
    if contract not in V2_CONTRACT_MODELS:
        return ContractReadResult(
            classification="invalid",
            requested_schema_id=schema_id,
            violations=(
                FieldViolation(field="schema_id", error="unknown v2 contract"),
            ),
        )
    try:
        model = contract.model_validate(payload)
    except ValidationError as exc:
        return ContractReadResult(
            classification="invalid",
            requested_schema_id=schema_id,
            violations=tuple(pydantic_error_violations(exc)),
        )
    return ContractReadResult(
        classification="canonical_v2",
        requested_schema_id=schema_id,
        canonical_model=model,
    )


__all__ = [
    "AcceptedProposalRecord",
    "AcceptedSourceRecord",
    "Approval",
    "ApprovalPackageBinding",
    "ApprovalPackageBindingReference",
    "ApprovalReference",
    "ArtifactRevertRequest",
    "ArtifactFormat",
    "ArtifactIdentityRecord",
    "ArtifactIdentityReference",
    "ArtifactSupersedeRequest",
    "ArtifactSupersessionRecord",
    "ArtifactSupersessionReference",
    "ArtifactRecord",
    "ArtifactRevision",
    "ArtifactRevisionReference",
    "ArtifactSubmitRequest",
    "authorized_input_classification_bytes",
    "AuditPromotionRequest",
    "AuditProposal",
    "AuditReportArtifact",
    "CandidateClaimsProposal",
    "ClaimFreezeRecord",
    "ClaimFreezeRequest",
    "ClaimRecord",
    "ClaimSourceBinding",
    "ClaimDraftsProposal",
    "CheckoutPublicationAck",
    "CheckoutPublicationCleanupObservation",
    "CheckoutPublicationIntent",
    "CheckoutPublicationMember",
    "CheckoutRevisionId",
    "CheckoutRevisionMember",
    "CheckoutRevisionRecord",
    "ContractReadResult",
    "CoreRunEventBinding",
    "CoreRunInitializeRequest",
    "CoreRunNextAction",
    "ExecutionSourceManifest",
    "ExecutionSourceManifestMember",
    "MultiTavilyExecutionSourceManifest",
    "MultiTavilySourcePackCommitRequest",
    "Delivery",
    "DeliveryAttemptRecord",
    "DeliveryAttemptReference",
    "DeliveryAttemptRequest",
    "DeliveryAuthorizationRecord",
    "DeliveryAuthorizationReference",
    "DeliveryAuthorizationRequest",
    "DeliveryResultRecord",
    "DeliveryResultObservation",
    "DeliveryResultReference",
    "DeliveryResultRequest",
    "derive_reader_review_result_status",
    "EventEnvelope",
    "GATE_ID_VALUES",
    "GateArtifactBinding",
    "GateCheckRequest",
    "GateEvaluationRecord",
    "GateFindingRecord",
    "GateRepairArtifactBinding",
    "GateRepairArtifactBindingReference",
    "GateRepairCycleRecord",
    "GateRepairCycleReference",
    "GateRepairOutcomeRecord",
    "GateRepairOutcomeReference",
    "FinalizeCompleteRequest",
    "FinalizeRenderRecord",
    "FinalizeRenderReference",
    "FinalizeRenderRequest",
    "FinalizationRecord",
    "FinalizationReference",
    "IntegrityCheckRequest",
    "InternalApprovalRequest",
    "IntakeEventBinding",
    "Invocation",
    "InvocationStartRequest",
    "InvocationFailureRequest",
    "LEGACY_READ_ONLY_CONTRACTS",
    "MimeType",
    "OwnedArtifactSubmissionRecord",
    "OwnedArtifactSubmitRequest",
    "ProposalSourceBinding",
    "ReaderReviewAssessmentInput",
    "PackageArtifactBinding",
    "PackageArtifactBindingReference",
    "PackageReadyRecord",
    "PackageReadyReference",
    "PostFinalAssessmentPolicyRevision",
    "PostFinalAssessmentPolicyRevisionReference",
    "PostFinalAssessmentRequestRecord",
    "PostFinalAssessmentRequestReference",
    "PostFinalAssessmentExecutionRecord",
    "PostFinalAssessmentExecutionReference",
    "PostFinalAssessmentExecutionStatus",
    "PostFinalAssessmentFailurePhase",
    "PostFinalAssessmentResultRecord",
    "PostFinalAssessmentResultReference",
    "ReaderReviewResultStatus",
    "PostFinalFindingDispositionRecord",
    "PostFinalFindingDispositionReference",
    "PostFinalGuidanceDraftRevision",
    "PostFinalGuidanceDraftReference",
    "PostFinalGuidanceStatusRevision",
    "POST_FINAL_GUIDANCE_STATUS_TRANSITIONS",
    "post_final_guidance_legal_actions",
    "post_final_guidance_status_transition_allowed",
    "PostFinalGuidanceStatusReference",
    "PublicationIdentityV1",
    "RecoveryCompleteRequest",
    "RecoveryCompletionRecord",
    "RecoveryCompletionReference",
    "RepairCompleteRequest",
    "RepairCompletionRecord",
    "RepairCompletionReference",
    "RepairCycleRecord",
    "RepairCycleReference",
    "RepairStartRequest",
    "ReceiptCheckoutBinding",
    "RunArchiveArtifactBinding",
    "RunArchiveArtifactBindingReference",
    "RunArchiveRecord",
    "RunArchiveReference",
    "RunContractBinding",
    "RunExecutionAuthorizationReference",
    "RunSourceDiscoveryAuthorizationReference",
    "RunSourceAcquisitionAttemptAuthorizationReference",
    "RunDirection",
    "GuidanceReuseScopeV1",
    "RunExecutionAuthorization",
    "RunExecutionAuthorizationBootstrap",
    "RunExecutionAuthorizationInput",
    "RunSourceDiscoveryAuthorization",
    "RunSourceAcquisitionAttemptAuthorization",
    "TavilyAcquisitionExchange",
    "TavilyExtractUrlOutcome",
    "TavilyAcquisitionBundle",
    "TavilySearchTaskExchange",
    "TavilyExtractBatchExchange",
    "TavilyTaskAcquisitionStatus",
    "TavilyAcquisitionBundleV2",
    "TavilyAcquisitionBundleRecordV2",
    "MarketDataSecurityV1",
    "MarketDataSecurityGapV1",
    "MarketDataSnapshotV1",
    "MarketDataWorkbookIdentityV2",
    "MarketDataSeriesPointV2",
    "MarketDataCorporateActionV2",
    "MarketDataFieldValueV2",
    "MarketDataSecurityV2",
    "MarketDataBenchmarkV2",
    "MarketDataFxRateV2",
    "MarketDataEventReactionV2",
    "MarketDataGapV2",
    "MarketDataConflictV2",
    "MarketDataSnapshotV2",
    "SourceAcquisitionAttemptAuthorizeRequest",
    "RunSourceDiscoveryAuthorizationBootstrap",
    "RunSourceDiscoveryAuthorizationInput",
    "RuntimeAdapterBinding",
    "RuntimeCachedPackageAcquisitionSpec",
    "RuntimeNewsApiAcquisitionSpec",
    "RuntimeSourceAcquisitionSpec",
    "RUNTIME_SOURCE_GENERIC_PROVIDER_IDS",
    "RUNTIME_SOURCE_PROVIDER_IDS",
    "RUNTIME_SOURCE_ROUTE_IDS",
    "RUNTIME_SOURCE_WEB_PROVIDER_IDS",
    "RuntimeSourcePlanBinding",
    "RuntimeSourceSearchPlanV2",
    "RuntimeSourceRouteBinding",
    "RuntimeWebSearchAcquisitionSpec",
    "RuntimeWebSearchBackfillSpecV1",
    "RuntimeWebSearchTaskSpecV3",
    "RuntimeWebSearchAcquisitionSpecV3",
    "RuntimeWebSearchRequestSpec",
    "RunIdentity",
    "RunIntegrityRecord",
    "RunHeadTransitionRecord",
    "RunHeadTransitionReference",
    "RunGuidanceSelectionDecisionRecord",
    "RunGuidanceSelectionDecisionReference",
    "RunGuidanceSnapshotItemRecord",
    "RunGuidanceSnapshotItemReference",
    "RunGuidanceSnapshotRecord",
    "RunGuidanceSnapshotReference",
    "RunResetRequest",
    "RunTerminationRequest",
    "RunSuccessorStartRequest",
    "ScreenedCandidatesProposal",
    "SourceAcquisitionFailureEvidence",
    "SOURCE_ACQUISITION_METHODS",
    "SOURCE_ELIGIBILITY_REASONS",
    "SOURCE_MATERIAL_KINDS",
    "SOURCE_ORIGIN_TYPES",
    "SourceCommitRequest",
    "SourcePackCommitMember",
    "SourcePackCommitRequest",
    "SourceProposal",
    "StageArtifactBinding",
    "StageCompleteRequest",
    "StageGateBinding",
    "StageState",
    "StageTransitionRecord",
    "StrictModel",
    "TransactionReceipt",
    "V2_CONTRACT_IDS",
    "V2_CONTRACT_MODELS",
    "WorkspaceRunHead",
    "WorkspaceControlStoreBootstrapV2",
    "read_contract_payload",
]
