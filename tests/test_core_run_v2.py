from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.contracts.v2 import (
    ArtifactSubmitRequest,
    AuditPromotionRequest,
    ClaimFreezeRequest,
    CoreRunInitializeRequest,
    GateCheckRequest,
    IntegrityCheckRequest,
    InvocationStartRequest,
    OwnedArtifactSubmitRequest,
    SourceCommitRequest,
    StageState,
    StageCompleteRequest,
)
from multi_agent_brief.control_store import (
    ControlStoreIntegrityError,
    SQLiteControlStore,
)
from multi_agent_brief.core_run_v2 import (
    ArtifactAcceptanceService,
    ClaimFreezeService,
    CoreRunService,
    GateEvaluationService,
)
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.policy import REQUIRED_AUDITOR_GATES
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.quality_gates.contract import GATE_IDS


RUN_ID = "RUN-CORE-V2-001"
WORKSPACE_ID = "WS-CORE-V2-001"
NOW = "2026-07-15T12:00:00Z"
CLOCK = lambda: datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _record(model_type, **values):
    return model_type.model_validate(
        {"schema_version": model_type.schema_id, **values},
        strict=True,
    )


def _write_json(path: Path, payload: dict[str, object]) -> bytes:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    return workspace


def _store_revision(workspace: Path) -> int:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        return store.current_revision


def _stage(workspace: Path, stage_id: str):
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    return next(item for item in snapshot.stage_states if item.stage_id == stage_id)


def _store_opener_with_failure(workspace: Path, failure_stage: str):
    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise ControlStoreIntegrityError("injected_core_run_failure")

    def open_store() -> SQLiteControlStore:
        return SQLiteControlStore.open(
            workspace / "briefloop.db",
            clock=CLOCK,
            _failure_hook=fail,
        )

    return open_store


def _initialize(
    workspace: Path,
    *,
    topology: str = "default",
) -> CoreRunService:
    service = CoreRunService(workspace, clock=CLOCK)
    request = deepcopy(CoreRunInitializeRequest.minimal_example)
    request.update(
        request_id="REQ-INIT-001",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        role_topology=topology,
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(
            workspace, "config.yaml"
        ).sha256,
        sources_config_sha256=read_workspace_file(
            workspace, "sources.yaml"
        ).sha256,
    )
    result = service.initialize(CoreRunInitializeRequest.model_validate(request, strict=True))
    assert result.status == "committed", result.to_dict()
    return service


def _start_invocation(
    service: CoreRunService,
    workspace: Path,
    *,
    request_id: str,
    stage_id: str,
    role_id: str,
) -> str:
    result = service.start_invocation(
        _record(
            InvocationStartRequest,
            request_id=request_id,
            run_id=RUN_ID,
            stage_id=stage_id,
            role_id=role_id,
            runtime="operator",
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert result.status == "committed", result.to_dict()
    assert result.primary_record_id is not None
    return result.primary_record_id


def _complete_stage(
    service: CoreRunService,
    workspace: Path,
    *,
    stage_id: str,
    artifacts: list[tuple[str, int]],
    gate_evaluation_ids: list[str] | None = None,
) -> None:
    stage = _stage(workspace, stage_id)
    result = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id=f"REQ-COMPLETE-{stage_id.upper()}",
            run_id=RUN_ID,
            stage_id=stage_id,
            reason=f"{stage_id} accepted output is complete",
            expected_stage_revision=stage.revision,
            expected_store_revision=_store_revision(workspace),
            expected_artifact_revisions=[
                {"artifact_id": artifact_id, "revision": revision}
                for artifact_id, revision in artifacts
            ],
            expected_gate_evaluation_ids=gate_evaluation_ids or [],
        )
    )
    assert result.status == "committed", result.to_dict()


def _submit_source(workspace: Path, invocation_id: str) -> None:
    scratch = workspace / "scratch" / invocation_id
    content = b"ExampleCo opened a public pilot facility on 2026-07-14.\n"
    content_path = scratch / "source_content.txt"
    content_path.parent.mkdir(parents=True, exist_ok=True)
    content_path.write_bytes(content)
    proposal_path = scratch / "source_proposal.json"
    _write_json(
        proposal_path,
        {
            "schema_version": "briefloop.source_proposal.v2",
            "proposal_id": "PROP-SOURCE-001",
            "run_id": RUN_ID,
            "source_id": "SRC-001",
            "origin_type": "uploaded_file",
            "acquisition_method": "manual_upload",
            "material_kind": "uploaded_file",
            "provider": None,
            "locator": {
                "kind": "file",
                "path": f"scratch/{invocation_id}/source_content.txt",
            },
            "title": "Synthetic public pilot filing",
            "publisher": "Example regulator",
            "published_at": "2026-07-14",
            "retrieved_at": NOW,
            "source_category": "regulator",
            "retrieval_source_type": "local_file",
            "underlying_evidence_type": "filing",
            "raw_underlying_evidence_type": None,
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_media_type": "text/plain",
            "raw_payload_sha256": None,
            "raw_payload_media_type": None,
        },
    )
    request_path = scratch / "submit_request.json"
    _write_json(
        request_path,
        _record(
            SourceCommitRequest,
            request_id="REQ-SOURCE-001",
            run_id=RUN_ID,
            invocation_id=invocation_id,
            proposal_path=proposal_path.relative_to(workspace).as_posix(),
            content_path=content_path.relative_to(workspace).as_posix(),
            raw_payload_path=None,
            expected_store_revision=_store_revision(workspace),
        ).model_dump(mode="json", exclude_unset=False),
    )
    result = IntakeService(workspace, clock=CLOCK).submit_source(
        request_path.relative_to(workspace).as_posix()
    )
    assert result.status == "committed", result.to_dict()


def _submit_proposal(
    workspace: Path,
    *,
    lane: str,
    invocation_id: str,
    request_id: str,
    artifact_id: str,
    payload: dict[str, object],
) -> None:
    scratch = workspace / "scratch" / invocation_id
    proposal_path = scratch / f"{artifact_id}.json"
    _write_json(proposal_path, payload)
    request_path = scratch / "submit_request.json"
    _write_json(
        request_path,
        _record(
            ArtifactSubmitRequest,
            request_id=request_id,
            run_id=RUN_ID,
            artifact_id=artifact_id,
            invocation_id=invocation_id,
            input_path=proposal_path.relative_to(workspace).as_posix(),
            expected_store_revision=_store_revision(workspace),
            expected_artifact_revision=0,
        ).model_dump(mode="json", exclude_unset=False),
    )
    result = IntakeService(workspace, clock=CLOCK).submit_proposal(
        lane,
        request_path.relative_to(workspace).as_posix(),
    )
    assert result.status == "committed", result.to_dict()


def _advance_to_scout_ready(
    workspace: Path,
    *,
    topology: str = "default",
) -> CoreRunService:
    service = _initialize(workspace, topology=topology)
    doctor = service.doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id="REQ-DOCTOR-001",
            run_id=RUN_ID,
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert doctor.status == "committed", doctor.to_dict()
    planner = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-PLANNER",
        stage_id="source-discovery",
        role_id="source-planner",
    )
    candidates = workspace / "scratch" / planner / "source_candidates.yaml"
    candidates.parent.mkdir(parents=True, exist_ok=True)
    candidates.write_text("sources:\n  - SRC-001\n", encoding="utf-8")
    accepted = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-SOURCES",
            run_id=RUN_ID,
            artifact_id="source_candidates",
            invocation_id=planner,
            producer_tool_id=None,
            input_path=candidates.relative_to(workspace).as_posix(),
            expected_store_revision=_store_revision(workspace),
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )
    assert accepted.status == "committed", accepted.to_dict()
    provider = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-PROVIDER",
        stage_id="source-discovery",
        role_id="source-provider",
    )
    _submit_source(workspace, provider)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        source = store.load_snapshot(RUN_ID).sources[0]
    _complete_stage(
        service,
        workspace,
        stage_id="source-discovery",
        artifacts=[
            ("source_candidates", 1),
            (source.content_artifact_id, source.content_artifact_revision),
        ],
    )
    _complete_stage(service, workspace, stage_id="input-governance", artifacts=[])
    return service


def _candidate_payload() -> dict[str, object]:
    return {
        "schema_version": "briefloop.candidate_claims_proposal.v2",
        "proposal_id": "PROP-CANDIDATE-001",
        "run_id": RUN_ID,
        "created_at": NOW,
        "candidates": [
            {
                "candidate_id": "CAND-001",
                "source_id": "SRC-001",
                "statement": "ExampleCo opened a public pilot facility.",
                "evidence_text": (
                    "ExampleCo opened a public pilot facility on 2026-07-14."
                ),
                "topic": "operations",
                "claim_type": "fact",
                "confidence": "high",
            }
        ],
    }


def _screened_payload() -> dict[str, object]:
    return {
        "schema_version": "briefloop.screened_candidates_proposal.v2",
        "proposal_id": "PROP-SCREENED-001",
        "run_id": RUN_ID,
        "candidate_claims_proposal_id": "PROP-CANDIDATE-001",
        "created_at": NOW,
        "decisions": [
            {
                "candidate_id": "CAND-001",
                "decision": "selected",
                "reason_code": "public_evidence_in_scope",
                "explanation": "Public evidence is in scope.",
                "priority": "high",
            }
        ],
    }


def _advance_to_claim_ledger_ready(
    workspace: Path,
    *,
    topology: str = "default",
) -> CoreRunService:
    service = _advance_to_scout_ready(workspace, topology=topology)
    scout = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-SCOUT",
        stage_id="scout",
        role_id="scout",
    )
    _submit_proposal(
        workspace,
        lane="candidate",
        invocation_id=scout,
        request_id="REQ-CANDIDATE-001",
        artifact_id="candidate_claims",
        payload=_candidate_payload(),
    )
    screening_scout = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-SCREEN",
        stage_id="scout",
        role_id="scout",
    )
    _submit_proposal(
        workspace,
        lane="screened",
        invocation_id=screening_scout,
        request_id="REQ-SCREENED-001",
        artifact_id="screened_candidates",
        payload=_screened_payload(),
    )
    _complete_stage(
        service,
        workspace,
        stage_id="scout",
        artifacts=[("candidate_claims", 1), ("screened_candidates", 1)],
    )
    return service


def _advance_to_analyst_ready(
    workspace: Path,
    *,
    topology: str = "default",
) -> CoreRunService:
    service = _advance_to_claim_ledger_ready(workspace, topology=topology)
    claim_ledger = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-CLAIMS",
        stage_id="claim-ledger",
        role_id="claim-ledger",
    )
    _submit_proposal(
        workspace,
        lane="claim-drafts",
        invocation_id=claim_ledger,
        request_id="REQ-CLAIM-DRAFTS-001",
        artifact_id="claim_drafts",
        payload={
            "schema_version": "briefloop.claim_drafts_proposal.v2",
            "proposal_id": "PROP-CLAIM-DRAFTS-001",
            "run_id": RUN_ID,
            "screened_candidates_proposal_id": "PROP-SCREENED-001",
            "created_at": NOW,
            "drafts": [
                {
                    "draft_id": "DRAFT-001",
                    "statement": "ExampleCo opened a public pilot facility.",
                    "evidence_text": (
                        "ExampleCo opened a public pilot facility on 2026-07-14."
                    ),
                    "source_ids": ["SRC-001"],
                    "claim_type": "fact",
                }
            ],
        },
    )
    frozen = ClaimFreezeService(workspace, clock=CLOCK).freeze(
        _record(
            ClaimFreezeRequest,
            request_id="REQ-FREEZE-001",
            run_id=RUN_ID,
            claim_drafts_proposal_id="PROP-CLAIM-DRAFTS-001",
            expected_claim_drafts_artifact={
                "artifact_id": "claim_drafts",
                "revision": 1,
            },
            expected_store_revision=_store_revision(workspace),
            expected_ledger_revision=0,
        )
    )
    assert frozen.status == "committed", frozen.to_dict()
    _complete_stage(
        service,
        workspace,
        stage_id="claim-ledger",
        artifacts=[("claim_drafts", 1), ("claim_ledger", 1)],
    )
    return service


def _advance_to_auditor_ready(
    workspace: Path,
    *,
    audit_decision: str = "pass",
    audit_findings: list[dict[str, object]] | None = None,
) -> CoreRunService:
    service = _advance_to_analyst_ready(workspace)
    analyst = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-ANALYST",
        stage_id="analyst",
        role_id="analyst",
    )
    analyst_path = workspace / "scratch" / analyst / "analyst_draft_snapshot.md"
    analyst_path.parent.mkdir(parents=True, exist_ok=True)
    analyst_path.write_text(
        "# ExampleCo weekly brief\n\n"
        "ExampleCo opened a public pilot facility. [src:CL-0001]\n",
        encoding="utf-8",
    )
    analyst_result = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-ANALYST",
            run_id=RUN_ID,
            artifact_id="analyst_draft_snapshot",
            invocation_id=analyst,
            producer_tool_id="analyst-snapshot-v2",
            input_path=analyst_path.relative_to(workspace).as_posix(),
            expected_store_revision=_store_revision(workspace),
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )
    assert analyst_result.status == "committed", analyst_result.to_dict()
    _complete_stage(
        service,
        workspace,
        stage_id="analyst",
        artifacts=[("analyst_draft_snapshot", 1)],
    )

    editor = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-EDITOR",
        stage_id="editor",
        role_id="editor",
    )
    brief_path = workspace / "scratch" / editor / "audited_brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(
        "# ExampleCo weekly brief\n\n## Executive Summary\n\n"
        "ExampleCo opened a public pilot facility on 2026-07-14. "
        "[src:CL-0001]\n",
        encoding="utf-8",
    )
    editor_result = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-EDITOR",
            run_id=RUN_ID,
            artifact_id="audited_brief",
            invocation_id=editor,
            producer_tool_id=None,
            input_path=brief_path.relative_to(workspace).as_posix(),
            expected_store_revision=_store_revision(workspace),
            expected_artifact_revision=0,
            expected_parent_artifact={
                "artifact_id": "analyst_draft_snapshot",
                "revision": 1,
            },
        )
    )
    assert editor_result.status == "committed", editor_result.to_dict()
    _complete_stage(
        service,
        workspace,
        stage_id="editor",
        artifacts=[("analyst_draft_snapshot", 1), ("audited_brief", 1)],
    )

    auditor = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-AUDITOR",
        stage_id="auditor",
        role_id="auditor",
    )
    _submit_proposal(
        workspace,
        lane="audit",
        invocation_id=auditor,
        request_id="REQ-AUDIT-001",
        artifact_id="audit_proposal",
        payload={
            "schema_version": "briefloop.audit_proposal.v2",
            "proposal_id": "PROP-AUDIT-001",
            "run_id": RUN_ID,
            "artifact_id": "audited_brief",
            "artifact_revision": 1,
            "decision": audit_decision,
            "created_at": NOW,
            "findings": audit_findings or [],
        },
    )
    promoted = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).promote_audit_proposal(
        _record(
            AuditPromotionRequest,
            request_id="REQ-AUDIT-PROMOTE-001",
            run_id=RUN_ID,
            audit_proposal_id="PROP-AUDIT-001",
            expected_target_artifact={
                "artifact_id": "audited_brief",
                "revision": 1,
            },
            expected_audit_report_revision=0,
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert promoted.status == "committed", promoted.to_dict()
    return service


def _gate_request(workspace: Path, *, request_id: str = "REQ-GATE-001"):
    return _record(
        GateCheckRequest,
        request_id=request_id,
        run_id=RUN_ID,
        stage_id="auditor",
        expected_store_revision=_store_revision(workspace),
        expected_report_artifact_revision=0,
        expected_input_artifacts=[
            {"artifact_id": "claim_ledger", "revision": 1},
            {"artifact_id": "audited_brief", "revision": 1},
            {"artifact_id": "analyst_draft_snapshot", "revision": 1},
            {"artifact_id": "screened_candidates", "revision": 1},
            {"artifact_id": "candidate_claims", "revision": 1},
        ],
    )


def test_strict_topology_requires_independent_screener(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_scout_ready(workspace, topology="strict")
    scout = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-SCOUT",
        stage_id="scout",
        role_id="scout",
    )
    _submit_proposal(
        workspace,
        lane="candidate",
        invocation_id=scout,
        request_id="REQ-CANDIDATE-001",
        artifact_id="candidate_claims",
        payload=_candidate_payload(),
    )
    _complete_stage(
        service,
        workspace,
        stage_id="scout",
        artifacts=[("candidate_claims", 1)],
    )
    assert _stage(workspace, "scout").status == "complete"
    assert _stage(workspace, "screener").status == "ready"

    screener = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-SCREENER",
        stage_id="screener",
        role_id="screener",
    )
    _submit_proposal(
        workspace,
        lane="screened",
        invocation_id=screener,
        request_id="REQ-SCREENED-001",
        artifact_id="screened_candidates",
        payload=_screened_payload(),
    )
    _complete_stage(
        service,
        workspace,
        stage_id="screener",
        artifacts=[("candidate_claims", 1), ("screened_candidates", 1)],
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    transition = next(
        item
        for item in snapshot.stage_transitions
        if item.stage_id == "screener" and item.transition_kind == "complete"
    )
    assert transition.producer_invocation_id == screener
    assert _stage(workspace, "claim-ledger").status == "ready"


def test_human_assisted_writer_satisfies_analyst_and_editor(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_analyst_ready(workspace, topology="human_assisted")
    writer = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-WRITER",
        stage_id="analyst",
        role_id="writer",
    )
    brief_path = workspace / "scratch" / writer / "audited_brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(
        "# ExampleCo weekly brief\n\n"
        "ExampleCo opened a public pilot facility. [src:CL-0001]\n",
        encoding="utf-8",
    )
    accepted = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-WRITER",
            run_id=RUN_ID,
            artifact_id="audited_brief",
            invocation_id=writer,
            producer_tool_id=None,
            input_path=brief_path.relative_to(workspace).as_posix(),
            expected_store_revision=_store_revision(workspace),
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )
    assert accepted.status == "committed", accepted.to_dict()
    _complete_stage(
        service,
        workspace,
        stage_id="analyst",
        artifacts=[("audited_brief", 1)],
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    transitions = {
        (item.stage_id, item.transition_kind): item
        for item in snapshot.stage_transitions
    }
    assert transitions[("analyst", "complete")].producer_invocation_id == writer
    editor = transitions[("editor", "satisfied_by_topology")]
    assert editor.producer_invocation_id == writer
    assert editor.satisfaction_source_kind == "role"
    assert editor.satisfied_by_id == "writer"
    assert _stage(workspace, "auditor").status == "ready"


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        ("missing", "unavailable"),
        ("malformed", "invalid"),
        ("invalid_finding", "invalid"),
    ],
)
def test_known_negative_gate_outcome_is_durable_and_blocks_auditor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_status: str,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_auditor_ready(workspace)
    direct_report = workspace / "output" / "intermediate" / "auditor_quality_gate_report.json"
    direct_report.parent.mkdir(parents=True, exist_ok=True)
    direct_report.write_text('{"status":"pass"}', encoding="utf-8")
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_direct = store.load_snapshot(RUN_ID)
    assert not before_direct.gate_evaluations
    assert next(
        item
        for item in before_direct.artifacts
        if item.artifact_id == "auditor_quality_gate_report"
    ).current_revision == 0

    def known_negative(**_kwargs):
        result: dict[str, object] = {gate_id: [] for gate_id in GATE_IDS}
        if mode == "missing":
            result.pop("freshness")
        elif mode == "malformed":
            result["freshness"] = "not-a-finding-list"
        else:
            result["freshness"] = [
                {
                    "finding_type": "bad-finding",
                    "severity": "not-a-severity",
                    "blocking_level": "blocking",
                }
            ]
        return result

    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.gates."
        "evaluate_quality_gate_findings_preloaded",
        known_negative,
    )
    gate_result = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace)
    )
    assert gate_result.status == "committed", gate_result.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    freshness = next(
        item for item in snapshot.gate_evaluations if item.gate_id == "freshness"
    )
    assert freshness.status == expected_status
    assert freshness.blocking is True
    assert freshness.finding_ids
    before = snapshot.store_revision
    auditor = _stage(workspace, "auditor")
    completion = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id=f"REQ-COMPLETE-AUDITOR-{mode.upper()}",
            run_id=RUN_ID,
            stage_id="auditor",
            reason="auditor output complete",
            expected_stage_revision=auditor.revision,
            expected_store_revision=before,
            expected_artifact_revisions=[
                {"artifact_id": "claim_ledger", "revision": 1},
                {"artifact_id": "audited_brief", "revision": 1},
                {"artifact_id": "audit_report", "revision": 1},
                {
                    "artifact_id": "auditor_quality_gate_report",
                    "revision": 1,
                },
                {"artifact_id": "analyst_draft_snapshot", "revision": 1},
            ],
            expected_gate_evaluation_ids=[
                item.evaluation_id
                for item in snapshot.gate_evaluations
                if item.gate_id in REQUIRED_AUDITOR_GATES
            ],
        )
    )
    assert completion.status == "failed_uncommitted"
    assert completion.error_code == "stage_gate_binding_invalid"
    assert _store_revision(workspace) == before
    assert _stage(workspace, "auditor").status == "ready"


@pytest.mark.parametrize("audit_mode", ["fail", "error_finding"])
def test_negative_audit_truth_blocks_auditor_without_rewriting_report(
    tmp_path: Path,
    audit_mode: str,
) -> None:
    workspace = _workspace(tmp_path)
    findings = (
        [
            {
                "finding_code": "UNSUPPORTED-CLAIM",
                "severity": "error",
                "artifact_id": "audited_brief",
                "summary": "One claim is not supported by frozen evidence.",
            }
        ]
        if audit_mode == "error_finding"
        else []
    )
    service = _advance_to_auditor_ready(
        workspace,
        audit_decision="fail" if audit_mode == "fail" else "pass",
        audit_findings=findings,
    )
    gate_result = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace, request_id=f"REQ-GATE-{audit_mode.upper()}")
    )
    assert gate_result.status == "committed", gate_result.to_dict()

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
        report = next(
            item
            for item in snapshot.artifact_revisions
            if item.artifact_id == "audit_report" and item.revision == 1
        )
        report_bytes = store.read_artifact_revision_bytes(
            RUN_ID,
            report.artifact_id,
            report.revision,
        )
    before = snapshot.store_revision
    auditor = _stage(workspace, "auditor")
    result = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id=f"REQ-COMPLETE-AUDITOR-{audit_mode.upper()}",
            run_id=RUN_ID,
            stage_id="auditor",
            reason="negative audit truth cannot complete",
            expected_stage_revision=auditor.revision,
            expected_store_revision=before,
            expected_artifact_revisions=[
                {"artifact_id": "claim_ledger", "revision": 1},
                {"artifact_id": "audited_brief", "revision": 1},
                {"artifact_id": "audit_report", "revision": 1},
                {
                    "artifact_id": "auditor_quality_gate_report",
                    "revision": 1,
                },
                {"artifact_id": "analyst_draft_snapshot", "revision": 1},
            ],
            expected_gate_evaluation_ids=[
                item.evaluation_id
                for item in snapshot.gate_evaluations
                if item.gate_id in REQUIRED_AUDITOR_GATES
            ],
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "stage_artifact_binding_invalid",
    }
    assert _store_revision(workspace) == before
    assert _stage(workspace, "auditor") == auditor
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.read_artifact_revision_bytes(
            RUN_ID,
            report.artifact_id,
            report.revision,
        ) == report_bytes


def test_unexpected_gate_evaluator_failure_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_auditor_ready(workspace)
    before = _store_revision(workspace)

    def explode(**_kwargs):
        raise RuntimeError("injected evaluator failure")

    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.gates."
        "evaluate_quality_gate_findings_preloaded",
        explode,
    )
    result = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace)
    )
    assert result.status == "failed_uncommitted"
    assert result.error_code == "gate_input_binding_invalid"
    assert _store_revision(workspace) == before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    assert not snapshot.gate_evaluations
    report = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "auditor_quality_gate_report"
    )
    assert report.current_revision == 0


def test_gate_commit_failure_rolls_back_complete_negative_or_positive_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_auditor_ready(workspace)
    before = _store_revision(workspace)
    service = GateEvaluationService(workspace, clock=CLOCK)
    monkeypatch.setattr(
        service,
        "_open_store",
        _store_opener_with_failure(workspace, "after_records"),
    )
    result = service.evaluate(_gate_request(workspace, request_id="REQ-GATE-ROLLBACK"))

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "control_store_integrity_invalid",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    report = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "auditor_quality_gate_report"
    )
    assert snapshot.store_revision == before
    assert snapshot.gate_evaluations == ()
    assert snapshot.gate_findings == ()
    assert snapshot.gate_artifact_bindings == ()
    assert report.current_revision == 0
    assert (workspace / report.path).is_file()


def test_direct_legacy_control_files_have_zero_run_truth_effect(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace)
    before = _store_revision(workspace)
    doctor_before = _stage(workspace, "doctor")
    controls = workspace / "output" / "intermediate"
    controls.mkdir(parents=True, exist_ok=True)
    (controls / "workflow_state.json").write_text(
        '{"current_stage":"finalize","stage_statuses":{"auditor":"complete"}}',
        encoding="utf-8",
    )
    (controls / "artifact_registry.json").write_text(
        '{"artifact_count":999,"artifacts":{"audited_brief":{"status":"valid"}}}',
        encoding="utf-8",
    )
    for relative_path in (
        "output/intermediate/claim_ledger.json",
        "output/intermediate/audit_report.json",
        "output/intermediate/auditor_quality_gate_report.json",
        "output/intermediate/finalize_report.json",
    ):
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"status":"pass"}', encoding="utf-8")
    assert _store_revision(workspace) == before
    assert _stage(workspace, "doctor") == doctor_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    assert not snapshot.claims
    assert not snapshot.claim_freezes
    assert not snapshot.gate_evaluations
    assert {
        item.artifact_id: item.current_revision
        for item in snapshot.artifacts
        if item.artifact_id
        in {"claim_ledger", "audit_report", "auditor_quality_gate_report"}
    } == {
        "audit_report": 0,
        "auditor_quality_gate_report": 0,
        "claim_ledger": 0,
    }


def test_initialize_replay_is_exact_and_conflict_is_zero_write(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    service = CoreRunService(workspace, clock=CLOCK)
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id="REQ-INIT-REPLAY",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(
            workspace, "config.yaml"
        ).sha256,
        sources_config_sha256=read_workspace_file(
            workspace, "sources.yaml"
        ).sha256,
    )
    request = CoreRunInitializeRequest.model_validate(payload, strict=True)
    first = service.initialize(request)
    assert first.status == "committed"
    revision = _store_revision(workspace)

    replay = service.initialize(request)
    assert replay.status == "replayed"
    assert replay.receipt == first.receipt
    assert _store_revision(workspace) == revision

    changed = deepcopy(payload)
    changed["run_direction"]["brief_title"] = "Conflicting title"
    conflict = service.initialize(
        CoreRunInitializeRequest.model_validate(changed, strict=True)
    )
    assert conflict.status == "failed_uncommitted"
    assert conflict.error_code == "submission_replay_conflict"
    assert _store_revision(workspace) == revision


@pytest.mark.parametrize("filename", ["config.yaml", "sources.yaml"])
def test_secret_bearing_workspace_input_is_rejected_before_store_creation(
    tmp_path: Path,
    filename: str,
) -> None:
    workspace = _workspace(tmp_path)
    secret = "DO-NOT-PERSIST-THIS-SECRET"
    with (workspace / filename).open("a", encoding="utf-8") as stream:
        stream.write(f"\nprivate_provider:\n  api_key: {secret}\n")
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id=f"REQ-INIT-SECRET-{filename.split('.')[0].upper()}",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(
            workspace, "config.yaml"
        ).sha256,
        sources_config_sha256=read_workspace_file(
            workspace, "sources.yaml"
        ).sha256,
    )
    result = CoreRunService(workspace, clock=CLOCK).initialize(
        CoreRunInitializeRequest.model_validate(payload, strict=True)
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "core_run_contract_mismatch",
    }
    assert secret not in str(result.to_dict())
    assert not (workspace / "briefloop.db").exists()


@pytest.mark.parametrize(
    "relative_path",
    [
        "output/intermediate/runtime_manifest.json",
        "output/intermediate/workflow_state.json",
        "output/intermediate/artifact_registry.json",
        "output/intermediate/event_log.jsonl",
        "output/intermediate/finalize_report.json",
    ],
)
def test_legacy_json_control_workspace_cannot_become_fresh_v2(
    tmp_path: Path,
    relative_path: str,
) -> None:
    workspace = _workspace(tmp_path)
    marker = workspace / relative_path
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"legacy":true}', encoding="utf-8")
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id="REQ-INIT-LEGACY-STATE",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(
            workspace, "config.yaml"
        ).sha256,
        sources_config_sha256=read_workspace_file(
            workspace, "sources.yaml"
        ).sha256,
    )
    result = CoreRunService(workspace, clock=CLOCK).initialize(
        CoreRunInitializeRequest.model_validate(payload, strict=True)
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "unsupported_schema_version",
    }
    assert not (workspace / "briefloop.db").exists()
    assert marker.read_text(encoding="utf-8") == '{"legacy":true}'


@pytest.mark.parametrize("filename", ["config.yaml", "sources.yaml"])
def test_workspace_input_byte_change_blocks_doctor_without_stage_effect(
    tmp_path: Path,
    filename: str,
) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(workspace)
    before = _store_revision(workspace)
    with (workspace / filename).open("a", encoding="utf-8") as stream:
        stream.write("\n# exact input fingerprint changed\n")
    result = service.doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id=f"REQ-DOCTOR-CHANGED-{filename.split('.')[0].upper()}",
            run_id=RUN_ID,
            expected_store_revision=before,
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "doctor_check_failed",
    }
    assert _store_revision(workspace) == before
    assert _stage(workspace, "doctor").status == "ready"
    assert _stage(workspace, "source-discovery").status == "pending"


@pytest.mark.parametrize(
    ("failure_stage", "committed"),
    [("after_records", False), ("after_commit", True)],
)
def test_initialize_failure_cleans_revision_zero_or_exactly_replays_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    committed: bool,
) -> None:
    workspace = _workspace(tmp_path)
    service = CoreRunService(workspace, clock=CLOCK)
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id=f"REQ-INIT-INJECT-{failure_stage.upper()}",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(
            workspace, "config.yaml"
        ).sha256,
        sources_config_sha256=read_workspace_file(
            workspace, "sources.yaml"
        ).sha256,
    )
    request = CoreRunInitializeRequest.model_validate(payload, strict=True)
    original_create = SQLiteControlStore.create

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise ControlStoreIntegrityError("injected_core_run_failure")

    def create_with_failure(path, **kwargs):
        return original_create(path, **kwargs, _failure_hook=fail)

    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteControlStore,
            "create",
            staticmethod(create_with_failure),
        )
        result = service.initialize(request)

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "control_store_integrity_invalid",
    }
    database = workspace / "briefloop.db"
    if not committed:
        assert not database.exists()
        assert not database.with_name("briefloop.db.blobs").exists()
        return

    assert _store_revision(workspace) == 1
    replay = service.initialize(request)
    assert replay.status == "replayed"
    assert replay.receipt is not None
    assert _store_revision(workspace) == 1


def test_generic_stage_completion_cannot_claim_doctor_pass(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(workspace)
    before = _store_revision(workspace)
    doctor = _stage(workspace, "doctor")

    result = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id="REQ-COMPLETE-DOCTOR-FORGED",
            run_id=RUN_ID,
            stage_id="doctor",
            reason="caller claims doctor passed",
            expected_stage_revision=doctor.revision,
            expected_store_revision=before,
            expected_artifact_revisions=[],
            expected_gate_evaluation_ids=[],
        )
    )

    assert result.status == "failed_uncommitted"
    assert result.error_code == "stage_decision_not_supported"
    assert _store_revision(workspace) == before
    assert _stage(workspace, "doctor") == doctor


@pytest.mark.parametrize("mode", ["error", "exception"])
def test_doctor_adapter_failure_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(workspace)
    before = _store_revision(workspace)
    doctor = _stage(workspace, "doctor")
    source_discovery = _stage(workspace, "source-discovery")

    if mode == "error":
        monkeypatch.setattr(
            "multi_agent_brief.core_run_v2.service.run_doctor",
            lambda **_kwargs: [SimpleNamespace(status="ERROR")],
        )
    else:
        def fail_doctor(**_kwargs):
            raise RuntimeError("injected deterministic doctor failure")

        monkeypatch.setattr(
            "multi_agent_brief.core_run_v2.service.run_doctor",
            fail_doctor,
        )

    result = service.doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id=f"REQ-DOCTOR-{mode.upper()}",
            run_id=RUN_ID,
            expected_store_revision=before,
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "doctor_check_failed",
    }
    assert _store_revision(workspace) == before
    assert _stage(workspace, "doctor") == doctor
    assert _stage(workspace, "source-discovery") == source_discovery


@pytest.mark.parametrize("only", ["source_candidates", "eligible_source"])
def test_source_discovery_requires_candidates_and_eligible_source(
    tmp_path: Path,
    only: str,
) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(workspace)
    checked = service.doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id="REQ-DOCTOR-SOURCE-BINDING",
            run_id=RUN_ID,
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert checked.status == "committed", checked.to_dict()

    expected_artifacts: list[dict[str, object]] = []
    if only == "source_candidates":
        planner = _start_invocation(
            service,
            workspace,
            request_id="REQ-INVOKE-PLANNER-ONLY",
            stage_id="source-discovery",
            role_id="source-planner",
        )
        candidates = workspace / "scratch" / planner / "source_candidates.yaml"
        candidates.parent.mkdir(parents=True, exist_ok=True)
        candidates.write_text("sources:\n  - SRC-001\n", encoding="utf-8")
        accepted = ArtifactAcceptanceService(
            workspace,
            clock=CLOCK,
        ).submit_owned_artifact(
            _record(
                OwnedArtifactSubmitRequest,
                request_id="REQ-ARTIFACT-SOURCES-ONLY",
                run_id=RUN_ID,
                artifact_id="source_candidates",
                invocation_id=planner,
                producer_tool_id=None,
                input_path=candidates.relative_to(workspace).as_posix(),
                expected_store_revision=_store_revision(workspace),
                expected_artifact_revision=0,
                expected_parent_artifact=None,
            )
        )
        assert accepted.status == "committed", accepted.to_dict()
        expected_artifacts.append({"artifact_id": "source_candidates", "revision": 1})
    else:
        provider = _start_invocation(
            service,
            workspace,
            request_id="REQ-INVOKE-PROVIDER-ONLY",
            stage_id="source-discovery",
            role_id="source-provider",
        )
        _submit_source(workspace, provider)
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            source = store.load_snapshot(RUN_ID).sources[0]
        expected_artifacts.append(
            {
                "artifact_id": source.content_artifact_id,
                "revision": source.content_artifact_revision,
            }
        )

    before = _store_revision(workspace)
    stage = _stage(workspace, "source-discovery")
    result = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id=f"REQ-COMPLETE-SOURCE-{only.upper()}",
            run_id=RUN_ID,
            stage_id="source-discovery",
            reason="one-sided source binding cannot complete",
            expected_stage_revision=stage.revision,
            expected_store_revision=before,
            expected_artifact_revisions=expected_artifacts,
            expected_gate_evaluation_ids=[],
        )
    )

    assert result.status == "failed_uncommitted"
    assert result.error_code == "stage_artifact_binding_invalid"
    assert _store_revision(workspace) == before
    assert _stage(workspace, "source-discovery") == stage


@pytest.mark.parametrize(
    ("failure_stage", "committed"),
    [("after_records", False), ("after_commit", True)],
)
def test_doctor_commit_failure_is_typed_and_postcommit_exactly_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    committed: bool,
) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(workspace)
    before = _store_revision(workspace)
    request = _record(
        IntegrityCheckRequest,
        request_id=f"REQ-DOCTOR-INJECT-{failure_stage.upper()}",
        run_id=RUN_ID,
        expected_store_revision=before,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            service,
            "_open_store",
            _store_opener_with_failure(workspace, failure_stage),
        )
        result = service.doctor_check(request)

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "control_store_integrity_invalid",
    }
    if not committed:
        assert _store_revision(workspace) == before
        assert _stage(workspace, "doctor").status == "ready"
        assert _stage(workspace, "source-discovery").status == "pending"
        return

    assert _store_revision(workspace) == before + 1
    assert _stage(workspace, "doctor").status == "complete"
    assert _stage(workspace, "source-discovery").status == "ready"
    replay = service.doctor_check(request)
    assert replay.status == "replayed"
    assert replay.receipt is not None
    assert _store_revision(workspace) == before + 1


def test_artifact_commit_failure_leaves_only_unbound_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    core = _advance_to_analyst_ready(workspace)
    invocation_id = _start_invocation(
        core,
        workspace,
        request_id="REQ-INVOKE-ANALYST-ROLLBACK",
        stage_id="analyst",
        role_id="analyst",
    )
    scratch = workspace / "scratch" / invocation_id / "analyst_draft_snapshot.md"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    content = b"# Unbound draft\n\nThis checkout must not become run truth.\n"
    scratch.write_bytes(content)
    before = _store_revision(workspace)
    service = ArtifactAcceptanceService(workspace, clock=CLOCK)
    monkeypatch.setattr(
        service,
        "_open_store",
        _store_opener_with_failure(workspace, "after_records"),
    )
    result = service.submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-ROLLBACK",
            run_id=RUN_ID,
            artifact_id="analyst_draft_snapshot",
            invocation_id=invocation_id,
            producer_tool_id="analyst-snapshot-v2",
            input_path=scratch.relative_to(workspace).as_posix(),
            expected_store_revision=before,
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "control_store_integrity_invalid",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    artifact = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "analyst_draft_snapshot"
    )
    assert snapshot.store_revision == before
    assert artifact.current_revision == 0
    assert not any(
        item.accepted_transaction_id == "REQ-ARTIFACT-ROLLBACK"
        for item in snapshot.owned_artifact_submissions
    )
    assert (workspace / artifact.path).read_bytes() == content
    analyst = _stage(workspace, "analyst")
    blocked = core.complete_stage(
        _record(
            StageCompleteRequest,
            request_id="REQ-COMPLETE-UNBOUND-ARTIFACT",
            run_id=RUN_ID,
            stage_id="analyst",
            reason="unbound bytes cannot satisfy the stage",
            expected_stage_revision=analyst.revision,
            expected_store_revision=before,
            expected_artifact_revisions=[
                {"artifact_id": "analyst_draft_snapshot", "revision": 1}
            ],
            expected_gate_evaluation_ids=[],
        )
    )
    assert blocked.status == "failed_uncommitted"
    assert blocked.error_code == "stage_artifact_binding_invalid"
    assert _store_revision(workspace) == before


def test_claim_commit_failure_rolls_back_claims_bindings_freeze_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    core = _advance_to_claim_ledger_ready(workspace)
    invocation_id = _start_invocation(
        core,
        workspace,
        request_id="REQ-INVOKE-CLAIMS-ROLLBACK",
        stage_id="claim-ledger",
        role_id="claim-ledger",
    )
    _submit_proposal(
        workspace,
        lane="claim-drafts",
        invocation_id=invocation_id,
        request_id="REQ-CLAIM-DRAFTS-ROLLBACK",
        artifact_id="claim_drafts",
        payload={
            "schema_version": "briefloop.claim_drafts_proposal.v2",
            "proposal_id": "PROP-CLAIM-DRAFTS-ROLLBACK",
            "run_id": RUN_ID,
            "screened_candidates_proposal_id": "PROP-SCREENED-001",
            "created_at": NOW,
            "drafts": [
                {
                    "draft_id": "DRAFT-ROLLBACK",
                    "statement": "ExampleCo opened a public pilot facility.",
                    "evidence_text": (
                        "ExampleCo opened a public pilot facility on 2026-07-14."
                    ),
                    "source_ids": ["SRC-001"],
                    "claim_type": "fact",
                }
            ],
        },
    )
    before = _store_revision(workspace)
    service = ClaimFreezeService(workspace, clock=CLOCK)
    monkeypatch.setattr(
        service,
        "_open_store",
        _store_opener_with_failure(workspace, "after_records"),
    )
    result = service.freeze(
        _record(
            ClaimFreezeRequest,
            request_id="REQ-FREEZE-ROLLBACK",
            run_id=RUN_ID,
            claim_drafts_proposal_id="PROP-CLAIM-DRAFTS-ROLLBACK",
            expected_claim_drafts_artifact={
                "artifact_id": "claim_drafts",
                "revision": 1,
            },
            expected_store_revision=before,
            expected_ledger_revision=0,
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "control_store_integrity_invalid",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    ledger = next(
        item for item in snapshot.artifacts if item.artifact_id == "claim_ledger"
    )
    assert snapshot.store_revision == before
    assert snapshot.claims == ()
    assert snapshot.claim_source_bindings == ()
    assert snapshot.claim_freezes == ()
    assert ledger.current_revision == 0
    assert (workspace / ledger.path).is_file()


def test_stage_state_without_transition_rolls_back(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace)
    before = _store_revision(workspace)
    doctor = _stage(workspace, "doctor")

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        unit = store.begin(
            RUN_ID,
            "TX-FORGED-STAGE-STATE",
            "structural-test",
            before,
        )
        unit.put_stage_state(
            _record(
                StageState,
                run_id=RUN_ID,
                stage_id="doctor",
                status="complete",
                revision=doctor.revision + 1,
                updated_at=NOW,
            )
        )
        with pytest.raises(ControlStoreIntegrityError) as exc_info:
            unit.commit()
    assert exc_info.value.code == "core_run_relation_invalid"
    assert _store_revision(workspace) == before
    assert _stage(workspace, "doctor") == doctor


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_protected_checkout_mutation_records_contamination_and_blocks_effect(
    tmp_path: Path,
    mutation: str,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_scout_ready(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    candidate_record = next(
        item for item in snapshot.artifacts if item.artifact_id == "source_candidates"
    )
    candidate_path = workspace / candidate_record.path
    if mutation == "edit":
        candidate_path.write_text("sources:\n  - MUTATED\n", encoding="utf-8")
    else:
        candidate_path.unlink()
    before = snapshot.store_revision
    result = service.start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-INVOKE-CONTAMINATED",
            run_id=RUN_ID,
            stage_id="scout",
            role_id="scout",
            runtime="operator",
            expected_store_revision=before,
        )
    )
    assert result.status == "blocked"
    assert result.error_code == "frozen_artifact_contaminated"
    assert result.receipt is not None
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after = store.load_snapshot(RUN_ID)
    assert after.store_revision == before + 1
    assert after.run_integrity_records[-1].status == "contaminated"
    assert after.run_integrity_records[-1].affected_artifact_id == "source_candidates"
    assert not any(
        item.invocation_id == result.primary_record_id for item in after.invocations
    )
    assert _stage(workspace, "scout").status == "ready"

    repeated = service.start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-INVOKE-CONTAMINATED-AGAIN",
            run_id=RUN_ID,
            stage_id="scout",
            role_id="scout",
            runtime="operator",
            expected_store_revision=after.store_revision,
        )
    )
    assert repeated.status == "failed_uncommitted"
    assert repeated.error_code == "core_run_integrity_blocked"
    assert _store_revision(workspace) == after.store_revision


def test_claim_freeze_is_byte_deterministic_for_equivalent_inputs(
    tmp_path: Path,
) -> None:
    workspaces = [tmp_path / "left", tmp_path / "right"]
    ledgers: list[bytes] = []
    claim_payloads: list[list[dict[str, object]]] = []
    for root in workspaces:
        workspace = _workspace(root)
        _advance_to_analyst_ready(workspace)
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            snapshot = store.load_snapshot(RUN_ID)
            freeze = snapshot.claim_freezes[0]
            ledgers.append(
                store.read_artifact_revision_bytes(
                    RUN_ID,
                    freeze.ledger_artifact.artifact_id,
                    freeze.ledger_artifact.revision,
                )
            )
            claim_payloads.append(
                [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in snapshot.claims
                ]
            )
    assert ledgers[0] == ledgers[1]
    assert claim_payloads[0] == claim_payloads[1]


def test_default_core_spine_reaches_finalize_ready(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_auditor_ready(workspace)

    assert _stage(workspace, "scout").status == "complete"
    assert _stage(workspace, "screener").status == "complete"
    assert _stage(workspace, "claim-ledger").status == "complete"

    gate_result = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace)
    )
    assert gate_result.status == "committed", gate_result.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    gate_ids = [
        item.evaluation_id
        for item in snapshot.gate_evaluations
        if item.gate_id in REQUIRED_AUDITOR_GATES
    ]
    _complete_stage(
        service,
        workspace,
        stage_id="auditor",
        artifacts=[
            ("claim_ledger", 1),
            ("audited_brief", 1),
            ("audit_report", 1),
            ("auditor_quality_gate_report", 1),
            ("analyst_draft_snapshot", 1),
        ],
        gate_evaluation_ids=gate_ids,
    )

    assert _stage(workspace, "auditor").status == "complete"
    assert _stage(workspace, "finalize").status == "ready"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        completed = store.load_snapshot(RUN_ID)
        audit_revision = next(
            item
            for item in completed.artifact_revisions
            if item.artifact_id == "audit_report" and item.revision == 1
        )
        audit_bytes = store.read_artifact_revision_bytes(
            RUN_ID,
            audit_revision.artifact_id,
            audit_revision.revision,
        )
    assert not completed.approvals
    assert not completed.deliveries
    assert not any(
        item.stage_id == "finalize" and item.transition_kind == "complete"
        for item in completed.stage_transitions
    )

    late_promotion = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).promote_audit_proposal(
        _record(
            AuditPromotionRequest,
            request_id="REQ-AUDIT-PROMOTE-LATE",
            run_id=RUN_ID,
            audit_proposal_id="PROP-AUDIT-001",
            expected_target_artifact={
                "artifact_id": "audited_brief",
                "revision": 1,
            },
            expected_audit_report_revision=1,
            expected_store_revision=completed.store_revision,
        )
    )
    assert late_promotion.status == "failed_uncommitted"
    assert late_promotion.error_code == "stage_not_current"
    assert _store_revision(workspace) == completed.store_revision
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.read_artifact_revision_bytes(
            RUN_ID,
            audit_revision.artifact_id,
            audit_revision.revision,
        ) == audit_bytes


def test_store_rejects_missing_core_receipt_reverse_relation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace)

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        store._connection.execute(
            "DROP TRIGGER transaction_stage_transitions_no_delete"
        )
        store._connection.execute(
            """
            DELETE FROM transaction_stage_transitions
            WHERE run_id = ? AND position = 0
            """,
            (RUN_ID,),
        )
        store._connection.execute(
            "CREATE TRIGGER transaction_stage_transitions_no_delete "
            "BEFORE DELETE ON transaction_stage_transitions "
            "BEGIN SELECT RAISE(ABORT, 'append_only'); END;"
        )
        store._connection.commit()

        with pytest.raises(ControlStoreIntegrityError) as exc_info:
            store.load_snapshot(RUN_ID)
    assert exc_info.value.code == "transaction_relation_mismatch"
