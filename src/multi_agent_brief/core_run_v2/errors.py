"""Stable, value-free results for the dormant fresh-v2 core run spine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from multi_agent_brief.contracts.v2 import TransactionReceipt
from multi_agent_brief.control_store import (
    ControlStoreCommitOutcomeUnknown,
    ControlStoreConflict,
    ControlStoreError,
)


CoreRunStatus = Literal[
    "committed",
    "replayed",
    "blocked",
    "failed_uncommitted",
    "commit_outcome_unknown",
]


class CoreRunError(RuntimeError):
    """One uncommitted domain failure with a stable public reason code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def core_run_error_code(error: CoreRunError | ControlStoreError) -> str:
    """Translate Store failures at the deterministic domain boundary."""

    if isinstance(error, CoreRunError):
        return error.code
    if isinstance(error, ControlStoreCommitOutcomeUnknown):
        return "commit_outcome_unknown"
    if isinstance(error, ControlStoreConflict):
        if error.code == "store_revision_conflict":
            return "store_revision_conflict"
        if error.code == "transaction_replay_conflict":
            return "submission_replay_conflict"
    return "control_store_integrity_invalid"


def core_run_failure_result(
    error: CoreRunError | ControlStoreError,
) -> "CoreRunResult":
    """Preserve the durable-commit boundary in one public result shape."""

    if isinstance(error, ControlStoreCommitOutcomeUnknown):
        return CoreRunResult(
            status="commit_outcome_unknown",
            error_code="commit_outcome_unknown",
        )
    return CoreRunResult(
        status="failed_uncommitted",
        error_code=core_run_error_code(error),
    )


@dataclass(frozen=True)
class CoreRunResult:
    """Value-free command result and its optional authoritative receipt."""

    status: CoreRunStatus
    receipt: TransactionReceipt | None = None
    error_code: str | None = None
    primary_record_id: str | None = None

    def __post_init__(self) -> None:
        if self.status in {"committed", "replayed"}:
            valid = self.receipt is not None and self.error_code is None
        elif self.status == "blocked":
            valid = self.receipt is not None and self.error_code is not None
        elif self.status == "commit_outcome_unknown":
            valid = (
                self.receipt is None
                and self.error_code == "commit_outcome_unknown"
                and self.primary_record_id is None
            )
        else:
            valid = self.receipt is None and self.error_code is not None
        if not valid:
            raise ValueError("invalid core-run result shape")

    @property
    def exit_code(self) -> int:
        return 0 if self.status in {"committed", "replayed"} else 1

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"status": self.status}
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.primary_record_id is not None:
            payload["primary_record_id"] = self.primary_record_id
        if self.receipt is not None:
            payload["receipt"] = self.receipt.model_dump(
                mode="json",
                exclude_unset=False,
            )
        return payload


__all__ = [
    "CoreRunError",
    "CoreRunResult",
    "CoreRunStatus",
    "core_run_error_code",
    "core_run_failure_result",
]
