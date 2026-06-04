"""MCP server source provider using the Model Context Protocol over stdio.

Communicates with MCP servers via JSON-RPC 2.0 over subprocess stdin/stdout.

Protocol lifecycle:
  1. initialize handshake
  2. notifications/initialized
  3. tools/list → discover available tools
  4. tools/call → execute a tool and collect results

Reference: https://modelcontextprotocol.io/docs/concepts/architecture
"""
from __future__ import annotations

import io
import json
import select
import shutil
import subprocess
import time
from hashlib import sha1
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_CLIENT_NAME = "multi-agent-brief-mcp"
MCP_CLIENT_VERSION = "0.1.0"

# To prevent zombie processes: timeout for MCP server communication
MCP_TIMEOUT_SECONDS = 30


class McpProvider(SourceProvider):
    """MCP server source provider.

    Configuration (in sources.yaml under ``mcp:``):

    .. code-block:: yaml

        mcp:
          enabled: true
          servers:
            - name: my-server
              command: npx
              args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
              query_template: "{keywords}"   # optional: how to pass keywords to the tool
              tool_name: "read_file"         # optional: which tool to call (default: first tool)
              tool_args: {}                  # optional: fixed arguments for the tool call
    """

    name = "mcp"
    source_type = "mcp"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []
        servers = config.get("servers", [])
        if not servers:
            errors.append("mcp: enabled but no servers configured")
            return errors
        for i, server in enumerate(servers):
            command = server.get("command", "")
            if not command:
                errors.append(f"mcp.servers[{i}] '{server.get('name', '?')}': missing 'command'")
                continue
            if not shutil.which(command.split()[0] if " " in command else command):
                errors.append(
                    f"mcp.servers[{i}] '{server.get('name', '?')}': "
                    f"command '{command}' not found in PATH"
                )
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

        servers = config.get("servers", [])
        if not servers:
            return []

        keywords = query.keywords or []
        items: list[SourceItem] = []

        for server_cfg in servers:
            try:
                result = self._collect_from_server(server_cfg, keywords)
                items.extend(result)
            except Exception:
                # Individual server failure is non-fatal
                continue

        return items

    def _collect_from_server(
        self, server_cfg: dict[str, Any], keywords: list[str]
    ) -> list[SourceItem]:
        """Connect to one MCP server and collect sources."""
        command = server_cfg.get("command", "")
        args = server_cfg.get("args", [])
        if not command:
            return []

        # Parse command string into list if needed
        if isinstance(command, str):
            cmd_parts = command.split()
        else:
            cmd_parts = [command]
        cmd_parts.extend(str(a) for a in (args or []))

        proc = subprocess.Popen(
            cmd_parts,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )

        try:
            # Step 1: Initialize
            init_response = self._jsonrpc_call(
                proc, "initialize", {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": MCP_CLIENT_NAME,
                        "version": MCP_CLIENT_VERSION,
                    },
                }
            )
            if init_response is None:
                proc.kill()
                return []

            # Step 2: notifications/initialized
            self._jsonrpc_notify(proc, "notifications/initialized")

            # Step 3: tools/list
            tools_response = self._jsonrpc_call(proc, "tools/list", {})
            if tools_response is None:
                proc.kill()
                return []

            tools = tools_response.get("tools", [])
            if not tools:
                proc.kill()
                return []

            # Step 4: Determine which tool to call
            tool_name = server_cfg.get("tool_name") or tools[0].get("name", "")
            if not tool_name:
                proc.kill()
                return []

            # Build tool arguments
            tool_args: dict[str, Any] = dict(server_cfg.get("tool_args", {}))
            query_template = server_cfg.get("query_template", "")
            if query_template and keywords:
                tool_args["query"] = " ".join(keywords)
            elif keywords and not tool_args:
                # Pass keywords as default 'query' argument
                tool_args["query"] = " ".join(keywords)

            # Step 5: tools/call
            call_response = self._jsonrpc_call(
                proc, "tools/call", {
                    "name": tool_name,
                    "arguments": tool_args,
                }
            )
            if call_response is None:
                proc.kill()
                return []

            # Parse the result content
            content_list = call_response.get("content", [])
            items: list[SourceItem] = []
            server_name = server_cfg.get("name", "mcp-server")

            for content_item in content_list:
                content_type = content_item.get("type", "text")
                text = content_item.get("text", "")
                if not text:
                    continue

                dedupe_key = f"mcp_{server_name}_{tool_name}_{sha1(text.encode()).hexdigest()[:12]}"
                items.append(
                    SourceItem(
                        source_id=f"mcp_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                        source_name=f"MCP: {server_name}/{tool_name}",
                        source_type="mcp",
                        title=f"[MCP] {server_name}: {tool_name}",
                        content=text,
                        url="",
                        published_at="",
                        retrieved_at="",
                        language="",
                        reliability="medium",
                        dedupe_key=dedupe_key,
                        metadata={
                            "backend": "mcp",
                            "server": server_name,
                            "tool": tool_name,
                            "content_type": content_type,
                            "protocol_version": MCP_PROTOCOL_VERSION,
                        },
                    )
                )

            return items

        finally:
            self._cleanup_proc(proc)

    def _jsonrpc_call(
        self, proc: subprocess.Popen, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Send a JSON-RPC request and read the response with timeout."""
        request_id = int(time.time() * 1000) % 100000
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        request_text = json.dumps(request) + "\n"

        if proc.stdin is None or proc.stdout is None:
            return None

        try:
            # Write as text string (text=True mode)
            proc.stdin.write(request_text)
            proc.stdin.flush()

            # Read response line with timeout via select.poll()
            line = self._readline_timeout(proc.stdout, MCP_TIMEOUT_SECONDS)
            if line is None:
                return None

            response = json.loads(line)

            if "error" in response:
                return None
            return response.get("result")
        except Exception:
            return None

    def _readline_timeout(
        self, stream: io.TextIOWrapper, timeout: float
    ) -> str | None:
        """Read one line from a text stream with a timeout.

        Returns the line (without newline), or None on timeout/error.
        """
        fd = stream.buffer.fileno() if hasattr(stream, "buffer") else stream.fileno()
        deadline = time.monotonic() + timeout
        chunks: list[str] = []

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            r, _, _ = select.select([fd], [], [], remaining)
            if not r:
                # Timeout
                break
            char = stream.read(1)
            if not char:
                # EOF
                break
            if char == "\n":
                return "".join(chunks)
            chunks.append(char)

        # Partial line before timeout/EOF
        if chunks:
            return "".join(chunks)
        return None

    def _jsonrpc_notify(self, proc: subprocess.Popen, method: str) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        notif_text = json.dumps(notification) + "\n"
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(notif_text)
            proc.stdin.flush()
        except Exception:
            pass

    def _cleanup_proc(self, proc: subprocess.Popen) -> None:
        """Cleanly terminate a subprocess to prevent zombies."""
        try:
            proc.stdin.close() if proc.stdin else None
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            proc.wait(timeout=5)
