"""Gold-blind projection from a frozen answer.json to the official OfficeQA scorer.

Implements protocol BL-OQA-SR-v1.0 §3.1/§4 (design-0223 §3.Q0):

* ``project_answer`` never touches gold: it only validates the frozen
  submission bytes and produces the text handed to the official scorer.
* Rejected input classes (score 0 with the reason recorded separately):
  duplicate JSON keys, more than one JSON object (or any non-JSON payload
  such as Markdown fences), NaN/Infinity constants, illegal UTF-8,
  non-string ``answer`` values, multi-line or >250-char answers, and
  ``FINAL_ANSWER`` label injection (case-insensitive).
* ``status="abstained"`` scores 0.0 and the official scorer is not called.
* Every answered projection is wrapped in exactly one
  ``<FINAL_ANSWER>...</FINAL_ANSWER>`` pair and scored with
  ``tolerance=0.0`` — the wrapper adds no verdicts of its own.

The optional evidence attachment is hashed for the episode record
(protocol §3.3) but is never read by the scorer path: an attachment that
happens to contain the gold cannot flip a wrong answer (protocol §3.2).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ANSWER_SCHEMA_VERSION = "officeqa.answer.v1"
KNOWN_ANSWER_FIELDS = frozenset({"schema_version", "status", "answer"})
MAX_ANSWER_CHARS = 250
TOLERANCE = 0.0

_FINAL_ANSWER_LABEL_RE = re.compile(r"final_answer", re.IGNORECASE)

_OFFICIAL_REWARD_PATH = Path(__file__).resolve().parent / "official" / "reward.py"


class ScorerPinError(RuntimeError):
    """The scorer file does not match the pinned official hashes (§4 infra error)."""


class OfficialRewardModule(Protocol):
    def score_answer(self, ground_truth: str, predicted: str,
                     tolerance: float = 0.0) -> float: ...


@dataclass(frozen=True)
class AnswerProjection:
    """Result of the gold-blind projection of one frozen answer.json."""

    status: str  # "answered" | "abstained" | "invalid"
    answer: str | None = None  # stripped answer text, "answered" only
    reason: str = ""  # why abstained/invalid; "" for answered
    warnings: tuple[str, ...] = field(default=())

    @property
    def submission_text(self) -> str:
        if self.status != "answered" or self.answer is None:
            raise ValueError(f"no submission text for status={self.status!r}")
        return f"<FINAL_ANSWER>{self.answer}</FINAL_ANSWER>"


@dataclass(frozen=True)
class ScoreResult:
    """Official 0/1 score plus the audit trail for the episode record."""

    score: float
    projection: AnswerProjection
    reason: str  # projection reason, or the projection status when scored
    answer_sha256: str
    evidence_sha256: str | None
    submission_text: str | None
    tolerance: float


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate JSON key: {key}")
        seen.add(key)
    return dict(pairs)


def _decode(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"answer.json is not valid UTF-8: {exc}") from exc
    return raw


def project_answer(raw_answer: str | bytes) -> AnswerProjection:
    """Validate one frozen answer.json payload without any access to gold."""
    try:
        text = _decode(raw_answer)
        record = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        # Covers non-JSON payloads, Markdown fences, NaN/Infinity and
        # trailing extra objects ("Extra data") alike.
        return AnswerProjection("invalid", reason=f"answer payload is not a single strict JSON object: {exc}")

    if not isinstance(record, dict):
        return AnswerProjection("invalid", reason=f"answer payload must be one JSON object, got {type(record).__name__}")

    warnings: list[str] = []
    extra = sorted(set(record) - KNOWN_ANSWER_FIELDS)
    if extra:
        warnings.append(f"extra fields ignored, never passed to the scorer: {extra}")

    schema_version = record.get("schema_version")
    if schema_version != ANSWER_SCHEMA_VERSION:
        return AnswerProjection(
            "invalid",
            reason=f"schema_version must be {ANSWER_SCHEMA_VERSION!r}, got {schema_version!r}",
            warnings=tuple(warnings),
        )

    status = record.get("status")
    answer = record.get("answer", ...)
    if answer is ...:
        return AnswerProjection("invalid", reason="missing required field: answer", warnings=tuple(warnings))

    if status == "abstained":
        if answer is not None:
            return AnswerProjection(
                "invalid",
                reason="status=abstained must pair with answer=null",
                warnings=tuple(warnings),
            )
        return AnswerProjection("abstained", reason="abstained", warnings=tuple(warnings))

    if status != "answered":
        return AnswerProjection(
            "invalid",
            reason=f"status must be 'answered' or 'abstained', got {status!r}",
            warnings=tuple(warnings),
        )

    if not isinstance(answer, str):
        return AnswerProjection(
            "invalid",
            reason=f"answer must be a string for status=answered, got {type(answer).__name__}",
            warnings=tuple(warnings),
        )

    stripped = answer.strip()
    if not stripped:
        return AnswerProjection("invalid", reason="answer is empty after stripping whitespace", warnings=tuple(warnings))
    if len(stripped.splitlines()) > 1:
        return AnswerProjection("invalid", reason="answer must be a single line", warnings=tuple(warnings))
    if len(stripped) > MAX_ANSWER_CHARS:
        return AnswerProjection(
            "invalid",
            reason=f"answer exceeds {MAX_ANSWER_CHARS} characters after stripping: {len(stripped)}",
            warnings=tuple(warnings),
        )
    if _FINAL_ANSWER_LABEL_RE.search(stripped):
        return AnswerProjection(
            "invalid",
            reason="answer embeds a FINAL_ANSWER label (case-insensitive); exactly one tag pair is added by the projection only",
            warnings=tuple(warnings),
        )

    return AnswerProjection("answered", answer=stripped, warnings=tuple(warnings))


def load_official_reward(path: str | Path | None = None) -> OfficialRewardModule:
    """Load the pinned official reward.py as a module (never the vendored copy)."""
    target = Path(path) if path is not None else _OFFICIAL_REWARD_PATH
    spec = importlib.util.spec_from_file_location("officeqa_official_reward", target)
    if spec is None or spec.loader is None:
        raise ScorerPinError(f"cannot load official reward module from {target}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module  # type: ignore[return-value]


def scorer_hashes(path: str | Path | None = None) -> dict[str, Any]:
    """Recompute the double-recorded identity of the scorer file."""
    data = (Path(path) if path is not None else _OFFICIAL_REWARD_PATH).read_bytes()
    return {
        "git_blob_sha1": hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def verify_scorer_pin(expected: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    """Raise ScorerPinError unless the file matches both pinned hashes (§4)."""
    actual = scorer_hashes(path)
    for key in ("git_blob_sha1", "sha256"):
        want, got = expected.get(key), actual[key]
        if want is not None and want != got:
            raise ScorerPinError(f"official reward.py {key} mismatch: expected {want}, got {got}")
    return actual


def _raw_bytes(raw: str | bytes) -> bytes:
    return raw if isinstance(raw, bytes) else raw.encode("utf-8")


def score_answer_record(
    ground_truth: str,
    raw_answer: str | bytes,
    *,
    evidence: str | bytes | None = None,
    reward: OfficialRewardModule | None = None,
    tolerance: float = TOLERANCE,
) -> ScoreResult:
    """Score one frozen answer.json against gold with the official scorer.

    ``ground_truth`` must be a non-empty string: broken gold is an
    evaluation-infrastructure error (protocol §4) and must not be silently
    scored as a solver wrong answer. ``evidence`` is only hashed for the
    episode record and never influences the score.
    """
    if not isinstance(ground_truth, str) or not ground_truth.strip():
        raise ValueError("ground_truth is empty or not a string: evaluation infrastructure error, halt this case")

    projection = project_answer(raw_answer)
    answer_sha = hashlib.sha256(_raw_bytes(raw_answer)).hexdigest()
    evidence_sha = hashlib.sha256(_raw_bytes(evidence)).hexdigest() if evidence is not None else None

    if projection.status != "answered":
        return ScoreResult(
            score=0.0,
            projection=projection,
            reason=projection.reason,
            answer_sha256=answer_sha,
            evidence_sha256=evidence_sha,
            submission_text=None,
            tolerance=tolerance,
        )

    scorer = reward if reward is not None else load_official_reward()
    submission = projection.submission_text
    score = float(scorer.score_answer(ground_truth=ground_truth, predicted=submission, tolerance=tolerance))
    return ScoreResult(
        score=score,
        projection=projection,
        reason="answered",
        answer_sha256=answer_sha,
        evidence_sha256=evidence_sha,
        submission_text=submission,
        tolerance=tolerance,
    )
