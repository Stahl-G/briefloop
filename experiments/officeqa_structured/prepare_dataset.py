"""OfficeQA Pro V2 dataset preparation for the structured-answer experiment.

Design-0223 §3.Q2 / protocol BL-OQA-SR-v1.0 §6.4, §7.  This is the single
process allowed to read the restricted local dataset (the CSVs with gold and
the official corpus parse).  It never answers, never runs models, and never
modifies the source directory.  Outputs, all under the data area:

* ``gated/``           snapshot of the restricted CSVs (0700/0600)
* ``evaluator-only/``  gold side-loads used later by the isolated scorer (0700)
* ``question_only/``   sanitized question jsonl + exposure ledger — the only
                       question view the episode runner may open.  Fields are
                       allow-listed; answer, source_files, source_docs and any
                       difficulty-derived hint never leave this process except
                       through evaluator-only.
* ``corpus/``          staged full corpus + query-blind index + probe report
                       (delegated to corpus_adapter, 1,435 documents, no gold
                       filtering — protocol §6.1).

Case identity is ``dataset revision + UID + question hash`` (protocol §1.2:
V2 renumbered UIDs, so a bare number is not an identity).  The exposure
ledger starts at ``unknown`` and marks questions that also exist in the
historical OfficeQA Pro v1 CSV as ``exposed`` (protocol §7: 未知记 unknown，
不记 unseen); the v1 pool itself is listed as exposed for the Q3 pilot.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_adapter  # noqa: E402  (same experiment directory)

SCHEMA_QUESTION = "officeqa.question.v1"
SCHEMA_GOLD = "officeqa.gold.v1"
SCHEMA_LEDGER = "officeqa.exposure_ledger.v1"
DATASET = "officeqa-pro-v2"
HISTORICAL_DATASET = "officeqa-pro-v1"
V2_GLOB = "officeqa_pro_v2*.csv"
HISTORICAL_CSV = "officeqa_pro.csv"
DEFAULT_SOURCE_ROOT = Path.home() / "Developer" / "datasets" / "officeqa-pro-v2"
DEFAULT_DATA_ROOT = corpus_adapter.DEFAULT_DATA_ROOT
QUESTION_FIELDS = ("schema_version", "dataset", "revision", "uid",
                   "case_key", "question", "question_sha256")


class PrepareError(RuntimeError):
    """Dataset preparation failed closed."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_question(text: str) -> str:
    """Stable question normalization for hashing (NFKC + collapsed whitespace)."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def question_sha256(text: str) -> str:
    return hashlib.sha256(normalize_question(text).encode("utf-8")).hexdigest()


def split_names(raw: str) -> tuple[str, ...]:
    """Split a source_files/source_docs cell on newlines and ';'.

    V2 rows use semicolon-separated single cells; older payloads use
    multiline cells.  Order preserved, empties dropped.
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").replace(";", "\n")
    return tuple(part.strip() for part in text.split("\n") if part.strip())


@dataclass(frozen=True)
class CaseRecord:
    uid: str
    question: str
    answer: str
    source_files: tuple[str, ...]
    source_docs: tuple[str, ...]


def _load_csv(path: Path, *, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise PrepareError(f"required CSV is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise PrepareError(f"{path} has no data rows")
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise PrepareError(f"{path} lacks column(s): {sorted(missing)}")
    return rows


def load_v2_cases(source_root: Path) -> list[CaseRecord]:
    """Load and cross-validate every officeqa_pro_v2*.csv payload.

    All globbed files must agree on uid → (question, answer); any drift between
    the plain and normalized payloads fails closed instead of picking one.
    """
    csvs = sorted(source_root.glob(V2_GLOB))
    if not csvs:
        raise PrepareError(f"no {V2_GLOB} under {source_root}")
    rows_by_name: dict[str, list[dict[str, str]]] = {}
    mapping_by_name: dict[str, dict[str, tuple[str, str]]] = {}
    for path in csvs:
        rows = _load_csv(path, required={"uid", "question", "answer", "source_files", "source_docs"})
        mapping: dict[str, tuple[str, str]] = {}
        for row in rows:
            uid = (row.get("uid") or "").strip()
            if not uid:
                raise PrepareError(f"{path.name} has a row with no uid")
            if uid in mapping:
                raise PrepareError(f"{path.name} repeats uid {uid!r}")
            question = (row.get("question") or "").strip()
            answer = (row.get("answer") or "").strip()
            if not question or not answer:
                raise PrepareError(f"uid {uid!r} in {path.name} has an empty question or answer")
            mapping[uid] = (question, answer)
        rows_by_name[path.name] = rows
        mapping_by_name[path.name] = mapping
    names = sorted(mapping_by_name)
    reference_name = next((name for name in names if name.endswith(".normalized.csv")), names[0])
    for name, mapping in mapping_by_name.items():
        if mapping != mapping_by_name[reference_name]:
            drift = [uid for uid, value in mapping_by_name[reference_name].items()
                     if mapping.get(uid) != value][:5]
            raise PrepareError(f"{name} disagrees with {reference_name} on uid/question/answer: {drift}")
    cases = [
        CaseRecord(uid=(row["uid"] or "").strip(), question=(row["question"] or "").strip(),
                   answer=(row["answer"] or "").strip(),
                   source_files=split_names(row.get("source_files") or ""),
                   source_docs=split_names(row.get("source_docs") or ""))
        for row in rows_by_name[reference_name]
    ]
    if len({case.uid for case in cases}) != len(cases):
        raise PrepareError("duplicate uid in canonical V2 payload")
    return cases


def dataset_revision(cases: list[CaseRecord]) -> str:
    """Content hash over the canonical records (stable across line-ending churn)."""
    payload = [
        {"uid": case.uid, "question": normalize_question(case.question), "answer": case.answer,
         "source_files": list(case.source_files), "source_docs": list(case.source_docs)}
        for case in sorted(cases, key=lambda case: case.uid)
    ]
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def case_key(revision: str, uid: str, question_hash: str) -> str:
    """Protocol §1.2: dataset revision + UID + question hash."""
    return hashlib.sha256(f"{DATASET}\n{revision}\n{uid}\n{question_hash}".encode("utf-8")).hexdigest()


def load_historical_exposure(source_root: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Historical Pro v1 questions, exposed by prior experiments (§7).

    Returns ledger pool entries (question hashes only — no answers here) and
    a map of question hash → uid for overlap marking.
    """
    path = source_root / HISTORICAL_CSV
    if not path.is_file():
        return [], {}
    rows = _load_csv(path, required={"uid", "question"})
    pool: list[dict[str, Any]] = []
    by_hash: dict[str, str] = {}
    for row in rows:
        uid = (row.get("uid") or "").strip()
        question = (row.get("question") or "").strip()
        if not uid or not question:
            raise PrepareError(f"{HISTORICAL_CSV} has a row with no uid/question")
        digest = question_sha256(question)
        by_hash[digest] = uid
        pool.append({"dataset": HISTORICAL_DATASET, "uid": uid, "question_sha256": digest})
    return pool, by_hash


def _copy_restricted(path: Path, target_dir: Path) -> str:
    target = target_dir / path.name
    data = path.read_bytes()
    if target.exists() and target.read_bytes() != data:
        raise PrepareError(f"gated snapshot differs from source, refusing to overwrite: {target}")
    if not target.exists():
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(data)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _write_private(path: Path, text: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, mode)
    temporary.replace(path)


def prepare(source_root: Path, data_root: Path, *, corpus_source: Path | None = None,
            skip_corpus: bool = False) -> dict[str, Any]:
    """Run the full preparation; returns the summary written to the data area."""
    started = time.monotonic()
    source_root = Path(source_root).expanduser().resolve()
    data_root = Path(data_root).expanduser().resolve()
    if not source_root.is_dir():
        raise PrepareError(f"source root is missing: {source_root}")
    for name in ("gated", "question_only", "corpus", "episodes", "evaluator-only"):
        (data_root / name).mkdir(parents=True, exist_ok=True)
    os.chmod(data_root / "gated", 0o700)
    os.chmod(data_root / "evaluator-only", 0o700)

    # 1. gated snapshot of every restricted CSV (source directory stays untouched).
    restricted = sorted(source_root.glob(V2_GLOB)) + ([source_root / HISTORICAL_CSV]
                                                      if (source_root / HISTORICAL_CSV).is_file() else [])
    if not restricted:
        raise PrepareError(f"no restricted CSVs under {source_root}")
    gated_hashes = {path.name: _copy_restricted(path, data_root / "gated") for path in restricted}

    # 2. canonical V2 payload + cross-file agreement.
    cases = load_v2_cases(source_root)
    revision = dataset_revision(cases)

    # 3. exposure ledger (§7): unknown by default, exposed when the question
    #    also exists in the historical Pro v1 payload.
    historical_pool, historical_by_hash = load_historical_exposure(source_root)
    historical_revision = ""
    if historical_pool:
        blob = json.dumps([{"uid": entry["uid"], "question_sha256": entry["question_sha256"]}
                           for entry in historical_pool], ensure_ascii=False, sort_keys=True)
        historical_revision = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    ledger_cases = []
    for case in cases:
        digest = question_sha256(case.question)
        exposed = digest in historical_by_hash
        ledger_cases.append({
            "case_key": case_key(revision, case.uid, digest),
            "uid": case.uid,
            "question_sha256": digest,
            "exposure": "exposed" if exposed else "unknown",
            "basis": (f"question also in {HISTORICAL_DATASET} uid {historical_by_hash[digest]}"
                      if exposed else "no prior use recorded"),
        })
    ledger = {
        "schema_version": SCHEMA_LEDGER,
        "dataset": DATASET,
        "revision": revision,
        "created": _now(),
        "generated_by": "prepare_dataset.py",
        "note": "unknown 不等于 unseen（协议 §7）；历史 Pro 题按题目哈希标已暴露。",
        "cases": ledger_cases,
        "historical_exposed_pool": [
            {**entry, "exposure": "exposed",
             "basis": "historical OfficeQA Pro question used in prior experiments"}
            for entry in historical_pool],
        **({"historical_revision": historical_revision} if historical_revision else {}),
    }
    (data_root / "question_only" / "exposure_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 4. sanitized question jsonl — allow-listed fields only (§6.4).
    lines = []
    for case, entry in zip(cases, ledger_cases, strict=True):
        line = json.dumps({
            "schema_version": SCHEMA_QUESTION,
            "dataset": DATASET,
            "revision": revision,
            "uid": case.uid,
            "case_key": entry["case_key"],
            "question": case.question,
            "question_sha256": entry["question_sha256"],
        }, ensure_ascii=False)
        if set(json.loads(line)) != set(QUESTION_FIELDS):
            raise PrepareError("question serialization leaked a non-allow-listed field")
        # The only fields that carry question-adjacent text are uid/question
        # (plus constants); revision/case_key/question hashes are one-way
        # digests and coincidental hex substrings of a short gold carry no
        # information, so they are not scan carriers.
        carriers = f"{DATASET} {case.uid} {case.question}"
        for forbidden in (case.answer, *case.source_files):
            if forbidden and forbidden in carriers and forbidden not in case.question:
                raise PrepareError(f"sanitization gate failed for uid {case.uid!r}")
        lines.append(line)
    (data_root / "question_only" / "question_only.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    # 5. gold side-loads in evaluator-only (0700 dir / 0600 files); the episode
    #    runner and every solver path never open this directory.
    gold_dir = data_root / "evaluator-only" / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(gold_dir, 0o700)
    gold_lines = [
        json.dumps({"schema_version": SCHEMA_GOLD, "dataset": DATASET, "revision": revision,
                    "uid": case.uid, "question_sha256": entry["question_sha256"],
                    "case_key": entry["case_key"], "answer": case.answer,
                    "source_files": list(case.source_files), "source_docs": list(case.source_docs)},
                   ensure_ascii=False)
        for case, entry in zip(cases, ledger_cases, strict=True)
    ]
    _write_private(gold_dir / "officeqa_pro_v2.gold.jsonl", "\n".join(gold_lines) + "\n", mode=0o600)
    if historical_pool:
        historical_path = source_root / HISTORICAL_CSV
        historical_rows = _load_csv(historical_path, required={"uid", "question", "answer"})
        historical_gold = [
            json.dumps({"schema_version": SCHEMA_GOLD, "dataset": HISTORICAL_DATASET,
                        "revision": historical_revision, "uid": row["uid"].strip(),
                        "question_sha256": question_sha256(row["question"]),
                        "case_key": case_key(historical_revision, row["uid"].strip(),
                                             question_sha256(row["question"])),
                        "answer": (row.get("answer") or "").strip(),
                        "source_files": list(split_names(row.get("source_files") or "")),
                        "source_docs": list(split_names(row.get("source_docs") or ""))},
                       ensure_ascii=False)
            for row in historical_rows
        ]
        _write_private(gold_dir / "officeqa_pro_v1.gold.jsonl", "\n".join(historical_gold) + "\n",
                       mode=0o600)

    summary: dict[str, Any] = {
        "schema_version": "officeqa.prepare_summary.v1",
        "prepared_at": _now(),
        "dataset": DATASET,
        "revision": revision,
        "questions": len(cases),
        "exposure": {"unknown": sum(1 for entry in ledger_cases if entry["exposure"] == "unknown"),
                     "exposed": sum(1 for entry in ledger_cases if entry["exposure"] == "exposed"),
                     "historical_pool": len(historical_pool)},
        "gated_snapshots": gated_hashes,
        "data_root": str(data_root),
        "source_root": str(source_root),
        "outputs": {"question_only": str(data_root / "question_only" / "question_only.jsonl"),
                    "exposure_ledger": str(data_root / "question_only" / "exposure_ledger.json"),
                    "gold_v2": str(gold_dir / "officeqa_pro_v2.gold.jsonl"),
                    **({"gold_v1": str(gold_dir / "officeqa_pro_v1.gold.jsonl")} if historical_pool else {})},
    }

    # 6. corpus staging + query-blind index + answer-free probe (§6.1).
    if not skip_corpus:
        corpus_source = corpus_source or source_root / "fullcorpus"
        staging = corpus_adapter.stage_documents(corpus_source, data_root / "corpus")
        manifest = corpus_adapter.build_index(data_root / "corpus")
        report = corpus_adapter.probe(data_root / "corpus")
        summary["corpus"] = {"staging": staging, "manifest": manifest,
                             "probe_status": report["status"],
                             "probe_documents": report["documents"],
                             "probe_failures": len(report["failures"])}
        if report["status"] != "green":
            raise PrepareError(f"corpus visibility probe is not green: {report['failures'][:5]}")
    summary["seconds"] = round(time.monotonic() - started, 1)
    (data_root / "prepare_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT),
                        help="restricted local dataset root (read-only)")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--corpus-source", help="V2 fullcorpus directory (default <source-root>/fullcorpus)")
    parser.add_argument("--skip-corpus", action="store_true",
                        help="only rebuild the question/gold/ledger outputs")
    args = parser.parse_args(argv)
    try:
        summary = prepare(Path(args.source_root), Path(args.data_root),
                          corpus_source=Path(args.corpus_source) if args.corpus_source else None,
                          skip_corpus=args.skip_corpus)
    except (PrepareError, corpus_adapter.CorpusError) as exc:
        print(f"prepare_dataset error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
