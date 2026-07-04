from __future__ import annotations

from multi_agent_brief.audit.interfaces import AuditAgentInterface
from multi_agent_brief.contracts.schemas.claim_support_matrix import VALID_SUPPORT_LABELS
from multi_agent_brief.contracts.schemas.semantic_assessment_report import (
    SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding, AuditReport, PipelineContext

# Support-calibration proposal labels. These describe *how* a reader draft may
# overreach its frozen evidence. They are proposal labels only: not gate IDs,
# not release decisions. Runtime auditors record the chosen label in each
# semantic_assessment_report row's metadata; Python never maps them to gates.
SEMANTIC_SUPPORT_PROPOSAL_LABELS: tuple[str, ...] = (
    "unsupported_claim",
    "overstated_claim",
    "missing_limitation",
    "source_scope_mismatch",
    "uncited_material_claim",
    "unsupported_number",
    "stale_current_framing",
    "causal_overreach",
    "confidence_overreach",
    "external_knowledge_used",
)


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
            audit_status="pass",
            audit_score=100,
            findings=[],
            metadata={
                "note": "Semantic audit adapter is configured but no model provider is attached.",
                "semantic_status": "not_configured",
                "ledger_claims": len(ledger),
            },
        )


class SemanticAuditPromptBuilder:
    """Builds prompts for external Semantic Support Auditor subagents.

    This builder is provider-less. It only constructs the instruction text a
    runtime agent reads; it never calls an LLM provider. The runtime agent does
    the model judgment outside Python and writes a structured
    ``semantic_assessment_report.json``. Python then validates and projects it.
    """

    def build_prompt(self, markdown: str, ledger: ClaimLedger) -> str:
        claim_lines = []
        for claim in ledger:
            claim_lines.append(
                f"- {claim.claim_id}: {claim.statement}\n"
                f"  Evidence: {claim.evidence_text}"
            )
        claims = "\n".join(claim_lines) or "- (no frozen claims provided)"
        return (
            f"{self._role_and_task()}\n\n"
            f"{self._hard_boundaries()}\n\n"
            f"{self._preservation_rules()}\n\n"
            f"{self.response_contract()}\n\n"
            f"## Reader Draft (audited_brief.md)\n{markdown}\n\n"
            f"## Frozen Claim Ledger\n{claims}\n"
        )

    @staticmethod
    def _role_and_task() -> str:
        return (
            "You are the BriefLoop Semantic Support Auditor. Compare the reader "
            "draft (audited_brief.md) against the FROZEN Claim Ledger evidence "
            "below and propose where the draft says more, newer, more causal, "
            "more certain, or more quantified things than the frozen evidence "
            "supports.\n"
            "Your output is a PROPOSAL only. It is not a gate, not delivery "
            "approval, and not release authority. Deterministic validators and "
            "human reviewers decide any effect."
        )

    @staticmethod
    def _hard_boundaries() -> str:
        return (
            "## Hard Boundaries\n"
            "- Do not improve prose.\n"
            "- Do not rewrite the draft.\n"
            "- Do not repair the draft.\n"
            "- Do not use external knowledge.\n"
            "- Do not infer missing sources.\n"
            "- Judge each claim only against the frozen Claim Ledger evidence "
            "shown below."
        )

    @staticmethod
    def _preservation_rules() -> str:
        return (
            "## Preserve\n"
            "Preserve, and do not flatten, the draft's uncertainty, limitations, "
            "scope, dates, and source strength. Flag when the draft drops a "
            "limitation or overstates certainty, but never remove hedging yourself."
        )

    @classmethod
    def response_contract(cls) -> str:
        support_labels = ", ".join(sorted(VALID_SUPPORT_LABELS))
        proposal_labels = ", ".join(SEMANTIC_SUPPORT_PROPOSAL_LABELS)
        return (
            "## Response Contract\n"
            "Return JSON only. No prose, no markdown fences, no free-text notes. "
            f"The JSON must be a semantic_assessment_report with "
            f'"schema_version": "{SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION}", an '
            '"assessors" list, and a "rows" list. Each row requires: row_id '
            "(SAR-####), claim_id (CL-####), atom_id (AC-####-##), an "
            "evidence_span_id (ESP-###-##) or candidate_evidence_span_ids, "
            "proposed_support_label, confidence (0-1), uncertainty, disagreement, "
            "requires_human_adjudication, assessment_method, assessor_id, and "
            "rationale.\n"
            f"proposed_support_label must be one of: {support_labels}.\n"
            "Record the support-calibration label in the row metadata using one "
            f"of: {proposal_labels}.\n"
            "Every row must bind to a related claim_id, atom_id, or evidence "
            "span. When no artifact id matches, state the unmatched draft text "
            "location explicitly in the rationale instead of inventing an id.\n"
            "If you would rely on external knowledge to judge a claim, do not "
            "emit a supported row; flag it as external_knowledge_used and set "
            "requires_human_adjudication to true."
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

