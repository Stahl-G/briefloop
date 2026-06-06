"""Tests for Audience Profiles (PR C).

Audience Profiles provide deterministic configuration for brief structure,
quality thresholds, and DOCX templates based on the target audience.
"""
from __future__ import annotations

from multi_agent_brief.audience.profiles import (
    AudienceProfile,
    get_profile,
    map_audience_to_profile,
    PROFILES,
)
from multi_agent_brief.audit.final_quality import FinalQualityAuditAgent, FinalQualityConfig
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import PipelineContext


class TestMapAudienceToProfile:
    """Test mapping free-text audience to profile IDs."""

    def test_management_exact_match(self):
        assert map_audience_to_profile("management") == "management"

    def test_management_executive(self):
        assert map_audience_to_profile("executive") == "management"

    def test_management_ceo(self):
        assert map_audience_to_profile("ceo") == "management"

    def test_management_board(self):
        assert map_audience_to_profile("board") == "management"

    def test_management_chinese(self):
        assert map_audience_to_profile("管理层") == "management"

    def test_management_chinese_board(self):
        assert map_audience_to_profile("董事会") == "management"

    def test_research_exact_match(self):
        assert map_audience_to_profile("research") == "research"

    def test_research_analyst(self):
        assert map_audience_to_profile("research analyst") == "research"

    def test_research_industry(self):
        assert map_audience_to_profile("industry research") == "research"

    def test_research_chinese(self):
        assert map_audience_to_profile("研究员") == "research"

    def test_ir_exact_match(self):
        assert map_audience_to_profile("ir") == "ir"

    def test_ir_investor_relations(self):
        assert map_audience_to_profile("investor relations") == "ir"

    def test_ir_disclosure(self):
        assert map_audience_to_profile("disclosure") == "ir"

    def test_ir_chinese(self):
        assert map_audience_to_profile("投关") == "ir"

    def test_legal_exact_match(self):
        assert map_audience_to_profile("legal") == "legal_compliance"

    def test_legal_compliance(self):
        assert map_audience_to_profile("compliance") == "legal_compliance"

    def test_legal_regulatory(self):
        assert map_audience_to_profile("regulatory") == "legal_compliance"

    def test_legal_chinese(self):
        assert map_audience_to_profile("法务") == "legal_compliance"

    def test_legal_chinese_compliance(self):
        assert map_audience_to_profile("合规") == "legal_compliance"

    def test_unknown_audience_maps_to_default(self):
        assert map_audience_to_profile("marketing") == "default"

    def test_empty_audience_maps_to_default(self):
        assert map_audience_to_profile("") == "default"

    def test_whitespace_audience_maps_to_default(self):
        assert map_audience_to_profile("  ") == "default"


class TestGetProfile:
    """Test retrieving profiles by ID."""

    def test_get_management_profile(self):
        profile = get_profile("management")
        assert profile.profile_id == "management"
        assert profile.display_name == "Management / Executive"
        assert "Executive Summary" in profile.required_sections

    def test_get_research_profile(self):
        profile = get_profile("research")
        assert profile.profile_id == "research"
        assert profile.docx_template == "research_note"

    def test_get_ir_profile(self):
        profile = get_profile("ir")
        assert profile.profile_id == "ir"
        assert len(profile.required_disclaimers) > 0

    def test_get_legal_profile(self):
        profile = get_profile("legal_compliance")
        assert profile.profile_id == "legal_compliance"
        assert len(profile.required_disclaimers) > 0

    def test_get_default_profile(self):
        profile = get_profile("default")
        assert profile.profile_id == "default"

    def test_unknown_id_returns_default(self):
        profile = get_profile("nonexistent")
        assert profile.profile_id == "default"


class TestAudienceProfilesRegistry:
    """Test the profile registry completeness."""

    def test_all_expected_profiles_exist(self):
        expected = {"management", "research", "ir", "legal_compliance", "default"}
        assert set(PROFILES.keys()) == expected

    def test_all_profiles_have_required_sections(self):
        for profile_id, profile in PROFILES.items():
            assert len(profile.required_sections) > 0, f"{profile_id} has no required sections"

    def test_all_profiles_have_min_thresholds(self):
        for profile_id, profile in PROFILES.items():
            assert profile.min_markdown_chars > 0, f"{profile_id} has no min_markdown_chars"
            assert profile.min_main_sections > 0, f"{profile_id} has no min_main_sections"

    def test_all_profiles_have_docx_template(self):
        for profile_id, profile in PROFILES.items():
            assert profile.docx_template, f"{profile_id} has no docx_template"


class TestAudienceProfileInPipeline:
    """Test audience profile integration in the pipeline."""

    def test_profile_applied_to_final_quality_config(self):
        """Final quality config should inherit profile thresholds."""
        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/input",
            output_dir="/tmp/output",
            audience_profile="management",
        )
        config = FinalQualityConfig()
        from multi_agent_brief.audit.final_quality import build_final_quality_config
        resolved = build_final_quality_config(context, config)

        # Management profile has expected_summary_bullets = 5
        assert resolved.expected_summary_bullets == 5
        # Management profile has min_selected_claims = 20
        assert resolved.min_selected_claims == 20

    def test_research_profile_higher_thresholds(self):
        """Research profile should have higher thresholds."""
        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/input",
            output_dir="/tmp/output",
            audience_profile="research",
        )
        config = FinalQualityConfig()
        from multi_agent_brief.audit.final_quality import build_final_quality_config
        resolved = build_final_quality_config(context, config)

        # Research profile has expected_summary_bullets = 7
        assert resolved.expected_summary_bullets == 7
        # Research profile has min_selected_claims = 30
        assert resolved.min_selected_claims == 30

    def test_config_override_takes_precedence(self):
        """Explicit config should override profile thresholds."""
        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/input",
            output_dir="/tmp/output",
            audience_profile="management",
        )
        # Set explicit override in metadata
        context.metadata["final_quality"] = {
            "expected_summary_bullets": 10,
            "min_selected_claims": 50,
        }
        config = FinalQualityConfig()
        from multi_agent_brief.audit.final_quality import build_final_quality_config
        resolved = build_final_quality_config(context, config)

        # Config overrides should take precedence
        assert resolved.expected_summary_bullets == 10
        assert resolved.min_selected_claims == 50

    def test_empty_profile_uses_defaults(self):
        """Empty profile should fall back to defaults."""
        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/input",
            output_dir="/tmp/output",
            audience_profile="",
        )
        config = FinalQualityConfig()
        from multi_agent_brief.audit.final_quality import build_final_quality_config
        resolved = build_final_quality_config(context, config)

        # Should use base defaults
        assert resolved.expected_summary_bullets == 5
        assert resolved.min_selected_claims == 0  # base default


class TestAudienceProfileFromConfig:
    """Test audience profile resolution from config."""

    def test_profile_from_config_yaml(self):
        """Profile ID should be read from config.yaml."""
        from multi_agent_brief.core.config import build_run_settings

        config = {
            "project": {"name": "Test", "audience": "research"},
            "audience_profile": {"id": "research"},
            "report": {"date": "2026-06-02"},
            "input": {"path": "input"},
            "output": {"path": "output"},
        }
        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )
        assert settings["audience_profile"] == "research"

    def test_profile_mapped_from_audience(self):
        """Profile should be mapped from audience text when not in config."""
        from multi_agent_brief.core.config import build_run_settings

        config = {
            "project": {"name": "Test", "audience": "investor relations"},
            "report": {"date": "2026-06-02"},
            "input": {"path": "input"},
            "output": {"path": "output"},
        }
        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )
        assert settings["audience_profile"] == "ir"

    def test_profile_override_from_cli(self):
        """CLI audience should override config audience for profile mapping."""
        from multi_agent_brief.core.config import build_run_settings

        config = {
            "project": {"name": "Test", "audience": "management"},
            "report": {"date": "2026-06-02"},
            "input": {"path": "input"},
            "output": {"path": "output"},
        }
        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience="legal",
        )
        assert settings["audience_profile"] == "legal_compliance"
