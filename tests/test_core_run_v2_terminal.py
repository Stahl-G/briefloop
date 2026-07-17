from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.contracts.v2 import (
    ArtifactRecord,
    ArtifactRevision,
    CoreRunInitializeRequest,
    FinalizationRecord,
    FinalizeRenderRecord,
    PackageArtifactBinding,
    PackageReadyRecord,
    RunArchiveArtifactBinding,
    RunArchiveRecord,
    TransactionReceipt,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.sqlite_store import ControlStoreHistory
from multi_agent_brief.control_store.serialization import (
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.core_run_v2 import CoreRunService
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.policy import (
    archive_artifact_usage,
    transaction_type_for,
)
from multi_agent_brief.core_run_v2.terminal import (
    classify_terminal_legality,
    classify_terminal_state,
)
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier

RUN_ID = "RUN-TERMINAL-PREFIX-001"


def _initialized_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request = deepcopy(CoreRunInitializeRequest.minimal_example)
    request.update(
        request_id="REQ-TERMINAL-PREFIX-INIT-001",
        workspace_id="WS-TERMINAL-PREFIX-001",
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


def test_terminal_projection_is_pure_over_one_historical_prefix(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        prefix = history.snapshot_at_revision(RUN_ID, 1)

    legality = classify_terminal_legality(prefix)
    assert legality.terminal_state == "core_active"
    assert legality.next_effects == ()
    assert classify_terminal_state(prefix).state == "core_active"


def _terminal_reconstruction_fixture(
    tmp_path: Path,
) -> tuple[ControlStoreHistory, object, TransactionReceipt]:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        original_history = store.load_history()
        original = original_history.snapshot_at_revision(RUN_ID, 1)

    timestamp = "2026-07-17T00:00:00Z"
    initialization = original.transactions[0]
    reader_revision = original.artifact_revisions[0]
    reader_reference = {
        "artifact_id": reader_revision.artifact_id,
        "revision": reader_revision.revision,
    }
    render = _record(
        FinalizeRenderRecord,
        render_id="RENDER-TERMINAL-001",
        run_id=RUN_ID,
        audit_proposal_id="PROP-TERMINAL-AUDIT-001",
        audited_brief=reader_reference,
        audit_report=reader_reference,
        reader_artifacts=[reader_reference],
        reader_clean_status="pass",
        policy_result_fingerprint="a" * 64,
        run_contract_fingerprint="b" * 64,
        created_at=timestamp,
        render_event_id="EVT-TERMINAL-RENDER-001",
        accepted_transaction_id=initialization.transaction_id,
        request_fingerprint="c" * 64,
    )
    initialization_with_render = TransactionReceipt.model_validate(
        {
            **initialization.model_dump(mode="json", exclude_unset=False),
            "finalize_renders": [{"render_id": render.render_id}],
        },
        strict=True,
    )
    historical_full = replace(
        original_history.snapshots[0],
        finalize_renders=(render,),
        transactions=(initialization_with_render,),
    )
    history = replace(original_history, snapshots=(historical_full,))
    pre = history.snapshot_at_revision(RUN_ID, 1)

    transaction_id = "REQ-TERMINAL-COMPLETE-001"
    finalization = _record(
        FinalizationRecord,
        finalization_id="FINALIZATION-TERMINAL-001",
        run_id=RUN_ID,
        render_id=render.render_id,
        finalize_transition_id="TRN-TERMINAL-FINALIZE-001",
        finalize_gate_batch_id="BATCH-TERMINAL-FINALIZE-001",
        finalize_gate_evaluation_ids=["EVAL-TERMINAL-FINALIZE-001"],
        recovery_id=None,
        integrity_revision=1,
        finalized_at=timestamp,
        finalization_event_id="EVT-TERMINAL-FINALIZE-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="d" * 64,
    )
    archive_members = [
        next(
            revision
            for revision in pre.artifact_revisions
            if revision.artifact_id == artifact.artifact_id
            and revision.revision == artifact.current_revision
        )
        for artifact in sorted(pre.artifacts, key=lambda item: item.artifact_id)
        if artifact.current_revision > 0
    ]
    archive_payload = {
        "schema_version": "briefloop.core_v2_run_archive.v1",
        "run_id": RUN_ID,
        "finalization_id": finalization.finalization_id,
        "artifacts": [
            {
                "artifact_id": item.artifact_id,
                "revision": item.revision,
                "sha256": item.sha256,
            }
            for item in archive_members
        ],
    }
    archive_bytes = canonical_json_bytes(archive_payload) + b"\n"
    archive_revision = _record(
        ArtifactRevision,
        run_id=RUN_ID,
        artifact_id="core_v2_run_archive",
        revision=1,
        path="output/intermediate/core_v2_run_archive.json",
        sha256=sha256_hex(archive_bytes),
        size_bytes=len(archive_bytes),
        frozen=True,
        producer_kind="control_tool",
        producer_id="core-v2-finalize-complete",
        created_at=timestamp,
    )
    archive = _record(
        RunArchiveRecord,
        archive_id="ARCHIVE-TERMINAL-001",
        run_id=RUN_ID,
        finalization_id=finalization.finalization_id,
        archive_artifact={
            "artifact_id": archive_revision.artifact_id,
            "revision": archive_revision.revision,
        },
        manifest_sha256=archive_revision.sha256,
        included_count=len(archive_members),
        created_at=timestamp,
        archive_event_id="EVT-TERMINAL-ARCHIVE-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="e" * 64,
    )
    package_payload = {
        "schema_version": "briefloop.core_v2_package_manifest.v1",
        "run_id": RUN_ID,
        "finalization_id": finalization.finalization_id,
        "archive": {
            "artifact_id": archive_revision.artifact_id,
            "revision": archive_revision.revision,
            "sha256": archive_revision.sha256,
        },
        "reader_artifacts": [
            {
                "artifact_id": reader_revision.artifact_id,
                "revision": reader_revision.revision,
                "sha256": reader_revision.sha256,
            }
        ],
    }
    package_bytes = canonical_json_bytes(package_payload) + b"\n"
    package_revision = _record(
        ArtifactRevision,
        run_id=RUN_ID,
        artifact_id="core_v2_package_manifest",
        revision=1,
        path="output/intermediate/core_v2_package_manifest.json",
        sha256=sha256_hex(package_bytes),
        size_bytes=len(package_bytes),
        frozen=True,
        producer_kind="control_tool",
        producer_id="core-v2-finalize-complete",
        created_at=timestamp,
    )
    package = _record(
        PackageReadyRecord,
        package_id="PACKAGE-TERMINAL-001",
        run_id=RUN_ID,
        finalization_id=finalization.finalization_id,
        archive_id=archive.archive_id,
        package_manifest_artifact={
            "artifact_id": package_revision.artifact_id,
            "revision": package_revision.revision,
        },
        package_manifest_sha256=package_revision.sha256,
        artifact_count=3,
        created_at=timestamp,
        package_event_id="EVT-TERMINAL-PACKAGE-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="f" * 64,
    )
    archive_bindings = tuple(
        _record(
            RunArchiveArtifactBinding,
            run_id=RUN_ID,
            archive_id=archive.archive_id,
            position=position,
            artifact_id=item.artifact_id,
            artifact_revision=item.revision,
            artifact_sha256=item.sha256,
            usage=archive_artifact_usage(item.artifact_id),
            accepted_transaction_id=transaction_id,
        )
        for position, item in enumerate(archive_members)
    )
    package_members = (
        (reader_revision, "reader"),
        (archive_revision, "archive"),
        (package_revision, "manifest"),
    )
    package_bindings = tuple(
        _record(
            PackageArtifactBinding,
            run_id=RUN_ID,
            package_id=package.package_id,
            position=position,
            artifact_id=item.artifact_id,
            artifact_revision=item.revision,
            artifact_sha256=item.sha256,
            usage=usage,
            accepted_transaction_id=transaction_id,
        )
        for position, (item, usage) in enumerate(package_members)
    )
    receipt = _record(
        TransactionReceipt,
        transaction_id=transaction_id,
        run_id=RUN_ID,
        transaction_type=transaction_type_for("finalize_complete"),
        prior_revision=1,
        committed_revision=2,
        committed_at=timestamp,
        projection_status="current",
        artifact_revisions=[
            {"artifact_id": archive_revision.artifact_id, "revision": 1},
            {"artifact_id": package_revision.artifact_id, "revision": 1},
        ],
        finalizations=[{"finalization_id": finalization.finalization_id}],
        run_archives=[{"archive_id": archive.archive_id}],
        run_archive_artifact_bindings=[
            {"archive_id": archive.archive_id, "position": item.position}
            for item in archive_bindings
        ],
        package_ready_records=[{"package_id": package.package_id}],
        package_artifact_bindings=[
            {"package_id": package.package_id, "position": item.position}
            for item in package_bindings
        ],
    )
    terminal_records = (
        _record(
            ArtifactRecord,
            run_id=RUN_ID,
            artifact_id=archive_revision.artifact_id,
            current_revision=1,
            status="valid",
            required=True,
            path=archive_revision.path,
            format="json",
        ),
        _record(
            ArtifactRecord,
            run_id=RUN_ID,
            artifact_id=package_revision.artifact_id,
            current_revision=1,
            status="valid",
            required=True,
            path=package_revision.path,
            format="json",
        ),
    )
    post = replace(
        pre,
        store_revision=2,
        artifacts=(*pre.artifacts, *terminal_records),
        artifact_revisions=(
            *pre.artifact_revisions,
            archive_revision,
            package_revision,
        ),
        finalizations=(finalization,),
        run_archives=(archive,),
        run_archive_artifact_bindings=archive_bindings,
        package_ready_records=(package,),
        package_artifact_bindings=package_bindings,
        transactions=(initialization_with_render, receipt),
    )
    history = replace(
        history,
        artifact_contents=MappingProxyType(
            {
                **history.artifact_contents,
                (RUN_ID, archive_revision.artifact_id, 1): archive_bytes,
                (RUN_ID, package_revision.artifact_id, 1): package_bytes,
            }
        ),
    )
    return history, post, receipt


def _forge_terminal_membership(post: object, target: str, forgery: str):
    binding_field = (
        "run_archive_artifact_bindings"
        if target == "archive"
        else "package_artifact_bindings"
    )
    bindings = getattr(post, binding_field)
    if forgery == "insertion":
        forged_bindings = (
            *bindings,
            bindings[-1].model_copy(update={"position": len(bindings)}),
        )
    elif forgery == "deletion":
        forged_bindings = bindings[:-1]
    elif forgery == "substitution":
        forged_bindings = (
            bindings[0].model_copy(
                update={"artifact_id": bindings[1].artifact_id}
            ),
            *bindings[1:],
        )
    elif forgery == "duplicate":
        forged_bindings = (*bindings, bindings[0])
    elif forgery == "reorder":
        forged_bindings = (
            bindings[0].model_copy(update={"position": 1}),
            bindings[1].model_copy(update={"position": 0}),
            *bindings[2:],
        )
    elif forgery == "stale":
        forged_bindings = (
            bindings[0].model_copy(
                update={"artifact_revision": bindings[0].artifact_revision + 1}
            ),
            *bindings[1:],
        )
    elif forgery == "cross_run":
        forged_bindings = (
            bindings[0].model_copy(update={"run_id": "RUN-TERMINAL-OTHER-001"}),
            *bindings[1:],
        )
    elif forgery == "wrong_usage":
        forged_bindings = (
            bindings[0].model_copy(
                update={"usage": "evidence" if target == "archive" else "archive"}
            ),
            *bindings[1:],
        )
    elif forgery == "member_hash":
        forged_bindings = (
            bindings[0].model_copy(update={"artifact_sha256": "0" * 64}),
            *bindings[1:],
        )
    elif forgery == "count":
        if target == "archive":
            record = post.run_archives[0]
            return replace(
                post,
                run_archives=(
                    record.model_copy(
                        update={"included_count": record.included_count + 1}
                    ),
                ),
            )
        record = post.package_ready_records[0]
        return replace(
            post,
            package_ready_records=(
                record.model_copy(
                    update={"artifact_count": record.artifact_count + 1}
                ),
            ),
        )
    elif forgery == "aggregate_hash":
        if target == "archive":
            record = post.run_archives[0]
            return replace(
                post,
                run_archives=(
                    record.model_copy(update={"manifest_sha256": "0" * 64}),
                ),
            )
        record = post.package_ready_records[0]
        return replace(
            post,
            package_ready_records=(
                record.model_copy(
                    update={"package_manifest_sha256": "0" * 64}
                ),
            ),
        )
    else:
        raise AssertionError(f"unknown forgery: {forgery}")
    return replace(post, **{binding_field: forged_bindings})


@pytest.mark.parametrize("target", ("archive", "package"))
@pytest.mark.parametrize(
    "forgery",
    (
        "insertion",
        "deletion",
        "substitution",
        "duplicate",
        "reorder",
        "stale",
        "cross_run",
        "wrong_usage",
        "count",
        "member_hash",
        "aggregate_hash",
    ),
)
def test_archive_and_package_reconstruction_rejects_parameterized_forgeries(
    tmp_path: Path,
    target: str,
    forgery: str,
) -> None:
    history, post, receipt = _terminal_reconstruction_fixture(tmp_path)
    CoreRunDomainVerifier._verify_archive_package_reconstruction(
        history,
        post,
        receipt,
    )

    with pytest.raises(CoreRunError) as error:
        CoreRunDomainVerifier._verify_archive_package_reconstruction(
            history,
            _forge_terminal_membership(post, target, forgery),
            receipt,
        )
    assert error.value.code == f"{target}_membership_invalid"
