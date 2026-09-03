"""Findings-to-outcome mapping for the Codex rollout adapter, offline.

Every payload literal below was recorded from a real file in this repo (the
path is cited above each one); the mapping is tested against reality, not
against a shape invented for the test.  The module under test deliberately
does NOT define ``build_codex_rollout``: the invocation wiring lands with
the Phase-2 rollout task, and one test pins that the CLI seam therefore
keeps failing closed.
"""

from __future__ import annotations

import pytest

from multi_agent_brief.cli import eval_commands
from multi_agent_brief.cli.main import main
from multi_agent_brief.evaluation_v2 import codex_rollout
from multi_agent_brief.evaluation_v2.codex_rollout import (
    outcome_from_findings,
    parse_reported_findings,
)
from multi_agent_brief.evaluation_v2.contracts import EvaluationCase


# ---------------------------------------------------------------------------
# Recorded payloads (inline, verbatim)
# ---------------------------------------------------------------------------


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
# source/claim/artifact id, no locator).  As an evaluation input this record
# cannot be double-matched and must be rejected loudly, not silently scored.
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
# Case / raw-finding helpers
# ---------------------------------------------------------------------------


def _defect(
    defect_id: str = "d1",
    finding_type: str = "stale_source",
    locator: str = "source-002.md#L14",
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


def _raw_finding(
    finding_type: str = "stale_source",
    locator: str = "source-002.md#L14",
    blocking_level: str = "blocking",
) -> dict:
    """A gate-report-shaped record carrying an explicit evaluation locator.

    Extra keys are not forbidden by the gate-report validator, and the
    Phase-2 rollout prompt can require the role to record ``locator``
    explicitly; these are the records such a rollout hands over.
    """
    return {
        "finding_id": "QG_TEST_001",
        "gate_id": "freshness",
        "finding_type": finding_type,
        "category": "stale_source",
        "severity": "high",
        "blocking": blocking_level == "blocking",
        "blocking_level": blocking_level,
        "claim_id": None,
        "source_id": None,
        "line_number": None,
        "locator": locator,
    }


# ---------------------------------------------------------------------------
# Round-trips with recorded payloads
# ---------------------------------------------------------------------------


def test_recorded_demo_report_round_trips():
    # The demo finding has line_number=4 and artifact_id="audited_brief"
    # (source_id/claim_id null), so the derived locator is
    # "audited_brief#L4"; the case's ground truth is annotated with exactly
    # that locator.
    case = _case(
        "demo",
        defects=[
            _defect(
                "d-demo",
                finding_type="final_missing_comparison_basis",
                locator="audited_brief#L4",
                expected_blocking_level="warning",
            )
        ],
        clean_claims=["audited_brief#L7"],
    )
    raw = parse_reported_findings(DEMO_REPORT)
    assert len(raw) == 1

    outcome = outcome_from_findings(case, raw, blocked=False)

    assert outcome.case_id == "demo"
    assert outcome.found_defect_ids == ["d-demo"]
    assert outcome.flagged_claim_locators == []
    assert outcome.blocked is False
    assert [f.model_dump() for f in outcome.findings] == [
        {
            "finding_type": "final_missing_comparison_basis",
            "locator": "audited_brief#L4",
            "blocking_level": "warning",
        }
    ]


def test_recorded_stage_scoped_clean_report_yields_empty_outcome():
    case = _case("clean", clean_claims=["source-001.md#L8"])
    outcome = outcome_from_findings(
        case, parse_reported_findings(EMPTY_STAGE_SCOPED_REPORT), blocked=False
    )
    assert outcome.case_id == "clean"
    assert outcome.findings == []
    assert outcome.found_defect_ids == []
    assert outcome.flagged_claim_locators == []
    assert outcome.blocked is False


def test_recorded_positionless_finding_is_rejected_loudly():
    # The provenance_projection_minimal fixture records a finding with no
    # position at all: no double match is possible, so it must raise rather
    # than silently read as a miss.
    raw = parse_reported_findings(POSITIONLESS_REPORT)
    with pytest.raises(ValueError, match="carries no locator"):
        outcome_from_findings(_case("prov"), raw, blocked=True)


# ---------------------------------------------------------------------------
# Detection semantics: double match, level-agnostic
# ---------------------------------------------------------------------------


def test_detection_requires_both_finding_type_and_locator():
    case = _case(
        defects=[
            _defect("d1", finding_type="stale_source", locator="source-002.md#L14")
        ]
    )
    right_type_wrong_locator = _raw_finding(locator="source-009.md#L99")
    outcome = outcome_from_findings(case, [right_type_wrong_locator], blocked=True)
    assert outcome.found_defect_ids == []

    right_locator_wrong_type = _raw_finding(
        finding_type="target_relevance_gap", locator="source-002.md#L14"
    )
    outcome = outcome_from_findings(case, [right_locator_wrong_type], blocked=True)
    assert outcome.found_defect_ids == []


def test_level_disagreement_still_detects_and_keeps_reported_level():
    # Truth expects a warning; the rollout reports blocking.  Detection is
    # level-agnostic, so the defect is credited -- and the reported level is
    # preserved verbatim in the findings the scorer reads.
    case = _case(
        defects=[
            _defect(
                "d1",
                finding_type="stale_source",
                locator="source-002.md#L14",
                expected_blocking_level="warning",
            )
        ]
    )
    outcome = outcome_from_findings(
        case, [_raw_finding(blocking_level="blocking")], blocked=True
    )
    assert outcome.found_defect_ids == ["d1"]
    assert outcome.findings[0].blocking_level == "blocking"

    # Mirror direction: truth blocking, report warning.
    strict_case = _case(
        defects=[
            _defect(
                "d1",
                finding_type="stale_source",
                locator="source-002.md#L14",
                expected_blocking_level="blocking",
            )
        ]
    )
    outcome = outcome_from_findings(
        strict_case, [_raw_finding(blocking_level="warning")], blocked=False
    )
    assert outcome.found_defect_ids == ["d1"]
    assert outcome.findings[0].blocking_level == "warning"


def test_blocked_comes_from_the_argument_not_the_findings():
    case = _case(defects=[_defect()])
    blocking_findings = [_raw_finding(blocking_level="blocking")]
    assert (
        outcome_from_findings(case, blocking_findings, blocked=False).blocked is False
    )
    assert outcome_from_findings(case, blocking_findings, blocked=True).blocked is True
    assert outcome_from_findings(case, [], blocked=True).blocked is True


def test_clean_claim_locator_is_flagged():
    case = _case(clean_claims=["source-001.md#L8"])
    outcome = outcome_from_findings(
        case,
        [
            _raw_finding(
                finding_type="number_without_source", locator="source-001.md#L8"
            )
        ],
        blocked=True,
    )
    assert outcome.flagged_claim_locators == ["source-001.md#L8"]
    assert outcome.found_defect_ids == []


def test_views_are_consistent_with_the_recorded_findings():
    # Recompute both views from outcome.findings (the record the scorer
    # reads) and the case ground truth; the adapter-provided views must
    # agree exactly, in case order.
    case = _case(
        defects=[
            _defect("d1", finding_type="stale_source", locator="source-002.md#L14"),
            _defect(
                "d2",
                finding_type="number_without_source",
                locator="source-003.md#L3",
            ),
        ],
        clean_claims=["source-001.md#L8", "source-001.md#L9"],
    )
    findings = [
        _raw_finding(),  # d1
        _raw_finding(
            finding_type="number_without_source", locator="source-001.md#L8"
        ),  # false flag on a clean claim
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
# Loud rejection of unknown vocabulary
# ---------------------------------------------------------------------------


def test_unknown_finding_type_is_rejected_loudly():
    # market_quote_metadata_incomplete is real gate vocabulary (freshness
    # gate) but outside the evaluation contract's FINDING_TYPES: a rollout
    # reporting it must fail loudly, never silently read as a miss.
    raw = _raw_finding(finding_type="market_quote_metadata_incomplete")
    with pytest.raises(ValueError, match="unknown finding_type"):
        outcome_from_findings(_case(), [raw], blocked=False)


def test_unknown_blocking_level_is_rejected_loudly():
    # "none" appears in the gate contract's BLOCKING_LEVELS for validation
    # but no finding producer emits it; the evaluation contract accepts
    # only blocking/warning.
    raw = _raw_finding(blocking_level="none")
    raw.pop("blocking")
    with pytest.raises(ValueError, match="unknown blocking_level"):
        outcome_from_findings(_case(), [raw], blocked=False)


def test_missing_finding_type_is_rejected_loudly():
    raw = _raw_finding()
    del raw["finding_type"]
    with pytest.raises(ValueError, match="missing required non-blank 'finding_type'"):
        outcome_from_findings(_case(), [raw], blocked=False)


# ---------------------------------------------------------------------------
# Locator derivation from real gate-report fields
# ---------------------------------------------------------------------------


def test_locator_derivation_prefers_source_then_claim_then_artifact():
    # Freshness-style record: source_id + line_number.
    case = _case(
        defects=[_defect("d1", finding_type="stale_source", locator="SRC-003#L12")]
    )
    raw = {
        "finding_id": "QG_FRESHNESS_001",
        "gate_id": "freshness",
        "finding_type": "stale_source",
        "blocking_level": "blocking",
        "blocking": True,
        "source_id": "SRC-003",
        "claim_id": "C-007",
        "line_number": 12,
        "artifact_id": "claim_ledger",
    }
    outcome = outcome_from_findings(case, [raw], blocked=True)
    assert outcome.found_defect_ids == ["d1"]
    assert outcome.findings[0].locator == "SRC-003#L12"

    # Audit-style record: claim_id + line_number (source_id absent).
    audit_raw = {
        "finding_type": "number_without_source",
        "blocking_level": "blocking",
        "claim_id": "C-007",
        "line_number": 14,
        "artifact_id": "audited_brief",
    }
    case2 = _case(
        defects=[
            _defect(
                "d1",
                finding_type="number_without_source",
                locator="C-007#L14",
            )
        ]
    )
    outcome2 = outcome_from_findings(case2, [audit_raw], blocked=True)
    assert outcome2.found_defect_ids == ["d1"]

    # Explicit locator wins over every derived anchor.
    explicit_raw = dict(audit_raw, locator="draft.md#L42")
    case3 = _case(
        defects=[
            _defect(
                "d1",
                finding_type="number_without_source",
                locator="draft.md#L42",
            )
        ]
    )
    outcome3 = outcome_from_findings(case3, [explicit_raw], blocked=True)
    assert outcome3.found_defect_ids == ["d1"]


# ---------------------------------------------------------------------------
# Payload-level parser guards
# ---------------------------------------------------------------------------


def test_parse_rejects_non_object_payload():
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_reported_findings(["not", "a", "report"])


def test_parse_rejects_wrong_schema_version():
    with pytest.raises(ValueError, match="schema_version"):
        parse_reported_findings({"schema_version": "someone/elses/v9", "findings": []})


def test_parse_rejects_missing_findings_list():
    with pytest.raises(ValueError, match="'findings' list"):
        parse_reported_findings(
            {"schema_version": "multi-agent-brief-quality-gates/v1"}
        )


def test_parse_rejects_non_object_finding():
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "findings": ["not-an-object"],
    }
    with pytest.raises(ValueError, match=r"findings\[0\] must be an object"):
        parse_reported_findings(payload)


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
