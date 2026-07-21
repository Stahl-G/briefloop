"""deliver — show or send finalized reader delivery artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multi_agent_brief.delivery.base import DeliveryArtifact, DeliveryResult, DeliveryTarget
from multi_agent_brief.delivery.artifact_policy import (
    reader_delivery_artifact_kind,
    reader_delivery_artifact_policy_text,
)
from multi_agent_brief.delivery.feishu import FeishuDeliveryConnector
from multi_agent_brief.delivery.gws import GwsGmailDeliveryConnector
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
E_DELIVERY_TARGET_INVALID = "E_DELIVERY_TARGET_INVALID"
E_DELIVERY_ARTIFACT_MISMATCH = "E_DELIVERY_ARTIFACT_MISMATCH"

_RECIPIENT_ID_RE = re.compile(r"\b(?:oc|ou|on|om|cli|fld|f)[A-Za-z0-9_-]{8,}\b")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_-]{23,}\b")


@dataclass(frozen=True)
class DeliveryBundle:
    workspace: Path
    artifacts: list[Path]
    markdown: Path | None
    docx: Path | None
    artifact_sha256: dict[str, str]
    render_transaction_id: str

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
        choices=("local", "feishu", "gmail"),
        default="local",
        help="Delivery target. Defaults to local.",
    )
    parser.add_argument(
        "--channel",
        choices=("doc", "drive", "chat", "draft", "send"),
        help="Delivery channel. Feishu: doc|drive|chat. Gmail: draft|send.",
    )
    parser.add_argument(
        "--recipient",
        help="Feishu folder token/chat id or Gmail recipient. Event metadata stores only recipient_present and recipient_sha256.",
    )
    parser.add_argument("--subject", help="Gmail subject. Defaults to the delivery title.")
    parser.add_argument("--body", help="Gmail body. Defaults to a short delivery note and brief excerpt.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    """Fail-closed stub for the retired public `deliver` CLI surface.

    The parser registration is retained so the authority guard can return
    the typed rejection for workspace invocations; any no-workspace bypass
    lands here instead of executing legacy code. The connector execution
    layer below (deliver_workspace and helpers) is retained as the tested
    direct seam; approval and delivery run through typed Store actions.
    """

    print("runtime_command_unsupported")
    return 1


def deliver_workspace(
    *,
    workspace: str | Path,
    target: str = "local",
    channel: str = "",
    recipient: str = "",
    subject: str = "",
    body: str = "",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    bundle = _load_delivery_bundle(ws)
    _verify_current_delivery_artifacts(bundle)

    if target == "local":
        artifact = bundle.markdown or bundle.artifacts[0]
        return {
            "ok": True,
            "target": "local",
            "channel": "local",
            "artifact": _workspace_relative(ws, artifact),
            "delivery_artifacts": bundle.relative_artifacts(),
            "delivered": False,
            "message": "Delivery bundle ready",
        }

    if target == "gmail":
        return _deliver_gmail(
            ws,
            bundle=bundle,
            channel=channel,
            recipient=recipient,
            subject=subject,
            body=body,
        )

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
    result = FeishuDeliveryConnector().deliver(
        DeliveryArtifact(path=str(artifact), title=artifact.stem),
        DeliveryTarget(channel=channel, recipient=recipient),
    )
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
    }


def _deliver_gmail(
    workspace: Path,
    *,
    bundle: DeliveryBundle,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    if channel not in {"draft", "send"}:
        raise DeliverCommandError(
            "Gmail delivery requires --channel draft|send.",
            error_code=E_DELIVERY_TARGET_INVALID,
            target="gmail",
            channel=channel,
        )
    if not recipient:
        raise DeliverCommandError(
            "Gmail delivery requires --recipient.",
            error_code=E_DELIVERY_TARGET_INVALID,
            target="gmail",
            channel=channel,
        )

    artifact = _select_gmail_attachment(bundle)
    markdown = bundle.markdown
    gmail_subject = subject.strip() or _default_gmail_subject(bundle)
    gmail_body = body.strip() or _default_gmail_body(bundle)
    result = GwsGmailDeliveryConnector().deliver(
        DeliveryArtifact(path=str(artifact), title=artifact.stem),
        DeliveryTarget(
            channel=channel,
            recipient=recipient,
            metadata={
                "subject": gmail_subject,
                "body": gmail_body,
                "attachments": [str(artifact)],
                "markdown": str(markdown) if markdown else "",
            },
        ),
    )
    if not result.delivered:
        fallback = "Gmail draft creation failed" if channel == "draft" else "Gmail send failed"
        extra: dict[str, Any] = {}
        if result.metadata.get("outcome_unknown"):
            extra = {
                "artifact": _workspace_relative(workspace, artifact),
                "draft_created": False,
                "sent": False,
                "outcome_unknown": True,
                "inspect_target": result.metadata.get("inspect_target") or (
                    "Gmail Drafts" if channel == "draft" else "Gmail Sent Mail"
                ),
            }
        raise DeliverCommandError(
            _sanitize_delivery_message(result.message or fallback, recipient=recipient),
            error_code=E_DELIVERY_FAILED,
            target="gmail",
            channel=channel,
            extra=extra,
        )
    return {
        "ok": True,
        "target": "gmail",
        "channel": channel,
        "artifact": _workspace_relative(workspace, artifact),
        "delivered": channel == "send",
        "draft_created": channel == "draft",
        "sent": channel == "send",
        "message": "Gmail draft created" if channel == "draft" else "Gmail message sent",
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
            "Delivery bundle is missing. Run: briefloop finalize --config <workspace>/config.yaml",
            error_code=E_DELIVERY_BUNDLE_MISSING,
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise DeliverCommandError(
            f"finalize_report.json is not valid UTF-8: {exc}",
            error_code=E_DELIVERY_BUNDLE_MISSING,
        ) from exc
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
    render_transaction_id = report.get("finalize_transaction_id")
    if not isinstance(render_transaction_id, str) or not render_transaction_id.strip():
        raise DeliverCommandError(
            "Delivery bundle is missing finalize_transaction_id. Run finalize again before delivery.",
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
            "Delivery bundle is missing delivery_artifacts. Run: briefloop finalize --config <workspace>/config.yaml",
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
        if not resolved.is_file():
            raise DeliverCommandError(
                f"Delivery artifact is not a file: {_workspace_relative(workspace, resolved)}",
                error_code=E_DELIVERY_BUNDLE_MISSING,
            )
        if not reader_delivery_artifact_kind(resolved):
            raise DeliverCommandError(
                f"Unsupported delivery artifact: {_workspace_relative(workspace, resolved)}. "
                f"{reader_delivery_artifact_policy_text()}.",
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
        try:
            actual_hash = _sha256_file(resolved)
        except OSError as exc:
            raise DeliverCommandError(
                f"Delivery artifact could not be read: {rel}. Run finalize again before delivery.",
                error_code=E_DELIVERY_BUNDLE_MISSING,
            ) from exc
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
        render_transaction_id=render_transaction_id.strip(),
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


def _select_gmail_attachment(bundle: DeliveryBundle) -> Path:
    return bundle.docx or bundle.markdown or bundle.artifacts[0]


def _workspace_relative(workspace: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_gmail_subject(bundle: DeliveryBundle) -> str:
    title = ""
    if bundle.markdown is not None:
        try:
            for line in bundle.markdown.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break
        except OSError:
            title = ""
    return f"BriefLoop delivery: {title or 'Final brief'}"


def _default_gmail_body(bundle: DeliveryBundle) -> str:
    attachment = _workspace_relative(bundle.workspace, _select_gmail_attachment(bundle))
    excerpt = ""
    if bundle.markdown is not None:
        try:
            text = bundle.markdown.read_text(encoding="utf-8")
            excerpt = _markdown_excerpt(text)
        except OSError:
            excerpt = ""
    lines = [
        "Please review the attached BriefLoop delivery.",
        "",
        f"Attachment: {attachment}",
        "",
        "Brief excerpt:",
        excerpt or "(No markdown excerpt available.)",
        "",
        "Audit/control files are not attached.",
    ]
    return "\n".join(lines)


def _markdown_excerpt(text: str, *, limit: int = 1200) -> str:
    cleaned_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading_text = stripped.lstrip("#").strip().lower()
        if heading_text == "source appendix":
            break
        cleaned_lines.append(stripped)
    excerpt = "\n".join(cleaned_lines).strip()
    if len(excerpt) <= limit:
        return excerpt
    return excerpt[: limit - 3].rstrip() + "..."


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
