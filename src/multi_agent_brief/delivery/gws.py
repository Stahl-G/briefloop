"""Gmail delivery connector using the optional gws CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from multi_agent_brief.delivery.base import DeliveryArtifact, DeliveryConnector, DeliveryResult, DeliveryTarget


class GwsGmailDeliveryConnector(DeliveryConnector):
    """Create Gmail drafts or send Gmail messages through the optional gws CLI."""

    name = "gmail"

    def deliver(self, artifact: DeliveryArtifact, target: DeliveryTarget) -> DeliveryResult:
        if target.channel not in {"draft", "send"}:
            return DeliveryResult(self.name, False, "gmail: supported channels are 'draft' and 'send'")
        if not target.recipient:
            return DeliveryResult(self.name, False, "gmail: --recipient is required")

        path = Path(artifact.path)
        if not path.exists():
            return DeliveryResult(self.name, False, f"gmail: artifact not found: {artifact.path}")

        if not shutil.which("gws"):
            return DeliveryResult(
                self.name,
                False,
                "gmail: 'gws' not found. Install googleworkspace/cli and run gws auth setup/login.",
            )

        auth_error = self._check_auth()
        if auth_error:
            return DeliveryResult(self.name, False, auth_error)

        subject = _metadata_text(target.metadata.get("subject")) or artifact.title or path.stem
        body = _metadata_text(target.metadata.get("body")) or "Please review the attached BriefLoop delivery."
        attachments = _metadata_list(target.metadata.get("attachments")) or [str(path)]
        attachment_paths = [Path(attachment).expanduser().resolve() for attachment in attachments]
        if any(not attachment.exists() for attachment in attachment_paths):
            return DeliveryResult(self.name, False, "gmail: attachment not found")
        attachment_cwd = Path(os.path.commonpath([str(attachment.parent) for attachment in attachment_paths]))

        args = [
            "gmail",
            "+send",
            "--to",
            target.recipient,
            "--subject",
            subject,
            "--body",
            body,
        ]
        if target.channel == "draft":
            args.append("--draft")
        for attachment in attachment_paths:
            args.extend(["--attach", attachment.relative_to(attachment_cwd).as_posix()])

        result = self._run_gws(args, cwd=attachment_cwd)
        if result is None:
            return DeliveryResult(self.name, False, "gmail: gws command failed or timed out")
        if result.returncode != 0:
            action = "draft creation" if target.channel == "draft" else "send"
            return DeliveryResult(
                self.name,
                False,
                f"gmail: gws {action} failed. Check gws auth, Gmail permissions, recipient, and attachment access.",
            )

        if target.channel == "draft":
            metadata = _draft_metadata(result.stdout)
            if not metadata.get("draft_id_present"):
                return DeliveryResult(
                    self.name,
                    False,
                    "gmail: gws did not confirm Gmail draft creation. "
                    "Inspect Gmail Drafts before retrying; do not retry blindly.",
                )
            return DeliveryResult(
                self.name,
                True,
                "Gmail draft created",
                metadata=metadata,
            )

        metadata = _message_metadata(result.stdout)
        if not metadata.get("sent_message_present"):
            return DeliveryResult(
                self.name,
                False,
                "gmail: gws did not confirm Gmail send. "
                "Inspect Gmail Sent Mail before retrying; do not retry blindly.",
            )
        return DeliveryResult(
            self.name,
            True,
            "Gmail message sent",
            metadata=metadata,
        )

    def _check_auth(self) -> str | None:
        result = self._run_gws(["auth", "status"], timeout=10)
        if result is None:
            return "gmail: unable to check gws auth. Run: gws auth setup; gws auth login"
        if result.returncode != 0:
            if _has_env_auth():
                return None
            return "gmail: gws is not authenticated. Run: gws auth setup; gws auth login"
        payload = _json_object_from_output(result.stdout)
        if payload is None:
            return None
        if isinstance(payload, dict) and payload.get("auth_method") == "none" and not _has_env_auth():
            return "gmail: gws is not authenticated. Run: gws auth setup; gws auth login"
        return None

    def _run_gws(
        self,
        args: list[str],
        *,
        timeout: int = 60,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        env = {**os.environ}
        try:
            return subprocess.run(
                ["gws", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None


def _metadata_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _metadata_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _has_env_auth() -> bool:
    return any(
        os.environ.get(name)
        for name in (
            "GOOGLE_WORKSPACE_CLI_TOKEN",
            "GWS_TOKEN",
        )
    )


def _json_object_from_output(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _draft_metadata(stdout: str) -> dict[str, Any]:
    payload = _json_object_from_output(stdout)
    if payload is None:
        return {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    draft_id = payload.get("id") or payload.get("draft_id") or data.get("id")
    if isinstance(draft_id, str) and draft_id.strip():
        return {"draft_id_present": True}
    return {}


def _message_metadata(stdout: str) -> dict[str, Any]:
    payload = _json_object_from_output(stdout)
    if payload is None:
        return {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    sent_ref = payload.get("message" + "_id") or payload.get("id") or message.get("id") or data.get("id")
    if isinstance(sent_ref, str) and sent_ref.strip():
        return {"sent_message_present": True}
    return {}
