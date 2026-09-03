"""Minimal acceptance for the QoQ-contrast slice (table-driven).

Proves exactly four things: cross-company inputs never pair, non-
cumulative metrics cannot even be expressed, the correct DEMO pair
derives, and a wrong-sign (or missing) QoQ quote cannot pass the
pairing gate.
"""

from __future__ import annotations

import pytest

from multi_agent_brief.contracts.v2 import (
    ClaimDraftItem,
    ClaimMetricValue,
    ClaimUpcomingEvent,
)
from multi_agent_brief.core_run_v2.derived_metrics import (
    collect_upcoming_events,
    derive_sequential_metrics,
)
from multi_agent_brief.quality_gates.metric_pairing import (
    metric_pairing_findings,
    render_catalyst_calendar,
)


class _Claim:
    def __init__(self, claim_id: str, metric=None, upcoming=None) -> None:
        self.claim_id = claim_id
        self.metric = metric
        self.upcoming = upcoming


def _metric(**overrides):
    payload = {
        "subject_id": "DEMO",
        "metric_id": "revenue",
        "value": 261.0,
        "unit": "usd_millions",
        "period": "2026H1",
        "comparison_period": "2025H1",
        "comparison_value": 139.1,
    }
    payload.update(overrides)
    return ClaimMetricValue.model_validate(payload, strict=True)


def test_non_cumulative_metric_is_rejected_at_schema() -> None:
    with pytest.raises(Exception):
        ClaimMetricValue.model_validate(
            {
                "subject_id": "DEMO",
                "metric_id": "cash",
                "value": 123.4,
                "unit": "usd_millions",
                "period": "2026H1",
            },
            strict=True,
        )
    with pytest.raises(Exception):
        ClaimMetricValue.model_validate(
            {
                "subject_id": "DEMO",
                "metric_id": "revenue",
                "value": 32.5,
                "unit": "pct",
                "period": "2026H1",
            },
            strict=True,
        )


@pytest.mark.parametrize(
    ("case", "claims"),
    (
        (
            "cross_company_never_pairs",
            [
                _Claim("CL-DEMO-H1", _metric()),
                _Claim(
                    "CL-FSLR-Q2",
                    _metric(subject_id="FSLR", value=118.2, period="2026Q2"),
                ),
            ],
        ),
        (
            "duplicate_half_is_diagnostic_not_derived",
            [
                _Claim("CL-H1-A", _metric()),
                _Claim("CL-H1-B", _metric(value=259.0)),
                _Claim("CL-Q2", _metric(value=118.2, period="2026Q2")),
            ],
        ),
        (
            "subject_qoq_derives",
            [
                _Claim("CL-H1", _metric()),
                _Claim(
                    "CL-Q2",
                    _metric(value=118.2, period="2026Q2"),
                ),
            ],
        ),
    ),
)
def test_derivation_table(case: str, claims: list) -> None:
    derived = derive_sequential_metrics(claims)
    pairs = [item for item in derived if item.get("kind", "pair") == "pair"]
    diagnostics = [
        item for item in derived if item.get("kind") == "diagnostic"
    ]
    if case == "cross_company_never_pairs":
        assert pairs == []
        assert diagnostics == []
    elif case == "duplicate_half_is_diagnostic_not_derived":
        assert pairs == []
        assert len(diagnostics) == 1
        assert diagnostics[0]["reason"] == "ambiguous_group"
        assert diagnostics[0]["subject_id"] == "DEMO"
    else:
        assert len(pairs) == 1
        item = pairs[0]
        assert item["subject_id"] == "DEMO"
        assert item["prior_quarter_value"] == pytest.approx(142.8)
        assert item["qoq_pct"] == pytest.approx(-17.2, abs=0.2)
        assert item["yoy_pct"] == pytest.approx(87.6, abs=0.2)
        assert item["sign_conflict"] is True


_BRIEF_YOY_ONLY = (
    "# Brief\n\n## 摘要\n\n"
    "DEMO 上半年收入同比增长 87.6% [src:CL-H1]。\n"
)
_DERIVED_DEMO = derive_sequential_metrics(
    [
        _Claim("CL-H1", _metric()),
        _Claim("CL-Q2", _metric(value=118.2, period="2026Q2")),
    ]
)


@pytest.mark.parametrize(
    ("case", "brief"),
    (
        ("missing_qoq_blocks", _BRIEF_YOY_ONLY),
        (
            "wrong_sign_qoq_blocks",
            _BRIEF_YOY_ONLY.replace(
                "同比增长 87.6% [src:CL-H1]。",
                "同比增长 87.6% [src:CL-H1]，环比回升 17.2% [src:CL-Q2]。",
            ),
        ),
        (
            "correct_sign_qoq_passes",
            _BRIEF_YOY_ONLY.replace(
                "同比增长 87.6% [src:CL-H1]。",
                "同比增长 87.6% [src:CL-H1]，环比回落 17.2% [src:CL-Q2]。",
            ),
        ),
        (
            "signed_token_qoq_passes",
            _BRIEF_YOY_ONLY.replace(
                "同比增长 87.6% [src:CL-H1]。",
                "同比增长 87.6% [src:CL-H1]，环比 -17.2% [src:CL-Q2]。",
            ),
        ),
        (
            "mention_without_number_is_not_a_headline",
            _BRIEF_YOY_ONLY.replace(
                "同比增长 87.6% [src:CL-H1]。",
                "基本面拐点已被财报确认（收入 [src:CL-H1][src:CL-Q2]）。",
            ),
        ),
        (
            "no_disclosure_wording_can_escape",
            _BRIEF_YOY_ONLY.replace(
                "同比增长 87.6% [src:CL-H1]。",
                "同比增长 87.6% [src:CL-H1]，环比未披露。",
            ),
        ),
    ),
)
def test_pairing_gate_table(case: str, brief: str) -> None:
    findings = metric_pairing_findings(
        brief,
        derived_metrics=_DERIVED_DEMO,
    )
    blocking = [
        f for f in findings if f["finding_type"] == "sequential_metric_unpaired"
    ]
    if case in (
        "missing_qoq_blocks",
        "wrong_sign_qoq_blocks",
        "no_disclosure_wording_can_escape",
    ):
        assert len(blocking) == 1
        assert blocking[0]["blocking_level"] == "blocking"
        assert blocking[0]["metadata"]["subject_id"] == "DEMO"
    else:
        assert blocking == []


def test_upcoming_events_dedupe_and_calendar_render() -> None:
    claims = [
        _Claim(
            "CL-A",
            upcoming=ClaimUpcomingEvent.model_validate(
                {"date": "2026-12-04", "label": "Section 232 effective"}, strict=True
            ),
        ),
        _Claim(
            "CL-B",
            upcoming=ClaimUpcomingEvent.model_validate(
                {"date": "2026-12-04", "label": "Same-day second event"}, strict=True
            ),
        ),
        _Claim(
            "CL-C",
            upcoming=ClaimUpcomingEvent.model_validate(
                {"date": "2026-11-19", "label": "Next earnings"}, strict=True
            ),
        ),
    ]
    events = collect_upcoming_events(claims)
    table = render_catalyst_calendar(events, zh=True)
    assert "| 2026-11-19 | Next earnings |" in table
    assert "| 2026-12-04 | Section 232 effective |" in table
    assert "| 2026-12-04 | Same-day second event |" in table
    empty = render_catalyst_calendar([], zh=True)
    assert empty == "未识别到有来源支持的明确日期催化剂"
    assert "CL-" not in table  # reader-safe: no internal ids


def test_draft_item_accepts_scoped_metric_and_upcoming() -> None:
    draft = ClaimDraftItem.model_validate(
        {
            "draft_id": "DRAFT-M",
            "statement": "Revenue grew.",
            "evidence_text": "Revenue grew.",
            "source_ids": ["SRC-1"],
            "claim_type": "fact",
            "metric": {
                "subject_id": "DEMO",
                "metric_id": "revenue",
                "value": 118.2,
                "unit": "usd_millions",
                "period": "2026Q2",
            },
            "upcoming": {"date": "2026-12-04", "label": "Effectivity"},
        },
        strict=True,
    )
    assert draft.metric is not None and draft.upcoming is not None
