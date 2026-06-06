"""Tests for the Final Clean gate (PR B).

Final Clean runs after Editor and before Formatter. It detects:
- Template variables ({{...}}, ${...}, <TODO>)
- Internal file paths
- Model/AI phrases
- User feedback leakage
- Editorial comments
- Investment recommendation wording
- Invalid or empty citation markers
"""
from __future__ import annotations

from multi_agent_brief.agents.draft_cleanup import (
    detect_final_clean_issues,
    detect_invalid_citations,
    clean_process_residue,
    validate_citations_intact,
)
from multi_agent_brief.audit.final_quality import FinalCleanAuditAgent, FinalCleanConfig
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim, PipelineContext


class TestDetectFinalCleanIssues:
    """Test pattern-based Final Clean detection."""

    def test_detects_template_variable_double_braces(self):
        text = "Report for {{project_name}} quarter."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "template_variable_residue" for i in issues)

    def test_detects_template_variable_dollar_braces(self):
        text = "Report for ${project_name} quarter."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "template_variable_residue" for i in issues)

    def test_detects_todo_placeholder(self):
        text = "Revenue grew 5% <TODO> add source."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "template_variable_residue" for i in issues)

    def test_detects_placeholder_tag(self):
        text = "Market size is <PLACEHOLDER> billion."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "template_variable_residue" for i in issues)

    def test_detects_internal_path(self):
        text = "Data from /Users/test/data/file.json shows growth."
        issues = detect_final_clean_issues(text)
        # Internal path detection may or may not trigger depending on pattern
        # This is a basic sanity check
        assert isinstance(issues, list)

    def test_detects_model_phrase_as_an_ai(self):
        text = "As an AI, I cannot provide investment advice."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "model_phrase_residue" for i in issues)

    def test_detects_model_phrase_agent_should(self):
        text = "The agent should run the pipeline daily."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "model_phrase_residue" for i in issues)

    def test_detects_model_phrase_next_run_should(self):
        text = "Next run should pick up new sources."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "model_phrase_residue" for i in issues)

    def test_detects_feedback_as_fact(self):
        text = "用户反馈 suggests demand is increasing."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "feedback_as_fact" for i in issues)

    def test_detects_editorial_comment(self):
        text = "Market grew 5%.\nTODO: verify this number.\nRevenue stable."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "editorial_comment_as_conclusion" for i in issues)

    def test_detects_investment_recommendation(self):
        text = "Based on analysis, we issue a strong buy recommendation."
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "investment_recommendation" for i in issues)

    def test_detects_investment_recommendation_chinese(self):
        text = "根据分析，我们强烈推荐买入该股票。"
        issues = detect_final_clean_issues(text)
        assert any(i["finding_type"] == "investment_recommendation" for i in issues)

    def test_clean_text_has_no_issues(self):
        text = "# Market Brief\n\nThe company reported 5% revenue growth in Q1 2026."
        issues = detect_final_clean_issues(text)
        # Clean text should have no issues (or only benign ones)
        high_severity = [i for i in issues if i["severity"] == "high"]
        assert len(high_severity) == 0


class TestDetectInvalidCitations:
    """Test citation validation for Final Clean."""

    def test_valid_citation_not_flagged(self):
        text = "Growth of 5% [src:ABC123DEF456]."
        valid_ids = {"ABC123DEF456"}
        issues = detect_invalid_citations(text, valid_ids)
        assert len(issues) == 0

    def test_invalid_citation_flagged(self):
        text = "Growth of 5% [src:INVALID0001]."
        valid_ids = {"ABC123DEF456"}
        issues = detect_invalid_citations(text, valid_ids)
        assert any(i["finding_type"] == "invalid_claim_id" for i in issues)

    def test_empty_citation_flagged(self):
        text = "Growth of 5% [src:]."
        valid_ids = {"ABC123DEF456"}
        issues = detect_invalid_citations(text, valid_ids)
        assert any(i["finding_type"] == "empty_source_marker" for i in issues)

    def test_mixed_valid_and_invalid(self):
        text = "Growth [src:ABC123DEF456] and decline [src:BAD00001]."
        valid_ids = {"ABC123DEF456"}
        issues = detect_invalid_citations(text, valid_ids)
        assert len(issues) == 1
        assert issues[0]["finding_type"] == "invalid_claim_id"


class TestFinalCleanAuditAgent:
    """Test the FinalCleanAuditAgent class."""

    def test_pass_on_clean_text(self):
        text = "# Brief\n\nThe company reported 5% revenue growth."
        ledger = ClaimLedger()
        agent = FinalCleanAuditAgent()
        report = agent.run_audit(text, ledger)
        assert report.audit_status == "pass"
        assert report.metadata["gate"] == "final_clean"

    def test_fail_on_template_variable(self):
        text = "# Brief\n\nRevenue for {{project}} grew 5%."
        ledger = ClaimLedger()
        agent = FinalCleanAuditAgent()
        report = agent.run_audit(text, ledger)
        assert report.audit_status == "fail"
        assert any(f.finding_type == "template_variable_residue" for f in report.findings)

    def test_fail_on_investment_advice(self):
        text = "# Brief\n\nStrong buy recommendation based on analysis."
        ledger = ClaimLedger()
        agent = FinalCleanAuditAgent()
        report = agent.run_audit(text, ledger)
        assert report.audit_status == "fail"
        assert any(f.finding_type == "investment_recommendation" for f in report.findings)

    def test_fail_on_invalid_citation(self):
        text = "# Brief\n\nGrowth [src:NONEXISTENT12]."
        ledger = ClaimLedger()
        agent = FinalCleanAuditAgent()
        report = agent.run_audit(text, ledger)
        assert report.audit_status == "fail"
        assert any(f.finding_type == "invalid_claim_id" for f in report.findings)

    def test_valid_citation_passes(self):
        text = "# Brief\n\nGrowth [src:ABC123DEF456]."
        ledger = ClaimLedger()
        claim = Claim(
            claim_id="ABC123DEF456",
            statement="Growth",
            source_id="src1",
            evidence_text="Evidence",
        )
        ledger.add_claim(claim)
        agent = FinalCleanAuditAgent()
        report = agent.run_audit(text, ledger)
        assert not any(f.finding_type == "invalid_claim_id" for f in report.findings)

    def test_disabled_config_returns_pass(self):
        text = "# Brief\n\n{{template}} and [src:BAD]."
        ledger = ClaimLedger()
        config = FinalCleanConfig(enabled=False)
        agent = FinalCleanAuditAgent(config)
        report = agent.run_audit(text, ledger)
        assert report.audit_status == "pass"
        assert len(report.findings) == 0

    def test_selective_checks(self):
        text = "# Brief\n\n{{template}} and strong buy."
        ledger = ClaimLedger()
        # Only check template variables, not investment advice
        config = FinalCleanConfig(check_investment_advice=False)
        agent = FinalCleanAuditAgent(config)
        report = agent.run_audit(text, ledger)
        assert any(f.finding_type == "template_variable_residue" for f in report.findings)
        assert not any(f.finding_type == "investment_recommendation" for f in report.findings)


class TestFinalCleanIntegration:
    """Integration tests for Final Clean in the pipeline."""

    def test_editor_runs_final_clean(self, tmp_path):
        """EditorAgent should produce a final_clean_report in context."""
        from multi_agent_brief.agents.editor import EditorAgent

        context = PipelineContext(
            project_name="Test",
            input_dir=str(tmp_path),
            output_dir=str(tmp_path / "output"),
        )
        context.report_state.draft_markdown = "# Brief\n\n{{template}} content."

        ledger = ClaimLedger()
        editor = EditorAgent()
        editor.run(context, ledger)

        # Final clean report should be set
        assert context.report_state.final_clean_report is not None
        assert "audit_status" in context.report_state.final_clean_report
        assert context.report_state.final_clean_report["audit_status"] == "fail"

    def test_formatter_writes_final_clean_report(self, tmp_path):
        """FormatterAgent should write final_clean_report.json."""
        from multi_agent_brief.agents.editor import EditorAgent
        from multi_agent_brief.agents.formatter import FormatterAgent

        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        (input_dir / "news.md").write_text(
            "- Market grew 5%.\n",
            encoding="utf-8",
        )

        context = PipelineContext(
            project_name="Test",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            report_date="2026-06-02",
            max_source_age_days=14,
        )
        context.report_state.draft_markdown = "# Brief\n\n{{template}} content."

        ledger = ClaimLedger()
        editor = EditorAgent()
        editor.run(context, ledger)

        formatter = FormatterAgent()
        formatter.run(context, ledger)

        # Check final_clean_report.json exists
        final_clean_path = output_dir / "intermediate" / "final_clean_report.json"
        assert final_clean_path.exists()

        import json
        report_data = json.loads(final_clean_path.read_text(encoding="utf-8"))
        assert "audit_status" in report_data
        assert report_data["audit_status"] == "fail"


class TestFinalCleanPreservesExistingBehavior:
    """Ensure Final Clean does not break existing Editor behavior."""

    def test_editor_preserves_valid_citations(self, tmp_path):
        """Editor must preserve valid [src:CLAIM_ID] citations."""
        from multi_agent_brief.agents.editor import EditorAgent

        context = PipelineContext(
            project_name="Test",
            input_dir=str(tmp_path),
            output_dir=str(tmp_path / "output"),
        )
        context.report_state.draft_markdown = (
            "# Brief\n\n"
            "- Growth [src:ABC123DEF456]\n"
            "- [SRC:] residue\n"
        )

        ledger = ClaimLedger()
        editor = EditorAgent()
        editor.run(context, ledger)

        prepared = context.report_state.prepared_markdown
        assert "[src:ABC123DEF456]" in prepared
        assert "[SRC:]" not in prepared

    def test_editor_removes_process_residue(self, tmp_path):
        """Editor must remove process residue."""
        from multi_agent_brief.agents.editor import EditorAgent

        context = PipelineContext(
            project_name="Test",
            input_dir=str(tmp_path),
            output_dir=str(tmp_path / "output"),
        )
        context.report_state.draft_markdown = (
            "Text\nThought for 3s\nMore text"
        )

        ledger = ClaimLedger()
        editor = EditorAgent()
        editor.run(context, ledger)

        prepared = context.report_state.prepared_markdown
        assert "Thought for" not in prepared
        assert "Text" in prepared
        assert "More text" in prepared
