"""Tests for the final_text_cleanup module and EditorAgent cleanup behavior."""
from __future__ import annotations

from multi_agent_brief.agents.final_text_cleanup import (
    clean_process_residue,
    validate_citations_intact,
)


class TestCleanProcessResidue:
    """Test that process residue is removed from final text."""

    def test_removes_src_empty(self):
        text = "Some text [SRC:] more text"
        result = clean_process_residue(text)
        assert "[SRC:]" not in result
        assert "Some text" in result

    def test_removes_source_empty(self):
        text = "Some text [SOURCE:] more text"
        result = clean_process_residue(text)
        assert "[SOURCE:]" not in result

    def test_removes_empty_src(self):
        text = "Some text [src:] more text"
        result = clean_process_residue(text)
        assert "[src:]" not in result

    def test_removes_empty_src_with_space(self):
        text = "Some text [src: ] more text"
        result = clean_process_residue(text)
        assert "[src:" not in result

    def test_removes_thought_for(self):
        text = "Line one\nThought for 5 seconds\nLine two"
        result = clean_process_residue(text)
        assert "Thought for" not in result
        assert "Line one" in result
        assert "Line two" in result

    def test_removes_bash_call(self):
        text = "Text\nBash(ls -la)\nMore text"
        result = clean_process_residue(text)
        assert "Bash(" not in result
        assert "Text" in result
        assert "More text" in result

    def test_removes_agent_completed(self):
        text = "Text\nAgent completed successfully\nMore text"
        result = clean_process_residue(text)
        assert "Agent completed" not in result

    def test_removes_audit_in_background(self):
        text = "Text\naudit in background\nMore text"
        result = clean_process_residue(text)
        assert "audit in background" not in result

    def test_collapses_multiple_blank_lines(self):
        text = "Line one\n\n\n\n\nLine two"
        result = clean_process_residue(text)
        assert "\n\n\n" not in result

    def test_preserves_valid_citation(self):
        text = "Important fact [src:ABC123XYZ]"
        result = clean_process_residue(text)
        assert "[src:ABC123XYZ]" in result

    def test_removes_residue_preserves_citation(self):
        text = "Fact [src:ABC123XYZ] [SRC:] Thought for 3s"
        result = clean_process_residue(text)
        assert "[src:ABC123XYZ]" in result
        assert "[SRC:]" not in result
        assert "Thought for" not in result

    def test_empty_input(self):
        assert clean_process_residue("") == ""

    def test_whitespace_only(self):
        assert clean_process_residue("   ") == ""


class TestValidateCitationsIntact:
    """Test that citation validation works correctly."""

    def test_all_citations_preserved(self):
        original = "Fact [src:ABC123XYZ] and [src:DEF456UVW]"
        cleaned = "Fact [src:ABC123XYZ] and [src:DEF456UVW]"
        assert validate_citations_intact(original, cleaned) is True

    def test_citation_lost(self):
        original = "Fact [src:ABC123XYZ] and [src:DEF456UVW]"
        cleaned = "Fact [src:ABC123XYZ] only"
        assert validate_citations_intact(original, cleaned) is False

    def test_no_citations(self):
        assert validate_citations_intact("plain text", "plain text") is True

    def test_residue_removed_citations_kept(self):
        original = "Fact [src:ABC123XYZ] [SRC:] [SOURCE:]"
        cleaned = "Fact [src:ABC123XYZ]"
        assert validate_citations_intact(original, cleaned) is True

    def test_empty_src_not_counted_as_citation(self):
        """[src:] (empty) should not be counted as a valid citation."""
        original = "Text [src:]"
        cleaned = "Text"
        assert validate_citations_intact(original, cleaned) is True
