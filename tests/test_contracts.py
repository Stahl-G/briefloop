"""Tests for Contracts Package — schema registry, validation, and migration."""

from __future__ import annotations

import pytest

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import ContractError, FieldViolation
from multi_agent_brief.contracts.schemas.source_item import SourceItemContract
from multi_agent_brief.contracts.schemas.candidate_item import CandidateItemContract
from multi_agent_brief.contracts.schemas.atomic_claim_graph import AtomicClaimGraphContract
from multi_agent_brief.contracts.schemas.claim_draft import ClaimDraftContract, claim_draft_diagnostics
from multi_agent_brief.contracts.schemas.claim import ClaimContract
from multi_agent_brief.contracts.schemas.audit_report import AuditReportContract
from multi_agent_brief.contracts.schemas.analysis_pack import (
    MarketEventContract,
    AnalysisCardContract,
)
from multi_agent_brief.contracts.migrations.claim_v1_to_v2 import migrate_claim_v1_to_v2


# ── SchemaRegistry ──


class TestSchemaRegistry:
    def test_register_and_get(self):
        assert SchemaRegistry.get("source_item") is SourceItemContract
        assert SchemaRegistry.get("claim") is ClaimContract
        assert SchemaRegistry.get("claim_drafts") is ClaimDraftContract
        assert SchemaRegistry.get("atomic_claim_graph") is AtomicClaimGraphContract

    def test_get_unknown_returns_none(self):
        assert SchemaRegistry.get("nonexistent") is None

    def test_validate_unknown_schema_raises(self):
        with pytest.raises(KeyError, match="Unknown schema"):
            SchemaRegistry.validate("nonexistent", {})


# ── SourceItemContract ──


class TestSourceItemContract:
    def test_valid_source_passes(self):
        data = {
            "source_id": "S1", "source_name": "Test",
            "source_type": "local_file", "title": "T", "content": "C",
        }
        assert SourceItemContract.is_valid(data)
        assert SourceItemContract.validate(data) == []

    def test_missing_required_field_fails(self):
        data = {"source_id": "S1"}  # missing source_name, source_type, title, content
        violations = SourceItemContract.validate(data)
        error_fields = [v.field for v in violations if v.severity == "error"]
        assert "source_name" in error_fields
        assert "source_type" in error_fields

    def test_unknown_field_warning(self):
        data = {
            "source_id": "S1", "source_name": "Test",
            "source_type": "local_file", "title": "T", "content": "C",
            "custom_field": "value",
        }
        violations = SourceItemContract.validate(data)
        warnings = [v for v in violations if v.severity == "warning"]
        assert any(v.field == "custom_field" for v in warnings)


# ── CandidateItemContract ──


class TestCandidateItemContract:
    def test_valid_candidate_passes(self):
        data = {"item_id": "C1", "title": "T", "summary": "S", "source_id": "S1"}
        assert CandidateItemContract.is_valid(data)

    def test_empty_title_fails(self):
        data = {"item_id": "C1", "title": "", "summary": "S", "source_id": "S1"}
        violations = CandidateItemContract.validate(data)
        assert any(v.field == "title" for v in violations)


# ── ClaimContract ──


class TestClaimContract:
    def test_v1_claim_passes(self):
        data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "fact",
        }
        assert ClaimContract.is_valid(data)

    def test_v2_claim_passes(self):
        data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "fact",
            "schema_version": "v2", "epistemic_type": "observed",
            "evidence_relation": "direct",
        }
        assert ClaimContract.is_valid(data)

    def test_invalid_claim_type_fails(self):
        data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "invalid_type",
        }
        violations = ClaimContract.validate(data)
        assert any(v.field == "claim_type" for v in violations)

    def test_invalid_confidence_fails(self):
        data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "confidence": "certain",
        }
        violations = ClaimContract.validate(data)
        assert any(v.field == "confidence" for v in violations)


# ── AtomicClaimGraphContract ──


def _valid_atomic_claim_graph() -> dict:
    return {
        "schema_version": "mabw.atomic_claim_graph.v1",
        "claims": [
            {
                "claim_id": "CL-0001",
                "statement": "ExampleCo opened a demo facility.",
                "atoms": [
                    {
                        "atom_id": "AC-0001-01",
                        "text": "ExampleCo opened a demo facility.",
                        "claim_role": "observed_fact",
                        "materiality": "high",
                    },
                    {
                        "atom_id": "AC-0001-02",
                        "text": "The facility is a demo facility.",
                        "claim_role": "background_context",
                        "materiality": "medium",
                    },
                ],
                "edges": [
                    {
                        "from": "AC-0001-01",
                        "to": "AC-0001-02",
                        "relation": "qualifies_context",
                    }
                ],
            }
        ],
        "metadata": {},
    }


class TestAtomicClaimGraphContract:
    def test_valid_minimal_graph_passes(self):
        assert AtomicClaimGraphContract.validate(_valid_atomic_claim_graph()) == []
        assert AtomicClaimGraphContract.is_valid(_valid_atomic_claim_graph())

    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ([], "<root>"),
            ({"schema_version": "wrong", "claims": []}, "schema_version"),
            ({"schema_version": "mabw.atomic_claim_graph.v1", "claims": []}, "claims"),
            ({"schema_version": "mabw.atomic_claim_graph.v1"}, "claims"),
        ],
    )
    def test_rejects_invalid_root_version_or_empty_claims(self, payload, field):
        violations = AtomicClaimGraphContract.validate(payload)

        assert any(violation.field == field for violation in violations)
        assert not AtomicClaimGraphContract.is_valid(payload)

    def test_rejects_duplicate_atom_id(self):
        graph = _valid_atomic_claim_graph()
        graph["claims"][0]["atoms"][1]["atom_id"] = "AC-0001-01"

        violations = AtomicClaimGraphContract.validate(graph)

        assert any(violation.field == "claims[0].atoms[1].atom_id" for violation in violations)

    def test_rejects_invalid_atom_id(self):
        graph = _valid_atomic_claim_graph()
        graph["claims"][0]["atoms"][0]["atom_id"] = "ATOM-1"

        violations = AtomicClaimGraphContract.validate(graph)

        assert any(violation.field == "claims[0].atoms[0].atom_id" for violation in violations)

    def test_rejects_canonical_claim_atom_prefix_mismatch(self):
        graph = _valid_atomic_claim_graph()
        graph["claims"][0]["atoms"][0]["atom_id"] = "AC-0002-01"

        violations = AtomicClaimGraphContract.validate(graph)

        assert any(violation.field == "claims[0].atoms[0].atom_id" for violation in violations)

    def test_allows_non_canonical_claim_id_without_prefix_rejection(self):
        graph = _valid_atomic_claim_graph()
        graph["claims"][0]["claim_id"] = "CLAIM-LOCAL-A"

        assert AtomicClaimGraphContract.validate(graph) == []

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("text", ""),
            ("claim_role", "unsupported_role"),
            ("materiality", "critical"),
        ],
    )
    def test_rejects_invalid_atom_fields(self, field, value):
        graph = _valid_atomic_claim_graph()
        graph["claims"][0]["atoms"][0][field] = value

        violations = AtomicClaimGraphContract.validate(graph)

        assert any(violation.field == f"claims[0].atoms[0].{field}" for violation in violations)

    def test_rejects_missing_atom_required_fields(self):
        graph = _valid_atomic_claim_graph()
        graph["claims"][0]["atoms"][0] = {"atom_id": "AC-0001-01"}

        violations = AtomicClaimGraphContract.validate(graph)
        fields = {violation.field for violation in violations}

        assert {
            "claims[0].atoms[0].text",
            "claims[0].atoms[0].claim_role",
            "claims[0].atoms[0].materiality",
        } <= fields

    @pytest.mark.parametrize(
        ("edge", "field"),
        [
            ({"to": "AC-0001-02", "relation": "r"}, "from"),
            ({"from": "AC-0001-01", "relation": "r"}, "to"),
            ({"from": "AC-0001-01", "to": "AC-0001-02", "relation": ""}, "relation"),
            ({"from": "AC-9999-01", "to": "AC-0001-02", "relation": "r"}, "from"),
        ],
    )
    def test_rejects_invalid_edges(self, edge, field):
        graph = _valid_atomic_claim_graph()
        graph["claims"][0]["edges"] = [edge]

        violations = AtomicClaimGraphContract.validate(graph)

        assert any(violation.field == f"claims[0].edges[0].{field}" for violation in violations)

    def test_rejects_cross_claim_edge_reference(self):
        graph = _valid_atomic_claim_graph()
        graph["claims"].append(
            {
                "claim_id": "CL-0002",
                "atoms": [
                    {
                        "atom_id": "AC-0002-01",
                        "text": "BetaCo expanded output.",
                        "claim_role": "observed_fact",
                        "materiality": "medium",
                    }
                ],
            }
        )
        graph["claims"][0]["edges"] = [
            {
                "from": "AC-0001-01",
                "to": "AC-0002-01",
                "relation": "cross_claim_reference",
            }
        ]

        violations = AtomicClaimGraphContract.validate(graph)

        assert any(violation.field == "claims[0].edges[0].to" for violation in violations)

    @pytest.mark.parametrize(
        ("path", "value", "field"),
        [
            (("metadata",), [], "metadata"),
            (("claims", 0, "metadata"), [], "claims[0].metadata"),
            (("claims", 0, "statement"), "", "claims[0].statement"),
        ],
    )
    def test_rejects_invalid_metadata_or_statement(self, path, value, field):
        graph = _valid_atomic_claim_graph()
        target = graph
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

        violations = AtomicClaimGraphContract.validate(graph)

        assert any(violation.field == field for violation in violations)


# ── ClaimDraftContract ──


class TestClaimDraftContract:
    def test_valid_claim_drafts_pass(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "claim_type": "fact",
                    "confidence": "medium",
                    "published_at": "2026-06-01",
                    "retrieved_at": "2026-06-16T00:00:00Z",
                    "source_path": "input/sources/source-001.md",
                    "source_title": "Example Source",
                    "source_name": "Example Wire",
                    "publisher": "Example Publisher",
                    "topic": "demo market",
                }
            ],
            "metadata": {"created_by": "test"},
        }

        assert ClaimDraftContract.is_valid(data)
        assert ClaimDraftContract.validate(data) == []

    def test_claim_drafts_reject_claim_id(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "claim_id": "CL-001",
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                }
            ],
        }

        violations = ClaimDraftContract.validate(data)

        assert any(v.field == "drafts[0].claim_id" for v in violations)
        assert not ClaimDraftContract.is_valid(data)

    def test_claim_drafts_reject_claim_id_in_metadata(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "metadata": {"claim_id": "CL-001"},
                }
            ],
        }

        violations = ClaimDraftContract.validate(data)

        assert any(v.field == "drafts[0].metadata.claim_id" for v in violations)
        assert not ClaimDraftContract.is_valid(data)

    def test_claim_drafts_reject_non_string_required_fields(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "statement": 123,
                    "source_id": ["SRC-001"],
                    "evidence_text": {"text": "Example evidence."},
                }
            ],
        }

        violations = ClaimDraftContract.validate(data)

        error_fields = {violation.field for violation in violations if violation.severity == "error"}
        assert {
            "drafts[0].statement",
            "drafts[0].source_id",
            "drafts[0].evidence_text",
        } <= error_fields
        assert not ClaimDraftContract.is_valid(data)

    def test_claim_drafts_reject_optional_field_type_mismatches(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "requires_audit": "yes",
                    "used_in_sections": ["summary", 3],
                    "limitations": "none",
                    "metadata": [],
                    "published_at": 20260601,
                    "retrieved_at": ["2026-06-16"],
                    "source_path": {"path": "source.md"},
                    "topic": 42,
                }
            ],
        }

        violations = ClaimDraftContract.validate(data)

        error_fields = {violation.field for violation in violations if violation.severity == "error"}
        assert {
            "drafts[0].requires_audit",
            "drafts[0].used_in_sections[1]",
            "drafts[0].limitations",
            "drafts[0].metadata",
            "drafts[0].published_at",
            "drafts[0].retrieved_at",
            "drafts[0].source_path",
            "drafts[0].topic",
        } <= error_fields

    def test_claim_draft_diagnostics_include_allowed_and_forbidden_values(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "claim_id": "CL-001",
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "claim_type": "unsupported",
                    "confidence": "certain",
                    "epistemic_type": "guess",
                    "evidence_relation": "maybe",
                }
            ],
        }

        diagnostics = claim_draft_diagnostics(
            [violation for violation in ClaimDraftContract.validate(data) if violation.severity == "error"]
        )

        by_field = {item["field"]: item for item in diagnostics}
        assert by_field["drafts[0].claim_id"]["forbidden_fields"] == ["claim_id"]
        assert "Python assigns CL-####" in by_field["drafts[0].claim_id"]["hint"]
        assert by_field["drafts[0].claim_type"]["allowed_values"] == [
            "date",
            "fact",
            "forecast",
            "interpretation",
            "number",
            "risk",
        ]
        assert by_field["drafts[0].confidence"]["allowed_values"] == ["high", "low", "medium"]
        assert by_field["drafts[0].epistemic_type"]["allowed_values"] == [
            "action",
            "analogy",
            "hypothesis",
            "interpreted",
            "observed",
        ]
        assert by_field["drafts[0].evidence_relation"]["allowed_values"] == [
            "analogous",
            "direct",
            "indirect",
            "inferred",
        ]

    def test_claim_drafts_do_not_semantically_dedupe(self):
        draft = {
            "statement": "ExampleCo opened a demo facility.",
            "source_id": "SRC-001",
            "evidence_text": "Example evidence.",
        }
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [dict(draft), dict(draft)],
        }

        assert ClaimDraftContract.validate(data) == []
        assert ClaimDraftContract.is_valid(data)


# ── AuditReportContract ──


class TestAuditReportContract:
    def test_valid_report_passes(self):
        data = {"audit_status": "pass", "audit_score": 100}
        assert AuditReportContract.is_valid(data)

    def test_invalid_status_fails(self):
        data = {"audit_status": "maybe", "audit_score": 50}
        violations = AuditReportContract.validate(data)
        assert any(v.field == "audit_status" for v in violations)

    def test_score_out_of_range_fails(self):
        data = {"audit_status": "pass", "audit_score": 150}
        violations = AuditReportContract.validate(data)
        assert any(v.field == "audit_score" for v in violations)


# ── AnalysisPack Contracts ──


class TestMarketEventContract:
    def test_valid_market_event_passes(self):
        data = {"event_id": "E1", "entity_ids": ["ENT1"], "event_type": "product_launch"}
        assert MarketEventContract.is_valid(data)

    def test_invalid_event_type_fails(self):
        data = {"event_id": "E1", "entity_ids": ["ENT1"], "event_type": "invalid"}
        violations = MarketEventContract.validate(data)
        assert any(v.field == "event_type" for v in violations)


class TestAnalysisCardContract:
    def test_valid_analysis_card_passes(self):
        data = {
            "analysis_id": "A1", "finding_type": "risk",
            "headline": "H", "observation": "O",
        }
        assert AnalysisCardContract.is_valid(data)

    def test_invalid_finding_type_fails(self):
        data = {
            "analysis_id": "A1", "finding_type": "invalid",
            "headline": "H", "observation": "O",
        }
        violations = AnalysisCardContract.validate(data)
        assert any(v.field == "finding_type" for v in violations)


# ── Claim Migration ──


class TestClaimMigration:
    def test_v1_to_v2_migration_produces_valid_v2(self):
        v1_data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "forecast",
        }
        v2_data = migrate_claim_v1_to_v2(v1_data)
        assert v2_data["schema_version"] == "v2"
        assert v2_data["epistemic_type"] == "hypothesis"
        violations = ClaimContract.validate(v2_data)
        assert not any(v.severity == "error" for v in violations)

    def test_already_v2_no_migration(self):
        v2_data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "fact",
            "schema_version": "v2", "epistemic_type": "observed",
        }
        result = ClaimContract.migrate(v2_data, "v2")
        assert result["epistemic_type"] == "observed"

    def test_unknown_version_passes_through(self):
        data = {"claim_id": "X", "statement": "s", "source_id": "S", "evidence_text": "e"}
        result = ClaimContract.migrate(data, "v99")
        assert result == data


# ── ContractError ──


class TestContractErrors:
    def test_contract_error_contains_violations(self):
        violations = [
            FieldViolation(field="x", error="missing"),
            FieldViolation(field="y", error="invalid", severity="warning"),
        ]
        err = ContractError(violations=violations, schema_id="test", schema_version="v1")
        assert err.error_count == 1
        assert err.warning_count == 1
        assert "2 violation" in str(err)

    def test_field_violation_str_representation(self):
        v = FieldViolation(field="claim_type", error="invalid value", severity="error")
        s = str(v)
        assert "[error]" in s
        assert "claim_type" in s
        assert "invalid value" in s


class TestAuditReportContractConsistency:
    """Verify that all audit agents produce AuditReport data that passes the contract."""

    def test_noop_semantic_passes_contract(self):
        from multi_agent_brief.audit.semantic import NoOpSemanticAuditAgent
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import Claim

        ledger = ClaimLedger([Claim("X", "s", "S", "e")])
        report = NoOpSemanticAuditAgent().run_audit("draft", ledger)
        violations = AuditReportContract.validate(report.to_dict())
        errors = [v for v in violations if v.severity == "error"]
        assert errors == [], f"NoOp violates AuditReport contract: {errors}"

    def test_deterministic_passes_contract(self):
        from multi_agent_brief.audit.deterministic import DeterministicAuditAgent
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import Claim

        ledger = ClaimLedger([Claim("X", "s", "S", "e")])
        report = DeterministicAuditAgent().run_audit("- s [src:X]", ledger)
        violations = AuditReportContract.validate(report.to_dict())
        errors = [v for v in violations if v.severity == "error"]
        assert errors == [], f"Deterministic audit violates contract: {errors}"
