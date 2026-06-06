"""Tests for HistoryStore module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.history.store import HistoryReport, HistoryStore


class TestHistoryReport:
    """Test HistoryReport dataclass."""

    def test_to_dict_empty(self):
        """Empty report to_dict returns correct structure."""
        report = HistoryReport()
        d = report.to_dict()
        assert d["previous_brief_loaded"] is False
        assert d["previous_ledger_loaded"] is False
        assert d["total_previous_claims"] == 0
        assert d["repeat_claims"] == 0
        assert d["novel_claims"] == 0
        assert d["warnings"] == []

    def test_to_dict_with_data(self):
        """Report with data to_dict returns correct structure."""
        report = HistoryReport(
            previous_brief_loaded=True,
            previous_brief_hash="abc123",
            total_previous_claims=10,
            repeat_claims=3,
            novel_claims=7,
            warnings=["test warning"],
        )
        d = report.to_dict()
        assert d["previous_brief_loaded"] is True
        assert d["previous_brief_hash"] == "abc123"
        assert d["total_previous_claims"] == 10
        assert d["repeat_claims"] == 3
        assert d["novel_claims"] == 7
        assert d["warnings"] == ["test warning"]


class TestHistoryStore:
    """Test HistoryStore."""

    def test_load_no_paths(self, tmp_path):
        """Loading with no paths produces no warnings."""
        store = HistoryStore(workspace_path=tmp_path)
        report = store.load()
        assert report.previous_brief_loaded is False
        assert report.previous_ledger_loaded is False
        assert report.warnings == []

    def test_load_missing_paths_warning(self, tmp_path):
        """Loading with missing paths produces warnings."""
        store = HistoryStore(
            workspace_path=tmp_path,
            previous_brief_path=tmp_path / "nonexistent_brief.md",
            previous_ledger_path=tmp_path / "nonexistent_ledger.json",
        )
        report = store.load()
        assert report.previous_brief_loaded is False
        assert report.previous_ledger_loaded is False
        assert len(report.warnings) == 2
        assert "Previous brief not found" in report.warnings[0]
        assert "Previous ledger not found" in report.warnings[1]

    def test_load_previous_brief(self, tmp_path):
        """Loading previous brief from file."""
        brief_path = tmp_path / "previous_brief.md"
        brief_path.write_text("# Previous Brief\n\nSome content.", encoding="utf-8")

        store = HistoryStore(
            workspace_path=tmp_path,
            previous_brief_path=brief_path,
        )
        report = store.load()

        assert report.previous_brief_loaded is True
        assert report.previous_brief_hash != ""
        assert store.get_previous_brief() == "# Previous Brief\n\nSome content."

    def test_load_previous_ledger(self, tmp_path):
        """Loading previous ledger from file."""
        ledger_path = tmp_path / "previous_ledger.json"
        ledger_data = {
            "claims": [
                {"claim_id": "CLM_001", "statement": "Claim 1"},
                {"claim_id": "CLM_002", "statement": "Claim 2"},
            ]
        }
        ledger_path.write_text(json.dumps(ledger_data), encoding="utf-8")

        store = HistoryStore(
            workspace_path=tmp_path,
            previous_ledger_path=ledger_path,
        )
        report = store.load()

        assert report.previous_ledger_loaded is True
        assert report.previous_ledger_hash != ""
        assert report.total_previous_claims == 2
        assert store.get_previous_ledger() == ledger_data

    def test_load_previous_source_map(self, tmp_path):
        """Loading previous source map from file."""
        source_map_path = tmp_path / "previous_source_map.md"
        source_map_path.write_text("# Source Map\n\nSources.", encoding="utf-8")

        store = HistoryStore(
            workspace_path=tmp_path,
            previous_source_map_path=source_map_path,
        )
        report = store.load()

        assert report.previous_source_map_loaded is True
        assert report.previous_source_map_hash != ""
        assert store.get_previous_source_map() == "# Source Map\n\nSources."

    def test_load_entity_history(self, tmp_path):
        """Loading entity history from JSONL file."""
        entity_path = tmp_path / "entity_history.jsonl"
        entity_path.write_text(
            '{"entity": "Company A", "event": "earnings"}\n'
            '{"entity": "Company B", "event": "merger"}\n',
            encoding="utf-8",
        )

        store = HistoryStore(
            workspace_path=tmp_path,
            entity_history_path=entity_path,
        )
        report = store.load()

        assert report.entity_history_loaded is True
        assert len(store.get_entity_history()) == 2
        assert store.get_entity_history()[0]["entity"] == "Company A"

    def test_load_manifest_history(self, tmp_path):
        """Loading manifest history from JSONL file."""
        manifest_path = tmp_path / "manifest_history.jsonl"
        manifest_path.write_text(
            '{"run_id": "run_001", "status": "success"}\n'
            '{"run_id": "run_002", "status": "failed"}\n',
            encoding="utf-8",
        )

        store = HistoryStore(
            workspace_path=tmp_path,
            manifest_history_path=manifest_path,
        )
        report = store.load()

        assert report.manifest_history_loaded is True
        assert len(store.get_manifest_history()) == 2
        assert store.get_manifest_history()[0]["run_id"] == "run_001"

    def test_repeat_novelty_tracking(self, tmp_path):
        """Repeat/novelty tracking works correctly."""
        ledger_path = tmp_path / "previous_ledger.json"
        ledger_data = {
            "claims": [
                {"claim_id": "CLM_001", "statement": "Claim 1"},
                {"claim_id": "CLM_002", "statement": "Claim 2"},
                {"claim_id": "CLM_003", "statement": "Claim 3"},
            ]
        }
        ledger_path.write_text(json.dumps(ledger_data), encoding="utf-8")

        store = HistoryStore(
            workspace_path=tmp_path,
            previous_ledger_path=ledger_path,
        )
        store.load()

        # Mark current claims: 2 repeats, 1 novel
        store.mark_current_claims({"CLM_001", "CLM_002", "CLM_004"})

        assert store.get_repeat_claims() == {"CLM_001", "CLM_002"}
        assert store.get_novel_claims() == {"CLM_004"}

    def test_update_report_with_counts(self, tmp_path):
        """Update report with repeat/novelty counts."""
        ledger_path = tmp_path / "previous_ledger.json"
        ledger_data = {
            "claims": [
                {"claim_id": "CLM_001", "statement": "Claim 1"},
                {"claim_id": "CLM_002", "statement": "Claim 2"},
            ]
        }
        ledger_path.write_text(json.dumps(ledger_data), encoding="utf-8")

        store = HistoryStore(
            workspace_path=tmp_path,
            previous_ledger_path=ledger_path,
        )
        report = store.load()

        store.mark_current_claims({"CLM_001", "CLM_003"})
        report = store.update_report_with_counts(report)

        assert report.repeat_claims == 1
        assert report.novel_claims == 1

    def test_save_history_report(self, tmp_path):
        """Save history report to JSON file."""
        store = HistoryStore(workspace_path=tmp_path)
        report = HistoryReport(
            previous_brief_loaded=True,
            total_previous_claims=5,
            repeat_claims=2,
            novel_claims=3,
        )

        output_path = tmp_path / "history_report.json"
        store.save_history_report(output_path, report)

        assert output_path.exists()
        saved = json.loads(output_path.read_text(encoding="utf-8"))
        assert saved["previous_brief_loaded"] is True
        assert saved["total_previous_claims"] == 5
        assert saved["repeat_claims"] == 2
        assert saved["novel_claims"] == 3

    def test_invalid_jsonl_skips_line(self, tmp_path):
        """Invalid JSONL lines are skipped with warning."""
        entity_path = tmp_path / "entity_history.jsonl"
        entity_path.write_text(
            '{"entity": "Company A"}\n'
            'invalid json line\n'
            '{"entity": "Company B"}\n',
            encoding="utf-8",
        )

        store = HistoryStore(
            workspace_path=tmp_path,
            entity_history_path=entity_path,
        )
        store.load()

        # Should load 2 valid records, skip 1 invalid
        assert len(store.get_entity_history()) == 2
