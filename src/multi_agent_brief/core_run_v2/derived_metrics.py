"""Deterministic sequential-metric derivation from frozen claims.

One product question only: when the brief headlines year-over-year
growth while the same subject's frozen numbers show the sequential
comparison moving the other way, that contrast must be derivable.  A
pair is derived only when the SAME subject, metric, unit, and year carry
exactly one half-year value and exactly one second-quarter value;
duplicates or cross-subject inputs never pair — they surface as
diagnostics.  No generic formula system, no unit-based addability.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_PERIOD_RE = re.compile(r"^(\d{4})(Q[1-4]|H[1-2]|-(?:0[1-9]|1[0-2]))$")

_HALF_TO_QUARTERS = {"H1": ("Q1", "Q2"), "H2": ("Q3", "Q4")}


def _parse_period(period: str) -> tuple[int, str] | None:
    match = _PERIOD_RE.match(period)
    if match is None:
        return None
    return int(match.group(1)), match.group(2)


def derive_sequential_metrics(
    claims: Iterable[Any],
) -> list[dict[str, Any]]:
    """Derive Q1 = H1 - Q2 pairs under exact uniqueness; else diagnose.

    Returns derived pairs plus diagnostics (kind="ambiguous_group") for
    groups where a duplicate half or quarter value makes the subtraction
    unreliable.  Diagnostics carry no numbers-derived conclusions.
    """

    entries: list[dict[str, Any]] = []
    for claim in claims:
        metric = getattr(claim, "metric", None)
        if metric is None:
            continue
        parsed = _parse_period(metric.period)
        if parsed is None:
            continue
        year, grain = parsed
        entries.append(
            {
                "claim_id": claim.claim_id,
                "subject_id": metric.subject_id,
                "metric_id": metric.metric_id,
                "unit": metric.unit,
                "year": year,
                "grain": grain,
                "value": float(metric.value),
                "comparison_period": metric.comparison_period,
                "comparison_value": (
                    None
                    if metric.comparison_value is None
                    else float(metric.comparison_value)
                ),
            }
        )

    grouped: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
    for entry in entries:
        key = (
            entry["subject_id"],
            entry["metric_id"],
            entry["unit"],
            entry["year"],
        )
        grouped.setdefault(key, []).append(entry)

    derived: list[dict[str, Any]] = []
    for key in sorted(grouped):
        subject_id, metric_id, unit, year = key
        rows = grouped[key]
        half_rows = {
            row["grain"]: row for row in rows if row["grain"] in ("H1", "H2")
        }
        quarter_rows = {
            row["grain"]: row
            for row in rows
            if row["grain"] in ("Q1", "Q2", "Q3", "Q4")
        }
        # Exact uniqueness: any duplicate half or quarter input for this
        # subject+metric+unit+year makes the subtraction unreliable.
        grain_counts: dict[str, int] = {}
        for row in rows:
            grain_counts[row["grain"]] = grain_counts.get(row["grain"], 0) + 1
        duplicates = [
            grain
            for grain in ("H1", "H2", "Q1", "Q2", "Q3", "Q4")
            if grain_counts.get(grain, 0) > 1
        ]
        if duplicates:
            derived.append(
                {
                    "kind": "diagnostic",
                    "reason": "ambiguous_group",
                    "subject_id": subject_id,
                    "metric_id": metric_id,
                    "unit": unit,
                    "year": year,
                    "duplicate_grains": sorted(set(duplicates)),
                    "claim_ids": sorted(row["claim_id"] for row in rows),
                }
            )
            continue
        for half, (first_quarter, second_quarter) in _HALF_TO_QUARTERS.items():
            half_row = half_rows.get(half)
            second_row = quarter_rows.get(second_quarter)
            if half_row is None or second_row is None:
                continue
            half_value = half_row["value"]
            second_value = second_row["value"]
            derived_prior = half_value - second_value
            if derived_prior == 0:
                continue
            qoq_pct = (second_value - derived_prior) / abs(derived_prior) * 100
            yoy_pct = None
            source = None
            for row in (half_row, second_row):
                if row["comparison_value"] not in (None, 0):
                    yoy_pct = (
                        (row["value"] - row["comparison_value"])
                        / abs(row["comparison_value"])
                        * 100
                    )
                    source = row
                    break
            sign_conflict = bool(
                yoy_pct is not None and yoy_pct * qoq_pct < 0
            )
            derived.append(
                {
                    "kind": "pair",
                    "subject_id": subject_id,
                    "metric_id": metric_id,
                    "unit": unit,
                    "year": year,
                    "half": half,
                    "half_claim_ids": [half_row["claim_id"]],
                    "quarter": second_quarter,
                    "quarter_claim_ids": [second_row["claim_id"]],
                    "prior_quarter": first_quarter,
                    "prior_quarter_value": round(derived_prior, 6),
                    "quarter_value": second_value,
                    "qoq_pct": round(qoq_pct, 4),
                    "yoy_pct": None if yoy_pct is None else round(yoy_pct, 4),
                    "yoy_basis_claim_id": (
                        None if source is None else source["claim_id"]
                    ),
                    "sign_conflict": sign_conflict,
                }
            )
    return derived


def collect_upcoming_events(claims: Iterable[Any]) -> list[dict[str, Any]]:
    """Collect dated future catalysts carried by frozen claims."""

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for claim in claims:
        upcoming = getattr(claim, "upcoming", None)
        if upcoming is None:
            continue
        key = (upcoming.date, upcoming.label)
        record = by_key.setdefault(
            key,
            {"date": upcoming.date, "label": upcoming.label, "claim_ids": []},
        )
        if claim.claim_id not in record["claim_ids"]:
            record["claim_ids"].append(claim.claim_id)
    return [by_key[key] for key in sorted(by_key)]


__all__ = [
    "collect_upcoming_events",
    "derive_sequential_metrics",
]
