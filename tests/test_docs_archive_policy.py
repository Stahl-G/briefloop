from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"
MARKDOWN_LINK_RE = re.compile(r"!?\[[^]]*\]\((?P<target>[^)]+)\)")
ARCHIVE_REFERENCE_RE = re.compile(r"(?:\bdocs/archive/|\]\((?:\.\./)?archive/)", re.I)
ARCHIVE_AUTHORITY_PATTERNS = (
    re.compile(r"\bdefines?\b.*\b(?:architecture|behavior|contract|engineering|implementation|support)\b", re.I),
    re.compile(r"\b(?:is|remains|serves as)\b.*\b(?:authoritative|canonical|current)\b", re.I),
    re.compile(r"\b(?:architecture|behavior|engineering|implementation|support)\s+(?:source of )?truth\b", re.I),
    re.compile(r"\bsource of truth\b", re.I),
)
ARCHIVE_AUTHORITY_NEGATION_RE = re.compile(
    r"\b(?:do not|does not|must not|never|no longer|not updated)\b[^.]*"
    r"\b(?:authority|current|implementation|support|truth)\b",
    re.I,
)


def _current_markdown_files() -> list[Path]:
    root_docs = list(ROOT.glob("*.md"))
    current_docs = [path for path in DOCS.rglob("*.md") if ARCHIVE not in path.parents]
    return sorted(root_docs + current_docs)


def _markdown_statements(text: str) -> list[tuple[int, str]]:
    statements: list[tuple[int, str]] = []
    current: list[str] = []
    start_line = 1
    list_item_re = re.compile(r"^\s*(?:[-*+] |\d+[.)] )")

    def flush() -> None:
        if current:
            statements.append((start_line, " ".join(part.strip() for part in current)))
            current.clear()

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            flush()
            continue
        if list_item_re.match(line) and current:
            flush()
        if not current:
            start_line = line_number
        current.append(line)
    flush()
    return statements


def test_current_docs_do_not_use_archive_as_implementation_or_support_truth() -> None:
    offenders: list[str] = []
    for path in _current_markdown_files():
        for line_number, statement in _markdown_statements(path.read_text(encoding="utf-8")):
            if not ARCHIVE_REFERENCE_RE.search(statement):
                continue
            normalized = statement.replace("`", "")
            if (
                any(pattern.search(normalized) for pattern in ARCHIVE_AUTHORITY_PATTERNS)
                and not ARCHIVE_AUTHORITY_NEGATION_RE.search(normalized)
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {statement.strip()}")

    assert offenders == [], "Current docs must not use archived documents as authority:\n" + "\n".join(offenders)


def test_archived_markdown_relative_links_resolve_after_moves() -> None:
    broken: list[str] = []
    for path in sorted(ARCHIVE.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            target = match.group("target").strip().strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not (path.parent / target).exists():
                line_number = text.count("\n", 0, match.start()) + 1
                broken.append(f"{path.relative_to(ROOT)}:{line_number}: {target}")

    assert broken == [], "Broken relative links in archived Markdown:\n" + "\n".join(broken)
