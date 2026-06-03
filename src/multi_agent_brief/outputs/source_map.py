from __future__ import annotations

from multi_agent_brief.core.claim_ledger import ClaimLedger


def render_source_map(ledger: ClaimLedger) -> str:
    lines = ["# Source Map", ""]
    for claim in ledger:
        lines.append(f"## {claim.claim_id}")
        lines.append("")
        lines.append(f"- Statement: {claim.statement}")
        lines.append(f"- Source ID: {claim.source_id}")
        lines.append(f"- Source Type: {claim.source_type}")
        if claim.source_url:
            lines.append(f"- Source URL: {claim.source_url}")
        published_at = claim.metadata.get("published_at", "")
        retrieved_at = claim.metadata.get("retrieved_at", "")
        if published_at:
            lines.append(f"- Published At: {published_at}")
        if retrieved_at:
            lines.append(f"- Retrieved At: {retrieved_at}")
        source_name = claim.metadata.get("source_name", "")
        if source_name:
            lines.append(f"- Source Name: {source_name}")
        lines.append(f"- Evidence: {claim.evidence_text}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
