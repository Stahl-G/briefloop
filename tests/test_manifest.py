"""Tests for Run Manifest — generation, serialization, and file hashes."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from multi_agent_brief.core.manifest import RunManifest, build_manifest, save_manifest, _file_hash


class TestRunManifestModel:
    def test_defaults(self):
        m = RunManifest()
        assert len(m.run_id) == 12
        assert m.audit_status == ""
        assert m.artifacts == {}
        assert m.stages == {}
        assert m.errors == []

    def test_to_dict_roundtrip(self):
        m = RunManifest(
            config_path="/tmp/config.yaml",
            config_hash="abc123",
            workspace="/tmp/ws",
            enabled_providers=["manual", "rss"],
            source_count=10,
            claim_count=5,
            audit_status="pass",
            audit_score=100,
        )
        d = m.to_dict()
        m2 = RunManifest.from_dict(d)
        assert m2.config_path == "/tmp/config.yaml"
        assert m2.config_hash == "abc123"
        assert m2.enabled_providers == ["manual", "rss"]
        assert m2.source_count == 10
        assert m2.claim_count == 5
        assert m2.audit_status == "pass"

    def test_export_json(self):
        m = RunManifest(source_count=3)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            m.export_json(f.name)
            data = json.loads(Path(f.name).read_text())
            assert data["source_count"] == 3
            assert "run_id" in data
            assert "timestamp" in data


class TestBuildManifest:
    def test_basic_build(self):
        m = build_manifest(
            enabled_providers=["manual"],
            source_count=5,
            claim_count=3,
            audit_status="pass",
            audit_score=95,
        )
        assert m.enabled_providers == ["manual"]
        assert m.source_count == 5
        assert m.claim_count == 3
        assert m.audit_status == "pass"
        assert m.audit_score == 95

    def test_artifact_hashes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test Brief\nSome content here.")
            f.flush()
            m = build_manifest(artifact_paths={"brief": f.name})
            assert "brief" in m.artifacts
            assert m.artifacts["brief"]["hash"] != ""
            assert m.artifacts["brief"]["path"] == f.name

    def test_missing_artifact_hash(self):
        m = build_manifest(artifact_paths={"brief": "/nonexistent/file.md"})
        assert m.artifacts["brief"]["hash"] == ""

    def test_config_hash(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("project_name: Test\n")
            f.flush()
            m = build_manifest(config_path=f.name)
            assert m.config_hash != ""
            assert len(m.config_hash) == 64  # full SHA-256 hex digest

    def test_stage_outputs(self):
        stages = [
            {"agent_name": "scout", "summary": "Found 10 claims"},
            {"agent_name": "formatter", "summary": "Wrote artifacts"},
        ]
        m = build_manifest(stage_outputs=stages)
        assert "scout" in m.stages
        assert m.stages["scout"]["status"] == "ok"
        assert "formatter" in m.stages

    def test_errors_recorded(self):
        errors = [{"stage": "source-collection", "error": "No API key"}]
        m = build_manifest(errors=errors)
        assert len(m.errors) == 1
        assert m.errors[0]["error"] == "No API key"

    def test_audit_not_run(self):
        m = build_manifest()
        assert m.audit_status == "not_run"


class TestSaveManifest:
    def test_saves_to_intermediate(self):
        m = build_manifest(source_count=2)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_manifest(m, tmpdir)
            assert path.name == "run_manifest.json"
            assert path.parent.name == "intermediate"
            data = json.loads(path.read_text())
            assert data["source_count"] == 2

    def test_creates_intermediate_dir(self):
        m = build_manifest()
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "output" / "deep"
            path = save_manifest(m, nested)
            assert path.exists()


class TestManifestFailureDetection:
    def test_failed_stage_detected_from_artifacts(self):
        stages = [
            {"agent_name": "analysis-module-market_competitor",
             "summary": "Module 'market_competitor' FAILED: ValueError",
             "artifacts": {"status": "failed", "error_type": "ValueError", "error": "bad data"}},
        ]
        m = build_manifest(stage_outputs=stages)
        assert m.stages["analysis-module-market_competitor"]["status"] == "failed"
        assert len(m.errors) == 1
        assert m.errors[0]["stage"] == "analysis-module-market_competitor"
        assert m.errors[0]["error_type"] == "ValueError"

    def test_failed_stage_detected_from_summary(self):
        stages = [
            {"agent_name": "source-collection",
             "summary": "Collected 0 sources. FAILED: No API key",
             "artifacts": {}},
        ]
        m = build_manifest(stage_outputs=stages)
        assert m.stages["source-collection"]["status"] == "failed"

    def test_ok_stage_no_errors(self):
        stages = [
            {"agent_name": "scout", "summary": "Loaded 5 sources", "artifacts": {}},
        ]
        m = build_manifest(stage_outputs=stages)
        assert m.stages["scout"]["status"] == "ok"
        assert len(m.errors) == 0

    def test_collection_errors_copied_to_manifest(self):
        stages = [
            {"agent_name": "source-collection",
             "summary": "Collected 0 sources",
             "artifacts": {
                 "collection_errors": [
                     {"provider": "web_search", "error_type": "NoSearchTasks",
                      "message": "No search_tasks defined"},
                 ],
             }},
        ]
        m = build_manifest(stage_outputs=stages)
        assert len(m.errors) == 1
        assert m.errors[0]["error_type"] == "NoSearchTasks"


class TestFileHash:
    def test_hash_matches(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("same content")
            f.flush()
            h1 = _file_hash(Path(f.name))
            h2 = _file_hash(Path(f.name))
            assert h1 == h2
            assert len(h1) == 64  # full SHA-256 hex digest

    def test_different_content_different_hash(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".a", delete=False) as f1, \
             tempfile.NamedTemporaryFile(mode="w", suffix=".b", delete=False) as f2:
            f1.write("content A")
            f1.flush()
            f2.write("content B")
            f2.flush()
            assert _file_hash(Path(f1.name)) != _file_hash(Path(f2.name))

    def test_missing_file_returns_empty(self):
        assert _file_hash(Path("/nonexistent/file.txt")) == ""
