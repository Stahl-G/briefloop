"""Append-only reward ledger: the measurement memory behind the B'' gate.

Every completed evaluation run appends exactly one JSONL record here.  The
ledger is the ONLY input ``scripts/check_reward_gate.py`` trusts: the gate
compares a candidate reward against the best recorded reward for the same
split and refuses to say anything when no measurement exists (fail-closed).

Each record pins the identity of everything that produced the number:

* ``corpus_sha256``   -- canonical digest over the packaged corpus files
* ``roles_sha256``    -- digest of ``configs/agent_roles.yaml``, the single
  generation source of the role instructions under evaluation (the B''
  loop evolves this file; the ledger must distinguish its versions)
* ``envelope_sha256`` -- digest of the harness-owned reporting contract the
  envelope injected (``corpus_data/envelope-auditor-reporting.md``); a
  wording change in the constraint changes what is measured

The reward decomposition travels with the number (recall, true-negative
rate, format compliance, block agreement) so a drop can be attributed to
detection or to contract adherence without re-running anything.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LEDGER_SCHEMA = "briefloop.evaluation_reward_ledger.v1"

#: Repo-side ledger location: committed measurement history, public-safe
#: (numbers and digests only), next to the human-readable evidence records.
DEFAULT_LEDGER_PATH = Path("docs/evaluation-results/reward_ledger.jsonl")


class _Strict(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, validate_default=True
    )


class RewardLedgerRecord(_Strict):
    """One completed evaluation run, as one ledger line."""

    schema_version: Literal["briefloop.evaluation_reward_ledger.v1"]
    recorded_at: str
    rollout_kind: Literal["codex"]
    split: Literal["train", "val"]
    run_index: int = Field(ge=1)
    case_count: int = Field(ge=1)
    reward: float = Field(ge=0.0, le=1.0)
    defect_recall: float = Field(ge=0.0, le=1.0)
    true_negative_rate: float = Field(ge=0.0, le=1.0)
    format_compliance: float = Field(ge=0.0, le=1.0)
    block_agreement: float = Field(ge=0.0, le=1.0)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    roles_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    envelope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    notes: str = ""


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus_digest(corpus_data_dir: Path) -> str:
    """Canonical digest over the packaged corpus files (manifest + cases)."""
    manifest = corpus_data_dir / "manifest.yaml"
    lines = [f"manifest.yaml:{_file_sha256(manifest)}"]
    for case_path in sorted((corpus_data_dir / "cases").glob("*.yaml")):
        lines.append(f"cases/{case_path.name}:{_file_sha256(case_path)}")
    digest_input = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


def envelope_digest(envelope_path: Path) -> str:
    """Digest of the harness-owned reporting contract."""
    return _file_sha256(envelope_path)


def roles_digest(agent_roles_yaml: Path) -> str:
    """Digest of ``configs/agent_roles.yaml``, the instructions' source."""
    return _file_sha256(agent_roles_yaml)


def append_record(ledger_path: Path, record: RewardLedgerRecord) -> None:
    """Append exactly one validated record; the ledger is never rewritten."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")


def load_records(ledger_path: Path) -> list[RewardLedgerRecord]:
    """Parse and validate every line; a malformed ledger is an error, not
    a partial read (the gate must never reason over a truncated memory)."""
    if not ledger_path.exists():
        return []
    records: list[RewardLedgerRecord] = []
    for line_number, line in enumerate(
        ledger_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            records.append(
                RewardLedgerRecord.model_validate(json.loads(line), strict=True)
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as ledger error
            raise ValueError(
                f"reward ledger line {line_number} is invalid: {exc}"
            ) from exc
    return records


def best_reward(records: list[RewardLedgerRecord], *, split: str = "val") -> float | None:
    """Best recorded reward for a split, or ``None`` when nothing is measured."""
    rewards = [record.reward for record in records if record.split == split]
    return max(rewards) if rewards else None


def record_from_score(
    score: Any,
    *,
    split: str,
    run_index: int,
    case_count: int,
    corpus_sha256: str,
    roles_sha256: str,
    envelope_sha256: str,
    recorded_at: str,
    notes: str = "",
) -> RewardLedgerRecord:
    """Build a ledger record from a ``CorpusScore`` and the run identity."""
    return RewardLedgerRecord(
        schema_version=LEDGER_SCHEMA,
        recorded_at=recorded_at,
        rollout_kind="codex",
        split=split,
        run_index=run_index,
        case_count=case_count,
        reward=score.reward,
        defect_recall=score.defect_recall,
        true_negative_rate=score.true_negative_rate,
        format_compliance=score.format_compliance,
        block_agreement=score.block_agreement,
        corpus_sha256=corpus_sha256,
        roles_sha256=roles_sha256,
        envelope_sha256=envelope_sha256,
        notes=notes,
    )
