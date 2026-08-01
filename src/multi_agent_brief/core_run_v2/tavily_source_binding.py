"""One complete deterministic Tavily source projection for every consumer."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from multi_agent_brief.contracts.v2 import (
    AcceptedSourceRecord,
    ExecutionSourceManifest,
    ExecutionSourceManifestMember,
    SourcePackCommitMember,
    SourcePackCommitRequest,
    SourceProposal,
)
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.sources.tavily_acquisition import (
    TavilyAcquisitionObservation,
)

from .policy import derived_id


@dataclass(frozen=True)
class ExpectedTavilySource:
    proposal: SourceProposal
    content: bytes
    raw_payload: bytes
    invocation_id: str


@dataclass(frozen=True)
class ExpectedTavilySourcePack:
    manifest: ExecutionSourceManifest
    manifest_sha256: str
    sources: tuple[ExpectedTavilySource, ...]

    @property
    def proposals(self) -> tuple[SourceProposal, ...]:
        return tuple(item.proposal for item in self.sources)

    @property
    def contents(self) -> tuple[bytes, ...]:
        return tuple(item.content for item in self.sources)

    @property
    def raw_payloads(self) -> tuple[bytes, ...]:
        return tuple(item.raw_payload for item in self.sources)


@dataclass(frozen=True)
class ExpectedTavilyIntakeSubmission:
    request: SourcePackCommitRequest
    request_fingerprint: str


def _provider_item_id(url: str, search_title: str) -> str:
    digest = hashlib.sha1(f"{url}|{search_title}".encode("utf-8")).hexdigest()
    return f"WS_{digest[:10].upper()}"


def expected_tavily_source_pack(
    observation: TavilyAcquisitionObservation,
    *,
    run_id: str,
    invocation_id: str,
    route_fingerprint: str,
    retrieved_at: str,
) -> ExpectedTavilySourcePack:
    """Derive complete proposal, manifest, content, and raw identities."""

    if (
        observation.bundle.status
        not in {
            "extract_results_partial",
            "extract_results_succeeded",
        }
        or not observation.sources
    ):
        raise ValueError("Tavily observation has no committable Extract source")

    base: list[ExpectedTavilySource] = []
    for observed in observation.sources:
        provider_item_id = _provider_item_id(observed.url, observed.search_title)
        source_id = derived_id(
            "SRC-HOST",
            route_fingerprint,
            provider_item_id,
            sha256_hex(observed.content),
        )
        proposal = SourceProposal.model_validate(
            {
                "schema_version": SourceProposal.schema_id,
                "proposal_id": derived_id(
                    "PROP-SOURCE-HOST",
                    invocation_id,
                    source_id,
                ),
                "run_id": run_id,
                "source_id": source_id,
                "origin_type": "provider_response",
                "acquisition_method": "provider_extract",
                "material_kind": "partial_extract",
                "provider": "tavily",
                "locator": {"kind": "web", "url": observed.url},
                "title": observed.title,
                "publisher": observed.publisher,
                "published_at": observed.published_at,
                "retrieved_at": retrieved_at,
                "source_category": "news_media",
                "retrieval_source_type": "news_media",
                "underlying_evidence_type": "media_report",
                "raw_underlying_evidence_type": "provider-response",
                "content_sha256": sha256_hex(observed.content),
                "content_media_type": "text/plain",
                "raw_payload_sha256": sha256_hex(observed.projection),
                "raw_payload_media_type": "application/json",
                "source_manifest_sha256": None,
                "manifest_local_file": None,
                "document_kind": None,
                "opened_at": None,
                "resolved_at": None,
            },
            strict=True,
        )
        base.append(
            ExpectedTavilySource(
                proposal=proposal,
                content=observed.content,
                raw_payload=observed.projection,
                invocation_id=invocation_id,
            )
        )
    ordered = sorted(base, key=lambda item: item.proposal.source_id)
    members = [
        ExecutionSourceManifestMember.model_validate(
            {
                "source_id": item.proposal.source_id,
                "input_path": (f"input/discovered/{item.proposal.source_id}.txt"),
                "content_sha256": item.proposal.content_sha256,
                "content_media_type": item.proposal.content_media_type,
                "origin_type": item.proposal.origin_type,
                "acquisition_method": item.proposal.acquisition_method,
                "material_kind": item.proposal.material_kind,
                "provider": item.proposal.provider,
                "locator": item.proposal.locator.model_dump(mode="json"),
                "title": item.proposal.title,
                "publisher": item.proposal.publisher,
                "published_at": item.proposal.published_at,
                "retrieved_at": item.proposal.retrieved_at,
                "source_category": item.proposal.source_category,
                "retrieval_source_type": item.proposal.retrieval_source_type,
                "underlying_evidence_type": (item.proposal.underlying_evidence_type),
                "raw_underlying_evidence_type": (
                    item.proposal.raw_underlying_evidence_type
                ),
                "document_kind": None,
                "opened_at": None,
                "resolved_at": None,
            },
            strict=True,
        )
        for item in ordered
    ]
    manifest = ExecutionSourceManifest.model_validate(
        {
            "schema_version": ExecutionSourceManifest.schema_id,
            "members": [
                item.model_dump(mode="json", exclude_unset=False) for item in members
            ],
        },
        strict=True,
    )
    manifest_sha256 = sha256_hex(
        canonical_json_bytes(manifest.model_dump(mode="json", exclude_unset=False))
    )
    by_source_id = {item.proposal.source_id: item for item in ordered}
    rebound: list[ExpectedTavilySource] = []
    for member in manifest.members:
        item = by_source_id[member.source_id]
        rebound.append(
            ExpectedTavilySource(
                proposal=SourceProposal.model_validate(
                    {
                        **item.proposal.model_dump(
                            mode="json",
                            exclude_unset=False,
                        ),
                        "source_manifest_sha256": manifest_sha256,
                        "manifest_local_file": member.input_path,
                    },
                    strict=True,
                ),
                content=item.content,
                raw_payload=item.raw_payload,
                invocation_id=item.invocation_id,
            )
        )
    return ExpectedTavilySourcePack(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        sources=tuple(rebound),
    )


def expected_tavily_intake_submission(
    pack: ExpectedTavilySourcePack,
    *,
    request_id: str,
    run_id: str,
    invocation_id: str,
    expected_store_revision: int,
    provider_response: bytes,
    attempt_authorization_id: str,
    attempt_ordinal: int,
    provider_request_fingerprint: str,
) -> ExpectedTavilyIntakeSubmission:
    """Rebuild the sole Intake request identity retained by the Store."""

    members = tuple(
        SourcePackCommitMember.model_validate(
            {
                "member_id": item.proposal.source_id,
                "proposal_path": (
                    f"scratch/{invocation_id}/sources/"
                    f"{item.proposal.source_id}/source_proposal.json"
                ),
                "content_path": (
                    f"scratch/{invocation_id}/sources/"
                    f"{item.proposal.source_id}/source_content.bin"
                ),
                "raw_payload_path": (
                    f"scratch/{invocation_id}/sources/"
                    f"{item.proposal.source_id}/source_raw.json"
                ),
            },
            strict=True,
        )
        for item in pack.sources
    )
    request = SourcePackCommitRequest.model_validate(
        {
            "schema_version": SourcePackCommitRequest.schema_id,
            "request_id": request_id,
            "run_id": run_id,
            "invocation_id": invocation_id,
            "members": [
                item.model_dump(mode="json", exclude_unset=False) for item in members
            ],
            "manifest_path": f"scratch/{invocation_id}/source_manifest.json",
            "expected_manifest_sha256": pack.manifest_sha256,
            "expected_store_revision": expected_store_revision,
        },
        strict=True,
    )
    request_fingerprint = canonical_fingerprint(
        {
            "lane": "discovery_source_pack",
            "request": request.model_dump(mode="json", exclude_unset=False),
            "manifest_sha256": pack.manifest_sha256,
            "provider_response_sha256": sha256_hex(provider_response),
            "attempt_authorization_id": attempt_authorization_id,
            "attempt_ordinal": attempt_ordinal,
            "provider_request_fingerprint": provider_request_fingerprint,
            "members": [
                {
                    "member_id": item.proposal.source_id,
                    "proposal_sha256": sha256_hex(
                        canonical_json_bytes(
                            item.proposal.model_dump(
                                mode="json",
                                exclude_unset=False,
                            )
                        )
                    ),
                    "content_sha256": item.proposal.content_sha256,
                    "raw_payload_sha256": item.proposal.raw_payload_sha256,
                }
                for item in pack.sources
            ],
        }
    )
    return ExpectedTavilyIntakeSubmission(
        request=request,
        request_fingerprint=request_fingerprint,
    )


def accepted_source_matches_expected(
    source: Any,
    expected: ExpectedTavilySource,
    *,
    accepted_transaction_id: str,
    request_fingerprint: str,
    created_at: str,
) -> bool:
    """Rebuild and compare the complete accepted-source Store record."""

    proposal = expected.proposal
    raw_digest = proposal.raw_payload_sha256
    if raw_digest is None:
        return False
    artifact_digest = hashlib.sha256(
        f"{proposal.run_id}\0{proposal.source_id}".encode("utf-8")
    ).hexdigest()[:32]
    content_artifact_id = f"SRC-CONTENT-{artifact_digest}"
    raw_artifact_id = f"SRC-RAW-{artifact_digest}"
    content_blob_path = (
        "briefloop.db.blobs/sha256/"
        f"{proposal.content_sha256[:2]}/{proposal.content_sha256}"
    )
    raw_blob_path = f"briefloop.db.blobs/sha256/{raw_digest[:2]}/{raw_digest}"
    event_id = derived_id(
        "EVT-SOURCE-PACK",
        accepted_transaction_id,
        request_fingerprint,
        proposal.source_id,
    )
    try:
        expected_record = AcceptedSourceRecord.model_validate(
            {
                "schema_version": AcceptedSourceRecord.schema_id,
                "source_id": proposal.source_id,
                "run_id": proposal.run_id,
                "origin_type": proposal.origin_type,
                "acquisition_method": proposal.acquisition_method,
                "material_kind": proposal.material_kind,
                "provider": proposal.provider,
                "locator": proposal.locator.model_dump(mode="json"),
                "title": proposal.title,
                "publisher": proposal.publisher,
                "published_at": proposal.published_at,
                "retrieved_at": proposal.retrieved_at,
                "source_category": proposal.source_category,
                "retrieval_source_type": proposal.retrieval_source_type,
                "underlying_evidence_type": proposal.underlying_evidence_type,
                "raw_underlying_evidence_type": (proposal.raw_underlying_evidence_type),
                "content_sha256": proposal.content_sha256,
                "content_size_bytes": len(expected.content),
                "content_media_type": proposal.content_media_type,
                "content_blob_path": content_blob_path,
                "content_artifact_id": content_artifact_id,
                "content_artifact_revision": 1,
                "raw_payload_sha256": raw_digest,
                "raw_payload_size_bytes": len(expected.raw_payload),
                "raw_payload_media_type": proposal.raw_payload_media_type,
                "raw_payload_blob_path": raw_blob_path,
                "raw_payload_artifact_id": raw_artifact_id,
                "raw_payload_artifact_revision": 1,
                "source_manifest_sha256": proposal.source_manifest_sha256,
                "manifest_local_file": proposal.manifest_local_file,
                "document_kind": proposal.document_kind,
                "opened_at": proposal.opened_at,
                "resolved_at": proposal.resolved_at,
                "claims_eligible": True,
                "eligibility_reason": "eligible_durable_source_content",
                "invocation_id": expected.invocation_id,
                "acquisition_event_id": event_id,
                "accepted_transaction_id": accepted_transaction_id,
                "request_fingerprint": request_fingerprint,
                "created_at": created_at,
            },
            strict=True,
        )
    except Exception:
        return False
    return source == expected_record


__all__ = [
    "ExpectedTavilySource",
    "ExpectedTavilySourcePack",
    "ExpectedTavilyIntakeSubmission",
    "accepted_source_matches_expected",
    "expected_tavily_intake_submission",
    "expected_tavily_source_pack",
]
