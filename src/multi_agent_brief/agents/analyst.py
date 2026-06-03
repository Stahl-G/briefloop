from __future__ import annotations

from collections import defaultdict

from multi_agent_brief.agents.base import BaseAgent
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
        grouped: dict[str, list] = defaultdict(list)
        for claim in ledger:
            grouped[claim.metadata.get("topic") or infer_section(claim.statement)].append(claim)

        # Build ordered topic list: known topics first, then any extra topics from the ledger
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
                lines.append(f"- {claim.statement} [src:{claim.claim_id}]")
            sections.append(BriefSection(title=title, body="\n".join(lines), claim_ids=claim_ids))

        if not sections:
            sections.append(BriefSection(title="No Reportable Signals", body="No candidate claims were found."))

        draft = render_draft(context.project_name, sections)
        context.report_state.sections = sections
        context.report_state.draft_markdown = draft
        return AgentOutput(
            agent_name=self.name,
            summary=f"Generated draft with {len(sections)} sections.",
            artifacts={"section_count": len(sections)},
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
