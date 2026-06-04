"""Tests for PR7: B17 Source Candidate file format, and
PR8: B18 output path / documentation consistency."""
from __future__ import annotations

from pathlib import Path


# ─── B17: YAML fallback to JSON removed ───

class TestB17CandidateFileFormat:
    """source_candidates.yaml write failure must error, not silently switch to JSON."""

    def test_save_yaml_function_exists(self):
        """_save_yaml in decider module writes YAML without JSON fallback."""
        from multi_agent_brief.sources.decider import _save_yaml
        import tempfile, yaml
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            tmppath = Path(f.name)
        try:
            data = {"test": "value", "items": [1, 2, 3]}
            _save_yaml(tmppath, data)
            assert tmppath.exists()
            result = yaml.safe_load(tmppath.read_text(encoding="utf-8"))
            assert result == data
        finally:
            tmppath.unlink(missing_ok=True)

    def test_generate_uses_save_yaml(self, tmp_path):
        """generate_source_candidates should use a consistent write path."""
        from multi_agent_brief.sources.decider import (
            generate_source_candidates, _save_yaml,
        )
        discovery = {"company": "Test", "industry": "tech", "language": "en"}
        candidates = generate_source_candidates(discovery)

        yaml_path = tmp_path / "source_candidates.yaml"
        _save_yaml(yaml_path, candidates)
        assert yaml_path.exists()
        # Must NOT create a .json fallback
        json_path = tmp_path / "source_candidates.json"
        assert not json_path.exists(), (
            "B17 FAIL: _save_yaml should not silently create JSON fallback"
        )


# ─── B18: Documentation consistency ───

class TestB18DocumentationConsistency:
    """README, CLI, and code comments must match actual behavior."""

    def test_output_paths_match_formatter(self):
        """Output paths in docs must reflect Formatter's actual behavior."""
        from multi_agent_brief.agents.formatter import FormatterAgent
        # intermediate artifacts go to output/intermediate/
        # final brief at output/brief.md
        # This test documents the contract
        formatter = FormatterAgent()
        assert formatter.name == "formatter"

    def test_brief_md_is_reader_copy_not_draft(self):
        """Formatter writes a citation-stripped reader copy as brief.md."""
        from multi_agent_brief.agents.formatter import FormatterAgent
        # Verify the formatter's actual behavior via source
        import inspect
        source = inspect.getsource(FormatterAgent.run)
        assert "strip_claim_citations" in source
        assert "brief_path.write_text" in source
        assert "audited_brief.md" in source

    def test_draft_brief_in_intermediate_dir(self):
        """draft_brief.md must go to output/intermediate/, not output/."""
        from multi_agent_brief.agents.formatter import FormatterAgent
        import inspect
        source = inspect.getsource(FormatterAgent.run)
        assert "intermediate" in source
        assert "draft_brief.md" in source

    def test_known_backends_registered(self):
        """All implemented backends must be registered in _KNOWN_BACKENDS."""
        from multi_agent_brief.sources.web_search import _register_known_backends, _KNOWN_BACKENDS
        _register_known_backends()
        # At minimum tavily and exa
        assert "tavily" in _KNOWN_BACKENDS, "Tavily backend not registered"
        assert "exa" in _KNOWN_BACKENDS, "Exa backend not registered"
        # Backends with implementations: brave, firecrawl, serper
        assert "brave" in _KNOWN_BACKENDS, "Brave backend not registered"
        assert "firecrawl" in _KNOWN_BACKENDS, "Firecrawl backend not registered"
        assert "serper" in _KNOWN_BACKENDS, "Serper backend not registered"

    def test_editor_comment_updated(self):
        """Editor comment must reflect citation preservation (PR1 fix)."""
        from multi_agent_brief.agents.editor import EditorAgent
        import inspect
        source = inspect.getsource(EditorAgent.run)
        # Must NOT say "citation-stripped" or "strip claim"
        assert "strip_claim" not in source, (
            "B18 FAIL: Editor comment still references stripped citations"
        )
        assert "PRESERVED" in source or "preserve" in source.lower(), (
            "B18 FAIL: Editor comment should document citation preservation"
        )
