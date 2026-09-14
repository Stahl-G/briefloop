"""Query-blind local read-only corpus adapter for OfficeQA Pro V2 (design §3.Q2).

Protocol BL-OQA-SR-v1.0 §6.1: A/B share one full-corpus surface — search,
snippet read and page view over all 1,435 documents — built without ever
reading the questions' provenance hints.  This module only ever receives
corpus directories; it has no code path that opens a question file, the
gated CSVs or any gold.

Two phases, two trust boundaries:

* ``build``/``probe`` (data preparation): stages the official parse from the
  source corpus into the data area and builds a deterministic SQLite index.
  This is the only phase allowed to touch the original dataset directory,
  read-only.
* ``CorpusAdapter`` / the ``docs``/``search``/``read``/``page``/``accept``
  commands (episode time): open the staged corpus under the data area in
  read-only mode.  The episode runner never references the dataset directory.

The V2 JSON parse is never ``repr``-ed into one blob (§6.1): every indexed
line is one ``document.elements`` entry, so line numbers, ``page_index`` and
``element_index`` agree with the evidence locators of protocol §3.2.  ``accept``
registers a document the agent actually used as a real BriefLoop run source
(Store.add_source + attach_source — the same deterministic acceptance path
``add-url`` uses), so B's evidence ``source_id`` refers to a registered source
and the Q1 attachment validation can locate excerpts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

INDEX_SCHEMA_VERSION = "officeqa.corpus_index.v1"
DEFAULT_DATA_ROOT = Path.home() / "Developer" / "briefloop-data" / "officeqa"

_JSON_REL = ("documents", "parsed")
_TXT_REL = ("documents", "text")
_PDF_REL = ("documents", "pdf")
_INDEX_REL = ("index",)
_PROBE_REL = ("probe",)

_READ_ELEMENT_CHARS = 1500  # page-view truncation per element
_CANDIDATE_TOKENS = 6  # probe: tokens tried per document
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{5,}")


class CorpusError(RuntimeError):
    """Corpus staging/index/probe failed closed."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# --- staging (data preparation phase; reads the dataset directory) ------------


def _source_pairs(source_root: Path) -> list[tuple[Path, Path]]:
    """Pair each official JSON parse with its transformed TXT, 1:1 by stem."""
    json_dir = source_root / "parsed_corpus" / "jsons"
    txt_dir = source_root / "treasury_bulletins_parsed" / "transformed"
    if not json_dir.is_dir():
        raise CorpusError(f"parsed JSON corpus is missing: {json_dir}")
    if not txt_dir.is_dir():
        raise CorpusError(f"transformed TXT corpus is missing: {txt_dir}")
    jsons = {path.stem: path for path in sorted(json_dir.glob("*.json"))}
    txts = {path.stem: path for path in sorted(txt_dir.glob("*.txt"))}
    if not jsons:
        raise CorpusError(f"no *.json documents under {json_dir}")
    json_only = sorted(set(jsons) - set(txts))
    txt_only = sorted(set(txts) - set(jsons))
    if json_only or txt_only:
        preview = (json_only + txt_only)[:8]
        raise CorpusError(
            f"corpus sides disagree ({len(json_only)} json-only, {len(txt_only)} txt-only): {preview}"
        )
    return [(jsons[stem], txts[stem]) for stem in sorted(jsons)]


def _pdf_sources(source_root: Path) -> list[Path]:
    """PDF originals when the corpus release ships them (0 in this V2 tree).

    Checked layouts: ``parsed_corpus/pdfs/*.pdf`` and PDFs sitting next to
    the JSON parses.  The local V2 fullcorpus has been verified to contain
    none (``find fullcorpus -name '*.pdf' | wc -l`` == 0), so visual parity
    per protocol §6.1 is moot rather than asymmetric: both arms share the
    same parsed-element page view.  The staging mechanism stays so a later
    release with PDFs is picked up instead of silently dropped.
    """
    candidates: list[Path] = []
    pdfs_dir = source_root / "parsed_corpus" / "pdfs"
    if pdfs_dir.is_dir():
        candidates.extend(sorted(pdfs_dir.glob("*.pdf")))
    candidates.extend(sorted((source_root / "parsed_corpus").glob("*.pdf")))
    unique: dict[str, Path] = {path.name: path for path in candidates}
    return [unique[name] for name in sorted(unique)]


def stage_documents(source_root: Path, corpus_root: Path) -> dict[str, Any]:
    """Stage the official corpus into the data area, read-only on the source.

    Hardlinks when the data area is on the same volume (no duplicate bytes),
    copies otherwise.  Existing identical targets are left alone so the
    command is re-runnable; a differing target fails closed.  PDF originals
    are staged into ``documents/pdf`` when present — this release ships
    none, which the report records as a corpus condition.
    """
    source_root = Path(source_root).expanduser().resolve()
    corpus_root = Path(corpus_root).expanduser().resolve()
    json_target = corpus_root.joinpath(*_JSON_REL)
    txt_target = corpus_root.joinpath(*_TXT_REL)
    pdf_target = corpus_root.joinpath(*_PDF_REL)
    json_target.mkdir(parents=True, exist_ok=True)
    txt_target.mkdir(parents=True, exist_ok=True)
    pdf_target.mkdir(parents=True, exist_ok=True)
    linked = copied = kept = 0

    def _stage(src: Path, dst_dir: Path) -> None:
        nonlocal linked, copied, kept
        dst = dst_dir / src.name
        if dst.exists():
            try:
                if os.path.samefile(src, dst) or _sha256_file(src) == _sha256_file(dst):
                    kept += 1
                    return
            except OSError:
                pass
            raise CorpusError(f"staged corpus file differs from source, refusing to overwrite: {dst}")
        try:
            os.link(src, dst)
            linked += 1
        except OSError:
            shutil.copy2(src, dst)
            copied += 1

    for json_path, txt_path in _source_pairs(source_root):
        _stage(json_path, json_target)
        _stage(txt_path, txt_target)
    pdfs = _pdf_sources(source_root)
    for pdf_path in pdfs:
        _stage(pdf_path, pdf_target)
    return {"documents": len(_source_pairs(source_root)), "linked": linked,
            "copied": copied, "already_current": kept,
            "pdf_documents": len(pdfs),
            "pdf_note": ("PDF originals staged under documents/pdf for the render-source path"
                         if pdfs else
                         "this V2 corpus release ships parsed JSON + TXT only (verified: no *.pdf "
                         "under fullcorpus); protocol §6.1 visual parity is therefore moot — both "
                         "arms share the same parsed-element page view, recorded as a corpus condition"),
            "json_dir": str(json_target), "txt_dir": str(txt_target)}


# --- index build --------------------------------------------------------------


def _element_lines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Project one V2 JSON document onto element-per-line rows.

    Line numbers are 1-based and align with ``element_id`` order; content is
    whitespace-flattened so one element is exactly one line — the same
    projection backs search, read, page view and accepted sources.

    Raw ``page_id`` conventions are not uniform across the corpus (some
    documents are 1-based, several have sparse ids for blank pages), so the
    canonical ``page_index`` is the 0-based rank of the raw id among the
    document's distinct ids (protocol §3.2: page_index 一律 0-based).  The raw
    id is kept as ``raw_page_id`` for provenance; an element with no bbox
    carries the previous element's page forward.
    """
    document = payload.get("document") if isinstance(payload, dict) else None
    elements = document.get("elements") if isinstance(document, dict) else None
    if not isinstance(elements, list):
        raise CorpusError("document JSON has no document.elements list")
    raw_pages: list[int] = []
    page = None
    for element in elements:
        if not isinstance(element, dict):
            continue
        bbox = element.get("bbox")
        if isinstance(bbox, list) and bbox and isinstance(bbox[0], dict):
            candidate = bbox[0].get("page_id")
            if isinstance(candidate, int):
                page = candidate
        raw_pages.append(page if page is not None else 0)
    canonical = {raw: index for index, raw in enumerate(sorted(set(raw_pages)))}
    rows: list[dict[str, Any]] = []
    for element, raw_page in zip((e for e in elements if isinstance(e, dict)), raw_pages, strict=True):
        content = element.get("content")
        text = " ".join(str(content).split()) if content is not None else ""
        rows.append({"line": len(rows) + 1, "page_id": canonical[raw_page], "raw_page_id": raw_page,
                     "element_id": element.get("id"), "etype": str(element.get("type") or ""),
                     "content": text})
    if not rows:
        raise CorpusError("document has no projectable elements")
    return rows


def _connect(db_path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        # Episode runtimes call the adapter from worker threads; read-only
        # access from any thread is safe and required (protocol §6.1 shared tool).
        connection = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
    else:
        connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def build_index(corpus_root: Path) -> dict[str, Any]:
    """Parse every staged JSON into the SQLite index; write index/manifest.json."""
    corpus_root = Path(corpus_root).expanduser().resolve()
    json_dir = corpus_root.joinpath(*_JSON_REL)
    txt_dir = corpus_root.joinpath(*_TXT_REL)
    index_dir = corpus_root.joinpath(*_INDEX_REL)
    index_dir.mkdir(parents=True, exist_ok=True)
    jsons = sorted(json_dir.glob("*.json"))
    txts = {path.stem for path in txt_dir.glob("*.txt")}
    if not jsons:
        raise CorpusError(f"no staged JSON documents under {json_dir}; run staging first")
    missing_txt = sorted({p.stem for p in jsons} - txts)
    if missing_txt:
        raise CorpusError(f"staged corpus is missing transformed TXT for: {missing_txt[:8]}")

    db_path = index_dir / "corpus.sqlite"
    started = time.monotonic()
    if db_path.exists():
        db_path.unlink()
    connection = _connect(db_path, readonly=False)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.executescript(
            """
            CREATE TABLE docs(
              doc_id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL,
              json_path TEXT NOT NULL, text_path TEXT NOT NULL,
              sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
              pages INTEGER NOT NULL, elements INTEGER NOT NULL);
            CREATE TABLE elements(
              id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL, line INTEGER NOT NULL,
              page_id INTEGER NOT NULL, raw_page_id INTEGER NOT NULL, element_id INTEGER,
              etype TEXT NOT NULL, content TEXT NOT NULL,
              UNIQUE(doc_id, line));
            CREATE INDEX elements_doc_page ON elements(doc_id, page_id, line);
            CREATE VIRTUAL TABLE elements_fts USING fts5(
              content, doc_id, content='elements', content_rowid='id');
            """
        )
        total_elements = 0
        element_pk = 0
        for number, json_path in enumerate(jsons, start=1):
            doc_id = f"d{number:04d}"
            raw = json_path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
            rows = _element_lines(payload)
            pages = len({row["raw_page_id"] for row in rows})
            connection.execute(
                "INSERT INTO docs VALUES(?,?,?,?,?,?,?,?)",
                (doc_id, json_path.stem, str(json_path), str(txt_dir / f"{json_path.stem}.txt"),
                 _sha256_file(json_path), json_path.stat().st_size, pages, len(rows)),
            )
            # Explicit element ids keep elements.id == elements_fts.rowid without
            # relying on autoincrement alignment.
            connection.executemany(
                "INSERT INTO elements(id,doc_id,line,page_id,raw_page_id,element_id,etype,content)"
                " VALUES(?,?,?,?,?,?,?,?)",
                [((element_pk := element_pk + 1), doc_id, row["line"], row["page_id"],
                  row["raw_page_id"], row["element_id"], row["etype"], row["content"])
                 for row in rows],
            )
            total_elements += len(rows)
            connection.commit()
            if number % 200 == 0:
                print(f"  parsed {number}/{len(jsons)} documents", flush=True)
        # External-content FTS5: index every elements row from the content
        # table itself, then verify the index is self-consistent.
        connection.execute("INSERT INTO elements_fts(elements_fts) VALUES('rebuild')")
        connection.execute("INSERT INTO elements_fts(elements_fts) VALUES('integrity-check')")
        connection.commit()
        count = connection.execute("SELECT COUNT(*) AS n FROM docs").fetchone()["n"]
        if count != len(jsons):
            raise CorpusError(f"index has {count} docs, staged {len(jsons)}")
    except sqlite3.Error as exc:
        raise CorpusError(f"corpus index build failed: {exc}") from exc
    finally:
        connection.close()
    manifest = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "built_at": _now(),
        "documents": len(jsons),
        "total_elements": total_elements,
        "projection": ("one line per document element, 1-based, content whitespace-flattened; "
                       "page_id is the 0-based rank of the raw parse page id among the document's "
                       "distinct ids (raw conventions vary across the corpus); raw_page_id kept "
                       "for provenance"),
        "db": str(db_path),
        "db_bytes": db_path.stat().st_size,
        "build_seconds": round(time.monotonic() - started, 1),
    }
    (index_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    return manifest


# --- episode-time read-only adapter -------------------------------------------


@dataclass(frozen=True)
class SearchHit:
    doc_id: str
    name: str
    line: int
    page_id: int
    element_id: int | None
    etype: str
    snippet: str


def _fts_query(terms: Iterable[str]) -> str:
    """Neutralize arbitrary user text into AND-ed quoted word tokens.

    Punctuation and FTS5 operators are stripped before quoting, so a hostile
    or accidental query string can neither raise a syntax error nor broaden
    into something the caller did not ask for.
    """
    tokens: list[str] = []
    for term in terms:
        tokens.extend(re.findall(r"\w+", term, re.UNICODE))
    if not tokens:
        raise CorpusError("search query has no usable terms")
    return " AND ".join(f'"{token}"' for token in tokens)


class CorpusAdapter:
    """Read-only query surface over the staged corpus (no dataset directory)."""

    def __init__(self, corpus_root: Path | str):
        self.root = Path(corpus_root).expanduser().resolve()
        manifest_path = self.root.joinpath(*_INDEX_REL) / "manifest.json"
        db_path = self.root.joinpath(*_INDEX_REL) / "corpus.sqlite"
        if not manifest_path.is_file() or not db_path.is_file():
            raise CorpusError(f"corpus index is missing under {self.root}; run build first")
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._db = _connect(db_path, readonly=True)
        # One adapter is shared by the episode's worker threads; SQLite
        # connections must not run interleaved cursors, so every query takes
        # this lock (read-only, millisecond-scale — no contention concern).
        self._query_lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "CorpusAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _doc_row(self, doc: str) -> sqlite3.Row:
        with self._query_lock:
            row = self._db.execute("SELECT * FROM docs WHERE name=? OR doc_id=?", (doc, doc)).fetchone()
        if row is None:
            raise CorpusError(f"unknown document {doc!r}; use the docs command to find names")
        return row

    def docs(self, query: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT doc_id, name, pages, elements FROM docs"
        args: tuple[Any, ...] = ()
        if query:
            sql += " WHERE name LIKE ?"
            args = (f"%{query}%",)
        sql += " ORDER BY name LIMIT ?"
        with self._query_lock:
            return [dict(row) for row in self._db.execute(sql, (*args, limit))]

    def search(self, query: str, *, doc: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Keyword search across the whole corpus (or one document).

        Terms are AND-ed; each token is quoted so punctuation cannot inject
        FTS5 syntax.  Hits carry the element line so ``read``/``page`` and the
        evidence locators of protocol §3.2 agree.
        """
        match = _fts_query(query.split())
        if doc is not None:
            match = f'"{self._doc_row(doc)["doc_id"]}" AND {match}'
        sql = (
            "SELECT f.doc_id AS doc_id, d.name AS name, e.line AS line, e.page_id AS page_id,"
            " e.raw_page_id AS raw_page_id, e.element_id AS element_id, e.etype AS etype,"
            " snippet(elements_fts, 0, '«', '»', '…', 10) AS snippet"
            " FROM elements_fts f JOIN elements e ON e.id=f.rowid JOIN docs d ON d.doc_id=e.doc_id"
            " WHERE elements_fts MATCH ? ORDER BY d.name, e.line LIMIT ?"
        )
        with self._query_lock:
            return [dict(row) for row in self._db.execute(sql, (match, max(1, int(limit))))]

    def read(self, doc: str, start_line: int, end_line: int, *, max_chars: int = 6000) -> dict[str, Any]:
        """Read a line range of one document with the page mapping."""
        row = self._doc_row(doc)
        start, end = int(start_line), int(end_line)
        if start < 1 or end < start:
            raise CorpusError(f"line range must be 1-based with start<=end, got {start}..{end}")
        with self._query_lock:
            lines = self._db.execute(
                "SELECT line, page_id, raw_page_id, element_id, etype, content FROM elements"
                " WHERE doc_id=? AND line BETWEEN ? AND ? ORDER BY line",
                (row["doc_id"], start, end),
            ).fetchall()
        emitted: list[dict[str, Any]] = []
        used = 0
        truncated = False
        for element in lines:
            content = element["content"]
            if used + len(content) > max_chars and emitted:
                truncated = True
                break
            emitted.append(dict(element))
            used += len(content)
        return {"doc": row["name"], "doc_id": row["doc_id"], "start_line": start, "end_line": end,
                "lines": emitted, "returned": len(emitted),
                "truncated": truncated or len(emitted) < len(lines)}

    def page(self, doc: str, page_index: int, *, max_element_chars: int = _READ_ELEMENT_CHARS) -> dict[str, Any]:
        """View one 0-based page: its elements in reading order."""
        row = self._doc_row(doc)
        page = int(page_index)
        with self._query_lock:
            elements = self._db.execute(
                "SELECT line, element_id, etype, content, raw_page_id FROM elements"
                " WHERE doc_id=? AND page_id=? ORDER BY line",
                (row["doc_id"], page),
            ).fetchall()
        if not elements:
            raise CorpusError(f"document {row['name']} has no page_index={page} (it has {row['pages']} pages)")
        view = []
        for element in elements:
            content = element["content"]
            view.append({"line": element["line"], "element_id": element["element_id"],
                         "type": element["etype"], "raw_page_id": element["raw_page_id"],
                         "content": content if len(content) <= max_element_chars else content[:max_element_chars] + "…",
                         "truncated": len(content) > max_element_chars})
        return {"doc": row["name"], "doc_id": row["doc_id"], "page_index": page,
                "pages": row["pages"], "elements": view}

    def document_text(self, doc: str) -> str:
        """The full projected text of one document (element per line)."""
        row = self._doc_row(doc)
        with self._query_lock:
            rows = self._db.execute(
                "SELECT content FROM elements WHERE doc_id=? ORDER BY line", (row["doc_id"],)
            ).fetchall()
        if not rows:
            raise CorpusError(f"document {row['name']} has no indexed lines")
        return "\n".join(element["content"] for element in rows)

    def accept(self, store: Any, run_id: str, doc: str) -> dict[str, Any]:
        """Register a used document as a real BriefLoop run source (§6.1).

        Goes through the product's deterministic acceptance path
        (Store.add_source + attach_source — the same store calls ``add-url``
        uses), so evidence ``source_id`` values refer to a registered source
        whose saved text is exactly this document's projection.
        """
        row = self._doc_row(doc)
        text = self.document_text(row["name"])
        source = store.add_source(f"{row['name']}.txt", text)
        store.attach_source(run_id, source["id"])
        return {"source_id": source["id"], "name": f"{row['name']}.txt",
                "lines": text.count("\n") + 1, "pages": row["pages"]}


# --- visibility probe (no questions, no gold) ---------------------------------


def _probe_tokens(head_lines: list[dict[str, Any]]) -> list[str]:
    tokens = {match.lower() for line in head_lines for match in _TOKEN_RE.findall(line["content"])}
    return sorted(tokens, key=lambda token: (-len(token), token))[:_CANDIDATE_TOKENS]


def probe(corpus_root: Path, *, write_report: bool = True) -> dict[str, Any]:
    """Answer-free visibility probe: every document findable, searchable, readable.

    Probe queries come only from the corpus itself (fixed generic terms and
    each document's own tokens) — never from question text or gold.
    """
    started = time.monotonic()
    with CorpusAdapter(corpus_root) as adapter:
        db = adapter._db
        failures: list[dict[str, str]] = []
        docs = [dict(row) for row in db.execute(
            "SELECT doc_id, name, pages, elements, json_path, text_path FROM docs ORDER BY name")]
        if not docs:
            raise CorpusError("index has no documents")
        searchable = 0
        for entry in docs:
            name = entry["name"]
            if not Path(entry["json_path"]).is_file() or not Path(entry["text_path"]).is_file():
                failures.append({"doc": name, "check": "findable", "error": "staged file missing"})
                continue
            try:
                page = adapter.page(name, 0)
                head = adapter.read(name, 1, 3)
                if not page["elements"] and not head["lines"] and entry["elements"]:
                    raise CorpusError("no readable lines or page elements")
            except CorpusError as exc:
                failures.append({"doc": name, "check": "readable", "error": str(exc)})
                continue
            if any(adapter.search(token, doc=name, limit=1)
                   for token in _probe_tokens(adapter.read(name, 1, min(40, entry["elements"]))["lines"])):
                searchable += 1
            else:
                failures.append({"doc": name, "check": "searchable",
                                 "error": "no own-token FTS hit in document scope"})
        generic_hits = {term: len(adapter.search(term, limit=1000)) for term in ("treasury", "receipts", "the")}
        db.execute("SELECT COUNT(*) FROM elements_fts")  # full-table sanity walk
        pdf_dir = adapter.root.joinpath(*_PDF_REL)
        pdf_files = sorted(pdf_dir.glob("*.pdf")) if pdf_dir.is_dir() else []
        report = {
            "schema_version": "officeqa.corpus_probe.v1",
            "probed_at": _now(),
            "corpus_root": str(adapter.root),
            "documents": len(docs),
            "total_elements": adapter.manifest.get("total_elements"),
            "findable": len(docs) - sum(1 for f in failures if f["check"] == "findable"),
            "readable": len(docs) - sum(1 for f in failures if f["check"] == "readable"),
            "searchable": searchable,
            "generic_query_hits": generic_hits,
            "fts_integrity": "ok",
            "pdf": {"present": bool(pdf_files), "count": len(pdf_files),
                    "note": ("PDF originals staged under documents/pdf; the page command still returns "
                             "the parsed-element view — PDF page rendering goes through the product's "
                             "render-source tool on a registered source, and arm A needs the same "
                             "render capability wired before visual parity holds (protocol §6.1)"
                             if pdf_files else
                             "this V2 corpus ships parsed JSON + TXT only; no PDF originals exist to "
                             "stage, so §6.1 visual parity is moot — A/B share the same parsed-element "
                             "page view. Recorded as a corpus condition, not an arm asymmetry")},
            "failures": failures,
            # Per-document findable/readable/searchable is the gate; the fixed
            # generic terms are informational global sanity (a two-document
            # synthetic corpus need not contain "the").
            "status": "green" if not failures else "red",
            "seconds": round(time.monotonic() - started, 1),
        }
    if write_report:
        report_dir = Path(corpus_root).expanduser().resolve().joinpath(*_PROBE_REL)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "probe_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


# --- CLI -----------------------------------------------------------------------


def _require_data_root(value: str | None) -> Path:
    root = Path(value).expanduser().resolve() if value else DEFAULT_DATA_ROOT
    corpus_root = root / "corpus"
    if not corpus_root.is_dir():
        raise CorpusError(f"corpus area is missing: {corpus_root}")
    return corpus_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="stage the source corpus and build the index (data preparation)")
    build.add_argument("--source", required=True, help="V2 fullcorpus directory (read-only)")
    build.add_argument("--data-root", help=f"data area root (default {DEFAULT_DATA_ROOT})")

    probe_cmd = commands.add_parser("probe", help="answer-free visibility probe over every staged document")
    probe_cmd.add_argument("--data-root")

    docs = commands.add_parser("docs", help="list/find documents by name substring")
    docs.add_argument("--data-root")
    docs.add_argument("--query")

    search = commands.add_parser("search", help="keyword search across the whole corpus or one document")
    search.add_argument("--data-root")
    search.add_argument("--query", required=True)
    search.add_argument("--doc", help="restrict to one document (name or doc_id)")
    search.add_argument("--limit", type=int, default=20)

    read = commands.add_parser("read", help="read a 1-based line range of one document")
    read.add_argument("--data-root")
    read.add_argument("--doc", required=True)
    read.add_argument("--start-line", type=int, required=True)
    read.add_argument("--end-line", type=int, required=True)
    read.add_argument("--max-chars", type=int, default=6000)

    page = commands.add_parser("page", help="view one 0-based page of one document")
    page.add_argument("--data-root")
    page.add_argument("--doc", required=True)
    page.add_argument("--page-index", type=int, required=True)

    accept = commands.add_parser("accept", help="register a used document as a BriefLoop run source")
    accept.add_argument("--data-root")
    accept.add_argument("--workspace", required=True, help="BriefLoop workspace root of this episode")
    accept.add_argument("--run", required=True)
    accept.add_argument("--doc", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            data_root = Path(args.data_root).expanduser().resolve() if args.data_root else DEFAULT_DATA_ROOT
            staging = stage_documents(Path(args.source), data_root / "corpus")
            manifest = build_index(data_root / "corpus")
            print(json.dumps({"staging": staging, "manifest": manifest}, ensure_ascii=False, indent=2))
            return 0
        corpus_root = _require_data_root(args.data_root)
        if args.command == "probe":
            report = probe(corpus_root)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["status"] == "green" else 1
        if args.command == "docs":
            with CorpusAdapter(corpus_root) as adapter:
                print(json.dumps(adapter.docs(args.query, limit=200), ensure_ascii=False, indent=2))
            return 0
        if args.command == "search":
            with CorpusAdapter(corpus_root) as adapter:
                print(json.dumps(adapter.search(args.query, doc=args.doc, limit=args.limit),
                                 ensure_ascii=False, indent=2))
            return 0
        if args.command == "read":
            with CorpusAdapter(corpus_root) as adapter:
                print(json.dumps(adapter.read(args.doc, args.start_line, args.end_line,
                                              max_chars=args.max_chars), ensure_ascii=False, indent=2))
            return 0
        if args.command == "page":
            with CorpusAdapter(corpus_root) as adapter:
                print(json.dumps(adapter.page(args.doc, args.page_index), ensure_ascii=False, indent=2))
            return 0
        if args.command == "accept":
            from briefloop.store import Store

            store = Store(Path(args.workspace).expanduser().resolve())
            with CorpusAdapter(corpus_root) as adapter:
                print(json.dumps(adapter.accept(store, args.run, args.doc), ensure_ascii=False, indent=2))
            return 0
        parser.error(f"unknown command {args.command}")
        return 2
    except CorpusError as exc:
        print(f"corpus adapter error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
