"""Drive an initialized brief workspace to a target core-run stage.

A single role cannot be invoked in isolation: the core run is a Store-driven
stage machine, so reaching (for example) the Auditor means walking the run
there first.  The constants and helpers below were extracted verbatim from
``tests/test_core_run_v2.py``; this module is now their canonical home and
is consumed both by the core-run tests (which re-import them unchanged) and
by the evaluation stack's rollout adapter.

Determinism: the fixed ``RUN_ID``/``WORKSPACE_ID`` ids and the fixed
``CLOCK`` make a seeded workspace reproducible.

Import boundary: this module must not import from ``tests/`` or from the
CLI layer.  The workspace itself is created by the caller (for example via
``create_demo_workspace``) before any helper here runs; ``pytest`` is
imported lazily inside the Windows skip guard so that non-dev installs of
the package can import this module without the test runner installed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

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
    StageCompleteRequest,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.core_run_v2 import (
    ArtifactAcceptanceService,
    ClaimFreezeService,
    CoreRunService,
    GateEvaluationService,
)
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.policy import REQUIRED_AUDITOR_GATES
from multi_agent_brief.intake_v2.service import IntakeService

RUN_ID = "RUN-CORE-V2-001"
WORKSPACE_ID = "WS-CORE-V2-001"
NOW = "2026-07-15T12:00:00Z"
CLOCK = lambda: datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _require_supported_working_projection() -> None:
    if sys.platform == "win32":
        # Lazy import: pytest is a dev-only dependency, and this branch only
        # ever executes under the test runner.  (In the original test module
        # ``pytest`` was a module-level import.)
        import pytest

        pytest.skip("working-checkout publication is precommit unsupported on Windows")


def _record(model_type, **values):
    return model_type.model_validate(
        {"schema_version": model_type.schema_id, **values},
        strict=True,
    )


def _bind_init_payload(payload: dict[str, object]) -> dict[str, object]:
    binding = dict(payload["runtime_adapter_binding"])  # type: ignore[arg-type]
    binding["run_id"] = payload["run_id"]
    binding["runtime"] = payload["runtime"]
    topology = str(payload["role_topology"])
    supported = set(binding["supported_role_topologies"])  # type: ignore[arg-type]
    supported.add(topology)
    binding["supported_role_topologies"] = sorted(supported)
    binding.pop("binding_fingerprint", None)
    binding["binding_fingerprint"] = canonical_fingerprint(binding)
    payload["runtime_adapter_binding"] = binding
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> bytes:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _store_revision(workspace: Path) -> int:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        return store.current_revision


def _stage(workspace: Path, stage_id: str):
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    return next(item for item in snapshot.stage_states if item.stage_id == stage_id)


def _initialize(
    workspace: Path,
    *,
    topology: str = "default",
    input_governance_required: bool = False,
    role_ids: list[str] | None = None,
    output_contract: dict[str, object] | None = None,
    execution_authorization: dict[str, object] | None = None,
) -> CoreRunService:
    service = CoreRunService(workspace, clock=CLOCK)
    request = deepcopy(CoreRunInitializeRequest.minimal_example)
    if role_ids is not None:
        request["runtime_adapter_binding"]["role_ids"] = role_ids
    if output_contract is not None:
        request["run_direction"]["output_contract"] = output_contract
    if execution_authorization is not None:
        request["execution_authorization"] = execution_authorization
    request.update(
        request_id="REQ-INIT-001",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        role_topology=topology,
        input_governance_required=input_governance_required,
        workspace_config_sha256=read_workspace_file(workspace, "config.yaml").sha256,
        sources_config_sha256=read_workspace_file(workspace, "sources.yaml").sha256,
    )
    result = service.initialize(
        CoreRunInitializeRequest.model_validate(
            _bind_init_payload(request), strict=True
        )
    )
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
    _require_supported_working_projection()
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
    _require_supported_working_projection()
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
    role_ids: list[str] | None = None,
    output_contract: dict[str, object] | None = None,
) -> CoreRunService:
    _require_supported_working_projection()
    service = _initialize(
        workspace,
        topology=topology,
        role_ids=role_ids,
        output_contract=output_contract,
    )
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
    _require_supported_working_projection()
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
    role_ids: list[str] | None = None,
    output_contract: dict[str, object] | None = None,
) -> CoreRunService:
    service = _advance_to_scout_ready(
        workspace,
        topology=topology,
        role_ids=role_ids,
        output_contract=output_contract,
    )
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
    role_ids: list[str] | None = None,
    output_contract: dict[str, object] | None = None,
) -> CoreRunService:
    service = _advance_to_claim_ledger_ready(
        workspace,
        topology=topology,
        role_ids=role_ids,
        output_contract=output_contract,
    )
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


def _advance_before_auditor(
    workspace: Path,
    *,
    output_contract: dict[str, object] | None = None,
) -> CoreRunService:
    service = _advance_to_analyst_ready(workspace, output_contract=output_contract)
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

    return service


def _advance_to_auditor_ready(
    workspace: Path,
    *,
    audit_decision: str = "pass",
    audit_findings: list[dict[str, object]] | None = None,
    output_contract: dict[str, object] | None = None,
) -> CoreRunService:
    service = _advance_before_auditor(workspace, output_contract=output_contract)
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


SEEDABLE_STAGES = (
    "scout",
    "screener",
    "claim-ledger",
    "analyst",
    "auditor",
    "finalize",
)


class StagingError(Exception):
    """Raised when a workspace cannot be advanced to the requested stage."""


# ``screener`` seeds through the input-governance variant: that helper is the
# only walk whose end state leaves the run ready for input screening work.
_ADVANCE_BY_STAGE = {
    "scout": _advance_to_scout_ready,
    "screener": _advance_to_input_governance_ready,
    "claim-ledger": _advance_to_claim_ledger_ready,
    "analyst": _advance_to_analyst_ready,
    "auditor": _advance_to_auditor_ready,
    "finalize": _advance_to_finalize_ready,
}


def seed_workspace_to_stage(
    workspace: Path, stage_id: str, **kwargs
) -> CoreRunService:
    """Advance ``workspace`` to ``stage_id`` and return the run service.

    ``workspace`` must already be an initialized brief workspace (containing
    ``config.yaml`` and ``sources.yaml``).  Extra keyword arguments pass
    through to the underlying stage helper (for example ``topology``,
    ``role_ids``, ``output_contract``, or ``audit_findings``).  Unknown
    stage ids raise :class:`StagingError`.
    """
    advance = _ADVANCE_BY_STAGE.get(stage_id)
    if advance is None:
        raise StagingError(f"stage {stage_id!r} is not seedable")
    return advance(workspace, **kwargs)
