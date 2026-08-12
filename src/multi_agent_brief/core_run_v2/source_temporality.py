"""Deterministic source temporality derived from frozen run direction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from multi_agent_brief.contracts.v2 import (
    AcceptedSourceRecord,
    CandidateClaimItem,
    RunDirection,
    ScreeningDecisionItem,
)


TemporalRole = Literal["current_window", "background"]
TemporalBasis = Literal["published_at", "opened_at", "none"]


@dataclass(frozen=True)
class SourceTemporality:
    role: TemporalRole
    basis: TemporalBasis
    anchor_date: date | None


def _date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _datetime_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def classify_source_temporality(
    source: AcceptedSourceRecord,
    direction: RunDirection,
) -> SourceTemporality:
    """Classify one accepted source without treating retrieval as publication."""

    if source.document_kind == "status_incident":
        basis: TemporalBasis = "opened_at"
        anchor = _datetime_date(source.opened_at)
    else:
        basis = "published_at"
        anchor = _date(source.published_at)
    if anchor is None:
        return SourceTemporality(role="background", basis="none", anchor_date=None)

    report_day = _date(direction.report_date)
    start = _date(direction.report_window_start)
    end = _date(direction.report_window_end)
    if start is None or end is None:
        if report_day is None or direction.max_source_age_days is None:
            return SourceTemporality(role="background", basis=basis, anchor_date=anchor)
        end = report_day
        start = report_day - timedelta(days=direction.max_source_age_days)
    role: TemporalRole = "current_window" if start <= anchor <= end else "background"
    return SourceTemporality(role=role, basis=basis, anchor_date=anchor)


def high_priority_background_candidate_ids(
    candidates: tuple[CandidateClaimItem, ...] | list[CandidateClaimItem],
    decisions: tuple[ScreeningDecisionItem, ...] | list[ScreeningDecisionItem],
    sources_by_id: dict[str, AcceptedSourceRecord],
    direction: RunDirection,
) -> tuple[str, ...]:
    """Return selected-high candidates that lack current-window evidence."""

    candidates_by_id = {item.candidate_id: item for item in candidates}
    invalid: list[str] = []
    for decision in decisions:
        if decision.decision != "selected" or decision.priority != "high":
            continue
        candidate = candidates_by_id[decision.candidate_id]
        source = sources_by_id.get(candidate.source_id)
        if (
            source is None
            or classify_source_temporality(source, direction).role != "current_window"
        ):
            invalid.append(decision.candidate_id)
    return tuple(sorted(invalid))


__all__ = [
    "SourceTemporality",
    "TemporalBasis",
    "TemporalRole",
    "classify_source_temporality",
    "high_priority_background_candidate_ids",
]
