from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

import pytest
import yaml

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.cli.main import main
from multi_agent_brief.contracts import SchemaRegistry
from multi_agent_brief.contracts.v2 import (
    InvocationStartRequest,
    SourceProposal,
    TavilyAcquisitionBundleV2,
    TavilyExtractBatchExchange,
    TavilyExtractUrlOutcome,
    TavilySearchTaskExchange,
    TavilyTaskAcquisitionStatus,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
)
from multi_agent_brief.core_run_v2.errors import CoreRunResult
from multi_agent_brief.core_run_v2.policy import derived_id
from multi_agent_brief.core_run_v2.service import CoreRunService
from multi_agent_brief.intake_v2.errors import IntakeResult
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.product.init_web.submit import (
    SUBMISSION_SCHEMA,
    InitWebSubmitter,
)
from multi_agent_brief.runtime_host_v2.codex import load_codex_adapter_binding
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.service import (
    RuntimeHostService,
    _ROLE_OUTPUTS,
    _role_task_instructions,
    _strict_proposal_violations,
    _target_relevance_task_instruction,
)
from multi_agent_brief.runtime_host_v2.submission import source_stage_root
from multi_agent_brief.runtime_assets import install_runtime_kit
from multi_agent_brief.sources.base import SourceItem
from multi_agent_brief.sources.search_backends.tavily import TavilyBackend
from multi_agent_brief.sources.web_search import (
    WebSearchCollection,
)
from multi_agent_brief.workspace.init_profile import InitProfile


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    values = iter(("codex-workspace", "codex-run"))
    create_workspace(
        workspace,
        InitProfile(
            company="ExampleCo",
            industry="manufacturing",
            brief_title="ExampleCo brief",
            task_objective="Prepare the ExampleCo brief.",
            audience="management",
            audience_profile="management",
            focus_areas=["operations"],
            output_formats=["markdown"],
            web_search_mode="disabled",
            web_search_enabled=False,
        ),
        report_date_factory=lambda: date(2026, 7, 19),
        identity_factory=lambda: next(values),
    )
    install_runtime_kit(workspace=workspace, runtime="codex")
    return workspace


def test_strict_json_role_instructions_bind_contract_preflight_commands() -> None:
    invocation_id = "INV-SCOUT-PREFLIGHT-001"
    instructions = _role_task_instructions(
        "scout",
        _ROLE_OUTPUTS["scout"],
        invocation_id,
    )

    assert (
        "briefloop contract show briefloop.candidate_claims_proposal.v2 --example full"
    ) in instructions
    assert (
        "briefloop runtime invocation-validate --workspace . --envelope "
        "scratch/INV-SCOUT-PREFLIGHT-001/role_task_envelope.json"
    ) in instructions
    assert "never guess aliases, wrapper names, or invocation bindings" in instructions

    owned_instructions = _role_task_instructions(
        "source-planner",
        _ROLE_OUTPUTS["source-planner"],
        "INV-PLANNER-001",
    )
    assert "briefloop contract" not in owned_instructions






def test_first_dynamic_proposal_is_created_and_advances_the_runtime(
    tmp_path: Path,
    capsys,
) -> None:
    if sys.platform == "win32":
        pytest.skip("working-checkout publication is precommit unsupported on Windows")
    workspace = _cached_workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    planner = json.loads(capsys.readouterr().out)
    assert planner["role_id"] == "source-planner"
    planner_scratch = workspace / planner["scratch_directory"]
    (planner_scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: cached_package\n",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "runtime",
                "invocation-accept",
                "--workspace",
                str(workspace),
                "--envelope",
                str(_envelope_path(workspace, planner)),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    host = RuntimeHostService(
        workspace,
        adapter_loader=load_codex_adapter_binding,
    )
    for _ in range(4):
        action = host.next_action()
        if action.role_id == "scout":
            break
        assert action.action_kind == "deterministic"
        assert _apply_current(workspace, capsys) == 0
        capsys.readouterr()
    assert host.next_action().role_id == "scout"
    dispatch = host.start_current_invocation()
    assert dispatch.envelope.role_id == "scout"
    payload = SchemaRegistry.example(
        "briefloop.candidate_claims_proposal.v2",
        "full",
    )
    payload["run_id"] = dispatch.envelope.run_id
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        source = store.load_snapshot(dispatch.envelope.run_id).sources[0]
    evidence_text = (
        (workspace / str(source.locator.path)).read_text(encoding="utf-8").strip()
    )
    payload["candidates"][0].update(
        source_id=source.source_id,
        statement=evidence_text,
        evidence_text=evidence_text,
    )
    payload["candidates"] = [payload["candidates"][0]]

    request, lane = host._derive_acceptance_request(
        dispatch.envelope,
        _ROLE_OUTPUTS["scout"],
        {"candidate_claims.json": json.dumps(payload).encode("utf-8")},
    )

    assert lane == "candidate"
    assert request.artifact_id == "candidate_claims"
    assert request.expected_artifact_revision == 0

    envelope_path = (
        workspace / dispatch.envelope.scratch_directory / "role_task_envelope.json"
    )
    (envelope_path.parent / "candidate_claims.json").write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision
    rc = main(
        [
            "runtime",
            "invocation-accept",
            "--workspace",
            str(workspace),
            "--envelope",
            str(envelope_path),
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0, output
    accepted = json.loads(output)
    assert accepted["status"] == "committed", accepted
    assert accepted["store_revision"] == before_revision + 1
    assert accepted["next_action"]["effect_kind"] == "stage_complete"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(dispatch.envelope.run_id)
    artifact = next(
        item for item in snapshot.artifacts if item.artifact_id == "candidate_claims"
    )
    assert artifact.current_revision == 1
    assert any(
        item.artifact_id == "candidate_claims" and item.revision == 1
        for item in snapshot.artifact_revisions
    )

    assert _apply_current(workspace, capsys) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "committed"
    next_action = host.next_action()
    assert next_action.action_kind == "delegate"
    assert next_action.role_id == "screener"


def _external_workspace(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "external-workspace"
    session_id = "runtime-host-codex-test-session"
    submitter = InitWebSubmitter(base_dir=tmp_path)
    submitter.configure_search_secret(
        session_id=session_id,
        body={"provider": "tavily", "api_key": "test-only-tavily-secret"},
    )
    status, response = submitter.submit(
        {
            "schema_version": SUBMISSION_SCHEMA,
            "request_id": "REQ-RUNTIME-HOST-CODEX-TAVILY",
            "payload": {
                "workspace_target": workspace.name,
                "selections": {
                    "company": "ExampleCo",
                    "report_type": "management_monthly",
                    "industry_or_theme": "manufacturing",
                    "task_objective": "Prepare the ExampleCo brief.",
                    "brief_title": "ExampleCo brief",
                    "audience": "management",
                    "interface_language": "en",
                    "output_language": "en",
                    "cadence": "weekly",
                    "max_source_age_days": 30,
                    "focus_areas": ["operations"],
                    "output_formats": ["markdown"],
                    "forbidden_sources": [],
                    "source_profile": "llm_decide",
                    "web_search_mode": "external_api",
                    "search_backend": "tavily",
                    "search_domains": [],
                    "output_extent": "balanced",
                },
                "completion_target": "finalized_local",
                "repair_budget": 1,
                "search_secret_session_id": session_id,
                "human_confirmation": True,
            },
        }
    )
    if status != 200 or response.get("source_discovery_authorized") is not True:
        raise AssertionError(f"external workspace initialization failed: {response!r}")
    return workspace


def _cached_workspace(tmp_path: Path) -> Path:
    workspace = _workspace(tmp_path)
    cached_paths: list[str] = []
    for position in range(1, 26):
        relative = f"input/cached-source-{position:02d}.txt"
        (workspace / relative).write_text(
            f"Durable cached source {position:02d} content long enough for deterministic intake.\n",
            encoding="utf-8",
        )
        cached_paths.append(relative)
    (workspace / "sources.yaml").write_text(
        """source_strategy:
  profile: conservative
  enabled_providers: [cached_package]
cached_package:
  enabled: true
  paths:
"""
        + "".join(f"    - {item}\n" for item in cached_paths)
        + """
  formats: [txt]
""",
        encoding="utf-8",
    )
    return workspace


def _specialist_workspace(tmp_path: Path) -> Path:
    workspace = _workspace(tmp_path)
    (workspace / "sources.yaml").write_text(
        """source_strategy:
  profile: research
  enabled_providers: [rss]
""",
        encoding="utf-8",
    )
    return workspace


def _advance_to_source_route(
    workspace: Path,
    capsys,
    *,
    route: str,
) -> tuple[RuntimeHostService, object]:
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    host = RuntimeHostService(
        workspace,
        adapter_loader=load_codex_adapter_binding,
    )
    planner = host.start_current_invocation()
    (
        workspace / planner.envelope.scratch_directory / "source_candidates.yaml"
    ).write_text(
        f"version: 1\ncandidates:\n  - route: {route}\n",
        encoding="utf-8",
    )
    accepted = host.accept_invocation(planner.envelope.invocation_id)
    return host, accepted.next_action


def _current_action_path(workspace: Path, capsys) -> Path:
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    path = workspace / "runtime_action.json"
    path.write_text(json.dumps(action), encoding="utf-8")
    return path


def _apply_current(workspace: Path, capsys) -> int:
    action = _current_action_path(workspace, capsys)
    return main(
        [
            "runtime",
            "apply",
            "--workspace",
            str(workspace),
            "--action",
            str(action),
        ]
    )


def _start_current(workspace: Path, capsys) -> int:
    return main(
        [
            "runtime",
            "invocation-start",
            "--workspace",
            str(workspace),
        ]
    )


def _start_current_with_action(workspace: Path, capsys) -> int:
    action = _current_action_path(workspace, capsys)
    return main(
        [
            "runtime",
            "invocation-start",
            "--workspace",
            str(workspace),
            "--action",
            str(action),
        ]
    )


def _envelope_path(workspace: Path, envelope: dict[str, object]) -> Path:
    return workspace / str(envelope["scratch_directory"]) / "role_task_envelope.json"


def test_codex_run_initializes_store_and_returns_exact_action(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)

    rc = main(["run", "--workspace", str(workspace), "--runtime", "codex"])

    assert rc == 0
    action = json.loads(capsys.readouterr().out)
    assert action["run_id"] == "RUN-codex-run"
    assert action["stage_id"] == "doctor"
    assert action["effect_kind"] == "doctor_check"

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == action["store_revision"]
        assert store.load_workspace_run_head().current_run_id == "RUN-codex-run"
    assert not (workspace / "output" / "intermediate" / "workflow_state.json").exists()


def test_stale_or_forged_action_file_cannot_start_invocation_or_write(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    doctor_action = json.loads(capsys.readouterr().out)
    action_path = workspace / "doctor_action.json"
    action_path.write_text(json.dumps(doctor_action), encoding="utf-8")
    assert (
        main(
            [
                "runtime",
                "apply",
                "--workspace",
                str(workspace),
                "--action",
                str(action_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision = store.current_revision

    assert (
        main(
            [
                "runtime",
                "invocation-start",
                "--workspace",
                str(workspace),
                "--action",
                str(action_path),
            ]
        )
        == 1
    )
    assert "runtime_action_stale" in capsys.readouterr().out
    forged = dict(doctor_action)
    forged["reason_code"] = "forged"
    action_path.write_text(json.dumps(forged), encoding="utf-8")
    assert (
        main(
            [
                "runtime",
                "invocation-start",
                "--workspace",
                str(workspace),
                "--action",
                str(action_path),
            ]
        )
        == 1
    )
    assert "runtime_action_invalid" in capsys.readouterr().out
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision




def test_invocation_start_unknown_immediately_replays_one_committed_request(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.current_revision
    original = CoreRunService.start_invocation
    calls = 0

    def unknown_after_first_commit(self, request):
        nonlocal calls
        calls += 1
        result = original(self, request)
        if calls == 1:
            assert result.status == "committed"
            return CoreRunResult(
                status="commit_outcome_unknown",
                error_code="commit_outcome_unknown",
            )
        return result

    monkeypatch.setattr(CoreRunService, "start_invocation", unknown_after_first_commit)
    host = RuntimeHostService(
        workspace,
        adapter_loader=load_codex_adapter_binding,
    )
    dispatch = host.start_current_invocation()

    assert calls == 2
    assert dispatch.envelope.role_id == "source-planner"
    assert dispatch.envelope_path.exists()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before + 1
        snapshot = store.load_snapshot("RUN-codex-run")
    assert len(snapshot.invocations) == 1
    assert snapshot.invocations[0].invocation_id == dispatch.envelope.invocation_id


def test_invocation_validate_is_read_only_and_envelope_bound(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)
    scratch = workspace / envelope["scratch_directory"]
    (scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates: []\n",
        encoding="utf-8",
    )
    envelope_path = _envelope_path(workspace, envelope)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.current_revision

    assert (
        main(
            [
                "runtime",
                "invocation-validate",
                "--workspace",
                str(workspace),
                "--envelope",
                str(envelope_path),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "valid"
    assert result["reason_code"] is None
    assert result["checked_filenames"] == ["source_candidates.yaml"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before




def test_role_submission_reconstructs_every_envelope_field_from_store(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    host = RuntimeHostService(
        workspace,
        adapter_loader=load_codex_adapter_binding,
    )
    dispatch = host.start_current_invocation()
    scratch = workspace / dispatch.envelope.scratch_directory
    (scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates: []\n",
        encoding="utf-8",
    )
    envelope_path = scratch / "role_task_envelope.json"
    original = json.loads(envelope_path.read_text(encoding="utf-8"))
    mutations = {
        "schema_version": "briefloop.role_task_envelope.invalid",
        "run_id": "RUN-FORGED",
        "invocation_id": "INV-FORGED",
        "store_revision": original["store_revision"] + 1,
        "action_fingerprint": "0" * 64,
        "role_id": "scout",
        "stage_id": "scout",
        "scratch_directory": "scratch/INV-FORGED",
        "allowed_output_filenames": ["forged.json"],
        "proposal_schema_id": "briefloop.forged.v2",
        "adapter_binding_fingerprint": "1" * 64,
        "source_plan_fingerprint": "2" * 64,
        "executor_kind": "delegated_specialist",
        "context_mode": "independent_stage_context",
        "review_mode": "independent_stage_context",
        "dispatch_instruction": "delegate_exact_role",
        "task_instructions": "Forged mutable task instructions.",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision
    for field, value in mutations.items():
        tampered = deepcopy(original)
        tampered[field] = value
        envelope_path.write_text(json.dumps(tampered, sort_keys=True), encoding="utf-8")
        with pytest.raises(RuntimeHostError, match="runtime_envelope_invalid"):
            host.validate_invocation(dispatch.envelope.invocation_id)
        envelope_path.write_text(json.dumps(original, sort_keys=True), encoding="utf-8")
    assert host.validate_invocation(dispatch.envelope.invocation_id).status == "valid"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_revision


def test_source_submission_verifier_binds_content_raw_and_advisory_race(
    tmp_path: Path,
    capsys,
) -> None:
    if sys.platform == "win32":
        pytest.skip("source-candidate publication is precommit unsupported on Windows")
    workspace = _specialist_workspace(tmp_path)
    host, action = _advance_to_source_route(workspace, capsys, route="rss")
    assert action.action_kind == "delegate"
    assert action.role_id == "source-provider"
    dispatch = host.start_current_invocation(expected_action=action)
    scratch = workspace / dispatch.envelope.scratch_directory
    content = b"Exact source content for sibling verification.\n"
    raw_payload = b'{"provider":"rss","result":"exact"}\n'
    payload = SchemaRegistry.example(SourceProposal.schema_id, "full")
    payload.update(
        proposal_id="PROP-SOURCE-RSS-001",
        run_id=action.run_id,
        source_id="SRC-RSS-001",
        content_sha256=hashlib.sha256(content).hexdigest(),
        raw_payload_sha256=hashlib.sha256(raw_payload).hexdigest(),
    )
    (scratch / "source_proposal.json").write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    content_path = scratch / "source_content.bin"
    raw_path = scratch / "source_raw.json"
    content_path.write_bytes(content)
    raw_path.write_bytes(raw_payload)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision

    assert host.validate_invocation(dispatch.envelope.invocation_id).status == "valid"
    content_path.write_bytes(content + b"tampered")
    content_result = host.validate_invocation(dispatch.envelope.invocation_id)
    assert content_result.status == "invalid"
    assert [item.field for item in content_result.violations] == ["content_sha256"]
    with pytest.raises(RuntimeHostError, match="runtime_proposal_invalid"):
        host.accept_invocation(dispatch.envelope.invocation_id)
    content_path.write_bytes(content)
    assert host.validate_invocation(dispatch.envelope.invocation_id).status == "valid"
    raw_path.write_bytes(raw_payload + b"tampered")
    raw_result = host.validate_invocation(dispatch.envelope.invocation_id)
    assert raw_result.status == "invalid"
    assert [item.field for item in raw_result.violations] == ["raw_payload_sha256"]
    with pytest.raises(RuntimeHostError, match="runtime_proposal_invalid"):
        host.accept_invocation(dispatch.envelope.invocation_id)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_revision




def test_symlinked_scratch_records_invocation_failure_without_external_write(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "scratch").symlink_to(outside, target_is_directory=True)

    assert (
        main(
            [
                "runtime",
                "invocation-start",
                "--workspace",
                str(workspace),
            ]
        )
        == 1
    )
    assert "runtime_envelope_materialization_failed" in capsys.readouterr().out
    assert list(outside.iterdir()) == []
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot("RUN-codex-run")
    assert len(snapshot.invocations) == 1
    assert snapshot.invocations[0].status == "failed"
    assert snapshot.invocations[0].failure_reason == "envelope_materialization_failed"








def test_runtime_doctor_then_exact_source_planner_invocation(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()

    assert _apply_current(workspace, capsys) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "committed"
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["action_kind"] == "delegate"
    assert action["role_id"] == "source-planner"

    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["role_id"] == "source-planner"
    assert envelope["action"] == action
    assert envelope["executor_kind"] == "main_session"
    assert envelope["context_mode"] == "shared_session"
    assert envelope["review_mode"] == "stage_separated_self_review"
    assert envelope["dispatch_instruction"] == "execute_in_current_session"
    envelope_path = (
        workspace / envelope["scratch_directory"] / "role_task_envelope.json"
    )
    assert json.loads(envelope_path.read_text(encoding="utf-8")) == envelope

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision = store.current_revision
    assert _start_current(workspace, capsys) == 0
    replayed_envelope = json.loads(capsys.readouterr().out)
    assert replayed_envelope == envelope
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision


def test_cli_authority_guard_blocks_non_sqlite_and_unlisted_commands(
    tmp_path: Path,
    capsys,
) -> None:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert main(["status", "--workspace", str(fresh), "--json"]) == 1
    assert "runtime_command_unsupported" in capsys.readouterr().out
    assert list(fresh.iterdir()) == []

    legacy = tmp_path / "legacy"
    control = legacy / "output" / "intermediate" / "workflow_state.json"
    control.parent.mkdir(parents=True)
    control.write_text("{}\n", encoding="utf-8")
    before_legacy = control.read_bytes()
    # legacy JSON control files do not create runnable authority; the
    # non-SQLite workspace is fresh and `status` is refused.
    assert main(["status", "--workspace", str(legacy), "--json"]) == 1
    assert "runtime_command_unsupported" in capsys.readouterr().out
    assert control.read_bytes() == before_legacy

    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    database = workspace / "briefloop.db"
    before_database = database.read_bytes()
    # commands outside the SQLite allowlist are refused on a Store workspace.
    assert main(["packs", "bundle", "--workspace", str(workspace), "--json"]) == 1
    assert "runtime_command_unsupported" in capsys.readouterr().out
    assert database.read_bytes() == before_database






def test_explicit_strict_topology_never_falls_back_to_current_session(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    config_path = workspace / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["controlstore_v2"]["role_topology"] = "strict"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)

    assert envelope["executor_kind"] == "delegated_specialist"
    assert envelope["dispatch_instruction"] == "delegate_exact_role"
    assert envelope["context_mode"] == "independent_stage_context"
    assert envelope["review_mode"] == "independent_stage_context"








def test_child_failure_is_value_free_recorded_and_exactly_replayed(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)
    invocation_id = envelope["invocation_id"]

    command = [
        "runtime",
        "invocation-fail",
        "--workspace",
        str(workspace),
        "--envelope",
        str(_envelope_path(workspace, envelope)),
        "--reason",
        "child_timed_out",
    ]
    assert main(command) == 0
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "rejected_recorded"
    assert failed["next_action"]["role_id"] == "source-planner"

    assert main(command) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "rejected_recorded"
    assert replay["transaction_id"] == failed["transaction_id"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot("RUN-codex-run")
    invocation = next(
        item for item in snapshot.invocations if item.invocation_id == invocation_id
    )
    assert invocation.status == "failed"
    assert invocation.failure_reason == "child_timed_out"




def _provider_item(position: int, *, content: str | None = None) -> SourceItem:
    return SourceItem(
        source_id=f"WEB-{position:04d}",
        source_name="Example Search",
        source_type="web_search",
        title=f"Search result {position:04d}",
        content=content or f"Bounded discovery result {position:04d}.",
        url=f"https://example.com/result/{position:04d}",
        published_at="2026-07-20T00:00:00Z",
        retrieved_at="2026-07-22T00:00:00Z",
        metadata={"rank": position},
    )


def _provider_collection(items: list[SourceItem]) -> WebSearchCollection:
    projections = [
        {
            "title": item.title,
            "url": item.url,
            "snippet": f"Discovery snippet {position}.",
            "raw_content": item.content,
            "published_date": item.published_at or "",
            "score": 0.9,
        }
        for position, item in enumerate(items, start=1)
    ]
    normalized = tuple(
        replace(
            item,
            metadata={
                **item.metadata,
                "backend": "tavily",
                "content_shape": "provider_raw_content",
                "has_raw_content": True,
                "evidence_quality": "partial_extract",
                "provider_projection": projection,
            },
        )
        for item, projection in zip(items, projections, strict=True)
    )
    response = {
        "results": [
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
    }
    return WebSearchCollection(
        items=normalized,
        raw_response=json.dumps(response, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        ),
        status_code=200,
    )


def _durable_tavily_collection(
    items: list[SourceItem],
    *,
    search_tasks: list[dict[str, object]],
) -> WebSearchCollection:
    """Build one schema18 multi-search + batch Extract success bundle."""

    search_rows = [
        {
            "title": item.title,
            "url": item.url,
            "content": f"Discovery snippet {position}.",
            "published_date": item.published_at,
            "score": 0.9,
        }
        for position, item in enumerate(items, start=1)
    ]
    searches: list[TavilySearchTaskExchange] = []
    task_statuses: list[TavilyTaskAcquisitionStatus] = []
    task_ids_by_url: dict[str, str] = {}
    for ordinal, task in enumerate(search_tasks, start=1):
        task_rows = search_rows[(ordinal - 1) * 20 : ordinal * 20]
        search_payload: dict[str, object] = {
            "query": task["query"],
            "max_results": 20,
            "topic": task["topic"],
            "search_depth": "advanced",
            "include_answer": False,
            "include_raw_content": False,
            "auto_parameters": False,
            "time_range": "week",
        }
        domains = task.get("domains") or []
        if domains:
            search_payload["include_domains"] = domains
        exchange = TavilyBackend._exchange(
            "search",
            canonical_json_bytes(search_payload),
            response_body=canonical_json_bytes({"results": task_rows}),
            status_code=200,
        )
        task_id = str(task["task_id"])
        for row in task_rows:
            task_ids_by_url[str(row["url"])] = task_id
        searches.append(
            TavilySearchTaskExchange.model_validate(
                {
                    "task_id": task_id,
                    "phase": "primary",
                    "status": "succeeded" if task_rows else "empty",
                    "exchange": exchange.model_dump(mode="json"),
                    "discovered_urls": sorted(row["url"] for row in task_rows),
                },
                strict=True,
            )
        )
        success_count = len(task_rows)
        minimum = int(task["minimum_extract_successes"])
        task_statuses.append(
            TavilyTaskAcquisitionStatus.model_validate(
                {
                    "task_id": task_id,
                    "primary_search_ordinal": ordinal,
                    "discovered_unique_url_count": len(task_rows),
                    "extracted_success_count": success_count,
                    "minimum_extract_successes": minimum,
                    "status": (
                        "covered"
                        if success_count >= minimum
                        else "coverage_insufficient"
                    ),
                },
                strict=True,
            )
        )

    extract_urls = sorted(item.url for item in items)
    extract_rows = [
        {"url": item.url, "raw_content": item.content.strip()}
        for item in sorted(items, key=lambda value: value.url)
    ]
    extract_batches: list[TavilyExtractBatchExchange] = []
    for batch_ordinal, start in enumerate(range(0, len(extract_rows), 20), start=1):
        batch_rows = extract_rows[start : start + 20]
        batch_urls = [str(row["url"]) for row in batch_rows]
        extract_exchange = TavilyBackend._exchange(
            "extract",
            canonical_json_bytes(
                {
                    "urls": batch_urls,
                    "chunks_per_source": 5,
                    "extract_depth": "advanced",
                    "include_images": False,
                    "include_favicon": False,
                    "format": "markdown",
                    "include_usage": True,
                }
            ),
            response_body=canonical_json_bytes(
                {"results": batch_rows, "failed_results": []}
            ),
            status_code=200,
        )
        outcomes = tuple(
            TavilyExtractUrlOutcome.model_validate(
                {
                    "url": row["url"],
                    "status": "succeeded",
                    "response_item_sha256": hashlib.sha256(
                        canonical_json_bytes(row)
                    ).hexdigest(),
                    "content_sha256": hashlib.sha256(
                        str(row["raw_content"]).encode("utf-8")
                    ).hexdigest(),
                    "content_size_bytes": len(
                        str(row["raw_content"]).encode("utf-8")
                    ),
                },
                strict=True,
            )
            for row in batch_rows
        )
        extract_batches.append(
            TavilyExtractBatchExchange.model_validate(
                {
                    "phase": "primary",
                    "batch_ordinal": batch_ordinal,
                    "status": "succeeded",
                    "exchange": extract_exchange.model_dump(mode="json"),
                    "urls": batch_urls,
                    "outcomes": [item.model_dump(mode="json") for item in outcomes],
                },
                strict=True,
            )
        )
    search_by_url = {row["url"]: row for row in search_rows}
    extract_by_url = {row["url"]: row for row in extract_rows}
    normalized = tuple(
        replace(
            item,
            content=item.content.strip(),
            metadata={
                **item.metadata,
                "backend": "tavily",
                "content_shape": "provider_extract_content",
                "has_raw_content": True,
                "evidence_quality": "partial_extract",
                "provider_projection": {
                    "schema_version": ("briefloop.tavily_extract_source_projection.v2"),
                    "search_result": search_by_url[item.url],
                    "extract_result": extract_by_url[item.url],
                    "discovery_task_ids": [task_ids_by_url[item.url]],
                },
            },
        )
        for item in items
    )
    bundle = TavilyAcquisitionBundleV2.model_validate(
        {
            "schema_version": TavilyAcquisitionBundleV2.schema_id,
            "provider_id": "tavily",
            "status": "partial",
            "searches": [item.model_dump(mode="json") for item in searches],
            "extract_batches": [
                item.model_dump(mode="json") for item in extract_batches
            ],
            "unique_urls": extract_urls,
            "task_statuses": [
                item.model_dump(mode="json") for item in task_statuses
            ],
        },
        strict=True,
    )
    return WebSearchCollection(
        items=normalized,
        raw_response=canonical_json_bytes(bundle.model_dump(mode="json")),
        status_code=200,
    )






def test_multi_tavily_commits_all_extracted_sources_and_store_replay_skips_redial(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    if sys.platform == "win32":
        pytest.skip("source-candidate publication is precommit unsupported on Windows")
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    workspace = _external_workspace(tmp_path)
    host, action = _advance_to_source_route(workspace, capsys, route="web-search")
    calls = 0

    def bounded(_provider, _query, config):
        nonlocal calls
        calls += 1
        return _durable_tavily_collection(
            [_provider_item(position) for position in range(25)],
            search_tasks=config["search_tasks"],
        )

    monkeypatch.setattr(
        "multi_agent_brief.sources.web_search.WebSearchProvider.collect_with_response",
        bounded,
    )

    committed = host.apply_current(expected_action=action)
    replayed = host.apply_current(expected_action=action)

    assert committed.status == "committed", (
        committed.next_action.reason_code,
        committed.next_action.effect_kind,
    )
    assert replayed.status == "replayed"
    assert replayed.transaction_id == committed.transaction_id
    assert replayed.store_revision == committed.store_revision
    assert calls == 1
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(action.run_id)
    provider_invocations = [
        item for item in snapshot.invocations if item.role_id == "source-provider"
    ]
    receipt = next(
        item
        for item in snapshot.transactions
        if item.transaction_id == committed.transaction_id
    )
    assert len(provider_invocations) == 1
    assert len(snapshot.sources) == 25
    assert len(receipt.source_ids) == 25
















def test_single_session_envelope_cannot_be_rewritten_as_delegated_execution(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)
    invocation_id = envelope["invocation_id"]
    scratch = workspace / envelope["scratch_directory"]
    (scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates: []\n",
        encoding="utf-8",
    )
    envelope["executor_kind"] = "delegated_specialist"
    envelope["context_mode"] = "independent_stage_context"
    envelope["review_mode"] = "independent_stage_context"
    envelope["dispatch_instruction"] = "delegate_exact_role"
    (scratch / "role_task_envelope.json").write_text(
        json.dumps(envelope, sort_keys=True),
        encoding="utf-8",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.current_revision

    assert (
        main(
            [
                "runtime",
                "invocation-accept",
                "--workspace",
                str(workspace),
                "--envelope",
                str(_envelope_path(workspace, envelope)),
            ]
        )
        == 1
    )
    assert "runtime_envelope_invalid" in capsys.readouterr().out
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before
