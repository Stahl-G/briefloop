"""deliver — show or send finalized reader delivery artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multi_agent_brief.delivery.base import DeliveryArtifact, DeliveryResult, DeliveryTarget
from multi_agent_brief.delivery.feishu import FeishuDeliveryConnector
from multi_agent_brief.orchestrator.runtime_state import (
    E_ACTIVE_REPAIR_OPEN,
    E_RUNTIME_STATE_NOT_INITIALIZED,
    RuntimeStateError,
    append_event,
    raise_if_active_repair_open,
    read_event_log_records_strict,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.run_integrity import (
    interpret_run_integrity,
    project_for_read,
    workflow_with_sticky_contamination_events,
)
from multi_agent_brief.outputs.reader_final_gate import (
    combine_reader_final_gate_results,
    detect_reader_residue,
    detect_reader_residue_in_docx,
)
from multi_agent_brief.product.policy_gate_adapter import (
    policy_forbidden_phrases,
    resolve_workspace_policy_gate_adapter,
)


E_DELIVERY_BUNDLE_MISSING = "E_DELIVERY_BUNDLE_MISSING"
E_DELIVERY_NOT_CLEAN = "E_DELIVERY_NOT_CLEAN"
E_DELIVERY_FAILED = "E_DELIVERY_FAILED"
E_DELIVERY_EVENT_FAILED = "E_DELIVERY_EVENT_FAILED"
E_DELIVERY_RUNTIME_MISSING = "E_DELIVERY_RUNTIME_MISSING"
E_DELIVERY_TARGET_INVALID = "E_DELIVERY_TARGET_INVALID"
E_DELIVERY_ARTIFACT_MISMATCH = "E_DELIVERY_ARTIFACT_MISMATCH"
E_DELIVERY_RUN_INTEGRITY_BLOCKED = "E_DELIVERY_RUN_INTEGRITY_BLOCKED"

_RECIPIENT_ID_RE = re.compile(r"\b(?:oc|ou|on|om|cli|fld|f)[A-Za-z0-9_-]{8,}\b")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_-]{23,}\b")


@dataclass(frozen=True)
class DeliveryBundle:
    workspace: Path
    artifacts: list[Path]
    markdown: Path | None
    docx: Path | None
    artifact_sha256: dict[str, str]

    def relative_artifacts(self) -> list[str]:
        return [_workspace_relative(self.workspace, path) for path in self.artifacts]


class DeliverCommandError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        target: str = "",
        channel: str = "",
        delivered: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.target = target
        self.channel = channel
        self.delivered = delivered
        self.extra = extra or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "error_code": self.error_code,
            "target": self.target,
            "channel": self.channel,
            "delivered": self.delivered,
            "message": str(self),
        }
        payload.update(self.extra)
        return payload


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "deliver",
        help="Show or send finalized reader delivery artifacts.",
    )
    parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    parser.add_argument(
        "--target",
        choices=("local", "feishu"),
        default="local",
        help="Delivery target. Defaults to local.",
    )
    parser.add_argument(
        "--channel",
        choices=("doc", "drive", "chat"),
        help="Feishu channel when --target feishu.",
    )
    parser.add_argument(
        "--recipient",
        help="Feishu folder token or chat id. Event metadata stores only recipient_present and recipient_sha256.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    target = getattr(args, "target", "local") or "local"
    channel = getattr(args, "channel", None) or ("local" if target == "local" else "")
    try:
        payload = deliver_workspace(
            workspace=args.workspace,
            target=target,
            channel=channel,
            recipient=getattr(args, "recipient", None) or "",
        )
    except DeliverCommandError as exc:
        payload = exc.to_payload()
        payload["target"] = payload.get("target") or target
        payload["channel"] = payload.get("channel") or channel
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"[deliver] Error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    elif target == "local":
        print("[deliver] Delivery bundle ready:")
        for artifact in payload["delivery_artifacts"]:
            print(f"  - {artifact}")
        print("[deliver] Internal audit/control records remain under output/intermediate/.")
        _print_run_integrity_warning(payload)
    else:
        suffix = f" {payload['channel']}" if payload.get("channel") else ""
        url = payload.get("url") or ""
        if url:
            print(f"[deliver] Delivered to {payload['target']}{suffix}: {url}")
        else:
            print(f"[deliver] Delivered to {payload['target']}{suffix}: {payload['artifact']}")
        if payload.get("event_recorded") is False:
            print(
                "[deliver] Warning: delivery succeeded but delivery_succeeded event was not recorded; do not retry blindly.",
                file=sys.stderr,
            )
        _print_run_integrity_warning(payload)
    return 0


def deliver_workspace(
    *,
    workspace: str | Path,
    target: str = "local",
    channel: str = "",
    recipient: str = "",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    bundle = _load_delivery_bundle(ws)
    _verify_current_delivery_artifacts(bundle)
    _preflight_delivery_event_surface(ws)
    _preflight_no_active_repair(ws, target=target, channel=channel)
    run_integrity = _delivery_run_integrity(ws)
    _preflight_run_integrity_for_delivery(
        run_integrity,
        target=target,
        channel=channel,
    )

    if target == "local":
        artifact = bundle.markdown or bundle.artifacts[0]
        _record_delivery_event(
            ws,
            event_type="delivery_attempted",
            target="local",
            channel="local",
            artifact=artifact,
            recipient=recipient,
        )
        _record_delivery_event(
            ws,
            event_type="delivery_succeeded",
            target="local",
            channel="local",
            artifact=artifact,
            recipient=recipient,
        )
        return {
            "ok": True,
            "target": "local",
            "channel": "local",
            "artifact": _workspace_relative(ws, artifact),
            "delivery_artifacts": bundle.relative_artifacts(),
            "delivered": False,
            "message": "Delivery bundle ready",
            "run_integrity": run_integrity,
        }

    if target != "feishu":
        raise DeliverCommandError(
            f"Unsupported delivery target: {target}",
            error_code=E_DELIVERY_TARGET_INVALID,
            target=target,
            channel=channel,
        )
    if channel not in {"doc", "drive", "chat"}:
        raise DeliverCommandError(
            "Feishu delivery requires --channel doc|drive|chat.",
            error_code=E_DELIVERY_TARGET_INVALID,
            target=target,
            channel=channel,
        )
    if not recipient:
        raise DeliverCommandError(
            "Feishu delivery requires --recipient.",
            error_code=E_DELIVERY_TARGET_INVALID,
            target=target,
            channel=channel,
        )

    artifact = _select_feishu_artifact(bundle, channel)
    _record_delivery_event(
        ws,
        event_type="delivery_attempted",
        target=target,
        channel=channel,
        artifact=artifact,
        recipient=recipient,
    )

    result = FeishuDeliveryConnector().deliver(
        DeliveryArtifact(path=str(artifact), title=artifact.stem),
        DeliveryTarget(channel=channel, recipient=recipient),
    )
    final_event = "delivery_succeeded" if result.delivered else "delivery_failed"
    event_recorded = True
    event_error = ""
    try:
        _record_delivery_event(
            ws,
            event_type=final_event,
            target=target,
            channel=channel,
            artifact=artifact,
            recipient=recipient,
            url=str(result.metadata.get("url") or ""),
        )
    except DeliverCommandError as exc:
        event_recorded = False
        event_error = str(exc)
        if not result.delivered:
            raise
    if not result.delivered:
        raise DeliverCommandError(
            _safe_delivery_message(result, channel, recipient=recipient),
            error_code=E_DELIVERY_FAILED,
            target=target,
            channel=channel,
        )

    return {
        "ok": True,
        "target": target,
        "channel": channel,
        "artifact": _workspace_relative(ws, artifact),
        "delivered": True,
        "message": _safe_delivery_message(result, channel, recipient=recipient),
        "url": str(result.metadata.get("url") or ""),
        "event_recorded": event_recorded,
        "event_error": event_error,
        "run_integrity": run_integrity,
    }


def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not (ws / "config.yaml").exists():
        raise DeliverCommandError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            error_code=E_DELIVERY_BUNDLE_MISSING,
        )
    return ws


def _load_delivery_bundle(workspace: Path) -> DeliveryBundle:
    report_path = workspace / "output" / "intermediate" / "finalize_report.json"
    if not report_path.exists():
        raise DeliverCommandError(
            "Delivery bundle is missing. Run: multi-agent-brief finalize --config <workspace>/config.yaml",
            error_code=E_DELIVERY_BUNDLE_MISSING,
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DeliverCommandError(
            f"finalize_report.json is not valid JSON: {exc}",
            error_code=E_DELIVERY_BUNDLE_MISSING,
        ) from exc
    if not isinstance(report, dict):
        raise DeliverCommandError(
            "finalize_report.json must be an object.",
            error_code=E_DELIVERY_BUNDLE_MISSING,
        )

    reader_clean = report.get("reader_clean")
    if report.get("status") != "pass" or not isinstance(reader_clean, dict) or reader_clean.get("status") != "pass":
        raise DeliverCommandError(
            "Delivery bundle is not clean. Run finalize and resolve reader-clean findings before delivery.",
            error_code=E_DELIVERY_NOT_CLEAN,
        )
    raw_artifacts = report.get("delivery_artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise DeliverCommandError(
            "Delivery bundle is missing delivery_artifacts. Run: multi-agent-brief finalize --config <workspace>/config.yaml",
            error_code=E_DELIVERY_BUNDLE_MISSING,
        )
    raw_hashes = report.get("delivery_artifact_sha256")
    if not isinstance(raw_hashes, dict) or not raw_hashes:
        raise DeliverCommandError(
            "Delivery bundle is missing delivery_artifact_sha256. Run finalize again before delivery.",
            error_code=E_DELIVERY_BUNDLE_MISSING,
        )

    delivery_root = (workspace / "output" / "delivery").resolve()
    artifacts: list[Path] = []
    artifact_hashes: dict[str, str] = {}
    for raw in raw_artifacts:
        if not isinstance(raw, str) or not raw.strip():
            raise DeliverCommandError(
                "Delivery bundle contains an invalid artifact path.",
                error_code=E_DELIVERY_BUNDLE_MISSING,
            )
        path = Path(raw).expanduser()
        resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
        try:
            resolved.relative_to(delivery_root)
        except ValueError as exc:
            raise DeliverCommandError(
                "Delivery bundle may only contain output/delivery artifacts.",
                error_code=E_DELIVERY_BUNDLE_MISSING,
            ) from exc
        if not resolved.exists():
            raise DeliverCommandError(
                f"Delivery artifact not found: {_workspace_relative(workspace, resolved)}",
                error_code=E_DELIVERY_BUNDLE_MISSING,
            )
        expected_hash = _hash_for_delivery_artifact(
            raw_hashes,
            raw_path=raw,
            workspace=workspace,
            resolved=resolved,
        )
        rel = _workspace_relative(workspace, resolved)
        if not expected_hash:
            raise DeliverCommandError(
                f"Delivery artifact hash missing for {rel}. Run finalize again before delivery.",
                error_code=E_DELIVERY_BUNDLE_MISSING,
            )
        actual_hash = _sha256_file(resolved)
        if actual_hash != expected_hash:
            raise DeliverCommandError(
                f"Delivery artifact has changed since finalize: {rel}. Run finalize again before delivery.",
                error_code=E_DELIVERY_ARTIFACT_MISMATCH,
                extra={"artifact": rel},
            )
        artifacts.append(resolved)
        artifact_hashes[rel] = expected_hash

    markdown = next((path for path in artifacts if path.name == "brief.md"), None)
    docx = next((path for path in artifacts if path.suffix.lower() == ".docx"), None)
    return DeliveryBundle(
        workspace=workspace,
        artifacts=artifacts,
        markdown=markdown,
        docx=docx,
        artifact_sha256=artifact_hashes,
    )


def _hash_for_delivery_artifact(
    hashes: dict[str, Any],
    *,
    raw_path: str,
    workspace: Path,
    resolved: Path,
) -> str:
    rel = _workspace_relative(workspace, resolved)
    candidates = [
        raw_path,
        rel,
        resolved.as_posix(),
        str(resolved),
    ]
    for key in candidates:
        value = hashes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _verify_current_delivery_artifacts(bundle: DeliveryBundle) -> None:
    markdown_results = []
    docx_results = []
    forbidden_phrases = policy_forbidden_phrases(resolve_workspace_policy_gate_adapter(bundle.workspace))
    for artifact in bundle.artifacts:
        rel = _workspace_relative(bundle.workspace, artifact)
        suffix = artifact.suffix.lower()
        if suffix == ".md":
            markdown_results.append(
                detect_reader_residue(
                    artifact.read_text(encoding="utf-8"),
                    artifact=rel,
                    forbidden_phrases=forbidden_phrases,
                )
            )
        elif suffix == ".docx":
            docx_results.append(
                detect_reader_residue_in_docx(artifact, artifact=rel, forbidden_phrases=forbidden_phrases)
            )
        else:
            raise DeliverCommandError(
                f"Unsupported delivery artifact type: {rel}",
                error_code=E_DELIVERY_BUNDLE_MISSING,
            )
    clean = combine_reader_final_gate_results([*markdown_results, *docx_results])
    if clean.status != "pass":
            raise DeliverCommandError(
                "Current delivery artifacts fail the reader-final gate. Run finalize again before delivery.",
                error_code=E_DELIVERY_NOT_CLEAN,
                extra={"reader_clean": clean.to_report_dict()},
            )


def _select_feishu_artifact(bundle: DeliveryBundle, channel: str) -> Path:
    if channel == "drive" and bundle.docx is not None:
        return bundle.docx
    if bundle.markdown is not None:
        return bundle.markdown
    raise DeliverCommandError(
        "Delivery bundle is missing output/delivery/brief.md.",
        error_code=E_DELIVERY_BUNDLE_MISSING,
        target="feishu",
        channel=channel,
    )


def _record_delivery_event(
    workspace: Path,
    *,
    event_type: str,
    target: str,
    channel: str,
    artifact: Path,
    recipient: str = "",
    url: str = "",
) -> None:
    try:
        run_id = _delivery_run_id(workspace)
        append_event(
            workspace=workspace,
            run_id=run_id,
            event_type=event_type,
            actor="cli",
            reason=_event_reason(event_type),
            metadata={
                "target": target,
                "channel": channel,
                "artifact": _workspace_relative(workspace, artifact),
                "recipient_present": bool(recipient),
                "recipient_sha256": _sha256(recipient) if recipient else "",
                "url": url,
            },
        )
    except (RuntimeStateError, OSError, json.JSONDecodeError) as exc:
        raise DeliverCommandError(
            f"Unable to record delivery event: {exc}",
            error_code=E_DELIVERY_EVENT_FAILED,
            target=target,
            channel=channel,
        ) from exc


def _preflight_delivery_event_surface(workspace: Path) -> None:
    try:
        _delivery_run_id(workspace)
        paths = runtime_state_paths(workspace)
        event_log = paths["event_log"]
        if event_log.exists():
            raw = event_log.read_bytes()
            if raw and not raw.endswith(b"\n"):
                raise RuntimeStateError("event_log.jsonl is not newline-terminated.")
            for lineno, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise RuntimeStateError(f"event_log.jsonl line {lineno} is not an object.")
    except DeliverCommandError:
        raise
    except (RuntimeStateError, OSError, json.JSONDecodeError) as exc:
        raise DeliverCommandError(
            f"Delivery event surface is not writable/readable: {exc}",
            error_code=E_DELIVERY_EVENT_FAILED,
        ) from exc


def _preflight_no_active_repair(workspace: Path, *, target: str, channel: str) -> None:
    paths = runtime_state_paths(workspace)
    try:
        workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
        if not isinstance(workflow, dict):
            raise RuntimeStateError("workflow_state.json must contain an object.")
        raise_if_active_repair_open(workspace=workspace, workflow=workflow)
    except RuntimeStateError as exc:
        if exc.error_code == E_ACTIVE_REPAIR_OPEN:
            raise DeliverCommandError(
                str(exc),
                error_code=E_ACTIVE_REPAIR_OPEN,
                target=target,
                channel=channel,
                extra={
                    "runtime_error_code": E_ACTIVE_REPAIR_OPEN,
                    "details": exc.details,
                },
            ) from exc
        raise DeliverCommandError(
            f"Unable to verify active repair state before delivery: {exc}",
            error_code=E_DELIVERY_EVENT_FAILED,
            target=target,
            channel=channel,
            extra={"runtime_error_code": exc.error_code or E_DELIVERY_EVENT_FAILED},
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliverCommandError(
            f"workflow_state.json is unreadable before delivery: {exc}",
            error_code=E_DELIVERY_EVENT_FAILED,
            target=target,
            channel=channel,
        ) from exc


def _delivery_run_id(workspace: Path) -> str:
    paths = runtime_state_paths(workspace)
    if not paths["runtime_manifest"].exists() or not paths["workflow_state"].exists():
        raise DeliverCommandError(
            "Runtime state is not initialized; deliver will not create a new run trace. Run `multi-agent-brief run --workspace <workspace>` first.",
            error_code=E_DELIVERY_RUNTIME_MISSING,
            extra={"runtime_error_code": E_RUNTIME_STATE_NOT_INITIALIZED},
        )
    manifest = json.loads(paths["runtime_manifest"].read_text(encoding="utf-8"))
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise RuntimeStateError("runtime_manifest.json is missing run_id.")
    return run_id


def _delivery_run_integrity(workspace: Path) -> dict[str, Any]:
    paths = runtime_state_paths(workspace)
    if not paths["workflow_state"].exists():
        return project_for_read(
            interpret_run_integrity(
                None,
                field_present=True,
                unavailable_reason={
                    "reason_code": "workflow_state_missing",
                    "message": "workflow_state.json is missing.",
                },
            )
        )
    try:
        workflow = json.loads(paths["workflow_state"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliverCommandError(
            f"workflow_state.json is unreadable; cannot verify run integrity: {exc}",
            error_code=E_DELIVERY_EVENT_FAILED,
        ) from exc
    if not isinstance(workflow, dict):
        raise DeliverCommandError(
            "workflow_state.json must contain an object; cannot verify run integrity.",
            error_code=E_DELIVERY_EVENT_FAILED,
        )
    try:
        workflow = workflow_with_sticky_contamination_events(
            workflow,
            read_event_log_records_strict(paths["event_log"]),
        )
    except RuntimeStateError:
        pass
    return project_for_read(
        interpret_run_integrity(
            workflow.get("run_integrity"),
            field_present="run_integrity" in workflow,
        )
    )


def _preflight_run_integrity_for_delivery(
    run_integrity: dict[str, Any],
    *,
    target: str,
    channel: str,
) -> None:
    if run_integrity.get("status") == "clean" and run_integrity.get("reference_eligible") is True:
        return
    reasons = run_integrity.get("reasons") if isinstance(run_integrity.get("reasons"), list) else []
    first_reason = reasons[0] if reasons and isinstance(reasons[0], dict) else {}
    message = first_reason.get("message") or first_reason.get("reason_code") or "run integrity is not clean"
    raise DeliverCommandError(
        "Delivery blocked because run integrity is not clean. "
        f"Run `multi-agent-brief status --workspace <workspace>` and resolve: {message}",
        error_code=E_DELIVERY_RUN_INTEGRITY_BLOCKED,
        target=target,
        channel=channel,
        extra={"run_integrity": run_integrity},
    )


def _print_run_integrity_warning(payload: dict[str, Any]) -> None:
    integrity = payload.get("run_integrity")
    if not isinstance(integrity, dict) or integrity.get("reference_eligible") is not False:
        return
    print(f"[deliver] Run integrity: {integrity.get('status') or 'unknown'}", file=sys.stderr)
    print("[deliver] Reference eligible: no", file=sys.stderr)
    reasons = integrity.get("reasons") if isinstance(integrity.get("reasons"), list) else []
    if reasons:
        first = reasons[0] if isinstance(reasons[0], dict) else {}
        message = first.get("message") or first.get("reason_code") or "run is contaminated"
        print(f"[deliver] Reason: {message}", file=sys.stderr)


def _event_reason(event_type: str) -> str:
    return {
        "delivery_attempted": "Reader delivery attempted.",
        "delivery_succeeded": "Reader delivery succeeded.",
        "delivery_failed": "Reader delivery failed.",
    }.get(event_type, "Reader delivery event.")


def _workspace_relative(workspace: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_delivery_message(result: DeliveryResult, channel: str, *, recipient: str = "") -> str:
    if result.delivered:
        if channel == "doc":
            return "Doc created"
        if channel == "drive":
            return "File uploaded"
        if channel == "chat":
            return "Message sent"
    return _sanitize_delivery_message(result.message or "Delivery failed", recipient=recipient)


def _sanitize_delivery_message(message: str, *, recipient: str = "") -> str:
    text = str(message)
    if recipient:
        text = text.replace(recipient, "[recipient]")
    text = _RECIPIENT_ID_RE.sub("[recipient]", text)
    text = _LONG_TOKEN_RE.sub("[token]", text)
    return text
