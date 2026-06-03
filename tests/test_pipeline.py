from pathlib import Path

from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext


def test_pipeline_writes_outputs(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "news.md").write_text(
        "- A competitor announced a 2 GW manufacturing expansion plan.\n",
        encoding="utf-8",
    )

    context = PipelineContext(
        project_name="Demo Brief",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        report_date="2026-06-02",
        max_source_age_days=14,
        fail_on_stale_source=True,
    )

    outputs = BriefPipeline().run(context)

    assert len(outputs) == 7  # source-collection + 6 agents
    assert (output_dir / "brief.md").exists()
    assert (output_dir / "claim_ledger.json").exists()
    assert (output_dir / "audit_report.json").exists()
    assert (output_dir / "source_map.md").exists()
    assert "Demo Brief" in (output_dir / "brief.md").read_text(encoding="utf-8")


# --- P0: Analyst renders all 10 topics ---

def test_analyst_renders_all_topics(tmp_path):
    """Analyst should render sections for compliance, technology, capital, etc."""
    from multi_agent_brief.agents.analyst import AnalystAgent
    from multi_agent_brief.core.claim_ledger import ClaimLedger
    from multi_agent_brief.core.schemas import Claim, PipelineContext

    ledger = ClaimLedger()
    topics_and_keywords = [
        ("compliance", "UFLPA compliance review triggered by CBP at port of entry."),
        ("technology", "TopCon cell efficiency reached 26.5 percent in pilot production line."),
        ("capital", "Major acquisition deal worth 2 billion for solar project portfolio."),
        ("demand", "PPA interconnection queue grew 40 percent in the Southwest region."),
        ("rates", "Treasury yield spread tightened as Fed signals rate pause in Q3."),
    ]
    for topic, statement in topics_and_keywords:
        claim = Claim(
            claim_id=f"TEST_{topic.upper()}",
            statement=statement,
            source_id="TEST_SRC",
            evidence_text=statement,
            claim_type="fact",
            metadata={"topic": topic},
        )
        ledger.add_claim(claim)

    context = PipelineContext(
        project_name="Topic Coverage Test",
        input_dir=str(tmp_path),
        output_dir=str(tmp_path / "output"),
    )

    agent = AnalystAgent()
    agent.run(context, ledger)

    draft = context.report_state.draft_markdown
    assert "Compliance" in draft
    assert "Technology" in draft
    assert "Capital" in draft
    assert "Demand" in draft
    assert "Rates" in draft


def test_analyst_handles_unknown_topics(tmp_path):
    """Analyst should not silently drop claims with unknown topics."""
    from multi_agent_brief.agents.analyst import AnalystAgent
    from multi_agent_brief.core.claim_ledger import ClaimLedger
    from multi_agent_brief.core.schemas import Claim, PipelineContext

    ledger = ClaimLedger()
    ledger.add_claim(Claim(
        claim_id="TEST_UNKNOWN",
        statement="Something with an unusual topic classification.",
        source_id="SRC",
        evidence_text="Something with an unusual topic classification.",
        claim_type="fact",
        metadata={"topic": "geopolitics"},
    ))

    context = PipelineContext(
        project_name="Unknown Topic Test",
        input_dir=str(tmp_path),
        output_dir=str(tmp_path / "output"),
    )

    agent = AnalystAgent()
    agent.run(context, ledger)

    draft = context.report_state.draft_markdown
    assert "geopolitics" in draft.lower() or "Geopolitics" in draft


def test_analyst_renders_all_claims_no_truncation(tmp_path):
    """Analyst must not silently truncate claims — all selected claims appear in brief."""
    from multi_agent_brief.agents.analyst import AnalystAgent
    from multi_agent_brief.core.claim_ledger import ClaimLedger
    from multi_agent_brief.core.schemas import Claim, PipelineContext

    ledger = ClaimLedger()
    for i in range(8):
        claim = Claim(
            claim_id=f"TEST_TECH_{i:02d}",
            statement=f"Technology update number {i}: solar cell efficiency milestone reached.",
            source_id="TEST_SRC",
            evidence_text=f"Technology update number {i}: solar cell efficiency milestone reached.",
            claim_type="fact",
            metadata={"topic": "technology"},
        )
        ledger.add_claim(claim)

    context = PipelineContext(
        project_name="Truncation Test",
        input_dir=str(tmp_path),
        output_dir=str(tmp_path / "output"),
    )

    agent = AnalystAgent()
    agent.run(context, ledger)

    draft = context.report_state.draft_markdown
    for i in range(8):
        assert f"TEST_TECH_{i:02d}" in draft, (
            f"Claim TEST_TECH_{i:02d} missing from draft — Analyst may be truncating"
        )
