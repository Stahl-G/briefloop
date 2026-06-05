"""MinerU document parsing source provider.

Supports three modes:
  - local (CLI): mineru -p <file> -o <dir>   (requires pip install mineru[all])
  - remote agent:  MinerU lightweight API, no token, IP rate-limited
  - remote premium: MinerU premium API, Bearer token, high accuracy

Uses Python stdlib urllib.request for all HTTP calls — zero extra dependencies.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from hashlib import sha1
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery

MINERU_DEFAULT_OUTPUT_DIR = "output/mineru_output"
MINERU_CLI_TIMEOUT = 600
MINERU_AGENT_API_BASE = "https://mineru.net/api/v1/agent"
MINERU_PREMIUM_API_BASE = "https://mineru.net/api/v4"
MINERU_REMOTE_POLL_TIMEOUT = 300
MINERU_REMOTE_POLL_INTERVAL = 3


def _http_post_json(url: str, data: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    """POST JSON and return parsed response dict. Raises on HTTP errors."""
    body = json.dumps(data).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """GET and return parsed response dict."""
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_put_file(url: str, file_path: str) -> bool:
    """PUT a file to a signed URL. Returns True on success."""
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        req = urllib.request.Request(url, data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status in (200, 201)
    except Exception:
        return False


class MineruProvider(SourceProvider):
    """Document parsing provider using mineru CLI or remote API.

    Local mode (default):

    .. code-block:: yaml

        mineru:
          enabled: true
          paths:
            - name: "Q1 Report"
              path: "input/q1-report.pdf"
          backend: pipeline

    Remote agent mode (no token, IP rate-limited, ≤10MB, ≤20 pages):

    .. code-block:: yaml

        mineru:
          enabled: true
          mode: remote
          files:
            - name: "Annual Report"
              url: "https://cdn-mineru.openxlab.org.cn/demo/example.pdf"
            - name: "Local Contract"
              path: "input/contract.pdf"
          language: ch

    Remote premium mode (Bearer token, ≤200MB, ≤200 pages):

    .. code-block:: yaml

        mineru:
          enabled: true
          mode: remote
          api_type: premium
          api_token: "your_token_from_mineru_net"
          model_version: vlm
          files:
            - name: "Annual Report"
              url: "https://cdn-mineru.openxlab.org.cn/demo/example.pdf"
    """

    name = "mineru"
    source_type = "mineru"

    # ── validate ──────────────────────────────────────────────────

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []

        mode = config.get("mode", "local")
        if mode == "remote":
            return self._validate_remote(config)

        # local mode — original logic
        errors: list[str] = []
        if not shutil.which("mineru"):
            errors.append(
                "mineru: 'mineru' not found in PATH. "
                'Install: pip install "mineru[all]" '
                "Or use mode: remote with the MinerU cloud API."
            )

        paths = config.get("paths", [])
        if not paths:
            errors.append("mineru: enabled but no paths configured")
            return errors

        for i, entry in enumerate(paths):
            name = entry.get("name", f"path-{i}")
            file_path = entry.get("path", "")
            if not file_path:
                errors.append(f"mineru.paths[{i}] '{name}': missing 'path'")
                continue
            if not Path(file_path).exists():
                errors.append(f"mineru.paths[{i}] '{name}': path does not exist: {file_path}")

        return errors

    def _validate_remote(self, config: dict[str, Any]) -> list[str]:
        """Validate remote API mode config."""
        errors: list[str] = []
        api_type = config.get("api_type", "agent")

        if api_type == "premium":
            token = config.get("api_token", "") or os.environ.get("MINERU_API_TOKEN", "")
            if not token:
                errors.append(
                    "mineru (premium): api_token is required. "
                    "Set it in sources.yaml or env var MINERU_API_TOKEN. "
                    "Get a token at https://mineru.net → Personal Center → API Token."
                )

        files = config.get("files", config.get("remote_files", []))
        if not files:
            errors.append("mineru (remote): enabled but no files configured")
            return errors

        for i, entry in enumerate(files):
            name = entry.get("name", f"file-{i}")
            url = entry.get("url", "")
            path_val = entry.get("path", "")
            if not url and not path_val:
                errors.append(f"mineru.files[{i}] '{name}': need 'url' or 'path'")
            if path_val:
                p = Path(path_val)
                if not p.exists():
                    errors.append(f"mineru.files[{i}] '{name}': path does not exist: {path_val}")

        return errors

    # ── collect ───────────────────────────────────────────────────

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

        mode = config.get("mode", "local")
        if mode == "remote":
            return self._collect_remote(config)

        # local mode
        if not shutil.which("mineru"):
            return []

        paths = config.get("paths", [])
        if not paths:
            return []

        backend = config.get("backend", "pipeline")
        output_dir_base = config.get("output_dir", MINERU_DEFAULT_OUTPUT_DIR)
        items: list[SourceItem] = []
        for entry in paths:
            try:
                result = self._parse_entry_local(entry, backend, output_dir_base)
                items.extend(result)
            except Exception:
                continue
        return items

    def _collect_remote(self, config: dict[str, Any]) -> list[SourceItem]:
        """Collect via remote MinerU API."""
        files = config.get("files", config.get("remote_files", []))
        if not files:
            return []

        api_type = config.get("api_type", "agent")
        language = config.get("language", "ch")
        enable_table = config.get("enable_table", True)
        enable_formula = config.get("enable_formula", True)
        is_ocr = config.get("is_ocr", False)
        poll_timeout = int(config.get("poll_timeout", MINERU_REMOTE_POLL_TIMEOUT))
        poll_interval_val = float(config.get("poll_interval", MINERU_REMOTE_POLL_INTERVAL))

        items: list[SourceItem] = []
        for entry in files:
            try:
                if api_type == "premium":
                    result = self._parse_remote_premium(entry, config, language, poll_timeout, poll_interval_val)
                else:
                    result = self._parse_remote_agent(entry, language, enable_table, enable_formula, is_ocr, poll_timeout, poll_interval_val)
                items.extend(result)
            except Exception:
                continue
        return items

    # ── remote agent API ──────────────────────────────────────────

    def _parse_remote_agent(
        self, entry: dict[str, Any],
        language: str, enable_table: bool, enable_formula: bool, is_ocr: bool,
        poll_timeout: int, poll_interval_val: float,
    ) -> list[SourceItem]:
        """Parse via MinerU Agent lightweight API (no token)."""
        name = entry.get("name", "document")
        url = entry.get("url", "")
        path_val = entry.get("path", "")

        if url:
            return self._agent_parse_url(name, url, language, enable_table, enable_formula, is_ocr, poll_timeout, poll_interval_val)
        if path_val:
            return self._agent_parse_file(name, path_val, language, enable_table, enable_formula, is_ocr, poll_timeout, poll_interval_val)
        return []

    def _agent_parse_url(
        self, name: str, file_url: str,
        language: str, enable_table: bool, enable_formula: bool, is_ocr: bool,
        poll_timeout: int, poll_interval_val: float,
    ) -> list[SourceItem]:
        """Submit URL → poll → download markdown."""
        data: dict[str, Any] = {
            "url": file_url,
            "language": language,
            "enable_table": enable_table,
            "enable_formula": enable_formula,
            "is_ocr": is_ocr,
        }
        resp = _http_post_json(f"{MINERU_AGENT_API_BASE}/parse/url", data)
        if resp.get("code") != 0:
            return []
        task_id = resp["data"]["task_id"]

        markdown_text = self._agent_poll(task_id, poll_timeout, poll_interval_val)
        if not markdown_text:
            return []
        return self._md_to_items(name, markdown_text, "agent_api", url=file_url)

    def _agent_parse_file(
        self, name: str, file_path: str,
        language: str, enable_table: bool, enable_formula: bool, is_ocr: bool,
        poll_timeout: int, poll_interval_val: float,
    ) -> list[SourceItem]:
        """Get signed upload URL → PUT file → poll → download markdown."""
        file_name = Path(file_path).name
        data: dict[str, Any] = {
            "file_name": file_name,
            "language": language,
            "enable_table": enable_table,
            "enable_formula": enable_formula,
            "is_ocr": is_ocr,
        }
        resp = _http_post_json(f"{MINERU_AGENT_API_BASE}/parse/file", data)
        if resp.get("code") != 0:
            return []
        task_id = resp["data"]["task_id"]
        signed_url = resp["data"]["file_url"]

        if not _http_put_file(signed_url, file_path):
            return []

        markdown_text = self._agent_poll(task_id, poll_timeout, poll_interval_val)
        if not markdown_text:
            return []
        return self._md_to_items(name, markdown_text, "agent_api", url=file_path)

    def _agent_poll(self, task_id: str, timeout: int, interval: float) -> str | None:
        """Poll agent parse result, return markdown text or None."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = _http_get_json(f"{MINERU_AGENT_API_BASE}/parse/{task_id}")
            except Exception:
                time.sleep(interval)
                continue
            state = r.get("data", {}).get("state", "")
            if state == "done":
                markdown_url = r["data"].get("markdown_url", "")
                if not markdown_url:
                    return None
                try:
                    return urllib.request.urlopen(markdown_url, timeout=30).read().decode("utf-8")
                except Exception:
                    return None
            if state == "failed":
                return None
            time.sleep(interval)
        return None

    # ── remote premium API ────────────────────────────────────────

    def _parse_remote_premium(
        self, entry: dict[str, Any], config: dict[str, Any],
        language: str, poll_timeout: int, poll_interval_val: float,
    ) -> list[SourceItem]:
        """Parse via MinerU premium API (Bearer token)."""
        name = entry.get("name", "document")
        url = entry.get("url", "")
        path_val = entry.get("path", "")
        token = config.get("api_token", "") or os.environ.get("MINERU_API_TOKEN", "")
        model = config.get("model_version", "pipeline")

        if not token:
            return []

        headers = {"Authorization": f"Bearer {token}"}

        if url:
            return self._premium_parse_url(name, url, token, model, language, poll_timeout, poll_interval_val, headers)
        if path_val:
            return self._premium_parse_file(name, path_val, token, model, language, poll_timeout, poll_interval_val, headers)
        return []

    def _premium_parse_url(
        self, name: str, file_url: str, token: str, model: str,
        language: str, timeout: int, interval: float, headers: dict[str, str],
    ) -> list[SourceItem]:
        """Submit URL → poll → download zip → extract full.md."""
        data: dict[str, Any] = {"url": file_url, "model_version": model, "language": language}
        resp = _http_post_json(f"{MINERU_PREMIUM_API_BASE}/extract/task", data, headers=headers)
        if resp.get("code") != 0:
            return []
        task_id = resp["data"]["task_id"]

        zip_url = self._premium_poll(task_id, timeout, interval, headers)
        if not zip_url:
            return []
        md_text = self._download_and_extract_zip(zip_url)
        if not md_text:
            return []
        return self._md_to_items(name, md_text, "premium_api", url=file_url)

    def _premium_parse_file(
        self, name: str, file_path: str, token: str, model: str,
        language: str, timeout: int, interval: float, headers: dict[str, str],
    ) -> list[SourceItem]:
        """Batch upload local file → poll → download zip → extract full.md."""
        file_name = Path(file_path).name
        data: dict[str, Any] = {
            "files": [{"name": file_name}],
            "model_version": model,
            "language": language,
        }
        resp = _http_post_json(f"{MINERU_PREMIUM_API_BASE}/file-urls/batch", data, headers=headers)
        if resp.get("code") != 0:
            return []
        file_urls = resp["data"].get("file_urls", [])
        if not file_urls:
            return []
        batch_id = resp["data"].get("batch_id", "")

        if not _http_put_file(file_urls[0], file_path):
            return []

        # Poll batch result
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = _http_get_json(f"{MINERU_PREMIUM_API_BASE}/extract-results/batch/{batch_id}", headers=headers)
            except Exception:
                time.sleep(interval)
                continue
            for result in r.get("data", {}).get("extract_result", []):
                if result.get("state") == "done":
                    zip_url = result.get("full_zip_url", "")
                    if zip_url:
                        md_text = self._download_and_extract_zip(zip_url)
                        if md_text:
                            return self._md_to_items(name, md_text, "premium_api", url=file_path)
                if result.get("state") == "failed":
                    return []
            time.sleep(interval)
        return []

    def _premium_poll(self, task_id: str, timeout: int, interval: float, headers: dict[str, str]) -> str | None:
        """Poll premium task, return full_zip_url or None."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = _http_get_json(f"{MINERU_PREMIUM_API_BASE}/extract/task/{task_id}", headers=headers)
            except Exception:
                time.sleep(interval)
                continue
            state = r.get("data", {}).get("state", "")
            if state == "done":
                return r["data"].get("full_zip_url", "")
            if state == "failed":
                return None
            time.sleep(interval)
        return None

    def _download_and_extract_zip(self, zip_url: str) -> str | None:
        """Download zip from URL, extract full.md, return text or None."""
        try:
            resp = urllib.request.urlopen(zip_url, timeout=120)
            zip_bytes = resp.read()
        except Exception:
            return None
        try:
            with ZipFile(BytesIO(zip_bytes)) as zf:
                if "full.md" in zf.namelist():
                    return zf.read("full.md").decode("utf-8")
                # Try to find any .md file
                for fname in zf.namelist():
                    if fname.endswith(".md") and "full" in fname.lower():
                        return zf.read(fname).decode("utf-8")
        except Exception:
            return None
        return None

    # ── shared: markdown → SourceItems ────────────────────────────

    def _md_to_items(self, name: str, text: str, api_label: str, url: str = "") -> list[SourceItem]:
        """Convert a single Markdown string into one SourceItem."""
        text = text.strip()
        if not text:
            return []
        dedupe_key = f"mineru_{name}_{sha1(text[:200].encode()).hexdigest()[:12]}"
        return [
            SourceItem(
                source_id=f"mineru_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                source_name=f"MinerU ({api_label}): {name}",
                source_type="mineru",
                title=f"[MinerU] {name}",
                content=text[:5000],
                url=url,
                published_at="",
                retrieved_at="",
                language="",
                reliability="high",
                dedupe_key=dedupe_key,
                metadata={
                    "backend": f"mineru_{api_label}",
                    "source_name": name,
                    "remote_url": url,
                    "char_count": len(text),
                },
            )
        ]

    # ── local CLI (unchanged) ─────────────────────────────────────

    def _parse_entry_local(
        self, entry: dict[str, Any], backend: str, output_dir_base: str
    ) -> list[SourceItem]:
        """Parse one path entry via mineru CLI and return SourceItems."""
        name = entry.get("name", "document")
        file_path = entry.get("path", "")
        path_obj = Path(file_path)
        if not path_obj.exists():
            return []

        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
        output_dir = Path(output_dir_base) / safe_name
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = ["mineru", "-p", str(path_obj.absolute()), "-o", str(output_dir.absolute()), "-b", backend]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=MINERU_CLI_TIMEOUT)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        if result.returncode != 0:
            return []

        items: list[SourceItem] = []
        seen_content: set[str] = set()

        for md_file in sorted(output_dir.rglob("*.md")):
            if md_file.stem == "metadata":
                continue
            try:
                text = md_file.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if not text or text in seen_content:
                continue
            seen_content.add(text)

            dedupe_key = f"mineru_{name}_{md_file.name}"
            items.append(
                SourceItem(
                    source_id=f"mineru_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                    source_name=f"MinerU: {name}",
                    source_type="mineru",
                    title=f"[MinerU] {name} - {md_file.stem}",
                    content=text[:5000],
                    url=str(md_file),
                    published_at="",
                    retrieved_at="",
                    language="",
                    reliability="high",
                    dedupe_key=dedupe_key,
                    metadata={
                        "backend": "mineru_cli",
                        "source_name": name,
                        "file_path": file_path,
                        "format": "markdown",
                        "char_count": len(text),
                    },
                )
            )

        for json_file in sorted(output_dir.rglob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                pages = data.get("pages", data.get("documents", [data]))
                if isinstance(pages, list):
                    for page in pages:
                        page_text = (page.get("text", page.get("content", "")) or "").strip()
                        if not page_text or page_text in seen_content:
                            continue
                        seen_content.add(page_text)
                        page_num = page.get("page_number", page.get("page_num", ""))
                        dedupe_key = f"mineru_{name}_json_{json_file.stem}_p{page_num}"
                        items.append(
                            SourceItem(
                                source_id=f"mineru_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                                source_name=f"MinerU: {name}",
                                source_type="mineru",
                                title=f"[MinerU] {name} - page {page_num}",
                                content=page_text[:5000],
                                url=str(json_file),
                                published_at="",
                                retrieved_at="",
                                language="",
                                reliability="high",
                                dedupe_key=dedupe_key,
                                metadata={
                                    "backend": "mineru_cli",
                                    "source_name": name,
                                    "file_path": file_path,
                                    "format": "json_page",
                                    "page_number": page_num,
                                    "char_count": len(page_text),
                                },
                            )
                        )

        return items
