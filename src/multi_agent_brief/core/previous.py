from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


def load_previous_report_baseline(directory: str | Path, report_date: str = "", limit: int = 5) -> tuple[str, list[str]]:
    """Load previous md/txt/docx reports as a deduplication baseline."""
    root = Path(directory)
    if not directory or not root.exists():
        return "", []

    current = _parse_report_date(report_date)
    candidates = [
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in {".md", ".txt", ".docx"}
    ]
    eligible = []
    for path in candidates:
        file_date = _date_from_name(path)
        if current and file_date and file_date >= current:
            continue
        eligible.append(path)

    eligible.sort(key=lambda path: (_date_from_name(path) or datetime.min.date(), path.stat().st_mtime), reverse=True)

    parts: list[str] = []
    names: list[str] = []
    for path in eligible[:limit]:
        text = _read_report_text(path).strip()
        if not text:
            continue
        names.append(path.name)
        parts.append(f"===== {path.name} =====\n{text}")
    return "\n\n".join(parts), names


def _parse_report_date(value: str):
    if not value:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _date_from_name(path: Path):
    match = re.search(r"(20\d{6})", path.name)
    if not match:
        return None
    return _parse_report_date(match.group(1))


def _read_report_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return _extract_docx_text(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(errors="ignore")


def _extract_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    except Exception:
        return ""
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines = []
    for paragraph in root.findall(".//w:p", ns):
        texts = [node.text for node in paragraph.findall(".//w:t", ns) if node.text]
        if texts:
            lines.append("".join(texts))
    return "\n".join(lines)

