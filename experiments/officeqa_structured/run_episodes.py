"""Two-arm episode runner for the OfficeQA structured-answer experiment.

Design-0223 §3.Q2 / protocol BL-OQA-SR-v1.0 §5, §8, §9.  Arm A is a native
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

Zero real model calls.  ``run --dry-run`` executes both arms with stub
solvers (a deterministic native stub, and a transport stub that walks the
real product interfaces); there is deliberately **no real-execution path
other than --dry-run** — wiring an actual host/model transport is the Q3
authorization gate (design §5) and fails closed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_adapter  # noqa: E402  (same experiment directory)
import prepare_dataset  # noqa: E402
import score_answer_record  # noqa: E402

EPISODE_SCHEMA = "officeqa.episode_record.v1"
PREDICTIONS_SCHEMA = "officeqa.predictions.v1"
SCORES_SCHEMA = "officeqa.scores.v1"
ARMS = ("A", "B")
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
CORPUS_TOOL_PATH = Path(__file__).resolve().parent / "corpus_adapter.py"
DEFAULT_DATA_ROOT = corpus_adapter.DEFAULT_DATA_ROOT
_REQUIRED_BUDGET_FIELDS = ("episode_wall_clock_seconds", "max_concurrent_model_calls",
                           "max_automatic_revisions_B", "format_repair_attempts_per_episode")


class RunnerError(RuntimeError):
    """Episode running/freezing/scoring failed closed."""


class RealExecutionGateError(RunnerError):
    """Real model execution is not wired until the Q3 authorization gate."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_bytes(record: dict[str, Any]) -> bytes:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Budget":
        budget = config["budget"]
        return cls(wall_clock_seconds=int(budget["episode_wall_clock_seconds"]),
                   max_concurrent_model_calls=int(budget["max_concurrent_model_calls"]),
                   max_automatic_revisions_B=int(budget["max_automatic_revisions_B"]),
                   format_repair_attempts=int(budget["format_repair_attempts_per_episode"]))


@dataclass(frozen=True)
class Case:
    case_key: str
    uid: str
    question: str
    question_sha256: str
    revision: str
    exposure: str


def load_cases(data_root: Path) -> list[Case]:
    """Load the sanitized question view; verify every case identity (§1.2).

    Opens only ``question_only/question_only.jsonl`` and the exposure ledger.
    Recomputing the question hash and case key here proves the runner's view
    is the prepared one — and the runner has no path to any answer payload.
    """
    data_root = Path(data_root).expanduser().resolve()
    question_path = data_root / "question_only" / "question_only.jsonl"
    ledger_path = data_root / "question_only" / "exposure_ledger.json"
    if not question_path.is_file() or not ledger_path.is_file():
        raise RunnerError(f"prepared dataset is missing under {data_root}; run prepare_dataset.py first")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    exposure = {entry["case_key"]: entry["exposure"] for entry in ledger["cases"]}
    revision = ledger["revision"]
    cases: list[Case] = []
    for line in question_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        digest = prepare_dataset.question_sha256(row["question"])
        if digest != row["question_sha256"]:
            raise RunnerError(f"question hash mismatch for uid {row['uid']!r}")
        if prepare_dataset.case_key(revision, row["uid"], digest) != row["case_key"]:
            raise RunnerError(f"case key mismatch for uid {row['uid']!r}")
        if row["revision"] != revision:
            raise RunnerError(f"revision mismatch for uid {row['uid']!r}")
        cases.append(Case(case_key=row["case_key"], uid=row["uid"], question=row["question"],
                          question_sha256=digest, revision=revision,
                          exposure=exposure.get(row["case_key"], "unknown")))
    if not cases:
        raise RunnerError("question_only.jsonl is empty")
    if set(exposure) != {case.case_key for case in cases}:
        raise RunnerError("exposure ledger and question list disagree")
    return cases


# --- shared instructions (identical corpus surface for both arms, §6.1) --------


def corpus_tool_instructions(data_root: Path) -> str:
    """The one corpus surface both arms see: search, snippet read, page view.

    No document list, no year hints, no file names — the agent must locate
    evidence itself (protocol §6.1).  B registers documents it actually uses
    through ``accept``; A reads without registration.
    """
    manifest_path = Path(data_root).expanduser().resolve() / "corpus" / "index" / "manifest.json"
    documents = "全量"
    if manifest_path.is_file():
        documents = str(json.loads(manifest_path.read_text(encoding="utf-8")).get("documents", documents))
    tool = str(CORPUS_TOOL_PATH)
    root = str(Path(data_root).expanduser().resolve())
    return f"""可用资料：一套本地只读语料（美国财政部公报全量解析，{documents} 份文档；不提供文件清单，也不给任何题目相关的文件提示）。
统一用以下命令操作语料（Python 已就绪，直接运行；输出为 JSON）：
- 查找文档：python3 {tool} docs --data-root {root} --query 关键词子串
- 关键词检索：python3 {tool} search --data-root {root} --query "关键词" [--doc 文档名] [--limit 20]
- 片段读取（1-based 行号）：python3 {tool} read --data-root {root} --doc 文档名 --start-line N --end-line M
- 按页查看（0-based page_index）：python3 {tool} page --data-root {root} --doc 文档名 --page-index P
检索词按文档年代、主题、指标与表头术语组合；同一文档先检索定位行号，再片段读取或按页查看；表格元素内容较长时会截断，需要完整内容时缩小行号范围。
每组还有同一受控联网搜索入口可用于补充公开资料：只读取原始发布者正文并记录全部请求；禁止浏览基准答案键、公开解题文章或任何评测输出。"""


NATIVE_FRAME = """You are answering one OfficeQA question against a local read-only corpus of parsed U.S. Treasury publications.

Work like a professional archivist: narrow by document era and family first (combined statements, govinfo receipts, appendix tables), then search entity and measure terms including period naming variants, then read the relevant tables closely — check headers, footnotes, units and the exact fiscal period before extracting operands. Multi-step arithmetic is allowed after extracting exact values.

This is a research task with self-checks: verify the answer against the located table, re-derive any computation, and confirm units and rounding follow the question. You have the corpus tools and the controlled web entry described below; no other assistance.

Submit the final answer as a single UTF-8 JSON file `answer.json` in the submit directory, exactly:
{{"schema_version": "officeqa.answer.v1", "status": "answered", "answer": "<single-line direct answer>"}}
The answer keeps the direct form the question asks for (number, date, text, or a list string in the question's order such as "[North, 0.866]"). No explanations, units the question did not ask for, citation markers, confidence or alternatives. One line, at most 250 characters after trimming. If you genuinely cannot determine the answer, submit {{"schema_version": "officeqa.answer.v1", "status": "abstained", "answer": null}}. The submitting program accepts, timestamps and hashes every answer file; only your latest accepted submission before the deadline counts.
"""


# --- program acceptance endpoint (protocol §8.3) -------------------------------


class SubmissionBox:
    """Program-side submission acceptance: validate, sequence, hash, timestamp.

    Contract-valid submissions are accepted with the next sequence number and
    their raw bytes preserved; format-invalid payloads (and anything arriving
    after the deadline) are kept as attempts without acceptance, so scoring
    falls back to the previous accepted answer exactly as §8.3 requires.
    """

    def __init__(self, episode_dir: Path, deadline: float):
        self.episode_dir = Path(episode_dir)
        self.submissions = self.episode_dir / "submissions"
        self.attempts = self.episode_dir / "attempts"
        self.submissions.mkdir(parents=True, exist_ok=True)
        self.attempts.mkdir(parents=True, exist_ok=True)
        self.deadline = deadline
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
    submits a fixed synthetic answer.  The term comes from the question text
    only — the stub never sees any gold.
    """

    answer = "native-stub-answer"

    def solve(self, case: Case, workspace: Path, box: SubmissionBox,
              adapter: corpus_adapter.CorpusAdapter, tool_calls: list[dict[str, Any]]) -> None:
        hit = _first_hit(adapter, case.question, tool_calls, limit=5)
        if hit:
            snippet = adapter.read(hit["name"], hit["line"], hit["line"] + 2)
            tool_calls.append({"op": "read", "args": {"doc": hit["name"], "line": hit["line"]},
                               "results": snippet["returned"]})
        payload = {"schema_version": score_answer_record.ANSWER_SCHEMA_VERSION,
                   "status": "answered", "answer": self.answer}
        box.accept(_canonical_bytes(payload), source="solver")


def run_native_episode(case: Case, *, data_root: Path, budget: Budget, label: str,
                       order_index: int) -> dict[str, Any]:
    episode_dir = Path(data_root) / "episodes" / label / "A" / case.case_key
    if episode_dir.exists():
        raise RunnerError(f"episode directory already exists: {episode_dir}")
    episode_dir.mkdir(parents=True)
    started = time.time()
    deadline = started + budget.wall_clock_seconds
    workspace = episode_dir / "workspace"
    workspace.mkdir()
    (workspace / "question.md").write_text(f"# OfficeQA {case.uid}\n\n{case.question}\n", encoding="utf-8")
    (workspace / "prompt.md").write_text(
        NATIVE_FRAME + "\n## Question\n\n" + case.question + "\n\n" + corpus_tool_instructions(data_root) + "\n",
        encoding="utf-8")
    tool_calls: list[dict[str, Any]] = []
    box = SubmissionBox(episode_dir, deadline)
    with corpus_adapter.CorpusAdapter(Path(data_root) / "corpus") as adapter:
        StubNativeSolver().solve(case, workspace, box, adapter, tool_calls)
    record = _episode_record(case=case, arm="A", label=label, order_index=order_index,
                             episode_dir=episode_dir, started=started, deadline=deadline,
                             budget=budget, box=box, tool_calls=tool_calls,
                             identifiers={"transport": "stub-native", "host": None, "model": None},
                             linkage=None, usage_complete=False)
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

    def __init__(self, store: Any, adapter: corpus_adapter.CorpusAdapter):
        self.store = store
        self.adapter = adapter
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


def _episode_workspace_settings(store: Any, budget: Budget) -> None:
    """Design §4 closure list: no auto-learning, no fact checker, one revision,
    product-level deadlines off (the runner enforces its own wall clock)."""
    raw = store.meta("settings")
    raw.update({"auto_learn": False, "fact_checker": False,
                "auto_revision": budget.max_automatic_revisions_B > 0,
                "max_reports": budget.max_concurrent_model_calls,
                "timeout_minutes": 0})
    store.set_meta("settings", raw)


def run_briefloop_episode(case: Case, *, data_root: Path, budget: Budget, label: str,
                          order_index: int) -> dict[str, Any]:
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
    _episode_workspace_settings(store, budget)
    requirements = {"title": f"OfficeQA {case.uid}"[:200], "objective": case.question,
                    "key_questions": [case.question], "result_format": RESULT_FORMAT,
                    "allow_web": True, "fact_check": False,
                    "raw_input": corpus_tool_instructions(data_root)}
    submitted = dispatch(store, {"workspace_id": store.meta("workspace_id"), "action": "submit",
                                 "request_id": f"oqa-{case.case_key[:16]}",
                                 "requirements": requirements, "source_ids": []})
    job_id, run_id = submitted["job_id"], submitted["run_id"]
    box = SubmissionBox(episode_dir, deadline)
    tool_calls: list[dict[str, Any]] = []
    snapshots: set[str] = set()

    with corpus_adapter.CorpusAdapter(Path(data_root) / "corpus") as adapter:
        transport = StubBriefloopTransport(store, adapter)
        worker = Worker(store, runtime=transport, report_runtime_factory=lambda: transport)
        worker._review_runtime = transport
        worker.start()
        try:
            state: dict[str, Any] = {}
            while True:
                state = dispatch(store, {"workspace_id": store.meta("workspace_id"),
                                         "action": "query", "job_id": job_id})
                for version in store.rows("SELECT id FROM briefs WHERE run_id=? ORDER BY rowid", (run_id,)):
                    if version["id"] in snapshots:
                        continue
                    snapshots.add(version["id"])
                    identity = answer_of(store, version["id"])
                    if identity is None:
                        continue
                    entry = box.accept(_canonical_bytes(identity["answer"]), source="briefloop-version")
                    entry["version_id"] = version["id"]
                if state.get("terminal"):
                    break
                if time.time() > deadline:
                    worker.stop_job(job_id)
                    break
                time.sleep(0.05)
            # A revision admitted in the last instant still counts: sweep once
            # more after the job settled (protocol §8.3 keeps every accepted
            # submission, failures never erase them).
            for version in store.rows("SELECT id FROM briefs WHERE run_id=? ORDER BY rowid", (run_id,)):
                if version["id"] not in snapshots:
                    snapshots.add(version["id"])
                    identity = answer_of(store, version["id"])
                    if identity is not None:
                        entry = box.accept(_canonical_bytes(identity["answer"]), source="briefloop-version")
                        entry["version_id"] = version["id"]
        finally:
            worker.close()
        tool_calls.extend(transport.tool_calls)
        model_calls = list(transport.calls)
    record = _episode_record(case=case, arm="B", label=label, order_index=order_index,
                             episode_dir=episode_dir, started=started, deadline=deadline,
                             budget=budget, box=box, tool_calls=tool_calls,
                             identifiers={"transport": "stub-briefloop", "host": None, "model": None},
                             linkage={"run_id": run_id, "job_id": job_id,
                                      "job_status": state.get("status"),
                                      "version_ids": sorted(snapshots),
                                      "model_calls": model_calls},
                             usage_complete=False)
    return record


# --- episode record (protocol §3.3) --------------------------------------------


def _episode_record(*, case: Case, arm: str, label: str, order_index: int, episode_dir: Path,
                    started: float, deadline: float, budget: Budget, box: SubmissionBox,
                    tool_calls: list[dict[str, Any]], identifiers: dict[str, Any],
                    linkage: dict[str, Any] | None, usage_complete: bool) -> dict[str, Any]:
    finished = time.time()
    chosen = box.chosen()
    record = {
        "schema_version": EPISODE_SCHEMA,
        "protocol_id": None,  # filled by the caller with config identity
        "experiment_id": None,
        "dataset": {"benchmark": prepare_dataset.DATASET, "revision": case.revision},
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
                   "format_repair_attempts": budget.format_repair_attempts},
        "tool_calls": tool_calls,
        "usage_complete": usage_complete,
        "submissions": box.entries(),
        "chosen_submission": ({"seq": chosen["seq"], "sha256": chosen["sha256"],
                               "received_at": chosen["received_at"],
                               "status": chosen["status"]} if chosen else None),
        "workspace": str(episode_dir),
        "error": None,
        **({"briefloop": linkage} if linkage else {}),
    }
    (episode_dir / "episode_record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


# --- run / freeze / score commands ----------------------------------------------

_EPISODE_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {"A": run_native_episode,
                                                              "B": run_briefloop_episode}


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
        if pool not in ("unknown", "exposed"):
            raise RunnerError(f"unknown pool {pool!r}")
        selected = [case for case in cases if case.exposure == pool]
        if not selected:
            raise RunnerError(f"no cases with exposure={pool!r}")
    return selected[:limit] if limit is not None else selected


def command_run(args: argparse.Namespace) -> int:
    if not args.dry_run:
        # Authorization gate (design §5): Q3/Q4 need explicit user sign-off,
        # and no real transport is wired in this tree.  Fail closed.
        raise RealExecutionGateError(
            "真实模型执行未接入：本 runner 只有 --dry-run（stub）路径；Q3 授权与冻结（config 无 null/TODO）后才能实现并启用真实宿主/模型传输")
    config = load_config()
    budget = Budget.from_config(config)
    data_root = Path(args.data_root).expanduser().resolve()
    cases = load_cases(data_root)
    selected = _select_cases(cases, limit=args.cases, case_keys=args.case_keys or [], pool=args.pool)
    arms = [arm for arm in args.arms.split(",") if arm]
    if not arms or any(arm not in ARMS for arm in arms):
        raise RunnerError(f"--arms must be a comma-separated subset of {ARMS}")
    if "B" in arms and budget.max_automatic_revisions_B < 0:
        raise RunnerError("max_automatic_revisions_B must be >= 0")
    label = args.run_label or f"dryrun-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
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

    def stamp(record: dict[str, Any]) -> dict[str, Any]:
        record["protocol_id"] = config["protocol_id"]
        record["experiment_id"] = config["experiment_id"]
        path = Path(record["workspace"]) / "episode_record.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return record

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=budget.max_concurrent_model_calls) as pool_executor:
        futures = {pool_executor.submit(_EPISODE_RUNNERS[arm], case, data_root=data_root,
                                        budget=budget, label=label, order_index=order_index):
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
        "dry_run": True, "seed": args.seed, "arms": arms,
        "budget": {"wall_clock_seconds": budget.wall_clock_seconds,
                   "max_concurrent_model_calls": budget.max_concurrent_model_calls,
                   "max_automatic_revisions_B": budget.max_automatic_revisions_B,
                   "format_repair_attempts": budget.format_repair_attempts},
        "cases": [{"case_key": case.case_key, "uid": case.uid, "exposure": case.exposure}
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
                (answers_root / name).write_bytes(payload)
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
    (frozen_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (frozen_root / "predictions.jsonl").write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in predictions) + "\n", encoding="utf-8")
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
    gold_path = data_root / "evaluator-only" / "gold" / "officeqa_pro_v2.gold.jsonl"
    for line in gold_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
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
               "dry-run stub 答案为固定合成值，分数只验证管线，不代表任何模型成绩"}
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
    (scores_root / "scores.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in scores) + "\n", encoding="utf-8")
    (scores_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_label": args.run_label, "arms": summary["arms"],
                      "out": str(scores_root / "summary.json")}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run A/B episodes (only --dry-run is implemented)")
    run.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    run.add_argument("--run-label")
    run.add_argument("--arms", default="A,B")
    run.add_argument("--cases", type=int, help="first N cases from the prepared order")
    run.add_argument("--case-keys", nargs="*", help="explicit case keys")
    run.add_argument("--pool", choices=("unknown", "exposed"), help="restrict to an exposure pool")
    run.add_argument("--seed", type=int, default=20260914)
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
