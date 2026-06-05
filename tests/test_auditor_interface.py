from multi_agent_brief.agents.auditor import AuditorAgent
from multi_agent_brief.audit.deterministic import DeterministicAuditAgent
from multi_agent_brief.audit.interfaces import CompositeAuditAgent, AuditAgentInterface
from multi_agent_brief.audit.semantic import NoOpSemanticAuditAgent, SemanticAuditPromptBuilder
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditReport, Claim, PipelineContext


def test_auditor_agent_delegates_to_audit_interface():
    ledger = ClaimLedger(
        [
            Claim(
                claim_id="SRC_ABCDEF",
                statement="A synthetic source reported a 2 GW expansion.",
                source_id="SRC",
                evidence_text="A synthetic source reported a 2 GW expansion.",
            )
        ]
    )
    context = PipelineContext(project_name="Demo", input_dir="input", output_dir="output")
    context.report_state.draft_markdown = "- A synthetic source reported a 2 GW expansion. [src:SRC_ABCDEF]"

    audit_agent = CompositeAuditAgent(DeterministicAuditAgent(), NoOpSemanticAuditAgent())
    result = AuditorAgent(audit_agent=audit_agent).run(context, ledger)

    assert result.artifacts["audit_status"] == "pass"
    assert context.report_state.audit_report.metadata["semantic_agent"] == "noop-semantic-auditor"


def test_semantic_prompt_builder_includes_claim_evidence():
    ledger = ClaimLedger(
        [
            Claim(
                claim_id="SRC_ABCDEF",
                statement="A synthetic source reported a 2 GW expansion.",
                source_id="SRC",
                evidence_text="Evidence text here.",
            )
        ]
    )

    prompt = SemanticAuditPromptBuilder().build_prompt("Draft [src:SRC_ABCDEF]", ledger)

    assert "SRC_ABCDEF" in prompt
    assert "Evidence text here." in prompt


# ── Semantic Status Tests (PR4) ──


def _clean_ledger():
    return ClaimLedger([
        Claim(
            claim_id="SRC_ABCDEF",
            statement="A synthetic source reported a 2 GW expansion.",
            source_id="SRC",
            evidence_text="A synthetic source reported a 2 GW expansion.",
        )
    ])


def _clean_context():
    ctx = PipelineContext(project_name="Demo", input_dir="input", output_dir="output")
    ctx.report_state.draft_markdown = "- A synthetic source reported a 2 GW expansion. [src:SRC_ABCDEF]"
    return ctx


class TestNoOpSemanticStatus:
    def test_noop_returns_pass_with_not_configured_metadata(self):
        noop = NoOpSemanticAuditAgent()
        report = noop.run_audit("draft", _clean_ledger())
        # Overall status stays pass (no findings) — semantic_status in metadata
        assert report.audit_status == "pass"
        assert report.audit_score == 100
        assert report.metadata["semantic_status"] == "not_configured"

    def test_noop_produces_no_findings(self):
        noop = NoOpSemanticAuditAgent()
        report = noop.run_audit("draft", _clean_ledger())
        assert len(report.findings) == 0


class TestCompositeSemanticStatus:
    def test_no_semantic_agent_sets_not_configured(self):
        det = DeterministicAuditAgent()
        comp = CompositeAuditAgent(det)  # no semantic agent
        report = comp.run_audit("- Statement [src:SRC_ABCDEF]", _clean_ledger(), _clean_context())
        assert report.metadata["semantic_agent"] == "not_configured"
        assert report.metadata["semantic_status"] == "not_configured"

    def test_noop_semantic_agent_sets_not_configured_in_metadata(self):
        det = DeterministicAuditAgent()
        noop = NoOpSemanticAuditAgent()
        comp = CompositeAuditAgent(det, noop)
        report = comp.run_audit("- Statement [src:SRC_ABCDEF]", _clean_ledger(), _clean_context())
        assert report.metadata["semantic_agent"] == "noop-semantic-auditor"
        assert report.metadata["semantic_status"] == "not_configured"
        # Overall status stays pass — semantic_status is metadata, not audit_status
        assert report.audit_status == "pass"

    def test_real_semantic_agent_sets_pass(self):
        class PassSemanticAgent(AuditAgentInterface):
            name = "test-pass-semantic"
            def run_audit(self, markdown, ledger, context=None):
                return AuditReport(
                    audit_status="pass", audit_score=100, findings=[],
                    metadata={"semantic_status": "pass"},
                )

        det = DeterministicAuditAgent()
        comp = CompositeAuditAgent(det, PassSemanticAgent())
        report = comp.run_audit("- Statement [src:SRC_ABCDEF]", _clean_ledger(), _clean_context())
        assert report.metadata["semantic_status"] == "pass"

    def test_failing_semantic_agent_sets_fail(self):
        from multi_agent_brief.core.schemas import AuditFinding

        class FailSemanticAgent(AuditAgentInterface):
            name = "test-fail-semantic"
            def run_audit(self, markdown, ledger, context=None):
                return AuditReport(
                    audit_status="fail", audit_score=50,
                    findings=[AuditFinding(
                        finding_id="SEM_001", severity="high",
                        finding_type="semantic_source_support",
                        description="Claim not supported",
                    )],
                    metadata={"semantic_status": "fail"},
                )

        det = DeterministicAuditAgent()
        comp = CompositeAuditAgent(det, FailSemanticAgent())
        report = comp.run_audit("- Statement [src:SRC_ABCDEF]", _clean_ledger(), _clean_context())
        assert report.metadata["semantic_status"] == "fail"
        assert report.audit_status == "fail"  # high finding from semantic

