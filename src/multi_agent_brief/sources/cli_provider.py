"""CLI scraper source provider: executes local scripts/commands and parses output.

Supports two output formats:
  - JSON: stdout parsed as a JSON array of items (each item becomes a SourceItem)
  - text: plain text output (entire stdout becomes one SourceItem per scraper)

Uses subprocess.run() with configurable timeout.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from hashlib import sha1
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery

CLI_DEFAULT_TIMEOUT = 30


class CliProvider(SourceProvider):
    """Local CLI scraper source provider.

    Configuration (in sources.yaml under ``mcp:`` — CLI shares the MCP config section):

    .. code-block:: yaml

        mcp:
          enabled: true
          scrapers:
            - name: daily-report
              command: python
              args: ["scripts/fetch_data.py", "--days", "7"]
              format: json       # "json" or "text" (default: text)
              timeout: 30        # seconds (optional)
    """

    name = "cli"
    source_type = "cli"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []
        scrapers = config.get("scrapers", [])
        if not scrapers:
            errors.append("cli: CLI source provider is enabled but no scrapers configured")
            return errors
        for i, scraper in enumerate(scrapers):
            command = scraper.get("command", "")
            if not command:
                errors.append(f"cli.scrapers[{i}] '{scraper.get('name', '?')}': missing 'command'")
                continue
            # Check if the command (or its first word) is in PATH
            cmd_path = command.split()[0] if " " in command else command
            if not shutil.which(cmd_path):
                errors.append(
                    f"cli.scrapers[{i}] '{scraper.get('name', '?')}': "
                    f"command '{cmd_path}' not found in PATH"
                )
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

        scrapers = config.get("scrapers", [])
        if not scrapers:
            return []

        keywords = query.keywords or []
        items: list[SourceItem] = []

        for scraper in scrapers:
            try:
                result = self._run_scraper(scraper, keywords)
                items.extend(result)
            except Exception:
                # Individual scraper failure is non-fatal
                continue

        return items

    def _run_scraper(
        self, scraper: dict[str, Any], keywords: list[str]
    ) -> list[SourceItem]:
        """Execute one scraper and parse its output."""
        command = scraper.get("command", "")
        args = scraper.get("args", [])
        fmt = scraper.get("format", "text")
        timeout = scraper.get("timeout", CLI_DEFAULT_TIMEOUT)
        scraper_name = scraper.get("name", "cli-scraper")

        if not command:
            return []

        # Build the command list
        if isinstance(command, str):
            cmd_parts = command.split()
        else:
            cmd_parts = [str(command)]
        # Add any extra args
        cmd_parts.extend(str(a) for a in (args or []))

        # Append keywords as additional positional args if the template says so
        if scraper.get("append_keywords") and keywords:
            cmd_parts.extend(keywords)

        try:
            result = subprocess.run(
                cmd_parts,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return []
        except FileNotFoundError:
            return []

        if result.returncode != 0:
            # Non-zero exit — provide the stderr as an error item
            error_text = (result.stderr or "").strip()[:200]
            if error_text:
                dedupe_key = f"cli_{scraper_name}_error_{sha1(error_text.encode()).hexdigest()[:12]}"
                return [
                    SourceItem(
                        source_id=f"cli_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                        source_name=f"CLI: {scraper_name}",
                        source_type="cli",
                        title=f"[CLI Error] {scraper_name} (exit {result.returncode})",
                        content=f"Command exited with code {result.returncode}: {error_text}",
                        url="",
                        published_at="",
                        retrieved_at="",
                        language="",
                        reliability="low",
                        dedupe_key=dedupe_key,
                        metadata={
                            "backend": "cli",
                            "scraper": scraper_name,
                            "exit_code": result.returncode,
                            "has_stderr": bool(error_text),
                            "error_type": "CliExecutionError",  # caught by registry._is_error_or_placeholder
                        },
                    )
                ]
            return []

        stdout = (result.stdout or "").strip()
        if not stdout:
            return []

        if fmt == "json":
            return self._parse_json_output(stdout, scraper_name)
        else:
            return self._parse_text_output(stdout, scraper_name)

    def _parse_json_output(self, stdout: str, scraper_name: str) -> list[SourceItem]:
        """Parse JSON output — expects a list of dicts with at least 'title' and 'content'."""
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Fallback: treat as text
            return self._parse_text_output(stdout, scraper_name)

        if isinstance(data, dict):
            data = [data]

        if not isinstance(data, list):
            return self._parse_text_output(stdout, scraper_name)

        items: list[SourceItem] = []
        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", entry.get("name", f"Item {i+1}")))[:200]
            content = str(entry.get("content", entry.get("body", entry.get("text", ""))))[:5000]
            url = str(entry.get("url", ""))
            published_at = str(entry.get("published_at", entry.get("date", entry.get("publishedAt", ""))))
            dedupe_key = f"cli_{scraper_name}_{i}_{sha1((title + content).encode()).hexdigest()[:12]}"

            items.append(
                SourceItem(
                    source_id=f"cli_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                    source_name=f"CLI: {scraper_name}",
                    source_type="cli",
                    title=title,
                    content=content,
                    url=url,
                    published_at=published_at,
                    retrieved_at="",
                    language="",
                    reliability="medium",
                    dedupe_key=dedupe_key,
                    metadata={
                        "backend": "cli",
                        "scraper": scraper_name,
                        "index": i,
                        "output_format": "json",
                    },
                )
            )
        return items

    def _parse_text_output(self, stdout: str, scraper_name: str) -> list[SourceItem]:
        """Parse plain text output — each line or paragraph as an item, or everything as one item."""
        lines = [line.strip() for line in stdout.split("\n") if line.strip()]

        if not lines:
            return []

        # If many short lines, treat each as an item; otherwise treat entire output as one item
        if len(lines) >= 3 and all(len(line) < 500 for line in lines):
            items: list[SourceItem] = []
            for i, line in enumerate(lines):
                dedupe_key = f"cli_{scraper_name}_text_{i}"
                items.append(
                    SourceItem(
                        source_id=f"cli_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                        source_name=f"CLI: {scraper_name}",
                        source_type="cli",
                        title=f"{scraper_name} - line {i+1}",
                        content=line[:2000],
                        url="",
                        published_at="",
                        retrieved_at="",
                        language="",
                        reliability="medium",
                        dedupe_key=dedupe_key,
                        metadata={
                            "backend": "cli",
                            "scraper": scraper_name,
                            "line": i,
                            "output_format": "text",
                        },
                    )
                )
            return items

        # Single block of text
        dedupe_key = f"cli_{scraper_name}_text_block"
        return [
            SourceItem(
                source_id=f"cli_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                source_name=f"CLI: {scraper_name}",
                source_type="cli",
                title=f"[CLI] {scraper_name}",
                content=stdout[:5000],
                url="",
                published_at="",
                retrieved_at="",
                language="",
                reliability="medium",
                dedupe_key=dedupe_key,
                metadata={
                    "backend": "cli",
                    "scraper": scraper_name,
                    "output_format": "text",
                },
            )
        ]
