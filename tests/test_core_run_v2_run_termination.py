from __future__ import annotations

from pathlib import Path
import sys

from tests.test_runtime_host_continue_v2 import (
    _authorized_workspace,
    _service,
    _write_current_role_proposal,
)

from multi_agent_brief.contracts.v2 import RunTerminationRequest
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.terminal import CoreRunTerminalService

RUN_TERMINATION_SCHEMA = "briefloop.run_termination_request.v2"


def _run_id_and_revision(workspace: Path) -> tuple[str, int]:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        return head.current_run_id, store.current_revision


def test_run_termination_request_closes_unresolvable_human_review(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)

    for _ in range(12):
        result = service.continue_authorized()
        if result.status == "needs_human":
            break
        assert result.status == "role_work_required", (
            result.reason_code,
            result.trace.next_action.action_kind,
            result.trace.next_action.effect_kind,
        )
        _write_current_role_proposal(
            workspace,
            result,
            initial_editor_repetitions=20,
            repair_editor_repetitions=210,
            repair_audit_decision="fail",
        )
    else:
        raise AssertionError("run did not reach Human review")

    assert (
        result.trace.next_action.action_kind,
        result.trace.next_action.effect_kind,
        result.trace.next_action.request_schema_id,
    ) == (
        "human_decision",
        "gate_repair_human_review",
        RUN_TERMINATION_SCHEMA,
    )
    run_id, _ = _run_id_and_revision(workspace)
    committed = service.apply_current(
        None,
        RunTerminationRequest(
            schema_version=RUN_TERMINATION_SCHEMA,
            request_id="REQ-RUN-TERMINATION-001",
            run_id=run_id,
            decision="terminate",
            reason_code="gate_repair_unresolvable",
            expected_store_revision=result.store_revision,
        ),
    )
    assert committed.status == "committed"

    blocked = service.continue_authorized()
    assert (
        blocked.status,
        blocked.reason_code,
        blocked.trace.next_action.action_kind,
        blocked.trace.next_action.effect_kind,
    ) == (
        "needs_attention",
        "run_terminated",
        "blocked",
        "run_terminated",
    )

    replay = service.continue_authorized()
    assert (replay.status, replay.reason_code) == (
        "needs_attention",
        "run_terminated",
    )
    assert replay.trace.transaction_ids == []


def test_run_termination_rejected_outside_human_review(tmp_path: Path) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    run_id, revision = _run_id_and_revision(workspace)

    result = CoreRunTerminalService(workspace).record_run_termination(
        RunTerminationRequest(
            schema_version=RUN_TERMINATION_SCHEMA,
            request_id="REQ-RUN-TERMINATION-002",
            run_id=run_id,
            decision="terminate",
            reason_code="operator_abandon",
            expected_store_revision=revision,
        )
    )

    assert result.status == "failed_uncommitted"
    assert result.error_code == "stage_not_current"
