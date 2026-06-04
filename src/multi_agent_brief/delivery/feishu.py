"""Feishu/Lark delivery connector using lark-cli.

Sends briefs to Feishu via:
  - IM message (lark-cli im +messages-send)
  - Document creation (lark-cli docs +create)
  - File upload (lark-cli drive upload)

Requires lark-cli to be installed and authenticated.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from multi_agent_brief.delivery.base import DeliveryArtifact, DeliveryConnector, DeliveryResult, DeliveryTarget

# Max text length for IM messages (Feishu limit ~150k chars, conservative)
IM_MAX_LENGTH = 50000


class FeishuDeliveryConnector(DeliveryConnector):
    """Deliver brief artifacts to Feishu via lark-cli.

    Supported channels:
      - chat: send as text message to a group or user chat
      - doc: create a Feishu document with the brief content
      - drive: upload the brief file to Feishu Drive

    Target recipient format:
      - chat: chat_id (oc_xxx) or user open_id (ou_xxx)
      - doc: folder_token (optional, defaults to root)
      - drive: folder_token (optional, defaults to root)
    """

    name = "feishu"

    def deliver(self, artifact: DeliveryArtifact, target: DeliveryTarget) -> DeliveryResult:
        """Deliver a brief artifact to a Feishu target.

        Args:
            artifact: The brief file to deliver (path + title).
            target:
                - channel='chat', recipient='oc_xxx' (chat_id) or 'ou_xxx' (user_id)
                - channel='doc', recipient='folder_token' (optional)
                - channel='drive', recipient='folder_token' (optional)

        Returns:
            DeliveryResult with delivery status and message.
        """
        # Check prerequisites
        if not shutil.which("lark-cli"):
            return DeliveryResult(
                self.name, False,
                "feishu: 'lark-cli' not found. Install: npx @larksuite/cli@latest install",
            )

        path = Path(artifact.path)
        if not path.exists():
            return DeliveryResult(self.name, False, f"feishu: artifact not found: {artifact.path}")

        title = artifact.title or path.stem

        channel = target.channel or "chat"

        if channel == "chat":
            return self._deliver_chat(path, title, target)
        elif channel == "doc":
            return self._deliver_doc(path, title, target)
        elif channel == "drive":
            return self._deliver_drive(path, title, target)
        else:
            return DeliveryResult(
                self.name, False,
                f"feishu: unknown channel '{channel}'. Supported: chat, doc, drive",
            )

    def _check_auth(self) -> str | None:
        """Check lark-cli auth status. Returns error message or None if OK."""
        try:
            result = subprocess.run(
                ["lark-cli", "auth", "status", "--format", "json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return "feishu: not authenticated. Run: lark-cli auth login --recommend"
            return None
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return f"feishu: unable to check auth: {e}"

    def _run_lark_cli(self, args: list[str]) -> subprocess.CompletedProcess | None:
        """Run lark-cli and return result."""
        try:
            return subprocess.run(
                ["lark-cli"] + args,
                capture_output=True, text=True, timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def _deliver_chat(self, path: Path, title: str, target: DeliveryTarget) -> DeliveryResult:
        """Send brief content as IM message."""
        auth_err = self._check_auth()
        if auth_err:
            return DeliveryResult(self.name, False, auth_err)

        chat_id = target.recipient or target.metadata.get("chat_id", "")
        if not chat_id:
            return DeliveryResult(self.name, False, "feishu (chat): no chat_id provided")

        # Read file content (truncate for IM limits)
        try:
            content = path.read_text(encoding="utf-8")[:IM_MAX_LENGTH]
        except Exception as e:
            return DeliveryResult(self.name, False, f"feishu (chat): failed to read artifact: {e}")

        result = self._run_lark_cli([
            "im", "+messages-send",
            "--chat-id", chat_id,
            "--text", content,
        ])
        if result is None or result.returncode != 0:
            stderr = (result.stderr or "").strip() if result else "command failed"
            return DeliveryResult(self.name, False, f"feishu (chat): {stderr[:200]}")

        return DeliveryResult(self.name, True, f"Sent to chat {chat_id}")

    def _deliver_doc(self, path: Path, title: str, target: DeliveryTarget) -> DeliveryResult:
        """Create a Feishu document from brief content."""
        auth_err = self._check_auth()
        if auth_err:
            return DeliveryResult(self.name, False, auth_err)

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return DeliveryResult(self.name, False, f"feishu (doc): failed to read artifact: {e}")

        # Build doc content with title
        doc_content = f"<title>{title}</title>\n{content}"

        result = self._run_lark_cli([
            "docs", "+create",
            "--api-version", "v2",
            "--doc-format", "markdown",
            "--content", doc_content,
        ])
        if result is None or result.returncode != 0:
            stderr = (result.stderr or "").strip()[:200] if result else "command failed"
            return DeliveryResult(self.name, False, f"feishu (doc): {stderr}")

        # Try to extract the doc URL from output
        try:
            output = json.loads(result.stdout) if result.stdout else {}
            doc_url = output.get("url", output.get("data", {}).get("url", ""))
        except (json.JSONDecodeError, AttributeError):
            doc_url = ""

        msg = f"Doc created: {title}"
        if doc_url:
            msg += f" ({doc_url})"
        return DeliveryResult(self.name, True, msg, metadata={"url": doc_url})

    def _deliver_drive(self, path: Path, title: str, target: DeliveryTarget) -> DeliveryResult:
        """Upload brief file to Feishu Drive."""
        auth_err = self._check_auth()
        if auth_err:
            return DeliveryResult(self.name, False, auth_err)

        folder_token = target.recipient or ""
        args = ["drive", "upload", "--file", str(path)]
        if folder_token:
            args.extend(["--folder-token", folder_token])

        result = self._run_lark_cli(args)
        if result is None or result.returncode != 0:
            stderr = (result.stderr or "").strip()[:200] if result else "command failed"
            return DeliveryResult(self.name, False, f"feishu (drive): {stderr}")

        return DeliveryResult(self.name, True, f"Uploaded {path.name} to Drive")
