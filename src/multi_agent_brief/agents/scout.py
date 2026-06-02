from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, CandidateItem, Claim, PipelineContext, SourceItem


class ScoutAgent(BaseAgent):
    name = "scout"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        sources = load_local_sources(Path(context.input_dir))
        context.sources = sources

        candidates: list[CandidateItem] = []
        for source in sources:
            for index, statement in enumerate(extract_candidate_lines(source.content), start=1):
                item_id = f"{source.source_id}_ITEM_{index:03d}"
                claim_id = ledger.build_claim_id(statement, source.source_id)
                candidate = CandidateItem(
                    item_id=item_id,
                    title=statement[:80],
                    summary=statement,
                    source_id=source.source_id,
                    topic=infer_topic(statement),
                    importance=infer_importance(statement),
                    reason_for_inclusion="Contains a reportable business, market, policy, or risk signal.",
                )
                claim = Claim(
                    claim_id=claim_id,
                    statement=statement,
                    source_id=source.source_id,
                    evidence_text=statement,
                    source_url=source.source_url,
                    source_type=source.source_type,
                    claim_type=infer_claim_type(statement),
                    confidence="medium",
                    created_by=self.name,
                    metadata={"candidate_item_id": item_id, "published_at": source.published_at},
                )
                candidates.append(candidate)
                ledger.add_claim(claim)

        context.candidates = candidates
        return AgentOutput(
            agent_name=self.name,
            summary=f"Loaded {len(sources)} sources and created {len(candidates)} candidate claims.",
            artifacts={"source_count": len(sources), "candidate_count": len(candidates)},
        )


def load_local_sources(input_dir: Path) -> list[SourceItem]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    sources: list[SourceItem] = []
    for path in sorted(input_dir.iterdir()):
        if path.is_dir() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in {".md", ".txt", ".json"}:
            continue

        content = path.read_text(encoding="utf-8")
        source_url = ""
        published_at = ""
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(content)
                source_url = str(parsed.get("source_url", ""))
                published_at = str(parsed.get("published_at", ""))
                if isinstance(parsed.get("items"), list):
                    content = "\n".join(str(item) for item in parsed["items"])
                elif parsed.get("content"):
                    content = str(parsed["content"])
            except json.JSONDecodeError:
                pass

        source_id = path.stem.upper().replace("-", "_")
        sources.append(
            SourceItem(
                source_id=source_id,
                title=path.stem.replace("_", " ").title(),
                source_type="local_file",
                content=content,
                source_url=source_url,
                published_at=published_at,
                metadata={"path": str(path)},
            )
        )
    return sources


def extract_candidate_lines(content: str) -> list[str]:
    candidates: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", "*")):
            line = line[1:].strip()
        if len(line) < 25:
            continue
        candidates.append(line)
    return candidates


def infer_topic(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["policy", "tariff", "regulation", "sec", "filing"]):
        return "policy"
    if any(word in lowered for word in ["revenue", "margin", "earnings", "cash flow"]):
        return "earnings"
    if any(word in lowered for word in ["competitor", "launch", "capacity", "plant"]):
        return "competitor"
    if any(word in lowered for word in ["price", "demand", "inventory", "market"]):
        return "market"
    return "general"


def infer_importance(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["material", "major", "risk", "tariff", "guidance"]):
        return "high"
    if any(word in lowered for word in ["watch", "monitor", "possible"]):
        return "medium"
    return "medium"


def infer_claim_type(text: str) -> str:
    lowered = text.lower()
    if any(char.isdigit() for char in text):
        return "number"
    if any(word in lowered for word in ["risk", "uncertainty"]):
        return "risk"
    if any(word in lowered for word in ["expects", "forecast", "guidance"]):
        return "forecast"
    return "fact"
