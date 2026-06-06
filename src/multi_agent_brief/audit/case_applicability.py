"""Comparable Case Applicability audit (v0.5.3 PR 3).

Enforces boundaries on analogous/comparable evidence:
1. evidence_relation=analogous MUST have applicability_reason.
2. Single comparable case can only support hypothesis/to_verify, NOT strong action.
3. No local direct evidence → must generate verification_path.
"""
from __future__ import annotations

from dataclasses import dataclass

from multi_agent_brief.analysis_blocks.schemas import AnalysisBlock
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


@dataclass
class CaseApplicabilityFinding:
    """One audit finding for case applicability."""

    finding_type: str
    severity: str  # "warning" or "fail"
    block_id: str
    claim_id: str
    description: str
    recommendation: str


def audit_case_applicability(
    blocks: list[AnalysisBlock],
    ledger: ClaimLedger,
) -> list[CaseApplicabilityFinding]:
    """Audit case applicability across all analysis blocks.

    Returns findings for:
    - analogous claims without applicability_reason
    - single comparable case supporting strong action
    - missing verification_path when no local direct evidence
    """
    findings: list[CaseApplicabilityFinding] = []

    for block in blocks:
        _check_analogous_applicability(block, ledger, findings)
        _check_single_case_action(block, ledger, findings)
        _check_verification_path(block, findings)

    return findings


def _check_analogous_applicability(
    block: AnalysisBlock,
    ledger: ClaimLedger,
    findings: list[CaseApplicabilityFinding],
) -> None:
    """Rule 1: analogous claims MUST have applicability_reason."""
    for cid in block.case_claim_ids:
        claim = ledger.get_claim(cid)
        if not claim:
            continue
        if claim.evidence_relation == "analogous" and not claim.applicability_reason.strip():
            findings.append(CaseApplicabilityFinding(
                finding_type="missing_applicability_reason",
                severity="warning",
                block_id=block.block_id,
                claim_id=cid,
                description=(
                    f"Claim {cid} uses analogous evidence but has no applicability_reason. "
                    "Readers cannot judge when this comparison applies."
                ),
                recommendation=(
                    "Add applicability_reason explaining why this comparable case is relevant "
                    "and what dimensions are not comparable."
                ),
            ))


def _check_single_case_action(
    block: AnalysisBlock,
    ledger: ClaimLedger,
    findings: list[CaseApplicabilityFinding],
) -> None:
    """Rule 2: single comparable case cannot support strong action."""
    # Check two scenarios:
    # A) action_claim_ids contain analogous evidence (direct action on analogy)
    # B) case_claim_ids contain epistemic_type=action (builder classified by evidence_relation first)
    if not block.case_claim_ids:
        return
    if block.fact_claim_ids:
        # There are direct facts, so analogous evidence is supplementary
        return

    # Scenario A: explicit action claims with analogous evidence
    for action_cid in block.action_claim_ids:
        action_claim = ledger.get_claim(action_cid)
        if not action_claim:
            continue
        if action_claim.evidence_relation == "analogous":
            findings.append(CaseApplicabilityFinding(
                finding_type="analogous_evidence_supports_action",
                severity="fail",
                block_id=block.block_id,
                claim_id=action_cid,
                description=(
                    f"Action claim {action_cid} is supported only by comparable/analogous evidence "
                    "with no direct fact backing. Single comparable cases cannot support strong actions."
                ),
                recommendation=(
                    "Downgrade to 'to_verify' or add direct evidence. "
                    "Comparable cases can support hypothesis or trend judgment, not actionable recommendations."
                ),
            ))

    # Scenario B: case claims that were originally action-type (builder put them in case due to analogous)
    for case_cid in block.case_claim_ids:
        case_claim = ledger.get_claim(case_cid)
        if not case_claim:
            continue
        if case_claim.epistemic_type == "action":
            findings.append(CaseApplicabilityFinding(
                finding_type="analogous_evidence_supports_action",
                severity="fail",
                block_id=block.block_id,
                claim_id=case_cid,
                description=(
                    f"Claim {case_cid} has epistemic_type='action' but evidence_relation='analogous'. "
                    "It was classified as a case (correct), but an action claim based solely on "
                    "comparable evidence cannot support a strong recommendation."
                ),
                recommendation=(
                    "Downgrade epistemic_type to 'hypothesis' or 'interpreted', "
                    "or add direct local evidence to support the action."
                ),
            ))


def _check_verification_path(
    block: AnalysisBlock,
    findings: list[CaseApplicabilityFinding],
) -> None:
    """Rule 3: no local direct evidence → must have verification_path."""
    has_direct_fact = bool(block.fact_claim_ids)
    has_case = bool(block.case_claim_ids)
    has_verification = bool(block.verification_path.strip())

    if has_case and not has_direct_fact and not has_verification:
        findings.append(CaseApplicabilityFinding(
            finding_type="missing_verification_path",
            severity="warning",
            block_id=block.block_id,
            claim_id="",
            description=(
                f"Block '{block.title}' uses comparable cases but has no direct local facts "
                "and no verification_path. Readers don't know how to validate the comparison."
            ),
            recommendation=(
                "Add verification_path describing what data or action would confirm "
                "whether the comparable case applies locally."
            ),
        ))


def format_case_applicability_report(findings: list[CaseApplicabilityFinding]) -> str:
    """Format findings into a readable report."""
    if not findings:
        return "Case applicability audit: all checks passed.\n"

    lines = ["Case Applicability Audit Report", "=" * 34, ""]
    for f in findings:
        icon = "❌" if f.severity == "fail" else "⚠️"
        lines.append(f"{icon} [{f.severity.upper()}] {f.finding_type}")
        lines.append(f"   Block: {f.block_id}")
        if f.claim_id:
            lines.append(f"   Claim: {f.claim_id}")
        lines.append(f"   {f.description}")
        lines.append(f"   → {f.recommendation}")
        lines.append("")

    fails = sum(1 for f in findings if f.severity == "fail")
    warns = sum(1 for f in findings if f.severity == "warning")
    lines.append(f"Result: {fails} fail(s), {warns} warning(s)")
    return "\n".join(lines)
