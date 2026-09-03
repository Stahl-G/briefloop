"""Tests for the draft_cleanup module and text cleanup behavior."""
from __future__ import annotations

from multi_agent_brief.tools.draft_cleanup import clean_process_residue


class TestCleanProcessResidue:
    """Test that process residue is removed from final text."""

    def test_removes_residue_preserves_citation(self):
        text = "Fact [src:ABC123XYZ] [SRC:] Thought for 3s"
        result = clean_process_residue(text)
        assert "[src:ABC123XYZ]" in result
        assert "[SRC:]" not in result
        assert "Thought for" not in result

    def test_preserves_valid_citation(self):
        text = "Important fact [src:ABC123XYZ]"
        result = clean_process_residue(text)
        assert "[src:ABC123XYZ]" in result
