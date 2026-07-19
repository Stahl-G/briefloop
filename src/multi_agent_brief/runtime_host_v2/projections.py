"""Read-only projections from one verified SQLite ControlStore snapshot."""

from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path

from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.policy import core_role_topology_policy
from multi_agent_brief.core_run_v2.terminal import classify_terminal_legality
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier

from .errors import RuntimeHostError


def build_store_status_projection(workspace: str | Path) -> dict[str, object]:
    """Project operator state without reading any JSON control projection."""

    try:
        root = Path(workspace).expanduser().resolve(strict=True)
        with SQLiteControlStore.open(root / "briefloop.db") as store:
            head = store.load_workspace_run_head()
            if head is None:
                raise RuntimeHostError("control_store_integrity_invalid")
            verified = CoreRunDomainVerifier().verify(store, head.current_run_id)
        action = classify_core_run_next_action(verified)
        terminal = classify_terminal_legality(verified.snapshot)
        topology = core_role_topology_policy(verified.binding.role_topology)
    except RuntimeHostError:
        raise
    except (ControlStoreError, CoreRunError, OSError, RuntimeError, ValueError) as exc:
        raise RuntimeHostError("control_store_integrity_invalid") from exc
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
        "terminal_state": terminal.terminal_state,
        "package_ready": terminal.terminal_state
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
        "delivered": terminal.terminal_state == "delivered",
        "projection_source": {
            "store_revision": verified.snapshot.store_revision,
            "receipt_ids": [
                item.transaction_id for item in verified.snapshot.transactions
            ],
        },
    }


def build_store_quality_projection(workspace: str | Path) -> dict[str, object]:
    """Return the Store-derived Quality Panel input or a typed unavailable result."""

    status = build_store_status_projection(workspace)
    if not bool(status["package_ready"]):
        return {
            "ok": False,
            "status": "projection_not_available",
            "reason_code": "package_not_ready",
            "authority": "sqlite_control_store",
            "run_id": status["run_id"],
            "store_revision": status["store_revision"],
        }
    return {
        "ok": True,
        "schema_version": "briefloop.sqlite_quality_panel_projection.v2",
        "boundary": "projection_only_not_gate_or_delivery_authority",
        "authority": "sqlite_control_store",
        "run_id": status["run_id"],
        "store_revision": status["store_revision"],
        "package_ready": status["package_ready"],
        "delivered": status["delivered"],
        "execution_topology": status["execution_topology"],
        "executor_display": status["executor_display"],
        "execution_topology_display": status["execution_topology_display"],
        "context_independence": status["context_independence"],
        "review_mode": status["review_mode"],
        "role_stages": status["role_stages"],
        "next_action": status["next_action"],
        "projection_source": status["projection_source"],
    }


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
    "build_store_quality_projection",
    "build_store_status_projection",
    "write_store_quality_projection",
]
