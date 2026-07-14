"""Strict, versioned v2 proposal and control DTO contracts.

These models define input shape only.  They do not write runtime state, decide
stage legality, establish source truth, or replace any current v1 authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
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
    WithJsonSchema,
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
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
RuntimeName = Literal[VALID_RUNTIMES]


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


class SourceProposal(StrictModel):
    schema_id = "briefloop.source_proposal.v2"

    schema_version: Literal["briefloop.source_proposal.v2"]
    proposal_id: ContractId
    run_id: ContractId
    source_id: ContractId
    title: CleanText
    locator: SourceLocator
    retrieved_at: IsoDateTime
    published_at: Optional[IsoDate] = None
    content_sha256: Optional[Sha256] = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


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
    expected_revision: NonNegativeInt

    @model_validator(mode="after")
    def scratch_input_matches_invocation_and_artifact(self) -> "ArtifactSubmitRequest":
        path = PurePosixPath(self.input_path)
        expected_parent = PurePosixPath("scratch") / self.invocation_id
        expected_names = {f"{self.artifact_id}.json", f"{self.artifact_id}.md"}
        if path.parent != expected_parent or path.name not in expected_names:
            raise ValueError(
                "artifact submission input must use its invocation scratch path"
            )
        return self


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
    format: Literal["json", "yaml", "markdown", "html", "docx", "pdf"]


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
    event_type: ContractId
    created_at: IsoDateTime
    actor: Literal["cli", "orchestrator", "runtime", "system"]
    transaction_id: Optional[ContractId] = None
    stage_id: Optional[ContractId] = None
    artifact_id: Optional[ContractId] = None
    decision: Optional[ContractId] = None
    reason: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


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
    reason: CleanText
    actor_id: ContractId
    recorded_at: IsoDateTime
    boundary: Literal[
        "internal_review_approval_records_only_not_public_release_authorization"
    ]
    event_id: ContractId


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
        return self


_RUN = "RUN-20260714-001"
_NOW = "2026-07-14T09:00:00Z"
_SHA_A = "a" * 64

SourceProposal.minimal_example = {
    "schema_version": SourceProposal.schema_id,
    "proposal_id": "PROP-SOURCE-001",
    "run_id": _RUN,
    "source_id": "SRC-001",
    "title": "Public source",
    "locator": {"kind": "web", "url": "https://example.com/report"},
    "retrieved_at": _NOW,
}
SourceProposal.full_example = {
    **SourceProposal.minimal_example,
    "published_at": "2026-07-13",
    "content_sha256": _SHA_A,
    "metadata": {"language": "en"},
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
    "artifact_id": "audited_brief",
    "invocation_id": "INV-EDITOR-001",
    "input_path": "scratch/INV-EDITOR-001/audited_brief.md",
    "expected_revision": 0,
}
ArtifactSubmitRequest.full_example = {
    **ArtifactSubmitRequest.minimal_example,
    "expected_revision": 1,
}

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
}


V2_CONTRACT_MODELS: tuple[type[StrictModel], ...] = (
    SourceProposal,
    CandidateClaimsProposal,
    ScreenedCandidatesProposal,
    ClaimDraftsProposal,
    AuditProposal,
    ArtifactSubmitRequest,
    RunIdentity,
    StageState,
    ArtifactRecord,
    ArtifactRevision,
    EventEnvelope,
    Invocation,
    Approval,
    Delivery,
    TransactionReceipt,
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
    "Approval",
    "ArtifactRecord",
    "ArtifactRevision",
    "ArtifactSubmitRequest",
    "AuditProposal",
    "CandidateClaimsProposal",
    "ClaimDraftsProposal",
    "ContractReadResult",
    "Delivery",
    "EventEnvelope",
    "Invocation",
    "LEGACY_READ_ONLY_CONTRACTS",
    "RunIdentity",
    "ScreenedCandidatesProposal",
    "SourceProposal",
    "StageState",
    "StrictModel",
    "TransactionReceipt",
    "V2_CONTRACT_IDS",
    "V2_CONTRACT_MODELS",
    "read_contract_payload",
]
