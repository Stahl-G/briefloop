from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path

import pytest
import yaml

from tests.test_core_run_v2_terminal import _finalize_ready_workspace

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.contracts.v2 import (
    DeliveryAuthorizationRequest,
    InternalApprovalRequest,
    RuntimeAdapterBinding,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.runtime_host_v2 import RuntimeHostError
from multi_agent_brief.runtime_host_v2.contracts import LocalRunGateSummary
from multi_agent_brief.runtime_host_v2.initialization import initialize_or_open_runtime
from multi_agent_brief.runtime_host_v2.projections import (
    build_local_run_presentation,
    build_store_quality_projection,
    build_store_status_projection,
)
from multi_agent_brief.runtime_host_v2.service import RuntimeHostService
from multi_agent_brief.runtime_host_v2.source_routes import collect_frozen_sources
from multi_agent_brief.runtime_host_v2.scratch import materialize_host_bytes
from multi_agent_brief.sources.base import SourceItem, SourceQuery
from multi_agent_brief.sources.web_search import (
    WebSearchCollection,
    WebSearchProvider,
)
from multi_agent_brief.intake_v2.policy import evaluate_source_eligibility
from multi_agent_brief.workspace.init_profile import InitProfile


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    ids = iter(("workspace", "run"))
    create_workspace(
        workspace,
        InitProfile(
            company="ExampleCo",
            industry="manufacturing",
            brief_title="ExampleCo weekly brief",
            task_objective="Prepare the weekly manufacturing brief.",
            audience="management",
            audience_profile="management",
            focus_areas=["operations", "policy"],
            output_formats=["markdown", "docx"],
            web_search_mode="disabled",
            web_search_enabled=False,
        ),
        report_date_factory=lambda: date(2026, 7, 19),
        identity_factory=lambda: next(ids),
    )
    return workspace


@pytest.mark.parametrize(
    "link_position", ["scratch", "invocation", "intermediate", "final"]
)
def test_host_owned_materialization_never_follows_workspace_symlinks(
    tmp_path: Path,
    link_position: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    relative = "scratch/REQ-HOST/nested/input.json"
    outside_target = outside / "input.json"
    if link_position == "scratch":
        (workspace / "scratch").symlink_to(outside, target_is_directory=True)
    elif link_position == "invocation":
        (workspace / "scratch").mkdir()
        (workspace / "scratch" / "REQ-HOST").symlink_to(
            outside,
            target_is_directory=True,
        )
    elif link_position == "intermediate":
        (workspace / "scratch" / "REQ-HOST").mkdir(parents=True)
        (workspace / "scratch" / "REQ-HOST" / "nested").symlink_to(
            outside,
            target_is_directory=True,
        )
    else:
        (workspace / "scratch" / "REQ-HOST" / "nested").mkdir(parents=True)
        outside_target.write_bytes(b"outside-original")
        (workspace / relative).symlink_to(outside_target)

    with pytest.raises(RuntimeHostError, match="runtime_deterministic_input_invalid"):
        materialize_host_bytes(
            workspace,
            relative,
            b"host-payload",
            error_code="runtime_deterministic_input_invalid",
        )

    if link_position == "final":
        assert outside_target.read_bytes() == b"outside-original"
    else:
        assert not outside_target.exists()


def test_host_owned_materialization_is_exactly_replayable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relative = "scratch/REQ-HOST/input.json"

    first = materialize_host_bytes(
        workspace,
        relative,
        b"host-payload",
        error_code="runtime_deterministic_input_invalid",
    )
    replay = materialize_host_bytes(
        workspace,
        relative,
        b"host-payload",
        error_code="runtime_deterministic_input_invalid",
    )
    assert replay == first
    assert first.read_bytes() == b"host-payload"

    with pytest.raises(RuntimeHostError, match="runtime_deterministic_input_invalid"):
        materialize_host_bytes(
            workspace,
            relative,
            b"different-payload",
            error_code="runtime_deterministic_input_invalid",
        )
    assert first.read_bytes() == b"host-payload"


def _external_web_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "external-workspace"
    ids = iter(("external-workspace", "external-run"))
    create_workspace(
        workspace,
        InitProfile(
            company="ExampleCo",
            industry="manufacturing",
            brief_title="ExampleCo weekly brief",
            task_objective="Prepare the weekly manufacturing brief.",
            audience="management",
            audience_profile="management",
            focus_areas=["operations", "policy"],
            output_formats=["markdown", "docx"],
            web_search_mode="external_api",
            web_search_enabled=True,
            search_backend="tavily",
        ),
        report_date_factory=lambda: date(2026, 7, 19),
        identity_factory=lambda: next(ids),
    )
    return workspace


def _adapter(run_id: str) -> RuntimeAdapterBinding:
    payload = deepcopy(RuntimeAdapterBinding.minimal_example)
    payload.update(
        run_id=run_id,
        runtime="codex",
        adapter_id="briefloop-codex-controlstore",
        role_ids=[
            "analyst",
            "auditor",
            "claim-ledger",
            "editor",
            "scout",
            "screener",
            "source-planner",
            "source-provider",
        ],
        supported_role_topologies=["default", "single_session", "strict"],
    )
    payload.pop("binding_fingerprint", None)
    payload["binding_fingerprint"] = canonical_fingerprint(payload)
    return RuntimeAdapterBinding.model_validate(payload, strict=True)


def _adapter_without_single_session(run_id: str) -> RuntimeAdapterBinding:
    payload = _adapter(run_id).model_dump(mode="json", exclude_unset=False)
    payload["supported_role_topologies"] = ["default", "strict"]
    payload.pop("binding_fingerprint")
    payload["binding_fingerprint"] = canonical_fingerprint(payload)
    return RuntimeAdapterBinding.model_validate(payload, strict=True)


def test_fresh_runtime_initializes_once_and_existing_store_ignores_input_drift(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    config = yaml.safe_load((workspace / "config.yaml").read_text(encoding="utf-8"))
    assert config["controlstore_v2"]["role_topology"] == "single_session"
    first = initialize_or_open_runtime(workspace, adapter_loader=_adapter)

    assert first.initialized is True
    assert first.verified.snapshot.workspace_id == "WS-workspace"
    assert first.verified.snapshot.run.run_id == "RUN-run"
    assert first.action.action_kind == "deterministic"
    revision = first.verified.snapshot.store_revision

    (workspace / "config.yaml").write_text("not: the authority\n", encoding="utf-8")
    (workspace / "sources.yaml").write_text("also: inert\n", encoding="utf-8")
    reopened = initialize_or_open_runtime(
        workspace,
        adapter_loader=_adapter,
    )
    assert reopened.initialized is False
    assert reopened.verified.snapshot.store_revision == revision
    assert reopened.action == first.action


def test_external_source_plan_freezes_executable_non_secret_requests(
    tmp_path: Path,
) -> None:
    workspace = _external_web_workspace(tmp_path)
    first = initialize_or_open_runtime(workspace, adapter_loader=_adapter)
    route = next(
        item
        for item in first.verified.source_plan.routes
        if item.route_id == "web-search"
    )
    spec = route.acquisition_spec
    assert spec is not None and spec.kind == "web_search"
    assert spec.provider_id == "tavily"
    assert [item.query for item in spec.requests] == [
        "Prepare the weekly manufacturing brief."
    ]
    assert all(item.max_results == 5 for item in spec.requests)
    assert "TAVILY_API_KEY" not in str(spec.model_dump(mode="json"))
    fingerprint = first.verified.source_plan.source_plan_fingerprint

    (workspace / "sources.yaml").write_text("changed: true\n", encoding="utf-8")
    reopened = initialize_or_open_runtime(workspace, adapter_loader=_adapter)
    reopened_route = next(
        item
        for item in reopened.verified.source_plan.routes
        if item.route_id == "web-search"
    )
    assert reopened.verified.source_plan.source_plan_fingerprint == fingerprint
    assert reopened_route.acquisition_spec == spec


def test_tavily_route_maps_only_verified_raw_content_to_eligible_extract(
    tmp_path: Path,
) -> None:
    workspace = _external_web_workspace(tmp_path)
    initialized = initialize_or_open_runtime(workspace, adapter_loader=_adapter)
    route = next(
        item
        for item in initialized.verified.source_plan.routes
        if item.route_id == "web-search"
    )

    projections = (
        {
            "title": "Durable",
            "url": "https://example.com/durable",
            "snippet": "discovery snippet",
            "raw_content": "retrieved durable page extract",
            "published_date": "",
            "score": 0.9,
        },
        {
            "title": "Snippet",
            "url": "https://example.com/snippet",
            "snippet": "search snippet",
            "raw_content": None,
            "published_date": "",
            "score": 0.7,
        },
    )
    items = tuple(
        SourceItem(
            source_id=source_id,
            source_name="example.com",
            source_type="web_search",
            title=projection["title"],
            content=projection["raw_content"] or projection["snippet"],
            url=projection["url"],
            metadata={
                "backend": "tavily",
                "content_shape": (
                    "provider_raw_content"
                    if projection["raw_content"]
                    else "search_snippet"
                ),
                "has_raw_content": bool(projection["raw_content"]),
                "evidence_quality": (
                    "partial_extract" if projection["raw_content"] else "snippet"
                ),
                "provider_projection": projection,
            },
        )
        for source_id, projection in zip(
            ("DURABLE", "SNIPPET"), projections, strict=True
        )
    )
    response = {
        "results": [
            {
                "title": item["title"],
                "url": item["url"],
                "content": item["snippet"],
                "raw_content": item["raw_content"],
                "published_date": item["published_date"],
                "score": item["score"],
            }
            for item in projections
        ]
    }
    provider = WebSearchProvider()
    provider.collect_with_response = lambda *_args, **_kwargs: WebSearchCollection(
        items=items,
        raw_response=json.dumps(response, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        ),
        status_code=200,
    )

    materials = collect_frozen_sources(
        workspace,
        run_id=initialized.verified.snapshot.run.run_id,
        invocation_id="INV-DURABLE-CONTENT",
        route=route,
        provider_factory=lambda _kind: provider,
    )

    by_title = {item.proposal.title: item for item in materials}
    durable = by_title["Durable"]
    assert durable.content == b"retrieved durable page extract"
    assert durable.proposal.origin_type == "provider_response"
    assert durable.proposal.acquisition_method == "provider_extract"
    assert durable.proposal.material_kind == "partial_extract"
    assert evaluate_source_eligibility(
        durable.proposal,
        raw_payload_present=True,
    ) == (True, "eligible_durable_source_content")
    snippet = by_title["Snippet"]
    assert snippet.proposal.origin_type == "search_snippet_only"
    assert snippet.proposal.acquisition_method == "provider_search"
    assert snippet.proposal.material_kind == "search_snippet"
    assert evaluate_source_eligibility(
        snippet.proposal,
        raw_payload_present=True,
    ) == (False, "ineligible_search_snippet")


def test_tavily_route_does_not_trust_raw_content_marker_alone(
    tmp_path: Path,
) -> None:
    workspace = _external_web_workspace(tmp_path)
    initialized = initialize_or_open_runtime(workspace, adapter_loader=_adapter)
    route = next(
        item
        for item in initialized.verified.source_plan.routes
        if item.route_id == "web-search"
    )

    projection = {
        "title": "Forged marker",
        "url": "https://example.com/forged",
        "snippet": "search snippet",
        "raw_content": None,
        "published_date": "",
        "score": 0.5,
    }
    forged = SourceItem(
        source_id="FORGED",
        source_name="example.com",
        source_type="web_search",
        title="Forged marker",
        content="search snippet",
        url="https://example.com/forged",
        metadata={
            "backend": "other",
            "content_shape": "provider_raw_content",
            "has_raw_content": True,
            "evidence_quality": "partial_extract",
            "provider_projection": projection,
        },
    )
    provider = WebSearchProvider()
    provider.collect_with_response = lambda *_args, **_kwargs: WebSearchCollection(
        items=(forged,),
        raw_response=json.dumps(
            {
                "results": [
                    {
                        "title": projection["title"],
                        "url": projection["url"],
                        "content": projection["snippet"],
                        "raw_content": projection["raw_content"],
                        "published_date": projection["published_date"],
                        "score": projection["score"],
                    }
                ]
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        status_code=200,
    )

    material = collect_frozen_sources(
        workspace,
        run_id=initialized.verified.snapshot.run.run_id,
        invocation_id="INV-FORGED-CONTENT",
        route=route,
        provider_factory=lambda _kind: provider,
    )[0]

    assert material.proposal.material_kind == "search_snippet"
    assert evaluate_source_eligibility(
        material.proposal,
        raw_payload_present=True,
    ) == (False, "ineligible_search_snippet")


def test_tavily_source_plan_uses_direction_and_fixed_bounds(
    tmp_path: Path,
) -> None:
    web_fingerprints: list[tuple[str, str]] = []
    for name, preferred_domains in (
        ("base", ["example.com"]),
        ("domain", ["example.org"]),
    ):
        workspace = _external_web_workspace(tmp_path / name)
        path = workspace / "sources.yaml"
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        payload["web_search"]["search_tasks"] = [
            {"query": "ignored legacy task", "domains": ["ignored.example"]}
        ]
        payload["web_search"]["news_source_domains"]["preferred_domains"] = (
            preferred_domains
        )
        payload["web_search"]["max_results"] = 5
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        initialized = initialize_or_open_runtime(workspace, adapter_loader=_adapter)
        route = next(
            item
            for item in initialized.verified.source_plan.routes
            if item.route_id == "web-search"
        )
        assert route.acquisition_spec is not None
        web_fingerprints.append(
            (
                route.acquisition_spec.acquisition_spec_fingerprint,
                route.route_fingerprint,
            )
        )
    assert len(set(web_fingerprints)) == len(web_fingerprints)

    invalid = _external_web_workspace(tmp_path / "invalid-bounds")
    invalid_path = invalid / "sources.yaml"
    invalid_payload = yaml.safe_load(invalid_path.read_text(encoding="utf-8"))
    invalid_payload["web_search"]["max_results"] = 9
    invalid_path.write_text(
        yaml.safe_dump(invalid_payload, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeHostError, match="runtime_initialization_input_invalid"):
        initialize_or_open_runtime(invalid, adapter_loader=_adapter)

    cached_fingerprints: list[tuple[str, str]] = []
    for name, logical_path, formats in (
        ("one", "input/one.txt", ["txt"]),
        ("two", "input/two.txt", ["md", "txt"]),
    ):
        workspace = _workspace(tmp_path / f"cached-{name}")
        source_path = workspace / logical_path
        source_path.write_text("cached source content", encoding="utf-8")
        (workspace / "sources.yaml").write_text(
            yaml.safe_dump(
                {
                    "source_strategy": {
                        "profile": "conservative",
                        "enabled_providers": ["cached_package"],
                    },
                    "cached_package": {
                        "enabled": True,
                        "paths": [logical_path],
                        "formats": formats,
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        initialized = initialize_or_open_runtime(workspace, adapter_loader=_adapter)
        route = initialized.verified.source_plan.routes[0]
        assert route.acquisition_spec is not None
        cached_fingerprints.append(
            (
                route.acquisition_spec.acquisition_spec_fingerprint,
                route.route_fingerprint,
            )
        )
    assert len(set(cached_fingerprints)) == len(cached_fingerprints)


def test_custom_source_credential_selector_fails_before_store_write(
    tmp_path: Path,
) -> None:
    workspace = _external_web_workspace(tmp_path)
    path = workspace / "sources.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["web_search"]["api_key_env"] = "CUSTOM_SECRET"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(RuntimeHostError, match="runtime_initialization_input_invalid"):
        initialize_or_open_runtime(workspace, adapter_loader=_adapter)

    assert not (workspace / "briefloop.db").exists()


def test_single_session_adapter_capability_is_required_before_store_write(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(RuntimeHostError, match="runtime_adapter_binding_invalid"):
        initialize_or_open_runtime(
            workspace,
            adapter_loader=_adapter_without_single_session,
        )

    assert not (workspace / "briefloop.db").exists()
    assert not (workspace / "scratch").exists()


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload.pop("run_direction"),
        lambda payload: payload.__setitem__("extra", True),
        lambda payload: payload.__setitem__("input_governance_required", "true"),
    ),
)
def test_invalid_bootstrap_fails_before_store_creation(
    tmp_path: Path, mutation
) -> None:
    workspace = _workspace(tmp_path)
    config_path = workspace / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    mutation(config["controlstore_v2"])
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(RuntimeHostError, match="runtime_initialization_input_invalid"):
        initialize_or_open_runtime(workspace, adapter_loader=_adapter)

    assert not (workspace / "briefloop.db").exists()
    assert not (workspace / "briefloop.db.blobs").exists()


def test_duplicate_bootstrap_key_fails_before_store_creation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    config_path = workspace / "config.yaml"
    content = config_path.read_text(encoding="utf-8")
    config_path.write_text(content + "controlstore_v2: {}\n", encoding="utf-8")

    with pytest.raises(RuntimeHostError, match="runtime_initialization_input_invalid"):
        initialize_or_open_runtime(workspace, adapter_loader=_adapter)
    assert not (workspace / "briefloop.db").exists()


def test_json_only_workspace_is_rejected_without_writes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    legacy = workspace / "output" / "intermediate" / "workflow_state.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("{}", encoding="utf-8")
    before = {
        path: path.read_bytes() for path in workspace.rglob("*") if path.is_file()
    }

    with pytest.raises(RuntimeHostError, match="legacy_workspace_unsupported"):
        initialize_or_open_runtime(workspace, adapter_loader=_adapter)

    after = {path: path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    assert after == before
    assert not (workspace / "briefloop.db").exists()


def test_store_status_ignores_forged_legacy_projections(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    initialize_or_open_runtime(workspace, adapter_loader=_adapter)
    first = build_store_status_projection(workspace)
    intermediate = workspace / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "workflow_state.json").write_text(
        '{"current_stage":"delivered"}', encoding="utf-8"
    )
    (intermediate / "event_log.jsonl").write_text(
        '{"event_type":"delivery_succeeded"}\n', encoding="utf-8"
    )

    second = build_store_status_projection(workspace)

    assert second == first
    assert second["authority"] == "sqlite_control_store"
    assert second["delivered"] is False
    assert second["execution_topology"] == "single_session"
    assert second["execution_topology_display"] == "Single session"
    assert second["context_independence"] == "Shared context"
    assert second["review_mode"] == "Stage-separated self-review"
    assert second["role_stages"] == "Separate recorded invocations"
    assert "independent review" not in str(second).lower()
    quality = build_store_quality_projection(workspace)
    assert quality == {
        "ok": False,
        "status": "projection_not_available",
        "reason_code": "final_reader_not_available",
        "authority": "sqlite_control_store",
        "run_id": "RUN-run",
        "store_revision": first["store_revision"],
    }


def test_local_presentation_uses_one_loaded_history_without_head_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    initialize_or_open_runtime(workspace, adapter_loader=_adapter)
    original = SQLiteControlStore.load_history
    calls = 0

    def counted(store: SQLiteControlStore):
        nonlocal calls
        calls += 1
        return original(store)

    monkeypatch.setattr(SQLiteControlStore, "load_history", counted)
    monkeypatch.setattr(
        SQLiteControlStore,
        "load_workspace_run_head",
        lambda _store: (_ for _ in ()).throw(AssertionError("head reopened")),
    )

    presentation = build_local_run_presentation(workspace)

    assert calls == 1
    assert presentation.view_state == "setup"
    assert presentation.reader_brief.state == "unavailable"
    assert presentation.presentation.status == "not_requested"


@pytest.mark.parametrize("status", ["pass", "fail", "warning"])
def test_local_presentation_gate_summary_preserves_every_store_status(
    status: str,
) -> None:
    summary = LocalRunGateSummary.model_validate(
        {
            "gate_id": "final_abstract_quality",
            "evaluation_id": f"EVAL-{status}",
            "stage_id": "auditor",
            "status": status,
            "blocking": status == "fail",
        },
        strict=True,
    )

    assert summary.status == status


def _delivery_result_ready_host(
    tmp_path: Path,
) -> tuple[Path, str, RuntimeHostService, dict[str, bytes]]:
    workspace, run_id, _clock = _finalize_ready_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        adapter = CoreRunDomainVerifier().verify(store, run_id).runtime_adapter
    host = RuntimeHostService(workspace, adapter_loader=lambda _run_id: adapter)
    for effect_kind in ("finalize_render", "finalize_gate", "finalize_complete"):
        action = host.next_action()
        assert action.effect_kind == effect_kind
        assert host.apply_current(action).status == "committed"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        package = verified.snapshot.package_ready_records[0]
    action = host.next_action()
    assert (
        host.apply_current(
            action,
            human_request=InternalApprovalRequest.model_validate(
                {
                    "schema_version": InternalApprovalRequest.schema_id,
                    "request_id": "REQ-HOST-V2-LOCAL-APPROVAL-SAFETY",
                    "run_id": run_id,
                    "package_id": package.package_id,
                    "approval_id": "APPROVAL-HOST-V2-LOCAL-SAFETY",
                    "mode": "internal_management_review",
                    "role": "content_owner",
                    "decision": "approve",
                    "reason": "reader package reviewed",
                    "actor_id": "human-reviewer",
                    "expected_store_revision": action.store_revision,
                },
                strict=True,
            ),
        ).status
        == "committed"
    )
    action = host.next_action()
    assert (
        host.apply_current(
            action,
            human_request=DeliveryAuthorizationRequest.model_validate(
                {
                    "schema_version": DeliveryAuthorizationRequest.schema_id,
                    "request_id": "REQ-HOST-V2-LOCAL-AUTH-SAFETY",
                    "run_id": run_id,
                    "package_id": package.package_id,
                    "prior_authorization_id": None,
                    "approval_mode": "internal_management_review",
                    "retry_of_attempt_id": None,
                    "purpose": "initial_attempt",
                    "decision": "authorize",
                    "target": "local",
                    "channel": "local_bundle",
                    "recipient_fingerprint": "b" * 64,
                    "actor_id": "human-reviewer",
                    "reason": "approved local bundle",
                    "expected_store_revision": action.store_revision,
                },
                strict=True,
            ),
        ).status
        == "committed"
    )
    action = host.next_action()
    assert action.effect_kind == "delivery_attempt"
    assert host.apply_current(action).status == "committed"
    assert host.next_action().effect_kind == "delivery_result"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        snapshot = verified.snapshot
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        expected = {
            Path(
                revisions[(binding.artifact_id, binding.artifact_revision)].path
            ).name: store.read_artifact_revision_bytes(
                run_id,
                binding.artifact_id,
                binding.artifact_revision,
            )
            for binding in snapshot.package_artifact_bindings
            if binding.package_id == package.package_id and binding.usage == "reader"
        }
    return workspace, run_id, host, expected


def test_store_native_local_delivery_materializes_receipt_bound_reader_bundle(
    tmp_path: Path,
) -> None:
    workspace, run_id, _clock = _finalize_ready_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        adapter = CoreRunDomainVerifier().verify(store, run_id).runtime_adapter
    host = RuntimeHostService(workspace, adapter_loader=lambda _run_id: adapter)

    for effect_kind in ("finalize_render", "finalize_gate", "finalize_complete"):
        action = host.next_action()
        assert action.effect_kind == effect_kind
        assert host.apply_current(action).status == "committed"

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        package = verified.snapshot.package_ready_records[0]

    action = host.next_action()
    approval = host.apply_current(
        action,
        human_request=InternalApprovalRequest.model_validate(
            {
                "schema_version": InternalApprovalRequest.schema_id,
                "request_id": "REQ-HOST-V2-LOCAL-APPROVAL-001",
                "run_id": run_id,
                "package_id": package.package_id,
                "approval_id": "APPROVAL-HOST-V2-LOCAL-001",
                "mode": "internal_management_review",
                "role": "content_owner",
                "decision": "approve",
                "reason": "reader package reviewed",
                "actor_id": "human-reviewer",
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        ),
    )
    assert approval.status == "committed"

    action = host.next_action()
    authorization = host.apply_current(
        action,
        human_request=DeliveryAuthorizationRequest.model_validate(
            {
                "schema_version": DeliveryAuthorizationRequest.schema_id,
                "request_id": "REQ-HOST-V2-LOCAL-AUTH-001",
                "run_id": run_id,
                "package_id": package.package_id,
                "prior_authorization_id": None,
                "approval_mode": "internal_management_review",
                "retry_of_attempt_id": None,
                "purpose": "initial_attempt",
                "decision": "authorize",
                "target": "local",
                "channel": "local_bundle",
                "recipient_fingerprint": "a" * 64,
                "actor_id": "human-reviewer",
                "reason": "approved local bundle",
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        ),
    )
    assert authorization.status == "committed"

    action = host.next_action()
    assert action.effect_kind == "delivery_attempt"
    assert host.apply_current(action).status == "committed"

    action = host.next_action()
    assert action.effect_kind == "delivery_result"
    delivery = workspace / "output" / "delivery"
    assert not delivery.exists()
    result = host.apply_current(action)
    assert result.status == "committed"

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        snapshot = verified.snapshot
        reader_bindings = sorted(
            (
                item
                for item in snapshot.package_artifact_bindings
                if item.package_id == package.package_id and item.usage == "reader"
            ),
            key=lambda item: item.position,
        )
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        expected = {
            Path(
                revisions[(binding.artifact_id, binding.artifact_revision)].path
            ).name: store.read_artifact_revision_bytes(
                run_id,
                binding.artifact_id,
                binding.artifact_revision,
            )
            for binding in reader_bindings
        }
        assert snapshot.delivery_results[-1].status == "bundle_prepared"

    assert expected
    assert {item.name for item in delivery.iterdir()} == set(expected)
    assert {name: (delivery / name).read_bytes() for name in expected} == expected
    assert result.receipt is not None
    assert [item.result_id for item in result.receipt.delivery_results] == [
        snapshot.delivery_results[-1].result_id
    ]
    replayed_manifest = host._materialize_local_delivery_bundle(
        snapshot,
        run_id=run_id,
        package_id=package.package_id,
    )
    assert {item["path"] for item in replayed_manifest} == {
        f"output/delivery/{name}" for name in expected
    }


@pytest.mark.parametrize(
    "conflict_kind",
    (
        "output_symlink",
        "delivery_symlink",
        "destination_symlink",
        "destination_hardlink",
        "destination_different_bytes",
        "unexpected_member",
    ),
)
def test_local_delivery_fails_closed_on_unsafe_or_conflicting_output(
    tmp_path: Path,
    conflict_kind: str,
) -> None:
    workspace, run_id, host, expected = _delivery_result_ready_host(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    delivery = workspace / "output" / "delivery"
    name = sorted(expected)[0]
    outside_target = outside / name
    if conflict_kind == "output_symlink":
        output = workspace / "output"
        output.rename(workspace / "output-preserved")
        output.symlink_to(outside, target_is_directory=True)
    elif conflict_kind == "delivery_symlink":
        delivery.symlink_to(outside, target_is_directory=True)
    else:
        delivery.mkdir()
        destination = delivery / name
        if conflict_kind == "destination_symlink":
            outside_target.write_bytes(b"outside-original")
            destination.symlink_to(outside_target)
        elif conflict_kind == "destination_hardlink":
            outside_target.write_bytes(expected[name])
            os.link(outside_target, destination)
        elif conflict_kind == "destination_different_bytes":
            destination.write_bytes(b"different")
        else:
            (delivery / "unexpected.txt").write_bytes(b"unexpected")
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision
        before_results = len(store.load_snapshot(run_id).delivery_results)

    with pytest.raises(
        RuntimeHostError,
        match="runtime_delivery_materialization_failed",
    ):
        host.apply_current(host.next_action())

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_revision
        assert len(store.load_snapshot(run_id).delivery_results) == before_results
    if outside_target.exists():
        expected_outside = (
            expected[name]
            if conflict_kind == "destination_hardlink"
            else b"outside-original"
        )
        assert outside_target.read_bytes() == expected_outside
    elif conflict_kind in {"output_symlink", "delivery_symlink"}:
        assert list(outside.iterdir()) == []
