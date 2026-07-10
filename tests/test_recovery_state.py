from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.deliver_commands import (
    DeliverCommandError,
    E_DELIVERY_RUN_INTEGRITY_BLOCKED,
    _preflight_run_integrity_for_delivery,
)
from multi_agent_brief.experiments.experiment_080 import _registered_run_integrity
from multi_agent_brief.orchestrator.recovery_state import (
    OWNER_REVISION_SCHEMA,
    RECOVERY_AWAITING,
    RECOVERY_COMPLETED_NON_REFERENCE,
    RECOVERY_FINALIZE_COMPLETION_PENDING,
    RECOVERY_FINALIZE_RENDER_REQUIRED,
    RECOVERY_IN_PROGRESS,
    RECOVERY_INVALID,
    RECOVERY_NOT_APPLICABLE,
    RECOVERY_RERUN_PENDING,
    evaluate_recovery_state,
    recovery_stale_artifact_baselines,
)
from multi_agent_brief.orchestrator.runtime_state import build_completion_projection
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_REGISTRY_SCHEMA,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import EVENT_LOG_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.workflow import WORKFLOW_STATE_SCHEMA


ROOT = Path(__file__).resolve().parent.parent
RUN_ID = "run-recovery-test"
CONTAMINATION_ID = "event-contamination-001"
RECOVERY_ID = "repair-complete-001"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _event(
    event_type: str,
    event_id: str,
    *,
    stage_id: str | None = None,
    decision: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "schema_version": EVENT_LOG_SCHEMA,
        "event_id": event_id,
        "run_id": RUN_ID,
        "created_at": "2026-07-10T00:00:00Z",
        "event_type": event_type,
        "actor": "system",
        "stage_id": stage_id,
        "artifact_id": None,
        "decision": decision,
        "reason": event_type,
        "metadata": metadata or {},
    }


def _workspace(tmp_path: Path, *, current_stage: str | None = "editor") -> Path:
    ws = tmp_path / "workspace"
    (ws / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (ws / "config.yaml").write_text("project_name: Recovery Test\n", encoding="utf-8")
    intermediate = ws / "output" / "intermediate"
    _write_json(
        intermediate / "runtime_manifest.json",
        {"schema_version": RUNTIME_MANIFEST_SCHEMA, "run_id": RUN_ID},
    )
    _write_json(
        intermediate / "workflow_state.json",
        {
            "schema_version": WORKFLOW_STATE_SCHEMA,
            "run_id": RUN_ID,
            "current_stage": current_stage,
            "blocked": False,
            "blocking_reason": "",
            "stage_statuses": {},
            "run_integrity": {
                "status": "clean",
                "reference_eligible": True,
                "clean_single_shot": True,
                "reasons": [],
            },
        },
    )
    (intermediate / "event_log.jsonl").write_text("", encoding="utf-8")
    return ws


def _read_workflow(ws: Path) -> dict:
    return json.loads((ws / "output/intermediate/workflow_state.json").read_text(encoding="utf-8"))


def _write_workflow(ws: Path, workflow: dict) -> None:
    _write_json(ws / "output/intermediate/workflow_state.json", workflow)


def _write_registry(ws: Path) -> None:
    _write_json(
        ws / "output/intermediate/artifact_registry.json",
        {
            "schema_version": ARTIFACT_REGISTRY_SCHEMA,
            "run_id": RUN_ID,
            "artifacts": {},
        },
    )


def _write_events(ws: Path, events: list[dict]) -> None:
    path = ws / "output/intermediate/event_log.jsonl"
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _mark_contaminated(ws: Path) -> dict:
    workflow = _read_workflow(ws)
    workflow["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "test_contamination", "message": "changed"}],
    }
    _write_workflow(ws, workflow)
    contamination = _event("run_integrity_contaminated", CONTAMINATION_ID)
    _write_events(ws, [contamination])
    _write_registry(ws)
    return contamination


def _recovery_event(*, rerun_start_stage: str = "auditor") -> dict:
    return _event(
        "repair_completed",
        "event-repair-completed-001",
        stage_id="editor",
        decision="repair_complete",
        metadata={
            "owner_revision_schema_version": OWNER_REVISION_SCHEMA,
            "transaction_id": RECOVERY_ID,
            "repair_start_transaction_id": "repair-start-001",
            "contamination_event_id": CONTAMINATION_ID,
            "owner_stage": "editor",
            "artifact_id": "audited_brief",
            "rerun_start_stage": rerun_start_stage,
            "reference_eligible": False,
            "stale_artifact_baselines": {
                "audit_report": {"sha256": "old-audit"},
            },
        },
    )


def _bind_recovery_pointer(ws: Path, *, rerun_start_stage: str = "auditor") -> None:
    workflow = _read_workflow(ws)
    workflow["last_repair_transaction"] = {
        "transaction_id": RECOVERY_ID,
        "run_id": RUN_ID,
        "contamination_event_id": CONTAMINATION_ID,
        "owner_stage": "editor",
        "artifact_id": "audited_brief",
        "rerun_start_stage": rerun_start_stage,
    }
    _write_workflow(ws, workflow)


def _write_bound_finalize_report(ws: Path) -> None:
    _write_json(
        ws / "output/intermediate/finalize_report.json",
        {
            "status": "pass",
            "finalize_transaction_id": "render-001",
            "reader_clean": {"status": "pass"},
            "delivery_promotion": "promoted",
            "recovery_binding": {
                "status": "bound_non_reference_recovery",
                "run_id": RUN_ID,
                "contamination_event_id": CONTAMINATION_ID,
                "recovery_transaction_id": RECOVERY_ID,
                "rerun_start_stage": "auditor",
                "reference_eligible": False,
            },
        },
    )


def _evaluate(ws: Path) -> dict:
    return evaluate_recovery_state(workspace=ws, repo_workdir=ROOT)


def test_recovery_state_clean_run_is_not_applicable(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_NOT_APPLICABLE
    assert payload["reference_eligible"] is True
    assert payload["recovery_blocks_delivery"] is False


def test_recovery_state_current_contamination_awaits_recovery(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _mark_contaminated(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_AWAITING
    assert payload["contamination_event_id"] == CONTAMINATION_ID
    assert payload["recommended_recovery_action"] == "request_recovery_decision"


def test_recovery_state_requires_bound_active_repair(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage="editor")
    contamination = _mark_contaminated(ws)
    started = _event(
        "repair_started",
        "event-repair-started-001",
        stage_id="editor",
        metadata={
            "transaction_id": "repair-start-001",
            "contamination_event_id": CONTAMINATION_ID,
        },
    )
    _write_events(ws, [contamination, started])
    workflow = _read_workflow(ws)
    workflow["active_repair"] = {
        "schema_version": "mabw.active_repair.v2",
        "run_id": RUN_ID,
        "repair_start_transaction_id": "repair-start-001",
        "repair_started_event_id": "event-repair-started-001",
        "contamination_event_id": CONTAMINATION_ID,
        "repair_owner": "editor",
        "must_rerun_from": "auditor",
        "source": {"artifact_id": "audited_brief"},
    }
    _write_workflow(ws, workflow)

    payload = _evaluate(ws)
    assert payload["status"] == RECOVERY_IN_PROGRESS

    workflow["active_repair"].pop("contamination_event_id")
    _write_workflow(ws, workflow)
    invalid = _evaluate(ws)
    assert invalid["status"] == RECOVERY_INVALID
    assert invalid["reason_code"] == "active_repair_binding_invalid"


def test_recovery_state_tracks_downstream_rerun_from_event(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, recovery])
    _bind_recovery_pointer(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_RERUN_PENDING
    assert payload["rerun_start_stage"] == "auditor"
    assert payload["stale_artifact_baselines"]["audit_report"]["sha256"] == "old-audit"


def test_recovery_state_requires_current_finalize_render_and_completion(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage="finalize")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, recovery])
    _bind_recovery_pointer(ws)

    missing = _evaluate(ws)
    assert missing["status"] == RECOVERY_FINALIZE_RENDER_REQUIRED

    _write_bound_finalize_report(ws)
    current = _evaluate(ws)
    assert current["status"] == RECOVERY_FINALIZE_COMPLETION_PENDING
    assert current["render_transaction_id"] == "render-001"


def test_recovery_state_validates_terminal_finalize_binding(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage=None)
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    completion = _event(
        "decision_recorded",
        "event-finalize-complete-001",
        stage_id="finalize",
        decision="finalize",
        metadata={
            "transaction_id": "finalize-complete-001",
            "render_transaction_id": "render-001",
            "recovery_transaction_id": RECOVERY_ID,
            "contamination_event_id": CONTAMINATION_ID,
        },
    )
    _write_events(ws, [contamination, recovery, completion])
    _bind_recovery_pointer(ws)
    workflow = _read_workflow(ws)
    workflow["last_completion_transaction"] = {
        "transaction_id": "finalize-complete-001",
        "run_id": RUN_ID,
        "stage_id": "finalize",
        "decision": "finalize",
        "render_transaction_id": "render-001",
        "recovery_transaction_id": RECOVERY_ID,
        "contamination_event_id": CONTAMINATION_ID,
    }
    _write_workflow(ws, workflow)
    _write_bound_finalize_report(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_COMPLETED_NON_REFERENCE
    assert payload["recovery_blocks_delivery"] is False
    assert payload["reference_eligible"] is False


def test_recovery_state_rejects_duplicate_event_ids(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    contamination = _mark_contaminated(ws)
    duplicate = dict(contamination)
    duplicate["event_type"] = "repair_completed"
    _write_events(ws, [contamination, duplicate])

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "control_context_invalid"
    assert "Duplicate event_id" in payload["reason"]


def test_recovery_state_rejects_unbound_legacy_repaired_status(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    workflow = _read_workflow(ws)
    workflow["run_integrity"] = {
        "status": "contaminated_repaired",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [],
    }
    _write_workflow(ws, workflow)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "legacy_recovery_unbound"


@pytest.mark.parametrize(
    "case_id",
    [
        "persisted-clean-contamination-wins",
        "empty-event-id-invalid",
        "recovery-before-latest-contamination-ignored",
        "recovery-bound-to-older-contamination-invalid",
        "old-run-events-ignored",
        "legacy-unversioned-clean-repair-ignored",
        "legacy-unversioned-stale-metadata-migrated",
        "versioned-owner-revision-missing-binding-invalid",
        "second-recovery-latest-wins",
        "new-contamination-opens-cycle",
    ],
    ids=lambda value: value,
)
def test_recovery_event_timeline_matrix(tmp_path: Path, case_id: str) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    _write_registry(ws)
    workflow = _read_workflow(ws)
    expected_status = RECOVERY_AWAITING
    expected_reason = ""
    expected_contamination = CONTAMINATION_ID
    expected_recovery = ""

    if case_id == "persisted-clean-contamination-wins":
        events = [_event("run_integrity_contaminated", CONTAMINATION_ID)]
    elif case_id == "empty-event-id-invalid":
        events = [_event("run_integrity_contaminated", "")]
        expected_status = RECOVERY_INVALID
        expected_reason = "control_context_invalid"
        expected_contamination = ""
    elif case_id == "old-run-events-ignored":
        old = _event("run_integrity_contaminated", "old-contamination")
        old["run_id"] = "old-run"
        old_recovery = _recovery_event()
        old_recovery["event_id"] = "old-recovery"
        old_recovery["run_id"] = "old-run"
        events = [old, old_recovery]
        expected_status = RECOVERY_NOT_APPLICABLE
        expected_contamination = ""
    elif case_id == "legacy-unversioned-clean-repair-ignored":
        events = [
            _event(
                "repair_completed",
                "legacy-repair-event",
                stage_id="editor",
                decision="repair_complete",
                metadata={
                    "transaction_id": "legacy-repair-transaction",
                    "repair_owner": "editor",
                    "must_rerun_from": "auditor",
                    "next_stage": "auditor",
                    "allowed_artifacts": ["output/intermediate/audited_brief.md"],
                },
            )
        ]
        expected_status = RECOVERY_NOT_APPLICABLE
        expected_contamination = ""
    elif case_id == "legacy-unversioned-stale-metadata-migrated":
        events = [
            _event(
                "repair_completed",
                "legacy-repair-event",
                stage_id="editor",
                decision="repair_complete",
                metadata={
                    "transaction_id": "legacy-repair-transaction",
                    "repair_owner": "editor",
                    "must_rerun_from": "auditor",
                    "next_stage": "auditor",
                },
            )
        ]
        workflow["last_repair_transaction"] = {
            "transaction_id": "legacy-repair-transaction",
            "stage_id": "editor",
            "decision": "repair_complete",
        }
        workflow["stage_statuses"] = {
            "auditor": {
                "status": "ready",
                "metadata": {
                    "stale_after_repair": True,
                    "repair_transaction_id": "legacy-repair-transaction",
                    "repair_owner": "editor",
                    "stale_artifact_baselines": {
                        "audit_report": {"sha256": "legacy-audit-sha"},
                    },
                },
            }
        }
        expected_status = RECOVERY_NOT_APPLICABLE
        expected_contamination = ""
    elif case_id == "versioned-owner-revision-missing-binding-invalid":
        events = [
            _event(
                "repair_completed",
                "invalid-current-repair-event",
                stage_id="editor",
                decision="repair_complete",
                metadata={
                    "owner_revision_schema_version": OWNER_REVISION_SCHEMA,
                    "transaction_id": "current-repair-transaction",
                },
            )
        ]
        expected_status = RECOVERY_INVALID
        expected_reason = "owner_revision_binding_invalid"
        expected_contamination = ""
    else:
        workflow["run_integrity"] = {
            "status": "contaminated",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [{"reason_code": "test_contamination"}],
        }
        first = _event("run_integrity_contaminated", "contamination-old")
        first_recovery = _recovery_event()
        first_recovery["metadata"]["contamination_event_id"] = "contamination-old"
        second = _event("run_integrity_contaminated", CONTAMINATION_ID)
        if case_id == "recovery-before-latest-contamination-ignored":
            events = [first, first_recovery, second]
        elif case_id == "recovery-bound-to-older-contamination-invalid":
            events = [first, second, first_recovery]
            expected_status = RECOVERY_INVALID
            expected_reason = "recovery_event_binding_invalid"
            expected_contamination = ""
        elif case_id == "new-contamination-opens-cycle":
            events = [first, first_recovery, second]
        else:
            first["event_id"] = CONTAMINATION_ID
            first_recovery["metadata"]["contamination_event_id"] = CONTAMINATION_ID
            second_recovery = _recovery_event()
            second_recovery["event_id"] = "event-repair-completed-002"
            second_recovery["metadata"]["transaction_id"] = "repair-complete-002"
            events = [first, first_recovery, second_recovery]
            workflow["last_repair_transaction"] = {
                "transaction_id": "repair-complete-002",
                "run_id": RUN_ID,
                "contamination_event_id": CONTAMINATION_ID,
                "owner_stage": "editor",
                "artifact_id": "audited_brief",
                "rerun_start_stage": "auditor",
            }
            expected_status = RECOVERY_RERUN_PENDING
            expected_recovery = "repair-complete-002"

    _write_workflow(ws, workflow)
    _write_events(ws, events)

    payload = _evaluate(ws)

    assert payload["status"] == expected_status
    if expected_reason:
        assert payload["reason_code"] == expected_reason
    if expected_contamination:
        assert payload["contamination_event_id"] == expected_contamination
    if expected_recovery:
        assert payload["recovery_transaction_id"] == expected_recovery
    if case_id == "legacy-unversioned-clean-repair-ignored":
        assert payload["owner_revision"]["status"] == "none"
    if case_id == "legacy-unversioned-stale-metadata-migrated":
        assert payload["owner_revision"]["status"] == "legacy_migrated"
        assert payload["owner_revision"]["stale_artifact_baselines"] == {
            "audit_report": {"sha256": "legacy-audit-sha"},
        }
        assert recovery_stale_artifact_baselines(payload) == {
            "audit_report": {"sha256": "legacy-audit-sha"},
        }


@pytest.mark.parametrize(
    "case_id",
    [
        "workflow-run-id-mismatch",
        "missing-runtime-manifest",
        "malformed-workflow-control",
        "recovery-transaction-id-missing",
        "repair-pointer-missing",
        "repair-pointer-mismatch",
        "later-orphan-recovery-event",
        "contamination-binding-missing",
        "rerun-stage-noncanonical",
        "current-stage-precedes-rerun",
    ],
    ids=lambda value: value,
)
def test_recovery_control_and_transaction_binding_matrix(
    tmp_path: Path,
    case_id: str,
) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, recovery])
    _bind_recovery_pointer(ws)
    expected_reason = ""

    if case_id == "workflow-run-id-mismatch":
        workflow = _read_workflow(ws)
        workflow["run_id"] = "wrong-run"
        _write_workflow(ws, workflow)
        expected_reason = "workflow_run_id_mismatch"
    elif case_id == "missing-runtime-manifest":
        (ws / "output/intermediate/runtime_manifest.json").unlink()
        expected_reason = "control_context_invalid"
    elif case_id == "malformed-workflow-control":
        (ws / "output/intermediate/workflow_state.json").write_text("{broken", encoding="utf-8")
        expected_reason = "control_context_invalid"
    elif case_id == "recovery-transaction-id-missing":
        recovery["metadata"]["transaction_id"] = ""
        _write_events(ws, [contamination, recovery])
        expected_reason = "owner_revision_binding_invalid"
    elif case_id == "repair-pointer-missing":
        workflow = _read_workflow(ws)
        workflow.pop("last_repair_transaction")
        _write_workflow(ws, workflow)
        expected_reason = "repair_pointer_invalid"
    elif case_id == "repair-pointer-mismatch":
        workflow = _read_workflow(ws)
        workflow["last_repair_transaction"]["transaction_id"] = "wrong-repair"
        _write_workflow(ws, workflow)
        expected_reason = "repair_pointer_invalid"
    elif case_id == "later-orphan-recovery-event":
        orphan = _recovery_event()
        orphan["event_id"] = "event-orphan-recovery"
        orphan["metadata"]["transaction_id"] = "orphan-recovery"
        _write_events(ws, [contamination, recovery, orphan])
        expected_reason = "repair_pointer_invalid"
    elif case_id == "contamination-binding-missing":
        recovery["metadata"]["contamination_event_id"] = ""
        _write_events(ws, [contamination, recovery])
        expected_reason = "recovery_event_binding_invalid"
    elif case_id == "rerun-stage-noncanonical":
        recovery["metadata"]["rerun_start_stage"] = "unknown-stage"
        _write_events(ws, [contamination, recovery])
        expected_reason = "owner_revision_binding_invalid"
    else:
        workflow = _read_workflow(ws)
        workflow["current_stage"] = "editor"
        _write_workflow(ws, workflow)
        expected_reason = "current_stage_precedes_recovery_rerun"

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == expected_reason


def _terminal_recovery_workspace(tmp_path: Path) -> tuple[Path, dict, dict]:
    ws = _workspace(tmp_path, current_stage=None)
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    completion = _event(
        "decision_recorded",
        "event-finalize-complete-001",
        stage_id="finalize",
        decision="finalize",
        metadata={
            "transaction_id": "finalize-complete-001",
            "render_transaction_id": "render-001",
            "recovery_transaction_id": RECOVERY_ID,
            "contamination_event_id": CONTAMINATION_ID,
        },
    )
    _write_events(ws, [contamination, recovery, completion])
    _bind_recovery_pointer(ws)
    workflow = _read_workflow(ws)
    workflow["last_completion_transaction"] = {
        "transaction_id": "finalize-complete-001",
        "run_id": RUN_ID,
        "stage_id": "finalize",
        "decision": "finalize",
        "render_transaction_id": "render-001",
        "recovery_transaction_id": RECOVERY_ID,
        "contamination_event_id": CONTAMINATION_ID,
    }
    _write_workflow(ws, workflow)
    _write_bound_finalize_report(ws)
    return ws, completion, recovery


@pytest.mark.parametrize(
    "case_id",
    [
        "old-pass-report-unbound",
        "old-failed-report-unbound",
        "current-bound-report-failed",
        "completion-transaction-id-empty",
        "finalize-event-precedes-recovery",
        "finalize-bindings-disagree",
        "terminal-decision-event-missing",
    ],
    ids=lambda value: value,
)
def test_recovery_finalize_binding_matrix(tmp_path: Path, case_id: str) -> None:
    if case_id in {
        "old-pass-report-unbound",
        "old-failed-report-unbound",
        "current-bound-report-failed",
    }:
        ws = _workspace(tmp_path, current_stage="finalize")
        contamination = _mark_contaminated(ws)
        recovery = _recovery_event()
        _write_events(ws, [contamination, recovery])
        _bind_recovery_pointer(ws)
        if case_id == "current-bound-report-failed":
            _write_bound_finalize_report(ws)
            report = json.loads(
                (ws / "output/intermediate/finalize_report.json").read_text(encoding="utf-8")
            )
            report["status"] = "fail"
            _write_json(ws / "output/intermediate/finalize_report.json", report)
            expected_reason = "finalize_report_failed"
        else:
            _write_json(
                ws / "output/intermediate/finalize_report.json",
                {
                    "status": "pass" if case_id == "old-pass-report-unbound" else "fail",
                    "finalize_transaction_id": "old-render",
                    "reader_clean": {"status": "pass"},
                    "delivery_promotion": "promoted",
                },
            )
            expected_reason = "finalize_report_recovery_unbound"
        expected_status = RECOVERY_FINALIZE_RENDER_REQUIRED
    else:
        ws, completion, recovery = _terminal_recovery_workspace(tmp_path)
        expected_status = RECOVERY_INVALID
        expected_reason = "finalize_completion_binding_invalid"
        if case_id == "completion-transaction-id-empty":
            workflow = _read_workflow(ws)
            workflow["last_completion_transaction"]["transaction_id"] = ""
            _write_workflow(ws, workflow)
        elif case_id == "finalize-event-precedes-recovery":
            contamination = _event("run_integrity_contaminated", CONTAMINATION_ID)
            _write_events(ws, [contamination, completion, recovery])
        elif case_id == "finalize-bindings-disagree":
            workflow = _read_workflow(ws)
            workflow["last_completion_transaction"]["render_transaction_id"] = "wrong-render"
            _write_workflow(ws, workflow)
        else:
            contamination = _event("run_integrity_contaminated", CONTAMINATION_ID)
            _write_events(ws, [contamination, recovery])

    payload = _evaluate(ws)

    assert payload["status"] == expected_status
    assert payload["reason_code"] == expected_reason


@pytest.mark.parametrize(
    "case_id",
    [
        "terminal-contamination-requires-new-run",
        "nonterminal-recovery-blocks-valid-bundle",
        "delivery-success-invalidated-by-new-contamination",
        "registration-preserves-non-reference-posture",
    ],
    ids=lambda value: value,
)
def test_recovered_delivery_and_reference_matrix(tmp_path: Path, case_id: str) -> None:
    if case_id == "nonterminal-recovery-blocks-valid-bundle":
        ws = _workspace(tmp_path, current_stage="auditor")
        contamination = _mark_contaminated(ws)
        recovery = _recovery_event()
        _write_events(ws, [contamination, recovery])
        _bind_recovery_pointer(ws)
        state = _evaluate(ws)
        workflow = _read_workflow(ws)

        with pytest.raises(DeliverCommandError) as excinfo:
            _preflight_run_integrity_for_delivery(
                workflow["run_integrity"],
                recovery_state=state,
                target="local",
                channel="local",
            )

        assert excinfo.value.error_code == E_DELIVERY_RUN_INTEGRITY_BLOCKED
        assert state["status"] == RECOVERY_RERUN_PENDING
        return

    ws, _completion, _recovery = _terminal_recovery_workspace(tmp_path)
    terminal = _evaluate(ws)
    assert terminal["status"] == RECOVERY_COMPLETED_NON_REFERENCE

    if case_id == "registration-preserves-non-reference-posture":
        registered = _registered_run_integrity(
            {"run_integrity": _read_workflow(ws)["run_integrity"]},
            path="workflow_state.run_integrity",
        )
        assert registered["status"] == "contaminated"
        assert registered["reference_eligible"] is False
        return

    events = [
        json.loads(line)
        for line in (ws / "output/intermediate/event_log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if case_id == "delivery-success-invalidated-by-new-contamination":
        events.append(
            _event(
                "delivery_succeeded",
                "event-delivery-succeeded-001",
                metadata={
                    "render_transaction_id": "render-001",
                    "recovery_transaction_id": RECOVERY_ID,
                    "contamination_event_id": CONTAMINATION_ID,
                },
            )
        )
    events.append(
        _event(
            "run_integrity_contaminated",
            "event-contamination-terminal-002",
            stage_id="finalize",
        )
    )
    _write_events(ws, events)

    current = _evaluate(ws)
    assert current["status"] == RECOVERY_AWAITING
    assert current["recommended_recovery_action"] == "start_new_run"
    assert current["recovery_blocks_delivery"] is True
    if case_id == "delivery-success-invalidated-by-new-contamination":
        projection = build_completion_projection(workspace=ws, repo_workdir=ROOT)
        assert projection["event_truth"]["delivery_succeeded"] is False
        assert projection["event_truth"]["delivery_outcome"] == "missing"
