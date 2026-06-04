"""Tests for B01: delivered brief must be auditable without exposing Claim IDs.

These tests verify the core contract:
- Editor must preserve valid [src:CLAIM_ID] citations (only remove process residue)
- Auditor audits the Editor-prepared text with citations
- Formatter writes that audited text to intermediate/audited_brief.md
- Formatter writes output/brief.md as a deterministic citation-stripped reader copy
"""
from __future__ import annotations

from pathlib import Path

import json

from multi_agent_brief.agents.analyst import AnalystAgent
from multi_agent_brief.agents.editor import EditorAgent
from multi_agent_brief.agents.auditor import AuditorAgent
from multi_agent_brief.agents.formatter import FormatterAgent
from multi_agent_brief.agents.draft_cleanup import strip_claim_citations
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import Claim, PipelineContext


class TestB01EditorPreservesCitations:
    """Editor must only remove process residue, NOT valid [src:CLAIM_ID] citations."""

    def test_editor_preserves_valid_citations(self, tmp_path):
        """After Editor cleanup, valid [src:CLAIM_ID] citations must still be present."""
        ledger = ClaimLedger()
        claim = Claim(
            claim_id="SRC001_A1B2C3D4E5",
            statement="A competitor announced a 2 GW factory expansion.",
            source_id="SRC001",
            evidence_text="A competitor announced a 2 GW factory expansion.",
            claim_type="fact",
            metadata={"topic": "competitor", "published_at": "2026-06-01"},
        )
        ledger.add_claim(claim)

        context = PipelineContext(
            project_name="B01 Test",
            input_dir=str(tmp_path),
            output_dir=str(tmp_path / "output"),
        )

        # Simulate Analyst producing draft with citations
        analyst = AnalystAgent()
        analyst.run(context, ledger)

        draft_before = context.report_state.draft_markdown
        # Verify draft has citations before Editor
        assert "[src:SRC001_A1B2C3D4E5]" in draft_before, (
            "Precondition: draft_markdown must contain [src:CLAIM_ID] citations"
        )

        # Run Editor
        editor = EditorAgent()
        editor.run(context, ledger)

        prepared = context.report_state.prepared_markdown
        # Editor must preserve valid citations
        assert "[src:SRC001_A1B2C3D4E5]" in prepared, (
            "B01 FAIL: Editor stripped valid [src:CLAIM_ID] citation — "
            "final brief would be untraceable"
        )

    def test_editor_removes_residue_but_keeps_citations(self, tmp_path):
        """Editor must remove [SRC:], [SOURCE:], empty [src:] but keep valid [src:CLAIM_ID]."""
        context = PipelineContext(
            project_name="B01 Residue Test",
            input_dir=str(tmp_path),
            output_dir=str(tmp_path / "output"),
        )
        # Simulate draft with both process residue and valid citations
        context.report_state.draft_markdown = (
            "# Test Brief\n\n"
            "## Market\n\n"
            "- 2026-06-01｜Market expanded 5% [src:SRC001_A1B2C3D4E5]\n"
            "- Price stable [SRC:] [SOURCE:] [src:]\n"
            "Thought for 3s\n"
            "- Another claim [src:SRC002_B2C3D4E5F6]\n"
        )

        ledger = ClaimLedger()
        editor = EditorAgent()
        editor.run(context, ledger)

        prepared = context.report_state.prepared_markdown

        # Valid citations must be preserved
        assert "[src:SRC001_A1B2C3D4E5]" in prepared, (
            "B01 FAIL: Valid citation [src:SRC001_A1B2C3D4E5] was removed"
        )
        assert "[src:SRC002_B2C3D4E5F6]" in prepared, (
            "B01 FAIL: Valid citation [src:SRC002_B2C3D4E5F6] was removed"
        )

        # Process residue must be removed
        assert "[SRC:]" not in prepared, "Process residue [SRC:] should be removed"
        assert "[SOURCE:]" not in prepared, "Process residue [SOURCE:] should be removed"
        # "Thought for" line is entirely removed — the line is gone, citation on separate line preserved
        assert "Thought for" not in prepared, "Process residue 'Thought for' should be removed"

    def test_editor_no_strip_claim_citations_effect(self, tmp_path):
        """Verify that strip_claim_citations is NOT called — 
        the prepared_markdown must have the same citations as draft_markdown
        (modulo process residue removal)."""
        from multi_agent_brief.agents.draft_cleanup import _VALID_SRC_REF

        context = PipelineContext(
            project_name="B01 Citation Count",
            input_dir=str(tmp_path),
            output_dir=str(tmp_path / "output"),
        )
        context.report_state.draft_markdown = (
            "# Brief\n\n"
            "- Claim one [src:A1B2C3D4E5F6]\n"
            "- Claim two [src:B2C3D4E5F6A1]\n"
            "- Claim three [src:C3D4E5F6A1B2]\n"
        )

        ledger = ClaimLedger()
        editor = EditorAgent()
        editor.run(context, ledger)

        prepared = context.report_state.prepared_markdown

        draft_citations = set(_VALID_SRC_REF.findall(context.report_state.draft_markdown))
        prepared_citations = set(_VALID_SRC_REF.findall(prepared))

        assert draft_citations == prepared_citations, (
            f"B01 FAIL: Citation sets differ. Draft: {draft_citations}, Prepared: {prepared_citations}"
        )


class TestB01AuditedTextAndReaderBrief:
    """Auditor must audit the claim-cited text that the reader brief derives from."""

    def test_auditor_audits_prepared_not_draft(self, tmp_path):
        """After pipeline run, the audited text must be prepared_markdown and
        the reader brief must be a deterministic citation-stripped copy."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        # Create input with a claim that will produce a citation
        (input_dir / "news.md").write_text(
            "- A competitor announced a 2 GW manufacturing expansion plan.\n",
            encoding="utf-8",
        )

        context = PipelineContext(
            project_name="B01 Audit Test",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            report_date="2026-06-02",
            max_source_age_days=14,
        )

        outputs = BriefPipeline().run(context)

        # Read the reader-facing brief and the audited internal brief
        brief_text = (output_dir / "brief.md").read_text(encoding="utf-8")
        audited_text = (output_dir / "intermediate" / "audited_brief.md").read_text(encoding="utf-8")

        # Read the audit report to find what was audited
        audit_report_path = output_dir / "intermediate" / "audit_report.json"
        audit_data = json.loads(audit_report_path.read_text(encoding="utf-8"))

        from multi_agent_brief.audit.deterministic import SRC_REF_PATTERN

        assert not SRC_REF_PATTERN.findall(brief_text), (
            "B01 FAIL: reader-facing brief.md must not expose [src:CLAIM_ID] markers"
        )
        assert SRC_REF_PATTERN.findall(audited_text), (
            "B01 FAIL: audited_brief.md must preserve [src:CLAIM_ID] markers for audit"
        )
        assert brief_text == strip_claim_citations(audited_text)
        assert audit_data.get("metadata", {}).get("refs_extracted", 0) > 0
        assert audit_data.get("metadata", {}).get("audited_markdown_artifact", "").endswith("audited_brief.md")
        assert audit_data.get("metadata", {}).get("reader_brief_transform") == "strip_claim_citations"

    def test_full_pipeline_audited_brief_traceable_reader_brief_clean(self, tmp_path):
        """End-to-end: audited_brief references the ledger while brief.md is clean."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        (input_dir / "news.md").write_text(
            "- Market prices declined 3.5% week-over-week.\n"
            "- New trade regulation announced for industrial products.\n",
            encoding="utf-8",
        )

        context = PipelineContext(
            project_name="B01 Traceability Test",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            report_date="2026-06-02",
            max_source_age_days=14,
        )

        BriefPipeline().run(context)

        # Read final reader brief and audited internal brief
        brief_text = (output_dir / "brief.md").read_text(encoding="utf-8")
        audited_text = (output_dir / "intermediate" / "audited_brief.md").read_text(encoding="utf-8")

        # Read claim ledger
        ledger = ClaimLedger.import_json(
            output_dir / "intermediate" / "claim_ledger.json"
        )

        from multi_agent_brief.audit.deterministic import SRC_REF_PATTERN
        brief_refs = SRC_REF_PATTERN.findall(brief_text)
        audited_refs = SRC_REF_PATTERN.findall(audited_text)

        assert not brief_refs, (
            "B01 FAIL: reader-facing brief.md must not expose [src:CLAIM_ID] citations"
        )
        assert len(audited_refs) > 0, (
            "B01 FAIL: audited_brief.md has zero [src:CLAIM_ID] citations — "
            "the audited text is not traceable to Claim Ledger"
        )
        assert brief_text == strip_claim_citations(audited_text)

        for ref in audited_refs:
            assert ledger.get_claim(ref) is not None, (
                f"B01 FAIL: [src:{ref}] found in audited_brief.md but NOT in "
                "claim_ledger.json — orphan citation in audited output"
            )

    def test_reader_brief_is_stripped_derivative_of_audited_text(self, tmp_path):
        """After the pipeline runs, brief.md should be a deterministic stripped
        derivative of the exact text Auditor audited."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        (input_dir / "news.md").write_text(
            "- Competitor increased capacity by 5 GW.\n",
            encoding="utf-8",
        )

        context = PipelineContext(
            project_name="B01 Text Match Test",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            report_date="2026-06-02",
            max_source_age_days=14,
        )

        outputs = BriefPipeline().run(context)

        brief_text = (output_dir / "brief.md").read_text(encoding="utf-8")
        audited_text = (output_dir / "intermediate" / "audited_brief.md").read_text(encoding="utf-8")
        prepared = context.report_state.prepared_markdown

        assert audited_text == prepared, (
            "B01 FAIL: audited_brief.md content does not match prepared_markdown — "
            "Formatter is not preserving the audited text"
        )
        assert brief_text == strip_claim_citations(prepared), (
            "B01 FAIL: brief.md must be a deterministic citation-stripped copy "
            "of the audited prepared_markdown"
        )
