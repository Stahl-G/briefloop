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


SCHEMA = "briefloop.run_termination_request.v2"


def _run_id_and_revision(workspace: Path) -> tuple[str, int]:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        return head.current_run_id, store.current_revision


def _human_review(workspace: Path):
    service = _service(workspace)
    for _ in range(12):
        result = service.continue_authorized()
        if result.status == "needs_human":
            return service, result
        assert result.status == "role_work_required"
        _write_current_role_proposal(
            workspace,
            result,
            initial_editor_repetitions=20,
            repair_editor_repetitions=210,
            repair_audit_decision="fail",
        )
    raise AssertionError("run did not reach Human review")


def _request(result, run_id: str, *, request_id: str = "REQ-TERMINATE-001", **changes):
    payload = {
        "schema_version": SCHEMA,
        "request_id": request_id,
        "run_id": run_id,
        "decision": "terminate",
        "reason_code": "gate_repair_unresolvable",
        "reason": "The authorized repair budget is exhausted; preserve the failed run.",
        "actor_id": "local-human-reviewer",
        "expected_action_fingerprint": result.trace.next_action.action_fingerprint,
        "expected_store_revision": result.store_revision,
        **changes,
    }
    return RunTerminationRequest.model_validate(payload, strict=True)


def test_run_termination_is_irreversible_terminal_and_replays(tmp_path: Path) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    service, human = _human_review(workspace)
    assert (
        human.trace.next_action.action_kind,
        human.trace.next_action.effect_kind,
        human.trace.next_action.request_schema_id,
    ) == ("human_decision", "gate_repair_human_review", SCHEMA)
    run_id, _ = _run_id_and_revision(workspace)
    request = _request(human, run_id)

    committed = service.apply_current(None, request)
    assert committed.status == "committed"
    replayed = CoreRunTerminalService(workspace).record_run_termination(request)
    assert replayed.status == "replayed"

    terminal = service.continue_authorized()
    assert (
        terminal.status,
        terminal.reason_code,
        terminal.trace.next_action.action_kind,
        terminal.trace.next_action.effect_kind,
        terminal.trace.transaction_ids,
    ) == (
        "terminated",
        "gate_repair_unresolvable",
        "complete",
        "run_terminated",
        [],
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
        events = [
            item for item in snapshot.events if item.event_type == "run_terminated"
        ]
        assert len(events) == 1
        assert events[0].actor == "runtime"
        assert events[0].metadata["human_actor_id"] == "local-human-reviewer"
        assert events[0].metadata["terminated_action_fingerprint"] == (
            human.trace.next_action.action_fingerprint
        )


def test_run_termination_rejects_stale_action_without_write(tmp_path: Path) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    _service_instance, human = _human_review(workspace)
    run_id, before_revision = _run_id_and_revision(workspace)
    incompatible = CoreRunTerminalService(workspace).record_run_termination(
        _request(
            human,
            run_id,
            request_id="REQ-TERMINATE-WRONG-REASON-001",
            reason_code="negative_audit_truth_accepted",
        )
    )
    assert (incompatible.status, incompatible.error_code) == (
        "failed_uncommitted",
        "core_run_request_invalid",
    )
    assert _run_id_and_revision(workspace)[1] == before_revision

    result = CoreRunTerminalService(workspace).record_run_termination(
        _request(human, run_id, expected_action_fingerprint="0" * 64)
    )

    assert (result.status, result.error_code) == (
        "failed_uncommitted",
        "runtime_action_stale",
    )
    assert _run_id_and_revision(workspace)[1] == before_revision


def test_run_termination_rejected_outside_human_review(tmp_path: Path) -> None:
    if sys.platform == "win32":
        return
    workspace = _authorized_workspace(tmp_path)
    service = _service(workspace)
    action = service.next_action()
    run_id, revision = _run_id_and_revision(workspace)
    request = RunTerminationRequest(
        schema_version=SCHEMA,
        request_id="REQ-TERMINATE-OUTSIDE-001",
        run_id=run_id,
        decision="terminate",
        reason_code="operator_abandon",
        reason="The Human explicitly abandoned this run.",
        actor_id="local-human-reviewer",
        expected_action_fingerprint=action.action_fingerprint,
        expected_store_revision=revision,
    )

    result = CoreRunTerminalService(workspace).record_run_termination(request)
    assert (result.status, result.error_code) == (
        "failed_uncommitted",
        "stage_not_current",
    )
