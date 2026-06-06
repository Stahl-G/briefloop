"""Limitation Hygiene audit (v0.5.3 PR 4).

Compresses repeated disclaimers into a single Evidence Boundary section.

Rules:
1. Same limitation text repeated >2 times across claims → warning to consolidate.
2. Limitation without corresponding verification_path → warning/fail.
3. Boilerplate disclaimers ("not local data", "for reference only", etc.)
   repeated >1 time → require consolidation into a single Evidence Boundary.

Output: limitation_hygiene_report.json
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from multi_agent_brief.analysis_blocks.schemas import AnalysisBlock
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


# Boilerplate phrases that add no information and should be consolidated
_BOILERPLATE_PATTERNS: list[str] = [
    "not local data",
    "not local",
    "for reference only",
    "仅供参考",
    "does not constitute",
    "不构成",
    "not investment advice",
    "不构成投资建议",
    "for informational purposes",
    "仅供参考，不构成",
    "may not be applicable",
    "might not apply",
    "needs verification",
    "需要验证",
    "subject to change",
    "数据可能有误",
]


@dataclass
class LimitationFinding:
    """One limitation hygiene finding."""

    finding_type: str
    severity: str  # "warning" or "fail"
    block_id: str
    description: str
    recommendation: str
    affected_claims: list[str] = field(default_factory=list)
    repeated_text: str = ""


@dataclass
class LimitationHygieneReport:
    """Summary report for limitation hygiene audit."""

    findings: list[LimitationFinding] = field(default_factory=list)
    total_limitations: int = 0
    unique_limitations: int = 0
    boilerplate_count: int = 0
    consolidated_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [asdict(f) for f in self.findings]
        return data


def audit_limitation_hygiene(
    blocks: list[AnalysisBlock],
    ledger: ClaimLedger,
) -> LimitationHygieneReport:
    """Audit limitation hygiene across all analysis blocks."""
    report = LimitationHygieneReport()

    for block in blocks:
        _check_repeated_limitations(block, ledger, report)
        _check_boilerplate_limitations(block, ledger, report)
        _check_limitation_verification(block, ledger, report)

    return report


def _collect_limitations(
    block: AnalysisBlock,
    ledger: ClaimLedger,
) -> list[tuple[str, str]]:
    """Collect (normalized_text, claim_id) pairs from block limitations."""
    pairs: list[tuple[str, str]] = []
    for cid in block.limitation_claim_ids:
        claim = ledger.get_claim(cid)
        if not claim:
            continue
        for lim in claim.limitations:
            normalized = " ".join(lim.lower().split())
            pairs.append((normalized, cid))
    return pairs


def _check_repeated_limitations(
    block: AnalysisBlock,
    ledger: ClaimLedger,
    report: LimitationHygieneReport,
) -> None:
    """Rule 1: same limitation repeated >2 times → warning."""
    pairs = _collect_limitations(block, ledger)
    report.total_limitations += len(pairs)

    counter: Counter[str] = Counter(text for text, _ in pairs)
    report.unique_limitations += len(counter)

    for text, count in counter.items():
        if count > 2:
            affected = [cid for t, cid in pairs if t == text]
            report.findings.append(LimitationFinding(
                finding_type="repeated_limitation",
                severity="warning",
                block_id=block.block_id,
                description=(
                    f"Limitation repeated {count} times across claims "
                    f"{', '.join(affected[:3])}{'...' if len(affected) > 3 else ''}. "
                    "Consolidate into a single Evidence Boundary section."
                ),
                recommendation="Merge duplicate limitations into one statement with a verification path.",
                affected_claims=affected,
                repeated_text=text,
            ))
            report.consolidated_count += count - 1


def _check_boilerplate_limitations(
    block: AnalysisBlock,
    ledger: ClaimLedger,
    report: LimitationHygieneReport,
) -> None:
    """Rule 3: boilerplate disclaimers repeated >1 time → consolidate."""
    pairs = _collect_limitations(block, ledger)

    boilerplate_hits: list[tuple[str, str]] = []
    for text, cid in pairs:
        if any(bp in text for bp in _BOILERPLATE_PATTERNS):
            boilerplate_hits.append((text, cid))

    report.boilerplate_count += len(boilerplate_hits)

    if len(boilerplate_hits) > 1:
        affected = list(set(cid for _, cid in boilerplate_hits))
        report.findings.append(LimitationFinding(
            finding_type="boilerplate_limitation",
            severity="warning",
            block_id=block.block_id,
            description=(
                f"{len(boilerplate_hits)} boilerplate disclaimers detected "
                f"(e.g., 'for reference only', 'not local data'). "
                "These add no information. Consolidate into one Evidence Boundary."
            ),
            recommendation=(
                "Replace repeated boilerplate with a single statement: "
                "'Evidence boundary: [specific gap]. Verification path: [how to close gap].'"
            ),
            affected_claims=affected,
        ))


def _check_limitation_verification(
    block: AnalysisBlock,
    ledger: ClaimLedger,
    report: LimitationHygieneReport,
) -> None:
    """Rule 2: limitation without verification_path → warning."""
    if not block.limitation_claim_ids:
        return
    if block.verification_path.strip():
        return

    # Check if any limitation claim has a non-boilerplate limitation
    has_real_limitation = False
    for cid in block.limitation_claim_ids:
        claim = ledger.get_claim(cid)
        if not claim:
            continue
        for lim in claim.limitations:
            normalized = " ".join(lim.lower().split())
            if not any(bp in normalized for bp in _BOILERPLATE_PATTERNS):
                has_real_limitation = True
                break
        if has_real_limitation:
            break

    if has_real_limitation:
        report.findings.append(LimitationFinding(
            finding_type="missing_verification_path",
            severity="warning",
            block_id=block.block_id,
            description=(
                f"Block '{block.title}' has substantive limitations but no verification_path. "
                "Limitations should describe what data to collect or how to verify."
            ),
            recommendation="Add verification_path: describe the specific data source or check that would resolve the limitation.",
            affected_claims=list(block.limitation_claim_ids),
        ))


def format_limitation_hygiene_report(report: LimitationHygieneReport) -> str:
    """Format report into readable text."""
    lines = [
        "Limitation Hygiene Report",
        "=" * 25,
        "",
        f"Total limitations: {report.total_limitations}",
        f"Unique limitations: {report.unique_limitations}",
        f"Boilerplate disclaimers: {report.boilerplate_count}",
        "",
    ]

    if not report.findings:
        lines.append("All limitation hygiene checks passed.")
        return "\n".join(lines)

    for f in report.findings:
        icon = "❌" if f.severity == "fail" else "⚠️"
        lines.append(f"{icon} [{f.severity.upper()}] {f.finding_type}")
        lines.append(f"   Block: {f.block_id}")
        lines.append(f"   {f.description}")
        lines.append(f"   → {f.recommendation}")
        if f.repeated_text:
            lines.append(f"   Repeated: \"{f.repeated_text}\"")
        lines.append("")

    fails = sum(1 for f in report.findings if f.severity == "fail")
    warns = sum(1 for f in report.findings if f.severity == "warning")
    lines.append(f"Result: {fails} fail(s), {warns} warning(s)")
    return "\n".join(lines)
