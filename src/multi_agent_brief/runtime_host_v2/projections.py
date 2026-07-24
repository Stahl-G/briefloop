"""Read-only projections from one verified SQLite ControlStore snapshot."""

from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
from typing import NamedTuple

from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.control_store.sqlite_store import ControlStoreHistory
from multi_agent_brief.control_store.serialization import sha256_hex
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.policy import core_role_topology_policy
from multi_agent_brief.core_run_v2.terminal import classify_terminal_legality
from multi_agent_brief.core_run_v2.verifier import (
    CoreRunDomainVerifier,
    VerifiedCoreRun,
)

from .contracts import (
    LocalPresentationResult,
    LocalReaderBrief,
    LocalRunPresentation,
    RuntimeContinuationResult,
)

from .errors import RuntimeHostError


class _PresentationContext(NamedTuple):
    root: Path
    history: ControlStoreHistory
    verified: VerifiedCoreRun
    presentation: LocalRunPresentation


def _current_run_id(history: ControlStoreHistory) -> str:
    heads = {
        (
            snapshot.workspace_run_head.workspace_id,
            snapshot.workspace_run_head.current_run_id,
        )
        for snapshot in history.snapshots
        if snapshot.workspace_run_head is not None
    }
    if len(heads) != 1:
        raise RuntimeHostError("control_store_integrity_invalid")
    workspace_id, run_id = next(iter(heads))
    if workspace_id != history.workspace_id:
        raise RuntimeHostError("control_store_integrity_invalid")
    return run_id


def _reader_brief(
    history: ControlStoreHistory,
    verified: VerifiedCoreRun,
) -> LocalReaderBrief:
    snapshot = verified.snapshot
    if len(snapshot.finalizations) != 1:
        raise RuntimeHostError("reader_brief_projection_invalid")
    finalization = snapshot.finalizations[0]
    renders = [
        item
        for item in snapshot.finalize_renders
        if item.render_id == finalization.render_id
    ]
    if len(renders) != 1:
        raise RuntimeHostError("reader_brief_projection_invalid")
    render = renders[0]
    if (
        len(render.reader_artifacts) != 1
        or render.reader_artifacts[0].artifact_id != "reader_brief"
    ):
        raise RuntimeHostError("reader_brief_projection_invalid")
    reference = render.reader_artifacts[0]
    records = [
        item for item in snapshot.artifacts if item.artifact_id == "reader_brief"
    ]
    revisions = [
        item
        for item in snapshot.artifact_revisions
        if item.artifact_id == reference.artifact_id
        and item.revision == reference.revision
    ]
    if (
        len(records) != 1
        or records[0].current_revision != reference.revision
        or len(revisions) != 1
    ):
        raise RuntimeHostError("reader_brief_projection_invalid")
    revision = revisions[0]
    try:
        markdown = history.read_artifact_revision_bytes(
            snapshot.run.run_id,
            reference.artifact_id,
            reference.revision,
        )
        markdown.decode("utf-8", errors="strict")
    except (ControlStoreError, UnicodeDecodeError) as exc:
        raise RuntimeHostError("reader_brief_projection_invalid") from exc
    if (
        sha256_hex(markdown) != revision.sha256
        or len(markdown) != revision.size_bytes
    ):
        raise RuntimeHostError("reader_brief_projection_invalid")
    return LocalReaderBrief.model_validate(
        {
            "state": "available",
            "artifact_id": "reader_brief",
            "revision": reference.revision,
            "sha256": revision.sha256,
            "markdown_utf8": markdown,
        },
        strict=True,
    )


def _local_run_presentation(
    history: ControlStoreHistory,
    verified: VerifiedCoreRun,
) -> LocalRunPresentation:
    snapshot = verified.snapshot
    action = classify_core_run_next_action(verified)
    terminal = classify_terminal_legality(snapshot)
    topology = core_role_topology_policy(verified.binding.role_topology)
    stages = snapshot.stage_states
    completed = sum(item.status in {"complete", "skipped"} for item in stages)
    exact_local_terminal = (
        action.action_kind == "complete"
        and action.effect_kind == "finalized_local"
        and action.reason_code == "local_finalization_complete"
        and terminal.terminal_state == "finalized_local"
    )
    if exact_local_terminal:
        view_state = "finalized"
        reader = _reader_brief(history, verified)
    else:
        reader = LocalReaderBrief(state="unavailable")
        if action.action_kind in {"human_decision", "blocked"}:
            view_state = "needs_attention"
        elif completed == 0:
            view_state = "setup"
        else:
            view_state = "running"
    authorization = (
        snapshot.run_execution_authorizations[0]
        if len(snapshot.run_execution_authorizations) == 1
        else None
    )
    return LocalRunPresentation.model_validate(
        {
            "schema_version": LocalRunPresentation.schema_id,
            "boundary": (
                "read_only_projection_not_gate_approval_delivery_or_runtime_authority"
            ),
            "run_id": snapshot.run.run_id,
            "store_revision": snapshot.store_revision,
            "runtime": snapshot.run.runtime,
            "execution_topology": topology.topology,
            "executor_display": topology.role_executor_route,
            "execution_topology_display": topology.topology_display,
            "context_independence": topology.context_display,
            "review_mode": topology.review_display,
            "role_stages": [
                str(item["stage_id"])
                for item in verified.stages
                if item.get("role_id") is not None
            ],
            "completion_target": (
                authorization.completion_target if authorization is not None else None
            ),
            "view_state": view_state,
            "completed_stages": completed,
            "total_stages": len(stages),
            "current_stage": action.stage_id,
            "current_role": action.role_id,
            "reason_code": action.reason_code,
            "terminal_state": terminal.terminal_state,
            "next_action": {
                "action_kind": action.action_kind,
                "effect_kind": action.effect_kind,
                "stage_id": action.stage_id,
                "role_id": action.role_id,
                "reason_code": action.reason_code,
            },
            "reader_brief": reader.model_dump(mode="python"),
            "summary": {
                "accepted_source_count": len(snapshot.sources),
                "claim_count": len(snapshot.claims),
                "finalization_count": len(snapshot.finalizations),
                "gates": [
                    {
                        "gate_id": item.gate_id,
                        "evaluation_id": item.evaluation_id,
                        "stage_id": item.stage_id,
                        "status": item.status,
                        "blocking": item.blocking,
                    }
                    for item in sorted(
                        snapshot.gate_evaluations,
                        key=lambda entry: (
                            entry.stage_id,
                            entry.gate_id,
                            entry.evaluation_id,
                        ),
                    )
                ],
                "receipt_ids": [
                    item.transaction_id for item in snapshot.transactions
                ],
            },
            "presentation": {"status": "not_requested"},
        },
        strict=True,
    )


def _load_presentation_context(workspace: str | Path) -> _PresentationContext:
    """Load and verify one immutable Store history for all presentation facts."""

    try:
        root = Path(workspace).expanduser().resolve(strict=True)
        with SQLiteControlStore.open(root / "briefloop.db") as store:
            history = store.load_history()
        run_id = _current_run_id(history)
        verified = CoreRunDomainVerifier().verify_loaded_history(history, run_id)
        presentation = _local_run_presentation(history, verified)
    except RuntimeHostError:
        raise
    except (ControlStoreError, CoreRunError, OSError, RuntimeError, ValueError) as exc:
        raise RuntimeHostError("control_store_integrity_invalid") from exc
    return _PresentationContext(root, history, verified, presentation)


def build_local_run_presentation(
    workspace: str | Path,
) -> LocalRunPresentation:
    """Build the strict local read model from one verified Store history."""

    return _load_presentation_context(workspace).presentation


def build_store_status_projection(workspace: str | Path) -> dict[str, object]:
    """Project operator state without reading any JSON control projection."""

    context = _load_presentation_context(workspace)
    root = context.root
    verified = context.verified
    local = context.presentation
    action = classify_core_run_next_action(verified)
    topology = core_role_topology_policy(verified.binding.role_topology)
    ready = [
        item.stage_id
        for item in verified.snapshot.stage_states
        if item.status == "ready"
    ]
    return {
        "schema_version": "briefloop.sqlite_status_projection.v2",
        "ok": True,
        "workspace": str(root),
        "read_only": True,
        "authority": "sqlite_control_store",
        "run_id": verified.snapshot.run.run_id,
        "runtime": verified.snapshot.run.runtime,
        "execution_topology": topology.topology,
        "executor_display": topology.role_executor_route,
        "execution_topology_display": topology.topology_display,
        "context_independence": topology.context_display,
        "review_mode": topology.review_display,
        "role_stages": topology.role_stages_display,
        "store_revision": verified.snapshot.store_revision,
        "current_stage": ready[0] if len(ready) == 1 else None,
        "stage_states": [
            item.model_dump(mode="json", exclude_unset=False)
            for item in verified.snapshot.stage_states
        ],
        "next_action": action.model_dump(mode="json", exclude_unset=False),
        "terminal_state": local.terminal_state,
        "view_state": local.view_state,
        "package_ready": local.terminal_state
        in {
            "package_ready",
            "approval_incomplete",
            "authorization_missing_or_denied",
            "attempt_pending",
            "delivery_outcome_unknown",
            "delivery_failed",
            "draft_created",
            "delivered",
        },
        "delivered": local.terminal_state == "delivered",
        "projection_source": {
            "store_revision": verified.snapshot.store_revision,
            "receipt_ids": [
                item.transaction_id for item in verified.snapshot.transactions
            ],
        },
    }


def build_runtime_continuation_result(
    verified,
    action,
    *,
    status: str,
    reason_code: str | None = None,
    envelope_path: str | None = None,
    transaction_ids: tuple[str, ...] = (),
    violations: tuple[dict[str, str], ...] = (),
    presentation: LocalPresentationResult | None = None,
) -> RuntimeContinuationResult:
    """Build one friendly result from the same verified snapshot and action."""

    stages = verified.snapshot.stage_states
    completed = sum(item.status in {"complete", "skipped"} for item in stages)
    return RuntimeContinuationResult.model_validate(
        {
            "schema_version": RuntimeContinuationResult.schema_id,
            "run_id": verified.snapshot.run.run_id,
            "store_revision": verified.snapshot.store_revision,
            "status": status,
            "reason_code": reason_code,
            "current_stage": action.stage_id,
            "current_role": action.role_id,
            "completed_stages": completed,
            "total_stages": len(stages),
            "violations": list(violations),
            "trace": {
                "next_action": action.model_dump(
                    mode="json", exclude_unset=False
                ),
                "envelope_path": envelope_path,
                "transaction_ids": list(transaction_ids),
            },
            "presentation": (
                presentation.model_dump(mode="json", exclude_unset=False)
                if presentation is not None
                else None
            ),
        },
        strict=True,
    )


def build_quality_projection_from_local_run(
    local: LocalRunPresentation,
) -> dict[str, object]:
    """Project Quality Panel facts from the strict one-history read model."""

    available = bool(
        local.view_state == "finalized"
        or local.terminal_state
        in {
            "package_ready",
            "approval_incomplete",
            "authorization_missing_or_denied",
            "attempt_pending",
            "delivery_outcome_unknown",
            "delivery_failed",
            "draft_created",
            "delivered",
        }
    )
    if not available:
        return {
            "ok": False,
            "status": "projection_not_available",
            "reason_code": "final_reader_not_available",
            "authority": "sqlite_control_store",
            "run_id": local.run_id,
            "store_revision": local.store_revision,
        }
    return {
        "ok": True,
        "schema_version": "briefloop.sqlite_quality_panel_projection.v2",
        "boundary": "projection_only_not_gate_or_delivery_authority",
        "authority": "sqlite_control_store",
        "run_id": local.run_id,
        "store_revision": local.store_revision,
        "package_ready": local.terminal_state != "finalized_local",
        "finalized_local": local.terminal_state == "finalized_local",
        "delivered": local.terminal_state == "delivered",
        "execution_topology": local.execution_topology,
        "executor_display": local.executor_display,
        "execution_topology_display": local.execution_topology_display,
        "context_independence": local.context_independence,
        "review_mode": local.review_mode,
        "role_stages": local.role_stages,
        "next_action": local.next_action.model_dump(
            mode="json", exclude_unset=False
        ),
        "projection_source": {
            "store_revision": local.store_revision,
            "receipt_ids": local.summary.receipt_ids,
        },
    }


def build_store_quality_projection(workspace: str | Path) -> dict[str, object]:
    """Return the Store-derived Quality Panel input or a typed unavailable result."""

    return build_quality_projection_from_local_run(
        _load_presentation_context(workspace).presentation
    )


def write_store_quality_projection(workspace: str | Path) -> dict[str, object]:
    """Write replaceable JSON/HTML views after deriving all facts from Store."""

    root = Path(workspace).expanduser().resolve(strict=True)
    payload = build_store_quality_projection(root)
    if not payload.get("ok"):
        return payload
    target_dir = root / "output" / "intermediate"
    target_dir.mkdir(parents=True, exist_ok=True)
    json_bytes = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    rendered = (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>BriefLoop Quality Panel</title></head><body>"
        "<h1>BriefLoop Quality Panel</h1>"
        "<p>Projection only; not Gate, approval, or delivery authority.</p>"
        f"<pre>{html.escape(json_bytes.decode('utf-8'))}</pre>"
        "</body></html>\n"
    ).encode("utf-8")
    json_path = target_dir / "quality_panel.json"
    html_path = target_dir / "quality_panel.html"
    _replace_projection(json_path, json_bytes)
    _replace_projection(html_path, rendered)
    return {
        **payload,
        "quality_panel": json_path.relative_to(root).as_posix(),
        "quality_panel_sha256": hashlib.sha256(json_bytes).hexdigest(),
        "quality_panel_html": html_path.relative_to(root).as_posix(),
        "quality_panel_html_sha256": hashlib.sha256(rendered).hexdigest(),
    }


def _replace_projection(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeHostError("projection_write_failed") from exc


__all__ = [
    "build_local_run_presentation",
    "build_quality_projection_from_local_run",
    "build_store_quality_projection",
    "build_store_status_projection",
    "build_runtime_continuation_result",
    "write_store_quality_projection",
]
