from __future__ import annotations

from collections import defaultdict

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
from multi_agent_brief.analysis_blocks.renderer import render_analysis_blocks
from multi_agent_brief.audit.case_applicability import audit_case_applicability
from multi_agent_brief.audit.limitation_hygiene import audit_limitation_hygiene
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, BriefSection, PipelineContext

# Must cover all topics used by Screener (selection.py TOPIC_CAPS).
# Any topic not in this list is appended at the end so claims are never silently dropped.
TOPIC_ORDER: list[str] = [
    "policy",
    "compliance",
    "earnings",
    "competitor",
    "market",
    "demand",
    "rates",
    "capital",
    "technology",
    "general",
]


class AnalystAgent(BaseAgent):
    name = "analyst"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        # Build AnalysisBlocks for epistemic classification (v0.5.3 PR 1+2)
        analysis_blocks = build_analysis_blocks(ledger)

        # Store blocks in context metadata for formatter export
        context.metadata["analysis_blocks"] = [b.to_dict() for b in analysis_blocks]

        # Run limitation hygiene audit (v0.5.3 PR 4)
        lh_report = audit_limitation_hygiene(analysis_blocks, ledger)
        context.metadata["limitation_hygiene_report"] = lh_report.to_dict()

        # Run case applicability audit (v0.5.3 PR 3)
        ca_findings = audit_case_applicability(analysis_blocks, ledger)
        context.metadata["case_applicability_findings"] = [
            {"finding_type": f.finding_type, "severity": f.severity,
             "block_id": f.block_id, "claim_id": f.claim_id,
             "description": f.description, "recommendation": f.recommendation}
            for f in ca_findings
        ]

        # Render structured draft using AnalysisBlocks
        structured_draft = render_analysis_blocks(
            analysis_blocks,
            ledger,
            project_name=context.project_name,
            audience=context.audience,
            language=context.language,
        )

        # Also build legacy BriefSection list for backward compatibility
        grouped: dict[str, list] = defaultdict(list)
        for claim in ledger:
            grouped[claim.metadata.get("topic") or infer_section(claim.statement)].append(claim)

        known_set = set(TOPIC_ORDER)
        extra_topics = sorted(t for t in grouped if t not in known_set)
        all_topics = TOPIC_ORDER + extra_topics

        sections: list[BriefSection] = []
        for topic in all_topics:
            claims = grouped.get(topic, [])
            if not claims:
                continue
            title = topic.replace("_", " ").title()
            lines = []
            claim_ids = []
            for claim in claims:
                claim.used_in_sections.append(title)
                claim_ids.append(claim.claim_id)
                date_str = claim.metadata.get("published_at") or claim.metadata.get("retrieved_at", "")
                date_prefix = f"{date_str}｜" if date_str else ""
                lines.append(f"- {date_prefix}{claim.statement} [src:{claim.claim_id}]")
            sections.append(BriefSection(title=title, body="\n".join(lines), claim_ids=claim_ids))

        if not sections:
            sections.append(BriefSection(title="No Reportable Signals", body="No candidate claims were found."))

        # Epistemic blocks are intermediate governance artifacts, NOT reader-facing.
        # Stored in metadata for analysis_blocks.json export, MAS shared-world use,
        # and downstream debugging/audit tooling that reads PipelineContext.metadata.
        context.metadata["epistemic_draft"] = structured_draft

        # The reader-facing brief uses the legacy prose format with executive summary.
        context.report_state.sections = sections
        context.report_state.draft_markdown = render_draft(context.project_name, sections)
        return AgentOutput(
            agent_name=self.name,
            summary=f"Generated draft with {len(analysis_blocks)} analysis blocks, {len(sections)} sections.",
            artifacts={
                "section_count": len(sections),
                "analysis_block_count": len(analysis_blocks),
            },
        )


def infer_section(statement: str) -> str:
    lowered = statement.lower()
    if any(word in lowered for word in ["policy", "tariff", "regulation"]):
        return "policy"
    if any(word in lowered for word in ["compliance", "uflpa", "forced labor"]):
        return "compliance"
    if any(word in lowered for word in ["revenue", "margin", "earnings"]):
        return "earnings"
    if any(word in lowered for word in ["competitor", "capacity", "launch"]):
        return "competitor"
    if any(word in lowered for word in ["market", "price", "demand"]):
        return "market"
    if any(word in lowered for word in ["installation", "generation", "ppa"]):
        return "demand"
    if any(word in lowered for word in ["treasury", "yield", "sofr", "fed", "rate"]):
        return "rates"
    if any(word in lowered for word in ["acquisition", "investment", "fund", "capital"]):
        return "capital"
    if any(word in lowered for word in ["topcon", "hjt", "technology", "efficiency"]):
        return "technology"
    return "general"


def render_draft(project_name: str, sections: list[BriefSection]) -> str:
    lines = [f"# {project_name}", "", "## Executive Summary", ""]
    for section in sections:
        first_line = section.body.splitlines()[0] if section.body else ""
        lines.append(f"- {section.title}: {first_line.removeprefix('- ').strip()}")
    lines.append("")
    for section in sections:
        lines.extend([f"## {section.title}", "", section.body, ""])
    return "\n".join(lines).strip() + "\n"
