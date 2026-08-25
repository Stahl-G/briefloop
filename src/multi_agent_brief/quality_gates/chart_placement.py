"""Deterministic chart-placement contract for equity-periodic briefs.

Charts used to pile up in an end-of-report dump (or get silently
dropped).  This contract binds chart ids from the frozen chart manifest
to section intents and verifies each bound chart actually appears inside
its bound section.  Charts the manifest rendered but the brief omits
must be disclosed in the coverage/limitations section — silent omission
is blocking, a visible gap is not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from multi_agent_brief.quality_gates.section_contract import (
    limitations_section,
    section_matching,
    sections_from_markdown,
)

# intent -> chart ids that must be placed inside that section
SECTION_CHART_BINDINGS: dict[str, tuple[str, ...]] = {
    "market_reaction_divergence": ("toyo-price-volume", "one-week-return"),
    "earnings_valuation": ("ps-ttm", "market-cap-usd"),
    "peer_events": ("primary-indexed-trend",),
    "policy_input_dashboard": ("overseas-indexed-trend",),
}

_CHART_FILE_RE = re.compile(r"!\[[^\]]*\]\(([^\)]+)\)")


def _chart_id_of(reference: str) -> str:
    stem = Path(reference).stem
    return stem


def manifest_chart_ids(manifest_path: Path) -> list[str]:
    """Chart ids rendered by the latest frozen chart manifest, or []."""

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    charts = payload.get("charts") if isinstance(payload, dict) else None
    if not isinstance(charts, list):
        return []
    ids: list[str] = []
    for item in charts:
        if isinstance(item, dict) and isinstance(item.get("chart_id"), str):
            ids.append(item["chart_id"])
    return ids


def chart_placement_findings(
    markdown: str,
    *,
    manifest_ids: list[str],
    required_intents: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    """Blocking findings for misplaced or silently omitted charts."""

    if not manifest_ids:
        return []
    sections = sections_from_markdown(markdown)
    limitations = limitations_section(sections)
    placements: dict[str, set[str]] = {}
    for section in sections:
        for reference in _CHART_FILE_RE.findall(section.body):
            placements.setdefault(_chart_id_of(reference), set()).add(
                section.title
            )
    findings: list[dict[str, Any]] = []
    bound_ids = {
        chart_id
        for intent, chart_ids in SECTION_CHART_BINDINGS.items()
        if intent in required_intents
        for chart_id in chart_ids
    }

    def _gap_disclosed(chart_id: str) -> bool:
        if limitations is None:
            return False
        lowered = limitations.body.lower()
        return chart_id in lowered

    for intent, chart_ids in SECTION_CHART_BINDINGS.items():
        if intent not in required_intents:
            continue
        section = section_matching(sections, intent)
        for chart_id in chart_ids:
            if chart_id not in manifest_ids:
                continue
            placed_inside = (
                section is not None
                and any(
                    _chart_id_of(reference) == chart_id
                    for reference in _CHART_FILE_RE.findall(section.body)
                )
            )
            if not placed_inside:
                findings.append(
                    {
                        "finding_type": "chart_placement_missing",
                        "gate_id": "final_abstract_quality",
                        "category": "chart_placement",
                        "severity": "high",
                        "blocking_level": "blocking",
                        "repair_owner": "editor",
                        "stage_id": "editor",
                        "artifact_id": "audited_brief",
                        "claim_id": None,
                        "source_id": None,
                        "description": (
                            f"Chart '{chart_id}' belongs in the section for "
                            f"intent '{intent}' but is not placed there."
                        ),
                        "recommendation": (
                            "Move the chart image into its bound section, or "
                            "disclose the omission in the coverage/limitations "
                            "section."
                        ),
                        "metadata": {
                            "chart_id": chart_id,
                            "section_intent": intent,
                        },
                    }
                )
    for chart_id in manifest_ids:
        if chart_id in placements:
            continue
        if chart_id in bound_ids or _gap_disclosed(chart_id):
            continue
        findings.append(
            {
                "finding_type": "chart_omitted_silently",
                "gate_id": "final_abstract_quality",
                "category": "chart_placement",
                "severity": "high",
                "blocking_level": "blocking",
                "repair_owner": "editor",
                "stage_id": "editor",
                "artifact_id": "audited_brief",
                "claim_id": None,
                "source_id": None,
                "description": (
                    f"Chart '{chart_id}' was rendered by the frozen manifest "
                    "but appears nowhere in the brief and is not disclosed "
                    "as omitted."
                ),
                "recommendation": (
                    "Place the chart in a suitable section or disclose the "
                    "omission in the coverage/limitations section."
                ),
                "metadata": {"chart_id": chart_id},
            }
        )
    return findings


__all__ = [
    "SECTION_CHART_BINDINGS",
    "chart_placement_findings",
    "manifest_chart_ids",
]
