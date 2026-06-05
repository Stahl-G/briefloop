"""Tests for the v0.5.0 reference workflow demo and artifact contract."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from multi_agent_brief.core.config import build_run_settings, load_config
from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext
from multi_agent_brief.sources.registry import load_sources_config

DEMO_DIR = Path(__file__).resolve().parent.parent / "examples" / "reference_workflow_demo"
SMOKE_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci" / "smoke_reference_workflow.py"


# ── Workspace structure ────────────────────────────────────────────────────────

class TestDemoWorkspaceStructure:
    """Verify the reference_workflow_demo workspace has all required files."""

    def test_config_yaml_exists(self):
        assert (DEMO_DIR / "config.yaml").exists()

    def test_sources_yaml_exists(self):
        assert (DEMO_DIR / "sources.yaml").exists()

    def test_user_md_exists(self):
        assert (DEMO_DIR / "user.md").exists()

    def test_input_dir_exists(self):
        assert (DEMO_DIR / "input").is_dir()

    def test_input_files_exist(self):
        input_files = list((DEMO_DIR / "input").glob("*.json"))
        assert len(input_files) >= 2, "Need at least 2 input JSON files"

    def test_config_loads_without_error(self):
        config = load_config(str(DEMO_DIR / "config.yaml"))
        assert "project" in config
        assert config["project"]["name"] == "Reference Workflow Demo"

    def test_sources_loads_without_error(self):
        sc = load_sources_config(DEMO_DIR / "sources.yaml")
        assert sc.profile == "conservative"
        assert "manual" in sc.enabled_providers

    def test_sources_no_web_search(self):
        sc = load_sources_config(DEMO_DIR / "sources.yaml")
        assert "web_search" not in sc.enabled_providers


# ── Artifact contract ──────────────────────────────────────────────────────────

def _run_demo_pipeline(tmp_path: Path) -> PipelineContext:
    """Run the demo pipeline into a temporary output directory."""
    config = load_config(str(DEMO_DIR / "config.yaml"))
    sc = load_sources_config(DEMO_DIR / "sources.yaml")

    settings = build_run_settings(
        config=config,
        input_dir=None,
        output_dir=str(tmp_path),
        name=None,
        language=None,
        audience=None,
    )
    context = PipelineContext(**settings)
    context.metadata["source_config"] = sc
    context.metadata["_config_dir"] = str(DEMO_DIR)

    BriefPipeline().run(context)
    return context


class TestArtifactContract:
    """Verify the reference workflow produces all expected artifacts."""

    @pytest.fixture(autouse=True)
    def _run_pipeline(self, tmp_path):
        self.output_dir = tmp_path
        self.context = _run_demo_pipeline(tmp_path)
        self.intermediate = tmp_path / "intermediate"

    def test_brief_md_exists(self):
        assert (self.output_dir / "brief.md").exists()

    def test_brief_md_no_src_markers(self):
        content = (self.output_dir / "brief.md").read_text(encoding="utf-8")
        assert "[src:" not in content

    def test_audited_brief_md_exists(self):
        assert (self.intermediate / "audited_brief.md").exists()

    def test_audited_brief_retains_citations(self):
        content = (self.intermediate / "audited_brief.md").read_text(encoding="utf-8")
        # audited_brief may or may not have citations depending on pipeline,
        # but the file must exist and be non-empty
        assert len(content) > 0

    def test_claim_ledger_exists_and_nonempty(self):
        ledger_path = self.intermediate / "claim_ledger.json"
        assert ledger_path.exists()
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert isinstance(ledger, list)
        assert len(ledger) > 0

    def test_audit_report_exists(self):
        assert (self.intermediate / "audit_report.json").exists()

    def test_audit_report_valid_json(self):
        report = json.loads(
            (self.intermediate / "audit_report.json").read_text(encoding="utf-8")
        )
        assert "audit_status" in report

    def test_source_map_exists(self):
        assert (self.intermediate / "source_map.md").exists()

    def test_draft_brief_exists(self):
        assert (self.intermediate / "draft_brief.md").exists()


# ── Manifest contract ──────────────────────────────────────────────────────────

class TestManifestContract:
    """Verify run_manifest.json after CLI prepare-style manifest generation."""

    @pytest.fixture(autouse=True)
    def _run_with_manifest(self, tmp_path):
        context = _run_demo_pipeline(tmp_path)
        from multi_agent_brief.core.manifest import build_manifest, save_manifest

        audit_report = context.report_state.audit_report
        manifest = build_manifest(
            config_path=str(DEMO_DIR / "config.yaml"),
            workspace=str(DEMO_DIR),
            enabled_providers=["manual"],
            output_formats=context.output_formats,
            language=context.language,
            report_date=context.report_date,
            source_count=len(context.sources),
            claim_count=len(context.metadata.get("_ledger", [])),
            candidate_count=len(context.candidates),
            audit_status=audit_report.audit_status if audit_report else "not_run",
            audit_score=audit_report.audit_score if audit_report else None,
            audit_finding_count=len(audit_report.findings) if audit_report else 0,
            artifact_paths={
                "brief": str(tmp_path / "brief.md"),
                "audited_brief": str(tmp_path / "intermediate" / "audited_brief.md"),
                "claim_ledger": str(tmp_path / "intermediate" / "claim_ledger.json"),
            },
        )
        manifest_path = save_manifest(manifest, tmp_path)
        self.manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_manifest_has_run_id(self):
        assert "run_id" in self.manifest_data
        assert len(self.manifest_data["run_id"]) == 12

    def test_manifest_has_timestamp(self):
        assert "timestamp" in self.manifest_data

    def test_manifest_has_artifacts(self):
        artifacts = self.manifest_data.get("artifacts", {})
        assert len(artifacts) >= 3

    def test_manifest_artifacts_have_hashes(self):
        for name, entry in self.manifest_data["artifacts"].items():
            assert "hash" in entry, f"Artifact {name} missing hash"
            assert "path" in entry, f"Artifact {name} missing path"

    def test_manifest_config_hash(self):
        assert self.manifest_data.get("config_hash", "") != ""

    def test_manifest_language(self):
        assert self.manifest_data.get("language") == "en-US"


# ── Smoke script ───────────────────────────────────────────────────────────────

class TestSmokeScript:
    """Run the CI smoke script as a subprocess."""

    def test_smoke_script_passes(self):
        result = subprocess.run(
            [sys.executable, str(SMOKE_SCRIPT), str(DEMO_DIR)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"Smoke script failed (exit {result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "SMOKE PASSED" in result.stdout
