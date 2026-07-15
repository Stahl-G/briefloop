from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.contracts.v2 import (
    ArtifactSubmitRequest,
    AuditPromotionRequest,
    ClaimFreezeRequest,
    CoreRunEventBinding,
    CoreRunInitializeRequest,
    EventEnvelope,
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
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
)
from multi_agent_brief.core_run_v2 import (
    ArtifactAcceptanceService,
    ClaimFreezeService,
    CoreRunService,
    GateEvaluationService,
)
from multi_agent_brief.core_run_v2.artifacts import _input_classification_bytes
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.policy import REQUIRED_AUDITOR_GATES
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.verifier import (
    CoreRunDomainVerifier,
    _CORE_EFFECT_BINDING_RULES,
    _verified_core_receipt_binding,
    resolve_core_replay,
)
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
    input_governance_required: bool = False,
) -> CoreRunService:
    service = CoreRunService(workspace, clock=CLOCK)
    request = deepcopy(CoreRunInitializeRequest.minimal_example)
    request.update(
        request_id="REQ-INIT-001",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        role_topology=topology,
        input_governance_required=input_governance_required,
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
    expected_artifact_revision: int = 0,
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
            expected_artifact_revision=expected_artifact_revision,
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


def _advance_to_input_governance_ready(workspace: Path) -> CoreRunService:
    service = _initialize(workspace, input_governance_required=True)
    doctor = service.doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id="REQ-DOCTOR-INPUT-GOV",
            run_id=RUN_ID,
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert doctor.status == "committed", doctor.to_dict()
    planner = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-PLANNER-INPUT-GOV",
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
            request_id="REQ-ARTIFACT-SOURCES-INPUT-GOV",
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
        request_id="REQ-INVOKE-PROVIDER-INPUT-GOV",
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
    assert _stage(workspace, "input-governance").status == "ready"
    return service


def test_input_governance_accepts_only_recomputed_canonical_tool_bytes(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_input_governance_ready(workspace)
    scratch = workspace / "scratch" / "input-governance-v2"
    scratch.mkdir(parents=True, exist_ok=True)
    candidate = scratch / "input_classification.json"
    candidate.write_bytes(b"this is not json\n")
    before = _store_revision(workspace)
    request_values = {
        "run_id": RUN_ID,
        "artifact_id": "input_classification",
        "invocation_id": None,
        "producer_tool_id": "input-governance-v2",
        "input_path": candidate.relative_to(workspace).as_posix(),
        "expected_store_revision": before,
        "expected_artifact_revision": 0,
        "expected_parent_artifact": None,
    }
    rejected = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-INPUT-GOV-FORGED",
            **request_values,
        )
    )
    assert rejected.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "artifact_input_unsafe",
    }
    assert _store_revision(workspace) == before
    assert _stage(workspace, "input-governance").status == "ready"
    assert not (
        workspace / "output" / "intermediate" / "input_classification.json"
    ).exists()

    canonical = _input_classification_bytes(workspace)
    candidate.write_bytes(canonical)
    accepted = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-INPUT-GOV-CANONICAL",
            **request_values,
        )
    )
    assert accepted.status == "committed", accepted.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.read_artifact_revision_bytes(
            RUN_ID,
            "input_classification",
            1,
        ) == canonical
    _complete_stage(
        service,
        workspace,
        stage_id="input-governance",
        artifacts=[("input_classification", 1)],
    )
    assert _stage(workspace, "input-governance").status == "complete"


def test_input_classification_exact_replay_precedes_current_input_scan(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_input_governance_ready(workspace)
    scratch = workspace / "scratch" / "input-governance-v2"
    scratch.mkdir(parents=True, exist_ok=True)
    candidate = scratch / "input_classification.json"
    original_content = _input_classification_bytes(workspace)
    candidate.write_bytes(original_content)
    before = _store_revision(workspace)
    request = _record(
        OwnedArtifactSubmitRequest,
        request_id="REQ-INPUT-GOV-REPLAY",
        run_id=RUN_ID,
        artifact_id="input_classification",
        invocation_id=None,
        producer_tool_id="input-governance-v2",
        input_path=candidate.relative_to(workspace).as_posix(),
        expected_store_revision=before,
        expected_artifact_revision=0,
        expected_parent_artifact=None,
    )
    service = ArtifactAcceptanceService(workspace, clock=CLOCK)
    first = service.submit_owned_artifact(request)
    assert first.status == "committed", first.to_dict()
    committed_revision = _store_revision(workspace)

    (workspace / "input" / "later.md").write_text("later\n", encoding="utf-8")
    replay = service.submit_owned_artifact(request)
    assert replay.status == "replayed"
    assert replay.receipt == first.receipt
    assert replay.primary_record_id == first.primary_record_id
    assert _store_revision(workspace) == committed_revision

    candidate.write_bytes(_input_classification_bytes(workspace))
    conflict = service.submit_owned_artifact(request)
    assert conflict.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "submission_replay_conflict",
    }
    assert _store_revision(workspace) == committed_revision

    candidate.write_bytes(original_content)
    stale = service.submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            **{
                **request.model_dump(mode="python", exclude_unset=False),
                "request_id": "REQ-INPUT-GOV-STALE",
                "expected_store_revision": committed_revision,
                "expected_artifact_revision": 1,
            },
        )
    )
    assert stale.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "artifact_input_unsafe",
    }
    assert _store_revision(workspace) == committed_revision


def test_input_classification_identity_is_workspace_relative_and_selector_stable(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    source = workspace / "input" / "source.md"
    source.write_text("public evidence\n", encoding="utf-8")
    canonical = _input_classification_bytes(workspace)
    payload = json.loads(canonical)
    reported_paths = [
        item[field]
        for lane in payload.values()
        for item in lane
        for field in ("path", "extracted_markdown")
        if item.get(field)
    ]
    assert reported_paths
    assert all(not Path(item).is_absolute() for item in reported_paths)
    assert "input/source.md" in reported_paths

    if os.name != "nt":
        alias = tmp_path / "workspace-alias"
        alias.symlink_to(workspace, target_is_directory=True)
        assert _input_classification_bytes(alias) == canonical

    if sys.platform == "darwin" and str(workspace).startswith("/private/var/"):
        var_alias = Path(str(workspace).removeprefix("/private"))
        assert var_alias.is_dir()
        assert _input_classification_bytes(var_alias) == canonical


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


def test_gate_batch_exactly_replays_and_a_second_request_is_zero_write(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    core = _advance_to_auditor_ready(workspace)
    service = GateEvaluationService(workspace, clock=CLOCK)
    request = _gate_request(workspace, request_id="REQ-GATE-REPLAY")
    first = service.evaluate(request)
    assert first.status == "committed", first.to_dict()
    committed_revision = _store_revision(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    report_path = workspace / next(
        item.path
        for item in snapshot.artifacts
        if item.artifact_id == "auditor_quality_gate_report"
    )
    report_bytes = report_path.read_bytes()

    replay = service.evaluate(request)
    assert replay.status == "replayed"
    assert replay.receipt == first.receipt
    assert replay.primary_record_id == first.primary_record_id
    assert _store_revision(workspace) == committed_revision
    assert report_path.read_bytes() == report_bytes

    second_values = request.model_dump(mode="python", exclude_unset=False)
    second_values.update(
        request_id="REQ-GATE-SECOND-BATCH",
        expected_store_revision=committed_revision,
        expected_report_artifact_revision=1,
    )
    second = service.evaluate(
        GateCheckRequest.model_validate(second_values, strict=True)
    )
    assert second.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "gate_policy_binding_invalid",
    }
    assert _store_revision(workspace) == committed_revision
    assert report_path.read_bytes() == report_bytes

    _complete_stage(
        core,
        workspace,
        stage_id="auditor",
        artifacts=[
            ("claim_ledger", 1),
            ("audited_brief", 1),
            ("audit_report", 1),
            ("auditor_quality_gate_report", 1),
            ("analyst_draft_snapshot", 1),
        ],
        gate_evaluation_ids=[
            item.evaluation_id
            for item in snapshot.gate_evaluations
            if item.gate_id in REQUIRED_AUDITOR_GATES
        ],
    )
    after_completion = _store_revision(workspace)
    assert _stage(workspace, "finalize").status == "ready"
    lifecycle_replay = service.evaluate(request)
    assert lifecycle_replay.status == "replayed"
    assert lifecycle_replay.receipt == first.receipt
    assert _store_revision(workspace) == after_completion
    assert report_path.read_bytes() == report_bytes


def test_audit_promotion_exact_replay_precedes_report_revision_and_stage_checks(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    core = _advance_to_auditor_ready(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        receipt = store.load_transaction_receipt(
            RUN_ID,
            "REQ-AUDIT-PROMOTE-001",
        )
    assert receipt is not None

    gate = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace)
    )
    assert gate.status == "committed", gate.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    _complete_stage(
        core,
        workspace,
        stage_id="auditor",
        artifacts=[
            ("claim_ledger", 1),
            ("audited_brief", 1),
            ("audit_report", 1),
            ("auditor_quality_gate_report", 1),
            ("analyst_draft_snapshot", 1),
        ],
        gate_evaluation_ids=[
            item.evaluation_id
            for item in snapshot.gate_evaluations
            if item.gate_id in REQUIRED_AUDITOR_GATES
        ],
    )
    after_completion = _store_revision(workspace)
    assert _stage(workspace, "finalize").status == "ready"

    original = _record(
        AuditPromotionRequest,
        request_id="REQ-AUDIT-PROMOTE-001",
        run_id=RUN_ID,
        audit_proposal_id="PROP-AUDIT-001",
        expected_target_artifact={
            "artifact_id": "audited_brief",
            "revision": 1,
        },
        expected_audit_report_revision=0,
        expected_store_revision=receipt.prior_revision,
    )
    replay = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).promote_audit_proposal(original)
    assert replay.status == "replayed"
    assert replay.receipt == receipt
    assert _store_revision(workspace) == after_completion

    changed_values = original.model_dump(mode="python", exclude_unset=False)
    changed_values["expected_audit_report_revision"] = 1
    conflict = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).promote_audit_proposal(
        AuditPromotionRequest.model_validate(changed_values, strict=True)
    )
    assert conflict.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "submission_replay_conflict",
    }
    assert _store_revision(workspace) == after_completion


def test_auditor_completion_rejects_report_for_a_different_artifact(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_auditor_ready(workspace)
    auditor = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-AUDITOR-WRONG-TARGET",
        stage_id="auditor",
        role_id="auditor",
    )
    _submit_proposal(
        workspace,
        lane="audit",
        invocation_id=auditor,
        request_id="REQ-AUDIT-WRONG-TARGET",
        artifact_id="audit_proposal",
        expected_artifact_revision=1,
        payload={
            "schema_version": "briefloop.audit_proposal.v2",
            "proposal_id": "PROP-AUDIT-WRONG-TARGET",
            "run_id": RUN_ID,
            "artifact_id": "claim_ledger",
            "artifact_revision": 1,
            "decision": "pass",
            "created_at": NOW,
            "findings": [],
        },
    )
    promoted = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).promote_audit_proposal(
        _record(
            AuditPromotionRequest,
            request_id="REQ-AUDIT-PROMOTE-WRONG-TARGET",
            run_id=RUN_ID,
            audit_proposal_id="PROP-AUDIT-WRONG-TARGET",
            expected_target_artifact={
                "artifact_id": "claim_ledger",
                "revision": 1,
            },
            expected_audit_report_revision=1,
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert promoted.status == "committed", promoted.to_dict()

    gate = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace)
    )
    assert gate.status == "committed", gate.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.load_snapshot(RUN_ID)
    gate_ids = [
        item.evaluation_id
        for item in before.gate_evaluations
        if item.gate_id in REQUIRED_AUDITOR_GATES
    ]
    auditor_stage = next(
        item for item in before.stage_states if item.stage_id == "auditor"
    )
    result = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id="REQ-COMPLETE-AUDITOR-WRONG-TARGET",
            run_id=RUN_ID,
            stage_id="auditor",
            reason="a report for a different artifact cannot complete audit",
            expected_stage_revision=auditor_stage.revision,
            expected_store_revision=before.store_revision,
            expected_artifact_revisions=[
                {"artifact_id": "claim_ledger", "revision": 1},
                {"artifact_id": "audited_brief", "revision": 1},
                {"artifact_id": "audit_report", "revision": 2},
                {
                    "artifact_id": "auditor_quality_gate_report",
                    "revision": 1,
                },
                {"artifact_id": "analyst_draft_snapshot", "revision": 1},
            ],
            expected_gate_evaluation_ids=gate_ids,
        )
    )
    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "stage_artifact_binding_invalid",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after = store.load_snapshot(RUN_ID)
    assert after.store_revision == before.store_revision
    assert after.stage_transitions == before.stage_transitions
    assert after.stage_states == before.stage_states
    assert _stage(workspace, "auditor").status == "ready"
    assert _stage(workspace, "finalize").status == "pending"


def test_domain_verifier_replays_the_exact_audited_brief_target(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_finalize_ready(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, RUN_ID)
        audit_revision = next(
            item
            for item in verified.snapshot.artifact_revisions
            if item.artifact_id == "audit_report" and item.revision == 1
        )
        ledger_revision = next(
            item
            for item in verified.snapshot.artifact_revisions
            if item.artifact_id == "claim_ledger" and item.revision == 1
        )
        forged_payload = json.loads(
            store.read_artifact_revision_bytes(
                RUN_ID,
                audit_revision.artifact_id,
                audit_revision.revision,
            )
        )
        forged_payload.update(
            target_artifact_id=ledger_revision.artifact_id,
            target_artifact_revision=ledger_revision.revision,
            target_artifact_sha256=ledger_revision.sha256,
        )
        forged_audit = canonical_json_bytes(forged_payload) + b"\n"

        def read_revision(
            run_id: str,
            artifact_id: str,
            revision: int,
        ) -> bytes:
            if artifact_id == "audit_report" and revision == 1:
                return forged_audit
            return store.read_artifact_revision_bytes(
                run_id,
                artifact_id,
                revision,
            )

        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            CoreRunDomainVerifier._verify_stage_chain(
                SimpleNamespace(read_artifact_revision_bytes=read_revision),
                verified.snapshot,
                verified.contracts,
                verified.binding,
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
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        CoreRunDomainVerifier().verify(store, RUN_ID)

    receipt = next(
        item
        for item in snapshot.transactions
        if item.transaction_id == "REQ-COMPLETE-ANALYST"
    )
    event_by_id = {item.event_id: item for item in snapshot.events}
    analyst_event = event_by_id[
        transitions[("analyst", "complete")].transition_event_id
    ]
    editor_event = event_by_id[editor.transition_event_id]
    forged_events = tuple(
        item.model_copy(
            update={
                "event_type": (
                    editor_event.event_type
                    if item.event_id == analyst_event.event_id
                    else analyst_event.event_type
                )
            }
        )
        if item.event_id in {analyst_event.event_id, editor_event.event_id}
        else item
        for item in snapshot.events
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(
            replace(snapshot, events=forged_events),
            receipt,
        )


def test_human_assisted_analyst_snapshot_routes_only_to_editor(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_analyst_ready(workspace, topology="human_assisted")
    analyst = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-ANALYST-HUMAN",
        stage_id="analyst",
        role_id="analyst",
    )
    analyst_path = workspace / "scratch" / analyst / "analyst_draft_snapshot.md"
    analyst_path.parent.mkdir(parents=True, exist_ok=True)
    analyst_path.write_text(
        "# Human-assisted analyst snapshot\n\n"
        "ExampleCo opened a public pilot facility. [src:CL-0001]\n",
        encoding="utf-8",
    )
    accepted = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-ANALYST-HUMAN",
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
    assert accepted.status == "committed", accepted.to_dict()
    _complete_stage(
        service,
        workspace,
        stage_id="analyst",
        artifacts=[("analyst_draft_snapshot", 1)],
    )
    assert _stage(workspace, "editor").status == "ready"

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.load_snapshot(RUN_ID)
    rejected_writer = service.start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-INVOKE-WRITER-AT-EDITOR",
            run_id=RUN_ID,
            stage_id="editor",
            role_id="writer",
            runtime="operator",
            expected_store_revision=before.store_revision,
        )
    )
    assert rejected_writer.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "invocation_owner_mismatch",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after = store.load_snapshot(RUN_ID)
    assert after.store_revision == before.store_revision
    assert after.invocations == before.invocations
    assert after.events == before.events

    editor = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-EDITOR-HUMAN",
        stage_id="editor",
        role_id="editor",
    )
    brief_path = workspace / "scratch" / editor / "audited_brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(
        "# Human-assisted edited brief\n\n"
        "ExampleCo opened a public pilot facility. [src:CL-0001]\n",
        encoding="utf-8",
    )
    editor_result = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-EDITOR-HUMAN",
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
    assert _stage(workspace, "auditor").status == "ready"

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, RUN_ID)
        snapshot = verified.snapshot
        submission = next(
            item
            for item in snapshot.owned_artifact_submissions
            if item.artifact_id == "audited_brief"
        )
        assert submission.parent_artifact is not None
        bad_parents = (
            None,
            submission.parent_artifact.model_copy(
                update={"artifact_id": "claim_ledger"}
            ),
            submission.parent_artifact.model_copy(update={"revision": 2}),
        )
        for bad_parent in bad_parents:
            forged_submissions = tuple(
                item.model_copy(update={"parent_artifact": bad_parent})
                if item.submission_id == submission.submission_id
                else item
                for item in snapshot.owned_artifact_submissions
            )
            with pytest.raises(
                CoreRunError,
                match="control_store_integrity_invalid",
            ):
                CoreRunDomainVerifier._verify_stage_chain(
                    store,
                    replace(
                        snapshot,
                        owned_artifact_submissions=forged_submissions,
                    ),
                    verified.contracts,
                    verified.binding,
                )


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


@pytest.mark.parametrize("filename", ["config.yaml", "sources.yaml"])
def test_initialize_replay_is_exact_and_conflict_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
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

    with (workspace / filename).open("a", encoding="utf-8") as stream:
        stream.write("\n# mutable input changed after initialization\n")

    def reject_workspace_reread(*_args, **_kwargs):
        raise AssertionError("initialize replay reread mutable workspace inputs")

    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.service.workspace_input_fingerprints",
        reject_workspace_reread,
    )
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
def test_new_initialize_rejects_workspace_input_hash_mismatch_without_store(
    tmp_path: Path,
    filename: str,
) -> None:
    workspace = _workspace(tmp_path)
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id=f"REQ-INIT-MISMATCH-{filename.split('.')[0].upper()}",
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
    with (workspace / filename).open("a", encoding="utf-8") as stream:
        stream.write("\n# changed before first initialize\n")

    result = CoreRunService(workspace, clock=CLOCK).initialize(
        CoreRunInitializeRequest.model_validate(payload, strict=True)
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "core_run_contract_mismatch",
    }
    assert not (workspace / "briefloop.db").exists()
    assert not (workspace / "briefloop.db.blobs").exists()


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


def test_non_core_receipt_cannot_own_a_core_event_binding(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace)
    before = _store_revision(workspace)
    transaction_id = "TX-NON-CORE-BINDING"
    event = _record(
        EventEnvelope,
        event_id="EVT-NON-CORE-BINDING",
        run_id=RUN_ID,
        event_type="quality_gate_checked",
        created_at=NOW,
        actor="system",
        transaction_id=transaction_id,
        stage_id="auditor",
        artifact_id=None,
        decision="continue",
        reason="forged core binding in a non-core receipt",
        metadata={},
        intake_binding=None,
        core_run_binding=CoreRunEventBinding.model_validate(
            {
                "request_id": transaction_id,
                "request_fingerprint": canonical_fingerprint(
                    {"request_id": transaction_id}
                ),
                "effect_kind": "gate_evaluation",
                "primary_record_id": "GATE-BATCH-NON-CORE",
                "outcome": "committed",
            },
            strict=True,
        ),
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        unit = store.begin(
            RUN_ID,
            transaction_id,
            "structural-test",
            before,
        )
        unit.append_event(event)
        unit.commit()
        assert store.current_revision == before + 1
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            CoreRunDomainVerifier().verify(store, RUN_ID)


def test_core_effect_receipt_binding_table_is_exact() -> None:
    assert set(_CORE_EFFECT_BINDING_RULES) == {
        "initialize",
        "invocation_start",
        "owned_artifact_acceptance",
        "claim_freeze",
        "audit_promotion",
        "gate_evaluation",
        "stage_transition",
        "integrity_contamination",
    }
    assert {
        effect: rule.receipt_event_counts
        for effect, rule in _CORE_EFFECT_BINDING_RULES.items()
    } == {
        "initialize": (("run_initialized", 1),),
        "invocation_start": (("role_invocation_started", 1),),
        "owned_artifact_acceptance": (("owned_artifact_accepted", 1),),
        "claim_freeze": (("claim_ledger_frozen", 1),),
        "audit_promotion": (("audit_proposal_promoted", 1),),
        "gate_evaluation": (("quality_gate_checked", 1),),
        "stage_transition": None,
        "integrity_contamination": (
            ("run_integrity_contaminated", 1),
            ("run_blocked", 1),
        ),
    }


def test_all_core_effects_replay_and_reject_extra_unbound_events(
    tmp_path: Path,
) -> None:
    complete_workspace = _workspace(tmp_path / "complete")
    _advance_to_finalize_ready(complete_workspace)
    with SQLiteControlStore.open(complete_workspace / "briefloop.db") as store:
        complete_snapshot = store.load_snapshot(RUN_ID)

    contaminated_workspace = _workspace(tmp_path / "contaminated")
    contaminated_service = _advance_to_scout_ready(contaminated_workspace)
    with SQLiteControlStore.open(
        contaminated_workspace / "briefloop.db"
    ) as store:
        before_contamination = store.load_snapshot(RUN_ID)
    candidate_path = contaminated_workspace / next(
        item.path
        for item in before_contamination.artifacts
        if item.artifact_id == "source_candidates"
    )
    candidate_path.write_text("sources:\n  - MUTATED\n", encoding="utf-8")
    blocked = contaminated_service.start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-EFFECT-TABLE-CONTAMINATION",
            run_id=RUN_ID,
            stage_id="scout",
            role_id="scout",
            runtime="operator",
            expected_store_revision=before_contamination.store_revision,
        )
    )
    assert blocked.status == "blocked", blocked.to_dict()
    with SQLiteControlStore.open(
        contaminated_workspace / "briefloop.db"
    ) as store:
        contaminated_snapshot = store.load_snapshot(RUN_ID)

    cases = {}
    for workspace, snapshot in (
        (complete_workspace, complete_snapshot),
        (contaminated_workspace, contaminated_snapshot),
    ):
        events = {item.event_id: item for item in snapshot.events}
        for receipt in snapshot.transactions:
            bound_events = [
                events[event_id]
                for event_id in receipt.event_ids
                if events[event_id].core_run_binding is not None
            ]
            if len(bound_events) != 1:
                continue
            binding = bound_events[0].core_run_binding
            assert binding is not None
            cases.setdefault(
                binding.effect_kind,
                (workspace, snapshot, receipt, binding),
            )
    assert set(cases) == set(_CORE_EFFECT_BINDING_RULES)

    for effect_kind, (workspace, snapshot, receipt, binding) in cases.items():
        replay_fingerprint = binding.request_fingerprint
        if effect_kind == "integrity_contamination":
            replay_fingerprint = next(
                item.request_fingerprint
                for item in snapshot.run_integrity_records
                if str(item.integrity_revision) == binding.primary_record_id
            )
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            before = store.current_revision
            replay = resolve_core_replay(
                store,
                run_id=RUN_ID,
                request_id=receipt.transaction_id,
                request_fingerprint=replay_fingerprint,
            )
            assert replay is not None
            assert replay.receipt == receipt
            assert replay.primary_record_id == binding.primary_record_id
            assert replay.status == (
                "blocked" if effect_kind == "integrity_contamination" else "replayed"
            )
            assert store.current_revision == before

        extra = _record(
            EventEnvelope,
            event_id=f"EVT-EXTRA-{effect_kind.upper().replace('_', '-')}",
            run_id=RUN_ID,
            event_type="run_blocked",
            created_at=NOW,
            actor="system",
            transaction_id=receipt.transaction_id,
            stage_id=None,
            artifact_id=None,
            decision="block",
            reason="forged_extra_event",
            metadata={},
            intake_binding=None,
            core_run_binding=None,
        )
        forged_receipt = receipt.model_copy(
            update={"event_ids": [*receipt.event_ids, extra.event_id]}
        )
        forged_snapshot = replace(
            snapshot,
            events=(*snapshot.events, extra),
        )
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            _verified_core_receipt_binding(forged_snapshot, forged_receipt)


def test_forged_core_primary_record_id_is_rejected_before_replay(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_scout_ready(workspace)
    database = workspace / "briefloop.db"
    with SQLiteControlStore.open(database) as store:
        row = store._connection.execute(
            "SELECT event_id, transaction_id, payload_json FROM events "
            "WHERE event_type = 'owned_artifact_accepted' LIMIT 1"
        ).fetchone()
        assert row is not None
        event_id, transaction_id, payload_json = row
        payload = json.loads(payload_json)
        fingerprint = payload["core_run_binding"]["request_fingerprint"]
        payload["core_run_binding"]["primary_record_id"] = "SUBMISSION-FORGED"
        trigger_sql = store._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'events_no_update'"
        ).fetchone()
        assert trigger_sql is not None
        store._connection.execute("DROP TRIGGER events_no_update")
        store._connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            (
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                event_id,
            ),
        )
        store._connection.execute(trigger_sql[0])
        store._connection.commit()

    with SQLiteControlStore.open(database) as store:
        revision = store.current_revision
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            CoreRunDomainVerifier().verify(store, RUN_ID)
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            resolve_core_replay(
                store,
                run_id=RUN_ID,
                request_id=transaction_id,
                request_fingerprint=fingerprint,
            )
        assert store.current_revision == revision


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
    request = _record(
        InvocationStartRequest,
        request_id="REQ-INVOKE-CONTAMINATED",
        run_id=RUN_ID,
        stage_id="scout",
        role_id="scout",
        runtime="operator",
        expected_store_revision=before,
    )
    result = service.start_invocation(request)
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
    contamination_event = next(
        item
        for item in after.events
        if item.event_type == "run_integrity_contaminated"
    )
    assert contamination_event.core_run_binding is not None
    base_request_fingerprint = canonical_fingerprint(
        request.model_dump(mode="json", exclude_unset=False)
    )
    contamination_record = after.run_integrity_records[-1]
    assert contamination_record.request_fingerprint == base_request_fingerprint
    observation_fingerprint = canonical_fingerprint(
        {
            "run_id": contamination_record.run_id,
            "artifact_id": contamination_record.affected_artifact_id,
            "artifact_revision": contamination_record.affected_artifact_revision,
            "expected_workspace_path": (
                contamination_record.expected_workspace_path
            ),
            "expected_sha256": contamination_record.expected_sha256,
            "observed_entry_kind": contamination_record.observed_entry_kind,
            "observed_sha256": contamination_record.observed_sha256,
        }
    )
    assert contamination_event.core_run_binding.request_fingerprint == (
        canonical_fingerprint(
            {
                "effect_kind": "integrity_contamination",
                "base_request_fingerprint": base_request_fingerprint,
                "observation_fingerprint": observation_fingerprint,
            }
        )
    )

    exact_replay = service.start_invocation(request)
    assert exact_replay.status == "blocked"
    assert exact_replay.receipt == result.receipt
    assert exact_replay.primary_record_id == result.primary_record_id
    assert _store_revision(workspace) == after.store_revision

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


def test_contamination_replay_binds_request_and_observation_identity(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_scout_ready(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.load_snapshot(RUN_ID)
    candidate = next(
        item for item in before.artifacts if item.artifact_id == "source_candidates"
    )
    (workspace / candidate.path).write_text(
        "sources:\n  - MUTATED\n",
        encoding="utf-8",
    )
    request = _record(
        InvocationStartRequest,
        request_id="REQ-CONTAMINATION-IDENTITY",
        run_id=RUN_ID,
        stage_id="scout",
        role_id="scout",
        runtime="operator",
        expected_store_revision=before.store_revision,
    )
    blocked = service.start_invocation(request)
    assert blocked.status == "blocked", blocked.to_dict()
    assert blocked.receipt is not None

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
        receipt = blocked.receipt
        event = next(
            item
            for item in snapshot.events
            if item.event_type == "run_integrity_contaminated"
            and item.transaction_id == request.request_id
        )
        binding = event.core_run_binding
        assert binding is not None
        record = next(
            item
            for item in snapshot.run_integrity_records
            if str(item.integrity_revision) == binding.primary_record_id
        )
        base_fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        assert record.request_fingerprint == base_fingerprint

        exact = resolve_core_replay(
            store,
            run_id=RUN_ID,
            request_id=request.request_id,
            request_fingerprint=base_fingerprint,
        )
        assert exact is not None
        assert exact.status == "blocked"
        assert exact.receipt == receipt

        with pytest.raises(CoreRunError, match="submission_replay_conflict"):
            resolve_core_replay(
                store,
                run_id=RUN_ID,
                request_id=request.request_id,
                request_fingerprint=canonical_fingerprint(
                    {
                        **request.model_dump(mode="json", exclude_unset=False),
                        "role_id": "editor",
                    }
                ),
            )

        forged_binding = binding.model_copy(
            update={"request_fingerprint": "0" * 64}
        )
        forged_events = tuple(
            item.model_copy(update={"core_run_binding": forged_binding})
            if item.event_id == event.event_id
            else item
            for item in snapshot.events
        )
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            _verified_core_receipt_binding(
                replace(snapshot, events=forged_events),
                receipt,
            )

        forged_records = tuple(
            item.model_copy(update={"request_fingerprint": "0" * 64})
            if item.integrity_revision == record.integrity_revision
            else item
            for item in snapshot.run_integrity_records
        )
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            _verified_core_receipt_binding(
                replace(snapshot, run_integrity_records=forged_records),
                receipt,
            )

        observed_sha256 = record.observed_sha256
        assert observed_sha256 is not None
        forged_observation = "0" * 64 if observed_sha256 != "0" * 64 else "1" * 64
        forged_records = tuple(
            item.model_copy(update={"observed_sha256": forged_observation})
            if item.integrity_revision == record.integrity_revision
            else item
            for item in snapshot.run_integrity_records
        )
        with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
            _verified_core_receipt_binding(
                replace(snapshot, run_integrity_records=forged_records),
                receipt,
            )


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


def _advance_to_finalize_ready(workspace: Path) -> CoreRunService:
    service = _advance_to_auditor_ready(workspace)
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
    return service


def test_default_core_spine_reaches_finalize_ready(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_finalize_ready(workspace)

    assert _stage(workspace, "scout").status == "complete"
    assert _stage(workspace, "screener").status == "complete"
    assert _stage(workspace, "claim-ledger").status == "complete"

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
