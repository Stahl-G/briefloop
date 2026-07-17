from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.contracts.v2 import (
    CoreRunInitializeRequest,
    RunHeadTransitionRecord,
    RunIdentity,
    TransactionReceipt,
    WorkspaceRunHead,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2 import CoreRunService
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.policy import transaction_type_for
from multi_agent_brief.core_run_v2.recovery import (
    CoreEffect,
    classify_effect_authorization,
    classify_recovery_legality,
)
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier

RUN_ID = "RUN-RECOVERY-PREFIX-001"


def _initialized_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request = deepcopy(CoreRunInitializeRequest.minimal_example)
    request.update(
        request_id="REQ-RECOVERY-PREFIX-INIT-001",
        workspace_id="WS-RECOVERY-PREFIX-001",
        run_id=RUN_ID,
        workspace_config_sha256=read_workspace_file(workspace, "config.yaml").sha256,
        sources_config_sha256=read_workspace_file(workspace, "sources.yaml").sha256,
    )
    result = CoreRunService(
        workspace,
        clock=lambda: datetime(2026, 7, 17, tzinfo=timezone.utc),
    ).initialize(CoreRunInitializeRequest.model_validate(request, strict=True))
    assert result.status == "committed"
    return workspace


def _record(model: type, **values: object):
    return model.model_validate(
        {"schema_version": model.schema_id, **values},
        strict=True,
    )


def test_clean_historical_prefix_has_no_recovery_authority(tmp_path: Path) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        prefix = history.snapshot_at_revision(RUN_ID, 1)

    legality = classify_recovery_legality(prefix)
    assert legality.state == "not_required"
    assert legality.ordinary_consumption_eligible is True
    assert classify_effect_authorization(
        prefix,
        CoreEffect.FINALIZE_RENDER,
    ).decision == "allow"
    assert classify_effect_authorization(
        prefix,
        CoreEffect.REPAIR_START,
    ).decision == "deny"


def test_reset_history_is_bound_to_the_exact_predecessor_prefix(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        predecessor = history.snapshot_at_revision(RUN_ID, 1)

    transaction_id = "REQ-RECOVERY-RESET-001"
    successor_run_id = "RUN-RECOVERY-PREFIX-002"
    transition = _record(
        RunHeadTransitionRecord,
        head_transition_id="HEAD-RECOVERY-RESET-001",
        workspace_id=predecessor.workspace_id,
        predecessor_run_id=RUN_ID,
        successor_run_id=successor_run_id,
        prior_workspace_revision=1,
        successor_workspace_revision=2,
        reason_code="run_reset",
        successor_disposition="non_reference",
        created_at="2026-07-17T00:00:00Z",
        transition_event_id="EVT-RECOVERY-RESET-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="a" * 64,
    )
    receipt = _record(
        TransactionReceipt,
        transaction_id=transaction_id,
        run_id=successor_run_id,
        transaction_type=transaction_type_for("run_head_transition"),
        prior_revision=1,
        committed_revision=2,
        committed_at="2026-07-17T00:00:00Z",
        projection_status="current",
        run_head_transitions=[
            {"head_transition_id": transition.head_transition_id}
        ],
    )
    post = replace(
        predecessor,
        store_revision=2,
        run=_record(
            RunIdentity,
            run_id=successor_run_id,
            workspace_id=predecessor.workspace_id,
            runtime=predecessor.run.runtime,
            created_at="2026-07-17T00:00:00Z",
        ),
        workspace_run_head=_record(
            WorkspaceRunHead,
            workspace_id=predecessor.workspace_id,
            current_run_id=successor_run_id,
            updated_at="2026-07-17T00:00:00Z",
        ),
        run_head_transitions=(transition,),
        transactions=(receipt,),
    )

    CoreRunDomainVerifier._verify_reset_history(history, post, receipt)

    forged_transitions = (
        transition.model_copy(
            update={"predecessor_run_id": "RUN-RECOVERY-MISSING-001"}
        ),
        transition.model_copy(
            update={
                "prior_workspace_revision": 0,
                "successor_workspace_revision": 1,
            }
        ),
    )
    for forged in forged_transitions:
        with pytest.raises(CoreRunError, match="reset_history_invalid"):
            CoreRunDomainVerifier._verify_reset_history(
                history,
                replace(post, run_head_transitions=(forged,)),
                receipt,
            )
