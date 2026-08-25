"""Unit tests for the pre-submit brief content lint."""

from __future__ import annotations

from multi_agent_brief.contracts.v2 import RunDirection
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.quality_gates.lint import brief_content_lint


def _direction(**overrides) -> RunDirection:
    payload = {
        **RunDirection.full_example,
        "subject_name": "ExampleCo",
        "target_terms": ["ExampleCo"],
        "report_date": "2026-08-25",
        "report_window_start": "2026-08-03",
        "report_window_end": "2026-08-25",
    }
    payload.update(overrides)
    return RunDirection.model_validate(payload, strict=True)


def _ledger() -> ClaimLedger:
    return ClaimLedger(
        [
            Claim.from_dict(
                {
                    "claim_id": "CL-0001",
                    "statement": "Revenue grew.",
                    "evidence_text": "Revenue grew 87%.",
                    "source_id": "SRC-1",
                    "claim_type": "fact",
                    "confidence": "medium",
                    "requires_audit": False,
                    "created_by": "claim-ledger",
                    "used_in_sections": [],
                }
            )
        ]
    )


_BRIEF = (
    "# ExampleCo Brief\n\n## Executive Summary\n\nExampleCo grew [src:CL-0001].\n"
)


class _EmptySnapshot:
    market_data_snapshots = ()


def test_clean_brief_lints_empty() -> None:
    assert (
        brief_content_lint(
            _BRIEF,
            ledger=_ledger(),
            direction=_direction(),
            workspace=None,
            snapshot=_EmptySnapshot(),
        )
        == []
    )


def test_bare_number_and_missing_summary_surface_as_violations() -> None:
    brief = "# ExampleCo Brief\n\nRevenue grew 87%.\n"
    findings = brief_content_lint(
        brief,
        ledger=_ledger(),
        direction=_direction(),
        workspace=None,
        snapshot=_EmptySnapshot(),
    )
    types = {item["finding_type"] for item in findings}
    assert "number_without_source" in types
    assert "target_relevance" in types


def test_process_wording_surfaces_as_reader_residue() -> None:
    brief = _BRIEF.replace(
        "ExampleCo grew", "ExampleCo grew per the Claim Ledger"
    )
    findings = brief_content_lint(
        brief,
        ledger=_ledger(),
        direction=_direction(),
        workspace=None,
        snapshot=_EmptySnapshot(),
    )
    assert any(item["finding_type"] == "reader_residue" for item in findings)


def test_required_sections_lint_from_frozen_intents() -> None:
    direction = _direction(
        required_section_intents=["market_reaction_divergence"]
    )
    findings = brief_content_lint(
        _BRIEF,
        ledger=_ledger(),
        direction=direction,
        workspace=None,
        snapshot=_EmptySnapshot(),
    )
    assert any(
        item["finding_type"] == "required_section_missing"
        for item in findings
    )


def test_background_framing_surfaces_at_lint_time() -> None:
    ledger = ClaimLedger(
        [
            Claim.from_dict(
                {
                    "claim_id": "CL-BG-0001",
                    "statement": "Old context statement.",
                    "evidence_text": "Old context evidence.",
                    "source_id": "SRC-BG",
                    "claim_type": "fact",
                    "confidence": "medium",
                    "requires_audit": False,
                    "created_by": "claim-ledger",
                    "used_in_sections": [],
                    "metadata": {"temporal_role": "background"},
                }
            )
        ]
    )
    brief = (
        "# ExampleCo Brief\n\n## Executive Summary\n\n"
        "本周进展如下：ExampleCo 发布公告 [src:CL-BG-0001]。\n"
    )
    findings = brief_content_lint(
        brief,
        ledger=ledger,
        direction=_direction(),
        workspace=None,
        snapshot=_EmptySnapshot(),
    )
    assert any(
        item["finding_type"] == "background_source_current_framing"
        for item in findings
    )
