"""Focused M3 authorized runtime-continuation State x Path rows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from tests.test_runtime_host_codex_v2 import (
    _advance_to_source_route,
    _specialist_workspace,
)

from multi_agent_brief.cli.main import main
from multi_agent_brief.contracts import SchemaRegistry
from multi_agent_brief.contracts.v2 import (
    CoreRunNextAction,
    IntegrityCheckRequest,
    SourceCommitRequest,
    SourceProposal,
)
from multi_agent_brief.control_store import (
    ControlStoreIntegrityError,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.errors import ControlStoreCommitOutcomeUnknown
from multi_agent_brief.control_store.sqlite_store import ControlStoreHistory
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.core_run_v2 import artifacts as artifact_service
from multi_agent_brief.core_run_v2.errors import CoreRunError, CoreRunResult
from multi_agent_brief.core_run_v2.integrity import (
    RunIntegrityService,
    protected_revision_keys,
    workspace_observation_revision_keys,
)
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.product.init_web.submit import InitWebSubmitter
from multi_agent_brief.product.projection_platform import (
    supports_retained_directory_publication,
)
from multi_agent_brief.runtime_host_v2.codex import workspace_codex_adapter_loader
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.initialization import (
    initialize_or_open_runtime,
)
from multi_agent_brief.runtime_host_v2 import service as host_service
from multi_agent_brief.runtime_host_v2.service import RuntimeHostService
from multi_agent_brief.runtime_host_v2.submission import source_stage_root
from multi_agent_brief.sources.base import SourceItem
from multi_agent_brief.sources.search_backends.tavily import TavilyBackend
from multi_agent_brief.sources.web_search import (
    WebSearchCollection,
    WebSearchProvider,
)


_REQUIRES_RETAINED_PUBLICATION = pytest.mark.skipif(
    not supports_retained_directory_publication(),
    reason="discovery promotion requires retained-directory publication",
)


def _body(*, authorized: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "workspace_target": "workspace",
        "selections": {
            "company": "ExampleCo",
            "industry_or_theme": "manufacturing",
            "task_objective": "Prepare a public-safe manufacturing brief.",
            "brief_title": "ExampleCo brief",
            "audience": "management",
            "interface_language": "en",
            "output_language": "en",
            "cadence": "weekly",
            "max_source_age_days": 7,
            "focus_areas": ["operations"],
            "output_formats": ["markdown"],
            "forbidden_sources": [],
            "web_search_mode": "disabled",
            "output_extent": "balanced",
        },
        "human_confirmation": True,
    }
    return {
        "schema_version": "briefloop.init_web.submission.v1",
        "request_id": "REQ-CONTINUE-001" if authorized else "REQ-MANUAL-001",
        "payload": payload,
    }


def _authorized_workspace(tmp_path: Path) -> Path:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    content = b"public durable source\n"
    staged = submitter.stage_upload(
        session_id="init-session",
        filename="source.txt",
        stream=BytesIO(content),
        declared_length=len(content),
    )
    body = _body(authorized=True)
    payload = body["payload"]
    assert isinstance(payload, dict)
    metadata = {
        "source_id": "SRC-INIT-001",
        "expected_content_sha256": staged["sha256"],
        "origin_type": "uploaded_file",
        "acquisition_method": "manual_upload",
        "material_kind": "uploaded_file",
        "provider": None,
        "original_url": None,
        "title": "Public durable source",
        "publisher": "Example publisher",
        "published_at": (date.today() - timedelta(days=1)).isoformat(),
        "retrieved_at": "2026-07-23T00:00:00Z",
        "source_category": "other",
        "retrieval_source_type": "local_file",
        "underlying_evidence_type": "unknown",
        "raw_underlying_evidence_type": None,
        "document_kind": None,
        "opened_at": None,
        "resolved_at": None,
    }
    bindings = [{"metadata_index": 0, "upload_handle": staged["upload_handle"]}]
    preview = submitter.preview_source_manifest(
        session_id="init-session",
        body={
            "source_manifest_mode": "imported",
            "source_metadata": [metadata],
            "upload_bindings": bindings,
        },
    )
    payload.update(
        {
            "completion_target": "finalized_local",
            "repair_budget": 1,
            "source_manifest_mode": "imported",
            "source_metadata": preview["source_metadata"],
            "source_manifest": preview["source_manifest"],
            "upload_session_id": "init-session",
            "upload_bindings": preview["routing_bindings"],
        }
    )
    status, response = submitter.submit(body)
    assert status == 200 and response["status"] == "committed"
    return tmp_path / "workspace"


def _discovery_workspace(tmp_path: Path, *, with_secret: bool = True) -> Path:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body(authorized=False)
    body["request_id"] = "REQ-DISCOVERY-001"
    payload = body["payload"]
    assert isinstance(payload, dict)
    selections = payload["selections"]
    assert isinstance(selections, dict)
    selections.update(
        {
            "source_profile": "llm_decide",
            "web_search_mode": "external_api",
            "search_backend": "tavily",
        }
    )
    payload["search_secret_session_id"] = "runtime-discovery-session"
    payload["completion_target"] = "finalized_local"
    payload["repair_budget"] = 1
    if with_secret:
        configured = submitter.configure_search_secret(
            session_id="runtime-discovery-session",
            body={
                "provider": "tavily",
                "api_key": "tvly-runtime-secret-sentinel",
            },
        )
        assert configured["configured"] is True
    status, response = submitter.submit(body)
    if with_secret:
        assert status == 200 and response["status"] == "committed"
    else:
        assert status == 400
        raise AssertionError("missing-secret workspace must be constructed separately")
    return tmp_path / "workspace"


def _revision(workspace: Path) -> int:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        return store.load_snapshot(head.current_run_id).store_revision


def _service(workspace: Path) -> RuntimeHostService:
    return RuntimeHostService(
        workspace,
        adapter_loader=workspace_codex_adapter_loader(workspace),
    )


def _advance_discovery_to_source_action(workspace: Path) -> CoreRunNextAction:
    service = _service(workspace)
    planner = service.continue_authorized()
    assert planner.status == "role_work_required"
    assert planner.trace.envelope_path is not None
    envelope_path = workspace / planner.trace.envelope_path
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    (envelope_path.parent / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: web-search\n",
        encoding="utf-8",
    )
    accepted = service.accept_invocation(envelope["invocation_id"])
    assert accepted.status == "committed"
    action = service.next_action()
    assert action.effect_kind == "source_acquire"
    return action


def _discovery_stage_identity(
    workspace: Path,
    action: CoreRunNextAction,
) -> str:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        discovery = store.load_snapshot(
            head.current_run_id
        ).run_source_discovery_authorizations[0]
    return canonical_fingerprint(
        {
            "kind": "discovery_source_pack",
            "run_id": action.run_id,
            "action_fingerprint": action.action_fingerprint,
            "discovery_authorization_id": discovery.authorization_id,
        }
    )


def _transferred_discovery_source_action(workspace: Path) -> CoreRunNextAction:
    """Build one verified source-acquire Store fixture without publication."""

    from datetime import datetime, timezone

    from multi_agent_brief.contracts.v2 import (
        ArtifactRecord,
        ArtifactRevision,
        CoreRunEventBinding,
        OwnedArtifactSubmissionRecord,
        OwnedArtifactSubmitRequest,
        PublicationIdentityV1,
        ReceiptCheckoutBinding,
    )
    from multi_agent_brief.control_store.serialization import (
        canonical_json_bytes,
        sha256_hex,
    )
    from multi_agent_brief.core_run_v2 import artifacts as artifact_service
    from multi_agent_brief.core_run_v2 import checkout as checkout_service
    from multi_agent_brief.core_run_v2.policy import (
        derived_id,
        transaction_type_for,
    )
    from multi_agent_brief.core_run_v2.publication_platform import (
        CapabilityProfile,
    )
    from multi_agent_brief.runtime_host_v2 import service as host_service
    from multi_agent_brief.runtime_host_v2.scratch import read_role_outputs
    from multi_agent_brief.runtime_host_v2.source_routes import (
        _material_from_item,
    )
    from multi_agent_brief.runtime_host_v2.submission import (
        SourceStageBytesInput,
        stage_source_pack_bytes,
    )

    service = _service(workspace)
    planner = service.continue_authorized()
    assert planner.status == "role_work_required"
    assert planner.trace.envelope_path is not None
    proposal_path = (
        workspace / planner.trace.envelope_path
    ).parent / "source_candidates.yaml"
    proposal_path.write_text(
        "version: 1\ncandidates:\n  - route: web-search\n",
        encoding="utf-8",
    )
    current = initialize_or_open_runtime(
        workspace,
        adapter_loader=workspace_codex_adapter_loader(workspace),
    )
    active = [
        item
        for item in current.verified.snapshot.invocations
        if item.status == "active"
    ]
    assert len(active) == 1
    envelope = service._expected_invocation_envelope(
        active[0].invocation_id,
        current=current,
    )
    spec = host_service._ROLE_OUTPUTS["source-planner"]
    outputs = read_role_outputs(workspace, envelope)
    request, lane = service._derive_acceptance_request(envelope, spec, outputs)
    assert isinstance(request, OwnedArtifactSubmitRequest)
    assert lane is None
    content = outputs["source_candidates.yaml"]
    snapshot = current.verified.snapshot
    artifact = next(
        item for item in snapshot.artifacts if item.artifact_id == "source_candidates"
    )
    assert artifact.current_revision == 0
    now_value = datetime.now(timezone.utc)
    now = now_value.isoformat().replace("+00:00", "Z")
    digest = sha256_hex(content)
    fingerprint = canonical_fingerprint(
        request.model_dump(mode="json", exclude_unset=False)
    )
    event_id = derived_id("EVT-ARTIFACT", request.request_id, fingerprint)
    submission_id = derived_id("SUBMISSION", request.request_id, digest)
    updated = ArtifactRecord.model_validate(
        {
            **artifact.model_dump(mode="json", exclude_unset=False),
            "current_revision": 1,
            "status": "valid",
        },
        strict=True,
    )
    revision = ArtifactRevision.model_validate(
        {
            "schema_version": ArtifactRevision.schema_id,
            "run_id": request.run_id,
            "artifact_id": artifact.artifact_id,
            "revision": 1,
            "path": artifact.path,
            "sha256": digest,
            "size_bytes": len(content),
            "frozen": True,
            "producer_kind": "workflow_stage",
            "producer_id": "source-planner",
            "created_at": now,
        },
        strict=True,
    )
    submission = OwnedArtifactSubmissionRecord.model_validate(
        {
            "schema_version": OwnedArtifactSubmissionRecord.schema_id,
            "submission_id": submission_id,
            "run_id": request.run_id,
            "artifact_id": artifact.artifact_id,
            "artifact_revision": 1,
            "artifact_sha256": digest,
            "owner_stage_id": "source-discovery",
            "owner_role_id": "source-planner",
            "run_contract_fingerprint": (current.verified.binding.contract_fingerprint),
            "invocation_id": request.invocation_id,
            "producer_tool_id": request.producer_tool_id,
            "parent_artifact": None,
            "canonical_workspace_path": artifact.path,
            "request_fingerprint": fingerprint,
            "accepted_event_id": event_id,
            "accepted_transaction_id": request.request_id,
            "created_at": now,
        },
        strict=True,
    )
    completed_invocation = artifact_service._completed_invocation(
        active[0],
        now,
    )
    event = artifact_service._event(
        event_id=event_id,
        run_id=request.run_id,
        transaction_id=request.request_id,
        event_type="owned_artifact_accepted",
        stage_id="source-discovery",
        artifact_id=artifact.artifact_id,
        reason="owned artifact accepted",
        created_at=now,
        binding=CoreRunEventBinding(
            request_id=request.request_id,
            request_fingerprint=fingerprint,
            effect_kind="owned_artifact_acceptance",
            primary_record_id=submission_id,
            outcome="committed",
        ),
    )
    pre = checkout_service._current_checkout(snapshot)
    revisions = {
        (item.artifact_id, item.revision): item for item in snapshot.artifact_revisions
    }
    store_resident = checkout_service.store_resident_revision_keys(snapshot)
    selected = [
        revisions[(item.artifact_id, item.current_revision)]
        for item in snapshot.artifacts
        if item.current_revision > 0
        and (
            item.artifact_id,
            item.current_revision,
        )
        not in store_resident
        and not revisions[(item.artifact_id, item.current_revision)].path.startswith(
            "briefloop.db.blobs/"
        )
    ]
    post = checkout_service.build_checkout_revision(
        workspace_id=snapshot.workspace_id,
        run_id=request.run_id,
        transaction_id=request.request_id,
        created_at=now_value,
        artifact_revisions=(*selected, revision),
        parent_checkout_revision_id=(
            None if pre is None else pre.record.checkout_revision_id
        ),
    )
    binding = ReceiptCheckoutBinding.model_validate(
        {
            "schema_version": ReceiptCheckoutBinding.schema_id,
            "workspace_id": snapshot.workspace_id,
            "run_id": request.run_id,
            "transaction_id": request.request_id,
            "pre_run_id": request.run_id,
            "pre_checkout_revision_id": (
                None if pre is None else pre.record.checkout_revision_id
            ),
            "post_run_id": request.run_id,
            "post_checkout_revision_id": post.record.checkout_revision_id,
        },
        strict=True,
    )
    identity = PublicationIdentityV1.model_validate(
        {
            "schema_version": "briefloop-publication-identity/v1",
            "workspace_id": snapshot.workspace_id,
            "run_id": request.run_id,
            "transaction_id": request.request_id,
            "checkout_revision_id": post.record.checkout_revision_id,
        },
        strict=True,
    )
    transferred_profile = CapabilityProfile(
        platform="darwin",
        filesystem="apfs",
        namespace_primitive="renameatx_np(RENAME_EXCL)",
        temp_durability="F_FULLFSYNC",
        canonical_post_durability="F_FULLFSYNC",
        parent_durability="fsync",
        canonical_open_flags="O_RDWR|O_NOFOLLOW|O_CLOEXEC",
    )
    intent, members = checkout_service.build_publication_intent(
        identity=identity,
        pre=pre,
        post=post,
        capability_profile_sha256=transferred_profile.sha256,
    )
    prepared = checkout_service.PreparedCheckoutEffect(
        pre=pre,
        post=post,
        binding=binding,
        identity=identity,
        intent=intent,
        publication_members=members,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        unit = store.begin(
            request.run_id,
            request.request_id,
            transaction_type_for("owned_artifact_acceptance"),
            request.expected_store_revision,
        )
        unit.put_invocation(completed_invocation)
        unit.put_artifact(updated)
        unit.put_artifact_revision(revision, content)
        unit.put_owned_artifact_submission(submission)
        unit.append_event(event)
        checkout_service.stage_checkout_effect(unit, prepared)
        unit.commit(
            _postcommit_observer=lambda _receipt: CoreRunDomainVerifier().verify(
                store, request.run_id
            )
        )
        verified = CoreRunDomainVerifier().verify(store, request.run_id)
    assert not (workspace / "source_candidates.yaml").exists()
    action = service.next_action()
    assert action.effect_kind == "source_acquire"
    assert verified.snapshot.run_execution_authorizations == ()
    assert verified.snapshot.sources == ()
    assert len(verified.snapshot.run_source_discovery_authorizations) == 1
    discovery = verified.snapshot.run_source_discovery_authorizations[0]
    route = next(
        item
        for item in verified.source_plan.routes
        if item.route_id == action.source_route_id
    )
    invocation_request_id = derived_id(
        "REQ-HOST-INVOKE",
        action.run_id,
        action.action_fingerprint,
    )
    current = initialize_or_open_runtime(
        workspace,
        adapter_loader=workspace_codex_adapter_loader(workspace),
    )
    _, invocation_id = service._planned_invocation(
        current,
        action,
        request_id=invocation_request_id,
    )
    material = _material_from_item(
        workspace=workspace,
        run_id=action.run_id,
        invocation_id=invocation_id,
        route=route,
        item=_tavily_item(durable=True),
    )
    _manifest, proposals, ordered_materials = service._freeze_discovery_source_manifest(
        (material,)
    )
    stage_identity = canonical_fingerprint(
        {
            "kind": "discovery_source_pack",
            "run_id": action.run_id,
            "action_fingerprint": action.action_fingerprint,
            "discovery_authorization_id": discovery.authorization_id,
        }
    )
    stage_fingerprint = canonical_fingerprint(
        {
            "action": action.model_dump(mode="json", exclude_unset=False),
            "route_fingerprint": route.route_fingerprint,
            "discovery_request_fingerprint": discovery.request_fingerprint,
        }
    )
    stage_source_pack_bytes(
        workspace,
        stage_identity=stage_identity,
        request_fingerprint=stage_fingerprint,
        members=tuple(
            SourceStageBytesInput(
                member_id=proposal.source_id,
                proposal_bytes=canonical_json_bytes(
                    proposal.model_dump(mode="json", exclude_unset=False)
                ),
                content_bytes=source.content,
                raw_payload_bytes=source.raw_payload,
            )
            for proposal, source in zip(
                proposals,
                ordered_materials,
                strict=True,
            )
        ),
    )
    return action


def _tavily_item(*, durable: bool) -> SourceItem:
    return SourceItem(
        source_id="durable" if durable else "snippet",
        source_name="Publisher",
        source_type="web_search",
        title="Durable source" if durable else "Snippet result",
        content=("durable provider content" if durable else "discovery snippet only"),
        url=(
            "https://example.com/durable" if durable else "https://example.com/snippet"
        ),
        retrieved_at="2026-07-26T00:00:00Z",
        metadata={
            "backend": "tavily",
            "content_shape": ("provider_raw_content" if durable else "search_snippet"),
            "has_raw_content": durable,
            "evidence_quality": "partial_extract" if durable else "snippet",
        },
    )


def _tavily_collection(
    items: list[SourceItem],
) -> WebSearchCollection:
    projections = [
        {
            "title": item.title,
            "url": item.url,
            "snippet": (
                "discovery snippet"
                if item.metadata.get("has_raw_content") is True
                else item.content
            ),
            "raw_content": (
                item.content if item.metadata.get("has_raw_content") is True else None
            ),
            "published_date": item.published_at or "",
            "score": 0.9,
        }
        for item in items
    ]
    normalized = [
        replace(
            item,
            metadata={
                **item.metadata,
                "provider_projection": projection,
            },
        )
        for item, projection in zip(items, projections, strict=True)
    ]
    response_rows = [
        {
            "title": projection["title"],
            "url": projection["url"],
            "content": projection["snippet"],
            "raw_content": projection["raw_content"],
            "published_date": projection["published_date"],
            "score": projection["score"],
        }
        for projection in projections
    ]
    return WebSearchCollection(
        items=tuple(normalized),
        raw_response=json.dumps(
            {"results": response_rows},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        status_code=200,
    )


def _public_source_request(
    workspace: Path,
    *,
    run_id: str,
    invocation_id: str,
    entrypoint: str,
) -> str:
    scratch = workspace / "scratch" / invocation_id
    scratch.mkdir(parents=True, exist_ok=True)
    if entrypoint == "source":
        payload: dict[str, object] = {
            "schema_version": "briefloop.source_commit_request.v2",
            "request_id": "REQ-PUBLIC-SOURCE-001",
            "run_id": run_id,
            "invocation_id": invocation_id,
            "proposal_path": f"scratch/{invocation_id}/source_proposal.json",
            "content_path": f"scratch/{invocation_id}/source_content.bin",
            "raw_payload_path": f"scratch/{invocation_id}/source_raw.json",
            "expected_store_revision": _revision(workspace),
        }
    elif entrypoint == "pack":
        payload = {
            "schema_version": "briefloop.source_pack_commit_request.v2",
            "request_id": "REQ-PUBLIC-PACK-001",
            "run_id": run_id,
            "invocation_id": invocation_id,
            "members": [
                {
                    "member_id": "SRC-PUBLIC-001",
                    "proposal_path": (
                        f"scratch/{invocation_id}/sources/SRC-PUBLIC-001/"
                        "source_proposal.json"
                    ),
                    "content_path": (
                        f"scratch/{invocation_id}/sources/SRC-PUBLIC-001/"
                        "source_content.bin"
                    ),
                    "raw_payload_path": (
                        f"scratch/{invocation_id}/sources/SRC-PUBLIC-001/"
                        "source_raw.json"
                    ),
                }
            ],
            "manifest_path": f"scratch/{invocation_id}/source_manifest.json",
            "expected_manifest_sha256": "0" * 64,
            "expected_store_revision": _revision(workspace),
        }
    else:
        raise AssertionError(entrypoint)
    request = scratch / "submit_request.json"
    request.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return request.relative_to(workspace).as_posix()


def test_unauthorized_run_returns_typed_zero_write_attention(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    status, _response = submitter.submit(_body(authorized=False))
    assert status == 200
    workspace = tmp_path / "workspace"
    revision = _revision(workspace)

    result = _service(workspace).continue_authorized()

    assert result.status == "needs_human"
    assert result.reason_code == "runtime_continuation_unsupported"
    assert _revision(workspace) == revision


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_missing_runtime_secret_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    _advance_discovery_to_source_action(workspace)
    (workspace / ".env").unlink()
    revision = _revision(workspace)
    calls = 0

    def collect(_provider, _query, _config):
        nonlocal calls
        calls += 1
        return _tavily_collection([_tavily_item(durable=True)])

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)

    result = _service(workspace).continue_authorized()

    assert result.status == "needs_attention"
    assert result.reason_code == "source_provider_secret_unavailable"
    assert calls == 0
    assert _revision(workspace) == revision


@pytest.mark.parametrize("entrypoint", ["source", "pack"])
@pytest.mark.parametrize("active_reserved_invocation", [False, True])
@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_authority_rejects_public_source_files_before_sibling_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    active_reserved_invocation: bool,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    action = _advance_discovery_to_source_action(workspace)
    host = _service(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        run_id = head.current_run_id
    if active_reserved_invocation:
        current = initialize_or_open_runtime(
            workspace,
            adapter_loader=workspace_codex_adapter_loader(workspace),
        )
        dispatch = host._start_invocation_for_action(
            current,
            action,
            role_id="source-provider",
            request_id=f"REQ-PUBLIC-GUARD-{entrypoint.upper()}",
        )
        invocation_id = dispatch.envelope.invocation_id
        reserved = host.next_action()
        assert reserved.effect_kind == "invocation_accept_or_fail"
        assert reserved.reason_code == "active_invocation_reserved"
    else:
        invocation_id = f"INV-PUBLIC-GUARD-{entrypoint.upper()}"
    request_path = _public_source_request(
        workspace,
        run_id=run_id,
        invocation_id=invocation_id,
        entrypoint=entrypoint,
    )
    intake = IntakeService(workspace)
    opened: list[str] = []
    original_read = intake._reader.read

    def _record_read(path):
        opened.append(str(path))
        return original_read(path)

    monkeypatch.setattr(intake._reader, "read", _record_read)
    before_revision = _revision(workspace)
    database_before = (workspace / "briefloop.db").read_bytes()

    result = (
        intake.submit_source(request_path)
        if entrypoint == "source"
        else intake.submit_source_pack(request_path)
    )

    assert result.status == "failed_uncommitted"
    assert result.error_code == "source_pack_authorization_invalid"
    assert opened == [request_path]
    assert _revision(workspace) == before_revision
    assert (workspace / "briefloop.db").read_bytes() == database_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
    assert len(snapshot.run_source_discovery_authorizations) == 1
    assert snapshot.run_execution_authorizations == ()
    assert snapshot.sources == ()


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_authority_rejects_generic_host_source_bytes(
    tmp_path: Path,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    action = _advance_discovery_to_source_action(workspace)
    current = initialize_or_open_runtime(
        workspace,
        adapter_loader=workspace_codex_adapter_loader(workspace),
    )
    dispatch = _service(workspace)._start_invocation_for_action(
        current,
        action,
        role_id="source-provider",
        request_id="REQ-HOST-GENERIC-SOURCE-GUARD",
    )
    request_relative = _public_source_request(
        workspace,
        run_id=action.run_id,
        invocation_id=dispatch.envelope.invocation_id,
        entrypoint="source",
    )
    request = SourceCommitRequest.model_validate_json(
        (workspace / request_relative).read_bytes(),
        strict=True,
    )
    before_revision = _revision(workspace)

    result = IntakeService(workspace)._submit_source_from_host(
        request,
        proposal_bytes=b"must not be parsed",
        content_bytes=b"must not be committed",
        raw_bytes=b"must not be committed",
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "source_pack_authorization_invalid",
    }
    assert _revision(workspace) == before_revision


@pytest.mark.parametrize("corruption", ["missing_receipt_relation", "cross_run"])
def test_malformed_discovery_graph_rejects_public_source_before_sibling_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        run_id = head.current_run_id
    request_path = _public_source_request(
        workspace,
        run_id=run_id,
        invocation_id="INV-PUBLIC-GUARD-MALFORMED",
        entrypoint="source",
    )
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        if corruption == "missing_receipt_relation":
            connection.execute(
                "DELETE FROM transaction_run_source_discovery_authorizations"
            )
        else:
            row = connection.execute(
                "SELECT run_id, authorization_id, payload_json "
                "FROM run_source_discovery_authorizations"
            ).fetchone()
            assert row is not None
            stored_run_id, authorization_id, payload_json = row
            payload = json.loads(payload_json)
            payload["run_id"] = "RUN-CROSS-BOUNDARY"
            connection.execute(
                "DROP TRIGGER run_source_discovery_authorizations_no_update"
            )
            connection.execute(
                "UPDATE run_source_discovery_authorizations "
                "SET payload_json = ? WHERE run_id = ? AND authorization_id = ?",
                (
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    stored_run_id,
                    authorization_id,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    intake = IntakeService(workspace)
    opened: list[str] = []
    original_read = intake._reader.read

    def _record_read(path):
        opened.append(str(path))
        return original_read(path)

    monkeypatch.setattr(intake._reader, "read", _record_read)
    database_before = (workspace / "briefloop.db").read_bytes()

    result = intake.submit_source(request_path)

    assert result.status == "failed_uncommitted"
    assert result.error_code == "control_store_integrity_invalid"
    assert opened == [request_path]
    assert (workspace / "briefloop.db").read_bytes() == database_before


def test_discovery_action_integrity_precedes_secret_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    revision = _revision(workspace)
    monkeypatch.setattr(
        RuntimeHostService,
        "_prepromotion_action_allowed",
        staticmethod(lambda _action: False),
    )

    def unexpected_secret_access(*_args, **_kwargs):
        raise AssertionError("secret access must follow action integrity")

    monkeypatch.setattr(
        "multi_agent_brief.runtime_host_v2.service.known_env_key_is_set",
        unexpected_secret_access,
    )

    result = _service(workspace).continue_authorized()

    assert result.status == "needs_attention"
    assert result.reason_code == "control_store_integrity_invalid"
    assert _revision(workspace) == revision


@pytest.mark.parametrize(
    ("corruption", "payload_field", "changed_value"),
    (
        ("missing_receipt_relation", None, None),
        ("cross_run_payload", "run_id", "RUN-CROSS-BOUNDARY"),
        (
            "mismatched_route_payload",
            "route_fingerprint",
            "0" * 64,
        ),
    ),
)
def test_discovery_authorization_graph_tampering_fails_closed(
    tmp_path: Path,
    corruption: str,
    payload_field: str | None,
    changed_value: object,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    database = workspace / "briefloop.db"
    connection = sqlite3.connect(database)
    try:
        if corruption == "missing_receipt_relation":
            connection.execute(
                "DELETE FROM transaction_run_source_discovery_authorizations"
            )
        else:
            row = connection.execute(
                "SELECT run_id, authorization_id, payload_json "
                "FROM run_source_discovery_authorizations"
            ).fetchone()
            assert row is not None
            run_id, authorization_id, payload_json = row
            payload = json.loads(payload_json)
            assert payload_field is not None
            payload[payload_field] = changed_value
            connection.executescript(
                """
                DROP TRIGGER run_source_discovery_authorizations_no_update;
                """
            )
            connection.execute(
                "UPDATE run_source_discovery_authorizations "
                "SET payload_json = ? WHERE run_id = ? AND authorization_id = ?",
                (
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    run_id,
                    authorization_id,
                ),
            )
        connection.commit()
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        connection.close()

    with pytest.raises(ControlStoreIntegrityError):
        SQLiteControlStore.open(database)


def test_discovery_authorization_is_unique_per_run_in_schema(
    tmp_path: Path,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        columns = [
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(run_source_discovery_authorizations)"
            )
        ]
        select_columns = [
            "'AUTH-DISCOVERY-DUPLICATE'" if column == "authorization_id" else column
            for column in columns
        ]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO run_source_discovery_authorizations "
                f"({','.join(columns)}) "
                f"SELECT {','.join(select_columns)} "
                "FROM run_source_discovery_authorizations"
            )
        connection.rollback()
    finally:
        connection.close()


def test_discovery_invocation_publication_stop_is_typed_and_retry_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    service = _service(workspace)
    planner = service.continue_authorized()
    assert planner.status == "role_work_required"
    assert planner.trace.envelope_path is not None
    proposal_path = (
        workspace / planner.trace.envelope_path
    ).parent / "source_candidates.yaml"
    proposal_path.write_text(
        "version: 1\ncandidates:\n  - route: web-search\n",
        encoding="utf-8",
    )
    action = service.next_action()
    assert action.effect_kind == "invocation_accept_or_fail"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot_before = store.load_snapshot(head.current_run_id)
    active_before = tuple(
        item for item in snapshot_before.invocations if item.status == "active"
    )
    assert len(active_before) == 1

    def unsupported_acceptance(*_args, **_kwargs):
        raise RuntimeHostError("checkout_publication_unsupported")

    def forbidden_provider(*_args, **_kwargs):
        pytest.fail("provider must not run before proposal acceptance")

    monkeypatch.setattr(
        artifact_service,
        "prepare_checkout_effect",
        unsupported_acceptance,
    )
    monkeypatch.setattr(WebSearchProvider, "collect_with_response", forbidden_provider)

    request_path = (
        workspace / "scratch" / active_before[0].invocation_id / "submit_request.json"
    )
    request_before: bytes | None = None
    request_stat = None
    for _ in range(2):
        stopped = service.continue_authorized()
        assert stopped.status == "needs_attention"
        assert stopped.reason_code == "checkout_publication_unsupported"
        assert stopped.store_revision == snapshot_before.store_revision
        assert stopped.trace.next_action == action
        assert stopped.trace.transaction_ids == []
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            head = store.load_workspace_run_head()
            assert head is not None
            snapshot_after = store.load_snapshot(head.current_run_id)
        assert snapshot_after == snapshot_before
        assert (
            tuple(
                item for item in snapshot_after.invocations if item.status == "active"
            )
            == active_before
        )
        if request_before is None:
            request_before = request_path.read_bytes()
            request_stat = request_path.stat()
        else:
            assert request_path.read_bytes() == request_before
            replay_stat = request_path.stat()
            assert request_stat is not None
            assert (replay_stat.st_dev, replay_stat.st_ino) == (
                request_stat.st_dev,
                request_stat.st_ino,
            )
            assert replay_stat.st_mtime_ns == request_stat.st_mtime_ns

    def unrelated_failure(*_args, **_kwargs):
        raise RuntimeHostError("runtime_proposal_invalid")

    monkeypatch.setattr(
        artifact_service,
        "prepare_checkout_effect",
        unrelated_failure,
    )
    with pytest.raises(RuntimeHostError, match="runtime_proposal_invalid"):
        service.continue_authorized()


@pytest.mark.parametrize(
    "mutation",
    [
        "different",
        "truncated",
        "oversized",
        "symlink",
        "hardlink",
        "directory",
        "unexpected_sibling",
    ],
)
def test_discovery_invocation_rejects_invalid_host_request_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    service = _service(workspace)
    planner = service.continue_authorized()
    assert planner.status == "role_work_required"
    assert planner.trace.envelope_path is not None
    scratch = (workspace / planner.trace.envelope_path).parent
    (scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: web-search\n",
        encoding="utf-8",
    )
    action = service.next_action()
    calls = 0

    def unsupported_acceptance(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeHostError("checkout_publication_unsupported")

    monkeypatch.setattr(
        artifact_service,
        "prepare_checkout_effect",
        unsupported_acceptance,
    )
    stopped = service.continue_authorized()
    assert stopped.status == "needs_attention"
    assert stopped.reason_code == "checkout_publication_unsupported"
    assert calls == 1
    request_path = scratch / "submit_request.json"
    canonical = request_path.read_bytes()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot_before = store.load_snapshot(head.current_run_id)

    if mutation == "different":
        request_path.write_bytes(b"{}")
    elif mutation == "truncated":
        request_path.write_bytes(canonical[:-1])
    elif mutation == "oversized":
        request_path.write_bytes(b"x" * (1024 * 1024 + 1))
    elif mutation == "symlink":
        outside = tmp_path / "outside-request.json"
        outside.write_bytes(canonical)
        request_path.unlink()
        request_path.symlink_to(outside)
    elif mutation == "hardlink":
        outside = tmp_path / "outside-request.json"
        outside.write_bytes(canonical)
        request_path.unlink()
        os.link(outside, request_path)
    elif mutation == "directory":
        request_path.unlink()
        request_path.mkdir()
    else:
        (scratch / "unexpected.json").write_bytes(b"unexpected")

    validation = service.validate_invocation(scratch.name)
    rejected = service.continue_authorized()

    assert validation.status == "invalid"
    assert validation.reason_code == "runtime_scratch_invalid"
    assert rejected.status == "proposal_invalid"
    assert rejected.reason_code == "runtime_proposal_invalid"
    assert rejected.trace.next_action == action
    assert rejected.trace.transaction_ids == []
    assert calls == 1
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        assert store.load_snapshot(head.current_run_id) == snapshot_before


def test_discovery_invocation_recreates_missing_canonical_host_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    service = _service(workspace)
    planner = service.continue_authorized()
    assert planner.status == "role_work_required"
    assert planner.trace.envelope_path is not None
    scratch = (workspace / planner.trace.envelope_path).parent
    (scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: web-search\n",
        encoding="utf-8",
    )
    calls = 0

    def unsupported_acceptance(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeHostError("checkout_publication_unsupported")

    monkeypatch.setattr(
        artifact_service,
        "prepare_checkout_effect",
        unsupported_acceptance,
    )
    first = service.continue_authorized()
    request_path = scratch / "submit_request.json"
    canonical = request_path.read_bytes()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot_before = store.load_snapshot(head.current_run_id)
    request_path.unlink()

    retried = service.continue_authorized()

    assert first.status == retried.status == "needs_attention"
    assert (
        first.reason_code == retried.reason_code == "checkout_publication_unsupported"
    )
    assert first.store_revision == retried.store_revision
    assert first.trace.next_action == retried.trace.next_action
    assert first.trace.transaction_ids == retried.trace.transaction_ids == []
    assert request_path.read_bytes() == canonical
    assert calls == 2
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        assert store.load_snapshot(head.current_run_id) == snapshot_before


def test_discovery_invocation_rejects_role_output_drift_after_host_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    service = _service(workspace)
    planner = service.continue_authorized()
    assert planner.status == "role_work_required"
    assert planner.trace.envelope_path is not None
    scratch = (workspace / planner.trace.envelope_path).parent
    output_path = scratch / "source_candidates.yaml"
    output_path.write_text(
        "version: 1\ncandidates:\n  - route: web-search\n",
        encoding="utf-8",
    )
    calls = 0

    def unsupported_acceptance(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeHostError("checkout_publication_unsupported")

    monkeypatch.setattr(
        artifact_service,
        "prepare_checkout_effect",
        unsupported_acceptance,
    )
    first = service.continue_authorized()
    assert first.reason_code == "checkout_publication_unsupported"
    request_path = scratch / "submit_request.json"
    request_before = request_path.read_bytes()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot_before = store.load_snapshot(head.current_run_id)
    output_path.write_text(
        "version: 1\ncandidates:\n  - route: web-search\n  - route: rss\n",
        encoding="utf-8",
    )

    validation = service.validate_invocation(scratch.name)
    rejected = service.continue_authorized()

    assert validation.status == "invalid"
    assert validation.reason_code == "runtime_scratch_invalid"
    assert rejected.status == "proposal_invalid"
    assert rejected.reason_code == "runtime_proposal_invalid"
    assert rejected.trace.transaction_ids == []
    assert request_path.read_bytes() == request_before
    assert calls == 1
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        assert store.load_snapshot(head.current_run_id) == snapshot_before


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_continue_promotes_one_mixed_pack_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    calls: list[str] = []

    def collect(
        _provider: WebSearchProvider,
        _query,
        _config,
    ) -> WebSearchCollection:
        calls.append("tavily")
        return _tavily_collection(
            [_tavily_item(durable=True), _tavily_item(durable=False)]
        )

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)

    planner = _service(workspace).continue_authorized()

    assert planner.status == "role_work_required"
    assert planner.trace.envelope_path is not None
    assert calls == []
    planner_scratch = (workspace / planner.trace.envelope_path).parent
    (planner_scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: web-search\n",
        encoding="utf-8",
    )

    result = _service(workspace).continue_authorized()

    assert result.status == "role_work_required", result.model_dump(mode="json")
    assert calls == ["tavily"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
        history = store.load_history()
    assert len(snapshot.run_source_discovery_authorizations) == 1
    assert len(snapshot.run_execution_authorizations) == 1
    assert len(snapshot.sources) == 2
    assert sorted(source.claims_eligible for source in snapshot.sources) == [
        False,
        True,
    ]
    promotion_receipts = [
        receipt
        for receipt in history.transactions
        if receipt.transaction_type == "source_evidence_intake"
    ]
    assert len(promotion_receipts) == 1
    promotion = promotion_receipts[0]
    assert len(promotion.source_ids) == 2
    assert len(promotion.run_source_discovery_authorizations) == 1
    assert len(promotion.run_execution_authorizations) == 1
    database_bytes = (workspace / "briefloop.db").read_bytes()
    secret = b"tvly-runtime-secret-sentinel"
    secret_hash = hashlib.sha256(secret).hexdigest().encode("ascii")
    assert secret not in database_bytes
    assert secret_hash not in database_bytes
    for path in workspace.rglob("*"):
        if path.is_file() and path.name != ".env":
            payload = path.read_bytes()
            assert secret not in payload
            assert secret_hash not in payload
    request_path = _public_source_request(
        workspace,
        run_id=snapshot.run.run_id,
        invocation_id="INV-POST-PROMOTION-GUARD",
        entrypoint="pack",
    )
    intake = IntakeService(workspace)
    opened: list[str] = []
    original_read = intake._reader.read

    def _record_read(path):
        opened.append(str(path))
        return original_read(path)

    monkeypatch.setattr(intake._reader, "read", _record_read)
    revision = _revision(workspace)

    blocked = intake.submit_source_pack(request_path)

    assert blocked.status == "failed_uncommitted"
    assert blocked.error_code == "source_pack_authorization_invalid"
    assert opened == [request_path]
    assert _revision(workspace) == revision


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_workspace_env_reaches_real_tavily_boundary_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    calls: list[dict[str, object]] = []
    authorization_headers: list[str | None] = []
    response_bytes = json.dumps(
        {
            "results": [
                {
                    "title": "Durable source",
                    "url": "https://example.com/durable",
                    "content": "snippet only",
                    "raw_content": "durable provider content",
                    "published_date": " 2026-07-23",
                    "score": 0.9,
                }
            ]
        },
        separators=(",", ":"),
    ).encode("utf-8")

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read(_limit=-1) -> bytes:
            return response_bytes

    def urlopen(request, *, timeout):
        assert timeout == 30
        assert request.full_url == "https://api.tavily.com/search"
        assert os.environ["TAVILY_API_KEY"] == "tvly-runtime-secret-sentinel"
        payload = json.loads(request.data.decode("utf-8"))
        calls.append(payload)
        authorization_headers.append(request.get_header("Authorization"))
        return _Response()

    monkeypatch.setattr(
        "multi_agent_brief.sources.search_backends.tavily.urllib.request.urlopen",
        urlopen,
    )
    service = _service(workspace)
    action = _advance_discovery_to_source_action(workspace)

    result = service.apply_current(action)

    assert result.status == "committed"
    assert len(calls) == 1
    assert calls[0]["include_raw_content"] == "markdown"
    assert calls[0]["auto_parameters"] is False
    assert calls[0]["search_depth"] == "basic"
    assert calls[0]["max_results"] == 5
    assert calls[0]["time_range"] == "week"
    assert "days" not in calls[0]
    assert "api_key" not in calls[0]
    assert authorization_headers == ["Bearer tvly-runtime-secret-sentinel"]
    assert "TAVILY_API_KEY" not in os.environ
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
        history = store.load_history()
        CoreRunDomainVerifier().verify(store, snapshot.run.run_id)
        promotion = next(
            receipt
            for receipt in history.transactions
            if receipt.transaction_type == "source_evidence_intake"
        )
        provider_revision = next(
            revision
            for revision in promotion.artifact_revisions
            if revision.artifact_id.startswith("ARTIFACT-PROVIDER-RESPONSE")
        )
        provider_bytes = store.read_artifact_revision_bytes(
            snapshot.run.run_id,
            provider_revision.artifact_id,
            provider_revision.revision,
        )
        source = snapshot.sources[0]
        raw_projection = store.read_artifact_revision_bytes(
            snapshot.run.run_id,
            source.raw_payload_artifact_id,
            source.raw_payload_artifact_revision,
        )
    assert len(snapshot.sources) == 1
    assert source.origin_type == "provider_response"
    assert source.acquisition_method == "provider_extract"
    assert source.material_kind == "partial_extract"
    assert source.claims_eligible is True
    assert source.published_at is None
    assert provider_bytes == response_bytes
    assert json.loads(raw_projection)["published_date"] == " 2026-07-23"
    assert len(snapshot.run_execution_authorizations) == 1
    handoff = service.continue_authorized()
    assert handoff.status == "role_work_required"
    database = (workspace / "briefloop.db").read_bytes()
    revision = _revision(workspace)
    (workspace / ".env").unlink()

    replayed = service.continue_authorized()

    assert replayed.status == "role_work_required"
    assert len(calls) == 1
    assert _revision(workspace) == revision
    assert (workspace / "briefloop.db").read_bytes() == database
    assert b"tvly-runtime-secret-sentinel" not in database

    from multi_agent_brief.control_store.serialization import canonical_json_bytes

    original_reader = ControlStoreHistory.read_artifact_revision_bytes

    def trim_frozen_projection(
        reader: ControlStoreHistory,
        run_id: str,
        artifact_id: str,
        artifact_revision: int,
    ) -> bytes:
        payload = original_reader(reader, run_id, artifact_id, artifact_revision)
        if artifact_id == source.raw_payload_artifact_id:
            projection = json.loads(payload)
            projection["published_date"] = projection["published_date"].strip()
            return canonical_json_bytes(projection)
        return payload

    monkeypatch.setattr(
        ControlStoreHistory,
        "read_artifact_revision_bytes",
        trim_frozen_projection,
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        CoreRunDomainVerifier()._verify_snapshot(history, snapshot)


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_exact_receipt_replay_precedes_secret_and_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    calls = 0

    def collect(_provider, _query, _config):
        nonlocal calls
        calls += 1
        return _tavily_collection([_tavily_item(durable=True)])

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    action = _advance_discovery_to_source_action(workspace)
    stage_identity = _discovery_stage_identity(workspace, action)
    service = _service(workspace)

    committed = service.apply_current(action)
    (workspace / ".env").unlink()
    replayed = service.apply_current(action)

    assert committed.status == "committed"
    assert replayed.status == "replayed"
    assert replayed.transaction_id == committed.transaction_id
    assert calls == 1
    assert not source_stage_root(workspace, stage_identity).exists()


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_precommit_crash_reuses_staged_bytes_before_secret_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    provider_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        return _tavily_collection([_tavily_item(durable=True)])

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    action = _advance_discovery_to_source_action(workspace)
    original_commit = IntakeService._commit_discovery_source_pack_from_core

    def crash_after_stage(_instance, _input):
        raise RuntimeHostError("simulated_precommit_crash")

    monkeypatch.setattr(
        IntakeService,
        "_commit_discovery_source_pack_from_core",
        crash_after_stage,
    )
    with pytest.raises(RuntimeHostError, match="simulated_precommit_crash"):
        _service(workspace).apply_current(action)
    assert provider_calls == 1
    (workspace / ".env").unlink()
    monkeypatch.setattr(
        IntakeService,
        "_commit_discovery_source_pack_from_core",
        original_commit,
    )

    committed = _service(workspace).apply_current(_service(workspace).next_action())

    assert committed.status == "committed"
    assert provider_calls == 1
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.sources) == 1
    assert len(snapshot.run_execution_authorizations) == 1


def test_discovery_source_acquire_platform_stop_preserves_verified_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_agent_brief.core_run_v2.publication_platform import (
        capability_profile as actual_capability_profile,
    )

    workspace = _discovery_workspace(tmp_path)
    action = _transferred_discovery_source_action(workspace)
    stage_identity = _discovery_stage_identity(workspace, action)

    stage_root = source_stage_root(workspace, stage_identity)
    stage_before = {
        path.relative_to(stage_root).as_posix(): path.read_bytes()
        for path in stage_root.rglob("*")
        if path.is_file()
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot_before = store.load_snapshot(head.current_run_id)
    env_before = (workspace / ".env").read_bytes()
    env_mtime_before = (workspace / ".env").stat().st_mtime_ns

    capability_checks = 0

    def unsupported_capability(path: Path):
        nonlocal capability_checks
        capability_checks += 1
        assert path == workspace
        try:
            actual_capability_profile(path)
        except CoreRunError as exc:
            assert exc.code == "checkout_publication_unsupported"
            raise
        raise CoreRunError("checkout_publication_unsupported")

    def forbidden_credential_read(*_args, **_kwargs):
        pytest.fail("credential must not be inspected after capability stop")

    def forbidden_provider(*_args, **_kwargs):
        pytest.fail("provider must not be called after capability stop")

    def forbidden_network(*_args, **_kwargs):
        pytest.fail("network must not be called after capability stop")

    monkeypatch.setattr(
        "multi_agent_brief.runtime_host_v2.service.capability_profile",
        unsupported_capability,
    )
    monkeypatch.setattr(
        "multi_agent_brief.runtime_host_v2.service.known_env_key_is_set",
        forbidden_credential_read,
    )
    monkeypatch.setattr(WebSearchProvider, "collect_with_response", forbidden_provider)
    monkeypatch.setattr(
        "multi_agent_brief.sources.search_backends.tavily.urllib.request.urlopen",
        forbidden_network,
    )

    for _ in range(2):
        stopped = _service(workspace).continue_authorized()
        assert stopped.status == "needs_attention"
        assert stopped.reason_code == "checkout_publication_unsupported"
        assert _service(workspace).next_action() == action

    assert capability_checks == 2
    assert "TAVILY_API_KEY" not in os.environ
    assert (workspace / ".env").read_bytes() == env_before
    assert (workspace / ".env").stat().st_mtime_ns == env_mtime_before
    assert {
        path.relative_to(stage_root).as_posix(): path.read_bytes()
        for path in stage_root.rglob("*")
        if path.is_file()
    } == stage_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot_after = store.load_snapshot(head.current_run_id)
    assert snapshot_after == snapshot_before


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_active_invocation_reuses_receipt_owned_stage_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    provider_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        return _tavily_collection([_tavily_item(durable=True)])

    def crash_before_promotion(_instance, _input):
        raise RuntimeHostError("simulated_post_invocation_crash")

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    monkeypatch.setattr(
        IntakeService,
        "_commit_discovery_source_pack_from_core",
        crash_before_promotion,
    )
    action = _advance_discovery_to_source_action(workspace)

    with pytest.raises(RuntimeHostError, match="simulated_post_invocation_crash"):
        _service(workspace).apply_current(action)

    resumed = _service(workspace).next_action()
    assert resumed.action_kind == "deterministic"
    assert resumed.effect_kind == "source_acquire"
    assert resumed.reason_code == "active_discovery_source_acquire_requires_resume"
    (workspace / ".env").unlink()
    monkeypatch.undo()

    committed = _service(workspace).apply_current(resumed)

    assert committed.status == "committed"
    assert provider_calls == 1
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.sources) == 1
    assert len(snapshot.run_execution_authorizations) == 1


@pytest.mark.parametrize("stage_damage", ["missing", "tampered"])
@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_active_invocation_invalid_stage_fails_without_provider_recall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage_damage: str,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    provider_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        return _tavily_collection([_tavily_item(durable=True)])

    def crash_before_promotion(_instance, _input):
        raise RuntimeHostError("simulated_post_invocation_crash")

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    monkeypatch.setattr(
        IntakeService,
        "_commit_discovery_source_pack_from_core",
        crash_before_promotion,
    )
    action = _advance_discovery_to_source_action(workspace)
    stage_identity = _discovery_stage_identity(workspace, action)

    with pytest.raises(RuntimeHostError, match="simulated_post_invocation_crash"):
        _service(workspace).apply_current(action)

    stage_root = source_stage_root(workspace, stage_identity)
    if stage_damage == "missing":
        stage_root.rename(stage_root.with_name(f"{stage_root.name}.missing"))
    else:
        next(stage_root.glob("sources/*/source_content.bin")).write_bytes(
            b"tampered staged content"
        )
    (workspace / ".env").unlink()
    monkeypatch.setattr(
        WebSearchProvider,
        "collect_with_response",
        lambda *_args, **_kwargs: pytest.fail("provider must not be recalled"),
    )

    expected = (
        "source_acquisition_outcome_unknown"
        if stage_damage == "missing"
        else "source_provider_result_invalid"
    )
    with pytest.raises(RuntimeHostError, match=expected):
        _service(workspace).apply_current(_service(workspace).next_action())

    assert provider_calls == 1
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()
    active = [
        item
        for item in snapshot.invocations
        if item.role_id == "source-provider" and item.status == "active"
    ]
    assert len(active) == 1


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_tampered_precommit_stage_fails_closed_without_provider_recall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    provider_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        return _tavily_collection([_tavily_item(durable=True)])

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    action = _advance_discovery_to_source_action(workspace)
    stage_identity = _discovery_stage_identity(workspace, action)

    def crash_after_stage(_instance, _input):
        raise RuntimeHostError("simulated_precommit_crash")

    monkeypatch.setattr(
        IntakeService,
        "_commit_discovery_source_pack_from_core",
        crash_after_stage,
    )
    with pytest.raises(RuntimeHostError, match="simulated_precommit_crash"):
        _service(workspace).apply_current(action)
    stage_root = source_stage_root(workspace, stage_identity)
    content_path = next(stage_root.glob("sources/*/source_content.bin"))
    content_path.write_bytes(b"tampered staged content")
    (workspace / ".env").unlink()

    with pytest.raises(RuntimeHostError, match="source_provider_result_invalid"):
        _service(workspace).apply_current(_service(workspace).next_action())

    assert provider_calls == 1
    assert stage_root.exists()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_commit_outcome_unknown_replays_without_provider_recall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    provider_calls = 0
    commit_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        return _tavily_collection([_tavily_item(durable=True)])

    original = IntakeService._commit_discovery_source_pack_from_core

    def commit_then_report_unknown(instance, input):
        nonlocal commit_calls
        commit_calls += 1
        result = original(instance, input)
        if commit_calls == 1:
            raise ControlStoreCommitOutcomeUnknown("commit_outcome_unknown")
        return result

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    monkeypatch.setattr(
        IntakeService,
        "_commit_discovery_source_pack_from_core",
        commit_then_report_unknown,
    )
    action = _advance_discovery_to_source_action(workspace)

    result = _service(workspace).apply_current(action)

    assert result.status == "replayed"
    assert provider_calls == 1
    assert commit_calls == 2
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.sources) == 1
    assert len(snapshot.run_execution_authorizations) == 1


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_promotion_failure_rolls_back_all_authority_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    action = _advance_discovery_to_source_action(workspace)
    monkeypatch.setattr(
        WebSearchProvider,
        "collect_with_response",
        lambda _provider, _query, _config: _tavily_collection(
            [_tavily_item(durable=True)]
        ),
    )
    original_init = IntakeService.__init__
    init_calls = 0

    def init_with_first_commit_failure(instance, workspace_path, **kwargs):
        nonlocal init_calls
        init_calls += 1

        def fail(stage: str) -> None:
            if stage == "after_records":
                raise ControlStoreIntegrityError("injected_promotion_failure")

        original_init(
            instance,
            workspace_path,
            _store_failure_hook=fail if init_calls == 1 else None,
            **kwargs,
        )

    monkeypatch.setattr(IntakeService, "__init__", init_with_first_commit_failure)

    with pytest.raises(RuntimeHostError, match="source_provider_result_invalid"):
        _service(workspace).apply_current(action)

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.run_source_discovery_authorizations) == 1
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()
    assert not [
        item
        for item in snapshot.owned_artifact_submissions
        if item.artifact_id == "input_classification"
    ]
    assert not [
        item
        for item in snapshot.artifact_revisions
        if item.artifact_id == "execution-source-manifest"
    ]
    failures = [
        item
        for item in snapshot.invocations
        if item.role_id == "source-provider" and item.status == "failed"
    ]
    assert len(failures) == 1


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_all_snippets_fail_without_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    monkeypatch.setattr(
        WebSearchProvider,
        "collect_with_response",
        lambda _provider, _query, _config: _tavily_collection(
            [_tavily_item(durable=False)]
        ),
    )
    action = _advance_discovery_to_source_action(workspace)

    with pytest.raises(RuntimeHostError, match="source_pack_empty"):
        _service(workspace).apply_current(action)

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()
    assert (
        len(
            [
                item
                for item in snapshot.invocations
                if item.role_id == "source-provider" and item.status == "failed"
            ]
        )
        == 1
    )


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_empty_provider_result_fails_without_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    monkeypatch.setattr(
        WebSearchProvider,
        "collect_with_response",
        lambda _provider, _query, _config: _tavily_collection([]),
    )
    action = _advance_discovery_to_source_action(workspace)

    with pytest.raises(RuntimeHostError, match="source_pack_empty"):
        _service(workspace).apply_current(action)

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()
    assert (
        len(
            [
                item
                for item in snapshot.invocations
                if item.role_id == "source-provider" and item.status == "failed"
            ]
        )
        == 1
    )


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_empty_provider_result_replays_stable_human_source_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    provider_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls != 1:
            pytest.fail("human source fallback must not recall the provider")
        return _tavily_collection([])

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    _advance_discovery_to_source_action(workspace)
    service = _service(workspace)

    failed = service.continue_authorized()

    assert failed.status == "needs_attention"
    assert failed.reason_code == "source_pack_empty"
    assert provider_calls == 1
    current = service.next_action()
    assert (
        current.action_kind,
        current.effect_kind,
        current.stage_id,
        current.reason_code,
    ) == (
        "human_decision",
        "source_input_required",
        "source-discovery",
        "human_source_material_required",
    )

    (workspace / ".env").unlink()
    before_revision = _revision(workspace)
    database_before = (workspace / "briefloop.db").read_bytes()

    first = service.continue_authorized()
    second = service.continue_authorized()

    assert first == second
    assert first.status == "needs_human"
    assert first.reason_code == "human_source_material_required"
    assert first.trace.next_action == current
    assert provider_calls == 1
    assert _revision(workspace) == before_revision
    assert (workspace / "briefloop.db").read_bytes() == database_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.run_source_discovery_authorizations) == 1
    assert snapshot.run_execution_authorizations == ()
    assert snapshot.sources == ()
    failures = [
        item
        for item in snapshot.invocations
        if item.role_id == "source-provider" and item.status == "failed"
    ]
    assert len(failures) == 1
    assert failures[0].failure_reason == "proposal_invalid"


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_malformed_provider_result_fails_without_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    duplicate = _tavily_item(durable=True)
    conflicting = replace(duplicate, content="conflicting durable content")
    monkeypatch.setattr(
        WebSearchProvider,
        "collect_with_response",
        lambda _provider, _query, _config: _tavily_collection([duplicate, conflicting]),
    )
    action = _advance_discovery_to_source_action(workspace)

    with pytest.raises(RuntimeHostError, match="source_provider_result_invalid"):
        _service(workspace).apply_current(action)

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()
    assert (
        len(
            [
                item
                for item in snapshot.invocations
                if item.role_id == "source-provider" and item.status == "failed"
            ]
        )
        == 1
    )


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_provider_failure_records_one_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    sentinel = "tvly-secret-must-not-escape"
    calls = 0

    def fail_transport(_request, timeout=30):
        nonlocal calls
        calls += 1
        raise RuntimeError(sentinel)

    monkeypatch.setattr("urllib.request.urlopen", fail_transport)
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-tavily-key")
    _advance_discovery_to_source_action(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        artifacts_before = store.load_snapshot(head.current_run_id).artifacts

    result = _service(workspace).continue_authorized()

    assert result.status == "needs_attention"
    assert result.reason_code == "source_provider_unavailable"
    assert sentinel not in repr(result)
    assert sentinel not in caplog.text
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
        history = store.load_history()
    assert calls == 1
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()
    assert snapshot.artifacts == artifacts_before
    failures = [
        item
        for item in snapshot.invocations
        if item.role_id == "source-provider" and item.status == "failed"
    ]
    assert len(failures) == 1
    assert failures[0].failure_reason == "child_failed"
    assert sentinel not in repr(snapshot)
    assert sentinel not in repr(history.transactions)
    assert sentinel.encode() not in (workspace / "briefloop.db").read_bytes()


@pytest.mark.parametrize("echo_kind", ["secret", "sha256"])
@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_provider_credential_echo_fails_before_stage_or_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    echo_kind: str,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    sentinel = "tvly-runtime-secret-sentinel"
    sentinel_hash = hashlib.sha256(sentinel.encode("utf-8")).hexdigest()
    calls = 0

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read(_limit=-1) -> bytes:
            echoed = sentinel if echo_kind == "secret" else sentinel_hash.upper()
            return json.dumps(
                {
                    "ignored_diagnostic": echoed,
                    "results": [
                        {
                            "title": "Durable source",
                            "url": "https://example.com/durable",
                            "content": "search snippet",
                            "raw_content": "retrieved durable page extract",
                            "score": 0.9,
                        }
                    ],
                }
            ).encode("utf-8")

    def echo_response(request, timeout=30):
        nonlocal calls
        assert timeout == 30
        assert request.get_header("Authorization") == f"Bearer {sentinel}"
        calls += 1
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", echo_response)
    action = _advance_discovery_to_source_action(workspace)
    stage_identity = _discovery_stage_identity(workspace, action)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        artifacts_before = store.load_snapshot(head.current_run_id).artifacts

    result = _service(workspace).continue_authorized()

    assert calls == 1
    assert result.status == "needs_attention"
    assert result.reason_code == "source_provider_unavailable"
    assert sentinel not in repr(result)
    assert sentinel_hash not in repr(result).lower()
    assert sentinel not in caplog.text
    assert sentinel_hash not in caplog.text.lower()
    assert not source_stage_root(workspace, stage_identity).exists()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
        history = store.load_history()
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()
    assert snapshot.artifacts == artifacts_before
    database_bytes = (workspace / "briefloop.db").read_bytes()
    assert sentinel.encode("utf-8") not in database_bytes
    assert sentinel_hash.encode("ascii") not in database_bytes.lower()
    assert sentinel not in repr(history.transactions)
    assert sentinel_hash not in repr(history.transactions).lower()


def test_authorized_continue_commits_pack_and_returns_exact_role_work(
    tmp_path: Path,
) -> None:
    workspace = _authorized_workspace(tmp_path)

    result = _service(workspace).continue_authorized()

    assert result.status == "role_work_required"
    assert result.reason_code == "role_work_required"
    assert result.current_stage == "scout"
    assert result.completed_stages >= 2
    assert result.trace.next_action.effect_kind == "invocation_accept_or_fail"
    assert result.trace.envelope_path is not None
    assert (workspace / result.trace.envelope_path).is_file()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.run_execution_authorizations) == 1
    assert len(snapshot.sources) == 1


def test_missing_and_invalid_proposal_are_zero_write(tmp_path: Path) -> None:
    workspace = _authorized_workspace(tmp_path)
    first = _service(workspace).continue_authorized()
    revision = _revision(workspace)

    missing = _service(workspace).continue_authorized()
    assert missing.status == "role_work_required"
    assert _revision(workspace) == revision

    assert first.trace.envelope_path is not None
    envelope = json.loads((workspace / first.trace.envelope_path).read_text())
    scratch = workspace / envelope["scratch_directory"]
    (scratch / "candidate_claims.json").write_text("{}\n", encoding="utf-8")
    invalid = _service(workspace).continue_authorized()

    assert invalid.status == "proposal_invalid"
    assert invalid.reason_code == "runtime_proposal_invalid"
    assert invalid.violations
    assert _revision(workspace) == revision


def test_continue_cli_hides_trace_unless_explicit(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _authorized_workspace(tmp_path)

    assert main(["runtime", "continue", "--workspace", str(workspace)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "role_work_required"
    assert "trace" not in payload

    assert main(["runtime", "continue", "--workspace", str(workspace), "--trace"]) == 0
    traced = json.loads(capsys.readouterr().out)
    assert traced["trace"]["next_action"]["effect_kind"] == "invocation_accept_or_fail"


def test_progress_ceiling_is_zero_write_attention(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    revision = _revision(workspace)
    monkeypatch.setattr(
        service,
        "apply_current",
        lambda _action, **_kwargs: SimpleNamespace(receipt=None),
    )

    result = service.continue_authorized(maximum_progress_attempts=2)

    assert result.status == "needs_attention"
    assert result.reason_code == "runtime_progress_stalled"
    assert _revision(workspace) == revision


def test_stale_deterministic_action_refreshes_without_spending_progress_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    original = service.apply_current
    calls = 0

    def _stale_once(action, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeHostError("runtime_action_stale")
        return original(action, **kwargs)

    monkeypatch.setattr(service, "apply_current", _stale_once)

    result = service.continue_authorized(maximum_progress_attempts=8)

    assert calls >= 2
    assert result.status == "role_work_required"
    assert result.current_stage == "scout"


def test_persistent_invocation_reservation_unknown_returns_typed_attention(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _authorized_workspace(tmp_path)
    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.service.CoreRunService.start_invocation",
        lambda *_args, **_kwargs: CoreRunResult(
            status="commit_outcome_unknown",
            error_code="commit_outcome_unknown",
        ),
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.maybe_auto_open_brief_pages",
        lambda _workspace: {
            "status": "projection_unavailable",
            "relative_path": None,
            "reason_code": "brief_html_projection_unavailable",
        },
    )

    result = _service(workspace).continue_authorized()

    assert result.status == "needs_attention"
    assert result.reason_code == "commit_outcome_unknown"


def test_persistent_proposal_accept_unknown_returns_typed_attention(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    required = service.continue_authorized()
    assert required.status == "role_work_required"
    _write_current_role_proposal(workspace, required)
    monkeypatch.setattr(
        IntakeService,
        "_submit_proposal_from_host",
        lambda *_args, **_kwargs: SimpleNamespace(status="commit_outcome_unknown"),
    )

    result = service.continue_authorized()

    assert result.status == "needs_attention"
    assert result.reason_code == "commit_outcome_unknown"


def test_committed_proposal_accept_unknown_replays_identity_before_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    required = service.continue_authorized()
    assert required.status == "role_work_required"
    _write_current_role_proposal(workspace, required)
    original = IntakeService._submit_proposal_from_host
    calls: list[tuple[str, bytes]] = []
    committed_transaction_id: str | None = None

    def _commit_then_unknown(instance, lane, request, proposal_bytes):
        nonlocal committed_transaction_id
        calls.append((request.request_id, proposal_bytes))
        result = original(instance, lane, request, proposal_bytes)
        if len(calls) == 1:
            assert result.receipt is not None
            committed_transaction_id = result.receipt.transaction_id
            return SimpleNamespace(status="commit_outcome_unknown")
        return result

    monkeypatch.setattr(
        IntakeService,
        "_submit_proposal_from_host",
        _commit_then_unknown,
    )

    result = service.continue_authorized()

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert committed_transaction_id is not None
    assert committed_transaction_id in result.trace.transaction_ids
    assert result.status == "role_work_required"


def test_runtime_host_proposal_acceptance_commits_pre_replacement_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    required = service.continue_authorized()
    assert required.status == "role_work_required"
    _write_current_role_proposal(workspace, required)
    assert required.trace.envelope_path is not None
    envelope_path = workspace / required.trace.envelope_path
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    proposal_path = workspace / envelope["scratch_directory"] / "candidate_claims.json"
    proposal_a = proposal_path.read_bytes()
    proposal_b_payload = json.loads(proposal_a)
    proposal_b_payload["candidates"][0]["statement"] = (
        "Valid replacement written after RuntimeHost verification."
    )
    proposal_b = json.dumps(
        proposal_b_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    original_materialize = host_service.materialize_host_request

    def _materialize_then_replace(*args, **kwargs):
        result = original_materialize(*args, **kwargs)
        proposal_path.write_bytes(proposal_b)
        return result

    monkeypatch.setattr(
        host_service,
        "materialize_host_request",
        _materialize_then_replace,
    )

    accepted = service.accept_invocation(envelope["invocation_id"])

    assert accepted.status == "committed"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(envelope["run_id"])
        proposal = next(
            item
            for item in snapshot.accepted_proposals
            if item.proposal_kind == "candidate"
        )
        assert proposal.proposal_sha256 == hashlib.sha256(proposal_a).hexdigest()
        revision = next(
            item
            for item in snapshot.artifact_revisions
            if item.artifact_id == proposal.artifact_id
            and item.revision == proposal.artifact_revision
        )
        assert (workspace / revision.path).read_bytes() == proposal_a
        CoreRunDomainVerifier().verify(store, envelope["run_id"])


@_REQUIRES_RETAINED_PUBLICATION
def test_runtime_host_owned_acceptance_commits_pre_replacement_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    service = _service(workspace)
    required = service.continue_authorized()
    assert required.status == "role_work_required"
    assert required.trace.envelope_path is not None
    envelope_path = workspace / required.trace.envelope_path
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    proposal_path = workspace / envelope["scratch_directory"] / "source_candidates.yaml"
    proposal_a = b"version: 1\ncandidates:\n  - route: web-search\n"
    proposal_b = b"version: 1\ncandidates:\n  - route: uploaded-source\n"
    proposal_path.write_bytes(proposal_a)
    original_materialize = host_service.materialize_host_request

    def _materialize_then_replace(*args, **kwargs):
        result = original_materialize(*args, **kwargs)
        proposal_path.write_bytes(proposal_b)
        return result

    monkeypatch.setattr(
        host_service,
        "materialize_host_request",
        _materialize_then_replace,
    )

    accepted = service.accept_invocation(envelope["invocation_id"])

    assert accepted.status == "committed"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(envelope["run_id"])
        revision = next(
            item
            for item in snapshot.artifact_revisions
            if item.artifact_id == "source_candidates" and item.revision == 1
        )
        assert revision.sha256 == hashlib.sha256(proposal_a).hexdigest()
        assert (workspace / revision.path).read_bytes() == proposal_a
        CoreRunDomainVerifier().verify(store, envelope["run_id"])


def test_runtime_host_source_acceptance_commits_pre_replacement_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    if sys.platform == "win32":
        pytest.skip("source-candidate publication is precommit unsupported on Windows")
    workspace = _specialist_workspace(tmp_path)
    host, action = _advance_to_source_route(workspace, capsys, route="rss")
    dispatch = host.start_current_invocation(expected_action=action)
    scratch = workspace / dispatch.envelope.scratch_directory
    content_a = b"Exact source bytes verified by RuntimeHost.\n"
    raw_a = b'{"provider":"rss","result":"verified-a"}\n'
    proposal_a_payload = SchemaRegistry.example(SourceProposal.schema_id, "full")
    proposal_a_payload.update(
        proposal_id="PROP-SOURCE-RSS-HOST-A",
        run_id=action.run_id,
        source_id="SRC-RSS-HOST-A",
        title="Verified source A",
        content_sha256=hashlib.sha256(content_a).hexdigest(),
        raw_payload_sha256=hashlib.sha256(raw_a).hexdigest(),
    )
    proposal_a = json.dumps(
        proposal_a_payload, sort_keys=True, separators=(",", ":")
    ).encode()
    proposal_path = scratch / "source_proposal.json"
    content_path = scratch / "source_content.bin"
    raw_path = scratch / "source_raw.json"
    proposal_path.write_bytes(proposal_a)
    content_path.write_bytes(content_a)
    raw_path.write_bytes(raw_a)

    content_b = b"Valid replacement source bytes after Host verification.\n"
    raw_b = b'{"provider":"rss","result":"replacement-b"}\n'
    proposal_b_payload = deepcopy(proposal_a_payload)
    proposal_b_payload.update(
        title="Replacement source B",
        content_sha256=hashlib.sha256(content_b).hexdigest(),
        raw_payload_sha256=hashlib.sha256(raw_b).hexdigest(),
    )
    proposal_b = json.dumps(
        proposal_b_payload, sort_keys=True, separators=(",", ":")
    ).encode()
    original_materialize = host_service.materialize_host_request

    def _materialize_then_replace(*args, **kwargs):
        result = original_materialize(*args, **kwargs)
        proposal_path.write_bytes(proposal_b)
        content_path.write_bytes(content_b)
        raw_path.write_bytes(raw_b)
        return result

    monkeypatch.setattr(
        host_service,
        "materialize_host_request",
        _materialize_then_replace,
    )

    accepted = host.accept_invocation(dispatch.envelope.invocation_id)

    assert accepted.status == "committed"
    replacement_hashes = {
        hashlib.sha256(proposal_b).hexdigest(),
        hashlib.sha256(content_b).hexdigest(),
        hashlib.sha256(raw_b).hexdigest(),
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(action.run_id)
        history = store.load_history()
        source = next(
            item for item in snapshot.sources if item.source_id == "SRC-RSS-HOST-A"
        )
        assert source.title == "Verified source A"
        assert source.content_sha256 == hashlib.sha256(content_a).hexdigest()
        assert source.raw_payload_sha256 == hashlib.sha256(raw_a).hexdigest()
        assert (workspace / source.content_blob_path).read_bytes() == content_a
        assert source.raw_payload_blob_path is not None
        assert (workspace / source.raw_payload_blob_path).read_bytes() == raw_a
        CoreRunDomainVerifier().verify(store, action.run_id)
        for replacement_hash in replacement_hashes:
            assert replacement_hash not in repr(snapshot)
            assert replacement_hash not in repr(history.transactions)
            assert (
                replacement_hash.encode()
                not in (workspace / "briefloop.db").read_bytes()
            )


def test_finalize_effect_suppresses_legacy_hook_then_presents_terminal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(
        "output:\n  html_report:\n    auto_open: true\n",
        encoding="utf-8",
    )

    def _action(*, kind: str, effect: str, reason: str, revision: int):
        payload: dict[str, object] = {
            "schema_version": "briefloop.core_run_next_action.v2",
            "run_id": "RUN-TEST",
            "store_revision": revision,
            "action_kind": kind,
            "effect_kind": effect,
            "stage_id": None,
            "role_id": None,
            "source_route_id": None,
            "source_provider_id": None,
            "reason_code": reason,
            "input_artifacts": [],
            "request_schema_id": None,
            "adapter_binding_fingerprint": "a" * 64,
            "source_plan_fingerprint": "b" * 64,
        }
        payload["action_fingerprint"] = canonical_fingerprint(payload)
        return CoreRunNextAction.model_validate(payload, strict=True)

    def _current(action, revision: int):
        snapshot = SimpleNamespace(
            run=SimpleNamespace(run_id="RUN-TEST"),
            store_revision=revision,
            stage_states=[],
            run_execution_authorizations=[object()],
            run_source_discovery_authorizations=[],
        )
        return SimpleNamespace(
            action=action,
            verified=SimpleNamespace(snapshot=snapshot),
        )

    first_action = _action(
        kind="deterministic",
        effect="finalize_complete",
        reason="finalize_completion_required",
        revision=8,
    )
    complete_action = _action(
        kind="complete",
        effect="finalized_local",
        reason="local_finalization_complete",
        revision=9,
    )
    currents = iter((_current(first_action, 8), _current(complete_action, 9)))
    monkeypatch.setattr(
        "multi_agent_brief.runtime_host_v2.service.initialize_or_open_runtime",
        lambda *_args, **_kwargs: next(currents),
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.render."
        "supports_retained_directory_publication",
        lambda: False,
    )
    service = RuntimeHostService(workspace, adapter_loader=lambda _runtime: None)
    presentation_flags: list[bool] = []
    assessment_observations: list[str] = []
    monkeypatch.setattr(
        service,
        "_observe_post_final_assessment",
        lambda: assessment_observations.append("finalized_local"),
    )
    monkeypatch.setattr(
        service,
        "apply_current",
        lambda _action, *, presentation_hook=True: (
            presentation_flags.append(presentation_hook)
            or SimpleNamespace(receipt=SimpleNamespace(transaction_id="TX-FINAL"))
        ),
    )

    result = service.continue_authorized()

    assert result.status == "finalized_local"
    assert result.reason_code == "local_finalization_complete"
    assert result.store_revision == 9
    assert result.trace.next_action.effect_kind == "finalized_local"
    assert result.trace.transaction_ids == ["TX-FINAL"]
    assert presentation_flags == [False]
    assert assessment_observations == ["finalized_local"]
    assert result.presentation is not None
    assert result.presentation.status == "projection_unavailable"
    assert result.presentation.relative_path is None
    assert result.presentation.reason_code == "brief_html_projection_unavailable"
    assert not (workspace / "output").exists()


def _write_current_role_proposal(
    workspace: Path,
    result,
    *,
    initial_editor_repetitions: int = 210,
    repair_editor_repetitions: int = 210,
) -> None:
    assert result.trace.envelope_path is not None
    envelope_path = workspace / result.trace.envelope_path
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    scratch = workspace / envelope["scratch_directory"]
    role_id = envelope["role_id"]
    run_id = envelope["run_id"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
    if role_id == "scout":
        payload = SchemaRegistry.example(
            "briefloop.candidate_claims_proposal.v2", "minimal"
        )
        payload["run_id"] = run_id
        payload["proposal_id"] = "PROP-M3-CANDIDATES"
        payload["candidates"][0].update(
            source_id=snapshot.sources[0].source_id,
            statement="ExampleCo opened a public pilot facility.",
            evidence_text="public durable source",
        )
        filename = "candidate_claims.json"
    elif role_id == "screener":
        candidate = next(
            item
            for item in snapshot.accepted_proposals
            if item.proposal_kind == "candidate"
        )
        payload = SchemaRegistry.example(
            "briefloop.screened_candidates_proposal.v2", "minimal"
        )
        payload.update(
            run_id=run_id,
            proposal_id="PROP-M3-SCREENED",
            candidate_claims_proposal_id=candidate.proposal_id,
        )
        payload["decisions"][0]["candidate_id"] = "CAND-001"
        filename = "screened_candidates.json"
    elif role_id == "claim-ledger":
        screened = next(
            item
            for item in snapshot.accepted_proposals
            if item.proposal_kind == "screened"
        )
        payload = SchemaRegistry.example(
            "briefloop.claim_drafts_proposal.v2", "minimal"
        )
        payload.update(
            run_id=run_id,
            proposal_id="PROP-M3-DRAFTS",
            screened_candidates_proposal_id=screened.proposal_id,
        )
        payload["drafts"][0]["source_ids"] = [snapshot.sources[0].source_id]
        filename = "claim_drafts.json"
    elif role_id in {"analyst", "editor"}:
        repairing = role_id == "editor" and bool(snapshot.gate_repair_cycles)
        repetitions = (
            repair_editor_repetitions if repairing else initial_editor_repetitions
        )
        body = (
            "# ExampleCo public brief\n\n## Executive Summary\n\n"
            + " ".join(["ExampleCo operations context"] * repetitions)
            + " ExampleCo opened a public pilot facility. [src:CL-0001]\n"
        )
        (
            scratch
            / ("analyst_draft.md" if role_id == "analyst" else "audited_brief.md")
        ).write_text(
            body,
            encoding="utf-8",
        )
        return
    elif role_id == "auditor":
        payload = deepcopy(
            SchemaRegistry.example("briefloop.audit_proposal.v2", "minimal")
        )
        payload.update(
            run_id=run_id,
            proposal_id=(
                "PROP-M4-AUDIT-REPAIR"
                if snapshot.gate_repair_cycles
                else "PROP-M3-AUDIT"
            ),
            artifact_id="audited_brief",
            artifact_revision=next(
                item.current_revision
                for item in snapshot.artifacts
                if item.artifact_id == "audited_brief"
            ),
            decision="pass",
            findings=[],
        )
        filename = "audit_proposal.json"
    else:  # pragma: no cover - the frozen single-session topology is exhaustive.
        raise AssertionError(role_id)
    (scratch / filename).write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )


def test_authorized_current_session_reaches_truthful_finalized_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        return
    from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
        ANTHROPIC_API_KEY_SETTING,
    )
    import multi_agent_brief.semantic_evaluator.runner as runner_module
    from tests.test_post_final_assessment import _fixture_service, _policy_payload

    workspace = _authorized_workspace(tmp_path)
    assessment_calls: list[tuple[str, int]] = []
    assessment = _fixture_service(
        workspace,
        assessment_calls,
        terminal_mode="finding",
    )
    policy = _policy_payload()
    policy["auto_run"] = True
    assert assessment.policy_set(policy)["ok"] is True
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.render.webbrowser.open",
        lambda _uri: False,
    )
    service = _service(workspace)
    assessment_observations: list[dict[str, object]] = []
    monkeypatch.setattr(
        service,
        "_observe_post_final_assessment",
        lambda: assessment_observations.append(assessment.observe_finalized_local()),
    )
    sequence: list[tuple[str, str, str]] = []

    for _ in range(8):
        result = service.continue_authorized()
        sequence.append(
            (
                result.status,
                result.reason_code,
                result.trace.next_action.action_fingerprint,
            )
        )
        if result.status == "finalized_local":
            break
        assert result.status == "role_work_required", (
            result.reason_code,
            result.trace.next_action.action_kind,
            result.trace.next_action.effect_kind,
            result.trace.next_action.reason_code,
            result.trace.transaction_ids,
        )
        _write_current_role_proposal(workspace, result)
    else:
        raise AssertionError("authorized current-session run did not terminate")

    assert result.reason_code == "local_finalization_complete"
    assert [item[0] for item in sequence] == [
        "role_work_required",
        "role_work_required",
        "role_work_required",
        "role_work_required",
        "role_work_required",
        "role_work_required",
        "finalized_local",
    ]
    assert all(len(item[2]) == 64 for item in sequence)
    assert result.trace.next_action.action_kind == "complete"
    assert result.trace.next_action.effect_kind == "finalized_local"
    assert result.presentation is not None
    assert result.presentation.status == "browser_unavailable"
    assert result.presentation.relative_path == "output/brief_pages.html"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
        reader = next(
            item
            for item in snapshot.artifact_revisions
            if item.artifact_id == "reader_brief"
            and item.revision
            == next(
                artifact.current_revision
                for artifact in snapshot.artifacts
                if artifact.artifact_id == "reader_brief"
            )
        )
        store_reader = store.read_artifact_revision_bytes(
            head.current_run_id,
            reader.artifact_id,
            reader.revision,
        )
    assert not snapshot.package_ready_records
    assert not snapshot.approvals
    assert not snapshot.delivery_authorizations
    assert not snapshot.delivery_attempts
    assert not snapshot.delivery_results
    assert len(snapshot.post_final_assessment_requests) == 1
    assert len(snapshot.post_final_assessment_results) == 1
    assert len(assessment_calls) == 9
    assert [item["status"] for item in assessment_observations] == ["available"]

    mutable_reader = workspace / "output" / "brief.md"
    mutable_reader.write_text("# forged mutable reader\n", encoding="utf-8")
    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    monkeypatch.setattr(
        runner_module.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("terminal replay touched SDK metadata")
        ),
    )
    replay = service.continue_authorized()
    assert replay.status == "finalized_local"
    assert replay.store_revision > result.store_revision
    assert len(assessment_calls) == 9
    assert [item["status"] for item in assessment_observations] == [
        "available",
        "available",
    ]
    html = (workspace / "output" / "brief_pages.html").read_text(encoding="utf-8")
    embedded = html.split('id="brief-pages-data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(embedded)["brief"]["markdown"] == store_reader.decode("utf-8")
    assert "forged mutable reader" not in html
    assert str(workspace) not in html
    assert result.trace.next_action.action_fingerprint not in html

    original_reader = ControlStoreHistory.read_artifact_revision_bytes

    def _non_utf8_reader(
        history: ControlStoreHistory,
        run_id: str,
        artifact_id: str,
        revision: int,
    ) -> bytes:
        if artifact_id == "reader_brief":
            return b"\xff"
        return original_reader(history, run_id, artifact_id, revision)

    monkeypatch.setattr(
        ControlStoreHistory,
        "read_artifact_revision_bytes",
        _non_utf8_reader,
    )
    from multi_agent_brief.product.brief_html import present_local_run

    projection_failure = present_local_run(
        workspace,
        browser_open=lambda _uri: True,
    )
    assert projection_failure == {
        "status": "projection_unavailable",
        "relative_path": None,
        "reason_code": "brief_html_projection_unavailable",
    }


def test_authorized_editor_gate_repair_runs_once_then_finalizes_local(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    role_sequence: list[str] = []

    for _ in range(12):
        result = service.continue_authorized()
        if result.status == "finalized_local":
            break
        assert result.status == "role_work_required", (
            result.reason_code,
            result.trace.next_action.action_kind,
            result.trace.next_action.effect_kind,
            result.trace.next_action.reason_code,
            result.trace.transaction_ids,
        )
        assert result.trace.envelope_path is not None
        envelope = json.loads(
            (workspace / result.trace.envelope_path).read_text(encoding="utf-8")
        )
        role_sequence.append(envelope["role_id"])
        _write_current_role_proposal(
            workspace,
            result,
            initial_editor_repetitions=20,
            repair_editor_repetitions=210,
        )
    else:
        raise AssertionError("authorized Gate repair did not terminate")

    assert role_sequence == [
        "scout",
        "screener",
        "claim-ledger",
        "analyst",
        "editor",
        "auditor",
        "editor",
        "auditor",
    ]
    assert result.reason_code == "local_finalization_complete"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.gate_repair_cycles) == 1
    assert len(snapshot.gate_repair_artifact_bindings) == 1
    assert len(snapshot.gate_repair_outcomes) == 1
    cycle = snapshot.gate_repair_cycles[0]
    binding = snapshot.gate_repair_artifact_bindings[0]
    outcome = snapshot.gate_repair_outcomes[0]
    assert cycle.repair_ordinal == 1
    assert binding.prior_artifact.revision == 1
    assert binding.successor_artifact.revision == 2
    assert outcome.disposition == "passed"
    assert not snapshot.repair_cycles
    assert not snapshot.artifact_supersessions
    assert not snapshot.repair_completions
    assert not snapshot.recovery_completions
    assert not snapshot.package_ready_records
    assert not snapshot.approvals
    assert not snapshot.delivery_authorizations
    assert not snapshot.delivery_attempts
    assert not snapshot.delivery_results

    editor_completions = sorted(
        (
            item
            for item in snapshot.stage_transitions
            if item.stage_id == "editor" and item.transition_kind == "complete"
        ),
        key=lambda item: item.result_revision,
    )
    assert len(editor_completions) == 2

    def editor_binding_signature(transition_id: str):
        return {
            (item.artifact_id, item.artifact_revision, item.usage)
            for item in snapshot.stage_artifact_bindings
            if item.transition_id == transition_id
        }

    assert editor_binding_signature(editor_completions[0].transition_id) == {
        ("analyst_draft_snapshot", 1, "consumed"),
        ("audited_brief", 1, "produced"),
    }
    assert editor_binding_signature(editor_completions[1].transition_id) == {
        ("audited_brief", 1, "consumed"),
        ("audited_brief", 2, "produced"),
    }

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, snapshot.run.run_id)
        history = store.load_history()
    revisions = {
        (item.artifact_id, item.revision): item for item in snapshot.artifact_revisions
    }
    prior = revisions[("audited_brief", 1)]
    successor = revisions[("audited_brief", 2)]
    prior_key = ("audited_brief", 1)
    successor_key = ("audited_brief", 2)
    analyst = revisions[("analyst_draft_snapshot", 1)]

    repair_consumed = next(
        item
        for item in snapshot.stage_artifact_bindings
        if item.transition_id == editor_completions[1].transition_id
        and item.usage == "consumed"
    )
    ordinary_consumed = next(
        item
        for item in snapshot.stage_artifact_bindings
        if item.transition_id == editor_completions[0].transition_id
        and item.usage == "consumed"
    )
    submission = next(
        item
        for item in snapshot.owned_artifact_submissions
        if item.submission_id == binding.owned_artifact_submission_id
    )
    lineage_forgeries = (
        replace(snapshot, gate_repair_artifact_bindings=()),
        replace(
            snapshot,
            gate_repair_artifact_bindings=(
                binding.model_copy(
                    update={
                        "gate_repair_id": "GATE-REPAIR-CROSS",
                    }
                ),
            ),
        ),
        replace(
            snapshot,
            gate_repair_artifact_bindings=(
                binding.model_copy(
                    update={
                        "prior_artifact": binding.prior_artifact.model_copy(
                            update={"revision": 2}
                        ),
                    }
                ),
            ),
        ),
        replace(
            snapshot,
            gate_repair_artifact_bindings=(
                binding.model_copy(
                    update={
                        "successor_artifact": binding.successor_artifact.model_copy(
                            update={"revision": 1}
                        ),
                    }
                ),
            ),
        ),
        replace(
            snapshot,
            owned_artifact_submissions=tuple(
                item.model_copy(update={"parent_artifact": binding.successor_artifact})
                if item.submission_id == submission.submission_id
                else item
                for item in snapshot.owned_artifact_submissions
            ),
        ),
        replace(
            snapshot,
            stage_artifact_bindings=tuple(
                item.model_copy(
                    update={
                        "artifact_id": analyst.artifact_id,
                        "artifact_revision": analyst.revision,
                        "artifact_sha256": analyst.sha256,
                    }
                )
                if (
                    item.transition_id == repair_consumed.transition_id
                    and item.position == repair_consumed.position
                )
                else item
                for item in snapshot.stage_artifact_bindings
            ),
        ),
        replace(
            snapshot,
            stage_artifact_bindings=tuple(
                item.model_copy(
                    update={
                        "artifact_id": prior.artifact_id,
                        "artifact_revision": prior.revision,
                        "artifact_sha256": prior.sha256,
                    }
                )
                if (
                    item.transition_id == ordinary_consumed.transition_id
                    and item.position == ordinary_consumed.position
                )
                else item
                for item in snapshot.stage_artifact_bindings
            ),
        ),
    )
    for forged_snapshot in lineage_forgeries:
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            CoreRunDomainVerifier()._verify_snapshot(history, forged_snapshot)

    integrity = RunIntegrityService(workspace)
    assert prior_key in protected_revision_keys(verified)
    observed = workspace_observation_revision_keys(
        verified,
        additional_revisions=(prior,),
    )
    assert prior_key in observed
    assert successor_key in observed
    hard_mismatch = integrity.first_mismatch(
        verified,
        additional_revisions=(prior,),
    )
    assert hard_mismatch is not None
    assert (hard_mismatch[0].artifact_id, hard_mismatch[0].revision) == prior_key

    lineage_observed = workspace_observation_revision_keys(
        verified,
        completion_lineage_revisions=(prior, successor),
    )
    assert prior_key not in lineage_observed
    assert successor_key in lineage_observed
    assert (
        integrity.first_mismatch(
            verified,
            completion_lineage_revisions=(prior, successor),
        )
        is None
    )

    forged_snapshots = (
        replace(snapshot, gate_repair_artifact_bindings=()),
        replace(
            snapshot,
            artifacts=tuple(
                item.model_copy(update={"current_revision": 1})
                if item.artifact_id == "audited_brief"
                else item
                for item in snapshot.artifacts
            ),
        ),
        replace(
            snapshot,
            artifact_revisions=tuple(
                item.model_copy(update={"path": "output/intermediate/other.md"})
                if (item.artifact_id, item.revision) == successor_key
                else item
                for item in snapshot.artifact_revisions
            ),
        ),
        replace(
            snapshot,
            gate_repair_artifact_bindings=(
                binding.model_copy(update={"gate_repair_id": "GATE-REPAIR-CROSS"}),
            ),
        ),
        replace(
            snapshot,
            checkout_revision_members=tuple(
                item.model_copy(
                    update={
                        "artifact_revision": 1,
                        "blob_sha256": prior.sha256,
                        "byte_size": prior.size_bytes,
                    }
                )
                if (item.artifact_id == "audited_brief" and item.artifact_revision == 2)
                else item
                for item in snapshot.checkout_revision_members
            ),
        ),
    )
    for forged_snapshot in forged_snapshots:
        forged = replace(verified, snapshot=forged_snapshot)
        assert prior_key in workspace_observation_revision_keys(forged)
        mismatch = integrity.first_mismatch(forged)
        assert mismatch is not None

    repaired_path = workspace / successor.path
    repaired_path.write_text("tampered repaired brief\n", encoding="utf-8")
    mismatch = integrity.first_mismatch(verified)
    assert mismatch is not None
    assert (mismatch[0].artifact_id, mismatch[0].revision) == successor_key


def test_active_gate_repair_contamination_is_zero_write_human_block(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)

    for _ in range(10):
        result = service.continue_authorized()
        assert result.status == "role_work_required"
        assert result.trace.envelope_path is not None
        envelope = json.loads(
            (workspace / result.trace.envelope_path).read_text(encoding="utf-8")
        )
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            snapshot = store.load_snapshot(envelope["run_id"])
        _write_current_role_proposal(
            workspace,
            result,
            initial_editor_repetitions=20,
            repair_editor_repetitions=210,
        )
        if envelope["role_id"] == "editor" and snapshot.gate_repair_cycles:
            break
    else:
        raise AssertionError("authorized Gate repair was not reserved")

    audited = next(
        item for item in snapshot.artifacts if item.artifact_id == "audited_brief"
    )
    audited_revision = next(
        item
        for item in snapshot.artifact_revisions
        if item.artifact_id == audited.artifact_id
        and item.revision == audited.current_revision
    )
    (workspace / audited_revision.path).write_text(
        "tampered while Gate repair is active\n",
        encoding="utf-8",
    )

    contamination = RunIntegrityService(workspace).inspect(
        IntegrityCheckRequest.model_validate(
            {
                **IntegrityCheckRequest.minimal_example,
                "request_id": "REQ-GATE-REPAIR-CONTAMINATION-001",
                "run_id": envelope["run_id"],
                "expected_store_revision": snapshot.store_revision,
            },
            strict=True,
        )
    )
    assert contamination["status"] == "blocked"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        blocked_revision = store.current_revision

    blocked = service.continue_authorized()
    assert (
        blocked.status,
        blocked.reason_code,
        blocked.trace.next_action.action_kind,
        blocked.trace.next_action.effect_kind,
    ) == (
        "needs_human",
        "gate_repair_failed_after_attempt",
        "human_decision",
        "gate_repair_human_review",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after = store.load_snapshot(envelope["run_id"])
        assert store.current_revision == blocked_revision
    assert after.run_integrity_records[-1].status == "contaminated"
    assert not after.repair_cycles
    assert not after.artifact_supersessions
    assert not after.repair_completions
    assert not after.recovery_completions

    replay = service.continue_authorized()
    assert replay == blocked
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == blocked_revision
