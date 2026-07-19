from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.contracts.v2 import RuntimeAdapterBinding
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.runtime_host_v2 import RuntimeHostError
from multi_agent_brief.runtime_host_v2.initialization import initialize_or_open_runtime
from multi_agent_brief.runtime_host_v2.projections import (
    build_store_quality_projection,
    build_store_status_projection,
)
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
        adapter_loader=lambda _run_id: (_ for _ in ()).throw(AssertionError()),
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
        item for item in first.verified.source_plan.routes if item.route_id == "web-search"
    )
    spec = route.acquisition_spec
    assert spec is not None and spec.kind == "web_search"
    assert spec.provider_id == "tavily"
    assert [item.query for item in spec.requests] == ["operations", "policy"]
    assert all(item.max_results == 5 for item in spec.requests)
    assert all(item.recency_days == 7 for item in spec.requests)
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


def test_executable_source_parameters_change_spec_and_route_fingerprints(
    tmp_path: Path,
) -> None:
    web_fingerprints: list[tuple[str, str]] = []
    for name, query, domains, max_results in (
        ("base", "operations", ["example.com"], 5),
        ("query", "policy", ["example.com"], 5),
        ("bounds", "operations", ["example.org"], 9),
    ):
        workspace = _external_web_workspace(tmp_path / name)
        path = workspace / "sources.yaml"
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        payload["web_search"]["search_tasks"] = [
            {"query": query, "domains": domains}
        ]
        payload["web_search"]["max_results"] = max_results
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
        "reason_code": "package_not_ready",
        "authority": "sqlite_control_store",
        "run_id": "RUN-run",
        "store_revision": first["store_revision"],
    }
