"""Tests for Claim Schema v2 — epistemic types, migration, and auditor gates."""

from __future__ import annotations

from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.audit.deterministic import run_deterministic_audit


def _claim(**overrides) -> Claim:
    defaults = dict(
        claim_id="TEST_001",
        statement="Test statement",
        source_id="SRC1",
        evidence_text="Evidence text",
        claim_type="fact",
        confidence="medium",
        epistemic_type="observed",
        evidence_relation="direct",
        applicability_reason="",
        limitations=[],
    )
    defaults.update(overrides)
    return Claim(**defaults)


# ── New Fields ──


class TestNewFields:
    def test_epistemic_type_defaults_to_observed(self):
        c = _claim()
        assert c.epistemic_type == "observed"

    def test_v2_claim_serializes_new_fields(self):
        c = _claim(
            schema_version="v2",
            epistemic_type="hypothesis",
            evidence_relation="inferred",
            applicability_reason="Based on similar market patterns",
            limitations=["Different regulatory environment"],
        )
        d = c.to_dict()
        assert d["schema_version"] == "v2"
        assert d["epistemic_type"] == "hypothesis"
        assert d["evidence_relation"] == "inferred"
        assert d["applicability_reason"] == "Based on similar market patterns"
        assert d["limitations"] == ["Different regulatory environment"]

    def test_v2_claim_from_dict_roundtrip(self):
        c = _claim(
            schema_version="v2",
            epistemic_type="analogy",
            evidence_relation="analogous",
            applicability_reason="Similar market conditions",
            limitations=["Different timeframe"],
        )
        d = c.to_dict()
        c2 = Claim.from_dict(d)
        assert c2.schema_version == "v2"
        assert c2.epistemic_type == "analogy"
        assert c2.evidence_relation == "analogous"
        assert c2.limitations == ["Different timeframe"]


# ── Migration ──


class TestMigration:
    def test_v1_fact_migrates_to_observed(self):
        c = Claim.from_dict({
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "fact",
        })
        assert c.epistemic_type == "observed"

    def test_v1_interpretation_migrates_to_interpreted(self):
        c = Claim.from_dict({
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "interpretation",
        })
        assert c.epistemic_type == "interpreted"

    def test_v1_forecast_migrates_to_hypothesis(self):
        c = Claim.from_dict({
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "forecast",
        })
        assert c.epistemic_type == "hypothesis"

    def test_v1_risk_migrates_to_hypothesis(self):
        c = Claim.from_dict({
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "risk",
        })
        assert c.epistemic_type == "hypothesis"


# ── Backward Compatibility ──


class TestBackwardCompat:
    def test_old_ledger_json_loads_without_error(self):
        old_data = [
            {
                "claim_id": "OLD_001",
                "statement": "Revenue grew 12%",
                "source_id": "SEC10K",
                "evidence_text": "Revenue increased from $1B to $1.12B",
                "claim_type": "fact",
                "confidence": "high",
                "source_type": "web_search",
            }
        ]
        ledger = ClaimLedger([Claim.from_dict(item) for item in old_data])
        assert len(ledger) == 1
        claim = list(ledger)[0]
        assert claim.epistemic_type == "observed"
        assert claim.schema_version == "v1"

    def test_from_dict_handles_missing_epistemic_fields(self):
        c = Claim.from_dict({
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e",
        })
        assert c.epistemic_type == "observed"
        assert c.evidence_relation == "direct"
        assert c.applicability_reason == ""
        assert c.limitations == []

    def test_to_dict_includes_all_fields_for_v1(self):
        c = _claim(schema_version="v1")
        d = c.to_dict()
        assert "schema_version" in d
        assert "epistemic_type" in d
        assert "evidence_relation" in d
        assert "applicability_reason" in d
        assert "limitations" in d


# ── Epistemic Auditor Gates ──


class TestEpistemicAuditor:
    def _audit(self, claims):
        ledger = ClaimLedger(claims)
        return run_deterministic_audit("- Statement [src:TEST_001]", ledger)

    def test_hypothesis_high_confidence_fails(self):
        report = self._audit([
            _claim(
                epistemic_type="hypothesis",
                confidence="high",
                applicability_reason="test",
                limitations=["test lim"],
            )
        ])
        types = [f.finding_type for f in report.findings]
        assert "hypothesis_high_confidence" in types

    def test_action_without_applicability_fails(self):
        report = self._audit([
            _claim(epistemic_type="action", applicability_reason="")
        ])
        types = [f.finding_type for f in report.findings]
        assert "action_without_basis" in types

    def test_analogy_without_limitations_warns(self):
        report = self._audit([
            _claim(
                epistemic_type="analogy",
                evidence_relation="indirect",
                limitations=[],
            )
        ])
        types = [f.finding_type for f in report.findings]
        assert "analogy_without_limitations" in types

    def test_analogy_with_direct_relation_fails(self):
        report = self._audit([
            _claim(
                epistemic_type="analogy",
                evidence_relation="direct",
                limitations=["some lim"],
            )
        ])
        types = [f.finding_type for f in report.findings]
        assert "analogy_direct_relation" in types

    def test_observed_with_high_confidence_passes(self):
        report = self._audit([
            _claim(epistemic_type="observed", confidence="high")
        ])
        epistemic_findings = [
            f for f in report.findings
            if f.finding_type.startswith("hypothesis_")
            or f.finding_type.startswith("action_")
            or f.finding_type.startswith("analogy_")
        ]
        assert len(epistemic_findings) == 0


# ── Edge Cases ──


class TestEdgeCases:
    def test_empty_ledger_migrates_cleanly(self):
        ledger = ClaimLedger()
        assert len(ledger) == 0

    def test_claim_type_backward_compat_preserved(self):
        # When claim_type="forecast" but epistemic_type is already set,
        # migration does not overwrite it
        c = _claim(claim_type="forecast")
        assert c.claim_type == "forecast"
        d = c.to_dict()
        assert d["claim_type"] == "forecast"
        c2 = Claim.from_dict(d)
        assert c2.claim_type == "forecast"
        assert c2.epistemic_type == "observed"  # already set by default

    def test_old_v1_dict_without_epistemic_migrates(self):
        # Simulates loading a real v1 claim_ledger.json that predates v2
        old_data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "forecast",
            # no epistemic_type key at all
        }
        c = Claim.from_dict(old_data)
        assert c.claim_type == "forecast"
        assert c.epistemic_type == "hypothesis"


class TestNewClaimDefaults:
    def test_new_claim_defaults_to_v2(self):
        c = Claim(
            claim_id="X", statement="s", source_id="S",
            evidence_text="e", claim_type="forecast",
        )
        assert c.schema_version == "v2"
        # epistemic_type defaults to "observed" — inference happens in Scout
        assert c.epistemic_type == "observed"

    def test_scout_infers_epistemic_from_claim_type(self):
        """Scout's _infer_epistemic maps claim_type → epistemic_type."""
        from multi_agent_brief.agents.scout import _infer_epistemic
        assert _infer_epistemic("forecast") == "hypothesis"
        assert _infer_epistemic("risk") == "hypothesis"
        assert _infer_epistemic("interpretation") == "interpreted"
        assert _infer_epistemic("fact") == "observed"
        assert _infer_epistemic("number") == "observed"

    def test_all_claim_types_default_to_v2(self):
        for ct in ("fact", "number", "date", "interpretation", "forecast", "risk"):
            c = Claim(
                claim_id="X", statement="s", source_id="S",
                evidence_text="e", claim_type=ct,
            )
            assert c.schema_version == "v2", f"claim_type={ct} should default to v2"

    def test_old_v1_loaded_keeps_v1(self):
        c = Claim.from_dict({
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "fact",
            # no schema_version → migration sets v1
        })
        assert c.schema_version == "v1"
