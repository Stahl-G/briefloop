"""Fresh-workspace smoke: QoQ contrast and the auto catalyst calendar.

Freezes scoped metric drafts through the real claim freeze, then proves
the reader render injects the deterministic catalyst calendar and that
the pairing lint/gate sees the sign-correct sequential contrast.  No
Tavily, no slow suites.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests import test_core_run_v2 as core_fixture

from multi_agent_brief.contracts.v2 import ClaimFreezeRequest, RunDirection
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.claims import ClaimFreezeService
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.runtime_host_v2.reader_render import render_reader_markdown


def _metric_drafts(run_id: str) -> dict[str, object]:
    return {
        "schema_version": "briefloop.claim_drafts_proposal.v2",
        "proposal_id": "PROP-CLAIM-DRAFTS-SMOKE",
        "run_id": run_id,
        "screened_candidates_proposal_id": "PROP-SCREENED-001",
        "created_at": core_fixture.NOW,
        "drafts": [
            {
                "draft_id": "DRAFT-H1",
                "statement": "DEMO H1 revenue was $261 million, up 87.6%.",
                "evidence_text": "Revenues were approximately $261.0 million.",
                "source_ids": ["SRC-001"],
                "claim_type": "fact",
                "metric": {
                    "subject_id": "DEMO",
                    "metric_id": "revenue",
                    "value": 261.0,
                    "unit": "usd_millions",
                    "period": "2026H1",
                    "comparison_period": "2025H1",
                    "comparison_value": 139.1,
                },
            },
            {
                "draft_id": "DRAFT-Q2",
                "statement": "DEMO Q2 revenue was $118.2 million.",
                "evidence_text": "Q2 revenues were approximately $118.2 million.",
                "source_ids": ["SRC-001"],
                "claim_type": "fact",
                "metric": {
                    "subject_id": "DEMO",
                    "metric_id": "revenue",
                    "value": 118.2,
                    "unit": "usd_millions",
                    "period": "2026Q2",
                },
            },
            {
                "draft_id": "DRAFT-232",
                "statement": "Price floors take effect December 4, 2026.",
                "evidence_text": "Effective December 4, 2026.",
                "source_ids": ["SRC-001"],
                "claim_type": "fact",
                "upcoming": {
                    "date": "2026-12-04",
                    "label": "Section 232 minimum import prices take effect",
                },
            },
            {
                "draft_id": "DRAFT-PAST",
                "statement": "An event already happened before the report date.",
                "evidence_text": "Happened on 2026-08-01.",
                "source_ids": ["SRC-001"],
                "claim_type": "fact",
                "upcoming": {"date": "2026-08-01", "label": "Past event"},
            },
        ],
    }


def _freeze_with_metrics(workspace: Path) -> dict:
    service = core_fixture._advance_to_claim_ledger_ready(workspace)
    invocation_id = core_fixture._start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-CLAIMS-SMOKE",
        stage_id="claim-ledger",
        role_id="claim-ledger",
    )
    payload = _metric_drafts(core_fixture.RUN_ID)
    core_fixture._submit_proposal(
        workspace,
        lane="claim-drafts",
        invocation_id=invocation_id,
        request_id="REQ-CLAIM-DRAFTS-SMOKE",
        artifact_id="claim_drafts",
        payload=payload,
    )
    frozen = ClaimFreezeService(workspace, clock=core_fixture.CLOCK).freeze(
        core_fixture._record(
            ClaimFreezeRequest,
            request_id="REQ-FREEZE-SMOKE",
            run_id=core_fixture.RUN_ID,
            claim_drafts_proposal_id=payload["proposal_id"],
            expected_claim_drafts_artifact={
                "artifact_id": "claim_drafts",
                "revision": 1,
            },
            expected_store_revision=core_fixture._store_revision(workspace),
            expected_ledger_revision=0,
        )
    )
    assert frozen.status == "committed", frozen.to_dict()
    with SQLiteControlStore.open(
        workspace / "briefloop.db", clock=core_fixture.CLOCK
    ) as store:
        snapshot = store.load_snapshot(core_fixture.RUN_ID)
        record = next(
            item
            for item in snapshot.artifacts
            if item.artifact_id == "claim_ledger"
        )
        ledger_bytes = store.read_artifact_revision_bytes(
            core_fixture.RUN_ID,
            "claim_ledger",
            record.current_revision,
        )
    return json.loads(ledger_bytes)


def test_solar_smoke_qoq_contrast_and_auto_calendar(tmp_path: Path) -> None:
    workspace = core_fixture._workspace(tmp_path)
    ledger_payload = _freeze_with_metrics(workspace)

    derived = ledger_payload["derived_metrics"]
    assert len(derived) == 1
    assert derived[0]["subject_id"] == "DEMO"
    assert derived[0]["prior_quarter_value"] == 142.8
    assert derived[0]["sign_conflict"] is True

    direction = RunDirection.model_validate(
        {
            **RunDirection.full_example,
            "output_language": "zh-CN",
            "report_date": "2026-08-25",
            "report_window_start": "2026-08-03",
            "report_window_end": "2026-08-25",
            "required_section_intents": ["investment_view_calendar"],
        },
        strict=True,
    )
    ledger = ClaimLedger(
        [Claim.from_dict(item) for item in ledger_payload["claims"]]
    )
    audited = (
        "# DEMO 股市月报\n\n## 摘要\n\n"
        "上半年收入同比增长 87.6% [src:CL-0001]，二季度环比回落 17.2% "
        "[src:CL-0002]。\n\n## 催化剂日历\n\n（本节由系统生成）\n"
    )

    from multi_agent_brief.quality_gates.lint import brief_content_lint

    class _Snap:
        market_data_snapshots = ()

    lint = brief_content_lint(
        audited,
        ledger=ledger,
        direction=direction,
        workspace=None,
        snapshot=_Snap(),
        ledger_payload=ledger_payload,
    )
    assert not [
        item for item in lint if item["finding_type"] == "sequential_metric_unpaired"
    ]

    reader = render_reader_markdown(
        audited_markdown=audited,
        ledger=ledger,
        run_direction=direction,
        run_id=core_fixture.RUN_ID,
        store_revision=9,
        upcoming_events=ledger_payload["upcoming_events"],
    )
    # Auto calendar: only the post-report-date event renders; the past
    # event is filtered; the placeholder line is replaced by the table.
    assert "| 2026-12-04 | Section 232 minimum import prices take effect |" in reader
    assert "Past event" not in reader
    assert "本节由系统生成" not in reader
    # Empty state when no future events qualify.
    empty_reader = render_reader_markdown(
        audited_markdown=audited,
        ledger=ledger,
        run_direction=direction,
        run_id=core_fixture.RUN_ID,
        store_revision=9,
        upcoming_events=[],
    )
    assert "未识别到有来源支持的明确日期催化剂" in empty_reader
