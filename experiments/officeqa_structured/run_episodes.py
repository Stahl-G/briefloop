"""Two-arm episode runner for the OfficeQA structured-answer experiment.

Design-0223 §3.Q2-Q3 / protocol BL-OQA-SR-v1.0 §5, §8, §9.  Arm A is a native
agent control (same host/model/corpus tools plus a seriously configured
research prompt); arm B drives the real BriefLoop pipeline — the
``external_requests`` submit/query interface, ``result_format=
grounded_qa_v1``, the Worker's generate → independent review → one
revision job chain — and never writes SQLite directly to fake completion.

This module is data-clean by construction: cases are loaded from the
sanitized ``question_only/`` view only, the corpus comes from the staged
data-area copy, and nothing here opens ``gated/`` or ``evaluator-only/``.
Gold reaches the process only in the ``score`` step, after ``freeze`` has
sealed the predictions (protocol §8.3), and the scorer itself is the pinned
official reward.py behind ``score_answer_record``.

Protocol §8.1's concurrency cap is a run-wide ``ModelCallLimiter`` over
simultaneously active model calls — every transport call holds one slot;
episode scheduling (``--episode-workers``) is deliberately decoupled from
it.  Before any episode runs, ``isolation.audit_data_area`` re-verifies the
data-area permission bits and fails closed on drift.  Arm A submits through
a concrete submit directory drained into the ``SubmissionBox``; format
repair (protocol §4) is bounded per episode with gold-blind feedback the
agent — never this program — acts on.

Two execution modes.  ``run --dry-run`` is the zero-model-call path: stub
solvers over the real interfaces (tests live here).  Without ``--dry-run``
the Q3 real transports run — arm A as one ``opencode run`` process per turn
in the episode workspace (model/variant from the frozen config), arm B as
the real ``InteractiveRuntime`` over an ``OpencodeHarness`` — and the gate
is the frozen-config validation (protocol §11 Q10: config 无 null/TODO 才
允许开跑) plus the enforced OS-level solver boundary: every solver process
is launched through a macOS seatbelt shim that denies reads on ``gated/``
and ``evaluator-only/`` (design §2 — the boundary is the OS, not prompt
text), with dataset credentials stripped from the environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_adapter  # noqa: E402  (same experiment directory)
import isolation  # noqa: E402  (same experiment directory)
import prepare_dataset  # noqa: E402
import score_answer_record  # noqa: E402

EPISODE_SCHEMA = "officeqa.episode_record.v1"
PREDICTIONS_SCHEMA = "officeqa.predictions.v1"
SCORES_SCHEMA = "officeqa.scores.v1"
ARMS = ("A", "B")
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
CORPUS_TOOL_PATH = Path(__file__).resolve().parent / "corpus_adapter.py"
DEFAULT_DATA_ROOT = corpus_adapter.DEFAULT_DATA_ROOT
WORKTREE_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED_BUDGET_FIELDS = ("episode_wall_clock_seconds", "max_concurrent_model_calls",
                           "max_automatic_revisions_B", "format_repair_attempts_per_episode")
# Protocol §8.1 / product research_budget.KINDS: the shared search budget shape
# both arms get, expressed with the same tool (the corpus CLI) and same numbers.
SEARCH_BUDGET_KINDS = ("search_requests", "candidate_urls", "source_pages")
SANDBOX_EXEC = "/usr/bin/sandbox-exec"


class RunnerError(RuntimeError):
    """Episode running/freezing/scoring failed closed."""


class RealExecutionGateError(RunnerError):
    """Real model execution refused: the frozen-config/isolation gate failed."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_bytes(record: dict[str, Any]) -> bytes:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_private_bytes(path: Path, payload: bytes) -> None:
    """Write an evaluator-only artifact with owner-only bits (design §2)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


class ModelCallLimiter:
    """Protocol §8.1: a run-global cap on simultaneously *active model calls*.

    This is deliberately NOT episode parallelism: one episode may host several
    concurrent role calls (orchestrator, scouts, reviewer, revision) and several
    episodes may be in flight — every real transport call, in every arm, must
    hold one slot for its duration.  Dry-run stub transports take slots too, so
    the accounting path is exercised end to end without any model.  A future
    real transport that bypasses ``slot()`` is a protocol deviation: wire Q3
    transports through this limiter, and read ``report()`` into the run index.
    """

    def __init__(self, limit: int):
        if limit < 1:
            raise RunnerError(f"max_concurrent_model_calls must be >= 1, got {limit}")
        self.limit = limit
        self._semaphore = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0
        self.total = 0

    @contextmanager
    def slot(self) -> Iterator[None]:
        with self._semaphore:
            with self._lock:
                self._active += 1
                self.total += 1
                self.peak = max(self.peak, self._active)
            try:
                yield
            finally:
                with self._lock:
                    self._active -= 1

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {"limit": self.limit, "peak_concurrent": self.peak,
                    "total_calls": self.total}


# --- configuration and sanitized case view -------------------------------------


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    budget = config.get("budget") or {}
    missing = [field for field in _REQUIRED_BUDGET_FIELDS if not isinstance(budget.get(field), int)]
    if missing:
        raise RunnerError(f"config.json budget is missing integer fields: {missing}")
    for field in ("protocol_id", "experiment_id"):
        if not config.get(field):
            raise RunnerError(f"config.json is missing {field}")
    return config


@dataclass(frozen=True)
class Budget:
    wall_clock_seconds: int
    max_concurrent_model_calls: int
    max_automatic_revisions_B: int
    format_repair_attempts: int
    search_budget: dict[str, int]

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Budget":
        budget = config["budget"]
        search = budget.get("search_budget") or {}
        # Missing/unset search_budget reads as zero for dry-run plumbing; the
        # real-execution gate (config_freeze_errors) is what demands frozen,
        # positive, identical-for-both-arms numbers.
        resolved = {kind: search.get(kind) for kind in SEARCH_BUDGET_KINDS}
        if not all(isinstance(value, int) for value in resolved.values()):
            resolved = {kind: 0 for kind in SEARCH_BUDGET_KINDS}
        return cls(wall_clock_seconds=int(budget["episode_wall_clock_seconds"]),
                   max_concurrent_model_calls=int(budget["max_concurrent_model_calls"]),
                   max_automatic_revisions_B=int(budget["max_automatic_revisions_B"]),
                   format_repair_attempts=int(budget["format_repair_attempts_per_episode"]),
                   search_budget={kind: int(resolved[kind]) for kind in SEARCH_BUDGET_KINDS})


@dataclass(frozen=True)
class Case:
    case_key: str
    uid: str
    question: str
    question_sha256: str
    revision: str
    exposure: str
    dataset: str = prepare_dataset.DATASET


def load_cases(data_root: Path) -> list[Case]:
    """Load the sanitized question view; verify every case identity (§1.2).

    Opens only ``question_only/question_only.jsonl``, the optional
    ``question_only/dev_pilot.jsonl`` view (protocol §7: 开发 pilot 来自已
    暴露旧题) and the exposure ledger.  Recomputing the question hash and
    case key here proves the runner's view is the prepared one — and the
    runner has no path to any answer payload.
    """
    data_root = Path(data_root).expanduser().resolve()
    question_path = data_root / "question_only" / "question_only.jsonl"
    ledger_path = data_root / "question_only" / "exposure_ledger.json"
    if not question_path.is_file() or not ledger_path.is_file():
        raise RunnerError(f"prepared dataset is missing under {data_root}; run prepare_dataset.py first")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    exposure = {entry["case_key"]: entry["exposure"] for entry in ledger["cases"]}
    revision = ledger["revision"]
    dev_keys = set((ledger.get("dev_pilot") or {}).get("case_keys") or [])
    cases: list[Case] = []

    def _admit(row: dict[str, Any], *, expected_revision: str, expected_dataset: str,
               default_exposure: str) -> None:
        digest = prepare_dataset.question_sha256(row["question"])
        if digest != row["question_sha256"]:
            raise RunnerError(f"question hash mismatch for uid {row['uid']!r}")
        if prepare_dataset.case_key(expected_revision, row["uid"], digest) != row["case_key"]:
            raise RunnerError(f"case key mismatch for uid {row['uid']!r}")
        if row["revision"] != expected_revision or row["dataset"] != expected_dataset:
            raise RunnerError(f"revision/dataset mismatch for uid {row['uid']!r}")
        cases.append(Case(case_key=row["case_key"], uid=row["uid"], question=row["question"],
                          question_sha256=digest, revision=expected_revision,
                          exposure="dev" if row["case_key"] in dev_keys else default_exposure,
                          dataset=expected_dataset))

    for line in question_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            _admit(json.loads(line), expected_revision=revision,
                   expected_dataset=prepare_dataset.DATASET, default_exposure="unknown")
    dev_path = data_root / "question_only" / "dev_pilot.jsonl"
    if dev_path.is_file():
        historical_revision = ledger.get("historical_revision")
        if not historical_revision:
            raise RunnerError("dev_pilot.jsonl exists but the ledger has no historical_revision")
        for line in dev_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                _admit(json.loads(line), expected_revision=historical_revision,
                       expected_dataset=prepare_dataset.HISTORICAL_DATASET, default_exposure="exposed")
    if not cases:
        raise RunnerError("question_only.jsonl is empty")
    if dev_keys and dev_keys != {case.case_key for case in cases if case.exposure == "dev"}:
        raise RunnerError("ledger dev_pilot marks and the dev question view disagree")
    if not dev_keys and set(exposure) != {case.case_key for case in cases}:
        raise RunnerError("exposure ledger and question list disagree")
    return cases


# --- shared instructions (identical corpus surface for both arms, §6.1) --------

V2_CORPUS_NAME = "corpus"


def corpus_selection(config: dict[str, Any]) -> dict[str, str]:
    """Route each question dataset to its staged corpus directory (§6.1).

    The main test (officeqa-pro-v2) always reads the frozen V2 corpus; the
    dev pilot (historical officeqa-pro-v1, protocol §7) reads the v1
    plain-text corpus named by ``config.dataset.dev_corpus`` — its source
    documents (old Treasury Bulletins) are not part of the V2 release.
    An unmapped dataset fails closed at selection time, never silently
    falls back to the wrong corpus.
    """
    dev_name = str(((config.get("dataset") or {}).get("dev_corpus") or {}).get("name") or "")
    selection = {prepare_dataset.DATASET: V2_CORPUS_NAME}
    if dev_name:
        selection[prepare_dataset.HISTORICAL_DATASET] = dev_name
    return selection


def _corpus_manifest(data_root: Path, corpus_name: str) -> dict[str, Any]:
    manifest_path = (Path(data_root).expanduser().resolve() / corpus_name / "index" / "manifest.json")
    if not manifest_path.is_file():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _corpus_identity(data_root: Path, corpus_name: str) -> dict[str, Any]:
    """The corpus an episode actually used, as recorded in its §3.3 record."""
    manifest = _corpus_manifest(data_root, corpus_name)
    return {"name": corpus_name, "format": manifest.get("format", "v2"),
            "documents": manifest.get("documents")}


STRICT_CORPUS_NAME = "corpus-v2-pdf"
REGISTER_HELPER = Path(__file__).with_name("register_pdf.py")


def strict_pdf_instructions(pdf_dir: Path) -> str:
    """The strict official-condition surface — identical text for both arms.

    Raw PDF originals, no parsed text, no index, no document list: extraction,
    table parsing and search are the agent's own work with its own tools
    (bash, pdftotext/pdfinfo, python3, web search).  No per-call metering;
    the wall clock is the only search budget.  The single deliberate arm
    asymmetry stays inside this shared text, exactly like the parsed-mode
    accept note: the BriefLoop pipeline must register relied-on PDFs.
    """
    pdfs = str(Path(pdf_dir).expanduser().resolve())
    reg = str(REGISTER_HELPER)
    return f"""## Evidence corpus (strict condition)

You are working against a local read-only directory of PDF originals: `{pdfs}`
This directory is the whole evidence universe — official document scans. There is no parsed text, no index and no document list; locating, extracting and reading the documents (tables, charts, footnotes) is your job, with your own tools: bash (`pdftotext` and `pdfinfo` are installed, `python3` is available), normal file search, and web search for supplementary public information. There is no search metering — manage your own time against the wall clock.

If you are running inside the BriefLoop pipeline, every PDF your answer relies on must be registered before it can carry evidence: `python3 {reg} <pdf-path> --workspace <episode workspace>`"""


def corpus_tool_instructions(data_root: Path, *, corpus_name: str = V2_CORPUS_NAME,
                             web_entry: str | None = None,
                             search_budget: dict[str, int] | None = None,
                             budget_file: Path | None = None) -> str:
    """The one corpus surface both arms see, generated for the selected corpus.

    No document list, no year hints, no file names — the agent must locate
    evidence itself (protocol §6.1).  B registers documents it actually uses
    through ``accept``; A reads without registration.  ``corpus_name`` picks
    the staged corpus the episode's question set routes to (V2 main test =
    ``corpus``; dev pilot = the v1 plain-text corpus), and every emitted
    command carries that ``--corpus`` path so the instruction text can never
    point one arm at the wrong corpus.  The v1 format has no page parse
    (page semantics not_applicable), so its instructions name read-only line
    ranges instead of promising a page view that does not exist.

    The controlled web entry (protocol §6.3) is claimed ONLY when the run
    configuration actually names one (``config.json web_search.entrypoint``,
    frozen at Q3).  With no entry wired, the instructions say so explicitly
    instead of promising a capability the runner does not provide.

    ``search_budget``/``budget_file`` (protocol §8.1) wire the identical
    shared search budget into the tool invocations themselves: the CLI
    refuses past the cap for either arm, and the journal doubles as the
    episode's usage record.
    """
    manifest = _corpus_manifest(data_root, corpus_name)
    documents = str(manifest.get("documents") or "全量")
    fmt = str(manifest.get("format") or "v2")
    tool = str(CORPUS_TOOL_PATH)
    root = str(Path(data_root).expanduser().resolve())
    web_line = (f"每组还有同一受控联网搜索入口可用于补充公开资料：{web_entry}；"
                "只读取原始发布者正文并记录全部请求；禁止浏览基准答案键、公开解题文章或任何评测输出。"
                if web_entry else
                "本次运行未接入联网搜索入口；全部资料只能来自上述本地只读语料，不要假定可以联网检索。")
    search_flags = page_flags = ""
    budget_line = ""
    if budget_file is not None and search_budget:
        search_flags = (f" --budget-file {Path(budget_file)} --search-budget "
                        f"{search_budget.get('search_requests', 0)}")
        budget_line = (f"\n检索本 episode 预算（检索 {search_budget.get('search_requests', 0)} 次，两组同额同工具）；"
                       "超出后命令会拒绝执行——保留已获取的证据继续作答，不要重试被拒的操作。")
        if fmt != "v1":
            page_flags = (f" --budget-file {Path(budget_file)} --page-budget "
                          f"{search_budget.get('source_pages', 0)}")
            budget_line = (f"\n检索与按页查看共享本 episode 预算（检索 {search_budget.get('search_requests', 0)} 次、"
                           f"按页 {search_budget.get('source_pages', 0)} 次，两组同额同工具）；"
                           "超出后命令会拒绝执行——保留已获取的证据继续作答，不要重试被拒的操作。")
    if fmt == "v1":
        corpus_line = (f"可用资料：一套本地只读语料（美国财政部公报 v1 全量纯文本，{documents} 份整档文档；"
                       "不提供文件清单，也不给任何题目相关的文件提示）。")
        commands = f"""- 查找文档：python3 {tool} docs --data-root {root} --corpus {corpus_name} --query 关键词子串
- 关键词检索：python3 {tool} search --data-root {root} --corpus {corpus_name} --query "关键词" [--doc 文档名] [--limit 20]{search_flags}
- 片段读取（1-based 行号）：python3 {tool} read --data-root {root} --corpus {corpus_name} --doc 文档名 --start-line N --end-line M"""
        usage_line = ("这套语料是纯文本整档（无分页解析，page 语义 not_applicable），不提供按页查看命令；"
                      "行号即文件行号，检索定位后用片段读取按行号范围读取，表格行内容较长时缩小行号范围。")
    else:
        corpus_line = (f"可用资料：一套本地只读语料（美国财政部公报全量解析，{documents} 份文档；"
                       "不提供文件清单，也不给任何题目相关的文件提示）。")
        commands = f"""- 查找文档：python3 {tool} docs --data-root {root} --corpus {corpus_name} --query 关键词子串
- 关键词检索：python3 {tool} search --data-root {root} --corpus {corpus_name} --query "关键词" [--doc 文档名] [--limit 20]{search_flags}
- 片段读取（1-based 行号）：python3 {tool} read --data-root {root} --corpus {corpus_name} --doc 文档名 --start-line N --end-line M
- 按页查看（0-based page_index）：python3 {tool} page --data-root {root} --corpus {corpus_name} --doc 文档名 --page-index P{page_flags}"""
        usage_line = ("检索词按文档年代、主题、指标与表头术语组合；同一文档先检索定位行号，再片段读取或按页查看；"
                      "表格元素内容较长时会截断，需要完整内容时缩小行号范围。")
    return f"""{corpus_line}
统一用以下命令操作语料（Python 已就绪，直接运行；输出为 JSON）：
{commands}
{usage_line}{budget_line}
{web_line}"""


def native_frame(submit_dir: Path, web_entry: str | None, pdf_dir: Path | None = None) -> str:
    """Arm-A frame (protocol §5.1): names the concrete submit endpoint.

    The submit directory is the program acceptance endpoint (protocol §8.3):
    the runner creates it, watches it, and accepts/timestamps/hashes every
    answer file the agent writes there.  The web clause matches what the
    runner actually wired — no promised entry when none is configured.
    """
    submit = str(Path(submit_dir).expanduser().resolve())
    if pdf_dir is not None:
        # Strict official condition: raw PDFs + the agent's own tools + web.
        intro = ("You are answering one OfficeQA question against a local directory of "
                 "official PDF originals — no parsed text, no index. Extraction and search "
                 "are yours to do with your own tools (bash, pdftotext/pdfinfo, python3, "
                 "web search). No per-call search metering; the wall clock is the budget.")
    else:
        intro = "You are answering one OfficeQA question against a local read-only corpus of parsed U.S. Treasury publications."
    web_clause = ("You have the corpus tools and the controlled web entry described below; "
                  "no other assistance."
                  if web_entry else
                  "You have the corpus tools described below and no other assistance; "
                  "this run wires no web access.")
    return f"""{intro}

Work like a professional archivist: narrow by document era and family first (combined statements, govinfo receipts, appendix tables), then search entity and measure terms including period naming variants, then read the relevant tables closely — check headers, footnotes, units and the exact fiscal period before extracting operands. Multi-step arithmetic is allowed after extracting exact values.

This is a research task with self-checks: verify the answer against the located table, re-derive any computation, and confirm units and rounding follow the question. {web_clause}

Submit the final answer as a single UTF-8 JSON file `answer.json` written into the submit directory ({submit}), exactly:
{{"schema_version": "officeqa.answer.v1", "status": "answered", "answer": "<single-line direct answer>"}}
The answer keeps the direct form the question asks for (number, date, text, or a list string in the question's order such as "[North, 0.866]"). No explanations, units the question did not ask for, citation markers, confidence or alternatives. One line, at most 250 characters after trimming. If you genuinely cannot determine the answer, submit {{"schema_version": "officeqa.answer.v1", "status": "abstained", "answer": null}}. The submitting program accepts, timestamps and hashes every answer file in that directory; only your latest accepted submission before the deadline counts.
"""


# --- program acceptance endpoint (protocol §8.3) -------------------------------


class SubmissionBox:
    """Program-side submission acceptance: validate, sequence, hash, timestamp.

    Contract-valid submissions are accepted with the next sequence number and
    their raw bytes preserved; format-invalid payloads (and anything arriving
    after the deadline) are kept as attempts without acceptance, so scoring
    falls back to the previous accepted answer exactly as §8.3 requires.

    Format repair (protocol §4): ``offer_format_repair`` turns one rejected
    invalid submission into gold-blind feedback for the *agent* to resubmit
    — at most ``repair_budget`` times per episode, never authored by this
    program.  ``drain_submit_directory`` is the file endpoint arm A's frame
    points at: every answer file sitting in the submit directory is
    accepted, timestamped and hashed.
    """

    def __init__(self, episode_dir: Path, deadline: float, *, repair_budget: int = 0):
        self.episode_dir = Path(episode_dir)
        self.submissions = self.episode_dir / "submissions"
        self.attempts = self.episode_dir / "attempts"
        self.submissions.mkdir(parents=True, exist_ok=True)
        self.attempts.mkdir(parents=True, exist_ok=True)
        self.deadline = deadline
        self.repair_budget = int(repair_budget)
        self.repairs_used = 0
        self._lock = threading.Lock()

    def accept(self, payload: bytes, *, source: str) -> dict[str, Any]:
        projection = score_answer_record.project_answer(payload)
        entry = {"source": source, "received_at": _now(),
                 "sha256": _sha256_bytes(payload), "bytes": len(payload),
                 "status": projection.status, "reason": projection.reason}
        with self._lock:
            accepted_entries = self.entries(accepted_only=True)
            if time.time() > self.deadline:
                entry.update({"accepted": False, "reason": f"after deadline: {entry['reason'] or 'valid'}"})
                target = self.attempts / f"{len(self.entries()) + 1:04d}-answer.json"
                target.write_bytes(payload)
            elif projection.status in ("answered", "abstained"):
                entry.update({"accepted": True, "seq": len(accepted_entries) + 1})
                (self.submissions / f"{entry['seq']:04d}-answer.json").write_bytes(payload)
            else:
                entry["accepted"] = False
                target = self.attempts / f"{len(self.entries()) + 1:04d}-answer.json"
                target.write_bytes(payload)
            with (self.episode_dir / "submissions" / "index.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def offer_format_repair(self, entry: dict[str, Any]) -> str | None:
        """Protocol §4: bounded, gold-blind format repair for the agent.

        Conditions: the entry is a pre-deadline format-invalid submission and
        the episode still has repair budget.  The returned text carries only
        the recorded projection reason plus the answer contract — the agent
        (never this program) authors the resubmission.  Issuances are
        appended to the submission index with ``source="format-repair"``.
        """
        if entry.get("accepted") or entry.get("status") != "invalid":
            return None
        if time.time() > self.deadline:
            return None
        with self._lock:
            if self.repairs_used >= self.repair_budget:
                return None
            self.repairs_used += 1
            repair_seq = self.repairs_used
            record = {"source": "format-repair", "accepted": False,
                      "repair_seq": repair_seq, "issued_at": _now(),
                      "for_sha256": entry.get("sha256"), "reason": entry.get("reason")}
            with (self.submissions / "index.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return (f"格式修复（第 {repair_seq}/{self.repair_budget} 次，整次 episode 共用）："
                f"上一次提交未被接纳，原因：{entry.get('reason')}。"
                "请由你自己重新提交一份符合 answer.json 契约的答案文件（单行 answer 字符串、"
                "无解释/单位/引用标记、不得超过 250 字符；确实无法确定则提交 abstained）。"
                "控制程序不会替你改正内容。")

    def drain_submit_directory(self, submit_dir: Path) -> list[dict[str, Any]]:
        """Accept every answer file currently sitting in the submit directory.

        Files are processed in (mtime, name) order; a file whose exact bytes
        were already recorded does not count again, so an agent overwriting
        ``answer.json`` with new bytes is a new submission (§8.3: the latest
        accepted submission before the deadline is the scored one).
        """
        submit_dir = Path(submit_dir)
        if not submit_dir.is_dir():
            return []
        with self._lock:
            known = {row.get("sha256") for row in self.entries()}
        accepted_now: list[dict[str, Any]] = []
        for path in sorted(submit_dir.glob("*.json"), key=lambda item: (item.stat().st_mtime, item.name)):
            payload = path.read_bytes()
            digest = _sha256_bytes(payload)
            if digest in known:
                continue
            entry = self.accept(payload, source=f"submit-dir:{path.name}")
            known.add(digest)
            accepted_now.append(entry)
        return accepted_now

    def entries(self, *, accepted_only: bool = False) -> list[dict[str, Any]]:
        index = self.submissions / "index.jsonl"
        if not index.is_file():
            return []
        rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [row for row in rows if row.get("accepted")] if accepted_only else rows

    def chosen(self) -> dict[str, Any] | None:
        """Latest accepted submission before the deadline — the scored one."""
        accepted = self.entries(accepted_only=True)
        return accepted[-1] if accepted else None

    def chosen_payload(self) -> bytes | None:
        chosen = self.chosen()
        if chosen is None:
            return None
        return (self.submissions / f"{chosen['seq']:04d}-answer.json").read_bytes()


# --- real-execution context: OS solver boundary + shared transports ------------


def _git(args: list[str]) -> str:
    result = subprocess.run(["git", "-C", str(WORKTREE_ROOT), *args], capture_output=True,
                            text=True, timeout=30)
    if result.returncode != 0:
        raise RunnerError(f"git {' '.join(args)} failed: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def code_state_matches(integration_commit: str) -> dict[str, Any]:
    """Real runs execute the frozen code: src/ and experiments/ must be
    byte-identical to ``baseline.integration_commit`` — except the freeze
    record itself (config.json / RUNBOOK.md), which is exactly what moves in
    the freeze commit.  Returns a report; ``matches`` False fails closed.
    """
    checked = ["src", "experiments",
               ":!experiments/officeqa_structured/config.json",
               ":!experiments/officeqa_structured/RUNBOOK.md"]
    try:
        head = _git(["rev-parse", "HEAD"])
        diff = subprocess.run(["git", "-C", str(WORKTREE_ROOT), "diff", "--quiet",
                               integration_commit, "--", *checked],
                              capture_output=True, text=True, timeout=60)
        return {"matches": diff.returncode == 0, "head": head, "integration_commit": integration_commit,
                "checked_paths": checked}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"matches": False, "error": f"{type(exc).__name__}: {exc}",
                "integration_commit": integration_commit, "checked_paths": checked}


def prepare_solver_boundary(data_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Materialize the OS-level solver boundary (design §2 / protocol §6.4).

    macOS seatbelt profile denying reads on ``gated/`` and ``evaluator-only/``
    (plus reads/writes on the original dataset directory and writes on the
    staged corpus), plus an ``opencode`` shim so every solver process — arm
    A's ``opencode run`` and arm B's managed ``opencode serve`` — executes
    inside it.  A live probe proves the fence actually bites before any
    episode starts; the canonical (``/private``-resolved) paths matter,
    seatbelt does not match through the ``/tmp`` symlink.
    """
    boundary = (config.get("isolation") or {}).get("solver_boundary") or {}
    if boundary.get("kind") != "sandbox":
        raise RunnerError("real runs need isolation.solver_boundary.kind=sandbox in this environment")
    data_root = Path(data_root).expanduser().resolve()
    area = data_root / "solver-boundary"
    profile_path = area / "solver.sb"
    shim_dir = area / "bin"
    real_binary = shutil.which("opencode") or str(Path.home() / ".opencode" / "bin" / "opencode")
    if not Path(real_binary).is_file():
        raise RunnerError(f"opencode CLI not found at {real_binary}")
    # (action, path): deny-read fences confidentiality (gated snapshot,
    # evaluator outputs, the original dataset directories); deny-write protects
    # the staged corpora — their files are hardlinks, an edit would corrupt the
    # dataset itself.  Corpus READS stay allowed: that is the shared surface.
    # The dev-pilot v1 corpus gets the same fences when it is staged/configured,
    # so a routed dev episode is fenced exactly like a main-test one.
    strict_manifest = data_root / STRICT_CORPUS_NAME / "index" / "manifest.json"
    strict_gates = ([("deny-write", "strict pdf corpus", data_root / STRICT_CORPUS_NAME)]
                    if strict_manifest.is_file() else [])
    gates = strict_gates + [
        ("deny-read", "gated", data_root / "gated"),
        ("deny-read", "evaluator-only", data_root / "evaluator-only"),
        ("deny-read", "dataset source", Path(config["dataset"]["source_root"]).expanduser().resolve()),
        ("deny-write", "staged corpus", data_root / "corpus"),
    ]
    dev_corpus = ((config.get("dataset") or {}).get("dev_corpus") or {})
    if dev_corpus.get("name"):
        dev_staged = data_root / str(dev_corpus["name"])
        if dev_staged.exists():
            gates.append(("deny-write", "staged dev corpus", dev_staged))
    if dev_corpus.get("source_root"):
        dev_source = Path(str(dev_corpus["source_root"])).expanduser().resolve()
        if dev_source.exists():
            gates.append(("deny-read", "dev dataset source", dev_source))
    rules = []
    for action, label, path in gates:
        if not path.exists():
            raise RunnerError(f"solver boundary gate path is missing ({label}): {path}")
        verbs = ("file-read*",) if action == "deny-read" else ("file-write*",)
        for verb in verbs:
            rules.append(f"(deny {verb} (subpath \"{path}\"))  ; {action}: {label}")
    profile = "(version 1)\n(allow default)\n" + "\n".join(rules) + "\n"
    area.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(profile, encoding="utf-8")
    os.chmod(profile_path, 0o644)
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / "opencode"
    shim.write_text("#!/bin/sh\n"
                    f"exec {SANDBOX_EXEC} -f {profile_path} -- {real_binary} \"$@\"\n",
                    encoding="utf-8")
    os.chmod(shim, 0o755)
    # Live probe (before any episode): the fence must deny a gated read and
    # still allow an ordinary one.  Refuse to start real episodes otherwise.
    probe = {"profile": str(profile_path), "shim": str(shim), "real_binary": real_binary,
             "rules": [rule.split(";", 1)[0].strip() for rule in rules]}
    for label, path in (("gated", data_root / "gated"), ("evaluator-only", data_root / "evaluator-only")):
        member = sorted(path.glob("*"))[0] if sorted(path.glob("*")) else None
        if member is None:
            continue
        denied = subprocess.run([SANDBOX_EXEC, "-f", str(profile_path), "/bin/cat", str(member)],
                                capture_output=True, timeout=30)
        probe[f"probe:{label}"] = {"path": str(member), "denied": denied.returncode != 0}
        if denied.returncode == 0:
            raise RunnerError(f"solver boundary probe failed: {label} stayed readable under the sandbox")
    allowed = subprocess.run([SANDBOX_EXEC, "-f", str(profile_path), "/bin/cat", str(profile_path)],
                             capture_output=True, timeout=30)
    probe["probe:profile-readable"] = {"denied": allowed.returncode != 0}
    if allowed.returncode != 0:
        raise RunnerError("solver boundary probe failed: the sandbox denied its own profile")
    # B's managed serve resolves `opencode` through PATH (host_bins.find);
    # prepending the shim dir keeps that spawn inside the boundary too.
    os.environ["PATH"] = f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    return probe


@dataclass
class RealContext:
    """Everything the real transports need, gated and prepared once per run."""
    model: str
    variant: str | None
    host_version: str
    shim: Path
    solver_env: dict[str, str]

    @classmethod
    def build(cls, config: dict[str, Any], boundary_probe: dict[str, Any]) -> "RealContext":
        runtime = config["runtime"]
        from briefloop.host_bins import find as find_host_bin
        real_binary = boundary_probe["real_binary"]
        resolved = find_host_bin("opencode") or real_binary
        version = subprocess.run([real_binary, "--version"], capture_output=True, text=True, timeout=30)
        host_version = (version.stdout or version.stderr).strip().splitlines()[0][:120]
        return cls(model=runtime["model"], variant=runtime.get("reasoning_effort"),
                   host_version=host_version, shim=Path(boundary_probe["shim"]),
                   solver_env=isolation.solver_environment())


class OpencodeRunSolver:
    """Arm-A real transport (protocol §5.1): ``opencode run`` in the workspace.

    One process per turn holds one §8.1 slot for its whole duration; the
    episode deadline kills the process group (the CLI boots its own server
    tree).  The first turn carries NATIVE_FRAME; a format-repair turn
    continues the same session with gold-blind feedback (protocol §4).
    """

    def __init__(self, real: RealContext, workspace: Path, limiter: "ModelCallLimiter"):
        self.real = real
        self.workspace = Path(workspace)
        self.limiter = limiter
        self.turns: list[dict[str, Any]] = []

    def _write_host_config(self, episode_dir: Path, *, web: bool = False,
                           read_dirs: tuple[Path, ...] = ()) -> None:
        """Workspace opencode.json: the non-interactive permission surface.

        Bash and workspace edits allowed (the corpus tool and the answer file
        need them); question/web denied unless the run's frozen condition
        wires web (strict mode: the official harness condition); file tools
        scoped to this episode directory plus the read-only corpus dirs.
        """
        canonical = lambda path: str(Path(path).resolve())
        external = {"*": "deny", canonical(episode_dir) + "/**": "allow"}
        for extra in read_dirs:
            external[canonical(extra) + "/**"] = "allow"
        config = {
            "$schema": "https://opencode.ai/config.json",
            "permission": {
                "question": "deny",
                "webfetch": "allow" if web else "deny",
                "websearch": "allow" if web else "deny",
                "bash": "allow",
                "edit": "allow",
                "external_directory": external,
            },
        }
        (self.workspace / "opencode.json").write_text(json.dumps(config, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")

    def turn(self, prompt: str, *, title: str, deadline: float,
             continue_last: bool = False, tag: str = "main") -> dict[str, Any]:
        argv = [str(self.real.shim), "run", "--dir", str(self.workspace),
                "--model", self.real.model, "--title", title]
        if self.real.variant:
            argv += ["--variant", self.real.variant]
        if continue_last:
            argv += ["--continue"]
        argv.append(prompt)
        stdout_path = self.workspace / f"opencode-{tag}.stdout"
        stderr_path = self.workspace / f"opencode-{tag}.stderr"
        record: dict[str, Any] = {"tag": tag, "argv": [str(self.real.shim), "run", "--dir", str(self.workspace),
                                                       "--model", self.real.model]
                                  + (["--variant", self.real.variant] if self.real.variant else [])
                                  + (["--continue"] if continue_last else []),
                                  "stdout": str(stdout_path), "stderr": str(stderr_path)}
        started = time.time()
        with self.limiter.slot():
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                process = subprocess.Popen(argv, cwd=str(self.workspace), env=self.real.solver_env,
                                            stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                            start_new_session=True)
                try:
                    record["returncode"] = process.wait(timeout=max(1.0, deadline - time.time()))
                    record["deadline_hit"] = False
                except subprocess.TimeoutExpired:
                    record["deadline_hit"] = True
                    self._kill_tree(process)
                    record["returncode"] = process.poll()
        record["seconds"] = round(time.time() - started, 1)
        self.turns.append(record)
        return record

    @staticmethod
    def _kill_tree(process: subprocess.Popen) -> None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except OSError:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                process.kill()
            process.wait(timeout=10)


class SlotAccountedRuntime:
    """Wrap the real InteractiveRuntime so every model turn holds one §8.1 slot.

    ``execute`` is the single entry the Worker uses for every model turn
    (generate / review / revision); wrapping it keeps the run-wide cap honest
    without touching product code.  Everything else delegates.
    """

    def __init__(self, inner: Any, limiter: "ModelCallLimiter"):
        self._inner = inner
        self._limiter = limiter

    def execute(self, job: dict[str, Any], prompt: str, folder: str,
                on_tick: Callable[[], None] = lambda: None, **kwargs: Any) -> dict[str, Any]:
        with self._limiter.slot():
            return self._inner.execute(job, prompt, folder, on_tick=on_tick, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def real_briefloop_runtime(store: Any, limiter: "ModelCallLimiter") -> SlotAccountedRuntime:
    """The real B transport: InteractiveRuntime over a managed OpencodeHarness.

    The workspace settings freeze ``agent_backend=opencode`` with the frozen
    model/variant, so every job payload carries the same runtime identity
    (store.enqueue freezes it at admission); the harness boots the managed
    ``opencode serve`` through the sandbox shim resolved via PATH.
    """
    from briefloop.interactive_runtime import InteractiveRuntime
    from briefloop.opencode_harness import OpencodeHarness

    harness = OpencodeHarness(store)
    inner = InteractiveRuntime(store, backends={"opencode": harness})
    return SlotAccountedRuntime(inner, limiter)


# --- arm A: native control (stub transport in dry-run) -------------------------


def _search_terms(question: str) -> list[str]:
    """Question-derived search candidates, longest first (never gold)."""
    words = [word.strip(".,;:()'\"").lower() for word in question.split()]
    words = [word for word in words if len(word) >= 6 and word.isalpha()]
    if not words:
        words = [word for word in question.split() if len(word) >= 4]
    return sorted(set(words), key=lambda word: -len(word))[:3] or ["treasury"]


def _first_hit(adapter: corpus_adapter.CorpusAdapter, question: str, tool_calls: list[dict[str, Any]],
               *, limit: int) -> dict[str, Any] | None:
    """Try the candidate terms in order; log every search (real agents retry too)."""
    for term in _search_terms(question):
        hits = adapter.search(term, limit=limit)
        tool_calls.append({"op": "search", "args": {"query": term}, "results": len(hits)})
        if hits:
            return hits[0]
    return None


class StubNativeSolver:
    """Deterministic A-arm stub: exercises the same corpus surface, no model.

    Searches the corpus with a question-derived term, reads one snippet, and
    writes its answer file into the episode submit directory — first with a
    deliberately tag-injected payload so the dry-run exercises the §4
    format-repair loop (gold-blind feedback, agent resubmits), then a clean
    payload after feedback.  The term and every answer byte come from the
    question text and fixed synthetic strings — the stub never sees gold.

    A real Q3 native transport plays exactly this role against the same
    submit directory and feedback channel, holding one ModelCallLimiter slot
    per model call.
    """

    answer = "native-stub-answer"

    def _write_answer(self, submit_dir: Path, payload: dict[str, Any]) -> None:
        (Path(submit_dir) / "answer.json").write_bytes(_canonical_bytes(payload))

    def solve(self, case: Case, submit_dir: Path,
              adapter: corpus_adapter.CorpusAdapter, tool_calls: list[dict[str, Any]]) -> None:
        hit = _first_hit(adapter, case.question, tool_calls, limit=5)
        if hit:
            snippet = adapter.read(hit["name"], hit["line"], hit["line"] + 2)
            tool_calls.append({"op": "read", "args": {"doc": hit["name"], "line": hit["line"]},
                               "results": snippet["returned"]})
        # First attempt embeds a FINAL_ANSWER tag — a contract violation the
        # box rejects, so the repair loop below gets exercised in dry-run.
        self._write_answer(submit_dir, {"schema_version": score_answer_record.ANSWER_SCHEMA_VERSION,
                                        "status": "answered",
                                        "answer": f"<FINAL_ANSWER>{self.answer}</FINAL_ANSWER>"})

    def resubmit_after_format_feedback(self, submit_dir: Path, feedback: str,
                                        tool_calls: list[dict[str, Any]]) -> None:
        """The agent-side half of §4: fix the format itself, then rewrite."""
        tool_calls.append({"op": "format-repair-resubmit", "args": {"feedback_chars": len(feedback)}})
        self._write_answer(submit_dir, {"schema_version": score_answer_record.ANSWER_SCHEMA_VERSION,
                                        "status": "answered", "answer": self.answer})


def run_native_episode(case: Case, *, data_root: Path, budget: Budget, label: str,
                       order_index: int, limiter: "ModelCallLimiter",
                       corpus_name: str = V2_CORPUS_NAME,
                       web_entry: str | None = None,
                       real: RealContext | None = None, strict: bool = False) -> dict[str, Any]:
    episode_dir = Path(data_root) / "episodes" / label / "A" / case.case_key
    if episode_dir.exists():
        raise RunnerError(f"episode directory already exists: {episode_dir}")
    episode_dir.mkdir(parents=True)
    started = time.time()
    deadline = started + budget.wall_clock_seconds
    workspace = episode_dir / "workspace"
    workspace.mkdir()
    # The program acceptance endpoint (§8.3): created here, named in the frame,
    # drained into the SubmissionBox after the solver finishes.
    submit_dir = episode_dir / "submit"
    submit_dir.mkdir()
    budget_file = episode_dir / "corpus-budget.jsonl"
    (workspace / "question.md").write_text(f"# OfficeQA {case.uid}\n\n{case.question}\n", encoding="utf-8")
    strict_pdf_dir = Path(data_root) / STRICT_CORPUS_NAME / "documents" / "pdf" if strict else None
    if strict:
        surface = strict_pdf_instructions(strict_pdf_dir)
    else:
        surface = corpus_tool_instructions(data_root, corpus_name=corpus_name, web_entry=web_entry,
                                           search_budget=budget.search_budget if real else None,
                                           budget_file=budget_file if real else None)
    (workspace / "prompt.md").write_text(
        native_frame(submit_dir, web_entry, pdf_dir=strict_pdf_dir)
        + "\n## Question\n\n" + case.question + "\n\n" + surface + "\n",
        encoding="utf-8")
    tool_calls: list[dict[str, Any]] = []
    box = SubmissionBox(episode_dir, deadline, repair_budget=budget.format_repair_attempts)
    turns: list[dict[str, Any]] = []
    error: str | None = None
    if real is None:
        with corpus_adapter.CorpusAdapter(Path(data_root) / corpus_name) as adapter:
            solver = StubNativeSolver()
            solver.solve(case, submit_dir, adapter, tool_calls)
            # Format-repair loop (§4): drain the submit directory, hand gold-blind
            # feedback to the agent, let it rewrite; bounded by the episode budget.
            for _ in range(max(0, box.repair_budget) + 1):
                entries = box.drain_submit_directory(submit_dir)
                latest = entries[-1] if entries else None
                if latest is None or latest.get("accepted"):
                    break
                feedback = box.offer_format_repair(latest)
                if feedback is None:
                    break
                solver.resubmit_after_format_feedback(submit_dir, feedback, tool_calls)
            box.drain_submit_directory(submit_dir)
        identifiers = {"transport": "stub-native", "host": None, "model": None}
    else:
        solver = OpencodeRunSolver(real, workspace, limiter)
        solver._write_host_config(
            episode_dir, web=strict,
            read_dirs=((Path(data_root) / STRICT_CORPUS_NAME / "documents" / "pdf",) if strict else ()))
        prompt = (workspace / "prompt.md").read_text(encoding="utf-8")
        try:
            turns.append(solver.turn(prompt, title=f"OfficeQA {case.uid} · A", deadline=deadline))
            # Format-repair loop (§4) over the real transport: gold-blind feedback
            # is delivered as a continuation turn of the SAME session; the agent
            # (never this program) authors the resubmission.  Past the deadline
            # there is no repair — whatever is accepted so far is the answer.
            if time.time() <= deadline:
                for _ in range(max(0, box.repair_budget) + 1):
                    entries = box.drain_submit_directory(submit_dir)
                    latest = entries[-1] if entries else None
                    if latest is None or latest.get("accepted"):
                        break
                    feedback = box.offer_format_repair(latest)
                    if feedback is None or time.time() > deadline:
                        break
                    turns.append(solver.turn(feedback, title=f"OfficeQA {case.uid} · A · format-repair",
                                             deadline=deadline, continue_last=True, tag="format-repair"))
        except Exception as exc:  # noqa: BLE001 - a crashed transport still keeps
            # every accepted submission and lands in the denominator (§8.3).
            error = f"{type(exc).__name__}: {exc}"
        try:
            box.drain_submit_directory(submit_dir)
        except Exception as exc:  # noqa: BLE001
            error = (error + "; " if error else "") + f"drain failed: {exc}"
        identifiers = {"transport": "opencode-run", "host": real.host_version,
                       "model": real.model, "variant": real.variant,
                       "binary": str(real.shim)}
    record = _episode_record(case=case, arm="A", label=label, order_index=order_index,
                             episode_dir=episode_dir, started=started, deadline=deadline,
                             budget=budget, box=box, tool_calls=tool_calls,
                             identifiers=identifiers,
                             linkage={"turns": turns} if turns else None, usage_complete=False,
                             error=error, corpus=_corpus_identity(data_root, corpus_name))
    return record


# --- arm B: real BriefLoop interfaces with a stub transport --------------------


class StubBriefloopTransport:
    """Walks the real product turn structure without any model call.

    Generate turn: searches the shared corpus, registers the used document
    through the adapter's real acceptance path, writes answer.json plus an
    evidence attachment citing the registered source, and self-checks via the
    product's ``check_files``.  Review turn: files a complete review with one
    minor finding so the pipeline exercises its single automatic revision.
    Revision turn: submits a new answer version.  All answers are fixed
    synthetic strings — never derived from any gold.
    """

    initial_answer = "briefloop-stub-answer"
    revised_answer = "briefloop-stub-answer-revised"

    def __init__(self, store: Any, adapter: corpus_adapter.CorpusAdapter, limiter: "ModelCallLimiter"):
        self.store = store
        self.adapter = adapter
        self.limiter = limiter
        self.cancelled = threading.Event()
        self.calls: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self.cancelled.set()

    def _log(self, role: str, job: dict[str, Any]) -> None:
        self.calls.append({"role": role, "job_id": job["id"], "at": _now()})

    def execute(self, job: dict[str, Any], prompt: str, folder: str,
                on_tick: Callable[[], None] = lambda: None, **kwargs: Any) -> dict[str, Any]:
        # Every execute() stands in for one real model call, so it holds one
        # run-global §8.1 slot for its duration — the same contract a Q3 real
        # transport must satisfy (never bypass the limiter).
        with self.limiter.slot():
            return self._execute_locked(job, prompt, folder, on_tick=on_tick, **kwargs)

    def _execute_locked(self, job: dict[str, Any], prompt: str, folder: str,
                        on_tick: Callable[[], None] = lambda: None, **kwargs: Any) -> dict[str, Any]:
        target = Path(folder)
        with self._lock:
            if "独立只读 Reviewer" in prompt:
                self._log("reviewer", job)
                match = re.search(r"version_id=(brief_\w+?)，fingerprint=([0-9a-f]{64})", prompt)
                brief_hash = re.search(r"assessment\.brief_hash=([0-9a-f]{64})", prompt)
                if not match or not brief_hash:
                    raise RunnerError("stub reviewer could not bind the review packet identity")
                target.joinpath("review.json").write_text(json.dumps({
                    "fingerprint": match.group(2), "version_id": match.group(1), "status": "complete",
                    "summary": "Checked the frozen answer against the registered evidence",
                    "coverage_scan_complete": True,
                    "assessment": {"brief_hash": brief_hash.group(1), "status": "complete",
                                   "summary": "Answer located; a unit check on the cited line failed",
                                   "overall": "建议修改", "evidence": 4, "coverage": 4,
                                   "analysis": 2, "expression": 4,
                                   "findings": [{"dimension": "analysis", "severity": "major",
                                                 "description": "the unit on the cited line does not match "
                                                                "the question; submit a corrected answer",
                                                 "report_quote": ""}]}}, ensure_ascii=False), encoding="utf-8")
                return {}
            revision = target.name == "revision"
            self._log("revision" if revision else "orchestrator", job)
            payload = json.loads(job["payload"])
            run_id = payload.get("run_id") or self.store.one("briefs", payload["version_id"])["run_id"]
            requirements = json.loads(self.store.one("runs", run_id)["requirements"])
            answer = self.revised_answer if revision else self.initial_answer
            evidence = None
            if not revision:
                # The generate turn actually uses the corpus and registers the
                # document it relied on (protocol §6.1: B 接纳实际使用的来源).
                hit = _first_hit(self.adapter, requirements["objective"], self.tool_calls, limit=3)
                if hit:
                    accepted = self.adapter.accept(self.store, run_id, hit["name"])
                    snippet = self.adapter.read(hit["name"], hit["line"], hit["line"] + 1)
                    self.tool_calls.append({"op": "accept", "args": {"doc": hit["name"]},
                                            "source_id": accepted["source_id"]})
                    self.tool_calls.append({"op": "read", "args": {"doc": hit["name"],
                                                                  "line": hit["line"]},
                                            "results": snippet["returned"]})
                    excerpt = snippet["lines"][0]["content"][:200] if snippet["lines"] else ""
                    evidence = {"schema_version": "officeqa.evidence.v1",
                                "evidence": [{"source_id": accepted["source_id"],
                                              "locator": {"kind": "line_range",
                                                          "start": hit["line"],
                                                          "end": hit["line"] + 1},
                                              "excerpt": excerpt}],
                                "calculations": [], "limitations": ["stub transport: synthetic dry-run"]}
            answer_path = target / "answer.json"
            answer_path.write_bytes(_canonical_bytes(
                {"schema_version": score_answer_record.ANSWER_SCHEMA_VERSION,
                 "status": "answered", "answer": answer}))
            if evidence is not None:
                (target / "evidence_draft.json").write_bytes(_canonical_bytes(evidence))
            if revision:
                (target / "responses.json").write_text("[]", encoding="utf-8")
            from briefloop.answer_result import check_files
            report = check_files(self.store, str(answer_path),
                                 str(target / "evidence_draft.json") if evidence is not None else None,
                                 run_id=run_id)
            self.tool_calls.append({"op": "check-answer", "args": {"path": str(answer_path)},
                                    "status": report["status"]})
            if report["status"] != "ok":
                raise RunnerError(f"stub submission failed the product self-check: {report['errors']}")
        on_tick()
        return {}


def _episode_workspace_settings(store: Any, budget: Budget,
                                real: RealContext | None = None) -> None:
    """Design §4 closure list, enforced at the episode workspace level.

    Beyond the original four keys this pins the remaining closure items the
    design names: scheduled reports and notifications are asserted absent
    (fresh workspace, runner never registers any), the multi-channel search
    switch is pinned to a single disabled native channel, company context is
    off, and the report research tier never varies a QA episode.  The
    version-compare UI is a desktop-only surface — headless episode
    workspaces never expose it, which the config closure note records.

    ``max_reports`` is deliberately 1: it gates how many generate jobs one
    Worker may run at once inside a single episode, NOT the protocol §8.1
    model-call cap — that is the runner-wide ModelCallLimiter.

    With ``real`` (Q3), the frozen runtime identity is pinned here too:
    ``agent_backend=opencode`` plus the frozen model and variant, so
    ``store.enqueue`` freezes exactly that runtime into every job payload
    (protocol §5.1: 每个角色使用同一模型及推理档，冻结后不得换模型).
    """
    raw = store.meta("settings")
    raw.update({"auto_learn": False,
                "fact_checker": False,
                "auto_revision": budget.max_automatic_revisions_B > 0,
                "timeout_minutes": 0,
                "company_context_enabled": False,
                "research_tier": "standard",
                "search_policy": {"primary_provider": "native", "supplemental_providers": [],
                                  "native_search_enabled": False, "coverage_mode": "primary_only"},
                "max_reports": 1})
    if real is not None:
        raw.update({"agent_backend": "opencode", "model": real.model,
                    "model_variant": real.variant, "model_selection_required": False})
    store.set_meta("settings", raw)
    # 设计 §4：episode 内关闭定时任务及其他后台模型作业——独立、干净的
    # episode 工作区天然没有定时报告与通知；显式断言二者为空，一旦未来
    # 默认行为变化在这里立即失败，而不是静默带后台作业开跑。
    schedules = (store.rows("SELECT COUNT(*) AS n FROM report_schedules") or [{"n": 0}])[0]["n"]
    notifications = (store.rows("SELECT COUNT(*) AS n FROM notifications") or [{"n": 0}])[0]["n"]
    if schedules or notifications:
        raise RunnerError(f"episode workspace must start without background jobs "
                          f"(schedules={schedules}, notifications={notifications})")


def run_briefloop_episode(case: Case, *, data_root: Path, budget: Budget, label: str,
                          order_index: int, limiter: "ModelCallLimiter",
                          corpus_name: str = V2_CORPUS_NAME,
                          web_entry: str | None = None,
                          real: RealContext | None = None, strict: bool = False) -> dict[str, Any]:
    from briefloop.answer_result import RESULT_FORMAT, answer_of
    from briefloop.external_requests import dispatch
    from briefloop.runtime import Worker
    from briefloop.store import Store

    episode_dir = Path(data_root) / "episodes" / label / "B" / case.case_key
    if episode_dir.exists():
        raise RunnerError(f"episode directory already exists: {episode_dir}")
    episode_dir.mkdir(parents=True)
    started = time.time()
    deadline = started + budget.wall_clock_seconds
    workspace = episode_dir / "workspace"
    store = Store(workspace)
    _episode_workspace_settings(store, budget, real=real)
    # allow_web tells the truth about what is wired: no controlled entry
    # configured (Q3 freeze) means no web claim and no web permission.
    budget_file = episode_dir / "corpus-budget.jsonl"
    requirements = {"title": f"OfficeQA {case.uid}"[:200], "objective": case.question,
                    "key_questions": [case.question], "result_format": RESULT_FORMAT,
                    "allow_web": web_entry is not None, "fact_check": False,
                    "raw_input": (
                        strict_pdf_instructions(Path(data_root) / STRICT_CORPUS_NAME / "documents" / "pdf")
                        if strict else
                        corpus_tool_instructions(data_root, corpus_name=corpus_name,
                                                 web_entry=web_entry,
                                                 search_budget=budget.search_budget if real else None,
                                                 budget_file=budget_file if real else None))}
    if real is not None:
        # Protocol §8.1: the shared search budget rides the run requirements
        # through the product's own ResearchBudget fields (same KINDS shape).
        requirements["research_budget"] = budget.search_budget
    submitted = dispatch(store, {"workspace_id": store.meta("workspace_id"), "action": "submit",
                                 "request_id": f"oqa-{case.case_key[:16]}",
                                 "requirements": requirements, "source_ids": []})
    job_id, run_id = submitted["job_id"], submitted["run_id"]
    box = SubmissionBox(episode_dir, deadline, repair_budget=budget.format_repair_attempts if real else 0)
    tool_calls: list[dict[str, Any]] = []
    snapshots: set[str] = set()

    if real is None:
        with corpus_adapter.CorpusAdapter(Path(data_root) / corpus_name) as adapter:
            transport = StubBriefloopTransport(store, adapter, limiter)
            worker = Worker(store, runtime=transport, report_runtime_factory=lambda: transport)
            worker._review_runtime = transport
            worker.start()
            try:
                state = _drive_briefloop_episode(store, worker, box, job_id, run_id, deadline, snapshots)
            finally:
                worker.close()
            tool_calls.extend(transport.tool_calls)
            model_calls = list(transport.calls)
        identifiers = {"transport": "stub-briefloop", "host": None, "model": None}
        error = None
    else:
        transport = real_briefloop_runtime(store, limiter)
        worker = Worker(store, runtime=transport, report_runtime_factory=lambda: transport)
        worker._review_runtime = transport
        worker.start()
        state: dict[str, Any] = {}
        error: str | None = None
        try:
            state = _drive_briefloop_episode(store, worker, box, job_id, run_id, deadline, snapshots)
        except Exception as exc:  # noqa: BLE001 - keep every admitted version and
            # stay in the denominator; the failure rides the record (§8.3).
            error = f"{type(exc).__name__}: {exc}"
        finally:
            worker.close()
        # Honest post-hoc accounting at job granularity: every model turn the
        # product ran for this episode is one queued→terminal job row.  Token
        # totals stay best-effort (usage_complete=False) until per-role usage
        # is proven complete.
        jobs = store.rows(
            "SELECT id, kind, status, error FROM jobs WHERE json_extract(payload,'$.run_id')=? ORDER BY rowid",
            (run_id,))
        model_calls = [{"job_id": job["id"], "kind": job["kind"], "status": job["status"]}
                       for job in jobs]
        identifiers = {"transport": "briefloop-interactive-runtime+opencode", "host": real.host_version,
                       "model": real.model, "variant": real.variant}
    record = _episode_record(case=case, arm="B", label=label, order_index=order_index,
                             episode_dir=episode_dir, started=started, deadline=deadline,
                             budget=budget, box=box, tool_calls=tool_calls,
                             identifiers=identifiers,
                             linkage={"run_id": run_id, "job_id": job_id,
                                      "job_status": state.get("status"),
                                      "version_ids": sorted(snapshots),
                                      "model_calls": model_calls},
                             usage_complete=False, error=error,
                             corpus=_corpus_identity(data_root, corpus_name))
    return record


def _drive_briefloop_episode(store: Any, worker: Any, box: SubmissionBox, job_id: str,
                             run_id: str, deadline: float, snapshots: set[str]) -> dict[str, Any]:
    """Poll the real external_requests query surface until terminal or deadline.

    Every admitted brief version is projected through ``answer_of`` and
    submitted to the box (§8.3); format-invalid versions consume the shared
    repair budget with the same gold-blind feedback path arm A uses.
    """
    from briefloop.answer_result import answer_of
    from briefloop.external_requests import dispatch

    def admit_versions() -> None:
        for version in store.rows("SELECT id FROM briefs WHERE run_id=? ORDER BY rowid", (run_id,)):
            if version["id"] in snapshots:
                continue
            snapshots.add(version["id"])
            identity = answer_of(store, version["id"])
            if identity is None:
                continue
            entry = box.accept(_canonical_bytes(identity["answer"]), source="briefloop-version")
            entry["version_id"] = version["id"]
            if not entry["accepted"]:
                # §4 repair accounting for B as well: a format-invalid version
                # consumes the shared episode repair budget with the same
                # gold-blind feedback.  The dry-run stub always projects valid
                # answers, so this stays dormant there by construction; in a
                # real run the feedback is recorded here for the record while
                # the worker's own revision chain carries the repair turn.
                box.offer_format_repair(entry)

    state: dict[str, Any] = {}
    while True:
        state = dispatch(store, {"workspace_id": store.meta("workspace_id"),
                                 "action": "query", "job_id": job_id})
        admit_versions()
        if state.get("terminal"):
            break
        if time.time() > deadline:
            worker.stop_job(job_id)
            break
        time.sleep(0.05)
    # A revision admitted in the last instant still counts: sweep once more
    # after the job settled (protocol §8.3 keeps every accepted submission,
    # failures never erase them).
    admit_versions()
    return state


# --- episode record (protocol §3.3) --------------------------------------------


def _episode_record(*, case: Case, arm: str, label: str, order_index: int, episode_dir: Path,
                    started: float, deadline: float, budget: Budget, box: SubmissionBox,
                    tool_calls: list[dict[str, Any]], identifiers: dict[str, Any],
                    linkage: dict[str, Any] | None, usage_complete: bool,
                    error: str | None = None,
                    corpus: dict[str, Any] | None = None) -> dict[str, Any]:
    finished = time.time()
    chosen = box.chosen()
    corpus_budget_file = Path(episode_dir) / "corpus-budget.jsonl"
    record = {
        "schema_version": EPISODE_SCHEMA,
        "protocol_id": None,  # filled by the caller with config identity
        "experiment_id": None,
        "dataset": {"benchmark": case.dataset, "revision": case.revision},
        "corpus": corpus,
        "case_key": case.case_key,
        "uid": case.uid,
        "question_sha256": case.question_sha256,
        "exposure": case.exposure,
        "run_label": label,
        "arm": arm,
        "arm_order_index": order_index,
        "identifiers": identifiers,
        "started_at": datetime.fromtimestamp(started, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deadline_at": datetime.fromtimestamp(deadline, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at": datetime.fromtimestamp(finished, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "wall_clock_seconds": round(finished - started, 3),
        "budget": {"wall_clock_seconds": budget.wall_clock_seconds,
                   "max_concurrent_model_calls": budget.max_concurrent_model_calls,
                   "max_automatic_revisions_B": budget.max_automatic_revisions_B,
                   "format_repair_attempts": budget.format_repair_attempts,
                   "search_budget": dict(budget.search_budget)},
        "tool_calls": tool_calls,
        "corpus_budget_usage": (corpus_adapter.budget_usage(corpus_budget_file)
                                if corpus_budget_file.is_file() else {}),
        "usage_complete": usage_complete,
        "format_repairs_issued": box.repairs_used,
        "submissions": box.entries(),
        "chosen_submission": ({"seq": chosen["seq"], "sha256": chosen["sha256"],
                               "received_at": chosen["received_at"],
                               "status": chosen["status"]} if chosen else None),
        "workspace": str(episode_dir),
        "error": error,
        **({("briefloop" if arm == "B" else "native"): linkage} if linkage else {}),
    }
    (episode_dir / "episode_record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


# --- run / freeze / score commands ----------------------------------------------

_EPISODE_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {"A": run_native_episode,
                                                              "B": run_briefloop_episode}


def _walk_nulls(node: Any, path: str = "config") -> list[str]:
    """Every null/TODO leaf — the protocol §11 Q10 freeze condition."""
    found: list[str] = []
    if node is None:
        found.append(path)
    elif isinstance(node, str) and "TODO" in node:
        found.append(f"{path} (contains TODO)")
    elif isinstance(node, dict):
        for key, value in node.items():
            found.extend(_walk_nulls(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_walk_nulls(value, f"{path}[{index}]"))
    return found


def dev_pilot_coverage_errors(data_root: Path, config: dict[str, Any]) -> list[str]:
    """The 2026-09-15 lesson as a gate: dev questions must be provably
    answerable from the staged dev corpus before any real run.  Reads only
    the aggregate audit verdict the evaluator-side prepare wrote (booleans
    and counts, never document names), and fails closed when it is missing,
    stale against the frozen dev corpus, or not fully covered."""
    dataset = config.get("dataset") or {}
    dev = dataset.get("dev_corpus") or {}
    pilot = ((dataset.get("eligible_questions") or {}).get("dev_pilot") or {})
    expected_corpus = str(dev.get("name") or "corpus-v1")
    expected_cases = len(pilot.get("case_keys") or [])
    path = Path(data_root) / "audit" / "dev_pilot_coverage.json"
    if not path.is_file():
        return [f"开发题覆盖审计缺失：{path}；先运行 prepare 的 dev-pilot 生成覆盖裁决"]
    try:
        audit = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return [f"开发题覆盖审计不可读：{exc}"]
    errors = []
    if audit.get("corpus") != expected_corpus:
        errors.append(f"覆盖审计的语料({audit.get('corpus')})与冻结的 dev_corpus({expected_corpus})不一致")
    if expected_cases and audit.get("cases") != expected_cases:
        errors.append(f"覆盖审计的题数({audit.get('cases')})与 dev_pilot 清单({expected_cases})不一致")
    if audit.get("all_covered") is not True:
        errors.append("开发题源文档未全覆盖 staged 语料；真实执行拒绝点火（缺失清单在 evaluator-only）")
    return errors


def strict_real_gates(data_root: Path, config: dict[str, Any]) -> list[str]:
    """Fail-closed preconditions for strict-v2 real runs (user-directed 2026-09-15).

    Raw PDF corpus staged and non-empty, evaluator-side strict coverage audit
    all_covered, and the frozen strict_v2 block present and consistent."""
    errors: list[str] = []
    block = config.get("strict_v2") or {}
    if not block.get("corpus") or block.get("web") != "native" or block.get("metering") != "none":
        errors.append("config.strict_v2 冻结不完整（需 corpus/web=native/metering=none）")
    manifest_path = Path(data_root) / STRICT_CORPUS_NAME / "index" / "manifest.json"
    if not manifest_path.is_file():
        errors.append(f"{STRICT_CORPUS_NAME} 未 staged（先 build --format pdf）")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = ((block.get("corpus") or {}).get("documents")) or 0
        if manifest.get("format") != "pdf" or not manifest.get("documents"):
            errors.append("PDF 语料 manifest 异常")
        elif expected and manifest.get("documents") != expected:
            errors.append(f"PDF 语料份数({manifest.get('documents')})与冻结值({expected})不一致")
    audit_path = Path(data_root) / "audit" / "strict_coverage.json"
    if not audit_path.is_file():
        errors.append("strict 覆盖审计缺失：先运行 prepare 的 strict-coverage")
    else:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("all_covered") is not True:
            errors.append("strict 题集的源 PDF 未全覆盖；拒绝点火")
    return errors


def config_freeze_errors(config: dict[str, Any]) -> list[str]:
    """Protocol §10 Q3 / §11 Q10: the frozen-config validation behind the
    real-execution gate.  A real run may start only when this is empty —
    plus the isolation assertions and the live solver-boundary probe the
    runner performs on top of it.
    """
    errors = [f"null/TODO 字段：{path}" for path in _walk_nulls(config)]
    commit = ((config.get("baseline") or {}).get("integration_commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        errors.append("baseline.integration_commit 必须是 40 位十六进制提交 SHA")
    runtime = config.get("runtime") or {}
    if runtime.get("host") != "opencode":
        errors.append("runtime.host 必须是 opencode（本次接线的唯一真实宿主）")
    if "/" not in str(runtime.get("model") or ""):
        errors.append("runtime.model 必须是 provider/model 形式")
    if not str(runtime.get("reviewer_model") or "").strip():
        errors.append("runtime.reviewer_model 未冻结")
    if not str(runtime.get("reasoning_effort") or "").strip():
        errors.append("runtime.reasoning_effort 未冻结")
    search = (config.get("budget") or {}).get("search_budget")
    if not isinstance(search, dict) or not all(
            isinstance(search.get(kind), int) and search.get(kind, -1) >= 0
            for kind in SEARCH_BUDGET_KINDS):
        errors.append(f"budget.search_budget 必须含非负整数 {list(SEARCH_BUDGET_KINDS)}")
    if not isolation.boundary_report(config)["enforced"]:
        errors.append("isolation.solver_boundary 未配置为真实 OS 边界（dedicated-user 或 sandbox）")
    if not str((config.get("web_search") or {}).get("entrypoint") or "").strip():
        errors.append("web_search.entrypoint 未显式冻结（离线运行写 \"offline\"）")
    dataset = config.get("dataset") or {}
    if not re.fullmatch(r"[0-9a-f]{64}", str(dataset.get("revision") or "")):
        errors.append("dataset.revision 必须是 64 位十六进制数据修订哈希")
    eligible = dataset.get("eligible_questions")
    if not isinstance(eligible, dict) or not (eligible.get("dev_pilot") or {}).get("case_keys"):
        errors.append("dataset.eligible_questions.dev_pilot.case_keys 未冻结（开发 6 题）")
    if not dataset.get("exposure_ledger"):
        errors.append("dataset.exposure_ledger 未冻结")
    dev_corpus = dataset.get("dev_corpus")
    if dev_corpus is not None:
        # The dev-pilot corpus pointer: dev questions route to this staged v1
        # corpus, so its name/revision/size/source must be frozen like the
        # rest — a null or stub here would silently route dev episodes at a
        # missing or wrong corpus.
        if not isinstance(dev_corpus, dict):
            errors.append("dataset.dev_corpus 必须是对象（v1 语料指针）")
        else:
            if not str(dev_corpus.get("name") or "").strip():
                errors.append("dataset.dev_corpus.name 未冻结（开发题集路由的语料目录名）")
            if not str(dev_corpus.get("revision") or "").strip():
                errors.append("dataset.dev_corpus.revision 未冻结（v1 语料官方修订标识）")
            if not isinstance(dev_corpus.get("documents"), int) or dev_corpus.get("documents", 0) < 1:
                errors.append("dataset.dev_corpus.documents 必须是正整数（v1 语料份数）")
            if not str(dev_corpus.get("source_root") or "").strip():
                errors.append("dataset.dev_corpus.source_root 未冻结（v1 语料只读源目录）")
    return errors


def _select_cases(cases: list[Case], *, limit: int | None, case_keys: list[str],
                  pool: str | None) -> list[Case]:
    if case_keys:
        by_key = {case.case_key: case for case in cases}
        missing = [key for key in case_keys if key not in by_key]
        if missing:
            raise RunnerError(f"unknown case keys: {missing[:3]}")
        return [by_key[key] for key in case_keys]
    selected = cases
    if pool is not None:
        if pool not in ("unknown", "exposed", "dev"):
            raise RunnerError(f"unknown pool {pool!r}")
        selected = [case for case in cases if case.exposure == pool]
        if not selected:
            raise RunnerError(f"no cases with exposure={pool!r}")
    return selected[:limit] if limit is not None else selected


def command_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    budget = Budget.from_config(config)
    data_root = Path(args.data_root).expanduser().resolve()
    # Isolation preflight (design §2 / protocol §6.4): fail closed on
    # permission drift, record the boundary status in the run index.
    isolation_audit = isolation.audit_data_area(data_root)
    if isolation_audit["status"] != "green":
        raise RunnerError(f"data-area isolation audit failed: {isolation_audit['failures'][:5]}")
    real: RealContext | None = None
    boundary_probe: dict[str, Any] | None = None
    code_state: dict[str, Any] | None = None
    if not args.dry_run:
        # Q3 real-execution gate (design §5 / protocol §11 Q10): the frozen
        # config validation replaces the old blanket refusal.  On top of it:
        # enforced OS isolation, credential-free solver environment, and the
        # integration-commit code state — fail closed on any of them.
        errors = config_freeze_errors(config) + dev_pilot_coverage_errors(data_root, config)
        if errors:
            raise RealExecutionGateError(
                "config 冻结校验未通过，拒绝真实执行：" + "；".join(errors[:8])
                + f"（共 {len(errors)} 项；config={config_path}）")
        isolation.assert_real_run_isolation(config, data_root)
        # B's managed serve inherits this process's environment; scrub dataset
        # credentials here so no solver path can inherit them either.
        for name in isolation.CREDENTIAL_ENV_VARS:
            os.environ.pop(name, None)
        code_state = code_state_matches(config["baseline"]["integration_commit"])
        if not code_state.get("matches"):
            raise RealExecutionGateError(
                f"代码状态与冻结的 integration_commit 不一致：{code_state}；真实执行必须运行冻结代码")
        boundary_probe = prepare_solver_boundary(data_root, config)
        real = RealContext.build(config, boundary_probe)
    web_entry = (config.get("web_search") or {}).get("entrypoint") or None
    if web_entry == "offline":
        web_entry = None  # explicit freeze of "no web entry wired" (§6.3)
    strict = getattr(args, "mode", "default") == "strict-v2"
    if strict:
        if not args.dry_run:
            strict_errors = strict_real_gates(data_root, config)
            if strict_errors:
                raise RealExecutionGateError("strict-v2 前置门未通过：" + "；".join(strict_errors[:6]))
        # The official condition: native web on, raw PDF corpus, no metering.
        web_entry = "native"
    cases = load_cases(data_root)
    selected = _select_cases(cases, limit=args.cases, case_keys=args.case_keys or [], pool=args.pool)
    # Corpus routing by question set (protocol §6.1): the dev pilot's v1
    # questions read the staged v1 plain-text corpus, the main test reads the
    # frozen V2 corpus.  Fail closed here — before any episode starts — when
    # a selected dataset has no mapped corpus or its index is missing.
    selection = corpus_selection(config)
    if strict:
        selection = dict(selection)
        selection[prepare_dataset.DATASET] = STRICT_CORPUS_NAME
    for case in selected:
        if case.dataset not in selection:
            raise RunnerError(f"no staged corpus configured for dataset {case.dataset!r} "
                              f"(case {case.case_key[:16]})")
    for corpus_name in sorted({selection[case.dataset] for case in selected}):
        if not (data_root / corpus_name / "index" / "manifest.json").is_file():
            raise RunnerError(f"selected cases need corpus {corpus_name!r} but its index is "
                              f"missing under {data_root}; run corpus_adapter build first")
    arms = [arm for arm in args.arms.split(",") if arm]
    if not arms or any(arm not in ARMS for arm in arms):
        raise RunnerError(f"--arms must be a comma-separated subset of {ARMS}")
    if "B" in arms and budget.max_automatic_revisions_B < 0:
        raise RunnerError("max_automatic_revisions_B must be >= 0")
    prefix = "dryrun" if args.dry_run else "run"
    label = args.run_label or f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    episodes_root = data_root / "episodes" / label
    if episodes_root.exists():
        raise RunnerError(f"run label already exists: {label}")

    # Per-case randomized arm order (§8.2), then a global interleave so the
    # arms share the same time window instead of all-A-then-all-B.
    rng = random.Random(args.seed)
    tasks: list[tuple[Case, str, int]] = []
    for case in selected:
        order = list(arms)
        rng.shuffle(order)
        tasks.extend((case, arm, index + 1) for index, arm in enumerate(order))
    rng.shuffle(tasks)

    # Protocol §8.1's cap is on simultaneously ACTIVE MODEL CALLS, not on
    # episodes: a run-wide ModelCallLimiter enforces it across every arm and
    # role call, while episode scheduling gets its own --episode-workers
    # knob (they are decoupled on purpose — waiting on a slot is the
    # correct behaviour when episodes would oversubscribe the call budget).
    limiter = ModelCallLimiter(budget.max_concurrent_model_calls)
    episode_workers = args.episode_workers or budget.max_concurrent_model_calls
    if episode_workers < 1:
        raise RunnerError("--episode-workers must be >= 1 (0 means max_concurrent_model_calls)")

    def stamp(record: dict[str, Any]) -> dict[str, Any]:
        record["protocol_id"] = config["protocol_id"]
        record["experiment_id"] = config["experiment_id"]
        path = Path(record["workspace"]) / "episode_record.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return record

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=episode_workers) as pool_executor:
        futures = {pool_executor.submit(_EPISODE_RUNNERS[arm], case, data_root=data_root,
                                        budget=budget, label=label, order_index=order_index,
                                        corpus_name=selection[case.dataset],
                                        limiter=limiter, web_entry=web_entry, real=real,
                                        strict=strict):
                   (case.case_key, arm) for case, arm, order_index in tasks}
        for future in as_completed(futures):
            case_key, arm = futures[future]
            try:
                results.append(stamp(future.result()))
            except Exception as exc:  # noqa: BLE001 - recorded, the batch continues
                failures.append({"case_key": case_key, "arm": arm,
                                 "error": f"{type(exc).__name__}: {exc}"})
    index = {
        "schema_version": "officeqa.episode_index.v1",
        "run_label": label, "created": _now(),
        "protocol_id": config["protocol_id"], "experiment_id": config["experiment_id"],
        "dry_run": bool(args.dry_run), "seed": args.seed, "arms": arms,
        "episode_workers": episode_workers,
        "config": {"path": str(config_path),
                   "sha256": _sha256_bytes(config_path.read_bytes())},
        "model_calls": limiter.report(),
        "isolation": {"data_area": isolation_audit,
                      "boundary": isolation.boundary_report(config),
                      "solver_environment": "credential-stripped via isolation.solver_environment()",
                      **({"boundary_probe": boundary_probe, "code_state": code_state}
                         if boundary_probe is not None else {})},
        "runtime": ({"host": "opencode", "model": real.model, "variant": real.variant,
                     "host_version": real.host_version}
                    if real else {"host": None, "model": None, "variant": None,
                                  "host_version": None}),
        "web_entry": web_entry,
        "budget": {"wall_clock_seconds": budget.wall_clock_seconds,
                   "max_concurrent_model_calls": budget.max_concurrent_model_calls,
                   "max_automatic_revisions_B": budget.max_automatic_revisions_B,
                   "format_repair_attempts": budget.format_repair_attempts,
                   "search_budget": dict(budget.search_budget)},
        "cases": [{"case_key": case.case_key, "uid": case.uid, "dataset": case.dataset,
                   "exposure": case.exposure, "corpus": selection[case.dataset]}
                  for case in selected],
        "episodes": sorted(results, key=lambda r: (r["case_key"], r["arm"])),
        "failures": failures,
    }
    (episodes_root / "run_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_label": label, "episodes": len(results), "failures": failures,
                      "index": str(episodes_root / "run_index.json")}, ensure_ascii=False))
    return 0 if not failures else 1


def command_freeze(args: argparse.Namespace) -> int:
    """Seal predictions into evaluator-only BEFORE any gold is read (§8.3)."""
    config = load_config()
    data_root = Path(args.data_root).expanduser().resolve()
    episodes_root = data_root / "episodes" / args.run_label
    index_path = episodes_root / "run_index.json"
    if not index_path.is_file():
        raise RunnerError(f"no run_index.json under {episodes_root}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    frozen_root = data_root / "evaluator-only" / "frozen" / args.run_label
    answers_root = frozen_root / "answers"
    answers_root.mkdir(parents=True, exist_ok=True)
    predictions: list[dict[str, Any]] = []
    intended_arms = index.get("arms") or sorted({episode["arm"] for episode in index["episodes"]})
    dataset_revision = index["episodes"][0]["dataset"]["revision"] if index["episodes"] else None
    for case in index["cases"]:
        for arm in sorted(intended_arms):
            records = [episode for episode in index["episodes"]
                       if episode["case_key"] == case["case_key"] and episode["arm"] == arm]
            record = records[0] if records else None
            chosen = record.get("chosen_submission") if record else None
            entry: dict[str, Any] = {"schema_version": PREDICTIONS_SCHEMA,
                                     "run_label": args.run_label, "arm": arm,
                                     "case_key": case["case_key"], "uid": case["uid"],
                                     "dataset_revision": dataset_revision}
            if chosen:
                payload = (Path(record["workspace"]) / "submissions" /
                           f"{chosen['seq']:04d}-answer.json").read_bytes()
                if _sha256_bytes(payload) != chosen["sha256"]:
                    raise RunnerError(f"frozen answer bytes changed for {arm} {case['case_key']}")
                name = f"{arm}-{case['case_key']}.answer.json"
                _write_private_bytes(answers_root / name, payload)
                entry.update({"status": chosen["status"], "answer_sha256": chosen["sha256"],
                              "submission_seq": chosen["seq"], "answer_file": str(answers_root / name)})
            else:
                # Harness-owned missing prediction (protocol §9.1): no fabricated
                # answer.json — score 0 with the reason recorded separately.
                entry.update({"status": "missing", "answer_sha256": None,
                              "submission_seq": None, "answer_file": None,
                              "reason": record.get("error") if record else "episode did not run"})
            predictions.append(entry)
    if not predictions:
        raise RunnerError("nothing to freeze: the run index has no cases")
    manifest = {"schema_version": "officeqa.frozen_predictions.v1",
                "run_label": args.run_label, "frozen_at": _now(),
                "protocol_id": config["protocol_id"], "experiment_id": config["experiment_id"],
                "dry_run": index.get("dry_run", False),
                "scorer": config["scorer"],
                "predictions": len(predictions),
                "answers_sha256": {entry["answer_file"].rsplit("/", 1)[-1]: entry["answer_sha256"]
                                   for entry in predictions if entry["answer_file"]}}
    _write_private_bytes(frozen_root / "manifest.json",
                         json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    _write_private_bytes(frozen_root / "predictions.jsonl",
                         ("\n".join(json.dumps(entry, ensure_ascii=False) for entry in predictions) + "\n").encode("utf-8"))
    print(json.dumps({"frozen": len(predictions), "root": str(frozen_root)}, ensure_ascii=False))
    return 0


def command_score(args: argparse.Namespace) -> int:
    """Isolated scoring: verify the freeze, then read gold and run the official scorer."""
    config = load_config()
    data_root = Path(args.data_root).expanduser().resolve()
    frozen_root = data_root / "evaluator-only" / "frozen" / args.run_label
    manifest_path = frozen_root / "manifest.json"
    predictions_path = frozen_root / "predictions.jsonl"
    if not manifest_path.is_file() or not predictions_path.is_file():
        raise RunnerError(f"no frozen predictions for run label {args.run_label!r}; freeze before scoring")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    score_answer_record.verify_scorer_pin(config["scorer"])  # infra error if the pin drifted
    gold = {}
    # Evaluator side (protocol §6.4): every prepared gold side-load is in play —
    # the v2 main set plus the historical v1 pool the dev pilot draws from.
    gold_files = sorted((data_root / "evaluator-only" / "gold").glob("officeqa_pro_*.gold.jsonl"))
    if not gold_files:
        raise RunnerError(f"no gold side-loads under {data_root / 'evaluator-only' / 'gold'}")
    for gold_path in gold_files:
        for line in gold_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row["case_key"] in gold:
                    raise RunnerError(f"duplicate case_key across gold files: {row['case_key'][:16]}")
                gold[row["case_key"]] = row["answer"]
    scores: list[dict[str, Any]] = []
    for line in predictions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        prediction = json.loads(line)
        case_key = prediction["case_key"]
        if case_key not in gold:
            raise RunnerError(f"prediction has no gold (dataset revision drift?): {case_key[:16]}")
        if prediction["status"] == "missing":
            scores.append({"schema_version": SCORES_SCHEMA, "run_label": args.run_label,
                           "arm": prediction["arm"], "case_key": case_key, "uid": prediction["uid"],
                           "score": 0.0, "outcome": "missing",
                           "answer_sha256": None, "reason": prediction.get("reason", "missing")})
            continue
        answer_file = Path(prediction["answer_file"])
        if _sha256_bytes(answer_file.read_bytes()) != prediction["answer_sha256"]:
            raise RunnerError(f"frozen answer hash mismatch for {prediction['arm']} {case_key[:16]}")
        result = score_answer_record.score_answer_record(gold[case_key], answer_file.read_bytes())
        outcome = result.projection.status  # answered | abstained | invalid
        scores.append({"schema_version": SCORES_SCHEMA, "run_label": args.run_label,
                       "arm": prediction["arm"], "case_key": case_key, "uid": prediction["uid"],
                       "score": result.score, "outcome": outcome,
                       "answer_sha256": result.answer_sha256, "reason": result.reason})
    summary = {"schema_version": "officeqa.score_summary.v1", "run_label": args.run_label,
               "scored_at": _now(), "protocol_id": config["protocol_id"],
               "experiment_id": config["experiment_id"], "dry_run": manifest.get("dry_run", False),
               "scorer_sha256": config["scorer"]["sha256"], "tolerance": config["scorer"]["tolerance"],
               "arms": {}, "paired_delta_b_minus_a": None, "note":
               ("dry-run stub 答案为固定合成值，分数只验证管线，不代表任何模型成绩" if manifest.get("dry_run", False)
                else "真实运行分数；开发 pilot 题目为已暴露旧题（协议 §7），只作接入诊断，不构成泛化结论")}
    for arm in ARMS:
        rows = [row for row in scores if row["arm"] == arm]
        if not rows:
            continue
        correct = sum(1 for row in rows if row["score"] >= 1.0)
        summary["arms"][arm] = {
            "N": len(rows), "correct": correct,
            "accuracy": round(correct / len(rows), 4),
            "valid_answer_rate": round(sum(1 for row in rows if row["outcome"] == "answered") / len(rows), 4),
            "abstained": sum(1 for row in rows if row["outcome"] == "abstained"),
            "format_error": sum(1 for row in rows if row["outcome"] == "invalid"),
            "missing": sum(1 for row in rows if row["outcome"] == "missing"),
        }
    if "A" in summary["arms"] and "B" in summary["arms"]:
        by_key = {(row["arm"], row["case_key"]): row["score"] for row in scores}
        pairs = [(by_key[("B", key)], by_key[("A", key)]) for key in
                 {row["case_key"] for row in scores}
                 if ("B", key) in by_key and ("A", key) in by_key]
        if pairs:
            summary["paired_delta_b_minus_a"] = round(
                sum(1.0 for b, a in pairs if b > a) - sum(1.0 for b, a in pairs if b < a), 4)
            summary["b_wrong_to_right"] = sum(1 for b, a in pairs if b > a)
            summary["b_right_to_wrong"] = sum(1 for b, a in pairs if b < a)
    scores_root = data_root / "evaluator-only" / "scores" / args.run_label
    scores_root.mkdir(parents=True, exist_ok=True)
    _write_private_bytes(scores_root / "scores.jsonl",
                         ("\n".join(json.dumps(row, ensure_ascii=False) for row in scores) + "\n").encode("utf-8"))
    _write_private_bytes(scores_root / "summary.json",
                         (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"run_label": args.run_label, "arms": summary["arms"],
                      "out": str(scores_root / "summary.json")}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run A/B episodes (--dry-run = stubs; without it the "
                                          "frozen-config gate opens the real opencode transports)")
    run.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    run.add_argument("--config", default=str(CONFIG_PATH),
                     help="experiment config (default: the frozen config.json next to this module; "
                          "the run index records the path and its SHA-256)")
    run.add_argument("--run-label")
    run.add_argument("--arms", default="A,B")
    run.add_argument("--mode", choices=("default", "strict-v2"), default="default",
                     help="default = staged parsed corpus + metered search; "
                          "strict-v2 = raw PDF originals, no metering, web on (official condition)")
    run.add_argument("--cases", type=int, help="first N cases from the prepared order")
    run.add_argument("--case-keys", nargs="*", help="explicit case keys")
    run.add_argument("--pool", choices=("unknown", "exposed", "dev"),
                     help="restrict to an exposure pool (dev = the Q3 pilot questions)")
    run.add_argument("--seed", type=int, default=20260914)
    run.add_argument("--episode-workers", type=int, default=0,
                     help="episode scheduling parallelism (0 = max_concurrent_model_calls). "
                          "Deliberately separate from the §8.1 model-call cap, which the "
                          "run-wide ModelCallLimiter enforces across all arms and roles.")
    run.add_argument("--dry-run", action="store_true",
                     help="stub solvers over the real interfaces; zero model calls")

    freeze = commands.add_parser("freeze", help="seal predictions into evaluator-only before scoring")
    freeze.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    freeze.add_argument("--run-label", required=True)

    score = commands.add_parser("score", help="isolated scoring of frozen predictions against gold")
    score.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    score.add_argument("--run-label", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return command_run(args)
        if args.command == "freeze":
            return command_freeze(args)
        if args.command == "score":
            return command_score(args)
        parser.error(f"unknown command {args.command}")
        return 2
    except (RunnerError, corpus_adapter.CorpusError) as exc:
        print(f"run_episodes error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
