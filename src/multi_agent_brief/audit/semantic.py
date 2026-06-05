from __future__ import annotations

from multi_agent_brief.audit.interfaces import AuditAgentInterface
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding, AuditReport, PipelineContext


class NoOpSemanticAuditAgent(AuditAgentInterface):
    """Semantic audit placeholder for model-backed implementations.

    Returns a distinct status so downstream consumers can tell this
    is NOT a real audit pass — it's an unconfigured placeholder.
    """

    name = "noop-semantic-auditor"

    def run_audit(
        self,
        markdown: str,
        ledger: ClaimLedger,
        context: PipelineContext | None = None,
    ) -> AuditReport:
        return AuditReport(
            audit_status="not_configured",
            audit_score=0,
            findings=[],
            metadata={
                "note": "Semantic audit adapter is configured but no model provider is attached.",
                "semantic_status": "not_configured",
                "ledger_claims": len(ledger),
            },
        )


class SemanticAuditPromptBuilder:
    """Builds prompts for external LLM audit agents without calling a provider."""

    def build_prompt(self, markdown: str, ledger: ClaimLedger) -> str:
        claim_lines = []
        for claim in ledger:
            claim_lines.append(
                f"- {claim.claim_id}: {claim.statement}\n"
                f"  Evidence: {claim.evidence_text}"
            )
        claims = "\n".join(claim_lines)
        return (
            "You are a source-grounding auditor. Check whether each cited claim in the draft "
            "is supported by the Claim Ledger evidence. Return JSON findings only.\n\n"
            f"## Draft\n{markdown}\n\n"
            f"## Claim Ledger\n{claims}\n"
        )


def finding_from_semantic_result(
    *,
    finding_id: str,
    related_claim_id: str,
    description: str,
    evidence: str,
    severity: str = "medium",
) -> AuditFinding:
    normalized_severity = severity if severity in {"low", "medium", "high"} else "medium"
    return AuditFinding(
        finding_id=finding_id,
        severity=normalized_severity,
        finding_type="semantic_source_support",
        related_claim_id=related_claim_id,
        description=description,
        recommendation="Revise the draft so the claim stays within the cited evidence.",
        evidence=evidence,
    )

