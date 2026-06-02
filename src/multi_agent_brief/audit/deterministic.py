from __future__ import annotations

import re

from multi_agent_brief.audit.redaction import scan_redaction_risks
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding, AuditReport


SRC_REF_PATTERN = re.compile(r"\[src:([A-Z0-9_]{6,})\]")
NUMBER_PATTERN = re.compile(r"(\$[\d,.]+|[\d,.]+%|\b\d+(?:\.\d+)?\s?(?:GW|GWh|MW|MWh|million|billion)\b)")


def extract_src_refs(markdown: str) -> list[dict]:
    refs: list[dict] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        for match in SRC_REF_PATTERN.finditer(line):
            refs.append({"claim_id": match.group(1), "line_number": line_number, "line": line.strip()})
    return refs


def run_deterministic_audit(markdown: str, ledger: ClaimLedger) -> AuditReport:
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

    unused_claims = [claim.claim_id for claim in ledger if claim.claim_id not in referenced_ids]
    metadata = {
        "refs_extracted": len(refs),
        "unique_refs": len(referenced_ids),
        "ledger_claims": len(ledger),
        "unused_claims": unused_claims,
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
