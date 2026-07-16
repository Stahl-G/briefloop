"""Fixed, value-free outcomes for dormant fresh-v2 intake."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from multi_agent_brief.contracts.v2 import TransactionReceipt


IntakeStatus = Literal[
    "committed",
    "replayed",
    "rejected_recorded",
    "failed_uncommitted",
    "commit_outcome_unknown",
]


class IntakeError(RuntimeError):
    """An uncommitted intake failure with one stable public code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class IntakeResult:
    """Value-free command result plus an optional authoritative receipt."""

    status: IntakeStatus
    receipt: TransactionReceipt | None = None
    error_code: str | None = None
    source_id: str | None = None
    proposal_id: str | None = None

    def __post_init__(self) -> None:
        if self.status in {"committed", "replayed"}:
            valid = self.receipt is not None and self.error_code is None
        elif self.status == "rejected_recorded":
            valid = self.receipt is not None and self.error_code is not None
        elif self.status == "commit_outcome_unknown":
            valid = (
                self.receipt is None
                and self.error_code == "commit_outcome_unknown"
                and self.source_id is None
                and self.proposal_id is None
            )
        else:
            valid = self.receipt is None and self.error_code is not None
        if not valid or (self.source_id is not None and self.proposal_id is not None):
            raise ValueError("invalid intake result shape")

    @property
    def exit_code(self) -> int:
        return 0 if self.status in {"committed", "replayed"} else 1

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"status": self.status}
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.source_id is not None:
            payload["source_id"] = self.source_id
        if self.proposal_id is not None:
            payload["proposal_id"] = self.proposal_id
        if self.receipt is not None:
            payload["receipt"] = self.receipt.model_dump(
                mode="json",
                exclude_unset=False,
            )
        return payload


__all__ = ["IntakeError", "IntakeResult", "IntakeStatus"]
