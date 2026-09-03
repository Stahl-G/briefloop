"""YoY/QoQ contrast pairing and the deterministic catalyst calendar.

One blocking rule: when a citing paragraph headlines year-over-year
growth for a subject whose reliably derived sequential comparison moves
the opposite way, that same paragraph must also show the sequential
percentage with the correct sign.  Everything else about structured
metrics is a visible warning, never a blocker.  The catalyst calendar is
not gated at all — Python renders it directly into the reader output.
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER_RE = re.compile(r"[+-]?[−-]?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?")
_VALUE_TOLERANCE = 0.05
_DECLINE_WORDS = (
    "下跌",
    "跌",
    "下滑",
    "回落",
    "decline",
    "fell",
    "down",
    "drop",
    "slump",
)
_RISE_WORDS = (
    "上涨",
    "涨",
    "增长",
    "上升",
    "回升",
    "rise",
    "rose",
    "gain",
    "climb",
    "grew",
    "growth",
)

_CALENDAR_EMPTY_ZH = "未识别到有来源支持的明确日期催化剂"
_CALENDAR_EMPTY_EN = "No source-backed dated catalyst identified"


def _qoq_shown_with_sign(body: str, qoq_pct: float) -> bool:
    """The sequential percentage must appear with its correct sign.

    A signed token (for example -17.2) always satisfies; an unsigned
    token satisfies only with same-line decline/rise wording matching
    the derived direction.
    """

    negative = qoq_pct < 0
    for match in _NUMBER_RE.finditer(body):
        token = match.group(0).replace(",", "").replace("−", "-")
        try:
            number = float(token)
        except ValueError:
            continue
        if abs(abs(number) - abs(qoq_pct)) > _VALUE_TOLERANCE:
            continue
        line_start = body.rfind("\n", 0, match.start()) + 1
        line_end = body.find("\n", match.end())
        line = body[line_start : len(body) if line_end == -1 else line_end].lower()
        if negative:
            if number < 0:
                return True
            if any(word in line for word in _DECLINE_WORDS):
                return True
        else:
            if number > 0 and (
                token.startswith("+")
                or any(word in line for word in _RISE_WORDS)
            ):
                return True
    return False


def _iter_claim_ids(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("half_claim_ids", "quarter_claim_ids", "yoy_basis_claim_id"):
        value = item.get(key)
        if isinstance(value, str):
            ids.append(value)
        elif isinstance(value, list):
            ids.extend(str(row) for row in value)
    return sorted(set(ids))


def _paragraph_headlines_yoy(paragraph: str, yoy_pct: float | None) -> bool:
    """True when the paragraph actually quotes the YoY number.

    Citing the underlying claims without restating the growth figure is
    not a headline; only an emphasized growth quote creates the pairing
    duty.
    """

    if yoy_pct is None:
        return False
    for match in _NUMBER_RE.finditer(paragraph):
        token = match.group(0).replace(",", "").replace("−", "-")
        try:
            number = float(token)
        except ValueError:
            continue
        if abs(abs(number) - abs(yoy_pct)) > _VALUE_TOLERANCE:
            continue
        line_start = paragraph.rfind("\n", 0, match.start()) + 1
        line_end = paragraph.find("\n", match.end())
        line = paragraph[
            line_start : len(paragraph) if line_end == -1 else line_end
        ].lower()
        if yoy_pct > 0 and any(word in line for word in _RISE_WORDS):
            return True
        if yoy_pct < 0 and any(word in line for word in _DECLINE_WORDS):
            return True
    return False


def metric_pairing_findings(
    markdown: str,
    *,
    derived_metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Block only the unshown sign-conflicting sequential contrast."""

    findings: list[dict[str, Any]] = []
    lines = markdown.splitlines()
    for item in derived_metrics:
        if item.get("kind") != "pair" or not item.get("sign_conflict"):
            continue
        qoq_pct = item.get("qoq_pct")
        if not isinstance(qoq_pct, (int, float)):
            continue
        cited_claim_ids = _iter_claim_ids(item)
        yoy_pct = item.get("yoy_pct")
        citing_paragraphs = [
            "\n".join(lines[start:end])
            for start, end in _citing_spans(lines, cited_claim_ids)
        ]
        duty_paragraphs = [
            paragraph
            for paragraph in citing_paragraphs
            if _paragraph_headlines_yoy(paragraph, yoy_pct)
        ]
        if not duty_paragraphs:
            continue
        blocked = False
        for paragraph in duty_paragraphs:
            if _qoq_shown_with_sign(paragraph, float(qoq_pct)):
                continue
            findings.append(
                {
                    "finding_type": "sequential_metric_unpaired",
                    "gate_id": "material_fact",
                    "category": "metric_pairing",
                    "severity": "high",
                    "blocking_level": "blocking",
                    "repair_owner": "editor",
                    "stage_id": "editor",
                    "artifact_id": "audited_brief",
                    "claim_id": None,
                    "source_id": None,
                    "description": (
                        f"A paragraph headlines {item['subject_id']} "
                        f"{item['metric_id']} {item['year']}{item['half']} "
                        "year-over-year growth while the derived sequential "
                        f"comparison {item['prior_quarter']}→"
                        f"{item['quarter']} is {qoq_pct:+.1f}%; that "
                        "paragraph must also show the sequential "
                        "percentage with the correct sign."
                    ),
                    "recommendation": (
                        "Quote the quarter-over-quarter change with its "
                        "sign in the same paragraph as the headline growth."
                    ),
                    "metadata": {
                        "subject_id": item.get("subject_id"),
                        "metric_id": item.get("metric_id"),
                        "qoq_pct": qoq_pct,
                        "claim_ids": cited_claim_ids,
                    },
                }
            )
            blocked = True
            break
        if blocked:
            continue
    # Ambiguous groups stay visible but never block.
    for item in derived_metrics:
        if item.get("kind") == "diagnostic":
            findings.append(
                {
                    "finding_type": "metric_derivation_ambiguous",
                    "gate_id": "material_fact",
                    "category": "metric_pairing",
                    "severity": "medium",
                    "blocking_level": "warning",
                    "repair_owner": "human",
                    "stage_id": None,
                    "artifact_id": None,
                    "claim_id": None,
                    "source_id": None,
                    "description": (
                        f"{item['subject_id']} {item['metric_id']} "
                        f"{item['year']} carries duplicate "
                        f"{', '.join(item['duplicate_grains'])} values; no "
                        "sequential comparison was derived."
                    ),
                    "recommendation": (
                        "Deduplicate or re-scope the structured metric "
                        "claims in the next run."
                    ),
                    "metadata": {
                        "subject_id": item.get("subject_id"),
                        "metric_id": item.get("metric_id"),
                        "claim_ids": list(item.get("claim_ids") or []),
                    },
                }
            )
    return findings


def _citing_spans(lines: list[str], claim_ids: list[str]):
    """Paragraph spans (start, end) whose lines cite any claim id."""

    spans: list[tuple[int, int]] = []
    start = 0
    for index, line in enumerate(lines + [""]):
        if line.strip() and not line.startswith("#"):
            continue
        if index > start:
            paragraph = "\n".join(lines[start:index])
            if any(f"[src:{claim_id}]" in paragraph for claim_id in claim_ids):
                spans.append((start, index))
        start = index + 1
    return spans


def render_catalyst_calendar(
    upcoming_events: list[dict[str, Any]],
    *,
    zh: bool = True,
) -> str:
    """Reader-safe deterministic calendar table (no internal ids)."""

    lines = ["| Date | Catalyst |", "| --- | --- |"]
    if not upcoming_events:
        return (
            f"{_CALENDAR_EMPTY_ZH if zh else _CALENDAR_EMPTY_EN}"
        )
    for event in sorted(
        upcoming_events, key=lambda item: (str(item.get("date") or ""), str(item.get("label") or ""))
    ):
        label = str(event.get("label") or "").replace("|", "/")
        lines.append(f"| {event.get('date', '')} | {label} |")
    return "\n".join(lines)


__all__ = [
    "metric_pairing_findings",
    "render_catalyst_calendar",
]
