"""Focused M3 authorized runtime-continuation State x Path rows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import stat
import sys
from types import SimpleNamespace

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.contracts import SchemaRegistry
from multi_agent_brief.contracts.v2 import (
    CoreRunNextAction,
    IntegrityCheckRequest,
    TavilyAcquisitionBundleV2,
    TavilyExtractBatchExchange,
    TavilyExtractUrlOutcome,
    TavilySearchTaskExchange,
    TavilyTaskAcquisitionStatus,
)
from multi_agent_brief.control_store import (
    ControlStoreIntegrityError,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.sqlite_store import ControlStoreHistory
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
)
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.integrity import (
    RunIntegrityService,
    protected_revision_keys,
    workspace_observation_revision_keys,
)
from multi_agent_brief.core_run_v2 import verifier as verifier_module
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.product.init_web.submit import InitWebSubmitter
from multi_agent_brief.product.projection_platform import (
    supports_retained_directory_publication,
)
from multi_agent_brief.runtime_host_v2.codex import workspace_codex_adapter_loader
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.contracts import (
    RuntimeSourceAcquisitionRecoveryRequest,
)
from multi_agent_brief.runtime_host_v2 import service as host_service
from multi_agent_brief.runtime_host_v2 import submission as host_submission
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
            "report_type": "management_monthly",
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


def _source_acquisition_failure_evidence(snapshot):
    """Return the single recorded acquisition failure, wherever it sits."""

    failures = [
        event.intake_binding.source_acquisition_failure
        for event in snapshot.events
        if event.intake_binding is not None
        and event.intake_binding.source_acquisition_failure is not None
    ]
    assert len(failures) == 1
    return failures[0]


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
        snapshot = store.load_snapshot(head.current_run_id)
        discovery = snapshot.run_source_discovery_authorizations[0]
        attempt = snapshot.run_source_acquisition_attempt_authorizations[-1]
    return canonical_fingerprint(
        {
            "kind": "discovery_source_pack",
            "run_id": action.run_id,
            "action_fingerprint": action.action_fingerprint,
            "discovery_authorization_id": discovery.authorization_id,
            "attempt_authorization_id": attempt.attempt_authorization_id,
        }
    )


def _tavily_item(*, durable: bool) -> SourceItem:
    return SourceItem(
        source_id="durable" if durable else "snippet",
        source_name="example.com",
        source_type="web_search",
        title="Durable source" if durable else "Snippet result",
        content=("durable provider content" if durable else "discovery snippet only"),
        url=(
            "https://example.com/durable" if durable else "https://example.com/snippet"
        ),
        retrieved_at="2026-07-26T00:00:00Z",
        metadata={
            "backend": "tavily",
            "content_shape": (
                "provider_extract_content" if durable else "search_snippet"
            ),
            "has_raw_content": durable,
            "evidence_quality": "partial_extract" if durable else "snippet",
        },
    )


def _tavily_collection(
    items: list[SourceItem],
    *,
    tasks: list[dict[str, object]],
    query: str | None = None,
    time_range: str = "week",
    domains: tuple[str, ...] | None = None,
) -> WebSearchCollection:
    """Build one spec-bound multi-task bundle for the frozen V3 acquisition.

    Every frozen task gets one primary Search exchange returning ``items``;
    the overrides ``query``/``time_range``/``domains`` forge request bytes that
    no longer match the frozen spec (rejection-path tests).
    """
    search_rows = [
        {
            "title": item.title,
            "url": item.url,
            "content": "discovery snippet",
            "published_date": item.published_at or "",
            "score": 0.9,
        }
        for item in items
    ]
    search_records: list[TavilySearchTaskExchange] = []
    for task in tasks:
        search_payload: dict[str, object] = {
            "query": query if query is not None else task["query"],
            "max_results": 20,
            "topic": task["topic"],
            "search_depth": "advanced",
            "include_answer": False,
            "include_raw_content": False,
            "auto_parameters": False,
            "time_range": time_range,
        }
        task_domains = domains if domains is not None else tuple(task["domains"])
        if task_domains:
            search_payload["include_domains"] = sorted(set(task_domains))
        search_exchange = TavilyBackend._exchange(
            "search",
            canonical_json_bytes(search_payload),
            response_body=canonical_json_bytes({"results": search_rows}),
            status_code=200,
        )
        search_records.append(
            TavilySearchTaskExchange.model_validate(
                {
                    "task_id": task["task_id"],
                    "phase": "primary",
                    "status": "succeeded" if items else "empty",
                    "exchange": search_exchange.model_dump(mode="json"),
                    "discovered_urls": sorted({item.url for item in items}),
                },
                strict=True,
            )
        )
    task_ids = [str(task["task_id"]) for task in tasks]
    if not items:
        bundle = TavilyAcquisitionBundleV2.model_validate(
            {
                "schema_version": TavilyAcquisitionBundleV2.schema_id,
                "provider_id": "tavily",
                "status": "failed",
                "searches": [record.model_dump(mode="json") for record in search_records],
                "extract_batches": [],
                "unique_urls": [],
                "task_statuses": [
                    TavilyTaskAcquisitionStatus.model_validate(
                        {
                            "task_id": task_id,
                            "primary_search_ordinal": ordinal,
                            "discovered_unique_url_count": 0,
                            "extracted_success_count": 0,
                            "minimum_extract_successes": 1,
                            "status": "coverage_insufficient",
                        },
                        strict=True,
                    ).model_dump(mode="json")
                    for ordinal, task_id in enumerate(task_ids, start=1)
                ],
            },
            strict=True,
        )
        return WebSearchCollection(
            items=(),
            raw_response=canonical_json_bytes(bundle.model_dump(mode="json")),
            status_code=200,
        )

    extract_urls = sorted({item.url for item in items})
    extract_request = canonical_json_bytes(
        {
            "urls": extract_urls,
            "chunks_per_source": 5,
            "extract_depth": "advanced",
            "include_images": False,
            "include_favicon": False,
            "format": "markdown",
            "include_usage": True,
        }
    )
    successes = [
        {"url": item.url, "raw_content": item.content}
        for item in items
        if item.metadata.get("has_raw_content") is True
    ]
    failures = [
        {"url": item.url, "error": "test_provider_failure"}
        for item in items
        if item.metadata.get("has_raw_content") is not True
    ]
    extract_exchange = TavilyBackend._exchange(
        "extract",
        extract_request,
        response_body=canonical_json_bytes(
            {"results": successes, "failed_results": failures}
        ),
        status_code=200,
    )
    outcomes: list[TavilyExtractUrlOutcome] = []
    for row in sorted([*successes, *failures], key=lambda value: value["url"]):
        content = row.get("raw_content")
        payload = {
            "url": row["url"],
            "status": "succeeded" if content else "provider_failed",
            "response_item_sha256": hashlib.sha256(
                canonical_json_bytes(row)
            ).hexdigest(),
        }
        if isinstance(content, str):
            content_bytes = content.strip().encode("utf-8")
            payload.update(
                {
                    "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
                    "content_size_bytes": len(content_bytes),
                }
            )
        outcomes.append(TavilyExtractUrlOutcome.model_validate(payload, strict=True))
    batch_status = (
        "all_failed"
        if not successes
        else "succeeded"
        if len(successes) == len(extract_urls)
        else "partial"
    )
    extract_batch = TavilyExtractBatchExchange.model_validate(
        {
            "phase": "primary",
            "batch_ordinal": 1,
            "status": batch_status,
            "exchange": extract_exchange.model_dump(mode="json"),
            "urls": extract_urls,
            "outcomes": [item.model_dump(mode="json") for item in outcomes],
        },
        strict=True,
    )
    covered = bool(successes)
    bundle = TavilyAcquisitionBundleV2.model_validate(
        {
            "schema_version": TavilyAcquisitionBundleV2.schema_id,
            "provider_id": "tavily",
            "status": "complete" if covered else "failed",
            "searches": [record.model_dump(mode="json") for record in search_records],
            "extract_batches": [extract_batch.model_dump(mode="json")],
            "unique_urls": extract_urls,
            "task_statuses": [
                TavilyTaskAcquisitionStatus.model_validate(
                    {
                        "task_id": task_id,
                        "primary_search_ordinal": ordinal,
                        "discovered_unique_url_count": len(extract_urls),
                        "extracted_success_count": len(successes),
                        "minimum_extract_successes": 1,
                        "status": "covered" if covered else "coverage_insufficient",
                    },
                    strict=True,
                ).model_dump(mode="json")
                for ordinal, task_id in enumerate(task_ids, start=1)
            ],
        },
        strict=True,
    )
    search_by_url = {row["url"]: row for row in search_rows}
    extract_by_url = {row["url"]: row for row in successes}
    normalized = [
        replace(
            item,
            metadata={
                **item.metadata,
                "provider_projection": {
                    "schema_version": (
                        "briefloop.tavily_extract_source_projection.v2"
                    ),
                    "discovery_task_ids": task_ids,
                    "search_result": search_by_url[item.url],
                    "extract_result": extract_by_url[item.url],
                },
            },
        )
        for item in items
        if item.metadata.get("has_raw_content") is True
    ]
    return WebSearchCollection(
        items=tuple(normalized),
        raw_response=canonical_json_bytes(bundle.model_dump(mode="json")),
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


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_missing_runtime_secret_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    _advance_discovery_to_source_action(workspace)
    (workspace / ".env").unlink()
    revision = _revision(workspace)
    database_before = (workspace / "briefloop.db").read_bytes()
    calls = 0

    def collect(_provider, _query, _config):
        nonlocal calls
        calls += 1
        return _tavily_collection(
            [_tavily_item(durable=True)], tasks=_config["search_tasks"]
        )

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)

    result = _service(workspace).continue_authorized()

    assert result.status == "needs_attention"
    assert result.reason_code == "source_provider_secret_unavailable"
    assert calls == 0
    assert _revision(workspace) == revision
    assert (workspace / "briefloop.db").read_bytes() == database_before
    assert not (workspace / ".briefloop-source-acquisition.lock").exists()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.run_source_acquisition_attempt_authorizations) == 1
    assert not [
        event
        for event in snapshot.events
        if event.intake_binding is not None
        and event.intake_binding.source_acquisition_failure is not None
    ]


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_authority_rejects_public_source_files_before_sibling_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entrypoint = "source"
    workspace = _discovery_workspace(tmp_path)
    _advance_discovery_to_source_action(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        run_id = head.current_run_id
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
        return _tavily_collection(
            [_tavily_item(durable=True)], tasks=_config["search_tasks"]
        )

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
        return _tavily_collection(
            [_tavily_item(durable=True)], tasks=_config["search_tasks"]
        )

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


@pytest.mark.parametrize(
    "stage_damage",
    ["missing", "dangling", "looping", "regular_file", "tampered"],
)
@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_active_invocation_missing_vs_tampered_stage_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage_damage: str,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    env_path = workspace / ".env"
    env_before = env_path.read_bytes()
    env_mtime_before = env_path.stat().st_mtime_ns
    provider_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        return _tavily_collection(
            [_tavily_item(durable=True)], tasks=_config["search_tasks"]
        )

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
    elif stage_damage in {"dangling", "looping", "regular_file"}:
        stage_root.rename(stage_root.with_name(f"{stage_root.name}.saved"))
        if stage_damage == "dangling":
            stage_root.symlink_to(stage_root.with_name(f"{stage_root.name}.absent"))
        elif stage_damage == "looping":
            stage_root.symlink_to(stage_root.name)
        else:
            stage_root.write_bytes(b"unsafe non-directory stage root")
    else:
        next(stage_root.glob("sources/*/source_content.bin")).write_bytes(
            b"tampered staged content"
        )
    unsafe_root_identity = None
    unsafe_link_target = None
    if stage_damage != "missing":
        unsafe_metadata = stage_root.lstat()
        unsafe_root_identity = (
            unsafe_metadata.st_dev,
            unsafe_metadata.st_ino,
            unsafe_metadata.st_mode,
        )
        if stat.S_ISLNK(unsafe_metadata.st_mode):
            unsafe_link_target = os.readlink(stage_root)
    revision_before_recovery = _revision(workspace)
    database_before_recovery = (workspace / "briefloop.db").read_bytes()

    def forbidden_effect(*_args, **_kwargs):
        pytest.fail("stage recovery must not inspect credential or provider")

    monkeypatch.setattr(host_service, "capability_profile", forbidden_effect)
    monkeypatch.setattr(host_service, "known_env_key_is_set", forbidden_effect)
    monkeypatch.setattr(
        WebSearchProvider,
        "collect_with_response",
        forbidden_effect,
    )

    if stage_damage == "missing":
        recovered = _service(workspace).apply_current(_service(workspace).next_action())
        assert recovered.status == "rejected_recorded"
        assert recovered.next_action.effect_kind == "source_acquisition_recovery"
    else:
        with pytest.raises(
            RuntimeHostError,
            match="source_acquisition_outcome_unknown",
        ):
            _service(workspace).apply_current(_service(workspace).next_action())

    assert provider_calls == 1
    assert env_path.read_bytes() == env_before
    assert env_path.stat().st_mtime_ns == env_mtime_before
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
    failures = [
        item.intake_binding.source_acquisition_failure
        for item in snapshot.events
        if item.intake_binding is not None
        and item.intake_binding.source_acquisition_failure is not None
    ]
    if stage_damage == "missing":
        assert active == []
        assert len(failures) == 1
        assert failures[0].failure_class == "provider_response_unavailable"
        assert failures[0].provider_response_artifact is None
    else:
        assert len(active) == 1
        assert failures == []
        assert _revision(workspace) == revision_before_recovery
        assert (workspace / "briefloop.db").read_bytes() == database_before_recovery
        unsafe_metadata = stage_root.lstat()
        assert (
            unsafe_metadata.st_dev,
            unsafe_metadata.st_ino,
            unsafe_metadata.st_mode,
        ) == unsafe_root_identity
        if unsafe_link_target is not None:
            assert os.readlink(stage_root) == unsafe_link_target
    if stage_damage != "missing":
        host_submission._discard_path(stage_root)
    saved_stage = stage_root.with_name(f"{stage_root.name}.saved")
    if saved_stage.is_dir():
        host_submission._discard_path(saved_stage)


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_extract_all_failed_is_terminal_without_automatic_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    provider_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        return _tavily_collection(
            [_tavily_item(durable=False)], tasks=_config["search_tasks"]
        )

    monkeypatch.setattr(
        WebSearchProvider,
        "collect_with_response",
        collect,
    )
    action = _advance_discovery_to_source_action(workspace)
    service = _service(workspace)

    result = service.apply_current(action)

    assert result.status == "rejected_recorded"
    assert result.next_action.effect_kind == "source_acquisition_recovery"
    assert (
        result.next_action.reason_code
        == "source_acquisition_recovery_decision_required"
    )

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()
    evidence = _source_acquisition_failure_evidence(snapshot)
    assert evidence is not None
    assert evidence.failure_class == "provider_results_without_durable_content"
    assert evidence.result_count == 1
    assert evidence.durable_content_count == 0
    assert evidence.claims_eligible_count == 0
    assert evidence.provider_response_artifact is not None
    assert provider_calls == 1
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
    database = (workspace / "briefloop.db").read_bytes()
    revision = _revision(workspace)
    (workspace / ".env").unlink()

    replay = service.continue_authorized()

    assert replay.status == "needs_human"
    assert replay.reason_code == "source_acquisition_recovery_decision_required"
    assert provider_calls == 1
    assert _revision(workspace) == revision
    assert (workspace / "briefloop.db").read_bytes() == database


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_next_attempt_requires_exact_human_authorization_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    env_bytes = (workspace / ".env").read_bytes()
    provider_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        return _tavily_collection(
            [] if provider_calls == 1 else [_tavily_item(durable=True)],
            tasks=_config["search_tasks"],
        )

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    _advance_discovery_to_source_action(workspace)
    service = _service(workspace)

    failed = service.continue_authorized()

    assert failed.status == "needs_human"
    assert provider_calls == 1
    action = service.next_action()
    assert action.effect_kind == "source_acquisition_recovery"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.run_source_acquisition_attempt_authorizations) == 1
    previous = snapshot.run_source_acquisition_attempt_authorizations[0]
    assert (
        action.source_acquisition_attempt_authorization_id
        == previous.attempt_authorization_id
    )
    recovery = RuntimeSourceAcquisitionRecoveryRequest.model_validate(
        {
            "schema_version": (RuntimeSourceAcquisitionRecoveryRequest.schema_id),
            "request_id": "REQ-HUMAN-TAVILY-ATTEMPT-002",
            "run_id": action.run_id,
            "expected_store_revision": action.store_revision,
            "expected_action_fingerprint": action.action_fingerprint,
            "decision": "authorize_next_tavily_attempt",
            "previous_attempt_authorization_id": (
                action.source_acquisition_attempt_authorization_id
            ),
            "human_confirmation": True,
            "provider_cost_status": "not_reported_acknowledged",
            "human_source_pack": None,
        },
        strict=True,
    )

    authorized = service.apply_current(action, human_request=recovery)
    replayed = service.apply_current(action, human_request=recovery)

    assert authorized.status == "committed"
    assert replayed.status == "replayed"
    assert provider_calls == 1
    conflicting = recovery.model_copy(
        update={
            "previous_attempt_authorization_id": (
                "SOURCE-ACQUIRE-ATTEMPT-AUTH-CONFLICT"
            )
        }
    )
    with pytest.raises(RuntimeHostError, match="submission_replay_conflict"):
        service.apply_current(action, human_request=conflicting)
    assert provider_calls == 1
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    attempts = snapshot.run_source_acquisition_attempt_authorizations
    assert [item.attempt_ordinal for item in attempts] == [1, 2]
    assert (
        attempts[1].previous_attempt_authorization_id
        == attempts[0].attempt_authorization_id
    )
    assert attempts[1].accepted_transaction_id == recovery.request_id
    second_action = service.next_action()
    assert second_action.effect_kind == "source_acquire"
    assert (
        second_action.source_acquisition_attempt_authorization_id
        == attempts[1].attempt_authorization_id
    )

    promoted = service.continue_authorized()

    assert promoted.status == "role_work_required", promoted.reason_code
    assert provider_calls == 2
    assert (workspace / ".env").read_bytes() == env_bytes
    replay = service.apply_current(second_action)
    assert replay.status == "replayed"
    assert provider_calls == 2
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert len(snapshot.run_execution_authorizations) == 1
    assert len(snapshot.sources) == 1


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_attempt_is_cross_process_serialized_before_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    _advance_discovery_to_source_action(workspace)
    action = _service(workspace).next_action()
    process_context = multiprocessing.get_context("fork")
    apply_entered = process_context.Barrier(2)
    provider_entered = process_context.Event()
    provider_release = process_context.Event()
    provider_calls = process_context.Value("i", 0)
    outcomes = process_context.Queue()

    def collect(_provider, _query, _config):
        with provider_calls.get_lock():
            provider_calls.value += 1
        provider_entered.set()
        assert provider_release.wait(timeout=10)
        return _tavily_collection(
            [_tavily_item(durable=True)], tasks=_config["search_tasks"]
        )

    original_apply = RuntimeHostService._apply_discovery_source_acquire

    def synchronized_apply(self, current, current_action, **kwargs):
        if not kwargs.get("_acquisition_lock_held", False):
            apply_entered.wait(timeout=10)
        return original_apply(self, current, current_action, **kwargs)

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    monkeypatch.setattr(
        RuntimeHostService,
        "_apply_discovery_source_acquire",
        synchronized_apply,
    )

    def run_one() -> None:
        try:
            result = _service(workspace).apply_current(action)
        except RuntimeHostError as exc:
            outcomes.put(("error", str(exc)))
        else:
            outcomes.put(("result", result.status))

    processes = [
        process_context.Process(target=run_one),
        process_context.Process(target=run_one),
    ]
    for process in processes:
        process.start()
    assert provider_entered.wait(timeout=10)
    assert all(process.is_alive() for process in processes)
    provider_release.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    observed = sorted(outcomes.get(timeout=2) for _ in processes)
    assert provider_calls.value == 1
    assert observed == [
        ("error", "runtime_action_stale"),
        ("result", "committed"),
    ]
    replay = _service(workspace).apply_current(action)
    assert replay.status == "replayed"
    assert provider_calls.value == 1


@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_rehashed_bundle_cannot_change_frozen_search_direction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _discovery_workspace(tmp_path)
    provider_calls = 0

    def collect(_provider, _query, _config):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls != 1:
            pytest.fail("frozen-spec rejection must not redial the provider")
        return _tavily_collection(
            [_tavily_item(durable=True)],
            tasks=_config["search_tasks"],
            query="unapproved replacement query",
            time_range="month",
            domains=("unapproved.example",),
        )

    monkeypatch.setattr(WebSearchProvider, "collect_with_response", collect)
    action = _advance_discovery_to_source_action(workspace)
    service = _service(workspace)

    rejected = service.apply_current(action)

    assert rejected.status == "rejected_recorded"
    assert rejected.next_action.reason_code == (
        "source_acquisition_recovery_decision_required"
    )

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
    assert snapshot.sources == ()
    assert snapshot.run_execution_authorizations == ()
    assert _source_acquisition_failure_evidence(snapshot) is not None
    assert provider_calls == 1
    database = (workspace / "briefloop.db").read_bytes()
    revision = snapshot.store_revision
    (workspace / ".env").unlink()

    replay = service.continue_authorized()

    assert replay.status == "needs_human"
    assert replay.reason_code == "source_acquisition_recovery_decision_required"
    assert provider_calls == 1
    assert _revision(workspace) == revision
    assert (workspace / "briefloop.db").read_bytes() == database


@pytest.mark.parametrize("echo_kind", ["secret", "sha256"])
@_REQUIRES_RETAINED_PUBLICATION
def test_discovery_provider_credential_echo_keeps_only_value_free_failure_bundle(
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

    assert calls == 40  # 20 primary + 20 conditional backfill Search calls
    assert result.status == "needs_human"
    assert result.reason_code == "source_acquisition_recovery_decision_required"
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
    assert len(snapshot.artifacts) == len(artifacts_before) + 1
    evidence = _source_acquisition_failure_evidence(snapshot)
    assert evidence is not None
    assert evidence.failure_class == "provider_search_failed"
    assert evidence.provider_status_class == "acquisition_bundle_retained"
    assert evidence.provider_response_artifact is not None
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
            "source_acquisition_attempt_authorization_id": None,
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
    initial_editor_reader_issue: str | None = None,
    repair_audit_decision: str = "pass",
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
        if role_id == "editor" and not repairing:
            if initial_editor_reader_issue == "residue":
                body += "\nClaim Ledger reference CL-0001 must not reach readers.\n"
            elif initial_editor_reader_issue == "malformed":
                body += "\n<!-- briefloop:projectable-reader-start --> trailing\n"
            elif initial_editor_reader_issue == "empty":
                body = (
                    "<!-- briefloop:projectable-reader-start -->\n"
                    + body
                    + "<!-- briefloop:projectable-reader-end -->\n"
                )
            elif initial_editor_reader_issue is not None:
                raise AssertionError(initial_editor_reader_issue)
        (
            scratch
            / ("analyst_draft.md" if role_id == "analyst" else "audited_brief.md")
        ).write_text(
            body,
            encoding="utf-8",
        )
        return
    elif role_id == "auditor":
        repairing = bool(snapshot.gate_repair_cycles)
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
            decision=repair_audit_decision if repairing else "pass",
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
    monkeypatch: pytest.MonkeyPatch,
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
    with monkeypatch.context() as patch:
        patch.setattr(
            verifier_module,
            "audit_promotion_allows_stage_completion",
            lambda _promotion: False,
        )
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            with pytest.raises(
                CoreRunError,
                match="control_store_integrity_invalid",
            ):
                CoreRunDomainVerifier().verify(store, snapshot.run.run_id)
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


def test_negative_audit_after_gate_repair_routes_stable_human_review(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    role_sequence: list[str] = []

    for _ in range(12):
        result = service.continue_authorized()
        if result.status == "needs_human":
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
            repair_audit_decision="fail",
        )
    else:
        raise AssertionError("negative repaired audit did not reach Human review")

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
    assert (
        result.status,
        result.reason_code,
        result.trace.next_action.action_kind,
        result.trace.next_action.effect_kind,
    ) == (
        "needs_human",
        "gate_repair_failed_after_attempt",
        "human_decision",
        "gate_repair_human_review",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        snapshot = store.load_snapshot(head.current_run_id)
        before_revision = store.current_revision
    assert len(snapshot.gate_repair_cycles) == 1
    assert len(snapshot.gate_repair_outcomes) == 1
    assert snapshot.gate_repair_outcomes[0].disposition == "passed"
    assert not snapshot.finalizations
    assert (
        next(
            item for item in snapshot.stage_states if item.stage_id == "auditor"
        ).status
        == "ready"
    )

    replay = service.continue_authorized()
    assert (
        replay.status,
        replay.reason_code,
        replay.store_revision,
        replay.trace.next_action,
    ) == (
        result.status,
        result.reason_code,
        result.store_revision,
        result.trace.next_action,
    )
    assert replay.trace.transaction_ids == []
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_revision


def test_reader_projection_issue_routes_editor_repair_before_finalize(
    tmp_path: Path,
) -> None:
    reader_issue = "residue"
    finding_type = "reader_projection_residue"
    expected_metadata = {
        "bare_claim_id_count": 1,
        "process_wording_count": 1,
        "reader_artifact_id": "reader_brief",
        "residue_kinds": ["bare_claim_id", "process_wording"],
    }
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
            initial_editor_reader_issue=reader_issue,
        )
    else:
        raise AssertionError("reader-projection Gate repair did not terminate")

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
        reader_record = next(
            item for item in snapshot.artifacts if item.artifact_id == "reader_brief"
        )
        reader_bytes = store.read_artifact_revision_bytes(
            head.current_run_id,
            "reader_brief",
            reader_record.current_revision,
        )

    projection_findings = [
        item for item in snapshot.gate_findings if item.finding_type == finding_type
    ]
    assert len(projection_findings) == 1
    finding = projection_findings[0]
    assert finding.blocking_level == "blocking"
    assert finding.gate_id == "final_abstract_quality"
    assert finding.repair_owner == "editor"
    assert finding.stage_id == "editor"
    assert finding.artifact_id == "audited_brief"
    assert finding.claim_id is None
    assert finding.source_id is None
    assert finding.metadata == expected_metadata
    assert len(snapshot.gate_repair_cycles) == 1
    assert len(snapshot.gate_repair_outcomes) == 1
    assert snapshot.gate_repair_outcomes[0].disposition == "passed"
    assert len(snapshot.finalize_renders) == 1
    assert len(snapshot.finalizations) == 1
    reader_text = reader_bytes.decode("utf-8")
    assert "Claim Ledger" not in reader_text
    assert "CL-0001" not in reader_text


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
