"""Strict, versioned v2 proposal and control DTO contracts.

These models define input shape only.  They do not write runtime state, decide
stage legality, establish source truth, or replace any current v1 authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Literal, Optional, Union

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
    r"[A-Za-z0-9][A-Za-z0-9._:-]*\.(?:json|md)$"
)
_APPROVAL_REASON_MAX_LENGTH = 1000
_MIME_TYPE_PATTERN = (
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)

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


def _contains_non_finite_number(value: Any) -> bool:
    stack = [value]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if type(current) is float and not math.isfinite(current):
            return True
        if isinstance(current, dict):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend(current)
    return False


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
RoleTopology = Literal["default", "strict", "human_assisted"]
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


def _event_type_json_schema(schema: dict[str, Any]) -> None:
    from multi_agent_brief.orchestrator.runtime_state.event_log import EVENT_TYPES

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

    @model_validator(mode="before")
    @classmethod
    def reject_non_finite_json_numbers(cls, value: Any) -> Any:
        if _contains_non_finite_number(value):
            raise PydanticCustomError(
                "non_finite_json_number",
                "non-finite JSON number",
            )
        return value

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

    @classmethod
    def contract_json_schema(cls) -> dict[str, Any]:
        schema = cls.model_json_schema()
        schema["$id"] = cls.schema_id
        schema["examples"] = [deepcopy(cls.minimal_example), deepcopy(cls.full_example)]
        return schema

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


class IntakeEventBinding(StrictModel):
    request_id: ContractId
    request_fingerprint: Sha256
    invocation_id: ContractId
    outcome: Literal["committed", "rejected"]
    source_id: Optional[ContractId] = None
    proposal_id: Optional[ContractId] = None
    reason_code: Optional[ContractId] = None

    @model_validator(mode="after")
    def identity_shape_is_unambiguous(self) -> "IntakeEventBinding":
        if self.source_id is not None and self.proposal_id is not None:
            raise ValueError("intake binding cannot name source and proposal")
        if self.outcome == "committed" and self.reason_code is not None:
            raise ValueError("committed intake binding cannot carry a rejection reason")
        if self.outcome == "rejected" and self.reason_code is None:
            raise ValueError("rejected intake binding requires a reason code")
        return self


class CoreRunEventBinding(StrictModel):
    """Replay identity for one deterministic PR-4A domain effect."""

    request_id: ContractId
    request_fingerprint: Sha256
    effect_kind: Literal[
        "initialize",
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
        "run_head_transition",
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
    underlying_evidence_type: Literal[
        tuple(sorted(VALID_UNDERLYING_EVIDENCE_TYPES))
    ]
    raw_underlying_evidence_type: Optional[CleanText] = None
    content_sha256: Sha256
    content_media_type: MimeType
    raw_payload_sha256: Optional[Sha256] = None
    raw_payload_media_type: Optional[MimeType] = None

    @model_validator(mode="after")
    def raw_payload_fields_are_paired(self) -> "SourceProposal":
        if (self.raw_payload_sha256 is None) != (
            self.raw_payload_media_type is None
        ):
            raise ValueError("raw payload hash and media type must be paired")
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
    underlying_evidence_type: Literal[
        tuple(sorted(VALID_UNDERLYING_EVIDENCE_TYPES))
    ]
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
        if not (all(value is None for value in raw_values) or all(value is not None for value in raw_values)):
            raise ValueError("raw payload fields must be all present or all absent")
        if self.claims_eligible != (
            self.eligibility_reason == "eligible_durable_source_content"
        ):
            raise ValueError("source eligibility reason does not match verdict")
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
        from multi_agent_brief.orchestrator.runtime_state.event_log import EVENT_TYPES

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
                "repair_start": {"repair_started"},
                "artifact_supersession": {
                    "repair_stage_superseded",
                    "owned_artifact_accepted",
                },
                "repair_complete": {"repair_completed", "stage_status_changed"},
                "recovery_complete": {"decision_recorded"},
                "run_head_transition": {
                    "run_reset",
                    "run_initialized",
                    "stage_status_changed",
                },
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
        from multi_agent_brief.product.release_approval import RELEASE_MODES

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


class RunDirection(StrictModel):
    schema_id = "briefloop.run_direction.v2"

    schema_version: Literal["briefloop.run_direction.v2"]
    subject_name: CleanText
    industry_or_theme: Optional[CleanText] = None
    brief_title: CleanText
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
    target_terms: list[CleanText] = Field(min_length=1)

    @model_validator(mode="after")
    def direction_is_canonical(self) -> "RunDirection":
        for field_name in (
            "focus_areas",
            "excluded_topics",
            "forbidden_sources",
            "output_formats",
            "target_terms",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique values")
        if (self.report_window_start is None) != (self.report_window_end is None):
            raise ValueError("report window boundaries must be paired")
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
        return self


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


class RuntimeSourceRouteBinding(StrictModel):
    """One safe source route frozen from initialization input."""

    schema_id = "briefloop.runtime_source_route_binding.v2"

    schema_version: Literal["briefloop.runtime_source_route_binding.v2"]
    route_id: ContractId
    route_kind: Literal[
        "manual",
        "local_file",
        "rss",
        "external_api",
        "runtime_tool",
        "cached_package",
        "disabled",
    ]
    provider_id: Optional[ContractId] = None
    execution_owner: Literal["specialist", "deterministic", "human"]
    required: bool
    route_fingerprint: Sha256

    @model_validator(mode="after")
    def canonical_route(self) -> "RuntimeSourceRouteBinding":
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
        "manual", "disabled", "external_api", "runtime_tool", "cached_package"
    ]
    search_backend: Optional[ContractId] = None
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
            if self.stage_id is None or self.role_id is None or self.request_schema_id is None:
                raise ValueError("delegate action requires stage, role and request schema")
        elif self.role_id is not None:
            raise ValueError("only delegate actions name a role")
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
        "initialize", "activate", "complete", "satisfied_by_topology", "repair_reopen"
    ]
    requested_decision: Optional[Literal["continue"]] = None
    prior_status: Optional[Literal["pending", "ready", "complete", "blocked", "skipped"]] = None
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
        topology_values = (self.topology, self.satisfaction_source_kind, self.satisfied_by_id)
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
        keys = [(item.artifact_id, item.revision) for item in self.expected_input_artifacts]
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
            if self.integrity_revision != 1 or self.prior_integrity_revision is not None:
                raise ValueError("clean integrity is the initial revision")
            if any(item is not None for item in contamination) or self.observed_sha256 is not None:
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
    reason_code: Literal["run_reset"]
    successor_disposition: Literal["non_reference"]
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
    schema_version: Literal[
        "briefloop.checkout_publication_cleanup_observation.v2"
    ]
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
    tree_sha256 = hashlib.sha256(
        _CHECKOUT_TREE_DOMAIN + manifest_bytes
    ).hexdigest()
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
                    None
                    if pre_record is None
                    else pre_record.checkout_revision_id
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
    run_contract_bindings: list[RunContractBindingReference] = Field(default_factory=list)
    owned_artifact_submissions: list[OwnedArtifactSubmissionReference] = Field(default_factory=list)
    stage_transitions: list[StageTransitionReference] = Field(default_factory=list)
    stage_artifact_bindings: list[StageArtifactBindingReference] = Field(default_factory=list)
    stage_gate_bindings: list[StageGateBindingReference] = Field(default_factory=list)
    claims: list[ClaimReference] = Field(default_factory=list)
    claim_source_bindings: list[ClaimSourceBindingReference] = Field(default_factory=list)
    claim_freezes: list[ClaimFreezeReference] = Field(default_factory=list)
    gate_evaluations: list[GateEvaluationReference] = Field(default_factory=list)
    gate_findings: list[GateFindingReference] = Field(default_factory=list)
    gate_artifact_bindings: list[GateArtifactBindingReference] = Field(default_factory=list)
    run_integrity_records: list[RunIntegrityReference] = Field(default_factory=list)
    repair_cycles: list[RepairCycleReference] = Field(default_factory=list)
    artifact_supersessions: list[ArtifactSupersessionReference] = Field(default_factory=list)
    repair_completions: list[RepairCompletionReference] = Field(default_factory=list)
    recovery_completions: list[RecoveryCompletionReference] = Field(default_factory=list)
    run_head_transitions: list[RunHeadTransitionReference] = Field(default_factory=list)
    finalize_renders: list[FinalizeRenderReference] = Field(default_factory=list)
    finalizations: list[FinalizationReference] = Field(default_factory=list)
    run_archives: list[RunArchiveReference] = Field(default_factory=list)
    run_archive_artifact_bindings: list[RunArchiveArtifactBindingReference] = Field(default_factory=list)
    package_ready_records: list[PackageReadyReference] = Field(default_factory=list)
    package_artifact_bindings: list[PackageArtifactBindingReference] = Field(default_factory=list)
    approvals: list[ApprovalReference] = Field(default_factory=list)
    approval_package_bindings: list[ApprovalPackageBindingReference] = Field(default_factory=list)
    delivery_authorizations: list[DeliveryAuthorizationReference] = Field(default_factory=list)
    delivery_attempts: list[DeliveryAttemptReference] = Field(default_factory=list)
    delivery_results: list[DeliveryResultReference] = Field(default_factory=list)
    checkout_revisions: list[CheckoutRevisionReference] = Field(default_factory=list)
    receipt_checkout_bindings: list[ReceiptCheckoutBindingReference] = Field(
        default_factory=list
    )
    checkout_publication_intents: list[
        CheckoutPublicationIntentReference
    ] = Field(default_factory=list)

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
ArtifactIdentityRecord.full_example = deepcopy(
    ArtifactIdentityRecord.minimal_example
)

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
_RUNTIME_ADAPTER_BINDING = {
    "schema_version": RuntimeAdapterBinding.schema_id,
    "run_id": _RUN,
    "runtime": "operator",
    "adapter_id": "briefloop-operator-controlstore",
    "adapter_version": "1",
    "briefloop_version": "0.12.1",
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
    "route_fingerprint": "0" * 64,
}
_SOURCE_ROUTE["route_fingerprint"] = _contract_fingerprint(
    _SOURCE_ROUTE,
    field="route_fingerprint",
)
RuntimeSourceRouteBinding.minimal_example = deepcopy(_SOURCE_ROUTE)
RuntimeSourceRouteBinding.full_example = deepcopy(_SOURCE_ROUTE)

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
    "schema_version": RepairCycleRecord.schema_id, "repair_id": "REPAIR-001",
    "run_id": _RUN, "contamination_revision": 2, "owner_stage_id": "editor",
    "permitted_artifact_ids": ["audited_brief"], "reason_code": "artifact_drift",
    "started_at": _NOW, "start_event_id": "EVT-REPAIR-001",
    "accepted_transaction_id": "REQ-REPAIR-001", "request_fingerprint": _SHA_A,
}
ArtifactSupersessionRecord.minimal_example = {
    "schema_version": ArtifactSupersessionRecord.schema_id,
    "supersession_id": "SUPERSEDE-001", "run_id": _RUN, "repair_id": "REPAIR-001",
    "mode": "repair", "prior_artifact": _AR1,
    "successor_artifact": {"artifact_id": "audited_brief", "revision": 2},
    "reason_code": "repair_replacement", "created_at": _NOW,
    "accepted_event_id": "EVT-SUPERSEDE-001", "accepted_transaction_id": "REQ-SUPERSEDE-001",
    "request_fingerprint": _SHA_A,
}
RepairCompletionRecord.minimal_example = {
    "schema_version": RepairCompletionRecord.schema_id, "repair_completion_id": "REPAIR-DONE-001",
    "run_id": _RUN, "repair_id": "REPAIR-001", "contamination_revision": 2,
    "supersession_ids": ["SUPERSEDE-001"], "reopened_transition_ids": ["TRANS-REOPEN-001"],
    "completed_at": _NOW, "completion_event_id": "EVT-REPAIR-DONE-001",
    "accepted_transaction_id": "REQ-REPAIR-DONE-001", "request_fingerprint": _SHA_A,
}
RecoveryCompletionRecord.minimal_example = {
    "schema_version": RecoveryCompletionRecord.schema_id, "recovery_id": "RECOVERY-001",
    "run_id": _RUN, "repair_completion_id": "REPAIR-DONE-001", "contamination_revision": 2,
    "supersession_ids": ["SUPERSEDE-001"], "rerun_transition_ids": ["TRANS-RERUN-001"],
    "gate_evaluation_ids": [], "disposition": "recovered_non_reference", "completed_at": _NOW,
    "completion_event_id": "EVT-RECOVERY-001", "accepted_transaction_id": "REQ-RECOVERY-001",
    "request_fingerprint": _SHA_A,
}
RunHeadTransitionRecord.minimal_example = {
    "schema_version": RunHeadTransitionRecord.schema_id, "head_transition_id": "HEAD-TRANS-001",
    "workspace_id": "WS-001", "predecessor_run_id": _RUN,
    "successor_run_id": "RUN-20260714-002", "prior_workspace_revision": 14,
    "successor_workspace_revision": 15, "reason_code": "run_reset",
    "successor_disposition": "non_reference", "created_at": _NOW,
    "transition_event_id": "EVT-RESET-001", "accepted_transaction_id": "REQ-RESET-001",
    "request_fingerprint": _SHA_A,
}
FinalizeRenderRecord.minimal_example = {
    "schema_version": FinalizeRenderRecord.schema_id, "render_id": "RENDER-001", "run_id": _RUN,
    "audit_proposal_id": "PROP-AUDIT-001", "audited_brief": _AR1, "audit_report": _AR2,
    "reader_artifacts": [_READER], "reader_clean_status": "pass",
    "policy_result_fingerprint": _SHA_A, "run_contract_fingerprint": _SHA_B,
    "created_at": _NOW, "render_event_id": "EVT-RENDER-001",
    "accepted_transaction_id": "REQ-RENDER-001", "request_fingerprint": _SHA_A,
}
FinalizationRecord.minimal_example = {
    "schema_version": FinalizationRecord.schema_id, "finalization_id": "FINAL-001", "run_id": _RUN,
    "render_id": "RENDER-001", "finalize_transition_id": "TRANS-FINAL-001",
    "finalize_gate_batch_id": "GATE-BATCH-FINAL-001",
    "finalize_gate_evaluation_ids": ["GATE-FINAL-001"], "recovery_id": None,
    "integrity_revision": 1, "finalized_at": _NOW, "finalization_event_id": "EVT-FINAL-001",
    "accepted_transaction_id": "REQ-FINAL-001", "request_fingerprint": _SHA_A,
}
RunArchiveRecord.minimal_example = {
    "schema_version": RunArchiveRecord.schema_id, "archive_id": "ARCHIVE-001", "run_id": _RUN,
    "finalization_id": "FINAL-001", "archive_artifact": {"artifact_id": "run_archive", "revision": 1},
    "manifest_sha256": _SHA_A, "included_count": 1, "created_at": _NOW,
    "archive_event_id": "EVT-ARCHIVE-001", "accepted_transaction_id": "REQ-FINAL-001",
    "request_fingerprint": _SHA_A,
}
RunArchiveArtifactBinding.minimal_example = {
    "schema_version": RunArchiveArtifactBinding.schema_id, "run_id": _RUN, "archive_id": "ARCHIVE-001",
    "position": 0, "artifact_id": "audited_brief", "artifact_revision": 1,
    "artifact_sha256": _SHA_A, "usage": "workflow", "accepted_transaction_id": "REQ-FINAL-001",
}
PackageReadyRecord.minimal_example = {
    "schema_version": PackageReadyRecord.schema_id, "package_id": "PACKAGE-001", "run_id": _RUN,
    "finalization_id": "FINAL-001", "archive_id": "ARCHIVE-001",
    "package_manifest_artifact": {"artifact_id": "package_manifest", "revision": 1},
    "package_manifest_sha256": _SHA_A, "artifact_count": 2, "created_at": _NOW,
    "package_event_id": "EVT-PACKAGE-001", "accepted_transaction_id": "REQ-FINAL-001",
    "request_fingerprint": _SHA_A,
}
PackageArtifactBinding.minimal_example = {
    "schema_version": PackageArtifactBinding.schema_id, "run_id": _RUN, "package_id": "PACKAGE-001",
    "position": 0, "artifact_id": "reader_brief", "artifact_revision": 1,
    "artifact_sha256": _SHA_A, "usage": "reader", "accepted_transaction_id": "REQ-FINAL-001",
}
ApprovalPackageBinding.minimal_example = {
    "schema_version": ApprovalPackageBinding.schema_id, "run_id": _RUN,
    "approval_id": "APPROVAL-001", "package_id": "PACKAGE-001",
    "accepted_transaction_id": "REQ-APPROVAL-001",
}
DeliveryAuthorizationRecord.minimal_example = {
    "schema_version": DeliveryAuthorizationRecord.schema_id, "authorization_id": "AUTH-001", "run_id": _RUN,
    "package_id": "PACKAGE-001", "prior_authorization_id": None,
    "approval_mode": "internal_draft", "retry_of_attempt_id": None, "purpose": "initial_attempt",
    "decision": "authorize",
    "target": "local", "channel": "filesystem", "recipient_fingerprint": _SHA_A,
    "actor_id": "HUMAN-001", "reason": "Approved local package preparation",
    "recorded_at": _NOW, "authorization_event_id": "EVT-AUTH-001",
    "accepted_transaction_id": "REQ-AUTH-001", "request_fingerprint": _SHA_A,
}
DeliveryAttemptRecord.minimal_example = {
    "schema_version": DeliveryAttemptRecord.schema_id, "attempt_id": "ATTEMPT-001", "run_id": _RUN,
    "package_id": "PACKAGE-001", "authorization_id": "AUTH-001", "target": "local",
    "channel": "filesystem", "recipient_fingerprint": _SHA_A,
    "connector_operation_id": "OP-001", "connector_request_fingerprint": _SHA_B,
    "created_at": _NOW, "attempt_event_id": "EVT-ATTEMPT-001",
    "accepted_transaction_id": "REQ-ATTEMPT-001", "request_fingerprint": _SHA_A,
}
DeliveryResultRecord.minimal_example = {
    "schema_version": DeliveryResultRecord.schema_id, "result_id": "RESULT-001", "run_id": _RUN,
    "attempt_id": "ATTEMPT-001", "prior_result_id": None, "reconciliation_authorization_id": None, "status": "bundle_prepared",
    "adapter_id": "local-adapter", "adapter_version": "V1", "connector_operation_id": "OP-001",
    "evidence_sha256": _SHA_A, "evidence_artifact": None, "recorded_at": _NOW,
    "result_event_id": "EVT-RESULT-001", "accepted_transaction_id": "REQ-RESULT-001",
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
    "schema_version": RepairStartRequest.schema_id, "request_id": "REQ-REPAIR-001", "run_id": _RUN,
    "contamination_revision": 2, "owner_stage_id": "editor",
    "permitted_artifact_ids": ["audited_brief"], "reason_code": "artifact_drift",
    "expected_store_revision": 14,
}
ArtifactSupersedeRequest.minimal_example = {
    "schema_version": ArtifactSupersedeRequest.schema_id, "request_id": "REQ-SUPERSEDE-001", "run_id": _RUN,
    "repair_id": "REPAIR-001", "prior_artifact": _AR1,
    "input_path": "scratch/INV-REPAIR-001/audited_brief.md", "expected_input_sha256": _SHA_A,
    "expected_current_revision": 1,
    "mode": "repair", "reason_code": "repair_replacement", "expected_store_revision": 14,
}
ArtifactRevertRequest.minimal_example = {
    "schema_version": ArtifactRevertRequest.schema_id, "request_id": "REQ-REVERT-001", "run_id": _RUN,
    "repair_id": "REPAIR-001", "current_artifact": {"artifact_id": "audited_brief", "revision": 2},
    "historical_source": _AR1, "expected_current_revision": 2, "mode": "revert",
    "reason_code": "explicit_revert", "expected_store_revision": 15,
}
RepairCompleteRequest.minimal_example = {
    "schema_version": RepairCompleteRequest.schema_id, "request_id": "REQ-REPAIR-DONE-001", "run_id": _RUN,
    "repair_id": "REPAIR-001", "supersession_ids": ["SUPERSEDE-001"],
    "expected_stage_revisions": {"editor": 2}, "expected_store_revision": 16,
}
RecoveryCompleteRequest.minimal_example = {
    "schema_version": RecoveryCompleteRequest.schema_id, "request_id": "REQ-RECOVERY-001", "run_id": _RUN,
    "repair_completion_id": "REPAIR-DONE-001", "contamination_revision": 2,
    "rerun_transition_ids": ["TRANS-RERUN-001"], "gate_evaluation_ids": [], "expected_store_revision": 18,
}
RunResetRequest.minimal_example = {
    "schema_version": RunResetRequest.schema_id, "request_id": "REQ-RESET-001",
    "predecessor_run_id": _RUN, "successor_run_id": "RUN-20260714-002", "workspace_id": "WS-001",
    "runtime": "operator", "expected_head_run_id": _RUN, "expected_store_revision": 14,
    "expected_workspace_revision": 1,
    "run_direction": deepcopy(CoreRunInitializeRequest.minimal_example["run_direction"]),
    "workspace_config_sha256": _SHA_A, "sources_config_sha256": _SHA_B,
    "role_topology": "default", "gate_strictness": {key: True for key in GATE_ID_VALUES},
    "input_governance_required": False,
}
FinalizeRenderRequest.minimal_example = {
    "schema_version": FinalizeRenderRequest.schema_id, "request_id": "REQ-RENDER-001", "run_id": _RUN,
    "audit_proposal_id": "PROP-AUDIT-001", "expected_audited_brief": _AR1,
    "expected_audit_report": _AR2, "reader_scratch_inputs": {"reader_brief": "scratch/INV-FINAL-001/brief.md"},
    "expected_reader_sha256": {"reader_brief": _SHA_A},
    "expected_reader_revisions": {"reader_brief": 0}, "expected_store_revision": 20,
}
FinalizeCompleteRequest.minimal_example = {
    "schema_version": FinalizeCompleteRequest.schema_id, "request_id": "REQ-FINAL-001", "run_id": _RUN,
    "render_id": "RENDER-001", "expected_finalize_stage_revision": 0,
    "gate_evaluation_ids": ["GATE-FINAL-001"], "recovery_id": None, "expected_store_revision": 22,
}
InternalApprovalRequest.minimal_example = {
    "schema_version": InternalApprovalRequest.schema_id, "request_id": "REQ-APPROVAL-001", "run_id": _RUN,
    "package_id": "PACKAGE-001", "approval_id": "APPROVAL-001", "mode": "internal_management_review",
    "role": "content_owner", "decision": "approve", "reason": "Approved for internal management review",
    "actor_id": "HUMAN-001", "expected_store_revision": 23,
}
DeliveryAuthorizationRequest.minimal_example = {
    "schema_version": DeliveryAuthorizationRequest.schema_id, "request_id": "REQ-AUTH-001", "run_id": _RUN,
    "package_id": "PACKAGE-001", "prior_authorization_id": None,
    "approval_mode": "internal_draft", "retry_of_attempt_id": None, "purpose": "initial_attempt",
    "decision": "authorize",
    "target": "local", "channel": "filesystem", "recipient_fingerprint": _SHA_A,
    "actor_id": "HUMAN-001", "reason": "Approved local package preparation", "expected_store_revision": 24,
}
DeliveryAttemptRequest.minimal_example = {
    "schema_version": DeliveryAttemptRequest.schema_id, "request_id": "REQ-ATTEMPT-001", "run_id": _RUN,
    "package_id": "PACKAGE-001", "authorization_id": "AUTH-001", "connector_operation_id": "OP-001",
    "connector_request_fingerprint": _SHA_B, "expected_store_revision": 25,
}
DeliveryResultRequest.minimal_example = {
    "schema_version": DeliveryResultRequest.schema_id, "request_id": "REQ-RESULT-001", "run_id": _RUN,
    "attempt_id": "ATTEMPT-001", "prior_result_id": None,
    "observation_input_path": None, "expected_observation_sha256": None,
    "reconciliation_authorization_id": None,
    "expected_store_revision": 26,
}

for _model in (
    RepairCycleRecord, ArtifactSupersessionRecord, RepairCompletionRecord,
    RecoveryCompletionRecord, RunHeadTransitionRecord, FinalizeRenderRecord,
    FinalizationRecord, RunArchiveRecord, RunArchiveArtifactBinding,
    PackageReadyRecord, PackageArtifactBinding, ApprovalPackageBinding,
    DeliveryAuthorizationRecord, DeliveryAttemptRecord, DeliveryResultRecord,
    RepairStartRequest, ArtifactSupersedeRequest, ArtifactRevertRequest,
    RepairCompleteRequest, RecoveryCompleteRequest, RunResetRequest,
    FinalizeRenderRequest, FinalizeCompleteRequest, InternalApprovalRequest,
    DeliveryAuthorizationRequest, DeliveryAttemptRequest, DeliveryResultRequest,
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
    "workspace_id": "WS-001", "run_id": _RUN,
    "parent_checkout_revision_id": None,
    "manifest_sha256": _SHA_B, "tree_sha256": "a" * 64,
    "member_count": 1, "created_at": _NOW,
    "creator_transaction_id": "TXN-001",
}
CheckoutRevisionMember.minimal_example = {
    "schema_version": CheckoutRevisionMember.schema_id,
    "checkout_revision_id": _CHECKOUT_REVISION_EXAMPLE, "ordinal": 0,
    "workspace_id": "WS-001", "run_id": _RUN,
    "canonical_path": "output/brief.md", "artifact_id": "reader_brief",
    "artifact_revision": 1, "blob_sha256": _SHA_A, "byte_size": 4,
}
ReceiptCheckoutBinding.minimal_example = {
    "schema_version": ReceiptCheckoutBinding.schema_id,
    "workspace_id": "WS-001", "run_id": _RUN, "transaction_id": "TXN-001",
    "pre_run_id": _RUN, "pre_checkout_revision_id": None,
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
    "post_manifest_sha256": _SHA_B, "post_tree_sha256": "a" * 64,
    "changed_member_count": 1, "capability_profile_sha256": "e" * 64,
}
CheckoutPublicationMember.minimal_example = {
    "schema_version": CheckoutPublicationMember.schema_id,
    "identity": deepcopy(_PUBLICATION_IDENTITY_EXAMPLE), "ordinal": 0,
    "canonical_path": "output/brief.md",
    "temporary_basename": ".briefloop-pub-v1-" + "d" * 64 + "-00000000-tmp",
    "claim_basename": ".briefloop-pub-v1-" + "d" * 64 + "-00000000-claim",
    "pre_kind": "absent", "pre_sha256": None, "pre_size": None,
    "post_kind": "blob", "post_sha256": _SHA_A, "post_size": 4,
}
CheckoutPublicationAck.minimal_example = {
    "schema_version": CheckoutPublicationAck.schema_id,
    "identity": deepcopy(_PUBLICATION_IDENTITY_EXAMPLE), "ordinal": 0,
    "publication_identity_sha256": "d" * 64,
    "capability_profile_sha256": "e" * 64,
    "post_kind": "blob", "post_sha256": _SHA_A, "post_size": 4,
    "verification": "post_verified_durable", "cleanup_policy": "retain_residue_v1",
    "appended_at": _NOW,
}
CheckoutPublicationCleanupObservation.minimal_example = {
    "schema_version": CheckoutPublicationCleanupObservation.schema_id,
    "cleanup_observation_id": "f" * 64,
    "identity": deepcopy(_PUBLICATION_IDENTITY_EXAMPLE), "ordinal": 0,
    "auxiliary_role": "temp",
    "reason_code": "checkout_projection_cleanup_retained",
    "expected_kind": "blob", "expected_sha256": _SHA_A, "expected_size": 4,
    "observed_kind": "blob", "observed_sha256": _SHA_A, "observed_size": 4,
    "appended_at": _NOW,
}
for _model in (
    CheckoutRevisionRecord, CheckoutRevisionMember, ReceiptCheckoutBinding,
    PublicationIdentityV1, CheckoutPublicationIntent,
    CheckoutPublicationMember, CheckoutPublicationAck,
    CheckoutPublicationCleanupObservation,
):
    _model.full_example = deepcopy(_model.minimal_example)


V2_CONTRACT_MODELS: tuple[type[StrictModel], ...] = (
    SourceProposal,
    SourceCommitRequest,
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
    RuntimeAdapterBinding,
    RuntimeSourceRouteBinding,
    RuntimeSourcePlanBinding,
    CoreRunNextAction,
    CoreRunInitializeRequest,
    RunContractBinding,
    InvocationStartRequest,
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
    DeliveryResultObservation,
    RepairStartRequest,
    ArtifactSupersedeRequest,
    ArtifactRevertRequest,
    RepairCompleteRequest,
    RecoveryCompleteRequest,
    RunResetRequest,
    FinalizeRenderRequest,
    FinalizeCompleteRequest,
    InternalApprovalRequest,
    DeliveryAuthorizationRequest,
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
    "EventEnvelope",
    "GATE_ID_VALUES",
    "GateArtifactBinding",
    "GateCheckRequest",
    "GateEvaluationRecord",
    "GateFindingRecord",
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
    "LEGACY_READ_ONLY_CONTRACTS",
    "MimeType",
    "OwnedArtifactSubmissionRecord",
    "OwnedArtifactSubmitRequest",
    "ProposalSourceBinding",
    "PackageArtifactBinding",
    "PackageArtifactBindingReference",
    "PackageReadyRecord",
    "PackageReadyReference",
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
    "RunDirection",
    "RuntimeAdapterBinding",
    "RuntimeSourcePlanBinding",
    "RuntimeSourceRouteBinding",
    "RunIdentity",
    "RunIntegrityRecord",
    "RunHeadTransitionRecord",
    "RunHeadTransitionReference",
    "RunResetRequest",
    "ScreenedCandidatesProposal",
    "SOURCE_ACQUISITION_METHODS",
    "SOURCE_ELIGIBILITY_REASONS",
    "SOURCE_MATERIAL_KINDS",
    "SOURCE_ORIGIN_TYPES",
    "SourceCommitRequest",
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
    "read_contract_payload",
]
