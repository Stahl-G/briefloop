"""Tests for Audience Profiles (PR C).

Audience Profiles provide deterministic configuration for brief structure,
quality thresholds, and DOCX templates based on the target audience.
"""
from __future__ import annotations

from multi_agent_brief.audience.profiles import PROFILES
from multi_agent_brief.audit.final_quality import FinalQualityConfig
from multi_agent_brief.core.schemas import PipelineContext


class TestAudienceProfilesRegistry:
    """Test the profile registry completeness."""

    def test_all_expected_profiles_exist(self):
        expected = {"management", "research", "ir", "legal_compliance", "default"}
        assert set(PROFILES.keys()) == expected


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
