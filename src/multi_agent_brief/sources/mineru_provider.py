"""MinerU document parsing source provider.

Uses mineru CLI to parse PDF, DOCX, PPTX, XLSX, and image files
into structured Markdown/JSON for use as brief source material.

Requires mineru to be installed:
  pip install "mineru[all]"
  # or: uv pip install -U "mineru[all]"

Usage:
  mineru -p <input_file_or_dir> -o <output_dir>
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from hashlib import sha1
from pathlib import Path
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery

MINERU_DEFAULT_OUTPUT_DIR = "output/mineru_output"
MINERU_TIMEOUT = 600  # 10 minutes for large documents


class MineruProvider(SourceProvider):
    """Document parsing provider using mineru CLI.

    Configuration (in sources.yaml):

    .. code-block:: yaml

        mineru:
          enabled: true
          paths:
            - name: "Q1 Report"
              path: "input/q1-report.pdf"
            - name: "Research Papers"
              path: "input/papers/"
          backend: pipeline    # pipeline | hybrid | vlm
          output_dir: "output/mineru_output"
    """

    name = "mineru"
    source_type = "mineru"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []

        if not shutil.which("mineru"):
            errors.append(
                "mineru: 'mineru' not found in PATH. "
                "Install: pip install \"mineru[all]\""
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

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

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
                result = self._parse_entry(entry, backend, output_dir_base)
                items.extend(result)
            except Exception:
                continue

        return items

    def _parse_entry(
        self, entry: dict[str, Any], backend: str, output_dir_base: str
    ) -> list[SourceItem]:
        """Parse one path entry via mineru CLI and return SourceItems."""
        name = entry.get("name", "document")
        file_path = entry.get("path", "")
        path_obj = Path(file_path)
        if not path_obj.exists():
            return []

        # Determine output dir for this entry: base/name/
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
        output_dir = Path(output_dir_base) / safe_name
        output_dir.mkdir(parents=True, exist_ok=True)

        # Run mineru CLI
        cmd = ["mineru", "-p", str(path_obj.absolute()), "-o", str(output_dir.absolute()), "-b", backend]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=MINERU_TIMEOUT)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        if result.returncode != 0:
            return []

        # mineru outputs .md and .json files in output_dir
        items: list[SourceItem] = []
        seen_content: set[str] = set()

        # Collect .md files first
        md_files = sorted(output_dir.rglob("*.md"))
        json_files = sorted(output_dir.rglob("*.json"))

        for md_file in md_files:
            if md_file.stem == "metadata":
                continue
            try:
                text = md_file.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if not text or text in seen_content:
                continue
            seen_content.add(text)

            title = f"[MinerU] {name} - {md_file.stem}"
            dedupe_key = f"mineru_{name}_{md_file.name}"

            items.append(
                SourceItem(
                    source_id=f"mineru_{sha1(dedupe_key.encode()).hexdigest()[:12]}",
                    source_name=f"MinerU: {name}",
                    source_type="mineru",
                    title=title[:200],
                    content=text[:5000],
                    url=str(md_file),
                    published_at="",
                    retrieved_at="",
                    language="",
                    reliability="high",
                    dedupe_key=dedupe_key,
                    metadata={
                        "backend": "mineru",
                        "source_name": name,
                        "file_path": file_path,
                        "format": "markdown",
                        "char_count": len(text),
                    },
                )
            )

        # For JSON files, parse records if they contain useful data
        for json_file in json_files:
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                # Extract page-level content
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
                                    "backend": "mineru",
                                    "source_name": name,
                                    "file_path": file_path,
                                    "format": "json_page",
                                    "page_number": page_num,
                                    "char_count": len(page_text),
                                },
                            )
                        )

        return items
