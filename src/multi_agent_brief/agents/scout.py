from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.agents.base import BaseAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AgentOutput, CandidateItem, Claim, PipelineContext, SourceItem
from multi_agent_brief.sources.content_quality import is_low_quality_snippet, sanitize_snippet


class ScoutAgent(BaseAgent):
    name = "scout"

    def run(self, context: PipelineContext, ledger: ClaimLedger) -> AgentOutput:
        # Use provider-collected sources if already available (set by pipeline._collect_sources).
        # Only fall back to loading local files when no sources were collected.
        if context.sources:
            sources = context.sources
        else:
            sources = load_local_sources(Path(context.input_dir))
            context.sources = sources

        candidates: list[CandidateItem] = []
        for source in sources:
            # Skip placeholder sources that have no real content to extract
            if _is_placeholder(source):
                continue

            if source.source_type == "web_search":
                # Web search: at most one claim per SourceItem from title + snippet
                claim = _extract_web_search_claim(source, ledger, self.name)
                if claim is not None:
                    item_id = f"{source.source_id}_ITEM_001"
                    candidate = CandidateItem(
                        item_id=item_id,
                        title=source.title[:80] if source.title else claim.statement[:80],
                        summary=claim.statement,
                        source_id=source.source_id,
                        topic=infer_topic(claim.statement),
                        importance=infer_importance(claim.statement),
                        reason_for_inclusion="Web search result with reportable signal.",
                    )
                    candidates.append(candidate)
                    ledger.add_claim(claim)
            else:
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
                        source_url=source.url,
                        source_type=source.source_type,
                        claim_type=source.metadata.get("claim_type") or infer_claim_type(statement),
                        confidence="medium",
                        created_by=self.name,
                        metadata={
                            "candidate_item_id": item_id,
                            "published_at": source.published_at,
                            "retrieved_at": source.retrieved_at,
                            "source_name": source.source_name,
                            "source_tier": source.metadata.get("source_tier", ""),
                        },
                    )
                    candidates.append(candidate)
                    ledger.add_claim(claim)

        context.candidates = candidates
        return AgentOutput(
            agent_name=self.name,
            summary=f"Loaded {len(sources)} sources and created {len(candidates)} candidate claims.",
            artifacts={"source_count": len(sources), "candidate_count": len(candidates)},
        )


def _is_placeholder(source: SourceItem) -> bool:
    """Return True if source should be skipped (placeholder, error, low quality)."""
    if source.metadata.get("requires_fetch"):
        return True
    if source.metadata.get("error_type"):
        return True
    if source.metadata.get("low_quality"):
        return True
    if source.metadata.get("filtered_reason"):
        return True
    if source.metadata.get("ingestion_status") == "placeholder":
        return True
    if source.source_type.endswith("_error"):
        return True
    if source.source_type == "manual_url" and source.content.startswith("Manual URL source:"):
        return True
    return False


def _extract_web_search_claim(source: SourceItem, ledger: ClaimLedger, agent_name: str) -> Claim | None:
    """Extract at most one claim from a web_search SourceItem.

    Combines title and snippet, sanitizes, and checks quality.
    Returns None if the snippet is low quality.
    """
    title = (source.title or "").strip()
    snippet = (source.content or "").strip()

    # Check snippet quality
    if is_low_quality_snippet(snippet):
        return None

    sanitized = sanitize_snippet(snippet)
    if not sanitized:
        return None

    # Build statement from title + snippet
    if title and sanitized:
        statement = f"{title}: {sanitized}"
    elif sanitized:
        statement = sanitized
    else:
        return None

    claim_id = ledger.build_claim_id(statement, source.source_id)
    return Claim(
        claim_id=claim_id,
        statement=statement,
        source_id=source.source_id,
        evidence_text=sanitized,
        source_url=source.url,
        source_type=source.source_type,
        claim_type=infer_claim_type(statement),
        confidence="medium",
        created_by=agent_name,
        metadata={
            "published_at": source.published_at,
            "retrieved_at": source.retrieved_at,
            "source_name": source.source_name,
            "source_tier": source.metadata.get("source_tier", ""),
            "backend": source.metadata.get("backend", ""),
            "query": source.metadata.get("query", ""),
        },
    )


def load_local_sources(input_dir: Path) -> list[SourceItem]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    sources: list[SourceItem] = []
    for path in sorted(input_dir.iterdir()):
        if path.is_dir() or path.name.startswith("."):
            continue
        if path.name.lower() == "readme.md":
            continue
        if path.suffix.lower() not in {".md", ".txt", ".json"}:
            continue

        content = path.read_text(encoding="utf-8")
        url = ""
        published_at = ""
        source_tier = ""
        claim_type = ""
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(content)
                url = str(parsed.get("source_url", ""))
                published_at = str(parsed.get("published_at", ""))
                source_tier = str(parsed.get("source_tier", ""))
                claim_type = str(parsed.get("claim_type", ""))
                if isinstance(parsed.get("items"), list):
                    content = "\n".join(str(item) for item in parsed["items"])
                elif parsed.get("content"):
                    content = str(parsed["content"])
            except json.JSONDecodeError:
                pass

        source_id = path.stem.upper().replace("-", "_")
        title = path.stem.replace("_", " ").title()
        sources.append(
            SourceItem(
                source_id=source_id,
                source_name=title,
                source_type="local_file",
                title=title,
                content=content,
                url=url,
                published_at=published_at,
                metadata={"path": str(path), "source_tier": source_tier, "claim_type": claim_type},
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
