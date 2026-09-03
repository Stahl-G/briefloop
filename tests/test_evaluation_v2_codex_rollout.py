"""Audit-report measurement parsing plus the gate oracle, offline.

Every payload literal below was recorded from a real artifact (the source is
cited above each one): the measurement literals mirror the
``audit_report.json`` written by the auditor role in the two real codex
rollouts, and the oracle literals are recorded quality-gate reports.  The
module under test deliberately does NOT define ``build_codex_rollout``: the
invocation wiring lands with the Phase-2 rollout task, and one test pins
that the CLI seam therefore keeps failing closed.

Why two parsers: the gate evaluator never reads the agent's audit report,
and the auditor writes ONLY audit_report.json, so gate findings cannot
measure the agent.  Gates are the ground-truth oracle at corpus
construction; the agent's own report is the measurement source.
"""

from __future__ import annotations

import pytest

from multi_agent_brief.cli import eval_commands
from multi_agent_brief.cli.main import main
from multi_agent_brief.evaluation_v2 import codex_rollout
from multi_agent_brief.evaluation_v2.codex_rollout import (
    outcome_from_findings,
    parse_gate_findings_for_oracle,
    parse_reported_audit,
)
from multi_agent_brief.evaluation_v2.contracts import EvaluationCase, ReportedFinding


# ---------------------------------------------------------------------------
# Recorded payloads (inline, verbatim)
# ---------------------------------------------------------------------------


# Mirrors the audit_report.json recorded from the auditor role in the two
# real codex rollouts (P2-T0 smoke, 2026-09-03; raw payloads archived under
# the repo's ignored planning directory).  Contract:
# src/multi_agent_brief/contracts/schemas/audit_report.py -- finding_type
# is a FREE string (observed values include
# `unsupported_fact_missing_citation` and `target_scope_mismatch`, NOT the
# canonical vocabulary); anchors appear as related_claim_id or line_number;
# positions otherwise live only in prose, which is unusable.
REAL_AUDIT_REPORT = {
    "audit_status": "fail",
    "audit_score": 58,
    "findings": [
        {
            "finding_id": "AF-001",
            "severity": "high",
            "finding_type": "unsupported_fact_missing_citation",
            "description": "A quantitative claim carries no citation.",
            "recommendation": "Add the source reference or drop the number.",
            "related_claim_id": "CL-0003",
            "line_number": None,
            "evidence": "Section 2 states a 37% share with no source id.",
        },
        {
            "finding_id": "AF-002",
            "severity": "high",
            "finding_type": "stale_source",
            "description": "The claim cites a source dated outside the freshness window.",
            "related_claim_id": "CL-0007",
        },
        {
            "finding_id": "AF-003",
            "severity": "medium",
            "finding_type": "number_without_source",
            "description": "Line 14 of the audited brief quotes a figure with no source anchor.",
            "line_number": 14,
        },
        {
            "finding_id": "AF-004",
            "severity": "low",
            "finding_type": "target_scope_mismatch",
            "description": "The brief drifts from the configured target scope around line 30.",
        },
    ],
    "metadata": {
        "stage_id": "auditor",
        "brief": "output/intermediate/audited_brief.md",
    },
}

# The passing mirror: a clean auditor run records no findings.
REAL_CLEAN_AUDIT_REPORT = {
    "audit_status": "pass",
    "audit_score": 100,
    "findings": [],
    "metadata": {"stage_id": "auditor"},
}


# Recorded from:
#   examples/reference-workspaces/industry-weekly-demo/artifacts/quality_gate_report.json
# Full finding-record shape as produced by `_finding()` in
# src/multi_agent_brief/quality_gates/evaluation.py: note there is no
# `locator` field -- position lives in `line_number` plus the
# source/claim/artifact routing ids.
DEMO_REPORT = {
    "schema_version": "multi-agent-brief-quality-gates/v1",
    "created_at": "2026-06-14T09:08:00Z",
    "updated_at": "2026-06-14T09:08:00Z",
    "workspace": "industry-weekly-demo",
    "report_date": "2026-06-14",
    "policy_pack": "default",
    "status": "warning",
    "gate_results": [
        {
            "gate_id": "material_fact",
            "status": "pass",
            "blocking": False,
            "finding_ids": [],
        },
        {
            "gate_id": "freshness",
            "status": "pass",
            "blocking": False,
            "finding_ids": [],
        },
        {
            "gate_id": "target_relevance",
            "status": "pass",
            "blocking": False,
            "finding_ids": [],
        },
        {
            "gate_id": "coverage_omission",
            "status": "pass",
            "blocking": False,
            "finding_ids": [],
        },
        {
            "gate_id": "final_abstract_quality",
            "status": "warning",
            "blocking": False,
            "finding_ids": ["QG_FINAL_ABSTRACT_QUALITY_001"],
        },
    ],
    "findings": [
        {
            "finding_id": "QG_FINAL_ABSTRACT_QUALITY_001",
            "gate_id": "final_abstract_quality",
            "finding_type": "final_missing_comparison_basis",
            "category": "final_abstract_quality",
            "severity": "medium",
            "blocking": False,
            "blocking_level": "warning",
            "repair_owner": "none",
            "stage_id": "editor",
            "artifact_id": "audited_brief",
            "gate_stage_id": "auditor",
            "gate_artifact_id": "auditor_quality_gate_report",
            "repair_stage_id": None,
            "repair_artifact_id": None,
            "claim_id": None,
            "source_id": None,
            "line_number": 4,
            "description": (
                "The brief uses a comparative phrase about demand improving, "
                "but the comparison basis is limited to the sample fixture."
            ),
            "recommendation": (
                "Keep the narrow sample framing in the reader-facing text and "
                "state the comparison basis before broadening the signal."
            ),
            "rule_summary": (
                "Comparison framing should include an explicit basis, method, "
                "benchmark, or scope."
            ),
            "docs_anchor": "docs/agent-contract.md#final_abstract_quality",
            "summary": (
                "The brief uses a comparative phrase about demand improving, "
                "but the comparison basis is limited to the sample fixture."
            ),
            "evidence_ref": (
                "Public-safe demo sources point to a modest pickup in "
                "grid-equipment demand."
            ),
            "metadata": {
                "semantic_boundary": (
                    "warning_only_deterministic_pattern_surface; not a "
                    "prose-quality score, truth proof, release authority, "
                    "delivery approval, or repair authority"
                ),
                "repair_boundary": "advisory_non_routable",
                "authority_boundary": (
                    "deterministic_warning_only_no_repair_or_delivery_authority"
                ),
            },
        }
    ],
    "metadata": {
        "stage_id": "auditor",
        "gate_stage_id": "auditor",
        "gate_artifact_id": "auditor_quality_gate_report",
        "brief": "output/intermediate/audited_brief.md",
        "ledger": "output/intermediate/claim_ledger.json",
        "reference_fixture": True,
        "authority_boundary": (
            "deterministic_warning_only_no_repair_or_delivery_authority"
        ),
    },
}


# Recorded from:
#   src/multi_agent_brief/evaluation_cases/fixtures/cases/provenance_projection_minimal/workspace/output/intermediate/quality_gate_report.json
# Minimal finding shape: no position fields at all (no line_number, no
# source/claim/artifact id, no locator).  As an ORACLE input this record
# cannot be double-matched and must be rejected loudly: the staging that
# produced it is wrong and corpus construction must stop.
POSITIONLESS_REPORT = {
    "schema_version": "multi-agent-brief-quality-gates/v1",
    "created_at": "2026-06-09T00:00:00+00:00",
    "updated_at": "2026-06-09T00:00:00+00:00",
    "workspace": ".",
    "report_date": "2026-06-09",
    "policy_pack": "default",
    "status": "fail",
    "gate_results": [],
    "findings": [
        {
            "finding_id": "qg_SYN_PROV001",
            "finding_type": "target_relevance_gap",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "gate_stage_id": "auditor",
            "gate_artifact_id": "quality_gate_report",
        }
    ],
    "metadata": {},
}


# Recorded from:
#   src/multi_agent_brief/evaluation_cases/fixtures/cases/same_evidence_reader_quality_regression/workspace/output/intermediate/gates/auditor_quality_gate_report.json
# Stage-scoped report with an empty findings list: a clean run.
EMPTY_STAGE_SCOPED_REPORT = {
    "created_at": "2026-07-01T00:00:00+00:00",
    "findings": [],
    "gate_results": [
        {
            "blocking": False,
            "finding_ids": [],
            "gate_id": "coverage_omission",
            "status": "pass",
        },
        {
            "blocking": False,
            "finding_ids": [],
            "gate_id": "freshness",
            "status": "pass",
        },
        {
            "blocking": False,
            "finding_ids": [],
            "gate_id": "material_fact",
            "status": "pass",
        },
        {
            "blocking": False,
            "finding_ids": [],
            "gate_id": "target_relevance",
            "status": "pass",
        },
    ],
    "metadata": {
        "brief": "output/intermediate/audited_brief.md",
        "gate_stage_id": "auditor",
        "ledger": "output/intermediate/claim_ledger.json",
        "stage_id": "auditor",
    },
    "policy_pack": "default",
    "report_date": "",
    "schema_version": "multi-agent-brief-quality-gates/v1",
    "workspace": ".",
    "updated_at": "2026-07-01T00:00:00+00:00",
    "status": "pass",
}


# ---------------------------------------------------------------------------
# Case / finding helpers
# ---------------------------------------------------------------------------


def _defect(
    defect_id: str = "d1",
    finding_type: str = "stale_source",
    locator: str = "CL-0007",
    expected_blocking_level: str = "blocking",
) -> dict:
    return {
        "defect_id": defect_id,
        "finding_type": finding_type,
        "locator": locator,
        "expected_blocking_level": expected_blocking_level,
    }


def _case(case_id: str = "b1", defects=(), clean_claims=()) -> EvaluationCase:
    return EvaluationCase.model_validate(
        {
            "case_id": case_id,
            "synthetic": True,
            "source_pack": f"cases/{case_id}/sources",
            "report_date": "2026-06-08",
            "rollout": {"role": "auditor", "runtime": "codex"},
            "seeded_defects": list(defects),
            "clean_claims": list(clean_claims),
        },
        strict=True,
    )


def _audit_finding(
    finding_type: str = "stale_source",
    severity: str = "high",
    related_claim_id=None,
    line_number=...,
) -> dict:
    """An audit-report-shaped finding record per the agent contract."""
    finding = {
        "finding_id": "AF-TEST-001",
        "severity": severity,
        "finding_type": finding_type,
        "description": "test finding",
    }
    if related_claim_id is not None:
        finding["related_claim_id"] = related_claim_id
    if line_number is not ...:
        finding["line_number"] = line_number
    return finding


# ---------------------------------------------------------------------------
# Measurement: parse_reported_audit over the real payload shapes
# ---------------------------------------------------------------------------


def test_real_audit_report_parses_to_compliant_and_noncompliant():
    # Of the four findings recorded in the real report: AF-001 uses the
    # observed free type `unsupported_fact_missing_citation` -> noncompliant;
    # AF-002 is compliant on its related_claim_id anchor; AF-003 is
    # compliant on its line_number anchor; AF-004 uses the observed free
    # type `target_scope_mismatch` with the position only in prose ->
    # noncompliant.  Nothing raises.
    findings, noncompliant = parse_reported_audit(REAL_AUDIT_REPORT)
    assert [f.model_dump() for f in findings] == [
        {
            "finding_type": "stale_source",
            "locator": "CL-0007",
            "blocking_level": "blocking",  # severity high
        },
        {
            "finding_type": "number_without_source",
            "locator": "audited_brief#L14",
            "blocking_level": "warning",  # severity medium
        },
    ]
    assert noncompliant == 2


def test_real_audit_report_folds_onto_the_case_ground_truth():
    case = _case(
        "real",
        defects=[
            _defect("d1", finding_type="stale_source", locator="CL-0007"),
            # Seeded but missed: the free-typed finding above was never
            # matchable, so d2 is an honest miss, not a crash.
            _defect(
                "d2", finding_type="number_without_source", locator="CL-0009"
            ),
        ],
        clean_claims=["CL-0101"],
    )
    findings, noncompliant = parse_reported_audit(REAL_AUDIT_REPORT)
    # Recommended blocked derivation from the same payload: fail -> True.
    blocked = REAL_AUDIT_REPORT.get("audit_status") == "fail"
    outcome = outcome_from_findings(
        case, findings, blocked=blocked, noncompliant_finding_count=noncompliant
    )
    assert outcome.case_id == "real"
    assert outcome.found_defect_ids == ["d1"]
    assert outcome.flagged_claim_locators == []
    assert outcome.blocked is True
    assert outcome.noncompliant_finding_count == 2
    assert len(outcome.findings) == 2


def test_real_clean_audit_report_yields_empty_outcome():
    case = _case("clean", clean_claims=["CL-0101"])
    findings, noncompliant = parse_reported_audit(REAL_CLEAN_AUDIT_REPORT)
    assert findings == []
    assert noncompliant == 0
    outcome = outcome_from_findings(
        case,
        findings,
        blocked=REAL_CLEAN_AUDIT_REPORT.get("audit_status") == "fail",
        noncompliant_finding_count=noncompliant,
    )
    assert outcome.findings == []
    assert outcome.found_defect_ids == []
    assert outcome.blocked is False


def test_related_claim_id_anchor_wins_over_line_number():
    payload = {
        "audit_status": "fail",
        "audit_score": 50,
        "findings": [
            _audit_finding(
                finding_type="claim_support_matrix_blocking_support",
                severity="high",
                related_claim_id="CL-0042",
                line_number=17,
            )
        ],
    }
    findings, noncompliant = parse_reported_audit(payload)
    assert noncompliant == 0
    assert findings[0].locator == "CL-0042"


def test_line_number_anchor_targets_the_audited_brief():
    payload = {
        "audit_status": "warning",
        "audit_score": 80,
        "findings": [
            _audit_finding(
                finding_type="target_priority_claim_missing_from_summary",
                severity="low",
                line_number=23,
            )
        ],
    }
    findings, noncompliant = parse_reported_audit(payload)
    assert noncompliant == 0
    assert findings[0].locator == "audited_brief#L23"
    assert findings[0].blocking_level == "warning"  # severity low


def test_bad_findings_are_recorded_not_fatal():
    # The 120-run measurement loop must survive every one of these without
    # raising; each bad finding is only counted.
    no_severity = {
        "finding_id": "AF-TEST-001",
        "finding_type": "stale_source",  # legal type, legal anchor...
        "description": "severity omitted entirely",
        "related_claim_id": "CL-0001",
    }
    payload = {
        "audit_status": "fail",
        "audit_score": 10,
        "findings": [
            "not-an-object",  # structurally not a finding
            _audit_finding(finding_type="some_free_type"),  # outside vocabulary
            _audit_finding(),  # in vocabulary but no anchor at all
            _audit_finding(line_number=None),  # anchor explicitly null
            _audit_finding(line_number=0),  # non-positive line
            _audit_finding(line_number="14"),  # line as a string
            no_severity,  # no severity to map onto a blocking level
            _audit_finding(related_claim_id="CL-0001", severity="critical"),  # bad severity
            _audit_finding(related_claim_id="   "),  # blank anchor
        ],
    }
    findings, noncompliant = parse_reported_audit(payload)
    assert findings == []
    assert noncompliant == 9


def test_parse_audit_rejects_non_object_payload():
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_reported_audit(["not", "a", "report"])


def test_parse_audit_rejects_non_list_findings():
    with pytest.raises(ValueError, match="'findings' list"):
        parse_reported_audit({"audit_status": "fail", "findings": {}})


def test_parse_audit_treats_missing_findings_as_empty():
    findings, noncompliant = parse_reported_audit(
        {"audit_status": "pass", "audit_score": 100}
    )
    assert findings == []
    assert noncompliant == 0


# ---------------------------------------------------------------------------
# Detection semantics: double match, level-agnostic
# ---------------------------------------------------------------------------


def _rf(
    finding_type: str = "stale_source",
    locator: str = "CL-0007",
    blocking_level: str = "blocking",
) -> ReportedFinding:
    return ReportedFinding.model_validate(
        {
            "finding_type": finding_type,
            "locator": locator,
            "blocking_level": blocking_level,
        },
        strict=True,
    )


def test_detection_requires_both_finding_type_and_locator():
    case = _case(
        defects=[
            _defect("d1", finding_type="stale_source", locator="CL-0007")
        ]
    )
    right_type_wrong_locator = outcome_from_findings(
        case, [_rf(locator="CL-0099")], blocked=True
    )
    assert right_type_wrong_locator.found_defect_ids == []

    right_locator_wrong_type = outcome_from_findings(
        case, [_rf(finding_type="number_without_source")], blocked=True
    )
    assert right_locator_wrong_type.found_defect_ids == []


def test_level_disagreement_still_detects_and_keeps_reported_level():
    # Truth expects a warning; the rollout reports blocking.  Detection is
    # level-agnostic, so the defect is credited -- and the reported level is
    # preserved verbatim in the findings the scorer reads.
    case = _case(
        defects=[
            _defect(
                "d1",
                finding_type="stale_source",
                locator="CL-0007",
                expected_blocking_level="warning",
            )
        ]
    )
    outcome = outcome_from_findings(case, [_rf(blocking_level="blocking")], blocked=True)
    assert outcome.found_defect_ids == ["d1"]
    assert outcome.findings[0].blocking_level == "blocking"

    # Mirror direction: truth blocking, report warning.
    strict_case = _case(
        defects=[
            _defect(
                "d1",
                finding_type="stale_source",
                locator="CL-0007",
                expected_blocking_level="blocking",
            )
        ]
    )
    outcome = outcome_from_findings(strict_case, [_rf(blocking_level="warning")], blocked=False)
    assert outcome.found_defect_ids == ["d1"]
    assert outcome.findings[0].blocking_level == "warning"


def test_blocked_is_a_passthrough_recommended_derivation_is_audit_status():
    case = _case(defects=[_defect()])
    assert outcome_from_findings(case, [_rf()], blocked=False).blocked is False
    assert outcome_from_findings(case, [_rf()], blocked=True).blocked is True
    assert outcome_from_findings(case, [], blocked=True).blocked is True
    # The documented derivation: fail blocks, pass/warning do not.
    assert REAL_AUDIT_REPORT.get("audit_status") == "fail"
    assert REAL_CLEAN_AUDIT_REPORT.get("audit_status") == "pass"


def test_clean_claim_locator_is_flagged():
    case = _case(clean_claims=["CL-0101"])
    outcome = outcome_from_findings(
        case,
        [_rf(finding_type="number_without_source", locator="CL-0101")],
        blocked=True,
    )
    assert outcome.flagged_claim_locators == ["CL-0101"]
    assert outcome.found_defect_ids == []


def test_views_are_consistent_with_the_parsed_findings():
    # Recompute both views from outcome.findings (the record the scorer
    # reads) and the case ground truth; the adapter-provided views must
    # agree exactly, in case order.
    case = _case(
        defects=[
            _defect("d1", finding_type="stale_source", locator="CL-0007"),
            _defect(
                "d2",
                finding_type="number_without_source",
                locator="CL-0008",
            ),
        ],
        clean_claims=["CL-0101", "CL-0102"],
    )
    findings = [
        _rf(),  # d1
        _rf(finding_type="number_without_source", locator="CL-0101"),  # false flag
    ]
    outcome = outcome_from_findings(case, findings, blocked=True)

    pairs = {(f.finding_type, f.locator) for f in outcome.findings}
    locators = {f.locator for f in outcome.findings}
    assert outcome.found_defect_ids == [
        d.defect_id for d in case.seeded_defects if (d.finding_type, d.locator) in pairs
    ]
    assert outcome.flagged_claim_locators == [
        locator for locator in case.clean_claims if locator in locators
    ]
    assert len(outcome.findings) == len(findings)
    assert outcome.findings[0].finding_type == "stale_source"


# ---------------------------------------------------------------------------
# Oracle: parse_gate_findings_for_oracle (corpus construction only)
# ---------------------------------------------------------------------------


def test_oracle_accepts_the_recorded_demo_report():
    # The demo finding has line_number=4 and artifact_id="audited_brief"
    # (source_id/claim_id null), so the derived locator is
    # "audited_brief#L4".  Its type is a generation-family type: legitimate
    # gate vocabulary at construction time, outside the measurement
    # vocabulary -- hence plain (type, locator) pairs, which the
    # measurement-side ReportedFinding Literal could not even hold.
    findings = parse_gate_findings_for_oracle(DEMO_REPORT)
    assert findings == [("final_missing_comparison_basis", "audited_brief#L4")]
    # A seeded defect is gate-detectable iff its (type, locator) pair is
    # among the oracle findings.
    assert ("final_missing_comparison_basis", "audited_brief#L4") in findings
    assert ("stale_source", "CL-0007") not in findings


def test_oracle_clean_report_yields_no_findings():
    assert parse_gate_findings_for_oracle(EMPTY_STAGE_SCOPED_REPORT) == []


def test_oracle_rejects_positionless_findings_loudly():
    # The provenance_projection_minimal fixture records a finding with no
    # position at all: no double match is possible, so corpus construction
    # must stop rather than seed a defect that can never be verified.
    with pytest.raises(ValueError, match="carries no locator"):
        parse_gate_findings_for_oracle(POSITIONLESS_REPORT)


def test_oracle_rejects_unknown_vocabulary_loudly():
    # market_quote_metadata_incomplete is real gate vocabulary (freshness
    # gate) but outside both the detection and the generation families: the
    # staging produced a finding the corpus cannot reason about.
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "findings": [
            {
                "finding_id": "QG_TEST_001",
                "finding_type": "market_quote_metadata_incomplete",
                "blocking_level": "warning",
                "source_id": "SRC-003",
                "line_number": 12,
            }
        ],
    }
    with pytest.raises(ValueError, match="unknown finding_type"):
        parse_gate_findings_for_oracle(payload)


def test_oracle_rejects_unknown_blocking_level_loudly():
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "findings": [
            {
                "finding_id": "QG_TEST_001",
                "finding_type": "stale_source",
                "blocking_level": "none",
                "source_id": "SRC-003",
            }
        ],
    }
    with pytest.raises(ValueError, match="unknown blocking_level"):
        parse_gate_findings_for_oracle(payload)


def test_oracle_rejects_missing_finding_type_loudly():
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "findings": [
            {
                "finding_id": "QG_TEST_001",
                "blocking_level": "blocking",
                "source_id": "SRC-003",
            }
        ],
    }
    with pytest.raises(ValueError, match="missing required non-blank 'finding_type'"):
        parse_gate_findings_for_oracle(payload)


def test_oracle_locator_derivation_prefers_source_then_claim_then_artifact():
    # Freshness-style record: source_id + line_number.
    raw = {
        "finding_id": "QG_FRESHNESS_001",
        "gate_id": "freshness",
        "finding_type": "stale_source",
        "blocking_level": "blocking",
        "blocking": True,
        "source_id": "SRC-003",
        "claim_id": "CL-0007",
        "line_number": 12,
        "artifact_id": "claim_ledger",
    }
    findings = parse_gate_findings_for_oracle(
        {"schema_version": "multi-agent-brief-quality-gates/v1", "findings": [raw]}
    )
    assert findings == [("stale_source", "SRC-003#L12")]

    # Audit-style record: claim_id + line_number (source_id absent).
    audit_raw = {
        "finding_type": "number_without_source",
        "blocking_level": "blocking",
        "claim_id": "CL-0007",
        "line_number": 14,
        "artifact_id": "audited_brief",
    }
    findings = parse_gate_findings_for_oracle(
        {"schema_version": "multi-agent-brief-quality-gates/v1", "findings": [audit_raw]}
    )
    assert findings == [("number_without_source", "CL-0007#L14")]

    # Explicit locator wins over every derived anchor.
    explicit_raw = dict(audit_raw, locator="CL-0042")
    findings = parse_gate_findings_for_oracle(
        {"schema_version": "multi-agent-brief-quality-gates/v1", "findings": [explicit_raw]}
    )
    assert findings == [("number_without_source", "CL-0042")]


def test_oracle_rejects_non_object_payload():
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_gate_findings_for_oracle(["not", "a", "report"])


def test_oracle_rejects_wrong_schema_version():
    with pytest.raises(ValueError, match="schema_version"):
        parse_gate_findings_for_oracle(
            {"schema_version": "someone/elses/v9", "findings": []}
        )


def test_oracle_rejects_missing_findings_list():
    with pytest.raises(ValueError, match="'findings' list"):
        parse_gate_findings_for_oracle(
            {"schema_version": "multi-agent-brief-quality-gates/v1"}
        )


def test_oracle_rejects_non_object_finding():
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "findings": ["not-an-object"],
    }
    with pytest.raises(ValueError, match=r"finding\[0\] must be an object"):
        parse_gate_findings_for_oracle(payload)


# ---------------------------------------------------------------------------
# The CLI seam stays closed until the Phase-2 adapter lands
# ---------------------------------------------------------------------------


def test_module_does_not_satisfy_the_cli_seam(capsys):
    # This module exists now, so the guard has teeth only if it still does
    # NOT export build_codex_rollout: the seam must keep failing closed
    # with the no-adapter message exactly as before.
    assert not hasattr(codex_rollout, "build_codex_rollout")

    with pytest.raises(eval_commands.RolloutAdapterUnavailable):
        eval_commands._build_rollout()

    assert main(["eval", "run", "--split", "val"]) == 1
    captured = capsys.readouterr()
    assert "no rollout adapter is available yet" in captured.err
    assert "codex adapter lands with the rollout task" in captured.err
    assert captured.out == ""
