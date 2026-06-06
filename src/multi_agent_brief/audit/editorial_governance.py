"""Editorial Governance Rule Packs — quality checks for editorial standards.

This module implements editorial governance checks that ensure:
- Factual density meets profile thresholds
- Business advice is supported by evidence
- Comparable cases include applicability and limitations
- Historical analogies are not presented as current facts
- Must-preserve facts are not removed during editing
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding, AuditReport, PipelineContext


@dataclass
class EditorialGovernanceConfig:
    """Configuration for editorial governance checks."""

    # Factual density thresholds
    min_claims_per_1000_chars: float = 2.0
    min_claims_per_section: int = 1

    # Business advice checks
    require_evidence_for_advice: bool = True

    # Comparable case checks
    require_applicability_for_analogies: bool = True
    require_limitations_for_analogies: bool = True

    # Historical analogy checks
    prevent_historical_as_current: bool = True

    # Must-preserve fact checks
    track_must_preserve_facts: bool = True

    # Profile-aware thresholds
    quiet_week: bool = False
    allow_quiet_week_exception: bool = False


# Patterns for detecting business advice
BUSINESS_ADVICE_PATTERNS = [
    re.compile(r"\b(should|must|need to|ought to|recommend|suggest|advise)\b", re.IGNORECASE),
    re.compile(r"\b(invest|buy|sell|hold|acquire|divest|merge)\b", re.IGNORECASE),
    re.compile(r"\b(strategy|strategic|tactic|tactical|initiative)\b", re.IGNORECASE),
]

# Patterns for detecting historical analogies
HISTORICAL_PATTERNS = [
    re.compile(r"\b(historically|in the past|previously|formerly|once|used to)\b", re.IGNORECASE),
    re.compile(r"\b(lessons from|learned from|based on history|historical precedent)\b", re.IGNORECASE),
    re.compile(r"\b(similar to|just like|as we saw|as happened)\b", re.IGNORECASE),
]

# Patterns for detecting current-period framing
CURRENT_FRAMING_PATTERNS = [
    re.compile(r"\b(this week|current|latest|newly|recently|now|today)\b", re.IGNORECASE),
    re.compile(r"\b(本周|本期|当前|最新|新增|近日|目前)\b"),
]


def check_factual_density(
    markdown: str,
    claims: list[Any],
    config: EditorialGovernanceConfig,
) -> list[AuditFinding]:
    """Check if factual density meets profile thresholds.

    Args:
        markdown: The brief markdown content.
        claims: List of claims from the ledger.
        config: Editorial governance configuration.

    Returns:
        List of audit findings for factual density issues.
    """
    findings: list[AuditFinding] = []

    if not markdown or len(markdown) < 100:
        return findings

    # Calculate claims per 1000 characters
    char_count = len(markdown)
    claim_count = len(claims)
    density = (claim_count / char_count) * 1000 if char_count > 0 else 0

    # Adjust threshold for quiet week
    min_density = config.min_claims_per_1000_chars
    if config.quiet_week and config.allow_quiet_week_exception:
        min_density *= 0.5  # Lower threshold for quiet weeks

    if density < min_density:
        findings.append(AuditFinding(
            finding_id="EDITORIAL_LOW_FACTUAL_DENSITY",
            finding_type="editorial_governance",
            severity="warning",
            description=(
                f"Factual density is {density:.1f} claims per 1000 chars, "
                f"below threshold of {min_density:.1f}. "
                f"Consider adding more evidence-based statements."
            ),
            blocking_level="editor_fixable",
            repair_owner="analyst",
        ))

    return findings


def check_business_advice(
    markdown: str,
    claims: list[Any],
    config: EditorialGovernanceConfig,
) -> list[AuditFinding]:
    """Check if business advice is supported by evidence.

    Args:
        markdown: The brief markdown content.
        claims: List of claims from the ledger.
        config: Editorial governance configuration.

    Returns:
        List of audit findings for unsupported business advice.
    """
    findings: list[AuditFinding] = []

    if not config.require_evidence_for_advice:
        return findings

    # Find sentences with business advice patterns
    sentences = re.split(r'[.!?]+', markdown)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Check if sentence contains business advice
        has_advice = any(pattern.search(sentence) for pattern in BUSINESS_ADVICE_PATTERNS)
        if not has_advice:
            continue

        # Check if sentence has supporting evidence (contains claim references)
        has_evidence = "[src:" in sentence or "according to" in sentence.lower()

        if not has_evidence:
            findings.append(AuditFinding(
                finding_id="EDITORIAL_UNSUPPORTED_ADVICE",
                finding_type="editorial_governance",
                severity="high",
                description=(
                    f"Business advice found without supporting evidence: "
                    f"'{sentence[:100]}...' "
                    f"Add source citations or qualify as analyst opinion."
                ),
                blocking_level="analyst_blocking",
                repair_owner="analyst",
            ))

    return findings


def check_comparable_cases(
    claims: list[Any],
    config: EditorialGovernanceConfig,
) -> list[AuditFinding]:
    """Check if comparable cases include applicability and limitations.

    Args:
        claims: List of claims from the ledger.
        config: Editorial governance configuration.

    Returns:
        List of audit findings for comparable case issues.
    """
    findings: list[AuditFinding] = []

    for claim in claims:
        # Check if claim is an analogy or comparable case
        epistemic_type = getattr(claim, "epistemic_type", "observed")
        if epistemic_type != "analogy":
            continue

        # Check for applicability reason
        if config.require_applicability_for_analogies:
            applicability_reason = getattr(claim, "applicability_reason", "")
            if not applicability_reason:
                findings.append(AuditFinding(
                    finding_id=f"EDITORIAL_ANALOGY_NO_APPLICABILITY_{claim.claim_id}",
                    finding_type="editorial_governance",
                    severity="warning",
                    description=(
                        f"Comparable case '{claim.statement[:50]}...' lacks applicability reason. "
                        f"Explain why this analogy is relevant."
                    ),
                    blocking_level="editor_fixable",
                    repair_owner="analyst",
                    related_claim_id=claim.claim_id,
                ))

        # Check for limitations
        if config.require_limitations_for_analogies:
            limitations = getattr(claim, "limitations", [])
            if not limitations:
                findings.append(AuditFinding(
                    finding_id=f"EDITORIAL_ANALOGY_NO_LIMITATIONS_{claim.claim_id}",
                    finding_type="editorial_governance",
                    severity="warning",
                    description=(
                        f"Comparable case '{claim.statement[:50]}...' lacks limitations. "
                        f"Add caveats about where this analogy may not apply."
                    ),
                    blocking_level="editor_fixable",
                    repair_owner="analyst",
                    related_claim_id=claim.claim_id,
                ))

    return findings


def check_historical_analogies(
    markdown: str,
    claims: list[Any],
    config: EditorialGovernanceConfig,
) -> list[AuditFinding]:
    """Check if historical analogies are presented as current facts.

    Args:
        markdown: The brief markdown content.
        claims: List of claims from the ledger.
        config: Editorial governance configuration.

    Returns:
        List of audit findings for historical analogy issues.
    """
    findings: list[AuditFinding] = []

    if not config.prevent_historical_as_current:
        return findings

    # Find sentences with historical patterns
    sentences = re.split(r'[.!?]+', markdown)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Check if sentence contains historical reference
        has_historical = any(pattern.search(sentence) for pattern in HISTORICAL_PATTERNS)
        if not has_historical:
            continue

        # Check if sentence also uses current-period framing
        has_current = any(pattern.search(sentence) for pattern in CURRENT_FRAMING_PATTERNS)

        if has_current:
            findings.append(AuditFinding(
                finding_id="EDITORIAL_HISTORICAL_AS_CURRENT",
                finding_type="editorial_governance",
                severity="high",
                description=(
                    f"Historical analogy presented as current fact: "
                    f"'{sentence[:100]}...' "
                    f"Clarify that this is historical context, not current data."
                ),
                blocking_level="analyst_blocking",
                repair_owner="analyst",
            ))

    return findings


def check_must_preserve_facts(
    original_claims: list[Any],
    current_claims: list[Any],
    config: EditorialGovernanceConfig,
) -> list[AuditFinding]:
    """Check if must-preserve facts were removed during editing.

    Args:
        original_claims: List of original claims before editing.
        current_claims: List of current claims after editing.
        config: Editorial governance configuration.

    Returns:
        List of audit findings for removed must-preserve facts.
    """
    findings: list[AuditFinding] = []

    if not config.track_must_preserve_facts:
        return findings

    # Build set of current claim IDs
    current_claim_ids = {c.claim_id for c in current_claims}

    # Check original claims for must-preserve markers
    for claim in original_claims:
        # Check if claim has must-preserve metadata
        metadata = getattr(claim, "metadata", {})
        if not metadata.get("must_preserve", False):
            continue

        # Check if claim is still present
        if claim.claim_id not in current_claim_ids:
            findings.append(AuditFinding(
                finding_id=f"EDITORIAL_MUST_PRESERVE_REMOVED_{claim.claim_id}",
                finding_type="editorial_governance",
                severity="high",
                description=(
                    f"Must-preserve fact removed during editing: "
                    f"'{claim.statement[:100]}...' "
                    f"This fact was marked as critical and should be retained."
                ),
                blocking_level="analyst_blocking",
                repair_owner="editor",
                related_claim_id=claim.claim_id,
            ))

    return findings


def run_editorial_governance_checks(
    markdown: str,
    ledger: ClaimLedger,
    context: PipelineContext | None = None,
    original_claims: list[Any] | None = None,
    config: EditorialGovernanceConfig | None = None,
) -> AuditReport:
    """Run all editorial governance checks.

    Args:
        markdown: The brief markdown content.
        ledger: The claim ledger.
        context: Pipeline context for profile-aware thresholds.
        original_claims: Original claims before editing (for must-preserve checks).
        config: Editorial governance configuration.

    Returns:
        AuditReport with editorial governance findings.
    """
    if config is None:
        config = EditorialGovernanceConfig()

    # Apply audience profile thresholds if available
    if context and context.audience_profile:
        from multi_agent_brief.audience.profiles import get_profile
        profile = get_profile(context.audience_profile)
        # Apply profile-specific thresholds
        if hasattr(profile, 'editorial_governance_thresholds'):
            thresholds = profile.editorial_governance_thresholds
            config.min_claims_per_1000_chars = thresholds.get(
                "min_claims_per_1000_chars", config.min_claims_per_1000_chars
            )

    # Get claims from ledger
    # ClaimLedger stores claims in _claims dict, iterate over values
    claims = list(ledger) if ledger else []

    # Run all checks
    findings: list[AuditFinding] = []
    findings.extend(check_factual_density(markdown, claims, config))
    findings.extend(check_business_advice(markdown, claims, config))
    findings.extend(check_comparable_cases(claims, config))
    findings.extend(check_historical_analogies(markdown, claims, config))

    if original_claims:
        findings.extend(check_must_preserve_facts(original_claims, claims, config))

    # Compute audit status
    has_blocking = any(f.severity == "high" for f in findings)
    has_warning = any(f.severity == "warning" for f in findings)

    if has_blocking:
        audit_status = "fail"
        audit_score = max(0, 100 - len(findings) * 10)
    elif has_warning:
        audit_status = "warning"
        audit_score = max(50, 100 - len(findings) * 5)
    else:
        audit_status = "pass"
        audit_score = 100

    return AuditReport(
        audit_status=audit_status,
        audit_score=audit_score,
        findings=findings,
        metadata={
            "governance_protocol": "editorial_governance_v1",
            "checks_run": [
                "factual_density",
                "business_advice",
                "comparable_cases",
                "historical_analogies",
                "must_preserve_facts",
            ],
            "config": {
                "min_claims_per_1000_chars": config.min_claims_per_1000_chars,
                "quiet_week": config.quiet_week,
            },
        },
    )
