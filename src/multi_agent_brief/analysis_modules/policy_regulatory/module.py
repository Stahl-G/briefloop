"""Policy & Regulatory Risk Analysis Module implementation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from multi_agent_brief.analysis_modules.base import AnalysisModule, ModuleOutput
from multi_agent_brief.analysis_modules.policy_regulatory.audit import (
    audit_policy_events,
)
from multi_agent_brief.analysis_modules.policy_regulatory.schemas import (
    ApplicabilityQuestion,
    PolicyCoverageReport,
    PolicyEvidencePack,
    PolicyEvent,
    RiskItem,
)

if TYPE_CHECKING:
    from multi_agent_brief.core.claim_ledger import ClaimLedger
    from multi_agent_brief.core.schemas import PipelineContext

logger = logging.getLogger(__name__)


class PolicyRegulatoryModule(AnalysisModule):
    """Policy & Regulatory Risk Analysis Module.

    This module analyzes policy and regulatory events from screened claims,
    identifies risks, and produces structured artifacts for the Analyst agent.
    """

    name = "policy_regulatory"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate module-specific configuration.

        Args:
            config: The module's section from config.yaml's ``modules`` block.

        Returns:
            A list of error messages. An empty list means "valid".
        """
        errors: list[str] = []

        # Check optional risk_types configuration
        risk_types = config.get("risk_types")
        if risk_types is not None:
            if not isinstance(risk_types, list):
                errors.append("risk_types must be a list of strings")
            else:
                valid_types = {"compliance", "operational", "market_access", "reputational"}
                for rt in risk_types:
                    if rt not in valid_types:
                        errors.append(f"Unknown risk type: {rt}")

        return errors

    def analyze(
        self,
        context: "PipelineContext",
        ledger: "ClaimLedger",
    ) -> ModuleOutput:
        """Run policy & regulatory analysis on a screened ClaimLedger.

        Args:
            context: The pipeline context.
            ledger: The screened ClaimLedger.

        Returns:
            A ModuleOutput with policy artifacts, findings, and metadata.
        """
        output = ModuleOutput(module_name=self.name)

        # Extract policy-relevant claims
        policy_claims = self._extract_policy_claims(ledger)

        if not policy_claims:
            output.metadata["status"] = "no_policy_claims"
            return output

        # Build evidence pack
        evidence_pack = self._build_evidence_pack(policy_claims, context)

        # Run audit checks
        findings = audit_policy_events(evidence_pack)
        output.findings.extend(findings)

        # Build coverage report
        coverage_report = self._build_coverage_report(evidence_pack)

        # Write artifacts to output directory
        output_dir = Path(context.output_dir) / "intermediate"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write policy_events.json
        events_path = output_dir / "policy_events.json"
        events_path.write_text(
            json.dumps(
                [e.to_dict() for e in evidence_pack.events],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        output.artifacts["policy_events"] = str(events_path)

        # Write risk_register.json
        risks_path = output_dir / "risk_register.json"
        risks_path.write_text(
            json.dumps(
                [r.to_dict() for r in evidence_pack.risks],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        output.artifacts["risk_register"] = str(risks_path)

        # Write applicability_questions.json
        questions_path = output_dir / "applicability_questions.json"
        questions_path.write_text(
            json.dumps(
                [q.to_dict() for q in evidence_pack.applicability_questions],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        output.artifacts["applicability_questions"] = str(questions_path)

        # Write policy_evidence_pack.json
        pack_path = output_dir / "policy_evidence_pack.json"
        pack_path.write_text(
            json.dumps(evidence_pack.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        output.artifacts["policy_evidence_pack"] = str(pack_path)

        # Write policy_coverage_report.json
        coverage_path = output_dir / "policy_coverage_report.json"
        coverage_path.write_text(
            json.dumps(coverage_report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        output.artifacts["policy_coverage_report"] = str(coverage_path)

        # Set metadata
        output.metadata["status"] = "completed"
        output.metadata["events_count"] = len(evidence_pack.events)
        output.metadata["risks_count"] = len(evidence_pack.risks)
        output.metadata["questions_count"] = len(evidence_pack.applicability_questions)
        output.metadata["findings_count"] = len(findings)

        return output

    def _extract_policy_claims(self, ledger: "ClaimLedger") -> list[Any]:
        """Extract claims relevant to policy & regulatory analysis.

        Args:
            ledger: The screened ClaimLedger.

        Returns:
            List of claims that are policy-relevant.
        """
        policy_keywords = {
            "policy", "regulation", "regulatory", "compliance", "law",
            "legislation", "act", "bill", "directive", "standard",
            "guideline", "enforcement", "penalty", "sanction",
            "licensing", "permit", "authorization", "approval",
        }

        policy_claims = []
        for claim in ledger.claims:
            # Check if claim has policy-related tags
            if hasattr(claim, "tags"):
                for tag in claim.tags:
                    if tag.lower() in policy_keywords:
                        policy_claims.append(claim)
                        break
                else:
                    # Check claim text for policy keywords
                    claim_text = claim.text.lower()
                    for keyword in policy_keywords:
                        if keyword in claim_text:
                            policy_claims.append(claim)
                            break

        return policy_claims

    def _build_evidence_pack(
        self,
        policy_claims: list[Any],
        context: "PipelineContext",
    ) -> PolicyEvidencePack:
        """Build evidence pack from policy claims.

        Args:
            policy_claims: List of policy-relevant claims.
            context: Pipeline context.

        Returns:
            PolicyEvidencePack with extracted events, risks, and questions.
        """
        events: list[PolicyEvent] = []
        risks: list[RiskItem] = []
        questions: list[ApplicabilityQuestion] = []

        for i, claim in enumerate(policy_claims):
            # Extract event from claim
            event = self._extract_event_from_claim(claim, i)
            if event:
                events.append(event)

                # Extract risks from claim
                claim_risks = self._extract_risks_from_claim(claim, event, i)
                risks.extend(claim_risks)

                # Extract applicability questions
                claim_questions = self._extract_questions_from_claim(claim, event, i)
                questions.extend(claim_questions)

        # Build metadata
        metadata = {
            "source": "policy_regulatory_module",
            "claim_count": len(policy_claims),
            "project_name": context.project_name,
            "report_date": context.report_date,
        }

        return PolicyEvidencePack(
            events=events,
            risks=risks,
            applicability_questions=questions,
            metadata=metadata,
        )

    def _extract_event_from_claim(
        self,
        claim: Any,
        index: int,
    ) -> PolicyEvent | None:
        """Extract a policy event from a claim.

        Args:
            claim: A claim from the ledger.
            index: Index for generating unique IDs.

        Returns:
            PolicyEvent or None if extraction fails.
        """
        # Basic extraction - in production this would be more sophisticated
        return PolicyEvent(
            event_id=f"POLICY_{index:04d}",
            jurisdiction=getattr(claim, "jurisdiction", ""),
            authority=getattr(claim, "authority", ""),
            instrument_name=getattr(claim, "instrument_name", claim.text[:100]),
            publication_date=getattr(claim, "publication_date", ""),
            effective_date=getattr(claim, "effective_date", ""),
            affected_entities=getattr(claim, "affected_entities", []),
            core_change=getattr(claim, "core_change", claim.text[:200]),
            compliance_deadline=getattr(claim, "compliance_deadline", ""),
            source_refs=getattr(claim, "source_refs", [claim.source_id] if hasattr(claim, "source_id") else []),
            limitations=getattr(claim, "limitations", []),
            epistemic_type=getattr(claim, "epistemic_type", "TO_VERIFY"),
        )

    def _extract_risks_from_claim(
        self,
        claim: Any,
        event: PolicyEvent,
        index: int,
    ) -> list[RiskItem]:
        """Extract risks from a claim.

        Args:
            claim: A claim from the ledger.
            event: The related policy event.
            index: Index for generating unique IDs.

        Returns:
            List of RiskItem.
        """
        risks: list[RiskItem] = []

        # Extract risk type from claim or default to compliance
        risk_type = getattr(claim, "risk_type", "compliance")
        if risk_type not in ("compliance", "operational", "market_access", "reputational"):
            risk_type = "compliance"

        # Extract severity from claim or default to medium
        severity = getattr(claim, "severity", "medium")
        if severity not in ("low", "medium", "high", "critical"):
            severity = "medium"

        risks.append(RiskItem(
            risk_id=f"RISK_{index:04d}",
            event_id=event.event_id,
            risk_type=risk_type,
            severity=severity,
            likelihood=getattr(claim, "likelihood", "possible"),
            affected_entities=getattr(claim, "affected_entities", []),
            mitigation_notes=getattr(claim, "mitigation_notes", ""),
            applicability_status=getattr(claim, "applicability_status", "TO_VERIFY"),
            source_refs=event.source_refs,
        ))

        return risks

    def _extract_questions_from_claim(
        self,
        claim: Any,
        event: PolicyEvent,
        index: int,
    ) -> list[ApplicabilityQuestion]:
        """Extract applicability questions from a claim.

        Args:
            claim: A claim from the ledger.
            event: The related policy event.
            index: Index for generating unique IDs.

        Returns:
            List of ApplicabilityQuestion.
        """
        questions: list[ApplicabilityQuestion] = []

        # If applicability is not confirmed, generate a question
        if event.epistemic_type in ("HYPOTHESIS", "TO_VERIFY"):
            questions.append(ApplicabilityQuestion(
                question_id=f"Q_{index:04d}",
                event_id=event.event_id,
                question=f"Is the policy event '{event.instrument_name}' applicable to the target entities?",
                context=event.core_change[:200] if event.core_change else "",
                priority="high" if event.epistemic_type == "TO_VERIFY" else "medium",
            ))

        return questions

    def _build_coverage_report(
        self,
        evidence_pack: PolicyEvidencePack,
    ) -> PolicyCoverageReport:
        """Build coverage report from evidence pack.

        Args:
            evidence_pack: The policy evidence pack.

        Returns:
            PolicyCoverageReport.
        """
        jurisdictions = set()
        authorities = set()
        risk_types = set()

        for event in evidence_pack.events:
            if event.jurisdiction:
                jurisdictions.add(event.jurisdiction)
            if event.authority:
                authorities.add(event.authority)

        for risk in evidence_pack.risks:
            risk_types.add(risk.risk_type)

        # Identify coverage gaps
        gaps: list[str] = []
        if not jurisdictions:
            gaps.append("No jurisdictions covered")
        if not authorities:
            gaps.append("No authorities covered")
        if not risk_types:
            gaps.append("No risk types identified")

        return PolicyCoverageReport(
            total_events=len(evidence_pack.events),
            jurisdictions_covered=sorted(jurisdictions),
            authorities_covered=sorted(authorities),
            risk_types_covered=sorted(risk_types),
            coverage_gaps=gaps,
            metadata={
                "risks_count": len(evidence_pack.risks),
                "questions_count": len(evidence_pack.applicability_questions),
            },
        )
