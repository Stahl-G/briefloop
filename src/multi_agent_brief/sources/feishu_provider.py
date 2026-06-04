"""Feishu/Lark source provider using lark-cli.

Requires lark-cli to be installed and authenticated:
  npx @larksuite/cli@latest install
  lark-cli config init
  lark-cli auth login --recommend

Supports pulling data from:
  - Feishu Docs (via lark-cli markdown fetch)
  - Meeting Minutes (via lark-cli minutes get)
  - Multi-dimensional Base (via lark-cli base record list)
  - Spreadsheets (via lark-cli sheets values read)
  - Calendar agenda (via lark-cli calendar +agenda)
  - Approval tasks (via lark-cli approval tasks list)
"""
from __future__ import annotations

import json
import shutil
import subprocess
from hashlib import sha1
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery

FEISHU_SOURCE_TYPES = {
    "doc": "feishu_doc",
    "minutes": "feishu_minutes",
    "base": "feishu_base",
    "sheet": "feishu_sheet",
    "agenda": "feishu_agenda",
    "approval": "feishu_approval",
}


class FeishuProvider(SourceProvider):
    """Feishu/Lark source provider using lark-cli.

    Configuration (in sources.yaml):

    .. code-block:: yaml

        feishu:
          enabled: true
          docs:
            - name: "weekly-planning"
              # token value comes from feishu doc url, configure in sources.yaml
              token: "plchldr"
              type: doc              # doc | minutes | base | sheet | agenda | approval
          auth:
            check_on_start: true     # verify lark-cli auth status at init
    """

    name = "feishu"
    source_type = "feishu"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []

        # Check lark-cli is installed
        if not shutil.which("lark-cli"):
            errors.append(
                "feishu: 'lark-cli' not found in PATH. "
                "Install: npx @larksuite/cli@latest install"
            )
            return errors  # can't do more checks without the binary

        # Check auth status
        try:
            result = subprocess.run(
                ["lark-cli", "auth", "status", "--format", "json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                errors.append(
                    "feishu: not authenticated. Run: lark-cli auth login --recommend"
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            errors.append("feishu: unable to check auth status via lark-cli")

        sources = config.get("sources", config.get("docs", []))
        if not sources:
            errors.append("feishu: enabled but no sources configured")
            return errors

        for i, src in enumerate(sources):
            name = src.get("name", f"source-{i}")
            stype = src.get("type", "doc")
            if stype not in FEISHU_SOURCE_TYPES:
                errors.append(
                    f"feishu.sources[{i}] '{name}': unknown type '{stype}'. "
                    f"Supported: {', '.join(FEISHU_SOURCE_TYPES)}"
                )
            token = src.get("token", "")
            if stype in ("doc", "minutes", "base", "sheet") and not token:
                errors.append(
                    f"feishu.sources[{i}] '{name}': requires 'token' for type '{stype}'"
                )

        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

        sources = config.get("sources", config.get("docs", []))
        if not sources:
            return []

        items: list[SourceItem] = []
        for src in sources:
            try:
                result = self._collect_from_source(src)
                items.extend(result)
            except Exception:
                continue

        return items

    def _collect_from_source(self, src: dict[str, Any]) -> list[SourceItem]:
        """Collect data from one Feishu source using lark-cli."""
        stype = src.get("type", "doc")
        name = src.get("name", "feishu-source")
        token = src.get("token", "")

        handler = {
            "doc": self._fetch_doc,
            "minutes": self._fetch_minutes,
            "base": self._fetch_base,
            "sheet": self._fetch_sheet,
            "agenda": self._fetch_agenda,
            "approval": self._fetch_approval,
        }
        fetcher = handler.get(stype)
        if fetcher is None:
            return []

        return fetcher(name, token, src)

    def _run_lark_cli(self, args: list[str]) -> dict[str, Any] | list[Any] | None:
        """Run lark-cli with --format json and parse output."""
        cmd = ["lark-cli"] + args + ["--format", "json"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return None
            return json.loads(result.stdout)
        except (json.JSONDecodeError, FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def _make_item(
        self, title: str, content: str, name: str, stype: str,
        url: str = "", published_at: str = "",
    ) -> SourceItem:
        dedupe_key = f"feishu_{name}_{sha1((title + content[:200]).encode()).hexdigest()[:12]}"
        return SourceItem(
            source_id=f"feishu_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
            source_name=f"Feishu: {name}",
            source_type=FEISHU_SOURCE_TYPES.get(stype, "feishu"),
            title=title[:200],
            content=content[:5000],
            url=url,
            published_at=published_at,
            retrieved_at="",
            language="",
            reliability="high",
            dedupe_key=dedupe_key,
            metadata={
                "backend": "lark-cli",
                "feishu_type": stype,
                "source_name": name,
            },
        )

    def _fetch_doc(self, name: str, token: str, src: dict[str, Any]) -> list[SourceItem]:
        """Fetch a Feishu document as Markdown via lark-cli."""
        data = self._run_lark_cli(["markdown", "fetch", "--token", token])
        if data is None:
            return []
        if isinstance(data, dict):
            content = data.get("content", data.get("text", json.dumps(data, ensure_ascii=False)))
        elif isinstance(data, str):
            content = data
        else:
            content = json.dumps(data, ensure_ascii=False) if data else ""
        if not content:
            return []
        return [self._make_item(
            title=src.get("title", name) or name,
            content=content,
            name=name,
            stype="doc",
            url=f"https://feishu.cn/doc/{token}",
        )]

    def _fetch_minutes(self, name: str, token: str, src: dict[str, Any]) -> list[SourceItem]:
        """Fetch meeting minutes AI artifacts via lark-cli."""
        data = self._run_lark_cli(["minutes", "get", "--token", token])
        if data is None:
            return []
        if isinstance(data, dict):
            items: list[SourceItem] = []
            # Extract summary, todos, chapters
            summary = data.get("summary", "")
            if summary:
                items.append(self._make_item(
                    f"[Minutes Summary] {name}", summary, name, "minutes",
                    url=f"https://feishu.cn/minutes/{token}",
                ))
            todos = data.get("todos", [])
            if todos:
                todo_text = "\n".join(f"- {t}" for t in (todos if isinstance(todos, list) else [todos]))
                items.append(self._make_item(
                    f"[Minutes Todos] {name}", todo_text, name, "minutes",
                ))
            chapters = data.get("chapters", [])
            if chapters:
                chapter_text = "\n".join(f"- {c}" for c in (chapters if isinstance(chapters, list) else [chapters]))
                items.append(self._make_item(
                    f"[Minutes Chapters] {name}", chapter_text[:5000], name, "minutes",
                ))
            if not items and data:
                items.append(self._make_item(
                    f"[Minutes] {name}", json.dumps(data, ensure_ascii=False)[:5000],
                    name, "minutes",
                ))
            return items
        if isinstance(data, list):
            return [self._make_item(
                f"[Minutes] {name}", json.dumps(data, ensure_ascii=False)[:5000],
                name, "minutes",
            )]
        return []

    def _fetch_base(self, name: str, token: str, src: dict[str, Any]) -> list[SourceItem]:
        """Fetch records from a Base table via lark-cli."""
        table_id = src.get("table_id", "")
        if not table_id:
            return []
        data = self._run_lark_cli([
            "base", "record", "list",
            "--base-token", token,
            "--table-id", table_id,
        ])
        if data is None:
            return []
        records = data if isinstance(data, list) else data.get("items", [])
        items: list[SourceItem] = []
        for i, record in enumerate(records[:50]):  # cap at 50
            if isinstance(record, dict):
                record_str = json.dumps(record, ensure_ascii=False)
                title = record.get("name", record.get("title", record.get("id", f"Record {i+1}")))
                items.append(self._make_item(
                    title=str(title)[:200],
                    content=record_str[:5000],
                    name=f"{name}/{title}",
                    stype="base",
                ))
        return items

    def _fetch_sheet(self, name: str, token: str, src: dict[str, Any]) -> list[SourceItem]:
        """Fetch spreadsheet values via lark-cli."""
        range_str = src.get("range", "")
        args = ["sheets", "values", "read", "--spreadsheet-token", token]
        if range_str:
            args.extend(["--range", range_str])
        data = self._run_lark_cli(args)
        if data is None:
            return []
        if isinstance(data, dict):
            values = data.get("values", data.get("data", []))
        elif isinstance(data, list):
            values = data
        else:
            values = []
        if not values:
            return []
        content = json.dumps(values, ensure_ascii=False) if isinstance(values, (list, dict)) else str(values)
        return [self._make_item(
            title=f"Sheet: {name}",
            content=content[:5000],
            name=name,
            stype="sheet",
        )]

    def _fetch_agenda(self, name: str, token: str, src: dict[str, Any]) -> list[SourceItem]:
        """Fetch today's calendar agenda via lark-cli."""
        data = self._run_lark_cli(["calendar", "+agenda"])
        if data is None:
            return []
        events = data if isinstance(data, list) else data.get("items", [data])
        items: list[SourceItem] = []
        for event in events:
            if isinstance(event, dict):
                title = event.get("title", event.get("summary", "Calendar Event"))
                content = json.dumps(event, ensure_ascii=False)
                start = event.get("start", {}).get("date", "") if isinstance(event.get("start"), dict) else ""
                items.append(self._make_item(
                    title=str(title)[:200],
                    content=content[:5000],
                    name=name,
                    stype="agenda",
                    published_at=str(start),
                ))
        return items

    def _fetch_approval(self, name: str, token: str, src: dict[str, Any]) -> list[SourceItem]:
        """Fetch approval tasks via lark-cli."""
        data = self._run_lark_cli(["approval", "tasks", "list"])
        if data is None:
            return []
        tasks = data if isinstance(data, list) else data.get("items", [data])
        items: list[SourceItem] = []
        for task in tasks:
            if isinstance(task, dict):
                title = task.get("title", task.get("name", "Approval Task"))
                content = json.dumps(task, ensure_ascii=False)
                items.append(self._make_item(
                    title=str(title)[:200],
                    content=content[:5000],
                    name=name,
                    stype="approval",
                ))
        return items
