from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

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

# Metadata key a runtime auditor uses to record the calibration label on a row.
SEMANTIC_SUPPORT_CALIBRATION_METADATA_KEY = "calibration_label"

# AuditFinding.finding_type for advisory semantic-support proposals. A distinct
# type keeps these findings recognizable as proposal-only: they never gate,
# deliver, or authorize release, and no deterministic gate reads them.
SEMANTIC_SUPPORT_PROPOSAL_FINDING_TYPE = "semantic_support_proposal"


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
            "Record the support-calibration label in row metadata under "
            f'"{SEMANTIC_SUPPORT_CALIBRATION_METADATA_KEY}" using one of: '
            f"{proposal_labels}.\n"
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


def semantic_support_proposal_finding(proposal_row: Mapping[str, Any]) -> AuditFinding:
    """Convert one projected semantic-support proposal row to an advisory finding.

    This is a pure conversion. The finding is advisory only: it always carries a
    ``low`` severity and a non-blocking level so it can never flip a merged
    AuditReport to warning/fail or open a repair route. The distinct
    ``semantic_support_proposal`` finding_type keeps it recognizable as
    proposal-only. This function does not read or write the Claim-Support Matrix,
    workflow state, gate reports, or delivery files.
    """

    proposal_id = _text(proposal_row.get("proposal_id")) or _text(proposal_row.get("source_row_id"))
    claim_id = _text(proposal_row.get("claim_id"))
    atom_id = _text(proposal_row.get("atom_id"))
    support_label = _text(proposal_row.get("proposed_support_label"))
    calibration_label = _calibration_label(proposal_row)
    requires_adjudication = proposal_row.get("requires_human_adjudication") is True

    description = (
        f"Semantic support proposal for {claim_id or '<unmatched-claim>'}"
        f"/{atom_id or '<unmatched-atom>'}: "
        f"proposed_support_label={support_label or '<none>'}"
    )
    if calibration_label:
        description += f", calibration_label={calibration_label}"

    recommendation = (
        "Advisory proposal only — not a gate, delivery, or release decision."
    )
    if requires_adjudication:
        recommendation += " Human adjudication required."

    return AuditFinding(
        finding_id=proposal_id or "SAR-UNKNOWN",
        severity="low",
        finding_type=SEMANTIC_SUPPORT_PROPOSAL_FINDING_TYPE,
        related_claim_id=claim_id,
        description=description,
        recommendation=recommendation,
        evidence=_proposal_evidence(proposal_row, calibration_label=calibration_label),
        blocking_level="editor_fixable",
        repair_owner="editor",
    )


def findings_from_semantic_proposal_rows(
    proposal_rows: Iterable[Any],
) -> list[AuditFinding]:
    """Adapt projected proposal rows into advisory AuditFindings (pure conversion)."""

    return [
        semantic_support_proposal_finding(row)
        for row in proposal_rows
        if isinstance(row, Mapping)
    ]


def _calibration_label(proposal_row: Mapping[str, Any]) -> str:
    metadata = proposal_row.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return _text(metadata.get(SEMANTIC_SUPPORT_CALIBRATION_METADATA_KEY))


def _proposal_evidence(proposal_row: Mapping[str, Any], *, calibration_label: str) -> str:
    span = _text(proposal_row.get("evidence_span_id"))
    if not span:
        candidates = proposal_row.get("candidate_evidence_span_ids")
        if isinstance(candidates, (list, tuple)):
            span = ",".join(_text(candidate) for candidate in candidates if _text(candidate))
    parts = [
        f"assessment_method={_text(proposal_row.get('assessment_method')) or '<none>'}",
        f"assessor_id={_text(proposal_row.get('assessor_id')) or '<none>'}",
        f"confidence={_confidence_text(proposal_row.get('confidence'))}",
        f"uncertainty={_text(proposal_row.get('uncertainty')) or '<none>'}",
        f"disagreement={_text(proposal_row.get('disagreement')) or '<none>'}",
        f"requires_human_adjudication={proposal_row.get('requires_human_adjudication') is True}",
        f"proposed_support_label={_text(proposal_row.get('proposed_support_label')) or '<none>'}",
        f"calibration_label={calibration_label or '<none>'}",
        f"evidence_span={span or '<unmatched>'}",
    ]
    rationale = _text(proposal_row.get("proposed_support_reason"))
    if rationale:
        parts.append(f"rationale={rationale}")
    return "; ".join(parts)


def _confidence_text(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "<none>"
    return str(value)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""

