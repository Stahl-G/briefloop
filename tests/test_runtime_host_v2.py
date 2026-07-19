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
    )
    payload.pop("binding_fingerprint", None)
    payload["binding_fingerprint"] = canonical_fingerprint(payload)
    return RuntimeAdapterBinding.model_validate(payload, strict=True)


def test_fresh_runtime_initializes_once_and_existing_store_ignores_input_drift(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
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
    quality = build_store_quality_projection(workspace)
    assert quality == {
        "ok": False,
        "status": "projection_not_available",
        "reason_code": "package_not_ready",
        "authority": "sqlite_control_store",
        "run_id": "RUN-run",
        "store_revision": first["store_revision"],
    }
