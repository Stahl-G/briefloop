"""Render AnalysisBlocks to structured markdown.

Produces a fixed-block format instead of prose:
  Fact → Case/Comparison → Interpretation → Limitations → Action/To Verify

Different audiences get different heading labels, but the underlying
block structure is always the same.
"""
from __future__ import annotations

from multi_agent_brief.analysis_blocks.schemas import AnalysisBlock
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim

# Audience-specific heading maps
_HEADING_MAPS: dict[str, dict[str, str]] = {
    "management": {
        "fact": "What Happened",
        "case": "Comparable Context",
        "interpretation": "Why It Matters",
        "limitations": "Evidence Boundary",
        "action": "What To Do",
        "to_verify": "What To Verify Next",
    },
    "research": {
        "fact": "Observed Facts",
        "case": "Comparable Cases",
        "interpretation": "Interpretation",
        "limitations": "Limitations",
        "action": "Recommended Actions",
        "to_verify": "Pending Verification",
    },
    "default": {
        "fact": "Fact",
        "case": "Case / Comparison",
        "interpretation": "Interpretation",
        "limitations": "Limitations",
        "action": "Action",
        "to_verify": "To Verify",
    },
}


def render_analysis_blocks(
    blocks: list[AnalysisBlock],
    ledger: ClaimLedger,
    *,
    project_name: str = "",
    audience: str = "default",
    language: str = "en-US",
) -> str:
    """Render analysis blocks to structured markdown.

    Args:
        blocks: AnalysisBlock instances from builder.
        ledger: ClaimLedger for resolving claim details.
        project_name: Optional project title shown at the top.
        audience: Heading style key ("management", "research", "default").
        language: "en-US" or "zh-CN".

    Returns:
        Structured markdown string.
    """
    headings = _HEADING_MAPS.get(audience, _HEADING_MAPS["default"])
    is_zh = language.startswith("zh")

    parts: list[str] = []
    if project_name:
        parts.append(f"# {project_name}")

    sections: list[str] = []
    for block in blocks:
        section = _render_block(block, ledger, headings, is_zh)
        if section:
            sections.append(section)

    if not sections:
        return _empty_message(is_zh)

    parts.extend(sections)
    return "\n\n".join(parts) + "\n"


def _render_block(
    block: AnalysisBlock,
    ledger: ClaimLedger,
    headings: dict[str, str],
    is_zh: bool,
) -> str:
    lines: list[str] = [f"## {block.title}", ""]

    # Fact section — conditional like all other sections
    if block.fact_claim_ids:
        lines.append(f"### {headings['fact']}")
        lines.append("")
        fact_lines = _render_claim_list(block.fact_claim_ids, ledger)
        if fact_lines:
            lines.extend(fact_lines)
        else:
            lines.append(_no_fact_message(is_zh))
        lines.append("")

    # Case / Comparison section
    case_lines = _render_claim_list(block.case_claim_ids, ledger)
    if case_lines:
        lines.append(f"### {headings['case']}")
        lines.append("")
        lines.extend(case_lines)
        # Applicability note (PR 3)
        if block.applicability_note:
            lines.append("")
            lines.append(f"> **{ _applicability_label(is_zh) }:** {block.applicability_note}")
        if block.case_applicability:
            ca = block.case_applicability
            if ca.comparable_dimensions:
                lines.append(f"> **{ _comparable_label(is_zh) }:** {', '.join(ca.comparable_dimensions)}")
            if ca.non_comparable_dimensions:
                lines.append(f"> **{ _non_comparable_label(is_zh) }:** {', '.join(ca.non_comparable_dimensions)}")
            if ca.local_verification_needed:
                lines.append(f"> ⚠️ { _local_verify_label(is_zh) }")
        lines.append("")

    # Interpretation section
    interp_lines = _render_claim_list(block.interpretation_claim_ids, ledger)
    if interp_lines:
        lines.append(f"### {headings['interpretation']}")
        lines.append("")
        lines.extend(interp_lines)
        lines.append("")

    # Limitations section
    limit_lines = _render_limitations(block, ledger, is_zh)
    if limit_lines:
        lines.append(f"### {headings['limitations']}")
        lines.append("")
        lines.extend(limit_lines)
        lines.append("")

    # Action section (only claims with direct evidence)
    action_lines = _render_claim_list(block.action_claim_ids, ledger)
    if action_lines:
        lines.append(f"### {headings['action']}")
        lines.append("")
        lines.extend(action_lines)
        lines.append("")

    # To Verify section
    verify_lines = _render_claim_list(block.to_verify_claim_ids, ledger)
    if verify_lines:
        lines.append(f"### {headings['to_verify']}")
        lines.append("")
        lines.extend(verify_lines)
        if block.verification_path:
            lines.append("")
            lines.append(f"> **{ _verification_path_label(is_zh) }:** {block.verification_path}")
        lines.append("")

    # Confidence footer (qualitative label, not percentage — avoids audit false positives)
    lines.append(f"_{ _confidence_label(is_zh) }: { _confidence_level(block.confidence, is_zh) }_")
    lines.append("")

    return "\n".join(lines)


def _render_claim_list(claim_ids: list[str], ledger: ClaimLedger) -> list[str]:
    lines = []
    for cid in claim_ids:
        claim = ledger.get_claim(cid)
        if claim:
            date_str = claim.metadata.get("published_at") or claim.metadata.get("retrieved_at", "")
            date_prefix = f"{date_str} | " if date_str else ""
            lines.append(f"- {date_prefix}{claim.statement} [src:{cid}]")
    return lines


def _render_limitations(block: AnalysisBlock, ledger: ClaimLedger, is_zh: bool) -> list[str]:
    lines = []
    seen: set[str] = set()
    for cid in block.limitation_claim_ids:
        claim = ledger.get_claim(cid)
        if not claim:
            continue
        for lim in claim.limitations:
            normalized = lim.strip().lower()
            if normalized not in seen:
                seen.add(normalized)
                lines.append(f"- {lim}")
    return lines


def _no_fact_message(is_zh: bool) -> str:
    if is_zh:
        return "_未找到本期直接事实依据。_"
    return "_No current-period direct fact found._"


def _empty_message(is_zh: bool) -> str:
    if is_zh:
        return "# 无可报告信号\n\n未发现符合条件的事实声明。\n"
    return "# No Reportable Signals\n\nNo candidate claims were found.\n"


def _applicability_label(is_zh: bool) -> str:
    return "适用性说明" if is_zh else "Applicability"


def _comparable_label(is_zh: bool) -> str:
    return "可比维度" if is_zh else "Comparable Dimensions"


def _non_comparable_label(is_zh: bool) -> str:
    return "不可比维度" if is_zh else "Non-Comparable Dimensions"


def _local_verify_label(is_zh: bool) -> str:
    return "需要本地验证" if is_zh else "Local verification needed"


def _verification_path_label(is_zh: bool) -> str:
    return "验证路径" if is_zh else "Verification Path"


def _confidence_label(is_zh: bool) -> str:
    return "置信度" if is_zh else "Confidence"


def _confidence_level(score: float, is_zh: bool) -> str:
    """Map numeric confidence to qualitative label.

    Avoids precise percentages that trigger audit false positives
    (e.g. '100%' flagged as number_without_source).
    """
    if score >= 0.8:
        return "高" if is_zh else "High"
    if score >= 0.5:
        return "中" if is_zh else "Medium"
    return "低" if is_zh else "Low"
