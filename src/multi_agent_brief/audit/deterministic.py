from __future__ import annotations

from datetime import date, datetime
import re

from multi_agent_brief.audit.redaction import scan_redaction_risks
from multi_agent_brief.audit.interfaces import AuditAgentInterface
from multi_agent_brief.audit.rule_packs import tag_finding
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding, AuditReport, PipelineContext


SRC_REF_PATTERN = re.compile(r"\[src:([A-Z0-9_]{6,})\]")
NUMBER_PATTERN = re.compile(r"(\$[\d,.]+|[\d,.]+%|\b\d+(?:\.\d+)?\s?(?:GW|GWh|MW|MWh|million|billion)\b)")


def _tag(finding_type: str, **kwargs) -> AuditFinding:
    """Create an AuditFinding with taxonomy tags from the rule pack."""
    tax = tag_finding(finding_type)
    return AuditFinding(
        finding_type=finding_type,
        blocking_level=tax["blocking_level"],
        repair_owner=tax["repair_owner"],
        **kwargs,
    )


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
                _tag(
                    "missing_claim",
                    finding_id=f"ORPHAN_{len(findings)+1:03d}",
                    severity="high",
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
                _tag(
                    "number_without_source",
                    finding_id=f"NUMBER_{len(findings)+1:03d}",
                    severity="medium",
                    line_number=line_number,
                    description=f"Number-like value '{match.group(1)}' appears without a source reference on the same line.",
                    recommendation="Attach a [src:CLAIM_ID] reference or remove the unsupported number.",
                    evidence=line.strip(),
                )
            )

    for claim in ledger.detect_missing_sources():
        findings.append(
            _tag(
                "missing_source",
                finding_id=f"SOURCE_{len(findings)+1:03d}",
                severity="high",
                related_claim_id=claim.claim_id,
                description="A claim requires audit but is missing source_id or evidence_text.",
                recommendation="Add source metadata and evidence text before using the claim.",
                evidence=claim.statement,
            )
        )

    for duplicate_group in ledger.detect_duplicate_claims():
        findings.append(
            _tag(
                "duplicate_claim",
                finding_id=f"DUP_{len(findings)+1:03d}",
                severity="low",
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
                        _tag(
                            "missing_source_date",
                            finding_id=f"DATE_{len(findings)+1:03d}",
                            severity="low",
                            related_claim_id=claim.claim_id,
                            description="Web search claim is missing a parseable published_at date.",
                            recommendation="Mark the source as retrieved_only or provide published_at.",
                            evidence=claim.statement,
                        )
                    )
                    web_search_missing_date_count += 1
                else:
                    findings.append(
                        _tag(
                            "missing_source_date",
                            finding_id=f"DATE_{len(findings)+1:03d}",
                            severity="medium",
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
                    _tag(
                        "stale_source",
                        finding_id=f"STALE_{len(findings)+1:03d}",
                        severity="high" if fail_on_stale_source else "medium",
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
            _tag(
                "redaction_risk",
                finding_id=f"REDACT_{len(findings)+1:03d}",
                severity="high" if risk["severity"] == "high" else "medium",
                description=f"Potential {risk['type']} detected.",
                recommendation=risk["recommendation"],
                evidence=risk["text"],
            )
        )

    # Epistemic gate checks (Claim Schema v2)
    for claim in ledger:
        if claim.epistemic_type == "hypothesis" and claim.confidence == "high":
            findings.append(
                _tag(
                    "hypothesis_high_confidence",
                    finding_id=f"EPISTEMIC_{len(findings)+1:03d}",
                    severity="high",
                    related_claim_id=claim.claim_id,
                    description="Hypothesis claim is presented with high confidence — hypotheses should not be treated as certain facts.",
                    recommendation="Lower confidence to medium/low or reclassify as observed.",
                    evidence=claim.statement,
                )
            )
        if claim.epistemic_type == "action" and not claim.applicability_reason.strip():
            findings.append(
                _tag(
                    "action_without_basis",
                    finding_id=f"EPISTEMIC_{len(findings)+1:03d}",
                    severity="high",
                    related_claim_id=claim.claim_id,
                    description="Action claim lacks applicability rationale — actions must justify why they apply here.",
                    recommendation="Add applicability_reason explaining why this action is relevant.",
                    evidence=claim.statement,
                )
            )
        if claim.epistemic_type == "analogy" and not claim.limitations:
            findings.append(
                _tag(
                    "analogy_without_limitations",
                    finding_id=f"EPISTEMIC_{len(findings)+1:03d}",
                    severity="medium",
                    related_claim_id=claim.claim_id,
                    description="Analogy claim has no stated limitations — analogies must declare where they break down.",
                    recommendation="Add limitations describing where the analogy does not hold.",
                    evidence=claim.statement,
                )
            )
        if claim.epistemic_type == "analogy" and claim.evidence_relation == "direct":
            findings.append(
                _tag(
                    "analogy_direct_relation",
                    finding_id=f"EPISTEMIC_{len(findings)+1:03d}",
                    severity="high",
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
        report = run_deterministic_audit(
            markdown,
            ledger,
            report_date=context.report_date if context else "",
            max_source_age_days=context.max_source_age_days if context else None,
            fail_on_stale_source=context.fail_on_stale_source if context else False,
        )

        # Local signal audit checks — only when local signal discovery is configured
        if context:
            local_signal_report = context.metadata.get("local_signal_report")
            discovery = context.metadata.get("source_discovery", {})
            has_local_signal = bool(
                local_signal_report
                or discovery.get("local_signal_discovery", {}).get("enabled", False)
            )
            if has_local_signal:
                local_findings = _check_local_signal_claims(markdown, ledger, context)
                report.findings.extend(local_findings)
                if local_findings:
                    from multi_agent_brief.audit.interfaces import recompute_report_status
                    recompute_report_status(report)

        return report


# ── Local Signal Audit Checks ────────────────────────────────────────

# Patterns that indicate consumer pain-point claims
# Chinese patterns — no case distinction
_CONSUMER_PAIN_PATTERNS_ZH = [
    re.compile(r"消费者(?:认为|抱怨|普遍|反馈|觉得|表示)"),
    re.compile(r"用户(?:抱怨|觉得|认为|反馈|评价|普遍)"),
    re.compile(r"市场反馈(?:显示|表明|指出)"),
    re.compile(r"用户评价(?:显示|表明)"),
]

# English patterns — case insensitive
_CONSUMER_PAIN_PATTERNS_EN = [
    re.compile(r"consumers?\s+\w*\s*(?:report|complain|believe|feel|say|indicate)", re.IGNORECASE),
    re.compile(r"users?\s+\w*\s*(?:complain|report|feel|believe|commonly)", re.IGNORECASE),
    re.compile(r"market\s+feedback\s+(?:shows|indicates|suggests)", re.IGNORECASE),
    re.compile(r"customer\s+(?:complaints?|feedback|reviews?)\s+(?:show|indicate|suggest)", re.IGNORECASE),
]

_CONSUMER_PAIN_PATTERNS = _CONSUMER_PAIN_PATTERNS_ZH + _CONSUMER_PAIN_PATTERNS_EN

# Source types that count as consumer-level evidence
_CONSUMER_SOURCE_TYPES = {"local_signal", "consumer_discussion", "platform_data"}


def _check_local_signal_claims(
    markdown: str,
    ledger: ClaimLedger,
    context: PipelineContext,
) -> list[AuditFinding]:
    """Check local signal audit rules.

    LOCAL_SIGNAL_CLAIM_001: Consumer pain-point claims require consumer-level sources.
    LOCAL_SIGNAL_PROVENANCE_001: Local signal claims require sample metadata.
    LOCAL_SIGNAL_PRIVACY_001: Personal data must not enter final brief.
    """
    findings: list[AuditFinding] = []

    # LOCAL_SIGNAL_CLAIM_001: Check for consumer pain-point claims without consumer sources
    for line_num, line in enumerate(markdown.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        for pattern in _CONSUMER_PAIN_PATTERNS:
            if pattern.search(line):
                # Check if this line has a source reference to a consumer-level claim
                src_refs = SRC_REF_PATTERN.findall(line)
                has_consumer_source = False
                for claim_id in src_refs:
                    claim = ledger.get_claim(claim_id)
                    if claim and claim.source_type in _CONSUMER_SOURCE_TYPES:
                        has_consumer_source = True
                        break
                    # Also check metadata for source_family
                    if claim and claim.metadata.get("source_family") == "local_signal":
                        has_consumer_source = True
                        break

                if not has_consumer_source:
                    findings.append(
                        _tag(
                            "local_signal_unsupported_claim",
                            finding_id=f"LOCAL_SIGNAL_CLAIM_{len(findings)+1:03d}",
                            severity="high",
                            line_number=line_num,
                            description=(
                                "Consumer pain-point claim appears without consumer-level "
                                "or platform-data source support. External trend articles "
                                "cannot support specific consumer sentiment claims."
                            ),
                            recommendation=(
                                "Add consumer-discussion or platform-data sources, "
                                "or reframe as external trend observation."
                            ),
                            evidence=line.strip(),
                        )
                    )
                break  # Only one finding per line

    # LOCAL_SIGNAL_PROVENANCE_001: Check local signal claims have required metadata
    for claim in ledger:
        if claim.source_type == "local_signal" or claim.metadata.get("source_family") == "local_signal":
            required_meta = ["platform", "market", "collected_at", "access_level", "sample_type", "collector"]
            missing = [k for k in required_meta if not claim.metadata.get(k)]
            if missing:
                findings.append(
                    _tag(
                        "local_signal_missing_provenance",
                        finding_id=f"LOCAL_SIGNAL_PROV_{len(findings)+1:03d}",
                        severity="medium",
                        related_claim_id=claim.claim_id,
                        description=(
                            f"Local signal claim {claim.claim_id} is missing required "
                            f"sample metadata: {', '.join(missing)}"
                        ),
                        recommendation="Add sample metadata (platform, market, collected_at, etc.) to the claim.",
                        evidence=claim.statement,
                    )
                )

    # LOCAL_SIGNAL_PRIVACY_001: Check for personal data in local signal claims
    for claim in ledger:
        is_local_signal = (
            claim.source_type == "local_signal"
            or claim.metadata.get("source_family") == "local_signal"
        )
        if is_local_signal and claim.metadata.get("contains_personal_data", False):
            findings.append(
                _tag(
                    "local_signal_privacy_violation",
                    finding_id=f"LOCAL_SIGNAL_PRIV_{len(findings)+1:03d}",
                    severity="high",
                    related_claim_id=claim.claim_id,
                    description=(
                        f"Claim {claim.claim_id} is marked as containing personal data "
                        f"and must not appear in the final brief."
                    ),
                    recommendation="Remove this claim or redact personal data before including in brief.",
                    evidence=claim.statement,
                )
            )

    return findings
