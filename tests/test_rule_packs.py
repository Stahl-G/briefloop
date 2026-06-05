"""Tests for Audit Finding Taxonomy and Rule Packs."""

from __future__ import annotations

from multi_agent_brief.audit.rule_packs import RULE_PACK, get_taxonomy, tag_finding, list_uncovered_types
from multi_agent_brief.core.schemas import AuditFinding


class TestRulePackCompleteness:
    """Every finding type used in the codebase should have a rule pack entry."""

    def test_all_harness_types_covered(self):
        harness_types = {
            "no_reportable_claims", "placeholder_text", "internal_process_term",
            "step_label_residue", "compilation_residue", "unsupported_certainty",
            "investment_advice_language", "needs_recrawl_claim_used",
            "low_confidence_source_used", "low_source_density",
            "possible_eia_unit_inflation", "repeat_claim_in_summary",
            "stale_filler_language",
        }
        missing = list_uncovered_types(harness_types)
        assert missing == [], f"Uncovered harness types: {missing}"

    def test_all_deterministic_types_covered(self):
        det_types = {
            "missing_claim", "number_without_source", "missing_source",
            "duplicate_claim", "missing_source_date", "stale_source",
            "redaction_risk",
        }
        missing = list_uncovered_types(det_types)
        assert missing == [], f"Uncovered deterministic types: {missing}"

    def test_all_epistemic_types_covered(self):
        epic_types = {
            "hypothesis_high_confidence", "action_without_basis",
            "analogy_without_limitations", "analogy_direct_relation",
        }
        missing = list_uncovered_types(epic_types)
        assert missing == [], f"Uncovered epistemic types: {missing}"

    def test_all_mc_specialist_types_covered(self):
        mc_types = {
            "comparison_missing_entity_evidence", "capacity_status_missing",
            "metric_basis_missing", "unsupported_market_trend",
            "single_source_interpretation", "competitor_coverage_gap",
        }
        missing = list_uncovered_types(mc_types)
        assert missing == [], f"Uncovered MC types: {missing}"

    def test_all_semantic_types_covered(self):
        semantic_types = {"semantic_source_support"}
        missing = list_uncovered_types(semantic_types)
        assert missing == [], f"Uncovered semantic types: {missing}"


class TestGetTaxonomy:
    def test_known_type(self):
        level, owner = get_taxonomy("missing_source")
        assert level == "source_blocking"
        assert owner == "source"

    def test_editor_fixable_type(self):
        level, owner = get_taxonomy("placeholder_text")
        assert level == "editor_fixable"
        assert owner == "editor"

    def test_safety_type(self):
        level, owner = get_taxonomy("investment_advice_language")
        assert level == "safety_blocking"
        assert owner == "safety"

    def test_unknown_type_defaults(self):
        level, owner = get_taxonomy("some_unknown_finding")
        assert level == "editor_fixable"
        assert owner == "editor"


class TestTagFinding:
    def test_returns_dict(self):
        tags = tag_finding("missing_source")
        assert tags["blocking_level"] == "source_blocking"
        assert tags["repair_owner"] == "source"

    def test_unknown_returns_defaults(self):
        tags = tag_finding("unknown")
        assert tags["blocking_level"] == "editor_fixable"
        assert tags["repair_owner"] == "editor"


class TestAuditFindingTaxonomy:
    def test_finding_has_taxonomy_fields(self):
        f = AuditFinding(
            finding_id="T001",
            severity="high",
            finding_type="missing_source",
            description="Test",
            blocking_level="source_blocking",
            repair_owner="source",
        )
        d = f.to_dict()
        assert d["blocking_level"] == "source_blocking"
        assert d["repair_owner"] == "source"

    def test_finding_defaults_to_editor(self):
        f = AuditFinding(
            finding_id="T002",
            severity="low",
            finding_type="test",
            description="Test",
        )
        assert f.blocking_level == "editor_fixable"
        assert f.repair_owner == "editor"

    def test_finding_roundtrip_preserves_taxonomy(self):
        f = AuditFinding(
            finding_id="T003",
            severity="medium",
            finding_type="unsupported_certainty",
            description="Test",
            blocking_level="analyst_blocking",
            repair_owner="analyst",
        )
        d = f.to_dict()
        f2 = AuditFinding(**{k: v for k, v in d.items() if k in AuditFinding.__dataclass_fields__})
        assert f2.blocking_level == "analyst_blocking"
        assert f2.repair_owner == "analyst"


class TestRulePackEntries:
    def test_all_entries_have_valid_blocking_level(self):
        valid_levels = {"editor_fixable", "analyst_blocking", "source_blocking",
                        "configuration_error", "rendering_error", "safety_blocking"}
        for ft, (level, owner, desc) in RULE_PACK.items():
            assert level in valid_levels, f"{ft}: invalid level {level}"

    def test_all_entries_have_valid_repair_owner(self):
        valid_owners = {"editor", "analyst", "source", "configuration", "rendering", "safety"}
        for ft, (level, owner, desc) in RULE_PACK.items():
            assert owner in valid_owners, f"{ft}: invalid owner {owner}"

    def test_all_entries_have_description(self):
        for ft, (level, owner, desc) in RULE_PACK.items():
            assert desc, f"{ft}: missing description"

    def test_rule_pack_has_at_least_30_entries(self):
        assert len(RULE_PACK) >= 30, f"Rule pack too small: {len(RULE_PACK)} entries"
