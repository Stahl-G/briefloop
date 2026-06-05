from __future__ import annotations

from datetime import date, datetime
import re

from multi_agent_brief.audit.redaction import scan_redaction_risks
from multi_agent_brief.audit.interfaces import AuditAgentInterface
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding, AuditReport, PipelineContext


SRC_REF_PATTERN = re.compile(r"\[src:([A-Z0-9_]{6,})\]")
NUMBER_PATTERN = re.compile(r"(\$[\d,.]+|[\d,.]+%|\b\d+(?:\.\d+)?\s?(?:GW|GWh|MW|MWh|million|billion)\b)")


def parse_date(value: str) -> date | None:
    if not value:
        return None
    text = value.strip()
    for fmt, width in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(text[:width], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def extract_src_refs(markdown: str) -> list[dict]:
    refs: list[dict] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        for match in SRC_REF_PATTERN.finditer(line):
            refs.append({"claim_id": match.group(1), "line_number": line_number, "line": line.strip()})
    return refs


def run_deterministic_audit(
    markdown: str,
    ledger: ClaimLedger,
    *,
    report_date: str = "",
    max_source_age_days: int | None = None,
    fail_on_stale_source: bool = False,
) -> AuditReport:
    findings: list[AuditFinding] = []
    refs = extract_src_refs(markdown)
    referenced_ids = {ref["claim_id"] for ref in refs}

    for ref in refs:
        if ledger.get_claim(ref["claim_id"]) is None:
            findings.append(
                AuditFinding(
                    finding_id=f"ORPHAN_{len(findings)+1:03d}",
                    severity="high",
                    finding_type="missing_claim",
                    related_claim_id=ref["claim_id"],
                    line_number=ref["line_number"],
                    description=f"Referenced claim_id {ref['claim_id']} was not found in the Claim Ledger.",
                    recommendation="Remove the reference or add the supporting claim to the ledger.",
                    evidence=ref["line"],
                )
            )

    for line_number, line in enumerate(markdown.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        if SRC_REF_PATTERN.search(line):
            continue
        for match in NUMBER_PATTERN.finditer(line):
            findings.append(
                AuditFinding(
                    finding_id=f"NUMBER_{len(findings)+1:03d}",
                    severity="medium",
                    finding_type="number_without_source",
                    line_number=line_number,
                    description=f"Number-like value '{match.group(1)}' appears without a source reference on the same line.",
                    recommendation="Attach a [src:CLAIM_ID] reference or remove the unsupported number.",
                    evidence=line.strip(),
                )
            )

    for claim in ledger.detect_missing_sources():
        findings.append(
            AuditFinding(
                finding_id=f"SOURCE_{len(findings)+1:03d}",
                severity="high",
                finding_type="missing_source",
                related_claim_id=claim.claim_id,
                description="A claim requires audit but is missing source_id or evidence_text.",
                recommendation="Add source metadata and evidence text before using the claim.",
                evidence=claim.statement,
            )
        )

    for duplicate_group in ledger.detect_duplicate_claims():
        findings.append(
            AuditFinding(
                finding_id=f"DUP_{len(findings)+1:03d}",
                severity="low",
                finding_type="duplicate_claim",
                related_claim_id=duplicate_group[0].claim_id,
                description=f"Duplicate claim statements found: {', '.join(claim.claim_id for claim in duplicate_group)}.",
                recommendation="Merge duplicate claims or clarify their separate source contexts.",
                evidence=duplicate_group[0].statement,
            )
        )

    report_day = parse_date(report_date)
    web_search_missing_date_count = 0
    if report_day and max_source_age_days is not None:
        for claim in ledger:
            published_at = str(claim.metadata.get("published_at", ""))
            source_day = parse_date(published_at)
            if source_day is None:
                # web_search sources often lack published_at — flag it at low severity (B15)
                if claim.source_type == "web_search":
                    findings.append(
                        AuditFinding(
                            finding_id=f"DATE_{len(findings)+1:03d}",
                            severity="low",
                            finding_type="missing_source_date",
                            related_claim_id=claim.claim_id,
                            description="Web search claim is missing a parseable published_at date.",
                            recommendation="Mark the source as retrieved_only or provide published_at.",
                            evidence=claim.statement,
                        )
                    )
                    web_search_missing_date_count += 1
                else:
                    findings.append(
                        AuditFinding(
                            finding_id=f"DATE_{len(findings)+1:03d}",
                            severity="medium",
                            finding_type="missing_source_date",
                            related_claim_id=claim.claim_id,
                            description="Claim source is missing a parseable published_at date for reporting-window audit.",
                            recommendation="Add source published_at metadata or mark the source as evergreen/background.",
                            evidence=claim.statement,
                        )
                    )
                continue
            age_days = (report_day - source_day).days
            if age_days > max_source_age_days:
                findings.append(
                    AuditFinding(
                        finding_id=f"STALE_{len(findings)+1:03d}",
                        severity="high" if fail_on_stale_source else "medium",
                        finding_type="stale_source",
                        related_claim_id=claim.claim_id,
                        description=(
                            f"Source date {source_day.isoformat()} is {age_days} days before report date "
                            f"{report_day.isoformat()}, exceeding the {max_source_age_days}-day reporting window."
                        ),
                        recommendation="Remove this item from the weekly brief or recast it as dated background.",
                        evidence=claim.statement,
                    )
                )

    for risk in scan_redaction_risks(markdown + "\n" + "\n".join(claim.evidence_text for claim in ledger)):
        findings.append(
            AuditFinding(
                finding_id=f"REDACT_{len(findings)+1:03d}",
                severity="high" if risk["severity"] == "high" else "medium",
                finding_type="redaction_risk",
                description=f"Potential {risk['type']} detected.",
                recommendation=risk["recommendation"],
                evidence=risk["text"],
            )
        )

    # Epistemic gate checks (Claim Schema v2)
    for claim in ledger:
        if claim.epistemic_type == "hypothesis" and claim.confidence == "high":
            findings.append(
                AuditFinding(
                    finding_id=f"EPISTEMIC_{len(findings)+1:03d}",
                    severity="high",
                    finding_type="hypothesis_high_confidence",
                    related_claim_id=claim.claim_id,
                    description="Hypothesis claim is presented with high confidence — hypotheses should not be treated as certain facts.",
                    recommendation="Lower confidence to medium/low or reclassify as observed.",
                    evidence=claim.statement,
                )
            )
        if claim.epistemic_type == "action" and not claim.applicability_reason.strip():
            findings.append(
                AuditFinding(
                    finding_id=f"EPISTEMIC_{len(findings)+1:03d}",
                    severity="high",
                    finding_type="action_without_basis",
                    related_claim_id=claim.claim_id,
                    description="Action claim lacks applicability rationale — actions must justify why they apply here.",
                    recommendation="Add applicability_reason explaining why this action is relevant.",
                    evidence=claim.statement,
                )
            )
        if claim.epistemic_type == "analogy" and not claim.limitations:
            findings.append(
                AuditFinding(
                    finding_id=f"EPISTEMIC_{len(findings)+1:03d}",
                    severity="medium",
                    finding_type="analogy_without_limitations",
                    related_claim_id=claim.claim_id,
                    description="Analogy claim has no stated limitations — analogies must declare where they break down.",
                    recommendation="Add limitations describing where the analogy does not hold.",
                    evidence=claim.statement,
                )
            )
        if claim.epistemic_type == "analogy" and claim.evidence_relation == "direct":
            findings.append(
                AuditFinding(
                    finding_id=f"EPISTEMIC_{len(findings)+1:03d}",
                    severity="high",
                    finding_type="analogy_direct_relation",
                    related_claim_id=claim.claim_id,
                    description="Analogy claim uses 'direct' evidence relation — analogies should use indirect or analogous relation.",
                    recommendation="Change evidence_relation to 'indirect' or 'analogous'.",
                    evidence=claim.statement,
                )
            )

    unused_claims = [claim.claim_id for claim in ledger if claim.claim_id not in referenced_ids]
    metadata = {
        "refs_extracted": len(refs),
        "unique_refs": len(referenced_ids),
        "ledger_claims": len(ledger),
        "unused_claims": unused_claims,
        "report_date": report_date,
        "max_source_age_days": max_source_age_days,
        "fail_on_stale_source": fail_on_stale_source,
        "web_search_missing_published_at": web_search_missing_date_count,
    }
    high = sum(1 for finding in findings if finding.severity == "high")
    medium = sum(1 for finding in findings if finding.severity == "medium")
    if high:
        status = "fail"
    elif medium:
        status = "warning"
    else:
        status = "pass"
    score = max(0, 100 - high * 25 - medium * 10 - (len(findings) - high - medium) * 3)
    return AuditReport(audit_status=status, audit_score=score, findings=findings, metadata=metadata)


class DeterministicAuditAgent(AuditAgentInterface):
    name = "deterministic-auditor"

    def run_audit(
        self,
        markdown: str,
        ledger: ClaimLedger,
        context: PipelineContext | None = None,
    ) -> AuditReport:
        return run_deterministic_audit(
            markdown,
            ledger,
            report_date=context.report_date if context else "",
            max_source_age_days=context.max_source_age_days if context else None,
            fail_on_stale_source=context.fail_on_stale_source if context else False,
        )
